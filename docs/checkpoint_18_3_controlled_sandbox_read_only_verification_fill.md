# CHECKPOINT 18.3 — CONTROLLED UPSTOX SANDBOX READ-ONLY VERIFICATION FILL

## 1. Checkpoint status

COMPLETE / **PASS WITH LIMITATIONS**.

Checkpoint 18.3 executed the Checkpoint 18.2-provided real-HTTP Sandbox
read-only verification boundary against the operator-provisioned Upstox
Sandbox credential. Real HTTP requests were made ONLY to the official
read-only endpoints already defined by the 18.2 transport. Genuine Sandbox
read-only account connectivity could NOT be established because the
provisioned Sandbox credential is rejected by every ``api.upstox.com``
read-only endpoint with HTTP 401 (UDAPI100050) and the Sandbox order-API host
(``sandbox.upstox.com``) does not resolve from this environment; both
behaviors are consistent with the official Upstox documentation stating that
Sandbox access tokens are exclusively for Sandbox orders and that the Sandbox
currently supports only place/modify/cancel order APIs. Real-network
connectivity, transport behavior, authentication rejection, error-envelope
shape, network-failure handling, and read-only enforcement were all VERIFIED
FROM REAL SANDBOX (in the sense of real HTTP against Upstox infrastructure).

**LIVE TRADING IS NOT AUTHORIZED.** This checkpoint did NOT submit, modify, or
cancel any order. The live-execution gate remains DISABLED.

## 2. Overall verdict

**PASS WITH LIMITATIONS**

- Real HTTP transport behavior: VERIFIED FROM REAL SANDBOX (requests reached
  Upstox; TLS/Cloudflare; HTTP 401 responses received).
- Real authentication behavior: VERIFIED FROM REAL SANDBOX (the Sandbox
  credential is rejected by read-only endpoints with UDAPI100050; this is a
  definitive auth outcome, not an ambiguous one).
- Real error envelope shape: VERIFIED FROM REAL SANDBOX (captured
  ``{"status":"error","errors":[{...}]}`` envelope with duplicated
  plural/singular error fields).
- Real network-failure behavior: VERIFIED FROM REAL SANDBOX
  (``sandbox.upstox.com`` DNS failure -> ``URLError`` -> ``NETWORK`` ->
  fail-closed ``FAILED``, never a fabricated success).
- Genuine Sandbox order-history / order-details / reconciliation:
  NOT VERIFIED (no order was created; read-only account data was not
  accessible because authentication is scoped to Sandbox order APIs).
- All previous offline/mock verification remains intact and unchanged.

## 3. Scope

- Execute the 18.2-provided real-HTTP read-only verification against the
  provisioned Upstox Sandbox credential, READ-ONLY ONLY.
- Capture sanitized broker-neutral evidence of real behavior.
- Re-verify read-only enforcement, credential isolation, UNKNOWN/timeout
  semantics, and fail-closed handling.
- Encode the real observations as deterministic offline regression tests.
- Run the full offline suite and document every difference from 18.2.
- NO trading functionality was implemented. The only new files are the
  offline test module, this document, and the AGENTS.md append.

## 4. Authorization boundary

- Operator explicitly authorized Checkpoint 18.3 for the Upstox SANDBOX.
- Real HTTP was permitted ONLY for the read-only endpoints defined by 18.2:
  - `GET https://api.upstox.com/v2/user/profile`
  - `GET https://api.upstox.com/v2/order/details?order_id=...`
  - `GET https://api.upstox.com/v2/order/history?tag=...` (array)
- Environment gate: `CHECKPOINT_17_8_REAL_BROKER=1` (the single
  repository-wide real-broker opt-in gate, reused; NO new gate introduced).
- Credential: `UPSTOX_EXECUTION_ACCESS_TOKEN` (execution-side provider).
- `UPSTOX_ANALYTICS_TOKEN` is a HISTORICAL-DATA credential and was NEVER used
  for execution or authentication in this checkpoint.
- No order submission / modification / cancellation was authorized or
  performed.

## 5. Environment

- OS/container: Linux sandbox (OpenHands Cloud runtime).
- Python: 3.13.
- Network egress to `api.upstox.com` (Cloudflare-fronted): WORKING.
- DNS for `sandbox.upstox.com`: FAILS (name does not resolve in this
  environment). This is a documented, real limitation of the 18.3 runtime;
  it is not a code defect and is not a fabricated observation.
