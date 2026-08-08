"""
Domain models for the Signal Generation Engine (Sprint 11C).

The Signal Engine converts a DecisionContext into a structured
trade setup. It never executes trades, connects to a broker,
or places orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.decision import (
    DecisionDirection,
    SetupQuality,
)


class SignalDirection(Enum):
    """
    Trade direction carried by a signal.

    NONE is used when no directional setup could be produced.
    """

    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class SignalState(Enum):
    """
    Lifecycle state of a signal.

    NO_SIGNAL:
        No eligible setup exists at the reference point.

    LONG:
        A valid bullish setup has been generated.

    SHORT:
        A valid bearish setup has been generated.

    INVALID:
        A setup was considered but rejected because it no
        longer satisfies the structural / risk requirements.
    """

    NO_SIGNAL = "NO_SIGNAL"
    LONG = "LONG"
    SHORT = "SHORT"
    INVALID = "INVALID"


class EntrySource(Enum):
    """
    Origin of the entry price used by the signal.

    TRIGGER_CLOSE:
        Close of the trigger candle.

    STRUCTURE_BREAK:
        A confirmed structure-break level.

    LIQUIDITY_LEVEL:
        A relevant liquidity pool price.

    SUPPLIED:
        An execution price supplied by the caller.
    """

    TRIGGER_CLOSE = "TRIGGER_CLOSE"
    STRUCTURE_BREAK = "STRUCTURE_BREAK"
    LIQUIDITY_LEVEL = "LIQUIDITY_LEVEL"
    SUPPLIED = "SUPPLIED"


@dataclass(frozen=True)
class Invalidation:
    """
    Conditions under which a generated signal is invalidated.

    At least one of ``price`` or ``condition`` is populated
    for every generated signal.
    """

    price: float | None
    condition: str


@dataclass(frozen=True)
class SignalResult:
    """
    Immutable result produced by the SignalEngine.

    The result contains the full trade setup geometry together
    with explainable quality, eligibility, invalidation and
    human-readable reasons. It does NOT contain execution
    instructions.
    """

    direction: SignalDirection

    state: SignalState

    entry_price: float | None
    entry_source: EntrySource | None

    stop_loss: float | None
    take_profit: float | None

    risk_per_unit: float
    reward_per_unit: float
    risk_reward_ratio: float

    confidence: float

    quality: SetupQuality

    eligible: bool

    invalidation: Invalidation

    decision_direction: DecisionDirection

    reasons: tuple[str, ...] = field(default_factory=tuple)
