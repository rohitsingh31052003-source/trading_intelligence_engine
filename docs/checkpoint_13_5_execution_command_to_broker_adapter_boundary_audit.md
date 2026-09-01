# Checkpoint 13.5 — Execution Command → Broker Adapter Boundary Audit & Design

## 1. Purpose

Determine what exact information is permitted to cross the boundary from a future **Execution Command** layer into a future **Broker Adapter** layer, and what responsibilities must remain outside the Broker Adapter. This checkpoint defines and audits the conceptual boundary only.

The central safety invariant is:

```
EXECUTION COMMAND
        ↓
BROKER ADAPTER
```

must preserve the authorized economic meaning of the command.

The Broker Adapter may translate a broker-neutral command into broker-specific representation. It must NOT silently alter:

- authorized direction
- authorized quantity
- authorized risk
- authorized instrument meaning
- authorized entry constraints
- authorized stop/target semantics

without an explicit upstream authorization/re-authorization boundary.

This is an **audit + contract design only** checkpoint. No implementation changes are made.

## 2. Scope

- Inspect the repository for any existing Broker Adapter, broker client, broker order, or execution result abstractions.
- Audit the Execution Command contract established in Checkpoint 13.4.
- Audit the Execution Authorization contract established in Checkpoint 13.3.
- Audit the Operational Trade Intent contract established in Checkpoint 13.2.
- Audit the TradePlan contract (frozen, Product Phase 4).
- Audit the PaperTrade contract (frozen, Product Phase 5).
- Determine the boundary subject: what the Broker Adapter should consume.
- Perform a field-by-field crossing audit for broker-neutral vs broker-specific concepts.
- Define symbol translation, exchange/segment translation, order-type boundary, price translation, quantity translation, risk preservation, direction translation, entry/stop/target semantics, stop-loss safety, account binding, execution mode, broker credentials, authentication vs authorization, broker availability, broker validation vs system validation, no silent auto-correction, broker response separation, order identity, idempotency, retry safety, replay protection, rate limiting, broker-specific failure handling, paper/live isolation, position separation, and point-in-time safety.
- Define the responsibility matrix, future Broker Adapter contract, lifecycle, and test contract.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Boundary |
|-----------|--------|----------|
| 11.1–11.8 | FROZEN | Market data → candle integrity → features → setup → evidence → `MarketScanResult` |
| 12.1–12.6 | FROZEN | `MarketScanResult` → `TradePlan` → `PaperTrade` → journal/performance → dashboard |
| 13.1 | ACCEPTED | No operational trade intent, execution path, broker order path, or authorization currently exists |
| 13.2 | ACCEPTED | Operational Trade Intent contract defined (not implemented) |
| 13.3 | ACCEPTED | Execution Authorization contract defined (not implemented) |
| 13.4 | ACCEPTED | Execution Command contract defined (not implemented) |

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

### Data Providers
- `src/engine/data/historical_provider.py` — `UpstoxHistoricalDataProvider`, `YahooHistoricalDataProvider`
- `src/engine/data/yahoo_provider.py` — `YahooFinanceProvider`
- `src/engine/data/base_provider.py` — base provider abstractions
- `src/engine/data/provider.py` — provider protocols

### Dashboard / Presentation
- `src/dashboard/views.py` — `ActionabilityState`, `derive_actionability`, `DashboardTradeView`
- `src/dashboard/services.py` — `DashboardAnalysisService`
- `src/dashboard/paper_trade_operations.py` — `PaperTradingOperations`, `OperationalStatus`
- `src/dashboard/paper_trade_store.py` — `PaperTradeStore`
- `src/dashboard/app.py` — FastAPI routes

### Config
- `src/engine/config/trade_plan_config.py` — `TradePlanConfig`
- `src/engine/config/paper_trade_config.py` — `PaperTradeConfig`

### Prior Audits
- `docs/checkpoint_13_1_operational_trade_intent_and_execution_boundary_audit.md`
- `docs/checkpoint_13_2_operational_trade_intent_model_and_contract_audit.md`
- `docs/checkpoint_13_3_execution_authorization_boundary_audit.md`
- `docs/checkpoint_13_4_execution_authorization_to_execution_command_boundary_audit.md`

### Tests (baseline only — not modified)
- `tests/test_trade_planning.py` — 158 tests
- `tests/test_paper_trading.py` — 114 tests
- `tests/test_paper_trading_operations.py` — 78 tests
- `tests/test_dashboard.py` — 67 tests
- `tests/test_workstation.py` — 95 tests
- `tests/test_watchlist_scanner.py` — 75 tests

## 5. Existing Broker/Execution Abstractions

### Search Results

The following keywords were searched across the entire codebase:

| Keyword | Result |
|---------|--------|
| `BrokerAdapter` | **No classes found.** Mentioned in Checkpoint 13.3 and 13.4 as a future responsibility. |
| `BrokerClient` | **No classes found.** |
| `BrokerOrder` | **No classes found.** |
| `BrokerConnection` | **No classes found.** |
| `BrokerService` | **No classes found.** |
| `BrokerIntegration` | **No classes found.** |
| `ExecutionResult` | **No classes found.** |
| `place_order`, `PlaceOrder` | **No classes found.** |
| `submit_order`, `SubmitOrder` | **No classes found.** |
| `send_order`, `SendOrder` | **No classes found.** |
| `create_order`, `CreateOrder` | **No classes found.** |
| `cancel_order`, `CancelOrder` | **No classes found.** |
| `modify_order`, `ModifyOrder` | **No classes found.** |
| `amend_order`, `AmendOrder` | **No classes found.** |
| `order_api`, `OrderAPI` | **No classes found.** |
| `order_endpoint`, `OrderEndpoint` | **No classes found.** |
| `order_request`, `OrderRequest` | **No classes found.** |
| `order_payload`, `OrderPayload` | **No classes found.** |
| `broker_symbol`, `BrokerSymbol` | **No classes found.** |
| `exchange` (order exchange) | **No classes found.** (Only in "exchange fees" and "exchange calendar" contexts, NOT modeled.) |
| `product_type`, `ProductType` | **No classes found.** |
| `order_type`, `OrderType` | **No classes found.** |
| `validity` (order validity) | **No classes found.** |
| `routing` (exchange routing) | **No classes found.** |
| `account` (broker account) | **No classes found.** (Only `account_capital` as user-supplied risk parameter.) |
| `client_order_id`, `ClientOrderID` | **No classes found.** |
| `order_id`, `OrderID` | **No classes found.** |
| `position`, `Position` | **No classes found.** (Only in "position sizing" context, not as maintained state.) |
| `fill` (order fill) | **No classes found.** (Only in paper-trade "fill" simulation context.) |
| `execution` (trade execution) | **No classes found.** (Only in "NOT an execution engine" disclaimers.) |
| `trading_api`, `TradingAPI` | **No classes found.** |
| `authentication` (broker auth) | **No classes found.** (Only data-provider token context.) |
| `access_token`, `AccessToken` | **No classes found.** |
| `broker_credentials`, `BrokerCredentials` | **No classes found.** |
| `rate_limit` (broker rate limit) | **No classes found.** (Only in data-provider error handling.) |
| `idempotency` | **No classes found.** |
| `retry` (broker retry) | **No classes found.** (Only "No retries" documented limitation for data providers.) |
| `replay` (order replay) | **No classes found.** (Only in historical replay context.) |
| `Portfolio` | **No classes found.** |

