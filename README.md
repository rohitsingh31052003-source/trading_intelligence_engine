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
Product Phase 4 (risk & trade planning) — COMPLETE; Product Phase 5 (paper trading
& real-world validation) — COMPLETE; Product Phase 6A (historical market-data
foundation) — COMPLETE.

The architecture is DESCRIPTIVE ONLY: it provides technical-analysis and
historical research context, does not guarantee future performance, and does not
constitute a trading recommendation. No ML / probability model / broker /
live-trading / order-execution / autonomous-trading is included; position sizing
is a deterministic risk calculation around the existing trade geometry only; paper
trading is observational validation only and places no real order.

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

## Risk & trade planning (Product Phase 4)

The workstation has a **Trade Plan / Risk Planning** section and a REST endpoint
`GET /api/trade-plan` that convert an EXISTING trade candidate / trade geometry
into a disciplined, user-specific trade plan. The user supplies **account capital**
and **risk percentage per trade**; the planner computes the deterministic position
size from the existing engine geometry.

```bash
# In the workstation UI: enter account capital + risk %, click "Plan risk"
# or call the API directly:
curl "http://127.0.0.1:8000/api/trade-plan?instrument=NIFTY&timeframe=15m&account_capital=100000&risk_percent=1"
```

- **Purpose** — existing decision + existing trade geometry + user/account risk
  parameters = structured trade plan. It answers *"if I choose to review/take this
  existing candidate, how much should I risk and what position size follows from my
  risk rules?"* It does **not** answer "will this trade definitely win?" and never
  produces BUY/SELL/ENTER/EXIT/HOLD recommendations.
- **Authoritative geometry** — entry / stop / target_1 / risk_distance /
  reward_distance / risk_reward_ratio are reused **verbatim** from the Sprint 11R
  `TradeCandidate`. The planner never recomputes a second entry / stop / target /
  R:R and never invents Target 2 (`target_2 = None`, `target_2_supported = False`).
- **Account risk vs engine risk (kept separate)** — ENGINE RISK = the candidate's
  `risk_distance`; ACCOUNT RISK = `account_capital * risk_percent / 100` =
  `maximum_risk`. The planner converts engine geometry into account-level risk; it
  never modifies the underlying engine geometry.
- **Risk calculation** — `maximum_risk = account_capital * risk_percent / 100`;
  `quantity = maximum_risk / engine_risk_distance` (scaled by an optional contract
  multiplier). When fractional quantities are disallowed the quantity is
  **floor**-rounded to the largest integer whose `planned_risk = quantity *
  engine_risk_distance` does **not** exceed `maximum_risk` — floor is the only
  rounding mode that guarantees `planned_risk <= maximum_risk`. `planned_reward =
  quantity * engine_reward_distance` is deterministic (NOT an expected return / not
  a prediction).
- **Quantity / lot handling** — the repository has no authoritative broker /
  exchange contract metadata, so the planner uses a **safe generic** quantity model
  by default (unit step, unit multiplier, fractional allowed). When no instrument
  `QuantitySpec` is supplied the plan surfaces an explicit
  `QUANTITY_SPEC_UNAVAILABLE` warning — no NSE lot size or broker-specific
  contract rule is fabricated.
- **Long / short symmetry** — direction is reused from the existing candidate
  where available; risk is the absolute distance between entry and stop for **both**
  directions, so long and short sizing are mathematically correct.
- **Validation** — invalid capital (<=0), invalid risk % (<=0 / above the configured
  maximum), missing geometry, zero / negative risk distance, NaN / infinity and
  inconsistent candidate geometry become an `INVALID_INPUT` or
  `GEOMETRY_UNAVAILABLE` / `RISK_LIMIT_EXCEEDED` plan — never a successful trade
  plan. Invalid financial inputs are never silently repaired.
- **Decision / actionability / evidence preserved** — the existing decision
  classification (`REJECTED` / `WATCH` / `QUALIFIED` / `PREFERRED`) is reused
  verbatim — never renamed to BUY/SELL, never upgraded / downgraded. The existing
  actionability is reused verbatim. A separate `RiskPlanStatus`
  (`VALID` / `INVALID_INPUT` / `GEOMETRY_UNAVAILABLE` / `RISK_LIMIT_EXCEEDED` /
  `QUANTITY_UNAVAILABLE`) describes the risk plan, **not** the market decision.
  Evidence is never used to calculate position size and never converted into a
  risk percentage.
