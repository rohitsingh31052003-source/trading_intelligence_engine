# Checkpoint 15.1 — Execution Authorization Boundary Audit & Design (CORRECTED)

## Correction From Previous 15.1 Conclusion

The initial Checkpoint 15.1 audit (2026-09-02) incorrectly concluded:

> "OperationalTradeIntent IS the execution authorization."

This conclusion is **rejected** because it directly contradicts the frozen architectural contract established in Checkpoints 13.3–14.6.

### Why the Previous Conclusion Was Incorrect

1. **Checkpoint 13.3 explicitly distinguishes the concepts:**
   ```
   Operational Trade Intent ≠ Execution Authorization
   ```

2. **Checkpoint 14.2 model docstring states:**
   > "It is NOT authorization, NOT an execution permission, NOT an execution command, NOT an order..."

3. **Checkpoint 14.6 froze the subsystem with the explicit understanding** that authorization is a separate downstream boundary, not the intent itself.

4. **The absence of an authorization implementation does not mean the intent becomes authorization.** It means the authorization layer has not yet been implemented. The boundary remains defined but unimplemented.

### Corrected Conclusion

```
OperationalTradeIntent ≠ Execution Authorization
```

- **OperationalTradeIntent** is a concrete operational representation of a trade plan that may potentially proceed toward execution.
- **Execution Authorization** is a separate, future layer that grants explicit permission for a specific OperationalTradeIntent to proceed toward execution under defined conditions.
- These are distinct facts requiring separate identity semantics: `intent_id` identifies WHAT is intended; `authorization_id` identifies the permission granted to that intent.

---

## 1. Scope

This checkpoint is an **architecture-first audit only**. No implementation changes are made.

The purpose is to determine exactly where Execution Authorization should begin, what it should consume, what it should produce, what responsibility belongs to it, and how it must remain strictly separated from all upstream and downstream subsystems.

The existing `OperationalTradeIntent` boundary from Checkpoint 14 is FROZEN and is treated as the authoritative upstream INPUT to authorization. It is NOT authorization itself.

## 2. Repository State

The repository is at Checkpoint 14.6 frozen state plus Product Phases 1–5 and Phases 6A–6F. The Operational Trade Intent subsystem (Checkpoints 14.1–14.6) is complete and frozen. No authorization layer exists anywhere in the production source tree.

**Test baseline (post-Checkpoint 14.6):** 5101 passed, 2 pre-existing yfinance-related failures, 3 skipped.

## 3. Exact Files Inspected

### Operational Trade Intent (frozen upstream)
- `src/engine/models/operational_trade_intent.py` — OperationalTradeIntent model + factory
- `src/engine/intelligence/operational_trade_intent.py` — OperationalTradeIntentEngine
- `src/engine/intelligence/operational_trade_intent_application.py` — OperationalTradeIntentApplicationService

### Dashboard layer
- `src/dashboard/services.py` — DashboardAnalysisService
- `src/dashboard/views.py` — ActionabilityState, derive_actionability, DashboardTradeView
- `src/dashboard/app.py` — FastAPI routes

### Upstream models
- `src/engine/models/trade_plan.py` — TradePlan, RiskPlanStatus
- `src/engine/models/market_scan.py` — MarketScanResult, InstrumentScanResult
- `src/engine/models/trade_candidate.py` — TradeCandidate
- `src/engine/models/trade_decision.py` — TradeDecision, DecisionClassification
- `src/engine/models/opportunity.py` — TradeOpportunity, OpportunityStatus
- `src/engine/models/paper_trade.py` — PaperTrade, PaperTradeStatus

### Relevant tests
- `tests/test_operational_trade_intent.py` — 125 tests
- `tests/test_operational_trade_intent_engine.py` — 69 tests
- `tests/test_operational_trade_intent_application.py` — 58 tests
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests
- `tests/test_dashboard.py` — 67 tests
- `tests/test_workstation.py` — 95 tests
- `tests/test_watchlist_scanner.py` — 75 tests

### Existing documentation
- `AGENTS.md` — repository memory
- `docs/checkpoint_13_3_execution_authorization_boundary_audit.md`
- `docs/checkpoint_13_4_execution_authorization_to_execution_command_boundary_audit.md`
- `docs/checkpoint_13_6_final_execution_architecture_integration_and_freeze_audit.md`
- `docs/checkpoint_14_6_final_operational_trade_intent_integration_and_freeze_audit.md`

## 4. Existing Authorization Abstractions

### Search Results

Exhaustive search of the entire source tree for authorization-related keywords across all `.py`, `.md`, and config files:

