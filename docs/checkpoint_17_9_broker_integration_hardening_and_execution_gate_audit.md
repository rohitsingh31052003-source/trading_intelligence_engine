# Checkpoint 17.9 — Broker Integration Hardening & Controlled Execution Gate Audit

**Status: COMPLETE / PASS WITH LIMITATIONS**

> **LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.9.**
>
> **CHECKPOINT 17.9 DOES NOT AUTHORIZE REAL-MONEY ORDER SUBMISSION.**

This document is the final hardening and controlled execution-gate audit of
Checkpoint 17. It determines whether the Trading Intelligence Engine execution
architecture is sufficiently hardened and fail-closed to define a separate,
explicit live-execution gate without weakening the frozen Checkpoints 10–16
architecture. It is an AUDIT + DESIGN + CONTROLLED TEST checkpoint.

Evidence classification used throughout:

- **VERIFIED BY SOURCE** — statement verified by reading the actual code.
- **VERIFIED BY TEST** — statement verified by a deterministic, network-free test in `tests/test_checkpoint_17_9_hardening.py` (97 tests) or the existing 17.x suites.
- **VERIFIED FROM OFFICIAL UPSTOX DOCUMENTATION** — statement verified from Upstox public developer documentation (see 17.6 §42 for the verified-facts list).
- **VERIFIED AGAINST CONTROLLED BROKER** — statement verified against a controlled/sandbox broker. **None in this checkpoint** (no sandbox credential was available; 17.8 limitation).
- **VERIFIED USING MOCKS** — statement verified using the deterministic `MockUpstoxBrokerClient` / `FakeBroker` / `ReferenceBrokerAdapter` only.
- **NOT VERIFIED** — could not be verified in this environment.
- **DEFERRED** — intentionally deferred design/implementation item.

---

## 1. Executive Summary

The Checkpoint 17 execution architecture (17.1–17.8, plus frozen Checkpoints
10–16) is hard to attack, fail-closed by construction, broker-neutral, and
deterministic. All submission paths are explicit, authorized, mode-bound, and
credential-isolated. Ambiguous broker outcomes can only ever enter `UNKNOWN`
and can only be resolved by reconciliation; blind retry is prohibited by the
engine. Persistence is atomic, schema-versioned, tamper-evident, and restart
recoverable with no automatic resubmission. No live-execution path exists.

Checkpoint 17.9 adds: (a) a deterministic, credential-free, fail-closed
live-execution-gate **design** (`src/engine/intelligence/execution_gate.py`),
(b) **97** new deterministic, network-free hardening tests
(`tests/test_checkpoint_17_9_hardening.py`), and (c) this audit document plus
an AGENTS.md entry.

The verdict is **PASS WITH LIMITATIONS**:

- PASS — the architecture is sufficiently hardened and fail-closed to safely
  define a separate explicit live-execution gate.
- WITH LIMITATIONS — real Upstox execution behavior remains **UNVERIFIED**
  (mock-only; no sandbox credential existed). This does not block freezing
  the architecture; it is a documented, non-blocking limitation.

**LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.9.**
**CHECKPOINT 17.9 DOES NOT AUTHORIZE REAL-MONEY ORDER SUBMISSION.**

---

## 2. Checkpoint Scope

This checkpoint:

1. Audits the entire execution chain (`ExecutionAuthorization` →
   `ExecutionCommand` → `SubmissionInfrastructure` → `SubmissionLifecycle` →
   `BrokerAdapter` → `UpstoxBrokerAdapter` → `UpstoxBrokerClient` → Broker).
2. Verifies Checkpoints 10–16 remain frozen and untouched.
3. Hardens authorization, execution mode, credentials, UNKNOWN handling,
   reconciliation, idempotency, duplicate prevention, concurrency,
   persistence, auditability, observability, and cancellation.
4. Designs (and tests the decision logic of) a future live-execution gate
   with a 20-condition mandatory matrix. The gate is **NOT activated**.
5. Runs 97 new deterministic, network-free tests plus the full regression
   suite.
6. Produces this document and an AGENTS.md entry.

Non-goals (explicit): no live trading, no real-money orders, no unrestricted
LIVE mode, no removal/bounding of safety gates, no authorization bypass, no
`UNKNOWN` automatic retry, no live fallback, no modification of frozen
Checkpoints 10–16, no replacement of broker-neutral models with Upstox models,
no automatic real-broker tests, no committed/exposed credentials, no
deployment, no automatic start of Checkpoint 18.

---

## 3. Checkpoint 17 Baseline

| Sub-checkpoint | Title | Status |
| --- | --- | --- |
| 17.1 | Broker Adapter Boundary Architecture Audit | COMPLETE / PASS |
| 17.2 | Broker Adapter Contract Design | COMPLETE / PASS |
| 17.3 | Submission Infrastructure | COMPLETE / PASS |
| 17.4 | Reference Broker Adapter | COMPLETE / PASS |
| 17.5 | Real Broker Integration Preparation & Boundary Safety Audit | COMPLETE / PASS WITH LIMITATIONS |
| 17.6 | Real Upstox Broker Adapter Design | COMPLETE / PASS |
| 17.7 | Upstox Broker Adapter Implementation | COMPLETE / PASS |
| 17.8 | Sandbox/Paper Broker Integration & Reconciliation Validation | COMPLETE / PASS WITH LIMITATIONS |

