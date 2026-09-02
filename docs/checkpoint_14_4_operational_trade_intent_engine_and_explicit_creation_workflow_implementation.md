# Checkpoint 14.4 — Operational Trade Intent Engine & Explicit Creation Workflow Implementation

## 1. Purpose

Implement the dedicated `OperationalTradeIntentEngine` and its explicit creation workflow, establishing a clean production-code boundary:

    TradePlan -> OperationalTradeIntentEngine -> OperationalTradeIntent

The engine converts an already-created authoritative `TradePlan` into an `OperationalTradeIntent` through an explicit, deterministic, validated workflow. It delegates authoritative construction to the existing factory `create_intent_from_plan()` from Checkpoint 14.2.

## 2. Scope

This checkpoint is IMPLEMENTATION (unlike 14.3 which was audit-only).

The engine's ONLY responsibility is to convert a `TradePlan` into an `OperationalTradeIntent`. It does NOT perform market scanning, candle retrieval, feature construction, setup detection, trade planning calculations, risk recalculation, quantity recalculation, authorization, execution, broker communication, position management, portfolio management, or paper-trade lifecycle management.

## 3. Previous checkpoint decisions

### Checkpoint 10.8 — Historical Research (FROZEN)
Descriptive historical research pipeline. Not modified.

### Checkpoint 11.8 — Current-Market Analytical Output (FROZEN)
Market scanning and analytical output. Not modified.

### Checkpoint 12.6 — Trade Planning + Paper Trading + Performance (FROZEN)
Trade planning, paper trading, performance analytics. Not modified.

### Checkpoint 13.6 — Pre-Execution Architecture (FROZEN)
Execution architecture freeze. No execution path exists. Not modified.

### Checkpoint 14.1 — Operational Trade Intent Boundary (FROZEN)
Established the boundary: intent is NOT authorization, NOT execution, NOT paper trade. Not modified.

### Checkpoint 14.2 — Operational Trade Intent Model & Identity (FROZEN)
Established the `OperationalTradeIntent` model, `create_intent_from_plan()` factory, deterministic identity contract (`intent-` + sha256[:16], `fp-` + sha256[:16]), frozen+slots immutability. Not modified.

### Checkpoint 14.3 — Operational Trade Intent Factory & TradePlan Integration Boundary (FROZEN)
Established that intent creation must be an EXPLICIT, SEPARATE workflow. Recommended a dedicated `OperationalTradeIntentEngine` in `src/engine/intelligence/operational_trade_intent.py`. Established that timestamps must be supplied by the caller/application layer. Not modified.

## 4. Exact implementation files

### New production file
- `src/engine/intelligence/operational_trade_intent.py` — `OperationalTradeIntentEngine`

### New test file
- `tests/test_operational_trade_intent_engine.py` — 69 tests across 25+ areas

### New documentation
- `docs/checkpoint_14_4_operational_trade_intent_engine_and_explicit_creation_workflow_implementation.md`

### Modified metadata
- `AGENTS.md` — Checkpoint 14.4 entry appended

### Files NOT modified
- `src/engine/models/operational_trade_intent.py` (14.2 model/factory — frozen)
- `src/engine/models/trade_plan.py` (TradePlan — frozen)
- `src/engine/intelligence/trade_planning.py` (TradePlanningEngine — frozen)
- All paper-trading, analytical, dashboard, authorization, execution components

## 5. OperationalTradeIntentEngine design

### Architecture
```
TradePlan (authoritative, already created)
    |
    v
OperationalTradeIntentEngine.create_from_plan()
    |-- Type validation (isinstance TradePlan)
    |-- Precondition validation (risk_plan_status.VALID, direction LONG/SHORT)
    |-- Field extraction (verbatim from TradePlan)
    |-- Caller-supplied timestamps (created_at REQUIRED)
    |-- Delegation to create_intent_from_plan()
    |
    v
OperationalTradeIntent (immutable, deterministic identity)
```

