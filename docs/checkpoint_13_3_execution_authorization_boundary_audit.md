# Checkpoint 13.3 — Execution Authorization Boundary Audit & Design

## 1. Purpose

Determine what "authorization to execute" must mean in this system before any Operational Trade Intent may proceed toward a future execution subsystem. This checkpoint defines the authorization boundary and its contract only.

This is an **audit + contract design only** checkpoint. No implementation changes are made.

## 2. Scope

- Inspect the repository for any existing authorization-like abstractions.
- Audit `READY_FOR_REVIEW`, `RiskPlanStatus.VALID`, and their relationship to authorization.
- Determine the authorization subject, identity binding, and scope.
- Define the conceptual separation between authorization, execution command, order, and position.
- Determine authorization authority, lifetime, revocation, versioning, provenance, and auditability.
- Classify safety gates and risk-limit relationships.
- Define the responsibility matrix, lifecycle, fail-closed behavior, and future execution handoff.
- Specify the future test contract.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Boundary |
| -----------|--------|----------|
| 11.1–11.8 | FROZEN | Market data → candle integrity → features → setup → evidence → `MarketScanResult` |
| 12.1–12.6 | FROZEN | `MarketScanResult` → `TradePlan` → `PaperTrade` → journal/performance → dashboard |
| 13.1 | ACCEPTED | No operational trade intent, execution path, broker order path, or authorization currently exists |
| 13.2 | ACCEPTED | Operational Trade Intent contract defined (not implemented) |

No frozen checkpoint is modified by this audit.

## 4. Exact Files Inspected

### Core Models
- `src/engine/models/trade_plan.py` — `TradePlan`, `RiskPlanStatus`, `QuantityStatus`, `QuantitySpec`
- `src/engine/models/paper_trade.py` — `PaperTrade`, `PaperTradeStatus`, `PaperExitReason`
- `src/engine/models/trade_candidate.py` — `TradeCandidate`, `CandidateDirection`, `CandidateStatus`
- `src/engine/models/trade_decision.py` — `TradeDecision`, `DecisionClassification`
- `src/engine/models/opportunity.py` — `TradeOpportunity`, `OpportunityStatus`
- `src/engine/models/market_scan.py` — `MarketScanResult`, `InstrumentScanResult`

### Intelligence Engines
- `src/engine/intelligence/trade_planning.py` — `TradePlanningEngine`
- `src/engine/intelligence/paper_trading.py` — `PaperTradingEngine`
- `src/engine/intelligence/market_scanner.py` — `MarketScanner`

### Dashboard / Presentation
- `src/dashboard/views.py` — `ActionabilityState`, `derive_actionability`, `DashboardTradeView`, `TradePlanView`, `PaperTradeView`, `WorkstationView`
- `src/dashboard/services.py` — `DashboardAnalysisService`
- `src/dashboard/paper_trade_operations.py` — `PaperTradingOperations`, `OperationalStatus`
- `src/dashboard/app.py` — FastAPI routes
- `src/dashboard/paper_trade_store.py` — `PaperTradeStore`

### Config
- `src/engine/config/trade_plan_config.py` — `TradePlanConfig`
- `src/engine/config/paper_trade_config.py` — `PaperTradeConfig`

### Prior Audits
- `docs/checkpoint_13_1_operational_trade_intent_and_execution_boundary_audit.md`
- `docs/checkpoint_13_2_operational_trade_intent_model_and_contract_audit.md`

### Tests (baseline only — not modified)
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests
- `tests/test_dashboard.py` — 67 tests
- `tests/test_workstation.py` — 95 tests
- `tests/test_watchlist_scanner.py` — 75 tests

## 5. Existing Authorization-Like Abstractions

### Search Results

The following keywords were searched across the entire codebase:

| Keyword | Result |
|---------|--------|
| `authorization`, `approve`, `approval`, `approved`, `authorized` | **No classes found.** No authorization layer exists. |
| `permission`, `execution permission`, `execution eligibility` | **No classes found.** |
| `manual approval`, `user approval`, `confirmation`, `consent` | **No classes found.** (Technical market-structure "confirmation" exists in swing/liquidity detection — unrelated.) |
| `kill switch`, `emergency stop`, `disabled trading`, `trading enabled` | **No classes found.** |
| `execution gate`, `risk gate`, `safety gate`, `policy gate` | **No classes found.** |
| `allowed instruments`, `trading session`, `account permission`, `broker permission` | **No classes found.** |
| `live mode`, `paper mode`, `live_trade`, `live_trading` | **No classes found.** The system is paper-trading-only. |

