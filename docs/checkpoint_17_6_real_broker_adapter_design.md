# CHECKPOINT 17.6 — REAL BROKER ADAPTER DESIGN

**Status:** COMPLETE / PASS
**Date:** 2026-09-05
**Type:** DESIGN ONLY (no implementation)
**Target broker:** Upstox (official V3 REST trading API)

> **IMPORTANT SAFETY STATEMENT**: This checkpoint produced a design only. It
> did NOT connect to a broker, did NOT add a broker SDK, did NOT add
> credentials, did NOT make API calls, did NOT introduce a network path, and
> did NOT submit/cancel/modify/reconcile any real order. A design PASS means
> only that the real broker adapter has been sufficiently designed to proceed
> to the Checkpoint 17.7 implementation checkpoint. It does NOT authorize
> live trading, real broker connection, or real order submission.

Throughout this document, every statement is tagged:

- **ALREADY VERIFIED** — implemented and verified in the frozen 17.1–17.5
  architecture or verified from authoritative public Upstox documentation.
- **DESIGNED FOR 17.7** — an architecture/design decision documented here that
  the future 17.7 implementation checkpoint must implement.
- **OUT OF SCOPE** — intentionally not addressed by this checkpoint or by the
  17.7 implementation checkpoint (deferred; requires explicit authorization).

---

## 1. Checkpoint 17.6 Overview

Checkpoint 17.6 is the architectural and technical DESIGN checkpoint for the
first REAL broker-specific adapter. It stands on the frozen broker-neutral
execution architecture (Checkpoints 13–16, frozen) and the verified
broker-adapter boundary work (Checkpoints 17.1–17.5, PASS). It answers the
primary objective:

> "Exactly how will a real broker adapter be implemented without contaminating
> the core Trading Intelligence Engine or weakening any execution safety
> invariant?"

The output is an implementation-ready design. Checkpoint 17.7 is expected to
implement the design WITHOUT live submission (mocked broker client, no
credentials, no network) and must NOT reopen the architectural questions
resolved here.

## 2. Objective

Produce the complete, implementation-ready design for the first broker-specific
adapter — selected target broker: **Upstox** — such that a downstream
implementation checkpoint can build the adapter against the ALREADY-FROZEN
broker-neutral contract without:

1. contaminating the core Trading Intelligence Engine;
2. weakening any execution safety invariant (AUTHORIZED-only submission,
   command immutability, deterministic identity, UNKNOWN → reconcile-before-
   retry, paper/live isolation, fail-closed behavior);
3. requiring changes to the frozen generic `BrokerAdapter` contract;
4. introducing real broker connectivity, credentials, SDKs, or network before
   a separate explicit authorization.

## 3. Scope

**DESIGNED FOR 17.7 — IN SCOPE:**

- Concrete `UpstoxBrokerAdapter` architecture against the frozen contract.
- Complete translation boundary design (ExecutionCommand → Upstox request).
- Instrument / symbol mapping boundary.
- Order-type, product, validity, exchange mapping.
- Capability model requirements.
- Authentication / credential boundary and isolation.
- Client-order-ID strategy respecting broker constraints.
- Broker idempotency strategy (with honest unknowns).
- Submission protocol (every validation gate + failure behavior).
- Timeout / UNKNOWN / reconciliation design.
- Order-state and error mapping.
- Rate-limit and network-failure policy.
- Response validation contract.
- Cancellation design.
- Paper/live isolation, startup safety, live-execution guard chain.
- Observability, persistence, API-versioning, clock/timing requirements.
- Failure-injection test spec and contract-conformance strategy.
- Dependency direction and the 17.7 implementation blueprint.
- Upstox-specific design appendix (publicly-verified facts labeled).
- Security review and repository safety sweep.

**OUT OF SCOPE (17.6 AND 17.7):**

- Any real broker connection, HTTP/WebSocket broker communication, broker SDK
  installation/import, live/sandbox trading against a real external service.
- Any credential material (API keys, access tokens, client secrets, bearer
  tokens, passwords) in code, config, fixtures, or this document.
- Any real order submission / cancellation / modification / reconciliation /
  health check.
- Authorizing live trading (no authorization authority exists at the adapter
  boundary; authorization stays strictly upstream and frozen).
- A broker-neutrality change to the frozen contract.

## 4. Non-Goals

- NOT an implementation checkpoint. ZERO production-code changes and ZERO
  test-code changes are permitted (Section 45 confirms zero).
- NOT a live-trading go decision. A PASS here only permits proceeding to the
  implementation design work.
- NOT a guarantee of broker-side idempotency, fills, or prices.
- NOT a claim that any broker API capability exists unless verified from
  authoritative public documentation (Section 42).
- NOT an operational deployment/runbook (account provisioning, static-IP
  whitelisting, environment separation of live deployments).

## 5. Frozen Checkpoint Constraints

**ALREADY VERIFIED — the following are FROZEN and must not be reopened or
modified by 17.6 or 17.7:**

| Checkpoint | Boundary |
|-----------|----------|
| 10–12 | Historical/research/paper-trading governance (frozen) |
| 13 | Execution architecture boundary (frozen) |
| 14 | Operational Trade Intent (frozen) |
| 15 | Execution Authorization (frozen) |
| 16 | Execution Command + persistence (frozen) |
| 17.1 | Broker Adapter Boundary Architecture Audit (PASS) |
| 17.2 | Broker Adapter Contract & Execution Lifecycle Contract (PASS) |
| 17.3 | Broker Adapter Infrastructure & Submission Lifecycle (PASS) |
| 17.4 | First Concrete Broker Adapter / Reference Adapter & Contract Conformance (PASS) |
| 17.5 | Real Broker Integration Preparation & Broker-Specific Boundary Safety Audit (PASS WITH LIMITATIONS) |

Constraint effects on 17.6/17.7:

- `ExecutionCommand`, `ExecutionAuthorization`, `OperationalTradeIntent`,
  `SubmissionLifecycle`, `BrokerAdapter`, `AdapterResult`, `BrokerError`,
  `BrokerResultStatus`, `BrokerErrorCode`, `BrokerErrorCategory`,
  `AdapterCapabilities`, `select_adapter`, `validate_adapter_mode`,
  `derive_client_order_id`, `derive_idempotency_key`,
  `SubmissionLifecycleEngine`, `SubmissionInfrastructure`, submission/command/
  authorization stores: **ALREADY VERIFIED — immutable, broker-neutral,
  deterministic, fail-closed; no 17.7 change may alter their semantics.**
- The deterministic value chain is fixed:
  `command_id → client_order_id → idempotency_key` (all under the same
  `broker_context`); the adapter maps but never redefines these identities.
- Blind retry after UNKNOWN is PROHIBITED by the frozen contract.
- Paper/live mode cannot silently cross (mode derived from authorization
  scope; `validate_adapter_mode` + `select_adapter` + `cp17_mode` binding).

## 6. 17.1–17.5 Baseline

**ALREADY VERIFIED — summary of the verified baseline this design extends:**

| Checkpoint | Deliverable | Verdict |
|-----------|------------|---------|
| 17.1 | Broker Adapter Boundary Architecture Audit | PASS (16 PASS, 10 CONCERN design-time, 0 BLOCKER) |
| 17.2 | Broker Adapter Contract & Execution Lifecycle Contract | PASS (72 new tests; full suite 5551) |
| 17.3 | Broker Adapter Infrastructure, Submission Lifecycle Integration & Persistence | PASS (84 new tests; full suite 5635) |
| 17.4 | ReferenceBrokerAdapter & Contract Conformance | PASS (186 new tests; full suite 5823) |
| 17.5 | Real Broker Integration Preparation & Broker-Specific Boundary Safety Audit | PASS WITH LIMITATIONS (0 tests; full suite 5823 unchanged) |

Verified artifacts inspected for 17.6 (source-verified, not doc-only):

- `src/engine/models/execution_command.py` — immutable `ExecutionCommand`,
  `ExecutionMode`, deterministic `command_id`, factory enforces
  AUTHORIZED-only + intent/fingerprint binding.
- `src/engine/models/execution_authorization.py` — `authorization_id`,
  `status`, `scope`, deterministic identity, `create_authorization`.
- `src/engine/models/operational_trade_intent.py` — intent + deterministic
  `intent_id` / `content_fingerprint`.
- `src/engine/models/submission_lifecycle.py` — `SubmissionLifecycle` /
  `SubmissionEvent` / `SubmissionState` (deterministic `submission_id`,
  `client_order_id`, `broker_order_id` downstream-only).
- `src/engine/models/broker_adapter.py` — `BrokerResultStatus`,
  `AdapterResult`, `BrokerError`/`BrokerErrorCode`/`BrokerErrorCategory`,
  `AdapterCapability`, fail-closed envelopes.
- `src/engine/intelligence/broker_adapter_contract.py` — `BrokerAdapter`
  Protocol, `AdapterCapabilities`, `derive_client_order_id` /
  `derive_idempotency_key`, `validate_adapter_mode`, `select_adapter`.
- `src/engine/intelligence/submission_lifecycle.py` — state machine
  (`request_submission`/`submit`/`reconcile_submission`/`request_cancellation`/
  `record_result`/`restart_recovery`/`resolve_adapter`), transition table,
  reconcile-before-retry, blind-retry prohibition.
- `src/engine/intelligence/broker_adapter_infrastructure.py` —
  `SubmissionInfrastructure` (create/submit/reconcile/recovery/audit),
  `cp17_mode` binding, persist-new-then-delete-old.
- `src/engine/intelligence/reference_broker_adapter.py` —
  `ReferenceBrokerAdapter`, `ReferenceBrokerRequest`/`Response`,
  `ReferenceSimulation`, `_translate_command`, `_normalize_response`.
- `src/engine/intelligence/fake_broker.py` — deterministic `FakeBroker`.
- `src/engine/persistence/submission_serialization.py`/`submission_store.py`,
  `execution_command_serialization.py`/`store.py`,
  `execution_authorization_serialization.py`/`store.py` — atomic, schema-
  versioned, safe-id, fail-closed stores.
- Prior audits `docs/checkpoint_17_1..17_5_*.md`.

## 7. Current Architecture

**ALREADY VERIFIED — the frozen/proven architecture is:**

```
Trading Intelligence
        ↓
TradePlan
        ↓
Operational Trade Intent             (14.2 model / 14.4 engine / 14.5 application)
        ↓
Execution Authorization              (15.2 model / 15.3 engine / 15.5 persistence)
        ↓
ExecutionCommand                     (16.2 model / 16.3 boundary / 16.5 persistence)
        ↓
SubmissionInfrastructure             (17.3)
        ↓
SubmissionLifecycle                  (17.2/17.3)
        ↓
BrokerAdapter                        (17.2 contract)
        ↓
ReferenceBrokerAdapter  |  FakeBroker (17.4 / 17.2 — deterministic, offline)
```

Invariants that are **ALREADY VERIFIED** in the current code:

1. `ExecutionCommand` is immutable and broker-neutral (no broker symbol,
   exchange, order type, product, credentials).
2. The adapter cannot authorize: it consumes only an already-authorized,
   immutable command.
3. The submission lifecycle is separate from the command; it references
   `command_id` only, never embeds/mutates the command.
4. `client_order_id`/`idempotency_key` are deterministic and restart-stable.
5. Timeout/unknown → `UNKNOWN`; `UNKNOWN` requires reconciliation; blind retry
   is prohibited and enforced (`ReconciliationRequiredError`,
   `request_submission` refusal).
6. Mode binding runs through `validate_adapter_mode`/`select_adapter`/
   `cp17_mode`; missing/mismatched adapters fail closed.
7. Persistence is schema-versioned, atomic, safe-id, and broker-neutral.
8. There is no network, no SDK, no credential, no broker-specific adapter in
   the execution path (repository sweep, Section 44).

## 8. Target Architecture

**DESIGNED FOR 17.7 — the future architecture:**

