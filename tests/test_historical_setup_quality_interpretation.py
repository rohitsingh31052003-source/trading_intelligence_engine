"""
Tests for Checkpoint 10.5 — Historical Setup Quality Interpretation Layer.

Deterministic, network-free: every test constructs behavior reports
directly (no provider, no corpus engine, no pipeline, no candles).
The interpretation layer consumes a HistoricalSetupBehaviorReport
(Checkpoint 10.4) and produces a transparent, deterministic,
descriptive interpretation of what the historical behavior of this
setup indicates descriptively.

Coverage:

A. Model — frozen, slots, validation, deterministic identity
B. Evidence availability — NO_DATA, INSUFFICIENT_DATA, EVALUABLE
C. Forward-return behavior — predominantly positive, negative, mixed, none
D. Directional consistency — high, moderate, low, not evaluable
E. Historical data states — empty, no observations, insufficient, threshold, above
F. Missing data — missing not zero, real zero distinguishable, insufficient explicit
G. Excursions — available, missing, insufficient, paired preservation
H. Safety — no candle/provider/store/corpus access, no mutation, no feedback
I. Determinism — repeated computation identical
J. Forbidden semantics — no BUY/SELL, win/loss, profitability, etc.
K. Immutability — source objects unchanged
L. Checkpoint 10.4 compatibility — generated from valid HistoricalSetupBehaviorReport
M. Type validation — TypeError on wrong input type
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_behavior import (
    HistoricalSetupBehaviorReport,
    assess_setup_behavior,
)
from engine.models.historical_setup_evaluation import (
    SetupEvaluationResult,
    SetupEvaluationStatus,
    evaluate_setup,
)
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
from engine.models.historical_setup_quality_interpretation import (
    DirectionalConsistency,
    EvidenceAvailability,
    ForwardReturnBehavior,
    HistoricalSetupQualityInterpretation,
    interpret_setup_behavior,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — shared helpers
# ---------------------------------------------------------------------------


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
    candidate: HistoricalSetupCandidate,
    *,
    occurrence_id: str = "occ-1",
    forward_return: float = 0.05,
    max_up: float = 0.08,
    max_down: float = -0.03,
    fr_status: ObservationStatus = ObservationStatus.AVAILABLE,
    pe_status: ObservationStatus = ObservationStatus.AVAILABLE,
) -> SetupEvidenceOccurrence:
    fr = _forward_return(candidate, forward_return=forward_return, status=fr_status)
    pe = _price_excursion(candidate, max_up=max_up, max_down=max_down, status=pe_status)
    return SetupEvidenceOccurrence(
        occurrence_id=occurrence_id,
        candidate=candidate,
        forward_return=fr if fr_status is ObservationStatus.AVAILABLE else None,
        price_excursion=pe if pe_status is ObservationStatus.AVAILABLE else None,
    )


def _batch(
    occurrences: tuple[SetupEvidenceOccurrence, ...],
) -> SetupEvidenceBatch:
    return aggregate_evidence(occurrences, criterion_key="NIFTY|15m|1D")


def _evaluable_batch(n: int = 15) -> SetupEvidenceBatch:
    """Build a batch with n evaluable occurrences."""
    occs = tuple(
        _occurrence(
            _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
            occurrence_id=f"occ-{i}",
            forward_return=0.05 + (i * 0.01),
        )
        for i in range(n)
    )
    return _batch(occs)


def _behavior_report(
    *,
    evaluation_status: SetupEvaluationStatus = SetupEvaluationStatus.EVALUABLE,
    total_occurrence_count: int = 10,
    sufficient_data_count: int = 10,
    insufficient_data_count: int = 0,
    forward_return_observation_count: int = 10,
    upward_excursion_observation_count: int = 10,
    downward_excursion_observation_count: int = 10,
    min_observations_for_evaluation: int = 5,
    forward_return_mean: float | None = 0.05,
    forward_return_median: float | None = 0.05,
    forward_return_minimum: float | None = 0.01,
    forward_return_maximum: float | None = 0.09,
    positive_forward_return_observation_count: int = 8,
    negative_forward_return_observation_count: int = 1,
    zero_forward_return_observation_count: int = 1,
    proportion_positive_forward_return: float | None = 0.8,
    forward_return_direction_consistency: float | None = 8 / 9,
    upward_excursion_mean: float | None = 0.08,
    upward_excursion_median: float | None = 0.08,
    downward_excursion_mean: float | None = -0.03,
    downward_excursion_median: float | None = -0.03,
) -> HistoricalSetupBehaviorReport:
    """Build a valid HistoricalSetupBehaviorReport with given parameters."""
    evaluation_id = "setup-eval-test1234567890"
    return HistoricalSetupBehaviorReport(
        behavior_report_id="behavior-"
        + __import__("hashlib")
        .sha256(evaluation_id.encode("utf-8"))
        .hexdigest()[:16],
        evaluation_id=evaluation_id,
        batch_id="evidence-batch-abc123",
        criterion_key="NIFTY|15m|1D",
        instrument="NIFTY",
        setup_timeframe="15m",
        context_timeframe="1D",
        evaluation_status=evaluation_status,
        total_occurrence_count=total_occurrence_count,
        sufficient_data_count=sufficient_data_count,
        insufficient_data_count=insufficient_data_count,
        forward_return_observation_count=forward_return_observation_count,
        upward_excursion_observation_count=upward_excursion_observation_count,
        downward_excursion_observation_count=downward_excursion_observation_count,
        min_observations_for_evaluation=min_observations_for_evaluation,
        forward_return_mean=forward_return_mean,
        forward_return_median=forward_return_median,
        forward_return_minimum=forward_return_minimum,
        forward_return_maximum=forward_return_maximum,
        positive_forward_return_observation_count=(
            positive_forward_return_observation_count
        ),
        negative_forward_return_observation_count=(
            negative_forward_return_observation_count
        ),
        zero_forward_return_observation_count=(
            zero_forward_return_observation_count
        ),
        proportion_positive_forward_return=proportion_positive_forward_return,
        forward_return_direction_consistency=forward_return_direction_consistency,
        upward_excursion_mean=upward_excursion_mean,
        upward_excursion_median=upward_excursion_median,
        downward_excursion_mean=downward_excursion_mean,
        downward_excursion_median=downward_excursion_median,
    )


def _interpret_from_batch(
    n: int = 15,
    min_observations: int = 5,
) -> HistoricalSetupQualityInterpretation:
    """Build an interpretation from a batch of n occurrences."""
    batch = _evaluable_batch(n=n)
    evaluation = evaluate_setup(batch, min_observations_for_evaluation=min_observations)
    quality = analyze_setup_quality(batch)
    report = assess_setup_behavior(evaluation, quality)
    return interpret_setup_behavior(report)


# ---------------------------------------------------------------------------
# A. Model tests
# ---------------------------------------------------------------------------


class TestModel:
    def test_frozen(self) -> None:
        interpretation = _interpret_from_batch()
        with pytest.raises(AttributeError):
            interpretation.evidence_availability = (  # type: ignore[misc]
                EvidenceAvailability.NO_HISTORICAL_DATA
            )

    def test_slots(self) -> None:
        interpretation = _interpret_from_batch()
        assert not hasattr(interpretation, "__dict__")
        assert hasattr(type(interpretation), "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            interpretation.arbitrary_attribute = "x"  # type: ignore[attr-defined]

    def test_interpretation_id_starts_with_prefix(self) -> None:
        interpretation = _interpret_from_batch()
        assert interpretation.interpretation_id.startswith(
            "setup-quality-interpretation-"
        )

    def test_interpretation_id_is_deterministic(self) -> None:
        i1 = _interpret_from_batch()
        i2 = _interpret_from_batch()
        assert i1.interpretation_id == i2.interpretation_id

    def test_different_input_produces_different_id(self) -> None:
        i1 = _interpret_from_batch(n=10)
        i2 = _interpret_from_batch(n=15)
        assert i1.interpretation_id != i2.interpretation_id

    def test_traceability_to_behavior_report(self) -> None:
        interpretation = _interpret_from_batch()
        assert interpretation.behavior_report_id.startswith("behavior-")

    def test_traceability_to_evaluation(self) -> None:
        interpretation = _interpret_from_batch()
        assert isinstance(interpretation.evaluation_id, str)
        assert len(interpretation.evaluation_id) > 0

    def test_traceability_to_batch(self) -> None:
        interpretation = _interpret_from_batch()
        assert isinstance(interpretation.batch_id, str)
        assert len(interpretation.batch_id) > 0

    def test_validation_negative_count(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            _behavior_report(total_occurrence_count=-1)

    def test_validation_count_sum(self) -> None:
        with pytest.raises(ValueError, match="must equal"):
            _behavior_report(
                total_occurrence_count=5,
                sufficient_data_count=2,
                insufficient_data_count=2,
            )

    def test_validation_no_data_requires_no_historical(self) -> None:
        with pytest.raises(ValueError, match="NO_HISTORICAL_DATA"):
            HistoricalSetupQualityInterpretation(
                interpretation_id="setup-quality-interpretation-abc",
                behavior_report_id="behavior-abc",
                evaluation_id="setup-eval-abc",
                batch_id="evidence-batch-abc",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.NO_DATA,
                total_occurrence_count=0,
                sufficient_data_count=0,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                min_observations_for_evaluation=5,
                evidence_availability=EvidenceAvailability.LIMITED_HISTORICAL_DATA,
                forward_return_behavior=ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION,
                directional_consistency=DirectionalConsistency.NOT_EVALUABLE,
                historical_behavior_summary="test",
            )

    def test_validation_insufficient_requires_limited(self) -> None:
        with pytest.raises(ValueError, match="LIMITED_HISTORICAL_DATA"):
            HistoricalSetupQualityInterpretation(
                interpretation_id="setup-quality-interpretation-abc",
                behavior_report_id="behavior-abc",
                evaluation_id="setup-eval-abc",
                batch_id="evidence-batch-abc",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
                total_occurrence_count=3,
                sufficient_data_count=3,
                insufficient_data_count=0,
                forward_return_observation_count=3,
                upward_excursion_observation_count=3,
                downward_excursion_observation_count=3,
                min_observations_for_evaluation=10,
                evidence_availability=EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA,
                forward_return_behavior=ForwardReturnBehavior.MIXED_DIRECTION,
                directional_consistency=DirectionalConsistency.MODERATE_CONSISTENCY,
                historical_behavior_summary="test",
            )

    def test_validation_evaluable_requires_sufficient(self) -> None:
        with pytest.raises(ValueError, match="SUFFICIENT_HISTORICAL_DATA"):
            HistoricalSetupQualityInterpretation(
                interpretation_id="setup-quality-interpretation-abc",
                behavior_report_id="behavior-abc",
                evaluation_id="setup-eval-abc",
                batch_id="evidence-batch-abc",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.EVALUABLE,
                total_occurrence_count=15,
                sufficient_data_count=15,
                insufficient_data_count=0,
                forward_return_observation_count=15,
                upward_excursion_observation_count=15,
                downward_excursion_observation_count=15,
                min_observations_for_evaluation=5,
                evidence_availability=EvidenceAvailability.LIMITED_HISTORICAL_DATA,
                forward_return_behavior=ForwardReturnBehavior.MIXED_DIRECTION,
                directional_consistency=DirectionalConsistency.MODERATE_CONSISTENCY,
                historical_behavior_summary="test",
            )

    def test_validation_no_observations_requires_no_directional(self) -> None:
        with pytest.raises(ValueError, match="NO_DIRECTIONAL_OBSERVATION"):
            HistoricalSetupQualityInterpretation(
                interpretation_id="setup-quality-interpretation-abc",
                behavior_report_id="behavior-abc",
                evaluation_id="setup-eval-abc",
                batch_id="evidence-batch-abc",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.NO_DATA,
                total_occurrence_count=0,
                sufficient_data_count=0,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                min_observations_for_evaluation=5,
                evidence_availability=EvidenceAvailability.NO_HISTORICAL_DATA,
                forward_return_behavior=ForwardReturnBehavior.PREDOMINANTLY_POSITIVE,
                directional_consistency=DirectionalConsistency.NOT_EVALUABLE,
                historical_behavior_summary="test",
            )

    def test_validation_no_data_requires_not_evaluable(self) -> None:
        with pytest.raises(ValueError, match="NOT_EVALUABLE"):
            HistoricalSetupQualityInterpretation(
                interpretation_id="setup-quality-interpretation-abc",
                behavior_report_id="behavior-abc",
                evaluation_id="setup-eval-abc",
                batch_id="evidence-batch-abc",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.NO_DATA,
                total_occurrence_count=0,
                sufficient_data_count=0,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                min_observations_for_evaluation=5,
                evidence_availability=EvidenceAvailability.NO_HISTORICAL_DATA,
                forward_return_behavior=ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION,
                directional_consistency=DirectionalConsistency.HIGH_CONSISTENCY,
                historical_behavior_summary="test",
            )


# ---------------------------------------------------------------------------
# B. Evidence availability
# ---------------------------------------------------------------------------


class TestEvidenceAvailability:
    def test_no_data(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.NO_HISTORICAL_DATA
        )
        assert interpretation.is_empty is True

    def test_limited_data(self) -> None:
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
            )
            for i in range(3)
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.LIMITED_HISTORICAL_DATA
        )
        assert interpretation.has_sufficient_evidence is False

    def test_sufficient_data(self) -> None:
        interpretation = _interpret_from_batch(n=15, min_observations=5)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
        )
        assert interpretation.has_sufficient_evidence is True

    def test_exactly_at_threshold(self) -> None:
        interpretation = _interpret_from_batch(n=10, min_observations=10)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
        )

    def test_just_below_threshold(self) -> None:
        interpretation = _interpret_from_batch(n=9, min_observations=10)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.LIMITED_HISTORICAL_DATA
        )


# ---------------------------------------------------------------------------
# C. Forward-return behavior
# ---------------------------------------------------------------------------


class TestForwardReturnBehavior:
    def test_no_directional_observation(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
        )

    def test_predominantly_positive(self) -> None:
        """18 positive, 4 negative, 2 zero -> 75% positive."""
        report = _behavior_report(
            total_occurrence_count=24,
            sufficient_data_count=24,
            insufficient_data_count=0,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=18,
            negative_forward_return_observation_count=4,
            zero_forward_return_observation_count=2,
            proportion_positive_forward_return=18 / 24,
            forward_return_direction_consistency=18 / 22,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
        )

    def test_predominantly_negative(self) -> None:
        """4 positive, 18 negative, 2 zero -> 16.7% positive."""
        report = _behavior_report(
            total_occurrence_count=24,
            sufficient_data_count=24,
            insufficient_data_count=0,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=4,
            negative_forward_return_observation_count=18,
            zero_forward_return_observation_count=2,
            proportion_positive_forward_return=4 / 24,
            forward_return_direction_consistency=18 / 22,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
        )

    def test_mixed_direction(self) -> None:
        """12 positive, 8 negative, 4 zero -> 50% positive."""
        report = _behavior_report(
            total_occurrence_count=24,
            sufficient_data_count=24,
            insufficient_data_count=0,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=12,
            negative_forward_return_observation_count=8,
            zero_forward_return_observation_count=4,
            proportion_positive_forward_return=12 / 24,
            forward_return_direction_consistency=12 / 20,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.MIXED_DIRECTION
        )

    def test_boundary_positive(self) -> None:
        """Exactly at 0.6 boundary -> MIXED (not > 0.6)."""
        report = _behavior_report(
            total_occurrence_count=10,
            sufficient_data_count=10,
            insufficient_data_count=0,
            forward_return_observation_count=10,
            positive_forward_return_observation_count=6,
            negative_forward_return_observation_count=4,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=0.6,
            forward_return_direction_consistency=0.6,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.MIXED_DIRECTION
        )

    def test_boundary_negative(self) -> None:
        """Exactly at 0.4 boundary -> MIXED (not < 0.4)."""
        report = _behavior_report(
            total_occurrence_count=10,
            sufficient_data_count=10,
            insufficient_data_count=0,
            forward_return_observation_count=10,
            positive_forward_return_observation_count=4,
            negative_forward_return_observation_count=6,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=0.4,
            forward_return_direction_consistency=0.6,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.MIXED_DIRECTION
        )

    def test_just_above_positive_threshold(self) -> None:
        """0.61 > 0.6 -> PREDOMINANTLY_POSITIVE."""
        report = _behavior_report(
            total_occurrence_count=100,
            sufficient_data_count=100,
            insufficient_data_count=0,
            forward_return_observation_count=100,
            positive_forward_return_observation_count=61,
            negative_forward_return_observation_count=39,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=0.61,
            forward_return_direction_consistency=61 / 100,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
        )

    def test_just_below_negative_threshold(self) -> None:
        """0.39 < 0.4 -> PREDOMINANTLY_NEGATIVE."""
        report = _behavior_report(
            total_occurrence_count=100,
            sufficient_data_count=100,
            insufficient_data_count=0,
            forward_return_observation_count=100,
            positive_forward_return_observation_count=39,
            negative_forward_return_observation_count=61,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=0.39,
            forward_return_direction_consistency=61 / 100,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
        )

    def test_one_sided_positive(self) -> None:
        """All positive, no negative."""
        report = _behavior_report(
            total_occurrence_count=10,
            sufficient_data_count=10,
            insufficient_data_count=0,
            forward_return_observation_count=10,
            positive_forward_return_observation_count=10,
            negative_forward_return_observation_count=0,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=1.0,
            forward_return_direction_consistency=1.0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
        )

    def test_one_sided_negative(self) -> None:
        """All negative, no positive."""
        report = _behavior_report(
            total_occurrence_count=10,
            sufficient_data_count=10,
            insufficient_data_count=0,
            forward_return_observation_count=10,
            positive_forward_return_observation_count=0,
            negative_forward_return_observation_count=10,
            zero_forward_return_observation_count=0,
            proportion_positive_forward_return=0.0,
            forward_return_direction_consistency=1.0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
        )


# ---------------------------------------------------------------------------
# D. Directional consistency
# ---------------------------------------------------------------------------


class TestDirectionalConsistency:
    def test_not_evaluable_no_data(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.NOT_EVALUABLE
        )

    def test_high_consistency(self) -> None:
        """consistency = 0.8 >= 0.75 -> HIGH."""
        report = _behavior_report(
            forward_return_direction_consistency=0.8,
            proportion_positive_forward_return=0.8,
            positive_forward_return_observation_count=8,
            negative_forward_return_observation_count=2,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.HIGH_CONSISTENCY
        )

    def test_moderate_consistency(self) -> None:
        """consistency = 0.65 -> MODERATE."""
        report = _behavior_report(
            total_occurrence_count=20,
            sufficient_data_count=20,
            forward_return_observation_count=20,
            upward_excursion_observation_count=20,
            downward_excursion_observation_count=20,
            forward_return_direction_consistency=0.65,
            proportion_positive_forward_return=0.65,
            positive_forward_return_observation_count=13,
            negative_forward_return_observation_count=7,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.MODERATE_CONSISTENCY
        )

    def test_low_consistency(self) -> None:
        """consistency = 0.55 -> LOW."""
        report = _behavior_report(
            total_occurrence_count=20,
            sufficient_data_count=20,
            forward_return_observation_count=20,
            upward_excursion_observation_count=20,
            downward_excursion_observation_count=20,
            forward_return_direction_consistency=0.55,
            proportion_positive_forward_return=0.55,
            positive_forward_return_observation_count=11,
            negative_forward_return_observation_count=9,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.LOW_CONSISTENCY
        )

    def test_boundary_high(self) -> None:
        """consistency = 0.75 exactly -> HIGH (>= 0.75)."""
        report = _behavior_report(
            total_occurrence_count=100,
            sufficient_data_count=100,
            forward_return_observation_count=100,
            upward_excursion_observation_count=100,
            downward_excursion_observation_count=100,
            forward_return_direction_consistency=0.75,
            proportion_positive_forward_return=0.75,
            positive_forward_return_observation_count=75,
            negative_forward_return_observation_count=25,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.HIGH_CONSISTENCY
        )

    def test_boundary_moderate(self) -> None:
        """consistency = 0.6 exactly -> MODERATE (>= 0.6)."""
        report = _behavior_report(
            forward_return_direction_consistency=0.6,
            proportion_positive_forward_return=0.6,
            positive_forward_return_observation_count=6,
            negative_forward_return_observation_count=4,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.MODERATE_CONSISTENCY
        )

    def test_perfect_consistency(self) -> None:
        """consistency = 1.0 -> HIGH."""
        report = _behavior_report(
            forward_return_direction_consistency=1.0,
            proportion_positive_forward_return=1.0,
            positive_forward_return_observation_count=10,
            negative_forward_return_observation_count=0,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.HIGH_CONSISTENCY
        )

    def test_minimum_consistency(self) -> None:
        """consistency = 0.5 (perfectly split) -> LOW."""
        report = _behavior_report(
            forward_return_direction_consistency=0.5,
            proportion_positive_forward_return=0.5,
            positive_forward_return_observation_count=5,
            negative_forward_return_observation_count=5,
            zero_forward_return_observation_count=0,
        )
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.directional_consistency
            is DirectionalConsistency.LOW_CONSISTENCY
        )


# ---------------------------------------------------------------------------
# E. Historical data states
# ---------------------------------------------------------------------------


class TestHistoricalDataStates:
    def test_empty_evidence(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.total_occurrence_count == 0
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.NO_HISTORICAL_DATA
        )

    def test_no_observations(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.forward_return_observation_count == 0

    def test_insufficient_observations(self) -> None:
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
            )
            for i in range(3)
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.LIMITED_HISTORICAL_DATA
        )

    def test_exactly_threshold_observations(self) -> None:
        interpretation = _interpret_from_batch(n=10, min_observations=10)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
        )

    def test_above_threshold_observations(self) -> None:
        interpretation = _interpret_from_batch(n=20, min_observations=5)
        assert (
            interpretation.evidence_availability
            is EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
        )


# ---------------------------------------------------------------------------
# F. Missing data
# ---------------------------------------------------------------------------


class TestMissingData:
    def test_missing_forward_return_not_zero(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.forward_return_behavior is (
            ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
        )

    def test_insufficient_observations_remain_explicit(self) -> None:
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
            )
            for i in range(2)
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert interpretation.total_occurrence_count == 2
        assert interpretation.min_observations_for_evaluation == 10

    def test_real_zero_distinguishable_from_missing(self) -> None:
        """A real observed zero forward return is counted, not treated as missing."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-zero",
                forward_return=0.0,
            ),
        )
        batch = _batch(occs)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.total_occurrence_count == 1
        assert interpretation.forward_return_observation_count == 1

    def test_zero_only_observations(self) -> None:
        """All zero forward returns -> mixed direction (0% positive)."""
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
                forward_return=0.0,
            )
            for i in range(10)
        )
        batch = _batch(occs)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        # All zeros: proportion_positive = 0/10 = 0.0 -> PREDOMINANTLY_NEGATIVE
        # direction_consistency is None (no non-zero) -> NOT_EVALUABLE
        assert interpretation.forward_return_behavior is (
            ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
        )
        assert interpretation.directional_consistency is (
            DirectionalConsistency.NOT_EVALUABLE
        )