### Conclusion

**No existing object in the repository serves the role of execution authorization.** The closest existing concepts are:

- `READY_FOR_REVIEW` — a presentation-level actionability mirror (NOT authorization).
- `RiskPlanStatus.VALID` — a risk-calculation status (NOT authorization).
- `PaperTrade` — a simulation artifact (NOT authorization).
- `TradePlan` — a planning artifact (NOT authorization).

None of these authorizes execution. None of these can be interpreted as permission to send an order toward a broker.

## 6. Existing Approval/Actionability Semantics

### READY_FOR_REVIEW

**Definition:** `ActionabilityState.READY_FOR_REVIEW` (`src/dashboard/views.py:121`)

**Meaning:** "A qualified / preferred decision with an eligible opportunity AND complete trade geometry AND evidence that is not INSUFFICIENT."

**Where created:** `derive_actionability()` (`views.py:156-240`) — deterministic presentation mapping from authoritative outputs.

**Where consumed:**
- `dashboard/paper_trade_operations.py:690` — the **single eligibility gate** for paper-trade creation: `eligible = actionability is ActionabilityState.READY_FOR_REVIEW`
- Dashboard templates — drives the "worth reviewing" UI state

**What it does NOT mean:**
- Does NOT mean "execution authorized"
- Does NOT mean "place order"
- Does NOT mean "risk-plan ready" in an operational sense
- Does NOT override the existing decision

**Verdict:** `READY_FOR_REVIEW` is a **presentation-level actionability mirror**, not an execution authorization. No code path from `READY_FOR_REVIEW` reaches order creation, broker contact, or live position management.

### RiskPlanStatus.VALID

**Definition:** `RiskPlanStatus.VALID` (`src/engine/models/trade_plan.py:121`)

**Meaning:** "The plan was computed successfully: complete geometry, valid account-risk inputs, a positive risk distance, and a quantity whose planned_risk does not exceed the configured maximum_risk."

**What it describes:** Whether the deterministic risk calculation produced a usable position size.

**What it does NOT mean:**
- Does NOT mean "authorized to trade"
- Does NOT mean "ready for execution"
- Does NOT mean "BUY/SELL signal"

**Verdict:** `RiskPlanStatus.VALID` is a **risk-calculation status**, not an execution authorization.

## 7. Authorization Subject

### Preferred Conceptual Relationship

```
Operational Trade Intent
        ↓
Authorization
```

NOT:

```
Strategy → Authorization
TradePlan → Authorization
PaperTrade → Authorization
MarketScanResult → Authorization
```

### Verification

The repository currently has **no authorization layer**. The principle that authorization must be tied to a specific Operational Trade Intent is a forward-looking design requirement, not a current implementation. When authorization is implemented, it must bind to a specific `intent_id` — not to a strategy, plan, paper trade, or scan result.

## 8. Identity Binding

### Binding Target

Authorization must bind to `intent_id` — the deterministic identity of a specific Operational Trade Intent.

### Content Fingerprint

Authorization should additionally bind to a **cryptographic/content fingerprint** of the authorized intent. This prevents:

```
Intent A → Authorization A → Intent B
```

The fingerprint must cover at minimum:
- `intent_id`
- `plan_id`
- `instrument`
- `timeframe`
- `direction`
- `entry`, `stop`, `target_1`
- `quantity`
- `planned_risk`

### Safety Implication

Without content fingerprinting, a mutated intent could be executed under an authorization granted to a different intent. The fingerprint makes authorization **non-transferable**.

## 9. Authorization Scope

### Narrow Initial Scope

The initial authorization boundary should permit only:

> "execution attempt for this specific intent"

NOT:

> "permission to trade generally"

### Scope Classification

| Action | Belongs to Authorization? |
|--------|--------------------------|
| Create operational intent | No — intent creation is a planning-layer concern |
| Submit execution command for this intent | **Yes** — this is the authorization scope |
| Submit one order | No — order creation belongs to execution layer |
| Submit multiple orders | No — execution layer concern |
| Manage an existing position | No — execution/portfolio layer concern |
| Cancel an order | No — execution layer concern |
| Modify an order | No — execution layer concern |

### Recommendation

Authorization scope = **"this specific Operational Trade Intent may proceed toward execution command generation."** Nothing more.

## 10. Authorization vs Execution

### Conceptual Separation