### Conclusion

**No broker, order, position, account, portfolio, exchange, venue, clearing, settlement, commission, slippage, spread, latency, margin, leverage, or execution abstraction exists.** The repository has no live trading capability. This is a clean baseline for defining the Broker Adapter boundary.

## 6. Current Provider Integrations

### Upstox (`src/engine/data/historical_provider.py`)

- `UpstoxHistoricalDataProvider` — OPTIONAL real-HTTP historical provider for the Upstox V3 Historical Candle API.
- Uses the Upstox V3 Historical Candle API **exclusively for OHLCV data retrieval**.
- Does NOT use any order, account, position, or execution endpoint.
- Authentication: `UPSTOX_ANALYTICS_TOKEN` environment variable, sent ONLY in `Authorization: Bearer <token>` header.
- Instrument-key resolution is isolated to the provider (`_default_upstox_instrument_key_map`).
- Timeframe support: `15m` and `1D` only.
- **This is strictly a DATA provider, NOT an execution provider.**

### Yahoo Finance (`src/engine/data/yahoo_provider.py`)

- `YahooFinanceProvider` — OPTIONAL live/near-live market-data provider.
- Uses `yfinance` library for OHLCV data retrieval only.
- Does NOT use any order, account, position, or execution endpoint.
- **This is strictly a DATA provider, NOT an execution provider.**

### Conclusion

Both provider integrations are **strictly DATA providers**. Neither has any execution capability. No broker API for order placement, cancellation, modification, position query, account query, or authentication-for-trading exists.

## 7. Boundary Subject

### Preferred Conceptual Flow

```
Execution Command
        ↓
Broker Adapter
```

### What Should the Broker Adapter Consume?

The audit evaluated four options:

| Option | Description | Verdict |
|--------|-------------|---------|
| **A.** Execution Command directly | Broker Adapter reads command fields directly | **REJECTED** — too loose, no authorization context |
| **B.** Authorized-Intent Snapshot | Broker Adapter reads the snapshot directly | **PARTIAL** — snapshot carries intent but not command identity |
| **C.** Execution Command + authorization metadata | Adapter reads both independently | **ACCEPTABLE** — but requires explicit binding verification |
| **D.** Execution Command directly (preferred) | Broker Adapter consumes the Execution Command, which already binds authorization + intent | **PREFERRED** — the command is the single immutable artifact |

### Recommended: Option D — Execution Command

The Broker Adapter should consume the **Execution Command** directly. The Execution Command already:

1. **References** the `authorization_id` (proof of valid authorization).
2. **References** the `intent_id` (the authorized intent).
3. **Carries** the authoritative intent fields by value (immutable snapshot).
4. **Includes** the content fingerprint (verification that intent has not changed).
5. **Binds** the execution mode (paper/live) from the authorization.
6. **Carries** a deterministic `command_id` (deduplication).

The Execution Command is the single immutable artifact that represents "what is authorized to be attempted." The Broker Adapter's job is to translate this broker-neutral command into a broker-specific request — nothing more.

### Safety Justification

- **The Execution Command is immutable** — once constructed, it cannot be mutated.
- **The command binds authorization** — the adapter receives proof that authorization occurred.
- **The command is broker-neutral** — the adapter is the only layer that introduces broker-specific concepts.
- **The command carries a deterministic identity** — the adapter can derive idempotency keys from it.

## 8. Broker-Neutral Contract

### Fields That Must Remain Broker-Neutral

The following fields belong to the Execution Command and must remain broker-neutral:

| Field | Type | Purpose | Broker-Neutral? |
|-------|------|---------|-----------------|
| `command_id` | `str` | Deterministic identity | **YES** |
| `authorization_id` | `str` | Proof of valid authorization | **YES** |
| `intent_id` | `str` | The authorized intent | **YES** |
| `plan_id` | `str` | Source plan (provenance) | **YES** |
| `instrument` | `str` | Target instrument (canonical name) | **YES** |
| `direction` | `str` | LONG/SHORT | **YES** |
| `quantity` | `Decimal` | Authorized position size | **YES** |
| `entry` | `Decimal` | Authorized entry level | **YES** |
| `stop` | `Decimal` | Authorized stop level | **YES** |
| `target` | `Decimal` | Authorized target level | **YES** |
| `planned_risk` | `Decimal` | Authorized planned risk | **YES** |
| `maximum_risk` | `Decimal` | Risk limit bound | **YES** |
| `execution_mode` | `str` | PAPER or LIVE | **YES** |
| `content_fingerprint` | `str` | Fingerprint of authorized intent | **YES** |
| `created_at` | `datetime` | Command creation timestamp | **YES** |
| `idempotency_key` | `str` | Deduplication key | **YES** |

### Critical Rule

**The Broker Adapter MUST NOT become the owner of planning semantics.** Entry, stop, target, quantity, planned_risk, and maximum_risk are authoritative from the Execution Command (originating from TradePlan via Operational Trade Intent). The Broker Adapter may only apply **safe normalizations** that do not change the economic meaning. Any material change requires re-authorization upstream.

## 9. Broker-Specific Contract

### Classification Categories

