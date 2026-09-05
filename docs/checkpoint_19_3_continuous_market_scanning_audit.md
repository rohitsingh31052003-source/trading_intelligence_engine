# Checkpoint 19.3 — Continuous Market Scanning (Audit)

Status: **COMPLETE / PASS**

Repository: `/workspace/project/trading_intelligence_engine`
Checkpoint 19.3 of the fixed Checkpoint 19 roadmap (NIFTY Top 200 →
Market Data → Continuous Scanner → Multi-Timeframe → Setup Intelligence
→ Trade Plan → Ranking → User Alert → User → Manual Execution).
**BROKER EXECUTION REMAINS COMPLETELY DEFERRED THROUGHOUT CHECKPOINT 19.x.**

---

## 1. Objective

Build the foundation for **CONTINUOUS MARKET SCANNING** across the
validated NIFTY Top 200 universe: repeatedly observe the market and
produce **deterministic scan-cycle results** that describe the current
**market-data state** of the universe.

> "At each scan cycle, what is the current market-data state of the
> NIFTY Top 200 universe?"

19.3 is about **continuous observation**. It does NOT decide which
stocks are good trades (19.5+), does NOT add multi-timeframe
intelligence (19.4), does NOT track setups (19.6), does NOT alert users
(19.7), and does NOT add the full reliability/recovery framework (19.8).

## 2. Scope

### In scope
- Scanner orchestration: a deterministic `ContinuousScanner` +
  `ContinuousScannerEngine`.
- Scan cycles: per-cycle identity, timestamps, universe/attempted/
  successful/unavailable/failed counts and duration.
- Repeated / incremental scan cycles with a configurable interval.
- Universe-wide scanning over the FROZEN 19.1 NIFTY Top 200 universe.
- Per-symbol scan results (explicit, never silently dropped).
- Deterministic result ordering (canonical universe order).
- Handling of symbols with unavailable data.
- Graceful interruption (clean stop, no overlap, no orphaned workers).
- Basic scanner lifecycle state (STOPPED / RUNNING / SCANNING /
  STOPPING / WAITING / SKIPPED-cycle outcomes).
- Reuse of 19.2 market session behavior for session awareness.
- Deterministic offline testing (injected clock + waiter + fixtures).
- Simple operator CLI (`scripts/scan_market.py`) with a deterministic
  `--once` mode.

### Out of scope (MUST NOT)
- Trade setup detection, technical signal generation, setup scoring,
  setup ranking.
- Multi-timeframe intelligence / confluence.
- Trade plans, entry/stop/target calculation.
- Setup lifecycle.
- Alerts / notifications.
- Broker execution, order placement, positions, portfolio management,
  automated trading.
- The full 19.8 reliability/recovery/observability framework.
- Forward-testing framework.

## 3. Relationship to Checkpoint 19.1 (frozen)

The scanner consumes the FROZEN 19.1 universe boundaries:

- `engine.config.nifty200_manifest` → canonical 200 constituent symbols
  (`NIFTY200_SYMBOLS`).
- `engine.config.universe_boundary.UniverseBuilder.nifty200()`
  → `DEFAULT_NIFTY200_UNIVERSE` (200 stocks + the separate `NIFTY`
  benchmark index; `instrument_count == 201` when the benchmark is
  included, `200` when only constituents are scanned).
- Verified symbol resolution (Yahoo `.NS` maps, verified Upstox keys)
  is unchanged and untouched.

A scan cycle accepts a `UniverseDefinition`, a plain sequence of
canonical instruments, or a `ScanOnceRequest` and **normalizes to a
deterministic, de-duplicated, sorted instrument list** before the
coverage layer is invoked. No second universe definition was created.

## 4. Relationship to Checkpoint 19.2 (frozen)

The scanner consumes the FROZEN 19.2 canonical intraday layer **by
composition** — it does not duplicate, bypass, or replace it:

- `dashboard.intraday_coverage.IntradayCoverageEngine` is the single
  acquisition path (capability gating → provider fetch → completed-candle
  boundary → canonical `OHLCVCandle` validation → session-aware
  freshness → per-instrument coverage).
