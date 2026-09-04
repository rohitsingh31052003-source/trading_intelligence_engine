# Checkpoint 17.4 — First Concrete Broker Adapter / Reference Adapter & Contract Conformance Audit

## 1. Checkpoint 17.4 Overview

Checkpoint 17.4 implements the **FIRST CONCRETE `BrokerAdapter` implementation**
against the frozen broker-neutral contract (Checkpoint 17.2). The adapter is a
**REFERENCE / SIMULATED / TEST-SAFE adapter**:

* It NEVER connects to any real broker.
* It NEVER makes network requests (no HTTP, no sockets, no WebSockets).
* It NEVER requires credentials / API keys / bearer tokens.
* It NEVER submits real orders.
* It is safe to execute in an offline CI environment.

Its purpose is to **prove** that a concrete adapter can implement the
broker-neutral contract while keeping all broker-specific translation and
behavior isolated behind the adapter boundary.

## 2. Scope

* Implement `ReferenceBrokerAdapter` (concrete, simulated, offline).
* Implement adapter-owned translation boundaries (symbol / exchange / order
  type / product / quantity / price / client_order_id / idempotency key).
* Implement the adapter-owned `ReferenceBrokerRequest` / `ReferenceBrokerResponse`
  representation.
* Normalize every internal outcome into the existing broker-neutral
  `AdapterResult` / `BrokerError` taxonomy.
* Bind the adapter explicitly to one `ExecutionMode` (PAPER or LIVE) using the
  existing `validate_adapter_mode` / `select_adapter` infrastructure.
* Exercise the existing reconcile contract (`UNKNOWN` -> `reconcile()` ->
  confirmed result).
* Provide deterministic scenario injection covering the generic contract.
* Integrate the concrete adapter with the existing `SubmissionInfrastructure`.
* Add a REUSABLE generic contract conformance suite that a future Upstox (or
  other broker) adapter can run unchanged.
* Add reference-adapter-specific tests and end-to-end infrastructure tests.
* Run the broker-neutrality, dependency-direction and no-network audits.

## 3. Non-Goals

Checkpoint 17.4 does NOT:

* connect to Upstox / Zerodha / any real broker,
* add a real broker SDK,
* add API credentials / keys / bearer tokens,
* add network clients / HTTP calls / WebSocket calls,
* submit or cancel live orders,
* perform real broker reconciliation,
* implement production broker authentication,
* introduce external network dependencies,
* modify frozen trading intelligence / authorization / ExecutionCommand,
* redesign the generic BrokerAdapter contract (no concrete contradiction was
  discovered).

## 4. Frozen Architecture Constraints

Checkpoints 10–16 remain architecturally frozen. The reference adapter is
purely additive:

* `ExecutionCommand` is unchanged (immutable, broker-neutral, deterministic
  `command_id`).
* `ExecutionAuthorization` is unchanged (upstream authority).
* `OperationalTradeIntent` is unchanged.
* The generic `BrokerAdapter` contract (`broker_adapter_contract.py`) is
  unchanged.
* `SubmissionLifecycle` / `SubmissionLifecycleEngine` / `SubmissionInfrastructure`
  are unchanged.
* `FakeBroker` is unchanged (still passes its 17.2/17.3 tests).

No frozen file was modified. The only new production module is
`src/engine/intelligence/reference_broker_adapter.py`.

## 5. 17.2 Contract Baseline

The frozen contract (Checkpoint 17.2) defines:

* `BrokerAdapter` protocol: `submit` / `reconcile` / `cancel` / `supports` /
  `check` + `capabilities` + `execution_mode`.
* `AdapterResult` — the single typed broker-neutral result envelope
  (`SUBMITTED` / `ACCEPTED` / `PARTIALLY_FILLED` / `FILLED` / `CANCELLED` /
  `REJECTED` / `FAILED` / `UNKNOWN`).
