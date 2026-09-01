# Checkpoint 14.1 — Operational Trade Intent Implementation Boundary Audit

## 1. Purpose

Determine exactly WHERE and HOW Operational Trade Intent should be implemented, based on the architecture already established and frozen through Checkpoint 13. This audit establishes the implementation contract for Checkpoint 14.2+ without modifying any production code.

## 2. Scope

- **In scope**: TradePlan model, TradePlanningEngine, PaperTradingEngine, PaperTrade model, dashboard services, dashboard paper-trade operations, serialization patterns, identity patterns, canonicalization helpers, frozen Checkpoint 13 contract documents.
- **Out of scope**: Execution Authorization, Execution Command, Broker Adapter, Broker Order, Position, Portfolio, live trading, broker integration.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Key Artifact |
|------------|--------|--------------|
| 10.8 | FROZEN | Historical Research Pipeline |
| 11.8 | FROZEN | Current-Market Analytical Output (MarketScanResult) |
| 12.6 | FROZEN | Trade Planning + Paper Trading + Performance (TradePlan, PaperTrade) |
| 13.1 | FROZEN | Operational Trade Intent / Execution Boundary |
| 13.2 | FROZEN | Operational Trade Intent Contract |
| 13.3 | FROZEN | Execution Authorization Boundary |
| 13.4 | FROZEN | Execution Authorization → Execution Command |
| 13.5 | FROZEN | Execution Command → Broker Adapter |
| 13.6 | FROZEN | Final Execution Architecture Integration & Freeze |

## 4. Exact Files Inspected

### Production Code
- `src/engine/models/trade_plan.py` (464 lines) — TradePlan, RiskPlanStatus, QuantityStatus, QuantitySpec, DEFAULT_QUANTITY_SPEC
- `src/engine/intelligence/trade_planning.py` (967 lines) — TradePlanningEngine, plan(), _plan_id()
- `src/engine/config/trade_plan_config.py` (119 lines) — TradePlanConfig
- `src/engine/intelligence/trade_planning_serialization.py` (251 lines) — serialize_trade_plan, deserialize_trade_plan
- `src/engine/reporting/trade_planning.py` (174 lines) — TradePlanFormatter
- `src/engine/models/paper_trade.py` (500+ lines) — PaperTrade, PaperTradeStatus, PaperExitReason
- `src/engine/intelligence/paper_trading.py` — PaperTradingEngine.create(), _paper_trade_id()
- `src/engine/intelligence/paper_trading_serialization.py` (264 lines) — paper trade serialization
- `src/engine/models/trade_candidate.py` — TradeCandidate, CandidateDirection, CandidateStatus, SetupType
- `src/engine/models/trade_decision.py` — TradeDecision, DecisionClassification, DecisionScore
- `src/engine/models/opportunity.py` — TradeOpportunity, OpportunityStatus, EligibilityStatus
- `src/engine/models/market_scan.py` — MarketScanResult, InstrumentScanResult, MTFAlignment
- `src/engine/models/pipeline.py` — PipelineResult, PipelineEvaluationPoint
- `src/engine/experiment/config.py` — _canonicalize() helper (lines 205-320)
- `src/dashboard/services.py` — DashboardAnalysisService, TradePlanRequest, PaperTradeRequest, plan_trade(), create_paper_trade(), run_paper_trading_cycle()
- `src/dashboard/paper_trade_operations.py` — PaperTradingOperations, OperationsConfig, run_once(), _create_eligible_trade()
- `src/dashboard/views.py` — TradePlanView, PaperTradeView, ActionabilityState, derive_actionability()
- `src/dashboard/app.py` — /api/trade-plan, /api/paper-trades, /api/paper-trading/run-once routes

### Documentation
- `docs/checkpoint_13_1_operational_trade_intent_and_execution_boundary_audit.md`
- `docs/checkpoint_13_2_operational_trade_intent_model_and_contract_audit.md`
- `docs/checkpoint_13_3_execution_authorization_boundary_audit.md`
- `docs/checkpoint_13_4_execution_authorization_to_execution_command_boundary_audit.md`
- `docs/checkpoint_13_5_execution_command_to_broker_adapter_boundary_audit.md`
- `docs/checkpoint_13_6_final_execution_architecture_integration_and_freeze_audit.md`

### Tests
- `tests/test_trade_planning.py` (155 tests, 40 areas A-AT)
- `tests/test_paper_trading.py` (111 tests, 38 areas A-AL)
- `tests/test_paper_trading_operations.py` (78 tests, 38 areas A-AJ)

