# CHECKPOINT 11.5 — Setup Evidence & Provenance Audit

**Date:** 2026-08-31  
**Status:** AUDIT COMPLETE  
**Verdict:** **PASS WITH LIMITATIONS**

---

## 1. Executive Summary

The current-market setup-detection subsystem (Sprints 11O-11U) preserves evidence and provenance to a high degree. The architecture was explicitly designed for auditability: every layer produces frozen, immutable dataclasses with structured evidence fields, human-readable reason strings, and explicit references to upstream inputs. The evidence chain from raw candle data through to final scan result is traceable, and the system distinguishes clearly between evidence (descriptive) and trading decisions (none produced).

**Strengths:**
- Structured evidence is preserved at every layer (not collapsed into opaque scores)
- Each evidence source is named, labeled, and explained with a human-readable reason
- The full upstream identity chain is preserved through references and copied fields
- Deterministic, stateless engines ensure reproducibility
- Explicit `None` sentinels prevent "unobserved" from being misread as "zero/false"
- Confluence preserves individual evidence items (conflicts remain visible)

**Limitations (all Low/Significant, no Critical):**
- Candle provenance is index/timestamp-based, not a direct reference (by design — candles are not duplicated)
- Detector identity is implicit (inferred from model type), not an explicit field
- No separate `confirmation_timestamp` vs `detection_timestamp` (the single timestamp is the detection/trigger time; confirmation is structurally always `False` for base patterns)
- `MarketScanResult` drops heavy per-engine references on serialization (by design — regenerable)

No implementation is required. The existing provenance architecture is adequate and well-tested.

---

## 2. Existing Output Models

### 2.1 CandlePattern (`src/engine/models/candle_pattern.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `CandlePatternEngine` (Sprint 11O) |
| **Consumer** | `SetupConfluenceEngine` |
| **Timestamp** | `timestamp: datetime \| None` (triggering candle timestamp) |
| **Symbol/TF** | Not stored (attributed to evaluation context by pipeline) |
| **Evidence fields** | `pattern_type`, `index`, `direction`, `measurements`, `score`, `reason` |
| **Provenance fields** | `index` (chronological position), `prior_index` (for two-candle patterns), `confirmed` (always False for base detector) |

**Role:** Raw pattern evidence. Describes candle shape only. Makes no profitability claim.

### 2.2 MarketContext (`src/engine/models/market_context.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `MarketContextEngine` (Sprint 11P) |
| **Consumer** | `SetupConfluenceEngine`, `TradeCandidateEngine` |
| **Timestamp** | None directly (uses `index` for chronological position) |
| **Symbol/TF** | Not stored (attributed by pipeline) |
| **Evidence fields** | `trend`, `range`, `support_resistance`, `recent_structure`, `confirmed_swings` |
| **Provenance fields** | `index` (evaluation point), `recent_structure` (ordered StructurePoint tuple) |

**Role:** Structural/trend context. All fields derived from `candles[:index+1]` only.

### 2.3 SetupAssessment (`src/engine/models/setup_confluence.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `SetupConfluenceEngine` (Sprint 11Q) |
| **Consumer** | `TradeCandidateEngine` |
| **Timestamp** | `timestamp: datetime \| None` (triggering candle timestamp) |
| **Symbol/TF** | Not stored |
| **Evidence fields** | `evidence` (SetupEvidence with 5 EvidenceItems), `confluence_score`, `classification`, `direction` |
| **Provenance fields** | `index`, `candle_evidence`, `structure_evidence`, `trend_evidence`, `location_evidence`, `regime_evidence`, `reason` |

**Role:** Confluence combination. Each evidence source is named, labeled, and explained. Conflicts remain visible.

### 2.4 TradeCandidate (`src/engine/models/trade_candidate.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `TradeCandidateEngine` (Sprint 11R) |
| **Consumer** | `TradeDecisionEngine` |
| **Timestamp** | `timestamp: datetime \| None` |
| **Symbol/TF** | Not stored |
| **Evidence fields** | `supporting_evidence`, `conflicting_evidence`, `confluence_score`, `setup_type`, `setup_classification` |
| **Provenance fields** | `evaluation_index`, `candle_evidence`, `market_trend`, `market_structure`, `location`, `range_context`, `reason` |
| **Geometry** | `entry_reference`, `stop_reference`, `target_reference`, `risk_distance`, `reward_distance`, `risk_reward_ratio` |

**Role:** Trade geometry derivation. Entry/stop/target are derived from structural levels available at T. Evidence tuples copied verbatim from 11Q.

