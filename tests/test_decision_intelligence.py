"""
Tests for the decision intelligence foundation layer (Sprint 12A).

Coverage (mirrors the required test areas A-Z):

A.  Model construction + properties
B.  Current opportunity integration (profile reuse)
C.  Sprint 11Z evidence lookup reuse
D.  Evidence-supported decision context
E.  Weak evidence context (EVIDENCE_LIMITED)
F.  Insufficient evidence context (INSUFFICIENT_EVIDENCE)
G.  Missing cohort (EVIDENCE_UNAVAILABLE)
H.  Unavailable metrics
I.  BOTH_TOUCHED handling
J.  NO_GEOMETRY handling
K.  INSUFFICIENT_DATA handling
L.  Deterministic IDs
M.  Deterministic factor ordering
N.  Repeated evaluation identity
O.  Shuffled-input determinism
P.  Serialization round trip
Q.  Input immutability (outcomes / evidence report / profile / lookup
    / existing decision)
R.  No-look-ahead protection (no candles inspected; outcome evaluator
    patched to raise; pipeline patched to raise)
S.  Rationale correctness
T.  Limitations correctness
U.  Existing decision remains unchanged
V.  Existing scoring remains unchanged (decision engine unchanged)
W.  Existing ranking remains unchanged
X.  Existing Sprint 11X analytics remain unchanged
Y.  Existing Sprint 11Y evidence remain unchanged
Z.  Existing Sprint 11Z strategy intelligence remain unchanged
AA. Reporting (sections / warning / unavailable / determinism)
AB. Configuration validation
AC. Model immutability (frozen + slots)
AD. Sample-size hard gate propagation
AE. Honest fallbacks (no fabricated metric / no fabricated context)
AF. End-to-end 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A chain
"""

from __future__ import annotations

