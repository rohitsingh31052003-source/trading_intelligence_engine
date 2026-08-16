# Trading Intelligence Engine

A personal research platform for systematic market analysis.

Status:
Core market-analysis and historical decision-intelligence architecture complete
(Sprints 11A–12E). A local web dashboard / productization layer is available
(`dashboard/`) that exposes the existing intelligence engine as an intraday
trade-review workstation for discretionary trading research.

The architecture is DESCRIPTIVE ONLY: it provides technical-analysis and
historical research context, does not guarantee future performance, and does not
constitute a trading recommendation. No ML / probability model / broker /
live-trading / order-execution / position-sizing is included.

## Run the dashboard

```bash
pip install -r requirements.txt
python -m uvicorn dashboard.app:app --reload
# open http://127.0.0.1:8000
```

or `python -m dashboard.app`. Env vars: `DASHBOARD_PROVIDER` (`fixture` default |
`yahoo`), `DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_EVIDENCE_PATH`
(optional path to a serialized Sprint 11Y `HistoricalEvidenceReport` to enable the
evidence section).

## Supported local data / timeframes

Out of the box the dashboard runs on local deterministic OHLCV fixtures
(NIFTY / RELIANCE / TCS / HDFCBANK / ICICIBANK). Only **15m setup + 1D context**
are available from the fixtures; every other offered timeframe shows an honest
"unavailable" state (actionability `INVALID`) — no data is fabricated. An optional
Yahoo Finance provider (`DASHBOARD_PROVIDER=yahoo`, requires `pandas` / `yfinance`)
can extend coverage; failures are handled gracefully and never crash the dashboard.

## What the dashboard shows (trade review)

The page is a **trade-review interface**, not a signal generator. For the selected
instrument + timeframe it surfaces the existing engine's outputs as separated,
inspectable sections:

- **Actionability** — a presentation review state (the headline): `INVALID`,
  `NO_OPPORTUNITY`, `TRADE_GEOMETRY_UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`,
  `READY_FOR_REVIEW`, or `WAIT`. This is a *deterministic presentation mirror* of
  the existing decision classification + opportunity status + trade-geometry
  completeness + evidence strength. It is **not** a predictive score, not a
  probability, and not a BUY/SELL/ENTER/EXIT/HOLD recommendation.
- **Market Overview** — reused Sprint 11P market context + Sprint 11U MTF
  alignment (price, trends, structure, support/resistance, MTF alignment).
- **Decision** — the existing authoritative Sprint 11S decision classification
  (`REJECTED` / `WATCH` / `QUALIFIED` / `PREFERRED`) + Sprint 11T opportunity
  status, reused **verbatim**. The dashboard never renames `QUALIFIED` to `BUY`
  or `PREFERRED` to `BUY`, and never upgrades/downgrades the decision.
- **Trade Geometry** — entry / stop (invalidation) / target 1 / risk / reward /
  R:R, reused **verbatim** from the Sprint 11R `TradeCandidate` (reached via the
  scan decision). When geometry is incomplete the panel shows
  "TRADE GEOMETRY UNAVAILABLE" — no level is invented to make it look complete.
- **Evidence** — optionally, a reused Sprint 11Y/11Z historical evidence view.
  Observed historical result and evidence strength are kept **separate** and are
  never merged into a single "confidence score". When no offline corpus is
  attached, evidence is honestly `UNAVAILABLE`.
- **Setup Details**, **Chart**, **Warnings & Limitations**.

## Trade geometry provenance / limitations

- `entry` = candidate `entry_reference`; `stop` = `stop_reference`;
  `target_1` = `target_reference`; `risk` / `reward` / `R:R` = the candidate's
  `risk_distance` / `reward_distance` / `risk_reward_ratio`; `geometry_complete`
  = the candidate's flag. The invalidation level **is** the stop.
- **Target 2 is not supported** by the architecture: `target_2 = None` with an
  explicit `target_2_supported = False` flag. The dashboard never invents a second
  target or speculative price projections, and never recomputes R:R in the
  frontend.
- **Evidence limitation**: the dashboard does not compute evidence. An offline
  Sprint 11Y evidence corpus may be attached to surface historical evidence; a
  1-trade 100% winner is never presented as strong evidence (sample-size hard
  gate). Missing evidence (no corpus) is not the same as insufficient evidence.

## No-look-ahead principle

The analysis always uses the latest **COMPLETED** candle. The evaluation point is
the close timestamp of the latest completed setup-timeframe candle; the higher
(1D) context uses only the latest higher-timeframe candle that closed strictly
before that point. The service never calls the Sprint 11W outcome evaluator during
current analysis, never runs the historical pipeline, and its public API accepts
no "future candles" argument. Appended future candles cannot change a fixed-T
analysis (entry / stop / target / decision).

## What the dashboard does NOT do

The dashboard is descriptive/contextual. It does **not** guarantee future
performance, does **not** predict winning trades, does **not** claim high accuracy
or guaranteed signals, and does **not** imply a "profitable strategy". It has no
broker integration, no order placement, no live execution, no position sizing,
and no portfolio management. Those are intentionally out of scope.

See `AGENTS.md` for the full architecture, the dashboard section, API routes,
no-look-ahead guarantees, trade-geometry semantics and limitations.