| Keyword | Result |
|---------|--------|
| `authorization`, `authorized`, `authorize` | **No production code.** Only occurrences are explicit negations in `OperationalTradeIntent` docstrings ("NOT authorization") and conceptual audit documents (Checkpoint 13.3/13.4). |
| `approval`, `approved`, `approve` | **No classes found.** |
| `permission` | **No classes found.** |
| `kill_switch`, `emergency_stop` | **No classes found.** |
| `risk_gate`, `execution_mode` | **No classes found.** |
| `revoke`, `expire`, `supersede` | **No production code.** Only in conceptual audit documents. |
| `live_mode`, `paper_mode`, `live_trading` | **No mode system exists.** The system is paper-trading-only by design. |
| `account binding` | **No classes found.** |

### Classification of Closest Existing Concepts

| Concept | File | Classification |
|---------|------|----------------|
| `RiskPlanStatus.VALID` | `src/engine/models/trade_plan.py:121` | **Risk-calculation status** — describes whether deterministic risk math produced a usable position size. NOT authorization. |
| `ActionabilityState.READY_FOR_REVIEW` | `src/dashboard/views.py:121` | **Presentation/actionability mirror** — deterministic mapping from existing outputs. Gates paper-trade creation only. NOT authorization. |
| `OperationalTradeIntent` | `src/engine/models/operational_trade_intent.py` | **Operational snapshot** — immutable reference to a TradePlan. Explicitly documented as NOT authorization. May later be presented to a future authorization layer. |
| `EligibilityStatus.ELIGIBLE` | `src/engine/models/opportunity.py:115` | **Opportunity eligibility** — gates whether a candidate should be surfaced. NOT execution authorization. |
| `PaperTradeStatus` | `src/engine/models/paper_trade.py:94` | **Paper-trade lifecycle** — simulation artifact. NOT authorization. |
| `DecisionClassification` | `src/engine/models/trade_decision.py:41` | **Decision classification** — technical evidence strength. NOT authorization. |

### Conclusion

**No authorization-like subsystem exists in the repository.** The closest concepts are all explicitly classified as non-authorization in their own docstrings and in Checkpoint 13.3.

## 5. Operational Trade Intent Boundary

The `OperationalTradeIntent` (Checkpoint 14) is the authoritative **upstream input** to the future Execution Authorization layer. It is NOT the authorization itself.

### What OperationalTradeIntent Is
- An immutable, frozen+slots dataclass
- A read-only projection/reference of a VALID TradePlan
- NOT authorization, NOT execution permission, NOT an execution command, NOT an order
- NOT a broker request, NOT a position, NOT a portfolio, NOT a paper trade
- A snapshot that "may later be presented to a future authorization layer" (Checkpoint 14.2 docstring)
- The intent identifies WHAT is intended; authorization identifies the permission granted to that intent

### What OperationalTradeIntent Produces
- `intent_id` — deterministic identity (`"intent-" + sha256[:16]`)
- `content_fingerprint` — cryptographic proof of economic content (`"fp-" + sha256[:16]`)

### Critical Distinction

```
OperationalTradeIntent (what)
        ≠
Execution Authorization (permission)
        ≠
Execution Command (how)
        ≠
Broker Order (where)
```

The previous Checkpoint 15.1 audit incorrectly stated that OperationalTradeIntent "IS the execution authorization." This was corrected: OperationalTradeIntent is the upstream artifact that future authorization will consume. Authorization is a separate layer with its own identity, lifecycle, and responsibility.
- All planning geometry copied verbatim from TradePlan
- No recalculation of any planning values

### Current Data Flow Into Authorization Boundary

```
TradePlan (frozen, Checkpoint 12.6)
    ↓
OperationalTradeIntent (frozen, Checkpoint 14.6)
    ↓
[FUTURE: Execution Authorization — Checkpoint 15.x]
```

There is **no current path** from intent to authorization because no authorization layer exists.

## 6. Authorization Responsibility

Execution Authorization is responsible for exactly one thing:

> Determining whether a specific OperationalTradeIntent is permitted to proceed toward execution command generation.

Authorization is NOT responsible for:
- Creating or modifying OperationalTradeIntent
- Modifying TradePlan
- Modifying MarketScanResult
- Modifying PaperTrade
- Constructing execution commands
- Contacting brokers
- Placing orders
- Managing positions
- Calculating P&L
- Performing market analysis
- Making trading decisions

## 7. Input Contract

### Sole Upstream Input

**One input:** An `OperationalTradeIntent` instance.

Authorization must consume the intent **by value** (extracting fields for verification). It must NOT mutate the intent. The intent is immutable.

### What Authorization Must Receive

| Input | Required | Purpose |
|-------|----------|---------|
| `intent_id` | Yes | Identity of the intent being authorized |
| `content_fingerprint` | Yes | Verify intent has not changed since creation |
| `plan_id` | Yes | Provenance reference |
| All economic fields | Yes | Eligibility verification (instrument, direction, geometry, quantity, risk) |

### What Authorization Must NOT Receive as Input

- Candle data
- Market data
- Future market data
- Broker credentials
- Broker account identifiers
- Execution results
- Paper trade results
- Performance metrics
- Signal scores
- Decision classifications (as authorization substitutes)

