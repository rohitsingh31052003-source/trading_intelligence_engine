# Checkpoint 13.4 — Execution Authorization → Execution Command Boundary Audit & Design

## 1. Purpose

Determine what exact information is permitted to cross the boundary from **Execution Authorization** to a future **Execution Command** layer, and what transformations are prohibited. This checkpoint defines and audits the conceptual boundary only.

The central safety invariant is:

```
AUTHORIZED INTENT
        ↓
EXECUTION COMMAND
```

must NOT silently become:

```
AUTHORIZED INTENT A
        ↓
MATERIALLY DIFFERENT EXECUTION COMMAND B
```

without a new authorization/re-authorization boundary.

This is an **audit + contract design only** checkpoint. No implementation changes are made.

## 2. Scope

- Inspect the repository for any existing Execution Command, broker, or order abstractions.
- Audit the Execution Authorization contract established in Checkpoint 13.3.
- Audit the Operational Trade Intent contract established in Checkpoint 13.2.
- Audit the TradePlan contract (frozen, Product Phase 4).
- Determine the boundary subject: what Execution Command should consume.
- Perform a field-by-field crossing audit.
- Define analytical data protection, planning data protection, and transformation boundaries.
- Define price precision, quantity precision, order-type, and broker-specific translation boundaries.
- Define authorization binding, expiry, re-authorization, command identity, immutability, idempotency, and replay protection.
- Define live/paper separation, account binding, session binding, temporal safety, and fail-closed behavior.
- Define the responsibility matrix, future Execution Command contract, lifecycle, and test contract.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Boundary |
| -----------|--------|----------|
| 11.1–11.8 | FROZEN | Market data → candle integrity → features → setup → evidence → `MarketScanResult` |
| 12.1–12.6 | FROZEN | `MarketScanResult` → `TradePlan` → `PaperTrade` → journal/performance → dashboard |
| 13.1 | ACCEPTED | No operational trade intent, execution path, broker order path, or authorization currently exists |
| 13.2 | ACCEPTED | Operational Trade Intent contract defined (not implemented) |
| 13.3 | ACCEPTED | Execution Authorization contract defined (not implemented) |

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
- `src/engine/intelligence/trade_candidates.py` — `TradeCandidateEngine`
- `src/engine/intelligence/trade_decision.py` — `TradeDecisionEngine`
- `src/engine/intelligence/trade_opportunity.py` — `TradeOpportunityEngine`
- `src/engine/intelligence/market_scanner.py` — `MarketScanner`

### Dashboard / Presentation
- `src/dashboard/views.py` — `ActionabilityState`, `derive_actionability`, `DashboardTradeView`, `WorkstationView`
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
- `docs/checkpoint_13_3_execution_authorization_boundary_audit.md`

### Tests (baseline only — not modified)
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests
- `tests/test_dashboard.py` — 67 tests
- `tests/test_workstation.py` — 95 tests
- `tests/test_watchlist_scanner.py` — 75 tests

## 5. Existing Execution-Command-Like Abstractions

### Search Results

The following keywords were searched across the entire codebase:

| Keyword | Result |
|---------|--------|
| `execution_command`, `ExecutionCommand` | **No classes found.** Mentioned only in Checkpoint 13.3 audit as a future concept. |
| `execution_request`, `ExecutionRequest` | **No classes found.** |
| `order_request`, `OrderRequest` | **No classes found.** No order model exists. |
| `order_intent`, `OrderIntent` | **No classes found.** |
| `broker_request`, `BrokerRequest` | **No classes found.** |
| `submit_order`, `SubmitOrder` | **No classes found.** |
| `place_order`, `PlaceOrder` | **No classes found.** |
| `send_order`, `SendOrder` | **No classes found.** |
| `broker_adapter`, `BrokerAdapter` | **No classes found.** Mentioned in Checkpoint 13.3 as a future responsibility. |
| `broker_client`, `BrokerClient` | **No classes found.** |
| `order_payload`, `OrderPayload` | **No classes found.** |
| `order_instruction`, `OrderInstruction` | **No classes found.** |
| `execution_instruction`, `ExecutionInstruction` | **No classes found.** |
| `live_order`, `LiveOrder` | **No classes found.** |
| `order_lifecycle`, `OrderLifecycle` | **No classes found.** |
| `position_creation`, `PositionCreation` | **No classes found.** |
| `fill` (order fill) | **No classes found.** |
| `account_execution`, `AccountExecution` | **No classes found.** |
| `routing` (exchange routing) | **No classes found.** |
| `exchange_routing`, `ExchangeRouting` | **No classes found.** |
| `product_type`, `ProductType` | **No classes found.** |
| `validity` (order validity) | **No classes found.** |
| `trigger_price`, `TriggerPrice` | **No classes found.** |
| `limit_price`, `LimitPrice` | **No classes found.** |
| `stop_price`, `StopPrice` | **No classes found.** |
| `execution` (trade execution) | **No classes found.** Occurs only in non-trading contexts (Windows `SetThreadExecutionState`, Task Scheduler `ExecutionTimeLimit`, "execution-timeframe" analysis references). |

### Conclusion

**No existing object in the repository serves the role of Execution Command.** The concept is unnamed and unimplemented. The repository is explicitly paper-trading-only. No execution path, broker integration, order model, or position model exists.

## 6. Existing Broker/Execution Abstractions

### Search Results

| Keyword | Result |
|---------|--------|
| `broker`, `Broker` | **No classes found.** |
| `order`, `Order` | **No classes found.** (Only "paper order" in docstrings, not a model.) |
| `position`, `Position` | **No classes found.** (Only in Checkpoint 13.3 responsibility matrix as a future layer.) |
| `account`, `Account` | **No classes found.** |
| `portfolio`, `Portfolio` | **No classes found.** |
| `exchange`, `Exchange` | **No classes found.** |
| `venue`, `Venue` | **No classes found.** |
| `clearing`, `Clearing` | **No classes found.** |
| `settlement`, `Settlement` | **No classes found.** |
| `commission`, `Commission` | **No classes found.** |
| `slippage`, `Slippage` | **No classes found.** |
| `spread`, `Spread` | **No classes found.** |
| `latency`, `Latency` | **No classes found.** |
| `margin`, `Margin` | **No classes found.** |
| `leverage`, `Leverage` | **No classes found.** |

