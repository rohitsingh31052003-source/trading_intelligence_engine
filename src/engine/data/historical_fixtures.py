"""
Deterministic historical market-data fixtures (Sprint 11V).

These fixtures provide small, deterministic, multi-instrument /
multi-timeframe historical OHLCV datasets for the Sprint 11V historical
replay layer and its automated tests. They are LOCAL and DETERMINISTIC:
they have NO network dependency and NO vendor dependency, so the test
suite never depends on live internet market data.

The fixtures are intentionally synthetic but realistic in SHAPE: each
instrument carries a higher / context timeframe (``1D``) and a lower /
setup timeframe (``15M``), with chronologically ordered, duplicate-free,
OHLC-valid candles. They exercise the existing intelligence pipeline
(market context -> setup / confluence -> candidate -> decision ->
opportunity -> multi-timeframe scanner) the same way real historical
data would.

DESIGN PRINCIPLE — no fabricated intelligence:

The fixtures provide RAW OHLCV only. They do NOT encode trends,
structures, setups, stops, targets, risk/reward, rankings or any
intelligence output. All intelligence is produced by the EXISTING
Sprint 11A-11U engines at replay time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.models.historical import HistoricalRecord
from engine.models.ohlcv import OHLCVCandle


_EPOCH = datetime(2025, 1, 6, tzinfo=UTC)


# ============================================================
# CANDLE BUILDERS
# ============================================================


def _candle(
    close: float, ts: datetime, spread: float = 2.0, volume: float = 1000.0,
) -> OHLCVCandle:
    """Build an OHLC-valid candle centered on ``close``."""

    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=volume,
    )


def _bullish_zigzag(
    start: float,
    n_legs: int = 4,
    start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1),
    rise: float = 6.0,
    pullback: float = 3.0,
    spread: float = 2.0,
) -> list[OHLCVCandle]:
    """Bullish zigzag (HH/HL) producing a BULLISH market context."""

    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close + rise, 2)
            candles.append(_candle(close, ts, spread=spread))
            ts = ts + step
        for _ in range(2):
            close = round(close - pullback, 2)
            candles.append(_candle(close, ts, spread=spread))
            ts = ts + step
    return candles


def _bearish_zigzag(
    start: float,
    n_legs: int = 4,
    start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1),
    fall: float = 6.0,
    bounce: float = 3.0,
    spread: float = 2.0,
) -> list[OHLCVCandle]:
    """Bearish zigzag (LH/LL) producing a BEARISH market context."""

    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close - fall, 2)
            candles.append(_candle(close, ts, spread=spread))
            ts = ts + step
        for _ in range(2):
            close = round(close + bounce, 2)
            candles.append(_candle(close, ts, spread=spread))
            ts = ts + step
    return candles


def _flat_oscillation(
    n: int, start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1), base: float = 100.0,
    spread: float = 2.0,
) -> list[OHLCVCandle]:
    """Sideways oscillating market (NEUTRAL / RANGE)."""

    candles: list[OHLCVCandle] = []
    ts = start_ts
    for i in range(n):
        close = round(base + (2 if i % 2 == 0 else -2), 2)
        candles.append(_candle(close, ts, spread=spread))
        ts = ts + step
    return candles


# ============================================================
# PER-INSTRUMENT TIMEFRAME PAIRS
# ============================================================


def _setup_after_context(
    context: list[OHLCVCandle],
    builder,
    step: timedelta = timedelta(minutes=15),
) -> list[OHLCVCandle]:
    """Build a setup-timeframe series continuing from the context close."""

    setup_start = context[-1].timestamp + step
    return builder(start=context[-1].close, start_ts=setup_start, step=step)


def nifty_pair() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """NIFTY: bullish daily context + bullish 15M setup (ALIGNED LONG)."""

    context = _bullish_zigzag(100.0, n_legs=4, start_ts=_EPOCH)
    setup = _setup_after_context(
        context,
        lambda start, start_ts, step: _bullish_zigzag(
            start, n_legs=4, start_ts=start_ts, step=step,
        ),
    )
    return context, setup


def reliance_pair() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """RELIANCE: bearish daily context + bearish 15M setup (ALIGNED SHORT)."""

    context = _bearish_zigzag(200.0, n_legs=4, start_ts=_EPOCH)
    setup = _setup_after_context(
        context,
        lambda start, start_ts, step: _bearish_zigzag(
            start, n_legs=4, start_ts=start_ts, step=step,
        ),
    )
    return context, setup


def tcs_pair() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """TCS: bearish daily context + bullish 15M setup (CONFLICTING LONG)."""

    context = _bearish_zigzag(300.0, n_legs=4, start_ts=_EPOCH)
    setup = _setup_after_context(
        context,
        lambda start, start_ts, step: _bullish_zigzag(
            start, n_legs=4, start_ts=start_ts, step=step,
        ),
    )
    return context, setup


def hdfcbank_pair() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """HDFCBANK: flat daily context + flat 15M setup (NEUTRAL / RANGE)."""

    context = _flat_oscillation(20, start_ts=_EPOCH)
    setup = _setup_after_context(
        context,
        lambda start, start_ts, step: _flat_oscillation(
            20, start_ts=start_ts, step=step, base=start,
        ),
    )
    return context, setup


def icicibank_pair() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """ICICIBANK: bullish daily context + bullish 15M setup (ALIGNED LONG)."""

    context = _bullish_zigzag(150.0, n_legs=4, start_ts=_EPOCH)
    setup = _setup_after_context(
        context,
        lambda start, start_ts, step: _bullish_zigzag(
            start, n_legs=4, start_ts=start_ts, step=step,
        ),
    )
    return context, setup


# ============================================================
# RECORD / DATASET ASSEMBLY
# ============================================================


def _series_to_records(
    instrument: str, timeframe: str, candles: list[OHLCVCandle],
) -> list[HistoricalRecord]:
    """Convert a candle list into historical records."""

    return [
        HistoricalRecord(
            timestamp=c.timestamp,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            instrument=instrument,
            timeframe=timeframe,
        )
        for c in candles
    ]


_INSTRUMENT_BUILDERS = {
    "NIFTY": nifty_pair,
    "RELIANCE": reliance_pair,
    "TCS": tcs_pair,
    "HDFCBANK": hdfcbank_pair,
    "ICICIBANK": icicibank_pair,
}


def historical_records(
    instruments: tuple[str, ...] = (
        "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    ),
    context_timeframe: str = "1D",
    setup_timeframe: str = "15M",
) -> list[HistoricalRecord]:
    """
    Build the deterministic multi-instrument historical record batch.

    Returns a flat list of :class:`HistoricalRecord` objects spanning the
    requested instruments and the context + setup timeframes. The order
    is deterministic (instruments in the given order; context records
    first, then setup records per instrument).
    """

    records: list[HistoricalRecord] = []
    for instrument in instruments:
        builder = _INSTRUMENT_BUILDERS[instrument]
        context, setup = builder()
        records.extend(
            _series_to_records(instrument, context_timeframe, context),
        )
        records.extend(
            _series_to_records(instrument, setup_timeframe, setup),
        )
    return records


def historical_candles_by_instrument(
    instruments: tuple[str, ...] = (
        "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    ),
    context_timeframe: str = "1D",
    setup_timeframe: str = "15M",
) -> dict[str, dict[str, list[OHLCVCandle]]]:
    """
    Build the deterministic fixtures as raw candles keyed by instrument
    then timeframe. Convenient for tests / demos that want to feed the
    scanner directly.
    """

    out: dict[str, dict[str, list[OHLCVCandle]]] = {}
    for instrument in instruments:
        builder = _INSTRUMENT_BUILDERS[instrument]
        context, setup = builder()
        out[instrument] = {
            context_timeframe: context,
            setup_timeframe: setup,
        }
    return out


# ============================================================
# INVALID / INCOMPLETE RECORD HELPERS (for data-quality tests)
# ============================================================


def invalid_high_below_low() -> HistoricalRecord:
    """A record where high < low (impossible OHLC)."""

    return HistoricalRecord(
        timestamp=datetime(2025, 1, 6, tzinfo=UTC),
        open=100.0, high=95.0, low=105.0, close=100.0, volume=1000.0,
        instrument="BAD", timeframe="1D",
    )


def invalid_missing_timestamp() -> HistoricalRecord:
    """A record missing its timestamp."""

    return HistoricalRecord(
        timestamp=None,
        open=100.0, high=105.0, low=95.0, close=102.0, volume=1000.0,
        instrument="BAD", timeframe="1D",
    )


def invalid_negative_volume() -> HistoricalRecord:
    """A record with negative volume."""

    return HistoricalRecord(
        timestamp=datetime(2025, 1, 6, tzinfo=UTC),
        open=100.0, high=105.0, low=95.0, close=102.0, volume=-50.0,
        instrument="BAD", timeframe="1D",
    )


def invalid_open_outside_range() -> HistoricalRecord:
    """A record where the open lies outside [low, high]."""

    return HistoricalRecord(
        timestamp=datetime(2025, 1, 6, tzinfo=UTC),
        open=200.0, high=105.0, low=95.0, close=102.0, volume=1000.0,
        instrument="BAD", timeframe="1D",
    )


def duplicate_timestamp_records() -> list[HistoricalRecord]:
    """Two valid records sharing one timestamp."""

    ts = datetime(2025, 1, 6, tzinfo=UTC)
    return [
        HistoricalRecord(
            timestamp=ts, open=100.0, high=105.0, low=95.0, close=102.0,
            volume=1000.0, instrument="DUP", timeframe="1D",
        ),
        HistoricalRecord(
            timestamp=ts, open=101.0, high=106.0, low=96.0, close=103.0,
            volume=1100.0, instrument="DUP", timeframe="1D",
        ),
    ]


def short_history_records(
    instrument: str = "SHORT", timeframe: str = "15M",
) -> list[HistoricalRecord]:
    """A series with too few candles to be VALID (INCOMPLETE)."""

    ts = _EPOCH
    records: list[HistoricalRecord] = []
    for i in range(3):
        records.append(
            HistoricalRecord(
                timestamp=ts,
                open=100.0 + i, high=105.0 + i, low=95.0 + i, close=102.0 + i,
                volume=1000.0, instrument=instrument, timeframe=timeframe,
            ),
        )
        ts = ts + timedelta(minutes=15)
    return records


__all__ = [
    "duplicate_timestamp_records",
    "historical_candles_by_instrument",
    "historical_records",
    "invalid_high_below_low",
    "invalid_missing_timestamp",
    "invalid_negative_volume",
    "invalid_open_outside_range",
    "short_history_records",
]
