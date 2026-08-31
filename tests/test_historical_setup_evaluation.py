"""
Tests for Checkpoint 10.3 — Setup Evaluation Boundary.

Deterministic, network-free: every test constructs candidates and
observations directly (no provider, no corpus engine, no pipeline).
The setup-evaluation layer consumes a SetupEvidenceBatch (Checkpoint 9.9)
and produces a deterministic, immutable evaluation result that answers:
"Does the available historical evidence provide enough information to
evaluate this setup?" without answering "Should I BUY or SELL this setup?"

Coverage:

A. Existing abstraction reuse/rejection
B. Evaluation output model invariants
C. Empty evidence (NO_DATA)
D. Insufficient future data (INSUFFICIENT_DATA)
E. Sufficient evidence (EVALUABLE)
F. Determinism
G. Immutability
H. Traceability to source evidence
I. No future candle access
J. No forbidden trading semantics
K. No black-box score
L. No mutation of source objects
M. Compatibility with Checkpoint 9 outputs
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
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
    candidate: HistoricalSetupCandidate,
    *,
    forward_return_status: ObservationStatus = ObservationStatus.AVAILABLE,
    price_excursion_status: ObservationStatus = ObservationStatus.AVAILABLE,
    occurrence_id: str = "occ-1",
) -> SetupEvidenceOccurrence:
    fr = _forward_return(candidate, status=forward_return_status)
    pe = _price_excursion(candidate, status=price_excursion_status)
    return SetupEvidenceOccurrence(
        occurrence_id=occurrence_id,
        candidate=candidate,
        forward_return=fr if forward_return_status is ObservationStatus.AVAILABLE else None,
        price_excursion=pe if price_excursion_status is ObservationStatus.AVAILABLE else None,
    )


def _batch(
    occurrences: tuple[SetupEvidenceOccurrence, ...] = (),
) -> SetupEvidenceBatch:
    return aggregate_evidence(occurrences, criterion_key="NIFTY|15m|1D")


# ---------------------------------------------------------------------------
# A. Existing abstraction reuse/rejection
# ---------------------------------------------------------------------------
class TestExistingAbstractionRejection:
    """Verify that no incompatible existing abstraction is reused."""

    def test_evaluation_status_is_distinct_from_evidence_strength(self) -> None:
        """SetupEvaluationStatus is distinct from Sprint 11Y EvidenceStrength."""
        from engine.models.historical_evidence import EvidenceStrength

        evaluation_members = {e.name for e in SetupEvaluationStatus}
        evidence_members = {e.name for e in EvidenceStrength}
        assert not evaluation_members.intersection(evidence_members)

    def test_evaluation_status_is_distinct_from_strategy_assessment(self) -> None:
        """SetupEvaluationStatus is distinct from Sprint 11Z StrategyAssessmentStatus."""
        from engine.models.strategy_intelligence import StrategyAssessmentStatus

        evaluation_members = {e.name for e in SetupEvaluationStatus}
        strategy_members = {e.name for e in StrategyAssessmentStatus}
        assert not evaluation_members.intersection(strategy_members)

    def test_evaluation_status_is_distinct_from_decision_context(self) -> None:
        """SetupEvaluationStatus is distinct from Sprint 12A DecisionContextStatus."""
        from engine.models.decision_intelligence import DecisionContextStatus

        evaluation_members = {e.name for e in SetupEvaluationStatus}
        decision_members = {e.name for e in DecisionContextStatus}
        assert not evaluation_members.intersection(decision_members)

    def test_evaluation_status_is_distinct_from_setup_classification(self) -> None:
        """SetupEvaluationStatus is distinct from Sprint 11Q SetupClassification."""
        from engine.models.setup_confluence import SetupClassification

        evaluation_members = {e.name for e in SetupEvaluationStatus}
        setup_members = {e.name for e in SetupClassification}
        assert not evaluation_members.intersection(setup_members)


# ---------------------------------------------------------------------------
# B. Evaluation output model invariants
# ---------------------------------------------------------------------------
class TestEvaluationOutputInvariants:
    """Verify that the evaluation result model enforces structural invariants."""

    def test_frozen_and_slots(self) -> None:
        """SetupEvaluationResult is frozen and uses slots."""
        result = evaluate_setup(_batch())
        with pytest.raises(AttributeError):
            result.evaluation_status = SetupEvaluationStatus.EVALUABLE  # type: ignore[misc]

    def test_count_invariants_sufficient_plus_insufficient_equals_total(self) -> None:
        """sufficient_data_count + insufficient_data_count == total_occurrence_count."""
        candidate = _candidate()
        occ = _occurrence(candidate)
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=1)
        assert result.sufficient_data_count + result.insufficient_data_count == result.total_occurrence_count

    def test_no_data_requires_zero_occurrences(self) -> None:
        """NO_DATA status requires total_occurrence_count == 0."""
        with pytest.raises(ValueError, match="NO_DATA requires total_occurrence_count == 0"):
            SetupEvaluationResult(
                evaluation_id="setup-eval-test",
                batch_id="evidence-batch-test",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.NO_DATA,
                total_occurrence_count=5,
                sufficient_data_count=0,
                insufficient_data_count=5,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                min_observations_for_evaluation=10,
                reason="test",
            )

    def test_evaluable_requires_all_metrics_above_threshold(self) -> None:
        """EVALUABLE status requires all metrics >= min_observations."""
        with pytest.raises(ValueError, match="EVALUABLE requires all metrics"):
            SetupEvaluationResult(
                evaluation_id="setup-eval-test",
                batch_id="evidence-batch-test",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.EVALUABLE,
                total_occurrence_count=10,
                sufficient_data_count=5,
                insufficient_data_count=5,
                forward_return_observation_count=3,
                upward_excursion_observation_count=10,
                downward_excursion_observation_count=10,
                min_observations_for_evaluation=5,
                reason="test",
            )

    def test_insufficient_data_requires_at_least_one_metric_below(self) -> None:
        """INSUFFICIENT_DATA status requires at least one metric below threshold."""
        with pytest.raises(ValueError, match="INSUFFICIENT_DATA requires at least one metric"):
            SetupEvaluationResult(
                evaluation_id="setup-eval-test",
                batch_id="evidence-batch-test",
                criterion_key="NIFTY|15m|1D",
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_status=SetupEvaluationStatus.INSUFFICIENT_DATA,
                total_occurrence_count=20,
                sufficient_data_count=20,
                insufficient_data_count=0,
                forward_return_observation_count=20,
                upward_excursion_observation_count=20,
                downward_excursion_observation_count=20,
                min_observations_for_evaluation=10,
                reason="test",
            )


# ---------------------------------------------------------------------------
# C. Empty evidence (NO_DATA)
# ---------------------------------------------------------------------------
class TestEmptyEvidence:
    """Verify that empty evidence produces NO_DATA status."""

    def test_empty_batch_returns_no_data(self) -> None:
        """An empty batch (no occurrences) produces NO_DATA."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.evaluation_status is SetupEvaluationStatus.NO_DATA
        assert result.total_occurrence_count == 0

    def test_no_data_has_zero_counts(self) -> None:
        """NO_DATA result has all counts at zero."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.sufficient_data_count == 0
        assert result.insufficient_data_count == 0
        assert result.forward_return_observation_count == 0
        assert result.upward_excursion_observation_count == 0
        assert result.downward_excursion_observation_count == 0

    def test_no_data_reason_mentions_no_occurrences(self) -> None:
        """NO_DATA reason explicitly mentions no occurrences."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert "No historical occurrences" in result.reason


