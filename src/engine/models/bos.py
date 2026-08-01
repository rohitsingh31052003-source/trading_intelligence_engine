"""
Break of Structure domain models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.swing import SwingPoint


class BOSType(Enum):
    """
    Direction of a confirmed Break of Structure.
    """

    NONE = "NONE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(slots=True, frozen=True)
class BOSResult:
    """
    Result returned by the BOSEngine.
    """

    detected: bool

    bos_type: BOSType

    protected_swing: SwingPoint | None

    trigger_swing: SwingPoint | None

    confidence: float = 0.0

    reasons: list[str] = field(default_factory=list)