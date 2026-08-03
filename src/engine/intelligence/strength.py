"""
Shared strength utilities.
"""

from engine.models.strength import StrengthCategory


def strength_category(strength: float) -> StrengthCategory:
    """
    Convert a numeric strength score into a category.
    """

    if strength >= 80:
        return StrengthCategory.VERY_STRONG

    if strength >= 60:
        return StrengthCategory.STRONG

    if strength >= 40:
        return StrengthCategory.MODERATE

    return StrengthCategory.WEAK