| Concept | Meaning |
|---------|---------|
| **Authorization** | Permission for a specific intent to proceed |
| **Execution Command** | Requested action sent toward execution |
| **Execution** | Actual attempt/result |
| **Order** | Broker-side state |

### Verification

No existing code conflates these concepts because no execution path exists. The separation must be maintained when execution is implemented:

- Authorization does NOT mean order sent
- Authorization does NOT mean order accepted
- Authorization does NOT mean order filled
- Authorization does NOT mean position opened
- Authorization does NOT mean execution successful

## 11. Authorization vs Actionability

### Expected Relationship

```
READY_FOR_REVIEW
        ↓
eligible for review
        ↓
Operational Trade Intent
        ↓
Authorization
        ↓
Execution Command
```

### Verification

`READY_FOR_REVIEW` is a presentation mirror. It gates paper-trade creation (simulation only). No code interprets it as authorization. The system must NOT silently interpret `READY_FOR_REVIEW` as `AUTHORIZED`.

**Verdict:** The semantic distinction holds. `READY_FOR_REVIEW ≠ AUTHORIZED`.

## 12. Authorization vs Risk Validity

### Expected Relationship

```
RiskPlanStatus.VALID = risk mathematics valid
Authorization         = permission granted
```

Therefore: `VALID ≠ AUTHORIZED`

### Verification

A valid plan may still be:
- Unauthorized
- Stale
- Expired
- Revoked
- Outside trading hours
- Outside allowed instruments
- Blocked by an operational safety policy

`RiskPlanStatus.VALID` describes whether the risk calculation produced a usable position size. It does not grant permission to execute.

**Verdict:** The semantic distinction holds. `VALID ≠ AUTHORIZED`.

## 13. Authorization Authority

### Evaluation

| Authority Type | Appropriate? | Rationale |
|----------------|--------------|-----------|
| Human/manual authorization | **Yes** | Required for live trading — a human must explicitly permit execution |
| Deterministic policy authorization | **Partial** | Policy gates can pre-check eligibility, but cannot alone authorize live execution |
| System authorization | **No** | System should not self-authorize execution without human consent |
| Hybrid human + policy | **Yes** | Policy gates determine eligibility; human grants authorization |
| Account-level authorization | **Future** | Account permissions belong to a future account layer |
| Session-level authorization | **No** | Too coarse — authorization must be intent-specific |
| Intent-level authorization | **Yes** | The correct granularity |

### Recommendation

**Hybrid human + policy authorization:**
1. Policy gates determine eligibility (instrument allowed, trading enabled, no emergency stop, intent fresh, intent not expired, intent not superseded).
2. Human explicitly grants authorization for a specific intent.

If the repository already has a human approval mechanism: **it does not.** Authorization authority is currently absent.

## 14. Manual vs Automatic Authorization

### Evaluation

| Mode | Safety Consequence |
|------|-------------------|
| Automatic authorization | **UNSAFE** — creates `MarketScanResult → TradePlan → Operational Trade Intent → automatic authorization → future execution` without independent safety boundary |
| Manual authorization | **SAFE** — requires explicit human action |
| Policy-gated | **Partial** — necessary but not sufficient alone |
| Configurable | **Dangerous** — if "auto-authorize" is a config flag, misconfiguration could enable unintended execution |

### Recommendation

Authorization must be **manually granted** by a human for a specific intent. Policy gates determine eligibility but do not grant authorization. There must be no configuration flag that silently enables automatic authorization.

### Critical Safety Principle

The path:

```
MarketScanResult → TradePlan → Operational Trade Intent → automatic authorization → future execution
```

is **unacceptable** without an independent safety boundary. The authorization layer IS that boundary.

## 15. Authorization Lifetime

### Evaluation

| Temporal Concept | Belongs to Authorization? |
|------------------|--------------------------|
| `authorization_timestamp` | Yes — when authorization was granted |
| `valid_from` | Yes — when authorization becomes effective |
| `expires_at` | Yes — when authorization ceases to be valid |
| Session boundary | Possible — authorization may not survive session end |
| Trading-day boundary | Possible — authorization may expire at market close |
| Plan expiry | Authorization must not outlive the plan's validity |
| Intent expiry | Authorization must not outlive the intent's freshness |
| Authorization expiry | **Yes** — authorization must have its own expiry |

### Recommendation

Authorization must have a defined lifetime. It must NOT remain valid indefinitely. Recommended constraints:

- Authorization expires when the intent expires
- Authorization expires when the intent is superseded
- Authorization expires at a configurable maximum duration (e.g., end of trading day)
- Authorization expires when the underlying plan becomes stale

