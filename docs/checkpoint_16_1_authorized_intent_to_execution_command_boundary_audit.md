# Checkpoint 16.1 — Authorized Intent to Execution Command Boundary Audit & Design

## 1. Purpose

Determine what exact information is permitted to cross the boundary from
`ExecutionAuthorization` (Checkpoint 15, frozen) to a future
`ExecutionCommand` layer, and what transformations, responsibilities, and
artifacts must remain strictly on the authorization side, the command side,
or downstream in the Broker Adapter.

This checkpoint defines the **first future architectural boundary after
ExecutionAuthorization**: the Authorized Intent Snapshot / Execution Command
layer. It is an **audit + contract design only** checkpoint. No
implementation of `ExecutionCommand` is performed.

## 2. Scope

- Inspect the repository for any existing `ExecutionCommand`, broker order,
  execution result, position, or portfolio abstractions.
- Audit the `ExecutionAuthorization` contract established in Checkpoints
  15.1–15.5 and frozen in Checkpoint 15.6.
- Audit the `OperationalTradeIntent` contract established in Checkpoints
  14.1–14.5 and frozen in Checkpoint 14.6.
- Audit the `TradePlan` contract (frozen, Product Phase 4).
- Audit the `PaperTrade` contract (frozen, Product Phase 5).
- Audit the dashboard / API layer for any execution reachability.
- Determine the boundary subject: what an `ExecutionCommand` must consume,
  produce, and preserve.
- Define the field-by-field crossing audit, normalization rules,
  re-authorization requirements, fail-closed behavior, and lifecycle.
- Specify the future test contract.

## 3. Frozen Prior Checkpoints

| Checkpoint | Status | Boundary |
|-----------|--------|----------|
| 11.1–11.8 | FROZEN | Market data → candle integrity → features → setup → evidence → `MarketScanResult` |
| 12.1–12.6 | FROZEN | `MarketScanResult` → `TradePlan` → `PaperTrade` → journal/performance → dashboard |
| 13.1–13.6 | FROZEN | Pre-execution architecture (Operational Trade Intent, Execution Authorization, Execution Command, Broker Adapter contracts defined) |
| 14.1–14.6 | FROZEN | `TradePlan` → `OperationalTradeIntent` (explicit, additive, sibling path) |
| 15.1–15.6 | FROZEN | `OperationalTradeIntent` → `ExecutionAuthorization` (verification-based, immutable, fail-closed) |

No frozen checkpoint is modified by this audit.

## 4. Exact Files Inspected

### Core Models
- `src/engine/models/execution_authorization.py` — `ExecutionAuthorization`, `AuthorizationStatus`, `create_authorization()` (Checkpoint 15.2, frozen)
- `src/engine/models/operational_trade_intent.py` — `OperationalTradeIntent`, `create_intent_from_plan()` (Checkpoint 14.2, frozen)
- `src/engine/models/trade_plan.py` — `TradePlan`, `RiskPlanStatus`, `QuantityStatus`, `QuantitySpec` (frozen, Product Phase 4)
- `src/engine/models/paper_trade.py` — `PaperTrade`, `PaperTradeStatus`, `PaperExitReason` (frozen, Product Phase 5)
- `src/engine/models/trade_candidate.py` — `TradeCandidate` (frozen)
- `src/engine/models/trade_decision.py` — `TradeDecision`, `DecisionClassification` (frozen)

### Intelligence Engines
- `src/engine/intelligence/execution_authorization.py` — `ExecutionAuthorizationEngine` (Checkpoint 15.3, frozen)
- `src/engine/intelligence/operational_trade_intent.py` — `OperationalTradeIntentEngine` (Checkpoint 14.4, frozen)
- `src/engine/intelligence/paper_trading.py` — `PaperTradingEngine` (frozen, Product Phase 5)
- `src/engine/intelligence/trade_planning.py` — `TradePlanningEngine` (frozen, Product Phase 4)

### Persistence Layer
- `src/engine/persistence/execution_authorization_serialization.py` — deterministic JSON serialization (Checkpoint 15.5, frozen)
- `src/engine/persistence/execution_authorization_store.py` — atomic filesystem store (Checkpoint 15.5, frozen)
- `src/engine/persistence/exceptions.py` — typed exception hierarchy (Checkpoint 15.5, frozen)

### Dashboard / Presentation
- `src/dashboard/views.py` — `ActionabilityState`, `derive_actionability`, `DashboardTradeView`, `OperationalTradeIntentView`
- `src/dashboard/services.py` — `DashboardAnalysisService`, `OperationalTradeIntentRequest`
- `src/dashboard/app.py` — FastAPI routes

### Prior Audits
- `docs/checkpoint_13_3_execution_authorization_boundary_audit.md`
- `docs/checkpoint_13_4_execution_authorization_to_execution_command_boundary_audit.md`
- `docs/checkpoint_13_5_execution_command_to_broker_adapter_boundary_audit.md`
- `docs/checkpoint_13_6_final_execution_architecture_integration_and_freeze_audit.md`
- `docs/checkpoint_14_6_final_operational_trade_intent_integration_and_freeze_audit.md`
- `docs/checkpoint_15_6_final_execution_authorization_integration_and_freeze_audit.md`

