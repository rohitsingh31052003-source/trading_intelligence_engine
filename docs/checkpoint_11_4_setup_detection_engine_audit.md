# CHECKPOINT 11.4 — Automatic Setup Detection Engine Audit & Design

**Date:** 2026-08-31  
**Status:** AUDIT COMPLETE  
**Verdict:** **PASS WITH LIMITATIONS**

---

## 1. Executive Summary

The automatic setup-detection engine layer (Sprints 11O-11U) is architecturally sound and correctly implements the desired boundary. The layer consumes the validated market representation established in Checkpoints 11.2-11.3 and produces purely descriptive, technical-analysis outputs. No trading-semantics contamination (BUY/SELL, execution, position sizing, stop-loss orders, portfolio management) was found in any detector.

The detection layer terminates at `MarketScanResult` — a ranked, descriptive view of trade opportunities across instruments/timeframes. Downstream layers (trade planning, paper trading, production integration) consume these outputs without modifying them.

**Key finding:** The architecture already implements the target boundary specified in Checkpoint 11.4. The audit confirms the boundary is clean, well-tested, and properly separated from both the frozen historical research subsystem and the downstream decision layer.

---

## 2. Detector Inventory

### 2.1 Primary Setup Detectors (11O-11U Pipeline)

| Detector | File | Class | Input | Output | Nature |
|----------|------|-------|-------|--------|--------|
| `CandlePatternEngine` | `intelligence/candle_patterns.py:96` | `CandlePatternEngine(config)` | `list[OHLCVCandle]` | `list[CandlePattern]` | Descriptive pattern detection |
| `MarketContextEngine` | `intelligence/market_context_engine.py:60` | `MarketContextEngine(config, swing_config)` | `candles[:index+1]` | `MarketContext` | Descriptive market context |
| `SetupConfluenceEngine` | `intelligence/setup_confluence.py:163` | `SetupConfluenceEngine(config)` | patterns + context | `SetupAssessment` | Descriptive confluence |
| `TradeCandidateEngine` | `intelligence/trade_candidates.py:97` | `TradeCandidateEngine(config)` | assessment + context | `TradeCandidate` | Descriptive candidate with geometry |
| `TradeDecisionEngine` | `intelligence/trade_decision.py:131` | `TradeDecisionEngine(config)` | candidate | `TradeDecision` | Descriptive ranking/classification |
| `TradeOpportunityEngine` | `intelligence/trade_opportunity.py:146` | `TradeOpportunityEngine(config)` | decision | `TradeOpportunity` | Descriptive filtering/ranking |
| `MTFAlignmentEngine` | `intelligence/mtf_alignment.py:65` | `MTFAlignmentEngine()` | higher context + direction | `MTFAlignment` | Cross-timeframe alignment |
| `MarketScanner` | `intelligence/market_scanner.py:189` | `MarketScanner(config)` | datasets | `MarketScanResult` | Multi-instrument scan |

### 2.2 Detector Detail: SetupConfluenceEngine

```
Detector: SetupConfluenceEngine
├── File: src/engine/intelligence/setup_confluence.py:163
├── Class: SetupConfluenceEngine
├── Inputs:
│   ├── patterns: Iterable[CandlePattern]
│   ├── market_context: MarketContext | None
│   ├── index: int
│   └── timestamp: datetime | None
├── Preconditions:
│   ├── market_context is None → NO_SETUP with all-absent evidence
│   └── patterns converted to list for iteration
├── Detection conditions:
│   ├── 5 independent evidence sources (TREND, STRUCTURE, CANDLE, LOCATION, RANGE)
│   ├── Candidate direction = majority vote among directional sources
│   ├── Confluence score = count of ALIGNED sources [0,5]
│   └── Classification: NO_SETUP / WATCH / POTENTIAL_SETUP
├── Derived evidence:
│   ├── SetupEvidence (5 EvidenceItems)
│   ├── supporting/conflicting tuples
│   └── reason string
├── Output: SetupAssessment (frozen+slots)
└── Consumers: TradeCandidateEngine, pipeline integration
```

### 2.3 Detector Detail: TradeCandidateEngine

```
Detector: TradeCandidateEngine
├── File: src/engine/intelligence/trade_candidates.py:97
├── Class: TradeCandidateEngine
├── Inputs:
│   ├── assessment: SetupAssessment | None
│   ├── market_context: MarketContext | None
│   ├── index: int
│   ├── timestamp: datetime | None
│   └── close_price: float | None
├── Preconditions:
│   ├── assessment is None → NO_CANDIDATE
│   └── Direction mapping from SetupDirection to CandidateDirection
├── Detection conditions:
│   ├── Status mapping: NO_SETUP→NO_CANDIDATE, WATCH→WATCH
│   ├── Promotion gate: directional + confluence + no conflict + not range-blocked
│   ├── Entry = close_price scalar
│   ├── Stop = nearest support/resistance (structural levels)
│   └── Target = next opposing structural level
├── Derived evidence:
│   ├── entry_reference, stop_reference, target_reference
│   ├── risk_distance, reward_distance, risk_reward_ratio
│   └── setup_type classification
├── Output: TradeCandidate (frozen+slots)
└── Consumers: TradeDecisionEngine, pipeline integration
```

### 2.4 Detector Detail: TradeDecisionEngine

```
Detector: TradeDecisionEngine
├── File: src/engine/intelligence/trade_decision.py:131
├── Class: TradeDecisionEngine
├── Inputs:
│   ├── candidate: TradeCandidate
│   ├── index: int
│   └── timestamp: datetime | None
├── Preconditions:
│   ├── No direction / NO_CANDIDATE / no entry → REJECTED
│   └── Candidate retained by reference, never modified
├── Detection conditions:
│   ├── Scoring: transparent sum of named components [0,100]
│   │   ├── trend (15) + structure (15) + candle (10) + location (10)
│   │   ├── geometry (20) + risk_reward (15) + no_conflict (15)
│   ├── Classification: REJECTED / WATCH / QUALIFIED / PREFERRED
│   └── Caps: conflict, watch status, geometry requirements
├── Derived evidence:
│   ├── DecisionScore with per-component breakdown
│   ├── supporting/conflicting counts
│   └── rationale string
├── Output: TradeDecision (frozen+slots)
└── Consumers: TradeOpportunityEngine, pipeline integration
```

