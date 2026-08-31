"""
Tests for Checkpoint 10.6 — Historical Setup Research Report Layer.

Deterministic, network-free: every test constructs interpretations directly
(no provider, no corpus engine, no pipeline, no candles).
The research-report layer consumes a HistoricalSetupQualityInterpretation
(Checkpoint 10.5) and produces a final, human-readable historical setup
research report describing what the historical research tells us about
this setup.

Coverage:

A. Model — frozen, slots, validation, deterministic identity
B. NO_DATA — no historical occurrences, correct research conclusion
C. INSUFFICIENT_DATA — limited observations, limitation statement
D. EVALUABLE — sufficient observations, historical conclusion
E. Forward-return interpretation — positive, negative, mixed, none
F. Consistency — high, moderate, low, not evaluable
G. Missing / zero — real zero preserved, missing remains missing
H. Traceability — all upstream identifiers preserved
I. Determinism — repeated generation produces identical output
J. Immutability — source interpretation not mutated
K. Point-in-time safety — no candle/provider/store imports
L. Forbidden semantics — no trading-decision logic, no scores
M. Type validation — TypeError on wrong input type
N. Research conclusion content — descriptive historical language
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
from engine.models.historical_setup_research_report import (
    HistoricalSetupResearchReport,
    generate_historical_setup_research_report,
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


def _interpretation(
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
) -> HistoricalSetupQualityInterpretation:
    """Build a valid HistoricalSetupQualityInterpretation with given parameters."""
    evaluation_id = "setup-eval-test1234567890"
    behavior_report_id = (
        "behavior-"
        + __import__("hashlib")
        .sha256(evaluation_id.encode("utf-8"))
        .hexdigest()[:16]
    )
    interpretation_id = (
        "setup-quality-interpretation-"
        + __import__("hashlib")
        .sha256(behavior_report_id.encode("utf-8"))
        .hexdigest()[:16]
    )

    evidence_availability = _derive_evidence_availability(evaluation_status)
    forward_return_behavior = _classify_forward_return(
        forward_return_observation_count,
        proportion_positive_forward_return,
    )
    directional_consistency = _classify_consistency(
        forward_return_direction_consistency,
    )

    historical_behavior_summary = _build_summary(
        evidence_availability,
        forward_return_behavior,
        directional_consistency,
    )

    return HistoricalSetupQualityInterpretation(
        interpretation_id=interpretation_id,
        behavior_report_id=behavior_report_id,
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
        evidence_availability=evidence_availability,
        forward_return_behavior=forward_return_behavior,
        directional_consistency=directional_consistency,
        historical_behavior_summary=historical_behavior_summary,
    )


def _derive_evidence_availability(
    status: SetupEvaluationStatus,
) -> EvidenceAvailability:
    if status is SetupEvaluationStatus.NO_DATA:
        return EvidenceAvailability.NO_HISTORICAL_DATA
    elif status is SetupEvaluationStatus.INSUFFICIENT_DATA:
        return EvidenceAvailability.LIMITED_HISTORICAL_DATA
    return EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA


def _classify_forward_return(
    count: int,
    proportion: float | None,
) -> ForwardReturnBehavior:
    if count == 0:
        return ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
    if proportion is None:
        return ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
    if proportion > 0.6:
        return ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
    elif proportion < 0.4:
        return ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
    return ForwardReturnBehavior.MIXED_DIRECTION


def _classify_consistency(
    consistency: float | None,
) -> DirectionalConsistency:
    if consistency is None:
        return DirectionalConsistency.NOT_EVALUABLE
    if consistency >= 0.75:
        return DirectionalConsistency.HIGH_CONSISTENCY
    elif consistency >= 0.6:
        return DirectionalConsistency.MODERATE_CONSISTENCY
    return DirectionalConsistency.LOW_CONSISTENCY


def _build_summary(
    availability: EvidenceAvailability,
    behavior: ForwardReturnBehavior,
    consistency: DirectionalConsistency,
) -> str:
    if availability is EvidenceAvailability.NO_HISTORICAL_DATA:
        return "No historical observations are available for this setup criterion."
    if availability is EvidenceAvailability.LIMITED_HISTORICAL_DATA:
        return "Limited historical data."
    return "Sufficient historical data."


def _report_from_batch(
    n: int = 15,
    min_observations: int = 5,
) -> HistoricalSetupResearchReport:
    """Build a research report from a batch of n occurrences."""
    batch = _evaluable_batch(n=n)
    evaluation = evaluate_setup(batch, min_observations_for_evaluation=min_observations)
    quality = analyze_setup_quality(batch)
    behavior = assess_setup_behavior(evaluation, quality)
    interpretation = interpret_setup_behavior(behavior)
    return generate_historical_setup_research_report(interpretation)


# ---------------------------------------------------------------------------
# A. Model tests
# ---------------------------------------------------------------------------


class TestModel:
    def test_frozen(self) -> None:
        report = _report_from_batch()
        with pytest.raises(AttributeError):
            report.report_id = "new"  # type: ignore[misc]

    def test_slots(self) -> None:
        report = _report_from_batch()
        assert not hasattr(report, "__dict__")
        assert hasattr(type(report), "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            report.arbitrary_attribute = "x"  # type: ignore[attr-defined]

    def test_report_id_starts_with_prefix(self) -> None:
        report = _report_from_batch()
        assert report.report_id.startswith("historical-setup-research-")

    def test_report_id_is_deterministic(self) -> None:
        r1 = _report_from_batch()
        r2 = _report_from_batch()
        assert r1.report_id == r2.report_id

    def test_different_input_produces_different_id(self) -> None:
        r1 = _report_from_batch(n=10)
        r2 = _report_from_batch(n=15)
        assert r1.report_id != r2.report_id

    def test_traceability_to_interpretation(self) -> None:
        report = _report_from_batch()
        assert report.interpretation_id.startswith("setup-quality-interpretation-")

    def test_traceability_to_behavior_report(self) -> None:
        report = _report_from_batch()
        assert report.behavior_report_id.startswith("behavior-")

    def test_traceability_to_evaluation(self) -> None:
        report = _report_from_batch()
        assert isinstance(report.evaluation_id, str)
        assert len(report.evaluation_id) > 0

    def test_traceability_to_batch(self) -> None:
        report = _report_from_batch()
        assert isinstance(report.batch_id, str)
        assert len(report.batch_id) > 0

    def test_traceability_criterion_key(self) -> None:
        report = _report_from_batch()
        assert report.criterion_key == "NIFTY|15m|1D"

    def test_traceability_instrument(self) -> None:
        report = _report_from_batch()
        assert report.instrument == "NIFTY"

    def test_traceability_timeframes(self) -> None:
        report = _report_from_batch()
        assert report.setup_timeframe == "15m"
        assert report.context_timeframe == "1D"

    def test_validation_negative_count(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            _interpretation(total_occurrence_count=-1)

    def test_validation_count_sum(self) -> None:
        with pytest.raises(ValueError, match="must equal"):
            _interpretation(
                total_occurrence_count=5,
                sufficient_data_count=2,
                insufficient_data_count=2,
            )

    def test_validation_no_data_requires_no_historical(self) -> None:
        with pytest.raises(ValueError, match="NO_HISTORICAL_DATA"):
            HistoricalSetupResearchReport(
                report_id="historical-setup-research-abc",
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
                research_conclusion="test",
            )

    def test_validation_insufficient_requires_limited(self) -> None:
        with pytest.raises(ValueError, match="LIMITED_HISTORICAL_DATA"):
            HistoricalSetupResearchReport(
                report_id="historical-setup-research-abc",
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
                research_conclusion="test",
            )

    def test_validation_evaluable_requires_sufficient(self) -> None:
        with pytest.raises(ValueError, match="SUFFICIENT_HISTORICAL_DATA"):
            HistoricalSetupResearchReport(
                report_id="historical-setup-research-abc",
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
                research_conclusion="test",
            )

    def test_validation_no_observations_requires_no_directional(self) -> None:
        with pytest.raises(ValueError, match="NO_DIRECTIONAL_OBSERVATION"):
            HistoricalSetupResearchReport(
                report_id="historical-setup-research-abc",
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
                research_conclusion="test",
            )

    def test_validation_no_data_requires_not_evaluable(self) -> None:
        with pytest.raises(ValueError, match="NOT_EVALUABLE"):
            HistoricalSetupResearchReport(
                report_id="historical-setup-research-abc",
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
                research_conclusion="test",
            )


# ---------------------------------------------------------------------------
# B. NO_DATA
# ---------------------------------------------------------------------------


class TestNoData:
    def test_no_historical_occurrences(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert report.total_occurrence_count == 0
        assert report.is_empty is True

    def test_correct_research_conclusion_no_data(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert "No historical occurrences" in report.research_conclusion

    def test_no_false_behavioral_classification(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.forward_return_behavior
            is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
        )
        assert (
            report.directional_consistency
            is DirectionalConsistency.NOT_EVALUABLE
        )


# ---------------------------------------------------------------------------
# C. INSUFFICIENT_DATA
# ---------------------------------------------------------------------------


class TestInsufficientData:
    def test_limited_observations(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
            total_occurrence_count=3,
            sufficient_data_count=3,
            insufficient_data_count=0,
            forward_return_observation_count=3,
            upward_excursion_observation_count=3,
            downward_excursion_observation_count=3,
            min_observations_for_evaluation=10,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert report.total_occurrence_count == 3
        assert report.has_sufficient_evidence is False

    def test_correct_limitation_statement(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
            total_occurrence_count=3,
            sufficient_data_count=3,
            insufficient_data_count=0,
            forward_return_observation_count=3,
            upward_excursion_observation_count=3,
            downward_excursion_observation_count=3,
            min_observations_for_evaluation=10,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert "limited" in report.research_conclusion.lower()

    def test_no_implication_of_reliability(self) -> None:
        """Insufficient data must not imply reliability beyond the threshold."""
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
            total_occurrence_count=3,
            sufficient_data_count=3,
            insufficient_data_count=0,
            forward_return_observation_count=3,
            upward_excursion_observation_count=3,
            downward_excursion_observation_count=3,
            min_observations_for_evaluation=10,
        )
        report = generate_historical_setup_research_report(interpretation)
        conclusion = report.research_conclusion.lower()
        assert "reliable" not in conclusion
        assert "confidence" not in conclusion
        assert "strong" not in conclusion


# ---------------------------------------------------------------------------
# D. EVALUABLE
# ---------------------------------------------------------------------------


class TestEvaluable:
    def test_sufficient_observations(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        assert report.total_occurrence_count == 15
        assert report.has_sufficient_evidence is True

    def test_historical_conclusion_generated(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        assert "sufficient" in report.research_conclusion.lower()

    def test_forward_return_info_in_conclusion(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        conclusion = report.research_conclusion.lower()
        assert (
            "positive" in conclusion
            or "negative" in conclusion
            or "mixed" in conclusion
        )

    def test_consistency_info_in_conclusion(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        conclusion = report.research_conclusion.lower()
        assert (
            "high" in conclusion
            or "moderate" in conclusion
            or "low" in conclusion
        )


# ---------------------------------------------------------------------------
# E. Forward-return interpretation
# ---------------------------------------------------------------------------


class TestForwardReturnInterpretation:
    def test_predominantly_positive(self) -> None:
        interpretation = _interpretation(
            total_occurrence_count=24,
            sufficient_data_count=24,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=18,
            negative_forward_return_observation_count=4,
            zero_forward_return_observation_count=2,
            proportion_positive_forward_return=18 / 24,
            forward_return_direction_consistency=18 / 22,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
        )
        assert "predominantly positive" in report.research_conclusion.lower()

    def test_predominantly_negative(self) -> None:
        interpretation = _interpretation(
            total_occurrence_count=24,
            sufficient_data_count=24,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=4,
            negative_forward_return_observation_count=18,
            zero_forward_return_observation_count=2,
            proportion_positive_forward_return=4 / 24,
            forward_return_direction_consistency=18 / 22,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.forward_return_behavior
            is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
        )
        assert "predominantly negative" in report.research_conclusion.lower()

    def test_mixed_direction(self) -> None:
        interpretation = _interpretation(
            total_occurrence_count=24,
            sufficient_data_count=24,
            forward_return_observation_count=24,
            positive_forward_return_observation_count=12,
            negative_forward_return_observation_count=8,
            zero_forward_return_observation_count=4,
            proportion_positive_forward_return=12 / 24,
            forward_return_direction_consistency=12 / 20,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.forward_return_behavior
            is ForwardReturnBehavior.MIXED_DIRECTION
        )
        assert "mixed direction" in report.research_conclusion.lower()

    def test_no_directional_observation(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.forward_return_behavior
            is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
        )
        assert "no historical occurrences" in report.research_conclusion.lower()


# ---------------------------------------------------------------------------
# F. Consistency
# ---------------------------------------------------------------------------


class TestConsistency:
    def test_high_consistency(self) -> None:
        interpretation = _interpretation(
            forward_return_direction_consistency=0.8,
            proportion_positive_forward_return=0.8,
            positive_forward_return_observation_count=8,
            negative_forward_return_observation_count=2,
            zero_forward_return_observation_count=0,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.directional_consistency
            is DirectionalConsistency.HIGH_CONSISTENCY
        )
        assert "high directional consistency" in report.research_conclusion.lower()

    def test_moderate_consistency(self) -> None:
        interpretation = _interpretation(
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
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.directional_consistency
            is DirectionalConsistency.MODERATE_CONSISTENCY
        )
        assert "moderate directional consistency" in report.research_conclusion.lower()

    def test_low_consistency(self) -> None:
        interpretation = _interpretation(
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
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.directional_consistency
            is DirectionalConsistency.LOW_CONSISTENCY
        )
        assert "low directional consistency" in report.research_conclusion.lower()

    def test_not_evaluable(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.directional_consistency
            is DirectionalConsistency.NOT_EVALUABLE
        )
        assert "no historical occurrences" in report.research_conclusion.lower()


# ---------------------------------------------------------------------------
# G. Missing / zero / insufficient
# ---------------------------------------------------------------------------


class TestMissingZeroInsufficient:
    def test_real_zero_preserved(self) -> None:
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
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        assert report.total_occurrence_count == 1
        assert report.forward_return_observation_count == 1

    def test_missing_remains_missing(self) -> None:
        """Missing values remain None, not converted to zero."""
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert report.forward_return_behavior is (
            ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
        )

    def test_insufficient_remains_distinct(self) -> None:
        """Insufficient data is distinct from NO_DATA and EVALUABLE."""
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
            total_occurrence_count=3,
            sufficient_data_count=3,
            insufficient_data_count=0,
            forward_return_observation_count=3,
            upward_excursion_observation_count=3,
            downward_excursion_observation_count=3,
            min_observations_for_evaluation=10,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert report.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert (
            report.evidence_availability
            is EvidenceAvailability.LIMITED_HISTORICAL_DATA
        )


# ---------------------------------------------------------------------------
# H. Traceability
# ---------------------------------------------------------------------------


class TestTraceability:
    def test_interpretation_id_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.interpretation_id == interpretation.interpretation_id

    def test_behavior_report_id_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.behavior_report_id == interpretation.behavior_report_id

    def test_evaluation_id_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.evaluation_id == interpretation.evaluation_id

    def test_batch_id_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.batch_id == interpretation.batch_id

    def test_criterion_key_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.criterion_key == interpretation.criterion_key

    def test_instrument_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.instrument == interpretation.instrument

    def test_timeframes_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.setup_timeframe == interpretation.setup_timeframe
        assert report.context_timeframe == interpretation.context_timeframe

    def test_evaluation_state_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert report.evaluation_status == interpretation.evaluation_status
        assert report.total_occurrence_count == interpretation.total_occurrence_count
        assert report.sufficient_data_count == interpretation.sufficient_data_count
        assert report.insufficient_data_count == interpretation.insufficient_data_count
        assert (
            report.forward_return_observation_count
            == interpretation.forward_return_observation_count
        )
        assert (
            report.upward_excursion_observation_count
            == interpretation.upward_excursion_observation_count
        )
        assert (
            report.downward_excursion_observation_count
            == interpretation.downward_excursion_observation_count
        )
        assert (
            report.min_observations_for_evaluation
            == interpretation.min_observations_for_evaluation
        )


# ---------------------------------------------------------------------------
# I. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_generation_identical(self) -> None:
        interpretation = _interpretation()
        r1 = generate_historical_setup_research_report(interpretation)
        r2 = generate_historical_setup_research_report(interpretation)
        assert r1 == r2

    def test_same_input_same_output(self) -> None:
        r1 = _report_from_batch(n=15, min_observations=5)
        r2 = _report_from_batch(n=15, min_observations=5)
        assert r1 == r2

    def test_report_id_deterministic(self) -> None:
        r1 = _report_from_batch(n=10)
        r2 = _report_from_batch(n=10)
        assert r1.report_id == r2.report_id

    def test_different_input_different_id(self) -> None:
        r1 = _report_from_batch(n=10)
        r2 = _report_from_batch(n=20)
        assert r1.report_id != r2.report_id


# ---------------------------------------------------------------------------
# J. Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_report_is_frozen(self) -> None:
        report = _report_from_batch()
        with pytest.raises(AttributeError):
            report.research_conclusion = "new"  # type: ignore[misc]

    def test_report_has_slots(self) -> None:
        report = _report_from_batch()
        assert not hasattr(report, "__dict__")

    def test_source_interpretation_unchanged(self) -> None:
        interpretation = _interpretation()
        id_before = interpretation.interpretation_id
        availability_before = interpretation.evidence_availability
        generate_historical_setup_research_report(interpretation)
        assert interpretation.interpretation_id == id_before
        assert interpretation.evidence_availability == availability_before


# ---------------------------------------------------------------------------
# K. Point-in-time safety
# ---------------------------------------------------------------------------


class TestPointInTimeSafety:
    def test_no_candle_access(self) -> None:
        report = _report_from_batch(n=5)
        assert "candle" not in type(report).__module__

    def test_no_provider_access(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        assert src is not None
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text
        assert "HistoricalMarketDataProvider" not in text

    def test_no_store_access(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text

    def test_no_corpus_slicing(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalResearchCorpusEngine" not in text
        assert "research_corpus" not in text

    def test_no_temporal_slicing(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "timedelta" not in text
        assert "datetime" not in text

    def test_no_discovery_mutation(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "setup_discovery" not in text


# ---------------------------------------------------------------------------
# L. Forbidden semantics
# ---------------------------------------------------------------------------


class TestForbiddenSemantics:
    def _assert_no_trading_terms(self) -> None:
        import engine.models.historical_setup_research_report as mod

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
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "win_rate" not in text
        assert "loss_rate" not in text

    def test_no_profitability(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "profit_factor" not in text

    def test_no_target_stop(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "take_profit" not in text
        assert "stop_loss" not in text

    def test_no_risk_reward(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "risk_reward" not in text

    def test_no_confidence_score(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "confidence" not in text
        assert "Confidence" not in text

    def test_no_black_box_quality_score(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "quality_score" not in text
        assert "black_box" not in text

    def test_no_prediction(self) -> None:
        import engine.models.historical_setup_research_report as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "predict" not in text.lower()

    def test_no_score_fields_in_model(self) -> None:
        report = _report_from_batch()
        for field in report.__dataclass_fields__:
            assert "score" not in field.lower(), f"Score field found: {field}"
            assert "confidence" not in field.lower(), f"Confidence field found: {field}"
            assert "buy" not in field.lower(), f"Buy field found: {field}"
            assert "sell" not in field.lower(), f"Sell field found: {field}"
            assert "profit" not in field.lower(), f"Profit field found: {field}"
            assert "predict" not in field.lower(), f"Predict field found: {field}"


# ---------------------------------------------------------------------------
# M. Type validation
# ---------------------------------------------------------------------------


class TestTypeValidation:
    def test_wrong_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupQualityInterpretation"):
            generate_historical_setup_research_report("not an interpretation")  # type: ignore[arg-type]

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupQualityInterpretation"):
            generate_historical_setup_research_report(None)  # type: ignore[arg-type]

    def test_behavior_report_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupQualityInterpretation"):
            generate_historical_setup_research_report(  # type: ignore[arg-type]
                HistoricalSetupBehaviorReport(
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
                    forward_return_mean=None,
                    forward_return_median=None,
                    forward_return_minimum=None,
                    forward_return_maximum=None,
                    positive_forward_return_observation_count=0,
                    negative_forward_return_observation_count=0,
                    zero_forward_return_observation_count=0,
                    proportion_positive_forward_return=None,
                    forward_return_direction_consistency=None,
                    upward_excursion_mean=None,
                    upward_excursion_median=None,
                    downward_excursion_mean=None,
                    downward_excursion_median=None,
                )
            )


# ---------------------------------------------------------------------------
# N. Research conclusion content
# ---------------------------------------------------------------------------


class TestResearchConclusion:
    def test_no_data_conclusion(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.NO_DATA,
            total_occurrence_count=0,
            sufficient_data_count=0,
            insufficient_data_count=0,
            forward_return_observation_count=0,
            upward_excursion_observation_count=0,
            downward_excursion_observation_count=0,
            forward_return_mean=None,
            forward_return_median=None,
            forward_return_minimum=None,
            forward_return_maximum=None,
            proportion_positive_forward_return=None,
            forward_return_direction_consistency=None,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.research_conclusion
            == "No historical occurrences were available for this setup criterion."
        )

    def test_limited_data_conclusion(self) -> None:
        interpretation = _interpretation(
            evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
            total_occurrence_count=3,
            sufficient_data_count=3,
            insufficient_data_count=0,
            forward_return_observation_count=3,
            upward_excursion_observation_count=3,
            downward_excursion_observation_count=3,
            min_observations_for_evaluation=10,
        )
        report = generate_historical_setup_research_report(interpretation)
        assert "limited" in report.research_conclusion.lower()
        assert "3" in report.research_conclusion

    def test_sufficient_data_conclusion(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        assert "sufficient" in report.research_conclusion.lower()

    def test_conclusion_no_prediction_language(self) -> None:
        """Research conclusion must not use predictive language."""
        report = _report_from_batch(n=15, min_observations=5)
        conclusion = report.research_conclusion.lower()
        assert "will" not in conclusion
        assert "likely" not in conclusion
        assert "expected" not in conclusion
        assert "probability" not in conclusion

    def test_conclusion_no_trading_language(self) -> None:
        """Research conclusion must not use trading language."""
        report = _report_from_batch(n=15, min_observations=5)
        conclusion = report.research_conclusion.lower()
        assert "buy" not in conclusion
        assert "sell" not in conclusion
        assert "trade" not in conclusion
        assert "entry" not in conclusion

    def test_historical_behavior_summary_preserved(self) -> None:
        interpretation = _interpretation()
        report = generate_historical_setup_research_report(interpretation)
        assert (
            report.historical_behavior_summary
            == interpretation.historical_behavior_summary
        )