### Tests (baseline only — not modified)
- `tests/test_execution_authorization.py` — 97 tests
- `tests/test_execution_authorization_engine.py` — 84 tests
- `tests/test_execution_authorization_store.py` — 57 tests
- `tests/test_operational_trade_intent.py` — 125 tests
- `tests/test_operational_trade_intent_engine.py` — 69 tests
- `tests/test_operational_trade_intent_application.py` — 58 tests

## 5. Existing Execution-Related Abstractions

### Search Results

The following keywords were searched across the entire codebase:

| Keyword | Result |
|---------|--------|
| `ExecutionCommand` | **No classes found.** |
| `execution_command` | **No classes found.** |
| `command_id` | **No classes found.** |
| `BrokerAdapter` | **No classes found.** Mentioned only in conceptual audit documents. |
| `BrokerOrder` | **No classes found.** |
| `ExecutionResult` | **No classes found.** |
| `place_order`, `PlaceOrder` | **No classes found.** |
| `submit_order`, `SubmitOrder` | **No classes found.** |
| `order_id`, `OrderID` | **No classes found.** |
| `fill_price`, `Fill` | **No classes found.** |
| `position`, `Position` | **No classes found.** (Only in "position sizing" context.) |
| `portfolio`, `Portfolio` | **No classes found.** |
| `execution_mode`, `live_mode` | **No classes found.** |
| `broker_symbol`, `exchange` (order) | **No classes found.** |
| `order_type`, `validity` (order) | **No classes found.** |
| `account` (broker account) | **No classes found.** |
| `client_order_id` | **No classes found.** |

### Conclusion

**No execution command, broker adapter, broker order, execution result,
position, portfolio, or execution-mode abstraction exists.** The repository
has no live trading capability. This is a clean baseline for defining the
Execution Command boundary.

The only provider integrations (Upstox, Yahoo) are **strictly DATA
providers** — they retrieve OHLCV data only. Neither has any execution
capability.

## 6. Current Boundary State

### What Exists Today

The current execution-related architecture (all frozen, all in `src/engine/`):

```
TradePlan (Checkpoint 12.6, frozen)
    ↓
OperationalTradeIntent (Checkpoint 14.6, frozen)
    ↓
ExecutionAuthorization (Checkpoint 15.6, frozen)
    ↓
[GAP — no ExecutionCommand exists]
    ↓
[FUTURE: Broker Adapter — not implemented]
    ↓
[FUTURE: Broker Order — not implemented]
```

### What Is Frozen

The following are **frozen and must not be modified**:

- `OperationalTradeIntent` model (`src/engine/models/operational_trade_intent.py`)
- `OperationalTradeIntentEngine` (`src/engine/intelligence/operational_trade_intent.py`)
- `OperationalTradeIntentApplicationService` (`src/engine/intelligence/operational_trade_intent_application.py`)
- `ExecutionAuthorization` model (`src/engine/models/execution_authorization.py`)
- `ExecutionAuthorizationEngine` (`src/engine/intelligence/execution_authorization.py`)
- `ExecutionAuthorizationStore` (`src/engine/persistence/execution_authorization_store.py`)
- `ExecutionAuthorizationSerialization` (`src/engine/persistence/execution_authorization_serialization.py`)

### What Is Not Frozen (Future Implementation Surface)

The following are **not yet implemented** and are the design target for this
audit:

- `ExecutionCommand` model (future)
- `ExecutionCommandEngine` or equivalent constructor (future)
- `ExecutionCommandStore` / serialization (future, pattern established by Checkpoint 15.5)
- `ExecutionCommandPersistence` (future)

## 7. Boundary Subject

### What Is the Execution Command?

The `ExecutionCommand` is the **first downstream artifact of an
AUTHORIZED `OperationalTradeIntent`**. It is a deterministic, immutable,
broker-neutral snapshot that expresses the authorized intent in a form
suitable for submission to a future Broker Adapter.

The Execution Command:
- **Consumes** an AUTHORIZED `ExecutionAuthorization` record.
- **Preserves** the authorization's proof of valid authorization
  (`authorization_id`, `content_fingerprint`).
- **Carries** the authorized economic fields by value (immutable snapshot).
- **Remains** broker-neutral — it knows nothing about exchanges, segments,
  order types, or broker-specific representations.
- **Fails closed** on any authorization mismatch, material field change, or
  missing data.
- **Does NOT** contact brokers, place orders, manage positions, or
  calculate P&L.

### Conceptual Flow

```
OperationalTradeIntent (what)
    ↓
ExecutionAuthorization (permission)
    ↓
ExecutionCommand (authorized "how" — broker-neutral)
    ↓
[FUTURE: Broker Adapter — broker-specific translation]
    ↓
[FUTURE: Broker Order — external representation]
```

## 8. Input Contract

### Sole Upstream Input

**One input:** An `ExecutionAuthorization` record in `AUTHORIZED` status.

The `ExecutionCommand` constructor must receive:
- The `ExecutionAuthorization` record (by value/reference — it is immutable).
- Optionally, the source `OperationalTradeIntent` (for verification).

### What the Command Must Receive

