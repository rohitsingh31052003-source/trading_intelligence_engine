from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.models.confluence import (
    ConfluenceDirection,
    ConfluenceEvidence,
    ConfluenceResult,
    EvidenceStrength,
)


# ============================================================
# CONFIGURATION
# ============================================================

# Maximum liquidity contribution that can be assigned to
# bullish evidence.
MAX_BULLISH_LIQUIDITY_SCORE = 40.0

# Maximum liquidity contribution that can be assigned to
# bearish evidence.
MAX_BEARISH_LIQUIDITY_SCORE = 40.0

# Backward-compatible aggregate cap.
#
# This is retained because earlier Sprint 11A code and
# consumers may still reference MAX_LIQUIDITY_SCORE.
MAX_LIQUIDITY_SCORE = max(
    MAX_BULLISH_LIQUIDITY_SCORE,
    MAX_BEARISH_LIQUIDITY_SCORE,
)

# Minimum dominance required before a directional
# conclusion is produced.
#
# Example:
#
# Bullish = 55
# Bearish = 45
#
# dominance = 10 / 100 = 0.10
#
# Since 0.10 < 0.15, result remains NEUTRAL.
MIN_DOMINANCE = 0.15


# ============================================================
# LIQUIDITY CONTRIBUTION
# ============================================================

@dataclass(frozen=True, slots=True)
class LiquidityContribution:
    """
    Normalized contribution from a single liquidity event.

    The pool price is retained so the contribution can
    be mapped back to the exact liquidity zone that
    generated it.
    """

    direction: ConfluenceDirection
    score: float
    event_type: str
    timestamp: datetime | None
    pool_price: float | None


# ============================================================
# CONFLUENCE ENGINE
# ============================================================

