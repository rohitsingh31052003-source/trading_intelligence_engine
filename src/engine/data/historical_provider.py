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
* :class:`UpstoxHistoricalDataProvider` — OPTIONAL real-HTTP
  historical provider for the Upstox V3 Historical Candle API. Only
  available when the ``UPSTOX_ANALYTICS_TOKEN`` environment variable is
  set. Windows (intraday ``15m`` and daily ``1D`` via the verified
  ``days/1`` endpoint) are split into bounded monthly calendar chunks;
  timestamps are normalized to UTC before the canonical validation
  layer. Daily responses are normalized by a deterministic rule
  (embedded intraday rows filtered before canonical candle
  construction). Instrument keys are resolved via an explicit verified
  mapping for the confirmed research universe (NIFTY + RELIANCE / TCS /
  HDFCBANK / ICICIBANK); unknown instruments fail clearly. Yahoo
  symbols are NOT valid Upstox instrument keys.

The provider must be replaceable without changing the research layer —
the service depends only on the protocol.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Protocol

from engine.config.universe import (
    BENCHMARK_INDEX,
    COMBINED_UNIVERSE,
    NIFTY200_SYMBOLS,
)
from engine.data.historical_times import (
    canonical_timeframe,
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
    resolves to its ``<NSE symbol>.NS`` Yahoo symbol, every NIFTY Top 200
    constituent resolves the same way (Checkpoint 19.1), and the
    benchmark index instruments resolve to their ``^``-prefixed Yahoo
    symbols. No second universe is defined here — only provider-specific
    Yahoo formatting of the existing canonical universes.
    """

    mapping = {name: f"{name}.NS" for name in COMBINED_UNIVERSE}
    mapping.update({name: f"{name}.NS" for name in NIFTY200_SYMBOLS})
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


# ============================================================
# OPTIONAL UPSTOX HISTORICAL PROVIDER (graceful; real HTTP)
# ============================================================


#: Environment variable holding the Upstox Analytics API token. NEVER
#: hard-coded, NEVER logged, NEVER committed. The provider reads it
#: lazily at construction/fetch time via the public
#: ``UPSTOX_ANALYTICS_TOKEN`` environment variable.
UPSTOX_TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"


#: Upstox V3 historical-candle endpoint template:
#: ``/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}``
#: (verified against the real API; candle rows arrive in REVERSE
#: chronological order).
UPSTOX_HISTORICAL_URL = (
    "https://api.upstox.com/v3/historical-candle/"
    "{instrument_key}/{unit}/{interval}/{to_date}/{from_date}"
)

#: Upstox candle unit for intraday / minute intervals.
UPSTOX_UNIT_MINUTES = "minutes"

#: Upstox candle unit for daily intervals. The verified V3 daily
#: endpoint is ``/historical-candle/{instrument_key}/days/1/...`` (the
#: correct spelling is ``days`` / ``1`` — NOT ``day`` / ``1``).
UPSTOX_UNIT_DAYS = "days"

#: Upstox candles are timestamped in the NSE/IST local timezone
#: (UTC+05:30) — e.g. ``2022-01-03T09:15:00+05:30``. Normalized to UTC
#: before the canonical OHLCVCandle objects reach validation.
UPSTOX_TIMESTAMP_TZ = timedelta(hours=5, minutes=30)

#: Default HTTP timeout for an Upstox request (seconds). Deliberately
#: finite — no infinite waits.
UPSTOX_HTTP_TIMEOUT = 30.0

#: Accept-Encoding header value. urllib defaults to ``identity``, which
#: the Upstox API gateway rejects; gzip/deflate is accepted and handled
#: by the provider before the payload reaches the JSON parser.
UPSTOX_ACCEPT_ENCODING = "gzip, deflate"

#: User-Agent sent to the Upstox API. The Cloudflare edge in front of
#: ``api.upstox.com`` rejects urllib's default ``Python-urllib/x.y``
#: User-Agent with HTTP 403 / Error 1010
#: (``browser_signature_banned``), so an explicit, neutral User-Agent
#: must be sent. This is the LIVE FAILURE that previously surfaced as
#: an empty/error Upstox response despite the endpoint returning HTTP
#: 200 when a real User-Agent (e.g. curl) is used.
UPSTOX_USER_AGENT = "python-urllib/upstox-historical-provider"


def _default_upstox_instrument_key_map() -> dict[str, str]:
    """Default instrument -> Upstox instrument-key mapping.

    Provider-specific Upstox formatting, isolated INSIDE this provider.
    No second universe is defined: the map is derived from the existing
    canonical universe (:mod:`engine.config.universe`) and contains the
    explicitly verified keys only. Instruments without a verified Upstox
    instrument key are NOT included — an unverified instrument is NEVER
    guessed or fabricated.
    """

    #: Upstox instrument keys verified against the real API / the
    #: established controlled Upstox verification. Only these canonical
    #: instruments are mapped; everything else is unknown and fails
    #: clearly instead of silently requesting an invalid key.
    #
    #: Equities use the NSE_EQ segment keyed by ISIN; the NIFTY 50 index
    #: uses the NSE_INDEX segment keyed by the exact Upstox name (the
    #: key contains a pipe separator and a space — it is percent-encoded
    #: at URL construction time, see ``fetch``).
    VERIFIED_KEY_MAP: dict[str, str] = {
        "RELIANCE": "NSE_EQ|INE002A01018",
        "TCS": "NSE_EQ|INE467B01029",
        "HDFCBANK": "NSE_EQ|INE040A01034",
        "ICICIBANK": "NSE_EQ|INE090A01021",
        "NIFTY": "NSE_INDEX|Nifty 50",
    }
    return dict(VERIFIED_KEY_MAP)


def _upstox_timestamp_to_utc(timestamp: object) -> datetime | None:
    """Normalize an Upstox timestamp to timezone-aware UTC.

    The Upstox API returns timestamps as ISO-8601 strings carrying the
    NSE/IST offset (``2022-01-03T09:15:00+05:30``). ISO strings are
    parsed (a parse failure returns ``None``) and aware ``datetime``
    values are converted to UTC. A NAIVE value (a datetime without
    ``tzinfo``, or an ISO string without an offset) returns ``None`` so
    the canonical ``NAIVE_TIMESTAMP`` validation is never weakened or
    bypassed.
    """

    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        return None  # naive -> rejected downstream; never repaired
    return timestamp.astimezone(UTC)


def _upstox_monthly_chunks(
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """
    Split ``[start, end]`` into bounded monthly calendar chunks.

    The Upstox V3 historical API accepts intraday requests, but the
    verified behavior shows 15-minute requests are only reliably served
    within bounded windows; a single multi-year intraday request is NOT
    made. This helper returns one half-open ``[chunk_start,
    chunk_end)`` pair per calendar month intersecting the requested
    range.

    Chunk boundaries are calendar-month ticks (never wall-clock
    arbitrary instants): a month chunk ``[M1, M2)`` where ``M2`` is the
    first instant of the following month. Every chunk pair is
    timezone-aware UTC and ``chunk_end`` NEVER exceeds the requested
    ``end``. Each chunk is later bounded to the inclusive UTC day at
    the API layer (the URL ``to_date``/``from_date`` are inclusive
    day labels), so no request extends beyond the requested range.

    The chunk count is bounded by the months intersecting the range
    (e.g. 12 months for a 1-year window; ~48 for 4 years). No infinite
    loop, no assumption that every month has the same number of candles.
    """

    if not start < end:
        return []
    # Cursor on the first-of-month tick at or before ``start``.
    cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
    chunks: list[tuple[datetime, datetime]] = []
    while cursor < end:
        nxt_anchor = datetime(
            cursor.year + (1 if cursor.month == 12 else 0),
            (cursor.month % 12) + 1,
            1,
            tzinfo=UTC,
        )
        chunk_end = min(nxt_anchor, end)
        # The first chunk never extends BEFORE the requested start — a
        # mid-month request is not silently widened.
        chunk_start = max(cursor, start)
        chunks.append((chunk_start, chunk_end))
        cursor = nxt_anchor
    return chunks


class UpstoxHistoricalDataProvider:
    """
    OPTIONAL Upstox-backed historical provider.

    Real HTTP provider for the Upstox V3 Historical Candle API:

        GET /historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}

    It implements the SAME provider contract as
    :class:`YahooHistoricalDataProvider` (``provider_name`` /
    ``is_available`` / ``supports`` / ``fetch`` -> a
    :class:`HistoricalProviderResponse`) and is only available when the
    ``UPSTOX_ANALYTICS_TOKEN`` environment variable is set. Network /
    API failures are converted into the existing provider response
    vocabulary (``ERROR`` / ``EMPTY`` / ``UNSUPPORTED``) — never raised.

    INSTRUMENT-KEY RESOLUTION (isolated inside the provider): canonical
    instrument names (``"RELIANCE"``, ...) resolve to Upstox instrument
    keys (``"NSE_EQ|INE002A01018"``) via an explicit mapping derived
    from the canonical universe. Only keys verified against the real
    API are mapped; an unknown instrument raises ``KeyError`` (fails
    clearly, never silently requests an invalid key). Yahoo symbols
    (``RELIANCE.NS``) are NOT valid Upstox instrument keys.

    MONTHLY CHUNKING (critical): a requested intraday window is split
    into bounded monthly calendar chunks and every chunk is requested
    separately. The multi-chunk candle lists are merged (dedupe by
    timestamp) and normalized to chronological order BEFORE the
    canonical validation layer.

    AUTHENTICATION SAFETY: the token is read from the
    ``UPSTOX_ANALYTICS_TOKEN`` environment variable, sent ONLY in the
    ``Authorization: Bearer <token>`` header, never logged, never
    printed, never committed. Missing token -> provider unavailable
    (an honest ``ERROR`` response on fetch).
    """

    provider_name = "upstox-historical"

    #: Upstox API unit/interval values for a canonical timeframe label
    #: (native only — no resampling / fabrication). Only timeframes the
    #: provider has actually been implemented for are listed.
    _INTERVALS: ClassVar[dict[str, tuple[str, str]]] = {
        "15m": (UPSTOX_UNIT_MINUTES, "15"),
        "1D": (UPSTOX_UNIT_DAYS, "1"),
    }

    #: Default HTTP timeout (seconds). Explicit, finite, configurable.
    timeout: float = UPSTOX_HTTP_TIMEOUT

    def __init__(
        self,
        *,
        token: str | None = None,
        instrument_key_map: Mapping[str, str] | None = None,
        urlopen=None,
        timeout: float | None = None,
    ) -> None:
        """
        Build an Upstox historical provider.

        ``token`` overrides the environment (for tests; a real caller
        should rely on the ``UPSTOX_ANALYTICS_TOKEN`` env var).
        ``instrument_key_map`` overrides the verified default map.
        ``urlopen`` is an injectable callable with the
        :func:`urllib.request.urlopen` signature used by deterministic
        tests (never required for production use). ``timeout`` overrides
        the finite default HTTP timeout.
        """

        self._token = token
        if instrument_key_map is not None:
            self._instrument_key_map = dict(instrument_key_map)
        else:
            self._instrument_key_map = _default_upstox_instrument_key_map()
        if urlopen is not None:
            self._urlopen = urlopen
        else:
            self._urlopen = None
        if timeout is not None:
            if timeout <= 0:
                raise ValueError("timeout must be positive.")
            self.timeout = float(timeout)

    # ------------------------------------------------------------
    # TOKEN / AVAILABILITY
    # ------------------------------------------------------------

    def _token_value(self) -> str | None:
        """Resolve the analytics token (override or environment)."""

        if self._token:
            return self._token
        value = os.environ.get(UPSTOX_TOKEN_ENV)
        return value if value else None

    def is_available(self) -> bool:
        """The provider is available only with a real analytics token."""

        return bool(self._token_value())

    def has_token(self) -> bool:
        """Public, non-sensitive availability probe (used by tests)."""

        return self.is_available()

    # ------------------------------------------------------------
    # SYMBOL / INSTRUMENT-KEY RESOLUTION
    # ------------------------------------------------------------

    def resolve_instrument_key(self, instrument: str) -> str:
        """
        Resolve a canonical instrument name to an Upstox instrument key.

        Raises ``KeyError`` for an instrument without a verified Upstox
        instrument key — unknown instruments fail clearly rather than
        silently requesting an invalid key.
        """

        key = self._instrument_key_map.get(instrument)
        if key is None:
            raise KeyError(
                f"Unknown Upstox instrument {instrument!r}: no verified "
                "Upstox instrument key. Yahoo symbols (e.g. RELIANCE.NS) "
                "are NOT valid Upstox instrument keys.",
            )
        return key

    # ------------------------------------------------------------
    # SUPPORT
    # ------------------------------------------------------------

    def supports(self, instrument: str, timeframe: str) -> bool:
        canonical = canonical_timeframe(timeframe)
        return canonical in self._INTERVALS

    # ------------------------------------------------------------
    # FETCH
    # ------------------------------------------------------------

    def fetch(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
    ) -> HistoricalProviderResponse:
        """
        Fetch a historical window from Upstox.

        The requested intraday range is split into bounded monthly
        chunks; every chunk response is validated (status/candle-shape/
        numeric/timestamp/parseable), converted to canonical
        ``OHLCVCandle`` objects, merged (dedupe), and normalized to
        chronological order before the response is returned. The
        canonical validation pipeline (the service) runs AFTER this
        provider; no validation logic is duplicated here.
        """

        del reference_now  # accepted for the service's reference-now
        # threading; the requested range is never extended - every
        # monthly chunk is bounded to the requested end.
        token = self._token_value()
        if token is None:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.ERROR,
                reason=(
                    "Upstox historical provider unavailable: "
                    f"{UPSTOX_TOKEN_ENV} environment variable is not set."
                ),
            )
        canonical = canonical_timeframe(request.timeframe)
        unit_interval = self._INTERVALS.get(canonical or "")
        if unit_interval is None:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.UNSUPPORTED,
                reason=(
                    f"timeframe {request.timeframe!r} not supported by "
                    "Upstox (supported: 15m, 1D)."
                ),
            )
        try:
            instrument_key = self.resolve_instrument_key(request.instrument)
        except KeyError as exc:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.UNSUPPORTED,
                reason=f"unknown instrument: {exc}",
            )
        unit, interval = unit_interval

        merged: dict[str, OHLCVCandle] = {}
        errors: list[str] = []
        called: list[str] = []
        daily_filtered_rows = 0
        for chunk_start, chunk_end in _upstox_monthly_chunks(
            request.start, request.end,
        ):
            # The API takes inclusive PER-DAY labels; we bound every
            # chunk to the requested range so the last record can never
            # exceed the requested end. The chunk's from_date is the
            # inclusive day of the chunk start, its to_date the
            # inclusive day of the chunk end.
            from_date = chunk_start.date()
            to_date = min(chunk_end.date(), request.end.date())
            url = UPSTOX_HISTORICAL_URL.format(
                instrument_key=urllib.parse.quote(instrument_key, safe="|"),
                unit=unit,
                interval=interval,
                to_date=to_date.isoformat(),
                from_date=from_date.isoformat(),
            )
            called.append(url)
            try:
                candles, filtered = self._fetch_one(url, token, unit=unit)
            except Exception as exc:  # noqa: BLE001 - network/parse failures
                errors.append(f"{from_date}: {exc}")
                continue
            if unit == UPSTOX_UNIT_DAYS:
                daily_filtered_rows += filtered
            for candle in candles:
                if unit == UPSTOX_UNIT_DAYS:
                    # RANGE SEMANTICS (DAILY): Upstox day labels are
                    # inclusive exchange-day labels derived from the
                    # requested ``[start.date(), end.date()]``. A daily
                    # candle is timestamped at 00:00:00+05:30 (IST), which
                    # normalizes to 18:30:00 UTC the PRIOR day — so a
                    # strict UTC comparison against the UTC ``[start,
                    # end]`` window would incorrectly drop the first
                    # requested trading day. The deterministic provider
                    # rule is therefore to compare the candle's IST
                    # trading-day label against the requested day labels
                    # (exactly what the API was asked to produce).
                    ist_label = (candle.timestamp + UPSTOX_TIMESTAMP_TZ).date()
                    if not (request.start.date() <= ist_label
                            <= request.end.date()):
                        continue
                else:
                    # RANGE SEMANTICS (INTRADAY): the NSE session
                    # candles fall within the same UTC day, so the
                    # strict UTC window is used unchanged.
                    if not (request.start <= candle.timestamp <= request.end):
                        continue
                merged[candle.timestamp.isoformat()] = candle

        if not merged:
            if errors and len(errors) >= len(called):
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.ERROR,
                    reason=f"provider error: {errors[0]}",
                )
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                reason="Upstox returned no candles for the request.",
            )

        # Reverse-chronological raw responses are normalized BEFORE the
        # canonical validation layer (chronological, deterministic).
        candles = sorted(merged.values(), key=lambda c: c.timestamp)
        parts = [f"{len(called)} monthly chunk(s) requested"]
        if daily_filtered_rows:
            parts.append(
                f"{daily_filtered_rows} embedded intraday row(s) filtered "
                "from daily response(s)",
            )
        if errors:
            parts.append(f"{len(errors)} chunk error(s)")
        parts.append("merged and normalized to chronological order")
        reason = "; ".join(parts)
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=tuple(candles),
            reason=reason,
        )

    # ------------------------------------------------------------
    # HTTP LAYER (injectable for tests; no token leakage)
    # ------------------------------------------------------------

    def _fetch_one(
        self,
        url: str,
        token: str,
        *,
        unit: str | None = None,
    ) -> tuple[list[OHLCVCandle], int]:
        """One GET request against the Upstox V3 historical endpoint.

        Returns ``(candles, filtered_count)`` where ``candles`` are the
        canonical ``OHLCVCandle`` objects parsed from the API response
        (normalized to UTC, chronological NOT required here — the caller
        sorts) and ``filtered_count`` is the number of rows dropped by
        the provider-specific daily normalization rule (0 for intraday
        responses). Throws on network errors and malformed /
        unsuccessful responses so the caller can report them honestly.

        When ``unit`` is :data:`UPSTOX_UNIT_DAYS` the daily normalization
        rule is applied (embedded intraday rows are filtered BEFORE
        canonical candle construction — see ``_parse_candles``).

        The ``urlopen`` injection (used by deterministic tests) receives
        the actual :class:`urllib.request.Request` object and the token,
        so tests exercise the very same URL/header construction code as
        production calls.
        """

        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": UPSTOX_ACCEPT_ENCODING,
                # The Cloudflare edge rejects urllib's default
                # ``Python-urllib/x.y`` User-Agent (HTTP 403 / Error
                # 1010), so an explicit, neutral User-Agent is always
                # sent.
                "User-Agent": UPSTOX_USER_AGENT,
                "Authorization": f"Bearer {token}",
            },
        )
        if self._urlopen is not None:
            raw = self._urlopen(request, token)
            text = self._decode_body(raw, None)
        else:
            # Upstox API is HTTPS by construction (urlopen is stdlib).
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "").lower()
            text = self._decode_body(raw, encoding)

        payload = json.loads(text)
        return self._parse_candles_filtered(payload, unit=unit)

    @staticmethod
    def _decode_body(raw: bytes | str | None,
                     encoding: str | None) -> str:
        """Decode a raw Upstox response body.

        ``urllib.request`` does NOT automatically decompress a
        ``Content-Encoding`` body, so a gzip/deflate response (which the
        Upstox gateway serves when ``Accept-Encoding`` is requested) is
        decompressed here. The fake/real paths both go through this
        helper, keeping production and test behavior identical.
        """

        if raw is None:
            raise ValueError("Upstox returned an empty body.")
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, bytes):
            raise ValueError(
                f"Upstox response body is type {type(raw).__name__!r} "
                "(expected bytes).",
            )
        encoding = (encoding or "").strip().lower()
        if encoding in ("gzip", "x-gzip"):
            try:
                return gzip.decompress(raw).decode("utf-8")
            except OSError as exc:
                raise ValueError(f"invalid gzip body: {exc}") from exc
        if encoding == "deflate":
            try:
                return zlib.decompress(raw).decode("utf-8")
            except zlib.error as exc:
                raise ValueError(f"invalid deflate body: {exc}") from exc
        return raw.decode("utf-8")

    @staticmethod
    def _parse_candles(payload: object) -> list[OHLCVCandle]:
        """
        Validate and convert one Upstox response payload to candles.

        Backward-compatible entry point: returns the list of canonical
        ``OHLCVCandle`` objects with NO daily row filtering (the
        behaviour the provider shipped before daily support). New code
        that must apply the provider-specific daily normalization rule
        calls :meth:`_parse_candles_filtered` instead.
        """

        candles, _ = UpstoxHistoricalDataProvider._parse_candles_filtered(
            payload,
            unit=None,
        )
        return candles

    @staticmethod
    def _parse_candles_filtered(
        payload: object,
        unit: str | None = None,
    ) -> tuple[list[OHLCVCandle], int]:
        """
        Validate and convert one Upstox response payload to candles.

        Returns ``(candles, filtered_count)`` where ``candles`` is the
        list of canonical ``OHLCVCandle`` objects and ``filtered_count``
        counts rows dropped by the provider-specific daily normalization
        rule (always 0 for intraday / ``unit is None`` responses).

        The response is validated before any ``OHLCVCandle`` is
        constructed:

        * top-level ``status == "success"``
        * the candle list ``data.candles`` (Upstox V3 nests the list
          under ``data``) is a (possibly empty) list; a top-level
          ``candles`` list is also accepted for backward compatibility
          with the pre-V3 shape
        * every candle row is a 7-element list/sequence whose first six
          elements are the OHLCV row ``[timestamp, open, high, low,
          close, volume]`` (the seventh, ``open_interest``, is ignored —
          the canonical contract has no field for it)
        * numeric OHLCV fields are finite numbers
        * timestamps are timezone-aware and parseable (Upstox returns
          ``+05:30``-aware ISO strings)

        Invalid rows raise ``ValueError`` so the caller reports the
        chunk error honestly (never a silent partial success).

        DAILY RESPONSE NORMALIZATION RULE (unit == ``UPSTOX_UNIT_DAYS``;
        ``days`` / ``1`` responses): a genuine Upstox daily candle is
        timestamped at 00:00:00+05:30 (IST) — the exchange-day start
        label — per the official V3 daily-response examples. Some daily
        responses may nevertheless embed intraday-related rows (e.g.
        09:15:00+05:30 intraday timestamps). Those rows are NOT valid
        daily candles: they share the trading day but carry a
        non-midnight IST time-of-day and would be stored as duplicates /
        malformed daily bars. The deterministic provider rule is
        therefore: for DAILY responses only, keep a row ONLY when its
        IST-local time-of-day is exactly midnight (00:00:00, the daily
        bar label) and FILTER (skip) every other row BEFORE canonical
        candle construction. Filtered rows are counted and reported in
        the response reason — never silently repaired, never fabricated
        into daily candles. A daily response consisting entirely of
        embedded intraday rows yields an honest empty chunk.
        """

        if not isinstance(payload, dict):
            raise ValueError("Upstox response is not an object.")
        if payload.get("status") != "success":
            raise ValueError(
                f"Upstox response status is {payload.get('status')!r}; "
                "expected 'success'.",
            )
        # Upstox V3 returns the candle list under ``data.candles``.
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("candles"), list):
            raw = data.get("candles")
        else:
            # Backward-compatible flat ``candles`` location.
            raw = payload.get("candles")
        if not isinstance(raw, list):
            raise ValueError(
                "Upstox response is missing a 'data.candles' list.",
            )
        candles: list[OHLCVCandle] = []
        filtered_count = 0
        for row_index, row in enumerate(raw):
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError(
                    f"Upstox candle row {row_index} has invalid shape "
                    f"{row!r} (expected 7-element [timestamp, open, high, "
                    "low, close, volume, open_interest]).",
                )
            ts = _upstox_timestamp_to_utc(row[0])
            if ts is None:
                raise ValueError(
                    f"Upstox candle row {row_index} timestamp {row[0]!r} "
                    "is not timezone-aware / parseable.",
                )
            if unit == UPSTOX_UNIT_DAYS:
                # DAILY RESPONSE NORMALIZATION RULE: keep a row only
                # when its IST-local time-of-day is midnight (the daily
                # bar label); filter embedded intraday rows explicitly.
                ist_local = ts + UPSTOX_TIMESTAMP_TZ
                if not (ist_local.hour == 0 and ist_local.minute == 0
                        and ist_local.second == 0):
                    filtered_count += 1
                    continue
            values = []
            for name, value in zip(
                ("open", "high", "low", "close", "volume"), row[1:6],
            ):
                try:
                    numeric = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Upstox candle row {row_index} {name}={value!r} "
                        "is not numeric.",
                    ) from exc
                if not math.isfinite(numeric):  # NaN / infinite
                    raise ValueError(
                        f"Upstox candle row {row_index} {name}={value!r} "
                        "is not a finite number.",
                    )
                values.append(numeric)
            open_price, high, low, close, volume = values
            candles.append(
                OHLCVCandle(
                    timestamp=ts,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                ),
            )
        return candles, filtered_count


__all__ = [
    "UPSTOX_ACCEPT_ENCODING",
    "UPSTOX_TOKEN_ENV",
    "UPSTOX_UNIT_DAYS",
    "UPSTOX_USER_AGENT",
    "DeterministicLocalHistoricalProvider",
    "HistoricalMarketDataProvider",
    "HistoricalProviderResponse",
    "InMemoryHistoricalProvider",
    "UpstoxHistoricalDataProvider",
    "YahooHistoricalDataProvider",
]
