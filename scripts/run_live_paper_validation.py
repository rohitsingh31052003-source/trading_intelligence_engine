#!/usr/bin/env python3
"""
Operator-facing CLI: run exactly ONE Phase 6F live paper validation cycle.

This script is a THIN command-line interface over the EXISTING Product
Phase 6F live paper validation workflow
(:class:`dashboard.live_validation.LivePaperValidation`). It implements
NO trading intelligence, NO scoring, NO prediction, NO signal / geometry
/ decision / risk / execution logic and NO paper-trading lifecycle
logic. The existing decision engine, trade geometry, trade plan,
historical evidence lookup, duplicate prevention, persistence, failure
isolation and lifecycle tracking remain AUTHORITATIVE and are reused
unchanged.

The cycle answers, per instrument:

    "When the system sees a current market state, what did historical
    research say about similar setups, and how does that historical
    expectation compare with the actual subsequent paper-trading
    outcome?"

Usage::

    python scripts/run_live_paper_validation.py
    python scripts/run_live_paper_validation.py \
        --instruments NIFTY,RELIANCE --timeframe 15m \
        --capital 100000 --risk-percent 1

Provider selection: when ``DASHBOARD_PROVIDER`` is not already set in
the environment, this CLI defaults it to ``yahoo`` (live / near-live).
An explicitly supplied value is NEVER overwritten.

Persistence: paper trades go to the existing default paper-trade
directory; Phase 6D historical research is read from the existing
default setup-research directory; validation observations go to the
dedicated live-validation directory (overridable via flags).

SCHEDULER INTEGRATION: this CLI (and the underlying
:meth:`LivePaperValidation.run_once`) is the single explicit entry point
the existing market-session scheduler / keep-awake launcher can invoke
on the same cadence as the existing paper-trading cycle — no second
scheduler is created.

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

from dashboard.universe import COMBINED_UNIVERSE  # noqa: E402

#: Default monitored universe: the NIFTY 50 ∪ SENSEX constituents
#: (de-duplicated) plus the pre-existing NIFTY benchmark index
#: instrument — the EXISTING configured universe, nothing hard-coded
#: in the validation layer.
DEFAULT_INSTRUMENTS: tuple[str, ...] = ("NIFTY",) + COMBINED_UNIVERSE
DEFAULT_TIMEFRAME = "15m"
DEFAULT_CAPITAL = "100000"
DEFAULT_RISK_PERCENT = "1"

PAPER_TRADING_BANNER = "NO REAL ORDERS ARE SENT — PAPER TRADING ONLY"
DESCRIPTIVE_BANNER = (
    "Historical evidence is observational research context. It does not "
    "predict future returns, guarantee profitability, or override the "
    "authoritative decision engine."
)

_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# ARGUMENT PARSING (CLI boundary only — no business logic)
# ---------------------------------------------------------------------------


def _positive_decimal(text: str) -> str:
    """argparse type: accept a positive, finite decimal; keep it a string."""

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
        prog="run_live_paper_validation",
        description=(
            "Run exactly ONE Phase 6F live paper validation cycle using "
            "the existing analysis / decision / geometry / trade-plan / "
            "historical-evidence / paper-trade lifecycle. "
            + PAPER_TRADING_BANNER
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
    parser.add_argument(
        "--research-dir",
        default=None,
        dest="research_dir",
        help=(
            "Directory of the persisted Phase 6D setup research "
            "(default: ./data/setup_research)."
        ),
    )
    parser.add_argument(
        "--validation-dir",
        default=None,
        dest="validation_dir",
        help=(
            "Directory for Phase 6F validation observations "
            "(default: ./data/live_validation)."
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
# REPORT FORMATTING (presentation only — every value reused from the result)
# ---------------------------------------------------------------------------


def _fmt_ts(value: Any) -> str:
    return _UNAVAILABLE if value is None else value.isoformat()


def _fmt_float(value: Any) -> str:
    return _UNAVAILABLE if value is None else f"{value:.2f}"


def format_cycle_report(
    result: Any,
    *,
    timeframe: str,
    capital: str,
    risk_percent: str,
) -> str:
    """Render a :class:`LiveValidationCycleResult` as a readable report.

    Pure formatting: every value is read verbatim from the existing
    result; timeframe / capital / risk percent are the CLI-supplied
    request parameters. Nothing is recomputed.
    """

    from engine.reporting.live_validation import LiveValidationFormatter

    lines: list[str] = []
    add = lines.append

    add("=" * 72)
    add("LIVE PAPER VALIDATION CYCLE (PRODUCT PHASE 6F)")
    add(PAPER_TRADING_BANNER)
    add(DESCRIPTIVE_BANNER)
    add("=" * 72)
    add(f"Cycle id:                  {result.cycle_id or _UNAVAILABLE}")
    add(f"Operations cycle:          {result.operations_cycle_id or _UNAVAILABLE}")
    add(f"Provider:                  {result.provider or _UNAVAILABLE}")
    add(f"Timeframe (setup):         {timeframe}")
    add(f"Account capital:           {capital}")
    add(f"Risk percent:              {risk_percent}%")
    add(f"Cycle status:              {result.status.value}")
    add(f"Reference / evaluation at: {_fmt_ts(result.reference_now)}")
    add(f"Instruments scanned:       {result.instruments_scanned}")
    add(f"Observations recorded:     {result.observations_recorded}")
    add(f"Observations advanced:     {result.observations_updated}")
    add(f"Paper trades created:      {result.paper_trades_created}")
    add(f"Duplicates skipped:        {result.duplicates_skipped}")
    add("")
    add(LiveValidationFormatter().format_cycle(result))
    add("")
    add("=" * 72)
    add(PAPER_TRADING_BANNER)
    add("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ENTRY POINT (orchestration only)
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run exactly one live paper validation cycle. Returns exit code."""

    args = parse_args(argv)
    try:
        instruments = parse_instruments(args.instruments)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    provider_name = _resolve_provider_name()

    try:
        from dashboard.live_validation import (
            LivePaperValidation,
            LiveValidationConfig,
        )
        from dashboard.live_validation_store import LiveValidationStore
        from dashboard.paper_trade_store import (
            PaperTradeStore,
            default_paper_trade_directory,
        )
        from dashboard.services import HistoricalEvidenceSource, default_service
        from engine.data.setup_research_store import (
            SetupResearchStore,
            default_setup_research_directory,
        )
    except ImportError as exc:  # pragma: no cover - environment failure
        print(f"error: failed to import components: {exc}", file=sys.stderr)
        return 1

    paper_store = PaperTradeStore(directory=default_paper_trade_directory())
    research_store = SetupResearchStore(
        args.research_dir or default_setup_research_directory()
    )
    validation_store = LiveValidationStore(args.validation_dir)

    try:
        service = default_service(
            provider_name=provider_name,
            paper_trade_store=paper_store,
            historical_evidence_source=HistoricalEvidenceSource(research_store),
        )
        validation = LivePaperValidation(
            service,
            config=LiveValidationConfig(
                account_capital=args.capital,
                risk_percent=args.risk_percent,
                setup_timeframe=args.timeframe,
                label="cli-live-paper-validation",
            ),
            validation_store=validation_store,
        )
        result = validation.run_once(instruments=instruments)
    except Exception as exc:  # noqa: BLE001 - CLI boundary failure reporting
        print(
            f"error: live paper validation cycle could not be executed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        format_cycle_report(
            result,
            timeframe=args.timeframe,
            capital=args.capital,
            risk_percent=args.risk_percent,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
