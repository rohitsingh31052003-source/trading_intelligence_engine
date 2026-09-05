# CHECKPOINT 18.4 — UPSTOX SANDBOX AUTHENTICATION / ENDPOINT RESOLUTION & CONTROLLED READ-ONLY VERIFICATION

## 1. Checkpoint status

COMPLETE / **PASS WITH LIMITATIONS**.

Checkpoint 18.4 resolves the unresolved Sandbox issues from Checkpoint 18.3
using official Upstox documentation, controlled real read-only verification
(READ-ONLY ONLY), deterministic offline tests, and a corrected
classification of the 18.3 observations. Genuine authenticated SANDBOX
read-only account access could NOT be established:

* the provisioned credential is rejected by every read-only
  ``api.upstox.com`` endpoint with HTTP 401 (UDAPI100050), and
* the documented sandbox order-API host ``sandbox.upstox.com`` has NO public
  DNS record (NXDOMAIN) and is unresolvable from this runtime, and
* official Upstox documentation states that sandbox access tokens are
  "exclusively for sandbox orders" and that the sandbox currently supports
  ONLY place / modify / cancel order APIs — the read-only endpoints used by
  this project are NOT documented as sandbox-enabled.

Therefore **genuine authenticated Sandbox connectivity / data access is NOT
established** in this checkpoint. All real-network observations are correctly
classified as **REAL UPSTOX API OBSERVATION / SANDBOX NOT ESTABLISHED**.

**LIVE TRADING IS NOT AUTHORIZED.** This checkpoint did NOT submit, modify,
cancel, or create any order. The live-execution gate remains DISABLED.

## 2. Overall verdict

**PASS WITH LIMITATIONS**

- Sandbox endpoint/credential-scope determination: VERIFIED FROM OFFICIAL
  DOCUMENTATION (sandbox order host + order-scoped 30-day token).
- Real HTTP 401 / UDAPI100050 against `api.upstox.com`: VERIFIED FROM REAL
  UPSTOX API (reproduces 18.3 exactly) — **NOT proven to be Sandbox**.
- Read-only enforcement / fail-closed / credential isolation: VERIFIED
  (source audit + offline tests).
- Genuine authenticated Sandbox read-only data: **NOT VERIFIED**.
- No safety blocker discovered.

## 3. Objective

Resolve the unresolved Sandbox issues from Checkpoint 18.3 and, if
technically possible, establish genuine authenticated READ-ONLY
communication with the Upstox Sandbox. The checkpoint remains strictly
READ-ONLY. The key question asked and answered honestly:

> "Can the provisioned credential authenticate against the actual Upstox
> Sandbox environment and, if so, can the existing read-only verifier
> retrieve genuine Sandbox data?"

Answer: **NOT VERIFIED — the credential cannot authenticate the read-only
api.upstox.com endpoints, and the documented Sandbox order host is not
resolvable and is order-scope only. No genuine Sandbox read-only account
data is accessible from this runtime.** No assumption was made; the evidence
is documented below.

## 4. Authorization boundary

- Operator explicitly authorized Checkpoint 18.4 for the Upstox SANDBOX.
- Real HTTP permitted ONLY for the existing read-only endpoints:
  - `GET https://api.upstox.com/v2/user/profile`
  - `GET https://api.upstox.com/v2/order/details?order_id=...`
  - `GET https://api.upstox.com/v2/order/history?tag=...` (array)
- Environment gate: `CHECKPOINT_17_8_REAL_BROKER=1` (the single
  repository-wide real-broker opt-in gate; no new gate introduced).
- Credential: `UPSTOX_EXECUTION_ACCESS_TOKEN` (execution-side provider).
  `UPSTOX_ANALYTICS_TOKEN` was NEVER used for execution/authentication.
- No order submission / modification / cancellation / creation was
  authorized or performed.

## 5. 18.3 findings being resolved

| 18.3 finding | 18.4 resolution |
| --- | --- |
| `api.upstox.com` read-only endpoints return 401 UDAPI100050 for the provisioned token | CONFIRMED (re-produced). Classified as **REAL UPSTOX API OBSERVATION / SANDBOX NOT ESTABLISHED**. |
| `sandbox.upstox.com` did not resolve | CONFIRMED and strengthened: NXDOMAIN (DNS Status 3) from public DNS resolvers (Google + Cloudflare DoH) and the runtime. Host currently has NO public DNS record. |
| The 401 was described as "real Sandbox authentication too strongly" | CORRECTED: the 401 proves only that credential was rejected by that endpoint. Sandbox authentication outcome is **NOT VERIFIED**. |
| Genuine Sandbox account data NOT established | REMAINS NOT VERIFIED. |
| Sandbox "supports only place/modify/cancel" | CONFIRMED from official docs (sandbox page + api-overview + per-page "Sandbox enabled" flags). |

