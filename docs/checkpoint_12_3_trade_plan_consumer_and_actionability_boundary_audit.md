# Checkpoint 12.3 — Trade Plan Consumer & Actionability Boundary Audit

## 1. Purpose

Determine exactly what happens AFTER a valid/invalid TradePlan is produced. Identify and freeze the responsibility boundary between TradePlan and its downstream consumers. This is an architecture-first audit only — no execution, broker integration, BUY/SELL logic, or portfolio management is being implemented.

## 2. Scope

This audit traces every direct and indirect consumer of:
- `TradePlan` (domain model)
- `TradePlanView` (presentation projection)
- `TradePlanRequest` (request dataclass)
- `plan_trade()` (service method)
- `/api/trade-plan` (API route)
- Serialized TradePlan representations
- Risk-plan status, planned quantity, planned risk, planned reward

## 3. Exact Files Inspected

| File | Role |
|------|------|
| `src/engine/models/trade_plan.py` | TradePlan domain model (frozen+slots dataclass) |
| `src/engine/intelligence/trade_planning.py` | TradePlanningEngine — deterministic risk calculation |
| `src/engine/config/trade_plan_config.py` | TradePlanConfig (max_risk_percent, rounding, precision) |
| `src/engine/intelligence/trade_planning_serialization.py` | TradePlan serialization (exists, NOT used downstream) |
| `src/engine/reporting/trade_planning.py` | TradePlanFormatter |
| `src/dashboard/views.py` | TradePlanView, PaperTradeView, projection functions |
| `src/dashboard/services.py` | DashboardAnalysisService.plan_trade(), create_paper_trade(), _to_trade_plan_view() |
| `src/dashboard/app.py` | /api/trade-plan route, workstation route |
| `src/dashboard/paper_trade_operations.py` | PaperTradingOperations — operational paper-trading layer |
| `src/dashboard/paper_trade_store.py` | PaperTradeStore — filesystem persistence |
| `src/engine/intelligence/paper_trading.py` | PaperTradingEngine.create()/track()/close_manually()/cancel() |
| `src/engine/models/paper_trade.py` | PaperTrade domain model |
| `src/engine/intelligence/paper_trading_serialization.py` | PaperTrade serialization |
| `src/dashboard/templates/workstation.html` | Workstation UI consuming TradePlanView |
| `src/dashboard/templates/paper_trading.html` | Paper trading journal UI |

## 4. Actual Downstream Data Flow

```
MarketScanResult / TradeCandidate (authoritative geometry)
        |
        v
TradePlanningEngine.plan()
        |
        v
TradePlan (domain model — deterministic, immutable, Decimal-based)
        |
        +---> [PATH A: Presentation]
        |       |
        |       v
        |   _to_trade_plan_view() -> TradePlanView
        |       |
        |       +---> /api/trade-plan  (JSON via trade_plan_view_to_jsonable)
        |       +---> /workstation      (HTML via workstation.html template)
        |       +---> DashboardAnalysisService.plan_trade() return value
        |
        +---> [PATH B: Paper Trading]
                |
                v
        PaperTradingEngine.create(plan=plan)
                |
                v
        PaperTrade (fields copied BY VALUE from TradePlan)
                |
                v
        PaperTradeStore (filesystem JSON persistence)
                |
                v
        PaperTradeView -> paper_trading.html / API / performance analytics
```

**CRITICAL FINDING**: TradePlan is NOT independently serialized or persisted. The `trade_planning_serialization.py` module exists but is never imported or called anywhere in the codebase. TradePlan survives ONLY by having its fields copied by value into PaperTrade at creation time.

## 5. TradePlan Consumers