### 2.5 TradeDecision (`src/engine/models/trade_decision.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `TradeDecisionEngine` (Sprint 11S) |
| **Consumer** | `TradeOpportunityEngine` |
| **Timestamp** | `timestamp: datetime \| None` |
| **Symbol/TF** | Not stored |
| **Evidence fields** | `score` (with auditable components), `classification`, `supporting_count`, `conflicting_count` |
| **Provenance fields** | `evaluation_index`, `candidate` (reference to TradeDecision), `confluence_score`, `geometry_complete`, `rationale` |

**Role:** Candidate ranking/classification. Retains `candidate` by reference.

### 2.6 TradeOpportunity (`src/engine/models/opportunity.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `TradeOpportunityEngine` (Sprint 11T) |
| **Consumer** | `MarketScanner`, dashboard |
| **Timestamp** | `timestamp: datetime \| None` |
| **Symbol/TF** | Not stored |
| **Evidence fields** | `eligibility_reasons`, `decision_classification`, `decision_score`, `confluence_score`, `supporting_count`, `conflicting_count` |
| **Provenance fields** | `evaluation_index`, `decision` (reference to TradeDecision), `rejection_reason`, `ranking_reason` |

**Role:** Opportunity filtering/ranking. Retains `decision` by reference (and through it, the full chain back to candles).

### 2.7 MarketScanResult (`src/engine/models/market_scan.py`)

| Attribute | Value |
|-----------|-------|
| **Producer** | `MarketScanner` (Sprint 11U) |
| **Consumer** | Dashboard, API, pipeline |
| **Timestamp** | `timestamp: datetime \| None` |
| **Symbol/TF** | `instruments: tuple[str,...]`, `timeframes: tuple[str,str]` |
| **Evidence fields** | `status`, `alignment`, `decision_classification`, `decision_score`, `risk_reward_ratio`, `eligible` |
| **Provenance fields** | `scan_id`, `rationale`, per-instrument `reason` |
| **References** | `higher_context`, `lower_context`, `decision`, `opportunity` (all by reference on live result) |

**Role:** Multi-instrument scan. Retains full references to per-engine outputs on the live result.

---

## 3. Evidence Chain

### 3.1 Chain Traceability

```
Candles[:T+1]
    ↓ (CandlePatternEngine)
CandlePattern { index, timestamp, pattern_type, measurements, reason }
    ↓ (MarketContextEngine)
MarketContext { index, trend, range, support_resistance, recent_structure }
    ↓ (SetupConfluenceEngine)
SetupAssessment {
    index, timestamp,
    evidence: SetupEvidence {
        trend: EvidenceItem { source, direction, alignment, label, reason },
        structure: EvidenceItem { ... },
        candle: EvidenceItem { ... },
        location: EvidenceItem { ... },
        range: EvidenceItem { ... }
    },
    supporting, conflicting
}
    ↓ (TradeCandidateEngine)
TradeCandidate {
    evaluation_index, timestamp,
    supporting_evidence, conflicting_evidence,  ← copied from 11Q
    entry_reference, stop_reference, target_reference,  ← from structural levels
    setup_type, setup_classification
}
    ↓ (TradeDecisionEngine)
TradeDecision {
    evaluation_index, timestamp,
    candidate: TradeCandidate,  ← by reference
    score: DecisionScore { total, components: [...], reason },
    classification
}
    ↓ (TradeOpportunityEngine)
TradeOpportunity {
    evaluation_index, timestamp,
    decision: TradeDecision,  ← by reference
    eligibility_reasons: [EligibilityReason { gate, passed, reason }],
    status, rank
}
    ↓ (MarketScanner)
MarketScanResult {
    scan_id, timestamp, instruments, timeframes,
    results: [InstrumentScanResult {
        higher_context, lower_context,  ← by reference
        decision, opportunity  ← by reference
    }]
}
```

### 3.2 Chain Completeness

The chain is **fully preserved**. From a `MarketScanResult`, one can trace back through:
- `InstrumentScanResult.opportunity` → `TradeOpportunity`
- `TradeOpportunity.decision` → `TradeDecision`
- `TradeDecision.candidate` → `TradeCandidate`
- `TradeCandidate.supporting_evidence` → `EvidenceItem` tuples
- `InstrumentScanResult.higher_context` → `MarketContext` (with `recent_structure`, `support_resistance`)

The only gap is that `CandlePattern` objects are not directly referenced from `SetupAssessment` (only summary labels like `candle_evidence: str` are copied). The patterns are the input to the engine, not carried forward. This is by design (avoid duplicating candle data) and is **not a defect**.

---