* `BrokerErrorCode` / `BrokerErrorCategory` / `BrokerError` — the broker-neutral
  error taxonomy.
* `AdapterCapabilities` — declared capabilities (SUBMIT + RECONCILE required).
* `derive_client_order_id` / `derive_idempotency_key` — deterministic identity.
* `validate_adapter_mode` / `select_adapter` — mode isolation + selection.
* `SubmissionLifecycle` / `SubmissionLifecycleEngine` — lifecycle authority,
  reconcile-before-retry, restart recovery.
* `FakeBroker` — deterministic fake broker.

## 6. 17.3 Infrastructure Baseline

The frozen infrastructure (Checkpoint 17.3) defines:

* `SubmissionInfrastructure` — stateless orchestration composing the engine,
  store and adapter.
* `SubmissionLifecycleStore` — atomic, schema-versioned lifecycle persistence.
* Duplicate detection, restart recovery, reconciliation integration, audit
  surface, broker-neutral persisted state.

Checkpoint 17.4 wires the concrete reference adapter into this unchanged
infrastructure.

## 7. Reference Adapter Architecture

```
ExecutionCommand
        |   (adapter-owned translation)
        v
ReferenceBrokerAdapter
        |   (adapter-owned request)
        v
ReferenceBrokerRequest
        |   (adapter-owned deterministic simulation)
        v
ReferenceBrokerResponse
        |   (adapter-owned normalization)
        v
AdapterResult   (broker-neutral)
```

The core architecture remains:

```
Trading Intelligence -> TradePlan -> Operational Trade Intent ->
Execution Authorization -> ExecutionCommand -> Persistence ->
SubmissionLifecycle -> SubmissionInfrastructure -> BrokerAdapter ->
Concrete Adapter (ReferenceBrokerAdapter)
```

The concrete adapter does NOT become a dependency of Trading Intelligence,
Research, Setup Analysis, Trade Planning, Decision Analysis, Opportunity
Filtering, Authorization, or ExecutionCommand creation.

## 8. Adapter-Owned Translation

The reference adapter owns a deliberately simple deterministic representation
inside the adapter boundary:

| Concept | Adapter-owned representation |
|---------|------------------------------|
| instrument/symbol | `REF:<INSTRUMENT>` (e.g. `REF:NIFTY`) |
| exchange | `REF` (`REFERENCE_EXCHANGE`) |
| order type | LONG -> `BUY` / SHORT -> `SELL` |
| product/variety | `REF-CASH` (`REFERENCE_PRODUCT`) |
| quantity | verbatim `Decimal` (never increased) |
| price | verbatim `Decimal` (never altered) |
| stop/target | verbatim `Decimal` (never altered) |
| client_order_id | deterministic 17.2 `derive_client_order_id` |
| idempotency key | deterministic 17.2 `derive_idempotency_key` |

The generic execution system never needs to understand the adapter's internal
representation. Translation lives INSIDE the concrete adapter
(`_translate_command`), never in the generic contract.

## 9. Adapter Request Model

`ReferenceBrokerRequest` (frozen+slots) is the adapter-owned request
representation. It is isolated from core domain models, does NOT leak into
`ExecutionCommand`, Trading Intelligence, or Authorization, and is NOT part of
the generic `BrokerAdapter` contract. It contains only the information the
simulated adapter needs (symbol, exchange, order type, product, quantity,
price, stop/target, client_order_id, idempotency_key, execution_mode,
created_at). `ReferenceBrokerResponse` is the corresponding adapter-owned
response representation.

## 10. Adapter Result Normalization

`_normalize_response` translates every internal `ReferenceBrokerResponse` into
the existing broker-neutral `AdapterResult`:

| Internal status | Broker-neutral result |
|-----------------|------------------------|
| accepted | `ACCEPTED` |
| submitted | `SUBMITTED` |
| cancelled | `CANCELLED` |
| filled | `FILLED` |
| partially_filled | `PARTIALLY_FILLED` |
| duplicate | `ACCEPTED` (broker_status="duplicate") |
| rejected | `REJECTED` (BROKER_REJECTION) |
| failed | `FAILED` (mapped error code) |
| timeout | `UNKNOWN` (TIMEOUT, AMBIGUOUS) |
| unknown | `UNKNOWN` (UNKNOWN_OUTCOME, AMBIGUOUS) |

The core system NEVER receives adapter-specific exceptions or raw reference
response objects.

## 11. Error Normalization

Internal error kinds are mapped to the broker-neutral taxonomy:

| Internal error kind | BrokerErrorCode | Category |
|---------------------|-----------------|----------|
| validation | `VALIDATION_FAILURE` | VALIDATION |
| unsupported_operation | `UNSUPPORTED_OPERATION` | VALIDATION |
| unsupported_instrument | `UNSUPPORTED_INSTRUMENT` | VALIDATION |
| unsupported_order_type | `UNSUPPORTED_ORDER_SEMANTICS` | VALIDATION |
| broker_rejection | `BROKER_REJECTION` | BROKER_REJECTION |
| timeout | `TIMEOUT` | AMBIGUOUS |
| unknown_outcome | `UNKNOWN_OUTCOME` | AMBIGUOUS |
| malformed_response | `MALFORMED_RESPONSE` | AMBIGUOUS |
| internal | `INTERNAL_ADAPTER_FAILURE` | INTERNAL |

A timeout / malformed / unknown outcome is NEVER automatically retryable
(reconciliation required first).

## 12. Capability Contract

`ReferenceBrokerAdapter` declares `AdapterCapabilities` with SUBMIT + RECONCILE
(required) + CANCEL (supported by default). The capability boundary:

* `supports(command)` returns `False` (never raises) for a non-command, an
  unsupported instrument, or an execution-mode mismatch.
* `check(command)` raises `ValueError` for an unsupported instrument / mode
  mismatch (deterministic pre-submission rejection).
* `cancel()` raises `ValueError` when CANCEL is not advertised.
* Unsupported operations are normalized to `FAILED(UNSUPPORTED_OPERATION)`.

No fake capabilities are added; unsupported operations return the existing
broker-neutral unsupported-operation result/error.

## 13. Execution Mode Binding

The adapter is explicitly bound to one `ExecutionMode` (PAPER or LIVE) via the
factories `paper_reference_adapter()` / `live_reference_adapter()`. It uses the
existing `validate_adapter_mode` (called before every submit/supports/check)
and `select_adapter` (used by the infrastructure) — no parallel mode-selection
system. A paper command can never silently reach a live adapter and vice versa;
an incompatible mode fails closed.

## 14. Idempotency

* Same `ExecutionCommand` -> same `command_id` -> same `client_order_id` ->
  same `idempotency_key` (17.2 deterministic identity).
* The adapter NEVER generates random client identities and NEVER generates a
  new client identity per `submit()` call.
* The adapter additionally demonstrates ADAPTER / REFERENCE-BROKER duplicate
  detection: submitting the same `client_order_id` twice (through a direct
  adapter call) reports `broker_status="duplicate"`.
* APPLICATION-LEVEL idempotency (deterministic identity) is distinguished from
  ADAPTER/REFERENCE-BROKER duplicate detection; the reference adapter does NOT
  claim a real broker's idempotency guarantees.

## 15. Reconciliation

The reference adapter exercises the existing reconcile contract:

```
submit() -> ambiguous result -> UNKNOWN -> reconcile() -> confirmed result
```

Tested: `UNKNOWN -> reconciliation -> accepted`, `UNKNOWN -> reconciliation ->
rejected`, `UNKNOWN -> reconciliation -> still unknown`. The adapter never
converts an unresolved outcome into a false failure, and blind retry through
reconciliation is prohibited by the frozen contract.

