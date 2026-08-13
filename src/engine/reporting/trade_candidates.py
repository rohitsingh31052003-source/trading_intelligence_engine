"""
Trade candidate report formatter (Sprint 11R).

``TradeCandidateFormatter`` renders a descriptive ``TradeCandidate`` (or
a sequence of them) as a plain-text report. It is stateless and
deterministic: identical inputs always produce identical text.

The report is DESCRIPTIVE. It never claims directional prediction,
profitability, or a trading recommendation. Every report ends with the
explicit warning that trade candidates are descriptive technical-
analysis outputs and are NOT predictive signals or guarantees of
profitability.
"""

from __future__ import annotations

from typing import Iterable

from engine.models.trade_candidate import TradeCandidate


_WARNING = (
    "Trade candidates are descriptive technical-analysis outputs and "
    "are NOT predictive signals or guarantees of profitability."
)


def _fmt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.4f}"


class TradeCandidateFormatter:
    """
    Render a descriptive trade candidate as plain text.
    """

    def format(self, candidate: TradeCandidate) -> str:
        """Render a single ``TradeCandidate``."""

        return self._render_point(candidate, include_warning=True)

    def format_sequence(
        self,
        candidates: Iterable[TradeCandidate],
    ) -> str:
        """Render a sequence of ``TradeCandidate`` snapshots.

        The descriptive warning is emitted exactly once at the end of
        the sequence rather than per-point.
        """

        lines: list[str] = []
        for c in candidates:
            lines.append(self._render_point(c, include_warning=False))
            lines.append("")
        lines.append(_WARNING)
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _render_point(
        self,
        c: TradeCandidate,
        include_warning: bool = True,
    ) -> str:

        supporting = (
            ", ".join(i.source.name.lower() for i in c.supporting_evidence)
            or "none"
        )
        conflicting = (
            ", ".join(
                i.source.name.lower() for i in c.conflicting_evidence
            )
            or "none"
        )

        rr = (
            f"{c.risk_reward_ratio:.2f}"
            if c.risk_reward_ratio is not None
            else "unavailable"
        )

        lines = [
            "Trade Candidate",
            "----------------",
            f"Index            : {c.evaluation_index}",
            f"Timestamp        : {c.timestamp}",
            f"Direction        : {c.direction.name}",
            f"Status           : {c.status.name}",
            f"Setup            : {c.setup_type.name}",
            f"Setup Class      : {c.setup_classification.name}",
            f"Entry            : {_fmt(c.entry_reference)}",
            f"Stop             : {_fmt(c.stop_reference)}",
            f"Target           : {_fmt(c.target_reference)}",
            f"Risk             : {_fmt(c.risk_distance)}",
            f"Reward           : {_fmt(c.reward_distance)}",
            f"Risk/Reward      : {rr}",
            f"Confluence       : {c.confluence_score}",
            f"Supporting Evidence : {supporting}",
            f"Conflicting Evidence : {conflicting}",
            f"Candle Evidence  : {c.candle_evidence}",
            f"Market Trend     : {c.market_trend}",
            f"Market Structure : {c.market_structure}",
            f"Location         : {c.location}",
            f"Range Context    : {c.range_context}",
            "",
            f"Reason           : {c.reason}",
        ]

        if include_warning:
            lines.append(_WARNING)
        return "\n".join(lines)


__all__ = ["TradeCandidateFormatter"]