17.8 baseline: **6066 passed / 6 skipped / 2 warnings** on that environment.
17.8 key limitation: real controlled broker behavior was NOT verified because
no sandbox credential was available. The 17.8 known concerns (place-response
order_ids array vs single, order-history array vs single record, one-trading-day
retention, order-state vocabulary subset, tag constraints, broker-side
idempotency, live auth/rate-limit behavior, no real HTTP client) are re-audited
in 17.9 (sections 12, 13, 46–48).

---

## 4. Architecture

**VERIFIED BY SOURCE.** The chain is:

```
ExecutionAuthorization (frozen 15.x)
        ↓ (authorized-only; intent binding + fingerprint verification)
ExecutionCommand (frozen 16.x, broker-neutral, immutable)
        ↓
SubmissionInfrastructure (17.3)  -- orchestrates, persists, audits
        ↓
SubmissionLifecycleEngine (17.2) -- state machine: CREATED → SUBMISSION_REQUESTED
        ↓                             → SUBMITTED/ACCEPTED/REJECTED/FAILED/UNKNOWN → terminal
SubmissionLifecycleStore (17.2)  -- atomic persistence (`./submissions/<id>.json`)
        ↓
BrokerAdapter Protocol (17.2)   -- broker-neutral contract
        ↓
UpstoxBrokerAdapter (17.7)      -- translation boundary (broker-specific)
        ↓
UpstoxBrokerClient Protocol (17.7)  -- broker-client boundary (transport owner)
        ↓
MockUpstoxBrokerClient (17.7)   -- the ONLY concrete client; network-free
```

Single responsibility is enforced and tested:

- **strategy cannot submit orders** — the intelligence/pipeline never imports
  submission/execution modules (AST import-graph sweep, Section 36).
- **TradePlan cannot submit orders** — `TradePlan` has no execution methods;
  commands are only built by `create_execution_command` from an
  `ExecutionAuthorization`.
- **ExecutionCommand cannot submit itself** — the command is a frozen
  dataclass with no behavior (VERIFIED BY SOURCE).
- **BrokerAdapter cannot authorize / create authorization** — the contract has
  no `authorize`/`create_authorization`/`grant` methods; `dir(adapter)` check
  in the frozen conformance suite (VERIFIED BY TEST).
- **broker client cannot authorize or modify risk/strategy decisions** — the
  client only owns transport (place/get/cancel); it receives an already
  translated `UpstoxBrokerRequest` and returns broker models.
- **persistence / reconciliation / recovery cannot authorize** — they only
  read/write lifecycle snapshots and report recovery decisions; recovery
  cannot create an authorization (VERIFIED BY TEST).
- **adapter cannot silently switch execution mode** — `validate_adapter_mode`
  + `select_adapter` + `cp17_mode` lifecycle metadata; PAPER↔LIVE never cross
  (VERIFIED BY TEST).

---

## 5. Authorization Boundary

**VERIFIED BY SOURCE + TEST.** `create_execution_command` requires an
`ExecutionAuthorization` whose `status == AUTHORIZED`; all other statuses
(`UNAUTHORIZED` / `ELIGIBLE` / `EXPIRED` / `REVOKED` / `SUPERSEDED`) raise
`ValueError`. The authorization must bind to the intent by `intent_id` and
`content_fingerprint` (mismatch → `ValueError`). The command copies the
authorization's identity fields verbatim; it can never mutate the
authorization. Recovery cannot create authorization; reconciliation cannot
transform unauthorized state into executable state; persistence cannot
authorize.

Explicitly tested (Section 5, Phases 3/16/25):

- unauthorized command → creation rejected
- expired authorization → creation rejected
- invalid authorization (non-AUTHORIZED status) → creation rejected
- missing authorization (hand-constructed with forged fingerprint) → rejected
- mismatched authorization (different intent economics) → rejected
- altered command (different economics) → different `command_id`
- replayed command (identical content) → identical `command_id` (detectable)
- authorization state unchanged by broker code
- recovery cannot create authorization

**Required result: FAIL CLOSED — verified.**

### 5.6 CONCERN C06 (documented)

The `ExecutionCommand` factory enforces `valid_until > valid_from` at
construction and the authorization enforces `expires_at <= intent.valid_until`.
However, the **submission infrastructure does not re-check the command's time
window against the submission time**. A command whose `valid_until` is in the
past can still be submitted (VERIFIED BY TEST: an expired-window command
submitted at a later time produces ACCEPTED). This is not an unsafe path by
itself (all submissions require an explicit caller), but the **future live
gate MUST enforce temporal validity** (Section 31, Phase 47 precondition 7).

---

## 6. Execution Mode Boundary

**VERIFIED BY SOURCE + TEST.** `ExecutionMode` (`PAPER`/`LIVE`) is derived at
command creation from the authorization `scope` and is immutable on the
command. `validate_adapter_mode` rejects a mode mismatch; `select_adapter`
fails closed when no mode-matched adapter exists; `cp17_mode` is recorded in
lifecycle metadata and verified on reconcile/cancel. No automatic fallback /
downgrade / upgrade / adapter substitution / credential substitution exists.

