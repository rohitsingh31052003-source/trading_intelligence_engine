# Checkpoint 17.5 - Real Broker Integration Preparation & Broker-Specific Boundary Safety Audit

## 1. Checkpoint 17.5 Overview

Checkpoint 17.5 is a **REAL BROKER INTEGRATION PREPARATION** plus
**BROKER-SPECIFIC BOUNDARY SAFETY AUDIT** checkpoint. It determines whether the
architecture -- after Checkpoints 17.1 (boundary audit), 17.2 (broker-neutral
contract), 17.3 (infrastructure, submission lifecycle integration, persistence)
and 17.4 (reference broker adapter + contract conformance) -- is ready to
safely introduce a REAL broker-specific adapter in a future checkpoint.

This is an **AUDIT AND PREPARATION** checkpoint. It is **NOT** a real broker
integration checkpoint. No real broker is contacted, no broker SDK is added, no
credentials are added, and no network path is introduced. The repository remains
fully offline and network-free.

The primary question answered:

> "What must be true before a real broker adapter such as an Upstox adapter can
> safely be introduced?"

The report clearly distinguishes:

* **ALREADY IMPLEMENTED** - proven and safe today (Checkpoints 17.1-17.4).
* **REQUIRED FOR FUTURE REAL BROKER** - a documented future control / design
  decision that a future checkpoint must deliver before real broker
  implementation.
* **OUT OF SCOPE FOR 17.5** - intentionally not implemented and not required by
  this checkpoint.

## 2. Objective

* Verify that the frozen execution architecture (Checkpoints 10-16) plus the
  broker-neutral contract / infrastructure / reference adapter (17.1-17.4)
  cannot accidentally contact, authorize, or submit to a real broker.
* Identify every boundary a future real broker adapter must respect so that
  broker-specific concerns (symbol, exchange, order types, products,
  credentials, HTTP, SDKs, errors, idempotency, reconciliation) remain
  **implementation details** and never leak into the core domain.
* Define the authentication, credential, network, timeout / ambiguous-outcome,
  broker-side idempotency, reconciliation, order-state mapping, error taxonomy,
  rate-limit / retry, paper/live isolation, fail-closed startup, live-execution
  guard, observability, response-validation, and clock/time requirements for a
  future real-broker adapter.
* Produce a security threat model, verify dependency direction, define the
  future test strategy, recommend the safest future checkpoint sequence, and
  perform a repository-wide secret / network safety sweep.
* Run the full regression suite to confirm zero regressions versus the
  Checkpoint 17.4 baseline (5823 passed / 0 failed).
* Produce this audit document and append the Checkpoint 17.5 entry to AGENTS.md.

## 3. Scope

**In scope:**

* Audit of the actual execution / broker architecture (Checkpoints 17.1-17.4
  implementation and documentation).
* Analysis and design (documentation only) of every boundary required for a
  future real broker adapter.
* Repository-wide secret / network / broker-reference safety sweep.
* Full regression test run.
* This document + the AGENTS.md entry.

**Out of scope:**

* Implementing a real broker adapter (Upstox or any broker).
* Adding any broker SDK, HTTP client, WebSocket client, API credential, access
  token, bearer token, or API key.
* Connecting to, submitting to, cancelling at, or reconciling against any real
  broker or broker API.
* Modifying any frozen Checkpoint 10-16 file or test.
* Adding any network-communication code for broker integration.
* Starting Checkpoint 17.6.

## 4. Non-Goals

Checkpoint 17.5 explicitly does NOT:

* prove that a real broker is safe (it only identifies and designs the boundary
  required to make future real-broker integration safe),
* implement a runnable live-execution path,
* test against a real broker account,
* use real broker credentials even if they already exist in the environment,
* modify the system so that a real broker could accidentally be contacted,
* treat the absence of a real broker adapter, live credentials, or network
  integration as a blocker (these absences are intentional and safe),
* add speculative abstractions without evidence from the audit.

## 5. Frozen Architecture Constraints

Checkpoints 10-16 remain **FROZEN** and are treated as completed architectural
contracts for this audit:

* Checkpoints 10.8, 11.8, 12.6 - historical-setup research adequacy, final
  output boundary, production integration.
* Checkpoint 13.6 - final execution architecture integration and freeze audit.
* Checkpoint 14.6 - final Operational Trade Intent freeze.
* Checkpoint 15.6 - final Execution Authorization freeze.
* Checkpoints 16.2-16.6 - Execution Command model, factory, persistence,
  integration and freeze audit.
* Checkpoints 17.1-17.4 - Broker Adapter boundary audit, contract design,
  infrastructure integration, reference adapter + conformance audit.

This checkpoint:

* does NOT refactor, reopen, or redesign any frozen component,
* does NOT change ExecutionCommand, command_id, authorization semantics,
  TradePlan, trading intelligence, research logic, setup analysis, decision
  analysis, opportunity filtering, historical research, or paper-trading
  semantics,
* does NOT modify any frozen test,
* documents any proposed change affecting a frozen component as a **future
  design requirement** rather than silently implementing it.

## 6. 17.1-17.4 Baseline

| Checkpoint | Deliverable | Verdict |
|-----------|-------------|---------|
| 17.1 | Broker Adapter Boundary Architecture Audit | PASS (16 PASS, 10 CONCERN design-time, 0 BLOCKER) |
| 17.2 | Broker Adapter Contract & Execution Lifecycle Contract | PASS (72 new tests; full suite 5551) |
| 17.3 | Broker Adapter Infrastructure, Submission Lifecycle Integration & Persistence Audit | PASS (84 new tests; full suite 5635) |
| 17.4 | First Concrete Broker Adapter / Reference Adapter & Contract Conformance Audit | PASS (186 new tests; full suite 5823) |

Checkpoint 17.4 established (verified again in this audit):

* `ReferenceBrokerAdapter` - a concrete, SIMULATED, offline adapter implementing
  the frozen `BrokerAdapter` contract.
* BrokerAdapter contract conformance - a REUSABLE generic contract suite
  (`BrokerAdapterContractConformanceBase`) proven against TWO adapters (the
  reference adapter and the unchanged `FakeBroker`).
* Generic contract tests (74), reference-adapter-specific tests (77),
  integration tests (35) - 186 new tests, all passing.
* `SubmissionInfrastructure` integration (submit / reconcile / recovery /
  audit).
