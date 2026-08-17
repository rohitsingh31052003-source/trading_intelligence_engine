"""
Human-readable trade-plan formatter (Product Phase 4).

A stateless, deterministic formatter that renders a
:class:`~engine.models.trade_plan.TradePlan` as a human-readable audit
report. It introduces NO new intelligence, NO prediction, NO
recommendation. Every value is read from the plan model. The formatter
returns a ``str`` (no ``print()`` inside the formatter).

The report STRICTLY separates the concerns the trade plan keeps
separate: ACCOUNT RISK (user-supplied capital / risk %), TRADE GEOMETRY
(reused verbatim from the Sprint 11R candidate), POSITION SIZE (the
deterministic calculation), STATUS (the risk-plan status, distinct from
the market decision). It explicitly distinguishes "potential planned
reward" (deterministic) from "expected return" (a prediction, never
made).

Every report ends with the explicit WARNING that the plan is a
deterministic risk calculation, not a prediction or guarantee of future
performance. No predictive / probability / buy-sell-enter-exit-hold
language is used.

Following the 11O-12E reporting convention, the formatter is imported
via full path (NOT re-exported from ``reporting/__init__.py``).
"""

from __future__ import annotations

from decimal import Decimal

from engine.models.trade_plan import TradePlan


_WARNING = (
    "WARNING: This is a deterministic risk calculation based on the supplied "
    "account parameters and the existing trade geometry. It is NOT a prediction "
    "or guarantee of future performance. The existing decision engine remains "
    "authoritative; this plan does NOT modify it and does NOT constitute a "
    "BUY/SELL/ENTER/EXIT/HOLD recommendation."
)


def _fmt_money(value: Decimal | None, precision: int = 2) -> str:
    if value is None:
        return "unavailable"
    q = Decimal(1).scaleb(-precision)
    return f"{value.quantize(q):,.{precision}f}"


def _fmt_qty(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    # Show integers without a trailing .0; show fractions compactly.
    if value == value.to_integral_value():
        return f"{int(value)}"
    return f"{value}"


def _fmt_ratio(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value}"


class TradePlanFormatter:
    """
    Format a :class:`TradePlan` as a human-readable audit report.

    Stateless and deterministic. Returns ``str``.

    Attributes:

    precision
        Number of decimal places for monetary values. Non-negative.
        Default ``2``.
    """

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    def format(self, plan: TradePlan) -> str:
        """Render a trade plan as a multi-section text report."""

        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("TRADE PLAN")
        lines.append("=" * 60)
        # Identity
        lines.append("")
        lines.append(f"Plan ID: {plan.plan_id}")
        if plan.label:
            lines.append(f"Label: {plan.label}")
        if plan.metadata:
            for k, v in plan.metadata:
                lines.append(f"  {k}: {v}")
        lines.append("")
        # Headline
        lines.append("INSTRUMENT")
        lines.append(f"  Instrument: {plan.instrument or 'unavailable'}")
        lines.append(f"  Timeframe: {plan.timeframe or 'unavailable'}")
        lines.append(f"  Direction: {plan.direction or 'NONE'}")
        lines.append(f"  Existing Decision: {plan.existing_decision or 'none'}")
        lines.append(f"  Actionability: {plan.actionability or 'none'}")
        lines.append("")
        # Account risk
        lines.append("ACCOUNT RISK")
        lines.append(f"  Account Capital: {_fmt_money(plan.account_capital, self.precision)}")
        risk_pct = plan.risk_percent
        if risk_pct is None:
            lines.append("  Risk per trade: unavailable")
        else:
            lines.append(f"  Risk per trade: {risk_pct}%")
        lines.append(f"  Maximum risk: {_fmt_money(plan.maximum_risk, self.precision)}")
        lines.append("")
        # Trade geometry (reused verbatim)
        lines.append("TRADE GEOMETRY (reused verbatim from the engine)")
        lines.append(f"  Entry: {_fmt_money(plan.entry, self.precision)}")
        lines.append(f"  Stop: {_fmt_money(plan.stop, self.precision)}")
        lines.append(f"  Target 1: {_fmt_money(plan.target_1, self.precision)}")
        lines.append(f"  Target 2: Not supported by current architecture")
        lines.append(
            f"  Engine risk distance: {_fmt_money(plan.engine_risk_distance, self.precision)}",
        )
        lines.append(
            f"  Engine reward distance: {_fmt_money(plan.engine_reward_distance, self.precision)}",
        )
        lines.append(
            f"  Engine risk/reward ratio: {_fmt_ratio(plan.engine_risk_reward_ratio)}",
        )
        lines.append("")
        # Position size
        lines.append("POSITION SIZE")
        lines.append(f"  Quantity: {_fmt_qty(plan.quantity)}")
        lines.append(f"  Quantity status: {plan.quantity_status.value}")
        lines.append(
            f"  Maximum planned loss: {_fmt_money(plan.planned_risk, self.precision)}",
        )
        lines.append(
            f"  Potential planned reward: {_fmt_money(plan.planned_reward, self.precision)}",
        )
        lines.append(
            "  (Potential planned reward is deterministic from quantity x "
            "reward distance; it is NOT an expected return and NOT a "
            "prediction.)",
        )
        lines.append("")
        # Status
        lines.append("STATUS")
        lines.append(f"  Risk plan status: {plan.risk_plan_status.value}")
        lines.append(
            f"  Quantity spec available: {plan.quantity_spec_available}",
        )
        lines.append("")
        # Warnings
        if plan.warnings:
            lines.append("WARNINGS")
            for w in plan.warnings:
                lines.append(f"  - {w}")
            lines.append("")
        # Rationale
        if plan.rationale:
            lines.append("RATIONALE")
            lines.append(f"  {plan.rationale}")
            lines.append("")
        # Disclaimer
        lines.append("-" * 60)
        lines.append(_WARNING)
        lines.append("=" * 60)
        return "\n".join(lines)


__all__ = ["TradePlanFormatter"]
