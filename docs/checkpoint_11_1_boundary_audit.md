# CHECKPOINT 11.1 — Current-Market Setup Detection Boundary Audit & Design

**Date:** 2026-08-31  
**Status:** AUDIT COMPLETE  
**Verdict:** **PASS WITH LIMITATIONS**

---

## 1. Executive Summary

The current-market setup detection subsystem is architecturally sound and already implements the desired boundary. The system follows a strict **separated concern pipeline** (Sprints 11O-11U) where each layer produces purely descriptive, technical-analysis outputs. No trading-semantics contamination (BUY/SELL, execution, position sizing, stop-loss orders, portfolio management) was found in the detection path.

The detection layer terminates at `MarketScanResult` — a ranked, descriptive view of trade opportunities across instruments/timeframes. Downstream layers (trade planning, paper trading, production integration) consume these outputs without modifying them.

**Key finding:** The architecture already implements the target boundary specified in Checkpoint 11.1. The audit confirms the boundary is clean, well-tested, and properly separated from both the frozen historical research subsystem and the downstream decision layer.

---

## 2. Current Architecture

### 2.1 Detection Pipeline (Sprint 11O-11U)

```
Market Data Provider (Yahoo/Fixture)
        ↓
DashboardDataProvider Protocol
        ↓
InstrumentSeries (completed candles only)
        ↓
DashboardAnalysisService.analyze()
        ↓
InstrumentDataset (context + setup candles)
        ↓
MarketScanner.scan()
        ↓
    ┌─── ScanEngines ───┐
    │  CandlePattern    │ (11O)
    │  MarketContext    │ (11P)
    │  SetupConfluence  │ (11Q)
    │  TradeCandidate   │ (11R)
    │  TradeDecision    │ (11S)
    │  TradeOpportunity │ (11T)
    │  MTFAlignment     │ (11U)
    └───────────────────┘
        ↓
MarketScanResult (ranked opportunities)
        ↓
─────────────────────────────────────
     DOWNSTREAM LAYER (out of scope)
─────────────────────────────────────
```

### 2.2 Downstream Layer (NOT part of 11.1)

```
MarketScanResult
        ↓
TradePlanningEngine (Phase 4) → TradePlan (position sizing, risk)
        ↓
PaperTradingEngine (Phase 5) → PaperTrade (observational validation)
        ↓
ProductionIntelligenceEngine (12E) → ProductionIntelligenceContext
        ↓
Dashboard (presentation only)
```

---

## 3. Current-Market Entry Points

### 3.1 Primary Entry Points

| Entry Point | File | Trigger |
|-------------|------|---------|
| `DashboardAnalysisService.analyze()` | `src/dashboard/services.py` | Single-instrument analysis |
| `DashboardAnalysisService.scan_watchlist()` | `src/dashboard/services.py` | Multi-instrument scanner |
| `DashboardAnalysisService.workstation()` | `src/dashboard/services.py` | Live trading workstation |
| `MarketScanner.scan()` | `src/engine/intelligence/market_scanner.py` | Direct scanner API |
| `scripts/run_paper_trading_cycle.py` | CLI | Manual CLI trigger |
| `scripts/run_live_paper_validation.py` | CLI | Manual CLI trigger |
| FastAPI routes (`/api/scan`, `/api/workstation`, `/api/analysis`) | `src/dashboard/app.py` | HTTP API |

### 3.2 Scanning Characteristics

- **Scheduling:** None. All scanning is **on-demand / manually triggered** (no cron, no background polling, no timers).
- **Concurrency:** None. All scanning is **synchronous and sequential**.
- **Symbol iteration:** Sequential loop over watchlist instruments.
- **Failure isolation:** Per-symbol failure isolation via `_scan_one()` — one failed instrument does not terminate the scan.
- **Watchlist source:** `Watchlist.DEFAULT_WATCHLIST` in `src/dashboard/watchlist.py` (NIFTY + NIFTY 50 + SENSEX constituents).

---

## 4. Current-Market Data Flow

### 4.1 Provider Layer

