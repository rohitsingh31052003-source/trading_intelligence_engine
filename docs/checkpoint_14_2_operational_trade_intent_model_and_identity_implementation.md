# Checkpoint 14.2 — Operational Trade Intent Model & Deterministic Identity Implementation

## 1. Purpose

Implement the `OperationalTradeIntent` model and its core deterministic identity/integrity mechanisms as defined by the Checkpoint 14.1 contract. This checkpoint moves from architecture (14.1) to controlled implementation of the operational intent data layer.

## 2. Scope

- **In scope**: `OperationalTradeIntent` model, deterministic `intent_id`, deterministic `content_fingerprint`, model validation, immutability, focused tests.
- **Out of scope**: Dashboard integration, `TradePlanningEngine` integration, `PaperTrade` integration, Execution Authorization, Execution Command, BrokerAdapter, reporting, serialization engine.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Key Artifact |
|------------|--------|--------------|
| 10.8 | FROZEN | Historical Research Pipeline |
| 11.8 | FROZEN | Current-Market Analytical Output (MarketScanResult) |
| 12.6 | FROZEN | Trade Planning + Paper Trading + Performance (TradePlan, PaperTrade) |
| 13.6 | FROZEN | Pre-Execution Architecture (Operational Trade Intent conceptual) |
| 14.1 | ACCEPTED | Operational Trade Intent Implementation Boundary Audit |

## 4. Exact Files Inspected

- `src/engine/models/trade_plan.py` (464 lines) — TradePlan, RiskPlanStatus, QuantityStatus, QuantitySpec
- `src/engine/intelligence/trade_planning.py` — `_plan_id()`, `_canonical_value()`
- `src/engine/intelligence/paper_trading.py` — `_paper_trade_id()`, `_canonical_value()`
- `src/engine/intelligence/trade_planning_serialization.py` — serialization patterns
- `docs/checkpoint_14_1_operational_trade_intent_implementation_boundary_audit.md` — approved contract

## 5. Exact Files Created

- `src/engine/models/operational_trade_intent.py` — model + factory (533 lines)
- `tests/test_operational_trade_intent.py` — focused tests (125 tests, 23 areas A-W)

## 6. Exact Files Modified

- `AGENTS.md` — appended Checkpoint 14.2 entry

## 7. Implemented Model

**File**: `src/engine/models/operational_trade_intent.py`

```python
@dataclass(frozen=True, slots=True)
class OperationalTradeIntent:
    # Identity
    intent_id: str
    plan_id: str

    # Instrument / direction
    instrument: str
    timeframe: str
    direction: str

    # Geometry (copied verbatim from TradePlan)
    entry: Decimal | None
    stop: Decimal | None
    target_1: Decimal | None
    engine_risk_distance: Decimal | None
    engine_reward_distance: Decimal | None
    engine_risk_reward_ratio: Decimal | None

    # Position / risk (copied verbatim from TradePlan)
    quantity: Decimal | None
    planned_risk: Decimal | None
    maximum_risk: Decimal | None
    risk_plan_status: RiskPlanStatus

    # Operational context
    existing_decision: str
    actionability: str

    # Timestamps
    created_at: datetime
    evaluation_timestamp: datetime | None
    valid_until: datetime | None

    # Integrity
    content_fingerprint: str
    version: int = OPERATIONAL_TRADE_INTENT_VERSION

    # Audit trail
    warnings: tuple[str, ...] = ()
    rationale: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
```

## 8. Field-Level Implementation Contract

### MUST PRESERVE (copied verbatim from TradePlan):

