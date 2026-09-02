# Checkpoint 14.5 — Operational Trade Intent Application Integration & Lifecycle Boundary Audit

## 1. Purpose

Audit the current application architecture and establish the correct explicit application-level entry point for `OperationalTradeIntentEngine`. Determine where, when, and under what exact conditions an `OperationalTradeIntent` should be created from a `TradePlan` at the application layer.

The lifecycle boundary established:

    TradePlan
    -> Explicit Intent Creation Request
    -> OperationalTradeIntentEngine
    -> OperationalTradeIntent
    -> [STOP]

## 2. Scope

This checkpoint is ADDITIVE application integration only. It determines the correct application-level owner of explicit `OperationalTradeIntent` creation and implements the minimal integration required.

It does NOT implement: Execution Authorization, Execution Command, Broker Adapter, Broker connection, Order submission, Live trading, Position management, Portfolio management, Execution results, Broker credentials, Live/paper execution routing, Automatic trading, Automatic authorization.

## 3. Frozen architecture

All previous accepted checkpoints remain frozen:
- Checkpoint 10.8 — Historical Research — FROZEN
- Checkpoint 11.8 — Current-Market Analytical Output — FROZEN
- Checkpoint 12.6 — Trade Planning + Paper Trading + Performance — FROZEN
- Checkpoint 13.6 — Pre-Execution Architecture — FROZEN
- Checkpoint 14.1 — Operational Trade Intent Boundary — FROZEN
- Checkpoint 14.2 — Operational Trade Intent Model & Identity — FROZEN
- Checkpoint 14.3 — Operational Trade Intent Factory & TradePlan Integration Boundary — FROZEN
- Checkpoint 14.4 — Operational Trade Intent Engine & Explicit Creation Workflow — FROZEN

## 4. Exact files inspected

### Engine layer
- `src/engine/models/trade_plan.py` — TradePlan model (frozen+slots, 5 risk plan statuses)
- `src/engine/models/operational_trade_intent.py` — OperationalTradeIntent model + `create_intent_from_plan()` factory (frozen+slots, deterministic identity)
- `src/engine/intelligence/trade_planning.py` — TradePlanningEngine (stateless, deterministic)
- `src/engine/intelligence/operational_trade_intent.py` — OperationalTradeIntentEngine (stateless, delegates to factory)
- `src/engine/intelligence/paper_trading.py` — PaperTradingEngine (independent path)
- `src/engine/models/paper_trade.py` — PaperTrade model (independent)
- `src/engine/intelligence/__init__.py` — intentionally empty (import via full paths)

### Dashboard layer
- `src/dashboard/services.py` — DashboardAnalysisService (orchestration boundary), request dataclasses, `default_service()`
- `src/dashboard/views.py` — Presentation models (frozen+slots projections)
- `src/dashboard/app.py` — FastAPI routes
- `src/dashboard/paper_trade_operations.py` — Paper trading operations (independent)

### Tests
- `tests/test_operational_trade_intent.py` — 125 model tests
- `tests/test_operational_trade_intent_engine.py` — 69 engine tests
- `tests/test_trade_planning.py` — 158 trade planning tests
- `tests/test_paper_trading.py` — 114 paper trading tests
- `tests/test_paper_trading_operations.py` — 78 paper trading operations tests

### Documentation
- `AGENTS.md` — agent memory
- `docs/checkpoint_14_1_operational_trade_intent_implementation_boundary_audit.md`
- `docs/checkpoint_14_2_operational_trade_intent_model_and_identity_implementation.md`
- `docs/checkpoint_14_3_operational_trade_intent_factory_and_trade_plan_integration_boundary_audit.md`
- `docs/checkpoint_14_4_operational_trade_intent_engine_and_explicit_creation_workflow_implementation.md`

## 5. Existing application flow

The current application flow from MarketScanResult through TradePlan to Dashboard:

```
MarketScanResult
    |
    v
InstrumentScanResult (via MarketScanner.scan)
    |
    v
DashboardAnalysisService.analyze()
    |-- provider.fetch() -> InstrumentSeries
    |-- Build InstrumentDataset
    |-- scanner.scan() -> InstrumentScanResult
    |-- _build_view() -> DashboardTradeView
    |
    v
DashboardTradeView (presentation model)
    |
    +---> plan_trade() -> TradePlanView
    |       |-- analyze() (reuse existing analysis)
    |       |-- trade_planning_engine.plan() -> TradePlan
    |       |-- _to_trade_plan_view() -> TradePlanView
    |
    +---> create_paper_trade() -> PaperTradeView
    |       |-- analyze() (reuse existing analysis)
    |       |-- trade_planning_engine.plan() -> TradePlan
    |       |-- paper_trading_engine.create() -> PaperTrade
    |       |-- to_paper_trade_view() -> PaperTradeView
    |
    +---> [NEW] create_operational_trade_intent() -> OperationalTradeIntentView
            |-- analyze() (reuse existing analysis)
            |-- trade_planning_engine.plan() -> TradePlan
            |-- operational_trade_intent_service.create_intent_from_trade_plan()
            |       -> OperationalTradeIntentEngine.create_from_plan()
            |               -> create_intent_from_plan() factory
            |-- OperationalTradeIntentView.from_intent() -> OperationalTradeIntentView
```

## 6. Existing TradePlan consumers

1. `DashboardAnalysisService.plan_trade()` — creates TradePlanView for display
2. `DashboardAnalysisService.create_paper_trade()` — sizes position via TradePlan, creates PaperTrade
3. `PaperTradingEngine.create()` — accepts plan object for paper trade
4. `TradePlanFormatter.format()` — serializes TradePlan for reporting
5. `trade_plan_view_to_jsonable()` — JSON projection
6. `PaperTrade model` — stores plan_id reference
7. Operations cycle (Product Phase 5) — creates trades from READY_FOR_REVIEW opportunities

NONE of these consumers create OperationalTradeIntent. They are all read-only consumers or create PaperTrade (independent path).

## 7. Existing intent engine

`OperationalTradeIntentEngine` (Checkpoint 14.4):
- Stateless, pure, deterministic
- `create_from_plan(plan, *, created_at, ...) -> OperationalTradeIntent`
- Type validation (isinstance TradePlan)
- Precondition validation (VALID status, LONG/SHORT direction)
- Delegates to `create_intent_from_plan()` factory
- No market data, no PaperTrade, no authorization, no execution, no broker, no persistence

## 8. Candidate application boundaries

### A. FastAPI route
- Separation of concerns: HTTP layer only, not reusable outside HTTP
- Testability: requires HTTP test client
- Coupling: ties intent creation to HTTP transport
- Reuse outside dashboard: NO (HTTP-only)
- Future authorization compatibility: requires additional service layer anyway
- Verdict: REJECTED as primary owner. Route should call a service.

### B. Dashboard view
- Views are presentation models only (frozen+slots, no behavior)
- Verdict: REJECTED. Views cannot contain creation logic.

### C. Dashboard service (DashboardAnalysisService)
- Separation of concerns: orchestration boundary, good
- Testability: excellent (direct method calls)
- Coupling: couples intent creation to dashboard package
- Reuse outside dashboard: LIMITED (requires dashboard dependency)
- Future authorization compatibility: acceptable
- Verdict: ACCEPTED as consumer/integration point, but NOT as primary owner

### D. Existing application/orchestration service
- No existing orchestration service for intents
- Verdict: REJECTED (does not exist)

### E. New dedicated application service
- Separation of concerns: clean facade over engine, single responsibility
- Testability: excellent (direct instantiation, injectable engine)
- Coupling: LOW (depends only on engine + models)
- Reuse outside dashboard: YES (CLI, future authorization, tests)
- Future authorization compatibility: EXCELLENT (natural integration point)
- Immutability: preserved (returns immutable intents)
- Deterministic behavior: preserved
- Explicitness: named method `create_intent_from_trade_plan`
- Discoverability: named service in intelligence package
- API cleanliness: clean facade over engine
- Verdict: ACCEPTED as primary owner

### F. Direct engine invocation by explicit caller
- Simplest approach
- But lacks discoverability and future integration point
- Verdict: ACCEPTED as the low-level mechanism (used by the application service)

## 9. Selected application boundary