1. **Broker-neutral** — belongs to Execution Command
2. **Broker-specific** — belongs to Broker Adapter
3. **Broker-generated** — produced by broker, belongs to Broker Response
4. **Broker-response data** — returned by broker, belongs to Execution Result
5. **Must never cross this boundary** — must not enter Execution Command or upstream

### Field Classification

| Concept | Classification | Rationale |
|---------|---------------|-----------|
| Broker symbol | **Broker-specific** | Internal symbol → broker symbol translation |
| Exchange code | **Broker-specific** | Broker-specific routing |
| Segment | **Broker-specific** | Broker-specific market segment |
| Product type | **Broker-specific** | Broker-specific product classification |
| Order type | **Broker-specific** | Broker-specific order representation |
| Validity | **Broker-specific** | Broker-specific order validity |
| Disclosed quantity | **Broker-specific** | Broker-specific order attribute |
| Trigger price | **Broker-specific** | Broker-specific stop representation |
| Limit price | **Broker-specific** | Broker-specific limit representation |
| Market order representation | **Broker-specific** | Broker-specific market order format |
| Routing | **Broker-specific** | Broker-specific exchange routing |
| Account identifier | **Broker-specific** | Broker-specific account reference |
| Broker-specific flags | **Broker-specific** | Broker-specific constraints/flags |
| Broker order ID | **Broker-generated** | Generated by broker, not command |
| Client order ID | **Broker-specific** | Generated by adapter for idempotency |
| Fill price | **Broker-response data** | Determined by broker/market |
| Fill quantity | **Broker-response data** | Determined by broker/market |
| Execution price | **Broker-response data** | Determined by broker/market |
| Realized P&L | **Broker-response data** | Determined by broker/market |
| Fees | **Broker-response data** | Determined by broker |
| Commission | **Broker-response data** | Determined by broker |
| Slippage | **Broker-response data** | Determined by broker/market |
| Position ID | **Broker-generated** | Generated by broker |
| Portfolio state | **Must never cross** | Belongs to portfolio layer |
| Broker credentials | **Must never cross** | Belongs to broker connection |

## 10. Symbol Translation

### Conceptual Transformation

```
Internal Instrument (e.g., "NIFTY")
        ↓
Broker Symbol (e.g., "NSE_INDEX|Nifty 50" for Upstox, "^NSEI" for Yahoo)
```

### Where Symbol Mapping Belongs

Symbol mapping belongs **exclusively to the Broker Adapter**. The Execution Command carries the canonical instrument name (e.g., "NIFTY"). The Broker Adapter is responsible for translating this to the broker-specific symbol.

### Requirements

| Requirement | Rule |
|-------------|------|
| Deterministic | Same internal instrument must always map to the same broker symbol |
| Versioned | Mapping must be versioned/tracked so changes are auditable |
| Missing mapping | **FAIL CLOSED** — if no mapping exists, reject the command |
| Ambiguous mapping | **FAIL CLOSED** — if mapping is ambiguous, reject the command |
| Mapping change | A mapping change can alter economic identity — requires re-authorization |

### Critical Principle

**An invalid or ambiguous mapping must fail closed.** The Broker Adapter must NOT silently substitute one instrument for another. If the mapping is missing, ambiguous, or stale, the command must be rejected.

## 11. Exchange/Segment Translation

### Conceptual Transformation

```
Internal Instrument
        ↓
Broker Exchange + Segment + Market + Contract + Security Identifier
```

### Where This Information Belongs

Exchange, segment, market, contract, and security identifier information belongs **exclusively to the Broker Adapter**. This information is broker-specific and must NOT be retroactively inserted into:

- `TradePlan`
- `MarketScanResult`
- `Operational Trade Intent`
- `Execution Authorization`
- `Execution Command`

### Principle

The planning and authorization layers are broker-neutral. They express intent in terms of canonical instruments and geometric levels. The Broker Adapter is the only layer that knows which exchange, segment, or market the instrument trades on.

## 12. Order-Type Boundary

### Where Order Type Begins

| Order Type | Belongs To |
|-----------|-----------|
| Market | **Broker Adapter** (broker-specific representation) |
| Limit | **Broker Adapter** (broker-specific representation) |
| Stop | **Broker Adapter** (broker-specific representation) |
| Stop-Limit | **Broker Adapter** (broker-specific representation) |
| Other broker order types | **Broker Adapter** (broker-specific translation) |

### Principle

**Do NOT introduce order types into TradePlan, Operational Trade Intent, Execution Authorization, or Execution Command.** The Execution Command expresses intent in terms of entry/stop/target geometry. The Broker Adapter decides how to represent this as broker-specific order types (e.g., a stop-loss order, a bracket order, an OCO order).

### Recommended Boundary

```
Execution Command = broker-neutral (entry/stop/target geometry)
Broker Adapter = broker-specific order translation
Broker Order = broker-specific order representation
```

## 13. Price Translation

### Conceptual Transformation

```
Execution Command price (Decimal)
        ↓
Broker-compatible price (tick-adjusted, precision-adjusted)
```

### Transformations Evaluated

| Transformation | Classification | Rule |
|---------------|---------------|------|
| Tick-size rounding | **SAFE IF** within half a tick | Must not materially change geometry |
| Decimal precision adjustment | **SAFE IF** within tolerance | Must not materially change geometry |
| Minimum increment rounding | **SAFE IF** floor-rounded | Must not increase risk |
| Price band clamping | **MATERIAL** | Must NOT silently clamp — reject instead |
| Broker restriction bypass | **PROHIBITED** | Must not bypass broker restrictions silently |

### Critical Rule

**A Broker Adapter MUST NOT silently transform a price in a way that materially changes the authorized trade.**

A price change is **material** if it exceeds half a tick size from the authorized level. If normalization would move the price more than half a tick, the command must fail closed (require re-authorization).

### Recommended Flow

```
Execution Command price (Decimal)
        ↓
Broker Adapter: normalize to tick precision (within half-tick tolerance)
        ↓
Verify normalized price ≈ authorized price (within tolerance)
        ↓
If tolerance exceeded → REJECT (require re-authorization)
        ↓
Broker-specific price format
        ↓
Broker
```

## 14. Quantity Translation

### Conceptual Transformation

```
Execution Command quantity (Decimal)
        ↓
Broker quantity (integer, lot-adjusted, step-adjusted)
```

