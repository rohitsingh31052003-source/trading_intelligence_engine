"""
Trade decision report formatter (Sprint 11S).

``TradeDecisionFormatter`` renders a descriptive ``TradeDecision`` (or a
``TradeDecisionRanking`` of multiple decisions) as a plain-text report.
It is stateless and deterministic: identical inputs always produce
identical text.

The report is DESCRIPTIVE. It never claims directional prediction,
probability of success, profitability, or a trading recommendation. The
``Decision Score`` is described as deterministic technical-evidence
strength/completeness, never as a probability. Every report ends with
the explicit warning that trade decisions are descriptive technical
outputs and are NOT predictive signals or guarantees of profitability.

Following the Sprint 11O-11R reporting convention, this formatter is
imported via its full path
(``from engine.reporting.trade_decision import TradeDecisionFormatter``)
and is NOT re-exported from ``reporting/__init__.py`` (matching
``TradeCandidateFormatter``, ``SetupAssessmentFormatter`` and
``MarketContextFormatter``).
"""

from __future__ import annotations

from engine.models.trade_decision import (
    TradeDecision,
    TradeDecisionRanking,
)


_WARNING = (
    "Trade decisions are descriptive technical-evidence outputs and "
    "are NOT predictive signals or guarantees of profitability."
)


def _fmt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.4f}"


class TradeDecisionFormatter:
    """
    Render a descriptive trade decision / ranking as plain text.
    """

    def format(self, decision: TradeDecision) -> str:
        """Render a single ``TradeDecision``."""

        return self._render_decision(decision, include_warning=True)

    def format_ranking(
        self,
        ranking: TradeDecisionRanking,
    ) -> str:
        """Render a ``TradeDecisionRanking``.

        Produces a ranking table (rank / direction / decision / score)
        followed by a per-decision detail block. The descriptive
        warning is emitted exactly once at the end of the report.
        """

        lines: list[str] = [
            "Trade Decision Ranking",
            "----------------------",
            f"Index            : {ranking.evaluation_index}",
            f"Timestamp        : {ranking.timestamp}",
            f"Candidate Count  : {ranking.candidate_count}",
            f"Has Preferred    : {ranking.has_preferred}",
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
            f"{'Rank':<5} {'Direction':<10} {'Decision':<12} "
            f"{'Score':<6}"
        )
        lines.append("-" * 40)
        for ranked in ranking.decisions:
            d = ranked.decision
            lines.append(
                f"{ranked.rank:<5} {d.direction:<10} "
                f"{d.classification.name:<12} {d.decision_score:<6}"
            )
        lines.append("")

        # Preferred summary.
        if ranking.has_preferred:
            p = ranking.preferred.decision
            lines.append(
                f"Preferred        : rank {ranking.preferred.rank}, "
                f"{p.direction}, {p.classification.name} "
                f"(score {p.decision_score})",
            )
        else:
            lines.append("Preferred        : none")
        lines.append("")

        # Per-decision detail.
        for ranked in ranking.decisions:
            lines.append(
                self._render_decision(ranked.decision, include_warning=False),
            )
            lines.append("")

        lines.append(f"Rationale        : {ranking.rationale}")
        lines.append(_WARNING)
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _render_decision(
        self,
        d: TradeDecision,
        include_warning: bool = True,
    ) -> str:

        supporting = ", ".join(
            i.source.name.lower() for i in d.candidate.supporting_evidence
        ) or "none"
        conflicting = ", ".join(
            i.source.name.lower() for i in d.candidate.conflicting_evidence
        ) or "none"

        rr = (
            f"{d.risk_reward_ratio:.2f}"
            if d.risk_reward_ratio is not None
            else "unavailable"
        )

        lines = [
            "Trade Decision",
            "--------------",
            f"Index            : {d.evaluation_index}",
            f"Timestamp        : {d.timestamp}",
            f"Candidate Status : {d.candidate.status.name}",
            f"Direction        : {d.direction}",
            f"Decision         : {d.classification.name}",
            f"Decision Score   : {d.decision_score}/{d.score.max_total}",
            f"Geometry Complete: {d.geometry_complete}",
            f"Confluence       : {d.confluence_score}",
            f"Supporting Evidence : {supporting}",
            f"Conflicting Evidence: {conflicting}",
            f"Entry            : {_fmt(d.candidate.entry_reference)}",
            f"Stop             : {_fmt(d.candidate.stop_reference)}",
            f"Target           : {_fmt(d.candidate.target_reference)}",
            f"Risk/Reward      : {rr}",
            "",
            "Score Components :",
        ]

        for comp in d.score.components:
            lines.append(
                f"  - {comp.name:<12} {comp.points:>3}/{comp.max_points:<3} "
                f"{comp.reason}"
            )

        lines.append("")
        lines.append(f"Rationale        : {d.rationale}")

        if include_warning:
            lines.append(_WARNING)
        return "\n".join(lines)


__all__ = ["TradeDecisionFormatter"]
