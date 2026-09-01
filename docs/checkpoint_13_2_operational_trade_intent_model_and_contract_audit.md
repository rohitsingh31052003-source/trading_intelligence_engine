# Checkpoint 13.2 — Operational Trade Intent Model & Contract Audit

## 1. Purpose

Determine the architectural contract and invariants for a future **Operational Trade Intent** boundary — the conceptual layer that sits between a frozen `TradePlan` and any future execution authorization. This checkpoint establishes **what information crosses** from TradePlan into Operational Trade Intent, **what must NOT cross**, and **what the contract must guarantee** — without implementing the model.

This is an **audit + contract design only** checkpoint. No implementation changes are made.

## 2. Scope

- Inspect the frozen `TradePlan` contract field-by-field and classify every field into boundary categories.
- Inspect the frozen `PaperTrade` contract to determine its relationship to Operational Trade Intent.
- Inspect `MarketScanResult`, `TradeCandidate`, `TradeDecision`, `TradeOpportunity` for provenance requirements.
- Search the repository for any existing intent-like abstraction.
- Determine identity, temporal, quantity, direction, broker-neutrality, authorization, validation, immutability, provenance, auditability, and supersession contracts.
- Define the future test contract.
- Specify the recommended conceptual contract and lifecycle.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Boundary |
| -----------|--------|----------|
| 11.1–11.8 | FROZEN | Market data → candle integrity → features → setup → evidence → `MarketScanResult` |
| 12.1–12.6 | FROZEN | `MarketScanResult` → `TradePlan` → `PaperTrade` → journal/performance → dashboard |
| 13.1 | ACCEPTED | Established that no operational trade intent, execution path, broker order path, or authorization currently exists |

No frozen checkpoint is modified by this audit.

## 4. Exact Files Inspected

### Core Models
- `src/engine/models/trade_plan.py` — `TradePlan`, `RiskPlanStatus`, `QuantityStatus`, `QuantitySpec`, `DEFAULT_QUANTITY_SPEC`
- `src/engine/models/paper_trade.py` — `PaperTrade`, `PaperTradeStatus`, `PaperExitReason`
- `src/engine/models/trade_candidate.py` — `TradeCandidate`, `CandidateDirection`, `CandidateStatus`, `SetupType`
- `src/engine/models/trade_decision.py` — `TradeDecision`, `DecisionClassification`, `DecisionScore`
- `src/engine/models/opportunity.py` — `TradeOpportunity`, `OpportunityStatus`, `EligibilityStatus`
- `src/engine/models/market_scan.py` — `MarketScanResult`, `InstrumentScanResult`, `RankedScanOpportunity`

### Intelligence Engines
- `src/engine/intelligence/trade_planning.py` — `TradePlanningEngine`
- `src/engine/intelligence/paper_trading.py` — `PaperTradingEngine`
- `src/engine/intelligence/trade_candidates.py` — `TradeCandidateEngine`
- `src/engine/intelligence/trade_decision.py` — `TradeDecisionEngine`
- `src/engine/intelligence/trade_opportunity.py` — `TradeOpportunityEngine`

### Dashboard / Presentation
- `src/dashboard/views.py` — `ActionabilityState`, `derive_actionability`, `TradePlanView`, `PaperTradeView`, `DashboardTradeView`, `WorkstationView`
- `src/dashboard/services.py` — `DashboardAnalysisService`
- `src/dashboard/paper_trade_operations.py` — `PaperTradingOperations`, `OperationalStatus`, `InstrumentOperationResult`, `OperationsCycleResult`
- `src/dashboard/app.py` — FastAPI routes
- `src/dashboard/paper_trade_store.py` — `PaperTradeStore`

### Config
- `src/engine/config/trade_plan_config.py` — `TradePlanConfig`
- `src/engine/config/paper_trade_config.py` — `PaperTradeConfig`

### Prior Audit
- `docs/checkpoint_13_1_operational_trade_intent_and_execution_boundary_audit.md`

### Tests (baseline only — not modified)
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests

## 5. Existing Intent-Like Abstractions

### Search Results

The following keywords were searched across the entire codebase:

| Keyword | Result |
|---------|--------|
| `intent`, `trade_intent`, `operational_intent` | **No classes found.** "Directional intent" appears only in docstrings describing the `direction` field. |
| `actionable`, `approved` | **No classes found.** `ActionabilityState` is a presentation mirror, not an approval. `EligibilityStatus` is a filter, not an approval. |
| `execution_intent`, `execution_request`, `trade_request`, `action_request` | **No classes found.** |
| `authorization`, `approval` | **No classes found.** No authorization layer exists. |
| `execution_eligibility`, `live_trade`, `live_trading` | **No classes found.** The system is paper-trading-only. |
| `order_intent`, `order_request` | **No classes found.** No order model exists. |

### Conclusion

**No existing object in the repository serves the role of Operational Trade Intent.** The concept is currently unnamed and unimplemented. The closest existing objects are:

- `TradePlan` — a risk/planning artifact (source of truth for planning geometry).
- `PaperTrade` — a simulation artifact (observational validation record).
- `ActionabilityState.READY_FOR_REVIEW` — a descriptive presentation state (NOT an approval or authorization).

None of these is an Operational Trade Intent.

## 6. TradePlan Contract

### 6.1 Complete Field Inventory

