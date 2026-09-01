# Checkpoint 14.3 — Operational Trade Intent Factory & TradePlan Integration Boundary Audit

## 1. Purpose

Determine the exact integration boundary for `create_intent_from_plan()` and `OperationalTradeIntent` within the existing architecture. Specifically: where, when, under what conditions, and by whom an `OperationalTradeIntent` should be created from a `TradePlan`. This audit establishes the integration contract for a future implementation checkpoint without modifying any production code.

## 2. Scope

- **In scope**: Current `TradePlan` consumers, current `OperationalTradeIntent` factory behavior, factory ownership analysis, creation-trigger analysis, timestamp validity, point-in-time safety, mutation safety, duplicate creation, persistence, presentation, authorization compatibility, execution compatibility, paper-trading compatibility, dependency direction, API/application boundary, test architecture.
- **Out of scope**: Execution Authorization implementation, Execution Command implementation, Broker Adapter implementation, broker integration, live trading, dashboard UI changes, persistence implementation, any production code changes.

## 3. Frozen Checkpoints

| Checkpoint | Status | Key Artifact |
|------------|--------|--------------|
| 10.8 | FROZEN | Historical Research Pipeline |
| 11.8 | FROZEN | Current-Market Analytical Output (`MarketScanResult`) |
| 12.6 | FROZEN | Trade Planning + Paper Trading + Performance (`TradePlan`, `PaperTrade`) |
| 13.6 | FROZEN | Pre-Execution Architecture (conceptual Operational Trade Intent) |
| 14.1 | ACCEPTED | Operational Trade Intent Implementation Boundary Audit |
| 14.2 | ACCEPTED | Operational Trade Intent Model & Deterministic Identity Implementation |

## 4. Exact Files Inspected

### Production Code
- `src/engine/models/operational_trade_intent.py` (533 lines) — `OperationalTradeIntent`, `create_intent_from_plan()`, `_canonical_value()`, `_canonical_identity_payload()`, `_canonical_fingerprint_payload()`, `_sha256_prefix()`
- `src/engine/models/trade_plan.py` (464 lines) — `TradePlan`, `RiskPlanStatus`, `QuantityStatus`, `QuantitySpec`, `DEFAULT_QUANTITY_SPEC`
- `src/engine/intelligence/trade_planning.py` (967 lines) — `TradePlanningEngine`, `plan()`, `_plan_id()`
- `src/engine/intelligence/paper_trading.py` (831 lines) — `PaperTradingEngine`, `create()`, `_paper_trade_id()`
- `src/engine/config/trade_plan_config.py` — `TradePlanConfig`
- `src/engine/reporting/trade_planning.py` — `TradePlanFormatter`
- `src/dashboard/services.py` (2258+ lines) — `DashboardAnalysisService`, `TradePlanRequest`, `PaperTradeRequest`, `OperationsRequest`, `plan_trade()`, `create_paper_trade()`, `run_paper_trading_cycle()`
- `src/dashboard/paper_trade_operations.py` (1241 lines) — `PaperTradingOperations`, `OperationsConfig`, `run_once()`, `_create_eligible_trade()`
- `src/dashboard/views.py` (2000+ lines) — `DashboardTradeView`, `TradePlanView`, `ActionabilityState`, `derive_actionability()`
- `src/dashboard/app.py` — `/api/trade-plan`, `/api/paper-trading/run-once`, `/workstation` routes

### Documentation
- `docs/checkpoint_14_1_operational_trade_intent_implementation_boundary_audit.md`
- `docs/checkpoint_14_2_operational_trade_intent_model_and_identity_implementation.md`
- `docs/checkpoint_13_6_final_execution_architecture_integration_and_freeze_audit.md`

### Tests
- `tests/test_operational_trade_intent.py` (125 tests)
- `tests/test_trade_planning.py` (158 tests)
- `tests/test_paper_trading.py` (114 tests)
- `tests/test_paper_trading_operations.py` (78 tests)

## 5. Current TradePlan Consumers

### 5.1 `DashboardAnalysisService.plan_trade()` (`src/dashboard/services.py:993-1064`)

| Attribute | Value |
|-----------|-------|
| **Call direction** | `analyze()` → `_build_view()` → `TradePlanningEngine.plan()` → `TradePlan` |
| **TradePlan usage** | Read-only projection into `TradePlanView` via `_to_trade_plan_view(plan)` |
| **Creates PaperTrade** | No |
| **Creates presentation output** | Yes — `TradePlanView` |
| **Persists anything** | No (TradePlan is not persisted) |
| **Should create OperationalTradeIntent** | **No** — this is a query/review path; intent is not authorization |

**Evidence**: `plan_trade()` is a pure orchestration method that reuses `analyze()` + `TradePlanningEngine.plan()` and projects the result into a `TradePlanView`. It is invoked by the `/api/trade-plan` route (GET) and the workstation. No mutation, no persistence.

### 5.2 `DashboardAnalysisService.create_paper_trade()` (`src/dashboard/services.py:1070-1135`)