| Input | Required | Purpose |
|-------|----------|---------|
| `authorization_id` | Yes | Proof of valid authorization |
| `intent_id` | Yes | The authorized intent identity |
| `plan_id` | Yes | Provenance reference |
| `content_fingerprint` | Yes | Verify intent has not changed |
| `instrument` | Yes | Target instrument (canonical name) |
| `direction` | Yes | LONG or SHORT |
| `quantity` | Yes | Authorized position size |
| `entry` | Yes | Authorized entry level |
| `stop` | Yes | Authorized stop level |
| `target` | Yes | Authorized target level |
| `planned_risk` | Yes | Authorized planned risk |
| `maximum_risk` | Yes | Risk limit bound |
| `execution_mode` | Yes | PAPER or LIVE (from authorization) |
| `authorized_at` | Yes | Authorization timestamp |
| `valid_from` | Yes | Command validity start |
| `valid_until` | Yes | Command validity end |
| `created_at` | Yes | Command creation timestamp |

### What the Command Must NOT Receive

The `ExecutionCommand` constructor must NOT accept:
- Broker-specific fields (symbol, exchange, segment, product type, routing)
- Broker order types (market, limit, stop, stop-limit)
- Broker response data (fill price, fill quantity, order ID)
- Position or portfolio references
- Broker credentials or authentication tokens
- Paper-trade simulation state
- Future market data or candles

## 9. Output Contract

### What the Execution Command Produces

| Output | Purpose |
|--------|---------|
| `command_id` | Deterministic identity (`"cmd-" + sha256[:16]`) |
| `authorization_id` | Proof of valid authorization |
| `intent_id` | The authorized intent |
| `content_fingerprint` | Fingerprint of authorized intent content |
| `instrument` | Canonical instrument name |
| `direction` | LONG or SHORT |
| `quantity` | Authorized quantity |
| `entry` | Authorized entry level |
| `stop` | Authorized stop level |
| `target` | Authorized target level |
| `planned_risk` | Authorized planned risk |
| `maximum_risk` | Authorized risk limit |
| `execution_mode` | PAPER or LIVE |
| `created_at` | Command creation timestamp |
| `idempotency_key` | Deduplication key |
| `normalization_applied` | Audit record of any safe normalizations |

### What the Execution Command Must NOT Produce

The `ExecutionCommand` must NOT produce:
- Broker-specific request payloads (belongs to Broker Adapter)
- Broker order IDs (generated by broker)
- Fill prices, fill quantities, execution prices (broker-response data)
- Position IDs, portfolio state (belongs to portfolio layer)
- Trading signals, recommendations, or predictions

## 10. Field-by-Field Crossing Audit

### Fields That Cross from Authorization to Command (Permitted)

| Field | Source | Type | Broker-Neutral? | Transformation Allowed? |
|-------|--------|------|-----------------|------------------------|
| `command_id` | Generated | `str` | YES | Deterministic identity only |
| `authorization_id` | Authorization | `str` | YES | None — copied verbatim |
| `intent_id` | Authorization → Intent | `str` | YES | None — copied verbatim |
| `plan_id` | Authorization → Intent | `str` | YES | None — copied verbatim |
| `content_fingerprint` | Authorization → Intent | `str` | YES | None — copied verbatim |
| `instrument` | Authorization → Intent | `str` | YES | None — canonical name only |
| `direction` | Authorization → Intent | `str` | YES | None — LONG/SHORT only |
| `quantity` | Authorization → Intent | `Decimal` | YES | Safe normalization only (see §13) |
| `entry` | Authorization → Intent | `Decimal` | YES | Safe normalization only (see §13) |
| `stop` | Authorization → Intent | `Decimal` | YES | Safe normalization only (see §13) |
| `target` | Authorization → Intent | `Decimal` | YES | Safe normalization only (see §13) |
| `planned_risk` | Authorization → Intent | `Decimal` | YES | None — must not increase |
| `maximum_risk` | Authorization → Intent | `Decimal` | YES | None — must not increase |
| `execution_mode` | Authorization | `str` | YES | None — PAPER/LIVE only |
| `authorized_at` | Authorization | `datetime` | YES | None — copied verbatim |
| `valid_from` | Authorization | `datetime` | YES | None — copied verbatim |
| `valid_until` | Authorization | `datetime` | YES | None — copied verbatim |
| `created_at` | Generated | `datetime` | YES | Caller-supplied |

### Fields That Must NOT Cross (Blocked at Command Boundary)

| Concept | Classification | Rationale |
|---------|---------------|-----------|
| Broker symbol | **Broker-specific** | Internal instrument → broker symbol translation belongs to Broker Adapter |
| Exchange code | **Broker-specific** | Broker-specific routing |
| Segment | **Broker-specific** | Broker-specific market segment |
| Product type | **Broker-specific** | Broker-specific product classification |
| Order type | **Broker-specific** | Broker-specific order representation (market/limit/stop/stop-limit) |
| Validity | **Broker-specific** | Broker-specific order validity |
| Disclosed quantity | **Broker-specific** | Broker-specific order attribute |
| Trigger price | **Broker-specific** | Broker-specific stop representation |
| Limit price | **Broker-specific** | Broker-specific limit representation |
| Routing | **Broker-specific** | Broker-specific exchange routing |
| Account identifier | **Broker-specific** | Broker-specific account reference |
| Broker-specific flags | **Broker-specific** | Broker-specific constraints/flags |
| Broker order ID | **Broker-generated** | Generated by broker, not by command |
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
| Target 2 | **Not supported** | Architecture produces a single structural target |
| Paper-trade state | **Must never cross** | Paper trading is a sibling path, not execution |

