from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConfluenceDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class EvidenceStrength(Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    NONE = "NONE"


@dataclass(frozen=True)
class ConfluenceEvidence:
    """
    One directional piece of evidence used by the
    Confluence Engine.
    """

    name: str
    direction: ConfluenceDirection
    score: float
    strength: EvidenceStrength
    reason: str


@dataclass(frozen=True)
class ConfluenceResult:
    """
    Final Confluence Engine result.

    The result deliberately keeps bullish and bearish
    evidence separate.

    This allows downstream systems to distinguish:

    - strong bullish conviction
    - strong bearish conviction
    - conflicting evidence
    - genuine lack of evidence
    """

    # -----------------------------------------------------
    # FINAL DIRECTION
    # -----------------------------------------------------

    direction: ConfluenceDirection

    # -----------------------------------------------------
    # CONFIDENCE
    # -----------------------------------------------------

    confidence: float

    # -----------------------------------------------------
    # BACKWARD-COMPATIBLE SCORE
    # -----------------------------------------------------

    score: float

    # -----------------------------------------------------
    # DIRECTIONAL SCORES
    # -----------------------------------------------------

    bullish_score: float = 0.0
    bearish_score: float = 0.0

    # Bullish minus bearish.
    net_score: float = 0.0

    # -----------------------------------------------------
    # CONFLICT
    # -----------------------------------------------------

    conflict: str = "NONE"

    # -----------------------------------------------------
    # LIQUIDITY
    # -----------------------------------------------------

    liquidity_score: float = 0.0

    # -----------------------------------------------------
    # EVIDENCE
    # -----------------------------------------------------

    evidence: tuple[ConfluenceEvidence, ...] = field(
        default_factory=tuple
    )

    reasons: tuple[str, ...] = field(
        default_factory=tuple
    )