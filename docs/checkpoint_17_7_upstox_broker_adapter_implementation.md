# CHECKPOINT 17.7 — UPSTOX BROKER ADAPTER IMPLEMENTATION

Status: COMPLETE / PASS

## 1. Checkpoint 17.7 Overview

Checkpoint 17.7 implements the FIRST concrete broker-specific adapter —
`UpstoxBrokerAdapter` — against the already-frozen `BrokerAdapter` contract
(Checkpoint 17.2) using the architecture approval from Checkpoint 17.6
(REAL BROKER ADAPTER DESIGN). The adapter is implemented ENTIRELY against a
MOCKED / INJECTED broker client. It proves that the designed Upstox adapter
correctly implements the frozen broker-neutral architecture and safety
contracts BEFORE any real broker connectivity is considered.

This checkpoint is EXPLICITLY AUTHORIZED.

## 2. Objective

Implement `UpstoxBrokerAdapter` per the 17.6 design such that it:

* conforms to the frozen generic `BrokerAdapter` protocol
* keeps all Upstox-specific models/errors/translation inside the adapter
  boundary
* implements request translation, instrument / order-type / product /
  validity / exchange / capability / client-order-id / idempotency /
  reconciliation / cancellation / error / state mapping
* binds a single `ExecutionMode` (PAPER or LIVE) with no fallback
* treats a rate-limited or timed-out submission as AMBIGUOUS -> UNKNOWN ->
  reconcile-before-retry (never a false failure, never a blind retry)
* integrates with the frozen `SubmissionInfrastructure` end-to-end against a
  mocked client only
* contains NO network code, NO Upstox SDK, NO real credentials, NO real
  orders

## 3. Scope

IMPLEMENTED IN 17.7:

* `UpstoxBrokerRequest` / `UpstoxOrderData` / `UpstoxPlaceOrderResponse` /
  `UpstoxOrderStateResponse` / `UpstoxCancelResponse` / `UpstoxClientFailure`
  (adapter-owned, in `src/engine/intelligence/upstox_broker_models.py`)
* `UpstoxTransactionType` / `UpstoxOrderType` / `UpstoxProduct` /
  `UpstoxValidity` / `UpstoxExchange` / `UpstoxOrderState` / `UpstoxErrorKind`
  (adapter-owned enums)
* credential-provider boundary (`upstox_credential_provider.py`) — protocol +
  deterministic test providers; does NOT load real credentials
* broker-client boundary (`upstox_broker_client.py`) — `UpstoxBrokerClient`
  Protocol + `MockUpstoxBrokerClient` (deterministic, in-memory, network-free)
  + `redact_sensitive` helper
* concrete `UpstoxBrokerAdapter` (`upstox_broker_adapter.py`) with
  `paper_upstox_adapter` / `live_upstox_adapter` factories
* the full 26-scenario failure matrix
* generic contract-conformance suite (reused from 17.4)
* mirror-mode `submitted` scenario for the duplicate-prevention guard
* documentation + AGENTS.md append

MOCKED / SIMULATED:

* the broker client (in-memory mock injected into the adapter)

DEFERRED TO FUTURE CHECKPOINT:

* any real HTTP `UpstoxBrokerClient` implementation
* real Upstox connectivity
* real credentials / auth handshake
* real order submission / cancellation / reconciliation
* broker-side idempotency verification

NOT IMPLEMENTED:

* no Upstox SDK, no network, no credentials, no live order

## 4. Non-Goals

* NO real broker connectivity
* NO Upstox SDK usage
* NO network I/O of any kind
* NO real credentials / tokens / API keys
* NO live order submission / cancellation / reconciliation
* NO new trading / risk / strategy logic
* NO modification of frozen Checkpoints 10–16

## 5. Frozen Constraints

* Checkpoints 10–16 are FROZEN. Do not reopen/refactor/redesign/modify.
* `ExecutionCommand` remains immutable and broker-neutral.
* Authorization remains strictly upstream (`BrokerAdapter` has zero
  authorization authority).