## 5. Current Implementation Inventory

### Operational Trade Intent
**STATUS: DOES NOT EXIST**

No class, model, function, or data structure named OperationalTradeIntent, TradeIntent, or OperationalIntent exists in production code. No `intent_id` field exists anywhere in `src/`.

### Execution Authorization
**STATUS: DOES NOT EXIST**

No authorization layer. The only `authorization` string in production code is the HTTP `Authorization: Bearer <token>` header in `historical_provider.py` (Upstox data provider).

### Execution Command
**STATUS: DOES NOT EXIST**

No `command_id`, ExecutionCommand, or command layer exists.

### Broker Adapter
**STATUS: DOES NOT EXIST**

No broker, order, position, or portfolio production code exists. All references are in docstrings/comments explaining what the system does NOT do.

### What DOES Exist
- `intent` — used descriptively in docstrings for directional intent (LONG/SHORT/NONE)
- `position` — price position within range (range_detection.py), position sizing (trade_planning.py)
- `execution` — "execution price" (signal.py), "execution timeframe" (market_scan_config.py)

## 6. Whether Operational Trade Intent Already Exists

**NO.** Verified by exhaustive search of `src/` and `scripts/`. No production class, function, or data structure implements any concept of operational trade intent, intent identity, or intent fingerprinting.

## 7. Recommended Package/Module Location

### Model
**File**: `src/engine/models/operational_trade_intent.py`

**Rationale**:
- Follows the established pattern: one model file per domain concept (trade_plan.py, paper_trade.py, trade_candidate.py)
- Lives in `src/engine/models/` (the model layer, no engine dependencies)
- Imports only from `src/engine/models/trade_plan.py` (RiskPlanStatus) and stdlib
- No circular dependency: models ← intelligence ← pipeline direction preserved

### Engine/Service
**File**: `src/engine/intelligence/operational_trade_intent.py`

**Rationale**:
- Follows the established pattern: engine files in `src/engine/intelligence/`
- Contains the `OperationalTradeIntentEngine` (or factory function) that transforms TradePlan → OperationalTradeIntent
- Imports from `src/engine/models/operational_trade_intent.py` and `src/engine/models/trade_plan.py`
- `intelligence/__init__.py` stays empty (import via full path)

### Serialization
**File**: `src/engine/intelligence/operational_trade_intent_serialization.py`

**Rationale**:
- Follows the established pattern: each domain has its own serializer in `src/engine/intelligence/`
- Standalone `_to_json`/`_from_json`/`_decode_field` with `_DATACLASSES`/`_ENUMS` maps
- Schema version constant: `OPERATIONAL_TRADE_INTENT_SCHEMA_VERSION = 1`

### Reporting
**File**: `src/engine/reporting/operational_trade_intent.py`

**Rationale**:
- Follows the established pattern: reporting formatters in `src/engine/reporting/`
- Imported via full path (NOT re-exported from `reporting/__init__.py`, matching 11O-12E convention)

### Config
**Not required at this layer.** Operational Trade Intent has no strategy/optimization/scoring parameters. The only configuration is structural (identity prefix, schema version).

## 8. Proposed Ownership Boundary

```
src/engine/models/operational_trade_intent.py          ← data contract (frozen+slots)
src/engine/intelligence/operational_trade_intent.py    ← creation/validation logic
src/engine/intelligence/operational_trade_intent_serialization.py  ← persistence
src/engine/reporting/operational_trade_intent.py       ← human-readable report
```

The model file is a leaf dependency (no internal engine imports). The engine file depends on the model file. The serializer depends on the model file. The reporter depends on the model file. No cycles.

## 9. TradePlan → Intent Dependency Direction

```
TradePlan (frozen, Product Phase 4)
    │
    ├──→ PaperTrade (frozen, Product Phase 5)  [sibling path, unchanged]
    │
    └──→ Operational Trade Intent (new)        [sibling path, new]
```

**NOT**:
- OperationalTradeIntent → TradePlan (reverse dependency)
- TradePlan ↔ OperationalTradeIntent (circular)
- OperationalTradeIntent → PaperTrade (no coupling)

The dependency direction is strictly: `models ← intelligence`. The intent engine consumes TradePlan by value (frozen), never by mutable reference.

## 10. Field-Level Contract

### Verified TradePlan Fields (27 fields, frozen+slots)

