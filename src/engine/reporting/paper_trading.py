"""
Human-readable paper-trade formatter (Product Phase 5).

A stateless, deterministic formatter that renders a
:class:`~engine.models.paper_trade.PaperTrade` (and a journal of trades +
:class:`~engine.models.paper_trade_performance.PaperTradePerformanceAnalytics`)
as a human-readable audit report. It introduces NO new intelligence, NO
prediction, NO recommendation. Every value is read from the paper-trade
model(s). The formatter returns a ``str`` (no ``print()`` inside the
formatter).

The report STRICTLY separates the concerns the paper-trade model keeps
separate: SYSTEM DECISION (reused verbatim, AUTHORITATIVE), TRADE
GEOMETRY (reused verbatim), TRADE PLAN (reused verbatim from Product
Phase 4), PAPER-TRADE LIFECYCLE (status / entry / exit), and
PAPER-TRADE RESULT (realized R / P&L — a SEPARATE concern from the
system decision). It explicitly distinguishes the system decision from
the paper-trade result so a ``QUALIFIED`` decision that resulted in a
``LOSS`` is NEVER re-classified, and a ``LOSS`` never implies the
decision was wrong.

Every report ends with the explicit WARNING that paper trading is
observational validation, does not guarantee future performance, and
does not constitute financial advice. No predictive / probability /
buy-sell-enter-exit-hold / statistical-significance language is used.

Following the 11O-12E reporting convention, the formatter is imported
via full path (NOT re-exported from ``reporting/__init__.py``).
"""

from __future__ import annotations

from decimal import Decimal

from engine.models.paper_trade import PaperTrade
from engine.models.paper_trade_performance import (
    PaperTradePerformanceAnalytics,
    PaperTradePerformanceStatistics,
)


_WARNING = (
    "WARNING: Paper trading is observational validation of how the system's "
    "existing trade opportunities would have performed. It does NOT guarantee "
    "future performance, does NOT constitute financial advice, and does NOT "
    "place any real order. The existing decision engine remains authoritative; "
    "paper-trade results NEVER rewrite the original system decision and are "
    "NOT a BUY/SELL/ENTER/EXIT/HOLD recommendation."
)


def _fmt_money(value, precision: int = 2) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, Decimal):
        q = Decimal(1).scaleb(-precision)
        return f"{value.quantize(q):,.{precision}f}"
    # float fallback (R-multiple aggregates are float).
    return f"{float(value):,.{precision}f}"


def _fmt_ratio(value, precision: int = 2) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return f"{value}"
        return f"{value}"
    return f"{float(value):.{precision}f}"


def _fmt_r(value, precision: int = 2) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, Decimal):
        q = Decimal(1).scaleb(-precision)
        return f"{value.quantize(q):,.{precision}f}R"
    return f"{float(value):,.{precision}f}R"


def _fmt_rate(value: float | None, precision: int = 1) -> str:
    if value is None:
        return "unavailable"
    return f"{value * 100:.{precision}f}%"


def _fmt_ts(value) -> str:
    if value is None:
        return "unavailable"
    return value.isoformat()