### Transformations Evaluated

| Transformation | Classification | Rule |
|---------------|---------------|------|
| Integer rounding | **SAFE IF** floor-rounded | Must not increase risk |
| Lot-size rounding | **SAFE IF** floor-rounded | Must not increase risk |
| Quantity step rounding | **SAFE IF** floor-rounded | Must not increase risk |
| Minimum quantity enforcement | **SAFE IF** rejects below minimum | Must not silently increase |
| Maximum quantity enforcement | **SAFE IF** rejects above maximum | Must not silently increase |
| Contract multiplier application | **SAFE IF** preserves risk | Must not increase risk |

### Critical Invariant

```
Executed quantity risk <= Authorized planned_risk <= maximum_risk
```

**The Broker Adapter MUST NOT silently increase authorized risk.**

For example:
- Authorized quantity = 100
- Broker quantity = 101
- **must not be silently accepted.**

### Quantity Reduction

A quantity reduction is permissible ONLY when:
1. It results from floor-rounding to lot size or quantity step.
2. The reduced quantity's risk does not exceed `planned_risk`.
3. The reduction is documented and auditable.

## 15. Risk Preservation

### Current Invariant (from TradePlan)

```
planned_risk <= maximum_risk
```

### Execution Must Preserve This Invariant

The Broker Adapter must NOT invalidate the risk invariant through broker-specific transformations.

### Evaluation

| Transformation | Risk Impact | Required Behavior |
|---------------|-------------|-------------------|
| Quantity increase | **INCREASES RISK** | **REJECT** |
| Quantity floor-rounding | **REDUCES OR PRESERVES RISK** | **PERMIT** (documented) |
| Price rounding (entry) | **MAY CHANGE RISK** | **REJECT IF material** |
| Price rounding (stop) | **MAY CHANGE RISK** | **REJECT IF material** |
| Stop-price normalization | **MAY CHANGE RISK** | **REJECT IF material** |
| Instrument mapping change | **CHANGES IDENTITY** | **REJECT** |
| Contract multiplier change | **CHANGES RISK** | **REJECT** |

### Conceptual Result

**REJECT / RE-AUTHORIZE** — not silently adjust and continue.

If broker-specific representation causes risk to increase, the Broker Adapter must reject the command and require re-authorization with the adjusted parameters.

## 16. Direction Translation

### Conceptual Transformation

```
LONG / SHORT
        ↓
Broker-specific buy/sell representation
```

### Where Translation Belongs

Direction translation belongs **exclusively to the Broker Adapter**. It must be a deterministic mechanical translation:

| Internal Direction | Broker Representation |
|-------------------|----------------------|
| LONG | Buy |
| SHORT | Sell |

### Critical Rule

**The Broker Adapter must never infer direction from:**
- current market price
- technical indicators
- scanner output
- historical data
- account state

Direction is authoritative from the Execution Command. The adapter only translates the representation.

## 17. Entry/Stop/Target Semantics

### Field Ownership

| Field | Execution Command | Broker Adapter | Broker Order | Execution Result |
|-------|-------------------|----------------|--------------|------------------|
| `entry` | **Authoritative** | Translates to limit/stop price | Contains translated price | — |
| `stop` | **Authoritative** | Translates to stop price | Contains translated price | — |
| `target` | **Authoritative** | Translates to limit price | Contains translated price | — |
| Entry price (broker) | — | Generates | Contains | — |
| Stop price (broker) | — | Generates | Contains | — |
| Target price (broker) | — | Generates | Contains | — |
| Actual entry price | — | — | — | **Contains** |
| Actual exit price | — | — | — | **Contains** |

### Principle

The adapter must not reinterpret the economic intent. Entry, stop, and target are authoritative from the Execution Command. The adapter translates these into broker-specific order representations (e.g., a bracket order with a limit entry, stop-loss, and take-profit).

## 18. Stop-Loss Safety

### Critical Requirement

The Broker Adapter must protect stop semantics for both LONG and SHORT directions.

### Evaluation

| Direction | Stop Side | Stop Direction | Trigger Price | Limit Price |
|-----------|-----------|----------------|---------------|-------------|
| LONG | Sell | Below entry | Stop level | — |
| SHORT | Buy | Above entry | Stop level | — |

### Stop Order Representation

The adapter must translate the stop level into the broker-specific stop order representation:

| Broker Order Type | LONG Stop | SHORT Stop |
|------------------|-----------|------------|
| Stop-Market | Sell at market when stop hit | Buy at market when stop hit |
| Stop-Limit | Sell at limit when stop hit | Buy at limit when stop hit |

### Critical Rule

**The adapter must not accidentally invert stop meaning.** A LONG stop must trigger a SELL. A SHORT stop must trigger a BUY. The adapter must verify the stop direction is correct before submission.

## 19. Account Binding

### Required Invariant

```
Authorization Account
        ==
Execution Command Account
        ==
Broker Adapter Account
```

### Behavior on Mismatch

| Condition | Result |
|-----------|--------|
| Account missing | **FAIL CLOSED** |
| Account differs | **FAIL CLOSED** |
| Account mapping ambiguous | **FAIL CLOSED** |
| Account credentials belong to another account | **FAIL CLOSED** |

### Principle

The Broker Adapter must verify that the account it is submitting to matches the authorized account. Any mismatch must fail closed.

## 20. Execution Mode

### Required Invariant

```
Execution Command mode
        ==
Authorization mode
        ==
Broker Adapter mode
```

### Behavior on Mismatch

| Condition | Result |
|-----------|--------|
| PAPER command + LIVE adapter | **FAIL CLOSED** |
| LIVE command + PAPER adapter | **FAIL CLOSED** |
| Unknown mode | **FAIL CLOSED** |

### Critical Rule

**A PAPER command must never accidentally reach a LIVE broker adapter. A LIVE command must never be silently downgraded to PAPER.**

The Broker Adapter must verify the execution mode matches the command mode. Any mismatch must fail closed.

## 21. Broker Credentials

### Where Credentials Belong

Broker credentials (API keys, access tokens, secrets, account credentials) belong **exclusively to the Broker Adapter** (or a dedicated broker connection manager).

### What Must NEVER Contain Credentials

