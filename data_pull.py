"""
Sector DCF dashboard with upside % - v4 clean visuals
- Removes blue upside bar from sector chart (now only price vs fair value)
- Removes ⚠ check badges next to tickers
- Adds Banks / Financials sector: GS, BAC, WFC, MS, LPLA
- Keeps path-fixed output so dashboard.html always writes next to script
"""

import json
import traceback
from datetime import datetime
from pathlib import Path

import yfinance as yf

SCRIPT_DIR = Path(__file__).parent
OUTPUT_HTML = SCRIPT_DIR / "dashboard.html"
OUTPUT_JSON = SCRIPT_DIR / "valuation_results.json"

SECTORS = {
    "Semiconductors": {
        "tickers": ["NVDA", "TSM", "AVGO", "ASML", "AMD"],
        "note": "GPU/AI compute (NVDA), foundry (TSM), networking/custom silicon (AVGO), lithography (ASML), CPU/GPU (AMD).",
    },
    "Energy": {
        "tickers": ["XOM", "CVX", "SHEL", "BP", "COP"],
        "note": "Integrated majors and large independents.",
    },
    "Natural Gas": {
        "tickers": ["EQT", "WMB", "KMI", "LNG", "OKE"],
        "note": "Upstream (EQT), midstream pipelines (WMB, KMI, OKE), LNG export (LNG).",
    },
    "Defense & Military": {
        "tickers": ["LMT", "RTX", "NOC", "GD", "LHX"],
        "note": "Prime contractors across aircraft, missiles, munitions, electronics.",
    },
    "Blue Chips": {
        "tickers": ["AAPL", "JPM", "PG", "KO", "WMT"],
        "note": "Diversified mega-caps outside other sectors.",
    },
    "Major Pharma": {
        "tickers": ["LLY", "JNJ", "ABBV", "MRK", "PFE"],
        "note": "Large-cap pharma with GLP-1 leadership (LLY).",
    },
    "Insurance": {
        "tickers": ["BRK-B", "PGR", "CB", "TRV", "ALL"],
        "note": "Insurance & diversified: BRK-B, PGR, CB, TRV, ALL. DCF N/A — Street targets used.",
    },
    "Big Tech": {
        "tickers": ["META", "MSFT", "GOOGL", "TSLA", "NFLX"],
        "note": "Mag 7 / large-cap tech: META, MSFT, GOOGL, TSLA, NFLX.",
    },
    "Banks / Financials": {
        "tickers": ["GS", "BAC", "WFC", "MS", "LPLA"],
        "note": "Banks & brokers: Goldman Sachs (GS), Bank of America (BAC), Wells Fargo (WFC), Morgan Stanley (MS), LPL Financial (LPLA). DCF not applicable — uses Street consensus only.",
    },
}

RISK_FREE_RATE = 0.043
EQUITY_RISK_PREMIUM = 0.05
MIN_DISCOUNT_RATE = 0.07
MAX_DISCOUNT_RATE = 0.13
TERMINAL_GROWTH_CAP = 0.04
PROJECTION_YEARS = 5
DEFAULT_BETA = 1.2
DEFAULT_GROWTH = 0.10
DEFAULT_FCF_MARGIN = 0.15

def safe_get(d, key, default=None):
    try:
        val = d.get(key, default)
        return val if val is not None else default
    except Exception:
        return default

def upside_pct(fair_value, current_price):
    if fair_value is None or current_price is None or current_price == 0:
        return None
    try:
        return round(((fair_value / current_price) - 1) * 100, 2)
    except Exception:
        return None

def get_consensus_growth_path(ticker_obj, years=PROJECTION_YEARS):
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
        g_5y = max(0.03, min(g_5y_raw, 0.20))
    except Exception:
        pass
    if g_cy is None or g_cy != g_cy:
        g_cy = DEFAULT_GROWTH
    if g_ny is None or g_ny != g_ny:
        g_ny = g_cy
    if g_5y is None or g_5y != g_5y:
        g_5y = min(g_ny, 0.10)
    g_cy = max(-0.10, min(g_cy, 0.60))
    g_ny = max(-0.10, min(g_ny, 0.60))
    path = [g_cy, g_ny]
    remaining = years - 2
    if remaining > 0:
        step = (g_5y - g_ny) / remaining
        for i in range(1, remaining + 1):
            path.append(g_ny + step * i)
    return path[:years], {"consensus_cy_growth": g_cy, "consensus_ny_growth": g_ny, "consensus_5y_growth": g_5y}

