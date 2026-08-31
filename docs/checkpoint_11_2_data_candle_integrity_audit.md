# CHECKPOINT 11.2 — Current-Market Data & Candle Integrity Audit

**Date:** 2026-08-31  
**Status:** AUDIT COMPLETE  
**Verdict:** **PASS WITH LIMITATIONS**

---

## 1. Executive Summary

The current-market data ingestion and candle-integrity boundary feeding the setup-detection subsystem is architecturally sound, well-tested, and correctly implemented. The data path from provider through to the detection layer enforces strict invariants: only validated, completed candles reach the engine; forming candles are excluded; future timestamps are rejected; and the entire pipeline is deterministic given identical inputs and a reference time.

The boundary is enforced at multiple redundant layers (provider normalization → `split_completed_candles` → scanner `_latest_completed_*` helpers), providing defense-in-depth against future-data leakage. Failures are isolated per-symbol and per-candle, never corrupting the entire scan. No silent fabrication of data occurs at any layer.

**Key finding:** The data entering the detection layer is sufficiently correct, deterministic, fresh (relative to configured thresholds), point-in-time safe, and well-defined for current-market setup detection. The architecture already implements the invariants Checkpoint 11.2 audits.

---

## 2. Provider Boundary

### 2.1 Interface

| Component | File | Role |
|-----------|------|------|
| `MarketDataProvider` (ABC) | `src/engine/data/provider.py` | Core abstract interface: `connect()`, `is_available()`, `get_history()`, `get_latest()` |
| `BaseDataProvider` | `src/engine/data/base_provider.py` | Shared implementation: provider name, connection state |
| `DashboardDataProvider` (Protocol) | `src/dashboard/data_provider.py:542` | Dashboard-level contract: `is_timeframe_supported()`, `fetch()`, `last_updated()` |

### 2.2 Concrete Implementations

| Provider | File | Nature |
|----------|------|--------|
| `FixtureDataProvider` | `src/dashboard/data_provider.py:569` | Deterministic local data (default). No network. Reuses `engine.data.historical_fixtures`. |
| `YahooDataProvider` | `src/dashboard/data_provider.py:711` | Optional live/near-live. Wraps `YahooFinanceProvider`. Graceful on any failure. |

### 2.3 Request Parameters

- **Symbol**: Canonical instrument name (e.g. `"NIFTY"`). Mapped to provider symbol inside the provider via `resolve_symbol()` (Yahoo: `YAHOO_SYMBOL_MAP`; unknown names pass through verbatim).
- **Timeframe**: Dashboard labels `"1m"/"3m"/"5m"/"15m"/"30m"/"1h"/"4h"/"1D"`. Mapped to Yahoo intervals directly (no resampling).
- **Lookback**: `lookback_bars` (default 300) + `ENGINE_CONTEXT_BUFFER_BARS` (250). Converted to a recent bounded window, capped per-interval at a safety-margined Yahoo max (`YAHOO_INTERVAL_MAX_DAYS`: 1m=6d, <=30m/90m=58d, 60m/1h=725d, 1D=365*5d).

### 2.4 Timeframe Handling

- Dashboard labels defined in `SUPPORTED_TIMEFRAMES` tuple (`data_provider.py:88`).
- Duration per label in `TIMEFRAME_DURATION_SECONDS` (`data_provider.py:140`), used by `split_completed_candles` for the completed-candle boundary.
- Context (higher) timeframe derived via `_CONTEXT_FALLBACK` mapping (`data_provider.py:105`): 15m → 1D.
- Yahoo-supported intervals: `1m/2m/5m/15m/30m/60m/1h/90m/1D/1d` (native only, no resampling).

### 2.5 Response Parsing

- **YahooFinanceProvider** (`yahoo_provider.py:31`): Calls `yf.download()`, normalizes columns via `DataNormalizer`, drops NaN OHLCV rows, constructs `OHLCVCandle` per row.
- **DataNormalizer** (`normalizer.py:24`): Flattens MultiIndex columns, renames lowercase→title-case, validates required columns present.
- **Timestamp handling**: `YahooFinanceProvider` converts pandas timestamps via `to_pydatetime()` (may return naive timestamps for daily data). `YahooDataProvider._ensure_aware_utc()` normalizes all timestamps to tz-aware UTC before boundary processing.

### 2.6 Normalization

