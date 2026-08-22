"""
Domain models for the historical market-data foundation (Product Phase 6A).

These models describe the HISTORICAL DATA FOUNDATION layer: typed
historical-data requests, provider responses, validation issues, gap
reports, provenance and ingestion/store/load results. They are the data
foundation for FUTURE historical setup research and evidence generation.

Product Phase 6A is DATA FOUNDATION ONLY:

* It is NOT a prediction engine, NOT machine learning, NOT a new
  trading strategy, NOT a decision engine, NOT a scoring layer.
* The historical layer never calls the decision engine, never generates
  trade candidates, never creates paper trades and never computes
  historical "evidence" (win rate, average R, profit factor, expected
  return, setup success probability). Those belong to the FUTURE
  Product Phase 6B historical research corpus.
* The EXISTING live path (live/near-live provider -> completed-candle
  boundary -> scanner -> authoritative decision -> geometry -> plan ->
  paper trading) remains authoritative and is untouched by these
  models.

DESIGN PRINCIPLE — no fabricated data:

Invalid records are REJECTED or explicitly reported — never silently
repaired. Missing candles are NEVER synthesized by validation or gap
detection. Empty / partial / invalid / error provider responses are
carried explicitly via :class:`HistoricalIngestionStatus` so downstream
layers surface them honestly.

DESIGN PRINCIPLE — temporal causality (look-ahead protection):

A historical dataset at evaluation timestamp T contains ONLY candles
with ``timestamp <= T``. No model carries a hidden future-candle
parameter. Ingestion rejects future-dated candles (relative to the
caller's reference "now") unless an explicit, controlled import flag is
set by the caller.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.ohlcv import OHLCVCandle


# ============================================================
# STATUS + ERROR VOCABULARY
# ============================================================


class HistoricalIngestionStatus(Enum):
    """
    Explicit outcome status of a historical fetch / ingestion operation.

    AVAILABLE
        The provider returned usable data and every received record was
        accepted after validation (no records rejected).

    EMPTY
        The provider responded but returned no candles (an empty
        response is NOT fabricated into data).

    PARTIAL
        The provider returned data but some records were rejected by
        validation (e.g. duplicates, future-dated candles, naive
        timestamps). Accepted candles are kept; rejected ones are
        reported honestly. The dataset is NOT claimed to be complete.

    INVALID
        The request or the response was invalid (unsupported
        instrument / timeframe, malformed response, every record
        rejected). No dataset is produced from invalid input.

    ERROR
        The provider raised / failed (network, timeout, malformed
        payload, missing optional dependency). The error is reported
        honestly; nothing is fabricated.
    """

    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    ERROR = "ERROR"


class ProviderResponseStatus(Enum):
    """
    Coarse status of a provider's response to a historical request.

    OK          The provider responded with (possibly empty) candles.
    EMPTY       The provider responded but returned no candles.
    ERROR       The provider raised / failed.
    UNSUPPORTED The provider does not support the instrument/timeframe.
    """

    OK = "OK"
    EMPTY = "EMPTY"
    ERROR = "ERROR"
    UNSUPPORTED = "UNSUPPORTED"


class HistoricalDataError(Enum):
    """
    Categorical reason a record / request / response was rejected or
    reported during historical validation. Carried on
    :class:`HistoricalDataIssue` for audit.
    """

    MISSING_INSTRUMENT = "MISSING_INSTRUMENT"
    MISSING_TIMEFRAME = "MISSING_TIMEFRAME"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    INVALID_RANGE = "INVALID_RANGE"
    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    FUTURE_DATED = "FUTURE_DATED"
    MISSING_OHLC_VALUE = "MISSING_OHLC_VALUE"
    INVALID_OHLC = "INVALID_OHLC"
    HIGH_BELOW_LOW = "HIGH_BELOW_LOW"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    UNORDERED = "UNORDERED"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class GapKind(Enum):
    """
    Classification of a detected gap between consecutive candles.

    POSSIBLE_MARKET_CLOSURE
        The gap plausibly corresponds to a market closure (a weekend, a
        short non-trading window or a plausible exchange-holiday span).
        It is reported for transparency but is NOT automatically a
        data-quality failure.

    UNEXPECTED_GAP
        The gap is larger than a plausible market-closure window and
        therefore looks like genuinely missing data. It is reported
        honestly. Missing candles are NEVER fabricated to fill the gap.

    A VALID TRADING SEQUENCE is represented by the ABSENCE of gap
    records (no enum member): a series with no detected gaps is a valid
    sequence.
    """

    POSSIBLE_MARKET_CLOSURE = "POSSIBLE_MARKET_CLOSURE"
    UNEXPECTED_GAP = "UNEXPECTED_GAP"


# ============================================================
# REQUEST MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalDataRequest:
    """
    A typed historical market-data request.

    Attributes:

    instrument
        Canonical instrument name (non-empty after stripping).

    timeframe
        Timeframe label (e.g. ``"15m"``, ``"1D"``). Canonicalization
        against the supported set happens in the data layer; the model
        requires a non-empty label.

    start
        Requested window start (inclusive). MUST be timezone-aware.

    end
        Requested window end (inclusive). MUST be timezone-aware and
        strictly greater than ``start``.

    provider
        Optional explicit provider name. ``""`` = the service default.

    adjusted
        Optional adjusted/raw hint for providers that support it.
        ``None`` = provider default (raw).

    allow_future_end
        When ``False`` (default) the service REJECTS a request whose
        ``end`` is in the future relative to the caller's reference
        "now" — the production ingestion path never requests future
        market data. ``True`` exists ONLY for controlled test / import
        scenarios and must be set deliberately by the caller.

    metadata
        Optional caller metadata (sorted tuple of pairs) carried onto
        provenance for audit.
    """

    instrument: str
    timeframe: str
    start: datetime
    end: datetime
    provider: str = ""
    adjusted: bool | None = None
    allow_future_end: bool = False
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string.")
        for name, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime.")
            if value.tzinfo is None:
                raise ValueError(
                    f"{name} must be timezone-aware (naive timestamps are "
                    "never silently accepted).",
                )
        if not self.start < self.end:
            raise ValueError("start must be strictly before end.")
        if not isinstance(self.metadata, tuple):
            object.__setattr__(
                self, "metadata", tuple(sorted(self.metadata)),
            )


# ============================================================
# VALIDATION ISSUE + GAP
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalDataIssue:
    """
    One auditable historical-data validation issue.

    Attributes:

    error
        The :class:`HistoricalDataError` categorizing the issue.

    reason
        Human-readable, descriptive explanation.

    instrument / timeframe
        Identity the issue pertains to (``""`` when unknown).

    timestamp
        The offending candle timestamp when applicable, else ``None``.
    """

    error: HistoricalDataError
    reason: str
    instrument: str = ""
    timeframe: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class HistoricalGap:
    """
    One detected gap between two consecutive candles.

    Attributes:

    kind
        :class:`GapKind` classification.

    previous_timestamp
        Timestamp of the last candle before the gap.

    next_timestamp
        Timestamp of the first candle after the gap.

    missing_count
        Number of expected candles missing in the gap (derived from the
        timeframe step). Never fabricated — an estimate used for
        reporting only.

    span_seconds
        The gap span in seconds (next - previous).

    reason
        Descriptive explanation.
    """

    kind: GapKind
    previous_timestamp: datetime
    next_timestamp: datetime
    missing_count: int
    span_seconds: float
    reason: str = ""


# ============================================================
# PROVENANCE
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalProvenance:
    """
    Provenance metadata for ONE historical fetch / ingestion operation.

    Every dataset import retains this so a stored dataset can be traced
    back to its source request. Data completeness is never claimed when
    the provider returned incomplete data (``status`` carries that).

    Attributes:

    provider
        Provider name that served the request (or the attempted one).

    instrument / timeframe
        Requested identity (canonicalized).

    requested_start / requested_end
        The requested window.

    actual_first_candle / actual_last_candle
        The first / last ACCEPTED candle timestamps, or ``None`` when
        no candles were accepted.

    ingestion_timestamp
        When the operation ran (caller-injected for determinism).

    records_received / records_accepted / records_rejected
        Record accounting. ``received == accepted + rejected``.

    status
        :class:`HistoricalIngestionStatus` of the operation.

    reason
        Descriptive reason (empty on full success).
    """

    provider: str
    instrument: str
    timeframe: str
    requested_start: datetime
    requested_end: datetime
    actual_first_candle: datetime | None
    actual_last_candle: datetime | None
    ingestion_timestamp: datetime
    records_received: int
    records_accepted: int
    records_rejected: int
    status: HistoricalIngestionStatus
    reason: str = ""

    def __post_init__(self) -> None:
        for name in ("records_received", "records_accepted", "records_rejected"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if self.records_received != self.records_accepted + self.records_rejected:
            raise ValueError(
                "records_received must equal records_accepted + "
                "records_rejected.",
            )


# ============================================================
# RESULT MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalFetchResult:
    """
    The normalized result of fetching + validating a historical request.

    Attributes:

    request
        The originating :class:`HistoricalDataRequest`.

    status
        :class:`HistoricalIngestionStatus`.

    candles
        Accepted, UTC-normalized, chronologically ordered, de-duplicated
        candles. Empty when none were accepted.

    issues
        Every validation issue encountered (rejected records, provider
        errors, unsupported identity, ...).

    gaps
        Detected gaps over the ACCEPTED candles (see
        :class:`HistoricalGap`).

    provenance
        :class:`HistoricalProvenance` for the operation.
    """

    request: HistoricalDataRequest
    status: HistoricalIngestionStatus
    candles: tuple[OHLCVCandle, ...]
    issues: tuple[HistoricalDataIssue, ...]
    gaps: tuple[HistoricalGap, ...]
    provenance: HistoricalProvenance

    @property
    def accepted_count(self) -> int:
        return len(self.candles)

    @property
    def rejected_count(self) -> int:
        return self.provenance.records_rejected

    @property
    def is_available(self) -> bool:
        return self.status is HistoricalIngestionStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class HistoricalStoreResult:
    """
    The outcome of storing candles idempotently.

    Attributes:

    instrument / timeframe
        Stored identity.

    records_added
        New candle timestamps persisted by this operation.

    records_existing
        Candle timestamps already present before this operation
        (re-ingested duplicates are NOT re-stored).

    total_candles
        Total stored candles after the operation, READ BACK from the
        persisted file (never the in-memory count) so the report always
        reflects what is actually persisted and reloadable.

    path
        The candle file path written.

    reload_verified
        True when the persisted file was reloaded after the write and
        the reloaded candle count matched the written count (the
        post-write persistence guarantee).
    """

    instrument: str
    timeframe: str
    records_added: int
    records_existing: int
    total_candles: int
    path: str
    reload_verified: bool = False


@dataclass(frozen=True, slots=True)
class HistoricalIngestResult:
    """fetch + validate + store combined result."""

    fetch: HistoricalFetchResult
    store: HistoricalStoreResult | None


@dataclass(frozen=True, slots=True)
class HistoricalDatasetSlice:
    """
    A stored historical dataset (or a filtered slice of it).

    LOOK-AHEAD PROTECTION: when ``evaluation_time`` is set, ``candles``
    contains ONLY candles with ``timestamp <= evaluation_time``.

    Attributes:

    instrument / timeframe
        Stored identity.

    candles
        Chronologically ordered stored candles (filtered by the
        caller's window / evaluation boundary).

    first_timestamp / last_timestamp
        First / last candle in the returned slice (``None`` when empty).

    count
        Number of candles in the returned slice.

    source_count
        Number of candles in the underlying stored series BEFORE any
        window / evaluation filtering.

    evaluation_time
        The applied evaluation boundary (``None`` = unbounded).
    """

    instrument: str
    timeframe: str
    candles: tuple[OHLCVCandle, ...]
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    count: int
    source_count: int
    evaluation_time: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not self.candles


# ============================================================
# RESEARCH UNIVERSE
# ============================================================


@dataclass(frozen=True, slots=True)
class ResearchUniverse:
    """
    The configurable research universe (instrument allow-list).

    The universe is CONFIGURATION, not provider logic: providers never
    hard-code the universe, and instruments can be added without
    touching the ingestion engine. Membership drives the
    ``UNSUPPORTED_INSTRUMENT`` validation verdict at the service layer.
    """

    instruments: tuple[str, ...] = (
        "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    )

    def __post_init__(self) -> None:
        normalized = tuple(
            sorted({str(i).strip().upper() for i in self.instruments if str(i).strip()}),
        )
        object.__setattr__(self, "instruments", normalized)

    def __contains__(self, instrument: object) -> bool:
        if not isinstance(instrument, str):
            return False
        return instrument.strip().upper() in self.instruments

    def __len__(self) -> int:
        return len(self.instruments)

    def __iter__(self):
        return iter(self.instruments)


#: The default research universe (Product Phase 6A starting coverage).
DEFAULT_RESEARCH_UNIVERSE = ResearchUniverse()


__all__ = [
    "DEFAULT_RESEARCH_UNIVERSE",
    "GapKind",
    "HistoricalDataError",
    "HistoricalDataIssue",
    "HistoricalDataRequest",
    "HistoricalDatasetSlice",
    "HistoricalFetchResult",
    "HistoricalGap",
    "HistoricalIngestionStatus",
    "HistoricalIngestResult",
    "HistoricalProvenance",
    "HistoricalStoreResult",
    "ProviderResponseStatus",
    "ResearchUniverse",
]