- **No prediction** — the planner produces no probability / win-rate / AI
  confidence / predictive score / expected return. `planned_reward` is
  deterministic from `quantity * reward_distance` and is explicitly distinguished
  from "expected return".
- **Deterministic ID** — `plan_id = "plan-" + sha256[:16]` of the canonical
  normalized inputs; repeated identical inputs produce identical ids, changing
  capital or risk % changes the id. No random / wall-clock / memory-address
  component.
- **Serialization** — versioned canonical JSON
  (`TRADE_PLAN_SCHEMA_VERSION = 1`); round-trips losslessly for every audit field;
  rejects unsupported future schema versions with `ValueError`; no `pickle` /
  `eval` / `exec`.
- **No-look-ahead** — the planner consumes already-computed engine geometry only
  and takes no candle / future-market-data argument; it never calls the Sprint 11W
  `OutcomeEvaluator` and never runs the `HistoricalEvaluationPipeline` (verified by
  patching both to raise). It uses the same completed-candle analysis as the
  workstation.
- **Numeric safety** — all financial math is done in `Decimal`; binary floating
  point is never used for money. The rounding policy (`floor` only) is explicitly
  documented and enforced.
- **UI safety** — the UI contains no "BUY NOW" / "SELL NOW" / "EXECUTE" / "PLACE
  ORDER" buttons; it uses terminology such as *Trade Plan*, *Risk Plan*, *Maximum
  Planned Loss*, *Potential Planned Reward*.

The trade plan is DESCRIPTIVE ONLY — it is a deterministic risk calculation, not a
prediction or guarantee of future performance, and not a trading recommendation.

## Paper trading & real-world validation (Product Phase 5)

The workstation has a **Paper Trading** journal page (`GET /paper-trading`) and
REST endpoints under `/api/paper-trades` that record what the system's EXISTING
trade opportunities would have done if followed in real / near-live market
conditions. A human reviews an opportunity on the workstation, then deliberately
creates a paper trade; the system tracks observed entry / exit conditions against
COMPLETED market candles and records the result.

```bash
# Create a paper trade from the existing current analysis + trade plan:
curl -X POST "http://127.0.0.1:8000/api/paper-trades?instrument=NIFTY&timeframe=15m&account_capital=100000&risk_percent=1"
# Track an open paper trade against the latest completed candles:
curl -X POST "http://127.0.0.1:8000/api/paper-trades/<id>/track"
# Review the journal + descriptive performance:
curl "http://127.0.0.1:8000/api/paper-trades"
```

- **Purpose** — establish a disciplined feedback loop: SYSTEM OBSERVATION → SYSTEM
  DECISION → TRADE PLAN → PAPER TRADE → REAL MARKET OBSERVATION → ACTUAL RESULT →
  MEASURED VALIDATION. It does **not** predict the market, does **not** guarantee
  profitability, and does **not** place any real order.
- **Authoritative reuse** — the existing Sprint 11S decision (`REJECTED` /
  `WATCH` / `QUALIFIED` / `PREFERRED`), the existing Sprint 11R trade geometry
  (entry / stop / target / risk / reward / R:R), and the existing Product Phase 4
  trade plan (account capital / risk % / maximum risk / quantity / planned risk)
  are reused **verbatim**. The paper-trading layer performs NO new position sizing
  and never recomputes geometry. Target 2 remains unsupported.
- **Lifecycle** — `WAITING_FOR_ENTRY` → `OPEN` → `CLOSED` (or `CANCELLED` /
  `INVALIDATED`). Illegal transitions fail safely (never silently converted into
  success). A human creates the trade deliberately; there is **no automatic
  trading** and the scanner is not turned into an auto-trading strategy.
- **Entry rule** (conservative, deterministic) — a paper trade enters when a
  COMPLETED candle after creation touches the entry reference (LONG: `low <= entry`;
  SHORT: `high >= entry`). Entry price = the planned entry reference. If no
  completed candle touches entry within `max_entry_bars`, the trade remains
  `WAITING_FOR_ENTRY` (no fabricated entry).
