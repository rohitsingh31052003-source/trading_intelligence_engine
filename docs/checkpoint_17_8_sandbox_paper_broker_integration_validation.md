# CHECKPOINT 17.8 — SANDBOX/PAPER BROKER INTEGRATION & RECONCILIATION VALIDATION

Status: COMPLETE / PASS WITH LIMITATIONS

## 1. Checkpoint Overview

Checkpoint 17.8 validates the implemented Upstox broker adapter (Checkpoint
17.7) against a CONTROLLED REAL BROKER / PAPER / SANDBOX environment, where
such an environment is actually available. The objective is to validate the
broker-specific assumptions that could not be proven with mocks in Checkpoint
17.7.

This checkpoint is EXPLICITLY AUTHORIZED. It is NOT a live-trading
checkpoint. No live trading, no real-money orders, no automatic live
execution, no bypass of existing execution-mode checks.

## 2. Objective

Validate, through a controlled non-live environment where available:

* the real broker client boundary (authentication, environment identity,
  capability/metadata, instrument lookup, order-history/reconciliation,
  cancellation)
* the guarded startup sequence (Phase 6)
* the LIVE-mode hard gate (Phase 7)
* client-order-id / tag behavior, broker order id behavior, broker-side
  idempotency
* real response / order-state / error normalization
* timeout / UNKNOWN / reconciliation behavior
* restart recovery and auditability
* paper/live isolation and credential safety

## 3. Scope

IN SCOPE (17.8):

* Pre-integration safety audit of the frozen 17.1–17.7 architecture.
* Determination of the controlled Upstox environment actually available.
* Credential safety audit.
* Offline / mock validation of every behavior that can be proven without a
  real broker (opt-in gate, startup guard, live-mode hard gate, mode
  isolation, credential boundary, restart recovery, auditability).
* Documentation of broker-specific facts VERIFIED FROM OFFICIAL UPSTOX
  DOCUMENTATION.
* An explicit opt-in mechanism (``CHECKPOINT_17_8_REAL_BROKER=1``) so real
  broker tests never run automatically.
* A real-broker integration test scaffold that SKIPS when no controlled
  credential is available.

OUT OF SCOPE (17.8):

* Any real HTTP broker client implementation (no controlled credential was
  available in this environment).
* Real order submission / cancellation / reconciliation.
* Live trading, real-money orders, production deployment.
* Modification of any frozen Checkpoint 10–16 file.

## 4. Non-Goals

* NO live trading.
* NO real-money orders.
* NO real broker connection (no controlled credential available).
* NO automatic live execution.
* NO bypass of execution-mode checks.
* NO use of a LIVE credential when a controlled PAPER/SANDBOX environment is
  unavailable.
* NO substitution of a real-money environment for a missing sandbox.

## 5. Safety Boundary

* Checkpoints 10–16 are FROZEN. Do not reopen/refactor/redesign/modify.
* ExecutionCommand remains immutable and broker-neutral.
* Authorization remains strictly upstream.
* SubmissionLifecycle / SubmissionInfrastructure remain broker-neutral.
* The broker-neutral BrokerAdapter contract remains unchanged.
* Blind retry remains prohibited (UNKNOWN -> RECONCILE -> only then decide).
* Paper/live cannot silently cross.
* Real network communication, if ever introduced, lives ONLY behind
  UpstoxBrokerClient.

## 6. 17.1–17.7 Baseline

* 17.1 Broker Adapter Boundary Architecture Audit — PASS
* 17.2 Broker Adapter Contract & Execution Lifecycle Contract — PASS
* 17.3 Submission Infrastructure — PASS
* 17.4 Reference Adapter — PASS
* 17.5 Real Broker Integration Preparation & Safety Audit — PASS WITH
  LIMITATIONS
* 17.6 Real Broker Adapter Design — PASS
* 17.7 Upstox Broker Adapter Implementation — PASS

Baseline suite (17.7): 5989 passed / 0 failed / 2 warnings.

## 7. Environment Used

**Determination (Phase 2):**

The official Upstox developer documentation confirms that Upstox provides a
controlled SANDBOX environment:

> "we have developed a sandbox environment that closely emulates the actual
> API integration experience. This setup allows developers to fully integrate
> and test their applications end-to-end on the payload before even connecting
> to the live market. In the sandbox, you can test strategies and
> integrations comprehensively without incurring any costs and without any
> time restrictions, unlike the live system which operates only during defined
> periods."
>
> — https://upstox.com/developer/api-documentation/sandbox

> "Test your integration without touching the live market. The sandbox
> environment closely emulates the real API with no risk and no time
> restrictions. Currently supports place, modify, and cancel order APIs."
>
> — https://upstox.com/developer/api-documentation/api-overview

The sandbox endpoint is documented as ``https://sandbox.upstox.com/v2/order/place``
with ``Authorization: Bearer {sandbox_token}`` (API Overview page).

**Environment actually available in this runtime:**

* ``UPSTOX_ANALYTICS_TOKEN`` is present (a LIVE market-data analytics token).
  Per the official API Overview page: "Powers Market Data and Realtime &
  Streaming APIs" and "Portfolio (read-only) and Account & Funds (read-only)
  APIs are also supported when accessed from a registered static IP." It is
  NOT an execution credential and NOT a sandbox credential.
* ``UPSTOX_EXECUTION_ACCESS_TOKEN`` is ABSENT.
* No sandbox execution token was provided or authorized by the user.

**Conclusion:** A controlled Upstox sandbox environment EXISTS per official
documentation, but NO controlled sandbox credential is available in this
environment, and the only credential present is a live data credential that
must NOT be used for execution. Per Phase 3 ("If credential injection is not
safely available: STOP") and Phase 42 stop conditions, the real-connectivity
portion of Checkpoint 17.8 STOPS here. All possible integration behavior is
validated through the existing mock, offline contract verification is
performed, and live/sandbox behavior is marked NOT VERIFIED.

## 8. Broker Connectivity Architecture

The architecture remains:

```
ExecutionCommand
    ↓
SubmissionInfrastructure
    ↓
SubmissionLifecycle
    ↓
BrokerAdapter
    ↓
UpstoxBrokerAdapter
    ↓
UpstoxBrokerClient
    ↓
Controlled Upstox environment
```

In Checkpoint 17.8 NO real HTTP client was introduced (no controlled
credential available). The only client implementations are the network-free
``MockUpstoxBrokerClient`` (17.7) and the test-only fake/reference brokers.
A future ``ControlledUpstoxBrokerClient`` would implement the same
``UpstoxBrokerClient`` protocol and be injectable; it is NOT implemented here.

## 9. Credential Architecture

* The adapter NEVER reads the token.
* The broker client obtains the token ONLY from an injected credential
  provider at the network boundary.
* ``EnvironmentUpstoxCredentialProvider`` reads ``UPSTOX_EXECUTION_ACCESS_TOKEN``
  lazily and returns an empty string when absent (fail closed).
* ``SENSITIVE_TOKEN_ENV_NAMES`` includes ``UPSTOX_EXECUTION_ACCESS_TOKEN``,
  ``UPSTOX_ANALYTICS_TOKEN``, ``UPSTOX_ACCESS_TOKEN``; the client's
  ``redact_sensitive`` scrubs ``Authorization: Bearer <token>`` and
  ``<NAME>=<value>`` patterns before any error/reason reaches the adapter.
* The startup guard checks the provider exists and yields a non-empty token,
  but NEVER stores, returns, or logs the token value.
* NO credential is committed, persisted, logged, printed, placed in
  exceptions, or placed in test snapshots.

## 10. Startup Guard

Implemented offline and deterministically in
``src/engine/intelligence/controlled_broker_validation.py``:

``controlled_broker_startup_guard(...)`` verifies, BEFORE any controlled
broker API call:

1. execution mode (reported mode valid and matches the required PAPER mode)
2. adapter identity (non-empty broker_identity)
3. broker identity (no LIVE environment-name confusion)
4. credential provider (a provider exposing ``get_access_token``)
5. credential availability (provider yields a non-empty token)
6. environment (recognized controlled SANDBOX/PAPER kind; UNKNOWN fails
   closed unless explicitly allowed)
7. controlled/paper account (LIVE -> SAFETY FAILURE)
8. capability (SUBMIT + RECONCILE present)
9. required configuration (required keys present and non-empty)
10. fail closed if ANY check fails

The guard NEVER infers "credentials exist" == "safe to trade": a valid token
with a LIVE environment still fails closed.

## 11. Mode Guard

``live_mode_hard_gate(expected_environment, reported_environment)`` fires
(returns True) when a controlled (SANDBOX/PAPER) environment is required and a
LIVE environment is reported. The correct result is SAFETY FAILURE -> NO ORDER.
There is NO automatic mode switch, NO environment retry, NO alternate
adapter, NO alternate credential, NO downgraded error.
``assert_no_auto_mode_switch()`` documents that the guard has no fallback code
path.

## 12. Instrument Validation

VERIFIED FROM OFFICIAL DOCUMENTATION (not live):

* The place-order API requires ``instrument_token`` (e.g.
  ``NSE_EQ|INE669E01016``). Official Place Order page: "instrument_token
  Required string Key of the instrument."
* The 17.7 adapter maps canonical instruments to verified tokens
  (RELIANCE/TCS/HDFCBANK/ICICIBANK -> ``NSE_EQ|INE...``, NIFTY ->
  ``NSE_INDEX|Nifty 50``); unknown instruments fail closed (never guessed).

NOT VERIFIED (no live instrument lookup performed): live tradability of each
mapped token; exact V3 instrument-token behavior.

## 13. Order-Type Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: "order_type Required string ... Possible values:
  ``MARKET``, ``LIMIT``, ``SL``, ``SL-M``."
* The 17.7 adapter first-scope supports LIMIT (and MARKET); SL/SL-M remain
  capability-gated pending real-semantics verification (17.7 design).

NOT VERIFIED (no live order-type submission): actual SL/SL-M trigger behavior
on the sandbox; whether the sandbox accepts MARKET orders (official docs note
``UDAPI1158 Market orders are not allowed. Try placing a limit order.`` as a
live error code).

## 14. Product Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: "product Required string ... Possible values:
  ``I``, ``D``, ``MTF``." The 17.7 adapter first-scope uses ``D`` (delivery).

NOT VERIFIED: live sandbox acceptance of product ``D`` for each instrument.

## 15. Validity Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: "validity Required string It can be one of the
  following - DAY(default), IOC. Possible values: ``DAY``, ``IOC``." The 17.7
  adapter first-scope uses ``DAY``.

NOT VERIFIED: live sandbox acceptance of ``DAY`` validity.

## 16. Exchange Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Instrument tokens carry an exchange segment (e.g. ``NSE_EQ|...`` for NSE
  equities). The 17.7 adapter supports ``NSE_EQ|`` and ``NSE_INDEX|`` prefixes
  and fails closed on others.