* Lifecycle integration (CREATED -> SUBMISSION_REQUESTED -> SUBMITTED ->
  ACCEPTED/..., UNKNOWN -> RECONCILE_REQUIRED).
* Reconciliation (UNKNOWN -> reconcile -> confirmed; still-UNKNOWN never
  retried blindly).
* Deterministic idempotency (command_id -> client_order_id -> idempotency_key,
  stable across restart).
* Mode binding (PAPER/LIVE isolation, fail-closed).
* Error normalization (adapter-specific kinds -> broker-neutral taxonomy).
* Broker-neutral translation boundaries (isolated inside the adapter).
* No-network operation; no real broker; no broker SDK; no credentials; no live
  execution.
## 7. Current Architecture

Verified by source inspection (Checkpoints 16.2-17.4):

```
Trading Intelligence
        |
        v
TradePlan                                    (Product Phase 4)
        |
        v
OperationalTradeIntent                       (14.2 model / 14.4 engine / 14.5 application)
        |
        v
ExecutionAuthorization                       (15.2 model / 15.3 engine / 15.5 persistence)
        |
        v
ExecutionCommand                             (16.2 immutable, broker-neutral)
        |
        v
ExecutionCommandStore                        (16.5 atomic persistence)
        |
        v
SubmissionInfrastructure                     (17.3)
        |
        v
SubmissionLifecycleEngine + SubmissionLifecycleStore   (17.2)
        |
        v
BrokerAdapter (Protocol, broker-neutral)     (17.2)
        |
        v
ReferenceBrokerAdapter  /  FakeBroker        (17.4 / 17.2  -- both offline)
```

Relevant source modules (verified present and clean):

* `src/engine/models/operational_trade_intent.py`
* `src/engine/models/execution_authorization.py`
* `src/engine/models/execution_command.py` (immutable `ExecutionCommand`,
  `ExecutionMode`, fail-closed `create_execution_command` factory)
* `src/engine/models/broker_adapter.py` (`BrokerResultStatus`,
  `BrokerErrorCode`, `BrokerErrorCategory`, `BrokerError`, `AdapterResult`,
  `AdapterCapability`, `derive_broker_order_id`)
* `src/engine/models/submission_lifecycle.py` (`SubmissionLifecycle`,
  `SubmissionEvent`, `SubmissionState`, `create_submission_lifecycle`)
* `src/engine/intelligence/broker_adapter_contract.py` (`BrokerAdapter`
  Protocol, `AdapterCapabilities`, `derive_client_order_id`,
  `derive_idempotency_key`, `validate_adapter_mode`, `select_adapter`)
* `src/engine/intelligence/submission_lifecycle.py` - `SubmissionLifecycleEngine`
  (state machine, reconcile-before-retry, restart-recovery, blind-retry
  prohibition)
* `src/engine/intelligence/broker_adapter_infrastructure.py` -
  `SubmissionInfrastructure` (submit/reconcile/recovery/audit, persistence
  contract, `cp17_mode` binding)
* `src/engine/intelligence/fake_broker.py` - `FakeBroker` (deterministic,
  network-free, credential-free, test-only)
* `src/engine/intelligence/reference_broker_adapter.py` -
  `ReferenceBrokerAdapter` + `ReferenceSimulation` + adapter-owned
  request/response models
* `src/engine/persistence/exceptions.py` - typed exception hierarchy
  (Authorization / Command / Submission families)
* `src/engine/persistence/submission_serialization.py` / `submission_store.py`,
  `execution_command_serialization.py` / `execution_command_store.py`,
  `execution_authorization_serialization.py` / `execution_authorization_store.py`

The dependency graph traced from `ExecutionCommand` to `SubmissionInfrastructure`
to `BrokerAdapter` to `ReferenceBrokerAdapter` is a strict one-way chain:

* `models.execution_command` -> `models.execution_authorization` ->
  `models.operational_trade_intent` -> `models.trade_plan`
* `intelligence.broker_adapter_contract` -> `models.broker_adapter`,
  `models.execution_command`
* `intelligence.submission_lifecycle` -> `intelligence.broker_adapter_contract`,
  `models.broker_adapter`, `models.execution_command`,
  `models.submission_lifecycle`
* `intelligence.broker_adapter_infrastructure` ->
  `intelligence.submission_lifecycle`, `intelligence.broker_adapter_contract`,
  `persistence.submission_store`, `persistence.exceptions`
* `intelligence.reference_broker_adapter` / `intelligence.fake_broker` ->
  `intelligence.broker_adapter_contract`, `models.*`

No execution/broker module imports a network library, a broker SDK, Upstox,
Zerodha, yfinance, credentials, or `os.environ` (grep-verified, Section 31).

## 8. Future Real Broker Architecture

The audited (NOT implemented) future architecture:

```
Core (Trading Intelligence, TradePlan, Intent, Authorization, ExecutionCommand,
SubmissionLifecycle)
        |
        v
Broker-Neutral Contract   (BrokerAdapter Protocol - frozen 17.2)
        |
        v
Real Broker Adapter (broker-specific translation only)
        |
        v
Broker Integration Client (broker SDK / HTTP client - broker-specific)
        |
        v
Broker API
```

Intended invariants for the future layer:

* Core domain NEVER imports requests / httpx / urllib / websocket libraries /
  broker SDKs.
* `ExecutionCommand` NEVER carries broker-specific fields (symbol, exchange,
  order type, product, credentials, order id).
* Broker-specific exceptions NEVER escape the adapter boundary.
* The adapter is the ONLY component that knows how to talk to a broker.
* No authorization authority exists below the ExecutionAuthorization layer.
* A real broker adapter is selected / mode-verified exactly like the reference
  adapter (no parallel mode system).

## 9. Broker-Specific Boundary

The future real-broker adapter must be a THIN translation layer. Its boundary
delivers (ALREADY IMPLEMENTED in the contract) and what remains adapter-owned
(REQUIRED FUTURE):