## 8. Output Contract

### Sole Downstream Output

**One output:** An authorization verdict (record) indicating whether the specific intent is permitted to proceed.

### What Authorization Must Produce

| Output | Required | Purpose |
|--------|----------|---------|
| `authorization_id` | Yes | Deterministic identity of the authorization event |
| `intent_id` | Yes | Reference to the authorized intent |
| `plan_id` | Yes | Provenance |
| `status` | Yes | Lifecycle state (UNAUTHORIZED/ELIGIBLE/AUTHORIZED/EXPIRED/REVOKED/SUPERSEDED) |
| `content_fingerprint` | Yes | Fingerprint of the intent at authorization time |
| `created_at` | Yes | When authorization was granted |
| `valid_from` | Yes | When authorization becomes effective |
| `expires_at` | Yes | When authorization expires |
| `scope` | Yes | What is permitted |
| `issuer` | Yes | Who/what granted authorization |

### What Authorization Must NOT Produce

- Broker orders
- Execution commands
- Fills
- Positions
- P&L
- Broker-specific routing
- Order lifecycle events

## 9. Lifecycle

### Recommended Lifecycle (from Checkpoint 13.3, validated)

```
UNAUTHORIZED
      ↓
ELIGIBLE (policy gates pass, awaiting human consent)
      ↓
AUTHORIZED (human consent recorded)
      ↓
EXPIRED / REVOKED / SUPERSEDED
```

### State Definitions

| State | Meaning |
|-------|---------|
| `UNAUTHORIZED` | No authorization exists for this intent |
| `ELIGIBLE` | Policy gates pass; human consent pending |
| `AUTHORIZED` | Human consent recorded; intent may proceed toward execution command |
| `EXPIRED` | Authorization lifetime elapsed |
| `REVOKED` | Authorization explicitly withdrawn |
| `SUPERSEDED` | Intent superseded; authorization invalidated |

### Lifecycle Validation

The proposed lifecycle is **appropriate** for this architecture:
- UNAUTHORIZED is the initial state for every intent
- ELIGIBLE requires explicit policy gate evaluation (not automatic)
- AUTHORIZED requires explicit human consent (not automatic)
- Terminal states (EXPIRED/REVOKED/SUPERSEDED) are one-way
- No state silently reverts to AUTHORIZED

### What Creates UNAUTHORIZED
- The initial state of every OperationalTradeIntent before authorization is requested

### What Makes an Intent ELIGIBLE
- Policy gates pass: intent is structurally valid, risk plan is valid, intent has not expired, required fields exist, no policy restriction blocks it

### Who/What Grants AUTHORIZED
- Explicit human consent (manual authorization)
- Policy eligibility alone is NOT sufficient for AUTHORIZED

### What Causes Expiration
- Authorization validity period elapses
- Intent expires (intent.valid_until reached)
- Underlying TradePlan becomes stale

### What Causes Revocation
- User revocation
- Emergency stop activation
- Trading disabled
- Account disabled
- Intent cancellation

### What Causes Supersession
- Material change to intent geometry (entry, stop, target, quantity, direction)
- New OperationalTradeIntent created from same TradePlan with different content

### Terminal States
- EXPIRED, REVOKED, SUPERSEDED are terminal. No transition back to AUTHORIZED.

### Legal Transitions
- UNAUTHORIZED → ELIGIBLE (policy gates pass)
- ELIGIBLE → AUTHORIZED (human consent)
- ELIGIBLE → UNAUTHORIZED (policy gate fails)
- AUTHORIZED → EXPIRED (time)
- AUTHORIZED → REVOKED (explicit withdrawal)
- AUTHORIZED → SUPERSEDED (intent replaced)

### Illegal Transitions
- Any transition FROM a terminal state back to AUTHORIZED must be rejected
- EXPIRED → AUTHORIZED without re-authorization must be rejected
- REVOKED → AUTHORIZED without re-authorization must be rejected
- SUPERSEDED → AUTHORIZED must be rejected

### Unknown State Behavior
- Unknown, missing, malformed, expired, revoked, or superseded authorization must NEVER be interpreted as authorized. Fail-closed.

## 10. Eligibility vs Authorization

### Policy Eligibility

Policy eligibility is a **pre-check** that determines whether an intent *could* be authorized. It is a system determination based on structural validity:

- Intent is structurally valid (non-empty intent_id, valid instrument, valid direction)
- Risk plan is valid (RiskPlanStatus.VALID)
- Intent has not expired (valid_until not passed)
- Required fields exist
- No policy restriction blocks it (instrument allowed, trading enabled, no emergency stop)

### Authorization

Authorization is an **explicit permission** that allows the intent to proceed. It requires:

1. Policy eligibility passes (system determination)
2. Human consent recorded (human decision)

