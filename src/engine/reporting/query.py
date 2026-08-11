"""
Experiment query / analysis report formatters (Sprint 11L).

Produces deterministic, human-readable reports from the query-layer
results (query rows, groupings, analysis summaries). The
formatters read the reused, already-computed values and present
them; they recompute nothing.

Design rules:

* No misleading claims. Descriptive leaders are shown ONLY among
  experiments with SUFFICIENT evidence; insufficient evidence is
  reported explicitly. No statement that historical results
  predict future performance.

* No print() inside the formatters; each ``format`` method returns
  a string.

* Stateless and deterministic: identical inputs produce identical
  output.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any, Sequence

from engine.models.query import (
    ExperimentAnalysisSummary,
    ExperimentGrouping,
    ExperimentQueryRow,
)


# ============================================================
# SHARED FORMAT HELPER
# ============================================================


def _fmt(value: Any, suffix: str = "", precision: str = ".4f") -> str:
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


def _bool_text(value: bool | None) -> str:
    if value is None:
        return "N/A"
    return "YES" if value else "NO"


# ============================================================
# QUERY ROW FORMATTER
# ============================================================


class ExperimentQueryFormatter:
    """
    Format a sequence of :class:`ExperimentQueryRow` into a
    readable report.

    Public API:

        format(rows) -> str

    Stateless and deterministic.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, rows: Sequence[ExperimentQueryRow]) -> str:
        """Produce the full query report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Query Results (Sprint 11L)")
        lines.append(self.SEPARATOR)

        if not rows:
            lines.append("")
            lines.append("No persisted experiments matched the query.")
            lines.append("")
            lines.append(self.SEPARATOR)
            return "\n".join(lines)

        lines.append("")
        lines.append(f"Matched experiments: {len(rows)}")
        lines.append(self.SUB_SEPARATOR)

        for row in rows:
            self._row_block(row, lines)

        lines.append("")
        lines.append("All values are descriptive, not predictive.")
        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    def _row_block(
        self,
        row: ExperimentQueryRow,
        lines: list[str],
    ) -> None:
        lines.append(f"[{row.experiment_id}] {row.label}")
        lines.append(f"  Dataset            : {row.dataset_name}")
        lines.append(
            f"  Evidence Status    : {row.evidence_status.value}"
        )
        lines.append(f"  Completed Trades   : {row.completed_trades}")
        lines.append(f"  Win Rate           : {_fmt(row.win_rate, '%')}")
        lines.append(f"  Expectancy         : {_fmt(row.expectancy, 'R')}")
        lines.append(f"  Total R            : {_fmt(row.total_r, 'R')}")
        lines.append(f"  Profit Factor      : {_fmt(row.profit_factor)}")
        lines.append(
            f"  Max Drawdown       : {_fmt(row.max_drawdown_r, 'R')}"
        )
        lines.append(f"  Robust             : {_bool_text(row.robust)}")
        lines.append(
            f"  OOS Expectancy     : {_fmt(row.oos_expectancy, 'R')}"
        )
        lines.append(f"  OOS Trades         : {row.oos_trades}")
        lines.append(
            f"  Data Sufficient    : {_bool_text(row.data_sufficient)}"
        )
        lines.append(
            f"  Leakage Passed     : {_bool_text(row.leakage_passed)}"
        )
        lines.append(
            f"  Reproducible       : {_bool_text(row.reproducible)}"
        )
        params = (
            ", ".join(f"{k}={v}" for k, v in sorted(row.parameter_values.items()))
            if row.parameter_values
            else "(none)"
        )
        lines.append(f"  Parameter Values   : {params}")
        lines.append("")


# ============================================================
# GROUPING FORMATTER
# ============================================================


class ExperimentGroupingFormatter:
    """
    Format an :class:`ExperimentGrouping` into a readable report.

    Public API:

        format(grouping) -> str

    Stateless and deterministic.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, grouping: ExperimentGrouping) -> str:
        """Produce the full grouping report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Grouping Report (Sprint 11L)")
        lines.append(self.SEPARATOR)

        lines.append("")
        lines.append(f"Grouping dimension : {grouping.dimension.value}")
        lines.append(f"Total experiments  : {grouping.total_experiments}")
        lines.append(f"Groups             : {grouping.total_groups}")
        lines.append(self.SUB_SEPARATOR)

        if grouping.is_empty:
            lines.append("No groups (no persisted experiments matched).")
            lines.append("")
            lines.append(self.SEPARATOR)
            return "\n".join(lines)

        for group in grouping.groups:
            lines.append(f"Group: {group.name}")
            lines.append(f"  Experiments : {group.experiment_count}")
            lines.append(
                f"  Sufficient  : {group.sufficient_count}"
            )
            lines.append(f"  Partial     : {group.partial_count}")
            lines.append(
                f"  Insufficient: {group.insufficient_count}"
            )
            for eid in group.experiment_ids:
                lines.append(f"    - {eid}")
            lines.append("")

        lines.append(
            "Group counts are descriptive; an INSUFFICIENT group is "
            "unobserved, not zero performance."
        )
        lines.append(self.SEPARATOR)

        return "\n".join(lines)


# ============================================================
# ANALYSIS SUMMARY FORMATTER
# ============================================================


class ExperimentAnalysisFormatter:
    """
    Format an :class:`ExperimentAnalysisSummary` into a readable
    report.

    Public API:

        format(summary) -> str

    The formatter never declares a "best" experiment unless the
    summary did so (and only among experiments with SUFFICIENT
    evidence). Insufficient evidence is shown explicitly.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, summary: ExperimentAnalysisSummary) -> str:
        """Produce the full analysis summary report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Analysis Summary (Sprint 11L)")
        lines.append(self.SEPARATOR)

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Overview")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Total experiments   : {summary.total_experiments}")
        lines.append(f"Sufficient evidence : {summary.sufficient_count}")
        lines.append(f"Partial evidence    : {summary.partial_count}")
        lines.append(
            f"Insufficient evidence: {summary.insufficient_count}"
        )
        lines.append(
            f"Has Sufficient      : "
            f"{'YES' if summary.has_sufficient_evidence else 'NO'}"
        )

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Descriptive Leaders (SUFFICIENT only)")
        lines.append(self.SUB_SEPARATOR)

        self._leader_block(
            "Highest expectancy",
            summary.best_by_expectancy,
            "R",
            lines,
        )
        self._leader_block(
            "Highest total R",
            summary.best_by_total_r,
            "R",
            lines,
        )
        self._leader_block(
            "Lowest max drawdown",
            summary.lowest_drawdown,
            "R",
            lines,
        )

        if not summary.has_sufficient_evidence:
            lines.append(
                "No descriptive leader declared: no experiment has "
                "SUFFICIENT evidence."
            )

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Reproducibility")
        lines.append(self.SUB_SEPARATOR)
        lines.append(
            f"Most reproducible experiments: "
            f"{len(summary.most_reproducible_experiment_ids)}"
        )
        for eid in summary.most_reproducible_experiment_ids:
            lines.append(f"  - {eid}")
        if not summary.most_reproducible_experiment_ids:
            lines.append("  (none)")

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Coverage")
        lines.append(self.SUB_SEPARATOR)

        self._coverage_block(
            "Dataset coverage",
            summary.dataset_coverage,
            lines,
        )
        self._coverage_block(
            "Configuration coverage",
            summary.configuration_coverage,
            lines,
        )
        self._coverage_block(
            "Parameter-values coverage",
            summary.parameter_coverage,
            lines,
        )

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Conclusions")
        lines.append(self.SUB_SEPARATOR)

        if summary.conclusions:
            for c in summary.conclusions:
                lines.append(f"- {c}")
        else:
            lines.append("- No conclusions derived.")

        lines.append("")
        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    # -----------------------------------------------------
    # INTERNAL
    # -----------------------------------------------------

    @staticmethod
    def _leader_block(
        label: str,
        leader: Any,
        suffix: str,
        lines: list[str],
    ) -> None:
        if leader is None:
            lines.append(f"{label}: N/A")
            return
        lines.append(
            f"{label}: {leader.experiment_id} "
            f"({_fmt(leader.value, suffix)})"
        )

    @staticmethod
    def _coverage_block(
        label: str,
        coverage: Any,
        lines: list[str],
    ) -> None:
        lines.append(f"{label} ({len(coverage)}):")
        if not coverage:
            lines.append("  (none)")
            return
        for name, count in coverage.items():
            lines.append(f"  - {name}: {count}")


__all__ = [
    "ExperimentAnalysisFormatter",
    "ExperimentGroupingFormatter",
    "ExperimentQueryFormatter",
]
