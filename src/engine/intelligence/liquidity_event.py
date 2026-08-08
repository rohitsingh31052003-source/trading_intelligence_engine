
"""
Liquidity Event Engine

Purpose
-------
Detect liquidity breaches, confirm them across a configurable
confirmation window, classify the resulting event, and calculate
evidence-based confidence.
"""

from __future__ import annotations

from engine.config.liquidity_event_config import LiquidityEventConfig
from engine.models.liquidity import LiquidityType
from engine.models.liquidity_event import (
    LiquidityEvent,
    LiquidityEventEvidence,
    LiquidityEventType,
)


class LiquidityEventEngine:
    """
    Analyze liquidity pools and classify resulting market events.
    """

    def __init__(
        self,
        config: LiquidityEventConfig | None = None,
    ) -> None:

        self.config = config or LiquidityEventConfig()

    # =========================================================
    # PUBLIC API
    # =========================================================

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

    # =========================================================
    # POOL ROUTING
    # =========================================================

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

    # =========================================================
    # BUY-SIDE BREACH DETECTION
    # =========================================================

    def _check_buy_side(
        self,
        pool,
        candles,
    ):

        for index, candle in enumerate(candles):

            # Never examine candles from before the pool existed.
            if candle.timestamp <= pool.created_at:
                continue

            # Strict breach.
            if candle.high <= pool.price:
                continue

            confirmation_candles = candles[
                index + 1 :
                index + 1 + self.config.confirmation_candles
            ]

            # Breach happened, but there is no candle available
            # to confirm the event yet.
            if not confirmation_candles:

                return self._unconfirmed_breach(
                    pool=pool,
                    breach_candle=candle,
                    side=LiquidityType.BUY_SIDE,
                )

            return self._classify_buy_side(
                pool=pool,
                breach_candle=candle,
                confirmation_candles=confirmation_candles,
            )

        # No breach occurred.
        return self._no_event(
            pool=pool,
        )

    # =========================================================
    # SELL-SIDE BREACH DETECTION
    # =========================================================

    def _check_sell_side(
        self,
        pool,
        candles,
    ):

        for index, candle in enumerate(candles):

            # Never examine candles from before the pool existed.
            if candle.timestamp <= pool.created_at:
                continue

            # Strict breach.
            if candle.low >= pool.price:
                continue

            confirmation_candles = candles[
                index + 1 :
                index + 1 + self.config.confirmation_candles
            ]

            # Breach happened, but there is no candle available
            # to confirm the event yet.
            if not confirmation_candles:

                return self._unconfirmed_breach(
                    pool=pool,
                    breach_candle=candle,
                    side=LiquidityType.SELL_SIDE,
                )

            return self._classify_sell_side(
                pool=pool,
                breach_candle=candle,
                confirmation_candles=confirmation_candles,
            )

        # No breach occurred.
        return self._no_event(
            pool=pool,
        )

    # =========================================================
    # BUY-SIDE CLASSIFICATION
    # =========================================================

    def _classify_buy_side(
        self,
        pool,
        breach_candle,
        confirmation_candles,
    ):

        # -----------------------------------------------------
        # Sweep
        #
        # Buy-side liquidity is swept when price trades above
        # the level and subsequently closes back below it.
        # -----------------------------------------------------

        for candle in confirmation_candles:

            if candle.close < pool.price:

                rejection_strength = (
                    self._calculate_rejection_strength(
                        pool=pool,
                        breach_candle=breach_candle,
                        confirmation_close=candle.close,
                    )
                )

                confidence = self._calculate_confidence(
                    event_type=LiquidityEventType.SWEEP,
                    rejection_strength=rejection_strength,
                    candles_checked=len(confirmation_candles),
                )

                reasons = [
                    "Buy-side liquidity was breached.",
                    "Price traded above the liquidity level.",
                    "Price subsequently closed below the level.",
                    (
                        f"Rejection confirmed within "
                        f"{len(confirmation_candles)} candles."
                    ),
                ]

                reasons.append(
                    self._rejection_description(
                        rejection_strength
                    )
                )

                reasons.append(
                    "Classified as SWEEP."
                )

                evidence = LiquidityEventEvidence(
                    liquidity_breached=True,
                    rejection_confirmed=True,
                    continuation_confirmed=False,
                    candles_checked=len(confirmation_candles),
                    rejection_strength=rejection_strength,
                )

                return LiquidityEvent(
                    pool=pool,
                    detected=True,
                    event_type=LiquidityEventType.SWEEP,
                    event_price=breach_candle.high,
                    event_timestamp=breach_candle.timestamp,
                    confidence=confidence,
                    evidence=evidence,
                    reasons=tuple(reasons),
                )

        # -----------------------------------------------------
        # Breakout
        #
        # Every confirmation candle must close above the level.
        # -----------------------------------------------------

        continuation_confirmed = all(
            candle.close > pool.price
            for candle in confirmation_candles
        )

        if continuation_confirmed:

            confidence = self._calculate_confidence(
                event_type=LiquidityEventType.BREAKOUT,
                rejection_strength=0.0,
                candles_checked=len(confirmation_candles),
            )

            reasons = (
                "Buy-side liquidity was breached.",
                "Price traded above the liquidity level.",
                "Price continued above the liquidity level.",
                (
                    f"Continuation confirmed across "
                    f"{len(confirmation_candles)} candles."
                ),
                "Classified as BREAKOUT.",
            )

            evidence = LiquidityEventEvidence(
                liquidity_breached=True,
                rejection_confirmed=False,
                continuation_confirmed=True,
                candles_checked=len(confirmation_candles),
                rejection_strength=0.0,
            )

            return LiquidityEvent(
                pool=pool,
                detected=True,
                event_type=LiquidityEventType.BREAKOUT,
                event_price=breach_candle.high,
                event_timestamp=breach_candle.timestamp,
                confidence=confidence,
                evidence=evidence,
                reasons=reasons,
            )

        # Breached, but not yet classified.
        return self._unconfirmed_breach(
            pool=pool,
            breach_candle=breach_candle,
            side=LiquidityType.BUY_SIDE,
            candles_checked=len(confirmation_candles),
        )

    # =========================================================
    # SELL-SIDE CLASSIFICATION
    # =========================================================

    def _classify_sell_side(
        self,
        pool,
        breach_candle,
        confirmation_candles,
    ):

        # -----------------------------------------------------
        # Sweep
        #
        # Sell-side liquidity is swept when price trades below
        # the level and subsequently closes back above it.
        # -----------------------------------------------------

        for candle in confirmation_candles:

            if candle.close > pool.price:

                rejection_strength = (
                    self._calculate_rejection_strength(
                        pool=pool,
                        breach_candle=breach_candle,
                        confirmation_close=candle.close,
                    )
                )

                confidence = self._calculate_confidence(
                    event_type=LiquidityEventType.SWEEP,
                    rejection_strength=rejection_strength,
                    candles_checked=len(confirmation_candles),
                )

                reasons = [
                    "Sell-side liquidity was breached.",
                    "Price traded below the liquidity level.",
                    "Price subsequently closed above the level.",
                    (
                        f"Rejection confirmed within "
                        f"{len(confirmation_candles)} candles."
                    ),
                ]

                reasons.append(
                    self._rejection_description(
                        rejection_strength
                    )
                )

                reasons.append(
                    "Classified as SWEEP."
                )

                evidence = LiquidityEventEvidence(
                    liquidity_breached=True,
                    rejection_confirmed=True,
                    continuation_confirmed=False,
                    candles_checked=len(confirmation_candles),
                    rejection_strength=rejection_strength,
                )

                return LiquidityEvent(
                    pool=pool,
                    detected=True,
                    event_type=LiquidityEventType.SWEEP,
                    event_price=breach_candle.low,
                    event_timestamp=breach_candle.timestamp,
                    confidence=confidence,
                    evidence=evidence,
                    reasons=tuple(reasons),
                )

        # -----------------------------------------------------
        # Breakout
        #
        # Every confirmation candle must close below the level.
        # -----------------------------------------------------

        continuation_confirmed = all(
            candle.close < pool.price
            for candle in confirmation_candles
        )

        if continuation_confirmed:

            confidence = self._calculate_confidence(
                event_type=LiquidityEventType.BREAKOUT,
                rejection_strength=0.0,
                candles_checked=len(confirmation_candles),
            )

            reasons = (
                "Sell-side liquidity was breached.",
                "Price traded below the liquidity level.",
                "Price continued below the liquidity level.",
                (
                    f"Continuation confirmed across "
                    f"{len(confirmation_candles)} candles."
                ),
                "Classified as BREAKOUT.",
            )

            evidence = LiquidityEventEvidence(
                liquidity_breached=True,
                rejection_confirmed=False,
                continuation_confirmed=True,
                candles_checked=len(confirmation_candles),
                rejection_strength=0.0,
            )

            return LiquidityEvent(
                pool=pool,
                detected=True,
                event_type=LiquidityEventType.BREAKOUT,
                event_price=breach_candle.low,
                event_timestamp=breach_candle.timestamp,
                confidence=confidence,
                evidence=evidence,
                reasons=reasons,
            )

        # Breached, but not yet classified.
        return self._unconfirmed_breach(
            pool=pool,
            breach_candle=breach_candle,
            side=LiquidityType.SELL_SIDE,
            candles_checked=len(confirmation_candles),
        )

    # =========================================================
    # REJECTION STRENGTH
    # =========================================================


    def _calculate_rejection_strength(
        self,
        pool,
        breach_candle,
        confirmation_close,
    ) -> float:
        """
        Calculate rejection strength from 0 to 100.

        Rejection strength is based on how far price moved
        away from the liquidity level after the breach,
        relative to the original breach distance.
        """

        if pool.liquidity_type == LiquidityType.BUY_SIDE:

            event_price = breach_candle.high

        else:

            event_price = breach_candle.low

        breach_distance = abs(
            event_price - pool.price
    )

        if breach_distance <= 0:
            return 0.0

        rejection_distance = abs(
            event_price - confirmation_close
    )

        strength = (
            rejection_distance
            / breach_distance
        ) * 100.0

        return min(
            max(strength, 0.0),
            100.0,
    )


    # =========================================================
    # REJECTION DESCRIPTION
    # =========================================================

    def _rejection_description(
        self,
        rejection_strength,
    ) -> str:

        if rejection_strength >= 70.0:

            return "Strong rejection detected."

        if rejection_strength >= 40.0:

            return "Moderate rejection detected."

        return "Weak rejection detected."

    # =========================================================
    # CONFIDENCE
    # =========================================================

    def _calculate_confidence(
        self,
        event_type,
        rejection_strength,
        candles_checked,
    ) -> float:

        if event_type == LiquidityEventType.SWEEP:

            base = 70.0

            rejection_component = (
                rejection_strength * 0.20
            )

        elif event_type == LiquidityEventType.BREAKOUT:

            base = 70.0

            rejection_component = 0.0

        else:

            return 0.0

        confirmation_component = min(
            candles_checked * 2.0,
            8.0,
        )

        confidence = (
            base
            + rejection_component
            + confirmation_component
        )

        return min(
            max(
                confidence,
                0.0,
            ),
            self.config.maximum_confidence,
        )

    # =========================================================
    # NO BREACH
    # =========================================================

    def _no_event(
        self,
        pool,
    ):

        evidence = LiquidityEventEvidence(
            liquidity_breached=False,
            rejection_confirmed=False,
            continuation_confirmed=False,
            candles_checked=0,
            rejection_strength=0.0,
        )

        return LiquidityEvent(
            pool=pool,
            detected=False,
            event_type=LiquidityEventType.NONE,
            event_price=None,
            event_timestamp=None,
            confidence=0.0,
            evidence=evidence,
            reasons=(
                "Liquidity remains active.",
            ),
        )

    # =========================================================
    # BREACHED BUT NOT CONFIRMED
    # =========================================================

    def _unconfirmed_breach(
        self,
        pool,
        breach_candle,
        side,
        candles_checked=0,
    ):

        if side == LiquidityType.BUY_SIDE:

            reason = (
                "Buy-side liquidity was breached, "
                "but confirmation is incomplete."
            )

        else:

            reason = (
                "Sell-side liquidity was breached, "
                "but confirmation is incomplete."
            )

        evidence = LiquidityEventEvidence(
            liquidity_breached=True,
            rejection_confirmed=False,
            continuation_confirmed=False,
            candles_checked=candles_checked,
            rejection_strength=0.0,
        )

        event_price = (
            breach_candle.high
            if side == LiquidityType.BUY_SIDE
            else breach_candle.low
        )

        return LiquidityEvent(
            pool=pool,
            detected=False,
            event_type=LiquidityEventType.NONE,
            event_price=event_price,
            event_timestamp=breach_candle.timestamp,
            confidence=0.0,
            evidence=evidence,
            reasons=(
                reason,
                "Event classification deferred.",
            ),
        )