# ---------------------------------------------------------------------------
# D. Insufficient future data (INSUFFICIENT_DATA)
# ---------------------------------------------------------------------------
class TestInsufficientData:
    """Verify that insufficient future data produces INSUFFICIENT_DATA status."""

    def test_few_observations_returns_insufficient(self) -> None:
        """Fewer observations than minimum produces INSUFFICIENT_DATA."""
        candidate = _candidate()
        occ = _occurrence(candidate)
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA

    def test_insufficient_when_forward_return_below_threshold(self) -> None:
        """INSUFFICIENT_DATA when forward return observations are below threshold."""
        candidate = _candidate()
        # Only forward return available, no price excursion
        occ = _occurrence(
            candidate,
            forward_return_status=ObservationStatus.AVAILABLE,
            price_excursion_status=ObservationStatus.INSUFFICIENT_DATA,
        )
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=5)
        assert result.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert result.forward_return_observation_count == 1
        assert result.upward_excursion_observation_count == 0

    def test_insufficient_when_excursion_below_threshold(self) -> None:
        """INSUFFICIENT_DATA when price excursion observations are below threshold."""
        candidate = _candidate()
        # Only price excursion available, no forward return
        occ = _occurrence(
            candidate,
            forward_return_status=ObservationStatus.INSUFFICIENT_DATA,
            price_excursion_status=ObservationStatus.AVAILABLE,
        )
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=5)
        assert result.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert result.forward_return_observation_count == 0
        assert result.upward_excursion_observation_count == 1

    def test_insufficient_reason_identifies_failing_metrics(self) -> None:
        """INSUFFICIENT_DATA reason identifies which metrics are below threshold."""
        candidate = _candidate()
        occ = _occurrence(
            candidate,
            forward_return_status=ObservationStatus.AVAILABLE,
            price_excursion_status=ObservationStatus.INSUFFICIENT_DATA,
        )
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=5)
        assert "forward return observations" in result.reason
        assert "below minimum" in result.reason

    def test_insufficient_data_does_not_mutate_source(self) -> None:
        """INSUFFICIENT_DATA evaluation does not mutate the source batch."""
        candidate = _candidate()
        occ = _occurrence(candidate)
        batch = _batch((occ,))
        original_total = batch.total_occurrences
        evaluate_setup(batch, min_observations_for_evaluation=10)
        assert batch.total_occurrences == original_total


