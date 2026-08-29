#!/usr/bin/env python3
"""
Operator-facing CLI: build the historical CORPUS-PREPARATION PLAN
(Checkpoint 3B — Historical Corpus Preparation).

This script is a THIN command-line interface over the PLANNER only. It
COMPUTES the deterministic ingestion plan for a research corpus
(universe × timeframes × monthly window chunks), reports the CURRENT
stored coverage (Phase 6B store) and prints the exact missing chunk
requests an operator still has to fetch. It implements NO trading
intelligence, NO scoring, NO prediction, NO decision / geometry /
evidence logic, and it NEVER fetches market data itself.

Planned data must still be ingested explicitly per request through the
existing ``scripts/ingest_historical_data.py`` CLI (the Phase 6B
pipeline) — the planner never issues an HTTP request.

Usage::

    # Plan the default research universe (15m + 1D) for one year.
    python scripts/prepare_corpus_data.py --start 2024-01-01 --end 2024-12-31

    # Plan the confirmed Upstox research universe for the same window.
    python scripts/prepare_corpus_data.py --provider upstox-historical \\
        --start 2024-01-01 --end 2024-12-31

    # Print ONLY the JSON plan (for machine consumption).
    python scripts/prepare_corpus_data.py --start ... --end ... --json

Exit codes: 0 when the plan was built successfully (missing chunks are
reported honestly, never treated as CLI failure); 1 on a service/runtime
exception or an invalid plan window; 2 for bad CLI args.

PLANNING + DATA PREPARATION ONLY — no prediction, no evidence, no
automatic ingestion.
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
from engine.data.corpus_plan import (  # noqa: E402
    CorpusPreparationPlanner,
)
from engine.reporting.corpus_plan import (  # noqa: E402
    CorpusPreparationFormatter,
)
from engine.data.historical_provider import (  # noqa: E402
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
    default_historical_data_directory,
)

_BANNER = (
    "PLANNING ONLY — the plan names missing requests; use "
    "ingest_historical_data.py to fetch them. No prediction, no evidence."
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
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def build_planner(
    provider_name: str = _DEFAULT_PROVIDER,
    *,
    data_directory: str | Path | None = None,
    timeframes: tuple[str, ...] = _DEFAULT_TIMEFRAMES,
) -> CorpusPreparationPlanner:
    """Build the planner with the selected provider + store."""

    if provider_name == "upstox-historical":
        provider = UpstoxHistoricalDataProvider()
    else:
        provider = None
    store = HistoricalDataStore(
        data_directory or default_historical_data_directory(),
    )
    config = CorpusPlanConfig(
        timeframes=timeframes,
        provider=provider_name,
    )
    return CorpusPreparationPlanner(
        config=config,
        store=store,
        provider=provider,
    )


def format_report(plan) -> str:
    """Render the plan report from an existing plan (format only)."""

    formatter = CorpusPreparationFormatter()
    return formatter.format(plan)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the historical corpus-preparation plan (Checkpoint 3B).",
    )
    parser.add_argument(
        "--provider",
        default=_DEFAULT_PROVIDER,
        help=(
            "provider name whose capability gates the plan "
            "(default upstox-historical)."
        ),
    )
    parser.add_argument(
        "--timeframes",
        default=",".join(_DEFAULT_TIMEFRAMES),
        help="comma-separated timeframes (default 15m,1D).",
    )
    parser.add_argument(
        "--instruments",
        default="",
        help=(
            "comma-separated instrument allow-list (default: the "
            "configured research universe)."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        help="plan window start (YYYY-MM-DD or ISO).",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="plan window end (YYYY-MM-DD or ISO).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="storage root (default ./data/historical).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print only the JSON plan projection.",
    )
    args = parser.parse_args(argv)

    timeframes = tuple(
        tf.strip() for tf in args.timeframes.split(",") if tf.strip()
    )
    if not timeframes:
        print("ERROR: --timeframes must not be empty.", file=sys.stderr)
        return 2
    instruments = (
        tuple(name.strip() for name in args.instruments.split(",") if name.strip())
        if args.instruments
        else None
    )
    try:
        start = _parse_timestamp(args.start)
        end = _parse_timestamp(args.end)
    except argparse.ArgumentTypeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        planner = build_planner(
            args.provider,
            data_directory=args.data_dir,
            timeframes=timeframes,
        )
        plan = planner.plan(
            instruments,
            start=start,
            end=end,
            label="",
        )
    except (ValueError, KeyError) as exc:
        print(f"PLAN FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(planner.plan_to_jsonable(plan), sort_keys=True))
        return 0
    print(_BANNER)
    print("")
    print(format_report(plan))
    print("")
    print(_BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main())