| Artifact | Must NOT Contain Credentials |
|----------|------------------------------|
| `MarketScanResult` | **YES** |
| `TradePlan` | **YES** |
| `Operational Trade Intent` | **YES** |
| `Execution Authorization` | **YES** |
| `Execution Command` | **YES** |
| `PaperTrade` | **YES** |

### Principle

Credentials are broker-specific secrets. They must never enter the broker-neutral layers. The Broker Adapter is the only layer that should access credentials, and only through a secure connection manager.

## 22. Authentication vs Authorization

### Distinction

| Concept | Definition | Belongs To |
|---------|-----------|------------|
| **Authentication** | Who/which broker connection is being used | Broker Adapter / Connection Manager |
| **Authorization** | Whether this particular trade intent is permitted | Execution Authorization (upstream) |

### Rule

**Do not collapse these concepts.** Authentication verifies the identity of the broker connection. Authorization verifies that the specific trade intent is permitted. The Broker Adapter handles authentication. The upstream authorization layer handles authorization.

## 23. Broker Availability

### Conceptual Behavior

When the broker is unavailable, the Broker Adapter must NOT mutate upstream artifacts:

| Condition | Result |
|-----------|--------|
| Broker unavailable | **Adapter error** (not command rejection) |
| Connection unavailable | **Adapter error** |
| Market unavailable | **Adapter error** |
| Instrument unavailable | **Adapter error** |
| Trading halted | **Adapter error** |
| API unavailable | **Adapter error** |
| Rate limited | **Adapter error** (retryable) |

### Principle

Broker availability issues are **broker/execution concerns**. They must NOT mutate:
- `TradePlan`
- `Operational Trade Intent`
- `Execution Authorization`
- `Execution Command`
- `PaperTrade`

They produce adapter errors or execution results, not upstream mutations.

## 24. Broker Validation vs System Validation

### Distinction

| Validation Type | Includes | Belongs To |
|----------------|----------|-----------|
| **System validation** | Authorization validity, intent fingerprint, risk invariants, mode, account binding, command integrity | Execution Command layer |
| **Broker validation** | Symbol validity, exchange support, order-type support, price tick requirements, quantity restrictions, market status, broker-specific limits | Broker Adapter |

### Rule

The Broker Adapter performs broker-specific validation BEFORE submission. System validation is already complete by the time the command reaches the adapter.

## 25. No Silent Auto-Correction

### Principle

The future adapter should NOT automatically "fix" invalid commands.

### Classification

| Example | Classification | Behavior |
|---------|---------------|----------|
| Quantity 100 → 99 (floor) | **Mechanical normalization** | **PERMIT** (documented, risk-preserving) |
| Quantity 100 → 101 | **Material modification** | **REJECT** |
| Price 100.03 → 100.05 (tick) | **Mechanical normalization** | **PERMIT IF** within half-tick |
| Price 100.03 → 100.15 | **Material modification** | **REJECT** |
| Symbol A → Symbol B | **Material modification** | **REJECT** |
| Limit → Market | **Material modification** | **REJECT** |
| Account A → Account B | **Material modification** | **REJECT** |

### Rule

**Material modification must NOT be silently applied.** Only mechanical normalizations that preserve economic meaning are permitted.

## 26. Broker Response Separation

### What Comes Back from a Broker

| Data | Belongs To |
|------|-----------|
| Broker order ID | **Broker Response** |
| Accepted/Rejected | **Broker Response** |
| Broker status | **Broker Response** |
| Broker message | **Broker Response** |
| Submitted timestamp | **Broker Response** |
| Fill information | **Execution Result** |
| Execution price | **Execution Result** |
| Realized P&L | **Execution Result** |

### What Must NOT Be Inserted Into Upstream Artifacts

| Artifact | Must NOT Contain Broker Response Data |
|----------|--------------------------------------|
| `Execution Command` | **YES** |
| `TradePlan` | **YES** |
| `Operational Trade Intent` | **YES** |
| `Execution Authorization` | **YES** |
| `PaperTrade` | **YES** |

### Principle

Broker response data belongs downstream. The future boundary is:

```
Broker Adapter
        ↓
Broker Response / Execution Result
        ↓
Position / Portfolio layer
```

## 27. Order Identity

### Identity Hierarchy

```
plan_id
    ↓
intent_id
    ↓
authorization_id
    ↓
command_id
    ↓
client_order_id (generated by adapter)
    ↓
broker_order_id (generated by broker)
    ↓
execution_id (generated by broker)
    ↓
position_id (generated by broker)
```

### Identity Ownership

| Identity | Generated By | Layer | Immutable? |
|----------|-------------|-------|-----------|
| `plan_id` | TradePlan | Planning | Yes |
| `intent_id` | Operational Trade Intent | Operational | Yes |
| `authorization_id` | Execution Authorization | Authorization | Yes |
| `command_id` | Execution Command | Execution | Yes |
| `client_order_id` | Broker Adapter | Execution | Yes |
| `broker_order_id` | Broker | Broker | Yes (broker-assigned) |
| `execution_id` | Broker | Broker | Yes (broker-assigned) |
| `position_id` | Broker | Broker | Yes (broker-assigned) |

### Distinction

- `plan_id` ≠ `intent_id` ≠ `authorization_id` ≠ `command_id` ≠ `client_order_id` ≠ `broker_order_id` ≠ `execution_id` ≠ `position_id`
- Each identity belongs to a different layer.
- Each identity is deterministic within its layer (except broker-generated IDs).

## 28. Idempotency

### Invariant

**One execution command must not unintentionally become multiple broker orders.**

### Mechanisms

| Mechanism | Purpose |
|-----------|---------|
| `command_id` | Deduplicate commands |
| `authorization_id` | Bind to specific authorization |
| `client_order_id` | Deduplicate broker submissions |
| `idempotency key` | Prevent duplicate submission |
| Command fingerprint | Detect duplicate commands |

### Recommended Approach

1. **Deterministic `client_order_id`** — derived from `command_id` + broker context.
2. **Idempotency key** — derived from `command_id` + broker identifier.
3. **Command store** — track submitted commands to prevent duplicates.

### Principle

```
ONE COMMAND + ONE BROKER → AT MOST ONE BROKER ORDER (per command context)
```

## 29. Retry Safety

### Critical Issue

A timeout does NOT prove that no broker order exists. The broker may have received and processed the order even though the response was lost.

### Conceptual Requirement

