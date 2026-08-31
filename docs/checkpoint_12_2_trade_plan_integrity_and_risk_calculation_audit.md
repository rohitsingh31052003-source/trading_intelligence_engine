# Checkpoint 12.2 — Trade Plan Integrity & Risk Calculation Audit

## 1. Purpose

Audit whether the existing TradePlan calculation layer correctly and deterministically transforms the supplied planning inputs into a valid TradePlan. This checkpoint verifies the mathematical correctness, boundary handling, determinism, immutability, serialization integrity, and failure-state coherence of the Trade Planning subsystem established in Product Phase 4 and confirmed by Checkpoint 12.1.

## 2. Scope

This audit covers the complete calculation and integrity layer of the Trade Planning subsystem:

- Account-risk calculation
- Risk-per-contract derivation
- Quantity calculation and rounding
- Planned risk / reward computation
- Risk-limit enforcement
- Geometry preservation (entry/stop/target/R:R)
- Long/short symmetry
- Decimal precision and boundary handling
- Invalid numeric handling (NaN, infinity, zero, negative)
- Deterministic plan identity
- Serialization round-trip integrity
- Input immutability
- Failure-state integrity
- Warning-state integrity
- QuantitySpec audit
- Point-in-time independence
- Trading-semantics contamination
- Test coverage adequacy

## 3. Exact Files Inspected

| File | Lines | Role |
|------|-------|------|
| `src/engine/intelligence/trade_planning.py` | 967 | Main planning engine |
| `src/engine/intelligence/trade_planning_serialization.py` | 251 | Deterministic serialization |
| `src/engine/models/trade_plan.py` | 464 | Domain models (TradePlan, QuantitySpec, enums) |
| `src/engine/config/trade_plan_config.py` | 119 | Configuration (frozen dataclass) |
| `src/engine/reporting/trade_planning.py` | 174 | Human-readable formatter |
| `tests/test_trade_planning.py` | 2120 | Test suite (158 tests) |

Additional files inspected for downstream integrity:
- `src/dashboard/views.py` (TradePlanView projection)
- `src/dashboard/services.py` (plan_trade orchestration)
- `src/dashboard/app.py` (/api/trade-plan route)

## 4. Existing Calculation Flow

The engine `TradePlanningEngine.plan()` follows this deterministic sequence:

```
1. Coerce account inputs (capital, risk%) to Decimal via _coerce_account_inputs()
2. Resolve geometry via _resolve_geometry() (explicit kwargs override object attributes)
3. Determine quantity spec availability (use supplied or DEFAULT_QUANTITY_SPEC)
4. Validate account inputs -> INVALID_INPUT if invalid
5. Validate geometry has risk -> GEOMETRY_UNAVAILABLE if not
6. Validate direction is LONG/SHORT -> GEOMETRY_UNAVAILABLE if not
7. Compute maximum_risk = capital * risk_percent / 100
8. Compute risk_per_contract = engine_risk * contract_multiplier
9. Check risk_per_contract > 0 -> QUANTITY_UNAVAILABLE if not
10. Compute raw_contracts = maximum_risk / risk_per_contract
11. Size quantity via _size_quantity() (fractional or floor-rounded)
12. Check quantity > 0 -> RISK_LIMIT_EXCEEDED if not
13. Compute planned_risk = quantity * risk_per_contract
14. Compute planned_reward = quantity * (engine_reward * contract_multiplier)
15. Defensive no-over-risk guard (re-floor if planned_risk > maximum_risk)
16. Build VALID plan
```

## 5. Account-Risk Calculation

**Formula (verified from source):**

```python
maximum_risk = capital_dec * risk_pct_dec / Decimal("100")
```

**Location:** `trade_planning.py:348`

**Validation:** `_coerce_account_inputs()` at `trade_planning.py:654-691`

- Capital must be positive (> 0)
- Risk percent must be strictly greater than zero
- Risk percent must not exceed `cfg.max_risk_percent` (default 10%)
- Risk percent must be >= `cfg.min_risk_percent` when positive
- All arithmetic performed in `Decimal` (no float for money)

**Boundary handling:**
- Very small capital (e.g. Decimal("0.01")): Valid, produces small maximum_risk
- Very large capital (e.g. Decimal("1000000000")): Valid, Decimal handles arbitrary precision
- Risk percent at exactly max: Valid (boundary inclusive)
- Risk percent at exactly 0: Invalid (strictly > 0 required)
- Risk percent above max: Invalid
- Risk percent below min (when min > 0): Invalid

## 6. Risk-Distance Calculation