- The per-symbol status vocabulary is **reused verbatim**:
  `engine.models.intraday_coverage.IntradayCoverageStatus`
  (SUPPORTED / UNSUPPORTED_INSTRUMENT / UNSUPPORTED_TIMEFRAME /
  TEMPORARILY_UNAVAILABLE / PROVIDER_ERROR / INVALID_RESPONSE /
  NO_DATA / EMPTY / STALE / VALID_WITH_GAPS / VALID / NOT_TESTED).
- The scanner never instantiates a provider itself; the provider lives
  inside the `IntradayCoverageEngine` supplied by the caller (with a
  `build()` convenience that never fabricates live coverage).

## 5. Existing scanner / background architecture (audited)

Findings from the repository audit:

1. NO in-process continuous scanner / polling loop / scheduler / worker
   loop existed before 19.3.
2. The frozen Sprint 11U `MarketScanner` / `MarketScanResult`
   (`engine/intelligence/market_scanner.py`) is trade-opportunity
   intelligence (SS-B‑it already computes MTF alignment, decisions,
   opportunities, ranking). It was deliberately NOT reused or modified —
   19.3 is market-data-state observation only.
3. The only "repeated" process in the repo is the external Windows Task
   Scheduler invoking the paper-trading operation CLI every 15 minutes —
   a paper-trading concern, unrelated to 19.3 (and untouched).
4. `IntradayCoverageEngine.assess_universe` already repeated per-symbol
   acquisition with failure isolation — the natural base for a scan
   cycle. The scanner wraps it (no duplicate acquisition logic).
5. History-research loops (corpus / setup research / live paper
   validation) are unrelated domains (research/validation), not reused
   by the scanner.
6. No scanner component is coupled to trading signals, trade planning,
   execution, or broker APIs (verified by import AST checks in tests).

So 19.3 introduces the first IN-PROCESS continuous scanning loop, on
top of the existing canonical data layer.

## 6. Market-data integration assessment

- The scanner accesses market data ONLY through
  `IntradayCoverageEngine.assess_universe(…, reference_now=…)`.
- The evaluation instant is explicitly threaded as `reference_now`
  (aware UTC); no wall clock is read inside the engine.
- The 19.2 completed-candle boundary and freshness/gap classification
  are preserved unchanged (the scanner adds no new market-data rules).
- Provider-specific details (Yahoo/Upstox/fixture) stay inside the
  provider; the scanner is provider-agnostic (tests use the scripted
  fake + fixture providers).

## 7. Universe integration assessment

- A scan cycle accepts `UniverseDefinition | Sequence[str] |
  ScanOnceRequest`.
- Instruments are normalized (strip + upper), de-duplicated and sorted
  → deterministic per-symbol processing order regardless of caller order.
- The benchmark index composing (`("NIFTY",) + symbols`) is supported
  (201-instrument market universe) without conflating the benchmark with
  a constituent.
- Unknown/invented instruments are passed to coverage, which classifies
  them as `UNSUPPORTED_INSTRUMENT` — the scanner explicitly represents
  them (never silently drops).

## 8. Scan-cycle design

Model: `engine.models.continuous_scan.MarketScanCycleResult`
(frozen+slots, deterministic `cycle_id = "cycle-" + sha256[:16]` over
canonical identity that includes `reference_now` + sorted universe).

Metadata (`ScanCycleMetadata`):
- `cycle_started_at` / `cycle_ended_at` (aware UTC, from the caller
  clock / post-cycle clock).
- `duration_seconds`.
- `requested_universe_size`, `attempted_instrument_count`,
  `successful_instrument_count`, `unavailable_instrument_count`,
  `failed_instrument_count`.
- Count invariant enforced in `__post_init__`:
  `successful + unavailable + failed == attempted` (when a cycle ran),
  with the all-zero/UNAVAILABLE edge case handled explicitly.

Cycle-level status (`MarketScanStatus`):
- `FULL_SUCCESS` — every symbol VALID.
- `PARTIAL_SUCCESS` — cycle completed but ≥1 symbol requires attention
  (STALE / EMPTY / NO_DATA / PROVIDER_ERROR / UNSUPPORTED_INSTRUMENT /
  TEMPORARILY_UNAVAILABLE / INVALID_RESPONSE / VALID_WITH_GAPS / …).
- `UNAVAILABLE` — no symbols requested (empty universe).
- `COMPLETE_FAILURE` — the cycle could not be produced (coverage
  raised); empty results + descriptive error.
- `SKIPPED` — an attempted cycle was refused under the no-overlap
  policy (manual scan while a cycle is running).

