# Checkpoint 17.1 — Broker Adapter Boundary Architecture Audit

## 1. Checkpoint 17.1 Overview

This checkpoint is an ARCHITECTURE-FIRST AUDIT of the future Broker Adapter
boundary that will sit between the frozen Execution Command layer and an
External Broker. It determines whether the existing Trading Intelligence Engine
is ready to introduce a broker-neutral Broker Adapter boundary without
contaminating or reopening the frozen Checkpoints 10-16 architecture.

This is an AUDIT-ONLY checkpoint. The Broker Adapter is NOT implemented. No
broker SDK is added. No orders are submitted. No broker is contacted. The audit
is based on the ACTUAL repository implementation, tests, documentation, and
architecture — not on assumptions.

The intended boundary under audit:

    Execution Command -> [FUTURE BROKER ADAPTER] -> [FUTURE BROKER]

The system currently has NO live broker execution capability.

## 2. Scope and Non-Goals

In scope:

* Inspect the actual Execution Command implementation (model, identity,
  factory).
* Inspect authorization integration (command creation from AUTHORIZED intent).
* Inspect the persistence boundary (ExecutionCommandStore, serialization).
* Audit broker neutrality (search for broker-specific leakage).
* Analyze execution lifecycle risks, idempotency / duplicate-submission risks,
  paper/live isolation, error handling, testing requirements, dependency
  direction, and future Upstox integration requirements.
* For each responsibility, determine whether it belongs upstream of the adapter,
  inside the broker-neutral adapter contract, inside a broker-specific
  implementation, or outside the execution system entirely.
* Recommend the scope of Checkpoint 17.2.

Non-goals (this checkpoint does NOT):

* Implement the Broker Adapter or any part of it.
* Connect to Upstox or any broker.
* Add live trading functionality, broker SDK dependencies, API credentials, or
  authentication tokens.
* Add broker-specific mappings or product-code tables.
* Modify trading intelligence, setup analysis, research logic, trade planning,
  authorization semantics, or command semantics.
* Modify frozen Checkpoints 10-16 merely for cleanup.
* Weaken any fail-closed behavior or bypass existing safety boundaries.
* Begin Checkpoint 17.2.

## 3. Frozen Architecture Constraints

The following boundaries are FROZEN and are treated as completed architectural
contracts for this audit:

* Checkpoints 10.8, 11.8, 12.6 — historical-setup research adequacy, final
  output boundary, production integration.
* Checkpoint 13.6 — final execution architecture integration and freeze audit.
* Checkpoint 14.6 — final Operational Trade Intent freeze.
* Checkpoint 15.6 — final Execution Authorization freeze.
* Checkpoints 16.2-16.5 — Execution Command model, factory, persistence.
* Checkpoint 16.6 — Execution Command integration and freeze audit (reported
  complete in the project context).

Prior audit/design documents reviewed for this checkpoint:

* `docs/checkpoint_13_5_execution_command_to_broker_adapter_boundary_audit.md`
  — conceptual pre-implementation design of the Broker Adapter boundary
  (responsibility matrix, lifecycle, idempotency, retry safety, paper/live
  isolation, error handling, future testing contract).
* `docs/checkpoint_16_4_execution_command_persistence_and_lifecycle_boundary_audit.md`
* `docs/checkpoint_16_5_execution_command_persistence_implementation.md`

## 4. Current Execution Architecture

Repository inspection confirms the implemented execution chain:

```
src/engine/models/operational_trade_intent.py           OperationalTradeIntent (14.2)
src/engine/intelligence/operational_trade_intent.py     OperationalTradeIntentEngine (14.4)
src/engine/intelligence/operational_trade_intent_application.py  ApplicationService (14.5)
src/engine/models/execution_authorization.py            ExecutionAuthorization (15.2)
src/engine/intelligence/execution_authorization.py      ExecutionAuthorizationEngine (15.3)
src/engine/persistence/execution_authorization_store.py ExecutionAuthorizationStore (15.5)
src/engine/models/execution_command.py                  ExecutionCommand + create_execution_command (16.2)
src/engine/persistence/execution_command_serialization.py  (16.5)
src/engine/persistence/execution_command_store.py       ExecutionCommandStore (16.5)
src/engine/persistence/exceptions.py                    Command* typed exceptions (16.5)
```

Key verified facts:

* `ExecutionCommand` has ZERO production consumers. A repository-wide search for
  `create_execution_command`, `ExecutionCommand`, and `ExecutionCommandStore`
  finds references only in the command model/persistence modules, the exception
  module, and the checkpoint test files. No dashboard route, service method,
  planning engine, paper-trading path, or operations layer constructs or
  consumes commands.
* The dashboard exposes NO execution route. `src/dashboard/app.py` contains only
  analysis / scanner / workstation / trade-plan / operational-trade-intent /
  paper-trading / historical-data endpoints. There is no `submit`, `place_order`,
  or command route.
* `PaperTrade` (Product Phase 5) is a sibling path from `TradePlan`; it is never
  referenced by the command model or store.
* The only Upstox integration is the HISTORICAL DATA provider
  (`engine/data/historical_provider.py`), which reads OHLCV candles. It is a
  data provider, NOT an execution path, and is never imported by any execution
  artifact.
* No execution lifecycle state (SUBMITTED / ACKNOWLEDGED / FILLED / etc.), no
  broker order ID, no client order ID, no idempotency key, and no submission
  record exists anywhere in `src/`. (Verified by grep; only paper-trading
  simulation lifecycle exists.)

## 5. Execution Command Contract Audit

### 5.1 What exactly is an Execution Command?

From the model docstring and implementation: an `ExecutionCommand` is an
immutable, broker-neutral snapshot of an ALREADY-AUTHORIZED
`OperationalTradeIntent`. It represents the exact authorized command that may be
handed to a future execution adapter. It is NOT a broker order, NOT a broker
request, NOT a position, NOT a fill, NOT an execution result, NOT an
account/portfolio object, and NOT an authorization artifact. It performs no
business logic; construction goes exclusively through the
`create_execution_command` factory.

### 5.2 Fields and requirements

`@dataclass(frozen=True, slots=True) ExecutionCommand`:

| Field                | Type            | Required | Immutable |
|----------------------|-----------------|----------|-----------|
| `command_id`         | str             | yes      | yes       |
| `authorization_id`   | str             | yes      | yes       |
| `intent_id`          | str             | yes      | yes       |
| `content_fingerprint`| str             | yes      | yes       |
| `instrument`         | str             | yes      | yes       |
| `direction`          | str (LONG/SHORT)| yes      | yes       |
| `entry`              | Decimal \| None | yes1     | yes       |
| `stop`               | Decimal \| None | yes1     | yes       |
| `target`             | Decimal \| None | yes1     | yes       |
| `quantity`           | Decimal \| None | yes      | yes       |
| `planned_risk`       | Decimal \| None | yes      | yes       |
| `maximum_risk`       | Decimal \| None | yes      | yes       |
| `execution_mode`     | ExecutionMode   | yes      | yes       |
| `created_at`         | datetime        | yes      | yes       |
| `valid_from`         | datetime \| None| optional | yes       |
| `valid_until`        | datetime \| None| optional | yes       |
| `label`              | str             | default ""| yes      |
| `metadata`           | tuple[pairs]    | default ()| yes      |
| `version`            | int             | yes      | yes       |

1. Geometry fields are copied verbatim from the intent; `None` is permitted and
   means "not available" (never fabricated).

`__post_init__` validates: identity non-empty + prefix format, aware timestamps,
`valid_from >= created_at`, `valid_until > valid_from`, risk invariant
`planned_risk <= maximum_risk`, positive quantity, `version >= 1`.

### 5.3 Identity generation

`command_id = "cmd-" + sha256[:16]` computed over the CANONICAL command payload
(`_canonical_command_payload`), which contains exactly:

    authorization_id, intent_id, content_fingerprint, instrument, direction,
    entry, stop, target, quantity, planned_risk, maximum_risk, execution_mode

Operational metadata (timestamps, label, metadata) is EXCLUDED from identity.

### 5.4 Is command_id deterministic?

Yes. It depends only on the canonical payload (sorted JSON). No wall-clock,
no random UUID, no memory address, no process state. `_canonical_value`
normalizes Decimal (`Decimal("1.0")` == `Decimal("1")`), enums by type+name,
and type-tags scalars.

### 5.5 Can the same authorized intent deterministically produce the same command?

Yes — for identical input values. The factory input is the authorization record
+ intent record; the command's identity payload is a pure function of those,
plus the derived execution_mode. Two factory calls with the same authorization
and intent produce the same `command_id` and identical economic content.
(`created_at` differs only in operational, non-identity fields.)

### 5.6 Does the command contain sufficient information for downstream broker translation?

Yes, at the broker-neutral level: canonical instrument, direction, entry/stop/
target prices, quantity, planned/maximum risk, and execution mode. This is the
economic content an adapter needs. Broker-specific enrichment (symbol, exchange,
order type, product codes) is intentionally NOT present.

### 5.7 Does it contain broker-specific information that should not be there?

No. There is no broker symbol, exchange, segment, order type, product code,
account id, credentials, or broker terminology in the model. (Verified by grep +
code review in section 10.)

### 5.8 Does it contain trading semantics that belong upstream?

No. Direction is LONG/SHORT (not broker BUY/SELL). The existing decision
classification (QUALIFIED/PREFERRED), actionability, and evidence are NOT part
of the command (they remain on the intent). No probability/confidence/score.

### 5.9 Can the command safely cross the boundary without modification?

As an immutable input, yes. The adapter may read it. Any translation happens
inside the adapter. The command itself is never modified (frozen).

### Audit Area 1 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F01 | Execution Command is a clean, deterministic, broker-neutral snapshot of an authorized intent. | PASS |
| 17.1-F02 | command_id is deterministic and content-addressed; identical authorized input yields identical command_id. | PASS |
| 17.1-F03 | Command carries all broker-neutral economic content needed for translation; no broker-specific leakage. | PASS |
| 17.1-F04 | Command does not carry decision/actionability/evidence vocabulary upstream. | PASS |

## 6. Authorization Boundary Audit

### 6.1 Can commands only be created from authorized intent?

Yes, structurally. The ONLY production construction path is
`create_execution_command`, which:

1. Type-checks intent and authorization (TypeError otherwise).
2. Requires `authorization.status is AuthorizationStatus.AUTHORIZED`; every
   other status (UNAUTHORIZED, ELIGIBLE, EXPIRED, REVOKED, SUPERSEDED,
   unknown) raises ValueError -> no command.
3. Requires `authorization.intent_id == intent.intent_id` (else ValueError).
4. Requires `authorization.content_fingerprint == intent.content_fingerprint`
   (else ValueError).
5. Derives `execution_mode` from `authorization.scope` ("paper"/"live"); an
   unrecognized scope raises ValueError.

There is no secondary path that constructs an `ExecutionCommand`; the only
consumers are `execution_command_serialization.py` and `execution_command_store.py`
(persistence), which never create commands.

### 6.2 Is authorization state preserved correctly?

Yes. The command copies `authorization_id`, `intent_id`, and
`content_fingerprint` verbatim from the AUTHORIZED authorization. The
authorization record itself is never mutated. The factory re-verifies binding
at creation time (not just at authorization time), so the command is bound to
the exact authorization that produced it.

### 6.3 Could a broker adapter bypass authorization?

Not in the current architecture, because no adapter exists. When one is
introduced, it must consume commands produced ONLY by the factory (post
AUTHORIZED check). The adapter must NOT receive intents and self-authorize. The
commitment required for 17.2: the adapter contract accepts an `ExecutionCommand`
(which already encodes authorization) and never an un-authorized intent.

### 6.4 Should the broker adapter ever make authorization decisions?

No. Per Checkpoint 13.5 design: authentication (broker connection identity)
belongs to the adapter; authorization (whether this specific intent is
permitted) belongs to the Execution Authorization layer. The adapter must not
grant, deny, or re-derive authorization. It may verify the command's integrity
and mode binding for safety, but that is verification, not authorization.

### 6.5 Does authorization information need to be carried into broker submission?

Provenance must be carried so the audit trail can link a broker order back to
`command_id` -> `authorization_id` -> `intent_id` -> `plan_id`. The recommended
design (per 13.5) is that the adapter derives a `client_order_id` from
`command_id` and includes the binding identities as reference metadata on the
broker request, WITHOUT giving the broker any authorization authority. Broker
submission records (future layer) should reference `command_id` and
`authorization_id`.

### 6.6 Does existing fail-closed behavior remain intact once an adapter is introduced?

Yes, provided the adapter is purely downstream. The AUTHORIZED gate lives in the
factory; the adapter adds no decision layer. A broker adapter that only
translates already-authorized commands preserves fail-closed behavior. This
must be enforced by 17.2's adapter contract (command-only input, no
re-prompting, no downgrade path).

