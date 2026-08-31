"""
Focused tests for Checkpoint 8.3 — research/setup integration with
HistoricalDataConsumer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

import pytest

from engine.data.historical_consumer import HistoricalDataConsumer
from engine.data.setup_research import HistoricalSetupResearchEngine
from engine.models.historical_availability import (
    HistoricalDataAvailabilityResult,
    HistoricalAvailabilityStatus,
)
from engine.models.historical_data import HistoricalDataRequest
from engine.models.ohlcv import OHLCVCandle
from engine.models.setup_research import SetupResearchRequest


class _FakeHistoricalDataConsumer:
    """Minimal fake consumer for testing the integration boundary."""

    def __init__(self, candles: tuple[OHLCVCandle, ...]) -> None:
        self.candles = candles
        self.last_request: HistoricalDataRequest | None = None

    def get_historical_data(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalDataAvailabilityResult:
        self.last_request = request
        return HistoricalDataAvailabilityResult(
            instrument=request.instrument,
            timeframe=request.timeframe,
            request_start=request.start,
            request_end=request.end,
            status=HistoricalAvailabilityStatus.COMPLETE,
            candles=self.candles,
        )


def _make_candle(timestamp: datetime) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )


def test_setup_research_uses_injected_consumer():
    candles = (_make_candle(datetime(2024, 1, 1, tzinfo=UTC)),)
    consumer = _FakeHistoricalDataConsumer(candles)

    engine = _engine_with_consumer(consumer)
    request = SetupResearchRequest(
        instrument="RELIANCE",
        setup_timeframe="15m",
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        end_time=datetime(2024, 1, 2, tzinfo=UTC),
    )

    result = engine._load_series(request)

    assert result == candles
    assert consumer.last_request is not None
    assert consumer.last_request.instrument == "RELIANCE"
    assert consumer.last_request.timeframe == "15m"


def test_setup_research_falls_back_to_corpus_service_when_no_consumer():
    from engine.data.historical_service import HistoricalMarketDataService
    from engine.data.research_corpus import HistoricalResearchCorpusEngine
    from engine.config.research_corpus_config import ResearchCorpusConfig

    service = HistoricalMarketDataService()
    corpus = HistoricalResearchCorpusEngine(
        service, ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="1D"),
    )
    engine = HistoricalSetupResearchEngine(corpus)
    request = SetupResearchRequest(
        instrument="RELIANCE",
        setup_timeframe="15m",
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        end_time=datetime(2024, 1, 2, tzinfo=UTC),
    )

    result = engine._load_series(request)
    assert result is None or result == ()


def test_setup_research_consumer_request_uses_request_bounds():
    candles = (_make_candle(datetime(2024, 6, 1, tzinfo=UTC)),)
    consumer = _FakeHistoricalDataConsumer(candles)

    engine = _engine_with_consumer(consumer)
    request = SetupResearchRequest(
        instrument="TCS",
        setup_timeframe="15m",
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        end_time=datetime(2024, 6, 1, tzinfo=UTC),
    )

    engine._load_series(request)

    assert consumer.last_request is not None
    assert consumer.last_request.start == datetime(2024, 1, 1, tzinfo=UTC)
    assert consumer.last_request.end == datetime(2024, 6, 1, tzinfo=UTC)


def test_setup_research_consumer_uses_default_bounds_when_request_has_none():
    candles = (_make_candle(datetime(2024, 1, 1, tzinfo=UTC)),)
    consumer = _FakeHistoricalDataConsumer(candles)

    engine = _engine_with_consumer(consumer)
    request = SetupResearchRequest(
        instrument="TCS",
        setup_timeframe="15m",
    )

    engine._load_series(request)

    assert consumer.last_request is not None
    assert consumer.last_request.start == datetime.min.replace(tzinfo=UTC)
    assert consumer.last_request.end == datetime.max.replace(tzinfo=UTC)


def test_setup_research_consumer_result_passed_back_as_none_when_no_candles():
    consumer = _FakeHistoricalDataConsumer(())

    engine = _engine_with_consumer(consumer)
    request = SetupResearchRequest(
        instrument="RELIANCE",
        setup_timeframe="15m",
    )

    result = engine._load_series(request)
    assert result == () or result is None


def _engine_with_consumer(consumer: _FakeHistoricalDataConsumer):
    from engine.data.historical_service import HistoricalMarketDataService
    from engine.data.research_corpus import HistoricalResearchCorpusEngine
    from engine.config.research_corpus_config import ResearchCorpusConfig

    service = HistoricalMarketDataService()
    corpus = HistoricalResearchCorpusEngine(
        service, ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="1D"),
    )
    return HistoricalSetupResearchEngine(
        corpus, historical_data_consumer=consumer,
    )