**Reconciliation before retry.** Before retrying a submission, the adapter must:
1. Query the broker for existing orders using the `client_order_id` or idempotency key.
2. Determine whether the order already exists.
3. Only retry if the order does NOT already exist.

### Behavior

| Condition | Required Behavior |
|-----------|-------------------|
| Request times out | **RECONCILE BEFORE RETRY** |
| Response is lost | **RECONCILE BEFORE RETRY** |
| Broker returns ambiguous status | **RECONCILE** |
| Network disconnects after submission | **RECONCILE BEFORE RETRY** |

## 30. Replay Protection

### Preventing Command Replay

The architecture must prevent the same Execution Command from being replayed to produce multiple broker orders.

### Mechanisms

| Mechanism | Purpose |
|-----------|---------|
| `command_id` | Unique command identity |
| `authorization_id` | Authorization binding |
| `client_order_id` | Broker-side deduplication |
| Idempotency key | Prevent duplicate submission |
| Command state tracking | Track command state to prevent re-submission |

### Principle

```
COMMAND SUBMITTED → CANNOT BE REPLAYED (without explicit re-authorization)
```

## 31. Rate Limiting

### Where Rate Limits Belong

Rate limits belong to the **Broker Adapter** (or a dedicated broker connection manager). They must NOT alter the authorized trade semantics.

### Rule

Rate limiting is a transport concern. It must NOT:
- Modify the Execution Command
- Mutate upstream artifacts
- Change the authorized trade semantics

## 32. Broker-Specific Failure Handling

### Failure Classification

| Failure | Classification | Behavior |
|---------|---------------|----------|
| Invalid symbol | **Deterministic rejection** | Reject command |
| Invalid quantity | **Deterministic rejection** | Reject command |
| Invalid price | **Deterministic rejection** | Reject command |
| Unsupported order type | **Broker capability failure** | Reject command |
| Market closed | **Broker capability failure** | Reject command |
| Insufficient buying power | **Deterministic rejection** | Reject command |
| Broker rejection | **Deterministic rejection** | Report rejection |
| Authentication failure | **Authorization failure** | Report failure |
| Network failure | **Transient failure** | Retryable (with reconciliation) |
| Timeout | **Ambiguous state** | Reconcile before retry |
| Ambiguous submission result | **Ambiguous state** | Reconcile |

### Principle

Broker-specific failures must be isolated to the Broker Adapter. They must NOT mutate upstream artifacts.

## 33. Paper/Live Isolation

### Critical Boundary

The existing PaperTrade subsystem is frozen simulation infrastructure. A future Broker Adapter architecture must prevent:

```
PaperTrade
    ↓
Live Broker
```

or:

```
Paper execution command
    ↓
Live adapter
```

without explicit mode authorization.

### Rule

| Command Mode | Adapter Mode | Permitted? |
|-------------|-------------|-----------|
| PAPER | PAPER | **YES** |
| PAPER | LIVE | **NO** |
| LIVE | LIVE | **YES** |
| LIVE | PAPER | **NO** |
| Unknown | Any | **NO** |

### Principle

**Paper simulation must remain untouched.** The Broker Adapter must verify the execution mode before any broker interaction.

## 34. Position Separation

### Conceptual Separation

```
Broker Adapter
        ↓
Broker Order / Execution Result
        ↓
Position / Portfolio layer
```

### Rule

**The Broker Adapter must not own portfolio logic.** The adapter produces broker orders and receives execution results. Position management and portfolio aggregation belong to a downstream layer.

## 35. Point-in-Time Safety

### Prohibitions

The Broker Adapter must NOT:
- Modify historical analysis
- Modify `MarketScanResult`
- Modify `TradePlan`
- Modify `Operational Trade Intent`
- Modify `Execution Authorization`
- Modify `Execution Command`
- Modify `PaperTrade`
- Use future paper-trading outcomes
- Use future historical data

### Principle

Broker integration must not modify historical analytical state. Execution-time broker information belongs downstream.

## 36. Responsibility Matrix

| Artifact | Purpose | Authoritative Inputs | Outputs | Identity | Mutable | Broker-Specific? | Can Authorize? | Can Create Commands? | Can Translate Commands? | Can Contact Broker? | Can Create Orders? | Can Mutate Upstream? | Can Contain Fills? | Can Contain Positions? |
|----------|---------|---------------------|---------|----------|---------|-----------------|----------------|---------------------|------------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
| **MarketScanResult** | Multi-instrument opportunity scan | OHLCV candles, market context | Ranked opportunities | `scan_id` | Immutable | No | No | No | No | No | No | No | No | No |
| **TradePlan** | Risk/position-size calculation | Trade candidate geometry, account params | Sized plan with quantity/risk | `plan_id` | Immutable | No | No | No | No | No | No | No | No | No |
| **Operational Trade Intent** | Operational snapshot of eligible plan | TradePlan (by reference) | Broker-neutral intent snapshot | `intent_id` | Immutable | No | No | No | No | No | No | No | No | No |
| **Execution Authorization** | Permission for intent to proceed | Intent, policy gates, human decision | Authorization record | `authorization_id` | Immutable (versioned) | No | No | No | No | No | No | No | No | No |
| **Execution Command** | Request toward execution | Authorization + intent snapshot | Broker-agnostic command | `command_id` | Immutable | No | No | No | No | No | No | No | No | No |
| **Broker Adapter** | Broker-specific translation | Execution Command | Broker request | N/A | Mutable (stateless) | **Yes** | No | No | **Yes** | **Yes** | **Yes** | No | No | No |
| **Broker Connection** | Broker communication | Broker request | Broker response | N/A | Mutable | **Yes** | No | No | No | **Yes** | No | No | No | No |
| **Broker Order** | Broker-side order state | Broker request | Order state, fills | `broker_order_id` | Mutable (external) | **Yes** | No | No | No | No | No | No | **Yes** | No |
| **Broker Response** | Broker submission result | Broker request | Status, broker order ID | N/A | Mutable (external) | **Yes** | No | No | No | No | No | No | No | No |
| **Execution Result** | Execution outcome | Broker response | Fill, price, P&L | N/A | Mutable (external) | **Yes** | No | No | No | No | No | No | **Yes** | No |
| **Position** | Broker-side position state | Fills | Position state | `position_id` | Mutable (external) | **Yes** | No | No | No | No | No | No | No | **Yes** |
| **Portfolio** | Account-level aggregation | Positions | Portfolio state | N/A | Mutable (external) | **Yes** | No | No | No | No | No | No | No | No |