| Attribute | Value |
|-----------|-------|
| **Call direction** | `analyze()` → `TradePlanningEngine.plan()` → `TradePlan` → `PaperTradingEngine.create()` |
| **TradePlan usage** | Passed to `PaperTradingEngine.create(plan=plan, plan_id=plan.plan_id)` |
| **Creates PaperTrade** | Yes |
| **Creates presentation output** | Yes — `PaperTradeView` |
| **Persists anything** | Yes — via `PaperTradeStore.save()` when store attached |
| **Should create OperationalTradeIntent** | **No** — TradePlan → PaperTrade is a sibling path; OperationalTradeIntent is a separate sibling path. Paper trade creation does NOT imply operational intent. |

**Evidence**: `create_paper_trade()` calls `self.trade_planning_engine.plan(...)` then passes the resulting `TradePlan` to `self.paper_trading_engine.create(..., plan=plan, plan_id=plan.plan_id)`. The `PaperTrade` already embeds the plan's identity via `plan_id`. No intent is needed for paper trading.

### 5.3 `DashboardAnalysisService.run_paper_trading_cycle()` (`src/dashboard/services.py:1311-1367`)

| Attribute | Value |
|-----------|-------|
| **Call direction** | `OperationsRequest` → `PaperTradingOperations.run_once()` |
| **TradePlan usage** | Indirect — `PaperTradingOperations._create_eligible_trade()` calls `trade_planning_engine.plan()` internally |
| **Creates PaperTrade** | Yes (when actionability is `READY_FOR_REVIEW`) |
| **Creates presentation output** | Yes — `OperationsCycleView` |
| **Persists anything** | Yes — paper trades via store |
| **Should create OperationalTradeIntent** | **No** — operations is paper-trading-only; intent is a separate concern |

**Evidence**: `PaperTradingOperations.run_once()` internally calls `self.service.trade_plading_engine.plan()` (via `_create_eligible_trade()`) when the existing opportunity is `READY_FOR_REVIEW`. This is paper-trading simulation, not operational intent.

### 5.4 `PaperTradingEngine.create()` (`src/engine/intelligence/paper_trading.py`)

| Attribute | Value |
|-----------|-------|
| **Call direction** | Accepts `plan` (TradePlan) and `plan_id` as parameters |
| **TradePlan usage** | Copies geometry/plan fields verbatim into PaperTrade |
| **Creates PaperTrade** | Yes |
| **Creates presentation output** | No (returns PaperTrade model) |
| **Persists anything** | No (caller persists) |
| **Should create OperationalTradeIntent** | **No** — engine must remain focused on simulation |

### 5.5 `TradePlanFormatter` (`src/engine/reporting/trade_planning.py`)

| Attribute | Value |
|-----------|-------|
| **Call direction** | Accepts TradePlan, returns str |
| **TradePlan usage** | Read-only formatting |
| **Creates PaperTrade** | No |
| **Creates presentation output** | Yes — formatted report string |
| **Persists anything** | No |
| **Should create OperationalTradeIntent** | **No** — pure reporting |

### 5.6 `_to_trade_plan_view()` (`src/dashboard/services.py` helper)

| Attribute | Value |
|-----------|-------|
| **Call direction** | Projects TradePlan → TradePlanView |
| **TradePlan usage** | Read-only projection |
| **Should create OperationalTradeIntent** | **No** — pure projection |

## 6. Current OperationalTradeIntent Consumers

### Production Consumers
**NONE.** `OperationalTradeIntent` and `create_intent_from_plan()` have ZERO production consumers. Verified by exhaustive search:
- Only 4 files reference `OperationalTradeIntent` or `create_intent_from_plan`:
  - `src/engine/models/operational_trade_intent.py` (definition)
  - `tests/test_operational_trade_intent.py` (tests)
  - `docs/checkpoint_14_1_...md` (documentation)
  - `docs/checkpoint_14_2_...md` (documentation)

### Test Consumers
**ONLY** `tests/test_operational_trade_intent.py` — 125 focused tests on the model and factory in isolation.

### Documentation References
- `docs/checkpoint_14_1_operational_trade_intent_implementation_boundary_audit.md`
- `docs/checkpoint_14_2_operational_trade_intent_model_and_identity_implementation.md`

### Accidental Integration
**NONE.** No production code path, no dashboard route, no service method, no engine references `OperationalTradeIntent` or `create_intent_from_plan()`.

## 7. Existing Factory Behavior

The factory `create_intent_from_plan()` is a **pure function** with the following properties:

1. **Input contract**: Accepts 22 keyword-only parameters (all TradePlan field values + operational metadata: `created_at`, `evaluation_timestamp`, `valid_until`, `label`, `metadata`)
2. **Failure contract**: Raises `ValueError` if `risk_plan_status` is not `VALID` or if `direction` is not `LONG`/`SHORT`
3. **Identity**: Computes `intent_id = "intent-" + sha256[:16]` from canonical operational content + instance discriminator
4. **Fingerprint**: Computes `content_fingerprint = "fp-" + sha256[:16]` from canonical economic content only
5. **No external access**: Does NOT access candles, call any engine, read market data, or mutate inputs
6. **No timestamps generated**: `created_at`, `evaluation_timestamp`, `valid_until` are ALL caller-supplied

