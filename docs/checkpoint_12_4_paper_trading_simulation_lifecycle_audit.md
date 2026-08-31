Checkpoint 12.4 — Paper Trading Simulation Lifecycle & Integrity Audit

Purpose
-----------------------------------------------------------

This audit establishes the exact behavior of the Paper Trading
subsystem as a SIMULATION layer. It traces the actual source code to
determine what Paper Trading consumes, how a PaperTrade is created,
how the simulated lifecycle works, how market prices are consumed,
and whether the simulation introduces hidden execution semantics.

The audit verifies that Paper Trading is strictly a SIMULATION layer
and NOT an EXECUTION layer, and that the TradePlan remains
authoritative.

Scope
-----------------------------------------------------------

Exact files inspected:

- src/engine/models/paper_trade.py (PaperTrade model, state enums)
- src/engine/intelligence/paper_trading.py (lifecycle engine)
- src/engine/intelligence/paper_trading_serialization.py (persistence)
- src/engine/config/paper_trade_config.py (configuration)
- src/engine/models/trade_plan.py (TradePlan, the planning artifact)
- src/dashboard/paper_trade_store.py (filesystem persistence)
- src/dashboard/paper_trade_operations.py (operational orchestration)
- src/dashboard/services.py (dashboard service layer)
- src/dashboard/views.py (presentation layer)
- tests/test_paper_trading.py (114 tests)
- tests/test_paper_trading_operations.py (78 tests)
- tests/test_run_paper_trading_cycle.py (39 tests)
- tests/test_live_paper_validation.py (99 tests)

Test results:

- tests/test_paper_trading.py: 114 passed
- tests/test_paper_trading_operations.py: 78 passed
- tests/test_run_paper_trading_cycle.py: 39 passed
- tests/test_live_paper_validation.py: 99 passed
- Total: 330 passed

Paper Trading architecture
-----------------------------------------------------------

The Paper Trading subsystem is Product Phase 5 of the Trading
Intelligence Engine. It is a SIMULATION / VALIDATION /
RECORDING layer. It is NOT a decision engine, NOT a broker, NOT a
prediction system, NOT an execution engine.

The architectural position is:

```
MarketScanResult
      |
Trade Planning
      |
TradePlan
      |
Paper Trading  <-- SIMULATION (this layer)
      |
PaperTrade
      |
PaperTradeStore
      |
PaperTradeView / Journal / Performance Analytics
```

The conceptual boundary is:

```
ANALYSIS     -> MarketScanResult
PLANNING     -> TradePlan
SIMULATION   -> PaperTrade
EXECUTION    -> Future boundary (NOT implemented)
```

These layers are NOT collapsed. Paper Trading consumes TradePlan
outputs but does not modify them.

Actual data flow
-----------------------------------------------------------

1. Market data is fetched through the existing Product Phase 1
   provider abstraction (DashboardDataProvider).
2. The existing MarketScanner produces an InstrumentScanResult
   containing the reused TradeDecision, TradeCandidate geometry, and
   TradeOpportunity.
3. The existing Product Phase 4 TradePlanningEngine computes a
   TradePlan from the geometry + user account parameters.
4. PaperTradingOperations.run_once orchestrates: advance existing
   trades, then create new trades from eligible opportunities.
5. PaperTradingEngine.create builds a PaperTrade from the existing
   decision + geometry + plan (all by value copy).
6. PaperTradingEngine.track advances the lifecycle using completed
   candles only.
7. PaperTradeStore persists each state transition atomically.

PaperTrade creation boundary
-----------------------------------------------------------

What creates a PaperTrade?

PaperTradingEngine.create(...) is the single factory. It is called
by:
- DashboardAnalysisService.create_paper_trade (API path)
- PaperTradingOperations._create_eligible_trade (operational path)

What inputs are required?

Required:
- instrument: str
- timeframe: str
- direction: str ("LONG" / "SHORT")
- created_at: datetime (caller-supplied, deterministic)

Optional (with defaults):
- existing_decision: str (default "")
- setup_type: str (default "")
- plan: TradePlan | None (default None)
- plan_id: str (default "")
- entry, stop, target_1: Decimal | None
- engine_risk_distance, engine_reward_distance,
  engine_risk_reward_ratio: Decimal | None
