# Checkpoint 17.2 — Broker Adapter Contract Design & Execution Lifecycle Contract

## 1. Checkpoint 17.2 Overview

Checkpoint 17.2 establishes the **broker-neutral Broker Adapter contract** and
the **submission / order lifecycle contract** required to safely place the
future Broker Adapter boundary. It is a CONTRACT DESIGN + minimal production
implementation checkpoint — it does **not** integrate any broker.

The architecture conceptually becomes:

```
Trading Intelligence
        ↓
TradePlan
        ↓
Operational Trade Intent
        ↓
Execution Authorization
        ↓
ExecutionCommand
        ↓
Persistence
        ↓
Submission / Order Lifecycle
        ↓
Broker-Neutral Broker Adapter Contract
        ↓
Future Broker-Specific Adapter
        ↓
External Broker
```

## 2. Scope

Checkpoint 17.2 delivers:

- The broker-neutral adapter contract (Protocol) with `submit` / `reconcile`
  (plus `cancel` / `supports` / `check`).
- Typed broker-neutral results (`AdapterResult` with `BrokerResultStatus`).
- A broker-neutral error taxonomy (`BrokerErrorCode` / `BrokerErrorCategory` /
  `BrokerError`).
- A separate submission / order lifecycle model (`SubmissionLifecycle`) that
  references `command_id` and NEVER mutates `ExecutionCommand`.
- Deterministic client order identity (`derive_client_order_id` /
  `derive_idempotency_key`).
- The reconcile-before-retry protocol (engine-enforced).
- The restart-recovery protocol (engine decision function).
- Paper/live adapter isolation (mode verification + adapter selection).
- The capability boundary (`AdapterCapability` / `AdapterCapabilities`).
- Minimal persistence for the submission lifecycle (`SubmissionLifecycleStore`
  + serialization + typed exceptions).
- A deterministic, network-free fake broker.
- A contract test matrix (72 tests across 3 files).

## 3. Non-Goals

Checkpoint 17.2 does **not**:

- Implement any broker (no Upstox, no Yahoo, no Zerodha, no broker SDK).
- Connect to any real broker.
- Submit real orders.
- Add broker credentials.
- Add broker SDK dependencies.
- Introduce live trading.
- Introduce real broker authentication.
- Bypass authorization.
- Modify trading intelligence / setup analysis / research logic.
- Modify trade planning.
- Modify authorization semantics (Checkpoint 15).
- Reopen frozen Checkpoints 10–16.
- Add broker-specific concepts to core domain models.

## 4. Frozen Architecture Constraints

Checkpoints 10–16 remain frozen. This checkpoint:

- Does **not** refactor them for cleanliness.
- Does **not** redesign existing `ExecutionCommand` semantics.
- Does **not** change deterministic command identity.
- Does **not** weaken existing authorization guarantees.
- Does **not** weaken fail-closed behavior.
- Does **not** modify historical/research/trading-intelligence behavior.

