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
    SetupCoverageReport,
    SetupDiscoveryResult,
)
from engine.models.market_context import MarketTrendState
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
            if not point.status.is_usable:
                candidates.append(
                    HistoricalSetupCandidate(
                        instrument=point.instrument,
                        evaluation_time=point.evaluation_time,
                        setup_timeframe=point.setup_timeframe,
                        context_timeframe=point.context_timeframe,
                        history_count=point.history_count,
                        status=point.status.value,
                        has_structure=False,
                        is_candidate=False,
                        reason=f"skipped: {point.status.value}",
                    ),
                )
                continue

            setup_ctx = (
                point.state.setup_context
                if point.state is not None
                else None
            )
            if setup_ctx is None:
                candidates.append(
                    HistoricalSetupCandidate(
                        instrument=point.instrument,
                        evaluation_time=point.evaluation_time,
                        setup_timeframe=point.setup_timeframe,
                        context_timeframe=point.context_timeframe,
                        history_count=point.history_count,
                        status=point.status.value,
                        has_structure=False,
                        is_candidate=False,
                        reason="no market structure",
                    ),
                )
                continue

            trend_state = setup_ctx.trend.state
            is_directional = trend_state in (
                MarketTrendState.BULLISH,
                MarketTrendState.BEARISH,
            )
            structure_intact = setup_ctx.trend.structure_intact
            confirmed_swings = setup_ctx.confirmed_swings

            if (
                is_directional
                and structure_intact
                and confirmed_swings >= 2
            ):
                is_candidate = True
                reason = (
                    f"directional structure present and intact "
                    f"({trend_state.value}, structure_intact, "
                    f"{confirmed_swings} confirmed swings)"
                )
            else:
                is_candidate = False
                parts = []
                if not is_directional:
                    parts.append(
                        f"non-directional trend ({trend_state.value})"
                    )
                if not structure_intact:
                    parts.append("structure broken")
                if confirmed_swings < 2:
                    parts.append(
                        f"insufficient confirmed swings ({confirmed_swings})"
                    )
                reason = "; ".join(parts)

            candidates.append(
                HistoricalSetupCandidate(
                    instrument=point.instrument,
                    evaluation_time=point.evaluation_time,
                    setup_timeframe=point.setup_timeframe,
                    context_timeframe=point.context_timeframe,
                    history_count=point.history_count,
                    status=point.status.value,
                    has_structure=True,
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
    "measure_setup_coverage",
]


def measure_setup_coverage(
    points: Sequence[CorpusEvaluationPoint],
    *,
    instrument: str = "",
) -> SetupCoverageReport:
    """
    Measure how many corpus evaluation points satisfy the directional-
    structure setup criterion at each stage.

    This is a RESEARCH-ONLY diagnostic. It consumes already-sliced
    :class:`CorpusEvaluationPoint` objects and performs NO data
    acquisition, NO candle slicing, NO future-data access and NO
    trading logic.

    Stages (in order):

    1. ``total_points`` — every input point.
    2. ``valid_points`` — points with a usable status.
    3. ``points_with_setup_context`` — VALID points carrying a
       reconstructed ``setup_context``.
    4. ``points_with_directional_trend`` — points whose setup trend is
       ``BULLISH`` or ``BEARISH``.
    5. ``points_with_intact_structure`` — points with
       ``structure_intact == True``.
    6. ``points_with_sufficient_swings`` — points with
       ``confirmed_swings >= 2``.
    7. ``final_candidates`` — points satisfying ALL conditions.

    The returned report also carries ``exclusion_reasons`` describing
    why points failed the final stage.
    """

    total = len(points)
    valid = 0
    has_context = 0
    directional = 0
    intact = 0
    sufficient_swings = 0
    candidates = 0
    exclusion_reasons: dict[str, int] = {}

    for point in points:
        if not point.status.is_usable:
            continue
        valid += 1

        setup_ctx = (
            point.state.setup_context
            if point.state is not None
            else None
        )
        if setup_ctx is None:
            exclusion_reasons["no market structure"] = (
                exclusion_reasons.get("no market structure", 0) + 1
            )
            continue
        has_context += 1

        trend_state = setup_ctx.trend.state
        is_directional = trend_state in (
            MarketTrendState.BULLISH,
            MarketTrendState.BEARISH,
        )
        if not is_directional:
            exclusion_reasons[
                f"non-directional trend ({trend_state.value})"
            ] = (
                exclusion_reasons.get(
                    f"non-directional trend ({trend_state.value})", 0
                )
                + 1
            )
            continue
        directional += 1

        if not setup_ctx.trend.structure_intact:
            exclusion_reasons["structure broken"] = (
                exclusion_reasons.get("structure broken", 0) + 1
            )
            continue
        intact += 1

        if setup_ctx.confirmed_swings < 2:
            exclusion_reasons[
                f"insufficient confirmed swings "
                f"({setup_ctx.confirmed_swings})"
            ] = (
                exclusion_reasons.get(
                    f"insufficient confirmed swings "
                    f"({setup_ctx.confirmed_swings})",
                    0,
                )
                + 1
            )
            continue
        sufficient_swings += 1
        candidates += 1

    return SetupCoverageReport(
        instrument=instrument or (points[0].instrument if points else ""),
        total_points=total,
        valid_points=valid,
        points_with_setup_context=has_context,
        points_with_directional_trend=directional,
        points_with_intact_structure=intact,
        points_with_sufficient_swings=sufficient_swings,
        final_candidates=candidates,
        exclusion_reasons=tuple(
            sorted(exclusion_reasons.items())
        ),
    )
