# CHECKPOINT 18.1 — CONTROLLED BROKER CONNECTIVITY & EXECUTION READINESS AUDIT

## 1. Checkpoint 18.1 status

**COMPLETE / PASS WITH LIMITATIONS**

- Checkpoint 18.1 is an AUDIT / READINESS checkpoint. No production code was
  changed. No real broker connection was established. No order was submitted.
  No live execution was activated.
- No controlled Upstox Sandbox/Paper execution credential is available in
  this environment. `UPSTOX_ANALYTICS_TOKEN` (a historical-DATA credential)
  IS present but is intentionally NOT used for execution (rule #18). The
  execution credential `UPSTOX_EXECUTION_ACCESS_TOKEN` is NOT set. The
  repository-wide opt-in gate `CHECKPOINT_17_8_REAL_BROKER` is NOT set.
- Consequently every real-broker online behavior remains **NOT VERIFIED**
  and controlled connectivity is **NOT VERIFIED** (there was nothing to
  connect to, and no connection was attempted).

## 2. Overall verdict

**PASS WITH LIMITATIONS**

The Trading Intelligence Engine is *architecturally* ready to ship a
controlled Upstox Sandbox/Paper connectivity stage: every frozen Checkpoint
17 boundary remains intact, the adapter/broker-client boundary is the only
place where broker-specific anything may exist, all order-affecting paths
are unreachable from production code, and the fail-closed, UNKNOWN →
RECONCILE discipline is preserved end-to-end.

The limitation is that *real* broker behavior (HTTP transport, real response
shapes, real order retention, live rate limits, broker-side idempotency)
remains **NOT VERIFIED** because no controlled sandbox credential exists.
A PASS here does NOT authorize live trading; it only means the architecture is
safe and ready for the next controlled stage (Checkpoint 18.2), and that the
next stage must run inside the existing opt-in gate with a genuine
sandbox/paper credential supplied by the operator.

## 3. Objective

Answer, with evidence:

> Is the Trading Intelligence Engine actually ready to establish a controlled
> Upstox Sandbox/Paper connection without weakening or bypassing any of the
> frozen Checkpoint 17 boundaries?

## 4. Scope

- Audit of repository state, frozen Checkpoint 17 integrity, credentials,
  authentication, network/transport boundary, real API request/response
  readiness, broker order identity, idempotency, reconciliation, submission
  lifecycle, cancellation, rate limiting, retry, execution gate,
  paper/live isolation, observability/audit.
- Offline / mock / official-documentation validation only.
- **NOT in scope:** live trading, live order submission, broker connection,
  credential acquisition, production code changes.

## 5. Frozen baseline

Frozen and MUST NOT be reopened (per AGENTS.md):

- Checkpoints 10–16 (research pipeline, evidence chain, operational intent,
  execution authorization, execution command).
- Checkpoint 17 (17.1 boundary audit, 17.2 broker-adapter contract +
  submission lifecycle contract, 17.3 infrastructure, 17.4 reference adapter,
  17.5 real-broker preparation, 17.6 Upstox design, 17.7 Upstox adapter
  implementation, 17.8 sandbox/paper validation boundary, 17.9 execution-gate
  hardening).

Baseline (AGENTS.md Checkpoint 17.9): 6,086 tests passed, 0 failed,
2 pre-existing deprecation warnings; Checkpoint 17.9 verbatim:
"Final verdict: **PASS WITH LIMITATIONS** … ready to be frozen with a
separately specified live-execution gate … real Upstox execution is NOT fully
verified (no sandbox credential, mock-only)."

## 6. Repository state

- Repo root: `/workspace/project/trading_intelligence_engine`
- Branch `master`; `git rev-parse --is-shallow-repository` = true
  (single grafted commit `137be3b` "Checkpoint 17.9 - Final hardening +
  execution-gate audit").
- `git status --short` → empty. `git diff --name-only` → empty.
- Before this checkpoint there was already a `controlled_broker_validation.py`
  module (plain `src/engine/intelligence/`; the module docstring names
  Checkpoint 17.8/17.9 — VERIFIED the OPT-IN machinery was put on the
  filesystem at 17.9, not by 18.1).
- Frozen docs present: `docs/checkpoint_17_1…17_9*.md`.

## 7. Checkpoint 17 integrity

VERIFIED FROM CODE (working-tree clean, no commit, no tracked-file diff, and
all Checkpoint 17 test suites pass unchanged):

- Checkpoints 10–16: unchanged (no tracked file modified).
- `ExecutionCommand` (src/engine/models/execution_command.py): unchanged,
  `command_id = "cmd-" + sha256[:16]`, AUTHORIZED-only factory,
  execution_mode derived from authorization scope.
- `ExecutionAuthorization` (model + engine): unchanged; explicit
  authorization workflow; no production caller of `authorize`.
- `TradePlan` (src/engine/models/trade_plan.py): unchanged.
- `BrokerAdapter` contract (models/broker_adapter.py + intelligence/
  broker_adapter_contract.py): unchanged; `create_execution_command` /
  `select_adapter` / `validate_adapter_mode` intact.
- `SubmissionLifecycle` (model + engine): unchanged.
- `SubmissionInfrastructure` (intelligence/broker_adapter_infrastructure.py):
  unchanged.
- Checkpoint 17.4 `ReferenceBrokerAdapter`: intact, network-free.
- Checkpoint 17.7 Upstox adapter: intact, contract-compatible
  (17.7 suites pass: 166 tests).
- Checkpoint 17.9 execution gate: intact, `execution_gate.py` referenced
  only by its module and tests; NOT wired into any submission path.

Frozen-test evidence: `test_checkpoint_17_2*`, `test_checkpoint_17_3*`,
`test_checkpoint_17_4*`, `test_checkpoint_17_7_*`, `test_checkpoint_17_8_*`,
`test_checkpoint_17_9_hardening.py` — see section 31.

## 8. Architecture diagram

Frozen architecture verified to remain intact:

```
Trading Intelligence
    ↓
TradePlan
    ↓
OperationalTradeIntent (Checkpoint 14)
    ↓
Execution Authorization (Checkpoint 15)
    ↓
ExecutionCommand (Checkpoint 16; command_id = cmd-<sha256[:16]>)
    ↓
SubmissionInfrastructure (Checkpoint 17.3)
    ↓
BrokerAdapter (broker-neutral contract, Checkpoint 17.2)
    ↓
UpstoxBrokerAdapter (Checkpoint 17.7, translation boundary only)
    ↓
UpstoxBrokerClient (Protocol / Mock — the ONLY module allowed to own
                    transport + Authorization header in a future build)
    ↓
Transport (NOT IMPLEMENTED in 17.x/18.1; network-free by construction)
    ↓
Controlled Upstox Environment (SANDBOX/PAPER, future)
```

Verified single-responsibility chains:

- Strategy cannot submit orders: no route from any analytics/decision module
  to `submit()`/`submit_command()`/`place_order()`.
- TradePlan cannot submit orders: `plan_trade`/`TradePlanningEngine.plan`
  never call submission code.
- ExecutionCommand cannot submit itself: the model has no execution-sending
  method; only `create_execution_command` constructs it (tests only).
- BrokerAdapter cannot authorize: the protocol has no authorize/
  create_authorization/grant method.
- UpstoxBrokerAdapter cannot authorize: adapter has no reference to the
  authorization engine.
- BrokerClient cannot authorize or modify trading decisions: the client
  protocol exposes only `place_order/get_order/cancel_order/check_health`;
  it never receives decision data beyond the translated request.
- Persistence cannot authorize: stores only save/load/list/delete.
- Reconciliation cannot authorize: `reconcile_submission` only queries the
  adapter with the same client_order_id.
- Recovery cannot bypass authorization: `restart_recovery`/
  `recovery_for_command` return a read-only decision; no auto-submit.
- ExecutionGate remains the final safety boundary: pure evaluation function,
  DISABLED by default (see section 24).
- Credentials remain outside the domain model (see sections 10, 19).
- Broker-specific models remain outside core domain models:
  `upstox_broker_models.py` appears only inside the adapter/client tree.

## 9. Controlled environment definition

What qualifies as a controlled broker environment (documentation-only,
consistent with Checkpoint 17/17.8 split):

- **Sandbox vs production:** Sandbox = Upstox's official Sandbox app;
  "closely emulates the actual API integration experience", "without
  incurring any costs and without any time restrictions",
  "before executing actual orders in the live market" (VERIFIED FROM OFFICIAL
  UPSTOX DOCUMENTATION, sandbox doc). Production = real money = NEVER in
  scope of upcoming controlled stages.
- **Paper vs live:** the architecture's `ExecutionMode.PAPER` maps to
  controlled/simulated; `ExecutionMode.LIVE` is fail-closed everywhere in
  18.1.
- **Broker identity:** `"upstox"` (`UPSTOX_BROKER_IDENTITY` in both the
  adapter and controlled_broker_validation).
- **Account identity:** must be the sandbox/paper account; account-bound
  identifiers are not modeled yet (DEFERRED to the gate / 18.2).
- **Environment identity:** `SANDBOX` / `PAPER` are the only recognized
  controlled kinds; `UNKNOWN` fails closed; `LIVE/PROD/REAL` are listed as
  never-controlled (VERIFIED FROM CODE in controlled_broker_validation).
- **API base URL / environment identity:** documented official references
  (`OFFICIAL_UPSTOX_REFERENCE_URLS`; e.g. place-order lives at
  `https://api-hft.upstox.com/v3/order/place` — VERIFIED FROM OFFICIAL
  DOCUMENTATION). No code contains a real executable URL.
- **Credential type:** Upstox access token (OAuth-style Bearer), 30-day
  validity per sandbox doc ("Copy this token, which will be valid for
  30 days" — VERIFIED FROM OFFICIAL UPSTOX DOCUMENTATION).
- **Credential source:** `UPSTOX_EXECUTION_ACCESS_TOKEN` env var via
  `EnvironmentUpstoxCredentialProvider` (lazy, never stored); injected into
  the client. Historical `UPSTOX_ANALYTICS_TOKEN` is NEVER used for execution.
- **Credential lifetime / rotation:** caller/env-managed; provider reads
  lazily so rotation is picked up; rotation does not change command/client/
  idempotency identities (VERIFIED BY TEST in 17.7).
- **Credential revocation:** fail-closed (empty→AUTHENTICATION_FAILURE);
  documented future gate precondition.
- **Required permissions/scopes:** authorization `scope` must yield
  `"paper"`/`"live"`; scope is the single execution-mode source.
- **Required instrument permissions:** instrument token must be in the
  verified `_DEFAULT_UPSTOX_INSTRUMENT_KEY_MAP`; unknown → fail closed.
- **Required trading permissions:** gate condition `capability_support`
  (SUBMIT+RECONCILE; CANCEL capability-gated).
- **Required account state:** gate condition `broker_health_readiness` +
  `no_outstanding_unknown` + `reconciliation_readiness` (design).

The system NEVER infers "credential exists = safe to trade". VERIFIED FROM
CODE: the gate treats `valid_live_credential` as one of 20 simultaneous
conditions, and the 17.8 startup guard lists credential availability as
check #5 of 10 with an explicit note "credentials exist" NEVER means "safe
to trade".

Minimum conditions for a controlled environment (documented): opt-in gate
`CHECKPOINT_17_8_REAL_BROKER=1` AND a separately supplied controlled
(sandbox/paper) credential AND guarded startup passing all 10 checks AND
environment kind ∈ {SANDBOX, PAPER} AND mode == PAPER AND adapter mode-match
AND, for ever reaching an order, the full 20-condition execution gate.

## 10. Credential boundary

```
Credential source (env / provider)
    ↓
Credential Provider (protocol; Environment/Static/Empty)
    ↓
UpstoxBrokerClient (Mock in 17.x/18.1)
    ↓
Transport (NOT IMPLEMENTED)
```

VERIFIED FROM CODE + BY TEST:

- Credentials NEVER appear in `ExecutionCommand`, `TradePlan`, authorization,
  lifecycle state, submission persistence, audit records, broker-neutral
  `AdapterResult`, logs, exceptions, fixtures, reports, AGENTS.md, or git:
  grep/AST sweep over execution modules found zero credential reads outside
  `upstox_credential_provider.py` and zero hard-coded secret literals.
- Only credential-typed env name referenced in execution code:
  `UPSTOX_EXECUTION_ACCESS_TOKEN` (name only, no default, no dump). The
  historical `UPSTOX_ANALYTICS_TOKEN` is read ONLY by the read-only
  historical provider (Checkpoint 3A/6B) — never by execution code.
- Redaction VERIFIED (17.7 tests): `redact_sensitive()` scrubs
  `Bearer <token>` and `UPSTOX_*` env-name patterns from every reason string
  before anything reaches a result/error/persistence.
- Missing credentials fail closed: empty provider → `AUTHENTICATION_FAILURE`
  before any operation (17.7 tests + this run's probe: empty provider yields
  `_token_available() is False`).
- Invalid credentials fail closed: `authentication_failure` scenario in the
  mock client → `AUTHENTICATION_FAILURE`; never a submitted/failed order.
- Expired/revoked credentials fail closed: same path — the provider yields
  empty/updated value, client `_token_available()` False and `check_health`
  False; documented in 17.9 gate design (credential-validation gate
  precondition).
- Credential rotation does not alter command identity: VERIFIED BY TEST —
  identity excludes any credential; `command_id`/`client_order_id`/
  `idempotency_key` are pure functions of immutable command content.

## 11. Authentication readiness

VERIFIED FROM CODE + BY TEST (offline):

- Safe authentication path exists only in the credential-provider +
  client-boundary design; NO handshake is implemented (no transport). The
  mock client authenticates by checking provider token presence and supports
  an `authentication_failure` scenario.
- Authentication errors: `AUTHENTICATION_FAILURE` → `BrokerErrorCategory
  TRANSPORT`; never a FAILED/EXECUTED order.
- Unauthorized responses: `AUTHORIZATION_FAILURE` category exists.
- Expired-credential behavior: provider yields empty → fail closed.
- Malformed authentication responses: no authentication response parsing
  exists (no transport); the mock deterministically returns the tagged
  failure. If a mutation/unknown response were received it normalizes to
  UNKNOWN (reconcile) — never to success.
- Transport failures during authentication: not testable without transport
  → **NOT VERIFIED** (DEFERRED to 18.2).
- Retry behavior: authentication failure is never an order; `TRANSPORT`
  retryable flag True is intended only for a fresh attempt AFTER
  reconciliation determines no order reached the broker.

**Authentication failure fails closed — VERIFIED BY TEST**:
`authentication_failure` scenario never produces a submitted/failed order.

**Real authentication — NOT VERIFIED** (no controlled credential + no
transport client).

## 12. Network / transport boundary

VERIFIED FROM CODE + AST sweep (this run):

- Zero network imports (`requests`/`httpx`/`urllib`/`socket`/`aiohttp`/
  `http`/`ssl`/`websocket`) across: `broker_adapter.py`,
  `execution_command.py`, `execution_authorization.py`,
  `operational_trade_intent.py`, `submission_lifecycle.py`,
  `broker_adapter_contract.py`, `submission_lifecycle.py`,
  `broker_adapter_infrastructure.py`, `upstox_broker_adapter.py`,
  `upstox_broker_client.py`, `upstox_broker_models.py`,
  `upstox_credential_provider.py`, `execution_gate.py`,
  `execution_authorization.py` (engine), `operational_trade_intent*.py`,
  `controlled_broker_validation.py`, `reference_broker_adapter.py`,
  `fake_broker.py`, all `src/engine/persistence/*.py`.
- Only network-bearing module in the repo is the read-only historical
  data provider (`src/engine/data/historical_provider.py`, urllib,
  Checkpoint 3A/6B), which is NOT part of the execution tree and is never
  imported by any execution module.
- No network calls in core domain code, ExecutionCommand, authorization,
  TradePlan, SubmissionLifecycle, persistence, or ExecutionGate.
- Broker-specific network behavior is isolated to the future
  `UpstoxBrokerClient`-implementing transport (documented; not implemented).
- Timeout/connection/DNS/TLS handling: only described in the adapter-design
  contract (17.6) — timeout→TIMEOUT/AMBIGUOUS→UNKNOWN→RECONCILE;
  connection-refused/DNS/TLS → NETWORK_FAILURE/TRANSPORT. **Real transport
  behavior NOT VERIFIED** (no network client exists).
- Malformed responses / unexpected statuses: malformed envelope →
  `MALFORMED_RESPONSE` → AMBIGUOUS → UNKNOWN (VERIFIED BY CODE in
  `_normalize_*` and mock `malformed_response` scenario).
- Transport failures are NEVER converted into confirmed order failure
  (VERIFIED BY CODE: RATE_LIMIT submit → UNKNOWN; TIMEOUT → UNKNOWN;
  unknown_outcome → UNKNOWN).

## 13. Real API request readiness

The adapter translates (VERIFIED FROM CODE in `_translate_command` and
`_validate_*`):

| Field | Source | Broker representation | Validation | Transformation | Rounding | Failure behavior | Semantic loss? | Capability |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| instrument_token | command.instrument via verified map | `NSE_EQ\|INE...` / `NSE_INDEX\|Nifty 50` | must be mapped & pass token prefix check | none (identity) | none | ValueError (fail closed, ungessed) | required |
| transaction_type | command.direction | `_DIRECTION_TO_TRANSACTION_TYPE` (LONG→BUY, SHORT→SELL) | direction must be mapped | none | none | ValueError | none | required |
| quantity | command.quantity (Decimal) | integer units | positive; never increased | lot-multiple FLOOR only | floor; floors-to-zero→fail closed | ValueError | none (documented floor policy) | required |
| product | config first scope | `D` (delivery) | enum only | none | none | UNSUPPORTED_ORDER_SEMANTICS (no fallback) | single-product first scope | required |
| validity | config first scope | `DAY` | enum only | none | none | fail closed (no fallback) | single-validity first scope | required |
| order_type | entry presence → LIMIT | `LIMIT` (first scope); MARKET/ SL/ SL-M capability-gated | generic order semantic validated | none | none | fail closed; NO automatic fallback | documented (SL/SL-M future) | required |
| price | command.entry (verbatim) | limit price | positive Decimal | none | none | fail closed | none | required |
| trigger_price | command.stop (verbatim) | stop/trigger price | SL geometry vs entry | none | none | fail closed | none | required for SL |
| exchange/segment | token prefix | inferred from instrument token | `_validate_token_prefix` | none | none | fail closed (no default exchange) | none | required |
| tag / client_order_id | deterministic identity | `uptag-` + sha256(client_order_id)[:12] (18 chars) | non-empty | deterministic mapping | none | n/a | tag bounded; never truncates underlying identity | required |
| disclosed_quantity | N/A | 0 (placeholder in adapter-owned request) | N/A | N/A | N/A | N/A | not transmitted (documented) | N/A |

Documented official constraints (VERIFIED FROM OFFICIAL DOCUMENTATION):
place-order response `data.order_ids` is an ARRAY; `tag` "exceeds the
permitted limit of 40 characters" (UDAPI1119); `order_type ∈
{MARKET,LIMIT,SL,SL-M}`; `product ∈ {I,D,MTF}`; `validity ∈ {DAY,IOC}`;
`transaction_type ∈ {BUY,SELL}`; API base `https://api-hft.upstox.com/v3/
order/place`; quoting `instrument_token` examples `NSE_FO|43919`;
`market_protection` / `is_amo` / `slice` are optional fields not used by the
adapter (documented, not silently substituted).

Adapter tag check vs documented 40-char limit: `uptag-`(6) + 12 hex = 18
chars ≤ 40 — **computationally VERIFIED this run**; the 17.6 note "tag length
REQUIRES IMPLEMENTATION-TIME VERIFICATION" remains (the official doc now
publishes the 40-char limit, classified VERIFIED FROM OFFICIAL DOCUMENTATION;
runtime conformance of a real sandbox remains NOT VERIFIED).

No silent field discard / semantic substitution / guessed instrument /
automatic order-type/product/exchange fallback / quantity increase / silent
price-trigger rounding — VERIFIED FROM CODE (fail-closed validation in
`_validate_capability`, `_validate_order_semantics`, `_translate_command`).

## 14. Real API response readiness

Classified against official Upstox documentation (fetched during this
checkpoint) and the adapter code:

1. **place-response order_ids shape** — VERIFIED FROM OFFICIAL DOCUMENTATION:
   V3 response body `{"status":"success","data":{"order_ids":[...]},
   "metadata":{"latency":30}}`. The adapter handles a single-id success;
   a multi-id response → UNKNOWN/MALFORMED_RESPONSE (CONCERN documented at
   17.8 #1; adapter is correctly defensive).
2. **order-history array vs single record** — VERIFIED FROM OFFICIAL
   DOCUMENTATION prose for the tag in order history ("assign a tag(unique
   identifier) to your order, allowing you to retrieve orders associated
   with that tag using the Order History API"); precise array-vs-single
   shape NOT VERIFIED (needs controlled response parsing; DEFERRED).
3. **one-trading-day retention** — NOT VERIFIED against the live/sandbox
   (no controlled session); documented limitation retained.
4. **full order-state vocabulary** — VERIFIED FROM OFFICIAL DOCUMENTATION
   that the status table includes `validation pending`, `modify pending`,
   `trigger pending`, `put order req received`, `open`, `complete`, `modified`,
   `not cancelled`, `cancel pending`, `rejected`, `cancelled`, `open pending`,
   `not modified`, plus AMO variants. The adapter maps a SUBSET
   (`open/accepted/complete/cancelled/rejected/partially_filled`) and maps
   any unrecognized string to UNKNOWN (fail closed). The mapping table below
   classifies the two published entries that map cleanly; the rest need
   controlled-page confirmation (DEFERRED).
5. **tag length/character restrictions** — VERIFIED FROM OFFICIAL
   DOCUMENTATION: `UDAPI1119` "tag length exceeds limit … permitted limit of
   40 characters". Adapter tag is 18 chars (verified <= 40). Runtime
   conformance NOT VERIFIED.
6. **broker-side idempotency** — NOT VERIFIED. No official doc claim exists
   that a tag/client_order_id dedupes at the broker. Retained policy:
   **UNKNOWN → RECONCILE → DECIDE**; no broker-idempotency claim.
7. **live authentication behavior** — NOT VERIFIED.
8. **live rate-limit behavior** — NOT VERIFIED.
9. **real HTTP timeout behavior** — NOT VERIFIED (no transport client).

Order-state → BrokerResultStatus map (VERIFIED FROM CODE):
`open`→SUBMITTED, `accepted`→ACCEPTED, `complete`→FILLED,
`cancelled`→CANCELLED, `rejected`→REJECTED, `partially_filled`→PARTIALLY_FILLED,
anything else→UNKNOWN (reconcile). `cancelled`/'complete'/'rejected' publish
exact documented strings; the rest require controlled mapping (documented).

Validation is never weakened for a plausible response (VERIFIED FROM CODE):
`UpstoxPlaceOrderResponse.__post_init__` rejects `success` without
`order_data`, `success` carrying error fields, `error` carrying data, and
`error` without code/message — a "success envelope with error fields" is
malformed → UNKNOWN.

## 15. Broker identity

- Canonical broker identity is `"upstox"` (adapter + guarded validation).
- No code detects/trusts any other broker. `select_adapter` matches on
  execution_mode only and never treats a different broker name as equivalent.
- `live_mode_hard_gate` treats a LIVE environment name reported when a
  controlled environment is required as a SAFETY FAILURE (Documented, tested
  in 17.8).
- Identity chains remain deterministic and restart-stable.

## 16. Broker order identity

The four identities remain separate (VERIFIED FROM CODE):

- `command_id` = `cmd-` + sha256[:16] of canonical command payload (model).
- `client_order_id` = `co-` + sha256[:16] of (command_id, broker_context)
  (contract); same for every submission of the same command; different per
  broker_context.
- `idempotency_key` = `idem-` + sha256[:16] (separate string; application
  duplicate detection only).
- `broker_order_id` = broker-generated (mock: deterministic `upstox-mock-…`;
  real: broker-provided `data.order_ids`). Downstream-only; never inserted
  into upstream artifacts.
- `submission_id` = `submission-` + sha256[:16]; the snapshot record binding
  to command_id.
- Upstox `tag` = `uptag-` + sha256(client_order_id)[:12] — the broker-facing
  application identity used for reconciliation (deterministic + invertible).

VERIFIED BY TEST (17.7/17.9): deterministic identity; restart stability;
reconciliation uses the SAME client_order_id/tag; different broker_contexts →
different client_order_id (paper/live separation).

## 17. Idempotency

Five-level distinction preserved:

1. `command_id` — immutable core identity.
2. `client_order_id` — deterministic broker-facing identity (restart-stable).
3. `idempotency_key` — separate deterministic key for local dedupe.
4. `broker_order_id` — broker-generated, downstream-only.
5. **Broker-side idempotency — NOT VERIFIED**; NEVER claimed. The contract
   explicitly documents that a deterministic application-level id does NOT
   by itself guarantee broker-side idempotency, and that the broker-facing
   adapter must use broker-side mechanisms where the broker contract
   verifiably provides them.

Policy: **UNKNOWN → RECONCILE → DECIDE**; blind retry after UNKNOWN is
prohibited (`request_submission` on UNKNOWN raises ValueError). VERIFIED FROM
CODE + BY TEST.

## 18. Reconciliation

VERIFIED FROM CODE + BY TEST:

- Primary lookup: deterministic Upstox `tag` (derived from client_order_id).
  Fallback: `broker_order_id` passed by the integration layer (documented;
  adapter boundary currently resolves from the mock order book; a real
  integration supplies the broker order id from the lifecycle).
- Exactly one match → confirmed state mapping.
- No match → record returns explicitly unknown/absent (never false success).
- Multiple matches / conflicting matches → UNKNOWN (never arbitrary choice).
- Malformed response → MALFORMED_RESPONSE/AMBIGUOUS → UNKNOWN.
- Stale response / missing order / broker retention expiry → UNKNOWN
  (documented; one-trading-day retention is a documented limitation).
- UNKNOWN state → stays UNKNOWN; retry prohibited; recovery → RECONCILE_REQUIRED.
- Network timeout → UNKNOWN; authentication failure → AUTHENTICATION_FAILURE
  (TRANSPORT); broker unavailable → BROKER_UNAVAILABLE (TRANSPORT).
- Reconciliation NEVER submits a new order (mock assertions prove zero new
  submissions on reconcile; VERIFIED BY TEST).

## 19. UNKNOWN handling

VERIFIED FROM CODE + BY TEST:

- timeout → UNKNOWN (never FAILED).
- ambiguous (rate-limit submit, unknown outcome, malformed response) →
  UNKNOWN (reconcile).
- UNKNOWN persists and survives restart; `restart_recovery(UNKNOWN)` →
  RECONCILE_REQUIRED; `recovery_for_command` marks `reconciliation_required`.
- No blind retry: `SubmissionLifecycleEngine.request_submission` raises on
  UNKNOWN; `submit_command` raises `ReconciliationRequiredError`.
- UNKNOWN is never converted into success without broker confirmation.

## 20. Submission lifecycle

VERIFIED FROM CODE + BY TEST:

```
CREATED → SUBMISSION_REQUESTED → SUBMITTED / UNKNOWN
         → ACCEPTED / REJECTED / FAILED / FILLED / CANCELLED
```

- timeout/ambiguous → UNKNOWN (never FAILED).
- UNKNOWN survives restart and requires reconciliation.
- No blind retry.
- Terminal states never resubmit (submit_command returns existing unchanged).
- Duplicate command cannot create another submission (in-process
  `DuplicateSubmissionError` + restart `command_exists` + store
  `load_by_command` single-record guard).
- Same command preserves same identity (command_id/client_order_id).

## 21. Cancellation

VERIFIED FROM CODE + BY TEST:

- Cancellation is capability-gated (`supports_cancel`; raises ValueError if
  CANCEL not advertised).
- Correct broker order identity used: resolved from the client order book via
  the tag; real integration would use the lifecycle `broker_order_id`.
- Cancellation is never confused with the original submission (separate
  `cancel()` call + separate `request_cancellation` semantics).
- Cancellation timeout → UNKNOWN (cancellation_timeout scenario).
- Cancel/fill race is fail-closed: `cancellation_race_fill` → cancel error →
  REJECTED (fill authoritative; never false cancellation).
- Already-cancelled → deterministic CANCELLED.
- Broker rejection of cancellation → normalized REJECTED.
- Ambiguous cancellation outcome → UNKNOWN; no blind cancellation retry.

## 22. Rate limiting

VERIFIED FROM CODE + BY TEST:

- Rate-limit on SUBMISSION is treated as AMBIGUOUS → UNKNOWN (broker may have
  accepted) → RECONCILE → DECIDE. Never FAILED, never retried blindly.
- `BrokerErrorCode.RATE_LIMIT` → category TRANSPORT, retryable True (safe only
  as a fresh GET/reconcile attempt after the outcome is determined).
- No automatic retry loops (documented bounded retry budget concept only).

## 23. Retry policy

VERIFIED FROM CODE:

- SAFE retries: GET/reconciliation/read-only health queries.
- UNSAFE: ambiguous order submission; ambiguous cancellation; any operation
  whose broker outcome is unknown.
- The adapter/infrastructure never auto-retries; UNKNOWN always requires
  reconciliation; `retryable` flag is documented to be interpreted WITH
  lifecycle state ("reconcile first").
- No infinite loops: no retry builder exists anywhere in execution code.

## 24. Execution gate

VERIFIED FROM CODE + BY TEST + this run's probes:

- Gate remains DISABLED by default; `LiveExecutionGateState.DISABLED` is the
  resting state; no `live_enabled` shortcut exists.
- 20 `MANDATORY_GATE_CONDITIONS` include: explicit_live_mode, correct_broker,
  correct_adapter, valid_live_credential, credential_provenance,
  authorization_state, command_validity, risk_quantity_constraints,
  capability_support, environment_identity, operator_explicit_authorization,
  execution_gate_enabled, startup_safety_checks, broker_health_readiness,
  reconciliation_readiness, audit_readiness, no_outstanding_unknown,
  no_conflicting_recovery, no_configuration_ambiguity, no_safety_override_active.
- The gate blocks on: operator authorization absent, gate disabled, wrong
  broker, wrong adapter, wrong environment, wrong execution mode, missing
  credential, invalid credential, paper/live mismatch, reconciliation
  required, UNKNOWN unresolved, unsafe retry, required capability unavailable,
  required configuration missing, environment identity unknown, broker
  identity unknown, adapter identity unknown, required safety checks failing —
  each as a blocking reason; missing keys are treated as False.
- **Credentials are necessary but NOT sufficient** — VERIFIED this run:
  `LiveExecutionGateInput(conditions=(valid_live_credential=True,
  credential_provenance=True), gate_enabled=False,
  explicit_operator_authorization=False)` → `NOT_ALLOWED` with 18 blocking
  reasons. ALL-conditions-true + gate_enabled + operator authorization →
  `ALLOWED` (architecture test only; NOT activated).
- Gate is NOT wired into any submission path (`grep execution_gate src/` →
  only the module itself; tests). No automatic authorization added; no
  temporary bypass exists.

## 25. Paper/live isolation

VERIFIED FROM CODE + BY TEST + build-decision (this run):

- PAPER cannot fall back to LIVE and LIVE cannot fall back to PAPER:
  `validate_adapter_mode` raises on mode mismatch; `select_adapter` matches
  only the command mode and raises when no adapter matches; `live_mode_hard_gate`
  treats a LIVE report under a controlled expectation as a SAFETY FAILURE.
- Missing live configuration cannot silently select paper and vice versa:
  selection is exclusive by mode; no adapter is "default".
- Adapter mode is immutable: `execution_mode` bound at construction; no setter.
- `cp17_mode` is bound and validated: stored on every lifecycle created via
  the infrastructure; every reconcile/cancel resolves the adapter by the
  recorded mode and fails closed when missing or unmatched.
- Wrong environment fails closed (17.8 guard checks 6–7).

## 26. Persistence

VERIFIED FROM CODE + BY TEST:

- Three persistence boundaries: authorization store (15.5), command store
  (16.5), submission store (17.2) — each schema-version 1, atomic
  same-dir `mkstemp` + `fsync` best-effort + `os.replace`, safe-id regex,
  typed exceptions, corrupt/identity-mismatch → typed integrity error.
- Submission store enforces exactly one active record per command
  (`load_by_command` raises `SubmissionIntegrityError` on duplicates;
  `command_exists` guards restart duplicate submission).
- Persisted artifacts are broker-neutral: no SDK objects, no credentials, no
  URLs, no broker order models.

## 27. Restart recovery

VERIFIED FROM CODE + BY TEST:

- CREATED / SUBMISSION_REQUESTED(pre_sub=True) → SAFE_TO_SUBMIT (broker never
  contacted).
- SUBMISSION_REQUESTED(pre_sub=False) / SUBMITTED / UNKNOWN →
  RECONCILE_REQUIRED.
- Terminal → NO_ACTION.
- No auto-submit / auto-reconcile; recovery is a read-only decision view.
- Ambiguous persisted state (two snapshots for one command, e.g. crash between
  persist-and-delete) → fail-closed, audit `duplicate_commands` labels them,
  manual review required.

## 28. Auditability

VERIFIED FROM CODE + BY TEST:

- `SubmissionInfrastructure.audit()` reconstructs per command: command_id,
  submission_id, state, client_order_id (derived), idempotency_key (derived),
  requires_reconciliation, reconciliation_performed, retry_allowed, terminal,
  pre_submission, created_at, event_count, last_reason — all from persisted
  broker-neutral records; no credentials.

## 29. Observability

VERIFIED FROM CODE:

- Lifecycle states and recovery actions distinguishable; normalized result +
  error category/code on every AdapterResult; broker-neutral reason strings.
- Never recorded: access token, Authorization header, credential value,
  private broker client internals. `redact_sensitive` enforces this in every
  reason string; tests assert zero token leakage through results/errors/
  persistence/requests.

## 30. Security / threat model

Repository-wide sweep this run:

- Upstox SDK imports: **ZERO** (grep + AST over exec tree).
- Network imports in execution modules: **ZERO** (AST).
- HTTP clients in execution modules: **ZERO**.
- URLs in execution code: only documentation strings inside
  `controlled_broker_validation.OFFICIAL_UPSTOX_REFERENCE_URLS` (no
  executable call).
- Credential env vars: only the name `UPSTOX_EXECUTION_ACCESS_TOKEN` in
  `upstox_credential_provider.py`; no default value, no dump, no logging.
- Bearer tokens / Authorization headers: literals exist only in the
  redaction REGEX and docstrings; no real token anywhere in src/.
- Hard-coded secrets: **ZERO** (grep `Bearer <token>|token=...` over src/).
- Live endpoints: **ZERO** executable.
- Paper/live fallback: **NONE** (mode-match exclusive).
- Automatic authorization: **NONE** (authorize has no production caller).
- Order submission outside adapter/client: **ZERO** matches.
- Order submission outside the execution gate: no submission path exists;
  the gate is not wired (design-stage safety boundary).
- Retry loops: **NONE**.
- Hidden network calls: **NONE** (AST sweep).
- Credential logging / persistence: **NONE** (17.7/17.9 tests).
- Broker-specific types leaking into core: **NONE** (`upstox_broker_models`
  confined to the adapter/client tree; import graph verified).
- Broker-specific exceptions escaping into core: **NONE** — every adapter
  path catches and normalizes to `AdapterResult` / typed `ValueError`/
  `TypeError`.

## 31. Testing

Baseline (Checkpoint 17 suites, this environment):

- Checkpoint 17.2–17.9 suites (13 files): **682 passed, 6 skipped**.
  (six skips = `test_checkpoint_17_8_real_broker_opt_in.py`, all gated by the
  opt-in flag and absent credential — correct opt-in behavior.)
- Frozen Checkpoint 14–16 execution suites + planning/paper + 17.8 offline:
  **1054 passed, 2 warnings**.

Full suite (this environment, optional deps installed):

- **6163 passed, 6 skipped, 2 warnings**.
- Warnings (pre-existing): StarletteDeprecationWarning (httpx with
  starlette testclient) + anyio BlockingPortal DeprecationWarning; both from
  third-party packages (fastapi/starlette/anyio), unrelated to this audit.
- Skipped: the 6 real-broker opt-in tests (correct — they must never run
  automatically; explicit skip reason recorded).
- **0 failures. No test was modified; none deleted; none weakened.**

New tests for 18.1: none required — this is an audit/readiness checkpoint
with zero production-code changes. Offline probes performed instead:
gate negative/positive matrix, empty-credential fail-closed, tag-length
bound, AST network/SDK sweep, order-submission call-site sweep, gate wiring
sweep.

Real broker test opt-in behavior verified: the 17.8 opt-in module requires
BOTH `CHECKPOINT_17_8_REAL_BROKER=1` AND a controlled credential; it cannot
execute in ordinary CI (module-level `skipif`); a LIVE env is FAIL CLOSED even
when enabled.

## 32. Full regression

- Full suite: **6163 passed / 0 failed / 6 skipped / 2 pre-existing warnings**.
- No pre-existing failures in this environment (optional deps installed).
- No regression vs the Checkpoint 17.9 baseline (6,086) — 77 additional tests
  exist in the suite set we can compare only loosely; the important fact is
  ZERO failures and ZERO frozen-suite regressions.

## 33. Controlled connectivity result

**NOT VERIFIED — NO CONTROLLED CREDENTIAL AVAILABLE; NO CONNECTION ATTEMPTED.**

Environment evidence (values never printed):

- `UPSTOX_ANALYTICS_TOKEN`: present — a historical-DATA credential (read-only
  historical provider). NOT an execution credential; deliberately NOT used
  for execution (rule #18).
- `UPSTOX_EXECUTION_ACCESS_TOKEN`: absent.
- `UPSTOX_SANDBOX_TOKEN` / `UPSTOX_PAPER_TOKEN` / `UPSTOX_BROKER_TOKEN`:
  absent.
- `CHECKPOINT_18_1_BROKER`: absent.
- `CHECKPOINT_17_8_REAL_BROKER=1`: not set.

Per Phase 17: perform only the safest checks (done offline), never fabricate
a credential, never use a live trading credential, never use the analytics
token for execution, and mark controlled connectivity NOT VERIFIED. The
opt-in scaffold (`tests/test_checkpoint_17_8_real_broker_opt_in.py`) is
present for a future operator with a genuine sandbox credential.

## 34. Verified behaviors (evidence)

VERIFIED FROM CODE / VERIFIED USING MOCKS / VERIFIED BY TEST:

- Dependency direction + single-responsibility chains (section 8). VERIFIED
  FROM CODE.
- AUTHORIZED-only command creation; intent binding + content fingerprint
  enforced (create_execution_command). VERIFIED FROM CODE.
- Mode-match exclusive adapter selection; immutable adapter mode; cp17_mode
  binding. VERIFIED FROM CODE + BY TEST.
- UNKNOWN → RECONCILE → DECIDE; no blind retry; terminal no-resubmit;
  duplicate rejection in-process + after restart. VERIFIED FROM CODE + BY TEST.
- Credential isolation + redaction; empty/invalid credential fail-closed
  (mock `authentication_failure`, `_token_available` False with Empty provider).
  VERIFIED BY TEST (17.7) + probe this run.
- Rate-limit/timeout/unknown → UNKNOWN (never failed), reconciliation with the
  same deterministic client_order_id. VERIFIED BY TEST.
- Cancellation capability gate + race/timeout/rejection normalization.
  VERIFIED BY TEST.
- Network-free execution tree (AST sweep, 19 modules, zero hits). VERIFIED
  this run.
- Execution gate negative/positive matrix + credentials-not-sufficient.
  VERIFIED BY TEST (17.9) + probe this run.
- Place-order response `data.order_ids` is an ARRAY; `tag` 40-char limit;
  order-type/product/validity vocabularies; status vocabulary includes
  `cancelled`/`complete`/`rejected`/`open`. VERIFIED FROM OFFICIAL
  DOCUMENTATION (fetched during 18.1).

## 35. Unverified behaviors (evidence)

NOT VERIFIED / DEFERRED (no controlled credential; no transport client):

- Real HTTP transport (connectivity, TLS, DNS), real timeouts.
- Real authentication handshake + token acceptance/revocation behavior.
- Real rate-limit behavior and any broker-side throttling semantics.
- Place-order single-vs-multi order_ids at runtime (array confirmed by docs;
  adapter handles both; runtime shape unconfirmed).
- Order-history array-vs-single-record shape.
- One-trading-day order retention and its effect on reconciliation windows.
- Full order-state vocabulary mapping (only `cancelled`/`complete`/`rejected`
  published strings map directly; `open` etc. require controlled mapping).
- Real tag character/encoding constraints beyond the 40-char limit.
- Broker-side idempotency (explicitly UNVERIFIED; never claimed).
- SL / SL-M trigger semantics against a live/sandbox session.
- Runtime cancellation behavior against a real order.

## 36. CONCERN findings

None block; all are documented limitations for the next controlled stage:

- C01 (17.9): no temporal re-validation of `valid_from`/`valid_until` at the
  submission boundary — the gate must enforce time validity.
- C02 (17.9): `check_health` exists on the client but is not consulted by the
  adapter/infrastructure before submit/reconcile/cancel — gate precondition
  `broker_health_readiness`.
- C03 (17.9): sandbox/paper environment identity is not modeled as first-class
  data (only strings in the guard) — gate condition `environment_identity`.
- C04 (17.9): multi-process concurrency not lock-protected (single-process
  serialization; documented).
- C05 (17.9/17.8): one-trading-day retention, real tag constraints,
  broker-side idempotency remain UNVERIFIED.
- C06 (17.9): the live Upstox adapter is mock-backed; no transport client
  exists.

The adapter's multi-order-id handling maps to UNKNOWN/MALFORMED_RESPONSE —
correct, and the only guard between a slicing response and a falsely assumed
single order.

## 37. BLOCKER findings

NONE. No frozen boundary is broken, bypassable, or requires unsafe change.

## 38. Known limitations

- No execution transport client (by design — next stage's first deliverable
  behind the opt-in gate).
- No live or sandbox credential available; no connectivity possible.
- Broker behaviors 35. remain unverified.
- `UPSTOX_ANALYTICS_TOKEN` is present but is out of scope for execution.
- The execution gate is design-only; nothing in the repository can place an
  order.
- Live-trading safety is NOT established (and not claimed).

## 39. Required follow-up

For Checkpoint 18.2 (and only on separate explicit authorization):

1. Provide a genuine Upstox Sandbox credential via
   `UPSTOX_EXECUTION_ACCESS_TOKEN` (30-day sandbox token) and enable
   `CHECKPOINT_17_8_REAL_BROKER=1` — the existing opt-in scaffold then runs.
2. Implement a real (but sandbox-only) HTTP transport implementing
   `UpstoxBrokerClient` behind the frozen protocol.
3. Verify: environment identity, broker identity, authentication, read-only
   capability (order history retrieval is safe), response normalization,
   credential redaction, connection-failure behavior, timeout→UNKNOWN,
   tag-based reconciliation, and one-trading-day retention effects.
4. Countersign with the 17.8 startup guard BEFORE any connectivity.
5. NEVER place a real order; sandbox so far, paper identity, PAPER mode.

## 40. Checkpoint 18.2 recommendation

RECOMMEND — CHECKPOINT 18.2 — CONTROLLED SANDBOX CONNECTIVITY IMPLEMENTATION &
READ-ONLY BROKER VERIFICATION — **only if separately authorized** and ONLY
with a genuine sandbox credential and the opt-in gate. The evidence supports
this: the boundary is ready, the opt-in scaffold exists, and the only missing
piece is a controlled session. Do NOT begin 18.2 automatically; do NOT
connect without the operator-supplied credential; never escalate to LIVE.

## 41. Final freeze decision

CHECKPOINT 18.1 IS FROZEN as **PASS WITH LIMITATIONS**.

LIVE TRADING IS NOT AUTHORIZED. CHECKPOINT 18.1 DOES NOT AUTHORIZE
CONNECTIVITY, SANDBOX TOKEN USE, OR ANY ORDER SUBMISSION. The only claim is:
the architecture is safe and ready for the next controlled stage; all
real-broker behavior is unverified and must be verified under the opt-in gate
with an operator-supplied sandbox credential.

## Appendix A — Evidence inventory

- `src/engine/models/execution_command.py`, `execution_authorization.py`,
  `operational_trade_intent.py`, `submission_lifecycle.py`, `broker_adapter.py`
- `src/engine/intelligence/broker_adapter_contract.py`,
  `submission_lifecycle.py`, `broker_adapter_infrastructure.py`,
  `reference_broker_adapter.py`, `fake_broker.py`,
  `upstox_broker_adapter.py`, `upstox_broker_client.py`,
  `upstox_broker_models.py`, `upstox_credential_provider.py`,
  `execution_gate.py`, `controlled_broker_validation.py`,
  `execution_authorization.py`, `operational_trade_intent*.py`
- `src/engine/persistence/{submission,execution_command,execution_authorization}_*.py`
- `src/dashboard/app.py` (introspection-only route) + `services.py`
- `tests/test_checkpoint_17_*` (all),
  `tests/test_{execution_authorization,execution_command,operational_trade_intent}*.py`
- `docs/checkpoint_17_1…17_9*.md`, AGENTS.md Checkpoint 17 history
- Official Upstox documentation (extracted during 18.1): sandbox,
  place-order V3, order-status appendix.