class PaperTradeFormatter:
    """Format a single :class:`PaperTrade` as a human-readable report."""

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    def format(self, trade: PaperTrade) -> str:
        p = self.precision
        lines: list[str] = []
        lines.append("PAPER TRADE REPORT")
        lines.append("=" * 60)
        lines.append(f"Paper Trade ID : {trade.paper_trade_id}")
        lines.append(f"Instrument     : {trade.instrument}")
        lines.append(f"Timeframe      : {trade.timeframe}")
        lines.append(f"Direction      : {trade.direction or 'unavailable'}")
        lines.append(f"Status         : {trade.status.value}")
        lines.append(f"Created At     : {_fmt_ts(trade.created_at)}")
        lines.append(f"Evaluation Ts  : {_fmt_ts(trade.evaluation_timestamp)}")
        if trade.label:
            lines.append(f"Label          : {trade.label}")
        lines.append("")
        lines.append("SYSTEM DECISION (AUTHORITATIVE, reused verbatim)")
        lines.append("-" * 60)
        lines.append(f"Existing Decision : {trade.existing_decision or 'unavailable'}")
        lines.append(f"Setup Type        : {trade.setup_type or 'unavailable'}")
        lines.append(
            "Note: The system decision is AUTHORITATIVE. A paper-trade "
            "result is a SEPARATE concern and NEVER rewrites this decision."
        )
        lines.append("")
        lines.append("TRADE GEOMETRY (reused verbatim from Sprint 11R candidate)")
        lines.append("-" * 60)
        lines.append(f"Entry          : {_fmt_money(trade.entry, p)}")
        lines.append(f"Stop           : {_fmt_money(trade.stop, p)}")
        lines.append(f"Target 1       : {_fmt_money(trade.target_1, p)}")
        lines.append(f"Target 2       : unsupported (None)")
        lines.append(f"Risk Distance  : {_fmt_money(trade.engine_risk_distance, p)}")
        lines.append(f"Reward Distance: {_fmt_money(trade.engine_reward_distance, p)}")
        lines.append(f"Risk/Reward    : {_fmt_ratio(trade.engine_risk_reward_ratio)}")
        lines.append("")
        lines.append("TRADE PLAN (reused verbatim from Product Phase 4)")
        lines.append("-" * 60)
        lines.append(f"Plan ID         : {trade.plan_id or 'unavailable'}")
        lines.append(f"Account Capital: {_fmt_money(trade.account_capital, p)}")
        lines.append(f"Risk Percent   : {_fmt_ratio(trade.risk_percent)}%")
        lines.append(f"Maximum Risk   : {_fmt_money(trade.maximum_risk, p)}")
        lines.append(f"Planned Quantity: {_fmt_money(trade.planned_quantity, p)}")
        lines.append(f"Planned Risk   : {_fmt_money(trade.planned_risk, p)}")
        lines.append("")
        lines.append("PAPER-TRADE LIFECYCLE")
        lines.append("-" * 60)
        lines.append(f"Entry Timestamp: {_fmt_ts(trade.entry_timestamp)}")
        lines.append(f"Actual Entry   : {_fmt_money(trade.actual_entry_price, p)}")
        lines.append(f"Exit Timestamp : {_fmt_ts(trade.exit_timestamp)}")
        lines.append(f"Actual Exit    : {_fmt_money(trade.actual_exit_price, p)}")
        lines.append(
            f"Exit Reason    : "
            f"{trade.exit_reason.value if trade.exit_reason else 'unavailable'}"
        )
        lines.append("")
        lines.append("PAPER-TRADE RESULT")
        lines.append("-" * 60)
        lines.append(f"Realized R     : {_fmt_r(trade.realized_r, p)}")
        lines.append(f"Realized P&L   : {_fmt_money(trade.realized_pnl, p)}")
        lines.append("")
        if trade.metadata:
            lines.append("METADATA")
            lines.append("-" * 60)
            for k, v in trade.metadata:
                lines.append(f"{k}: {v}")
            lines.append("")
        lines.append(_WARNING)
        return "\n".join(lines)


class PaperTradeJournalFormatter:
    """Format a journal (ordered list) of paper trades."""

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    def format(self, trades: list[PaperTrade]) -> str:
        p = self.precision
        lines: list[str] = []
        lines.append("PAPER TRADING JOURNAL")
        lines.append("=" * 60)
        lines.append(f"Total trades: {len(trades)}")
        lines.append("")
        if not trades:
            lines.append("No paper trades recorded.")
            lines.append("")
            lines.append(_WARNING)
            return "\n".join(lines)
        lines.append(
            f"{'#':>3}  {'Instrument':<12} {'Dir':<5} {'Decision':<10} "
            f"{'Status':<18} {'Entry':>12} {'Stop':>12} {'Exit':>12} "
            f"{'R':>8} {'P&L':>14}"
        )
        lines.append("-" * 120)
        for i, t in enumerate(trades, 1):
            lines.append(
                f"{i:>3}  {t.instrument:<12} {t.direction or '-':<5} "
                f"{t.existing_decision or '-':<10} {t.status.value:<18} "
                f"{_fmt_money(t.entry, p):>12} {_fmt_money(t.stop, p):>12} "
                f"{_fmt_money(t.actual_exit_price, p):>12} "
                f"{_fmt_r(t.realized_r, p):>8} "
                f"{_fmt_money(t.realized_pnl, p):>14}"
            )
        lines.append("")
        lines.append(_WARNING)
        return "\n".join(lines)