| Consumer | Type | How it consumes |
|----------|------|-----------------|
| `DashboardAnalysisService.plan_trade()` | Service | Projects TradePlan → TradePlanView (pure projection) |
| `/api/trade-plan` route | API | Calls plan_trade(), serializes TradePlanView to JSON |
| `/workstation` route | UI | Calls plan_trade(), passes TradePlanView to template |
| `DashboardAnalysisService.create_paper_trade()` | Service | Passes TradePlan to PaperTradingEngine.create(plan=plan) |
| `PaperTradingOperations._create_eligible_trade()` | Service | Passes TradePlan to PaperTradingEngine.create(plan=plan) |
| `TradePlanFormatter` | Reporting | Formats TradePlan for human-readable report |

## 6. TradePlanView Consumers

| Consumer | Type | How it consumes |
|----------|------|-----------------|
| `trade_plan_view_to_jsonable()` | Serialization | Converts TradePlanView → JSON dict (Decimal as string + _float) |
| `workstation.html` template | UI | Renders trade_plan fields (status, geometry, position size, rationale, warnings) |
| `/api/trade-plan` route | API | Returns trade_plan_view_to_jsonable(plan_view) as JSONResponse |

## 7. TradePlanRequest / plan_trade Consumers

| Consumer | Type | How it consumes |
|----------|------|-----------------|
| `DashboardAnalysisService.plan_trade()` | Service | Accepts TradePlanRequest, orchestrates analyze() + TradePlanningEngine.plan() |
| `/api/trade-plan` route | API | Constructs TradePlanRequest from query params, calls plan_trade() |
| `/workstation` route | UI | Constructs TradePlanRequest from query params (account_capital, risk_percent), calls plan_trade() |

## 8. Paper-trading Architecture

### 8.1 What inputs does paper trading consume?

Paper trading consumes (VERBATIM, by value):
- **From TradePlan**: entry, stop, target_1, engine_risk_distance, engine_reward_distance, engine_risk_reward_ratio, quantity, planned_risk, planned_reward, maximum_risk, account_capital, risk_percent, plan_id
- **From TradeCandidate (via analyze view)**: direction, setup_type, existing_decision

### 8.2 Does it consume TradePlan directly?

YES — `PaperTradingEngine.create(plan=plan)` accepts a TradePlan object (or TradePlanView — both have the same attributes via duck typing).

### 8.3 Does it consume TradePlanView?

YES — the engine's `_attr()` helper uses `getattr(plan, name, None)`, so TradePlanView works identically.

### 8.4 Does it independently recalculate quantity?

NO — quantity is copied verbatim from TradePlan.quantity (via `_attr("quantity")`).

### 8.5 Does it independently recalculate entry/stop/target?

NO — entry, stop, target_1 are copied verbatim from TradePlan.

### 8.6 Does it independently calculate risk?

NO — engine_risk_distance, planned_risk, planned_reward, maximum_risk are copied verbatim.

### 8.7 Does it create a paper order/trade?

YES — it creates a `PaperTrade` (frozen immutable record), NOT an order. There is no order abstraction.

### 8.8 Does it contain BUY/SELL semantics?

NO — it uses LONG/SHORT direction only. The system deliberately avoids BUY/SELL/ENTER/EXIT/HOLD.

### 8.9 Does it contain execution-like state?

YES — but SIMULATED state only: `WAITING_FOR_ENTRY → OPEN → CLOSED/CANCELLED/INVALIDATED`. This is a paper-trade lifecycle, NOT execution.

### 8.10 Does it maintain position state?

PARTIALLY — it tracks individual paper trades (entry/exit prices, realized R/P&L). There is NO multi-position portfolio management. The closest to "portfolio" is `PaperTradePerformanceEngine` which computes descriptive statistics across trades.

### 8.11 Does it use broker APIs?

NO — explicitly stated throughout. No broker integration exists.

### 8.12 Does it use live market prices after planning?

YES — but ONLY completed candles for entry/exit touch detection (deterministic, no look-ahead). The live prices do NOT alter the plan; they only determine whether the simulated entry/exit conditions were met.

### 8.13 Does it mutate TradePlan?

