"""
Focused tests for Checkpoint 8.4 — consumer candles reaching the
setup-research analysis path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

import pytest

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.data.historical_provider import InMemoryHistoricalProvider
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.research_corpus import HistoricalResearchCorpusEngine
from engine.data.setup_research import HistoricalSetupResearchEngine
from engine.models.historical_availability import HistoricalDataAvailabilityResult
from engine.models.historical_availability import HistoricalAvailabilityStatus
from engine.models.historical_data import HistoricalDataRequest
from engine.models.historical_outcome import OutcomeStatus
from engine.models.ohlcv import OHLCVCandle
from engine.models.setup_research import SetupResearchRequest

from tests.test_historical_setup_research import (
    BASE,
    NOW,
    STEP,
    _candle,
    _daily_series,
    _service,
    _trending_series,
)


class _FakeConsumer:
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


def _request(**overrides) -> SetupResearchRequest:
    kwargs = {
        "instrument": "NIFTY",
        "setup_timeframe": "15m",
        "minimum_history": 5,
        "forward_horizon": 10,
    }
    kwargs.update(overrides)
    return SetupResearchRequest(**kwargs)


def test_consumer_candles_affect_outcome_target_hit(tmp_path):
    setup = _trending_series()
    service = _service(tmp_path, setup, _daily_series())
    corpus = HistoricalResearchCorpusEngine(
        service, ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="1D"),
    )

    base_engine = HistoricalSetupResearchEngine(corpus)
    base_result = base_engine.research(_request())
    occurrence = next(
        o for o in base_result.observations if o.occurrence.geometry_available
    )

    entry = occurrence.occurrence.entry
    target = occurrence.occurrence.target
    forward_candles = (
        OHLCVCandle(
            timestamp=occurrence.evaluation_time + STEP,
            open=entry,
            high=target + 5,
            low=entry - 5,
            close=target,
            volume=1000,
        ),
    )
    consumer = _FakeConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(corpus, historical_data_consumer=consumer)
    result = engine.research(_request())

    outcome = next(
        o for o in result.observations
        if o.evaluation_time == occurrence.evaluation_time
    )
    assert outcome.outcome_status is OutcomeStatus.TARGET_HIT


def test_consumer_candles_affect_outcome_stop_hit(tmp_path):
    setup = _trending_series()
    service = _service(tmp_path, setup, _daily_series())
    corpus = HistoricalResearchCorpusEngine(
        service, ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="1D"),
    )

    base_engine = HistoricalSetupResearchEngine(corpus)
    base_result = base_engine.research(_request())
    occurrence = next(
        o for o in base_result.observations if o.occurrence.geometry_available
    )

    entry = occurrence.occurrence.entry
    stop = occurrence.occurrence.stop
    target = occurrence.occurrence.target
    forward_candles = (
        OHLCVCandle(
            timestamp=occurrence.evaluation_time + STEP,
            open=125.0,
            high=132.0,
            low=110.0,
            close=115.0,
            volume=1000,
        ),
    )
    consumer = _FakeConsumer(forward_candles)

    engine = HistoricalSetupResearchEngine(corpus, historical_data_consumer=consumer)
    result = engine.research(_request())

    outcome = next(
        o for o in result.observations
        if o.evaluation_time == occurrence.evaluation_time
    )
    assert outcome.outcome_status is OutcomeStatus.STOP_HIT


def test_consumer_request_matches_setup_research_request(tmp_path):
    setup = _trending_series()
    service = _service(tmp_path, setup, _daily_series())
    corpus = HistoricalResearchCorpusEngine(
        service, ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="1D"),
    )

    request = _request(
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        end_time=datetime(2024, 6, 1, tzinfo=UTC),
    )
    consumer = _FakeConsumer(())

    engine = HistoricalSetupResearchEngine(corpus, historical_data_consumer=consumer)
    engine.research(request)

    assert consumer.last_request is not None
    assert consumer.last_request.instrument == "NIFTY"
    assert consumer.last_request.timeframe == "15m"
    assert consumer.last_request.start == datetime(2024, 1, 1, tzinfo=UTC)
    assert consumer.last_request.end == datetime(2024, 6, 1, tzinfo=UTC)