| Field | Type | Rationale |
|-------|------|-----------|
| `plan_id` | `str` | Provenance reference |
| `instrument` | `str` | Canonical instrument |
| `timeframe` | `str` | Setup timeframe |
| `direction` | `str` | LONG/SHORT |
| `entry` | `Decimal \| None` | Authoritative entry |
| `stop` | `Decimal \| None` | Authoritative stop |
| `target_1` | `Decimal \| None` | Authoritative target |
| `engine_risk_distance` | `Decimal \| None` | Per-unit risk |
| `engine_reward_distance` | `Decimal \| None` | Per-unit reward |
| `engine_risk_reward_ratio` | `Decimal \| None` | R:R ratio |
| `quantity` | `Decimal \| None` | Position size |
| `planned_risk` | `Decimal \| None` | Max planned loss |
| `maximum_risk` | `Decimal \| None` | Risk limit |
| `risk_plan_status` | `RiskPlanStatus` | Overall status |

### MAY COPY (for reference):

| Field | Type | Rationale |
|-------|------|-----------|
| `existing_decision` | `str` | Operational context |
| `actionability` | `str` | Operational context |
| `warnings` | `tuple[str, ...]` | Audit trail |
| `rationale` | `str` | Audit trail |
| `label` | `str` | Caller identity |
| `metadata` | `tuple[tuple[str, str], ...]` | Audit trail |

### EXCLUDED (remain in TradePlan):

| Field | Rationale |
|-------|-----------|
| `account_capital` | Account-level parameter |
| `risk_percent` | Account-level parameter |

### FORBIDDEN (do not cross):

| Field | Rationale |
|-------|-----------|
| `target_2` | Always None, unsupported |
| `target_2_supported` | Always False, unsupported |

### NEW INTENT FIELDS:

| Field | Type | Rationale |
|-------|------|-----------|
| `intent_id` | `str` | Deterministic operational identity |
| `created_at` | `datetime` | Creation timestamp |
| `evaluation_timestamp` | `datetime \| None` | Market data evaluation time |
| `valid_until` | `datetime \| None` | Policy-derived expiry |
| `content_fingerprint` | `str` | Cryptographic proof of content |
| `version` | `int` | Schema/model version (default 1) |

## 9. TradePlan Source-of-Truth Relationship

TradePlan remains the authoritative planning artifact. `OperationalTradeIntent` is a snapshot/reference derived from TradePlan. The intent MUST NOT recalculate entry, stop, target, quantity, planned risk, maximum risk, reward, R:R, direction, or any planning geometry.

The implementation copies authoritative values from TradePlan verbatim. No transformation functions are applied.

## 10. intent_id Contract

- **Format**: `"intent-" + sha256[:16]`
- **Deterministic**: Same canonical inputs produce the same intent_id
- **Distinct from plan_id**: Planning identity ≠ operational identity
- **Content-bound**: Captures the full operational content + instance discriminator

### Canonical Identity Payload (sorted keys):

```python
{
    "instrument", "timeframe", "direction",
    "entry", "stop", "target_1",
    "engine_risk_distance", "engine_reward_distance", "engine_risk_reward_ratio",
    "quantity", "planned_risk", "maximum_risk",
    "risk_plan_status", "existing_decision", "actionability",
    "created_at", "evaluation_timestamp",
    "label", "metadata"  # metadata sorted for determinism
}
```

### Excluded from identity:

- `plan_id` (provenance, not content)
- `intent_id` itself (derived, not input)
- `account_capital`, `risk_percent` (excluded from intent)
- `target_2`, `target_2_supported` (forbidden)
- `content_fingerprint` (derived separately)
- `valid_until` (policy, not content)
- `version` (metadata, not content)

## 11. content_fingerprint Contract

- **Format**: `"fp-" + sha256[:16]`
- **Purpose**: Verify that the economic intent has not changed

### Canonical Fingerprint Payload (sorted keys):

```python
{
    "instrument", "direction",
    "entry", "stop", "target_1",
    "engine_risk_distance", "engine_reward_distance", "engine_risk_reward_ratio",
    "quantity", "planned_risk", "maximum_risk",
    "risk_plan_status"
}
```

### Excluded from fingerprint:

- `intent_id`, `plan_id` (identity, not content)
- `created_at`, `evaluation_timestamp`, `valid_until` (operational metadata)
- `existing_decision`, `actionability` (context, not economic content)
- `warnings`, `rationale`, `label`, `metadata` (audit, not economic content)
- `account_capital`, `risk_percent` (excluded from intent)
- `target_2`, `target_2_supported` (forbidden)

## 12. Canonicalization Rules

All values are canonicalized through `_canonical_value()` before hashing:

- `None` → `"null"`
- `bool` → `"bool:{value}"` (before Decimal check to reject bool-as-number)
- `Decimal` → `"dec:{value.normalize()!s}"` (trailing zeros stripped)
- `Enum` → `"enum:{ClassName}.{name}"`
- `datetime` → `"dt:{isoformat}"`
- `int` → `"int:{value!s}"`
- `float` → `"num:{value!s}"`
- `str` → `"str:{value!s}"`

All canonical structures use `json.dumps(payload, sort_keys=True, ensure_ascii=False)` for deterministic ordering. Metadata entries are sorted lexicographically.

## 13. Decimal Handling

All financial/price/risk/quantity values preserve `Decimal` semantics from TradePlan. No conversion to float, binary floating-point, or string internally. `Decimal("1.0")` and `Decimal("1.00")` produce the same canonical representation via `Decimal.normalize()`.

## 14. Enum Handling

Enums (`RiskPlanStatus`) are canonicalized as `"enum:{ClassName}.{name}"` to prevent accidental collision between enum types sharing member names.

## 15. Timestamp Handling

- `created_at` is required and must be timezone-aware
- `evaluation_timestamp` is optional; if present, must be timezone-aware
- `valid_until` is optional; if present, must be timezone-aware
- `valid_until >= created_at` is enforced
- Timestamps participate in `intent_id` (instance discriminator) but NOT in `content_fingerprint`

## 16. Version Handling

`version` defaults to `OPERATIONAL_TRADE_INTENT_VERSION = 1`. It is a schema/model version, NOT a strategy version, broker version, or execution version. Version does NOT participate in `intent_id` or `content_fingerprint`.

## 17. Immutability Guarantees

- `@dataclass(frozen=True, slots=True)` — matches all existing model conventions
- No mutable fields (no `list`, `dict`, `set`)
- `tuple` for sequences (warnings, metadata)
- `Decimal` for numerics (immutable)
- `str` for labels/enums (immutable)
- `datetime` for timestamps (immutable)
- No back-references to TradePlan

## 18. Validation Invariants

The `__post_init__` validates:

1. `intent_id` non-empty
2. `plan_id` non-empty
3. `instrument` non-empty (after strip)
4. `direction` in `("LONG", "SHORT", "NONE", "")`
5. `content_fingerprint` starts with `"fp-"`
6. `version >= 1`
7. All timestamps timezone-aware
8. `valid_until >= created_at` (when both present)

The factory (`create_intent_from_plan`) additionally enforces:

9. `risk_plan_status` must be VALID
10. `direction` must be `"LONG"` or `"SHORT"` (not NONE or empty)

## 19. Point-in-Time Guarantees

Intent construction requires NO:
- New candle data
- Future candle data
- MarketScanner reruns
- HistoricalPipeline execution
- OutcomeEvaluator invocation

The intent is created from ALREADY-ESTABLISHED TradePlan field values.

## 20. Mutation/Reference Guarantees

- TradePlan is NEVER mutated by intent creation
- Intent holds NO reference to TradePlan
- Intent holds `plan_id` as a string provenance only
- Both objects are independently immutable

## 21. Broker Neutrality

The model contains NO broker-specific fields: no broker symbol, exchange code, segment, routing, product type, broker account ID, broker credentials, broker order ID, or order type.

## 22. Authorization Separation

The model contains NO authorization fields: no `authorization_id`, `authorization_status`, `approval_state`, `human_approval`, `policy_approval`, `execution_permission`, `authorized`, or `approved` flags.

## 23. Execution Separation