The `TradePlan` class (`src/engine/models/trade_plan.py:222-455`) is a **frozen+slots dataclass** with the following fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `plan_id` | `str` | *required* | Deterministic `"plan-" + sha256[:16]` |
| `instrument` | `str` | *required* | Canonical instrument name |
| `timeframe` | `str` | *required* | Setup timeframe label |
| `direction` | `str` | *required* | `"LONG"` / `"SHORT"` / `"NONE"` / `""` |
| `existing_decision` | `str` | *required* | Sprint 11S decision classification name |
| `actionability` | `str` | *required* | `ActionabilityState` name |
| `account_capital` | `Decimal \| None` | *required* | User-supplied account capital |
| `risk_percent` | `Decimal \| None` | *required* | User-supplied risk percentage |
| `maximum_risk` | `Decimal \| None` | *required* | `account_capital * risk_percent / 100` |
| `entry` | `Decimal \| None` | *required* | Engine geometry entry level |
| `stop` | `Decimal \| None` | *required* | Engine geometry stop level |
| `target_1` | `Decimal \| None` | *required* | Engine geometry target level |
| `engine_risk_distance` | `Decimal \| None` | *required* | Per-unit risk from geometry |
| `engine_reward_distance` | `Decimal \| None` | *required* | Per-unit reward from geometry |
| `engine_risk_reward_ratio` | `Decimal \| None` | *required* | Engine R:R ratio |
| `target_2` | `Decimal \| None` | `None` | Always `None` (unsupported) |
| `target_2_supported` | `bool` | `False` | Always `False` |
| `quantity` | `Decimal \| None` | `None` | Position quantity |
| `planned_risk` | `Decimal \| None` | `None` | `quantity * engine_risk_distance` |
| `planned_reward` | `Decimal \| None` | `None` | `quantity * engine_reward_distance` |
| `quantity_status` | `QuantityStatus` | `UNSIZED` | How quantity was computed |
| `risk_plan_status` | `RiskPlanStatus` | `GEOMETRY_UNAVAILABLE` | Risk calculation status |
| `quantity_spec_available` | `bool` | `False` | Whether instrument spec was supplied |
| `warnings` | `tuple[str, ...]` | `()` | Validation/honesty warnings |
| `rationale` | `str` | `""` | Human-readable status summary |
| `label` | `str` | `""* | Caller-supplied identity |
| `metadata` | `tuple[tuple[str, str], ...]` | `()` | Audit trail metadata |

### 6.2 Invariants Enforced by `__post_init__`

A `VALID` plan **must**:
- Carry directional intent (`direction in ("LONG", "SHORT")`)
- Have complete geometry (`entry`, `stop`, `engine_risk_distance > 0`)
- Have positive quantity (`quantity > 0`)
- Have positive planned_risk (`planned_risk > 0`)
- Satisfy no-over-risk (`planned_risk <= maximum_risk`)
- Have `target_2 = None` and `target_2_supported = False`

`planned_risk` requires `quantity` + `engine_risk_distance`. `planned_reward` requires `quantity` + `engine_reward_distance`.

### 6.3 Properties

- `is_valid` — whether `risk_plan_status.is_valid`
- `has_geometry` — whether entry/stop/risk are usable

## 7. Field-by-Field Boundary Classification

### Classification Categories

1. **MUST be preserved** — authoritative operational values that cross verbatim
2. **MAY be copied** — reference values useful for operational context
3. **MUST remain in TradePlan** — planning-internal values with no operational meaning
4. **MUST be transformed** — values requiring conversion before crossing
5. **MUST NOT cross** — values that must never enter operational layer

### Field Classification

| TradePlan Field | Classification | Rationale |
|-----------------|----------------|-----------|
| `plan_id` | **MUST be preserved** | Authoritative plan identity; operational intent must reference its source plan |
| `instrument` | **MUST be preserved** | Authoritative instrument identity |
| `timeframe` | **MUST be preserved** | Authoritative timeframe context |
| `direction` | **MUST be preserved** | Authoritative directional intent (LONG/SHORT) |
| `existing_decision` | **MAY be copied** | Useful operational context (PREFERRED/QUALIFIED/WATCH/REJECTED) |
| `actionability` | **MAY be copied** | Useful operational context |
| `account_capital` | **MUST remain in TradePlan** | Account-level parameter; operational intent references plan, not account state directly |
| `risk_percent` | **MUST remain in TradePlan** | Account-level parameter; derived from planning inputs |
| `maximum_risk` | **MUST be preserved** | Authoritative risk limit; operational intent must respect this bound |
| `entry` | **MUST be preserved** | Authoritative geometry — operational intent references this level |
| `stop` | **MUST be preserved** | Authoritative geometry — operational intent references this level |
| `target_1` | **MUST be preserved** | Authoritative geometry — operational intent references this level |
| `engine_risk_distance` | **MUST be preserved** | Authoritative per-unit risk; needed for operational risk verification |
| `engine_reward_distance` | **MAY be copied** | Useful for operational R:R reference |
| `engine_risk_reward_ratio` | **MAY be copied** | Useful for operational R:R reference |
| `target_2` | **MUST NOT cross** | Always `None`; architecture does not support a second target |
| `target_2_supported` | **MUST NOT cross** | Always `False`; documents unsupported feature |
| `quantity` | **MUST be preserved** | Authoritative position size; operational intent must not silently change |
| `planned_risk` | **MUST be preserved** | Authoritative planned loss; operational intent must not exceed |
| `planned_reward` | **MAY be copied** | Useful for operational reference |
| `quantity_status` | **MAY be copied** | Useful for operational honesty (was quantity floor-rounded?) |
| `risk_plan_status` | **MUST be preserved** | Operational intent requires `VALID` status; this is the gate |
| `quantity_spec_available` | **MAY be copied** | Useful for operational honesty (generic model vs. instrument-specific) |
| `warnings` | **MAY be copied** | Useful for operational audit trail |
| `rationale` | **MAY be copied** | Useful for operational audit trail |
| `label` | **MAY be copied** | Useful for operational audit trail |
| `metadata` | **MAY be copied** | Useful for operational audit trail |

### Summary of Boundary Rules

**Operational Trade Intent MUST preserve verbatim:**
- `plan_id`, `instrument`, `timeframe`, `direction`
- `entry`, `stop`, `target_1`
- `engine_risk_distance`, `maximum_risk`
- `quantity`, `planned_risk`
- `risk_plan_status`

**Operational Trade Intent MAY copy for reference:**
- `existing_decision`, `actionability`
- `engine_reward_distance`, `engine_risk_reward_ratio`
- `planned_reward`, `quantity_status`, `quantity_spec_available`
- `warnings`, `rationale`, `label`, `metadata`

**Operational Trade Intent MUST NOT contain:**
- `account_capital`, `risk_percent` (account-level planning inputs)
- `target_2`, `target_2_supported` (unsupported features)

## 8. Source-of-Truth Rules

### Architectural Principle

```
TradePlan = source of truth for planning geometry
Operational Trade Intent = operational snapshot/reference
```

### Rules

1. **TradePlan is authoritative** for entry, stop, target, risk, reward, quantity, and maximum risk.
2. **Operational Trade Intent MUST NOT silently recalculate** any planning value.
3. **Operational Trade Intent MUST NOT modify** the TradePlan it was created from.
4. If the operational layer needs different values:
   - **A new TradePlan is required** (different inputs → different deterministic plan_id), OR
   - **An explicit operational transformation is required** (documented, auditable, does not mutate the original plan), OR
   - **The difference belongs to execution** (slippage, partial fills — not operational intent).
5. **TradePlan.plan_id is immutable** once created. Operational intent references it but never reassigns it.
6. **TradePlan risk geometry is immutable.** Operational intent carries a snapshot/reference, not a mutable copy.

### Consequence

If market conditions change such that the original TradePlan's entry/stop/target are no longer valid, the correct behavior is:
- **Create a new TradePlan** with the new market data, OR
- **Reject the stale Operational Trade Intent** and require fresh analysis/planning.

The system **MUST NOT** silently refresh an old TradePlan's geometry.

## 9. Identity Contract

### 9.1 Relationship to Existing Identities

| Identity | Source | Operational Trade Intent Relationship |
|----------|--------|---------------------------------------|
| `MarketScanResult.scan_id` | Sprint 11U scanner | Indirect: scan → decision → opportunity → candidate → plan → intent |
| `TradePlan.plan_id` | Product Phase 4 planner | **Direct: intent references plan_id** |
| `PaperTrade.paper_trade_id` | Product Phase 5 paper trading | **Separate: intent and paper trade are siblings, not parent-child** |

### 9.2 Recommended Identity Fields

| Field | Type | Purpose |
|-------|------|---------|
| `intent_id` | `str` | Deterministic operational identity (`"intent-" + sha256[:16]`) |
| `plan_id` | `str` | Reference to source TradePlan (provenance) |
| `created_at` | `datetime` | Creation timestamp (explicit, deterministic) |
| `version` | `int` | Operational version (default 1) |

### 9.3 Identity Rules

1. `intent_id` is **deterministic** from canonical inputs (plan_id + operational context + creation timestamp + sequence).
2. `intent_id` is **distinct from plan_id** — planning identity ≠ operational identity.
3. Two Operational Trade Intents from the **same TradePlan** at **different times** have **different** `intent_id` values.
4. Two Operational Trade Intents from the **same TradePlan** at the **same time** with the **same context** have the **same** `intent_id` (idempotent).
5. `plan_id` is **preserved verbatim** — never transformed or reinterpreted.

## 10. Determinism vs Operational Identity

### Options Evaluated

| Option | Description | Consequences |
|--------|-------------|--------------|
| **A. Inherit plan_id as identity** | `intent_id = plan_id` | Planning identity = operational identity. A new plan (different capital) creates a new intent. But same plan rescanned later would collide. No operational lifecycle identity. |
| **B. Separate deterministic identity** | `intent_id = hash(canonical operational inputs)` | Same operational context → same intent_id (deduplication). Different context → different intent_id. Clean separation from planning. |
| **C. Separate operational identity** | `intent_id = hash(plan_id + operational_timestamp + sequence)` | Explicit operational timestamp distinguishes rescans. Sequence allows multiple intents from same plan at same time. Most flexible. |
| **D. Another mechanism** | e.g., UUID, auto-increment | Non-deterministic. Violates the repository's deterministic identity convention. Rejected. |

### Recommended: Option B + C Hybrid

The `intent_id` should be **deterministic from canonical operational inputs** (plan_id + creation timestamp + operational context + sequence), providing:

- **Deduplication**: Same plan + same context → same intent_id (prevents accidental duplicates).
- **Operational distinction**: Same plan at different times → different intent_id (operational lifecycle).
- **Planning independence**: Different plans → different intent_id (even if geometry is identical).

This matches the repository's established convention (SHA-256 prefix-based deterministic IDs used by `plan_id`, `paper_trade_id`, `experiment_id`, `suite_id`, `selection_id`).

## 11. Temporal Contract

### 11.1 Temporal Concepts

| Concept | Meaning | Layer |
|---------|---------|-------|
| `created_at` | When the operational intent was created | Operational Trade Intent |
| `valid_from` | When the intent becomes eligible for authorization | Operational Trade Intent |
| `valid_until` | When the intent expires (stale) | Operational Trade Intent |
| `evaluation_timestamp` | When the source market data was evaluated | TradePlan (inherited) |
| `plan_created_at` | When the TradePlan was created | TradePlan (inherited) |

### 11.2 Temporal Rules

1. **A TradePlan does NOT remain valid indefinitely as an Operational Trade Intent.**
2. **Operational Trade Intent is time-bounded** — it represents an operational representation at a point in time.
3. `valid_until` is derived from:
   - Market session boundaries, OR
   - Plan age (time since evaluation_timestamp), OR
   - Explicit expiry policy, OR
   - Market structure change detection.
4. **After `valid_until`**, the Operational Trade Intent is **stale** and MUST NOT be authorized for execution.
5. **Staleness belongs to the Operational Trade Intent layer** — TradePlan remains a historical artifact; the intent is the time-bounded representation.

### 11.3 Recommended Temporal Fields

| Field | Type | Purpose |
|-------|------|---------|
| `created_at` | `datetime` | Creation timestamp (explicit, deterministic) |
| `evaluation_timestamp` | `datetime` | Inherited from TradePlan (when market data was evaluated) |
| `valid_until` | `datetime \| None` | Expiry timestamp (None = session-bound) |

## 12. Stale-Plan Behavior

### Scenario

1. A TradePlan was created earlier.
2. New market data becomes available.
3. The market structure changes.
4. The original TradePlan is still mathematically valid.
5. Someone attempts to operationalize the old plan.

### Required Behavior

**The system MUST NOT silently mutate the old TradePlan.**

### Correct Behavior

The correct behavior is: **Reject + require fresh analysis.**

Specifically:

1. **Reject** the stale Operational Trade Intent (it has expired).
2. **Require fresh analysis** — a new scan, new decision, new opportunity, new candidate, new plan.
3. **Create a new TradePlan** with the new market data.
4. **Create a new Operational Trade Intent** from the new plan.

The system **MUST NOT**:
- Silently refresh the old TradePlan's geometry.
- Extend the old intent's `valid_until` without fresh analysis.
- Authorize execution from a stale intent.

### Architectural Justification

The frozen architecture is:
```
MarketScanResult → TradePlan → PaperTrade
```

Each step is deterministic from its inputs. If market data changes, the correct response is a new analytical chain — not mutation of historical artifacts.

## 13. Direction Semantics

### 13.1 Current Representation

The repository uses `direction: str` with values:
- `"LONG"` — directional bullish
- `"SHORT"` — directional bearish
- `"NONE"` — no directional intent
- `""` — unavailable/unknown

This is consistent across `TradeCandidate`, `TradeDecision`, `TradeOpportunity`, `TradePlan`, and `PaperTrade`.

### 13.2 Operational Trade Intent Direction Contract

**Operational Trade Intent MAY contain:**
- `LONG`
- `SHORT`

**Operational Trade Intent MUST NOT contain:**
- `BUY` — execution verb, belongs to execution layer
- `SELL` — execution verb, belongs to execution layer
- `ENTER` — execution verb, belongs to execution layer
- `EXIT` — execution verb, belongs to execution layer
- `HOLD` — execution verb, belongs to execution layer

### 13.3 Conversion Boundary

The conversion from `LONG/SHORT` (analytical) to execution-side semantics (`BUY/SELL/ENTER/EXIT`) **MUST occur at the execution layer**, not in Operational Trade Intent.

**Operational Trade Intent remains analytically directional.** It says "this is a LONG plan" or "this is a SHORT plan." It does not say "BUY this instrument."

## 14. Quantity Contract

### 14.1 Current Quantity Semantics (from Checkpoint 12)

| Field | Source | Meaning |
|-------|--------|---------|
| `quantity` | TradePlan | Position quantity (Decimal) |
| `planned_risk` | TradePlan | `quantity * engine_risk_distance` |
| `maximum_risk` | TradePlan | `account_capital * risk_percent / 100` |
| `engine_risk_distance` | TradePlan | Per-unit risk from geometry |

### 14.2 Operational Trade Intent Quantity Contract

1. **Operational Trade Intent preserves `quantity` verbatim** from TradePlan.
2. **Operational Trade Intent preserves `planned_risk` verbatim** from TradePlan.
3. **Operational Trade Intent preserves `maximum_risk` verbatim** from TradePlan.
4. **Operational Trade Intent MUST NOT silently change planned quantity.**
5. If execution constraints require a different quantity:
   - The transformation **belongs to the execution layer** (broker lot sizes, margin constraints).
   - The original TradePlan **remains unchanged**.
   - The execution layer **documents the deviation** from the planned quantity.
   - Re-authorization **MAY be required** if the deviation exceeds a threshold.

### 14.3 Quantity Step / Fractional / Multiplier

These belong to `QuantitySpec` in TradePlan. Operational Trade Intent:
- **MUST NOT** modify quantity step, contract multiplier, or fractional rules.
- **MAY** reference them for operational honesty (was the generic model used?).
- **MUST** preserve the `quantity_spec_available` flag.

## 15. Broker Neutrality

### 15.1 Classification

| Field | Classification | Rationale |
|-------|----------------|-----------|
| Broker name | **MUST NOT be in intent** | Execution-layer responsibility |
| Broker account | **MUST NOT be in intent** | Account/portfolio responsibility |
| Broker instrument ID | **MUST NOT be in intent** | Broker-adapter responsibility |
| Exchange-specific symbol | **MUST NOT be in intent** | Broker-adapter responsibility |
| Order type | **MUST NOT be in intent** | Execution-layer responsibility |
| Product type | **MUST NOT be in intent** | Execution-layer responsibility |
| Validity type | **MUST NOT be in intent** | Execution-layer responsibility |
| Exchange routing | **MUST NOT be in intent** | Broker-adapter responsibility |
| Broker-specific quantity rules | **MUST NOT be in intent** | Broker-adapter responsibility |

### 15.2 Principle

**Operational Trade Intent is broker-neutral.** It represents "what plan is operationally eligible" — not "how to execute it at broker X."

The instrument identity in Operational Trade Intent is the **canonical instrument name** (e.g., `"NIFTY"`, `"RELIANCE"`), not a broker-specific symbol.

## 16. Order Separation

### 16.1 Order Semantics — MUST NOT BE in Operational Trade Intent

| Field | Correct Layer |
|-------|---------------|
| Order ID | Execution layer |
| Client order ID | Execution layer |
| Order type | Execution layer |
| Order status | Execution layer |
| Fill status | Execution layer |
| Execution price | Execution layer |
| Fill quantity | Execution layer |
| Rejection reason | Execution layer |
| Cancellation status | Execution layer |

### 16.2 Principle

```
Operational Trade Intent ≠ Order
```

Operational Trade Intent represents an **eligible plan**. An **Order** represents a **request to a broker**. These are separated by Execution Authorization and Execution Command layers.

## 17. Position Separation

### 17.1 Position Semantics — MUST NOT BE in Operational Trade Intent

| Field | Correct Layer |
|-------|---------------|
| Position ID | Position management layer |
| Current position | Position management layer |
| Net position | Portfolio layer |
| Holdings | Portfolio layer |
| Account exposure | Portfolio layer |
| Portfolio exposure | Portfolio layer |

### 17.2 Principle

```
Operational Trade Intent ≠ Position
```

Operational Trade Intent represents a **single eligible plan**. Positions and portfolios represent **aggregated state** across multiple executions.

## 18. Authorization Separation

### 18.1 Critical Distinction

```
Operational Trade Intent ≠ Execution Authorization
```

### 18.2 What Authorization Answers

> "Is this particular operational intent permitted to be sent toward execution?"

### 18.3 Authorization Should Be

- **Separate object** — not a field on Operational Trade Intent.
- **Separate state** — distinct from intent lifecycle.
- **Policy decision** — based on risk limits, exposure, time, user preferences.
- **Manual or automated** — but explicitly modeled as authorization, not implicit in intent creation.

### 18.4 Authorization Contract

1. Operational Trade Intent **does not contain** an `authorized` field.
2. Operational Trade Intent **does not contain** an `approval_status` field.
3. Authorization is a **separate concern** that references `intent_id`.
4. Authorization **MUST NOT** be implicit in intent creation.
5. Authorization **MUST NOT** be implicit in `RiskPlanStatus.VALID`.
6. Authorization **MUST NOT** be implicit in `READY_FOR_REVIEW`.

## 19. READY_FOR_REVIEW Relationship

### 19.1 Current Semantics

`READY_FOR_REVIEW` is an `ActionabilityState` value in `src/dashboard/views.py`. It is derived deterministically from:
- Scan completeness
- Sprint 11S decision classification (QUALIFIED or PREFERRED)
- Sprint 11T opportunity status (ELIGIBLE)
- Geometry completeness
- Evidence strength (not INSUFFICIENT)

It is **descriptive only** — it does NOT predict success, authorize execution, or approve a trade.

### 19.2 Relationship to Operational Trade Intent

```
READY_FOR_REVIEW
    ↓
