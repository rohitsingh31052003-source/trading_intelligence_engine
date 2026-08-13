"""
Market setup / confluence engine (Sprint 11Q).

``SetupConfluenceEngine`` combines the existing candle-pattern evidence
(Sprint 11O) and market-context evidence (Sprint 11P) into a single
descriptive ``SetupAssessment``. It is deterministic, pure where
practical, and future-leakage safe: it reads ONLY the already-computed
``CandlePattern`` and ``MarketContext`` objects, both of which are
themselves derived from ``candles[:T+1]`` only. The engine inspects no
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
``from engine.intelligence.setup_confluence import SetupConfluenceEngine``.

DESIGN PRINCIPLE — interpretable evidence, not numeric aggregation:

The engine deliberately does NOT collapse the evidence into a single
opaque score. Instead it builds one ``EvidenceItem`` per independent
source (trend / structure / candle / location / range), each carrying
its own direction and alignment relative to the candidate setup
direction. Conflicts remain visible: a bullish trend with a bearish
reversal candle is recorded as conflicting, not silently merged into
"bullish".

EVIDENCE RULES (explicit, documented, deterministic):

Trend evidence
    From ``MarketContext.trend.state``:
        BULLISH  -> BULLISH
        BEARISH  -> BEARISH
        NEUTRAL  -> NEUTRAL
        RANGE    -> NEUTRAL (a range is not a directional trend)
        UNKNOWN  -> ABSENT

Structure evidence
    From ``MarketContext.recent_structure`` (StructurePoint sequence).
    When fewer than ``min_structure_for_evidence`` structures exist the
    evidence is ABSENT. Otherwise the recent sequence is classified:
        predominantly HH / HL -> BULLISH
        predominantly LH / LL -> BEARISH
        mixed / unclear        -> NEUTRAL

Candle evidence
    From the ``CandlePattern`` objects attributed to index ``T``. The
    strongest directional pattern determines the direction:
        HAMMER / BULLISH_ENGULFING -> BULLISH
        SHOOTING_STAR / BEARISH_ENGULFING -> BEARISH
        DOJI / INSIDE_BAR -> NEUTRAL
    When directional patterns of BOTH directions are present the candle
    evidence is marked CONFLICTING. When no pattern is present the
    evidence is ABSENT. When ``neutral_candle_contributes`` is False
    (default), neutral patterns count as NEUTRAL and do not add to the
    confluence score.

Location evidence
    From ``MarketContext.support_resistance.location``, evaluated
    RELATIVE to the candidate setup direction (location is only
    meaningful in context of a bias):
        BULLISH candidate + NEAR_SUPPORT / ABOVE_RESISTANCE -> ALIGNED
        BULLISH candidate + NEAR_RESISTANCE / BELOW_SUPPORT -> CONFLICTING
        BEARISH candidate + NEAR_RESISTANCE / BELOW_SUPPORT -> ALIGNED
        BEARISH candidate + NEAR_SUPPORT / ABOVE_RESISTANCE -> CONFLICTING
        INSIDE_RANGE -> NEUTRAL
        UNKNOWN (no levels) -> ABSENT
    When no candidate direction exists the location is NEUTRAL.

Range / regime evidence
    From ``MarketContext.range.state``:
        IN_RANGE    -> NEUTRAL (and, by default, caps classification)
        NOT_IN_RANGE -> ALIGNED with a directional candidate
        UNKNOWN     -> ABSENT

CLASSIFICATION (see ``SetupClassification`` for full semantics):

    1. Determine the candidate direction from the directional evidence
       (trend / structure / candle). Ties or all-neutral -> UNKNOWN.
    2. Evaluate location relative to the candidate direction.
    3. Count ALIGNED independent sources (the confluence score).
    4. Record CONFLICTING sources.
    5. If no candidate direction -> NO_SETUP.
    6. If ``range_caps_classification`` and IN_RANGE -> cap at WATCH.
    7. If a conflict exists and ``conflicting_blocks_potential_setup``
       -> cap at WATCH.
    8. confluence_score >= min_supporting_for_potential_setup ->
       POTENTIAL_SETUP; >= min_supporting_for_watch -> WATCH;
       otherwise NO_SETUP.

No trade signal is produced. ``POTENTIAL_SETUP`` is DESCRIPTIVE: it
means the technical evidence currently forms a coherent candidate
worth further evaluation. It is NOT a prediction, guarantee, or
trading recommendation.
"""