```
Trading Intelligence
        ↓
TradePlan
        ↓
Operational Trade Intent
        ↓
Execution Authorization
        ↓
ExecutionCommand                      (frozen, unchanged)
        ↓
SubmissionInfrastructure              (frozen, unchanged)
        ↓
SubmissionLifecycle                   (frozen, unchanged)
        ↓
BrokerAdapter                         (frozen contract, unchanged)
        ↓
UpstoxBrokerAdapter                   (NEW — the first concrete real-broker adapter)
        │
        ├── request translator        (ExecutionCommand → UpstoxBrokerRequest)
        ├── response translator       (UpstoxBrokerResponse → AdapterResult)
        ├── error translator          (Upstox errors → BrokerError)
        ├── capability mapper         (supports/check)
        ├── instrument mapper         (canonical instrument → Upstox instrument_token)
        ├── order-state mapper        (Upstox status → BrokerResultStatus)
        ├── idempotency mapper        (client_order_id/idempotency derivation + Upstox tag)
        ├── reconciliation mapper     (Upstox order lookup → normalized state)
        └── broker client boundary
                   │
                   ▼
        UpstoxBrokerClient            (adapter-owned, injected credential holder)
                   │
                   ▼
        Upstox HTTPS API (V3)         (OUT OF SCOPE — no network in 17.6/17.7)
```

The real broker adapter is a translation boundary. It is the only component in
the repository that may ever know Upstox's API vocabulary. It sits BELOW the
frozen `BrokerAdapter` protocol; the generic execution system never depends on
it.

## 9. Target Broker

**DECISION: Upstox** (the first production broker candidate).

Reasoning (ALREADY VERIFIED from prior checkpoints and current repository
state):

- The repository already maintains an isolated Upstox integration surface —
  the Upstox HISTORICAL DATA provider (`engine/data/historical_provider.py`)
  — proving symbol/key mapping conventions (`NSE_EQ|INE...` instrument keys,
  ISIN identifiers) are already known and isolated.
- Upstox publishes an official V3 REST trading API with place/cancel/modify/
  order-history endpoints (publicly documented; Section 42 labels each fact).

**Relationship to the historical Upstox token**: The existing
`UPSTOX_ANALYTICS_TOKEN` environment variable is used ONLY by the historical
DATA provider (read-only OHLCV) and the corpus-ingestion precheck. It is NOT
an execution credential. The future live execution adapter uses a SEPARATE
access-token credential (OAuth2 access token / refresh flow, broker-specific)
that does not exist in this checkpoint (Section 17). This distinction is
preserved throughout (Section 44).

## 10. Adapter Responsibilities

**DESIGNED FOR 17.7 — `UpstoxBrokerAdapter` owns:**

1. Translating an `ExecutionCommand` into an Upstox place-order request
   (side, quantity, price, trigger price, order type, product, validity,
   instrument token, tag).
2. Normalizing Upstox responses into `AdapterResult` (never leaking Upstox
   models, HTTP envelopes, or status strings upward).
3. Normalizing Upstox errors/HTTP errors into `BrokerError` using the frozen
   taxonomy (`BrokerErrorCode` + `BrokerErrorCategory`).
4. Exposing broker-neutral capabilities (`AdapterCapabilities`: SUBMIT +
   RECONCILE [+ CANCEL]) and `supports`/`check` capability validation.
5. Verifying paper/live mode via the frozen `validate_adapter_mode` before
   EVERY operation.
6. Owning all broker-specific mapping tables (instrument, order type,
   product, validity, exchange, order-state, error, rate-limit).
7. Owning the deterministic client-order identity mapping (Section 19) and
   the Upstox-supported idempotency story (Section 20) WITHOUT falsely
   claiming broker-side deduplication.
8. Owning reconcile-by-`client_order_id`/Upstox `tag`/`order_id` lookups,
   strictly through a single normalization boundary.
9. Doing nothing else: it does NOT authorize, plan, score, manage positions,
   own portfolio, generate signals, or mutate any upstream artifact.

**The adapter does NOT hold credentials.** Credentials are injected into the
adapter-owned broker CLIENT from a credential provider (Section 17).

## 11. Core vs Adapter Boundary

**ALREADY VERIFIED (frozen) + DESIGNED FOR 17.7:**

| Concern | Core (frozen) | Adapter (17.7) |
|---------|---------------|----------------|
| Identity | `command_id`, `client_order_id`, `idempotency_key` (deterministic) | maps them onto Upstox fields (`tag`) |
| Economics | entry/stop/target/quantity/risk verbatim | validates precision/lots; NEVER alters/increases |
| Order side | `direction` LONG/SHORT | `transaction_type` BUY/SELL |
| Order type | none (broker-neutral) | Upstox order_type MARKET/LIMIT/SL/SL-M |
| Product | none | Upstox product I/D/MTF (mapped) |
| Validity | none | Upstox validity DAY/IOC |
| Instrument | canonical name (e.g. "RELIANCE") | `NSE_EQ|INE002A01018` instrument token |
| Execution mode | `ExecutionMode` (derived) | bound `execution_mode` + validate before each op |
| States | `BrokerResultStatus` | Upstox status strings mapped |
| Errors | `BrokerError` taxonomy | Upstox error codes/HTTP mapped |
| Credentials | NEVER | broker CLIENT only (injected) |

Rule: **Unsupported semantics fail closed.** The adapter never silently
discards an unsupported field, never substitutes a default product/order type,
never ignores an unmappable state. Anything that cannot be translated or
mapped with confidence produces a deterministic `AdapterResult.UNKNOWN` /
`FAILED` (with the proper `BrokerErrorCode`), or raises `ValueError`/`TypeError`
for pre-submission validation rejection (frozen contract semantics).
## 12. Translation Matrix

**DESIGNED FOR 17.7.** Complete translation matrix for every `ExecutionCommand`
field and the adapter-owned broker request fields. For every field we document:
(1) core meaning, (2) broker-neutral representation, (3) broker-specific
representation, (4) conversion direction, (5) validation requirement,
(6) failure behavior, (7) whether information can be lost, (8) whether
information can be transformed, (9) whether rounding/conversion is required,
(10) whether the field requires broker capability validation.

Legend: **L** = can information be lost? **T** = can it be transformed?
**R** = rounding/conversion required? **C** = broker capability validation?

| Field | (1) Core meaning | (2) Broker-neutral repr | (3) Upstox repr | (4) Direction | (5) Validation | (6) Failure behavior | (7) L | (8) T | (9) R | (10) C |
|-------|------------------|--------------------------|-----------------|----------------|----------------|----------------------|-------|-------|-------|--------|
| command_id | immutable execution identity | `cmd-` + sha256[:16] | NOT sent to broker (identity only; mapped to `tag` via client_order_id) | core → adapter (read-only) | non-empty, prefix | N/A (never sent) | No | No | No | No |
| authorization_id / intent_id / content_fingerprint | audit binding | `auth-`/`intent-`/`fp-` ids | NOT sent (audit only) | core → adapter (read-only) | non-empty | N/A | No | No | No | No |
| instrument | canonical instrument name | e.g. "RELIANCE" | `instrument_token` `NSE_EQ|INE002A01018` | core → adapter (map) | mapping exists; token format valid | `UNSUPPORTED_INSTRUMENT` (VALIDATION) before broker call | No (fail closed if unmapped) | Yes (name→token) | No | Yes |
| direction | trade side | "LONG"/"SHORT" | `transaction_type` BUY/SELL | core → adapter (map) | recognized value | `VALIDATION_FAILURE` before broker call | No | Yes (LONG→BUY, SHORT→SELL) | No | Yes |
| entry | reference price | Decimal | `price` (LIMIT/SL) | core → adapter (verbatim) | finite Decimal, tick-valid | `VALIDATION_FAILURE` before broker call | No | No (verbatim) | Yes (tick/precision check; NEVER alter value) | Yes |
| stop | reference level | Decimal | `trigger_price` (SL/SL-M) | core → adapter (verbatim) | finite Decimal, tick-valid, < entry for LONG | `VALIDATION_FAILURE` before broker call | No | No (verbatim) | Yes (tick/precision check) | Yes |
| target | reference level | Decimal | NOT sent (Upstox has no bracket target on place; target is informational) | core → adapter (read-only) | finite Decimal | N/A (not sent; recorded locally) | Yes (target not transmitted) | No | No | No |
| quantity | position quantity | Decimal | `quantity` (integer) | core → adapter (validate) | positive; integer lot-multiple; never increased | `VALIDATION_FAILURE` before broker call | Yes (fractional → lot-rounded DOWN only; never increase) | Yes (Decimal → int, floor to lot) | Yes | Yes |
| planned_risk / maximum_risk | risk invariants | Decimal | NOT sent (risk validated upstream) | core → adapter (read-only) | planned ≤ maximum | N/A | No | No | No | No |
| execution_mode | PAPER/LIVE intent | `ExecutionMode` | bound adapter mode; validate before each op | core → adapter (verify) | mode match | `ValueError` (mode mismatch) before broker call | No | No | No | Yes |
| created_at / valid_from / valid_until | time bounds | aware datetimes | NOT sent (validity window checked locally) | core → adapter (read-only) | aware; ordering | `VALIDATION_FAILURE` if invalid | No | No | No | No |
| label / metadata | audit metadata | str / pairs | `tag` (mapped from client_order_id; metadata NOT sent) | core → adapter (map) | non-empty tag | `VALIDATION_FAILURE` if tag invalid | Yes (metadata not sent) | Yes (label→tag) | No | Yes |
| client_order_id | deterministic application identity | `co-` + sha256[:16] | Upstox `tag` (see Section 19/20) | core → adapter (map) | deterministic; broker tag constraints | `VALIDATION_FAILURE` before broker call | No | Yes (co- → tag) | No | Yes |
| idempotency_key | deterministic idempotency key | `idem-` + sha256[:16] | NOT natively supported (see Section 20); used for local duplicate detection | core → adapter (map) | deterministic | N/A (local only) | No | No | No | No |

**Key rules:**

- **L (loss)**: Only fields that are genuinely not transmissible (target,
  metadata, idempotency_key) are dropped — and only when the drop is explicit,
  documented, and does not alter the authorized economic meaning. The entry
  price, stop, quantity and side are NEVER lost.
- **T (transformation)**: Only side (LONG/SHORT → BUY/SELL), instrument
  (name → token), and identity (command_id → client_order_id → tag) are
  transformed. Prices/quantities are never transformed beyond tick/lot
  validation.
- **R (rounding)**: Quantity may be floored to the broker lot size (never
  increased — increasing quantity would increase risk and violate the frozen
  risk invariant). Prices are tick-checked but never rounded to a different
  value.
- **C (capability validation)**: instrument, side, order type, product,
  validity, quantity, price/trigger price, and tag all require capability
  validation via `supports`/`check` BEFORE the broker call.
- **NEVER silently discard an unsupported field.** If a field cannot be
  transmitted with confidence, the adapter fails closed (raises or returns a
  typed `FAILED`/`UNKNOWN`), never silently omits it.

## 13. Instrument Mapping

**DESIGNED FOR 17.7.** The broker-specific instrument mapping boundary.

- **Canonical source of truth**: the core canonical instrument name on the
  `ExecutionCommand` (e.g. "RELIANCE"). The core never depends on broker
  identifiers.
- **Mapping ownership**: the adapter owns an isolated instrument map
  (`_default_upstox_instrument_key_map`), mirroring the existing historical
  provider's isolated map. The map is a module-level constant table of
  canonical name → `instrument_token` (e.g. `RELIANCE → NSE_EQ|INE002A01018`).
- **Mapping freshness**: the table is static and versioned in the adapter.
  Instrument tokens (ISIN-based) are stable; a refresh is an adapter release,
  not a runtime lookup. (REQUIRES IMPLEMENTATION-TIME VERIFICATION that the
  tokens remain current.)
- **Invalid mapping behavior**: an unknown canonical instrument → `supports`
  returns False and `check` raises `ValueError` (`UNSUPPORTED_INSTRUMENT`) —
  never a broker call.