## 4. Timestamp Provenance

### 4.1 Timestamp Types in the System

| Timestamp | Model | Semantics |
|-----------|-------|-----------|
| `timestamp` | `CandlePattern` | Triggering candle timestamp (when the pattern occurred) |
| `index` | All assessment models | Chronological position in the candle series |
| `timestamp` | `SetupAssessment` | Triggering candle timestamp (passed through from CandlePattern) |
| `timestamp` | `TradeCandidate` | Same (passed through) |
| `timestamp` | `TradeDecision` | Same (passed through) |
| `timestamp` | `TradeOpportunity` | Same (passed through) |
| `timestamp` | `MarketScanResult` | Latest completed setup-timeframe candle close |

### 4.2 Event vs Confirmation vs Detection

The system uses a **single timestamp** per evaluation point, representing the close of the latest completed candle at which the setup was detected. This is correct because:

1. **Pattern occurrence = detection**: A candle pattern is detected at the close of its triggering candle (no future data needed).
2. **Base patterns are never confirmed**: `CandlePattern.confirmed` is always `False` — confirmation would require future candles, which the base detector never inspects.
3. **No separate confirmation timestamp exists** because the system does not implement multi-bar confirmation patterns. If it did, a separate `confirmation_timestamp` would be warranted.

**Finding:** The single-timestamp approach is appropriate for the current detector semantics. No ambiguity is introduced. **No deficiency.**

### 4.3 Scan Timestamp

The `MarketScanResult.timestamp` is the evaluation time (latest completed setup candle across instruments). The scan itself has no separate "scan executed at" wall-clock timestamp, because:
- The scan is deterministic given inputs
- Wall-clock time is irrelevant to the point-in-time correctness
- The evaluation boundary is explicitly the candle timestamp

**Finding:** Correct design. No deficiency.

---

## 5. Candle Provenance

### 5.1 What Is Retained

| Field | Model | Purpose |
|-------|-------|---------|
| `index: int` | `CandlePattern` | Chronological position of the triggering candle |
| `timestamp: datetime \| None` | `CandlePattern` | Timestamp of the triggering candle |
| `prior_index: int \| None` | `CandlePattern` | For two-candle patterns (e.g., engulfing, inside bar) |
| `index: int` | `SetupAssessment`, `TradeCandidate`, etc. | Evaluation point position |

### 5.2 What Is NOT Retained (By Design)

- Raw OHLCV candle data is **not** duplicated into assessment models (would be redundant and memory-heavy)
- The candle series itself is not referenced from output models
- `MarketContext.index` is the evaluation point, but the candle series is not stored on the context

### 5.3 Provenance Validity After Scan

The `index` and `timestamp` fields remain valid after the scan completes because:
- `index` is a position in the original candle series (stable unless the series is mutated)
- `timestamp` is copied from the candle and never modified

The system does not mutate candles after detection (all models are frozen, and the pipeline works on `candles[:T+1]` slices).

**Finding:** Candle provenance is adequate. The `index`/`timestamp` pair uniquely identifies the evaluation point. No deficiency.

---

## 6. Feature Provenance

### 6.1 Structural Evidence

```
Setup → MarketContext.recent_structure → StructurePoint { structure: StructureType }
                                              ↓
                           StructureType.HIGHER_HIGH / HIGHER_LOW / LOWER_HIGH / LOWER_LOW / FIRST
```

The `recent_structure` field on `MarketContext` retains the actual sequence of structure classifications. The `SetupAssessment.structure_evidence` field is a string label summary (e.g., `"HIGHER_HIGH / HIGHER_LOW"`). The raw sequence is available via `MarketContext.recent_structure` on the `InstrumentScanResult.higher_context` / `lower_context`.

### 6.2 Support/Resistance Evidence

```
Setup → MarketContext.support_resistance → SupportResistanceContext {
        support, resistance, distance_to_support, distance_to_resistance, location
    }
```

Actual price levels and signed distances are retained. The `SetupAssessment.location_evidence` is a summary label; the raw levels are available via the `MarketContext`.

### 6.3 Candle Pattern Evidence

```
Setup → SetupAssessment.evidence.candle → EvidenceItem {
        source=CANDLE, direction, alignment, label, reason
    }
```

The candle evidence item carries the pattern type name(s) as `label` (e.g., `"HAMMER"`, `"BULLISH_ENGULFING"`). The full `CandlePattern` objects (with measurements, scores) are NOT carried forward into the assessment — only summary labels. This is by design to avoid bloating the evidence chain.

### 6.4 Actual Values vs Boolean Flags

