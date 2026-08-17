"""
Risk & trade planning demo (Product Phase 4).

Proves the risk / trade planning layer is a THIN, honest, deterministic
calculation around the EXISTING trade candidate / trade geometry. The
planner implements NO market analysis, NO decision logic, NO prediction,
NO probability, NO BUY/SELL/ENTER/EXIT/HOLD recommendation. The existing
Sprint 11S decision classification (REJECTED / WATCH / QUALIFIED /
PREFERRED) is AUTHORITATIVE and never renamed / upgraded / downgraded.
The existing Sprint 11R ``TradeCandidate`` geometry (entry / stop /
target / risk_distance / reward_distance / risk_reward_ratio) is
AUTHORITATIVE and never recomputed; Phase 4 only performs deterministic
calculations AROUND those existing values.

Visibly demonstrates (1-22):

1.  Valid LONG trade plan
2.  Valid SHORT trade plan
3.  Account risk calculation
4.  Maximum monetary risk
5.  Position sizing
6.  Quantity rounding (floor never over-risks)
7.  Planned risk
8.  Planned reward
9.  Existing R:R preservation
10. Invalid account capital
11. Invalid risk percentage
12. Incomplete geometry
13. Unsupported quantity specification
14. Decision remains unchanged
15. Actionability remains unchanged
16. Evidence remains unchanged (separate)
17. Target 2 remains unsupported
18. Serialization round trip
19. Deterministic ID
20. No-look-ahead (OutcomeEvaluator + pipeline patched to raise)
21. Existing workstation integration
22. Pipeline baseline (signals_generated=4, completed_trades=3)

Every demo check prints explicit PASS / FAIL / SKIPPED. The demo exits
0 on success. The plan is DESCRIPTIVE ONLY — it does NOT predict, does
NOT guarantee profitability, and does NOT constitute a trading
recommendation.

Run::

    python scripts/test_trade_planning.py
"""

from __future__ import annotations

import sys
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import FixtureDataProvider
from dashboard.services import (
    DashboardAnalysisService,
    TradePlanRequest,
)
from engine.config.trade_plan_config import TradePlanConfig
from engine.intelligence.trade_planning import TradePlanningEngine
from engine.intelligence.trade_planning_serialization import (
    deserialize_trade_plan,
    serialize_trade_plan,
)
from engine.models.trade_plan import QuantitySpec
from engine.reporting.trade_planning import TradePlanFormatter


# ============================================================
# DEMO HARNESS
# ============================================================


_CHECKS: list[tuple[str, str]] = []


def _check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


# ============================================================
# DEMONSTRATIONS
# ============================================================


def demo_valid_long_plan() -> None:
    _banner("1. Valid LONG trade plan")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="NIFTY", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", existing_decision="QUALIFIED",
        actionability="READY_FOR_REVIEW",
        entry=Decimal("25000"), stop=Decimal("24800"), target_1=Decimal("25600"),
        risk_distance=Decimal("200"), reward_distance=Decimal("600"),
        risk_reward_ratio=Decimal("3"),
    )
    _check("status VALID", plan.risk_plan_status.value == "VALID")
    _check("direction LONG", plan.direction == "LONG")
    _check("maximum_risk = 1000", plan.maximum_risk == Decimal("1000"))
    _check("quantity = 5", plan.quantity == Decimal("5"))
    _check("planned_risk = 1000", plan.planned_risk == Decimal("1000"))
    _check("planned_reward = 3000", plan.planned_reward == Decimal("3000"))
    _check("planned_risk <= maximum_risk", plan.planned_risk <= plan.maximum_risk)


def demo_valid_short_plan() -> None:
    _banner("2. Valid SHORT trade plan")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="REL", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="SHORT", existing_decision="QUALIFIED",
        actionability="READY_FOR_REVIEW",
        entry=Decimal("100"), stop=Decimal("110"), target_1=Decimal("80"),
        risk_distance=Decimal("10"), reward_distance=Decimal("20"),
        risk_reward_ratio=Decimal("2"),
    )
    _check("status VALID", plan.risk_plan_status.value == "VALID")
    _check("direction SHORT", plan.direction == "SHORT")
    _check("quantity = 100", plan.quantity == Decimal("100"))
    _check("planned_risk = 1000", plan.planned_risk == Decimal("1000"))
    _check("planned_reward = 2000", plan.planned_reward == Decimal("2000"))


