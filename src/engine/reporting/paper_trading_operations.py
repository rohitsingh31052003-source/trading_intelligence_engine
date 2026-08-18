"""
Human-readable paper-trading OPERATIONS formatter (Product Phase 5 increment).

A stateless, deterministic formatter that renders a
:class:`~dashboard.paper_trade_operations.OperationsCycleResult` as a
human-readable operational audit report. It introduces NO new intelligence,
NO prediction, NO recommendation. Every value is read from the operational
result. The formatter returns a ``str`` (no ``print()`` inside the formatter).

The report STRICTLY separates the concerns the operational model keeps
separate: OPERATIONAL CYCLE, SYSTEM DECISION (reused verbatim,
AUTHORITATIVE), TRADE GEOMETRY (reused verbatim), TRADE PLAN (reused
verbatim from Product Phase 4), and PAPER-TRADE RESULT (a SEPARATE concern
from the system decision). It explicitly distinguishes the system decision
from the paper-trade result so a ``QUALIFIED`` decision that resulted in a
``LOSS`` is NEVER re-classified.

Every report ends with the explicit disclaimer that this system performs
paper trading only, no real orders are placed, and paper-trade results are
observational validation that do not guarantee future performance or
constitute financial advice. No predictive / probability /
buy-sell-enter-exit-hold / statistical-significance language is used.

Following the 11O-12E reporting convention, the formatter is imported via
full path (NOT re-exported from ``reporting/__init__.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


# The fixed disclaimer appended to every operational report. Duplicated here
# (rather than imported from the dashboard) so the reporting layer keeps NO
# dependency on the dashboard package (the architectural invariant
# `models <- intelligence <- pipeline` with reporting ABOVE models and BELOW
# the dashboard must be preserved). The dashboard's
# `OPERATIONS_DISCLAIMER` is the authoritative source; this constant is kept
# byte-identical to it.
OPERATIONS_DISCLAIMER = (
    "This system performs paper trading only. No real orders are placed. "
    "Paper-trade results are observational validation and do not guarantee "
    "future performance or constitute financial advice."
)


def _ts(value: datetime | None) -> str:
    return "unavailable" if value is None else value.isoformat()


class OperationalReportFormatter:
    """
    Format an operational cycle result as a human-readable report.

    DUCK-TYPED: the formatter accepts either the dashboard
    ``OperationsCycleResult`` / ``OperationsCycleView`` (or any object
    exposing the same attribute surface) so the reporting layer keeps NO
    dependency on the dashboard package. It reads attributes defensively
    via ``getattr``.

    Stateless and deterministic. Returns ``str``. No ``print()``. No
    predictive language. The disclaimer is always appended.
    """

    def __init__(self, width: int = 60) -> None:
        if width < 20:
            raise ValueError("width must be >= 20")
        self.width = width

    def format(self, result: Any) -> str:
        lines: list[str] = []
        bar = "=" * self.width
        lines.append(bar)
        lines.append("PAPER TRADING OPERATIONS REPORT")
        lines.append(bar)
        lines.append("")
        lines.append("Operational Cycle")
        lines.append("-----------------")
        lines.append(f"  Cycle ID:              {getattr(result, 'cycle_id', '')}")
        status = getattr(result, 'status', '')
        status_val = getattr(status, 'value', status) or ''
        lines.append(f"  Status:                {status_val}")
        lines.append(f"  Started At:            {_ts(getattr(result, 'started_at', None))}")
        lines.append(f"  Completed At:          {_ts(getattr(result, 'completed_at', None))}")
        lines.append(f"  Reference Now:         {_ts(getattr(result, 'reference_now', None))}")
        lines.append(f"  Provider:              {getattr(result, 'provider', '') or 'unavailable'}")
        lines.append(f"  Freshness:             {getattr(result, 'freshness', '') or 'unavailable'}")
        lines.append(f"  Instruments Scanned:   {getattr(result, 'instruments_scanned', 0)}")
        lines.append(f"  Instruments Analysed:  {getattr(result, 'instruments_analysed', 0)}")
        lines.append(f"  Trades Created:        {getattr(result, 'trades_created', 0)}")
        lines.append(f"  Trades Updated:        {getattr(result, 'trades_updated', 0)}")
        lines.append(f"  Trades Closed:         {getattr(result, 'trades_closed', 0)}")
        lines.append(f"  Duplicates Skipped:    {getattr(result, 'duplicates_skipped', 0)}")
        lines.append(f"  Active Trades:         {getattr(result, 'active_trades', 0)}")
        errors = tuple(getattr(result, 'errors', ()) or ())
        lines.append(f"  Errors:                {len(errors)}")
        lines.append("")

        if errors:
            lines.append("Errors (failure-isolated)")
            lines.append("------------------------")
            for err in errors:
                lines.append(f"  - {err}")
            lines.append("")

        warnings = tuple(getattr(result, 'warnings', ()) or ())
        if warnings:
            lines.append("Warnings")
            lines.append("--------")
            for w in warnings:
                lines.append(f"  - {w}")
            lines.append("")

        lines.append("Per-Instrument Outcomes")
        lines.append("-----------------------")
        results = tuple(getattr(result, 'results', ()) or ())
        if not results:
            lines.append("  (no instruments processed)")
        else:
            for r in results:
                lines.extend(self._format_instrument(r))
        lines.append("")

        lines.append("Rationale")
        lines.append("---------")
        lines.append(f"  {getattr(result, 'rationale', '')}")
        lines.append("")

        lines.append("Limitations")
        lines.append("-----------")
        lines.append(f"  {getattr(result, 'limitations', '')}")
        lines.append("")
        lines.append(bar)
        lines.append(f"DISCLAIMER: {OPERATIONS_DISCLAIMER}")
        lines.append(bar)
        return "\n".join(lines)

    def _format_instrument(self, r: Any) -> list[str]:
        out: list[str] = []
        out.append(f"  {getattr(r, 'instrument', '')}")
        out.append(f"    Analysed:               {getattr(r, 'analysed', False)}")
        out.append(
            f"    System Decision:        {getattr(r, 'decision_classification', '') or 'unavailable'}",
        )
        out.append(f"    Actionability:          {getattr(r, 'actionability', '') or 'unavailable'}")
        out.append(
            f"    Eligible For Paper:     {getattr(r, 'eligible_for_paper_trade', False)}",
        )
        out.append(f"    Direction:              {getattr(r, 'direction', '') or 'unavailable'}")
        out.append(
            f"    Evaluation Timestamp:   {_ts(getattr(r, 'evaluation_timestamp', None))}",
        )
        out.append(f"    Provider Status:        {getattr(r, 'provider_status', '') or 'unavailable'}")
        out.append(f"    Freshness State:        {getattr(r, 'freshness_state', '') or 'unavailable'}")
        created = tuple(getattr(r, 'created', ()) or ())
        updated = tuple(getattr(r, 'updated', ()) or ())
        closed = tuple(getattr(r, 'closed', ()) or ())
        out.append(f"    Trades Created:         {len(created)}")
        for cid in created:
            out.append(f"      - {cid}")
        out.append(f"    Trades Updated:         {len(updated)}")
        for uid in updated:
            out.append(f"      - {uid}")
        out.append(f"    Trades Closed:          {len(closed)}")
        for cid in closed:
            out.append(f"      - {cid}")
        out.append(f"    Duplicate Skipped:      {getattr(r, 'duplicate', False)}")
        dup_id = getattr(r, 'duplicate_paper_trade_id', '') or ''
        if dup_id:
            out.append(f"      existing: {dup_id}")
        out.append(f"    Error:                  {getattr(r, 'error', False)}")
        reason = getattr(r, 'reason', '') or ''
        if reason:
            out.append(f"    Reason:                 {reason}")
        return out


__all__ = ["OPERATIONS_DISCLAIMER", "OperationalReportFormatter"]
