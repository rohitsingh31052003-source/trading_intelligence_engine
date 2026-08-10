"""
Experiment comparison report formatter (Sprint 11J).

Produces a deterministic, human-readable comparison report from
an ``ExperimentComparison``. The report presents a side-by-side
table of the required metrics and the descriptive conclusions.

The formatter never declares an experiment "best" unless the
comparison itself did so (and only among experiments with
SUFFICIENT evidence). Insufficient evidence is shown explicitly.
No print() inside the formatter; it returns a string.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any

from engine.models.experiment import ExperimentComparison


class ExperimentComparisonFormatter:
    """
    Format an ``ExperimentComparison`` into a readable string.

    Public API:

        format(comparison) -> str

    The formatter is stateless and deterministic.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, comparison: ExperimentComparison) -> str:
        """
        Produce the full comparison report as a string.
        """

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Comparison Report (Sprint 11J)")
        lines.append(self.SEPARATOR)

        if comparison.is_empty:
            lines.append("")
            lines.append("No experiments were supplied for comparison.")
            lines.append("")
            lines.append(self.SEPARATOR)
            return "\n".join(lines)

        self._summary(comparison, lines)
        self._table(comparison, lines)
        self._rankings(comparison, lines)
        self._conclusions(comparison, lines)

        lines.append("")
        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    # ========================================================
    # SECTIONS
    # ========================================================

    def _summary(
        self,
        comparison: ExperimentComparison,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Comparison Summary")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Experiments Compared : {comparison.experiment_count}")
        lines.append(
            f"Sufficient Evidence  : {len(comparison.sufficient_experiments)}"
        )
        lines.append(
            f"Partial Evidence     : {len(comparison.partial_experiments)}"
        )
        lines.append(
            f"Insufficient Evidence: "
            f"{len(comparison.insufficient_experiments)}"
        )
        lines.append(
            f"Has Sufficient       : "
            f"{'YES' if comparison.has_sufficient_evidence else 'NO'}"
        )

    def _table(
        self,
        comparison: ExperimentComparison,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Side-by-Side Metrics")
        lines.append(self.SUB_SEPARATOR)

        metrics = self._metric_rows(comparison)

        for label, values in metrics:
            lines.append(f"{label}")
            for row, value in zip(comparison.rows, values):
                lines.append(
                    f"  [{row.experiment_id}] {row.label:<16} : {value}"
                )
            lines.append("")

    def _rankings(
        self,
        comparison: ExperimentComparison,
        lines: list[str],
    ) -> None:
        lines.append(self.SUB_SEPARATOR)
        lines.append("Descriptive Ranking (SUFFICIENT only)")
        lines.append(self.SUB_SEPARATOR)

        best_exp = comparison.best_by_expectancy
        best_r = comparison.best_by_total_r

        if best_exp is None and best_r is None:
            lines.append(
                "No descriptive best declared: no experiment has "
                "SUFFICIENT evidence."
            )
            return

        if best_exp is not None:
            lines.append(
                f"Highest expectancy (descriptive): {best_exp}"
            )
        if best_r is not None:
            lines.append(
                f"Highest total R (descriptive)   : {best_r}"
            )

        lines.append("Rankings are descriptive, not predictive.")

    def _conclusions(
        self,
        comparison: ExperimentComparison,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Comparison Conclusions")
        lines.append(self.SUB_SEPARATOR)

        if comparison.conclusions:
            for c in comparison.conclusions:
                lines.append(f"- {c}")
        else:
            lines.append("- No conclusions derived.")

    # ========================================================
    # METRIC ROWS
    # ========================================================

    @staticmethod
    def _metric_rows(
        comparison: ExperimentComparison,
    ) -> list[tuple[str, list[str]]]:
        """
        Build the ordered list of (metric label, per-row values).
        """

        rows: list[tuple[str, list[str]]] = []

        rows.append(
            (
                "Experiment ID",
                [r.experiment_id for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Label",
                [r.label for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Evidence Status",
                [r.evidence_status.value for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Completed Trades",
                [str(r.completed_trades) for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Win Rate",
                [_fmt(r.win_rate, "%") for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Expectancy",
                [_fmt(r.expectancy, "R") for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Total R",
                [_fmt(r.total_r, "R") for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Profit Factor",
                [_fmt(r.profit_factor) for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Max Drawdown",
                [_fmt(r.max_drawdown_r, "R") for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Robust",
                [
                    "N/A" if r.robust is None
                    else ("YES" if r.robust else "NO")
                    for r in comparison.rows
                ],
            )
        )
        rows.append(
            (
                "OOS Expectancy",
                [
                    "N/A" if r.oos_expectancy is None
                    else _fmt(r.oos_expectancy, "R")
                    for r in comparison.rows
                ],
            )
        )
        rows.append(
            (
                "OOS Trades",
                [str(r.oos_trades) for r in comparison.rows],
            )
        )
        rows.append(
            (
                "Data Sufficient",
                [
                    "YES" if r.data_sufficient else "NO"
                    for r in comparison.rows
                ],
            )
        )
        rows.append(
            (
                "Leakage Passed",
                [
                    "N/A" if r.leakage_passed is None
                    else ("YES" if r.leakage_passed else "NO")
                    for r in comparison.rows
                ],
            )
        )
        rows.append(
            (
                "Leakage NOT VERIFIED",
                [
                    "YES" if r.leakage_not_verified else "NO"
                    for r in comparison.rows
                ],
            )
        )

        return rows


def _fmt(value: Any, suffix: str = "", precision: str = ".2f") -> str:
    """
    Format a numeric value, returning ``N/A`` for non-numeric.
    """

    if value is None:
        return "N/A"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if isinf(numeric):
        return ("INF" if numeric > 0 else "-INF") + suffix

    if not isfinite(numeric):
        return "N/A"

    return f"{numeric:{precision}}{suffix}"