**Critical observation**: The factory does NOT accept a `TradePlan` object. It accepts individual field values. This is intentional (the 14.1/14.2 contract specifies "copies authoritative values from TradePlan verbatim"). The integration layer must extract fields from TradePlan and pass them to the factory.

## 8. Factory Ownership Analysis

### Candidate A: `TradePlanningEngine`

| Criterion | Assessment |
|-----------|------------|
| Single responsibility | **REJECTED** — TradePlanningEngine is a pure calculation engine (geometry → position size). Adding intent creation would conflate planning with operational intent. |
| Dependency direction | **REJECTED** — TradePlanningEngine lives in `intelligence/`. Intent model lives in `models/`. Engine already depends on models; adding intent creation would couple the calculation engine to the operational layer. |
| Frozen boundaries | **REJECTED** — TradePlanningEngine is a frozen Product Phase 4 component. |
| Separation from execution | **REJECTED** — the engine must remain broker-neutral and execution-free. |

### Candidate B: `DashboardAnalysisService`

| Criterion | Assessment |
|-----------|------------|
| Single responsibility | **REJECTED** — the service is already the orchestration boundary for analyze/scan/workstation/paper-trade/operations. Adding intent creation would further expand its scope. |
| Dependency direction | **ACCEPTABLE** — the service already depends on TradePlanningEngine and TradePlan. |
| Coupling to FastAPI/dashboard | **REJECTED** — the service is the dashboard coupling point. Intent creation should be decoupled from the HTTP/dashboard layer. |
| Testability | **REDUCED** — testing intent creation requires constructing a full service with provider, scanner, engines. |

### Candidate C: Dashboard service orchestration

| Criterion | Assessment |
|-----------|------------|
| Single responsibility | **REJECTED** — same issues as B but worse; orchestration code is not the right home for domain creation logic. |

### Candidate D: Dedicated `OperationalTradeIntentEngine` (or factory wrapper)

| Criterion | Assessment |
|-----------|------------|
| Single responsibility | **ACCEPTABLE** — a dedicated engine/factory whose sole purpose is TradePlan → OperationalTradeIntent transformation. |
| Dependency direction | **ACCEPTING** — lives in `engine/intelligence/`, depends on `engine/models/operational_trade_intent.py` and `engine/models/trade_plan.py`. Models ← intelligence direction preserved. |
| Frozen boundaries | **ACCEPTABLE** — does NOT modify TradePlanningEngine, PaperTradingEngine, or any frozen component. Adds a new file only. |
| Testability | **HIGH** — pure function, easily testable in isolation. |
| Separation from analysis | **YES** — does not call MarketScanner or any analysis engine. |
| Separation from simulation | **YES** — does not call PaperTradingEngine. |
| Separation from execution | **YES** — produces only an immutable data carrier. |
| Future authorization compatibility | **YES** — intent_id + content_fingerprint are designed for this. |
| Future broker compatibility | **YES** — broker-neutral by design. |

### Candidate E: Explicit caller / application layer

| Criterion | Assessment |
|-----------|------------|
| Assessment | **REJECTED** — too vague; "application layer" in this codebase is the dashboard, which has the same coupling problems as B/C. |

### Candidate F: Another existing architectural component

| Criterion | Assessment |
|-----------|------------|
| Assessment | **NO CANDIDATE** — no existing component has the right scope. PaperTradingOperations is paper-trading-specific. MarketScanner is analysis-specific. |

### RECOMMENDED OWNER: **Candidate D — Dedicated `OperationalTradeIntentEngine`**

**File**: `src/engine/intelligence/operational_trade_intent.py`

This follows the established pattern:
- `engine/intelligence/trade_planning.py` — TradePlanningEngine (one engine per capability)
- `engine/intelligence/paper_trading.py` — PaperTradingEngine
- `engine/intelligence/operational_trade_intent.py` — OperationalTradeIntentEngine (NEW)

The engine would provide a single public method:
```python
def create_intent(self, plan: TradePlan, *, created_at: datetime, evaluation_timestamp: datetime | None = None, valid_until: datetime | None = None, label: str = "", metadata: tuple[tuple[str, str], ...] = ()) -> OperationalTradeIntent
```

This wraps the existing `create_intent_from_plan()` factory with a TradePlan-object-aware interface, extracting fields from the TradePlan and delegating to the pure factory.

## 9. Creation-Trigger Analysis

### Evaluated Triggers

| Trigger | Recommended | Rationale |
|---------|-------------|-----------|
| Every valid TradePlan | **NO** | TradePlans are created for review/display (plan_trade) and paper trading. Most valid plans are never "operationally intended." |
| Only actionability `READY_FOR_REVIEW` | **NO (as a hard gate)** | `READY_FOR_REVIEW` is a presentation mirror for paper-trading eligibility. Intent creation should be decoupled from presentation state. |
| Explicit user request | **YES** | Intent should be created only when a human/system explicitly requests operational intent — not as a side effect of planning or scanning. |
| Explicit application command | **YES** | An application-level command/service explicitly creates intent from a VALID plan. |
| Authorization preparation | **FUTURE** | The future authorization layer may trigger intent creation. This checkpoint does not implement it. |
| Dashboard display | **NO** | Displaying a plan ≠ creating operational intent. |
| Paper-trade creation | **NO** | PaperTrade is a separate sibling path; paper-trade creation does NOT imply operational intent. |
| Separate operational-intent workflow | **YES** | Intent creation should be a separate, explicit workflow — not embedded in the planning or paper-trading flows. |