### Conclusion

**No broker, order, position, account, portfolio, exchange, or execution abstraction exists.** The repository has no live trading capability. This is a clean baseline for defining the Execution Command boundary.

## 7. Boundary Subject

### Preferred Conceptual Flow

```
Operational Trade Intent
        ↓
Execution Authorization
        ↓
Execution Command
```

### What Should Execution Command Consume?

The audit evaluated five options:

| Option | Description | Verdict |
|--------|-------------|---------|
| **A.** Operational Trade Intent directly | Execution Command reads intent fields directly | **REJECTED** — bypasses authorization verification |
| **B.** Execution Authorization directly | Command reads authorization record directly | **PARTIAL** — authorization proves permission but does not carry intent geometry |
| **C.** Both intent and authorization | Command reads both independently | **ACCEPTABLE** — but requires explicit binding verification |
| **D.** Dedicated authorized-intent snapshot | Command consumes a single immutable snapshot that binds authorization + intent | **PREFERRED** — strongest safety |
| **E.** Another explicit immutable boundary | A new boundary object that couples authorization proof with intent snapshot | **ACCEPTABLE** — equivalent to D |

### Recommended: Option D — Dedicated Authorized-Intent Snapshot

The Execution Command should consume a **dedicated authorized-intent snapshot** that:

1. **References** the `authorization_id` (proof of valid authorization).
2. **References** the `intent_id` (the authorized intent).
3. **Carries** the authoritative intent fields by value (immutable snapshot).
4. **Includes** the content fingerprint (verification that intent has not changed).
5. **Binds** the execution mode (paper/live) from the authorization.

This prevents:

```
Authorization A → Intent B → Execution Command
```

because the snapshot is constructed only when authorization is valid for the exact intent, and the fingerprint verification ensures the intent has not been substituted.

### Safety Justification

- **Authorization alone is insufficient** — it proves permission but does not carry the geometric/operational fields needed for execution.
- **Intent alone is insufficient** — it carries fields but does not prove authorization.
- **A dedicated snapshot couples them** — the command layer receives a single immutable artifact that proves both "what is permitted" and "what is authorized."
- **The snapshot is immutable** — once constructed, it cannot be mutated to represent a different intent or authorization.

## 8. Source-of-Truth Analysis

### Authoritative Source for Each Execution-Relevant Field

| Field | Authoritative Source | Rationale |
|-------|---------------------|-----------|
| `instrument` | **TradePlan** (via Operational Trade Intent) | Canonical instrument name from planning layer |
| `timeframe` | **TradePlan** (via Operational Trade Intent) | Setup timeframe from planning layer |
| `direction` | **TradePlan** (via Operational Trade Intent) | LONG/SHORT from planning layer |
| `entry` | **TradePlan** (via Operational Trade Intent) | Engine geometry entry level |
| `stop` | **TradePlan** (via Operational Trade Intent) | Engine geometry stop level |
| `target` | **TradePlan** (via Operational Trade Intent) | Engine geometry target level |
| `quantity` | **TradePlan** (via Operational Trade Intent) | Position size from risk calculation |
| `planned_risk` | **TradePlan** (via Operational Trade Intent) | Planned loss from risk calculation |
| `maximum_risk` | **TradePlan** (via Operational Trade Intent) | Risk limit from risk calculation |
| `plan_id` | **TradePlan** | Planning-layer identity |
| `intent_id` | **Operational Trade Intent** | Operational-layer identity |
| `authorization_id` | **Execution Authorization** | Authorization-layer identity |
| `account_capital` | **TradePlan** | Account-level planning input |
| `risk_percent` | **TradePlan** | Account-level planning input |

### Critical Rule

**The execution layer MUST NOT silently recompute analytical or planning values.** Entry, stop, target, quantity, planned_risk, and maximum_risk are authoritative from TradePlan (carried via Operational Trade Intent). The execution layer may only apply **safe normalizations** (e.g., price tick rounding, quantity step rounding) that do not change the economic meaning. Any material change requires re-authorization.

## 9. Field-by-Field Crossing Audit

### Classification Categories

1. **MUST cross** — required for execution command construction
2. **MAY cross** — useful for execution context/audit
3. **MUST NOT cross** — must not enter execution command
4. **Must be transformed by execution layer** — requires conversion
5. **Must be generated by broker adapter** — broker-specific
6. **Requires re-authorization if changed** — material change

### Field Classification

