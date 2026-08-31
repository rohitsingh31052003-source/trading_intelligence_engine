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
)
from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_outcome import (
    ForwardReturnObservation,
    ObservationStatus,
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
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]))
        assert obs.__dataclass_params__.frozen
        assert obs.__dataclass_params__.slots
        with pytest.raises((AttributeError, Exception)):
            obs.reason = "x"  # type: ignore[misc]

    def test_delegates_evaluation_time_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]))
        assert obs.evaluation_time == _EPOCH

    def test_delegates_instrument_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(instrument="RELIANCE"), _future([101.0])
        )
        assert obs.instrument == "RELIANCE"

    def test_delegates_timeframes_to_candidate(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), _future([101.0]))
        assert obs.setup_timeframe == "15m"
        assert obs.context_timeframe == "1D"

    def test_future_candle_count(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0, 102.0, 103.0])
        )
        assert obs.future_candle_count == 3

    def test_available_must_carry_future_candles(self) -> None:
        with pytest.raises(ValueError):
            SetupObservation(
                candidate=_candidate(),
                future_candles=(),
                observation_status=ObservationStatus.AVAILABLE,
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
        obs = MinimalObservationEngine().observe(cand, _future([101.0]))
        assert obs.candidate is cand

    def test_observation_evaluation_time_matches_candidate(self) -> None:
        t = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
        cand = _candidate(evaluation_time=t)
        obs = MinimalObservationEngine().observe(cand, _future([101.0], start=t))
        assert obs.evaluation_time == t

    def test_different_candidates_produce_distinct_evaluation_times(self) -> None:
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
        o1 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t1), _future([101.0], start=t1)
        )
        o2 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t2), _future([101.0], start=t2)
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
            _candidate(), _future([101.0, 102.0, 103.0])
        )
        for c in obs.future_candles:
            assert c.timestamp > _EPOCH

    def test_future_candles_preserve_chronological_order(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0, 102.0, 103.0])
        )
        timestamps = [c.timestamp for c in obs.future_candles]
        assert timestamps == sorted(timestamps)

    def test_candle_at_evaluation_time_excluded(self) -> None:
        # A candle AT T is NOT strictly after T and must be excluded.
        at_t = _candle(_EPOCH, 100.0)
        after = _future([101.0])
        obs = MinimalObservationEngine().observe(_candidate(), [at_t, *after])
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
        MinimalObservationEngine().observe(cand, _future([101.0, 102.0]))
        after = (
            cand.instrument,
            cand.evaluation_time,
            cand.is_candidate,
            cand.history_count,
        )
        assert before == after

    def test_candidate_identity_preserved(self) -> None:
        cand = _candidate()
        obs = MinimalObservationEngine().observe(cand, _future([101.0]))
        assert obs.candidate is cand


# ============================================================
# F. NO PRE-OBSERVATION CANDLES INCLUDED
# ============================================================


class TestNoPreObservation:
    def test_candles_before_evaluation_time_excluded(self) -> None:
        candles = _mixed_candles(_EPOCH, n_before=2, n_at=1, n_after=3)
        obs = MinimalObservationEngine().observe(_candidate(), candles)
        assert obs.future_candle_count == 3
        for c in obs.future_candles:
            assert c.timestamp > _EPOCH

    def test_only_pre_observation_candles_yield_insufficient(self) -> None:
        before = [_candle(_EPOCH - timedelta(days=i + 1), 100.0) for i in range(3)]
        obs = MinimalObservationEngine().observe(_candidate(), before)
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.future_candles == ()

    def test_at_t_and_before_excluded_after_included(self) -> None:
        at_t = [_candle(_EPOCH, 100.0)]
        before = [_candle(_EPOCH - timedelta(days=1), 100.0)]
        after = _future([101.0, 102.0])
        obs = MinimalObservationEngine().observe(
            _candidate(), before + at_t + after
        )
        assert obs.future_candle_count == 2
        assert obs.observation_status is ObservationStatus.AVAILABLE


# ============================================================
# G. EMPTY FUTURE DATA HANDLED DETERMINISTICALLY
# ============================================================


class TestEmptyFutureData:
    def test_empty_future_yields_insufficient_data(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), [])
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert obs.future_candles == ()
        assert obs.future_candle_count == 0

    def test_empty_future_deterministic(self) -> None:
        engine = MinimalObservationEngine()
        o1 = engine.observe(_candidate(), [])
        o2 = engine.observe(_candidate(), [])
        assert o1 == o2
        assert o1.observation_status == o2.observation_status
        assert o1.future_candles == o2.future_candles

    def test_empty_future_reason_present(self) -> None:
        obs = MinimalObservationEngine().observe(_candidate(), [])
        assert isinstance(obs.reason, str)
        assert len(obs.reason) > 0


