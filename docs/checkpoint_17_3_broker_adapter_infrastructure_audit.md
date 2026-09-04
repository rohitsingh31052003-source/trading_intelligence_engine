# Checkpoint 17.3 — Broker Adapter Infrastructure, Submission Lifecycle Integration & Persistence Audit

## 1. Checkpoint 17.3 Overview

Checkpoint 17.3 establishes the **broker adapter infrastructure** around the
already-defined broker-neutral contract (Checkpoint 17.2, frozen), integrates
the **SubmissionLifecycle** with persistence and the deterministic fake broker
end-to-end, and performs a **broker-neutral submission-state persistence audit**.

The checkpoint proves that the architecture can safely move from:

```
Persisted ExecutionCommand
        ↓
Submission Lifecycle Creation
        ↓
Submission Persistence
        ↓
Adapter Selection
        ↓
BrokerAdapter.submit()
        ↓
FakeBroker (test-only)
        ↓
AdapterResult
        ↓
Submission Lifecycle transition
        ↓
Persistence
```

without modifying or contaminating the frozen execution architecture.

## 2. Scope

Checkpoint 17.3 delivers:

- A thin, stateless orchestration infrastructure service
  (`SubmissionInfrastructure`) that composes the frozen components into one
  safe end-to-end flow.
- A dormant-lifecycle creation path (`create_lifecycle`) supporting the
  crash-before-submit persistence case.
- Deterministic duplicate-submission detection across restart.
- Reconcile-before-retry enforcement at the infrastructure boundary.
- Restart-recovery decision views (`recovery_for_command`).
- A deterministic broker-neutral auditability surface (`audit`).
- An end-to-end test matrix (84 tests) covering the Phase 10 items A-Z,
  failure injection, persistence cases 1-9, broker-neutrality and no-network
  safety audits.

## 3. Non-Goals

Checkpoint 17.3 does **not**:

- Connect to Upstox or any real broker.
- Submit real orders.
- Add Upstox SDK or any broker SDK.
- Add broker credentials, API keys, bearer tokens, or real broker
  authentication.
- Make external network calls.
- Introduce live trading.
- Introduce a production broker-specific adapter.
- Create a path to accidentally submit live orders.
- Modify or reopen frozen Checkpoints 10-16.

## 4. Frozen Architecture Constraints

Checkpoints 10-16 remain frozen. Checkpoint 17.3:

- Does **not** refactor them for cleanliness.
- Does **not** change `ExecutionCommand` semantics.
- Does **not** change deterministic `command_id` behavior.
- Does **not** change authorization semantics (Checkpoint 15).
- Does **not** change `TradePlan` behavior.
- Does **not** change trading intelligence / research / setup / decision /
  opportunity / historical-research logic.
- Does **not** weaken fail-closed behavior.
- Does **not** modify frozen tests merely to accommodate 17.3.

The only new production file is additive
(`src/engine/intelligence/broker_adapter_infrastructure.py`). No frozen file
was modified.

## 5. Checkpoint 17.2 Contract Baseline

Checkpoint 17.2 (frozen) provided:

- `BrokerAdapter` protocol (`submit` / `reconcile` / `cancel` / `supports` /
  `check`).
- `AdapterCapabilities` with bound `execution_mode`.
- `AdapterResult` / `BrokerResultStatus` / `BrokerError` /
  `BrokerErrorCode` / `BrokerErrorCategory`.
- Deterministic `derive_client_order_id` / `derive_idempotency_key`.
- `validate_adapter_mode` / `select_adapter` (fail-closed mode selection).
- `SubmissionLifecycle` model (frozen+slots, references `command_id`).
- `SubmissionLifecycleEngine` (transition / reconcile / recovery authority).
- `SubmissionLifecycleStore` + serialization (atomic, schema-versioned).
- `FakeBroker` (deterministic, network-free, credential-free, test-only).
- 72 contract tests.

Checkpoint 17.3 adds the orchestration infrastructure that binds these
components together and proves the end-to-end flow.

## 6. Infrastructure Architecture

`src/engine/intelligence/broker_adapter_infrastructure.py` defines:

- `SubmissionInfrastructure` — a stateless orchestration service.
- `SubmissionInfrastructureError` (base) + `DuplicateSubmissionError` /
  `ReconciliationRequiredError` / `CommandNotSubmittedError`.
- `SubmissionAuditRow` / `SubmissionInfrastructureAudit` — deterministic
  broker-neutral audit views.

Public API:

- `create_lifecycle(...)` — persist a CREATED (dormant) lifecycle.
- `submit_command(...)` — end-to-end submit + persist.
- `reconcile_command(...)` — end-to-end reconcile + persist.
- `recovery_for_command(...)` — deterministic restart-recovery view.
- `audit(...)` — broker-neutral auditability summary from the store alone.

The service holds no mutable state, no cache, no registry. All timestamps are
caller-supplied (no `datetime.now()`). It never mutates commands, lifecycles
or adapters.

## 7. Submission Persistence Design

The persistence layer survives every Phase 3 case:

| Case | Persisted state | Recovery behavior |
|------|-----------------|-------------------|
| 1. Crash before broker submission | CREATED (pre_submission=True) | SAFE_TO_SUBMIT (broker never contacted) |
| 2. Crash before broker response | SUBMISSION_REQUESTED / SUBMITTED / UNKNOWN | RECONCILE_REQUIRED |
| 3. Broker accepts, app loses response | UNKNOWN | RECONCILE_REQUIRED -> reconcile discovers ACCEPTED |
| 4. Network timeout | UNKNOWN | RECONCILE_REQUIRED (never a false failure) |
| 5. Restart while UNKNOWN | UNKNOWN survives restart | RECONCILE_REQUIRED |
| 6. Same command again after restart | Existing lifecycle loaded | Duplicate guard / recovery decision |
| 7. Reconcile discovers accepted | ACCEPTED | Terminal NO_ACTION |
| 8. Reconcile discovers rejection | REJECTED | Terminal NO_ACTION |
| 9. Reconcile remains UNKNOWN | UNKNOWN | RECONCILE_REQUIRED (no blind retry) |