**Key answer:** An authorization issued for an old intent must NOT remain valid indefinitely. There is no architectural justification for indefinite authorization.

## 16. Stale Authorization

### Scenarios

| Scenario | Correct Behavior |
|----------|-----------------|
| Intent authorized, market conditions change | Authorization invalidation |
| Intent expires | Authorization invalidation |
| User changes a planning field | Intent supersession → re-authorization |
| Quantity changes | Intent supersession → re-authorization |
| Entry/stop/target changes | Intent supersession → re-authorization |
| New TradePlan supersedes old one | Intent supersession → re-authorization |

### Recommendation

Authorization must be **invalidated** when the authorized intent undergoes material changes. The correct mechanism is:

1. Material change → intent superseded (new `intent_id`)
2. Old authorization invalidated
3. New intent requires fresh authorization

Authorization must not silently survive material changes.

## 17. Revocation

### Revocable: Yes

Authorization must be revocable. Revocation triggers include:

- User revocation
- Intent cancellation
- Intent expiry
- Plan supersession
- Risk-policy change
- System shutdown
- Emergency stop / kill switch
- Trading disabled
- Account disabled
- Broker unavailable

### Conceptual Separation

| Concept | Meaning |
|---------|---------|
| **Authorization revoked** | Permission withdrawn |
| **Order cancelled** | Broker-side order cancelled (execution layer) |
| **Position closed** | Broker-side position closed (execution layer) |

These are different layers. Revocation does NOT cancel orders or close positions — it prevents new execution commands from being generated.

## 18. Duplicate Authorization

### Behavior

The architecture must prevent:

```
one intent → multiple ambiguous authorizations → multiple unintended execution attempts
```

### Recommendation

Authorization for a given `intent_id` must be **idempotent or rejecting**:

- If authorization for `intent_id` already exists and is active → return existing authorization (idempotent) or reject the duplicate.
- A new authorization for the same intent must not create a second independent authorization record.
- If the intent is superseded (new `intent_id`), the old authorization is invalidated and a new one may be granted for the new intent.

## 19. Authorization Identity

### Recommendation

Authorization must have a distinct identity:

- `authorization_id` — deterministic, distinct from `intent_id` and `plan_id`
- `intent_id` — reference to the authorized intent
- `plan_id` — reference to the source plan (provenance)

All three identities are necessary:

| Identity | Purpose |
|----------|---------|
| `plan_id` | Source of truth for planning geometry |
| `intent_id` | Operational snapshot being authorized |
| `authorization_id` | Unique record of this authorization event |

### Identity Distinction

```
plan_id         = "plan-" + sha256[:16]   (planning layer)
intent_id       = "intent-" + sha256[:16] (operational layer)
authorization_id = "auth-" + sha256[:16]  (authorization layer)
```

## 20. Authorization Versioning

### Recommendation

Once granted, an authorization should be an **immutable record**. If authorization changes:

- Create a new authorization record
- Revoke the old authorization

Authorization must NOT be mutated in place. This preserves auditability — the full history of who authorized what, when, and why is retained.

## 21. Authorization Provenance

### Required Fields

An authorization record must contain:

| Field | Purpose |
|-------|---------|
| `authorization_id` | Unique identity |
| `intent_id` | What was authorized |
| `plan_id` | Source plan (provenance) |
| `scope` | What is permitted |
| `issuer` | Who/what authorized |
| `authorization_method` | How authorization was granted |
| `created_at` | When authorization was granted |
| `valid_from` | When it becomes effective |
| `expires_at` | When it expires |
| `status` | Current lifecycle state |
| `version` | Authorization version |
| `content_fingerprint` | Fingerprint of authorized intent |
| `policy_reference` | Which policy version was applied |
| `safety_check_summary` | Which safety gates were evaluated |

### Provenance Principle

Do NOT copy the entire analytical object. Prefer references (`intent_id`, `plan_id`) and canonical snapshots (content fingerprint) where appropriate.

## 22. Authorization Auditability

At minimum, the following must be auditable:

- What was authorized
- Which intent was authorized
- Which plan it came from
- Who/what authorized it
- When it was authorized
- When it expires
- What scope was granted
- Whether it was revoked
- Why it was revoked
- Whether execution was subsequently attempted

**Note:** "execution attempted" belongs to a future execution layer. The authorization-side provenance must connect these events by recording `authorization_id` on any execution command that consumes it.