- **Exit rule** (reuses the Sprint 11W touch semantics) — once OPEN, completed
  candles strictly after the entry candle are watched for stop / target touches.
  Same-candle both-touch → `BOTH_TOUCHED` (ambiguous; a winner / loser is NEVER
  manufactured; `realized_r` / `realized_pnl` are `None`). Neither within
  `max_holding_bars` → `EXPIRED` (mark-to-close). `MANUAL_CLOSE` is a human action.
- **P&L / R accounting** (Decimal) — `risk = abs(entry - stop)`; LONG
  `realized_r = (exit-entry)/risk`, SHORT `(entry-exit)/risk`;
  `realized_pnl = (exit-entry)*quantity` (LONG) / `(entry-exit)*quantity` (SHORT).
  `realized_r` / `realized_pnl` are `None` for `BOTH_TOUCHED` / `NO_GEOMETRY` /
  unresolved states — never fabricated.
- **Performance** (descriptive) — total / open / closed / wins / losses / ambiguous
  / expired / win rate / total + average + median realized R / profit factor /
  total realized P&L, plus breakdowns by instrument / direction / decision /
  setup type / timeframe. `BOTH_TOUCHED` is excluded from win/loss + R;
  `NO_GEOMETRY` is excluded from R/P&L. No statistical significance / probability /
  confidence score is claimed.
- **Decision vs result** — the journal STRICTLY distinguishes the SYSTEM DECISION
  from the PAPER-TRADE RESULT: a `QUALIFIED` decision that resulted in a `LOSS`
  does NOT become `REJECTED`, and a `LOSS` never implies the decision was wrong.
  The system records what happened; it never retroactively rewrites the decision.
- **Persistence** — paper trades are persisted as schema-versioned JSON files
  (one per trade, atomic writes) in `./paper_trades` (overridable via
  `DASHBOARD_PAPER_TRADE_DIR`); they survive page refreshes / process restarts.
  No `pickle` / `eval` / `exec`; safe-id validation prevents path traversal;
  corrupted records fail safely.
- **No broker / no real money** — there is absolutely no broker API, no order
  placement, no real-money execution, no authentication tokens, no live positions.
  Paper trading is PAPER ONLY.
- **No-look-ahead** — the tracker inspects ONLY completed candles with
  `timestamp <= reference_now`; forming / future-dated candles are excluded. The
  Sprint 11W `OutcomeEvaluator` and the `HistoricalEvaluationPipeline` are NEVER
  invoked to determine current paper-trade state (the touch logic is implemented
  directly in the paper-trading engine). A previously-resolved (terminal) paper
  trade is never altered by future candles.

### Paper trading operations (Product Phase 5 operational increment)

The workstation has a **Paper Trading Operations** section (`GET /workstation`) and
a `POST /api/paper-trading/run-once` endpoint that turn the paper-trading capability
into a controlled, deterministic, real / near-live OPERATIONAL workflow. One
`run_once()` observation cycle observes the configured watchlist through the
existing Product Phase 1 provider, runs the existing analysis at completed-candle
boundaries, creates paper trades for eligible existing opportunities, tracks
already-open / waiting trades on subsequent completed candles chronologically, and
persists state through the existing journal.

```bash
# Run ONE deterministic paper-trading observation cycle over the watchlist:
curl -X POST "http://127.0.0.1:8000/api/paper-trading/run-once?instruments=NIFTY,RELIANCE&timeframe=15m&account_capital=100000&risk_percent=1"
```

- **Orchestration only** — the operations layer (`PaperTradingOperations.run_once`)
  implements NO market analysis, NO decision logic, NO prediction, NO probability,
  NO broker, NO BUY/SELL/ENTER/EXIT/HOLD recommendation. It reuses the existing
  provider + `DashboardAnalysisService.analyze` + the existing `TradePlanningEngine`
  + the existing `PaperTradingEngine` create/track lifecycle + the existing
  `PaperTradeStore`.
- **Completed-candle only** — new paper trades are created ONLY from the latest
  COMPLETED setup candle when the existing opportunity is `READY_FOR_REVIEW`
  (eligible + complete geometry + QUALIFIED/PREFERRED). The forming candle never
  creates / changes / closes a trade; future-dated candles are rejected. No
  `NO_OPPORTUNITY` / `INVALID` / `TRADE_GEOMETRY_UNAVAILABLE` opportunity is ever
  paper-traded.