eligible for creation/review
    ↓
Operational Trade Intent (if created)
    ↓
separate authorization (NOT implicit)
    ↓
Execution Command
```

### 19.3 Rules

1. `READY_FOR_REVIEW` is **necessary but not sufficient** for Operational Trade Intent creation.
2. `READY_FOR_REVIEW` does **NOT** implicitly authorize execution.
3. `RiskPlanStatus.VALID` does **NOT** implicitly authorize execution.
4. Operational Trade Intent creation is **NOT** automatic upon `READY_FOR_REVIEW`.
5. A human or explicit policy decision creates the Operational Trade Intent.

## 20. Validation Responsibilities

### 20.1 Validation Layer Assignment

| Validation | TradePlan | Operational Intent | Execution Authorization | Execution Command | Broker |
|------------|-----------|-------------------|------------------------|-------------------|--------|
| Valid geometry | ✓ | — | — | — | — |
| Positive risk distance | ✓ | — | — | — | — |
| Quantity within risk limit | ✓ | — | — | — | — |
| Direction present | ✓ | — | — | — | — |
| Plan identity present | — | ✓ | — | — | — |
| Plan is VALID | — | ✓ | — | — | — |
| Plan not stale | — | ✓ | — | — | — |
| Intent not expired | — | ✓ | — | — | — |
| Intent not duplicate | — | ✓ | — | — | — |
| Intent not superseded | — | ✓ | — | — | — |
| Risk within portfolio limits | — | — | ✓ | — | — |
| Exposure within limits | — | — | ✓ | — | — |
| Account has sufficient margin | — | — | — | ✓ | — |
| Instrument tradable | — | — | — | ✓ | — |
| Order within broker rules | — | — | — | — | ✓ |
| Broker connectivity | — | — | — | — | ✓ |

### 20.2 Operational Trade Intent Validations

Before an Operational Trade Intent can exist, the following MUST be true:

1. **Valid TradePlan** — `risk_plan_status == VALID`
2. **Valid geometry** — entry, stop, target present with positive risk distance
3. **Valid quantity** — positive quantity within maximum_risk
4. **Direction present** — LONG or SHORT
5. **Plan identity present** — non-empty plan_id
6. **Instrument identity present** — non-empty instrument
7. **Temporal validity** — plan is not stale (within session/freshness window)
8. **Required provenance** — plan_id references an existing TradePlan
9. **No contradictory fields** — direction matches plan, quantity matches plan

## 21. Immutability / Ownership

### 21.1 Immutability Principle

**Planning values should be immutable.**

### 21.2 Recommended Model

Operational Trade Intent should be an **immutable snapshot** — not a mutable lifecycle object.

```
Operational Trade Intent = immutable snapshot of an eligible plan at a point in time
```

### 21.3 Lifecycle Model

If operational state changes, it should be represented by:

- **Immutable state transitions** — new intent with new state, OR
- **Replacement objects** — new intent supersedes old intent, OR
- **Separate lifecycle object** — intent is immutable; lifecycle is tracked separately.

**NOT** by mutating an existing intent's fields.

### 21.4 Recommendation

Operational Trade Intent should be a **frozen+slots dataclass** (matching the repository convention). Lifecycle state (authorized, revoked, expired, superseded) should be tracked by **separate lifecycle objects** that reference `intent_id`.

## 22. Mutation Protection

### 22.1 Prohibited Mutations

The future boundary **MUST PREVENT**:

```
Operational Trade Intent
    ↓