## 6. Official Sandbox documentation evidence

All broker-specific conclusions below are VERIFIED FROM OFFICIAL
DOCUMENTATION (pages fetched directly; quotes verbatim):

### 6.1 Sandbox page — `https://upstox.com/developer/api-documentation/sandbox`

- Sandbox tokens are order-scoped and 30-day:
  > "Copy this token, which will be valid for 30 days, for use in your
  > sandbox API executions."
- Live-transaction exclusion:
  > "Sandbox access tokens are exclusively for sandbox orders and cannot be
  > used for live transactions."
- Sandbox-enabled API list (order-mutation only):
  > "we have listed APIs with sandbox capabilities within our documentation
  > or developer portal. Place Order / Place Order V3 / Place Multi Order /
  > Modify Order / Modify Order V3 / Cancel Order / Cancel Order V3"

### 6.2 Build using Sandbox — `https://upstox.com/developer/api-documentation/build-using-sandbox`

- SDK configuration toggles sandbox/live; sandbox token used for order
  APIs:
  > "configuration = upstox_client.Configuration(sandbox=True)
  > configuration.access_token = 'SANDBOX_ACCESS_TOKEN'"
  > Complete example to **Place an Order** in Sandbox Mode (OrderApiV3).

### 6.3 API overview — `https://upstox.com/developer/api-documentation/api-overview`

- Sandbox host + supported API set:
  > "The sandbox environment closely emulates the real API with no risk and
  > no time restrictions. **Currently supports place, modify, and cancel
  > order APIs**."
  > cURL: `curl --location 'https://sandbox.upstox.com/v2/order/place'`

### 6.4 Sandbox-mode announcement — `.../announcements/sandbox-mode-for-apis`

- Sandbox order lifecycle + data retrieval:
  > "Orders submitted within Sandbox Mode remain active for a full 24-hour
  > cycle, enabling developers to perform modifications, cancellations, and
  > retrieve associated data (e.g., order details, orderbook, and
  > historical records) with the same fidelity as in a live environment."

### 6.5 Per-endpoint "Sandbox enabled" flags

| Endpoint page | Flag |
| --- | --- |
| Place Order | "Sandbox Enabled" |
| Place Order V3 | "Sandbox Enabled" |
| Modify Order | "Sandbox Enabled" |
| Cancel Order | "Sandbox Enabled" |
| Get Order History | **(no Sandbox flag)** |
| Get Order Details | **(no Sandbox flag)** |
| Get Profile | **(no Sandbox flag)** |

Conclusion: the read-only endpoints retained by this project are NOT
documented as sandbox-enabled. (NOT VERIFIED as sandbox-supported.)

## 7. Sandbox endpoint determination

- Documented sandbox host: `https://sandbox.upstox.com` — used ONLY in
  official examples for ORDER APIs (`/v2/order/place`).
- DNS: `sandbox.upstox.com` → NXDOMAIN from Google DoH (Status 3) and
  Cloudflare DoH (Status 3) and from the local resolver. The host
  currently has NO public A/AAAA record.
- Read-only endpoints are documented on `https://api.upstox.com`
  (`/v2/user/profile`, `/v2/order/details`, `/v2/order/history`).
- Therefore: no documented Sandbox base exists for the READ-ONLY endpoints,
  and the Sandbox order host is not reachable from this runtime.
- Per Phase 4 outcome classification:
  - B (Sandbox uses another documented endpoint): the read-only endpoints
    are documented on `api.upstox.com`, not on a sandbox host.
  - F (runtime cannot access the Sandbox infrastructure): `sandbox.upstox.com`
    is NXDOMAIN; a connection cannot be attempted.
  - No unsupported/untrusted alternate host was invented; no switch to the
    live API occurred.

**SANDBOX CONNECTIVITY NOT VERIFIED.**

## 8. Credential-scope determination