| Component | File | Role |
|-----------|------|------|
| `MarketDataProvider` (ABC) | `src/engine/data/provider.py` | Core provider interface |
| `BaseDataProvider` | `src/engine/data/base_provider.py` | Shared implementation |
| `YahooFinanceProvider` | `src/engine/data/yahoo_provider.py` | Live/near-live data |
| `DashboardDataProvider` (Protocol) | `src/dashboard/data_provider.py` | Dashboard-level abstraction |
| `FixtureDataProvider` | `src/dashboard/data_provider.py` | Deterministic offline data |
| `YahooDataProvider` | `src/dashboard/data_provider.py` | Live adapter |

### 4.2 Data Path

```
YahooFinanceProvider.get_history()
        ↓
DataNormalizer.normalize()  ← column name standardization
        ↓
DataValidator.validate_candle()  ← per-candle OHLC validation
        ↓
InstrumentSeries  ← completed candles only, forming candle tracked separately
        ↓
split_completed_candles()  ← strict boundary: close_time <= now → engine
        ↓                           open <= now < close → display only
        ↓                           timestamp > now → rejected
InstrumentDataset(context_candles, setup_candles)
        ↓
MarketScanner.scan()
```

### 4.3 Separation from Historical Path

The current-market path is **completely decoupled** from the historical research path:

- Current-market uses `DashboardDataProvider` protocol → `InstrumentSeries`
- Historical uses `HistoricalMarketDataProvider` protocol → `HistoricalDataStore`
- Both converge at `MarketScanner.scan()` via `InstrumentSeries` → `InstrumentDataset` adapter
- Current-market modules do NOT import from historical computation modules
- Historical modules intentionally reuse current-market detection (one-way dependency)

---

## 5. Candle Integrity

### 5.1 Candle Model

**`src/engine/models/ohlcv.py`** — `OHLCVCandle` (frozen+slots dataclass):
- `timestamp: datetime` (timezone-aware UTC)
- `open, high, low, close: float`
- `volume: float`
- Validation: `high >= low`, `low <= open <= high`, `low <= close <= high`, `volume >= 0`

### 5.2 Integrity Guarantees

| Concern | Handling | File |
|---------|----------|------|
| Timestamp representation | All timestamps normalized to UTC | `historical_provider.py`, `data_provider.py` |
| Timezone handling | Naive timestamps rejected (historical) or interpreted as UTC (Yahoo daily) | `historical_provider.py` |
| Ordering | Candles sorted chronologically | `historical_adapter.py` |
| Duplicate candles | Deduplicated by timestamp (keep first) | `historical_validation.py` |
| Missing candles | Gap detection reported, not fabricated | `historical_gaps.py` |
| Incomplete/live candles | `split_completed_candles()` — forming candle excluded from engine | `data_provider.py` |
| Candle close boundary | `_latest_completed_before()` — strictly before evaluation time | `market_scanner.py` |
| Stale data | `FreshnessConfig` — CURRENT/STALE/UNAVAILABLE/INVALID classification | `data_provider.py` |
| Future timestamps | Rejected with `rejected_future_count` tracked | `data_provider.py` |
| Malformed data | Per-candle validation, invalid candles dropped with count | `data_provider.py` |
| Provider failures | `ProviderStatus.ERROR` reported, no silent fallback to fixtures | `data_provider.py` |
| Empty responses | `ProviderStatus.EMPTY` reported | `data_provider.py` |

### 5.3 Architectural Gaps

- **Volume analysis:** `src/engine/intelligence/volume.py` is an empty file. Volume structures are not yet implemented.
- **SwingQualityAnalyzer look-ahead:** Uses future candles for quality metrics, but this is controlled — the swing's `confirmation_index` ensures it is only available at or after confirmation point.

---

## 6. Symbol/Watchlist Scanning

### 6.1 Watchlist Definition

**`src/dashboard/watchlist.py`:**
- `Watchlist` class — mutable, deterministic
- `WatchlistSpec` — immutable snapshot
- `DEFAULT_WATCHLIST` — `("NIFTY",) + COMBINED_UNIVERSE` (NIFTY 50 ∪ SENSEX)
- Instruments stored canonical (stripped, upper-cased), de-duplicated, sorted lexicographically

### 6.2 Scanner Behavior