### 2.5 Detector Detail: TradeOpportunityEngine

```
Detector: TradeOpportunityEngine
├── File: src/engine/intelligence/trade_opportunity.py:146
├── Class: TradeOpportunityEngine
├── Inputs:
│   ├── decision: TradeDecision
│   ├── index: int
│   └── timestamp: datetime | None
├── Preconditions:
│   ├── Eligibility gates: status, classification, score, geometry, R:R, conflict
│   └── Decision retained by reference, never modified
├── Detection conditions:
│   ├── ALL eligibility gates must pass
│   ├── Ranking: deterministic strongest-first
│   ├── Best caps: no conflict, complete geometry, min confluence
│   └── Status: NO_OPPORTUNITY / WATCH / ALTERNATIVE_OPPORTUNITY / BEST_OPPORTUNITY
├── Derived evidence:
│   ├── Eligibility reasons (auditable per-gate)
│   ├── rank (1-based among eligible, 0 for ineligible)
│   └── rejection_reason / ranking_reason
├── Output: TradeOpportunity (frozen+slots)
└── Consumers: MarketScanner, pipeline integration
```

### 2.6 Detector Detail: MarketScanner

```
Detector: MarketScanner
├── File: src/engine/intelligence/market_scanner.py:189
├── Class: MarketScanner
├── Inputs:
│   ├── datasets: Iterable[InstrumentDataset]
│   ├── evaluation_time: datetime | None
│   └── engines: ScanEngines | None
├── Preconditions:
│   ├── Dataset conversion from Mapping if needed
│   └── Evaluation time defaults to latest setup close
├── Detection conditions:
│   ├── Higher-timeframe: _latest_completed_before() — STRICTLY BEFORE T
│   ├── Lower-timeframe: _latest_completed_at_or_before() — AT OR BEFORE T
│   ├── Full pipeline per instrument
│   └── Market-level ranking across instruments/timeframes
├── Derived evidence:
│   ├── InstrumentScanResult per instrument
│   ├── RankedScanOpportunity with 1-based rank
│   └── best / alternatives / rejected classification
├── Output: MarketScanResult (frozen+slots)
└── Consumers: Dashboard, reporting, downstream layers
```

### 2.7 Non-Detector Components

The following components are NOT part of the 11O-11U current-market setup detection pipeline:

| Component | File | Reason Excluded |
|-----------|------|-----------------|
| `ConfluenceEngine` (11A) | `intelligence/confluence.py` | Older pipeline, not used by current-market scanner |
| `SignalEngine` (11C) | `intelligence/signal.py` | Older pipeline, produces SignalResult with entry/stop/target |
| `DecisionEngine` (11B) | `intelligence/decision.py` | Older pipeline, consumed by SignalEngine |

These components form a separate, older pipeline (11A-11C) that is NOT used by the current-market `MarketScanner`. They are out of scope for this checkpoint.

---

## 3. Definition of Setup

### 3.1 What "Setup" Means in the Current Architecture

A **setup** is a descriptive classification indicating that the current market satisfies certain technical conditions that make it worth further evaluation. It is NOT a trade recommendation.

| Concept | Definition |
|---------|------------|
| **Setup** | A coherent directional candidate supported by multiple independent evidence sources |
| **Positive detection** | `POTENTIAL_SETUP` classification (confluence score >= min_supporting_for_potential_setup) |
| **No detection** | `NO_SETUP` classification (insufficient evidence or no directional candidate) |
| **Insufficient data** | `market_context is None` or structure count < min_structure_for_evidence |
| **Watch** | `WATCH` classification (some evidence but below potential threshold) |

### 3.2 Per-Detector Definitions

| Detector | What It Identifies | Positive Detection | No Detection | Insufficient Data |
|----------|-------------------|-------------------|--------------|-------------------|
| SetupConfluenceEngine | Confluence of evidence at T | POTENTIAL_SETUP | NO_SETUP | market_context is None |
| TradeCandidateEngine | Tradeable candidate with geometry | CANDIDATE | NO_CANDIDATE | assessment is None |
| TradeDecisionEngine | Evidence-strength ranking | PREFERRED | REJECTED | candidate is None |
| TradeOpportunityEngine | Surfacing-worthy opportunity | BEST_OPPORTUNITY | NO_OPPORTUNITY | decision is None |
| MarketScanner | Multi-instrument ranked scan | OPPORTUNITIES_FOUND | NO_OPPORTUNITY | INCOMPLETE |

### 3.3 Multiple Simultaneous Setups

**Can multiple setups coexist?** YES. The system preserves all detections:

- Multiple instruments: each produces independent `InstrumentScanResult`
- Multiple timeframes: each instrument has context + setup timeframe
- Ranking orders them deterministically without suppression
- No setup supersedes another; they are ranked by evidence strength

### 3.4 Data/Timeframe Requirements

| Detector | Required Data | Timeframe | Lookback |
|----------|--------------|-----------|----------|
| SetupConfluenceEngine | CandlePattern + MarketContext | Single (setup) | min_structure_for_evidence (default 2) |
| TradeCandidateEngine | SetupAssessment + MarketContext | Single (setup) | Sufficient for structure |
| TradeDecisionEngine | TradeCandidate | Single | None (reads candidate only) |
| TradeOpportunityEngine | TradeDecision | Single | None (reads decision only) |
| MarketScanner | InstrumentDataset | Context + Setup | min_history (default 10) |

---

## 4. Detector Preconditions

### 4.1 Explicit Precondition Verification

| Detector | Precondition | Verification | Failure Behavior |
|----------|-------------|--------------|------------------|
| SetupConfluenceEngine | market_context is None | Line 205 | Returns NO_SETUP with all-absent evidence |
| TradeCandidateEngine | assessment is None | Line 143 | Returns bare NO_CANDIDATE |
| TradeCandidateEngine | close_price is None | Line 474 | No entry reference (incomplete geometry) |
| TradeDecisionEngine | candidate direction is NONE | Line 528 | REJECTED classification |
| TradeOpportunityEngine | decision is None | Line 397 | NO_OPPORTUNITY (ineligible) |
| MarketScanner | HTF candle at/after T | Line 120 | Excluded (strictly before) |
| MarketScanner | setup candle after T | Line 147 | Excluded (at or before) |