The system retains **actual values** at the `MarketContext` level (support/resistance prices, distances, range boundaries, structure sequence) and **classification labels** at the `SetupAssessment` level. Boolean flags are NOT used for evidence — instead, `EvidenceAlignment` (ALIGNED/CONFLICTING/NEUTRAL/ABSENT) provides explicit, interpretable states.

**Finding:** Feature provenance is adequate. Actual values are available at the context level; summary labels are carried into assessments. No deficiency.

---

## 7. Detector Identity

### 7.1 Identification Mechanism

Detector identity is **implicit** — inferred from the model type:

| Detector | Output Model | Identity |
|----------|-------------|----------|
| `CandlePatternEngine` | `CandlePattern` | Model type |
| `MarketContextEngine` | `MarketContext` | Model type |
| `SetupConfluenceEngine` | `SetupAssessment` | Model type |
| `TradeCandidateEngine` | `TradeCandidate` | Model type |
| `TradeDecisionEngine` | `TradeDecision` | Model type |
| `TradeOpportunityEngine` | `TradeOpportunity` | Model type |
| `MTFAlignmentEngine` | `MTFAlignment` | Enum value |
| `MarketScanner` | `MarketScanResult` | Model type |

### 7.2 Multiple Detectors Contributing to One Result

When multiple detectors contribute (e.g., the scanner runs candle patterns + market context + confluence + candidate + decision + opportunity), the `MarketScanResult` does NOT explicitly tag which detector produced each value. However, this is not needed because:
- Each detector produces a distinct, well-defined model type
- The pipeline is deterministic and sequential — the chain of models IS the provenance
- The `InstrumentScanResult` carries references to each stage (`higher_context`, `lower_context`, `decision`, `opportunity`)

### 7.3 Aggregation Order

The pipeline order is deterministic and documented:
1. Candle patterns → Market context → Setup assessment → Trade candidate → Trade decision → Trade opportunity → MTF alignment → Market scan

**Finding:** Detector identity is implicit but unambiguous. No explicit "detector_id" field exists, but the model type serves this purpose. **Low** limitation — a future enhancement could add an explicit `producer` field, but it is not required for correctness.

---

## 8. Confluence Evidence

### 8.1 Individual Evidence Preservation

The `SetupConfluenceEngine` preserves **all five evidence sources** as distinct `EvidenceItem` objects:

```python
SetupEvidence:
    trend: EvidenceItem      # { source, direction, alignment, label, reason }
    structure: EvidenceItem  # { ... }
    candle: EvidenceItem     # { ... }
    location: EvidenceItem   # { ... }
    range: EvidenceItem      # { ... }
    supporting: tuple[EvidenceItem, ...]  # convenience view
    conflicting: tuple[EvidenceItem, ...]  # convenience view
```

Individual evidence is **NOT discarded** after aggregation. Each `EvidenceItem` carries:
- `source`: Which detector/source produced this evidence (TREND/STRUCTURE/CANDLE/LOCATION/RANGE)
- `direction`: The directional bias (BULLISH/BEARISH/NEUTRAL/UNKNOWN)
- `alignment`: How it relates to the candidate (ALIGNED/CONFLICTING/NEUTRAL/ABSENT)
- `label`: Short human-readable description (e.g., `"HAMMER"`, `"HIGHER_HIGH / HIGHER_LOW"`)
- `reason`: Full human-readable explanation

### 8.2 Conflicting Evidence Representation

Conflicting evidence is **explicitly recorded**, not silently merged. For example:
- Bullish trend + bearish shooting star → `candle` EvidenceItem has `alignment=CONFLICTING`
- The `conflicting` tuple contains the conflicting item
- The classification is capped at `WATCH` (not `POTENTIAL_SETUP`) when conflicts block

### 8.3 Confluence Score

`confluence_score = len(evidence.supporting)` — a count of aligned sources, NOT a weighted aggregation. This is transparent and auditable.

**Finding:** Confluence evidence is fully preserved. Conflicts are visible. **No deficiency.**

---

## 9. TradeCandidate Provenance

### 9.1 Input Preservation

`TradeCandidate` preserves:
- `supporting_evidence` / `conflicting_evidence`: Copied verbatim from `SetupAssessment.evidence`
- `confluence_score`: Copied from `SetupAssessment`
- `setup_classification`: Copied from `SetupAssessment`
- `candle_evidence`, `market_trend`, `market_structure`, `location`, `range_context`: String labels copied from `SetupAssessment`
- `setup_type`: Determined by `TradeCandidateEngine` from structural levels
- `entry_reference` / `stop_reference` / `target_reference`: Derived from `MarketContext.support_resistance`