- **Stale mapping behavior**: if a token is rejected by the broker
  (instrument not found), the adapter normalizes to `UNSUPPORTED_INSTRUMENT`
  (VALIDATION) and does NOT attempt a fallback token.
- **Missing mapping behavior**: same as invalid — fail closed, no broker call.
- **Ambiguous mapping behavior**: if one canonical name maps to more than one
  token (should never happen by construction), the adapter fails closed
  (deterministic first-by-sorted-key is NOT acceptable for ambiguity; raise).
- **Derivative identifiers** (expiry/strike/option type): OUT OF SCOPE for the
  first adapter (equity cash only). A derivative instrument without a mapped
  token fails closed as `UNSUPPORTED_INSTRUMENT`.

## 14. Order-Type Mapping

**DESIGNED FOR 17.7.** Mapping of generic order semantics to Upstox order
types. Upstox publicly documents order types `MARKET`, `LIMIT`, `SL`, `SL-M`
(VERIFIED FROM PUBLIC DOCUMENTATION — Section 42).

| Generic semantic (adapter-owned) | Upstox order_type | Conditions | Invalid combos | Special handling |
|----------------------------------|-------------------|------------|----------------|------------------|
| MARKET (entry at market) | `MARKET` | price=0, trigger=0 | SL-M with price=0 | price must be 0 for MARKET |
| LIMIT (entry at limit) | `LIMIT` | price=entry, trigger=0 | trigger != 0 | price required |
| STOP / STOP-LOSS (trigger-based) | `SL` | price=entry, trigger=stop | trigger >= entry (LONG) | trigger required; SL is a stop-limit in Upstox semantics |
| STOP-MARKET (trigger-based market) | `SL-M` | price=0, trigger=stop | price != 0 | trigger required |
| Bracket / OCO (entry+stop+target) | NOT SUPPORTED by Upstox place-order | — | any bracket request | fail closed: `UNSUPPORTED_ORDER_SEMANTICS` (the `target` field is not transmissible; Section 12) |

**Semantic equivalence requirement (CRITICAL):** Do NOT assume similarly
named order types have identical semantics. Upstox `SL` is a stop-LIMIT
(requires both `price` and `trigger_price`); `SL-M` is a stop-market
(`price`=0, `trigger_price` set). The adapter must verify these semantics at
implementation time against the current Upstox docs before mapping
(REQUIRES IMPLEMENTATION-TIME VERIFICATION). If the semantics cannot be
verified with confidence, the adapter maps to `UNKNOWN`/fails closed rather
than guessing.

**Which combinations are invalid (fail closed, never sent):**

- MARKET with a non-zero price.
- LIMIT with a non-zero trigger.
- SL with trigger >= entry for LONG (or trigger <= entry for SHORT).
- Any request that requires a bracket/target the broker cannot express.

## 15. Product / Validity / Exchange Mapping

**DESIGNED FOR 17.7.** Explicit mappings with NO silent fallback. Upstox
publicly documents products `I` (intraday/margin), `D` (delivery), `MTF`
(margin trading facility) and validity `DAY`/`IOC` (VERIFIED FROM PUBLIC
DOCUMENTATION — Section 42).

| Core value | Broker-specific value | Validation | Unsupported → |
|------------|----------------------|------------|----------------|
| product: cash/delivery (adapter-owned) | `D` (delivery) | product must be one of the adapter's supported set | REJECT BEFORE BROKER CALL (`UNSUPPORTED_ORDER_SEMANTICS`) |
| product: intraday (adapter-owned, future) | `I` | supported set | REJECT BEFORE BROKER CALL |
| product: margin (adapter-owned, future) | `MTF` | supported set | REJECT BEFORE BROKER CALL |
| validity: DAY (adapter-owned) | `DAY` | supported set | REJECT BEFORE BROKER CALL |
| validity: IOC (adapter-owned, future) | `IOC` | supported set | REJECT BEFORE BROKER CALL |
| exchange: NSE (adapter-owned) | derived from `instrument_token` prefix (`NSE_EQ|...`) | token prefix must be supported | REJECT BEFORE BROKER CALL |
| exchange: BSE (adapter-owned, future) | `BSE_EQ|...` | supported set | REJECT BEFORE BROKER CALL |

Rules:

- The adapter exposes a small, explicit supported set (first scope: product
  `D`, validity `DAY`, exchange NSE). Anything outside → `UNSUPPORTED_ORDER_SEMANTICS`
  / `UNSUPPORTED_INSTRUMENT` BEFORE the broker call.
- **No silent fallback**: an unsupported product is never "defaulted" to `D`;
  an unsupported validity is never "defaulted" to `DAY`. The adapter rejects.
- The core never expresses product/validity/exchange; these are adapter-owned
  decisions. If the adapter cannot determine a safe value, it fails closed.

## 16. Capability Model

**ALREADY VERIFIED (contract) + DESIGNED FOR 17.7 (adapter exposure).**

The frozen `AdapterCapabilities` requires SUBMIT + RECONCILE and supports an
optional CANCEL. The `UpstoxBrokerAdapter` must expose:

| Capability | Requirement | Failure behavior |
|------------|-------------|-------------------|
| SUBMIT | required (place order) | — |
| RECONCILE | required (order history / order details lookup) | — |
| CANCEL | required for the first adapter (cancel order) | `ValueError` if not advertised when `cancel()` is called |
| supported order types | MARKET/LIMIT/SL/SL-M (first scope) | `supports` False / `check` raises |
| supported products | `D` (first scope) | fail closed |
| supported exchanges | NSE (first scope) | fail closed |
| supported instruments | the adapter instrument map | fail closed |
| supported execution modes | bound PAPER or LIVE (one per adapter instance) | `validate_adapter_mode` mismatch → ValueError |
| idempotency support | application-level only (Section 20); broker-side NOT claimed | documented limitation |
| cancellation support | CANCEL capability | — |

**Capability mismatches must fail before submission.** `supports(command)`
returns False and `check(command)` raises for any unsupported instrument,
order type, product, validity, exchange, quantity, or price — before any
broker request is generated (Section 23 guard chain).

## 17. Authentication Boundary

**DESIGNED FOR 17.7.** Desired conceptual structure (no implementation):

```
Execution Engine
       ↓
BrokerAdapter                 (broker-neutral protocol; NO auth logic)
       ↓
UpstoxBrokerClient            (owns authentication handshake; adapter-owned)
       ↓
Credential Provider           (broker-specific; injected)
       ↓
Broker Authentication        (Upstox OAuth2 access-token / refresh flow)
```

- **Credential ownership**: the Upstox credential (an OAuth2 access token,
  and refresh-token handling) belongs to a broker-specific credential
  provider. The adapter itself never reads the token.
- **Credential injection**: constructor injection of the credential provider
  into the `UpstoxBrokerClient`. The client uses the provider to obtain the
  current access token for each request; the token is attached ONLY by the
  client at the network boundary.
- **Credential lifetime**: the access token has a finite lifetime managed by
  the provider (refresh flow). The execution system is unaware of the token
  value or lifetime.
- **Credential rotation**: rotate at the provider; a new client with a new
  token; identity/persistence unchanged (17.5 finding).
- **Credential validation**: a startup gate verifies the provider yields a
  non-empty token (Section 22) and (at implementation time) that the token is
  accepted by the broker (REQUIRES IMPLEMENTATION-TIME VERIFICATION — a
  validation call must be read-only and safe).
- **Credential failure**: missing/invalid/expired token → fail closed:
  `AUTHENTICATION_FAILURE` (TRANSPORT) before any order-affecting call; the
  adapter never proceeds with an empty token.
- **Credential isolation**: credentials never enter `ExecutionCommand`,
  `TradePlan`, `OperationalTradeIntent`, `SubmissionLifecycle`,
  `AdapterResult`, `BrokerError`, persistence records, logs, audit events, or
  test fixtures (Section 18).
- **Paper/live credential separation**: separate credential providers per
  mode. The live provider is never injected into a paper adapter and vice
  versa. A live adapter can only exist with a live credential provider
  (Section 21).

## 18. Credential Isolation

**ALREADY VERIFIED (today) + DESIGNED FOR 17.7 (enforcement).**

Credentials MUST NOT appear in:

| Location | Today (verified) | 17.7 requirement |
|----------|------------------|------------------|
| ExecutionCommand | no credential fields | keep |
| TradePlan | no credential fields | keep |
| OperationalTradeIntent | no credential fields | keep |
| SubmissionLifecycle | no credential fields | keep |
| AdapterResult / BrokerError | no credential fields | keep; reason strings redacted |
| persistence records (command/authorization/submission stores) | no credential fields | keep |
| logs / audit events | no credential material | broker client redacts auth headers + error bodies |
| test fixtures | no credentials (deterministic fakes) | keep |

17.7 must implement a broker-client redaction rule (reuse the existing
`_BEARER_RE` pattern from `corpus_ingestion.py`): any error text that could
contain an `Authorization: Bearer <token>` or the token value is redacted
before it reaches normalization. The adapter's `BrokerError.message` and
`AdapterResult.reason` carry non-sensitive, broker-neutral reasons only.

## 19. Client Order ID

**ALREADY VERIFIED (identity derivation) + DESIGNED FOR 17.7 (broker mapping).**

Existing invariant (frozen, verified):

```
same ExecutionCommand
    ↓
same command_id
    ↓
same client_order_id        (derive_client_order_id: "co-" + sha256[:16] of
                             {command_id, broker_context})
    ↓
same idempotency_key        (derive_idempotency_key: "idem-" + sha256[:16])
```

**DESIGNED FOR 17.7 — Upstox mapping:**

- **Exact derivation inputs**: `command_id` + `broker_context` (unchanged,
  frozen). The adapter passes the SAME `broker_context` for every submission
  of the same command so the identity is stable across restart/retry.
- **Namespace**: `co-` prefix (frozen). Upstox `tag` field is the broker field
  that carries the application identity.
- **Length / allowed characters**: the deterministic `client_order_id` is
  `co-` + 16 hex chars = 19 chars, `[A-Za-z0-9-]` only — safe for Upstox `tag`
  (REQUIRES IMPLEMENTATION-TIME VERIFICATION of Upstox `tag` constraints).
- **Collision resistance**: sha256[:16] (64 bits) — collision-resistant for
  the application's scale; identical commands yield identical ids by design.
- **Broker length constraints**: if Upstox imposes a `tag` length/character
  limit, DO NOT silently truncate. Use a deterministic collision-safe
  encoding: the adapter maps `client_order_id` → a bounded `tag` via a
  documented, deterministic transform (e.g. `"co" + sha256(client_order_id)[:N]`
  with N within the broker limit), and records the mapping so reconciliation
  can invert it. The transform must be deterministic and restart-stable.
  (REQUIRES IMPLEMENTATION-TIME VERIFICATION of the actual Upstox `tag`
  constraints; if unverifiable, the adapter fails closed rather than sending
  an oversized tag.)
- **Deterministic behavior / restart behavior**: the same command always
  produces the same `tag`; a restarted process re-derives the same `tag` from
  the persisted `command_id` (no random state).

## 20. Broker Idempotency

**ALREADY VERIFIED (distinction) + DESIGNED FOR 17.7 (mapping).**

Four concepts, kept strictly separate:

| Concept | Provides | Owner | 17.7 status |
|---------|----------|-------|-------------|
| `command_id` | deterministic immutable identity of the authorized command | core (16.2) | frozen, unchanged |
| `client_order_id` | APPLICATION-LEVEL duplicate identity stable across restart | contract (17.2) | frozen, unchanged |
| `idempotency_key` | separate deterministic key string | contract (17.2) | frozen, unchanged |
| broker order ID | broker-generated order reference (`order_id`) | broker + adapter | downstream-only; never in core |

**Relationship:**

- `command_id → client_order_id → Upstox tag` (application identity carried
  to the broker for reconciliation).
- `idempotency_key` is a separate deterministic string used for local
  duplicate detection; it is NOT claimed to be a broker idempotency key.
- The broker-generated `order_id` (from the place-order response) is recorded
  on the `SubmissionLifecycle.broker_order_id` (downstream-only) for
  reconciliation.