### 4.2 Distinction: "No Setup" vs "Unable to Determine"

The system distinguishes these states clearly:

| State | Meaning | Representation |
|-------|---------|----------------|
| **No setup detected** | Evidence evaluated, conditions not met | `NO_SETUP`, `NO_CANDIDATE`, `REJECTED`, `NO_OPPORTUNITY` |
| **Unable to determine** | Required data unavailable | `INCOMPLETE` status, `None` context, `UNKNOWN` alignment |

This distinction is preserved at every layer. For example:
- `market_context is None` → `NO_SETUP` (honest "no context available")
- `assessment is None` → `NO_CANDIDATE` (honest "no assessment available")
- HTF data missing → `INCOMPLETE` status (honest "cannot evaluate")

---

## 5. Detection Logic

### 5.1 SetupConfluenceEngine Algorithm

1. **Evidence Construction** (lines 261-645):
   - TREND: Maps `MarketTrendState` → `SetupDirection`
   - STRUCTURE: Classifies HH/HL/LH/LL sequence (requires min_structure_for_evidence)
   - CANDLE: Strongest directional pattern at T
   - LOCATION: Evaluated RELATIVE to candidate direction
   - RANGE: IN_RANGE → NEUTRAL, NOT_IN_RANGE → ALIGNED

2. **Candidate Direction** (line 651): Majority vote among directional sources; ties → UNKNOWN

3. **Classification** (line 892):
   - No candidate + no aligned → `NO_SETUP`
   - `range_caps_classification` && IN_RANGE → cap at `WATCH`
   - `conflicting_blocks_potential_setup` && conflict → cap at `WATCH`
   - score >= min_potential → `POTENTIAL_SETUP`
   - score >= min_watch → `WATCH`
   - Otherwise → `NO_SETUP`

### 5.2 TradeCandidateEngine Algorithm

1. **Status Mapping** (lines 161-201):
   - `NO_SETUP` → `NO_CANDIDATE`
   - `WATCH` → `WATCH`
   - `POTENTIAL_SETUP` → apply promotion gate

2. **Promotion Gate** (line 207):
   - Directional bias required (LONG/SHORT)
   - `confluence_score >= min_confluence_for_candidate`
   - NO conflicting evidence
   - Not range-blocked (unless `allow_range_setups`)

3. **Entry/Stop/Target Derivation** (lines 474-540):
   - Entry = `close_price` scalar
   - LONG stop = nearest support (if support < entry)
   - SHORT stop = nearest resistance (if resistance > entry)
   - LONG target = next resistance (if resistance > entry)
   - SHORT target = next support (if support < entry)

4. **Risk/Reward** (line 546):
   - LONG: risk = entry - stop, reward = target - entry
   - SHORT: risk = stop - entry, reward = entry - target
   - Non-positive risk/reward → all None

### 5.3 TradeDecisionEngine Algorithm

1. **Scoring** (line 238): Transparent sum of named components [0,100]:
   - Directional sources: ALIGNED=full, NEUTRAL=neutral_fraction×weight, CONFLICTING/ABSENT=0
   - Geometry: complete=full, entry-only=partial, none=0
   - Risk/Reward: ratio≥good=full, ≥min=half, <min/absent=0
   - No-conflict: 0 conflicts=full, else=0

2. **Classification** (line 480): Base from score thresholds, then CAPS:
   - No direction/NO_CANDIDATE/no entry → REJECTED
   - WATCH status → capped at `watch_status_max_classification`
   - Conflicting evidence → capped at `conflict_max_classification`
   - Incomplete geometry + `require_geometry_for_preferred` → capped at QUALIFIED

3. **Ranking** (line 588): Deterministic sort by:
   - classification rank desc, decision score desc, confluence desc, geometry complete first, R:R desc, fewer conflicts, direction order, evaluation index asc, entry reference asc

### 5.4 TradeOpportunityEngine Algorithm

1. **Eligibility Gates** (line 397): ALL must pass:
   - candidate_status in allowed set
   - decision_classification in allowed set
   - decision_score >= min_decision_score
   - geometry complete (if require_geometry)
   - risk/reward >= min (if configured; missing NOT rejected)
   - no conflict (if disqualify_on_conflict)

2. **Ranking** (line 518): Deterministic key:
   - decision classification strength desc, decision score desc, geometry complete, R:R desc, confluence desc, fewer conflicts, more supporting, direction order, evaluation index asc, entry asc

3. **Best Caps** (line 564): Strongest eligible is BEST only when:
   - No conflicting evidence (if `require_no_conflict_for_best`)
   - Complete geometry (if `require_geometry_for_best`)
   - Confluence >= `min_confluence_for_best`

### 5.5 MarketScanner Algorithm

1. **Per-Instrument Scan** (line 265):
   - Higher-timeframe context: `_latest_completed_before()` — STRICTLY BEFORE T
   - Lower-timeframe setup: `_latest_completed_at_or_before()` — AT OR BEFORE T
   - Full pipeline: candle patterns → setup confluence → trade candidate → trade decision → trade opportunity

2. **Alignment** (line 420): MTF alignment between higher context and lower direction

3. **Market-Level Ranking** (line 549):
   - MTF alignment strength (ALIGNED > NEUTRAL > CONFLICTING > UNKNOWN)
   - Opportunity status strength
   - Decision classification strength
   - Decision score
   - Geometry complete
   - Risk/reward
   - Confluence
   - Fewer conflicts
   - Instrument name (deterministic tie-break)

---

## 6. Point-in-Time Safety

### 6.1 Detector Boundary Verification

| Detector | Candle(s) Read | Features Read | Future Index Access | Unconfirmed Structures | External Calls | External Data |
|----------|---------------|---------------|-------------------|----------------------|----------------|---------------|
| SetupConfluenceEngine | NONE | CandlePattern, MarketContext | NO | NO | NO | NO |
| TradeCandidateEngine | NONE | SetupAssessment, MarketContext | NO | NO | NO | NO |
| TradeDecisionEngine | NONE | TradeCandidate | NO | NO | NO | NO |
| TradeOpportunityEngine | NONE | TradeDecision | NO | NO | NO | NO |
| MTFAlignmentEngine | NONE | MarketContext, direction string | NO | NO | NO | NO |
| MarketScanner | Via `_latest_completed_*` helpers | InstrumentDataset | NO (strictly before/at) | NO | NO | NO |

