"""
Domain models for market structure classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from engine.models.swing import SwingPoint


class StructureType(Enum):
    """
    Classification of swing structure.
    """

    FIRST_HIGH = "FIRST_HIGH"
    FIRST_LOW = "FIRST_LOW"

    HIGHER_HIGH = "HIGHER_HIGH"
    LOWER_HIGH = "LOWER_HIGH"

    HIGHER_LOW = "HIGHER_LOW"
    LOWER_LOW = "LOWER_LOW"


@dataclass(slots=True, frozen=True)
class StructurePoint:
    """
    Market structure classification
    for a swing point.

    The entire SwingPoint is stored so that all
    associated metadata (confirmation status,
    confidence, evidence, etc.) remains available.
    """

    swing: SwingPoint

    structure: StructureType