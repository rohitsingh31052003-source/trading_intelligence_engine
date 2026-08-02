from enum import Enum


class StrengthCategory(Enum):
    VERY_WEAK = "VERY_WEAK"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"


def get_strength_category(score: float) -> StrengthCategory:

    if score >= 80:
        return StrengthCategory.VERY_STRONG

    if score >= 60:
        return StrengthCategory.STRONG

    if score >= 40:
        return StrengthCategory.MODERATE

    if score >= 20:
        return StrengthCategory.WEAK

    return StrengthCategory.VERY_WEAK
