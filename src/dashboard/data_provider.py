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

No broker integration. No order execution. No live trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from engine.models.ohlcv import OHLCVCandle


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
        Higher-timeframe candles (may be empty -> scan INCOMPLETE).

    setup_candles
        Setup-timeframe candles (may be empty -> scan INCOMPLETE).

    available
        Whether at least the setup timeframe carried usable data.

    reason
        Human-readable reason when unavailable (e.g. "unsupported
        timeframe for fixture provider", "provider error: ...").
    """

    instrument: str
    context_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    setup_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    available: bool = False
    reason: str = ""


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

    def __init__(self) -> None:
        # Lazy import so the dashboard module loads even if a fixture
        # helper were unavailable (it never is — fixtures are stdlib).
        from engine.data.historical_fixtures import (
            historical_candles_by_instrument,
        )

        self._cache = historical_candles_by_instrument(
            FIXTURE_INSTRUMENTS, "1D", "15M",
        )

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return setup_timeframe in ("15M", "15m")

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
    ) -> InstrumentSeries:
        if instrument not in self._cache:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=f"instrument '{instrument}' not in fixture set",
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
            )
        pair = self._cache[instrument]
        setup = tuple(pair.get("15M", ()))
        context = tuple(pair.get("1D", ()))
        return InstrumentSeries(
            instrument=instrument,
            context_candles=context,
            setup_candles=setup,
            available=bool(setup),
            reason="" if setup else "empty setup series",
        )

    def last_updated(
        self, instrument: str, setup_timeframe: str,
    ) -> datetime | None:
        series = self.fetch(instrument, setup_timeframe)
        if not series.setup_candles:
            return None
        return series.setup_candles[-1].timestamp


# ============================================================
# YAHOO PROVIDER (optional, graceful)
# ============================================================


class YahooDataProvider:
    """
    OPTIONAL live market-data provider reusing the existing
    :class:`YahooFinanceProvider`.

    This provider is only used when explicitly selected AND the optional
    ``yfinance`` / ``pandas`` dependencies are importable. ANY failure
    (missing dependency, network error, unsupported intraday interval,
    unknown symbol, empty response) is caught and reported as
    ``available=False`` with a reason — the dashboard NEVER crashes and
    NEVER fabricates data.

    Yahoo Finance intraday intervals are limited (typically up to 60
    days for <=5m, up to 730 days for <=1h). The provider requests a
    best-effort lookback window; if Yahoo returns no data the result is
    honestly unavailable.
    """

    def __init__(self) -> None:
        self._provider = None
        self._init_error: str = ""
        try:
            from engine.data.yahoo_provider import YahooFinanceProvider  # noqa
            self._provider = YahooFinanceProvider()
            self._provider.connect()
        except Exception as exc:  # pragma: no cover - environment dependent
            self._provider = None
            self._init_error = f"{type(exc).__name__}: {exc}"

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        # yfinance supports these intervals (subject to history limits).
        return setup_timeframe in (
            "1m", "2m", "5m", "15m", "30m", "60m", "1h", "90m", "1D", "1d",
        )

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
    ) -> InstrumentSeries:
        if self._provider is None:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=(
                    "Yahoo provider unavailable: optional dependency "
                    f"missing or init failed ({self._init_error or 'yfinance/pandas not installed'})"
                ),
            )
        if not self.is_timeframe_supported(setup_timeframe):
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=f"timeframe '{setup_timeframe}' not supported by Yahoo provider",
            )
        try:
            interval = "1d" if setup_timeframe.upper() == "1D" else setup_timeframe
            end = datetime.now()
            # Heuristic lookback window honoring Yahoo's intraday limits.
            if interval == "1d":
                start = end - timedelta(days=max(lookback_bars, 365))
            elif interval in ("1h", "60m"):
                start = end - timedelta(days=730)
            else:
                start = end - timedelta(days=60)
            candles = self._provider.get_history(
                symbol=instrument,
                start=start,
                end=end,
                interval=interval,
            )
            if not candles:
                return InstrumentSeries(
                    instrument=instrument,
                    available=False,
                    reason=f"Yahoo returned no data for {instrument} {setup_timeframe}",
                )
            setup = tuple(candles)
            # Context = a higher timeframe when supported; otherwise the
            # scan is INCOMPLETE for this instrument (honest).
            ctx_tf = context_timeframe_for(setup_timeframe)
            context: tuple[OHLCVCandle, ...] = ()
            ctx_reason = ""
            if ctx_tf and ctx_tf.upper() != setup_timeframe.upper():
                if self.is_timeframe_supported(ctx_tf):
                    try:
                        ctx_interval = (
                            "1d" if ctx_tf.upper() == "1D" else ctx_tf
                        )
                        if ctx_interval == "1d":
                            cstart = end - timedelta(days=730)
                        else:
                            cstart = end - timedelta(days=60)
                        ctx_candles = self._provider.get_history(
                            symbol=instrument,
                            start=cstart,
                            end=end,
                            interval=ctx_interval,
                        )
                        context = tuple(ctx_candles)
                    except Exception as exc:  # pragma: no cover
                        ctx_reason = f"context fetch failed: {exc}"
                else:
                    ctx_reason = f"context timeframe '{ctx_tf}' unsupported"
            reason = ctx_reason
            return InstrumentSeries(
                instrument=instrument,
                context_candles=context,
                setup_candles=setup,
                available=True,
                reason=reason,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason=f"provider error: {type(exc).__name__}: {exc}",
            )

    def last_updated(
        self, instrument: str, setup_timeframe: str,
    ) -> datetime | None:
        series = self.fetch(instrument, setup_timeframe)
        if not series.setup_candles:
            return None
        return series.setup_candles[-1].timestamp


# ============================================================
# FACTORY
# ============================================================


def make_provider(name: str = "fixture") -> DashboardDataProvider:
    """
    Build a dashboard data provider by name.

    ``"fixture"`` (default) -> deterministic local fixtures.
    ``"yahoo"`` -> optional live Yahoo provider (graceful on failure).

    Unknown names fall back to the fixture provider (the dashboard
    always remains runnable).
    """

    name = (name or "fixture").lower()
    if name == "yahoo":
        return YahooDataProvider()
    return FixtureDataProvider()


__all__ = [
    "DashboardDataProvider",
    "FIXTURE_INSTRUMENTS",
    "FixtureDataProvider",
    "InstrumentSeries",
    "SUPPORTED_TIMEFRAMES",
    "YahooDataProvider",
    "context_timeframe_for",
    "make_provider",
]