### Boundary Between Eligibility and Authorization

```
Eligibility (system)
    ↓
    policy gates pass?
    YES → ELIGIBLE (awaiting human consent)
    NO  → NOT ELIGIBLE (cannot proceed)
         ↓
    (human consent required)
         ↓
Authorization (human)
    ↓
    consent granted?
    YES → AUTHORIZED
    NO  → remains ELIGIBLE or reverts to UNAUTHORIZED
```

### Key Distinction

- **Eligibility** = "this intent passes all automated checks"
- **Authorization** = "a human has explicitly permitted this intent to proceed"

Eligibility does NOT imply authorization. Authorization does NOT imply eligibility (though in practice authorization should verify eligibility first).

## 11. Human vs Policy Authorization

### Proposed Hybrid Model

The architecture should distinguish:

**Policy eligibility** (system):
- Intent is structurally valid
- Risk plan is valid
- Intent has not expired
- Required fields exist
- No policy restriction blocks it

**Authorization** (human):
- Explicit permission for a specific intent
- Recorded with issuer, timestamp, and scope
- Immutable once granted

### Recommendation

**Hybrid human + policy authorization:**

1. Policy gates determine eligibility (system)
2. Human explicitly grants authorization for a specific intent

Policy gates alone are NOT sufficient for authorization. There must be no configuration flag that silently enables automatic authorization.

### Critical Safety Principle

The path:
```
MarketScanResult → TradePlan → OperationalTradeIntent → automatic authorization → execution
```
is **unacceptable** without an independent safety boundary. The authorization layer IS that boundary.

## 12. Identity

### Authorization Identity

Authorization must have its own deterministic identity:

```
authorization_id = "auth-" + sha256[:16]
```

Generated from a canonical payload that includes:
- `intent_id`
- `plan_id`
- `content_fingerprint`
- `issuer`
- `authorization_method`
- `created_at`
- `valid_from`
- `expires_at`
- `scope`
- `policy_reference`
- `safety_check_summary`
- `label`
- `metadata`

### Identity Relationships

| Identity | Format | Purpose |
|----------|--------|---------|
| `plan_id` | `"plan-" + sha256[:16]` | Source planning truth |
| `intent_id` | `"intent-" + sha256[:16]` | Operational snapshot being authorized |
| `authorization_id` | `"auth-" + sha256[:16]` | Authorization event record |

### Identity Verification Invariants

```
authorization.intent_id == authorized_intent.intent_id    (MUST match)
authorization.content_fingerprint == intent.content_fingerprint  (MUST match at authorization time)
authorization.plan_id == intent.plan_id                    (MUST match for provenance)
```

### Determinism

Authorization identity must be deterministic. No randomness. No UUID. No wall-clock timestamps in the identity payload (timestamps are values, not identity inputs).

## 13. Fingerprint Binding

### Content Fingerprint Contract

The `OperationalTradeIntent.content_fingerprint` is a SHA-256 prefix (`"fp-" + sha256[:16]`) of the canonical economic content:

- instrument, direction, entry, stop, target_1
- engine_risk_distance, engine_reward_distance, engine_risk_reward_ratio
- quantity, planned_risk, maximum_risk
- risk_plan_status

The fingerprint EXCLUDES operational metadata (timestamps, labels, warnings, rationale, existing_decision, actionability, metadata).

### Authorization Fingerprint Binding

Authorization must:
1. Record the `content_fingerprint` of the intent at authorization time
2. Verify that the intent presented for execution has the same `content_fingerprint`
3. Reject any intent whose `content_fingerprint` does not match the authorized fingerprint

### Why Fingerprint Binding Matters

Without fingerprint binding:
```
Intent A (entry=100) → Authorization A
    ↓
Intent B (entry=200, same intent_id) → accepted as authorized
```

With fingerprint binding, Intent B's different fingerprint causes rejection.

### Re-Authorization on Material Change

If the intent's content_fingerprint changes (material change to instrument, direction, entry, stop, target, quantity, planned risk, or risk_plan_status), a new authorization is required. The old authorization becomes SUPERSEDED.

## 14. Immutability and Mutation Audit

### Authorization Must Not Mutate

Authorization must NOT mutate:
- `OperationalTradeIntent` — immutable snapshot
- `TradePlan` — authoritative planning truth
- `MarketScanResult` — analytical output
- `PaperTrade` — simulation artifact
- Any analytical object

### Authorization Must Create an Immutable Record

Authorization must create a new, immutable authorization record. The likely desired relationship is:

```
OperationalTradeIntent
        ↓
Authorization Record (immutable)
        ↓
Authorized Intent Snapshot (immutable)
```

NOT:
```
OperationalTradeIntent
        ↓
mutate intent to "AUTHORIZED"
```

### Authorized Intent Snapshot