**What the application guarantees:** the same command always yields the same
`client_order_id`/`idempotency_key`/`tag`; in-process and post-restart
duplicate submission is detected and refused (store guard +
`load_by_command` + lifecycle refusal — ALREADY VERIFIED).

**What the adapter must guarantee (17.7):** map the deterministic identity
onto Upstox's supported field (`tag`) and use it as the reconciliation key.
**The adapter must NEVER claim that `tag` (or `client_order_id`) guarantees
broker-side deduplication** unless the Upstox contract explicitly guarantees
it. Public documentation does NOT establish a client-supplied idempotency
guarantee for place-order (REQUIRES IMPLEMENTATION-TIME VERIFICATION).

**What happens when the broker reports DUPLICATE:**

- If Upstox ever returns a duplicate-order indication (REQUIRES
  IMPLEMENTATION-TIME VERIFICATION of the exact signal), the adapter must
  normalize it to a confirmed outcome using the broker order ID from the
  response (e.g. `ACCEPTED` with `broker_status="duplicate"`), mirroring the
  reference adapter's duplicate handling — it must NOT fabricate a new order
  and must NOT treat the duplicate as a fresh submission.
- If the duplicate signal is ambiguous, map to `UNKNOWN` (reconcile).

## 21. Submission Protocol

**DESIGNED FOR 17.7 — the exact future submission flow.** Every validation
gate and its failure behavior. No broker call exists in 17.6/17.7.

```
Authorized ExecutionCommand
   ↓ 1. command exists (persisted)            — else: CommandNotSubmittedError / store guard
   ↓ 2. command is AUTHORIZED (factory)       — already enforced upstream (16.2)
   ↓ 3. command immutable (frozen)            — structural
   ↓ 4. execution mode explicit               — ExecutionMode from authorization scope
   ↓ 5. correct broker selected               — explicit broker selection (Section 22)
   ↓ 6. correct adapter selected             — select_adapter (mode match, fail-closed)
   ↓ 7. adapter supports the operation       — supports/check (capability boundary)
   ↓ 8. instrument mapping valid             — instrument map; else UNSUPPORTED_INSTRUMENT
   ↓ 9. order type supported                 — order-type map; else UNSUPPORTED_ORDER_SEMANTICS
   ↓ 10. product supported                   — product map; else UNSUPPORTED_ORDER_SEMANTICS
   ↓ 11. exchange supported                  — token prefix; else UNSUPPORTED_INSTRUMENT
   ↓ 12. quantity valid (positive, lot)      — else VALIDATION_FAILURE
   ↓ 13. price/trigger price valid (tick)    — else VALIDATION_FAILURE
   ↓ 14. client order ID deterministic       — derive_client_order_id (frozen)
   ↓ 15. duplicate submission checked        — store guard + lifecycle refusal
   ↓ 16. existing UNKNOWN reconciled         — else ReconciliationRequiredError
   ↓ 17. required credentials available      — credential provider gate (Section 22)
   ↓ 18. broker client healthy               — adapter-level health check (17.7)
   ↓ 19. request passes adapter validation  — _validate_request (adapter-owned)
   ↓ 20. submission lifecycle persisted     — SubmissionInfrastructure (frozen)
   ↓ 21. broker result normalized           — _normalize_response → AdapterResult
   ↓ 22. audit event recorded               — SubmissionEvent (frozen)
```

Failure behavior summary:

- Gates 1–19 are pre-broker; any failure raises `ValueError`/`TypeError`
  (frozen contract) or returns a typed `FAILED`/`UNKNOWN` — never a false
  success.
- Gates 20–22 are the frozen infrastructure flow; the adapter participates
  only by returning `AdapterResult` from `submit`.
- A timeout/unknown at gate 21 → `UNKNOWN` (never `FAILED`), and the frozen
  lifecycle enforces reconcile-before-retry.
## 22. Timeout Handling

**ALREADY VERIFIED (frozen machinery) + DESIGNED FOR 17.7 (adapter policy).**

The frozen machinery already guarantees:

```
request sent → timeout → broker outcome unknown
        ↓
    UNKNOWN
        ↓
RECONCILE_REQUIRED
```

Never:

```
TIMEOUT → FAILED → RETRY
```

and never:

```
TIMEOUT → SUBMIT AGAIN
```

unless reconciliation establishes that the original submission definitely did
not occur AND the retry is explicitly safe per the broker contract.

**DESIGNED FOR 17.7 — adapter-level timeout policy:**

- **Request timeout**: the `UpstoxBrokerClient` applies a bounded, configurable
  request timeout (e.g. default 30s, matching the historical provider's finite
  timeout convention). A timeout → `BrokerErrorCode.TIMEOUT` (AMBIGUOUS) →
  `AdapterResult.UNKNOWN` (never `FAILED`).
- **Reconciliation timeout**: after a timeout, the lifecycle is `UNKNOWN` and
  the frozen `SubmissionInfrastructure` raises `ReconciliationRequiredError`
  on any further submit. Reconciliation (order lookup) is a GET — safe to
  retry — but must itself have a bounded timeout and map a timeout to
  `UNKNOWN` (stay `UNKNOWN`).
- **Reconcile window**: a broker-specific policy (e.g. retry reconciliation a
  bounded number of times over a bounded window before manual review) —
  REQUIRES IMPLEMENTATION-TIME VERIFICATION of Upstox order-history
  availability (public docs state orders remain available for one trading day;
  VERIFIED FROM PUBLIC DOCUMENTATION).
- **Stale state**: a lifecycle left in `UNKNOWN`/`SUBMITTED` past a stale
  threshold is surfaced by the recovery view as `RECONCILE_REQUIRED`; the
  adapter never auto-resolves it.
- **Broker timestamp vs local timestamp**: broker timestamps are validated for
  parseability/sanity but never used for identity; local timestamps are used
  only for correlation (Section 28).

## 23. UNKNOWN Handling

**ALREADY VERIFIED (frozen) — the UNKNOWN contract is enforced by the frozen
architecture**:

- `AdapterResult.UNKNOWN` is the ONLY normalized outcome for an ambiguous broker
  state (timeout / lost response / malformed response / unknown outcome).
- `SubmissionState.UNKNOWN` requires reconciliation
  (`SubmissionState.UNKNOWN.is_ambiguous` / `requires_reconciliation`).
- `request_submission` REFUSES an UNKNOWN lifecycle (blind retry prohibited).
- `SubmissionInfrastructure` surfaces `ReconciliationRequiredError` for UNKNOWN.
- UNKNOWN survives restart (`restart_recovery` → `RECONCILE_REQUIRED`).

**DESIGNED FOR 17.7 — adapter behavior**:

- A timeout, connection reset, partial response, or any unconfirmable broker
  outcome after the request was transmitted → `UNKNOWN` (never `FAILED`).
- The broker may or may not have accepted the order; the adapter NEVER treats
  UNKNOWN as success or failure.
- Reconciliation (Section 24) is the ONLY permitted next step. Still-UNKNOWN
  after reconciliation stays UNKNOWN; retry remains prohibited.
- `UNKNOWN` is surfaced in the recovery view as `RECONCILE_REQUIRED`; the
  adapter never auto-resolves it.

Never:

```
TIMEOUT → FAILED → RETRY
TIMEOUT → SUBMIT AGAIN
```

unless reconciliation establishes the original submission definitely did not
occur AND the retry is explicitly safe per the broker contract.

## 24. Reconciliation Design
**ALREADY VERIFIED (frozen machinery) + DESIGNED FOR 17.7 (broker-specific).**

- **Primary identifier**: the deterministic `client_order_id`, carried to the
  broker as the Upstox `tag`. Reconcile = Upstox order-history lookup filtered
  by `tag`.
- **Fallback identifiers (explicitly justified)**: the broker-generated
  `order_id` recorded on the lifecycle (`broker_order_id`) may be used when
  the `tag` lookup is unavailable — justified ONLY because the `order_id` was
  returned by the broker for the SAME submission (recorded downstream-only).
  The `idempotency_key` is NOT a broker lookup key.
- **Exact match**: one order with the matching `tag` → normalize its status
  via the order-state mapper (Section 25).
- **No match**: explicit `UNKNOWN`/`REJECTED` at adapter discretion — never a
  false success. (If the broker confirms no order exists and the original
  submission was never confirmed, the adapter may report `REJECTED` with a
  documented reason, or `UNKNOWN`; REQUIRES IMPLEMENTATION-TIME VERIFICATION
  of the exact Upstox empty-result semantics.)
- **Multiple matches**: ambiguous → `UNKNOWN` (never fabricate a match).
- **Malformed result**: `MALFORMED_RESPONSE` → `UNKNOWN` (fail closed).
- **Broker unavailable**: `BROKER_UNAVAILABLE`/`RATE_LIMIT` (TRANSPORT) →
  reconcile again later; never a false determination.
- **Stale result**: an order-history entry older than the reconcile window →
  surfaced as stale; never treated as a fresh confirmation.
- **Duplicate result**: multiple records for the same `tag` → `UNKNOWN`
  (manual review) unless a single authoritative record is unambiguous.
- **Conflicting results**: conflicting statuses across records → `UNKNOWN`.

**Required principle**: the adapter normalizes everything into `AdapterResult`.
Upstox response objects never escape the adapter (Section 19/29).

## 25. Order-State Mapping
**DESIGNED FOR 17.7.** Upstox publicly documents order statuses including
`open`, `complete`, `cancelled`, `rejected`, and pending states (VERIFIED
FROM PUBLIC DOCUMENTATION — Section 42). The exact status vocabulary must be
confirmed at implementation time (REQUIRES IMPLEMENTATION-TIME VERIFICATION).

| Upstox status (publicly documented families) | Generic state | Confidence | Conditions | Required fields | Ambiguity handling |
|----------------------------------------------|---------------|------------|------------|-----------------|---------------------|
| `open` (pending/working) | `SUBMITTED`/`ACCEPTED` | HIGH | order accepted, not filled | order_id, status | map to `SUBMITTED` (or `ACCEPTED` if broker confirms acceptance) |
| `complete` (filled) | `FILLED` | HIGH | full fill confirmed | order_id, status, filled quantity | — |
| `cancelled` | `CANCELLED` | HIGH | cancellation confirmed | order_id, status | — |
| `rejected` | `REJECTED` | HIGH | rejection confirmed | order_id, status, status_message | — |
| partial fill (if exposed) | `PARTIALLY_FILLED` | MEDIUM | partial fill confirmed | order_id, status, filled quantity | if ambiguous → `UNKNOWN` |
| unknown / unconfirmable / lost | `UNKNOWN` | clear | cannot confirm | — | MUST map to `UNKNOWN` (reconcile) |

Rules:

- **Never force an unsafe mapping.** If a broker state cannot be safely
  represented (e.g. a mixed/completed-with-rejection state), classify it as
  a CONCERN and map to `UNKNOWN` (reconcile) until the exact mapping is
  confirmed at implementation time.
- A broker state meaning "accepted but not submittable yet" (pending approval
  / margin check) maps to `ACCEPTED` only after confirmed semantics;
  otherwise `UNKNOWN`.
- A broker "finished/expired" (session-expired order) has no generic member;
  draft as `CANCELLED` or `REJECTED` per broker semantics, otherwise `UNKNOWN`.

## 26. Error Mapping
**DESIGNED FOR 17.7.** Broker-specific error mapping matrix. Upstox publicly
documents error codes such as `UDAPI1004` (valid order type required),
`UDAPI1007` (validity required), `UDAPI1056` (invalid order type),
`UDAPI1055` (invalid validity), `UDAPI1008` (price required), `UDAPI1036`
(trigger price required), `UDAPI1003` (order id required), `UDAPI1154`
(static-IP restriction), `UDAPI1158` (market orders not allowed), and
`UDAPI100041` (modification of already-finalized orders not allowed)
(VERIFIED FROM PUBLIC DOCUMENTATION — Section 42). The exact code catalog must
be confirmed at implementation time (REQUIRES IMPLEMENTATION-TIME VERIFICATION).