- planned_quantity, planned_risk, maximum_risk, account_capital,
  risk_percent: Decimal | None
- label, metadata, sequence

Does creation consume TradePlan directly?

YES. The create method accepts a plan argument (a TradePlan or
TradePlanView) and pulls fields from it via getattr. Explicit kwargs
override plan fields. The plan object is never modified.

Which TradePlan fields are copied?

From the plan object (or explicit kwargs):
- direction
- entry (from plan.entry)
- stop (from plan.stop)
- target_1 (from plan.target_1)
- engine_risk_distance (from plan.engine_risk_distance)
- engine_reward_distance (from plan.engine_reward_distance)
- engine_risk_reward_ratio (from plan.engine_risk_reward_ratio)
- planned_quantity (from plan.quantity)
- planned_risk (from plan.planned_risk)
- maximum_risk (from plan.maximum_risk)
- account_capital (from plan.account_capital)
- risk_percent (from plan.risk_percent)
- plan_id (from plan.plan_id)
- setup_type (from plan.setup_type, if available)

Are they copied by value?

YES. All numeric fields are converted to Decimal via _to_decimal,
which creates new Decimal instances. Strings are immutable in Python.
The metadata tuple is rebuilt as a new sorted tuple. The plan object
itself is retained by reference in the operations layer but the
PaperTrade only stores the extracted values.

Is the original TradePlan retained?

YES. The TradePlan is never modified. The create method only reads
from it via getattr. The original plan object continues to exist
unchanged.

Is any analytical object retained?

The PaperTrade model stores only scalar values copied from the
analytical objects. It does NOT retain references to TradePlan,
TradeCandidate, TradeDecision, or TradeOpportunity objects. This
means a serialized PaperTrade is fully self-contained for audit.

TradePlan fidelity
-----------------------------------------------------------

Audit of which TradePlan fields survive into PaperTrade:

| TradePlan field | PaperTrade field | Survives? |
| --------------- | ---------------- | --------- |
| plan_id | plan_id | YES |
| instrument | instrument | YES |
| timeframe | timeframe | YES |
| direction | direction | YES |
| existing_decision | existing_decision | YES |
| actionability | (not stored) | NO |
| account_capital | account_capital | YES |
| risk_percent | risk_percent | YES |
| maximum_risk | maximum_risk | YES |
| entry | entry | YES |
| stop | stop | YES |
| target_1 | target_1 | YES |
| engine_risk_distance | engine_risk_distance | YES |
| engine_reward_distance | engine_reward_distance | YES |
| engine_risk_reward_ratio | engine_risk_reward_ratio | YES |
| target_2 | target_2 (always None) | NO (unsupported) |
| target_2_supported | target_2_supported (always False) | NO (unsupported) |
| quantity | planned_quantity | YES |
| planned_risk | planned_risk | YES |
| planned_reward | (not stored) | NO |
| quantity_status | (not stored) | NO |
| risk_plan_status | (not stored) | NO |
| quantity_spec_available | (not stored) | NO |
| warnings | warnings (new tuple) | PARTIAL |
| rationale | (not stored) | NO |
| label | label | YES |
| metadata | metadata | YES |

Information loss:

- actionability: Not stored on PaperTrade. The PaperTrade records
  the existing_decision (REJECTED/WATCH/QUALIFIED/PREFERRED) which
  is the authoritative decision classification. The actionability
  mirror is a presentation concern, not a simulation input.
- planned_reward: Not stored. This is a deterministic planning
  output (quantity * reward_distance) that can be recomputed from
  planned_quantity + engine_reward_distance if needed.
- quantity_status, risk_plan_status, quantity_spec_available: Not
  stored. These describe the planning computation, not the
  simulation. A PaperTrade is created only when the plan produced
  a usable geometry (entry/stop/risk_distance present and
  positive).
- rationale: Not stored. Human-readable planning summary, not a
  simulation input.
- warnings: The PaperTrade has its own warnings field, but it is
  initialized to () at creation, not copied from the plan.

