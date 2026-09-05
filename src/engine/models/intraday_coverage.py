"""
Intraday market-data coverage + reliability models (Checkpoint 19.2).

These models describe the DETERMINISTIC coverage/reliability layer that
answers "can the future scanner reliably obtain intraday data for the
NIFTY Top 200?" They are DATA-QUALITY / AVAILABILITY models only:

* They carry NO trading, scoring, prediction, decision, ranking or
  execution logic.
* They explicitly distinguish complete / partial / missing / empty /
  stale / failed / unsupported / malformed outcomes so missing data is
  never silently converted into valid data.
* The statuses reuse the project's existing provider-response
  vocabulary (:class:`engine.models.historical_data.ProviderResponseStatus`
  and the dashboard's :class:`dashboard.data_provider.ProviderStatus`)
  rather than inventing a parallel taxonomy.

Strict per-map vocabulary (documented on each enum member):

SUPPORTED
    The provider declares that it CAN serve this instrument/timeframe.

UNSUPPORTED_INSTRUMENT
    The provider does NOT support this instrument (no verified
    provider symbol / instrument key).

UNSUPPORTED_TIMEFRAME
    The provider does NOT support this timeframe interval.

NO_DATA / EMPTY
    The provider responded but returned no candles for the window.

TEMPORARILY_UNAVAILABLE
    The provider is not currently available without necessarily being
    permanently unsupported (e.g. optional dependency missing,
    not-initialized state).

PROVIDER_ERROR
    The provider raised / failed (network, timeout, rate limit,
    malformed response, auth) while serving a supported request.

STALE
    Valid completed data exists but the latest completed candle is
    older than the session-aware staleness threshold.

VALID_WITH_GAPS
    Completed candles exist and are fresh, but gap detection found an
    UNEXPECTED gap in the served window (missing intraday candles are
    reported honestly — never fabricated).

INVALID_RESPONSE
    The provider returned data that could not be validated (impossible
    OHLC, naive timestamps, future-dated candles, all-rejected).

NOT_TESTED
    The instrument is in the requested universe but the caller did not
    request an assessment for it (coverage-report bookkeeping only).

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* ``__post_init__`` performs structural validation only; no business
  logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.ohlcv import OHLCVCandle


class IntradayCoverageStatus(Enum):
    """
    Per-instrument intraday coverage classification.

    The mapping is documented on every member and is a strict,
    deterministic function of the provider response + validation +
    freshness checks. Missing data is NEVER silently converted into
    valid data; ``STALE`` / ``VALID_WITH_GAPS`` remain valid data with
    explicit quality caveats.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    NO_DATA = "NO_DATA"
    EMPTY = "EMPTY"
    STALE = "STALE"
    VALID_WITH_GAPS = "VALID_WITH_GAPS"
    VALID = "VALID"
    NOT_TESTED = "NOT_TESTED"

    @property
    def is_valid_data(self) -> bool:
        """True when usable completed candles were returned."""
        return self in (
            IntradayCoverageStatus.VALID,
            IntradayCoverageStatus.VALID_WITH_GAPS,
            IntradayCoverageStatus.STALE,
        )

    @property
    def has_data(self) -> bool:
        """True when ANY validated candle was accepted (usable or not)."""
        return self.is_valid_data

    @property
    def needs_attention(self) -> bool:
        """True when the instrument would block/limit scanner coverage."""
        return self in (
            IntradayCoverageStatus.NO_DATA,
            IntradayCoverageStatus.EMPTY,
            IntradayCoverageStatus.PROVIDER_ERROR,
            IntradayCoverageStatus.INVALID_RESPONSE,
            IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE,
            IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT,
            IntradayCoverageStatus.UNSUPPORTED_TIMEFRAME,
            IntradayCoverageStatus.STALE,
            IntradayCoverageStatus.VALID_WITH_GAPS,
        )


