from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DecisionDirection(Enum):
    """
    Direction carried forward from the confluence layer.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class SetupQuality(Enum):
    """
    Overall quality of the current market setup.

    INVALID:
        Evidence is insufficient or contradictory.

    WEAK:
        Some directional evidence exists, but quality is low.

    MODERATE:
        Directional evidence is usable but not especially strong.

    STRONG:
        Directional evidence is well aligned.

    EXCELLENT:
        Multiple important signals align with very low conflict.
    """

    INVALID = "INVALID"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    EXCELLENT = "EXCELLENT"


class DecisionStatus(Enum):
    """
    Eligibility state for downstream decision-making.

    NOT_READY:
        The engine does not have enough evidence.

    CONFLICTED:
        Bullish and bearish evidence materially conflict.

    READY:
        Evidence is sufficiently aligned for downstream
        signal-generation logic to evaluate the setup.
    """

    NOT_READY = "NOT_READY"
    CONFLICTED = "CONFLICTED"
    READY = "READY"


@dataclass(frozen=True)
class DecisionEvidence:
    """
    Explainable evidence used by the decision-context layer.
    """

    name: str
    direction: DecisionDirection
    score: float
    reason: str


@dataclass(frozen=True)
class DecisionContext:
    """
    Structured decision context produced from ConfluenceResult.

    This object deliberately does NOT contain:

    - entry price
    - stop loss
    - take profit
    - position size
    - buy/sell execution instructions

    Those belong to later layers.
    """

    direction: DecisionDirection

    confidence: float

    setup_quality: SetupQuality

    status: DecisionStatus

    trade_eligible: bool

    bullish_score: float = 0.0
    bearish_score: float = 0.0
    net_score: float = 0.0

    conflict: str = "NONE"

    evidence_quality: float = 0.0

    evidence: tuple[DecisionEvidence, ...] = field(
        default_factory=tuple
    )

    reasons: tuple[str, ...] = field(
        default_factory=tuple
    )