* `SubmissionLifecycle` remains separate and references `command_id` only.
* `client_order_id` remains deterministic.
* Blind retry remains prohibited.
* broker idempotency is not falsely claimed.
* paper/live mode cannot silently cross.
* the generic `BrokerAdapter` contract remains reusable.

## 6. 17.1–17.6 Baseline

* 17.1 Broker Adapter Boundary Architecture Audit — PASS
* 17.2 Broker Adapter Contract & Execution Lifecycle Contract — PASS
* 17.3 Broker Adapter Infrastructure — PASS
* 17.4 First Concrete Broker Adapter / Reference Adapter — PASS
* 17.5 Real Broker Integration Preparation & Boundary Safety Audit —
  PASS WITH LIMITATIONS
* 17.6 Real Broker Adapter Design — PASS
  (design doc approved; ZERO production-code changes added)

Baseline suite: 5823 passed / 0 failed / 2 warnings.

## 7. Implementation Architecture

```
ExecutionCommand (frozen, broker-neutral)
    -> SubmissionInfrastructure (frozen 17.3)
    -> SubmissionLifecycle (frozen 17.2)
    -> BrokerAdapter (frozen 17.2 protocol)
    -> UpstoxBrokerAdapter (17.7, translation boundary)
        -> UpstoxBrokerClient Protocol (17.7)
        -> MockUpstoxBrokerClient (injected, network-free)
    -> AdapterResult (frozen, broker-neutral)
    -> SubmissionLifecycle update
    -> SubmissionLifecycleStore persistence
```

## 8. Module Structure

| Module | Purpose |
| ------ | ------- |
| `src/engine/intelligence/upstox_broker_models.py` | Adapter-owned Upstox request/response/error models + enums (stdlib only). |
| `src/engine/intelligence/upstox_credential_provider.py` | Credential-boundary protocol + deterministic test providers; no real credential loading. |
| `src/engine/intelligence/upstox_broker_client.py` | `UpstoxBrokerClient` Protocol + `MockUpstoxBrokerClient` (in-memory, network-free) + `redact_sensitive`. |
| `src/engine/intelligence/upstox_broker_adapter.py` | Concrete `UpstoxBrokerAdapter` implementing the frozen `BrokerAdapter`: translation, mapping, normalization, fail-closed mode binding; `paper_upstox_adapter` / `live_upstox_adapter` factories. |
| `tests/test_checkpoint_17_7_upstox_adapter.py` | Generic conformance (reused 17.4 suite) + request/instrument/order-type/product/validity/exchange/capability/tag/error/state/response-validation/cancellation/reconcile tests. |
| `tests/test_checkpoint_17_7_upstox_client.py` | Client boundary tests + 26-scenario failure matrix + no-network + credential-leakage + redaction. |
| `tests/test_checkpoint_17_7_upstox_integration.py` | End-to-end `SubmissionInfrastructure` integration, restart/recovery, mode binding, broker-neutrality, dependency-direction. |
| `docs/checkpoint_17_7_upstox_broker_adapter_implementation.md` | This document. |
| `AGENTS.md` | Checkpoint 17.7 entry appended. |

## 9. Upstox Models

All broker-specific models are frozen+slots dataclasses / enums in
`upstox_broker_models.py`:

* `UpstoxTransactionType` (BUY / SELL), `UpstoxOrderType` (LIMIT / MARKET /
  SL / SL_MARKET / UNKNOWN), `UpstoxProduct` (D / M / I / C / UNKNOWN),
  `UpstoxValidity` (DAY / IOC / UNKNOWN), `UpstoxExchange` (NSE / BSE),
  `UpstoxOrderState` (OPEN / ACCEPTED / COMPLETE / CANCELLED / REJECTED /
  PARTIALLY_FILLED / UNKNOWN), `UpstoxErrorKind` (adapter-internal failure
  classification), `UpstoxErrorCode` (Upstox-documented error vocabulary).
* `UpstoxBrokerRequest` — the adapter-owned place-order request carrying
  instrument_token, transaction_type, quantity, product, validity, order_type,
  price, trigger_price, tag, client_order_id, idempotency_key, execution_mode,
  created_at. `__post_init__` validates structure (no negative quantity, no
  instrument mismatch).