The authorization layer should create an `AuthorizedIntentSnapshot` — an immutable record that captures the exact state of the intent at the moment of authorization. This snapshot:
- Is immutable once created
- Carries its own identity
- Binds to the authorization_id
- Preserves the content_fingerprint for downstream verification
- Contains only the fields needed for execution command generation

### Frozen Boundaries

The OperationalTradeIntent model (Checkpoint 14) must NOT be modified to add authorization status fields. The current model is frozen and complete. Authorization state lives in the authorization layer, not in the intent.

## 15. Field-Level Crossing Matrix

For every `OperationalTradeIntent` field, determine what crosses into authorization:

| Field | Must Cross | May Cross | Must Remain Upstream | Must NOT Cross | Generated by Authorization |
|-------|-----------|-----------|---------------------|---------------|--------------------------|
| `intent_id` | Yes | | | | |
| `plan_id` | Yes | | | | |
| `instrument` | Yes | | | | |
| `timeframe` | Yes | | | | |
| `direction` | Yes | | | | |
| `entry` | Yes | | | | |
| `stop` | Yes | | | | |
| `target_1` | Yes | | | | |
| `engine_risk_distance` | Yes | | | | |
| `engine_reward_distance` | Yes | | | | |
| `engine_risk_reward_ratio` | Yes | | | | |
| `quantity` | Yes | | | | |
| `planned_risk` | Yes | | | | |
| `maximum_risk` | Yes | | | | |
| `risk_plan_status` | Yes | | | | |
| `existing_decision` | | Yes | | | |
| `actionability` | | Yes | | | |
| `created_at` | Yes | | | | |
| `evaluation_timestamp` | | Yes | | | |
| `valid_until` | Yes | | | | |
| `content_fingerprint` | Yes | | | | |
| `version` | Yes | | | | |
| `warnings` | | | Yes | | |
| `rationale` | | | Yes | | |
| `label` | | Yes | | | |
| `metadata` | | Yes | | | |

### Authorization-Generated Fields

These fields are generated by the authorization layer and do not cross from upstream:

| Field | Purpose |
|-------|---------|
| `authorization_id` | Deterministic identity of authorization event |
| `status` | Lifecycle state |
| `authorized_at` | When authorization was granted |
| `valid_from` | When authorization becomes effective |
| `expires_at` | When authorization expires |
| `issuer` | Who/what granted authorization |
| `authorization_method` | How authorization was granted |
| `scope` | What is permitted |
| `policy_reference` | Which policy version was applied |
| `safety_check_summary` | Which safety gates were evaluated |

## 16. Time and Validity

### Temporal Fields

| Field | Layer | Purpose |
|-------|-------|---------|
| `created_at` | Intent | When the intent was created (caller-supplied, timezone-aware) |
| `evaluation_timestamp` | Intent | When market data was evaluated |
| `valid_until` | Intent | Policy-derived expiry for the intent |
| `authorized_at` | Authorization | When authorization was granted |
| `valid_from` | Authorization | When authorization becomes effective |
| `expires_at` | Authorization | When authorization expires |

### Validity Rules

1. Authorization must have a bounded validity period. Indefinite authorization is not permitted.
2. `valid_from` must be >= `authorized_at`
3. `expires_at` must be > `valid_from`
4. Authorization must not outlive the intent's `valid_until`
5. Expired authorization must fail closed
6. Future-dated authorization (`valid_from` in the future) must not be treated as currently valid unless explicitly designed

### No Hidden Wall-Clock Dependencies

Authorization domain logic must NOT call `datetime.now()`. The caller supplies:
- `authorized_at` (explicit timestamp)
- `valid_from` (explicit timestamp)
- `expires_at` (explicit timestamp, derived from valid_from + configured duration)

If a clock abstraction is needed, it must be injected at the application boundary, not embedded in domain logic.

## 17. Re-Authorization Rules

### Changes That Invalidate Authorization

Any material change to the intent's content_fingerprint invalidates the authorization. Material changes include:

| Field | Change Invalidates Authorization? |
|-------|----------------------------------|
| `instrument` | Yes |
| `direction` | Yes |
| `entry` | Yes |
| `stop` | Yes |
| `target_1` | Yes |
| `quantity` | Yes |
| `planned_risk` | Yes |
| `engine_risk_distance` | Yes |
| `engine_risk_reward_ratio` | Yes |
| `risk_plan_status` | Yes |
| `execution_mode` | Yes (future) |
| `account` | Yes (future) |
| `intent_id` | Yes (new intent = new authorization) |
| `content_fingerprint` | Yes (by definition) |

### Non-Material Changes

These changes do NOT invalidate authorization (but may affect the authorized intent snapshot):

| Field | Change Invalidates Authorization? |
|-------|----------------------------------|
| `label` | No |
| `metadata` | No |
| `warnings` | No |
| `rationale` | No |
| `evaluation_timestamp` | No |

### Re-Authorization Rule