## 11. Authorization Binding

### Required Binding

The `ExecutionCommand` must bind to the AUTHORIZED `ExecutionAuthorization`
record. The following invariants must hold:

```
command.authorization_id == authorization.authorization_id
command.intent_id == authorization.intent_id
command.plan_id == authorization.plan_id
command.content_fingerprint == authorization.content_fingerprint
```

### Content Fingerprint Verification

The `content_fingerprint` on the command must match the `content_fingerprint`
on the authorization. This must be verified **before** command creation.

If the fingerprints do not match, the command must fail closed:
**NO COMMAND**.

### Prevention of Misbinding

The system must prevent:

```
Authorization A
    ↓
Intent B (different intent)
    ↓
Execution Command
```

This is prevented by:
1. **Authorization binds to `intent_id`** — the authorization record references a specific intent.
2. **Content fingerprint** — the authorization binds to a fingerprint of the intent's economic content.
3. **Command creation verifies both** — the command constructor verifies the authorization is valid for the exact intent.

## 12. Authorization State Verification

### Required State

The authorization must be in `AUTHORIZED` state. Any other state must fail
closed.

| Authorization State | Command Creation Behavior |
|--------------------|--------------------------|
| `AUTHORIZED` | **Permitted** (if all other checks pass) |
| `ELIGIBLE` | **NO COMMAND** — not yet authorized |
| `EXPIRED` | **NO COMMAND** — fail closed |
| `REVOKED` | **NO COMMAND** — fail closed |
| `SUPERSEDED` | **NO COMMAND** — fail closed |
| `UNAUTHORIZED` | **NO COMMAND** — no authorization exists |
| Missing | **NO COMMAND** — fail closed |
| Unknown | **NO COMMAND** — fail closed |
| Malformed | **NO COMMAND** — fail closed |

### Principle

```
NO VALID AUTHORIZATION → NO EXECUTION COMMAND
```

Any ambiguity, missing data, or unexpected state must result in **NO
COMMAND**. This is a safety-critical boundary.

## 13. Price and Quantity Normalization

### Price Normalization

The Execution Command may apply **safe normalizations** to prices, but only
within strict tolerance:

| Transformation | Allowed? | Rule |
|----------------|----------|------|
| Tick-size rounding | **YES IF** within half a tick | Must not materially change geometry |
| Decimal precision adjustment | **YES IF** within tolerance | Must not materially change geometry |
| Minimum increment rounding | **YES IF** floor-rounded | Must not increase risk |
| Price band clamping | **PROHIBITED** | Must NOT silently clamp — reject instead |
| Broker restriction bypass | **PROHIBITED** | Must not bypass broker restrictions silently |

### Critical Rule

**The execution layer MUST NOT silently round a price in a way that
invalidates the authorization.**

### Recommended Normalization Location

Price normalization should happen **during execution-command construction**,
not inside the Broker Adapter. This ensures:
1. The authorization can verify the normalized price matches the authorized level.
2. The Broker Adapter receives a clean, validated price.
3. Price normalization is auditable before the command is constructed.

### Tolerance Rule

Normalized prices must be within **half a tick** of the authorized price.
If normalization would move the price more than half a tick, the command
must fail closed (require re-authorization).

### Quantity Normalization

| Transformation | Allowed? | Rule |
|----------------|----------|------|
| Integer rounding | **YES IF** floor-rounded | Must not increase risk |
| Lot-size rounding | **YES IF** floor-rounded | Must not increase risk |
| Quantity step rounding | **YES IF** floor-rounded | Must not increase risk |
| Minimum quantity enforcement | **YES IF** rejects below minimum | Must not silently increase |
| Maximum quantity enforcement | **YES IF** rejects above maximum | Must not silently increase |

### Critical Invariant

```
Executed quantity risk <= Authorized planned_risk <= maximum_risk
```

**The Execution Command MUST NOT silently increase authorized risk.**

A broker-side quantity transformation MUST NOT silently increase authorized
risk. If rounding would increase risk, the command must fail closed.

## 14. Order-Type Boundary

### Where Order Types Belong

| Order Type | Belongs To |
|-----------|-----------|
| Market | **Broker Adapter** (broker-specific representation) |
| Limit | **Broker Adapter** (broker-specific representation) |
| Stop | **Broker Adapter** (broker-specific representation) |
| Stop-Limit | **Broker Adapter** (broker-specific representation) |
| Other broker order types | **Broker Adapter** (broker-specific translation) |

### Principle

**Do NOT introduce an order type into TradePlan, Operational Trade Intent,
Execution Authorization, or Execution Command.** The Execution Command
expresses intent in terms of entry/stop/target geometry. The Broker Adapter
decides how to represent this as broker-specific order types.

### Recommended Boundary

