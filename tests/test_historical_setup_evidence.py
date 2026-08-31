"""
Tests for Checkpoint 9.9 — Historical Evidence Aggregation Boundary.

Deterministic, network-free: every test constructs candidates and
observations directly (no provider, no corpus engine, no pipeline).
The evidence aggregation boundary is RESEARCH ONLY — it collects
individual historical occurrences under a shared setup criterion;
it does NOT compute averages, medians, win rates, loss rates,
expectancy, Sharpe ratios, statistical significance, predictive
power, or quality scores.

Coverage:

A. Occurrence model (frozen/slots, delegation, invariants)
B. One occurrence can be aggregated
C. Multiple occurrences remain individually traceable
D. Forward-return observations remain associated with the correct occurrence
E. Paired excursion values remain together
F. Insufficient-data observations remain explicitly represented
G. Duplicate handling is deterministic
H. Aggregation does not mutate source observations
I. Empty aggregation is deterministic
J. No statistical judgement is performed
K. No forbidden trading semantics are introduced
L. Batch model invariants
M. Batch properties (counts, is_empty)
N. Determinism (repeated aggregation produces identical batch)
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


# ============================================================
# A. Occurrence model (frozen/slots, delegation, invariants)
# ============================================================


class TestOccurrenceModel:
    def test_frozen(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        with pytest.raises(AttributeError):
            occ.occurrence_id = "other"  # type: ignore[misc]

    def test_delegation_evaluation_time(self) -> None:
        ts = _EPOCH + timedelta(days=5)
        cand = _candidate(evaluation_time=ts)
        occ = _occurrence("occ-1", cand)
        assert occ.evaluation_time == ts

    def test_delegation_instrument(self) -> None:
        occ = _occurrence("occ-1", _candidate(instrument="RELIANCE"))
        assert occ.instrument == "RELIANCE"

    def test_delegation_timeframes(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        assert occ.setup_timeframe == "15m"
        assert occ.context_timeframe == "1D"

    def test_forward_return_candidate_mismatch_rejected(self) -> None:
        cand1 = _candidate()
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        fr = _forward_return(cand1)
        pe = _price_excursion(cand2)
        with pytest.raises(ValueError, match="candidate does not match"):
            SetupEvidenceOccurrence(
                occurrence_id="occ-bad",
                candidate=cand2,
                forward_return=fr,
                price_excursion=pe,
            )

    def test_price_excursion_candidate_mismatch_rejected(self) -> None:
        cand1 = _candidate()
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        fr = _forward_return(cand2)
        pe = _price_excursion(cand1)
        with pytest.raises(ValueError, match="candidate does not match"):
            SetupEvidenceOccurrence(
                occurrence_id="occ-bad",
                candidate=cand2,
                forward_return=fr,
                price_excursion=pe,
            )


# ============================================================
# B. One occurrence can be aggregated
# ============================================================


class TestSingleOccurrenceAggregation:
    def test_single_occurrence_batch(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch = aggregate_evidence((occ,))
        assert batch.total_occurrences == 1
        assert batch.occurrences[0] is occ

    def test_single_occurrence_batch_id_deterministic(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch1 = aggregate_evidence((occ,))
        batch2 = aggregate_evidence((occ,))
        assert batch1.batch_id == batch2.batch_id

    def test_single_occurrence_criterion_key_derived(self) -> None:
        occ = _occurrence("occ-1", _candidate(instrument="TCS"))
        batch = aggregate_evidence((occ,))
        assert batch.criterion_key == "TCS|15m|1D"

    def test_single_occurrence_explicit_criterion_key(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch = aggregate_evidence((occ,), criterion_key="CUSTOM_CRITERION")
        assert batch.criterion_key == "CUSTOM_CRITERION"


# ============================================================
# C. Multiple occurrences remain individually traceable
# ============================================================


class TestMultipleOccurrencesTraceable:
    def test_two_occurrences_preserved(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = _occurrence("occ-2", cand2)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.total_occurrences == 2
        assert batch.occurrences[0] is occ1
        assert batch.occurrences[1] is occ2

    def test_three_occurrences_chronological_order(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH + timedelta(days=2))
        cand2 = _candidate(evaluation_time=_EPOCH)
        cand3 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-a", cand1)
        occ2 = _occurrence("occ-b", cand2)
        occ3 = _occurrence("occ-c", cand3)
        batch = aggregate_evidence((occ1, occ2, occ3))
        assert batch.total_occurrences == 3
        assert batch.occurrences[0] is occ2
        assert batch.occurrences[1] is occ3
        assert batch.occurrences[2] is occ1

    def test_each_occurrence_retains_candidate_identity(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH, instrument="NIFTY")
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1), instrument="RELIANCE")
        occ1 = _occurrence("occ-1", cand1)
        occ2 = _occurrence("occ-2", cand2)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.occurrences[0].candidate.instrument == "NIFTY"
        assert batch.occurrences[1].candidate.instrument == "RELIANCE"
        assert batch.occurrences[0].evaluation_time == _EPOCH
        assert batch.occurrences[1].evaluation_time == _EPOCH + timedelta(days=1)


# ============================================================
# D. Forward-return observations remain associated with the correct occurrence
# ============================================================


class TestForwardReturnAssociation:
    def test_forward_return_matches_occurrence(self) -> None:
        cand = _candidate()
        fr = _forward_return(cand, forward_return=0.12)
        occ = _occurrence("occ-1", cand, forward_return=fr)
        batch = aggregate_evidence((occ,))
        assert batch.occurrences[0].forward_return is fr
        assert batch.occurrences[0].forward_return.forward_return == 0.12

    def test_different_forward_returns_per_occurrence(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        fr1 = _forward_return(cand1, forward_return=0.05)
        fr2 = _forward_return(cand2, forward_return=-0.02)
        occ1 = _occurrence("occ-1", cand1, forward_return=fr1)
        occ2 = _occurrence("occ-2", cand2, forward_return=fr2)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.occurrences[0].forward_return.forward_return == 0.05
        assert batch.occurrences[1].forward_return.forward_return == -0.02

    def test_forward_return_none_for_insufficient_data(self) -> None:
        cand = _candidate()
        occ = SetupEvidenceOccurrence(
            occurrence_id="occ-ins",
            candidate=cand,
            forward_return=None,
            price_excursion=None,
        )
        batch = aggregate_evidence((occ,))
        assert batch.occurrences[0].forward_return is None
        assert batch.occurrences[0].is_insufficient_data is True


# ============================================================
# E. Paired excursion values remain together
# ============================================================


class TestPairedExcursionValues:
    def test_excursion_values_paired_in_occurrence(self) -> None:
        cand = _candidate()
        pe = _price_excursion(cand, max_up=0.10, max_down=-0.04)
        occ = _occurrence("occ-1", cand, price_excursion=pe)
        batch = aggregate_evidence((occ,))
        retrieved = batch.occurrences[0].price_excursion
        assert retrieved is not None
        assert retrieved.max_upward_excursion == 0.10
        assert retrieved.max_downward_excursion == -0.04
        assert retrieved.max_high == pytest.approx(110.0)
        assert retrieved.min_low == pytest.approx(96.0)

    def test_excursions_not_split_across_occurrences(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        pe1 = _price_excursion(cand1, max_up=0.07, max_down=-0.02)
        pe2 = _price_excursion(cand2, max_up=0.15, max_down=-0.06)
        occ1 = _occurrence("occ-1", cand1, price_excursion=pe1)
        occ2 = _occurrence("occ-2", cand2, price_excursion=pe2)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.occurrences[0].price_excursion.max_upward_excursion == 0.07
        assert batch.occurrences[0].price_excursion.max_downward_excursion == -0.02
        assert batch.occurrences[1].price_excursion.max_upward_excursion == 0.15
        assert batch.occurrences[1].price_excursion.max_downward_excursion == -0.06

    def test_forward_return_and_excursion_same_occurrence(self) -> None:
        cand = _candidate()
        fr = _forward_return(cand, forward_return=0.06)
        pe = _price_excursion(cand, max_up=0.09, max_down=-0.03)
        occ = _occurrence("occ-1", cand, forward_return=fr, price_excursion=pe)
        batch = aggregate_evidence((occ,))
        retrieved = batch.occurrences[0]
        assert retrieved.forward_return.forward_return == 0.06
        assert retrieved.price_excursion.max_upward_excursion == 0.09
        assert retrieved.price_excursion.max_downward_excursion == -0.03


# ============================================================
# F. Insufficient-data observations remain explicitly represented
# ============================================================


class TestInsufficientDataRepresentation:
    def test_insufficient_data_occurrence_in_batch(self) -> None:
        cand = _candidate()
        occ = SetupEvidenceOccurrence(
            occurrence_id="occ-ins",
            candidate=cand,
            forward_return=None,
            price_excursion=None,
        )
        batch = aggregate_evidence((occ,))
        assert batch.insufficient_data_count == 1
        assert batch.sufficient_data_count == 0
        assert batch.occurrences[0].is_insufficient_data is True

    def test_mixed_sufficient_and_insufficient(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = SetupEvidenceOccurrence(
            occurrence_id="occ-2",
            candidate=cand2,
            forward_return=None,
            price_excursion=None,
        )
        batch = aggregate_evidence((occ1, occ2))
        assert batch.total_occurrences == 2
        assert batch.sufficient_data_count == 1
        assert batch.insufficient_data_count == 1
        assert batch.occurrences[0].has_sufficient_data is True
        assert batch.occurrences[1].is_insufficient_data is True

    def test_insufficient_forward_return_only(self) -> None:
        cand = _candidate()
        fr_ins = _forward_return(cand, status=ObservationStatus.INSUFFICIENT_DATA)
        pe = _price_excursion(cand)
        occ = SetupEvidenceOccurrence(
            occurrence_id="occ-partial",
            candidate=cand,
            forward_return=fr_ins,
            price_excursion=pe,
        )
        batch = aggregate_evidence((occ,))
        assert batch.insufficient_data_count == 1
        assert batch.occurrences[0].is_insufficient_data is True

    def test_insufficient_price_excursion_only(self) -> None:
        cand = _candidate()
        fr = _forward_return(cand)
        pe_ins = _price_excursion(cand, status=ObservationStatus.INSUFFICIENT_DATA)
        occ = SetupEvidenceOccurrence(
            occurrence_id="occ-partial",
            candidate=cand,
            forward_return=fr,
            price_excursion=pe_ins,
        )
        batch = aggregate_evidence((occ,))
        assert batch.insufficient_data_count == 1
        assert batch.occurrences[0].is_insufficient_data is True


# ============================================================
# G. Duplicate handling is deterministic
# ============================================================


class TestDuplicateHandling:
    def test_duplicate_ids_deduplicated(self) -> None:
        cand = _candidate()
        occ1 = _occurrence("occ-dup", cand)
        occ2 = _occurrence("occ-dup", cand)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.total_occurrences == 1

    def test_first_occurrence_wins(self) -> None:
        cand = _candidate()
        fr1 = _forward_return(cand, forward_return=0.05)
        fr2 = _forward_return(cand, forward_return=0.99)
        occ1 = _occurrence("occ-dup", cand, forward_return=fr1)
        occ2 = _occurrence("occ-dup", cand, forward_return=fr2)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.occurrences[0].forward_return.forward_return == 0.05

    def test_duplicate_with_different_ids_kept(self) -> None:
        cand = _candidate()
        occ1 = _occurrence("occ-1", cand)
        occ2 = _occurrence("occ-2", cand)
        batch = aggregate_evidence((occ1, occ2))
        assert batch.total_occurrences == 2

    def test_duplicate_handling_deterministic(self) -> None:
        cand = _candidate()
        occ1 = _occurrence("occ-dup", cand)
        occ2 = _occurrence("occ-dup", cand)
        batch1 = aggregate_evidence((occ1, occ2))
        batch2 = aggregate_evidence((occ2, occ1))
        assert batch1.batch_id == batch2.batch_id
        assert batch1.total_occurrences == batch2.total_occurrences


# ============================================================
# H. Aggregation does not mutate source observations
# ============================================================


class TestAggregationNoMutation:
    def test_source_observations_unchanged(self) -> None:
        cand = _candidate()
        occ = _occurrence("occ-1", cand)
        original_id = occ.occurrence_id
        original_candidate = occ.candidate
        aggregate_evidence((occ,))
        assert occ.occurrence_id == original_id
        assert occ.candidate is original_candidate

    def test_batch_occurrences_are_source_objects(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch = aggregate_evidence((occ,))
        assert batch.occurrences[0] is occ

    def test_repeated_aggregation_same_result(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch1 = aggregate_evidence((occ,))
        batch2 = aggregate_evidence((occ,))
        assert batch1.batch_id == batch2.batch_id
        assert batch1.occurrences == batch2.occurrences


# ============================================================
# I. Empty aggregation is deterministic
# ============================================================


class TestEmptyAggregation:
    def test_empty_batch(self) -> None:
        batch = aggregate_evidence(())
        assert batch.is_empty is True
        assert batch.total_occurrences == 0
        assert batch.sufficient_data_count == 0
        assert batch.insufficient_data_count == 0

    def test_empty_batch_deterministic(self) -> None:
        batch1 = aggregate_evidence(())
        batch2 = aggregate_evidence(())
        assert batch1.batch_id == batch2.batch_id

    def test_empty_batch_default_fields(self) -> None:
        batch = aggregate_evidence(())
        assert batch.instrument == ""
        assert batch.setup_timeframe == ""
        assert batch.context_timeframe == ""
        assert batch.occurrences == ()


# ============================================================
# J. No statistical judgement is performed
# ============================================================


class TestNoStatisticalJudgement:
    def test_no_average_return_attribute(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch = aggregate_evidence((occ,))
        assert not hasattr(batch, "average_return")
        assert not hasattr(batch, "median_return")
        assert not hasattr(batch, "win_rate")
        assert not hasattr(batch, "loss_rate")
        assert not hasattr(batch, "expectancy")
        assert not hasattr(batch, "sharpe")
        assert not hasattr(batch, "significance")
        assert not hasattr(batch, "predictive_power")
        assert not hasattr(batch, "quality_score")

    def test_no_statistical_attributes_on_occurrence(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        assert not hasattr(occ, "average_return")
        assert not hasattr(occ, "win_rate")
        assert not hasattr(occ, "expectancy")
        assert not hasattr(occ, "quality_score")

    def test_no_statistical_methods_on_batch(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        assert not callable(getattr(batch, "compute_statistics", None))
        assert not callable(getattr(batch, "score", None))
        assert not callable(getattr(batch, "rank", None))


# ============================================================
# K. No forbidden trading semantics are introduced
# ============================================================


class TestNoForbiddenTradingSemantics:
    def test_no_buy_sell(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        assert not hasattr(batch, "BUY")
        assert not hasattr(batch, "SELL")
        attrs = dir(batch)
        assert not any("buy" in a.lower() for a in attrs)
        assert not any("sell" in a.lower() for a in attrs)

    def test_no_target_stop(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        attrs = dir(batch)
        assert not any("target" in a.lower() for a in attrs)
        assert not any("stop" in a.lower() for a in attrs)

    def test_no_win_loss(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        attrs = dir(batch)
        assert not any("win" in a.lower() for a in attrs)
        assert not any("loss" in a.lower() for a in attrs)

    def test_no_confidence_ranking(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        attrs = dir(batch)
        assert not any("confidence" in a.lower() for a in attrs)
        assert not any("ranking" in a.lower() for a in attrs)
        assert not any("profitable" in a.lower() for a in attrs)

    def test_no_ml_execution(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        attrs = dir(batch)
        assert not any("ml" in a.lower() for a in attrs)
        assert not any("execution" in a.lower() for a in attrs)
        assert not any("paper_trading" in a.lower() for a in attrs)
        assert not any("live_trading" in a.lower() for a in attrs)
        assert not any("schedule" in a.lower() for a in attrs)
        assert not any("dashboard" in a.lower() for a in attrs)


# ============================================================
# L. Batch model invariants
# ============================================================


class TestBatchInvariants:
    def test_frozen(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        with pytest.raises(AttributeError):
            batch.batch_id = "other"  # type: ignore[misc]

    def test_batch_id_prefix(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        assert batch.batch_id.startswith("evidence-batch-")

    def test_count_invariant_sufficient_plus_insufficient(self) -> None:
        batch = aggregate_evidence(
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
        assert batch.sufficient_data_count + batch.insufficient_data_count == batch.total_occurrences


# ============================================================
# M. Batch properties (counts, is_empty)
# ============================================================


class TestBatchProperties:
    def test_is_empty_true(self) -> None:
        batch = aggregate_evidence(())
        assert batch.is_empty is True

    def test_is_empty_false(self) -> None:
        batch = aggregate_evidence((_occurrence("occ-1", _candidate()),))
        assert batch.is_empty is False

    def test_sufficient_count_all_available(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        batch = aggregate_evidence(
            (_occurrence("occ-1", cand1), _occurrence("occ-2", cand2))
        )
        assert batch.sufficient_data_count == 2
        assert batch.insufficient_data_count == 0

    def test_insufficient_count_all_insufficient(self) -> None:
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
        batch = aggregate_evidence((occ1, occ2))
        assert batch.sufficient_data_count == 0
        assert batch.insufficient_data_count == 2


# ============================================================
# N. Determinism (repeated aggregation produces identical batch)
# ============================================================


class TestDeterminism:
    def test_repeated_aggregation_identical(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = _occurrence("occ-2", cand2)
        batch1 = aggregate_evidence((occ1, occ2))
        batch2 = aggregate_evidence((occ1, occ2))
        assert batch1.batch_id == batch2.batch_id
        assert batch1.criterion_key == batch2.criterion_key
        assert batch1.total_occurrences == batch2.total_occurrences
        assert batch1.occurrences == batch2.occurrences

    def test_different_criterion_keys_different_batch_ids(self) -> None:
        occ = _occurrence("occ-1", _candidate())
        batch1 = aggregate_evidence((occ,), criterion_key="KEY_A")
        batch2 = aggregate_evidence((occ,), criterion_key="KEY_B")
        assert batch1.batch_id != batch2.batch_id

    def test_same_occurrences_different_order_same_batch(self) -> None:
        cand1 = _candidate(evaluation_time=_EPOCH)
        cand2 = _candidate(evaluation_time=_EPOCH + timedelta(days=1))
        occ1 = _occurrence("occ-1", cand1)
        occ2 = _occurrence("occ-2", cand2)
        batch1 = aggregate_evidence((occ1, occ2))
        batch2 = aggregate_evidence((occ2, occ1))
        assert batch1.batch_id == batch2.batch_id
        assert batch1.occurrences == batch2.occurrences
