"""
Decision intelligence report formatter (Sprint 12A).

:class:`DecisionIntelligenceFormatter` renders the Sprint 12A
decision-intelligence context as a plain-text, analyst-style report so
a human can understand what historical evidence is relevant to a
current opportunity, how strong that evidence is, and how it should be
presented to the existing decision process — WITHOUT inspecting
internal Python objects.

The report STRICTLY separates the six concerns:

* the CURRENT OPPORTUNITY (reused profile),
* the EXISTING DECISION (read-only summary projection — represented,
  never altered),
* the HISTORICAL EVIDENCE (reused cohort / observations),
* the OBSERVED PERFORMANCE (reused Sprint 11X statistics),
* the EVIDENCE STRENGTH (reused Sprint 11Y strength),
* the STRATEGY INTERPRETATION (reused Sprint 11Z status), and
* the DECISION INTELLIGENCE (Sprint 12A context status + factors).

Every report ends with the explicit warning that historical evidence
is descriptive and does NOT guarantee future performance, and that the
existing decision / scoring logic is not modified by this context. No
predictive, profitability, probability, statistical-significance,
buy / sell / enter / exit / hold, or trading-recommendation language is
used. Unavailable metrics are shown as ``"unavailable"`` — never
fabricated.

Following the Sprint 11O-11Z reporting convention, this formatter is
imported via its full path (``from engine.reporting.decision_intelligence
import DecisionIntelligenceFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionIntelligenceContext,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import OpportunityProfile


_WARNING = (
    "Historical evidence is descriptive and does not guarantee future "
    "performance. The existing decision / scoring logic is not modified "
    "by this context."
)


def _rate(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def _r(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}R"


def _int_or_na(value: int) -> str:
    return str(value)


def _str_or_na(value: str) -> str:
    return value if value else "unavailable"


def _key_label(key: str) -> str:
    return "unavailable" if key == "" else key


def _spec_label(spec) -> str:
    if spec is None:
        return "unavailable"
    return " + ".join(d.name for d in spec.dimensions)


class DecisionIntelligenceFormatter:
    """
    Format Sprint 12A decision-intelligence contexts as plain-text,
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

    def format(self, context: DecisionIntelligenceContext) -> str:
        """Format a :class:`DecisionIntelligenceContext` as a report."""

        p = self.precision
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Decision Intelligence Report")
        lines.append("=" * 60)
        lines.append(f"Context ID            : {context.context_id}")
        if context.label:
            lines.append(f"Label                 : {context.label}")
        if context.metadata:
            for k, v in context.metadata:
                lines.append(f"Metadata              : {k} = {v}")
        lines.append("")

        # Current Opportunity.
        lines.append("-" * 60)
        lines.append("Current Opportunity")
        lines.append("-" * 60)
        lines.extend(self._profile_lines(context.profile))
        lines.append("")

        # Existing Decision.
        lines.append("-" * 60)
        lines.append("Existing Decision (represented without alteration)")
        lines.append("-" * 60)
        lines.extend(self._decision_lines(context, p))
        lines.append("")

        # Historical Evidence.
        lines.append("-" * 60)
        lines.append("Historical Evidence")
        lines.append("-" * 60)
        lines.extend(self._evidence_lines(context))
        lines.append("")

        # Observed Performance.
        lines.append("-" * 60)
        lines.append("Observed Performance")
        lines.append("-" * 60)
        if context.observed_performance is None:
            lines.append("unavailable (no matching historical cohort)")
        else:
            lines.append(self._stats_block(context.observed_performance, p, ""))
        lines.append("")

        # Evidence Strength.
        lines.append("-" * 60)
        lines.append("Evidence Strength")
        lines.append("-" * 60)
        lines.append(
            f"Evidence Strength     : "
            f"{context.evidence_strength.name if context.evidence_strength else 'unavailable'}",
        )
        lines.append("")

        # Strategy Interpretation.
        lines.append("-" * 60)
        lines.append("Strategy Interpretation")
        lines.append("-" * 60)
        lines.append(
            f"Strategy Assessment   : "
            f"{context.strategy_interpretation.name if context.strategy_interpretation else 'unavailable'}",
        )
        if context.lookup.assessment is not None:
            lines.append(
                f"Interpretation        : {context.lookup.assessment.interpretation}",
            )
        lines.append("")

        # Decision Context.
        lines.append("-" * 60)
        lines.append("Decision Context")
        lines.append("-" * 60)
        lines.append(
            f"Decision Context Status : {context.decision_context_status.name}",
        )
        lines.append("")
        if context.factors:
            lines.append("Factors:")
            for f in context.factors:
                lines.append(f"  - {f.factor.name}")
                lines.append(f"      {f.reason}")
        else:
            lines.append("Factors: none")
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

    def _profile_lines(self, profile: OpportunityProfile) -> list[str]:
        pairs = profile.available_dimensions()
        if not pairs:
            return ["(no opportunity characteristics provided)"]
        return [f"{dim:<20} = {value}" for dim, value in pairs]

    def _decision_lines(
        self, context: DecisionIntelligenceContext, p: int,
    ) -> list[str]:
        d = context.existing_decision
        if not d.has_decision:
            return ["(no existing decision provided)"]
        return [
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
        ]

    def _evidence_lines(
        self, context: DecisionIntelligenceContext,
    ) -> list[str]:
        lookup = context.lookup
        lines = [
            f"Match Status          : {lookup.match_status.name}",
            f"Matched Spec          : {_spec_label(lookup.matched_spec)}",
        ]
        if lookup.matched_cohort is not None:
            cohort = lookup.matched_cohort
            lines.append(f"Cohort Key            : {_key_label(cohort.key)}")
            lines.append(
                f"Observations          : {_int_or_na(cohort.sample_count)}",
            )
            lines.append(
                f"Resolved              : {_int_or_na(cohort.resolved_count)}",
            )
            lines.append(
                f"Valid R Obs.          : {_int_or_na(cohort.valid_r_count)}",
            )
        else:
            lines.append("Observations          : unavailable")
            lines.append("Resolved              : unavailable")
            lines.append("Valid R Obs.          : unavailable")
        return lines

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


__all__ = ["DecisionIntelligenceFormatter"]
