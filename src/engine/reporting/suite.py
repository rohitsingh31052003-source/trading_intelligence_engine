"""
Experiment suite report formatters (Sprint 11M).

Produces deterministic, human-readable reports from the suite-layer
results (a single ``SuiteResult`` and a ``SuiteComparison``). The
formatters read the reused, already-computed values and present them;
they recompute nothing.

Design rules:

* No misleading claims. Descriptive suite-level leaders are shown ONLY
  among suites / members with SUFFICIENT evidence; insufficient
  evidence is reported explicitly. No statement that historical results
  predict future performance.

* No print() inside the formatters; each ``format`` method returns a
  string.

* Stateless and deterministic: identical inputs produce identical
  output.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any, Sequence

from engine.models.suite import SuiteComparison, SuiteResult


# ============================================================
# SHARED FORMAT HELPERS
# ============================================================


def _fmt(value: Any, suffix: str = "", precision: str = ".4f") -> str:
    """Format a numeric value, returning ``N/A`` for non-numeric."""

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
# SUITE REPORT FORMATTER
# ============================================================


class SuiteReportFormatter:
    """
    Format a :class:`SuiteResult` into a readable report string.

    Public API:

        format(result) -> str

    The formatter is stateless and deterministic. It makes NO claims
    beyond what the underlying suite summary supports; insufficient
    evidence is reported explicitly.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    # ========================================================
    # PUBLIC API
    # ========================================================

    def format(self, result: SuiteResult) -> str:
        """Produce the full suite report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Suite Report (Sprint 11M)")
        lines.append(self.SEPARATOR)

        self._identity(result, lines)
        self._members(result, lines)
        self._summary(result, lines)
        self._reproducibility(result, lines)
        self._conclusions(result, lines)

        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    # -----------------------------------------------------
    # SECTIONS
    # -----------------------------------------------------

    def _identity(self, result: SuiteResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Suite Identity")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Suite ID            : {result.suite_id}")
        lines.append(f"Label               : {result.label}")
        lines.append(f"Member Count        : {result.member_count}")

        config = result.config
        if config is not None:
            lines.append(
                f"Configuration Hash  : {config.configuration_hash}"
            )

    def _members(self, result: SuiteResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Member Experiments")
        lines.append(self.SUB_SEPARATOR)

        if not result.members:
            lines.append("(no members)")
            return

        for index, member in enumerate(result.members):
            lines.append(
                f"  [{index}] {member.experiment_id} "
                f"({member.label})"
            )
            lines.append(
                f"        Dataset      : {member.dataset.name}"
            )
            lines.append(
                f"        Evidence     : "
                f"{member.summary.evidence_status.value}"
            )
            lines.append(
                f"        Trades       : "
                f"{member.summary.completed_trades}"
            )
            lines.append(
                f"        Expectancy   : "
                f"{_fmt(member.summary.expectancy, 'R')}"
            )

    def _summary(self, result: SuiteResult, lines: list[str]) -> None:
        summary = result.summary

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Suite Summary")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Member Count        : {summary.member_count}")
        lines.append(f"Sufficient Evidence : {summary.sufficient_count}")
        lines.append(f"Partial Evidence    : {summary.partial_count}")
        lines.append(
            f"Insufficient Evidence: {summary.insufficient_count}"
        )
        lines.append(
            f"Has Sufficient      : "
            f"{'YES' if summary.has_sufficient_evidence else 'NO'}"
        )

        analysis = summary.analysis_summary
        if analysis is not None:
            lines.append("")
            lines.append("Descriptive Leaders (SUFFICIENT members only)")
            self._leader_block(
                "Highest member expectancy",
                analysis.best_by_expectancy,
                "R",
                lines,
            )
            self._leader_block(
                "Highest member total R",
                analysis.best_by_total_r,
                "R",
                lines,
            )
            self._leader_block(
                "Lowest member max drawdown",
                analysis.lowest_drawdown,
                "R",
                lines,
            )
            if not summary.has_sufficient_evidence:
                lines.append(
                    "No descriptive leader declared: no member has "
                    "SUFFICIENT evidence."
                )

    def _reproducibility(
        self,
        result: SuiteResult,
        lines: list[str],
    ) -> None:
        repro = result.reproducibility

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Reproducibility")
        lines.append(self.SUB_SEPARATOR)
        lines.append(
            f"Reproducible        : {_bool_text(repro.reproducible)}"
        )
        lines.append(f"Code Version        : {repro.code_version}")
        lines.append(
            f"Member Experiment IDs: {repro.member_count}"
        )
        for member_id in repro.member_experiment_ids:
            lines.append(f"  - {member_id}")

    def _conclusions(self, result: SuiteResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Conclusions")
        lines.append(self.SUB_SEPARATOR)

        conclusions = result.summary.conclusions
        if conclusions:
            for conclusion in conclusions:
                lines.append(f"- {conclusion}")
        else:
            lines.append("- No conclusions derived.")

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


# ============================================================
# SUITE COMPARISON FORMATTER
# ============================================================


class SuiteComparisonFormatter:
    """
    Format a :class:`SuiteComparison` into a readable report string.

    Public API:

        format(comparison) -> str

    The formatter never declares a "best" suite unless the comparison
    did so (and only among suites with at least one SUFFICIENT member).
    Insufficient evidence is shown explicitly.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, comparison: SuiteComparison) -> str:
        """Produce the full suite comparison report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Suite Comparison Report (Sprint 11M)")
        lines.append(self.SEPARATOR)

        lines.append("")
        lines.append(f"Compared suites: {comparison.suite_count}")

        if comparison.is_empty:
            lines.append("No suites supplied for comparison.")
            lines.append("")
            lines.append(self.SEPARATOR)
            return "\n".join(lines)

        lines.append(self.SUB_SEPARATOR)
        lines.append("Suite Comparison Table")
        lines.append(self.SUB_SEPARATOR)
        self._table(comparison, lines)

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Descriptive Ranking (SUFFICIENT members only)")
        lines.append(self.SUB_SEPARATOR)

        if comparison.best_suite_by_member_expectancy is not None:
            lines.append(
                f"Suite with highest member expectancy: "
                f"{comparison.best_suite_by_member_expectancy}"
            )
        else:
            lines.append(
                "No descriptive best suite declared: no suite has a "
                "SUFFICIENT member."
            )

        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Conclusions")
        lines.append(self.SUB_SEPARATOR)

        if comparison.conclusions:
            for conclusion in comparison.conclusions:
                lines.append(f"- {conclusion}")
        else:
            lines.append("- No conclusions derived.")

        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    @staticmethod
    def _table(
        comparison: SuiteComparison,
        lines: list[str],
    ) -> None:
        lines.append(
            f"  {'Suite ID':<20} {'Members':>7} {'Suff':>5} "
            f"{'Part':>5} {'Insuff':>7} {'Best Member Expectancy'}"
        )
        for row in comparison.rows:
            best_member = row.best_member_by_expectancy or "(none)"
            lines.append(
                f"  {row.suite_id:<20} {row.member_count:>7} "
                f"{row.sufficient_count:>5} {row.partial_count:>5} "
                f"{row.insufficient_count:>7} {best_member}"
            )


__all__ = [
    "SuiteComparisonFormatter",
    "SuiteReportFormatter",
]