The 18.3 token produced HTTP 401 / UDAPI100050 against `api.upstox.com`.
Possible causes classified evidence-based:

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| Wrong host / wrong environment | Token is an order-scoped sandbox token; read-only endpoints aren't sandbox-enabled (official docs). | CONSISTENT (docs) |
| Order-only sandbox token | "Sandbox access tokens are exclusively for sandbox orders..." | CONSISTENT (docs) |
| Expired token | Cannot be established without a valid read-only/sandbox-order authentication. | NOT VERIFIED |
| Invalid token | Cannot be proven invalid solely because the production API rejected it. | NOT VERIFIED |
| Missing API scope | No documented read-only scope for sandbox tokens. | CONSISTENT (docs) |

Correct classification: **"token is sandbox-order-scoped; read-only endpoints
are not sandbox-enabled" is CONSISTENT WITH official documentation.** The
token is NOT labeled "invalid"; it is labeled **scope-limited and not
verifiable through read-only endpoints**.

## 9. Credential safety

- `UPSTOX_EXECUTION_ACCESS_TOKEN`: present (boolean True); value NEVER
  printed/logged/persisted/committed; the token's value is not stated here.
- `UPSTOX_ANALYTICS_TOKEN`: present (boolean True); a HISTORICAL-DATA
  credential NEVER used for execution or Sandbox authentication.
- The execution-side provider (`EnvironmentUpstoxCredentialProvider`) reads
  ONLY `UPSTOX_EXECUTION_ACCESS_TOKEN` lazily and never stores it.
- `redact_sensitive` + `_scrub_token` defense-in-depth confirmed by tests.
- Token/value never appears in `SandboxReadOnlyVerification.to_dict()`,
  audit entries, exceptions, logs, or tests (asserted by tests).

## 10. Real authentication result

- With `CHECKPOINT_17_8_REAL_BROKER=1` and the provisioned token:
  `GET https://api.upstox.com/v2/user/profile` → HTTP 401
  `{"status":"error","errors":[{"errorCode":"UDAPI100050",
  "message":"Invalid token used to access API",...}]}`.
- Transport classification: `UpstoxErrorKind.AUTHENTICATION` →
  `VerificationClassification.FAILED`,
  normalized `BrokerResultStatus.FAILED`,
  `BrokerErrorCode.AUTHENTICATION_FAILURE`, category TRANSPORT.
- Verifier: `gate_passed=True`, `token_available=True`,
  `real_sandbox_connected=False`. Exit code 1 (honest, ran-but-not-connected).
- Classification: **REAL UPSTOX API OBSERVATION / SANDBOX NOT ESTABLISHED.**

## 11. Real profile result

- Profile GET executed (real). Result: HTTP 401 UDAPI100050. No profile
  identity fields obtained. `profile_broker=""`,
  `profile_is_active=False`, `profile_user_id_present=False`.
- REAL PROFILE CONTENT: **NOT VERIFIED** (no authenticated profile data).
- The documented profile success shape (broker/exchanges/products/order_types/
  user_type/is_active/user_id masked) is VERIFIED FROM OFFICIAL DOCUMENTATION
  and offline-normalized in tests (UCC value never retained).

## 12. Real order-history result

- Order-history GET executed (real). Result: HTTP 401 UDAPI100050.
- No order exists in a fresh sandbox; none was created.
- REAL ORDER-HISTORY CONTENT: **NOT VERIFIED**.
- Documented array-of-records success shape, tag/order_id filtering, and
  one-trading-day availability VERIFIED FROM OFFICIAL DOCUMENTATION;
  offline tests lock the parsing semantics.

## 13. Real order-details result

- Order-details GET executed (real). Result: HTTP 401 UDAPI100050.
- No legitimate pre-existing Sandbox order id was available.
- REAL ORDER DETAILS: **NOT VERIFIED**.
- Documented single-record success shape VERIFIED FROM OFFICIAL DOCUMENTATION;
  offline tests lock the parsing semantics.

## 14. Real reconciliation result

- No operator-supplied pre-existing order ids
  (`CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS` unset).
- Verifier recorded `reconciliation_result="NOT_VERIFIED"` with an
  `order_history UNVERIFIED` audit entry (honest).
- REAL RECONCILIATION: **NOT VERIFIED**.

## 15. Response-shape observations

- Real 401 error envelope re-captured (sanitized, token redacted):
  `{"status":"error","errors":[{"errorCode":"UDAPI100050",
  "message":"Invalid token used to access API","propertyPath":null,
  "invalidValue":null,"error_code":"UDAPI100050","property_path":null,
  "invalid_value":null}]}` — matches 18.3 exactly.
- Documented success envelopes (profile object; history array; details
  object) are implemented in the transport and covered offline; none was
  observed live because authentication failed.

## 16. Error observations