### API
```python
class OperationalTradeIntentEngine:
    def create_from_plan(
        self,
        plan: TradePlan,
        *,
        created_at: datetime,              # REQUIRED, caller-supplied
        evaluation_timestamp: datetime | None = None,
        valid_until: datetime | None = None,
        label: str | None = None,         # defaults to plan.label
        metadata: Mapping[str, str] | None = None,  # defaults to plan.metadata
    ) -> OperationalTradeIntent:
```

### Design decisions
- **Stateless**: No mutable state, no config object, no cache, no registry.
- **Explicit keyword-only timestamps**: `created_at` is REQUIRED (no default), preventing silent wall-clock generation.
- **Caller-supplied values**: `label` and `metadata` default to the plan's values when not explicitly provided.
- **Type validation**: Raises `TypeError` for non-TradePlan inputs.
- **Precondition validation**: Raises `ValueError` for non-VALID plans or non-directional plans before factory delegation.
- **Pure delegation**: All construction authority rests with `create_intent_from_plan()`.

## 6. Factory delegation

The engine delegates ALL authoritative construction to `create_intent_from_plan()` from Checkpoint 14.2. The engine does NOT:

- Recalculate entry, stop, target, quantity, planned risk, planned reward
- Recalculate risk distance, reward distance, risk/reward ratio
- Access candles, market data, or analytical engines
- Generate timestamps silently

The engine extracts TradePlan fields VERBATIM and passes them to the factory. The factory computes the deterministic `intent_id` and `content_fingerprint`.

## 7. Explicit creation workflow

The intent is created ONLY through an explicit call to `engine.create_from_plan(plan, created_at=...)`. It is NEVER created automatically as a side effect of:

- Market scanning (`MarketScanner.scan()`)
- Trade planning (`TradePlanningEngine.plan()`)
- Paper trading (`PaperTradingEngine`, `create_paper_trade()`)
- Dashboard rendering (`DashboardAnalysisService`)
- Any other path

There is NO `READY_FOR_REVIEW` trigger, NO automatic intent creation, NO side-effect-based creation.

## 8. Timestamp handling

Per Checkpoint 14.3, timestamps are supplied by the caller/application layer:

- `created_at`: REQUIRED, timezone-aware. The engine NEVER generates it. No `datetime.now()` call exists in the engine.
- `evaluation_timestamp`: Optional, defaults to `None`.
- `valid_until`: Optional, defaults to `None`.

The engine performs NO timestamp generation. Naive timestamps are rejected by the factory's validation (propagated as `ValueError`).

## 9. Validity handling

The engine does NOT invent an expiration policy. It uses the existing factory contract:

- `valid_until` is passed through to the factory.
- The factory validates `valid_until >= created_at`.
- Expiration describes the validity window of the operational intent ONLY.
- It does NOT mean authorized, approved, or executable.

## 10. Identity behavior

The Checkpoint 14.2 identity contract is preserved:

- `intent_id`: `"intent-" + sha256[:16]` of canonical operational content + instance discriminator
- `content_fingerprint`: `"fp-" + sha256[:16]` of canonical economic content only
- Deterministic: same inputs produce same identity
- Timestamps affect `intent_id` (instance discriminator) but NOT `content_fingerprint`
- No UUIDs, no random identifiers, no broker IDs

## 11. Duplicate creation behavior

Repeated explicit creation with the same inputs produces intents with identical identity (deterministic by construction). The engine does NOT:

- Introduce a registry
- Introduce hidden caching
- Persist automatically
- Attempt to deduplicate using mutable global state

If the identity contract intentionally allows distinct intent instances through the instance discriminator (different `created_at` or `label`), this behavior is preserved.

## 12. Immutability verification

- `OperationalTradeIntent` remains `frozen=True, slots=True` (unchanged from 14.2).
- All fields use immutable types (`tuple`, `Decimal`, `str`, `datetime`).
- The engine returns the intent directly; no mutation path exists.
- `warnings` is a `tuple`, `metadata` is a `tuple` of `tuple`s.
- Tests verify frozen behavior (attribute assignment raises `AttributeError`).

