# Trading Intelligence Engine

A personal research platform for systematic market analysis.

Status:
Core market-analysis and historical decision-intelligence architecture complete
(Sprints 11A–12E). A local web dashboard / productization layer is available
(`dashboard/`) that exposes the existing intelligence engine as an intraday
trade-review workstation for discretionary trading research. The productization
roadmap is: Product Phase 1 (live / near-live market-data integration) — COMPLETE;
Product Phase 2 (multi-instrument scanner & watchlist) — COMPLETE;
Product Phase 3 (live trading workstation / dashboard) — COMPLETE;
Product Phase 4 (risk & trade planning) — FUTURE; Product Phase 5 (paper trading
& real-world validation) — FUTURE.

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

## Live / near-live market data (Product Phase 1)

The dashboard can run on two data sources via `DASHBOARD_PROVIDER`:

- `fixture` (default) — deterministic local OHLCV fixtures, no network. Safe for
  research, demos and tests.
- `yahoo` — optional live / near-live Yahoo Finance data (requires the optional
  `pandas` / `yfinance` packages). Select it with `DASHBOARD_PROVIDER=yahoo`.

```bash
pip install pandas yfinance
DASHBOARD_PROVIDER=yahoo python -m uvicorn dashboard.app:app --reload
```

The live provider is **data integration only** — "live data" does **not** mean
"live trading". It does not predict the market, does not generate signals, and
does not modify the existing decision engine. The analysis always uses the
latest **COMPLETED** candle; a currently-forming / in-progress candle is never
fed to the intelligence engine (it is shown for display only), and future-dated
candles returned by the provider are rejected. Yahoo-specific symbol formatting
(e.g. `NIFTY` -> `^NSEI`, `RELIANCE` -> `RELIANCE.NS`) is isolated inside the
provider. Any provider failure (network, timeout, empty response, unsupported
instrument / timeframe, malformed candle) is reported honestly as an
"unavailable" state — the dashboard never crashes, never fabricates data, never
substitutes stale data for current data, and never silently falls back from a
failed live provider to fixture data. Supported Yahoo timeframes are the native
intervals `1m`/`2m`/`5m`/`15m`/`30m`/`60m`/`1h`/`90m`/`1D` (no resampling /
fabrication of other intervals). Freshness (`CURRENT` / `STALE` / `UNAVAILABLE`
/ `INVALID`) is data quality / product state only — it never alters the decision.

## Multi-instrument scanner / watchlist (Product Phase 2)

The dashboard also has a **multi-instrument scanner / watchlist** view at
`/scan` (JSON at `/api/scan`) that scans every instrument in a watchlist
independently using the EXISTING intelligence pipeline and presents the
resulting opportunities in one coherent, deterministically ordered view.

```bash
python -m uvicorn dashboard.app:app --reload
# open http://127.0.0.1:8000/scan
# or GET /api/scan?timeframe=15m&instruments=NIFTY,RELIANCE,TCS
```

- **Watchlist** — a small, deterministic, validated collection of instrument
  names (`dashboard.watchlist.Watchlist`): add / remove with duplicate
  prevention, canonical (stripped + upper-cased) names, and lexicographic
  ordering so input order never affects results. No persistence (local
  workstation only). The default watchlist is the local fixture instruments.
- **Reuse-only** — the scanner orchestrates the existing single-instrument
  analysis (`DashboardAnalysisService.analyze`) per instrument. It implements NO
  new market-analysis, decision, scoring, geometry or evidence logic; every
  per-row value (decision, actionability, geometry, evidence, freshness) is read
  from the reused trade view.
- **Presentation ordering, not a score** — rows are ordered by a fixed
  **presentational** key: decision classification (`PREFERRED` < `QUALIFIED` <
  `WATCH` < `REJECTED`), then actionability/readiness, then evidence strength,
  then geometry availability, then freshness, then instrument name. This is a
  SORT, not a probability / predictive score / new ranking layer. Direction
  (LONG/SHORT) is deliberately NOT a ranking key. The existing decision
  classification is reused **verbatim** — never renamed to BUY/SELL, never
  upgraded or downgraded.
- **Failure isolation** — one instrument that fails (provider timeout /
  unsupported instrument / unsupported timeframe / empty / malformed / invalid
  data) is reported as an honest `INVALID` row and the scan CONTINUES with the
  remaining instruments. One bad symbol never aborts the whole scan.
- **Completed-candle guarantee** — each instrument is evaluated using the latest
  COMPLETED setup candle; a forming candle is never fed to the engine and no
  future candle is read (Product Phase 1 guarantees preserved). The scanner never
  calls the Sprint 11W outcome evaluator and never runs the historical pipeline.
