"""
Market scan report formatter (Sprint 11U).

``MarketScanFormatter`` renders a descriptive ``MarketScanResult`` as a
plain-text report. It is stateless and deterministic: identical inputs
always produce identical text.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. It
identifies the strongest AVAILABLE technical trade opportunities across
the scanned instruments / timeframes at one evaluation point. Every
report ends with the explicit warning that market scan results are
descriptive technical-analysis outputs and are NOT predictive signals
or guarantees of profitability.

Following the Sprint 11O-11T reporting convention, this formatter is
imported via its full path
(``from engine.reporting.market_scan import MarketScanFormatter``) and is
NOT re-exported from ``reporting/__init__.py`` (matching
``TradeOpportunityFormatter``, ``TradeDecisionFormatter``,
``TradeCandidateFormatter``, ``SetupAssessmentFormatter`` and
``MarketContextFormatter``).
"""

from __future__ import annotations

from engine.models.market_scan import (
    InstrumentScanResult,
    MarketScanResult,
)


_WARNING = (
    "Market scan results are descriptive technical-analysis outputs and "
    "are NOT predictive signals or guarantees of profitability."
)


def _rr(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


def _fmt_ts(ts) -> str:
    return "unavailable" if ts is None else str(ts)


class MarketScanFormatter:
    """
    Render a descriptive market scan result as plain text.
    """

    def format(self, result: MarketScanResult) -> str:
        """Render a :class:`MarketScanResult`."""

        lines: list[str] = [
            "============================================================",
            "Market Opportunity Scan",
            "============================================================",
            f"Scan ID         : {result.scan_id or 'unavailable'}",
            f"Evaluation Time : {_fmt_ts(result.timestamp)}",
            f"Instruments     : {len(result.instruments)}",
            f"Timeframes      : {result.timeframes[0]} / "
            f"{result.timeframes[1]}",
            f"Status          : {result.status.name}",
            "",
        ]

        if result.is_empty:
            lines.extend(
                [
                    "No instruments were scanned.",
                    "",
                    _WARNING,
                    "============================================================",
                ]
            )
            return "\n".join(lines)

        # ----------------------------------------------------------
        # RANKING TABLE
        # ----------------------------------------------------------
        lines.append(
            "------------------------------------------------------------",
        )
        lines.append(
            f"{'Rank':<5} {'Instrument':<12} {'Direction':<10} "
            f"{'MTF':<12} {'Decision':<10} {'Score':<6} {'R:R':<8}",
        )
        lines.append(
            "------------------------------------------------------------",
        )
        for r in result.ranked:
            opp = r.opportunity
            rank = str(r.rank) if r.rank > 0 else "-"
            lines.append(
                f"{rank:<5} {opp.instrument:<12} {opp.direction or '-':<10} "
                f"{r.alignment.name:<12} "
                f"{opp.decision_classification or '-':<10} "
                f"{opp.decision_score:<6} {_rr(opp.risk_reward_ratio):<8}",
            )
        lines.append("")

        # ----------------------------------------------------------
        # BEST OPPORTUNITY
        # ----------------------------------------------------------
        if result.has_best:
            b = result.best.opportunity
            lines.append("------------------------------------------------------------")
            lines.append("Best Opportunity")
            lines.append("------------------------------------------------------------")
            lines.append("")
            lines.append(f"Instrument : {b.instrument}")
            lines.append(f"Direction  : {b.direction or 'none'}")
            lines.append(f"MTF        : {result.best.alignment.name}")
            lines.append(
                f"Decision   : {b.decision_classification or 'none'}",
            )
            lines.append(f"Score      : {b.decision_score}")
            lines.append(f"R:R        : {_rr(b.risk_reward_ratio)}")
            lines.append("")
            lines.append("Reason:")
            lines.append(self._wrap_reason(b.reason))
            lines.append("")

        if result.alternatives:
            lines.append("------------------------------------------------------------")
            lines.append("Alternative Opportunities")
            lines.append("------------------------------------------------------------")
            lines.append("")
            for a in result.alternatives:
                opp = a.opportunity
                lines.append(
                    f"Rank {a.rank}: {opp.instrument} {opp.direction or 'none'} "
                    f"(MTF {a.alignment.name}, decision "
                    f"{opp.decision_classification or 'none'}, score "
                    f"{opp.decision_score}, R:R "
                    f"{_rr(opp.risk_reward_ratio)})",
                )
            lines.append("")

        # ----------------------------------------------------------
        # REJECTED / INELIGIBLE
        # ----------------------------------------------------------
        if result.rejected:
            lines.append("------------------------------------------------------------")
            lines.append("Rejected / Ineligible / Incomplete")
            lines.append("------------------------------------------------------------")
            lines.append("")
            for r in result.rejected:
                lines.append(f"- {r.instrument}: {r.reason}")
            lines.append("")

        # ----------------------------------------------------------
        # RATIONALE + WARNING
        # ----------------------------------------------------------
        lines.append("------------------------------------------------------------")
        lines.append("Scan Rationale")
        lines.append("------------------------------------------------------------")
        lines.append("")
        lines.append(result.rationale)
        lines.append("")
        lines.append("WARNING:")
        lines.append(_WARNING)
        lines.append("============================================================")
        return "\n".join(lines)

    def format_instrument(self, result: InstrumentScanResult) -> str:
        """Render a single :class:`InstrumentScanResult`."""

        lines: list[str] = [
            "============================================================",
            "Instrument Scan Result",
            "============================================================",
            f"Instrument        : {result.instrument}",
            f"Context Timeframe : {result.context_timeframe}",
            f"Setup Timeframe   : {result.setup_timeframe}",
            f"Timestamp         : {_fmt_ts(result.timestamp)}",
            f"Complete          : {result.complete}",
            f"Direction         : {result.direction or 'none'}",
            f"MTF Alignment     : {result.alignment.name}",
            f"Decision          : {result.decision_classification or 'none'}",
            f"Decision Score    : {result.decision_score}",
            f"Risk/Reward       : {_rr(result.risk_reward_ratio)}",
            f"Eligible          : {result.eligible}",
            "",
            "Reason:",
            self._wrap_reason(result.reason),
            "",
            _WARNING,
            "============================================================",
        ]
        return "\n".join(lines)

    @staticmethod
    def _wrap_reason(reason: str, width: int = 60) -> str:
        """Naive word-wrap for readability (descriptive text only)."""

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


__all__ = ["MarketScanFormatter"]