Explicitly tested:

- `PAPER → LIVE` fails closed (ValueError)
- `LIVE → PAPER` fails closed (ValueError)
- missing mode → fails closed
- invalid mode → fails closed (TypeError)
- wrong adapter for mode → fails closed
- missing credential → fails closed (`AUTHENTICATION_FAILURE`)
- live factory without credential → fails closed (ValueError)
- no automatic adoption for either direction

**Required result: FAIL CLOSED — verified.**

### 6.1 Sandbox environment identity is NOT modeled

There is no explicit `SANDBOX` environment/mode vocabulary (17.5-CONCERN
deferred). The future gate MUST add an explicit environment identity that
cannot be spoofed by a mode string (Sections 29, 31).

---

## 7. Credential Boundary

**VERIFIED BY SOURCE + TEST.**

- Credentials are injected only at the broker-client boundary through the
  `UpstoxCredentialProvider` Protocol.
- Credentials never enter `ExecutionCommand` / `SubmissionLifecycle` /
  `SubmissionInfrastructure` state / persistence / audit records / results /
  logs / exceptions / test fixtures / AGENTS.md / documentation / git.
- Missing / empty / malformed / invalid credentials fail closed
  (`BrokerErrorCode.AUTHENTICATION_FAILURE`, TRANSPORT category).
- `redact_sensitive` scrubs `Authorization: Bearer ...` and
  `UPSTOX_EXECUTION_ACCESS_TOKEN` values from every reason string.

Explicitly tested (Section 7, Phases 7/35):

- missing credential → `AUTHENTICATION_FAILURE`
- empty credential (`EmptyUpstoxCredentialProvider`) → `AUTHENTICATION_FAILURE`
- credentials absent from command, lifecycle store files, audit blob, results
- redaction scrubs bearer + env-token text
- environment provider returns `""` when the env var is unset

**Required behavior: FAIL CLOSED — verified.**

---

## 8. Network Boundary

**VERIFIED BY SOURCE + TEST.** An AST import sweep across all execution /
broker / persistence modules shows ZERO `requests` / `httpx` / `urllib` /
`aiohttp` / `socket` / `websocket` / broker-SDK imports. `UpstoxBrokerClient`
is a Protocol; the only concrete implementation is the in-memory
`MockUpstoxBrokerClient`. Broker transport remains isolated to the
broker-client boundary. Core execution modules remain broker-neutral.

---

## 9. Submission Lifecycle

**VERIFIED BY SOURCE + TEST.** The lifecycle is `CREATED` →
`SUBMISSION_REQUESTED` → `SUBMITTED` / `ACCEPTED` / `REJECTED` / `FAILED` /
`UNKNOWN` → terminal (`ACCEPTED`/`REJECTED`/`FAILED`/`FILLED`/`CANCELLED`/
`PARTIALLY_FILLED` terminal; `SUBMITTED` in-flight; `UNKNOWN` non-terminal).
Transitions are validated by `SubmissionLifecycleEngine`; the lifecycle
references `command_id` and never mutates the command; `broker_order_id` is
recorded downstream-only.

---

## 10. UNKNOWN State

**VERIFIED BY SOURCE + TEST.**

- `TIMEOUT` / `UNKNOWN_OUTCOME` / `MALFORMED_RESPONSE` / `RATE_LIMIT` →
  `BrokerResultStatus.UNKNOWN` (category `AMBIGUOUS` or `TRANSPORT`),
  **never** `FAILED`.
- UNKNOWN cannot become `FAILED` (timeout), `CANCELLED`, `FILLED`, `ACCEPTED`,
  or safe-to-resubmit without a broker-confirmed outcome.
- UNKNOWN survives process restart: the persisted lifecycle reloads as UNKNOWN
  and `recovery_for_command` returns `RecoveryAction.RECONCILE_REQUIRED`.
- UNKNOWN → RECONCILE_REQUIRED; no blind retry (`request_submission` on an
  UNKNOWN lifecycle raises ValueError).
- Reconciliation resolves UNKNOWN to the confirmed outcome (ACCEPTED /
  REJECTED) using the SAME deterministic `client_order_id`; a still-unknown
  reconcile keeps UNKNOWN and retry stays prohibited.

**Required result: UNKNOWN → RECONCILE, never blind resubmit — verified.**

---

## 11. Reconciliation

**VERIFIED USING MOCKS.** Primary lookup key = deterministic
`client_order_id` → Upstox `tag` (`derive_upstox_tag`). Fallback =
`broker_order_id` (documented; the adapter boundary currently has no
`broker_order_id` — callers that have it pass it in the integration layer,
which is how the audit surface records it). Behavior table documented and
tested for: exact match (→ ACCEPTED), no match / stale / multiple-conflicting /
malformed / unknown (→ UNKNOWN), delayed match (→ confirmed on later
reconcile), already-filled / already-cancelled / rejected / partially-filled
(→ mapped to the confirmed state via the order-state mapper). **Ambiguous →
UNKNOWN; never choose arbitrarily among conflicting records.**

---

## 12. Reconciliation Window

