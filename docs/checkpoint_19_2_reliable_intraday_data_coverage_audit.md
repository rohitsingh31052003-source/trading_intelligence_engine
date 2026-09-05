# Checkpoint 19.2 — Reliable Intraday Data Coverage (Audit)

Status: **COMPLETE / PASS**

Repository: `/workspace/project/trading_intelligence_engine`
Checkpoint 19.2 of the fixed Checkpoint 19 roadmap (NIFTY Top 200 →
Market Data → Continuous Scanner → Multi-Timeframe → Setup Intelligence
→ Trade Plan → Ranking → User Alert → User → Manual Execution).
**BROKER EXECUTION REMAINS COMPLETELY DEFERRED THROUGHOUT CHECKPOINT 19.x.**

---

## 1. Objective

Establish and prove a reliable intraday market-data foundation capable of
serving the validated NIFTY Top 200 universe. This checkpoint is about **data
coverage and reliability**, NOT the continuous scanner (19.3). It answers:

> Can the system reliably obtain, normalize, validate, and expose the intraday
> market data required by the future scanner for the NIFTY Top 200?

## 2. Scope

In scope:

* Provider capability discovery (never invented capabilities).
* NIFTY Top 200 instrument coverage classification (supported /
  unsupported / no-data / empty / stale / temporarily unavailable /
  provider-error / invalid-response / not-tested).
* Normalization of provider responses into the canonical `OHLCVCandle`
  representation (never provider-specific leakage into future scanner
  logic).
* Timestamp / timezone correctness (UTC internal, IST display, aware-only
  IO).
* OHLCV validation (revert to canonical `DataValidator` semantics).
* Completeness distinction (complete / partial / missing / empty / stale /
  provider failure) — never silently converting missing data into valid
  data.
* Session-aware freshness (NSE cash-equity 09:15–15:30 IST, lunch pause,
  weekend) — documented thresholds, never magic constants.
* Per-instrument failure isolation (one bad symbol never breaks the
  universe).
* Bounded retry/rate-limit awareness at the provider boundary only (no
  reliability framework here — that is Checkpoint 19.8).
* Provider abstraction dependency: the coverage layer consumes the
  canonical `dashboard.data_provider.DashboardDataProvider` interface, not
  Yahoo / Upstox APIs directly.
* Deterministic diagnostic + reporting mechanism (CLI) that answers the
  data-coverage questions for the Top 200.

Out of scope (NOT implemented):

* The continuous scanner (19.3) — explicitly NOT built.
* Multi-timeframe analysis (19.4), setup intelligence (19.5), setup
  lifecycle (19.6), alerts (19.7), reliability/recovery/observability
  framework (19.8), validation/forward-testing (19.9).
* Any trading signal / setup scoring / ranking / decision logic.
* Any market-data ingestion into the historical corpus (Phase 6B-6D
  remain untouched).
* Any broker / execution integration (Checkpoints 13-18 remain frozen).

## 3. Relationship to Checkpoint 19.1

Checkpoint 19.1 (FROZEN) established the validated NIFTY Top 200 universe
manifest:

* `src/engine/config/nifty200_manifest.py` — `NIFTY200_CONSTITUENTS`
  (200 × symbol/company/ISIN), `NIFTY200_SYMBOLS`, source URL, version
  (`2026-04-22-nse`), CSV SHA-256.
* `src/engine/config/universe_boundary.py` — `UniverseKind`,
  `UniverseDefinition`, `UniverseBuilder` (construction boundary),
  `DEFAULT_NIFTY200_UNIVERSE` (201 = 200 stocks + `NIFTY` benchmark).
* Provider symbol maps: `TOP200_YAHOO_SYMBOLS` (all 200 via `<NSE>.NS`)
  in `dashboard/universe.py` and `engine/data/historical_provider.py`;
  Upstox verified-key map unchanged (RELIANCE / TCS / HDFCBANK /
  ICICIBANK / NIFTY — **never fabricated**).

Checkpoint 19.2 CONSUMES that frozen universe as its input:
`UniverseBuilder.nifty200()` (and `DEFAULT_NIFTY200_UNIVERSE`) is passed
directly into the intraday coverage layer. 19.2 adds **no second
universe** — the manifest from 19.1 is the single source of truth, and the
coverage layer's default universe is exactly `DEFAULT_NIFTY200_UNIVERSE`.

