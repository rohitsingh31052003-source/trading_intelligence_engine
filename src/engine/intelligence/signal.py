"""
Signal Generation Engine (Sprint 11C).

The SignalEngine converts a DecisionContext (Sprint 11B)
into a structured, immutable trade setup (SignalResult).

Pipeline placement:

    Market Data
      -> Structure
      -> BOS / CHOCH
      -> Liquidity
      -> Trend / Bias
      -> Confluence Engine (11A)
      -> Decision Engine (11B)
      -> Signal Engine (11C)

The Signal Engine:

- consumes the decision layer plus relevant market context
- determines direction, entry, stop, target, risk, reward
- preserves the decision-layer confidence
- never executes trades or connects to a broker
- never uses future candles (hard no-look-ahead rule)
- is fully deterministic for identical inputs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.models.decision import (
    DecisionContext,
    DecisionDirection,
    DecisionStatus,
    SetupQuality,
)
from engine.models.signal import (
    EntrySource,
    Invalidation,
    SignalDirection,
    SignalResult,
    SignalState,
)


# ============================================================
# CONTEXT
# ============================================================


@dataclass(frozen=True)
class SignalContext:
    """
    Optional market context the Signal Engine may use to derive
    entry, stop and target prices.

    Only information available at or before ``reference_time``
    may be supplied. Future candles must never be passed.

    Attributes:

        trigger_close:
            Close price of the candle that triggered the
            setup (candle T). Used as the default entry.

        structure_break_level:
            A confirmed structure-break price known at T.
            Used as a structural entry / invalidation anchor.

        liquidity_level:
            A liquidity pool price known at T. May be used
            as an entry or as a structural target.

        supplied_entry:
            An explicit execution price supplied by the
            caller. Takes precedence over derived entries.

        reference_time:
            The candle T timestamp. Used only to filter
            context that is strictly older than T. It is
            never used to read future data.
    """

    trigger_close: float | None = None
    structure_break_level: float | None = None
    liquidity_level: float | None = None
    supplied_entry: float | None = None
    reference_time: datetime | None = None


# ============================================================
# ENGINE
# ============================================================


class SignalEngine:
    """
    Convert a DecisionContext into a SignalResult.

    The engine does NOT generate trades. It only produces a
    structured setup that downstream (non-existent in this
    sprint) execution layers may consume.
    """

    # ---------------------------------------------------------
    # CONFIGURATION
    # ---------------------------------------------------------

    # Minimum reward:risk required for a setup to be eligible.
    MIN_RISK_REWARD = 1.5

    # Default reward:risk used by the deterministic fallback
    # target when no structural / liquidity target exists.
    DEFAULT_RISK_REWARD = 2.0

    # Minimum distance (in price units) between entry and stop
    # to avoid zero-risk / degenerate setups.
    MIN_STOP_DISTANCE = 0.0

    # ---------------------------------------------------------
    # ANALYZE
    # ---------------------------------------------------------

    def analyze(
        self,
        decision: DecisionContext | None,
        context: SignalContext | None = None,
    ) -> SignalResult:
        """
        Convert a DecisionContext into a SignalResult.

        Deterministic: identical inputs always produce
        identical outputs.
        """

        context = context or SignalContext()

        if decision is None:
            return self._no_signal(
                decision_direction=DecisionDirection.UNKNOWN,
                reasons=[
                    "No decision context available."
                ],
            )

        direction = decision.direction

        # -------------------------------------------------
        # DIRECTION GATING
        # -------------------------------------------------

        if not self._is_directional(direction):
            return self._no_signal(
                decision_direction=direction,
                reasons=self._no_direction_reasons(
                    decision
                ),
            )

        if not self._is_eligible(decision):
            return self._no_signal(
                decision_direction=direction,
                reasons=self._not_eligible_reasons(
                    decision
                ),
            )

        signal_direction = (
            self._signal_direction(direction)
        )

        # -------------------------------------------------
        # ENTRY
        # -------------------------------------------------

        entry_price, entry_source = self._calculate_entry(
            direction=signal_direction,
            context=context,
        )

        if not self._is_valid_price(entry_price):
            return self._invalid(
                decision_direction=direction,
                signal_direction=signal_direction,
                confidence=decision.confidence,
                reasons=[
                    f"Decision is {direction.value.lower()}.",
                    "Entry price could not be determined.",
                ],
            )

        # -------------------------------------------------
        # STOP LOSS
        # -------------------------------------------------

        stop_loss = self._calculate_stop_loss(
            direction=signal_direction,
            entry_price=entry_price,
            context=context,
        )

        if not self._is_valid_stop(
            signal_direction,
            entry_price,
            stop_loss,
        ):
            return self._invalid(
                decision_direction=direction,
                signal_direction=signal_direction,
                entry_price=entry_price,
                entry_source=entry_source,
                confidence=decision.confidence,
                reasons=[
                    f"Decision is {direction.value.lower()}.",
                    "Stop loss is invalid for the signal "
                    "direction.",
                ],
            )

        # -------------------------------------------------
        # TAKE PROFIT
        # -------------------------------------------------

        take_profit = self._calculate_take_profit(
            direction=signal_direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            context=context,
        )

        if not self._is_valid_target(
            signal_direction,
            entry_price,
            take_profit,
        ):
            return self._invalid(
                decision_direction=direction,
                signal_direction=signal_direction,
                entry_price=entry_price,
                entry_source=entry_source,
                stop_loss=stop_loss,
                confidence=decision.confidence,
                reasons=[
                    f"Decision is {direction.value.lower()}.",
                    "Take profit is invalid for the signal "
                    "direction.",
                ],
            )

        # -------------------------------------------------
        # RISK / REWARD
        # -------------------------------------------------

        risk, reward, ratio = self._calculate_risk_reward(
            direction=signal_direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if risk <= 0 or reward <= 0:
            return self._invalid(
                decision_direction=direction,
                signal_direction=signal_direction,
                entry_price=entry_price,
                entry_source=entry_source,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=decision.confidence,
                reasons=[
                    f"Decision is {direction.value.lower()}.",
                    "Risk or reward is not positive; setup "
                    "rejected.",
                ],
            )

        # -------------------------------------------------
        # CONFIDENCE
        # -------------------------------------------------

        confidence = self._bound_confidence(
            decision.confidence
        )

        # -------------------------------------------------
        # QUALITY
        # -------------------------------------------------

        quality = self._classify_quality(
            decision=decision,
            confidence=confidence,
            ratio=ratio,
        )

        # -------------------------------------------------
        # MIN R:R
        # -------------------------------------------------

        meets_min_rr = ratio >= self.MIN_RISK_REWARD

        eligible = (
            meets_min_rr
            and quality != SetupQuality.INVALID
        )

        # -------------------------------------------------
        # INVALIDATION
        # -------------------------------------------------

        invalidation = self._build_invalidation(
            direction=signal_direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
        )

        # -------------------------------------------------
        # REASONS
        # -------------------------------------------------

        reasons = self._build_reasons(
            direction=direction,
            decision=decision,
            entry_price=entry_price,
            entry_source=entry_source,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            reward=reward,
            ratio=ratio,
            confidence=confidence,
            quality=quality,
            eligible=eligible,
            meets_min_rr=meets_min_rr,
        )

        if not eligible:
            state = SignalState.INVALID
        else:
            state = (
                SignalState.LONG
                if signal_direction is SignalDirection.LONG
                else SignalState.SHORT
            )

        return SignalResult(
            direction=signal_direction,
            state=state,
            entry_price=entry_price,
            entry_source=entry_source,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_unit=risk,
            reward_per_unit=reward,
            risk_reward_ratio=ratio,
            confidence=confidence,
            quality=quality,
            eligible=eligible,
            invalidation=invalidation,
            decision_direction=direction,
            reasons=tuple(reasons),
        )

    # ========================================================
    # DIRECTION GATING
    # ========================================================

    def _is_directional(
        self,
        direction: DecisionDirection,
    ) -> bool:

        return direction in (
            DecisionDirection.BULLISH,
            DecisionDirection.BEARISH,
        )

    def _is_eligible(
        self,
        decision: DecisionContext,
    ) -> bool:

        if decision.status not in (
            DecisionStatus.READY,
        ):
            return False

        if not decision.trade_eligible:
            return False

        if decision.setup_quality == SetupQuality.INVALID:
            return False

        return True

    def _signal_direction(
        self,
        direction: DecisionDirection,
    ) -> SignalDirection:

        if direction == DecisionDirection.BULLISH:
            return SignalDirection.LONG

        if direction == DecisionDirection.BEARISH:
            return SignalDirection.SHORT

        return SignalDirection.NONE

    # ========================================================
    # ENTRY
    # ========================================================

    def _calculate_entry(
        self,
        *,
        direction: SignalDirection,
        context: SignalContext,
    ) -> tuple[float | None, EntrySource | None]:
        """
        Deterministically resolve the entry price.

        Precedence (deliberate and explicit):

        1. Supplied execution price.
        2. Trigger candle close (the natural default entry).
        3. Confirmed structure-break level.
        4. Relevant liquidity level.

        Structural / liquidity levels are primarily used as
        stop and target anchors; they only become the entry
        when no trigger close or supplied price exists.

        No future market information is consulted.
        """

        if self._is_valid_price(context.supplied_entry):
            return (
                float(context.supplied_entry),
                EntrySource.SUPPLIED,
            )

        if self._is_valid_price(context.trigger_close):
            return (
                float(context.trigger_close),
                EntrySource.TRIGGER_CLOSE,
            )

        if self._is_valid_price(
            context.structure_break_level
        ):
            return (
                float(context.structure_break_level),
                EntrySource.STRUCTURE_BREAK,
            )

        if self._is_valid_price(context.liquidity_level):
            return (
                float(context.liquidity_level),
                EntrySource.LIQUIDITY_LEVEL,
            )

        return (None, None)

    # ========================================================
    # STOP LOSS
    # ========================================================

    def _calculate_stop_loss(
        self,
        *,
        direction: SignalDirection,
        entry_price: float,
        context: SignalContext,
    ) -> float | None:
        """
        Direction-aware stop loss.

        Structural anchors are preferred where available:

        - LONG: the most relevant structural low / liquidity
          low below the entry.
        - SHORT: the most relevant structural high / liquidity
          high above the entry.

        If no structural anchor exists, a deterministic
        fraction of the entry is used as a documented
        fallback. Future prices are never consulted.
        """

        structural = self._select_structural_stop(
            direction,
            entry_price,
            context,
        )

        if structural is not None:
            return structural

        return self._fallback_stop(
            direction,
            entry_price,
        )

    def _select_structural_stop(
        self,
        direction: SignalDirection,
        entry_price: float,
        context: SignalContext,
    ) -> float | None:
        """
        Pick a structural / liquidity level on the correct
        side of the entry to act as the stop.

        LONG: stop below entry -> choose the largest level
        that is still strictly below the entry.

        SHORT: stop above entry -> choose the smallest level
        that is still strictly above the entry.
        """

        candidates: list[float] = []

        if self._is_valid_price(
            context.structure_break_level
        ):
            candidates.append(
                float(context.structure_break_level)
            )

        if self._is_valid_price(context.liquidity_level):
            candidates.append(
                float(context.liquidity_level)
            )

        if direction is SignalDirection.LONG:

            below = [
                level
                for level in candidates
                if level < entry_price
            ]

            if below:
                # Largest below entry = closest structural low.
                return max(below)

            return None

        if direction is SignalDirection.SHORT:

            above = [
                level
                for level in candidates
                if level > entry_price
            ]

            if above:
                # Smallest above entry = closest structural high.
                return min(above)

            return None

        return None

    def _fallback_stop(
        self,
        direction: SignalDirection,
        entry_price: float,
    ) -> float:
        """
        Deterministic fallback stop.

        Uses a fixed fraction of the entry price. This is a
        documented fallback used only when no structural /
        liquidity anchor is available. It does not invent
        market structure.
        """

        fraction = 0.02

        if direction is SignalDirection.LONG:
            return round(
                entry_price * (1 - fraction),
                8,
            )

        if direction is SignalDirection.SHORT:
            return round(
                entry_price * (1 + fraction),
                8,
            )

        # Should not happen for a directional signal.
        return entry_price

    # ========================================================
    # TAKE PROFIT
    # ========================================================

    def _calculate_take_profit(
        self,
        *,
        direction: SignalDirection,
        entry_price: float,
        stop_loss: float,
        context: SignalContext,
    ) -> float | None:
        """
        Direction-aware take profit.

        Structural / liquidity targets are preferred where
        they sit on the correct side of the entry. If none
        are available, a deterministic R-multiple fallback
        is used. Future prices are never consulted.
        """

        risk = abs(entry_price - stop_loss)

        if risk <= 0:
            return None

        structural = self._select_structural_target(
            direction,
            entry_price,
            context,
        )

        if structural is not None:
            return structural

        return self._fallback_target(
            direction,
            entry_price,
            risk,
        )

    def _select_structural_target(
        self,
        direction: SignalDirection,
        entry_price: float,
        context: SignalContext,
    ) -> float | None:
        """
        Pick a structural / liquidity level on the correct
        side of the entry to act as the target.

        LONG: target above entry -> smallest level above.
        SHORT: target below entry -> largest level below.
        """

        candidates: list[float] = []

        if self._is_valid_price(
            context.structure_break_level
        ):
            candidates.append(
                float(context.structure_break_level)
            )

        if self._is_valid_price(context.liquidity_level):
            candidates.append(
                float(context.liquidity_level)
            )

        if direction is SignalDirection.LONG:

            above = [
                level
                for level in candidates
                if level > entry_price
            ]

            if above:
                # Closest structural target above entry.
                return min(above)

            return None

        if direction is SignalDirection.SHORT:

            below = [
                level
                for level in candidates
                if level < entry_price
            ]

            if below:
                # Closest structural target below entry.
                return max(below)

            return None

        return None

    def _fallback_target(
        self,
        direction: SignalDirection,
        entry_price: float,
        risk: float,
    ) -> float:
        """
        Deterministic R-multiple fallback target.

        Uses DEFAULT_RISK_REWARD. This is a documented
        fallback used only when no structural / liquidity
        target is available.
        """

        reward = risk * self.DEFAULT_RISK_REWARD

        if direction is SignalDirection.LONG:
            return round(
                entry_price + reward,
                8,
            )

        if direction is SignalDirection.SHORT:
            return round(
                entry_price - reward,
                8,
            )

        return entry_price

    # ========================================================
    # RISK / REWARD
    # ========================================================

    def _calculate_risk_reward(
        self,
        *,
        direction: SignalDirection,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[float, float, float]:
        """
        Compute risk, reward and reward:risk.

        LONG:
            risk = entry - stop
            reward = target - entry

        SHORT:
            risk = stop - entry
            reward = entry - target
        """

        if direction is SignalDirection.LONG:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price

        elif direction is SignalDirection.SHORT:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit

        else:
            return (0.0, 0.0, 0.0)

        if risk <= 0:
            return (risk, reward, 0.0)

        ratio = round(reward / risk, 8)

        return (
            round(risk, 8),
            round(reward, 8),
            ratio,
        )

    # ========================================================
    # CONFIDENCE
    # ========================================================

    def _bound_confidence(
        self,
        confidence: float,
    ) -> float:
        """
        Preserve the decision-layer confidence, clamped to
        [0, 100]. No artificial score is introduced.
        """

        try:
            value = float(confidence)
        except (TypeError, ValueError):
            return 0.0

        return round(
            min(100.0, max(0.0, value)),
            2,
        )

    # ========================================================
    # QUALITY
    # ========================================================

    def _classify_quality(
        self,
        *,
        decision: DecisionContext,
        confidence: float,
        ratio: float,
    ) -> SetupQuality:
        """
        Map the signal onto the project's existing
        SetupQuality enum (INVALID / WEAK / MODERATE /
        STRONG / EXCELLENT).

        Deterministic and explainable. The decision layer's
        own setup_quality is the primary input; the R:R acts
        as a secondary quality signal.
        """

        base = decision.setup_quality

        if base == SetupQuality.INVALID:
            return SetupQuality.INVALID

        if ratio < self.MIN_RISK_REWARD:
            # A sub-threshold R:R downgrades the signal but
            # does not necessarily invalidate the underlying
            # decision evidence.
            if base in (
                SetupQuality.STRONG,
                SetupQuality.EXCELLENT,
            ):
                return SetupQuality.MODERATE

            return SetupQuality.WEAK

        if confidence >= 85.0 and ratio >= 2.0:
            return SetupQuality.EXCELLENT

        if confidence >= 70.0 and ratio >= 1.5:
            return SetupQuality.STRONG

        if confidence >= 55.0:
            return SetupQuality.MODERATE

        return SetupQuality.WEAK

    # ========================================================
    # INVALIDATION
    # ========================================================

    def _build_invalidation(
        self,
        *,
        direction: SignalDirection,
        entry_price: float,
        stop_loss: float,
    ) -> Invalidation:

        if direction is SignalDirection.LONG:

            condition = (
                f"Price reaches or breaks {stop_loss:.2f}, "
                "or the bullish structure that created the "
                "setup becomes invalid."
            )

            return Invalidation(
                price=stop_loss,
                condition=condition,
            )

        if direction is SignalDirection.SHORT:

            condition = (
                f"Price reaches or breaks {stop_loss:.2f}, "
                "or the bearish structure that created the "
                "setup becomes invalid."
            )

            return Invalidation(
                price=stop_loss,
                condition=condition,
            )

        return Invalidation(
            price=None,
            condition="No directional setup to invalidate.",
        )

    # ========================================================
    # VALIDATION HELPERS
    # ========================================================

    def _is_valid_price(
        self,
        value: Any,
    ) -> bool:

        if value is None:
            return False

        try:
            number = float(value)
        except (TypeError, ValueError):
            return False

        return number > 0

    def _is_valid_stop(
        self,
        direction: SignalDirection,
        entry_price: float,
        stop_loss: float | None,
    ) -> bool:

        if stop_loss is None:
            return False

        if not self._is_valid_price(stop_loss):
            return False

        if direction is SignalDirection.LONG:
            return stop_loss < entry_price

        if direction is SignalDirection.SHORT:
            return stop_loss > entry_price

        return False

    def _is_valid_target(
        self,
        direction: SignalDirection,
        entry_price: float,
        take_profit: float | None,
    ) -> bool:

        if take_profit is None:
            return False

        if not self._is_valid_price(take_profit):
            return False

        if direction is SignalDirection.LONG:
            return take_profit > entry_price

        if direction is SignalDirection.SHORT:
            return take_profit < entry_price

        return False

    # ========================================================
    # REASONS
    # ========================================================

    def _no_direction_reasons(
        self,
        decision: DecisionContext,
    ) -> list[str]:

        reasons: list[str] = []

        if decision.direction == DecisionDirection.NEUTRAL:
            reasons.append("Decision is neutral.")
        elif decision.direction == DecisionDirection.UNKNOWN:
            reasons.append("Decision direction is unknown.")
        else:
            reasons.append(
                "Decision direction is not actionable."
            )

        reasons.append(
            "No signal can be generated without a clear "
            "directional decision."
        )

        return reasons

    def _not_eligible_reasons(
        self,
        decision: DecisionContext,
    ) -> list[str]:

        reasons: list[str] = []

        reasons.append(
            f"Decision is {decision.direction.value.lower()}."
        )

        reasons.append(
            f"Decision status is "
            f"{decision.status.value}; not eligible."
        )

        if decision.trade_eligible is False:
            reasons.append(
                "Decision layer marked the setup as not "
                "trade-eligible."
            )

        return reasons

    def _build_reasons(
        self,
        *,
        direction: DecisionDirection,
        decision: DecisionContext,
        entry_price: float,
        entry_source: EntrySource,
        stop_loss: float,
        take_profit: float,
        risk: float,
        reward: float,
        ratio: float,
        confidence: float,
        quality: SetupQuality,
        eligible: bool,
        meets_min_rr: bool,
    ) -> list[str]:

        reasons: list[str] = []

        reasons.append(
            f"Decision is {direction.value.lower()}."
        )

        reasons.append(
            f"Entry derived from "
            f"{entry_source.value.lower()} at "
            f"{entry_price:.2f}."
        )

        reasons.append(
            f"Stop loss set at {stop_loss:.2f}."
        )

        reasons.append(
            f"Take profit set at {take_profit:.2f}."
        )

        reasons.append(
            f"Risk per unit is {risk:.2f}; "
            f"reward per unit is {reward:.2f}."
        )

        reasons.append(
            f"Risk/reward ratio is {ratio:.2f}."
        )

        if meets_min_rr:
            reasons.append(
                "Risk/reward requirement satisfied."
            )
        else:
            reasons.append(
                f"Risk/reward below minimum "
                f"({self.MIN_RISK_REWARD:.2f}); setup not "
                "eligible."
            )

        reasons.append(
            f"Confidence preserved at {confidence:.1f}."
        )

        reasons.append(
            f"Signal quality is {quality.value.lower()}."
        )

        if eligible:
            reasons.append(
                "Setup is eligible."
            )
            reasons.append(
                "Signal generated successfully."
            )
        else:
            reasons.append(
                "Setup is not eligible."
            )

        return reasons

    # ========================================================
    # RESULT BUILDERS
    # ========================================================

    def _no_signal(
        self,
        *,
        decision_direction: DecisionDirection,
        reasons: list[str],
    ) -> SignalResult:

        return SignalResult(
            direction=SignalDirection.NONE,
            state=SignalState.NO_SIGNAL,
            entry_price=None,
            entry_source=None,
            stop_loss=None,
            take_profit=None,
            risk_per_unit=0.0,
            reward_per_unit=0.0,
            risk_reward_ratio=0.0,
            confidence=0.0,
            quality=SetupQuality.INVALID,
            eligible=False,
            invalidation=Invalidation(
                price=None,
                condition="No signal generated.",
            ),
            decision_direction=decision_direction,
            reasons=tuple(reasons),
        )

    def _invalid(
        self,
        *,
        decision_direction: DecisionDirection,
        signal_direction: SignalDirection,
        confidence: float = 0.0,
        entry_price: float | None = None,
        entry_source: EntrySource | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reasons: list[str],
    ) -> SignalResult:

        risk, reward, ratio = self._calculate_risk_reward(
            direction=signal_direction,
            entry_price=entry_price or 0.0,
            stop_loss=stop_loss or 0.0,
            take_profit=take_profit or 0.0,
        )

        if entry_price is None or stop_loss is None:
            invalidation = Invalidation(
                price=None,
                condition="Setup rejected before "
                "invalidation could be defined.",
            )
        else:
            invalidation = self._build_invalidation(
                direction=signal_direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
            )

        return SignalResult(
            direction=signal_direction,
            state=SignalState.INVALID,
            entry_price=entry_price,
            entry_source=entry_source,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_unit=risk,
            reward_per_unit=reward,
            risk_reward_ratio=ratio,
            confidence=self._bound_confidence(confidence),
            quality=SetupQuality.INVALID,
            eligible=False,
            invalidation=invalidation,
            decision_direction=decision_direction,
            reasons=tuple(reasons),
        )
