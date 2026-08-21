"""
Reporting for the Product Phase 6E historical evidence context.

Stateless, deterministic, returns ``str`` (no ``print()``). Follows the
11O-12E + Product-Phase reporting convention: imported via full path
(NOT re-exported from ``reporting/__init__.py``).

The report is DESCRIPTIVE ONLY: it presents the historical evidence
matched for a current assessment and always carries the explicit
disclaimer that historical evidence is observational, never predictive,
and never modifies the authoritative existing decision.
"""

from __future__ import annotations

from engine.models.historical_context import (
    HistoricalContextStatus,
    HistoricalEvidenceContext,
)


class HistoricalEvidenceContextFormatter:
    """Format one Phase 6E historical evidence context as a report."""

    def __init__(self, precision: int = 2, width: int = 72) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        if width < 20:
            raise ValueError("width must be >= 20.")
        self.precision = precision
        self.width = width

    def _num(self, value: float | None) -> str:
        return (
            "unavailable"
            if value is None
            else f"{value:.{self.precision}f}"
        )

    def format(self, context: HistoricalEvidenceContext) -> str:
        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("HISTORICAL EVIDENCE CONTEXT (Product Phase 6E)")
        lines.append("=" * w)
        lines.append(f"Context ID : {context.context_id}")
        if context.label:
            lines.append(f"Label      : {context.label}")
        if context.metadata:
            lines.append(
                "Metadata   : "
                + ", ".join(f"{k}={v}" for k, v in context.metadata),
            )
        lines.append("")

        req = context.request
        lines.append("CURRENT ASSESSMENT (matching criteria at T)")
        lines.append("-" * w)
        lines.append(f"  Instrument        : {req.instrument}")
        lines.append(f"  Setup timeframe   : {req.setup_timeframe}")
        lines.append(f"  Context timeframe : {req.context_timeframe or 'none'}")
        lines.append(
            "  Evaluation time   : "
            + (
                req.evaluation_time.isoformat()
                if req.evaluation_time is not None
                else "unavailable"
            ),
        )
        lines.append(f"  Match key         : {context.match_key or req.match_key}")
        lines.append("")

        lines.append("EVIDENCE AVAILABILITY")
        lines.append("-" * w)
        lines.append(f"  Status            : {context.status.name}")
        lines.append(
            "  Evidence strength : "
            + (context.strength.name if context.strength is not None else "UNAVAILABLE"),
        )
        lines.append("")

        lines.append("OCCURRENCES")
        lines.append("-" * w)
        lines.append(f"  Comparable occurrences : {context.comparable_occurrences}")
        lines.append(f"  Completed outcomes     : {context.completed_outcomes}")
        lines.append(f"  Ambiguous outcomes     : {context.ambiguous_count}")
        lines.append(f"  Unresolved outcomes    : {context.unresolved_count}")
        lines.append("")

        lines.append("HISTORICAL OUTCOME STATISTICS")
        lines.append("-" * w)
        stats = context.statistics
        if stats is None:
            lines.append("  unavailable (no matched already-resolved outcomes)")
        else:
            lines.append(f"  Win rate             : {self._num(stats.win_rate)}")
            lines.append(
                f"  Average realized R   : {self._num(stats.average_realized_r)}",
            )
            lines.append(
                f"  Median realized R    : {self._num(stats.median_realized_r)}",
            )
            lines.append(
                f"  Profit factor        : {self._num(stats.profit_factor)}",
            )
            lines.append(f"  Average MFE          : {self._num(stats.average_mfe)}")
            lines.append(f"  Average MAE          : {self._num(stats.average_mae)}")
        lines.append("")

        lines.append("PROVENANCE")
        lines.append("-" * w)
        if context.research_ids:
            for rid in context.research_ids:
                lines.append(f"  Phase 6D research: {rid}")
        else:
            lines.append("  no Phase 6D research referenced")
        lines.append("")

        lines.append("RATIONALE")
        lines.append("-" * w)
        lines.append(f"  {context.reason or 'none'}")
        lines.append("")

        lines.append("LIMITATIONS")
        lines.append("-" * w)
        for limitation in context.limitations:
            lines.append(f"  - {limitation}")
        lines.append("")

        lines.append(
            "WARNING: Historical evidence is descriptive and observational. "
            "It is NOT a prediction, NOT a probability of success, NOT a "
            "profitability guarantee and NOT a trading recommendation. It "
            "NEVER modifies the authoritative existing decision.",
        )
        return "\n".join(lines)


__all__ = ["HistoricalEvidenceContextFormatter"]