### Audit Area 2 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F05 | Commands are only creatable from an AUTHORIZED authorization with verified intent binding + fingerprint. | PASS |
| 17.1-F06 | Authorization state is preserved verbatim on the command; no path mutates the authorization. | PASS |
| 17.1-F07 | No bypass path exists today (zero consumers); the adapter must be a command consumer, not an intent consumer. | PASS |
| 17.1-F08 | Adapter must never make authorization decisions — requires explicit contract in 17.2. | CONCERN |

## 7. Persistence Boundary Audit

### 7.1 What is persisted?

`ExecutionCommandStore` persists the full deterministic JSON serialization of an
`ExecutionCommand` (schema_version + all model fields, lossless round-trip:
Decimal as string, datetime ISO, ExecutionMode by name, tuples preserved). One
file per command id: `<directory>/<command_id>.json`.

### 7.2 When is it persisted?

Only on an explicit `save()` call. Command creation does NOT auto-persist.
Persistence is a deliberate application-layer action. The store is stateless
across calls; identical inputs produce identical on-disk content.

### 7.3 Is persistence immutable?

Effectively yes. `save()` with identical content is idempotent (no-op success).
`save()` with DIFFERENT content for an existing id raises
`CommandIntegrityError` unless `overwrite=True`. `load()` verifies the schema
version BEFORE reconstruction and verifies `command.command_id == file-name id`.
Corrupted JSON / missing records / integrity failures surface typed exceptions
(`CommandStoreError`, `CommandNotFoundError`, `CommandIntegrityError`,
`UnsupportedCommandSchemaVersionError`). No silent error swallowing.

### 7.4 Is the persisted command an immutable historical record?

Yes. The persisted command is a point-in-time immutable record of command
creation. It is content-addressed; any modification changes `command_id` and
fails the load-time identity check.

### 7.5 Can persistence distinguish command creation from execution/submission?

No — and this is BY DESIGN. The store records only CREATED. There is no
submission state, no broker-order state, no execution-result state. This is a
deliberate boundary (Checkpoint 16.4: lifecycle ends at NOT_CREATED -> CREATED).
Execution/submission state must live in a SEPARATE lifecycle model (future
Broker Order / Execution Result layer), NOT on the immutable command.

### 7.6 Can the future adapter safely consume persisted commands?

Yes. `load()` returns a verified, reconstructed immutable command. An adapter
(or a restart-recovery process) can reload persisted commands after a restart
without recomputation. The store is the durable source of "commands that were
created."

### 7.7 What happens if persistence succeeds but broker submission fails?

The command remains recorded as CREATED. Submission failure is a NEW concern
that does not exist yet. The store provides the durable "work item"; a future
submission-state machine must record the attempt. Nothing is fabricated today
because submission does not exist.

### 7.8 What happens if broker submission succeeds but the response is lost?

This is the classic ambiguous-submission problem. The command record still says
CREATED; the system cannot know whether the broker accepted. The correct
behavior (per 13.5 retry-safety design) is RECONCILE BEFORE RETRY: query the
broker by the derived `client_order_id` / idempotency key before re-submitting.
This mechanism does not exist yet and is a 17.2+ requirement.

### 7.9 What happens if the process crashes between persistence and submission?

The atomic write guarantees the command is either fully persisted or not
present. On restart, the persisted command can be loaded. Because submission
state does not exist, there is nothing to recover yet; once a submission-state
machine exists, restart recovery must reload persisted commands and reconcile
with the broker before any retry.

### 7.10 What happens if the same command is dispatched more than once?

There is no dispatcher today, so nothing can be dispatched twice. The store
itself is idempotent (identical save is a no-op). The DUPLICATE-SUBMISSION risk
(broker-level) is NOT solved by persistence alone — a deterministic command_id
does not by itself guarantee the broker did not receive the order twice. This is
addressed in section 12.

### Audit Area 3 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F09 | Persistence is atomic, schema-versioned, lossless, tamper-evident, and fail-closed on corruption. | PASS |
| 17.1-F10 | Persisted command is an immutable historical record; the store does NOT treat it as an order-lifecycle object. | PASS |
| 17.1-F11 | Persistence distinguishes only CREATED; submission/execution state requires a separate lifecycle model (future). | CONCERN |
| 17.1-F12 | Crash-between-persist-and-submit and lost-response scenarios need a submission-state + reconciliation mechanism (future). | CONCERN |

## 8. Proposed Broker Adapter Boundary

### 8.1 What the Broker Adapter is

Based on the Checkpoint 13.5 design and the implemented command model, the
Broker Adapter should be a BROKER-SPECIFIC, STATELESS translation + transport
layer that:

* Consumes an `ExecutionCommand` (frozen, Checkpoint 16.2) as its ONLY business
  input.
* Translates the broker-neutral command into a broker-specific request
  (symbol/order-type/price/quantity formatting).
* Communicates with a specific broker via that broker's API.
* Normalizes broker responses/rejections/errors into broker-neutral domain
  results.
* Derives a deterministic `client_order_id` / idempotency key from `command_id`.

### 8.2 What the Broker Adapter is NOT

From the implemented boundaries: it is NOT a trading-decision layer, NOT an
authorization layer, NOT an order-lifecycle database, NOT a position/portfolio
manager, NOT a planner, and NOT part of the frozen Trading Intelligence core.
It does not exist yet and must be introduced as a pure downstream consumer.

### 8.3 Contract sketch (design input for 17.2, NOT implemented)

```
submit(command: ExecutionCommand) -> BrokerSubmissionResult
  - verify command integrity/identity (re-derive command_id? no — verify binding)
  - verify execution_mode against adapter mode
  - translate instrument -> broker symbol (fail closed on missing/ambiguous)
  - translate direction (LONG -> buy, SHORT -> sell)
  - translate entry/stop/target into broker order semantics (order types)
  - normalize price/quantity (tick/step, floor only; reject material change)
  - derive client_order_id from command_id (+ broker context)
  - send request; classify outcome
```

The exact contract is Checkpoint 17.2 work.

### 8.4 Boundary invariants (must hold in 17.2)

* Adapter never constructs an `ExecutionCommand` (only the factory can).
* Adapter never sees an un-authorized intent.
* Adapter never mutates the command / authorization / intent / plan.
* Economic meaning cannot change (quantity floor-only, price within half-tick,
  symbol never silently substituted).
* Mode mismatch (PAPER command -> LIVE adapter, or vice versa) fails closed.
* No broker response/fill/position data ever flows back into the command,
  authorization, intent, or TradePlan.

## 9. Responsibility Matrix