- **Iteration:** Sequential `for instrument in instruments` loop
- **Batching:** None (sequential processing)
- **Concurrency:** None (synchronous)
- **Per-symbol failure isolation:** `_scan_one()` catches exceptions per instrument
- **Duplicate handling:** Watchlist deduplicates at construction
- **Invalid symbols:** Reported as `available=False` with reason string
- **Unavailable symbols:** Reported as `ProviderStatus.ERROR/UNSUPPORTED/EMPTY`
- **Determinism:** Fully deterministic given same inputs and evaluation time

---

## 7. Timeframe/Chart Construction

### 7.1 Representation

The system does NOT use graphical charts. The "chart" is a structured representation:

```
InstrumentDataset
    ├── instrument: str
    ├── context_candles: tuple[OHLCVCandle, ...]  (higher timeframe, e.g., 1D)
    └── setup_candles: tuple[OHLCVCandle, ...]    (lower timeframe, e.g., 15M)
```

### 7.2 Timeframe Construction

- **Default context timeframe:** `"1D"` (configurable via `MarketScanConfig`)
- **Default setup timeframe:** `"15M"` (configurable via `MarketScanConfig`)
- **Minimum history:** 10 candles (`MarketScanConfig.min_history`)
- **Context fallback:** `_CONTEXT_FALLBACK` dict maps lower timeframes to higher ones
- **No fabrication:** Missing context = INCOMPLETE status, never fabricated

### 7.3 Look-Ahead Protection

- Higher-timeframe context: `_latest_completed_before(timestamp < T)` — strictly before
- Lower-timeframe setup: `_latest_completed_at_or_before(timestamp <= T)` — at or before
- Forming candle: excluded from engine, tracked separately for display only

---

## 8. Feature/Indicator/Structure Layer

### 8.1 Engine Inventory

| Engine | Sprint | Input | Output | Look-Ahead Safe |
|--------|--------|-------|--------|-----------------|
| `CandlePatternEngine` | 11O | `list[OHLCVCandle]` | `list[CandlePattern]` | Yes |
| `MarketContextEngine` | 11O-11P | `candles[:T+1]` | `MarketContext` | Yes |
| `SwingEngine` | 11P | `list[OHLCVCandle]` | `list[SwingPoint]` | Controlled |
| `MarketStructureEngine` | 11P | confirmed swings | `list[StructurePoint]` | Yes |
| `BOSEngine` | 11P | `StructureAnalysis` | `BOSResult` | Yes |
| `CHOCHEngine` | 11P | structures + BOS | `CHOCHResult` | Yes |
| `LiquidityEngine` | 11P | confirmed swings | `list[LiquidityPool]` | Yes |
| `RangeDetectionEngine` | 11P | structures + candle | `RangeContext` | Yes |
| `StructuralLevelsEngine` | 11P | confirmed swings | `list[StructuralLevel]` | Yes |
| `MarketTrendEngine` | 11P | analysis + range | `MarketTrend` | Yes |
| `SupportResistanceContextEngine` | 11P | structures + candle | `SupportResistanceContext` | Yes |
| `SetupConfluenceEngine` | 11Q | patterns + context | `SetupAssessment` | Yes |
| `TradeCandidateEngine` | 11R | assessment + context | `TradeCandidate` | Yes |
| `TradeDecisionEngine` | 11S | candidate | `TradeDecision` | Yes |
| `TradeOpportunityEngine` | 11T | decision | `TradeOpportunity` | Yes |
| `MTFAlignmentEngine` | 11U | higher context + direction | `MTFAlignment` | Yes |
| `ConfluenceEngine` | 11B | analysis + BOS + CHOCH + trend | `ConfluenceResult` | Yes |
| `TrendEngine` | 11A | analysis + BOS + CHOCH | `TrendResult` | Yes |
| `SignalEngine` | 11C | decision context | `SignalResult` | Yes |

### 8.2 Empty/Not Yet Implemented

- `src/engine/intelligence/volume.py` — empty file, no volume analysis
- `src/engine/intelligence/structural_strength.py` — referenced but not in intelligence layer

---

## 9. Setup Detectors

### 9.1 Detector Inventory