* `UpstoxOrderData` — single/multi order-id payload with a `single_id`
  property.
* `UpstoxPlaceOrderResponse` — success/error envelope (`status`,
  `order_data`, `error_code`, `error_message`); `is_success`/
  `is_error` properties.
* `UpstoxOrderStateResponse` — reconciliation response (order_id, tag,
  status, reason).
* `UpstoxCancelResponse` — cancellation response.
* `UpstoxClientFailure` — adapter-client failure classification (kind,
  message).

## 10. Client Boundary

`UpstoxBrokerClient` (Protocol) exposes:

* `place_order(request) -> UpstoxPlaceOrderResponse | UpstoxClientFailure`
* `get_order(tag, order_id=None) -> UpstoxOrderStateResponse | UpstoxClientFailure`
* `cancel_order(order_id) -> UpstoxCancelResponse | UpstoxClientFailure`
* `check_health() -> bool`

The Protocol itself performs no network operations. The implementation used
by 17.7 tests is `MockUpstoxBrokerClient` — a deterministic, in-memory,
network-free fake with injectable scenarios. A future real HTTP client
belongs to a later checkpoint.

## 11. Credential Boundary

* `UpstoxCredentialProvider` (Protocol): `get_access_token() -> str | None`.
* `EnvironmentUpstoxCredentialProvider` reads ONLY the (future) execution
  access-token env var `UPSTOX_EXECUTION_ACCESS_TOKEN`; the historical-data
  analytics token is deliberately never read by execution code.
* `StaticUpstoxCredentialProvider` / `EmptyUpstoxCredentialProvider` —
  deterministic test providers.
* Credentials NEVER enter ExecutionCommand / TradePlan / Intent /
  SubmissionLifecycle / AdapterResult / BrokerError / persistence / logs /
  audit / fixtures.
* Missing / invalid credentials -> FAIL CLOSED
  (`AUTHENTICATION_FAILURE`, TRANSPORT, never a false success).
* No real credential loading is introduced in 17.7.

## 12. Request Translation

`ExecutionCommand -> UpstoxBrokerRequest`:

| Command field | Translation | Rule |
| ------------- | ----------- | ---- |
| instrument | instrument_token | verified map; unknown -> ValueError (fail closed) |
| direction (LONG/SHORT) | transaction_type (BUY/SELL) | 1:1 deterministic |
| quantity | quantity | Decimal, floored to positive integer, NEVER increased; 0 after floor -> failure |
| entry | price | LIMIT price, verbatim, positive |
| stop (below entry for LONG / above for SHORT) | trigger_price | verbatim when present; invalid geometry -> ValueError |
| (target) | NOT transmitted | documented loss; never fabricated |
| order type | order_type | LIMIT (default); MARKET/SL supported through capability gate; unknown -> fail closed |
| product | product | verified D/M/I/C; unknown -> fail closed |
| validity | validity | DAY (first scope); unknown -> fail closed |
|exchange|NSE via the verified token prefix|unknown prefix -> fail closed|
|client_order_id|tag + client_order_id|deterministic, restart-stable, bounded|
|execution_mode|execution_mode|recorded, never overridden|

Unsupported semantics fail closed: no silent discard of a field, no
order-type/product/exchange/instrument substitution, no quantity increase,
no price/trigger change.

## 13. Instrument Mapping

Verified map (mirrors the isolated historical-provider instrument map):

| Canonical | Token |
| --------- | ----- |
| RELIANCE | NSE_EQ|INE002A01018 |
| TCS | NSE_EQ|INE467B01029 |
| HDFCBANK | NSE_EQ|INE040A01034 |
| ICICIBANK | NSE_EQ|INE090A01021 |
| NIFTY | NSE_INDEX|Nifty 50 |

Custom maps are configurable via `UpstoxBrokerConfig.instrument_key_map`.
Unknown / missing / ambiguous -> fail closed (never a guessed instrument).

## 14. Order-Type Mapping

Supported generic order types on the command are translated to Upstox
`order_type`:

* LIMIT (entry present) -> `LIMIT`.
* MARKET (entry absent/unsupported semantic) -> capability-gated; unknown
  semantics fail closed.
* STOP / SL trigger-based variants -> DESIGNED; actual SL/SL-M advice-
  semantics REQUIRE IMPLEMENTATION-TIME VERIFICATION and the capability gate
  keeps the adapter from claiming support it cannot prove.

Every unsupported semantic -> normalized broker-neutral validation error
(`UnsupportedOrderSemantics`).

## 15-17. Product / Validity / Exchange Mapping

Explicit 1:1 mappings (no default fallback, no silent substitution):

* PRODUCT: D (delivery) is the first-scope default; M/I/C are mappable but
  capability-gated.
* VALIDITY: DAY is the first-scope default; IOC is recognized.
* EXCHANGE: derived from the instrument-token prefix (NSE_EQ / NSE_INDEX);
  unknown prefixes fail closed.

## 18. Capability Mapping

`AdapterCapabilities` advertise SUBMIT + RECONCILE + CANCEL (all three are
implemented and verified). `supports(command)` returns False (never raises)
for unmapped instruments / invalid geometry / non-commands. `check(command)`
raises `ValueError` for unsupported semantics (fail closed). Unadvertised
capabilities raise at call time. Capabilities are configurable
(`capabilities=(...)`) so a narrower adapter can be declared.

## 19. Client Order ID

`client_order_id = derive_client_order_id(command_id, broker_context="default")`
— deterministic, restart-stable, `co-` prefixed. The adapter uses the SAME
default broker context as the frozen infrastructure so the lifecycle identity
and the request identity match (reconciliation + idempotency depend on this).
The Upstox `tag = "uptag-" + sha256(client_order_id)[:12]` is a bounded,
collision-safe encoding of the client_order_id (Upstox tag length limits; the
16-hex client_order_id is deterministic and never truncated for identity —
only the *display/key* tag is sha256-bounded; the full client_order_id is
carried on the request).

## 20. Idempotency

The four-way distinction is preserved:

1. `command_id` (core, immutable)
2. `client_order_id` (application-level, deterministic, restart-stable)
3. `idempotency_key` (derived, local duplicate-detection)
4. `broker_order_id` (broker-generated, downstream-only)

The adapter NEVER claims broker-side idempotency for `tag`/`client_order_id`.
A broker duplicate response is normalized safely (accept-with-order-id).
Ambiguous broker behavior -> UNKNOWN.

## 21. Submission Flow

`submit(command)`:

1. `_validate_command` (type guard; `ExecutionCommand` required)
2. `validate_adapter_mode` (mode binding; mismatch -> fail closed)
3. `check(command)` (capability + instrument + order semantics; fail closed)
4. deterministic client_order_id + tag
5. `_translate_command` -> `UpstoxBrokerRequest`
6. `self.client.place_order(request)` (mocked)
7. `_normalize_place_order_response` -> `AdapterResult`
8. unexpected exceptions -> `normalize_unexpected_exception` (never a leak)

The adapter does NOT authorize commands; it accepts only already-authorized
`ExecutionCommand` objects.

## 22. Response Validation

* HTTP/envelope "success" is NOT order success: only a confirmed success
  envelope with a SINGLE non-empty order id is a confirmed outcome.
* Multi-order-id -> MALFORMED_RESPONSE -> UNKNOWN.
* Error envelope -> mapped to the broker-neutral taxonomy; unknown codes ->
  deterministic rejection-heuristic OR UNKNOWN_OUTCOME (never a guessed
  outcome).
* Malformed client failures -> `normalize_client_failure` (ambiguous kinds ->
  UNKNOWN).

## 23. Order-State Mapping

| Upstox state | BrokerResultStatus |
| ------------ | ------------------ |
| OPEN | SUBMITTED |
| ACCEPTED | ACCEPTED |
| COMPLETE | FILLED |
| CANCELLED | CANCELLED |
| REJECTED | REJECTED |
| PARTIALLY_FILLED | PARTIALLY_FILLED |
| UNKNOWN / unrecognized | UNKNOWN (reconcile) |

