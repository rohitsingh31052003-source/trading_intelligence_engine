"""
Strategy intelligence report formatter (Sprint 11Z).

:class:`StrategyIntelligenceFormatter` renders the Sprint 11Z strategy
intelligence results (an assessment, a cohort comparison, and an
opportunity-evidence lookup) as plain-text, analyst-style reports so a
human can understand what the historical evidence tells us about a
type of opportunity — WITHOUT inspecting internal Python objects.

The report STRICTLY distinguishes the three concerns:

* the OBSERVED historical result (reused Sprint 11X statistics),
* the EVIDENCE STRENGTH (reused Sprint 11Y strength), and
* the STRATEGY INTERPRETATION (Sprint 11Z status + interpretation /
  limitations).

Every report ends with the explicit warning that historical evidence
is descriptive and does NOT guarantee future performance. No
predictive, profitability, probability, statistical-significance, or
trading-recommendation language is used. Unavailable metrics are shown
as ``"unavailable"`` — never fabricated.

Following the Sprint 11O-11Y reporting convention, this formatter is
imported via its full path (``from engine.reporting.strategy_intelligence
import StrategyIntelligenceFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    CohortComparison,
    CohortComparisonMetric,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyEvidenceAssessment,
)


_WARNING = (
    "Historical evidence is descriptive and does not guarantee future "
    "performance."
)


def _rate(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def _r(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}R"


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


def _spec_label(spec) -> str:
    return " + ".join(_dimension_label(d) for d in spec.dimensions)


class StrategyIntelligenceFormatter:
    """
    Format Sprint 11Z strategy intelligence results as plain-text,
    analyst-style reports.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, assessment: StrategyEvidenceAssessment) -> str:
        """Format a single :class:`StrategyEvidenceAssessment`."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Strategy Intelligence / Evidence Assessment Report")
        lines.append("=" * 60)
        lines.append(f"Assessment ID : {assessment.assessment_id}")
        if assessment.label:
            lines.append(f"Label         : {assessment.label}")
        if assessment.metadata:
            for k, v in assessment.metadata:
                lines.append(f"Metadata      : {k} = {v}")
        lines.append("")

        # Cohort.
        lines.append("-" * 60)
        lines.append("Cohort")
        lines.append("-" * 60)
        lines.append(f"Spec          : {_spec_label(assessment.spec)}")
        lines.append(f"Cohort Key    : {_key_label(assessment.cohort_key)}")
        lines.append(
            f"Sample Size   : {_int_or_na(assessment.sample_count)}",
        )
        lines.append(
            f"Resolved      : {_int_or_na(assessment.resolved_count)}",
        )
        lines.append(
            f"Valid R Obs.  : {_int_or_na(assessment.valid_r_count)}",
        )
        lines.append("")

        # Observed historical result.
        lines.append("-" * 60)
        lines.append("Observed Historical Result")
        lines.append("-" * 60)
        lines.append(self._stats_block(assessment.observed_performance, p, ""))
        lines.append("")

        # Evidence strength.
        lines.append("-" * 60)
        lines.append("Evidence Strength")
        lines.append("-" * 60)
        lines.append(
            f"Evidence Strength : {assessment.evidence_strength.name}",
        )
        lines.append("")

        # Strategy interpretation.
        lines.append("-" * 60)
        lines.append("Strategy Interpretation")
        lines.append("-" * 60)
        lines.append(
            f"Assessment Status : {assessment.assessment_status.name}",
        )
        lines.append(f"Interpretation    : {assessment.interpretation}")
        lines.append("")

        # Limitations.
        lines.append("-" * 60)
        lines.append("Limitations")
        lines.append("-" * 60)
        lines.append(assessment.limitations or "No limitations recorded.")
        lines.append("")

        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    def format_comparison(self, comparison: CohortComparison) -> str:
        """Format a :class:`CohortComparison` as a side-by-side report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Cohort Comparison Report")
        lines.append("=" * 60)
        lines.append(f"Comparison ID : {comparison.comparison_id}")
        lines.append(f"Spec          : {_spec_label(comparison.spec)}")
        lines.append(f"Cohort A Key  : {_key_label(comparison.cohort_a_key)}")
        lines.append(f"Cohort B Key  : {_key_label(comparison.cohort_b_key)}")
        lines.append(
            f"Cohort A present : {comparison.cohort_a_present}",
        )
        lines.append(
            f"Cohort B present : {comparison.cohort_b_present}",
        )
        lines.append("")

        lines.append("-" * 60)
        lines.append("Metric Comparison")
        lines.append("-" * 60)
        lines.append(
            f"{'Metric':<22}{'Cohort A':>14}{'Cohort B':>14}"
            f"{'Delta':>14}",
        )
        for metric in comparison.metrics:
            lines.append(self._metric_row(metric, p))
        lines.append("")

        lines.append("-" * 60)
        lines.append("Evidence Strength Comparison")
        lines.append("-" * 60)
        sa = (
            comparison.assessment_a.evidence_strength.name
            if comparison.assessment_a is not None else "unavailable"
        )
        sb = (
            comparison.assessment_b.evidence_strength.name
            if comparison.assessment_b is not None else "unavailable"
        )
        sta = (
            comparison.assessment_a.assessment_status.name
            if comparison.assessment_a is not None else "unavailable"
        )
        stb = (
            comparison.assessment_b.assessment_status.name
            if comparison.assessment_b is not None else "unavailable"
        )
        lines.append(f"Cohort A evidence strength   : {sa}")
        lines.append(f"Cohort B evidence strength   : {sb}")
        lines.append(f"Cohort A assessment status   : {sta}")
        lines.append(f"Cohort B assessment status   : {stb}")
        lines.append("")

        lines.append("-" * 60)
        lines.append("Descriptive Notes")
        lines.append("-" * 60)
        if comparison.notes:
            for note in comparison.notes:
                lines.append(f"- {note}")
        else:
            lines.append("No descriptive notes.")
        lines.append("")

        lines.append("-" * 60)
        lines.append("Disclaimer")
        lines.append("-" * 60)
        lines.append(comparison.disclaimer or _WARNING)
        lines.append("")
        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    def format_lookup(self, lookup: OpportunityEvidenceLookup) -> str:
        """Format an :class:`OpportunityEvidenceLookup` as a report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Current Opportunity Evidence Lookup Report")
        lines.append("=" * 60)
        lines.append(f"Lookup ID     : {lookup.lookup_id}")
        lines.append(f"Match Status  : {lookup.match_status.name}")
        lines.append("")

        lines.append("-" * 60)
        lines.append("Opportunity Profile")
        lines.append("-" * 60)
        lines.extend(self._profile_lines(lookup.profile))
        lines.append("")

        lines.append("-" * 60)
        lines.append("Matched Historical Cohort")
        lines.append("-" * 60)
        if lookup.matched_cohort is None or lookup.matched_spec is None:
            lines.append("No matching historical cohort found.")
        else:
            cohort = lookup.matched_cohort
            lines.append(f"Spec          : {_spec_label(lookup.matched_spec)}")
            lines.append(f"Cohort Key    : {_key_label(cohort.key)}")
            lines.append(
                f"Sample Size   : {_int_or_na(cohort.sample_count)}",
            )
            lines.append(
                f"Resolved      : {_int_or_na(cohort.resolved_count)}",
            )
            lines.append(
                f"Valid R Obs.  : {_int_or_na(cohort.valid_r_count)}",
            )
            lines.append(
                f"Evidence Strength : {cohort.strength.name}",
            )
        lines.append("")

        lines.append("-" * 60)
        lines.append("Strategy Assessment")
        lines.append("-" * 60)
        if lookup.assessment is None:
            lines.append(
                "No strategy assessment available (no matching cohort).",
            )
        else:
            a = lookup.assessment
            lines.append(
                f"Assessment ID : {a.assessment_id}",
            )
            lines.append(
                f"Assessment Status : {a.assessment_status.name}",
            )
            lines.append(f"Evidence Strength : {a.evidence_strength.name}")
            lines.append("")
            lines.append("Observed Historical Result:")
            lines.append(self._stats_block(a.observed_performance, p, "  "))
            lines.append("")
            lines.append(f"Interpretation: {a.interpretation}")
        lines.append("")

        lines.append("-" * 60)
        lines.append("Limitations")
        lines.append("-" * 60)
        lines.append(lookup.limitations or "No limitations recorded.")
        lines.append("")

        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

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
            f"{indent}Average MFE        : {_rate(s.average_mfe, p)}",
            f"{indent}Average MAE        : {_rate(s.average_mae, p)}",
            f"{indent}Average MFE (R)    : {_r(s.average_mfe_r, p)}",
            f"{indent}Average MAE (R)    : {_r(s.average_mae_r, p)}",
        ]
        return "\n".join(lines)

    def _metric_row(
        self, metric: CohortComparisonMetric, p: int,
    ) -> str:
        va = "unavailable" if metric.value_a is None else f"{metric.value_a:.{p}f}"
        vb = "unavailable" if metric.value_b is None else f"{metric.value_b:.{p}f}"
        delta = (
            "unavailable" if metric.delta is None
            else f"{metric.delta:+.{p}f}"
        )
        return f"{metric.name:<22}{va:>14}{vb:>14}{delta:>14}"

    def _profile_lines(self, profile: OpportunityProfile) -> list[str]:
        pairs = profile.available_dimensions()
        if not pairs:
            return ["(no opportunity characteristics provided)"]
        return [f"{dim:<20} = {value}" for dim, value in pairs]


__all__ = ["StrategyIntelligenceFormatter"]
