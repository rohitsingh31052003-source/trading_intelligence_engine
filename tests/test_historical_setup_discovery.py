"""
Tests for Checkpoint 9.1 — Historical Setup Discovery Boundary.

Deterministic, network-free: every test constructs corpus evaluation
points directly (no provider, no corpus engine, no pipeline). The
discovery boundary is RESEARCH ONLY — it produces candidate
observations; it does NOT call the decision engine, generate trade
candidates, create paper trades, compute outcomes or evidence.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Sequence

import pytest

from engine.data.historical_setup_discovery import (
    HistoricalSetupDiscoveryProtocol,
    MinimalSetupDiscoveryEngine,
)
from engine.models.historical_data import HistoricalDataError, HistoricalDataIssue
from engine.models.historical_setup_discovery import (
    HistoricalSetupCandidate,
    SetupDiscoveryResult,
)
from engine.models.market_context import (
    MarketContext,
    MarketTrend,
    MarketTrendState,
    PriceLocation,
    RangeContext,
    RangeState,
    SupportResistanceContext,
)
from engine.models.market_structure import StructurePoint
from engine.models.ohlcv import OHLCVCandle
from engine.models.research_corpus import (
    CorpusDataQuality,
    CorpusEvaluationPoint,
    CorpusPointStatus,
    CorpusTimeframeSlice,
    HistoricalMarketState,
)
from engine.models.structure_analysis import StructureBias


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _quality(
    source_count: int = 0,
    window_count: int = 0,
    first: datetime | None = None,
    last: datetime | None = None,
) -> CorpusDataQuality:
    return CorpusDataQuality(
        source_count=source_count,
        window_count=window_count,
        first_timestamp=first,
        last_timestamp=last,
        unexpected_gap_count=0,
        closure_gap_count=0,
        invalid_records=0,
        gaps=(),
        issues=(),
    )


def _slice(
    candles: Sequence[OHLCVCandle],
    evaluation_time: datetime,
    *,
    inclusive: bool = True,
    source_count: int | None = None,
) -> CorpusTimeframeSlice:
    source = source_count if source_count is not None else len(candles)
    return CorpusTimeframeSlice(
        instrument="NIFTY",
        timeframe="15m",
        candles=tuple(candles),
        evaluation_time=evaluation_time,
        boundary_inclusive=inclusive,
        first_timestamp=candles[0].timestamp if candles else None,
        last_timestamp=candles[-1].timestamp if candles else None,
        count=len(candles),
        source_count=source,
        quality=_quality(source_count=source, window_count=len(candles)),
    )


def _market_context() -> MarketContext:
    return MarketContext(
        index=0,
        trend=MarketTrend(
            state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
        ),
        range=RangeContext(
            state=RangeState.NOT_IN_RANGE,
            high=None,
            low=None,
            width=None,
            position=None,
            reason="directional",
        ),
        support_resistance=SupportResistanceContext(
            support=None,
            resistance=None,
            distance_to_support=None,
            distance_to_resistance=None,
            location=PriceLocation.UNKNOWN,
        ),
    )


def _state_with_structure(
    ts: datetime,
    candles: Sequence[OHLCVCandle],
) -> HistoricalMarketState:
    return HistoricalMarketState(
        instrument="NIFTY",
        evaluation_time=ts,
        setup_timeframe="15m",
        context_timeframe="1D",
        setup_slice=_slice(candles, ts, inclusive=True),
        context_slice=_slice([], ts, inclusive=False),
        setup_context=_market_context(),
        context_context=None,
        mtf_alignment="UNKNOWN",
        latest_usable_setup_timestamp=candles[-1].timestamp if candles else None,
        latest_usable_context_timestamp=None,
        structure_unavailable_reasons=(),
    )


def _state_without_structure(
    ts: datetime,
    candles: Sequence[OHLCVCandle],
) -> HistoricalMarketState:
    return HistoricalMarketState(
        instrument="NIFTY",
        evaluation_time=ts,
        setup_timeframe="15m",
        context_timeframe="1D",
        setup_slice=_slice(candles, ts, inclusive=True),
        context_slice=_slice([], ts, inclusive=False),
        setup_context=None,
        context_context=None,
        mtf_alignment="UNKNOWN",
        latest_usable_setup_timestamp=candles[-1].timestamp if candles else None,
        latest_usable_context_timestamp=None,
        structure_unavailable_reasons=(),
    )


def _valid_point(
    ts: datetime,
    candles: Sequence[OHLCVCandle],
    *,
    has_structure: bool = True,
    history_count: int | None = None,
) -> CorpusEvaluationPoint:
    state = (
        _state_with_structure(ts, candles)
        if has_structure
        else _state_without_structure(ts, candles)
    )
    return CorpusEvaluationPoint(
        instrument="NIFTY",
        evaluation_time=ts,
        setup_timeframe="15m",
        context_timeframe="1D",
        status=CorpusPointStatus.VALID,
        state=state,
        history_count=history_count if history_count is not None else len(candles),
        reason="",
    )


def _skipped_point(
    ts: datetime,
    status: CorpusPointStatus,
    history_count: int = 0,
) -> CorpusEvaluationPoint:
    return CorpusEvaluationPoint(
        instrument="NIFTY",
        evaluation_time=ts,
        setup_timeframe="15m",
        context_timeframe="1D",
        status=status,
        state=None,
        history_count=history_count,
        reason=f"skipped: {status.value}",
    )


# ============================================================
# TESTS
# ============================================================


class TestEmptyInput:
    """Empty historical input is handled correctly."""

    def test_empty_points_returns_empty_result(self) -> None:
        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([])
        assert result.is_empty
        assert result.total_evaluated == 0
        assert result.candidate_count == 0
        assert result.candidates == ()
        assert result.instrument == ""
        assert result.timeframe == ""

    def test_empty_points_generates_deterministic_id(self) -> None:
        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([])
        r2 = engine.discover([])
        assert r1.discovery_id == r2.discovery_id
        assert r1.discovery_id.startswith("discovery-")


class TestCandidateDetection:
    """Candidate observations are returned in structured form."""

    def test_single_valid_point_with_structure_is_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])

        assert result.total_evaluated == 1
        assert result.candidate_count == 1
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.is_candidate is True
        assert c.has_structure is True
        assert c.status == "VALID"
        assert c.instrument == "NIFTY"
        assert c.setup_timeframe == "15m"
        assert c.context_timeframe == "1D"
        assert c.history_count == 1
        assert c.reason == "structure available"

    def test_single_valid_point_without_structure_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=False)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])

        assert result.total_evaluated == 1
        assert result.candidate_count == 0
        c = result.candidates[0]
        assert c.is_candidate is False
        assert c.has_structure is False
        assert c.reason == "no structure"

    def test_skipped_point_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        point = _skipped_point(ts, CorpusPointStatus.INSUFFICIENT_HISTORY)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])

        assert result.total_evaluated == 1
        assert result.candidate_count == 0
        c = result.candidates[0]
        assert c.is_candidate is False
        assert c.status == "INSUFFICIENT_HISTORY"
        assert "skipped" in c.reason

    def test_mixed_points_preserve_order(self) -> None:
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 1, 1, 12, 15, tzinfo=UTC)
        t3 = datetime(2024, 1, 1, 12, 30, tzinfo=UTC)

        p1 = _valid_point(t1, [_candle(t1, 100.0)], has_structure=True)
        p2 = _valid_point(t2, [_candle(t2, 101.0)], has_structure=False)
        p3 = _skipped_point(t3, CorpusPointStatus.MISSING_DATA)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([p1, p2, p3])

        assert result.total_evaluated == 3
        assert result.candidate_count == 1
        assert len(result.candidates) == 3
        assert result.candidates[0].evaluation_time == t1
        assert result.candidates[0].is_candidate is True
        assert result.candidates[1].evaluation_time == t2
        assert result.candidates[1].is_candidate is False
        assert result.candidates[2].evaluation_time == t3
        assert result.candidates[2].is_candidate is False

    def test_all_statuses_recorded(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        for status in CorpusPointStatus:
            if status is CorpusPointStatus.VALID:
                point = _valid_point(ts, [_candle(ts, 100.0)], has_structure=False)
            else:
                point = _skipped_point(ts, status)
            engine = MinimalSetupDiscoveryEngine()
            result = engine.discover([point])
            assert result.candidates[0].status == status.value


class TestDeterminism:
    """Discovery is deterministic."""

    def test_same_input_same_output(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([point], label="run1")
        r2 = engine.discover([point], label="run1")
        assert r1 == r2
        assert r1.discovery_id == r2.discovery_id

    def test_different_input_different_id(self) -> None:
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 1, 1, 12, 15, tzinfo=UTC)
        p1 = _valid_point(t1, [_candle(t1, 100.0)], has_structure=True)
        p2 = _valid_point(t2, [_candle(t2, 101.0)], has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([p1], label="run-a")
        r2 = engine.discover([p2], label="run-b")
        assert r1.discovery_id != r2.discovery_id

    def test_label_affects_id(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        point = _valid_point(ts, [_candle(ts, 100.0)], has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([point], label="A")
        r2 = engine.discover([point], label="B")
        assert r1.discovery_id != r2.discovery_id

    def test_metadata_sorted_in_id(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        point = _valid_point(ts, [_candle(ts, 100.0)], has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([point], metadata=[("b", "2"), ("a", "1")])
        r2 = engine.discover([point], metadata=[("a", "1"), ("b", "2")])
        assert r1.discovery_id == r2.discovery_id


class TestSampling:
    """Sample-every parameter affects which points are evaluated."""

    def test_sample_every_2_skips_alternate(self) -> None:
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        t2 = datetime(2024, 1, 1, 12, 15, tzinfo=UTC)
        t3 = datetime(2024, 1, 1, 12, 30, tzinfo=UTC)

        p1 = _valid_point(t1, [_candle(t1, 100.0)], has_structure=True)
        p2 = _valid_point(t2, [_candle(t2, 101.0)], has_structure=True)
        p3 = _valid_point(t3, [_candle(t3, 102.0)], has_structure=True)

        engine = MinimalSetupDiscoveryEngine(sample_every=2)
        result = engine.discover([p1, p2, p3])

        assert result.total_evaluated == 3
        assert len(result.candidates) == 2
        assert result.candidates[0].evaluation_time == t1
        assert result.candidates[1].evaluation_time == t3

    def test_sample_every_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="sample_every must be >= 1"):
            MinimalSetupDiscoveryEngine(sample_every=0)


class TestProtocolCompliance:
    """The engine satisfies the protocol contract."""

    def test_engine_is_protocol_instance(self) -> None:
        engine = MinimalSetupDiscoveryEngine()
        assert isinstance(engine, HistoricalSetupDiscoveryProtocol)

    def test_protocol_method_signature(self) -> None:
        engine = MinimalSetupDiscoveryEngine()
        sig = inspect.signature(engine.discover)
        params = list(sig.parameters.keys())
        assert params == ["points", "label", "metadata"]


class TestNoTradingBehavior:
    """No trading or execution behavior is invoked."""

    def test_output_is_pure_data(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])

        assert isinstance(result, SetupDiscoveryResult)
        assert isinstance(result.candidates, tuple)
        assert isinstance(result.candidates[0], HistoricalSetupCandidate)
        assert result.candidates[0].is_candidate is True

    def test_no_side_effects_on_input(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        engine.discover([point])

        assert point.status == CorpusPointStatus.VALID
        assert point.state is not None
        assert point.state.has_structure is True