def get_historical_fcf_margin(income_statement, cash_flow):
    default_quality = {"negative_years": 0, "weighted_margin_negative": False, "years_used": 0}
    try:
        revenue = income_statement.loc["Total Revenue"].dropna()
        fcf = cash_flow.loc["Free Cash Flow"].dropna()
        margins = (fcf / revenue).dropna()
        margins = margins[(margins > -1) & (margins < 1)]
        if len(margins) == 0:
            return DEFAULT_FCF_MARGIN, default_quality
        margins = margins.iloc[:3]
        weights = [3, 2, 1][:len(margins)]
        weighted_margin = sum(m * w for m, w in zip(margins, weights)) / sum(weights)
        negative_years = int((margins < 0).sum())
        quality_info = {"negative_years": negative_years, "weighted_margin_negative": weighted_margin < 0, "years_used": len(margins)}
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
    sector = (safe_get(info, "sector") or "").lower()
    industry = (safe_get(info, "industry") or "").lower()
    if "financial" in sector:
        return True
    bank_keywords = ["bank", "insurance", "capital markets", "asset management", "credit services"]
    return any(k in industry for k in bank_keywords)

def value_company(symbol, sector):
    ticker = yf.Ticker(symbol)
    info = ticker.info

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
            "symbol": symbol, "sector": sector, "company_name": safe_get(info, "shortName", symbol),
            "current_price": round(current_price, 2) if current_price else None,
            "shares_outstanding": safe_get(info, "sharesOutstanding"), "market_cap": safe_get(info, "marketCap"),
            "dcf_fair_value": None, "dcf_upside": None,
            "street_mean_target": round(street_mean, 2) if street_mean else None,
            "street_upside": upside_pct(street_mean, current_price),
            "street_median_target": round(street_median, 2) if street_median else None,
            "street_low_target": round(street_low, 2) if street_low else None,
            "street_high_target": round(street_high, 2) if street_high else None,
            "blended_fair_value": round(street_mean, 2) if street_mean else None,
            "blended_upside": upside_pct(street_mean, current_price),
            "verdict": "N/A — financial (Street only)",
            "data_warning": None,  # hide warning since it's expected for banks
            "assumptions": None, "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    income_statement = ticker.financials
    cash_flow = ticker.cashflow
    balance_sheet = ticker.balance_sheet
    current_price = safe_get(info, "currentPrice")
    shares_outstanding = safe_get(info, "sharesOutstanding")
    market_cap = safe_get(info, "marketCap")
    if not current_price or not shares_outstanding:
        raise ValueError(f"Missing core price/share data for {symbol}")

    price_currency = safe_get(info, "currency", "USD")
    financial_currency = safe_get(info, "financialCurrency", price_currency)
    fx_rate = 1.0
    if financial_currency and price_currency and financial_currency != price_currency:
        fx_rate = get_fx_rate(financial_currency, price_currency) or 1.0

    latest_revenue = float(income_statement.loc["Total Revenue"].iloc[0]) * fx_rate
    growth_path, growth_meta = get_consensus_growth_path(ticker)
    fcf_margin, fcf_quality = get_historical_fcf_margin(income_statement, cash_flow)
    discount_rate, beta = get_discount_rate(info)
    terminal_growth_rate = min(growth_meta["consensus_5y_growth"], TERMINAL_GROWTH_CAP)

    projected_fcf = []
    rev = latest_revenue
    for g in growth_path:
        rev = rev * (1 + g)
        projected_fcf.append(rev * fcf_margin)

    discounted_fcf = [fcf_year / ((1 + discount_rate) ** year) for year, fcf_year in enumerate(projected_fcf, 1)]
    final_year_fcf = projected_fcf[-1]
    terminal_value = (final_year_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** PROJECTION_YEARS)
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

    try:
        targets = ticker.analyst_price_targets
        street_mean = float(targets.get("mean")) if targets.get("mean") else None
        street_median = float(targets.get("median")) if targets.get("median") else None
        street_low = float(targets.get("low")) if targets.get("low") else None
        street_high = float(targets.get("high")) if targets.get("high") else None
    except Exception:
        street_mean = street_median = street_low = street_high = None

    blended_fair_value = (dcf_fair_value + street_mean) / 2 if street_mean else dcf_fair_value
    verdict = "UNDERVALUED" if blended_fair_value > current_price else "OVERVALUED"

    # Keep data_warning for internal but don't show badge - only show in detail panel if needed
    data_warning = None
    if fcf_quality["weighted_margin_negative"]:
        data_warning = f"Historical FCF margin negative — DCF may be distorted by cycle."
    elif fcf_quality["negative_years"] > 0:
        data_warning = f"{fcf_quality['negative_years']} of last {fcf_quality['years_used']} yrs negative FCF — use caution."

    return {
        "symbol": symbol, "sector": sector, "company_name": safe_get(info, "shortName", symbol),
        "current_price": round(current_price, 2), "shares_outstanding": shares_outstanding, "market_cap": market_cap,
        "dcf_fair_value": round(dcf_fair_value, 2), "dcf_upside": upside_pct(dcf_fair_value, current_price),
        "street_mean_target": round(street_mean, 2) if street_mean else None, "street_upside": upside_pct(street_mean, current_price),
        "street_median_target": round(street_median, 2) if street_median else None,
        "street_low_target": round(street_low, 2) if street_low else None,
        "street_high_target": round(street_high, 2) if street_high else None,
        "blended_fair_value": round(blended_fair_value, 2), "blended_upside": upside_pct(blended_fair_value, current_price),
        "verdict": verdict, "data_warning": data_warning,
        "assumptions": {
            "discount_rate": round(discount_rate, 4), "beta": round(beta, 2),
            "terminal_growth_rate": round(terminal_growth_rate, 4), "fcf_margin": round(fcf_margin, 4),
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
                print(f"  {symbol}: DCF {result['dcf_fair_value']} ({result['dcf_upside']}%) | Street {result['street_mean_target']} ({result['street_upside']}%) | Blended {result['blended_fair_value']} ({result['blended_upside']}%)")
            except Exception as e:
                print(f"  FAILED {symbol}: {e}")
                traceback.print_exc()

    with open(OUTPUT_JSON, "w") as f:
        json.dump({"generated_at": datetime.now().isoformat(), "results": results}, f, indent=2)
    build_dashboard(results)
    print(f"\nDashboard written to {OUTPUT_HTML}")

def build_dashboard(results):
    data_json = json.dumps(results)
    sectors_json = json.dumps({name: cfg["note"] for name, cfg in SECTORS.items()})
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sector Valuation Dashboard + Upside</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.32.0/plotly.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1117; color:#e8e8e8; margin:0; padding:24px; }}
  h1 {{ font-size: 20px; margin-bottom:4px; }} .subtitle {{ color:#9aa0a6; font-size:13px; margin-bottom:20px; }}
  .sector-tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
  .sector-btn {{ background:#1b1e27; border:1px solid #2c303c; color:#cfd2da; padding:9px 18px; border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; }}
  .sector-btn.active {{ background:#2E86AB; color:white; border-color:#2E86AB; }}
  .company-tabs {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:20px; }}
  .company-btn {{ background:#161922; border:1px solid #2c303c; color:#cfd2da; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; }}
  .company-btn.active {{ background:#3ddc97; color:#0f1117; border-color:#3ddc97; }}
  .sector-panel {{ display:none; }} .sector-panel.active {{ display:block; }}
  .company-panel {{ display:none; }} .company-panel.active {{ display:block; }}
  .card {{ background:#161922; border:1px solid #2c303c; border-radius:12px; padding:20px; margin-bottom:16px; }}
  .sector-note {{ font-size:13px; color:#9aa0a6; margin-bottom:16px; line-height:1.6; }}
  .stat-row {{ display:flex; gap:24px; flex-wrap:wrap; margin-top:12px; }}
  .stat {{ min-width:140px; }} .stat-label {{ font-size:11px; color:#9aa0a6; text-transform:uppercase; letter-spacing:0.5px;}} .stat-value {{ font-size:18px; font-weight:600; }}
  .upside-pos {{ color:#3ddc97; }} .upside-neg {{ color:#ff6b6b; }}
  .verdict-under {{ color:#3ddc97; }} .verdict-over {{ color:#ff6b6b; }}
  .assumptions {{ font-size:13px; color:#9aa0a6; margin-top:16px; line-height:1.6; }}
  table.summary {{ width:100%; border-collapse: collapse; font-size:13px; }}
  table.summary th, table.summary td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #2c303c; }}
  table.summary th {{ color:#9aa0a6; font-weight:500; }}
</style>
</head>
<body>
<h1>Sector Valuation Dashboard — with Upside %</h1>
<div class="subtitle">Generated {generated_at} &middot; Upside % = (Fair Value - Price)/Price &middot; Not investment advice</div>
<div class="sector-tabs" id="sector-tabs"></div>
<div id="sector-panels"></div>
<script>
const results = {data_json};
const sectorNotes = {sectors_json};
const sectorOrder = Object.keys(sectorNotes);
const bySector = {{}}; sectorOrder.forEach(s => bySector[s] = []); results.forEach(r => {{ if (bySector[r.sector]) bySector[r.sector].push(r); }});
const sectorTabsEl = document.getElementById('sector-tabs'); const sectorPanelsEl = document.getElementById('sector-panels');
function verdictClass(v) {{ return v.includes('UNDERVALUED') ? 'verdict-under' : 'verdict-over'; }}
function upsideClass(v) {{ if (v===null || v===undefined) return ''; return v>=0 ? 'upside-pos' : 'upside-neg'; }}
function fmtUpside(v) {{ if (v===null || v===undefined) return 'n/a'; return (v>0?'+':'')+v.toFixed(1)+'%'; }}
sectorOrder.forEach((sector, idx) => {{
  const items = bySector[sector];
  const sBtn = document.createElement('div'); sBtn.className = 'sector-btn' + (idx === 0 ? ' active' : ''); sBtn.textContent = sector; sBtn.onclick = () => showSector(sector); sectorTabsEl.appendChild(sBtn);
  const sPanel = document.createElement('div'); sPanel.className = 'sector-panel' + (idx === 0 ? ' active' : ''); sPanel.id = 'sector-panel-' + sector;
  const rows = items.map(r => `
    <tr>
      <td><b>${{r.symbol}}</b> &middot; ${{r.company_name}}</td>
      <td>$${{r.current_price}}</td>
      <td>${{r.dcf_fair_value !== null ? '$' + r.dcf_fair_value + ' <span class="'+upsideClass(r.dcf_upside)+'">('+fmtUpside(r.dcf_upside)+')</span>' : 'n/a'}}</td>
      <td>${{r.street_mean_target ? '$' + r.street_mean_target + ' <span class="'+upsideClass(r.street_upside)+'">('+fmtUpside(r.street_upside)+')</span>' : 'n/a'}}</td>
      <td>$${{r.blended_fair_value}} <span class="${{upsideClass(r.blended_upside)}}">(${{fmtUpside(r.blended_upside)}})</span></td>
      <td class="${{verdictClass(r.verdict)}}">${{r.verdict}}</td>
    </tr>`).join('');
  sPanel.innerHTML = `
    <div class="sector-note">${{sectorNotes[sector]}}</div>
    <div class="card">
      <table class="summary">
        <tr><th>Company</th><th>Price</th><th>DCF Model (upside)</th><th>Street Target (upside)</th><th>Blended (upside)</th><th>Verdict</th></tr>
        ${{rows}}
      </table>
    </div>
    <div id="chart-${{sector.replace(/\\s+/g,'-').replace(/\\//g,'-')}}" class="card" style="height:380px;"></div>
    <div class="company-tabs" id="company-tabs-${{sector.replace(/\\s+/g,'-').replace(/\\//g,'-')}}"></div>
    <div id="company-panels-${{sector.replace(/\\s+/g,'-').replace(/\\//g,'-')}}"></div>
  `;
  sectorPanelsEl.appendChild(sPanel);

  // CLEAN BAR CHART - NO BLUE UPSIDE BAR - ONLY 3 PRICE BARS
  Plotly.newPlot('chart-' + sector.replace(/\\s+/g,'-').replace(/\\//g,'-'), [
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.current_price), name: 'Current Price', type: 'bar', marker: {{color:'#A23B72'}} }},
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.dcf_fair_value ?? 0), name: 'DCF Fair Value', type: 'bar', marker: {{color:'#2E86AB'}} }},
    {{ x: items.map(r=>r.symbol), y: items.map(r=>r.blended_fair_value), name: 'Blended Fair Value', type: 'bar', marker: {{color:'#3ddc97'}} }},
  ], {{
    paper_bgcolor:'#161922', plot_bgcolor:'#161922', font:{{color:'#e8e8e8'}},
    barmode:'group', margin:{{t:20, b:60}}, legend:{{orientation:'h', y:-0.15}},
    yaxis: {{title:'Price $'}}
  }}, {{displayModeBar:false, responsive:true}});

  const cTabsEl = document.getElementById('company-tabs-' + sector.replace(/\\s+/g,'-').replace(/\\//g,'-'));
  const cPanelsEl = document.getElementById('company-panels-' + sector.replace(/\\s+/g,'-').replace(/\\//g,'-'));
  items.forEach((r, cIdx) => {{
    const cBtn = document.createElement('div'); cBtn.className = 'company-btn' + (cIdx === 0 ? ' active' : ''); cBtn.textContent = r.symbol; cBtn.onclick = () => showCompany(sector, r.symbol); cTabsEl.appendChild(cBtn);
    const cPanel = document.createElement('div'); cPanel.className = 'company-panel' + (cIdx === 0 ? ' active' : ''); cPanel.id = 'company-panel-' + sector.replace(/\\s+/g,'-').replace(/\\//g,'-') + '-' + r.symbol;
    const a = r.assumptions;
    const assumpHtml = a ? `Discount: ${{(a.discount_rate*100).toFixed(1)}}% (beta ${{a.beta}}) &middot; Terminal: ${{(a.terminal_growth_rate*100).toFixed(1)}}% &middot; FCF margin: ${{(a.fcf_margin*100).toFixed(1)}}%<br>Growth: ${{a.growth_path.map(g => (g*100).toFixed(1)+'%').join(' → ')}}` : 'Financial — valuation based on Street consensus price targets (DCF not applicable for banks/insurers).';
    cPanel.innerHTML = `
      <div class="card">
        <div class="stat-row">
          <div class="stat"><div class="stat-label">Current Price</div><div class="stat-value">$${{r.current_price}}</div></div>
          <div class="stat"><div class="stat-label">DCF Fair Value</div><div class="stat-value">${{r.dcf_fair_value !== null ? '$' + r.dcf_fair_value : 'n/a'}} <span class="${{upsideClass(r.dcf_upside)}}">${{fmtUpside(r.dcf_upside)}}</span></div></div>
          <div class="stat"><div class="stat-label">Street Mean</div><div class="stat-value">${{r.street_mean_target ? '$'+r.street_mean_target : 'n/a'}} <span class="${{upsideClass(r.street_upside)}}">${{fmtUpside(r.street_upside)}}</span></div></div>
          <div class="stat"><div class="stat-label">Blended</div><div class="stat-value">$${{r.blended_fair_value}} <span class="${{upsideClass(r.blended_upside)}}">${{fmtUpside(r.blended_upside)}}</span></div></div>
          <div class="stat"><div class="stat-label">Verdict</div><div class="stat-value ${{verdictClass(r.verdict)}}">${{r.verdict}}</div></div>
        </div>
        <div id="companychart-${{sector.replace(/\\s+/g,'-').replace(/\\//g,'-')}}-${{r.symbol}}" style="height:340px; margin-top:20px;"></div>
        <div class="assumptions"><b>Model assumptions:</b><br>${{assumpHtml}}</div>
      </div>
    `;
    cPanelsEl.appendChild(cPanel);
    Plotly.newPlot('companychart-' + sector.replace(/\\s+/g,'-').replace(/\\//g,'-') + '-' + r.symbol, [{{
      x: ['Current Price', 'DCF Model', 'Street Mean', 'Blended'],
      y: [r.current_price, r.dcf_fair_value ?? 0, r.street_mean_target || 0, r.blended_fair_value],
      type: 'bar', marker: {{color: ['#A23B72', '#2E86AB', '#f4a261', '#3ddc97']}},
      text: [r.current_price, r.dcf_fair_value, r.street_mean_target, r.blended_fair_value].map((v,i) => {{
        const ups = [null, r.dcf_upside, r.street_upside, r.blended_upside][i];
        return v ? '$'+v.toFixed(2)+(ups!=null?' ('+fmtUpside(ups)+')':'') : 'n/a';
      }}), textposition: 'outside',
    }}], {{ paper_bgcolor:'#161922', plot_bgcolor:'#161922', font:{{color:'#e8e8e8'}}, margin:{{t:20}}, title: {{text: r.symbol + ': ' + r.company_name, font:{{size:14}}}} }}, {{displayModeBar:false, responsive:true}});
  }});
}});
function showSector(sector) {{
  document.querySelectorAll('.sector-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sector-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('sector-panel-' + sector).classList.add('active');
  [...document.querySelectorAll('.sector-btn')].find(b => b.textContent === sector).classList.add('active');
}}
function showCompany(sector, symbol) {{
  const key = sector.replace(/\\s+/g,'-').replace(/\\//g,'-');
  document.querySelectorAll('#company-panels-' + key + ' .company-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('#company-tabs-' + key + ' .company-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('company-panel-' + key + '-' + symbol).classList.add('active');
  [...document.querySelectorAll('#company-tabs-' + key + ' .company-btn')].find(b => b.textContent === symbol).classList.add('active');
}}
</script>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

if __name__ == "__main__":
    main()