```
Execution Command = broker-neutral (entry/stop/target geometry)
Broker Adapter = broker-specific order translation
Broker Order = broker-specific order representation
```

## 15. Broker-Specific Translation

### Where Broker-Specific Concepts Belong

| Concept | Belongs To |
|---------|-----------|
| Broker symbol | **Broker Adapter** |
| Exchange code | **Broker Adapter** |
| Segment | **Broker Adapter** |
| Product type | **Broker Adapter** |
| Order type (broker-specific) | **Broker Adapter** |
| Validity (broker-specific) | **Broker Adapter** |
| Routing | **Broker Adapter** |
| Account | **Broker Adapter** |
| Client order ID | **Broker Adapter** (generated, passed to broker) |

### Preferred Conceptual Boundary

```
Operational Trade Intent = broker-neutral
Execution Authorization = broker-neutral
Execution Command = broker-neutral (entry/stop/target geometry)
Broker Adapter = broker-specific translation (symbol, exchange, product, routing)
```

### Principle

The Execution Command must remain **broker-neutral**. It expresses the
intent to buy/sell a quantity of an instrument at a price, without knowing
which broker, exchange, or routing will be used. The Broker Adapter
translates the broker-neutral command into a broker-specific request.

## 16. Authorization Binding at Command Creation

### Required Verification Sequence

Before an `ExecutionCommand` can be constructed, the following must be
verified **in order**:

1. **Authorization exists** — the `authorization_id` resolves to a valid
   `ExecutionAuthorization` record.
2. **Authorization is AUTHORIZED** — the authorization status is
   `AuthorizationStatus.AUTHORIZED`.
3. **Authorization is not expired** — `valid_until` is either None or
   strictly greater than the current time.
4. **Authorization is not revoked** — status is not `REVOKED`.
5. **Authorization is not superseded** — status is not `SUPERSEDED`.
6. **Intent binding** — `authorization.intent_id` matches the intent being
   commanded.
7. **Content fingerprint** — `authorization.content_fingerprint` matches the
   intent's current fingerprint.

If any check fails: **NO COMMAND**.

### Re-Authorization Requirements

Re-authorization is required when any **material field** changes:

| Field | Change | Re-Authorization Required |
|-------|--------|--------------------------|
| `instrument` | Changed | **YES** |
| `direction` | Changed | **YES** |
| `quantity` | Changed | **YES** |
| `entry` | Changed materially | **YES** |
| `stop` | Changed materially | **YES** |
| `target` | Changed materially | **YES** |
| `planned_risk` | Increased | **YES** |
| `maximum_risk` | Decreased | **YES** |
| `account_capital` | Changed | **YES** (affects quantity via risk %) |
| `risk_percent` | Changed | **YES** (affects quantity via risk %) |
| `execution_mode` | Paper → Live | **YES** |

### Non-Material Technical Normalization

The following changes do NOT require re-authorization:

| Change | Reason |
|--------|--------|
| Price normalization within half-tick tolerance | Safe technical normalization |
| Quantity floor-rounding to lot size | Does not increase risk |
| Timestamp advancement within validity window | Expected time passage |
| Broker-specific symbol mapping | Broker Adapter responsibility |

## 17. Execution Mode Boundary

### Execution Mode Semantics

| Mode | Meaning | Command Behavior |
|------|---------|-----------------|
| `PAPER` | Simulation — no real orders | Command may be constructed for paper execution |
| `LIVE` | Real trading — real orders | Command may be constructed for live execution |

### Critical Rule

**The execution mode is determined by the AUTHORIZATION, not by the
command constructor.** The `ExecutionAuthorization` carries the
`execution_mode` field. The `ExecutionCommand` inherits this value.

The command constructor must NOT:
- Accept an `execution_mode` parameter that overrides the authorization.
- Default to `LIVE` when the authorization mode is `PAPER`.
- Allow mode transitions without re-authorization.

### Paper/Live Isolation

```
PAPER mode command → [FUTURE: Paper Execution Adapter]
LIVE mode command → [FUTURE: Live Broker Adapter]
```

The command itself is mode-agnostic in structure. The mode determines
which downstream adapter processes it.

## 18. Paper Trading Isolation

### Paper Trade Is a Sibling Path

```
TradePlan (authoritative)
    |
    +---> PaperTrade (sibling path — simulation/validation)
    |
    +---> OperationalTradeIntent → ExecutionAuthorization → ExecutionCommand
```

The `PaperTrade` path and the `ExecutionCommand` path are **independent
sibling paths** from `TradePlan`. They must never converge:

- A `PaperTrade` must never become an `ExecutionCommand`.
- An `ExecutionCommand` must never reference a `PaperTrade`.
- A paper-trade result must never modify an `ExecutionAuthorization`.
- A paper-trade result must never modify an `OperationalTradeIntent`.

### Verification

The command constructor must verify:
- `command.intent_id` does not reference a `PaperTrade`.
- `command.authorization_id` does not reference a paper-trade artifact.
- No paper-trade lifecycle fields appear on the command.

## 19. Dashboard and API Isolation

### Current Dashboard State

