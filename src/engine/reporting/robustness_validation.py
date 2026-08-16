"""
Robustness validation report formatter (Sprint 12D).

:class:`RobustnessValidationFormatter` renders the Sprint 12D
:class:`~engine.models.robustness_validation.RobustnessValidationReport`
as a plain-text, analyst-style validation report so a human can
understand whether the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A
-> 12B -> 12C architecture remained correct and safe under difficult
boundary conditions, malformed-but-representable inputs, empty data,
partial data, serialization edge cases, unusual cohort structures,
deterministic replay variations and failure isolation.

The report distinguishes clearly between:

* the VALIDATION STATUS (overall + per-category),
* the SCENARIO COUNT + CHECK COUNTS (passed / failed / skipped /
  unavailable / invalid),
* the EDGE-CASE COVERAGE,
* the OUTCOME DISTRIBUTION,
* the DETERMINISM check result,
* the LOOK-AHEAD check result,
* the ACCOUNTING INVARIANTS result,
* the SERIALIZATION ADVERSARIAL result,
* the INTEGRATION ISOLATION result,
* the CROSS-LAYER CONSISTENCY result,
* the PIPELINE REGRESSION result,
* the per-scenario check detail.

Every report ends with the explicit limitation:

    "Robustness validation verifies implementation invariants and
    historical accounting behavior; it does not establish predictive
    validity, statistical significance, or future profitability."

No predictive, profitability, probability, statistical-significance,
buy / sell / enter / exit / hold, or trading-recommendation language is
used. Skipped / unavailable / invalid checks are shown explicitly and
are NEVER counted as PASS.

Following the Sprint 11O-12C reporting convention, this formatter is
imported via its full path (``from
engine.reporting.robustness_validation import
RobustnessValidationFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.robustness_validation import (
    RobustnessScenarioResult,
    RobustnessValidationReport,
)


def _status_label(status: object) -> str:
    return getattr(status, "name", str(status))


class RobustnessValidationFormatter:
    """
    Format Sprint 12D robustness validation reports as plain-text,
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

    def format(self, report: RobustnessValidationReport) -> str:
        """Format a :class:`RobustnessValidationReport` as a report."""

        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("Robustness Validation Report")
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
            f"{report.skipped_count} skipped/unavailable/invalid)",
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
            f"Integration Isolation : {_status_label(report.integration_status)}",
        )
        lines.append(
            f"Cross-Layer           : {_status_label(report.cross_layer_status)}",
        )
        lines.append(
            f"Pipeline Regression   : {_status_label(report.pipeline_regression_status)}",
        )
        lines.append("")

        # Edge-case coverage.
        lines.append("-" * w)
        lines.append("Edge-Case Coverage")
        lines.append("-" * w)
        if report.edge_case_coverage:
            for tag in report.edge_case_coverage:
                lines.append(f"  - {tag}")
        else:
            lines.append("no edge-case tags")
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
                    f"{cat.category.name:<30}: "
                    f"{cat.total} total ({cat.passed} pass / "
                    f"{cat.failed} fail / {cat.skipped} skip) -> "
                    f"{'PASS' if cat.passed_category else 'FAIL/SKIP'}",
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

        # Report-level checks.
        lines.append("-" * w)
        lines.append("Report-Level Checks")
        lines.append("-" * w)
        if report.report_checks:
            for c in report.report_checks:
                lines.append(
                    f"  {_status_label(c.status):<12} {c.category.name:<30} "
                    f"{c.name}",
                )
                if c.detail:
                    detail = c.detail
                    if len(detail) > 80:
                        detail = detail[:77] + "..."
                    lines.append(f"        {detail}")
        else:
            lines.append("no report-level checks")
        lines.append("")

        # Rationale.
        lines.append("-" * w)
        lines.append("Rationale")
        lines.append("-" * w)
        lines.append(report.rationale or "(no rationale)")
        lines.append("")

        # Limitations.
        lines.append("-" * w)
        lines.append("Limitations")
        lines.append("-" * w)
        lines.append(report.limitations or "(no limitations)")

        lines.append("")
        lines.append("=" * w)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _scenario_lines(self, scenario: RobustnessScenarioResult) -> list[str]:
        lines: list[str] = []
        tag = f" [{scenario.edge_case}]" if scenario.edge_case else ""
        lines.append(
            f"[{scenario.name}] outcomes={scenario.outcome_count}{tag} "
            f"-> {'PASS' if scenario.passed else 'FAIL/SKIP'}",
        )
        for c in scenario.checks:
            lines.append(
                f"  {_status_label(c.status):<12} {c.category.name:<30} "
                f"{c.name}",
            )
            if c.detail:
                detail = c.detail
                if len(detail) > 80:
                    detail = detail[:77] + "..."
                lines.append(f"        {detail}")
        return lines


__all__ = ["RobustnessValidationFormatter"]