def demo_account_risk() -> None:
    _banner("3. Account risk calculation")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("333333"), risk_percent=Decimal("1.5"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("10"),
        reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
    )
    expected = Decimal("333333") * Decimal("1.5") / Decimal("100")
    _check(
        f"maximum_risk = {expected}",
        plan.maximum_risk == expected,
    )
    _check("Decimal preserved", isinstance(plan.maximum_risk, Decimal))


def demo_maximum_monetary_risk() -> None:
    _banner("4. Maximum monetary risk")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("500000"), risk_percent=Decimal("2"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("10"),
        reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
    )
    _check("2% of 500000 = 10000", plan.maximum_risk == Decimal("10000"))


def demo_position_sizing() -> None:
    _banner("5. Position sizing")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("25"),
        reward_distance=Decimal("50"), risk_reward_ratio=Decimal("2"),
    )
    # max_risk=1000, risk/unit=25 -> qty=40
    _check("quantity = 40", plan.quantity == Decimal("40"))


def demo_quantity_rounding() -> None:
    _banner("6. Quantity rounding (floor never over-risks)")
    eng = TradePlanningEngine(TradePlanConfig(allow_fractional_quantity=False))
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("1000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("3"),
        reward_distance=Decimal("6"), risk_reward_ratio=Decimal("2"),
    )
    # max_risk=10, risk/unit=3 -> floor(10/3)=3, planned_risk=9 <= 10
    _check("floored quantity = 3", plan.quantity == Decimal("3"))
    _check("planned_risk = 9", plan.planned_risk == Decimal("9"))
    _check("planned_risk <= maximum_risk", plan.planned_risk <= plan.maximum_risk)


def demo_planned_risk() -> None:
    _banner("7. Planned risk")
    plan = demo_long_plan()
    _check(
        "planned_risk = quantity * engine_risk_distance",
        plan.planned_risk == plan.quantity * plan.engine_risk_distance,
    )


def demo_planned_reward() -> None:
    _banner("8. Planned reward")
    plan = demo_long_plan()
    _check(
        "planned_reward = quantity * engine_reward_distance",
        plan.planned_reward == plan.quantity * plan.engine_reward_distance,
    )


def demo_rr_preservation() -> None:
    _banner("9. Existing R:R preservation")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("10"),
        reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2.5"),
    )
    _check(
        "engine_risk_reward_ratio reused verbatim (not recomputed)",
        plan.engine_risk_reward_ratio == Decimal("2.5"),
    )


def demo_invalid_capital() -> None:
    _banner("10. Invalid account capital")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m", account_capital=Decimal("0"),
        risk_percent=Decimal("1"), direction="LONG",
        entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
        risk_distance=Decimal("10"), reward_distance=Decimal("20"),
        risk_reward_ratio=Decimal("2"),
    )
    _check("status INVALID_INPUT", plan.risk_plan_status.value == "INVALID_INPUT")
    _check("no quantity fabricated", plan.quantity is None)


def demo_invalid_risk_percent() -> None:
    _banner("11. Invalid risk percentage")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m", account_capital=Decimal("100000"),
        risk_percent=Decimal("50"), direction="LONG",
        entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
        risk_distance=Decimal("10"), reward_distance=Decimal("20"),
        risk_reward_ratio=Decimal("2"),
    )
    _check("status INVALID_INPUT (above max)", plan.risk_plan_status.value == "INVALID_INPUT")


def demo_incomplete_geometry() -> None:
    _banner("12. Incomplete geometry")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m", account_capital=Decimal("100000"),
        risk_percent=Decimal("1"), direction="LONG",
        entry=Decimal("100"), stop=None, target_1=None,
        risk_distance=None, reward_distance=None, risk_reward_ratio=None,
    )
    _check(
        "status GEOMETRY_UNAVAILABLE",
        plan.risk_plan_status.value == "GEOMETRY_UNAVAILABLE",
    )
    _check("no quantity invented", plan.quantity is None)


def demo_unsupported_quantity_spec() -> None:
    _banner("13. Unsupported quantity specification")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("10"),
        reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
    )
    has_warn = any(
        "quantity specification unavailable" in w.lower()
        for w in plan.warnings
    )
    _check("QUANTITY_SPEC_UNAVAILABLE warning surfaced", has_warn)
    _check("quantity_spec_available False", plan.quantity_spec_available is False)