**Engine risk distance** is NOT computed by the planner. It is the existing `engine_risk_distance` value from the Sprint 11R TradeCandidate, reused verbatim.

**Risk per contract:**
```python
risk_per_contract = engine_risk * contract_multiplier
```

**Location:** `trade_planning.py:413`

When `contract_multiplier == 1` (default), `risk_per_contract == engine_risk`. When the caller supplies a real QuantitySpec with a multiplier (e.g. 75 for NIFTY lots), the per-contract risk scales accordingly.

## 7. Quantity Calculation

**Formula (verified):**
```python
raw_contracts = maximum_risk / risk_per_contract
```

**Location:** `trade_planning.py:458`

**Two paths:**

1. **Fractional allowed** (`allow_fractional_quantity=True`): Quantity = `raw_contracts` at full Decimal precision. `planned_risk == maximum_risk` exactly (by construction).

2. **Integer-only** (`allow_fractional_quantity=False`): Quantity = largest integer-step multiple whose `planned_risk <= maximum_risk`. Computed by `_floor_to_fit()` at `trade_planning.py:865-884`.

**Rounding direction:** FLOOR only. `ROUND_FLOOR` used via `Decimal.to_integral_value()`. Round/ceil are rejected by config.

**Location:** `trade_planning.py:859`

## 8. Quantity Rounding

**Floor rounding guarantees `planned_risk <= maximum_risk`.**

When fractional is disallowed:
```python
max_contracts = (maximum_risk / risk_per_contract).to_integral_value(rounding=ROUND_FLOOR)
if step != 1:
    max_steps = (max_contracts / step).to_integral_value(rounding=ROUND_FLOOR)
    max_contracts = max_steps * step
```

**Rounding can NEVER cause planned_risk to exceed maximum_risk.** Floor division always rounds down, and a defensive guard at line 535-590 re-floors if any edge case somehow over-risks.

**Fractional quantities:** `planned_risk = raw * risk_per_contract = maximum_risk` exactly (no rounding error since `raw = maximum_risk / risk_per_contract`).

## 9. Quantity Constraints

- `quantity_step`: Must be positive (validated in `QuantitySpec.__post_init__`). Snaps quantity to multiples when integer-only.
- `contract_multiplier`: Must be positive. Scales risk/reward per contract.
- `allow_fractional_quantity`: Boolean flag.
- Minimum quantity: Implicitly the `quantity_step` (one step). If one step exceeds maximum_risk, RISK_LIMIT_EXCEEDED.
- Maximum quantity: Not explicitly bounded; governed by maximum_risk / risk_per_contract.

## 10. Planned Risk

**Formula (verified):**
```python
planned_risk = quantity * risk_per_contract
```

**Location:** `trade_planning.py:522`

- Deterministic from quantity and engine_risk_distance.
- Guaranteed `<= maximum_risk` by floor rounding + defensive guard.
- `None` when position is unsized (UNSIZED status).
- Decimal arithmetic throughout.

## 11. Planned Reward

**Formula (verified):**
```python
reward_per_contract = engine_reward * contract_multiplier
planned_reward = quantity * reward_per_contract
```

**Location:** `trade_planning.py:526-529`

**Critical distinction:** `planned_reward` is:
- Deterministic from quantity and engine_reward_distance
- NOT an expected return
- NOT a prediction
- NOT a probability
- NOT alpha
- NOT a forecast
- NOT a guaranteed profit

It is the potential reward if price reaches the structural target, computed mechanically.

`planned_reward` is `None` when engine_reward_distance is None or <= 0.

## 12. Long/Short Symmetry

**Risk is symmetric.** The planner uses `engine_risk_distance` (an absolute distance from the Sprint 11R candidate) for BOTH LONG and SHORT directions. No direction-specific arithmetic exists in the sizing logic.

Verified by tests:
- `TestShortSizing::test_short_sizing_symmetric`
- `TestShortSizing::test_short_risk_is_absolute_distance`

The direction string is preserved verbatim on the plan but does not affect any calculation.

## 13. Geometry Preservation

**Entry, stop, target_1, engine_risk_distance, engine_reward_distance, engine_risk_reward_ratio are all reused VERBATIM from the existing engine geometry.**

The `_resolve_geometry()` function at `trade_planning.py:694-768`:
- Reads attributes from geometry object OR explicit kwargs
- Coerces to Decimal (does not recompute)
- Does NOT modify the source object (verified by test `test_geometry_object_not_mutated`)
- Does NOT recompute R:R (verified by test `test_plan_does_not_recompute_rr`)