- **Duplicate prevention** — repeated cycles against the same completed candle do
  NOT create duplicate trades (deterministic trade id + `created_at` = evaluation
  timestamp); duplicates are reported as `DUPLICATE` / `ALREADY_EXISTS`.
- **One-candle-at-a-time / chronological** — if multiple previously-unseen
  completed candles arrive after a downtime/restart, they are processed in
  chronological order so an intervening target is never skipped.
- **Restart / recovery** — on restart the operations layer loads existing
  WAITING_FOR_ENTRY / OPEN trades from the journal and continues tracking them
  without duplicating or resetting state (status / entry / exit / realized R /
  trade id all preserved).
- **Failure isolation** — one instrument failure (provider timeout / unsupported
  instrument / empty data / malformed candle / raised exception) never aborts the
  cycle; the failure is surfaced as an error and no trade is fabricated.
- **Operational status** — `READY` / `STALE` / `NO_DATA` / `ERROR` / `NOT_READY`
  (a SEPARATE concern from `ProviderStatus` and `FreshnessState`; never collapsed
  into one score).
- **Decision / geometry / plan preservation** — the existing decision
  classification is reused VERBATIM (never renamed to BUY/SELL, never
  upgraded/downgraded); trade geometry is reused VERBATIM; the trade plan is
  reused VERBATIM; a paper-trade RESULT never rewrites the original system
  decision; Target 2 remains unsupported.
- **No-look-ahead** — `run_once()` accepts NO `future` / `future_candles`
  argument; never calls `OutcomeEvaluator.evaluate`; never runs
  `HistoricalEvaluationPipeline.evaluate`. Proven by patch-to-raise tests.
- **Manual refresh first** — there is NO background polling / NO WebSocket
  streaming. The deterministic `run_once()` is the core API; the UI offers a
  manual "Run Paper Trading Cycle" action.
- **Paper only** — no real order is placed anywhere; no broker API; no
  credentials; no live execution. Paper-trade results are observational
  validation and do not guarantee future performance or constitute financial
  advice.

## Historical market data foundation (Product Phase 6A)

The historical-data foundation (`engine/data/historical_*`) is the first
increment of the historical-market-intelligence groundwork: a reliable,
deterministic, validated historical OHLCV data layer that will later support
historical setup research and evidence generation (Product Phase 6B). It is
**DATA FOUNDATION ONLY** — it does NOT call the decision engine, does NOT
generate trade candidates or decisions, does NOT create paper trades, and does
NOT compute historical "evidence" (win rate / average R / profit factor /
expected return / setup success probability).

```bash
# Ingest a bounded historical window (deterministic local provider by default):
python scripts/ingest_historical_data.py \
    --instrument NIFTY --timeframe 1D --start 2024-01-01 --end 2024-12-31
```

- **Clean architecture** — Historical Provider → Historical Market Data Service
  → Canonical OHLCV → Validation / normalisation → Dataset persistence →
  (future research/evidence layer). The provider is replaceable behind the
  `HistoricalMarketDataProvider` protocol; the service depends only on the
  protocol.
- **Providers** — `local-deterministic` (default; clearly-labelled synthetic
  series derived from a hash of instrument+timeframe; no network / no API key),
  `in-memory-import` (caller-supplied records for tests / imports), an
  optional `yahoo-historical` adapter (reuses the existing
  `YahooFinanceProvider.get_history`; graceful when yfinance/pandas are
  unavailable), and an optional `upstox-historical` adapter (real-HTTP Upstox
  V3 Historical Candle API; requires the `UPSTOX_ANALYTICS_TOKEN` env var;
  15m intraday only; intraday ranges are split into bounded monthly calendar
  chunks; timestamps normalized to UTC). Yahoo remains the LIVE/near-live
  provider; Upstox is an additive historical provider. Enable an Upstox
  one-month live check with:
  ```bash
  python scripts/verify_upstox_live.py   # RELIANCE 15m 2022-12-01 -> 2023-01-01
  ```
  See `docs/upstox_historical_provider` for instrument-key resolution,
  monthly chunking, authentication safety and limitations. Ingestion remains
  an explicit operator action — Upstox does NOT automatically download data
  merely because the provider exists.