NOT VERIFIED: live sandbox behavior for NSE index (NIFTY) orders.

## 17. Quantity Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: "quantity Required integer (int32) ... For other
  Futures & Options and equities - number of units is accepted in multiples
  of the tick size." and error ``UDAPI1052 The order 'quantity' cannot be
  zero``.

The 17.7 adapter floors quantity to a positive integer lot multiple and NEVER
increases it (risk invariant). Zero/negative/fractional -> fail closed.

NOT VERIFIED: live sandbox lot-size / tick-size behavior per instrument.

## 18. Price/Trigger Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: ``price`` required number (float); ``trigger_price``
  "If the order is a stop loss order then the trigger price to be set is
  mentioned here"; error codes ``UDAPI1037 Trigger price should be less than
  limit price`` and ``UDAPI1038 Trigger price should be greater than limit
  price``.
* The 17.7 adapter passes entry/stop verbatim (Decimal), validates LONG stop <
  entry / SHORT stop > entry, and NEVER invents rounding.

NOT VERIFIED: live tick-size / price-precision rules on the sandbox.

## 19. Client Order ID Validation

17.7 implemented ``command_id -> client_order_id (co- + sha256[:16]) ->
Upstox tag (uptag- + sha256[:12])``.

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page: "tag Optional string Tag for a particular order"
  and "You can assign a tag(unique identifier) to your order, allowing you to
  retrieve orders associated with that tag using the Order History API."
* Official Get Order History page: "Order history can be retrieved by
  utilizing either the order_id or a tag."

NOT VERIFIED (no live tag submission): the real maximum tag length and
permitted characters (the 17.7 bounded tag ``uptag-`` + 12 hex chars = 18
chars is a conservative design; REQUIRES IMPLEMENTATION-TIME VERIFICATION
against the sandbox). No silent truncation is performed.