| Field | Classification | Rationale |
|-------|---------------|-----------|
| `intent_id` | **MUST cross** | Identity of the authorized intent |
| `plan_id` | **MUST cross** | Provenance: source plan |
| `authorization_id` | **MUST cross** | Proof of valid authorization |
| `instrument` | **MUST cross** | Target instrument |
| `timeframe` | **MAY cross** | Operational context |
| `direction` | **MUST cross** | LONG/SHORT |
| `entry` | **MUST cross** | Entry price reference |
| `stop` | **MUST cross** | Stop price reference |
| `target` | **MUST cross** | Target price reference |
| `quantity` | **MUST cross** | Position size |
| `planned_risk` | **MUST cross** | Authorized risk amount |
| `maximum_risk` | **MUST cross** | Risk limit bound |
| `risk_percent` | **MAY cross** | Audit context |
| `account_capital` | **MAY cross** | Audit context |
| `actionability` | **MAY cross** | Audit context |
| `rationale` | **MAY cross** | Audit context |
| `warnings` | **MAY cross** | Audit context |
| `metadata` | **MAY cross** | Audit trail |
| `broker symbol` | **Must be generated by broker adapter** | Internal symbol → broker symbol translation |
| `exchange` | **Must be generated by broker adapter** | Broker-specific routing |
| `order type` | **Must be transformed by execution layer** | Analytical intent → execution order type |
| `product type` | **Must be generated by broker adapter** | Broker-specific product classification |
| `validity` | **Must be transformed by execution layer** | Execution-layer order validity |
| `routing` | **Must be generated by broker adapter** | Broker-specific exchange routing |
| `client order ID` | **Must be transformed by execution layer** | Execution-layer identity |
| `broker order ID` | **MUST NOT cross** | Generated by broker, not execution command |
| `trigger price` | **Must be transformed by execution layer** | Derived from analytical levels |
| `limit price` | **Must be transformed by execution layer** | Derived from analytical entry |
| `execution price` | **MUST NOT cross** | Determined by broker/market |
| `fill price` | **MUST NOT cross** | Determined by broker/market |
| `slippage` | **MUST NOT cross** | Absent by design (Checkpoint 12) |
| `fees` | **MUST NOT cross** | Absent by design (Checkpoint 12) |
| `position ID` | **MUST NOT cross** | Determined by broker |

### Material Change Classification

The following changes are **material** and require re-authorization:

| Field | Change | Classification |
|-------|--------|---------------|
| `quantity` | Changed | **MATERIAL** — changes economic exposure |
| `direction` | Changed | **MATERIAL** — reverses the trade |
| `entry` | Changed materially | **MATERIAL** — changes execution geometry |
| `stop` | Changed materially | **MATERIAL** — changes risk geometry |
| `target` | Changed materially | **MATERIAL** — changes reward geometry |
| `instrument` | Changed | **MATERIAL** — different asset |
| `order type` | Changed | **MATERIAL** — changes execution semantics |
| `product type` | Changed | **MATERIAL** — changes instrument type |
| `planned_risk` | Increased | **MATERIAL** — exceeds authorized risk |
| `account` | Changed | **MATERIAL** — different account |

## 10. Analytical Data Protection

### Fields That MUST NOT Cross into Execution

The following analytical artifacts must never enter the Execution Command layer:

| Artifact | Reason |
|----------|--------|
| `MarketScanResult` | Analytical scan output — execution must not consume scan internals |
| `TradeCandidate` | Analytical candidate — execution must not consume candidate internals |
| `TradeDecision` | Analytical decision — execution must not consume decision internals |
| `TradeOpportunity` | Analytical opportunity — execution must not consume opportunity internals |
| Feature values | Analytical features — execution must not consume feature internals |
| Chart structures | Analytical chart data — execution must not consume chart internals |
| Confluence evidence | Analytical evidence — execution must not consume evidence internals |
| Historical research outputs | Research artifacts — execution must not consume research internals |
| Scanner diagnostics | Scanner internals — execution must not consume diagnostics |

### Principle

The execution layer must not become an analytical consumer. Execution Command consumes only the **authorized operational snapshot** (intent fields + authorization proof). It must never reach back into `MarketScanResult`, `TradeCandidate`, `TradeDecision`, or `TradeOpportunity` directly.

## 11. Planning Data Protection

### Authoritative TradePlan Fields

The following TradePlan fields are authoritative and must be protected from silent recalculation:

| Field | Protection |
|-------|-----------|
| `entry` | Must not be recomputed by execution |
| `stop` | Must not be recomputed by execution |
| `target_1` | Must not be recomputed by execution |
| `quantity` | Must not be recomputed by execution |
| `planned_risk` | Must not be recomputed by execution |
| `maximum_risk` | Must not be recomputed by execution |
| `engine_risk_distance` | Must not be recomputed by execution |
| `engine_reward_distance` | Must not be recomputed by execution |
| `engine_risk_reward_ratio` | Must not be recomputed by execution |

### Critical Invariant

```
planned_risk <= maximum_risk
```

TradePlan currently guarantees this invariant through its quantity logic. The execution layer **MUST NOT** silently increase `planned_risk` beyond `maximum_risk`. A broker-side quantity transformation that would increase authorized risk is **prohibited** without re-authorization.

### Principle

If execution requires broker-specific transformations (tick rounding, quantity steps), those transformations must:
1. **Preserve or reduce** risk (never increase it).
2. **Be documented and auditable**.
3. **Fail closed** if they would violate the risk invariant.

## 12. Transformation Boundary

### Legitimate (Safe) Transformations

| Transformation | From | To | Safety |
|---------------|------|-----|--------|
| Instrument symbol | Internal symbol (`NIFTY`) | Broker symbol (`^NSEI`) | **SAFE** — same asset, different representation |
| Price precision | Analytical `Decimal` | Broker tick precision | **SAFE IF** rounding does not materially change geometry |
| Quantity representation | Planning `Decimal` | Broker-supported quantity | **SAFE IF** quantity does not increase risk |
| Entry reference | Analytical entry level | Limit/stop order price | **SAFE IF** derived from authorized entry |
| Direction | `LONG`/`SHORT` | Buy/Sell | **SAFE** — direct mapping |

### Prohibited (Material) Transformations

| Transformation | Reason |
|---------------|--------|
| Quantity changed (increased) | Changes economic exposure without authorization |
| Direction changed (LONG→SHORT) | Reverses the trade |
| Entry changed materially | Changes execution geometry |
| Stop changed materially | Changes risk geometry |
| Target changed materially | Changes reward geometry |
| Instrument changed | Different asset entirely |
| Order type changed (market→limit) | Changes execution semantics |
| Risk increased | Exceeds authorized risk bound |
| Account changed | Different account without authorization |

### Materiality Threshold

A transformation is **material** if it changes the economic meaning of the authorized intent. The following thresholds apply:

- **Price**: A price change is material if it exceeds half the tick size from the authorized level.
- **Quantity**: Any quantity change is material (no tolerance).
- **Direction**: Any direction change is material.
- **Instrument**: Any instrument change is material.
- **Risk**: Any risk increase is material.

## 13. Price Precision

### Future Execution Price Handling

The execution layer must handle:
- **Tick size**: minimum price increment for the instrument
- **Decimal precision**: number of decimal places
- **Price rounding**: rounding to tick precision
- **Minimum price increment**: smallest valid price change

### Critical Rule

**The execution layer MUST NOT silently round a price in a way that invalidates the authorization.**

### Recommended Price Normalization Location

Price normalization should happen **during execution-command construction**, not inside the broker adapter. This ensures:
1. The authorization can verify the normalized price matches the authorized level.
2. The broker adapter receives a clean, validated price.
3. Price normalization is auditable before the command is constructed.

### Recommended Flow

```
Analytical Decimal (entry/stop/target)
        ↓
Execution Command construction (normalize to tick precision)
        ↓
Verify normalized price ≈ authorized price (within tolerance)
        ↓
Broker adapter (translates to broker-specific format)
        ↓
Broker
```

### Tolerance Rule

Normalized prices must be within **half a tick** of the authorized price. If normalization would move the price more than half a tick, the command must fail closed (require re-authorization).

## 14. Quantity Precision

### Future Execution Quantity Handling

The execution layer must handle:
- **Integer quantity**: whole units
- **Fractional quantity**: partial units (if supported)
- **Quantity step**: minimum quantity increment
- **Minimum quantity**: smallest allowed order
- **Maximum quantity**: largest allowed order

### Critical Invariant

```
Executed quantity risk <= Authorized planned_risk <= maximum_risk
```

A broker-side quantity transformation MUST NOT silently increase authorized risk.

### Recommended Approach

1. **Preserve authorized quantity** as the primary quantity.
2. **If broker requires rounding**, round DOWN (floor) to the nearest valid quantity step.
3. **Verify** that the rounded quantity's risk does not exceed `planned_risk`.
4. **If rounding would increase risk**, fail closed (require re-authorization).

### Fractional Quantity

If the instrument supports fractional quantities, the authorized quantity may be used as-is (subject to step precision). If fractional quantities are not supported, floor-round to the nearest integer step.

## 15. Order-Type Boundary

### Where Order Types Belong

| Order Type | Belongs To |
|-----------|-----------|
| Market | **Execution Command** (execution-layer decision) |
| Limit | **Execution Command** (execution-layer decision) |
| Stop | **Execution Command** (execution-layer decision) |
| Stop-Limit | **Execution Command** (execution-layer decision) |
| Other broker order types | **Broker Adapter** (broker-specific translation) |

### Principle

**Do NOT introduce an order type into TradePlan or Operational Trade Intent.** Order-specific semantics begin at the Execution Command layer. The Operational Trade Intent and Execution Authorization are broker-neutral and order-type-agnostic.

### Recommended Boundary

```
Operational Trade Intent = broker-neutral, order-type-agnostic
Execution Authorization = broker-neutral, order-type-agnostic
Execution Command = execution-oriented (order type decided here)
Broker Adapter = broker-specific order translation
```

## 16. Broker-Specific Translation

### Where Broker-Specific Concepts Belong

| Concept | Belongs To |
|---------|-----------|
| Broker symbol | **Broker Adapter** |
| Exchange code | **Broker Adapter** |
| Product type | **Broker Adapter** |
| Order type (broker-specific) | **Broker Adapter** |
| Validity (broker-specific) | **Broker Adapter** |
| Routing | **Broker Adapter** |
| Account | **Broker Adapter** |
| Client order ID | **Execution Command** (generated, passed to broker) |

### Preferred Conceptual Boundary

```
Operational Trade Intent = broker-neutral
Execution Authorization = broker-neutral
Execution Command = execution-oriented (broker-agnostic command structure)
Broker Adapter = broker-specific translation (symbol, exchange, product, routing)
```

### Principle

The Execution Command must remain **broker-neutral**. It expresses the intent to buy/sell a quantity of an instrument at a price, without knowing which broker, exchange, or routing will be used. The Broker Adapter translates the broker-neutral command into a broker-specific request.

## 17. Authorization Binding

### Required Binding

Execution Command creation MUST require a valid authorization for the exact intent. The following invariant must hold:

```
authorization.intent_id == command.intent_id
```

### Content Fingerprint Verification

The authorization content fingerprint must match the authorized intent content fingerprint:

```
authorization.content_fingerprint == command.content_fingerprint
```

This must be verified **before** command creation.

### Prevention of Misbinding

The system must prevent:

```
Authorization A
    ↓
Intent B
    ↓
Execution Command
```

This is prevented by:
1. **Authorization binds to `intent_id`** — the authorization record references a specific intent.
2. **Content fingerprint** — the authorization binds to a fingerprint of the intent's content.
3. **Command creation verifies both** — the command constructor verifies the authorization is valid for the exact intent.

### Authorization State Verification

The authorization must be in `AUTHORIZED` state. Any other state (`UNAUTHORIZED`, `ELIGIBLE`, `EXPIRED`, `REVOKED`, `SUPERSEDED`) must fail closed.

## 18. Authorization Expiry

### Expected Behavior

| Authorization State | Command Creation Behavior |
|--------------------|--------------------------|
| `AUTHORIZED` | **Permitted** (if all other checks pass) |
| `EXPIRED` | **NO COMMAND** — fail closed |
| `REVOKED` | **NO COMMAND** — fail closed |
| `SUPERSEDED` | **NO COMMAND** — fail closed |
| `UNAUTHORIZED` | **NO COMMAND** — fail closed |
| `ELIGIBLE` | **NO COMMAND** — not yet authorized |
| Missing | **NO COMMAND** — fail closed |
| Unknown | **NO COMMAND** — fail closed |
| Malformed | **NO COMMAND** — fail closed |