## 13. TradePlan preservation

The TradePlan is consumed by VALUE (fields extracted). It is NEVER mutated. The engine reads fields via attribute access only. Tests verify the plan's fields are unchanged after intent creation.

## 14. PaperTrade separation

The engine does NOT:

- Import or reference `PaperTrade`, `PaperTradingEngine`, or paper-trading persistence
- Insert `OperationalTradeIntent` into `PaperTrade`
- Create any feedback loop from paper trading outcomes

The relationship remains:
- `TradePlan -> PaperTrade` (independent path)
- `TradePlan -> OperationalTradeIntent` (this engine)

## 15. Analytical separation

The engine does NOT:

- Import or reference `MarketScanResult`, `MarketScanner`, `TradeCandidate`, `TradeDecision`, `TradeOpportunity`
- Access candles, indicators, feature construction, or setup detection
- Call any analytical engine

## 16. Authorization separation

NO authorization is implemented. The engine does NOT create:

- `Authorization`, `ExecutionAuthorization`, `Approval`, `Permission`
- `KillSwitch`, `EmergencyStop`, or any equivalent artifact
- Any authorization fields on `OperationalTradeIntent`

The future authorization boundary will consume `OperationalTradeIntent` and bind to `intent_id` + `content_fingerprint`.

## 17. Execution separation

NO execution is implemented. The engine does NOT create:

- `ExecutionCommand`, `BrokerAdapter`, `BrokerClient`, `Order`
- `Position`, `ExecutionResult`, `BrokerOrder`
- `LiveTrading`, `OrderSubmission`, or any equivalent

No broker connection, no Upstox order API, no live endpoint.

## 18. Persistence behavior

The engine does NOT implement persistence. It returns the intent object. No JSON, database, files, cache entries, or dashboard state are written. Persistence belongs to a later checkpoint if required.

## 19. Dashboard/API behavior

The engine is NOT integrated into FastAPI routes or dashboard services. No modification to `src/dashboard/services.py`, `src/dashboard/views.py`, or `src/dashboard/app.py`. Dashboard integration is a separate future checkpoint.

## 20. Tests created

`tests/test_operational_trade_intent_engine.py` — 69 tests covering:

1. Valid TradePlan -> OperationalTradeIntent
2. Exact type validation (dict, None, string, fake object rejected)
3. Factory invocation / delegation (intent_id format, fingerprint format, version)
4. TradePlan geometry preserved verbatim (entry, stop, target, risk/reward distances)
5. Quantity preserved
6. Risk fields preserved (planned_risk, maximum_risk, risk_plan_status)
7. Identity preserved according to 14.2 (deterministic, changes with instrument/geometry)
8. Content fingerprint preserved (deterministic, changes with economic content, ignores timestamps)
9. No TradePlan mutation (fields unchanged after creation)
10. No MarketScanResult interaction (no import, no access)
11. No PaperTrade interaction (no paper_trade_id attribute)
12. No market-data interaction (no candle access)
13. No authorization interaction (no authorization_id, no approved)
14. No execution interaction (no execution_command_id, no broker_order_id)
15. Stateless repeated calls (identical results, no mutable state)
16. Deterministic behavior (same inputs same identity, different label different identity)
17. Invalid TradePlan failure (non-VALID statuses rejected)
18. Invalid factory inputs (non-directional rejected, TradePlan constructor enforces)
19. Timestamp handling (required, preserved, naive rejected)
20. valid_until handling (None default, preserved, before created_at rejected)
21. Immutability (frozen, slots)
22. Metadata immutability (tuple, caller metadata used)
23. Warnings immutability (tuple, preserved from plan)
24. No hidden global state (two engines behave identically, no registry/cache)
25. No hidden persistence (no files written, intent returned not stored)