This information loss is intentional: PaperTrade records the
geometry and plan values needed for simulation, not the full
planning metadata.

Source-of-truth audit
-----------------------------------------------------------

Does PaperTrade ever recalculate quantity, risk, reward, entry,
stop, or target?

NO. The PaperTradingEngine.create method copies these values
verbatim from the supplied plan or explicit kwargs. The engine
performs NO new position sizing. The _realized_r and _realized_pnl
functions compute simulation RESULTS (from entry/exit/risk/
quantity), not planning values.

The only computation in the engine is:
- realized_r = (exit - entry) / risk  (LONG)
- realized_r = (entry - exit) / risk  (SHORT)
- realized_pnl = (exit - entry) * quantity  (LONG)
- realized_pnl = (entry - exit) * quantity  (SHORT)

These are simulation outputs derived from the planned entry/stop/
quantity and the observed exit. They do NOT modify the planning
values.

The TradePlan remains the single source of truth for:
- quantity (planned_quantity)
- risk (engine_risk_distance)
- reward (engine_reward_distance)
- entry
- stop
- target

PaperTrade does NOT silently create a second risk-planning source
of truth.

State machine
-----------------------------------------------------------

The PaperTrade state machine has 5 states:

```
WAITING_FOR_ENTRY
    |-- entry confirmed by completed candle --> OPEN
    |-- human cancels --> CANCELLED
    |-- (no transition: INVALIDATED at creation if no geometry)

OPEN
    |-- stop hit first --> CLOSED (STOP_HIT)
    |-- target hit first --> CLOSED (TARGET_HIT)
    |-- same candle both --> CLOSED (BOTH_TOUCHED)
    |-- max_holding_bars elapsed --> CLOSED (EXPIRED)
    |-- human manually closes --> CLOSED (MANUAL_CLOSE)

CLOSED  (terminal - immutable)
CANCELLED (terminal - immutable)
INVALIDATED (terminal - immutable)
```

State transition diagram:

```
                  +-----------------+
                  |   creation      |
                  +--------+--------+
                           |
                  +--------v--------+
                  | WAITING_FOR_ENTRY| <-- (no geometry at creation)
                  +--------+--------+    goes to INVALIDATED
                    |      |      |
    entry confirmed |      |      | human cancels
                    v      |      v
              +----+----+  |  +---------+
              |  OPEN   |  |  |CANCELLED|
              +----+----+  |  +---------+
                |  |  |    |
    stop hit    |  |  |    |  target hit
                v  |  |    v
         +------+ |  | +------+
         |CLOSED| |  | |CLOSED|
         |STOP  | |  | |TARGET|
         +------+ |  | +------+
                  |  |
         both hit |  | expired
                  v  v
              +---------+
              | CLOSED  |
              |BOTH/EXP |
              +---------+

              +-----------+
              |INVALIDATED| (terminal, set at creation if no geometry)
              +-----------+
```

States and their properties:

| State | Terminal | Entry state | Exit state |
| ----- | -------- | ----------- | ---------- |
| WAITING_FOR_ENTRY | NO | None | None |
| OPEN | YES (populated) | entry_timestamp + actual_entry_price | None |
| CLOSED | YES (populated) | entry_timestamp + actual_entry_price | exit_timestamp + actual_exit_price + exit_reason + realized_r + realized_pnl |
| CANCELLED | YES | None | exit_reason=CANCELLED |
| INVALIDATED | YES | None | exit_reason=NO_GEOMETRY |

Allowed transitions:

| From | To | Trigger |
| ---- | -- | ------- |
| WAITING_FOR_ENTRY | OPEN | Completed candle touches entry after created_at |
| WAITING_FOR_ENTRY | CANCELLED | Human calls cancel() |
| OPEN | CLOSED | Stop/target/expired/manual close |
| CLOSED | (none) | Terminal |
| CANCELLED | (none) | Terminal |
| INVALIDATED | (none) | Terminal |

Illegal transitions are rejected:

- close_manually() on a non-OPEN trade raises ValueError
- close_manually() on a terminal trade raises ValueError
- cancel() on a non-WAITING_FOR_ENTRY trade raises ValueError
- track() on a terminal trade returns it unchanged (no error)