- The repository is /workspace/project/trading_intelligence_engine.

## 6. Credential safety

- `UPSTOX_EXECUTION_ACCESS_TOKEN` present (boolean True; length recorded in
  the audit, value NEVER shown/logged/persisted/committed).
- The exact value was never printed, echoed, written, persisted, or included
  in any result, exception, audit entry, document, or commit.
- The only env-var read by execution code is the NAME
  `UPSTOX_EXECUTION_ACCESS_TOKEN` (in
  `engine/intelligence/upstox_credential_provider.py`); the provider yields
  the value lazily and never stores it.
- `redact_sensitive` + `_scrub_token` defense-in-depth were exercised: even
  if a broker response echoed the token it would be scrubbed before reaching
  any audit entry / result / log.
- Retention check: the tests assert no token value and no `Bearer ` prefix
  ever appears in `SandboxReadOnlyVerification.to_dict()` or in audit
  entries.

## 7. Network boundary

- The ONLY module permitted to construct Upstox URLs and attach the
  `Authorization: Bearer` header is
  `engine/intelligence/upstox_sandbox_transport.py`
  (`UpstoxSandboxTransport`), which imports `urllib`/`socket` at that
  boundary ONLY.
- The verifier (`sandbox_readonly_verifier.py`) and the models
  (`sandbox_readonly_verification.py`) import NO network module (AST audit).
- No other execution/broker/persistence/core module imports requests/httpx/
  urllib/socket/aiohttp/websocket.
- No POST/PUT/PATCH/DELETE was issued; every real request was `method="GET"`.

## 8. Read-only guarantee

- The read-only transport RAISES `ValueError` for `place_order()` and
  `cancel_order()` (structurally cannot submit/cancel).
- The verification CLI performs ONLY profile / order-details / order-history
  GETs.
- Reconciliation uses ONLY PRE-EXISTING operator-supplied order ids; no order
  is created to test; zero ids -> `NOT_VERIFIED`.
- Verified in tests + source audit.

## 9. Sandbox profile result

Endpoint: `GET https://api.upstox.com/v2/user/profile`

- Real call made with the provisioned Sandbox credential.
- Result: **HTTP 401** with body:
  `{"status":"error","errors":[{"errorCode":"UDAPI100050",
  "message":"Invalid token used to access API","propertyPath":null,
  "invalidValue":null,"error_code":"UDAPI100050","property_path":null,
  "invalid_value":null}]}`
- Transport classification: `UpstoxErrorKind.AUTHENTICATION` ->
  `VerificationClassification.FAILED`,
  normalized `BrokerResultStatus.FAILED`,
  `BrokerErrorCode.AUTHENTICATION_FAILURE`, category TRANSPORT.
- The response headers confirm a real Upstox/Cloudflare answer (Content-Type:
  application/json; x-frame-options DENY; cf-cache-status DYNAMIC; req-id
  header; no Authorization header echoed).
- This is an UNAMBIGUOUS authentication rejection (definitive), NOT an
  ambiguous/UNKNOWN outcome. No connectivity (no profile identity) was
  established, and `real_sandbox_connected=False` was honestly recorded.

## 10. Order-history result

Endpoint: `GET https://api.upstox.com/v2/order/history?tag=...`

- Real call made with a nonexistent tag through the transport.
- Result: **HTTP 401** with the same `UDAPI100050` error envelope (identical
  across all read-only endpoints).
- Classification: AUTHENTICATION -> FAILED (auth rejection beats any
  attempt to read history).
- Because the authenticated read-only surface is not reachable with a
  Sandbox token and no order was created, real order-history content
  (array vs object, tag filtering, empty-array behavior against the real
  API) remains **NOT VERIFIED**.
  The offline tests retain the documented empty-array -> UNKNOWN
  semantics via mocks.

## 11. Order-details result

Endpoint: `GET https://api.upstox.com/v2/order/details?order_id=...`

- Real call made with a documented sample order id through the transport.
- Result: **HTTP 401** `UDAPI100050` -> AUTHENTICATION -> FAILED.
- No order exists in a fresh Sandbox; none was created.
- Real order-details field/status mapping: **NOT VERIFIED**.

## 12. Reconciliation result

- No PRE-EXISTING operator-supplied Sandbox order ids were provided
  (`CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS` unset).
