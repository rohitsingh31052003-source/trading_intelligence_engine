"""
Historical setup discovery boundary (Checkpoint 9.1).

This module defines the research-layer boundary for historical setup
discovery. It sits between the Product Phase 6C historical research
corpus and the Product 6D historical setup research engine.

    Phase 6C Corpus (CorpusEvaluationPoint)
                |
        Checkpoint 9.1 Setup Discovery
                |
        Phase 6D Setup Research (future)

Checkpoint 9.1 is BOUNDARY ONLY:

* It defines the protocol a setup discovery component must implement.
* It provides a minimal deterministic implementation suitable for
  testing and as a placeholder for future detectors.
* It does NOT implement BUY/SELL signals, order generation, execution
  logic, outcome analysis, scoring/ranking, machine learning or a large
  generic setup-detection engine.
* It consumes Product Phase 6C corpus
  :class:`~engine.models.research_corpus.CorpusEvaluationPoint` objects
  (which are themselves derived from historical OHLCV candles through
  the existing historical-data interfaces) and produces structured
  candidate observations.

POINT-IN-TIME: the component operates on already-sliced corpus points.
No future candle leakage is possible because the corpus guarantees
``timestamp <= T`` on the setup timeframe and ``timestamp < T`` on the
context timeframe.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.historical_setup_discovery import MinimalSetupDiscoveryEngine
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from engine.models.historical_setup_discovery import (
    HistoricalSetupCandidate,
    SetupDiscoveryResult,
)
from engine.models.research_corpus import CorpusEvaluationPoint


@runtime_checkable
class HistoricalSetupDiscoveryProtocol(Protocol):
    """
    Domain-facing contract for historical setup discovery.

    A setup discovery component accepts a sequence of corpus evaluation
    points (one instrument, ordered by evaluation time) and returns a
    structured result containing candidate observations. The contract
    guarantees:

    * Deterministic output for the same input sequence.
    * No trading or execution behavior is invoked.
    * No future-candle leakage (the input points are already
      point-in-time slices from the Phase 6C corpus).
    """

    def discover(
        self,
        points: Sequence[CorpusEvaluationPoint],
        *,
        label: str = "",
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> SetupDiscoveryResult: ...


class MinimalSetupDiscoveryEngine:
    """
    Minimal deterministic setup discovery engine (Checkpoint 9.1).

    This engine implements the :class:`HistoricalSetupDiscoveryProtocol`
    with the smallest possible deterministic logic: every VALID corpus
    point that carries a computed market structure (``has_structure``)
    is flagged as a candidate; all other points (INSUFFICIENT_HISTORY,
    MISSING_DATA, DATA_GAP, INVALID, or VALID without structure) are
    marked non-candidates. This is a placeholder implementation — future
    checkpoints can replace the engine with a real setup detector
    without changing the protocol boundary.

    Attributes:
        sample_every
            Trivial sampling interval (default 1 = every point is
            evaluated). A value > 1 skips most points for lightweight
            testing. Never affects determinism.
    """

    def __init__(self, sample_every: int = 1) -> None:
        if sample_every < 1:
            raise ValueError("sample_every must be >= 1.")
        self.sample_every = sample_every

    def discover(
        self,
        points: Sequence[CorpusEvaluationPoint],
        *,
        label: str = "",
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> SetupDiscoveryResult:
        if not points:
            return SetupDiscoveryResult(
                discovery_id=self._make_id("", "", label, metadata),
                instrument="",
                timeframe="",
                candidates=(),
                total_evaluated=0,
                candidate_count=0,
                label=label,
                metadata=self._canonicalize_metadata(metadata),
            )

        instrument = points[0].instrument
        timeframe = points[0].setup_timeframe
        candidates: list[HistoricalSetupCandidate] = []

        for idx, point in enumerate(points):
            if idx % self.sample_every != 0:
                continue
            has_structure = (
                point.state is not None and point.state.has_structure
            )
            is_candidate = has_structure and point.status.is_usable
            if is_candidate:
                reason = "structure available"
            elif not point.status.is_usable:
                reason = f"skipped: {point.status.value}"
            else:
                reason = "no structure"
            candidates.append(
                HistoricalSetupCandidate(
                    instrument=point.instrument,
                    evaluation_time=point.evaluation_time,
                    setup_timeframe=point.setup_timeframe,
                    context_timeframe=point.context_timeframe,
                    history_count=point.history_count,
                    status=point.status.value,
                    has_structure=has_structure,
                    is_candidate=is_candidate,
                    reason=reason,
                ),
            )

        candidate_count = sum(1 for c in candidates if c.is_candidate)
        discovery_id = self._make_id(instrument, timeframe, label, metadata)

        return SetupDiscoveryResult(
            discovery_id=discovery_id,
            instrument=instrument,
            timeframe=timeframe,
            candidates=tuple(candidates),
            total_evaluated=len(points),
            candidate_count=candidate_count,
            label=label,
            metadata=self._canonicalize_metadata(metadata),
        )

    @staticmethod
    def _make_id(
        instrument: str,
        timeframe: str,
        label: str,
        metadata: Sequence[tuple[str, str]] | None,
    ) -> str:
        parts = [instrument, timeframe, label]
        if metadata:
            parts.append(
                ",".join(f"{k}={v}" for k, v in sorted(metadata)),
            )
        raw = "|".join(parts)
        return "discovery-" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _canonicalize_metadata(
        metadata: Sequence[tuple[str, str]] | None,
    ) -> tuple[tuple[str, str], ...]:
        if not metadata:
            return ()
        return tuple(sorted((str(k), str(v)) for k, v in metadata))


__all__ = [
    "HistoricalSetupDiscoveryProtocol",
    "MinimalSetupDiscoveryEngine",
]
