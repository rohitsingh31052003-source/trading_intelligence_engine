"""
Domain models for swing analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from engine.models.swing_evidence import SwingEvidence


class SwingType(Enum):
    """Type of swing."""

    HIGH = "HIGH"
    LOW = "LOW"


class SwingStrength(Enum):
    """Relative importance of a swing."""

    WEAK = "WEAK"
    NORMAL = "NORMAL"
    STRONG = "STRONG"
    MAJOR = "MAJOR"

class SwingStatus(Enum):
    """
    Current lifecycle stage of a swing.
    """

    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
@dataclass(slots=True, frozen=True)
class SwingPoint:
    """
    Represents one confirmed swing point.
    """

    timestamp: datetime
    index: int
    price: float
    swing_type: SwingType

    confirmation_index: int
    
    confirmed: bool
    status: SwingStatus
    strength: SwingStrength = SwingStrength.NORMAL

    evidence: SwingEvidence = field(default_factory=SwingEvidence)