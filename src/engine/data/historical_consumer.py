"""
Historical Data Consumer contract (Step 8.1).

This module defines the provider-agnostic, domain-facing contract for
requesting historical market data. Higher-level research code depends
on this protocol rather than on any specific service implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from engine.models.historical_availability import HistoricalDataAvailabilityResult
from engine.models.historical_data import HistoricalDataRequest


@runtime_checkable
class HistoricalDataConsumer(Protocol):
    """
    Domain-facing contract for requesting historical market data.
    """

    def get_historical_data(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalDataAvailabilityResult: ...