from __future__ import annotations

from typing import Iterable

from engine.config.setup_confluence_config import SetupConfluenceConfig
from engine.models.candle_pattern import (
    CandleDirection,
    CandlePattern,
    CandlePatternType,
)
from engine.models.market_context import (
    MarketContext,
    MarketTrendState,
    PriceLocation,
    RangeState,
)
from engine.models.market_structure import StructureType
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceItem,
    EvidenceSource,
    SetupAssessment,
    SetupClassification,
    SetupDirection,
    SetupEvidence,
)


# Pattern -> intrinsic candle direction. Used to classify candle
# evidence. Neutral patterns carry no directional information.
_DIRECTIONAL_PATTERNS: dict[CandlePatternType, SetupDirection] = {
    CandlePatternType.HAMMER: SetupDirection.BULLISH,
    CandlePatternType.BULLISH_ENGULFING: SetupDirection.BULLISH,
    CandlePatternType.SHOOTING_STAR: SetupDirection.BEARISH,
    CandlePatternType.BEARISH_ENGULFING: SetupDirection.BEARISH,
}

_NEUTRAL_PATTERNS: frozenset[CandlePatternType] = frozenset(
    {
        CandlePatternType.DOJI,
        CandlePatternType.INSIDE_BAR,
    },
)

# Directional structure labels (others are neutral/non-directional).
_BULLISH_STRUCTURES: frozenset[StructureType] = frozenset(
    {
        StructureType.HIGHER_HIGH,
        StructureType.HIGHER_LOW,
    },
)
_BEARISH_STRUCTURES: frozenset[StructureType] = frozenset(
    {
        StructureType.LOWER_HIGH,
        StructureType.LOWER_LOW,
    },
)