### Principle

```
NO VALID AUTHORIZATION → NO EXECUTION COMMAND
```

Any ambiguity, missing data, or unexpected state must result in **NO COMMAND**. This is a safety-critical boundary.

## 19. Re-Authorization Requirements

### When Re-Authorization Is Required

Re-authorization is required when any **material field** changes. The following table summarizes:

| Field | Change | Re-Authorization Required |
|-------|--------|--------------------------|
| `instrument` | Changed | **YES** |
| `direction` | Changed | **YES** |
| `quantity` | Changed | **YES** |
| `entry` | Changed materially | **YES** |
| `stop` | Changed materially | **YES** |
| `target` | Changed materially | **YES** |
| `planned_risk` | Increased | **YES** |
| `account` | Changed | **YES** |
| `execution mode` | Paper→Live | **YES** |
| `order type` | Changed | **YES** |

### Non-Material Technical Normalization

The following changes do NOT require re-authorization (they are safe normalizations):

| Change | Tolerance |
|--------|-----------|
| Price tick rounding | Within half a tick |
| Quantity step rounding | Floor-rounded, risk not increased |
| Broker symbol translation | Same asset, different representation |
| Client order ID generation | Deterministic from intent |

### Distinction

**Non-material technical normalization** = changes that preserve the economic meaning of the authorized intent (e.g., rounding to tick precision).

**Material economic change** = changes that alter the economic meaning (e.g., changing quantity, direction, instrument, or materially changing geometry).

## 20. Command Identity

### Identity Hierarchy

The following identity hierarchy is recommended:

```
plan_id
    ↓
intent_id
    ↓
authorization_id
    ↓
command_id
    ↓
broker_order_id
```

### Identity Relationships

| Identity | Source | Layer | Purpose |
|----------|--------|-------|---------|
| `plan_id` | `TradePlan` | Planning | Source plan identity |
| `intent_id` | Operational Trade Intent | Operational | Operational snapshot identity |
| `authorization_id` | Execution Authorization | Authorization | Authorization record identity |
| `command_id` | Execution Command | Execution | Command identity (deterministic) |
| `broker_order_id` | Broker | Broker | Broker-side order identity |

### Command ID Generation

The `command_id` should be **deterministic** from canonical inputs:

```
command_id = "cmd-" + sha256[:16] of (authorization_id + intent_id + command context)
```

This provides:
- **Deduplication**: Same authorization + same context → same command_id.
- **Auditability**: The command can be traced back to its authorization and intent.
- **Determinism**: No wall-clock or random component.

### Distinction

- `plan_id` ≠ `intent_id` ≠ `authorization_id` ≠ `command_id` ≠ `broker_order_id`
- Each identity belongs to a different layer.
- Each identity is deterministic within its layer.

## 21. Command Immutability

### Recommended: Immutable Command

Once an execution command is constructed, it should be **immutable**. Changing material fields should require:
1. A **new command** (new `command_id`).
2. **Appropriate authorization** (new or existing, depending on the change).

### Justification

- **Auditability**: An immutable command provides a permanent record of what was attempted.
- **Safety**: Prevents a command from being silently mutated after authorization.
- **Traceability**: Each command has a unique identity that can be traced to its authorization.

### Preferred Safety Principle

```
Command V1 → immutable
```

NOT:

```
Command V1 → mutable → Command V2 (same id)
```

## 22. Idempotency

### Duplicate Command Prevention

The architecture must prevent one authorization from accidentally generating multiple unintended execution attempts. The following mechanisms are recommended:

| Mechanism | Purpose |
|-----------|---------|
| `intent_id` | Bind command to specific intent |
| `authorization_id` | Bind command to specific authorization |
| `command_id` | Deterministic identity (deduplication) |
| Idempotency key | Prevent duplicate submission |
| Command fingerprint | Detect duplicate commands |

### Recommended Approach

1. **Deterministic `command_id`** — same authorization + same context → same command_id.
2. **Idempotency key** — derived from `authorization_id` + `intent_id` + command context.
3. **Command store** — track created commands to prevent duplicates.

### Principle

```
ONE AUTHORIZATION + ONE INTENT → AT MOST ONE COMMAND (per command context)
```

If a command for the same authorization + intent already exists, return the existing command (idempotent) or reject the duplicate.

## 23. Replay Protection

### Preventing Old Authorization Replay

The architecture must prevent an old valid authorization from being replayed. The following mechanisms are recommended:

| Mechanism | Purpose |
|-----------|---------|
| Expiry | Authorization has a limited lifetime |
| Nonce/version | Each authorization has a unique version |
| Consumed authorization | Mark authorization as consumed after command creation |
| Command state | Track command state to prevent re-submission |
| Session binding | Bind authorization to a session/context |
| Idempotency | Prevent duplicate command creation |

### Recommended Approach

1. **Authorization expiry** — authorization has a defined lifetime (`expires_at`).
2. **Consumed flag** — once an authorization is consumed by a command, it cannot be consumed again.
3. **Command state tracking** — track the state of each command to prevent re-submission.

### Principle

```
AUTHORIZATION CONSUMED → CANNOT BE REPLAYED
```

## 24. Live/Paper Separation

### Critical Boundary

The future command boundary **cannot** accidentally convert:

```
PAPER authorization → LIVE execution command
```

### Recommended Principle

```
Authorization mode == Execution Command mode
```

Unless an explicit re-authorization occurs.

### Mode Binding

The execution mode (paper/live) must be **explicitly bound** to the authorization. The command inherits the mode from the authorization. A paper authorization can only produce a paper command. A live authorization can only produce a live command.

### Fail-Closed

Unknown mode → NO COMMAND.

## 25. Account Binding

