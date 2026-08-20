#!/usr/bin/env python3
"""
Operator-facing CLI: run exactly ONE paper-trading operations cycle.

This script is a THIN command-line interface over the EXISTING Product
Phase 5 paper-trading operations workflow
(:class:`dashboard.paper_trade_operations.PaperTradingOperations`, reached
through :meth:`dashboard.services.DashboardAnalysisService.run_paper_trading_cycle`
with an :class:`dashboard.services.OperationsRequest`). It implements NO
trading intelligence, NO scoring, NO prediction, NO signal / geometry /
decision / risk / execution logic, and NO paper-trading lifecycle logic.
The existing decision engine, trade geometry, trade plan, duplicate
prevention, persistence, failure isolation and lifecycle tracking remain
AUTHORITATIVE and are reused unchanged.

Usage::

    python scripts/run_paper_trading_cycle.py
    python scripts/run_paper_trading_cycle.py \
        --instruments NIFTY,RELIANCE --timeframe 15m \
        --capital 100000 --risk-percent 1

Provider selection: when ``DASHBOARD_PROVIDER`` is not already set in the
environment, this CLI defaults it to ``yahoo`` (live / near-live). An
explicitly supplied value is NEVER overwritten.

Paper trades are persisted to the existing default paper-trade directory
(:func:`dashboard.paper_trade_store.default_paper_trade_directory`).

NO REAL ORDERS ARE SENT — PAPER TRADING ONLY.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

DEFAULT_INSTRUMENTS: tuple[str, ...] = (
    "NIFTY",
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
)
DEFAULT_TIMEFRAME = "15m"
DEFAULT_CAPITAL = "100000"
DEFAULT_RISK_PERCENT = "1"

PAPER_TRADING_BANNER = "NO REAL ORDERS ARE SENT — PAPER TRADING ONLY"

_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# ARGUMENT PARSING (CLI boundary only — no business logic)
# ---------------------------------------------------------------------------


def _positive_decimal(text: str) -> str:
    """argparse type: accept a positive, finite decimal; keep it a string.

    The string is passed through unchanged so the existing Product Phase 4
    planner coerces it to ``Decimal`` itself (no float money). This is CLI
    input validation only; the planner's full risk rules stay authoritative.
    """

    try:
        value = Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        raise argparse.ArgumentTypeError(f"{text!r} is not a number")
    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a positive number")
    return text.strip()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. No argument is required for normal use."""

    parser = argparse.ArgumentParser(
        prog="run_paper_trading_cycle",
        description=(
            "Run exactly ONE paper-trading operations cycle using the "
            "existing analysis / decision / geometry / trade-plan / "
            "paper-trade lifecycle. " + PAPER_TRADING_BANNER
        ),
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_INSTRUMENTS),
        help=(
            "Comma-separated instrument watchlist "
            f"(default: {','.join(DEFAULT_INSTRUMENTS)})."
        ),
    )
    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        help=f"Setup timeframe (default: {DEFAULT_TIMEFRAME}).",
    )
    parser.add_argument(
        "--capital",
        type=_positive_decimal,
        default=DEFAULT_CAPITAL,
        help=f"Account capital for risk sizing (default: {DEFAULT_CAPITAL}).",
    )
    parser.add_argument(
        "--risk-percent",
        type=_positive_decimal,
        default=DEFAULT_RISK_PERCENT,
        dest="risk_percent",
        help=(
            "Per-trade account risk percentage "
            f"(default: {DEFAULT_RISK_PERCENT})."
        ),
    )
    return parser.parse_args(argv)


def parse_instruments(raw: str) -> tuple[str, ...]:
    """Parse the comma-separated watchlist into canonical instrument names."""

    instruments = tuple(
        dict.fromkeys(
            part.strip().upper() for part in raw.split(",") if part.strip()
        )
    )
    if not instruments:
        raise ValueError("--instruments produced an empty watchlist")
    return instruments


def _resolve_provider_name() -> str:
    """Default ``DASHBOARD_PROVIDER`` to ``yahoo`` without overwriting."""

    os.environ.setdefault("DASHBOARD_PROVIDER", "yahoo")
    return os.environ["DASHBOARD_PROVIDER"]


# ---------------------------------------------------------------------------
# REPORT FORMATTING (presentation only — every value reused from the view)
# ---------------------------------------------------------------------------


def _fmt_ts(value: Any) -> str:
    return _UNAVAILABLE if value is None else value.isoformat()


def _fmt_money(text: str) -> str:
    try:
        return f"{Decimal(text):,}"
    except InvalidOperation:
        return text


def _geometry_label(row: Any) -> str:
    """Presentation label for geometry availability from existing fields."""

    if row.error:
        return "not assessed (instrument error)"
    if row.actionability == "TRADE_GEOMETRY_UNAVAILABLE":
        return _UNAVAILABLE
    if row.eligible_for_paper_trade or row.actionability == "READY_FOR_REVIEW":
        return "complete"
    if not row.analysed:
        return "not assessed (not analysed)"
    return "not applicable (no eligible opportunity)"


