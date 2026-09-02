# Checkpoint 15.2 — Execution Authorization Model & Identity Implementation

## Objective
Implement the Execution Authorization domain model and deterministic identity foundation per the Checkpoint 15.1 boundary audit. No dashboard, planning engine, paper trading, execution, or broker integration.

## Scope
- `src/engine/models/execution_authorization.py` — model + factory
- `tests/test_execution_authorization.py` — 97 tests

## Design Decisions

### Model
- `ExecutionAuthorization` is a frozen+slots dataclass.
- `authorization_id` format: `"auth-" + sha256[:16]`, deterministic, no UUID/wall-clock dependency.
- Identity payload includes `intent_id`, `plan_id`, `content_fingerprint`, `status`, `authorized_at`, `valid_from`, `expires_at`, `issuer`, `authorization_method`, `scope`, `policy_reference`, `safety_check_summary`, `label`, `metadata`.
- `status` is included in canonical identity payload (affects authorization_id).
- Timestamps are caller-supplied; model NEVER calls `datetime.now()`/`utcnow()`.
- `__post_init__` validates internal consistency: required fields, timestamp ordering, status invariants, fail-closed principle.

### Factory
- `create_authorization()` is pure/deterministic.
- Does NOT mutate the intent.
- Does NOT recalculate geometry.
- Does NOT access trade geometry, market data, paper trading, or external system code.
- Validates timestamps: `valid_from >= authorized_at`, `expires_at > valid_from`, `expires_at <= intent.valid_until` (when present).
- `AUTHORIZED` timing validation lives in the factory (not `__post_init__`), since `valid_from >= authorized_at` and `expires_at > valid_from` together imply `expires_at > authorized_at` for AUTHORIZED.
- Fail-closed: malformed/contradictory records cannot become AUTHORIZED.

### Identity
- `authorization_id` is deterministic: `"auth-" + sha256[:16]` of canonical payload.
- No random UUIDs, object memory addresses, unordered dictionary serialization, current wall-clock time, `datetime.now()`, or process state.

### Separation
- Execution Authorization is NOT an execution command, NOT an order, NOT an external request, NOT a simulation record.
- It is the application's authorization decision only.
- No authorization fields on `OperationalTradeIntent`.
- No execution fields on `ExecutionAuthorization`.

## Forbidden Imports
The model must NOT import:
- `paper_trading`
- `market_scanner`
- `trade_planning`
- historical providers
- Yahoo
- Upstox
- FastAPI
- dashboard services
- broker SDKs
- execution code

## Test Results
- `tests/test_execution_authorization.py`: 97 passed
- Frozen regression suites: 602 passed
- Full suite: 5198 passed, 2 pre-existing yfinance failures, 3 skipped
- Pipeline baseline unchanged (signals=4, trades=3)

## Limitations
- No clock abstraction
- No persistence implementation
- No authorization layer integration
- No dashboard integration
- No execution layer

## Next Move
1. Create `docs/checkpoint_15_2_execution_authorization_model_and_identity_implementation.md` — DONE
2. Append Checkpoint 15.2 entry to AGENTS.md — PENDING
