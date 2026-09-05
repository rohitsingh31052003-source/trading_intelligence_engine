"""
Indian (NSE) market-session awareness for intraday data freshness
(Checkpoint 19.2).

This module is DATA / PRODUCT-STATE infrastructure only. It implements
NO trading, scoring, prediction, decision or execution logic. It exists
to give the intraday market-data layer a deterministic, documented,
timezone-correct answer to:

* "Is the Indian market currently in (or near) a trading session?"
* "How old may the latest completed candle be before it is considered
  stale, given the current session state?"

The NSE cash-equity trading session modeled here is the canonical,
deterministically-representable session used by the existing scanner
architecture:

    Session        : 09:15 IST -> 15:30 IST
    UTC anchor     : 03:45 UTC  -> 10:00 UTC   (IST = UTC + 05:30)
    Trading days   : Monday - Friday
    Lunch break    : 12:00 IST -> 13:00 IST (equity trading pauses but
                     the SESSION IS TREATED AS CONTINUOUS here — this is
                     a documented simplification; the pause never
                     invalidates a 15-minute candle series).

All functions are PURE and DETERMINISTIC on their inputs; ``reference_now``
is always an explicit parameter (never wall-clock). Naive datetimes are
REJECTED (never silently accepted), matching the repository's timestamp
discipline.

No network, no exchange calendar service, no API key, no broker
dependency. This is configuration + arithmetic only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from engine.data.historical_times import (
    HISTORICAL_TIMEFRAME_SECONDS,
    canonical_timeframe,
    timeframe_seconds,
)

#: The Indian timezone used for NSE session boundaries (stdlib zoneinfo,
#: no pytz, no network).
IST_TZ: ZoneInfo = ZoneInfo("Asia/Kolkata")

#: Local session boundaries in IST (wall-clock day-labelled).
NSE_SESSION_OPEN_IST: datetime = datetime(2000, 1, 1, 9, 15, tzinfo=IST_TZ)
NSE_SESSION_CLOSE_IST: datetime = datetime(2000, 1, 1, 15, 30, tzinfo=IST_TZ)

#: Session boundary minute-of-day IN IST (09:15 IST = 555, 15:30 IST =
#: 930). The classifier compares the IST wall-clock minute-of-day
#: against these, so the constants are always the local-time values
#: regardless of the UTC offset representation.
_NSE_OPEN_MINUTE_IST: int = (
    NSE_SESSION_OPEN_IST.hour * 60 + NSE_SESSION_OPEN_IST.minute
)
_NSE_CLOSE_MINUTE_IST: int = (
    NSE_SESSION_CLOSE_IST.hour * 60 + NSE_SESSION_CLOSE_IST.minute
)

#: Trading days of the week (Monday = 0 ... Sunday = 6).
TRADING_WEEKDAYS: frozenset[int] = frozenset(range(0, 5))


class MarketSessionState(Enum):
    """
    Deterministic classification of a reference instant relative to the
    NSE cash-equity session.

    OPEN
        A trading weekday and the local time is inside
        ``09:15 -> 15:30`` IST.

    PRE_OPEN
        A trading weekday before 09:15 IST (the pre-market window).

    POST_CLOSE
        A trading weekday at/after 15:30 IST (after the session close;
        also covers the small "post-close" window on a trading day).

    WEEKEND
        Saturday or Sunday (in IST). The market is closed.

    UNKNOWN
        The reference instant could not be classified (e.g. a naive
        datetime). Never treated as a trading session.
    """

    OPEN = "OPEN"
    PRE_OPEN = "PRE_OPEN"
    POST_CLOSE = "POST_CLOSE"
    WEEKEND = "WEEKEND"
    UNKNOWN = "UNKNOWN"


class SessionAwareFreshness(Enum):
    """
    Data-quality freshness of an intraday series RELATIVE TO THE NSE
    SESSION (Checkpoint 19.2). This is DATA QUALITY / PRODUCT STATE —
    NOT a trading signal. It never alters the intelligence engine's
    decision semantics; it only grades how recent the data is given
    the market's open/closed state.

    CURRENT
        The latest completed candle is recent enough for the session
        state (its age is within the documented staleness window).

    STALE
        Valid data exists but the latest completed candle is older than
        the documented staleness window for the session state.

    UNAVAILABLE
        No usable completed candle exists (nothing to grade).
    """

    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SessionFreshnessConfig:
    """
    Documented staleness thresholds for session-aware freshness.

    Attributes:

    open_staleness_multiplier
        While the market is OPEN, the latest completed candle is CURRENT
        when its age is at most this many candle-durations. Default 2.0
        means "at most two candles old" (e.g. a 15m candle may be up to
        30 minutes old while the market is open — two 15m intervals —
        which is enough to cover slight data latency without accepting a
        stale bar).

    closed_staleness_seconds
        While the market is CLOSED (PRE_OPEN / POST_CLOSE on a trading
        weekday), data is CURRENT when the latest completed candle is at
        most this old. Default 26h comfortably spans overnight /
        pre-open without degrading to "stale" for the prior session.

    weekend_staleness_seconds
        Over a WEEKEND, data is CURRENT when the latest completed candle
        is at most this old. Default 96h spans Friday close through
        Sunday and into Monday pre-open.
    """

    open_staleness_multiplier: float = 2.0
    closed_staleness_seconds: int = 26 * 3600
    weekend_staleness_seconds: int = 4 * 24 * 3600

    def __post_init__(self) -> None:
        if self.open_staleness_multiplier <= 0:
            raise ValueError("open_staleness_multiplier must be positive.")
        if self.closed_staleness_seconds <= 0:
            raise ValueError("closed_staleness_seconds must be positive.")
        if self.weekend_staleness_seconds <= 0:
            raise ValueError("weekend_staleness_seconds must be positive.")


def ist_now(reference_now: datetime) -> datetime:
    """
    Convert an AWARE reference instant to the IST wall-clock used by the
    session classifier.

    Raises ``ValueError`` for a naive datetime (never silently accepted).
    """

    _require_aware(reference_now, "reference_now")
    return reference_now.astimezone(IST_TZ)


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")
    if value.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware (naive datetimes are never "
            "silently accepted).",
        )


def is_trading_weekday(day: datetime) -> bool:
    """
    True when ``day`` is a Monday-Friday (in the IST calendar).

    ``day`` must be aware; it is converted to IST before the weekday check.
    """

    _require_aware(day, "day")
    return day.astimezone(IST_TZ).weekday() in TRADING_WEEKDAYS


def market_session_state(reference_now: datetime) -> MarketSessionState:
    """
    Classify ``reference_now`` against the NSE session
    (DETERMINISTIC, pure).

    * naive input -> ``UNKNOWN``;
    * Saturday/Sunday (IST) -> ``WEEKEND``;
    * trading weekday before 09:15 IST -> ``PRE_OPEN``;
    * trading weekday 09:15 <= t < 15:30 IST -> ``OPEN``;
    * trading weekday at/after 15:30 IST -> ``POST_CLOSE``.

    The classification is in IST, computed from the aware UTC instant —
    it never depends on wall-clock time.
    """

    try:
        local = ist_now(reference_now)
    except (TypeError, ValueError):
        return MarketSessionState.UNKNOWN
    minute = local.hour * 60 + local.minute
    if local.weekday() not in TRADING_WEEKDAYS:
        return MarketSessionState.WEEKEND
    if minute < _NSE_OPEN_MINUTE_IST:
        return MarketSessionState.PRE_OPEN
    if minute < _NSE_CLOSE_MINUTE_IST:
        return MarketSessionState.OPEN
    return MarketSessionState.POST_CLOSE


def staleness_seconds(
    reference_now: datetime,
    timeframe: str,
    config: SessionFreshnessConfig | None = None,
) -> int | None:
    """
    Deterministic staleness threshold (seconds) for a timeframe given the
    session state at ``reference_now``.

    Returns ``None`` when the timeframe is unknown or ``reference_now``
    is naive (nothing to grade).
    """

    cfg = config or SessionFreshnessConfig()
    state = market_session_state(reference_now)
    if state is MarketSessionState.UNKNOWN:
        return None
    dur = timeframe_seconds(timeframe)
    # Unknown durations conservatively use a 1-day candle so the OPEN
    # threshold is generous but still bounded.
    if dur is None or dur <= 0:
        dur = 86400
    if state is MarketSessionState.OPEN:
        return int(dur * cfg.open_staleness_multiplier)
    if state is MarketSessionState.WEEKEND:
        return cfg.weekend_staleness_seconds
    # PRE_OPEN / POST_CLOSE
    return cfg.closed_staleness_seconds


def session_aware_freshness(
    reference_now: datetime,
    latest_completed_timestamp: datetime | None,
    timeframe: str,
    config: SessionFreshnessConfig | None = None,
) -> SessionAwareFreshness:
    """
    Classify the freshness of a completed-candle series
    (DETERMINISTIC, pure, session-aware).

    * no completed candle -> ``UNAVAILABLE``;
    * naive reference / naive latest -> ``STALE`` (cannot be verified,
      never classified CURRENT);
    * latest candle age within the session-aware threshold -> ``CURRENT``;
    * otherwise -> ``STALE``.
    """

    if latest_completed_timestamp is None:
        return SessionAwareFreshness.UNAVAILABLE
    try:
        _require_aware(reference_now, "reference_now")
        _require_aware(latest_completed_timestamp, "latest_completed_timestamp")
    except (TypeError, ValueError):
        return SessionAwareFreshness.STALE
    threshold = staleness_seconds(reference_now, timeframe, config)
    if threshold is None:
        return SessionAwareFreshness.STALE
    age = (reference_now - latest_completed_timestamp).total_seconds()
    if age < 0:
        # Future-dated final candle: NOT valid freshness — grade stale.
        return SessionAwareFreshness.STALE
    if age <= threshold:
        return SessionAwareFreshness.CURRENT
    return SessionAwareFreshness.STALE


def seconds_until_next_open(reference_now: datetime) -> timedelta | None:
    """
    Deterministic estimate of time until the next NSE cash-equity open.

    Returns ``None`` when ``reference_now`` is naive. The estimate is
    arithmetic over the IST calendar (no exchange-holiday service); a
    holiday is NOT accounted for (documented simplification). When the
    market is currently OPEN the result is ``timedelta(0)``.
    """

    try:
        local = ist_now(reference_now)
    except (TypeError, ValueError):
        return None
    if market_session_state(reference_now) is MarketSessionState.OPEN:
        return timedelta(0)
    # Next trading weekday 09:15 IST.
    probe = datetime(local.year, local.month, local.day, 9, 15, tzinfo=IST_TZ)
    days_ahead = 0
    for offset in range(8):
        candidate = probe + timedelta(days=offset)
        if candidate.weekday() in TRADING_WEEKDAYS and candidate > local:
            return candidate - local
        days_ahead = offset
    # Fallback: conservatively 8 days (never an infinite loop).
    return probe + timedelta(days=days_ahead + 1) - local


INTRADAY_TIMEFRAMES: tuple[str, ...] = tuple(
    tf for tf in HISTORICAL_TIMEFRAME_SECONDS if tf != "1D"
)


__all__ = [
    "IST_TZ",
    "INTRADAY_TIMEFRAMES",
    "MarketSessionState",
    "NSE_SESSION_CLOSE_IST",
    "NSE_SESSION_OPEN_IST",
    "SessionAwareFreshness",
    "SessionFreshnessConfig",
    "TRADING_WEEKDAYS",
    "is_trading_weekday",
    "ist_now",
    "market_session_state",
    "seconds_until_next_open",
    "session_aware_freshness",
    "staleness_seconds",
]