modifies TradePlan  ← PROHIBITED
```

```
Operational Trade Intent
    ↓
modifies MarketScanResult  ← PROHIBITED
```

```
Operational Trade Intent
    ↓
modifies PaperTrade  ← PROHIBITED
```

### 22.2 One-Directional Flow

```
MarketScanResult → TradePlan → Operational Trade Intent
                                      ↓
                                  PaperTrade (sibling, not child)
```

The flow is one-directional. Operational Trade Intent **consumes** references to TradePlan but **never mutates** it.

## 23. Provenance Requirements

### 23.1 Required Provenance

Operational Trade Intent MUST preserve:

| Provenance Field | Purpose |
|------------------|---------|
| `plan_id` | Reference to source TradePlan |
| `instrument` | Canonical instrument identity |
| `timeframe` | Setup timeframe |
| `evaluation_timestamp` | When market data was evaluated |
| `created_at` | When intent was created |

### 23.2 Optional Provenance

| Provenance Field | Purpose |
|------------------|---------|
| `existing_decision` | Source decision classification |
| `actionability` | Source actionability state |
| `scanner_run_id` | Future: reference to scanner run |
| `planning_configuration` | Future: reference to planning config |

### 23.3 Provenance Rules

1. **Prefer references/identity** over copying entire analytical structures.
2. **Do NOT duplicate** MarketScanResult, TradeCandidate, TradeDecision, or TradeOpportunity.
3. **Preserve plan_id** as the single authoritative reference to the planning artifact.
4. **Preserve evaluation_timestamp** for temporal audit.

## 24. Auditability Requirements

### 24.1 Minimum Auditable Information

Operational Trade Intent MUST make the following auditable:

| Auditable Question | Required Information |
|--------------------|----------------------|
| What plan created it? | `plan_id` |
| When was it created? | `created_at` |
| What version does it represent? | `version` |
| Was it approved? | Reference to authorization record (separate) |
| Was it rejected? | Reference to authorization record (separate) |
| Did it expire? | `valid_until` + lifecycle record |
| Was it superseded? | Reference to superseding intent_id |
| Was it sent for execution? | Reference to execution record (separate) |
| Was execution attempted? | Reference to execution record (separate) |

### 24.2 Audit Trail Principle

Every state change in the operational lifecycle MUST be traceable back to:
- The source TradePlan (`plan_id`)
- The creation timestamp (`created_at`)
- The authorization decision (separate authorization record)

## 25. Supersession / Duplication

### 25.1 Scenarios

| Scenario | Correct Behavior |
|----------|------------------|
| Same TradePlan requested twice | Same `intent_id` (deterministic deduplication) |
| Same opportunity rescanned | New `evaluation_timestamp` → new TradePlan → new `intent_id` |
| New TradePlan superseding old plan | New `intent_id` + reference to superseded `intent_id` |
| Stale Operational Trade Intent | Mark expired (lifecycle record), create new intent from new plan |
| Duplicate operational intent | Prevented by deterministic `intent_id` |
| Re-approval of existing intent | New authorization record referencing same `intent_id` |

### 25.2 Identity and Lifecycle Contract

1. **Deterministic identity prevents accidental duplicates.**
2. **Supersession creates a new intent** — does not mutate the old one.
3. **Stale intents are expired** — not deleted (audit trail).
4. **Re-approval references the same intent** — new authorization record, not new intent.

## 26. Failure States

### 26.1 TradePlan Failure States (belong to TradePlan)

| State | Meaning |
|-------|---------|
| `INVALID_INPUT` | Invalid account-risk inputs |
| `GEOMETRY_UNAVAILABLE` | Incomplete geometry |
| `RISK_LIMIT_EXCEEDED` | Smallest position exceeds maximum risk |
| `QUANTITY_UNAVAILABLE` | Quantity spec unavailable |

### 26.2 Operational Trade Intent Failure States (belong to Operational Intent)

| State | Meaning |
|-------|---------|
| `stale` | Plan is no longer fresh |
| `expired` | Past `valid_until` |
| `superseded` | Replaced by a newer intent |
| `unauthorized` | Authorization denied |
| `revoked` | Authorization revoked after grant |
| `invalidated` | Plan became invalid after intent creation |
| `duplicate` | Prevented by deterministic identity |

### 26.3 Layer Separation

TradePlan failures are **planning-time** failures. Operational Trade Intent failures are **operational-lifecycle** failures. They MUST NOT be conflated.

## 27. Cancellation / Revocation

### 27.1 Conceptual Separation

| Concept | Meaning | Layer |
|---------|---------|-------|
| Intent cancellation | Operational intent is withdrawn before authorization | Operational Trade Intent lifecycle |
| Authorization revocation | Authorization is revoked after grant | Execution Authorization layer |
| Order cancellation | Broker order is cancelled | Execution/Broker layer |
| Position closure | Position is closed | Position management layer |

### 27.2 Operational Trade Intent Lifecycle Concepts

Operational Trade Intent needs:
- **cancelled** — intent withdrawn before authorization
- **revoked** — authorization revoked (reference to authorization record)
- **expired** — past `valid_until`
- **superseded** — replaced by newer intent

These concepts should live in a **separate lifecycle object** that references `intent_id`, NOT on the intent itself (which is immutable).

## 28. Point-in-Time Safety

### 28.1 Rules

1. **Information copied from TradePlan** is frozen at intent creation time.
2. **New market data MUST NOT be attached** to an existing intent.
3. **New data MUST trigger a new plan** (new scan → new decision → new plan).
4. **An old intent CANNOT be silently refreshed** — new data → new intent.
5. **Historical planning identity is immutable** — `plan_id` never changes.

### 28.2 Look-Ahead Prevention

The Operational Trade Intent contract **CANNOT introduce look-ahead** because:
- It only references an existing TradePlan (which is point-in-time safe).
- It does not access market data directly.
- It does not mutate the TradePlan.
- New data produces a new analytical chain, not a mutation.

## 29. Paper Trading Relationship

### 29.1 Correct Relationship

```
TradePlan
    ├── PaperTrade
    │
    └── Operational Trade Intent