| Concern | Broker-neutral contract today (17.2) | Future adapter ownership |
|---------|-------------------------------------|--------------------------|
| Input | immutable ExecutionCommand (frozen) | read-only; never mutate |
| Output | AdapterResult + BrokerError taxonomy | normalize broker response into it |
| Submit | submit(command) | translate command -> broker request |
| Reconcile | reconcile(client_order_id) | map broker lookup -> AdapterResult |
| Cancel | cancel(client_order_id) (optional) | map broker cancel -> AdapterResult |
| Capability | supports / check | broker capability gating |
| Mode | execution_mode + validate_adapter_mode | verify mode BEFORE every op |
| Errors | BrokerErrorCode / BrokerErrorCategory | normalize broker errors (never leak) |
| Idempotency | derive_client_order_id / derive_idempotency_key | map to broker-side mechanism |

Components that must NEVER live in the core: broker SDK objects, broker response
models, broker HTTP clients, broker authentication, broker order terminology,
broker product codes, broker rate-limit tables, broker-specific order states.

## 10. Translation Matrix

Each `ExecutionCommand` field -> broker-neutral meaning -> future adapter mapping
-> broker-specific representation. **NOT IMPLEMENTED - design guide for the
future adapter only.**

| ExecutionCommand field | Broker-neutral meaning | Future adapter mapping | Broker-specific representation (example only) |
|------------------------|------------------------|------------------------|-----------------------------------------------|
| command_id | immutable execution identity | base for client_order_id | adapter-specific order reference mapping |
| authorization_id / intent_id / content_fingerprint | audit binding | adapter request context if broker requires it; never persisted | n/a |
| instrument | canonical instrument name | symbol + exchange resolution | NSE_EQ\|... (Upstox), ^NSEI (Yahoo) |
| direction (LONG/SHORT) | trade side | side mapping | BUY/SELL |
| entry | reference price | price validation + precision | broker price format |
| stop / target | reference levels | trigger/stop price mapping | broker trigger fields (if supported) |
| quantity | decimal quantity | validation + lot/step resolution | broker lot-size multiples |
| planned_risk / maximum_risk | risk invariants (never altered) | risk validation only | n/a |
| execution_mode | PAPER/LIVE intent | adapter factory selection + mode verification | paper/live credential + environment |
| created_at / valid_from / valid_until | time bounds | time-window checks | broker validity/expiry fields |
| label / metadata | audit metadata | optional passthrough | n/a |

Translation classes:

* **ALREADY BROKER-NEUTRAL (A):** command_id, binding ids, economic fields
  (entry/stop/target/quantity/risk), execution_mode, timestamps. The frozen
  contract carries these; the adapter maps them.
* **ADAPTER-SPECIFIC (B):** symbol/exchange/order-type/product/price-precision/
  quantity-step/validity/cancel-id/reconcile-id/client_order_id.
* **POTENTIALLY DANGEROUS IF THEY LEAK INTO CORE (C):** broker order ids,
  broker fills, broker status strings, broker product codes, broker rate limits,
  broker-specific exceptions, credentials. Today NONE of these exist in the
  core or persistence (verified by grep, Section 31); the future adapter must
  never write them into core models or persistence.

## 11. Authentication Boundary

Design (architecture/design only - NOT implemented):

```
Core application
        |
        v
Broker Adapter         (broker-neutral protocol; NO auth logic)
        |
        v
Credential provider    (broker-specific; injected)
        |
        v
Broker client          (owns authentication handshake)
```

* Credentials MUST NOT appear in: ExecutionCommand, TradePlan,
  OperationalTradeIntent, SubmissionLifecycle, AdapterResult, generic
  BrokerError, generic persistence models, logs, audit records, or test
  fixtures.
* Credentials SHOULD live in a broker-specific credential provider /
  configuration source (e.g., environment variables or a secrets manager)
  consumed ONLY by the broker client.
* Injection: constructor injection of the credential provider into the
  adapter's broker client. The adapter itself never reads the token.
* Exclusion from persistence: submission / command / authorization stores
  serialize only deterministic identity + broker-neutral state (verified
  today).
* Exclusion from logs / exception messages: the broker client redacts /
  fails close; the AdapterResult / BrokerError boundaries carry
  non-sensitive reasons.
* Missing credentials -> fail closed (Section 14).
* Credential rotation: conceptual - rotate at the provider, new client with
  a new token; the execution system sees no change (identity/persistence
  unchanged).
* Paper/live credential isolation: separate credential providers per mode;
  the live provider is never injected into a paper adapter and vice versa.

## 12. Secret Safety

Audit results (see also Section 31 Repository Safety Sweep):

* Zero `.env` files checked into the repository.
* Zero hard-coded secret-like literals in `src/`, `scripts/`, `tests/`
  (pattern scan for api_key / secret / password / bearer / authorization
  with 12+ char values: 0 matches).
* The only credential-typed environment reads in the repository are:
  * `src/engine/data/historical_provider.py` - `UPSTOX_ANALYTICS_TOKEN`
    (`UPSTOX_TOKEN_ENV`), read lazily ONLY for the Upstox HISTORICAL DATA
    provider; sent ONLY in that provider's `Authorization: Bearer` header;
    never logged/printed; the provider is a candle-data provider, never an
    execution path.
  * `src/engine/data/corpus_ingestion.py` - credential PRECHECK for
    historical corpus ingestion (fails clean with zero API requests when
    missing). Historical data only.
* The execution / broker modules contain NO `os.environ` / token reads /
  network imports (verified by grep - zero matches).
* The codebase already has a redaction helper (`_BEARER_RE` in
  `corpus_ingestion.py`) that shadows `Bearer <token>` patterns in
  progress/failure text - a pattern a future broker adapter must reuse.
* Logs / exceptions / persistence / audit records today contain no
  credentials (verified: execution modules are clean; persistence models
  carry no credential fields).
* Finding: **PASS** - no accidental secret exposure. **REQUIRED FOR FUTURE
  REAL BROKER**: a dedicated broker-client redaction + fail-closed rule
  (never echo auth headers / error bodies verbatim).

## 13. Network Boundary

Design (architecture/design only - NOT implemented):

```
Broker Adapter
        |
        v
Broker Client        (adapter-owned or injected)
        |
        v
Network Transport   (broker SDK or HTTP client)
        |
        v
Broker API
```

* The core system does NOT directly import requests / httpx / urllib /
  socket / websocket libraries / broker SDKs. Verified today: the only
  urllib imports in the repository are inside the isolated HISTORICAL DATA
  provider (`engine/data/historical_provider.py`) - not an execution path.