| # | Field | Type | Default | Role |
|---|-------|------|---------|------|
| 1 | `plan_id` | `str` | required | Planning identity |
| 2 | `instrument` | `str` | required | Canonical instrument |
| 3 | `timeframe` | `str` | required | Setup timeframe |
| 4 | `direction` | `str` | required | LONG/SHORT/NONE/""
| 5 | `existing_decision` | `str` | required | REJECTED/WATCH/QUALIFIED/PREFERRED/""
| 6 | `actionability` | `str` | required | ActionabilityState name/""
| 7 | `account_capital` | `Decimal \| None` | required | Account-level (None if invalid)
| 8 | `risk_percent` | `Decimal \| None` | required | Account-level (None if invalid)
| 9 | `maximum_risk` | `Decimal \| None` | required | Risk limit
| 10 | `entry` | `Decimal \| None` | required | Entry level
| 11 | `stop` | `Decimal \| None` | required | Stop level
| 12 | `target_1` | `Decimal \| None` | required | Target level
| 13 | `engine_risk_distance` | `Decimal \| None` | required | Per-unit risk
| 14 | `engine_reward_distance` | `Decimal \| None` | required | Per-unit reward
| 15 | `engine_risk_reward_ratio` | `Decimal \| None` | required | R:R ratio
| 16 | `target_2` | `Decimal \| None` | `None` | ALWAYS None
| 17 | `target_2_supported` | `bool` | `False` | ALWAYS False
| 18 | `quantity` | `Decimal \| None` | `None` | Position size
| 19 | `planned_risk` | `Decimal \| None` | `None` | Max planned loss
| 20 | `planned_reward` | `Decimal \| None` | `None` | Potential reward
| 21 | `quantity_status` | `QuantityStatus` | `UNSIZED` | How qty computed
| 22 | `risk_plan_status` | `RiskPlanStatus` | `GEOMETRY_UNAVAILABLE` | Overall status
| 23 | `quantity_spec_available` | `bool` | `False` | Spec supplied?
| 24 | `warnings` | `tuple[str, ...]` | `()` | Human-readable
| 25 | `rationale` | `str` | `""` | Status explanation
| 26 | `label` | `str` | `""` | Caller identity
| 27 | `metadata` | `tuple[tuple[str, str], ...]` | `()` | Audit trail |

### Field Classification for Operational Trade Intent

**COPY VERBATIM (MUST PRESERVE):**

| Field | Type | Rationale |
|-------|------|-----------|
| `plan_id` | `str` | Provenance reference to source TradePlan |
| `instrument` | `str` | Canonical instrument name |
| `timeframe` | `str` | Setup timeframe |
| `direction` | `str` | LONG/SHORT |
| `entry` | `Decimal \| None` | Authoritative entry level |
| `stop` | `Decimal \| None` | Authoritative stop level |
| `target_1` | `Decimal \| None` | Authoritative target level |
| `engine_risk_distance` | `Decimal \| None` | Per-unit risk |
| `engine_reward_distance` | `Decimal \| None` | Per-unit reward |
| `engine_risk_reward_ratio` | `Decimal \| None` | R:R ratio |
| `quantity` | `Decimal \| None` | Position size |
| `planned_risk` | `Decimal \| None` | Maximum planned loss |
| `maximum_risk` | `Decimal \| None` | Risk limit |
| `risk_plan_status` | `RiskPlanStatus` | Must be VALID for intent |

**COPY FOR REFERENCE (MAY COPY):**

| Field | Type | Rationale |
|-------|------|-----------|
| `existing_decision` | `str` | Operational context (REJECTED/WATCH/QUALIFIED/PREFERRED) |
| `actionability` | `str` | Operational context |
| `planned_reward` | `Decimal \| None` | Descriptive |
| `quantity_status` | `QuantityStatus` | Descriptive |
| `quantity_spec_available` | `bool` | Descriptive |
| `warnings` | `tuple[str, ...]` | Audit trail |
| `rationale` | `str` | Audit trail |
| `label` | `str` | Caller identity |
| `metadata` | `tuple[tuple[str, str], ...]` | Audit trail |

**EXCLUDED (MUST REMAIN IN TradePlan):**

| Field | Type | Rationale |
|-------|------|-----------|
| `account_capital` | `Decimal \| None` | Account-level parameter, not operational |
| `risk_percent` | `Decimal \| None` | Account-level parameter, not operational |

**FORBIDDEN (MUST NOT CROSS):**