```

**NOT:**

```
TradePlan
    ↓
PaperTrade
    ↓
Operational Trade Intent
```

### 29.2 Rules

1. **PaperTrade and Operational Trade Intent are siblings** — both derive from TradePlan.
2. **PaperTrade is a simulation artifact** — observational validation only.
3. **Operational Trade Intent is an operational representation** — eligible plan snapshot.
4. **Simulation state MUST NOT become the source of live operational state.**
5. **PaperTrade results MUST NOT automatically authorize** Operational Trade Intent.

### 29.3 Justification

Paper trading is observational validation. Operational Trade Intent is an operational representation of an approved/eligible plan. Conflating them would allow simulation results to influence live operational decisions — a dangerous feedback loop.

## 30. Performance Relationship

### 30.1 Principles

| Principle | Meaning |
|-----------|---------|
| Paper-trade performance ≠ automatic authorization | Good paper results do not auto-authorize live intent |
| Paper-trade performance ≠ automatic operational activation | Good paper results do not auto-create intent |
| Paper-trade performance ≠ strategy modification | Good paper results do not modify the analytical pipeline |

### 30.2 Existing Feedback Paths

**No feedback paths exist** from paper-trade performance to:
- TradePlan creation
- TradePlan geometry
- TradePlan risk calculations
- Operational Trade Intent (does not exist yet)
- Execution (does not exist yet)

This separation MUST be preserved.

## 31. Dashboard Boundary

### 31.1 What the Dashboard Must Distinguish

A future UI MUST distinguish:

| Concept | Current Dashboard Representation |
|---------|----------------------------------|
| Analytical result | `MarketScanResult` → `DashboardTradeView` |
| Plan | `TradePlan` → `TradePlanView` |
| Paper simulation | `PaperTrade` → `PaperTradeView` |
| Operational intent | **Does not exist** — future addition |
| Authorization | **Does not exist** — future addition |
| Execution | **Does not exist** — future addition |

### 31.2 Dashboard Rules

1. **READY_FOR_REVIEW** is descriptive — not an operational state.
2. **TradePlanView** is a planning view — not an operational view.
3. **PaperTradeView** is a simulation view — not an operational view.
4. **Operational Trade Intent** (future) requires a **distinct UI representation**.
5. **Authorization** (future) requires a **distinct UI representation**.
6. **Execution** (future) requires a **distinct UI representation**.

## 32. Security / Safety Considerations

### 32.1 Dangerous Fields

The following fields would be dangerous if Operational Trade Intent could be freely constructed:

| Field | Danger | Mitigation |
|-------|--------|------------|
| `quantity` | Over-leveraging | Must be preserved from TradePlan, not client-controlled |
| `entry` | Slippage manipulation | Must be preserved from TradePlan, not client-controlled |
| `stop` | Risk manipulation | Must be preserved from TradePlan, not client-controlled |
| `instrument` | Wrong instrument | Must be preserved from TradePlan, not client-controlled |
| `direction` | Wrong direction | Must be preserved from TradePlan, not client-controlled |
| `account_capital` | Risk parameter tampering | Must NOT be in intent (remains in TradePlan) |
| `risk_percent` | Risk parameter tampering | Must NOT be in intent (remains in TradePlan) |

### 32.2 Required Safety Properties

The future contract SHOULD require:

1. **Provenance from TradePlan** — intent must reference a valid `plan_id`.
2. **Immutable plan reference** — `plan_id` cannot be changed after creation.
3. **Explicit authorization** — intent is not self-authorizing.
4. **Server-side validation** — intent is validated server-side, not client-side.
5. **No client-controlled risk fields** — risk fields come from TradePlan, not from the intent creation request.

## 33. Future Test Contract

### 33.1 Required Test Categories

The following test categories would be required for Operational Trade Intent:

| # | Category | Description |
|---|----------|-------------|
| 1 | Construction only from valid TradePlan | Intent cannot be created from INVALID plan |
| 2 | Immutable planning values | entry/stop/target/quantity cannot be changed after creation |
| 3 | plan_id preservation | plan_id is preserved verbatim |
| 4 | Provenance preservation | evaluation_timestamp, instrument preserved |
| 5 | No mutation of TradePlan | Creating intent does not modify the plan |
| 6 | No mutation of MarketScanResult | Creating intent does not modify the scan |
| 7 | No PaperTrade mutation | Creating intent does not modify paper trades |
| 8 | Stale-plan handling | Stale plans produce rejected/expired intents |
| 9 | Duplicate-intent handling | Same plan + context → same intent_id |
| 10 | Supersession | New plan supersedes old intent |
| 11 | Expiry | Intent expires after valid_until |
| 12 | Direction semantics | Only LONG/SHORT allowed; no BUY/SELL |
| 13 | Quantity preservation | Quantity preserved verbatim from plan |
| 14 | Authorization separation | Intent does not contain authorization |
| 15 | Broker neutrality | No broker-specific fields |
| 16 | Order-state separation | No order fields |
| 17 | Position-state separation | No position fields |
| 18 | Deterministic identity | Same inputs → same intent_id |
| 19 | Serialization | Deterministic serialization if required |
| 20 | Point-in-time safety | New data does not mutate existing intent |

## 34. Recommended Operational Trade Intent Contract

### 34.1 What is Operational Trade Intent?

> **Operational Trade Intent** is an immutable, time-bounded, broker-neutral operational snapshot of an eligible TradePlan at a specific point in time. It represents "this plan is operationally eligible for review/authorization" — NOT an execution command, NOT an order, NOT a position, NOT an authorization. It preserves the authoritative planning geometry (entry, stop, target, quantity, risk) verbatim from its source TradePlan and references the plan by identity. It does not mutate the plan, does not authorize execution, and does not contain broker-specific information. It is created from a VALID TradePlan and expires when the plan becomes stale or the market session ends.

### 34.2 What Does It Contain?

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `intent_id` | `str` | Deterministic hash | Operational identity |
| `plan_id` | `str` | TradePlan.plan_id | Provenance reference |
| `instrument` | `str` | TradePlan.instrument | Canonical instrument |
| `timeframe` | `str` | TradePlan.timeframe | Setup timeframe |
| `direction` | `str` | TradePlan.direction | LONG/SHORT |
| `entry` | `Decimal` | TradePlan.entry | Authoritative entry level |
| `stop` | `Decimal` | TradePlan.stop | Authoritative stop level |
| `target` | `Decimal` | TradePlan.target_1 | Authoritative target level |
| `engine_risk_distance` | `Decimal` | TradePlan.engine_risk_distance | Per-unit risk |
| `quantity` | `Decimal` | TradePlan.quantity | Position size |
| `planned_risk` | `Decimal` | TradePlan.planned_risk | Maximum planned loss |
| `maximum_risk` | `Decimal` | TradePlan.maximum_risk | Risk limit |
| `risk_plan_status` | `RiskPlanStatus` | TradePlan.risk_plan_status | Must be VALID |
| `evaluation_timestamp` | `datetime` | TradePlan | When market data was evaluated |
| `created_at` | `datetime` | Explicit | When intent was created |
| `valid_until` | `datetime \| None` | Policy-derived | Expiry timestamp |
| `existing_decision` | `str` | TradePlan.existing_decision | Operational context |
| `actionability` | `str` | TradePlan.actionability | Operational context |
| `version` | `int` | Default 1 | Operational version |

### 34.3 What Does It NOT Contain?

| Prohibited Category | Examples | Reason |
|---------------------|----------|--------|
| Account-level parameters | `account_capital`, `risk_percent` | Belong to TradePlan |
| Execution verbs | `BUY`, `SELL`, `ENTER`, `EXIT`, `HOLD` | Belong to execution layer |
| Broker-specific fields | broker name, account, instrument ID, order type | Belong to broker adapter |
| Order fields | order ID, fill status, execution price | Belong to execution layer |
| Position fields | position ID, net position, holdings | Belong to position layer |
| Authorization fields | `authorized`, `approval_status` | Belong to authorization layer |
| Unsupported features | `target_2`, `target_2_supported` | Architecture does not support |
| Mutable lifecycle state | `status`, `state` | Lifecycle tracked separately |

### 34.4 What Creates It?

**Authoritative source:** A `VALID` TradePlan.

**Creation conditions:**
1. `risk_plan_status == VALID`
2. Complete geometry (entry, stop, target, positive risk distance)
3. Positive quantity within maximum_risk
4. Direction is LONG or SHORT
5. Plan is not stale (within freshness window)
6. Plan identity is present

**Creator:** A future operational layer (human action or explicit policy) — NOT automatic upon `READY_FOR_REVIEW`.

### 34.5 What Can Modify It?

**Nothing.** Operational Trade Intent is **immutable**.

If operational state changes:
- A **new intent** is created (new `intent_id`), OR
- A **separate lifecycle object** tracks state transitions (referencing `intent_id`).

### 34.6 What Destroys/Revokes It?

| Lifecycle Event | Mechanism |
|-----------------|-----------|
| Expiry | Past `valid_until` |
| Staleness | Plan age exceeds freshness window |
| Supersession | New intent created from new plan |
| Cancellation | Human withdraws intent before authorization |
| Invalidation | Plan becomes invalid (rare — plan is immutable) |

**Destruction is logical, not physical.** Expired/revoked intents remain in the audit trail.

### 34.7 What Does It Produce?

**Operational Trade Intent does NOT produce a broker order.**

It produces:
- An **operational snapshot** available for authorization review
- A **reference** for future authorization objects
- An **audit trail** entry

### 34.8 What Consumes It?

Only future operational/authorization/execution layers:
1. **Execution Authorization** — references `intent_id` to determine eligibility
2. **Execution Command** — references `intent_id` to build execution request
3. **Dashboard/UI** — displays intent state (read-only)

## 35. Recommended Lifecycle

### 35.1 Lifecycle States (Separate Lifecycle Object)

```
CREATED → ELIGIBLE → AUTHORIZED → SENT → EXECUTED
   ↓         ↓           ↓         ↓