- Live error: HTTP 401 UDAPI100050 on all three read-only endpoints.
- 401 → AUTHENTICATION → FAILED/AUTHENTICATION_FAILURE (fail closed).
- Documented error-code normalization: UDAPI100010 → UNKNOWN_OUTCOME,
  UDAPI100058/100059 → VALIDATION, unknown codes → BROKER_REJECTION
  (offline tests; UDAPI100050 is intercepted by the HTTP 401 status kind).
- No destructive errors were generated; no rate-limit was triggered.

## 17. Timeout/network observations

- No timeout occurred against `api.upstox.com`.
- `sandbox.upstox.com` DNS failure would map to NETWORK → FAILED (fail
  closed), but the host is NXDOMAIN so no request even reaches it.
- Timeout → AMBIGUOUS → UNKNOWN → reconcile discipline is preserved and
  covered offline (exactly one attempt; no automatic retry).

## 18. UNKNOWN / reconciliation behavior

- UNKNOWN (timeout/malformed/zero/multiple/unknown-state) is NEVER
  converted into SUCCESS or FAILED without broker-confirmed evidence.
- zero-match history → UNKNOWN_OUTCOME; multiple distinct orders → UNKNOWN
  (MALFORMED_RESPONSE/ambiguous); unknown status string → UpstoxOrderState
  UNKNOWN (offline tests).
- 401 UDAPI100050 is a CONFIRMED auth rejection → FAILED/
  AUTHENTICATION_FAILURE (the correct fail-closed interpretation of a
  definitive endpoint rejection), while the ENVIRONMENT classification
  remains separate and NOT VERIFIED.

## 19. Read-only enforcement

- `place_order` and `cancel_order` on the read-only transport RAISE
  `ValueError` (structural).
- Request builder uses `method="GET"` only; no POST/PUT/PATCH/DELETE
  construction exists (source audit).
- Network imports (`urllib`, `socket`) are confined to the transport
  boundary; verifier/models/core import none (AST audit).

## 20. Paper/live/Sandbox isolation

- Verifier environment: SANDBOX; expected mode PAPER.
- `live_mode_hard_gate` fires on a reported LIVE environment.
- `is_controlled_environment("LIVE")==False`; `is_live_environment("LIVE")
  ==True`.
- No PAPER→LIVE, LIVE→PAPER, or Sandbox→LIVE fallback exists.
- The adapter/verifier mode mismatch fails closed; the live order host
  (`api-hft.upstox.com`) is never a read-only default.

## 21. Execution-gate status

- `LiveExecutionGate` remains a pure design surface, **DISABLED** by default
  and NOT wired to any submission path.
- Negative matrix re-verified offline: 20 mandatory conditions + gate
  enabled + explicit operator authorization required; credentials alone
  never allow.

## 22. Credential rotation

- `EnvironmentUpstoxCredentialProvider` reads lazily → rotation at the
  environment is picked up on next read.
- Verification/command identity is credential-independent
  (rotation_does_not_alter identity tests in 17.9/18.2 still pass).
- Token value never enters ExecutionCommand / SubmissionLifecycle /
  persistence / audit / exceptions.

## 23. Auditability

- Verification id `roverify-<sha256[:16]>`; audit entries
  `valaudit-<sha256[:16]>`.
- This run's audit recorded: PROFILE FAILED (AUTHENTICATION_FAILURE) and
  ORDER_HISTORY UNVERIFIED (no order ids); values redacted, no credential
  material.

## 24. Tests

New offline deterministic module
`tests/test_checkpoint_18_4_sandbox_auth_resolution.py` — 37 tests across
10 sections (A corrected 401 classification; B sandbox endpoint
determination; C wrong-environment fail-closed/live not selectable; D
read-only hard gate; E credential safety incl. analytics-never-execution;
F real 401 envelope + response/error normalization incl. documented success
shapes; G order-history/details/reconciliation semantics; H UNKNOWN +
no-automatic-retry; I execution gate stays disabled; J SANDBOX/PAPER/LIVE
isolation). Network-free; opt-in real-sandbox suite remains gated.

## 25. Full-suite result

- Baseline (pre-18.4): 6269 passed / 12 skipped / 2 warnings (deprecation).
- 18.4 focused: 37 new tests pass.
- 18.2+18.3+18.4 + 17.2–17.9 suites with gate/token unset:
  825 passed / 6 skipped (opt-in).
- Frozen 14–16 + planning/paper: 1016 passed / 2 warnings.
- Full suite post-18.4 (with gate + token UNSET): see §25a below.