Never a forced unsafe mapping.

## 24. Error Mapping

Documented Upstox error codes map to broker-neutral codes:

| Upstox code | Meaning | Broker-neutral |
| ----------- | ------- | -------------- |
| UDAPI1004 / UDAPI1056 / UDAPI1158 | order-type issues | UNSUPPORTED_ORDER_SEMANTICS |
| UDAPI1007 / UDAPI1055 / UDAPI1008 / UDAPI1036 / UDAPI1003 | validation | VALIDATION_FAILURE |
| UDAPI1154 / UDAPI100041 | static-IP / finalized-order | BROKER_REJECTION |

Broker-confirmed rejection (documented code OR rejection-hint text on an
unknown code) -> REJECTED (terminal, broker-confirmed). Deterministic
validation failures -> FAILED with matching code. Unknown error codes ->
UNKNOWN (unknown-outcome) unless a rejection hint matches. Unknown broker
errors never escape into the core; they always normalize safely.

## 25. Timeout Handling

`UpstoxClientFailure(TIMEOUT)` -> AMBIGUOUS -> `AdapterResult.UNKNOWN` with
`BrokerErrorCode.TIMEOUT`. Never a false FAILED, never an automatic submit
retry.

## 26. UNKNOWN Handling

UNKNOWN results require reconciliation. The infrastructure refuses a blind
resubmission of an UNKNOWN submission (`ReconciliationRequiredError`).
UNKNOWN survives restart (`RECONCILE_REQUIRED` recovery action).

## 27. Reconciliation

`reconcile(client_order_id)` -> Upstox tag (primary) -> `get_order(tag,
order_id)` (fallback). Exact match -> confirmed state; no match / multiple
matches / malformed / conflicting -> UNKNOWN; broker unavailable ->
BROKER_UNAVAILABLE (FAILED). Everything returned to the core is
`AdapterResult`; no Upstox response objects cross the boundary.

## 28. Cancellation

`cancel(client_order_id)` -> supported only when CANCEL is advertised ->
`cancel_order(order_id)` on the mock -> normalized: success -> CANCELLED;
already-cancelled -> CANCELLED; race-with-fill -> REJECTED (the fill is
authoritative; a false cancellation is never produced); timeout/unknown
outcome -> UNKNOWN (reconcile).

## 29. Rate-Limit Handling

A rate-limited SUBMISSION is NOT proof the order was not accepted -> UNKNOWN
(`RATE_LIMIT`) -> reconcile. Safe retries are GET/reconcile operations only;
blind submission retry is prohibited.

## 30. Paper/Live Isolation

* `paper_upstox_adapter` is bound to `ExecutionMode.PAPER`; `live_upstox_adapter`
  to `ExecutionMode.LIVE`.
* `validate_adapter_mode` is called before every operation; mismatch -> fail
  closed.
* `select_adapter` fails closed when the registry contains no mode-matched
  adapter; `cp17_mode` binding is recorded on the lifecycle and verified on
  reconcile.
* NO LIVE -> PAPER and NO PAPER -> LIVE fallback.

## 31. SubmissionInfrastructure Integration

The full flow is proven in the integration tests:

```
Authorized ExecutionCommand
    -> SubmissionInfrastructure
    -> SubmissionLifecycle
    -> UpstoxBrokerAdapter
    -> MockUpstoxBrokerClient
    -> AdapterResult
    -> SubmissionLifecycle update + persistence
```

Includes UNKNOWN persistence + restart recovery, reconciliation, duplicate
guard, terminal-state no-resubmit, mode binding, deterministic identity, and
broker-neutrality of the persisted lifecycle.

## 32. Persistence Integration

No broker state is moved into `ExecutionCommand`. No broker-specific fields
are added to broker-neutral models. The SubmissionLifecycle persists through
the frozen store (submission state, UNKNOWN, recovery, reconciliation,
duplicate detection, terminal no-bind-resubmit).

## 33. Auditability