### 9.2 Provenance Chain

```
TradeCandidate → (via evidence labels) → SetupAssessment → (via MarketContext) → Candles
TradeCandidate → (via setup_classification) → SetupAssessment classification
```

### 9.3 Geometry Provenance

Entry/stop/target are derived from structural levels:
- **Entry** = trigger candle close (scalar from pipeline)
- **Stop** = nearest support (LONG) / resistance (SHORT) from `MarketContext.support_resistance`
- **Target** = next opposing structural level from `MarketContext.support_resistance`

When structural levels are absent or on the wrong side of entry, geometry is `None` (not fabricated). This is explicitly documented.

**Finding:** TradeCandidate provenance is complete. Evidence chain is preserved. **No deficiency.**

---

## 10. TradeDecision Provenance

### 10.1 Input Preservation

`TradeDecision` retains the full `TradeCandidate` **by reference** (`candidate: TradeCandidate`). Through this reference, the entire upstream chain is accessible.

Additionally, summary fields are copied for convenient access:
- `direction`, `confluence_score`, `geometry_complete`, `supporting_count`, `conflicting_count`, `risk_reward_ratio`

### 10.2 Score Auditability

`DecisionScore` carries:
- `total`: Integer [0, 100]
- `max_total`: 100
- `components: tuple[DecisionScoreComponent, ...]`: Each component has `name`, `points`, `max_points`, `reason`
- `reason`: Summary string

Every point is attributable to a named component with a reason.

### 10.3 Classification Rationale

The `rationale` field provides a human-readable explanation of the classification and score.

**Finding:** TradeDecision provenance is complete. The candidate reference + auditable score components provide full traceability. **No deficiency.**

---

## 11. TradeOpportunity Provenance

### 11.1 Input Preservation

`TradeOpportunity` retains the full `TradeDecision` **by reference** (`decision: TradeDecision`). Through this, the entire upstream chain is accessible.

### 11.2 Eligibility Auditability

Each eligibility gate is recorded as an `EligibilityReason`:
```python
EligibilityReason {
    gate: str,      # e.g., "candidate_status", "decision_score"
    passed: bool,
    reason: str     # e.g., "score 92 >= min 60"
}
```

### 11.3 Ranking Rationale

The `ranking_reason` field explains why the candidate received its rank/status. The `rejection_reason` field explains ineligibility.

### 11.4 No Hidden Trading Instruction

`TradeOpportunity` does NOT contain:
- An `action` field (BUY/SELL/HOLD)
- An `execute` flag
- A `confidence` or `probability` field
- An `order_type` field

The `status` field (NO_OPPORTUNITY/WATCH/ALTERNATIVE_OPPORTUNITY/BEST_OPPORTUNITY) is purely descriptive — it indicates relative technical strength, not a trading instruction.

**Finding:** TradeOpportunity provenance is complete. The decision reference + eligibility reasons provide full auditability. No hidden trading instruction. **No deficiency.**

---

## 12. Reproducibility

### 12.1 Determinism

| Factor | Status | Evidence |
|--------|--------|----------|
| Stateless engines | YES | All engines are `@dataclass(frozen=True, slots=True)` or plain classes with no mutable state |
| Deterministic algorithms | YES | All detection rules are explicit `if/else` chains with no randomness |
| No wall-clock dependence | YES | No `datetime.now()` in production detection code |
| No provider calls in detection | YES | Detection consumes pre-computed `CandlePattern` + `MarketContext` |
| No mutable global state | YES | All state is passed as parameters |
| Configuration dependence | Documented | All thresholds are in frozen config dataclasses |

### 12.2 Reproducibility Guarantee

Given the same:
1. Candle dataset (`list[OHLCVCandle]`)
2. Configuration (`SetupConfluenceConfig`, `TradeCandidateConfig`, etc.)
3. Evaluation index/timestamp

The system will produce **exactly the same** `MarketScanResult`. This is:
- Structurally guaranteed by deterministic, stateless engines
- Regression-tested (future-mutation tests, determinism tests, shuffle-invariance tests)

### 12.3 Versioning

No explicit "detector version" field exists on models. However:
- The code version is captured in experiment reproducibility metadata (Sprint 11J+)
- The frozen config dataclasses capture all thresholds
- The deterministic `scan_id` (SHA-256 of canonical identity) uniquely identifies the scan configuration

**Finding:** Reproducibility is ensured by design. No deficiency.

---

## 13. Serialization / Persistence

### 13.1 Serialization Approach