| Field | Type | Rationale |
|-------|------|-----------|
| `target_2` | `Decimal \| None` | Always None, unsupported |
| `target_2_supported` | `bool` | Always False, unsupported |

**NEW FIELDS (intent-only):**

| Field | Type | Rationale |
|-------|------|-----------|
| `intent_id` | `str` | Deterministic operational identity |
| `created_at` | `datetime` | Creation timestamp (explicit, deterministic) |
| `evaluation_timestamp` | `datetime \| None` | When market data was evaluated |
| `valid_until` | `datetime \| None` | Policy-derived expiry |
| `content_fingerprint` | `str` | Cryptographic proof of intent content |
| `version` | `int` | Operational version (default 1) |

## 11. Identity Contract

### intent_id

- **Format**: `"intent-" + sha256[:16]`
- **Deterministic**: Same canonical inputs → same intent_id
- **Distinct from plan_id**: Planning identity ≠ operational identity
- **Content-bound**: Captures the full operational content
- **Collision-resistant**: SHA-256 prefix (6.5 × 10^18 combinations)

### Canonical Identity Inputs

The following fields MUST be included in the intent_id hash (canonical, sorted):

```
instrument, timeframe, direction, entry, stop, target_1,
engine_risk_distance, engine_reward_distance, engine_risk_reward_ratio,
quantity, planned_risk, maximum_risk, risk_plan_status,
existing_decision, actionability, created_at, evaluation_timestamp,
label, metadata
```

The following fields MUST NOT be included in the intent_id hash:
- `plan_id` (provenance, not content — but stored on the intent)
- `intent_id` itself (derived, not input)
- `account_capital`, `risk_percent` (excluded from intent)
- `target_2`, `target_2_supported` (forbidden)
- `content_fingerprint` (derived separately)
- `valid_until` (policy, not content)
- `version` (metadata, not content)

### Identity Generation Pattern

Reuse the established pattern from `_paper_trade_id()` in `paper_trading.py`:

```python
canonical = json.dumps({
    "instrument": _canonical_value(instrument),
    "timeframe": _canonical_value(timeframe),
    ...  # all content fields
    "created_at": _canonical_value(created_at),
    "label": _canonical_value(label),
    "metadata": [_canonical_value(k) + "=" + _canonical_value(v) for k, v in metadata],
}, sort_keys=True)
digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
return f"intent-{digest[:16]}"
```

## 12. Content Fingerprint Contract

### Purpose

Allow downstream authorization to prove: "The intent being authorized is the same intent later presented for execution."

### Fingerprint Format

`"fp-" + sha256[:16]` of canonical content fields.

### Fingerprint Inputs

The content fingerprint MUST include ALL fields that carry economic meaning:
- `instrument`, `timeframe`, `direction`
- `entry`, `stop`, `target_1`
- `engine_risk_distance`, `engine_reward_distance`, `engine_risk_reward_ratio`
- `quantity`, `planned_risk`, `maximum_risk`
- `risk_plan_status`

The fingerprint MUST NOT include:
- `intent_id`, `plan_id` (identity, not content)
- `created_at`, `valid_until`, `version` (operational metadata)
- `existing_decision`, `actionability` (context, not economic content)
- `warnings`, `rationale`, `label`, `metadata` (audit, not economic content)
- `account_capital`, `risk_percent` (excluded from intent)
- `target_2`, `target_2_supported` (forbidden)

### Decimal Normalization

All Decimal values MUST be normalized to a canonical string form before hashing. Use the established `_canonical_value()` pattern:
- `Decimal("100.50")` → `"dec:100.5"` (trailing zeros stripped via `normalize()`)
- `None` → `"null"`

### Enum Normalization

Enums MUST be represented by their `.name` string (matching the `_canonicalize()` pattern in `experiment/config.py`).

### Ordering

All canonical structures MUST use `sort_keys=True` in `json.dumps()` for deterministic ordering.

## 13. Immutability Requirements

### Structural Immutability

- `@dataclass(frozen=True, slots=True)` — matches all existing model conventions
- No mutable fields (no `list`, `dict`, `set`)
- `tuple` for sequences (warnings, metadata)
- `Decimal` for numerics (immutable)
- `str` for labels/enums (immutable)
- `datetime` for timestamps (immutable)
- `RiskPlanStatus` enum (frozen)

### Nested Immutability