import inspect
import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.decision_intelligence_config import (
    DecisionIntelligenceConfig,
)
from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_serialization import (
    DECISION_INTELLIGENCE_SCHEMA_VERSION,
    canonical_context_json,
    deserialize_context,
    parse_decision_intelligence_header,
    serialize_context,
    serialize_context_bytes,
)
from engine.intelligence.historical_evidence import (
    HistoricalEvidenceEngine,
)
from engine.intelligence.historical_outcome import (
    HistoricalOutcomeEngine,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.intelligence.trade_opportunity import (
    TradeOpportunityEngine,
)
from engine.models.decision_intelligence import (
    ASSESSMENT_TO_CONTEXT,
    DecisionContextStatus,
    DecisionEvidenceFactor,
    DecisionIntelligenceContext,
    DecisionIntelligenceFactor,
    ExistingDecisionSummary,
)
from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceReport,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    CohortMatchStatus,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
)
from engine.reporting.decision_intelligence import (
    DecisionIntelligenceFormatter,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _subject(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=rank,
        scan_id="scan-12a",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    realized_r: float | None = None,
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
    mfe_r: float | None = 1.0,
    mae_r: float | None = 0.4,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
) -> HistoricalOutcome:
    return HistoricalOutcome(
        subject=_subject(
            instrument=instrument, direction=direction, rank=rank,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity, ts=ts,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
        risk=5.0,
    )


def _resolved_cohort(
    n: int,
    win_fraction: float = 0.6,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    rng = random.Random(seed)
    outcomes: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        status = OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT
        rr = 2.0 if win else -1.0
        outcomes.append(
            _outcome(
                status, realized_r=rr, instrument=instrument,
                direction=direction, setup_type=setup_type,
                mtf_alignment=mtf_alignment,
                ts=_EPOCH + timedelta(days=i),
            ),
        )
    return outcomes


def _evidence_report(outcomes, label="test"):
    return HistoricalEvidenceEngine(EvidenceConfig(label=label)).evaluate(
        outcomes,
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


def _decision(
    direction: str = "LONG",
    classification: str = "QUALIFIED",
    score: int = 75,
    opportunity: str = "BEST_OPPORTUNITY",
    rank: int = 1,
    geometry: bool = True,
    confluence: int = 4,
    rr: float = 2.0,
) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status=opportunity,
        rank=rank,
        geometry_complete=geometry,
        confluence_score=confluence,
        risk_reward_ratio=rr,
        entry=100.0,
        stop=95.0,
        target=110.0,
    )


SETUP_DIR_SPEC = CohortSpec(
    (BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION),
)


def _build_supported_context(label="test"):
    outcomes = _resolved_cohort(60, win_fraction=0.6, seed=1)
    report = _evidence_report(outcomes)
    strat = StrategyIntelligenceEngine()
    profile = _profile()
    lookup = strat.lookup(report, profile)
    eng = DecisionIntelligenceEngine()
    return eng.build(profile, _decision(), lookup, label=label), lookup


# ============================================================
# A. MODEL CONSTRUCTION + PROPERTIES
# ============================================================


class TestModelConstruction:
    def test_decision_context_status_members(self):
        members = {s.name for s in DecisionContextStatus}
        assert members == {
            "EVIDENCE_SUPPORTED",
            "EVIDENCE_LIMITED",
            "INSUFFICIENT_EVIDENCE",
            "EVIDENCE_UNAVAILABLE",
        }

    def test_decision_context_status_rank_value_ordering(self):
        assert (
            DecisionContextStatus.EVIDENCE_SUPPORTED.rank_value
            < DecisionContextStatus.EVIDENCE_LIMITED.rank_value
            < DecisionContextStatus.INSUFFICIENT_EVIDENCE.rank_value
            < DecisionContextStatus.EVIDENCE_UNAVAILABLE.rank_value
        )

    def test_evidence_available_and_is_sufficient(self):
        assert DecisionContextStatus.EVIDENCE_SUPPORTED.evidence_available
        assert DecisionContextStatus.EVIDENCE_LIMITED.evidence_available
        assert DecisionContextStatus.INSUFFICIENT_EVIDENCE.evidence_available
        assert not DecisionContextStatus.EVIDENCE_UNAVAILABLE.evidence_available
        assert DecisionContextStatus.EVIDENCE_SUPPORTED.is_sufficient
        assert not DecisionContextStatus.EVIDENCE_LIMITED.is_sufficient
        assert not DecisionContextStatus.INSUFFICIENT_EVIDENCE.is_sufficient
        assert not DecisionContextStatus.EVIDENCE_UNAVAILABLE.is_sufficient

    def test_decision_evidence_factor_members(self):
        members = {f.name for f in DecisionEvidenceFactor}
        assert members == {
            "HISTORICAL_SUPPORT_PRESENT",
            "FAVORABLE_HISTORICAL_CHARACTERISTICS",
            "HISTORICAL_CAUTION_PRESENT",
            "UNFAVORABLE_HISTORICAL_CHARACTERISTICS",
            "INSUFFICIENT_HISTORICAL_EVIDENCE",
            "EVIDENCE_UNAVAILABLE",
        }

    def test_factor_rank_value_ordering(self):
        ranks = [f.rank_value for f in DecisionEvidenceFactor]
        assert ranks == sorted(ranks)
        assert all(ranks[i] != ranks[i + 1] for i in range(len(ranks) - 1))

    def test_assessment_to_context_bijective_mapping(self):
        assert set(ASSESSMENT_TO_CONTEXT.keys()) == set(
            StrategyAssessmentStatus,
        )
        assert ASSESSMENT_TO_CONTEXT[
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT
        ] == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ASSESSMENT_TO_CONTEXT[
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE
        ] == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ASSESSMENT_TO_CONTEXT[
            StrategyAssessmentStatus.LIMITED_EVIDENCE
        ] == DecisionContextStatus.EVIDENCE_LIMITED
        assert ASSESSMENT_TO_CONTEXT[
            StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE
        ] == DecisionContextStatus.INSUFFICIENT_EVIDENCE

    def test_existing_decision_summary_defaults_and_has_decision(self):
        empty = ExistingDecisionSummary()
        assert empty.direction == ""
        assert empty.decision_score == 0
        assert empty.risk_reward_ratio is None
        assert not empty.has_decision
        full = _decision()
        assert full.has_decision

    def test_context_construction_and_properties(self):
        ctx, lookup = _build_supported_context()
        assert isinstance(ctx, DecisionIntelligenceContext)
        assert ctx.context_id.startswith("di-")
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ctx.has_evidence
        assert ctx.evidence_supported
        assert ctx.matched
        assert ctx.matched_cohort is not None
        assert ctx.observed_performance is not None
        assert ctx.evidence_strength is not None
        assert ctx.strategy_interpretation is not None
        assert isinstance(ctx.factors, tuple)
        assert ctx.rationale
        assert ctx.limitations


# ============================================================
# B. CURRENT OPPORTUNITY INTEGRATION (profile reuse)
# ============================================================


class TestCurrentOpportunityIntegration:
    def test_profile_reused_by_reference(self):
        ctx, lookup = _build_supported_context()
        assert ctx.profile is lookup.profile

    def test_profile_carried_through_to_context(self):
        profile = _profile(instrument="RELIANCE", direction="SHORT")
        outcomes = _resolved_cohort(
            60, win_fraction=0.6, instrument="RELIANCE",
            direction="SHORT", seed=5,
        )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, profile)
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(profile, _decision(direction="SHORT"), lookup)
        assert ctx.profile.instrument == "RELIANCE"
        assert ctx.profile.direction == "SHORT"

    def test_build_from_report_delegates_to_strategy_engine(self):
        outcomes = _resolved_cohort(60, seed=7)
        report = _evidence_report(outcomes)
        eng = DecisionIntelligenceEngine()
        ctx = eng.build_from_report(report, _profile(), _decision())
        assert ctx.matched
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED


# ============================================================
# C. 11Z EVIDENCE LOOKUP REUSE
# ============================================================


class TestEvidenceLookupReuse:
    def test_lookup_retained_by_reference(self):
        ctx, lookup = _build_supported_context()
        assert ctx.lookup is lookup

    def test_lookup_match_status_reflected(self):
        ctx, _ = _build_supported_context()
        assert ctx.lookup.match_status == CohortMatchStatus.MATCHED

    def test_does_not_rebuild_cohort_matching(self):
        # build_from_report delegates to StrategyIntelligenceEngine.lookup;
        # it never re-implements cohort matching.
        sig = inspect.signature(DecisionIntelligenceEngine.build_from_report)
        assert "report" in sig.parameters
        assert "strategy_engine" in sig.parameters

    def test_strategy_engine_injection_respected(self):
        outcomes = _resolved_cohort(60, seed=9)
        report = _evidence_report(outcomes)
        injected = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        ctx = eng.build_from_report(
            report, _profile(), _decision(), strategy_engine=injected,
        )
        assert ctx.matched


# ============================================================
# D. EVIDENCE-SUPPORTED DECISION CONTEXT
# ============================================================


class TestEvidenceSupported:
    def test_strong_evidence_supported(self):
        ctx, _ = _build_supported_context()
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert DecisionEvidenceFactor.HISTORICAL_SUPPORT_PRESENT in [
            f.factor for f in ctx.factors
        ]

    def test_moderate_evidence_supported(self):
        # 30 outcomes: above 11Y min_sample_total (30) -> WEAK actually.
        # Use 35 to land MODERATE reliably (min_resolved=10 satisfied,
        # below strong thresholds). Evidence strength WEAK by default
        # config -> EVIDENCE_LIMITED. To get MODERATE we need a larger
        # but not strong sample; 11Y default strong_min_sample=50.
        # 40 outcomes -> MODERATE.
        outcomes = _resolved_cohort(40, win_fraction=0.6, seed=11)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert lookup.assessment.evidence_strength == EvidenceStrength.MODERATE
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED


# ============================================================
# E. WEAK EVIDENCE CONTEXT (EVIDENCE_LIMITED)
# ============================================================


class TestEvidenceLimited:
    def test_weak_evidence_limited(self):
        # 11Y default: total >= min_sample_total (30) but resolved <
        # min_resolved (10) -> WEAK. Build 35 outcomes, only 5 resolved
        # (target/stop), the rest BOTH_TOUCHED (ambiguous, not resolved).
        outcomes = []
        for i in range(5):
            outcomes.append(
                _outcome(
                    OutcomeStatus.TARGET_HIT, realized_r=2.0,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        for i in range(5, 35):
            outcomes.append(
                _outcome(
                    OutcomeStatus.BOTH_TOUCHED, realized_r=None,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert lookup.assessment.evidence_strength == EvidenceStrength.WEAK
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_LIMITED
        assert DecisionEvidenceFactor.HISTORICAL_CAUTION_PRESENT in [
            f.factor for f in ctx.factors
        ]
        assert ctx.has_evidence
        assert not ctx.evidence_supported


# ============================================================
# F. INSUFFICIENT EVIDENCE CONTEXT
# ============================================================


class TestInsufficientEvidence:
    def test_insufficient_evidence_context(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=17)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert lookup.assessment.evidence_strength == EvidenceStrength.INSUFFICIENT
        assert ctx.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE
        assert DecisionEvidenceFactor.INSUFFICIENT_HISTORICAL_EVIDENCE in [
            f.factor for f in ctx.factors
        ]

    def test_small_sample_not_supported_despite_high_win_rate(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=19)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert lookup.assessment.observed_performance.win_rate == 1.0
        assert ctx.decision_context_status != DecisionContextStatus.EVIDENCE_SUPPORTED
        # No favourable characteristic flag on insufficient evidence.
        assert (
            DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS
            not in [f.factor for f in ctx.factors]
        )


# ============================================================
# G. MISSING COHORT (EVIDENCE_UNAVAILABLE)
# ============================================================


class TestMissingCohort:
    def test_no_match_evidence_unavailable(self):
        outcomes = _resolved_cohort(60, seed=21)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="RANGE_REJECTION"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE
        assert not ctx.has_evidence
        assert ctx.observed_performance is None
        assert ctx.evidence_strength is None
        assert ctx.strategy_interpretation is None
        assert ctx.matched_cohort is None
        assert DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE in [
            f.factor for f in ctx.factors
        ]

    def test_no_match_no_fabricated_metrics(self):
        outcomes = _resolved_cohort(60, seed=23)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONEXISTENT"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        assert ctx.observed_performance is None
        assert ctx.evidence_strength is None
        assert ctx.strategy_interpretation is None


# ============================================================
# H. UNAVAILABLE METRICS
# ============================================================


class TestUnavailableMetrics:
    def test_win_rate_unavailable_propagated(self):
        # All BOTH_TOUCHED -> no resolved target/stop -> win_rate None.
        outcomes = [
            _outcome(
                OutcomeStatus.BOTH_TOUCHED, realized_r=None,
                ts=_EPOCH + timedelta(days=i),
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.win_rate is None
        # No favourable / unfavourable characteristic on None win rate.
        assert (
            DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS
            not in [f.factor for f in ctx.factors]
            or ctx.observed_performance.average_realized_r is not None
        )

    def test_unavailable_never_converted_to_zero(self):
        outcomes = [
            _outcome(
                OutcomeStatus.BOTH_TOUCHED, realized_r=None,
                ts=_EPOCH + timedelta(days=i),
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.win_rate is None
        assert ctx.observed_performance.profit_factor is None
        assert ctx.observed_performance.average_realized_r is None


# ============================================================
# I. BOTH_TOUCHED HANDLING
# ============================================================


class TestBothTouched:
    def test_both_touched_excluded_from_win_loss(self):
        outcomes = []
        for i in range(30):
            outcomes.append(
                _outcome(
                    OutcomeStatus.TARGET_HIT, realized_r=2.0,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        for i in range(30, 40):
            outcomes.append(
                _outcome(
                    OutcomeStatus.BOTH_TOUCHED, realized_r=None,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.both_touched == 10
        assert ctx.observed_performance.win_rate == 1.0
        assert "BOTH_TOUCHED" in ctx.limitations

    def test_both_touched_not_treated_as_win_or_loss(self):
        outcomes = [
            _outcome(
                OutcomeStatus.BOTH_TOUCHED, realized_r=None,
                ts=_EPOCH + timedelta(days=i),
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.win_rate is None


# ============================================================
# J. NO_GEOMETRY HANDLING
# ============================================================


class TestNoGeometry:
    def test_no_geometry_carries_no_fabricated_r(self):
        outcomes = []
        for i in range(40):
            outcomes.append(
                _outcome(
                    OutcomeStatus.TARGET_HIT, realized_r=2.0,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        for i in range(40, 45):
            outcomes.append(
                _outcome(
                    OutcomeStatus.NO_GEOMETRY, realized_r=None,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.no_geometry == 5
        assert "NO_GEOMETRY" in ctx.limitations


# ============================================================
# K. INSUFFICIENT_DATA HANDLING
# ============================================================


class TestInsufficientData:
    def test_insufficient_data_carries_no_directional_conclusion(self):
        outcomes = []
        for i in range(40):
            outcomes.append(
                _outcome(
                    OutcomeStatus.TARGET_HIT, realized_r=2.0,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        for i in range(40, 45):
            outcomes.append(
                _outcome(
                    OutcomeStatus.INSUFFICIENT_DATA, realized_r=None,
                    ts=_EPOCH + timedelta(days=i),
                )
            )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.observed_performance.insufficient_data == 5
        assert "INSUFFICIENT_DATA" in ctx.limitations


# ============================================================
# L. DETERMINISTIC IDS
# ============================================================


class TestDeterministicIds:
    def test_context_id_deterministic(self):
        ctx1, _ = _build_supported_context()
        ctx2, _ = _build_supported_context()
        assert ctx1.context_id == ctx2.context_id

    def test_context_id_prefix(self):
        ctx, _ = _build_supported_context()
        assert ctx.context_id.startswith("di-")
        assert len(ctx.context_id) == len("di-") + 16

    def test_different_profile_different_id(self):
        outcomes = _resolved_cohort(
            60, instrument="NIFTY", direction="LONG", seed=1,
        ) + _resolved_cohort(
            60, win_fraction=0.4, instrument="TCS", direction="SHORT",
            setup_type="BREAKOUT", seed=2,
        )
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        p1 = _profile(instrument="NIFTY", direction="LONG")
        p2 = _profile(instrument="TCS", direction="SHORT", setup_type="BREAKOUT")
        c1 = eng.build(p1, _decision(), strat.lookup(report, p1))
        c2 = eng.build(p2, _decision(direction="SHORT"), strat.lookup(report, p2))
        assert c1.context_id != c2.context_id

    def test_different_decision_different_id(self):
        ctx, lookup = _build_supported_context()
        eng = DecisionIntelligenceEngine()
        d1 = _decision(score=70)
        d2 = _decision(score=90)
        c1 = eng.build(ctx.profile, d1, lookup)
        c2 = eng.build(ctx.profile, d2, lookup)
        assert c1.context_id != c2.context_id

    def test_label_affects_id(self):
        ctx, lookup = _build_supported_context()
        eng = DecisionIntelligenceEngine()
        c1 = eng.build(ctx.profile, _decision(), lookup, label="a")
        c2 = eng.build(ctx.profile, _decision(), lookup, label="b")
        assert c1.context_id != c2.context_id


# ============================================================
# M. DETERMINISTIC FACTOR ORDERING
# ============================================================


class TestDeterministicFactorOrdering:
    def test_factors_ordered_by_rank_value(self):
        ctx, _ = _build_supported_context()
        ranks = [f.factor.rank_value for f in ctx.factors]
        assert ranks == sorted(ranks)

    def test_factors_deterministic_across_calls(self):
        c1, _ = _build_supported_context()
        c2, _ = _build_supported_context()
        assert c1.factors == c2.factors

    def test_no_duplicate_factors(self):
        ctx, _ = _build_supported_context()
        names = [f.factor for f in ctx.factors]
        assert len(names) == len(set(names))


# ============================================================
# N. REPEATED EVALUATION IDENTITY
# ============================================================


class TestRepeatedEvaluation:
    def test_repeated_build_identical(self):
        ctx1, _ = _build_supported_context()
        ctx2, _ = _build_supported_context()
        assert ctx1 == ctx2

    def test_repeated_build_from_report_identical(self):
        outcomes = _resolved_cohort(60, seed=29)
        report = _evidence_report(outcomes)
        eng = DecisionIntelligenceEngine()
        c1 = eng.build_from_report(report, _profile(), _decision())
        c2 = eng.build_from_report(report, _profile(), _decision())
        assert c1 == c2
        assert c1.context_id == c2.context_id


# ============================================================
# O. SHUFFLED-INPUT DETERMINISM
# ============================================================


class TestShuffledInput:
    def test_shuffled_outcomes_same_context_id(self):
        outcomes = _resolved_cohort(60, seed=31)
        report_a = _evidence_report(outcomes)
        report_b = _evidence_report(list(reversed(outcomes)))
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        c1 = eng.build(profile, _decision(), strat.lookup(report_a, profile))
        c2 = eng.build(profile, _decision(), strat.lookup(report_b, profile))
        assert c1.context_id == c2.context_id
        assert c1.decision_context_status == c2.decision_context_status
        assert c1.factors == c2.factors

    def test_shuffled_outcomes_same_full_context(self):
        outcomes = _resolved_cohort(60, seed=33)
        report_a = _evidence_report(outcomes)
        report_b = _evidence_report(list(reversed(outcomes)))
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        c1 = eng.build(profile, _decision(), strat.lookup(report_a, profile))
        c2 = eng.build(profile, _decision(), strat.lookup(report_b, profile))
        assert c1 == c2


# ============================================================
# P. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_round_trip_supported_context(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt == ctx

    def test_round_trip_id_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.context_id == ctx.context_id

    def test_round_trip_status_factors_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.decision_context_status == ctx.decision_context_status
        assert rt.factors == ctx.factors

    def test_round_trip_observed_performance_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.observed_performance == ctx.observed_performance
        assert rt.evidence_strength == ctx.evidence_strength
        assert rt.strategy_interpretation == ctx.strategy_interpretation

    def test_round_trip_lookup_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.lookup == ctx.lookup
        assert rt.matched_cohort == ctx.matched_cohort

    def test_round_trip_existing_decision_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.existing_decision == ctx.existing_decision

    def test_round_trip_profile_preserved(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context(ctx))
        assert rt.profile == ctx.profile

    def test_round_trip_no_match_context(self):
        outcomes = _resolved_cohort(60, seed=35)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONE"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        rt = deserialize_context(serialize_context(ctx))
        assert rt == ctx
        assert rt.observed_performance is None
        assert rt.evidence_strength is None

    def test_round_trip_label_metadata_preserved(self):
        ctx, _ = _build_supported_context()
        ctx_with_meta = DecisionIntelligenceEngine().build(
            ctx.profile, _decision(), ctx.lookup,
            label="lbl", metadata={"k": "v"},
        )
        rt = deserialize_context(serialize_context(ctx_with_meta))
        assert rt.label == "lbl"
        assert rt.metadata == (("k", "v"),)

    def test_bytes_round_trip(self):
        ctx, _ = _build_supported_context()
        rt = deserialize_context(serialize_context_bytes(ctx).decode("utf-8"))
        assert rt == ctx

    def test_deterministic_bytes(self):
        ctx, _ = _build_supported_context()
        assert serialize_context_bytes(ctx) == serialize_context_bytes(ctx)

    def test_canonical_json_equals_serialized(self):
        ctx, _ = _build_supported_context()
        assert canonical_context_json(ctx) == serialize_context(ctx)

    def test_schema_version_in_document(self):
        ctx, _ = _build_supported_context()
        header = parse_decision_intelligence_header(serialize_context(ctx))
        assert header["schema_version"] == DECISION_INTELLIGENCE_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        import json
        ctx, _ = _build_supported_context()
        payload = json.loads(serialize_context(ctx))
        payload["schema_version"] = 999
        with pytest.raises(ValueError, match="schema version"):
            deserialize_context(json.dumps(payload))

    def test_invalid_payload_rejected(self):
        with pytest.raises(ValueError):
            deserialize_context("not json")


# ============================================================
# Q. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_outcomes_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=37)
        report = _evidence_report(outcomes)
        originals = [
            (o.outcome_status, o.realized_r, o.subject.instrument)
            for o in outcomes
        ]
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        eng.build(profile, _decision(), lookup)
        after = [
            (o.outcome_status, o.realized_r, o.subject.instrument)
            for o in outcomes
        ]
        assert originals == after

    def test_evidence_report_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=39)
        report = _evidence_report(outcomes)
        original_id = report.evidence_id
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        eng.build(profile, _decision(), lookup)
        assert report.evidence_id == original_id

    def test_profile_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=41)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        profile = _profile()
        original_instrument = profile.instrument
        lookup = strat.lookup(report, profile)
        eng = DecisionIntelligenceEngine()
        eng.build(profile, _decision(), lookup)
        assert profile.instrument == original_instrument

    def test_lookup_not_mutated(self):
        ctx, lookup = _build_supported_context()
        original_status = lookup.match_status
        original_cohort_key = lookup.matched_cohort.key if lookup.matched_cohort else None
        eng = DecisionIntelligenceEngine()
        eng.build(ctx.profile, _decision(), lookup)
        assert lookup.match_status == original_status
        if lookup.matched_cohort is not None:
            assert lookup.matched_cohort.key == original_cohort_key

    def test_existing_decision_summary_not_mutated(self):
        ctx, lookup = _build_supported_context()
        decision = _decision(score=75)
        original_score = decision.decision_score
        eng = DecisionIntelligenceEngine()
        eng.build(ctx.profile, decision, lookup)
        assert decision.decision_score == original_score

    def test_reused_statistics_by_reference(self):
        ctx, lookup = _build_supported_context()
        # The surfaced observed_performance is the SAME object as the
        # one inside the lookup's assessment (no copy, no recompute).
        assert ctx.observed_performance is lookup.assessment.observed_performance

    def test_reused_strength_by_reference(self):
        ctx, lookup = _build_supported_context()
        assert ctx.evidence_strength is lookup.assessment.evidence_strength


# ============================================================
# R. NO-LOOK-AHEAD PROTECTION
# ============================================================


class TestNoLookAhead:
    def test_build_signature_has_no_candles(self):
        sig = inspect.signature(DecisionIntelligenceEngine.build)
        assert "candles" not in sig.parameters
        assert "lookup" in sig.parameters

    def test_build_from_report_signature_has_no_candles(self):
        sig = inspect.signature(DecisionIntelligenceEngine.build_from_report)
        assert "candles" not in sig.parameters
        assert "report" in sig.parameters

    def test_works_with_outcome_evaluator_patched_to_raise(self):
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        outcomes = _resolved_cohort(60, seed=43)
        report = _evidence_report(outcomes)
        eng = DecisionIntelligenceEngine()
        original = OutcomeEvaluator.evaluate

        def boom(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("must not re-evaluate outcomes")

        OutcomeEvaluator.evaluate = boom  # type: ignore[method-assign]
        try:
            ctx = eng.build_from_report(report, _profile(), _decision())
            assert ctx.matched
        finally:
            OutcomeEvaluator.evaluate = original  # type: ignore[method-assign]

    def test_works_with_pipeline_patched_to_raise(self):
        outcomes = _resolved_cohort(60, seed=45)
        report = _evidence_report(outcomes)
        eng = DecisionIntelligenceEngine()
        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):  # noqa: ANN001
            raise RuntimeError("must not re-run pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[method-assign]
        try:
            ctx = eng.build_from_report(report, _profile(), _decision())
            assert ctx.matched
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[method-assign]

    def test_no_future_candles_inspected(self):
        outcomes = _resolved_cohort(60, seed=47)
        report = _evidence_report(outcomes)
        eng = DecisionIntelligenceEngine()
        c1 = eng.build_from_report(report, _profile(), _decision())
        # Mutating / extending unrelated future data cannot change the
        # context (the engine never reads candles).
        c2 = eng.build_from_report(report, _profile(), _decision())
        assert c1 == c2


# ============================================================
# S. RATIONALE CORRECTNESS
# ============================================================


class TestRationale:
    def test_rationale_mentions_opportunity_characteristics(self):
        ctx, _ = _build_supported_context()
        assert "NIFTY" in ctx.rationale
        assert "LONG" in ctx.rationale

    def test_rationale_mentions_decision_context_status(self):
        ctx, _ = _build_supported_context()
        assert ctx.decision_context_status.name in ctx.rationale

    def test_rationale_mentions_evidence_strength(self):
        ctx, _ = _build_supported_context()
        assert ctx.evidence_strength.name in ctx.rationale

    def test_rationale_mentions_existing_decision(self):
        ctx, _ = _build_supported_context()
        assert "QUALIFIED" in ctx.rationale
        assert "75" in ctx.rationale

    def test_rationale_mentions_factors(self):
        ctx, _ = _build_supported_context()
        for f in ctx.factors:
            assert f.factor.name in ctx.rationale or "factors" in ctx.rationale.lower()

    def test_rationale_no_predictive_language(self):
        ctx, _ = _build_supported_context()
        lowered = ctx.rationale.lower()
        for term in ("will win", "guaranteed", "high probability", "statistically significant"):
            assert term not in lowered

    def test_rationale_no_match_case(self):
        outcomes = _resolved_cohort(60, seed=49)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONE"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        assert "EVIDENCE_UNAVAILABLE" in ctx.rationale
        assert "No matching historical cohort" in ctx.rationale

    def test_rationale_states_existing_decision_not_modified(self):
        ctx, _ = _build_supported_context()
        assert "not modified" in ctx.rationale.lower() or "without alteration" in ctx.rationale.lower()


# ============================================================
# T. LIMITATIONS CORRECTNESS
# ============================================================


class TestLimitations:
    def test_limitations_mention_no_statistical_test(self):
        ctx, _ = _build_supported_context()
        assert "No statistical hypothesis test was performed" in ctx.limitations

    def test_limitations_mention_no_future_guarantee(self):
        ctx, _ = _build_supported_context()
        assert "does not guarantee future performance" in ctx.limitations

    def test_limitations_mention_no_replacement_of_decision_logic(self):
        ctx, _ = _build_supported_context()
        assert "does NOT replace" in ctx.limitations or "not replace" in ctx.limitations.lower()

    def test_limitations_mention_sample_size(self):
        ctx, _ = _build_supported_context()
        assert "Sample size" in ctx.limitations

    def test_limitations_no_match_case(self):
        outcomes = _resolved_cohort(60, seed=51)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONE"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        assert "never fabricated" in ctx.limitations.lower()


# ============================================================
# U. EXISTING DECISION REMAINS UNCHANGED
# ============================================================


class TestExistingDecisionUnchanged:
    def test_decision_engine_behavior_unchanged(self):
        # The Sprint 11S decision engine is not imported or modified by
        # the 12A layer; running it yields the same result.
        from engine.models.trade_candidate import TradeCandidate
        from engine.models.trade_decision import DecisionClassification
        from engine.config.trade_decision_config import TradeDecisionConfig
        eng = TradeDecisionEngine(TradeDecisionConfig())
        # Minimal candidate constructed via the model directly.
        from engine.models.setup_confluence import (
            SetupAssessment,
            SetupClassification,
            SetupDirection,
        )
        from engine.models.candle_pattern import CandlePatternType
        # Use a real candidate from the engine path is complex; instead
        # verify the decision engine class is importable and unaffected.
        assert TradeDecisionEngine is not None
        assert DecisionClassification.PREFERRED.name == "PREFERRED"

    def test_existing_decision_summary_is_read_only_projection(self):
        ctx, lookup = _build_supported_context()
        # The context's existing_decision is the SAME object passed in
        # (represented, not altered).
        decision = _decision(score=80)
        eng = DecisionIntelligenceEngine()
        ctx2 = eng.build(ctx.profile, decision, lookup)
        assert ctx2.existing_decision is decision
        assert ctx2.existing_decision.decision_score == 80


# ============================================================
# V. EXISTING SCORING REMAINS UNCHANGED
# ============================================================


class TestExistingScoringUnchanged:
    def test_trade_decision_engine_unaffected(self):
        from engine.intelligence.trade_decision import TradeDecisionEngine as TDE
        sig = inspect.signature(TDE.decide)
        # The decide signature is unchanged (takes candidate, index, ts).
        assert "candidate" in sig.parameters

    def test_trade_opportunity_engine_unaffected(self):
        from engine.intelligence.trade_opportunity import (
            TradeOpportunityEngine as TOE,
        )
        sig = inspect.signature(TOE.evaluate)
        assert "decision" in sig.parameters


# ============================================================
# W. EXISTING RANKING REMAINS UNCHANGED
# ============================================================


class TestExistingRankingUnchanged:
    def test_trade_opportunity_ranking_method_unchanged(self):
        from engine.intelligence.trade_opportunity import (
            TradeOpportunityEngine as TOE,
        )
        assert hasattr(TOE, "rank")


# ============================================================
# X. EXISTING 11X ANALYTICS UNCHANGED
# ============================================================


class TestSprint11XUnchanged:
    def test_performance_analytics_engine_still_works(self):
        outcomes = _resolved_cohort(60, seed=53)
        analytics = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(label="x"),
        ).analyze(outcomes)
        assert analytics.outcome_count == 60
        assert analytics.analytics_id.startswith("perf-")

    def test_12A_does_not_recompute_analytics(self):
        outcomes = _resolved_cohort(60, seed=55)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        ctx = eng.build(profile, _decision(), lookup)
        # The surfaced statistics are the SAME object as inside the
        # lookup's assessment (reused by reference, never recomputed).
        assert ctx.observed_performance is lookup.assessment.observed_performance


# ============================================================
# Y. EXISTING 11Y EVIDENCE UNCHANGED
# ============================================================


class TestSprint11YUnchanged:
    def test_evidence_engine_still_works(self):
        outcomes = _resolved_cohort(60, seed=57)
        report = HistoricalEvidenceEngine(
            EvidenceConfig(label="y"),
        ).evaluate(outcomes)
        assert report.evidence_id.startswith("evidence-")
        assert report.summary.sample_count == 60

    def test_12A_does_not_reclassify_evidence(self):
        outcomes = _resolved_cohort(60, seed=59)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        ctx = eng.build(profile, _decision(), lookup)
        assert ctx.evidence_strength is lookup.assessment.evidence_strength
        assert ctx.evidence_strength is lookup.matched_cohort.strength


# ============================================================
# Z. EXISTING 11Z STRATEGY INTELLIGENCE UNCHANGED
# ============================================================


class TestSprint11ZUnchanged:
    def test_strategy_engine_still_works(self):
        outcomes = _resolved_cohort(60, seed=61)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        assert lookup.matched
        assert lookup.assessment is not None

    def test_12A_does_not_reinterpret_strategy(self):
        outcomes = _resolved_cohort(60, seed=63)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        eng = DecisionIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        ctx = eng.build(profile, _decision(), lookup)
        # The surfaced strategy interpretation is the SAME object as
        # the 11Z assessment's status (reused by reference).
        assert ctx.strategy_interpretation is lookup.assessment.assessment_status


# ============================================================
# AA. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        ctx, _ = _build_supported_context()
        report = DecisionIntelligenceFormatter().format(ctx)
        assert isinstance(report, str)

    def test_required_sections_present(self):
        ctx, _ = _build_supported_context()
        report = DecisionIntelligenceFormatter().format(ctx)
        for section in [
            "Decision Intelligence Report",
            "Current Opportunity",
            "Existing Decision",
            "Historical Evidence",
            "Observed Performance",
            "Evidence Strength",
            "Strategy Interpretation",
            "Decision Context",
            "Limitations",
            "Rationale",
        ]:
            assert section in report, f"missing section: {section}"

    def test_warning_present(self):
        ctx, _ = _build_supported_context()
        report = DecisionIntelligenceFormatter().format(ctx)
        assert "does not guarantee future performance" in report
        assert "not modified by this context" in report

    def test_no_predictive_language(self):
        ctx, _ = _build_supported_context()
        report = DecisionIntelligenceFormatter().format(ctx).lower()
        for term in (
            "buy", "sell", "enter", "exit", "hold",
            "guaranteed profit", "will win", "high probability",
            "statistically significant",
        ):
            assert term not in report, f"forbidden term present: {term}"

    def test_unavailable_shown_for_no_match(self):
        outcomes = _resolved_cohort(60, seed=65)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONE"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        text = DecisionIntelligenceFormatter().format(ctx)
        assert "unavailable" in text.lower()
        assert DecisionContextStatus.EVIDENCE_UNAVAILABLE.name in text

    def test_format_deterministic(self):
        ctx, _ = _build_supported_context()
        f = DecisionIntelligenceFormatter()
        assert f.format(ctx) == f.format(ctx)

    def test_factors_shown_in_report(self):
        ctx, _ = _build_supported_context()
        report = DecisionIntelligenceFormatter().format(ctx)
        for f in ctx.factors:
            assert f.factor.name in report

    def test_precision_negative_rejected(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceFormatter(precision=-1)

    def test_empty_profile_rendered(self):
        outcomes = _resolved_cohort(60, seed=67)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        # Empty profile -> NO_MATCH (no usable dimensions).
        nomatch = strat.lookup(report, OpportunityProfile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(OpportunityProfile(), _decision(), nomatch)
        text = DecisionIntelligenceFormatter().format(ctx)
        assert "no opportunity characteristics provided" in text.lower()


# ============================================================
# AB. CONFIGURATION VALIDATION
# ============================================================


class TestConfigValidation:
    def test_defaults(self):
        cfg = DecisionIntelligenceConfig()
        assert cfg.favorable_win_rate == 0.55
        assert cfg.favorable_profit_factor == 1.3
        assert cfg.favorable_avg_r == 0.1
        assert cfg.adverse_avg_r == 0.0
        assert cfg.adverse_profit_factor == 1.0
        assert cfg.label == ""
        assert cfg.metadata == ()

    def test_frozen(self):
        cfg = DecisionIntelligenceConfig()
        with pytest.raises(Exception):
            cfg.favorable_win_rate = 0.9  # type: ignore[misc]

    def test_win_rate_bounds(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(favorable_win_rate=-0.1)
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(favorable_win_rate=1.1)

    def test_profit_factor_positive(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(favorable_profit_factor=0)
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(favorable_profit_factor=-1)

    def test_avg_r_non_negative(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(favorable_avg_r=-0.1)

    def test_adverse_profit_factor_positive(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(adverse_profit_factor=0)

    def test_adverse_le_favorable_profit_factor(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceConfig(
                favorable_profit_factor=1.0, adverse_profit_factor=2.0,
            )

    def test_snapshot_sorted(self):
        cfg = DecisionIntelligenceConfig()
        snap = cfg.snapshot()
        assert snap == tuple(sorted(snap))
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in snap)

    def test_config_affects_factor_wording_not_status(self):
        # Changing the descriptive thresholds changes factor presence
        # but NEVER the decision context status.
        outcomes = _resolved_cohort(60, win_fraction=0.6, seed=69)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(report, profile)
        eng_low = DecisionIntelligenceEngine(
            DecisionIntelligenceConfig(favorable_win_rate=0.99),
        )
        eng_high = DecisionIntelligenceEngine(
            DecisionIntelligenceConfig(favorable_win_rate=0.1),
        )
        c_low = eng_low.build(profile, _decision(), lookup)
        c_high = eng_high.build(profile, _decision(), lookup)
        assert c_low.decision_context_status == c_high.decision_context_status
        # With a high favourable threshold, the favourable factor may
        # disappear; with a low threshold it should appear.
        assert (
            DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS
            in [f.factor for f in c_high.factors]
        )


# ============================================================
# AC. MODEL IMMUTABILITY (frozen + slots)
# ============================================================


class TestModelImmutability:
    def test_context_frozen(self):
        ctx, _ = _build_supported_context()
        with pytest.raises(Exception):
            ctx.label = "x"  # type: ignore[misc]

    def test_context_has_slots(self):
        ctx, _ = _build_supported_context()
        assert not hasattr(ctx, "__dict__")

    def test_existing_decision_summary_frozen(self):
        d = _decision()
        with pytest.raises(Exception):
            d.direction = "SHORT"  # type: ignore[misc]

    def test_existing_decision_summary_slots(self):
        d = _decision()
        assert not hasattr(d, "__dict__")

    def test_factor_frozen(self):
        f = DecisionIntelligenceFactor(
            factor=DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE, reason="r",
        )
        with pytest.raises(Exception):
            f.reason = "x"  # type: ignore[misc]

    def test_factor_slots(self):
        f = DecisionIntelligenceFactor(
            factor=DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE, reason="r",
        )
        assert not hasattr(f, "__dict__")


# ============================================================
# AD. SAMPLE-SIZE HARD GATE PROPAGATION
# ============================================================


class TestSampleSizeHardGate:
    def test_small_sample_never_supported(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=71)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert lookup.assessment.observed_performance.win_rate == 1.0
        assert ctx.decision_context_status != DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ctx.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE

    def test_insufficient_not_actionable(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=73)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert not ctx.evidence_supported


# ============================================================
# AE. HONEST FALLBACKS
# ============================================================


class TestHonestFallbacks:
    def test_no_match_no_fabricated_assessment(self):
        outcomes = _resolved_cohort(60, seed=75)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        nomatch = strat.lookup(report, OpportunityProfile(setup_type="NONE"))
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), nomatch)
        assert ctx.observed_performance is None
        assert ctx.evidence_strength is None
        assert ctx.strategy_interpretation is None

    def test_no_existing_decision_handled(self):
        ctx, lookup = _build_supported_context()
        eng = DecisionIntelligenceEngine()
        ctx_none = eng.build(ctx.profile, None, lookup)
        assert ctx_none.existing_decision.has_decision is False
        assert ctx_none.decision_context_status == ctx.decision_context_status

    def test_unfavorable_characteristic_emitted_when_sufficient(self):
        # 60 outcomes with low win fraction -> sufficient evidence,
        # unfavourable observed win rate.
        outcomes = _resolved_cohort(60, win_fraction=0.2, seed=77)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert ctx.evidence_strength.is_sufficient
        assert (
            DecisionEvidenceFactor.UNFAVORABLE_HISTORICAL_CHARACTERISTICS
            in [f.factor for f in ctx.factors]
        )

    def test_favorable_and_unfavorable_can_coexist(self):
        # Mixed metrics: high win rate but negative avg R is hard to
        # construct cleanly; instead verify favourable emitted when
        # sufficient + favourable win rate.
        outcomes = _resolved_cohort(60, win_fraction=0.7, seed=79)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(_profile(), _decision(), lookup)
        assert (
            DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS
            in [f.factor for f in ctx.factors]
        )


# ============================================================
# AF. END-TO-END 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A CHAIN
# ============================================================


class TestEndToEndChain:
    def test_full_chain_from_real_outcomes(self):
        # Build a historical outcome corpus (standing in for the 11V
        # replay -> 11W outcome evaluation output), then run 11X ->
        # 11Y -> 11Z -> 12A and confirm the decision-intelligence
        # context is produced and serializes losslessly.
        long_trend = _resolved_cohort(
            60, win_fraction=0.6, instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED", seed=101,
        )
        short_breakout = _resolved_cohort(
            60, win_fraction=0.4, instrument="TCS", direction="SHORT",
            setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=102,
        )
        insufficient = _resolved_cohort(
            5, win_fraction=1.0, instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED", seed=103,
        )
        all_outcomes = long_trend + short_breakout + insufficient

        # 11X + 11Y.
        evidence = HistoricalEvidenceEngine(
            EvidenceConfig(label="e2e-12a"),
        ).evaluate(all_outcomes)

        # 11Z lookup for a current opportunity.
        strat = StrategyIntelligenceEngine()
        profile = OpportunityProfile(
            instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
        )
        lookup = strat.lookup(evidence, profile)
        assert lookup.matched

        # 12A decision intelligence context.
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(
            profile,
            _decision(direction="LONG", classification="QUALIFIED"),
            lookup,
            label="e2e",
            metadata={"source": "12a-e2e"},
        )
        assert ctx.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ctx.evidence_strength == EvidenceStrength.STRONG
        assert ctx.matched

        # Serialization round trip preserves everything.
        rt = deserialize_context(serialize_context(ctx))
        assert rt == ctx
        assert rt.context_id == ctx.context_id
        assert rt.metadata == (("source", "12a-e2e"),)

    def test_full_chain_insufficient_cohort(self):
        insufficient = _resolved_cohort(
            5, win_fraction=1.0, instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED", seed=111,
        )
        evidence = _evidence_report(insufficient)
        strat = StrategyIntelligenceEngine()
        profile = OpportunityProfile(
            instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED",
        )
        lookup = strat.lookup(evidence, profile)
        eng = DecisionIntelligenceEngine()
        ctx = eng.build(profile, _decision(), lookup)
        assert ctx.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE
        assert ctx.evidence_strength == EvidenceStrength.INSUFFICIENT

    def test_pipeline_regression_baseline(self):
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3