```
If intent.content_fingerprint != authorized_snapshot.content_fingerprint:
    authorization = SUPERSEDED (or INVALID)
    new authorization required for the new intent
```

A new authorization is ALWAYS required when the intent's content_fingerprint changes. Authorization must NOT silently approve a modified intent.

## 18. Execution-Mode Binding

### Current State

**No execution mode system exists.** The system is paper-trading-only. There is no `PAPER`/`LIVE` mode flag, enum, or configuration.

### Authorization Mode Binding (Future)

When a mode system is introduced, authorization must bind to the execution mode:

- `PAPER` authorization must NOT permit `LIVE` execution
- `LIVE` authorization must NOT permit `PAPER` execution
- Unknown mode must fail closed (no authorization)

### Mode Escalation Prevention

```
PAPER authorization → LIVE command    MUST FAIL CLOSED
LIVE authorization → PAPER command    MUST FAIL CLOSED (different scope)
```

Authorization must not permit mode escalation. The mode is part of the authorization scope and is verified before any execution command is generated.

## 19. Account Binding

### Current State

**No account layer exists.** No account identifiers, account permissions, or broker account concepts exist in the repository.

### Authorization Account Binding (Future)

Authorization must be **intent-specific**, not account-specific at the authorization layer. Account identity belongs to:
- Execution Command — specifies the target account
- Broker layer — executes against the account

Authorization answers "is this intent permitted to proceed?" — not "which account?"

### Account Mismatch Behavior

When account binding is introduced:
- Authorization for intent A on account X must not authorize intent B on account Y
- Account mismatch must fail closed
- Account identity must be verified before execution command generation (future execution layer responsibility)

### Separation of Authentication and Authorization

Authentication (who you are) and authorization (what you can do) must remain separate concepts. Authorization does not authenticate the user; it verifies that a specific intent is permitted.

## 20. Paper-Trading Separation

### Current Architecture

```
TradePlan
    ├──→ PaperTrade
    │       SIMULATION
    │
    └──→ OperationalTradeIntent
             ↓
         [FUTURE: Authorization]
             ↓
         [FUTURE: Execution]
```

### Paper Trading Must Remain Independent

- PaperTrade is a simulation artifact
- Authorization is a permission for execution
- PaperTrade and Authorization are separate concerns
- A paper trade result must NEVER become an authorization
- A paper trade result must NEVER authorize live execution

### Verification

The repository confirms:
- PaperTradeStatus is distinct from any authorization status
- PaperTrade has no path to execution
- PaperTradingEngine creates NO authorization records
- Paper-trade operations (Product Phase 5) create NO authorization records
- The dashboard paper-trading section is read-only presentation

## 21. Execution Separation

### Proof: No Path from AUTHORIZED to Automatic Order

Authorization must only establish permission. It must NOT:
- Place an order
- Construct a broker request
- Contact Upstox
- Contact another broker
- Create a position
- Calculate fills
- Calculate P&L
- Mutate PaperTrade
- Invoke a Broker Adapter

### Current Verification

**No execution path exists in the repository.** The following were searched and confirmed absent:

| Path | Exists? |
|------|---------|
| OperationalTradeIntent → Execution Command | No |
| READY_FOR_REVIEW → Execution | No (gates paper-trade creation only) |
| RiskPlanStatus.VALID → Execution | No (risk-calculation status only) |
| PaperTrade → Execution | No (simulation only) |
| Dashboard → Execution | No (read-only presentation) |
| Configuration flag → automatic authorization | No |

### Future Architecture Must Remain

```
OperationalTradeIntent
        ↓
Execution Authorization
        ↓
Authorized Intent Snapshot
        ↓
Execution Command
        ↓
Broker Adapter
        ↓
Broker Order
```

Authorization terminates at AUTHORIZED or NOT AUTHORIZED. It does not construct orders or contact brokers.

## 22. Persistence Boundary

### Current State

**No authorization persistence exists.** The OperationalTradeIntent has no persistence (deferred from Checkpoint 14.5).

### Persistence Decision

**Defer persistence to the implementation checkpoint.** The current audit does not require persistence because:

1. No authorization implementation is being created
2. The authorization contract can be fully defined without persistence
3. Persistence concerns (schema, atomic writes, recovery, schema versioning) are implementation details

### Future Persistence Considerations

When persistence is implemented:
- Authorization records must survive process restart
- In-memory authorization is NOT sufficient for production
- Persistence belongs in the authorization layer (not in the intent layer)
- Schema versioning required
- Atomic writes required
- Integrity verification on load

## 23. Replay and Concurrency Considerations

### Duplicate Authorization

The architecture must prevent:
```
one intent → multiple ambiguous authorizations → multiple unintended execution attempts
```

### Rules

1. Authorization for a given `intent_id` must be **idempotent or rejecting**
2. If an active authorization for `intent_id` exists → return existing or reject duplicate
3. A new authorization for the same intent must not create a second independent record
4. If the intent is superseded (new `intent_id`), the old authorization is invalidated