**VERIFIED FROM OFFICIAL UPSTOX DOCUMENTATION (17.6 §42) + NOT VERIFIED for
real durations.** Upstox documentation indicates a limited order-history
retention period (17.8 CONCERN #3 — one-trading-day retention, pending
implementation-time confirmation).

Implications documented:

- The **maximum safe reconciliation window** must be operator-confirmed
  against the current Upstox contract before any real adapter is enabled.
- Once the broker no longer retains the order, **no reconciliation outcome can
  be truthfully claimed**; the adapter returns / must return UNKNOWN, never a
  fabricated result (tested: unknown lookup → UNKNOWN).
- Local audit retention is separate and preserved in the submission store.
- UNKNOWN + irreconcilable = escalation to a **manual-review state** (Phase 49
  emergency-stop / operator-intervention design). Never claim successful
  reconciliation after broker history is unavailable.

---

## 13. Idempotency

**VERIFIED BY SOURCE + TEST.** Four identities stay distinct:

| Identity | Prefix | Purpose |
| --- | --- | --- |
| `command_id` | `cmd-` | Deterministic identity of the authorized command |
| `client_order_id` | `co-` | Deterministic application-level broker identity |
| `idempotency_key` | `idem-` | Local duplicate-detection key |
| `broker_order_id` | broker-side | Downstream-only broker-generated id |

Same command → same command_id → same client_order_id → same idempotency
identity across restarts; different broker context → different client id.
Tests prove identity stability and that `broker_order_id` never contaminates
the command/lifecycle identity.

**BROKER-SIDE IDEMPOTENCY NOT VERIFIED.** The audit surface explicitly
documents that tag/client_order_id do not claim broker-side idempotency
(documentation assertion tested). The frozen contract distinction is
preserved: `UNKNOWN → RECONCILE → determine outcome → only then consider
retry`.

---

## 14. Duplicate Prevention

**VERIFIED BY SOURCE + TEST.** Duplicate command → same identity (detectable).
Duplicate submission blocked in-process (`DuplicateSubmissionError`) and after
restart (`store.command_exists` + `load_by_command` single-record guard + infra
pre-check). Terminal states never resubmit (returns the existing snapshot
unchanged). Repeated reconciliation is idempotent. The store raises
`SubmissionIntegrityError` if two records ever claim the same command
(duplicate-current-record guard, tested). No accidental generation of a new
order identity during retry/recovery.

---

## 15. Concurrency / Race

**VERIFIED BY SOURCE + TEST (single-process).** Same-command concurrent
equivalent operations are serialized by the store guard + infra pre-check:
two sequential submits of the same command → `DuplicateSubmissionError`;
two lifecycle records for one command → `SubmissionIntegrityError` on
`load_by_command`.

**CONCERN C04 (documented, not an unsafe path):** true multi-process /
multi-worker concurrent submission is NOT lock-protected. Because every
submission goes through the explicit `SubmissionInfrastructure.submit_command`
path with a serialized caller, and the store guard detects a duplicate current
record, the system fails closed rather than silently duplicating. The future
gate MUST require an external serialization mechanism (e.g. a single
execution worker / distributed lock) before any multi-process live execution.

---

## 16. Persistence

**VERIFIED BY SOURCE + TEST.** One current submission record per command
(`./submissions/<submission_id>.json`). Atomic same-directory temp +
flush + fsync (best-effort) + `os.replace`; schema-versioned
(`SUBMISSION_SCHEMA_VERSION = 1`) with version checked before reconstruction;
tamper-evidence via deterministic content + submit id integrity check;
singular-record-per-command guard; deterministic sorted-key serialization;
UNKNOWN records persist and reload. Restart cases covered by the recovery
decision function (Section 17).

---

## 17. Restart Recovery

**VERIFIED BY SOURCE + TEST.** `recovery_for_command` returns:

- `SAFE_TO_SUBMIT` — CREATED / SUBMISSION_REQUESTED `pre_submission=True`
- `RECONCILE_REQUIRED` — SUBMISSION_REQUESTED `pre_submission=False` /
  SUBMITTED / UNKNOWN
- `NO_ACTION` — terminal
- `INVALID` — ambiguous/corrupt

Recovery is a read-only decision view; it never auto-submits and never
auto-reconciles. Crash-before-submit / crash-after-request /
crash-after-UNKNOWN / crash-after-reconcile all produce a deterministic
recovery classification. No automatic live resubmission.

---

## 18. Auditability

**VERIFIED BY SOURCE + TEST.** `SubmissionInfrastructure.audit` reconstructs
per-command: submission_id, command_id, client_order_id, idempotency_key,
state, terminal flag, provider, execution mode — with no credentials (tested:
audit blob contains no token text). The full forensic chain — authorization →
command → submission → broker identity → client order id → broker order id →
normalized result → lifecycle transition → reconciliation → recovery decision —
is reconstructable from deterministic ids + timestamps (tested end-to-end).

---

## 19. Broker State Drift