## 20. Broker Order ID Validation

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Official Place Order page response: ``{"status":"success","data":{"order_id":"1644490272000"}}``
  — a single broker ``order_id`` string.
* Official Get Order History page: ``data[].order_id`` "Unique order ID
  assigned internally for the order placed"; ``data[].exchange_order_id``
  "Unique order ID assigned by the exchange".

CONCERN (documented, requires implementation-time verification): the 17.7
``UpstoxOrderData.order_ids`` models the place response as a TUPLE of ids
(V3 may return an array); the official V2 place response returns a SINGLE
``data.order_id`` string. The adapter normalization must be verified against
the actual V3 sandbox response before real submission. Broker order id is
NEVER treated as interchangeable with command_id.

## 21. Broker Idempotency Validation

NOT VERIFIED. No live submission was performed, so broker-side idempotency
for the relevant submission operation could not be demonstrated. The system
does NOT assume broker-side idempotency, does NOT infer it from
client_order_id, and does NOT infer it from broker order id.

Documented separately:

1. ``command_id`` — deterministic core identity (cmd- + sha256[:16]).
2. ``client_order_id`` — deterministic application identity (co- + sha256[:16]).
3. ``idempotency_key`` — deterministic local duplicate key (idem- + sha256[:16]).
4. ``broker_order_id`` — broker-generated id (downstream-only on the lifecycle).
5. Actual broker-side duplicate behavior — NOT VERIFIED.

Because broker-side idempotency is unverified, the system retains
UNKNOWN -> RECONCILE -> ONLY THEN determine whether retry is safe.

## 22. Submission Validation

No real submission was performed (no controlled credential). The submission
path was validated USING MOCKS (17.7 + 17.8 offline tests): accepted /
rejected / failed / timeout / unknown / duplicate / restart scenarios all
normalize correctly and the frozen lifecycle rules hold.

## 23. Response Validation

VERIFIED FROM OFFICIAL DOCUMENTATION (envelope shape):

* Place Order response: ``status`` ("success") + ``data.order_id``.
* Get Order History response: ``status`` ("success") + ``data`` ARRAY of
  order records (each with status/order_id/tag/quantity/price/...).
* Cancel Order response: ``status`` ("success") + ``data.order_id``.

CONCERN (documented): the 17.7 ``UpstoxOrderStateResponse`` models a SINGLE
order-history record; the official Get Order History returns an ARRAY. The
adapter's order-history parsing must be verified against the real array shape
before real reconciliation. No validation was weakened to make a response
pass.

## 24. Order-State Validation

VERIFIED FROM OFFICIAL DOCUMENTATION (Order Status appendix):

Real Upstox status vocabulary includes: ``validation pending``, ``modify
pending``, ``trigger pending``, ``put order req received``, ``open``,
``complete``, ``cancelled``, ``rejected``, ``cancel pending``, ``not
cancelled``, ``open pending``, ``modified``, ``not modified``, etc.

CONCERN (documented): the 17.7 ``UpstoxOrderState`` enum (open/accepted/
complete/cancelled/rejected/partially_filled/unknown) is a SUBSET of the real
vocabulary. Any unrecognized real status string maps to UNKNOWN (never forced
into an unsafe mapping). Full state mapping REQUIRES IMPLEMENTATION-TIME
VERIFICATION against the sandbox.

## 25. Error Mapping Validation

VERIFIED FROM OFFICIAL DOCUMENTATION (Place Order error codes):

``UDAPI1026`` instrument key required; ``UDAPI1004`` valid order type required;
``UDAPI1056`` order_type invalid; ``UDAPI1057`` transaction_type invalid;
``UDAPI1006`` product required; ``UDAPI1054`` product invalid; ``UDAPI1007``
validity required; ``UDAPI1055`` validity invalid; ``UDAPI1008`` price
required; ``UDAPI1052`` quantity cannot be zero; ``UDAPI1037``/``UDAPI1038``
trigger/limit ordering; ``UDAPI100011`` invalid instrument key; ``UDAPI1158``
market orders not allowed (try limit).

The 17.7 adapter normalizes known codes to the broker-neutral taxonomy; any
unknown code -> ``UNKNOWN_OUTCOME`` (AMBIGUOUS). Broker-specific error
structures never leak into core.

