"""
Analyst-style end-to-end historical replay report formatter (Sprint 11V).

:class:`HistoricalReplayFormatter` renders a
:class:`~engine.intelligence.historical_replay.ReplayResult` (or a single
:class:`~engine.models.market_scan.MarketScanResult`) as a plain-text,
analyst-style report so a human can understand a complete market scan
without inspecting internal Python objects.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. It
identifies the strongest AVAILABLE technical trade opportunities across
the scanned instruments / timeframes at one evaluation point, derived
entirely from the EXISTING Sprint 11A-11U intelligence outputs retained
by reference on the scan result (higher / lower
:class:`~engine.models.market_context.MarketContext`, the Sprint 11S
:class:`~engine.models.trade_decision.TradeDecision` and the Sprint 11T
:class:`~engine.models.opportunity.TradeOpportunity`). No intelligence is
recomputed here.

Every report ends with the explicit warning that historical replay /
market scan results are descriptive technical-analysis outputs and are
NOT predictive signals or guarantees of profitability.

Following the Sprint 11O-11U reporting convention, this formatter is
imported via its full path
(``from engine.reporting.historical_replay import HistoricalReplayFormatter``)
and is NOT re-exported from ``reporting/__init__.py``.
"""

from __future__ import annotations

from datetime import datetime

from engine.intelligence.historical_replay import (
    ReplayEvaluationPoint,
    ReplayResult,
)
from engine.models.market_scan import (
    InstrumentScanResult,
    MarketScanResult,
)


_WARNING = (
    "Historical replay / market scan results are descriptive "
    "technical-analysis outputs and are NOT predictive signals or "
    "guarantees of profitability."
)


def _fmt_ts(ts: datetime | None) -> str:
    return "unavailable" if ts is None else str(ts)


