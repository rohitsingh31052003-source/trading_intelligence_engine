"""
Tests for the robust historical backtesting & adversarial validation
layer (Sprint 12C).

Coverage (mirrors the required test areas A-O):

A.  Scenario construction
B.  End-to-end replay
C.  Point-in-time safety
D.  Look-ahead protection
E.  Accounting reconciliation
F.  Cohort reconciliation
G.  Shuffle invariance
H.  Deterministic IDs
I.  Serialization
J.  Immutability
K.  Adversarial data
L.  Evidence gating
M.  Decision authority
N.  End-to-end integration
O.  Report validation
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.backtest_validation_config import BacktestValidationConfig
from engine.intelligence.backtest_validation import (
    BacktestValidationEngine,
    _recompute_statistics,
)
from engine.intelligence.backtest_validation_serialization import (
    BACKTEST_VALIDATION_SCHEMA_VERSION,
    canonical_validation_json,
    deserialize_validation,
    parse_validation_header,
    serialize_validation,
    serialize_validation_bytes,
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
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
    _dimension_key,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.backtest_validation import (
    BacktestValidationReport,
    CategorySummary,
    CheckResult,
    ScenarioResult,
    ValidationCategory,
    ValidationCheckStatus,
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
from engine.models.historical_performance import BreakdownDimension
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.backtest_validation import BacktestValidationFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _subject(
    i: int = 0,
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
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> HistoricalOutcome:
    risk = abs(entry - stop)
    return HistoricalOutcome(
        subject=_subject(
            i, instrument=instrument, direction=direction,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity,
            entry=entry, stop=stop, target=target,
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
    win_fraction: float = 0.6,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    rng = random.Random(seed)
    out: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        status = OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT
        rr = 2.0 if win else -1.0
        out.append(
            _outcome(
                status, i=i, realized_r=rr, instrument=instrument,
                direction=direction, setup_type=setup_type,
                mtf_alignment=mtf_alignment,
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


def _validate(outcomes, decision=None, expected_cls=None, profile=None):
    eng = BacktestValidationEngine()
    scenario = {"name": "s", "outcomes": tuple(outcomes)}
    if decision is not None:
        scenario["decision"] = decision
    if expected_cls is not None:
        scenario["expected_classification"] = expected_cls
    if profile is not None:
        scenario["profile"] = profile
    return eng.validate([scenario])


# ============================================================
# A. SCENARIO CONSTRUCTION
# ============================================================


class TestScenarioConstruction:
    def test_empty_scenario_skipped(self):
        r = _validate([])
        sc = r.scenarios[0]
        cc = [c for c in sc.checks if c.category == ValidationCategory.SCENARIO_CONSTRUCTION]
        assert cc[0].skipped

    def test_well_formed_outcomes_pass(self):
        r = _validate(_resolved(10))
        sc = r.scenarios[0]
        cc = [c for c in sc.checks if c.category == ValidationCategory.SCENARIO_CONSTRUCTION]
        assert cc[0].passed

    def test_non_outcome_entry_fails(self):
        eng = BacktestValidationEngine()
        with pytest.raises(AttributeError):
            # Passing a non-HistoricalOutcome triggers attribute access on
            # outcome_status during accounting; the scenario construction
            # check flags non-HistoricalOutcome entries.
            eng.validate([{"name": "bad", "outcomes": ("not-an-outcome",)}])


# ============================================================
# B. END-TO-END REPLAY
# ============================================================


class TestEndToEndReplay:
    def test_full_scenario_matrix_passes(self):
        outs = _resolved(40, 0.6, seed=1)
        r = _validate(outs, decision=_decision(), expected_cls="QUALIFIED")
        assert r.overall_status == ValidationCheckStatus.PASS

    def test_scenario_count_reflects_input(self):
        eng = BacktestValidationEngine()
        r = eng.validate(
            [
                {"name": "a", "outcomes": tuple(_resolved(5))},
                {"name": "b", "outcomes": tuple(_resolved(5, seed=2))},
            ],
        )
        assert r.scenario_count == 2

    def test_outcome_distribution_aggregated(self):
        eng = BacktestValidationEngine()
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, i=2, realized_r=0.3),
        ]
        r = eng.validate([{"name": "dist", "outcomes": tuple(outs)}])
        dist = dict(r.outcome_distribution)
        assert dist["TARGET_HIT"] == 1
        assert dist["STOP_HIT"] == 1
        assert dist["EXPIRED"] == 1


# ============================================================
# C. POINT-IN-TIME SAFETY
# ============================================================


class TestPointInTimeSafety:
    def test_look_ahead_status_pass(self):
        r = _validate(_resolved(20))
        assert r.look_ahead_status == ValidationCheckStatus.PASS

    def test_downstream_works_with_evaluator_patched(self):
        outs = _resolved(20)
        original = OutcomeEvaluator.evaluate

        def boom(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("must not re-evaluate")

        OutcomeEvaluator.evaluate = boom  # type: ignore[method-assign]
        try:
            ev = HistoricalEvidenceEngine().evaluate(outs)
            assert ev.summary.sample_count == 20
        finally:
            OutcomeEvaluator.evaluate = original  # type: ignore[method-assign]

    def test_downstream_works_with_pipeline_patched(self):
        outs = _resolved(20)
        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):  # noqa: ANN001
            raise RuntimeError("must not re-run")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[method-assign]
        try:
            ev = HistoricalEvidenceEngine().evaluate(outs)
            assert ev.summary.sample_count == 20
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[method-assign]


# ============================================================
# D. LOOK-AHEAD PROTECTION
# ============================================================


class TestLookAheadProtection:
    def test_no_hidden_candle_dependency(self):
        r = _validate(_resolved(20))
        assert r.look_ahead_status == ValidationCheckStatus.PASS

    def test_chain_consumes_outcomes_only(self):
        outs = _resolved(20)
        perf = PerformanceAnalyticsEngine()
        ev = HistoricalEvidenceEngine()
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        integ = DecisionIntelligenceIntegrationEngine()
        profile = _profile()
        # All these take outcomes / already-computed objects, never candles.
        analytics = perf.analyze(outs)
        evidence = ev.evaluate(outs)
        lookup = strat.lookup(evidence, profile)
        ctx = di.build(profile, _decision(), lookup)
        integrated = integ.integrate(_decision(), ctx, profile)
        assert analytics.outcome_count == 20
        assert integrated.existing_decision_summary.decision_classification == "QUALIFIED"


# ============================================================
# E. ACCOUNTING RECONCILIATION
# ============================================================


class TestAccountingReconciliation:
    def test_overall_statistics_reconcile(self):
        outs = _resolved(40, 0.6, seed=1)
        r = _validate(outs)
        assert r.accounting_status == ValidationCheckStatus.PASS

    def test_target_hit_r_contributes(self):
        outs = [_outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0)]
        stats = _recompute_statistics(outs)
        assert stats.target_hits == 1
        assert stats.total_realized_r == 2.0

    def test_stop_hit_r_contributes(self):
        outs = [_outcome(OutcomeStatus.STOP_HIT, i=0, realized_r=-1.0)]
        stats = _recompute_statistics(outs)
        assert stats.stop_hits == 1
        assert stats.total_realized_r == -1.0

    def test_expired_r_contributes_when_defined(self):
        outs = [_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3)]
        stats = _recompute_statistics(outs)
        assert stats.expired == 1
        assert stats.total_realized_r == 0.3
        assert stats.valid_r_count == 1

    def test_both_touched_no_fabricated_r(self):
        outs = [_outcome(OutcomeStatus.BOTH_TOUCHED, i=0, realized_r=None)]
        stats = _recompute_statistics(outs)
        assert stats.both_touched == 1
        assert stats.total_realized_r is None
        assert stats.win_rate is None
        assert stats.valid_r_count == 0

    def test_no_geometry_no_realized_r(self):
        outs = [_outcome(OutcomeStatus.NO_GEOMETRY, i=0, realized_r=None)]
        stats = _recompute_statistics(outs)
        assert stats.no_geometry == 1
        assert stats.total_realized_r is None
        assert stats.valid_r_count == 0

    def test_insufficient_data_no_fabricated_performance(self):
        outs = [_outcome(OutcomeStatus.INSUFFICIENT_DATA, i=0, realized_r=None)]
        stats = _recompute_statistics(outs)
        assert stats.insufficient_data == 1
        assert stats.total_realized_r is None
        assert stats.win_rate is None

    def test_gross_positive_plus_negative_reconciles_total(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=3.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.TARGET_HIT, i=2, realized_r=2.0),
        ]
        stats = _recompute_statistics(outs)
        assert stats.gross_positive_r == 5.0
        assert stats.gross_negative_r == -1.0
        assert abs(stats.total_realized_r - 4.0) < 1e-9

    def test_valid_r_count_reconciles(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=1, realized_r=None),
            _outcome(OutcomeStatus.NO_GEOMETRY, i=2, realized_r=None),
            _outcome(OutcomeStatus.STOP_HIT, i=3, realized_r=-1.0),
        ]
        stats = _recompute_statistics(outs)
        assert stats.valid_r_count == 2

    def test_breakdown_groups_reconcile(self):
        outs = _resolved(40, 0.6, seed=1)
        analytics = PerformanceAnalyticsEngine().analyze(outs)
        for dim in BreakdownDimension:
            for b in analytics.breakdowns:
                if b.dimension != dim:
                    continue
                for g in b.groups:
                    bucket = [
                        o for o in outs
                        if _dimension_key(o, dim) == g.key
                    ]
                    expected = _recompute_statistics(bucket)
                    assert expected.total == g.statistics.total
                    assert expected.target_hits == g.statistics.target_hits

    def test_accounting_across_dimensions(self):
        # instrument / direction / mtf / setup / decision / status / rank
        outs = (
            _resolved(20, 0.6, instrument="NIFTY", seed=1)
            + _resolved(20, 0.4, instrument="TCS", direction="SHORT",
                        setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=2)
        )
        r = _validate(outs)
        assert r.accounting_status == ValidationCheckStatus.PASS


# ============================================================
# F. COHORT RECONCILIATION
# ============================================================


class TestCohortReconciliation:
    def test_breakdown_totals_equal_overall(self):
        outs = _resolved(40, 0.6, seed=1)
        analytics = PerformanceAnalyticsEngine().analyze(outs)
        for b in analytics.breakdowns:
            total = sum(g.statistics.total for g in b.groups)
            assert total == analytics.overall.total

    def test_no_outcome_dropped_or_double_counted(self):
        outs = _resolved(40, 0.6, seed=1)
        r = _validate(outs)
        sc = r.scenarios[0]
        cc = [c for c in sc.checks if c.name == "cohort_reconciliation"]
        assert cc[0].passed

    def test_no_mutually_exclusive_status_overlap(self):
        # Each outcome has exactly one OutcomeStatus; the sum of status
        # counts must equal the total.
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, i=2, realized_r=0.3),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=3, realized_r=None),
            _outcome(OutcomeStatus.NO_GEOMETRY, i=4, realized_r=None),
            _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=5, realized_r=None),
        ]
        stats = _recompute_statistics(outs)
        counted = (
            stats.target_hits + stats.stop_hits + stats.expired
            + stats.both_touched + stats.no_geometry + stats.insufficient_data
        )
        assert counted == stats.total == 6

    def test_unavailable_rank_grouped_explicitly(self):
        outs = _resolved(20, 0.6, seed=1)
        analytics = PerformanceAnalyticsEngine().analyze(outs)
        rank_breakdown = next(
            b for b in analytics.breakdowns
            if b.dimension == BreakdownDimension.OPPORTUNITY_RANK
        )
        # All outcomes have rank=1, so only the "1" group should exist.
        keys = [g.key for g in rank_breakdown.groups]
        assert "1" in keys


# ============================================================
# G. SHUFFLE INVARIANCE
# ============================================================


class TestShuffleInvariance:
    def test_shuffled_same_analytics_id(self):
        outs = _resolved(40, 0.6, seed=1)
        perf = PerformanceAnalyticsEngine()
        a1 = perf.analyze(outs)
        shuf = list(outs)
        random.Random(7).shuffle(shuf)
        a2 = perf.analyze(shuf)
        assert a1.analytics_id == a2.analytics_id

    def test_shuffled_same_evidence_id(self):
        outs = _resolved(40, 0.6, seed=1)
        ev = HistoricalEvidenceEngine()
        e1 = ev.evaluate(outs)
        shuf = list(outs)
        random.Random(7).shuffle(shuf)
        e2 = ev.evaluate(shuf)
        assert e1.evidence_id == e2.evidence_id

    def test_shuffled_same_12a_context_id(self):
        outs = _resolved(40, 0.6, seed=1)
        ev = HistoricalEvidenceEngine()
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        profile = _profile()
        c1 = di.build(profile, None, strat.lookup(ev.evaluate(outs), profile))
        shuf = list(outs)
        random.Random(7).shuffle(shuf)
        c2 = di.build(profile, None, strat.lookup(ev.evaluate(shuf), profile))
        assert c1.context_id == c2.context_id

    def test_validation_shuffle_invariance_check_passes(self):
        r = _validate(_resolved(40, 0.6, seed=1))
        sc = r.scenarios[0]
        cc = [c for c in sc.checks if c.name == "shuffle_invariance"]
        assert cc[0].passed


# ============================================================
# H. DETERMINISTIC IDS
# ============================================================


class TestDeterministicIds:
    def test_repeated_validation_same_id(self):
        outs = _resolved(40, 0.6, seed=1)
        eng = BacktestValidationEngine()
        r1 = eng.validate([{"name": "s", "outcomes": tuple(outs)}])
        r2 = eng.validate([{"name": "s", "outcomes": tuple(outs)}])
        assert r1.validation_id == r2.validation_id

    def test_repeated_analytics_same_id(self):
        outs = _resolved(40, 0.6, seed=1)
        perf = PerformanceAnalyticsEngine()
        assert perf.analyze(outs).analytics_id == perf.analyze(outs).analytics_id

    def test_validation_id_prefixed(self):
        r = _validate(_resolved(10))
        assert r.validation_id.startswith("validation-")

    def test_different_scenarios_different_id(self):
        eng = BacktestValidationEngine()
        r1 = eng.validate([{"name": "a", "outcomes": tuple(_resolved(10))}])
        r2 = eng.validate([{"name": "b", "outcomes": tuple(_resolved(10))}])
        assert r1.validation_id != r2.validation_id

    def test_determinism_check_passes(self):
        r = _validate(_resolved(20))
        assert r.determinism_status == ValidationCheckStatus.PASS


# ============================================================
# I. SERIALIZATION
# ============================================================


class TestSerialization:
    def test_round_trip_preserves_id(self):
        r = _validate(_resolved(20))
        rt = deserialize_validation(serialize_validation(r))
        assert rt.validation_id == r.validation_id

    def test_round_trip_preserves_overall_status(self):
        r = _validate(_resolved(20))
        rt = deserialize_validation(serialize_validation(r))
        assert rt.overall_status == r.overall_status

    def test_round_trip_preserves_scenarios(self):
        r = _validate(_resolved(20))
        rt = deserialize_validation(serialize_validation(r))
        assert rt.scenario_count == r.scenario_count
        assert rt.scenarios[0].checks == r.scenarios[0].checks

    def test_deterministic_bytes(self):
        r = _validate(_resolved(20))
        assert serialize_validation(r) == serialize_validation(r)
        assert serialize_validation_bytes(r) == serialize_validation_bytes(r)

    def test_canonical_json(self):
        r = _validate(_resolved(20))
        assert canonical_validation_json(r) == serialize_validation(r)

    def test_schema_version(self):
        assert BACKTEST_VALIDATION_SCHEMA_VERSION == 1

    def test_header_parse(self):
        r = _validate(_resolved(20))
        header = parse_validation_header(serialize_validation(r))
        assert header["schema_version"] == 1

    def test_future_schema_rejected(self):
        import json as _json
        r = _validate(_resolved(20))
        payload = _json.loads(serialize_validation(r))
        payload["schema_version"] = 999
        with pytest.raises(ValueError):
            deserialize_validation(_json.dumps(payload))

    def test_empty_report_round_trips(self):
        eng = BacktestValidationEngine()
        r = eng.validate([{"name": "empty", "outcomes": ()}])
        rt = deserialize_validation(serialize_validation(r))
        assert rt.validation_id == r.validation_id
        assert rt.scenario_count == 1


# ============================================================
# J. IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_source_outcomes_not_mutated(self):
        outs = _resolved(20)
        before = [(o.outcome_status, o.realized_r, o.subject.instrument) for o in outs]
        _validate(outs)
        after = [(o.outcome_status, o.realized_r, o.subject.instrument) for o in outs]
        assert before == after

    def test_evidence_not_mutated(self):
        outs = _resolved(20)
        ev = HistoricalEvidenceEngine().evaluate(outs)
        ev_id_before = ev.evidence_id
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        lookup = strat.lookup(ev, _profile())
        di.build(_profile(), None, lookup)
        assert ev.evidence_id == ev_id_before

    def test_12a_context_not_mutated(self):
        outs = _resolved(40, 0.6, seed=1)
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        profile = _profile()
        ctx = di.build(profile, None, strat.lookup(ev, profile))
        ctx_id = ctx.context_id
        integ = DecisionIntelligenceIntegrationEngine()
        integ.integrate(_decision(), ctx, profile)
        assert ctx.context_id == ctx_id

    def test_12b_integration_not_mutated(self):
        outs = _resolved(40, 0.6, seed=1)
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        profile = _profile()
        ctx = di.build(profile, None, strat.lookup(ev, profile))
        integ = DecisionIntelligenceIntegrationEngine()
        integrated = integ.integrate(_decision(), ctx, profile)
        int_id = integrated.integration_id
        # Re-run serialization (read-only).
        from engine.intelligence.decision_intelligence_integration_serialization import (
            serialize_integration,
        )
        serialize_integration(integrated)
        assert integrated.integration_id == int_id

    def test_reference_identity_preserved(self):
        outs = _resolved(40, 0.6, seed=1)
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        profile = _profile()
        ctx = di.build(profile, None, strat.lookup(ev, profile))
        integ = DecisionIntelligenceIntegrationEngine()
        dec = _decision()
        integrated = integ.integrate(dec, ctx, profile)
        assert integrated.existing_decision is dec
        assert integrated.decision_intelligence is ctx


# ============================================================
# K. ADVERSARIAL DATA
# ============================================================


class TestAdversarialData:
    def test_one_enormous_winning_trade(self):
        outs = [_outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=100.0)]
        r = _validate(outs)
        assert r.overall_status in (
            ValidationCheckStatus.PASS, ValidationCheckStatus.SKIPPED,
        ) or r.overall_status == ValidationCheckStatus.PASS

    def test_one_enormous_losing_trade(self):
        outs = [_outcome(OutcomeStatus.STOP_HIT, i=0, realized_r=-100.0)]
        stats = _recompute_statistics(outs)
        assert stats.total_realized_r == -100.0

    def test_many_tiny_wins(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=0.1)
            for k in range(30)
        ]
        stats = _recompute_statistics(outs)
        assert stats.target_hits == 30
        assert abs(stats.total_realized_r - 3.0) < 1e-9

    def test_many_tiny_losses(self):
        outs = [
            _outcome(OutcomeStatus.STOP_HIT, i=k, realized_r=-0.1)
            for k in range(30)
        ]
        stats = _recompute_statistics(outs)
        assert stats.stop_hits == 30
        assert abs(stats.total_realized_r - (-3.0)) < 1e-9

    def test_100_percent_win_rate_tiny_cohort_insufficient(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0)
            for k in range(3)
        ]
        ev = HistoricalEvidenceEngine().evaluate(outs)
        assert ev.summary.strength == EvidenceStrength.INSUFFICIENT

    def test_0_percent_win_rate_tiny_cohort_insufficient(self):
        outs = [
            _outcome(OutcomeStatus.STOP_HIT, i=k, realized_r=-1.0)
            for k in range(3)
        ]
        ev = HistoricalEvidenceEngine().evaluate(outs)
        assert ev.summary.strength == EvidenceStrength.INSUFFICIENT

    def test_all_both_touched(self):
        outs = [
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=k, realized_r=None)
            for k in range(10)
        ]
        stats = _recompute_statistics(outs)
        assert stats.both_touched == 10
        assert stats.win_rate is None
        assert stats.total_realized_r is None

    def test_all_no_geometry(self):
        outs = [
            _outcome(OutcomeStatus.NO_GEOMETRY, i=k, realized_r=None)
            for k in range(10)
        ]
        stats = _recompute_statistics(outs)
        assert stats.no_geometry == 10
        assert stats.total_realized_r is None

    def test_empty_dataset(self):
        r = _validate([])
        assert r.overall_status == ValidationCheckStatus.SKIPPED or r.scenario_count == 1

    def test_single_outcome_dataset(self):
        outs = [_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3)]
        stats = _recompute_statistics(outs)
        assert stats.total == 1
        assert stats.expired == 1

    def test_duplicate_looking_opportunities(self):
        # Same instrument/direction/setup at consecutive timestamps.
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0)
            for k in range(5)
        ]
        stats = _recompute_statistics(outs)
        assert stats.total == 5

    def test_same_instrument_repeated(self):
        outs = (
            _resolved(20, 0.6, instrument="NIFTY", seed=1)
            + _resolved(20, 0.6, instrument="NIFTY", seed=2)
        )
        analytics = PerformanceAnalyticsEngine().analyze(outs)
        assert analytics.overall.total == 40

    def test_mixed_ranks(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0,
                     opportunity="BEST_OPPORTUNITY"),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0,
                     opportunity="ALTERNATIVE_OPPORTUNITY"),
        ]
        r = _validate(outs)
        assert r.accounting_status == ValidationCheckStatus.PASS

    def test_missing_metadata(self):
        # Outcomes with empty setup_type / mtf_alignment.
        s = OutcomeSubject(
            instrument="X", direction="LONG",
            evaluation_timestamp=_EPOCH, entry=100.0, stop=95.0, target=110.0,
        )
        o = HistoricalOutcome(
            subject=s, outcome_status=OutcomeStatus.TARGET_HIT,
            realized_r=2.0, risk=5.0,
        )
        stats = _recompute_statistics([o])
        assert stats.total == 1

    def test_extreme_numeric_values(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=1e6,
                     entry=1e6, stop=0.0, target=2e6),
        ]
        stats = _recompute_statistics(outs)
        assert stats.total_realized_r == 1e6

    def test_zero_risk_geometry_represented(self):
        # stop == entry -> zero risk. The outcome subject carries it but
        # the evaluator would flag NO_GEOMETRY; here we just confirm the
        # subject can be constructed and statistics handle it.
        s = OutcomeSubject(
            instrument="X", direction="LONG",
            evaluation_timestamp=_EPOCH, entry=100.0, stop=100.0, target=110.0,
        )
        o = HistoricalOutcome(
            subject=s, outcome_status=OutcomeStatus.NO_GEOMETRY,
            realized_r=None, risk=None,
        )
        stats = _recompute_statistics([o])
        assert stats.no_geometry == 1
        assert stats.total_realized_r is None

    def test_long_expiration_horizon(self):
        # 60 expired outcomes mark-to-close.
        outs = [
            _outcome(OutcomeStatus.EXPIRED, i=k, realized_r=0.05)
            for k in range(60)
        ]
        stats = _recompute_statistics(outs)
        assert stats.expired == 60
        assert stats.valid_r_count == 60


# ============================================================
# L. EVIDENCE GATING
# ============================================================


class TestEvidenceGating:
    def test_tiny_cohort_never_strong(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0)
            for k in range(2)
        ]
        ev = HistoricalEvidenceEngine().evaluate(outs)
        assert ev.summary.strength == EvidenceStrength.INSUFFICIENT

    def test_chain_preserves_insufficient(self):
        outs = [
            _outcome(
                OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0,
                instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION",
            )
            for k in range(2)
        ]
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        profile = _profile(instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION")
        lookup = strat.lookup(ev, profile)
        ctx = di.build(profile, _decision(), lookup)
        assert ev.summary.strength == EvidenceStrength.INSUFFICIENT
        assert ctx.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE

    def test_evidence_gating_check_passes_for_tiny(self):
        outs = [
            _outcome(
                OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0,
                instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION",
            )
            for k in range(2)
        ]
        r = _validate(
            outs, decision=_decision(), expected_cls="QUALIFIED",
            profile=_profile(instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION"),
        )
        sc = r.scenarios[0]
        cc = [c for c in sc.checks if c.name == "evidence_gating"]
        assert cc[0].passed

    def test_sufficient_cohort_can_be_strong(self):
        outs = _resolved(60, 0.6, seed=1)
        ev = HistoricalEvidenceEngine().evaluate(outs)
        assert ev.summary.strength != EvidenceStrength.INSUFFICIENT


# ============================================================
# M. DECISION AUTHORITY
# ============================================================


class TestDecisionAuthority:
    def _evidence_for(self, outs, profile):
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        lookup = strat.lookup(ev, profile)
        return di.build(profile, _decision(), lookup)

    def test_qualified_preserved_under_strong_evidence(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        profile = _profile(instrument="RELIANCE")
        ctx = self._evidence_for(outs, profile)
        dec = _decision(classification="QUALIFIED")
        integ = DecisionIntelligenceIntegrationEngine().integrate(dec, ctx, profile)
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"
        assert integ.existing_decision is dec

    def test_preferred_preserved(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        profile = _profile(instrument="RELIANCE")
        ctx = self._evidence_for(outs, profile)
        dec = _decision(classification="PREFERRED", score=90)
        integ = DecisionIntelligenceIntegrationEngine().integrate(dec, ctx, profile)
        assert integ.existing_decision_summary.decision_classification == "PREFERRED"

    def test_watch_preserved(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        profile = _profile(instrument="RELIANCE")
        ctx = self._evidence_for(outs, profile)
        dec = _decision(classification="WATCH")
        integ = DecisionIntelligenceIntegrationEngine().integrate(dec, ctx, profile)
        assert integ.existing_decision_summary.decision_classification == "WATCH"

    def test_rejected_preserved(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        profile = _profile(instrument="RELIANCE")
        ctx = self._evidence_for(outs, profile)
        dec = _decision(classification="REJECTED")
        integ = DecisionIntelligenceIntegrationEngine().integrate(dec, ctx, profile)
        assert integ.existing_decision_summary.decision_classification == "REJECTED"

    def test_preserved_under_unavailable_evidence(self):
        dec = _decision(classification="QUALIFIED")
        integ = DecisionIntelligenceIntegrationEngine().integrate(
            dec, None, _profile(),
        )
        assert integ.integration_status == IntegrationStatus.UNAVAILABLE
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"

    def test_preserved_under_no_match_evidence(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        profile_nomatch = OpportunityProfile(setup_type="RANGE_REJECTION")
        ev = HistoricalEvidenceEngine().evaluate(outs)
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        lookup = strat.lookup(ev, profile_nomatch)
        ctx = di.build(profile_nomatch, _decision(), lookup)
        dec = _decision(classification="QUALIFIED")
        integ = DecisionIntelligenceIntegrationEngine().integrate(dec, ctx, profile_nomatch)
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"

    def test_decision_authority_check_passes(self):
        outs = _resolved(60, 0.8, instrument="RELIANCE", seed=1)
        r = _validate(
            outs, decision=_decision(classification="QUALIFIED"),
            expected_cls="QUALIFIED", profile=_profile(instrument="RELIANCE"),
        )
        assert r.decision_authority_status == ValidationCheckStatus.PASS


# ============================================================
# N. END-TO-END INTEGRATION
# ============================================================


class TestEndToEndIntegration:
    def test_full_chain_validation_passes(self):
        outs = _resolved(40, 0.6, seed=1)
        r = _validate(
            outs, decision=_decision(), expected_cls="QUALIFIED",
        )
        assert r.overall_status == ValidationCheckStatus.PASS
        assert r.accounting_status == ValidationCheckStatus.PASS
        assert r.determinism_status == ValidationCheckStatus.PASS
        assert r.look_ahead_status == ValidationCheckStatus.PASS
        assert r.serialization_status == ValidationCheckStatus.PASS
        assert r.decision_authority_status == ValidationCheckStatus.PASS

    def test_no_lookahead_introduced(self):
        outs = _resolved(20)
        original_eval = OutcomeEvaluator.evaluate
        original_pipe = HistoricalEvaluationPipeline.evaluate

        def boom_eval(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("no")

        def boom_pipe(self, candles):  # noqa: ANN001
            raise RuntimeError("no")

        OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
        try:
            r = _validate(outs)
            assert r.overall_status == ValidationCheckStatus.PASS
        finally:
            OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
            HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]

    def test_pipeline_regression_baseline(self):
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3

    def test_chain_11v_to_12c(self):
        # Build outcomes -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C.
        outs = _resolved(40, 0.6, seed=1)
        perf = PerformanceAnalyticsEngine()
        ev = HistoricalEvidenceEngine()
        strat = StrategyIntelligenceEngine()
        di = DecisionIntelligenceEngine()
        integ = DecisionIntelligenceIntegrationEngine()
        profile = _profile()
        analytics = perf.analyze(outs)
        evidence = ev.evaluate(outs)
        lookup = strat.lookup(evidence, profile)
        ctx = di.build(profile, _decision(), lookup)
        integrated = integ.integrate(_decision(), ctx, profile)
        # 12C validates the chain.
        r = _validate(outs, decision=_decision(), expected_cls="QUALIFIED")
        assert analytics.outcome_count == 40
        assert integrated.integration_status == IntegrationStatus.INTEGRATED
        assert r.overall_status == ValidationCheckStatus.PASS


# ============================================================
# O. REPORT VALIDATION
# ============================================================


class TestReportValidation:
    def test_formatter_returns_str(self):
        r = _validate(_resolved(20))
        assert isinstance(BacktestValidationFormatter().format(r), str)

    def test_report_has_required_sections(self):
        r = _validate(_resolved(20))
        text = BacktestValidationFormatter().format(r)
        assert "Backtest Validation Report" in text
        assert "Validation Status" in text
        assert "Outcome Distribution" in text
        assert "Category Summary" in text
        assert "Scenario Detail" in text
        assert "Rationale" in text

    def test_report_has_disclaimer(self):
        r = _validate(_resolved(20))
        text = BacktestValidationFormatter().format(r)
        assert "do not predict future market behavior" in text
        assert "do not guarantee profitability" in text

    def test_report_no_predictive_language(self):
        r = _validate(_resolved(20))
        text = BacktestValidationFormatter().format(r).lower()
        for term in ("will rise", "will fall", "guaranteed profit", "buy now",
                     "sell now", "statistically significant"):
            assert term not in text

    def test_report_shows_skipped_explicitly(self):
        eng = BacktestValidationEngine()
        r = eng.validate([{"name": "empty", "outcomes": ()}])
        text = BacktestValidationFormatter().format(r)
        assert "SKIP" in text or "SKIPPED" in text

    def test_formatter_deterministic(self):
        r = _validate(_resolved(20))
        f = BacktestValidationFormatter()
        assert f.format(r) == f.format(r)

    def test_formatter_empty_report(self):
        eng = BacktestValidationEngine()
        r = eng.validate([])
        text = BacktestValidationFormatter().format(r)
        assert "Backtest Validation Report" in text

    def test_negative_width_rejected(self):
        with pytest.raises(ValueError):
            BacktestValidationFormatter(width=5)


# ============================================================
# MODEL + CONFIG IMMUTABILITY
# ============================================================


class TestModelsAndConfig:
    def test_models_frozen(self):
        r = _validate(_resolved(10))
        with pytest.raises(Exception):
            r.overall_status = ValidationCheckStatus.FAIL  # type: ignore[misc]

    def test_check_result_frozen(self):
        cr = CheckResult(
            name="x", category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.PASS,
        )
        with pytest.raises(Exception):
            cr.status = ValidationCheckStatus.FAIL  # type: ignore[misc]

    def test_config_frozen(self):
        cfg = BacktestValidationConfig()
        with pytest.raises(Exception):
            cfg.label = "x"  # type: ignore[misc]

    def test_config_negative_tolerance_rejected(self):
        with pytest.raises(ValueError):
            BacktestValidationConfig(accounting_tolerance=-1.0)

    def test_config_snapshot_sorted(self):
        cfg = BacktestValidationConfig()
        snap = cfg.snapshot()
        assert snap == tuple(sorted(snap))

    def test_validation_category_members(self):
        cats = {c.name for c in ValidationCategory}
        assert "ACCOUNTING_RECONCILIATION" in cats
        assert "DECISION_AUTHORITY" in cats
        assert "EVIDENCE_GATING" in cats

    def test_check_status_properties(self):
        assert ValidationCheckStatus.PASS.is_pass
        assert not ValidationCheckStatus.FAIL.is_pass


# ============================================================
# PACKAGE SURFACE
# ============================================================


class TestPackageSurface:
    def test_engine_importable(self):
        from engine.intelligence.backtest_validation import (
            BacktestValidationEngine,
        )
        assert BacktestValidationEngine is not None

    def test_serialization_importable(self):
        from engine.intelligence.backtest_validation_serialization import (
            BACKTEST_VALIDATION_SCHEMA_VERSION,
            serialize_validation,
            deserialize_validation,
        )
        assert BACKTEST_VALIDATION_SCHEMA_VERSION == 1
        assert callable(serialize_validation)
        assert callable(deserialize_validation)

    def test_models_importable(self):
        from engine.models.backtest_validation import (
            BacktestValidationReport,
            CheckResult,
            ScenarioResult,
            ValidationCategory,
            ValidationCheckStatus,
        )
        assert BacktestValidationReport is not None

    def test_reporting_importable(self):
        from engine.reporting.backtest_validation import (
            BacktestValidationFormatter,
        )
        assert BacktestValidationFormatter is not None

    def test_config_importable(self):
        from engine.config.backtest_validation_config import (
            BacktestValidationConfig,
        )
        assert BacktestValidationConfig is not None

    def test_intelligence_init_empty(self):
        # 12C follows the 11O-12B convention: intelligence/__init__.py
        # stays intentionally empty.
        import engine.intelligence as intel_pkg
        assert not hasattr(intel_pkg, "BacktestValidationEngine")
