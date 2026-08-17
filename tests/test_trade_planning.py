"""
Tests for Product Phase 4 — RISK & TRADE PLANNING.

These tests verify the risk / trade planning layer is a THIN, honest,
deterministic calculation around the EXISTING trade candidate / trade
geometry. The planner implements NO market analysis, NO decision logic,
NO prediction, NO probability, NO BUY/SELL/ENTER/EXIT/HOLD
recommendation. The existing Sprint 11S decision classification
(REJECTED / WATCH / QUALIFIED / PREFERRED) is AUTHORITATIVE and never
renamed / upgraded / downgraded. The existing Sprint 11R ``TradeCandidate``
geometry (entry / stop / target / risk_distance / reward_distance /
risk_reward_ratio) is AUTHORITATIVE and never recomputed; Phase 4 only
performs deterministic calculations AROUND those existing values.

Coverage areas (A-AT):

A.  Model validation
B.  Config validation
C.  Account capital validation
D.  Risk percentage validation
E.  Maximum-risk calculation
F.  Entry preservation
G.  Stop preservation
H.  Target preservation
I.  Existing R:R preservation
J.  Long sizing
K.  Short sizing
L.  Quantity rounding
M.  Planned-risk calculation
N.  Planned-reward calculation
O.  Risk-limit enforcement
P.  Zero-risk-distance handling
Q.  Missing geometry
R.  Missing target
S.  Invalid numbers
T.  NaN/infinity
U.  Deterministic IDs
V.  Shuffle/order independence
W.  Serialization round trip
X.  Malformed serialization
Y.  Future schema rejection
Z.  Input immutability
AA. Reference/geometry preservation
AB. Decision preservation
AC. Actionability preservation
AD. Evidence preservation
AE. No-look-ahead
AF. OutcomeEvaluator not called
AG. HistoricalPipeline not called
AH. Workstation integration
AI. API validation
AJ. API response schema
AK. HTML rendering
AL. Error states
AM. Target 2 remains unsupported
AN. Geometry unavailable
AO. Risk-plan warnings
AP. Existing dashboard regression
AQ. Existing scanner regression
AR. Product Phase 1 regression
AS. Product Phase 2 regression
AT. Product Phase 3 regression

Also tests financial rounding carefully (Decimal, exact division,
fractional quantity, quantity floor, tiny / large risk distance, very
small / large account, very small / maximum risk %, invalid risk %,
zero values, Decimal behavior).
"""

from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import FixtureDataProvider
from dashboard.services import (
    DashboardAnalysisService,
    TradePlanRequest,
)
from dashboard.views import (
    TradePlanView,
    trade_plan_view_to_jsonable,
)
from engine.config.trade_plan_config import TradePlanConfig
from engine.intelligence.trade_planning import TradePlanningEngine
from engine.intelligence.trade_planning_serialization import (
    TRADE_PLAN_SCHEMA_VERSION,
    canonical_trade_plan_json,
    deserialize_trade_plan,
    parse_trade_plan_header,
    serialize_trade_plan,
    serialize_trade_plan_bytes,
)
from engine.models.trade_plan import (
    DEFAULT_QUANTITY_SPEC,
    QuantitySpec,
    QuantityStatus,
    RiskPlanStatus,
    TradePlan,
)
from engine.reporting.trade_planning import TradePlanFormatter


# ============================================================
# FIXTURES / HELPERS
# ============================================================


def _geom(
    direction="LONG",
    entry=Decimal("100"),
    stop=Decimal("90"),
    target_1=Decimal("120"),
    risk_distance=Decimal("10"),
    reward_distance=Decimal("20"),
    risk_reward_ratio=Decimal("2"),
):
    """A tiny geometry stand-in exposing the GeometryView attributes."""

    class _G:
        pass

    g = _G()
    g.direction = direction
    g.entry = entry
    g.stop = stop
    g.target_1 = target_1
    g.risk_distance = risk_distance
    g.reward_distance = reward_distance
    g.risk_reward_ratio = risk_reward_ratio
    return g


def _sig(func) -> set[str]:
    return set(inspect.signature(func).parameters)


def _valid_plan(**overrides) -> TradePlan:
    """Build a VALID LONG plan quickly for tests."""

    eng = TradePlanningEngine()
    kwargs = dict(
        instrument="NIFTY",
        timeframe="15m",
        account_capital=Decimal("100000"),
        risk_percent=Decimal("1"),
        direction="LONG",
        entry=Decimal("25000"),
        stop=Decimal("24800"),
        target_1=Decimal("25600"),
        risk_distance=Decimal("200"),
        reward_distance=Decimal("600"),
        risk_reward_ratio=Decimal("3"),
    )
    kwargs.update(overrides)
    return eng.plan(**kwargs)


@pytest.fixture
def service() -> DashboardAnalysisService:
    return DashboardAnalysisService(provider=FixtureDataProvider())