| Responsibility | Placement | Rationale |
|----------------|-----------|-----------|
| Entry/stop/target/quantity/risk definition | A. Upstream (TradePlan -> Intent) | Authoritative; adapter must not re-derive |
| Authorization of the intent | A. Upstream (Execution Authorization) | Adapter must not authorize |
| Command identity / binding / fingerprint | A. Upstream (ExecutionCommand factory) | Already implemented |
| Execution-mode (paper/live) definition | A. Upstream (authorization scope -> command) | Already implemented |
| Command persistence (CREATED record) | A. Upstream (ExecutionCommandStore) | Already implemented |
| Command -> broker request translation | C. Broker-specific implementation | Adapter-owned |
| Instrument/symbol mapping | C. Broker-specific implementation | Adapter-owned (e.g., Upstox key map) |
| Exchange/segment/product-type mapping | C. Broker-specific implementation | Adapter-owned |
| Order-type mapping (entry/stop/target -> broker order types) | C. Broker-specific implementation | Adapter-owned |
| Quantity conversion (lot/step; floor-only) | C. Broker-specific implementation (under neutral rules) | Must preserve risk invariant |
| Price representation (tick; half-tick tolerance) | C. Broker-specific implementation (under neutral rules) | Material change -> reject |
| Broker authentication / session | C. Broker-specific implementation (or connection manager) | Credentials never cross upstream |
| Broker API communication | C. Broker-specific implementation | Transport |
| Broker response normalization | C. Broker-specific implementation -> neutral result | Normalize to domain |
| Broker rejection normalization | C. Broker-specific implementation -> typed domain errors | See section 14 |
| Broker error normalization | C. Broker-specific implementation -> typed domain errors | See section 14 |
| Network failure handling | C. Broker-specific implementation (with neutral classification) | Timeout = ambiguous, reconcile |
| Authentication vs authorization distinction | B. Adapter contract (rule) | Contract-level rule, adapter owns authn |
| Idempotency key derivation (command_id -> client_order_id) | B. Adapter contract (rule) + C. implementation | Deterministic from command_id |
| Reconcile-before-retry | B. Adapter contract (rule) | Submit-state machine (future layer) |
| Submission/order lifecycle state | D. Outside (future Broker Order / Execution Result layer) | Never on the command |
| Position/portfolio state | D. Outside (future layer) | Never on the command |
| Account/portfolio management | D. Outside (future layer / out of scope) | Not in an adapter |

Placement key:

* A — Upstream of the adapter (already within the frozen architecture).
* B — Inside the broker-neutral adapter contract (rules/interface shared across
  brokers).
* C — Inside a broker-specific implementation.
* D — Outside the execution system entirely (or a future downstream layer).

## 10. Broker-Neutrality Audit

### 10.1 Search performed

Grep across `src/` for broker-specific concepts: `upstox`, `broker`, `order`,
`position`, `portfolio`, `symbol`, `exchange`, `order_type`, `product_code`,
`instrument_token`, `account_id`, `api_key`, `client_secret`, `session_token`,
`client_order_id`, `idempotency_key`, `SUBMITTED`, broker SDK imports
(`kiteconnect`, `upstox` SDK, `zerodha`), `place_order`, `submit_order`, base
URLs, and credentials.

### 10.2 Results

* **Core execution models** (`execution_command.py`, `execution_authorization.py`,
  `operational_trade_intent.py`): broker terms appear only in docstrings as
  explicit "NOT" statements. No broker fields, no broker terms in any field or
  enum value.
* **Persistence layer** (`execution_command_store.py`,
  `execution_command_serialization.py`, `exceptions.py`): no broker terms, no
  order lifecycle, no credentials.
* **Upstox integration**: exists ONLY as the historical data provider
  (`engine/data/historical_provider.py`, `UpstoxHistoricalDataProvider`), which
  fetches OHLCV candles via `UPSTOX_ANALYTICS_TOKEN`. It is a DATA provider,
  entirely within the historical-data layer. It has no execution capability, is
  not imported by any execution artifact, and its token is used only for
  historical data retrieval.
* **Yahoo integration**: data provider only.
* **Paper trading**: simulation lifecycle is intentionally separate and never
  referenced by the command model.

### 10.3 Verdict

**PASS — no broker-specific leakage into the core.** The Trading Intelligence
Engine, Trade Plan, Operational Trade Intent, Execution Authorization,
Execution Command, and command persistence are fully broker-neutral. No broker
SDK, order model, authentication assumption, symbol/exchange identifier,
product code, response model, or exception type from a broker appears anywhere
in the core execution chain.

The existing Upstox integration is a historical-market-data concern only, and
it is properly isolated behind the historical provider protocol.

### Audit Area 5 finding

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F13 | Zero broker-specific leakage in the core; Upstox exists only as an isolated historical-data provider. | PASS |

## 11. Execution Lifecycle Analysis

### 11.1 Which states exist today

The architecture today distinguishes, in separate frozen artifacts:

1. Execution intent created — `OperationalTradeIntent` (14.2).
2. Authorization granted — `ExecutionAuthorization` AUTHORIZED (15.2).
3. Execution command created — `ExecutionCommand` (16.2).
4. Execution command persisted — `ExecutionCommandStore.save` (16.5).

Everything after step 4 is deliberately absent.

### 11.2 Which states must exist in the future

The critical future distinction is submission status separated from the
immutable command:

* 5. Submission requested (an intent to send; owned by a future submit-state).
* 6. Broker request sent (transport).
* 7. Broker response received (transport/normalization).
* 8. Broker accepted.
* 9. Broker rejected.
* 10. Execution state unknown (ambiguous — the dangerous state).
* 11. Order partially filled.
* 12. Order filled.
* 13. Order cancelled.
* 14. Submission failed.

States 5-14 MUST NOT live on the `ExecutionCommand` model. The command is
frozen by design; mutating lifecycle would reopen Checkpoint 16. They belong to
a future Broker Order / Execution Result / submission-state layer that
REFERENCES `command_id` and `authorization_id`.

### 11.3 The timeout scenario

```
Execution Command persisted
    -> broker request submitted
    -> network timeout
    -> no broker response received
```

The system must NOT blindly assume the order failed and must NOT blindly retry.
Correct behavior: mark state ambiguous (UNKNOWN), then RECONCILE by querying the
broker for the order using the deterministic `client_order_id` derived from
`command_id`. Only after reconciliation may the state advance or retry occur.

### 11.4 The crash scenario

```
Execution Command persisted
    -> broker accepts order
    -> application crashes before recording acknowledgement
```