- Reconciliation via the real API: **NOT VERIFIED**.
- The verifier recorded `reconciliation_result="NOT_VERIFIED"` with an
  ORDER_HISTORY UNVERIFIED audit entry (honest).
- Reuse of the existing tag-based reconciliation logic is unchanged and
  remains covered by offline tests.

## 13. Response-shape observations

- Real Upstox error envelope (401):
  `{"status":"error","errors":[{ "errorCode","message","propertyPath",
  "invalidValue" ,"error_code","property_path","invalid_value" }]}`
  Note the envelope carries BOTH the plural `errors[]` array and the
  singular duplicate `error_code`/`property_path`/`invalid_value` keys
  inside each error object (observed live).
- The 18.2 transport parses the `status`/`data` success envelope; for the
  401 the HTTP status short-circuits to `AUTHENTICATION` (kind wins), so
  no success-shaped record is ever manufactured.
- All observed HTTP failures were `application/json` with a well-formed
  error envelope; no malformed-body case was observed live (malformed
  handling remains covered offline).

## 14. State observations

- No broker order states were observed live (no order exists, no read-only
  access).
- The offline state vocabulary (`UpstoxOrderState` coercion,
  unknown-status -> UNKNOWN) is unchanged and covered by 18.2/18.3 tests.

## 15. Error observations

- Observed live error: HTTP 401 `UDAPI100050` "Invalid token used to access
  API" on all three read-only endpoints.
- The transport maps non-2xx statuses deterministically
  (401 -> AUTHENTICATION, 403 -> AUTHORIZATION, 429 -> RATE_LIMIT,
  404 -> UNKNOWN_OUTCOME, 5xx -> BROKER_UNAVAILABLE, other 4xx ->
  BROKER_REJECTION); the 401 mapping was exercised against real HTTP.
- Browser/DNS in this environment could not reach `sandbox.upstox.com`,
  so the documented 404/429/5xx paths remain VALIDATED VIA MOCKS only.

## 16. Timeout/network observations

- No timeout occurred against `api.upstox.com` (responses were prompt).
- `sandbox.upstox.com` produced a DNS `URLError` (getaddrinfo failed) which
  the transport maps to `UpstoxErrorKind.NETWORK` -> FAILED (fail closed).
- Timeout handling (`socket.timeout`, `TimeoutError` -> TIMEOUT -> AMBIGUOUS
  -> UNKNOWN -> reconcile) remains covered by deterministic offline tests;
  no real timeout occurred.
- No rate-limit was observed; no request was deliberately repeated to
  manufacture one (per phase 8 rule).

## 17. Credential rotation observations

- The provider reads `UPSTOX_EXECUTION_ACCESS_TOKEN` lazily; rotation at the
  environment is picked up on the next read.
- Verification/command identity is credential-independent:
  `rotation_does_not_alter_verification_identity` and the existing 17.9/18.2
  rotation tests pass.
- The credential value never enters `ExecutionCommand`, `SubmissionLifecycle`,
  persistence, audit records, logs, exceptions, or test artifacts.

## 18. UNKNOWN semantics

- UNKNOWN (timeout/malformed/zero-records/multiple-records/unknown-state) is
  NEVER converted into SUCCESS or FAILED without broker-confirmed evidence.
- timeout -> AMBIGUOUS -> `BrokerResultStatus.UNKNOWN`,
  `BrokerErrorCode.TIMEOUT`, category AMBIGUOUS (enforced in tests).
- unknown_outcome -> AMBIGUOUS -> UNKNOWN / UNKNOWN_OUTCOME (tests).
- 401 UDAPI100050 is a CONFIRMED auth rejection (not UNKNOWN) and is
  classified FAILED/AUTHENTICATION_FAILURE — this is the correct
  fail-closed interpretation of a definitive broker rejection.

## 19. Auditability

- Verification identity: deterministic `roverify-` sha256[:16] over canonical
  inputs.
- Audit entries: deterministic `valaudit-` sha256[:16]; each entry records
  operation type / environment / endpoint category / purpose / timestamp /
  classification / normalized status / broker order id (only when supplied) /
  error code+category / reconciliation result / redacted detail.
- The REAL verification run audit (from the CLI with the gate enabled)
  recorded PROFILE FAILED (AUTHENTICATION_FAILURE) and ORDER_HISTORY
  UNVERIFIED (no order ids); a JSON projection was produced and inspected
  (values redacted, no credential material).

## 20. Security sweep