## 4. Existing market-data architecture

Prior to 19.2 the repository already had a rich market-data stack:

* **Live / near-live provider** (`dashboard/data_provider.py`):
  `DashboardDataProvider` protocol
  (`is_timeframe_supported` / `fetch` / `last_updated`),
  `FixtureDataProvider` (deterministic, offline, 4 instruments, 15m/1D),
  `YahooDataProvider` (OPTIONAL live/near-live, reuses
  `YahooFinanceProvider`, symbol map `<NSE>.NS` + `^NSEI`, native
  intervals only, completed-candle boundary via
  `split_completed_candles`, `FreshnessConfig` / `FreshnessState`,
  `ProviderStatus`, graceful failure isolation, `rejected_future_count`).
* **Historical data layer** (`engine/data/`): `historical_provider.py`
  (Yahoo + Upstox historical providers, `HistoricalProviderResponse`),
  `historical_validation.py`, `historical_gaps.py`, `historical_store.py`,
  `historical_service.py`, `historical_times.py` (canonical timeframes),
  `historical_data_availability.py` (Checkpoint 7), `corpus_*`, and the
  Phase 6C-6F research layers. These are **historical** (bounded ranges +
  monthly chunking) — NOT live/intraday scanning.
* **Canonical candle model**: `engine/models/ohlcv.py` `OHLCVCandle`
  (rejects impossible OHLC at construction), `DataValidator.validate_candle`.
* **Canonical timeframes**: `engine/data/historical_times.py`
  `HISTORICAL_TIMEFRAME_SECONDS` (1m/2m/3m/5m/15m/30m/1h/90m/4h/1D),
  `canonical_timeframe`, `timeframe_seconds`, `supported_timeframes`.

19.2 adds a **thin coverage layer** on top of that stack and a NEW
session-aware freshness helper — all additive.

## 5. Existing provider capabilities

| Provider | Type | Instruments | Intraday intervals | Dependencies | Network |
|---|---|---|---|---|---|
| `FixtureDataProvider` | deterministic offline | 4 fixture instruments | 15m only (+1D context) | none | none |
| `YahooDataProvider` | live/near-live (OPT-IN) | any instrument via `<NSE>.NS` pass-through | 1m/2m/5m/15m/30m/60m/1h/90m (+1D/1d) | `yfinance` optional | yes |
| `YahooHistoricalDataProvider` | historical | all 200 via `TOP200_YAHOO_SYMBOLS` | bounded-window historical | `yfinance` optional | yes |
| `UpstoxHistoricalDataProvider` | historical (OPT-IN) | verified keys only (5) | `15m`, `1D` (native V3) | `UPSTOX_ANALYTICS_TOKEN` (historical-data only) | yes |

**Important**: Upstox in this repository is a HISTORICAL DATA PROVIDER only
(no live/intraday Upstox feed, no execution). The live/intraday provider is
Yahoo (OPT-IN). Upstox verified-key policy from 19.1 is preserved — no fake
Upstox keys are ever created.

## 6. Intraday interval support

Canonical intraday timeframes (`INTRADAY_TIMEFRAMES` in
`engine/data/market_session.py`, derived from `HISTORICAL_TIMEFRAME_SECONDS`
excluding `1D`): `1m`, `2m`, `3m`, `5m`, `15m`, `30m`, `1h`, `90m`, `4h`.

Fixture provider supports **15m only**. Yahoo live provider supports 1m/2m/
5m/15m/30m/60m/1h/90m (native intervals; NO resampling/fabrication).
Upstox historical supports 15m + 1D (verified keys only).

The coverage layer grades a single canonical intraday timeframe (default
`15m`, configurable) per assessment. `IntradayCoverageConfig` rejects both
non-canonical labels and `1D` (explicitly non-intraday); aliases are
canonicalized (e.g. `15M` → `15m`, `60m` → `1h`).

## 7. NIFTY Top 200 coverage assessment

The coverage layer accepts the frozen 19.1 universe and classifies every
constituent deterministically. With the default **fixture** provider the
honest result is:

* universe size (stocks): **200** (+ the `NIFTY` benchmark carried
  separately)
* assessed: **200**
* `supported` (capability declaration): **4** (RELIANCE / TCS /
  HDFCBANK / ICICIBANK — the fixture set)
* `with_valid_data`: **4** (all stale in the offline fixture)
* `unsupported_instrument`: **196**
* `no_data` / `empty` / `provider_errors` / `invalid_responses`: **0**
* `coverage_ratio`: **0.02** (2%) — honestly NOT full coverage.

This is the deterministic demonstration that partial coverage never
becomes false full coverage and that the coverage layer is **honest about
provider limits** (the fixture provider does NOT pretend to serve the Top
200).

Live coverage (Yahoo, OPT-IN) is NOT claimed: it requires an explicit
`--provider yahoo` run on the operator's machine with `yfinance` installed,
and genuine live coverage depends on market conditions, Yahoo rate limits,
retention windows, and connectivity. The CLI/demo document this limitation.

## 8. Symbol / instrument resolution assessment

| Identity | Example | Source |
|---|---|---|
| universe instrument (canonical) | `RELIANCE` | 19.1 `UniverseBuilder` (strip + upper) |
| Yahoo symbol | `RELIANCE.NS` | `TOP200_YAHOO_SYMBOLS` (dashboard + engine maps) |
| Upstox instrument key | `NSE_EQ\|INE002A01018` | verified map in `engine/data/historical_provider.py` (17.1-verified policy) |
| provider fetch key | primary provider `resolve_symbol()` | `DashboardDataProvider` / scripted provider |

`IntradayCoverageEngine.symbol_resolutions(instruments)` returns
`IntradaySymbolResolution(instrument, yahoo_symbol, upstox_instrument_key)`
for every constituent, preferring the ACTIVE provider's `resolve_symbol`
when present and falling back to the canonical static Yahoo map (from 19.1)
— never fabricating a mapping. Upstox keys come ONLY from the verified map
(`_default_upstox_instrument_key_map`); unknown → `None`.

## 9. Normalization assessment

Provider responses flow through the existing normalization boundaries:

`provider.fetch(instrument, tf, reference_now=now)` →
`InstrumentSeries` → `split_completed_candles(series.setup_candles, tf, now)`
(completed / forming / rejected-future) → `DataValidator.validate_candle`
per accepted candle → canonical `OHLCVCandle`.

Patterns:

* the coverage layer consumes ONLY the canonical `DashboardDataProvider`
  protocol;
* provider-specific formats (Yahoo `.NS` strings, Upstox keys) are
  confined to provider adapters; the coverage layer never sees them;
* `IntradayCoverageStatus` vocabulary (SUPPORTED /
  UNSUPPORTED_INSTRUMENT / UNSUPPORTED_TIMEFRAME /
  TEMPORARILY_UNAVAILABLE / PROVIDER_ERROR / INVALID_RESPONSE / NO_DATA /
  EMPTY / STALE / VALID_WITH_GAPS / VALID / NOT_TESTED) is the honest
  per-instrument classification the future scanner can consume without
  knowing provider internals.

## 10. Timestamp / timezone assessment

* **Internal representation**: timezone-aware UTC throughout (all candle
  timestamps, reference times).
* **Deterministic representation**: ISO-8601 in diagnostics; datetimes kept
  as `datetime` in models.
* **Chronological ordering**: raw out-of-order timestamps are detected
  (`UNORDERED` issue) and normalized (sorted).
* **Duplicate timestamps**: detected on the RAW provider series
  (`DUPLICATE` issue + `duplicate_count`), first-kept semantics,
  consistent with the existing boundary behavior.
* **Future-dated candles**: rejected by `split_completed_candles`
  (`FUTURE_DATED` issue + `rejected_future_count`, incl. the provider's own
  count) — never used, never look-ahead.
* **Naive datetimes**: in the coverage engine, naive reference times
  default to `datetime.now(UTC)` if not supplied (documented); the session
  helper NEVER silently accepts naive instants (treats them as STALE /
  UNKNOWN — fail-closed).
* **IANA zones**: IST via `zoneinfo.ZoneInfo("Asia/Kolkata")` (stdlib,
  no tzdata dependency needed in the runtime).