### Key Distinctions

- **Only the Broker Adapter can translate commands.**
- **Only the Broker Adapter can contact brokers.**
- **Only the Broker Adapter can create broker orders.**
- **Only the Authorization layer can authorize.**
- **Only the Execution Command layer can create commands.**
- **No artifact can both authorize and execute.**
- **MarketScanResult, TradePlan, Operational Trade Intent, Execution Authorization, Execution Command** are immutable analytical/operational records. They neither authorize nor execute.
- **Execution Command is broker-neutral.** Broker-specific translation is isolated to the Broker Adapter.
- **Broker Order, Broker Response, Execution Result, Position, Portfolio** are broker-specific/external. They must never enter upstream artifacts.

## 37. Future Broker Adapter Contract

### What is Broker Adapter?

The Broker Adapter is a **stateless, broker-specific translation layer** that consumes a broker-neutral Execution Command and translates it into a broker-specific request suitable for submission to a specific broker. It is the ONLY layer that:
- Knows which broker is being used
- Knows broker-specific symbol mappings
- Knows broker-specific order types
- Knows broker-specific exchange/segment/routing
- Knows broker-specific price/quantity constraints
- Contacts the broker
- Manages broker credentials

It does NOT:
- Authorize trades
- Create execution commands
- Own planning semantics
- Own portfolio logic
- Mutate upstream artifacts
- Silently alter authorized economic meaning

### What Does It Consume?

The Broker Adapter consumes exactly one input:
- **Execution Command** — an immutable, identity-bound, broker-neutral command that carries:
  - `command_id` (deterministic identity)
  - `authorization_id` (proof of authorization)
  - `intent_id` (authorized intent)
  - `plan_id` (provenance)
  - `instrument` (canonical name)
  - `direction` (LONG/SHORT)
  - `quantity` (authorized position size)
  - `entry` (authorized entry level)
  - `stop` (authorized stop level)
  - `target` (authorized target level)
  - `planned_risk` (authorized risk amount)
  - `maximum_risk` (risk limit bound)
  - `execution_mode` (PAPER/LIVE)
  - `content_fingerprint` (integrity verification)
  - `idempotency_key` (deduplication)

### What Does It Produce?

The Broker Adapter produces exactly one conceptual output:
- **Broker Request** — a broker-specific request containing:
  - Broker symbol (translated from canonical instrument)
  - Broker exchange/segment/routing
  - Broker order type(s)
  - Broker price(s) (tick-adjusted)
  - Broker quantity (lot-adjusted)
  - Broker account identifier
  - Client order ID (for idempotency)
  - Broker-specific flags/constraints

### What Is Broker-Specific?

| Concept | Broker-Specific? |
|---------|-----------------|
| Broker symbol | **YES** |
| Exchange code | **YES** |
| Segment | **YES** |
| Product type | **YES** |
| Order type | **YES** |
| Validity | **YES** |
| Disclosed quantity | **YES** |
| Trigger price | **YES** |
| Limit price | **YES** |
| Routing | **YES** |
| Account identifier | **YES** |
| Broker-specific flags | **YES** |
| Client order ID | **YES** |
| Broker order ID | **YES** (broker-generated) |
| Fill data | **YES** (broker-generated) |
| Execution result | **YES** (broker-generated) |

### What Must Remain Broker-Neutral?

| Concept | Broker-Neutral? |
|---------|----------------|
| `command_id` | **YES** |
| `authorization_id` | **YES** |
| `intent_id` | **YES** |
| `plan_id` | **YES** |
| `instrument` (canonical) | **YES** |
| `direction` (LONG/SHORT) | **YES** |
| `quantity` (authorized) | **YES** |
| `entry` (authorized) | **YES** |
| `stop` (authorized) | **YES** |
| `target` (authorized) | **YES** |
| `planned_risk` | **YES** |
| `maximum_risk` | **YES** |
| `execution_mode` | **YES** |
| `content_fingerprint` | **YES** |
| `idempotency_key` | **YES** |

### What Transformations Are Allowed?

| Transformation | Allowed | Condition |
|---------------|---------|-----------|
| Symbol translation | Yes | Deterministic, versioned, fail-closed on missing/ambiguous |
| Price tick rounding | Yes | Within half-tick tolerance |
| Quantity lot rounding | Yes | Floor-rounded, risk not increased |
| Direction mapping | Yes | Deterministic LONG→Buy, SHORT→Sell |
| Order type selection | Yes | Broker-specific representation of entry/stop/target |
| Exchange/routing selection | Yes | Broker-specific |
| Client order ID generation | Yes | Deterministic from command_id |
| Idempotency key generation | Yes | Deterministic from command_id + broker |

### What Transformations Are Prohibited?

| Transformation | Prohibited Reason |
|---------------|-------------------|
| Quantity increase | Increases authorized risk |
| Direction reversal | Reverses the trade |
| Entry change (material) | Changes execution geometry |
| Stop change (material) | Changes risk geometry |
| Target change (material) | Changes reward geometry |
| Instrument substitution | Different asset |
| Order type change (market↔limit) | Changes execution semantics |
| Risk increase | Exceeds authorized risk bound |
| Account change | Different account without authorization |
| Mode change (paper↔live) | Safety-critical boundary violation |
| Silent price alteration | Changes economic meaning |
| Credential insertion into command | Security violation |

### What Validation Occurs Before Broker Submission?

| Validation | Layer |
|-----------|-------|
| Authorization validity | Execution Command (already verified) |
| Intent fingerprint match | Execution Command (already verified) |
| Risk invariants | Execution Command (already verified) |
| Mode match | Broker Adapter |
| Account match | Broker Adapter |
| Symbol mapping exists | Broker Adapter |
| Symbol mapping unambiguous | Broker Adapter |
| Price within tick tolerance | Broker Adapter |
| Quantity within lot tolerance | Broker Adapter |
| Order type supported | Broker Adapter |
| Exchange/segment supported | Broker Adapter |
| Market status | Broker Adapter |
| Broker-specific limits | Broker Adapter |

### What Happens on Broker Rejection?