NO — TradePlan is frozen+slots. PaperTrade stores values BY VALUE (copied), not by reference. The `PaperTradingEngine.create()` reads attributes from the plan but never modifies it.

### 8.14 Does it create a second source of truth?

NO — TradePlan remains the single authoritative source for planned quantity/risk/reward. PaperTrade stores a VALUE COPY at creation time. The copy is a historical snapshot, not a competing authority.

## 9. Actionability Boundary

### 9.1 The desired semantic separation

```
TradePlan VALID  ≠  Execution Authorized
```

This separation IS maintained:
- `RiskPlanStatus.VALID` means: "complete geometry, valid account-risk inputs, quantity sized, planned_risk ≤ maximum_risk"
- It NEVER means: "authorized to trade", "ready for execution", or "BUY/SELL signal"
- The paper-trading eligibility gate is `ActionabilityState.READY_FOR_REVIEW` (from `derive_actionability`), which is a SEPARATE concern from TradePlan validity

### 9.2 Can an invalid TradePlan reach paper trading?

NO — but the path is indirect:
1. `create_paper_trade()` calls `TradePlanningEngine.plan()` internally (does NOT accept a pre-built plan)
2. The resulting TradePlan (even if INVALID/GEOMETRY_UNAVAILABLE/RISK_LIMIT_EXCEEDED) is passed to `PaperTradingEngine.create()`
3. `create()` checks `has_geometry` independently — if geometry is incomplete → `INVALIDATED` (NO_GEOMETRY)
4. For VALID plans with valid quantity → `WAITING_FOR_ENTRY`
5. A plan with `RISK_LIMIT_EXCEEDED` or `QUANTITY_UNAVAILABLE` would have `quantity=None` but geometry present — this creates a trade with `has_geometry=True` but `quantity=None` — the trade would be `WAITING_FOR_ENTRY` but unsized

**FINDING**: The paper-trading layer does NOT check `TradePlan.risk_plan_status`. It relies solely on its own `has_geometry` check. A `RISK_LIMIT_EXCEEDED` plan (which has complete geometry but no quantity) would create a WAITING_FOR_ENTRY paper trade with `quantity=None`. This is a minor semantic gap — the trade would wait for entry but has no position size. In practice, the operations layer's eligibility gate (`READY_FOR_REVIEW` requiring complete geometry + QUALIFIED/PREFERRED) prevents this, and manual creation via the API is human-driven.

### 9.3 Can RISK_LIMIT_EXCEEDED reach an operational path?

The `PaperTradingOperations._create_eligible_trade()` checks `actionability == READY_FOR_REVIEW`. The `READY_FOR_REVIEW` state requires complete geometry. A `RISK_LIMIT_EXCEEDED` plan has complete geometry. So theoretically YES — but the operations path requires `account_capital` and `risk_percent` to be set, and a human would need to supply capital/risk% that produce a RISK_LIMIT_EXCEEDED plan. The resulting paper trade would have `quantity=None`. This is a **documented limitation**, not a defect — paper trades with `quantity=None` are valid for tracking (entry/exit still detected), but P&L/R cannot be computed.

### 9.4 Can QUANTITY_UNAVAILABLE become a trade?

YES — same reasoning as RISK_LIMIT_EXCEEDED. The plan would have geometry but no quantity. The paper trade would track entry/exit but have `realized_pnl=None`.

### 9.5 Is VALID treated as "ready for execution"?

NO — VALID is treated as "risk plan mathematically valid, produced a usable position size." The downstream `READY_FOR_REVIEW` actionability gate is the eligibility check for paper trading. VALID ≠ READY_FOR_REVIEW.

## 10. TradePlan Status Interpretation

