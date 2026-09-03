# Checkpoint 15.3 — Execution Authorization Engine & Workflow Implementation

## Objective
Implement the Execution Authorization Engine and explicit authorization workflow on top of the existing `ExecutionAuthorization` model from Checkpoint 15.2. The engine evaluates eligibility and records explicit authorization decisions for `OperationalTradeIntent`, with clean separation from execution semantics.

## Scope
- `src/engine/intelligence/execution_authorization.py` — engine implementation
- `tests/test_execution_authorization_engine.py` — 84 tests

## Design Decisions

### Engine
- `ExecutionAuthorizationEngine` is stateless (no mutable state, no caching, no registry).
- Pure delegation to the existing `create_authorization()` factory for authorization record construction. The factory is the single source of truth for identity computation, timestamp validation, and immutable artifact construction.
- The engine is responsible for eligibility evaluation, policy enforcement, and the explicit authorization workflow.
- Eligibility and authorization are separate concepts. `EligibilityResult` answers "does this intent satisfy policy conditions?" and `AuthorizationDecision` answers "has an explicit authorization been recorded for this eligible intent?".
- The engine validates the intent; it does NOT become a second planning engine. It never recalculates entry, stop, target, quantity, planned risk, maximum risk, or risk/reward.
- No `datetime.now()` / `datetime.utcnow()`. The caller supplies the evaluation timestamp.
- Fail-closed: any unknown, missing, or contradictory condition must NOT produce `AUTHORIZED`.

### Eligibility Evaluation
- `evaluate_eligibility(intent, evaluation_timestamp)` returns `EligibilityResult`.
- Checks: intent existence, type, intent_id format, content_fingerprint format, instrument non-empty, direction LONG/SHORT, timeframe non-empty, positive geometry values, non-negative planned risk, positive maximum risk, planned_risk <= maximum_risk, valid risk plan status, not expired, timezone-aware evaluation timestamp.
- `valid_until >= evaluation_timestamp` means the intent is NOT expired (inclusive boundary: at exactly `valid_until` the intent is considered expired).
- Naive/aware datetime comparison raises `TypeError`; the engine catches this and treats the intent as ineligible.
- The engine catches `ValueError`/`TypeError` from intent validation gracefully, returning `EligibilityResult(eligible=False, reasons=...)`.

### Authorization Workflow
- `authorize(intent, evaluation_timestamp, *, authorized_at, valid_from, expires_at, issuer, ...)` returns `AuthorizationDecision`.
- Step 1: Evaluate eligibility. If not eligible, return `NOT_AUTHORIZED` with eligibility reasons.
- Step 2: Validate explicit authorization inputs (issuer, authorization_method, scope, policy_reference, safety_check_summary must all be non-empty).
- Step 3: Delegate to `create_authorization()` factory. Catch `TypeError`/`ValueError` and return `NOT_AUTHORIZED`.
- Eligibility alone does NOT create an `AUTHORIZED` record. The caller must deliberately request authorization.

### Result Types
- `EligibilityResult`: `eligible: bool`, `reasons: tuple[str, ...]`
- `AuthorizationDecision`: `authorized: bool`, `authorization: ExecutionAuthorization | None`, `reasons: tuple[str, ...]`

### Identity
- `authorization_id` is deterministic: `"auth-" + sha256[:16]` of canonical payload (timestamps excluded).
- Same inputs → same authorization_id. Changed label/issuer → different authorization_id.
- No random UUIDs, no wall-clock dependency in identity.

### Immutability
- Intent is never mutated by the engine.
- `ExecutionAuthorization` is frozen; mutation raises `FrozenInstanceError`.
- `TradePlan` is never mutated.

### Isolation
- No `PaperTradingEngine` import.
- No `MarketScanner` import.
- No `TradePlanningEngine` import.
- No historical provider access.
- No broker code (`upstox`, `yahoo`, `broker_adapter`).
- No dashboard code (`fastapi`).
- No `datetime.now()`/`datetime.utcnow()` calls.

### No Execution Semantics
- Engine has no `execute`, `submit`, `send_order`, `place_order`, `broker`, `order`, `position`, `fill`, `execution_result`, `kill_switch`, or `emergency_stop` methods.

### Determinism
- Repeated calls with the same inputs produce equivalent results.
- `evaluate_eligibility` is deterministic.
- `authorize` is deterministic (same inputs → same authorization_id).

### Separation of Concerns
- `EligibilityResult` carries NO `authorization` attribute.
- `AuthorizationDecision` carries the `ExecutionAuthorization` record (or None).
- Eligibility is a SYSTEM determination; authorization is an EXPLICIT operation.

## Test Strategy
- 84 tests covering: construction, statelessness, determinism, eligibility (valid/invalid/missing/expired/naive-timestamp/risk-plan/geometry/fingerprint), authorization (eligible→authorized, ineligible→not, explicit required, identity, fail-closed, immutability, isolation, no execution semantics, determinism, separation, lifecycle, fingerprint integrity).

## Test Results
- `tests/test_execution_authorization_engine.py`: 84 passed
- `tests/test_execution_authorization.py`: 97 passed
- `tests/test_operational_trade_intent.py` + engine + application: 252 passed
- `tests/test_trade_planning.py` + `tests/test_paper_trading.py` + `tests/test_paper_trading_operations.py`: 350 passed
- Full suite: 5282 passed, 2 pre-existing yfinance failures, 3 skipped
- Pipeline baseline unchanged (signals=4, trades=3)

## Limitations
- No persistence implementation
- No dashboard integration
- No execution path
- No broker integration
- No clock abstraction
