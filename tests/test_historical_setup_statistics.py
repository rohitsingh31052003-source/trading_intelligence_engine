"""
Tests for Checkpoint 9.10 — Statistical Evidence Analysis Layer.

Deterministic, network-free: every test constructs candidates and
observations directly (no provider, no corpus engine, no pipeline).
The statistical evidence-analysis layer consumes a SetupEvidenceBatch
(Checkpoint 9.9) and computes neutral descriptive statistics only.
It does NOT determine setup quality, does NOT compute win rates, loss
rates, expectancy, Sharpe ratios, profit factors, statistical
significance, predictive power, or quality scores.

Coverage:

A. Correct occurrence counts
B. Sufficient / insufficient counts
C. Mean forward return
D. Median forward return
E. Min / max forward return
F. Mean / median upward excursion
G. Mean / median downward excursion
H. Missing / insufficient observations excluded from the relevant statistic
I. Zero returns / excursions preserved as real observations
J. Empty batch
K. No sufficient observations
L. Deterministic results
M. Source evidence is not mutated
N. No forbidden trade-quality semantics in the statistical report
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
# A. Correct occurrence counts
# ============================================================


class TestOccurrenceCounts:
    def test_single_occurrence_total(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert report.total_occurrence_count == 1

    def test_multiple_occurrences_total(self) -> None:
        occs = tuple(
            _occurrence(f"occ-{i}", _candidate(evaluation_time=_EPOCH + timedelta(days=i)))
            for i in range(5)
        )
        batch = _batch(occs)
        report = compute_statistics(batch)
        assert report.total_occurrence_count == 5

    def test_total_equals_batch_total(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = compute_statistics(batch)
        assert report.total_occurrence_count == batch.total_occurrences


# ============================================================
# B. Sufficient / insufficient counts
# ============================================================


class TestSufficientInsufficientCounts:
    def test_all_sufficient(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        report = compute_statistics(batch)
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
        report = compute_statistics(batch)
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
        report = compute_statistics(batch)
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
        report = compute_statistics(batch)
        assert (
            report.sufficient_data_count + report.insufficient_data_count
            == report.total_occurrence_count
        )


# ============================================================
# C. Mean forward return
# ============================================================


class TestMeanForwardReturn:
    def test_mean_single_observation(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert report.forward_return_mean == pytest.approx(0.05)

    def test_mean_multiple_observations(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.forward_return_mean == pytest.approx((0.10 + (-0.04) + 0.06) / 3)

    def test_mean_negative_returns(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=-0.02))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.08))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_mean == pytest.approx((-0.02 + (-0.08)) / 2)


# ============================================================
# D. Median forward return
# ============================================================


class TestMedianForwardReturn:
    def test_median_odd_count(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.forward_return_median == pytest.approx(0.06)

    def test_median_even_count(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        occ4 = _occurrence("occ-4", cand4, forward_return=_forward_return(cand4, forward_return=0.02))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = compute_statistics(batch)
        # sorted: -0.04, 0.02, 0.06, 0.10 -> median = (0.02 + 0.06) / 2
        assert report.forward_return_median == pytest.approx((0.02 + 0.06) / 2)


# ============================================================
# E. Min / max forward return
# ============================================================


class TestMinMaxForwardReturn:
    def test_min_forward_return(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.forward_return_minimum == pytest.approx(-0.04)

    def test_max_forward_return(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=-0.04))
        occ3 = _occurrence("occ-3", cand3, forward_return=_forward_return(cand3, forward_return=0.06))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.forward_return_maximum == pytest.approx(0.10)

    def test_min_max_with_single_observation(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert report.forward_return_minimum == pytest.approx(0.05)
        assert report.forward_return_maximum == pytest.approx(0.05)
        assert report.forward_return_minimum == report.forward_return_maximum


# ============================================================
# F. Mean / median upward excursion
# ============================================================


class TestMeanMedianUpwardExcursion:
    def test_mean_upward_excursion(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_up=0.04))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.upward_excursion_mean == pytest.approx((0.10 + 0.04) / 2)

    def test_median_upward_excursion_odd(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_up=0.04))
        occ3 = _occurrence("occ-3", cand3, price_excursion=_price_excursion(cand3, max_up=0.07))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.upward_excursion_median == pytest.approx(0.07)

    def test_median_upward_excursion_even(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_up=0.04))
        occ3 = _occurrence("occ-3", cand3, price_excursion=_price_excursion(cand3, max_up=0.07))
        occ4 = _occurrence("occ-4", cand4, price_excursion=_price_excursion(cand4, max_up=0.02))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = compute_statistics(batch)
        assert report.upward_excursion_median == pytest.approx((0.04 + 0.07) / 2)


# ============================================================
# G. Mean / median downward excursion
# ============================================================


class TestMeanMedianDownwardExcursion:
    def test_mean_downward_excursion(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_down=-0.05))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_down=-0.01))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.downward_excursion_mean == pytest.approx((-0.05 + (-0.01)) / 2)

    def test_median_downward_excursion_odd(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_down=-0.05))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_down=-0.01))
        occ3 = _occurrence("occ-3", cand3, price_excursion=_price_excursion(cand3, max_down=-0.03))
        batch = _batch((occ1, occ2, occ3))
        report = compute_statistics(batch)
        assert report.downward_excursion_median == pytest.approx(-0.03)

    def test_median_downward_excursion_even(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand4 = _candidate(evaluation_time=_EPOCH + timedelta(days=3))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_down=-0.05))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_down=-0.01))
        occ3 = _occurrence("occ-3", cand3, price_excursion=_price_excursion(cand3, max_down=-0.03))
        occ4 = _occurrence("occ-4", cand4, price_excursion=_price_excursion(cand4, max_down=-0.07))
        batch = _batch((occ1, occ2, occ3, occ4))
        report = compute_statistics(batch)
        assert report.downward_excursion_median == pytest.approx((-0.03 + (-0.05)) / 2)


# ============================================================
# H. Missing / insufficient observations excluded from the relevant statistic
# ============================================================


class TestMissingInsufficientExcluded:
    def test_insufficient_forward_return_excluded_from_forward_stats(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence(
            "occ-2",
            cand2,
            forward_return=_forward_return(cand2, status=ObservationStatus.INSUFFICIENT_DATA),
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 1
        assert report.forward_return_mean == pytest.approx(0.10)

    def test_insufficient_price_excursion_excluded_from_excursion_stats(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10, max_down=-0.05))
        occ2 = _occurrence(
            "occ-2",
            cand2,
            price_excursion=_price_excursion(cand2, status=ObservationStatus.INSUFFICIENT_DATA),
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.upward_excursion_observation_count == 1
        assert report.downward_excursion_observation_count == 1
        assert report.upward_excursion_mean == pytest.approx(0.10)
        assert report.downward_excursion_mean == pytest.approx(-0.05)

    def test_none_forward_return_excluded(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=_price_excursion(cand2),
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 1
        assert report.forward_return_mean == pytest.approx(0.10)

    def test_none_price_excursion_excluded(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10, max_down=-0.05))
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=_forward_return(cand2),
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.upward_excursion_observation_count == 1
        assert report.downward_excursion_observation_count == 1

    def test_partial_data_forward_sufficient_excursion_insufficient(self) -> None:
        """An occurrence with sufficient forward return but insufficient
        excursion still contributes to forward-return stats but NOT to
        excursion stats."""
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=_forward_return(cand2, forward_return=0.20),
            price_excursion=_price_excursion(cand2, status=ObservationStatus.INSUFFICIENT_DATA),
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 2
        assert report.forward_return_mean == pytest.approx((0.05 + 0.20) / 2)
        assert report.upward_excursion_observation_count == 1
        assert report.downward_excursion_observation_count == 1

    def test_insufficient_observations_do_not_affect_other_metrics(self) -> None:
        """Insufficient forward-return data does not affect excursion stats."""
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = _occurrence(
            "occ-2",
            cand2,
            forward_return=_forward_return(cand2, status=ObservationStatus.INSUFFICIENT_DATA),
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.upward_excursion_observation_count == 2
        assert report.downward_excursion_observation_count == 2


# ============================================================
# I. Zero returns / excursions preserved as real observations
# ============================================================


class TestZeroValuesPreserved:
    def test_zero_forward_return_included(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.10))
        occ2 = _occurrence("occ-2", cand2, forward_return=_forward_return(cand2, forward_return=0.0))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 2
        assert report.forward_return_mean == pytest.approx((0.10 + 0.0) / 2)
        assert report.forward_return_minimum == pytest.approx(0.0)

    def test_zero_upward_excursion_included(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_up=0.10))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_up=0.0, max_down=-0.05))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.upward_excursion_observation_count == 2
        assert report.upward_excursion_mean == pytest.approx((0.10 + 0.0) / 2)

    def test_zero_downward_excursion_included(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, price_excursion=_price_excursion(cand1, max_down=-0.05))
        occ2 = _occurrence("occ-2", cand2, price_excursion=_price_excursion(cand2, max_up=0.03, max_down=0.0))
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.downward_excursion_observation_count == 2
        assert report.downward_excursion_mean == pytest.approx((-0.05 + 0.0) / 2)

    def test_zero_not_confused_with_missing(self) -> None:
        """A zero return is a real observation and must not be treated as
        missing/insufficient."""
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1, forward_return=_forward_return(cand1, forward_return=0.0))
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1, occ2))
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 1
        assert report.forward_return_mean == pytest.approx(0.0)


# ============================================================
# J. Empty batch
# ============================================================


class TestEmptyBatch:
    def test_empty_batch_all_counts_zero(self) -> None:
        batch = _batch(())
        report = compute_statistics(batch)
        assert report.total_occurrence_count == 0
        assert report.sufficient_data_count == 0
        assert report.insufficient_data_count == 0

    def test_empty_batch_all_statistics_none(self) -> None:
        batch = _batch(())
        report = compute_statistics(batch)
        assert report.forward_return_mean is None
        assert report.forward_return_median is None
        assert report.forward_return_minimum is None
        assert report.forward_return_maximum is None
        assert report.upward_excursion_mean is None
        assert report.upward_excursion_median is None
        assert report.downward_excursion_mean is None
        assert report.downward_excursion_median is None

    def test_empty_batch_observation_counts_zero(self) -> None:
        batch = _batch(())
        report = compute_statistics(batch)
        assert report.forward_return_observation_count == 0
        assert report.upward_excursion_observation_count == 0
        assert report.downward_excursion_observation_count == 0

    def test_empty_batch_is_empty_property(self) -> None:
        batch = _batch(())
        report = compute_statistics(batch)
        assert report.is_empty is True

    def test_empty_batch_deterministic(self) -> None:
        batch = _batch(())
        report1 = compute_statistics(batch)
        report2 = compute_statistics(batch)
        assert report1 == report2


# ============================================================
# K. No sufficient observations
# ============================================================


class TestNoSufficientObservations:
    def test_all_insufficient_forward_return_stats_none(self) -> None:
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
        report = compute_statistics(batch)
        assert report.forward_return_mean is None
        assert report.forward_return_median is None
        assert report.forward_return_minimum is None
        assert report.forward_return_maximum is None
        assert report.forward_return_observation_count == 0

    def test_all_insufficient_excursion_stats_none(self) -> None:
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
        report = compute_statistics(batch)
        assert report.upward_excursion_mean is None
        assert report.upward_excursion_median is None
        assert report.downward_excursion_mean is None
        assert report.downward_excursion_median is None
        assert report.upward_excursion_observation_count == 0
        assert report.downward_excursion_observation_count == 0

    def test_no_sufficient_observations_deterministic(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        occ1 = SetupEvidenceOccurrence(
            occurrence_id="occ-1",
            candidate=cand1,
            forward_return=None,
            price_excursion=None,
        )
        batch = _batch((occ1,))
        report1 = compute_statistics(batch)
        report2 = compute_statistics(batch)
        assert report1 == report2


# ============================================================
# L. Deterministic results
# ============================================================


class TestDeterministicResults:
    def test_repeated_computation_identical(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
                _occurrence("occ-3", _candidate(evaluation_time=_EPOCH + timedelta(days=2))),
            )
        )
        report1 = compute_statistics(batch)
        report2 = compute_statistics(batch)
        assert report1 == report2

    def test_same_batch_different_order_same_result(self) -> None:
        occ1 = _occurrence("occ-1", _candidate(evaluation_time=_EPOCH))
        occ2 = _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1)))
        batch1 = _batch((occ1, occ2))
        batch2 = _batch((occ2, occ1))
        report1 = compute_statistics(batch1)
        report2 = compute_statistics(batch2)
        assert report1.forward_return_mean == report2.forward_return_mean
        assert report1.forward_return_median == report2.forward_return_median
        assert report1.forward_return_minimum == report2.forward_return_minimum
        assert report1.forward_return_maximum == report2.forward_return_maximum
        assert report1.upward_excursion_mean == report2.upward_excursion_mean
        assert report1.downward_excursion_mean == report2.downward_excursion_mean

    def test_report_is_frozen(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        with pytest.raises(AttributeError):
            report.total_occurrence_count = 99  # type: ignore[misc]

    def test_report_preserves_batch_identity(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert report.batch_id == batch.batch_id
        assert report.criterion_key == batch.criterion_key


# ============================================================
# M. Source evidence is not mutated
# ============================================================


class TestSourceNotMutated:
    def test_batch_not_mutated(self) -> None:
        batch = _batch(
            (
                _occurrence("occ-1", _candidate()),
                _occurrence("occ-2", _candidate(evaluation_time=_EPOCH + timedelta(days=1))),
            )
        )
        original_total = batch.total_occurrences
        original_occurrences = batch.occurrences
        compute_statistics(batch)
        assert batch.total_occurrences == original_total
        assert batch.occurrences is original_occurrences

    def test_occurrence_not_mutated(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        original_fr = occ.forward_return
        original_pe = occ.price_excursion
        batch = _batch((occ,))
        compute_statistics(batch)
        assert occ.forward_return is original_fr
        assert occ.price_excursion is original_pe

    def test_forward_return_observation_not_mutated(self) -> None:
        cand = _candidate()
        fr = _forward_return(cand, forward_return=0.123)
        batch = _batch((_occurrence("occ-1", cand, forward_return=fr),))
        compute_statistics(batch)
        assert fr.forward_return == pytest.approx(0.123)

    def test_price_excursion_observation_not_mutated(self) -> None:
        cand = _candidate()
        pe = _price_excursion(cand, max_up=0.09, max_down=-0.04)
        batch = _batch((_occurrence("occ-1", cand, price_excursion=pe),))
        compute_statistics(batch)
        assert pe.max_upward_excursion == pytest.approx(0.09)
        assert pe.max_downward_excursion == pytest.approx(-0.04)


# ============================================================
# N. No forbidden trade-quality semantics in the statistical report
# ============================================================


class TestNoForbiddenSemantics:
    def test_no_win_rate(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert not hasattr(report, "win_rate")

    def test_no_loss_rate(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert not hasattr(report, "loss_rate")

    def test_no_expectancy(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert not hasattr(report, "expectancy")

    def test_no_profit_factor(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert not hasattr(report, "profit_factor")

    def test_no_sharpe_ratio(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        assert not hasattr(report, "sharpe")
        assert not hasattr(report, "sharpe_ratio")

    def test_no_trade_profitability(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("profitable" in a.lower() for a in attrs)

    def test_no_target_stop(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("target" in a.lower() for a in attrs)
        assert not any("stop" in a.lower() for a in attrs)

    def test_no_buy_sell(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("buy" in a.lower() for a in attrs)
        assert not any("sell" in a.lower() for a in attrs)

    def test_no_setup_quality(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("quality" in a.lower() for a in attrs)

    def test_no_confidence_score(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("confidence" in a.lower() for a in attrs)
        assert not any("score" in a.lower() for a in attrs)

    def test_no_ranking(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("ranking" in a.lower() for a in attrs)
        assert not any("rank" in a.lower() for a in attrs)

    def test_no_ml_execution(self) -> None:
        batch = _batch((_occurrence("occ-1", _candidate()),))
        report = compute_statistics(batch)
        attrs = dir(report)
        assert not any("ml" in a.lower() for a in attrs)
        assert not any("execution" in a.lower() for a in attrs)
        assert not any("paper_trading" in a.lower() for a in attrs)
        assert not any("live_trading" in a.lower() for a in attrs)
        assert not any("schedule" in a.lower() for a in attrs)
        assert not any("dashboard" in a.lower() for a in attrs)