## 11. Market-session assessment

New helper `engine/data/market_session.py` documents Indian-market
session boundaries:

* **NSE cash equity**: 09:15–15:30 IST, Monday–Friday.
* `MarketSessionState`: `PRE_OPEN` (< 09:15 IST) / `OPEN`
  (09:15 ≤ t < 15:30) / `POST_CLOSE` (≥ 15:30) / `WEEKEND` (Sat–Sun) /
  `UNKNOWN` (naive input).
* **Lunch pause**: 12:00–13:00 IST is handled by the intraday gap detector
  (a candle before 12:00 and the next at/after 13:00, ≤ 75 minutes, is NOT
  an unexpected gap).
* `seconds_until_next_open`: arithmetic over the IST calendar (explicitly
  does NOT account for exchange holidays — documented simplification).
* Freshness thresholds are EXPLICIT configuration
  (`SessionFreshnessConfig`), never magic constants:
  * OPEN: `open_staleness_multiplier` × candle duration (default 2.0);
  * POST_CLOSE/PRE_OPEN: `closed_staleness_seconds` (default
    `26 * 3600`);
  * WEEKEND: `weekend_staleness_seconds` (default `4 * 24 * 3600`).
* The coverage layer is **look-ahead safe by construction**: the
  reference time is explicit; only candles that CLOSED before it
  (`split_completed_candles`) are used.

## 12. OHLCV validation assessment

The coverage layer re-validates every accepted candle defensively via
`DataValidator.validate_candle` (the canonical model contract): OHLC
consistency (low ≤ open/high, open/close within [low, high]), finite
numerics, volume ≥ 0, aware timestamps. The primary gate is structural —
`OHLCVCandle.__post_init__` rejects impossible OHLC at construction — and
the coverage engine additionally re-checks via the validator so a
provider that bypasses the model cannot smuggle a malformed candle in.
Rejected candles are counted (`invalid_count` → `INVALID_OHLC` issue);
a series with **zero** valid completed candles classifies
`INVALID_RESPONSE` (nothing is fabricated). No silent repair of
financially meaningful data.

## 13. Completeness assessment

The coverage layer distinguishes, per instrument:

| Condition | Status |
|---|---|
| provider declares support + served fresh, contiguous candles | `VALID` |
| fresh candles but unexpected intraday gap(s) present | `VALID_WITH_GAPS` |
| fresh check fails (age > session-aware threshold) | `STALE` |
| candles exist but none closed as of `reference_now` | `NO_DATA` |
| explicit empty response | `EMPTY` |
| provider error / raised exception | `PROVIDER_ERROR` |
| provider not ready | `TEMPORARILY_UNAVAILABLE` |
| instrument not served | `UNSUPPORTED_INSTRUMENT` |
| timeframe not supported | `UNSUPPORTED_TIMEFRAME` |
| all valid candles rejected | `INVALID_RESPONSE` |
| not part of the assessed sample | `NOT_TESTED` |

Missing data is NEVER converted into valid data; partial coverage is
reported with `coverage_ratio < 1.0`.

## 14. Freshness / staleness assessment

* `SessionAwareFreshness`: `CURRENT` / `STALE` / `UNAVAILABLE`,
  computed by `session_aware_freshness(reference_now, latest_completed, tf,
  config)`.
* **During OPEN**: weighted by candle duration × multiplier (15m × 2 =
  30 min stale threshold).
* **After close / pre-open**: fixed closed window (26 h) — a market that
  is legitimately closed does not go stale overnight.
* **Weekend**: fixed weekend window (4 days).
* `IntradayInstrumentCoverage.staleness_seconds` and `data_age_seconds`
  surface the exact threshold used (auditable).
* The fixture provider's offline data is honestly classified `STALE`
  (not "live"): the stale flag is informational for fixtures.

## 15. Error / failure classification

`ProviderStatus` (canonical, existing) maps onto the coverage status:

| `ProviderStatus` | Coverage status |
|---|---|
| `OK` | proceed to data quality grading |
| `EMPTY` | `EMPTY` |
| `UNSUPPORTED` | `UNSUPPORTED_INSTRUMENT` |
| `NOT_READY` | `TEMPORARILY_UNAVAILABLE` |
| `ERROR` + raised exceptions | `PROVIDER_ERROR` |