## 16. Failure Scenarios

The reference adapter supports deterministic scenario injection covering the
generic contract (18 scenarios):

`accepted`, `rejected`, `failed`, `timeout`, `unknown`, `reconcile_accepted`,
`reconcile_rejected`, `reconcile_unknown`, `duplicate`, `restart`, `cancelled`,
`filled`, `partially_filled`, `unsupported_operation`, `unsupported_instrument`,
`unsupported_order_type`, `validation_failure`, `malformed_internal`.

Scenario behavior is fully deterministic (no randomness).

## 17. SubmissionInfrastructure Integration

The reference adapter is wired into the unchanged `SubmissionInfrastructure`:

```
Authorized ExecutionCommand
    -> SubmissionInfrastructure
    -> SubmissionLifecycle
    -> ReferenceBrokerAdapter
    -> ReferenceBrokerRequest
    -> AdapterResult
    -> SubmissionLifecycle update
    -> Persistence
```

The infrastructure still enforces: authorization, command immutability,
lifecycle rules, idempotency, reconciliation, recovery, execution mode, and
fail-closed behavior with the concrete adapter wired in.

## 18. Contract Conformance Tests

`tests/test_checkpoint_17_4_contract_conformance.py` provides a REUSABLE
generic contract conformance suite (`BrokerAdapterContractConformanceBase`).
Any concrete adapter (a future Upstox adapter, a future broker-specific
adapter) can run the same generic tests by supplying an `ADAPTER_FACTORY`.

The generic suite verifies the broker-NEUTRAL behavioral contract: accepted /
rejected / failed / unknown behavior, reconciliation, idempotency expectations,
mode binding, error normalization, capability behavior, lifecycle
compatibility, and authorization separation.

The suite currently runs against TWO concrete adapters:
1. `ReferenceBrokerAdapter` (Checkpoint 17.4).
2. `FakeBroker` (Checkpoint 17.2, unchanged) — proving the suite is generic and
   not coupled to the reference adapter.

## 19. Integration Tests

`tests/test_checkpoint_17_4_integration.py` proves the full lifecycle matrix
(Phase 13 items 1–26) end-to-end with the concrete reference adapter through
`SubmissionInfrastructure` + `SubmissionLifecycleStore`:

1. authorized command reaches adapter
2. unauthorized command never reaches adapter
3. command remains immutable
4. command_id remains unchanged
5. submission lifecycle references command_id
6. accepted submission persists
7. rejected submission persists
8. failed submission persists
9. timeout becomes UNKNOWN
10. UNKNOWN persists
11. UNKNOWN survives restart
12. restart requires reconciliation
13. reconciliation discovers accepted result
14. reconciliation discovers rejection
15. reconciliation remains unknown
16. no blind retry occurs
17. duplicate submission is blocked/detected
18. deterministic client_order_id remains stable
19. deterministic idempotency_key remains stable
20. paper mode is correctly enforced
21. incompatible mode fails closed
22. unsupported capability is normalized
23. adapter error is normalized
24. raw adapter result does not leak into core
25. adapter-specific request does not leak into core
26. no network access occurs

## 20. Broker-Neutrality Audit

Source audit of the new module (`reference_broker_adapter.py`):

* No Upstox / Zerodha / kiteconnect / yfinance / pyotp / place_order references.
* No broker SDK imports.
* No broker API URLs.
* No API credentials / bearer tokens / api keys / access tokens.
* No broker-specific domain / error / response types leak into the core.

The reference adapter remains a simulated / offline implementation.

## 21. Dependency Direction Audit

Verified programmatically (AST + source scan):

* `broker_adapter_contract.py` does NOT import `reference_broker_adapter`.
* `broker_adapter.py` (models) does NOT import `reference_broker_adapter`.
* `execution_command.py` does NOT import `reference_broker_adapter`.
* `submission_lifecycle.py` (models + engine) does NOT import
  `reference_broker_adapter`.