class SetupConfluenceEngine:
    """
    Combine candle-pattern and market-context evidence into a
    descriptive setup assessment.

    Public API:

        assess(patterns, market_context, index, timestamp) -> SetupAssessment

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(
        self,
        config: SetupConfluenceConfig | None = None,
    ) -> None:
        self.config = config or SetupConfluenceConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def assess(
        self,
        patterns: Iterable[CandlePattern],
        market_context: MarketContext | None,
        index: int,
        timestamp=None,
    ) -> SetupAssessment:
        """
        Produce a setup assessment at ``index``.

        ``patterns`` are the candle patterns attributed to ``index``
        (already filtered to the triggering candle). ``market_context``
        is the Sprint 11P context at ``index``; ``None`` is treated as
        fully unobserved (all evidence ABSENT) and yields ``NO_SETUP``.
        ``timestamp`` is the triggering candle timestamp, when known.
        """

        pattern_list = list(patterns)

        if market_context is None:
            evidence = self._empty_evidence()
            return self._build_assessment(
                index=index,
                timestamp=timestamp,
                evidence=evidence,
                candle_label="none",
                structure_label="none",
                trend_label="UNKNOWN",
                location_label="UNKNOWN",
                regime_label="UNKNOWN",
            )

        # Build each evidence item independently.
        trend_item = self._trend_evidence(market_context)
        structure_item = self._structure_evidence(market_context)
        candle_item, candle_label = self._candle_evidence(pattern_list)
        range_item, regime_label = self._range_evidence(market_context)

        # Candidate direction is derived from the directional sources
        # only (trend / structure / candle). Location and range are
        # evaluated relative to (or as modifiers of) this candidate.
        candidate = self._candidate_direction(
            trend_item, structure_item, candle_item,
        )

        location_item, location_label = self._location_evidence(
            market_context, candidate,
        )

        evidence = self._assemble_evidence(
            trend=trend_item,
            structure=structure_item,
            candle=candle_item,
            location=location_item,
            range_item=range_item,
        )

        structure_label = self._structure_label(market_context)
        trend_label = market_context.trend.state.name

        return self._build_assessment(
            index=index,
            timestamp=timestamp,
            evidence=evidence,
            candle_label=candle_label,
            structure_label=structure_label,
            trend_label=trend_label,
            location_label=location_label,
            regime_label=regime_label,
        )

    # ========================================================
    # EVIDENCE BUILDERS
    # ========================================================

    def _trend_evidence(
        self,
        ctx: MarketContext,
    ) -> EvidenceItem:
        """Classify trend evidence from the descriptive trend state."""

        state = ctx.trend.state

        if state == MarketTrendState.BULLISH:
            return EvidenceItem(
                source=EvidenceSource.TREND,
                direction=SetupDirection.BULLISH,
                alignment=EvidenceAlignment.ABSENT,  # set later
                label="BULLISH",
                reason="Descriptive market trend is bullish.",
            )
        if state == MarketTrendState.BEARISH:
            return EvidenceItem(
                source=EvidenceSource.TREND,
                direction=SetupDirection.BEARISH,
                alignment=EvidenceAlignment.ABSENT,
                label="BEARISH",
                reason="Descriptive market trend is bearish.",
            )
        if state == MarketTrendState.NEUTRAL:
            return EvidenceItem(
                source=EvidenceSource.TREND,
                direction=SetupDirection.NEUTRAL,
                alignment=EvidenceAlignment.NEUTRAL,
                label="NEUTRAL",
                reason="Market trend is neutral.",
            )
        if state == MarketTrendState.RANGE:
            return EvidenceItem(
                source=EvidenceSource.TREND,
                direction=SetupDirection.NEUTRAL,
                alignment=EvidenceAlignment.NEUTRAL,
                label="RANGE",
                reason="Market trend is range-bound; not directional.",
            )
        # UNKNOWN
        return EvidenceItem(
            source=EvidenceSource.TREND,
            direction=SetupDirection.UNKNOWN,
            alignment=EvidenceAlignment.ABSENT,
            label="UNKNOWN",
            reason="No usable trend state available.",
        )

    def _structure_evidence(
        self,
        ctx: MarketContext,
    ) -> EvidenceItem:
        """
        Classify structure evidence from the recent confirmed
        structure sequence (HH / HL / LH / LL).
        """

        recent = ctx.recent_structure
        structures = [s.structure for s in recent]

        if len(structures) < self.config.min_structure_for_evidence:
            return EvidenceItem(
                source=EvidenceSource.STRUCTURE,
                direction=SetupDirection.UNKNOWN,
                alignment=EvidenceAlignment.ABSENT,
                label="none",
                reason=(
                    "Insufficient confirmed structure for directional "
                    "evidence."
                ),
            )

        bullish = sum(
            1 for s in structures if s in _BULLISH_STRUCTURES
        )
        bearish = sum(
            1 for s in structures if s in _BEARISH_STRUCTURES
        )

        if bullish > bearish and bearish == 0:
            return EvidenceItem(
                source=EvidenceSource.STRUCTURE,
                direction=SetupDirection.BULLISH,
                alignment=EvidenceAlignment.ABSENT,
                label="BULLISH",
                reason="Recent structure is higher highs / higher lows.",
            )
        if bearish > bullish and bullish == 0:
            return EvidenceItem(
                source=EvidenceSource.STRUCTURE,
                direction=SetupDirection.BEARISH,
                alignment=EvidenceAlignment.ABSENT,
                label="BEARISH",
                reason="Recent structure is lower highs / lower lows.",
            )
        if bullish > bearish:
            return EvidenceItem(
                source=EvidenceSource.STRUCTURE,
                direction=SetupDirection.BULLISH,
                alignment=EvidenceAlignment.ABSENT,
                label="MOSTLY_BULLISH",
                reason=(
                    "Recent structure is mostly higher highs / "
                    "higher lows with some bearish structure."
                ),
            )
        if bearish > bullish:
            return EvidenceItem(
                source=EvidenceSource.STRUCTURE,
                direction=SetupDirection.BEARISH,
                alignment=EvidenceAlignment.ABSENT,
                label="MOSTLY_BEARISH",
                reason=(
                    "Recent structure is mostly lower highs / "
                    "lower lows with some bullish structure."
                ),
            )

        return EvidenceItem(
            source=EvidenceSource.STRUCTURE,
            direction=SetupDirection.NEUTRAL,
            alignment=EvidenceAlignment.NEUTRAL,
            label="MIXED",
            reason="Recent structure is mixed / non-directional.",
        )

    def _candle_evidence(
        self,
        patterns: list[CandlePattern],
    ) -> tuple[EvidenceItem, str]:
        """
        Classify candle evidence from the patterns attributed to T.

        Returns the evidence item plus a short label summarising the
        observed pattern(s).
        """

        if not patterns:
            return (
                EvidenceItem(
                    source=EvidenceSource.CANDLE,
                    direction=SetupDirection.UNKNOWN,
                    alignment=EvidenceAlignment.ABSENT,
                    label="none",
                    reason="No candle pattern attributed to this point.",
                ),
                "none",
            )

        bullish = [
            p for p in patterns
            if _DIRECTIONAL_PATTERNS.get(p.pattern_type)
            == SetupDirection.BULLISH
        ]
        bearish = [
            p for p in patterns
            if _DIRECTIONAL_PATTERNS.get(p.pattern_type)
            == SetupDirection.BEARISH
        ]
        neutral = [
            p for p in patterns
            if p.pattern_type in _NEUTRAL_PATTERNS
        ]

        labels = sorted(p.pattern_type.name for p in patterns)
        label = " + ".join(labels)

        has_bull = bool(bullish)
        has_bear = bool(bearish)

        if has_bull and has_bear:
            # Conflicting directional candle patterns.
            return (
                EvidenceItem(
                    source=EvidenceSource.CANDLE,
                    direction=SetupDirection.UNKNOWN,
                    alignment=EvidenceAlignment.CONFLICTING,
                    label=label,
                    reason=(
                        "Both bullish and bearish directional candle "
                        "patterns are present; candle evidence "
                        "conflicts."
                    ),
                ),
                label,
            )

        if has_bull:
            return (
                EvidenceItem(
                    source=EvidenceSource.CANDLE,
                    direction=SetupDirection.BULLISH,
                    alignment=EvidenceAlignment.ABSENT,
                    label=label,
                    reason="Bullish directional candle pattern present.",
                ),
                label,
            )

        if has_bear:
            return (
                EvidenceItem(
                    source=EvidenceSource.CANDLE,
                    direction=SetupDirection.BEARISH,
                    alignment=EvidenceAlignment.ABSENT,
                    label=label,
                    reason="Bearish directional candle pattern present.",
                ),
                label,
            )

        # Only neutral patterns.
        contributes = self.config.neutral_candle_contributes
        return (
            EvidenceItem(
                source=EvidenceSource.CANDLE,
                direction=SetupDirection.NEUTRAL,
                alignment=(
                    EvidenceAlignment.ALIGNED
                    if contributes
                    else EvidenceAlignment.NEUTRAL
                ),
                label=label,
                reason=(
                    "Only neutral candle pattern(s) present; "
                    "counted as supporting evidence (config)."
                    if contributes
                    else "Only neutral candle pattern(s) present; no "
                    "directional candle confirmation."
                ),
            ),
            label,
        )

    def _location_evidence(
        self,
        ctx: MarketContext,
        candidate: SetupDirection,
    ) -> tuple[EvidenceItem, str]:
        """
        Classify location evidence relative to the candidate direction.

        Location is only meaningful relative to a bias, so this is
        evaluated against the candidate setup direction.
        """

        location = ctx.support_resistance.location
        label = location.name

        if location == PriceLocation.UNKNOWN:
            return (
                EvidenceItem(
                    source=EvidenceSource.LOCATION,
                    direction=SetupDirection.UNKNOWN,
                    alignment=EvidenceAlignment.ABSENT,
                    label=label,
                    reason="No support/resistance levels available.",
                ),
                label,
            )

        if location == PriceLocation.INSIDE_RANGE:
            return (
                EvidenceItem(
                    source=EvidenceSource.LOCATION,
                    direction=SetupDirection.NEUTRAL,
                    alignment=EvidenceAlignment.NEUTRAL,
                    label=label,
                    reason=(
                        "Price is inside the support/resistance range; "
                        "no constructive location."
                    ),
                ),
                label,
            )

        if candidate == SetupDirection.UNKNOWN:
            # No candidate bias: location carries no alignment.
            return (
                EvidenceItem(
                    source=EvidenceSource.LOCATION,
                    direction=SetupDirection.NEUTRAL,
                    alignment=EvidenceAlignment.NEUTRAL,
                    label=label,
                    reason=(
                        "Price location observed but no candidate "
                        "direction to evaluate it against."
                    ),
                ),
                label,
            )

        if candidate == SetupDirection.BULLISH:
            aligned = location in (
                PriceLocation.NEAR_SUPPORT,
                PriceLocation.ABOVE_RESISTANCE,
            )
            direction = SetupDirection.BULLISH
            reason = (
                "Price near/above support; constructive for a "
                "bullish candidate."
                if aligned
                else "Price near/below resistance; adverse for a "
                "bullish candidate."
            )
        else:  # BEARISH candidate
            aligned = location in (
                PriceLocation.NEAR_RESISTANCE,
                PriceLocation.BELOW_SUPPORT,
            )
            direction = SetupDirection.BEARISH
            reason = (
                "Price near/below resistance; constructive for a "
                "bearish candidate."
                if aligned
                else "Price near/above support; adverse for a "
                "bearish candidate."
            )

        return (
            EvidenceItem(
                source=EvidenceSource.LOCATION,
                direction=direction,
                alignment=(
                    EvidenceAlignment.ALIGNED
                    if aligned
                    else EvidenceAlignment.CONFLICTING
                ),
                label=label,
                reason=reason,
            ),
            label,
        )

    def _range_evidence(
        self,
        ctx: MarketContext,
    ) -> tuple[EvidenceItem, str]:
        """Classify range / regime evidence."""

        state = ctx.range.state
        label = state.name

        if state == RangeState.IN_RANGE:
            return (
                EvidenceItem(
                    source=EvidenceSource.RANGE,
                    direction=SetupDirection.NEUTRAL,
                    alignment=EvidenceAlignment.NEUTRAL,
                    label=label,
                    reason=(
                        "Active consolidation range; not a directional "
                        "trend setup."
                    ),
                ),
                label,
            )
        if state == RangeState.NOT_IN_RANGE:
            # Directional regime; aligned with whichever candidate
            # direction emerges. Alignment finalised during assembly.
            return (
                EvidenceItem(
                    source=EvidenceSource.RANGE,
                    direction=SetupDirection.UNKNOWN,
                    alignment=EvidenceAlignment.ABSENT,
                    label=label,
                    reason=(
                        "Market is not in a range; directional regime "
                        "does not conflict with a trend setup."
                    ),
                ),
                label,
            )
        # UNKNOWN
        return (
            EvidenceItem(
                source=EvidenceSource.RANGE,
                direction=SetupDirection.UNKNOWN,
                alignment=EvidenceAlignment.ABSENT,
                label=label,
                reason="Range state unknown.",
            ),
            label,
        )

    # ========================================================
    # CANDIDATE DIRECTION + ASSEMBLY
    # ========================================================

    def _candidate_direction(
        self,
        trend: EvidenceItem,
        structure: EvidenceItem,
        candle: EvidenceItem,
    ) -> SetupDirection:
        """
        Determine the candidate setup direction from the directional
        evidence sources (trend / structure / candle).

        Neutral / unknown / absent sources do not vote. A clear
        majority is required; ties or no votes yield ``UNKNOWN``.
        """

        votes: list[SetupDirection] = []
        for item in (trend, structure, candle):
            if item.direction in (
                SetupDirection.BULLISH,
                SetupDirection.BEARISH,
            ):
                votes.append(item.direction)

        if not votes:
            return SetupDirection.UNKNOWN

        bullish = votes.count(SetupDirection.BULLISH)
        bearish = votes.count(SetupDirection.BEARISH)

        if bullish > bearish:
            return SetupDirection.BULLISH
        if bearish > bullish:
            return SetupDirection.BEARISH
        return SetupDirection.UNKNOWN

    def _assemble_evidence(
        self,
        trend: EvidenceItem,
        structure: EvidenceItem,
        candle: EvidenceItem,
        location: EvidenceItem,
        range_item: EvidenceItem,
    ) -> SetupEvidence:
        """
        Finalise alignments against the candidate direction and build
        the structured evidence with supporting / conflicting views.
        """

        candidate = self._candidate_direction(trend, structure, candle)

        finalised: list[EvidenceItem] = []
        for item in (trend, structure, candle, location, range_item):
            finalised.append(self._finalise_alignment(item, candidate))

        trend_f, structure_f, candle_f, location_f, range_f = finalised

        supporting = tuple(
            i for i in finalised if i.alignment == EvidenceAlignment.ALIGNED
        )
        conflicting = tuple(
            i for i in finalised
            if i.alignment == EvidenceAlignment.CONFLICTING
        )

        return SetupEvidence(
            trend=trend_f,
            structure=structure_f,
            candle=candle_f,
            location=location_f,
            range=range_f,
            supporting=supporting,
            conflicting=conflicting,
        )

    def _finalise_alignment(
        self,
        item: EvidenceItem,
        candidate: SetupDirection,
    ) -> EvidenceItem:
        """
        Set the alignment of a directional evidence item relative to
        the candidate direction.

        Location alignment is already resolved (it is inherently
        relative). Range is NEUTRAL/ABSENT and not directional. Only
        trend / structure / candle directional items are compared here.
        """

        if item.source == EvidenceSource.LOCATION:
            return item
        if item.source == EvidenceSource.RANGE:
            return item

        # Neutral / unknown / absent items keep their alignment.
        if item.direction not in (
            SetupDirection.BULLISH,
            SetupDirection.BEARISH,
        ):
            return item

        if candidate == SetupDirection.UNKNOWN:
            # No candidate: a lone directional source is recorded as
            # ALIGNED with itself so it can still count toward WATCH.
            return EvidenceItem(
                source=item.source,
                direction=item.direction,
                alignment=EvidenceAlignment.ALIGNED,
                label=item.label,
                reason=item.reason,
            )

        if item.direction == candidate:
            alignment = EvidenceAlignment.ALIGNED
        else:
            alignment = EvidenceAlignment.CONFLICTING

        return EvidenceItem(
            source=item.source,
            direction=item.direction,
            alignment=alignment,
            label=item.label,
            reason=item.reason,
        )

    def _empty_evidence(self) -> SetupEvidence:
        """All-absent evidence for an unobserved market context."""

        def _absent(source: EvidenceSource, reason: str) -> EvidenceItem:
            return EvidenceItem(
                source=source,
                direction=SetupDirection.UNKNOWN,
                alignment=EvidenceAlignment.ABSENT,
                label="none",
                reason=reason,
            )

        trend_absent = _absent(
            EvidenceSource.TREND, "No market context available.",
        )
        structure_absent = _absent(
            EvidenceSource.STRUCTURE, "No market context available.",
        )
        candle_absent = _absent(
            EvidenceSource.CANDLE, "No candle evidence available.",
        )
        location_absent = _absent(
            EvidenceSource.LOCATION, "No location evidence available.",
        )
        range_absent = _absent(
            EvidenceSource.RANGE, "No range evidence available.",
        )
        return SetupEvidence(
            trend=trend_absent,
            structure=structure_absent,
            candle=candle_absent,
            location=location_absent,
            range=range_absent,
            supporting=tuple(),
            conflicting=tuple(),
        )

    # ========================================================
    # STRUCTURE LABEL
    # ========================================================

    def _structure_label(self, ctx: MarketContext) -> str:
        """Short label for the recent structure sequence."""

        if not ctx.recent_structure:
            return "none"
        return " / ".join(
            s.structure.name for s in ctx.recent_structure
        )

    # ========================================================
    # CLASSIFICATION
    # ========================================================

    def _build_assessment(
        self,
        index: int,
        timestamp,
        evidence: SetupEvidence,
        candle_label: str,
        structure_label: str,
        trend_label: str,
        location_label: str,
        regime_label: str,
    ) -> SetupAssessment:
        """Compute the direction + classification and assemble."""

        candidate = self._candidate_direction(
            evidence.trend, evidence.structure, evidence.candle,
        )

        confluence_score = len(evidence.supporting)
        has_conflict = len(evidence.conflicting) > 0

        in_range = (
            evidence.range.label == RangeState.IN_RANGE.name
        )

        # -----------------------------------------------------
        # DIRECTION
        # -----------------------------------------------------
        if candidate == SetupDirection.UNKNOWN:
            if confluence_score == 0:
                direction = SetupDirection.UNKNOWN
            else:
                # A lone directional source with no candidate still
                # imparts its direction descriptively.
                direction = evidence.supporting[0].direction
        else:
            direction = candidate

        # -----------------------------------------------------
        # CLASSIFICATION
        # -----------------------------------------------------
        classification, reason = self._classify(
            direction=direction,
            candidate=candidate,
            confluence_score=confluence_score,
            has_conflict=has_conflict,
            in_range=in_range,
            evidence=evidence,
        )

        return SetupAssessment(
            index=index,
            timestamp=timestamp,
            direction=direction,
            classification=classification,
            confluence_score=confluence_score,
            evidence=evidence,
            candle_evidence=candle_label,
            structure_evidence=structure_label,
            trend_evidence=trend_label,
            location_evidence=location_label,
            regime_evidence=regime_label,
            reason=reason,
        )

    def _classify(
        self,
        direction: SetupDirection,
        candidate: SetupDirection,
        confluence_score: int,
        has_conflict: bool,
        in_range: bool,
        evidence: SetupEvidence,
    ) -> tuple[SetupClassification, str]:
        """Apply the deterministic classification rules."""

        # No candidate direction and no aligned evidence.
        if candidate == SetupDirection.UNKNOWN and confluence_score == 0:
            return (
                SetupClassification.NO_SETUP,
                "No directional evidence; insufficient confluence for "
                "any setup classification.",
            )

        # Range caps the classification at WATCH by default.
        range_capped = (
            self.config.range_caps_classification and in_range
        )

        # Conflicts block POTENTIAL_SETUP by default.
        conflict_blocks = (
            self.config.conflicting_blocks_potential_setup
            and has_conflict
        )

        if confluence_score >= self.config.min_supporting_for_potential_setup:
            if conflict_blocks:
                return (
                    SetupClassification.WATCH,
                    f"Confluence of {confluence_score} aligned "
                    "sources but conflicting evidence present; "
                    "capped at WATCH.",
                )
            if range_capped:
                return (
                    SetupClassification.WATCH,
                    f"Confluence of {confluence_score} aligned "
                    "sources but market is in a range; capped at "
                    "WATCH (not a directional trend setup).",
                )
            return (
                SetupClassification.POTENTIAL_SETUP,
                f"Confluence of {confluence_score} independent "
                "evidence sources aligned with a "
                f"{direction.name} candidate; no disqualifying "
                "conflict. Descriptive candidate setup.",
            )

        if confluence_score >= self.config.min_supporting_for_watch:
            return (
                SetupClassification.WATCH,
                f"Confluence of {confluence_score} aligned "
                "source(s); not enough for a potential setup.",
            )

        return (
            SetupClassification.NO_SETUP,
            "Insufficient aligned evidence for a setup classification.",
        )


__all__ = ["SetupConfluenceEngine"]
