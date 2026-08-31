"""
Focused tests for the HistoricalDataConsumer contract (Step 8.1).
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Iterable
from datetime import datetime

import pytest

from engine.data.historical_consumer import HistoricalDataConsumer
from engine.models.historical_availability import HistoricalDataAvailabilityResult
from engine.models.historical_data import HistoricalDataRequest


def test_historical_data_consumer_is_importable():
    assert HistoricalDataConsumer is not None


def test_historical_data_consumer_has_get_historical_data_method():
    assert hasattr(HistoricalDataConsumer, "get_historical_data")


def test_historical_data_consumer_method_signature():
    sig = inspect.signature(HistoricalDataConsumer.get_historical_data)
    params = list(sig.parameters.keys())
    assert params == ["self", "request", "reference_now", "label", "metadata"]
    hints = typing.get_type_hints(HistoricalDataConsumer.get_historical_data)
    assert hints["request"] is HistoricalDataRequest
    assert hints["label"] is str
    assert hints["return"] is HistoricalDataAvailabilityResult
    assert hints["reference_now"] is not None
    assert hints["metadata"] is not None


def test_historical_data_consumer_can_be_implemented():
    class ConcreteConsumer:
        def get_historical_data(
            self,
            request: HistoricalDataRequest,
            *,
            reference_now: datetime | None = None,
            label: str = "",
            metadata: Iterable[tuple[str, str]] | None = None,
        ) -> HistoricalDataAvailabilityResult:
            raise NotImplementedError

    consumer = ConcreteConsumer()
    assert hasattr(consumer, "get_historical_data")
    assert callable(consumer.get_historical_data)
