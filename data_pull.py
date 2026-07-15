import yfinance as yf
import matplotlib.pyplot as plt

# Pull Micron's data
ticker = yf.Ticker("MU")

income_statement = ticker.financials
cash_flow = ticker.cashflow
balance_sheet = ticker.balance_sheet

info = ticker.info
current_price = info.get("currentPrice")
shares_outstanding = info.get("sharesOutstanding")
market_cap = info.get("marketCap")

revenue = income_statement.loc["Total Revenue"]
fcf = cash_flow.loc["Free Cash Flow"]
fcf_margin = fcf / revenue

print("=== CURRENT PRICE INFO ===")
print(f"Current Price: ${current_price}")
print(f"Shares Outstanding: {shares_outstanding}")
print(f"Market Cap: ${market_cap}")

# --- DCF PROJECTION (revised to reflect AI-driven memory supercycle) ---
latest_revenue = revenue.iloc[0]

# Assumptions calibrated to Street consensus (UBS, BofA, Citi lower bound)
# Reflects DRAM pricing surge, HBM demand, and constrained supply through 2027
discount_rate = 0.09
terminal_growth_rate = 0.04
projection_years = 5

# Tapering growth: aggressive near-term (supply-constrained pricing), 
# normalizing as new capacity comes online post-2027
growth_rates = [0.35, 0.35, 0.25, 0.20, 0.15]
fcf_margins = [0.18, 0.20, 0.20, 0.18, 0.17]  # margin expansion then slight normalization

projected_revenue = []
projected_fcf = []
rev = latest_revenue

for year in range(projection_years):
    rev = rev * (1 + growth_rates[year])
    fcf_year = rev * fcf_margins[year]
    projected_revenue.append(rev)
    projected_fcf.append(fcf_year)

print("\n=== PROJECTED FREE CASH FLOW (Years 1-5) ===")
for i, f in enumerate(projected_fcf, 1):
    print(f"Year {i}: ${f:,.0f}")

# --- DISCOUNT CASH FLOWS ---
discounted_fcf = []
for year, fcf_year in enumerate(projected_fcf, 1):
    pv = fcf_year / ((1 + discount_rate) ** year)
    discounted_fcf.append(pv)

# --- TERMINAL VALUE ---
final_year_fcf = projected_fcf[-1]
terminal_value = (final_year_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
discounted_terminal_value = terminal_value / ((1 + discount_rate) ** projection_years)

# --- ENTERPRISE / EQUITY VALUE ---
enterprise_value = sum(discounted_fcf) + discounted_terminal_value

total_debt = balance_sheet.loc["Total Debt"].iloc[0]
cash = balance_sheet.loc["Cash And Cash Equivalents"].iloc[0]
net_debt = total_debt - cash

equity_value = enterprise_value - net_debt
fair_value_per_share = equity_value / shares_outstanding

print(f"\n=== FINAL RESULT ===")
print(f"Fair Value Per Share: ${fair_value_per_share:,.2f}")
print(f"Current Market Price: ${current_price:,.2f}")

if fair_value_per_share > current_price:
    print("Verdict: Potentially UNDERVALUED based on this model")
else:
    print("Verdict: Potentially OVERVALUED based on this model")

# --- CHART (must come after fair_value_per_share is calculated) ---
labels = ["Model Fair Value", "Current Market Price"]
values = [fair_value_per_share, current_price]

plt.figure(figsize=(6,4))
plt.bar(labels, values, color=["#2E86AB", "#A23B72"])
plt.title("Micron (MU): Model Fair Value vs. Market Price")
plt.ylabel("Price per Share ($)")
for i, v in enumerate(values):
    plt.text(i, v + 10, f"${v:,.2f}", ha='center', fontweight='bold')
plt.savefig("valuation_chart.png", dpi=150, bbox_inches="tight")
print("\nChart saved as valuation_chart.png")