| Detector | File | Input | Output | Nature |
|----------|------|-------|--------|--------|
| `SetupConfluenceEngine` | `intelligence/setup_confluence.py` | patterns + context | `SetupAssessment` | Descriptive confluence |
| `TradeCandidateEngine` | `intelligence/trade_candidates.py` | assessment + context | `TradeCandidate` | Descriptive candidate with geometry |
| `TradeDecisionEngine` | `intelligence/trade_decision.py` | candidate | `TradeDecision` | Descriptive ranking/classification |
| `TradeOpportunityEngine` | `intelligence/trade_opportunity.py` | decision | `TradeOpportunity` | Descriptive filtering/ranking |
| `MarketScanner` | `intelligence/market_scanner.py` | datasets | `MarketScanResult` | Multi-instrument scan |

### 9.2 Detector Relationships

```
CandlePatternEngine ──→ SetupConfluenceEngine ──→ TradeCandidateEngine
MarketContextEngine ──→ SetupConfluenceEngine ──→ TradeCandidateEngine
                                                        │
                                                        ↓
                                              TradeDecisionEngine
                                                        │
                                                        ↓
                                              TradeOpportunityEngine
                                                        │
                                                        ↓
                                                   MarketScanner
                                                        │
                                              MTFAlignmentEngine (cross-timeframe)
```

- **Independence:** Each detector is independently callable
- **Chaining:** Detectors are chained via the `ScanEngines` bundle
- **Orchestration:** Central orchestration in `MarketScanner.scan()`
- **No trading decisions:** No detector produces BUY/SELL/ENTER/EXIT

---

## 10. Detector Output Contracts

### 10.1 Output Type Hierarchy

```
SetupAssessment
    ├── direction: SetupDirection (BULLISH/BEARISH/NEUTRAL/UNKNOWN)
    ├── classification: SetupClassification (NO_SETUP/WATCH/POTENTIAL_SETUP)
    ├── confluence_score: int [0,5]
    └── evidence: SetupEvidence (5 sources)

TradeCandidate
    ├── direction: CandidateDirection (LONG/SHORT/NONE)
    ├── status: CandidateStatus (NO_CANDIDATE/WATCH/CANDIDATE)
    ├── setup_type: SetupType
    ├── entry_reference: float | None
    ├── stop_reference: float | None
    ├── target_reference: float | None
    └── risk_reward_ratio: float | None

TradeDecision
    ├── classification: DecisionClassification (REJECTED/WATCH/QUALIFIED/PREFERRED)
    ├── score: DecisionScore (total [0,100])
    └── risk_reward_ratio: float | None

TradeOpportunity
    ├── status: OpportunityStatus (NO_OPPORTUNITY/WATCH/ALTERNATIVE_OPPORTUNITY/BEST_OPPORTUNITY)
    ├── eligibility: EligibilityStatus (ELIGIBLE/INELIGIBLE)
    └── rank: int

MarketScanResult
    ├── status: ScanStatus (OPPORTUNITIES_FOUND/WATCH_ONLY/NO_OPPORTUNITY/INCOMPLETE)
    ├── results: tuple[InstrumentScanResult, ...]
    ├── ranked: tuple[RankedScanOpportunity, ...]
    └── best: RankedScanOpportunity | None
```

### 10.2 Boundary Assessment

The output contract is **clean**. All outputs are descriptive classifications:
- `POTENTIAL_SETUP` ≠ "trade this"
- `CANDIDATE` ≠ "enter position"
- `QUALIFIED` ≠ "approved for execution"
- `BEST_OPPORTUNITY` ≠ "BUY now"

Every output type carries explicit documentation that it is "NOT a trade signal, NOT a prediction, NOT a BUY/SELL recommendation."

---

## 11. Trading-Semantics Contamination

### 11.1 Result: NO CONTAMINATION FOUND

The audit searched for all trading-semantics concepts in the detection path:

| Concept | Found in Detection? | Location | Classification |
|---------|---------------------|----------|----------------|
| BUY/SELL (as actions) | No | Only in liquidity classification (BUY_SIDE/SELL_SIDE) and explicit denials | Descriptive |
| Entry/Exit (as trade actions) | No | `entry_reference`, `stop_reference`, `target_reference` — geometric references only | Descriptive |
| Order | No | Only in explicit denials ("NEVER places a real order") | N/A |
| Position | No | Only in downstream `trade_planning.py` | Downstream |
| Stop Loss | No | `stop_reference` in detection; `stop_loss` only in `signal.py` (11C, separate pipeline) | Descriptive |
| Target/Take Profit | No | `target_reference` in detection; `take_profit` only in `signal.py` (11C) | Descriptive |
| Risk (management) | No | `risk_reward_ratio` is descriptive; position sizing only in `trade_planning.py` | Downstream |
| Portfolio | No | Explicitly "intentionally out of scope" | N/A |
| Execution | No | Only in explicit denials | N/A |
| Allocation | No | Not found in codebase | N/A |

### 11.2 Terminology Discipline

The codebase maintains strict terminology separation:

| Detection Layer | Downstream Layer |
|-----------------|------------------|
| `entry_reference` | `entry_price` (paper trading) |
| `stop_reference` | `stop_loss` (signal engine) |
| `target_reference` | `take_profit` (signal engine) |
| `CandidateDirection.LONG/SHORT` | `SignalDirection.LONG/SHORT` |
| `CandidateStatus` | `PaperTradeStatus` |
| `DecisionClassification` | `ActionabilityState` |

---

## 12. Current vs Historical Boundary

### 12.1 Dependency Direction

```
SHARED DOMAIN PRIMITIVES (OHLCVCandle, models)
        ↓                           ↓
   Current Market              Historical Research
        ↑                           │
        │                           │
        └─── (reused by) ──────────┘
```

### 12.2 Coupling Analysis

| Question | Answer |
|----------|--------|
| Does current-market import historical orchestration? | **NO** |
| Does historical import current-market orchestration? | **YES** — intentional reuse |
| Are providers coupled? | **NO** — separate protocols |
| Are interfaces reusable? | **YES** — `OHLCVCandle` is shared |

### 12.3 Historical Reuse of Current-Market

- `src/engine/data/setup_research.py` imports: `CandlePatternEngine`, `SetupConfluenceEngine`, `TradeCandidateEngine`, `TradeDecisionEngine`, `OutcomeEvaluator`
- `src/engine/intelligence/historical_replay.py` imports: `MarketScanner`, `InstrumentDataset`, `ScanEngines`

This is **architecturally sound**: historical research feeds historical candles through the existing current-market detection pipeline. The coupling is one-way (historical → current-market), never cyclic.

### 12.4 Frozen Files (Checkpoint 10.8)

The following historical research files should remain frozen:
- `src/engine/data/historical_setup_discovery.py`
- `src/engine/data/setup_research.py`
- `src/engine/intelligence/historical_replay.py`
- `src/engine/intelligence/historical_evidence.py`
- `src/engine/intelligence/historical_outcome.py`
- `src/engine/intelligence/performance_analytics.py`
- `src/engine/data/research_corpus.py`
- `src/engine/data/historical_store.py`
- `src/engine/data/historical_service.py`
- All `src/engine/models/historical*.py`

---

## 13. Scanner Orchestration

### 13.1 Orchestration Characteristics

| Aspect | Behavior |
|--------|----------|
| Scheduling | None — on-demand only |
| Scan lifecycle | Single synchronous call |
| Symbol iteration | Sequential loop |
| Timeframe iteration | Per instrument, context + setup |
| Detector execution | Via `ScanEngines` bundle |
| Concurrency | None — synchronous |
| Retries | None |
| Failures | Per-symbol isolation |
| Timeouts | None |
| Duplicate prevention | Watchlist deduplication |
| Idempotency | Deterministic given same inputs |
| Logging | Via dashboard service layer |
| Observability | `MarketScanResult.status` + per-instrument status |
| Persistence | None (on-demand, not persisted) |

### 13.2 Failure Isolation

One failed symbol or detector **cannot** terminate the entire scan. The `_scan_one()` helper catches exceptions per instrument and reports them as `available=False` rows.

---

## 14. Existing Test Coverage

### 14.1 Test Files (80+ files, 4000+ tests)