Entry simulation
-----------------------------------------------------------

How does a WAITING_FOR_ENTRY trade become OPEN?

The _track_entry method in PaperTradingEngine:

1. Filters completed candles to those strictly after
   trade.created_at (no look-ahead at creation candle).
2. Limits to max_entry_bars candles (default 20).
3. For each candidate candle, checks _entry_touched:
   - LONG: candle.low <= entry_reference
   - SHORT: candle.high >= entry_reference
4. On first touch, returns a new PaperTrade with:
   - status = OPEN
   - entry_timestamp = candle.timestamp
   - actual_entry_price = entry_reference (the planned entry)
5. If no touch within max_entry_bars, returns the trade unchanged
   (still WAITING_FOR_ENTRY).

Key behaviors:
- Entry uses candle LOW for LONG (price came down to entry).
- Entry uses candle HIGH for SHORT (price came up to entry).
- Entry price is the planned entry_reference, NOT the candle
  open/close/low/high. This is a limit-order-style fill at the
  structural level.
- The candle OPEN price does NOT trigger entry. Only low (LONG)
  or high (SHORT) matters.
- If price gaps through entry (e.g., candle opens below entry for
  LONG), entry still triggers because low <= entry is satisfied.
- Entry is NOT confirmed on the creation candle itself (c.timestamp
  > trade.created_at is required).

Stop simulation
-----------------------------------------------------------

For LONG:
- Stop touched when candle.low <= stop
- Evaluated against OHLC (specifically LOW), not close only.

For SHORT:
- Stop touched when candle.high >= stop
- Evaluated against OHLC (specifically HIGH), not close only.

Target simulation
-----------------------------------------------------------

For LONG:
- Target touched when candle.high >= target
- Evaluated against OHLC (specifically HIGH), not close only.

For SHORT:
- Target touched when candle.low <= target
- Evaluated against OHLC (specifically LOW), not close only.

Long/short symmetry is verified:
- LONG: target = high >= target, stop = low <= stop
- SHORT: target = low <= target, stop = high >= stop

Ambiguous candle behavior
-----------------------------------------------------------

Case: same candle touches both STOP and TARGET.

The _resolve_exit method handles this:

1. The engine tracks first_target_bar and first_stop_bar (the index
   of the first candle touching each).
2. If both are the SAME index (same candle), the result is
   BOTH_TOUCHED.
3. BOTH_TOUCHED carries:
   - actual_exit_price = None
   - realized_r = None
   - realized_pnl = None
   - exit_reason = BOTH_TOUCHED

Classification: DETERMINISTIC and CONSERVATIVE.

The rule is deterministic because the engine compares bar indices
(first_target_bar == first_stop_bar). It is conservative because
it does NOT manufacture a winner or loser when intrabar ordering
is unknown. This is a documented simulation-model limitation: the
system cannot know which level was touched first within a single
completed candle without tick data.

Gap behavior
-----------------------------------------------------------

LONG gap scenarios:

1. Candle opens below stop (gap down through stop):
   - Stop is touched (low <= stop is satisfied).
   - If this is the first candle after entry and target is NOT
   touched, result is STOP_HIT at the stop level.

2. Candle opens above target (gap up through target):
   - Target is touched (high >= target is satisfied).
   - If this is the first candle after entry and stop is NOT
   touched, result is TARGET_HIT at the target level.

3. Candle jumps across entry (e.g., opens below entry for LONG):
   - Entry is triggered (low <= entry is satisfied).
   - Entry price is the planned entry_reference, not the open.

SHORT gap scenarios (mirror):

1. Candle opens above stop (gap up through stop):
   - Stop is touched (high >= stop is satisfied).

2. Candle opens below target (gap down through target):
   - Target is touched (low <= target is satisfied).

3. Candle jumps across entry (e.g., opens above entry for SHORT):
   - Entry is triggered (high >= entry is satisfied).

The simulated fill price is ALWAYS the planned entry/stop/target
level, never the opening price or some other price. This is a
limit-order-style fill model.

Market price consumption
-----------------------------------------------------------

Price source: Completed OHLCV candles supplied by the caller.