NOT VERIFIED: live authentication-failure / rate-limit / broker-unavailable
responses (no live call).

## 26. Timeout Validation

VALIDATED USING MOCKS (17.7/17.8): a timeout on submission normalizes to
``UNKNOWN`` (never ``FAILED``); a timeout on cancellation normalizes to
``UNKNOWN`` (reconcile). The safety property "timeout/lost response ->
UNKNOWN -> reconcile" is preserved. No deliberate real network interruption
was created (Phase 23 safety: mock simulation used instead).

## 27. UNKNOWN Validation

VALIDATED USING MOCKS + frozen contract: UNKNOWN means "broker may or may not
have accepted"; blind retry is PROHIBITED (request_submission on UNKNOWN
raises); reconciliation uses the SAME deterministic client_order_id; a
still-unknown reconcile keeps UNKNOWN and retry stays prohibited. UNKNOWN
survives restart as RECONCILE_REQUIRED.

## 28. Reconciliation Validation

VALIDATED USING MOCKS: reconcile-by-tag (primary) and broker-order-id
(fallback) lookup; exact match -> confirmed; no match / multiple / malformed /
conflicting -> UNKNOWN; broker unavailable -> BROKER_UNAVAILABLE. The frozen
``reconcile_command`` advances the lifecycle ONLY on a confirmed outcome.

VERIFIED FROM OFFICIAL DOCUMENTATION: Get Order History supports lookup by
``order_id`` OR ``tag``.

CONCERN (documented, critical for real reconciliation): official Get Order
History states "Orders placed by the user remain available for one trading day
and are automatically removed at the end of the trading session." A real
reconciliation window must therefore be bounded to the same trading day; this
REQUIRES IMPLEMENTATION-TIME VERIFICATION and a documented reconcile-window
policy before real use.

## 29. Cancellation Validation

VERIFIED FROM OFFICIAL DOCUMENTATION: Cancel Order API (order_id required);
error ``UDAPI100040 Cancel of already cancelled/rejected/completed order is
not allowed``; response ``status`` + ``data.order_id``.

VALIDATED USING MOCKS: confirmed cancellation -> CANCELLED; fill/cancel race
-> REJECTED (fill authoritative); cancellation timeout/unknown -> UNKNOWN
(reconcile). No live cancellation was performed.

## 30. Rate-Limit Validation

NOT VERIFIED against the live/sandbox API (no aggressive request generation
was performed — Phase 26 explicitly forbids intentionally abusing the broker
API). The 17.7 design treats a rate-limited submission as AMBIGUOUS -> UNKNOWN
-> reconcile; safe retry = GET/reconcile only. This remains the documented
policy.

## 31. Restart Recovery

VALIDATED USING MOCKS (17.3/17.7/17.8): UNKNOWN persists; after restart the
recovery decision is RECONCILE_REQUIRED (never SAFE_TO_SUBMIT, never
automatic resubmit). CREATED/SUBMISSION_REQUESTED(pre_submission=True) ->
SAFE_TO_SUBMIT; terminal -> NO_ACTION. The 17.8 offline test
``test_unknown_persistence_recovery`` re-asserts this at the 17.8 level.

## 32. Auditability

VALIDATED USING MOCKS: ``SubmissionInfrastructureAudit`` reconstructs, from
the persisted broker-neutral store alone: command_id, submission_id,
client_order_id, idempotency_key, state, requires_reconciliation,
reconciliation_performed, retry_allowed, terminal, pre_submission, created_at,
event_count, last_reason, duplicate_commands. NO credentials, NO broker SDK
objects, NO URLs, NO products are stored. The 17.8 offline test
``test_audit_is_broker_neutral`` re-asserts the audit contains no
access_token/Authorization/Bearer material.

## 33. Paper/Live Isolation

VALIDATED USING MOCKS + frozen contract + 17.8 offline tests:

* PAPER adapter + LIVE command -> FAIL CLOSED (ValueError).
* LIVE adapter + PAPER command -> FAIL CLOSED (ValueError).
* Missing environment -> FAIL CLOSED (startup guard).
* Missing credential -> FAIL CLOSED (startup guard / client).
* Wrong broker -> FAIL CLOSED (broker identity check / select_adapter).
* Unsupported capability -> FAIL CLOSED (capability boundary).
* NO fallback behavior anywhere.

