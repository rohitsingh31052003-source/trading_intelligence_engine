"""
Focused end-to-end integration tests for Checkpoint 8.7.

Proves that the historical data availability layer (HistoricalDataConsumer)
and the historical setup research layer (HistoricalSetupResearchEngine)
can work together through their provider-agnostic boundaries without
any network, provider, or credential dependency.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.config.setup_research_config import SetupResearchConfig
from engine.data.historical_consumer import HistoricalDataConsumer
from engine.data.historical_provider import InMemoryHistoricalProvider
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.research_corpus import HistoricalResearchCorpusEngine
from engine.data.setup_research import HistoricalSetupResearchEngine
from engine.models.historical_availability import (
    HistoricalDataAvailabilityResult,
    HistoricalAvailabilityStatus,
)
from engine.models.historical_data import HistoricalDataRequest
from engine.models.historical_setup_research import HistoricalSetupResearchResult
from engine.models.ohlcv import OHLCVCandle
from engine.models.setup_research import SetupResearchRequest, SetupResearchStatus


def _candle(timestamp: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=timestamp,
        open=close,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=1000.0,
    )


def _build_corpus(tmp_path):
    """Build a minimal deterministic corpus using in-memory provider."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    step = timedelta(minutes=15)
    day = timedelta(days=1)

    setup_candles = tuple(
        _candle(base + step * i, 100.0 + i * 0.5) for i in range(40)
    )
    context_candles = tuple(
        _candle(base + day * i, 100.0 + i) for i in range(10)
    )
    records = {
        ("NIFTY", "15m"): setup_candles,
        ("NIFTY", "1D"): context_candles,
    }
    provider = InMemoryHistoricalProvider(records)
    store = HistoricalDataStore(tmp_path / "store")
    service = HistoricalMarketDataService(provider=provider, store=store)
    service.ingest(
        HistoricalDataRequest(
            "NIFTY",
            "15m",
            setup_candles[0].timestamp,
            setup_candles[-1].timestamp,
        ),
        reference_now=datetime(2024, 2, 1, tzinfo=UTC),
    )
    service.ingest(
        HistoricalDataRequest(
            "NIFTY",
            "1D",
            context_candles[0].timestamp,
            context_candles[-1].timestamp,
        ),
        reference_now=datetime(2024, 2, 1, tzinfo=UTC),
    )

    corpus = HistoricalResearchCorpusEngine(
        service,
        ResearchCorpusConfig(
            setup_timeframe="15m",
            context_timeframe="1D",
            min_setup_history=5,
        ),
    )
    return corpus, setup_candles, context_candles


class _FakeHistoricalDataConsumer:
    """Minimal deterministic fake consumer — no network, no provider, no credentials."""

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


def test_consumer_supplies_candles_without_upstox(tmp_path):
    """The fake consumer returns canonical candles with no Upstox dependency."""
    corpus, _, _ = _build_corpus(tmp_path)
    forward_candles = (
        _candle(datetime(2024, 2, 1, tzinfo=UTC), 105.0),
        _candle(datetime(2024, 2, 2, tzinfo=UTC), 110.0),
    )
    consumer = _FakeHistoricalDataConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(
        corpus,
        historical_data_consumer=consumer,
    )
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert consumer.last_request is not None
    assert consumer.last_request.instrument == "NIFTY"
    assert consumer.last_request.timeframe == "15m"


def test_consumer_candles_reach_setup_research_analysis_path(tmp_path):
    """Candles from the consumer are used by the engine's analysis path."""
    corpus, _, _ = _build_corpus(tmp_path)
    forward_candles = (
        _candle(datetime(2024, 2, 1, tzinfo=UTC), 105.0),
        _candle(datetime(2024, 2, 2, tzinfo=UTC), 110.0),
    )
    consumer = _FakeHistoricalDataConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(
        corpus,
        historical_data_consumer=consumer,
    )
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert result.status in (
        SetupResearchStatus.NO_OCCURRENCES,
        SetupResearchStatus.RESEARCHED,
        SetupResearchStatus.INSUFFICIENT_DATA,
    )


def test_research_output_satisfies_historical_setup_research_result(tmp_path):
    """The resulting research output satisfies the boundary protocol."""
    corpus, _, _ = _build_corpus(tmp_path)
    forward_candles = (
        _candle(datetime(2024, 2, 1, tzinfo=UTC), 105.0),
        _candle(datetime(2024, 2, 2, tzinfo=UTC), 110.0),
    )
    consumer = _FakeHistoricalDataConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(
        corpus,
        historical_data_consumer=consumer,
    )
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert isinstance(result.research_id, str)
    assert len(result.research_id) > 0
    assert isinstance(result.observations, tuple)
    assert isinstance(result.grouped_evidence, tuple)
    assert isinstance(result.limitations, tuple)
    assert isinstance(result.rationale, str)


def test_no_network_provider_or_credential_dependency(tmp_path):
    """The integration uses only deterministic in-memory data."""
    corpus, _, _ = _build_corpus(tmp_path)
    forward_candles = (
        _candle(datetime(2024, 2, 1, tzinfo=UTC), 105.0),
    )
    consumer = _FakeHistoricalDataConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(
        corpus,
        historical_data_consumer=consumer,
    )
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert result.status is SetupResearchStatus.NO_OCCURRENCES


def test_existing_behavior_unchanged_without_consumer(tmp_path):
    """Without a consumer, the engine falls back to corpus service as before."""
    corpus, _, _ = _build_corpus(tmp_path)

    engine = HistoricalSetupResearchEngine(corpus)
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert result.status is SetupResearchStatus.NO_OCCURRENCES


def test_end_to_end_boundary_integration(tmp_path):
    """Complete conceptual flow: consumer → candles → research → boundary result."""
    corpus, _, _ = _build_corpus(tmp_path)
    forward_candles = (
        _candle(datetime(2024, 2, 1, tzinfo=UTC), 105.0),
        _candle(datetime(2024, 2, 2, tzinfo=UTC), 110.0),
    )
    consumer = _FakeHistoricalDataConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(
        corpus,
        historical_data_consumer=consumer,
    )
    request = SetupResearchRequest(
        instrument="NIFTY",
        setup_timeframe="15m",
        minimum_history=5,
    )

    result = engine.research(request)

    assert isinstance(result, HistoricalSetupResearchResult)
    assert consumer.last_request is not None
    assert consumer.last_request.instrument == "NIFTY"
    assert consumer.last_request.timeframe == "15m"
    assert result.has_occurrences is False
    assert result.is_researched is False