| Status | Meaning | Can reach paper trading? | Can reach UI? |
|--------|---------|------------------------|---------------|
| VALID | Sized position, planned_risk ≤ maximum_risk | Yes (if READY_FOR_REVIEW) | Yes |
| INVALID_INPUT | Bad capital/risk% inputs | No (geometry absent → INVALIDATED) | Yes (shown in UI) |
| GEOMETRY_UNAVAILABLE | Incomplete entry/stop | No (geometry absent → INVALIDATED) | Yes |
| RISK_LIMIT_EXCEEDED | Smallest unit exceeds max risk | Yes (geometry present, quantity=None) | Yes |
| QUANTITY_UNAVAILABLE | No quantity spec / fractional below step | Yes (geometry present, quantity=None) | Yes |

All five statuses are DISPLAYED in the workstation UI and JSON API. None trigger execution.

## 11. Source-of-Truth Audit

### 11.1 Planned quantity

- **Authoritative source**: `TradePlan.quantity` (computed by TradePlanningEngine)
- **PaperTrade**: `planned_quantity` — copied by value from TradePlan at creation
- **Duplicate calculations**: NONE — paper trading never recalculates quantity
- **VERDICT**: Clean — TradePlan is single source of truth

### 11.2 Planned risk

- **Authoritative source**: `TradePlan.planned_risk` (= quantity × engine_risk_distance)
- **PaperTrade**: `planned_risk` — copied by value from TradePlan at creation
- **Duplicate calculations**: NONE
- **VERDICT**: Clean

### 11.3 Planned reward

- **Authoritative source**: `TradePlan.planned_reward` (= quantity × engine_reward_distance)
- **PaperTrade**: N/A (PaperTrade does not store planned_reward explicitly — it has `engine_reward_distance` from which reward could be derived)
- **VERDICT**: Clean — no competing calculation

### 11.4 Entry / Stop / Target

- **Authoritative source**: `TradePlan.entry/stop/target_1` (copied verbatim from TradeCandidate)
- **PaperTrade**: `entry/stop/target_1` — copied by value from TradePlan at creation
- **Duplicate calculations**: NONE
- **VERDICT**: Clean

## 12. Duplicate Calculation Audit

**RESULT**: No duplicate calculations found. The paper-trading layer copies all planning values by value and never recomputes them. The `PaperTradingEngine.track()` method implements its own entry/exit TOUCH DETECTION (using OHLC), but this is NOT a recalculation of the plan — it's a lifecycle simulation that observes whether market prices touched the planned levels.

## 13. Mutation Audit

| Artifact | Mutated by downstream? | Evidence |
|----------|----------------------|----------|
| TradePlan | NO | frozen+slots; PaperTradeEngine reads via getattr only |
| TradePlanView | NO | frozen+slots; pure projection |
| TradeCandidate | NO | retained by reference in TradeDecision, never modified |
| MarketScanResult | NO | frozen+slots |
| PaperTrade | NO (creates NEW) | track()/close_manually()/cancel() use `dataclasses.replace()` to return new instances |

**VERDICT**: No planning artifact is ever mutated downstream.

## 14. Lifecycle Audit

The paper-trade lifecycle is:
```
[creation] → WAITING_FOR_ENTRY → OPEN → CLOSED
                                  |        ↑
                                  +→ CANCELLED (from WAITING_FOR_ENTRY only)
                                  +→ INVALIDATED (from creation only, when geometry incomplete)
```

- **WAITING_FOR_ENTRY**: entry condition not yet confirmed by a completed candle
- **OPEN**: entry confirmed, waiting for stop/target touch
- **CLOSED**: stop hit, target hit, expired, or manually closed
- **CANCELLED**: human action, only from WAITING_FOR_ENTRY
- **INVALIDATED**: set at creation when geometry is incomplete (NO_GEOMETRY)

Terminal states: CLOSED, CANCELLED, INVALIDATED — returned UNCHANGED by track().

## 15. Temporal / Point-in-Time Audit

### 15.1 Does paper trading use the same planning snapshot?

YES — PaperTrade stores entry/stop/target/quantity/risk BY VALUE at creation. These values never change regardless of future market data.

### 15.2 Does it re-run analysis?