Additionally: malformed/unknown timeframe → `UNSUPPORTED_TIMEFRAME` (before
any fetch); all-valid-candles-rejected → `INVALID_RESPONSE`; stale →
`STALE`. Provider failures NEVER produce a false success; raw provider
detail is captured in `issues`/`reason`.

## 16. Failure-isolation assessment

`assess_universe` iterates instruments deterministically (sorted) and
ONLY calls `assess_instrument` per instrument. A raised provider exception,
malformed series, or per-symbol failure is caught and classified as that
instrument's `PROVIDER_ERROR`/`UNSUPPORTED_*`/`EMPTY`; the loop
CONTINUES for the remaining constituents. Regression-tested
(`test_one_bad_symbol_does_not_break_universe`,
`test_raised_provider_isolated`).

## 17. Retry / rate-limit assessment

* **Audit**: the existing Yahoo provider performs a single bounded attempt
  per fetch (no retry loop inside), respects Yahoo's per-interval retention
  windows (`YAHOO_INTERVAL_MAX_DAYS`, safety-margined), and maps
  timeouts/failures to `ProviderStatus.ERROR`/`EMPTY`. There is no
  rate-limit counter beyond Yahoo's native behavior.
* **19.2** adds NO retry/backoff machinery: coverage is a one-shot
  diagnostic observation (a bounded reliability framework belongs to
  Checkpoint 19.8). A provider failure is reported as
  `PROVIDER_ERROR` with a deterministic reason; the caller (future
  scanner) decides whether/how to retry.
* The CLI/documentation note that live Yahoo runs are OPT-IN and depend on
  provider rate limits and retention; the deterministic offline path never
  touches the network.

## 18. Provider abstraction assessment

The coverage layer depends ONLY on the canonical
`DashboardDataProvider` protocol (`is_timeframe_supported` /
`supports_instrument` / `resolve_symbol` / `fetch` / `last_updated`).
Provider-specific logic (Yahoo symbol mapping, Upstox keys, native
intervals) stays inside provider adapters. Two providers are covered:

* `FixtureDataProvider` (default, deterministic, offline);
* `YahooDataProvider` (OPT-IN, live/near-live, exercised with an injected
  deterministic fake backend in tests).

`IntradayCoverageEngine.build(provider_name)` is a stateless factory over
`make_provider`; it never constructs a broker provider and never silently
falls back between providers. A future broker-specific adapter (NOT in
this checkpoint) would plug in the same way — but **no broker provider
exists in 19.2**.

## 19. Changes implemented

New files:

* `src/engine/data/market_session.py` — NSE session state, session-aware
  freshness, seconds-until-next-open, `SessionFreshnessConfig` (pure,
  deterministic, stdlib `zoneinfo`).
* `src/engine/models/intraday_coverage.py` — frozen+slots models:
  `IntradayCoverageStatus`, `IntradayCandleIssue`,
  `ProviderCoverageCapability`, `IntradaySymbolResolution`,
  `IntradayCoverageCounts`, `IntradayInstrumentCoverage`,
  `IntradayCoverageReport`.
* `src/dashboard/intraday_coverage.py` — `IntradayCoverageConfig` +
  `IntradayCoverageEngine` (capability discovery, symbol resolution,
  per-instrument classification, universe assessment, failure isolation,
  session-aware gap refinement) + `IntradayCoverageFormatter`.
* `scripts/check_intraday_coverage.py` — operator diagnostic CLI
  (`--provider fixture|yahoo`, `--timeframe`, `--instruments`,
  `--reference-now`, `--json`; exit 0/1/2 convention).
* Tests: `tests/test_market_session.py` (41),
  `tests/test_intraday_coverage.py` (42),
  `tests/test_intraday_coverage_yahoo.py` (8),
  `tests/test_intraday_coverage_cli.py` (8).
* Demo: `scripts/test_checkpoint_19_2.py` (16 checks).

Modified files:

* `src/dashboard/data_provider.py` — added
  `supports_instrument()` to `FixtureDataProvider` (declares the fixture
  set only) and `YahooDataProvider` (declares pass-through support — a
  capability claim, never a data-coverage claim). Additive; no existing
  behavior changed.

