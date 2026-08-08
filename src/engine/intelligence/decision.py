from __future__ import annotations

from engine.models.confluence import (
    ConfluenceDirection,
)
from engine.models.decision import (
    DecisionContext,
    DecisionDirection,
    DecisionEvidence,
    DecisionStatus,
    SetupQuality,
)


class DecisionEngine:
    """
    Convert ConfluenceResult into a structured
    decision context.

    The engine does NOT generate entries, exits,
    stop losses, targets, or orders.

    It answers:

        "Is the current evidence sufficiently
         aligned for downstream decision logic?"
    """

    # ---------------------------------------------------------
    # CONFIGURATION
    # ---------------------------------------------------------

    MIN_CONFIDENCE = 55.0
    STRONG_CONFIDENCE = 70.0
    EXCELLENT_CONFIDENCE = 85.0

    MIN_EVIDENCE_SCORE = 20.0

    # Maximum conflict allowed for a READY setup.
    MAX_READY_CONFLICT = "MEDIUM"

    # ---------------------------------------------------------
    # ANALYZE
    # ---------------------------------------------------------

    def analyze(
        self,
        confluence,
    ) -> DecisionContext:
        """
        Convert a ConfluenceResult into a DecisionContext.
        """

        if confluence is None:
            return self._not_ready(
                "No confluence result available."
            )

        direction = self._normalize_direction(
            getattr(
                confluence,
                "direction",
                None,
            )
        )

        confidence = self._safe_float(
            getattr(
                confluence,
                "confidence",
                0.0,
            )
        )

        bullish_score = self._safe_float(
            getattr(
                confluence,
                "bullish_score",
                0.0,
            )
        )

        bearish_score = self._safe_float(
            getattr(
                confluence,
                "bearish_score",
                0.0,
            )
        )

        net_score = self._safe_float(
            getattr(
                confluence,
                "net_score",
                bullish_score - bearish_score,
            )
        )

        conflict = str(
            getattr(
                confluence,
                "conflict",
                "NONE",
            )
        ).upper()

        evidence = self._convert_evidence(
            confluence
        )

        evidence_quality = (
            self._calculate_evidence_quality(
                confluence,
                evidence,
            )
        )

        setup_quality = (
            self._classify_setup_quality(
                direction=direction,
                confidence=confidence,
                conflict=conflict,
                evidence_quality=evidence_quality,
                bullish_score=bullish_score,
                bearish_score=bearish_score,
            )
        )

        status = self._determine_status(
            direction=direction,
            confidence=confidence,
            conflict=conflict,
            setup_quality=setup_quality,
            evidence_quality=evidence_quality,
        )

        trade_eligible = (
            status == DecisionStatus.READY
        )

        reasons = self._build_reasons(
            direction=direction,
            confidence=confidence,
            conflict=conflict,
            setup_quality=setup_quality,
            status=status,
            evidence_quality=evidence_quality,
        )

        return DecisionContext(
            direction=direction,
            confidence=confidence,
            setup_quality=setup_quality,
            status=status,
            trade_eligible=trade_eligible,
            bullish_score=bullish_score,
            bearish_score=bearish_score,
            net_score=net_score,
            conflict=conflict,
            evidence_quality=evidence_quality,
            evidence=tuple(evidence),
            reasons=tuple(reasons),
        )

    # ---------------------------------------------------------
    # DIRECTION
    # ---------------------------------------------------------

    def _normalize_direction(
        self,
        value,
    ) -> DecisionDirection:

        if value is None:
            return DecisionDirection.UNKNOWN

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
            return DecisionDirection.BULLISH

        if "BEAR" in text:
            return DecisionDirection.BEARISH

        if "NEUTRAL" in text:
            return DecisionDirection.NEUTRAL

        if "UNKNOWN" in text:
            return DecisionDirection.UNKNOWN

        return DecisionDirection.UNKNOWN

    # ---------------------------------------------------------
    # SAFE FLOAT
    # ---------------------------------------------------------

    def _safe_float(
        self,
        value,
    ) -> float:

        try:
            result = float(value)
        except (
            TypeError,
            ValueError,
        ):
            return 0.0

        if result < 0:
            return 0.0

        return result

    # ---------------------------------------------------------
    # EVIDENCE CONVERSION
    # ---------------------------------------------------------

    def _convert_evidence(
        self,
        confluence,
    ):

        converted = []

        source_evidence = getattr(
            confluence,
            "evidence",
            (),
        )

        for item in source_evidence:

            direction = (
                self._normalize_direction(
                    getattr(
                        item,
                        "direction",
                        None,
                    )
                )
            )

            score = self._safe_float(
                getattr(
                    item,
                    "score",
                    0.0,
                )
            )

            name = str(
                getattr(
                    item,
                    "name",
                    "Unknown",
                )
            )

            reason = str(
                getattr(
                    item,
                    "reason",
                    "",
                )
            )

            converted.append(
                DecisionEvidence(
                    name=name,
                    direction=direction,
                    score=score,
                    reason=reason,
                )
            )

        return converted

    # ---------------------------------------------------------
    # EVIDENCE QUALITY
    # ---------------------------------------------------------

    def _calculate_evidence_quality(
        self,
        confluence,
        evidence,
    ) -> float:
        """
        Estimate evidence quality from:

        - amount of directional evidence
        - directional score
        - confidence
        - conflict

        This is NOT prediction probability.
        """

        confidence = self._safe_float(
            getattr(
                confluence,
                "confidence",
                0.0,
            )
        )

        bullish_score = self._safe_float(
            getattr(
                confluence,
                "bullish_score",
                0.0,
            )
        )

        bearish_score = self._safe_float(
            getattr(
                confluence,
                "bearish_score",
                0.0,
            )
        )

        total_score = (
            bullish_score
            + bearish_score
        )

        if total_score <= 0:
            return 0.0

        directional_strength = (
            max(
                bullish_score,
                bearish_score,
            )
            / total_score
            * 100.0
        )

        evidence_count = len(evidence)

        if evidence_count <= 0:
            evidence_factor = 0.0

        elif evidence_count == 1:
            evidence_factor = 50.0

        elif evidence_count == 2:
            evidence_factor = 70.0

        elif evidence_count == 3:
            evidence_factor = 85.0

        else:
            evidence_factor = 100.0

        conflict = str(
            getattr(
                confluence,
                "conflict",
                "NONE",
            )
        ).upper()

        conflict_factor = {
            "NONE": 100.0,
            "LOW": 90.0,
            "MEDIUM": 65.0,
            "HIGH": 30.0,
        }.get(
            conflict,
            30.0,
        )

        quality = (
            confidence * 0.40
            + directional_strength * 0.30
            + evidence_factor * 0.15
            + conflict_factor * 0.15
        )

        return round(
            min(
                100.0,
                max(
                    0.0,
                    quality,
                ),
            ),
            2,
        )

    # ---------------------------------------------------------
    # SETUP QUALITY
    # ---------------------------------------------------------

    def _classify_setup_quality(
        self,
        *,
        direction,
        confidence,
        conflict,
        evidence_quality,
        bullish_score,
        bearish_score,
    ) -> SetupQuality:

        if direction in (
            DecisionDirection.UNKNOWN,
            DecisionDirection.NEUTRAL,
        ):
            return SetupQuality.INVALID

        if conflict == "HIGH":
            return SetupQuality.INVALID

        if confidence < self.MIN_CONFIDENCE:
            return SetupQuality.WEAK

        if (
            confidence >= self.EXCELLENT_CONFIDENCE
            and conflict == "NONE"
            and evidence_quality >= 85.0
        ):
            return SetupQuality.EXCELLENT

        if (
            confidence >= self.STRONG_CONFIDENCE
            and conflict in (
                "NONE",
                "LOW",
            )
            and evidence_quality >= 70.0
        ):
            return SetupQuality.STRONG

        if (
            confidence >= self.MIN_CONFIDENCE
            and conflict in (
                "NONE",
                "LOW",
                "MEDIUM",
            )
        ):
            return SetupQuality.MODERATE

        return SetupQuality.WEAK

    # ---------------------------------------------------------
    # STATUS
    # ---------------------------------------------------------

    def _determine_status(
        self,
        *,
        direction,
        confidence,
        conflict,
        setup_quality,
        evidence_quality,
    ) -> DecisionStatus:

        if direction in (
            DecisionDirection.UNKNOWN,
            DecisionDirection.NEUTRAL,
        ):
            return DecisionStatus.NOT_READY

        if conflict == "HIGH":
            return DecisionStatus.CONFLICTED

        if confidence < self.MIN_CONFIDENCE:
            return DecisionStatus.NOT_READY

        if evidence_quality < self.MIN_EVIDENCE_SCORE:
            return DecisionStatus.NOT_READY

        if setup_quality == SetupQuality.INVALID:
            return DecisionStatus.NOT_READY

        if conflict == "MEDIUM":
            return DecisionStatus.NOT_READY

        return DecisionStatus.READY

    # ---------------------------------------------------------
    # REASONS
    # ---------------------------------------------------------

    def _build_reasons(
        self,
        *,
        direction,
        confidence,
        conflict,
        setup_quality,
        status,
        evidence_quality,
    ):

        reasons = []

        if direction == DecisionDirection.UNKNOWN:

            reasons.append(
                "Directional confluence is unknown."
            )

        elif direction == DecisionDirection.NEUTRAL:

            reasons.append(
                "Confluence is neutral."
            )

        else:

            reasons.append(
                f"Confluence direction is "
                f"{direction.value.lower()}."
            )

        if conflict == "HIGH":

            reasons.append(
                "Bullish and bearish evidence "
                "are strongly conflicting."
            )

        elif conflict == "MEDIUM":

            reasons.append(
                "Bullish and bearish evidence "
                "show meaningful conflict."
            )

        elif conflict == "LOW":

            reasons.append(
                "Directional evidence has low conflict."
            )

        else:

            reasons.append(
                "No material directional conflict detected."
            )

        reasons.append(
            f"Confluence confidence is "
            f"{confidence:.1f}."
        )

        reasons.append(
            f"Evidence quality is "
            f"{evidence_quality:.1f}."
        )

        reasons.append(
            f"Setup quality is "
            f"{setup_quality.value.lower()}."
        )

        if status == DecisionStatus.READY:

            reasons.append(
                "Setup is eligible for downstream "
                "signal evaluation."
            )

        elif status == DecisionStatus.CONFLICTED:

            reasons.append(
                "Setup is blocked because directional "
                "evidence is conflicting."
            )

        else:

            reasons.append(
                "Setup is not ready for downstream "
                "signal evaluation."
            )

        return reasons

    # ---------------------------------------------------------
    # NOT READY
    # ---------------------------------------------------------

    def _not_ready(
        self,
        reason,
    ) -> DecisionContext:

        return DecisionContext(
            direction=DecisionDirection.UNKNOWN,
            confidence=0.0,
            setup_quality=SetupQuality.INVALID,
            status=DecisionStatus.NOT_READY,
            trade_eligible=False,
            reasons=(
                reason,
            ),
        )