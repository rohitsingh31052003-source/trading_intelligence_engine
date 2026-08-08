"""
Deterministic synthetic historical datasets for the pipeline
(Sprint 11F).

These datasets are constructed from simple deterministic OHLC
values. They exist to exercise the end-to-end integration; they
do NOT tune the production engines to force trades.

Dataset shapes:

* ``trending_dataset``
    A zigzag uptrend followed by a zigzag downtrend. The deep
    pullbacks and continuations produce aligned buy-side / sell-
    side breakouts, so the confluence engine receives consistent
    directional evidence. This yields a mix of LONG and SHORT
    signals with known TP / SL outcomes.

* ``flat_dataset``
    A sideways, oscillating sequence that produces little or no
    directional agreement. Useful for verifying the "no signal"
    path.

* ``minimal_dataset``
    A handful of candles below the minimum-history threshold.
    Useful for verifying the insufficient-history skip.

All candles are valid ``OHLCVCandle`` instances supplied in
chronological order (oldest -> newest).
"""

from __future__ import annotations

from datetime import UTC, timedelta
from datetime import datetime

from engine.models.ohlcv import OHLCVCandle


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _candle(
    close: float,
    spread: float,
    index: int,
) -> OHLCVCandle:
    """
    Build a valid OHLCV candle from a close price and a
    symmetric high/low spread.
    """

    low = round(close - spread, 2)
    high = round(close + spread, 2)
    open_ = round((low + high) / 2, 2)

    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _zigzag_leg(
    start: float,
    step: int,
    rise: int,
    pullback: int,
    spread: float,
    base_index: int,
    direction: int = 1,
):
    """
    Generate one zigzag leg of an uptrend (direction=1) or
    downtrend (direction=-1).

    The leg rises ``rise`` candles by ``step`` each, then
    pulls back ``pullback`` candles by ``step`` each. Returns
    ``(candles, next_close, next_index)``.
    """

    candles: list[OHLCVCandle] = []
    close = start
    index = base_index

    for _ in range(rise):
        close = round(close + direction * step, 2)
        candles.append(_candle(close, spread, index))
        index += 1

    for _ in range(pullback):
        close = round(close - direction * step, 2)
        candles.append(_candle(close, spread, index))
        index += 1

    return candles, close, index


def trending_dataset() -> list[OHLCVCandle]:
    """
    A deterministic up-then-down market.

    The uptrend portion produces LONG signals whose validation
    against the continuing rise resolves as WIN. The downtrend
    portion produces SHORT signals.

    The dataset is deliberately long enough (>= 40 candles) to
    exercise the full walk-forward funnel, the one-active-signal
    policy and the performance analytics layer.
    """

    candles: list[OHLCVCandle] = []
    close = 100.0
    index = 0

    # Build a rising zigzag: long rise legs with shallow pullbacks.
    for _ in range(4):
        leg, close, index = _zigzag_leg(
            start=close,
            step=2,
            rise=6,
            pullback=2,
            spread=1.0,
            base_index=index,
            direction=1,
        )
        candles.extend(leg)

    # Build a falling zigzag: long fall legs with shallow pullbacks.
    for _ in range(4):
        leg, close, index = _zigzag_leg(
            start=close,
            step=2,
            rise=6,
            pullback=2,
            spread=1.0,
            base_index=index,
            direction=-1,
        )
        candles.extend(leg)

    return candles


def flat_dataset() -> list[OHLCVCandle]:
    """
    A sideways oscillating market around a flat level.

    Produces swings but no sustained directional structure, so
    most evaluation points yield no eligible signal.
    """

    candles: list[OHLCVCandle] = []
    base = 100.0

    for i in range(40):
        # Small deterministic oscillation.
        close = round(base + (2 if i % 2 == 0 else -2), 2)
        candles.append(_candle(close, 1.0, i))

    return candles


def minimal_dataset() -> list[OHLCVCandle]:
    """
    A dataset shorter than the default minimum history.

    Used to verify the insufficient-history skip path.
    """

    return [
        _candle(round(100.0 + i, 2), 1.0, i)
        for i in range(5)
    ]
