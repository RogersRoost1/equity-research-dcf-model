import yfinance as yf

# Pull Micron's data
ticker = yf.Ticker("MU")

# Get financial statements
income_statement = ticker.financials
cash_flow = ticker.cashflow
balance_sheet = ticker.balance_sheet

# Get current stock info
info = ticker.info
current_price = info.get("currentPrice")
shares_outstanding = info.get("sharesOutstanding")
market_cap = info.get("marketCap")

# Print everything so we can see what we're working with
print("=== CURRENT PRICE INFO ===")
print(f"Current Price: ${current_price}")
print(f"Shares Outstanding: {shares_outstanding}")
print(f"Market Cap: ${market_cap}")

print("\n=== INCOME STATEMENT ===")
print(income_statement)

print("\n=== CASH FLOW STATEMENT ===")
print(cash_flow)

print("\n=== BALANCE SHEET ===")
print(balance_sheet)

# Get revenue history
revenue = income_statement.loc["Total Revenue"]
print("=== REVENUE (last 4 years) ===")
print(revenue)

# Get free cash flow history
fcf = cash_flow.loc["Free Cash Flow"]
print("\n=== FREE CASH FLOW (last 4 years) ===")
print(fcf)

# Calculate FCF margin (FCF / Revenue) for each year
fcf_margin = fcf / revenue
print("\n=== FCF MARGIN (FCF / Revenue) ===")
print(fcf_margin)

# --- DCF PROJECTION ---

# Use most recent revenue as our starting point
latest_revenue = revenue.iloc[0]  # 2025 revenue

# Assumptions (you can adjust these later)
revenue_growth_rate = 0.10      # 10% annual revenue growth
normalized_fcf_margin = 0.065   # 6.5% FCF margin (avg of normal years)
discount_rate = 0.10            # 10% WACC assumption
terminal_growth_rate = 0.03     # 3% long-term growth
projection_years = 5

# Project revenue and FCF for each future year
projected_revenue = []
projected_fcf = []
rev = latest_revenue

for year in range(1, projection_years + 1):
    rev = rev * (1 + revenue_growth_rate)
    fcf_year = rev * normalized_fcf_margin
    projected_revenue.append(rev)
    projected_fcf.append(fcf_year)

print("\n=== PROJECTED REVENUE (Years 1-5) ===")
for i, r in enumerate(projected_revenue, 1):
    print(f"Year {i}: ${r:,.0f}")

print("\n=== PROJECTED FREE CASH FLOW (Years 1-5) ===")
for i, f in enumerate(projected_fcf, 1):
    print(f"Year {i}: ${f:,.0f}")

    # --- DISCOUNT CASH FLOWS BACK TO PRESENT VALUE ---

discounted_fcf = []
for year, fcf_year in enumerate(projected_fcf, 1):
    pv = fcf_year / ((1 + discount_rate) ** year)
    discounted_fcf.append(pv)

print("\n=== DISCOUNTED FREE CASH FLOW (Present Value) ===")
for i, pv in enumerate(discounted_fcf, 1):
    print(f"Year {i}: ${pv:,.0f}")

# --- TERMINAL VALUE ---
# Value of all cash flows beyond year 5, assuming steady growth forever after

final_year_fcf = projected_fcf[-1]
terminal_value = (final_year_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
discounted_terminal_value = terminal_value / ((1 + discount_rate) ** projection_years)

print(f"\nTerminal Value (undiscounted): ${terminal_value:,.0f}")
print(f"Terminal Value (discounted to today): ${discounted_terminal_value:,.0f}")

# --- ENTERPRISE VALUE ---
enterprise_value = sum(discounted_fcf) + discounted_terminal_value
print(f"\nEnterprise Value: ${enterprise_value:,.0f}")

# --- EQUITY VALUE ---
# Get net debt from balance sheet
total_debt = balance_sheet.loc["Total Debt"].iloc[0]
cash = balance_sheet.loc["Cash And Cash Equivalents"].iloc[0]
net_debt = total_debt - cash

equity_value = enterprise_value - net_debt
print(f"Net Debt: ${net_debt:,.0f}")
print(f"Equity Value: ${equity_value:,.0f}")

# --- FAIR VALUE PER SHARE ---
fair_value_per_share = equity_value / shares_outstanding

print(f"\n=== FINAL RESULT ===")
print(f"Fair Value Per Share: ${fair_value_per_share:,.2f}")
print(f"Current Market Price: ${current_price:,.2f}")

if fair_value_per_share > current_price:
    print("Verdict: Potentially UNDERVALUED based on this model")
else:
    print("Verdict: Potentially OVERVALUED based on this model")