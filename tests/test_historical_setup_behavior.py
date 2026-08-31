"""
Tests for Checkpoint 10.4 — Historical Setup Behavioral Assessment Boundary.

Deterministic, network-free: every test constructs candidates and
observations directly (no provider, no corpus engine, no pipeline).
The behavioral-assessment layer consumes a SetupEvaluationResult
(Checkpoint 10.3) and a SetupQualityReport (Checkpoint 9.11) and
produces a direction-neutral, deterministic, immutable behavioral
report describing how a setup has behaved historically.

Coverage:

A. Model — frozen, slots, validation, determinism, traceability
B. State handling — NO_DATA, INSUFFICIENT_DATA, EVALUABLE, empty,
   insufficient, sufficient
C. Forward-return behavior — mean, median, min, max, sign counts,
   proportion, consistency, sample count
D. Excursion behavior — upward/downward mean/median, paired integrity
E. Missing data — missing not converted to zero, real zero
   distinguishable, insufficient explicit
F. Safety — no candle/provider/store/corpus access, no mutation,
   no feedback
G. Semantics — no BUY/SELL, win/loss, profitability, target/stop,
   risk/reward, confidence score, black-box score, prediction
H. Determinism — repeated computation identical, ordering independent
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


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — shared helpers (mirror Checkpoint 10.3 test helpers)
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


# ---------------------------------------------------------------------------
# A. Model tests
# ---------------------------------------------------------------------------


class TestModel:
    def test_frozen(self) -> None:
        report = _build_minimal_report()
        with pytest.raises(AttributeError):
            report.evaluation_status = SetupEvaluationStatus.NO_DATA  # type: ignore[misc]

    def test_slots(self) -> None:
        report = _build_minimal_report()
        # Slots dataclass: no __dict__, and __slots__ defined.
        assert not hasattr(report, "__dict__")
        assert hasattr(type(report), "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            report.arbitrary_attribute = "x"  # type: ignore[attr-defined]

    def test_field_validation_negative_count(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            _build_minimal_report(total_occurrence_count=-1)

    def test_field_validation_count_sum(self) -> None:
        with pytest.raises(ValueError, match="must equal"):
            _build_minimal_report(
                total_occurrence_count=5,
                sufficient_data_count=2,
                insufficient_data_count=2,
            )

    def test_field_validation_no_data_requires_zero(self) -> None:
        with pytest.raises(ValueError, match="NO_DATA requires"):
            _build_minimal_report(
                evaluation_status=SetupEvaluationStatus.NO_DATA,
                total_occurrence_count=3,
                sufficient_data_count=3,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                positive_forward_return_observation_count=0,
                negative_forward_return_observation_count=0,
                zero_forward_return_observation_count=0,
            )

    def test_field_validation_evaluable_requires_positive(self) -> None:
        with pytest.raises(ValueError, match="EVALUABLE requires"):
            _build_minimal_report(
                evaluation_status=SetupEvaluationStatus.EVALUABLE,
                total_occurrence_count=0,
                sufficient_data_count=0,
                insufficient_data_count=0,
                forward_return_observation_count=0,
                upward_excursion_observation_count=0,
                downward_excursion_observation_count=0,
                positive_forward_return_observation_count=0,
                negative_forward_return_observation_count=0,
                zero_forward_return_observation_count=0,
            )

    def test_deterministic_construction(self) -> None:
        r1 = _build_minimal_report()
        r2 = _build_minimal_report()
        assert r1 == r2

    def test_traceability_to_batch(self) -> None:
        report = _build_minimal_report(batch_id="evidence-batch-abc123")
        assert report.batch_id == "evidence-batch-abc123"

    def test_traceability_to_evaluation(self) -> None:
        report = _build_minimal_report(evaluation_id="setup-eval-xyz")
        assert report.evaluation_id == "setup-eval-xyz"
        assert report.behavior_report_id.startswith("behavior-")


# ---------------------------------------------------------------------------
# B. State handling
# ---------------------------------------------------------------------------


class TestStateHandling:
    def test_no_data(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        assert report.evaluation_status is SetupEvaluationStatus.NO_DATA
        assert report.total_occurrence_count == 0
        assert report.forward_return_mean is None
        assert report.upward_excursion_mean is None
        assert report.is_empty is True

    def test_insufficient_data(self) -> None:
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
        assert report.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert report.total_occurrence_count == 3

    def test_evaluable(self) -> None:
        batch = _evaluable_batch(n=15)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        assert report.evaluation_status is SetupEvaluationStatus.EVALUABLE
        assert report.total_occurrence_count == 15

    def test_empty_evidence(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_observation_count == 0
        assert report.forward_return_mean is None
        assert report.proportion_positive_forward_return is None
        assert report.forward_return_direction_consistency is None

    def test_insufficient_observations_preserve_available(self) -> None:
        """INSUFFICIENT_DATA still preserves available descriptive stats."""
        occs = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
                forward_return=0.10,
            )
            for i in range(5)
        )
        batch = _batch(occs)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=10)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        assert report.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert report.forward_return_observation_count == 5
        assert report.forward_return_mean is not None
        assert report.forward_return_mean == pytest.approx(0.10)

    def test_sufficient_observations(self) -> None:
        batch = _evaluable_batch(n=20)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        assert report.evaluation_status is SetupEvaluationStatus.EVALUABLE
        assert report.forward_return_observation_count == 20
        assert report.has_forward_return_observations is True
        assert report.has_excursion_observations is True


# ---------------------------------------------------------------------------
# C. Forward-return behavior
# ---------------------------------------------------------------------------


class TestForwardReturnBehavior:
    def test_mean_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_mean == quality.forward_return_mean

    def test_median_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_median == quality.forward_return_median

    def test_minimum_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_minimum == quality.forward_return_minimum

    def test_maximum_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_maximum == quality.forward_return_maximum

    def test_positive_count_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.positive_forward_return_observation_count
            == quality.positive_forward_return_observation_count
        )

    def test_negative_count_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.negative_forward_return_observation_count
            == quality.negative_forward_return_observation_count
        )

    def test_zero_count_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.zero_forward_return_observation_count
            == quality.zero_forward_return_observation_count
        )

    def test_proportion_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.proportion_positive_forward_return
            == quality.proportion_positive_forward_return
        )

    def test_direction_consistency_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.forward_return_direction_consistency
            == quality.forward_return_direction_consistency
        )

    def test_sample_count_preserved(self) -> None:
        batch = _evaluable_batch(n=12)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_observation_count == 12


# ---------------------------------------------------------------------------
# D. Excursion behavior
# ---------------------------------------------------------------------------


class TestExcursionBehavior:
    def test_upward_mean_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.upward_excursion_mean == quality.upward_excursion_mean

    def test_upward_median_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.upward_excursion_median == quality.upward_excursion_median

    def test_downward_mean_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.downward_excursion_mean == quality.downward_excursion_mean

    def test_downward_median_preserved(self) -> None:
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert report.downward_excursion_median == quality.downward_excursion_median

    def test_paired_excursion_integrity(self) -> None:
        """Upward/downward counts are equal (paired at source)."""
        batch = _evaluable_batch(n=10)
        quality = analyze_setup_quality(batch)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        report = assess_setup_behavior(evaluation, quality)
        assert (
            report.upward_excursion_observation_count
            == report.downward_excursion_observation_count
        )


# ---------------------------------------------------------------------------
# E. Missing data
# ---------------------------------------------------------------------------


class TestMissingData:
    def test_missing_forward_return_not_zero(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        assert report.forward_return_mean is None
        assert report.forward_return_minimum is None
        assert report.forward_return_maximum is None

    def test_missing_excursion_not_zero(self) -> None:
        evaluation = evaluate_setup(_batch(()))
        quality = analyze_setup_quality(_batch(()))
        report = assess_setup_behavior(evaluation, quality)
        assert report.upward_excursion_mean is None
        assert report.downward_excursion_mean is None

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
        assert report.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA
        assert report.total_occurrence_count == 2
        # Both occurrences have sufficient data at the batch level.
        assert report.sufficient_data_count == 2
        assert report.insufficient_data_count == 0
        # But the evaluation is INSUFFICIENT_DATA because total < min threshold.
        assert report.min_observations_for_evaluation == 10

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
        assert report.zero_forward_return_observation_count == 1
        assert report.forward_return_observation_count == 1
        assert report.forward_return_mean == 0.0


# ---------------------------------------------------------------------------
# F. Safety
# ---------------------------------------------------------------------------


class TestSafety:
    def test_no_candle_access(self) -> None:
        """The behavioral layer never inspects candles."""
        batch = _evaluable_batch(n=5)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=3)
        quality = analyze_setup_quality(batch)
        report = assess_setup_behavior(evaluation, quality)
        assert "candle" not in type(report).__module__

    def test_no_provider_access(self) -> None:
        """The behavioral layer imports no provider."""
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        assert src is not None
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text
        assert "HistoricalMarketDataProvider" not in text

    def test_no_store_access(self) -> None:
        """The behavioral layer does not access any data store."""
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalDataStore" not in text

    def test_no_corpus_slicing(self) -> None:
        """The behavioral layer does not import the corpus engine."""
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "HistoricalResearchCorpusEngine" not in text
        assert "research_corpus" not in text

    def test_no_mutation_of_source_objects(self) -> None:
        batch = _evaluable_batch(n=5)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=3)
        quality = analyze_setup_quality(batch)
        eval_id_before = evaluation.evaluation_id
        quality_mean_before = quality.forward_return_mean
        assess_setup_behavior(evaluation, quality)
        assert evaluation.evaluation_id == eval_id_before
        assert quality.forward_return_mean == quality_mean_before

    def test_no_feedback_into_discovery(self) -> None:
        """The behavioral layer does not import discovery modules."""
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "historical_setup_discovery" not in text


# ---------------------------------------------------------------------------
# G. Semantics — no trading decision logic
# ---------------------------------------------------------------------------


class TestSemantics:
    def _assert_no_trading_terms(self) -> None:
        import engine.models.historical_setup_behavior as mod

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
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "win_rate" not in text
        assert "loss_rate" not in text

    def test_no_profitability(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "profit_factor" not in text

    def test_no_target_stop(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "take_profit" not in text
        assert "stop_loss" not in text

    def test_no_risk_reward(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "risk_reward" not in text

    def test_no_confidence_score(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "confidence" not in text
        assert "Confidence" not in text

    def test_no_black_box_quality_score(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "quality_score" not in text
        assert "black_box" not in text

    def test_no_prediction(self) -> None:
        import engine.models.historical_setup_behavior as mod

        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "predict" not in text.lower()


# ---------------------------------------------------------------------------
# H. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_computation_identical(self) -> None:
        batch = _evaluable_batch(n=10)
        evaluation = evaluate_setup(batch, min_observations_for_evaluation=5)
        quality = analyze_setup_quality(batch)
        r1 = assess_setup_behavior(evaluation, quality)
        r2 = assess_setup_behavior(evaluation, quality)
        assert r1 == r2

    def test_input_ordering_does_not_alter_report(self) -> None:
        """Shuffling occurrences produces the same behavioral report."""
        occs_a = tuple(
            _occurrence(
                _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                occurrence_id=f"occ-{i}",
                forward_return=0.05 + (i * 0.01),
            )
            for i in range(10)
        )
        occs_b = tuple(reversed(occs_a))
        batch_a = _batch(occs_a)
        batch_b = _batch(occs_b)
        evaluation_a = evaluate_setup(batch_a, min_observations_for_evaluation=5)
        evaluation_b = evaluate_setup(batch_b, min_observations_for_evaluation=5)
        quality_a = analyze_setup_quality(batch_a)
        quality_b = analyze_setup_quality(batch_b)
        report_a = assess_setup_behavior(evaluation_a, quality_a)
        report_b = assess_setup_behavior(evaluation_b, quality_b)
        assert report_a.forward_return_mean == report_b.forward_return_mean
        assert report_a.forward_return_observation_count == (
            report_b.forward_return_observation_count
        )


# ---------------------------------------------------------------------------
# Cross-cutting: batch_id mismatch
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_mismatched_batch_id_raises(self) -> None:
        batch_a = aggregate_evidence(
            tuple(
                _occurrence(
                    _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                    occurrence_id=f"a-occ-{i}",
                )
                for i in range(5)
            ),
            criterion_key="NIFTY|15m|1D",
        )
        batch_b = aggregate_evidence(
            tuple(
                _occurrence(
                    _candidate(evaluation_time=_EPOCH + timedelta(hours=i)),
                    occurrence_id=f"b-occ-{i}",
                )
                for i in range(5)
            ),
            criterion_key="RELIANCE|15m|1D",
        )
        evaluation = evaluate_setup(batch_a, min_observations_for_evaluation=3)
        quality = analyze_setup_quality(batch_b)
        with pytest.raises(ValueError, match="batch_id"):
            assess_setup_behavior(evaluation, quality)


# ---------------------------------------------------------------------------
# Helper: build a minimal valid HistoricalSetupBehaviorReport for model tests
# ---------------------------------------------------------------------------


def _build_minimal_report(
    *,
    batch_id: str = "evidence-batch-abc123",
    evaluation_id: str = "setup-eval-xyz",
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
    return HistoricalSetupBehaviorReport(
        behavior_report_id="behavior-" + evaluation_id[-16:],
        evaluation_id=evaluation_id,
        batch_id=batch_id,
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
        forward_return_direction_consistency=(
            forward_return_direction_consistency
        ),
        upward_excursion_mean=upward_excursion_mean,
        upward_excursion_median=upward_excursion_median,
        downward_excursion_mean=downward_excursion_mean,
        downward_excursion_median=downward_excursion_median,
    )
