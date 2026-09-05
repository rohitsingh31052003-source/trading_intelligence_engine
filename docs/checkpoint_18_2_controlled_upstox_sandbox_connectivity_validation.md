# CHECKPOINT 18.2 — CONTROLLED UPSTOX SANDBOX CONNECTIVITY & READ-ONLY BROKER VERIFICATION

## 1. Checkpoint 18.2 status

**COMPLETE / PASS WITH LIMITATIONS.**

Checkpoint 18.2 establishes the FIRST REAL-HTTP connectivity boundary of the
execution architecture -- but **READ-ONLY ONLY** and **SANDBOX ONLY**. It
implements the real HTTP transport behind the frozen `UpstoxBrokerClient`
protocol, restricted to three verified read-only endpoints, plus a
deterministic, audit-producing read-only verification service, a CLI, and
offline/opt-in tests.

**REAL SANDBOX CONNECTIVITY: NOT VERIFIED in this environment.** No genuine
Upstox Sandbox execution credential is available (`UPSTOX_EXECUTION_ACCESS_TOKEN`
is absent). `UPSTOX_ANALYTICS_TOKEN` (a historical-data credential) IS present
but is deliberately NEVER used for execution. The read-only transport, the
verifier, the CLI, and the opt-in test suite are implementation-complete and
fail closed; every online behavior remains **NOT VERIFIED** until an operator
supplies a genuine sandbox token under the opt-in gate.

## 2. Overall verdict

**PASS WITH LIMITATIONS**

- All offline implementation, tests, documentation, sweeps, and regression
  baselines complete successfully.