- Column standardization via `DataNormalizer.normalize()`.
- Timestamp normalization to tz-aware UTC via `YahooDataProvider._ensure_aware_utc()` (`data_provider.py:911`).
- Naive timestamps interpreted as UTC (daily bars are exchange-day labels).

### 2.7 Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing optional dependency | `YahooDataProvider._provider = None`, `NOT_READY` status, honest reason |
| Network error / timeout | Caught, `ProviderStatus.ERROR`, reason string, `available=False` |
| Empty response | `ProviderStatus.EMPTY`, `available=False` |
| Unsupported instrument | `ProviderStatus.UNSUPPORTED`, `available=False` |
| Unsupported timeframe | `ProviderStatus.UNSUPPORTED`, `available=False` |
| Malformed candle (per-candle) | Dropped via `DataValidator.validate_candle`, count surfaced in reason |
| All candles invalid | `ProviderStatus.ERROR`, "all N candles were invalid" |
| Provider init failure | `_init_error` captured, `NOT_READY`, never crashes dashboard |

### 2.8 Empty/Malformed Responses

- Empty DataFrame from Yahoo → `ValueError` raised internally → caught → `ProviderStatus.EMPTY`.
- NaN OHLCV rows dropped during parsing (`yahoo_provider.py:59`).
- Per-candle validation in `_fetch_raw` (`data_provider.py:982`): invalid candles dropped, valid ones kept, count reported.

### 2.9 Retries / Timeouts

- **No retries** in the current-market provider layer. Yahoo's `yf.download` has its own internal retry/timeout (yfinance default). The `YahooDataProvider` does not add a custom timeout wrapper around the yfinance call.
- **No explicit socket timeout** configured for the Yahoo provider.

### 2.10 Fallback Behavior

- A failed live provider **NEVER** silently falls back to fixture data in a live request (documented Product Phase 1 rule, `data_provider.py:723`).
- Unknown provider names in `make_provider()` fall back to the fixture provider (`data_provider.py:1157`) — this is the factory default, not a live-request fallback.

### 2.11 Caching

- **No caching** in the `YahooDataProvider`. Each `fetch()` call hits the network.
- **FixtureDataProvider** loads fixtures once at construction (`self._cache`, `data_provider.py:598`). The cache is deterministic and never refreshed (fixtures are static).

---

## 3. InstrumentSeries Audit

### 3.1 What It Represents

`InstrumentSeries` (`data_provider.py:467`) is one instrument's normalized candles for a context + setup timeframe pair, as the existing scanner expects. It is the **output contract** of the provider layer.

### 3.2 Immutability

`@dataclass(frozen=True)` — immutable after construction. All fields are tuples or scalars.

### 3.3 Candle Ordering Guarantees

- `setup_candles` and `context_candles` are tuples of `OHLCVCandle` in **chronological order** (oldest → newest), produced by `split_completed_candles` which sorts by timestamp.

### 3.4 Timestamp Guarantees

- All timestamps in `setup_candles` / `context_candles` are **tz-aware UTC** (normalized by `_ensure_aware_utc` in the Yahoo path; fixtures are UTC by construction).
- `latest_completed_candle_timestamp` is the timestamp of the last completed setup candle — this is the analysis boundary.
- `latest_candle_timestamp` may be a forming candle timestamp (for display metadata only).

### 3.5 Timeframe Guarantees

- `setup_candles` are all the same timeframe (the requested setup timeframe).
- `context_candles` are all the higher/context timeframe.
- Timeframe identity is preserved by the provider and passed through to the scanner via `InstrumentDataset`.

### 3.6 Symbol/Instrument Identity

- `instrument` field carries the canonical instrument name (e.g. `"NIFTY"`), never the provider-specific symbol.

### 3.7 Minimum/Maximum Data Assumptions

- No explicit minimum enforced at the `InstrumentSeries` level. The scanner enforces `min_history` (default 10).
- No maximum. The provider caps the request window via `YAHOO_INTERVAL_MAX_DAYS`.

### 3.8 Duplicate Handling

- `split_completed_candles` de-duplicates by timestamp (keep first) before sorting (`data_provider.py:419-426`).

### 3.9 Missing-Candle Handling

- Missing candles (gaps) are not fabricated. The provider returns what the source provides. Gap detection exists in the historical layer but is not applied in the current-market path.

### 3.10 Empty-Series Behavior

- When no completed setup candles exist: `available=False`, `reason` explains why, `setup_candles=()`, `provider_status` indicates the cause (EMPTY/ERROR/UNSUPPORTED).