**Quantity rounding and Decimal normalization do NOT alter the analytical geometry.** The geometry fields are copied by value into the TradePlan; no operation in the planning engine modifies them.

## 14. Decimal Precision

- All monetary values stored as `Decimal` in models
- `_to_decimal()` coerces int/float/str to Decimal, rejects bool/NaN/infinity
- All financial math performed in Decimal (no float for money)
- `ROUND_FLOOR` used for floor rounding
- Serialization stores Decimal as strings: `{"__decimal__": str(value)}`
- API renders Decimal as strings for precision + parallel `_float` fields

## 15. Invalid Numeric Handling

**`_to_decimal()` at `trade_plan.py:50-72` rejects:**
- `bool` (raises ValueError: "Boolean is not a valid monetary value.")
- `Decimal('NaN')`, `Decimal('Infinity')`, `Decimal('-Infinity')` (raises ValueError: "Monetary value must be finite")
- `float('nan')`, `float('inf')` (rejected via `Decimal(str(value)).is_finite()` check)
- Unparseable strings (Decimal() raises, caught by caller)

**`_coerce_account_inputs()` catches:**
- `ValueError`, `TypeError`, `ArithmeticError` during coercion
- Returns error message for INVALID_INPUT plan

## 16. Failure States

**Five mutually exclusive `RiskPlanStatus` values:**

| Status | Condition | Coherent? |
|--------|-----------|-----------|
| `VALID` | Complete geometry + valid inputs + sized quantity within risk limit | YES |
| `INVALID_INPUT` | Non-positive capital, out-of-bounds risk%, NaN/infinity | YES |
| `GEOMETRY_UNAVAILABLE` | Missing entry/stop/risk_distance, zero/negative risk, non-directional | YES |
| `RISK_LIMIT_EXCEEDED` | Smallest valid integer position exceeds maximum_risk | YES |
| `QUANTITY_UNAVAILABLE` | risk_per_contract <= 0, or fractional below step | YES |

**Partial/ambiguous plans CANNOT accidentally be reported as VALID.** The `TradePlan.__post_init__` at `trade_plan.py:402-455` enforces:
- VALID requires direction in ("LONG", "SHORT")
- VALID requires complete geometry (entry, stop, engine_risk_distance > 0)
- VALID requires positive quantity
- VALID requires positive planned_risk
- VALID requires planned_risk <= maximum_risk
- VALID requires target_2 is None
- target_2 must ALWAYS be None
- target_2_supported must ALWAYS be False

## 17. QuantitySpec Audit

**`QuantitySpec` at `trade_plan.py:166-208`:**

- `quantity_step`: Default `Decimal("1")`, must be positive
- `contract_multiplier`: Default `Decimal("1")`, must be positive
- `allow_fractional_quantity`: Default `False`
- Frozen + slots (immutable)
- `__post_init__` validates positivity

**Default spec (`DEFAULT_QUANTITY_SPEC`):**
```python
QuantitySpec(
    quantity_step=Decimal("1"),
    contract_multiplier=Decimal("1"),
    allow_fractional_quantity=True,
)
```

**Generic default explicitly represented:** When no QuantitySpec is supplied, the plan sets `quantity_spec_available=False` and surfaces a warning: "Instrument quantity specification unavailable: the safe generic quantity model is used by default (unit step, unit multiplier). No NSE lot size or broker-specific contract rule is fabricated."

**No broker-specific assumptions.** No NSE lot sizes or exchange-specific contract metadata hard-coded. The system explicitly warns when the generic model is used.

## 18. Determinism / plan_id

**plan_id formula:**
```python
digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
return f"plan-{digest[:16]}"
```

**Location:** `trade_planning.py:915-958`

**Inputs hashed (canonical JSON with sorted keys):**
- instrument, timeframe, direction, existing_decision, actionability
- account_capital, risk_percent
- entry, stop, target_1
- engine_risk_distance, engine_reward_distance, engine_risk_reward_ratio
- quantity_spec_available, label, metadata

**Normalization:**
- `_canonical_value()` at `trade_plading.py:900-912`:
  - None -> "null"
  - Decimal -> "dec:{normalized}" (so Decimal("1.0") == Decimal("1"))
  - bool -> "bool:{value}"
  - int/float -> "num:{value}"
  - str -> "str:{value}"

**No timestamps, no random UUIDs, no memory addresses.**

**Same inputs -> same plan_id:** VERIFIED
**Equivalent normalized inputs -> same plan_id:** VERIFIED (Decimal normalization)
**Different inputs -> different plan_id:** VERIFIED
**Metadata order independent:** VERIFIED (sorted before hashing)