@pytest.fixture
def client(service: DashboardAnalysisService) -> TestClient:
    return TestClient(create_app(service=service))


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_enums_members(self):
        assert RiskPlanStatus.VALID.value == "VALID"
        assert RiskPlanStatus.INVALID_INPUT.value == "INVALID_INPUT"
        assert RiskPlanStatus.GEOMETRY_UNAVAILABLE.value == "GEOMETRY_UNAVAILABLE"
        assert RiskPlanStatus.RISK_LIMIT_EXCEEDED.value == "RISK_LIMIT_EXCEEDED"
        assert RiskPlanStatus.QUANTITY_UNAVAILABLE.value == "QUANTITY_UNAVAILABLE"
        assert QuantityStatus.DETERMINED.value == "DETERMINED"
        assert QuantityStatus.FRACTIONAL_ALLOWED.value == "FRACTIONAL_ALLOWED"
        assert QuantityStatus.FLOOR_ROUNDED.value == "FLOOR_ROUNDED"
        assert QuantityStatus.UNSIZED.value == "UNSIZED"

    def test_risk_plan_status_is_valid_property(self):
        assert RiskPlanStatus.VALID.is_valid is True
        assert RiskPlanStatus.INVALID_INPUT.is_valid is False
        assert RiskPlanStatus.GEOMETRY_UNAVAILABLE.is_valid is False

    def test_model_is_frozen_and_slots(self):
        plan = _valid_plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.instrument = "X"  # type: ignore[misc]
        assert TradePlan.__slots__ is not None

    def test_valid_plan_post_init_invariants(self):
        plan = _valid_plan()
        assert plan.is_valid
        assert plan.has_geometry

    def test_valid_plan_requires_direction(self):
        with pytest.raises(ValueError):
            TradePlan(
                plan_id="plan-x", instrument="N", timeframe="15m",
                direction="NONE", existing_decision="QUALIFIED",
                actionability="READY_FOR_REVIEW",
                account_capital=Decimal("100000"), risk_percent=Decimal("1"),
                maximum_risk=Decimal("1000"),
                entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
                engine_risk_distance=Decimal("10"), engine_reward_distance=Decimal("20"),
                engine_risk_reward_ratio=Decimal("2"),
                quantity=Decimal("100"), planned_risk=Decimal("1000"),
                planned_reward=Decimal("2000"),
                quantity_status=QuantityStatus.DETERMINED,
                risk_plan_status=RiskPlanStatus.VALID,
            )

    def test_valid_plan_requires_geometry(self):
        with pytest.raises(ValueError):
            TradePlan(
                plan_id="plan-x", instrument="N", timeframe="15m",
                direction="LONG", existing_decision="QUALIFIED",
                actionability="READY_FOR_REVIEW",
                account_capital=Decimal("100000"), risk_percent=Decimal("1"),
                maximum_risk=Decimal("1000"),
                entry=None, stop=None, target_1=None,
                engine_risk_distance=None, engine_reward_distance=None,
                engine_risk_reward_ratio=None,
                quantity=Decimal("100"), planned_risk=Decimal("1000"),
                planned_reward=Decimal("2000"),
                quantity_status=QuantityStatus.DETERMINED,
                risk_plan_status=RiskPlanStatus.VALID,
            )

    def test_valid_plan_rejects_planned_risk_exceeding_maximum(self):
        with pytest.raises(ValueError):
            TradePlan(
                plan_id="plan-x", instrument="N", timeframe="15m",
                direction="LONG", existing_decision="QUALIFIED",
                actionability="READY_FOR_REVIEW",
                account_capital=Decimal("100000"), risk_percent=Decimal("1"),
                maximum_risk=Decimal("1000"),
                entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
                engine_risk_distance=Decimal("10"), engine_reward_distance=Decimal("20"),
                engine_risk_reward_ratio=Decimal("2"),
                quantity=Decimal("200"), planned_risk=Decimal("2000"),
                planned_reward=Decimal("4000"),
                quantity_status=QuantityStatus.DETERMINED,
                risk_plan_status=RiskPlanStatus.VALID,
            )

    def test_target_2_always_none(self):
        plan = _valid_plan()
        assert plan.target_2 is None
        assert plan.target_2_supported is False
        with pytest.raises(ValueError):
            TradePlan(
                plan_id="plan-x", instrument="N", timeframe="15m",
                direction="LONG", existing_decision="QUALIFIED",
                actionability="READY_FOR_REVIEW",
                account_capital=Decimal("100000"), risk_percent=Decimal("1"),
                maximum_risk=Decimal("1000"),
                entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
                engine_risk_distance=Decimal("10"), engine_reward_distance=Decimal("20"),
                engine_risk_reward_ratio=Decimal("2"),
                quantity=Decimal("100"), planned_risk=Decimal("1000"),
                planned_reward=Decimal("2000"),
                quantity_status=QuantityStatus.DETERMINED,
                risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE,
                target_2=Decimal("999"),
            )

    def test_quantity_spec_validation(self):
        with pytest.raises(ValueError):
            QuantitySpec(quantity_step=Decimal("0"))
        with pytest.raises(ValueError):
            QuantitySpec(contract_multiplier=Decimal("-1"))

    def test_default_quantity_spec(self):
        assert DEFAULT_QUANTITY_SPEC.quantity_step == Decimal("1")
        assert DEFAULT_QUANTITY_SPEC.contract_multiplier == Decimal("1")
        assert DEFAULT_QUANTITY_SPEC.allow_fractional_quantity is True


# ============================================================
# B. CONFIG VALIDATION
# ============================================================


class TestConfigValidation:
    def test_defaults(self):
        cfg = TradePlanConfig()
        assert cfg.max_risk_percent == Decimal("10")
        assert cfg.min_risk_percent == Decimal("0")
        assert cfg.allow_fractional_quantity is True
        assert cfg.quantity_rounding_mode == "floor"
        assert cfg.monetary_precision == 2

    def test_frozen_and_slots(self):
        cfg = TradePlanConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.max_risk_percent = Decimal("5")  # type: ignore[misc]
        assert TradePlanConfig.__slots__ is not None

    def test_max_risk_percent_must_be_positive(self):
        with pytest.raises(ValueError):
            TradePlanConfig(max_risk_percent=Decimal("0"))
        with pytest.raises(ValueError):
            TradePlanConfig(max_risk_percent=Decimal("-1"))

    def test_min_must_be_non_negative(self):
        with pytest.raises(ValueError):
            TradePlanConfig(min_risk_percent=Decimal("-0.5"))

    def test_min_must_not_exceed_max(self):
        with pytest.raises(ValueError):
            TradePlanConfig(min_risk_percent=Decimal("5"), max_risk_percent=Decimal("2"))

    def test_only_floor_rounding_allowed(self):
        with pytest.raises(ValueError):
            TradePlanConfig(quantity_rounding_mode="round")
        with pytest.raises(ValueError):
            TradePlanConfig(quantity_rounding_mode="ceil")

    def test_monetary_precision_non_negative(self):
        with pytest.raises(ValueError):
            TradePlanConfig(monetary_precision=-1)

    def test_metadata_must_be_tuple_of_string_pairs(self):
        with pytest.raises(ValueError):
            TradePlanConfig(metadata=("a",))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            TradePlanConfig(metadata=(("a", 1),))  # type: ignore[arg-type]
        cfg = TradePlanConfig(metadata=(("k", "v"),))
        assert cfg.metadata == (("k", "v"),)


# ============================================================
# C. ACCOUNT CAPITAL VALIDATION
# ============================================================


