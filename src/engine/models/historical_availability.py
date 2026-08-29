"""
Result models for the historical-data availability layer (Checkpoint 7).

The availability layer answers "does the persisted corpus already satisfy
this historical request, and if not, can the missing monthly chunks be
acquired through the existing ingestion pipeline?". These models are the
DOMAIN-ORIENTED result contract of that layer:

* They describe the OUTCOME of an availability check / acquisition run in
  terms of the existing corpus vocabulary (covered / acquired / missing
  chunks, the existing :class:`HistoricalIngestionStatus`) — never in
  Upstox / provider terms.
* They carry the canonical :class:`OHLCVCandle` series returned to the
  caller (the existing canonical representation — raw provider JSON and
  provider-specific candle structures never surface here).
* They expose NO credentials, NO Authorization headers, NO provider
  secrets and no environment contents.
* Optional fields use ``None`` / ``0`` so "unavailable" / "not
  attempted" is never silently a real value, and ``__post_init__``
  enforces the count/vocabulary invariants so the caller always gets a
  structurally consistent picture.

NO trading / scoring / prediction / decision / geometry / evidence logic
lives here. Frozen + slots everywhere (the repository model convention).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.historical_data import HistoricalIngestionStatus
from engine.models.ohlcv import OHLCVCandle


class HistoricalAvailabilityStatus(Enum):
    """
    Outcome of ONE availability check / acquisition run.

    COMPLETE
        Every requested monthly chunk is already covered by the persisted
        corpus; NO acquisition was needed and NO chunk remains missing.

    ACQUIRED
        Some (or all) missing chunks were acquired and persisted; after a
        re-check every requested chunk is covered. No chunk remains
        missing.

    INCOMPLETE
        At least one chunk could not be acquired. Successfully acquired
        chunks ARE persisted (resumable on the next call); the missing
        chunks are listed explicitly. The requested dataset is NOT
        claimed complete.

    UNSUPPORTED_INSTRUMENT
        The requested instrument is not in the research universe (the
        existing ``UNSUPPORTED_INSTRUMENT`` validation verdict).

    UNSUPPORTED_TIMEFRAME
        The requested timeframe is not a supported canonical timeframe
        (the existing ``UNSUPPORTED_TIMEFRAME`` validation verdict).

    UNPLANNED_TIMEFRAME
        The requested timeframe is a supported canonical timeframe but is
        NOT part of the configured corpus-plan timeframes (the planner is
        the single source of the chunk grid the acquisition runs against),
        so the request cannot be planned. No provider request is made.

    INVALID_REQUEST
        The request itself was invalid (naive timestamps, reversed /
        empty window, unknown provider, ...) — reported honestly, never
        coerced into a usable outcome.

    CREDENTIAL_MISSING
        Acquisition was required through ``upstox-historical`` but the
        ``UPSTOX_ANALYTICS_TOKEN`` environment variable is missing /
        empty. The service fails BEFORE making any provider / network
        request. ``UPSTOX_ACCESS_TOKEN`` is never used as a fallback.

    NO_STORE_LOCAL_DATA
        The service was constructed without a persisted store (a
        read/coverage check cannot run). The result still names the
        chunks the caller asked for (the planner's availability
        vocabulary), but no local data is available.

    NO_ACQUISITION_PATH
        Acquisition was required but no provider / service pipeline is
        available (no injected acquisition engine). The chunks that would
        need to be acquired are listed explicitly.

    NO_STORE_ACQUISITION
        Acquisition was required but the service has no persisted store,
        so successful acquisition could never become durable. No provider
        request was made.

    ERROR
        A runtime failure occurred while evaluating / acquiring the
        dataset (a store failure, an unexpected exception). A single
        per-chunk acquisition failure does NOT produce this status — it
        produces :attr:`INCOMPLETE` with the failing chunk listed.
    """

    COMPLETE = "COMPLETE"
    ACQUIRED = "ACQUIRED"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    UNPLANNED_TIMEFRAME = "UNPLANNED_TIMEFRAME"
    INVALID_REQUEST = "INVALID_REQUEST"
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
    NO_STORE_LOCAL_DATA = "NO_STORE_LOCAL_DATA"
    NO_ACQUISITION_PATH = "NO_ACQUISITION_PATH"
    NO_STORE_ACQUISITION = "NO_STORE_ACQUISITION"
    ERROR = "ERROR"

    # ------------------------------------------------------------
    # CONVENIENCE PROPERTIES
    # ------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """The dataset can be served from the persisted corpus."""

        return self in (HistoricalAvailabilityStatus.COMPLETE, HistoricalAvailabilityStatus.ACQUIRED)

    @property
    def is_success(self) -> bool:
        """The request was satisfied (a canonical dataset is returned)."""

        return self.is_available

    @property
    def requires_acquisition(self) -> bool:
        """True when the request could not be served from local data alone."""

        return self in (
            HistoricalAvailabilityStatus.ACQUIRED,
            HistoricalAvailabilityStatus.INCOMPLETE,
            HistoricalAvailabilityStatus.CREDENTIAL_MISSING,
            HistoricalAvailabilityStatus.NO_ACQUISITION_PATH,
            HistoricalAvailabilityStatus.NO_STORE_ACQUISITION,
        )


@dataclass(frozen=True, slots=True)
class AcquisitionFailure:
    """
    ONE failed acquisition attempt (a missing monthly chunk that could
    not be acquired). Never becomes ``covered``.

    Attributes:

    instrument / timeframe / start / end
        The chunk identity (a single monthly chunk of the requested
        window).

    reason
        The safe, redacted reason for the failure. NEVER carries a
        credential value, a token, an Authorization header or provider
        secrets.
    """

    instrument: str
    timeframe: str
    start: datetime
    end: datetime
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string.")
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        for name, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime.")
            if value.tzinfo is None:
                raise ValueError(
                    f"{name} must be timezone-aware (naive timestamps are "
                    "never silently accepted).",
                )
        if not self.start < self.end:
            raise ValueError("'start' must be strictly before 'end'.")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string.")


_NON_ACQUIRING_STATUSES = (
    HistoricalAvailabilityStatus.COMPLETE,
    HistoricalAvailabilityStatus.UNSUPPORTED_INSTRUMENT,
    HistoricalAvailabilityStatus.UNSUPPORTED_TIMEFRAME,
    HistoricalAvailabilityStatus.UNPLANNED_TIMEFRAME,
    HistoricalAvailabilityStatus.INVALID_REQUEST,
    HistoricalAvailabilityStatus.NO_STORE_LOCAL_DATA,
    HistoricalAvailabilityStatus.ERROR,
)


@dataclass(frozen=True, slots=True)
class HistoricalDataAvailabilityResult:
    """
    The result of one availability check / acquisition run.

    The required contract (Checkpoint 7 §12) is covered explicitly:

    * requested instrument / timeframe / start / end
    * whether the data was already available (``status`` +
      ``was_already_available``)
    * whether acquisition occurred (``acquisition_attempted``)
    * chunks already covered / acquired / still missing
      (``chunks_covered`` / ``chunks_acquired`` /
      ``chunks_still_missing``)
    * acquisition failures, if any (``failures``)
    * number of candles returned (``candle_count``)
    * the canonical candles themselves (``candles``)

    Invariants:

    * ``candles`` are canonical :class:`OHLCVCandle` objects,
      chronologically ordered, and ONLY from the requested window —
      validated by the existing canonical validation pipeline (no future
      data can leak in).
    * ``chunks_covered`` is the FINAL covered-count AFTER any
      acquisition (so a freshly acquired chunk is counted covered and
      the invariant ``chunks_covered + len(chunks_still_missing) ==
      chunks_required`` holds deterministically).
    * ``chunks_acquired`` counts only chunks that were newly persisted
      during THIS run (``0`` when the data was already available).
    * ``failures`` never overlap ``chunks_acquired`` (a failed chunk is
      never reported as acquired) and every failed chunk is also listed
      in ``chunks_still_missing``.
    * ``reference_now`` is the deterministic boundary enforced for the
      request (future-dated candles can never be returned).
    """

    instrument: str
    timeframe: str
    request_start: datetime
    request_end: datetime
    status: HistoricalAvailabilityStatus
    chunks_required: int = 0
    chunks_covered: int = 0
    chunks_acquired: int = 0
    chunks_skipped: int = 0
    chunks_still_missing: tuple[str, ...] = ()
    acquired_chunk_keys: tuple[str, ...] = ()
    failures: tuple[AcquisitionFailure, ...] = ()
    candles: tuple[OHLCVCandle, ...] = ()
    reference_now: datetime | None = None
    acquisition_attempted: bool = False
    request: object | None = None

    def __post_init__(self) -> None:
        # An INVALID_REQUEST result is the one legitimately-empty identity
        # (the structure of the request was invalid, so no instrument /
        # timeframe can be projected from it).
        is_invalid = self.status is HistoricalAvailabilityStatus.INVALID_REQUEST
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            if not is_invalid:
                raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            if not is_invalid:
                raise ValueError("timeframe must be a non-empty string.")
        # NOTE: the timeframe is kept as supplied (canonical spellings are
        # lowercase minutes / "1D" — upper-casing would corrupt "15m").
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        for name, value in (
            ("request_start", self.request_start),
            ("request_end", self.request_end),
        ):
            if not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime.")
            if value.tzinfo is None:
                raise ValueError(
                    f"{name} must be timezone-aware (naive timestamps are "
                    "never silently accepted).",
                )
        if not self.request_start < self.request_end:
            raise ValueError(
                "'request_start' must be strictly before 'request_end'.",
            )
        if self.chunks_required < 0:
            raise ValueError("chunks_required must be non-negative.")
        if not 0 <= self.chunks_covered <= self.chunks_required:
            raise ValueError(
                "chunks_covered must be within [0, chunks_required].",
            )
        if self.chunks_acquired < 0:
            raise ValueError("chunks_acquired must be non-negative.")
        if self.chunks_acquired > self.chunks_required:
            raise ValueError(
                "chunks_acquired cannot exceed chunks_required.",
            )
        if self.chunks_skipped < 0:
            raise ValueError("chunks_skipped must be non-negative.")
        if self.chunks_skipped > self.chunks_required:
            raise ValueError(
                "chunks_skipped cannot exceed chunks_required.",
            )
        if self.chunks_acquired + self.chunks_skipped > self.chunks_covered:
            raise ValueError(
                "chunks_acquired + chunks_skipped cannot exceed the final "
                "chunks_covered (they are part of the covered set).",
            )
        # Frozen ordering for the still-missing list.
        object.__setattr__(
            self,
            "chunks_still_missing",
            tuple(sorted(self.chunks_still_missing)),
        )
        if len(self.chunks_still_missing) != self.chunks_required - self.chunks_covered:
            raise ValueError(
                "chunks_still_missing length must equal chunks_required - "
                "chunks_covered (final coverage accounting).",
            )
        # A failure is never also an acquired chunk.
        failed_keys = {
            f"{f.timeframe}:{f.start.isoformat()}:{f.end.isoformat()}"
            for f in self.failures
        }
        acquired_set = set(self.acquired_chunk_keys)
        overlap = failed_keys & acquired_set
        if overlap:
            raise ValueError(
                f"acquisition failures overlap acquired chunks: "
                f"{sorted(overlap)}.",
            )
        # Every failure is listed among the chunks still missing.
        if not failed_keys.issubset(set(self.chunks_still_missing)):
            raise ValueError(
                "every acquisition failure must also be listed in "
                "chunks_still_missing.",
            )

    # ------------------------------------------------------------
    # CONVENIENCE PROPERTIES
    # ------------------------------------------------------------

    @property
    def candle_count(self) -> int:
        """Number of canonical candles returned."""

        return len(self.candles)

    @property
    def acquisition_required(self) -> bool:
        """True when the request was not fully covered by local data."""

        return (
            self.chunks_covered < self.chunks_required
            and self.status.requires_acquisition
        )

    @property
    def was_already_available(self) -> bool:
        """True when the request was served without any acquisition."""

        return (
            self.status is HistoricalAvailabilityStatus.COMPLETE
            and not self.acquisition_attempted
        )

    @property
    def succeeded(self) -> bool:
        """The requested dataset is fully available (canonical candles)."""

        return self.status.is_success and not self.chunks_still_missing

    @property
    def fully_covered(self) -> bool:
        """Coverage accounting reached the requested endpoint."""

        return (
            self.chunks_required > 0
            and self.chunks_still_missing == ()
            and self.status.is_available
        )


__all__ = [
    "AcquisitionFailure",
    "HistoricalAvailabilityStatus",
    "HistoricalDataAvailabilityResult",
]