* Timeout policy, connection / DNS / TLS failure handling, HTTP error
  handling, malformed-response handling, rate-limit handling, retry policy
  / retry safety, request / response correlation: ALL REQUIRED FOR FUTURE -
  the broker client owns them and normalizes everything through
  AdapterResult / BrokerError.
* No automatic real-broker retry is implemented or permitted in 17.5.
* No network layer is implemented.

## 14. Timeout and Ambiguous Outcome

The critical real-broker scenario:

```
Application sends order -> Broker may receive it -> Network response lost ->
Application does not know outcome
```

Audit of existing architecture (ALREADY IMPLEMENTED and sufficient):

* BrokerErrorCode.TIMEOUT / UNKNOWN_OUTCOME / MALFORMED_RESPONSE ->
  BrokerErrorCategory.AMBIGUOUS -> AdapterResult.UNKNOWN (never a false
  failure/success - verified: reference adapter timeout/unknown scenarios).
* SubmissionState.UNKNOWN -> requires_reconciliation True.
* SubmissionLifecycleEngine.request_submission REFUSES an UNKNOWN lifecycle
  (ValueError) - blind retry PROHIBITED by the frozen contract.
* SubmissionInfrastructure surfaces ReconciliationRequiredError for UNKNOWN.
* reconcile_submission queries with the SAME deterministic
  client_order_id; advances only on a confirmed outcome; still-UNKNOWN
  leaves UNKNOWN and retry remains prohibited.
* UNKNOWN survives restart (persisted; restart_recovery ->
  RECONCILE_REQUIRED).

| Scenario | Existing behavior | Sufficient |
|----------|-------------------|------------|
| timeout -> unknown outcome | UNKNOWN -> reconcile -> confirmed | YES |
| timeout -> blind retry | PROHIBITED (ValueError / ReconciliationRequiredError) | YES |
| broker accepts, response lost | UNKNOWN (never false failure) | YES |
| still-unknown after reconcile | stays UNKNOWN, no retry | YES |
| restart with unknown state | RECONCILE_REQUIRED | YES |

Additional REQUIRED FUTURE design note: reconcile-window and timeout bounds
need a broker-specific policy (Section 25); the retry rule must stay
"retryability depends on operation semantics + known broker outcome, not
merely on the presence of a transport error."

## 15. Broker-Side Idempotency

Three concepts (distinguished; documentation REQUIRED FUTURE for the
adapter):

| Concept | Provides | Owner |
|---------|----------|-------|
| command_id | deterministic immutable identity of the authorized command | core (16.2) |
| deterministic client_order_id / idempotency_key | APPLICATION-LEVEL duplicate identity stable across restart | contract (17.2) |
| broker-side idempotency mechanism | actual broker deduplication | broker + adapter (future) |

* What the application guarantees: the SAME command always yields the SAME
  client_order_id / idempotency_key; in-process and post-restart duplicate
  submission is detected and refused.
* What the adapter must guarantee (future): map the deterministic identity
  onto whatever broker-side key field the broker supports and verify the
  broker's duplicate contract before claiming deduplication.
* What the broker must guarantee: broker-specific (their contract).
* What cannot be guaranteed by this repository alone: broker-side
  deduplication - the future adapter must NEVER falsely claim "same
  client_order_id guarantees broker idempotency" unless the broker contract
  explicitly guarantees it.
* ALREADY DOCUMENTED in 17.2/17.3/17.4 (the distinction is on the audit
  surface). No change needed.

## 16. Reconciliation

REQUIRED FUTURE adapter behavior (design; the CONTRACT already supports it):

Reconcile against: accepted / rejected / partially filled / filled /
cancelled / unknown / missing / duplicate / delayed / stale orders.

* Identifiers needed: the deterministic client_order_id (already the sole
  id in the contract) + the adapter-owned broker query id (e.g., broker
  order id returned by the original submit). RECONCILE BY client_order_id;
  broker lookup may need both idempotency key and broker order id
  (adapter-owned).
* Broker returns multiple matching records -> ambiguous -> UNKNOWN (adapter
  must not fabricate a match).
* Broker returns no matching record -> normalized to an explicit
  unknown/REJECTED at adapter discretion; never a false success.
* Broker returns malformed data -> MALFORMED_RESPONSE -> UNKNOWN (fail
  closed).
* Broker temporarily unavailable -> BROKER_UNAVAILABLE / RATE_LIMIT
  (adapter-mapped; reconcile again later - never false determination).
* The core system receives only broker-neutral AdapterResult.

## 17. Order-State Mapping

Mapping analysis between generic BrokerResultStatus and possible real-broker
order states (future adapter design):

| BrokerResultStatus | Typical broker states | Mapping clarity |
|--------------------|----------------------|----------------|
| SUBMITTED | order transmitted, pending | clear |
| ACCEPTED | accepted / queued / acknowledged | clear |
| PARTIALLY_FILLED | partial fill | clear |
| FILLED | filled | clear |
| CANCELLED | cancelled | clear |
| REJECTED | rejected (validation / broker rules) | clear |
| FAILED | deterministic transport / internal failure | clear |
| UNKNOWN | unknown, lost, ambiguous, unconfirmable | clear - MUST map to UNKNOWN (reconcile) |

* Possible states that do not map cleanly (CONCERN for future adapter):
  * a broker state meaning "accepted but not submittable yet" (pending
    approval / margin check) - no EXACT match; map to ACCEPTED only after
    confirmed semantics, otherwise UNKNOWN.
  * a broker state that mixes multiple conditions (e.g., "completed" with
    explicit is_rejected flags) - MUST be resolved before mapping; an
    unresolved mix -> UNKNOWN.
  * a broker "finished/expired" (session-expired order) - has no generic
    member; draft as CANCELLED or REJECTED per broker semantics, otherwise
    UNKNOWN.
* Rule: **Never force an unsafe mapping.** If a broker state cannot be
  safely represented, classify it as CONCERN and map to UNKNOWN (reconcile)
  until the future checkpoint defines exact mapping.

## 18. Error Taxonomy Mapping

Mapping of realistic broker failures to the existing broker-neutral taxonomy
(verified present in `broker_adapter.py`):