def demo_decision_unchanged() -> None:
    _banner("14. Decision remains unchanged")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", existing_decision="WATCH",
        entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
        risk_distance=Decimal("10"), reward_distance=Decimal("20"),
        risk_reward_ratio=Decimal("2"),
    )
    _check(
        "WATCH stays WATCH (not upgraded)",
        plan.existing_decision == "WATCH",
    )
    _check("no BUY/SELL language", "BUY" not in plan.existing_decision)


def demo_actionability_unchanged() -> None:
    _banner("15. Actionability remains unchanged")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", actionability="READY_FOR_REVIEW",
        entry=Decimal("100"), stop=Decimal("90"), target_1=Decimal("120"),
        risk_distance=Decimal("10"), reward_distance=Decimal("20"),
        risk_reward_ratio=Decimal("2"),
    )
    _check(
        "actionability reused verbatim",
        plan.actionability == "READY_FOR_REVIEW",
    )
    _check(
        "risk_plan_status distinct from actionability",
        plan.risk_plan_status.value not in (
            "INVALID", "READY_FOR_REVIEW", "WAIT",
        ),
    )


def demo_evidence_separate() -> None:
    _banner("16. Evidence remains unchanged (separate)")
    eng = TradePlanningEngine()
    plan = eng.plan(
        instrument="X", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("100"), stop=Decimal("90"),
        target_1=Decimal("120"), risk_distance=Decimal("10"),
        reward_distance=Decimal("20"), risk_reward_ratio=Decimal("2"),
    )
    # The plan API takes no evidence argument; evidence is never used.
    import inspect
    sig = set(inspect.signature(TradePlanningEngine.plan).parameters)
    _check("plan API takes no evidence argument", "evidence" not in sig)
    _check(
        "quantity depends only on capital/risk%/risk_distance",
        plan.quantity == plan.maximum_risk / plan.engine_risk_distance,
    )


def demo_target_2_unsupported() -> None:
    _banner("17. Target 2 remains unsupported")
    plan = demo_long_plan()
    _check("target_2 None", plan.target_2 is None)
    _check("target_2_supported False", plan.target_2_supported is False)


def demo_serialization() -> None:
    _banner("18. Serialization round trip")
    plan = demo_long_plan()
    s = serialize_trade_plan(plan)
    plan2 = deserialize_trade_plan(s)
    _check("plan_id preserved", plan.plan_id == plan2.plan_id)
    _check("quantity preserved", plan.quantity == plan2.quantity)
    _check("planned_risk preserved", plan.planned_risk == plan2.planned_risk)
    _check("maximum_risk preserved", plan.maximum_risk == plan2.maximum_risk)
    _check(
        "Decimal type preserved",
        isinstance(plan2.maximum_risk, Decimal),
    )
    _check(
        "deterministic bytes",
        serialize_trade_plan(plan) == serialize_trade_plan(plan2),
    )


