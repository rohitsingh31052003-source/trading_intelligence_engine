"""
Historical evidence report formatter (Sprint 11Y).

:class:`HistoricalEvidenceFormatter` renders a
:class:`~engine.models.historical_evidence.HistoricalEvidenceReport`
as a plain-text, analyst-style report so a human can understand the
strength of the available historical evidence per cohort, without
inspecting internal Python objects.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, statistical significance, or a
trading recommendation. It classifies the STRENGTH of already-computed
historical evidence (sample-size driven). Unavailable metrics are
shown as ``"unavailable"`` — never fabricated.

Every report ends with the explicit warning that historical evidence
is descriptive and does NOT guarantee future performance.

Following the Sprint 11O-11X reporting convention, this formatter is
imported via its full path
(``from engine.reporting.historical_evidence import
HistoricalEvidenceFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.historical_evidence import (
    EvidenceStrength,
    HistoricalEvidenceBreakdown,
    HistoricalEvidenceCohort,
    HistoricalEvidenceReport,
    HistoricalEvidenceSummary,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)


_WARNING = (
    "Historical evidence is descriptive and does not guarantee future "
    "performance."
)


def _rate(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def _r(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}R"


def _price(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def _int_or_na(value: int) -> str:
    return str(value)


def _key_label(key: str) -> str:
    return "unavailable" if key == "" else key


def _dimension_label(dim: BreakdownDimension) -> str:
    return {
        BreakdownDimension.INSTRUMENT: "Instrument",
        BreakdownDimension.DIRECTION: "Direction",
        BreakdownDimension.MTF_ALIGNMENT: "MTF Alignment",
        BreakdownDimension.SETUP_TYPE: "Setup Type",
        BreakdownDimension.DECISION: "Decision",
        BreakdownDimension.OPPORTUNITY_STATUS: "Opportunity Status",
        BreakdownDimension.OPPORTUNITY_RANK: "Opportunity Rank",
    }[dim]


class HistoricalEvidenceFormatter:
    """
    Format a :class:`HistoricalEvidenceReport` as a plain-text
    analyst-style historical evidence report.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, report: HistoricalEvidenceReport) -> str:
        """Format the full historical evidence report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Historical Evidence Report")
        lines.append("=" * 60)
        lines.append(f"Evidence ID : {report.evidence_id}")
        if report.label:
            lines.append(f"Label       : {report.label}")
        if report.metadata:
            for k, v in report.metadata:
                lines.append(f"Metadata    : {k} = {v}")
        if report.config_snapshot:
            lines.append("Thresholds  :")
            for k, v in report.config_snapshot:
                lines.append(f"  {k} = {v}")
        lines.append("")

        # Overall evidence summary.
        lines.append("-" * 60)
        lines.append("Overall Evidence Summary")
        lines.append("-" * 60)
        lines.extend(self._summary_lines(report.summary, p))
        lines.append("")

        # Cohort counts.
        lines.append("-" * 60)
        lines.append("Cohort Summary")
        lines.append("-" * 60)
        lines.append(
            f"Total cohorts evaluated  : {_int_or_na(report.cohort_count)}",
        )
        lines.append(
            f"Cohorts with usable ev.  : "
            f"{_int_or_na(report.sufficient_cohort_count)}",
        )
        lines.append(
            f"Insufficient cohorts     : "
            f"{_int_or_na(report.insufficient_cohort_count)}",
        )
        lines.append("")

        # Breakdowns.
        for breakdown in report.breakdowns:
            lines.append("-" * 60)
            lines.append(f"Evidence by {_breakdown_title(breakdown)}")
            lines.append("-" * 60)
            lines.extend(self._breakdown_lines(breakdown, p))
            lines.append("")

        # Rationale.
        lines.append("-" * 60)
        lines.append("Rationale")
        lines.append("-" * 60)
        lines.append(report.rationale or "No rationale recorded.")
        lines.append("")
        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _summary_lines(
        self, summary: HistoricalEvidenceSummary, p: int,
    ) -> list[str]:
        s = summary.statistics
        return [
            f"Evidence Strength     : {summary.strength.name}",
            f"Sample Size           : {_int_or_na(summary.sample_count)}",
            f"Resolved Outcomes     : {_int_or_na(summary.resolved_count)}",
            f"Valid R Observations  : {_int_or_na(summary.valid_r_count)}",
            "",
            self._stats_block(s, p, indent=""),
            "",
            f"Rationale             : {summary.rationale}",
        ]

    def _breakdown_lines(
        self, breakdown: HistoricalEvidenceBreakdown, p: int,
    ) -> list[str]:
        if not breakdown.cohorts:
            return ["No cohorts in this breakdown."]
        lines: list[str] = []
        for cohort in breakdown.cohorts:
            lines.extend(self._cohort_lines(cohort, p))
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def _cohort_lines(
        self, cohort: HistoricalEvidenceCohort, p: int,
    ) -> list[str]:
        s = cohort.statistics
        block = self._stats_block(s, p, indent="  ")
        return [
            f"[{_key_label(cohort.key)}]",
            f"  Evidence Strength : {cohort.strength.name}",
            f"  Sample Size       : {_int_or_na(cohort.sample_count)}",
            f"  Resolved Outcomes : {_int_or_na(cohort.resolved_count)}",
            f"  Valid R Obs.      : {_int_or_na(cohort.valid_r_count)}",
            "",
            block,
            "",
            f"  Rationale         : {cohort.rationale}",
        ]

    def _stats_block(
        self, s: HistoricalPerformanceStatistics, p: int, indent: str,
    ) -> str:
        """Render the reused observed-result statistics as a block."""

        lines = [
            f"{indent}TARGET_HIT         : {_int_or_na(s.target_hits)}",
            f"{indent}STOP_HIT           : {_int_or_na(s.stop_hits)}",
            f"{indent}EXPIRED            : {_int_or_na(s.expired)}",
            f"{indent}BOTH_TOUCHED       : {_int_or_na(s.both_touched)}",
            f"{indent}NO_GEOMETRY        : {_int_or_na(s.no_geometry)}",
            f"{indent}INSUFFICIENT_DATA  : {_int_or_na(s.insufficient_data)}",
            f"{indent}Win Rate           : {_rate(s.win_rate, p)}",
            f"{indent}Average Realized R : {_r(s.average_realized_r, p)}",
            f"{indent}Median Realized R  : {_r(s.median_realized_r, p)}",
            f"{indent}Total Realized R   : {_r(s.total_realized_r, p)}",
            f"{indent}Profit Factor      : {_rate(s.profit_factor, p)}",
            f"{indent}Average MFE        : {_price(s.average_mfe, p)}",
            f"{indent}Average MAE        : {_price(s.average_mae, p)}",
            f"{indent}Average MFE (R)    : {_r(s.average_mfe_r, p)}",
            f"{indent}Average MAE (R)    : {_r(s.average_mae_r, p)}",
        ]
        return "\n".join(lines)


def _breakdown_title(breakdown: HistoricalEvidenceBreakdown) -> str:
    return " + ".join(_dimension_label(d) for d in breakdown.spec.dimensions)


__all__ = ["HistoricalEvidenceFormatter"]