Auditable identity chain: command_id / client_order_id / tag / adapter
identity / broker identity (UPSTOX_BROKER_IDENTITY) / execution mode /
lifecycle state / normalized result / normalized error / reconciliation
event / recovery action / timestamps (caller-supplied, timezone-aware).
Credentials are NEVER recorded.

## 34. Test Architecture

Three layers:

* A. GENERIC conformance — reused `BrokerAdapterContractConformanceBase`
  (17.4) run against UpstoxBrokerAdapter.
* B. UPSTOX-SPECIFIC — request mapping, instrument mapping, order-type
  mapping, product mapping, exchange mapping, validity mapping, capability
  mapping, client-order-ID mapping, error mapping, state mapping, response
  validation, cancellation, reconciliation.
* C. SUBMISSION INFRASTRUCE INTEGRATION — end-to-end with the mocked Upstox
  client.

## 35. 26-Scenario Failure Matrix

All 26 scenarios from 17.6 exposed as test methods; each fails safely:

1 accepted submission, 2 rejected submission, 3 validation failure,
4 insufficient funds, 5 invalid instrument, 6 invalid order type, 7 timeout,
8 unknown outcome, 9 reconciliation accepted, 10 reconciliation rejected,
11 reconciliation unknown, 12 duplicate submission, 13 duplicate broker
response, 14 malformed broker response, 15 rate limit, 16 broker
unavailable, 17 authentication failure, 18 restart during submission,
19 restart during UNKNOWN, 20 paper/live mismatch, 21 wrong adapter
selection, 22 missing credentials, 23 invalid credentials, 24 unsupported
capability, 25 cancellation timeout, 26 cancellation race with fill.

## 36. Contract-Conformance Results

`tests/test_checkpoint_17_7_upstox_adapter.py::TestUpstoxBrokerAdapterContractConformance`
runs the full reusable 17.4 conformance suite against the Upstox adapter.
All pass (module total 97 tests). The 342 prior 17.2–17.4 conformance tests
pass unchanged.

## 37. Network Safety Audit

grep + AST scan across the 17.7 modules:

* network imports: NONE
* Upstox SDK: NONE
* API URLs in execution code: NONE
* socket / WebSocket / HTTP-client references: NONE

The only `place_order`/`cancel_order` calls are to the injected
`UpstoxBrokerClient` Protocol (mock in 17.7), NOT a network transport.

## 38. Credential Safety Audit

Fake-secret tests prove credentials cannot appear in:

* exceptions
* AdapterResult
* BrokerError
* logs / audit data
* persistence payloads
* request representations

`redact_sensitive` scrubs `Authorization: Bearer ...`,
`UPSTOX_EXECUTION_ACCESS_TOKEN=...`, `UPSTOX_ANALYTICS_TOKEN=...`,
`UPSTOX_ACCESS_TOKEN=...` from every reason string.

## 39. Broker-Neutrality Audit

ExecutionCommand, BrokerResultStatus, BrokerError, AdapterResult,
SubmissionLifecycle contain NO Upstox-specific types or fields
(verified by tests + source inspection). Broker-specific types terminate
at the adapter/client boundary.

## 40. Dependency-Direction Audit

core --> BrokerAdapter --> UpstoxBrokerAdapter --> UpstoxBrokerClient.
NO core --> Upstox path. NO ExecutionCommand --> UpstoxOrderRequest path.
NO SubmissionLifecycle --> Upstox API-model path. Verified by the
dependency-direction tests (import inspection of core modules).

## 41. Regression Audit

`git diff --name-only` is EMPTY (no tracked file change). All 7 new files
are untracked additions. Checkpoints 10–16 remain frozen; no frozen test was
modified; no execution semantics changed; no authorization bypass; no
command mutation; no lifecycle corruption; no persistence regression; no
paper/live regression; no adapter-selection regression.

Prior execution suites re-run and pass unchanged:

* 17.2 contract/store/fake-broker: pass
* 17.3 infrastructure: pass
* 17.4 conformance/reference/integration: pass
* 14–16 execution command/authorization/intent (execution_command,
  execution_command_store, execution_authorization, execution_authorization_engine,
  execution_authorization_store, operational_trade_intent, engine, app): pass