| Failure | BrokerErrorCategory | BrokerErrorCode |
|---------|---------------------|-----------------|
| authentication failure / invalid token / expired token | TRANSPORT | AUTHENTICATION_FAILURE |
| authorization failure (insufficient permission) | BROKER_REJECTION | AUTHORIZATION_FAILURE |
| invalid instrument / instrument not found | VALIDATION | UNSUPPORTED_INSTRUMENT |
| invalid quantity | VALIDATION | VALIDATION_FAILURE |
| invalid price / invalid trigger price | VALIDATION | VALIDATION_FAILURE |
| invalid order type | VALIDATION | UNSUPPORTED_ORDER_SEMANTICS |
| invalid product | VALIDATION | UNSUPPORTED_ORDER_SEMANTICS |
| invalid exchange | VALIDATION | UNSUPPORTED_INSTRUMENT |
| market closed | BROKER_REJECTION | BROKER_REJECTION (adapter-mapped) |
| insufficient funds / margin | BROKER_REJECTION | BROKER_REJECTION (adapter-mapped) |
| rate limit / throttling | TRANSPORT | RATE_LIMIT |
| timeout | AMBIGUOUS | TIMEOUT |
| network failure (connection/DNS/TLS/reset) | TRANSPORT | NETWORK_FAILURE |
| broker unavailable / outage | TRANSPORT | BROKER_UNAVAILABLE |
| duplicate order | adapter-mapped (see Section 20) | adapter-owned |
| malformed response | AMBIGUOUS | MALFORMED_RESPONSE |
| unknown broker error | AMBIGUOUS | UNKNOWN_OUTCOME |
| internal adapter error | INTERNAL | INTERNAL_ADAPTER_FAILURE |

Rules:

- Upstox-specific exceptions/HTTP bodies stay INSIDE the adapter/client layer.
  Only the broker-neutral `AdapterResult`/`BrokerError` cross the boundary.
- Unknown Upstox error codes → `UNKNOWN_OUTCOME` (AMBIGUOUS), never a guessed
  VALIDATION/BROKER_REJECTION.
- Error messages are redacted (Section 18) and carry non-sensitive reasons.

## 27. Rate-Limit Policy
**DESIGNED FOR 17.7.**

- **Detection**: HTTP 429 / Upstox rate-limit error code / throttling response
  → `RATE_LIMIT` (TRANSPORT).
- **Classification**: `RATE_LIMIT` is retryable at the error-code level, but
  retryability MUST be interpreted together with lifecycle state (17.5-C04).
- **Retryability**:
  - SAFE RETRY: a GET/reconciliation request may be retried (no new order).
  - UNSAFE RETRY: an unknown-order submission MUST NOT be auto-retried merely
    because a rate-limit error occurred — the broker may have accepted the
    order (reconcile first). This is enforced by the frozen lifecycle
    (`UNKNOWN` refuses submit).
- **Backoff**: bounded, documented backoff for safe retries (e.g. exponential
  backoff with a cap); REQUIRES IMPLEMENTATION-TIME VERIFICATION of Upstox
  rate-limit headers/limits.
- **Maximum retry behavior**: a bounded retry budget for safe retries; after
  the budget is exhausted, surface the state for manual review — never an
  unbounded loop.
- **Reconciliation implications**: a rate-limit on submit → `UNKNOWN` (not
  `FAILED`) if the request may have been accepted; reconcile before any
  resubmit.
- **Submission safety**: **A rate-limit error on submission is not
  automatically proof that the order was not accepted.** The adapter must
  treat a rate-limited submit as ambiguous (`UNKNOWN`) unless the broker
  contract explicitly states otherwise (REQUIRES IMPLEMENTATION-TIME
  VERIFICATION).

## 28. Network Failure Policy
**DESIGNED FOR 17.7.** Classify each failure as KNOWN FAILURE or AMBIGUOUS
OUTCOME; if ambiguous → `UNKNOWN` and reconciliation required.

| Failure | Classification | AdapterResult |
|---------|----------------|--------------|
| connection refused | KNOWN FAILURE (no request sent) | `FAILED` (NETWORK_FAILURE) |
| DNS failure | KNOWN FAILURE (no request sent) | `FAILED` (NETWORK_FAILURE) |
| TLS failure | KNOWN FAILURE (no request sent) | `FAILED` (NETWORK_FAILURE) |
| connection reset | AMBIGUOUS (request may have been sent) | `UNKNOWN` (NETWORK_FAILURE → reconcile) |
| timeout | AMBIGUOUS | `UNKNOWN` (TIMEOUT) |
| partial response | AMBIGUOUS | `UNKNOWN` (MALFORMED_RESPONSE) |
| malformed response | AMBIGUOUS | `UNKNOWN` (MALFORMED_RESPONSE) |
| broker outage | KNOWN FAILURE (broker unavailable) | `FAILED` (BROKER_UNAVAILABLE) |

Rule: a failure that occurs AFTER the request was transmitted (connection
reset mid-flight, timeout, partial/malformed response) is AMBIGUOUS → `UNKNOWN`
→ reconcile-before-retry. A failure that provably occurred BEFORE transmission
(connection refused, DNS, TLS handshake failure) is a KNOWN FAILURE →
`FAILED` (safe to retry as a fresh submission because no request reached the
broker). The adapter must be conservative: if it cannot prove the request was
not sent, it must treat the outcome as `UNKNOWN`.

## 29. Response Validation
**DESIGNED FOR 17.7.** Strict broker response validation BEFORE accepting a
broker response. Malformed or contradictory responses fail closed.

Validate:

- **Response structure**: JSON object; `status` field present (`success`/
  `error`); `data` present when expected.
- **Required identifiers**: `order_id` present and non-empty on a successful
  place-order; `client_order_id`/`tag` cross-check matches the submitted tag.
- **Order ID**: broker `order_id` recorded (downstream-only).
- **Client order ID**: response `tag` (if echoed) must match the submitted tag.
- **Status**: a known Upstox status string; unknown → `UNKNOWN`/`MALFORMED`.
- **Quantities / prices**: finite Decimal; never fabricate; cross-check
  against the submitted request when present.
- **Timestamps**: parseable, sane ordering.
- **Error fields**: `status_message`/error codes normalized (Section 26).
- **Correlation fields**: any request/response correlation id is validated for
  presence when documented.

**Never interpret HTTP success as order success.** The normalizer maps only
explicit, confirmed broker states to `ACCEPTED`/`FILLED`. An HTTP 200 with an
error body, or a success envelope without a confirmed order state, is not an
order success.

## 30. Cancellation Design
**DESIGNED FOR 17.7.**

- **Cancellation identifier**: the broker `order_id` (recorded on the
  lifecycle) is the primary cancel identifier (Upstox cancel-order takes
  `order_id` — VERIFIED FROM PUBLIC DOCUMENTATION). The `client_order_id`/
  `tag` is the reconciliation key.
- **Valid cancellation states**: only in-flight (SUBMITTED / ACCEPTED /
  PARTIALLY_FILLED / SUBMISSION_REQUESTED-non-pre) lifecycles may be
  cancelled; terminal states are absorbing (frozen transition table).
- **Duplicate cancellation behavior**: cancelling an already-cancelled order
  → normalize the broker response (e.g. `CANCELLED` with a documented reason
  or `UNKNOWN`); never a new order.
- **Timeout behavior**: a cancel timeout → `UNKNOWN` (AMBIGUOUS) — the broker
  may or may not have cancelled; reconcile.
- **Unknown cancellation outcome**: `UNKNOWN` → reconcile-before-retry
  (same frozen rule).
- **Reconciliation after cancellation timeout**: reconcile by `order_id`/
  `tag` to confirm the final state.
- **Already-filled behavior**: cancelling an already-filled order → broker
  rejects; normalize to `FILLED` (the fill is authoritative) or `REJECTED`
  with a documented reason; never a false cancellation.
- **Already-cancelled behavior**: normalize to `CANCELLED` (confirmed) or
  `UNKNOWN` if ambiguous.
- Cancellation uses the SAME broker-neutral result/error boundary
  (`AdapterResult` + `BrokerError`), never broker-specific types.

## 31. Paper/Live Isolation
**ALREADY VERIFIED (frozen) + DESIGNED FOR 17.7 (adapter binding).**

Required:

- PAPER → paper/simulated adapter (reference/fake — existing).
- LIVE → real broker adapter (Upstox, 17.7).

No:

- LIVE → PAPER fallback (forever prohibited).
- PAPER → LIVE fallback.
- Silent mode changes.

Missing live adapter → fail closed (`select_adapter` raises on empty/mismatch
registry — ALREADY VERIFIED). The `UpstoxBrokerAdapter` is bound to exactly one
`ExecutionMode`; `validate_adapter_mode` runs before EVERY operation
(ALREADY VERIFIED); the `cp17_mode` lifecycle binding is verified during
reconcile/cancel (ALREADY VERIFIED). A live adapter only exists with a live
credential provider (Section 17). Environment separation for live vs paper
deployments is an operational control (OUT OF SCOPE for 17.7).

## 32. Startup Safety
**DESIGNED FOR 17.7 — the future live startup gates.** Any failure → FAIL
CLOSED; no fallback to paper.

| Gate | Failure behavior |
|------|------------------|
| explicit execution mode | unknown/missing mode → fail closed |
| explicit broker selection | no broker selected → fail closed |
| adapter availability | adapter not registered / not constructable → fail closed |
| credential availability | credential provider yields no token → fail closed |
| credential validation | token rejected by broker (read-only validation) → fail closed |
| instrument mapping availability | instrument map missing → fail closed |
| capability validation | adapter capabilities invalid → fail closed |
| configuration validation | invalid adapter config → fail closed |
| environment validation | live environment misconfigured → fail closed |

The frozen infrastructure already fails closed for adapter selection/mode
(ALREADY VERIFIED). The credential gates are new (DESIGNED FOR 17.7) and must
fail closed before any live adapter is constructed.

## 33. Live Execution Guard
**ALREADY VERIFIED (frozen gates) + DESIGNED FOR 17.7 (adapter gates).**
Complete pre-submission guard chain — a DESIGN CHECKLIST ONLY. Before a real
order can ever be submitted:

1. ExecutionCommand exists. — ALREADY VERIFIED (store)
2. Command is authorized. — ALREADY VERIFIED (16.2 factory)
3. Command is immutable. — ALREADY VERIFIED (frozen)
4. Execution mode is explicit. — ALREADY VERIFIED (scope-derived)
5. Correct broker is selected. — DESIGNED FOR 17.7 (explicit broker selection)
6. Correct adapter is selected. — ALREADY VERIFIED (select_adapter)
7. Adapter supports the requested operation. — ALREADY VERIFIED (supports/check)
8. Instrument mapping is valid. — DESIGNED FOR 17.7 (instrument map)
9. Order type is supported. — DESIGNED FOR 17.7 (order-type map)
10. Product is supported. — DESIGNED FOR 17.7 (product map)
11. Exchange is supported. — DESIGNED FOR 17.7 (token prefix)
12. Quantity is valid. — DESIGNED FOR 17.7 (lot/step)
13. Price/trigger price is valid. — DESIGNED FOR 17.7 (tick)
14. Client order ID is deterministic. — ALREADY VERIFIED (derive_client_order_id)
15. Duplicate submission is checked. — ALREADY VERIFIED (store guard + lifecycle)
16. Existing UNKNOWN state is reconciled. — ALREADY VERIFIED (ReconciliationRequiredError)
17. Required credentials are available. — DESIGNED FOR 17.7 (credential gate)
18. Broker client is healthy. — DESIGNED FOR 17.7 (adapter-level health check)
19. Request passes adapter validation. — DESIGNED FOR 17.7 (_validate_request)
20. Submission lifecycle is persisted. — ALREADY VERIFIED (SubmissionInfrastructure)
21. Broker result is normalized. — ALREADY VERIFIED (AdapterResult boundary)
22. Audit event is recorded. — ALREADY VERIFIED (SubmissionEvent)