### Recommended Principle

```
Authorization for Account A → Command for Account B = NO
```

### Account Binding Location

Account binding belongs to:
- **Execution Command** — specifies the target account.
- **Broker Adapter** — translates account to broker-specific account.
- **Broker** — executes against the account.

The authorization does not need to know the account (it answers "is this intent permitted to proceed?"). The command specifies the account. The account on the command must be consistent with the authorization's scope.

### Principle

Account cannot silently change between authorization and command.

## 26. Session Binding

### Recommended Session Concepts

| Concept | Belongs To |
|---------|-----------|
| Trading day | Authorization lifetime |
| Trading session | Authorization lifetime |
| Application session | Not bound (authorization persists) |
| Authorization lifetime | Primary temporal bound |

### Principle

Commands should be bound to the **authorization lifetime**, not to an application process or system instance. A command can be created within the authorization's lifetime, regardless of application restarts.

### Replay Across Sessions

A command cannot be replayed in a later session if the authorization has expired or been consumed.

## 27. Temporal / Point-in-Time Safety

### Prohibitions

Execution Command creation MUST NOT:
- Modify historical analysis
- Modify `MarketScanResult`
- Modify `TradePlan`
- Modify `Operational Trade Intent`
- Modify `PaperTrade`
- Use future paper-trading outcomes
- Use future historical data
- Modify the `Execution Authorization` it consumes

### Principle

Execution Command creation is a **point-in-time operation** that consumes existing authorized state and produces a new command artifact. It does not mutate any upstream artifact.

### Execution-Specific Market Checks

Execution-specific market checks (e.g., market open/closed, price bands) may belong to a future execution layer, but must not mutate frozen upstream artifacts.

## 28. Execution-Time Market Data Boundary

### Future Market Data Needs

The future execution layer may need current market information:
- Current bid/ask
- Current price
- Trading status
- Market open/closed
- Price bands
- Tick size

### Where Market Data Belongs

| Data | Belongs To |
|------|-----------|
| Current bid/execution-time price | **Broker Adapter** or **Broker response** |
| Market open/closed status | **Execution Command validation** |
| Price bands | **Execution Command validation** |
| Tick size | **Broker Adapter** |

### Principle

**Do NOT add these to TradePlan or Operational Trade Intent.** Execution-time market data belongs to the execution layer, not the planning or intent layers.

## 29. Slippage/Fees Boundary

### Checkpoint 12 Explicit Documentation

Checkpoint 12 explicitly documented: **fees/slippage/spread/latency are absent by design.**

### Do NOT Retrofit

Do NOT retrofit fees, slippage, spread, or latency into TradePlan or Operational Trade Intent.

### Future Responsibility

| Concept | Future Layer |
|---------|-------------|
| Fees | **Broker Adapter** or **Execution Result** |
| Slippage | **Execution Result** |
| Spread | **Broker Adapter** or **Execution Result** |
| Latency | **Execution Result** |

These concepts belong to future execution/result layers, not to planning or intent.

## 30. Execution-Result Separation

### Execution Command MUST NOT Contain

The following fields belong to future execution/result/position layers, NOT to Execution Command:

| Field | Belongs To |
|-------|-----------|
| Fill price | **Execution Result** |
| Actual execution price | **Execution Result** |
| Broker order ID | **Broker** |
| Position ID | **Position** |
| Realized P&L | **Execution Result** |
| Realized R | **Execution Result** |

### Principle

Execution Command expresses **intent to execute**, not the result of execution. Results belong to a future execution-result layer.

## 31. Position Separation

### Execution Command ≠ Position Opened

Execution Command does NOT mean a position is opened. The future boundary is:

```
Execution Command
    ↓
Broker Order
    ↓
Execution Result
    ↓
Position
```

### Principle

Execution Command is a request toward execution. It does not imply:
- Order accepted
- Order filled
- Position opened
- Execution successful

## 32. Fail-Closed Behavior

### Principle

Unknown → NO COMMAND.

| Condition | Result |
|-----------|--------|
| Missing authorization | **NO COMMAND** |
| Expired authorization | **NO COMMAND** |
| Revoked authorization | **NO COMMAND** |
| Superseded authorization | **NO COMMAND** |
| Mismatched intent | **NO COMMAND** |
| Mismatched fingerprint | **NO COMMAND** |
| Changed quantity | **NO COMMAND / RE-AUTHORIZATION** |
| Changed direction | **NO COMMAND / RE-AUTHORIZATION** |
| Changed geometry | **NO COMMAND / RE-AUTHORIZATION** |
| Unknown authorization state | **NO COMMAND** |
| Emergency stop active | **NO COMMAND** |
| Trading disabled | **NO COMMAND** |
| Paper authorization + live command mode | **NO COMMAND** |
| Account mismatch | **NO COMMAND** |

### Justification

Fail-closed is the appropriate principle for execution command creation. Any ambiguity, missing data, or unexpected state must result in NO COMMAND. This is a safety-critical boundary.

## 33. Client/Dashboard Boundary

### Architectural Principle

```
Client input ≠ trusted execution command
```

### Expected Architecture

```
Client
    ↓
Trusted server-side authorization boundary
    ↓
Authorized intent
    ↓
Execution command
```

### Current State

The dashboard is read-only presentation. No execution path exists. When execution is implemented:
- Dashboard may **request** authorization.
- Server-side boundary must **evaluate** and **grant** authorization.
- Client cannot directly set `authorized = true` or construct an execution command.

### Principle

Client input must not be trusted as an execution command.

## 34. Direct Broker Access Audit

### Search Results

The following paths were searched:

| Path | Exists? |
|------|---------|
| TradePlan → Broker | **No** |
| Operational Trade Intent → Broker | **No** |
| Execution Authorization → Broker | **No** |
| Dashboard → Broker | **No** |
| PaperTrade → Broker | **No** |
| Any object → `urlopen`/HTTP to broker API | **No** (only historical data providers use HTTP, not broker APIs) |