| Category | Test Files | Coverage |
|----------|------------|----------|
| Core Intelligence | `test_candle_patterns.py`, `test_bos.py`, `test_choch.py`, `test_swings.py`, `test_trend.py`, `test_liquidity.py`, `test_confluence.py`, `test_market_context.py`, `test_structure_analysis.py`, `test_support_resistance.py` | 11O-11P engines |
| Setup Pipeline | `test_setup_confluence.py`, `test_trade_candidates.py`, `test_trade_decision.py`, `test_trade_opportunity.py`, `test_market_scanner.py` | 11Q-11U pipeline |
| Signal | `test_signal.py` | 11C signal engine |
| Dashboard | `test_dashboard.py`, `test_watchlist_scanner.py`, `test_workstation.py`, `test_live_data_integration.py` | Product layer |
| Data Layer | `test_ohlcv.py`, `test_validator.py`, `test_historical_data_*.py` | Data integrity |
| Historical Research | `test_historical_setup_*.py` (12 files) | Frozen subsystem |
| Paper Trading | `test_paper_trading.py`, `test_paper_trading_operations.py` | Downstream |
| Validation | `test_backtest_validation.py`, `test_robustness_validation.py` | Offline validation |

### 14.2 Architectural Contracts Protected

| Contract | Test File |
|----------|-----------|
| Candle integrity (OHLC validation) | `test_ohlcv.py`, `test_validator.py` |
| Look-ahead safety | `test_candle_patterns.py`, `test_market_context.py`, `test_setup_confluence.py` |
| Descriptive-only outputs | `test_setup_confluence.py`, `test_trade_candidates.py`, `test_trade_decision.py`, `test_trade_opportunity.py` |
| No BUY/SELL in detection | `test_watchlist_scanner.py`, `test_dashboard.py` |
| Determinism | `test_market_scanner.py`, `test_dashboard.py` |
| Failure isolation | `test_live_data_integration.py`, `test_watchlist_scanner.py` |
| Current/historical separation | `test_live_data_integration.py` |
| Completed-candle boundary | `test_live_data_integration.py` |
| Forming-candle exclusion | `test_live_data_integration.py` |
| Future-timestamp rejection | `test_live_data_integration.py` |

---

## 15. Architectural Gaps

### 15.1 Evidence-Based Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| Volume analysis not implemented | Low | `src/engine/intelligence/volume.py` is empty. Volume structures are referenced in confluence but not computed. |
| No current-market persistence | Low | Scan results are not persisted; each scan is on-demand. This is intentional (no scheduling). |
| SignalEngine (11C) is separate pipeline | Info | The 11C signal pipeline produces `SignalResult` with entry/stop/target. It is NOT used by the 11O-11U pipeline. This is intentional — 11C is the older pipeline, 11O-11U is the newer "separated concern" approach. |
| No multi-instrument concurrency | Info | Scanning is sequential. For large watchlists, this could be slow. Not a correctness issue. |

### 15.2 Non-Gaps (Verified Clean)

- Trading-semantics contamination: **NONE**
- Current/historical coupling: **NONE** (one-way reuse only)
- Look-ahead bias: **NONE** (controlled in SwingEngine)
- Detector output contract: **CLEAN**
- Failure isolation: **PRESENT**
- Test coverage: **COMPREHENSIVE**

---

## 16. Target Architecture

### 16.1 Current State vs Desired State

The current architecture **already implements** the desired boundary:

```
CURRENT MARKET          →  DashboardDataProvider  →  InstrumentSeries
Candle Integrity        →  OHLCVCandle + split_completed_candles()
Chart/Market Context    →  MarketContextEngine (11P)
Feature/Structure Layer →  14 engines (11O-11T)
Setup Detection Engine  →  MarketScanner (11U)
Setup Candidate/Evidence →  MarketScanResult
─────────────────────────────────────────
DOWNSTREAM LAYER        →  TradePlanning → PaperTrading → Production
```

### 16.2 No Architectural Changes Required

The existing architecture is already aligned with the Checkpoint 11.1 target boundary. No refactoring is needed to establish the boundary — it already exists.

---

## 17. Proposed Contracts

### A. Market Data → Scanner

**Guarantees:**
- All candles are validated OHLCVCandle instances
- All timestamps are timezone-aware UTC
- Only completed candles (close_time <= evaluation_time) enter the engine
- Forming candles are excluded from analysis
- Future timestamps are rejected and counted
- Provider failures are reported honestly (no silent fallback)