The dashboard (Product Phases 1–3) is a **presentation-only layer**:
- It displays existing analysis outputs.
- It never creates `ExecutionCommand` artifacts.
- It never places orders or contacts brokers.
- The `POST /api/operational-trade-intent` endpoint creates intents only.
- No dashboard endpoint can produce an `ExecutionCommand`.

### Future Dashboard Integration

When execution commands are eventually implemented, the dashboard must:
- Display `ExecutionCommand` status as **read-only presentation**.
- Never construct commands directly from user input without authorization.
- Never bypass the `ExecutionAuthorization` layer.
- Never allow command mutation after creation (commands are immutable).

## 20. Point-in-Time Safety

### Authorization Time Boundary

The `ExecutionCommand` is only valid within its authorization's time window:

```
valid_from <= now < valid_until
```

If the current time is outside this window: **NO COMMAND**.

### Command Validity

The command itself has a `created_at` timestamp. A command that was valid
at creation time may become invalid as time passes (if `valid_until` is
reached). The downstream Broker Adapter must verify command validity at
submission time, not just at creation time.

### No Future Data

The `ExecutionCommand` constructor must NOT:
- Accept future candles or market data.
- Accept forward-looking price updates.
- Accept any parameter that would allow look-ahead bias.

## 21. Determinism and Identity

### Command Identity

The `command_id` must be deterministic:

```
command_id = "cmd-" + sha256[:16] of canonical command content
```

The same authorization + intent + normalization must always produce the
same `command_id`.

### Idempotency Key

The `idempotency_key` is derived from the `command_id` and is used by the
Broker Adapter for deduplication. It must be stable across serialization
round-trips.

### Deterministic Normalization

Any safe normalization applied during command construction must be:
- Deterministic (same input → same output).
- Auditable (recorded in `normalization_applied`).
- Bounded (within half-tick for prices, floor-only for quantities).

## 22. Immutability

### Command Is Immutable

The `ExecutionCommand` must be:
- `@dataclass(frozen=True, slots=True)` — immutable, slots-enabled.
- All fields must be immutable types (`str`, `Decimal`, `datetime`, `tuple`).
- No mutable fields (no `list`, `dict`, `set`).
- No methods that mutate state.

### Authorization Is Not Mutated

The command constructor must NOT mutate the source `ExecutionAuthorization`
record. The authorization is immutable by design.

### Intent Is Not Mutated

The command constructor must NOT mutate the source `OperationalTradeIntent`.
The intent is immutable by design.

## 23. Fail-Closed Behavior

### Principle

```
NO VALID AUTHORIZATION → NO EXECUTION COMMAND
```

Any of the following conditions must result in **NO COMMAND**:

| Condition | Behavior |
|-----------|----------|
| Authorization missing | NO COMMAND |
| Authorization not AUTHORIZED | NO COMMAND |
| Authorization EXPIRED | NO COMMAND |
| Authorization REVOKED | NO COMMAND |
| Authorization SUPERSEDED | NO COMMAND |
| Intent binding mismatch | NO COMMAND |
| Content fingerprint mismatch | NO COMMAND |
| Invalid instrument | NO COMMAND |
| Invalid direction | NO COMMAND |
| Missing/zero quantity | NO COMMAND |
| Missing entry/stop/target | NO COMMAND |
| Non-positive risk distance | NO COMMAND |
| planned_risk > maximum_risk | NO COMMAND |
| Execution mode change without re-authorization | NO COMMAND |
| Price normalization exceeds half-tick tolerance | NO COMMAND |
| Quantity rounding would increase risk | NO COMMAND |

### Error Reporting

Every failure must produce a typed error/reason that explains WHY the
command was not created. Errors must never be silently swallowed.

## 24. Lifecycle Boundary

### Command Lifecycle

The `ExecutionCommand` has a simple two-state lifecycle:

```
NOT_CREATED → CREATED
```

There is no `CANCELLED`, `SUBMITTED`, `FILLED`, or `EXPIRED` state on the
command itself. Those states belong to downstream layers:

- `CANCELLED` / `SUBMITTED` / `FILLED` → Broker Order / Execution Result
- `EXPIRED` → Authorization lifecycle (already defined in Checkpoint 15)

### Command Is a Snapshot

The `ExecutionCommand` is a **point-in-time snapshot** of an authorized
intent. It does not track state changes after creation. If the underlying
intent or authorization changes, a **new** command must be created (which
requires re-authorization if material fields changed).

## 25. Relationship to Other Boundaries

### Execution Command → Broker Adapter (Checkpoint 13.5, frozen)

The `ExecutionCommand` is the **sole input** to the Broker Adapter. The
Broker Adapter:
- Translates broker-neutral command fields to broker-specific
  representations.
- Applies safe normalizations (within tolerance).
- Validates broker-specific constraints (minimum quantity, price bands).
- Produces a broker-specific order request.
- MUST NOT silently alter authorized economic meaning.

### Execution Authorization → Execution Command (this checkpoint)

The `ExecutionCommand` consumes the `ExecutionAuthorization` and the
authorized `OperationalTradeIntent`. The command:
- Verifies authorization is AUTHORIZED.
- Copies authorized fields by value.
- Applies safe normalizations.
- Produces a deterministic, immutable command artifact.