A scan result describes MARKET STATE / DATA AVAILABILITY — never a
trade recommendation.


## 9. Per-symbol result design

Model: `engine.models.continuous_scan.PerSymbolScanResult` (frozen+slots).

Each universe constituent has an explicit result; nothing is silently
dropped. Fields:
- `instrument` (canonical upper name) + `sort_key` (deterministic
  ordering).
- `status` (reused `IntradayCoverageStatus`) — every 19.2 state maps
  through without loss.
- `coverage`: the reused `IntradayInstrumentCoverage` object (by
  reference; never recomputed).
- `available` / `fresh` / `needs_attention` — derived presentation
  mirrors of the reused coverage state.
- `error`: a safe, short failure string for `PROVIDER_ERROR` /
  `INVALID_RESPONSE` (no exception objects, no credentials).
- `latest_price`: optional scalar market state populated by an injected
  `PriceSource` (e.g. `float(candles[-1].close)`); `None` when no price
  source is attached. This is a plain price observation for display, not
  a signal.

Statuses are 19.2-verbatim: unsupported, stale, provider-error, empty,
no-data, temporarily-unavailable, invalid-response, valid-with-gaps,
valid.

## 10. Deterministic ordering

- Per-symbol results are sorted by canonical instrument name (the 19.1
  manifest order). Caller/input/provider response order can never affect
  the externally visible result order.
- Cycle identity is deterministic: identical (universe, timeframe,
  `reference_now`, source) → identical `cycle_id`.
- Deterministic ordering is verified by tests and by the demo.

## 11. Scheduling / polling design

`ContinuousScanner` provides two execution models with the SAME
`run_cycle` core:

1. **One-shot / manual**: `scan_once(reference_now=…)` — a single
   deterministic cycle; the CLI's `--once` path.
2. **Continuous**:
   - `run(cycles=N | None)` — blocking loop with an injected
     `clock` and `waiter` (default `time.sleep`); bounded by the
     `max_cycles` config cap when `cycles` is `None`.
   - `start()` / `stop()` / `join()` — background daemon-thread worker,
     deterministic lifecycle (see §17).

Injection points (per 19.3 DI guidance, only where meaningful):
- `clock: Callable[[], datetime]` for `reference_now` + cycle
  timestamps.
- `waiter: Callable[[float], None]` in place of `time.sleep`.
- the coverage engine (provider-isolated) and the universe source.

No new scheduling framework, no cron, no asyncio scheduler — the fixed
orbit range was satisfied with the simplest loop that fits the project.

## 12. Scan-interval behavior

- `ContinuousScanConfig.scan_interval_seconds` configures the spacing
  BETWEEN cycles. Default `300` (5 min) — a conservative default so an
  operator must deliberately lower it; documented rationale: respect the
  provider-rate/load limits established by 19.2, avoid aggressive
  polling. No production interval is hard-coded in scanner logic.
- Tests use injected instant waiters + fake clocks — they never sleep in
  real time (verified: `time.sleep` only appears in two deliberate
  provider-slow-down tests with ≤0.05s real sleeps, still
  deterministic).
- Faster scanning is NOT assumed better; no minimum interval assertion
  exists beyond the config validation (interval must be ≥ 0).

## 13. No-overlap prevention

Chosen policy (documented): **do not start a new cycle while the
previous cycle is still running** (simple deterministic policy; the full
19.8 concurrency/recovery framework is explicitly NOT built here).

Mechanism:
- In the loop: the `_busy` flag is set before each cycle and cleared in
  a `finally`; if a scheduled tick finds `_busy`, the scheduled cycle is
  recorded as `SKIPPED` (no-overlap policy), the schedule continues, and
  the interval is re-anchored to that tick — no "catch-up storm".
- For manual `scan_once` during a running cycle: returns an explicit
  `SKIPPED` result immediately (never executes concurrently).
- The engine itself is single-threaded and stateless across cycles;
  the lock guards the scanner bookkeeping, not the acquisition.

Slow provider tests prove: a provider slower than the interval never
causes concurrent cycles; distinct reference times per cycle.

## 14. Failure isolation

- Each symbol is processed independently through
  `IntradayCoverageEngine.assess_instrument` semantics inside
  `assess_universe`; a raised symbol-level exception is caught and
  classified as `PROVIDER_ERROR` (per-symbol, safe message), and the
  cycle continues.