## 34. Observability
**ALREADY VERIFIED (persistable today) + DESIGNED FOR 17.7 (adapter fields).**

| Field | Today | 17.7 |
|-------|-------|-------|
| command_id | persisted (command store) | — |
| client_order_id | persisted (lifecycle) | — |
| adapter identity | name / execution_mode (adapter registry) | UpstoxBrokerAdapter name |
| broker identity | — | "upstox" (adapter-owned constant) |
| execution mode | cp17_mode on lifecycle | — |
| lifecycle state | SubmissionState | — |
| normalized result | lifecycle state + last_reason | — |
| normalized error | BrokerError detail in events | — |
| reconciliation event | reconcile events flagged | — |
| retry decision | retry_allowed audit row | — |
| recovery action | RecoveryAction view | — |
| timestamps | lifecycle created_at / events | broker timestamps (validated) |
| correlation ID | — | Upstox request/response correlation id where available |

**Never log**: access token, API key, client secret, Authorization header,
password, credential material (Section 18). The broker client redacts before
any reason string reaches the adapter/audit.

## 35. Persistence Requirements
**ALREADY VERIFIED (sufficiency audit) + DESIGNED FOR 17.7 (adapter refs).**

Audit of whether existing persistence must retain the following for the real
adapter:

| Artifact | Existing persistence | 17.7 requirement |
|----------|---------------------|------------------|
| command_id | command store (16.5) | sufficient |
| client_order_id | submission lifecycle (17.2/17.3) | sufficient |
| broker order ID | `SubmissionLifecycle.broker_order_id` (downstream-only) | sufficient (already a field) |
| adapter identity | adapter registry (name/execution_mode) | sufficient; the lifecycle's `cp17_mode` binding exists |
| broker identity | — | adapter-owned constant; NOT persisted (derivable) |
| lifecycle state | submission store | sufficient |
| last normalized result | lifecycle state + last_reason + events | sufficient |
| reconciliation state | reconcile events + state | sufficient |
| timestamps | lifecycle created_at / events | sufficient |
| recovery state | `recovery_for_command` view (derived) | sufficient |

**Conclusion: existing persistence is sufficient for the real adapter.** No
persistence schema change is required. The broker `order_id` is already
captured on the lifecycle via `AdapterResult.broker_order_id` (frozen). The
adapter must NOT add new persisted fields for broker-specific data; any
broker-specific detail stays inside the adapter/client layer and is never
written to the stores. If 17.7 discovers a genuine need (e.g. a broker
`tag`→`client_order_id` mapping table for reconciliation), it must be
documented as a 17.7 implementation requirement and remain broker-neutral in
the persisted form — but the design does not anticipate any such need.

## 36. Broker API Versioning
**DESIGNED FOR 17.7.** How the adapter handles broker API changes.

- **API version**: the adapter pins the Upstox V3 API version it targets
  (e.g. `/v3/...` endpoints) as an adapter-owned constant.
- **Schema changes**: response parsing is defensive (Section 28); an
  unexpected/missing field → `MALFORMED_RESPONSE` → `UNKNOWN`, never a crash
  or a guessed value.
- **Deprecated fields**: the adapter does not depend on deprecated fields; a
  removed field surfaces as a malformed response (fail closed).
- **New order states**: an unknown status string → `UNKNOWN` (reconcile) until
  the adapter is updated with the new mapping (Section 25).
- **New error codes**: an unknown error code → `UNKNOWN_OUTCOME` (AMBIGUOUS)
  until the adapter is updated (Section 26).
- **Incompatible changes**: the adapter release carries the API version +
  mapping tables; an incompatible API change requires an adapter update. The
  CORE is insulated: it only ever sees `AdapterResult`/`BrokerError`, so
  broker API evolution never reaches the core (dependency direction, Section
  40).

## 37. Clock / Timing Requirements
**ALREADY VERIFIED (no wall-clock today) + DESIGNED FOR 17.7 (adapter policy).**

Based on 17.5 findings, the real adapter needs:

- **Request timeout**: bounded request timeout (Section 22).
- **Reconciliation timeout**: bounded reconcile timeout (Section 22).
- **Stale state**: stale-threshold check for `UNKNOWN`/`SUBMITTED` lifecycles.
- **Broker timestamp**: validated parseable broker timestamps (Section 28).
- **Local timestamp**: local time only for correlation, never identity.
- **Idempotency expiry**: broker-side idempotency TTL — NOT applicable (no
  broker-side idempotency claim; Section 20).
- **Retry windows**: bounded retry window for safe retries (Section 26).

**Clock abstraction decision**: the frozen execution modules have NO
`datetime.now()` and all timestamps are caller-supplied (ALREADY VERIFIED).
For 17.7, a clock abstraction (a controllable "now" injected into the adapter
client for timeout/stale/reconcile-window decisions) is **DESIGNED FOR 17.7**
and should be introduced ONLY at the adapter/client boundary — NOT in the
core, NOT in the frozen lifecycle/infrastructure. This keeps the frozen
modules deterministic while giving the adapter testable time behavior. It is
not introduced for theoretical purity; it is required for deterministic
timeout/stale testing of the broker client.

## 38. Failure-Injection Strategy
**DESIGNED FOR 17.7 — the tests 17.7 must implement.** No real broker is used.

1. accepted submission
2. rejected submission
3. validation failure (invalid quantity/price/order type/product/instrument)
4. insufficient funds
5. invalid instrument
6. invalid order type
7. timeout
8. unknown outcome
9. reconciliation accepted
10. reconciliation rejected
11. reconciliation unknown
12. duplicate submission (application-level guard)
13. duplicate broker response (broker reports duplicate)
14. malformed broker response
15. rate limit
16. broker unavailable
17. authentication failure
18. restart during submission
19. restart during UNKNOWN
20. paper/live mismatch
21. wrong adapter selection
22. missing credentials
23. invalid credentials
24. unsupported capability
25. cancellation timeout
26. cancellation race with fill

Implementation approach (DESIGNED FOR 17.7): a mocked/fake Upstox broker
client (injected into the adapter) that deterministically produces each
failure scenario — mirroring the existing `FakeBroker`/`ReferenceSimulation`
pattern. The adapter must be tested against the mock WITHOUT any network, any
credentials, or any real Upstox call.

## 39. Contract-Conformance Strategy
**ALREADY VERIFIED (reusable suite) + DESIGNED FOR 17.7 (adapter reuse).**

The future real adapter must prove conformance to the frozen `BrokerAdapter`
contract using the SAME generic contract suite used for `ReferenceBrokerAdapter`
(the reusable `BrokerAdapterContractConformanceBase` from
`tests/test_checkpoint_17_4_contract_conformance.py` — ALREADY VERIFIED it runs
against TWO adapters).

Design:

```
BrokerAdapter contract tests        (generic, reusable — exists)
        +
UpstoxBrokerAdapter-specific tests  (translation/error/state mapping — 17.7)
        +
integration-with-infrastructure     (SubmissionInfrastructure + adapter — 17.7)
```

The real adapter must NOT require changes to the generic contract merely
because it is a real broker. If a conformance failure is found, it is an
adapter defect, not a contract defect (the contract is frozen).

## 40. Dependency Direction
**ALREADY VERIFIED (current) + DESIGNED FOR 17.7 (target).**

Verified intended dependency direction:

```
Core
 ↓
BrokerAdapter
 ↓
Concrete Broker Adapter (UpstoxBrokerAdapter)
 ↓
Broker Client (UpstoxBrokerClient)
 ↓
Broker SDK/API (Upstox V3 HTTPS)
```

Never:

```
Core → Broker SDK
ExecutionCommand → Broker-specific order model
Trading Intelligence → Broker API
```

**Recommended package/module boundary for 17.7** (DESIGNED FOR 17.7):

- `src/engine/intelligence/upstox_broker_adapter.py` — the concrete adapter
  (implements `BrokerAdapter`; owns translation/normalization/mapping; imports
  only frozen models/contract + its own adapter-owned models).
- `src/engine/intelligence/upstox_broker_client.py` — the adapter-owned
  broker client boundary (owns HTTP/auth/credential injection; imports NO
  core domain models; the ONLY module that may reference Upstox API
  endpoints/URLs).
- `src/engine/intelligence/upstox_broker_models.py` (adapter-owned request/
  response models) — isolated from core domain models (mirrors
  `ReferenceBrokerRequest`/`ReferenceBrokerResponse`).
- `src/engine/intelligence/upstox_credential_provider.py` — broker-specific
  credential provider (injected into the client; the only module that reads
  the credential source). NO credential material in any other module.
- Tests under `tests/test_checkpoint_17_7_*.py` (mocked client, no network).

The generic execution system (models/intelligence/persistence) must have ZERO
imports of any `upstox_*` module.

## 41. Implementation Blueprint for 17.7
**DESIGNED FOR 17.7 — concrete blueprint.** Each proposed file: path,
responsibility, dependencies, prohibited dependencies, tests required.

| # | Path | Responsibility | Depends on | Prohibited deps | Tests |
|---|------|----------------|------------|-----------------|-------|
| 1 | `src/engine/intelligence/upstox_broker_models.py` | adapter-owned request/response models (frozen+slots) | stdlib (dataclasses, decimal, datetime, enum) | core domain models, network, credentials | request/response model tests |
| 2 | `src/engine/intelligence/upstox_broker_adapter.py` | concrete `UpstoxBrokerAdapter` implementing `BrokerAdapter`; translation, normalization, error/state/capability mapping | frozen `models.broker_adapter`, `models.execution_command`, `intelligence.broker_adapter_contract`, its own models | network, credentials, SDK, core analysis/decision/paper-trade modules | contract conformance + adapter-specific tests |
| 3 | `src/engine/intelligence/upstox_broker_client.py` | broker client boundary; owns HTTP/auth/credential injection; redaction; timeout policy | its own models + a credential provider protocol | core domain models, any non-upstox broker, credentials as literals | client unit tests with mocked transport |
| 4 | `src/engine/intelligence/upstox_credential_provider.py` | broker-specific credential provider (reads the credential source; yields access token) | stdlib (os) | core domain models, network | provider tests (no real token) |
| 5 | `tests/test_checkpoint_17_7_upstox_adapter.py` | contract conformance + translation + error/state mapping + mode binding + idempotency + reconciliation | frozen conformance base | real broker | — |
| 6 | `tests/test_checkpoint_17_7_upstox_client.py` | mocked-transport failure injection (26 scenarios) | mock transport | real broker | — |
| 7 | `tests/test_checkpoint_17_7_integration.py` | SubmissionInfrastructure + UpstoxBrokerAdapter end-to-end (mocked) | frozen infra | real broker | — |

**Dependency injection points:**

- Credential provider → `UpstoxBrokerClient` (constructor injection).
- Mock transport / HTTP client → `UpstoxBrokerClient` (constructor injection
  for tests).
- `UpstoxBrokerClient` → `UpstoxBrokerAdapter` (constructor injection).
- Adapter → adapter registry (`adapters` dict passed to `select_adapter` /
  `SubmissionInfrastructure`), no factory change required.

**Adapter factory changes**: none required in the frozen system. 17.7 adds
`paper_upstox_adapter()`/`live_upstox_adapter()` convenience factories
mirroring `paper_reference_adapter()`/`live_reference_adapter()` (the LIVE
factory must require a live credential provider and fail closed without it).

**Configuration boundary**: an adapter-owned frozen+slots config
(`UpstoxBrokerConfig`: name, execution_mode, timeout_seconds, retry budget,
instrument map reference, credential provider reference) validated at
construction.

**Credential boundary**: the credential provider protocol
(`UpstoxCredentialProvider` with `get_access_token() -> str`) injected ONLY
into the client. The adapter never calls it.

**Client boundary**: `UpstoxBrokerClient` is the ONLY module that may
construct Upstox API URLs and attach the Authorization header. It exposes
broker-neutral methods (`place_order(request)`, `get_order(tag/order_id)`,
`cancel_order(order_id)`) returning adapter-owned response models.