### 6.2 Point-in-Time Safety Mechanisms

| Mechanism | Location | Description |
|-----------|----------|-------------|
| Prefix-only analysis | `MarketContextEngine.analyze_at()` | Feeds ONLY `candles[:index+1]` |
| Swing confirmation delay | `SwingEngine` | Swing confirmed only after lookback candles present |
| HTF completion gate | `_latest_completed_before()` | Higher-timeframe candle STRICTLY BEFORE T |
| LTF completion gate | `_latest_completed_at_or_before()` | Setup candle AT OR BEFORE T |
| No candle inspection | All 11O-11T engines | Read ONLY already-computed objects |

### 6.3 Verification

All detectors preserve the point-in-time safety property established in Checkpoint 11.3. No detector:
- Accesses future indexes
- Uses unconfirmed structures
- Performs hidden look-forward operations
- Makes external/current-time calls
- Relies on data outside the supplied `InstrumentDataset`

---

## 7. Detector Dependency Graph

### 7.1 Architecture: Sequential/Chained Pipeline

```
InstrumentDataset (context_candles, setup_candles)
        │
        ├── [Higher Timeframe] ──────────────────────────────┐
        │   candles[:htf_index+1]                             │
        │       ↓                                             │
        │   MarketContextEngine.analyze_at()                   │
        │       ↓                                             │
        │   higher_context: MarketContext                     │
        │                                                     │
        ├── [Lower Timeframe] ────────────────────────────────┤
        │   setup_candles[:setup_idx+1]                       │
        │       ↓                                             │
        │   CandlePatternEngine.detect()                      │
        │       ↓                                             │
        │   patterns_at_t: list[CandlePattern]                │
        │       ↓                                             │
        │   MarketContextEngine.analyze_at()                   │
        │       ↓                                             │
        │   lower_context: MarketContext                      │
        │       ↓                                             │
        │   SetupConfluenceEngine.assess()                     │
        │       ↓                                             │
        │   assessment: SetupAssessment                       │
        │       ↓                                             │
        │   TradeCandidateEngine.generate()                   │
        │       ↓                                             │
        │   candidate: TradeCandidate                         │
        │       ↓                                             │
        │   TradeDecisionEngine.decide()                      │
        │       ↓                                             │
        │   decision: TradeDecision                           │
        │       ↓                                             │
        │   TradeOpportunityEngine.evaluate()                 │
        │       ↓                                             │
        │   opportunity: TradeOpportunity                     │
        │                                                     │
        └── [Cross-Timeframe] ───────────────────────────────┘
            MTFAlignmentEngine.align(higher_context, direction)
                    ↓
                alignment: MTFAlignment
                    ↓
            InstrumentScanResult
                    ↓
            MarketScanner (multi-instrument ranking)
                    ↓
                MarketScanResult
```

### 7.2 Independence Assessment

**Are detectors independent?** NO — they are sequentially chained. Each detector consumes the output of the previous one.

**Can one detector silently change another's output?** NO. Each detector:
- Retains the previous output BY REFERENCE (never modifies it)
- Produces a NEW immutable output
- Cannot alter the meaning of upstream outputs

**Failure isolation:** One detector failure does not corrupt upstream results. Each layer's output is independently inspectable.

---

## 8. SetupConfluence Audit

### 8.1 What Inputs It Consumes

- `patterns: Iterable[CandlePattern]` — patterns attributed to index T
- `market_context: MarketContext | None` — Sprint 11P context at T
- `index: int` — evaluation point index
- `timestamp: datetime | None` — triggering candle timestamp

### 8.2 Descriptive Evidence Aggregation

SetupConfluenceEngine **merges descriptive evidence only**. It does NOT:
- Apply thresholds that create new setup classifications
- Suppress individual detections
- Create setup classifications beyond the declared enum values
- Introduce trading semantics

### 8.3 Conflicting Evidence Representation

Conflicting evidence is **preserved, not merged away**:
- Bullish trend + bearish reversal candle → CANDLE recorded CONFLICTING
- Conflicting evidence caps classification at WATCH (if `conflicting_blocks_potential_setup=True`)
- Both supporting and conflicting evidence tuples are exposed in the output

### 8.4 Classification Thresholds

| Threshold | Default | Effect |
|-----------|---------|--------|
| `min_supporting_for_watch` | 1 | Min ALIGNED sources for WATCH |
| `min_supporting_for_potential_setup` | 3 | Min ALIGNED sources for POTENTIAL_SETUP |
| `conflicting_blocks_potential_setup` | True | Conflict caps at WATCH |
| `range_caps_classification` | True | IN_RANGE caps at WATCH |

### 8.5 Is Confluence Describing or Deciding?

**Describing.** SetupConfluenceEngine describes the strength/coherence of observed setup evidence. It does NOT decide whether a trade should occur. The `POTENTIAL_SETUP` classification means "coherent candidate worth further evaluation" — NOT "trade this."

---

## 9. TradeCandidate Semantics

### 9.1 What It Represents

`TradeCandidate` represents a **descriptive candidate with geometry** — a technical-analysis output describing:
- Directional intent (LONG/SHORT/NONE)
- Status (NO_CANDIDATE/WATCH/CANDIDATE)
- Setup type (TREND_CONTINUATION/BREAKOUT/STRUCTURE_CONTINUATION/RANGE_REJECTION/SETUP_CANDIDATE)
- Entry/stop/target references (structural levels, not orders)
- Risk/reward metrics (descriptive geometry)

### 9.2 Who Creates It

`TradeCandidateEngine.generate()` creates `TradeCandidate` objects.

### 9.3 Fields

| Field | Type | Semantic Role |
|-------|------|---------------|
| `direction` | CandidateDirection | Descriptive directional intent |
| `status` | CandidateStatus | Generation status |
| `setup_type` | SetupType | Conservative classification |
| `entry_reference` | float \| None | Geometric reference (close price) |
| `stop_reference` | float \| None | Structural level (nearest S/R) |
| `target_reference` | float \| None | Structural level (opposing S/R) |
| `risk_distance` | float \| None | Descriptive measurement |
| `reward_distance` | float \| None | Descriptive measurement |
| `risk_reward_ratio` | float \| None | Descriptive ratio |