## 23. Safety Gates

### Gate Classification

| Gate | Layer |
|------|-------|
| Valid Operational Trade Intent | **Operational-intent layer** |
| Valid TradePlan | **Planning layer** |
| Risk plan status VALID | **Planning layer** |
| Intent freshness | **Operational-intent layer** |
| Intent not expired | **Operational-intent layer** |
| Intent not superseded | **Operational-intent layer** |
| Allowed instrument | **Authorization layer** |
| Allowed direction | **Authorization layer** |
| Trading session | **Authorization layer** |
| Market availability | **Execution layer** |
| Account availability | **Broker/account layer** |
| System trading enabled | **Authorization layer** |
| Emergency stop state | **Authorization layer** |
| Duplicate intent | **Operational-intent layer** |
| Existing conflicting intent | **Authorization layer** |

### Principle

Do not move responsibilities across frozen boundaries. The authorization layer checks authorization-specific gates (instrument allowed, trading enabled, emergency stop, conflicting intent). It trusts the planning layer for plan validity and the operational-intent layer for intent validity.

## 24. Risk-Limit Relationship

### Evaluation

| Option | Appropriate? | Rationale |
|--------|--------------|-----------|
| A. Trust TradePlan completely | **Partial** | TradePlan is authoritative for planning geometry, but authorization must verify operational constraints |
| B. Re-validate risk invariants | **Yes** | Authorization must verify that the intent's quantity/planned_risk match the authorized bounds |
| C. Apply additional operational limits | **Yes** | Authorization may apply operational risk limits beyond planning (e.g., max concurrent exposure) |
| D. Delegate risk enforcement to execution | **Partial** | Execution is the final constraint, but authorization must not delegate its own responsibility |

### Recommendation

**B + C:** Authorization re-validates risk invariants (quantity, planned_risk match the authorized intent) and may apply additional operational limits. The architectural distinction:

- **TradePlan** = mathematical plan validity
- **Authorization** = operational permission within policy
- **Execution** = final executable constraints

## 25. Quantity Protection

### Invariant

```
Authorized quantity = 10
        ↓
Execution attempts quantity = 100
```

This must be impossible without deliberate re-authorization.

### Recommendation

Authorization binds to the exact `quantity` and `planned_risk` of the Operational Trade Intent via the content fingerprint. Any change to quantity invalidates the authorization. The execution layer must verify that the attempted quantity matches the authorized quantity.

## 26. Price/Geometry Protection

### Invariant

An authorization for `Entry = A, Stop = B, Target = C` must not silently become authorization for `Entry = X, Stop = Y, Target = Z`.

### Recommendation

Authorization binds to `entry`, `stop`, `target_1`, and `direction` via the content fingerprint. If execution needs a transformed value (e.g., a limit price derived from the entry reference), that transformation belongs to the **execution layer**, not the authorization layer. The authorization layer records the authoritative geometry; the execution layer derives executable prices.

## 27. Broker Neutrality

### Classification

| Field | Layer |
|-------|-------|
| Broker | Broker adapter responsibility |
| Account | Account/portfolio responsibility |
| Exchange | Broker adapter responsibility |
| Broker symbol | Broker adapter responsibility |
| Order type | Execution responsibility |
| Product type | Execution responsibility |
| Routing | Broker adapter responsibility |
| Validity | Execution responsibility |

### Recommendation

Authorization must contain **no broker-specific information**. Authorization is broker-neutral. It authorizes "this intent may proceed." The choice of broker, account, exchange, and routing belongs to the execution layer.

## 28. Account Binding

### Recommendation

Authorization should be **intent-specific**, not account-specific. Account identity belongs to:

- **Operational Trade Intent** — may reference an account context
- **Execution Command** — specifies the target account
- **Broker layer** — executes against the account

Authorization does not need to know the account. It answers "is this intent permitted to proceed?" — not "which account?"

## 29. Session Binding

### Recommendation

Authorization should NOT be bound to an application process or system instance. It should be bound to:

- The intent it authorizes
- Its own lifetime (expiry)
- Trading session or trading day (configurable)

Carrying authorization across sessions introduces risk: a session crash and restart could leave stale authorization active. Authorization should be persisted and re-validated on load.

## 30. Live/Paper Separation

### Conceptual Distinction

```
Paper authorization ≠ Live authorization
```

A paper-trading permission must NEVER automatically authorize a live execution.

### Current State

The repository has **no mode system**. The application does not distinguish PAPER from LIVE via a mode flag, enum, or configuration. The system is paper-trading-only.