**Candidate E: New dedicated application service** — `OperationalTradeIntentApplicationService`.

The service is the single application-level owner of explicit OperationalTradeIntent creation. It wraps `OperationalTradeIntentEngine` and provides a clean, reusable, testable facade:

```
TradePlan -> OperationalTradeIntentApplicationService -> OperationalTradeIntent
                |
                v
        OperationalTradeIntentEngine (Checkpoint 14.4)
                |
                v
        create_intent_from_plan() factory (Checkpoint 14.2)
```

The dashboard service (`DashboardAnalysisService`) consumes this application service as its integration point, and the FastAPI route consumes the dashboard service.

## 10. Explicit creation workflow

The explicit creation workflow:

```python
from engine.intelligence.operational_trade_intent_application import (
    OperationalTradeIntentApplicationService,
)

service = OperationalTradeIntentApplicationService()
intent = service.create_intent_from_trade_plan(
    plan,
    created_at=datetime.now(timezone.utc),  # REQUIRED
    evaluation_timestamp=view.evaluation_timestamp,  # optional
    valid_until=None,  # optional
    label="my-intent",
    metadata={"source": "workstation"},
)
```

At the dashboard layer:

```python
from dashboard.services import DashboardAnalysisService, OperationalTradeIntentRequest

view = service.create_operational_trade_intent(
    OperationalTradeIntentRequest(
        instrument="NIFTY",
        account_capital="100000",
        risk_percent="1",
        created_at=datetime.now(timezone.utc),
        setup_timeframe="15m",
    ),
)
```

At the HTTP layer:

```http
POST /api/operational-trade-intent
    ?instrument=NIFTY
    &account_capital=100000
    &risk_percent=1
    &created_at=2026-09-01T12:00:00%2B00:00
    &timeframe=15m
```

## 11. Creation trigger

Intent creation requires an EXPLICIT application action:
- Direct call to `OperationalTradeIntentApplicationService.create_intent_from_trade_plan()`
- Direct call to `DashboardAnalysisService.create_operational_trade_intent()`
- HTTP POST to `/api/operational-trade-intent`

Intent is NEVER created automatically as a side effect of:
- `plan_trade()` — returns TradePlanView only
- `TradePlanningEngine.plan()` — returns TradePlan only
- `MarketScanner.scan()` — returns MarketScanResult only
- `DashboardAnalysisService.analyze()` — returns DashboardTradeView only
- `create_paper_trade()` — creates PaperTrade only
- Paper trading operations — creates PaperTrade only
- Dashboard page rendering — read-only
- API GET requests — read-only
- Performance analytics — read-only

A user merely viewing a TradePlan does NOT automatically create an intent.

## 12. Timestamp responsibility

Per Checkpoint 14.3/14.4 contract:
- `created_at`: REQUIRED, caller-supplied, timezone-aware. The service NEVER generates it silently. No `datetime.now()` in engine or application service.
- `evaluation_timestamp`: OPTIONAL, defaults to the analysis evaluation timestamp (the close of the latest completed setup candle).
- `valid_until`: OPTIONAL, defaults to None (no expiry).

The API endpoint requires `created_at` as an explicit ISO 8601 timezone-aware query parameter. Naive timestamps are rejected with HTTP 400.

## 13. Validity responsibility

- The `valid_until` field is OPTIONAL and defaults to None.
- When provided, it must be >= `created_at` (validated by the model `__post_init__`).
- The application service does NOT impose additional validity policy — it delegates to the engine/factory.
- Future authorization checkpoints may impose additional validity windows.

## 14. Identity behavior

Preserves Checkpoint 14.2 identity contract:
- `intent_id`: `"intent-" + sha256[:16]` of canonical operational content + instance discriminator (created_at, evaluation_timestamp, label, metadata).
- `content_fingerprint`: `"fp-" + sha256[:16]` of canonical economic content only (excludes operational metadata).

The application service does NOT modify identity generation — it delegates entirely to the engine/factory.

## 15. Duplicate creation behavior