### Recommended Creation Trigger

**Explicit application-level command that:**
1. Takes an already-created VALID `TradePlan` (from any source: plan_trade, paper-trade prep, or other)
2. Validates the plan is VALID and directional
3. Calls the dedicated intent engine to create the intent
4. Returns/logs the intent for downstream use

**The trigger is NOT:**
- Automatic side effect of `TradePlanningEngine.plan()`
- Automatic side effect of `DashboardAnalysisService.plan_trade()`
- Automatic side effect of `PaperTradingEngine.create()`
- Coupled to `READY_FOR_REVIEW` actionability
- Coupled to paper-trade operations

### IMPORTANT: `READY_FOR_REVIEW` Has NO Relationship to Intent Creation

`READY_FOR_REVIEW` is a presentation mirror for the paper-trading eligibility gate. `RiskPlanStatus.VALID` is a risk-calculation status. Neither is authorization. Neither implies operational intent. The intent creation trigger must be a separate, explicit action.

## 10. Recommended Creation Boundary

```
┌─────────────────────────────────────────────────────────────────┐
│ EXISTING PATHS (unchanged)                                      │
│                                                                 │
│  analyze() → MarketScanResult → TradeCandidate                  │
│       ↓                                                         │
│  TradePlanningEngine.plan() → TradePlan                         │
│       ↓                    ↓                                    │
│  plan_trade()          create_paper_trade()                      │
│  ( TradePlanView )     ( PaperTrade )                           │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ NEW PATH (future checkpoint)                                    │
│                                                                 │
│  TradePlan (VALID)                                              │
│       ↓                                                         │
│  [EXPLICIT COMMAND / APPLICATION LAYER]                         │
│       ↓                                                         │
│  OperationalTradeIntentEngine.create_intent(plan, ...)           │
│       ↓                                                         │
│  OperationalTradeIntent                                         │
│       ↓                                                         │
│  [FUTURE: Execution Authorization]                              │
└─────────────────────────────────────────────────────────────────┘
```

**Key**: The intent is NOT created as part of any existing flow. It is created by a NEW, explicit, separate command that consumes an already-existing TradePlan.

## 11. Timestamp Responsibility

### Current Factory Behavior
- `created_at`: Caller-supplied, required, must be timezone-aware
- `evaluation_timestamp`: Caller-supplied, optional, must be timezone-aware if present
- `valid_until`: Caller-supplied, optional, must be timezone-aware if present and `>= created_at`

### Recommended Responsibility

| Timestamp | Supplied by | Rationale |
|-----------|-------------|-----------|
| `created_at` | The explicit command/application layer (caller of the intent engine) | The moment the intent is operationally created. NOT the plan creation time. Should be `datetime.now(UTC)` or an explicit deterministic timestamp. |
| `evaluation_timestamp` | Extracted from `TradePlan` → `TradePlan` does not currently carry an explicit evaluation_timestamp field. **RECOMMENDATION**: The intent engine should extract this from the `TradePlan.metadata` (if present) or from the analysis `evaluation_timestamp` passed by the caller. | The market data time the plan was based on. |
| `valid_until` | Caller-supplied, derived from configuration (policy) | NOT generated by the factory. The authorization policy should determine this. |

### Clock Abstraction
The current architecture has **NO clock abstraction**. The codebase uses explicit `datetime` parameters throughout (e.g., `reference_now`, `created_at`). This is the established pattern and should be followed: the intent engine should NOT read `datetime.now()` internally; the caller supplies all timestamps.

**Limitation**: Without a clock abstraction, the `created_at` timestamp is only as accurate as the caller's clock. This is acceptable for the current scope and matches the existing pattern.

## 12. Validity/Expiration Responsibility

- `valid_until` is a **policy-derived** value, NOT a calculation.
- The intent engine/factory does NOT generate `valid_until`.
- The future authorization policy should determine the validity window.
- The application/command layer that creates the intent is responsible for computing `valid_until` from policy configuration.
- The factory validates `valid_until >= created_at` (already implemented in `__post_init__`).

## 13. Point-in-Time Analysis

### Does intent creation introduce look-ahead?

**NO.** The factory:
- Accepts only TradePlan field values (already-computed data)
- Does NOT access candles
- Does NOT call MarketScanner
- Does NOT call OutcomeEvaluator
- Does NOT access HistoricalPipeline
- Does NOT retrieve fresh market data
- Does NOT invoke any analysis engine

### Does the proposed integration boundary preserve point-in-time safety?

**YES.** The recommended integration:
- Consumes an already-created TradePlan (which was created from already-computed analysis)
- The TradePlan itself is point-in-time safe (created from candles ≤ evaluation_time)
- Intent creation adds NO new candle access
- The `evaluation_timestamp` on the intent is the SAME as the analysis evaluation timestamp (no new data)