- **Canonical candle model** — reuses the existing `OHLCVCandle`; timestamps are
  normalized to UTC and naive timestamps are rejected (never silently accepted).
- **Typed request model** — `HistoricalDataRequest` (instrument / timeframe /
  start / end / optional provider / adjusted / metadata / `allow_future_end`).
  Validation: non-empty instrument, timezone-aware timestamps, start < end,
  supported timeframe; a future `end` is rejected unless the caller deliberately
  sets `allow_future_end=True` (controlled imports only).
- **Validation** — explicit detection of duplicate timestamps, out-of-order
  sequences, future-dated candles, naive timestamps, invalid OHLC relationships,
  negative volume, missing required OHLC/timestamp values, unsupported
  instrument/timeframe, empty provider responses, and malformed provider
  responses. Invalid records are rejected — never silently repaired, never
  fabricated.
- **Gap detection** — deterministic classification of `POSSIBLE_MARKET_CLOSURE`
  (weekend / short non-trading window / plausible holiday span) vs
  `UNEXPECTED_GAP`. It is intentionally NOT a full exchange-calendar subsystem;
  missing candles are reported, never synthesized.
- **Persistence** — local filesystem JSON under `./data/historical/` (git-ignored;
  one `candles.json` per instrument+timeframe plus an append-only
  `provenance.jsonl` per ingestion). Ingestion MERGES by candle timestamp so the
  same ingestion run twice creates NO duplicates. Atomic writes; safe-id
  validation; schema-versioned.
- **Provenance** — every ingestion records provider / instrument / timeframe /
  requested window / actual first+last accepted candle / ingestion timestamp /
  received-accepted-rejected counts / validation status (`AVAILABLE` / `EMPTY` /
  `PARTIAL` / `INVALID` / `ERROR`). Data is never claimed complete beyond that
  status.
- **Research universe** — the default instrument allow-list (NIFTY / RELIANCE /
  TCS / HDFCBANK / ICICIBANK) is a configurable `ResearchUniverse`, not provider
  hard-coding; instruments can be added without touching the ingestion engine.
- **Look-ahead protection** — ingestion rejects future-dated candles relative to
  the caller's reference "now"; `load_historical(..., evaluation_time=T)`
  returns only candles `<= T`; the public API accepts no hidden future-candle
  parameter; `OutcomeEvaluator` and `HistoricalEvaluationPipeline` are never
  called during ingestion (patch-to-raise tests prove it).
- **Dashboard status surface** — `GET /historical-data` (HTML) and
  `GET /api/historical-data` (JSON) list stored datasets with provider / status /
  record count / first+last timestamp. The page deliberately shows NO win rates
  or trade recommendations — Phase 6A does not calculate them.
- **LIMITATIONS** — historical evidence / research is a FUTURE phase (6B); the
  default provider is a clearly-labelled deterministic synthetic series (wire a
  real vendor behind the same abstraction when configured); gap detection is a
  simple deterministic heuristic, not an exchange calendar.

Roadmap:

- Product Phase 6A — HISTORICAL MARKET DATA FOUNDATION — COMPLETE
- Product Phase 6B — HISTORICAL RESEARCH CORPUS — FUTURE (not started)

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

The dashboard (including the live trading workstation, the trade-plan /
risk-planning layer, and the paper-trading layer) is descriptive/contextual. It
does **not** guarantee future performance, does **not** predict winning trades,
does **not** claim high accuracy or guaranteed signals, and does **not** imply a
"profitable strategy". It has no broker integration, no order placement, no live
execution, no autonomous trading, and no portfolio management. The trade-plan
layer performs a deterministic position-sizing calculation around the existing
trade geometry only — it does **not** generate BUY/SELL signals and is not a
prediction. The paper-trading layer records observational validation only — it
places no real order, never rewrites the original system decision, and is not a
trading recommendation. There is no background polling / WebSocket streaming /
autonomous trading. Broker integration / real-money order execution are
intentionally out of scope.

See `AGENTS.md` for the full architecture, the dashboard section, API routes,
no-look-ahead guarantees, trade-geometry semantics and limitations.