### 3.11 Downstream Invariants

Downstream components can rely on these explicit invariants:
1. `setup_candles` contains ONLY completed candles (close time ≤ fetch time).
2. `context_candles` contains ONLY completed candles.
3. The forming candle is NEVER in `setup_candles` / `context_candles` — it is carried separately on `forming_setup_candles`.
4. All timestamps are tz-aware UTC.
5. Candles are chronologically sorted and de-duplicated.
6. `latest_completed_candle_timestamp` is the correct evaluation boundary.

---

## 4. Candle Integrity

### 4.1 OHLCVCandle Model

`src/engine/models/ohlcv.py` — `OHLCVCandle` (`frozen=True, slots=True`):

| Field | Type | Validation |
|-------|------|------------|
| `timestamp` | `datetime` | No explicit validation in `__post_init__` (timezone handled at provider layer) |
| `open` | `float` | `low <= open <= high` |
| `high` | `float` | `high >= low` |
| `low` | `float` | `low <= open`, `low <= close` |
| `close` | `float` | `low <= close <= high` |
| `volume` | `float` | `volume >= 0` |

### 4.2 Timestamp

- **Timezone-aware or naive**: The model accepts both. The provider layer normalizes to tz-aware UTC.
- **Canonical timezone**: UTC.
- **Timestamp precision**: Microsecond (Python `datetime` default). Provider timestamps come from `yf.download()` (minute or daily precision).
- **Chronological ordering**: Enforced by `split_completed_candles` (sort by timestamp).
- **Future timestamps**: Rejected by `split_completed_candles` (`timestamp > reference_now` → `rejected_future`).
- **Duplicate timestamps**: De-duplicated by `split_completed_candles` (keep first).

### 4.3 OHLC Invariant Validation

The system validates:
- `high >= low` (explicit check)
- `low <= open <= high` (explicit check)
- `low <= close <= high` (explicit check)

**NOT validated** (by design):
- `high >= max(open, close)` — implied by the above checks
- `low <= min(open, close)` — implied by the above checks

Invalid values raise `ValueError` during `OHLCVCandle` construction. The `DataValidator.validate_candle()` mirrors these checks for the dataset-level validation path.

### 4.4 Volume

- **Optional**: No. `volume` is a required field (no default).
- **Zero volume permitted**: Yes (`volume >= 0` allows zero).
- **Negative volume handling**: Rejected with `ValueError("Volume cannot be negative.")`.
- **Missing volume handling**: NaN volume rows are dropped during Yahoo parsing (`yahoo_provider.py:59`).
- **Provider-specific interpretation**: Volume is passed through as-is from Yahoo. No adjustment for splits/dividends (raw volume).

---

## 5. Completed/Incomplete Candle Handling

### 5.1 The Boundary Function

`split_completed_candles(candles, timeframe, reference_now, *, duration_seconds=None) -> CandleBoundaryResult` (`data_provider.py:365`).

### 5.2 Classification Logic

```
For each candle (sorted, de-duplicated):
    if timestamp > reference_now:
        → REJECTED_FUTURE (never used)
    elif duration is unknown:
        → COMPLETED (conservative: last candle treated as forming unless strictly older)
    elif timestamp + duration <= reference_now:
        → COMPLETED (close time has passed)
    else:
        → FORMING (open <= now, close > now) — kept for DISPLAY ONLY
```

### 5.3 Which Candles Reach Setup Detection

```
Incoming candles (from provider)
      ↓
split_completed_candles()
      ↓
COMPLETED candles → InstrumentSeries.setup_candles → InstrumentDataset → MarketScanner → Detection
FORMING candle → InstrumentSeries.forming_setup_candle → Dashboard display ONLY (never engine)
REJECTED_FUTURE → InstrumentSeries.rejected_future_count → Metadata/warning ONLY
```

### 5.4 Can a Detector Accidentally Consume an Incomplete Candle?

**No.** The forming candle is excluded at the provider boundary (`split_completed_candles`) and carried separately. The scanner receives only `setup_candles` (completed). The scanner's own `_latest_completed_at_or_before` provides a second layer of protection.

### 5.5 Timeframe Boundary Calculations

- Duration from `TIMEFRAME_DURATION_SECONDS` (`data_provider.py:140`): 15m = 900s, 1D = 86400s, etc.
- Close time = `timestamp + timedelta(seconds=dur)`.
- A candle is completed when `close_time <= reference_now`.