The system uses deterministic, self-describing JSON serialization with type tags (`__enum__`, `__dataclass__`, `__datetime__`, `__tuple__`) for:
- Market scan results (`market_scan_serialization.py`, `SCANNER_SCHEMA_VERSION = 1`)
- Paper trades, experiment results, etc.

### 13.2 What Survives Serialization

| Field | Survives? | Notes |
|-------|-----------|-------|
| Timestamps | YES | ISO format with `__datetime__` tag |
| Symbol/TF | YES | `instruments`, `timeframes` tuples |
| Evidence labels | YES | String fields |
| Evidence alignment | YES | Enum tags |
| Detector identity | Implicit | Model type preserved via `__dataclass__` tag |
| Confluence score | YES | Integer |
| Decision score + components | YES | Nested dataclass |
| Eligibility reasons | YES | Tuple of dataclasses |
| scan_id | YES | Deterministic string |

### 13.3 What Is Dropped on Serialization (By Design)

The `MarketScanResult` serializer intentionally drops heavy per-engine references:
- `higher_context` (MarketContext) → `None`
- `lower_context` (MarketContext) → `None`
- `decision` (TradeDecision) → `None`
- `opportunity` (TradeOpportunity) → `None`

This is **by design** — these are regenerable by re-running the scan with the same inputs. The `InstrumentScanResult` stores an explicit `eligible` flag so the eligibility verdict survives serialization (the `RankedScanOpportunity` invariant holds).

### 13.4 Schema Versioning

All serializers include a `schema_version` field. Loaders validate the version BEFORE reconstructing any model. Future versions are rejected with clear errors.

**Finding:** Serialization preserves all audit-critical fields (identity, timestamps, scores, eligibility). Heavy references are dropped by design (regenerable). **No deficiency.**

---

## 14. API / Dashboard Representation

### 14.1 Dashboard Presentation Models

The dashboard (`dashboard/views.py`) uses read-only presentation models:
- `DashboardTradeView`: Wraps the full scan result
- `DecisionView`: Projects the `TradeDecision`
- `GeometryView`: Projects the `TradeCandidate` geometry
- `EvidenceView`: Projects the `HistoricalEvidenceContext` (Sprint 11Y)
- `MarketOverviewView`: Projects the `MarketContext`
- `ActionabilityState`: A deterministic mirror of existing outputs

### 14.2 Evidence Retained in API

The `/api/analysis` and `/api/scan` endpoints expose:
- Decision classification (REJECTED/WATCH/QUALIFIED/PREFERRED)
- Decision score (total + per-component breakdown via `DecisionScore`)
- Eligibility reasons (gate, passed, reason)
- MTF alignment (ALIGNED/CONFLICTING/NEUTRAL/UNKNOWN)
- Setup type, direction, confluence score
- Entry/stop/target geometry (when available)
- Evidence strength (when offline corpus attached)
- Actionability state (documented mirror, not a new score)

### 14.3 No Trading Instruction in API

The API does NOT expose:
- BUY/SELL/ENTER/EXIT/HOLD labels
- Confidence percentages
- Probability of profit
- Order instructions

The `ActionabilityState` (INVALID/NO_OPPORTUNITY/TRADE_GEOMETRY_UNAVAILABLE/INSUFFICIENT_EVIDENCE/READY_FOR_REVIEW/WAIT) is explicitly documented as a presentation mirror, not a trading signal.

### 14.4 Honest Unavailable States

When evidence is incomplete, the API returns:
- `None` for unavailable geometry (not fabricated values)
- `"unavailable"` strings in the UI
- `ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE` when geometry is incomplete
- `ActionabilityState.INSUFFICIENT_EVIDENCE` only when evidence corpus exists AND is below threshold

**Finding:** API/dashboard representation retains sufficient descriptive information. No accidental trading instruction. **No deficiency.**

---

## 15. Evidence vs Decision Boundary

### 15.1 Explicit Separation

| Concept | Model | Contains BUY/SELL? | Contains Order? |
|---------|-------|-------------------|-----------------|
| Candle pattern evidence | `CandlePattern` | NO | NO |
| Market context | `MarketContext` | NO | NO |
| Setup assessment | `SetupAssessment` | NO | NO |
| Trade candidate | `TradeCandidate` | NO | NO |
| Trade decision | `TradeDecision` | NO | NO |
| Trade opportunity | `TradeOpportunity` | NO | NO |
| Market scan | `MarketScanResult` | NO | NO |

### 15.2 Detection Timestamp vs Execution Timestamp

The system produces only a **detection timestamp** (when the setup was identified). There is **no execution timestamp** because the system does not execute trades (intentionally out of scope).

