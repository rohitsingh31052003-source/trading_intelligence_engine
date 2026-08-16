"""
Production INTEGRATION + FINAL HARDENING report formatter (Sprint 12E).

:class:`ProductionIntelligenceFormatter` renders the Sprint 12E
:class:`ProductionIntelligenceContext` as a plain-text, analyst-style
report so a human can understand, in ONE coherent view, the complete
separated-concern picture of a production-facing artifact: the existing
decision, the historical observed performance, the evidence strength,
the strategy interpretation, the decision-intelligence context, the
controlled integration status, and the offline validation / robustness
state — WITHOUT any risk of interpreting the bundle as a new trading
signal.

The report STRICTLY separates the eight concerns (never collapsed into
one score):

1. Market / opportunity information
2. Existing decision (authoritative, represented without alteration)
3. Historical observed performance (descriptive)
4. Evidence strength (sample-size-gated)
5. Strategy interpretation (evidence-conditioned)
6. Decision intelligence context (contextual)
7. Controlled integration status (explicit)
8. Validation / robustness state (explicit where available)

Every report ends with the explicit warning that production intelligence
is a coherent bundle of already-computed descriptive artifacts, is NOT
a predictive guarantee, does NOT constitute a trading recommendation,
and does NOT modify the existing decision / scoring logic. No
predictive, profitability, probability, statistical-significance, buy /
sell / enter / exit / hold, or trading-recommendation language is used.
Unavailable metrics are shown as ``"unavailable"`` — never fabricated.

Following the Sprint 11O-12D reporting convention, this formatter is
imported via its full path (``from
engine.reporting.production_intelligence import
ProductionIntelligenceFormatter``) and is NOT re-exported from
``reporting/__init__.py``.
"""

from __future__ import annotations

from engine.models.production_intelligence import (
    ProductionIntelligenceContext,
    ProductionIntegrationStatus,
    ProductionValidationState,
)


_WARNING = (
    "Production intelligence is a coherent bundle of already-computed "
    "descriptive artifacts. It is descriptive, does NOT guarantee future "
    "performance, does NOT constitute a trading recommendation, does NOT "
    "imply live-trading readiness, and does NOT modify the existing "
    "decision / scoring logic."
)


def _enum_or_na(value) -> str:
    return value.name if value is not None else "unavailable"


def _str_or_na(value: str) -> str:
    return value if value else "unavailable"


def _int_or_na(value: int) -> str:
    return str(value)


