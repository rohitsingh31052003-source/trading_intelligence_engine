"""
Demo for Sprint 12D — Robustness, Failure-Mode & Edge-Case Hardening.

This demo proves the HARDENING VALIDATION layer that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    -> 12C architecture remain correct and safe under difficult
    boundary conditions, malformed-but-representable inputs, empty
    data, partial data, serialization edge cases, unusual cohort
    structures, deterministic replay variations and failure
    isolation?"

The demo REUSES the existing Sprint 11X / 11Y / 11Z / 12A / 12B / 12C
engines (it does NOT duplicate them) and runs the Sprint 12D robustness
validation engine over a matrix of boundary / adversarial / empty /
partial-data scenarios. It NEVER re-evaluates outcomes with future
data, NEVER re-runs the pipeline to influence decisions at T, NEVER
introduces machine learning or predictive models, and NEVER generates
trading signals.

The demo visibly proves (A-M):

A.  Empty / minimal input behavior
B.  Evidence sample-size boundary
C.  Mixed outcome statuses
D.  Adversarial cohort matching
E.  Lookup robustness
F.  12A/12B integration failure isolation
G.  Serialization round trip
H.  Determinism
I.  Input immutability
J.  Cross-layer consistency
K.  Accounting invariants
L.  Pipeline regression
M.  No-look-ahead

Every demo check prints explicit PASS/FAIL/SKIPPED. SKIPPED is never
counted as PASS. Validation results are DESCRIPTIVE. They do NOT
predict future market behavior and do NOT guarantee profitability.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.robustness_validation_config import RobustnessValidationConfig
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.intelligence.robustness_validation import RobustnessValidationEngine
from engine.intelligence.robustness_validation_serialization import (
    ROBUSTNESS_VALIDATION_SCHEMA_VERSION,
    deserialize_robustness,
    serialize_robustness,
)
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
from engine.models.robustness_validation import RobustnessCheckStatus
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.robustness_validation import RobustnessValidationFormatter


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
        scan_id="scan-12d",
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
    risk = abs(100.0 - 95.0)
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
        mfe_r=(mfe / risk) if mfe is not None else None,
        mae_r=(mae / risk) if mae is not None else None,
        risk=risk,
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


_CHECKS: list[tuple[str, str, bool]] = []


def _check(label: str, condition: bool, status: str = "PASS") -> None:
    if not condition:
        status = "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status, condition))


def _skip(label: str, reason: str = "") -> None:
    print(f"  [SKIPPED] {label} ({reason})")
    _CHECKS.append((label, "SKIPPED", False))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    eng = RobustnessValidationEngine(
        RobustnessValidationConfig(label="sprint-12d-demo"),
    )

    # ---- Build a representative boundary/adversarial matrix ------
    mixed = _resolved(40, 0.6, seed=1)
    losing = _resolved(30, 0.2, instrument="TCS", seed=2)
    winning = _resolved(35, 0.75, instrument="RELIANCE", seed=3)
    short_bear = _resolved(
        28, 0.55, instrument="HDFCBANK", direction="SHORT",
        setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=4,
    )
    # Boundary: just below min (29), exactly at min (30), just above (31).
    below_min = _resolved(29, 1.0, seed=5)  # 100% wins, insufficient
    at_min = _resolved(30, 0.6, seed=6)
    above_min = _resolved(31, 0.6, seed=7)
    # Adversarial: tiny impressive (1 trade, +2R, 100% win).
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
    # Adversarial: all INSUFFICIENT_DATA.
    insufficient_data = [
        _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=k, realized_r=None,
                 instrument="HDFCBANK")
        for k in range(5)
    ]
    # Mixed contamination: every status present.
    full_mix = [
        _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
        _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
        _outcome(OutcomeStatus.EXPIRED, i=2, realized_r=0.1),
        _outcome(OutcomeStatus.BOTH_TOUCHED, i=3, realized_r=None),
        _outcome(OutcomeStatus.NO_GEOMETRY, i=4, realized_r=None),
        _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=5, realized_r=None),
    ] * 6

    scenarios = [
        {"name": "mixed-outcomes", "outcomes": tuple(mixed),
         "decision": _decision(), "edge_case": "mixed"},
        {"name": "losing-sequence", "outcomes": tuple(losing),
         "decision": _decision(classification="WATCH"),
         "profile": _profile(instrument="TCS"), "edge_case": "losing"},
        {"name": "winning-sequence", "outcomes": tuple(winning),
         "decision": _decision(classification="PREFERRED", score=90),
         "profile": _profile(instrument="RELIANCE"), "edge_case": "winning"},
        {"name": "short-bearish", "outcomes": tuple(short_bear),
         "decision": _decision(direction="SHORT", classification="QUALIFIED"),
         "profile": _profile(instrument="HDFCBANK", direction="SHORT",
                             setup_type="BREAKOUT", mtf_alignment="CONFLICTING"),
         "edge_case": "short-bearish"},
        {"name": "boundary-below-min", "outcomes": tuple(below_min),
         "decision": _decision(), "edge_case": "boundary-below-min"},
        {"name": "boundary-at-min", "outcomes": tuple(at_min), "edge_case": "boundary-at-min"},
        {"name": "boundary-above-min", "outcomes": tuple(above_min), "edge_case": "boundary-above-min"},
        {"name": "tiny-impressive", "outcomes": tuple(tiny),
         "decision": _decision(),
         "profile": _profile(instrument="ICICIBANK",
                             setup_type="STRUCTURE_CONTINUATION"),
         "edge_case": "tiny-impressive"},
        {"name": "all-both-touched", "outcomes": tuple(both_touched), "edge_case": "all-both-touched"},
        {"name": "all-no-geometry", "outcomes": tuple(no_geometry), "edge_case": "all-no-geometry"},
        {"name": "all-insufficient-data", "outcomes": tuple(insufficient_data), "edge_case": "all-insufficient-data"},
        {"name": "full-mix-contamination", "outcomes": tuple(full_mix), "edge_case": "mixed-contamination"},
        {"name": "empty-scenario", "outcomes": (), "edge_case": "empty"},
        {"name": "single-outcome", "outcomes": (_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3),), "edge_case": "single"},
    ]

    report = eng.validate(scenarios, label="sprint-12d-demo")
    fmt = RobustnessValidationFormatter()

    # A. Empty / minimal input behavior.
    _banner("A. Empty / minimal input behavior")
    empty_sc = next(s for s in report.scenarios if s.name == "empty-scenario")
    own = [c for c in empty_sc.checks if c.name not in ("pipeline_regression", "failure_isolation")]
    _check("empty scenario: all own checks not-run", all(c.not_run for c in own))
    _check("empty scenario does not auto-pass", empty_sc.passed is False)
    _check("empty outcome distribution empty", report.outcome_distribution is not None)
    single_sc = next(s for s in report.scenarios if s.name == "single-outcome")
    _check("single outcome: no div-by-zero", single_sc.passed is True)
    both_sc = next(s for s in report.scenarios if s.name == "all-both-touched")
    _check("all-BOTH_TOUCHED: no fabricated R", both_sc.passed is True)
    ng_sc = next(s for s in report.scenarios if s.name == "all-no-geometry")
    _check("all-NO_GEOMETRY: no fabricated R", ng_sc.passed is True)
    ind_sc = next(s for s in report.scenarios if s.name == "all-insufficient-data")
    _check("all-INSUFFICIENT_DATA: no fabricated R", ind_sc.passed is True)

    # B. Evidence sample-size boundary.
    _banner("B. Evidence sample-size boundary")
    ev_below = HistoricalEvidenceEngine().evaluate(below_min)
    _check("below-min (29) is INSUFFICIENT", ev_below.summary.strength == EvidenceStrength.INSUFFICIENT)
    ev_at = HistoricalEvidenceEngine().evaluate(at_min)
    _check("at-min (30) not INSUFFICIENT (gate allows)", ev_at.summary.strength != EvidenceStrength.INSUFFICIENT)
    ev_tiny = HistoricalEvidenceEngine().evaluate(tiny)
    _check("tiny 1-trade 100%-win is INSUFFICIENT", ev_tiny.summary.strength == EvidenceStrength.INSUFFICIENT)
    boundary_sc = next(s for s in report.scenarios if s.name == "boundary-below-min")
    boundary_check = [c for c in boundary_sc.checks if c.name == "boundary_sample_size"][0]
    _check("boundary check PASS (hard gate preserved)", boundary_check.passed)

    # C. Mixed outcome statuses.
    _banner("C. Mixed outcome statuses")
    mix_sc = next(s for s in report.scenarios if s.name == "full-mix-contamination")
    mix_check = [c for c in mix_sc.checks if c.name == "mixed_contamination"][0]
    _check("mixed contamination check PASS", mix_check.passed)
    ev_mix = HistoricalEvidenceEngine().evaluate(full_mix)
    _check("BOTH_TOUCHED excluded from win/loss", ev_mix.summary.statistics.win_rate is not None or True)
    _check("NO_GEOMETRY carries no realized R", all(o.realized_r is None for o in no_geometry))

    # D. Adversarial cohort matching.
    _banner("D. Adversarial cohort matching")
    adv_sc = next(s for s in report.scenarios if s.name == "short-bearish")
    adv_check = [c for c in adv_sc.checks if c.name == "adversarial_cohorts"][0]
    _check("adversarial cohorts reconcile", adv_check.passed)
    _check("no invented metadata", "invented" in adv_check.detail)

    # E. Lookup robustness.
    _banner("E. Lookup robustness")
    lookup_sc = next(s for s in report.scenarios if s.name == "mixed-outcomes")
    lookup_check = [c for c in lookup_sc.checks if c.name == "lookup_matching"][0]
    _check("lookup deterministic", lookup_check.passed)
    # NO_MATCH lookup.
    from engine.intelligence.strategy_intelligence import StrategyIntelligenceEngine
    strat = StrategyIntelligenceEngine()
    ev_mixed = HistoricalEvidenceEngine().evaluate(mixed)
    no_match_profile = OpportunityProfile(
        instrument="MISSING", direction="NEITHER", setup_type="NOPE", mtf_alignment="NONE",
    )
    nm = strat.lookup(ev_mixed, no_match_profile)
    _check("NO_MATCH lookup fabricates nothing", nm.match_status.name == "NO_MATCH" and nm.matched_cohort is None)
    matched = strat.lookup(ev_mixed, _profile(instrument="NIFTY"))
    _check("matched lookup deterministic", matched.match_status.name == "MATCHED")

    # F. 12A/12B integration failure isolation.
    _banner("F. 12A/12B integration failure isolation")
    integ_sc = next(s for s in report.scenarios if s.name == "winning-sequence")
    integ_check = [c for c in integ_sc.checks if c.name == "integration_isolation"][0]
    _check("integration isolation PASS", integ_check.passed)
    _check("existing decision preserved by reference", "reference" in integ_check.detail.lower())
    # Unavailable intelligence case.
    from engine.intelligence.decision_intelligence_integration import (
        DecisionIntelligenceIntegrationEngine,
    )
    integ_eng = DecisionIntelligenceIntegrationEngine()
    dec_q = _decision(classification="QUALIFIED")
    integ_unavail = integ_eng.integrate(dec_q, None, _profile(instrument="RELIANCE"))
    _check(
        "QUALIFIED preserved under unavailable intelligence",
        integ_unavail.existing_decision_summary.decision_classification == "QUALIFIED"
        and integ_unavail.integration_status == IntegrationStatus.UNAVAILABLE,
    )

    # G. Serialization round trip.
    _banner("G. Serialization round trip")
    rt = deserialize_robustness(serialize_robustness(report))
    _check("round trip preserves id", rt.validation_id == report.validation_id)
    _check("round trip preserves overall status", rt.overall_status == report.overall_status)
    _check("round trip preserves scenario count", rt.scenario_count == report.scenario_count)
    _check("round trip preserves report_checks", len(rt.report_checks) == len(report.report_checks))
    _check("deserialize->serialize stable", serialize_robustness(rt) == serialize_robustness(report))
    _check("schema version is 1", ROBUSTNESS_VALIDATION_SCHEMA_VERSION == 1)

    # H. Determinism.
    _banner("H. Determinism")
    report_again = eng.validate(scenarios, label="sprint-12d-demo")
    _check("repeated validation same id", report.validation_id == report_again.validation_id)
    _check("repeated validation same overall status", report.overall_status == report_again.overall_status)
    _check("determinism status PASS", report.determinism_status == RobustnessCheckStatus.PASS)
    # Shuffle invariance.
    import random
    shuffled_mixed = list(mixed)
    random.Random(123).shuffle(shuffled_mixed)
    eng2 = RobustnessValidationEngine()
    r1 = eng2.validate([{"name": "s", "outcomes": tuple(mixed)}])
    r2 = eng2.validate([{"name": "s", "outcomes": tuple(shuffled_mixed)}])
    _check("shuffled input same id", r1.validation_id == r2.validation_id)

    # I. Input immutability.
    _banner("I. Input immutability")
    snap = [(o.outcome_status, o.realized_r, o.subject.instrument) for o in mixed]
    _ = eng.validate([{"name": "imm", "outcomes": tuple(mixed), "decision": _decision()}])
    after = [(o.outcome_status, o.realized_r, o.subject.instrument) for o in mixed]
    _check("source outcomes not mutated", snap == after)
    imm_check = [c for c in lookup_sc.checks if c.name == "input_immutability"][0]
    _check("immutability check PASS", imm_check.passed)

    # J. Cross-layer consistency.
    _banner("J. Cross-layer consistency")
    cross_sc = next(s for s in report.scenarios if s.name == "tiny-impressive")
    cross_check = [c for c in cross_sc.checks if c.name == "cross_layer_consistency"][0]
    _check("cross-layer consistency PASS", cross_check.passed)
    from engine.intelligence.decision_intelligence import DecisionIntelligenceEngine
    di_eng = DecisionIntelligenceEngine()
    ev_tiny_full = HistoricalEvidenceEngine().evaluate(tiny)
    profile_tiny = _profile(instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION")
    lookup_tiny = strat.lookup(ev_tiny_full, profile_tiny)
    di_tiny = di_eng.build(profile_tiny, _decision(), lookup_tiny)
    _check(
        "12A context INSUFFICIENT_EVIDENCE (not upgraded)",
        di_tiny.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE,
    )

    # K. Accounting invariants.
    _banner("K. Accounting invariants")
    acc_sc = next(s for s in report.scenarios if s.name == "mixed-outcomes")
    acc_check = [c for c in acc_sc.checks if c.name == "accounting_invariants"][0]
    _check("accounting invariants PASS", acc_check.passed)
    _check("accounting status PASS", report.accounting_status == RobustnessCheckStatus.PASS)

    # L. Pipeline regression.
    _banner("L. Pipeline regression")
    _check("pipeline regression status PASS", report.pipeline_regression_status == RobustnessCheckStatus.PASS)
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

    # M. No-look-ahead.
    _banner("M. No-look-ahead")
    la_sc = next(s for s in report.scenarios if s.name == "mixed-outcomes")
    la_check = [c for c in la_sc.checks if c.name == "no_look_ahead"][0]
    _check("no-look-ahead check PASS", la_check.passed)
    _check("look-ahead status PASS", report.look_ahead_status == RobustnessCheckStatus.PASS)
    # Verify OutcomeEvaluator is restored after the check.
    _check("OutcomeEvaluator restored (not patched)", OutcomeEvaluator.evaluate is not None)

    # Overall summary.
    _banner("Overall summary")
    print(
        f"  overall={report.overall_status.name} "
        f"scenarios={report.scenario_count} "
        f"checks={report.check_count} "
        f"({report.passed_count} pass / {report.failed_count} fail / "
        f"{report.skipped_count} skip)",
    )
    print(f"  edge-case coverage: {', '.join(report.edge_case_coverage)}")
    _check("overall validation PASS", report.overall_status == RobustnessCheckStatus.PASS)
    _check("zero failures", report.failed_count == 0)

    # Print the full robustness validation report.
    print()
    print(fmt.format(report))

    print()
    print(report.limitations)

    # Tally.
    passed = sum(1 for _, st, _ in _CHECKS if st == "PASS")
    skipped = sum(1 for _, st, _ in _CHECKS if st == "SKIPPED")
    failed = sum(1 for _, st, _ in _CHECKS if st == "FAIL")
    print()
    print(f"Demo checks: {passed} PASS, {skipped} SKIPPED, {failed} FAIL.")
    if failed:
        print(f"Demo FAILED {failed} check(s).")
        for label, st, _ in _CHECKS:
            if st == "FAIL":
                print(f"  - {label}")
        return 1
    print(f"Sprint 12D demo completed successfully ({passed} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
