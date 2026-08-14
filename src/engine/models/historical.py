"""
Domain models for historical market data integration (Sprint 11V).

These models are the data-ingestion layer of Sprint 11V. They describe
REALISTIC historical OHLCV market data normalized into the engine's
canonical :class:`~engine.models.ohlcv.OHLCVCandle` form, keyed by
instrument and timeframe, together with an explicit distinction between
VALID, INVALID and INCOMPLETE data.

Sprint 11V is NOT an intelligence layer and NOT a scoring layer. It is
the integration + validation milestone that proves the EXISTING
intelligence pipeline (Sprints 11A-11U) can consume realistic historical
OHLCV data exactly as if it were seeing the market sequentially in real
time, without using future information. No candle, structure, setup,
candidate, decision, opportunity or scanner logic is duplicated here.

DESIGN PRINCIPLE — no fabricated data:

Missing data MUST NEVER become a directional conclusion. Invalid records
are REJECTED (the existing repository convention: ``OHLCVCandle`` and
``DataValidator`` raise on impossible OHLC relationships). Incomplete
data (a missing required timeframe, insufficient history, an unavailable
higher timeframe, or an in-progress higher-timeframe candle) is
represented EXPLICITLY via the :class:`DataQuality` enum and carried on
the normalization result — never silently manufactured into a price, a
trend, a structure, a setup, a stop, a target or a risk/reward figure.

DESIGN PRINCIPLE — reuse, do not re-invent:

The normalized candle is the EXISTING ``OHLCVCandle``. Instrument /
timeframe identity is carried by the wrapping series models, not by
duplicating a candle type. The scanner (:class:`InstrumentDataset`) is
fed from these series; no intelligence is reimplemented.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers.
* ``__post_init__`` validates internal consistency so hand-construction
  bugs surface early.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.ohlcv import OHLCVCandle


class DataQuality(Enum):
    """
    The quality verdict for a normalized historical data series.

    VALID
        The series carries a non-empty, chronologically ordered,
        duplicate-free set of valid OHLCV candles.

    INVALID
        The series could not be normalized (e.g. impossible OHLC
        relationships, duplicate timestamps that could not be
        reconciled, a missing timestamp). Invalid records are REJECTED
        per the existing repository convention — they never produce a
        fabricated series.

    INCOMPLETE
        The series is structurally valid but does not carry the minimum
        information required for analysis (e.g. an empty series, a
        missing required timeframe, insufficient historical candles, or
        an in-progress higher-timeframe candle). Incomplete data is
        NEVER a directional conclusion; it is represented explicitly so
        downstream layers surface it honestly.
    """

    VALID = "VALID"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"


class HistoricalNormalizationError(Enum):
    """
    Categorical reason a record was rejected or marked incomplete during
    normalization. Carried on :class:`NormalizationIssue` for audit.
    """

    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    INVALID_OHLC = "INVALID_OHLC"
    HIGH_BELOW_LOW = "HIGH_BELOW_LOW"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    UNORDERED = "UNORDERED"
    EMPTY_SERIES = "EMPTY_SERIES"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_TIMEFRAME = "MISSING_TIMEFRAME"
    MISSING_INSTRUMENT = "MISSING_INSTRUMENT"


@dataclass(frozen=True, slots=True)
class NormalizationIssue:
    """
    One auditable normalization issue.

    Attributes:

    severity
        The :class:`DataQuality` verdict this issue contributed to
        (``INVALID`` for a rejected record, ``INCOMPLETE`` for a missing
        / insufficient series).

    instrument
        Canonical instrument name the issue pertains to (``""`` when the
        record itself carried no usable instrument identity).

    timeframe
        Canonical timeframe label the issue pertains to (``""`` when
        unknown).

    error
        The :class:`HistoricalNormalizationError` categorizing the issue.

    reason
        Human-readable, descriptive explanation.
    """

    severity: DataQuality
    instrument: str
    timeframe: str
    error: HistoricalNormalizationError
    reason: str


@dataclass(frozen=True, slots=True)
class TimeframeSeries:
    """
    One instrument's normalized candle series for one timeframe.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``).

    timeframe
        Canonical timeframe label (e.g. ``"1D"``, ``"15M"``).

    candles
        Chronologically ordered (oldest -> newest), duplicate-free,
        valid ``OHLCVCandle`` tuple. Empty when the series is
        INCOMPLETE (no usable candles).

    quality
        The :class:`DataQuality` verdict for this series.

    candle_count
        Number of candles carried (``0`` when INCOMPLETE).
    """

    instrument: str
    timeframe: str
    candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    quality: DataQuality = DataQuality.INCOMPLETE
    candle_count: int = 0

    def __post_init__(self) -> None:
        if self.candle_count != len(self.candles):
            object.__setattr__(self, "candle_count", len(self.candles))
        if self.instrument == "":
            raise ValueError("instrument must be a non-empty string.")
        if self.timeframe == "":
            raise ValueError("timeframe must be a non-empty string.")


@dataclass(frozen=True, slots=True)
class HistoricalInstrumentData:
    """
    One instrument's normalized data across the requested timeframes.

    Attributes:

    instrument
        Canonical instrument name.

    series
        Mapping of timeframe label -> :class:`TimeframeSeries`. A
        timeframe that was requested but missing / incomplete is
        represented by a series with ``quality == INCOMPLETE`` (an empty
        candles tuple) rather than being omitted, so missing data is
        explicit.

    timeframes
        Sorted tuple of timeframe labels present in ``series``.
    """

    instrument: str
    series: dict[str, TimeframeSeries] = field(default_factory=dict)

    @property
    def timeframes(self) -> tuple[str, ...]:
        return tuple(sorted(self.series.keys()))

    def get(self, timeframe: str) -> TimeframeSeries | None:
        """Return the series for ``timeframe`` or ``None`` when absent."""

        return self.series.get(timeframe)


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    """
    A normalized, multi-instrument / multi-timeframe historical dataset.

    This is the canonical input to the historical replay layer. It
    carries the VALID normalized series per instrument / timeframe plus
    an explicit record of every INVALID (rejected) and INCOMPLETE series
    discovered during normalization, so data quality is auditable end to
    end.

    Attributes:

    instruments
        Sorted tuple of instrument names with at least one series.

    data
        Mapping of instrument name -> :class:`HistoricalInstrumentData`.

    issues
        Tuple of every :class:`NormalizationIssue` (rejected + incomplete)
        discovered during normalization, in the order encountered.

    invalid_count
        Number of INVALID (rejected) series / records.

    incomplete_count
        Number of INCOMPLETE series.

    valid_count
        Number of VALID series.
    """

    data: dict[str, HistoricalInstrumentData] = field(default_factory=dict)
    issues: tuple[NormalizationIssue, ...] = field(default_factory=tuple)

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(sorted(self.data.keys()))

    def get(self, instrument: str) -> HistoricalInstrumentData | None:
        """Return one instrument's data or ``None`` when absent."""

        return self.data.get(instrument)

    @property
    def valid_count(self) -> int:
        return sum(
            1
            for inst in self.data.values()
            for s in inst.series.values()
            if s.quality == DataQuality.VALID
        )

    @property
    def invalid_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == DataQuality.INVALID)

    @property
    def incomplete_count(self) -> int:
        return sum(
            1
            for inst in self.data.values()
            for s in inst.series.values()
            if s.quality == DataQuality.INCOMPLETE
        ) + sum(
            1 for i in self.issues if i.severity == DataQuality.INCOMPLETE
        )

    def context_candles(
        self, instrument: str, timeframe: str,
    ) -> tuple[OHLCVCandle, ...]:
        """Return the candles for an instrument's context timeframe."""

        inst = self.data.get(instrument)
        if inst is None:
            return ()
        series = inst.series.get(timeframe)
        return series.candles if series is not None else ()

    def setup_candles(
        self, instrument: str, timeframe: str,
    ) -> tuple[OHLCVCandle, ...]:
        """Return the candles for an instrument's setup timeframe."""

        return self.context_candles(instrument, timeframe)


@dataclass(frozen=True, slots=True)
class HistoricalRecord:
    """
    One raw historical OHLCV record pending normalization.

    The adapter accepts these (or equivalent mappings) and normalizes
    them into the canonical :class:`OHLCVCandle` form. Fields are
    optional so the adapter can report which records are invalid /
    incomplete rather than raising on the whole batch.

    Attributes:

    timestamp
        The candle close/open timestamp. ``None`` when the record is
        missing a timestamp (INVALID).

    open, high, low, close, volume
        OHLCV values. May be ``None`` / invalid for a partial record.

    instrument
        Canonical instrument name. ``""`` when missing.

    timeframe
        Canonical timeframe label. ``""`` when missing.
    """

    timestamp: datetime | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    instrument: str = ""
    timeframe: str = ""


__all__ = [
    "DataQuality",
    "HistoricalDataset",
    "HistoricalInstrumentData",
    "HistoricalNormalizationError",
    "HistoricalRecord",
    "NormalizationIssue",
    "TimeframeSeries",
]
