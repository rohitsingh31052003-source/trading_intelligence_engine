"""
Historical market-data provider abstraction (Product Phase 6A).

This module defines the provider contract for the HISTORICAL data path.
It is deliberately separate from the live/near-live dashboard providers:
the existing ``YahooDataProvider`` remains the LIVE provider; providers
below only ever serve HISTORICAL requests.

PROVIDER STRATEGY (per Product Phase 6A §3/§4):

* A replaceable abstraction (:class:`HistoricalMarketDataProvider`
  protocol) so the research layer never depends on provider internals.
* :class:`InMemoryHistoricalProvider` — an IMPORT provider fed with
  caller-supplied records/candles. Deterministic; ideal for tests and
  CLI import scenarios. No API key required, no internet dependency.
* :class:`DeterministicLocalHistoricalProvider` — a LOCAL deterministic
  provider that synthesizes a repeatable candle series from a hash-seed
  of (instrument, timeframe, timestamps). It exists so the CLI/demo can
  run out-of-the-box without network access. It is clearly labelled
  ``"local-deterministic"`` and its data is synthetic by name; it is
  NEVER used to repair validation gaps.
* :class:`YahooHistoricalDataProvider` — OPTIONAL, reuses the existing
  :class:`engine.data.yahoo_provider.YahooFinanceProvider` (which
  natively accepts start/end/interval). Only active when
  ``yfinance``/``pandas`` are importable; graceful on ANY failure.
  Yahoo remains the live provider; this adapter is additive.

The provider must be replaceable without changing the research layer —
the service depends only on the protocol.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Mapping, Protocol, Sequence

from engine.config.universe import BENCHMARK_INDEX, COMBINED_UNIVERSE
from engine.data.historical_times import (
    canonical_timeframe,
    supported_timeframes,
    timeframe_seconds,
)
from engine.models.historical_data import (
    HistoricalDataRequest,
    ProviderResponseStatus,
)
from engine.models.ohlcv import OHLCVCandle


@dataclass(frozen=True)
class HistoricalProviderResponse:
    """
    A provider's raw response to a historical request.

    The provider returns RAW candles (which may be unordered, duplicated
    or malformed) together with a coarse status. Normalization /
    validation belong to the service layer below; the provider only
    transports data.

    Attributes:

    provider_name
        Name of the provider that produced the response.

    status
        :class:`ProviderResponseStatus`.

    candles
        Raw candles (possibly empty). Unordered input is normalized to
        chronological order downstream.

    reason
        Human-readable reason when not OK (honest, never raised).
    """

    provider_name: str
    status: ProviderResponseStatus
    candles: tuple[OHLCVCandle, ...] = ()
    reason: str = ""


class HistoricalMarketDataProvider(Protocol):
    """
    Minimal provider contract the historical service depends on.

    Implementations fetch OHLCV candles for an instrument / timeframe /
    [start, end] window. Failures are reported via the response status
    — never raised.
    """

    provider_name: str

    def is_available(self) -> bool: ...

    def supports(self, instrument: str, timeframe: str) -> bool: ...

    def fetch(
        self,
        request: HistoricalDataRequest,
    ) -> HistoricalProviderResponse: ...


# ============================================================
# IN-MEMORY IMPORT PROVIDER (deterministic; tests / imports)
# ============================================================


class InMemoryHistoricalProvider:
    """
    An import provider fed with caller-supplied records.

    Records are keyed by ``(instrument, timeframe)``; any sequence of
    :class:`OHLCVCandle` objects (even unordered / with duplicates) is
    accepted. This provider exists for deterministic tests and for CLI
    import scenarios — it has NO network dependency and NO API key.
    """

    provider_name = "in-memory-import"

    def __init__(
        self,
        records: Mapping[tuple[str, str], Sequence[OHLCVCandle]] | None = None,
    ) -> None:
        self._records: dict[tuple[str, str], tuple[OHLCVCandle, ...]] = {}
        if records:
            for (instrument, timeframe), candles in records.items():
                self._records[(instrument.upper(), timeframe)] = tuple(candles)

    def is_available(self) -> bool:
        return True

    def supports(self, instrument: str, timeframe: str) -> bool:
        return canonical_timeframe(timeframe) is not None

    def add(
        self,
        instrument: str,
        timeframe: str,
        candles: Sequence[OHLCVCandle],
    ) -> None:
        key = (instrument.upper(), timeframe)
        self._records[key] = tuple(
            (self._records.get(key, ()) + tuple(candles)),
        )

    def fetch(
        self,
        request: HistoricalDataRequest,
    ) -> HistoricalProviderResponse:
        key = (request.instrument, request.timeframe)
        candles = self._records.get(key, ())
        if not candles:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                candles=(),
                reason=(
                    f"no records imported for {request.instrument} "
                    f"{request.timeframe}"
                ),
            )
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=tuple(candles),
        )


# ============================================================
# DETERMINISTIC LOCAL PROVIDER (synthetic, no network / no API key)
# ============================================================


class DeterministicLocalHistoricalProvider:
    """
    A deterministic local provider for out-of-the-box CLI / demo use.

    Given a request it synthesizes a REPEATABLE candle series derived
    from a hash of ``(instrument, timeframe)``. Identical requests
    always produce identical series (deterministic). The provider name
    (``"local-deterministic"``) makes the synthetic origin explicit —
    and the data is never used to fill validation gaps.

    This provider exists because no real historical vendor is configured
    in the repository; it keeps tests and demos network-free and keyless.
    """

    provider_name = "local-deterministic"

    #: Simulated deterministic gap shape injected into the series:
    #: 0 = no gaps; when >0, every N-th candle is skipped (gives the
    #: demo/tests a realistic mix without any randomness).
    skip_every_n: int = 0

    def __init__(self, skip_every_n: int = 0) -> None:
        self.skip_every_n = max(0, int(skip_every_n))

    def is_available(self) -> bool:
        return True

    def supports(self, instrument: str, timeframe: str) -> bool:
        return canonical_timeframe(timeframe) is not None

    def fetch(
        self,
        request: HistoricalDataRequest,
    ) -> HistoricalProviderResponse:
        step = timeframe_seconds(request.timeframe)
        if step is None:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.UNSUPPORTED,
                reason=f"unsupported timeframe {request.timeframe!r}",
            )
        seed = int.from_bytes(
            hashlib.sha256(
                f"{request.instrument}|{request.timeframe}".encode(),
            ).digest()[:8],
            "big",
        )
        candles: list[OHLCVCandle] = []
        ts = request.start
        index = 0
        while ts <= request.end:
            index += 1
            if not self.skip_every_n or index % self.skip_every_n != 0:
                candles.append(self._synth_candle(seed, ts, index))
            ts = ts + timedelta(seconds=step)
        if not candles:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                candles=(),
                reason="window produced no candles.",
            )
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=tuple(candles),
        )

    @staticmethod
    def _synth_candle(seed: int, ts: datetime, index: int) -> OHLCVCandle:
        """Deterministic, OHLC-valid synthetic candle."""

        base = 1000.0 + (seed % 1000)
        drift = ((index * 37 + seed) % 89) - 44  # bounded [-44, +44]
        open_price = round(base + drift, 2)
        spread = 1.0 + ((seed + index) % 40) / 10.0
        close_price = round(open_price + ((index % 3) - 1) * 0.8, 2)
        high = round(max(open_price, close_price) + spread, 2)
        low = round(min(open_price, close_price) - spread, 2)
        volume = float(1000 + ((seed + 17 * index) % 9000))
        return OHLCVCandle(
            timestamp=ts,
            open=open_price,
            high=high,
            low=low,
            close=close_price,
            volume=volume,
        )


# ============================================================
# OPTIONAL YAHOO HISTORICAL PROVIDER (graceful; reuse existing engine)
# ============================================================


#: Yahoo symbols for the benchmark index instruments (indices use the
#: ``^`` prefix). Provider-specific Yahoo formatting, isolated here —
#: it never enters the research/service layers.
_INDEX_YAHOO_SYMBOLS: dict[str, str] = {
    "NIFTY": "^NSEI",
}


def _yahoo_timestamp_to_utc(timestamp: datetime) -> datetime | None:
    """Normalize a Yahoo/yfinance timestamp to timezone-aware UTC.

    yfinance returns timezone-naive timestamps for daily/weekly/monthly
    data and timezone-aware timestamps (exchange-local tz) for intraday
    data. Provider-specific formatting stays inside the provider layer:
    naive values are interpreted as UTC (Yahoo daily bars are
    exchange-day labels carrying no clock time) and aware values are
    converted to UTC. This runs BEFORE the canonical ``OHLCVCandle`` is
    created, so the existing NAIVE_TIMESTAMP validation is never
    weakened or bypassed. Returns ``None`` for non-datetime values.
    """

    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _default_yahoo_symbol_map() -> dict[str, str]:
    """Default instrument -> Yahoo symbol map for the canonical universe.

    Derived from the single canonical universe source
    (:mod:`engine.config.universe`): every NIFTY 50 ∪ SENSEX constituent
    resolves to its ``<NSE symbol>.NS`` Yahoo symbol and the benchmark
    index instruments resolve to their ``^``-prefixed Yahoo symbols.
    No second universe is defined here — only provider-specific Yahoo
    formatting of the existing canonical universe.
    """

    mapping = {name: f"{name}.NS" for name in COMBINED_UNIVERSE}
    for name in BENCHMARK_INDEX:
        mapping[name] = _INDEX_YAHOO_SYMBOLS.get(name, name)
    return mapping


class YahooHistoricalDataProvider:
    """
    OPTIONAL Yahoo-backed historical provider.

    Reuses the existing :class:`YahooFinanceProvider`'s
    ``get_history(symbol, start, end, interval)`` path. It is only
    available when ``yfinance``/``pandas`` are importable; ANY failure
    (network, missing symbol, unsupported interval) is reported via the
    response status — never raised.

    Yahoo remains the LIVE provider for the dashboard; this adapter is
    additive and isolated behind the historical abstraction.

    SYMBOL MAPPING (isolated inside the provider): canonical instrument
    names (``"NIFTY"``, ``"RELIANCE"``, ...) are mapped to Yahoo symbols
    (``"^NSEI"``, ``"RELIANCE.NS"``, ...) INSIDE this provider via the
    default map derived from the canonical universe
    (:mod:`engine.config.universe`) or the ``symbol_map`` constructor
    argument. Unknown instruments pass through verbatim (Yahoo accepts
    many tickers directly, e.g. ``AAPL``).
    """

    provider_name = "yahoo-historical"

    #: Yahoo interval for a canonical timeframe label (native only —
    #: no resampling / fabrication).
    _INTERVALS: dict[str, str] = {
        "1m": "1m",
        "2m": "2m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "90m": "90m",
        "1D": "1d",
    }

    #: Per-interval Yahoo window cap in days (SAFETY-MARGINED). Yahoo
    #: rejects an intraday request whose START is older than its
    #: retention window (~7 days for 1m, ~60 days for <=30m/90m, ~730
    #: days for hourly). Daily data is retained for decades. Mirrors
    #: the established live-provider range fix; provider-specific Yahoo
    #: formatting stays inside this provider.
    _INTERVAL_LIMIT_DAYS: dict[str, int] = {
        "1m": 6,
        "2m": 58,
        "5m": 58,
        "15m": 58,
        "30m": 58,
        "90m": 58,
        "1h": 725,
        "1D": 1825,
    }

    #: Intervals whose retention window is limited (older data does not
    #: exist on Yahoo at all). Daily data has no practical retention
    #: limit, so the retention floor never applies to it.
    _INTRADAY_LIMITED: frozenset = frozenset(
        {"1m", "2m", "5m", "15m", "30m", "90m", "1h"},
    )

    def __init__(self, provider=None, symbol_map: dict[str, str] | None = None) -> None:
        self._symbol_map = (
            dict(symbol_map) if symbol_map else _default_yahoo_symbol_map()
        )
        if provider is not None:
            self._provider = provider
        else:
            self._provider = None
            try:
                from engine.data.yahoo_provider import YahooFinanceProvider

                self._provider = YahooFinanceProvider()
            except Exception:  # pragma: no cover - dependency missing
                self._provider = None

    def is_available(self) -> bool:
        return self._provider is not None

    def resolve_symbol(self, instrument: str) -> str:
        return self._symbol_map.get(instrument, instrument)

    def supports(self, instrument: str, timeframe: str) -> bool:
        canonical = canonical_timeframe(timeframe)
        return canonical in self._INTERVALS

    def fetch(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
    ) -> HistoricalProviderResponse:
        if self._provider is None:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.ERROR,
                reason=(
                    "Yahoo historical provider unavailable: optional "
                    "dependency missing (yfinance/pandas not installed)."
                ),
            )
        canonical = canonical_timeframe(request.timeframe)
        if canonical not in self._INTERVALS:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.UNSUPPORTED,
                reason=f"timeframe {request.timeframe!r} not supported by Yahoo.",
            )
        symbol = self.resolve_symbol(request.instrument)
        interval = self._INTERVALS[canonical]
        max_days = self._INTERVAL_LIMIT_DAYS[canonical]
        anchor = reference_now or datetime.now(UTC)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        # RETENTION FLOOR: Yahoo rejects an intraday request whose start
        # is older than its retention window, so the requested start is
        # clipped to the retrievable boundary. Data before the floor
        # genuinely does not exist on Yahoo and is NEVER fabricated; the
        # response reason reports the clip honestly.
        effective_start = request.start
        clipped = False
        if canonical in self._INTRADAY_LIMITED:
            floor = anchor - timedelta(days=max_days)
            if request.end <= floor:
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.EMPTY,
                    candles=(),
                    reason=(
                        f"Yahoo retains {interval} data for approximately "
                        f"the last {max_days} days; the requested range "
                        f"({request.start.date()} -> {request.end.date()}) "
                        "is entirely outside that retention window."
                    ),
                )
            if request.start < floor:
                effective_start = floor
                clipped = True
        # WINDOWED FETCH: split long ranges into per-interval windows
        # Yahoo accepts, then merge + dedupe + sort chronologically.
        windows: list[tuple[datetime, datetime]] = []
        cursor = effective_start
        while cursor < request.end:
            windows.append(
                (cursor, min(cursor + timedelta(days=max_days), request.end)),
            )
            cursor = cursor + timedelta(days=max_days)
        merged: dict[str, OHLCVCandle] = {}
        errors: list[str] = []
        for window_start, window_end in windows:
            try:
                window_candles = self._provider.get_history(
                    symbol, window_start, window_end, interval,
                )
            except Exception as exc:
                errors.append(str(exc))
                continue
            for candle in window_candles or []:
                # Normalize provider-specific timestamps to
                # timezone-aware UTC BEFORE the canonical OHLCVCandle
                # objects reach validation (naive -> UTC, aware -> UTC).
                ts = _yahoo_timestamp_to_utc(
                    getattr(candle, "timestamp", None))
                if ts is None:
                    continue  # malformed record; the service reports counts
                merged[ts.isoformat()] = (
                    candle
                    if ts is candle.timestamp
                    else replace(candle, timestamp=ts)
                )
        candles = sorted(merged.values(), key=lambda c: c.timestamp)
        if not candles:
            if errors and len(errors) == len(windows):
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.ERROR,
                    reason=f"provider error: {errors[0]}",
                )
            reason = "Yahoo returned no candles for the request."
            if clipped:
                reason += (
                    f" Yahoo retains {interval} data for approximately "
                    f"the last {max_days} days; older portions of the "
                    "requested range are unavailable from Yahoo."
                )
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                candles=(),
                reason=reason,
            )
        reason = ""
        if clipped:
            reason = (
                f"Requested start predates Yahoo's {interval} retention "
                f"window (~{max_days} days); data before "
                f"{effective_start.date()} is unavailable from Yahoo and "
                "was not fabricated."
            )
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=tuple(candles),
            reason=reason,
        )


__all__ = [
    "DeterministicLocalHistoricalProvider",
    "HistoricalMarketDataProvider",
    "HistoricalProviderResponse",
    "InMemoryHistoricalProvider",
    "YahooHistoricalDataProvider",
]