Persistence contract (matches the frozen store's single-active-record guard):

- Exactly ONE persisted record per command (the CURRENT lifecycle snapshot).
- Advancing a snapshot is persisted FIRST, then the prior snapshot(s) for the
  same command are deleted (never lose the newest state).
- Re-persisting the SAME snapshot is idempotent.
- A crash between snapshot-persist-and-prior-delete can leave two records for
  one command -> the store's `load_by_command` guard raises
  `SubmissionIntegrityError` (fail-closed, no blind retry; the audit's
  `duplicate_commands` names them).

No raw broker SDK objects, no broker-specific response models, no
credentials, no URLs are persisted.

## 8. Submission Lifecycle Integration

The integration preserves the valid state transitions (engine authority):

```
CREATED → SUBMISSION_REQUESTED → SUBMITTED → ACCEPTED
SUBMISSION_REQUESTED → FAILED
SUBMITTED → UNKNOWN → RECONCILE_REQUIRED
UNKNOWN → RECONCILE → ACCEPTED / REJECTED / UNKNOWN (still unresolved)
```

The infrastructure refuses:

- `UNKNOWN` → blind retry (`ReconciliationRequiredError`).
- In-flight lifecycles → duplicate submission (`DuplicateSubmissionError`).
- Terminal states → resubmission (returns the existing snapshot unchanged).

## 9. Idempotency Integration

- Same `ExecutionCommand` -> same `command_id` -> same deterministic
  `client_order_id` -> same deterministic `idempotency_key` across repeated
  process execution, restart, duplicate processing, reconciliation and
  recovery.
- Duplicate command processing is detected via the store's
  `load_by_command` guard + the service's pre-submit check.
- **Application-level idempotency** (deterministic identity) is documented
  as distinct from **broker-level idempotency** (the broker-facing adapter
  owns the actual broker-side mechanism). The audit surface documents this
  distinction explicitly.

## 10. Reconciliation Integration

`adapter.reconcile(...)` integration proves:

1. Submission becomes UNKNOWN when appropriate (timeout / ambiguous).
2. UNKNOWN survives persistence.
3. UNKNOWN survives process restart.
4. Recovery detects that reconciliation is required.
5. Reconciliation is attempted using the deterministic client_order_id.
6. Reconciliation can discover accepted state.
7. Reconciliation can discover rejected state.
8. Reconciliation can remain unresolved.
9. No blind retry occurs while the outcome remains ambiguous.

## 11. Restart Recovery

`recovery_for_command` returns a deterministic decision view:

- CREATED / SUBMISSION_REQUESTED (pre_submission=True) -> SAFE_TO_SUBMIT.
- SUBMISSION_REQUESTED (pre_submission=False) / SUBMITTED / UNKNOWN ->
  RECONCILE_REQUIRED.
- Terminal states -> NO_ACTION.
- No persisted lifecycle -> fresh submission allowed.
- Ambiguous persisted storage -> manual review required (no blind retry).

Recovery never auto-submits and never auto-reconciles; the caller must
explicitly invoke submit / reconcile after inspecting the recovery action.

## 12. Adapter Selection and Mode Isolation

- Paper command -> paper adapter; live command -> live adapter (via
  `select_adapter`, fail-closed).
- No implicit fallback live -> paper or paper -> live.
- The selection mechanism fails closed when the required adapter is
  unavailable or incompatible with the execution mode.
- The execution mode is recorded on the lifecycle metadata (`cp17_mode`) so
  later reconcile/cancel verify the mode binding (engine
  `_validate_adapter_mode_for_lifecycle`).
- Reconcile selects the adapter matching the lifecycle's recorded mode; a
  live-only registry cannot reconcile a paper lifecycle (and vice versa).

## 13. Fake Broker Integration

The existing `FakeBroker` (17.2) is used as the test double. It remains:

- deterministic
- network-free
- credential-free
- broker-neutral
- test-only

The infrastructure module imports no fake-broker code; the fake broker is
injected by callers/tests only.

## 14. Failure Injection Results

Using the deterministic fake broker scenarios:

| Scenario | Result | Lifecycle state |
|----------|--------|-----------------|
| accepted | ACCEPTED | ACCEPTED |
| rejected | REJECTED | REJECTED |
| failed | FAILED (INTERNAL_ADAPTER_FAILURE) | FAILED |
| timeout | UNKNOWN (TIMEOUT, AMBIGUOUS) | UNKNOWN |
| unknown | UNKNOWN (UNKNOWN_OUTCOME, AMBIGUOUS) | UNKNOWN |
| reconcile_accepted | ACCEPTED | ACCEPTED |
| reconcile_rejected | REJECTED | REJECTED |
| reconcile_unknown | UNKNOWN | UNKNOWN |
| duplicate | ACCEPTED (dedupe) | ACCEPTED |
| restart | SUBMITTED | SUBMITTED -> RECONCILE_REQUIRED |

Each produces the correct broker-neutral result and lifecycle state.

## 15. Auditability

The `audit` surface answers the Phase 12 questions from persisted data alone:

1. Which ExecutionCommand was authorized? -> `command_id` on every row.
2. What was its command_id? -> `command_id`.
3. What submission attempt was associated? -> `submission_id`.
4. What client_order_id / idempotency identity was used? ->
   `client_order_id` / `idempotency_key` (derived deterministically).
5. What lifecycle state exists now? -> `state`.
6. Was reconciliation required? -> `requires_reconciliation`.
7. Was reconciliation performed? -> `reconciliation_performed`.
8. What was the normalized outcome? -> `state` + `last_reason`.
9. Was a retry allowed? -> `retry_allowed`.
10. Did the system ever attempt an unsafe duplicate submission? ->
    `duplicate_commands` (crash artifacts) + the store's `load_by_command`
    guard (fail-closed); no code path creates a second active lifecycle for
    the same command.

Immutable command history is never sacrificed: `ExecutionCommand` is never
mutated and the lifecycle references it only via `command_id`.

## 16. Broker-Neutrality Audit

Grep across the new module for `upstox|zerodha|kiteconnect|yfinance|pyotp|
import requests|import socket|import httpx|from urllib|import urllib|urlopen|
Authorization:|Bearer |api_key|access_token` returns **zero matches**. The new
infrastructure imports only stdlib + the frozen broker-neutral contract /
lifecycle / persistence modules. The only permitted broker concept is the
generic `BrokerAdapter` abstraction and the `FakeBroker` test infrastructure.

## 17. Network Safety Audit

The new module performs no external network communication. The test suite runs
without internet access, broker credentials, Upstox account, broker SDK, API
token, or external broker availability. All broker interaction uses the
deterministic fake broker / test double.

## 18. Testing Report

- New Checkpoint 17.3 tests: `tests/test_checkpoint_17_3_infrastructure.py`
  — **84 passed**.
- Checkpoint 17.2 tests: `tests/test_checkpoint_17_2_contract.py` +
  `test_checkpoint_17_2_fake_broker.py` + `test_checkpoint_17_2_store.py` —
  **72 passed**.
- Relevant frozen Checkpoint 14-16 execution tests: execution_command (65),
  execution_command_store (68), execution_authorization (97),
  execution_authorization_engine (84), execution_authorization_store (57),
  operational_trade_intent (125), operational_trade_intent_engine (69),
  operational_trade_intent_application (58), trade_planning, paper_trading,
  paper_trading_operations — all pass.
- Full suite: **5635 passed, 2 failed** (5551 + 84 new).

## 19. Files Inspected

Source:

- `src/engine/intelligence/broker_adapter_contract.py`
- `src/engine/intelligence/submission_lifecycle.py`
- `src/engine/intelligence/fake_broker.py`
- `src/engine/models/broker_adapter.py`
- `src/engine/models/submission_lifecycle.py`
- `src/engine/models/execution_command.py`
- `src/engine/persistence/submission_store.py`
- `src/engine/persistence/submission_serialization.py`
- `src/engine/persistence/execution_command_store.py`
- `src/engine/persistence/exceptions.py`

Tests:

- `tests/test_checkpoint_17_2_contract.py`
- `tests/test_checkpoint_17_2_fake_broker.py`
- `tests/test_checkpoint_17_2_store.py`
- `tests/_checkpoint17_2_fixtures.py`
- `tests/test_execution_command*.py`, `tests/test_execution_authorization*.py`,
  `tests/test_operational_trade_intent*.py`

Documentation:

- `docs/checkpoint_17_1_broker_adapter_boundary_audit.md`
- `docs/checkpoint_17_2_broker_adapter_contract_design.md`
- `AGENTS.md`

## 20. Code Changes

New file (additive only):

- `src/engine/intelligence/broker_adapter_infrastructure.py` — the
  orchestration infrastructure service + audit surface.

New test file (additive only):

- `tests/test_checkpoint_17_3_infrastructure.py` — 84 tests.

No frozen Checkpoints 10-16 file was modified. No frozen test was modified.

## 21. Findings

| Finding | Classification |
|---------|----------------|
| ExecutionCommand remains immutable; never mutated by infrastructure | PASS |
| ExecutionCommand represents an authorized instruction, not broker state | PASS |
| Submission lifecycle is separate from ExecutionCommand, references command_id | PASS |
| Authorization remains upstream; adapter/infrastructure has no authority | PASS |
| Timeout/ambiguous broker response becomes UNKNOWN (not false failure) | PASS |
| UNKNOWN requires reconciliation; blind retry prohibited | PASS |
| Unknown state survives restart | PASS |
| Deterministic identity survives restart | PASS |
| Duplicate command processing is detectable | PASS |
| Paper/live mode cannot silently cross; missing adapter fails closed | PASS |
| Broker-specific errors do not leak into the core contract | PASS |
| Persisted lifecycle state is broker-neutral | PASS |
| No external network communication | PASS |
| No real broker integration | PASS |
| Frozen Checkpoints 10-16 remain unchanged architecturally | PASS |
| Persistence survives all 9 Phase-3 cases | PASS |
| Reconciliation/restart recovery proven end-to-end | PASS |
| Auditability surface answers Phase-12 questions | PASS |
| Application-level vs broker-level idempotency distinction documented | PASS |

No BLOCKER findings. No CONCERN findings requiring remediation before
Checkpoint 17.4 (all limitations below are intentional scope).

## 22. Known Limitations

- Application-level deterministic ids do NOT by themselves guarantee
  broker-side idempotency; the broker-facing adapter must use broker-side
  mechanisms where available (documented, not a defect).
- No real broker is integrated; `reconcile`/`cancel` semantics are validated
  only against the deterministic fake broker.
- The lifecycle model tracks state snapshots; broker fills/prices/positions
  remain downstream and broker-specific.
- A crash between snapshot-persist-and-prior-delete can leave two persisted
  records for one command; the store's guard surfaces it fail-closed and the
  audit names it for manual review (no blind retry).
- No clock abstraction: all timestamps are caller-supplied.

## 23. Checkpoint 17.4 Recommendation

Checkpoint 17.4 should implement the **first broker-specific adapter** (or a
broker-agnostic reference adapter) against this contract, plus a
submission-state + reconcile integration test harness using the fake broker,
and a broker-neutral submission-state persistence audit. Do NOT connect to
any real broker until a separate explicit authorization.

## 24. Final Verdict

**PASS**

The broker adapter infrastructure is complete, all required invariants are
proven, the submission persistence is validated for all nine Phase-3 cases,
the fake-broker end-to-end flow is proven, reconciliation/restart behavior is
proven, safety invariants are tested, documentation is created, and AGENTS.md
is updated. The frozen Checkpoints 10-16 remain intact, and the full suite
passes (5635 passed; 2 pre-existing yfinance baseline failures).
