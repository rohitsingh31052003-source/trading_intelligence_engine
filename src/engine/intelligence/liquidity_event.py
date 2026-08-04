"""
Liquidity Event Engine

Purpose
-------
Classify what happened after liquidity was formed.
"""

from engine.models.liquidity import LiquidityType
from engine.models.liquidity_event import (
    LiquidityEvent,
    LiquidityEventType,
)


class LiquidityEventEngine:
    """
    Analyze liquidity pools and classify the resulting market event.
    """

    def analyze(
        self,
        pools,
        candles,
    ):

        events = []

        for pool in pools:

            events.append(
                self._analyze_pool(
                    pool,
                    candles,
                )
            )

        return events

    def _analyze_pool(
        self,
        pool,
        candles,
    ):

        if pool.liquidity_type == LiquidityType.BUY_SIDE:

            return self._check_buy_side(
                pool,
                candles,
            )

        return self._check_sell_side(
            pool,
            candles,
        )

    def _check_buy_side(
        self,
        pool,
        candles,
    ):

        for index, candle in enumerate(candles):

            if candle.timestamp <= pool.created_at:
                continue

            if candle.high > pool.price:

                next_candle = (
                    candles[index + 1]
                    if index + 1 < len(candles)
                    else None
                )

                if (
                    next_candle
                    and next_candle.close < pool.price
                ):

                    return LiquidityEvent(
                        pool=pool,
                        detected=True,
                        event_type=LiquidityEventType.SWEEP,
                        event_price=candle.high,
                        event_timestamp=candle.timestamp,
                        confidence=90.0,
                        reasons=[
                            "Liquidity taken.",
                            "Next candle closed back below liquidity.",
                            "Classified as sweep.",
                        ],
                    )

                return LiquidityEvent(
                    pool=pool,
                    detected=True,
                    event_type=LiquidityEventType.BREAKOUT,
                    event_price=candle.high,
                    event_timestamp=candle.timestamp,
                    confidence=85.0,
                    reasons=[
                        "Liquidity taken.",
                        "Price continued above liquidity.",
                        "Classified as breakout.",
                    ],
                )

        return LiquidityEvent(
            pool=pool,
            detected=False,
            event_type=LiquidityEventType.NONE,
            event_price=None,
            event_timestamp=None,
            confidence=0.0,
            reasons=[
                "Liquidity remains active.",
            ],
        )

    def _check_sell_side(
        self,
        pool,
        candles,
    ):

        for index, candle in enumerate(candles):

            if candle.timestamp <= pool.created_at:
                continue

            if candle.low < pool.price:

                next_candle = (
                    candles[index + 1]
                    if index + 1 < len(candles)
                    else None
                )

                if (
                    next_candle
                    and next_candle.close > pool.price
                ):

                    return LiquidityEvent(
                        pool=pool,
                        detected=True,
                        event_type=LiquidityEventType.SWEEP,
                        event_price=candle.low,
                        event_timestamp=candle.timestamp,
                        confidence=90.0,
                        reasons=[
                            "Liquidity taken.",
                            "Next candle closed back above liquidity.",
                            "Classified as sweep.",
                        ],
                    )

                return LiquidityEvent(
                    pool=pool,
                    detected=True,
                    event_type=LiquidityEventType.BREAKOUT,
                    event_price=candle.low,
                    event_timestamp=candle.timestamp,
                    confidence=85.0,
                    reasons=[
                        "Liquidity taken.",
                        "Price continued below liquidity.",
                        "Classified as breakout.",
                    ],
                )

        return LiquidityEvent(
            pool=pool,
            detected=False,
            event_type=LiquidityEventType.NONE,
            event_price=None,
            event_timestamp=None,
            confidence=0.0,
            reasons=[
                "Liquidity remains active.",
            ],
        )