## 14. Mutation Analysis

### Verified: No mutations occur

| Object | Mutated by intent creation? | Evidence |
|--------|----------------------------|----------|
| TradePlan | **NO** | Factory accepts field values, not the object. Even the recommended `create_intent(plan, ...)` wrapper would only READ fields. |
| MarketScanResult | **NO** | Not accessed |
| PaperTrade | **NO** | Not accessed |
| No intent attached to TradePlan | **YES** | Intent holds `plan_id` by value only |
| No intent attached to PaperTrade | **YES** | No reference from PaperTrade to intent |
| No global registry | **YES** | No registry introduced |
| No cache | **YES** | No cache introduced |
| No hidden mutable state | **YES** | All objects immutable (frozen+slots) |

## 15. Duplicate Creation Analysis

### Identity Behavior

The `intent_id` is deterministic based on:
- ALL operational content fields (instrument, timeframe, direction, geometry, quantity, risk, status, decision, actionability)
- `created_at` (instance discriminator)
- `evaluation_timestamp` (instance discriminator)
- `label` (instance discriminator)
- `metadata` (instance discriminator)

### Duplicate Scenarios

| Scenario | Same intent_id? | Acceptable? |
|----------|-----------------|-------------|
| Same plan, same created_at, same metadata | **YES** | **ACCEPTABLE** — idempotent. Same inputs produce same intent. |
| Same plan, different created_at | **NO** | **ACCEPTABLE** — different creation moments are different intents. |
| Same plan, different label | **NO** | **ACCEPTABLE** — different operational context. |
| Same plan, different evaluation_timestamp | **NO** | **ACCEPTABLE** — different market evaluation times. |

### Is idempotency required?

**YES, by construction.** The deterministic identity ensures that repeated calls with identical inputs produce the same `intent_id`. This is a desirable property: it means the intent can be re-derived from the same plan without creating a conflicting duplicate.

### Is persistence required before integration?

**NO for this checkpoint.** This checkpoint is audit-only. The future implementation checkpoint should determine whether to persist intents immediately or defer persistence to the authorization layer. The existing pattern (PaperTradeStore, ExperimentRecord persistence) suggests persistence is likely desirable but not required for the integration boundary design.

## 16. Identity/Idempotency Analysis

The current identity design is sound for integration:

1. **`intent_id`** includes `created_at` as an instance discriminator → repeated creation at different times produces different identities (prevents accidental collapse of distinct operational decisions).
2. **`content_fingerprint`** excludes timestamps → the same economic content always produces the same fingerprint (allows content verification regardless of when created).
3. **Failure factory contract** enforces VALID + directional → non-VALID or non-directional plans cannot produce intents.

**No defect found in the 14.2 identity design that would block integration.**

### One Recommendation

The factory currently does NOT accept a TradePlan object directly (it accepts 22 individual field values). The integration wrapper (`OperationalTradeIntentEngine.create_intent`) should accept a TradePlan and extract fields, because:
- The 22-parameter signature is error-prone at call sites
- Field extraction is mechanical and should be encapsulated
- The wrapper can validate the TradePlan is VALID before delegating

This is a recommendation for the next checkpoint, NOT a defect in 14.2.

## 17. Persistence Analysis

### Current Persistence Architecture

| Artifact | Persisted? | Mechanism |
|----------|------------|-----------|
| TradePlan | **NO** | Transient calculation result |
| PaperTrade | **YES** | PaperTradeStore (filesystem, JSON) |
| OperationalTradeIntent | **NO** | Not yet integrated |

### Does intent need persistence before authorization?

**RECOMMENDATION**: Yes, intents should be persisted before or at the authorization boundary. Rationale:
- Authorization binds to `intent_id` and `content_fingerprint`
- The intent must be retrievable by `intent_id` for the authorization layer to verify it
- The existing pattern (schema-versioned JSON, atomic writes, safe-id) should be followed

**BUT**: Persistence implementation belongs in a LATER checkpoint (after integration boundary is established). This checkpoint only documents the requirement.

### Persistence Decision

- **Required**: Yes, for the future authorization workflow
- **File**: `src/engine/intelligence/operational_trade_intent_serialization.py` (per 14.1 contract)
- **Format**: Schema-versioned JSON (`OPERATIONAL_TRADE_INTENT_SCHEMA_VERSION = 1`)
- **Suffix**: `.intent` (distinct from `.json`, `.selection`, `.validation`, `.research.json`)
- **Location**: `OperationalTradeIntentEngine` should NOT handle persistence directly; a dedicated serializer should be used by the application/command layer.

## 18. Presentation Analysis

### Should dashboard presentation expose intent fields?

**YES, but with caution.**

| Field | Safe to display? | Rationale |
|-------|-----------------|-----------|
| `intent_id` | **YES** | Operational identity; not a recommendation |
| `content_fingerprint` | **YES** | Cryptographic proof; displayable |
| `valid_until` | **YES** | Policy metadata; displayable |
| `created_at` | **YES** | Audit metadata; displayable |
| `risk_plan_status` | **YES** | Already displayed via TradePlanView |
| `plan_id` | **YES** | Provenance reference; displayable |