- `200 requested → 198 valid + 1 stale + 1 provider error` still yields
  a complete `MarketScanCycleResult` with all 200 per-symbol results.
- No fake market data is fabricated for failed symbols; no silent
  indefinite retry loop exists (retry/backoff is deferred to 19.8).
- Only a cycle-level (universe-wide) coverage failure yields
  `COMPLETE_FAILURE`.

## 15. Partial / complete failure handling

- `FULL_SUCCESS` ⟺ every symbol `VALID`.
- `PARTIAL_SUCCESS` ⟺ cycle completed with ≥1 symbol needing attention;
  never reported as FULL_SUCCESS, failed symbols never discarded.
- `UNAVAILABLE` ⟺ empty universe.
- `COMPLETE_FAILURE` ⟺ cycle could not be produced; `results == ()` and
  `error` carries the safe detail.
- `SKIPPED` ⟺ no-overlap refusal; distinct from all above.

## 16. Market-session behavior

- Reused 19.2 session helpers (`engine.data.market_session`): the cycle
  attaches `market_session` (`PRE_OPEN / OPEN / POST_CLOSE / WEEKEND /
  UNKNOWN`) plus `seconds_until_next_open`.
- Session awareness does NOT gate scanning (a weekend scan still
  reports the data state — honestly STALE/UNAVAILABLE), it only
  classifies the observed instant. 19.2's documented holiday limitation
  is preserved unchanged; no exchange-holiday infrastructure was added.

## 17. Scanner state / lifecycle

`ScannerState` (project-convention-compatible, minimal):
`STOPPED → RUNNING → SCANNING → RUNNING → STOPPING → STOPPED`, with
`WAITING` (loop sleeping between cycles) and `STOPPED` as the clean
terminal state. `ScannerState.FAILED` exists in the enum for 19.8 and is
never entered by 19.3.

Lifecycle methods:
- `start()` — idempotent; resets the cycle list + schedule on restart so
  a fresh run begins immediately.
- `stop()` — sets the stop flag; the in-flight cycle finishes
  (terminated cycles are consistent), no new cycle starts. Idempotent.
- `join()` — joins the worker thread.
- `run(cycles=…)` — blocking bounded loop; sets `STOPPED` on exit.
- `state()` / `results` / `cycle_count`.

No orphaned worker threads after stop (verified by test).

## 18. Graceful-stop behavior

- Stop between cycles: worker exits cleanly at the next loop check
  (no half-written cycle result).
- Stop mid-cycle: the in-flight fetch completes; `_busy` is cleared in
  `finally`; the completed result is appended; the loop exits at the next
  check.
- Duplicate `stop()` is idempotent.
- Start/stop/restart is deterministic: a restarted scanner resets its
  schedule and cycle list (no duplicate worker, no orphan state).

## 19. Offline testing strategy

- All automated tests are offline + deterministic:
  - fixture providers (`FixtureDataProvider`, 4 instruments, 15m) — no
    network, deterministic data;
  - scripted fake providers (per-symbol statuses / raised exceptions /
    controlled delays) — no network;
  - fake clock + instant waiter — no real sleeps for interval logic;
  - real `time.sleep` ONLY in the two deliberate slow/blocking-provider
    tests (≤0.05 s per call, result assertions are structural and
    deterministic).
- No Yahoo/Upstox credentials, no internet, no market hours dependency.
- Live scanning remains an explicit operator path (`--provider yahoo`),
  never a test dependency, and no claim of live 200-stock continuous
  coverage is made.

## 20. CLI / operator behavior

`scripts/scan_market.py` — thin operator-facing entry point:

- `--provider fixture|yahoo` (default `fixture`; OPT-IN live).
- `--timeframe` (default `15m`; canonicalized, unsupported rejected).
- `--instruments` (comma/space list; default = NIFTY Top 200 + NIFTY).
- `--cycles N` (default 1) and `--interval SECONDS` (default 900 s note:
  a conservative no-op for a single cycle; used for multi-cycle runs).
- `--once` (deterministic single-cycle mode, preferred for diagnostics).
- `--reference-now` (aware ISO; default = wall clock for live use).
- `--json` (pure machine-readable JSON on stdout, no banner).
- Exit codes: 0 = cycle executed (honest findings reported), 2 = bad
  args.