### Execution Command → Execution Result (future, not designed here)

The Broker Adapter submits the command to the broker. The broker produces
an `ExecutionResult` (or equivalent) that:
- Carries broker-generated identifiers.
- Reports actual fill prices and quantities.
- Reports execution timestamp.
- Is completely separate from the `ExecutionCommand`.

## 26. What Must NOT Cross the Boundary

### Prohibited Concepts

The following concepts must NEVER appear on an `ExecutionCommand`:

| Concept | Why Prohibited |
|---------|---------------|
| Broker symbol / exchange / segment | Broker-specific — belongs to Broker Adapter |
| Order type (market/limit/stop) | Broker-specific — belongs to Broker Adapter |
| Broker order ID | Broker-generated — belongs to Execution Result |
| Fill price / fill quantity | Broker-response data — belongs to Execution Result |
| Position ID / portfolio state | Portfolio layer — belongs downstream |
| Broker credentials / auth tokens | Security boundary — must never leave broker connection |
| Paper-trade state / simulation state | Sibling path — must never converge |
| Trading signal / recommendation | Not an execution artifact |
| Probability / confidence / score | Not an execution artifact |
| Target 2 | Not supported by architecture |
| Recalculation of geometry | Authorization/planning is authoritative |

### Prohibited Transformations

| Transformation | Why Prohibited |
|----------------|---------------|
| Price clamping | Material change — requires re-authorization |
| Quantity increase | Increases risk — requires re-authorization |
| Stop/target modification | Material change — requires re-authorization |
| Direction change | Material change — requires re-authorization |
| Instrument substitution | Changes identity — requires re-authorization |
| Silent fallback to different provider/mode | Safety violation |

## 27. Paper Trading Isolation (Detailed)

### Why Paper Trading Must Not Feed Execution

The `PaperTrade` layer (Product Phase 5) is an **observational validation**
layer. It records what existing opportunities would have done if followed.
It is NOT a source of execution commands.

Reasons:
1. **Different purpose**: Paper trading validates historical outcomes.
   Execution commands place orders.
2. **Different lifecycle**: Paper trades track completed candles for
   entry/exit observation. Execution commands submit to brokers.
3. **Different authority**: Paper trades reuse `TradePlan` geometry verbatim.
   Execution commands require `ExecutionAuthorization`.
4. **No convergence**: A paper-trade result (WIN/LOSS) must never trigger
   an execution command. The existing decision remains authoritative.

### Verification

The command constructor must verify:
- The source `OperationalTradeIntent` is NOT derived from a `PaperTrade`.
- The source `ExecutionAuthorization` does NOT reference a `PaperTrade`.
- No `PaperTradeStatus` or `PaperExitReason` fields appear on the command.

## 28. Dashboard Isolation (Detailed)

### Current State

The dashboard has **zero execution reachability**:
- No endpoint constructs `ExecutionCommand`.
- No endpoint contacts brokers.
- No endpoint places orders.
- The `POST /api/operational-trade-intent` endpoint creates intents only
  (requires explicit `created_at`, explicit instrument, explicit account).
- The `POST /api/paper-trades` endpoint creates paper trades only.

### Future Integration Contract

When execution commands are eventually displayed in the dashboard:
- They must be **read-only** — displayed, not constructed.
- The dashboard must never bypass the `ExecutionAuthorization` layer.
- The dashboard must never allow command mutation after creation.
- The dashboard must clearly distinguish `PAPER` mode from `LIVE` mode.

## 29. Point-in-Time Safety (Detailed)

### Authorization Time Boundary

The `ExecutionCommand` inherits its validity window from the authorization:

```
command.valid_from >= authorization.valid_from
command.valid_until <= authorization.valid_until
command.valid_until > command.valid_from
```

### Command Creation Time

The `created_at` timestamp on the command is:
- Caller-supplied (not generated by the constructor).
- Must be within the authorization's validity window.
- Must be timezone-aware.

### No Future Data

The command constructor must NOT:
- Accept candles, market data, or price updates.
- Accept forward-looking parameters.
- Reference any data beyond the authorization's `valid_until`.

## 30. Determinism and Reproducibility

### Deterministic Command Identity

Given the same authorized intent and the same normalization parameters, the
command constructor must always produce the same `command_id`.

### Deterministic Normalization

Any normalization applied during command construction must be:
- Deterministic (no randomness, no wall-clock dependency).
- Reproducible (same inputs → same outputs).
- Auditable (recorded in `normalization_applied`).

### Serialization Round-Trip

The `ExecutionCommand` must be serializable to deterministic JSON and
deserializable without loss. The round-trip must preserve:
- `command_id`
- `authorization_id`
- `intent_id`
- `content_fingerprint`
- All economic fields
- `normalization_applied`

## 31. Error Handling and Reporting

### Typed Errors

The command constructor must raise typed errors for different failure modes:

| Failure | Error Type | Behavior |
|---------|-----------|----------|
| Authorization not found | `AuthorizationNotFoundError` | NO COMMAND |
| Authorization not AUTHORIZED | `AuthorizationStateError` | NO COMMAND |
| Authorization expired | `AuthorizationStateError` | NO COMMAND |
| Intent binding mismatch | `AuthorizationBindingError` | NO COMMAND |
| Content fingerprint mismatch | `AuthorizationBindingError` | NO COMMAND |
| Invalid instrument | `ValueError` | NO COMMAND |
| Invalid direction | `ValueError` | NO COMMAND |
| Missing geometry | `ValueError` | NO COMMAND |
| Price normalization exceeds tolerance | `NormalizationError` | NO COMMAND |
| Quantity rounding increases risk | `NormalizationError` | NO COMMAND |

### No Silent Failures

No error must be silently swallowed. Every failure must produce an
explicit reason that can be logged, reported, or returned to the caller.

## 32. Future Test Contract

### Required Test Areas

When `ExecutionCommand` is eventually implemented, the following test areas
must be covered:

1. **Model tests** — construction, immutability, slots, field validation,
   forbidden field exclusion, deterministic identity.
2. **Authorization binding tests** — valid AUTHORIZED → command created;
   ELIGIBLE → NO COMMAND; EXPIRED → NO COMMAND; REVOKED → NO COMMAND;
   SUPERSEDED → NO COMMAND; missing → NO COMMAND.
3. **Content fingerprint tests** — matching fingerprint → command created;
   mismatched fingerprint → NO COMMAND.
4. **Normalization tests** — price within half-tick → permitted; price
   beyond half-tick → NO COMMAND; quantity floor-rounding → permitted;
   quantity increase → NO COMMAND.
5. **Re-authorization tests** — material field change → requires new
   authorization; non-material normalization → no re-authorization needed.
6. **Execution mode tests** — PAPER mode → command created for paper;
   LIVE mode → command created for live; mode change without
   re-authorization → NO COMMAND.
7. **Isolation tests** — no broker fields on command; no paper-trade fields
   on command; no execution-result fields on command; no position/portfolio
   fields on command.
8. **Point-in-time tests** — command valid within validity window; command
   invalid outside validity window; no future data accepted.
9. **Determinism tests** — same inputs → same command_id; repeated
   construction → same identity; serialization round-trip preserves all
   fields.
10. **Dashboard isolation tests** — no dashboard endpoint constructs
    commands; dashboard displays commands as read-only.
11. **Paper trading isolation tests** — no paper-trade path creates commands;
    paper-trade result does not modify authorization or intent.
12. **Regression tests** — full suite baseline unchanged (5339 passed, 2
    pre-existing yfinance failures, 3 skipped).

### Test Baseline

Current test baseline (post-Checkpoint 15.6): **5339 passed, 2
pre-existing yfinance failures, 3 skipped, 1 warning.**

No implementation changes are made in this checkpoint. The baseline must
remain unchanged.

## 33. Recommended Implementation Sequence

When the `ExecutionCommand` layer is eventually implemented, the recommended
sequence is:

1. **Model** (`src/engine/models/execution_command.py`) — frozen+slots
   dataclass, deterministic `command_id`, immutable, broker-neutral.
2. **Engine/Constructor** (`src/engine/intelligence/execution_command.py`) —
   stateless, validates authorization, applies safe normalizations, fails
   closed.
3. **Serialization** (`src/engine/persistence/execution_command_serialization.py`) —
   deterministic JSON, schema versioning, lossless round-trip.
4. **Store** (`src/engine/persistence/execution_command_store.py`) — atomic
   filesystem persistence, following the Checkpoint 15.5 pattern.
5. **Tests** — comprehensive test suite covering all 12 test areas above.
6. **Dashboard integration** — read-only display of command status.

## 34. Final Verdict

**PASS**

The boundary between `ExecutionAuthorization` (Checkpoint 15, frozen) and
the future `ExecutionCommand` layer is **well-defined, safe, and consistent**
with the existing frozen architecture.

Key conclusions:
- No `ExecutionCommand` implementation exists — clean baseline.
- The authorization contract (Checkpoint 15.6) provides all necessary inputs
  for command construction: `authorization_id`, `intent_id`,
  `content_fingerprint`, `execution_mode`, timestamps.
- The intent contract (Checkpoint 14.6) provides all necessary economic
  fields: `instrument`, `direction`, `quantity`, `entry`, `stop`, `target`,
  `planned_risk`, `maximum_risk`.
- The command must remain broker-neutral — all broker-specific concepts
  belong to the Broker Adapter (Checkpoint 13.5, frozen).
- The command must fail closed on any authorization mismatch or material
  field change.
- Price/quantity normalization must be safe, bounded, and auditable.
- Paper trading is a sibling path — must never converge with execution.
- The dashboard is presentation-only — must never construct commands.
- Point-in-time safety is structurally enforced by the authorization
  validity window.
- Determinism is preserved via the established `sha256[:16]` identity pattern.

### Production files modified in this audit

**None.** This is an audit-only checkpoint.

### Regressions

**None.** No implementation changes were made. Full suite baseline unchanged:
5339 passed, 2 pre-existing yfinance failures, 3 skipped, 1 warning.

### Recommended Next Checkpoint

Checkpoint 16.2 should implement the `ExecutionCommand` model and engine
per the contract defined in this audit, followed by Checkpoint 16.3 for
persistence, and Checkpoint 16.4 for integration and freeze.