The only modification to a frozen-adjacent file is an **additive** extension of
`src/engine/persistence/exceptions.py` with three new submission-store
exception classes (a new boundary's exceptions, no existing semantics changed).

## 5. Existing 17.1 Findings Being Addressed

| 17.1 finding | Addressed by |
|---|---|
| F08 — adapter must never make authorization decisions | Contract declares zero authorization authority; engine accepts only already-authorized commands |
| F11 — separate submission/execution lifecycle model | `SubmissionLifecycle` model + engine |
| F12 — crash-between-persist-and-submit / lost-response | `SubmissionLifecycleStore` + reconcile-before-retry + restart recovery |
| F15 — submission/fill/cancel/unknown states need separate lifecycle | `SubmissionState` enum + transition table |
| F16 — timeout / crash-after-accept need reconcile-before-retry + durable state | Engine `reconcile_submission` + `restart_recovery` + store |
| F18 — broker-level duplicate-submission protection | `derive_client_order_id` + `command_exists` + engine duplicate guard |
| F19 — timeout / lost-response / restart unhandled | Reconcile-before-retry + restart recovery protocol |
| F21 — mode-specific adapters + boundary mode verification | `validate_adapter_mode` + `select_adapter` + `AdapterCapabilities.execution_mode` |
| F23 — broker-neutral error taxonomy + structured audit detail | `BrokerErrorCode` / `BrokerErrorCategory` / `BrokerError` + event `detail` |
| F25 — adapter/fake-broker/idempotency/recovery tests | Fake broker + 72-test contract matrix |

## 6. Broker Adapter Contract

`src/engine/intelligence/broker_adapter_contract.py` defines:

```python
@runtime_checkable
class BrokerAdapter(Protocol):
    capabilities: AdapterCapabilities
    execution_mode: ExecutionMode

    def submit(self, command: ExecutionCommand) -> AdapterResult: ...
    def reconcile(self, client_order_id: str) -> AdapterResult: ...
    def cancel(self, client_order_id: str) -> AdapterResult: ...
    def supports(self, command: ExecutionCommand) -> bool: ...
    def check(self, command: ExecutionCommand) -> None: ...
```

The contract is a broker-specific translation layer ONLY. It never authorizes,
creates commands, owns planning/portfolio, mutates upstream artifacts, or
silently alters authorized economic meaning.

## 7. Adapter Inputs

- `submit` accepts an ALREADY-AUTHORIZED, IMMUTABLE `ExecutionCommand`.
- `reconcile` accepts the deterministic `client_order_id` string.
- `cancel` accepts the deterministic `client_order_id` string.
- `supports` / `check` accept an `ExecutionCommand` for the capability
  boundary.

## 8. Adapter Results

`AdapterResult` (`src/engine/models/broker_adapter.py`) is the single typed
result envelope:

- `BrokerResultStatus.SUBMITTED` / `ACCEPTED` / `PARTIALLY_FILLED` / `FILLED` /
  `CANCELLED` — submission progressed and the broker confirmed the outcome.
- `BrokerResultStatus.REJECTED` — broker-confirmed rejection.
- `BrokerResultStatus.FAILED` — known deterministic failure.
- `BrokerResultStatus.UNKNOWN` — ambiguous outcome (e.g. timeout); the broker
  may or may not have accepted. Reconciliation is required before any retry.

`AdapterResult.__post_init__` is fail-closed: failure-like statuses MUST carry
a `BrokerError`; non-failure statuses MUST NOT.

## 9. Error Taxonomy

`BrokerErrorCode` (broker-neutral) includes: `VALIDATION_FAILURE`,
`UNSUPPORTED_OPERATION`, `UNSUPPORTED_INSTRUMENT`,
`UNSUPPORTED_ORDER_SEMANTICS`, `AUTHENTICATION_FAILURE`,
`AUTHORIZATION_FAILURE`, `RATE_LIMIT`, `NETWORK_FAILURE`, `TIMEOUT`,
`BROKER_UNAVAILABLE`, `BROKER_REJECTION`, `MALFORMED_RESPONSE`,
`UNKNOWN_OUTCOME`, `INTERNAL_ADAPTER_FAILURE`.

`BrokerErrorCategory` separates concerns:

- `VALIDATION` — deterministic request rejection BEFORE submission.
- `BROKER_REJECTION` — broker-confirmed rejection.
- `TRANSPORT` — transport/network failure.
- `AMBIGUOUS` — timeout / malformed / unknown outcome (reconcile before retry).
- `INTERNAL` — internal adapter failure.

`BrokerError` carries a single code + message + derived category + derived
retryable flag. A timeout is NEVER automatically retryable (fail closed).

## 10. Submission / Order Lifecycle Model

`src/engine/models/submission_lifecycle.py` defines `SubmissionLifecycle` — an
immutable snapshot referencing `command_id` (never embedded in or mutating
`ExecutionCommand`). `SubmissionState`:

- `CREATED`
- `SUBMISSION_REQUESTED`
- `SUBMITTED`
- `ACCEPTED`
- `REJECTED`
- `UNKNOWN`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELLED`
- `FAILED`

Each `SubmissionEvent` records a state transition with a `reason` and optional
structured `detail` (audit provenance).

## 11. State Transition Rules

Enforced by `_allowed_transition` in the engine:

- `CREATED` → SUBMISSION_REQUESTED / FAILED / CANCELLED
- `SUBMISSION_REQUESTED` → SUBMITTED / ACCEPTED / REJECTED / UNKNOWN / FAILED
- `SUBMITTED` → ACCEPTED / REJECTED / UNKNOWN / PARTIALLY_FILLED / CANCELLED / FAILED
- `ACCEPTED` → PARTIALLY_FILLED / FILLED / REJECTED / CANCELLED / FAILED / UNKNOWN
- `PARTIALLY_FILLED` → FILLED / REJECTED / CANCELLED / UNKNOWN / FAILED
- `UNKNOWN` → any known outcome (SUBMITTED / ACCEPTED / REJECTED / PARTIALLY_FILLED / FILLED / CANCELLED / FAILED / UNKNOWN)
- `FILLED` / `CANCELLED` / `REJECTED` / `FAILED` are absorbing (terminal)

Illegal transitions raise `ValueError` (fail closed).

## 12. Command Identity and client_order_id

`derive_client_order_id(command_id, broker_context)` returns
`"co-" + sha256[:16](command_id + broker_context)`. It is:

- Deterministic and stable across process restarts.
- Derived from immutable command identity.
- Independent of random process state and broker response.
- The SAME for every submission of the same command (retries reuse it).
- Different per broker context (live vs paper never share an id).

`derive_idempotency_key` is a separate deterministic key for brokers requiring
a distinct key field.

## 13. Idempotency Contract

- The deterministic `client_order_id` is the application-level idempotency
  identity.
- The engine refuses to re-submit a command that already has a terminal or
  in-flight lifecycle for the same `command_id` (`command_exists` /
  `load_by_command` guard).
- The broker-neutral layer does NOT pretend application-level deterministic ids
  alone guarantee broker-side idempotency — the broker-facing adapter owns the
  actual idempotency mechanism and must use broker-side mechanisms where
  available (documented limitation).

## 14. Reconcile-Before-Retry Protocol

- A `UNKNOWN` submission state means the broker may or may not have accepted.
- `request_submission` on an existing `UNKNOWN` lifecycle raises `ValueError`
  — blind retry is prohibited.
- `reconcile_submission` queries the adapter with the SAME deterministic
  `client_order_id`; it NEVER sends a new order.
- If reconciliation confirms acceptance → `ACCEPTED`; rejection → `REJECTED`;
  still unknown → lifecycle stays `UNKNOWN` and retry remains prohibited.

## 15. Restart Recovery Protocol

`restart_recovery(lifecycle)` returns a deterministic `RecoveryAction`:

- `CREATED` / `SUBMISSION_REQUESTED` (pre_submission=True) → `SAFE_TO_SUBMIT`
  (broker never contacted; fresh submission with the same idempotency identity
  is safe).
- `SUBMISSION_REQUESTED` (pre_submission=False) / `SUBMITTED` / `UNKNOWN` →
  `RECONCILE_REQUIRED` (broker MAY have received the request).
- Terminal states → `NO_ACTION`.

The contract NEVER assumes absence of a local acknowledgement means the broker
did not receive the order.

## 16. Paper/Live Adapter Isolation

- `validate_adapter_mode(adapter_execution_mode, command)` raises `ValueError`
  on any paper/live mismatch — enforced before every submit/reconcile/cancel.
- `select_adapter(adapters, command, preferred)` deterministically selects an
  adapter whose `execution_mode` matches the command; no silent substitution.
- A paper-authorized command can never silently reach a live adapter and vice
  versa.

## 17. Capability Boundary

- `AdapterCapability` = `SUBMIT` / `RECONCILE` / `CANCEL`.
- `AdapterCapabilities` declares the adapter's capabilities + bound mode.
- `supports(command)` returns `False` (never raises) for unsupported
  instrument / order semantics / quantity constraints / execution behavior.
- `check(command)` raises `ValueError`/`TypeError` for a deterministic
  pre-submission rejection.
- No broker-specific capability names are introduced.

## 18. Persistence Requirements

`SubmissionLifecycleStore` (`src/engine/persistence/submission_store.py`):

- Atomic writes (same-dir temp + flush + fsync best-effort + `os.replace`).
- One JSON file per submission id (`<id>.json`).
- Schema-versioned (`SUBMISSION_SCHEMA_VERSION = 1`), validated before
  reconstruction.
- Safe-id regex prevents path traversal.
- `command_id` is the stable link; `load_by_command` / `command_exists`
  provide duplicate detection across restart.
- Ambiguous (`UNKNOWN`) submissions survive restart and are reconciled before
  retry.
- Reconciliation state is auditable via the persisted event history.
- Typed exceptions: `SubmissionStoreError`, `SubmissionNotFoundError`,
  `SubmissionIntegrityError`, `UnsupportedSubmissionSchemaVersionError`.

## 19. Fake Broker Contract

`src/engine/intelligence/fake_broker.py` provides a deterministic, network-free
`FakeBroker` implementing the `BrokerAdapter` protocol with scenarios:
`accepted`, `rejected`, `failed`, `timeout`, `unknown`, `reconcile_accepted`,
`reconcile_rejected`, `reconcile_unknown`, `duplicate`, `restart`. It records
every operation for assertions. It never requires credentials or network access.

## 20. Contract Test Matrix

72 tests across 3 files:

- `tests/test_checkpoint_17_2_contract.py` (25) — A authorization, B command
  immutability, C lifecycle, D deterministic identity, E idempotency, F timeout/
  ambiguity, G reconciliation.
- `tests/test_checkpoint_17_2_store.py` (21) — serialization round-trip,
  schema versioning, atomic writes, safe-id, restart recovery via store.
- `tests/test_checkpoint_17_2_fake_broker.py` (26) — L fake broker scenarios,
  J mode isolation, K broker neutrality, adapter selection.

## 21. Dependency Direction

```
models <- intelligence <- persistence
```

- `engine.models.broker_adapter` and `engine.models.submission_lifecycle`
  depend only on stdlib + `engine.models.execution_command`.
- `engine.intelligence.broker_adapter_contract` depends on models.
- `engine.intelligence.submission_lifecycle` (engine) depends on contract +
  models.
- `engine.persistence.submission_*` depends on models + exceptions.
- No execution artifact imports analysis/scanner/decision/paper-trade in
  reverse.

## 22. Broker-Neutrality Verification

Grep across all new modules for `upstox|yahoo|kiteconnect|yfinance|pyotp|
import requests|import socket|urllib|httpx|urlopen` returns **zero matches**.
No broker SDK imports, no broker-specific exception classes, no broker-specific
response models, no credentials in commands/persistence.

## 23. Future Upstox Adapter Requirements

A future Upstox adapter must:

- Implement the `BrokerAdapter` protocol (submit/reconcile/cancel/supports/check).
- Normalize Upstox-specific exceptions to the broker-neutral error taxonomy.
- Derive broker-side idempotency from the deterministic `client_order_id`.
- Verify mode via `validate_adapter_mode` before any operation.
- Keep symbol/exchange/order-type/product/quantity mappings adapter-owned.
- Never place credentials in commands or persisted lifecycle records.

## 24. Known Limitations

- Application-level deterministic ids do NOT by themselves guarantee
  broker-side idempotency; the broker-facing adapter must use broker-side
  mechanisms where available.
- No real broker is integrated; `reconcile`/`cancel` semantics are validated
  only against the deterministic fake broker.
- The lifecycle model tracks state snapshots; broker fills/prices/positions
  remain downstream and broker-specific.

## 25. Checkpoint 17.3 Recommendation

Checkpoint 17.3 should implement the **first broker-specific adapter** (or a
broker-agnostic reference adapter) against this contract, plus a
submission-state + reconcile integration test harness using the fake broker,
and a broker-neutral submission-state persistence audit. Do NOT connect to any
real broker until a separate explicit authorization.

## 26. Final Verdict

**PASS**

The broker-neutral contract is complete, all required invariants are satisfied,
all 72 new contract tests pass, the frozen Checkpoints 10–16 remain intact, and
the full suite passes (5551 passed; 2 pre-existing yfinance baseline failures).
