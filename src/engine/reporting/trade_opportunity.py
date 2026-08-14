"""
Trade opportunity report formatter (Sprint 11T).

``TradeOpportunityFormatter`` renders a descriptive ``TradeOpportunity``
(or a ``TradeOpportunityRanking`` of multiple opportunities) as a
plain-text report. It is stateless and deterministic: identical inputs
always produce identical text.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. It
identifies the best AVAILABLE technical opportunity among the eligible
candidates at a point in time. Every report ends with the explicit
warning that trade opportunities are descriptive technical outputs and
are NOT predictive signals or guarantees of profitability.

Following the Sprint 11O-11S reporting convention, this formatter is
imported via its full path
(``from engine.reporting.trade_opportunity import TradeOpportunityFormatter``)
and is NOT re-exported from ``reporting/__init__.py`` (matching
``TradeDecisionFormatter``, ``TradeCandidateFormatter``,
``SetupAssessmentFormatter`` and ``MarketContextFormatter``).
"""

from __future__ import annotations

from engine.models.opportunity import (
    TradeOpportunity,
    TradeOpportunityRanking,
)


_WARNING = (
    "Trade opportunities are descriptive technical-analysis outputs "
    "and are NOT predictive signals or guarantees of profitability."
)


def _fmt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.4f}"


def _rr(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


class TradeOpportunityFormatter:
    """
    Render a descriptive trade opportunity / ranking as plain text.
    """

    def format(self, opportunity: TradeOpportunity) -> str:
        """Render a single ``TradeOpportunity``."""

        return self._render_opportunity(opportunity, include_warning=True)

    def format_ranking(
        self,
        ranking: TradeOpportunityRanking,
    ) -> str:
        """Render a ``TradeOpportunityRanking``.

        Produces a ranking table (rank / direction / status / decision /
        score) followed by a per-opportunity detail block. The
        descriptive warning is emitted exactly once at the end of the
        report.
        """

        lines: list[str] = [
            "Trade Opportunity Ranking",
            "-------------------------",
            f"Index            : {ranking.evaluation_index}",
            f"Timestamp        : {ranking.timestamp}",
            f"Candidate Count  : {ranking.candidate_count}",
            f"Eligible Count   : {ranking.eligible_count}",
            f"Has Best         : {ranking.has_best}",
            "",
        ]

        if ranking.is_empty:
            lines.append("No candidates to rank.")
            lines.append("")
            lines.append(f"Rationale        : {ranking.rationale}")
            lines.append(_WARNING)
            return "\n".join(lines)

        # Ranking table.
        lines.append(
            f"{'Rank':<5} {'Direction':<10} {'Status':<22} "
            f"{'Decision':<12} {'Score':<6}"
        )
        lines.append("-" * 62)
        for op in ranking.opportunities:
            lines.append(
                f"{op.rank:<5} {op.direction:<10} "
                f"{op.status.name:<22} {op.decision_classification:<12} "
                f"{op.decision_score:<6}"
            )
        lines.append("")

        # Best summary.
        if ranking.has_best:
            b = ranking.best
            lines.append(
                f"Best             : rank {b.rank}, {b.direction}, "
                f"{b.status.name} (decision {b.decision_classification}, "
                f"score {b.decision_score})",
            )
        else:
            lines.append("Best             : none")
        lines.append("")

        # Per-opportunity detail.
        for op in ranking.opportunities:
            lines.append(
                self._render_opportunity(op, include_warning=False),
            )
            lines.append("")

        lines.append(f"Rationale        : {ranking.rationale}")
        lines.append(_WARNING)
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _render_opportunity(
        self,
        op: TradeOpportunity,
        include_warning: bool = True,
    ) -> str:

        c = op.decision.candidate
        supporting = ", ".join(
            i.source.name.lower()
            for i in c.supporting_evidence
        ) or "none"
        conflicting = ", ".join(
            i.source.name.lower()
            for i in c.conflicting_evidence
        ) or "none"
        geometry = "COMPLETE" if op.geometry_complete else "INCOMPLETE"

        lines = [
            "Trade Opportunity",
            "-----------------",
            f"Index              : {op.evaluation_index}",
            f"Timestamp          : {op.timestamp}",
            f"Rank               : {op.rank}",
            f"Direction          : {op.direction}",
            f"Status             : {op.status.name}",
            f"Decision           : {op.decision_classification}",
            f"Score              : {op.decision_score}",
            f"Entry              : {_fmt(c.entry_reference)}",
            f"Stop               : {_fmt(c.stop_reference)}",
            f"Target             : {_fmt(c.target_reference)}",
            f"Risk/Reward        : {_rr(op.risk_reward_ratio)}",
            f"Geometry           : {geometry}",
            f"Confluence         : {op.confluence_score}",
            f"Supporting Evidence: {supporting}",
            f"Conflicting Evidence: {conflicting}",
            f"Eligibility        : {op.eligibility.name}",
        ]

        if op.rejection_reason:
            lines.append(f"Rejection Reason   : {op.rejection_reason}")

        lines.append(f"Ranking Reason     : {op.ranking_reason}")

        if include_warning:
            lines.append(_WARNING)
        return "\n".join(lines)


__all__ = ["TradeOpportunityFormatter"]
