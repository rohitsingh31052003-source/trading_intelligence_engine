"""
Configuration for the trade candidate ranking & decision layer
(Sprint 11S).

All scoring weights, thresholds and caps live here; no magic numbers
are embedded in the engine. The defaults are deliberately conservative
and deterministic. They are NOT calibrated to any market; they express
interpretable, rule-based decision criteria.

The decision layer is DESCRIPTIVE. The ``Decision Score`` it produces
represents deterministic technical-evidence strength and completeness.
It is NOT a probability of success or a profitability prediction.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.models.trade_decision import DecisionClassification


@dataclass(slots=True, frozen=True)
class DecisionWeights:
    """
    Per-component weights for the ``Decision Score``.

    Every weight is a non-negative integer. The sum of all weights is
    the maximum achievable score (``100``). Each weight expresses how
    much a single, independent technical-evidence dimension contributes
    to the overall strength / completeness of a candidate.

    Attributes:

    trend
        Weight for the descriptive market-trend alignment evidence
        (Sprint 11P / 11Q trend evidence item).

    structure
        Weight for the recent market-structure alignment evidence
        (HH / HL / LH / LL sequence).

    candle
        Weight for the candle / price-action pattern evidence
        (Sprint 11O) alignment.

    location
        Weight for the price-location-relative-to-support-resistance
        evidence (Sprint 11P / 11Q).

    geometry
        Weight for entry / stop / target geometry completeness (entry
        + stop + target + positive risk/reward). Partial geometry
        earns a partial share of this weight; no geometry earns none.

    risk_reward
        Weight for the risk/reward ratio quality (good / acceptable /
        poor-or-absent). Derived ONLY from structural references
        available at the evaluation point.

    no_conflict
        Weight for the absence of conflicting evidence. A candidate
        with any conflicting evidence earns zero on this component;
        a clean candidate earns the full weight.
    """

    trend: int = 15
    structure: int = 15
    candle: int = 10
    location: int = 10
    geometry: int = 20
    risk_reward: int = 15
    no_conflict: int = 15

    def __post_init__(self) -> None:
        for name in (
            "trend",
            "structure",
            "candle",
            "location",
            "geometry",
            "risk_reward",
            "no_conflict",
        ):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(
                    f"Decision weight '{name}' must be non-negative.",
                )
        if self.total <= 0:
            raise ValueError(
                "Decision weights must sum to a positive total.",
            )

    @property
    def total(self) -> int:
        """Sum of all weights (the maximum achievable score)."""

        return (
            self.trend
            + self.structure
            + self.candle
            + self.location
            + self.geometry
            + self.risk_reward
            + self.no_conflict
        )


@dataclass(slots=True, frozen=True)
class TradeDecisionConfig:
    """
    Configuration for ``TradeDecisionEngine``.

    Classification thresholds (documented in the engine):

    preferred_threshold
        Minimum ``Decision Score`` (inclusive) required for a
        candidate to be eligible for PREFERRED. Default ``80``.

    qualified_threshold
        Minimum score (inclusive) for QUALIFIED. Default ``60``.

    watch_threshold
        Minimum score (inclusive) for WATCH. A candidate scoring below
        this is REJECTED. Default ``40``.

    Conflict / cap policy:

    conflict_max_classification
        The strongest classification a candidate with conflicting
        evidence may reach. Default ``QUALIFIED`` (conflict can never
        automatically produce PREFERRED). A conflicting candidate may
        still be REJECTED / WATCH depending on its score.

    watch_status_max_classification
        The strongest classification a Sprint 11R ``WATCH`` (or
        ``NO_CANDIDATE``) candidate may reach. Default ``QUALIFIED``.
        A non-promoted candidate is never PREFERRED.

    require_geometry_for_preferred
        When True (default), a candidate with incomplete entry / stop /
        target geometry cannot be PREFERRED (capped at QUALIFIED).
        Incomplete geometry is reported honestly, never fabricated.

    neutral_fraction
        Fraction of an evidence-source weight awarded when the source
        is NEUTRAL (carries no directional information). ALIGNED earns
        the full weight; CONFLICTING / ABSENT earn zero. Default
        ``0.5``. Must lie within ``[0.0, 1.0]``.

    Risk / reward quality bands:

    good_risk_reward_ratio
        A risk/reward ratio at or above this value earns the full
        risk_reward weight. Default ``2.0``.

    min_risk_reward_ratio
        A risk/reward ratio at or above this value (but below
        ``good``) earns a partial share of the risk_reward weight.
        Below this value (or absent) earns zero. Default ``1.0``.
    """

    weights: DecisionWeights = DecisionWeights()

    preferred_threshold: int = 80
    qualified_threshold: int = 60
    watch_threshold: int = 40

    conflict_max_classification: DecisionClassification = (
        DecisionClassification.QUALIFIED
    )
    watch_status_max_classification: DecisionClassification = (
        DecisionClassification.QUALIFIED
    )

    require_geometry_for_preferred: bool = True
    neutral_fraction: float = 0.5

    good_risk_reward_ratio: float = 2.0
    min_risk_reward_ratio: float = 1.0

    def __post_init__(self) -> None:
        w = self.weights.total
        if self.preferred_threshold < 0 or self.preferred_threshold > w:
            raise ValueError(
                "preferred_threshold must lie within [0, max score].",
            )
        if self.qualified_threshold < 0 or self.qualified_threshold > w:
            raise ValueError(
                "qualified_threshold must lie within [0, max score].",
            )
        if self.watch_threshold < 0 or self.watch_threshold > w:
            raise ValueError(
                "watch_threshold must lie within [0, max score].",
            )
        if not (
            self.watch_threshold
            <= self.qualified_threshold
            <= self.preferred_threshold
        ):
            raise ValueError(
                "Thresholds must satisfy "
                "watch_threshold <= qualified_threshold "
                "<= preferred_threshold.",
            )
        if self.conflict_max_classification not in (
            DecisionClassification.REJECTED,
            DecisionClassification.WATCH,
            DecisionClassification.QUALIFIED,
            DecisionClassification.PREFERRED,
        ):
            raise ValueError(
                "conflict_max_classification must be a valid "
                "DecisionClassification.",
            )
        if self.watch_status_max_classification not in (
            DecisionClassification.REJECTED,
            DecisionClassification.WATCH,
            DecisionClassification.QUALIFIED,
            DecisionClassification.PREFERRED,
        ):
            raise ValueError(
                "watch_status_max_classification must be a valid "
                "DecisionClassification.",
            )
        if not 0.0 <= self.neutral_fraction <= 1.0:
            raise ValueError(
                "neutral_fraction must lie within [0.0, 1.0].",
            )
        if self.good_risk_reward_ratio <= 0:
            raise ValueError(
                "good_risk_reward_ratio must be positive.",
            )
        if self.min_risk_reward_ratio <= 0:
            raise ValueError(
                "min_risk_reward_ratio must be positive.",
            )
        if self.min_risk_reward_ratio > self.good_risk_reward_ratio:
            raise ValueError(
                "min_risk_reward_ratio cannot exceed "
                "good_risk_reward_ratio.",
            )


__all__ = ["DecisionWeights", "TradeDecisionConfig"]