### B. Scanner → Chart/Market Context

**Input guaranteed:**
- `context_candles`: chronologically sorted, completed, higher-timeframe candles
- `setup_candles`: chronologically sorted, completed, lower-timeframe candles
- Minimum `min_history` candles (default 10)

### C. Chart/Context → Detector

**Representation consumed:**
- `MarketContext` — descriptive snapshot at evaluation point T
- `SetupAssessment` — confluence of evidence at T
- All detectors operate on `candles[:T+1]` only (no future)

### D. Detector → Setup Candidate

**Detection guarantees:**
- `TradeCandidate` carries descriptive geometry (entry/stop/target references)
- `TradeDecision` carries evidence-strength classification
- `TradeOpportunity` carries eligibility and ranking
- All outputs are deterministic given same inputs
- No output constitutes a trading recommendation

### E. Setup Candidate → Downstream

**Information exposed:**
- `MarketScanResult` — ranked opportunities across instruments/timeframes
- Decision classification (REJECTED/WATCH/QUALIFIED/PREFERRED)
- Opportunity status (NO_OPPORTUNITY/WATCH/ALTERNATIVE/BEST)
- Geometry references (entry/stop/target)
- Confluence score, evidence counts, risk/reward ratio
- **NOT exposed:** BUY/SELL signals, execution instructions, position sizes

---

## 18. Files Requiring Future Changes

### 18.1 Files Likely Requiring Modification

| File | Reason |
|------|--------|
| `src/engine/intelligence/volume.py` | Empty file — volume analysis not yet implemented |
| `src/engine/config/market_scan_config.py` | May need additional configuration for new detectors |

### 18.2 Files That Should Remain Untouched

| File | Reason |
|------|--------|
| All `src/engine/data/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/models/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/intelligence/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/research/*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/data/setup_research*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/data/research_corpus*.py` | Frozen per Checkpoint 10.8 |
| All `docs/phase_6d_*` and `docs/phase_6e_*` | Frozen documentation |

### 18.3 New Files That May Be Required

| File | Justification |
|------|---------------|
| None currently justified | The existing architecture is sufficient |

---

## 19. Files That Should Remain Frozen

All historical research subsystem files (Checkpoint 10.8 freeze):

```
src/engine/data/historical_adapter.py
src/engine/data/historical_consumer.py
src/engine/data/historical_data_availability.py
src/engine/data/historical_evidence_lookup.py
src/engine/data/historical_fixtures.py
src/engine/data/historical_gaps.py
src/engine/data/historical_provider.py
src/engine/data/historical_serialization.py
src/engine/data/historical_service.py
src/engine/data/historical_setup_discovery.py
src/engine/data/historical_setup_outcome.py
src/engine/data/historical_store.py
src/engine/data/historical_times.py
src/engine/data/historical_validation.py
src/engine/data/research_corpus.py
src/engine/data/research_corpus_serialization.py
src/engine/data/research_corpus_store.py
src/engine/data/setup_research.py
src/engine/data/setup_research_serialization.py
src/engine/data/setup_research_store.py
src/engine/intelligence/historical_evidence.py
src/engine/intelligence/historical_outcome.py
src/engine/intelligence/historical_replay.py
src/engine/intelligence/performance_analytics.py
src/engine/intelligence/strategy_intelligence.py
src/engine/intelligence/decision_intelligence.py
src/engine/intelligence/decision_intelligence_integration.py
src/engine/intelligence/backtest_validation.py
src/engine/intelligence/robustness_validation.py
src/engine/intelligence/production_intelligence.py
src/engine/models/historical*.py (all 15+ files)
src/engine/research/*.py (all files)
```

---

## 20. Risks / Open Questions

### 20.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| SignalEngine (11C) confusion | Low | 11C is a separate pipeline; document clearly that 11O-11U is the current-market path |
| Volume analysis gap | Low | Volume structures referenced in confluence but not computed; implement when needed |
| Watchlist scalability | Low | Sequential scanning may be slow for large watchlists; not a correctness issue |

### 20.2 Open Questions