The model contains NO execution fields: no `command_id`, `broker_order_id`, `order_id`, `fill_price`, `fill_quantity`, `execution_timestamp`, `slippage`, `fees`, `position_id`, `realized_pnl`, `execution_mode`, or `account_id`.

## 24. Paper-Trading Separation

The model contains NO paper-trading fields: no `paper_trade_id`, `paper_trade_status`, `simulation_state`, `realized_outcome`, or `exit_state`.

## 25. Test Coverage

**File**: `tests/test_operational_trade_intent.py`

125 tests across 23 areas (A-W):

| Area | Category | Tests |
|------|----------|-------|
| A | Model Construction | 8 |
| B | Frozen Immutability | 7 |
| C | Field Preservation | 21 |
| D | Forbidden-Field Exclusion | 14 |
| E | Decimal Preservation | 7 |
| F | Deterministic intent_id | 11 |
| G | intent_id Independent of Ordering | 1 |
| H | Content Fingerprint | 15 |
| I | Decimal Canonicalization | 3 |
| J | Enum Canonicalization | 1 |
| K | Timestamp Validation | 5 |
| L | Version Validation | 2 |
| M | Broker Neutrality | 1 |
| N | Authorization Separation | 1 |
| O | Execution Separation | 1 |
| P | Paper-Trading Separation | 1 |
| Q | Point-in-Time Independence | 3 |
| R | No Recalculation | 6 |
| S | Repeated Construction | 1 |
| T | Failure Contract | 9 |
| U | SHORT Direction | 2 |
| V | Empty Defaults | 4 |
| W | Fingerprint vs Intent ID | 2 |

## 26. Regression Test Results

### New Tests
```
tests/test_operational_trade_intent.py — 125 passed
```

### Focused Regression
```
tests/test_trade_planning.py — 158 passed
tests/test_paper_trading.py — 114 passed
tests/test_paper_trading_operations.py — 78 passed
Total: 350 passed
```

### Full Suite
```
4974 passed, 2 pre-existing yfinance-related failures, 3 skipped
```

Baseline before Checkpoint 14.2: 4849 passed
After Checkpoint 14.2: 4974 passed (+125 new tests)
Pre-existing failures unchanged (yfinance module not installed in env).

## 27. Limitations

1. **No authorization layer**: Intent creation is necessary but not sufficient for execution. Authorization remains future work.
2. **No execution path**: Intent cannot be executed. Execution Command and Broker Adapter remain future work.
3. **No position/portfolio**: Intent does not track positions or portfolio state.
4. **No broker integration**: Intent is broker-neutral. No broker symbol mapping, no order types.
5. **No live trading**: Intent is a data structure, not a trading system.
6. **No account binding**: Intent references plan_id but does not bind to a specific broker account.
7. **No lifecycle management**: Intent is static. No state machine, no transitions.
8. **No dashboard integration**: Intent is backend-only until dashboard presentation is designed.
9. **No dedicated serializer**: Checkpoint 14.2 implements only the model. Serialization infrastructure reserved for a later checkpoint.
10. **No reporting**: Reporting formatter reserved for a later checkpoint.

## 28. Implementation Decision

**IMPLEMENTATION APPROVED.**

This checkpoint implements:
- `OperationalTradeIntent` model (frozen+slots, immutable)
- Deterministic `intent_id` ("intent-" + sha256[:16])
- Deterministic `content_fingerprint` ("fp-" + sha256[:16])
- Model validation (10 invariants)
- `create_intent_from_plan()` factory
- 125 focused tests
- No integration with dashboard, planning engine, or paper trading

## 29. Final Verdict

**PASS**

The `OperationalTradeIntent` model has been implemented according to the approved 14.1 contract. Deterministic identity/integrity is proven, immutability is enforced, and no frozen architecture was disturbed. All 125 new tests pass. All 350 planning/paper-trading regression tests pass. The full suite shows 4974 passed (125 new) with only pre-existing yfinance failures unchanged.