class ConfluenceEngine:
    """
    Combine independent market signals into a final
    directional confluence assessment.
    """

    # ========================================================
    # ANALYZE
    # ========================================================

    def analyze(
        self,
        analysis,
        bos,
        choch,
        trend,
        liquidity_events=None,
        reference_time=None,
    ):
        """
        Analyze all available evidence.

        Evidence sources:

        - Market bias
        - Trend
        - BOS
        - CHOCH
        - Liquidity events

        The engine keeps bullish and bearish evidence
        independent and resolves the final direction using
        directional dominance.
        """

        evidence = []

        liquidity_events = liquidity_events or []

        # ----------------------------------------------------
        # MARKET BIAS
        # ----------------------------------------------------

        analysis_bias = self._normalize_direction(
            getattr(
                analysis,
                "bias",
                None,
            )
        )

        if analysis_bias in (
            ConfluenceDirection.BULLISH,
            ConfluenceDirection.BEARISH,
        ):
            evidence.append(
                ConfluenceEvidence(
                    name="Market Bias",
                    direction=analysis_bias,
                    score=30.0,
                    strength=EvidenceStrength.STRONG,
                    reason=(
                        f"Market bias is "
                        f"{analysis_bias.value.lower()}."
                    ),
                )
            )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        trend_direction = self._normalize_direction(
            getattr(
                trend,
                "state",
                None,
            )
        )

        if trend_direction in (
            ConfluenceDirection.BULLISH,
            ConfluenceDirection.BEARISH,
        ):
            evidence.append(
                ConfluenceEvidence(
                    name="Trend",
                    direction=trend_direction,
                    score=30.0,
                    strength=EvidenceStrength.STRONG,
                    reason=(
                        f"Market trend is "
                        f"{trend_direction.value.lower()}."
                    ),
                )
            )

        # ----------------------------------------------------
        # BOS
        # ----------------------------------------------------

        if getattr(
            bos,
            "detected",
            False,
        ):
            bos_direction = self._normalize_direction(
                getattr(
                    bos,
                    "type",
                    None,
                )
            )

            if bos_direction in (
                ConfluenceDirection.BULLISH,
                ConfluenceDirection.BEARISH,
            ):
                evidence.append(
                    ConfluenceEvidence(
                        name="BOS",
                        direction=bos_direction,
                        score=10.0,
                        strength=EvidenceStrength.MODERATE,
                        reason=(
                            f"{bos_direction.value.title()} "
                            "break of structure detected."
                        ),
                    )
                )

        # ----------------------------------------------------
        # CHOCH
        # ----------------------------------------------------

        if getattr(
            choch,
            "detected",
            False,
        ):
            choch_direction = self._normalize_direction(
                getattr(
                    choch,
                    "type",
                    None,
                )
            )

            if choch_direction in (
                ConfluenceDirection.BULLISH,
                ConfluenceDirection.BEARISH,
            ):
                evidence.append(
                    ConfluenceEvidence(
                        name="CHOCH",
                        direction=choch_direction,
                        score=10.0,
                        strength=EvidenceStrength.MODERATE,
                        reason=(
                            f"{choch_direction.value.title()} "
                            "change of character detected."
                        ),
                    )
                )

        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        if reference_time is None:
            reference_time = (
                self._latest_liquidity_timestamp(
                    liquidity_events
                )
            )

        self._add_liquidity_evidence(
            evidence=evidence,
            liquidity_events=liquidity_events,
            reference_time=reference_time,
        )

        # ----------------------------------------------------
        # FINAL RESULT
        # ----------------------------------------------------

        return self._build_result(
            evidence
        )

    # ========================================================
    # NORMALIZATION
    # ========================================================

    def _normalize_direction(
        self,
        value,
    ) -> ConfluenceDirection:
        """
        Normalize common direction representations.

        Supports:

        - ConfluenceDirection
        - strings
        - enum-like objects
        - objects exposing .name
        - objects exposing .value
        """

        if value is None:
            return ConfluenceDirection.UNKNOWN

        name = getattr(
            value,
            "name",
            "",
        )

        enum_value = getattr(
            value,
            "value",
            "",
        )

        text = (
            f"{name} "
            f"{enum_value} "
            f"{value}"
        ).upper()

        if "BULL" in text:
            return ConfluenceDirection.BULLISH

        if "BEAR" in text:
            return ConfluenceDirection.BEARISH

        if "NEUTRAL" in text:
            return ConfluenceDirection.NEUTRAL

        if "UNKNOWN" in text:
            return ConfluenceDirection.UNKNOWN

        return ConfluenceDirection.UNKNOWN

    # ========================================================
    # LIQUIDITY DIRECTION
    # ========================================================

    def _liquidity_event_direction(
        self,
        event,
    ) -> ConfluenceDirection:
        """
        Convert liquidity events into directional evidence.

        SELL-SIDE SWEEP
            -> BULLISH

        BUY-SIDE SWEEP
            -> BEARISH

        BUY-SIDE BREAKOUT
            -> BULLISH

        SELL-SIDE BREAKOUT
            -> BEARISH
        """

        if event is None:
            return ConfluenceDirection.UNKNOWN

        if not getattr(
            event,
            "detected",
            False,
        ):
            return ConfluenceDirection.UNKNOWN

        event_type = getattr(
            event,
            "event_type",
            None,
        )

        pool = getattr(
            event,
            "pool",
            None,
        )

        if pool is None:
            return ConfluenceDirection.UNKNOWN

        side = getattr(
            pool,
            "liquidity_type",
            None,
        )

        event_name = getattr(
            event_type,
            "name",
            str(event_type),
        ).upper()

        side_name = getattr(
            side,
            "name",
            str(side),
        ).upper()

        # ----------------------------------------------------
        # SWEEP
        # ----------------------------------------------------

        if event_name == "SWEEP":

            if "SELL" in side_name:
                return ConfluenceDirection.BULLISH

            if "BUY" in side_name:
                return ConfluenceDirection.BEARISH

        # ----------------------------------------------------
        # BREAKOUT
        # ----------------------------------------------------

        if event_name == "BREAKOUT":

            if "BUY" in side_name:
                return ConfluenceDirection.BULLISH

            if "SELL" in side_name:
                return ConfluenceDirection.BEARISH

        return ConfluenceDirection.UNKNOWN

    # ========================================================
    # LIQUIDITY BASE SCORE
    # ========================================================

    def _liquidity_base_score(
        self,
        event,
    ) -> float:
        """
        Base liquidity weights.

        BREAKOUT
            12

        SWEEP
            10
        """

        event_type = getattr(
            event,
            "event_type",
            None,
        )

        event_name = getattr(
            event_type,
            "name",
            str(event_type),
        ).upper()

        if event_name == "BREAKOUT":
            return 12.0

        if event_name == "SWEEP":
            return 10.0

        return 0.0

    # ========================================================
    # RECENCY
    # ========================================================

    def _recency_multiplier(
        self,
        event_time: datetime | None,
        reference_time: datetime | None,
    ) -> float:
        """
        Recency weighting.

        0-7 days
            1.00

        8-30 days
            0.85

        31-90 days
            0.65

        91-180 days
            0.45

        180+ days
            0.25
        """

        if (
            event_time is None
            or reference_time is None
        ):
            return 1.0

        age_days = max(
            0,
            (reference_time - event_time).days,
        )

        if age_days <= 7:
            return 1.00

        if age_days <= 30:
            return 0.85

        if age_days <= 90:
            return 0.65

        if age_days <= 180:
            return 0.45

        return 0.25

    # ========================================================
    # LIQUIDITY ZONE
    # ========================================================

    def _same_liquidity_zone(
        self,
        price_a: float,
        price_b: float,
        tolerance: float = 0.005,
    ) -> bool:
        """
        Determine whether two liquidity prices belong to
        the same liquidity zone.

        The default tolerance is 0.5%.

        A tighter tolerance prevents genuinely different
        liquidity pools from being incorrectly merged.
        """

        if price_a <= 0 or price_b <= 0:
            return False

        reference_price = max(
            price_a,
            price_b,
        )

        difference = abs(
            price_a - price_b
        )

        return (
            difference / reference_price
        ) <= tolerance

    # ========================================================
    # CONFLICT
    # ========================================================

    def _conflict_level(
        self,
        bullish_score: float,
        bearish_score: float,
    ) -> str:
        """
        Determine directional conflict.

        Difference ratio:

        >= 0.60
            LOW

        >= 0.30
            MEDIUM

        < 0.30
            HIGH
        """

        total = (
            bullish_score
            + bearish_score
        )

        if total <= 0:
            return "NONE"

        difference = abs(
            bullish_score
            - bearish_score
        )

        ratio = (
            difference / total
        )

        if ratio >= 0.60:
            return "LOW"

        if ratio >= 0.30:
            return "MEDIUM"

        return "HIGH"

    # ========================================================
    # LATEST LIQUIDITY TIMESTAMP
    # ========================================================

    def _latest_liquidity_timestamp(
        self,
        liquidity_events,
    ):
        """
        Return the latest liquidity event timestamp.
        """

        timestamps = []

        for event in liquidity_events:

            timestamp = getattr(
                event,
                "event_timestamp",
                None,
            )

            if timestamp is not None:
                timestamps.append(
                    timestamp
                )

        if not timestamps:
            return None

        return max(timestamps)

    # ========================================================
    # LIQUIDITY EVIDENCE
    # ========================================================

    def _add_liquidity_evidence(
        self,
        evidence,
        liquidity_events,
        reference_time=None,
    ):
        """
        Convert liquidity events into directional evidence.

        Processing:

        1. Normalize direction.
        2. Assign base score.
        3. Apply recency.
        4. Sort newest first.
        5. Deduplicate price zones.
        6. Apply directional cap.
        7. Add explainable evidence.
        """

        contributions = []

        # ----------------------------------------------------
        # NORMALIZE
        # ----------------------------------------------------

        for event in liquidity_events:

            direction = (
                self._liquidity_event_direction(
                    event
                )
            )

            if (
                direction
                == ConfluenceDirection.UNKNOWN
            ):
                continue

            base_score = (
                self._liquidity_base_score(
                    event
                )
            )

            if base_score <= 0:
                continue

            event_type = getattr(
                event,
                "event_type",
                None,
            )

            event_name = getattr(
                event_type,
                "name",
                str(event_type),
            ).upper()

            timestamp = getattr(
                event,
                "event_timestamp",
                None,
            )

            multiplier = (
                self._recency_multiplier(
                    timestamp,
                    reference_time,
                )
            )

            score = (
                base_score
                * multiplier
            )

            pool = getattr(
            event,
            "pool",
            None,
)

            pool_price = getattr(
            pool,
            "price",
            None,
)

            contributions.append(
                LiquidityContribution(
                    direction=direction,
                    score=score,
                    event_type=event_name,
                    timestamp=timestamp,
                    pool_price=pool_price,
                )
            )


        # ----------------------------------------------------
        # NEWEST FIRST
        # ----------------------------------------------------

        contributions.sort(
            key=lambda item: (
                item.timestamp is not None,
                item.timestamp or datetime.min,
                item.score,
            ),
            reverse=True,
        )

        # ----------------------------------------------------
        # TRACK ACCEPTED PRICE ZONES
        # ----------------------------------------------------

        accepted_zones = {
            ConfluenceDirection.BULLISH: [],
            ConfluenceDirection.BEARISH: [],
        }

        directional_scores = {
            ConfluenceDirection.BULLISH: 0.0,
            ConfluenceDirection.BEARISH: 0.0,
        }

        # ----------------------------------------------------
        # ACCEPT CONTRIBUTIONS
        # ----------------------------------------------------

        for contribution in contributions:

            direction = (
                contribution.direction
            )

            current_score = (
                directional_scores[
                    direction
                ]
            )

            if direction == ConfluenceDirection.BULLISH:
                maximum_score = MAX_BULLISH_LIQUIDITY_SCORE
            else:
                maximum_score = MAX_BEARISH_LIQUIDITY_SCORE

            remaining = (
                maximum_score
                - current_score
)

            if remaining <= 0:
                continue

            pool_price = contribution.pool_price


            # ------------------------------------------------
            # DEDUPLICATION
            # ------------------------------------------------

            if pool_price is not None:

                duplicate = any(
                    self._same_liquidity_zone(
                        pool_price,
                        existing_price,
                    )
                    for existing_price
                    in accepted_zones[
                        direction
                    ]
                )

                if duplicate:
                    accepted_zones[
                        direction
                    ].append(
                        pool_price
                    )
                    continue

            # ------------------------------------------------
            # DIRECTIONAL CAP
            # ------------------------------------------------

            contribution_score = min(
                contribution.score,
                remaining,
            )

            if contribution_score <= 0:
                continue

            directional_scores[
                direction
            ] += contribution_score

            if pool_price is not None:
                accepted_zones[
                    direction
                ].append(
                    pool_price
                )

            # ------------------------------------------------
            # EXPLAINABILITY
            # ------------------------------------------------

            if contribution.event_type == "SWEEP":

                strength = (
                    EvidenceStrength.STRONG
                )

                reason = (
                    f"{direction.value.title()} "
                    "liquidity sweep detected."
                )

            elif contribution.event_type == "BREAKOUT":

                strength = (
                    EvidenceStrength.MODERATE
                )

                reason = (
                    f"{direction.value.title()} "
                    "liquidity breakout detected."
                )

            else:
                continue

            evidence.append(
                ConfluenceEvidence(
                    name="Liquidity",
                    direction=direction,
                    score=contribution_score,
                    strength=strength,
                    reason=reason,
                )
            )

    # ========================================================
    # CONFIDENCE
    # ========================================================

    def _calculate_confidence(
        self,
        dominant_score: float,
        opposing_score: float,
        total_score: float | None = None,
    ) -> float:
        """
        Confidence represents the percentage of
        directional evidence controlled by the dominant side.

        Example:

            bullish = 80
            bearish = 20

            confidence = 80%
        """

        total = (
            dominant_score
            + opposing_score
        )

        if total <= 0:
            return 0.0

        confidence = (
            dominant_score
            / total
            * 100.0
        )

        return min(
            100.0,
            max(
                0.0,
                confidence,
            ),
        )

    # ========================================================
    # RESULT
    # ========================================================

    def _build_result(
        self,
        evidence,
    ):
        """
        Build the final ConfluenceResult.

        Important:

        UNKNOWN
            means there is no directional evidence.

        NEUTRAL
            means there is directional evidence, but neither
            side has enough dominance.

        BULLISH / BEARISH
            means one side has crossed the minimum dominance
            threshold.
        """

        # ----------------------------------------------------
        # NO EVIDENCE
        # ----------------------------------------------------

        if not evidence:

            return ConfluenceResult(
                direction=ConfluenceDirection.UNKNOWN,
                confidence=0.0,
                score=0.0,
                evidence=tuple(),
                reasons=(
                    "No directional evidence available.",
                ),
                bullish_score=0.0,
                bearish_score=0.0,
                net_score=0.0,
                conflict="NONE",
                liquidity_score=0.0,
            )

        # ----------------------------------------------------
        # DIRECTIONAL SCORES
        # ----------------------------------------------------

        bullish_score = sum(
            item.score
            for item in evidence
            if (
                item.direction
                == ConfluenceDirection.BULLISH
            )
        )

        bearish_score = sum(
            item.score
            for item in evidence
            if (
                item.direction
                == ConfluenceDirection.BEARISH
            )
        )

        net_score = (
            bullish_score
            - bearish_score
        )

        total_score = (
            bullish_score
            + bearish_score
        )

        # ----------------------------------------------------
        # DOMINANCE
        # ----------------------------------------------------

        if total_score > 0:

            dominance = (
                abs(net_score)
                / total_score
            )

        else:

            dominance = 0.0

        # ----------------------------------------------------
        # CONFLICT
        # ----------------------------------------------------

        conflict = (
            self._conflict_level(
                bullish_score,
                bearish_score,
            )
        )

        # ----------------------------------------------------
        # DIRECTION
        # ----------------------------------------------------

        if total_score <= 0:

            direction = (
                ConfluenceDirection.UNKNOWN
            )

            confidence = 0.0

        elif dominance < MIN_DOMINANCE:

            direction = (
                ConfluenceDirection.NEUTRAL
            )

            # For neutral markets we deliberately do NOT
            # report the dominant-side percentage as confidence.
            #
            # That would be misleading because the engine has
            # explicitly rejected directional commitment.

            confidence = 0.0

        elif bullish_score > bearish_score:

            direction = (
                ConfluenceDirection.BULLISH
            )

            confidence = (
                self._calculate_confidence(
                    dominant_score=bullish_score,
                    opposing_score=bearish_score,
                    total_score=total_score,
                )
            )

        elif bearish_score > bullish_score:

            direction = (
                ConfluenceDirection.BEARISH
            )

            confidence = (
                self._calculate_confidence(
                    dominant_score=bearish_score,
                    opposing_score=bullish_score,
                    total_score=total_score,
                )
            )

        else:

            direction = (
                ConfluenceDirection.NEUTRAL
            )

            confidence = 0.0

        # ----------------------------------------------------
        # SAFETY CAP
        # ----------------------------------------------------

        confidence = min(
            100.0,
            max(
                0.0,
                confidence,
            ),
        )

        # ----------------------------------------------------
        # LEGACY SCORE
        # ----------------------------------------------------

        score = max(
            bullish_score,
            bearish_score,
        )

        # ----------------------------------------------------
        # LIQUIDITY SCORE
        # ----------------------------------------------------

        liquidity_score = sum(
            item.score
            for item in evidence
            if item.name == "Liquidity"
        )

        # ----------------------------------------------------
        # REASONS
        # ----------------------------------------------------

        reasons = tuple(
            item.reason
            for item in evidence
        )

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        return ConfluenceResult(
            direction=direction,
            confidence=confidence,
            score=score,
            evidence=tuple(evidence),
            reasons=reasons,
            bullish_score=bullish_score,
            bearish_score=bearish_score,
            net_score=net_score,
            conflict=conflict,
            liquidity_score=liquidity_score,
        )