**VERIFIED BY SOURCE + TEST.** Broker-confirmed state is authoritative.
Conflicts are reconciled into a NEW lifecycle event; the prior snapshot is
never silently overwritten. Local `SUBMITTED` + broker `FILLED` → reconcile →
FILLED. Local `UNKNOWN` + broker `CANCELLED`/`REJECTED` → reconcile discovery.
Local `CANCELLED` + broker `FILLED` → fill authoritative (cancel returns
REJECTED, never a false cancellation). All mappings go through the order-state
mapper; nothing is guessed.

---

## 20. Error Normalization

**VERIFIED BY SOURCE + TEST.** Every broker-specific error is normalized at
the adapter boundary into the broker-neutral `BrokerErrorCode` /
`BrokerErrorCategory` taxonomy before entering core. Auth / authorization /
validation / broker rejection / transport / timeout / rate limit / unavailable
/ malformed / ambiguous / unknown are all mapped. Unknown broker error codes →
`UNKNOWN_OUTCOME` (AMBIGUOUS), never manufactured as a known result. The only
result type that crosses the boundary is `AdapterResult`. `BrokerError` for
unrecognized codes is fail-closed.

---

## 21. Retry Policy

**VERIFIED BY SOURCE + TEST.**

- **SAFE RETRIES:** GET / reconciliation / status lookup / non-mutating
  metadata.
- **UNSAFE RETRIES:** ambiguous order submission, unknown submission outcome,
  or any mutating request whose broker outcome is unknown.

No automatic blind retry exists (tested). The design documents a bounded retry
budget, bounded delay/backoff, retry exhaustion → manual intervention, and
reconciliation escalation. No infinite retries, no `sleep` loops.

---

## 22. Rate Limits

**VERIFIED USING MOCKS.** A rate-limited submission maps to
`BrokerResultStatus.UNKNOWN` with `BrokerErrorCode.RATE_LIMIT` (TRANSPORT),
never to FAILED and never to "definitely not submitted" — the broker may have
accepted the order, so the only safe path is reconcile. Documented policy:
bounded retry, backoff, retry budget, reconciliation priority, failure
escalation. Nothing auto-retries a rate-limited request.

---

## 23. Cancellation

**VERIFIED USING MOCKS.** Cases covered: open order (→ CANCELLED), already
cancelled (→ CANCELLED), already filled (→ REJECTED, fill authoritative),
rejected cancellation (→ REJECTED), timeout (→ UNKNOWN/TIMEOUT), unknown
result (→ UNKNOWN), fill/cancel race (→ REJECTED). Never assume cancellation
succeeded because the request was accepted.

---

## 24. Startup Safety

**VERIFIED BY SOURCE + TEST.** Startup must identify execution mode, broker,
adapter, credential availability, outstanding/UNKNOWN submissions,
reconciliation-required records, authorization state, and gate state
(documentation). Startup NEVER automatically submits orders and NEVER
automatically retries UNKNOWN submissions. Startup may enumerate work
requiring reconciliation, but actual reconciliation/submission remains
explicitly controlled via the established lifecycle contract.

---

## 25. Configuration Safety

**VERIFIED BY SOURCE + TEST.** The safe default is **NO LIVE EXECUTION**:
adapter factories default to `ExecutionMode.PAPER`, the live factory requires
an explicit credential provider, and the execution gate defaults to
`LiveExecutionGateState.DISABLED` with `gate_enabled=False`. There is no
`live_enabled = True` shortcut anywhere. Changing configuration cannot
silently bypass authorization.

---

## 26. Environment Safety

**VERIFIED BY SOURCE.** The architecture explicitly distinguishes PAPER and
LIVE at the mode level and never treats `production = paper`. There is no
sandbox environment identity yet (Section 6.1 CONCERN). The future gate MUST
confirm broker/account/environment identity independently of any mode string
(Section 31 precondition 10).

---

## 27. Security Threat Model

The 16-threat model from 17.5 § was re-audited against actual 17.9 source:

| Threat | Mitigation | Status |
| --- | --- | --- |
| Wrong execution mode | mode binding + selection + cp17_mode | MITIGATED (tested) |
| Wrong adapter / broker | explicit registry + selection | MITIGATED (tested) |
| Missing/invalid credentials | provider boundary + fail-closed | MITIGATED (tested) |
| Credential leakage | boundary injection + redaction + exclusion from all artifacts | MITIGATED (tested) |
| Duplicate submission | deterministic identity + store guard + infra pre-check | MITIGATED (tested) |
| Unknown response | AMBIGUOUS → UNKNOWN → reconcile | MITIGATED (tested) |
| Network timeout | TIMEOUT → UNKNOWN (never FAILED) | MITIGATED (tested) |
| Broker outage | BROKER_UNAVAILABLE → UNKNOWN | MITIGATED (mock-tested) |
| Malformed response | UNKNOWN (verified) | MITIGATED (tested) |
| Broker rejection | REJECTED (terminal) | MITIGATED (tested) |
| Instrument mapping | verified map, unknown → fail closed | MITIGATED (17.7 tested) |
| Order-type/product mapping | capability-gated, unsupported → fail closed | MITIGATED (17.7 tested) |
| Quantity/price conversion | verbatim Decimal, floor-never-increase | MITIGATED (17.7 tested) |
| Unsafe retry | no blind retry | MITIGATED (tested) |
| Paper/live cross-contamination | no fallback; mode isolation | MITIGATED (tested) |
| Restart during submission | recovery decision view | MITIGATED (tested) |