Plus additional coverage: operational context preservation, SHORT direction, label handling.

## 21. Test results

### Focused tests
```
python -m pytest tests/test_operational_trade_intent_engine.py -q
69 passed in 0.51s
```

### Model tests (14.2, unchanged)
```
python -m pytest tests/test_operational_trade_intent.py -q
125 passed in 0.36s
```

### Regression tests
```
python -m pytest tests/test_trade_planning.py tests/test_paper_trading.py tests/test_paper_trading_operations.py -q
350 passed, 1 warning in 10.15s
```

### Full suite
```
python -m pytest tests/ -q
5043 passed, 2 failed, 3 skipped, 1 warning in 327.25s
```

The 2 failures are pre-existing yfinance-related failures (identical to baseline). No new regressions.

## 22. Regression results

| Suite | Result |
|-------|--------|
| test_operational_trade_intent.py (14.2) | 125 passed |
| test_operational_trade_intent_engine.py (14.4) | 69 passed |
| test_trade_planning.py | 158 passed |
| test_paper_trading.py | 114 passed |
| test_paper_trading_operations.py | 78 passed |
| Full suite | 5043 passed, 2 pre-existing failures, 3 skipped |

Baseline was 4974 passed + 2 pre-existing failures + 3 skipped. Increase of 69 passes = exactly the new engine tests. No regressions.

## 23. Architectural invariants

| # | Invariant | Status |
|---|-----------|--------|
| 1 | TradePlan remains authoritative | VERIFIED |
| 2 | OperationalTradeIntent derived only from TradePlan | VERIFIED |
| 3 | PaperTrade remains independent | VERIFIED |
| 4 | OperationalTradeIntent is not authorization | VERIFIED |
| 5 | OperationalTradeIntent is not an order | VERIFIED |
| 6 | OperationalTradeIntent is not a position | VERIFIED |
| 7 | No broker integration exists | VERIFIED |
| 8 | No live execution path exists | VERIFIED |
| 9 | No analytical object is mutated | VERIFIED |
| 10 | No paper-trading object is mutated | VERIFIED |
| 11 | No hidden persistence exists | VERIFIED |
| 12 | No hidden market-data access exists | VERIFIED |
| 13 | No hidden clock dependency exists | VERIFIED |
| 14 | Identity remains deterministic per 14.2 | VERIFIED |
| 15 | Creation is explicit, not automatic | VERIFIED |

## 24. Limitations

- No authorization layer (future checkpoint)
- No execution path (future checkpoint)
- No broker integration (future checkpoint)
- No persistence (future checkpoint, if required)
- No dashboard/API integration (future checkpoint)
- No position/portfolio management (future checkpoint)
- No clock abstraction (caller supplies timestamps directly)
- The engine's direction check is defense-in-depth; the TradePlan constructor already enforces directional intent for VALID plans

## 25. Implementation decision

**Dedicated stateless engine with pure factory delegation.**

The engine is a thin, explicit boundary that:
1. Validates the input is a TradePlan
2. Validates preconditions (VALID status, LONG/SHORT direction)
3. Extracts fields verbatim
4. Accepts caller-supplied timestamps
5. Delegates to `create_intent_from_plan()`
6. Returns the immutable intent

This is the simplest implementation that preserves the established boundary. No abstractions were introduced merely because future execution will need them.

## 26. Final verdict

**PASS**

All success criteria met:
1. `OperationalTradeIntentEngine` exists
2. It consumes TradePlan
3. It delegates authoritative construction to `create_intent_from_plan()`
4. Creation is explicit
5. No frozen subsystem is modified
6. No automatic intent creation occurs
7. No market data is accessed
8. No PaperTrade dependency is introduced
9. No authorization is introduced
10. No execution is introduced
11. No persistence is introduced
12. Identity contract remains intact
13. Immutability is preserved
14. Focused tests pass (69/69)
15. Regression tests pass (no new failures)
16. Documentation is complete
17. AGENTS.md is appended correctly
