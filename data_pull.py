"""
Sector-organized multi-company DCF valuation dashboard.

Extends the original single-basket script into six sector baskets:
    - Semiconductors
    - Energy (integrated oil & majors)
    - Natural Gas / Midstream
    - Defense & Military
    - Blue Chips (diversified mega-cap, outside the other sectors)
    - Major Pharma

For each ticker, pulls live data from yfinance and builds a 5-year DCF
using growth/margin assumptions ANCHORED TO WALL STREET CONSENSUS (not
hand-picked numbers), then renders an interactive HTML dashboard
(dashboard.html) with a two-level tab structure: Sector -> Company.

Each company panel compares:
    - Your DCF "Model Fair Value"
    - The actual Street mean analyst price target (from Yahoo Finance)
    - A "Blended Fair Value" (50/50 average of the two)
    - Current market price

Each sector panel also shows a summary table/chart across its 5 names.

Run this daily (see README.md for automation options) and it will
regenerate dashboard.html with fresh numbers.

NOTE: requires `pip install yfinance` and outbound internet access to
Yahoo Finance. Ticker basket picks below are a reasonable starting point,
not a definitive "top 5" — swap names in SECTORS to fit your own view.
"""

import json
import traceback
from datetime import datetime

import yfinance as yf

# --- Sector baskets -------------------------------------------------------
# Picked as large, liquid, well-covered names (good analyst-estimate
# coverage = better consensus data for the DCF inputs). Feel free to swap.
SECTORS = {
    "Semiconductors": {
        "tickers": ["NVDA", "TSM", "AVGO", "ASML", "AMD"],
        "note": "Leaders across GPU/AI compute (NVDA), foundry (TSM), networking/custom "
                "silicon (AVGO), lithography equipment (ASML), and CPU/GPU (AMD). Could also "
                "consider: MU (memory), QCOM (mobile/edge), ARM (IP licensing), MRVL (data "
                "center interconnect).",
    },
    "Energy": {
        "tickers": ["XOM", "CVX", "SHEL", "BP", "COP"],
        "note": "Integrated majors and large independents. Could also consider: TTE "
                "(TotalEnergies), EOG (Permian-focused E&P), PBR (Petrobras, higher "
                "geopolitical/FX risk).",
    },
    "Natural Gas": {
        "tickers": ["EQT", "WMB", "KMI", "LNG", "OKE"],
        "note": "Upstream producer (EQT), midstream pipelines (WMB, KMI, OKE), and LNG "
                "export (LNG/Cheniere). Could also consider: TRGP (Targa Resources), "
                "AR (Antero Resources).",
    },
    "Defense & Military": {
        "tickers": ["LMT", "RTX", "NOC", "GD", "LHX"],
        "note": "Prime contractors across aircraft, missiles, munitions, and electronics. "
                "Could also consider: BA (Boeing, mixed with commercial aero risk), "
                "HII (Huntington Ingalls, shipbuilding), TDG (TransDigm, aftermarket parts).",
    },
    "Blue Chips": {
        "tickers": ["AAPL", "MSFT", "JPM", "PG", "KO"],
        "note": "Diversified mega-caps outside the other five sectors: consumer tech "
                "(AAPL), enterprise software/cloud (MSFT), banking (JPM), consumer "
                "staples (PG, KO). Could also consider: BRK-B (Berkshire), WMT, V/MA "
                "(payments), UNH (health insurance).",
    },
    "Major Pharma": {
        "tickers": ["JNJ", "PFE", "MRK", "ABBV", "LLY"],
        "note": "Large-cap diversified and specialty pharma, including current GLP-1 "
                "leadership (LLY). Could also consider: NVO (Novo Nordisk, ADR — GLP-1 "
                "peer), BMY (Bristol Myers Squibb), AZN (AstraZeneca, ADR).",
    },
}

# --- Global assumptions (kept intentionally conservative / adjustable) ---
RISK_FREE_RATE = 0.043       # ~10yr Treasury, update as needed
EQUITY_RISK_PREMIUM = 0.05
MIN_DISCOUNT_RATE = 0.07
MAX_DISCOUNT_RATE = 0.13
TERMINAL_GROWTH_CAP = 0.04   # never let terminal growth exceed long-run GDP-ish rate
PROJECTION_YEARS = 5
DEFAULT_BETA = 1.2           # fallback if beta missing
DEFAULT_GROWTH = 0.10        # fallback if consensus growth data missing
DEFAULT_FCF_MARGIN = 0.15    # fallback if historical FCF margin can't be computed