# ---------------------------------------------------------------------------
# E. Sufficient evidence (EVALUABLE)
# ---------------------------------------------------------------------------
class TestSufficientEvidence:
    """Verify that sufficient evidence produces EVALUABLE status."""

    def test_enough_observations_returns_evaluable(self) -> None:
        """Enough observations for all metrics produces EVALUABLE."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(15)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result.evaluation_status is SetupEvaluationStatus.EVALUABLE

    def test_evaluable_counts_all_metrics(self) -> None:
        """EVALUABLE result has all metric counts >= minimum."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(15)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result.forward_return_observation_count >= 10
        assert result.upward_excursion_observation_count >= 10
        assert result.downward_excursion_observation_count >= 10

    def test_evaluable_reason_mentions_threshold_met(self) -> None:
        """EVALUABLE reason mentions that the threshold is met."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(15)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert "minimum observation threshold" in result.reason

    def test_evaluable_at_exact_threshold(self) -> None:
        """EVALUABLE when observations exactly equal the threshold."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(10)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result.evaluation_status is SetupEvaluationStatus.EVALUABLE


# ---------------------------------------------------------------------------
# F. Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    """Verify that the evaluation is deterministic."""

    def test_same_input_produces_same_output(self) -> None:
        """Identical input produces identical output."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(5)
        )
        batch = _batch(occurrences)
        result1 = evaluate_setup(batch, min_observations_for_evaluation=10)
        result2 = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result1 == result2

    def test_evaluation_id_is_deterministic(self) -> None:
        """The evaluation_id is deterministic for identical input."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(5)
        )
        batch = _batch(occurrences)
        result1 = evaluate_setup(batch, min_observations_for_evaluation=10)
        result2 = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result1.evaluation_id == result2.evaluation_id

    def test_different_threshold_produces_different_id(self) -> None:
        """Different min_observations produces different evaluation_id."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(15)
        )
        batch = _batch(occurrences)
        result1 = evaluate_setup(batch, min_observations_for_evaluation=5)
        result2 = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result1.evaluation_id != result2.evaluation_id

    def test_evaluation_id_starts_with_prefix(self) -> None:
        """The evaluation_id starts with 'setup-eval-'."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.evaluation_id.startswith("setup-eval-")


# ---------------------------------------------------------------------------
# G. Immutability
# ---------------------------------------------------------------------------
class TestImmutability:
    """Verify that the evaluation result is immutable."""

    def test_result_is_frozen(self) -> None:
        """SetupEvaluationResult is frozen."""
        result = evaluate_setup(_batch())
        with pytest.raises(AttributeError):
            result.reason = "new reason"  # type: ignore[misc]

    def test_result_has_slots(self) -> None:
        """SetupEvaluationResult uses slots (no __dict__)."""
        result = evaluate_setup(_batch())
        assert not hasattr(result, "__dict__")

    def test_status_enum_is_frozen(self) -> None:
        """SetupEvaluationStatus enum members cannot be modified."""
        with pytest.raises(AttributeError):
            SetupEvaluationStatus.NO_DATA = "CHANGED"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# H. Traceability to source evidence