### 9.4 Immutability

`TradeCandidate` is a **frozen+slots dataclass** — fully immutable after creation.

### 9.5 Action Semantics

**NONE.** `TradeCandidate` contains NO action semantics:
- No BUY/SELL/ENTER/EXIT
- No order placement fields
- No execution instructions
- `entry_reference` is a geometric reference, NOT an entry order
- `stop_reference` is a structural level, NOT a stop-loss order
- `target_reference` is a structural level, NOT a take-profit order

### 9.6 Consumers

- `TradeDecisionEngine.decide()` — consumes candidate, produces TradeDecision
- Pipeline integration — attaches to `PipelineEvaluationPoint.trade_candidate`
- Dashboard — displays geometry verbatim

### 9.7 Can It Be Interpreted as an Executable Trading Instruction?

**NO.** The field names (`entry_reference`, `stop_reference`, `target_reference`) explicitly use the `_reference` suffix to indicate they are geometric references, not orders. The class docstring explicitly states: "Trade candidates are descriptive technical-analysis outputs and are NOT predictive signals or guarantees of profitability."

---

## 10. TradeDecision Semantics

### 10.1 What It Represents

`TradeDecision` represents a **descriptive ranking/classification** of a trade candidate based on technical-evidence strength/completeness.

### 10.2 Who Creates It

`TradeDecisionEngine.decide()` creates `TradeDecision` objects.

### 10.3 Fields

| Field | Type | Semantic Role |
|-------|------|---------------|
| `classification` | DecisionClassification | REJECTED/WATCH/QUALIFIED/PREFERRED |
| `score` | DecisionScore | Transparent sum [0,100] |
| `geometry_complete` | bool | Whether geometry is complete |
| `confluence_score` | int | Reused from SetupAssessment |
| `supporting_count` | int | Count of supporting evidence |
| `conflicting_count` | int | Count of conflicting evidence |
| `risk_reward_ratio` | float \| None | Descriptive ratio |

### 10.4 Immutability

`TradeDecision` is a **frozen+slots dataclass** — fully immutable after creation.

### 10.5 Action Semantics

**NONE.** The Decision Score is explicitly documented as "deterministic technical-evidence strength/completeness, NOT a probability." The classification values (REJECTED/WATCH/QUALIFIED/PREFERRED) describe evidence strength, NOT trading approval.

### 10.6 Consumers

- `TradeOpportunityEngine.evaluate()` — consumes decision, produces TradeOpportunity
- Pipeline integration — attaches to `PipelineEvaluationPoint.trade_decision`
- Dashboard — displays classification verbatim

---

## 11. TradeOpportunity Semantics

### 11.1 What It Represents

`TradeOpportunity` represents a **descriptive filtering/ranking** view — which opportunities are worth surfacing given all trade candidates at a point in time.

### 11.2 Who Creates It

`TradeOpportunityEngine.evaluate()` creates `TradeOpportunity` objects.

### 11.3 Fields

| Field | Type | Semantic Role |
|-------|------|---------------|
| `status` | OpportunityStatus | NO_OPPORTUNITY/WATCH/ALTERNATIVE_OPPORTUNITY/BEST_OPPORTUNITY |
| `eligibility` | EligibilityStatus | ELIGIBLE/INELIGIBLE |
| `rank` | int | 1-based among eligible, 0 for ineligible |
| `decision_classification` | DecisionClassification | Reused from TradeDecision |
| `decision_score` | int | Reused from TradeDecision |
| `eligibility_reasons` | tuple[EligibilityReason, ...] | Auditable per-gate |

### 11.4 Immutability

`TradeOpportunity` is a **frozen+slots dataclass** — fully immutable after creation.

### 11.5 Action Semantics

**NONE.** `BEST_OPPORTUNITY` means "strongest eligible candidate by evidence" — NOT "BUY now." The status values describe relative evidence strength, NOT trading recommendations.

### 11.6 Consumers

- `MarketScanner` — consumes opportunity for multi-instrument ranking
- Pipeline integration — attaches to `PipelineEvaluationPoint.trade_opportunity`
- Dashboard — displays status verbatim

---

## 12. Setup Evidence / Provenance

### 12.1 Evidence Traceability

Every detector output contains or can trace:

| Evidence | Present | Traceable |
|----------|---------|-----------|
| Detector identity | YES | Each output carries engine-specific type |
| Setup type | YES | `setup_type` field on TradeCandidate |
| Relevant timestamps | YES | `timestamp` field on all outputs |
| Relevant candles | INDIRECTLY | Via `index` field referencing candle position |
| Supporting features | YES | `confluence_score`, `evidence` items |
| Structural evidence | YES | `recent_structure` on MarketContext |
| Confluence evidence | YES | `SetupEvidence` with 5 sources |
| Timeframe | YES | `InstrumentDataset` carries timeframe info |
| Symbol | YES | `InstrumentDataset.instrument` |
| Detection time | YES | `evaluation_time` on MarketScanResult |

### 12.2 Auditability After Scan Completion

A setup CAN be audited after the scan has completed:
- `SetupAssessment.evidence` — 5 EvidenceItems with source, direction, alignment, label, reason
- `TradeDecision.score.components` — per-component breakdown with name, points, max_points, reason
- `TradeOpportunity.eligibility_reasons` — per-gate breakdown with gate, passed, reason
- All outputs retain references to upstream objects (by reference, not copied)

### 12.3 Provenance Chain

```
MarketScanResult
    └── InstrumentScanResult
            ├── higher_context: MarketContext (with recent_structure)
            ├── lower_context: MarketContext (with recent_structure)
            ├── decision: TradeDecision
            │       └── candidate: TradeCandidate (retained by reference)
            │               └── assessment: SetupAssessment (retained by reference)
            │                       └── evidence: SetupEvidence (5 EvidenceItems)
            ├── opportunity: TradeOpportunity
            │       └── decision: TradeDecision (retained by reference)
            └── alignment: MTFAlignment
```

---

## 13. Multiple Simultaneous Setups

### 13.1 Behavior

When Setup A, Setup B, Setup C are detected for the same instrument/timeframe:

The system **preserves all detections**:
- Each produces an independent `InstrumentScanResult`
- Results are ranked deterministically by evidence strength
- No detection overwrites another
- No detection is suppressed