## 42. Files Changed

Created (7 files):

* `src/engine/intelligence/upstox_broker_models.py`
* `src/engine/intelligence/upstox_credential_provider.py`
* `src/engine/intelligence/upstox_broker_client.py`
* `src/engine/intelligence/upstox_broker_adapter.py`
* `tests/test_checkpoint_17_7_upstox_adapter.py`
* `tests/test_checkpoint_17_7_upstox_client.py`
* `tests/test_checkpoint_17_7_upstox_integration.py`
* `docs/checkpoint_17_7_upstox_broker_adapter_implementation.md`
* `AGENTS.md` (appended entry)

Modified: NO tracked file.

## 43. Testing Report

* new 17.7 tests: 166 (97 adapter + 52 client + 17 integration)
* 17.2–17.4 prior conformance: 342 pass
* 14–16 frozen execution: 627 pass
* full suite: 5989 passed / 0 failed / 2 warnings
* baseline 5823 + 166 = 5989 (exact)

## 44. PASS Findings

* UpstoxBrokerAdapter implements the frozen BrokerAdapter contract.
* Broker-specific models/errors are isolated.
* request/instrument/order-type/product/validity/exchange/capability/
  client-order-id/idempotency/error/state/reconciliation/cancellation
  mapping implemented.
* timeout/rate-limit -> UNKNOWN -> reconcile (never a false failure).
* UNKNOWN requires reconciliation; blind retry prohibited.
* paper/live isolation fail-closed with no fallback.
* 26-scenario failure matrix passes.
* generic conformance suite (reused) passes.
* SubmissionInfrastructure integration passes.
* NO network, NO SDK, NO credentials, NO real broker.
* full suite 5989 with zero unexplained regression.

## 45. CONCERN Findings

* Real Upstox order-state vocabulary, error-code semantics, tag length
  rules and broker-side idempotency REQUIRE IMPLEMENTATION-TIME
  VERIFICATION against current Upstox documents before any real adapter is
  enabled (already captured in the 17.6 design).
* The `submitted` scenario is a mock-specific convenience; the exact
  distinction between SUBMITted and ACCEPTED broker states must be
  confirmed live.
* No real rate-limit / timeout durations exist (adapter-boundary clock
  abstraction delegated to 17.7/real-HTTP).
* No real credential provider exists (only deterministic test providers).

## 46. BLOCKER Findings

NONE.

## 47. Known Limitations

* The adapter does NOT perform network operations and CANNOT connect to
  Upstox.
* The first execution-mode is LIMIT/DELIVERY/DAYNSE (fixtures + mock);
  SL / SL-M / IOC / margins are capability-gated until verified.
* Target 2 is not transmissible (documented loss; never fabricated).
* Real-broker behavior is unknown until sandbox/paper connectivity exists.
* This is the FIRST concrete broker-specific adapter; live-trading safety
  is NOT established (and not claimed).

## 48. Checkpoint 17.8 Recommendation

CHECKPOINT 17.8 — SANDBOX/PAPER BROKER INTEGRATION & RECONCILIATION
VALIDATION. 17.8 must STILL NOT automatically authorize live trading; it
should focus on controlled sandbox/paper connectivity where available,
credential-boundary validation, real response normalization, real error
mapping, timeout behavior, reconciliation, duplicate handling, broker-side
order-state behavior, cancellation behavior, rate-limit behavior, restart
recovery, observability, and safety gates. Any real broker connectivity must
remain separately controlled and explicitly scoped.

## 49. Final Verdict

PASS.

"The designed Upstox broker adapter has been implemented and verified
against the broker-neutral architecture using mocks only."

PASS does NOT mean:
* Upstox connectivity is proven.
* Upstox credentials are valid.
* Upstox API behavior is proven live.
* Live orders are safe.
* Live trading is authorized.

STOP after Checkpoint 17.7. Do NOT start Checkpoint 17.8 automatically. Do
NOT connect to Upstox. Do NOT request credentials. Do NOT submit any order.