EXPIRED   REVOKED    REJECTED   FAILED
```

| State | Meaning |
|-------|---------|
| `CREATED` | Intent created from valid plan |
| `ELIGIBLE` | Intent is eligible for authorization review |
| `AUTHORIZED` | Authorization granted (separate authorization record) |
| `SENT` | Sent for execution (separate execution record) |
| `EXECUTED` | Execution attempted (separate execution record) |
| `EXPIRED` | Past `valid_until` |
| `REVOKED` | Authorization revoked |
| `REJECTED` | Authorization denied |
| `FAILED` | Execution failed |

### 35.2 Lifecycle Rules

1. **Intent is immutable** — lifecycle state is tracked separately.
2. **One intent → one authorization** — authorization references intent_id.
3. **One authorization → one execution command** — execution references authorization.
4. **Expired intents cannot be authorized.**
5. **Superseded intents cannot be authorized.**
6. **Revoked authorization does not destroy the intent** — it creates a new lifecycle record.

## 36. Recommended Next Boundary

The next boundary to define is **Execution Authorization** — the layer that answers:

> "Is this particular Operational Trade Intent permitted to be sent toward execution?"

Execution Authorization should:
- Reference `intent_id`
- Be a separate object with separate state
- Be a policy decision (manual or automated)
- NOT be implicit in intent creation
- NOT be implicit in `RiskPlanStatus.VALID`
- NOT be implicit in `READY_FOR_REVIEW`

## 37. Limitations

1. **This checkpoint does not implement Operational Trade Intent.** It defines the contract only.
2. **The temporal policy** (how long an intent remains valid) is not fully specified. It requires a future policy decision.
3. **The authorization mechanism** (manual vs. automated) is not defined. It requires a future design decision.
4. **The supersession detection** (how to determine a new plan supersedes an old plan) requires additional design.
5. **The dashboard representation** of Operational Trade Intent is not designed.
6. **The exact deterministic identity inputs** for `intent_id` require implementation-time decisions.

## 38. Implementation Decision

**NO IMPLEMENTATION CHANGES.**

The contract is defined cleanly with no blocking architectural problems. The repository's existing conventions (frozen+slots dataclasses, deterministic SHA-256 IDs, Decimal monetary precision, one-directional flow) provide a solid foundation for implementing Operational Trade Intent in a future checkpoint.

## 39. Final Verdict

**PASS**

The conceptual Operational Trade Intent contract can be established cleanly with no blocking architectural problems. The frozen TradePlan provides a complete and authoritative source of truth for planning geometry. The repository's existing conventions support a clean, immutable, deterministic, broker-neutral operational intent model. The separation from PaperTrade, Execution Authorization, Execution Command, and Broker is well-defined and enforceable.

---

## Appendix A: Test Baseline

### Test Commands Executed

```
python -m pytest tests/test_trade_planning.py tests/test_paper_trading.py tests/test_paper_trading_operations.py -q --tb=no
```

### Results

```
350 passed, 1 warning in 4.09s
```

### Test Counts

| Test File | Count |
|-----------|-------|
| `tests/test_trade_planning.py` | 158 |
| `tests/test_paper_trading.py` | 114 |
| `tests/test_paper_trading_operations.py` | 78 |
| **Total** | **350** |

No failures. No regressions. Baseline is clean.

## Appendix B: Repository Convention Summary

The following conventions are established across the repository and MUST be preserved:

| Convention | Examples |
|------------|----------|
| Frozen+slots dataclasses | `TradePlan`, `PaperTrade`, `MarketScanResult` |
| Deterministic SHA-256 prefix IDs | `plan-`, `pt-`, `scan-`, `experiment-`, `suite-`, `sel-` |
| Decimal monetary precision | All money/R values stored as `Decimal` |
| Optional fields use `None` | "unobserved" never silently reported as real value |
| No business logic in models | Models are data carriers |
| One-directional flow | `models ← intelligence ← pipeline` |
| No future data / no look-ahead | Point-in-time safety structurally enforced |
| Descriptive only | No BUY/SELL/ENTER/EXIT/HOLD recommendations |
| No broker integration | Data-provider integrations only |