* `broker_adapter_infrastructure.py` does NOT import `reference_broker_adapter`.
* `reference_broker_adapter.py` imports ONLY the generic contract + broker-neutral
  models + `ExecutionCommand` — no reverse / analysis / paper-trading dependency.

Direction: `Core -> Broker-neutral contract -> Concrete adapter`. NOT the
reverse.

## 22. Network / Live Execution Safety Audit

Verified programmatically:

* No `socket` / `requests` / `httpx` / `urllib` / `http` / `urlopen` /
  `websocket` / `aiohttp` imports in the new module.
* No `Authorization:` / `Bearer ` / `api_key` / `access_token` literals.
* The adapter exposes no credential attributes.
* No real broker URL exists.
* No real order can be submitted.

The adapter is safe to execute in an offline CI environment.

## 23. Testing Report

* `tests/test_checkpoint_17_4_contract_conformance.py` — 74 tests (generic
  conformance suite run against BOTH the reference adapter and the 17.2
  FakeBroker).
* `tests/test_checkpoint_17_4_reference_adapter.py` — 77 tests (models,
  translation, normalization, error taxonomy, mode binding, capabilities,
  idempotency, reconciliation, deterministic scenarios, no-network /
  broker-neutrality / dependency-direction / authorization-separation audits,
  immutability).
* `tests/test_checkpoint_17_4_integration.py` — 35 tests (Phase 13 full
  lifecycle matrix items 1–26 + end-to-end proofs).

Total new: **186 tests** (74 + 77 + 35).

Focused reruns:
* Checkpoint 17.2 + 17.3 suites: 156 passed (unchanged).
* Frozen Checkpoint 14–16 execution suites: 627 passed (unchanged).
* Full suite: **5823 passed**, 0 failed, 2 warnings.

## 24. Files Inspected

* `docs/checkpoint_17_1_broker_adapter_boundary_audit.md`
* `docs/checkpoint_17_2_broker_adapter_contract_design.md`
* `docs/checkpoint_17_3_broker_adapter_infrastructure_audit.md`
* `src/engine/models/broker_adapter.py`
* `src/engine/models/submission_lifecycle.py`
* `src/engine/models/execution_command.py`
* `src/engine/intelligence/broker_adapter_contract.py`
* `src/engine/intelligence/submission_lifecycle.py`
* `src/engine/intelligence/broker_adapter_infrastructure.py`
* `src/engine/intelligence/fake_broker.py`
* `src/engine/persistence/submission_store.py`
* `src/engine/persistence/submission_serialization.py`
* `src/engine/persistence/exceptions.py`
* `tests/test_checkpoint_17_2_contract.py`
* `tests/test_checkpoint_17_2_store.py`
* `tests/test_checkpoint_17_2_fake_broker.py`
* `tests/test_checkpoint_17_3_infrastructure.py`
* `tests/_checkpoint17_2_fixtures.py`

## 25. Code Changes

New files (ADDITIVE only; no frozen file modified):

* `src/engine/intelligence/reference_broker_adapter.py` — the concrete
  reference / simulated adapter + adapter-owned request/response models +
  deterministic simulation + normalization + factories.
* `tests/test_checkpoint_17_4_contract_conformance.py` — reusable generic
  contract conformance suite.
* `tests/test_checkpoint_17_4_reference_adapter.py` — reference-adapter-specific
  tests + audits.
* `tests/test_checkpoint_17_4_integration.py` — SubmissionInfrastructure
  integration tests.

No frozen Checkpoints 10–16 file was modified.

## 26. Findings