### Verdict

**No direct broker access exists.** No path from any current object reaches a broker. This is a clean baseline.

## 35. Responsibility Matrix

| Artifact | Purpose | Authoritative Inputs | Outputs | Mutable | Identity | Broker-Specific? | Can Authorize? | Can Create Commands? | Can Send Broker Requests? | Can Mutate Upstream? | Can Contain Execution Results? |
|----------|---------|---------------------|---------|---------|----------|-----------------|----------------|---------------------|--------------------------|---------------------|-------------------------------|
| **MarketScanResult** | Multi-instrument opportunity scan | OHLCV candles, market context | Ranked opportunities | Immutable | `scan_id` | No | No | No | No | No | No |
| **TradePlan** | Risk/position-size calculation | Trade candidate geometry, account params | Sized plan with quantity/risk | Immutable | `plan_id` | No | No | No | No | No | No |
| **Operational Trade Intent** | Operational snapshot of eligible plan | TradePlan (by reference) | Broker-neutral intent snapshot | Immutable | `intent_id` | No | No | No | No | No | No |
| **Execution Authorization** | Permission for intent to proceed | Intent, policy gates, human decision | Authorization record | Immutable (versioned) | `authorization_id` | No | No | No | No | No | No |
| **Execution Command** | Request toward execution | Authorization + intent snapshot | Broker-agnostic command | Immutable | `command_id` | No | No | No | No | No | No |
| **Broker Adapter** | Broker-specific translation | Execution Command | Broker request | Mutable (stateless) | N/A | **Yes** | No | No | **Yes** | No | No |
| **Broker Order** | Broker-side order state | Broker request | Order state, fills | Mutable (external) | `broker_order_id` | **Yes** | No | No | No | No | No |
| **Execution Result** | Execution outcome | Broker response | Fill, price, P&L | Mutable (external) | N/A | **Yes** | No | No | No | No | **Yes** |
| **Position** | Broker-side position state | Fills | Position state | Mutable (external) | `position_id` | **Yes** | No | No | No | No | No |
| **Portfolio** | Account-level aggregation | Positions | Portfolio state | Mutable (external) | N/A | **Yes** | No | No | No | No | No |

### Key Distinctions

- **Only the Broker Adapter can send broker requests.**
- **Only the Authorization layer can authorize.**
- **Only the Execution Command layer can create commands.**
- **No artifact can both authorize and execute.**
- **MarketScanResult, TradePlan, Operational Trade Intent, Execution Authorization, Execution Command** are immutable analytical/operational records. They neither authorize nor execute.
- **Execution Command is broker-neutral.** Broker-specific translation is isolated to the Broker Adapter.

## 36. Future Execution Command Contract

### What is Execution Command?

Execution Command is an **immutable, identity-bound, fail-closed, broker-neutral** artifact that represents a request to attempt execution of a specific authorized Operational Trade Intent. It is produced by a trusted server-side execution boundary after verifying a valid authorization for the exact intent. It does not execute, does not construct broker requests, and does not contact brokers. It answers one question: "Is THIS particular authorized intent ready to be sent toward a broker adapter?"

### What Creates It?

A trusted server-side execution boundary that:
1. Validates the authorization is valid, not expired, not revoked, not superseded.
2. Verifies the authorization binds to the exact `intent_id`.
3. Verifies the content fingerprint matches the authorized intent.
4. Verifies the execution mode (paper/live) matches the authorization.
5. Verifies no material field has changed.
6. Produces an immutable command artifact.

### What Authorization Is Required?

- A valid `authorization_id` in `AUTHORIZED` state.
- The authorization must bind to the exact `intent_id`.
- The authorization's content fingerprint must match the intent's content fingerprint.
- The authorization must not be expired, revoked, or superseded.
- The authorization's execution mode must match the command's execution mode.

### What Fields Does It Contain?

| Field | Type | Purpose |
|-------|------|---------|
| `command_id` | `str` | Deterministic identity (`"cmd-" + sha256[:16]`) |
| `authorization_id` | `str` | Proof of valid authorization |
| `intent_id` | `str` | The authorized intent |
| `plan_id` | `str` | Source plan (provenance) |
| `instrument` | `str` | Target instrument (canonical) |
| `direction` | `str` | LONG/SHORT |
| `quantity` | `Decimal` | Authorized position size |
| `entry` | `Decimal` | Authorized entry level |
| `stop` | `Decimal` | Authorized stop level |
| `target` | `Decimal` | Authorized target level |
| `planned_risk` | `Decimal` | Authorized planned risk |
| `maximum_risk` | `Decimal` | Risk limit bound |
| `execution_mode` | `str` | PAPER or LIVE (from authorization) |
| `content_fingerprint` | `str` | Fingerprint of authorized intent |
| `created_at` | `datetime` | Command creation timestamp |
| `idempotency_key` | `str` | Deduplication key |

### What Fields Must Never Be Included?

- `fill_price` — belongs to Execution Result
- `actual_execution_price` — belongs to Execution Result
- `broker_order_id` — belongs to Broker
- `position_id` — belongs to Position
- `realized_pnl` — belongs to Execution Result
- `realized_r` — belongs to Execution Result
- `portfolio_state` — belongs to Portfolio
- `broker_symbol` — belongs to Broker Adapter
- `exchange` — belongs to Broker Adapter
- `routing` — belongs to Broker Adapter
- `slippage` — absent by design
- `fees` — absent by design

### What Transformations Are Allowed?

| Transformation | Allowed | Condition |
|---------------|---------|-----------|
| Price tick rounding | Yes | Within half a tick of authorized price |
| Quantity step rounding | Yes | Floor-rounded, risk not increased |
| Instrument → broker symbol | Yes | Broker Adapter responsibility |
| LONG/SHORT → Buy/Sell | Yes | Direct mapping |

