# Trading Intelligence Engine

A personal research platform for systematic market analysis.

Status:
Core market-analysis and historical decision-intelligence architecture complete
(Sprints 11A–12E). A local web dashboard / productization layer is available
(`dashboard/`) that exposes the existing intelligence engine for intraday
discretionary trading research.

The architecture is DESCRIPTIVE ONLY: it provides technical-analysis and
historical research context, does not guarantee future performance, and does not
constitute a trading recommendation. No ML / probability model / broker /
live-trading / order-execution is included.

## Run the dashboard

```bash
pip install -r requirements.txt
python -m uvicorn dashboard.app:app --reload
# open http://127.0.0.1:8000
```

Out of the box the dashboard runs on local deterministic OHLCV fixtures
(NIFTY / RELIANCE / TCS / HDFCBANK / ICICIBANK, 15m setup + 1D context). An
optional Yahoo Finance provider can be enabled with
`DASHBOARD_PROVIDER=yahoo` (requires `pandas` / `yfinance`; failures are
handled gracefully and never crash the dashboard).

See `AGENTS.md` for the full architecture, the dashboard section, API routes,
no-look-ahead guarantees, trade-geometry semantics and limitations.