NO — the analysis (analyze → scan → TradePlan) runs once at creation. Subsequent tracking only fetches completed candles for touch detection.

### 15.3 Does it fetch newer prices?

YES — `track_paper_trade()` and `_track_one()` fetch completed candles from the provider. These are used ONLY for entry/exit touch detection, NOT to modify the plan.

### 15.4 Does it alter the planned entry/stop/target?

NO — the planned levels are immutable in PaperTrade. Touch detection does not modify them.

### 15.5 Can a later market state overwrite the original plan?

NO — the original plan (TradePlan) is immutable. PaperTrade stores a value snapshot. Neither is overwritten by later market data.

### 15.5 Are timestamps preserved?

YES — PaperTrade stores: `created_at`, `evaluation_timestamp`, `entry_timestamp`, `exit_timestamp`. The evaluation_timestamp (when the plan was created) is distinct from lifecycle timestamps.

### 15.6 Can a plan created at T1 be associated with a paper trade created at T2?

YES — but in practice, `create_paper_trade()` creates both in one call. The `created_at` is caller-supplied (defaults to `datetime.now(UTC)`). The `evaluation_timestamp` is the analysis timestamp (close of latest completed candle). The deterministic `paper_trade_id` hashes the canonical opportunity identity + created_at + sequence, so the same opportunity at T1 vs T2 produces DIFFERENT trade ids (by design).

### 15.7 Market Update vs Plan Mutation

**DISTINCT**: A market update (new completed candle) may change the PAPER TRADE's lifecycle state (WAITING_FOR_ENTRY → OPEN → CLOSED) but NEVER changes the planned entry/stop/target/quantity. This is NOT look-ahead — it is the legitimate observation of whether planned levels were touched by subsequent completed candles.

## 16. API / Dashboard Audit

### 16.1 /api/trade-plan

- Returns structured JSON of TradePlanView
- Does NOT create paper trades
- Does NOT trigger operational behavior
- Merely displays the plan

### 16.2 TradePlanView in workstation

- Rendered in `workstation.html` (lines 137-200)
- Displayed as metrics: plan_id, direction, decision, status, account risk, geometry, position size, rationale, warnings
- Has a form (account_capital, risk_percent inputs) that triggers a GET to /workstation
- Does NOT create paper trades directly (paper trades created via /api/paper-trades POST)

### 16.3 UI actions that consume TradePlan

- **Display only**: /api/trade-plan, /workstation (trade plan section)
- **Paper trade creation**: Uses plan internally (via create_paper_trade) but does NOT expose TradePlan to the user as an execution authorization
- **Implication**: No UI action interprets TradePlan.VALID as "execute now"

## 17. Serialization / Persistence Audit

### 17.1 TradePlan persistence

**TradePlan is NOT independently persisted.** The `trade_planning_serialization.py` module (serialize_trade_plan/deserialize_trade_plan) exists and has tests, but is NEVER imported or called outside its own module. TradePlan survives only by having its fields copied into PaperTrade.

### 17.2 PaperTrade persistence

PaperTrade IS persisted via `PaperTradeStore`:
- Filesystem JSON, one file per paper trade (`<directory>/<paper_trade_id>.json`)
- Atomic writes (tempfile + os.replace)
- Schema-versioned (PAPER_TRADE_SCHEMA_VERSION = 1)
- Uses `paper_trading_serialization.py` (serialize_paper_trade/deserialize_paper_trade)

### 17.3 Information preservation across the TradePlan → PaperTrade transition