- The real-connectivity verification is provisioned and ready, but could not
  be executed because no sandbox credential is present (this is NOT a
  failure; per the checkpoint brief, "IF NO SANDBOX CREDENTIAL IS AVAILABLE
  … complete all offline implementation and tests … document REAL SANDBOX
  CONNECTIVITY as NOT VERIFIED").
- Real Upstox Sandbox behavior (HTTP transport against the real endpoints,
  real authentication, real payload shapes) is **NOT VERIFIED**, and no
  claim of live execution safety is made.

**This checkpoint does NOT authorize live trading.** Even when a future
operator enables the gate with a genuine sandbox token and the read-only
verification succeeds, that does NOT authorize live trading.

## 3. Scope

Checkpoint 18.2 scope boundary:

- **IN:** pre-connection safety audit; sandbox credential boundary; the real
  read-only HTTP transport; read-only verification (profile / order details /
  order history against PRE-EXISTING data only); response validation
  (fail closed); reconciliation verification; credential rotation/failure
  tests; network-failure tests; live/paper isolation; test architecture
  (offline vs opt-in sandbox); observability/audit; documentation; AGENTS.md
  append; full regression; security sweeps; frozen integrity.
- **OUT (forbidden / not done):** any order submission, cancellation,
  modification, or creation; any invocation of an endpoint capable of
  creating/modifying trading state; use of `UPSTOX_ANALYTICS_TOKEN` for
  execution; hard-coded or committed credentials; production/live credential
  provider; live Upstox trading API connection; enabling live execution;
  weakening `validate_adapter_mode` or paper/live isolation; modifying
  `ExecutionCommand` / authorization / TradePlan / strategy / risk / signal /
  intelligence / research layers; reopening frozen checkpoints (10–16, 17.1–
  17.9); rewriting prior AGENTS.md history; weakening existing tests.

## 4. Frozen baseline

- Checkpoints 10–16: FROZEN, unchanged (verified via `git status`, no tracked
  file modified).
- Checkpoint 17.1–17.9: FROZEN, unchanged (all 17.2–17.9 suites pass
  unchanged).
- Checkpoint 18.1: PASS WITH LIMITATIONS (audit/readiness only).

Baseline test run (reproduced exactly): **6163 passed / 0 failed / 6 skipped /
2 pre-existing deprecation warnings** (StarletteDeprecationWarning +
anyio BlockingPortal deprecation; optional `fastapi`/`yfinance`/`httpx`
installed for this sandbox so fewer skips than the 18.1 baseline of 6
skipped / 0 failed).

## 5. Phase 1 — Pre-connection safety audit (DONE)

Inspected Checkpoint 17.7/17.8 implementation and 18.1 audit, and verified:

| Check | Result |
| --- | --- |
| Live execution disabled | VERIFIED — no live path exists anywhere |
| Execution gate disabled | VERIFIED — `LiveExecutionGate` is a pure design surface not wired to any path |
| No live fallback | VERIFIED — `select_adapter` fail-closed mode match; no PAPER↔LIVE fallback |
| No credential leakage | VERIFIED by sweep + tests (no token in results/exceptions/audit/repr) |
| No accidental order path | VERIFIED — only the read-only transport; place/cancel raise |
| No frozen component needs modification | VERIFIED — zero tracked-file diffs |

### Read-only endpoints used (Phase 1D/1E)

Only genuinely read-only GET endpoints are implemented, verified from the
official Upstox developer documentation (fetched this checkpoint):

| Endpoint | Path | Purpose | Read-only |
| --- | --- | --- | --- |
| Get Profile | `GET https://api.upstox.com/v2/user/profile` | Authentication validity + identity/capability | YES (GET, READ) |
| Get Order Details | `GET https://api.upstox.com/v2/order/details?order_id=...` | Existing order status | YES (GET, READ) |
| Get Order History | `GET https://api.upstox.com/v2/order/history?tag=...` | Existing order history (array) | YES (GET, READ) |

No place/modify/cancel/multi/exit endpoint is implemented or importable as a
network path.

## 6. Phase 2 — Sandbox credential boundary (DONE)

- Source remains `UPSTOX_EXECUTION_ACCESS_TOKEN` via the existing
  `EnvironmentUpstoxCredentialProvider` (lazy read, never stored).
- `UPSTOX_ANALYTICS_TOKEN` is NEVER read by the execution side; it appears
  only in the redaction scrub list by name.
- The verifier's `token_available` boolean is the ONLY credential-derived
  value that reaches audit/projection; the token VALUE never does.
- Fail closed: missing, empty, or non-string credentials produce
  `UNVERIFIED` / no request; `_scrub_token` guarantees the raw value is
  removed from any message even if it echoes back from a broker body.
- Deterministic redaction tests assert the token marker never appears in
  results, exceptions, audit records, repr/str, or CLI output.

## 7. Phase 3 — Real sandbox client (DONE, behind the frozen boundary)

`src/engine/intelligence/upstox_sandbox_transport.py` implements the frozen
`UpstoxBrokerClient` protocol for the read-only surface:

- `get_profile()` → `UpstoxProfileResponse` (masked; only non-sensitive facts;
  account identifier is replaced by a boolean `user_id_present`).
- `get_order(tag, order_id)` → single-record details (primary) or history
  array reduced to the latest record for one order (fallback); multiple
  distinct orders / empty array / missing order id → `UNKNOWN` semantics.
- `check_health()` → profile-based reachability probe.
- `place_order()` / `cancel_order()` **raise `ValueError`** — the transport
  can never create, modify, or cancel an order.
- Injectable `urlopen` for deterministic offline tests; default
  `urllib.request.urlopen` at this boundary. Timeout, gzip/deflate decode,
  HTTP error mapping, and User-Agent are handled here only.
- Dependency direction preserved:
  `ExecutionCommand → SubmissionInfrastructure → BrokerAdapter →
  UpstoxBrokerAdapter → UpstoxBrokerClient → Sandbox HTTP Transport →
  Upstox Sandbox`. The core intelligence/domain/persistence layers gain no
  network imports (AST sweep verified; network is confined to the transport).

## 8. Phase 4 — Read-only sandbox verification (PROVISIONED / NOT EXECUTED here)

`src/engine/intelligence/sandbox_readonly_verifier.py` orchestrates:

1. Opt-in gate (`CHECKPOINT_17_8_REAL_BROKER=1`) — reuses the repository-wide
   real-broker gate; 18.2 introduces NO second gate. Disabled → UNVERIFIED,
   no request.
2. Credential availability (boolean only).
3. Frozen 17.8 startup guard (identity/mode/env/capability/config checks).
4. Read-only checks: profile identity; order details + history for
   operator-supplied PRE-EXISTING order ids.
5. Broker-neutral audit trail + aggregate `SandboxReadOnlyVerification`
   (`real_sandbox_connected` True ONLY when SANDBOX + token + guard + a
   recognized profile identity).

Because no sandbox credential is available here, real verification steps 4–5
were exercised ONLY with the fake transport (offline) and the CLI is
wired and tested offline; REAL sandbox behavior is NOT VERIFIED.

## 9. Phase 5 — Response validation (VERIFIED USING MOCKS)

Offline deterministic tests cover: HTTP status handling (401/403/429/404/5xx/
4xx), success/error envelope handling, missing fields, wrong types, empty
arrays, multiple records, missing broker order ID, unknown status, unknown
error code, authentication/authorization failure, rate-limit response, and
timeout/network failure. Every failure maps into the broker-neutral taxonomy
(`AdapterResult`-compatible audit fields via `BrokerResultStatus` /
`BrokerErrorCode` / `BrokerErrorCategory`); ambiguous kinds (timeout /
malformed / unknown) map to AMBIGUOUS/UNKNOWN; no automatic retry (a single
read attempt per call — verified by a call-count test).

## 10. Phase 6 — Reconciliation verification (PARTIALLY VERIFIED USING MOCKS)

Offline tests verify the mapping against pre-existing records: tag lookup,
order_id fallback, zero matches (UNKNOWN), exactly one match (success), and
multiple distinct records (AMBIGUOUS). Because the sandbox cannot be queried
without a credential and NO order is ever created to manufacture data,
real reconciliation behavior at Upstox is **NOT VERIFIED** and reported as
`reconciliation_result = NOT_VERIFIED`.

## 11. Phase 7 — Credential rotation / failure testing (VERIFIED USING MOCKS)

- Missing / empty / malformed credential → fail closed before any request.
- Token value never appears in errors, audit, lifecycle state, persistence,
  logs, or repr/str (asserted).
- Credential rotation does not alter `command_id`, `client_order_id`, or
  `idempotency_key` (deterministic identity derived from command only), and
  does not alter the verification identity.

## 12. Phase 8 — Network failure testing (VERIFIED USING MOCKS)

Mocked `urlopen` covers: timeout, connection failure (URLError), malformed
HTTP, malformed JSON, empty response, HTTP 5xx, HTTP 4xx, authentication
failure, rate-limit, unexpected schema, broker error, unknown error. All
ambiguous execution-related outcomes stay UNKNOWN; no blind retry; no
automatic submission.

## 13. Phase 9 — Live/paper isolation (VERIFIED USING MOCKS)

- The sandbox transport blocks order placement and cancellation outright.
- A SANDBOX verification surface has no `execution_mode`, no live fallback,
  and no way to be selected as LIVE.
- Missing sandbox credentials fail closed; wrong environment identity
  (LIVE/PROD/REAL) fails closed via the frozen 17.8 startup guard.
- Credentials alone never authorize execution; the conclusion text is
  explicit.

## 14. Phase 10 — Test architecture (DONE)

Clear separation:

- **Offline deterministic tests** (always run): transport (42), verifier
  (35), CLI (7), plus the demo script (18 checks). No network, fake tokens
  only.
- **Opt-in sandbox tests** (`tests/test_checkpoint_18_2_sandbox_opt_in.py`,
  6 tests): SKIPPED BY DEFAULT; require `CHECKPOINT_17_8_REAL_BROKER=1` AND a
  genuine `UPSTOX_EXECUTION_ACCESS_TOKEN`; never run in normal CI; never use
  a live credential; never submit/cancel/modify orders. If the gate is set
  but the token is absent the module FAILS CLOSED (skips with an explicit
  "ambiguous environment" reason) — it never converts a missing credential
  into a pass. The gate convention is the repository's existing
  `CHECKPOINT_17_8_REAL_BROKER`.

## 15. Phase 11 — Observability and audit (DONE)

`SandboxVerificationAuditEntry` records: operation type (PROFILE /
ORDER_DETAILS / ORDER_HISTORY / HEALTH), environment (SANDBOX), endpoint
category (opaque string), request purpose, timestamp, response classification
(SUCCESS / FAILED / AMBIGUOUS / UNVERIFIED), normalized result
(`BrokerResultStatus`), error code/category, reconciliation result, and
broker order id only when supplied by the caller (never a credential).
Ambiguous audit entries MUST carry an error code + UNKNOWN status (enforced
invariant). Token values, Authorization headers, and bearer tokens are
structurally absent.

## 16. Phase 12 — Documentation (this file)

REAL SANDBOX observation log: NONE (no credential). No endpoint was actually
called. Behavior classification legend:

- VERIFIED USING MOCKS: response validation, reconciliation mapping, network
  failure mapping, credential boundary + rotation, live/paper isolation,
  audit invariants, CLI wiring, opt-in skip behavior.
- VERIFIED FROM OFFICIAL DOCUMENTATION: the three read-only endpoint paths,
  the sandbox 30-day token, the order-status vocabulary sample, order-history
  retention (~1 trading day).
- NOT VERIFIED: real HTTP against the sandbox; real authentication; real
  payload shapes; one-trading-day retention against this account; real
  reconciliation; real rate limits.
- DEFERRED: real connectivity execution (requires operator token + gate).
- UNSAFE / BLOCKED: any order-affecting operation (explicitly not
  implemented and guarded).

## 17. Phase 13 — AGENTS.md

A Checkpoint 18.2 entry is appended to AGENTS.md (APPEND ONLY; no prior entry
modified, reordered, compressed, or deleted).

## 18. Phase 14 — Full regression

- New Checkpoint 18.2 tests: 84 passed, 6 skipped (opt-in).
- Checkpoint 17.2–17.9 suites: 682 passed, 6 skipped (17.8 opt-in).
- Frozen Checkpoint 14–16 execution tests + planning/paper-trading: 977 passed.
- Full repository suite: see result below (baseline 6163 passed / 0 failed /
  6 skipped / 2 warnings; the addition is 84 passed + 6 new skips, with the
  totals changing accordingly; every difference is explained: the previous 6
  skips remain the 17.8 opt-in tests, and the new 6 skips are the 18.2
  opt-in tests).

## 19. Phase 15 — Frozen integrity (VERIFIED)

- Checkpoints 10–16: no tracked file modified.
- Checkpoint 17 implementation: no tracked file modified (no frozen
  component required a fix).
- `ExecutionCommand`, authorization, `SubmissionLifecycle`,
  `SubmissionInfrastructure`, `execution_gate`: unchanged.
- No frozen test modified.

## 20. Phase 16 — Security sweep (VERIFIED)

- AST/source sweep: network imports confined to the read-only transport
  (`upstox_sandbox_transport.py` uses `socket`/`urllib` by design); zero
  network imports in intelligence/domain/persistence layers.
- Zero hard-coded secrets / access tokens / bearer tokens / credential
  values; zero accidental logging; zero credential persistence.
- Zero live endpoint literals; no order-affecting endpoint implemented.
- Zero PAPER→LIVE or LIVE→PAPER fallback; zero authorization bypass; zero
  direct broker calls from core; zero accidental order-submission methods.

## 21. Files created

- `src/engine/intelligence/upstox_sandbox_transport.py`
- `src/engine/intelligence/sandbox_readonly_verifier.py`
- `src/engine/models/sandbox_readonly_verification.py`
- `tests/test_checkpoint_18_2_sandbox_transport.py`
- `tests/test_checkpoint_18_2_sandbox_verifier.py`
- `tests/test_checkpoint_18_2_sandbox_cli.py`
- `tests/test_checkpoint_18_2_sandbox_opt_in.py`
- `tests/fake_18_2_verifier.py`
- `scripts/verify_upstox_sandbox_readonly.py`
- `scripts/test_checkpoint_18_2_sandbox_readonly.py`
- `docs/checkpoint_18_2_controlled_upstox_sandbox_connectivity_validation.md`
  (this file)
- AGENTS.md (Checkpoint 18.2 entry appended)

## 22. Verified behaviors (evidence)

VERIFIED FROM OFFICIAL UPSTOX DOCUMENTATION (fetched during this checkpoint):

- `GET /v2/user/profile` read-only profile endpoint with success envelope
  `{"status":"success","data":{...exchanges/products/order_types...}}`
  (source: get-profile page).
- `GET /v2/order/details?order_id=...` read-only single-record order status
  (source: get-order-details page).
- `GET /v2/order/history?order_id=...|tag=...` read-only order-history ARRAY
  (source: get-order-history page).
- Order-history retention "remain available for one trading day" (source:
  get-order-history / get-order-details pages).
- Sandbox token "valid for 30 days" (source: sandbox page).

VERIFIED USING MOCKS / BY TEST: all response validation, reconciliation
mapping, network-failure mapping, credential boundary + rotation,
live/paper isolation, audit invariants, CLI wiring, opt-in skip/fail-closed
behavior, determinism, no-auto-retry, READ-ONLY enforcement, and the full
offline regression.

## 23. NOT VERIFIED behaviors

- Real HTTP transport against the Upstox Sandbox.
- Real authentication / token acceptance / revocation.
- Real payload shapes at runtime (single vs multi order_ids; history array
  shape; full order-status vocabulary on this account).
- Real reconciliation / one-trading-day retention against this account.
- Real rate limits / timeouts / connection behavior.
- Sandbox read-only behavior with a genuine sandbox token (none available).

## 24. CONCERN findings

None block; none require reopening any frozen checkpoint:

- C18.2-1: Real behavioral verification of the three endpoints awaits a
  genuine sandbox token and the opt-in gate (deferred by design).
- C18.2-2: `check_health` reachability also requires the token (it cannot
  probe without a credential).
- C18.2-3: One-trading-day order-history retention means long-ago orders are
  not reconcilable; the verifier requires operator-supplied recent order ids.

## 25. BLOCKER findings

NONE.

## 26. Known limitations

- No sandbox credential available; real connectivity NOT VERIFIED.
- `UPSTOX_ANALYTICS_TOKEN` present but intentionally out of scope for
  execution.
- The execution gate is design-only; nothing in the repository can place an
  order.
- Live-trading safety is NOT established (and not claimed).
- The read-only transport uses `urllib` (stdlib) only; no third-party HTTP
  client.

## 27. How an operator can run the REAL read-only verification

```
# 1. Obtain a genuine Upstox SANDBOX access token (30-day validity per docs).
# 2. Export it (NEVER hard-code / commit / paste into chat):
#    export UPSTOX_EXECUTION_ACCESS_TOKEN="<sandbox-token>"
# 3. Enable the repository-wide real-broker gate:
#    export CHECKPOINT_17_8_REAL_BROKER=1
# 4. Optionally provide PRE-EXISTING sandbox order ids for read-only
#    reconciliation (comma-separated):
#    export CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS="240108010445130,..."
# 5. Run the read-only verification (JSON mode):
#    python scripts/verify_upstox_sandbox_readonly.py --json
#    # exit 0 => read-only connectivity verified; 1 => ran but not connected;
#    # 2 => missing gate/token (fail closed, no request issued)
```

Running the opt-in tests:

```
CHECKPOINT_17_8_REAL_BROKER=1 tests/test_checkpoint_18_2_sandbox_opt_in.py
```

## 28. Recommendation for Checkpoint 18.3

RECOMMEND — CHECKPOINT 18.3 — CONTROLLED SANDBOX READ-ONLY VERIFICATION FILL
**only on separate explicit authorization and only with a genuine sandbox
credential + the opt-in gate**. 18.3 should execute the provisioned read-only
verifier/CLI against the real sandbox, record the resulting verification
audit, map any newly observed behavior, and explicitly re-mark reconciliations
NOT VERIFIED unless pre-existing orders are supplied. 18.3 must remain
READ-ONLY and must NOT connect to a live broker, place/cancel/modify orders,
or enable live execution. Do NOT begin Checkpoint 18.3 automatically.

## 29. Final freeze decision

CHECKPOINT 18.2 IS FROZEN as **PASS WITH LIMITATIONS**.

**Sandbox connectivity and read-only verification do NOT authorize live
trading.** REAL SANDBOX CONNECTIVITY IS NOT VERIFIED in this environment; all
online broker behaviors remain NOT VERIFIED; no order was submitted,
cancelled, or modified; no live broker was contacted; no live credential was
used; the credential boundary, the execution gate (disabled), and the frozen
architecture are intact.