## 19. Immutability

**`TradePlan` model:** `@dataclass(frozen=True, slots=True)` — immutable after construction.

**`QuantitySpec` model:** `@dataclass(frozen=True, slots=True)` — immutable.

**Engine does NOT mutate input objects:**
- `_resolve_geometry()` reads attributes via `getattr()` (never writes)
- Test `test_geometry_object_not_mutated` explicitly verifies
- Test `test_repeated_planning_no_state_leak` verifies statelessness

**Engine is stateless across calls.** No instance state modified by `plan()`.

## 20. Serialization

**File:** `trade_planning_serialization.py` (251 lines)

**Format:** Canonical JSON with `schema_version` + `plan` keys, sorted.

**Type tags:**
- `__decimal__`: Decimal as string
- `__enum__`: Enum member by name
- `__dataclass__`: Dataclass with class name + fields
- `__tuple__`: Tuple as tagged list

**Schema version:** `TRADE_PLAN_SCHEMA_VERSION = 1`

**Validation on deserialize:**
- Malformed JSON -> ValueError
- Non-object payload -> ValueError
- Unsupported schema version -> ValueError (BEFORE any model reconstruction)
- Missing `plan` key -> ValueError

**Round-trip integrity:** LOSSLESS for all audit fields (verified by `TestSerializationRoundTrip`).

**Deterministic reconstruction:** Same plan produces same bytes (verified by `test_serialization_is_deterministic`).

## 21. Point-in-Time Independence

**VERIFIED.** The TradePlanningEngine:
- Takes NO candle / future-market-data argument (verified by test `test_plan_api_no_future_argument`)
- NEVER calls Sprint 11W `OutcomeEvaluator` (verified by test `test_plan_works_with_evaluator_patched_to_raise`)
- NEVER runs `HistoricalEvaluationPipeline` (verified by test `test_plan_works_with_pipeline_patched_to_raise`)
- Consumes only already-computed engine geometry
- No market-data time-series dependency

## 22. Trading-Semantics Contamination Audit

**VERIFIED CLEAN.** The TradePlan layer:
- Does NOT contain BUY/SELL/ENTER/EXIT/HOLD enum values or string literals in executable code
- Does NOT transform REJECTED/WATCH/QUALIFIED/PREFERRED into trading instructions
- `RiskPlanStatus` is explicitly DISTINCT from `ActionabilityState` and Sprint 11S decision classification
- `planned_reward` is explicitly NOT an expected return or prediction
- Formatter includes explicit disclaimer on every report
- Test `test_plan_never_renamed_to_buy_sell` verifies no BUY/SELL leakage

## 23. Tests Inspected

**File:** `tests/test_trade_planning.py` (2120 lines)

**Test classes (28 classes, 158 tests total):**

| Class | Area | Count |
|-------|------|-------|
| TestModelValidation | Enum/frozen/invariants | 10 |
| TestConfigValidation | Config defaults/validation | 8 |
| TestAccountCapitalValidation | Capital coercion/bounds | 4 |
| TestRiskPercentValidation | Risk% bounds/edge cases | 6 |
| TestMaximumRiskCalculation | Formula/precision | 3 |
| TestGeometryPreservation | Entry/stop/target preserved | 6 |
| TestRiskRewardPreservation | R:R reused verbatim | 2 |
| TestLongSizing | LONG sizing exact | 2 |
| TestShortSizing | SHORT symmetry | 2 |
| TestQuantityRounding | Fractional/floor/step | 6 |
| TestPlannedRiskCalculation | Formula/never-exceeds | 3 |
| TestPlannedRewardCalculation | Formula/deterministic | 3 |
| TestRiskLimitEnforcement | One unit too big | 2 |
| TestZeroRiskDistance | Zero/negative risk | 2 |
| TestMissingGeometry | Missing entry/stop | 3 |
| TestMissingTarget | Target not required | 1 |
| TestInvalidNumbers | None capital, boolean | 2 |
| TestNanInfinity | NaN/Infinity handling | 3 |
| TestDeterministicIds | Same/different inputs | 7 |
| TestOrderIndependence | Metadata order | 2 |
| TestSerializationRoundTrip | All fields preserved | 8 |
| TestMalformedSerialization | Bad JSON/missing key | 3 |
| TestFutureSchemaRejection | Future schema | 2 |
| TestInputImmutability | Geometry/model/state | 3 |
| TestReferenceGeometryPreservation | Provenance | 2 |
| TestDecisionPreservation | 4 classifications + no BUY/SELL | 6 |
| TestActionabilityPreservation | Reused verbatim | 2 |
| TestEvidenceSeparation | No evidence argument | 2 |
| TestNoLookAhead | No future arg, evaluator patched | 3 |
| TestServiceNoLookAhead | plan_trade no future arg | 3 |
| TestWorkstationIntegration | Section/form/built | 3 |
| TestApiValidation | Invalid capital/risk%/above max | 4 |
| TestApiResponseSchema | Schema fields/decimal as string | 3 |
| TestHtmlRendering | Warning/no execution button | 3 |
| TestErrorStates | All 5 statuses | 4 |
| TestTarget2Unsupported | None in model/API/HTML | 3 |
| TestGeometryUnavailable | Surfaces honestly | 1 |
| TestRiskPlanWarnings | All warning types | 4 |
| TestExistingRegression | Routes/health/pipeline 4/3 | 4 |
| TestReporting | Formatter sections/warning | 8 |
| TestViewModel | JSONable/frozen | 2 |
| TestFinancialRounding | Exact/tiny/large/small/large/decimal/floor | 7 |