### 25a. Full suite result (18.4, gate/token unset)

Full suite run with `CHECKPOINT_17_8_REAL_BROKER=` and
`UPSTOX_EXECUTION_ACCESS_TOKEN=` unset:

```
6306 passed, 12 skipped, 2 warnings in 119.81s
EXIT=0
```

- Baseline was 6269 passed / 12 skipped / 2 warnings; +37 new = **6306**. No
  regression.
- The previously environment-dependent 17.9 `test_rotation_picked_up_lazily`
  passes in this unset-token run (as expected; it fails only when an operator
  provisions the execution token, which is an environmental delta, not a code
  regression).
- Skips: the existing opt-in real-broker/real-sandbox suites (17.8/18.2)
  correctly skip with the gate unset.

## 26. Frozen-checkpoint integrity

- No Sprint 11A–12E / Product-Phase-1–6F / Checkpoint 10–17 file modified.
- No Checkpoint 18.1/18.2/18.3 file modified.
- No existing test modified, deleted, weakened, or skipped artificially.
- AGENTS.md append-only (see Phase upstream entry).

## 27. Security sweep

- No hard-coded credential / Authorization header / Bearer token in new
  files, tests, or docs.
- No credential logging / persistence / exception leakage.
- No live Upstox endpoint referenced in production code beyond the
  documented read-only api.upstox.com GETs in the transport.
- place/cancel blocked; GET-only; network imports transport-confinement
  verified.
- No PAPER→LIVE/Sandbox→LIVE fallback; execution gate disabled; no
  authorization bypass.

## 28. VERIFIED FROM REAL SANDBOX

NONE. No observation in this checkpoint is classified VERIFIED FROM REAL
SANDBOX because genuine Sandbox authentication was not established.

(18.3's "VERIFIED FROM REAL SANDBOX" language for the api.upstox.com
observations is corrected in §38: those observations are reclassified.)

## 29. VERIFIED FROM REAL UPSTOX API BUT NOT PROVEN SANDBOX