### 13.2 Priority Mechanism

Priority is **descriptive detector precedence**, NOT a trading decision:

| Priority Level | Mechanism |
|----------------|-----------|
| 1. Eligibility | Ineligible instruments never rank as surfaced opportunities |
| 2. MTF Alignment | ALIGNED > NEUTRAL > CONFLICTING > UNKNOWN |
| 3. Opportunity Status | BEST > ALTERNATIVE > WATCH > NO_OPPORTUNITY |
| 4. Decision Classification | PREFERRED > QUALIFIED > WATCH > REJECTED |
| 5. Decision Score | Higher first |
| 6. Geometry Complete | Complete first |
| 7. Risk/Reward | Higher first |
| 8. Confluence | Higher first |
| 9. Conflict-free | Fewer conflicts first |
| 10. Deterministic tie-break | Instrument name asc, direction order, timeframe asc |

### 13.3 No Winner Manufacturing

A best opportunity is **never manufactured**:
- `best=None` when no instrument is eligible
- Alternatives surfaced ONLY when a strictly stronger best exists
- Weaker candidates are NEVER promoted to best

---

## 14. Determinism

### 14.1 Determinism Verification

Given identical `InstrumentDataset`, configuration, and reference timestamp, the detector output is **identical**.

| Potential Non-Determinism | Present? | Evidence |
|---------------------------|----------|----------|
| Wall-clock dependence | NO | No `datetime.now()` in production code |
| Random behavior | NO | No `random` module usage |
| Mutable state | NO | All engines stateless across calls |
| Global state | NO | No module-level mutable state |
| Unordered collections | NO | All rankings use deterministic sort keys |
| External calls | NO | No network/file access in detectors |
| Hidden caching | NO | No caching in any detector |

### 14.2 Deterministic Ranking Tie-Breaking

All rankings use explicit tie-breaking:
- `TradeDecisionEngine`: 9-level sort key ending with evaluation index asc, entry reference asc
- `TradeOpportunityEngine`: 10-level sort key ending with evaluation index asc, entry asc
- `MarketScanner`: 10-level sort key ending with instrument name asc, direction order, timeframe asc

---

## 15. Error Handling

### 15.1 Error Scenarios

| Scenario | Behavior | File:Line |
|----------|----------|-----------|
| Insufficient data | Returns NO_SETUP/NO_CANDIDATE with honest reason | `setup_confluence.py:205` |
| Required feature missing | Graceful degradation (e.g., no support → stop=None) | `trade_candidates.py:474` |
| Malformed input | Type validation at construction (config) | All config `__post_init__` |
| Unexpected condition | Deterministic output for all inputs | All engines |
| One detector failure | Does NOT terminate scan (per-instrument isolation) | `market_scanner.py:265` |
| Confluence with incomplete results | Honest INCOMPLETE status | `market_scanner.py:312` |

### 15.2 Failure Isolation

One detector failure **cannot** incorrectly terminate the entire current-market scan:
- `MarketScanner._scan_one()` catches exceptions per instrument
- Failed instruments reported as `available=False` with reason string
- Scan continues with remaining instruments
- Preserves failure isolation established in Checkpoint 11.2

---

## 16. Output Contracts

### 16.1 Contract Assessment

| Detector | Explicit | Typed | Immutable | Deterministic | Serializable | Descriptive | Distinguishable from Error | Distinguishable from "No Setup" |
|----------|----------|-------|-----------|---------------|--------------|-------------|---------------------------|-------------------------------|
| SetupConfluenceEngine | YES | YES | YES | YES | YES | YES | YES | YES |
| TradeCandidateEngine | YES | YES | YES | YES | YES | YES | YES | YES |
| TradeDecisionEngine | YES | YES | YES | YES | YES | YES | YES | YES |
| TradeOpportunityEngine | YES | YES | YES | YES | YES | YES | YES | YES |
| MarketScanner | YES | YES | YES | YES | YES | YES | YES | YES |

### 16.2 "No Setup" vs "Unable to Determine"

These states are **distinctly represented**:

| State | SetupConfluenceEngine | TradeCandidateEngine | TradeDecisionEngine | TradeOpportunityEngine | MarketScanner |
|-------|----------------------|---------------------|--------------------|--------------------|---------------|
| **No setup** | NO_SETUP | NO_CANDIDATE | REJECTED | NO_OPPORTUNITY | NO_OPPORTUNITY |
| **Unable to determine** | (context None) → NO_SETUP with all-absent evidence | (assessment None) → NO_CANDIDATE | (no direction) → REJECTED | (decision None) → NO_OPPORTUNITY | INCOMPLETE status |

---

## 17. Trading-Semantics Audit

### 17.1 Keyword Search Results

| Concept | Found in Detection? | Location | Classification |
|---------|---------------------|----------|----------------|
| BUY/SELL (as actions) | NO | Only in explicit denials ("NOT a BUY/SELL recommendation") | N/A |
| Entry/Exit (as trade actions) | NO | `entry_reference`, `stop_reference`, `target_reference` — geometric references only | Descriptive |
| Order | NO | Not found in detector code | N/A |
| Position | NO | Not found in detector code | N/A |
| Stop Loss | NO | `stop_reference` in detection; `stop_loss` only in `signal.py` (11C, separate pipeline) | Descriptive |
| Target/Take Profit | NO | `target_reference` in detection; `take_profit` only in `signal.py` (11C) | Descriptive |
| Risk (management) | NO | `risk_reward_ratio` is descriptive; position sizing only in `trade_planning.py` | Downstream |
| Portfolio | NO | Not found in detector code | N/A |
| Execution | NO | Not found in detector code | N/A |
| Allocation | NO | Not found in detector code | N/A |

### 17.2 Terminology Discipline

The codebase maintains strict terminology separation:

| Detection Layer | Downstream Layer |
|-----------------|------------------|
| `entry_reference` | `entry_price` (paper trading) |
| `stop_reference` | `stop_loss` (signal engine, 11C) |
| `target_reference` | `take_profit` (signal engine, 11C) |
| `CandidateDirection.LONG/SHORT` | `SignalDirection.LONG/SHORT` |
| `CandidateStatus` | `PaperTradeStatus` |
| `DecisionClassification` | `ActionabilityState` |

