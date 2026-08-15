"""
Configuration for the historical performance analytics layer
(Sprint 11X).

The config is deliberately MINIMAL. Sprint 11X is an analytics /
measurement layer, NOT a strategy optimizer. It carries no strategy /
optimization parameters — only an optional label and metadata for
traceability, plus an optional descriptive rounding precision used
solely for human-readable reporting (never for computation; the
underlying statistics retain full precision).

The analytics layer is DESCRIPTIVE. It aggregates already-computed
historical outcomes (Sprint 11W). It is NOT a prediction, NOT a
probability of success, NOT a profitability prediction, and NOT a
trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceAnalyticsConfig:
    """
    Minimal configuration for the historical performance analytics
    engine.

    Attributes:

    label
        Optional descriptive label identifying the analytics run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.

    rounding_precision
        Optional decimal precision used ONLY by the report formatter
        when rendering float metrics for human readability. The
        underlying statistics retain full precision. ``None`` disables
        rounding in the formatter (raw repr). Must be non-negative
        when set.
    """

    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    rounding_precision: int | None = 2

    def __post_init__(self) -> None:
        if self.rounding_precision is not None and self.rounding_precision < 0:
            raise ValueError(
                "rounding_precision must be non-negative when set.",
            )


__all__ = ["PerformanceAnalyticsConfig"]