- `metadata: tuple[tuple[str, str], ...]` — tuple of tuples, fully immutable
- `warnings: tuple[str, ...]` — tuple of strings, fully immutable
- No nested dicts or lists
- No back-references to TradePlan or other mutable objects

### No Back-References

The intent MUST NOT hold a reference to the source TradePlan. It holds `plan_id` as a provenance string only. This prevents:
- Accidental mutation of TradePlan through intent
- Circular reference chains
- Serialization complexity

### Replacement Semantics

If operational state changes (e.g., new intent required), a NEW intent object is created with a new `intent_id`. Existing intents are NEVER mutated.

## 14. Source-of-Truth Analysis

### TradePlan = Planning Truth

TradePlan remains the authoritative source for:
- Entry, stop, target levels
- Risk/reward distances
- Quantity and planned risk
- Risk calculation status

### Operational Trade Intent = Immutable Operational Snapshot

The intent is a read-only projection of a VALID TradePlan. It MUST NOT:
- Recalculate entry, stop, or target
- Recalculate quantity or planned risk
- Reinterpret direction
- Modify geometry
- Apply broker normalization
- Apply price tick rounding
- Apply quantity step rounding

### Enforcement Mechanism

The intent engine copies fields VERBATIM from TradePlan. No transformation functions are applied. The only derivation is:
- `intent_id` = hash of canonical content
- `content_fingerprint` = hash of economic content
- `created_at` = explicit parameter (not derived)
- `valid_until` = policy parameter (not derived)

## 15. Point-in-Time Analysis

### No Future Data Required

Creating Operational Trade Intent requires NO:
- New candle data
- Future candle data
- MarketScanner reruns
- HistoricalPipeline execution
- OutcomeEvaluator invocation
- Market refresh
- Strategy recalculation

### Input Requirements

The intent is created from an ALREADY-ESTABLISHED TradePlan. The TradePlan itself is created from an ALREADY-ESTABLISHED TradeCandidate/GeometryView (which was derived from candles[:T+1] at evaluation time T).

### No Accidental Re-Analysis

The intent engine is a pure function: `TradePlan + context → OperationalTradeIntent`. It has no access to:
- Market data providers
- Candle stores
- Scanner instances
- Pipeline instances

## 16. Mutation/Reference Analysis

### TradePlan → Intent Relationship

```
TradePlan (frozen, exists)
    │
    ├── fields copied by VALUE into
    │
    └──→ OperationalTradeIntent (new, frozen)
```

- TradePlan is NEVER mutated by intent creation
- Intent holds NO reference to TradePlan
- Intent holds `plan_id` as a string provenance only
- Both objects are independently immutable

### Nested Value Safety

| TradePlan Field | Type | Intent Handling |
|-----------------|------|-----------------|
| `warnings` | `tuple[str, ...]` | Copy reference (tuples are immutable) |
| `metadata` | `tuple[tuple[str, str], ...]` | Copy reference (tuples are immutable) |
| `rationale` | `str` | Copy reference (strings are immutable) |
| All `Decimal` fields | `Decimal \| None` | Copy reference (Decimal is immutable) |
| All `str` fields | `str` | Copy reference (strings are immutable) |
| `risk_plan_status` | `RiskPlanStatus` | Copy reference (enum is immutable) |

No deep copy required — all values are immutable types.

## 17. Serialization Analysis

### Pattern

Follow the established serialization pattern (16 standalone serializers in `src/engine/intelligence/`):

```python
OPERATIONAL_TRADE_INTENT_SCHEMA_VERSION = 1

def serialize_intent(intent: OperationalTradeIntent) -> str:
    return json.dumps({
        "schema_version": 1,
        "intent": _to_json(intent)
    }, sort_keys=True, ensure_ascii=False)

def deserialize_intent(payload: str) -> OperationalTradeIntent:
    parsed = json.loads(payload)
    if parsed.get("schema_version") != 1:
        raise ValueError(...)
    return _from_json(parsed["intent"], OperationalTradeIntent)
```

### Type Tags (established convention)

- `__enum__` + `__enum_class__` for enums (matching decision_intelligence_serialization pattern)
- `__decimal__` for Decimal values
- `__dataclass__` for nested dataclasses
- `__tuple__` for tuples
- `__datetime__` for timestamps

### Type Maps

```python
_DATACLASSES = {"OperationalTradeIntent": OperationalTradeIntent}
_ENUMS = {"RiskPlanStatus": RiskPlanStatus}
```

### Decimal Handling