def safe_get(d, key, default=None):
    try:
        val = d.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def get_consensus_growth_path(ticker_obj, years=PROJECTION_YEARS):
    """
    Build a growth-rate path anchored to consensus estimates:
      Year 1 -> current fiscal year consensus revenue growth (revenue_estimate '0y')
      Year 2 -> next fiscal year consensus revenue growth (revenue_estimate '+1y')
      Years 3-5 -> linear taper from Year 2 rate down to the 5yr consensus
                   long-term growth estimate (growth_estimates '+5y')
    Falls back to DEFAULT_GROWTH-based values if data is missing, and clips
    everything to a sane band so a noisy consensus figure can't blow up the model.
    """
    g_cy = g_ny = g_5y = None

    try:
        rev_est = ticker_obj.revenue_estimate
        g_cy = float(rev_est.loc["0y", "growth"])
        g_ny = float(rev_est.loc["+1y", "growth"])
    except Exception:
        pass

    try:
        growth_est = ticker_obj.growth_estimates
        g_5y_raw = float(growth_est.loc["+5y", "stock"])
        # Yahoo's 5yr estimate is often EPS growth and can run hot; clip it.
        g_5y = max(0.03, min(g_5y_raw, 0.20))
    except Exception:
        pass

    if g_cy is None or g_cy != g_cy:  # NaN check
        g_cy = DEFAULT_GROWTH
    if g_ny is None or g_ny != g_ny:
        g_ny = g_cy
    if g_5y is None or g_5y != g_5y:
        g_5y = min(g_ny, 0.10)

    # Clip individual years to avoid absurd outliers from thin analyst coverage
    g_cy = max(-0.10, min(g_cy, 0.60))
    g_ny = max(-0.10, min(g_ny, 0.60))

    path = [g_cy, g_ny]
    # taper linearly from g_ny to g_5y over the remaining years
    remaining = years - 2
    if remaining > 0:
        step = (g_5y - g_ny) / remaining
        for i in range(1, remaining + 1):
            path.append(g_ny + step * i)

    return path[:years], {"consensus_cy_growth": g_cy, "consensus_ny_growth": g_ny,
                           "consensus_5y_growth": g_5y}


def get_historical_fcf_margin(income_statement, cash_flow):
    """
    Returns (margin, quality_info).

    Instead of a flat average of the last 3 years (which lets a single stale
    downturn year drag the whole forecast negative for cyclical names), this
    weights the most recent year more heavily: weights [3, 2, 1] for the
    3 most recent years, most-recent first (yfinance financials are ordered
    most-recent-first by default).

    quality_info flags:
      - "negative_years": count of years with negative FCF margin
      - "weighted_margin_negative": True if the final weighted margin is still negative
      - "years_used": how many years of data were actually available
    """
    default_quality = {"negative_years": 0, "weighted_margin_negative": False, "years_used": 0}
    try:
        revenue = income_statement.loc["Total Revenue"].dropna()
        fcf = cash_flow.loc["Free Cash Flow"].dropna()
        margins = (fcf / revenue).dropna()
        margins = margins[(margins > -1) & (margins < 1)]  # sanity filter
        if len(margins) == 0:
            return DEFAULT_FCF_MARGIN, default_quality

        margins = margins.iloc[:3]  # most recent 3 years, most-recent first
        weights = [3, 2, 1][:len(margins)]
        weighted_margin = sum(m * w for m, w in zip(margins, weights)) / sum(weights)

        negative_years = int((margins < 0).sum())
        quality_info = {
            "negative_years": negative_years,
            "weighted_margin_negative": weighted_margin < 0,
            "years_used": len(margins),
        }
        return float(weighted_margin), quality_info
    except Exception:
        return DEFAULT_FCF_MARGIN, default_quality


