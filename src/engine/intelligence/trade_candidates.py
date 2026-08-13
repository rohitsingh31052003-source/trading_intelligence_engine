"""
Trade candidate generation engine (Sprint 11R).

``TradeCandidateEngine`` turns the existing intelligence produced by
Sprints 11O (candle patterns), 11P (market context) and 11Q (setup /
confluence) into a deterministic, descriptive ``TradeCandidate``.

It is the third step of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)  <- this layer
    4. TRADE VALIDATION             (future)
    5. SIGNAL / EXECUTION           (future)

The engine is deterministic, pure where practical, future-leakage safe
and independent from the historical evaluation runner. It reads ONLY
the already-computed ``SetupAssessment`` (Sprint 11Q) and
``MarketContext`` (Sprint 11P) plus the trigger candle's close price
(a scalar). Both ``SetupAssessment`` and ``MarketContext`` are
themselves derived from ``candles[:T+1]`` only; the engine inspects no
candles directly and therefore cannot introduce look-ahead bias.

Dependency direction (preserved):

    models
       ↑
    intelligence engines (existing + new)
       ↑
    pipeline / orchestration

This is intelligence, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via
full paths, e.g.
``from engine.intelligence.trade_candidates import TradeCandidateEngine``.

DESIGN PRINCIPLE — reuse, do not re-invent:

The candidate layer does NOT introduce a separate scoring system. It
reuses the Sprint 11Q ``SetupClassification`` and ``confluence_score``
verbatim, plus the structured ``EvidenceItem`` supporting / conflicting
views. The only logic added here is:

* a conservative promotion gate (POTENTIAL_SETUP -> CANDIDATE) that
  requires a directional bias, sufficient confluence and no
  disqualifying conflict / range block;
* objective entry / stop / target derivation from the structural
  levels already present in the Sprint 11P market context;
* deterministic risk / reward computation with honest ``None`` for
  unavailable or invalid geometry;
* a conservative setup-type classification that never pretends to
  know more than the evidence supports.

No trade signal is produced. A ``CANDIDATE`` is DESCRIPTIVE: a
structured candidate for further validation / evaluation. It is NOT a
prediction, guarantee, profitability claim, or trading recommendation.
"""

from __future__ import annotations

from datetime import datetime

from engine.config.trade_candidate_config import TradeCandidateConfig
from engine.models.market_context import (
    MarketContext,
    MarketTrendState,
    PriceLocation,
    RangeState,
)
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceItem,
    SetupAssessment,
    SetupClassification,
    SetupDirection,
)
from engine.models.trade_candidate import (
    CandidateDirection,
    CandidateStatus,
    SetupType,
    TradeCandidate,
)


# ============================================================
# DIRECTION MAPPING
# ============================================================

_SETUP_TO_CANDIDATE_DIRECTION = {
    SetupDirection.BULLISH: CandidateDirection.LONG,
    SetupDirection.BEARISH: CandidateDirection.SHORT,
    SetupDirection.NEUTRAL: CandidateDirection.NONE,
    SetupDirection.UNKNOWN: CandidateDirection.NONE,
}