## 34. Test Architecture

* Network-free default suite: tests/test_checkpoint_17_8_offline_validation.py
  (77 tests) — runs in ordinary CI, requires NO internet/Upstox/credentials.
* Opt-in real-broker suite: tests/test_checkpoint_17_8_real_broker_opt_in.py
  (6 tests) — SKIPPED unless ``CHECKPOINT_17_8_REAL_BROKER=1`` AND a
  controlled credential is available. Never runs by default.
* The 17.7 mock / fake / reference broker tests remain intact.

## 35. Real Broker Tests

The opt-in suite (``test_checkpoint_17_8_real_broker_opt_in.py``) is the
designated real-broker test surface. In this environment it SKIPS (6 skipped)
because no controlled sandbox credential is available. When a controlled
credential is later provided, the suite runs only with
``CHECKPOINT_17_8_REAL_BROKER=1``.

## 36. Mock/Fake Tests

All broker behavior that could be validated without a real broker was
validated using the deterministic network-free mock (17.7) and the offline
17.8 tests (opt-in gate, startup guard, live-mode hard gate, mode isolation,
credential boundary/redaction, restart recovery, auditability, error/state
normalization).

## 37. Full Regression

* 17.8 unit tests: 77 passed (offline) + 6 skipped (opt-in real broker).
* 17.7 tests: 166 passed.
* 17.2–17.4 contract/infrastructure tests: 342 passed.
* Frozen Checkpoint 14–16 execution tests: 627 passed.
* Focused execution aggregate: 1135 passed.
* FULL SUITE (network-free, WITHOUT real-broker integration): 6066 passed,
  6 skipped, 2 warnings. Baseline was 5989 passed; delta = +77 new offline
  tests. No regression.
* Controlled 17.8 integration suite: NOT RUN (no controlled credential
  available; the opt-in suite is skipped).

## 38. Frozen Checkpoint Integrity

``git diff`` shows NO tracked file modified. Only three NEW untracked files
were added:

* src/engine/intelligence/controlled_broker_validation.py
* tests/test_checkpoint_17_8_offline_validation.py
* tests/test_checkpoint_17_8_real_broker_opt_in.py

Checkpoints 10–16 remain frozen and byte-identical. ExecutionCommand,
authorization, TradePlan, the broker-neutral contract, SubmissionLifecycle,
and SubmissionInfrastructure are UNCHANGED.

## 39. Security Sweep

* Hard-coded secrets: NONE.
* Network imports outside the broker client: NONE.
* Broker API endpoint literals in execution code: NONE (the only Upstox URL
  in src/ is the pre-existing HISTORICAL data provider endpoint, untouched
  and unreachable from the execution layer).
* Accidental live-mode defaults: NONE.
* Automatic live fallback: NONE.
* Credential logging/persistence: NONE.
* Real HTTP execution client: NONE (not implemented — no controlled
  credential available).

## 40. Network Boundary Audit

No network path was introduced by Checkpoint 17.8. The only client
implementations remain the network-free mock (17.7) and test fakes. A future
real client would sit ONLY behind ``UpstoxBrokerClient``; the core remains
unaware of HTTP/REST/URLs/headers/tokens/status codes.

## 41. Credential Leakage Audit

* No credential in source, docs, AGENTS.md, tests, fixtures, persistence, or
  git history.
* ``redact_sensitive`` scrubs ``Bearer <token>`` and token env-var values from
  every error/reason string (verified by tests).
* The startup guard never returns/logs the token value (verified by test).
* ``UPSTOX_ANALYTICS_TOKEN`` (the only credential present) is a LIVE data
  credential and was NOT used for any execution operation.

## 42. Findings

Summary of the 17.8 findings (PASS / CONCERN / BLOCKER below).

## 43. PASS Findings

1. The official Upstox sandbox environment EXISTS (verified from official
   documentation) and supports place/modify/cancel order APIs.
2. No controlled sandbox credential was available; the real-connectivity
   portion STOPPED safely per the checkpoint rules (no live substitution).
3. The startup guard (offline, deterministic) enforces all Phase 6
   preconditions and fails closed on any unmet check.
4. The LIVE-mode hard gate fires on LIVE environment/mode when a controlled
   environment is required (SAFETY FAILURE -> NO ORDER); no fallback.