| Failure | BrokerErrorCategory | BrokerErrorCode |
|---------|---------------------|-----------------|
| validation error | VALIDATION | VALIDATION_FAILURE |
| unsupported instrument | VALIDATION | UNSUPPORTED_INSTRUMENT |
| unsupported order type / semantics | VALIDATION | UNSUPPORTED_ORDER_SEMANTICS |
| unsupported operation | VALIDATION | UNSUPPORTED_OPERATION |
| authentication failure | TRANSPORT | AUTHENTICATION_FAILURE |
| authorization failure | BROKER_REJECTION | AUTHORIZATION_FAILURE |
| insufficient funds | BROKER_REJECTION | BROKER_REJECTION (adapter-mapped) |
| invalid quantity / invalid price | VALIDATION | VALIDATION_FAILURE (adapter-mapped) |
| market closed | BROKER_REJECTION (or VALIDATION) | BROKER_REJECTION (adapter-mapped) |
| rate limit | TRANSPORT | RATE_LIMIT |
| timeout | AMBIGUOUS | TIMEOUT |
| connection failure | TRANSPORT | NETWORK_FAILURE |
| broker unavailable | TRANSPORT | BROKER_UNAVAILABLE |
| duplicate order | adapter-mapped -> BROKER_REJECTION or ACCEPTED-with-duplicate-status | adapter-owned |
| malformed broker response | AMBIGUOUS | MALFORMED_RESPONSE |
| internal adapter error | INTERNAL | INTERNAL_ADAPTER_FAILURE |
| ambiguous result | AMBIGUOUS | UNKNOWN_OUTCOME |

* No broker-specific exception class leaks into the core (verified: only
  AdapterResult / BrokerError cross the boundary).
* Broker-specific detail stays inside the adapter; only broker-neutral
  code / category / retryable / message surface.

## 19. Rate Limits and Retry Safety

Design policy (REQUIRED FUTURE; NO automatic real-broker retry is
implemented or authorized):

* HTTP rate limiting / broker throttling / temporary broker outages map to
  RATE_LIMIT / BROKER_UNAVAILABLE (TRANSPORT, retryable=True at the
  error-code level) - retryability only means a *fresh, semantically-safe*
  attempt may be considered after the appropriate backoff and
  reconciliation of pre-submission state.
* SAFE RETRY vs UNSAFE RETRY:
  * SAFE RETRY: a GET/reconciliation request (reconcile) may be retried -
    it sends no new order.
  * UNSAFE RETRY: an unknown-order submission MUST NOT automatically be
    retried merely because a transport error occurred - the broker may have
    accepted it (this is already prohibited: UNKNOWN / request_submission
    refuses).
* The rule (documented):
  > "Retryability depends on operation semantics and known broker outcome,
  > not merely on the presence of a transport error."
* CONCERN: the existing BrokerError.retryable=True for AUTHENTICATION,
  RATE_LIMIT, NETWORK, BROKER_UNAVAILABLE is an error-code-level flag and
  must be interpreted together with lifecycle state (a transport error after
  a submission attempt must NOT auto-retry the submit - reconcile first).
  The future adapter must document this clearly and never auto-retry a
  submit blind.

## 20. Paper/Live Isolation

Audit of existing isolation (ALREADY IMPLEMENTED):

| Concern | Existing mechanism | Status |
|---------|--------------------|--------|
| adapter selection | select_adapter fail-closed mode match | PASS |
| execution mode | derived from authorization scope, non-overridable | PASS |
| configuration | adapters registry + paper_.../live_... factories | PASS |
| credentials | none exist; future per-mode credential providers | PASS (design) |
| persistence | lifecycle records carry cp17_mode binding; reconcile/cancel verify it | PASS |
| environment variables | no execution-mode env vars | PASS |
| test fixtures | paper/live command helpers; mode-mismatch tests | PASS |
| startup behavior | missing/mismatched adapter fails closed | PASS |

Required additional safeguards for a real broker (future):

* NO LIVE -> PAPER fallback and NO PAPER -> LIVE fallback anywhere (today:
  select_adapter raises on mismatch; future live integration must also
  raise - never silently downgrade live-to-paper).
* A live adapter must only ever exist with a live credential provider; a
  live command with no live adapter/credential fails closed at startup.
* Environment separation for live (production) vs paper (sandbox):
  documented as a future operational control.

## 21. Fail-Closed Startup

Required behavior for a future live-broker startup (design; the fail-closed
pattern already exists for adapter selection / credential gates):

| Condition | Required behavior |
|-----------|------------------|
| Missing broker credentials | FAIL CLOSED (no live adapter constructed) |
| Invalid broker configuration | FAIL CLOSED |
| Unsupported instrument | FAIL CLOSED (supports/check -> ValueError, no order) |
| Unsupported order type | FAIL CLOSED |
| Unknown execution mode | FAIL CLOSED |
| Broker adapter unavailable | FAIL CLOSED (empty/mismatch registry -> ValueError) |
| Live -> paper downgrade | FOREVER PROHIBITED |

The system must NEVER silently downgrade from live to paper. This is already
the behavior in the frozen contract/infrastructure for everything except
actual credential gating (which has no implementation yet - future).

## 22. Live Execution Guard Requirements

Checklist of REQUIRED FUTURE safeguards before the first real order can ever
be submitted (design; none of these are implemented in 17.5 and none are
automatic):

- [ ] explicit live mode + explicit broker selection (non-ambiguous)
- [ ] credential presence + credential validity verification (fail closed)
- [ ] instrument validation (adapter capability boundary)
- [ ] quantity validation (steps/lots, non-negative, capped)
- [ ] price validation (precision/ticks, within tolerance)
- [ ] capability validation (supports / check)
- [ ] authorization verification (AUTHORIZED-only factory - ALREADY in 16.2)
- [ ] command existence verification (command persisted before submission -
      ALREADY in store)
- [ ] duplicate submission detection (store guard + client_order_id -
      ALREADY in 17.2/17.3)
- [ ] reconcile-before-retry (UNKNOWN -> reconcile - ALREADY enforced)
- [ ] adapter health check (check / supports - contract surface existing)
- [ ] broker availability check (future adapter-level health probe; not in
      contract)
- [ ] audit record creation (submission lifecycle records - ALREADY
      persisted)
- [ ] explicit operator confirmation if required (future application-level
      gate; no UI exists for it)
- [ ] environment separation (paper vs live deployments - future
      operational)

## 23. Observability and Audit Trail

What must be observable when a real broker exists (REQUIRED FUTURE, largely
ALREADY persistable today):