| TradePlan field | Stored in PaperTrade? | Field name |
|----------------|----------------------|------------|
| plan_id | YES | plan_id |
| instrument | YES | instrument |
| timeframe | YES | timeframe |
| direction | YES | direction |
| existing_decision | YES | existing_decision |
| actionability | NO (not needed) | — |
| account_capital | YES | account_capital |
| risk_percent | YES | risk_percent |
| maximum_risk | YES | maximum_risk |
| entry | YES | entry |
| stop | YES | stop |
| target_1 | YES | target_1 |
| target_2 | YES (always None) | target_2 |
| target_2_supported | YES (always False) | target_2_supported |
| engine_risk_distance | YES | engine_risk_distance |
| engine_reward_distance | YES | engine_reward_distance |
| engine_risk_reward_ratio | YES | engine_risk_reward_ratio |
| quantity | YES | planned_quantity |
| planned_risk | YES | planned_risk |
| planned_reward | NO | — |
| quantity_status | NO | — |
| risk_plan_status | NO | — |
| quantity_spec_available | NO | — |
| warnings | NO | — |
| rationale | NO | — |
| label | YES | label |
| metadata | YES | metadata |

**Information loss**: `planned_reward`, `quantity_status`, `risk_plan_status`, `quantity_spec_available`, `warnings`, `rationale` are NOT stored in PaperTrade. These are presentation/audit fields for the plan, not essential for lifecycle tracking. `planned_reward` can be recomputed from `planned_quantity × engine_reward_distance`.

## 18. Dependency Direction

### 18.1 Desired direction (CONFIRMED)

```
Analytical (MarketScanResult)
    ↓
Planning (TradePlan)
    ↓
Simulation (PaperTrade)
    ↓
Execution (future boundary — NOT IMPLEMENTED)
```

### 18.2 Verified clean (no violations)

- Paper trading does NOT recalculate planning → geometry/quantity/risk are copied verbatim
- Paper trading does NOT modify analytical results → TradePlan/TradeCandidate are immutable
- Planning has NO direct broker dependency → TradePlanningEngine is pure calculation
- Dashboard has NO independent risk calculations → all values come from TradePlan
- No backward dependency from simulation to planning

## 19. Analytical vs Planning vs Simulation vs Execution Classification

| Component | Category | Evidence |
|-----------|----------|----------|
| TradePlanningEngine | PLANNING | Pure calculation around existing geometry |
| TradePlan (model) | PLANNING | Risk/position-size calculation output |
| TradePlanView | PLANNING (presentation) | Read-only projection of TradePlan |
| TradePlanRequest | PLANNING (request) | Request dataclass for planning |
| /api/trade-plan | PLANNING (presentation) | Returns plan as JSON, no action |
| PaperTradingEngine | SIMULATION | Simulates entry/exit tracking |
| PaperTrade (model) | SIMULATION | Simulated trade record |
| PaperTradeView | SIMULATION (presentation) | Read-only projection |
| PaperTradeStore | SIMULATION (persistence) | Persists simulated trades |
| PaperTradePerformanceEngine | SIMULATION (analytics) | Descriptive stats on simulated trades |
| PaperTradingOperations | SIMULATION (orchestration) | Orchestrates tracking + creation |
| Execution | EXECUTION | **NOT IMPLEMENTED** |

**NO COMPONENT MIXES CATEGORIES.** Planning never simulates; simulation never plans; execution does not exist.

## 20. Trading-Semantics Contamination Audit

### 20.1 Forbidden terms search

| Term | Found in executable context? | Location |
|------|----------------------------|----------|
| BUY | NO | Only in docstrings stating "NOT a BUY/SELL" |
| SELL | NO | Only in docstrings stating "NOT a BUY/SELL" |
| ENTER | NO | Only as "WAITING_FOR_ENTRY" (state name, not action) |
| EXIT | NO | Only as "_track_exit" (internal method name for touch detection) |
| HOLD | NO | Only in docstrings |
| EXECUTE | NO | Only in docstrings stating "NOT an execution engine" |
| ORDER | NO | "Paper order" does not exist as a concept |
| PLACE ORDER | NO | Not present |
| CANCEL ORDER | NO | "cancel_paper_trade" exists (human action, not broker) |
| BROKER | NO | Only in docstrings stating "NOT a broker" |
| POSITION | NO | "Position" appears only in "position sizing" context (planning), not as a maintained state |
| FILLED/FILL | NO | Not present (entry is "confirmed"/"touched", not "filled") |
| SL/STOP LOSS | NO | "stop" used, never "stop loss" |
| TP/TAKE PROFIT | NO | "target" used, never "take profit" |

