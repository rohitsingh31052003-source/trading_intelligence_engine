"""
Liquidity detection engine.
"""

from __future__ import annotations

from engine.config.liquidity_config import LiquidityConfig
from engine.models.liquidity import (
    LiquidityPool,
    LiquidityStatus,
    LiquidityType,
)
from engine.models.swing import SwingType
from engine.intelligence.strength import strength_category

class LiquidityEngine:
    """
    Detect buy-side and sell-side liquidity pools
    from confirmed swing highs and lows.
    """

    def __init__(
        self,
        config: LiquidityConfig | None = None,
    ) -> None:

        self.config = config or LiquidityConfig()

    def detect(self, swings) -> list[LiquidityPool]:
        """
        Detect liquidity pools from confirmed swings.
        """

        pools: list[LiquidityPool] = []

        # -----------------------------
        # Create initial pools
        # -----------------------------
        for swing in swings:

            if not swing.confirmed:
                continue

            liquidity_type = (
                LiquidityType.BUY_SIDE
                if swing.swing_type == SwingType.HIGH
                else LiquidityType.SELL_SIDE
            )

            pools.append(
                LiquidityPool(
                    price=swing.price,
                    liquidity_type=liquidity_type,
                    created_at=swing.timestamp,
                    swing_count=1,
                    strength=20.0,
                    category=strength_category(20.0),
                    status=LiquidityStatus.ACTIVE,
                )
            )

        # -----------------------------
        # Merge nearby pools
        # -----------------------------
        return self._merge_pools(pools)

    def _merge_pools(
        self,
        pools: list[LiquidityPool],
    ) -> list[LiquidityPool]:
        """
        Merge nearby liquidity pools.
        """

        merged: list[LiquidityPool] = []

        for pool in pools:

            merged_existing = False

            for i, existing in enumerate(merged):

                if existing.liquidity_type != pool.liquidity_type:
                    continue

                average_price = (
                    existing.price + pool.price
                ) / 2

                difference_percent = (
                    abs(existing.price - pool.price)
                    / average_price
                ) * 100

                if (
                    difference_percent
                    <= self.config.equal_level_tolerance_percent
                ):

                    swing_count = (
                        existing.swing_count
                        + pool.swing_count
                    )

                    strength = min(
                        20.0 + 5.0 * (swing_count - 1),
                        100.0,
                    )

                    merged[i] = LiquidityPool(
                        price=average_price,
                        liquidity_type=existing.liquidity_type,
                        created_at=min(
                            existing.created_at,
                            pool.created_at,
                        ),
                        swing_count=swing_count,
                        strength=strength,
                        category=strength_category(strength),
                        status=LiquidityStatus.ACTIVE,
                    )

                    merged_existing = True
                    break

            if not merged_existing:
                merged.append(pool)

        return merged