### What Happens When a Material Field Changes?

**Re-authorization is required.** The existing command remains immutable. A new intent + new authorization + new command are required.

### Is It Immutable?

**Yes.** Once constructed, an Execution Command cannot be mutated. Changes require a new command and appropriate authorization.

### How Is Duplication Prevented?

- **Deterministic `command_id`** — same authorization + same context → same command_id.
- **Idempotency key** — derived from authorization_id + intent_id + context.
- **Command store** — track created commands to prevent duplicates.

## 37. Future Lifecycle

### Conceptual Command Lifecycle

The following states are evaluated for the future Execution Command lifecycle:

| State | Belongs To This Checkpoint? | Description |
|-------|---------------------------|-------------|
| `NOT_CREATED` | **Yes** | No command exists yet |
| `CREATED` | **Yes** | Command constructed, not yet submitted |
| `SUBMITTED` | **No** | Broker submission (future layer) |
| `ACCEPTED` | **No** | Broker accepted (future layer) |
| `REJECTED` | **No** | Broker rejected (future layer) |
| `FILLED` | **No** | Broker filled (future layer) |
| `CANCELLED` | **No** | Broker cancelled (future layer) |
| `EXPIRED` | **No** | Broker expired (future layer) |

### Checkpoint Boundary

This checkpoint covers only the states **before broker submission**:

```
NOT_CREATED → CREATED
```

The checkpoint stops at the `CREATED` state. The boundary between `CREATED` and `SUBMITTED` belongs to a future broker-integration checkpoint.

### State Distinctions

- **NOT_CREATED** — No command exists. Authorization may or may not exist.
- **CREATED** — Command is constructed and immutable. Ready for broker submission (future layer).
- **SUBMITTED** — (Future) Command sent to broker.

## 38. Future Test Contract

The following test categories must be defined when Execution Command is implemented:

1. Command requires valid authorization
2. Command binds to exact `intent_id`
3. Command binds to `authorization_id`
4. Content fingerprint matches
5. Expired authorization rejected
6. Revoked authorization rejected
7. Superseded authorization rejected
8. Missing authorization rejected
9. Unknown authorization rejected
10. Quantity cannot silently change
11. Direction cannot silently change
12. Instrument cannot silently change
13. Entry cannot silently change
14. Stop cannot silently change
15. Target cannot silently change
16. Risk cannot silently increase
17. Paper authorization cannot produce live command
18. Account cannot silently change
19. Command identity deterministic/appropriate
20. Duplicate command behavior deterministic
21. Replay protection
22. Command immutability
23. Broker-neutral boundary
24. Broker-specific translation isolated
25. No upstream mutation
26. No analytical mutation
27. No PaperTrade mutation
28. No future information
29. Fail-closed behavior
30. No direct broker contact
31. No order creation
32. No position creation

## 39. Recommended Next Boundary

The next boundary to audit and design is:

```
Execution Command
    ↓
Broker Adapter
```

This would define what a broker adapter is, how it consumes an execution command, what broker-specific translations it performs, and how it remains isolated from the broker-neutral command layer.

## 40. Limitations

1. **No execution command layer exists.** This audit defines the contract only. Implementation is a future checkpoint.
2. **No authorization layer exists.** The authorization contract is defined but not implemented.
3. **No operational trade intent layer exists.** The intent contract is defined but not implemented.
4. **No mode system (paper/live).** The command must distinguish paper from live when a mode system is introduced.
5. **No emergency stop exists.** A future execution system will require a global emergency-disable boundary.
6. **No account/portfolio layer.** Account binding for commands is deferred.
7. **No broker adapter.** Broker neutrality of commands is a design principle, not a tested implementation.
8. **No human approval UI.** The human consent mechanism is undefined.
9. **No broker integration.** The command boundary stops before broker submission.

## 41. Implementation Decision

**NO IMPLEMENTATION CHANGES.**

The Execution Authorization → Execution Command boundary can be clearly defined. No blocking architectural defect exists. The conceptual contract is sound.

## 42. Final Verdict

**PASS**

The system now has a clearly defined conceptual boundary between Execution Authorization and a future Execution Command layer. Authorized intents can be converted into execution-oriented commands only through a controlled, identity-bound, fail-closed boundary. Material changes require re-authorization. No order is created, no broker is contacted, no position is created, and no live execution is implemented.

---

## Appendix A: Test Baseline

### Commands Executed

```
python -m pytest tests/test_trade_planning.py tests/test_paper_trading.py tests/test_paper_trading_operations.py -q
python -m pytest tests/ -q
```

### Results

| Suite | Result |
|-------|--------|
| trade_planning + paper_trading + paper_trading_operations | 350 passed |
| Full suite | 4849 passed, 2 failed (pre-existing: missing `yfinance`), 3 skipped |

### Failures

The 2 failures are pre-existing and unrelated to this checkpoint:
- `tests/test_live_data_integration.py::TestProviderFailure::test_yahoo_not_ready_when_no_backend`
- `tests/test_live_data_integration.py::TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`

Both fail because `yfinance` is not installed in the current environment. This is an optional dependency limitation, not a regression.

## Appendix B: Repository State Summary

| Aspect | State |
|--------|-------|
| Execution Command layer | **Absent** — defined conceptually only |
| Execution Authorization layer | **Absent** — defined conceptually only (Checkpoint 13.3) |
| Operational Trade Intent layer | **Absent** — defined conceptually only (Checkpoint 13.2) |
| Broker integration | **Absent** |
| Order model | **Absent** |
| Position model | **Absent** |
| Broker Adapter | **Absent** |
| Kill switch | **Absent** |
| Mode system (paper/live) | **Absent** |
| Human approval mechanism | **Absent** |
| Automatic bypass paths | **None found** |
| Frozen boundaries | **Intact** — no modifications |