Decimal values stored as `{"__decimal__": str(value)}` to preserve precision.

### Schema Versioning

`OPERATIONAL_TRADE_INTENT_SCHEMA_VERSION = 1` — checked before model reconstruction. Future versions rejected with clear error.

## 18. Consumer Analysis

### Current Consumers (must NOT be modified in 14.1)

| Consumer | Current Behavior | Impact of Intent |
|----------|------------------|------------------|
| `DashboardAnalysisService.plan_trade()` | Builds TradePlan, returns TradePlanView | May eventually present intent |
| `DashboardAnalysisService.create_paper_trade()` | Builds TradePlan → PaperTrade | Unchanged (PaperTrade is sibling path) |
| `PaperTradingOperations.run_once()` | Builds TradePlan → PaperTrade | Unchanged (PaperTrade is sibling path) |
| `TradePlanView` | Presentation projection of TradePlan | Unchanged |
| `PaperTradeView` | Presentation projection of PaperTrade | Unchanged |

### Future Consumers (NOT created in 14.1)

| Future Consumer | Purpose |
|-----------------|---------|
| Execution Authorization | Consumes intent, produces authorization |
| Execution Command | Consumes authorized intent, produces command |
| Dashboard (eventual) | May present intent separately from plan |
| Audit logging | Records intent creation |

### No Current Consumer Modification Required

The intent implementation is ADDITIVE. No existing consumer needs modification. The intent is a new sibling path from TradePlan.

## 19. Paper-Trading Separation

### Current State (FROZEN, must NOT change)

```
TradePlan
    ↓
PaperTrade
```

`PaperTradingEngine.create(plan=plan, plan_id=plan.plan_id, ...)` reads fields from TradePlan via `_attr()` helper. This path is FROZEN.

### New State (after 14.2+)

```
TradePlan
    ├──→ PaperTrade (UNCHANGED)
    └──→ OperationalTradeIntent (NEW)
```

### Key Separation Rules

1. PaperTrade does NOT consume OperationalTradeIntent
2. OperationalTradeIntent does NOT consume PaperTrade
3. Both are sibling paths from TradePlan
4. PaperTrade eligibility gate (READY_FOR_REVIEW) is UNCHANGED
5. PaperTrade creation flow is UNCHANGED
6. No new coupling between intent and paper trading

## 20. Dashboard Separation

### Current State

- Dashboard consumes TradePlan via `plan_trade()` → `TradePlanView`
- Dashboard creates PaperTrades via `create_paper_trade()` → `PaperTradeView`
- Dashboard runs operations via `run_paper_trading_cycle()` → `OperationsCycleView`

### After Implementation

- Dashboard MAY eventually present OperationalTradeIntent separately
- No dashboard behavior changes in 14.1
- `READY_FOR_REVIEW` remains a presentation mirror, NOT authorization
- No new dashboard routes in 14.1

## 21. Authorization Separation

### Operational Trade Intent MUST NOT contain:

- `authorization_id`
- `authorization_status`
- `approval_state`
- `human_approval`
- `policy_approval`
- `execution_permission`
- `authorized` flag
- `approved` flag

These belong to the future authorization layer (Checkpoint 13.3).

## 22. Execution Separation

### Operational Trade Intent MUST NOT contain:

- `command_id`
- `broker_order_id`
- `order_id`
- `fill_price`
- `fill_quantity`
- `execution_timestamp`
- `broker_symbol`
- `exchange`
- `routing`
- `broker_credentials`
- `slippage`
- `fees`
- `position_id`
- `realized_pnl`
- `execution_mode`
- `account_id`

These belong to the future execution layer (Checkpoints 13.4-13.5).

## 23. Broker-Neutrality

### Operational Trade Intent MUST NOT contain:

- Upstox symbol
- Broker symbol
- Exchange code
- Segment
- Product type
- Routing
- Broker credentials
- Broker account ID
- Order type (market/limit/stop)
- Time in force

The intent is broker-neutral. It expresses WHAT (instrument, direction, quantity) and WHERE (entry, stop, target), not HOW (broker, order type, routing).

## 24. Economic Integrity

### Fields Preserved Verbatim

| Field | Economic Meaning |
|-------|------------------|
| `instrument` | What to trade |
| `direction` | Long or short |
| `entry` | Price to enter |
| `stop` | Price to exit at loss |
| `target_1` | Price to exit at profit |
| `engine_risk_distance` | Per-unit risk amount |
| `engine_reward_distance` | Per-unit reward amount |
| `engine_risk_reward_ratio` | Risk/reward ratio |
| `quantity` | How many units |
| `planned_risk` | Maximum monetary loss |
| `maximum_risk` | Risk limit |