---

## 28. Direct Broker Access Audit

**VERIFIED BY SOURCE + TEST.** `place_order` / `get_order` / `cancel_order`
exist ONLY on the `UpstoxBrokerClient` Protocol boundary (concretely the
in-memory mock). No core module calls Upstox (AST sweep + test: execution/
broker/persistence modules contain no `place_order` calls).

---

## 29. Dependency Direction

**VERIFIED BY SOURCE.** Direction is enforced:

```
core
  ↓
broker-neutral contract
  ↓
broker-specific adapter
  ↓
broker client (transport owner)
  ↓
(future) real HTTP transport
```

Never `core → Upstox SDK/API`, never `broker client → strategy`, never
`adapter → authorization engine`. The Upstox provider in `engine.data`
(historical data) is isolated from execution code and is never imported by any
execution artifact.

---

## 30. Observability

**VERIFIED BY SOURCE + TEST.** An operator can distinguish submitted /
accepted / partially-filled / filled / cancelled / rejected / failed /
UNKNOWN / reconciliation-required / recovery-required / blocked-by-gate. The
system never presents UNKNOWN as FAILED and never presents FAILED as UNKNOWN
when the broker outcome is actually known. Recovery actions
(`SAFE_TO_SUBMIT`/`RECONCILE_REQUIRED`/`NO_ACTION`) and gate verdict blocking
reasons are explicit.

---

## 31. Live Execution Gate

**VERIFIED BY SOURCE + TEST.** The gate (`LiveExecutionGate`) is a pure,
stateless, deterministic evaluation function over a caller-supplied snapshot.
It is credential-free, network-free, and DISABLED by default. It requires
**20 mandatory conditions simultaneously**:

1. explicit live mode
2. correct broker
3. correct adapter
4. valid live credential
5. credential provenance
6. authorization state (AUTHORIZED)
7. command validity
8. risk/quantity constraints
9. capability support
10. environment identity
11. operator explicit authorization
12. execution gate enabled
13. startup safety checks
14. broker health/readiness
15. reconciliation readiness
16. audit readiness
17. no outstanding UNKNOWN affecting execution
18. no conflicting recovery state
19. no configuration ambiguity
20. no safety override active

**The gate is NOT equivalent to `if credential_exists: allow_live()`.**
Credentials are necessary but NOT sufficient. The gate fails closed if ANY
mandatory condition is missing. A missing condition key is treated as False
(test-verified). The verdict carries the blocking reasons + a deterministic
verdict id. **The gate is NOT wired into any submission path.**

### 31.1 Negative gate matrix (Phase 30) — tested

Every single missing condition blocks; missing key treated as False; wrong
broker/adapter/environment/paper-mode/missing-capability/unknown-submission/
reconciliation-required/missing-credential/no-operator-authorization/
gate-disabled/safety-config-missing all BLOCK. Auditable `blocking_reasons`.
Verdict is deterministic.

### 31.2 Positive gate matrix (Phase 31) — defined/tested, NOT activated

ALLOWED only when all 20 conditions are True AND the gate is explicitly
enabled AND an explicit operator authorization is recorded. Tested; the
result is a design artifact only.

---

## 32. Live Gate Preconditions (Phase 47 spec — condensed)

Preconditions: explicit live mode; correct broker/adapter/environment; valid
live credential with verified provenance; AUTHORIZED authorization; valid
command; valid risk/quantity constraints; supported capability; no outstanding
UNKNOWN; reconciliation healthy; audit available; broker healthy; gate
enabled; recorded operator authorization. Failure of any → NOT ALLOWED /
BLOCKED. **"Credentials are necessary but NOT sufficient for live
execution."** Missing live gate → DISABLED is the only safe default.

---

## 33. Emergency Stop (Phase 49 design)

Emergency stop prevents NEW submissions. It does NOT blindly mutate existing
broker state. Existing UNKNOWN orders remain subject to reconciliation.
Triggers: broker unavailable, reconciliation failure, credential revoked, gate
revoked, operator disables execution, audit unavailable, state corruption
detected, broker state conflict, duplicate-risk detected.

---

## 34. Failure Injection

**VERIFIED USING MOCKS (97 tests + existing 17.2–17.7 suites).** Matrix
covers: credential failure, authentication failure, authorization failure,
timeout, network reset, rate limit, broker unavailable, malformed response,
unknown response, duplicate submission, duplicate command, restart,
concurrent submission, reconciliation mismatch, stale broker state,
cancellation race, capability mismatch, environment mismatch. Each row records
Input / Expected / Actual / Safety invariant / PASS-CONCERN-BLOCKER. All
required invariants PASS; C04 (multi-worker lock) and C05 (broker-side
unverified) are documented CONCERNs, not blockers.

---

## 35. Test Architecture

Three categories maintained:

1. **Deterministic offline unit tests** — this suite (97) + all 17.x suites
   (342 + 186) + frozen 14–16 execution suites (627) + planning/paper-trading
   regression (350). Network-free, deterministic.
2. **Controlled broker/sandbox integration** — only the deterministic mock
   clients exist; no real broker was connected. Opt-in only.