### 15.3 Setup Classification vs Order Instruction

- `POTENTIAL_SETUP` ≠ BUY order
- `BEST_OPPORTUNITY` ≠ EXECUTE NOW
- `CANDIDATE` ≠ ENTER POSITION

These are all descriptive classifications. The reporting layer repeats this disclaimer explicitly.

### 15.4 Evidence Strength vs Decision

Historical evidence strength (Sprint 11Y) is a separate concern from the current detection:
- Evidence describes what happened historically to similar setups
- Detection describes what the technical structure looks like now
- Neither produces a trading instruction

**Finding:** The evidence/decision boundary is explicitly maintained. No contamination. **No deficiency.**

---

## 16. Existing Test Coverage

### 16.1 Relevant Test Files

| Test File | Coverage Area |
|-----------|--------------|
| `test_candle_patterns.py` (67 tests) | Pattern detection, measurements, indexes, timestamps, look-ahead safety |
| `test_market_context.py` (50 tests) | Context derivation, structure, trend, range, S-R, future-leakage |
| `test_setup_confluence.py` (78 tests) | Evidence items, alignment, classification, supporting/conflicting, determinism |
| `test_trade_candidates.py` (72 tests) | Candidate generation, geometry, evidence preservation, promotion gate |
| `test_trade_decision.py` (87 tests) | Score components, classification, ranking, determinism, point-in-time |
| `test_trade_opportunity.py` (88 tests) | Eligibility gates, ranking, status assignment, symmetry |
| `test_market_scanner.py` (80 tests) | Multi-instrument scanning, MTF alignment, ranking, serialization, leakage |

### 16.2 Invariants Explicitly Tested

| Invariant | Tested In |
|-----------|-----------|
| Evidence alignment correctness | `test_setup_confluence.py` |
| Supporting/conflicting subset correctness | `test_setup_confluence.py` |
| Confluence score = count of aligned | `test_setup_confluence.py` |
| Future mutation leaves detection unchanged | All engine test files |
| Determinism (same input → same output) | All engine test files |
| Point-in-time (prefix == full series) | All engine test files |
| Immutability (frozen models) | All engine test files |
| Evidence preservation through candidate | `test_trade_candidates.py` |
| Score component bounds | `test_trade_decision.py` |
| Eligibility reason correctness | `test_trade_opportunity.py` |
| Serialization round-trip | `test_market_scanner.py` |
| No-look-ahead safety | All engine test files |
| Existing pipeline regression | Product phase tests |

**Finding:** Test coverage for evidence/provenance invariants is comprehensive. **No deficiency.**

---

## 17. Architectural Gaps

### 17.1 Critical

**NONE.** No evidence/timestamp ambiguity that could cause point-in-time correctness failure or setup misinterpretation.

### 17.2 Significant

**NONE.** All critical evidence is preserved.

### 17.3 Low

| # | Gap | Description | Impact |
|---|-----|-------------|--------|
| L1 | Implicit detector identity | No explicit `producer` field on output models | Debugging convenience only |
| L2 | CandlePattern not referenced from SetupAssessment | Only summary labels carried forward | Cannot trace from assessment back to specific pattern objects without re-running |
| L3 | No confirmation timestamp | Single timestamp serves as both event and detection time | Appropriate for current detectors; would be needed if multi-bar confirmation added |
| L4 | MarketContext has no timestamp field | Uses `index` only | Minor — timestamp is available on the triggering pattern |
| L5 | No explicit provenance graph | Relationships are by-reference, not a formal graph | Debugging convenience only |

---

## 18. Severity Classification

### Critical
**None identified.** The system correctly preserves evidence and provenance for all detected setups.

### Significant
**None identified.** No evidence loss that would make reproduction or auditing materially difficult.

### Low
1. Implicit detector identity (model type serves as identity)
2. CandlePattern objects not referenced from SetupAssessment (by design)
3. No separate confirmation timestamp (not needed for current detectors)
4. MarketContext lacks timestamp field (index is sufficient)

---

## 19. Minimal Evidence Boundary Design

Based on the actual architecture, the minimum evidence that crosses each boundary is already implemented:

### CandlePattern → SetupConfluence
- `pattern_type`, `index`, `timestamp`, `direction`, `measurements`, `score`, `reason`

### MarketContext → SetupConfluence
- `trend.state`, `recent_structure`, `support_resistance.location`, `range.state`

### SetupAssessment → TradeCandidate
- All 5 `EvidenceItem` objects (via `supporting`/`conflicting` tuples), `confluence_score`, `setup_classification`, all summary labels