### Prohibited Transformations

- No quantity increase
- No risk increase
- No direction change
- No entry/stop/target modification
- No rounding (no tick rounding, no quantity step rounding)
- No broker normalization

## 25. Account-Data Boundary

### Remain in TradePlan (do NOT cross into intent):

| Field | Rationale |
|-------|-----------|
| `account_capital` | Account-level parameter |
| `risk_percent` | Account-level parameter |

Account binding belongs to the future authorization/execution architecture. The intent references the TradePlan by `plan_id` but does not carry account data.

### No Account Reference in Intent

The intent does not need an account reference at this layer. The TradePlan was already computed for a specific account, and the `plan_id` provides provenance.

## 26. Lifecycle Boundary

### Conceptual Lifecycle

```
TradePlan exists (VALID)
    ↓
Intent created (immutable)
    ↓
Intent may be presented to Authorization
    ↓
[FUTURE] Authorization decision
    ↓
[FUTURE] Execution
```

### States NOT in Intent

The intent does NOT have lifecycle states. These belong elsewhere:

| State | Belongs To |
|-------|------------|
| AUTHORIZED | Execution Authorization layer |
| EXECUTING | Execution Command layer |
| FILLED | Broker Order layer |
| CANCELLED | Execution Command layer |
| CLOSED | Position layer |
| EXPIRED | Policy layer |

The intent is a STATIC snapshot. It does not transition.

## 27. Failure Contract

### When Intent Creation is Impossible

| Condition | Behavior |
|-----------|----------|
| `plan is None` | Raise `ValueError("TradePlan is required")` |
| `plan.risk_plan_status != VALID` | Raise `ValueError("Cannot create intent from non-VALID TradePlan")` |
| `plan.direction not in ("LONG", "SHORT")` | Raise `ValueError("Intent requires directional bias")` |
| Missing required field | Raise `ValueError` with specific field |
| `created_at` is None | Raise `ValueError("created_at is required")` |
| Serialization failure | Raise `OperationalTradeIntentError` |

### Error Type

A new exception class: `OperationalTradeIntentError` (in `src/engine/intelligence/operational_trade_intent.py`).

### No Silent Failures

Intent creation NEVER returns a partial or invalid object. It either returns a valid `OperationalTradeIntent` or raises.

## 28. Test Architecture

### New Test File

**File**: `tests/test_operational_trade_intent.py`

### Test Categories (for 14.2+)

| Area | Category | Minimum Tests |
|------|----------|---------------|
| A | Model Construction | frozen/slots/validation/enum/invariants |
| B | Field Preservation | All MUST PRESERVE fields copied verbatim |
| C | Field Exclusion | Account fields excluded, forbidden fields excluded |
| D | Deterministic intent_id | Same inputs → same id |
| E | Deterministic fingerprint | Same content → same fingerprint |
| F | Decimal normalization | Trailing zeros, scientific notation |
| G | Metadata ordering | Canonical ordering preserved |
| H | Immutability | Frozen dataclass, no mutation |
| I | Nested Immutability | Tuples immutable |
| J | No TradePlan Mutation | Source plan unchanged after intent creation |
| K | Round-trip Serialization | serialize → deserialize → identical |
| L | Schema Versioning | Version 1 accepted, future rejected |
| M | Point-in-time Safety | No candle/provider/store access |
| N | Broker Neutrality | No broker-specific fields |
| O | Authorization Separation | No authorization fields |
| P | Execution Separation | No execution fields |
| Q | PaperTrade Sibling Path | PaperTrade unchanged by intent |
| R | Failure Contract | Invalid inputs raise, no silent failures |
| S | Economic Integrity | No transformation of economic values |
| T | Determinism | Repeated calls identical |

### Existing Tests Must Remain Green

- `tests/test_trade_planning.py` (155 tests)
- `tests/test_paper_trading.py` (111 tests)
- `tests/test_paper_trading_operations.py` (78 tests)
- `tests/test_dashboard.py` (67 tests)
- `tests/test_workstation.py` (95 tests)
- `tests/test_watchlist_scanner.py` (75 tests)
- All Sprint 11A-12E tests (4849+ total)

## 29. Regression Requirements

### Frozen Checkpoint Verification

