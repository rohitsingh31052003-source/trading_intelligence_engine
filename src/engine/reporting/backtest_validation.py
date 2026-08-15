"""
Backtest validation report formatter (Sprint 12C).

:class:`BacktestValidationFormatter` renders the Sprint 12C
:class:`~engine.models.backtest_validation.BacktestValidationReport` as a
plain-text, analyst-style validation report so a human can understand
whether the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
architecture remained correct, deterministic, leak-free and
accounting-consistent under the supplied scenarios.

The report distinguishes clearly between:

* the VALIDATION STATUS (overall + per-category),
* the SCENARIO COUNT + CHECK COUNTS,
* the OUTCOME DISTRIBUTION,
* the ACCOUNTING RECONCILIATION result,
* the DETERMINISM check result,
* the LOOK-AHEAD check result,
* the SERIALIZATION check result,
* the IMMUTABILITY check result,
* the DECISION-AUTHORITY check result,
* the per-scenario check detail.

Every report ends with the explicit disclaimer:

    "Historical backtesting and robustness results are descriptive
    validation outputs. They do not predict future market behavior and
    do not guarantee profitability."

No predictive, profitability, probability, statistical-significance,
buy / sell / enter / exit / hold, or trading-recommendation language is
used. Unavailable / skipped checks are shown explicitly.

Following the Sprint 11O-12B reporting convention, this formatter is
imported via its full path (``from
engine.reporting.backtest_validation import
BacktestValidationFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.backtest_validation import (
    BacktestValidationReport,
    ScenarioResult,
    ValidationCheckStatus,
)


_DISCLAIMER = (
    "Historical backtesting and robustness results are descriptive "
    "validation outputs. They do not predict future market behavior and "
    "do not guarantee profitability."
)


def _status_label(status: ValidationCheckStatus) -> str:
    return status.name


class BacktestValidationFormatter:
    """
    Format Sprint 12C backtest validation reports as plain-text,
    analyst-style reports.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def __init__(self, width: int = 60) -> None:
        if width < 20:
            raise ValueError("width must be >= 20.")
        self.width = width

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, report: BacktestValidationReport) -> str:
        """Format a :class:`BacktestValidationReport` as a report."""

        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("Backtest Validation Report")
        lines.append("=" * w)
        lines.append(f"Validation ID         : {report.validation_id}")
        if report.label:
            lines.append(f"Label                 : {report.label}")
        if report.metadata:
            for k, v in report.metadata:
                lines.append(f"Metadata              : {k} = {v}")
        lines.append("")

        # Validation status overview.
        lines.append("-" * w)
        lines.append("Validation Status")
        lines.append("-" * w)
        lines.append(
            f"Overall Status        : {_status_label(report.overall_status)}",
        )
        lines.append(f"Scenarios             : {report.scenario_count}")
        lines.append(
            f"Checks                : {report.check_count} total "
            f"({report.passed_count} passed, {report.failed_count} failed, "
            f"{report.skipped_count} skipped)",
        )
        lines.append(
            f"Determinism           : {_status_label(report.determinism_status)}",
        )
        lines.append(
            f"Look-Ahead Protection  : {_status_label(report.look_ahead_status)}",
        )
        lines.append(
            f"Accounting            : {_status_label(report.accounting_status)}",
        )
        lines.append(
            f"Serialization         : {_status_label(report.serialization_status)}",
        )
        lines.append(
            f"Decision Authority    : {_status_label(report.decision_authority_status)}",
        )
        lines.append("")

        # Outcome distribution.
        lines.append("-" * w)
        lines.append("Outcome Distribution")
        lines.append("-" * w)
        if report.outcome_distribution:
            for name, count in report.outcome_distribution:
                lines.append(f"{name:<22}: {count}")
        else:
            lines.append("no outcomes evaluated")
        lines.append("")

        # Category summaries.
        lines.append("-" * w)
        lines.append("Category Summary")
        lines.append("-" * w)
        if report.categories:
            for cat in report.categories:
                lines.append(
                    f"{cat.category.name:<28}: "
                    f"{cat.total} total ({cat.passed} pass / "
                    f"{cat.failed} fail / {cat.skipped} skip) -> "
                    f"{'PASS' if cat.passed_category else 'FAIL'}",
                )
        else:
            lines.append("no categories")
        lines.append("")

        # Per-scenario detail.
        lines.append("-" * w)
        lines.append("Scenario Detail")
        lines.append("-" * w)
        if report.scenarios:
            for scenario in report.scenarios:
                lines.extend(self._scenario_lines(scenario))
        else:
            lines.append("no scenarios")
        lines.append("")

        # Rationale.
        lines.append("-" * w)
        lines.append("Rationale")
        lines.append("-" * w)
        lines.append(report.rationale or "(no rationale)")
        lines.append("")

        lines.append("=" * w)
        lines.append(_DISCLAIMER)
        lines.append("=" * w)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _scenario_lines(self, scenario: ScenarioResult) -> list[str]:
        lines: list[str] = []
        lines.append(
            f"[{scenario.name}] outcomes={scenario.outcome_count} "
            f"-> {'PASS' if scenario.passed else 'FAIL'}",
        )
        for c in scenario.checks:
            lines.append(
                f"  {_status_label(c.status):<6} {c.category.name:<28} "
                f"{c.name}",
            )
            if c.detail:
                # Wrap long detail lines for readability.
                detail = c.detail
                if len(detail) > 80:
                    detail = detail[:77] + "..."
                lines.append(f"        {detail}")
        return lines


__all__ = ["BacktestValidationFormatter"]
