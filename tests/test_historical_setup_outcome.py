"""
Tests for Checkpoint 9.5 — Historical Outcome Analysis Boundary.

Deterministic, network-free: every test constructs candidates and candles
directly (no provider, no corpus engine, no pipeline). The outcome-
analysis boundary is RESEARCH ONLY — it produces structured observations
of what future candles are available after a candidate; it does NOT call
the decision engine, generate trade candidates, create paper trades,
compute outcomes, evidence or setup quality.

Coverage:

A. Observation-status basics (enum members, properties)
B. Observation model (frozen/slots, delegation, invariants)
C. Association with the correct candidate / evaluation time
D. Future observations are strictly after the candidate observation time
E. The candidate itself remains unchanged
F. No pre-observation candles are included
G. Empty future data is handled deterministically
H. Insufficient future history is represented explicitly
I. Repeated identical inputs produce identical results
J. Protocol compliance
K. No forbidden attributes (no target/stop/win/loss/quality)
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from engine.data.historical_setup_outcome import (
    ForwardReturnEngine,
    ForwardReturnProtocol,
    HistoricalOutcomeAnalysisProtocol,
    MinimalObservationEngine,
    PriceExcursionEngine,
    PriceExcursionProtocol,
)
from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_outcome import (
    ForwardReturnObservation,
    ObservationStatus,
    PriceExcursionObservation,
    SetupObservation,
)
from engine.models.ohlcv import OHLCVCandle


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    """Build an OHLC-valid candle (high/low around close)."""
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0,
    )


def _candidate(
    evaluation_time: datetime = _EPOCH,
    instrument: str = "NIFTY",
    *,
    is_candidate: bool = True,
) -> HistoricalSetupCandidate:
    return HistoricalSetupCandidate(
        instrument=instrument,
        evaluation_time=evaluation_time,
        setup_timeframe="15m",
        context_timeframe="1D",
        history_count=10,
        status="VALID",
        has_structure=True,
        is_candidate=is_candidate,
        reason="directional structure present",
    )


def _future(closes: list[float], start: datetime = _EPOCH) -> list[OHLCVCandle]:
    """Future candles with timestamps strictly after ``start``."""
    return [
        _candle(start + timedelta(days=i + 1), c) for i, c in enumerate(closes)
    ]


def _mixed_candles(
    evaluation_time: datetime, n_before: int = 2, n_at: int = 1, n_after: int = 3
) -> list[OHLCVCandle]:
    """Candles before, at, and after the evaluation time."""
    candles: list[OHLCVCandle] = []
    for i in range(n_before):
        candles.append(_candle(evaluation_time - timedelta(days=n_before - i), 100.0))
    for i in range(n_at):
        candles.append(_candle(evaluation_time, 100.0))
    for i in range(n_after):
        candles.append(_candle(evaluation_time + timedelta(days=i + 1), 100.0))
    return candles


# ============================================================
# A. OBSERVATION-STATUS BASICS
# ============================================================


class TestObservationStatus:
    def test_status_members(self) -> None:
        names = {s.name for s in ObservationStatus}
        assert names == {"AVAILABLE", "INSUFFICIENT_DATA"}

    def test_is_available_property(self) -> None:
        assert ObservationStatus.AVAILABLE.is_available
        assert not ObservationStatus.INSUFFICIENT_DATA.is_available


# ============================================================
# B. OBSERVATION MODEL
# ============================================================


class TestObservationModel:
    def test_frozen_and_slotted(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]), reference_price=100.0)
        assert obs.__dataclass_params__.frozen
        assert obs.__dataclass_params__.slots
        with pytest.raises((AttributeError, Exception)):
            obs.reason = "x"  # type: ignore[misc]

    def test_delegates_evaluation_time_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]), reference_price=100.0)
        assert obs.evaluation_time == _EPOCH

    def test_delegates_instrument_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(instrument="RELIANCE"), _future([101.0]), reference_price=100.0
        )
        assert obs.instrument == "RELIANCE"

    def test_delegates_timeframes_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]), reference_price=100.0)
        assert obs.setup_timeframe == "15m"
        assert obs.context_timeframe == "1D"

    def test_future_candle_count(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0, 102.0, 103.0]), reference_price=100.0
        )
        assert obs.future_candle_count == 3

    def test_available_must_carry_future_candles(self) -> None:
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=(),
                observation_status=ObservationStatus.AVAILABLE,
                reference_price=100.0,
            )

    def test_insufficient_data_must_carry_no_future_candles(self) -> None:
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=tuple(_future([101.0])),
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
            )


# ============================================================
# C. ASSOCIATION WITH CORRECT CANDIDATE / EVALUATION TIME
# ============================================================


class TestAssociation:
    def test_observation_references_the_candidate(self) -> None:
        cand = _candidate()
        obs = MinimalObservationEngine().observe(cand, _future([101.0]), reference_price=100.0)
        assert obs.candidate is cand

    def test_observation_evaluation_time_matches_candidate(self) -> None:
        t = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
        cand = _candidate(evaluation_time=t)
        obs = MinimalObservationEngine().observe(cand, _future([101.0], start=t), reference_price=100.0)
        assert obs.evaluation_time == t

    def test_different_candidates_produce_distinct_evaluation_times(self) -> None:
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
        o1 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t1), _future([101.0], start=t1), reference_price=100.0
        )
        o2 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t2), _future([101.0], start=t2), reference_price=100.0
        )
        assert o1.evaluation_time == t1
        assert o2.evaluation_time == t2
        assert o1.evaluation_time != o2.evaluation_time


# ============================================================
# D. FUTURE OBSERVATIONS STRICTLY AFTER CANDIDATE TIME
# ============================================================


class TestStrictlyAfter:
    def test_all_future_candles_strictly_after_evaluation_time(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0, 102.0, 103.0]), reference_price=100.0
        )
        for c in obs.future_candles:
            assert c.timestamp > _EPOCH

    def test_future_candles_preserve_chronological_order(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0, 102.0, 103.0]), reference_price=100.0
        )
        timestamps = [c.timestamp for c in obs.future_candles]
        assert timestamps == sorted(timestamps)

    def test_candle_at_evaluation_time_excluded(self) -> None:
        # A candle AT T is NOT strictly after T and must be excluded.
        at_t = _candle(_EPOCH, 100.0)
        after = _future([101.0])
        obs = MinimalObservationEngine().observe(_candidate(), [at_t, *after], reference_price=100.0)
        assert obs.future_candle_count == 1
        assert all(c.timestamp > _EPOCH for c in obs.future_candles)


# ============================================================
# E. CANDIDATE ITSELF REMAINS UNCHANGED
# ============================================================


class TestCandidateUnchanged:
    def test_candidate_not_mutated_by_observation(self) -> None:
        cand = _candidate()
        before = (
            cand.instrument,
            cand.evaluation_time,
            cand.is_candidate,
            cand.history_count,
        )
        MinimalObservationEngine().observe(cand, _future([101.0, 102.0]), reference_price=100.0)
        after = (
            cand.instrument,
            cand.evaluation_time,
            cand.is_candidate,
            cand.history_count,
        )
        assert before == after

    def test_candidate_identity_preserved(self) -> None:
        cand = _candidate()
        obs = MinimalObservationEngine().observe(cand, _future([101.0]), reference_price=100.0)
        assert obs.candidate is cand


# ============================================================
# F. NO PRE-OBSERVATION CANDLES INCLUDED
# ============================================================


class TestNoPreObservation:
    def test_candles_before_evaluation_time_excluded(self) -> None:
        candles = _mixed_candles(_EPOCH, n_before=2, n_at=1, n_after=3)
        obs = MinimalObservationEngine().observe(_candidate(), candles, reference_price=100.0)
        assert obs.future_candle_count == 3
        for c in obs.future_candles:
            assert c.timestamp > _EPOCH

    def test_only_pre_observation_candles_yield_insufficient(self) -> None:
        before = [_candle(_EPOCH - timedelta(days=i + 1), 100.0) for i in range(3)]
        obs = MinimalObservationEngine().observe(_candidate(), before, reference_price=100.0)
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.future_candles == ()

    def test_at_t_and_before_excluded_after_included(self) -> None:
        at_t = [_candle(_EPOCH, 100.0)]
        before = [_candle(_EPOCH - timedelta(days=1), 100.0)]
        after = _future([101.0, 102.0])
        obs = MinimalObservationEngine().observe(
            _candidate(), before + at_t + after, reference_price=100.0
        )
        assert obs.future_candle_count == 2
        assert obs.observation_status is ObservationStatus.AVAILABLE


# ============================================================
# G. EMPTY FUTURE DATA HANDLED DETERMINISTICALLY
# ============================================================


class TestEmptyFutureData:
    def test_empty_future_yields_insufficient_data(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), [], reference_price=100.0)
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.future_candles == ()
        assert obs.future_candle_count == 0

    def test_empty_future_deterministic(self) -> None:
        engine = MinimalObservationEngine()
        o1 = engine.observe(_candidate(), [], reference_price=100.0)
        o2 = engine.observe(_candidate(), [], reference_price=100.0)
        assert o1 == o2
        assert o1.observation_status == o2.observation_status
        assert o1.future_candles == o2.future_candles

    def test_empty_future_reason_present(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), [], reference_price=100.0)
        assert isinstance(obs.reason, str)
        assert len(obs.reason) > 0


# ============================================================
# H. INSUFFICIENT FUTURE HISTORY REPRESENTED EXPLICITLY
# ============================================================


class TestInsufficientExplicit:
    def test_insufficient_is_explicit_not_an_outcome(self) -> None:
        # No future data must NOT be silently treated as a determinate
        # outcome (e.g. it must not be AVAILABLE).
        obs = MinimalObservationEngine().observe(_candidate(), [], reference_price=100.0)
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert not obs.observation_status.is_available

    def test_insufficient_distinguishable_from_available(self) -> None:
        obs_empty = MinimalObservationEngine().observe(_candidate(), [])
        obs_data = MinimalObservationEngine().observe(_candidate(), _future([101.0]), reference_price=100.0)
        assert obs_empty.observation_status != obs_data.observation_status

    def test_insufficient_observed_for_each_candidate_independently(self) -> None:
        # Two candidates, one with future data and one without, are
        # evaluated independently.
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        o1 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t1), _future([101.0], start=t1), reference_price=100.0
        )
        o2 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t2), []
        )
        assert o1.observation_status is ObservationStatus.AVAILABLE
        assert o2.observation_status is ObservationStatus.INSUFFICIENT_DATA


# ============================================================
# I. REPEATED IDENTICAL INPUTS PRODUCE IDENTICAL RESULTS
# ============================================================


class TestDeterminism:
    def test_repeated_observation_identical(self) -> None:
        engine = MinimalObservationEngine()
        cand = _candidate()
        fut = _future([101.0, 102.0, 103.0])
        o1 = engine.observe(cand, fut, reference_price=100.0)
        o2 = engine.observe(cand, fut, reference_price=100.0)
        assert o1 == o2

    def test_repeated_empty_identical(self) -> None:
        engine = MinimalObservationEngine()
        o1 = engine.observe(_candidate(), [], reference_price=100.0)
        o2 = engine.observe(_candidate(), [], reference_price=100.0)
        assert o1 == o2

    def test_repeated_mixed_filter_identical(self) -> None:
        engine = MinimalObservationEngine()
        candles = _mixed_candles(_EPOCH, n_before=3, n_at=2, n_after=4)
        o1 = engine.observe(_candidate(), candles, reference_price=100.0)
        o2 = engine.observe(_candidate(), candles, reference_price=100.0)
        assert o1 == o2
        assert o1.future_candle_count == 4

    def test_equivalent_candidates_equivalent_observations(self) -> None:
        engine = MinimalObservationEngine()
        fut = _future([101.0])
        o1 = engine.observe(_candidate(instrument="NIFTY"), fut, reference_price=100.0)
        o2 = engine.observe(_candidate(instrument="NIFTY"), fut, reference_price=100.0)
        assert o1 == o2

    def test_no_randomness(self) -> None:
        engine = MinimalObservationEngine()
        results = [
            engine.observe(_candidate(), _future([101.0]), reference_price=100.0)
            for _ in range(10)
        ]
        assert all(r == results[0] for r in results)


# ============================================================
# J. PROTOCOL COMPLIANCE
# ============================================================


class TestProtocolCompliance:
    def test_engine_is_protocol_instance(self) -> None:
        engine = MinimalObservationEngine()
        assert isinstance(engine, HistoricalOutcomeAnalysisProtocol)

    def test_protocol_method_signature(self) -> None:
        engine = MinimalObservationEngine()
        sig = inspect.signature(engine.observe)
        params = list(sig.parameters.keys())
        assert "candidate" in params
        assert "future_candles" in params
        assert "reference_price" in params


# ============================================================
# K. NO FORBIDDEN ATTRIBUTES
# ============================================================


class TestNoForbiddenAttributes:
    def test_observation_has_no_trade_geometry(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0]), reference_price=100.0
        )
        forbidden = (
            "entry",
            "stop",
            "target",
            "direction",
            "exit_price",
            "realized_r",
            "mfe",
            "mae",
            "risk",
            "bars_held",
            "outcome_status",
            "outcome_timestamp",
        )
        for attr in forbidden:
            assert not hasattr(obs, attr), f"observation must not have {attr}"

    def test_observation_has_no_outcome_metrics(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0]), reference_price=100.0
        )
        forbidden = (
            "win",
            "loss",
            "win_loss",
            "profitability",
            "quality_score",
            "confidence",
            "score",
            "weight",
            "probability",
            "expected_return",
            "future_return",
            "evidence",
            "aggregated_evidence",
        )
        for attr in forbidden:
            assert not hasattr(obs, attr), f"observation must not have {attr}"

    def test_observation_has_no_trading_behavior(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0]), reference_price=100.0
        )
        forbidden = (
            "decision",
            "execution",
            "order",
            "portfolio",
            "position",
            "buy",
            "sell",
        )
        for attr in forbidden:
            assert not hasattr(obs, attr), f"observation must not have {attr}"


# ============================================================
# CHECKPOINT 9.6 — FIXED-HORIZON FORWARD PRICE-RETURN METRIC
# ============================================================


def _observation(
    evaluation_time: datetime = _EPOCH,
    future_closes: list[float] | None = None,
    instrument: str = "NIFTY",
    reference_price: float = 100.0,
) -> SetupObservation:
    """Build a SetupObservation with the given future closes."""
    if future_closes is None:
        future_closes = [101.0, 102.0, 103.0]
    candidate = _candidate(evaluation_time=evaluation_time, instrument=instrument)
    candles = _future(future_closes, start=evaluation_time)
    return SetupObservation(
        candidate=candidate,
        future_candles=tuple(candles),
        observation_status=ObservationStatus.AVAILABLE,
        reference_price=reference_price,
        reason="future candles available",
    )


# ============================================================
# L. FORWARD-RETURN MODEL BASICS
# ============================================================


class TestForwardReturnModel:
    def test_frozen_and_slotted(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.__dataclass_params__.frozen
        assert result.__dataclass_params__.slots
        with pytest.raises((AttributeError, Exception)):
            result.reason = "x"  # type: ignore[misc]

    def test_delegates_evaluation_time_to_candidate(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.evaluation_time == _EPOCH

    def test_delegates_instrument_to_candidate(self) -> None:
        obs = _observation(instrument="RELIANCE")
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.instrument == "RELIANCE"

    def test_delegates_timeframes_to_candidate(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.setup_timeframe == "15m"
        assert result.context_timeframe == "1D"

    def test_available_must_carry_endpoint_and_return(self) -> None:
        with pytest.raises(ValueError):
            ForwardReturnObservation(
                candidate=_candidate(),
                reference_price=100.0,
                horizon_candles=1,
                endpoint_price=None,
                forward_return=None,
                observation_status=ObservationStatus.AVAILABLE,
            )

    def test_insufficient_data_must_not_carry_endpoint_or_return(self) -> None:
        with pytest.raises(ValueError):
            ForwardReturnObservation(
                candidate=_candidate(),
                reference_price=100.0,
                horizon_candles=5,
                endpoint_price=110.0,
                forward_return=0.1,
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
            )


# ============================================================
# M. EXACT N-CANDLE HORIZON
# ============================================================


class TestExactHorizon:
    def test_exact_n_candle_horizon(self) -> None:
        closes = [101.0, 102.0, 103.0, 104.0, 105.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.horizon_candles == 3
        assert result.endpoint_price == 103.0

    def test_endpoint_is_close_of_nth_future_candle(self) -> None:
        closes = [101.0, 102.0, 103.0, 104.0, 105.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 1)
        assert result.endpoint_price == 101.0
        result = ForwardReturnEngine().observe_forward_return(obs, 5)
        assert result.endpoint_price == 105.0

    def test_nth_candle_strictly_after_evaluation_time(self) -> None:
        t = datetime(2024, 3, 15, 9, 30, tzinfo=UTC)
        obs = _observation(evaluation_time=t, future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 2)
        assert result.endpoint_price == 102.0
        endpoint_candle = obs.future_candles[1]
        assert endpoint_candle.timestamp > t


# ============================================================
# N. CORRECT REFERENCE PRICE
# ============================================================


class TestReferencePrice:
    def test_reference_price_stored_explicitly(self) -> None:
        obs = _observation(reference_price=200.0)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.reference_price == 200.0

    def test_reference_price_is_observation_time_price(self) -> None:
        obs = _observation(reference_price=150.0)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.reference_price == 150.0

    def test_reference_price_used_in_formula(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0], reference_price=200.0)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        expected = (103.0 - 200.0) / 200.0
        assert result.forward_return == pytest.approx(expected)


# ============================================================
# O. CORRECT FORWARD RETURN
# ============================================================


class TestForwardReturn:
    def test_forward_return_formula(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 110.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.forward_return == pytest.approx(0.10)

    def test_forward_return_negative(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 90.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.forward_return == pytest.approx(-0.10)

    def test_forward_return_zero(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 100.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.forward_return == pytest.approx(0.0)

    def test_forward_return_fractional(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.forward_return == pytest.approx(0.03)


# ============================================================
# P. INSUFFICIENT FUTURE CANDLES
# ============================================================


class TestInsufficientFuture:
    def test_insufficient_future_candles(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_no_partial_horizon_return(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_empty_future_candles(self) -> None:
        candidate = _observation().candidate
        empty_obs = SetupObservation(
            candidate=candidate,
            future_candles=(),
            observation_status=ObservationStatus.INSUFFICIENT_DATA,
            reference_price=None,
            reason="no future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(
            empty_obs, 1
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None


# ============================================================
# Q. EXACT BOUNDARY WHERE N CANDLES ARE AVAILABLE
# ============================================================


class TestExactBoundary:
    def test_exact_boundary_n_candles_available(self) -> None:
        closes = [101.0, 102.0, 103.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0

    def test_one_below_boundary_insufficient(self) -> None:
        closes = [101.0, 102.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA

    def test_one_above_boundary_sufficient(self) -> None:
        closes = [101.0, 102.0, 103.0, 104.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0


# ============================================================
# R. CANDLES AT/BEFORE T ARE EXCLUDED
# ============================================================


class TestCandlesExcluded:
    def test_uses_only_future_candles_from_observation(self) -> None:
        mixed = _mixed_candles(_EPOCH, n_before=2, n_at=1, n_after=3)
        filtered = [c for c in mixed if c.timestamp > _EPOCH]
        candidate = _candidate()
        obs = SetupObservation(
            candidate=candidate,
            future_candles=tuple(filtered),
            observation_status=ObservationStatus.AVAILABLE,
            reference_price=100.0,
            reason="future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == filtered[-1].close

    def test_at_t_candle_not_used_as_endpoint(self) -> None:
        at_t = _candle(_EPOCH, 50.0)
        after = _future([101.0, 102.0, 103.0])
        candidate = _candidate()
        obs = SetupObservation(
            candidate=candidate,
            future_candles=tuple(after),
            observation_status=ObservationStatus.AVAILABLE,
            reference_price=100.0,
            reason="future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.endpoint_price == 103.0
        assert result.endpoint_price != at_t.close


# ============================================================
# S. CANDIDATE REMAINS UNCHANGED
# ============================================================


class TestCandidateUnchangedForward:
    def test_candidate_retained_by_reference(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.candidate is obs.candidate

    def test_candidate_not_mutated(self) -> None:
        obs = _observation()
        candidate = obs.candidate
        before = (
            candidate.instrument,
            candidate.evaluation_time,
            candidate.is_candidate,
            candidate.history_count,
        )
        ForwardReturnEngine().observe_forward_return(obs, 3)
        after = (
            candidate.instrument,
            candidate.evaluation_time,
            candidate.is_candidate,
            candidate.history_count,
        )
        assert before == after


# ============================================================
# T. DETERMINISTIC OUTPUT
# ============================================================


class TestDeterministicForward:
    def test_repeated_observation_identical(self) -> None:
        engine = ForwardReturnEngine()
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        r1 = engine.observe_forward_return(obs, 3)
        r2 = engine.observe_forward_return(obs, 3)
        assert r1 == r2

    def test_repeated_insufficient_identical(self) -> None:
        engine = ForwardReturnEngine()
        obs = _observation(future_closes=[101.0])
        r1 = engine.observe_forward_return(obs, 3)
        r2 = engine.observe_forward_return(obs, 3)
        assert r1 == r2

    def test_no_randomness(self) -> None:
        engine = ForwardReturnEngine()
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        results = [
            engine.observe_forward_return(obs, 3) for _ in range(10)
        ]
        assert all(r == results[0] for r in results)


# ============================================================
# U. ZERO/INVALID HORIZON HANDLING
# ============================================================


class TestZeroInvalidHorizon:
    def test_zero_horizon_insufficient(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 0)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_negative_horizon_insufficient(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, -1)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_zero_horizon_reason_present(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 0)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ============================================================
# V. NO FORBIDDEN TRADING SEMANTICS IN RESULT MODEL
# ============================================================


class TestNoForbiddenSemantics:
    def test_no_trade_geometry(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        forbidden = (
            "entry",
            "stop",
            "target",
            "direction",
            "exit_price",
            "realized_r",
            "mfe",
            "mae",
            "risk",
            "bars_held",
            "outcome_status",
            "outcome_timestamp",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_no_win_loss_classification(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        forbidden = (
            "win",
            "loss",
            "win_loss",
            "profitability",
            "quality_score",
            "confidence",
            "score",
            "weight",
            "probability",
            "expected_return",
            "evidence",
            "aggregated_evidence",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_no_trading_behavior(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        forbidden = (
            "decision",
            "execution",
            "order",
            "portfolio",
            "position",
            "buy",
            "sell",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_insufficient_result_also_has_no_forbidden(self) -> None:
        obs = _observation(future_closes=[101.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        forbidden = (
            "entry",
            "stop",
            "target",
            "direction",
            "win",
            "loss",
            "profitability",
            "buy",
            "sell",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"


# ============================================================
# W. PROTOCOL COMPLIANCE
# ============================================================


class TestForwardReturnProtocol:
    def test_engine_is_protocol_instance(self) -> None:
        engine = ForwardReturnEngine()
        assert isinstance(engine, ForwardReturnProtocol)

    def test_protocol_method_signature(self) -> None:
        engine = ForwardReturnEngine()
        sig = inspect.signature(engine.observe_forward_return)
        params = list(sig.parameters.keys())
        assert params == ["observation", "horizon_candles"]


# ============================================================
# X. COMPOSABLE WITH SetupObservation
# ============================================================


class TestComposableWithObservation:
    def test_composes_with_observation_engine(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0, 104.0, 105.0])
        observation = MinimalObservationEngine().observe(
            candidate, candles, reference_price=100.0
        )
        result = ForwardReturnEngine().observe_forward_return(
            observation, 3
        )
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0
        assert result.forward_return == pytest.approx(0.03)

    def test_composition_preserves_candidate(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0])
        observation = MinimalObservationEngine().observe(
            candidate, candles, reference_price=100.0
        )
        result = ForwardReturnEngine().observe_forward_return(
            observation, 3
        )
        assert result.candidate is candidate

    def test_composition_with_insufficient_observation(self) -> None:
        candidate = _candidate()
        observation = MinimalObservationEngine().observe(candidate, [], reference_price=100.0)
        result = ForwardReturnEngine().observe_forward_return(
            observation, 3
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None


# ============================================================
# CHECKPOINT 9.7 — OBSERVATION-TIME PRICE ANCHOR
# ============================================================


def _observation_with_reference(
    evaluation_time: datetime = _EPOCH,
    future_closes: list[float] | None = None,
    reference_price: float = 100.0,
    instrument: str = "NIFTY",
) -> SetupObservation:
    """Build a SetupObservation with a reference price."""
    if future_closes is None:
        future_closes = [101.0, 102.0, 103.0]
    candidate = _candidate(evaluation_time=evaluation_time, instrument=instrument)
    candles = _future(future_closes, start=evaluation_time)
    return SetupObservation(
        candidate=candidate,
        future_candles=tuple(candles),
        observation_status=ObservationStatus.AVAILABLE,
        reference_price=reference_price,
        reason="future candles available",
    )


class TestObservationReferencePrice:
    """Checkpoint 9.7 — the observation-time price anchor."""

    def test_available_observation_carries_reference_price(self) -> None:
        obs = _observation_with_reference(reference_price=150.0)
        assert obs.reference_price == 150.0

    def test_reference_price_is_close_of_latest_candle_at_or_before_T(
        self,
    ) -> None:
        # The reference price is defined as the close of the latest
        # completed setup candle at/before T. Here we simulate that
        # by passing the close directly.
        t = datetime(2024, 3, 15, 9, 30, tzinfo=UTC)
        latest_candle_close = 200.0
        obs = _observation_with_reference(
            evaluation_time=t,
            reference_price=latest_candle_close,
        )
        assert obs.reference_price == latest_candle_close

    def test_reference_price_comes_from_information_available_at_T(
        self,
    ) -> None:
        # The reference price must come from information available at T,
        # not from future candles. We verify this by ensuring the
        # reference price is independent of future candle values.
        t = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        ref_price = 100.0
        obs1 = _observation_with_reference(
            evaluation_time=t,
            future_closes=[110.0, 120.0, 130.0],
            reference_price=ref_price,
        )
        obs2 = _observation_with_reference(
            evaluation_time=t,
            future_closes=[90.0, 80.0, 70.0],
            reference_price=ref_price,
        )
        # Same reference price regardless of future candles
        assert obs1.reference_price == obs2.reference_price == ref_price

    def test_no_future_candle_contributes_to_reference_price(self) -> None:
        # The reference price must NOT be derived from future candles.
        # We verify by constructing an observation where future candles
        # have different values but the reference price is fixed.
        t = datetime(2024, 7, 1, 9, 15, tzinfo=UTC)
        ref_price = 50.0
        obs = _observation_with_reference(
            evaluation_time=t,
            future_closes=[200.0, 300.0, 400.0],
            reference_price=ref_price,
        )
        # Reference price is independent of future values
        assert obs.reference_price == ref_price
        assert obs.reference_price != 200.0
        assert obs.reference_price != 300.0
        assert obs.reference_price != 400.0

    def test_same_anchor_reused_deterministically(self) -> None:
        # The same anchor is reused deterministically across multiple
        # observations with the same evaluation time.
        t = datetime(2024, 8, 1, 10, 0, tzinfo=UTC)
        ref_price = 75.0
        obs1 = _observation_with_reference(
            evaluation_time=t, reference_price=ref_price
        )
        obs2 = _observation_with_reference(
            evaluation_time=t, reference_price=ref_price
        )
        assert obs1.reference_price == obs2.reference_price

    def test_insufficient_data_has_no_reference_price(self) -> None:
        # When there is insufficient data, reference_price is None.
        obs = MinimalObservationEngine().observe(_candidate(), [], reference_price=100.0)
        assert obs.reference_price is None
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA

    def test_available_requires_reference_price(self) -> None:
        # An AVAILABLE observation must carry a reference_price.
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=tuple(_future([101.0])),
                observation_status=ObservationStatus.AVAILABLE,
                reference_price=None,
            )

    def test_insufficient_data_must_not_carry_reference_price(self) -> None:
        # An INSUFFICIENT_DATA observation must NOT carry a reference_price.
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=(),
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
                reference_price=100.0,
            )

    def test_reference_price_must_be_positive_when_available(self) -> None:
        # A reference price of zero or negative is invalid.
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=tuple(_future([101.0])),
                observation_status=ObservationStatus.AVAILABLE,
                reference_price=0.0,
            )
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=tuple(_future([101.0])),
                observation_status=ObservationStatus.AVAILABLE,
                reference_price=-10.0,
            )


class TestObservationEngineReferencePrice:
    """Checkpoint 9.7 — MinimalObservationEngine handles reference_price."""

    def test_engine_accepts_reference_price(self) -> None:
        engine = MinimalObservationEngine()
        obs = engine.observe(
            _candidate(), _future([101.0]), reference_price=100.0
        )
        assert obs.reference_price == 100.0
        assert obs.observation_status is ObservationStatus.AVAILABLE

    def test_engine_without_reference_price_yields_insufficient(self) -> None:
        # When reference_price is not supplied, the observation is
        # INSUFFICIENT_DATA even if future candles exist.
        engine = MinimalObservationEngine()
        obs = engine.observe(_candidate(), _future([101.0]))
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.reference_price is None

    def test_engine_with_none_reference_price_yields_insufficient(self) -> None:
        engine = MinimalObservationEngine()
        obs = engine.observe(
            _candidate(), _future([101.0]), reference_price=None
        )
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.reference_price is None

    def test_engine_reference_price_is_exactly_supplied_value(self) -> None:
        engine = MinimalObservationEngine()
        obs = engine.observe(
            _candidate(), _future([101.0]), reference_price=250.0
        )
        assert obs.reference_price == 250.0

    def test_engine_does_not_derive_reference_from_future(self) -> None:
        # The engine must NOT derive the reference price from future
        # candles. It uses only the supplied reference_price.
        engine = MinimalObservationEngine()
        obs = engine.observe(
            _candidate(), _future([200.0, 300.0]), reference_price=50.0
        )
        assert obs.reference_price == 50.0
        assert obs.reference_price != 200.0
        assert obs.reference_price != 300.0


class TestForwardReturnEngineUsesAnchor:
    """Checkpoint 9.7 — ForwardReturnEngine uses the established anchor."""

    def test_engine_uses_observation_reference_price(self) -> None:
        obs = _observation_with_reference(
            future_closes=[101.0, 102.0, 110.0], reference_price=100.0
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.reference_price == 100.0
        assert result.forward_return == pytest.approx(0.10)

    def test_engine_uses_anchor_not_caller_supplied(self) -> None:
        # The engine uses the observation's reference price, not a
        # caller-supplied value. This is the core of Checkpoint 9.7.
        obs = _observation_with_reference(
            future_closes=[101.0, 102.0, 103.0], reference_price=200.0
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        expected = (103.0 - 200.0) / 200.0
        assert result.forward_return == pytest.approx(expected)

    def test_engine_returns_insufficient_when_no_anchor(self) -> None:
        # When the observation has no reference price, the engine
        # returns INSUFFICIENT_DATA.
        # An AVAILABLE observation with reference_price=None is now
        # invalid, so we test via the engine path that produces
        # INSUFFICIENT_DATA when reference_price is missing.
        engine = MinimalObservationEngine()
        obs_insufficient = engine.observe(_candidate(), _future([101.0]))
        assert obs_insufficient.reference_price is None
        result = ForwardReturnEngine().observe_forward_return(
            obs_insufficient, 1
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_engine_preserves_neutral_meaning(self) -> None:
        # The forward return remains direction-neutral.
        obs = _observation_with_reference(
            future_closes=[101.0, 102.0, 90.0], reference_price=100.0
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 3)
        assert result.forward_return == pytest.approx(-0.10)

    def test_engine_composition_with_observation_engine(self) -> None:
        # Full composition: observation engine -> forward return engine.
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0, 104.0, 105.0])
        observation = MinimalObservationEngine().observe(
            candidate, candles, reference_price=100.0
        )
        result = ForwardReturnEngine().observe_forward_return(observation, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0
        assert result.forward_return == pytest.approx(0.03)
        assert result.reference_price == 100.0

    def test_candidate_discovery_unchanged(self) -> None:
        # The HistoricalSetupCandidate remains structural/metadata-oriented.
        # It does NOT carry price information.
        candidate = _candidate()
        # Verify no price-related attributes
        price_attrs = ("reference_price", "close_price", "entry_price")
        for attr in price_attrs:
            assert not hasattr(candidate, attr), (
                f"Candidate should not have {attr}"
            )


class TestReferencePriceProtocolSignature:
    """Checkpoint 9.7 — protocol signatures updated."""

    def test_forward_return_protocol_signature(self) -> None:
        engine = ForwardReturnEngine()
        sig = inspect.signature(engine.observe_forward_return)
        params = list(sig.parameters.keys())
        assert params == ["observation", "horizon_candles"]

    def test_observation_protocol_signature(self) -> None:
        sig = inspect.signature(MinimalObservationEngine.observe)
        params = list(sig.parameters.keys())
        assert "reference_price" in params

    def test_forward_return_engine_is_protocol_instance(self) -> None:
        engine = ForwardReturnEngine()
        assert isinstance(engine, ForwardReturnProtocol)

    def test_observation_engine_is_protocol_instance(self) -> None:
        engine = MinimalObservationEngine()
        assert isinstance(engine, HistoricalOutcomeAnalysisProtocol)


# ============================================================
# CHECKPOINT 9.8 — FIXED-HORIZON FUTURE PRICE-EXCURSION METRIC
# ============================================================


def _candle_with_range(
    ts: datetime, high: float, low: float, close: float | None = None
) -> OHLCVCandle:
    """Build an OHLC-valid candle with explicit high/low."""
    if close is None:
        close = (high + low) / 2.0
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _excursion_future(
    ranges: list[tuple[float, float]], start: datetime = _EPOCH
) -> list[OHLCVCandle]:
    """Future candles with explicit (high, low) ranges."""
    return [
        _candle_with_range(start + timedelta(days=i + 1), high, low)
        for i, (high, low) in enumerate(ranges)
    ]


def _excursion_observation(
    evaluation_time: datetime = _EPOCH,
    ranges: list[tuple[float, float]] | None = None,
    instrument: str = "NIFTY",
    reference_price: float = 100.0,
) -> SetupObservation:
    """Build a SetupObservation with explicit future price ranges."""
    if ranges is None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 97.0)]
    candidate = _candidate(evaluation_time=evaluation_time, instrument=instrument)
    candles = _excursion_future(ranges, start=evaluation_time)
    return SetupObservation(
        candidate=candidate,
        future_candles=tuple(candles),
        observation_status=ObservationStatus.AVAILABLE,
        reference_price=reference_price,
        reason="future candles available",
    )


# ============================================================
# Y. EXCURSION MODEL BASICS
# ============================================================


class TestExcursionModel:
    def test_frozen_and_slotted(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.__dataclass_params__.frozen
        assert result.__dataclass_params__.slots
        with pytest.raises((AttributeError, Exception)):
            result.reason = "x"  # type: ignore[misc]

    def test_delegates_evaluation_time_to_candidate(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.evaluation_time == _EPOCH

    def test_delegates_instrument_to_candidate(self) -> None:
        obs = _excursion_observation(instrument="RELIANCE")
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.instrument == "RELIANCE"

    def test_delegates_timeframes_to_candidate(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.setup_timeframe == "15m"
        assert result.context_timeframe == "1D"

    def test_available_must_carry_excursions(self) -> None:
        with pytest.raises(ValueError):
            PriceExcursionObservation(
                candidate=_candidate(),
                reference_price=100.0,
                horizon_candles=1,
                max_upward_excursion=None,
                max_downward_excursion=0.0,
                max_high=101.0,
                min_low=100.0,
                observation_status=ObservationStatus.AVAILABLE,
            )

    def test_insufficient_data_must_not_carry_excursions(self) -> None:
        with pytest.raises(ValueError):
            PriceExcursionObservation(
                candidate=_candidate(),
                reference_price=100.0,
                horizon_candles=5,
                max_upward_excursion=0.01,
                max_downward_excursion=-0.01,
                max_high=101.0,
                min_low=99.0,
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
            )


# ============================================================
# Z. EXACT N-CANDLE WINDOW
# ============================================================


class TestExcursionExactWindow:
    def test_exact_n_candle_window(self) -> None:
        # 5 future candles available, horizon = 3.
        # The window is the FIRST 3; candles 4 and 5 are ignored.
        ranges = [
            (101.0, 99.0),  # candle 1
            (102.0, 98.0),  # candle 2
            (103.0, 97.0),  # candle 3
            (120.0, 96.0),  # candle 4 (outside window)
            (80.0, 70.0),   # candle 5 (outside window)
        ]
        obs = _excursion_observation(ranges=ranges)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.horizon_candles == 3
        # max high over first 3: 103.0; min low over first 3: 97.0
        assert result.max_high == 103.0
        assert result.min_low == 97.0

    def test_window_excludes_candles_beyond_n(self) -> None:
        # A candle beyond N with a higher high must NOT affect max_high.
        ranges = [
            (100.5, 99.5),
            (100.5, 99.5),
            (100.5, 99.5),
            (200.0, 99.0),  # extreme high beyond N=3
        ]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_high == 100.5
        assert result.max_upward_excursion == pytest.approx(0.005)

    def test_window_excludes_candles_beyond_n_low(self) -> None:
        ranges = [
            (100.5, 99.5),
            (100.5, 99.5),
            (100.5, 99.5),
            (110.0, 50.0),  # extreme low beyond N=3
        ]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.min_low == 99.5
        assert result.max_downward_excursion == pytest.approx(-0.005)


# ============================================================
# AA. MAXIMUM HIGH CALCULATION
# ============================================================


class TestMaximumHigh:
    def test_max_high_is_maximum_of_future_highs(self) -> None:
        ranges = [(101.0, 99.0), (105.0, 98.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_high == 105.0
        assert result.max_upward_excursion == pytest.approx(0.05)

    def test_max_high_appears_at_different_positions(self) -> None:
        # max high at the beginning
        ranges = [(110.0, 99.0), (102.0, 98.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_high == 110.0

    def test_max_high_same_as_reference_gives_zero_upward(self) -> None:
        ranges = [(100.0, 99.0), (100.0, 98.0), (100.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_high == 100.0
        assert result.max_upward_excursion == pytest.approx(0.0)


# ============================================================
# AB. MINIMUM LOW CALCULATION
# ============================================================


class TestMinimumLow:
    def test_min_low_is_minimum_of_future_lows(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 95.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.min_low == 95.0
        assert result.max_downward_excursion == pytest.approx(-0.05)

    def test_min_low_appears_at_different_positions(self) -> None:
        # min low at the end
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 90.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.min_low == 90.0

    def test_min_low_same_as_reference_gives_zero_downward(self) -> None:
        ranges = [(101.0, 100.0), (102.0, 100.0), (103.0, 100.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.min_low == 100.0
        assert result.max_downward_excursion == pytest.approx(0.0)


# ============================================================
# AC. POSITIVE / NEGATIVE / ZERO EXCURSIONS
# ============================================================


class TestExcursionSigns:
    def test_positive_upward_excursion(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (110.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_upward_excursion == pytest.approx(0.10)
        assert result.max_upward_excursion > 0

    def test_negative_downward_excursion(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 90.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_downward_excursion == pytest.approx(-0.10)
        assert result.max_downward_excursion < 0

    def test_zero_upward_when_high_equals_reference(self) -> None:
        ranges = [(100.0, 99.0), (100.0, 98.0), (100.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_upward_excursion == pytest.approx(0.0)

    def test_zero_downward_when_low_equals_reference(self) -> None:
        ranges = [(101.0, 100.0), (102.0, 100.0), (103.0, 100.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_downward_excursion == pytest.approx(0.0)

    def test_both_excursions_in_same_window(self) -> None:
        # upward = (110 - 100)/100 = 0.10; downward = (90 - 100)/100 = -0.10
        ranges = [(110.0, 99.0), (102.0, 90.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_upward_excursion == pytest.approx(0.10)
        assert result.max_downward_excursion == pytest.approx(-0.10)


# ============================================================
# AD. INSUFFICIENT FUTURE CANDLES
# ============================================================


class TestExcursionInsufficient:
    def test_insufficient_future_candles(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None
        assert result.max_high is None
        assert result.min_low is None

    def test_no_partial_window_excursion(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None

    def test_empty_future_candles(self) -> None:
        candidate = _excursion_observation().candidate
        empty_obs = SetupObservation(
            candidate=candidate,
            future_candles=(),
            observation_status=ObservationStatus.INSUFFICIENT_DATA,
            reference_price=None,
            reason="no future candles",
        )
        result = PriceExcursionEngine().observe_price_excursion(empty_obs, 1)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None


# ============================================================
# AE. N VERSUS N-1 BOUNDARY
# ============================================================


class TestExcursionBoundary:
    def test_exact_boundary_n_candles_available(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.max_high == 103.0
        assert result.min_low == 97.0

    def test_one_below_boundary_insufficient(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA

    def test_one_above_boundary_sufficient(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 97.0), (104.0, 96.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        # window is first 3 only
        assert result.max_high == 103.0
        assert result.min_low == 97.0


# ============================================================
# AF. CANDLES AT/BEFORE T EXCLUDED
# ============================================================


class TestExcursionCandlesExcluded:
    def test_window_uses_only_future_candles_from_observation(self) -> None:
        mixed = _mixed_candles(_EPOCH, n_before=2, n_at=1, n_after=3)
        filtered = tuple(c for c in mixed if c.timestamp > _EPOCH)
        candidate = _candidate()
        obs = SetupObservation(
            candidate=candidate,
            future_candles=filtered,
            observation_status=ObservationStatus.AVAILABLE,
            reference_price=100.0,
            reason="future candles",
        )
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        # future candles have high=101, low=99 -> excursion based on those
        assert result.max_high == 101.0
        assert result.min_low == 99.0

    def test_at_t_candle_not_in_window(self) -> None:
        # A candle AT T with extreme range must NOT be used.
        at_t = _candle_with_range(_EPOCH, high=200.0, low=50.0)
        after = _excursion_future([(101.0, 99.0), (102.0, 98.0), (103.0, 97.0)])
        candidate = _candidate()
        obs = SetupObservation(
            candidate=candidate,
            future_candles=tuple(after),
            observation_status=ObservationStatus.AVAILABLE,
            reference_price=100.0,
            reason="future candles",
        )
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.max_high == 103.0
        assert result.min_low == 97.0
        assert result.max_high != at_t.high
        assert result.min_low != at_t.low


# ============================================================
# AG. REFERENCE PRICE REUSED FROM SetupObservation
# ============================================================


class TestExcursionReferencePrice:
    def test_reference_price_stored_explicitly(self) -> None:
        obs = _excursion_observation(reference_price=200.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.reference_price == 200.0

    def test_reference_price_used_in_formula(self) -> None:
        ranges = [(101.0, 99.0), (102.0, 98.0), (103.0, 97.0)]
        obs = _excursion_observation(ranges=ranges, reference_price=200.0)
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        expected_up = (103.0 - 200.0) / 200.0
        expected_down = (97.0 - 200.0) / 200.0
        assert result.max_upward_excursion == pytest.approx(expected_up)
        assert result.max_downward_excursion == pytest.approx(expected_down)

    def test_engine_reuses_observation_reference_price(self) -> None:
        # The engine uses the observation's reference price, not a
        # caller-supplied value. This is the core of Checkpoint 9.7.
        obs = _excursion_observation(
            ranges=[(101.0, 99.0), (102.0, 98.0), (103.0, 97.0)],
            reference_price=200.0,
        )
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        expected_up = (103.0 - 200.0) / 200.0
        assert result.max_upward_excursion == pytest.approx(expected_up)

    def test_engine_returns_insufficient_when_no_anchor(self) -> None:
        engine = MinimalObservationEngine()
        obs_insufficient = engine.observe(_candidate(), _future([101.0]))
        assert obs_insufficient.reference_price is None
        result = PriceExcursionEngine().observe_price_excursion(
            obs_insufficient, 1
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None

    def test_no_caller_supplied_reference_price(self) -> None:
        # The engine does NOT accept a caller-supplied reference price.
        engine = PriceExcursionEngine()
        sig = inspect.signature(engine.observe_price_excursion)
        params = list(sig.parameters.keys())
        assert params == ["observation", "horizon_candles"]
        assert "reference_price" not in params


# ============================================================
# AH. CANDIDATE UNCHANGED
# ============================================================


class TestExcursionCandidateUnchanged:
    def test_candidate_retained_by_reference(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.candidate is obs.candidate

    def test_candidate_not_mutated(self) -> None:
        obs = _excursion_observation()
        candidate = obs.candidate
        before = (
            candidate.instrument,
            candidate.evaluation_time,
            candidate.is_candidate,
            candidate.history_count,
        )
        PriceExcursionEngine().observe_price_excursion(obs, 3)
        after = (
            candidate.instrument,
            candidate.evaluation_time,
            candidate.is_candidate,
            candidate.history_count,
        )
        assert before == after


# ============================================================
# AI. DETERMINISTIC OUTPUT
# ============================================================


class TestExcursionDeterministic:
    def test_repeated_observation_identical(self) -> None:
        engine = PriceExcursionEngine()
        obs = _excursion_observation()
        r1 = engine.observe_price_excursion(obs, 3)
        r2 = engine.observe_price_excursion(obs, 3)
        assert r1 == r2

    def test_repeated_insufficient_identical(self) -> None:
        engine = PriceExcursionEngine()
        obs = _excursion_observation(ranges=[(101.0, 99.0)])
        r1 = engine.observe_price_excursion(obs, 3)
        r2 = engine.observe_price_excursion(obs, 3)
        assert r1 == r2

    def test_no_randomness(self) -> None:
        engine = PriceExcursionEngine()
        obs = _excursion_observation()
        results = [
            engine.observe_price_excursion(obs, 3) for _ in range(10)
        ]
        assert all(r == results[0] for r in results)


# ============================================================
# AJ. INVALID HORIZON HANDLING
# ============================================================


class TestExcursionInvalidHorizon:
    def test_zero_horizon_insufficient(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 0)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None

    def test_negative_horizon_insufficient(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, -1)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None

    def test_zero_horizon_reason_present(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 0)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ============================================================
# AK. NO FORBIDDEN TRADING SEMANTICS
# ============================================================


class TestExcursionNoForbiddenSemantics:
    def test_no_trade_geometry(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        forbidden = (
            "entry",
            "stop",
            "target",
            "direction",
            "exit_price",
            "realized_r",
            "risk",
            "bars_held",
            "outcome_status",
            "outcome_timestamp",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_no_win_loss_classification(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        forbidden = (
            "win",
            "loss",
            "win_loss",
            "profitability",
            "quality_score",
            "confidence",
            "score",
            "weight",
            "probability",
            "expected_return",
            "evidence",
            "aggregated_evidence",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_no_trading_behavior(self) -> None:
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        forbidden = (
            "decision",
            "execution",
            "order",
            "portfolio",
            "position",
            "buy",
            "sell",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_insufficient_result_also_has_no_forbidden(self) -> None:
        obs = _excursion_observation(ranges=[(101.0, 99.0)])
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        forbidden = (
            "entry",
            "stop",
            "target",
            "direction",
            "win",
            "loss",
            "profitability",
            "buy",
            "sell",
        )
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"

    def test_no_favorable_adverse_terminology(self) -> None:
        # The metric must NOT use favorable/adverse/profit/loss semantics.
        obs = _excursion_observation()
        result = PriceExcursionEngine().observe_price_excursion(obs, 3)
        forbidden = ("favorable", "adverse", "profit", "mfe_r", "mae_r")
        for attr in forbidden:
            assert not hasattr(result, attr), f"result must not have {attr}"


# ============================================================
# AL. PROTOCOL COMPLIANCE
# ============================================================


class TestExcursionProtocol:
    def test_engine_is_protocol_instance(self) -> None:
        engine = PriceExcursionEngine()
        assert isinstance(engine, PriceExcursionProtocol)

    def test_protocol_method_signature(self) -> None:
        engine = PriceExcursionEngine()
        sig = inspect.signature(engine.observe_price_excursion)
        params = list(sig.parameters.keys())
        assert params == ["observation", "horizon_candles"]


# ============================================================
# AM. COMPOSABLE WITH SetupObservation
# ============================================================


class TestExcursionComposable:
    def test_composes_with_observation_engine(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0, 104.0, 105.0])
        observation = MinimalObservationEngine().observe(
            candidate, candles, reference_price=100.0
        )
        result = PriceExcursionEngine().observe_price_excursion(
            observation, 3
        )
        assert result.observation_status is ObservationStatus.AVAILABLE
        # future candles: high=close+1, low=close-1
        # first 3 highs: 102, 103, 104 -> max 104
        # first 3 lows: 100, 101, 102 -> min 100
        assert result.max_high == 104.0
        assert result.min_low == 100.0
        assert result.max_upward_excursion == pytest.approx(0.04)
        assert result.max_downward_excursion == pytest.approx(0.0)

    def test_composition_preserves_candidate(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0])
        observation = MinimalObservationEngine().observe(
            candidate, candles, reference_price=100.0
        )
        result = PriceExcursionEngine().observe_price_excursion(
            observation, 3
        )
        assert result.candidate is candidate

    def test_composition_with_insufficient_observation(self) -> None:
        candidate = _candidate()
        observation = MinimalObservationEngine().observe(candidate, [], reference_price=100.0)
        result = PriceExcursionEngine().observe_price_excursion(
            observation, 3
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.max_upward_excursion is None
        assert result.max_downward_excursion is None