# ---------------------------------------------------------------------------
class TestTraceability:
    """Verify that the evaluation result is traceable to its source evidence."""

    def test_result_retains_batch_id(self) -> None:
        """The result retains the source batch_id."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.batch_id == batch.batch_id

    def test_result_retains_criterion_key(self) -> None:
        """The result retains the criterion_key."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.criterion_key == batch.criterion_key

    def test_result_retains_instrument(self) -> None:
        """The result retains the instrument."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.instrument == batch.instrument

    def test_result_retains_timeframes(self) -> None:
        """The result retains setup and context timeframes."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert result.setup_timeframe == batch.setup_timeframe
        assert result.context_timeframe == batch.context_timeframe


# ---------------------------------------------------------------------------
# I. No future candle access
# ---------------------------------------------------------------------------
class TestNoFutureCandleAccess:
    """Verify that the evaluation does not access future candles."""

    def test_evaluation_consumes_only_batch(self) -> None:
        """evaluate_setup accepts only a SetupEvidenceBatch."""
        import inspect

        sig = inspect.signature(evaluate_setup)
        params = list(sig.parameters.keys())
        assert "batch" in params
        # No candle-related parameters
        assert "candles" not in params
        assert "future_candles" not in params

    def test_evaluation_does_not_create_candles(self) -> None:
        """evaluate_setup does not create or access OHLCVCandle objects."""
        import engine.models.historical_setup_evaluation as mod

        # Verify the module does not import OHLCVCandle
        assert not hasattr(mod, "OHLCVCandle")

    def test_evaluation_uses_only_already_computed_observations(self) -> None:
        """Evaluation reads only observation_status, not raw candles."""
        candidate = _candidate()
        occ = _occurrence(candidate)
        batch = _batch((occ,))
        result = evaluate_setup(batch, min_observations_for_evaluation=1)
        # The result reflects the observation status, not raw candle data
        assert result.forward_return_observation_count == 1
        assert result.upward_excursion_observation_count == 1


# ---------------------------------------------------------------------------
# J. No forbidden trading semantics
# ---------------------------------------------------------------------------
class TestNoForbiddenSemantics:
    """Verify that the evaluation result contains no forbidden trading semantics."""

    _FORBIDDEN_TERMS = [
        "BUY", "SELL", "LONG", "SHORT", "WIN", "LOSS", "PROFITABLE",
        "STOP LOSS", "TARGET", "RISK/REWARD", "POSITION SIZE", "TRADE SCORE",
        "CONFIDENCE SCORE", "EXPECTANCY", "SHARPE", "PREDICTION",
    ]

    def test_evaluation_status_has_no_forbidden_terms(self) -> None:
        """SetupEvaluationStatus members contain no forbidden terms."""
        for member in SetupEvaluationStatus:
            for term in self._FORBIDDEN_TERMS:
                assert term not in member.name, f"Forbidden term '{term}' in {member.name}"

    def test_evaluation_result_has_no_score_fields(self) -> None:
        """SetupEvaluationResult has no score-related fields."""
        result = evaluate_setup(_batch())
        for field in result.__dataclass_fields__:
            assert "score" not in field.lower(), f"Score field found: {field}"
            assert "confidence" not in field.lower(), f"Confidence field found: {field}"

    def test_evaluation_result_has_no_trading_fields(self) -> None:
        """SetupEvaluationResult has no trading-related fields."""
        result = evaluate_setup(_batch())
        for field in result.__dataclass_fields__:
            assert "buy" not in field.lower(), f"Buy field found: {field}"
            assert "sell" not in field.lower(), f"Sell field found: {field}"
            assert "profit" not in field.lower(), f"Profit field found: {field}"

    def test_no_black_box_quality_score(self) -> None:
        """The evaluation does not produce a composite quality score."""
        result = evaluate_setup(_batch())
        for field in result.__dataclass_fields__:
            assert "quality" not in field.lower(), f"Quality field found: {field}"