### Does displaying intent imply authorization?

**NOT if presented correctly.** Displaying an `intent_id` is similar to displaying a `plan_id` or `paper_trade_id` — it is an operational reference, not an authorization claim. The presentation MUST:
- Clearly label the intent as "operational reference" not "authorization"
- Include the standard disclaimer that descriptive outputs are not trading recommendations
- NOT use language like "approved," "authorized," "cleared," "ready to execute"

### Recommended Presentation Boundary

The intent should be surfaced as a SEPARATE section/block in the trade-plan view, NOT collapsed into the existing TradePlanView. A dedicated `OperationalTradeIntentView` presentation model should be created in the future implementation checkpoint.

## 19. Authorization Compatibility

### Future authorization binds to:
- `intent_id` — to identify the specific intent being authorized
- `content_fingerprint` — to verify the intent content has not changed since creation

### Does the proposed integration preserve this?

**YES.**

1. `intent_id` is deterministic and content-bound → authorization can verify the intent identity
2. `content_fingerprint` captures economic content only (excludes operational metadata) → authorization can verify the economic intent is unchanged
3. `valid_until` is policy-derived → authorization can check expiry
4. No authorization fields on the model → clean separation

### Explicit Statement

> `OperationalTradeIntent` ≠ Execution Authorization. The intent is an immutable data carrier that MAY be presented to a future authorization layer. The intent contains NO authorization fields, NO approval logic, NO kill switch, NO permission logic.

## 20. Execution Compatibility

### Future execution architecture:
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
```

### Does the proposed integration keep this chain clean?

**YES.**

1. The intent is created BEFORE authorization — it is the input to authorization, not the output
2. The intent is immutable — authorization cannot modify it
3. The intent has no execution fields — no command_id, fill_price, position_id
4. The intent is broker-neutral — no broker symbol, exchange, routing
5. The intent creation does NOT trigger any execution workflow

## 21. Paper-Trading Compatibility

### Confirmed Independent Paths

```
TradePlan
    ├────────────→ PaperTrade (existing, unchanged)
    │
    └────────────→ OperationalTradeIntent (new, separate)
```

| Requirement | Status |
|-------------|--------|
| PaperTrade does NOT depend on OperationalTradeIntent | **CONFIRMED** — PaperTradingEngine accepts `plan` and `plan_id`, no intent reference |
| OperationalTradeIntent does NOT depend on PaperTrade | **CONFIRMED** — intent factory accepts TradePlan fields, no PaperTrade reference |
| Paper-trade outcomes do NOT create/modify intent | **CONFIRMED** — no path from PaperTrade to intent |
| No feedback loops | **CONFIRMED** — intent is downstream of TradePlan, PaperTrade is downstream of TradePlan; they are siblings |

## 22. Trading-Semantics Analysis

### Search Results for Forbidden Semantics

Searched `src/engine/models/operational_trade_intent.py` for: BUY, SELL, ENTER, EXIT, HOLD, EXECUTE, ORDER, FILLED, BROKER, POSITION.

| Term | Found in executable code? | Found in docstrings/comments? |
|------|--------------------------|-------------------------------|
| BUY | NO | NO |
| SELL | NO | NO |
| ENTER | NO | NO |
| EXIT | NO | NO |
| HOLD | NO | NO |
| EXECUTE | NO | NO |
| ORDER | NO | NO |
| FILLED | NO | NO |
| BROKER | NO | YES (only in "NOT a broker" negative documentation) |
| POSITION | NO | YES (only in "NOT a position" negative documentation) |

**Verdict**: No executable trading semantics contamination. All forbidden terms appear only in negative documentation ("NOT a broker", "NOT a position") or not at all.

## 23. Dependency Direction

### Current (Frozen)
```
Market Data → Analysis → MarketScanResult → Trade Planning → TradePlan
                                                                    ├→ PaperTrade
                                                                    └→ [FUTURE: OperationalTradeIntent]
```

### After Integration (Recommended)
```
models ← intelligence ← dashboard

engine/models/trade_plan.py         (frozen, Product Phase 4)
engine/models/operational_trade_intent.py  (frozen, Checkpoint 14.2)
engine/intelligence/operational_trade_intent.py  (NEW, intent engine)
dashboard/services.py               (frozen, orchestration — may call intent engine in future)
```

**No backward arrows. No execution component points into analysis. No paper-trading component points into operational intent.**

## 24. API/Application Boundary

### Where should intent creation belong?

| Layer | Assessment |
|-------|------------|
| Inside API route | **REJECTED** — routes should be thin; creation logic belongs in a service/engine |
| Inside dashboard service | **REJECTED** — too coupled to dashboard; intent is a domain concern |
| Inside engine | **ACCEPTABLE** — but only the model/factory layer; engine/intelligence for the creation wrapper |
| Inside an application command/service | **RECOMMENDED** — a dedicated application command that orchestrates: load plan → create intent → persist intent → return intent view |
| Outside the current dashboard entirely | **ACCEPTABLE** — intent creation may live in a separate CLI/operator tool |

### Recommended Boundary

```
┌──────────────────────────────────────────────────────────┐
│ Future: Application Command / Operator Tool              │
│ (separate from dashboard routes)                         │
│                                                          │
│  1. Load or create a VALID TradePlan                     │
│  2. Call OperationalTradeIntentEngine.create_intent()    │
│  3. Persist intent via serializer                        │
│  4. Return intent for authorization workflow             │
└──────────────────────────────────────────────────────────┘
         ↓ calls