5. Paper/live isolation is proven (PAPER<->LIVE cannot cross; missing
   environment/credential/capability fail closed).
6. Credential boundary + redaction are proven (no token in results/errors/
   persistence/audit).
7. UNKNOWN never becomes FAILED merely because a network request failed
   (timeout/unknown/malformed -> UNKNOWN).
8. UNKNOWN requires reconciliation; blind retry is impossible (frozen
   contract + tests).
9. Restart recovery: UNKNOWN persists and recovers as RECONCILE_REQUIRED
   (never automatic resubmit).
10. Auditability is broker-neutral and credential-free.
11. Broker-specific facts (order types, product, validity, tag lookup,
    order-status vocabulary, error codes, cancel semantics) are documented
    with verbatim official quotes.
12. The opt-in gate (``CHECKPOINT_17_8_REAL_BROKER=1``) keeps real broker
    tests from running automatically; the default suite is network-free.
13. Full regression: 6066 passed / 6 skipped / 2 warnings; no regression from
    the 5989 baseline.
14. Frozen Checkpoints 10–16 are untouched (git diff clean for tracked files).

## 44. CONCERN Findings

1. The 17.7 ``UpstoxOrderData.order_ids`` models the place response as a
   TUPLE; the official V2 place response returns a SINGLE ``data.order_id``
   string. The adapter normalization must be verified against the real V3
   sandbox response before real submission (REQUIRES IMPLEMENTATION-TIME
   VERIFICATION).
2. The official Get Order History returns an ARRAY of order records; the 17.7
   ``UpstoxOrderStateResponse`` models a SINGLE record. Real reconciliation
   parsing must be verified against the array shape.
3. Official Get Order History states orders are available for ONE TRADING DAY
   and removed at end of session; real reconciliation must be bounded to the
   trading day (reconcile-window policy REQUIRES VERIFICATION).
4. The 17.7 ``UpstoxOrderState`` enum is a subset of the real order-status
   vocabulary; unrecognized statuses map to UNKNOWN (safe) but the full
   mapping requires sandbox verification.
5. The real Upstox tag maximum length / permitted characters are NOT VERIFIED;
   the conservative bounded tag (18 chars) requires confirmation (no silent
   truncation).
6. Broker-side idempotency is NOT VERIFIED; the system correctly retains
   UNKNOWN -> RECONCILE -> only-then-decide.
7. Live authentication-failure / rate-limit / broker-unavailable responses
   are NOT VERIFIED (no live call).
8. No real HTTP client was implemented (no controlled credential); this is a
   scope limitation, not a defect.

None of the CONCERN items create an unsafe path; all are documented future
implementation-time verifications.

## 45. BLOCKER Findings

NONE.

The absence of real broker connectivity, real credentials, and a live path is
INTENTIONAL and NOT a blocker (stated explicitly in the checkpoint
instructions). Live trading is intentionally OUT OF SCOPE.

## 46. Known Limitations

* Real controlled broker behavior is NOT VERIFIED (no sandbox credential
  available in this environment).
* No real HTTP Upstox client was implemented.
* No real order submission / cancellation / reconciliation occurred.
* Broker-side idempotency is unverified.
* Real tag-length / order-state / error-code / rate-limit / timeout behavior
  requires implementation-time verification against the sandbox.
* The order-history array-vs-single-record and place-response order_ids
  tuple-vs-single-string discrepancies require adapter verification.

## 47. Verified Broker Behaviors

VERIFIED FROM OFFICIAL DOCUMENTATION:

* Sandbox environment exists; supports place/modify/cancel; endpoint
  ``https://sandbox.upstox.com/v2/order/place``; Bearer sandbox token.
* Place order fields: quantity (int, positive), product (I/D/MTF), validity
  (DAY/IOC), price, tag (optional), instrument_token (required), order_type
  (MARKET/LIMIT/SL/SL-M), transaction_type (BUY/SELL), trigger_price,
  disclosed_quantity, is_amo, market_protection.
* Place order success response: ``{"status":"success","data":{"order_id":...}}``.
* Place order error codes: UDAPI1004/1006/1007/1008/1026/1037/1038/1041/1042/
  1043/1052/1054/1055/1056/1057/100011/1158 (documented above).