**Translation boundary**: `_translate_command(command) -> UpstoxBrokerRequest`
inside the adapter (mirrors `_translate_command` in the reference adapter).

**Response normalization boundary**: `_normalize_response(response) ->
AdapterResult` inside the adapter (mirrors the reference adapter).

**Error normalization boundary**: `_normalize_error(exc/response) ->
BrokerError` inside the adapter/client boundary.

**Reconciliation boundary**: `reconcile(client_order_id)` resolves the tag,
queries the client, normalizes.

**Persistence integration points**: none new — the frozen
`SubmissionInfrastructure` + stores are used unchanged.

**Testing strategy**: 26-scenario failure-injection matrix (Section 37) +
reusable contract conformance suite (Section 38) + integration tests with the
frozen infrastructure. NEVER a real broker in automated CI.

## 42. Upstox-Specific Design Appendix
**DESIGNED FOR 17.7.** Every item is labeled:

- **VERIFIED FROM PUBLIC DOCUMENTATION** — verified from authoritative public
  Upstox developer documentation during this checkpoint.
- **REQUIRES IMPLEMENTATION-TIME VERIFICATION** — must be re-verified against
  current Upstox docs during 17.7 before use.
- **UNKNOWN** — cannot be safely established from public documentation; the
  design fails closed.

### 42.1 Authentication boundary

- Upstox V3 trading APIs use `Authorization: Bearer {access_token}` headers
  (VERIFIED FROM PUBLIC DOCUMENTATION).
- The OAuth2 access-token / refresh flow details (endpoints, token TTL,
  refresh semantics) REQUIRES IMPLEMENTATION-TIME VERIFICATION.
- No credential material exists in this checkpoint (UNKNOWN — none added by
  design; the adapter fails closed without a token).