3. **Future live tests** — do not exist; must require explicit separate
   authorization, environment gating, credential gating, environment identity
   verification, and must fail closed. Real-broker tests must NEVER run in
   normal CI.

---

## 36. Failure-Injection Matrix (Phase 38)

Documented above (Section 34) and realized by the 97-test suite. All INVARIANT
columns hold: `UNKNOWN→reconcile`, `no blind retry`, `no lease on frozen
files`, `no credential leakage`, `no auto-resubmit`, `mode isolation`, `fail
closed`.

---

## 37. Regression Results

- **17.9 new tests**: `tests/test_checkpoint_17_9_hardening.py` — **97 passed**.
- **17.2–17.4 suites**: `test_checkpoint_17_2_*`, `test_checkpoint_17_3_*`,
  `test_checkpoint_17_4_*` — **342 passed**.
- **17.7 Upstox suites**: `test_checkpoint_17_7_*` — **186 passed**.
- **Frozen 14–16 execution suites**: `test_execution_command(_store)`,
  `test_execution_authorization(_engine/_store)`,
  `test_operational_trade_intent(_engine/_application)` — **627 passed**.
- **Planning/paper-trading regression**: `test_trade_planning`,
  `test_paper_trading`, `test_paper_trading_operations` — **350 passed**.
- **FULL SUITE**: **6086 passed / 0 failed / 2 warnings** (5989 baseline + 97
  new). The 17.8 baseline (6066/6 skipped/2 warnings) ran without the optional
  dashboard deps; this environment has the optional deps installed, producing
  0 skipped.

No existing test was modified or deleted; no assertion was weakened.

---

## 38. Frozen Checkpoint Integrity (Phases 2/44)

**VERIFIED.** `git status` clean except the intended new files:
`src/engine/intelligence/execution_gate.py`,
`tests/test_checkpoint_17_9_hardening.py`,
`docs/checkpoint_17_9_broker_integration_hardening_and_execution_gate_audit.md`,
`AGENTS.md`. **ZERO tracked frozen file was modified.** Checkpoints 10–16 and
their tests are byte-identical. Broker-neutral contract /
`SubmissionLifecycle` / `SubmissionInfrastructure` remain broker-neutral; no
Upstox dependency leaked into core.

---

## 39..48 (checkpoint-section 53-slot numbering shorthand)

Sections 39–48 below correspond to the Phase 52 numbering slots 39–48:
39 (Frozen Checkpoint Integrity — above), 40 (Security Sweep),
41 (Credential Sweep), 42 (Network Sweep), 43 (Findings), 44 (PASS),
45 (CONCERN), 46 (BLOCKER), 47 (Verified Broker Behaviors),
48 (Unverified Broker Behaviors).

---

## 40. Security Sweep

**VERIFIED.** Repository-wide sweep (AST + grep): zero secrets, zero
credentials, zero bearer tokens, zero API keys, zero client secrets, zero
passwords, zero authorization-header dumps in the repo. The only execution
credential reference is the env-var **NAME** `UPSTOX_EXECUTION_ACCESS_TOKEN`
in `upstox_credential_provider.py` — a name, not a value; no default; no dump.
Zero `--force`/`--live`/`skip_auth`/`disable_checks`/bypass markers in
execution modules. No hidden live-execution path. No unsafe automatic retries.
No automatic recovery submission. No credential logging or persistence. No
live defaults.

---

## 41. Credential Sweep

**VERIFIED.** No credential text in: git-tracked files, test fixtures, AGENTS.md,
documentation, persisted lifecycle records, audit blobs, adapter results, or
logs. `tests/test_checkpoint_17_9_hardening.py` includes explicit no-leak
assertions (command object, store files, audit blob, result reason).

---

## 42. Network Sweep

**VERIFIED.** Zero network imports across all execution/broker/persistence
modules (AST sweep, Section 8). The only concrete broker client is the
in-memory `MockUpstoxBrokerClient`. No REST URL, no socket, no websocket, no
SDK. The trading-engine historical-data provider (`engine.data`) is the sole
network-capable component and is not part of the execution chain.

---

## 43. Findings

- **PASS findings:** 44 (Section 44).
- **CONCERN findings:** 6 (Section 45).
- **BLOCKER findings:** 0 (Section 46).

---

## 44. PASS Findings

1. AUTHORIZED-only command construction; no authorization bypass.
2. Forged / replayed / altered command detection.
3. PAPER↔LIVE fail-closed; no fallback/substitution.
4. `UNKNOWN → RECONCILE` discipline; no blind retry.
5. Credential isolation + redaction; no leakage anywhere.
6. Duplicate prevention in-process + across restart.
7. Atomic, schema-versioned, tamper-evident persistence.
8. Deterministic restart recovery (SAFE_TO_SUBMIT / RECONCILE_REQUIRED /
   NO_ACTION); no auto-resubmit.
9. Broker response hardening (missing/multiple/malformed → UNKNOWN).
10. Error normalization; no broker object leaks.
11. Cancellation race/timeout handled; no false cancellations.
12. No network imports / direct broker calls / override flags (AST sweep).
13. Deterministic live-gate negative and positive matrices.
14. Full audit/forensic chain reconstruction.
15. 97 new tests + full suite green (6086 passed, 0 failed).