- Banner: `MARKET-DATA SCANNING ONLY — no prediction, no setups, no
  ranking, no broker execution.`
- No credentials are required for the normal (fixture) path; the Yahoo
  path is transient and bounded by the 19.2 retention windows.

## 21. Changes implemented

Files created (all ADDITIVE):
- `src/engine/models/continuous_scan.py` — 19.3 domain models
  (`MarketScanStatus`, `ScannerState`, `ScanSourceKind`,
  `ContinuousScanConfig`, `ScanCycleMetadata`, `PerSymbolScanResult`,
  `MarketScanCycleResult`, `ScanOnceRequest`).
- `src/engine/reporting/continuous_scan.py` — `MarketScanCycleFormatter`
  (returns str; no print; disclaimer).
- `src/dashboard/continuous_scanner.py` — `ContinuousScannerEngine`
  (run_cycle over `IntradayCoverageEngine`) + `ContinuousScanner`
  (lifecycle / loop / no-overlap / clock + waiter injection /
  `PriceSource`).
- `scripts/scan_market.py` — operator CLI.
- `scripts/test_checkpoint_19_3.py` — demo (18 PASS).
- `tests/test_continuous_scanner.py` — 43 deterministic tests.
- `tests/test_scan_market_cli.py` — 10 CLI tests.
- `docs/checkpoint_19_3_continuous_market_scanning_audit.md` — this
  document.

Files modified:
- NONE in the frozen 19.1 / 19.2 layer or any other pre-19.3 source.
  (Only this repo's `AGENTS.md`, appended per convention.)

## 22. Tests added

- `tests/test_continuous_scanner.py` — 43 tests across 10 areas:
  A) universe acceptance (19.1 default #200, benchmark #201, plain
  sequence, `ScanOnceRequest`, sorting);
  B) single-cycle expected-universe / every-symbol-present /
  deterministic ordering / per-symbol data preservation / metadata /
  latest-price with and without `PriceSource`;
  C) explicit statuses: unsupported, stale, provider-error, empty,
  one-failure-does-not-kill-the-cycle;
  D) FULL vs PARTIAL vs UNAVAILABLE vs COMPLETE_FAILURE;
  E) cycle identity determinism;
  F) continuous loop: 3 independent cycles / interval respected by the
  clock / configurable interval / max_cycles cap / state during run;
  G) no-overlap: manual-while-busy → SKIPPED, slow-provider never
  overlaps;
  H) graceful stop / duplicate-stop / restart / no-orphaned worker;
  I) market-session behavior (OPEN, WEEKEND);
  J) boundaries: no setup/score/rank/entry/stop/target field names, no
  broker/order/execution field names, no network/requests/httpx/urllib/
  socket/broker imports in the scanner module, cycle never directional,
  independent repeated cycles, pipeline baseline importable.
- `tests/test_scan_market_cli.py` — 10 tests: fixture `--once` runs,
  Top-200 default universe label, pure-JSON mode, deterministic JSON
  ordering, multi-cycle mode, bad args exit 2, no credentials, no broker/
  prediction language, repeated-run determinism.

## 23. Tests executed and results

- Focused 19.3 suite: `tests/test_continuous_scanner.py` + `tests/
  test_scan_market_cli.py` → **53 passed** (43 + 10).
- 19.1 universe regression: `tests/test_nifty200_universe.py` → passed.
- 19.2 market-data/coverage regression: `tests/test_market_session.py` + `tests/
  test_intraday_coverage.py` + `tests/test_intraday_coverage_yahoo.py` +
  `tests/test_intraday_coverage_cli.py` → passed.
- Combined focused + 19.1 + 19.2: **200 passed**.
- Dashboard/provider/data regression: `tests/test_dashboard.py`,
  `tests/test_live_data_integration.py`, `tests/test_watchlist_scanner.py`,
  `tests/test_workstation.py`, `tests/test_yahoo_range_fix.py`,
  `tests/test_upstox_historical.py` → **480 passed**.
- Full suite: **6506 passed / 12 skipped / 2 warnings** (see §23a).

### 23a. Full-suite result

```
python -m pytest -q
→ 6506 passed, 12 skipped, 2 warnings in 128.39s
```

