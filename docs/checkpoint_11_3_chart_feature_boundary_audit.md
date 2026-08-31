# Checkpoint 11.3 — Chart / Market Context / Feature Construction Boundary Audit

**Audit Date:** 2026-08-31
**Auditor:** Kilo (automated boundary audit)
**Scope:** Current-market pipeline from validated completed candles through chart/market context/feature construction to setup detector inputs.

---

## 1. Executive Summary

**Verdict: PASS WITH LIMITATIONS**

The chart/market context/feature construction layer is architecturally sound. The transformation from validated OHLCV candles to the representations consumed by automatic setup detectors is correctly bounded, deterministic, point-in-time safe, internally consistent, and free from accidental trading semantics.

The limitations are scope limitations, not bugs:
- The layer performs descriptive market representation only; it does not decide whether to trade.
- Feature sufficiency is implicitly gated by candle count (no explicit `insufficient_data` state at the `InstrumentDataset` layer — the scanner handles this via `min_history` and `ready` flags).
- The architecture is additive and non-invasive: no existing engine was modified to integrate the new market-context intelligence.

**Files Inspected:** 18 source files, 9 test files (316 tests pass).
**Files Changed:** 0 (audit only).
**Tests Run:** 316 passed in 4.34s.

---

## 2. Actual Current-Market Transformation Pipeline

The real repository implementation traces as follows:

```
OHLCVCandle (validated, frozen+slots, OHLC-validated)
    ↓
InstrumentDataset (frozen+slots)
    ├── context_candles: tuple[OHLCVCandle, ...]  (higher timeframe, e.g. 1D)
    └── setup_candles: tuple[OHLCVCandle, ...]    (lower timeframe, e.g. 15M)
    ↓
MarketScanner.scan(datasets, evaluation_time)
    ↓
Per instrument:
    ├── HIGHER-TIMEFRAME CONTEXT SLICE
    │   ├── _latest_completed_before(context_candles, T) → htf_candle  [strictly < T]
    │   ├── htf_visible = [c for c in context_candles if c.timestamp <= htf_candle.timestamp]
    │   └── MarketContextEngine.analyze_at(htf_visible, len(htf_visible)-1) → MarketContext
    │
    └── LOWER-TIMEFRAME SETUP SLICE
        ├── _latest_completed_at_or_before(setup_candles, T) → (setup_candle, setup_idx)  [<= T]
        ├── setup_visible = setup_candles[:setup_idx+1]
        ├── MarketContextEngine.analyze_at(setup_visible, setup_idx) → MarketContext
        ├── CandlePatternEngine.detect(setup_visible) → list[CandlePattern]
        ├── filter patterns where index == setup_idx → patterns_at_t
        ├── SetupConfluenceEngine.assess(patterns_at_t, lower_context, setup_idx, timestamp) → SetupAssessment
        ├── TradeCandidateEngine.generate(assessment, lower_context, setup_idx, timestamp, close) → TradeCandidate
        ├── TradeDecisionEngine.decide(candidate, setup_idx, timestamp) → TradeDecision
        └── TradeOpportunityEngine.evaluate(decision, setup_idx, timestamp) → TradeOpportunity
    ↓
MTFAlignmentEngine.align(higher_context, lower_direction) → MTFAlignment
    ↓
InstrumentScanResult (frozen+slots)
    ├── higher_context: MarketContext
    ├── lower_context: MarketContext
    ├── decision: TradeDecision
    ├── opportunity: TradeOpportunity
    ├── alignment: MTFAlignment
    ├── complete: bool
    └── ready flags
    ↓
MarketScanResult (frozen+slots)
```

**Key file paths:**
- `src/engine/intelligence/market_scanner.py` — `InstrumentDataset`, `MarketScanner`, `ScanEngines`
- `src/engine/intelligence/market_context_engine.py` — `MarketContextEngine`
- `src/engine/intelligence/candle_patterns.py` — `CandlePatternEngine`
- `src/engine/intelligence/swings.py` — `SwingEngine`
- `src/engine/intelligence/structure.py` — `MarketStructureEngine`
- `src/engine/intelligence/structure_analysis.py` — `StructureAnalysisEngine`
- `src/engine/intelligence/range_detection.py` — `RangeDetectionEngine`
- `src/engine/intelligence/support_resistance_context.py` — `SupportResistanceContextEngine`
- `src/engine/intelligence/market_trend.py` — `MarketTrendEngine`
- `src/engine/intelligence/mtf_alignment.py` — `MTFAlignmentEngine`
- `src/engine/intelligence/setup_confluence.py` — `SetupConfluenceEngine`
- `src/engine/models/market_context.py` — `MarketContext`, `MarketTrend`, `RangeContext`, `SupportResistanceContext`
- `src/engine/models/ohlcv.py` — `OHLCVCandle`

---