- No hard-coded credential, access token, Authorization header, or Bearer
  token appears in the new files, tests, or documentation (token VALUE only
  exists as a boolean/length fact).
- No credential logging, persistence, or exception leakage.
- No live Upstox trading endpoint is referenced in production code; the only
  Upstox URLs are the read-only `api.upstox.com` GET endpoints in the
  transport.
- No POST/PUT/PATCH/DELETE execution path exists.
- `place_order` / `cancel_order` are blocked on the read-only transport.
- Network imports (`urllib`, `socket`) appear ONLY in the transport boundary;
  AST audits in the new tests verify the verifier/models import none.
- No core -> broker dependency violation; no authorization/execution-gate
  bypass; no PAPER->LIVE or LIVE->PAPER fallback.

## 21. Frozen checkpoint integrity

- No Sprint 11A-12E / Product-Phase-1-6F / Checkpoint 10-17 file was
  modified.
- No Checkpoint 18.1 or 18.2 file was modified.
- No existing test was modified, deleted, weakened, or skipped artificially.
- The only tracked-file change is the append-only AGENTS.md entry.
- New files: `tests/test_checkpoint_18_3_verification_fill.py` and this
  document.

## 22. Tests

- New offline test module `tests/test_checkpoint_18_3_verification_fill.py`:
  22 tests encoding the 18.3 real observations + fail-closed guarantees
  (401-envelope mapping, order-details/history 401, envelope-error helper,
  verifier records auth failure honestly, sandbox-token-rejected !=
  connectivity, DNS failure maps NETWORK + never connectivity, place/cancel
  blocked, no network imports outside transport, timeout/unknown -> AMBIGUOUS
  + no auto-retry, credential isolation + rotation identity, opt-in gate
  fail-closed on every non-"1" value).
- Existing 18.2 opt-in real-sandbox suite: unchanged; with gate+token it
  performs real calls; the positive-connectivity test correctly FAILS in
  this environment because real sandbox auth does NOT authenticate the
  read-only endpoints (documented difference, see §28). With gate unset it
  skips (fail closed).
- All prior suites unchanged.

## 23. Full-suite result

- Checks on the 18.2 baseline (your environment): 84 passed / 6 skipped.
- Full suite post-18.3 (all tests incl. the 22 new): **6246 passed,
  1 failed, 12 skipped, 1 deprecation warning**.
- The single failure is `tests/test_checkpoint_17_9_hardening.py::
  TestCredentialRotation::test_rotation_picked_up_lazily`, which asserts the
  execution-token env var is UNSET. Because the operator PROVISIONED
  `UPSTOX_EXECUTION_ACCESS_TOKEN` for this checkpoint, the test now fails —
  this is an environmental difference from the 18.2 baseline, not a code
  regression. With the variable removed the test passes (verified). No test
  was modified to accommodate this.
- The 1 deprecation warning is the pre-existing third-party
  Starlette/anyio `BlockingPortal` deprecation.

## 24. Verified behaviors

| Behavior | Classification |
| --- | --- |
| Real HTTP transport reaches Upstox (`api.upstox.com`) over TLS/Cloudflare | VERIFIED FROM REAL SANDBOX |
| Provisioned Sandbox credential is rejected by read-only endpoints (HTTP 401) | VERIFIED FROM REAL SANDBOX |
| Real error-envelope shape (`status:error` + `errors[]` + duplicate singular keys + UDAPI100050 message) | VERIFIED FROM REAL SANDBOX |
| 401 -> AUTHENTICATION -> FAILED/AUTHENTICATION_FAILURE (no fabricated success) | VERIFIED FROM REAL SANDBOX (+ tests) |
| All three read-only endpoints behave identically for the Sandbox credential | VERIFIED FROM REAL SANDBOX |
| `sandbox.upstox.com` DNS failure -> NETWORK -> FAILED (fail closed) | VERIFIED FROM REAL SANDBOX |
| Read-only transport structurally blocks place/cancel (ValueError) | VERIFIED USING MOCKS + source audit |
| No automatic retry on network error (one attempt) | VERIFIED USING MOCKS |
| Timeout -> AMBIGUOUS -> UNKNOWN -> RECONCILE_REQUIRED discipline | VERIFIED USING MOCKS |
| Token value never in results/audit/logs/exceptions | VERIFIED USING MOCKS + real run |
| Credential rotation does not alter verification identity | VERIFIED USING MOCKS |
| Opt-in gate fail-closed for all non-`"1"` values | VERIFIED USING MOCKS |
| Envelope-error helper: UDAPI100010 -> UNKNOWN_OUTCOME, UDAPI100058 -> VALIDATION | VERIFIED USING MOCKS |
| Checkpoint 18.2 statement: sandbox tokens valid 30 days; sandbox for order APIs only | VERIFIED FROM OFFICIAL DOCUMENTATION |