* Get Order History: lookup by order_id OR tag; response data is an array;
  orders retained one trading day.
* Order status vocabulary (appendix): validation pending / open / complete /
  cancelled / rejected / cancel pending / not cancelled / open pending / etc.
* Cancel Order: order_id required; UDAPI100040 (cannot cancel already
  cancelled/rejected/completed); success response status + data.order_id.

VERIFIED USING MOCKS (17.7 + 17.8 offline):

* Submission normalization (accepted/rejected/failed/timeout/unknown/
  duplicate/restart).
* Timeout/unknown/malformed -> UNKNOWN (never FAILED).
* UNKNOWN -> reconcile -> confirmed outcome; still-unknown stays UNKNOWN.
* Blind retry impossible; restart recovery RECONCILE_REQUIRED.
* Paper/live isolation; missing env/credential/capability fail closed.
* Credential boundary + redaction; audit broker-neutral + credential-free.
* Startup guard + live-mode hard gate.

## 48. Unverified Broker Behaviors

NOT VERIFIED (no live controlled connectivity):

* Live authentication / credential validation against the sandbox.
* Live instrument lookup / tradability.
* Live order-type / product / validity / exchange acceptance.
* Live quantity / price / trigger / tick-size rules.
* Real tag length / permitted characters / lookup behavior.
* Broker order id stability and duplicate handling.
* Broker-side idempotency.
* Real response envelope / order-state / error mapping.
* Timeout / rate-limit / cancellation behavior against the real sandbox.

## 49. Changes Required for Future Live Integration

Before any future real (or sandbox) integration:

1. Obtain a controlled sandbox credential and verify the adapter's place-order
   response parsing against the real V3 response (order_ids tuple vs single
   order_id).
2. Verify Get Order History array parsing and the one-trading-day retention
   window; implement a bounded reconcile-window policy.
3. Verify the full real order-status vocabulary and extend the state mapper
   (unrecognized -> UNKNOWN preserved).
4. Verify the real tag length/character constraints; adjust the deterministic
   tag encoding WITHOUT silent truncation if required.
5. Verify broker-side idempotency; do not claim it until demonstrated.
6. Implement the real HTTP client ONLY behind UpstoxBrokerClient with the
   startup guard + live-mode hard gate + opt-in gate enforced.
7. Verify rate-limit / timeout / cancellation behavior in the sandbox.
8. Re-run the repository safety sweep and full regression after any change.

## 50. Checkpoint 17.9 Recommendation

RECOMMENDED: **CHECKPOINT 17.9 — BROKER INTEGRATION HARDENING & CONTROLLED
EXECUTION GATE AUDIT.**

17.9 should evaluate (per the checkpoint instructions):

* long-running broker connectivity
* credential rotation
* connection recovery
* reconciliation robustness
* duplicate prevention
* operational observability
* audit completeness
* broker-state drift
* restart behavior
* failure recovery
* execution-mode hardening
* explicit live-execution gate design

17.9 MUST NOT automatically authorize live trading. Live execution requires a
separate explicit authorization checkpoint. Do NOT start Checkpoint 17.9
automatically.

## 51. Final Verdict

**PASS WITH LIMITATIONS**

* The offline/mock controlled validation works and every critical safety
  behavior is proven within the available (mock) environment: startup guard,
  live-mode hard gate, paper/live isolation, credential boundary, UNKNOWN
  semantics, reconciliation, restart recovery, auditability.
* The controlled real broker integration portion is NOT VERIFIED: no
  controlled sandbox credential was available in this environment, so no real
  connectivity was attempted (correctly — a lack of sandbox availability is
  NOT permission to use a live account, and the only credential present is a
  live data token that was not used for execution).
* Broker-specific facts are documented from official Upstox documentation with
  verbatim quotes; the discrepancies and unverified behaviors are recorded as
  CONCERN items requiring implementation-time verification.
* Full regression: 6066 passed / 6 skipped / 2 warnings. Frozen Checkpoints
  10–16 untouched. Security sweep clean.

**LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.8.**

A PASS WITH LIMITATIONS does NOT mean the system is authorized to trade, does
NOT mean live broker connectivity is proven, and does NOT authorize real-money
orders. The repository remains offline, paper/simulation-only, and
fail-closed by construction.