def format_cycle_report(
    view: Any,
    *,
    timeframe: str,
    capital: str,
    risk_percent: str,
) -> str:
    """Render an :class:`OperationsCycleView` as a human-readable report.

    Pure formatting: every cycle value is read verbatim from the existing
    view; timeframe / capital / risk percent are the CLI-supplied request
    parameters. Nothing is recomputed.
    """

    lines: list[str] = []
    add = lines.append

    add("=" * 72)
    add("PAPER TRADING OPERATIONS CYCLE")
    add(PAPER_TRADING_BANNER)
    add("=" * 72)
    add(f"Cycle id:                  {view.cycle_id or _UNAVAILABLE}")
    add(f"Provider:                  {view.provider or _UNAVAILABLE}")
    add(f"Timeframe (setup):         {timeframe}")
    add(f"Account capital:           {_fmt_money(capital)}")
    add(f"Risk percent:              {risk_percent}%")
    add(f"Cycle status:              {view.status}")
    add(f"Freshness:                 {view.freshness or _UNAVAILABLE}")
    add(f"Reference / evaluation at: {_fmt_ts(view.reference_now)}")
    add(f"Instruments scanned:       {view.instruments_scanned}")
    add(f"Instruments analyzed:      {view.instruments_analysed}")
    add("")
    add("-" * 72)
    add("PER-INSTRUMENT OUTCOMES")
    add("-" * 72)
    if not view.results:
        add("  (no instruments produced an outcome this cycle)")
    for row in view.results:
        add(f"  {row.instrument or _UNAVAILABLE}")
        add(
            "    Decision classification: "
            f"{row.decision_classification or _UNAVAILABLE}"
        )
        add(
            "    Actionability/opportunity: "
            f"{row.actionability or _UNAVAILABLE}"
        )
        add(f"    Direction:               {row.direction or _UNAVAILABLE}")
        add(f"    Geometry:                {_geometry_label(row)}")
        if row.error:
            add("    Instrument error:        YES (failure isolated)")
        if row.created:
            add(f"    Paper trade created:     YES ({', '.join(row.created)})")
        else:
            add("    Paper trade created:     no")
        if row.duplicate:
            add(
                "    Duplicate:               YES — skipped "
                f"(existing {row.duplicate_paper_trade_id or _UNAVAILABLE})"
            )
        if row.updated:
            add(f"    Trades updated:          {', '.join(row.updated)}")
        if row.closed:
            add(f"    Trades closed:           {', '.join(row.closed)}")
        add(f"    Reason:                  {row.reason or _UNAVAILABLE}")
    add("")
    add("-" * 72)
    add("CYCLE TOTALS")
    add("-" * 72)
    add(f"Trades created:            {view.trades_created}")
    add(f"Trades updated:            {view.trades_updated}")
    add(f"Trades closed:             {view.trades_closed}")
    add(f"Duplicates skipped:        {view.duplicates_skipped}")
    add(f"Active trades:             {view.active_trades}")
    add("")
    add(f"Errors ({len(view.errors)}):")
    if view.errors:
        for err in view.errors:
            add(f"  - {err}")
    else:
        add("  (none)")
    add(f"Warnings ({len(view.warnings)}):")
    if view.warnings:
        for warning in view.warnings:
            add(f"  - {warning}")
    else:
        add("  (none)")
    if view.rationale:
        add("")
        add(f"Rationale: {view.rationale}")
    add("")
    add("=" * 72)
    add(PAPER_TRADING_BANNER)
    add("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ENTRY POINT (orchestration only)
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run exactly one paper-trading operations cycle. Returns exit code."""

    args = parse_args(argv)
    try:
        instruments = parse_instruments(args.instruments)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    provider_name = _resolve_provider_name()

    try:
        from dashboard.paper_trade_store import (
            PaperTradeStore,
            default_paper_trade_directory,
        )
        from dashboard.services import OperationsRequest, default_service
    except ImportError as exc:  # pragma: no cover - environment failure
        print(f"error: failed to import dashboard components: {exc}",
              file=sys.stderr)
        return 1

    store = PaperTradeStore(directory=default_paper_trade_directory())

    try:
        service = default_service(
            provider_name=provider_name,
            paper_trade_store=store,
        )
        view = service.run_paper_trading_cycle(
            OperationsRequest(
                watchlist=instruments,
                setup_timeframe=args.timeframe,
                account_capital=args.capital,
                risk_percent=args.risk_percent,
                label="cli-paper-trading-cycle",
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary failure reporting
        print(
            f"error: paper-trading cycle could not be executed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        format_cycle_report(
            view,
            timeframe=args.timeframe,
            capital=args.capital,
            risk_percent=args.risk_percent,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