Candle source: The operations layer fetches candles from the
DashboardDataProvider (fixture or Yahoo).

Timeframe: The setup timeframe from the analysis (e.g., 15m).

Timestamp: Each candle has a timestamp. The engine filters to
candles with timestamp <= reference_now.

OHLC usage:
- OPEN: Not used for entry/exit detection.
- HIGH: Used for SHORT entry, LONG target, SHORT stop.
- LOW: Used for LONG entry, SHORT target, LONG stop.
- CLOSE: Used only for EXPIRED mark-to-close exit price.

Completed candles required: YES. The _completed_window helper
filters to candles with timestamp <= reference_now. Forming
candles (timestamp > reference_now) are excluded.

Intrabar data: NOT used. The simulation operates on completed
candle OHLC only.

Future candles: NEVER consumed. The _completed_window filter
excludes candles with timestamp > reference_now.

Current/live prices: NOT used directly. The simulation uses
completed candles only. The latest completed candle timestamp
is the reference boundary.

Point-in-time / look-ahead audit
-----------------------------------------------------------

This is a critical audit area.

The simulation is POINT-IN-TIME SAFE.

Evidence:

1. Entry detection uses candles strictly after trade.created_at
   and up to reference_now. The creation candle itself is never
   used for entry.

2. Exit detection uses candles strictly after the entry candle
   and up to reference_now.

3. The _completed_window helper filters: _naive(c.timestamp) <=
   _naive(reference_now). Candles with timestamp > reference_now
   are excluded.

4. Terminal trades (CLOSED/CANCELATED/INVALIDATED) are returned
   UNCHANGED by track(). A previously resolved trade is never
   altered by future candles.

5. The track() method accepts an explicit reference_now
   parameter. It does NOT read wall-clock time.

6. The engine NEVER calls OutcomeEvaluator.evaluate or
   HistoricalEvaluationPipeline.evaluate. The touch logic is
   implemented directly in the paper-trading engine.

7. The public APIs accept NO future / future_candles argument.

8. The operations layer uses min(cycle reference_now, provider
   latest_completed) as the tracking boundary, so a
   caller-supplied reference_now is never bypassed by a fresher
   provider candle.

Temporal ordering:

```
event timestamp (candle close)
        |
        v
market data timestamp (candle.timestamp)
        |
        v
decision timestamp (reference_now >= candle.timestamp)
        |
        v
state transition timestamp (candle.timestamp of the triggering candle)
```

A simulation decision at timestamp T uses only candles with
timestamp <= T. No information known after T can influence the
state at T.

Fees / slippage / spread
-----------------------------------------------------------

Brokerage fees: NOT modeled.
Exchange fees: NOT modeled.
Taxes: NOT modeled.
Spread: NOT modeled.
Slippage: NOT modeled.
Latency: NOT modeled.

These features are ABSENT. The absence affects simulation
interpretation: simulated P&L is idealized (no transaction costs).
This is a documented simulation limitation, not a bug. The system
explicitly states that paper trading is observational validation
and does not model real execution costs.

P&L / performance calculations
-----------------------------------------------------------

Realized P&L (Decimal):

```
LONG  realized_pnl = (exit - entry) * quantity
SHORT realized_pnl = (entry - exit) * quantity
```

Realized R (Decimal):

```
risk = abs(entry - stop)
LONG  realized_r = (exit - entry) / risk
SHORT realized_r = (entry - exit) / risk
```

Return: Not explicitly calculated as a percentage. The realized_r
is the R-multiple, which is the return relative to the planned
risk.

Risk/reward result: The realized_r IS the risk/reward result. A
value of +1.0 means the trade gained exactly the planned risk
amount. A value of -1.0 means the trade lost exactly the planned
risk amount.

Closure result: The exit_reason enum describes the closure:
- TARGET_HIT: favorable, determinate
- STOP_HIT: adverse, determinate
- BOTH_TOUCHED: ambiguous, realized_r/realized_pnl = None
- MANUAL_CLOSE: human action, determinate
- EXPIRED: mark-to-close, determinate

P&L is a simulation result. It is:
- Separate from TradePlan (planned_risk/planned_reward are
  planning values, not simulation results).
