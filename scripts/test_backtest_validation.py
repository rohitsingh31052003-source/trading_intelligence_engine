"""
Demo for Sprint 12C — Robust Historical Backtesting & Adversarial
Validation.

This demo proves the VALIDATION layer that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    architecture remain correct, deterministic, leak-free and
    accounting-consistent when subjected to broader historical replay
    and adversarial conditions?"

The demo REUSES the existing Sprint 11X / 11Y / 11Z / 12A / 12B engines
(it does NOT duplicate them) and runs the Sprint 12C validation engine
over a matrix of historical / adversarial scenarios. It NEVER
re-evaluates outcomes with future data, NEVER re-runs the pipeline to
influence decisions at T, NEVER introduces machine learning or
predictive models, and NEVER generates trading signals.

The demo visibly proves:

1.  Mixed historical replay (accounting reconciliation + cohort
    reconciliation over a mixed-outcome scenario).
2.  Accounting reconciliation (overall + breakdowns + R identity +
    excluded outcomes).
3.  Point-in-time protection (decision at T stable; OutcomeEvaluator +
    pipeline patched to raise; downstream chain unaffected).
4.  Look-ahead protection (no hidden candle dependency downstream).
5.  Shuffle invariance (shuffled input yields identical ids + results).
6.  Deterministic repeated evaluation (identical ids + results).
7.  Serialization round trip (11Y evidence / 12A context / 12B
    integration / 12C validation report).
8.  Immutability (source outcomes not mutated).
9.  Adversarial scenarios (enormous win / loss, tiny cohorts, all
    BOTH_TOUCHED, all NO_GEOMETRY, empty, single-outcome).
10. Evidence hard-gate preservation (1-trade +2R 100% win-rate cohort
    stays INSUFFICIENT through 11Y -> 11Z -> 12A -> 12B).
11. 12B decision authority preservation (QUALIFIED / PREFERRED / WATCH
    / REJECTED never modified by integration, under every evidence
    regime).
12. Complete 11V -> 12B -> 12C chain (end-to-end validation report).

Validation results are DESCRIPTIVE. They do NOT predict future market
behavior and do NOT guarantee profitability.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.backtest_validation_config import BacktestValidationConfig
from engine.intelligence.backtest_validation import BacktestValidationEngine
from engine.intelligence.backtest_validation_serialization import (
    BACKTEST_VALIDATION_SCHEMA_VERSION,
    deserialize_validation,
    serialize_validation,
)
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import (
    OutcomeEvaluator,
    build_outcome_subject,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.backtest_validation import ValidationCheckStatus
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegrationStatus,
)
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.backtest_validation import BacktestValidationFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# SCENARIO BUILDERS
# ============================================================


def _subject(
    i: int,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
    ts: datetime | None = None,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts or _EPOCH + timedelta(days=i),
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=1,
        scan_id="scan-12c",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    i: int = 0,
    realized_r: float | None = None,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
) -> HistoricalOutcome:
    return HistoricalOutcome(
        subject=_subject(
            i, instrument=instrument, direction=direction,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=(mfe / 5.0) if mfe is not None else None,
        mae_r=(mae / 5.0) if mae is not None else None,
        risk=5.0,
    )


def _resolved(
    n: int,
    win_fraction: float,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    import random
    rng = random.Random(seed)
    out: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        out.append(
            _outcome(
                OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT,
                i=i, realized_r=2.0 if win else -1.0,
                instrument=instrument, direction=direction,
                setup_type=setup_type, mtf_alignment=mtf_alignment,
            ),
        )
    return out


def _decision(
    direction: str = "LONG", classification: str = "QUALIFIED", score: int = 75,
) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1,
        geometry_complete=True,
        confluence_score=4,
        risk_reward_ratio=2.0,
        entry=100.0,
        stop=95.0,
        target=110.0,
    )


def _profile(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
) -> OpportunityProfile:
    return OpportunityProfile(
        instrument=instrument,
        direction=direction,
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


# ============================================================
# DEMO
# ============================================================


_CHECKS: list[tuple[str, bool]] = []


def _check(label: str, condition: bool) -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}")
    _CHECKS.append((label, condition))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    eng = BacktestValidationEngine(
        BacktestValidationConfig(label="sprint-12c-demo"),
    )

    # ---- Build a representative scenario matrix -----------------
    mixed = _resolved(40, 0.6, seed=1)
    losing = _resolved(30, 0.2, instrument="TCS", seed=2)
    winning = _resolved(30, 0.8, instrument="RELIANCE", seed=3)
    short_bear = _resolved(
        25, 0.55, instrument="HDFCBANK", direction="SHORT",
        setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=4,
    )
    # Adversarial: tiny impressive cohort (1 trade, +2R, 100% win).
    tiny = [
        _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0,
                 instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION"),
    ]
    # Adversarial: all BOTH_TOUCHED.
    both_touched = [
        _outcome(OutcomeStatus.BOTH_TOUCHED, i=k, realized_r=None,
                 instrument="NIFTY")
        for k in range(8)
    ]
    # Adversarial: all NO_GEOMETRY.
    no_geometry = [
        _outcome(OutcomeStatus.NO_GEOMETRY, i=k, realized_r=None,
                 instrument="TCS")
        for k in range(6)
    ]
    # Adversarial: enormous win + enormous loss.
    extreme = [
        _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=50.0),
        _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-50.0),
    ]

    scenarios = [
        {
            "name": "mixed-outcomes",
            "outcomes": tuple(mixed),
            "decision": _decision(),
            "expected_classification": "QUALIFIED",
        },
        {
            "name": "losing-sequence",
            "outcomes": tuple(losing),
            "decision": _decision(classification="WATCH"),
            "expected_classification": "WATCH",
            "profile": _profile(instrument="TCS"),
        },
        {
            "name": "winning-sequence",
            "outcomes": tuple(winning),
            "decision": _decision(classification="PREFERRED", score=90),
            "expected_classification": "PREFERRED",
            "profile": _profile(instrument="RELIANCE"),
        },
        {
            "name": "short-bearish",
            "outcomes": tuple(short_bear),
            "decision": _decision(direction="SHORT", classification="QUALIFIED"),
            "expected_classification": "QUALIFIED",
            "profile": _profile(
                instrument="HDFCBANK", direction="SHORT",
                setup_type="BREAKOUT", mtf_alignment="CONFLICTING",
            ),
        },
        {
            "name": "tiny-impressive-cohort",
            "outcomes": tuple(tiny),
            "decision": _decision(),
            "expected_classification": "QUALIFIED",
            "profile": _profile(
                instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION",
            ),
        },
        {"name": "all-both-touched", "outcomes": tuple(both_touched)},
        {"name": "all-no-geometry", "outcomes": tuple(no_geometry)},
        {"name": "extreme-win-loss", "outcomes": tuple(extreme)},
        {"name": "empty-scenario", "outcomes": ()},
        {
            "name": "single-outcome",
            "outcomes": (_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3),),
        },
    ]

    report = eng.validate(scenarios, label="sprint-12c-demo")
    fmt = BacktestValidationFormatter()

    # 1. Mixed historical replay.
    _banner("1. Mixed historical replay")
    print(
        f"  overall={report.overall_status.name} "
        f"scenarios={report.scenario_count} "
        f"checks={report.check_count} "
        f"(pass={report.passed_count} fail={report.failed_count} "
        f"skip={report.skipped_count})",
    )
    _check("mixed scenario validated", report.scenario_count >= 1)
    _check(
        "overall validation passed",
        report.overall_status == ValidationCheckStatus.PASS,
    )

    # 2. Accounting reconciliation.
    _banner("2. Accounting reconciliation")
    _check(
        "accounting status PASS",
        report.accounting_status == ValidationCheckStatus.PASS,
    )
    # Independently recompute one cohort to show the cross-check.
    perf = PerformanceAnalyticsEngine()
    analytics = perf.analyze(mixed)
    from engine.intelligence.backtest_validation import _recompute_statistics
    expected = _recompute_statistics(mixed)
    _check(
        "overall statistics reconcile independently",
        expected.total == analytics.overall.total
        and expected.target_hits == analytics.overall.target_hits
        and expected.stop_hits == analytics.overall.stop_hits,
    )

    # 3. Point-in-time protection.
    _banner("3. Point-in-time protection")
    _check(
        "look-ahead status PASS",
        report.look_ahead_status == ValidationCheckStatus.PASS,
    )
    # Demonstrate directly: patch evaluator + pipeline, run downstream.
    original_eval = OutcomeEvaluator.evaluate
    original_pipe = HistoricalEvaluationPipeline.evaluate

    def boom_eval(self, subject, future_candles):  # noqa: ANN001
        raise RuntimeError("must not re-evaluate outcomes")

    def boom_pipe(self, candles):  # noqa: ANN001
        raise RuntimeError("must not re-run pipeline")

    OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
    HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
    try:
        ev = HistoricalEvidenceEngine().evaluate(mixed)
        _check(
            "evidence built with evaluator+pipeline patched to raise",
            ev.summary.sample_count == len(mixed),
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]

    # 4. Look-ahead protection.
    _banner("4. Look-ahead protection")
    _check(
        "look-ahead category PASS",
        report.look_ahead_status == ValidationCheckStatus.PASS,
    )

    # 5. Shuffle invariance.
    _banner("5. Shuffle invariance")
    perf = PerformanceAnalyticsEngine()
    ev_eng = HistoricalEvidenceEngine()
    a1 = perf.analyze(mixed)
    e1 = ev_eng.evaluate(mixed)
    import random
    shuffled = list(mixed)
    random.Random(99).shuffle(shuffled)
    a2 = perf.analyze(shuffled)
    e2 = ev_eng.evaluate(shuffled)
    _check(
        "shuffled input same analytics id",
        a1.analytics_id == a2.analytics_id,
    )
    _check(
        "shuffled input same evidence id",
        e1.evidence_id == e2.evidence_id,
    )

    # 6. Deterministic repeated evaluation.
    _banner("6. Deterministic repeated evaluation")
    report_again = eng.validate(scenarios, label="sprint-12c-demo")
    _check(
        "repeated validation same id",
        report.validation_id == report_again.validation_id,
    )
    _check(
        "repeated validation same overall status",
        report.overall_status == report_again.overall_status,
    )
    _check(
        "determinism status PASS",
        report.determinism_status == ValidationCheckStatus.PASS,
    )

    # 7. Serialization round trip.
    _banner("7. Serialization round trip")
    rt = deserialize_validation(serialize_validation(report))
    _check(
        "validation report round trip preserves id",
        rt.validation_id == report.validation_id,
    )
    _check(
        "validation report round trip preserves overall status",
        rt.overall_status == report.overall_status,
    )
    _check(
        "validation report round trip preserves scenario count",
        rt.scenario_count == report.scenario_count,
    )
    _check(
        "schema version is 1",
        BACKTEST_VALIDATION_SCHEMA_VERSION == 1,
    )

    # 8. Immutability.
    _banner("8. Immutability")
    snapshot = [
        (o.outcome_status, o.realized_r, o.subject.instrument) for o in mixed
    ]
    _ = eng.validate(
        [{"name": "imm", "outcomes": tuple(mixed), "decision": _decision()}],
    )
    after = [
        (o.outcome_status, o.realized_r, o.subject.instrument) for o in mixed
    ]
    _check("source outcomes not mutated", snapshot == after)

    # 9. Adversarial scenarios.
    _banner("9. Adversarial scenarios")
    adv_report = eng.validate(
        [
            {"name": "tiny-impressive", "outcomes": tuple(tiny)},
            {"name": "all-both-touched", "outcomes": tuple(both_touched)},
            {"name": "all-no-geometry", "outcomes": tuple(no_geometry)},
            {"name": "extreme-win-loss", "outcomes": tuple(extreme)},
            {"name": "empty", "outcomes": ()},
            {"name": "single", "outcomes": tuple([_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3)])},
        ],
        label="adversarial",
    )
    _check(
        "adversarial suite completed (no exceptions)",
        adv_report.scenario_count == 6,
    )
    # Tiny impressive cohort must NOT produce strong evidence.
    ev_tiny = HistoricalEvidenceEngine().evaluate(tiny)
    _check(
        "tiny 1-trade 100%-win cohort is INSUFFICIENT",
        ev_tiny.summary.strength == EvidenceStrength.INSUFFICIENT,
    )
    # All BOTH_TOUCHED must produce no fabricated realized R.
    ev_bt = HistoricalEvidenceEngine().evaluate(both_touched)
    _check(
        "all-BOTH_TOUCHED: win_rate unavailable (no fabricated win/loss)",
        ev_bt.summary.statistics.win_rate is None,
    )
    _check(
        "all-BOTH_TOUCHED: total_realized_r unavailable",
        ev_bt.summary.statistics.total_realized_r is None,
    )
    # All NO_GEOMETRY must carry no realized R.
    ev_ng = HistoricalEvidenceEngine().evaluate(no_geometry)
    _check(
        "all-NO_GEOMETRY: total_realized_r unavailable",
        ev_ng.summary.statistics.total_realized_r is None,
    )

    # 10. Evidence hard-gate preservation.
    _banner("10. Evidence hard-gate preservation")
    strat = StrategyIntelligenceEngine()
    di_eng = DecisionIntelligenceEngine()
    integ_eng = DecisionIntelligenceIntegrationEngine()
    profile_tiny = _profile(
        instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION",
    )
    ev_tiny_full = HistoricalEvidenceEngine().evaluate(tiny)
    lookup_tiny = strat.lookup(ev_tiny_full, profile_tiny)
    di_tiny = di_eng.build(profile_tiny, _decision(), lookup_tiny)
    integ_tiny = integ_eng.integrate(_decision(), di_tiny, profile_tiny)
    print(
        f"  tiny cohort: evidence={ev_tiny_full.summary.strength.name} "
        f"12A-context={di_tiny.decision_context_status.name} "
        f"12B-status={integ_tiny.integration_status.name}",
    )
    _check(
        "11Y strength INSUFFICIENT for tiny cohort",
        ev_tiny_full.summary.strength == EvidenceStrength.INSUFFICIENT,
    )
    _check(
        "12A context INSUFFICIENT_EVIDENCE (not upgraded)",
        di_tiny.decision_context_status
        == DecisionContextStatus.INSUFFICIENT_EVIDENCE,
    )
    _check(
        "12B did not modify existing decision classification",
        integ_tiny.existing_decision_summary.decision_classification == "QUALIFIED",
    )

    # 11. 12B decision authority preservation.
    _banner("11. 12B decision authority preservation")
    ev_supportive = HistoricalEvidenceEngine().evaluate(winning)
    profile_win = _profile(instrument="RELIANCE")
    lookup_win = strat.lookup(ev_supportive, profile_win)
    di_win = di_eng.build(profile_win, _decision(), lookup_win)
    for cls in ("REJECTED", "WATCH", "QUALIFIED", "PREFERRED"):
        dec = _decision(classification=cls)
        integ = integ_eng.integrate(dec, di_win, profile_win)
        preserved = (
            integ.existing_decision_summary.decision_classification == cls
            and integ.existing_decision is dec
        )
        _check(f"{cls} preserved under supportive evidence", preserved)
    # Unavailable intelligence must also preserve the decision.
    dec_q = _decision(classification="QUALIFIED")
    integ_unavail = integ_eng.integrate(dec_q, None, profile_win)
    _check(
        "QUALIFIED preserved under unavailable intelligence",
        integ_unavail.existing_decision_summary.decision_classification == "QUALIFIED"
        and integ_unavail.integration_status == IntegrationStatus.UNAVAILABLE,
    )

    # 12. Complete 11V -> 12B -> 12C chain.
    _banner("12. Complete 11V -> 12B -> 12C chain")
    # The full chain over a realistic scenario matrix.
    chain_report = eng.validate(scenarios, label="e2e-12c-chain")
    print(
        f"  chain: overall={chain_report.overall_status.name} "
        f"scenarios={chain_report.scenario_count} "
        f"checks={chain_report.check_count}",
    )
    _check(
        "end-to-end chain validation passed",
        chain_report.overall_status == ValidationCheckStatus.PASS,
    )
    _check(
        "chain covers accounting + determinism + look-ahead + "
        "serialization + decision authority",
        chain_report.accounting_status == ValidationCheckStatus.PASS
        and chain_report.determinism_status == ValidationCheckStatus.PASS
        and chain_report.look_ahead_status == ValidationCheckStatus.PASS
        and chain_report.serialization_status == ValidationCheckStatus.PASS
        and chain_report.decision_authority_status == ValidationCheckStatus.PASS,
    )

    # Pipeline regression baseline.
    _banner("Pipeline regression baseline")
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    print(
        f"  Pipeline: signals_generated={result.signals_generated} "
        f"completed_trades={result.performance.completed_trades}",
    )
    _check(
        "Sprint 11V baseline (signals=4, trades=3)",
        result.signals_generated == 4
        and result.performance.completed_trades == 3,
    )

    # Print the full validation report.
    print()
    print(fmt.format(report))

    print()
    print(
        "Historical backtesting and robustness results are descriptive "
        "validation outputs. They do not predict future market behavior "
        "and do not guarantee profitability.",
    )

    failed = [label for label, ok in _CHECKS if not ok]
    print()
    if failed:
        print(f"Demo FAILED {len(failed)} check(s):")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"Sprint 12C demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
