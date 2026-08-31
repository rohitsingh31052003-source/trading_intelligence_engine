"""
Tests for Checkpoint 10.7 — Historical Setup Research Adequacy Boundary.

Deterministic, network-free: every test constructs reports directly
(no provider, no corpus engine, no pipeline, no candles).
The research-adequacy layer consumes a HistoricalSetupResearchReport
(Checkpoint 10.6) and produces a deterministic, immutable adequacy
classification that answers: "Is the historical setup research report
adequate for downstream descriptive research?"

Coverage:

A. Model contract — frozen, slots, required fields, enum validity, invariants
B. NO_DATA — produces NO_RESEARCH_DATA, zero counts, deterministic reason
C. INSUFFICIENT_DATA — produces INSUFFICIENT_RESEARCH_DATA, threshold, reason
D. EVALUABLE — produces ADEQUATE_FOR_DESCRIPTIVE_RESEARCH, no extra scoring
E. Traceability — adequacy -> report -> interpretation -> behavior -> evaluation -> batch
F. Missing data — missing values remain missing
G. Zero preservation — genuine zero return remains valid observation
H. Determinism — repeated evaluation produces identical output and ID
I. Immutability — source report remains unchanged
J. No temporal access — no candle/data/provider/corpus imports
K. Forbidden semantics — no trading concepts or score fields
L. Checkpoint 10.6 compatibility — NO_DATA/INSUFFICIENT_DATA/EVALUABLE accepted
M. Boundary cases — zero/one/exactly threshold/threshold-1/threshold+1 observations
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
from engine.models.historical_setup_research_adequacy import (
    HistoricalSetupResearchAdequacy,
    ResearchAdequacy,
    assess_historical_setup_research_adequacy,
)
from engine.models.historical_setup_research_report import (
    HistoricalSetupResearchReport,
    generate_historical_setup_research_report,
)
from engine.models.historical_setup_discovery import HistoricalSetupCandidate


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


def _adequacy_from_batch(
    n: int = 15,
    min_observations: int = 5,
) -> HistoricalSetupResearchAdequacy:
    """Build an adequacy result from a batch of n occurrences."""
    report = _report_from_batch(n=n, min_observations=min_observations)
    return assess_historical_setup_research_adequacy(report)


# ---------------------------------------------------------------------------
# A. Model contract
# ---------------------------------------------------------------------------


class TestModelContract:
    def test_frozen(self) -> None:
        result = _adequacy_from_batch()
        with pytest.raises(AttributeError):
            result.adequacy_id = "new"  # type: ignore[misc]

    def test_slots(self) -> None:
        result = _adequacy_from_batch()
        assert not hasattr(result, "__dict__")
        assert hasattr(type(result), "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            result.arbitrary_attribute = "x"  # type: ignore[attr-defined]

    def test_adequacy_id_starts_with_prefix(self) -> None:
        result = _adequacy_from_batch()
        assert result.adequacy_id.startswith("historical-setup-adequacy-")

    def test_adequacy_id_is_deterministic(self) -> None:
        r1 = _adequacy_from_batch()
        r2 = _adequacy_from_batch()
        assert r1.adequacy_id == r2.adequacy_id

    def test_different_input_produces_different_id(self) -> None:
        r1 = _adequacy_from_batch(n=10)
        r2 = _adequacy_from_batch(n=15)
        assert r1.adequacy_id != r2.adequacy_id

    def test_enum_members(self) -> None:
        assert ResearchAdequacy.NO_RESEARCH_DATA.value == "NO_RESEARCH_DATA"
        assert (
            ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA.value
            == "INSUFFICIENT_RESEARCH_DATA"
        )
        assert (
            ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH.value
            == "ADEQUATE_FOR_DESCRIPTIVE_RESEARCH"
        )

    def test_enum_is_adequate_property(self) -> None:
        assert ResearchAdequacy.NO_RESEARCH_DATA.is_adequate is False
        assert ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA.is_adequate is False
        assert ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH.is_adequate is True

    def test_enum_has_research_data_property(self) -> None:
        assert ResearchAdequacy.NO_RESEARCH_DATA.has_research_data is False
        assert ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA.has_research_data is True
        assert (
            ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH.has_research_data
            is True
        )

    def test_validation_negative_count(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                total_occurrence_count=-1,
                sufficient_data_count=0,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                min_observations_for_evaluation=5,
                adequacy=ResearchAdequacy.NO_RESEARCH_DATA,
                reason="test",
            )

    def test_validation_count_sum(self) -> None:
        with pytest.raises(ValueError, match="must equal"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                total_occurrence_count=5,
                sufficient_data_count=2,
                insufficient_data_count=2,
                forward_return_observation_count=5,
                upward_excursion_observation_count=5,
                downward_excursion_observation_count=5,
                min_observations_for_evaluation=10,
                adequacy=ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA,
                reason="test",
            )

    def test_validation_no_data_requires_no_research_data(self) -> None:
        with pytest.raises(ValueError, match="NO_RESEARCH_DATA"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                adequacy=ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA,
                reason="test",
            )

    def test_validation_insufficient_requires_insufficient_research(self) -> None:
        with pytest.raises(ValueError, match="INSUFFICIENT_RESEARCH_DATA"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                adequacy=ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH,
                reason="test",
            )

    def test_validation_evaluable_requires_adequate(self) -> None:
        with pytest.raises(ValueError, match="ADEQUATE_FOR_DESCRIPTIVE_RESEARCH"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                adequacy=ResearchAdequacy.NO_RESEARCH_DATA,
                reason="test",
            )

    def test_validation_no_data_requires_zero_occurrences(self) -> None:
        with pytest.raises(ValueError, match="total_occurrence_count == 0"):
            HistoricalSetupResearchAdequacy(
                adequacy_id="historical-setup-adequacy-abc",
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
                total_occurrence_count=1,
                sufficient_data_count=1,
                insufficient_data_count=0,
                forward_return_observation_count=1,
                upward_excursion_observation_count=1,
                downward_excursion_observation_count=1,
                min_observations_for_evaluation=5,
                adequacy=ResearchAdequacy.NO_RESEARCH_DATA,
                reason="test",
            )


# ---------------------------------------------------------------------------
# B. NO_DATA
# ---------------------------------------------------------------------------


class TestNoData:
    def test_produces_no_research_data(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.NO_RESEARCH_DATA

    def test_zero_counts_preserved(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 0
        assert result.sufficient_data_count == 0
        assert result.insufficient_data_count == 0
        assert result.forward_return_observation_count == 0
        assert result.upward_excursion_observation_count == 0
        assert result.downward_excursion_observation_count == 0

    def test_deterministic_reason(self) -> None:
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
        r1 = assess_historical_setup_research_adequacy(report)
        r2 = assess_historical_setup_research_adequacy(report)
        assert r1.reason == r2.reason
        assert "No historical setup observations" in r1.reason

    def test_is_adequate_false(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.is_adequate is False
        assert result.has_research_data is False


# ---------------------------------------------------------------------------
# C. INSUFFICIENT_DATA
# ---------------------------------------------------------------------------


class TestInsufficientData:
    def test_produces_insufficient_research_data(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA

    def test_threshold_preserved(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.min_observations_for_evaluation == 10

    def test_counts_preserved(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 3
        assert result.forward_return_observation_count == 3

    def test_reason_identifies_insufficient_sample(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert "below the evaluation threshold" in result.reason
        assert "10" in result.reason

    def test_is_adequate_false(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.is_adequate is False
        assert result.has_research_data is True


# ---------------------------------------------------------------------------
# D. EVALUABLE
# ---------------------------------------------------------------------------


class TestEvaluable:
    def test_produces_adequate_for_descriptive_research(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH

    def test_counts_preserved(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        assert result.total_occurrence_count == 15
        assert result.sufficient_data_count == 15
        assert result.insufficient_data_count == 0
        assert result.forward_return_observation_count == 15

    def test_threshold_preserved(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        assert result.min_observations_for_evaluation == 5

    def test_no_additional_scoring(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        for field in result.__dataclass_fields__:
            assert "score" not in field.lower(), f"Score field found: {field}"
            assert "confidence" not in field.lower(), f"Confidence field found: {field}"

    def test_reason_identifies_sufficient_evidence(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        assert "sufficiently populated" in result.reason
        assert "adequate for downstream descriptive research" in result.reason

    def test_is_adequate_true(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        assert result.is_adequate is True
        assert result.has_research_data is True


# ---------------------------------------------------------------------------
# E. Traceability
# ---------------------------------------------------------------------------


class TestTraceability:
    def test_report_id_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.report_id == report.report_id

    def test_interpretation_id_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.interpretation_id == report.interpretation_id

    def test_behavior_report_id_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.behavior_report_id == report.behavior_report_id

    def test_evaluation_id_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.evaluation_id == report.evaluation_id

    def test_batch_id_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.batch_id == report.batch_id

    def test_criterion_key_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.criterion_key == report.criterion_key

    def test_instrument_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.instrument == report.instrument

    def test_timeframes_preserved(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.setup_timeframe == report.setup_timeframe
        assert result.context_timeframe == report.context_timeframe

    def test_full_chain_traceability(self) -> None:
        """Verify adequacy -> report -> interpretation -> behavior -> evaluation -> batch."""
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.report_id.startswith("historical-setup-research-")
        assert result.interpretation_id.startswith("setup-quality-interpretation-")
        assert result.behavior_report_id.startswith("behavior-")
        assert isinstance(result.evaluation_id, str)
        assert len(result.evaluation_id) > 0
        assert isinstance(result.batch_id, str)
        assert len(result.batch_id) > 0


# ---------------------------------------------------------------------------
# F. Missing data
# ---------------------------------------------------------------------------


class TestMissingData:
    def test_missing_values_remain_missing(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 0
        assert result.forward_return_observation_count == 0

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
        result = assess_historical_setup_research_adequacy(report)
        assert result.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert result.adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA


# ---------------------------------------------------------------------------
# G. Zero preservation
# ---------------------------------------------------------------------------


class TestZeroPreservation:
    def test_genuine_zero_return_remains_valid_observation(self) -> None:
        """A real observed zero forward return is counted, not treated as missing."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-zero",
                forward_return=0.0,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 1
        assert result.forward_return_observation_count == 1

    def test_zero_does_not_alter_adequacy_classification(self) -> None:
        """A single zero observation with min_observations=1 is EVALUABLE."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-zero",
                forward_return=0.0,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.evaluation_status is SetupEvaluationStatus.EVALUABLE
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH


# ---------------------------------------------------------------------------
# H. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_evaluation_identical(self) -> None:
        result = _adequacy_from_batch(n=15, min_observations=5)
        r1 = assess_historical_setup_research_adequacy(_report_from_batch(n=15, min_observations=5))
        r2 = assess_historical_setup_research_adequacy(_report_from_batch(n=15, min_observations=5))
        assert r1 == r2

    def test_same_input_same_output(self) -> None:
        r1 = _adequacy_from_batch(n=15, min_observations=5)
        r2 = _adequacy_from_batch(n=15, min_observations=5)
        assert r1 == r2

    def test_adequacy_id_deterministic(self) -> None:
        r1 = _adequacy_from_batch(n=10)
        r2 = _adequacy_from_batch(n=10)
        assert r1.adequacy_id == r2.adequacy_id

    def test_different_input_different_id(self) -> None:
        r1 = _adequacy_from_batch(n=10)
        r2 = _adequacy_from_batch(n=20)
        assert r1.adequacy_id != r2.adequacy_id

    def test_reason_deterministic(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        r1 = assess_historical_setup_research_adequacy(report)
        r2 = assess_historical_setup_research_adequacy(report)
        assert r1.reason == r2.reason


# ---------------------------------------------------------------------------
# I. Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_result_is_frozen(self) -> None:
        result = _adequacy_from_batch()
        with pytest.raises(AttributeError):
            result.adequacy = ResearchAdequacy.NO_RESEARCH_DATA  # type: ignore[misc]

    def test_result_has_slots(self) -> None:
        result = _adequacy_from_batch()
        assert not hasattr(result, "__dict__")

    def test_source_report_unchanged(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        report_id_before = report.report_id
        adequacy_before = report.evidence_availability
        assess_historical_setup_research_adequacy(report)
        assert report.report_id == report_id_before
        assert report.evidence_availability == adequacy_before


# ---------------------------------------------------------------------------
# J. No temporal access
# ---------------------------------------------------------------------------


class TestNoTemporalAccess:
    def test_no_candle_access(self) -> None:
        result = _adequacy_from_batch(n=5)
        assert "candle" not in type(result).__module__

    def test_no_provider_access(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        assert src is not None
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text
        assert "HistoricalMarketDataProvider" not in text

    def test_no_store_access(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text

    def test_no_corpus_slicing(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalResearchCorpusEngine" not in text
        assert "research_corpus" not in text

    def test_no_temporal_slicing(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "timedelta" not in text
        assert "datetime" not in text

    def test_no_discovery_mutation(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "setup_discovery" not in text


# ---------------------------------------------------------------------------
# K. Forbidden semantics
# ---------------------------------------------------------------------------


class TestForbiddenSemantics:
    def _assert_no_trading_terms(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

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
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "win_rate" not in text
        assert "loss_rate" not in text

    def test_no_profitability(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "profit_factor" not in text

    def test_no_target_stop(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "take_profit" not in text
        assert "stop_loss" not in text

    def test_no_risk_reward(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "risk_reward" not in text

    def test_no_confidence_score(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "confidence" not in text
        assert "Confidence" not in text

    def test_no_black_box_quality_score(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "quality_score" not in text
        assert "black_box" not in text

    def test_no_prediction(self) -> None:
        import engine.models.historical_setup_research_adequacy as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "predict" not in text.lower()

    def test_no_score_fields_in_model(self) -> None:
        result = _adequacy_from_batch()
        for field in result.__dataclass_fields__:
            assert "score" not in field.lower(), f"Score field found: {field}"
            assert "confidence" not in field.lower(), f"Confidence field found: {field}"
            assert "buy" not in field.lower(), f"Buy field found: {field}"
            assert "sell" not in field.lower(), f"Sell field found: {field}"
            assert "profit" not in field.lower(), f"Profit field found: {field}"
            assert "predict" not in field.lower(), f"Predict field found: {field}"


# ---------------------------------------------------------------------------
# L. Checkpoint 10.6 compatibility
# ---------------------------------------------------------------------------


class TestCheckpoint106Compatibility:
    def test_no_data_report_accepted(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.NO_RESEARCH_DATA

    def test_insufficient_data_report_accepted(self) -> None:
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA

    def test_evaluable_report_accepted(self) -> None:
        report = _report_from_batch(n=15, min_observations=5)
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH


# ---------------------------------------------------------------------------
# M. Boundary cases
# ---------------------------------------------------------------------------


class TestBoundaryCases:
    def test_zero_observations(self) -> None:
        """Zero observations produces NO_RESEARCH_DATA."""
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
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.NO_RESEARCH_DATA
        assert result.total_occurrence_count == 0

    def test_one_observation_below_threshold(self) -> None:
        """One observation with min_observations=10 is INSUFFICIENT_RESEARCH_DATA."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-1",
                forward_return=0.05,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA
        assert result.total_occurrence_count == 1
        assert result.min_observations_for_evaluation == 10

    def test_one_observation_at_threshold(self) -> None:
        """One observation with min_observations=1 is ADEQUATE."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-1",
                forward_return=0.05,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
        assert result.total_occurrence_count == 1
        assert result.min_observations_for_evaluation == 1

    def test_exactly_threshold_observations(self) -> None:
        """Exactly min_observations occurrences is ADEQUATE."""
        result = _adequacy_from_batch(n=10, min_observations=10)
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
        assert result.total_occurrence_count == 10
        assert result.min_observations_for_evaluation == 10

    def test_threshold_minus_one(self) -> None:
        """min_observations - 1 occurrences is INSUFFICIENT."""
        result = _adequacy_from_batch(n=9, min_observations=10)
        assert result.adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA
        assert result.total_occurrence_count == 9
        assert result.min_observations_for_evaluation == 10

    def test_threshold_plus_one(self) -> None:
        """min_observations + 1 occurrences is ADEQUATE."""
        result = _adequacy_from_batch(n=11, min_observations=10)
        assert result.adequacy is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
        assert result.total_occurrence_count == 11
        assert result.min_observations_for_evaluation == 10

    def test_missing_forward_return_observations(self) -> None:
        """Occurrences with missing forward-return observations."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-1",
                fr_status=ObservationStatus.INSUFFICIENT_DATA,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 1
        assert result.forward_return_observation_count == 0

    def test_missing_excursion_observations(self) -> None:
        """Occurrences with missing excursion observations."""
        occs = (
            _occurrence(
                _candidate(evaluation_time=_EPOCH),
                occurrence_id="occ-1",
                pe_status=ObservationStatus.INSUFFICIENT_DATA,
            ),
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=1)
        quality = analyze_setup_quality(batch)
        behavior = assess_setup_behavior(evaluation, quality)
        interpretation = interpret_setup_behavior(behavior)
        report = generate_historical_setup_research_report(interpretation)
        result = assess_historical_setup_research_adequacy(report)
        assert result.total_occurrence_count == 1
        assert result.upward_excursion_observation_count == 0
        assert result.downward_excursion_observation_count == 0


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------


class TestTypeValidation:
    def test_wrong_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupResearchReport"):
            assess_historical_setup_research_adequacy("not a report")  # type: ignore[arg-type]

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupResearchReport"):
            assess_historical_setup_research_adequacy(None)  # type: ignore[arg-type]

    def test_interpretation_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="HistoricalSetupResearchReport"):
            assess_historical_setup_research_adequacy(
                _interpretation()  # type: ignore[arg-type]
            )