| Finding | Classification |
|---------|----------------|
| Concrete reference adapter implements the frozen broker-neutral contract | PASS |
| Adapter-owned translation is isolated (symbol/exchange/order type/product/quantity/price/client_order_id/idempotency key) | PASS |
| Adapter request/response models do not leak into the core domain | PASS |
| Adapter results are normalized into the broker-neutral AdapterResult | PASS |
| Adapter errors are normalized into the broker-neutral taxonomy | PASS |
| Execution mode is explicitly bound (PAPER/LIVE) and cannot silently cross | PASS |
| Unsupported capabilities fail correctly | PASS |
| Deterministic idempotency preserved (client_order_id/idempotency_key stable) | PASS |
| Adapter/ref-broker duplicate detection demonstrated | PASS |
| Reconciliation integrated (UNKNOWN -> reconcile -> confirmed) | PASS |
| Timeout/UNKNOWN behavior proven (never a false failure) | PASS |
| Duplicate submission behavior proven | PASS |
| SubmissionInfrastructure integration proven end-to-end | PASS |
| Generic contract tests exist and run against TWO adapters | PASS |
| Reference-adapter tests exist | PASS |
| End-to-end fake-broker tests exist | PASS |
| No broker-specific leakage | PASS |
| No network communication | PASS |
| No real broker integration | PASS |
| No broker SDK / credentials | PASS |
| Frozen Checkpoints 10–16 remain intact | PASS |
| Existing tests remain passing (full suite 5823 passed) | PASS |

No BLOCKER findings. No CONCERN findings requiring remediation before
Checkpoint 17.5.

## 27. Known Limitations

* The reference adapter is a SIMULATION; it does NOT prove a real broker is
  safe to integrate. It proves only contract conformance, boundary isolation,
  lifecycle integration, persistence behavior, reconciliation behavior, error
  normalization, mode isolation, idempotency mechanics, and testability.
* Application-level deterministic ids do NOT by themselves guarantee
  broker-side idempotency; the reference adapter's duplicate detection is an
  adapter-level mechanism and does NOT claim a real broker's idempotency
  guarantees.
* No real broker is integrated; `reconcile` / `cancel` semantics are validated
  only against the deterministic simulation.
* The lifecycle model tracks state snapshots; broker fills/prices/positions
  remain downstream and broker-specific.

## 28. Checkpoint 17.5 Recommendation

**Recommendation: A. Broker-specific adapter architecture/design audit.**

Checkpoint 17.5 should perform a broker-specific adapter architecture / design
audit (NOT a real broker integration). The reference adapter proves the
contract and infrastructure safely; the next step is to design the concrete
broker-specific translation layer for a target broker (e.g. Upstox) against
the frozen contract — symbol/exchange/order-type/product/quantity/idempotency
mappings, broker-specific error normalization, mode binding verification, and
a fake-broker integration test harness — WITHOUT connecting to any real broker.

Do NOT automatically integrate a real broker in Checkpoint 17.5. A real broker
integration requires a separate explicit authorization.

## 29. Final Verdict

**PASS**

The reference adapter proves the contract and infrastructure safely. All
success criteria are met: a concrete reference/simulated BrokerAdapter exists,
it conforms to the frozen 17.2 contract, it remains completely offline, no real
broker integration / SDK / credentials exist, adapter-owned translation is
isolated, results and errors are normalized, execution mode is explicitly
bound, unsupported capabilities fail correctly, deterministic idempotency is
preserved, reconciliation is integrated, timeout/UNKNOWN and duplicate
submission behavior are proven, SubmissionInfrastructure integration is proven,
generic contract tests + reference-adapter tests + end-to-end tests exist, no
broker-specific leakage exists, no network communication exists, frozen
Checkpoints 10–16 remain intact, existing tests remain passing, the full suite
has been run (5823 passed), documentation is complete, and AGENTS.md has been
appended with the 17.4 result.

**IMPORTANT**: a successful reference adapter does NOT prove that a real
broker is safe to integrate. It only proves contract conformance, boundary
isolation, lifecycle integration, persistence behavior, reconciliation
behavior, error normalization, mode isolation, idempotency mechanics, and
testability.