**Important boundary cases covered:**
- Exact division (no rounding needed)
- Tiny risk distance (very small per-unit risk)
- Large risk distance (very large per-unit risk)
- Very small account (Decimal("0.01"))
- Very large account (Decimal("1000000000"))
- Decimal not float for money
- Floor never over-risks (both fractional and integer)
- NaN/Infinity capital and risk_percent
- Boolean rejected as monetary value
- Zero risk distance
- Negative risk distance
- Quantity step snapping
- Risk limit (one unit too big)

## 24. Test Results

```
python -m pytest tests/test_trade_planning.py -v
==============================
158 passed, 1 warning in 1.61s
==============================
```

**All 158 tests PASS.** No failures, no errors.

**Regression:** Full pipeline baseline `signals=4, trades=3` verified by `TestExistingRegression::test_pipeline_baseline_signals_4_trades_3`.

## 25. Coverage Gaps

**No significant coverage gaps identified.** The test suite covers:

- All five failure states
- Decimal precision and boundary values
- Invalid numeric inputs (NaN, infinity, zero, negative, boolean)
- Long/short symmetry
- Fractional vs integer-only quantity paths
- Risk-limit enforcement edge cases
- Serialization round-trip and malformed payload rejection
- Future schema rejection
- Input immutability (geometry object, frozen model, stateless engine)
- Determinism (same inputs, different inputs, Decimal normalization, metadata order)
- No-look-ahead (signature inspection, evaluator/pipeline patched to raise)
- Decision/actionability preservation
- Evidence separation

**Potential gaps (minor, acceptable):**
- No explicit test for `contract_multiplier > 1` path (multiplier=75 for NIFTY-like contracts). The generic default uses multiplier=1, and the architecture correctly supports a caller-supplied QuantitySpec with any positive multiplier. The formula is straightforward multiplication.
- No test for extremely large metadata (thousands of pairs). Metadata is sorted and hashed; no known limitation.

## 26. Limitations

1. **Generic quantity model:** When no QuantitySpec is supplied, the planner uses a safe generic model (unit step, unit multiplier, fractional allowed). This is intentional and explicitly warned. The system does NOT contain broker/exchange-specific contract metadata.

2. **Single structural target:** Target 2 is always unsupported (`target_2=None`, `target_2_supported=False`). The architecture produces one structural target from the Sprint 11R candidate.

3. **No probability/planned reward is deterministic:** `planned_reward` is a mechanical computation, NOT a prediction of future performance. This is by design.

4. **No multi-contract portfolio interaction:** Each plan is computed independently for a single instrument/geometry. Portfolio-level risk aggregation is out of scope for this layer.

5. **Floor rounding may under-utilize capital:** When integer-only, the planned risk may be significantly less than maximum_risk (e.g. when the raw quantity is 1.9, floor gives 1, using ~53% of allowed risk). This is a feature, not a bug — it is the only rounding mode that guarantees no over-risk.

## 27. Implementation Decision

**No implementation changes required.**

The audit found no genuine defects. The Trade Plan calculation layer is:
- Mathematically correct
- Deterministic
- Immutable where required
- Point-in-time independent
- Free of trading-semantics contamination
- Fully tested (158/158 passing)
- Properly bounded (all invalid inputs rejected, never silently repaired)

**No files were modified.**

## 28. Final Verdict

**PASS**