# ============================================================
# H. INSUFFICIENT FUTURE HISTORY REPRESENTED EXPLICITLY
# ============================================================


class TestInsufficientExplicit:
    def test_insufficient_is_explicit_not_an_outcome(self) -> None:
        # No future data must NOT be silently treated as a determinate
        # outcome (e.g. it must not be AVAILABLE).
        obs = MinimalObservationEngine().observe(_candidate(), [])
        assert obs.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert not obs.observation_status.is_available

    def test_insufficient_distinguishable_from_available(self) -> None:
        obs_empty = MinimalObservationEngine().observe(_candidate(), [])
        obs_data = MinimalObservationEngine().observe(_candidate(), _future([101.0]))
        assert obs_empty.observation_status != obs_data.observation_status

    def test_insufficient_observed_for_each_candidate_independently(self) -> None:
        # Two candidates, one with future data and one without, are
        # evaluated independently.
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        o1 = MinimalObservationEngine().observe(
            _candidate(evaluation_time=t1), _future([101.0], start=t1)
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
        o1 = engine.observe(cand, fut)
        o2 = engine.observe(cand, fut)
        assert o1 == o2

    def test_repeated_empty_identical(self) -> None:
        engine = MinimalObservationEngine()
        o1 = engine.observe(_candidate(), [])
        o2 = engine.observe(_candidate(), [])
        assert o1 == o2

    def test_repeated_mixed_filter_identical(self) -> None:
        engine = MinimalObservationEngine()
        candles = _mixed_candles(_EPOCH, n_before=3, n_at=2, n_after=4)
        o1 = engine.observe(_candidate(), candles)
        o2 = engine.observe(_candidate(), candles)
        assert o1 == o2
        assert o1.future_candle_count == 4

    def test_equivalent_candidates_equivalent_observations(self) -> None:
        engine = MinimalObservationEngine()
        fut = _future([101.0])
        o1 = engine.observe(_candidate(instrument="NIFTY"), fut)
        o2 = engine.observe(_candidate(instrument="NIFTY"), fut)
        assert o1 == o2

    def test_no_randomness(self) -> None:
        engine = MinimalObservationEngine()
        results = [
            engine.observe(_candidate(), _future([101.0]))
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
        assert params == ["candidate", "future_candles"]


# ============================================================
# K. NO FORBIDDEN ATTRIBUTES
# ============================================================


class TestNoForbiddenAttributes:
    def test_observation_has_no_trade_geometry(self) -> None:
        obs = MinimalObservationEngine().observe(
            _candidate(), _future([101.0])
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
            _candidate(), _future([101.0])
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
            _candidate(), _future([101.0])
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
        reason="future candles available",
    )


# ============================================================
# L. FORWARD-RETURN MODEL BASICS
# ============================================================


class TestForwardReturnModel:
    def test_frozen_and_slotted(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.__dataclass_params__.frozen
        assert result.__dataclass_params__.slots
        with pytest.raises((AttributeError, Exception)):
            result.reason = "x"  # type: ignore[misc]

    def test_delegates_evaluation_time_to_candidate(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.evaluation_time == _EPOCH

    def test_delegates_instrument_to_candidate(self) -> None:
        obs = _observation(instrument="RELIANCE")
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.instrument == "RELIANCE"

    def test_delegates_timeframes_to_candidate(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.horizon_candles == 3
        assert result.endpoint_price == 103.0

    def test_endpoint_is_close_of_nth_future_candle(self) -> None:
        closes = [101.0, 102.0, 103.0, 104.0, 105.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 1)
        assert result.endpoint_price == 101.0
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 5)
        assert result.endpoint_price == 105.0

    def test_nth_candle_strictly_after_evaluation_time(self) -> None:
        t = datetime(2024, 3, 15, 9, 30, tzinfo=UTC)
        obs = _observation(evaluation_time=t, future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 2)
        assert result.endpoint_price == 102.0
        endpoint_candle = obs.future_candles[1]
        assert endpoint_candle.timestamp > t


# ============================================================
# N. CORRECT REFERENCE PRICE
# ============================================================


class TestReferencePrice:
    def test_reference_price_stored_explicitly(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 200.0, 3)
        assert result.reference_price == 200.0

    def test_reference_price_is_observation_time_price(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 150.0, 3)
        assert result.reference_price == 150.0

    def test_reference_price_used_in_formula(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 200.0, 3)
        expected = (103.0 - 200.0) / 200.0
        assert result.forward_return == pytest.approx(expected)


# ============================================================
# O. CORRECT FORWARD RETURN
# ============================================================


class TestForwardReturn:
    def test_forward_return_formula(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 110.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.forward_return == pytest.approx(0.10)

    def test_forward_return_negative(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 90.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.forward_return == pytest.approx(-0.10)

    def test_forward_return_zero(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 100.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.forward_return == pytest.approx(0.0)

    def test_forward_return_fractional(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.forward_return == pytest.approx(0.03)


# ============================================================
# P. INSUFFICIENT FUTURE CANDLES
# ============================================================


class TestInsufficientFuture:
    def test_insufficient_future_candles(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_no_partial_horizon_return(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_empty_future_candles(self) -> None:
        candidate = _observation().candidate
        empty_obs = SetupObservation(
            candidate=candidate,
            future_candles=(),
            observation_status=ObservationStatus.INSUFFICIENT_DATA,
            reason="no future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(
            empty_obs, 100.0, 1
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
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0

    def test_one_below_boundary_insufficient(self) -> None:
        closes = [101.0, 102.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA

    def test_one_above_boundary_sufficient(self) -> None:
        closes = [101.0, 102.0, 103.0, 104.0]
        obs = _observation(future_closes=closes)
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
            reason="future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
            reason="future candles",
        )
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
        assert result.endpoint_price == 103.0
        assert result.endpoint_price != at_t.close


# ============================================================
# S. CANDIDATE REMAINS UNCHANGED
# ============================================================


class TestCandidateUnchangedForward:
    def test_candidate_retained_by_reference(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        r1 = engine.observe_forward_return(obs, 100.0, 3)
        r2 = engine.observe_forward_return(obs, 100.0, 3)
        assert r1 == r2

    def test_repeated_insufficient_identical(self) -> None:
        engine = ForwardReturnEngine()
        obs = _observation(future_closes=[101.0])
        r1 = engine.observe_forward_return(obs, 100.0, 3)
        r2 = engine.observe_forward_return(obs, 100.0, 3)
        assert r1 == r2

    def test_no_randomness(self) -> None:
        engine = ForwardReturnEngine()
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        results = [
            engine.observe_forward_return(obs, 100.0, 3) for _ in range(10)
        ]
        assert all(r == results[0] for r in results)


# ============================================================
# U. ZERO/INVALID HORIZON HANDLING
# ============================================================


class TestZeroInvalidHorizon:
    def test_zero_horizon_insufficient(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 0)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_negative_horizon_insufficient(self) -> None:
        obs = _observation(future_closes=[101.0, 102.0, 103.0])
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, -1)
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
        assert result.forward_return is None

    def test_zero_horizon_reason_present(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 0)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ============================================================
# V. NO FORBIDDEN TRADING SEMANTICS IN RESULT MODEL
# ============================================================


class TestNoForbiddenSemantics:
    def test_no_trade_geometry(self) -> None:
        obs = _observation()
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        result = ForwardReturnEngine().observe_forward_return(obs, 100.0, 3)
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
        assert params == ["observation", "reference_price", "horizon_candles"]


# ============================================================
# X. COMPOSABLE WITH SetupObservation
# ============================================================


class TestComposableWithObservation:
    def test_composes_with_observation_engine(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0, 104.0, 105.0])
        observation = MinimalObservationEngine().observe(candidate, candles)
        result = ForwardReturnEngine().observe_forward_return(
            observation, 100.0, 3
        )
        assert result.observation_status is ObservationStatus.AVAILABLE
        assert result.endpoint_price == 103.0
        assert result.forward_return == pytest.approx(0.03)

    def test_composition_preserves_candidate(self) -> None:
        candidate = _candidate()
        candles = _future([101.0, 102.0, 103.0])
        observation = MinimalObservationEngine().observe(candidate, candles)
        result = ForwardReturnEngine().observe_forward_return(
            observation, 100.0, 3
        )
        assert result.candidate is candidate

    def test_composition_with_insufficient_observation(self) -> None:
        candidate = _candidate()
        observation = MinimalObservationEngine().observe(candidate, [])
        result = ForwardReturnEngine().observe_forward_return(
            observation, 100.0, 3
        )
        assert result.observation_status is ObservationStatus.INSUFFICIENT_DATA
        assert result.endpoint_price is None