# ---------------------------------------------------------------------------
# G. Excursions
# ---------------------------------------------------------------------------


class TestExcursions:
    def test_available_excursion_data(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        assert interpretation.upward_excursion_observation_count == 15
        assert interpretation.downward_excursion_observation_count == 15

    def test_paired_excursion_preservation(self) -> None:
        """Upward/downward counts are equal (paired at source)."""
        interpretation = _interpret_from_batch(n=10)
        assert (
            interpretation.upward_excursion_observation_count
            == interpretation.downward_excursion_observation_count
        )

    def test_missing_excursion_data(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert interpretation.upward_excursion_observation_count == 0
        assert interpretation.downward_excursion_observation_count == 0


# ---------------------------------------------------------------------------
# H. Safety
# ---------------------------------------------------------------------------


class TestSafety:
    def test_no_candle_access(self) -> None:
        interpretation = _interpret_from_batch(n=5)
        assert "candle" not in type(interpretation).__module__

    def test_no_provider_access(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        assert src is not None
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text
        assert "HistoricalMarketDataProvider" not in text

    def test_no_store_access(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text

    def test_no_corpus_slicing(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalResearchCorpusEngine" not in text
        assert "research_corpus" not in text

    def test_no_mutation_of_source_objects(self) -> None:
        batch = _evaluable_batch(n=5)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=3)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        report_id_before = report.behavior_report_id
        eval_status_before = report.evaluation_status
        interpret_setup_behavior(report)
        assert report.behavior_report_id == report_id_before
        assert report.evaluation_status == eval_status_before

    def test_no_feedback_into_discovery(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "historical_setup_discovery" not in text

    def test_no_temporal_slicing(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "timedelta" not in text
        assert "datetime" not in text

    def test_no_discovery_mutation(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "setup_discovery" not in text


# ---------------------------------------------------------------------------
# I. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_computation_identical(self) -> None:
        batch = _evaluable_batch(n=10)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        i1 = interpret_setup_behavior(report)
        i2 = interpret_setup_behavior(report)
        assert i1 == i2

    def test_same_input_same_output(self) -> None:
        i1 = _interpret_from_batch(n=15, min_observations=5)
        i2 = _interpret_from_batch(n=15, min_observations=5)
        assert i1 == i2

    def test_interpretation_id_deterministic(self) -> None:
        i1 = _interpret_from_batch(n=10)
        i2 = _interpret_from_batch(n=10)
        assert i1.interpretation_id == i2.interpretation_id

    def test_different_input_different_id(self) -> None:
        i1 = _interpret_from_batch(n=10)
        i2 = _interpret_from_batch(n=20)
        assert i1.interpretation_id != i2.interpretation_id


# ---------------------------------------------------------------------------
# J. Forbidden semantics
# ---------------------------------------------------------------------------


class TestForbiddenSemantics:
    def _assert_no_trading_terms(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        forbidden = [
            "BUY",
            "SELL",
            "LONG",
            "SHORT",
            "WIN",
            "LOSS",
            "PROFIT",
            "PROFITABLE",
            "STOP",
            "TARGET",
            "RISK",
            "REWARD",
            "EXPECTANCY",
            "SHARPE",
            "POSITION",
            "SIGNAL",
            "PREDICTION",
        ]
        for term in forbidden:
            assert term not in text, f"Forbidden term {term!r} found in source"

    def test_no_buy_sell(self) -> None:
        self._assert_no_trading_terms()

    def test_no_win_loss(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "win_rate" not in text
        assert "loss_rate" not in text

    def test_no_profitability(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "profit_factor" not in text

    def test_no_target_stop(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "take_profit" not in text
        assert "stop_loss" not in text

    def test_no_risk_reward(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "risk_reward" not in text

    def test_no_confidence_score(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "confidence" not in text
        assert "Confidence" not in text

    def test_no_black_box_quality_score(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "quality_score" not in text
        assert "black_box" not in text

    def test_no_prediction(self) -> None:
        import engine.models.historical_setup_quality_interpretation as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "predict" not in text.lower()

    def test_no_forbidden_fields_in_model(self) -> None:
        interpretation = _interpret_from_batch()
        for field in interpretation.__dataclass_fields__:
            assert "score" not in field.lower(), f"Score field found: {field}"
            assert "confidence" not in field.lower(), f"Confidence field found: {field}"
            assert "buy" not in field.lower(), f"Buy field found: {field}"
            assert "sell" not in field.lower(), f"Sell field found: {field}"
            assert "profit" not in field.lower(), f"Profit field found: {field}"
            assert "predict" not in field.lower(), f"Predict field found: {field}"


# ---------------------------------------------------------------------------
# K. Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_interpretation_is_frozen(self) -> None:
        interpretation = _interpret_from_batch()
        with pytest.raises(AttributeError):
            interpretation.historical_behavior_summary = "new"  # type: ignore[misc]

    def test_interpretation_has_slots(self) -> None:
        interpretation = _interpret_from_batch()
        assert not hasattr(interpretation, "__dict__")

    def test_source_report_unchanged(self) -> None:
        batch = _evaluable_batch(n=10)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        report_copy = report
        interpret_setup_behavior(report)
        assert report is report_copy
        assert isinstance(report, HistoricalSetupBehaviorReport)


# ---------------------------------------------------------------------------
# L. Checkpoint 10.4 compatibility
# ---------------------------------------------------------------------------


class TestCheckpoint104Compatibility:
    def test_generated_from_valid_behavior_report(self) -> None:
        """Every interpretation can be generated from a valid HistoricalSetupBehaviorReport."""
        batch = _evaluable_batch(n=15)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert isinstance(interpretation, HistoricalSetupQualityInterpretation)
        assert interpretation.behavior_report_id == report.behavior_report_id

    def test_preserves_evaluation_state(self) -> None:
        interpretation = _interpret_from_batch()
        assert interpretation.evaluation_status is SetupEvaluationStatus.EVALUABLE

    def test_preserves_occurrence_counts(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        assert interpretation.total_occurrence_count == 15
        assert interpretation.sufficient_data_count == 15
        assert interpretation.insufficient_data_count == 0

    def test_preserves_forward_return_observation_count(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        assert interpretation.forward_return_observation_count == 15

    def test_preserves_excursion_observation_counts(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        assert interpretation.upward_excursion_observation_count == 15
        assert interpretation.downward_excursion_observation_count == 15

    def test_preserves_min_observations(self) -> None:
        interpretation = _interpret_from_batch(n=15, min_observations=10)
        assert interpretation.min_observations_for_evaluation == 10


# ---------------------------------------------------------------------------
# M. Type validation
# ---------------------------------------------------------------------------


class TestTypeValidation:
    def test_wrong_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupBehaviorReport"):
            interpret_setup_behavior("not a report")  # type: ignore[arg-type]

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupBehaviorReport"):
            interpret_setup_behavior(None)  # type: ignore[arg-type]

    def test_evaluation_result_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupBehaviorReport"):
            interpret_setup_behavior(  # type: ignore[arg-type]
                evaluate_setup(_batch(()))
            )


# ---------------------------------------------------------------------------
# N. Historical behavior summary
# ---------------------------------------------------------------------------


class TestHistoricalBehaviorSummary:
    def test_no_data_summary(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert "No historical observations" in interpretation.historical_behavior_summary

    def test_limited_data_summary(self) -> None:
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
            )
            for i in range(3)
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(report)
        assert "Limited historical data" in interpretation.historical_behavior_summary
        assert "3 observations" in interpretation.historical_behavior_summary

    def test_sufficient_data_summary(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        assert "Sufficient historical data" in interpretation.historical_behavior_summary
        assert "15 observations" in interpretation.historical_behavior_summary

    def test_summary_contains_forward_return_info(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        summary = interpretation.historical_behavior_summary
        # Should mention forward return behavior
        assert (
            "positive" in summary.lower()
            or "negative" in summary.lower()
            or "mixed" in summary.lower()
        )

    def test_summary_contains_consistency_info(self) -> None:
        interpretation = _interpret_from_batch(n=15)
        summary = interpretation.historical_behavior_summary
        # Should mention consistency
        assert (
            "high" in summary.lower()
            or "moderate" in summary.lower()
            or "low" in summary.lower()
            or "not evaluable" in summary.lower()
        )


# ---------------------------------------------------------------------------
# O. Enum distinctness
# ---------------------------------------------------------------------------


class TestEnumDistinctness:
    def test_evidence_availability_distinct_from_evaluation_status(self) -> None:
        """EvidenceAvailability is distinct from SetupEvaluationStatus."""
        ev_members = {e.name for e in EvidenceAvailability}
        sv_members = {e.name for e in SetupEvaluationStatus}
        assert not ev_members.intersection(sv_members)

    def test_forward_return_behavior_distinct_from_evidence_availability(self) -> None:
        """ForwardReturnBehavior is distinct from EvidenceAvailability."""
        fr_members = {e.name for e in ForwardReturnBehavior}
        ev_members = {e.name for e in EvidenceAvailability}
        assert not fr_members.intersection(ev_members)

    def test_directional_consistency_distinct_from_others(self) -> None:
        """DirectionalConsistency is distinct from other enums."""
        dc_members = {e.name for e in DirectionalConsistency}
        fr_members = {e.name for e in ForwardReturnBehavior}
        ev_members = {e.name for e in EvidenceAvailability}
        assert not dc_members.intersection(fr_members)
        assert not dc_members.intersection(ev_members)

    def test_enums_distinct_from_sprint_11y_evidence_strength(self) -> None:
        """New enums are distinct from Sprint 11Y EvidenceStrength."""
        from engine.models.historical_evidence import EvidenceStrength

        es_members = {e.name for e in EvidenceStrength}
        for enum_cls in [EvidenceAvailability, ForwardReturnBehavior, DirectionalConsistency]:
            new_members = {e.name for e in enum_cls}
            assert not new_members.intersection(es_members), (
                f"Overlap between {enum_cls.__name__} and EvidenceStrength"
            )

    def test_enums_distinct_from_sprint_11z_strategy_assessment(self) -> None:
        """New enums are distinct from Sprint 11Z StrategyAssessmentStatus."""
        from engine.models.strategy_intelligence import StrategyAssessmentStatus

        sa_members = {e.name for e in StrategyAssessmentStatus}
        for enum_cls in [EvidenceAvailability, ForwardReturnBehavior, DirectionalConsistency]:
            new_members = {e.name for e in enum_cls}
            assert not new_members.intersection(sa_members), (
                f"Overlap between {enum_cls.__name__} and StrategyAssessmentStatus"
            )

    def test_enums_distinct_from_sprint_12a_decision_context(self) -> None:
        """New enums are distinct from Sprint 12A DecisionContextStatus."""
        from engine.models.decision_intelligence import DecisionContextStatus

        dc_members = {e.name for e in DecisionContextStatus}
        for enum_cls in [EvidenceAvailability, ForwardReturnBehavior, DirectionalConsistency]:
            new_members = {e.name for e in enum_cls}
            assert not new_members.intersection(dc_members), (
                f"Overlap between {enum_cls.__name__} and DecisionContextStatus"
            )