- Derived from actual simulated prices (entry/exit/stop/target).
- Deterministic (same inputs produce same outputs).
- Directionally correct (LONG gains when exit > entry, SHORT
  gains when exit < entry).

P&L is NOT fed backward into:
- MarketScanResult
- TradePlan
- TradeCandidate
- TradeDecision
- TradeOpportunity
- Setup detection
- Historical research
- Strategy optimization

Timestamp audit
-----------------------------------------------------------

All timestamps in the paper-trade system are datetime objects.
The engine uses a _naive helper that strips tzinfo for comparison:

```python
def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)
```

This means:
- Timezone-aware and naive datetimes can be compared (aware is
  converted to naive for comparison).
- The original datetime objects are preserved on the model.
- Timestamps are caller-supplied (no wall-clock reads in tests).

Timestamps tracked:
- created_at: Paper-trade creation time (caller-supplied)
- evaluation_timestamp: Market evaluation time at creation
- entry_timestamp: When entry was confirmed (candle timestamp)
- exit_timestamp: When the trade was resolved (candle timestamp)

Timezone handling: The system does NOT enforce UTC. It accepts
any datetime and compares them naively. This is a potential
concern for mixed-timezone usage, but in practice all timestamps
come from the same source (the data provider) and are consistent.

Persistence audit
-----------------------------------------------------------

Storage format: JSON, one file per paper trade.
File naming: <directory>/<paper_trade_id>.json
Atomicity: YES. Uses tempfile.mkstemp in the same directory,
write, flush, fsync (best-effort), then os.replace (atomic
rename on Windows and POSIX).

Read/write behavior:
- save(): Serializes to JSON, writes atomically.
- load(): Reads JSON, deserializes, verifies id matches filename.
- load_all(): Lists all trades, loads each.
- delete(): Unlinks the file.

Duplicate handling: The save method has an overwrite parameter.
Default is overwrite=True because paper trades are updated through
their lifecycle. The operations layer checks for duplicates
before creation using deterministic IDs.

Corruption handling:
- Corrupted JSON raises PaperTradeIntegrityError.
- Schema version mismatch raises ValueError (rejected before
  model reconstruction).
- Filename-id mismatch raises PaperTradeIntegrityError.
- Missing record raises PaperTradeNotFoundError.

Schema versioning: YES. PAPER_TRADE_SCHEMA_VERSION = 1. The
deserializer checks schema version before reconstruction.

Serialization: Deterministic sorted-key JSON. Decimal values
stored as strings. Datetimes stored as ISO format. Enums stored
by name with class qualification.

Recovery behavior: Paper trades survive process restart. The
operations layer loads existing WAITING_FOR_ENTRY/OPEN trades
from the store and continues tracking them.

Important TradePlan-derived information that survives persistence:
- entry, stop, target_1
- engine_risk_distance, engine_reward_distance,
  engine_risk_reward_ratio
- planned_quantity, planned_risk, maximum_risk
- account_capital, risk_percent
- plan_id
- existing_decision, setup_type

All numeric values are preserved as Decimal strings in JSON.

Mutation audit
-----------------------------------------------------------

Does Paper Trading mutate TradePlan?

NO. The create method reads from the plan via getattr but never
writes to it. The TradePlan is a frozen dataclass (frozen=True,
slots=True), so it cannot be mutated even if the code attempted
to.

Does Paper Trading mutate MarketScanResult, TradeCandidate,
TradeDecision, TradeOpportunity?

NO. These objects are never referenced by the paper-trading
engine. The engine only receives scalar values copied from them.

Does Paper Trading mutate its OWN lifecycle state?

YES, but only by creating NEW immutable instances. The track(),
close_manually(), and cancel() methods use dataclasses.replace()
to create a new PaperTrade with updated fields. The original
PaperTrade is never mutated (it is frozen).

The desired flow is maintained:

```
TradePlan
|
└── value copy
     |
     v
PaperTrade
     |
     v
lifecycle updates (new instances via replace)
```

Duplicate calculation audit
-----------------------------------------------------------

Search for duplicate implementations of position quantity, risk,
entry, stop, target, reward, direction:

- quantity: TradePlan computes quantity. PaperTrade stores it as
  planned_quantity. PaperTrade does NOT recompute quantity.
- risk: TradePlan provides engine_risk_distance. PaperTrade
  stores it. The _realized_r function computes risk from
  abs(entry - stop), which should equal engine_risk_distance
  for a valid plan. No duplicate risk calculation with different
  rules.
- reward: TradePlan provides engine_reward_distance. PaperTrade
  stores it. Not recomputed.
- entry/stop/target: Copied verbatim from TradePlan. Not
  recomputed.
- direction: Copied verbatim from TradePlan. Not recomputed.

The system does NOT have duplicate planning logic. PaperTrade
reuses TradePlan values verbatim.

Actionability / execution boundary
-----------------------------------------------------------

Does Paper Trading interpret READY_FOR_REVIEW or
RiskPlanStatus.VALID as permission to execute?

NO. Paper Trading is SIMULATION. The READY_FOR_REVIEW
actionability state is used as an eligibility gate for creating
a paper trade (a simulated record), NOT as permission to place
a real order.

The RiskPlanStatus.VALID is a planning status indicating the risk
calculation produced a usable position size. It is NOT an
execution signal.

No broker/order API is called. No real order is placed. No
execution layer exists.

The clean boundary is:
- Paper Trading creates a SIMULATED record (PaperTrade).
- Paper Trading tracks the SIMULATED lifecycle against completed
  candles.
- Paper Trading records SIMULATED results (realized_r,
  realized_pnl).
- Paper Trading NEVER places a real order.

Error handling
-----------------------------------------------------------

TradePlan is invalid:
- PaperTrade is created with INVALIDATED status and
  exit_reason=NO_GEOMETRY. No entry/exit/P&L/R is fabricated.

quantity is missing:
- planned_quantity is stored as None. P&L calculations return
  None (not fabricated).

entry is missing:
- PaperTrade is INVALIDATED. No entry is fabricated.

stop is missing:
- PaperTrade is INVALIDATED. No stop is fabricated.

target is missing:
- PaperTrade can still be created (target_1 = None). The
  _track_exit method returns the trade unchanged if target is
  None (no fabricated exit).

duplicate trade is requested:
- The operations layer checks store.exists(candidate_id) and
  _find_existing_for_candle(). Returns duplicate=True, no
  re-creation.

nonexistent trade is updated:
- load() raises PaperTradeNotFoundError.

invalid state transition requested:
- close_manually() on non-OPEN raises ValueError.
- cancel() on non-WAITING_FOR_ENTRY raises ValueError.

malformed persistence data exists:
- deserialize_paper_trade raises ValueError.
- load() catches ValueError and raises
  PaperTradeIntegrityError.

market data is missing:
- _completed_window returns empty list. track() returns the
  trade unchanged.

market data timestamp is invalid:
- The _naive helper handles both aware and naive timestamps.
- Comparison is defensive.

Error behavior classification:
- Explicit: YES (ValueError for illegal transitions, typed
  errors for store issues).
- Silent: NO (no errors are swallowed silently).
- Partially applied: NO (state transitions are atomic via
  replace).
- Recoverable: YES (store errors are typed and the caller can
  decide).
- Destructive: NO (no state is lost on error).

Concurrency / duplicate handling
-----------------------------------------------------------

The current system assumes:
- Single-process: YES (no inter-process locking).
- Single-user: YES (no user authentication).
- Serialized operations: YES (no concurrent track() calls on the
  same trade).

Two simultaneous operations CAN:
- Create duplicate PaperTrades if they use different sequence
  numbers (different deterministic IDs).
- Update the same trade incorrectly if track() is called
  concurrently (last-write-wins on the atomic file write).

The system does NOT have:
- Distributed locking.
- Optimistic concurrency control.
- Version counters on PaperTrade records.

This is documented as a limitation: the system is designed for a
single-user personal trading workstation, not a multi-user
production trading platform.

Tests inspected
-----------------------------------------------------------