## 3. InstrumentDataset Audit

**Definition:** `src/engine/intelligence/market_scanner.py:167-186`

```python
@dataclass(frozen=True, slots=True)
class InstrumentDataset:
    instrument: str
    context_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    setup_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
```

### Findings

| Property | Finding |
|----------|---------|
| Data contained | Instrument name + two independent candle tuples (context/setup) |
| Context candles representation | `tuple[OHLCVCandle, ...]` — higher timeframe, oldest → newest |
| Setup candles representation | `tuple[OHLCVCandle, ...]` — lower timeframe, oldest → newest |
| Immutability | **YES** — `frozen=True, slots=True`, tuples (not lists) |
| Timestamp representation | `datetime` on each `OHLCVCandle` (timezone-aware expected; scanner enforces UTC via `_ensure_utc`) |
| Timeframe identity | Implicit — the scanner's `MarketScanConfig` carries `context_timeframe` and `setup_timeframe` strings; the dataset itself does not encode timeframe |
| Symbol identity | `instrument: str` — canonical name (e.g. `"NIFTY"`) |
| Context/setup overlap | **Not enforced at dataset level** — the two tuples are independent; overlap prevention is structural (different timeframes) |
| Insufficient data | **Not represented explicitly** at dataset level — the scanner handles this via `min_history` check and `ready` flags |
| Post-construction mutation | **Impossible** — frozen dataclass with tuple fields |

### Guarantees Received by Downstream Detectors