### Recommendation

When authorization is implemented, it must distinguish paper from live. A paper authorization must not authorize live execution. If a mode system is introduced, it must be fail-closed: unknown mode = no authorization.

## 31. Emergency Stop / Kill Switch

### Current State

**No kill switch exists.** No `kill_switch`, `emergency_stop`, `disabled_trading`, or `trading_enabled` concept was found in any file.

### Recommendation

A future execution system will require an emergency-disable boundary. The emergency stop must sit **above** authorization in the hierarchy:

```
Emergency Stop (global)
        ↓
Authorization (per intent)
        ↓
Execution Command
        ↓
Broker
```

When the emergency stop is active, ALL authorizations are effectively suspended. No new execution commands can be generated. The emergency stop must be checked before authorization is granted or before an existing authorization is consumed.

## 32. Client/Dashboard Trust Boundary

### Architectural Principle

```
Client input ≠ trusted authorization
```

Authorization must be produced by a trusted server-side boundary. Dashboard/API input is untrusted — it is a request, not a command.

### Current State

The dashboard is read-only presentation. No execution path exists. When authorization is implemented:

- Dashboard may **request** authorization
- Server-side boundary must **evaluate** and **grant** authorization
- Client cannot directly set `authorized = true`

## 33. Automatic Bypass Audit

### Search Results

The following paths were searched:

| Path | Exists? |
|------|---------|
| Operational Trade Intent → Execution Command (without authorization) | **No** — no execution command exists |
| READY_FOR_REVIEW → Execution | **No** — READY_FOR_REVIEW only gates paper-trade creation |
| VALID → Execution | **No** — VALID is a planning status only |
| PaperTrade → Execution | **No** — PaperTrade is simulation only |
| Dashboard button → Execution | **No** — dashboard is read-only |
| Configuration flag → Execution | **No** — no such flag exists |

### Verdict

**No automatic bypass exists.** No path from any current object reaches execution without going through the (future) authorization layer. This is a clean baseline.

## 34. Point-in-Time Safety

### Principles

Authorization MUST NOT:
- Use future market data
- Retroactively modify analysis
- Modify TradePlan
- Modify MarketScanResult
- Rewrite PaperTrade outcomes
- Use future execution results to justify authorization

### Recommendation

Authorization must be based on information available at the time of authorization. New market information after authorization should trigger re-validation or invalidation, not retroactive modification.

## 35. Performance Feedback Isolation

### Principles

- PaperTrade performance ≠ authorization permission
- Historical backtest performance ≠ authorization permission
- Past winning trades ≠ automatic authorization
- Strategy performance ≠ automatic live activation

### Recommendation

No automatic learning or optimization should be introduced. Authorization is a permission gate, not a performance-driven decision. Performance information may inform human decisions but must not automatically grant authorization.

## 36. Fail-Closed Behavior

### Principle

Unknown → NOT AUTHORIZED

| Condition | Result |
|-----------|--------|
| Unknown state | NOT AUTHORIZED |
| Missing intent | NOT AUTHORIZED |
| Invalid intent | NOT AUTHORIZED |
| Expired intent | NOT AUTHORIZED |
| Revoked intent | NOT AUTHORIZED |
| Unknown authorization state | NOT AUTHORIZED |
| Emergency stop active | NOT AUTHORIZED |
| Trading disabled | NOT AUTHORIZED |
| Conflicting intent exists | NOT AUTHORIZED |
| Content fingerprint mismatch | NOT AUTHORIZED |

### Recommendation

Fail-closed is the appropriate principle for authorization. Any ambiguity, missing data, or unexpected state must result in NOT AUTHORIZED. This is a safety-critical boundary.

## 37. Responsibility Matrix

| Artifact | Responsibility | Inputs | Outputs | Mutable | Source of Truth | Can Authorize? | Can Execute? |
|----------|---------------|--------|---------|---------|-----------------|----------------|--------------|
| **MarketScanResult** | Multi-instrument opportunity scan | OHLCV candles, market context | Ranked opportunities | Immutable | Sprint 11U scanner | **No** | **No** |
| **TradePlan** | Risk/position-size calculation | Trade candidate geometry, account params | Sized plan with quantity/risk | Immutable | TradePlanningEngine | **No** | **No** |
| **Operational Trade Intent** | Operational snapshot of eligible plan | TradePlan (by reference) | Broker-neutral intent snapshot | Immutable | Future operational layer | **No** | **No** |
| **Execution Authorization** | Permission for intent to proceed | Intent, policy gates, human decision | Authorization record | Immutable (versioned) | Authorization layer | **No** | **No** |
| **Execution Command** | Request toward execution | Authorization + intent | Broker-agnostic command | Immutable | Execution layer | **No** | **No** |
| **Broker** | Order execution | Execution command | Order state, fills | Mutable (external) | Broker | **No** | **Yes** |
| **Position** | Broker-side position state | Fills | Position state | Mutable (external) | Broker | **No** | **No** |
| **Portfolio** | Account-level aggregation | Positions | Portfolio state | Mutable (external) | Broker/account | **No** | **No** |

