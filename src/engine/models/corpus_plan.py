"""
Corpus-preparation plan models (Checkpoint 3B — Historical Corpus Preparation).

This is the PLANNING layer that sits between the historical provider
extension (Checkpoint 3A) and the Phase 6C research corpus: it computes
the deterministic ingestion / coverage work matrix for a research corpus
(universe × timeframes × monthly window chunks), reports the CURRENT
stored coverage per dataset, and names the exact missing chunk requests
an operator still has to fetch.

It contains NO trading, scoring, prediction, decision, geometry,
evidence or paper-trading logic. The authoritative decision engine, the
existing trade geometry, trade plans, paper-trading operations and the
live Yahoo completed-candle boundary are NEVER touched by this layer.

IMPORTANT: this module is the PLANNER vocabulary only — it never fetches
market data and never talks to a provider. The execution of the plan (a
single request at a time) goes through the EXISTING Phase 6B
``HistoricalMarketDataService`` / ``HistoricalDataStore`` pipeline, so a
planned chunk request is issued exactly like a manually-ingested one.

Optional fields use ``None`` so "unavailable" is never silently a real
value. Frozen + slots everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable


# ============================================================
# STATUS VOCABULARY
# ============================================================


class DatasetCoverageStatus(Enum):
    """
    Coverage of ONE (instrument, timeframe) dataset over the planned
    data window, computed from the EXISTING Phase 6B store.

    MISSING
        No stored dataset (or no stored candles) exists for the dataset.

    EMPTY
        A persisted dataset exists but holds zero candles (an empty
        ingestion was audited; no usable data).

    PARTIAL
        Some stored candles exist and cover a subset of the planned
        monthly chunks, but the full window is NOT covered.

    COMPLETE
        Stored candles cover the full planned window (every planned
        monthly chunk contains at least one stored candle).

    UNAVAILABLE
        No store is configured, so coverage cannot be measured. The
        plan still names the required chunks; they are all treated as
        missing for the request accounting.
    """

    MISSING = "MISSING"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def is_complete(self) -> bool:
        """Only COMPLETE datasets need no further fetching."""

        return self is DatasetCoverageStatus.COMPLETE

    @property
    def is_usable(self) -> bool:
        """Observations exist (usable research data), even if partial."""

        return self in (
            DatasetCoverageStatus.COMPLETE,
            DatasetCoverageStatus.PARTIAL,
        )


# ============================================================
# COVERAGE OF ONE DATASET
# ============================================================


@dataclass(frozen=True, slots=True)
class DatasetCoverage:
    """
    Coverage of ONE (instrument, timeframe) dataset over the planned
    window.

    Chunk keys are deterministic ``"<timeframe>:<chunk-start-iso>:
    <chunk-end-iso>"`` strings so the plan is serializable and
    comparable without datetime equality surprises.

    Invariants:

    * ``required_chunks`` is the number of monthly calendar chunks the
      planned window splits into for this timeframe (> 0).
    * ``0 <= covered_chunks <= required_chunks``.
    * ``missing_chunk_keys`` length == ``required_chunks -
      covered_chunks`` and is sorted.
    * A MISSING / EMPTY / UNAVAILABLE dataset never reports stored
      candles or covered chunks.
    * A COMPLETE dataset must cover every required chunk.
    """

    instrument: str
    timeframe: str
    status: DatasetCoverageStatus
    stored_count: int = 0
    stored_first: str | None = None
    stored_last: str | None = None
    required_chunks: int = 0
    covered_chunks: int = 0
    missing_chunk_keys: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string.")
        if self.required_chunks < 0:
            raise ValueError("required_chunks must be non-negative.")
        if not 0 <= self.covered_chunks <= self.required_chunks:
            raise ValueError(
                "covered_chunks must be within [0, required_chunks].",
            )
        if self.stored_count < 0:
            raise ValueError("stored_count must be non-negative.")
        missing = tuple(sorted(self.missing_chunk_keys))
        if len(missing) != self.required_chunks - self.covered_chunks:
            raise ValueError(
                "missing_chunk_keys length must equal required_chunks - "
                "covered_chunks.",
            )
        object.__setattr__(self, "missing_chunk_keys", missing)
        if self.status in (
            DatasetCoverageStatus.MISSING,
            DatasetCoverageStatus.EMPTY,
        ):
            if self.stored_count != 0 or self.covered_chunks != 0:
                raise ValueError(
                    f"{self.status.value} coverage cannot carry stored "
                    "candles or covered chunks.",
                )
        if self.status is DatasetCoverageStatus.UNAVAILABLE:
            if self.stored_count != 0:
                raise ValueError(
                    "UNAVAILABLE coverage cannot carry stored candles.",
                )
            if self.covered_chunks != 0 and len(missing) == 0:
                raise ValueError(
                    "UNAVAILABLE coverage cannot report covered chunks "
                    "without missing chunks.",
                )
        if self.status is DatasetCoverageStatus.COMPLETE:
            if self.required_chunks <= 0 or self.covered_chunks != self.required_chunks:
                raise ValueError(
                    "COMPLETE coverage must cover every required chunk.",
                )

    @property
    def is_complete(self) -> bool:
        return self.status.is_complete

    @property
    def is_usable(self) -> bool:
        return self.status.is_usable


# ============================================================
# PLAN ROW + THE PLAN
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusPreparationRow:
    """
    One (instrument, timeframe) row of the corpus-preparation plan.

    ``provider_supported`` records whether the SELECTED provider (when a
    provider is supplied at planning time) supports this row — an
    honest capability flag, never a silent skip. ``coverage`` is always
    present (computed from the store when one is configured, else
    ``UNAVAILABLE``).
    """

    instrument: str
    timeframe: str
    provider_supported: bool = True
    coverage: DatasetCoverage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string.")
        if self.coverage is not None:
            if self.coverage.instrument != self.instrument:
                raise ValueError(
                    "coverage instrument must match the row instrument.",
                )
            if self.coverage.timeframe != self.timeframe:
                raise ValueError(
                    "coverage timeframe must match the row timeframe.",
                )


@dataclass(frozen=True, slots=True)
class CorpusPreparationPlan:
    """
    The deterministic corpus-preparation plan.

    ``plan_id = "prep-"+sha256[:16]`` of the canonical identity
    (instruments + timeframes + start + end + provider + label +
    metadata). Same inputs -> same plan id; a different window, universe,
    provider, label or metadata -> a different id. No wall-clock, no
    randomness.

    Request accounting (the "work matrix"): a chunk request is the
    smallest unit of ingestion — ONE (instrument, timeframe) monthly
    chunk. ``required_request_count`` counts every required chunk across
    ALL rows, ``covered_request_count`` the chunk requests already
    satisfied by the store, ``missing_request_count`` the chunk requests
    the operator still has to fetch (never negative; a chunk is counted
    once). Rows whose provider doesn't support them are excluded from
    the request accounting but reported explicitly via
    ``unsupported_count``.
    """

    plan_id: str
    instruments: tuple[str, ...]
    timeframes: tuple[str, ...]
    start: datetime
    end: datetime
    provider: str
    rows: tuple[CorpusPreparationRow, ...]
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.plan_id.startswith("prep-"):
            raise ValueError("plan_id must use the 'prep-' prefix.")
        if not isinstance(self.metadata, tuple):
            object.__setattr__(
                self,
                "metadata",
                tuple(sorted(self.metadata)),
            )

    # ------------------------------------------------------------
    # COVERAGE ACCOUNTING
    # ------------------------------------------------------------

    @property
    def dataset_count(self) -> int:
        """Total planned (instrument, timeframe) datasets."""

        return len(self.rows)

    @property
    def supported_row_count(self) -> int:
        """Rows the selected provider can actually serve."""

        return sum(1 for r in self.rows if r.provider_supported)

    def _count(self, statuses: Iterable[DatasetCoverageStatus]) -> int:
        wanted = set(statuses)
        return sum(
            1
            for r in self.rows
            if r.coverage is not None and r.coverage.status in wanted
        )

    @property
    def complete_count(self) -> int:
        return self._count((DatasetCoverageStatus.COMPLETE,))

    @property
    def partial_count(self) -> int:
        return self._count((DatasetCoverageStatus.PARTIAL,))

    @property
    def missing_count(self) -> int:
        return self._count((DatasetCoverageStatus.MISSING,))

    @property
    def empty_count(self) -> int:
        return self._count((DatasetCoverageStatus.EMPTY,))

    @property
    def unavailable_count(self) -> int:
        return self._count((DatasetCoverageStatus.UNAVAILABLE,))

    @property
    def unsupported_count(self) -> int:
        return self.dataset_count - self.supported_row_count

    # ------------------------------------------------------------
    # REQUEST ACCOUNTING
    # ------------------------------------------------------------

    @property
    def required_request_count(self) -> int:
        """Every required chunk request across provider-supported rows."""

        return sum(
            r.coverage.required_chunks if r.coverage is not None else 0
            for r in self.rows
            if r.provider_supported
        )

    @property
    def covered_request_count(self) -> int:
        """Chunk requests already satisfied by the stored data."""

        return sum(
            r.coverage.covered_chunks if r.coverage is not None else 0
            for r in self.rows
            if r.provider_supported
        )

    @property
    def missing_request_count(self) -> int:
        """Chunk requests the operator still has to fetch (>= 0)."""

        return self.required_request_count - self.covered_request_count

    @property
    def is_empty(self) -> bool:
        """No planned datasets (empty universe / no timeframes)."""

        return self.dataset_count == 0

    @property
    def has_missing(self) -> bool:
        """True when at least one chunk request remains outstanding."""

        return self.missing_request_count > 0

    @property
    def is_fully_covered(self) -> bool:
        """Every planned chunk request is already stored."""

        return (
            self.required_request_count > 0
            and self.missing_request_count == 0
            and self.unsupported_count == 0
        )


__all__ = [
    "CorpusPreparationPlan",
    "CorpusPreparationRow",
    "DatasetCoverage",
    "DatasetCoverageStatus",
]