@dataclass(frozen=True, slots=True)
class IntradayCandleIssue:
    """
    One auditable validation issue for an intraday candle (rejected /
    reported during normalization).

    Attributes:

    code
        Short machine-readable issue code (e.g. ``"NAIVE_TIMESTAMP"``,
        ``"DUPLICATE", "UNORDERED", "FUTURE_DATED", "INVALID_OHLC",
        "INVALID_VOLUME", "MALFORMED_RECORD", "EMPTY_RESPONSE",
        "PROVIDER_ERROR", "UNSUPPORTED"). Reuses the
        :class:`engine.models.historical_data.HistoricalDataError`
        vocabulary where it applies; provider-specific failures carry a
        stable short code chosen by the coverage layer.

    timestamp
        The offending candle timestamp when applicable, else ``None``.

    detail
        Human-readable, descriptive explanation (provider-safe; never
        contains credentials).
    """

    code: str
    timestamp: datetime | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProviderCoverageCapability:
    """
    Explicit capability declaration for ONE (provider, instrument,
    timeframe) triple (Checkpoint 19.2 — "do not pretend a provider
    supports an interval or instrument if it does not").

    Attributes:

    provider_name
        Name of the provider (e.g. ``"yahoo"``, ``"upstox-historical"``,
        ``"fixture"``).

    instrument / timeframe
        The requested identity (canonical).

    supported
        Whether the provider DECLARES support for this
        instrument/timeframe. ``False`` is honest — the provider is
        never silently assumed to serve the triple.

    resolved_symbol
        The provider-specific symbol / instrument key that WOULD be
        used when supported (``None`` when the provider cannot resolve
        the instrument at all).

    reason
        Any reason the triple is unsupported (empty when supported).
    """

    provider_name: str
    instrument: str
    timeframe: str
    supported: bool
    resolved_symbol: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IntradaySymbolResolution:
    """
    Symbol / instrument-identifier resolution for one instrument across
    the provider capabilities.

    Attributes:

    instrument
        Canonical universe instrument name.

    yahoo_symbol
        Yahoo symbol (``<NSE>.NS``), or ``None`` when the provider
        cannot map it (Yahoo passes unknown symbols verbatim, so this is
        normally set).

    upstox_instrument_key
        Upstox instrument key (``NSE_EQ|ISIN``), or ``None`` when no
        VERIFIED key exists (never guessed).

    resolution_available
        True when at least one provider can resolve the instrument.
    """

    instrument: str
    yahoo_symbol: str | None = None
    upstox_instrument_key: str | None = None

    @property
    def resolution_available(self) -> bool:
        return bool(self.yahoo_symbol or self.upstox_instrument_key)


# Aggregate report counts (NIFTY Top 200) — see :class:`IntradayCoverageReport`.


@dataclass(frozen=True, slots=True)
class IntradayCoverageCounts:
    """
    Deterministic counts over a coverage assessment (per universe).

    ``supported`` is a DERIVED capability count (tested minus explicit
    unsupported / not-ready), never a fetch outcome — a provider can
    declare support and still return no data / errors / stale bars,
    which are counted in their own buckets. The counts mirror the
    :class:`IntradayCoverageStatus` vocabulary so a diagnostic report
    can answer "how many of the 200 are supported / unsupported /
    no-data / stale / error / malformed" without interior inspection.
    """

    unsupported_instrument: int = 0
    unsupported_timeframe: int = 0
    temporarily_unavailable: int = 0
    provider_errors: int = 0
    invalid_responses: int = 0
    no_data: int = 0
    empty: int = 0
    stale: int = 0
    valid_with_gaps: int = 0
    valid: int = 0
    not_tested: int = 0

    @property
    def tested(self) -> int:
        """Number of instruments actually assessed (excludes NOT_TESTED)."""
        return sum(
            (
                self.unsupported_instrument,
                self.unsupported_timeframe,
                self.temporarily_unavailable,
                self.provider_errors,
                self.invalid_responses,
                self.no_data,
                self.empty,
                self.stale,
                self.valid_with_gaps,
                self.valid,
            ),
        )

    @property
    def supported(self) -> int:
        """Instruments the provider DECLARES capable of serving.

        ``tested`` minus the explicitly-unsupported / not-ready buckets.
        This is a capability claim — it does NOT imply those instruments
        returned current valid data.
        """
        return (
            self.tested
            - self.unsupported_instrument
            - self.unsupported_timeframe
            - self.temporarily_unavailable
        )

    @property
    def with_valid_data(self) -> int:
        """Instruments with usable completed candles (incl. stale/gapped)."""
        return self.valid + self.valid_with_gaps + self.stale

    @property
    def needs_attention(self) -> int:
        """Instruments NOT serving clean current data (scanner-relevant)."""
        return (
            self.no_data
            + self.empty
            + self.provider_errors
            + self.invalid_responses
            + self.temporarily_unavailable
            + self.unsupported_instrument
            + self.unsupported_timeframe
            + self.stale
            + self.valid_with_gaps
        )

    @property
    def assessed_total(self) -> int:
        """Total universe size recorded for the assessment (tested+not)."""
        return self.tested + self.not_tested