# ---------------------------------------------------------------------------
# K. No black-box score
# ---------------------------------------------------------------------------
class TestNoBlackBoxScore:
    """Verify that the evaluation does not produce a black-box score."""

    def test_status_is_descriptive_not_numeric(self) -> None:
        """The evaluation status is a descriptive enum, not a numeric score."""
        result = evaluate_setup(_batch())
        assert isinstance(result.evaluation_status, SetupEvaluationStatus)

    def test_no_single_composite_metric(self) -> None:
        """There is no single composite metric field."""
        result = evaluate_setup(_batch())
        for field in result.__dataclass_fields__:
            assert "composite" not in field.lower(), f"Composite field found: {field}"


# ---------------------------------------------------------------------------
# L. No mutation of source objects
# ---------------------------------------------------------------------------
class TestNoMutation:
    """Verify that evaluation does not mutate source objects."""

    def test_batch_not_mutated(self) -> None:
        """evaluate_setup does not mutate the source batch."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(5)
        )
        batch = _batch(occurrences)
        original_occurrences = batch.occurrences
        original_total = batch.total_occurrences
        evaluate_setup(batch, min_observations_for_evaluation=10)
        assert batch.occurrences is original_occurrences
        assert batch.total_occurrences == original_total

    def test_occurrences_not_mutated(self) -> None:
        """evaluate_setup does not mutate individual occurrences."""
        candidate = _candidate()
        occ = _occurrence(candidate)
        batch = _batch((occ,))
        original_fr_status = occ.forward_return.observation_status
        original_pe_status = occ.price_excursion.observation_status
        evaluate_setup(batch, min_observations_for_evaluation=10)
        assert occ.forward_return.observation_status == original_fr_status
        assert occ.price_excursion.observation_status == original_pe_status


# ---------------------------------------------------------------------------
# M. Compatibility with Checkpoint 9 outputs
# ---------------------------------------------------------------------------
class TestCheckpoint9Compatibility:
    """Verify that the evaluation is compatible with Checkpoint 9 outputs."""

    def test_consumes_setup_evidence_batch(self) -> None:
        """evaluate_setup consumes a SetupEvidenceBatch (Checkpoint 9.9 output)."""
        batch = _batch()
        result = evaluate_setup(batch)
        assert isinstance(batch, SetupEvidenceBatch)

    def test_works_with_aggregated_evidence(self) -> None:
        """evaluate_setup works with evidence aggregated via aggregate_evidence."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(20)
        )
        batch = aggregate_evidence(occurrences, criterion_key="NIFTY|15m|1D")
        result = evaluate_setup(batch, min_observations_for_evaluation=10)
        assert result.evaluation_status is SetupEvaluationStatus.EVALUABLE

    def test_preserves_occurrence_level_counts(self) -> None:
        """The evaluation preserves occurrence-level counts from the batch."""
        occurrences = tuple(
            _occurrence(_candidate(_EPOCH + timedelta(days=i)), occurrence_id=f"occ-{i}")
            for i in range(10)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=5)
        assert result.total_occurrence_count == batch.total_occurrences
        assert result.sufficient_data_count == batch.sufficient_data_count
        assert result.insufficient_data_count == batch.insufficient_data_count

    def test_mixed_sufficient_and_insufficient_occurrences(self) -> None:
        """Evaluation handles a mix of sufficient and insufficient occurrences."""
        candidates = [_candidate(_EPOCH + timedelta(days=i)) for i in range(10)]
        occurrences = tuple(
            _occurrence(
                cand,
                forward_return_status=ObservationStatus.AVAILABLE,
                price_excursion_status=ObservationStatus.AVAILABLE if i < 7 else ObservationStatus.INSUFFICIENT_DATA,
                occurrence_id=f"occ-{i}",
            )
            for i, cand in enumerate(candidates)
        )
        batch = _batch(occurrences)
        result = evaluate_setup(batch, min_observations_for_evaluation=5)
        # 7 have both, 3 have only forward return
        assert result.total_occurrence_count == 10
        assert result.forward_return_observation_count == 10
        assert result.upward_excursion_observation_count == 7