Required architectural mechanism (documented for 17.2+, NOT implemented): a
durable submission/order state record (separate from the command) that is
written with the same atomic discipline; on restart, load persisted commands +
submission records and reconcile with the broker (query by `client_order_id`)
before advancing to CONFIRMED or retrying. Fail-closed: if reconciliation
cannot determine the broker state, the entry remains in UNKNOWN and is never
blindly re-submitted.

### 11.5 Conclusion

The current ExecutionCommand must remain immutable (it already is). Broker
execution state requires a SEPARATE lifecycle/state model in a future layer.
The architecture correctly refuses to overload the command with lifecycle.

### Audit Area 6 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F14 | Command lifecycle correctly stops at CREATED; command remains immutable and is never treated as an order object. | PASS |
| 17.1-F15 | Submission/fill/cancel/unknown states require a separate future lifecycle model; none exists yet (by design). | CONCERN |
| 17.1-F16 | Timeout and crash-after-accept scenarios require reconcile-before-retry + durable submission state in the future layer. | CONCERN |

## 12. Idempotency and Duplicate-Submission Analysis

### 12.1 Can command_id be used as an idempotency identity?

Yes, as the BASE. `command_id` is deterministic and unique per authorized
economic content. A broker adapter must derive a `client_order_id` (or
idempotency key) from `command_id` (+ broker context) so that repeated attempts
for the SAME command use the SAME broker-side order id.

### 12.2 Can the same command accidentally be submitted twice?

Today there is no dispatcher, so no. In the future, duplicate submission can
happen through:
* restart / replay after a lost response;
* a process crash between persistence and submission, then recovery replaying
  the persisted command;
* user/orchestrator re-invocation of a submission routine against the same
  persisted command.

### 12.3 Does persistence currently provide sufficient protection?

Persistence provides:
* Durable, content-addressed command records (idempotent save, tamper-evident).
* A stable identity (`command_id`) from which a client_order_id can be derived.

Persistence does NOT provide:
* A record of "submitted / accepted / rejected / filled".
* Broker-level deduplication (only the broker can guarantee no duplicate order,
  when it honors a client-order-id / idempotency key).

So persistence alone is NOT sufficient protection against duplicate broker
submission.

### 12.4 Does the adapter contract need idempotency guarantees?

Yes. The adapter contract (17.2) must require:
* A deterministic `client_order_id` derived from `command_id` (+ broker context).
* Broker-level idempotency when the broker supports it (Upstox
  `client_order_id`).
* Reconcile-before-retry as the ONLY retry-on-unknown path.

### 12.5 Are broker-specific idempotency mechanisms required?

Yes, where the broker provides them (e.g., Upstox `client_order_id`). The generic
adapter contract defines the REQUIREMENT (derive a unique business id per
command); each broker implementation uses that broker's mechanism. A broker
without native idempotency requires query-by-`client_order_id` reconciliation
before ANY retry.

### 12.6 What happens after a timeout when submission status is unknown?

Command record: CREATED (unchanged — immutable). Submission state (future):
UNKNOWN. Required behavior: reconcile (query broker by `client_order_id`);
do NOT re-submit until reconciliation proves the order does not exist.

### 12.7 What happens after application restart?

Persisted commands can be reloaded. Any command with an unknown or in-progress
submission state must be reconciled with the broker before retry. Commands never
submitted can be submitted. This is future-layer work.

### 12.8 What happens if the same command is encountered again?

The store treats an identical save idempotently. A submission system that sees
the same `command_id` must route it to reconciliation (query broker) rather than
unconditionally re-submitting. If the broker confirms no order exists for the
client_order_id, re-submit; otherwise attach to the existing order.

### 12.9 What guarantees exist vs. required

Existing (guaranteed): deterministic command_id; idempotent command persistence;
immutable records; fail-closed load.

Required (17.2+): deterministic client_order_id derivation; submission-state
durability; reconcile-before-retry; broker-specific idempotency usage;
restart-recovery protocol. Deterministic command_id alone does NOT solve
broker-level duplicate submission.

### Audit Area 7 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F17 | command_id is a sound base for idempotency; persistence is idempotent at the store level. | PASS |
| 17.1-F18 | Broker-level duplicate-submission protection does not exist yet; requires client_order_id derivation + reconcile-before-retry + submission state. | CONCERN |
| 17.1-F19 | Timeout / lost-response / restart scenarios are unhandled today but architectural design from 13.5 covers the required behavior. | CONCERN |

## 13. Paper/Live Isolation Analysis

### 13.1 Current state

* `ExecutionMode` (PAPER / LIVE) is derived from the authorization `scope` and
  stamped on every `ExecutionCommand`. The caller cannot override it.
* Paper trading today exists ONLY as the PaperTrade simulation path
  (Product Phase 5/5-op), a sibling of TradePlan. It never interacts with
  execution commands.
* There is no live execution path, so no cross-contamination is possible today.

### 13.2 Required isolation rules (future)

The future adapter must enforce, per Checkpoint 13.5 and the command model:

* PAPER command -> PAPER adapter implementation (simulation-only, no real broker).
* PAPER command -> LIVE broker: FORBIDDEN (fail closed).
* LIVE command -> PAPER adapter: FORBIDDEN (fail closed; a live command must
  never be silently downgraded to simulation).
* LIVE command -> LIVE broker: permitted only with the LIVE authorization.

The mode check must be repeated at the adapter boundary (defense in depth), even
though the mode is already on the command.

### 13.3 Where isolation should exist

* The adapter contract (B) must REQUIRE mode verification for every submission.
* Each broker-specific adapter (C) must be constructed for exactly one mode
  (e.g., a paper adapter and a live adapter are distinct objects/configs) so a
  mode mismatch is structurally impossible, and additionally verified.
* The future submission-state layer must record the mode so restarts retain it.

### 13.4 Verdict

No silent bypass is possible today (no execution path). Introducing an adapter
cannot silently bypass paper/live safety if the 17.2 contract enforces
mode-specific adapters + boundary mode verification. The frozen model already
embeds the mode and prevents override.

### Audit Area 8 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F20 | ExecutionMode is on the command, derived from authorization, and non-overridable; no live path exists to contaminate. | PASS |
| 17.1-F21 | 17.2 must define mode-specific adapters + boundary mode verification to keep the paper/live isolation structurally safe. | CONCERN |

## 14. Error Handling Analysis

### 14.1 Required future error categories

The adapter must represent, at minimum: broker rejection, validation failure,
authentication failure, rate limit, network timeout, connection failure,
malformed broker response, broker unavailable, ambiguous submission result,
unsupported order type, unsupported instrument, insufficient funds/margin, and
other broker-specific failures.