class PaperTradePerformanceFormatter:
    """Format :class:`PaperTradePerformanceAnalytics` as a report."""

    def __init__(self, precision: int = 2) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        self.precision = precision

    def format(self, analytics: PaperTradePerformanceAnalytics) -> str:
        p = self.precision
        lines: list[str] = []
        lines.append("PAPER TRADING PERFORMANCE REPORT")
        lines.append("=" * 60)
        lines.append(f"Analytics ID : {analytics.analytics_id}")
        if analytics.label:
            lines.append(f"Label        : {analytics.label}")
        lines.append(f"Trade Count  : {analytics.trade_count}")
        lines.append("")
        lines.append("OVERALL PERFORMANCE")
        lines.append("-" * 60)
        _format_stats(analytics.overall, p, lines)
        lines.append("")
        for breakdown in analytics.breakdowns:
            lines.append(f"PERFORMANCE BY {breakdown.dimension.value}")
            lines.append("-" * 60)
            if not breakdown.groups:
                lines.append("No groups.")
                lines.append("")
                continue
            for group in breakdown.groups:
                key_display = group.key if group.key else "unavailable"
                lines.append(f"[{key_display}]")
                _format_stats(group.statistics, p, lines, indent="  ")
                lines.append("")
        lines.append("RATIONALE")
        lines.append("-" * 60)
        lines.append(analytics.rationale)
        lines.append("")
        lines.append(_WARNING)
        return "\n".join(lines)


def _format_stats(
    stats: PaperTradePerformanceStatistics,
    precision: int,
    lines: list[str],
    indent: str = "",
) -> None:
    lines.append(f"{indent}Total trades       : {stats.total}")
    lines.append(
        f"{indent}Lifecycle          : "
        f"waiting={stats.waiting} open={stats.open} closed={stats.closed} "
        f"cancelled={stats.cancelled} invalidated={stats.invalidated}"
    )
    lines.append(
        f"{indent}Outcomes           : wins={stats.wins} losses={stats.losses} "
        f"ambiguous={stats.ambiguous} expired={stats.expired} "
        f"manual={stats.manual_close}"
    )
    lines.append(f"{indent}Win rate           : {_fmt_rate(stats.win_rate)}")
    lines.append(f"{indent}Loss rate          : {_fmt_rate(stats.loss_rate)}")
    lines.append(f"{indent}Total realized R   : {_fmt_r(stats.total_realized_r, precision)}")
    lines.append(f"{indent}Average realized R : {_fmt_r(stats.average_realized_r, precision)}")
    lines.append(f"{indent}Median realized R  : {_fmt_r(stats.median_realized_r, precision)}")
    lines.append(f"{indent}Gross positive R   : {_fmt_r(stats.gross_positive_r, precision)}")
    lines.append(f"{indent}Gross negative R   : {_fmt_r(stats.gross_negative_r, precision)}")
    lines.append(f"{indent}Profit factor      : {_fmt_ratio(Decimal(str(stats.profit_factor)) if stats.profit_factor is not None else None)}")
    lines.append(f"{indent}Valid R count      : {stats.valid_r_count}")
    lines.append(f"{indent}Total realized P&L : {_fmt_money(stats.total_realized_pnl, precision)}")
    lines.append(f"{indent}Average P&L        : {_fmt_money(stats.average_realized_pnl, precision)}")
    lines.append(f"{indent}Valid P&L count    : {stats.valid_pnl_count}")


__all__ = [
    "PaperTradeFormatter",
    "PaperTradeJournalFormatter",
    "PaperTradePerformanceFormatter",
]
