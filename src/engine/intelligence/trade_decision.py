"""
Trade candidate ranking & decision engine (Sprint 11S).

``TradeDecisionEngine`` turns one or more Sprint 11R ``TradeCandidate``
objects into a deterministic, descriptive DECISION and RANKING. It is
the fourth step of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)  <- this layer
    5. TRADE VALIDATION             (future)
    6. SIGNAL / EXECUTION           (future)

The engine is deterministic, pure, future-leakage safe and independent
from the historical evaluation runner. It reads ONLY the already-computed
``TradeCandidate`` (Sprint 11R), which is itself derived from
``candles[:T+1]`` only; it inspects no candles directly and therefore
cannot introduce look-ahead bias.

DESIGN PRINCIPLE — descriptive evidence strength, not probability:

The ``Decision Score`` is an integer in ``[0, 100]`` expressing the
STRENGTH and COMPLETENESS of the deterministic technical evidence
behind a candidate. It is computed as a transparent, auditable sum of
named components (trend / structure / candle / location / geometry /
risk-reward / no-conflict). It is NOT a probability of success, NOT a
profitability prediction, and NOT a trading recommendation.

DESIGN PRINCIPLE — reuse, do not re-invent:

The decision layer reuses the Sprint 11R candidate's directional bias,
status, confluence score, supporting / conflicting evidence, geometry
and risk/reward verbatim. It does NOT recompute candle patterns, market
context, setups or candidates.

DESIGN PRINCIPLE — conflict matters:

Conflicting evidence is never ignored. A candidate with conflicting
evidence is capped (by default at QUALIFIED) and loses the no-conflict
component of its score. PREFERRED is never produced merely because a
majority of evidence sources are aligned when a conflict is present.

DESIGN PRINCIPLE — honest geometry:

Incomplete entry / stop / target geometry is reported honestly. No
stop, target, risk, reward or risk/reward value is ever fabricated. A
candidate with incomplete geometry loses the geometry and (part of the)
risk-reward score, and (by default) cannot be PREFERRED.

This is intelligence, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g.
``from engine.intelligence.trade_decision import TradeDecisionEngine``.
"""

from __future__ import annotations

from datetime import datetime

from engine.config.trade_decision_config import (
    DecisionWeights,
    TradeDecisionConfig,
)
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceSource,
)
from engine.models.trade_candidate import (
    CandidateDirection,
    CandidateStatus,
    TradeCandidate,
)
from engine.models.trade_decision import (
    DecisionClassification,
    DecisionScore,
    DecisionScoreComponent,
    RankedDecision,
    TradeDecision,
    TradeDecisionRanking,
)


# ============================================================
# EVIDENCE SOURCE -> WEIGHT ATTRIBUTE NAME + ABSENCE SENTINEL
# ============================================================
#
# Maps each Sprint 11Q evidence source to the decision-weight attribute
# it contributes to and the label value that signals ABSENCE. The
# ``TradeCandidate`` (Sprint 11R) carries the aligned (supporting) and
# conflicting evidence subsets plus short labels per source. To score
# each independent source we reconstruct its alignment relative to the
# candidate direction WITHOUT modifying the 11R model:
#
#   * a source present in ``supporting_evidence`` is ALIGNED;
#   * a source present in ``conflicting_evidence`` is CONFLICTING;
#   * otherwise its label is inspected: the absence sentinel means
#     ABSENT (no evidence of that kind was available); any other
#     value means NEUTRAL (evidence exists but carries no directional
#     information).
#
# The range evidence source does not carry a standalone directional
# weight: a range is reflected through the conflict cap and the 11R
# candidate status (range-blocked candidates are WATCH).

_SOURCE_TO_WEIGHT = {
    "trend": "trend",
    "structure": "structure",
    "candle": "candle",
    "location": "location",
}

# Label field on TradeCandidate for each source, and the sentinel value
# that marks that source as ABSENT (unobserved) rather than NEUTRAL.
_SOURCE_LABEL = {
    "trend": ("market_trend", "UNKNOWN"),
    "structure": ("market_structure", "none"),
    "candle": ("candle_evidence", "none"),
    "location": ("location", "UNKNOWN"),
}

