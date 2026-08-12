"""
Selection report formatter (Sprint 11N).

Produces a deterministic, human-readable report from a
:class:`SelectionResult`. The formatter reads the reused, already-
computed values and presents them; it recomputes nothing.

Design rules:

* No misleading claims. The selected entity is shown only when one was
  promoted (and only among SUFFICIENT candidates). Insufficient evidence
  is reported explicitly. No statement that historical results predict
  future performance; no implication of live-trading readiness.

* No print() inside the formatter; ``format`` returns a string.

* Stateless and deterministic: identical inputs produce identical output.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any

from engine.models.selection import (
    SelectionResult,
    SelectionStatus,
    SelectionType,
)


# ============================================================
# FORMAT HELPERS
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


def _oos_text(expectancy: float | None, trades: int) -> str:
    if expectancy is None or trades <= 0:
        return "NONE"
    return f"{_fmt(expectancy, 'R')} / {trades} trades"


def _criteria_text(criteria: Any) -> list[str]:
    """Render the explicit selection criteria as readable lines."""

    lines: list[str] = []

    if criteria.require_evidence_status is not None:
        lines.append(
            f"  Required Evidence Status : "
            f"{criteria.require_evidence_status.value}"
        )
    if criteria.min_expectancy is not None:
        lines.append(
            f"  Minimum Expectancy        : {_fmt(criteria.min_expectancy, 'R')}"
        )
    if criteria.min_total_r is not None:
        lines.append(
            f"  Minimum Total R           : {_fmt(criteria.min_total_r, 'R')}"
        )
    if criteria.max_drawdown_r is not None:
        lines.append(
            f"  Maximum Drawdown (R)      : {_fmt(criteria.max_drawdown_r, 'R')}"
        )
    if criteria.require_robust:
        lines.append("  Require Robust            : YES")
    if criteria.require_oos_evidence:
        lines.append("  Require OOS Evidence      : YES")
    if criteria.require_reproducible:
        lines.append("  Require Reproducible      : YES")
    if criteria.min_completed_trades is not None:
        lines.append(
            f"  Minimum Completed Trades  : {criteria.min_completed_trades}"
        )

    if not lines:
        lines.append("  (no explicit criteria; SUFFICIENT evidence only)")

    return lines


# ============================================================
# SELECTION REPORT FORMATTER
# ============================================================


class SelectionReportFormatter:
    """
    Format a :class:`SelectionResult` into a readable report string.

    Public API:

        format(result) -> str

    The formatter is stateless and deterministic. It makes NO claims
    beyond what the underlying selection result supports; insufficient
    evidence is reported explicitly and the conclusion always states
    that the result is descriptive, not predictive.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    def format(self, result: SelectionResult) -> str:
        """Produce the full selection report as a string."""

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Experiment Selection Report (Sprint 11N)")
        lines.append(self.SEPARATOR)

        self._identity(result, lines)
        self._criteria(result, lines)
        self._candidates(result, lines)
        self._rejected(result, lines)
        self._selected(result, lines)
        self._conclusions(result, lines)

        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    # -----------------------------------------------------
    # SECTIONS
    # -----------------------------------------------------

    def _identity(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Selection Identity")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Selection ID    : {result.selection_id}")
        lines.append(f"Label           : {result.label}")
        lines.append(
            f"Selection Type  : {result.selection_type.value}"
        )
        lines.append(f"Evaluated Count : {result.all_evaluated}")

    def _criteria(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Selection Criteria")
        lines.append(self.SUB_SEPARATOR)
        lines.extend(_criteria_text(result.criteria))

    def _candidates(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Candidates")
        lines.append(self.SUB_SEPARATOR)

        if not result.candidates:
            lines.append("(no candidates evaluated)")
            return

        lines.append(
            f"  {'ID':<20} {'Evidence':<12} {'Expectancy':>11} "
            f"{'Total R':>9} {'MaxDD':>9} {'Robust':>7} {'OOS':<22} "
            f"{'Repro':>6} {'Elig':>5} {'Status':<12}"
        )

        for c in result.candidates:
            lines.append(
                f"  {c.entity_id:<20} {c.evidence_status.value:<12} "
                f"{_fmt(c.expectancy, 'R'):>11} "
                f"{_fmt(c.total_r, 'R'):>9} "
                f"{_fmt(c.max_drawdown_r, 'R'):>9} "
                f"{_bool_text(c.robust):>7} "
                f"{_oos_text(c.oos_expectancy, c.oos_trades):<22} "
                f"{_bool_text(c.reproducible):>6} "
                f"{'YES' if c.eligible else 'NO':>5} "
                f"{c.status.value:<12}"
            )

    def _rejected(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Rejected / Ineligible")
        lines.append(self.SUB_SEPARATOR)

        if not result.rejected:
            lines.append("(none)")
            return

        for r in result.rejected:
            evidence = (
                r.evidence_status.value
                if r.evidence_status is not None
                else "N/A"
            )
            lines.append(
                f"  {r.entity_id:<20} {r.status.value:<12} "
                f"evidence={evidence:<12}"
            )
            lines.append(f"    reason: {r.reason}")

    def _selected(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Selected Result")
        lines.append(self.SUB_SEPARATOR)

        if result.selected is None:
            lines.append("Selected: NONE")
            lines.append(
                "No eligible SUFFICIENT candidate existed; no winner "
                "was manufactured."
            )
            return

        sel = result.selected
        lines.append(f"Selected ID    : {sel.entity_id}")
        lines.append(f"Label          : {sel.label}")
        lines.append(
            f"Selection Type : {sel.selection_type.value}"
        )
        lines.append(f"Rationale      : {sel.rationale}")

    def _conclusions(self, result: SelectionResult, lines: list[str]) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Conclusion")
        lines.append(self.SUB_SEPARATOR)

        if result.conclusions:
            for conclusion in result.conclusions:
                lines.append(f"- {conclusion}")
        else:
            lines.append("- No conclusions derived.")

        # Always reiterate the descriptive-only warning explicitly so it
        # is impossible to miss, even when conclusions are empty.
        lines.append(
            "- WARNING: this selection is DESCRIPTIVE ONLY. Historical "
            "experiment selection is NOT predictive and does NOT imply "
            "live-trading readiness."
        )


__all__ = ["SelectionReportFormatter"]