- **Determinism** — input watchlist ordering never changes the output order;
  two scans of identical data always produce identical results and row order.
- **Kept concerns separate** — decision / geometry / evidence / actionability /
  data source are never collapsed into one "signal". Target 2 remains
  unsupported (`None` + `target_2_supported=False`). Evidence `UNAVAILABLE` (no
  corpus) is NOT the same as `INSUFFICIENT` (and is never fabricated).

The scanner is DESCRIPTIVE ONLY — it does not predict, does not guarantee
profitability, and does not constitute a trading recommendation.

## Live trading workstation (Product Phase 3)

The dashboard has a coherent **live trading workstation** at `/workstation`
(JSON at `/api/workstation`) that bundles the watchlist scanner + the selected
instrument's detailed trade review into one view for intraday market
monitoring and trade review. It is the natural "monitor the watchlist + inspect
one instrument" surface a human trader uses.

```bash
python -m uvicorn dashboard.app:app --reload
# open http://127.0.0.1:8000/workstation
# or GET /api/workstation?instrument=NIFTY&timeframe=15m
```

- **Reuse-only** — the workstation orchestrates the EXISTING
  `DashboardAnalysisService.scan_watchlist` (Product Phase 2) +
  `analyze` (Product Phase 1) for the selected instrument. It implements NO
  new market-analysis, decision, scoring, geometry or evidence logic; every
  value is read from the reused outputs.
- **Layout** — top controls (timeframe / watchlist / focus instrument /
  **Refresh**), a Market / Watchlist Status table (reused scanner rows, each
  row links to focus that instrument in the workstation), the Selected
  Instrument detail (reused trade-review sections: data source,
  actionability, market overview, decision, trade geometry, evidence, chart,
  warnings), a "Why is this in its current state?" explanation (descriptive
  text synthesized from the reused outputs), and a consolidated Limitations
  section.
- **Navigation** — the workstation links back to `/scan` (Scanner) and `/`
  (Trade Review); scanner rows link to the workstation; the nav bar offers
  Workstation / Scanner / Trade Review. All existing routes are preserved.
- **Refresh is deliberate + manual** — the **Refresh** button re-submits the
  form (a GET request) and re-runs the analysis over the latest COMPLETED
  candle. There is **no background polling and no WebSocket streaming**. The
  `refresh_token` is the honest evaluation boundary (the latest completed
  candle timestamp), never a wall-clock value during fixture analysis.
- **Completed-candle boundary** — inherited from Product Phase 1: the
  analysis always uses the latest COMPLETED setup candle; a forming candle is
  shown for display only and is never fed to the engine; future-dated candles
  are rejected. The workstation never calls the Sprint 11W outcome evaluator
  and never runs the historical pipeline during current analysis.
- **Decision authority** — the existing Sprint 11S decision classification
  (`REJECTED` / `WATCH` / `QUALIFIED` / `PREFERRED`) is reused **verbatim** —
  never renamed to BUY/SELL, never upgraded / downgraded. The watchlist row
  order is the reused PRESENTATIONAL ordering (a sort, not a predictive
  score).
- **Trade geometry** — entry / stop (invalidation) / target 1 / risk / reward
  / R:R are reused **verbatim** from the Sprint 11R `TradeCandidate`. Target 2
  remains unsupported (`None` + `target_2_supported=False`).
- **Evidence** — evidence is reused from the optional offline Sprint 11Y
  corpus; without a corpus it is honestly `UNAVAILABLE` (NOT `INSUFFICIENT`,
  never fabricated).
- **Determinism** — repeated / shuffled-input scans produce identical output
  and row order; the selected instrument is chosen deterministically when not
  specified (first analyzed row), never invented.

The workstation is DESCRIPTIVE ONLY — it does not predict, does not guarantee
profitability, and does not constitute a trading recommendation.

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

The dashboard (including the live trading workstation) is descriptive/contextual.
It does **not** guarantee future performance, does **not** predict winning
trades, does **not** claim high accuracy or guaranteed signals, and does
**not** imply a "profitable strategy". It has no broker integration, no order
placement, no live execution, no position sizing, and no portfolio management.
There is no background polling / WebSocket streaming / autonomous trading. Risk
& trade planning (Product Phase 4), paper trading & real-world validation
(Product Phase 5), and broker integration / order execution are intentionally
out of scope for the current phase.

See `AGENTS.md` for the full architecture, the dashboard section, API routes,
no-look-ahead guarantees, trade-geometry semantics and limitations.
