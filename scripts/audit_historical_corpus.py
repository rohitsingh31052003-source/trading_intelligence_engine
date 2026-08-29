#!/usr/bin/env python3
"""
Operator-facing CLI: READ-ONLY HISTORICAL CORPUS INTEGRITY AUDIT
(Checkpoint 3B).

This script inspects the EXISTING persisted historical corpus
(Product Phase 6A / Checkpoint 3B historical store) and reports its
condition WITHOUT modifying anything. It is strictly
READ -> CHECK -> REPORT:

* NEVER calls the Upstox API and NEVER requires ``UPSTOX_ANALYTICS_TOKEN``.
* NEVER ingests data, NEVER writes / repairs / rewrites candle files.
* NEVER silently discards invalid candles and NEVER normalizes / "fixes"
  bad OHLC data.
* Leaves the planner, the provider and every prediction / evidence /
  trading / replay component untouched.

It implements NO trading / scoring / prediction / decision / evidence
logic. The audit reuses the existing domain logic: the Phase 6B store
loaders, the canonical ``OHLCVCandle`` / ``DataValidator`` contract, the
Phase 6A gap-detection logic and the Checkpoint 3B planner coverage
semantics.

Usage::

    python scripts/audit_historical_corpus.py \
        --start 2023-01-01 --end 2026-08-01 \
        --timeframes 15m 1D \
        --instruments HDFCBANK ICICIBANK NIFTY RELIANCE TCS

Defaults follow the established research corpus configuration
(15m + 1D over the configured research universe).

Exit codes: 0 when the audit ran and concluded PASS or REVIEW REQUIRED
(a data-quality / completeness finding is reported honestly, NEVER
treated as CLI failure); 1 on a service/runtime exception or an invalid
audit window; 2 for bad CLI args.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from datetime import UTC, datetime  # noqa: E402

from engine.config.corpus_plan_config import (  # noqa: E402
    CorpusPlanConfig,
)
from engine.data.corpus_audit import (  # noqa: E402
    CorpusAuditEngine,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
    default_historical_data_directory,
)
from engine.reporting.corpus_audit import (  # noqa: E402
    CorpusAuditFormatter,
)

_BANNER = (
    "READ-ONLY AUDIT — no fetch, no token, no writes, no repair. "
    "Descriptive reporting only; no prediction / trading recommendation."
)

_DEFAULT_PROVIDER = "upstox-historical"
_DEFAULT_TIMEFRAMES = ("15m", "1D")


def _parse_timestamp(value: str) -> datetime:
    """Parse YYYY-MM-DD or ISO timestamps as UTC-aware (never naive)."""

    try:
        ts = datetime.fromisoformat(value.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid timestamp {value!r} (expected YYYY-MM-DD or ISO)."
        )
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def build_audit_engine(
    *,
    data_directory: str | Path | None = None,
    timeframes: tuple[str, ...] = _DEFAULT_TIMEFRAMES,
    instruments: Sequence[str] | None = None,
) -> CorpusAuditEngine:
    """
    Build the audit engine over the EXISTING historical store.

    No provider is instantiated here: the audit NEVER needs one. The
    provider name is used only for the (harmless, unused elsewhere)
    planner config convention — the audit engine itself reads only the
    store.
    """

    store = HistoricalDataStore(
        data_directory or default_historical_data_directory(),
    )
    config = CorpusPlanConfig(
        timeframes=timeframes,
        provider=_DEFAULT_PROVIDER,
    )
    return CorpusAuditEngine(
        config=config,
        store=store,
        instruments=instruments,
    )


def format_report(report) -> str:
    """Render the audit report from an existing report (format only)."""

    return CorpusAuditFormatter().format(report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only historical corpus integrity audit (Checkpoint 3B).",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help=(
            "space- or comma-separated timeframes (default 15m 1D). "
            "Example: --timeframes 15m 1D or --timeframes 15m,1D."
        ),
    )
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=None,
        help=(
            "space- or comma-separated instrument list (default: the "
            "configured research universe)."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        help="audit window start (YYYY-MM-DD or ISO).",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="audit window end (YYYY-MM-DD or ISO).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="storage root to audit (default ./data/historical).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print only the JSON audit projection.",
    )
    args = parser.parse_args(argv)

    def _split(values: list[str] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        parts: list[str] = []
        for value in values:
            for piece in value.split(","):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
        return tuple(parts) if parts else None

    if args.timeframes is None:
        timeframes = _DEFAULT_TIMEFRAMES
    else:
        timeframes = _split(args.timeframes)
        if not timeframes:
            print("ERROR: --timeframes must not be empty.", file=sys.stderr)
            return 2
    instruments = _split(args.instruments)
    try:
        start = _parse_timestamp(args.start)
        end = _parse_timestamp(args.end)
    except argparse.ArgumentTypeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        engine = build_audit_engine(
            data_directory=args.data_dir,
            timeframes=timeframes,
            instruments=instruments,
        )
        report = engine.audit(label="", start=start, end=end)
    except (ValueError, KeyError) as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(engine.audit_to_jsonable(report), sort_keys=True))
        return 0
    print(_BANNER)
    print("")
    print(format_report(report))
    print("")
    print(_BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main())