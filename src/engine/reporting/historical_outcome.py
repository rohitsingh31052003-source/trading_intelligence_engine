"""
Analyst-style historical outcome report formatter (Sprint 11W).

:class:`HistoricalOutcomeFormatter` renders a
:class:`~engine.models.historical_outcome.HistoricalOutcome` (or a full
:class:`~engine.models.historical_outcome.ReplayOutcomeReport`) as a
plain-text, analyst-style report so a human can understand what price
did after an opportunity was identified, without inspecting internal
Python objects.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. It
reports the historical outcome of an opportunity the existing
intelligence pipeline identified at a point in time, evaluated
forward-only using only candles that closed strictly after the
evaluation timestamp.

Every report ends with the explicit warning that historical outcome
results are descriptive technical-analysis outputs and are NOT
predictive signals or guarantees of profitability.

Following the Sprint 11O-11V reporting convention, this formatter is
imported via its full path
(``from engine.reporting.historical_outcome import
HistoricalOutcomeFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from datetime import datetime

from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    ReplayOutcomePoint,
    ReplayOutcomeReport,
)


_WARNING = (
    "Historical outcome results are descriptive technical-analysis "
    "outputs and are NOT predictive signals or guarantees of "
    "profitability."
)


def _fmt_ts(ts: datetime | None) -> str:
    return "unavailable" if ts is None else str(ts)


def _price(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


def _r(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}R"


def _status(status: OutcomeStatus) -> str:
    return status.name


class HistoricalOutcomeFormatter:
    """
    Format a :class:`HistoricalOutcome` or
    :class:`ReplayOutcomeReport` as a plain-text analyst-style report.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def format(self, outcome: HistoricalOutcome) -> str:
        """Format a single historical outcome."""

        subject = outcome.subject
        lines: list[str] = []
        lines.append("-" * 60)
        lines.append("Historical Replay Outcome Report")
        lines.append("-" * 60)
        lines.append(f"Scan ID           : {subject.scan_id or 'unavailable'}")
        lines.append(f"Instrument        : {subject.instrument}")
        lines.append(f"Direction         : {subject.direction or 'NONE'}")
        lines.append(
            f"Evaluation Time   : {_fmt_ts(subject.evaluation_timestamp)}",
        )
        lines.append(
            f"Decision          : "
            f"{subject.decision_classification or 'unavailable'}",
        )
        lines.append(f"Score             : {subject.decision_score}")
        lines.append(
            f"Opportunity Status: "
            f"{subject.opportunity_status or 'unavailable'}",
        )
        if subject.rank > 0:
            lines.append(f"Rank              : {subject.rank}")
        lines.append("")
        lines.append(f"Entry             : {_price(subject.entry)}")
        lines.append(f"Stop              : {_price(subject.stop)}")
        lines.append(f"Target            : {_price(subject.target)}")
        if outcome.risk is not None:
            lines.append(f"Risk              : {_price(outcome.risk)}")
        lines.append("")
        lines.append(f"Outcome           : {_status(outcome.outcome_status)}")
        lines.append(f"Outcome Time      : {_fmt_ts(outcome.outcome_timestamp)}")
        lines.append(f"Exit Price        : {_price(outcome.exit_price)}")
        lines.append(f"Bars Held         : {outcome.bars_held}")
        lines.append(f"MFE               : {_price(outcome.mfe)}")
        lines.append(f"MAE               : {_price(outcome.mae)}")
        lines.append(f"MFE (R)           : {_r(outcome.mfe_r)}")
        lines.append(f"MAE (R)           : {_r(outcome.mae_r)}")
        lines.append(f"Realized R        : {_r(outcome.realized_r)}")
        lines.append("")
        lines.append("Reason:")
        lines.append(outcome.reason or "No reason recorded.")
        lines.append("")
        lines.append("NOTE: " + _WARNING)
        lines.append("-" * 60)
        return "\n".join(lines)

    def format_point(self, point: ReplayOutcomePoint) -> str:
        """Format a single replay outcome point (one evaluation time)."""

        lines: list[str] = []
        lines.append("=" * 60)
        lines.append(f"Evaluation Time: {_fmt_ts(point.evaluation_time)}")
        lines.append("=" * 60)
        if point.is_empty:
            lines.append("No eligible opportunities at this point.")
        for outcome in point.outcomes:
            lines.append(self.format(outcome))
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_report(self, report: ReplayOutcomeReport) -> str:
        """Format a full replay outcome report."""

        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Historical Replay Outcome Report")
        lines.append("=" * 60)
        lines.append(f"Report ID  : {report.report_id}")
        lines.append(f"Instruments: {', '.join(report.instruments) or 'none'}")
        ctx = report.timeframes[0] if len(report.timeframes) > 0 else ""
        setup = report.timeframes[1] if len(report.timeframes) > 1 else ""
        lines.append(f"Timeframes : {ctx} / {setup}")
        lines.append(
            f"Points     : {len(report.points)}  "
            f"Outcomes   : {report.outcome_count}",
        )
        lines.append("")
        if report.is_empty:
            lines.append("No evaluation points in this report.")
        for point in report.points:
            lines.append("")
            lines.append(self.format_point(point))
        lines.append("")
        lines.append("Rationale:")
        lines.append(report.rationale or "No rationale recorded.")
        lines.append("")
        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)


__all__ = ["HistoricalOutcomeFormatter"]