tests/test_paper_trading.py (114 tests, areas A-AL):
- Paper trade creation, invalid creation, state transitions
- Entry detection, waiting-for-entry, stop/target detection
- Manual close, BOTH_TOUCHED ambiguity, NO_GEOMETRY
- Missing geometry/target, decision/geometry/plan preservation
- Decimal accounting, realized P&L/R
- Aggregate performance, grouping (instrument/setup/decision/
  timeframe)
- Persistence, reload after restart, malformed data
- Deterministic serialization, future-candle protection,
  forming-candle protection
- No-look-ahead (patch-to-raise), input immutability, failure
  isolation
- Workstation integration, API schema, backward compatibility,
  empty database
- Regression against Product Phases 1-4 + Sprint 12C/12D/12E +
  pipeline baseline

tests/test_paper_trading_operations.py (78 tests, areas A-AJ):
- run_once happy path, live/fixture provider abstraction
- Completed/forming/future candle handling
- Trade creation, duplicate prevention, WAITING/OPEN tracking
- STOP_HIT/TARGET_HIT/BOTH_TOUCHED, manual close compatibility
- Persistence, restart recovery, multiple unseen candles,
  chronological processing
- Instrument/provider failure isolation, empty/malformed data
- Unsupported instrument/timeframe, deterministic cycle,
  shuffle invariance
- No-look-ahead, decision/geometry/plan preservation, Target 2
  unsupported
- API schema, workstation integration, reporting, pipeline
  baseline

tests/test_run_paper_trading_cycle.py (39 tests):
- Argument parsing/defaults, provider-env defaulting
- Formatter sections/banner/duplicate/error/empty/determinism
- Exit behavior, end-to-end fixture cycle, deterministic cycle
  id
- No-look-ahead verification

tests/test_live_paper_validation.py (99 tests, areas A-J):
- Observation model, point-in-time correctness
- Historical evidence (AVAILABLE/NO_MATCH/RESEARCH_UNAVAILABLE)
- Decision preservation, paper trading lifecycle
- Outcomes, failure isolation, persistence
- Multi-instrument, regression

Coverage gaps:

1. No test for mixed-timezone timestamp handling (all tests use
   consistent timezone-aware or naive timestamps).
2. No test for concurrent access to the same paper trade (by
   design, single-process assumption).
3. No test for very large max_entry_bars / max_holding_bars
   values (edge case, not a defect).
4. No test for partial fill simulation (not supported by the
   model; partial fills are not represented).

Missing cases that are NOT defects:
- Partial/ambiguous fills: The model does NOT support partial
  fills. A trade either enters at the planned entry or remains
  waiting. This is a deliberate simplification.
- Slippage/fees/spread: Not modeled by design.

Limitations
-----------------------------------------------------------

Documented simulation limitations:

1. No transaction costs: Brokerage, exchange fees, taxes, spread,
   slippage, and latency are NOT modeled. Simulated P&L is
   idealized.

2. Limit-order fill model: Entry fills occur at the planned
   entry price, stop/target fills at the planned levels. No
   slippage is modeled.

3. No partial fills: A trade either enters fully at the planned
   entry or remains waiting. Partial position building is not
   supported.

4. Single-candle ambiguity: When a single candle touches both
   stop and target, the outcome is BOTH_TOUCHED (ambiguous). The
   system does not model intrabar ordering.

5. No portfolio management: Paper trades are tracked
   independently. No correlation, no position sizing across
   simultaneous trades, no portfolio heat.

6. No strategy optimization: The system records what would have
   happened; it does not optimize entry/exit rules.

7. Single-process assumption: No concurrent access protection.

8. Completed-candle only: The simulation does not use intrabar
   tick data or real-time price streams.

Implementation decision
-----------------------------------------------------------

This is an AUDIT checkpoint. The implementation is sound.

No genuine defects were discovered. The Paper Trading subsystem:
- Faithfully preserves TradePlan values (by value copy).
- Maintains a clean SIMULATION boundary (no execution).
- Is point-in-time safe (no look-ahead).
- Has a well-defined state machine with illegal transitions
  rejected.
- Uses deterministic, atomic persistence.
- Has comprehensive test coverage (330 tests passing).

The limitations are documented and intentional.

Final verdict
-----------------------------------------------------------

PASS
