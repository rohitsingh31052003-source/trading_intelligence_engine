"""
Configuration for the robust historical backtesting & adversarial
validation layer (Sprint 12C).

The config is intentionally MINIMAL. Sprint 12C is a VALIDATION layer,
NOT a new decision / scoring / predictive layer. It has NO strategy
parameters, NO optimization parameters, NO probability thresholds and
NO trading logic. It only carries accounting-reconciliation tolerance
plus descriptive label / metadata used to identify a validation run.

The validation layer REUSES the existing Sprint 11X / 11Y / 11Z /
12A / 12B engines verbatim; it performs INDEPENDENT CROSS-CHECKS of
their already-computed outputs (it never duplicates the trading /
outcome / analytics / evidence / decision logic). The only numeric it
introduces is a tiny floating-point tolerance used when reconciling R
aggregates, so deterministic floating-point rounding noise never
produces a spurious accounting failure.

NO PREDICTIVE CLAIMS:

The validation layer does NOT perform statistical hypothesis tests and
does NOT use terms such as "statistically significant". Validation
results are DESCRIPTIVE: they report whether the existing architecture
remains internally consistent, deterministic, leak-free and
accounting-consistent under the supplied scenarios. They do NOT predict
future market behavior and do NOT guarantee profitability.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BacktestValidationConfig:
    """
    Minimal configuration for the backtest validation engine.

    Attributes:

    accounting_tolerance
        Absolute floating-point tolerance used when reconciling R
        aggregates (total / average / median / gross positive /
        gross negative / profit factor) independently recomputed from
        the raw outcomes against the Sprint 11X reported statistics.
        Defaults to ``1e-9``. Must be non-negative. This is a numeric
        tolerance ONLY; it never weakens a count / status / evidence
        comparison (those are compared by exact equality).

    label
        Optional descriptive label identifying the validation run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    accounting_tolerance: float = 1e-9
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.accounting_tolerance < 0:
            raise ValueError("accounting_tolerance must be non-negative.")

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the config values."""

        pairs = [
            ("accounting_tolerance", str(self.accounting_tolerance)),
        ]
        return tuple(sorted(pairs))


__all__ = ["BacktestValidationConfig"]