| Artifact | Today | Future |
|----------|-------|--------|
| command_id | persisted (command store) | - |
| submission identity | submission_id persisted | - |
| adapter identity | name / execution_mode (adapter registry) | - |
| execution mode | cp17_mode on lifecycle | - |
| request/response timestamps | lifecycle created_at / events | adapter timestamps (future) |
| lifecycle transitions | SubmissionEvent tuple | - |
| normalized result | lifecycle state + last_reason | - |
| normalized error | BrokerError detail in events (error_code / error_category) | - |
| reconciliation events | reconcile events flagged | - |
| retry decisions | retry_allowed audit row | - |
| recovery decisions | RecoveryAction view | - |

Must NEVER store: access tokens, API keys, client secrets, authorization
headers, sensitive broker credentials. Verified today: persistence is clean.
REQUIRED FUTURE: the broker client must redact credentials from any error
text before it reaches normalization (existing _BEARER_RE pattern reusable).

## 24. Broker Response Validation

A real broker may return malformed / incomplete / unexpected / schema-changing
responses. The future adapter MUST validate:

- response type / shape (JSON / fields present),
- required identifiers (client_order_id, broker_order_id when expected),
- status (known broker status string; unknown -> UNKNOWN / MALFORMED),
- timestamps (parsable, sane order),
- quantities / prices (finite Decimal; never fabricate),
- order identifiers + reconciliation identifiers (cross-check with the
  submitted client_order_id),
- fail closing: a response that cannot be safely normalized -> typed
  MALFORMED_RESPONSE / UNKNOWN_OUTCOME (never a success).
- "Never assume a successful HTTP response means a successful order" - the
  normalizer maps only explicit confirmed broker states to ACCEPTED / FILLED.

## 25. Clock/Time Requirements

Audited areas where a real-broker integration depends on time:

| Area | Dependence |
|------|-----------|
| timestamps | lifecycle/events created_at (caller-supplied) |
| timeout windows | broker-client timeout policy (future adapter) |
| reconcile windows | reconcile-after-timeout policy (future adapter) |
| stale state | lifecycle state staleness checks (future) |
| idempotency expiry | broker-side idempotency TTL (adapter) |
| broker timestamps | broker response timestamps (validation) |
| local timestamps | adapter local time (only for correlation, never identity) |

Finding: today there is NO wall-clock dependence in the execution modules
(verified - no datetime.now() in models/engines/infrastructure/persistence;
all timestamps caller-supplied). **A clock abstraction will eventually be
required** for a real broker (controllable "now" for timeout / stale /
reconcile-window decisions) - do NOT introduce it in this checkpoint;
document for the future integration checkpoint.

## 26. Future Upstox Boundary - FUTURE / OUT OF SCOPE

All references below are explicitly **FUTURE / OUT OF SCOPE for 17.5**. No
Upstox connection, SDK, credential, or API call exists or is added.

A future Upstox adapter would need to isolate (design only):

- auth: Upstox access-token authentication (credential provider, never in
  core);
- instrument mapping: canonical name -> NSE_EQ|... instrument keys
  (isolated map, like the historical provider's isolated symbol map -
  historical data only today);
- exchange mapping: NSE etc.;
- order-type mapping: BUY/SELL etc.;
- product mapping: cash/margin/... (adapter-owned table);
- order submission / cancellation / lookup / reconcile: broker API calls
  (broker client behind the adapter);
- broker-specific statuses: normalize into BrokerResultStatus (Section 17);
- broker-specific errors: normalize into BrokerErrorCode (Section 18);
- broker-specific rate limits: map to RATE_LIMIT (Section 19);
- broker-specific idempotency: map the deterministic identity onto Upstox's
  mechanism (Section 15);
- API response validation (Section 24).

The existing Upstox integration in this repository is the HISTORICAL DATA
provider (`engine/data/historical_provider.py`) - a read-only OHLCV data
source that is NEVER imported by any execution artifact (verified). It is
not an execution path.

## 27. Security Threat Model

| # | Threat | Existing protection | Remaining gap | Required future control | Severity | Checkpoint |
|---|--------|--------------------|---------------|-------------------------|----------|------------|
| 1 | Wrong execution mode | mode derived from authorization; validate_adapter_mode fail-closed | none | none (keep) | HIGH | frozen / 17.2 |
| 2 | Wrong adapter selected | select_adapter fail-closed mode match | none | none (keep) | HIGH | 17.2 |
| 3 | Missing/invalid credentials | no credentials exist; historical gate prototype | no live credential gate | live credential gate (fail close) | HIGH | future broker CP |
| 4 | Credential leakage | no credentials in core/persistence/logs; Bearer redaction helper | broker client error text | broker-client redaction rule | HIGH | future broker CP |
| 5 | Duplicate submission | store guard + client_order_id + lifecycle refusal | broker-side dedup | broker idempotency mapping | HIGH | 17.2/17.3/future |
| 6 | Unknown broker response | AMBIGUOUS -> UNKNOWN -> reconcile | broker-state normalization | response validation + state mapping | HIGH | 17.2/future |
| 7 | Network timeout | UNKNOWN never false failure; no blind retry | timeout-window policy | adapter timeout policy | MEDIUM | 17.2/future |
| 8 | Broker API outage | BROKER_UNAVAILABLE / RATE_LIMIT (TRANSPORT) | per-outage messaging | adapter outage handling | MEDIUM | future |
| 9 | Malformed broker response | MALFORMED_RESPONSE -> AMBIGUOUS | detail validation | response validator | HIGH | future |
| 10 | Broker rejects request | REJECTED + BROKER_REJECTION | interpretation | documented interpretation | MEDIUM | 17.2/future |
| 11 | Instrument mapping error | UNSUPPORTED_INSTRUMENT capability gate | Upstox symbol table | isolated symbol map + fail-close | HIGH | future broker CP |
| 12 | Order-type mapping error | UNSUPPORTED_ORDER_SEMANTICS | broker order types | isolated side/type map | HIGH | future broker CP |
| 13 | Quantity/price conversion error | Decimal verbatim, risk invariant | step/lot conversion | validation + never-increase rule | HIGH | 17.4/future |
| 14 | Unsafe retry | UNKNOWN won't retry; reconcile-first | transport-error retry semantics | documented SAFE/UNSAFE retry rule | HIGH | 17.2/future |
| 15 | Paper/live cross-contamination | mode binding + selection | live credential isolation | env separation + fail-closed | HIGH | 17.2/future |
| 16 | Restart during submission | persisted lifecycle + restart-recovery | post-restart live-idempotency entry | recovery decision + confirm | HIGH | 17.3/future |