### 5.6 Current Timestamp Usage

- `reference_now` is an **explicit parameter** (not wall-clock) for deterministic testing.
- In production (Yahoo path): `now = reference_now or datetime.now(UTC)` (`data_provider.py:1007`).
- In fixture path: `reference_now` defaults to `_FAR_FUTURE_NOW` (all candles treated as completed) when not supplied (`data_provider.py:658`).

### 5.7 Market-Session Handling

- **No explicit market-session logic** in the current-market path. The provider returns whatever candles Yahoo provides (including pre/post-market if Yahoo includes them).
- The system does not distinguish market-open vs market-close candles.
- This is a **documented limitation**, not a defect.

---

## 6. Future Timestamp Rejection

### 6.1 Where Rejection Occurs

1. **Provider boundary** (`split_completed_candles`, `data_provider.py:434`): `timestamp > reference_now` → `rejected_future`.
2. **Scanner** (`_latest_completed_before`, `market_scanner.py:136`): `timestamp < cutoff` (strict) for HTF context.
3. **Scanner** (`_latest_completed_at_or_before`, `market_scanner.py:161`): `timestamp <= cutoff` for setup.

### 6.2 Reference Clock

- `reference_now` parameter (explicit, deterministic).
- Defaults to `datetime.now(UTC)` in the Yahoo production path.

### 6.3 Timezone Assumptions

- All comparisons are performed on **naive UTC** equivalents (aware timestamps converted via `_naive()` helper in `split_completed_candles`, `data_provider.py:411`).
- Scanner uses `_ensure_utc()` (`market_scanner.py:115`) for consistent comparison.

### 6.4 Equality at the Boundary

- `split_completed_candles`: `timestamp > reference_now` is future; `timestamp <= reference_now` is at-or-before (not future). A candle exactly at `reference_now` is NOT rejected.
- `_latest_completed_at_or_before`: `timestamp <= cutoff` includes the boundary candle.
- `_latest_completed_before`: `timestamp < cutoff` excludes the boundary candle (strictly before).

### 6.5 Provider Clock Differences

- **Not explicitly handled.** The system assumes the provider's clock is sufficiently close to the local clock. A provider returning candles with timestamps slightly ahead of local time could cause those candles to be rejected as future. This is the correct behavior (erring on the side of exclusion).

### 6.6 Silent Drop vs. Error

- Future candles are **silently dropped** from engine input but **counted** in `rejected_future_count` on `InstrumentSeries`. This count surfaces in the dashboard as a data-quality warning. Not silent — reported.

---

## 7. Point-in-Time Safety

### 7.1 Future Candles

Rejected at provider boundary and scanner level (see §6).

### 7.2 Accidental Look-Ahead

- The `DashboardAnalysisService.analyze()` API accepts **no** `future_candles` / `lookahead` argument.
- The service **never** calls `OutcomeEvaluator.evaluate` or `HistoricalEvaluationPipeline.evaluate` during current analysis (regression-tested).

### 7.3 Future Aggregation

- Feature calculations (swings, structure, trend) operate on `candles[:T+1]` slices — the scanner constructs `setup_visible = setup_candles[:setup_idx+1]` and `htf_visible` filtered to `timestamp <= htf_candle.timestamp`.

### 7.4 Rolling-Window Leakage

- No rolling window includes future data. All slices are prefix-based (`[:index+1]`).

### 7.5 Feature Calculations Using Excluded Candles

- The forming candle is excluded from `setup_candles`. The scanner's `setup_visible` is built from `setup_candles[:setup_idx+1]` where `setup_idx` is the index of the latest completed candle. The forming candle (if it exists in the raw data) is not in `setup_candles` at all.

### 7.6 Caching of Future Data

- No caching layer exists that could leak future data. The `FixtureDataProvider._cache` is static and deterministic.

### 7.7 Provider Responses Beyond Permitted Boundary

- Yahoo may return the currently-forming candle as the last row. `split_completed_candles` correctly classifies it as forming and excludes it from `setup_candles`.

---

## 8. Timeframe Integrity

### 8.1 Timeframe Identity