### Key Distinctions

- **Only the Broker can execute** (send orders to the market).
- **Only the Authorization layer can authorize** (grant permission).
- **No artifact can both authorize and execute** — separation of responsibilities.
- **MarketScanResult, TradePlan, Operational Trade Intent** are immutable analytical/operational records. They neither authorize nor execute.

## 38. Future Execution Boundary

### Conceptual Flow

```
Operational Trade Intent
        ↓
Execution Authorization
        ↓
[future]
Execution Command
        ↓
Broker
```

### Minimum Contract for Execution Command

A future Execution Command must consume from authorization at minimum:

- `authorization_id` — proof of authorization
- `intent_id` — the authorized intent
- Content fingerprint — verification that the intent has not changed
- Scope — what is permitted

Authorization must NOT itself construct a broker order. It terminates at `AUTHORIZED` or `NOT AUTHORIZED`.

## 39. Future Test Contract

The following test categories must be defined when authorization is implemented:

1. Authorization requires a specific Operational Trade Intent
2. No authorization without valid intent
3. Invalid intent cannot be authorized
4. Stale intent cannot be authorized
5. Expired intent cannot be authorized
6. Superseded intent cannot be authorized
7. Authorization binds to `intent_id`
8. Authorization cannot silently authorize another intent
9. Quantity cannot silently change
10. Entry cannot silently change
11. Stop cannot silently change
12. Target cannot silently change
13. Direction cannot silently change
14. Authorization cannot mutate TradePlan
15. Authorization cannot mutate MarketScanResult
16. Authorization cannot mutate PaperTrade
17. `READY_FOR_REVIEW` does not imply authorization
18. `VALID` does not imply authorization
19. Duplicate authorization behavior is deterministic
20. Authorization expiry
21. Authorization revocation
22. Authorization provenance
23. Authorization identity
24. Authorization versioning
25. Paper authorization cannot become live authorization
26. Unknown state fails closed
27. Authorization cannot directly produce an order
28. Authorization cannot directly contact a broker
29. Point-in-time safety
30. No performance feedback

## 40. Recommended Authorization Contract

### What is Execution Authorization?

Execution Authorization is an immutable, time-bounded, broker-neutral permission record that grants a specific Operational Trade Intent the right to proceed toward execution command generation. It is produced by a trusted server-side boundary after evaluating policy gates and receiving explicit human consent. It does not execute, does not construct orders, and does not contact brokers. It answers one question: "Is THIS particular Operational Trade Intent permitted to be sent toward execution?"

### What is it authorizing?

A specific Operational Trade Intent, identified by `intent_id` and bound to a content fingerprint of the intent's authoritative fields.

### What does it contain?

| Field | Type | Purpose |
|-------|------|---------|
| `authorization_id` | `str` | Deterministic identity (`"auth-" + sha256[:16]`) |
| `intent_id` | `str` | The authorized intent |
| `plan_id` | `str` | Source plan (provenance) |
| `scope` | `str` | What is permitted (e.g., "execution_attempt") |
| `issuer` | `str` | Who granted authorization |
| `authorization_method` | `str` | How it was granted (e.g., "manual", "hybrid") |
| `created_at` | `datetime` | When granted |
| `valid_from` | `datetime` | When effective |
| `expires_at` | `datetime` | When it expires |
| `status` | `AuthorizationStatus` | Lifecycle state |
| `version` | `int` | Version (immutable records) |
| `content_fingerprint` | `str` | SHA-256 of authorized intent fields |
| `policy_reference` | `str` | Policy version applied |
| `safety_check_summary` | `tuple[str, ...]` | Gates evaluated |

### What does it NOT contain?

- Broker order ID
- Fill
- Execution price
- Position
- Portfolio state
- Broker-specific routing
- Order lifecycle
- Execution result

### What creates it?

