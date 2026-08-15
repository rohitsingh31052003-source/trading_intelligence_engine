"""
Historical performance report formatter (Sprint 11X).

:class:`PerformanceReportFormatter` renders a
:class:`~engine.models.historical_performance.HistoricalPerformanceAnalytics`
result as a plain-text, analyst-style report so a human can understand
how the opportunities historically produced by the engine performed,
without inspecting internal Python objects.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. It
reports aggregate measurements of already-evaluated historical
outcomes (Sprint 11W). Unavailable metrics are shown as ``"unavailable"``
— never fabricated.

Every report ends with the explicit warning that historical performance
results are descriptive technical-analysis outputs and are NOT
predictive signals or guarantees of profitability.

Following the Sprint 11O-11W reporting convention, this formatter is
imported via its full path
(``from engine.reporting.performance import PerformanceReportFormatter``)
and is NOT re-exported from ``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
    HistoricalPerformanceBreakdown,
    HistoricalPerformanceGroup,
    HistoricalPerformanceStatistics,
)


_WARNING = (
    "Historical performance results are descriptive technical-analysis "
    "outputs and are NOT predictive signals or guarantees of "
    "profitability."
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


def _dimension_title(dimension: BreakdownDimension) -> str:
    return {
        BreakdownDimension.INSTRUMENT: "Performance by Instrument",
        BreakdownDimension.DIRECTION: "Performance by Direction",
        BreakdownDimension.MTF_ALIGNMENT: "Performance by MTF Alignment",
        BreakdownDimension.SETUP_TYPE: "Performance by Setup Type",
        BreakdownDimension.DECISION: "Performance by Decision",
        BreakdownDimension.OPPORTUNITY_STATUS: "Performance by Opportunity Status",
        BreakdownDimension.OPPORTUNITY_RANK: "Performance by Opportunity Rank",
    }[dimension]


class PerformanceReportFormatter:
    """
    Format a :class:`HistoricalPerformanceAnalytics` as a plain-text
    analyst-style historical performance report.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, analytics: HistoricalPerformanceAnalytics) -> str:
        """Format the full historical performance report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Historical Performance Report")
        lines.append("=" * 60)
        lines.append(f"Analytics ID : {analytics.analytics_id}")
        if analytics.label:
            lines.append(f"Label        : {analytics.label}")
        if analytics.metadata:
            for k, v in analytics.metadata:
                lines.append(f"Metadata     : {k} = {v}")
        lines.append("")

        # Overall.
        lines.append("-" * 60)
        lines.append("Overall Performance")
        lines.append("-" * 60)
        lines.extend(self._overall_lines(analytics.overall, p))
        lines.append("")

        # Breakdowns.
        for breakdown in analytics.breakdowns:
            lines.append("-" * 60)
            lines.append(_dimension_title(breakdown.dimension))
            lines.append("-" * 60)
            lines.extend(self._breakdown_lines(breakdown, p))
            lines.append("")

        # Rationale.
        lines.append("-" * 60)
        lines.append("Rationale")
        lines.append("-" * 60)
        lines.append(analytics.rationale or "No rationale recorded.")
        lines.append("")
        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _overall_lines(
        self, s: HistoricalPerformanceStatistics, p: int,
    ) -> list[str]:
        return [
            f"Opportunities Evaluated : {_int_or_na(s.total)}",
            f"Valid/Resolved          : {_int_or_na(s.resolved)}",
            "",
            f"TARGET_HIT              : {_int_or_na(s.target_hits)}",
            f"STOP_HIT                : {_int_or_na(s.stop_hits)}",
            f"EXPIRED                 : {_int_or_na(s.expired)}",
            f"BOTH_TOUCHED            : {_int_or_na(s.both_touched)}",
            f"NO_GEOMETRY             : {_int_or_na(s.no_geometry)}",
            f"INSUFFICIENT_DATA       : {_int_or_na(s.insufficient_data)}",
            "",
            f"Win Rate                : {_rate(s.win_rate, p)}",
            f"Loss Rate               : {_rate(s.loss_rate, p)}",
            f"Expiration Rate         : {_rate(s.expiration_rate, p)}",
            f"Ambiguous Rate          : {_rate(s.ambiguous_rate, p)}",
            "",
            f"Total Realized R        : {_r(s.total_realized_r, p)}",
            f"Average Realized R      : {_r(s.average_realized_r, p)}",
            f"Median Realized R       : {_r(s.median_realized_r, p)}",
            f"Valid R Observations    : {_int_or_na(s.valid_r_count)}",
            "",
            f"Gross Positive R        : {_r(s.gross_positive_r, p)}",
            f"Gross Negative R        : {_r(s.gross_negative_r, p)}",
            f"Profit Factor           : {_rate(s.profit_factor, p)}",
            "",
            f"Average MFE             : {_price(s.average_mfe, p)}",
            f"Average MAE             : {_price(s.average_mae, p)}",
            f"Average MFE (R)         : {_r(s.average_mfe_r, p)}",
            f"Average MAE (R)         : {_r(s.average_mae_r, p)}",
        ]

    def _breakdown_lines(
        self, breakdown: HistoricalPerformanceBreakdown, p: int,
    ) -> list[str]:
        if not breakdown.groups:
            return ["No outcomes in any group."]
        lines: list[str] = []
        for group in breakdown.groups:
            lines.extend(self._group_lines(group, p))
            lines.append("")
        # Drop the trailing blank line for a tidy section.
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def _group_lines(
        self, group: HistoricalPerformanceGroup, p: int,
    ) -> list[str]:
        s = group.statistics
        return [
            f"[{_key_label(group.key)}]",
            f"  Count            : {_int_or_na(s.total)}",
            f"  Target Hits      : {_int_or_na(s.target_hits)}",
            f"  Stop Hits        : {_int_or_na(s.stop_hits)}",
            f"  Expired          : {_int_or_na(s.expired)}",
            f"  Both Touched     : {_int_or_na(s.both_touched)}",
            f"  No Geometry      : {_int_or_na(s.no_geometry)}",
            f"  Win Rate         : {_rate(s.win_rate, p)}",
            f"  Average Realized R: {_r(s.average_realized_r, p)}",
            f"  Median Realized R : {_r(s.median_realized_r, p)}",
            f"  Total Realized R  : {_r(s.total_realized_r, p)}",
            f"  Average MFE       : {_price(s.average_mfe, p)}",
            f"  Average MAE       : {_price(s.average_mae, p)}",
            f"  Average MFE (R)   : {_r(s.average_mfe_r, p)}",
            f"  Average MAE (R)   : {_r(s.average_mae_r, p)}",
        ]


__all__ = ["PerformanceReportFormatter"]