@dataclass(frozen=True, slots=True)
class IntradayInstrumentCoverage:
    """
    Coverage status for ONE instrument at one reference time.

    Attributes:

    instrument
        Canonical instrument name.

    timeframe
        Canonical timeframe (e.g. ``"15m"``).

    status
        :class:`IntradayCoverageStatus` classification.

    provider_name
        The provider that served the assessment (``""`` when no
        provider assessment ran).

    resolved_symbol
        Provider-specific symbol used (``None`` when unresolved).

    candle_count
        Number of VALIDATED completed candles accepted, or ``0``.

    first_timestamp / last_timestamp
        First / last ACCEPTED candle timestamps, or ``None``.

    forming_candle
        The currently-forming candle (DISPLAY ONLY), or ``None``. NEVER
        fed to an intelligence engine.

    rejected_future_count
        Number of future-dated candles rejected by the completed-candle
        boundary (``0`` when none).

    duplicate_count
        Number of duplicate-timestamp candles rejected (``0`` when none).

    out_of_order_count
        Number of out-of-order candles normalized (``0`` when sorted).

    issues
        Every auditable validation issue encountered
        (:class:`IntradayCandleIssue`).

    market_session
        :class:`engine.data.market_session.MarketSessionState` at the
        reference time, or ``None`` when not evaluated.

    staleness_seconds
        The deterministic staleness threshold used (``None`` when not
        evaluated).

    data_age_seconds
        Age of the latest completed candle at the reference time
        (``None`` when no candle / not evaluated).

    last_update_time
        Provider-level last-successful-fetch metadata, or ``None``.

    reason
        Human-readable summary (cumulative, provider-safe).
    """

    instrument: str
    timeframe: str
    status: IntradayCoverageStatus
    provider_name: str = ""
    resolved_symbol: str | None = None
    candle_count: int = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    forming_candle: OHLCVCandle | None = None
    rejected_future_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    issues: tuple[IntradayCandleIssue, ...] = field(default_factory=tuple)
    market_session: object | None = None
    staleness_seconds: int | None = None
    data_age_seconds: float | None = None
    last_update_time: datetime | None = None
    reason: str = ""

    @property
    def has_completed_candles(self) -> bool:
        return self.candle_count > 0


@dataclass(frozen=True, slots=True)
class IntradayCoverageReport:
    """
    Aggregate deterministic coverage report over a universe (NIFTY Top
    200) for one provider / timeframe pair.

    Attributes:

    provider_name / timeframe
        The assessment identity.

    universe_instrument_count
        Requested universe size (stocks + benchmark where included):
        200 for the stock Constituents-only universe,
        200 + benchmark when the market tuple was used.

    instruments
        Canonical instruments in the request (sorted).

    results
        Per-instrument :class:`IntradayInstrumentCoverage` (sorted by
        instrument).

    counts
        :class:`IntradayCoverageCounts` aggregate.

    reference_now
        The deterministic reference time used for the freshness /
        boundary evaluation.

    market_session
        :class:`engine.data.market_session.MarketSessionState` at
        ``reference_now``.

    seconds_until_next_open
        Deterministic estimate of seconds until the next NSE open
        (``None`` when not evaluable).

    capabilities
        Per-triple provider capability declarations
        (:class:`ProviderCoverageCapability`) — a deterministic
        capability surface for the future scanner.
    """

    provider_name: str
    timeframe: str
    universe_instrument_count: int
    reference_now: datetime
    results: tuple[IntradayInstrumentCoverage, ...] = field(default_factory=tuple)
    counts: IntradayCoverageCounts = field(default_factory=IntradayCoverageCounts)
    market_session: object | None = None
    seconds_until_next_open: float | None = None
    capabilities: tuple[ProviderCoverageCapability, ...] = field(
        default_factory=tuple,
    )

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(r.instrument for r in self.results)

    @property
    def instrument_count(self) -> int:
        return len(self.results)

    def instrument_status(self, instrument: str) -> IntradayCoverageStatus | None:
        """Per-instrument status lookup (``None`` when not present)."""
        for result in self.results:
            if result.instrument == instrument:
                return result.status
        return None

    @property
    def coverage_ratio(self) -> float:
        """
        Proportion of TESTED instruments carrying valid completed data,
        in ``[0, 1]`` (0 when nothing was tested). Never 1.0 unless every
        tested instrument actually returned usable candles — partial
        coverage can never become false full coverage.
        """
        tested = self.counts.tested
        if tested == 0:
            return 0.0
        return self.counts.with_valid_data / tested


__all__ = [
    "IntradayCandleIssue",
    "IntradayCoverageCounts",
    "IntradayCoverageReport",
    "IntradayCoverageStatus",
    "IntradayInstrumentCoverage",
    "IntradaySymbolResolution",
    "ProviderCoverageCapability",
]