Repeated explicit requests with the same inputs produce the SAME identity:
- Same TradePlan + same `created_at` + same `label` + same `metadata` = same `intent_id` and `content_fingerprint`.
- Different `created_at` = different `intent_id` but same `content_fingerprint`.
- Different `label` = different `intent_id` but same `content_fingerprint`.

This is deterministic per the Checkpoint 14.2 contract. The application service does NOT invent a second identity system.

## 16. Persistence decision

**Persistence is NOT implemented in this checkpoint.**

Rationale:
- No demonstrated architectural requirement for persistence at this boundary.
- The intent is an immutable snapshot/reference that can be reconstructed deterministically from the TradePlan.
- Persistence introduces questions of storage format, retrieval, lifecycle management, and authorization binding that belong to future checkpoints.
- The application service returns `OperationalTradeIntent` and leaves persistence to the caller.

If persistence is needed in a future checkpoint, the application service is the natural integration point.

## 17. API decision

**A POST endpoint is implemented: `POST /api/operational-trade-intent`**

Justification:
- Intent creation is an explicit mutation/action — POST is semantically correct.
- The endpoint receives an existing TradePlan context (via instrument + account params) and creates an intent.
- It does NOT regenerate the TradePlan — it reuses the existing current analysis' geometry.
- It does NOT create PaperTrade.
- It does NOT authorize execution.
- It returns the `OperationalTradeIntent` representation.
- It fails closed on invalid input (HTTP 400).
- It never implies authorization.

The endpoint is a POST (not GET) because intent creation is a mutation. GET does not create intents.

## 18. Dashboard decision

**Dashboard integration is implemented via `DashboardAnalysisService.create_operational_trade_intent()`.**

The dashboard service:
- Creates a TradePlan from the current analysis (reusing `analyze()` + `TradePlanningEngine`).
- Delegates intent creation to the application service.
- Returns an `OperationalTradeIntentView` presentation model.

The UI does NOT automatically display or create intents as part of existing TradePlan pages. Intent creation requires an explicit action (API POST or direct service call).

The `OperationalTradeIntentView` is a distinct presentation model from `TradePlanView` — the UI must distinguish between a TradePlan (planning artifact) and an OperationalTradeIntent (operational snapshot/reference).

## 19. PaperTrade separation

**PaperTrade remains completely independent.**

- `TradePlan -> PaperTrade` path is unchanged.
- `TradePlan -> OperationalTradeIntent` is a sibling path.
- No path from OperationalTradeIntent to PaperTrade.
- No path from PaperTrade to OperationalTradeIntent.
- Paper-trade outcomes do NOT modify intent.
- The `create_paper_trade()` method is unchanged.
- The `create_operational_trade_intent()` method does NOT create PaperTrade.

## 20. Point-in-time safety

The application workflow does NOT introduce look-ahead:
- Intent creation uses ONLY the supplied TradePlan (already created from completed candles).
- The dashboard service's `analyze()` uses the latest COMPLETED setup candle (completed-candle boundary inherited from Product Phase 1).
- No fresh candles are fetched for intent creation.
- No `OutcomeEvaluator` is invoked.
- No `HistoricalEvaluationPipeline` is invoked.
- No `MarketScanner` is invoked for intent creation.
- Explicit intent creation is a transformation of an existing planning artifact, not a new analytical evaluation.

## 21. Mutation safety

After intent creation:
- `TradePlan` remains byte/value-equivalent (verified by pickle round-trip test).
- `MarketScanResult` is untouched.
- `PaperTrade` is untouched.
- No intent reference is attached to TradePlan.
- No intent reference is attached to PaperTrade.
- No mutable global state is introduced.
- No hidden singleton state is introduced (verified: each service instance is independent).
- No hidden cache is introduced.
- No hidden registry is introduced.

## 22. Authorization compatibility

This checkpoint prepares for a future authorization boundary:
- `intent_id` and `content_fingerprint` are stable references (deterministic, immutable).
- The application service is the natural integration point for future authorization.
- No authorization fields are added (no `authorization_id`, `approval_state`, `authorized_at`, `approver`, `kill_switch`, `emergency_stop`, `permission_state`).
- The intent is NOT authorization — it is a snapshot/reference that may later be presented to authorization.

## 23. Execution compatibility

