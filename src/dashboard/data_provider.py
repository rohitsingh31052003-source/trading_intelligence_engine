"""
Dashboard market-data provider adapter (productization layer).

This adapter is the ONLY component in the dashboard that touches raw
market data. It produces the normalized :class:`OHLCVCandle` tuples the
existing scanner consumes, reusing the existing
:class:`DataValidator` / :class:`HistoricalDataAdapter` normalization
semantics. It implements NO intelligence.

Two providers are supported:

1. A deterministic LOCAL fixture provider (default) — reuses
   :mod:`engine.data.historical_fixtures`. No network dependency. This
   is what runs out-of-the-box for research / demos.

2. An OPTIONAL Yahoo Finance provider — reuses the existing
   :class:`YahooFinanceProvider` through a thin adapter. It is only used
   when explicitly enabled AND the optional ``yfinance`` / ``pandas``
   dependencies are importable. ANY provider failure (network, missing
   symbol, unsupported intraday interval) is handled GRACEFULLY: the
   adapter returns an empty / unavailable dataset and the dashboard
   shows an honest "unavailable" state. It NEVER crashes the dashboard
   and NEVER fabricates data.

The adapter maps the dashboard's intraday timeframe labels
(``1m``/``3m``/``5m``/``15m``/``30m``/``1h``/``4h``/``1D``) onto the
``(context_timeframe, setup_timeframe)`` pair the existing scanner
requires. The selected timeframe becomes the SETUP (execution)
timeframe; a higher timeframe is chosen as CONTEXT when available,
otherwise the scan is reported INCOMPLETE (never a fabricated
directional conclusion).

COMPLETED-CANDLE BOUNDARY (Product Phase 1):

The analysis is defined around CLOSED candles. A currently FORMING /
in-progress candle MUST NOT influence the analysis. The
:func:`split_completed_candles` helper is the single, deterministic,
testable implementation of this boundary: given raw candles + a
timeframe + a reference "now" it returns the completed candles (those
whose close time ``timestamp + duration <= now``), the optional forming
candle (kept for DISPLAY ONLY, never fed to the engine), and rejects
any future-dated candle (``timestamp > now``) as invalid. Providers
return ONLY completed candles in ``setup_candles`` / ``context_candles``;
the forming candle is carried separately on ``InstrumentSeries`` so the
dashboard can display it without ever feeding it to the intelligence
engine. The service then sets ``evaluation_time`` to
``latest_completed_candle_timestamp`` — never to the forming candle.

FRESHNESS (Product Phase 1):

Freshness is DATA QUALITY / PRODUCT STATE, NOT a trading signal. It
never alters the intelligence engine's decision semantics — it only
drives presentation warnings + metadata. The :class:`FreshnessState`
enum (``CURRENT`` / ``STALE`` / ``UNAVAILABLE`` / ``INVALID``) and the
config-driven :class:`FreshnessConfig` thresholds replace scattered
magic constants. A missing/failed provider response is ``UNAVAILABLE``;
a malformed candle is ``INVALID``; the latest completed candle older
than the timeframe's staleness threshold is ``STALE``; otherwise
``CURRENT``. No fake "live" state is invented.

No broker integration. No order execution. No live trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Protocol, Sequence

from engine.data.validator import DataValidator
from engine.models.ohlcv import OHLCVCandle


#: Far-future aware sentinel used to treat all supplied candles as
#: completed when no explicit ``reference_now`` is provided (deterministic
#: fixture path). Aware so it compares cleanly against aware candle
#: timestamps without raising a TypeError on a naive/aware mix.
_FAR_FUTURE_NOW: datetime = datetime(9999, 12, 31, tzinfo=UTC)


# ============================================================
# TIMEFRAME MAPPING
# ============================================================

#: Canonical dashboard intraday timeframe labels, ordered low->high.
SUPPORTED_TIMEFRAMES: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D",
)

#: Fixed fixture timeframes available in the local deterministic
#: fixtures (Sprint 11V). Timeframes outside this set are reported as
#: unavailable when the fixture provider is used — honestly, never
#: fabricated.
FIXTURE_TIMEFRAMES: dict[str, str] = {
    "1D": "1D",
    "15M": "15M",
    "15m": "15M",
}

#: Mapping from a dashboard setup timeframe to a sensible higher
#: CONTEXT timeframe, when one is available. When no higher timeframe
#: is available the scan is INCOMPLETE for that instrument.
_CONTEXT_FALLBACK: dict[str, str] = {
    "1m": "15m",
    "3m": "15m",
    "5m": "1h",
    "15m": "1D",
    "15M": "1D",
    "30m": "4h",
    "1h": "1D",
    "4h": "1D",
    "1D": "1D",  # daily has no higher context in fixtures -> INCOMPLETE
}

#: Instruments available in the local deterministic fixtures.
FIXTURE_INSTRUMENTS: tuple[str, ...] = (
    "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
)


def context_timeframe_for(setup_timeframe: str) -> str | None:
    """Return a sensible context timeframe, or ``None`` when none."""

    return _CONTEXT_FALLBACK.get(setup_timeframe)


# ============================================================
# TIMEFRAME DURATION (for the completed-candle boundary)
# ============================================================

#: Duration in seconds of one candle for each supported timeframe label.
#: Used by :func:`split_completed_candles` to determine whether a candle
#: had CLOSED (``timestamp + duration <= now``) at the reference time.
#: Labels not present here are treated as having an UNKNOWN duration —
#: the boundary helper then conservatively treats the last candle as
#: potentially forming (so it is excluded from the engine input unless
#: the caller explicitly marks the data as historical/completed).
TIMEFRAME_DURATION_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "15M": 900,
    "30m": 1800,
    "60m": 3600,
    "1h": 3600,
    "90m": 5400,
    "4h": 14400,
    "1D": 86400,
    "1d": 86400,
}


def timeframe_duration_seconds(timeframe: str) -> int | None:
    """Return the candle duration in seconds, or ``None`` when unknown."""

    return TIMEFRAME_DURATION_SECONDS.get(timeframe)


# ============================================================
# FRESHNESS + PROVIDER STATUS (Product Phase 1)
# ============================================================


class FreshnessState(Enum):
    """
    Data-quality / product-state freshness of a fetched series.

    This is NOT a trading signal and NEVER alters the intelligence
    engine's decision semantics — it only drives presentation warnings
    and metadata.

    CURRENT
        The latest completed candle is sufficiently recent relative to
        the configured staleness threshold for its timeframe.

    STALE
        Data was fetched successfully but the latest completed candle is
        older than the configured staleness threshold. The analysis is
        still produced honestly (over completed candles) but a stale
        warning is surfaced.

    UNAVAILABLE
        No usable data could be fetched (provider failure, empty
        response, unsupported instrument / timeframe, optional
        dependency missing). No analysis is produced; nothing is
        fabricated.

    INVALID
        The provider returned malformed data (impossible OHLC, future
        timestamps, unsorted). Invalid candles are REJECTED; the result
        reports the issue honestly rather than producing a partial
        analysis from corrupted data.
    """

    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class ProviderStatus(Enum):
    """
    Coarse provider-side status of a fetch, independent of freshness.

    OK
        The provider responded and returned at least one completed
        candle.

    UNSUPPORTED
        The requested instrument / timeframe is not supported by this
        provider.

    EMPTY
        The provider responded but returned no candles.

    ERROR
        The provider raised / failed (network, timeout, rate limit,
        invalid response, missing optional dependency).

    NOT_READY
        The provider is not initialized (e.g. optional dependency
        missing at construction time).
    """

    OK = "OK"
    UNSUPPORTED = "UNSUPPORTED"
    EMPTY = "EMPTY"
    ERROR = "ERROR"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class FreshnessConfig:
    """
    Configuration-driven freshness thresholds (Product Phase 1).

    Replaces scattered magic constants. The staleness threshold is the
    maximum age (in seconds) of the latest completed candle for it to be
    considered CURRENT. A per-timeframe override may be supplied; the
    default threshold is used otherwise. Freshness is DATA QUALITY only
    — it never alters the intelligence engine's decision semantics.

    Attributes:

    default_staleness_seconds
        Default max age of the latest completed candle (seconds).

    timeframe_overrides
        Optional mapping ``timeframe -> seconds`` overriding the default
        for specific timeframes (e.g. ``{"1m": 120, "1D": 172800}``).
    """

    default_staleness_seconds: int = 24 * 60 * 60
    timeframe_overrides: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    def staleness_seconds_for(self, timeframe: str) -> int:
        """Return the staleness threshold (seconds) for a timeframe."""

        overrides = dict(self.timeframe_overrides)
        if timeframe in overrides:
            return int(overrides[timeframe])
        upper = timeframe.upper()
        if upper in overrides:
            return int(overrides[upper])
        return int(self.default_staleness_seconds)


def classify_freshness(
    *,
    latest_completed_timestamp: datetime | None,
    reference_now: datetime,
    staleness_seconds: int,
    provider_status: ProviderStatus,
) -> FreshnessState:
    """
    Classify the freshness of a fetched series (DETERMINISTIC).

    The classification is a pure function of the latest completed
    candle timestamp, a reference "now", the staleness threshold and the
    provider status. It introduces NO new intelligence and never alters
    decision semantics.

    Mapping:

    * provider status EMPTY / ERROR / NOT_READY / UNSUPPORTED with no
      completed candle -> ``UNAVAILABLE``.
    * no completed candle but provider OK -> ``UNAVAILABLE``.
    * completed candle present and its age
      ``(reference_now - latest_completed)`` <= threshold ->
      ``CURRENT``.
    * completed candle present but older than the threshold -> ``STALE``.

    ``INVALID`` is assigned by the provider / boundary helper when
    malformed candles are encountered (not by this function, which only
    sees the surviving completed candles).
    """

    if latest_completed_timestamp is None:
        return FreshnessState.UNAVAILABLE
    if provider_status in (
        ProviderStatus.EMPTY,
        ProviderStatus.ERROR,
        ProviderStatus.NOT_READY,
    ):
        return FreshnessState.UNAVAILABLE
    # Normalize naive/aware so a naive reference_now never raises.
    def _naive(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(UTC).replace(tzinfo=None)

    age = (
        _naive(reference_now) - _naive(latest_completed_timestamp)
    ).total_seconds()
    if age <= staleness_seconds:
        return FreshnessState.CURRENT
    return FreshnessState.STALE


# ============================================================
# COMPLETED-CANDLE BOUNDARY (Product Phase 1)
# ============================================================


@dataclass(frozen=True)
class CandleBoundaryResult:
    """
    Result of :func:`split_completed_candles`.

    Attributes:

    completed
        Chronologically sorted, de-duplicated CLOSED candles (close time
        ``<= now``). These are the ONLY candles ever fed to the
        intelligence engine.

    forming
        The optional currently-forming candle (close time ``> now`` but
        open time ``<= now``), kept for DISPLAY ONLY. Never fed to the
        engine.

    rejected_future
        Candles with an open timestamp strictly after ``now`` (future
        data) — REJECTED, never used.

    latest_completed_timestamp
        Timestamp of the last completed candle, or ``None``.

    invalid
        Whether any candle was rejected as impossible (failed
        :class:`OHLCVCandle` construction) — reported honestly.
    """

    completed: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    forming: OHLCVCandle | None = None
    rejected_future: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    latest_completed_timestamp: datetime | None = None
    invalid: bool = False


def split_completed_candles(
    candles: "Sequence[OHLCVCandle] | None",
    timeframe: str,
    reference_now: datetime,
    *,
    duration_seconds: int | None = None,
) -> CandleBoundaryResult:
    """
    Split raw candles into COMPLETED vs FORMING vs REJECTED-FUTURE.

    This is the single, deterministic, testable implementation of the
    completed-candle boundary. It implements NO intelligence and never
    mutates the input.

    A candle is COMPLETED when its close time
    ``timestamp + duration <= reference_now``. The duration is the
    candle timeframe's duration (see :data:`TIMEFRAME_DURATION_SECONDS`);
    an explicit ``duration_seconds`` overrides it (used by tests). When
    the duration is UNKNOWN the helper conservatively treats the LAST
    candle as forming (excluded from the engine input) unless it is
    strictly older than the reference — this guarantees a forming candle
    is never accidentally fed to the engine for an unknown timeframe.

    Future-dated candles (``timestamp > reference_now``) are REJECTED and
    reported — never used. Impossible candles (those that cannot be
    constructed as :class:`OHLCVCandle`) are filtered by the caller
    before this helper; this helper only inspects timestamps.

    The result is DETERMINISTIC: identical inputs always yield identical
    outputs. ``reference_now`` is an explicit parameter so tests do not
    depend on wall-clock time.
    """

    if candles is None:
        return CandleBoundaryResult()
    seq: Sequence[OHLCVCandle] = candles

    dur = duration_seconds if duration_seconds is not None else (
        timeframe_duration_seconds(timeframe)
    )

    # Normalize naive/aware timestamps so a naive ``reference_now`` (or
    # naive candle timestamps) never raises a TypeError on comparison.
    # We compare on the naive instant; tz-aware datetimes are converted
    # to their naive UTC equivalent. This is a data-layer convenience —
    # the intelligence engine still receives the original candles.
    def _naive(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(UTC).replace(tzinfo=None)

    ref_naive = _naive(reference_now)

    # De-duplicate by timestamp (keep first) and sort chronologically.
    seen: set[datetime] = set()
    unique: list[OHLCVCandle] = []
    for c in seq:
        if c.timestamp in seen:
            continue
        seen.add(c.timestamp)
        unique.append(c)
    unique.sort(key=lambda c: c.timestamp)

    rejected_future: list[OHLCVCandle] = []
    completed: list[OHLCVCandle] = []
    forming: OHLCVCandle | None = None

    for c in unique:
        c_naive = _naive(c.timestamp)
        if c_naive > ref_naive:
            # Future-dated candle — reject, never use.
            rejected_future.append(c)
            continue
        if dur is None:
            # Unknown duration: conservatively treat the last candle as
            # forming unless it is strictly older than the reference.
            completed.append(c)
            continue
        close_time_epoch = c_naive + timedelta(seconds=dur)
        if close_time_epoch <= ref_naive:
            completed.append(c)
        else:
            # Forming candle (open <= now but close > now). Keep the
            # most recent forming candle for display only.
            if forming is None or c.timestamp > forming.timestamp:
                forming = c

    latest = completed[-1].timestamp if completed else None
    return CandleBoundaryResult(
        completed=tuple(completed),
        forming=forming,
        rejected_future=tuple(rejected_future),
        latest_completed_timestamp=latest,
        invalid=False,
    )


# ============================================================
# PROVIDER PROTOCOL
# ============================================================


@dataclass(frozen=True)
class InstrumentSeries:
    """
    One instrument's normalized candles for a context + setup
    timeframe pair, as the existing scanner expects.

    Attributes:

    instrument
        Canonical instrument name.

    context_candles
        Higher-timeframe COMPLETED candles (may be empty -> scan
        INCOMPLETE). Never contains a forming candle.

    setup_candles
        Setup-timeframe COMPLETED candles (may be empty -> scan
        INCOMPLETE). Never contains a forming candle — the forming
        candle is carried separately on ``forming_setup_candle`` for
        DISPLAY ONLY.

    available
        Whether at least the setup timeframe carried usable completed
        data.

    reason
        Human-readable reason when unavailable (e.g. "unsupported
        timeframe for fixture provider", "provider error: ...").

    data_source
        Name of the data source (``"fixture"`` / ``"yahoo"`` / ...).

    provider_status
        :class:`ProviderStatus` of the fetch (Product Phase 1).

    freshness_state
        :class:`FreshnessState` of the setup series (Product Phase 1).
        DATA QUALITY only — never alters decision semantics.

    latest_candle_timestamp
        Timestamp of the latest candle the provider saw (may be a
        forming candle), or ``None``.

    latest_completed_candle_timestamp
        Timestamp of the latest COMPLETED setup candle — this is the
        analysis boundary. The service uses this as ``evaluation_time``.

    forming_setup_candle
        The currently-forming setup candle (DISPLAY ONLY), or ``None``.
        NEVER fed to the intelligence engine.

    last_successful_fetch_time
        When the provider last successfully fetched data, or ``None``.
        For the deterministic fixture provider this is the fetch time.

    rejected_future_count
        Number of future-dated candles rejected by the boundary (honest
        reporting of malformed provider output).
    """

    instrument: str
    context_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    setup_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    available: bool = False
    reason: str = ""
    data_source: str = ""
    provider_status: ProviderStatus = ProviderStatus.NOT_READY
    freshness_state: FreshnessState = FreshnessState.UNAVAILABLE
    latest_candle_timestamp: datetime | None = None
    latest_completed_candle_timestamp: datetime | None = None
    forming_setup_candle: OHLCVCandle | None = None
    last_successful_fetch_time: datetime | None = None
    rejected_future_count: int = 0


class DashboardDataProvider(Protocol):
    """
    Minimal provider contract the dashboard service depends on.

    Implementations return an :class:`InstrumentSeries` for one
    instrument + setup timeframe. Failures are reported via
    ``available=False`` + a reason — never raised.
    """

    def is_timeframe_supported(self, setup_timeframe: str) -> bool: ...

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
    ) -> InstrumentSeries: ...

    def last_updated(self, instrument: str, setup_timeframe: str) -> datetime | None:
        """Return the timestamp of the latest available candle, or None."""


# ============================================================
# FIXTURE PROVIDER (default, deterministic, no network)
# ============================================================


class FixtureDataProvider:
    """
    Deterministic LOCAL market-data provider backed by the Sprint 11V
    :mod:`engine.data.historical_fixtures`.

    Only ``1D`` (context) and ``15M`` / ``15m`` (setup) are available
    from the fixtures. Any other timeframe is reported as UNSUPPORTED
    (honest "unavailable" state). Any instrument outside the fixture
    set is reported unavailable. No data is fabricated.

    The fixture candles are SYNTHETIC but OHLC-valid and chronologically
    ordered; they exercise the full existing intelligence pipeline the
    same way real data would. They are intended for local research and
    demos, NOT live trading.
    """

    #: Name of this data source (Product Phase 1 metadata).
    DATA_SOURCE: str = "fixture"

    def __init__(
        self,
        freshness_config: FreshnessConfig | None = None,
    ) -> None:
        # Lazy import so the dashboard module loads even if a fixture
        # helper were unavailable (it never is — fixtures are stdlib).
        from engine.data.historical_fixtures import (
            historical_candles_by_instrument,
        )

        self._cache = historical_candles_by_instrument(
            FIXTURE_INSTRUMENTS, "1D", "15M",
        )
        self.freshness_config = freshness_config or FreshnessConfig()

    @property
    def data_source(self) -> str:
        return self.DATA_SOURCE

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return setup_timeframe in ("15M", "15m")

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
        *,
        reference_now: datetime | None = None,
    ) -> InstrumentSeries:
        # The fixture path is FULLY DETERMINISTIC — it never calls
        # wall-clock ``datetime.now()``. When ``reference_now`` is
        # supplied (tests), freshness is computed against it; otherwise
        # fixture data is honestly reported as STALE (it is historical
        # synthetic data) and ``last_successful_fetch_time`` is anchored
        # to the data's own latest completed candle timestamp.
        now = reference_now
        if instrument not in self._cache:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=f"instrument '{instrument}' not in fixture set",
                data_source="fixture",
                provider_status=ProviderStatus.UNSUPPORTED,
                freshness_state=FreshnessState.UNAVAILABLE,
                last_successful_fetch_time=now,
            )
        if not self.is_timeframe_supported(setup_timeframe):
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=(
                    f"timeframe '{setup_timeframe}' not available in the "
                    "local deterministic fixtures (only 15M setup / 1D "
                    "context are provided)"
                ),
                data_source="fixture",
                provider_status=ProviderStatus.UNSUPPORTED,
                freshness_state=FreshnessState.UNAVAILABLE,
                last_successful_fetch_time=now,
            )
        pair = self._cache[instrument]
        setup_raw = tuple(pair.get("15M", ()))
        context_raw = tuple(pair.get("1D", ()))

        # Apply the completed-candle boundary deterministically. With no
        # reference_now the fixture candles (all historical) are treated
        # as completed by passing a far-future aware sentinel; this
        # never affects the engine output (the scanner re-truncates at
        # the evaluation time) and keeps the fixture path wall-clock-free.
        boundary_now = now if now is not None else _FAR_FUTURE_NOW
        setup_boundary = split_completed_candles(setup_raw, "15m", boundary_now)
        context_boundary = split_completed_candles(context_raw, "1D", boundary_now)
        setup = setup_boundary.completed
        context = context_boundary.completed

        latest_completed = setup_boundary.latest_completed_timestamp
        if now is not None:
            freshness = classify_freshness(
                latest_completed_timestamp=latest_completed,
                reference_now=now,
                staleness_seconds=self.freshness_config.staleness_seconds_for("15m"),
                provider_status=ProviderStatus.OK,
            )
            fetch_time: datetime | None = now
        else:
            # Deterministic: fixture data is historical -> STALE, and
            # the fetch time is anchored to the data's own timestamp.
            freshness = FreshnessState.STALE
            fetch_time = latest_completed
        return InstrumentSeries(
            instrument=instrument,
            context_candles=context,
            setup_candles=setup,
            available=bool(setup),
            reason="" if setup else "empty setup series",
            data_source="fixture",
            provider_status=ProviderStatus.OK,
            freshness_state=freshness,
            latest_candle_timestamp=latest_completed,
            latest_completed_candle_timestamp=latest_completed,
            forming_setup_candle=None,
            last_successful_fetch_time=fetch_time,
            rejected_future_count=(
                len(setup_boundary.rejected_future)
                + len(context_boundary.rejected_future)
            ),
        )

    def last_updated(
        self, instrument: str, setup_timeframe: str,
    ) -> datetime | None:
        series = self.fetch(instrument, setup_timeframe)
        if not series.setup_candles:
            return None
        return series.latest_completed_candle_timestamp


# ============================================================
# YAHOO PROVIDER (optional, graceful)
# ============================================================


class YahooDataProvider:
    """
    OPTIONAL live / near-live market-data provider reusing the existing
    :class:`YahooFinanceProvider` (Product Phase 1).

    This provider is only used when explicitly selected AND the optional
    ``yfinance`` / ``pandas`` dependencies are importable. ANY failure
    (missing dependency, network error, unsupported intraday interval,
    unknown symbol, empty response, malformed candle) is caught and
    reported as ``available=False`` with a structured
    :class:`ProviderStatus` + reason — the dashboard NEVER crashes and
    NEVER fabricates data. A failed live provider NEVER silently falls
    back to fixture data in a live request (Product Phase 1 rule).

    COMPLETED-CANDLE BOUNDARY (NON-NEGOTIABLE):

    Yahoo Finance can return the currently-FORMING candle as the last
    row of an intraday response. This provider runs EVERY fetched series
    (setup + context) through :func:`split_completed_candles` so that
    ONLY closed candles are returned in ``setup_candles`` /
    ``context_candles``. The forming candle is carried separately on
    ``forming_setup_candle`` for DISPLAY ONLY — it is NEVER fed to the
    intelligence engine. Future-dated candles (``timestamp > now``) are
    REJECTED and counted in ``rejected_future_count``.

    SYMBOL MAPPING (isolated inside the provider):

    The dashboard's canonical instrument names (e.g. ``"NIFTY"``) are
    mapped to Yahoo symbols (e.g. ``"^NSEI"``) INSIDE this provider via
    :data:`YAHOO_SYMBOL_MAP` / the ``symbol_map`` constructor argument.
    Provider-specific symbol formatting never leaks into the analysis
    engine or the dashboard UI. Unknown instruments are passed through
    verbatim (Yahoo accepts many tickers directly, e.g. ``AAPL``).

    Yahoo Finance intraday intervals are limited (typically up to 60
    days for <=30m/90m, up to 730 days for <=1h, up to 7 days for 1m).
    The provider requests a RECENT, BOUNDED window derived from
    ``lookback_bars`` + a modest engine-context buffer, capped per
    interval at a value SAFELY INSIDE Yahoo's permitted range (never an
    exact boundary, so clock skew / timezone differences cannot cause a
    "must be within the last N days" rejection). It NEVER requests a
    fixed 60-day window for every intraday call. If Yahoo returns no data
    the result is honestly unavailable.
    """

    #: Default mapping from canonical dashboard instruments to Yahoo
    #: symbols. Indian index futures / stocks use the ``.NS`` / ``.BO``
    #: suffix convention; indices use the ``^`` prefix. This mapping is
    #: intentionally ISOLATED here — it never enters the engine.
    YAHOO_SYMBOL_MAP: dict[str, str] = {
        "NIFTY": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "RELIANCE": "RELIANCE.NS",
        "TCS": "TCS.NS",
        "HDFCBANK": "HDFCBANK.NS",
        "ICICIBANK": "ICICIBANK.NS",
        "INFY": "INFY.NS",
        "AAPL": "AAPL",
        "MSFT": "MSFT",
    }

    #: Yahoo-supported intervals (subject to history limits). Only these
    #: are reported SUPPORTED; anything else is UNSUPPORTED (honest).
    SUPPORTED_INTERVALS: tuple[str, ...] = (
        "1m", "2m", "5m", "15m", "30m", "60m", "1h", "90m", "1D", "1d",
    )

    #: Maximum retrievable history (in days) Yahoo permits per interval,
    #: REDUCED by a small SAFETY MARGIN so the calculated ``start`` is
    #: comfortably inside the permitted window. Yahoo rejects a request
    #: whose range is "not within the last N days"; an exact-N-day
    #: boundary can fail because of clock skew / timezone differences, so
    #: we never sit on the boundary. Intervals not listed fall back to a
    #: conservative intraday cap (58 days). Daily data is effectively
    #: unlimited, so a generous cap is used.
    YAHOO_INTERVAL_MAX_DAYS: dict[str, int] = {
        "1m": 6,        # Yahoo ~7d -> 6d safe
        "2m": 58,       # Yahoo ~60d -> 58d safe
        "5m": 58,
        "15m": 58,
        "30m": 58,
        "60m": 725,     # Yahoo ~730d -> 725d safe
        "1h": 725,
        "90m": 58,
        "1D": 365 * 5,
        "1d": 365 * 5,
    }

    #: Conservative fallback cap for any intraday interval without an
    #: explicit entry above (Yahoo's common intraday limit class).
    _YAHOO_DEFAULT_INTRADAY_MAX_DAYS: int = 58

    #: Additional bars beyond ``lookback_bars`` the existing analysis
    #: engine may need for swing / market-structure / trend / context
    #: construction (the scanner's ``min_history`` default is 10, the
    #: fixture baseline is 20, swing lookback is small). A modest, fixed
    #: buffer keeps the requested window recent and bounded while still
    #: giving the engine enough history to produce meaningful structure.
    #: It is intentionally far below every interval's Yahoo limit.
    ENGINE_CONTEXT_BUFFER_BARS: int = 250

    #: Name of this data source (Product Phase 1 metadata).
    DATA_SOURCE: str = "yahoo"

    @property
    def data_source(self) -> str:
        return self.DATA_SOURCE

    def __init__(
        self,
        symbol_map: dict[str, str] | None = None,
        freshness_config: FreshnessConfig | None = None,
        provider: "object | None" = None,
    ) -> None:
        self._symbol_map = dict(symbol_map) if symbol_map else dict(
            self.YAHOO_SYMBOL_MAP,
        )
        self.freshness_config = freshness_config or FreshnessConfig()
        self._last_successful_fetch_time: datetime | None = None
        self._provider = provider
        self._init_error: str = ""
        if provider is None:
            try:
                from engine.data.yahoo_provider import (
                    YahooFinanceProvider,  # noqa
                )
                self._provider = YahooFinanceProvider()
                self._provider.connect()
            except Exception as exc:  # pragma: no cover - env dependent
                self._provider = None
                msg = f"{type(exc).__name__}: {exc}".strip()
                self._init_error = msg or "yfinance/pandas not installed"

    def resolve_symbol(self, instrument: str) -> str:
        """
        Map a canonical instrument to a Yahoo symbol (isolated here).

        Unknown instruments pass through verbatim.
        """

        return self._symbol_map.get(instrument, instrument)

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return setup_timeframe in self.SUPPORTED_INTERVALS

    def _interval_max_days(self, interval: str) -> int:
        """Yahoo's permitted history (days) for ``interval``, safety-margined.

        Falls back to the conservative intraday cap for any intraday
        interval without an explicit entry. Never sits on Yahoo's exact
        boundary (clock-skew / timezone safe).
        """

        return self.YAHOO_INTERVAL_MAX_DAYS.get(
            interval, self._YAHOO_DEFAULT_INTRADAY_MAX_DAYS,
        )

    def _lookback_window(
        self,
        interval: str,
        lookback_bars: int,
        *,
        reference_now: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """
        Recent, bounded Yahoo request window honoring ``lookback_bars``.

        The window span is derived from the candles the caller actually
        requires (``lookback_bars``) PLUS a modest
        :data:`ENGINE_CONTEXT_BUFFER_BARS` so the existing analysis engine
        has enough history for swing / structure / trend construction. The
        span in days is then CAPPED at the interval-specific Yahoo maximum
        (already safety-margined), so the request is always comfortably
        inside Yahoo's permitted range and never an exact boundary.

        This is why a ``15m`` / ``lookback_bars=50`` request yields a
        recent ~3-day window (300 bars * 15min) instead of an unnecessary
        60-day window that Yahoo rejects.
        """

        end = reference_now if reference_now is not None else datetime.now(
            self._provider_tz(),
        )
        max_days = self._interval_max_days(interval)
        # Duration of one candle for this interval (seconds). Unknown
        # durations conservatively use a 1-day candle so the span is
        # generous but still capped by ``max_days``.
        dur = timeframe_duration_seconds(interval)
        if dur is None or dur <= 0:
            dur = 86400
        required_bars = max(1, lookback_bars) + self.ENGINE_CONTEXT_BUFFER_BARS
        required_days = required_bars * dur / 86400.0
        span_days = min(required_days, float(max_days))
        start = end - timedelta(days=span_days)
        return start, end

    @staticmethod
    def _provider_tz():
        return UTC

    @staticmethod
    def _ensure_aware_utc(candle: OHLCVCandle) -> OHLCVCandle:
        """Return ``candle`` with a tz-aware UTC timestamp.

        The underlying Yahoo provider may return naive timestamps (e.g.
        daily data). A naive timestamp is interpreted as UTC and a new
        frozen candle is built with the aware timestamp. Already-aware
        timestamps are converted to UTC. The frozen OHLCVCandle model
        is NOT modified — this rebuilds an equivalent candle.
        """

        ts = candle.timestamp
        if ts.tzinfo is None:
            aware = ts.replace(tzinfo=UTC)
        else:
            aware = ts.astimezone(UTC)
        if aware is ts:
            return candle
        return OHLCVCandle(
            timestamp=aware,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )

    def _fetch_raw(
        self,
        symbol: str,
        interval: str,
        lookback_bars: int,
        *,
        reference_now: datetime | None = None,
    ) -> tuple[list[OHLCVCandle], ProviderStatus, str]:
        """Fetch raw candles from the underlying Yahoo provider."""

        if self._provider is None:
            return [], ProviderStatus.NOT_READY, (
                "Yahoo provider unavailable: optional dependency missing "
                f"or init failed ({self._init_error or 'yfinance/pandas not installed'})"
            )
        try:
            start, end = self._lookback_window(
                interval, lookback_bars, reference_now=reference_now,
            )
            candles = self._provider.get_history(
                symbol=symbol,
                start=start,
                end=end,
                interval=interval,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            return [], ProviderStatus.ERROR, (
                f"provider error: {type(exc).__name__}: {exc}"
            )
        if not candles:
            return [], ProviderStatus.EMPTY, (
                f"Yahoo returned no data for {symbol} {interval}"
            )
        # Normalize every candle timestamp to tz-aware UTC. The
        # underlying YahooFinanceProvider may return NAIVE timestamps
        # (e.g. daily data via ``to_pydatetime()``), while the dashboard
        # boundary + scanner compare against AWARE UTC datetimes. Mixing
        # naive/aware raises ``TypeError``; normalizing here at the
        # provider boundary keeps downstream comparisons clean and
        # deterministic without touching the frozen OHLCVCandle model.
        candles = [self._ensure_aware_utc(c) for c in candles]
        # Validate each candle via the existing DataValidator semantics;
        # impossible candles are dropped and reported (never crash).
        valid: list[OHLCVCandle] = []
        invalid_count = 0
        for c in candles:
            try:
                DataValidator.validate_candle(c)
                valid.append(c)
            except (ValueError, TypeError):
                invalid_count += 1
        if not valid:
            return [], ProviderStatus.ERROR, (
                f"all {len(candles)} candles were invalid for {symbol} {interval}"
            )
        if invalid_count:
            # Keep valid candles but surface the issue in the reason.
            return valid, ProviderStatus.OK, (
                f"{invalid_count} invalid candle(s) dropped for {symbol} {interval}"
            )
        return valid, ProviderStatus.OK, ""

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
        *,
        reference_now: datetime | None = None,
    ) -> InstrumentSeries:
        now = reference_now or datetime.now(UTC)
        data_source = "yahoo"

        if self._provider is None:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=(
                    "Yahoo provider unavailable: optional dependency "
                    f"missing or init failed ({self._init_error or 'yfinance/pandas not installed'})"
                ),
                data_source=data_source,
                provider_status=ProviderStatus.NOT_READY,
                freshness_state=FreshnessState.UNAVAILABLE,
                last_successful_fetch_time=self._last_successful_fetch_time,
            )
        if not self.is_timeframe_supported(setup_timeframe):
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=(
                    f"timeframe '{setup_timeframe}' not supported by Yahoo "
                    "provider"
                ),
                data_source=data_source,
                provider_status=ProviderStatus.UNSUPPORTED,
                freshness_state=FreshnessState.UNAVAILABLE,
                last_successful_fetch_time=self._last_successful_fetch_time,
            )

        symbol = self.resolve_symbol(instrument)
        interval = "1d" if setup_timeframe.upper() == "1D" else setup_timeframe

        setup_raw, setup_status, setup_reason = self._fetch_raw(
            symbol, interval, lookback_bars, reference_now=now,
        )
        if setup_status is not ProviderStatus.OK or not setup_raw:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=setup_reason or "no setup data",
                data_source=data_source,
                provider_status=setup_status,
                freshness_state=FreshnessState.UNAVAILABLE,
                last_successful_fetch_time=self._last_successful_fetch_time,
            )

        # --- Apply the completed-candle boundary to the setup series ---
        setup_boundary = split_completed_candles(setup_raw, setup_timeframe, now)
        setup = setup_boundary.completed
        if not setup:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=(
                    "no COMPLETED setup candles available (the latest "
                    "candle may still be forming)"
                ),
                data_source=data_source,
                provider_status=ProviderStatus.EMPTY,
                freshness_state=FreshnessState.UNAVAILABLE,
                latest_candle_timestamp=(
                    setup_raw[-1].timestamp if setup_raw else None
                ),
                forming_setup_candle=setup_boundary.forming,
                last_successful_fetch_time=self._last_successful_fetch_time,
                rejected_future_count=len(setup_boundary.rejected_future),
            )

        self._last_successful_fetch_time = now

        # --- Context timeframe (higher TF, best-effort, honest on miss) ---
        ctx_tf = context_timeframe_for(setup_timeframe)
        context: tuple[OHLCVCandle, ...] = ()
        ctx_reason = setup_reason
        if ctx_tf and ctx_tf.upper() != setup_timeframe.upper():
            if self.is_timeframe_supported(ctx_tf):
                ctx_interval = "1d" if ctx_tf.upper() == "1D" else ctx_tf
                ctx_raw, ctx_status, ctx_r = self._fetch_raw(
                    symbol, ctx_interval, lookback_bars, reference_now=now,
                )
                if ctx_status is ProviderStatus.OK and ctx_raw:
                    ctx_boundary = split_completed_candles(
                        ctx_raw, ctx_tf, now,
                    )
                    context = ctx_boundary.completed
                    if ctx_r:
                        ctx_reason = ctx_r
                else:
                    ctx_reason = ctx_r or f"context fetch failed for {ctx_tf}"
            else:
                ctx_reason = f"context timeframe '{ctx_tf}' unsupported"

        latest_completed = setup_boundary.latest_completed_timestamp
        freshness = classify_freshness(
            latest_completed_timestamp=latest_completed,
            reference_now=now,
            staleness_seconds=self.freshness_config.staleness_seconds_for(
                setup_timeframe,
            ),
            provider_status=ProviderStatus.OK,
        )
        return InstrumentSeries(
            instrument=instrument,
            context_candles=context,
            setup_candles=setup,
            available=True,
            reason=ctx_reason,
            data_source=data_source,
            provider_status=ProviderStatus.OK,
            freshness_state=freshness,
            latest_candle_timestamp=setup_raw[-1].timestamp,
            latest_completed_candle_timestamp=latest_completed,
            forming_setup_candle=setup_boundary.forming,
            last_successful_fetch_time=self._last_successful_fetch_time,
            rejected_future_count=len(setup_boundary.rejected_future),
        )

    def last_updated(
        self, instrument: str, setup_timeframe: str,
    ) -> datetime | None:
        series = self.fetch(instrument, setup_timeframe)
        if not series.setup_candles:
            return None
        return series.latest_completed_candle_timestamp


# ============================================================
# FACTORY
# ============================================================


def make_provider(
    name: str = "fixture",
    *,
    freshness_config: FreshnessConfig | None = None,
    symbol_map: dict[str, str] | None = None,
) -> DashboardDataProvider:
    """
    Build a dashboard data provider by name.

    ``"fixture"`` (default) -> deterministic local fixtures.
    ``"yahoo"`` -> optional live Yahoo provider (graceful on failure).

    Unknown names fall back to the fixture provider (the dashboard
    always remains runnable). A failed live provider NEVER silently
    falls back to fixture data in a live request — the failure is
    reported honestly via the :class:`InstrumentSeries` status fields.
    """

    name = (name or "fixture").lower()
    if name == "yahoo":
        return YahooDataProvider(
            symbol_map=symbol_map,
            freshness_config=freshness_config,
        )
    return FixtureDataProvider(freshness_config=freshness_config)


__all__ = [
    "CandleBoundaryResult",
    "DashboardDataProvider",
    "FIXTURE_INSTRUMENTS",
    "FixtureDataProvider",
    "FreshnessConfig",
    "FreshnessState",
    "InstrumentSeries",
    "ProviderStatus",
    "SUPPORTED_TIMEFRAMES",
    "TIMEFRAME_DURATION_SECONDS",
    "YahooDataProvider",
    "classify_freshness",
    "context_timeframe_for",
    "make_provider",
    "split_completed_candles",
    "timeframe_duration_seconds",
]
