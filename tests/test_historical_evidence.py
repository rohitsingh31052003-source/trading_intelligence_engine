"""
Tests for the historical evidence / validation layer (Sprint 11Y).

Coverage (mirrors the required test areas):

A.  Basic historical evidence aggregation
B.  Instrument-level evidence
C.  Direction-level evidence
D.  MTF-alignment evidence
E.  Setup-type evidence
F.  Decision evidence
G.  Opportunity-status evidence
H.  Opportunity-rank evidence
I.  Supported factor combinations (composite cohorts)
J.  Insufficient sample detection
K.  Strong / moderate / weak evidence classification
L.  BOTH_TOUCHED handling
M.  NO_GEOMETRY handling
N.  EXPIRED handling
O.  Missing-metadata handling
P.  Missing-performance-metric handling
Q.  Deterministic repeated evaluation
R.  Shuffled input produces identical results
S.  Input objects are not mutated
T.  Serialization round trip
U.  Deterministic ID generation
V.  Stable ordering
W.  Configuration changes affect classification appropriately
X.  Future / unrelated data cannot change existing historical evidence
Y.  Existing Sprint 11X analytics remain unchanged
Z.  Existing Sprint 11V -> 11W -> 11X pipeline regression
AA. Reporting
BB. Configuration validation
CC. Model immutability
DD. Sample size is a hard gate (small sample never strong)
EE. No statistical-significance language
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.intelligence.historical_evidence import (
    SUPPORTED_COHORT_SPECS,
    HistoricalEvidenceEngine,
)
from engine.intelligence.historical_evidence_serialization import (
    EVIDENCE_SCHEMA_VERSION,
    canonical_evidence_json,
    deserialize_evidence,
    parse_evidence_header,
    serialize_evidence,
    serialize_evidence_bytes,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceBreakdown,
    HistoricalEvidenceCohort,
    HistoricalEvidenceReport,
    HistoricalEvidenceSummary,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import BreakdownDimension
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.historical_evidence import HistoricalEvidenceFormatter


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
        scan_id="scan-y",
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


def _find_breakdown(
    report: HistoricalEvidenceReport, *dims: BreakdownDimension,
) -> HistoricalEvidenceBreakdown:
    spec = CohortSpec(dims)
    for b in report.breakdowns:
        if b.spec.dimensions == spec.dimensions:
            return b
    raise AssertionError(f"No breakdown for spec {dims}")


# ============================================================
# A. BASIC HISTORICAL EVIDENCE AGGREGATION
# ============================================================


class TestBasicAggregation:
    def test_empty_input(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate([])
        assert report.is_empty is True
        assert report.summary.sample_count == 0
        assert report.summary.strength == EvidenceStrength.INSUFFICIENT
        assert report.cohort_count == 0
        assert report.evidence_id.startswith("evidence-")

    def test_basic_aggregation(self):
        outcomes = _resolved_cohort(40, seed=1)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.sample_count == 40
        assert report.summary.resolved_count == 40
        assert report.summary.valid_r_count == 40
        # 12 specs by default (7 singles + 5 composites).
        assert len(report.breakdowns) == 12
        assert report.cohort_count > 0

    def test_default_specs_match_supported(self):
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        assert eng.specs == SUPPORTED_COHORT_SPECS
        assert len(SUPPORTED_COHORT_SPECS) == 12


# ============================================================
# B-H. SINGLE-DIMENSION EVIDENCE
# ============================================================


class TestSingleDimensionEvidence:
    @pytest.mark.parametrize(
        "dim,value",
        [
            (BreakdownDimension.INSTRUMENT, "NIFTY"),
            (BreakdownDimension.DIRECTION, "LONG"),
            (BreakdownDimension.MTF_ALIGNMENT, "ALIGNED"),
            (BreakdownDimension.SETUP_TYPE, "TREND_CONTINUATION"),
            (BreakdownDimension.DECISION, "QUALIFIED"),
            (BreakdownDimension.OPPORTUNITY_STATUS, "BEST_OPPORTUNITY"),
            (BreakdownDimension.OPPORTUNITY_RANK, "1"),
        ],
    )
    def test_single_dimension_cohort_present(self, dim, value):
        outcomes = _resolved_cohort(40, seed=2)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, dim)
        keys = [c.key for c in b.cohorts]
        assert value in keys


# ============================================================
# I. SUPPORTED FACTOR COMBINATIONS (COMPOSITE COHORTS)
# ============================================================


class TestCompositeCohorts:
    @pytest.mark.parametrize(
        "dims",
        [
            (BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION),
            (BreakdownDimension.SETUP_TYPE, BreakdownDimension.MTF_ALIGNMENT),
            (BreakdownDimension.DECISION, BreakdownDimension.OPPORTUNITY_RANK),
            (BreakdownDimension.SETUP_TYPE, BreakdownDimension.DECISION),
            (BreakdownDimension.INSTRUMENT, BreakdownDimension.SETUP_TYPE),
        ],
    )
    def test_composite_spec_in_supported(self, dims):
        spec = CohortSpec(dims)
        assert spec in SUPPORTED_COHORT_SPECS

    def test_composite_key_uses_pipe_join(self):
        outcomes = _resolved_cohort(40, seed=3) + _resolved_cohort(
            40, instrument="TCS", direction="SHORT", setup_type="BREAKOUT",
            mtf_alignment="CONFLICTING", seed=4,
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(
            report, BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION,
        )
        keys = [c.key for c in b.cohorts]
        assert "TREND_CONTINUATION|LONG" in keys
        assert "BREAKOUT|SHORT" in keys

    def test_cohort_spec_rejects_more_than_two_dims(self):
        with pytest.raises(ValueError):
            CohortSpec((
                BreakdownDimension.INSTRUMENT,
                BreakdownDimension.DIRECTION,
                BreakdownDimension.SETUP_TYPE,
            ))

    def test_cohort_spec_rejects_empty(self):
        with pytest.raises(ValueError):
            CohortSpec(())


# ============================================================
# J. INSUFFICIENT SAMPLE DETECTION
# ============================================================


class TestInsufficientSample:
    def test_single_outcome_insufficient(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            [_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)],
        )
        assert report.summary.strength == EvidenceStrength.INSUFFICIENT

    def test_below_min_sample_total_insufficient(self):
        # 29 outcomes, default min_sample_total 30 -> INSUFFICIENT even if
        # all winners.
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, ts=_EPOCH + timedelta(days=i))
            for i in range(29)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.INSUFFICIENT
        assert "below the configured minimum" in report.summary.rationale


# ============================================================
# K. STRONG / MODERATE / WEAK EVIDENCE CLASSIFICATION
# ============================================================


class TestStrengthClassification:
    def test_strong(self):
        outcomes = _resolved_cohort(60, seed=10)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.STRONG

    def test_moderate(self):
        # 40 resolved -> above min (30/10/10) but below strong (50/30/30).
        outcomes = _resolved_cohort(40, seed=11)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.MODERATE

    def test_weak_sample_ok_but_resolved_low(self):
        # 30 NO_GEOMETRY outcomes: sample 30 (>= min) but resolved 0.
        outcomes = [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(30)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.WEAK

    def test_all_four_strengths_distinct(self):
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        insufficient = eng.evaluate(
            [_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)],
        )
        weak = eng.evaluate(
            [_outcome(OutcomeStatus.NO_GEOMETRY, mfe=None, mae=None, mfe_r=None, mae_r=None) for _ in range(30)],
        )
        moderate = eng.evaluate(_resolved_cohort(40, seed=11))
        strong = eng.evaluate(_resolved_cohort(60, seed=10))
        assert insufficient.summary.strength == EvidenceStrength.INSUFFICIENT
        assert weak.summary.strength == EvidenceStrength.WEAK
        assert moderate.summary.strength == EvidenceStrength.MODERATE
        assert strong.summary.strength == EvidenceStrength.STRONG


# ============================================================
# DD. SAMPLE SIZE IS A HARD GATE (small sample never strong)
# ============================================================


class TestSampleHardGate:
    def test_one_trade_2R_not_strong(self):
        """1 trade at +2R must NOT be stronger than 100 modest trades."""
        small = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            [_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)],
        )
        large = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(100, win_fraction=0.5, seed=20),
        )
        assert small.summary.strength == EvidenceStrength.INSUFFICIENT
        assert large.summary.strength == EvidenceStrength.STRONG
        assert large.summary.strength.rank_value < small.summary.strength.rank_value

    def test_small_sample_insufficient_regardless_of_winrate(self):
        # 5 trades all winners -> still INSUFFICIENT.
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, ts=_EPOCH + timedelta(days=i))
            for i in range(5)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.INSUFFICIENT


# ============================================================
# L. BOTH_TOUCHED HANDLING
# ============================================================


class TestBothTouched:
    def test_both_touched_excluded_from_win_loss(self):
        outcomes = [_outcome(OutcomeStatus.BOTH_TOUCHED) for _ in range(30)]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        s = report.summary.statistics
        assert s.both_touched == 30
        assert s.win_rate is None  # no resolved target/stop
        assert s.total_realized_r is None  # realized_r is None
        assert s.valid_r_count == 0
        # Resolved (BOTH_TOUCHED counts as resolved) but no valid R -> WEAK.
        assert report.summary.strength == EvidenceStrength.WEAK

    def test_both_touched_does_not_become_win_or_loss(self):
        outcomes = [
            _outcome(OutcomeStatus.BOTH_TOUCHED),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
        ] * 20
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        s = report.summary.statistics
        # win rate computed over target+stop only (40 each).
        assert s.win_rate == pytest.approx(0.5)


# ============================================================
# M. NO_GEOMETRY HANDLING
# ============================================================


class TestNoGeometry:
    def test_no_geometry_excluded_from_r(self):
        outcomes = [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(30)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        s = report.summary.statistics
        assert s.no_geometry == 30
        assert s.total_realized_r is None
        assert s.profit_factor is None
        assert s.average_mfe is None
        assert report.summary.strength == EvidenceStrength.WEAK


# ============================================================
# N. EXPIRED HANDLING
# ============================================================


class TestExpired:
    def test_expired_mark_to_close_contributes_r(self):
        outcomes = [
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3, ts=_EPOCH + timedelta(days=i))
            for i in range(40)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        s = report.summary.statistics
        assert s.expired == 40
        assert s.valid_r_count == 40
        assert s.total_realized_r == pytest.approx(12.0)
        # EXPIRED contributes resolved but no target/stop -> win_rate None.
        assert s.win_rate is None


# ============================================================
# O. MISSING-METADATA HANDLING
# ============================================================


class TestMissingMetadata:
    def test_empty_metadata_groups_under_unavailable_sentinel(self):
        # Outcomes with empty setup_type / mtf_alignment group under "".
        outcomes = [
            HistoricalOutcome(
                subject=OutcomeSubject(
                    instrument="NIFTY", direction="LONG",
                    evaluation_timestamp=_EPOCH + timedelta(days=i),
                    entry=100.0, stop=95.0, target=110.0,
                    decision_classification="QUALIFIED", decision_score=70,
                    opportunity_status="BEST_OPPORTUNITY", rank=1,
                    scan_id="s", setup_timeframe="15M",
                    setup_type="", mtf_alignment="",
                ),
                outcome_status=OutcomeStatus.TARGET_HIT, realized_r=2.0,
                risk=5.0,
            )
            for i in range(40)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, BreakdownDimension.SETUP_TYPE)
        assert b.cohorts[0].key == ""
        assert b.cohorts[0].sample_count == 40

    def test_missing_rank_groups_under_unavailable(self):
        outcomes = [
            _outcome(
                OutcomeStatus.TARGET_HIT, realized_r=2.0, rank=0,
                ts=_EPOCH + timedelta(days=i),
            )
            for i in range(40)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, BreakdownDimension.OPPORTUNITY_RANK)
        # rank 0 -> "" unavailable key.
        assert b.cohorts[0].key == ""


# ============================================================
# P. MISSING-PERFORMANCE-METRIC HANDLING
# ============================================================


class TestMissingMetrics:
    def test_no_metrics_when_no_valid_r(self):
        outcomes = [_outcome(OutcomeStatus.BOTH_TOUCHED) for _ in range(30)]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        cohort = report.breakdowns[0].cohorts[0]
        assert cohort.statistics.average_realized_r is None
        assert cohort.statistics.median_realized_r is None
        assert cohort.statistics.profit_factor is None
        assert cohort.statistics.win_rate is None


# ============================================================
# Q. DETERMINISTIC REPEATED EVALUATION
# ============================================================


class TestDeterminism:
    def test_repeated_evaluation_identical(self):
        outcomes = _resolved_cohort(50, seed=30)
        eng = HistoricalEvidenceEngine(EvidenceConfig(label="det"))
        r1 = eng.evaluate(outcomes)
        r2 = eng.evaluate(outcomes)
        assert r1 == r2
        assert r1.evidence_id == r2.evidence_id


# ============================================================
# R. SHUFFLED INPUT PRODUCES IDENTICAL RESULTS
# ============================================================


class TestShuffleInvariance:
    def test_shuffled_same_id_and_summary(self):
        outcomes = _resolved_cohort(50, seed=31)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        r1 = eng.evaluate(outcomes)
        r2 = eng.evaluate(list(reversed(outcomes)))
        assert r1.evidence_id == r2.evidence_id
        assert r1.summary == r2.summary

    def test_shuffled_breakdowns_identical(self):
        outcomes = _resolved_cohort(60, seed=32) + _resolved_cohort(
            60, instrument="TCS", direction="SHORT", seed=33,
        )
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        r1 = eng.evaluate(outcomes)
        shuffled = outcomes[:]
        random.Random(99).shuffle(shuffled)
        r2 = eng.evaluate(shuffled)
        assert r1 == r2


# ============================================================
# S. INPUT OBJECTS ARE NOT MUTATED
# ============================================================


class TestNoMutation:
    def test_outcomes_not_mutated(self):
        outcomes = _resolved_cohort(40, seed=40)
        originals = [
            (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
        ]
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        eng.evaluate(outcomes)
        after = [
            (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
        ]
        assert originals == after

    def test_input_list_not_mutated(self):
        outcomes = _resolved_cohort(40, seed=41)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        eng.evaluate(outcomes)
        assert len(outcomes) == 40


# ============================================================
# T. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_round_trip_full(self):
        outcomes = _resolved_cohort(50, seed=50)
        report = HistoricalEvidenceEngine(EvidenceConfig(label="ser")).evaluate(outcomes)
        back = deserialize_evidence(serialize_evidence(report))
        assert back == report

    def test_round_trip_preserves_id_and_summary(self):
        outcomes = _resolved_cohort(50, seed=51)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        back = deserialize_evidence(serialize_evidence(report))
        assert back.evidence_id == report.evidence_id
        assert back.summary == report.summary

    def test_round_trip_preserves_breakdowns(self):
        outcomes = _resolved_cohort(60, seed=52) + _resolved_cohort(
            60, instrument="TCS", direction="SHORT", seed=53,
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        back = deserialize_evidence(serialize_evidence(report))
        assert back.breakdowns == report.breakdowns

    def test_round_trip_preserves_config_snapshot(self):
        outcomes = _resolved_cohort(40, seed=54)
        cfg = EvidenceConfig(label="cfg-snap")
        report = HistoricalEvidenceEngine(cfg).evaluate(outcomes)
        back = deserialize_evidence(serialize_evidence(report))
        assert back.config_snapshot == report.config_snapshot

    def test_round_trip_preserves_strengths(self):
        outcomes = _resolved_cohort(60, seed=55)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        back = deserialize_evidence(serialize_evidence(report))
        assert back.summary.strength == report.summary.strength
        for b1, b2 in zip(report.breakdowns, back.breakdowns):
            for c1, c2 in zip(b1.cohorts, b2.cohorts):
                assert c1.strength == c2.strength

    def test_bytes_round_trip(self):
        outcomes = _resolved_cohort(40, seed=56)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        back = deserialize_evidence(
            serialize_evidence_bytes(report).decode("utf-8"),
        )
        assert back == report

    def test_deterministic_bytes(self):
        outcomes = _resolved_cohort(40, seed=57)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert serialize_evidence(report) == serialize_evidence(report)
        assert canonical_evidence_json(report) == serialize_evidence(report)

    def test_header_schema_version(self):
        outcomes = _resolved_cohort(40, seed=58)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        header = parse_evidence_header(serialize_evidence(report))
        assert header["schema_version"] == EVIDENCE_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        outcomes = _resolved_cohort(40, seed=59)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        payload = serialize_evidence(report)
        bad = payload.replace(
            '"schema_version": 1', '"schema_version": 999',
        )
        with pytest.raises(ValueError):
            deserialize_evidence(bad)


# ============================================================
# U. DETERMINISTIC ID GENERATION
# ============================================================


class TestDeterministicId:
    def test_id_prefix(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=60),
        )
        assert report.evidence_id.startswith("evidence-")

    def test_different_inputs_different_id(self):
        r1 = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=61),
        )
        r2 = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, win_fraction=0.4, seed=62),
        )
        assert r1.evidence_id != r2.evidence_id

    def test_different_label_different_id(self):
        outcomes = _resolved_cohort(40, seed=63)
        r1 = HistoricalEvidenceEngine(EvidenceConfig(label="a")).evaluate(outcomes)
        r2 = HistoricalEvidenceEngine(EvidenceConfig(label="b")).evaluate(outcomes)
        assert r1.evidence_id != r2.evidence_id

    def test_different_specs_different_id(self):
        outcomes = _resolved_cohort(40, seed=64)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        r1 = eng.evaluate(outcomes, specs=[CohortSpec((BreakdownDimension.INSTRUMENT,))])
        r2 = eng.evaluate(outcomes, specs=[CohortSpec((BreakdownDimension.DIRECTION,))])
        assert r1.evidence_id != r2.evidence_id


# ============================================================
# V. STABLE ORDERING
# ============================================================


class TestStableOrdering:
    def test_instrument_sorted_lexicographically(self):
        outcomes = (
            _resolved_cohort(40, instrument="TCS", seed=70)
            + _resolved_cohort(40, instrument="NIFTY", seed=71)
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, BreakdownDimension.INSTRUMENT)
        keys = [c.key for c in b.cohorts]
        assert keys == sorted(keys)

    def test_direction_canonical_long_before_short(self):
        outcomes = (
            _resolved_cohort(40, direction="LONG", seed=72)
            + _resolved_cohort(40, direction="SHORT", seed=73)
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, BreakdownDimension.DIRECTION)
        keys = [c.key for c in b.cohorts]
        assert keys.index("LONG") < keys.index("SHORT")

    def test_rank_numeric_ascending_unavailable_last(self):
        outcomes = (
            _resolved_cohort(40, rank=2, seed=74)
            + _resolved_cohort(40, rank=1, seed=75)
            + _resolved_cohort(40, rank=0, seed=76)
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(report, BreakdownDimension.OPPORTUNITY_RANK)
        keys = [c.key for c in b.cohorts]
        assert keys == ["1", "2", ""]

    def test_composite_order_first_dimension_dominates(self):
        outcomes = (
            _resolved_cohort(40, setup_type="BREAKOUT", direction="SHORT", seed=80)
            + _resolved_cohort(40, setup_type="TREND_CONTINUATION", direction="LONG", seed=81)
        )
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        b = _find_breakdown(
            report, BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION,
        )
        keys = [c.key for c in b.cohorts]
        assert keys.index("TREND_CONTINUATION|LONG") < keys.index("BREAKOUT|SHORT")


# ============================================================
# W. CONFIGURATION CHANGES AFFECT CLASSIFICATION
# ============================================================


class TestConfigAffectsClassification:
    def test_lowering_min_sample_promotes_to_weak(self):
        outcomes = _resolved_cohort(5, seed=90)
        # Default min_sample_total 30 -> INSUFFICIENT.
        default = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert default.summary.strength == EvidenceStrength.INSUFFICIENT
        # Lower thresholds -> at least WEAK (5 resolved, 5 valid R).
        relaxed = HistoricalEvidenceEngine(
            EvidenceConfig(min_sample_total=5, min_resolved=5, min_valid_r=5),
        ).evaluate(outcomes)
        assert relaxed.summary.strength != EvidenceStrength.INSUFFICIENT

    def test_raising_strong_threshold_prevents_strong(self):
        outcomes = _resolved_cohort(60, seed=91)
        strict = HistoricalEvidenceEngine(
            EvidenceConfig(strong_min_sample=1000),
        ).evaluate(outcomes)
        assert strict.summary.strength != EvidenceStrength.STRONG

    def test_config_snapshot_captures_thresholds(self):
        cfg = EvidenceConfig(min_sample_total=25, strong_min_sample=99)
        report = HistoricalEvidenceEngine(cfg).evaluate(_resolved_cohort(40, seed=92))
        snap = dict(report.config_snapshot)
        assert snap["min_sample_total"] == "25"
        assert snap["strong_min_sample"] == "99"


# ============================================================
# X. FUTURE / UNRELATED DATA CANNOT CHANGE EVIDENCE
# ============================================================


class TestNoFutureInfluence:
    def test_unrelated_candles_do_not_change_evidence(self):
        outcomes = _resolved_cohort(40, seed=100)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        before = eng.evaluate(outcomes)
        # Construct unrelated future candles; the evidence layer never
        # reads candles, so the result must be unchanged.
        _unrelated = [
            OHLCVCandle(
                timestamp=_EPOCH + timedelta(days=999),
                open=1.0, high=999.0, low=0.5, close=500.0, volume=99.0,
            ),
        ]
        after = eng.evaluate(outcomes)
        assert before == after

    def test_no_lookahead_via_outcomes(self):
        # The evidence layer consumes already-computed outcomes only;
        # there is no path to future candles.
        outcomes = _resolved_cohort(40, seed=101)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        # The summary statistics derive solely from the outcomes.
        assert report.summary.sample_count == len(outcomes)


# ============================================================
# Y. EXISTING SPRINT 11X ANALYTICS REMAIN UNCHANGED
# ============================================================


class TestSprintXUnchanged:
    def test_performance_analytics_still_importable_and_working(self):
        outcomes = _resolved_cohort(40, seed=110)
        engine = PerformanceAnalyticsEngine(PerformanceAnalyticsConfig())
        analytics = engine.analyze(outcomes)
        assert analytics.outcome_count == 40
        assert analytics.overall.total == 40

    def test_performance_schema_version_unchanged(self):
        from engine.intelligence.performance_serialization import (
            PERFORMANCE_SCHEMA_VERSION,
        )
        assert PERFORMANCE_SCHEMA_VERSION == 1

    def test_evidence_does_not_modify_performance_engine(self):
        # Running evidence engine does not affect a parallel performance
        # analytics run on the same outcomes.
        outcomes = _resolved_cohort(40, seed=111)
        perf = PerformanceAnalyticsEngine(PerformanceAnalyticsConfig()).analyze(outcomes)
        HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        perf2 = PerformanceAnalyticsEngine(PerformanceAnalyticsConfig()).analyze(outcomes)
        assert perf == perf2


# ============================================================
# Z. EXISTING 11V -> 11W -> 11X PIPELINE REGRESSION
# ============================================================


class TestPipelineRegression:
    def test_pipeline_baseline_signals_trades(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )
        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
            trending_dataset(),
        )
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3

    def test_end_to_end_e2e_chain(self):
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.config.market_scan_config import MarketScanConfig
        from engine.data.historical_adapter import (
            HistoricalAdapterConfig,
            HistoricalDataAdapter,
        )
        from engine.data.historical_fixtures import historical_records
        from engine.intelligence.historical_outcome import HistoricalOutcomeEngine
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
        outcomes = [
            o for p in outcome_report.points for o in p.outcomes
        ]
        # Evidence engine consumes the chain output without error.
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.evidence_id.startswith("evidence-")


# ============================================================
# AA. REPORTING
# ============================================================


class TestReporting:
    def test_returns_str(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=120),
        )
        assert isinstance(HistoricalEvidenceFormatter().format(report), str)

    def test_required_sections(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=121),
        )
        text = HistoricalEvidenceFormatter().format(report)
        assert "Historical Evidence Report" in text
        assert "Overall Evidence Summary" in text
        assert "Cohort Summary" in text
        assert "Rationale" in text

    def test_warning_present(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=122),
        )
        text = HistoricalEvidenceFormatter().format(report)
        assert "does not guarantee future performance" in text

    def test_no_predictive_language(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=123),
        )
        text = HistoricalEvidenceFormatter().format(report)
        for bad in ("guaranteed profit", "will rise", "will fall", "is profitable"):
            assert bad not in text.lower()

    def test_empty_report(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate([])
        text = HistoricalEvidenceFormatter().format(report)
        assert isinstance(text, str)
        assert "Historical Evidence Report" in text

    def test_deterministic_report(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=124),
        )
        f = HistoricalEvidenceFormatter()
        assert f.format(report) == f.format(report)

    def test_strength_shown(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(60, seed=125),
        )
        text = HistoricalEvidenceFormatter().format(report)
        assert "STRONG" in text

    def test_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            HistoricalEvidenceFormatter(precision=-1)


# ============================================================
# EE. NO STATISTICAL-SIGNIFICANCE LANGUAGE
# ============================================================


class TestNoStatisticalLanguage:
    def test_no_significant_word(self):
        outcomes = _resolved_cohort(60, seed=130)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        for text in [report.rationale, report.summary.rationale] + [
            c.rationale for b in report.breakdowns for c in b.cohorts
        ]:
            assert "significant" not in text.lower()

    def test_explicit_no_hypothesis_test(self):
        outcomes = _resolved_cohort(60, seed=131)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert "no statistical hypothesis test" in report.rationale.lower()


# ============================================================
# BB. CONFIGURATION VALIDATION
# ============================================================


class TestConfigValidation:
    def test_min_sample_total_must_be_positive(self):
        with pytest.raises(ValueError):
            EvidenceConfig(min_sample_total=0)

    def test_strong_min_sample_must_exceed_min(self):
        with pytest.raises(ValueError):
            EvidenceConfig(min_sample_total=30, strong_min_sample=20)

    def test_strong_min_resolved_must_exceed_min(self):
        with pytest.raises(ValueError):
            EvidenceConfig(min_resolved=10, strong_min_resolved=5)

    def test_strong_min_valid_r_must_exceed_min(self):
        with pytest.raises(ValueError):
            EvidenceConfig(min_valid_r=10, strong_min_valid_r=5)

    def test_favorable_win_rate_in_range(self):
        with pytest.raises(ValueError):
            EvidenceConfig(favorable_win_rate=1.5)

    def test_favorable_profit_factor_positive(self):
        with pytest.raises(ValueError):
            EvidenceConfig(favorable_profit_factor=0)

    def test_config_frozen(self):
        cfg = EvidenceConfig()
        with pytest.raises(Exception):
            cfg.min_sample_total = 99  # type: ignore[misc]


# ============================================================
# CC. MODEL IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_models_frozen(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=140),
        )
        with pytest.raises(Exception):
            report.evidence_id = "x"  # type: ignore[misc]
        with pytest.raises(Exception):
            report.summary.strength = EvidenceStrength.WEAK  # type: ignore[misc]

    def test_cohort_frozen(self):
        outcomes = _resolved_cohort(40, seed=141)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        cohort = report.breakdowns[0].cohorts[0]
        with pytest.raises(Exception):
            cohort.strength = EvidenceStrength.STRONG  # type: ignore[misc]

    def test_cohort_spec_frozen(self):
        spec = CohortSpec((BreakdownDimension.INSTRUMENT,))
        with pytest.raises(Exception):
            spec.dimensions = ()  # type: ignore[misc]

    def test_models_have_slots(self):
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(
            _resolved_cohort(40, seed=142),
        )
        with pytest.raises(AttributeError):
            report.new_attr = 1  # type: ignore[attr-defined]

    def test_strength_rank_value(self):
        assert EvidenceStrength.STRONG.rank_value < EvidenceStrength.MODERATE.rank_value
        assert EvidenceStrength.MODERATE.rank_value < EvidenceStrength.WEAK.rank_value
        assert EvidenceStrength.WEAK.rank_value < EvidenceStrength.INSUFFICIENT.rank_value

    def test_is_sufficient(self):
        assert EvidenceStrength.STRONG.is_sufficient is True
        assert EvidenceStrength.MODERATE.is_sufficient is True
        assert EvidenceStrength.WEAK.is_sufficient is True
        assert EvidenceStrength.INSUFFICIENT.is_sufficient is False


# ============================================================
# ENGINE EDGE CASES
# ============================================================


class TestEngineEdgeCases:
    def test_empty_specs_rejected(self):
        with pytest.raises(ValueError):
            HistoricalEvidenceEngine(EvidenceConfig(), specs=[])

    def test_specs_override(self):
        outcomes = _resolved_cohort(40, seed=150)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        report = eng.evaluate(
            outcomes, specs=[CohortSpec((BreakdownDimension.INSTRUMENT,))],
        )
        assert len(report.breakdowns) == 1

    def test_label_override(self):
        outcomes = _resolved_cohort(40, seed=151)
        eng = HistoricalEvidenceEngine(EvidenceConfig(label="config-label"))
        report = eng.evaluate(outcomes, label="override-label")
        assert report.label == "override-label"

    def test_metadata_override(self):
        outcomes = _resolved_cohort(40, seed=152)
        eng = HistoricalEvidenceEngine(EvidenceConfig())
        report = eng.evaluate(outcomes, metadata={"b": "2", "a": "1"})
        assert report.metadata == (("a", "1"), ("b", "2"))

    def test_cohort_counts_consistent(self):
        outcomes = _resolved_cohort(40, seed=153)
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert (
            report.sufficient_cohort_count + report.insufficient_cohort_count
            == report.cohort_count
        )

    def test_observed_vs_evidence_distinct(self):
        # A cohort with great observed result but tiny sample: observed
        # win rate 100% but INSUFFICIENT evidence.
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, ts=_EPOCH + timedelta(days=i))
            for i in range(3)
        ]
        report = HistoricalEvidenceEngine(EvidenceConfig()).evaluate(outcomes)
        assert report.summary.statistics.win_rate == 1.0
        assert report.summary.strength == EvidenceStrength.INSUFFICIENT
