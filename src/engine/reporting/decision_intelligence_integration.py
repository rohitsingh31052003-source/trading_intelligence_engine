"""
Controlled decision intelligence INTEGRATION report formatter
(Sprint 12B).

:class:`DecisionIntelligenceIntegrationFormatter` renders the Sprint 12B
:class:`IntegratedDecisionContext` as a plain-text, analyst-style report
so a human can understand which existing decision was integrated with
which decision-intelligence context, whether the intelligence was
available, what evidence status / strategy interpretation was attached,
and why the integration status was assigned — WITHOUT inspecting
internal Python objects and WITHOUT any risk of interpreting the
attached evidence as a new trading signal.

The report STRICTLY separates the concerns:

* the INTEGRATION IDENTITY (id / label / metadata),
* the EXISTING DECISION (read-only projection — represented, never
  altered),
* the DECISION INTELLIGENCE (reused 12A context id / decision-context
  status),
* the EVIDENCE STATUS (reused 12A ``DecisionContextStatus``),
* the STRATEGY INTERPRETATION (reused 11Z ``StrategyAssessmentStatus``),
* the INTEGRATION STATUS (Sprint 12B ``IntegrationStatus``), and
* the RATIONALE / LIMITATIONS / DISCLAIMER.

Every report ends with the explicit warning that historical /
evidence-derived context is descriptive, is NOT a predictive guarantee,
does NOT constitute a trading recommendation, and does NOT modify the
existing decision / scoring logic. No predictive, profitability,
probability, statistical-significance, buy / sell / enter / exit / hold,
or trading-recommendation language is used. Unavailable metrics are
shown as ``"unavailable"`` — never fabricated.

Following the Sprint 11O-12A reporting convention, this formatter is
imported via its full path (``from
engine.reporting.decision_intelligence_integration import
DecisionIntelligenceIntegrationFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionIntelligenceContext,
)
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import OpportunityProfile


_WARNING = (
    "Decision intelligence is contextual evidence attached to the existing "
    "decision. It is descriptive, does NOT guarantee future performance, "
    "does NOT constitute a trading recommendation, and does NOT modify the "
    "existing decision / scoring logic."
)


def _rate(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def _r(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}R"


def _str_or_na(value: str) -> str:
    return value if value else "unavailable"


def _int_or_na(value: int) -> str:
    return str(value)


def _enum_or_na(value) -> str:
    return value.name if value is not None else "unavailable"


class DecisionIntelligenceIntegrationFormatter:
    """
    Format Sprint 12B integrated decision contexts as plain-text,
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

    def format(self, context: IntegratedDecisionContext) -> str:
        """Format an :class:`IntegratedDecisionContext` as a report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Decision Intelligence Integration Report")
        lines.append("=" * 60)
        lines.append(f"Integration ID        : {context.integration_id}")
        if context.label:
            lines.append(f"Label                 : {context.label}")
        if context.metadata:
            for k, v in context.metadata:
                lines.append(f"Metadata              : {k} = {v}")
        lines.append("")

        # Existing Decision.
        lines.append("-" * 60)
        lines.append("Existing Decision (represented without alteration)")
        lines.append("-" * 60)
        lines.extend(self._decision_lines(context, p))
        lines.append("")

        # Decision Intelligence.
        lines.append("-" * 60)
        lines.append("Decision Intelligence")
        lines.append("-" * 60)
        lines.extend(self._intelligence_lines(context))
        lines.append("")

        # Evidence Status.
        lines.append("-" * 60)
        lines.append("Evidence Status")
        lines.append("-" * 60)
        lines.append(f"Evidence Status       : {_enum_or_na(context.evidence_status)}")
        lines.append("")

        # Strategy Interpretation.
        lines.append("-" * 60)
        lines.append("Strategy Interpretation")
        lines.append("-" * 60)
        lines.append(
            f"Strategy Assessment   : {_enum_or_na(context.strategy_interpretation)}",
        )
        lines.append("")

        # Observed Performance (reused; surfaced for audit separation).
        lines.append("-" * 60)
        lines.append("Observed Performance")
        lines.append("-" * 60)
        if context.observed_performance is None:
            lines.append("unavailable (no matching historical cohort)")
        else:
            lines.append(self._stats_block(context.observed_performance, p, ""))
        lines.append("")

        # Evidence Strength (reused; surfaced for audit separation).
        lines.append("-" * 60)
        lines.append("Evidence Strength")
        lines.append("-" * 60)
        lines.append(f"Evidence Strength     : {_enum_or_na(context.evidence_strength)}")
        lines.append("")

        # Integration Status.
        lines.append("-" * 60)
        lines.append("Integration Status")
        lines.append("-" * 60)
        lines.append(
            f"Integration Status    : {context.integration_status.name}",
        )
        lines.append(
            f"Decision Intelligence Attached : {context.integration_status.is_attached}",
        )
        lines.append("")

        # Contextual Factors (reused from 12A).
        lines.append("-" * 60)
        lines.append("Contextual Factors")
        lines.append("-" * 60)
        if context.contextual_factors:
            for f in context.contextual_factors:
                lines.append(f"  - {f.factor.name}")
                lines.append(f"      {f.reason}")
        else:
            lines.append("(no decision-intelligence factors; "
                         "no decision intelligence attached)")
        lines.append("")

        # Limitations.
        lines.append("-" * 60)
        lines.append("Limitations")
        lines.append("-" * 60)
        lines.append(context.limitations or "No limitations recorded.")
        lines.append("")

        # Rationale.
        lines.append("-" * 60)
        lines.append("Rationale")
        lines.append("-" * 60)
        lines.append(context.rationale or "No rationale recorded.")
        lines.append("")

        lines.append("WARNING: " + _WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _decision_lines(
        self, context: IntegratedDecisionContext, p: int,
    ) -> list[str]:
        d = context.existing_decision_summary
        ref_note = (
            "retained by reference (original object preserved unchanged)"
            if context.existing_decision is not None
            else "no original decision object supplied"
        )
        lines = [f"Original Object       : {ref_note}"]
        if not d.has_decision:
            lines.append("(no existing decision projection)")
            return lines
        lines.extend([
            f"Direction             : {_str_or_na(d.direction)}",
            f"Decision              : {_str_or_na(d.decision_classification)}",
            f"Decision Score        : {_int_or_na(d.decision_score)}",
            f"Opportunity Status    : {_str_or_na(d.opportunity_status)}",
            f"Rank                  : {_int_or_na(d.rank)}",
            f"Geometry Complete     : {d.geometry_complete}",
            f"Confluence Score      : {_int_or_na(d.confluence_score)}",
            f"Risk/Reward           : {_rate(d.risk_reward_ratio, p)}",
            f"Entry                 : {_rate(d.entry, p)}",
            f"Stop                  : {_rate(d.stop, p)}",
            f"Target                : {_rate(d.target, p)}",
        ])
        return lines

    def _intelligence_lines(
        self, context: IntegratedDecisionContext,
    ) -> list[str]:
        di: DecisionIntelligenceContext | None = context.decision_intelligence
        if di is None:
            return [
                "Decision Intelligence : unavailable (no context supplied)",
                "Context ID            : unavailable",
                "Decision Context      : unavailable",
            ]
        return [
            f"Decision Intelligence : attached (reused Sprint 12A context)",
            f"Context ID            : {di.context_id}",
            f"Decision Context      : {di.decision_context_status.name}",
            f"Matched Cohort        : {di.matched}",
        ]

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


__all__ = ["DecisionIntelligenceIntegrationFormatter"]