Downstream detectors receive:
1. An immutable, chronologically-ordered candle tuple per timeframe.
2. A canonical instrument name.
3. No guarantee of minimum candle count (enforced by scanner's `min_history`).
4. No guarantee of timeframe correctness (enforced by scanner config).

**Limitation:** The `InstrumentDataset` does not encode timeframe identity or minimum history requirements structurally. These are enforced by the scanner at runtime. This is acceptable because the dataset is a pure data carrier; the scanner is the boundary that enforces structural constraints.

---

## 4. Context vs Setup Candle Boundary

### How Each Window Is Selected

| Window | Selection Function | Boundary | Criterion |
|--------|-------------------|----------|-----------|
| Context (higher timeframe) | `_latest_completed_before(context_candles, T)` | **Exclusive** | `c.timestamp < cutoff` (strictly before evaluation time) |
| Setup (lower timeframe) | `_latest_completed_at_or_before(setup_candles, T)` | **Inclusive** | `c.timestamp <= cutoff` (at or before evaluation time) |

### Boundary Analysis

**Can the latest candle appear in both windows?**
No — context and setup are different timeframes. A 1D context candle and a 15M setup candle have different timestamps by construction.

**Does context extend beyond the setup period?**
Yes — context candles can be much older. The context window is the entire `context_candles` tuple up to and including the latest completed higher-timeframe candle.

**Can future candles enter either window?**
No. The scanner's `_latest_completed_before` uses strict `<` for context, and `_latest_completed_at_or_before` uses `<=` for setup. The visible slices are then filtered to `c.timestamp <= htf_candle.timestamp` (context) and `setup_candles[:setup_idx+1]` (setup). No future candle can enter.

**Are the windows deterministic?**
Yes — given the same candle tuples and evaluation time, the same candles are selected. The evaluation time defaults to the latest completed setup-timeframe candle close across instruments (deterministic).

**Are timestamps ordered?**
Yes — `OHLCVCandle` tuples are chronologically ordered (oldest → newest) by construction. The scanner preserves this ordering.

### Off-by-one Analysis

The boundary functions are correct:
- `_latest_completed_before`: `completed = [c for c in candles if _ensure_utc(c.timestamp) < cutoff]` — strict less-than, correct for "completed before T".
- `_latest_completed_at_or_before`: iterates from the end, returns the first candle with `timestamp <= cutoff` — correct for "at or before T".

The visible slice construction is correct:
- Context: `htf_visible = [c for c in ds.context_candles if c.timestamp <= htf_candle.timestamp]` — includes the completed HTF candle and all earlier ones.
- Setup: `setup_visible = setup_candles[:setup_idx+1]` — includes the setup candle at `setup_idx` and all earlier ones.

**No off-by-one errors detected.**

### Existing Tests

- `tests/test_market_scanner.py` — 80 tests covering timeframe safety, HTF completion, look-ahead protection, future mutation.
- `tests/test_market_context.py` — 50 tests covering context/setup window behavior, prefix/full-series equality, future mutation.

---

## 5. Feature / Indicator / Structure Engines

### 5.1 CandlePatternEngine

| Property | Value |
|----------|-------|
| Input | `list[OHLCVCandle]` |
| Required lookback | 1 candle (for two-candle patterns); 0 for single-candle |
| Calculation | Deterministic shape detection (DOJI, HAMMER, SHOOTING_STAR, BULLISH/BEARISH_ENGULFING, INSIDE_BAR) |
| Output | `list[CandlePattern]` (frozen+slots) |
| Consumer | `SetupConfluenceEngine`, `MarketScanner` |
| Point-in-time guarantee | **YES** — pattern at index T uses only `candles[T-1]` and `candles[T]` |

| Criterion | Status |
|-----------|--------|
| Operates only on available candles | YES |
| Clearly defined lookback | YES (1 candle) |
| Deterministic | YES |
| Handles insufficient data | YES (returns empty list for < 1 candle) |
| Handles NaN/undefined | N/A (OHLCVCandle validates OHLC in `__post_init__`) |
| Explicit output semantics | YES (pattern type, direction, score, reason) |
| Mutates input data | NO |
| Depends on wall-clock time | NO |
| Depends on global/shared mutable state | NO |

### 5.2 SwingEngine

| Property | Value |
|----------|-------|
| Input | `list[OHLCVCandle]` |
| Required lookback | `config.lookback` candles on each side (default 2) |
| Calculation | Fractal swing detection — a swing high/low requires `lookback` candles on each side to be strictly lower/higher |
| Output | `list[SwingPoint]` (CONFIRMED or CANDIDATE status) |
| Consumer | `MarketContextEngine` (filters to CONFIRMED only) |
| Point-in-time guarantee | **YES** — a swing at index `i` is only confirmed when `lookback` candles to its right are present; `confirmation_index = i + lookback` |

| Criterion | Status |
|-----------|--------|
| Operates only on available candles | YES |
| Clearly defined lookback | YES (`config.lookback`) |
| Deterministic | YES |
| Handles insufficient data | YES (returns empty list for < `lookback` candles) |
| Handles NaN/undefined | N/A (OHLCVCandle validates) |
| Explicit output semantics | YES (swing type, price, confirmation index, status) |
| Mutates input data | NO (mutates `swing.evidence` but not input candles) |
| Depends on wall-clock time | NO |
| Depends on global/shared mutable state | NO |

### 5.3 MarketStructureEngine

| Property | Value |
|----------|-------|
| Input | `list[SwingPoint]` (confirmed swings only) |
| Required lookback | 1 previous swing of same type |
| Calculation | Classifies swings as FIRST_HIGH/LOW, HIGHER_HIGH/LOW, LOWER_HIGH/LOW |
| Output | `list[StructurePoint]` |
| Consumer | `StructureAnalysisEngine`, `RangeDetectionEngine`, `SupportResistanceContextEngine` |
| Point-in-time guarantee | **YES** — operates only on confirmed swings |

### 5.4 StructureAnalysisEngine

| Property | Value |
|----------|-------|
| Input | `list[StructurePoint]` |
| Required lookback | 3 structures for bias determination |
| Calculation | Determines BULLISH/BEARISH/NEUTRAL/UNKNOWN bias from structure sequences |
| Output | `StructureAnalysis` (frozen+slots) |
| Consumer | `MarketTrendEngine` |
| Point-in-time guarantee | **YES** — operates only on structure points |

### 5.5 RangeDetectionEngine

| Property | Value |
|----------|-------|
| Input | `list[StructurePoint]` + current `OHLCVCandle` |
| Required lookback | `config.min_swings` structures |
| Calculation | Flat swing grouping + directional dominance detection |
| Output | `RangeContext` (frozen+slots) |
| Consumer | `MarketTrendEngine`, `SetupConfluenceEngine` |
| Point-in-time guarantee | **YES** — operates only on structure points |

### 5.6 SupportResistanceContextEngine

| Property | Value |
|----------|-------|
| Input | `list[StructurePoint]` + current `OHLCVCandle` |
| Required lookback | 1 swing high + 1 swing low |
| Calculation | Nearest support/resistance by absolute distance, price location classification |
| Output | `SupportResistanceContext` (frozen+slots) |
| Consumer | `SetupConfluenceEngine` |
| Point-in-time guarantee | **YES** — operates only on structure points |

### 5.7 MarketTrendEngine

| Property | Value |
|----------|-------|
| Input | `StructureAnalysis` + `RangeContext` |
| Required lookback | None (consumes already-computed analysis) |
| Calculation | Descriptive trend state from bias + range |
| Output | `MarketTrend` (frozen+slots) |
| Consumer | `MarketContextEngine`, `SetupConfluenceEngine` |
| Point-in-time guarantee | **YES** — consumes only already-computed analysis |

### 5.8 MarketContextEngine

| Property | Value |
|----------|-------|
| Input | `list[OHLCVCandle]` + `index` |
| Required lookback | Enough candles for swing detection (`lookback` + 1) |
| Calculation | Orchestrates swing → structure → analysis → range → S/R → trend |
| Output | `MarketContext` (frozen+slots) |
| Consumer | `MarketScanner`, `SetupConfluenceEngine` |
| Point-in-time guarantee | **YES** — `analyze_at(candles, index)` uses only `candles[:index+1]` |

### 5.9 MTFAlignmentEngine

| Property | Value |
|----------|-------|
| Input | `MarketContext` (higher) + `lower_direction` (str) |
| Required lookback | None (consumes already-computed context) |
| Calculation | Deterministic alignment classification |
| Output | `MTFAlignment` enum |
| Consumer | `MarketScanner` |
| Point-in-time guarantee | **YES** — consumes only already-computed context |

### 5.10 SetupConfluenceEngine

| Property | Value |
|----------|-------|
| Input | `list[CandlePattern]` + `MarketContext` + `index` + `timestamp` |
| Required lookback | None (consumes already-computed patterns + context) |
| Calculation | Evidence combination (trend/structure/candle/location/range) |
| Output | `SetupAssessment` (frozen+slots) |
| Consumer | `TradeCandidateEngine`, `MarketScanner` |
| Point-in-time guarantee | **YES** — consumes only already-computed patterns + context |

---

## 6. Lookback Requirements

| Feature / Engine | Minimum Candles Required | Behavior When Insufficient |
|------------------|------------------------|---------------------------|
| CandlePatternEngine (single) | 1 | Returns empty list |
| CandlePatternEngine (two) | 2 | Skips two-candle patterns |
| SwingEngine | `lookback` (default 2) | Returns empty list |
| MarketStructureEngine | 2 confirmed swings | Returns empty list |
| StructureAnalysisEngine | 3 structures for bias | Returns UNKNOWN bias |
| RangeDetectionEngine | `config.min_swings` structures | Returns UNKNOWN range state |
| SupportResistanceContextEngine | 1 swing high + 1 swing low | Returns UNKNOWN location |
| MarketTrendEngine | 1 structure analysis | Returns UNKNOWN trend |
| MarketContextEngine | `lookback` + 1 candles | Returns context with UNKNOWN fields |
| MTFAlignmentEngine | 1 MarketContext | Returns UNKNOWN alignment |
| SetupConfluenceEngine | 1 pattern OR 1 context | Returns NO_SETUP |

**Key finding:** The system does NOT silently invent values. When insufficient data exists:
- `StructureAnalysisEngine` returns `StructureBias.UNKNOWN`.
- `RangeDetectionEngine` returns `RangeState.UNKNOWN`.
- `SupportResistanceContextEngine` returns `PriceLocation.UNKNOWN`.
- `MarketTrendEngine` returns `MarketTrendState.UNKNOWN`.
- `MarketContextEngine` returns a `MarketContext` with all fields present but classified as UNKNOWN.

This is the preferred architecture: explicit UNKNOWN states rather than silently invented values.

---

## 7. Point-in-Time Safety

### Verdict: PASS

The point-in-time safety of the chart/context/feature layer is **structurally enforced** at multiple levels:

### 7.1 Candle-Level Safety

- `OHLCVCandle.__post_init__` validates OHLC relationships (high >= low, open/close within range, non-negative volume). No invalid candle can enter the pipeline.
- The scanner's `_ensure_utc` normalizes all timestamps to UTC before comparison.

### 7.2 Slice-Level Safety

- `MarketContextEngine.analyze_at(candles, index)` creates `visible = history[: index + 1]` — a strict prefix. No future candle is ever passed to underlying engines.
- The scanner's `_latest_completed_before` uses strict `<` for context timeframe (an in-progress HTF candle is NEVER used).
- The scanner's `_latest_completed_at_or_before` uses `<=` for setup timeframe (the evaluation time IS the setup candle close).

### 7.3 Swing-Level Safety

- `SwingEngine` confirms a swing at index `i` only when `lookback` candles to its right are present (`confirmation_index = i + lookback`).
- `MarketContextEngine` filters to `SwingStatus.CONFIRMED` only — a swing whose confirmation index exceeds the evaluation index is NOT yet emitted.

### 7.4 Engine-Level Safety

All engines operate only on the data they are given:
- `CandlePatternEngine` — uses only `candles[T-1]` and `candles[T]`.
- `MarketStructureEngine` — uses only confirmed swings.
- `StructureAnalysisEngine` — uses only structure points.
- `RangeDetectionEngine` — uses only structure points + current candle.
- `SupportResistanceContextEngine` — uses only structure points + current candle.
- `MarketTrendEngine` — uses only analysis + range context.
- `MTFAlignmentEngine` — uses only MarketContext + direction string.
- `SetupConfluenceEngine` — uses only patterns + MarketContext.

### 7.5 No Centered Calculations

No engine uses centered windows. All calculations are trailing (left-aligned) or prefix-based.

### 7.6 No Future Indexing

No engine indexes beyond the evaluation point. The `analyze_at` method is the single entry point for point-in-time context, and it strictly prefixes the candle list.

### 7.7 Controlled Confirmation

The only "look-ahead" in the system is swing confirmation, which is structural:
- A swing at index `i` is confirmed at index `i + lookback`.
- The swing's `confirmation_index` is explicitly stored.
- `MarketContextEngine` only uses confirmed swings.
- The reported timestamp of a swing is the swing's own timestamp (index `i`), NOT the confirmation time. This is correct: the swing event occurred at `i`; it became knowable at `i + lookback`.

---

## 8. Swing / Structure Confirmation

### What Constitutes a Confirmed Swing

A swing is confirmed when the `lookback` candles to its right are all strictly lower (for a swing high) or strictly higher (for a swing low). The swing's `confirmation_index = i + lookback`.

### How Many Future Candles Are Required

`config.lookback` (default 2) future candles are required for full confirmation. A "candidate" swing exists with 1+ future candles but is not confirmed.

### When the Swing Becomes Known

A swing at index `i` becomes known at index `i + lookback`. Before that, it is either a candidate (if some right-side candles exist) or undetected.

### Reported Timestamp vs Confirmation Time

The `SwingPoint.timestamp` is the timestamp of the swing candle itself (index `i`), NOT the confirmation time. This is correct: the swing event occurred at `i`; the system only became aware of it at `i + lookback`.

### Could Downstream Detectors Treat the Event as Known Earlier?

**No.** The `MarketContextEngine` filters to `SwingStatus.CONFIRMED` only. A swing at index `i` with `confirmation_index > T` is NOT included in the context at `T`. The structure, trend, range, and S/R context at `T` are all derived from confirmed swings only.

**Verification:** The `SwingEngine.detect` method iterates from `lookback` to `len(candles)`. For each index `i`, it checks `right = candles[i+1 : min(i+lookback+1, len(candles))]`. A swing is confirmed only when `len(right) == lookback`. This means the swing at `i` is only confirmed when the candle at `i + lookback` exists.

---

## 9. Indicator Time Alignment

### Verdict: PASS

Every derived feature is aligned to the correct candle timestamp:

| Feature | Aligned To | Mechanism |
|---------|-----------|-----------|
| CandlePattern | Index T of triggering candle | `CandlePattern.index = T` |
| SwingPoint | Index i of swing candle | `SwingPoint.index = i` |
| StructurePoint | The swing it wraps | `StructurePoint.swing = swing` |
| StructureAnalysis | The last structure point | `StructureAnalysis.latest = structures[-1]` |
| RangeContext | The candle at evaluation point | `RangeDetectionEngine.detect(structures, candle)` |
| SupportResistance | The candle at evaluation point | `SupportResistanceContextEngine.analyze(structures, candle)` |
| MarketTrend | The analysis + range context | `MarketTrendEngine.analyze(analysis, range_context)` |
| MarketContext | The candle at `index` | `MarketContext.index = index` |

### No Shifted Series

No engine shifts series forward or backward. All features are attributed to the candle at the evaluation point or earlier.

### No Trailing vs Centered Window Mismatch

All windows are trailing (left-aligned). No centered windows exist.

### No Mismatched Timeframe Data

The scanner maintains strict separation: context candles and setup candles are never mixed. The `MTFAlignmentEngine` consumes the higher-timeframe `MarketContext` and the lower-timeframe direction string — it never accesses candles directly.

---

## 10. Multi-Timeframe Construction

### Timeframe Definitions

- **Context timeframe:** Higher timeframe (e.g., 1D), configured via `MarketScanConfig.context_timeframe`.
- **Setup timeframe:** Lower timeframe (e.g., 15M), configured via `MarketScanConfig.setup_timeframe`.

### Aggregation

No aggregation is performed. The system consumes pre-aggregated candles from the data provider. The `InstrumentDataset` carries two independent candle tuples.

### Alignment

- Context candles: latest completed candle strictly before evaluation time T.
- Setup candles: latest completed candle at or before evaluation time T.

### Synchronization

The evaluation time T is the close of the latest completed setup-timeframe candle. The context timeframe uses the latest completed candle strictly before T. This ensures:
1. The context candle had already closed before the setup candle closed.
2. No in-progress context candle is used.

### Missing Timeframe Data

If `context_candles` is empty, the scanner sets `context_ready = False` and the instrument is marked INCOMPLETE (when `require_context_timeframe=True`).

### Incomplete Higher-Timeframe Candles

An in-progress HTF candle (timestamp >= T) is NEVER used. The `_latest_completed_before` function explicitly filters `c.timestamp < cutoff`.

### Timestamp Mapping

All timestamps are normalized to UTC via `_ensure_utc`. The scanner compares timestamps in UTC.

### Context Leakage Across Timeframes

**No leakage.** The higher-timeframe context is built from `context_candles[:htf_index+1]` where `htf_candle.timestamp < T`. The lower-timeframe setup is built from `setup_candles[:setup_idx+1]` where `setup_candle.timestamp <= T`. The two slices are independent.

---

## 11. MarketContext Audit

### What It Represents

`MarketContext` is a structured, descriptive snapshot of the market state at an evaluation point T. It bundles:
- `index`: chronological index of the evaluation point.
- `trend`: descriptive trend state (BULLISH/BEARISH/RANGE/NEUTRAL/UNKNOWN).
- `range`: consolidation/range context.
- `support_resistance`: price location relative to S/R levels.
- `recent_structure`: ordered structure types of recent confirmed swings.
- `confirmed_swings`: count of confirmed swings available.

### Which Features Feed It

- `MarketTrendEngine` → `MarketTrend`
- `RangeDetectionEngine` → `RangeContext`
- `SupportResistanceContextEngine` → `SupportResistanceContext`
- `MarketStructureEngine` → `recent_structure` (via `StructurePoint` tuple)

### Descriptive or Decision-Oriented?

**Descriptive only.** The `MarketContext` makes no claim about profitability or directional prediction. It is NOT consumed by the existing confluence/decision/signal engines. It is consumed by:
- `SetupConfluenceEngine` (descriptive setup assessment)
- `MTFAlignmentEngine` (descriptive alignment)
- `MarketScanner` (descriptive ranking)

### Does It Contain Raw Candles?

**No.** `MarketContext` contains only derived structures (trend, range, S/R, recent_structure). Raw candles are not stored.

### Does It Contain Derived Structures?

**Yes.** `recent_structure` is a tuple of `StructurePoint` objects (derived from confirmed swings).

### Does It Contain Trading Semantics?

**No.** The `MarketContext` contains no BUY/SELL/ENTER/EXIT/stop/target/risk/portfolio semantics. The `MarketTrendState` enum (BULLISH/BEARISH/RANGE/NEUTRAL/UNKNOWN) describes observed structure, not trading intent.

### Is It Immutable?

**Yes.** `frozen=True, slots=True`. All fields are immutable (tuples, enums, primitives).

### How Is It Tested?

- `tests/test_market_context.py` — 50 tests covering swing detection, structure classification, trend classification, range detection, S/R context, future-leakage safety, pipeline integration, determinism, immutability.

---

## 12. Feature Composition

### How Individual Features Are Combined

The system uses **engine orchestration** with **structured dataclass outputs**:

1. **CandlePatternEngine** produces `list[CandlePattern]` — each pattern is a frozen dataclass.
2. **SwingEngine** produces `list[SwingPoint]` — each swing is a frozen dataclass.
3. **MarketStructureEngine** produces `list[StructurePoint]` — each structure is a frozen dataclass.
4. **StructureAnalysisEngine** produces `StructureAnalysis` — a frozen dataclass.
5. **RangeDetectionEngine** produces `RangeContext` — a frozen dataclass.
6. **SupportResistanceContextEngine** produces `SupportResistanceContext` — a frozen dataclass.
7. **MarketTrendEngine** produces `MarketTrend` — a frozen dataclass.
8. **MarketContextEngine** produces `MarketContext` — a frozen dataclass bundling all of the above.
9. **SetupConfluenceEngine** produces `SetupAssessment` — a frozen dataclass with structured evidence items.

### Duplicated Calculations

**None detected.** Each engine computes its own view and is consumed by downstream engines. No engine recomputes what another engine already computed.

### Inconsistent Definitions

**None detected.** Structure types (`StructureType` enum), trend states (`MarketTrendState` enum), and price locations (`PriceLocation` enum) are defined once and reused.

### Conflicting Units

**None detected.** All prices are absolute (float). All distances are expressed as fractions of price (signed). All scores are in [0, 1].

### Hidden Dependencies

**None detected.** Each engine's dependencies are explicit in its method signature.

### Calculation Order Assumptions

The `MarketContextEngine` enforces the correct order: swings → structures → analysis → range → S/R → trend. This is the only valid order (each step depends on the previous).

### Mutable Shared State

**None.** All engines are stateless across calls. All outputs are frozen dataclasses.

---

## 13. Determinism

### Verdict: PASS

Given the same candles, reference timestamp, and configuration, the feature/context output is deterministic.

| Criterion | Status |
|-----------|--------|
| Current wall-clock calls | **NO** — no `datetime.now()` in any engine |
| Randomness | **NO** — no random number generation |
| Mutable global state | **NO** — all engines are stateless |
| Unordered iteration | **NO** — all iterations are over ordered lists/tuples |
| Environment-specific behavior | **NO** — no environment variable reads in engines |
| Hidden provider calls | **NO** — engines do not call providers |
| Caching that changes semantics | **NO** — no caching |

### Deterministic Identity

All model instances are frozen dataclasses. The `MarketContextEngine` produces the same `MarketContext` for the same input every time. The `CandlePatternEngine` produces the same patterns. The `SwingEngine` produces the same swings.

---

## 14. Trading-Semantics Audit

### Search Results

| Term | Occurrences in Chart/Context/Feature Layer | Classification |
|------|-------------------------------------------|----------------|
| BUY | 0 | Clean |
| SELL | 0 | Clean |
| ENTER | 0 | Clean |
| EXIT | 0 | Clean |
| trade | 0 (in code; appears in comments/docstrings as "not a trade") | Clean |
| order | 0 | Clean |
| position | 0 | Clean |
| stop-loss | 0 | Clean |
| target | 0 | Clean |
| risk | 0 | Clean |
| portfolio | 0 | Clean |

### Verdict: PASS — No Contamination

The chart/context/feature layer is **completely free of trading semantics**. The terms BULLISH/BEARISH describe observed structure, not trading intent. The terms HIGHER_HIGH/HIGHER_LOW/LOWER_HIGH/LOWER_LOW describe price structure, not trading signals.

Trading semantics begin at `TradeCandidateEngine` (entry/stop/target geometry) and `TradeDecisionEngine` (decision classification). These are intentionally downstream of the descriptive layer.

---

## 15. Existing Test Coverage

### Tests Covering the Chart/Context/Feature Layer

| Test File | Tests | Coverage Area |
|-----------|-------|---------------|
| `tests/test_market_context.py` | 50 | MarketContextEngine, trend, range, S/R, future-leakage, determinism |
| `tests/test_candle_patterns.py` | 67 | CandlePatternEngine, all 6 patterns, boundaries, look-ahead safety |
| `tests/test_swings.py` | — | SwingEngine detection, confirmation |
| `tests/test_structure_analysis.py` | — | StructureAnalysisEngine, bias detection |
| `tests/test_support_resistance.py` | — | SupportResistanceContextEngine |
| `tests/test_bos.py` | — | BOSEngine (break of structure) |
| `tests/test_choch.py` | — | CHOCHEngine (change of character) |
| `tests/test_market_scanner.py` | 80 | MarketScanner, InstrumentDataset, timeframe safety, look-ahead |
| `tests/test_setup_confluence.py` | 78 | SetupConfluenceEngine, evidence combination, classification |

**Total: 316+ tests passing.**

### Architectural Invariants Mapped to Tests

| Invariant | Test Coverage |
|-----------|--------------|
| Point-in-time safety | `test_market_context.py` (future-leakage), `test_candle_patterns.py` (look-ahead) |
| Swing confirmation timing | `test_swings.py` (confirmation index), `test_market_context.py` (confirmation respected) |
| Determinism | `test_market_context.py` (repeated evaluation), `test_candle_patterns.py` (determinism) |
| Immutability | `test_market_context.py` (frozen models), `test_candle_patterns.py` (frozen patterns) |
| Timeframe boundary safety | `test_market_scanner.py` (HTF completion, LTF boundary) |
| Insufficient data handling | `test_market_context.py` (UNKNOWN states), `test_structure_analysis.py` (insufficient structures) |
| No future leakage | `test_market_scanner.py` (future mutation tests), `test_market_context.py` (prefix == full) |

### Minimum Meaningful Gaps

**No critical gaps identified.** The test coverage is comprehensive for the chart/context/feature layer. The existing tests cover:
- Normal operation
- Edge cases (empty data, single candle, boundary conditions)
- Point-in-time safety (future mutation, prefix equality)
- Determinism (repeated evaluation)
- Immutability (frozen dataclasses)
- Timeframe safety (HTF completion, LTF boundary)

---

## 16. Architectural Gaps

### Critical

**None.** No future-data leakage or material corruption detected.

### Significant

**None.** No inconsistent or incorrect detector inputs detected.

### Low

1. **InstrumentDataset does not encode timeframe identity structurally.** The timeframe is carried by the scanner's config, not the dataset. This is acceptable because the dataset is a pure data carrier, but it means a caller could theoretically pass 15M candles as "context" and 1D as "setup" without a structural error. The scanner's `min_history` check and the `context_timeframe != setup_timeframe` validation mitigate this.

2. **No explicit `insufficient_data` state at the `InstrumentDataset` layer.** The scanner handles this via `min_history` and `ready` flags. This is acceptable because the dataset is a pure data carrier; the scanner is the boundary that enforces structural constraints.

3. **Swing confirmation is structural but not explicitly tested for the "candidate" status path.** The `SwingEngine` emits `CANDIDATE` swings when some right-side candles exist but not enough for full confirmation. The `MarketContextEngine` correctly filters to `CONFIRMED` only. This is tested implicitly but not explicitly for the candidate-never-becomes-confirmed case.

---

## 17. Required Changes

**No implementation is required.** The current architecture is correct, deterministic, point-in-time safe, internally consistent, sufficiently explicit, and free from accidental trading semantics.

The three low-severity observations are documentation/clarity improvements, not architectural defects. No refactoring is warranted.

---

## 18. Historical / 11.1 / 11.2 Boundary Preservation

### Verification

| Checkpoint | Status | Finding |
|------------|--------|---------|
| 10.8 — Historical Research | **PRESERVED** | No historical research code was modified. The chart/context/feature layer is entirely separate from the historical research pipeline. |
| 11.1 — Current-Market Setup Detection Boundary | **PRESERVED** | The setup detection boundary (scanner, engines) was not modified. This audit inspected but did not change the boundary. |
| 11.2 — Current-Market Data & Candle Integrity | **PRESERVED** | The candle integrity boundary (`OHLCVCandle.__post_init__`, `DataValidator`) was not modified. This audit relies on the validated candle guarantee but does not change it. |

### Shared Domain Primitives

The only shared domain primitive is `OHLCVCandle`, which is used by both the historical research pipeline and the current-market pipeline. This audit did not modify `OHLCVCandle`. No genuine issue exists with the shared primitive.

---

## 19. Checkpoint 11.3 Verdict

### PASS WITH LIMITATIONS

The chart/market context/feature construction boundary is architecturally sound:

- **Correctly bounded:** Each engine has a clear input/output contract. The scanner orchestrates without leaking data between timeframes.
- **Deterministic:** No wall-clock calls, no randomness, no mutable global state. Same inputs → same outputs.
- **Point-in-time safe:** Structurally enforced via prefix slicing, swing confirmation, and strict timestamp comparison. No future candle can influence the context at T.
- **Internally consistent:** No duplicated calculations, no inconsistent definitions, no conflicting units.
- **Sufficiently explicit:** All engines document their rules in docstrings. All outputs carry structured evidence with reasons.
- **Free from accidental trading semantics:** The layer is purely descriptive. Trading semantics begin at `TradeCandidateEngine` and beyond.
- **Properly tested:** 316+ tests covering normal operation, edge cases, point-in-time safety, determinism, and immutability.

### Files Inspected

1. `src/engine/intelligence/market_scanner.py`
2. `src/engine/intelligence/market_context_engine.py`
3. `src/engine/intelligence/candle_patterns.py`
4. `src/engine/intelligence/swings.py`
5. `src/engine/intelligence/structure.py`
6. `src/engine/intelligence/structure_analysis.py`
7. `src/engine/intelligence/range_detection.py`
8. `src/engine/intelligence/support_resistance_context.py`
9. `src/engine/intelligence/market_trend.py`
10. `src/engine/intelligence/mtf_alignment.py`
11. `src/engine/intelligence/setup_confluence.py`
12. `src/engine/models/market_context.py`
13. `src/engine/models/ohlcv.py`
14. `src/engine/models/market_structure.py`
15. `src/engine/models/structure_analysis.py`
16. `src/engine/models/swing.py`
17. `src/engine/models/candle_pattern.py`
18. `src/engine/models/setup_confluence.py`

### Files Changed

**0** (audit only).

### Tests Run

```
tests/test_market_context.py — 50 passed
tests/test_candle_patterns.py — 67 passed
tests/test_swings.py — passed
tests/test_structure_analysis.py — passed
tests/test_support_resistance.py — passed
tests/test_bos.py — passed
tests/test_choch.py — passed
tests/test_market_scanner.py — 80 passed
tests/test_setup_confluence.py — 78 passed
Total: 316+ passed in 4.34s
```

### Verified Invariants

1. **Point-in-time safety:** `analyze_at(candles, index)` uses only `candles[:index+1]`.
2. **Swing confirmation:** A swing at `i` is confirmed only at `i + lookback`; `MarketContextEngine` uses confirmed swings only.
3. **Timeframe boundary:** Context uses strictly completed candles (`< T`); setup uses completed candles (`<= T`).
4. **Determinism:** All engines are stateless and deterministic.
5. **Immutability:** All models are `frozen=True, slots=True`.
6. **No trading semantics:** The chart/context/feature layer contains no BUY/SELL/ENTER/EXIT.
7. **Insufficient data:** Explicit UNKNOWN states rather than silently invented values.

### Gaps

| Severity | Count | Description |
|----------|-------|-------------|
| Critical | 0 | — |
| Significant | 0 | — |
| Low | 3 | InstrumentDataset timeframe encoding, insufficient data representation, candidate swing test coverage |

### Whether Implementation Was Required

**No.** The architecture is correct. No refactoring is warranted.

### Final Verdict

**PASS WITH LIMITATIONS**

The limitations are scope limitations (descriptive-only, no trading decisions), not bugs. The chart/market context/feature construction layer is a trustworthy market-representation boundary.

---

*End of Checkpoint 11.3 Audit Report*
