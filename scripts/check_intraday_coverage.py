"""
Checkpoint 19.2 — intraday market-data coverage diagnostic CLI.

A THIN operator-facing CLI over the EXISTING
:class:`dashboard.intraday_coverage.IntradayCoverageEngine`. It answers
the Checkpoint 19.2 data-coverage questions for a universe
(requested size, supported, with-valid-data, stale, no-data, empty,
provider-errors, unsupported, malformed) with EXPLICIT counts.

The CLI performs NO trading, scoring, prediction, decision, ranking or
execution logic. It only reads provider capability declarations +
fetches/normalizes/validates intraday candles through the EXISTING
provider abstraction and reports classified status. No broker
credentials are required. No broker execution is triggered.

By default the run is FULLY OFFLINE and DETERMINISTIC:

* ``--provider fixture`` (default) uses the local deterministic
  fixtures (no network, no API key) and grades ``15m``. It proves the
  coverage layer against the NIFTY Top 200 with honest results (only
  the 4 fixture instruments carry data; the remaining 196 are
  unsupported — never fabricated).

LIVE validation is OPT-IN and separated:

* ``--provider yahoo`` uses the OPTIONAL live/near-live Yahoo provider
  (requires ``yfinance``; makes real network requests). It does a
  bounded recent-window fetch per instrument, so a full 200-instrument
  Yahoo run is a REAL NETWORK operation that will make many requests
  and should only be run deliberately on the operator's machine.
* For very small runs use ``--instruments RELIANCE,TCS``.

``--reference-now`` (ISO-8601, UTC) forces a deterministic reference
time; the default is the wall-clock UTC "now" for live runs and a fixed
deterministic sentinel for fixture runs (so fixture runs are always
deterministic).

Exit codes: 0 whenever the assessment ran (per-instrument coverage
findings are reported honestly, never treated as CLI failure);
2 for bad args; 1 on runtime failure.

Banner: HISTORICAL/DATA-ONLY — no prediction, no evidence computed, no
broker execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Deterministic reference time used for default FIXTURE runs (a Monday
#: 11:00 IST trading-weekday instant) so the fixture path never depends
#: on wall-clock time and its freshness grading is reproducible.
_FIXTURE_DETERMINISTIC_NOW = datetime(2026, 9, 7, 5, 30, tzinfo=UTC)  # 11:00 IST Mon


def _parse_reference_now(raw: str | None) -> datetime:
    if not raw:
        return _FIXTURE_DETERMINISTIC_NOW
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
        prog="check_intraday_coverage.py",
        description=(
            "NIFTY Top 200 intraday data-coverage diagnostic "
            "(Checkpoint 19.2). Descriptive data-quality only."
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
        help="Canonical intraday timeframe to grade (default 15m).",
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
        "--json",
        action="store_true",
        help="Emit the machine-readable coverage projection as JSON.",
    )
    return parser


def _jsonable(report) -> dict:
    counts = report.counts
    return {
        "provider": report.provider_name,
        "timeframe": report.timeframe,
        "universe_instrument_count": report.universe_instrument_count,
        "assessed": counts.tested,
        "supported": counts.supported,
        "with_valid_data": counts.with_valid_data,
        "valid": counts.valid,
        "valid_with_gaps": counts.valid_with_gaps,
        "stale": counts.stale,
        "no_data": counts.no_data,
        "empty": counts.empty,
        "unsupported_instrument": counts.unsupported_instrument,
        "unsupported_timeframe": counts.unsupported_timeframe,
        "temporarily_unavailable": counts.temporarily_unavailable,
        "provider_errors": counts.provider_errors,
        "invalid_responses": counts.invalid_responses,
        "needs_attention": counts.needs_attention,
        "coverage_ratio": round(report.coverage_ratio, 6),
        "reference_now": report.reference_now.isoformat(),
        "market_session": (
            report.market_session.value if report.market_session is not None else None
        ),
        "seconds_until_next_open": report.seconds_until_next_open,
        "instruments": [
            {
                "instrument": r.instrument,
                "status": r.status.value,
                "candle_count": r.candle_count,
                "first_timestamp": (
                    r.first_timestamp.isoformat() if r.first_timestamp else None
                ),
                "last_timestamp": (
                    r.last_timestamp.isoformat() if r.last_timestamp else None
                ),
                "stale": r.status.value == "STALE",
                "issues": [i.code for i in r.issues],
                "reason": r.reason,
            }
            for r in report.results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    raw_reference = args.reference_now or _FIXTURE_DETERMINISTIC_NOW
    if isinstance(raw_reference, str):
        try:
            reference_now = _parse_reference_now(raw_reference)
        except argparse.ArgumentTypeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        reference_now = raw_reference

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

    from dashboard.intraday_coverage import (
        IntradayCoverageEngine,
        IntradayCoverageFormatter,
    )

    engine = IntradayCoverageEngine.build(
        args.provider,
        timeframe=args.timeframe,
    )

    report = engine.assess_universe(universe, reference_now=reference_now)

    if args.json:
        print(json.dumps(_jsonable(report), indent=2, sort_keys=True))
    else:
        print(
            f"[i] universe        : {universe_label} "
            f"({report.universe_instrument_count} instruments)",
        )
        formatter = IntradayCoverageFormatter()
        print(formatter.format(report))

    counts = report.counts
    if not args.json:
        print(
            "\n[done] assessed={assessed} supported={supported} "
            "with_valid_data={valid} needs_attention={na} "
            "coverage_ratio={cr:.2f}".format(
                assessed=counts.tested,
                supported=counts.supported,
                valid=counts.with_valid_data,
                na=counts.needs_attention,
                cr=report.coverage_ratio,
            ),
        )
        print(
            "\nHISTORICAL/DATA-ONLY — no prediction, no evidence computed, "
            "no broker execution. Live validation requires an explicit "
            "--provider yahoo run.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())