For every current-market series, the quadruple `(symbol, timeframe, timestamp, OHLCV)` is maintained:
- `symbol`: Canonical name on `InstrumentSeries.instrument` / `InstrumentDataset.instrument`.
- `timeframe`: Preserved by the provider (setup vs context). Scanner config carries `context_timeframe` and `setup_timeframe`.
- `timestamp`: On each `OHLCVCandle`.
- `OHLCV`: On each `OHLCVCandle`.

### 8.2 Timeframe Enums/Constants

- `SUPPORTED_TIMEFRAMES` tuple (`data_provider.py:88`): canonical dashboard labels.
- `TIMEFRAME_DURATION_SECONDS` dict (`data_provider.py:140`): duration per label.
- `FIXTURE_TIMEFRAMES` dict (`data_provider.py:96`): fixture-available timeframes.
- `YAHOO_INTERVAL_MAX_DAYS` dict (`data_provider.py:785`): per-interval history caps.

### 8.3 Timeframe Conversion

- No resampling. Dashboard labels map 1:1 to Yahoo intervals (e.g., `"15m"` → `"15m"`).
- `"1D"` is normalized to `"1d"` for Yahoo (`data_provider.py:1038`).

### 8.4 Resampling

- **Not implemented.** The system does not create 15m candles from 5m data or vice versa. Unsupported timeframes are reported as `UNSUPPORTED`, never fabricated.

### 8.5 Provider Timeframe Mapping

- Yahoo: native intervals only (`1m/2m/5m/15m/30m/60m/1h/90m/1D/1d`).
- Fixture: `15M` setup + `1D` context only.

### 8.6 Candle Boundary Alignment

- Candles are aligned to their natural boundaries (e.g., a 15m candle at 09:15 closes at 09:30). The `split_completed_candles` function uses `timestamp + duration` to compute close time, preserving alignment.

### 8.7 15-Minute Timeframe

- 15m is the **default setup timeframe** (`AnalysisRequest.setup_timeframe = "15m"`).
- Duration: 900 seconds (`TIMEFRAME_DURATION_SECONDS["15m"]`).
- Context: 1D (`_CONTEXT_FALLBACK["15m"] = "1D"`).
- Fixture provider: 15M setup + 1D context available.
- Yahoo provider: 15m supported (up to 58 days).

---

## 9. Session/Market-Hours Handling

### 9.1 Current Guarantees

- **No explicit market-calendar infrastructure** in the current-market path.
- The system does not model market open/close, holidays, pre-market/post-market, or overnight gaps.
- Candles are processed as-is from the provider.

### 9.2 What the System Currently Guarantees

- Only completed candles reach the engine (temporal boundary, not session boundary).
- Stale data detection flags old candles (configurable threshold).
- Gap detection exists in the historical layer but is **not applied** in the current-market path.

### 9.3 Pre/Post-Market Data

- If Yahoo includes pre/post-market candles, they are processed identically. No filtering by session.

### 9.4 Overnight Gaps

- Not explicitly handled. The chronological ordering and completed-candle boundary naturally exclude gaps (missing candles are not fabricated).

---

## 10. Data Freshness

### 10.1 Freshness Classification

`classify_freshness()` (`data_provider.py:272`) is a **pure, deterministic** function of:
- `latest_completed_timestamp`
- `reference_now`
- `staleness_seconds` (from `FreshnessConfig`)
- `provider_status`

### 10.2 Freshness States

| State | Condition |
|-------|-----------|
| `UNAVAILABLE` | No completed candle, or provider EMPTY/ERROR/NOT_READY |
| `CURRENT` | Latest completed candle age ≤ staleness threshold |
| `STALE` | Latest completed candle age > staleness threshold |
| `INVALID` | Assigned by provider/boundary when malformed candles encountered |

### 10.3 Staleness Threshold

- `FreshnessConfig.default_staleness_seconds` = 86400 (24 hours) by default.
- Per-timeframe overrides supported via `timeframe_overrides`.

### 10.4 Stale Data Behavior

- Stale data is **accepted** for analysis (the analysis is still produced honestly over completed candles).
- A **warning** is surfaced in the dashboard.
- Stale data does **not** cause the symbol to be skipped or the scan to fail.
- Freshness is **DATA QUALITY only** — it never alters the intelligence engine's decision semantics.

### 10.5 Provider Latency / Delayed Data

- Not explicitly modeled. The freshness threshold indirectly accommodates latency (a 15m candle 5 minutes old is still CURRENT).

---

## 11. Failure Isolation

### 11.1 One Symbol Fails

