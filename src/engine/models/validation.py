from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationStatus(Enum):
    """
    Final outcome of signal validation.
    """

    OPEN = "OPEN"
    WIN = "WIN"
    LOSS = "LOSS"
    EXPIRED = "EXPIRED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    AMBIGUOUS = "AMBIGUOUS"


class ExitReason(Enum):
    """
    Reason why validation ended.
    """

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    EXPIRY = "EXPIRY"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    BOTH_TOUCHED = "BOTH_TOUCHED"
    NONE = "NONE"


@dataclass(frozen=True)
class ValidationResult:
    """
    Immutable result produced by SignalValidationEngine.
    """

    status: ValidationStatus
    exit_reason: ExitReason

    entry_price: float
    stop_loss: float
    take_profit: float

    entry_triggered: bool
    exit_price: float | None

    candles_evaluated: int
    duration_candles: int

    realized_r: float | None
    mfe_r: float
    mae_r: float

    validation_timestamp: Any | None = None

    reason: str = ""

    details: tuple[str, ...] = field(
        default_factory=tuple
    )