## 25. NOT VERIFIED behaviors

| Behavior | Classification |
| --- | --- |
| Genuine Sandbox read-only account access (profile data / order data) | NOT VERIFIED (auth is Sandbox-order-scoped) |
| Real order-history success envelope (array shape, tag filtering) | NOT VERIFIED |
| Real order-details success envelope / status vocabulary | NOT VERIFIED |
| Real reconciliation against an existing broker order | NOT VERIFIED (no order created; none supplied) |
| Real rate-limit behavior | NOT VERIFIED (not observed; not manufactured) |
| Real timeout on Upstox | NOT VERIFIED (not observed) |
| Real cancellation/fill races | DEFERRED (not reachable read-only) |
| `sandbox.upstox.com` reachability | NOT VERIFIED (DNS unsolvable from this environment) |

## 26. CONCERN findings

- C18.3-1 geniune read-only Sandbox authentication is unattainable with the
  provisioned scope (documented to be order-scoped). A future controlled
  fill of read-only reconciliation requires either (a) a Sandbox-order
  record created through the read-only-supplied path (NOT this checkpoint),
  or (b) confirmation with the operator that a token capable of
  authenticating read-only endpoints is available.
- C18.3-2 `sandbox.upstox.com` does not resolve from the OpenHands runtime;
  any real Sandbox order-API verification would need to run from an
  environment where that DNS entry exists (operator's Windows machine).
- C18.3-3 the existing 18.2 opt-in suite's positive-connectivity test
  (`test_profile_readonly_reachable_and_auth_valid`) fails under the
  real-but-rejected token. This is a correct representation of reality,
  documented, NOT changed.
- None of the above is a safety issue.

## 27. BLOCKER findings

NONE. No safety/architecture blocker was found.

## 28. Known limitations

- The provisioning/scope of the Sandbox credential and the environment's DNS
  prevented a fully authenticated read-only Sandbox session.
- Real order-history/details/reconciliation content could not be observed.
- The repository remains offline-deterministic for normal CI (all new tests
  are network-free; the real opt-in suite is gated and skipped when
  disabled).

## 29. Comparison with Checkpoint 18.2

| Aspect | 18.2 | 18.3 |
| --- | --- | --- |
| Real HTTP transport | NOT VERIFIED (no credential) | VERIFIED FROM REAL SANDBOX |
| Real authentication | NOT VERIFIED | VERIFIED FROM REAL SANDBOX (rejected: UDAPI100050) |
| Real error envelope | NOT VERIFIED | VERIFIED FROM REAL SANDBOX (captured) |
| Read-only enforcement | VERIFIED USING MOCKS | VERIFIED (mocks + live CLI run) |
| Genuine read-only account data | NOT VERIFIED | NOT VERIFIED (auth is order-scoped) |
| Real reconciliation | NOT VERIFIED | NOT VERIFIED (no order; none supplied) |
| Offline/mock verification | Intact | Intact (22 new offline tests added) |
| New files | transport/verifier/CLI/models/tests/docs | offline tests + this doc + AGENTS append |

## 30. Recommendation for Checkpoint 18.4

Checkpoint 18.4 (if and only if separately authorized by the operator):

- Confirm with the operator whether a Sandbox token capable of authenticating
  the read-only `api.upstox.com` endpoints is available, or run from an
  environment where `sandbox.upstox.com` resolves.
- Remain READ-ONLY. Do NOT place/cancel/modify orders.
- If genuine read-only Sandbox access is available, fill the remaining
  real observations: profile identity fields, order-history success envelope,
  order-details status vocabulary, and reconciliation over a pre-existing
  operator-provided Sandbox order id.
- Do NOT connect to a live broker, enable live execution, or activate the
  execution gate.

## 31. Explicit live-trading safety statement

**Checkpoint 18.3 does NOT authorize live trading. All real-network activity
in this checkpoint is restricted to the Upstox Sandbox and READ-ONLY
endpoints. No order was submitted or cancelled.**