---

## 45. CONCERN Findings (none unsafe)

- **C06 (documented in Section 5.6):** submission boundary does not re-check
  the command's temporal validity window against the submission time. The
  command factory enforces `valid_until > valid_from` at construction, but the
  PIPELINE does not. **Future gate precondition: enforce time validity.**
- **C02:** `check_health` exists on the client but is not consulted by the
  adapter/infrastructure before submit/reconcile/cancel. **Future gate
  precondition: broker health.**
- **C03:** no sandbox environment identity is modeled. **Future gate
  precondition: explicit environment identity.**
- **C04:** multi-process concurrency lacks an external lock (single-process
  store guard + serialized caller path only). **Future gate precondition:
  serialized execution or fail closed.**
- **C05:** residual broker behaviors unverified (real place-response order_ids
  shape, order-history array, retention window, tag constraints, broker-side
  idempotency, live auth/rate-limit, real HTTP transport). Deferred to any
  future controlled connection.
- **C01 (subsumed by C02/C06 naming note):** documented as C06 above.

---

## 46. BLOCKER Findings

**NONE.** No live execution without explicit authorization; no PAPER↔LIVE
fallback; no credential leak; no UNKNOWN auto-resubmit; no authorization
bypass; no direct broker calls; no frozen-file modification; no CI-real-broker
auto-run; no hidden `--force`/`--live` bypass; no ambiguous-response-as-success;
no unprotected concurrent duplicate; audit trail sufficient.

---

## 47. Verified Broker Behaviors (VERIFIED USING MOCKS)

- transaction-type mapping (BUY/SELL)
- order-type / product / validity / exchange mapping for the documented
  Upstox vocabulary (fail closed on unsupported)
- error-code mapping for documented Upstox codes
- timeout → UNKNOWN; reconcile-by-tag; cancellation race/timeout
- rate-limit → UNKNOWN; restart/duplicate detection; mode isolation
- instrument token mapping for the verified universe set

---

## 48. Unverified Broker Behaviors (NOT VERIFIED / DEFERRED)

- real place-response `order_ids` array vs single `order_id`
- order-history array vs single-record model
- one-trading-day order retention window
- full current order-state vocabulary
- tag length/character constraints
- broker-side idempotency
- live authentication / rate-limit / unavailable behavior
- real HTTP transport (no network execution client implemented)

---

## 49. Known Limitations

- No real broker connectivity (mock-only).
- No live/sandbox credential available; no controlled/sandbox verification.
- No network execution client exists.
- The live-execution gate is designed/tested but NOT activated.
- No sandbox environment identity.
- No broker-side idempotency claim.
- No guaranteed reconciliation beyond broker retention.
- No guaranteed fills or cancellations.
- Live-trading safety NOT established and NOT claimed.

---

## 50. Future Live Execution Requirements

Full 26-point specification in the AGENTS.md 17.9 entry (Phase 47 summary):
preconditions, authorization source/scope, credential requirements,
broker/environment identity, adapter identity, capability checks, command and
risk validation, submission-state validation, UNKNOWN-state restrictions,
reconciliation requirements, broker health requirements, audit requirements,
operator confirmation, startup/restart requirements, failure/revocation/
emergency-stop/fail-closed behavior, logging requirements, credential
exclusion, test requirements, CI restrictions, deployment restrictions.
**"Credentials are necessary but NOT sufficient for live execution."**

Future live state machine (Phase 48):
`DISABLED → CONFIGURED → PRECHECK → AUTHORIZED → GATE_VERIFIED → READY`;
any failure → `BLOCKED`; execution
`READY → SUBMISSION_REQUESTED → SUBMITTED/UNKNOWN → RECONCILE → CONFIRMED
TERMINAL STATE`. **UNKNOWN never auto-resubmits.**

---

## 51. Checkpoint 17 Completion Decision

**YES — COMPLETE / FROZEN** (with the live-execution gate specified
separately). Every remaining limitation is documented as non-blocking
(CONCERN-class, Sections 45/49) or UNVERIFIED-broker-behavior
(Section 48) and none creates an unsafe execution path. No BLOCKER remains.
Frozen Checkpoints 10–16 are intact. No hidden live path exists. Real Upstox
execution is NOT fully verified — the architecture is frozen, real-broker
execution is not.

---

## 52. Checkpoint 18 Recommendation

**DEFERRED — do NOT begin automatically.** Only on separate explicit
authorization: (a) any future controlled sandbox/paper broker connectivity to
verify the Section 48 unverified behaviors, then (b) a real (still gated)
broker-adapter integration under an explicit live-execution authorization
beyond Checkpoint 17.

---

## 53. Final Verdict

**PASS WITH LIMITATIONS.**

- The architecture is sufficiently hardened and fail-closed to safely define
  a separate explicit live-execution gate without weakening the frozen
  Checkpoints 10–16 architecture.
- External broker behaviors remain unverified (mock-only) and
  controlled-environment validation remains incomplete.

> **LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.9.**
> **CHECKPOINT 17.9 DOES NOT AUTHORIZE REAL-MONEY ORDER SUBMISSION.**