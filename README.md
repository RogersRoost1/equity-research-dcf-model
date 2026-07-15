# Tech Valuation Dashboard

Multi-company DCF model (`data_pull.py`) for **MU, AVGO, GOOGL, MRVL, AMZN, SNDK**,
anchored to Wall Street consensus estimates pulled live from `yfinance`.

## What changed vs. the original single-ticker script

- Loops over all 6 tickers instead of one.
- Growth assumptions are no longer hand-picked. They come from:
  - `ticker.revenue_estimate` → consensus revenue growth for the current and next
    fiscal year (Years 1–2 of the DCF).
  - `ticker.growth_estimates` → 5-year consensus growth estimate, which Years 3–5
    taper toward.
- Discount rate is CAPM-based (`risk-free rate + beta × equity risk premium`)
  instead of one flat number for every company.
- FCF margin uses each company's own 3-year historical average instead of a
  guessed margin-expansion curve.
- Pulls `ticker.analyst_price_targets` (the actual Street mean/median/low/high
  target) and reports a **Blended Fair Value** = average of your DCF output and
  the Street mean target — this is the lever that keeps the model's output
  closer to consensus, per your request.
- Output is now `dashboard.html`: one interactive tab per company (Plotly bar
  chart + assumptions), plus a Summary tab comparing all six at once. Also
  writes `valuation_results.json` if you want to log history over time.

## Run it once

```bash
pip install yfinance
python data_pull.py
```

Open `dashboard.html` in a browser.

## Making it update automatically every day

There's no way for me to run code on your machine on a schedule from this chat —
but here are the two standard ways to get a real daily auto-refresh, from
easiest to "fully hands-off":

### Option A — Local scheduler (simplest, requires your computer to run daily)

**macOS/Linux (cron):** run `crontab -e` and add a line to run it every morning
at 7am, for example:
```
0 7 * * * cd /path/to/valuation_dashboard && /usr/bin/python3 data_pull.py
```

**Windows (Task Scheduler):** create a Basic Task → Trigger: Daily →
Action: "Start a program" → Program: `python.exe` → Arguments:
`data_pull.py` → Start in: your project folder.

Either way, `dashboard.html` gets overwritten with fresh numbers each run —
just keep the file open/refresh your browser tab.

### Option B — GitHub Actions (truly automatic, runs in the cloud daily, no computer needed)

1. Push this folder to a GitHub repo.
2. Add `.github/workflows/daily_update.yml` (content below).
3. Enable GitHub Pages for the repo (Settings → Pages → deploy from the
   branch the workflow commits to). You'll get a public URL that shows the
   latest `dashboard.html`, refreshed daily, automatically — no local machine
   needs to be on.

```yaml
name: Daily Valuation Update

on:
  schedule:
    - cron: '0 12 * * 1-5'   # 12:00 UTC, weekdays (~8am ET) — adjust as needed
  workflow_dispatch: {}       # lets you also trigger it manually from GitHub

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install yfinance
      - run: python data_pull.py
      - name: Commit updated dashboard
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add dashboard.html valuation_results.json
          git commit -m "Daily valuation update $(date +%F)" || echo "No changes"
          git push
```

## Notes / limitations

- Yahoo's data (via `yfinance`) is unofficial and occasionally missing fields
  for a given ticker (e.g., a thinly-covered stock might lack a 5yr growth
  estimate) — the script falls back to conservative defaults when that
  happens and still runs, but check the console output for any "FAILED"
  lines.
- This is a simplified DCF for illustration, not a substitute for a full
  model (e.g., it doesn't model stock-based comp dilution, buybacks, or
  segment-level detail). Treat both the DCF and Street numbers as inputs to
  your own thinking, not a recommendation.
- SNDK is SanDisk (spun off from Western Digital in 2025) — confirm the
  ticker is still correct on your data provider if it's been a while.