def get_discount_rate(info):
    beta = safe_get(info, "beta", DEFAULT_BETA)
    try:
        beta = float(beta)
    except (TypeError, ValueError):
        beta = DEFAULT_BETA
    rate = RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM
    return max(MIN_DISCOUNT_RATE, min(rate, MAX_DISCOUNT_RATE)), beta


def get_fx_rate(from_currency, to_currency):
    """
    Fetch a spot FX rate to convert `from_currency` amounts into `to_currency`.
    Returns None if unavailable (caller should fall back to no conversion,
    with a data_warning flagging the figures may be off).
    """
    if not from_currency or not to_currency or from_currency == to_currency:
        return 1.0
    try:
        pair = yf.Ticker(f"{from_currency}{to_currency}=X")
        rate = safe_get(pair.info, "regularMarketPrice")
        if rate is None:
            hist = pair.history(period="5d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
        return float(rate) if rate else None
    except Exception:
        return None


def is_financial_sector(info):
    """
    Flags banks/diversified financials/insurance where FCF-based DCF is not
    a meaningful valuation method (operating cash flow is dominated by
    deposits, loans, and trading positions rather than true FCF generation).
    """
    sector = (safe_get(info, "sector") or "").lower()
    industry = (safe_get(info, "industry") or "").lower()
    if "financial" in sector:
        return True
    bank_keywords = ["bank", "insurance", "capital markets", "asset management", "credit services"]
    return any(k in industry for k in bank_keywords)


def value_company(symbol, sector):
    ticker = yf.Ticker(symbol)
    info = ticker.info

    # --- Banks/financials: FCF-based DCF isn't meaningful, skip it cleanly ---
    if is_financial_sector(info):
        current_price = safe_get(info, "currentPrice")
        try:
            targets = ticker.analyst_price_targets
            street_mean = float(targets.get("mean")) if targets.get("mean") else None
            street_median = float(targets.get("median")) if targets.get("median") else None
            street_low = float(targets.get("low")) if targets.get("low") else None
            street_high = float(targets.get("high")) if targets.get("high") else None
        except Exception:
            street_mean = street_median = street_low = street_high = None

        return {
            "symbol": symbol,
            "sector": sector,
            "company_name": safe_get(info, "shortName", symbol),
            "current_price": round(current_price, 2) if current_price else None,
            "shares_outstanding": safe_get(info, "sharesOutstanding"),
            "market_cap": safe_get(info, "marketCap"),
            "dcf_fair_value": None,
            "street_mean_target": round(street_mean, 2) if street_mean else None,
            "street_median_target": round(street_median, 2) if street_median else None,
            "street_low_target": round(street_low, 2) if street_low else None,
            "street_high_target": round(street_high, 2) if street_high else None,
            "blended_fair_value": round(street_mean, 2) if street_mean else None,
            "verdict": "N/A — bank/financial (FCF-based DCF not applicable)",
            "data_warning": (
                "This is a bank or diversified financial company. Free cash flow is not a "
                "meaningful valuation metric for depository/financial institutions, since "
                "operating cash flow is dominated by deposits, loans, and trading positions "
                "rather than true cash generation. DCF fair value is intentionally omitted; "
                "only the Street consensus target is shown. Consider a price-to-book or "
                "dividend discount approach for this name instead."
            ),
            "assumptions": None,
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    income_statement = ticker.financials
    cash_flow = ticker.cashflow
    balance_sheet = ticker.balance_sheet

    current_price = safe_get(info, "currentPrice")
    shares_outstanding = safe_get(info, "sharesOutstanding")
    market_cap = safe_get(info, "marketCap")

    if not current_price or not shares_outstanding:
        raise ValueError(f"Missing core price/share data for {symbol}")

    # --- Currency mismatch check (common for foreign ADRs like TSM, ASML) ---
    # Some foreign issuers report financial statements in local currency
    # (financialCurrency) while trading/quoting in USD (currency). If these
    # differ, convert revenue/cash/debt figures to the quote currency before
    # running the DCF, or the per-share result will be wildly off.
    price_currency = safe_get(info, "currency", "USD")
    financial_currency = safe_get(info, "financialCurrency", price_currency)
    fx_rate = 1.0
    currency_warning = None
    if financial_currency and price_currency and financial_currency != price_currency:
        fx_rate = get_fx_rate(financial_currency, price_currency)
        if fx_rate is None:
            fx_rate = 1.0
            currency_warning = (
                f"Financials are reported in {financial_currency} but the stock trades in "
                f"{price_currency}, and an FX rate could not be retrieved to convert them. "
                "Treat this DCF fair value with caution — it may be significantly off."
            )
        else:
            currency_warning = (
                f"Financials were reported in {financial_currency} and converted to "
                f"{price_currency} at ~{fx_rate:.4f} for this model."
            )

    latest_revenue = float(income_statement.loc["Total Revenue"].iloc[0]) * fx_rate

    growth_path, growth_meta = get_consensus_growth_path(ticker)
    fcf_margin, fcf_quality = get_historical_fcf_margin(income_statement, cash_flow)
    discount_rate, beta = get_discount_rate(info)
    terminal_growth_rate = min(growth_meta["consensus_5y_growth"], TERMINAL_GROWTH_CAP)

    # --- Project revenue & FCF ---
    projected_revenue, projected_fcf = [], []
    rev = latest_revenue
    for g in growth_path:
        rev = rev * (1 + g)
        projected_revenue.append(rev)
        projected_fcf.append(rev * fcf_margin)

    # --- Discount cash flows ---
    discounted_fcf = [
        fcf_year / ((1 + discount_rate) ** year)
        for year, fcf_year in enumerate(projected_fcf, 1)
    ]

    # --- Terminal value ---
    final_year_fcf = projected_fcf[-1]
    terminal_value = (final_year_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** PROJECTION_YEARS)

    # --- Enterprise / equity value ---
    enterprise_value = sum(discounted_fcf) + discounted_terminal_value
    try:
        total_debt = float(balance_sheet.loc["Total Debt"].iloc[0]) * fx_rate
    except Exception:
        total_debt = 0.0
    try:
        cash = float(balance_sheet.loc["Cash And Cash Equivalents"].iloc[0]) * fx_rate
    except Exception:
        cash = 0.0
    net_debt = total_debt - cash
    equity_value = enterprise_value - net_debt
    dcf_fair_value = equity_value / shares_outstanding

    # --- Street consensus price target (mean of covering analysts) ---
    try:
        targets = ticker.analyst_price_targets
        street_mean = float(targets.get("mean")) if targets.get("mean") else None
        street_median = float(targets.get("median")) if targets.get("median") else None
        street_low = float(targets.get("low")) if targets.get("low") else None
        street_high = float(targets.get("high")) if targets.get("high") else None
    except Exception:
        street_mean = street_median = street_low = street_high = None

    # --- Blended fair value: average of your DCF model and Street consensus ---
    if street_mean:
        blended_fair_value = (dcf_fair_value + street_mean) / 2
    else:
        blended_fair_value = dcf_fair_value

    verdict = "UNDERVALUED" if blended_fair_value > current_price else "OVERVALUED"

    # Flag results that likely reflect a cyclical downturn distorting the model
    # rather than a genuine fundamental problem, so they aren't trusted at face value.
    data_warning = None
    if fcf_quality["weighted_margin_negative"]:
        data_warning = (
            f"Historical FCF margin is negative (based on {fcf_quality['years_used']} "
            f"recent year(s), {fcf_quality['negative_years']} of which had negative FCF). "
            "This DCF is likely distorted by a cyclical downturn and should not be trusted at face value."
        )
    elif fcf_quality["negative_years"] > 0:
        data_warning = (
            f"{fcf_quality['negative_years']} of the last {fcf_quality['years_used']} years had "
            "negative FCF margin; recent years were weighted more heavily to reduce distortion, "
            "but treat this DCF with extra caution."
        )

    if currency_warning:
        data_warning = (currency_warning if not data_warning
                         else f"{currency_warning} Also: {data_warning}")

    return {
        "symbol": symbol,
        "sector": sector,
        "company_name": safe_get(info, "shortName", symbol),
        "current_price": round(current_price, 2),
        "shares_outstanding": shares_outstanding,
        "market_cap": market_cap,
        "dcf_fair_value": round(dcf_fair_value, 2),
        "street_mean_target": round(street_mean, 2) if street_mean else None,
        "street_median_target": round(street_median, 2) if street_median else None,
        "street_low_target": round(street_low, 2) if street_low else None,
        "street_high_target": round(street_high, 2) if street_high else None,
        "blended_fair_value": round(blended_fair_value, 2),
        "verdict": verdict,
        "data_warning": data_warning,
        "assumptions": {
            "discount_rate": round(discount_rate, 4),
            "beta": round(beta, 2),
            "terminal_growth_rate": round(terminal_growth_rate, 4),
            "fcf_margin": round(fcf_margin, 4),
            "growth_path": [round(g, 4) for g in growth_path],
            "consensus_5y_growth_estimate": round(growth_meta["consensus_5y_growth"], 4),
        },
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def main():
    results = []
    for sector, cfg in SECTORS.items():
        for symbol in cfg["tickers"]:
            print(f"Pulling and valuing {symbol} ({sector}) ...")
            try:
                result = value_company(symbol, sector)
                results.append(result)
                dcf_display = f"${result['dcf_fair_value']}" if result['dcf_fair_value'] is not None else "N/A (bank)"
                print(f"  {symbol}: DCF {dcf_display} | "
                      f"Street ${result['street_mean_target']} | "
                      f"Blended ${result['blended_fair_value']} | "
                      f"Price ${result['current_price']} -> {result['verdict']}")
            except Exception as e:
                print(f"  FAILED to value {symbol}: {e}")
                traceback.print_exc()

    with open("valuation_results.json", "w") as f:
        json.dump({"generated_at": datetime.now().isoformat(), "results": results}, f, indent=2)

    build_dashboard(results)
    print("\nDashboard written to dashboard.html")


def build_dashboard(results):
    data_json = json.dumps(results)
    sectors_json = json.dumps({name: cfg["note"] for name, cfg in SECTORS.items()})
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sector Valuation Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.32.0/plotly.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1117; color:#e8e8e8; margin:0; padding:24px; }}
  h1 {{ font-size: 20px; margin-bottom:4px; }}
  .subtitle {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .sector-tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
  .sector-btn {{ background:#1b1e27; border:1px solid #2c303c; color:#cfd2da; padding:9px 18px; border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; }}
  .sector-btn.active {{ background:#2E86AB; color:white; border-color:#2E86AB; }}
  .company-tabs {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:20px; }}
  .company-btn {{ background:#161922; border:1px solid #2c303c; color:#cfd2da; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; }}
  .company-btn.active {{ background:#3ddc97; color:#0f1117; border-color:#3ddc97; }}
  .sector-panel {{ display:none; }}
  .sector-panel.active {{ display:block; }}
  .company-panel {{ display:none; }}
  .company-panel.active {{ display:block; }}
  .card {{ background:#161922; border:1px solid #2c303c; border-radius:12px; padding:20px; margin-bottom:16px; }}
  .sector-note {{ font-size:13px; color:#9aa0a6; margin-bottom:16px; line-height:1.6; }}
  .stat-row {{ display:flex; gap:24px; flex-wrap:wrap; margin-top:12px; }}
  .stat {{ min-width:140px; }}
  .stat-label {{ font-size:12px; color:#9aa0a6; text-transform:uppercase; letter-spacing:0.5px;}}
  .stat-value {{ font-size:20px; font-weight:600; }}
  .verdict-under {{ color:#3ddc97; }}
  .verdict-over {{ color:#ff6b6b; }}
  .assumptions {{ font-size:13px; color:#9aa0a6; margin-top:16px; line-height:1.6; }}
  .warning-badge {{ display:inline-block; background:#3a2a12; color:#f4a261; border:1px solid #6b4a1f; border-radius:6px; padding:2px 8px; font-size:11px; margin-left:8px; }}
  .warning-card {{ background:#241a0f; border:1px solid #6b4a1f; border-radius:10px; padding:14px 16px; margin-top:12px; font-size:13px; color:#f4a261; }}
  table.summary {{ width:100%; border-collapse: collapse; font-size:13px; }}
  table.summary th, table.summary td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #2c303c; }}
  table.summary th {{ color:#9aa0a6; font-weight:500; }}
</style>
</head>
<body>
<h1>Sector Valuation Dashboard</h1>
<div class="subtitle">Generated {generated_at} &middot; DCF assumptions anchored to Yahoo Finance consensus analyst estimates &middot; Not investment advice</div>

<div class="sector-tabs" id="sector-tabs"></div>
<div id="sector-panels"></div>

<script>
const results = {data_json};
const sectorNotes = {sectors_json};

// Group results by sector, preserving basket order
const sectorOrder = Object.keys(sectorNotes);
const bySector = {{}};
sectorOrder.forEach(s => bySector[s] = []);
results.forEach(r => {{ if (bySector[r.sector]) bySector[r.sector].push(r); }});

const sectorTabsEl = document.getElementById('sector-tabs');
const sectorPanelsEl = document.getElementById('sector-panels');

function verdictClass(v) {{ return v === 'UNDERVALUED' ? 'verdict-under' : 'verdict-over'; }}

sectorOrder.forEach((sector, idx) => {{
  const items = bySector[sector];

  // Sector tab button
  const sBtn = document.createElement('div');
  sBtn.className = 'sector-btn' + (idx === 0 ? ' active' : '');
  sBtn.textContent = sector;
  sBtn.onclick = () => showSector(sector);
  sectorTabsEl.appendChild(sBtn);

  // Sector panel
  const sPanel = document.createElement('div');
  sPanel.className = 'sector-panel' + (idx === 0 ? ' active' : '');
  sPanel.id = 'sector-panel-' + sector;

  const rows = items.map(r => `
    <tr>
      <td><b>${{r.symbol}}</b> &middot; ${{r.company_name}} ${{r.data_warning ? '<span class="warning-badge">⚠ check data</span>' : ''}}</td>
      <td>$${{r.current_price}}</td>
      <td>${{r.dcf_fair_value !== null ? '$' + r.dcf_fair_value : 'n/a (bank)'}}</td>
      <td>${{r.street_mean_target ? '$' + r.street_mean_target : 'n/a'}}</td>
      <td>$${{r.blended_fair_value}}</td>
      <td class="${{verdictClass(r.verdict)}}">${{r.verdict}}</td>
    </tr>`).join('');

  sPanel.innerHTML = `
    <div class="sector-note">${{sectorNotes[sector]}}</div>
    <div class="card">
      <table class="summary">
        <tr><th>Company</th><th>Price</th><th>DCF Model</th><th>Street Mean Target</th><th>Blended Fair Value</th><th>Verdict</th></tr>
        ${{rows}}
      </table>
    </div>
    <div id="chart-${{sector.replace(/\\s+/g,'-')}}" class="card" style="height:380px;"></div>
    <div class="company-tabs" id="company-tabs-${{sector.replace(/\\s+/g,'-')}}"></div>
    <div id="company-panels-${{sector.replace(/\\s+/g,'-')}}"></div>
  `;
  sectorPanelsEl.appendChild(sPanel);

  // Sector summary chart
  Plotly.newPlot('chart-' + sector.replace(/\\s+/g,'-'), [
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.current_price), name: 'Current Price', type: 'bar', marker: {{color:'#A23B72'}} }},
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.dcf_fair_value ?? 0), name: 'DCF Fair Value', type: 'bar', marker: {{color:'#2E86AB'}} }},
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.blended_fair_value), name: 'Blended Fair Value', type: 'bar', marker: {{color:'#3ddc97'}} }},
  ], {{
    paper_bgcolor:'#161922', plot_bgcolor:'#161922', font:{{color:'#e8e8e8'}},
    barmode:'group', margin:{{t:20}}, legend:{{orientation:'h', y:-0.2}}
  }}, {{displayModeBar:false, responsive:true}});

  // Company sub-tabs within this sector
  const cTabsEl = document.getElementById('company-tabs-' + sector.replace(/\\s+/g,'-'));
  const cPanelsEl = document.getElementById('company-panels-' + sector.replace(/\\s+/g,'-'));

  items.forEach((r, cIdx) => {{
    const cBtn = document.createElement('div');
    cBtn.className = 'company-btn' + (cIdx === 0 ? ' active' : '');
    cBtn.textContent = r.symbol;
    cBtn.onclick = () => showCompany(sector, r.symbol);
    cTabsEl.appendChild(cBtn);

    const cPanel = document.createElement('div');
    cPanel.className = 'company-panel' + (cIdx === 0 ? ' active' : '');
    cPanel.id = 'company-panel-' + sector.replace(/\\s+/g,'-') + '-' + r.symbol;
    const a = r.assumptions;
    cPanel.innerHTML = `
      <div class="card">
        <div class="stat-row">
          <div class="stat"><div class="stat-label">Current Price</div><div class="stat-value">$${{r.current_price}}</div></div>
          <div class="stat"><div class="stat-label">DCF Fair Value</div><div class="stat-value">${{r.dcf_fair_value !== null ? '$' + r.dcf_fair_value : 'n/a (bank)'}}</div></div>
          <div class="stat"><div class="stat-label">Street Mean Target</div><div class="stat-value">${{r.street_mean_target ? '$'+r.street_mean_target : 'n/a'}}</div></div>
          <div class="stat"><div class="stat-label">Blended Fair Value</div><div class="stat-value">$${{r.blended_fair_value}}</div></div>
          <div class="stat"><div class="stat-label">Verdict</div><div class="stat-value ${{verdictClass(r.verdict)}}">${{r.verdict}}</div></div>
        </div>
        <div id="companychart-${{sector.replace(/\\s+/g,'-')}}-${{r.symbol}}" style="height:340px; margin-top:20px;"></div>
        ${{r.data_warning ? `<div class="warning-card">⚠ <b>Data quality note:</b> ${{r.data_warning}}</div>` : ''}}
        <div class="assumptions">
          <b>Model assumptions (consensus-anchored):</b><br>
          Discount rate: ${{(a.discount_rate*100).toFixed(1)}}% (beta ${{a.beta}}) &middot;
          Terminal growth: ${{(a.terminal_growth_rate*100).toFixed(1)}}% &middot;
          FCF margin (weighted recent years): ${{(a.fcf_margin*100).toFixed(1)}}%<br>
          5-yr revenue growth path: ${{a.growth_path.map(g => (g*100).toFixed(1)+'%').join(' \\u2192 ')}}<br>
          Street target range: ${{r.street_low_target ? '$'+r.street_low_target : 'n/a'}} \\u2013 ${{r.street_high_target ? '$'+r.street_high_target : 'n/a'}} (median $${{r.street_median_target ?? 'n/a'}})
        </div>
      </div>
    `;
    cPanelsEl.appendChild(cPanel);

    Plotly.newPlot('companychart-' + sector.replace(/\\s+/g,'-') + '-' + r.symbol, [{{
      x: ['Current Price', 'DCF Model', 'Street Mean', 'Blended Fair Value'],
      y: [r.current_price, r.dcf_fair_value ?? 0, r.street_mean_target || 0, r.blended_fair_value],
      type: 'bar',
      marker: {{color: ['#A23B72', '#2E86AB', '#f4a261', '#3ddc97']}},
      text: [r.current_price, r.dcf_fair_value ?? 0, r.street_mean_target || 0, r.blended_fair_value].map(v => '$'+v.toFixed(2)),
      textposition: 'outside',
    }}], {{
      paper_bgcolor:'#161922', plot_bgcolor:'#161922', font:{{color:'#e8e8e8'}},
      margin:{{t:20}}, title: {{text: r.symbol + ': ' + r.company_name, font:{{size:14}}}}
    }}, {{displayModeBar:false, responsive:true}});
  }});
}});

function showSector(sector) {{
  document.querySelectorAll('.sector-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sector-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('sector-panel-' + sector).classList.add('active');
  [...document.querySelectorAll('.sector-btn')].find(b => b.textContent === sector).classList.add('active');
}}

function showCompany(sector, symbol) {{
  const key = sector.replace(/\\s+/g,'-');
  document.querySelectorAll('#company-panels-' + key + ' .company-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('#company-tabs-' + key + ' .company-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('company-panel-' + key + '-' + symbol).classList.add('active');
  [...document.querySelectorAll('#company-tabs-' + key + ' .company-btn')].find(b => b.textContent === symbol).classList.add('active');
}}
</script>
</body>
</html>
"""
    with open("dashboard.html", "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