No other engine/model was modified. No new trading intelligence. No
second universe. No broker code.

## 20. Tests added

* `tests/test_market_session.py` (41): session state boundaries, IST
  conversion, staleness thresholds, session-aware freshness (CURRENT/STALE
  during OPEN / after close / weekend, naive fail-closed), seconds-until-
  next-open (incl. Fri→Mon), config validation/frozen.
* `tests/test_intraday_coverage.py` (42): NIFTY Top 200 acceptance (200 +
  benchmark), default universe, fixture honest coverage (196 unsupported,
  4 with data, ratio < 1), capability discovery, symbol resolution
  (Yahoo + verified Upstox keys), classification (UNSUPPORTED_INSTRUMENT /
  UNSUPPORTED_TIMEFRAME / EMPTY / PROVIDER_ERROR /
  TEMPORARILY_UNAVAILABLE / INVALID_RESPONSE / VALID / STALE),
  data quality (DUPLICATE / UNORDERED / FUTURE_DATED / UNEXPECTED_GAP /
  VALID_WITH_GAPS / gap-detection-off), failure isolation, partial-vs-full,
  count reconciliation, session + staleness metadata, formatter
  (sections / warning / negative-width rejection), no-broker-imports AST
  check, determinism + input-order independence + model frozen.
* `tests/test_intraday_coverage_yahoo.py` (8): Yahoo capability
  declaration, unsupported timeframe (`3m`), fresh→VALID, stale→STALE,
  empty→EMPTY, provider failure→PROVIDER_ERROR, future-candle rejection
  (boundary + provider count), symbol map used (fake backend, no network).
* `tests/test_intraday_coverage_cli.py` (8): fixture run exit 0 + honest
  counts, custom instruments, pure-JSON mode, bad --reference-now exit 2,
  empty instruments exit 2, unknown provider exit 2, deterministic output.

Total new tests: **99**.

## 21. Tests executed and results

Focused:

```
python -m pytest tests/test_market_session.py tests/test_intraday_coverage.py \
    tests/test_intraday_coverage_yahoo.py tests/test_intraday_coverage_cli.py -q
→ 99 passed
```

Regression (provider / dashboard / historical subsets):

```
python -m pytest tests/test_nifty200_universe.py tests/test_upstox_historical.py \
    tests/test_yahoo_range_fix.py tests/test_live_data_integration.py \
    tests/test_watchlist_scanner.py tests/test_dashboard.py -q
→ 433 passed

python -m pytest tests/test_historical_data_foundation.py \
    tests/test_historical_data_availability.py tests/test_corpus_preparation.py \
    tests/test_corpus_ingestion.py tests/test_corpus_audit.py \
    tests/test_historical_data_consumer.py -q
→ 336 passed
```

Full suite (after the final CLI fix):

```
python -m pytest tests/ -q
→ 6445 passed, 12 skipped, 2 warnings (StarletteDeprecationWarning +
  anyio BlockingPortal deprecation — pre-existing, third-party)
```

Baseline: 6354 passed / 12 skipped (Checkpoint 19.1). **+91 net new
tests** (99 new − 0 removed) with **zero regressions**. Pipeline baseline
unchanged (signals=4, trades=3 — covered by the existing tests that all
pass).

## 22. Live / provider validation results

* **Deterministic offline run** (default): the CLI + demo prove the full
  Top-200 grading pipeline against the fixture provider (honest 2%
  coverage — documented, NOT claimed as live).
* **Yahoo live path**: exercised deterministically with an injected fake
  backend (no network, no credentials) in
  `tests/test_intraday_coverage_yahoo.py`. The real live Yahoo path is a
  documented OPT-IN operator action: `python scripts/check_intraday_coverage.py
  --provider yahoo --instruments <subset>` on a machine with `yfinance`
  installed. **No live validation was performed in this automated run**
  (no live market dependency, no credentials, no rate-limit exposure in
  CI). Coverage of the full 200-instruction live list is therefore
  **NOT claimed**.
* **Upstox**: no live Upstox intraday feed exists in this repository
  (Upstox is historical-only); verified-key policy preserved; no fake
  keys created.

## 23. Limitations