def _rr(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


def _price(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


def _trend_state(context: object | None) -> str:
    if context is None:
        return "UNAVAILABLE"
    trend = getattr(context, "trend", None)
    if trend is None:
        return "UNAVAILABLE"
    state = getattr(trend, "state", None)
    return getattr(state, "name", "UNKNOWN") if state is not None else "UNKNOWN"


def _range_state(context: object | None) -> str:
    if context is None:
        return "UNAVAILABLE"
    rng = getattr(context, "range", None)
    if rng is None:
        return "UNAVAILABLE"
    state = getattr(rng, "state", None)
    return getattr(state, "name", "UNKNOWN") if state is not None else "UNKNOWN"


def _structure_summary(context: object | None) -> str:
    """Compact summary of the most recent confirmed structure labels."""

    if context is None:
        return "unavailable"
    recent = getattr(context, "recent_structure", None)
    if not recent:
        return "unavailable"
    labels = []
    for sp in recent:
        structure = getattr(sp, "structure", None)
        labels.append(getattr(structure, "name", "?") if structure else "?")
    return ", ".join(labels)


def _support_resistance(context: object | None) -> str:
    if context is None:
        return "unavailable / unavailable"
    sr = getattr(context, "support_resistance", None)
    if sr is None:
        return "unavailable / unavailable"
    support = getattr(sr, "support", None)
    resistance = getattr(sr, "resistance", None)
    return f"{_price(support)} / {_price(resistance)}"


def _candidate_geometry(result: InstrumentScanResult) -> str:
    """Entry / stop / target / R:R from the retained candidate/decision."""

    opportunity = result.opportunity
    decision = getattr(result, "decision", None) or getattr(
        opportunity, "decision", None,
    )
    candidate = getattr(decision, "candidate", None)
    if candidate is None:
        return (
            f"Entry: unavailable | Stop: unavailable | Target: unavailable "
            f"| R:R: {_rr(result.risk_reward_ratio)}"
        )
    entry = getattr(candidate, "entry_reference", None)
    stop = getattr(candidate, "stop_reference", None)
    target = getattr(candidate, "target_reference", None)
    return (
        f"Entry: {_price(entry)} | Stop: {_price(stop)} | "
        f"Target: {_price(target)} | R:R: {_rr(result.risk_reward_ratio)}"
    )


class HistoricalReplayFormatter:
    """
    Render a descriptive historical replay result as analyst-style text.
    """

    # ============================================================
    # REPLAY-LEVEL REPORT
    # ============================================================

    def format(self, result: ReplayResult) -> str:
        """Render a complete :class:`ReplayResult`."""

        lines: list[str] = [
            "============================================================",
            "Historical Market Replay — Analyst Report",
            "============================================================",
            f"Replay ID       : {result.replay_id or 'unavailable'}",
            f"Instruments     : {', '.join(result.instruments) or 'none'}",
            f"Timeframes      : {result.timeframes[0]} / "
            f"{result.timeframes[1]}",
            f"Evaluation Pts  : {len(result.evaluation_times)}",
            "",
        ]
        if result.is_empty:
            lines.extend(
                [
                    "No evaluation points were replayed.",
                    "",
                    _WARNING,
                    "============================================================",
                ]
            )
            return "\n".join(lines)

        for i, point in enumerate(result.points, start=1):
            lines.append(self._format_point_block(i, point))
            lines.append("")

        lines.append("------------------------------------------------------------")
        lines.append("Replay Rationale")
        lines.append("------------------------------------------------------------")
        lines.append("")
        lines.append(result.rationale)
        lines.append("")
        lines.append("WARNING:")
        lines.append(_WARNING)
        lines.append("============================================================")
        return "\n".join(lines)

    # ============================================================
    # SINGLE SCAN REPORT
    # ============================================================

    def format_scan(self, scan: MarketScanResult) -> str:
        """Render a single :class:`MarketScanResult` analyst report."""

        return self._format_scan(scan, label="Market Opportunity Scan")

    # ============================================================
    # INTERNAL BLOCK BUILDERS
    # ============================================================

    def _format_point_block(
        self, index: int, point: ReplayEvaluationPoint,
    ) -> str:
        lines: list[str] = [
            "------------------------------------------------------------",
            f"Evaluation Point {index}: {_fmt_ts(point.evaluation_time)}",
            "------------------------------------------------------------",
            f"Scan ID         : {point.scan.scan_id or 'unavailable'}",
            f"Scan Status     : {point.scan.status.name}",
            f"Eligible Count  : {point.scan.eligible_count}",
            "",
        ]
        lines.append(self._format_scan_body(point.scan))
        return "\n".join(lines)

    def _format_scan(
        self, scan: MarketScanResult, label: str,
    ) -> str:
        lines: list[str] = [
            "============================================================",
            label,
            "============================================================",
            f"Scan ID         : {scan.scan_id or 'unavailable'}",
            f"Evaluation Time : {_fmt_ts(scan.timestamp)}",
            f"Instruments     : {len(scan.instruments)}",
            f"Timeframes      : {scan.timeframes[0]} / {scan.timeframes[1]}",
            f"Status          : {scan.status.name}",
            "",
        ]
        if scan.is_empty:
            lines.extend(
                [
                    "No instruments were scanned.",
                    "",
                    _WARNING,
                    "============================================================",
                ]
            )
            return "\n".join(lines)
        lines.append(self._format_scan_body(scan))
        lines.append("")
        lines.append("WARNING:")
        lines.append(_WARNING)
        lines.append("============================================================")
        return "\n".join(lines)

    def _format_scan_body(self, scan: MarketScanResult) -> str:
        lines: list[str] = []

        # Per-instrument analyst detail.
        for r in scan.results:
            lines.extend(self._format_instrument_detail(r))
            lines.append("")

        # Best opportunity.
        if scan.has_best:
            lines.append("------------------------------------------------------------")
            lines.append("Best Opportunity")
            lines.append("------------------------------------------------------------")
            b = scan.best.opportunity
            lines.append(f"Instrument : {b.instrument}")
            lines.append(f"Direction  : {b.direction or 'none'}")
            lines.append(f"MTF        : {scan.best.alignment.name}")
            lines.append(f"Decision   : {b.decision_classification or 'none'}")
            lines.append(f"Score      : {b.decision_score}")
            lines.append(f"R:R        : {_rr(b.risk_reward_ratio)}")
            lines.append(f"Geometry   : {_candidate_geometry(b)}")
            lines.append("")

        # Alternatives.
        if scan.alternatives:
            lines.append("------------------------------------------------------------")
            lines.append("Alternative Opportunities")
            lines.append("------------------------------------------------------------")
            for a in scan.alternatives:
                opp = a.opportunity
                lines.append(
                    f"Rank {a.rank}: {opp.instrument} "
                    f"{opp.direction or 'none'} "
                    f"(MTF {a.alignment.name}, decision "
                    f"{opp.decision_classification or 'none'}, score "
                    f"{opp.decision_score}, R:R "
                    f"{_rr(opp.risk_reward_ratio)})",
                )
            lines.append("")

        # Rejected / incomplete.
        if scan.rejected:
            lines.append("------------------------------------------------------------")
            lines.append("Rejected / Ineligible / Incomplete")
            lines.append("------------------------------------------------------------")
            for r in scan.rejected:
                lines.append(f"- {r.instrument}: {r.reason}")
            lines.append("")

        lines.append("------------------------------------------------------------")
        lines.append("Scan Rationale")
        lines.append("------------------------------------------------------------")
        lines.append(scan.rationale)
        return "\n".join(lines)

    def _format_instrument_detail(
        self, result: InstrumentScanResult,
    ) -> list[str]:
        higher = result.higher_context
        lower = result.lower_context
        decision = result.decision
        opportunity = result.opportunity

        opp_status = "none"
        confluence = 0
        geometry_complete = False
        if opportunity is not None:
            opp_status = getattr(
                getattr(opportunity, "status", None), "name", "none",
            )
            confluence = getattr(opportunity, "confluence_score", 0)
            geometry_complete = getattr(opportunity, "geometry_complete", False)

        setup_type = "none"
        if decision is not None:
            candidate = getattr(decision, "candidate", None)
            if candidate is not None:
                st = getattr(candidate, "setup_type", None)
                setup_type = getattr(st, "name", "none")

        lines: list[str] = [
            "------------------------------------------------------------",
            f"Instrument: {result.instrument}",
            "------------------------------------------------------------",
            f"Complete         : {result.complete}",
            f"Eligible         : {result.eligible}",
            f"Direction        : {result.direction or 'none'}",
            f"MTF Alignment    : {result.alignment.name}",
            "",
            "Higher Timeframe Context "
            f"({result.context_timeframe}):",
            f"  Trend          : {_trend_state(higher)}",
            f"  Range          : {_range_state(higher)}",
            f"  Structure      : {_structure_summary(higher)}",
            f"  Support/Resist : {_support_resistance(higher)}",
            "",
            f"Setup Timeframe ({result.setup_timeframe}):",
            f"  Trend          : {_trend_state(lower)}",
            f"  Range          : {_range_state(lower)}",
            f"  Structure      : {_structure_summary(lower)}",
            f"  Setup Type     : {setup_type}",
            f"  Decision       : {result.decision_classification or 'none'}",
            f"  Score          : {result.decision_score}",
            f"  Confluence     : {confluence}",
            f"  Geometry       : "
            f"{'complete' if geometry_complete else 'incomplete'}",
            f"  {_candidate_geometry(result)}",
            "",
            f"Opportunity      : {opp_status}",
            f"Timestamp        : {_fmt_ts(result.timestamp)}",
            "Reason:",
            self._wrap(result.reason),
        ]
        return lines

    @staticmethod
    def _wrap(reason: str, width: int = 58) -> str:
        if not reason:
            return ""
        words = reason.split()
        lines: list[str] = []
        current = ""
        for w in words:
            if current and len(current) + 1 + len(w) > width:
                lines.append(current)
                current = w
            else:
                current = f"{current} {w}".strip()
        if current:
            lines.append(current)
        return "\n".join(lines)


__all__ = ["HistoricalReplayFormatter"]