| Checkpoint | Verification |
|------------|--------------|
| Sprint 11A-12E | All 4849+ tests pass |
| Product Phase 1-3 | Dashboard tests pass |
| Product Phase 4 | Trade planning tests pass (155 tests) |
| Product Phase 5 | Paper trading tests pass (111 + 78 tests) |
| Product Phase 5 Operations | Operations tests pass |
| Product Phase 6A-6F | Historical/live tests pass |
| Checkpoint 13.1-13.6 | No execution code exists (verified) |

### Pipeline Baseline

`signals=4, trades=3` — the canonical pipeline regression baseline must remain unchanged.

## 30. Implementation Plan for 14.2+

### Step 1: Create Model File

**File**: `src/engine/models/operational_trade_intent.py`

Contents:
- `OperationalTradeIntent` (@dataclass(frozen=True, slots=True))
- `OperationalTradeIntentStatus` (NOT a lifecycle state — structural status only, e.g., CREATED)
- Module docstring with explicit "NOT authorization, NOT execution, NOT broker" statement
- `__all__` export

### Step 2: Create Engine File

**File**: `src/engine/intelligence/operational_trade_intent.py`

Contents:
- `OperationalTradeIntentEngine` (or factory function `create_intent(...)`)
- `OperationalTradeIntentError` exception
- `create_intent_from_plan(plan, created_at, ...) → OperationalTradeIntent`
- Validation logic (plan must be VALID, directional, etc.)
- `_intent_id(...)` — deterministic identity generation
- `_content_fingerprint(...)` — cryptographic fingerprint
- `__all__` export

### Step 3: Create Serializer File

**File**: `src/engine/intelligence/operational_trade_intent_serialization.py`

Contents:
- `OPERATIONAL_TRADE_INTENT_SCHEMA_VERSION = 1`
- `serialize_intent()`, `deserialize_intent()`, `parse_intent_header()`
- `_to_json()`, `_from_json()`, `_decode_field()`
- `_DATACLASSES`, `_ENUMS` maps
- `__all__` export

### Step 4: Create Reporter File

**File**: `src/engine/reporting/operational_trade_intent.py`

Contents:
- `OperationalTradeIntentFormatter`
- Sections: Identity, Plan Provenance, Instrument/Direction, Geometry, Position, Status, Fingerprint, Warnings, Rationale, Disclaimer
- Returns str (no print())
- `__all__` export

### Step 5: Create Tests

**File**: `tests/test_operational_trade_intent.py`

Contents: 20+ test areas (A-T per Section 28), 80+ tests.

### Step 6: Update AGENTS.md

Append Checkpoint 14.2 entry with implementation summary.

### Implementation Order

1. Model (no dependencies)
2. Engine (depends on model)
3. Serializer (depends on model)
4. Reporter (depends on model)
5. Tests (depend on all above)
6. AGENTS.md update

## 31. Limitations

1. **No authorization layer**: Intent creation is necessary but not sufficient for execution. Authorization remains future work.
2. **No execution path**: Intent cannot be executed. Execution Command and Broker Adapter remain future work.
3. **No position/portfolio**: Intent does not track positions or portfolio state.
4. **No broker integration**: Intent is broker-neutral. No broker symbol mapping, no order types.
5. **No live trading**: Intent is a data structure, not a trading system.
6. **No account binding**: Intent references plan_id but does not bind to a specific broker account.
7. **No lifecycle management**: Intent is static. No state machine, no transitions.
8. **No dashboard integration in 14.1**: Intent is backend-only until dashboard presentation is designed.

## 32. Implementation Decision

**NO IMPLEMENTATION CHANGES IN 14.1.**

This audit establishes the complete implementation contract for Checkpoint 14.2+. All field classifications, identity rules, immutability requirements, serialization patterns, and test architectures are defined. No genuine architectural contradiction or blocking defect was discovered.

## 33. Final Verdict

**PASS**

The Operational Trade Intent implementation boundary is clearly defined, does not conflict with frozen Checkpoints 11-13, and can be implemented safely in the next checkpoint. The contract is complete:

- Immutable intent (frozen+slots)
- Deterministic intent_id ("intent-" + sha256[:16])
- Deterministic content fingerprint ("fp-" + sha256[:16])
- No recalculation of TradePlan values
- No mutation of TradePlan
- No broker-specific data
- No authorization data
- No execution data
- No position data
- No paper-trading coupling
- No future-data dependency
- No live execution path