### Replay Protection

- Authorization identity must be deterministic (not random)
- Replayed authorization requests for the same intent at the same time must produce the same `authorization_id`
- Timestamps in authorization must be explicit (supplied by caller), not wall-clock

### Stale Authorization

- Expired authorization must fail closed
- Revoked authorization must fail closed
- Superseded authorization must fail closed
- Unknown authorization state must fail closed

## 24. Fail-Closed Rules

### Principle

**Unknown → NOT AUTHORIZED**

### Situations That Must Produce NOT AUTHORIZED

| Situation | Result |
|-----------|--------|
| Missing intent | NOT AUTHORIZED |
| Invalid intent | NOT AUTHORIZED |
| Fingerprint mismatch | NOT AUTHORIZED |
| Intent expired | NOT AUTHORIZED |
| Authorization expired | NOT AUTHORIZED |
| Revoked authorization | NOT AUTHORIZED |
| Superseded authorization | NOT AUTHORIZED |
| Unknown authorization state | NOT AUTHORIZED |
| Missing required field | NOT AUTHORIZED |
| Execution mode mismatch | NOT AUTHORIZED |
| Account mismatch | NOT AUTHORIZED |
| Changed quantity | NOT AUTHORIZED (requires re-authorization) |
| Changed geometry | NOT AUTHORIZED (requires re-authorization) |
| Changed risk | NOT AUTHORIZED (requires re-authorization) |
| Changed direction | NOT AUTHORIZED (requires re-authorization) |
| Malformed authorization | NOT AUTHORIZED |
| Stale authorization | NOT AUTHORIZED |
| Emergency stop active | NOT AUTHORIZED |
| Trading disabled | NOT AUTHORIZED |
| Unknown execution mode | NOT AUTHORIZED |

### No Ambiguous State May Become Authorized

Authorization is fail-closed by design. Any ambiguity, missing data, unexpected state, or unknown condition must result in NOT AUTHORIZED. This is a safety-critical boundary.

## 25. Dependency Boundary

### What Authorization May Import

```
OperationalTradeIntent (model)
        ↓
Authorization (domain)
```

Authorization should import:
- `engine.models.operational_trade_intent` — the intent model
- `engine.models.trade_plan` — RiskPlanStatus (for eligibility checks)
- Standard library only (hashlib, datetime, decimal, enum, dataclasses)

### What Authorization Must NOT Import

| Module | Reason |
|--------|--------|
| `engine.intelligence.paper_trading` | Paper trading is a sibling path |
| `engine.intelligence.market_scanner` | Market analysis is upstream |
| `engine.intelligence.trade_planning` | Trade planning is upstream |
| `engine.intelligence.historical_replay` | Historical analysis is upstream |
| `engine.intelligence.paper_trade_performance` | Performance is downstream |
| `engine.data.historical_provider` | Data providers are upstream |
| `engine.data.historical_service` | Data services are upstream |
| `dashboard.services` | Dashboard is application layer |
| `dashboard.app` | FastAPI is application layer |
| Any broker SDK | Broker adapters are downstream |
| `fastapi` | HTTP framework is application layer |

### Application/Dashboard Separation

If an application/dashboard layer is required for authorization, it must be identified separately from the domain authorization engine. The domain engine must remain independent of HTTP, FastAPI, and dashboard concerns.

## 26. Test Architecture

### Test Categories Required for Future Authorization Implementation

| Category | Purpose |
|----------|---------|
| Identity | Deterministic authorization_id generation |
| Determinism | Same inputs produce same authorization |
| Immutability | Authorization record cannot be mutated |
| Lifecycle | State transitions (UNAUTHORIZED → ELIGIBLE → AUTHORIZED → terminal) |
| Eligibility | Policy gate evaluation |
| Explicit authorization | Human consent required, not automatic |
| Expiration | Time-based invalidation |
| Revocation | Explicit withdrawal |
| Supersession | Intent replacement invalidates old authorization |
| Fingerprint binding | Intent fingerprint verified at authorization and execution |
| Intent mismatch | Wrong intent rejected |
| Execution-mode mismatch | Wrong mode rejected |
| Account mismatch | Wrong account rejected |
| Replay protection | Duplicate authorization handled correctly |
| Fail-closed behavior | Unknown/missing/expired/revoked → NOT AUTHORIZED |
| Point-in-time validity | No future data used |
| Separation from execution | Authorization produces no orders |
| Separation from PaperTrade | Paper trade cannot become authorization |
| Separation from RiskPlanStatus | VALID ≠ AUTHORIZED |
| Separation from ActionabilityState | READY_FOR_REVIEW ≠ AUTHORIZED |
| No mutation of upstream | Intent, TradePlan, MarketScanResult unchanged |
| No broker integration | No broker SDK imported or called |
| No auto-bypass | No path from any upstream state to authorized without explicit grant |