## 28. Dependency Direction

Verified dependency direction (import graph of the execution/broker modules,
Section 7):

* Core models import only core models. Persistence imports models +
  exceptions. Intelligence contract/engines import models only.
  Infrastructure imports contract + engine + models + persistence.
  Reference/Fake import contract + models. Dashboard imports the intent
  application service only (no execution artifact imports
  analysis/scanner/decision/paper-trade in reverse).
* No execution/broker module imports network, broker SDKs, Upstox, Zerodha,
  yfinance, or credentials (grep-verified).

Target for the future: Core -> BrokerAdapter -> Concrete Broker Adapter ->
Broker Client / SDK -> Network. NOT Core -> Upstox SDK, NOT
ExecutionCommand -> Upstox Order Model, NOT Trading Intelligence -> Broker
API. No violations exist today.

## 29. Future Test Strategy

Test pyramid (design for BEFORE any real-broker implementation):

| Level | Suite | Owner |
|-------|-------|-------|
| 1 | broker-neutral contract tests | exists (17.2/17.4 conformance) |
| 2 | reference adapter tests | exists (17.4) |
| 3 | real broker adapter unit tests with mocked broker client | future |
| 4 | broker response normalization tests | future |
| 5 | failure-injection tests (timeout/malformed/rate-limit/outage) | future |
| 6 | reconciliation tests (accepted/rejected/unknown/missing/duplicate) | future |
| 7 | persistence/restart tests | partially exists (17.2/17.3); extend for adapter refs |
| 8 | paper/sandbox integration tests, if supported | future (only if broker sandbox) |
| 9 | limited controlled live validation, ONLY in a future explicitly authorized checkpoint | future (explicit approval) |

NEVER test against a real broker in automated CI: no live API calls, no real
account, no real credentials, no real orders - the future live validation
step (9) must be a separate, controlled, explicitly authorized process.

## 30. Recommended Future Checkpoint Sequence

Based on actual findings, the safest sequence (numbers are suggestions):

| Seq | Checkpoint | Content |
|-----|-----------|--------|
| 17.5 | (this) | preparation & boundary audit |
| 17.6 | real broker adapter design (target broker documented against the frozen contract; translation matrix, error/state mapping, reconciliation design) - NO implementation |
| 17.7 | real broker adapter implementation WITHOUT live submission (mocked broker client; no credentials; no network) |
| 17.8 | sandbox/paper broker integration (broker sandbox API only, if available; no live orders) |
| 17.9 | reconciliation and failure audit against sandbox |
| 18.x | explicitly authorized controlled live integration (requires separate explicit authorization) |

Do NOT begin 17.6 automatically. Wait for explicit authorization.

## 31. Repository Safety Sweep

Sweep performed (Phases 5 and 25):

| Search | Result |
|--------|--------|
| `.env` files in repo | NONE |
| hard-coded secret-like literal strings in `src/scripts/tests` | 0 matches |
| os.environ reads with token/secret names | exactly 2 (historical Upstox token read + corpus ingestion precheck) - NOT execution |
| network imports (requests/httpx/socket/websocket/urllib) in execution/broker modules | 0 |
| urllib imports repository-wide | exactly 1 module (`historical_provider.py`) - historical data only |
| Upstox/Zerodha references in execution/broker modules | 0 |
| broker-SDK / HTTP-client imports | 0 |
| live/submit/cancel-order executable refs in execution modules | 0 (docstring words only) |
| credentials in command/authorization/intent/submission/adapter/persistence/logs/audit/models | 0 |

All broker / credential / network terms that appear in the repository are
documentation (docstrings describing non-goals / NOT lists) or the isolated
historical data provider - none are execution paths. **No real broker
integration exists. No credentials exist. No network path exists.**

## 32. Testing Report

| Suite | Result |
|-------|--------|
| 17.2 contract tests (`tests/test_checkpoint_17_2_contract.py`) | 25 passed |
| 17.2 store tests (`tests/test_checkpoint_17_2_store.py`) | 21 passed |
| 17.2 fake-broker tests (`tests/test_checkpoint_17_2_fake_broker.py`) | 26 passed |
| 17.3 infrastructure tests (`tests/test_checkpoint_17_3_infrastructure.py`) | 84 passed |
| 17.4 conformance tests (`tests/test_checkpoint_17_4_contract_conformance.py`) | 74 passed |
| 17.4 reference-adapter tests (`tests/test_checkpoint_17_4_reference_adapter.py`) | 77 passed |
| 17.4 integration tests (`tests/test_checkpoint_17_4_integration.py`) | 35 passed |
| **Checkpoint 17.2-17.4 subtotal** | **342 passed** |
| Frozen 14-16 execution suite (command/store, authorization+engine+store, intent+engine+app) | 627 passed |
| Planning/paper-trading regression (trade_planning, paper_trading, paper_trading_operations, run_cycle, live_paper_validation) | 488 passed |
| **FULL SUITE (`tests/`)** | **5823 passed, 0 failed, 2 warnings** |

* No test was modified.
* The two historical yfinance baseline failures were already resolved in 17.4
  by installing the declared optional dependency `yfinance`; both live-data
  tests now pass.
* Full-suite result EXACTLY matches the Checkpoint 17.4 baseline (5823
  passed / 0 failed) - no regression.

## 33. Files Inspected

* Docs: `docs/checkpoint_17_1_broker_adapter_boundary_audit.md`,
  `docs/checkpoint_17_2_broker_adapter_contract_design.md`,
  `docs/checkpoint_17_3_broker_adapter_infrastructure_audit.md`,
  `docs/checkpoint_17_4_reference_adapter_audit.md`, `AGENTS.md`.
* Source: `src/engine/models/operational_trade_intent.py`,
  `execution_authorization.py`, `execution_command.py`, `broker_adapter.py`,
  `submission_lifecycle.py`;
  `src/engine/intelligence/broker_adapter_contract.py`,
  `submission_lifecycle.py`, `broker_adapter_infrastructure.py`,
  `reference_broker_adapter.py`, `fake_broker.py`;
  `src/engine/persistence/exceptions.py`, `submission_serialization.py`,
  `submission_store.py`, `execution_command_serialization.py`,
  `execution_command_store.py`, `execution_authorization_serialization.py`,
  `execution_authorization_store.py`;
  `src/dashboard/app.py`, `src/dashboard/services.py`.
