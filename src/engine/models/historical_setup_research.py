"""
Domain-facing result boundary for historical setup research.

This module defines the minimal contract for historical setup research
outputs produced by the existing research engine.  A conforming result
represents research/setup findings only and must not contain BUY/SELL
decisions, order instructions, position sizing, execution instructions,
paper-trade instructions, or live-trade instructions.

The contract is provider-agnostic: it carries no dependency on Upstox,
HistoricalDataAvailabilityService, HistoricalDataStore,
CorpusIngestionEngine, HTTP, credentials, or persistence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.models.setup_research import (
    SetupEvidence,
    SetupResearchObservation,
    SetupResearchStatus,
)


@runtime_checkable
class HistoricalSetupResearchResult(Protocol):
    """Minimal domain-facing contract for historical setup research results.

    The contract answers: "What did the historical-data analysis find?"
    It must NOT answer: "Should the engine place a trade?"

    Implementations must be frozen, deterministic, and descriptive only.
    """

    @property
    def research_id(self) -> str:
        """Stable identifier for this research result."""
        ...

    @property
    def status(self) -> SetupResearchStatus:
        """Overall status of the research run."""
        ...

    @property
    def has_occurrences(self) -> bool:
        """Whether the result contains any researched occurrences."""
        ...

    @property
    def is_researched(self) -> bool:
        """Whether the research produced evaluated observations."""
        ...

    @property
    def observations(self) -> tuple[SetupResearchObservation, ...]:
        """Researched historical observations (occurrence + realized outcome)."""
        ...

    @property
    def evidence(self) -> SetupEvidence | None:
        """Aggregated descriptive evidence over all observations."""
        ...

    @property
    def grouped_evidence(self) -> tuple[SetupEvidence, ...]:
        """Per-dimension grouped evidence records."""
        ...

    @property
    def rationale(self) -> str:
        """Human-readable explanation of the research outcome."""
        ...

    @property
    def limitations(self) -> tuple[str, ...]:
        """Fixed descriptive limitations of this research result."""
        ...


__all__ = ["HistoricalSetupResearchResult"]