def _rate(value: float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


class ProductionIntelligenceFormatter:
    """
    Format Sprint 12E production intelligence contexts as plain-text,
    analyst-style reports.

    Stateless and deterministic. Returns ``str`` (no ``print()``).
    """

    def __init__(self, precision: int = 2, width: int = 60) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        if width < 40:
            raise ValueError("width must be at least 40.")
        self.precision = precision
        self.width = width

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, context: ProductionIntelligenceContext) -> str:
        """Format a :class:`ProductionIntelligenceContext` as a report."""

        w = self.width
        p = self.precision
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("Production Intelligence Report")
        lines.append("=" * w)
        lines.append(f"Production ID         : {context.production_id}")
        if context.label:
            lines.append(f"Label                 : {context.label}")
        if context.metadata:
            for k, v in context.metadata:
                lines.append(f"Metadata              : {k} = {v}")
        lines.append("")

        integ = context.integrated_context

        # 1. Market / opportunity information.
        lines.append("-" * w)
        lines.append("Market / Opportunity Information")
        lines.append("-" * w)
        profile = context.profile
        dims = profile.available_dimensions()
        if dims:
            for dim, value in dims:
                lines.append(f"{dim:<22}: {value}")
        else:
            lines.append("(no opportunity characteristics provided)")
        lines.append("")

        # 2. Existing decision (authoritative).
        lines.append("-" * w)
        lines.append("Existing Decision (authoritative; represented without alteration)")
        lines.append("-" * w)
        lines.extend(self._decision_lines(context, integ, p))
        lines.append("")

        # 3. Historical observed performance (descriptive).
        lines.append("-" * w)
        lines.append("Historical Observed Performance (descriptive)")
        lines.append("-" * w)
        obs = context.observed_performance
        if obs is None:
            lines.append("unavailable (no matching historical cohort)")
        else:
            lines.append(self._stats_block(obs, p))
        lines.append("")

        # 4. Evidence strength (sample-size-gated).
        lines.append("-" * w)
        lines.append("Evidence Strength (sample-size-gated)")
        lines.append("-" * w)
        lines.append(f"Evidence Strength     : {_enum_or_na(context.evidence_strength)}")
        lines.append("")

        # 5. Strategy interpretation (evidence-conditioned).
        lines.append("-" * w)
        lines.append("Strategy Interpretation (evidence-conditioned)")
        lines.append("-" * w)
        lines.append(
            f"Strategy Assessment   : {_enum_or_na(context.strategy_interpretation)}",
        )
        lines.append("")

        # 6. Decision intelligence context (contextual).
        lines.append("-" * w)
        lines.append("Decision Intelligence Context (contextual)")
        lines.append("-" * w)
        di = context.decision_intelligence
        if di is None:
            lines.append("Decision Intelligence : unavailable (no context supplied)")
            lines.append("Context ID            : unavailable")
            lines.append("Decision Context      : unavailable")
        else:
            lines.append("Decision Intelligence : attached (reused Sprint 12A context)")
            lines.append(f"Context ID            : {di.context_id}")
            lines.append(f"Decision Context      : {di.decision_context_status.name}")
            lines.append(f"Matched Cohort        : {di.matched}")
        lines.append("")

        # 7. Controlled integration status (explicit).
        lines.append("-" * w)
        lines.append("Controlled Integration Status (explicit)")
        lines.append("-" * w)
        lines.append(f"Production Status     : {context.integration_status.name}")
        lines.append(
            f"DI Attached           : {context.integration_status.is_attached}",
        )
        if integ is not None:
            lines.append(
                f"Reused 12B Integration: id {integ.integration_id}, "
                f"status {integ.integration_status.name}",
            )
        else:
            lines.append("Reused 12B Integration: unavailable (no 12B context supplied)")
        lines.append("")

        # 8. Validation / robustness state (explicit where available).
        lines.append("-" * w)
        lines.append("Validation / Robustness State (offline; explicit)")
        lines.append("-" * w)
        lines.append(f"Validation State      : {context.validation_state.name}")
        lines.append(f"Validation Attached   : {context.has_validation}")
        if context.backtest_validation is not None:
            bv = context.backtest_validation
            lines.append(
                f"12C Backtest          : id {bv.validation_id}, "
                f"overall {bv.overall_status.name}, "
                f"scenarios {bv.scenario_count}, checks {bv.check_count} "
                f"(passed {bv.passed_count}, failed {bv.failed_count}, "
                f"skipped {bv.skipped_count})",
            )
        else:
            lines.append("12C Backtest          : not attached")
        if context.robustness_validation is not None:
            rv = context.robustness_validation
            lines.append(
                f"12D Robustness        : id {rv.validation_id}, "
                f"overall {rv.overall_status.name}, "
                f"scenarios {rv.scenario_count}, checks {rv.check_count} "
                f"(passed {rv.passed_count}, failed {rv.failed_count}, "
                f"skipped {rv.skipped_count})",
            )
        else:
            lines.append("12D Robustness        : not attached")
        lines.append("")

        # Contextual factors (reused from 12A via 12B).
        lines.append("-" * w)
        lines.append("Contextual Factors (reused from Sprint 12A)")
        lines.append("-" * w)
        factors = context.contextual_factors
        if factors:
            for f in factors:
                lines.append(f"  - {f.factor.name}")
                lines.append(f"      {f.reason}")
        else:
            lines.append(
                "(no decision-intelligence factors; no decision intelligence attached)",
            )
        lines.append("")

        # Limitations.
        lines.append("-" * w)
        lines.append("Limitations")
        lines.append("-" * w)
        lines.append(context.limitations or "No limitations recorded.")
        lines.append("")

        # Rationale.
        lines.append("-" * w)
        lines.append("Rationale")
        lines.append("-" * w)
        lines.append(context.rationale or "No rationale recorded.")
        lines.append("")

        lines.append("WARNING: " + _WARNING)
        lines.append("=" * w)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _decision_lines(self, context, integ, p: int) -> list[str]:
        d = context.existing_decision_summary
        if integ is not None and integ.existing_decision is not None:
            ref_note = (
                "retained by reference (original object preserved unchanged)"
            )
        elif integ is not None:
            ref_note = "no original decision object carried by the 12B context"
        else:
            ref_note = "no 12B integrated context supplied"
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

    def _stats_block(self, s, p: int) -> str:
        """Render the reused observed-result statistics as a block."""
        return (
            f"total={s.total}, resolved={s.resolved}, "
            f"target_hits={s.target_hits}, stop_hits={s.stop_hits}, "
            f"expired={s.expired}, both_touched={s.both_touched}, "
            f"no_geometry={s.no_geometry}, "
            f"insufficient_data={s.insufficient_data}; "
            f"win_rate={_rate(s.win_rate, p)}, "
            f"avg_R={_rate(s.average_realized_r, p)}, "
            f"total_R={_rate(s.total_realized_r, p)}, "
            f"profit_factor={_rate(s.profit_factor, p)}, "
            f"valid_r={s.valid_r_count}"
        )


__all__ = ["ProductionIntelligenceFormatter"]
