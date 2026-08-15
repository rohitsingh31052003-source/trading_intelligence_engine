"""
Tests for the evidence-conditioned strategy intelligence layer
(Sprint 11Z).

Coverage (mirrors the required test areas):

A.  Model construction + properties
B.  Valid strategy assessment (assess_cohort / assess)
C.  Evidence-conditioned classification propagation
    (insufficient / weak / moderate / strong)
D.  Current opportunity evidence lookup
E.  Missing cohort lookup (NO_MATCH honest fallback)
F.  Cohort comparison
G.  Missing cohort in comparison (honest fallback)
H.  Deterministic ordering
I.  Deterministic IDs
J.  Serialization round trip (assessment / comparison / lookup)
K.  Input immutability (outcomes / evidence report / profile)
L.  BOTH_TOUCHED handling
M.  NO_GEOMETRY handling
N.  INSUFFICIENT_DATA handling
O.  No-look-ahead protection (no candles inspected; outcome evaluator
    patched to raise; pipeline patched to raise)
P.  Repeated evaluation identity
Q.  Shuffled-input determinism
R.  Existing Sprint 11X analytics remain unchanged
S.  Existing Sprint 11Y evidence remains unchanged
T.  Existing pipeline regression (signals=4, trades=3)
U.  Reporting (sections / disclaimer / unavailable / determinism)
V.  No statistical-superiority language in comparison
W.  Configuration validation
X.  Model immutability (frozen + slots)
Y.  Sample-size hard gate propagation (small sample never strong)
Z.  Honest fallbacks (no fabricated metric / no fabricated R / no
    fabricated assessment)
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
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
from engine.intelligence.strategy_intelligence_serialization import (
    STRATEGY_SCHEMA_VERSION,
    canonical_assessment_json,
    canonical_comparison_json,
    canonical_lookup_json,
    deserialize_assessment,
    deserialize_comparison,
    deserialize_lookup,
    parse_strategy_header,
    serialize_assessment,
    serialize_assessment_bytes,
    serialize_comparison,
    serialize_comparison_bytes,
    serialize_lookup,
    serialize_lookup_bytes,
)
from engine.config.strategy_intelligence_config import StrategyIntelligenceConfig
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
from engine.models.ohlcv import OHLCVCandle
from engine.models.strategy_intelligence import (
    EVIDENCE_TO_STRATEGY,
    CohortComparison,
    CohortComparisonMetric,
    CohortMatchStatus,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
    StrategyEvidenceAssessment,
)
from engine.reporting.strategy_intelligence import StrategyIntelligenceFormatter
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
        scan_id="scan-z",
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
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    rank: int = 1,
    seed: int = 0,
) -> list[HistoricalOutcome]:
    """A deterministic cohort of ``n`` resolved target/stop outcomes."""

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
                mtf_alignment=mtf_alignment, decision=decision,
                opportunity=opportunity, rank=rank,
                ts=_EPOCH + timedelta(days=i),
            ),
        )
    return outcomes


def _evidence_report(outcomes, label="test"):
    return HistoricalEvidenceEngine(EvidenceConfig(label=label)).evaluate(
        outcomes,
    )


def _find_cohort_in_report(
    report: HistoricalEvidenceReport, spec: CohortSpec, key: str,
):
    for b in report.breakdowns:
        if b.spec.dimensions == spec.dimensions:
            for c in b.cohorts:
                if c.key == key:
                    return c
    return None


SETUP_DIR_SPEC = CohortSpec(
    (BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION),
)
SETUP_MTF_SPEC = CohortSpec(
    (BreakdownDimension.SETUP_TYPE, BreakdownDimension.MTF_ALIGNMENT),
)
SETUP_SPEC = CohortSpec((BreakdownDimension.SETUP_TYPE,))
DIRECTION_SPEC = CohortSpec((BreakdownDimension.DIRECTION,))


# ============================================================
# A. MODEL CONSTRUCTION + PROPERTIES
# ============================================================


class TestModelConstruction:
    def test_strategy_assessment_status_members(self):
        names = {s.name for s in StrategyAssessmentStatus}
        assert names == {
            "INSUFFICIENT_EVIDENCE", "LIMITED_EVIDENCE",
            "SUPPORTIVE_EVIDENCE", "STRONGER_HISTORICAL_SUPPORT",
        }

    def test_strategy_assessment_status_rank_value_ordering(self):
        order = sorted(
            StrategyAssessmentStatus,
            key=lambda s: s.rank_value,
        )
        assert order == [
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE,
            StrategyAssessmentStatus.LIMITED_EVIDENCE,
            StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE,
        ]

    def test_is_actionable(self):
        assert not StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE.is_actionable
        assert StrategyAssessmentStatus.LIMITED_EVIDENCE.is_actionable
        assert StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE.is_actionable
        assert StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT.is_actionable

    def test_cohort_match_status_members(self):
        assert {m.name for m in CohortMatchStatus} == {"MATCHED", "NO_MATCH"}

    def test_evidence_to_strategy_mapping_is_one_to_one(self):
        assert set(EVIDENCE_TO_STRATEGY) == set(EvidenceStrength)
        # Each strength maps to a distinct status (bijective).
        statuses = list(EVIDENCE_TO_STRATEGY.values())
        assert len(set(statuses)) == len(statuses)

    def test_opportunity_profile_available_dimensions(self):
        p = OpportunityProfile(
            instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION",
        )
        assert p.available_dimensions() == (
            ("INSTRUMENT", "NIFTY"),
            ("DIRECTION", "LONG"),
            ("SETUP_TYPE", "TREND_CONTINUATION"),
        )

    def test_opportunity_profile_rank_available(self):
        p = OpportunityProfile(rank=2)
        assert ("OPPORTUNITY_RANK", "2") in p.available_dimensions()

    def test_opportunity_profile_empty(self):
        assert OpportunityProfile().is_empty
        assert not OpportunityProfile(direction="LONG").is_empty

    def test_assessment_has_evidence_and_is_supported(self):
        outcomes = _resolved_cohort(60, seed=10)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a is not None
        assert a.has_evidence is True
        assert a.is_supported is True

    def test_lookup_matched_and_is_empty_properties(self):
        outcomes = _resolved_cohort(60, seed=11)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        matched = eng.lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        assert matched.matched is True
        assert matched.is_empty is False
        nomatch = eng.lookup(
            report, OpportunityProfile(setup_type="NONEXISTENT"),
        )
        assert nomatch.matched is False
        assert nomatch.is_empty is True


# ============================================================
# B. VALID STRATEGY ASSESSMENT
# ============================================================


class TestValidAssessment:
    def test_assess_cohort_returns_assessment(self):
        outcomes = _resolved_cohort(60, seed=20)
        report = _evidence_report(outcomes)
        cohort = _find_cohort_in_report(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        eng = StrategyIntelligenceEngine()
        a = eng.assess_cohort(cohort)
        assert isinstance(a, StrategyEvidenceAssessment)
        assert a.assessment_id.startswith("strat-")
        assert a.spec == cohort.spec
        assert a.cohort_key == cohort.key
        assert a.observed_performance is cohort.statistics
        assert a.evidence_strength is cohort.strength

    def test_assess_cohort_counts_match_cohort(self):
        outcomes = _resolved_cohort(60, seed=21)
        report = _evidence_report(outcomes)
        cohort = _find_cohort_in_report(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        a = StrategyIntelligenceEngine().assess_cohort(cohort)
        assert a.sample_count == cohort.sample_count
        assert a.resolved_count == cohort.resolved_count
        assert a.valid_r_count == cohort.valid_r_count

    def test_assess_finds_existing_cohort(self):
        outcomes = _resolved_cohort(60, seed=22)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a is not None
        assert a.cohort_key == "TREND_CONTINUATION"

    def test_assess_returns_none_for_missing_cohort(self):
        outcomes = _resolved_cohort(60, seed=23)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        assert eng.assess(report, SETUP_SPEC, "NONEXISTENT") is None

    def test_assess_reuses_statistics_no_recompute(self):
        outcomes = _resolved_cohort(60, seed=24)
        report = _evidence_report(outcomes)
        cohort = _find_cohort_in_report(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        a = StrategyIntelligenceEngine().assess_cohort(cohort)
        # The reused statistics object is the SAME object (by reference).
        assert a.observed_performance is cohort.statistics
        assert a.evidence_strength is cohort.strength


# ============================================================
# C. EVIDENCE-CONDITIONED CLASSIFICATION PROPAGATION
# ============================================================


class TestClassificationPropagation:
    def _status_for_outcomes(self, outcomes):
        report = _evidence_report(outcomes)
        cohort = _find_cohort_in_report(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        return StrategyIntelligenceEngine().assess_cohort(cohort).assessment_status

    def test_strong_evidence_propagates(self):
        outcomes = _resolved_cohort(60, win_fraction=0.6, seed=30)
        assert self._status_for_outcomes(outcomes) == (
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT
        )

    def test_moderate_evidence_propagates(self):
        # 40 outcomes, all resolved -> resolved=40 (>=10), valid_r=40
        # (>=10); not all strong thresholds (50/30/30) -> MODERATE.
        outcomes = _resolved_cohort(40, win_fraction=0.6, seed=31)
        assert self._status_for_outcomes(outcomes) == (
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE
        )

    def test_weak_evidence_propagates(self):
        # 30 outcomes all NO_GEOMETRY -> resolved=0 < 10 -> WEAK.
        outcomes = [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(30)
        ]
        assert self._status_for_outcomes(outcomes) == (
            StrategyAssessmentStatus.LIMITED_EVIDENCE
        )

    def test_insufficient_evidence_propagates(self):
        outcomes = _resolved_cohort(5, seed=32)
        assert self._status_for_outcomes(outcomes) == (
            StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE
        )

    def test_status_matches_strength_for_each_strength(self):
        cases = {
            EvidenceStrength.STRONG: 60,
            EvidenceStrength.MODERATE: 40,
            EvidenceStrength.WEAK: None,  # NO_GEOMETRY cohort
            EvidenceStrength.INSUFFICIENT: 5,
        }
        for strength, n in cases.items():
            if n is None:
                outcomes = [
                    _outcome(
                        OutcomeStatus.NO_GEOMETRY,
                        ts=_EPOCH + timedelta(days=i),
                        mfe=None, mae=None, mfe_r=None, mae_r=None,
                    )
                    for i in range(30)
                ]
            else:
                outcomes = _resolved_cohort(n, seed=33)
            report = _evidence_report(outcomes)
            cohort = _find_cohort_in_report(
                report, SETUP_SPEC, "TREND_CONTINUATION",
            )
            a = StrategyIntelligenceEngine().assess_cohort(cohort)
            assert a.evidence_strength == strength
            assert a.assessment_status == EVIDENCE_TO_STRATEGY[strength]


# ============================================================
# D. CURRENT OPPORTUNITY EVIDENCE LOOKUP
# ============================================================


class TestLookup:
    def test_lookup_matches_single_dimension(self):
        outcomes = _resolved_cohort(60, seed=40)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        assert lk.match_status == CohortMatchStatus.MATCHED
        assert lk.matched_spec == SETUP_SPEC
        assert lk.matched_cohort is not None
        assert lk.assessment is not None
        assert lk.assessment.cohort_key == "TREND_CONTINUATION"

    def test_lookup_prefers_most_specific_composite(self):
        # Two cohorts differing by direction for the same setup type.
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=41)
            + _resolved_cohort(60, direction="SHORT", seed=42)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(
            report,
            OpportunityProfile(
                setup_type="TREND_CONTINUATION", direction="LONG",
            ),
        )
        # Composite SETUP_TYPE+DIRECTION is more specific than
        # SETUP_TYPE alone -> selected.
        assert lk.matched_spec == SETUP_DIR_SPEC
        assert lk.matched_cohort.key == "TREND_CONTINUATION|LONG"

    def test_lookup_deterministic_spec_selection(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=43)
            + _resolved_cohort(60, direction="SHORT", seed=44)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk1 = eng.lookup(
            report,
            OpportunityProfile(setup_type="TREND_CONTINUATION", direction="LONG"),
        )
        lk2 = eng.lookup(
            report,
            OpportunityProfile(setup_type="TREND_CONTINUATION", direction="LONG"),
        )
        assert lk1.matched_spec == lk2.matched_spec
        assert lk1.lookup_id == lk2.lookup_id

    def test_lookup_rank_dimension(self):
        outcomes = _resolved_cohort(60, rank=1, seed=45)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(report, OpportunityProfile(rank=1))
        assert lk.match_status == CohortMatchStatus.MATCHED
        assert lk.matched_spec == CohortSpec((BreakdownDimension.OPPORTUNITY_RANK,))


# ============================================================
# E. MISSING COHORT LOOKUP (NO_MATCH HONEST FALLBACK)
# ============================================================


class TestMissingCohortLookup:
    def test_no_match_when_no_characteristic(self):
        outcomes = _resolved_cohort(60, seed=50)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(report, OpportunityProfile())
        assert lk.match_status == CohortMatchStatus.NO_MATCH
        assert lk.matched_cohort is None
        assert lk.assessment is None
        assert lk.matched_spec is None
        assert "No supported cohort spec" in lk.limitations

    def test_no_match_when_key_absent(self):
        outcomes = _resolved_cohort(60, seed=51)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(
            report, OpportunityProfile(setup_type="NONEXISTENT"),
        )
        assert lk.match_status == CohortMatchStatus.NO_MATCH
        assert lk.assessment is None
        assert "No historical cohort matches" in lk.limitations

    def test_no_match_lookup_id_is_deterministic(self):
        outcomes = _resolved_cohort(60, seed=52)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk1 = eng.lookup(report, OpportunityProfile(setup_type="NONEXISTENT"))
        lk2 = eng.lookup(report, OpportunityProfile(setup_type="NONEXISTENT"))
        assert lk1.lookup_id == lk2.lookup_id


# ============================================================
# F. COHORT COMPARISON
# ============================================================


class TestComparison:
    def test_comparison_both_present(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", win_fraction=0.6, seed=60)
            + _resolved_cohort(
                60, direction="SHORT", win_fraction=0.4, seed=61,
            )
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert cmp.both_present is True
        assert cmp.comparison_id.startswith("compare-")
        assert len(cmp.metrics) == 7
        names = [m.name for m in cmp.metrics]
        assert names == [
            "Opportunity Count", "Resolved Count", "Valid R Count",
            "Win Rate", "Average Realized R", "Total Realized R",
            "Profit Factor",
        ]

    def test_comparison_exposes_evidence_strength(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=62)
            + _resolved_cohort(5, direction="SHORT", seed=63)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert cmp.assessment_a is not None
        assert cmp.assessment_b is not None
        assert cmp.assessment_a.evidence_strength == EvidenceStrength.STRONG
        assert cmp.assessment_b.evidence_strength == EvidenceStrength.INSUFFICIENT

    def test_comparison_metric_deltas_descriptive(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", win_fraction=0.7, seed=64)
            + _resolved_cohort(
                60, direction="SHORT", win_fraction=0.3, seed=65,
            )
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        win_metric = next(m for m in cmp.metrics if m.name == "Win Rate")
        assert win_metric.value_a is not None
        assert win_metric.value_b is not None
        assert win_metric.delta == win_metric.value_a - win_metric.value_b
        assert "cohort A higher" in win_metric.note

    def test_comparison_id_symmetric_in_keys(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=66)
            + _resolved_cohort(60, direction="SHORT", seed=67)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp_ab = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        cmp_ba = eng.compare(report, DIRECTION_SPEC, "SHORT", "LONG")
        # id is symmetric in the key pair.
        assert cmp_ab.comparison_id == cmp_ba.comparison_id


# ============================================================
# G. MISSING COHORT IN COMPARISON (HONEST FALLBACK)
# ============================================================


class TestComparisonMissing:
    def test_missing_cohort_a_recorded(self):
        outcomes = _resolved_cohort(60, direction="LONG", seed=70)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp = eng.compare(report, DIRECTION_SPEC, "LONG", "NONEXISTENT")
        assert cmp.cohort_a_present is True
        assert cmp.cohort_b_present is False
        assert cmp.assessment_b is None
        for m in cmp.metrics:
            assert m.value_b is None
            assert m.delta is None
        assert any("Cohort B" in n and "absent" in n for n in cmp.notes)

    def test_missing_both_cohorts(self):
        outcomes = _resolved_cohort(60, seed=71)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp = eng.compare(report, DIRECTION_SPEC, "NONE_A", "NONE_B")
        assert cmp.cohort_a_present is False
        assert cmp.cohort_b_present is False
        for m in cmp.metrics:
            assert m.value_a is None
            assert m.value_b is None
            assert m.delta is None


# ============================================================
# H. DETERMINISTIC ORDERING
# ============================================================


class TestDeterministicOrdering:
    def test_metrics_order_stable(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=80)
            + _resolved_cohort(60, direction="SHORT", seed=81)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp1 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        cmp2 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert [m.name for m in cmp1.metrics] == [m.name for m in cmp2.metrics]

    def test_notes_order_stable(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=82)
            + _resolved_cohort(60, direction="SHORT", seed=83)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        cmp1 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        cmp2 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert cmp1.notes == cmp2.notes


# ============================================================
# I. DETERMINISTIC IDS
# ============================================================


class TestDeterministicIds:
    def test_assessment_id_deterministic(self):
        outcomes = _resolved_cohort(60, seed=90)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a1 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        a2 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a1.assessment_id == a2.assessment_id

    def test_assessment_id_prefix(self):
        outcomes = _resolved_cohort(60, seed=91)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a.assessment_id.startswith("strat-")

    def test_different_cohort_different_id(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=92)
            + _resolved_cohort(60, direction="SHORT", seed=93)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a_long = eng.assess(report, DIRECTION_SPEC, "LONG")
        a_short = eng.assess(report, DIRECTION_SPEC, "SHORT")
        assert a_long.assessment_id != a_short.assessment_id

    def test_lookup_id_deterministic(self):
        outcomes = _resolved_cohort(60, seed=94)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk1 = eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        lk2 = eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        assert lk1.lookup_id == lk2.lookup_id

    def test_comparison_id_deterministic(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=95)
            + _resolved_cohort(60, direction="SHORT", seed=96)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        c1 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        c2 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert c1.comparison_id == c2.comparison_id


# ============================================================
# J. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_assessment_round_trip(self):
        outcomes = _resolved_cohort(60, seed=100)
        report = _evidence_report(outcomes, label="ser-a")
        a = StrategyIntelligenceEngine(
            StrategyIntelligenceConfig(label="ser-a"),
        ).assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        back = deserialize_assessment(serialize_assessment(a))
        assert back == a

    def test_assessment_round_trip_preserves_id(self):
        outcomes = _resolved_cohort(60, seed=101)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        back = deserialize_assessment(serialize_assessment(a))
        assert back.assessment_id == a.assessment_id
        assert back.observed_performance == a.observed_performance
        assert back.evidence_strength == a.evidence_strength
        assert back.assessment_status == a.assessment_status
        assert back.interpretation == a.interpretation
        assert back.limitations == a.limitations
        assert back.metadata == a.metadata

    def test_comparison_round_trip(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=102)
            + _resolved_cohort(60, direction="SHORT", seed=103)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        back = deserialize_comparison(serialize_comparison(cmp))
        assert back == cmp
        assert back.comparison_id == cmp.comparison_id
        assert back.metrics == cmp.metrics
        assert back.notes == cmp.notes

    def test_lookup_round_trip_matched(self):
        outcomes = _resolved_cohort(60, seed=104)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        back = deserialize_lookup(serialize_lookup(lk))
        assert back == lk
        assert back.lookup_id == lk.lookup_id
        assert back.match_status == lk.match_status
        assert back.assessment == lk.assessment

    def test_lookup_round_trip_no_match(self):
        outcomes = _resolved_cohort(60, seed=105)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="NONEXISTENT"),
        )
        back = deserialize_lookup(serialize_lookup(lk))
        assert back == lk
        assert back.matched_cohort is None
        assert back.assessment is None

    def test_bytes_round_trip(self):
        outcomes = _resolved_cohort(60, seed=106)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        back = deserialize_assessment(
            serialize_assessment_bytes(a).decode("utf-8"),
        )
        assert back == a

    def test_comparison_bytes_round_trip(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=107)
            + _resolved_cohort(60, direction="SHORT", seed=108)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        back = deserialize_comparison(
            serialize_comparison_bytes(cmp).decode("utf-8"),
        )
        assert back == cmp

    def test_lookup_bytes_round_trip(self):
        outcomes = _resolved_cohort(60, seed=109)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        back = deserialize_lookup(
            serialize_lookup_bytes(lk).decode("utf-8"),
        )
        assert back == lk

    def test_deterministic_bytes(self):
        outcomes = _resolved_cohort(60, seed=110)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        assert serialize_assessment(a) == serialize_assessment(a)
        assert canonical_assessment_json(a) == serialize_assessment(a)

    def test_deterministic_comparison_bytes(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=111)
            + _resolved_cohort(60, direction="SHORT", seed=112)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        assert serialize_comparison(cmp) == serialize_comparison(cmp)
        assert canonical_comparison_json(cmp) == serialize_comparison(cmp)

    def test_deterministic_lookup_bytes(self):
        outcomes = _resolved_cohort(60, seed=113)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        assert serialize_lookup(lk) == serialize_lookup(lk)
        assert canonical_lookup_json(lk) == serialize_lookup(lk)

    def test_header_schema_version(self):
        outcomes = _resolved_cohort(60, seed=114)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        header = parse_strategy_header(serialize_assessment(a))
        assert header["schema_version"] == STRATEGY_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        outcomes = _resolved_cohort(60, seed=115)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess_cohort(
            _find_cohort_in_report(report, SETUP_SPEC, "TREND_CONTINUATION"),
        )
        bad = serialize_assessment(a).replace(
            '"schema_version": 1', '"schema_version": 999',
        )
        with pytest.raises(ValueError):
            deserialize_assessment(bad)

    def test_comparison_future_schema_rejected(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=116)
            + _resolved_cohort(60, direction="SHORT", seed=117)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        bad = serialize_comparison(cmp).replace(
            '"schema_version": 1', '"schema_version": 999',
        )
        with pytest.raises(ValueError):
            deserialize_comparison(bad)

    def test_lookup_future_schema_rejected(self):
        outcomes = _resolved_cohort(60, seed=118)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        bad = serialize_lookup(lk).replace(
            '"schema_version": 1', '"schema_version": 999',
        )
        with pytest.raises(ValueError):
            deserialize_lookup(bad)


# ============================================================
# K. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_outcomes_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=120)
        originals = [
            (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
        ]
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        eng.compare(
            report, DIRECTION_SPEC, "LONG", "LONG",
        )
        after = [
            (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
        ]
        assert originals == after

    def test_evidence_report_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=121)
        report = _evidence_report(outcomes)
        original_breakdowns = tuple(
            (b.spec, tuple((c.key, c.strength) for c in b.cohorts))
            for b in report.breakdowns
        )
        eng = StrategyIntelligenceEngine()
        eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        after_breakdowns = tuple(
            (b.spec, tuple((c.key, c.strength) for c in b.cohorts))
            for b in report.breakdowns
        )
        assert original_breakdowns == after_breakdowns

    def test_profile_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=122)
        report = _evidence_report(outcomes)
        profile = OpportunityProfile(setup_type="TREND_CONTINUATION")
        original = (profile.setup_type, profile.direction, profile.rank)
        StrategyIntelligenceEngine().lookup(report, profile)
        assert (profile.setup_type, profile.direction, profile.rank) == original

    def test_reused_statistics_object_identity_preserved(self):
        outcomes = _resolved_cohort(60, seed=123)
        report = _evidence_report(outcomes)
        cohort = _find_cohort_in_report(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        a = StrategyIntelligenceEngine().assess_cohort(cohort)
        # The statistics object is referenced, not copied/mutated.
        assert a.observed_performance is cohort.statistics
        assert a.evidence_strength is cohort.strength


# ============================================================
# L. BOTH_TOUCHED HANDLING
# ============================================================


class TestBothTouched:
    def test_both_touched_win_rate_unavailable(self):
        outcomes = [
            _outcome(OutcomeStatus.BOTH_TOUCHED, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        # BOTH_TOUCHED all direction LONG/setup TREND_CONTINUATION.
        a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a is not None
        assert a.observed_performance.win_rate is None
        assert a.observed_performance.total_realized_r is None
        assert a.observed_performance.profit_factor is None
        assert a.observed_performance.valid_r_count == 0
        assert a.observed_performance.both_touched == 40

    def test_both_touched_mentioned_in_limitations(self):
        outcomes = [
            _outcome(OutcomeStatus.BOTH_TOUCHED, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert "BOTH_TOUCHED" in a.limitations


# ============================================================
# M. NO_GEOMETRY HANDLING
# ============================================================


class TestNoGeometry:
    def test_no_geometry_metrics_unavailable(self):
        outcomes = [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a is not None
        assert a.observed_performance.profit_factor is None
        assert a.observed_performance.average_mfe is None
        assert a.observed_performance.average_realized_r is None
        assert a.observed_performance.no_geometry == 40

    def test_no_geometry_mentioned_in_limitations(self):
        outcomes = [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert "NO_GEOMETRY" in a.limitations


# ============================================================
# N. INSUFFICIENT_DATA HANDLING
# ============================================================


class TestInsufficientData:
    def test_insufficient_data_metrics_unavailable(self):
        outcomes = [
            _outcome(
                OutcomeStatus.INSUFFICIENT_DATA, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a is not None
        assert a.observed_performance.win_rate is None
        assert a.observed_performance.total_realized_r is None
        assert a.observed_performance.insufficient_data == 40

    def test_insufficient_data_mentioned_in_limitations(self):
        outcomes = [
            _outcome(
                OutcomeStatus.INSUFFICIENT_DATA, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert "INSUFFICIENT_DATA" in a.limitations


# ============================================================
# O. NO-LOOK-AHEAD PROTECTION
# ============================================================


class TestNoLookAhead:
    def test_engine_does_not_accept_candles(self):
        # The public API takes a HistoricalEvidenceReport, never
        # candles. Confirmed by signature inspection.
        import inspect

        eng = StrategyIntelligenceEngine()
        sig = inspect.signature(eng.assess)
        assert "candles" not in sig.parameters
        assert "report" in sig.parameters
        sig2 = inspect.signature(eng.lookup)
        assert "candles" not in sig2.parameters

    def test_works_with_outcome_evaluator_patched_to_raise(self):
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        outcomes = _resolved_cohort(60, seed=130)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()

        original = OutcomeEvaluator.evaluate

        def boom(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("must not re-evaluate outcomes")

        OutcomeEvaluator.evaluate = boom  # type: ignore[method-assign]
        try:
            a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
            assert a is not None
            lk = eng.lookup(
                report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
            )
            assert lk.matched is True
        finally:
            OutcomeEvaluator.evaluate = original  # type: ignore[method-assign]

    def test_works_with_pipeline_patched_to_raise(self):
        outcomes = _resolved_cohort(60, seed=131)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()

        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):  # noqa: ANN001
            raise RuntimeError("must not re-run pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[method-assign]
        try:
            a = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
            assert a is not None
            cmp = eng.compare(report, SETUP_SPEC, "TREND_CONTINUATION", "TREND_CONTINUATION")
            assert cmp.both_present is True
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[method-assign]

    def test_no_future_candles_inspected(self):
        # Building an assessment from an already-computed report does
        # not depend on any candle input at all; mutating unrelated
        # future candles cannot change the assessment.
        outcomes = _resolved_cohort(60, seed=132)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a1 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        # Construct and mutate unrelated future candles.
        candles = [
            OHLCVCandle(
                timestamp=_EPOCH + timedelta(days=i),
                open=100.0, high=110.0, low=95.0, close=105.0, volume=1.0,
            )
            for i in range(10)
        ]
        mutated = [
            OHLCVCandle(
                timestamp=c.timestamp, open=c.open, high=200.0,
                low=c.low, close=c.close, volume=c.volume,
            )
            for c in candles
        ]
        del candles, mutated  # not passed anywhere
        a2 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a2 == a1


# ============================================================
# P. REPEATED EVALUATION IDENTITY
# ============================================================


class TestRepeatedEvaluation:
    def test_repeated_assessment_identical(self):
        outcomes = _resolved_cohort(60, seed=140)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        a1 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        a2 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        assert a1 == a2

    def test_repeated_lookup_identical(self):
        outcomes = _resolved_cohort(60, seed=141)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk1 = eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        lk2 = eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        assert lk1 == lk2

    def test_repeated_comparison_identical(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=142)
            + _resolved_cohort(60, direction="SHORT", seed=143)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        c1 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        c2 = eng.compare(report, DIRECTION_SPEC, "LONG", "SHORT")
        assert c1 == c2


# ============================================================
# Q. SHUFFLED-INPUT DETERMINISM
# ============================================================


class TestShuffleDeterminism:
    def test_shuffled_outcomes_same_assessment_id(self):
        # The 11Y evidence id is shuffle-invariant (sorted identities);
        # the 11Z assessment id is derived from the cohort's spec/key/
        # stats/strength, which are themselves shuffle-invariant.
        outcomes = _resolved_cohort(60, seed=150)
        report = _evidence_report(outcomes)
        report_shuffled = _evidence_report(list(reversed(outcomes)))
        eng = StrategyIntelligenceEngine()
        a1 = eng.assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        a2 = eng.assess(report_shuffled, SETUP_SPEC, "TREND_CONTINUATION")
        assert a1.assessment_id == a2.assessment_id
        assert a1.observed_performance == a2.observed_performance

    def test_shuffled_outcomes_same_lookup_id(self):
        outcomes = _resolved_cohort(60, seed=151)
        report = _evidence_report(outcomes)
        report_shuffled = _evidence_report(list(reversed(outcomes)))
        eng = StrategyIntelligenceEngine()
        lk1 = eng.lookup(report, OpportunityProfile(setup_type="TREND_CONTINUATION"))
        lk2 = eng.lookup(
            report_shuffled, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        assert lk1.lookup_id == lk2.lookup_id


# ============================================================
# R. EXISTING SPRINT 11X ANALYTICS REMAIN UNCHANGED
# ============================================================


class TestSprintXUnchanged:
    def test_performance_engine_still_works(self):
        outcomes = _resolved_cohort(60, seed=160)
        perf = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(),
        ).analyze(outcomes)
        assert perf.outcome_count == 60

    def test_strategy_layer_does_not_touch_performance_engine(self):
        outcomes = _resolved_cohort(60, seed=161)
        report = _evidence_report(outcomes)
        StrategyIntelligenceEngine().assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        perf = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(),
        ).analyze(outcomes)
        assert perf.outcome_count == 60


# ============================================================
# S. EXISTING SPRINT 11Y EVIDENCE REMAINS UNCHANGED
# ============================================================


class TestSprintYUnchanged:
    def test_evidence_engine_still_works(self):
        outcomes = _resolved_cohort(60, seed=170)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.sample_count == 60

    def test_strategy_layer_does_not_touch_evidence_engine(self):
        outcomes = _resolved_cohort(60, seed=171)
        cfg = EvidenceConfig(label="y-unchanged")
        report = HistoricalEvidenceEngine(cfg).evaluate(outcomes)
        StrategyIntelligenceEngine().assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        report2 = HistoricalEvidenceEngine(cfg).evaluate(outcomes)
        assert report2 == report


# ============================================================
# T. EXISTING PIPELINE REGRESSION
# ============================================================


class TestPipelineRegression:
    def test_pipeline_baseline_4_3(self):
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3

    def test_strategy_layer_does_not_touch_pipeline(self):
        outcomes = _resolved_cohort(60, seed=180)
        report = _evidence_report(outcomes)
        StrategyIntelligenceEngine().assess(report, SETUP_SPEC, "TREND_CONTINUATION")
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3


# ============================================================
# U. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        outcomes = _resolved_cohort(60, seed=190)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        text = StrategyIntelligenceFormatter().format(a)
        assert isinstance(text, str)
        assert text

    def test_format_required_sections(self):
        outcomes = _resolved_cohort(60, seed=191)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        text = StrategyIntelligenceFormatter().format(a)
        for section in (
            "Strategy Intelligence / Evidence Assessment Report",
            "Observed Historical Result",
            "Evidence Strength",
            "Strategy Interpretation",
            "Limitations",
        ):
            assert section in text

    def test_format_distinct_observed_evidence_interpretation(self):
        outcomes = _resolved_cohort(60, seed=192)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        text = StrategyIntelligenceFormatter().format(a)
        # The three concerns appear as distinct labeled sections.
        assert "Observed Historical Result" in text
        assert "Evidence Strength" in text
        assert "Strategy Interpretation" in text
        assert a.assessment_status.name in text
        assert a.evidence_strength.name in text

    def test_format_warning_present(self):
        outcomes = _resolved_cohort(60, seed=193)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        text = StrategyIntelligenceFormatter().format(a)
        assert "does not guarantee future performance" in text

    def test_format_unavailable_shown(self):
        outcomes = [
            _outcome(OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        text = StrategyIntelligenceFormatter().format(a)
        assert "unavailable" in text

    def test_format_comparison_returns_str(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=194)
            + _resolved_cohort(60, direction="SHORT", seed=195)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        text = StrategyIntelligenceFormatter().format_comparison(cmp)
        assert isinstance(text, str)
        assert "Cohort Comparison Report" in text
        assert "Metric Comparison" in text
        assert "WARNING" in text

    def test_format_lookup_returns_str(self):
        outcomes = _resolved_cohort(60, seed=196)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        text = StrategyIntelligenceFormatter().format_lookup(lk)
        assert isinstance(text, str)
        assert "Current Opportunity Evidence Lookup Report" in text
        assert "WARNING" in text

    def test_format_lookup_no_match(self):
        outcomes = _resolved_cohort(60, seed=197)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="NONEXISTENT"),
        )
        text = StrategyIntelligenceFormatter().format_lookup(lk)
        assert "No matching historical cohort found" in text

    def test_format_deterministic(self):
        outcomes = _resolved_cohort(60, seed=198)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        f = StrategyIntelligenceFormatter()
        assert f.format(a) == f.format(a)

    def test_format_precision_negative_rejected(self):
        with pytest.raises(ValueError):
            StrategyIntelligenceFormatter(precision=-1)


# ============================================================
# V. NO STATISTICAL-SUPERIORITY LANGUAGE IN COMPARISON
# ============================================================


class TestNoStatisticalLanguage:
    def test_comparison_notes_no_superiority(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", win_fraction=0.7, seed=200)
            + _resolved_cohort(
                60, direction="SHORT", win_fraction=0.3, seed=201,
            )
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        joined = " ".join(cmp.notes) + " " + cmp.disclaimer
        assert "statistically superior" not in joined.lower()
        assert "statistically significant" not in joined.lower()
        assert "No statistical procedure" in joined

    def test_assessment_no_statistical_language(self):
        outcomes = _resolved_cohort(5, seed=202)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a is not None
        combined = a.interpretation + " " + a.limitations
        assert "statistically significant" not in combined.lower()
        assert "No statistical hypothesis test was performed" in a.limitations


# ============================================================
# W. CONFIGURATION VALIDATION
# ============================================================


class TestConfigValidation:
    def test_config_defaults(self):
        cfg = StrategyIntelligenceConfig()
        assert cfg.favorable_win_rate == 0.55
        assert cfg.favorable_profit_factor == 1.3
        assert cfg.favorable_avg_r == 0.1
        assert cfg.lookup_max_dimensions == 2

    def test_config_frozen(self):
        cfg = StrategyIntelligenceConfig()
        with pytest.raises(Exception):
            cfg.favorable_win_rate = 0.9  # type: ignore[misc]

    def test_config_has_slots(self):
        cfg = StrategyIntelligenceConfig()
        with pytest.raises(AttributeError):
            cfg.bogus = 1  # type: ignore[attr-defined]

    def test_config_win_rate_bounds(self):
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(favorable_win_rate=-0.1)
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(favorable_win_rate=1.1)

    def test_config_profit_factor_positive(self):
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(favorable_profit_factor=0.0)

    def test_config_avg_r_non_negative(self):
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(favorable_avg_r=-0.1)

    def test_config_lookup_max_dimensions_bounds(self):
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(lookup_max_dimensions=0)
        with pytest.raises(ValueError):
            StrategyIntelligenceConfig(lookup_max_dimensions=3)

    def test_config_snapshot_sorted(self):
        cfg = StrategyIntelligenceConfig()
        snap = cfg.snapshot()
        assert snap == tuple(sorted(snap))
        names = [k for k, _ in snap]
        assert "favorable_win_rate" in names
        assert "lookup_max_dimensions" in names

    def test_lookup_max_dimensions_limits_specificity(self):
        # With max=1 the lookup must NOT select a 2-dim composite spec.
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=210)
            + _resolved_cohort(60, direction="SHORT", seed=211)
        )
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine(
            StrategyIntelligenceConfig(lookup_max_dimensions=1),
        )
        lk = eng.lookup(
            report,
            OpportunityProfile(setup_type="TREND_CONTINUATION", direction="LONG"),
        )
        assert lk.matched is True
        assert len(lk.matched_spec.dimensions) == 1


# ============================================================
# X. MODEL IMMUTABILITY (FROZEN + SLOTS)
# ============================================================


class TestModelImmutability:
    def test_assessment_frozen(self):
        outcomes = _resolved_cohort(60, seed=220)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        with pytest.raises(Exception):
            a.interpretation = "mutated"  # type: ignore[misc]

    def test_assessment_has_slots(self):
        outcomes = _resolved_cohort(60, seed=221)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        with pytest.raises(AttributeError):
            a.bogus = 1  # type: ignore[attr-defined]

    def test_comparison_frozen(self):
        outcomes = (
            _resolved_cohort(60, direction="LONG", seed=222)
            + _resolved_cohort(60, direction="SHORT", seed=223)
        )
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "SHORT",
        )
        with pytest.raises(Exception):
            cmp.disclaimer = "mutated"  # type: ignore[misc]

    def test_profile_frozen(self):
        p = OpportunityProfile(setup_type="TREND_CONTINUATION")
        with pytest.raises(Exception):
            p.setup_type = "x"  # type: ignore[misc]

    def test_profile_has_slots(self):
        p = OpportunityProfile()
        with pytest.raises(AttributeError):
            p.bogus = 1  # type: ignore[attr-defined]

    def test_comparison_metric_frozen(self):
        m = CohortComparisonMetric(
            name="Win Rate", value_a=0.6, value_b=0.4, delta=0.2, note="x",
        )
        with pytest.raises(Exception):
            m.note = "y"  # type: ignore[misc]

    def test_lookup_frozen(self):
        outcomes = _resolved_cohort(60, seed=224)
        report = _evidence_report(outcomes)
        lk = StrategyIntelligenceEngine().lookup(
            report, OpportunityProfile(setup_type="TREND_CONTINUATION"),
        )
        with pytest.raises(Exception):
            lk.limitations = "mutated"  # type: ignore[misc]


# ============================================================
# Y. SAMPLE-SIZE HARD GATE PROPAGATION
# ============================================================


class TestSampleHardGate:
    def test_small_sample_never_strong_support(self):
        # 5 wins out of 5 -> observed win rate 1.0, but sample too small.
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=230)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a is not None
        assert a.observed_performance.win_rate == 1.0
        assert a.evidence_strength == EvidenceStrength.INSUFFICIENT
        assert a.assessment_status == (
            StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE
        )
        assert a.assessment_status != (
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT
        )
        assert not a.has_evidence

    def test_insufficient_not_actionable(self):
        outcomes = _resolved_cohort(5, seed=231)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a.assessment_status.is_actionable is False

    def test_supported_requires_meaningful_sample(self):
        outcomes = _resolved_cohort(60, win_fraction=0.5, seed=232)
        report = _evidence_report(outcomes)
        a = StrategyIntelligenceEngine().assess(
            report, SETUP_SPEC, "TREND_CONTINUATION",
        )
        assert a.is_supported is True


# ============================================================
# Z. HONEST FALLBACKS (NO FABRICATION)
# ============================================================


class TestHonestFallbacks:
    def test_missing_assessment_returns_none_not_fake(self):
        outcomes = _resolved_cohort(60, seed=240)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        assert eng.assess(report, SETUP_SPEC, "NONEXISTENT") is None

    def test_missing_lookup_has_no_assessment(self):
        outcomes = _resolved_cohort(60, seed=241)
        report = _evidence_report(outcomes)
        eng = StrategyIntelligenceEngine()
        lk = eng.lookup(report, OpportunityProfile(setup_type="NONEXISTENT"))
        assert lk.assessment is None
        assert lk.matched_cohort is None

    def test_comparison_missing_metric_delta_none(self):
        outcomes = _resolved_cohort(60, direction="LONG", seed=242)
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, DIRECTION_SPEC, "LONG", "NONEXISTENT",
        )
        for m in cmp.metrics:
            assert m.value_b is None
            assert m.delta is None

    def test_unavailable_win_rate_propagates_to_comparison(self):
        # BOTH_TOUCHED cohort -> win_rate None.
        outcomes = [
            _outcome(OutcomeStatus.BOTH_TOUCHED, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, SETUP_SPEC, "TREND_CONTINUATION", "TREND_CONTINUATION",
        )
        win_metric = next(m for m in cmp.metrics if m.name == "Win Rate")
        assert win_metric.value_a is None
        assert win_metric.value_b is None
        assert win_metric.delta is None

    def test_no_fabricated_realized_r_in_comparison(self):
        outcomes = [
            _outcome(OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = _evidence_report(outcomes)
        cmp = StrategyIntelligenceEngine().compare(
            report, SETUP_SPEC, "TREND_CONTINUATION", "TREND_CONTINUATION",
        )
        total_r = next(m for m in cmp.metrics if m.name == "Total Realized R")
        assert total_r.value_a is None
        assert total_r.delta is None


# ============================================================
# END-TO-END: 11V -> 11W -> 11X -> 11Y -> 11Z
# ============================================================


class TestEndToEnd:
    def test_e2e_chain_from_pipeline_replay(self):
        from engine.config.market_scan_config import MarketScanConfig
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.data.historical_adapter import (
            HistoricalAdapterConfig,
            HistoricalDataAdapter,
        )
        from engine.data.historical_fixtures import historical_records
        from engine.intelligence.historical_replay import (
            HistoricalReplayEngine,
            evaluation_times_from_setup_candles,
        )

        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        dataset = adapter.normalize(historical_records())
        times = evaluation_times_from_setup_candles(
            dataset, "15M", count=4, min_history=10,
        )
        if not times:
            pytest.skip("no shared evaluation times in fixture")
        replay = HistoricalReplayEngine(MarketScanConfig()).replay(dataset, times)
        outcome_report = HistoricalOutcomeEngine(
            OutcomeConfig(max_holding_bars=15),
        ).evaluate_replay(replay, dataset)
        replay_outcomes = [
            o for point in outcome_report.points for o in point.outcomes
        ]
        if not replay_outcomes:
            pytest.skip("no eligible outcomes produced by fixture replay")
        evidence = HistoricalEvidenceEngine(EvidenceConfig(label="e2e")).evaluate(
            replay_outcomes,
        )
        eng = StrategyIntelligenceEngine()
        # If any cohort has usable evidence, produce an assessment.
        assessed = 0
        for b in evidence.breakdowns:
            for c in b.cohorts:
                a = eng.assess_cohort(c)
                assert a.assessment_status == EVIDENCE_TO_STRATEGY[c.strength]
                assessed += 1
                if assessed >= 3:
                    break
            if assessed >= 3:
                break
        assert assessed >= 1
        # Lookup by a profile built from one of the outcomes.
        first = replay_outcomes[0]
        profile = OpportunityProfile(
            instrument=first.subject.instrument,
            direction=first.subject.direction,
            setup_type=first.subject.setup_type,
        )
        lk = eng.lookup(evidence, profile)
        assert lk.match_status in (CohortMatchStatus.MATCHED, CohortMatchStatus.NO_MATCH)
        assert deserialize_lookup(serialize_lookup(lk)) == lk
