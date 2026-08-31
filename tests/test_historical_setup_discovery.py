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
    measure_setup_coverage,
)
from engine.models.historical_data import (
    HistoricalDataError,
    HistoricalDataIssue,
    HistoricalDataRequest,
)
from engine.models.historical_setup_discovery import (
    HistoricalSetupCandidate,
    SetupCoverageReport,
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


def _market_context(
    *,
    confirmed_swings: int = 2,
    trend_state: MarketTrendState = MarketTrendState.BULLISH,
    structure_intact: bool = True,
) -> MarketContext:
    return MarketContext(
        index=0,
        trend=MarketTrend(
            state=trend_state,
            bias=StructureBias.BULLISH,
            structure_intact=structure_intact,
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
        confirmed_swings=confirmed_swings,
    )


def _state_with_structure(
    ts: datetime,
    candles: Sequence[OHLCVCandle],
    *,
    confirmed_swings: int = 2,
) -> HistoricalMarketState:
    return HistoricalMarketState(
        instrument="NIFTY",
        evaluation_time=ts,
        setup_timeframe="15m",
        context_timeframe="1D",
        setup_slice=_slice(candles, ts, inclusive=True),
        context_slice=_slice([], ts, inclusive=False),
        setup_context=_market_context(confirmed_swings=confirmed_swings),
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
    confirmed_swings: int = 2,
) -> CorpusEvaluationPoint:
    state = (
        _state_with_structure(ts, candles, confirmed_swings=confirmed_swings)
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
        assert c.reason == (
            "directional structure present and intact "
            "(BULLISH, structure_intact, 2 confirmed swings)"
        )

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
        assert c.reason == "no market structure"

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


class TestCandidateContract:
    """The candidate contract contains ONLY observation-time information."""

    def test_candidate_fields_are_observation_time_only(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        expected_fields = {
            "instrument",
            "evaluation_time",
            "setup_timeframe",
            "context_timeframe",
            "history_count",
            "status",
            "has_structure",
            "is_candidate",
            "reason",
        }
        assert expected_fields == set(c.__dataclass_fields__.keys())

    def test_candidate_has_no_forbidden_attributes(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        forbidden = (
            "future_return",
            "future_candles",
            "target_result",
            "stop_result",
            "win_loss",
            "profitability",
            "decision",
            "execution",
            "confidence",
            "quality_score",
            "outcome",
            "evidence",
            "aggregated_evidence",
            "score",
            "weight",
            "probability",
            "expected_return",
        )
        for attr in forbidden:
            assert not hasattr(c, attr), f"candidate must not have {attr}"

    def test_candidate_is_frozen_and_slotted(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.__dataclass_params__.frozen
        assert c.__dataclass_params__.slots

    def test_candidate_values_match_corpus_point_at_observation_time(
        self,
    ) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, history_count=7
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.instrument == point.instrument
        assert c.evaluation_time == point.evaluation_time
        assert c.setup_timeframe == point.setup_timeframe
        assert c.context_timeframe == point.context_timeframe
        assert c.history_count == point.history_count
        assert c.status == point.status.value
        assert c.has_structure == point.state.has_structure
        assert c.is_candidate is True
        assert c.reason == (
            "directional structure present and intact "
            "(BULLISH, structure_intact, 2 confirmed swings)"
        )

    def test_candidate_deterministic_from_same_point(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(ts, candles, has_structure=True)

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([point])
        r2 = engine.discover([point])

        assert r1.candidates[0] == r2.candidates[0]
        assert r1.candidates[0] is not r2.candidates[0]

    def test_skipped_point_candidate_has_no_future_outcome(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        point = _skipped_point(ts, CorpusPointStatus.MISSING_DATA)

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is False
        assert c.has_structure is False
        assert c.status == "MISSING_DATA"
        assert "skipped" in c.reason
        assert c.history_count == 0
        assert not hasattr(c, "outcome")
        assert not hasattr(c, "win")
        assert not hasattr(c, "loss")


class TestSetupCriterion:
    """The directional-structure criterion controls candidate selection."""

    def test_bullish_structure_intact_two_swings_is_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is True
        assert c.reason == (
            "directional structure present and intact "
            "(BULLISH, structure_intact, 2 confirmed swings)"
        )

    def test_bearish_structure_intact_two_swings_is_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        state = HistoricalMarketState(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            setup_slice=_slice(candles, ts, inclusive=True),
            context_slice=_slice([], ts, inclusive=False),
            setup_context=MarketContext(
                index=0,
                trend=MarketTrend(
                    state=MarketTrendState.BEARISH,
                    bias=StructureBias.BEARISH,
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
                confirmed_swings=2,
            ),
            context_context=None,
            mtf_alignment="UNKNOWN",
            latest_usable_setup_timestamp=candles[-1].timestamp,
            latest_usable_context_timestamp=None,
            structure_unavailable_reasons=(),
        )
        point = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=state,
            history_count=len(candles),
            reason="",
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is True
        assert c.reason == (
            "directional structure present and intact "
            "(BEARISH, structure_intact, 2 confirmed swings)"
        )

    def test_range_trend_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        state = HistoricalMarketState(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            setup_slice=_slice(candles, ts, inclusive=True),
            context_slice=_slice([], ts, inclusive=False),
            setup_context=MarketContext(
                index=0,
                trend=MarketTrend(
                    state=MarketTrendState.RANGE,
                    bias=StructureBias.NEUTRAL,
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
                confirmed_swings=2,
            ),
            context_context=None,
            mtf_alignment="UNKNOWN",
            latest_usable_setup_timestamp=candles[-1].timestamp,
            latest_usable_context_timestamp=None,
            structure_unavailable_reasons=(),
        )
        point = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=state,
            history_count=len(candles),
            reason="",
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is False
        assert "non-directional trend (RANGE)" in c.reason

    def test_broken_structure_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        state = HistoricalMarketState(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            setup_slice=_slice(candles, ts, inclusive=True),
            context_slice=_slice([], ts, inclusive=False),
            setup_context=MarketContext(
                index=0,
                trend=MarketTrend(
                    state=MarketTrendState.BULLISH,
                    bias=StructureBias.BULLISH,
                    structure_intact=False,
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
                confirmed_swings=2,
            ),
            context_context=None,
            mtf_alignment="UNKNOWN",
            latest_usable_setup_timestamp=candles[-1].timestamp,
            latest_usable_context_timestamp=None,
            structure_unavailable_reasons=(),
        )
        point = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=state,
            history_count=len(candles),
            reason="",
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is False
        assert "structure broken" in c.reason

    def test_insufficient_swings_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]

        for swing_count in (0, 1):
            point = _valid_point(
                ts,
                candles,
                has_structure=True,
                confirmed_swings=swing_count,
            )
            engine = MinimalSetupDiscoveryEngine()
            result = engine.discover([point])
            c = result.candidates[0]

            assert c.is_candidate is False
            assert (
                f"insufficient confirmed swings ({swing_count})"
                in c.reason
            )

    def test_two_swings_is_boundary_threshold(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]

        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=1
        )
        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        assert result.candidates[0].is_candidate is False

        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )
        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        assert result.candidates[0].is_candidate is True

    def test_neutral_trend_is_not_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        state = HistoricalMarketState(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            setup_slice=_slice(candles, ts, inclusive=True),
            context_slice=_slice([], ts, inclusive=False),
            setup_context=MarketContext(
                index=0,
                trend=MarketTrend(
                    state=MarketTrendState.NEUTRAL,
                    bias=StructureBias.NEUTRAL,
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
                confirmed_swings=2,
            ),
            context_context=None,
            mtf_alignment="UNKNOWN",
            latest_usable_setup_timestamp=candles[-1].timestamp,
            latest_usable_context_timestamp=None,
            structure_unavailable_reasons=(),
        )
        point = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=state,
            history_count=len(candles),
            reason="",
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is False
        assert "non-directional trend (NEUTRAL)" in c.reason

    def test_skipped_point_remains_non_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        for status in (
            CorpusPointStatus.INSUFFICIENT_HISTORY,
            CorpusPointStatus.MISSING_DATA,
            CorpusPointStatus.DATA_GAP,
            CorpusPointStatus.INVALID,
        ):
            point = _skipped_point(ts, status)
            engine = MinimalSetupDiscoveryEngine()
            result = engine.discover([point])
            c = result.candidates[0]
            assert c.is_candidate is False
            assert "skipped" in c.reason

    def test_criterion_reason_identifies_criterion(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert "directional structure present and intact" in c.reason
        assert "BULLISH" in c.reason
        assert "structure_intact" in c.reason
        assert "confirmed swings" in c.reason

    def test_criterion_does_not_access_future_candles(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover([point])
        c = result.candidates[0]

        assert c.is_candidate is True
        assert not hasattr(c, "future_candles")
        assert not hasattr(c, "future_return")

    def test_criterion_deterministic_output(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        engine = MinimalSetupDiscoveryEngine()
        r1 = engine.discover([point])
        r2 = engine.discover([point])

        assert r1.candidates[0] == r2.candidates[0]
        assert r1.candidates[0].reason == r2.candidates[0].reason


class TestSetupCoverage:
    """Coverage measurement for the directional-structure criterion."""

    def test_empty_points_produces_empty_report(self) -> None:
        report = measure_setup_coverage([])
        assert report.is_empty
        assert report.total_points == 0
        assert report.final_candidates == 0
        assert report.candidate_percentage is None

    def test_stage_counts_single_candidate(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        report = measure_setup_coverage([point])

        assert report.total_points == 1
        assert report.valid_points == 1
        assert report.points_with_setup_context == 1
        assert report.points_with_directional_trend == 1
        assert report.points_with_intact_structure == 1
        assert report.points_with_sufficient_swings == 1
        assert report.final_candidates == 1
        assert report.candidate_percentage == 100.0

    def test_stage_counts_reject_each_condition(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]

        skipped = _skipped_point(ts, CorpusPointStatus.MISSING_DATA)
        no_ctx = _valid_point(
            ts, candles, has_structure=False, confirmed_swings=0
        )
        range_pt = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=HistoricalMarketState(
                instrument="NIFTY",
                evaluation_time=ts,
                setup_timeframe="15m",
                context_timeframe="1D",
                setup_slice=_slice(candles, ts, inclusive=True),
                context_slice=_slice([], ts, inclusive=False),
                setup_context=MarketContext(
                    index=0,
                    trend=MarketTrend(
                        state=MarketTrendState.RANGE,
                        bias=StructureBias.NEUTRAL,
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
                    confirmed_swings=2,
                ),
                context_context=None,
                mtf_alignment="UNKNOWN",
                latest_usable_setup_timestamp=candles[-1].timestamp,
                latest_usable_context_timestamp=None,
                structure_unavailable_reasons=(),
            ),
            history_count=len(candles),
            reason="",
        )
        broken_pt = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=HistoricalMarketState(
                instrument="NIFTY",
                evaluation_time=ts,
                setup_timeframe="15m",
                context_timeframe="1D",
                setup_slice=_slice(candles, ts, inclusive=True),
                context_slice=_slice([], ts, inclusive=False),
                setup_context=MarketContext(
                    index=0,
                    trend=MarketTrend(
                        state=MarketTrendState.BULLISH,
                        bias=StructureBias.BULLISH,
                        structure_intact=False,
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
                    confirmed_swings=2,
                ),
                context_context=None,
                mtf_alignment="UNKNOWN",
                latest_usable_setup_timestamp=candles[-1].timestamp,
                latest_usable_context_timestamp=None,
                structure_unavailable_reasons=(),
            ),
            history_count=len(candles),
            reason="",
        )
        low_swing = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=1
        )

        report = measure_setup_coverage(
            [skipped, no_ctx, range_pt, broken_pt, low_swing]
        )

        assert report.total_points == 5
        assert report.valid_points == 4
        assert report.points_with_setup_context == 3
        assert report.points_with_directional_trend == 2
        assert report.points_with_intact_structure == 1
        assert report.points_with_sufficient_swings == 0
        assert report.final_candidates == 0

    def test_exclusion_reasons_breakdown(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]

        no_ctx = _valid_point(
            ts, candles, has_structure=False, confirmed_swings=0
        )
        range_pt = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=HistoricalMarketState(
                instrument="NIFTY",
                evaluation_time=ts,
                setup_timeframe="15m",
                context_timeframe="1D",
                setup_slice=_slice(candles, ts, inclusive=True),
                context_slice=_slice([], ts, inclusive=False),
                setup_context=MarketContext(
                    index=0,
                    trend=MarketTrend(
                        state=MarketTrendState.RANGE,
                        bias=StructureBias.NEUTRAL,
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
                    confirmed_swings=2,
                ),
                context_context=None,
                mtf_alignment="UNKNOWN",
                latest_usable_setup_timestamp=candles[-1].timestamp,
                latest_usable_context_timestamp=None,
                structure_unavailable_reasons=(),
            ),
            history_count=len(candles),
            reason="",
        )
        broken_pt = CorpusEvaluationPoint(
            instrument="NIFTY",
            evaluation_time=ts,
            setup_timeframe="15m",
            context_timeframe="1D",
            status=CorpusPointStatus.VALID,
            state=HistoricalMarketState(
                instrument="NIFTY",
                evaluation_time=ts,
                setup_timeframe="15m",
                context_timeframe="1D",
                setup_slice=_slice(candles, ts, inclusive=True),
                context_slice=_slice([], ts, inclusive=False),
                setup_context=MarketContext(
                    index=0,
                    trend=MarketTrend(
                        state=MarketTrendState.BULLISH,
                        bias=StructureBias.BULLISH,
                        structure_intact=False,
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
                    confirmed_swings=2,
                ),
                context_context=None,
                mtf_alignment="UNKNOWN",
                latest_usable_setup_timestamp=candles[-1].timestamp,
                latest_usable_context_timestamp=None,
                structure_unavailable_reasons=(),
            ),
            history_count=len(candles),
            reason="",
        )
        low_swing = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=1
        )

        report = measure_setup_coverage(
            [no_ctx, range_pt, broken_pt, low_swing]
        )

        reason_map = dict(report.exclusion_reasons)
        assert reason_map["no market structure"] == 1
        assert reason_map["non-directional trend (RANGE)"] == 1
        assert reason_map["structure broken"] == 1
        assert (
            reason_map["insufficient confirmed swings (1)"] == 1
        )

    def test_coverage_matches_discovery_engine(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        points = [
            _valid_point(ts, candles, has_structure=True, confirmed_swings=2),
            _valid_point(ts, candles, has_structure=True, confirmed_swings=1),
            _valid_point(ts, candles, has_structure=False),
            _skipped_point(ts, CorpusPointStatus.MISSING_DATA),
        ]

        engine = MinimalSetupDiscoveryEngine()
        result = engine.discover(points)
        report = measure_setup_coverage(points)

        assert result.total_evaluated == report.total_points
        assert result.candidate_count == report.final_candidates
        assert report.valid_points == 3
        assert report.points_with_setup_context == 2

    def test_deterministic_repeated_measurement(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        points = [
            _valid_point(ts, candles, has_structure=True, confirmed_swings=2),
            _valid_point(ts, candles, has_structure=True, confirmed_swings=1),
            _skipped_point(ts, CorpusPointStatus.INSUFFICIENT_HISTORY),
        ]

        r1 = measure_setup_coverage(points)
        r2 = measure_setup_coverage(points)

        assert r1 == r2
        assert r1.exclusion_reasons == r2.exclusion_reasons

    def test_no_future_data_accessed(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(ts, 100.0)]
        point = _valid_point(
            ts, candles, has_structure=True, confirmed_swings=2
        )

        report = measure_setup_coverage([point])

        assert report.final_candidates == 1
        assert not hasattr(report, "future_candles")
        assert not hasattr(report, "outcome")

    def test_fixture_derived_corpus_coverage_reported(self) -> None:
        from engine.data.historical_fixtures import historical_candles_by_instrument

        fixture_candles = historical_candles_by_instrument()
        points: list[CorpusEvaluationPoint] = []

        for instrument in ("NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"):
            setup_candles = fixture_candles[instrument]["15M"]
            context_candles = fixture_candles[instrument]["1D"]

            for idx, setup_candle in enumerate(setup_candles):
                evaluation_time = setup_candle.timestamp
                setup_slice = CorpusTimeframeSlice(
                    instrument=instrument,
                    timeframe="15M",
                    candles=tuple(setup_candles[: idx + 1]),
                    evaluation_time=evaluation_time,
                    boundary_inclusive=True,
                    first_timestamp=setup_candles[0].timestamp,
                    last_timestamp=setup_candle.timestamp,
                    count=idx + 1,
                    source_count=len(setup_candles),
                    quality=_quality(
                        source_count=len(setup_candles),
                        window_count=idx + 1,
                        first=setup_candles[0].timestamp,
                        last=setup_candle.timestamp,
                    ),
                )
                context_slice = CorpusTimeframeSlice(
                    instrument=instrument,
                    timeframe="1D",
                    candles=tuple(
                        c
                        for c in context_candles
                        if c.timestamp < evaluation_time
                    ),
                    evaluation_time=evaluation_time,
                    boundary_inclusive=False,
                    first_timestamp=context_candles[0].timestamp
                    if context_candles
                    else None,
                    last_timestamp=context_candles[-1].timestamp
                    if context_candles
                    else None,
                    count=sum(
                        1 for c in context_candles if c.timestamp < evaluation_time
                    ),
                    source_count=len(context_candles),
                    quality=_quality(
                        source_count=len(context_candles),
                        window_count=sum(
                            1 for c in context_candles if c.timestamp < evaluation_time
                        ),
                        first=context_candles[0].timestamp if context_candles else None,
                        last=context_candles[-1].timestamp if context_candles else None,
                    ),
                )

                has_context = len(context_candles) >= 2
                has_structure = idx >= 2

                if not has_context or not has_structure:
                    status = CorpusPointStatus.INSUFFICIENT_HISTORY
                    state = None
                else:
                    status = CorpusPointStatus.VALID
                    state = HistoricalMarketState(
                        instrument=instrument,
                        evaluation_time=evaluation_time,
                        setup_timeframe="15M",
                        context_timeframe="1D",
                        setup_slice=setup_slice,
                        context_slice=context_slice,
                        setup_context=_market_context(
                            confirmed_swings=max(0, idx - 1),
                        ),
                        context_context=None,
                        mtf_alignment="UNKNOWN",
                        latest_usable_setup_timestamp=setup_candle.timestamp,
                        latest_usable_context_timestamp=context_candles[-1].timestamp
                        if context_candles
                        else None,
                        structure_unavailable_reasons=(),
                    )

                points.append(
                    CorpusEvaluationPoint(
                        instrument=instrument,
                        evaluation_time=evaluation_time,
                        setup_timeframe="15M",
                        context_timeframe="1D",
                        status=status,
                        state=state,
                        history_count=idx + 1,
                        reason="",
                    )
                )

        report = measure_setup_coverage(
            points, instrument="fixture-5-instruments",
        )
        assert report.total_points == len(points)
        assert report.final_candidates == sum(
            1 for p in points
            if MinimalSetupDiscoveryEngine().discover([p]).candidates[0].is_candidate
        )
        assert report.candidate_percentage is not None
        assert report.candidate_percentage >= 0.0
        assert report.candidate_percentage <= 100.0