### 14.2 Should errors be normalized into broker-neutral domain errors?

Yes. A broker-neutral submission-result / error vocabulary should sit in the
adapter CONTRACT (B), with broker implementations (C) mapping their native
errors onto it. The core domain (upstream) must never see broker terminology.

### 14.3 What must remain available for audit without leaking broker internals

* The immutable command_id / authorization_id / intent_id as correlation ids.
* A typed, stable error category (e.g., REJECTED, RATE_LIMITED, TIMEOUT,
  UNKNOWN, UNSUPPORTED_INSTRUMENT).
* A structured, broker-specific detail payload for debugging — stored in the
  future submission-state layer, NOT in the command or upstream artifacts.
* Credentials and raw tokens must never be logged or stored.

### 14.4 Classification within the contract

Per Checkpoint 13.5, the contract should classify: deterministic rejection
(invalid symbol/quantity/price/margin), broker capability failure (unsupported
order type/market closed), transient failure (network, rate limit -> retryable
with backoff), and ambiguous state (timeout, lost response -> reconcile, never
blind retry). Unsupported instrument/order type -> deterministic rejection.

### Audit Area 9 findings

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F22 | No error-handling vocabulary exists downstream yet (by design); the core upstream remains clean. | PASS |
| 17.1-F23 | 17.2 must define a broker-neutral error taxonomy + structured audit detail kept out of upstream artifacts. | CONCERN |

## 15. Dependency Direction Analysis

### 15.1 Verified current direction

The dependency graph of implemented execution artifacts:

```
models (intent) <- intelligence (intent engine) <- application
models (authorization) <- intelligence (authorization engine)
models (command) <- persistence (serialization/store)
```

The dashboard imports the intent application service to create intents (for
the POST /api/operational-trade-intent route) but does NOT import command or
authorization modules. Paper trading does not import command modules. No
execution artifact imports analysis/scanner/decision/paper-trade modules in
reverse.

### 15.2 Intended conceptual direction (must be preserved)

Trading Intelligence -> Trade Planning -> Execution Intent -> Authorization ->
Execution Command -> Broker-neutral execution boundary -> Broker-specific
adapter -> External broker.

The broker-specific implementation must NOT become a dependency of trading
intelligence, setup analysis, research, authorization, or command-generation
layers. The adapter must sit BELOW command persistence, importing only the
command model + contract types.

### 15.3 Risks and mitigations

* Risk: an adapter imported by the dashboard for "nice" surfacing — would
  create presentation->execution coupling. Mitigation: no dashboard route may
  call the adapter (matches checkpoint 16.4 "no dashboard integration" rule).
* Risk: broker SDK types leaking into the contract. Mitigation: contract uses
  only broker-neutral result types.
* Risk: adapter importing the authorization engine to "double check" — must be
  avoided; the command already encodes authorization.

### Audit Area 11 finding

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F24 | Dependency direction is clean today; 17.2 must keep the adapter strictly below command persistence and never let it become an upstream dependency. | PASS |

## 16. Testing Requirements

### 16.1 Required test families before a real broker adapter

1. Broker Adapter contract tests (interface behavior against a fake broker).
2. Fake broker tests (an in-memory broker implementing the contract).
3. Deterministic command tests (already exist: `tests/test_execution_command.py`, 65 tests).
4. Authorization boundary tests (already exist: engine + factory + store, 238 tests).
5. Persistence tests (already exist: `tests/test_execution_command_store.py`, 68 tests).
6. Duplicate submission tests (future).
7. Idempotency tests (future: same command -> same client_order_id; reconciler dedupes).
8. Timeout tests (future: ambiguous-state handling, reconcile-before-retry).
9. Ambiguous-response tests (future).
10. Rejection tests (future: broker rejection -> typed domain error).
11. Crash/restart tests (future: persisted command reload + reconcile).
12. Paper/live isolation tests (future: mode mismatch fails closed).
13. Broker-neutrality tests (future: contract contains no broker types).
14. Error normalization tests (future: native broker error -> neutral category).

### 16.2 Existing infrastructure that already supports these tests

* Frozen test suites for command identity, factory authorization-gating, and
  store persistence provide the upstream baseline (see Testing Report below).
* Established patterns for fake providers (e.g., `_FakeYahooBackend`,
  fake historical backends), atomic-store test helpers using `tmp_path`, and
  patched-to-raise guard tests (OutcomeEvaluator / pipeline) all transfer to a
  fake-broker test strategy.
* The repository test discipline (deterministic, no network) is directly
  applicable to fake-broker / adapter tests.
* The 13.5 audit already enumerated a 40-point future test contract for the
  adapter (section 39 of that document), which becomes the 17.2 test checklist.

### Audit Area 10 finding

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F25 | Upstream tests (command/authorization/persistence) are comprehensive; adapter/fake-broker/idempotency/recovery tests are future work with a ready-made contract from 13.5. | CONCERN |

## 17. Future Upstox Integration Considerations

### 17.1 Information a future Upstox adapter would require

Broker-neutral (from the command): instrument, direction, entry/stop/target,
quantity, planned/maximum risk, execution_mode, command_id, authorization_id.

Adapter-maintained (Upstox-specific): upstream API credentials (tokens),
instrument key (`NSE_EQ|...` ISIN / `NSE_INDEX|...`), exchange/segment
(NSE/BSE), product type (MIS/CNC/NRML), order type semantics (LMT/SL-LMT),
price/quantity constraints, the Upstox API endpoint + request formatting, and
the historical data providers (upstox, able, etc.).

### 17.2 Where each mapping belongs

* Symbol mapping: Upstox adapter (C).
* Exchange/segment: Upstox adapter (C).
* Order-type mapping: Upstox adapter (C).
* Price/quantity formatting: Upstox adapter (C) under neutral rules (tick,
  floor-only).
* Idempotency (client_order_id): Upstox adapter (C) implementing the contract
  rule (B).
* Product codes, margin semantics, response parsing: Upstox adapter (C).

### 17.3 Which broker capabilities may not map cleanly

* Upstox product/variety (MIS/CNC/NRML) and bracket/cover order types do not
  map 1:1 to entry/stop/target geometry; the adapter must translate using
  safe-normalization rules and reject what cannot be preserved.
* Upstox does not support all instrument types the canonical universe uses; the
  adapter must fail closed on unsupported instruments rather than substitute.
* Stop-trigger semantics differ between brokers; material translation must be
  rejected at contract level.