┌──────────────────────────────────────────────────────────┐
│ src/engine/intelligence/operational_trade_intent.py      │
│   OperationalTradeIntentEngine                           │
│     .create_intent(plan, created_at, ...)                │
│         ↓ delegates to                                   │
│   create_intent_from_plan(...)  [pure factory, 14.2]    │
└──────────────────────────────────────────────────────────┘
```

The dashboard should NOT create intents in this architecture. Intent creation is an operational workflow, not a presentation concern. The dashboard may DISPLAY an existing intent (in a future checkpoint) but should NOT create one.

## 25. Required Future Integration Changes

For the NEXT implementation checkpoint (14.4 or equivalent):

### New Files
1. `src/engine/intelligence/operational_trade_intent.py` — `OperationalTradeIntentEngine` with `create_intent(plan, ...)` method that wraps the pure factory
2. `src/engine/intelligence/operational_trade_intent_serialization.py` — `serialize_intent`/`deserialize_intent`/`parse_intent_header` (schema-versioned JSON)
3. `tests/test_operational_trade_intent_integration.py` — integration tests

### Modified Files (future checkpoint)
- NONE of the frozen files need modification for the integration boundary.
- `src/dashboard/services.py` — may gain an `create_operational_intent()` method in a LATER checkpoint (after the engine is stable and the authorization boundary is clearer).
- `src/dashboard/views.py` — may gain an `OperationalTradeIntentView` presentation model in a LATER checkpoint.

### NOT Modified (confirmed)
- `src/engine/models/trade_plan.py` — frozen, no changes
- `src/engine/intelligence/trade_planning.py` — frozen, no changes
- `src/engine/intelligence/paper_trading.py` — frozen, no changes
- `src/dashboard/paper_trade_operations.py` — frozen, no changes
- `src/engine/models/operational_trade_intent.py` — frozen (14.2), no changes needed for integration boundary

## 26. Required Future Tests

### Integration Tests (next checkpoint)
1. **Factory wrapper tests**: `OperationalTradeIntentEngine.create_intent(plan, ...)` correctly extracts fields from TradePlan and delegates to `create_intent_from_plan()`
2. **VALID-only rejection**: Non-VALID TradePlan raises ValueError
3. **Directional-only rejection**: Non-directional TradePlan raises ValueError
4. **Field preservation tests**: All 14 MUST-PRESERVE fields copied verbatim
5. **Excluded fields tests**: `account_capital`, `risk_percent` NOT on intent
6. **Forbidden fields tests**: `target_2`, `target_2_supported` NOT on intent
7. **Timestamp tests**: `created_at`, `evaluation_timestamp`, `valid_until` correctly propagated
8. **Identity tests**: Same plan + same metadata → same intent_id; different created_at → different intent_id
9. **Fingerprint tests**: Same economic content → same fingerprint regardless of timestamps
10. **Immutability tests**: Result is frozen+slots, no mutation possible
11. **Point-in-time tests**: Intent creation does not access candles/engines
12. **Duplicate/idempotency tests**: Repeated creation from same plan produces same intent_id
13. **Serialization round-trip tests**: intent → JSON → intent preserves all fields
14. **Authorization handoff tests** (later checkpoint): intent_id + content_fingerprint survive for authorization binding

### Regression Tests (must remain unchanged)
- `tests/test_operational_trade_intent.py` — 125 tests
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests
- All other tests — 4974 total passed

## 27. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Accidental coupling of intent creation to paper-trading flow | **MEDIUM** | Explicit architectural rule: intent creation is a SEPARATE workflow, not triggered by paper-trade creation |
| Dashboard displaying intent_id implying authorization | **LOW** | Clear labeling + standard disclaimer; intent_id is an operational reference, not authorization |
| Timestamp source ambiguity (who supplies created_at) | **LOW** | Factory requires caller-supplied timestamps; no internal clock reads |
| Duplicate intents from repeated creation | **LOW** | Deterministic identity ensures same inputs → same intent_id; persistence layer can deduplicate |
| Integration modifying frozen components | **HIGH** | This audit explicitly recommends NO modifications to frozen files; new files only |

## 28. Limitations

1. **No clock abstraction**: Timestamps are caller-supplied; the system relies on the caller's clock accuracy. Matches existing pattern.
2. **No persistence yet**: This audit documents the persistence requirement but does not implement it.
3. **No authorization layer**: The intent is designed for future authorization but the authorization layer does not exist yet.
4. **No dashboard integration**: The dashboard does not create or display intents yet; this is deferred to a later checkpoint.
5. **TradePlan has no evaluation_timestamp field**: The intent's `evaluation_timestamp` must be supplied by the caller (extracted from the analysis context, not from TradePlan itself).

## 29. Implementation Decision

**NO IMPLEMENTATION CHANGES.** This checkpoint is audit-only.

The audit establishes:
1. **Who creates**: A dedicated `OperationalTradeIntentEngine` in `src/engine/intelligence/operational_trade_intent.py`
2. **From what artifact**: An already-created VALID `TradePlan` (consumed by value, field extraction)
3. **At what boundary**: A separate, explicit application-level command/workflow — NOT embedded in planning, scanning, or paper-trading flows
4. **Under what trigger**: Explicit operational intent request — NOT automatic side effect of any existing flow
5. **Who supplies timestamps**: The caller (application/command layer); the factory generates none
6. **Duplicate handling**: Deterministic identity ensures idempotency by construction
7. **Persistence**: Required for future authorization workflow; implementation deferred
8. **Authorization consumption**: `intent_id` + `content_fingerprint` are designed for authorization binding
9. **PaperTrade independence**: TradePlan → PaperTrade remains unchanged; intent is a sibling path
10. **Files to modify in next checkpoint**: New files only — `src/engine/intelligence/operational_trade_intent.py` (engine wrapper), `src/engine/intelligence/operational_trade_intent_serialization.py` (persistence), `tests/test_operational_trade_intent_integration.py` (integration tests)

## 30. Final Verdict

**PASS WITH LIMITATIONS**

The audit establishes a clear, architecturally sound integration boundary for `OperationalTradeIntent`:

- The existing `create_intent_from_plan()` factory (14.2) is correct and requires NO modification.
- The integration should be a NEW dedicated engine wrapper, NOT modifications to existing frozen components.
- Intent creation should be an EXPLICIT, SEPARATE workflow — not a side effect of planning or paper trading.
- `READY_FOR_REVIEW` and `RiskPlanStatus.VALID` have NO relationship to intent creation.
- Paper trading remains completely independent.
- The future authorization layer can bind to `intent_id` + `content_fingerprint` without modification.

**Limitations**: No clock abstraction, no persistence implementation, no authorization layer, no dashboard integration in this checkpoint. All are deferred to future checkpoints by design.

---

## Appendix A: TradePlan Consumer Trace (Complete)

| # | File | Function/Class | Reads TradePlan? | Creates PaperTrade? | Creates Intent? | Should Create Intent? |
|---|------|----------------|-------------------|---------------------|-----------------|----------------------|
| 1 | `dashboard/services.py` | `plan_trade()` | YES (via engine) | NO | NO | **NO** — review path |
| 2 | `dashboard/services.py` | `create_paper_trade()` | YES (via engine) | YES | NO | **NO** — paper-trade path |
| 3 | `dashboard/services.py` | `run_paper_trading_cycle()` | YES (indirect) | YES | NO | **NO** — operations path |
| 4 | `dashboard/services.py` | `_to_trade_plan_view()` | YES (projection) | NO | NO | **NO** — pure projection |
| 5 | `engine/intelligence/paper_trading.py` | `PaperTradingEngine.create()` | YES (parameter) | YES | NO | **NO** — simulation engine |
| 6 | `engine/reporting/trade_planning.py` | `TradePlanFormatter.format()` | YES (read-only) | NO | NO | **NO** — reporting |
| 7 | `engine/intelligence/trade_planning.py` | `TradePlanningEngine.plan()` | NO (creates) | NO | NO | **NO** — planning engine |

## Appendix B: Existing Factory Input Mapping

The `create_intent_from_plan()` factory accepts these parameters, mapped from TradePlan fields:

| Factory Parameter | Source TradePlan Field | Notes |
|-------------------|------------------------|-------|
| `plan_id` | `plan_id` | Provenance |
| `instrument` | `instrument` | |
| `timeframe` | `timeframe` | |
| `direction` | `direction` | Must be LONG/SHORT |
| `entry` | `entry` | |
| `stop` | `stop` | |
| `target_1` | `target_1` | |
| `engine_risk_distance` | `engine_risk_distance` | |
| `engine_reward_distance` | `engine_reward_distance` | |
| `engine_risk_reward_ratio` | `engine_risk_reward_ratio` | |
| `quantity` | `quantity` | |
| `planned_risk` | `planned_risk` | |
| `maximum_risk` | `maximum_risk` | |
| `risk_plan_status` | `risk_plan_status` | Must be VALID |
| `existing_decision` | `existing_decision` | |
| `actionability` | `actionability` | |
| `warnings` | `warnings` | |
| `rationale` | `rationale` | |
| `label` | `label` | |
| `metadata` | `metadata` | |
| `created_at` | CALLER-SUPPLIED | NOT from TradePlan |
| `evaluation_timestamp` | CALLER-SUPPLIED | NOT from TradePlan |
| `valid_until` | CALLER-SUPPLIED | NOT from TradePlan |

**Note**: 3 of 22 parameters (`created_at`, `evaluation_timestamp`, `valid_until`) are NOT from TradePlan. They are operational metadata supplied by the caller.