- Previous Checkpoint 19.2 baseline: **6453 passed / 12 skipped / 2 warnings**.
- Delta: **+53 net new tests** (43 scanner + 10 CLI), zero regressions.
- The 12 skips are the pre-existing opt-in real-broker/sandbox tests
  (gated on `CHECKPOINT_17_8_REAL_BROKER` + genuine credentials; they
  never run in normal CI).
- The 2 warnings are the pre-existing third-party deprecations
  (Starlette/httpx testclient, anyio BlockingPortal), unrelated to 19.3.
- Pipeline signal/trade baseline unchanged (signals=4, trades=3).

## 24. Limitations

- The fixture provider serves only 4 instruments; the other 196 are
  HONEST `UNSUPPORTED_INSTRUMENT` per symbol (partial coverage is never
  presented as full).
- Live 200-stock continuous scanning is operator-opt-in (`--provider
  yahoo`), bounded by Yahoo retention/rate limits and market hours;
  NOT claimed as demonstrated.
- Interval pacing uses a real `time.sleep` by default in production; the
  injected clock/waiter is the deterministic testing path.
- The no-overlap policy is a simple `_busy` guard, not a concurrency
  framework (multi-process locking / persistent scan state / supervisor
  behavior is explicitly deferred to 19.8).
- Session-awareness is informational (classification only); there is no
  scheduler gating on market hours.
- Scanner state is in-memory (no persistence); restart recovery is
  deferred to 19.8.
- The `PriceSource` is an optional presentation hook (a plain latest
  close observation); it carries no derived signals.

## 25. Architectural boundary assessment

- The scanner depends ONLY on the frozen canonical layers:
  `UniverseBuilder / NIFTY200_SYMBOLS` (19.1) → `IntradayCoverageEngine`
  + `IntradayCoverageStatus` + session helpers (19.2) → models. Dependency
  direction: `models ← dashboard ← reporting ← scripts`.
- No existing engine/model was modified. No second universe or
  market-data abstraction was created. The frozen Sprint 11U
  `market_scan.py` and all execution/paper-trading/broker artifacts are
  untouched.
- The scanner is provider-agnostic; `ContinuousScannerEngine` contains no
  network/broker imports (AST-verified).

## 26. Confirmation: 19.4–19.9 NOT implemented

- NO multi-timeframe analysis/confluence (19.4 deferred).
- NO setup detection / scoring / ranking / quality (19.5 deferred).
- NO setup lifecycle (19.6 deferred).
- NO user alerts / notifications (19.7 deferred).
- NO reliability/recovery/observability framework beyond the minimal
  loop mechanics (19.8 deferred).
- NO forward-testing (19.9 deferred).
- Scanner lifecycle ("scan cycle state") is implemented; the TRADE
  setup lifecycle is not.

## 27. Broker-execution boundary confirmation

- Zero broker integration added or touched: no order placement, no
  execution adapters invoked, no broker credentials read, no network
  imports in scanner code, no "BUY/SELL/ENTER/EXIT/HOLD" vocabulary in
  scan models or reports (field-name AST checks in tests).
- Live-scanning CLI flags are market-DATA options only (provider /
  timeframe); they can never submit orders.
- **BROKER EXECUTION REMAINS COMPLETELY DEFERRED THROUGHOUT CHECKPOINT
  19.x.**

## 28. Final verdict

**PASS** — the continuous market-scanning foundation is complete:

1. ✅ Consumes the frozen NIFTY Top 200 universe (19.1).
2. ✅ Consumes the canonical 19.2 intraday data layer (never bypassed).
3. ✅ Deterministic scan cycle over the universe.
4. ✅ Every constituent has an explicit per-symbol result.
5. ✅ Single-symbol failures never kill the cycle.
6. ✅ PARTIAL vs FULL vs COMPLETE_FAILURE vs SKIPPED explicit.
7. ✅ Results deterministic + reproducible.
8. ✅ Scan interval configurable.
9. ✅ Repeated cycles produce independent results.
10. ✅ No-overlap enforced (simple policy, documented).
11. ✅ Clean stop; deterministic lifecycle.
12. ✅ Fully offline deterministic testing.
13. ✅ Market-session behavior compatible with 19.2.
14. ✅ Provider details encapsulated.
15–20. ✅ No setup/MTF/lifecycle/alerts/broker intelligence introduced;
    19.1 and 19.2 remain regression-safe.

A scan cycle reports MARKET STATE / DATA AVAILABILITY only — it does
not recommend trades and does not touch execution.