A trusted server-side authorization boundary that:
1. Validates the Operational Trade Intent is valid, fresh, not expired, not superseded
2. Evaluates policy gates (instrument allowed, trading enabled, no emergency stop, no conflicting intent)
3. Receives explicit human consent
4. Produces an immutable authorization record

### What can modify it?

**Nothing.** Authorization records are immutable. Changes require creating a new authorization and revoking the old one.

### What revokes it?

- User revocation
- Intent cancellation, expiry, or supersession
- Emergency stop activation
- Trading disabled
- Authorization expiry
- Content fingerprint mismatch (intent changed)

### What does it produce?

Authorization only. It produces an `AUTHORIZED` or `NOT AUTHORIZED` verdict. It must NOT produce an order.

## 41. Recommended Lifecycle

```
UNAUTHORIZED
      ↓
ELIGIBLE (policy gates pass, awaiting human consent)
      ↓
AUTHORIZED (human consent recorded)
      ↓
EXPIRED / REVOKED / SUPERSEDED
```

### State Distinctions

| State | Meaning |
|-------|---------|
| `UNAUTHORIZED` | No authorization exists |
| `ELIGIBLE` | Policy gates pass; human consent pending |
| `AUTHORIZED` | Human consent recorded; intent may proceed |
| `EXPIRED` | Authorization lifetime elapsed |
| `REVOKED` | Authorization explicitly withdrawn |
| `SUPERSEDED` | Intent superseded; authorization invalidated |

### Separation

- **Eligibility** = policy gates pass (system determination)
- **Authorization** = human consent recorded (human decision)
- **Expiration** = time-based invalidation
- **Revocation** = explicit withdrawal
- **Supersession** = intent replaced
- **Execution** = future layer (not authorization concern)

## 42. Recommended Next Boundary

The next boundary to audit and design is:

```
Execution Authorization
        ↓
Execution Command
```

This would define what an execution command is, how it consumes authorization, what it contains, and how it remains broker-neutral while carrying enough information for a future broker adapter to construct an order.

## 43. Limitations

1. **No authorization layer exists.** This audit defines the contract only. Implementation is a future checkpoint.
2. **No mode system (paper/live).** Authorization must distinguish paper from live when a mode system is introduced.
3. **No emergency stop exists.** A future execution system will require a global emergency-disable boundary.
4. **No account/portfolio layer.** Account binding for authorization is deferred.
5. **No broker adapter.** Broker neutrality of authorization is a design principle, not a tested implementation.
6. **No human approval UI.** The human consent mechanism is undefined.

## 44. Implementation Decision

**NO IMPLEMENTATION CHANGES.**

The authorization boundary can be clearly defined. No blocking architectural defect exists. The conceptual contract is sound.

## 45. Final Verdict

**PASS**

The system now has a clearly defined conceptual separation between Operational Trade Intent and Execution Authorization. No execution path exists, no broker is contacted, no orders are created, and the authorization contract can be implemented in a future checkpoint without modifying the frozen analytical, planning, or simulation boundaries.

---

## Appendix A: Test Baseline

### Commands Executed

```
python -m pytest tests/test_trade_planning.py tests/test_paper_trading.py tests/test_paper_trading_operations.py -q
python -m pytest tests/test_dashboard.py tests/test_workstation.py tests/test_watchlist_scanner.py -q
python -m pytest tests/ -q
```

### Results

| Suite | Result |
|-------|--------|
| trade_planning + paper_trading + paper_trading_operations | 350 passed |
| dashboard + workstation + watchlist_scanner | 268 passed |
| Full suite | 4849 passed, 2 failed (pre-existing: missing `yfinance`), 3 skipped |

### Failures

The 2 failures are pre-existing and unrelated to this checkpoint:
- `tests/test_live_data_integration.py::TestProviderFailure::test_yahoo_not_ready_when_no_backend`
- `tests/test_live_data_integration.py::TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`

Both fail because `yfinance` is not installed in the current environment. This is an optional dependency limitation, not a regression.

## Appendix B: Repository State Summary

| Aspect | State |
|--------|-------|
| Authorization layer | **Absent** — defined conceptually only |
| Execution path | **Absent** |
| Broker integration | **Absent** |
| Order model | **Absent** |
| Position model | **Absent** |
| Kill switch | **Absent** |
| Mode system (paper/live) | **Absent** |
| Human approval mechanism | **Absent** |
| Automatic bypass paths | **None found** |
| Frozen boundaries | **Intact** — no modifications |