class TradeCandidateEngine:
    """
    Generate a deterministic, descriptive ``TradeCandidate`` from the
    existing 11O-11Q intelligence.

    Public API:

        generate(
            assessment, market_context, index, timestamp, close_price
        ) -> TradeCandidate

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(
        self,
        config: TradeCandidateConfig | None = None,
    ) -> None:
        self.config = config or TradeCandidateConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def generate(
        self,
        assessment: SetupAssessment | None,
        market_context: MarketContext | None,
        index: int,
        timestamp: datetime | None = None,
        close_price: float | None = None,
    ) -> TradeCandidate:
        """
        Produce a trade candidate at ``index``.

        ``assessment`` is the Sprint 11Q setup assessment at the
        evaluation point (``None`` is treated as NO_SETUP).
        ``market_context`` is the Sprint 11P market context at the
        evaluation point (``None`` means no structural levels are
        available, so stop / target cannot be derived and the
        candidate is geometrically incomplete). ``close_price`` is the
        trigger candle close used as the objective entry reference
        (``None`` means no entry reference can be established).
        """

        if assessment is None:
            return self._no_candidate(
                index=index,
                timestamp=timestamp,
                reason="No setup assessment available; no candidate.",
            )

        direction = _SETUP_TO_CANDIDATE_DIRECTION.get(
            assessment.direction, CandidateDirection.NONE,
        )

        evidence = assessment.evidence
        supporting = tuple(evidence.supporting)
        conflicting = tuple(evidence.conflicting)

        # -----------------------------------------------------
        # STATUS DETERMINATION (reuses 11Q classification)
        # -----------------------------------------------------
        if assessment.classification == SetupClassification.NO_SETUP:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.NO_CANDIDATE,
                direction=direction,
                reason=(
                    "Setup classification is NO_SETUP; insufficient "
                    "evidence for a trade candidate."
                ),
            )

        if assessment.classification == SetupClassification.WATCH:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    "Setup classification is WATCH; confluence not "
                    "strong enough for a trade candidate."
                ),
            )

        # POTENTIAL_SETUP: apply the conservative promotion gate.
        return self._build_candidate_from_potential(
            assessment=assessment,
            market_context=market_context,
            index=index,
            timestamp=timestamp,
            close_price=close_price,
            direction=direction,
            supporting=supporting,
            conflicting=conflicting,
        )

    # ========================================================
    # CANDIDATE PROMOTION (POTENTIAL_SETUP -> CANDIDATE / WATCH)
    # ========================================================

    def _build_candidate_from_potential(
        self,
        assessment: SetupAssessment,
        market_context: MarketContext | None,
        index: int,
        timestamp: datetime | None,
        close_price: float | None,
        direction: CandidateDirection,
        supporting: tuple[EvidenceItem, ...],
        conflicting: tuple[EvidenceItem, ...],
    ) -> TradeCandidate:
        """
        Apply the conservative promotion gate to a POTENTIAL_SETUP.

        A POTENTIAL_SETUP is NOT automatically a candidate. It must:

        * carry a directional bias (LONG / SHORT);
        * have confluence >= ``min_confluence_for_candidate``;
        * have no disqualifying conflicting evidence;
        * not be range-blocked (unless ``allow_range_setups``).
        """

        # No directional bias -> cannot be a directional candidate.
        if direction == CandidateDirection.NONE:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    "Potential setup but no directional bias; capped "
                    "at WATCH."
                ),
            )

        # Insufficient confluence for a candidate.
        if assessment.confluence_score < self.config.min_confluence_for_candidate:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    f"Confluence {assessment.confluence_score} below "
                    f"candidate threshold "
                    f"{self.config.min_confluence_for_candidate}; "
                    "capped at WATCH."
                ),
            )

        # Disqualifying conflicting evidence.
        if len(conflicting) > 0:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    "Conflicting evidence present; potential setup "
                    "downgraded to WATCH (no automatic candidate)."
                ),
            )

        # Range block (unless range setups are explicitly allowed).
        in_range = self._is_in_range(market_context)
        if in_range and not self.config.allow_range_setups:
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    "Market is in a consolidation range; range setups "
                    "disallowed by configuration; downgraded to WATCH."
                ),
            )

        # Passed the promotion gate -> CANDIDATE.
        return self._build_candidate(
            assessment=assessment,
            market_context=market_context,
            index=index,
            timestamp=timestamp,
            close_price=close_price,
            direction=direction,
            supporting=supporting,
            conflicting=conflicting,
        )

    # ========================================================
    # CANDIDATE CONSTRUCTION (with geometry)
    # ========================================================

    def _build_candidate(
        self,
        assessment: SetupAssessment,
        market_context: MarketContext | None,
        index: int,
        timestamp: datetime | None,
        close_price: float | None,
        direction: CandidateDirection,
        supporting: tuple[EvidenceItem, ...],
        conflicting: tuple[EvidenceItem, ...],
    ) -> TradeCandidate:
        """
        Build a CANDIDATE with entry / stop / target geometry derived
        from the structural levels available at the evaluation point.
        """

        entry = close_price
        stop = self._stop_reference(direction, market_context, entry)
        target = self._target_reference(direction, market_context, entry)

        risk, reward, ratio = self._risk_reward(
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
        )

        setup_type = self._setup_type(
            direction=direction,
            market_context=market_context,
            assessment=assessment,
        )

        # Optional R:R gate: a complete-but-poor-ratio candidate is
        # downgraded to WATCH. Incomplete geometry is NOT rejected by
        # this gate (reported honestly instead).
        rr_threshold = self.config.min_risk_reward_ratio
        if (
            rr_threshold is not None
            and ratio is not None
            and ratio < rr_threshold
        ):
            return self._build_non_candidate(
                assessment=assessment,
                market_context=market_context,
                index=index,
                timestamp=timestamp,
                close_price=close_price,
                status=CandidateStatus.WATCH,
                direction=direction,
                reason=(
                    f"Risk/reward {ratio:.2f} below configured minimum "
                    f"{rr_threshold:.2f}; downgraded to WATCH."
                ),
            )

        reason = self._candidate_reason(
            direction=direction,
            setup_type=setup_type,
            assessment=assessment,
            entry=entry,
            stop=stop,
            target=target,
            risk=risk,
            reward=reward,
            ratio=ratio,
        )

        return TradeCandidate(
            timestamp=timestamp,
            evaluation_index=index,
            direction=direction,
            status=CandidateStatus.CANDIDATE,
            setup_type=setup_type,
            setup_classification=assessment.classification,
            entry_reference=entry,
            stop_reference=stop,
            target_reference=target,
            risk_distance=risk,
            reward_distance=reward,
            risk_reward_ratio=ratio,
            confluence_score=assessment.confluence_score,
            supporting_evidence=supporting,
            conflicting_evidence=conflicting,
            candle_evidence=assessment.candle_evidence,
            market_trend=assessment.trend_evidence,
            market_structure=assessment.structure_evidence,
            location=assessment.location_evidence,
            range_context=assessment.regime_evidence,
            reason=reason,
        )

    # ========================================================
    # NON-CANDIDATE CONSTRUCTION (NO_CANDIDATE / WATCH)
    # ========================================================

    def _build_non_candidate(
        self,
        assessment: SetupAssessment,
        market_context: MarketContext | None,
        index: int,
        timestamp: datetime | None,
        close_price: float | None,
        status: CandidateStatus,
        direction: CandidateDirection,
        reason: str,
    ) -> TradeCandidate:
        """
        Build a NO_CANDIDATE or WATCH point.

        Non-candidate points carry no trade geometry (entry / stop /
        target / risk / reward are all ``None``): a watch is not a
        trade candidate and the geometry would be misleading. The
        structured evidence is preserved for inspection.
        """

        return TradeCandidate(
            timestamp=timestamp,
            evaluation_index=index,
            direction=direction,
            status=status,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=assessment.classification,
            entry_reference=None,
            stop_reference=None,
            target_reference=None,
            risk_distance=None,
            reward_distance=None,
            risk_reward_ratio=None,
            confluence_score=assessment.confluence_score,
            supporting_evidence=tuple(assessment.evidence.supporting),
            conflicting_evidence=tuple(assessment.evidence.conflicting),
            candle_evidence=assessment.candle_evidence,
            market_trend=assessment.trend_evidence,
            market_structure=assessment.structure_evidence,
            location=assessment.location_evidence,
            range_context=assessment.regime_evidence,
            reason=reason,
        )

    def _no_candidate(
        self,
        index: int,
        timestamp: datetime | None,
        reason: str,
    ) -> TradeCandidate:
        """Build a bare NO_CANDIDATE (no assessment available)."""

        return TradeCandidate(
            timestamp=timestamp,
            evaluation_index=index,
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.NO_SETUP,
            reason=reason,
        )

    # ========================================================
    # ENTRY / STOP / TARGET
    # ========================================================

    def _stop_reference(
        self,
        direction: CandidateDirection,
        market_context: MarketContext | None,
        entry: float | None,
    ) -> float | None:
        """
        Structural stop derived from confirmed market structure.

        LONG: below the nearest support (recent confirmed swing low).
        SHORT: above the nearest resistance (recent confirmed swing
        high). The level must lie on the correct side of the entry;
        otherwise no valid structural stop is available (``None``).
        """

        if market_context is None or entry is None:
            return None

        sr = market_context.support_resistance

        if direction == CandidateDirection.LONG:
            support = sr.support
            if support is None:
                return None
            return support if support < entry else None

        if direction == CandidateDirection.SHORT:
            resistance = sr.resistance
            if resistance is None:
                return None
            return resistance if resistance > entry else None

        return None

    def _target_reference(
        self,
        direction: CandidateDirection,
        market_context: MarketContext | None,
        entry: float | None,
    ) -> float | None:
        """
        Deterministic target from the opposing structural level.

        LONG: the next resistance above the entry. SHORT: the next
        support below the entry. The level must lie on the correct
        side of the entry; otherwise the target is explicitly
        unavailable (``None``) rather than invented.
        """

        if market_context is None or entry is None:
            return None

        sr = market_context.support_resistance

        if direction == CandidateDirection.LONG:
            resistance = sr.resistance
            if resistance is None:
                return None
            return resistance if resistance > entry else None

        if direction == CandidateDirection.SHORT:
            support = sr.support
            if support is None:
                return None
            return support if support < entry else None

        return None

    # ========================================================
    # RISK / REWARD
    # ========================================================

    def _risk_reward(
        self,
        direction: CandidateDirection,
        entry: float | None,
        stop: float | None,
        target: float | None,
    ) -> tuple[float | None, float | None, float | None]:
        """
        Compute (risk, reward, ratio) with honest ``None`` for
        unavailable / invalid geometry.

        LONG: risk = entry - stop; reward = target - entry.
        SHORT: risk = stop - entry; reward = entry - target.
        Non-positive risk or reward is rejected (``None``) rather
        than manufactured.
        """

        if entry is None or stop is None or target is None:
            return None, None, None

        if direction == CandidateDirection.LONG:
            risk = entry - stop
            reward = target - entry
        elif direction == CandidateDirection.SHORT:
            risk = stop - entry
            reward = entry - target
        else:
            return None, None, None

        if risk <= 0 or reward <= 0:
            return None, None, None

        return risk, reward, reward / risk

    # ========================================================
    # SETUP TYPE
    # ========================================================

    def _setup_type(
        self,
        direction: CandidateDirection,
        market_context: MarketContext | None,
        assessment: SetupAssessment,
    ) -> SetupType:
        """
        Conservative setup-type classification.

        Only types justifiable from the existing 11O-11Q evidence are
        returned. When the evidence cannot reliably distinguish a
        specific type, the generic ``SETUP_CANDIDATE`` is used.
        """

        if market_context is None:
            return SetupType.SETUP_CANDIDATE

        location = market_context.support_resistance.location
        trend_aligned = self._trend_aligned(direction, assessment)

        if direction == CandidateDirection.LONG:
            if location == PriceLocation.ABOVE_RESISTANCE:
                return SetupType.BREAKOUT
            if location == PriceLocation.NEAR_SUPPORT:
                if trend_aligned:
                    return SetupType.TREND_CONTINUATION
                return SetupType.STRUCTURE_CONTINUATION
            return SetupType.SETUP_CANDIDATE

        if direction == CandidateDirection.SHORT:
            if location == PriceLocation.BELOW_SUPPORT:
                return SetupType.BREAKOUT
            if location == PriceLocation.NEAR_RESISTANCE:
                if trend_aligned:
                    return SetupType.TREND_CONTINUATION
                return SetupType.STRUCTURE_CONTINUATION
            return SetupType.SETUP_CANDIDATE

        return SetupType.SETUP_CANDIDATE

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _is_in_range(market_context: MarketContext | None) -> bool:
        """Whether the market is currently classified IN_RANGE."""

        if market_context is None:
            return False
        return market_context.range.state == RangeState.IN_RANGE

    @staticmethod
    def _trend_aligned(
        direction: CandidateDirection,
        assessment: SetupAssessment,
    ) -> bool:
        """
        Whether the descriptive market trend is aligned with the
        candidate direction. Uses the Sprint 11Q trend evidence item
        (already finalised relative to the candidate direction): the
        trend is aligned when its evidence item is ALIGNED.
        """

        trend_item = assessment.evidence.trend
        return trend_item.alignment == EvidenceAlignment.ALIGNED

    @staticmethod
    def _candidate_reason(
        direction: CandidateDirection,
        setup_type: SetupType,
        assessment: SetupAssessment,
        entry: float | None,
        stop: float | None,
        target: float | None,
        risk: float | None,
        reward: float | None,
        ratio: float | None,
    ) -> str:
        """Human-readable summary of a CANDIDATE."""

        parts = [
            f"{direction.name} candidate ({setup_type.name}) from "
            f"{assessment.confluence_score} aligned evidence source(s) "
            "and no disqualifying conflict.",
        ]

        if entry is None:
            parts.append("Entry reference unavailable.")
        else:
            parts.append(f"Entry reference {entry:.4f} (trigger close).")

        if stop is None:
            parts.append("Structural stop unavailable.")
        else:
            parts.append(f"Structural stop {stop:.4f}.")

        if target is None:
            parts.append("Target reference unavailable.")
        else:
            parts.append(f"Target reference {target:.4f}.")

        if risk is not None and reward is not None and ratio is not None:
            parts.append(
                f"Risk {risk:.4f} / reward {reward:.4f} "
                f"(R:R {ratio:.2f})."
            )
        else:
            parts.append(
                "Risk/reward not computable from available structural "
                "references (geometrically incomplete)."
            )

        parts.append(
            "Descriptive candidate; not a prediction or guarantee of "
            "profitability."
        )
        return " ".join(parts)


__all__ = ["TradeCandidateEngine"]