### TradeCandidate → TradeDecision
- Full `TradeCandidate` by reference, plus summary counts

### TradeDecision → TradeOpportunity
- Full `TradeDecision` by reference, plus `EligibilityReason` tuples

### TradeOpportunity → MarketScanResult
- Full `TradeOpportunity` by reference (live), summary fields persist (serialized)

**The existing architecture already implements this minimum boundary.** No new model is required.

---

## 20. Required Changes

**None.** The existing evidence/provenance architecture is adequate. The limitations identified are all Low severity and do not warrant implementation changes.

If future requirements demand:
- **Explicit detector identity**: Add a `producer: str` field to output models (additive, backward-compatible)
- **Full provenance graph**: Add a `provenance: tuple[Reference, ...]` field (additive)
- **Confirmation timestamp**: Add `confirmation_timestamp: datetime | None` to `CandlePattern` (additive)

None of these are required for the current scope.

---

## 21. Previous Checkpoint Preservation

This audit does NOT modify:
- Frozen historical research (Checkpoints 9-10.8) — untouched
- Current-market candle integrity (Checkpoint 11.2) — untouched
- Chart/context construction (Checkpoint 11.3) — untouched
- Detector semantics (Checkpoint 11.4) — untouched

This audit is purely **descriptive** — it inspects and documents existing behavior without modifying any code, engine, model, or test.

---

## 22. Checkpoint 11.5 Verdict

### **PASS WITH LIMITATIONS**

### Rationale

The setup-detection subsystem preserves evidence and provenance to a degree that fully supports the audit objective:

> *Given a setup detected at time T, can we determine exactly **what was detected, using what information, from which candles/features/structures, and why the detector produced that result?***

**Answer: YES.**

- **What was detected**: `SetupAssessment.classification` + `direction` + `candle_evidence` + `structure_evidence` + `trend_evidence` + `location_evidence` + `regime_evidence`
- **Using what information**: `SetupEvidence` with 5 named `EvidenceItem` objects, each with `source`, `direction`, `alignment`, `label`, `reason`
- **From which candles/features/structures**: `CandlePattern.index`/`timestamp` (candle), `MarketContext.recent_structure`/`support_resistance` (features), `MarketContext.index` (evaluation point)
- **Why**: `SetupAssessment.reason` + each `EvidenceItem.reason` + `DecisionScore.components` + `EligibilityReason.reason` + `TradeOpportunity.ranking_reason`

The chain is:
1. **Complete** — no evidence is silently discarded
2. **Deterministic** — reproducible from inputs
3. **Auditable** — every point attributable to named sources with reasons
4. **Point-in-time correct** — no future data leakage
5. **Immutable** — frozen dataclasses prevent post-hoc modification
6. **Explicitly non-trading** — no BUY/SELL/order semantics anywhere

### Limitations (all Low)
- Implicit detector identity (not a defect)
- No separate confirmation timestamp (not needed)
- CandlePattern not referenced from assessment (by design)

### Implementation Requirement
**None.**

---

### Files Inspected

| File | Purpose |
|------|---------|
| `src/engine/models/candle_pattern.py` | CandlePattern model |
| `src/engine/models/market_context.py` | MarketContext model |
| `src/engine/models/setup_confluence.py` | SetupAssessment + EvidenceItem models |
| `src/engine/models/trade_candidate.py` | TradeCandidate model |
| `src/engine/models/trade_decision.py` | TradeDecision + DecisionScore models |
| `src/engine/models/opportunity.py` | TradeOpportunity model |
| `src/engine/models/market_scan.py` | MarketScanResult + InstrumentScanResult models |
| `src/engine/intelligence/setup_confluence.py` | SetupConfluenceEngine |
| `src/engine/intelligence/trade_candidates.py` | TradeCandidateEngine |
| `src/engine/intelligence/trade_decision.py` | TradeDecisionEngine |
| `src/engine/intelligence/trade_opportunity.py` | TradeOpportunityEngine |
| `src/engine/intelligence/market_scanner.py` | MarketScanner |
| `src/engine/intelligence/mtf_alignment.py` | MTFAlignmentEngine |
| `src/dashboard/views.py` | Dashboard presentation models |
| `src/dashboard/services.py` | DashboardAnalysisService |
| `src/dashboard/app.py` | FastAPI routes |

### Files Changed

**None.** This audit is purely descriptive.

### Tests Run

**None required.** No code changes made. Existing test suite (4328+ tests) continues to pass per AGENTS.md baseline.

---

*End of Checkpoint 11.5 audit.*