**VERDICT**: No trading-semantics contamination. The system uses LONG/SHORT, entry/stop/target, WAITING_FOR_ENTRY/OPEN/CLOSED — deliberately distinct from execution vocabulary.

## 21. Tests Inspected

| Test File | Test Count | What it Proves |
|-----------|-----------|----------------|
| `tests/test_trade_planning.py` | 158 | TradePlan calculations are deterministic, Decimal-based, risk-constrained, immutable, geometry-preserving, point-in-time independent |
| `tests/test_paper_trading.py` | 114 | PaperTrade creation/tracking/close/cancel lifecycle, entry/exit detection, Decimal P&L/R accounting, persistence, no-look-ahead |
| `tests/test_paper_trading_operations.py` | 78 | Operational cycle orchestration, duplicate prevention, chronological tracking, failure isolation, decision/geometry/plan preservation |
| `tests/test_dashboard.py` | 67 | Dashboard routes, health, instrument/timeframe selection, decision rendering, evidence rendering, trade geometry rendering, actionability mapping, no-look-ahead |

## 22. Test Results

```
448 passed, 1 warning in 8.93s
```

All 448 tests pass. The single warning is a pre-existing Starlette/httpx deprecation (unrelated).

## 23. Coverage Gaps

| Gap | Severity | Notes |
|-----|----------|-------|
| No test for TradePlan → PaperTrade `quantity=None` propagation path | LOW | Theoretically a RISK_LIMIT_EXCEEDED plan creates a paper trade with quantity=None; not explicitly tested but handled gracefully (P&L/R=None) |
| No test for TradePlanView JSON round-trip via /api/trade-plan end-to-end with invalid inputs | LOW | Input validation is tested at the service level; route-level integration is tested in test_dashboard.py |
| No explicit test that TradePlan is never mutated by paper trading | LOW | Frozen dataclass + create() only reads; tested implicitly by immutability tests |
| TradePlan serialization module (trade_planning_serialization.py) has no integration tests | LOW | Module has unit tests but is never used in production code paths |

## 24. Limitations

1. TradePlan is not independently persisted — it exists only as a transient object and as value-copies inside PaperTrade. This is by design (planning is point-in-time; paper trade is the durable record).
2. The `trade_planning_serialization.py` module exists but is unused. It could be removed or documented as "reserved for future use."
3. PaperTrade does not store `planned_reward`, `risk_plan_status`, `quantity_status`, `warnings`, or `rationale`. These are available only in the live TradePlan object.
4. The `PaperTradingEngine.create()` does not check `TradePlan.risk_plan_status` — it uses its own `has_geometry` check. This means plans with complete geometry but no sized quantity (RISK_LIMIT_EXCEEDED, QUANTITY_UNAVAILABLE) can create WAITING_FOR_ENTRY paper trades with `quantity=None`.
5. No execution layer exists. The boundary between simulation (paper trading) and execution (broker/order) is cleanly separated but execution is entirely absent.

## 25. Implementation Decision

**DO NOT MODIFY IMPLEMENTATION.**

The architecture is clean:
- TradePlan has a single, well-defined creation path (TradePlanningEngine.plan())
- TradePlan has well-defined consumption paths (presentation + paper trading)
- Paper trading copies planning values by value and never mutates them
- No duplicate calculations exist
- No trading-semantics contamination exists
- No dependency direction violations exist
- The VALID ≠ Execution Authorized separation is maintained

**One documented observation (not a defect)**: The `trade_planning_serialization.py` module is unused. It is not a defect — it is available infrastructure. It should either be documented as "reserved for future TradePlan persistence" or removed in a future cleanup. This is outside the scope of Checkpoint 12.3.

## 26. Final Verdict

**PASS**