### Current Test Coverage

The existing test suite already enforces many of these boundaries for the upstream OperationalTradeIntent:
- Authorization separation (no auth fields on intent)
- Execution separation (no exec fields on intent)
- Broker neutrality (no broker fields on intent)
- Paper-trading separation (no paper trade fields on intent)
- Immutability (frozen+slots)
- Deterministic identity
- Point-in-time independence
- No recalculation

## 27. Limitations

1. **No authorization layer exists.** This audit defines the boundary and contract only. Implementation is a future checkpoint.
2. **No mode system (paper/live).** Authorization must distinguish paper from live when a mode system is introduced.
3. **No emergency stop exists.** A future execution system will require a global emergency-disable boundary above authorization.
4. **No account/portfolio layer.** Account binding for authorization is deferred.
5. **No broker adapter.** Broker neutrality of authorization is a design principle, not a tested implementation.
6. **No human approval UI.** The human consent mechanism is undefined.
7. **No persistence.** Authorization persistence is deferred.
8. **No execution command layer.** The downstream execution boundary is defined conceptually only.

## 28. Implementation Decision

### Option B: Implement a minimal dedicated Execution Authorization domain boundary.

**Rationale:** The audit confirms that no authorization layer exists, no execution path exists, and no automatic bypass exists. The OperationalTradeIntent boundary (Checkpoint 14) is frozen and provides a clean upstream contract. The conceptual authorization contract from Checkpoint 13.3 is sound. The repository is ready for a minimal authorization implementation.

### What Would Be Created

| File | Purpose |
|------|---------|
| `src/engine/models/execution_authorization.py` | AuthorizationRecord model, AuthorizationStatus enum |
| `src/engine/intelligence/execution_authorization.py` | ExecutionAuthorizationEngine (stateless, pure) |
| `tests/test_execution_authorization.py` | Domain engine tests |
| `tests/test_execution_authorization_integration.py` | Integration tests |

### What Must NOT Be Modified

| File/Component | Reason |
|---------------|--------|
| `src/engine/models/operational_trade_intent.py` | FROZEN (Checkpoint 14.2) |
| `src/engine/intelligence/operational_trade_intent.py` | FROZEN (Checkpoint 14.4) |
| `src/engine/intelligence/operational_trade_intent_application.py` | FROZEN (Checkpoint 14.5) |
| `src/engine/models/trade_plan.py` | FROZEN (Checkpoint 12.6) |
| `src/engine/models/paper_trade.py` | FROZEN (Checkpoint 12.6) |
| Any upstream engine/model | FROZEN |
| Dashboard services/views/app | Not authorization concern |

### Model Ownership

Authorization model is owned by `engine.models` (domain layer).

### Engine/Service Ownership

Authorization engine is owned by `engine.intelligence` (domain layer). No dashboard coupling.

### API/Application Ownership

If a dashboard API endpoint is needed, it belongs in `dashboard/` as a thin HTTP layer over the domain engine. This is a future decision.

### Test Location

Tests in `tests/test_execution_authorization.py` and `tests/test_execution_authorization_integration.py`.

### Serialization/Persistence Decision

Defer persistence to the implementation checkpoint. The domain model and engine must be serialization-ready (frozen+slots, deterministic identity) but persistence is a separate concern.

## 29. Final Verdict

### PASS — Authorization boundary is clearly defined and ready for implementation.

**Summary:**

- **No authorization layer exists** in production code — only conceptual design in Checkpoint 13.3 documents
- **No automatic bypass exists** — no path from any current object reaches execution without going through the (future) authorization layer
- **OperationalTradeIntent is the clean upstream boundary** — frozen, immutable, deterministic, with intent_id and content_fingerprint designed for authorization binding
- **The conceptual contract from Checkpoint 13.3 is sound** and validated against the current repository state
- **The proposed lifecycle (UNAUTHORIZED → ELIGIBLE → AUTHORIZED → EXPIRED/REVOKED/SUPERSEDED) is appropriate**
- **Fail-closed is the correct principle** — unknown/missing/expired/revoked/superseded must NEVER be authorized
- **Authorization must be immutable** — changes require new authorization records
- **Fingerprint binding is essential** — prevents authorization transfer between different intents
- **No broker, no execution, no account, no mode system, no emergency stop currently exist** — all are future concerns
- **Checkpoints 10–14 remain frozen** — no modifications to any frozen component are required or recommended

### Recommended Next Checkpoint

**Checkpoint 15.2 — Execution Authorization Model & Deterministic Identity Implementation**

Implement the minimal `ExecutionAuthorization` model (frozen+slots), `AuthorizationStatus` enum, and deterministic `authorization_id` generation. Define the `AuthorizationRecord` fields and the `create_authorization()` factory. Do NOT implement the engine, API, persistence, or any downstream integration.
