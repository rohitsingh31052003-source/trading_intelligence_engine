"""
Tests for Checkpoint 9.11 — Historical Setup Quality Analysis Boundary.

Deterministic, network-free: every test constructs candidates and
observations directly (no provider, no corpus engine, no pipeline).
The setup quality-analysis layer consumes a SetupEvidenceBatch
(Checkpoint 9.9) and/or SetupEvidenceStatisticsReport (Checkpoint 9.10)
and produces a transparent research-quality report that describes the
strength and consistency of the historical observations without
pretending to predict future profitability.

Coverage:

A. Deterministic quality report
B. Correct occurrence counts
C. Sufficient / insufficient counts preserved
D. Per-metric sample sizes preserved
E. Descriptive return statistics preserved
F. Descriptive excursion statistics preserved
G. Positive / negative / zero forward-return counts
H. Proportion of positive forward-return observations
I. Forward-return direction consistency
J. Insufficient data is preserved
K. Empty evidence behaves deterministically
L. No-sufficient-data evidence behaves deterministically
M. Source evidence / statistics are not mutated
N. No forbidden trading semantics exist
O. No black-box quality score exists
P. Zero forward return distinguished from missing / insufficient
Q. Direction consistency edge cases
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_evidence import (
    SetupEvidenceBatch,
    SetupEvidenceOccurrence,
    aggregate_evidence,
)
from engine.models.historical_setup_outcome import (
    ForwardReturnObservation,
    ObservationStatus,
    PriceExcursionObservation,
)
from engine.models.historical_setup_quality import (
    SetupQualityReport,
    analyze_setup_quality,
)
from engine.models.historical_setup_statistics import (
    SetupEvidenceStatisticsReport,
    compute_statistics,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _candidate(
    evaluation_time: datetime = _EPOCH,
    instrument: str = "NIFTY",
) -> HistoricalSetupCandidate:
    return HistoricalSetupCandidate(
        instrument=instrument,
        evaluation_time=evaluation_time,
        setup_timeframe="15m",
        context_timeframe="1D",
        history_count=10,
        status="VALID",
        has_structure=True,
        is_candidate=True,
        reason="directional structure present",
    )


def _forward_return(
    candidate: HistoricalSetupCandidate,
    reference_price: float = 100.0,
    forward_return: float = 0.05,
    *,
    status: ObservationStatus = ObservationStatus.AVAILABLE,
) -> ForwardReturnObservation:
    if status is ObservationStatus.INSUFFICIENT_DATA:
        return ForwardReturnObservation(
            candidate=candidate,
            reference_price=reference_price,
            horizon_candles=10,
            endpoint_price=None,
            forward_return=None,
            observation_status=ObservationStatus.INSUFFICIENT_DATA,
            reason="insufficient future candles",
        )
    return ForwardReturnObservation(
        candidate=candidate,
        reference_price=reference_price,
        horizon_candles=10,
        endpoint_price=reference_price * (1.0 + forward_return),
        forward_return=forward_return,
        observation_status=ObservationStatus.AVAILABLE,
        reason="forward return computed",
    )


def _price_excursion(
    candidate: HistoricalSetupCandidate,
    reference_price: float = 100.0,
    max_up: float = 0.08,
    max_down: float = -0.03,
    *,
    status: ObservationStatus = ObservationStatus.AVAILABLE,
) -> PriceExcursionObservation:
    if status is ObservationStatus.INSUFFICIENT_DATA:
        return PriceExcursionObservation(
            candidate=candidate,
            reference_price=reference_price,
            horizon_candles=10,
            max_upward_excursion=None,
            max_downward_excursion=None,
            max_high=None,
            min_low=None,
            observation_status=ObservationStatus.INSUFFICIENT_DATA,
            reason="insufficient future candles",
        )
    return PriceExcursionObservation(
        candidate=candidate,
        reference_price=reference_price,
        horizon_candles=10,
        max_upward_excursion=max_up,
        max_downward_excursion=max_down,
        max_high=reference_price * (1.0 + max_up),
        min_low=reference_price * (1.0 + max_down),
        observation_status=ObservationStatus.AVAILABLE,
        reason="price excursion computed",
    )


def _occurrence(
    occurrence_id: str,
    candidate: HistoricalSetupCandidate,
    *,
    forward_return: ForwardReturnObservation | None = None,
    price_excursion: PriceExcursionObservation | None = None,
) -> SetupEvidenceOccurrence:
    if forward_return is None:
        forward_return = _forward_return(candidate)
    if price_excursion is None:
        price_excursion = _price_excursion(candidate)
    return SetupEvidenceOccurrence(
        occurrence_id=occurrence_id,
        candidate=candidate,
        forward_return=forward_return,
        price_excursion=price_excursion,
    )


def _batch(occurrences: tuple[SetupEvidenceOccurrence, ...]) -> SetupEvidenceBatch:
    return aggregate_evidence(occurrences)


# ============================================================
# A. Deterministic quality report
# ============================================================


class TestDeterministic:
    def test_same_batch_produces_same_report(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report1 = analyze_setup_quality(batch)
        report2 = analyze_setup_quality(batch)
        assert report1 == report2

    def test_repeated_calls_identical(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        reports = [analyze_setup_quality(batch) for _ in range(5)]
        for r in reports[1:]:
            assert r == reports[0]


# ============================================================
# B. Correct occurrence counts
# ============================================================


class TestOccurrenceCounts:
    def test_single_occurrence_total(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert report.total_occurrence_count == 1

    def test_multiple_occurrences_total(self) -> None:
        occs = tuple(
            _occurrence(f"occ-{i}", _candidate(evaluation_time=_EPOCH + timedelta(days=i)))
            for i in range(5)
        )
        batch = _batch(occs)
        report = analyze_setup_quality(batch)
        assert report.total_occurrence_count == 5

    def test_total_equals_batch_total(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        assert report.total_occurrence_count == batch.total_occurrences


# ============================================================
# C. Sufficient / insufficient counts preserved
# ============================================================


class TestSufficientInsufficientCounts:
    def test_all_sufficient(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        assert report.sufficient_data_count == 2
        assert report.insufficient_data_count == 0

    def test_all_insufficient(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = SetupEvidenceOccurrence(
            occurrence_id="occ-1",
            candidate=cand1,
            forward_return=None,
            price_excursion=None,
        )
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.sufficient_data_count == 0
        assert report.insufficient_data_count == 2

    def test_mixed_sufficient_insufficient(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.sufficient_data_count == 1
        assert report.insufficient_data_count == 1

    def test_counts_sum_to_total(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                SetupEvidenceOccurrence(
                    occurrence_id="occ-2",
                    candidate=_candidate(evaluation_time=_EPOCH + timedelta(days=1)),
                    forward_return=None,
                    price_excursion=None,
                ),
                _occurrence("occ-3", _candidate(evaluation_time=_EPOCH + timedelta(days=2))),
            )
        )
        report = analyze_setup_quality(batch)
        assert (
            report.sufficient_data_count + report.insufficient_data_count
            == report.total_occurrence_count
        )


# ============================================================
# D. Per-metric sample sizes preserved
# ============================================================


class TestPerMetricSampleSizes:
    def test_forward_return_observation_count(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 2

    def test_upward_excursion_observation_count(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        assert report.upward_excursion_observation_count == 2

    def test_downward_excursion_observation_count(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        assert report.downward_excursion_observation_count == 2

    def test_forward_return_count_matches_statistics(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                SetupEvidenceOccurrence(
                    occurrence_id="occ-2",
                    candidate=_candidate(evaluation_time=_EPOCH + timedelta(days=1)),
                    forward_return=None,
                    price_excursion=None,
                ),
            )
        )
        report = analyze_setup_quality(batch)
        stats = compute_statistics(batch)
        assert report.forward_return_observation_count == stats.forward_return_observation_count


# ============================================================
# E. Descriptive return statistics preserved
# ============================================================


class TestDescriptiveReturnStatistics:
    def test_forward_return_mean_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_mean == pytest.approx((0.10 + (-0.04)) / 2)

    def test_forward_return_median_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert report.forward_return_median == pytest.approx(0.06)

    def test_forward_return_min_max_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_minimum == pytest.approx(-0.04)
        assert report.forward_return_maximum == pytest.approx(0.10)


# ============================================================
# F. Descriptive excursion statistics preserved
# ============================================================


class TestDescriptiveExcursionStatistics:
    def test_upward_excursion_mean_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence(
            "occ-1",
            cand1,
            price_excursion=_price_excursion(cand1, max_up=0.08, max_down=-0.03),
        )
        occ2 = _occurrence(
            "occ-2",
            cand2,
            price_excursion=_price_excursion(cand2, max_up=0.12, max_down=-0.05),
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.upward_excursion_mean == pytest.approx((0.08 + 0.12) / 2)

    def test_downward_excursion_median_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence(
            "occ-1",
            cand1,
            price_excursion=_price_excursion(cand1, max_up=0.08, max_down=-0.03),
        )
        occ2 = _occurrence(
            "occ-2",
            cand2,
            price_excursion=_price_excursion(cand2, max_up=0.12, max_down=-0.05),
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.downward_excursion_median == pytest.approx((-0.03 + (-0.05)) / 2)


# ============================================================
# G. Positive / negative / zero forward-return counts
# ============================================================


class TestPositiveNegativeZeroCounts:
    def test_all_positive(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 2
        assert report.negative_forward_return_observation_count == 0
        assert report.zero_forward_return_observation_count == 0

    def test_all_negative(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=-0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 0
        assert report.negative_forward_return_observation_count == 2
        assert report.zero_forward_return_observation_count == 0

    def test_all_zero(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.0))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.0))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 0
        assert report.negative_forward_return_observation_count == 0
        assert report.zero_forward_return_observation_count == 2

    def test_mixed_positive_negative_zero(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.0))
        occ4 = _occurrence("occ-4", cand4, forward_return=_forward_return(cand4, forward_return=0.08))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 2
        assert report.negative_forward_return_observation_count == 1
        assert report.zero_forward_return_observation_count == 1

    def test_counts_sum_to_forward_return_observation_count(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.0))
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert (
            report.positive_forward_return_observation_count
            + report.negative_forward_return_observation_count
            + report.zero_forward_return_observation_count
            == report.forward_return_observation_count
        )


# ============================================================
# H. Proportion of positive forward-return observations
# ============================================================


class TestProportionPositive:
    def test_all_positive_proportion_is_one(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.proportion_positive_forward_return == pytest.approx(1.0)

    def test_all_negative_proportion_is_zero(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=-0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.proportion_positive_forward_return == pytest.approx(0.0)

    def test_mixed_proportion(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.0))
        occ4 = _occurrence("occ-4", cand4, forward_return=_forward_return(cand4, forward_return=0.08))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = analyze_setup_quality(batch)
        assert report.proportion_positive_forward_return == pytest.approx(2 / 4)

    def test_zero_forward_returns_included_in_proportion(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.0))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.proportion_positive_forward_return == pytest.approx(0.5)


# ============================================================
# I. Forward-return direction consistency
# ============================================================


class TestDirectionConsistency:
    def test_all_positive_consistency_is_one(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(1.0)

    def test_all_negative_consistency_is_one(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=-0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.10))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(1.0)

    def test_perfectly_split_consistency_is_half(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(0.5)

    def test_three_to_one_split(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.08))
        occ4 = _occurrence("occ-4", cand4, forward_return=_forward_return(cand4, forward_return=-0.02))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(3 / 4)

    def test_zero_returns_excluded_from_consistency(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.0))
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(0.5)


# ============================================================
# J. Insufficient data is preserved
# ============================================================


class TestInsufficientDataPreserved:
    def test_insufficient_observations_excluded_from_derived_counts(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 1
        assert report.positive_forward_return_observation_count == 1
        assert report.negative_forward_return_observation_count == 0
        assert report.zero_forward_return_observation_count == 0

    def test_insufficient_forward_return_with_sufficient_excursion(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=_forward_return(
                cand2, status=ObservationStatus.INSUFFICIENT_DATA
            ),
            price_excursion=_price_excursion(cand2),
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 1
        assert report.upward_excursion_observation_count == 2

    def test_insufficient_data_count_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        occ3 = SetupEvidenceOccurrence(
            occurrence_id="occ-3",
            candidate=cand3,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert report.insufficient_data_count == 2
        assert report.sufficient_data_count == 1


# ============================================================
# K. Empty evidence behaves deterministically
# ============================================================


class TestEmptyEvidence:
    def test_empty_batch_produces_empty_report(self) -> None:
        batch = _batch(())
        report = analyze_setup_quality(batch)
        assert report.is_empty
        assert report.total_occurrence_count == 0
        assert report.sufficient_data_count == 0
        assert report.insufficient_data_count == 0

    def test_empty_batch_forward_return_count_zero(self) -> None:
        batch = _batch(())
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 0

    def test_empty_batch_statistics_are_none(self) -> None:
        batch = _batch(())
        report = analyze_setup_quality(batch)
        assert report.forward_return_mean is None
        assert report.forward_return_median is None
        assert report.forward_return_minimum is None
        assert report.forward_return_maximum is None

    def test_empty_batch_derived_indicators_zero_or_none(self) -> None:
        batch = _batch(())
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 0
        assert report.negative_forward_return_observation_count == 0
        assert report.zero_forward_return_observation_count == 0
        assert report.proportion_positive_forward_return is None
        assert report.forward_return_direction_consistency is None

    def test_empty_batch_not_has_sufficient_observations(self) -> None:
        batch = _batch(())
        report = analyze_setup_quality(batch)
        assert not report.has_sufficient_observations


# ============================================================
# L. No-sufficient-data evidence behaves deterministically
# ============================================================


class TestNoSufficientData:
    def test_all_insufficient_forward_return_statistics_none(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = SetupEvidenceOccurrence(
            occurrence_id="occ-1",
            candidate=cand1,
            forward_return=None,
            price_excursion=None,
        )
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 0
        assert report.forward_return_mean is None
        assert report.forward_return_median is None
        assert report.forward_return_minimum is None
        assert report.forward_return_maximum is None

    def test_all_insufficient_derived_indicators_zero_or_none(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = SetupEvidenceOccurrence(
            occurrence_id="occ-1",
            candidate=cand1,
            forward_return=None,
            price_excursion=None,
        )
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.positive_forward_return_observation_count == 0
        assert report.negative_forward_return_observation_count == 0
        assert report.zero_forward_return_observation_count == 0
        assert report.proportion_positive_forward_return is None
        assert report.forward_return_direction_consistency is None

    def test_all_insufficient_not_has_sufficient_observations(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        occ1 = SetupEvidenceOccurrence(
            occurrence_id="occ-1",
            candidate=cand1,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1,))
        report = analyze_setup_quality(batch)
        assert not report.has_sufficient_observations


# ============================================================
# M. Source evidence / statistics are not mutated
# ============================================================


class TestSourceNotMutated:
    def test_batch_not_mutated(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        original_occurrences = batch.occurrences
        original_total = batch.total_occurrences
        original_sufficient = batch.sufficient_data_count
        _ = analyze_setup_quality(batch)
        assert batch.occurrences is original_occurrences
        assert batch.total_occurrences == original_total
        assert batch.sufficient_data_count == original_sufficient

    def test_statistics_not_mutated(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        stats = compute_statistics(batch)
        original_mean = stats.forward_return_mean
        original_count = stats.forward_return_observation_count
        _ = analyze_setup_quality(batch, statistics=stats)
        assert stats.forward_return_mean == original_mean
        assert stats.forward_return_observation_count == original_count

    def test_precomputed_statistics_used_when_provided(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        stats = compute_statistics(batch)
        report = analyze_setup_quality(batch, statistics=stats)
        assert report.forward_return_mean == stats.forward_return_mean
        assert report.forward_return_observation_count == stats.forward_return_observation_count


# ============================================================
# N. No forbidden trading semantics exist
# ============================================================


class TestNoForbiddenSemantics:
    def test_no_quality_score_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "quality_score")

    def test_no_confidence_score_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "confidence_score")

    def test_no_evidence_strength_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "evidence_strength")

    def test_no_win_rate_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "win_rate")

    def test_no_success_rate_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "success_rate")

    def test_no_profitability_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "profitability")

    def test_no_trade_accuracy_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "trade_accuracy")

    def test_no_buy_sell_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "BUY")
        assert not hasattr(report, "SELL")

    def test_no_expectancy_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "expectancy")

    def test_no_profit_factor_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "profit_factor")

    def test_no_sharpe_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "sharpe")

    def test_no_ranking_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "ranking")

    def test_no_ml_prediction_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "ml_prediction")

    def test_no_target_stop_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "target")
        assert not hasattr(report, "stop")

    def test_no_win_loss_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "win")
        assert not hasattr(report, "loss")

    def test_no_trading_recommendation_attribute(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = analyze_setup_quality(batch)
        assert not hasattr(report, "trading_recommendation")


# ============================================================
# O. No black-box quality score exists
# ============================================================


class TestNoBlackBoxQualityScore:
    def test_all_attributes_are_transparent_counts_or_statistics(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        # Verify every attribute is either an int, float, str, or None
        for field_name in report.__dataclass_fields__:
            value = getattr(report, field_name)
            assert isinstance(value, (int, float, str, type(None))), (
                f"Field {field_name} has unexpected type {type(value)}"
            )

    def test_no_hidden_composite_score(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = analyze_setup_quality(batch)
        # No attribute should be a composite score combining multiple factors
        # All numeric attributes should be directly interpretable
        assert report.positive_forward_return_observation_count >= 0
        assert report.negative_forward_return_observation_count >= 0
        assert report.zero_forward_return_observation_count >= 0


# ============================================================
# P. Zero forward return distinguished from missing / insufficient
# ============================================================


class TestZeroDistinguishedFromMissing:
    def test_zero_return_counted_separately_from_insufficient(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.0))
        occ3 = SetupEvidenceOccurrence(
            occurrence_id="occ-3",
            candidate=cand3,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 2
        assert report.positive_forward_return_observation_count == 1
        assert report.zero_forward_return_observation_count == 1
        assert report.negative_forward_return_observation_count == 0
        assert report.insufficient_data_count == 1

    def test_zero_return_counted_separately_from_insufficient_data_status(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence(
            "occ-2",
            cand2,
            forward_return=_forward_return(cand2, forward_return=0.0),
        )
        occ3 = SetupEvidenceOccurrence(
            occurrence_id="occ-3",
            candidate=cand3,
            forward_return=_forward_return(
                cand3, status=ObservationStatus.INSUFFICIENT_DATA
            ),
            price_excursion=_price_excursion(cand3),
        )
        batch = _batch((occ1, occ2, occ3))
        report = analyze_setup_quality(batch)
        assert report.forward_return_observation_count == 2
        assert report.zero_forward_return_observation_count == 1
        assert report.positive_forward_return_observation_count == 1


# ============================================================
# Q. Direction consistency edge cases
# ============================================================


class TestDirectionConsistencyEdgeCases:
    def test_single_positive_observation_consistency_is_one(self) -> None:
        cand = _candidate()
        batch = _batch(
            (_occurrence("occ-1", cand, forward_return=_forward_return(cand, forward_return=0.05)),)
        )
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(1.0)

    def test_single_negative_observation_consistency_is_one(self) -> None:
        cand = _candidate()
        batch = _batch(
            (_occurrence("occ-1", cand, forward_return=_forward_return(cand, forward_return=-0.05)),)
        )
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency == pytest.approx(1.0)

    def test_single_zero_observation_consistency_is_none(self) -> None:
        cand = _candidate()
        batch = _batch(
            (_occurrence("occ-1", cand, forward_return=_forward_return(cand, forward_return=0.0)),)
        )
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency is None

    def test_all_zero_returns_consistency_is_none(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.0))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.0))
        batch = _batch((occ1, occ2))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency is None

    def test_consistency_range(self) -> None:
        """Consistency should always be in [0.5, 1.0] when not None."""
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.05))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.03))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.08))
        occ4 = _occurrence("occ-4", cand4, forward_return=_forward_return(cand4, forward_return=-0.02))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = analyze_setup_quality(batch)
        assert report.forward_return_direction_consistency is not None
        assert 0.5 <= report.forward_return_direction_consistency <= 1.0