The existing Upstox HISTORICAL provider already maintains an instrument-key map
(`_default_upstox_instrument_key_map`) and token-handling discipline
(`UPSTOX_ANALYTICS_TOKEN`) that demonstrate the isolation pattern a future
Upstox EXECUTION adapter must follow.

### 17.4 Does the boundary allow another broker to be added without modifying core?

Yes. The command is broker-neutral; adding a second broker adapter requires only
a new broker-specific implementation of the 17.2 contract plus its mappings. No
core trading-intelligence, planning, authorization, or command code changes.

### Audit Area 12 finding

| # | Finding | Classification |
|---|---------|----------------|
| 17.1-F26 | The broker-neutral command boundary supports adding Upstox or another broker purely as a new adapter implementation without touching core layers. | PASS |

## 18. Findings

### 18.1 PASS

| # | Finding |
|---|---------|
| 17.1-F01 | Execution Command is a clean, deterministic, broker-neutral snapshot of an authorized intent. |
| 17.1-F02 | command_id is deterministic and content-addressed; identical authorized input yields identical command_id. |
| 17.1-F03 | Command carries all broker-neutral economic content needed for translation; no broker leakage. |
| 17.1-F04 | Command carries no decision/actionability/evidence vocabulary upstream. |
| 17.1-F05 | Commands only creatable from AUTHORIZED authorization with verified binding + fingerprint. |
| 17.1-F06 | Authorization state preserved verbatim; no path mutates the authorization. |
| 17.1-F07 | No bypass path exists (zero consumers); adapter must be a command consumer. |
| 17.1-F09 | Persistence is atomic, schema-versioned, lossless, tamper-evident, fail-closed on corruption. |
| 17.1-F10 | Persisted command is immutable; store never treats it as an order-lifecycle object. |
| 17.1-F13 | Zero broker-specific leakage; Upstox exists only as an isolated historical-data provider. |
| 17.1-F14 | Command lifecycle correctly stops at CREATED; command remains immutable. |
| 17.1-F17 | command_id is a sound idempotency base; store-level persistence is idempotent. |
| 17.1-F20 | ExecutionMode on command, derived from authorization, non-overridable; no live path exists. |
| 17.1-F22 | No downstream error vocabulary exists yet; core upstream stays clean. |
| 17.1-F24 | Dependency direction clean; 17.2 must keep adapter strictly below command persistence. |
| 17.1-F26 | Broker-neutral boundary supports adding any broker as a new adapter implementation. |

### 18.2 CONCERN (none are blockers to proceeding with the audit conclusion)

| # | Finding | Where it must be addressed |
|---|---------|----------------------------|
| 17.1-F08 | Adapter must never make authorization decisions. | 17.2 contract |
| 17.1-F11 | Persistence distinguishes only CREATED; submission state needs a separate lifecycle model. | 17.2 design / future layer |
| 17.1-F12 | Crash-between-persist-and-submit and lost-response need submission-state + reconciliation. | 17.2 design / future layer |
| 17.1-F15 | Submission/fill/unknown states require a separate future lifecycle model. | 17.2 design / future layer |
| 17.1-F16 | Timeout and crash-after-accept require reconcile-before-retry + durable submission state. | 17.2 design / future layer |
| 17.1-F18 | Broker-level duplicate-submission protection not yet present. | 17.2 contract |
| 17.1-F19 | Timeout/lost-response/restart scenarios are future-layer work (13.5 design exists). | 17.2+ |
| 17.1-F21 | 17.2 must define mode-specific adapters + boundary mode verification. | 17.2 contract |
| 17.1-F23 | 17.2 must define a broker-neutral error taxonomy + audit detail keystone. | 17.2 contract |
| 17.1-F25 | Adapter/fake-broker/idempotency/recovery tests are future work. | 17.2 tests |

### 18.3 BLOCKER

None. No finding prevents the safe introduction of the Broker Adapter boundary.
The items classified CONCERN are design decisions / explicit contracts / future
work that Checkpoint 17.2 is the correct place to specify — none require
reopening frozen Checkpoints 10-16, and none represent an existing defect in
the implemented architecture.

## 19. Required Prerequisites

For Checkpoint 17.2 (design of the Broker Adapter boundary), the following
prerequisites are already satisfied by the frozen architecture:

1. Immutable broker-neutral ExecutionCommand + deterministic command_id: DONE.
2. Authorization-AUTHORIZED-only command creation: DONE.
3. Atomic, idempotent, tamper-evident command persistence: DONE.
4. ExecutionMode (paper/live) bound to command: DONE.
5. Broker-neutral core (zero leakage): DONE.
6. Clean dependency direction: DONE.

Prerequisites that must be DELIVERED by 17.2 (design) or a later checkpoint
(implementation):

7. Adapter contract (command-only input, mode checking, no authorization
   authority).
8. Broker-neutral result/error taxonomy.
9. client_order_id derivation rule from command_id.
10. Reconcile-before-retry policy for timeout/ambiguous/lost-response.
11. Mode-specific adapter construction rule.
12. Submission-state and order-lifecycle model (separate from the command).
13. Fake-broker test harness + adapter test contract (derive from 13.5 section 39).

## 20. Recommendation for Checkpoint 17.2

**Broker Adapter CONTRACT DESIGN** (audit + design, additive, no broker
integration), followed by the minimal REQUIRED ARCHITECTURAL REMEDIATION that
the contract exposes, in order:

1. **Define the broker-neutral adapter contract** — `submit(command)`,
   `reconcile(command_id, client_order_id)`, `cancel(...)` (if scoped), typed
   result/error taxonomy, mode-specific adapter construction, and the
   no-authorization-authority rule. Do NOT implement any broker.
2. **Define the separate submission / order-lifecycle model** — states
   CREATED / SUBMITTING / SUBMITTED / ACCEPTED / REJECTED / UNKNOWN / PARTIALLY
   FILLED / FILLED / CANCELLED, persisted separately from (and referencing) the
   command. Design only, per the family of CONCERN findings (F11, F12, F15, F16).
3. **Define client_order_id derivation + reconcile-before-retry + restart
   recovery protocol** (addresses F18, F19).
4. **Define the fake-broker test contract and the broker-neutrality /
   error-normalization test matrix** (addresses F21, F23, F25).

Checkpoint 17.2 should NOT implement a real Upstox adapter, add broker SDKs, or
connect to any broker. If Checkpoint 17.2 determines that any remediation must
touch frozen layers (it should not require this), it must document the specific
change for a separate decision before implementation.

## 21. Final Verdict

**PASS**

The primary audit question — "Is the Trading Intelligence Engine ready to
introduce a broker-neutral Broker Adapter boundary without contaminating the
frozen Checkpoints 10-16 architecture?" — is answered **YES**.