1. **Should the SignalEngine (11C) be integrated into the 11O-11U pipeline?** Currently it is separate. The 11C pipeline produces `SignalResult` with entry/stop/target, while 11O-11U deliberately stops at `MarketScanResult`. This is intentional but should be documented.

2. **Should scan results be persisted?** Currently scans are on-demand with no persistence. If historical tracking of scan results is needed, a persistence layer would be required.

3. **What is the relationship between `ConfluenceEngine` (11B) and `SetupConfluenceEngine` (11Q)?** Both exist. 11B is the older confluence engine; 11Q is the newer setup-specific version. Both are tested and functional.

---

## 21. Explicit Non-Goals

The following are explicitly **NOT** part of Checkpoint 11.1:

- Implementing BUY/SELL logic
- Implementing trade signals
- Implementing execution
- Implementing portfolio logic
- Implementing position sizing
- Implementing risk management
- Implementing stop-loss/target logic
- Redesigning the historical research subsystem
- Performing broad refactoring merely for cleanliness
- Creating speculative abstractions
- Proceeding to Checkpoint 11.2

---

## 22. Checkpoint 11.1 Conclusion

### Verdict: **PASS WITH LIMITATIONS**

The current-market setup detection boundary is **architecturally clean and well-implemented**. The system already implements the desired separation between detection and trading decisions.

### What Was Inspected

1. Project structure (80+ source files, 80+ test files)
2. Current-market entry points (DashboardAnalysisService, MarketScanner, CLI, FastAPI)
3. Complete data flow (Provider → Normalization → InstrumentSeries → Scanner)
4. Candle integrity (OHLCVCandle validation, split_completed_candles)
5. Symbol/watchlist scanning (Watchlist, sequential iteration, failure isolation)
6. Timeframe/chart construction (InstrumentDataset, context + setup candles)
7. Feature/indicator/structure layer (14 engines, 11O-11T)
8. Setup detectors (5 detectors, 11Q-11U)
9. Detector output contracts (SetupAssessment → TradeCandidate → TradeDecision → TradeOpportunity → MarketScanResult)
10. Trading-semantics contamination (NONE found)
11. Current vs historical boundary (one-way reuse, no coupling)
12. Scanner orchestration (on-demand, synchronous, failure-isolated)
13. Existing test coverage (4000+ tests, comprehensive)

### What the Current Architecture Actually Is

A strictly layered, descriptive-only pipeline that:
- Consumes live/near-live market data via `DashboardDataProvider`
- Normalizes and validates candles via `OHLCVCandle` + `split_completed_candles`
- Constructs market context via `MarketContextEngine` (11P)
- Detects setups via `SetupConfluenceEngine` (11Q)
- Generates candidates via `TradeCandidateEngine` (11R)
- Ranks decisions via `TradeDecisionEngine` (11S)
- Filters opportunities via `TradeOpportunityEngine` (11T)
- Scans multiple instruments via `MarketScanner` (11U)
- Terminates at `MarketScanResult` — a descriptive, ranked view

### What Boundary Currently Exists

The detection layer terminates at `MarketScanResult`. Downstream layers (trade planning, paper trading, production integration) consume these outputs without modifying them. No trading decisions are made in the detection layer.

### What Boundary Should Exist

The existing boundary is already correct. No architectural changes are required to establish it.

### Violations Found

**None.** No trading-semantics contamination, no current/historical coupling, no look-ahead bias, no boundary violations.

### Files That Would Need Changes

None for boundary establishment. `volume.py` (empty) may need implementation in future checkpoints.

### Existing Test Coverage

Comprehensive: 4000+ tests covering all detection layers, candle integrity, look-ahead safety, failure isolation, and the current/historical boundary.

### Required Future Tests

Tests for any new detectors or features added in future checkpoints. Current coverage is sufficient for the existing architecture.

### Questions for Review

1. Should the SignalEngine (11C) be integrated into the 11O-11U pipeline, or remain separate?
2. Should scan results be persisted for historical tracking?
3. What is the long-term relationship between ConfluenceEngine (11B) and SetupConfluenceEngine (11Q)?

---

**END OF CHECKPOINT 11.1**

**Next step:** Review this report before proceeding to Checkpoint 11.2.