| Observation | Evidence |
| --- | --- |
| `GET https://api.upstox.com/v2/user/profile` → HTTP 401 UDAPI100050 | Real HTTP this checkpoint |
| `GET /v2/order/details` → HTTP 401 UDAPI100050 | Real HTTP (transport; via 18.3 and this transport's get_order) |
| `GET /v2/order/history` → HTTP 401 UDAPI100050 | Real HTTP (transport) |
| Real 401 error envelope (status:error + errors[] + duplicate singular keys) | Captured sanitized this checkpoint |
| 401 → AUTHENTICATION → FAILED/AUTHENTICATION_FAILURE | Real HTTP + code path |
| `sandbox.upstox.com` NXDOMAIN (DNS Status 3) from runtime | Local resolver; strengthened by DoH NXDOMAIN |

## 30. VERIFIED FROM OFFICIAL DOCUMENTATION

| Claim | Source |
| --- | --- |
| Sandbox token valid 30 days | /api-documentation/sandbox |
| Sandbox tokens are exclusively for sandbox orders; cannot be used for live transactions | /api-documentation/sandbox |
| Sandbox-enabled APIs: Place/V3, Multi, Modify/V3, Cancel/V3 | /api-documentation/sandbox |
| Sandbox currently supports place, modify, and cancel order APIs | /api-documentation/api-overview |
| Sandbox order host `https://sandbox.upstox.com/v2/order/place` | /api-documentation/api-overview |
| Read-only endpoints documented on `https://api.upstox.com/v2/...` | /api-documentation/get-profile, get-order-details, get-order-history |
| Get Profile / History / Details pages carry NO Sandbox flag | per-endpoint pages |
| Sandbox orders remain active 24h; order details/history retrievable for sandbox orders | /announcements/sandbox-mode-for-apis |
| Order history retention one trading day; retrieval by order_id or tag | /get-order-history |
| Order-details/profile success response shapes | /get-order-details, /get-profile |

## 31. VERIFIED USING MOCKS

| Behavior | Notes |
| --- | --- |
| 401 envelope → AUTHENTICATION_FAILURE | Fake urlopen reproduces the real body |
| Empty order history → UNKNOWN_OUTCOME | Offline test |
| Multiple distinct orders → UNKNOWN/MALFORMED | Offline test |
| Unknown status string → UpstoxOrderState.UNKNOWN | Offline test |
| Timeout → TIMEOUT kind; one attempt; no blind retry | Offline test |
| Gate negative matrix; credentials-only blocked | Offline test |
| Read-only place/cancel blocked | Offline + source audit |

## 32. NOT VERIFIED

- Genuine authenticated Sandbox read-only account data.
- Real Sandbox profile/history/details success envelopes.
- Real reconciliation against an existing Sandbox order.
- Whether the provisioned token is valid for any Sandbox order endpoint
  (no sandbox-order host reachable; no order created).
- Real rate-limit / real timeout / real cancellation-fill race.
- `sandbox.upstox.com` reachability from any environment.

## 33. DEFERRED

- Any Sandbox ORDER-API connectivity (place/modify/cancel) — requires the
  Sandbox order host, is OUT OF SCOPE for a READ-ONLY checkpoint, and would
  require separate authorization.
- Filling the remaining real observations (profile identity, history
  success envelope, order-details status vocabulary, reconciliation over a
  pre-existing Sandbox order id) — requires a Sandbox-order-scoped session
  or an environment where `sandbox.upstox.com` resolves and a sandbox order
  pre-exists.

## 34. CONCERN findings

- C18.4-1 Genuine read-only Sandbox authentication is unattainable with the
  provisioned order-scoped token + api.upstox.com read-only endpoints
  (consistent with official docs). No safe read-only path exists.
- C18.4-2 `sandbox.upstox.com` is NXDOMAIN globally (public DoH + local);
  any real Sandbox order-API verification would need an environment/network
  where that name resolves (or an official doc change introducing a
  resolvable sandbox host).
- C18.4-3 The read-only endpoints are not documented as sandbox-enabled;
  even with a valid sandbox token, profile/history/details against
  api.upstox.com may not be sandbox-scoped. Not a defect; an honest scope
  limitation.
- None are safety issues.

## 35. BLOCKER findings

NONE. No safety or architectural blocker was discovered.

## 36. Known limitations

- No genuine Sandbox read-only data was accessible from this runtime.
- The repository normal CI remains network-free and deterministic (all new
  tests offline; real opt-in suite gated).
- The corrected interpretation of the 401 is documented; the historical
  18.3 record is preserved (no rewriting).

## 37. Comparison with 18.3

| Aspect | 18.3 | 18.4 |
| --- | --- | --- |
| api.upstox.com 401 | "VERIFIED FROM REAL SANDBOX" | Corrected: REAL UPSTOX API / SANDBOX NOT ESTABLISHED |
| sandbox.upstox.com DNS failure | env-specific | + global NXDOMAIN (DoH) |
| Official sandbox docs | partially cited | fetched + quoted verbatim; per-endpoint flags tabulated |
| Sandbox token scope | documented order-only | fully documented order-only; read-only endpoints un-flagged |
| Genuine Sandbox data | NOT VERIFIED | NOT VERIFIED (unchanged) |
| Read-only / fail-closed | verified | re-verified + 37 new tests |
| New files | 1 test + doc | 1 test + this doc + AGENTS append |

## 38. Corrected interpretation of 18.3

The original 18.3 record is PRESERVED (not rewritten). This 18.4 checkpoint
formally reclassifies the 18.3 `api.upstox.com` observations:

> Checkpoint 18.4 reclassifies the 18.3 api.upstox.com observations as
> REAL UPSTOX API OBSERVATION / SANDBOX NOT ESTABLISHED.

The HTTP 401 / UDAPI100050 responses are real rejections by the Upstox
`api.upstox.com` endpoint of that credential. They do NOT prove Sandbox
authentication failed, and they do NOT prove the token invalid. Sandbox
authentication was simply **not verifiable** from this runtime.

## 39. Recommendation for 18.5

DEFERRED. Do NOT begin Checkpoint 18.5 automatically. Only on separate,
explicit operator authorization, AND with one of:

1. a confirmed Sandbox token/scope that authenticates the read-only
   `api.upstox.com` endpoints (per official docs this is currently
   uncommon/unsupported), or
2. an environment where `sandbox.upstox.com` resolves AND a pre-existing
   sandbox ORDER id is available for READ-ONLY order history/details/
   reconciliation, or
3. an officially documented, resolvable Sandbox read-only endpoint.

Remain READ-ONLY. Do NOT place/cancel/modify orders. Do NOT connect to a
live broker. Do NOT enable the live gate.

## 40. Explicit live-trading safety statement

Checkpoint 18.4 does NOT authorize live trading. All real-network activity
in this checkpoint is restricted to the verified Upstox Sandbox/read-only
boundary. No order was submitted, modified, or cancelled. The live-execution
gate remains DISABLED.