- `_scan_one()` in `DashboardAnalysisService` catches exceptions per instrument and converts them to `ActionabilityState.INVALID` rows. The scan **continues** with remaining instruments.

### 11.2 One Provider Request Fails

- `YahooDataProvider.fetch()` catches all exceptions and returns `available=False` with `ProviderStatus.ERROR`. No exception propagates to the dashboard.

### 11.3 One Candle Is Malformed

- Per-candle validation in `_fetch_raw` (`data_provider.py:982`): invalid candles are dropped, valid ones kept, count reported in reason.

### 11.4 One Timeframe Fails

- If the context timeframe fetch fails, the scan is reported **INCOMPLETE** for that instrument (the scanner enforces `require_context_timeframe`). The setup timeframe analysis is still produced.

### 11.5 One Detector Receives Insufficient Data

- The scanner checks `len(setup_visible) <= min_history` and marks the setup slice as `ready=False` (INCOMPLETE). No crash.

### 11.6 Provider Returns Empty Result

- `ProviderStatus.EMPTY` → `available=False` → dashboard shows honest "unavailable" state.

### 11.7 Isolated Failure Does Not Corrupt the Scan

- **Verified.** Each layer catches and isolates failures. No shared mutable state leaks between symbols.

---

## 12. Determinism

### 12.1 InstrumentSeries Determinism

Given identical provider data and identical `reference_now`, the resulting `InstrumentSeries` is **deterministic**:
- `split_completed_candles` is a pure function (no side effects, no wall-clock dependency when `reference_now` is provided).
- De-duplication keeps the first occurrence (deterministic for identical input order).
- Sorting is stable and deterministic.

### 12.2 Downstream Detection Determinism

Given identical `InstrumentSeries` and `evaluation_time`, the downstream detection inputs are deterministic:
- `InstrumentDataset` is constructed from deterministic tuples.
- `MarketScanner.scan()` is a pure function of its inputs.
- All engines in `ScanEngines` are deterministic (no randomness, no wall-clock).

### 12.3 Non-Determinism Sources (Absent)

| Potential Source | Present? |
|------------------|----------|
| Unordered collections (set/dict iteration) | No — tuples and sorted lists used |
| Implicit current-time dependencies | No — `reference_now` explicit |
| Non-deterministic iteration | No — sequential, sorted |
| Mutable shared state | No — frozen dataclasses, stateless engines |
| Random behavior | No |
| Environment-dependent behavior | No (except live network data, which is external input) |

---

## 13. Existing Tests

### 13.1 Test Files Covering the Data/Candle Boundary

| Test File | Count | Areas Covered |
|-----------|-------|---------------|
| `test_live_data_integration.py` | 71 | Provider abstraction, fixture compatibility, normalization, malformed candles, empty response, provider failure, unsupported instrument/timeframe, completed-candle detection, forming-candle exclusion, future-timestamp protection, stale/fresh detection, dashboard integration, no-look-ahead, determinism, InstrumentSeries |
| `test_yahoo_range_fix.py` | 37 | split_completed_candles boundary, lookback-driven window, interval-safe ranges, future rejection, forming exclusion, provider status on empty/exception, determinism |
| `test_dashboard.py` | 67 | InstrumentSeries, stale data, malformed candles, empty data, timeframe integrity, failure isolation, determinism, freshness, provider behavior |
| `test_watchlist_scanner.py` | 75 | InstrumentSeries, stale data, forming exclusion, future rejection, empty provider result, provider failure, one-symbol failure isolation, determinism, freshness |
| `test_workstation.py` | 95 | InstrumentSeries, stale data, forming exclusion, future rejection, provider unavailable, unsupported timeframe/instrument, failure isolation, determinism, freshness |
| `test_ohlcv.py` | 9 | Candle model validation (high/low, open/close range, negative volume) |
| `test_validator.py` | 9 | Dataset validation (empty, duplicates, unsorted) |
| `test_paper_trading.py` | 114 | Malformed data, empty data, failure isolation, determinism |
| `test_paper_trading_operations.py` | 78 | InstrumentSeries, stale data, forming exclusion, future rejection, empty/malformed data, provider failure, failure isolation, freshness |
| `test_live_paper_validation.py` | 99 | InstrumentSeries, freshness, failure isolation, determinism |

### 13.2 Invariants with Explicit Tests

