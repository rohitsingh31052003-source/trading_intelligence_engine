"""
Configuration for the historical opportunity outcome evaluator
(Sprint 11W).

All evaluation-horizon parameters live here; no magic numbers are
embedded in the engine. The defaults are deliberately conservative and
deterministic. They are NOT calibrated to any market; they express an
interpretable, rule-based forward-only evaluation horizon.

The outcome layer is DESCRIPTIVE. It evaluates what price did after an
opportunity was identified, using ONLY candles that closed strictly
after the evaluation timestamp and within the configured horizon. It
is NOT a prediction, NOT a probability of success, NOT a profitability
prediction, and NOT a trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutcomeConfig:
    """
    Configuration for the forward-only historical outcome evaluator.

    Attributes:

    max_holding_bars
        The deterministic maximum evaluation horizon, expressed as the
        maximum number of forward candles (candles with a timestamp
        strictly greater than the evaluation timestamp) the evaluator
        may inspect. If neither the target nor the stop is reached
        within this horizon, the outcome is :attr:`EXPIRED
        <engine.models.historical_outcome.OutcomeStatus.EXPIRED>`.
        Must be a positive integer.

    min_history
        Unused by the evaluator directly; carried for symmetry with the
        other configs and for future auditability. Defaults to ``0``.

    label
        Optional descriptive label identifying the evaluation run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    max_holding_bars: int = 20
    min_history: int = 0
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.max_holding_bars <= 0:
            raise ValueError("max_holding_bars must be a positive integer.")
        if self.min_history < 0:
            raise ValueError("min_history must be non-negative.")


__all__ = ["OutcomeConfig"]
