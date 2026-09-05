#!/usr/bin/env python3
"""
Operator-facing CLI for the Checkpoint 19.3 CONTInUOUS MARKET SCANNER.

A THIN command-line interface over the EXISTING 19.2 intraday coverage
layer + the NEW 19.3 continuous scan-cycle orchestration. It answers,
for one or several scan cycles, "what is the current market-data state
of every requested NIFTY Top 200 constituent?".

The CLI implements NO trading intelligence:

* NO setup detection, NO technical signals, NO scoring, NO ranking,
  NO trade plans, NO entry/stop/target, NO alerts, NO broker execution.
* A scan cycle reports MARKET STATE / DATA AVAILABILITY only.

Behaviour by default is FULLY OFFLINE and DETERMINISTIC:

* ``--provider fixture`` (default) uses the local deterministic
  fixtures (no network, no API key) and scans ``15m``. Only the 4
  fixture instruments carry data; the remaining constituents are
  honestly reported (unsupported), and the cycle is PARTIAL (never a
  fabricated full scan).

LIVE scanning is OPT-IN and separated:

* ``--provider yahoo`` uses the OPTIONAL live/near-live Yahoo provider
  (requires ``yfinance``; makes real network requests). A full
  200-instrument Yahoo run is a real network operation with many
  requests — only run it deliberately on the operator's machine.

Modes:

* ``--once`` (default) runs exactly ONE scan cycle and exits.
* ``--cycles N`` runs N cycles with the continuous loop
  (``--interval SECONDS`` controls the spacing; the default 900s is
  documented and intentionally conservative).
* ``--timeout SECONDS`` bounds the whole CLI run (safety valve).

``--reference-now`` (ISO-8601, UTC) forces a deterministic reference
time; the default is a fixed deterministic sentinel for fixture runs
(fully reproducible) and wall-clock for live runs.

Exit codes: 0 whenever the scan ran (per-instrument findings are
reported honestly, never treated as CLI failure); 2 for bad args; 1 on
runtime failure. Banner: MARKET-DATA SCANNING ONLY — no prediction, no
setups, no broker execution.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Deterministic reference time used for default FIXTURE runs (a Monday
#: 11:00 IST trading-weekday instant) so the fixture path never depends
#: on wall-clock time and its freshness grading is reproducible.
_FIXTURE_DETERMINISTIC_NOW = datetime(2026, 9, 7, 5, 30, tzinfo=UTC)  # 11:00 IST Mon


def _parse_reference_now(raw: str | None, provider: str) -> datetime:
    if not raw:
        if provider == "fixture":
            return _FIXTURE_DETERMINISTIC_NOW
        return datetime.now(UTC)
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --reference-now {raw!r}: {exc}",
        ) from exc
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "--reference-now must be timezone-aware (add an offset).",
        )
    return value.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scan_market.py",
        description=(
            "NIFTY Top 200 continuous market-data scanner "
            "(Checkpoint 19.3). Descriptive market-state scanning only."
        ),
    )
    parser.add_argument(
        "--provider",
        default="fixture",
        choices=("fixture", "yahoo"),
        help=(
            "Market-data provider: 'fixture' (default, deterministic, "
            "offline, 15m only) or 'yahoo' (OPT-IN live/near-live; "
            "requires yfinance; makes real network requests)."
        ),
    )
    parser.add_argument(
        "--timeframe",
        default="15m",
        help="Canonical intraday timeframe to scan (default 15m).",
    )
    parser.add_argument(
        "--instruments",
        default=None,
        help=(
            "Comma-separated instrument list (default: the full NIFTY "
            "Top 200 universe from the 19.1 manifest)."
        ),
    )
    parser.add_argument(
        "--reference-now",
        default=None,
        help=(
            "ISO-8601 timezone-aware reference instant (UTC recommended). "
            "Deterministic fixture default when omitted."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run exactly ONE scan cycle and exit (default).",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=None,
        help="Run N scan cycles with the continuous loop.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=900.0,
        help="Scan interval in seconds between cycle starts (default 900).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Overall CLI time budget in seconds (default 120).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable cycle projection(s) as JSON.",
    )
    return parser


def _cycle_to_jsonable(result) -> dict:
    meta = result.metadata
    return {
        "cycle_id": result.cycle_id,
        "status": result.status.value,
        "reference_now": result.reference_now.isoformat(),
        "timeframe": result.timeframe,
        "source": result.source.value,
        "manual_run_id": result.manual_run_id,
        "requested": meta.requested_universe_size if meta else None,
        "attempted": meta.attempted_instrument_count if meta else None,
        "successful": result.successful_count,
        "unavailable": result.unavailable_count,
        "failed": result.failed_count,
        "duration_seconds": meta.duration_seconds if meta else None,
        "market_session": (
            result.market_session.value
            if result.market_session is not None
            else None
        ),
        "error": result.error or None,
        "instruments": [
            {
                "instrument": s.instrument,
                "status": s.status.value,
                "available": s.available,
                "fresh": s.fresh,
                "latest_price": s.latest_price,
                "latest_bar_timestamp": (
                    s.latest_bar_timestamp.isoformat()
                    if s.latest_bar_timestamp
                    else None
                ),
            }
            for s in result.results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        reference_now = _parse_reference_now(args.reference_now, args.provider)
    except argparse.ArgumentTypeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Resolve the universe: explicit list or the validated NIFTY Top 200.
    from engine.config.universe_boundary import (
        DEFAULT_NIFTY200_UNIVERSE,
        UniverseBuilder,
    )

    if args.instruments:
        names = [n.strip().upper() for n in args.instruments.split(",")]
        names = [n for n in names if n]
        if not names:
            print("ERROR: --instruments produced an empty list.", file=sys.stderr)
            return 2
        universe = UniverseBuilder.custom(names, label="cli explicit")
        universe_label = f"custom ({len(names)})"
    else:
        universe = DEFAULT_NIFTY200_UNIVERSE
        universe_label = "NIFTY Top 200"

    from dashboard.continuous_scanner import ContinuousScanner, ContinuousScannerEngine
    from engine.models.continuous_scan import ContinuousScanConfig
    from engine.reporting.continuous_scan import MarketScanCycleFormatter

    engine = ContinuousScannerEngine.build(
        args.provider, timeframe=args.timeframe,
    )
    cfg = ContinuousScanConfig(
        scan_interval_seconds=max(float(args.interval), 0.01),
        skip_intervals_on_overrun=True,
    )
    scanner = ContinuousScanner(
        engine=engine,
        config=cfg,
        universe=universe,
        clock=lambda: reference_now,
        waiter=lambda s: None,  # deterministic: the loop advances instantly
    )

    cycle_count = args.cycles if args.cycles is not None else 1

    # Bounded overall wall-clock budget.
    start_at = time.monotonic()
    results: list = []
    for _ in range(cycle_count):
        if time.monotonic() - start_at > float(args.timeout):
            print("ERROR: overall --timeout reached; stopping early.", file=sys.stderr)
            break
        result = scanner.scan_once(reference_now=reference_now)
        results.append(result)

    if args.json:
        payload = [ _cycle_to_jsonable(r) for r in results ]
        if len(payload) == 1:
            payload = payload[0]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        formatter = MarketScanCycleFormatter()
        print(
            f"[i] universe  : {universe_label} "
            f"({results[0].metadata.requested_universe_size if results and results[0].metadata else 'n/a'} instruments)"
            f"  provider={args.provider}  timeframe={args.timeframe}"
        )
        for idx, result in enumerate(results, start=1):
            print(f"\n=== SCAN CYCLE {idx}/{len(results)} ===")
            print(formatter.format_summary(result))
            print(formatter.format(result))
        last = results[-1] if results else None
        if last is not None:
            print(
                f"\n[done] cycles={len(results)} last_status={last.status.value} "
                f"successful={last.successful_count} "
                f"unavailable={last.unavailable_count} "
                f"failed={last.failed_count}"
            )
            print(
                "\nMARKET-DATA SCANNING ONLY — no prediction, no setups, "
                "no ranking, no broker execution.",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())