# Source name -> EvidenceSource enum member for subset membership tests.
_SOURCE_ENUM = {
    "trend": EvidenceSource.TREND,
    "structure": EvidenceSource.STRUCTURE,
    "candle": EvidenceSource.CANDLE,
    "location": EvidenceSource.LOCATION,
}


class TradeDecisionEngine:
    """
    Produce a deterministic, descriptive decision and ranking over one
    or more Sprint 11R trade candidates.

    Public API:

        decide(candidate, index, timestamp) -> TradeDecision
        rank(candidates, index, timestamp) -> TradeDecisionRanking

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(
        self,
        config: TradeDecisionConfig | None = None,
    ) -> None:
        self.config = config or TradeDecisionConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def decide(
        self,
        candidate: TradeCandidate,
        index: int,
        timestamp: datetime | None = None,
    ) -> TradeDecision:
        """
        Produce a descriptive ``TradeDecision`` over a single
        candidate.

        ``index`` and ``timestamp`` identify the evaluation point (the
        pipeline passes the trigger candle's index / timestamp). The
        candidate is retained by reference and never modified.
        """

        score = self._score(candidate)
        classification = self._classify(candidate, score)
        rationale = self._rationale(candidate, score, classification)

        supporting = candidate.supporting_evidence
        conflicting = candidate.conflicting_evidence

        return TradeDecision(
            timestamp=timestamp,
            evaluation_index=index,
            candidate=candidate,
            direction=candidate.direction.name,
            classification=classification,
            score=score,
            geometry_complete=candidate.geometry_complete,
            confluence_score=candidate.confluence_score,
            supporting_count=len(supporting),
            conflicting_count=len(conflicting),
            risk_reward_ratio=candidate.risk_reward_ratio,
            rationale=rationale,
        )

    def rank(
        self,
        candidates,
        index: int,
        timestamp: datetime | None = None,
    ) -> TradeDecisionRanking:
        """
        Produce a deterministic, descriptive ranking over one or more
        candidates.

        Candidates are ranked strongest-first by a deterministic
        tie-breaking key. Ranks are 1-based and unique. A preferred
        candidate is identified only when a PREFERRED decision exists;
        a winner is never manufactured.
        """

        candidate_list = list(candidates)
        decisions = [
            self.decide(c, index, timestamp) for c in candidate_list
        ]
        decisions.sort(key=self._ranking_key)

        ranked = [
            RankedDecision(rank=i + 1, decision=d)
            for i, d in enumerate(decisions)
        ]

        preferred = None
        if ranked and ranked[0].decision.is_preferred:
            preferred = ranked[0]

        rationale = self._ranking_rationale(ranked)

        return TradeDecisionRanking(
            timestamp=timestamp,
            evaluation_index=index,
            decisions=tuple(ranked),
            candidate_count=len(ranked),
            preferred=preferred,
            rationale=rationale,
        )

    # ========================================================
    # SCORING
    # ========================================================

    def _score(
        self,
        candidate: TradeCandidate,
    ) -> DecisionScore:
        """
        Compute the deterministic ``Decision Score``.

        The score is a transparent sum of named components. Each
        directional evidence source (trend / structure / candle /
        location) contributes a share of its weight based on its
        alignment relative to the candidate direction: ALIGNED earns
        the full weight, NEUTRAL earns ``neutral_fraction`` of the
        weight, CONFLICTING / ABSENT earn zero. Geometry and
        risk/reward contribute based on completeness / quality. The
        no-conflict component is awarded only when no conflicting
        evidence is present.
        """

        weights: DecisionWeights = self.config.weights
        components: list[DecisionScoreComponent] = []
        total = 0

        # --- Directional evidence sources ----------------------
        # Reconstruct each source's alignment relative to the
        # candidate direction from the supporting / conflicting
        # subsets the Sprint 11R candidate already carries, plus the
        # short per-source label. No 11R model is modified.
        supporting = candidate.supporting_evidence
        conflicting = candidate.conflicting_evidence
        supporting_sources = {item.source for item in supporting}
        conflicting_sources = {item.source for item in conflicting}

        for source_name, weight_attr in _SOURCE_TO_WEIGHT.items():
            weight = getattr(weights, weight_attr)
            alignment = self._source_alignment(
                source_name,
                supporting_sources,
                conflicting_sources,
                candidate,
            )
            points, reason = self._evidence_points(
                alignment, weight, source_name,
            )
            total += points
            components.append(
                DecisionScoreComponent(
                    name=source_name,
                    points=points,
                    max_points=weight,
                    reason=reason,
                )
            )

        # --- Geometry completeness ----------------------------
        geo_points, geo_reason = self._geometry_points(candidate)
        total += geo_points
        components.append(
            DecisionScoreComponent(
                name="geometry",
                points=geo_points,
                max_points=weights.geometry,
                reason=geo_reason,
            )
        )

        # --- Risk / reward quality ----------------------------
        rr_points, rr_reason = self._risk_reward_points(candidate)
        total += rr_points
        components.append(
            DecisionScoreComponent(
                name="risk_reward",
                points=rr_points,
                max_points=weights.risk_reward,
                reason=rr_reason,
            )
        )

        # --- No conflicting evidence --------------------------
        nc_points, nc_reason = self._no_conflict_points(candidate)
        total += nc_points
        components.append(
            DecisionScoreComponent(
                name="no_conflict",
                points=nc_points,
                max_points=weights.no_conflict,
                reason=nc_reason,
            )
        )

        # Clamp for floating-point safety; the integer components
        # above are already rounded, so this is a no-op in practice.
        total = max(0, min(weights.total, int(round(total))))

        return DecisionScore(
            total=total,
            max_total=weights.total,
            components=tuple(components),
            reason=(
                "Decision score is deterministic technical-evidence "
                "strength/completeness; not predictive."
            ),
        )

    def _source_alignment(
        self,
        source_name: str,
        supporting_sources: set,
        conflicting_sources: set,
        candidate: TradeCandidate,
    ) -> EvidenceAlignment:
        """
        Reconstruct a single evidence source's alignment relative to
        the candidate direction WITHOUT modifying the Sprint 11R model.

        * present in the supporting subset -> ALIGNED;
        * present in the conflicting subset -> CONFLICTING;
        * otherwise inspect the source's short label: the absence
          sentinel marks ABSENT (no evidence of that kind was
          available); any other value marks NEUTRAL (evidence exists
          but carries no directional information).
        """

        source_enum = _SOURCE_ENUM[source_name]
        if source_enum in supporting_sources:
            return EvidenceAlignment.ALIGNED
        if source_enum in conflicting_sources:
            return EvidenceAlignment.CONFLICTING
        label_attr, sentinel = _SOURCE_LABEL[source_name]
        label = getattr(candidate, label_attr)
        if label == sentinel:
            return EvidenceAlignment.ABSENT
        return EvidenceAlignment.NEUTRAL

    def _evidence_points(
        self,
        alignment: EvidenceAlignment,
        weight: int,
        source_name: str,
    ) -> tuple[int, str]:
        """Points for a single directional evidence source."""

        if weight == 0:
            return 0, f"{source_name} weight is zero."
        if alignment == EvidenceAlignment.ALIGNED:
            return weight, f"{source_name} aligned with candidate."
        if alignment == EvidenceAlignment.NEUTRAL:
            pts = int(round(weight * self.config.neutral_fraction))
            return (
                pts,
                f"{source_name} neutral; partial credit "
                f"({self.config.neutral_fraction:.2f}).",
            )
        if alignment == EvidenceAlignment.CONFLICTING:
            return 0, f"{source_name} conflicts with candidate."
        # ABSENT
        return 0, f"{source_name} absent (no evidence)."

    def _geometry_points(
        self,
        candidate: TradeCandidate,
    ) -> tuple[int, str]:
        """
        Points for entry / stop / target geometry completeness.

        Complete geometry (entry + stop + target + positive risk and
        reward) earns the full weight. An entry reference alone (a
        watch-level candidate with no stop / target) earns a partial
        share. No geometry earns nothing. Nothing is fabricated.
        """

        weight = self.config.weights.geometry
        if weight == 0:
            return 0, "geometry weight is zero."
        if candidate.geometry_complete:
            return weight, "Geometry complete (entry/stop/target/R:R)."
        if candidate.entry_reference is not None:
            share = int(round(weight * self.config.neutral_fraction))
            return (
                share,
                "Entry present but stop/target incomplete; partial "
                "credit.",
            )
        return 0, "Geometry incomplete (no entry reference)."

    def _risk_reward_points(
        self,
        candidate: TradeCandidate,
    ) -> tuple[int, str]:
        """
        Points for risk/reward quality, derived ONLY from structural
        references available at the evaluation point.

        ``good_risk_reward_ratio`` or above earns the full weight.
        ``min_risk_reward_ratio`` or above earns a partial share
        (half). Below the minimum (or absent / invalid) earns zero.
        Incomplete geometry is reported honestly rather than
        fabricated.
        """

        weight = self.config.weights.risk_reward
        if weight == 0:
            return 0, "risk_reward weight is zero."
        ratio = candidate.risk_reward_ratio
        if ratio is None:
            return 0, "Risk/reward unavailable (incomplete geometry)."
        if ratio >= self.config.good_risk_reward_ratio:
            return (
                weight,
                f"Risk/reward {ratio:.2f} >= good "
                f"({self.config.good_risk_reward_ratio:.2f}).",
            )
        if ratio >= self.config.min_risk_reward_ratio:
            share = int(round(weight * 0.5))
            return (
                share,
                f"Risk/reward {ratio:.2f} acceptable (>= "
                f"{self.config.min_risk_reward_ratio:.2f}).",
            )
        return (
            0,
            f"Risk/reward {ratio:.2f} below minimum "
            f"({self.config.min_risk_reward_ratio:.2f}).",
        )

    def _no_conflict_points(
        self,
        candidate: TradeCandidate,
    ) -> tuple[int, str]:
        """Points for the absence of conflicting evidence."""

        weight = self.config.weights.no_conflict
        if weight == 0:
            return 0, "no_conflict weight is zero."
        n = len(candidate.conflicting_evidence)
        if n == 0:
            return weight, "No conflicting evidence."
        return 0, f"{n} conflicting evidence source(s) present."

    # ========================================================
    # CLASSIFICATION
    # ========================================================

    def _classify(
        self,
        candidate: TradeCandidate,
        score: DecisionScore,
    ) -> DecisionClassification:
        """
        Classify a candidate from its score, then apply caps.

        The base classification is derived from the score thresholds.
        The following caps are then applied (each can only reduce the
        classification):

        * A candidate with no directional bias (NONE), a Sprint 11R
          NO_CANDIDATE status, or no entry reference is REJECTED.
        * A Sprint 11R WATCH status (non-promoted candidate) is capped
          at ``watch_status_max_classification``.
        * A candidate with conflicting evidence is capped at
          ``conflict_max_classification`` (so conflict can never
          automatically produce PREFERRED by default).
        * When ``require_geometry_for_preferred`` is True, incomplete
          geometry caps the classification at QUALIFIED.
        """

        cls = self._base_classification(score.total)
        cls = self._cap_non_directional(candidate, cls)
        cls = self._cap_watch_status(candidate, cls)
        cls = self._cap_conflict(candidate, cls)
        cls = self._cap_geometry(candidate, cls)
        return cls

    def _base_classification(self, total: int) -> DecisionClassification:
        """Score-threshold classification (before caps)."""

        if total >= self.config.preferred_threshold:
            return DecisionClassification.PREFERRED
        if total >= self.config.qualified_threshold:
            return DecisionClassification.QUALIFIED
        if total >= self.config.watch_threshold:
            return DecisionClassification.WATCH
        return DecisionClassification.REJECTED

    def _cap_non_directional(
        self,
        candidate: TradeCandidate,
        cls: DecisionClassification,
    ) -> DecisionClassification:
        """No-direction / no-candidate / no-entry -> REJECTED."""

        if (
            candidate.direction == CandidateDirection.NONE
            or candidate.status == CandidateStatus.NO_CANDIDATE
            or candidate.entry_reference is None
        ):
            return DecisionClassification.REJECTED
        return cls

    def _cap_watch_status(
        self,
        candidate: TradeCandidate,
        cls: DecisionClassification,
    ) -> DecisionClassification:
        """A Sprint 11R WATCH candidate is capped at the configured max."""

        if candidate.status != CandidateStatus.WATCH:
            return cls
        return self._cap_at(
            cls, self.config.watch_status_max_classification,
        )

    def _cap_conflict(
        self,
        candidate: TradeCandidate,
        cls: DecisionClassification,
    ) -> DecisionClassification:
        """Conflicting evidence caps the classification."""

        if len(candidate.conflicting_evidence) == 0:
            return cls
        return self._cap_at(cls, self.config.conflict_max_classification)

    def _cap_geometry(
        self,
        candidate: TradeCandidate,
        cls: DecisionClassification,
    ) -> DecisionClassification:
        """Incomplete geometry caps at QUALIFIED (when configured)."""

        if not self.config.require_geometry_for_preferred:
            return cls
        if candidate.geometry_complete:
            return cls
        return self._cap_at(cls, DecisionClassification.QUALIFIED)

    @staticmethod
    def _cap_at(
        cls: DecisionClassification,
        ceiling: DecisionClassification,
    ) -> DecisionClassification:
        """Reduce ``cls`` to at most ``ceiling``."""

        if cls.rank_value > ceiling.rank_value:
            return ceiling
        return cls

    # ========================================================
    # RANKING
    # ========================================================

    def _ranking_key(self, decision: TradeDecision) -> tuple:
        """
        Deterministic ranking key (strongest first).

        Ties are broken by a fully deterministic sequence of secondary
        keys so identical inputs always produce identical rankings and
        no two decisions share a rank:

        1. classification strength (desc)
        2. decision score (desc)
        3. confluence score (desc)
        4. geometry complete first
        5. risk/reward ratio (desc, absent last)
        6. fewer conflicting evidence sources
        7. candidate direction order (LONG < SHORT < NONE for
           determinism)
        8. evaluation index (asc)
        9. entry reference (asc) as a final stable tie-break
        """

        c = decision.candidate
        ratio = c.risk_reward_ratio
        direction_order = {
            CandidateDirection.LONG: 0,
            CandidateDirection.SHORT: 1,
            CandidateDirection.NONE: 2,
        }[c.direction]
        entry = c.entry_reference
        return (
            -decision.classification.rank_value,
            -decision.decision_score,
            -decision.confluence_score,
            0 if decision.geometry_complete else 1,
            -(ratio if ratio is not None else -1.0),
            decision.conflicting_count,
            direction_order,
            decision.evaluation_index,
            entry if entry is not None else float("inf"),
        )

    # ========================================================
    # RATIONALE
    # ========================================================

    def _rationale(
        self,
        candidate: TradeCandidate,
        score: DecisionScore,
        classification: DecisionClassification,
    ) -> str:
        """Descriptive rationale for a single decision."""

        parts: list[str] = []

        parts.append(
            f"{classification.name} ({score.total}/{score.max_total}).",
        )

        if candidate.direction == CandidateDirection.NONE:
            parts.append("No directional bias; rejected.")
        elif candidate.status == CandidateStatus.NO_CANDIDATE:
            parts.append("No trade candidate generated; rejected.")
        elif candidate.status == CandidateStatus.WATCH:
            parts.append("Sprint 11R WATCH candidate; capped.")
        else:
            parts.append(
                f"{candidate.direction.name} candidate with "
                f"{candidate.confluence_score} aligned source(s).",
            )

        if len(candidate.conflicting_evidence) > 0:
            parts.append(
                f"{len(candidate.conflicting_evidence)} conflicting "
                "source(s) reduce/cap the decision.",
            )

        if not candidate.geometry_complete:
            parts.append("Geometry incomplete; reported honestly.")

        if candidate.risk_reward_ratio is not None:
            parts.append(
                f"Risk/reward {candidate.risk_reward_ratio:.2f}.",
            )

        parts.append(
            "Decision is descriptive technical-evidence strength; "
            "not predictive and not a guarantee of profitability.",
        )
        return " ".join(parts)

    def _ranking_rationale(
        self,
        ranked: list[RankedDecision],
    ) -> str:
        """Descriptive rationale for the ranking."""

        if not ranked:
            return "No candidates to rank."
        top = ranked[0]
        if top.decision.is_preferred:
            return (
                f"Preferred candidate identified at rank 1 "
                f"({top.decision.direction}, score "
                f"{top.decision.decision_score}). Ranking is "
                "descriptive; not predictive and not a guarantee."
            )
        return (
            f"Strongest candidate at rank 1 is "
            f"{top.decision.classification.name} "
            f"({top.decision.direction}, score "
            f"{top.decision.decision_score}); no PREFERRED candidate. "
            "Ranking is descriptive; not predictive and not a guarantee."
        )


__all__ = ["TradeDecisionEngine"]