| Invariant | Test File |
|-----------|-----------|
| OHLC validation (high >= low, open/close in range) | `test_ohlcv.py`, `test_validator.py` |
| Non-negative volume | `test_ohlcv.py` |
| Completed-candle boundary | `test_live_data_integration.py`, `test_yahoo_range_fix.py` |
| Forming-candle exclusion | `test_live_data_integration.py`, `test_watchlist_scanner.py`, `test_workstation.py` |
| Future-timestamp rejection | `test_live_data_integration.py`, `test_yahoo_range_fix.py` |
| Chronological ordering | `test_validator.py` |
| Duplicate handling | `test_validator.py` |
| Empty data → unavailable | `test_live_data_integration.py`, `test_dashboard.py` |
| Stale data warning | `test_live_data_integration.py`, `test_dashboard.py` |
| Timeframe integrity | `test_live_data_integration.py`, `test_workstation.py` |
| Failure isolation | `test_live_data_integration.py`, `test_watchlist_scanner.py`, `test_paper_trading_operations.py` |
| Determinism | `test_live_data_integration.py`, `test_yahoo_range_fix.py`, `test_dashboard.py` |
| No look-ahead | `test_live_data_integration.py` |
| Provider error → graceful | `test_live_data_integration.py`, `test_dashboard.py` |

---

## 14. Identified Gaps

### 14.1 Critical

**None.** No gaps were found that could allow invalid, future, or non-point-in-time data into setup detection.

### 14.2 Significant

| Gap | Description | Location |
|-----|-------------|----------|
| **Naive timestamp interpretation** | `_ensure_aware_utc` interprets naive timestamps as UTC. If a provider returns naive timestamps in a non-UTC timezone, they would be silently misinterpreted. In practice, Yahoo daily data is exchange-day labels (naive), and interpreting as UTC is correct. But the assumption is not validated. | `data_provider.py:911-935` |
| **No explicit provider clock-skew handling** | Future-timestamp rejection uses local `reference_now`. A provider whose clock is ahead of local time could cause valid candles to be rejected (correct behavior, but the reason is not distinguishable from genuinely future data). | `data_provider.py:434` |

### 14.3 Low

| Gap | Description | Location |
|-----|-------------|----------|
| **No market-session filtering** | Pre/post-market candles are processed identically if the provider includes them. Documented limitation. | `data_provider.py` |
| **No gap detection in current-market path** | The historical layer has gap detection; the current-market path does not report gaps. | `data_provider.py` |
| **No retry logic** | The Yahoo provider has no explicit retry or timeout configuration for network requests. Relies on yfinance defaults. | `yahoo_provider.py:42` |
| **No current-market persistence** | Scan results are on-demand, not persisted. Intentional (no scheduling). | `services.py` |
| **Volume analysis not implemented** | `src/engine/intelligence/volume.py` is empty. Volume structures referenced in confluence but not computed. | `intelligence/volume.py` |
| **No explicit NaN/inf validation in OHLCVCandle** | `OHLCVCandle.__post_init__` does not check for NaN or infinity. NaN values would pass validation (NaN comparisons return False, but `low <= open <= high` with NaN open would be False, raising ValueError — this is actually safe by accident). | `ohlcv.py:24` |

---

## 15. Severity Classification

### 15.1 Critical

**None.** The data-integrity boundary is sound. No implementation changes are required to prevent invalid/future/non-point-in-time data from reaching setup detection.

### 15.2 Significant

1. **Naive timestamp interpretation assumption** — The system assumes naive timestamps are UTC. This is correct for Yahoo's daily data but is not validated. Risk: low (Yahoo is the only live provider, and its data is well-understood).

2. **Provider clock skew** — No explicit handling. The conservative behavior (reject if timestamp > now) is correct but could cause false rejections. Risk: low (minor data freshness impact, no correctness issue).

### 15.3 Low

All other gaps are improvements that do not threaten architectural correctness:
- Market-session filtering
- Gap detection in current-market path
- Retry/timeout configuration
- Volume analysis
- NaN/inf explicit validation

---

## 16. Required Changes

### 16.1 Implementation Decision

**No implementation changes are required** to establish the data-integrity boundary. The architecture already implements the invariants Checkpoint 11.2 audits.

### 16.2 Recommended Future Improvements (Not Blocking)

1. **Explicit NaN/inf rejection in OHLCVCandle** — Add `math.isfinite()` checks to `__post_init__` for defensive completeness. Low effort, low risk.
2. **Document the naive-timestamp-UTC assumption** — Add a comment in `_ensure_aware_utc` documenting why naive = UTC is correct for this provider.
3. **Consider market-session filtering** — For future product phases where session-aware analysis is needed.

