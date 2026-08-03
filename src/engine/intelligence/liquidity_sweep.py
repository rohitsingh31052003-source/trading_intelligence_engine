"""
Liquidity Sweep Engine

Purpose
-------
Detect whether existing liquidity pools have been taken by
subsequent market price action.

Input
-----
Liquidity Pools
OHLCV Candles

Output
------
LiquiditySweep objects
"""

from engine.models.liquidity import LiquidityType
from engine.models.liquidity_sweep import (
    LiquiditySweep,
    LiquiditySweepType,
)


class LiquiditySweepEngine:
    """
    Detects sweeps of previously identified liquidity pools.
    """

    def analyze(
        self,
        pools,
        candles,
    ):

        results = []

        for pool in pools:
            results.append(
                self._analyze_pool(
                    pool,
                    candles,
                )
            )

        return results

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

        for candle in candles:

            if candle.timestamp <= pool.created_at:
                continue

            if candle.high > pool.price:

                return LiquiditySweep(
                    pool=pool,
                    detected=True,
                    sweep_type=LiquiditySweepType.BUY_SIDE,
                    sweep_price=candle.high,
                    sweep_timestamp=candle.timestamp,
                    confidence=80.0,
                    reasons=[
                        "High traded above buy-side liquidity.",
                    ],
                )

        return LiquiditySweep(
            pool=pool,
            detected=False,
            sweep_type=LiquiditySweepType.NONE,
            sweep_price=None,
            sweep_timestamp=None,
            confidence=0.0,
            reasons=[
                "Buy-side liquidity remains active.",
            ],
        )

    def _check_sell_side(
        self,
        pool,
        candles,
    ):

        for candle in candles:

            if candle.timestamp <= pool.created_at:
                continue

            if candle.low < pool.price:

                return LiquiditySweep(
                    pool=pool,
                    detected=True,
                    sweep_type=LiquiditySweepType.SELL_SIDE,
                    sweep_price=candle.low,
                    sweep_timestamp=candle.timestamp,
                    confidence=80.0,
                    reasons=[
                        "Low traded below sell-side liquidity.",
                    ],
                )

        return LiquiditySweep(
            pool=pool,
            detected=False,
            sweep_type=LiquiditySweepType.NONE,
            sweep_price=None,
            sweep_timestamp=None,
            confidence=0.0,
            reasons=[
                "Sell-side liquidity remains active.",
            ],
        )