class TestAccountCapitalValidation:
    def test_non_positive_capital_invalid(self):
        eng = TradePlanningEngine()
        for cap in (Decimal("0"), Decimal("-100")):
            plan = eng.plan(
                instrument="X", timeframe="15m", account_capital=cap,
                risk_percent=Decimal("1"), direction="LONG",
                entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
                risk_distance=Decimal("10"), reward_distance=Decimal("20"),
                risk_reward_ratio=Decimal("2"),
            )
            assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT
            assert plan.quantity is None
            assert plan.planned_risk is None

    def test_capital_string_coerced(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital="100000",
            risk_percent="1", direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.account_capital == Decimal("100000")
        assert plan.is_valid

    def test_capital_float_coerced_to_decimal(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=100000.0,
            risk_percent=1.0, direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert isinstance(plan.account_capital, Decimal)
        assert plan.is_valid

    def test_unparseable_capital_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital="not-a-number",
            risk_percent="1", direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT
        assert plan.account_capital is None


# ============================================================
# D. RISK PERCENTAGE VALIDATION
# ============================================================


class TestRiskPercentValidation:
    def test_zero_risk_percent_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("0"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_negative_risk_percent_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("-1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_risk_percent_above_max_invalid(self):
        eng = TradePlanningEngine(TradePlanConfig(max_risk_percent=Decimal("5")))
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("6"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_risk_percent_below_min_invalid(self):
        eng = TradePlanningEngine(
            TradePlanConfig(min_risk_percent=Decimal("0.5"), max_risk_percent=Decimal("10")),
        )
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("0.25"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_max_allowed_risk_percent_valid(self):
        eng = TradePlanningEngine(TradePlanConfig(max_risk_percent=Decimal("10")))
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("10"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.is_valid
        # 10% of 100000 = 10000; risk/unit = 10 -> qty = 1000
        assert plan.quantity == Decimal("1000")
        assert plan.maximum_risk == Decimal("10000")
        assert plan.planned_risk == Decimal("10000")

    def test_very_small_risk_percent_valid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("1000000"),
            risk_percent=Decimal("0.01"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.is_valid
        # 0.01% of 1,000,000 = 100; /10 = 10
        assert plan.quantity == Decimal("10")
        assert plan.planned_risk == Decimal("100")


# ============================================================
# E. MAXIMUM-RISK CALCULATION
# ============================================================


class TestMaximumRiskCalculation:
    def test_maximum_risk_formula(self):
        plan = _valid_plan()
        assert plan.maximum_risk == Decimal("100000") * Decimal("1") / Decimal("100")
        assert plan.maximum_risk == Decimal("1000")

    def test_maximum_risk_decimal_precision(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("333333"), risk_percent=Decimal("1.5"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.maximum_risk == Decimal("333333") * Decimal("1.5") / Decimal("100")
        assert isinstance(plan.maximum_risk, Decimal)

    def test_maximum_risk_none_for_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("-1"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.maximum_risk is None


# ============================================================
# F-H. ENTRY / STOP / TARGET PRESERVATION
# ============================================================


class TestGeometryPreservation:
    def test_entry_preserved_verbatim(self):
        plan = _valid_plan(entry=Decimal("25000"))
        assert plan.entry == Decimal("25000")
        assert isinstance(plan.entry, Decimal)

    def test_stop_preserved_verbatim(self):
        plan = _valid_plan(stop=Decimal("24800"))
        assert plan.stop == Decimal("24800")

    def test_target_preserved_verbatim(self):
        plan = _valid_plan(target_1=Decimal("25600"))
        assert plan.target_1 == Decimal("25600")

    def test_geometry_from_object_reused(self):
        eng = TradePlanningEngine()
        geom = _geom()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            geometry=geom,
        )
        assert plan.entry == Decimal("100")
        assert plan.stop == Decimal("90")
        assert plan.target_1 == Decimal("120")
        assert plan.engine_risk_distance == Decimal("10")
        assert plan.engine_reward_distance == Decimal("20")
        assert plan.engine_risk_reward_ratio == Decimal("2")

    def test_explicit_kwargs_override_geometry(self):
        eng = TradePlanningEngine()
        geom = _geom(entry=Decimal("100"))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            geometry=geom, entry=Decimal("9999"), stop=Decimal("9998"),
            target_1=Decimal("9999.5"), risk_distance=Decimal("1"),
            reward_distance=Decimal("0.5"), risk_reward_ratio=Decimal("0.5"),
        )
        assert plan.entry == Decimal("9999")
        assert plan.stop == Decimal("9998")

    def test_engine_geometry_not_recomputed(self):
        # The plan must NOT recompute risk_distance from entry/stop; it
        # reuses the engine value verbatim.
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"),
            risk_distance=Decimal("777"),  # deliberately inconsistent w/ entry-stop
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        # The plan trusts the engine risk_distance verbatim.
        assert plan.engine_risk_distance == Decimal("777")


# ============================================================
# I. EXISTING R:R PRESERVATION
# ============================================================


class TestRiskRewardPreservation:
    def test_engine_rr_reused_verbatim(self):
        plan = _valid_plan(risk_reward_ratio=Decimal("3"))
        assert plan.engine_risk_reward_ratio == Decimal("3")

    def test_plan_does_not_recompute_rr(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("999"),  # deliberately off
        )
        assert plan.engine_risk_reward_ratio == Decimal("999")


# ============================================================
# J. LONG SIZING
# ============================================================


class TestLongSizing:
    def test_long_sizing_exact(self):
        plan = _valid_plan()
        # max_risk=1000, risk/unit=200 -> qty=5, planned_risk=1000, planned_reward=3000
        assert plan.direction == "LONG"
        assert plan.quantity == Decimal("5")
        assert plan.planned_risk == Decimal("1000")
        assert plan.planned_reward == Decimal("3000")

    def test_long_sizing_illustrative_example(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="NIFTY", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("25000"), stop=Decimal("24800"),
            target_1=Decimal("25600"), risk_distance=Decimal("200"),
            reward_distance=Decimal("600"), risk_reward_ratio=Decimal("3"),
        )
        assert plan.is_valid
        assert plan.account_capital == Decimal("100000")
        assert plan.risk_percent == Decimal("1")
        assert plan.maximum_risk == Decimal("1000")
        assert plan.quantity == Decimal("5")
        assert plan.planned_risk == Decimal("1000")
        assert plan.planned_reward == Decimal("3000")


# ============================================================
# K. SHORT SIZING
# ============================================================


class TestShortSizing:
    def test_short_sizing_symmetric(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="REL", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="SHORT", entry=Decimal("100"), stop=Decimal("110"),
            target_1=Decimal("80"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.direction == "SHORT"
        assert plan.is_valid
        assert plan.quantity == Decimal("100")
        assert plan.planned_risk == Decimal("1000")
        assert plan.planned_reward == Decimal("2000")

    def test_short_risk_is_absolute_distance(self):
        # Risk for SHORT is |entry - stop| = stop - entry; same as LONG.
        eng = TradePlanningEngine()
        long_p = eng.plan(
            instrument="L", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        short_p = eng.plan(
            instrument="S", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="SHORT", entry=Decimal("100"), stop=Decimal("110"),
            target_1=Decimal("80"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert long_p.quantity == short_p.quantity
        assert long_p.planned_risk == short_p.planned_risk


# ============================================================
# L. QUANTITY ROUNDING
# ============================================================


class TestQuantityRounding:
    def test_fractional_allowed_default(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("30"),
            reward_distance=Decimal("60"), risk_reward_ratio=Decimal("2"),
        )
        # 10 / 30 = 0.333...
        assert plan.is_valid
        assert plan.quantity_status is QuantityStatus.FRACTIONAL_ALLOWED
        # planned_risk = qty * 30 <= 10 (maximum_risk)
        assert plan.planned_risk <= plan.maximum_risk

    def test_integer_floor_no_over_risk(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("30"),
            reward_distance=Decimal("60"), risk_reward_ratio=Decimal("2"),
        )
        # max_risk=10, risk/unit=30 -> floor(10/30)=0 -> RISK_LIMIT
        assert plan.risk_plan_status is RiskPlanStatus.RISK_LIMIT_EXCEEDED

    def test_integer_floor_rounds_down(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("3"),
            reward_distance=Decimal("6"), risk_reward_ratio=Decimal("2"),
        )
        # max_risk=10, risk/unit=3 -> floor(10/3)=3, planned_risk=9 <= 10
        assert plan.is_valid
        assert plan.quantity == Decimal("3")
        assert plan.quantity_status in (
            QuantityStatus.FLOOR_ROUNDED, QuantityStatus.DETERMINED,
        )
        assert plan.planned_risk == Decimal("9")
        assert plan.planned_risk <= plan.maximum_risk

    def test_exact_integer_quantity_is_determined(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        # 1000 / 10 = 100 exact
        assert plan.quantity == Decimal("100")
        assert plan.quantity_status is QuantityStatus.DETERMINED

    def test_quantity_step_snaps_to_step(self):
        # step=5 -> only multiples of 5 tradable.
        spec = QuantitySpec(
            quantity_step=Decimal("5"), contract_multiplier=Decimal("1"),
            allow_fractional_quantity=False,
        )
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("3"),
            reward_distance=Decimal("6"), risk_reward_ratio=Decimal("2"),
            quantity_spec=spec,
        )
        # max_risk=10, risk/unit=3 -> raw=3.33 -> floor to step 5 -> 0 -> RISK_LIMIT
        assert plan.risk_plan_status is RiskPlanStatus.RISK_LIMIT_EXCEEDED

    def test_quantity_step_fits(self):
        spec = QuantitySpec(
            quantity_step=Decimal("5"), contract_multiplier=Decimal("1"),
            allow_fractional_quantity=False,
        )
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("10000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("3"),
            reward_distance=Decimal("6"), risk_reward_ratio=Decimal("2"),
            quantity_spec=spec,
        )
        # max_risk=100, risk/unit=3 -> raw=33.33 -> snap to step 5 -> 30
        assert plan.is_valid
        assert plan.quantity == Decimal("30")
        assert plan.planned_risk == Decimal("90")


# ============================================================
# M. PLANNED-RISK CALCULATION
# ============================================================


class TestPlannedRiskCalculation:
    def test_planned_risk_formula(self):
        plan = _valid_plan()
        assert plan.planned_risk == plan.quantity * plan.engine_risk_distance

    def test_planned_risk_never_exceeds_maximum(self):
        # Across many random-ish inputs planned_risk <= maximum_risk.
        eng = TradePlanningEngine()
        for cap in (Decimal("1000"), Decimal("99999"), Decimal("1000000")):
            for risk_dist in (Decimal("0.5"), Decimal("3"), Decimal("17"), Decimal("200")):
                plan = eng.plan(
                    instrument="X", timeframe="15m",
                    account_capital=cap, risk_percent=Decimal("1"),
                    direction="LONG", entry=Decimal("1000"),
                    stop=Decimal("1000") - risk_dist, target_1=Decimal("1000") + risk_dist * 2,
                    risk_distance=risk_dist, reward_distance=risk_dist * 2,
                    risk_reward_ratio=Decimal("2"),
                )
                if plan.is_valid:
                    assert plan.planned_risk <= plan.maximum_risk

    def test_planned_risk_none_when_unsized(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=None, target_1=None,
            risk_distance=None, reward_distance=None, risk_reward_ratio=None,
        )
        assert plan.planned_risk is None


# ============================================================
# N. PLANNED-REWARD CALCULATION
# ============================================================


class TestPlannedRewardCalculation:
    def test_planned_reward_formula(self):
        plan = _valid_plan()
        assert plan.planned_reward == plan.quantity * plan.engine_reward_distance

    def test_planned_reward_none_when_reward_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=None, risk_distance=Decimal("10"),
            reward_distance=None, risk_reward_ratio=None,
        )
        # Geometry has risk but no reward -> quantity sized, planned_reward None.
        assert plan.is_valid
        assert plan.planned_reward is None
        assert plan.planned_risk is not None

    def test_planned_reward_is_deterministic_not_prediction(self):
        plan = _valid_plan()
        assert plan.planned_reward is not None
        # Distinguish from expected return: it is exactly quantity * reward.
        assert plan.planned_reward == plan.quantity * plan.engine_reward_distance


# ============================================================
# O. RISK-LIMIT ENFORCEMENT
# ============================================================


class TestRiskLimitEnforcement:
    def test_risk_limit_exceeded_when_one_unit_too_big(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("50"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("0.4"),
        )
        # max_risk=1, risk/unit=50 -> even 1 unit = 50 > 1 -> RISK_LIMIT
        assert plan.risk_plan_status is RiskPlanStatus.RISK_LIMIT_EXCEEDED
        assert plan.quantity is None
        assert plan.planned_risk is None

    def test_risk_limit_floor_never_over_risks(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("99"),
            target_1=Decimal("102"), risk_distance=Decimal("1"),
            reward_distance=Decimal("3"), risk_reward_ratio=Decimal("3"),
        )
        # max_risk=1, risk/unit=1 -> 1 contract = 1 <= 1
        assert plan.is_valid
        assert plan.quantity == Decimal("1")
        assert plan.planned_risk == Decimal("1")


# ============================================================
# P. ZERO-RISK-DISTANCE HANDLING
# ============================================================


class TestZeroRiskDistance:
    def test_zero_risk_distance_geometry_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("100"),
            target_1=Decimal("100"), risk_distance=Decimal("0"),
            reward_distance=Decimal("0"), risk_reward_ratio=Decimal("0"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE
        assert plan.quantity is None

    def test_negative_risk_distance_geometry_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("110"),
            target_1=Decimal("90"), risk_distance=Decimal("-10"),
            reward_distance=Decimal("10"), risk_reward_ratio=Decimal("-1"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE


# ============================================================
# Q. MISSING GEOMETRY
# ============================================================


class TestMissingGeometry:
    def test_missing_entry_geometry_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=None, stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE

    def test_missing_stop_geometry_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=None,
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE

    def test_missing_risk_distance_geometry_unavailable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=None,
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE


# ============================================================
# R. MISSING TARGET (target NOT required for sizing)
# ============================================================


class TestMissingTarget:
    def test_missing_target_still_sizes(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=None, risk_distance=Decimal("10"),
            reward_distance=None, risk_reward_ratio=None,
        )
        # Risk-only geometry: sizing works; planned_reward None.
        assert plan.is_valid
        assert plan.quantity == Decimal("100")
        assert plan.planned_risk == Decimal("1000")
        assert plan.planned_reward is None

    def test_target_preserved_when_present(self):
        plan = _valid_plan(target_1=Decimal("25600"))
        assert plan.target_1 == Decimal("25600")


# ============================================================
# S. INVALID NUMBERS
# ============================================================


class TestInvalidNumbers:
    def test_capital_none_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=None,
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_boolean_capital_rejected(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=True,
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT


# ============================================================
# T. NaN / INFINITY
# ============================================================


class TestNanInfinity:
    def test_nan_capital_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("NaN"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_infinity_capital_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("Infinity"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_nan_risk_percent_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"),
            risk_percent=Decimal("NaN"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT

    def test_float_nan_capital_invalid(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=math.nan,
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT


# ============================================================
# U. DETERMINISTIC IDS
# ============================================================


class TestDeterministicIds:
    def test_identical_inputs_same_id(self):
        p1 = _valid_plan()
        p2 = _valid_plan()
        assert p1.plan_id == p2.plan_id
        assert p1.plan_id.startswith("plan-")

    def test_different_capital_different_id(self):
        p1 = _valid_plan(account_capital=Decimal("100000"))
        p2 = _valid_plan(account_capital=Decimal("200000"))
        assert p1.plan_id != p2.plan_id

    def test_different_risk_percent_different_id(self):
        p1 = _valid_plan(risk_percent=Decimal("1"))
        p2 = _valid_plan(risk_percent=Decimal("2"))
        assert p1.plan_id != p2.plan_id

    def test_different_instrument_different_id(self):
        p1 = _valid_plan(instrument="A")
        p2 = _valid_plan(instrument="B")
        assert p1.plan_id != p2.plan_id

    def test_different_geometry_different_id(self):
        p1 = _valid_plan(risk_distance=Decimal("200"))
        p2 = _valid_plan(risk_distance=Decimal("100"))
        assert p1.plan_id != p2.plan_id

    def test_no_random_component(self):
        p1 = _valid_plan()
        p2 = _valid_plan()
        # Same id on repeat -> no random / wall-clock.
        assert p1.plan_id == p2.plan_id

    def test_decimal_normalization_in_id(self):
        # Decimal("1.0") and Decimal("1") should produce the same id.
        p1 = _valid_plan(account_capital=Decimal("100000"))
        p2 = _valid_plan(account_capital=Decimal("100000.0"))
        assert p1.plan_id == p2.plan_id


# ============================================================
# V. SHUFFLE / ORDER INDEPENDENCE
# ============================================================


class TestOrderIndependence:
    def test_metadata_order_independent(self):
        eng = TradePlanningEngine()
        p1 = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
            metadata={"a": "1", "b": "2"},
        )
        p2 = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
            metadata={"b": "2", "a": "1"},
        )
        assert p1.plan_id == p2.plan_id

    def test_repeated_calls_identical(self):
        eng = TradePlanningEngine()
        plans = [
            eng.plan(
                instrument="X", timeframe="15m",
                account_capital=Decimal("100000"), risk_percent=Decimal("1"),
                direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
                target_1=Decimal("120"), risk_distance=Decimal("10"),
                reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
            )
            for _ in range(5)
        ]
        assert all(p.plan_id == plans[0].plan_id for p in plans)


# ============================================================
# W. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerializationRoundTrip:
    def test_round_trip_preserves_all_fields(self):
        plan = _valid_plan(
            metadata={"source": "test"},
            label="demo",
        )
        s = serialize_trade_plan(plan)
        plan2 = deserialize_trade_plan(s)
        assert plan2.plan_id == plan.plan_id
        assert plan2.instrument == plan.instrument
        assert plan2.account_capital == plan.account_capital
        assert plan2.risk_percent == plan.risk_percent
        assert plan2.maximum_risk == plan.maximum_risk
        assert plan2.entry == plan.entry
        assert plan2.stop == plan.stop
        assert plan2.target_1 == plan.target_1
        assert plan2.target_2 is None
        assert plan2.target_2_supported is False
        assert plan2.engine_risk_distance == plan.engine_risk_distance
        assert plan2.engine_reward_distance == plan.engine_reward_distance
        assert plan2.engine_risk_reward_ratio == plan.engine_risk_reward_ratio
        assert plan2.quantity == plan.quantity
        assert plan2.planned_risk == plan.planned_risk
        assert plan2.planned_reward == plan.planned_reward
        assert plan2.quantity_status == plan.quantity_status
        assert plan2.risk_plan_status == plan.risk_plan_status
        assert plan2.quantity_spec_available == plan.quantity_spec_available
        assert plan2.warnings == plan.warnings
        assert plan2.rationale == plan.rationale
        assert plan2.label == plan.label
        assert plan2.metadata == plan.metadata

    def test_decimal_preserved_as_decimal(self):
        plan = _valid_plan()
        plan2 = deserialize_trade_plan(serialize_trade_plan(plan))
        assert isinstance(plan2.account_capital, Decimal)
        assert isinstance(plan2.maximum_risk, Decimal)
        assert isinstance(plan2.quantity, Decimal)
        assert isinstance(plan2.planned_risk, Decimal)

    def test_bytes_serialization(self):
        plan = _valid_plan()
        b = serialize_trade_plan_bytes(plan)
        assert isinstance(b, bytes)
        assert deserialize_trade_plan(b.decode("utf-8")).plan_id == plan.plan_id

    def test_deterministic_bytes(self):
        plan = _valid_plan()
        assert serialize_trade_plan(plan) == serialize_trade_plan(plan)
        assert canonical_trade_plan_json(plan) == serialize_trade_plan(plan)

    def test_schema_version_written(self):
        plan = _valid_plan()
        header = parse_trade_plan_header(serialize_trade_plan(plan))
        assert header["schema_version"] == TRADE_PLAN_SCHEMA_VERSION

    def test_invalid_plan_round_trips(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("-1"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        plan2 = deserialize_trade_plan(serialize_trade_plan(plan))
        assert plan2.risk_plan_status is RiskPlanStatus.INVALID_INPUT
        assert plan2.plan_id == plan.plan_id

    def test_geometry_unavailable_round_trips(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=None, stop=None, target_1=None,
            risk_distance=None, reward_distance=None, risk_reward_ratio=None,
        )
        plan2 = deserialize_trade_plan(serialize_trade_plan(plan))
        assert plan2.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE
        assert plan2.quantity is None


# ============================================================
# X. MALFORMED SERIALIZATION
# ============================================================


class TestMalformedSerialization:
    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            deserialize_trade_plan("not-json")

    def test_missing_plan_key_raises(self):
        with pytest.raises(ValueError):
            deserialize_trade_plan('{"schema_version": 1}')

    def test_non_object_payload_raises(self):
        with pytest.raises(ValueError):
            deserialize_trade_plan('"a string"')


# ============================================================
# Y. FUTURE SCHEMA REJECTION
# ============================================================


class TestFutureSchemaRejection:
    def test_future_schema_rejected(self):
        plan = _valid_plan()
        s = serialize_trade_plan(plan)
        # Bump the schema version.
        import json as _json
        parsed = _json.loads(s)
        parsed["schema_version"] = 999
        bad = _json.dumps(parsed)
        with pytest.raises(ValueError):
            deserialize_trade_plan(bad)

    def test_missing_schema_rejected(self):
        plan = _valid_plan()
        s = serialize_trade_plan(plan)
        import json as _json
        parsed = _json.loads(s)
        parsed.pop("schema_version")
        bad = _json.dumps(parsed)
        with pytest.raises(ValueError):
            deserialize_trade_plan(bad)


# ============================================================
# Z. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_geometry_object_not_mutated(self):
        geom = _geom()
        eng = TradePlanningEngine()
        eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            geometry=geom,
        )
        assert geom.entry == Decimal("100")
        assert geom.stop == Decimal("90")
        assert geom.risk_distance == Decimal("10")

    def test_plan_model_is_frozen(self):
        plan = _valid_plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.quantity = Decimal("999")  # type: ignore[misc]

    def test_repeated_planning_no_state_leak(self):
        eng = TradePlanningEngine()
        p1 = _valid_plan_via(eng, account_capital=Decimal("100000"))
        p2 = _valid_plan_via(eng, account_capital=Decimal("200000"))
        # The engine is stateless; p1 unaffected by p2.
        assert p1.account_capital == Decimal("100000")
        assert p2.account_capital == Decimal("200000")


def _valid_plan_via(eng, **overrides):
    kwargs = dict(
        instrument="NIFTY", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("25000"), stop=Decimal("24800"),
        target_1=Decimal("25600"), risk_distance=Decimal("200"),
        reward_distance=Decimal("600"), risk_reward_ratio=Decimal("3"),
    )
    kwargs.update(overrides)
    return eng.plan(**kwargs)


# ============================================================
# AA. REFERENCE / GEOMETRY PRESERVATION
# ============================================================


class TestReferenceGeometryPreservation:
    def test_engine_geometry_provenance_documented(self):
        plan = _valid_plan()
        # The plan carries the engine_* named fields explicitly.
        assert plan.engine_risk_distance == Decimal("200")
        assert plan.engine_reward_distance == Decimal("600")
        assert plan.engine_risk_reward_ratio == Decimal("3")

    def test_plan_does_not_invent_levels(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=None, risk_distance=Decimal("10"),
            reward_distance=None, risk_reward_ratio=None,
        )
        # target_1 / reward stay None — never invented.
        assert plan.target_1 is None
        assert plan.engine_reward_distance is None
        assert plan.planned_reward is None


# ============================================================
# AB. DECISION PRESERVATION
# ============================================================


class TestDecisionPreservation:
    @pytest.mark.parametrize("dec", ["REJECTED", "WATCH", "QUALIFIED", "PREFERRED"])
    def test_decision_reused_verbatim(self, dec):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", existing_decision=dec,
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.existing_decision == dec

    def test_valid_plan_does_not_upgrade_watch_to_qualified(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", existing_decision="WATCH",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        # WATCH stays WATCH — the plan never upgrades the decision.
        assert plan.existing_decision == "WATCH"
        assert plan.is_valid  # the RISK plan is valid; the decision is unchanged.

    def test_plan_never_renamed_to_buy_sell(self):
        plan = _valid_plan(existing_decision="QUALIFIED")
        assert "BUY" not in plan.existing_decision
        assert "SELL" not in plan.existing_decision


# ============================================================
# AC. ACTIONABILITY PRESERVATION
# ============================================================


class TestActionabilityPreservation:
    def test_actionability_reused_verbatim(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", actionability="READY_FOR_REVIEW",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.actionability == "READY_FOR_REVIEW"

    def test_risk_plan_status_distinct_from_actionability(self):
        plan = _valid_plan()
        # The risk plan status is a separate vocabulary.
        assert plan.risk_plan_status is RiskPlanStatus.VALID
        assert plan.risk_plan_status.value not in (
            "INVALID", "NO_OPPORTUNITY", "TRADE_GEOMETRY_UNAVAILABLE",
            "INSUFFICIENT_EVIDENCE", "READY_FOR_REVIEW", "WAIT",
        )


# ============================================================
# AD. EVIDENCE PRESERVATION (separation)
# ============================================================


class TestEvidenceSeparation:
    def test_plan_api_takes_no_evidence_argument(self):
        sig = _sig(TradePlanningEngine.plan)
        assert "evidence" not in sig
        assert "evidence_strength" not in sig

    def test_evidence_not_used_for_position_size(self):
        # The plan size depends ONLY on capital, risk%, risk_distance.
        plan = _valid_plan()
        assert plan.quantity == plan.maximum_risk / plan.engine_risk_distance


# ============================================================
# AE-AF. NO-LOOK-AHEAD / EVALUATOR NOT CALLED
# ============================================================


class TestNoLookAhead:
    def test_plan_api_no_future_argument(self):
        sig = _sig(TradePlanningEngine.plan)
        assert "future" not in sig
        assert "future_candles" not in sig
        assert "candles" not in sig

    def test_plan_works_with_evaluator_patched_to_raise(self, monkeypatch):
        from engine.intelligence import historical_outcome as ho

        orig = ho.OutcomeEvaluator.evaluate

        def _boom(*a, **k):
            raise RuntimeError("outcome evaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        try:
            plan = _valid_plan()
            assert plan.is_valid
        finally:
            ho.OutcomeEvaluator.evaluate = orig

    def test_plan_works_with_pipeline_patched_to_raise(self, monkeypatch):
        from engine.pipeline import historical_pipeline as hp

        orig = hp.HistoricalEvaluationPipeline.evaluate

        def _boom(*a, **k):
            raise RuntimeError("pipeline must not be called")

        monkeypatch.setattr(hp.HistoricalEvaluationPipeline, "evaluate", _boom)
        try:
            plan = _valid_plan()
            assert plan.is_valid
        finally:
            hp.HistoricalEvaluationPipeline.evaluate = orig


# ============================================================
# AG. HISTORICAL PIPELINE NOT CALLED (covered above) + service no-look-ahead
# ============================================================


class TestServiceNoLookAhead:
    def test_plan_trade_api_no_future_argument(self):
        sig = _sig(DashboardAnalysisService.plan_trade)
        assert "future" not in sig
        assert "future_candles" not in sig

    def test_plan_trade_works_with_evaluator_patched(self, service, monkeypatch):
        from engine.intelligence import historical_outcome as ho

        orig = ho.OutcomeEvaluator.evaluate

        def _boom(*a, **k):
            raise RuntimeError("evaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        try:
            view = service.plan_trade(
                TradePlanRequest(
                    instrument="NIFTY", account_capital="100000",
                    risk_percent="1", setup_timeframe="15m",
                ),
            )
            assert view is not None
        finally:
            ho.OutcomeEvaluator.evaluate = orig

    def test_plan_trade_works_with_pipeline_patched(self, service, monkeypatch):
        from engine.pipeline import historical_pipeline as hp

        orig = hp.HistoricalEvaluationPipeline.evaluate

        def _boom(*a, **k):
            raise RuntimeError("pipeline must not be called")

        monkeypatch.setattr(hp.HistoricalEvaluationPipeline, "evaluate", _boom)
        try:
            view = service.plan_trade(
                TradePlanRequest(
                    instrument="NIFTY", account_capital="100000",
                    risk_percent="1", setup_timeframe="15m",
                ),
            )
            assert view is not None
        finally:
            hp.HistoricalEvaluationPipeline.evaluate = orig


# ============================================================
# AH. WORKSTATION INTEGRATION
# ============================================================


class TestWorkstationIntegration:
    def test_workstation_has_trade_plan_section(self, client):
        r = client.get("/workstation")
        assert r.status_code == 200
        assert "Trade Plan / Risk Planning" in r.text

    def test_workstation_trade_plan_form_present(self, client):
        r = client.get("/workstation")
        assert "account_capital" in r.text
        assert "risk_percent" in r.text
        assert "Plan risk" in r.text

    def test_workstation_trade_plan_built_when_params_supplied(self, client):
        r = client.get(
            "/workstation",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        assert r.status_code == 200
        assert "Trade Plan / Risk Planning" in r.text
        # The plan section renders the plan id (NIFTY is a BREAKOUT ->
        # geometry unavailable, but the plan is still built + shown).
        assert "plan-" in r.text


# ============================================================
# AI. API VALIDATION
# ============================================================


class TestApiValidation:
    def test_api_trade_plan_invalid_capital(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "-100", "risk_percent": "1",
            },
        )
        assert r.status_code == 200
        j = r.json()
        assert j["risk_plan_status"] == "INVALID_INPUT"

    def test_api_trade_plan_invalid_risk_percent(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "0",
            },
        )
        assert r.status_code == 200
        j = r.json()
        assert j["risk_plan_status"] == "INVALID_INPUT"

    def test_api_trade_plan_risk_percent_above_max(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "50",
            },
        )
        assert r.status_code == 200
        j = r.json()
        assert j["risk_plan_status"] == "INVALID_INPUT"

    def test_api_trade_plan_returns_no_buy_sell(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        j = r.json()
        text = str(j)
        assert "BUY" not in text
        assert "SELL" not in text
        assert "EXECUTE" not in text
        assert "PLACE ORDER" not in text


# ============================================================
# AJ. API RESPONSE SCHEMA
# ============================================================


class TestApiResponseSchema:
    def test_api_trade_plan_schema(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        j = r.json()
        expected = {
            "plan_id", "instrument", "timeframe", "direction",
            "existing_decision", "actionability",
            "account_capital", "account_capital_float",
            "risk_percent", "risk_percent_float",
            "maximum_risk", "maximum_risk_float",
            "entry", "entry_float", "stop", "stop_float",
            "target_1", "target_1_float",
            "target_2", "target_2_supported",
            "engine_risk_distance", "engine_risk_distance_float",
            "engine_reward_distance", "engine_reward_distance_float",
            "engine_risk_reward_ratio", "engine_risk_reward_ratio_float",
            "quantity", "quantity_float",
            "planned_risk", "planned_risk_float",
            "planned_reward", "planned_reward_float",
            "quantity_status", "risk_plan_status",
            "quantity_spec_available", "warnings", "rationale",
            "label", "metadata", "is_valid", "has_geometry",
        }
        assert expected.issubset(set(j.keys()))

    def test_api_trade_plan_target_2_unsupported(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        j = r.json()
        assert j["target_2"] is None
        assert j["target_2_supported"] is False

    def test_api_trade_plan_decimal_as_string(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        j = r.json()
        # account_capital rendered as a string for precision.
        assert j["account_capital"] == "100000"
        assert isinstance(j["account_capital_float"], float)


# ============================================================
# AK. HTML RENDERING
# ============================================================


class TestHtmlRendering:
    def test_trade_plan_section_shows_warning_text(self, client):
        r = client.get(
            "/workstation",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        assert "deterministic risk calculation" in r.text
        assert "not" in r.text.lower() and "predict" in r.text.lower()

    def test_trade_plan_no_execution_button(self, client):
        r = client.get(
            "/workstation",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        assert "BUY NOW" not in r.text
        assert "SELL NOW" not in r.text
        assert "EXECUTE" not in r.text
        assert "PLACE ORDER" not in r.text

    def test_trade_plan_uses_safe_terminology(self, client):
        r = client.get(
            "/workstation",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        assert "Maximum Planned Loss" in r.text
        assert "Potential Planned Reward" in r.text


# ============================================================
# AL. ERROR STATES
# ============================================================


class TestErrorStates:
    def test_invalid_input_state(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("0"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.INVALID_INPUT
        assert not plan.is_valid

    def test_geometry_unavailable_state(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=None, stop=None, target_1=None,
            risk_distance=None, reward_distance=None, risk_reward_ratio=None,
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE

    def test_risk_limit_exceeded_state(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("50"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("0.4"),
        )
        assert plan.risk_plan_status is RiskPlanStatus.RISK_LIMIT_EXCEEDED

    def test_quantity_unavailable_state(self):
        # A non-positive contract multiplier is rejected at QuantitySpec
        # construction (model validation). The QUANTITY_UNAVAILABLE state
        # arises when the per-contract risk is non-positive defensively
        # inside the engine; here we assert the state exists in the enum
        # and the model carries it correctly when constructed by hand.
        assert RiskPlanStatus.QUANTITY_UNAVAILABLE.value == "QUANTITY_UNAVAILABLE"
        with pytest.raises(ValueError):
            QuantitySpec(contract_multiplier=Decimal("0"))
        with pytest.raises(ValueError):
            QuantitySpec(contract_multiplier=Decimal("-1"))


# ============================================================
# AM. TARGET 2 REMAINS UNSUPPORTED
# ============================================================


class TestTarget2Unsupported:
    def test_target_2_none_in_model(self):
        plan = _valid_plan()
        assert plan.target_2 is None
        assert plan.target_2_supported is False

    def test_target_2_none_in_api(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        j = r.json()
        assert j["target_2"] is None
        assert j["target_2_supported"] is False

    def test_target_2_none_in_html(self, client):
        r = client.get(
            "/workstation",
            params={
                "instrument": "NIFTY", "timeframe": "15m",
                "account_capital": "100000", "risk_percent": "1",
            },
        )
        assert "Not supported" in r.text


# ============================================================
# AN. GEOMETRY UNAVAILABLE
# ============================================================


class TestGeometryUnavailable:
    def test_geometry_unavailable_surfaces_honestly(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=None, stop=None, target_1=None,
            risk_distance=None, reward_distance=None, risk_reward_ratio=None,
        )
        assert plan.risk_plan_status is RiskPlanStatus.GEOMETRY_UNAVAILABLE
        assert plan.quantity is None
        assert plan.planned_risk is None
        assert plan.entry is None
        assert plan.stop is None


# ============================================================
# AO. RISK-PLAN WARNINGS
# ============================================================


class TestRiskPlanWarnings:
    def test_quantity_spec_unavailable_warning_when_no_spec(self):
        plan = _valid_plan()
        assert any(
            "quantity specification unavailable" in w.lower()
            for w in plan.warnings
        )

    def test_no_quantity_spec_warning_when_spec_supplied(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
            quantity_spec=QuantitySpec(),
        )
        assert not any(
            "quantity specification unavailable" in w.lower()
            for w in plan.warnings
        )

    def test_geometry_unavailable_warning(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100000"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=None, stop=None, target_1=None,
            risk_distance=None, reward_distance=None, risk_reward_ratio=None,
        )
        assert any("geometry" in w.lower() for w in plan.warnings)

    def test_risk_limit_warning(self):
        eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("100"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("50"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("0.4"),
        )
        assert any("risk" in w.lower() for w in plan.warnings)


# ============================================================
# AP-AT. REGRESSION (existing routes / scanner / dashboard / pipeline)
# ============================================================


class TestExistingRegression:
    def test_existing_routes_preserved(self, client):
        for path, params in [
            ("/", None),
            ("/health", None),
            ("/api/health", None),
            ("/api/analysis", {"instrument": "NIFTY"}),
            ("/api/instruments", None),
            ("/scan", None),
            ("/api/scan", None),
            ("/workstation", None),
            ("/api/workstation", None),
        ]:
            assert client.get(path, params=params).status_code == 200, path

    def test_api_trade_plan_route_exists(self, client):
        r = client.get(
            "/api/trade-plan",
            params={
                "instrument": "NIFTY", "account_capital": "100000",
                "risk_percent": "1",
            },
        )
        assert r.status_code == 200

    def test_health_still_works(self, client):
        r = client.get("/health")
        j = r.json()
        assert j["status"] == "ok"

    def test_pipeline_baseline_signals_4_trades_3(self):
        from engine.pipeline.datasets import trending_dataset
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
        )

        candles = trending_dataset()
        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        assert result.signals_generated == 4
        assert result.completed_trades == 3


# ============================================================
# REPORTING FORMATTER
# ============================================================


class TestReporting:
    def test_formatter_returns_str(self):
        plan = _valid_plan()
        text = TradePlanFormatter().format(plan)
        assert isinstance(text, str)

    def test_formatter_required_sections(self):
        plan = _valid_plan(label="demo")
        text = TradePlanFormatter().format(plan)
        assert "TRADE PLAN" in text
        assert "ACCOUNT RISK" in text
        assert "TRADE GEOMETRY" in text
        assert "POSITION SIZE" in text
        assert "STATUS" in text
        assert "Maximum planned loss" in text
        assert "Potential planned reward" in text

    def test_formatter_warning_present(self):
        plan = _valid_plan()
        text = TradePlanFormatter().format(plan)
        assert "WARNING" in text
        assert "not a prediction" in text.lower()

    def test_formatter_no_buy_sell_recommendation(self):
        plan = _valid_plan()
        text = TradePlanFormatter().format(plan)
        # The disclaimer legitimately mentions BUY/SELL to say it does
        # NOT constitute one; we check there is no affirmative
        # recommendation language.
        assert "recommendation" in text.lower()
        assert "does NOT constitute a BUY/SELL" in text
        assert "BUY NOW" not in text
        assert "SELL NOW" not in text

    def test_formatter_target_2_unsupported(self):
        plan = _valid_plan()
        text = TradePlanFormatter().format(plan)
        assert "Not supported" in text

    def test_formatter_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            TradePlanFormatter(precision=-1)

    def test_formatter_deterministic(self):
        plan = _valid_plan()
        assert TradePlanFormatter().format(plan) == TradePlanFormatter().format(plan)

    def test_formatter_invalid_plan(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m", account_capital=Decimal("-1"),
            risk_percent=Decimal("1"), direction="LONG",
            entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
            risk_distance=Decimal("10"), reward_distance=Decimal("20"),
            risk_reward_ratio=Decimal("2"),
        )
        text = TradePlanFormatter().format(plan)
        assert "INVALID_INPUT" in text


# ============================================================
# VIEW MODEL
# ============================================================


class TestViewModel:
    def test_trade_plan_view_to_jsonable(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        from dashboard.services import _to_trade_plan_view
        view = _to_trade_plan_view(plan)
        j = trade_plan_view_to_jsonable(view)
        assert j["plan_id"] == plan.plan_id
        assert j["is_valid"] is True
        assert j["target_2_supported"] is False

    def test_trade_plan_view_is_frozen(self):
        view = TradePlanView(plan_id="plan-x")
        with pytest.raises((AttributeError, TypeError)):
            view.plan_id = "other"  # type: ignore[misc]


# ============================================================
# FINANCIAL ROUNDING EDGE CASES
# ============================================================


class TestFinancialRounding:
    def test_exact_division(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("99"),
            target_1=Decimal("102"), risk_distance=Decimal("1"),
            reward_distance=Decimal("2"), risk_reward_ratio=Decimal("2"),
        )
        # max_risk=10, risk/unit=1 -> exact qty=10
        assert plan.quantity == Decimal("10")
        assert plan.planned_risk == Decimal("10")

    def test_tiny_risk_distance(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("99.99"),
            target_1=Decimal("100.02"), risk_distance=Decimal("0.01"),
            reward_distance=Decimal("0.02"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.is_valid
        assert plan.planned_risk <= plan.maximum_risk

    def test_large_risk_distance(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90000"),
            target_1=Decimal("200"), risk_distance=Decimal("80000"),
            reward_distance=Decimal("100"), risk_reward_ratio=Decimal("0.00125"),
        )
        # max_risk=1000, risk/unit=80000 -> fractional qty=0.0125
        # planned_risk = 0.0125 * 80000 = 1000 == maximum_risk (no over-risk).
        assert plan.is_valid
        assert plan.quantity == Decimal("1000") / Decimal("80000")
        assert plan.planned_risk <= plan.maximum_risk

    def test_very_small_account(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("100"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.is_valid
        assert plan.maximum_risk == Decimal("1")
        assert plan.quantity == Decimal("0.1")
        assert plan.planned_risk == Decimal("1")

    def test_very_large_account(self):
        eng = TradePlanningEngine()
        plan = eng.plan(
            instrument="X", timeframe="15m",
            account_capital=Decimal("1000000000"), risk_percent=Decimal("1"),
            direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
            target_1=Decimal("120"), risk_distance=Decimal("10"),
            reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
        )
        assert plan.is_valid
        assert plan.maximum_risk == Decimal("10000000")
        assert plan.quantity == Decimal("1000000")

    def test_decimal_not_float_for_money(self):
        plan = _valid_plan()
        assert isinstance(plan.account_capital, Decimal)
        assert isinstance(plan.maximum_risk, Decimal)
        assert isinstance(plan.planned_risk, Decimal)
        assert not isinstance(plan.account_capital, float)

    def test_floor_never_over_risks_fractional(self):
        # Fractional floor: ensure planned_risk <= maximum_risk for many cases.
        eng = TradePlanningEngine()
        for cap in (Decimal("333"), Decimal("7"), Decimal("123456")):
            for rd in (Decimal("7"), Decimal("13"), Decimal("0.3")):
                plan = eng.plan(
                    instrument="X", timeframe="15m",
                    account_capital=cap, risk_percent=Decimal("1"),
                    direction="LONG", entry=Decimal("100"),
                    stop=Decimal("100") - rd, target_1=Decimal("100") + rd * 2,
                    risk_distance=rd, reward_distance=rd * 2,
                    risk_reward_ratio=Decimal("2"),
                )
                if plan.is_valid:
                    assert plan.planned_risk <= plan.maximum_risk