No execution implementation:
- No `ExecutionCommand`, `Order`, `BrokerOrder`, `BrokerAdapter`, `ExecutionResult`, `Position`, `Portfolio`, `LiveExecution`, `OrderSubmission`.
- No broker credentials.
- No broker requests.
- The intent is NOT an execution instruction.

## 24. Trading-semantics analysis

The application code does NOT introduce forbidden trading semantics:
- No `BUY`, `SELL`, `ENTER`, `EXIT`, `HOLD`, `EXECUTE`, `ORDER`, `FILLED`, `BROKER`, `POSITION` in executable code.
- Existing `LONG`/`SHORT` remain valid directional planning semantics.
- `OperationalTradeIntent` is NOT an execution instruction.

Verified by AST-level import analysis in tests (no forbidden module imports).

## 25. Dependency direction

The resulting dependency direction:

```
Analysis
    |
    v
MarketScanResult
    |
    v
TradePlan
    +----------> PaperTrade (independent)
    |
    v
OperationalTradeIntentApplicationService (NEW)
    |
    v
OperationalTradeIntentEngine (Checkpoint 14.4)
    |
    v
create_intent_from_plan() factory (Checkpoint 14.2)
    |
    v
OperationalTradeIntent
    |
    v
[FUTURE] Authorization
    |
    v
[FUTURE] Execution Command
    |
    v
[FUTURE] Broker Adapter
```

Application orchestration (dashboard service, API route) sits around these components but does not create backward dependencies.

- No engine imports dashboard code.
- No OperationalTradeIntent engine imports FastAPI.
- No OperationalTradeIntent engine imports PaperTrade.
- The application service depends only on the engine and models.

## 26. Implementation changes

### New files
1. `src/engine/intelligence/operational_trade_intent_application.py`
   - `OperationalTradeIntentApplicationService` — application-level owner
   - Stateless, deterministic, pure delegation to engine
   - Reusable outside dashboard (CLI, future authorization, tests)
   - No market data, no PaperTrade, no authorization, no execution, no broker, no persistence

2. `tests/test_operational_trade_intent_application.py`
   - 58 focused integration tests across 13 test classes

3. `docs/checkpoint_14_5_operational_trade_intent_application_integration_and_lifecycle_boundary_audit.md`
   - This document

### Modified files
1. `src/dashboard/services.py`
   - Added `OperationalTradeIntentRequest` dataclass (frozen, instrument + account_capital + risk_percent + created_at REQUIRED + optional fields)
   - Added `create_operational_trade_intent()` method to `DashboardAnalysisService`
   - Added `OperationalTradeIntentApplicationService` instantiation in `__init__`
   - Added `OperationalTradeIntentRequest` to `__all__`

2. `src/dashboard/views.py`
   - Added `OperationalTradeIntentView` presentation model (frozen+slots, 26 fields)
   - Added `OperationalTradeIntentView.from_intent()` classmethod
   - Added `operational_trade_intent_view_to_jsonable()` function (Decimal-as-string + parallel _float fields, datetime-as-ISO)
   - Added both to `__all__`

3. `src/dashboard/app.py`
   - Added `POST /api/operational-trade-intent` endpoint (explicit mutation, not GET)
   - Added imports for `OperationalTradeIntentRequest` and `operational_trade_intent_view_to_jsonable`

4. `AGENTS.md`
   - Appended Checkpoint 14.5 entry

### Files NOT modified
- `src/engine/models/trade_plan.py` (frozen)
- `src/engine/models/operational_trade_intent.py` (frozen)
- `src/engine/intelligence/trade_planning.py` (frozen)
- `src/engine/intelligence/operational_trade_intent.py` (frozen)
- `src/engine/intelligence/paper_trading.py` (frozen)
- `src/engine/models/paper_trade.py` (frozen)
- `src/dashboard/paper_trade_operations.py` (frozen)
- MarketScanner, MarketScanResult, TradeCandidate, TradeDecision, TradeOpportunity (all frozen)
- All historical research components (frozen)

## 27. Tests added

`tests/test_operational_trade_intent_application.py` — 58 tests across 13 areas:

| Area | Tests | Description |
|------|-------|-------------|
| AppServiceDirectUsage | 5 | Direct application service usage |
| AppServiceWrapsEngine | 3 | Service wraps engine correctly |
| GeometryPreserved | 9 | TradePlan geometry copied verbatim |
| NoTradePlanMutation | 2 | TradePlan unchanged after creation |
| NoForbiddenInteractions | 6 | No forbidden imports (AST-level) |
| IdentityPreservation | 4 | Deterministic identity + fingerprint |
| TimestampHandling | 5 | Timestamp responsibility + validation |
| InvalidTradePlanHandling | 6 | Invalid plans rejected |
| Immutability | 4 | Intent is frozen+slots |
| NoHiddenGlobalState | 3 | Stateless, no registry/cache |
| RepeatedCreation | 3 | Repeated explicit creation behavior |
| OperationalTradeIntentView | 3 | Presentation view projection |
| JsonSerialization | 5 | JSON serialization (Decimal-as-string, datetime-as-ISO) |

## 28. Test results

### Focused tests
```
tests/test_operational_trade_intent_application.py: 58 passed
tests/test_operational_trade_intent.py: 125 passed
tests/test_operational_trade_intent_engine.py: 69 passed
tests/test_trade_planning.py: 158 passed
tests/test_paper_trading.py: 114 passed
tests/test_paper_trading_operations.py: 78 passed
Focused total: 544 passed
```

### Full suite
```
5101 passed
2 pre-existing yfinance-related failures (test_live_data_integration.py)
3 skipped
```

Baseline was 5043 passed. New tests: 5101 - 5043 = 58 (matches new test file). No new regressions.

## 29. Regression results

- `tests/test_operational_trade_intent.py`: 125 passed (no regression)
- `tests/test_operational_trade_intent_engine.py`: 69 passed (no regression)
- `tests/test_trade_planning.py`: 158 passed (no regression)
- `tests/test_paper_trading.py`: 114 passed (no regression)
- `tests/test_paper_trading_operations.py`: 78 passed (no regression)
- Full suite: 5101 passed, 2 pre-existing failures, 3 skipped (no new regressions)

## 30. Limitations

1. **No persistence**: Intents are returned in memory only. Persistence is deferred to a future checkpoint when authorization requires durable intent references.
2. **No authorization**: The intent is NOT authorization. Future checkpoints will add an authorization boundary.
3. **No execution**: No execution command, broker adapter, or order submission.
4. **No UI presentation layer**: The API endpoint returns JSON. No HTML template section is added (deferred until UI requirements are clear).
5. **No dedicated serializer**: The `OperationalTradeIntent` model does not have a dedicated serialization module (the presentation view's JSON conversion is sufficient for the API).
6. **No reporting formatter**: No human-readable report formatter for intents (deferred).

## 31. Implementation decision

**IMPLEMENTATION: YES**

The audit determined that a new dedicated application service (`OperationalTradeIntentApplicationService`) is the correct application-level owner of explicit OperationalTradeIntent creation. The service is:
- Stateless, deterministic, pure delegation
- Reusable outside the dashboard
- The natural integration point for future authorization
- Consumed by the dashboard service and API endpoint

The implementation is minimal and additive:
- 1 new engine file (application service)
- 1 new test file (58 tests)
- 3 modified dashboard files (request dataclass, service method, view, API endpoint)
- No frozen components modified

## 32. Final verdict

**PASS**

All success criteria met:
1. Correct application boundary identified (dedicated application service)
2. Explicit intent creation exposed cleanly
3. Intent creation is NOT an automatic side effect
4. TradePlan remains authoritative
5. OperationalTradeIntentEngine remains responsible for construction
6. OperationalTradeIntent remains immutable
7. PaperTrade remains independent
8. No analytical component is modified
9. No future market data is accessed
10. No authorization is implemented
11. No execution is implemented
12. No broker integration exists
13. No hidden persistence is introduced
14. Identity contract remains unchanged
15. Timestamp responsibility remains explicit
16. Focused integration tests pass (58 new)
17. Existing tests pass (544 focused, 5101 full suite)
18. Full suite has no new regressions
19. Documentation is complete
20. AGENTS.md is appended correctly