def demo_deterministic_id() -> None:
    _banner("19. Deterministic ID")
    eng = TradePlanningEngine()
    p1 = demo_long_plan()
    p2 = eng.plan(
        instrument="NIFTY", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("25000"), stop=Decimal("24800"),
        target_1=Decimal("25600"), risk_distance=Decimal("200"),
        reward_distance=Decimal("600"), risk_reward_ratio=Decimal("3"),
    )
    _check("identical inputs -> identical id", p1.plan_id == p2.plan_id)
    _check("id prefixed 'plan-'", p1.plan_id.startswith("plan-"))
    p3 = eng.plan(
        instrument="NIFTY", timeframe="15m",
        account_capital=Decimal("200000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("25000"), stop=Decimal("24800"),
        target_1=Decimal("25600"), risk_distance=Decimal("200"),
        reward_distance=Decimal("600"), risk_reward_ratio=Decimal("3"),
    )
    _check("different capital -> different id", p1.plan_id != p3.plan_id)


def demo_no_look_ahead() -> None:
    _banner("20. No-look-ahead (evaluator + pipeline patched to raise)")
    from engine.intelligence import historical_outcome as ho
    from engine.pipeline import historical_pipeline as hp

    orig_eval = ho.OutcomeEvaluator.evaluate
    orig_pipe = hp.HistoricalEvaluationPipeline.evaluate

    def _boom_eval(*a, **k):
        raise RuntimeError("outcome evaluator must not be called")

    def _boom_pipe(*a, **k):
        raise RuntimeError("pipeline must not be called")

    ho.OutcomeEvaluator.evaluate = _boom_eval
    hp.HistoricalEvaluationPipeline.evaluate = _boom_pipe
    try:
        plan = demo_long_plan()
        _check("plan computed with evaluator patched", plan.is_valid)
        _check("plan computed with pipeline patched", plan.is_valid)
    finally:
        ho.OutcomeEvaluator.evaluate = orig_eval
        hp.HistoricalEvaluationPipeline.evaluate = orig_pipe
    _check("OutcomeEvaluator restored", ho.OutcomeEvaluator.evaluate is orig_eval)


def demo_workstation_integration() -> None:
    _banner("21. Existing workstation integration")
    svc = DashboardAnalysisService(provider=FixtureDataProvider())
    client = TestClient(create_app(service=svc))
    # HTML workstation with trade plan params
    r = client.get(
        "/workstation",
        params={
            "instrument": "NIFTY", "timeframe": "15m",
            "account_capital": "100000", "risk_percent": "1",
        },
    )
    _check("workstation HTML 200", r.status_code == 200)
    _check("has Trade Plan section", "Trade Plan / Risk Planning" in r.text)
    _check("has Plan risk button", "Plan risk" in r.text)
    # API trade plan
    r2 = client.get(
        "/api/trade-plan",
        params={
            "instrument": "NIFTY", "timeframe": "15m",
            "account_capital": "100000", "risk_percent": "1",
        },
    )
    _check("api/trade-plan 200", r2.status_code == 200)
    j = r2.json()
    _check("api returns plan_id", j["plan_id"].startswith("plan-"))
    _check(
        "api returns risk_plan_status",
        j["risk_plan_status"] in (
            "VALID", "INVALID_INPUT", "GEOMETRY_UNAVAILABLE",
            "RISK_LIMIT_EXCEEDED", "QUANTITY_UNAVAILABLE",
        ),
    )
    _check("api target_2 unsupported", j["target_2"] is None)
    _check("no BUY/SELL in response", "BUY" not in str(j))


def demo_pipeline_baseline() -> None:
    _banner("22. Pipeline baseline (signals=4, trades=3)")
    from engine.pipeline.datasets import trending_dataset
    from engine.pipeline.historical_pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
    )

    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
    _check("signals_generated == 4", result.signals_generated == 4)
    _check("completed_trades == 3", result.completed_trades == 3)


# ============================================================
# HELPERS
# ============================================================


def demo_long_plan():
    eng = TradePlanningEngine()
    return eng.plan(
        instrument="NIFTY", timeframe="15m",
        account_capital=Decimal("100000"), risk_percent=Decimal("1"),
        direction="LONG", entry=Decimal("25000"), stop=Decimal("24800"),
        target_1=Decimal("25600"), risk_distance=Decimal("200"),
        reward_distance=Decimal("600"), risk_reward_ratio=Decimal("3"),
    )


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    print()
    print("=" * 60)
    print("PRODUCT PHASE 4 — RISK & TRADE PLANNING DEMO")
    print("=" * 60)
    print(
        "The risk / trade planning layer is a THIN, deterministic "
        "calculation around the EXISTING trade candidate / geometry. It "
        "does NOT predict, does NOT guarantee profitability, and does NOT "
        "constitute a trading recommendation. The existing decision and "
        "geometry remain AUTHORITATIVE.",
    )

    demo_valid_long_plan()
    demo_valid_short_plan()
    demo_account_risk()
    demo_maximum_monetary_risk()
    demo_position_sizing()
    demo_quantity_rounding()
    demo_planned_risk()
    demo_planned_reward()
    demo_rr_preservation()
    demo_invalid_capital()
    demo_invalid_risk_percent()
    demo_incomplete_geometry()
    demo_unsupported_quantity_spec()
    demo_decision_unchanged()
    demo_actionability_unchanged()
    demo_evidence_separate()
    demo_target_2_unsupported()
    demo_serialization()
    demo_deterministic_id()
    demo_no_look_ahead()
    demo_workstation_integration()
    demo_pipeline_baseline()

    # Print a full trade plan report.
    _banner("FULL TRADE PLAN REPORT (illustrative LONG)")
    print(TradePlanFormatter().format(demo_long_plan()))

    # Summary
    _banner("SUMMARY")
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"  Total checks: {len(_CHECKS)}")
    print(f"  PASS: {passed}")
    print(f"  FAIL: {failed}")
    if failed:
        print()
        print("FAILED checks:")
        for label, status in _CHECKS:
            if status == "FAIL":
                print(f"  - {label}")
        sys.exit(1)
    print()
    print("Product Phase 4 demo completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