The Execution Command contract, authorization integration, persistence
boundary, broker neutrality, execution-mode binding, and dependency direction
are all clean and provide a sound, safe foundation for designing a broker-
neutral Broker Adapter boundary. The CONCERN findings are all about work that
belongs to Checkpoint 17.2 or later (adapter contract details, separate
submission-state model, idempotency/reconciliation protocols, error taxonomy,
fake-broker tests) — none require reopening or modifying the frozen
architecture, and none are existing defects. The limitations are design-time
requirements for 17.2, not blockers.

## 22. Testing Report

### 22.1 Test commands executed

```
pip install -q pytest fastapi uvicorn jinja2 python-multipart httpx
python -m pytest tests/test_execution_command.py tests/test_execution_command_store.py \
    tests/test_execution_authorization.py tests/test_execution_authorization_engine.py \
    tests/test_execution_authorization_store.py tests/test_operational_trade_intent.py \
    tests/test_operational_trade_intent_engine.py tests/test_operational_trade_intent_application.py -q
   -> 627 passed

python -m pytest tests/test_trade_planning.py tests/test_paper_trading.py \
    tests/test_paper_trading_operations.py tests/test_run_paper_trading_cycle.py \
    tests/test_live_paper_validation.py -q
   -> 488 passed (2 StarletteDeprecation warnings, pre-existing)

python -m pytest tests/test_operational_trade_intent_application.py tests/test_dashboard.py \
    tests/test_workstation.py tests/test_watchlist_scanner.py -q
   -> 326 passed (2 warnings, pre-existing)

python -m pytest tests/test_execution_command.py tests/test_execution_command_store.py \
    tests/test_execution_authorization.py tests/test_execution_authorization_engine.py \
    tests/test_execution_authorization_store.py tests/test_operational_trade_intent.py \
    tests/test_operational_trade_intent_engine.py tests/test_operational_trade_intent_application.py \
    tests/test_trade_planning.py tests/test_paper_trading.py \
    tests/test_paper_trading_operations.py tests/test_run_paper_trading_cycle.py \
    tests/test_live_paper_validation.py tests/test_dashboard.py \
    tests/test_workstation.py tests/test_watchlist_scanner.py -q
   -> 1383 passed (consolidated relevant suites, 2 warnings)

python -m pytest tests/ -q
   -> 5479 passed, 2 failed, 2 warnings
```

### 22.2 Failures

The 2 full-suite failures are `tests/test_live_data_integration.py`:
`TestProviderFailure::test_yahoo_not_ready_when_no_backend` and
`TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`.
Both fail with `ImportError: No module named 'yfinance'`.

These are PRE-EXISTING, environment-only failures — the optional `yfinance`
/`pandas` dependency (declared in `requirements.txt` as optional for the live
Yahoo provider) is not installed in this sandbox. The AGENTS.md baselines
document "2 pre-existing yfinance-related failures" for the post-13.x suite
(4849 passed, 2 pre-existing failures, 3 skipped, and the same in 14.x/16.x
baselines). They are unrelated to this audit: neither test touches Execution
Command, authorization, or persistence. No execution-relevant test failed.

### 22.3 Skips

None of the execution-relevant suites had skipped tests. The full-suite run
reported 0 skipped in this environment (the pre-existing baselines recorded
3 skipped elsewhere).

### 22.4 Test count breakdown (focused suites)

| Suite | Tests |
|-------|-------|
| tests/test_execution_command.py | 65 |
| tests/test_execution_command_store.py | 68 |
| tests/test_execution_authorization.py | 97 |
| tests/test_execution_authorization_engine.py | 84 |
| tests/test_execution_authorization_store.py | 57 |
| tests/test_operational_trade_intent.py | 125 |
| tests/test_operational_trade_intent_engine.py | 69 |
| tests/test_operational_trade_intent_application.py | 58 |
| tests/test_trade_planning.py | 155 |
| tests/test_paper_trading.py | 111 |
| tests/test_paper_trading_operations.py | 78 |
| tests/test_run_paper_trading_cycle.py | 32 |
| tests/test_live_paper_validation.py | 99 |
| tests/test_dashboard.py | 98 |
| tests/test_workstation.py | 95 |
| tests/test_watchlist_scanner.py | 75 |

No tests were modified during this checkpoint.

## 23. Repository Files Inspected

Source:
- `src/engine/models/operational_trade_intent.py`
- `src/engine/models/execution_authorization.py`
- `src/engine/models/execution_command.py`
- `src/engine/intelligence/operational_trade_intent.py`
- `src/engine/intelligence/operational_trade_intent_application.py`
- `src/engine/intelligence/execution_authorization.py`
- `src/engine/persistence/execution_command_store.py`
- `src/engine/persistence/execution_command_serialization.py`
- `src/engine/persistence/exceptions.py`
- `src/dashboard/app.py`, `src/dashboard/services.py`, `src/dashboard/views.py`
- `src/dashboard/paper_trade_operations.py`, `src/dashboard/live_validation.py`
- `src/engine/data/historical_provider.py` (Upstox/Yahoo historical providers)
- `src/engine/models/paper_trade.py`, `src/engine/models/trade_plan.py`

Tests:
- `tests/test_execution_command.py`, `tests/test_execution_command_store.py`
- `tests/test_execution_authorization.py`, `tests/test_execution_authorization_engine.py`,
  `tests/test_execution_authorization_store.py`
- `tests/test_operational_trade_intent.py`, `tests/test_operational_trade_intent_engine.py`,
  `tests/test_operational_trade_intent_application.py`
- `tests/test_trade_planning.py`, `tests/test_paper_trading.py`,
  `tests/test_paper_trading_operations.py`, `tests/test_run_paper_trading_cycle.py`,
  `tests/test_live_paper_validation.py`
- `tests/test_dashboard.py`, `tests/test_workstation.py`, `tests/test_watchlist_scanner.py`
- `tests/test_live_data_integration.py` (failure diagnosis)

Prior audit/design docs:
- `docs/checkpoint_13_5_execution_command_to_broker_adapter_boundary_audit.md`
- `docs/checkpoint_16_1..16_5` (command boundary/model/factory/persistence)
- `docs/checkpoint_13_3..13_6`, `docs/checkpoint_14_1..14_6`, `docs/checkpoint_15_1..15_6`

## 24. Code Changes

**None.** This checkpoint made zero code changes. Only this audit document was
created. No frozen Checkpoints 10-16 file was modified. Frozen boundaries
remain intact. No live broker execution was introduced.