### 42.2 Instrument lookup
- Instrument tokens use the `NSE_EQ|INE...` format (VERIFIED FROM PUBLIC
  DOCUMENTATION; also already used by the repository's historical provider).
- The full instrument-list endpoint and its freshness REQUIRES
  IMPLEMENTATION-TIME VERIFICATION.
- The adapter's static map (Section 13) is the first-scope mechanism; a
  runtime lookup is optional and REQUIRES IMPLEMENTATION-TIME VERIFICATION.

### 42.3 Exchange mapping
- `NSE_EQ|...` for NSE equities (VERIFIED FROM PUBLIC DOCUMENTATION).
- `BSE_EQ|...` for BSE equities REQUIRES IMPLEMENTATION-TIME VERIFICATION
  (out of first scope).

### 42.4 Order submission translation
- Place-order request fields: `quantity`, `product`, `validity`, `price`,
  `tag`, `instrument_token`, `order_type`, `transaction_type`,
  `disclosed_quantity`, `trigger_price`, `is_amo`, `market_protection`
  (VERIFIED FROM PUBLIC DOCUMENTATION).
- `transaction_type` values `BUY`/`SELL` (VERIFIED FROM PUBLIC
  DOCUMENTATION).
- `order_type` values `MARKET`/`LIMIT`/`SL`/`SL-M` (VERIFIED FROM PUBLIC
  DOCUMENTATION).
- Exact `SL` (stop-limit) vs `SL-M` (stop-market) semantics REQUIRES
  IMPLEMENTATION-TIME VERIFICATION.
- Place-order response returns `data.order_ids` (array) (VERIFIED FROM PUBLIC
  DOCUMENTATION). Multiple order ids (slicing) REQUIRES IMPLEMENTATION-TIME
  VERIFICATION — the first-scope adapter must handle a single order id and
  treat a multi-id response as a CONCERN (map to `UNKNOWN`/manual review
  unless the semantics are confirmed).

### 42.5 Cancellation translation
- Cancel-order takes `order_id` (VERIFIED FROM PUBLIC DOCUMENTATION).
- Cancel response returns `data.order_id` (VERIFIED FROM PUBLIC
  DOCUMENTATION).

### 42.6 Order lookup / reconciliation
- Order-history (get order details) supports lookup by `order_id` or a
  `tag` (VERIFIED FROM PUBLIC DOCUMENTATION — "Order history can be retrieved
  by utilizing either the order_id or a tag").
- Orders remain available for one trading day and are removed at end of
  session (VERIFIED FROM PUBLIC DOCUMENTATION). This bounds the reconcile
  window.

### 42.7 Order-state normalization
- Publicly documented status families: `open`, `complete`, `cancelled`,
  `rejected`, pending (VERIFIED FROM PUBLIC DOCUMENTATION). The exact
  enumeration REQUIRES IMPLEMENTATION-TIME VERIFICATION; unknown status →
  `UNKNOWN`.

### 42.8 Error normalization
- Publicly documented error codes include `UDAPI1004`, `UDAPI1007`,
  `UDAPI1056`, `UDAPI1055`, `UDAPI1008`, `UDAPI1036`, `UDAPI1003`,
  `UDAPI1154`, `UDAPI1158`, `UDAPI100041` (VERIFIED FROM PUBLIC
  DOCUMENTATION). The complete catalog REQUIRES IMPLEMENTATION-TIME
  VERIFICATION; unknown codes → `UNKNOWN_OUTCOME`.

### 42.9 Rate-limit handling
- Upstox rate-limit/throttling behavior and headers REQUIRES
  IMPLEMENTATION-TIME VERIFICATION. A rate-limited submit is treated as
  ambiguous (`UNKNOWN`) unless the contract states otherwise (Section 26).

### 42.10 Idempotency strategy
- No client-supplied idempotency guarantee for place-order is established by
  public documentation (UNKNOWN — REQUIRES IMPLEMENTATION-TIME VERIFICATION).
  The adapter uses the `tag` for reconciliation, NOT as a claimed
  idempotency key (Section 20).

### 42.11 Response validation
- Response envelope: `status` (`success`/`error`), `data`, `metadata.latency`
  (VERIFIED FROM PUBLIC DOCUMENTATION). Validation per Section 28.

## 43. Security Review
**DESIGNED FOR 17.7 — 20-item threat analysis.** Classification: PASS /
CONCERN / BLOCKER.

| # | Threat | Classification | Analysis |
|---|--------|----------------|----------|
| 1 | Credential leakage | PASS (design) | credentials confined to the credential provider + client; redaction rule (Section 18); never in core/persistence/logs |
| 2 | Wrong broker | PASS (design) | explicit broker selection (Section 22/32); adapter registry keyed by name |
| 3 | Wrong execution mode | PASS (frozen) | `validate_adapter_mode` + `select_adapter` + `cp17_mode` (ALREADY VERIFIED) |
| 4 | Wrong instrument | PASS (design) | instrument map fail-closed (Section 13) |
| 5 | Wrong order type | PASS (design) | order-type map fail-closed (Section 14) |
| 6 | Duplicate submission | PASS (frozen) | store guard + lifecycle refusal + deterministic identity (ALREADY VERIFIED) |
| 7 | Unknown outcome | PASS (frozen) | `UNKNOWN` → reconcile-before-retry (ALREADY VERIFIED) |
| 8 | Unsafe retry | PASS (frozen) | blind retry prohibited (ALREADY VERIFIED); SAFE/UNSAFE retry rule (Section 26) |
| 9 | Stale reconciliation | PASS (design) | bounded reconcile window + stale surfacing (Sections 22/23) |
| 10 | Malformed response | PASS (design) | strict response validation → `MALFORMED_RESPONSE` → `UNKNOWN` (Section 28) |
| 11 | Broker outage | PASS (design) | `BROKER_UNAVAILABLE` (TRANSPORT) + bounded retry (Section 27) |
| 12 | Rate limiting | PASS (design) | `RATE_LIMIT` + SAFE/UNSAFE retry + ambiguity on submit (Section 26) |
| 13 | API schema change | PASS (design) | defensive parsing + API version pinning (Section 35) |
| 14 | Credential expiration | PASS (design) | provider refresh + fail-closed gate (Section 17) |
| 15 | Authentication failure | PASS (design) | `AUTHENTICATION_FAILURE` (TRANSPORT) + fail-closed (Section 17) |
| 16 | Paper/live cross-contamination | PASS (frozen) | mode binding + selection + credential separation (Section 30) |
| 17 | Adapter misconfiguration | PASS (design) | startup gates fail closed (Section 31) |
| 18 | Persistence corruption | PASS (frozen) | atomic writes + schema versioning + integrity checks (ALREADY VERIFIED) |
| 19 | Audit corruption | PASS (frozen) | deterministic events + store integrity (ALREADY VERIFIED) |
| 20 | Dependency contamination | PASS (frozen+design) | dependency direction enforced; no core→SDK imports (Section 39/44) |

**BLOCKER: NONE.**

## 44. Repository Safety Sweep
**ALREADY VERIFIED — sweep results (this checkpoint, matching 17.5):**

| Search | Result |
|--------|--------|
| `.env` files in repo | NONE |
| hard-coded secret-like literals in src/scripts/tests | 0 matches (only docstring/test negative lists) |
| network imports (requests/httpx/socket/websocket/aiohttp/urllib/http) in execution/broker modules | 0 |
| urllib/network imports repo-wide | exactly 1 module: `engine/data/historical_provider.py` (historical data only) |
| broker SDK refs (kiteconnect/pyotp/zerodha/place_order) in execution modules | 0 |
| Upstox refs in execution/broker modules | 0 (Upstox refs exist only in the data layer) |
| os.environ credential reads in execution modules | 0 |
| os.environ token reads repo-wide | exactly 2 (historical provider + corpus-ingestion precheck) — NOT execution |
| credentials in command/authorization/intent/submission/adapter/persistence/logs/audit/models | 0 |

**Distinction (explicit):**

- **Historical-data provider usage**: `UPSTOX_ANALYTICS_TOKEN` is read lazily
  ONLY by `engine/data/historical_provider.py` (read-only OHLCV data) and the
  corpus-ingestion precheck. It is sent ONLY in that provider's
  `Authorization: Bearer` header. It is a candle-data credential, never an
  execution credential, and never imported by any execution artifact.
- **Execution-broker integration**: does NOT exist. The future live execution
  adapter uses a SEPARATE access-token credential (Section 17) that does not
  exist in this checkpoint. No execution module reads any environment
  variable, imports any network library, or references any broker SDK.

**No real broker integration exists. No credentials exist. No network path
exists.**

## 45. Code Changes

**17.6 produced:**

- **ZERO production-code changes.**
- **ZERO test-code changes.**
- ZERO build/config changes.
- The only files created are this design document and the AGENTS.md entry
  (Section 46/47).

No frozen checkpoint file was modified. No real broker functionality was
introduced. The repository remains fully offline and network-free.

## 46. Testing Report

**ALREADY VERIFIED — full test-suite verification run during 17.6:**

| Suite | Result |
|-------|--------|
| 17.2 contract tests (`tests/test_checkpoint_17_2_contract.py`) | 25 passed |
| 17.2 store tests (`tests/test_checkpoint_17_2_store.py`) | 21 passed |
| 17.2 fake-broker tests (`tests/test_checkpoint_17_2_fake_broker.py`) | 26 passed |
| 17.3 infrastructure tests (`tests/test_checkpoint_17_3_infrastructure.py`) | 84 passed |
| 17.4 conformance tests (`tests/test_checkpoint_17_4_contract_conformance.py`) | 74 passed |
| 17.4 reference-adapter tests (`tests/test_checkpoint_17_4_reference_adapter.py`) | 77 passed |
| 17.4 integration tests (`tests/test_checkpoint_17_4_integration.py`) | 35 passed |
| **Checkpoint 17.2–17.4 subtotal** | **342 passed** |
| Frozen 14–16 execution suite (command+store, authorization+engine+store, intent+engine+app) | 627 passed |
| **FULL SUITE (`tests/`)** | **5823 passed, 0 failed, 2 warnings** |

No test was modified. The full-suite result EXACTLY matches the Checkpoint
17.4/17.5 baseline (5823 passed / 0 failed / 2 warnings) — **no regression.**

## 47. Files Inspected

**Docs:**

- `docs/checkpoint_17_1_broker_adapter_boundary_audit.md`
- `docs/checkpoint_17_2_broker_adapter_contract_design.md`
- `docs/checkpoint_17_3_broker_adapter_infrastructure_audit.md`
- `docs/checkpoint_17_4_reference_adapter_audit.md`
- `docs/checkpoint_17_5_real_broker_integration_preparation_audit.md`
- `AGENTS.md`

**Source (verified by reading, not doc-only):**

- `src/engine/models/execution_command.py` (immutable command, ExecutionMode,
  factory)
- `src/engine/models/execution_authorization.py`
- `src/engine/models/operational_trade_intent.py`
- `src/engine/models/broker_adapter.py` (BrokerResultStatus, AdapterResult,
  BrokerError/Code/Category, AdapterCapability)
- `src/engine/models/submission_lifecycle.py`
- `src/engine/intelligence/broker_adapter_contract.py` (BrokerAdapter Protocol,
  AdapterCapabilities, derive_client_order_id, derive_idempotency_key,
  validate_adapter_mode, select_adapter)
- `src/engine/intelligence/submission_lifecycle.py` (engine + transition table)
- `src/engine/intelligence/broker_adapter_infrastructure.py`
- `src/engine/intelligence/reference_broker_adapter.py`
- `src/engine/intelligence/fake_broker.py`
- `src/engine/persistence/submission_serialization.py`, `submission_store.py`
- `src/engine/persistence/execution_command_serialization.py`, `execution_command_store.py`
- `src/engine/persistence/execution_authorization_serialization.py`, `execution_authorization_store.py`
- `src/engine/persistence/exceptions.py`
- `src/engine/data/historical_provider.py` (historical Upstox provider — to
  verify the historical-data vs execution distinction)

**Tests inspected/run:**

- All Checkpoint 17.2–17.4 test files; frozen 14–16 execution test files;
  `tests/_checkpoint_17_2_fixtures.py`.

**Public Upstox documentation** (verified facts in Section 42):
place/cancel/modify/order-history developer pages, order-status appendix.

## 48. PASS Findings

| # | Finding |
|---|---------|
| 17.6-F01 | Target broker explicitly identified (Upstox, V3). |
| 17.6-F02 | Adapter architecture fully designed (translation/response/error/capability/instrument/order-state/idempotency/reconciliation/client boundaries). |
| 17.6-F03 | Complete translation matrix produced (Section 12). |
| 17.6-F04 | Instrument mapping designed (canonical source of truth, ownership, freshness, fail-closed behaviors). |
| 17.6-F05 | Order-type mapping designed (MARKET/LIMIT/SL/SL-M, invalid combos, semantic-equivalence requirement). |
| 17.6-F06 | Product/validity/exchange mapping designed with NO silent fallback. |
| 17.6-F07 | Capability model requirements defined against frozen AdapterCapabilities. |
| 17.6-F08 | Authentication boundary defined (credential provider → client; adapter never reads token). |
| 17.6-F09 | Credential isolation defined (never in core/persistence/logs/fixtures; redaction rule). |
| 17.6-F10 | Client-order-ID strategy defined (deterministic, collision-safe, no silent truncation). |
| 17.6-F11 | Broker idempotency strategy defined with the command_id/client_order_id/idempotency_key/broker-order-id distinction; no false claims. |
| 17.6-F12 | Submission protocol with every validation gate + failure behavior defined. |
| 17.6-F13 | Timeout behavior defined (bounded request/reconcile timeouts → UNKNOWN, never FAILED). |
| 17.6-F14 | UNKNOWN behavior defined (reconcile-before-retry; never retry/resubmit blind). |
| 17.6-F15 | Reconciliation protocol defined (client_order_id/tag primary; order_id fallback justified; 9 match outcomes). |
| 17.6-F16 | Order-state mapping defined (never force an unsafe mapping; unknown → UNKNOWN). |
| 17.6-F17 | Error mapping defined (matrix + unknown code → UNKNOWN_OUTCOME). |
| 17.6-F18 | Rate-limit/retry policy defined (SAFE vs UNSAFE retry; submit rate-limit treated ambiguous). |
| 17.6-F19 | Response validation defined (structure/ids/status/quantities/prices/timestamps/errors/correlation; HTTP success ≠ order success). |
| 17.6-F20 | Cancellation design defined (order_id, valid states, timeout → UNKNOWN, fill/cancel races). |
| 17.6-F21 | Paper/live isolation defined (no LIVE→PAPER or PAPER→LIVE fallback). |
| 17.6-F22 | Startup safety defined (9 gates, all fail closed). |
| 17.6-F23 | Live-execution guard chain defined (22 gates checklist). |
| 17.6-F24 | Observability requirements defined (identity/adapter/broker/state/result/error/reconcile/retry/recovery/correlation; never credentials). |
| 17.6-F25 | Persistence requirements audited — existing persistence is SUFFICIENT; no schema change. |
| 17.6-F26 | Failure-injection test spec designed (26 scenarios). |
| 17.6-F27 | Contract-conformance strategy defined (reusable generic suite + adapter-specific + infra integration). |
| 17.6-F28 | Dependency direction verified (Core → BrokerAdapter → Concrete Adapter → Client → API; never Core → SDK). |
| 17.6-F29 | 17.7 implementation blueprint complete (modules/classes/protocols/deps/prohibited-deps/tests). |
| 17.6-F30 | Security review complete (20-item threat model; 0 BLOCKER). |
| 17.6-F31 | Repository safety sweep complete (0 secrets/network/SDK in execution path). |
| 17.6-F32 | No real broker connection occurred. |
| 17.6-F33 | No credentials were added. |
| 17.6-F34 | No broker SDK was added. |
| 17.6-F35 | No network path was introduced. |
| 17.6-F36 | Checkpoints 10–16 remain frozen (none modified). |
| 17.6-F37 | Full test suite shows NO regression (5823 passed / 0 failed / 2 warnings — matches 17.4/17.5 baseline). |
| 17.6-F38 | Documentation complete (this document, 53 sections). |
| 17.6-F39 | AGENTS.md appended through Checkpoint 17.6. |
| 17.6-F40 | Checkpoint 17.7 recommendation explicit (below). |

## 49. CONCERN Findings

CONCERN items are design-time implementation details for 17.7 — the current
architecture remains safe and none block proceeding:

| # | Finding | Where it must be addressed |
|---|---------|---------------------------|
| 17.6-C01 | Exact Upstox `tag` field constraints (length/allowed characters) not verifiable from the public docs used; must be confirmed at implementation time | 17.7 (client-order-ID mapping) |
| 17.6-C02 | Whether Upstox place-order offers ANY client-supplied idempotency guarantee is UNKNOWN; adapter must not claim it | 17.7 (idempotency mapper) |
| 17.6-C03 | Upstox `order_type` semantics (`SL` stop-limit vs `SL-M` stop-market) and price/trigger requirements must be re-verified | 17.7 (order-type mapper) |
| 17.6-C04 | Exact Upstox order-status enumeration (beyond the publicly documented open/complete/cancelled/rejected families) must be confirmed before final state mapping | 17.7 (order-state mapper) |
| 17.6-C05 | Complete Upstox error-code catalog must be confirmed; unknown codes → `UNKNOWN_OUTCOME` | 17.7 (error mapper) |
| 17.6-C06 | Upstox rate-limit headers/limits must be confirmed; a rate-limited submit is treated ambiguous | 17.7 (rate-limit policy) |
| 17.6-C07 | The `order_ids` array may contain multiple ids (slicing); multi-id semantics must be confirmed | 17.7 (response validation) |
| 17.6-C08 | Upstox order-history availability/retention (public docs: ~1 trading day) informs the reconcile-window policy | 17.7 (reconciliation policy) |
| 17.6-C09 | An adapter-level clock abstraction is DESIGNED for the client boundary only; do not introduce it into frozen modules | 17.7 (clock/timing) |
| 17.6-C10 | Live credential provider (OAuth2 flow) details are broker-specific and must be implemented WITHOUT committing any credential; the live adapter fails closed until then | 17.7 (credential boundary) |

## 50. BLOCKER Findings

**NONE.**

The absence of a real broker connection, a real broker SDK, live credentials,
and a live network path is INTENTIONAL and NOT a blocker (stated explicitly in
the checkpoint instructions). No safety or architectural defect prevents
proceeding to the 17.7 implementation checkpoint.

## 51. Known Limitations

- This is a DESIGN checkpoint: nothing real is implemented.
- The report contains no executable broker code; only interfaces, protocols,
  schemas, mapping tables, dependency-injection seams, and design text.
- Broker-specific facts that could not be verified from public documentation
  are explicitly labeled UNKNOWN / REQUIRES IMPLEMENTATION-TIME VERIFICATION
  (Section 42) and the design fails closed where they matter.
- No hidden network path, credential, or SDK was introduced (sweep-verified).
- A PASS here does NOT authorize live trading.
- Operational deployment controls (account provisioning, static-IP
  whitelisting, environment separation of production live deployments) are OUT
  OF SCOPE for 17.7.

## 52. Recommended Checkpoint 17.7

**17.7 — REAL BROKER ADAPTER IMPLEMENTATION (NO LIVE SUBMISSION).**

Implement the Upstox broker adapter design against the frozen contract in
this exact order, WITH a mocked broker client and NO credentials/network/SDK:

1. `upstox_broker_models.py` (adapter-owned request/response models).
2. `upstox_broker_credential_provider.py` (credential provider PROTOCOL only;
   the concrete provider reads no real credential in tests).
3. `upstox_broker_client.py` (broker client boundary; mocked transport;
   timeout/redaction policy; adapter-owned URLs allowed here ONLY in design
   constants — no network calls).
4. `upstox_broker_adapter.py` (concrete adapter: translation, normalization,
   mapping; `paper_upstox_adapter()`; `live_upstox_adapter()` fail-closed
   without a live credential provider).
5. Reusable contract-conformance suite run against the new adapter
   (proving the generic contract needs NO change).
6. 26-scenario failure-injection tests (mocked client).
7. Integration tests with `SubmissionInfrastructure` (mocked).
8. Re-run the full suite with ZERO regression.
9. Re-run the repository safety sweep (assert zero network/credential/SDK in
   all new modules).
10. Update AGENTS.md + add `docs/checkpoint_17_7_*.md`.

**Hard rules for 17.7**: no real broker connection; no credentials; no SDK;
no API calls; no network; no live/sandbox trading against a real external
service; do NOT connect to Upstox. A separate explicit authorization is
required before any real-broker/sandbox integration checkpoint (17.8+).

**Do NOT start Checkpoint 17.7 automatically — wait for explicit
authorization.**

## 53. Final Verdict

**PASS**

- The target broker is explicitly identified (Upstox).
- The real-broker adapter is fully designed against the frozen broker-neutral
  contract with a complete translation matrix, mapping boundaries, auth/
  credential boundary, idempotency design, submission protocol, timeout/
  UNKNOWN/reconciliation design, order-state/error mapping, rate-limit/retry
  policy, response validation, cancellation design, paper/live isolation,
  startup safety, observability, persistence audit, failure-injection spec,
  conformance strategy, dependency direction, and a concrete 17.7
  implementation blueprint.
- Security review: 20 threats analyzed; 0 BLOCKER. Repository sweep clean.
- Full suite: 5823 passed / 0 failed / 2 warnings (no regression).
- ZERO production/test code changes. Checkpoints 10–16 remain frozen.
- No real broker connection, no credentials, no SDK, no network path.

**A PASS DOES NOT AUTHORIZE LIVE TRADING. A PASS DOES NOT AUTHORIZE REAL
BROKER CONNECTION. A PASS ONLY MEANS: "The real broker adapter has been
sufficiently designed to proceed to the implementation checkpoint."**

The repository remains offline, paper/simulation-only, and fail-closed by
construction. Proceed to Checkpoint 17.7 only on explicit authorization.