---

## 17. Historical Boundary Verification

### 17.1 Coupling Check

| Question | Answer |
|----------|--------|
| Does this audit modify the historical research subsystem? | **No** |
| Does the current-market path import historical orchestration? | **No** |
| Does the current-market path import historical computation? | **No** |
| Are shared domain primitives used? | **Yes** — `OHLCVCandle` is shared (acceptable) |
| Does the audit introduce coupling? | **No** |

### 17.2 Shared Primitives

- `OHLCVCandle` (`engine/models/ohlcv.py`) — shared immutable domain model. Acceptable.
- `DataValidator` (`engine/data/validator.py`) — reused for per-candle validation. Acceptable.
- `historical_fixtures` — `FixtureDataProvider` reuses the Sprint 11V fixtures. Acceptable (fixtures are static data, not historical orchestration).

### 17.3 Historical Orchestration Untouched

No files in `src/engine/data/historical_*.py`, `src/engine/data/setup_research*.py`, `src/engine/data/research_corpus*.py`, `src/engine/intelligence/historical_*.py`, or `src/engine/research/*.py` were modified or imported by this audit.

---

## 18. Checkpoint 11.2 Verdict

### Verdict: **PASS WITH LIMITATIONS**

The current-market data ingestion and candle-integrity boundary is **architecturally sound and correctly implemented**. The data entering the detection layer is sufficiently correct, deterministic, fresh (relative to configured thresholds), point-in-time safe, and well-defined for current-market setup detection.

### What Was Inspected

1. Provider boundary (`DashboardDataProvider` protocol, `FixtureDataProvider`, `YahooDataProvider`)
2. `InstrumentSeries` invariants and immutability
3. `OHLCVCandle` integrity (OHLC validation, volume, timestamps)
4. `split_completed_candles` completed/incomplete/future boundary
5. Future-timestamp rejection (provider + scanner layers)
6. Point-in-time safety (no look-ahead, no future aggregation)
7. Timeframe identity and 15m handling
8. Session/market-hours handling (documented limitation)
9. Data freshness classification
10. Failure isolation (per-symbol, per-candle, per-provider)
11. Determinism (pure functions, explicit reference time)
12. Existing test coverage (400+ tests across 10+ files)
13. Historical boundary preservation

### What the Data Path Actually Is

```
YahooFinanceProvider.get_history() / FixtureDataProvider._cache
        ↓
DataNormalizer.normalize() + _ensure_aware_utc()
        ↓
DataValidator.validate_candle() (per-candle, drop invalid)
        ↓
split_completed_candles() → completed / forming / rejected_future
        ↓
InstrumentSeries (completed only in setup_candles/context_candles)
        ↓
InstrumentDataset (context + setup candles)
        ↓
MarketScanner.scan() → _latest_completed_before / _latest_completed_at_or_before
        ↓
ScanEngines (11O-11T) on candles[:T+1] only
        ↓
MarketScanResult
```

### What Boundary Currently Exists

A **defense-in-depth** boundary with three layers:
1. **Provider normalization** — validates and normalizes timestamps to UTC.
2. **`split_completed_candles`** — single deterministic function classifying candles as completed / forming / future.
3. **Scanner `_latest_completed_*` helpers** — second layer enforcing the temporal boundary at scan time.

### What Boundary Should Exist

The existing boundary is already correct. No architectural changes are required.

### Violations Found

**None.** No data-integrity violations, no future-leakage paths, no point-in-time violations.

### Files That Would Need Changes

None for boundary establishment. The gaps identified (§14) are improvements, not correctness issues.

### Existing Test Coverage

Comprehensive: 400+ tests covering provider behavior, candle validation, timestamp handling, future rejection, completed/incomplete candles, ordering, duplicates, malformed candles, empty data, stale data, timeframe integrity, failure isolation, and deterministic behavior.

### Questions for Review

1. Should market-session filtering be added to the current-market path in a future phase?
2. Should the naive-timestamp-UTC assumption be validated rather than assumed?
3. Is the 24-hour default staleness threshold appropriate for all timeframes, or should per-timeframe defaults be configured?

---

**END OF CHECKPOINT 11.2**

**Strict stop condition observed. Checkpoint 11.3 not proceeded to.**