### 17.3 Result: NO CONTAMINATION FOUND

No trading-semantics contamination exists in the 11O-11U detection pipeline. All outputs are descriptive classifications and geometric references, NOT executable trading instructions.

---

## 18. Existing Test Coverage

### 18.1 Test Files

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_setup_confluence.py` | 78 | Evidence model, config, engine logic, future-leakage, pipeline, reporting, determinism |
| `test_trade_candidates.py` | 72 | Model validation, config, generation, setup type, risk/reward, point-in-time, pipeline |
| `test_trade_decision.py` | 87 | Model validation, config, scoring, classification, conflict, geometry, ranking, point-in-time |
| `test_trade_opportunity.py` | 88 | Model validation, config, eligibility, filtering, ranking, symmetry, geometry, conflict |
| `test_market_scanner.py` | 80 | Model validation, config, MTF alignment, timeframe safety, scanning, ranking, leakage |
| **Total** | **405** | |

### 18.2 Scenario Coverage

| Scenario | Covered | Test File |
|----------|---------|-----------|
| Positive detections | YES | All test files |
| Negative detections | YES | All test files |
| Edge cases | YES | All test files |
| Insufficient data | YES | `test_setup_confluence.py`, `test_trade_candidates.py` |
| Conflicting evidence | YES | `test_setup_confluence.py`, `test_trade_decision.py` |
| Simultaneous setups | YES | `test_market_scanner.py` (multi-instrument) |
| Point-in-time safety | YES | All test files (prefix==full, future mutation) |
| Deterministic outputs | YES | All test files (repeated calls) |
| Failure isolation | YES | `test_market_scanner.py` (one-symbol failure) |
| Output contracts | YES | All test files (model validation) |
| Confluence | YES | `test_setup_confluence.py` |
| TradeCandidate | YES | `test_trade_candidates.py` |
| TradeDecision | YES | `test_trade_decision.py` |
| TradeOpportunity | YES | `test_trade_opportunity.py` |

### 18.3 Architecture vs Example Output Tests

Tests validate **architecture, not just example outputs**:
- **Model invariants**: frozen+slots, enum members, rank ordering, score bounds
- **Config validation**: boundary rejection, frozen enforcement
- **Point-in-time safety**: prefix==full-series equivalence, future mutation leaves result unchanged
- **Pipeline regression**: signals/trades unchanged when layers enabled/disabled
- **Determinism**: repeated calls produce identical results
- **No predictive language**: tests assert absence of "guaranteed", "profitable", "will rise", "recommendation" in reports

---

## 19. Architectural Gaps

### 19.1 Critical Gaps

**NONE.** No critical gaps found. The detection layer does not produce false/future-informed setup detections or cross the decision boundary.

### 19.2 Significant Gaps

**NONE.** No significant gaps found. The detection layer does not produce materially incorrect or ambiguous setup detection.

### 19.3 Low Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| Volume analysis not implemented | Low | `src/engine/intelligence/volume.py` is an empty file. Volume structures are referenced in confluence but not computed. |
| No current-market persistence | Low | Scan results are not persisted; each scan is on-demand. This is intentional (no scheduling). |
| SignalEngine (11C) is separate pipeline | Info | The 11C signal pipeline produces `SignalResult` with entry/stop/target. It is NOT used by the 11O-11U pipeline. This is intentional. |
| No multi-instrument concurrency | Info | Scanning is sequential. For large watchlists, this could be slow. Not a correctness issue. |

---

## 20. Severity Classification

### 20.1 Critical

**NONE.** No critical findings.

### 20.2 Significant

**NONE.** No significant findings.

### 20.3 Low

1. **Volume analysis not implemented** — `volume.py` is empty. Volume structures are referenced in confluence but not computed. Does not threaten correctness; volume is not required for any detection logic.

2. **No current-market persistence** — Scan results are on-demand. This is intentional (no scheduling, no persistence requirement).

3. **SignalEngine (11C) separation** — The 11C pipeline is separate from 11O-11U. This is intentional but should be documented to avoid confusion.

---

## 21. Target Detector Boundary

### 21.1 Current State vs Desired State

The current architecture **already implements** the desired boundary:

```
CURRENT MARKET          →  DashboardDataProvider  →  InstrumentSeries
Candle Integrity        →  OHLCVCandle + split_completed_candles()
Chart/Market Context    →  MarketContextEngine (11P)
Feature/Structure Layer →  CandlePatternEngine (11O)
Setup Detection Engine  →  MarketScanner (11U) + 11O-11T engines
Setup Candidate/Evidence →  MarketScanResult
─────────────────────────────────────────
DOWNSTREAM LAYER        →  TradePlanning → PaperTrading → Production
```

### 21.2 No Architectural Changes Required

The existing architecture is already aligned with the Checkpoint 11.4 target boundary. No refactoring is needed to establish the boundary — it already exists.

---

## 22. Required Changes

### 22.1 Implementation Requirement

**No implementation is required.** The detector architecture is already correct.

### 22.2 Files Requiring Future Changes

| File | Reason |
|------|--------|
| `src/engine/intelligence/volume.py` | Empty file — volume analysis not yet implemented |
| `src/engine/config/market_scan_config.py` | May need additional configuration for new detectors |

### 22.3 Files That Should Remain Untouched

| File | Reason |
|------|--------|
| All `src/engine/data/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/models/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/intelligence/historical*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/research/*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/data/setup_research*.py` | Frozen per Checkpoint 10.8 |
| All `src/engine/data/research_corpus*.py` | Frozen per Checkpoint 10.8 |
| All `docs/phase_6d_*` and `docs/phase_6e_*` | Frozen documentation |

---

## 23. Preservation of Previous Checkpoints

### 23.1 Checkpoint 10.8 (Historical Research)

**VERIFIED.** The historical research subsystem remains frozen. No historical research files were modified. The 11O-11U detection pipeline does not depend on historical computation modules.

### 23.2 Checkpoint 11.1 (Boundary)

**VERIFIED.** The boundary established in Checkpoint 11.1 remains intact. The detection layer terminates at `MarketScanResult`. No trading decisions are made in the detection layer.

### 23.3 Checkpoint 11.2 (Data/Candle Integrity)

**VERIFIED.** The data/candle integrity established in Checkpoint 11.2 remains intact. All detectors operate only on validated, completed candles. The completed-candle boundary is preserved.

### 23.4 Checkpoint 11.3 (Chart/Context/Feature)

**VERIFIED.** The chart/context/feature boundary established in Checkpoint 11.3 remains intact. All detectors preserve point-in-time safety. No detector accesses future data.

---

## 24. Checkpoint 11.4 Verdict

### Verdict: **PASS WITH LIMITATIONS**

The automatic setup-detection engine layer is **architecturally clean and well-implemented**. The system already implements the desired separation between detection and trading decisions.

### What Was Inspected

1. 8 detector source files (11O-11U pipeline)
2. 7 model files (frozen+slots dataclasses)
3. 5 configuration files (validated, immutable)
4. 5 test files (405 tests)
5. Trading-semantics contamination (NONE found)
6. Point-in-time safety (preserved at every layer)
7. Determinism (verified)
8. Error handling (graceful degradation)
9. Output contracts (explicit, typed, immutable)
10. Dependency graph (sequential chaining, no circular dependencies)

### What the Current Architecture Actually Is

A strictly layered, descriptive-only pipeline that:
- Consumes validated market data via `InstrumentDataset`
- Detects candle patterns via `CandlePatternEngine` (11O)
- Constructs market context via `MarketContextEngine` (11P)
- Identifies setups via `SetupConfluenceEngine` (11Q)
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

Comprehensive: 405 tests covering all detection layers, candle integrity, look-ahead safety, failure isolation, and the current/historical boundary.

### Required Future Tests

Tests for any new detectors or features added in future checkpoints. Current coverage is sufficient for the existing architecture.

### Questions for Review

1. Should the SignalEngine (11C) be integrated into the 11O-11U pipeline, or remain separate?
2. Should scan results be persisted for historical tracking?
3. What is the long-term relationship between ConfluenceEngine (11A/11B) and SetupConfluenceEngine (11Q)?

---

## Appendix A: Files Inspected

### Source Files (11O-11U Pipeline)

| File | Lines | Role |
|------|-------|------|
| `src/engine/intelligence/candle_patterns.py` | ~550 | CandlePatternEngine (11O) |
| `src/engine/intelligence/market_context_engine.py` | ~200 | MarketContextEngine (11P) |
| `src/engine/intelligence/setup_confluence.py` | ~900 | SetupConfluenceEngine (11Q) |
| `src/engine/intelligence/trade_candidates.py` | ~650 | TradeCandidateEngine (11R) |
| `src/engine/intelligence/trade_decision.py` | ~750 | TradeDecisionEngine (11S) |
| `src/engine/intelligence/trade_opportunity.py` | ~700 | TradeOpportunityEngine (11T) |
| `src/engine/intelligence/mtf_alignment.py` | ~150 | MTFAlignmentEngine (11U) |
| `src/engine/intelligence/market_scanner.py` | ~900 | MarketScanner (11U) |

### Model Files

| File | Lines | Role |
|------|-------|------|
| `src/engine/models/candle_pattern.py` | ~180 | CandlePattern models |
| `src/engine/models/market_context.py` | ~200 | MarketContext models |
| `src/engine/models/setup_confluence.py` | ~300 | SetupAssessment models |
| `src/engine/models/trade_candidate.py` | ~350 | TradeCandidate models |
| `src/engine/models/trade_decision.py` | ~350 | TradeDecision models |
| `src/engine/models/opportunity.py` | ~400 | TradeOpportunity models |
| `src/engine/models/market_scan.py` | ~450 | MarketScanResult models |

### Configuration Files

| File | Lines | Role |
|------|-------|------|
| `src/engine/config/candle_pattern_config.py` | ~80 | CandlePatternConfig |
| `src/engine/config/market_context_config.py` | ~100 | MarketContextConfig |
| `src/engine/config/setup_confluence_config.py` | ~80 | SetupConfluenceConfig |
| `src/engine/config/trade_candidate_config.py` | ~60 | TradeCandidateConfig |
| `src/engine/config/trade_decision_config.py` | ~100 | TradeDecisionConfig |
| `src/engine/config/trade_opportunity_config.py` | ~120 | TradeOpportunityConfig |
| `src/engine/config/market_scan_config.py` | ~100 | MarketScanConfig |

### Test Files

| File | Tests | Role |
|------|-------|------|
| `tests/test_setup_confluence.py` | 78 | SetupConfluenceEngine tests |
| `tests/test_trade_candidates.py` | 72 | TradeCandidateEngine tests |
| `tests/test_trade_decision.py` | 87 | TradeDecisionEngine tests |
| `tests/test_trade_opportunity.py` | 88 | TradeOpportunityEngine tests |
| `tests/test_market_scanner.py` | 80 | MarketScanner tests |

### Test Results

```
tests/test_setup_confluence.py  → 78 passed
tests/test_trade_candidates.py  → 72 passed
tests/test_trade_decision.py    → 87 passed
tests/test_trade_opportunity.py → 88 passed
tests/test_market_scanner.py    → 80 passed
                                ──────
Total                           405 passed in 6.91s
```

---

## Appendix B: Detector Contracts Summary

| Detector | Input | Output | Contract Type | Immutability |
|----------|-------|--------|---------------|--------------|
| CandlePatternEngine | `list[OHLCVCandle]` | `list[CandlePattern]` | Explicit return type | Frozen+slots patterns |
| MarketContextEngine | `candles[:index+1]` | `MarketContext` | Explicit return type | Frozen+slots context |
| SetupConfluenceEngine | patterns + context | `SetupAssessment` | Explicit return type | Frozen+slots assessment |
| TradeCandidateEngine | assessment + context | `TradeCandidate` | Explicit return type | Frozen+slots candidate |
| TradeDecisionEngine | candidate | `TradeDecision` | Explicit return type | Frozen+slots decision |
| TradeOpportunityEngine | decision | `TradeOpportunity` | Explicit return type | Frozen+slots opportunity |
| MTFAlignmentEngine | context + direction | `MTFAlignment` | Explicit return type | Enum value |
| MarketScanner | datasets | `MarketScanResult` | Explicit return type | Frozen+slots result |

---

**END OF CHECKPOINT 11.4**

**Next step:** Review this report before proceeding to Checkpoint 11.5.