The Broker Adapter reports the rejection as a **Broker Response** or **Execution Result**. It does NOT:
- Mutate the Execution Command
- Mutate upstream artifacts
- Silently retry without reconciliation
- Alter the authorized trade semantics

### What Happens on Ambiguous Submission?

The Broker Adapter treats ambiguous submission (e.g., timeout) as an **ambiguous state**. It must:
1. NOT retry immediately.
2. Reconcile by querying the broker for existing orders.
3. Determine whether the order already exists.
4. Report the ambiguous state to the caller.

### How Are Duplicate Submissions Prevented?

- **Deterministic `client_order_id`** — derived from `command_id` + broker context.
- **Idempotency key** — derived from `command_id` + broker identifier.
- **Command store** — track submitted commands to prevent duplicates.
- **Broker-side deduplication** — broker uses `client_order_id` to detect duplicates.

### How Are Retries Handled?

- **Reconciliation before retry** — query broker for existing orders first.
- **Idempotency** — use the same `client_order_id` for retries.
- **No blind retry** — never retry without knowing the current state.

### How Are Credentials Isolated?

- Credentials belong to the Broker Adapter (or a dedicated connection manager).
- Credentials NEVER enter the Execution Command or upstream artifacts.
- Credentials are accessed only through a secure mechanism.
- Credentials are never logged, printed, or committed.

## 38. Future Lifecycle

### Conceptual Broker Adapter Lifecycle

The following states are evaluated for the future Broker Adapter lifecycle:

| State | Belongs To This Checkpoint? | Description |
|-------|---------------------------|-------------|
| `NOT_CONNECTED` | **Yes** | Adapter not connected to broker |
| `CONNECTED` | **Yes** | Adapter connected to broker |
| `VALIDATING` | **Yes** | Adapter validating command against broker constraints |
| `READY` | **Yes** | Adapter ready to submit |
| `SUBMITTING` | **No** | Broker submission (future layer) |
| `SUBMITTED` | **No** | Broker submitted (future layer) |
| `REJECTED` | **No** | Broker rejected (future layer) |
| `UNKNOWN` | **No** | Ambiguous state (future layer) |

### Checkpoint Boundary

This checkpoint covers only the states **before broker submission**:

```
NOT_CONNECTED → CONNECTED → VALIDATING → READY
```

The checkpoint stops at the `READY` state. The boundary between `READY` and `SUBMITTING` belongs to a future broker-integration checkpoint.

### State Distinctions

- **NOT_CONNECTED** — Adapter not connected to broker. No submission possible.
- **CONNECTED** — Adapter connected to broker. Ready to validate.
- **VALIDATING** — Adapter validating command against broker constraints (symbol, price, quantity, order type, exchange, market status).
- **READY** — Command validated and ready for broker submission.

## 39. Future Test Contract

The following test categories must be defined when the Broker Adapter is implemented:

1. Only valid Execution Commands accepted
2. Authorization binding preserved
3. Command fingerprint preserved
4. Broker-neutral command remains unchanged
5. Symbol mapping deterministic
6. Missing symbol mapping fails closed
7. Ambiguous symbol mapping fails closed
8. Direction mapping deterministic
9. Quantity cannot increase
10. Quantity-step handling cannot increase risk
11. Price normalization cannot silently alter economics
12. Stop semantics preserved
13. Target semantics preserved
14. Account binding preserved
15. Execution mode preserved
16. Paper/live separation enforced
17. Credentials excluded from upstream artifacts
18. Unsupported order type rejected
19. Unsupported instrument rejected
20. Invalid price rejected
21. Invalid quantity rejected
22. Broker rejection isolated
23. Network failure isolated
24. Timeout treated as ambiguous when appropriate
25. Retry does not duplicate order
26. Idempotency enforced
27. Replay protection
28. Broker order ID remains downstream
29. Fill data remains downstream
30. Position data remains downstream
31. Upstream artifacts remain immutable
32. No analytical mutation
33. No planning mutation
34. No authorization mutation
35. No PaperTrade mutation
36. No future historical data
37. No direct dashboard-to-broker path
38. No silent auto-correction
39. Fail-closed behavior
40. No actual broker submission during tests

## 40. Recommended Next Boundary

The next boundary to audit and design is:

```
Broker Adapter
        ↓
Broker Order / Execution Result
```

This would define what a broker order is, how it is represented, what execution results look like, and how they remain isolated from the broker-neutral command layer.

## 41. Limitations

1. **No Broker Adapter layer exists.** This audit defines the contract only. Implementation is a future checkpoint.
2. **No Execution Command layer exists.** The command contract is defined but not implemented.
3. **No authorization layer exists.** The authorization contract is defined but not implemented.
4. **No operational trade intent layer exists.** The intent contract is defined but not implemented.
5. **No mode system (paper/live).** The adapter must distinguish paper from live when a mode system is introduced.
6. **No emergency stop exists.** A future execution system will require a global emergency-disable boundary.
7. **No account/portfolio layer.** Account binding for commands is deferred.
8. **No broker integration.** The adapter boundary stops before broker submission.
9. **No human approval UI.** The human consent mechanism is undefined.
10. **No actual broker.** No broker connection, order submission, or live trading is implemented.

## 42. Implementation Decision

**NO IMPLEMENTATION CHANGES.**

The Execution Command → Broker Adapter boundary can be clearly defined. No blocking architectural defect exists. The conceptual contract is sound.

## 43. Final Verdict

**PASS**

The system now has a clearly defined conceptual boundary between a broker-neutral Execution Command and a future broker-specific Broker Adapter. The adapter may perform deterministic broker translation and validation but must not silently alter the authorized economic meaning of the command. Broker responses, order IDs, fills, execution results, positions, and portfolio state remain downstream. No broker connection or order submission is implemented.

---

## Appendix A: Test Baseline

### Commands Executed

```
python -m pytest tests/ -q
```

### Results

| Suite | Result |
|-------|--------|
| Full suite | 4849 passed, 2 failed (pre-existing: missing `yfinance`), 3 skipped |

### Failures

The 2 failures are pre-existing and unrelated to this checkpoint:
- `tests/test_live_data_integration.py::TestProviderFailure::test_yahoo_not_ready_when_no_backend`
- `tests/test_live_data_integration.py::TestSerializationBackwardCompat::test_default_service_yahoo_with_symbol_map`