1. `FixtureDataProvider` serves only 4 instruments — the deterministic
   path demonstrates the mechanism, not the full 200 live.
2. Live Yahoo coverage of all 200 depends on `yfinance`, Yahoo retention
   windows (1m ≤ ~6 days, ≤30m ≤ ~58 days, 1h ≤ ~725 days, 1d unlimited),
   rate limits, and market hours; concurrent 200-symbol runs are not
   optimized (sequential) and were not executed in CI.
3. Session-awareness is arithmetic over the IST calendar — no exchange
   holiday calendar is consulted (`seconds_until_next_open` documents
   this simplification).
4. Lunch-pause handling covers the documented 12:00–13:00 IST equity
   window for the intraday gap refinement; exchange-specific nuances
   remain documentation-level.
5. Retry/backoff and a general reliability/observability framework are
   intentionally deferred to Checkpoint 19.8.
6. No streaming/WebSocket feed; polling granularity is provider-bound.
7. Upstox historical data remains outside this checkpoint's scope; no
   live Upstox intraday provider was created.

## 24. Architectural boundary assessment

The intended 19.2 boundary is implemented exactly:

```
NIFTY Top 200 (19.1 manifest)
        ↓
Validated Universe
        ↓
Intraday Market Data Provider        (DashboardDataProvider)
        ↓
Provider Response                    (InstrumentSeries)
        ↓
Normalized Market Data               (OHLCVCandle / DateValidator)
        ↓
Validated Intraday Data              (split_completed_candles)
        ↓
Data Availability / Freshness Status (IntradayCoverageEngine)
        ↓
Future Continuous Scanner (19.3)     (NOT implemented)
```

Engine ↔ dashboard dependency direction preserved (models ← data ←
dashboard). The coverage engine sits in `dashboard/` because it consumes
the dashboard-level `DashboardDataProvider` protocol — the same canon the
future scanner will use. The frozen 19.1 manifest, provider maps, and
historical layers are untouched.

## 25. Broker-execution boundary confirmation

* **ZERO execution / broker code added or invoked.**
* `IntradayCoverageEngine.build` can construct ONLY `fixture` or `yahoo`
  providers via `make_provider` — no broker provider exists.
* AST test (`test_no_broker_imports`) proves the new coverage module
  imports nothing containing `execution` / `broker` / `authorization` /
  `command`.
* No credentials are required or read anywhere in the 19.2 path
  (the historical-only `UPSTOX_ANALYTICS_TOKEN` is never touched by the
  coverage layer; `yfinance` is optional and unauthenticated).
* Checkpoints 13–18 (execution architecture, broker adapter contract,
  reference adapter, Upstox mock adapter, sandbox read-only, execution
  gate) remain FROZEN and untouched; the execution gate remains DISABLED.

## 26. Final verdict

**PASS.** Checkpoint 19.2 successfully establishes a reliable intraday
market-data foundation:

1. ✅ The NIFTY Top 200 universe from 19.1 is accepted by the
   market-data layer (200 stocks + benchmark).
2. ✅ Intraday provider capabilities are explicitly understood
   (fixture 15m; live Yahoo 1m–90m native; no invented capabilities).
3. ✅ Provider-specific data is normalized into the canonical
   `OHLCVCandle` representation (no provider leakage).
4. ✅ Data-quality problems (duplicates, out-of-order, malformed,
   future-dated, gaps, stale) are detectable, not silently accepted.
5. ✅ Data freshness is assessed session-aware (NSE 09:15–15:30 IST,
   documented thresholds).
6. ✅ Missing / unsupported / failed instruments are explicitly
   distinguishable.
7. ✅ One bad instrument does not break the remaining universe.
8. ✅ Provider limitations are documented honestly (fixture 2% coverage;
   Yahoo OPT-IN; Upstox historical-only verified keys).
9. ✅ The future scanner can consume the canonical intraday data layer
   without knowing provider-specific details.
10. ✅ 99 deterministic tests prove the behavior (plus the 16-check demo).
11. ✅ Existing functionality is regression-safe (full suite 6445 passed
    with zero regressions).
12. ✅ Broker execution remains completely untouched.

STOP after Checkpoint 19.2 — the continuous scanner is NOT implemented.