* Tests: all Checkpoint 17.2-17.4 test files, frozen 14-16 execution test
  files, `tests/_checkpoint_17_2_fixtures.py`.
* Sweep: repository-wide secret/network/broker-reference grep.

## 34. Code Changes

**ZERO production-code changes.** This is an audit/design checkpoint. No
source file was modified. No test file was modified.

Files ADDED by this checkpoint:

* `docs/checkpoint_17_5_real_broker_integration_preparation_audit.md`
  (this document)
* `AGENTS.md` (appended Checkpoint 17.5 entry)

## 35. Findings

PASS findings (ALREADY IMPLEMENTED and verified):

| # | Finding |
|---|---------|
| 17.5-F01 | ExecutionCommand remains immutable and broker-neutral (model + grep verified) |
| 17.5-F02 | command_id / client_order_id / idempotency_key deterministic and restart-stable |
| 17.5-F03 | Authorization remains strictly upstream; BrokerAdapter has zero authorization authority |
| 17.5-F04 | Submission lifecycle separate from command; references command_id only |
| 17.5-F05 | All concrete broker logic isolated behind BrokerAdapter (reference / Fake) |
| 17.5-F06 | Zero broker SDK / network / credential references in every core, execution, broker, persistence module |
| 17.5-F07 | Timeout -> UNKNOWN; UNKNOWN requires reconciliation; blind retry prohibited (test-proven) |
| 17.5-F08 | AMBIGUOUS error class (TIMEOUT/MALFORMED/UNKNOWN_OUTCOME) never auto-retryable |
| 17.5-F09 | Paper/live cannot silently cross (mode binding + selection + cp17_mode) |
| 17.5-F10 | Missing adapter / mode mismatch fails closed |
| 17.5-F11 | Malformed inputs / unknown codes fail closed (BrokerError.for_code, _state_for_result) |
| 17.5-F12 | Network/transport failures cannot produce false success |
| 17.5-F13 | Persistence model clean; audit surface broker-neutral |
| 17.5-F14 | Dependency direction verified (core <- adapter <- future client) |
| 17.5-F15 | No real broker / no credentials / no network path in 17.5 |
| 17.5-F16 | Frozen Checkpoints 10-16 intact; no frozen test modified; full suite 5823 pass |
| 17.5-F17 | Application-level vs broker-level idempotency distinction already documented |
| 17.5-F18 | Reference adapter proves contract conformance against TWO adapters |

CONCERN findings (design-time; the current architecture remains safe):

| # | Finding | Where it must be addressed |
|---|---------|---------------------------|
| 17.5-C01 | Live credential presence/validity gate does not exist (no live credentials) | future broker checkpoint (17.7) |
| 17.5-C02 | Broker-specific symbol/exchange/order-type/product tables do not exist | future adapter design (17.6) |
| 17.5-C03 | Broker-side idempotency mapping (exact broker contract) not defined | future adapter (17.7) |
| 17.5-C04 | Timeout-window and reconcile-window policy for a real broker not defined | future adapter (17.7) |
| 17.5-C05 | Real-broker order-state mapping has states that may not map cleanly (Section 17) | future adapter design (17.6) |
| 17.5-C06 | Broker-client error-redaction rule not yet enforced (no broker client) | future adapter (17.7) |
| 17.5-C07 | Clock abstraction not present (no wall-clock today); required for real-broker timeout/stale decisions | future integration checkpoint (17.7/17.9) |
| 17.5-C08 | Some realistic error codes are adapter-mapped (insufficient_funds -> BROKER_REJECTION, market_closed -> adapter-mapped); mapping documented for future adapter | 17.6 |
| 17.5-C09 | No live-availability health probe in the contract (adapter-level check only) | 17.7 |

BLOCKER: **NONE.** The absence of a real broker adapter, live credentials,
or network integration is intentional and NOT a blocker.

## 36. Known Limitations

* This is an audit/design checkpoint: nothing real is implemented.
* The report contains no executable code; the interfaces/protocols/types /
  dependency-injection seams that ALREADY exist (Protocol,
  validate_adapter_mode, select_adapter, supports/check) were reused for
  design proof.
* No hidden future network path was created (sweep-verified).
* Broker-specific design decisions (exact mappings) are left to 17.6+.

## 37. Checkpoint 17.6 Recommendation

**Do NOT start 17.6 automatically - wait for explicit authorization.**

Recommended 17.6 content: a **REAL BROKER ADAPTER DESIGN** checkpoint (NOT
implementation): produce the concrete broker-specific translation design for
a target broker (e.g., Upstox) against the frozen contract - translation
matrix (Section 10), authentication/credential boundary (Section 11),
order-state mapping (Section 17), error mapping (Section 18), rate-limit /
retry policy (Section 19), timeout/reconcile windows (Sections 14/16/25),
broker idempotency mapping (Section 15), response validation contract
(Section 24), and the guarded start-up + live-execution guard checklists
(Sections 21/22). No broker connection, no SDK, no credentials, no API
calls during 17.6.

## 38. Final Verdict

**PASS WITH LIMITATIONS**

The architecture is safe and adequately prepared for the next
broker-specific design stage. The frozen 17.1-17.4 execution /
broker-neutral architecture was verified: the broker-neutral contract +
infrastructure + reference adapter prove contract conformance,
reconciliation, idempotency, error normalization, mode isolation and
fail-closed behavior offline and network-free. The remaining items are
explicitly documented future controls (credential gate, broker mapping
tables, broker-side idempotency mapping, timeout/reconcile windows, clock
abstraction, live guards, adapter-level health probe, tested test pyramid)
that must be delivered by a future checkpoint BEFORE any real broker
implementation. NO real broker was contacted; NO credentials were added; NO
network path was introduced; NO broker SDK was added; Checkpoints 10-16
remain frozen; full suite 5823 passed / 0 failed (no regression).

**IMPORTANT SAFETY STATEMENT**: A PASS here ONLY means "the architecture is
ready to proceed to the next design/implementation checkpoint." It does NOT
mean "the system is authorized to trade." The system remains offline,
paper/simulation-only, and fail-closed by construction.
