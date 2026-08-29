#!/usr/bin/env python3
"""
Operator-facing CLI: run the SAFE, RESUMABLE historical CORPUS INGESTION
(Checkpoint 3B, step 2).

This script is a THIN command-line interface over the EXISTING
:class:`engine.data.corpus_ingestion.CorpusIngestionEngine` (which derives
its missing work from the EXISTING :class:`CorpusPreparationPlanner` and
executes each chunk through the EXISTING
:class:`HistoricalMarketDataService` / :class:`HistoricalDataStore`
pipeline). It implements NO trading intelligence, NO scoring, NO
prediction, NO decision / geometry / evidence logic, NO provider HTTP
client and NO second persistence database. The only intended side effect
is persistence of historical market data through the existing ingestion /
store pipeline.

Usage::

    python scripts/ingest_corpus_data.py \
        --start 2023-01-01 --end 2026-08-01 \
        --provider upstox-historical --timeframes 15m 1D

Provider selection: ``upstox-historical`` (default) performs the
``UPSTOX_ANALYTICS_TOKEN`` credential precheck BEFORE any ingestion
step. ``local-deterministic`` / ``in-memory-import`` are supported for
offline / test use (the precheck is skipped for them).

Exit codes: 0 when the run executed (any per-chunk failures are reported
honestly, never treated as CLI failure); 1 when the run could not be
started (missing credential / invalid window / runtime failure); 2 for bad
CLI args.

HISTORICAL DATA ONLY — no prediction, no evidence computation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from datetime import UTC, datetime  # noqa: E402

from engine.config.corpus_plan_config import CorpusPlanConfig  # noqa: E402
from engine.data.corpus_ingestion import (  # noqa: E402
    CorpusIngestionConfig,
    CorpusIngestionEngine,
    CorpusIngestionError,
    require_upstox_token,
)
from engine.data.corpus_plan import CorpusPreparationPlanner  # noqa: E402
from engine.data.historical_provider import (  # noqa: E402
    DeterministicLocalHistoricalProvider,
    InMemoryHistoricalProvider,
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_service import (  # noqa: E402
    HistoricalMarketDataService,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
    default_historical_data_directory,
)

DEFAULT_PROVIDER = "upstox-historical"
DEFAULT_TIMEFRAMES = ("15m", "1D")

_BANNER = "HISTORICAL DATA ONLY — no prediction, no evidence computed"

_PROVIDERS = {
    "upstox-historical": UpstoxHistoricalDataProvider,
    "local-deterministic": DeterministicLocalHistoricalProvider,
    "in-memory-import": InMemoryHistoricalProvider,
}


def _parse_timestamp(value: str) -> datetime:
    """Parse YYYY-MM-DD or ISO timestamps as UTC-aware (never naive)."""

    try:
        ts = datetime.fromisoformat(value.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid timestamp {value!r} (expected YYYY-MM-DD or ISO).",
        )
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def build_planner_and_service(
    provider_name: str = DEFAULT_PROVIDER,
    *,
    data_directory: str | Path | None = None,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
) -> tuple[CorpusPreparationPlanner, HistoricalMarketDataService]:
    """Build the existing planner + service with the selected provider."""

    try:
        provider_cls = _PROVIDERS[provider_name]
    except KeyError:
        raise SystemExit(
            f"Unknown provider {provider_name!r}; supported: "
            f"{sorted(_PROVIDERS)}.",
        ) from None
    provider = provider_cls()
    store = HistoricalDataStore(data_directory or default_historical_data_directory())
    service = HistoricalMarketDataService(provider=provider, store=store)
    planner = CorpusPreparationPlanner(
        config=CorpusPlanConfig(timeframes=timeframes, provider=provider_name),
        store=store,
        provider=provider,
    )
    return planner, service


def format_summary(session) -> str:
    """Render the final corpus-ingestion summary (presentation only)."""

    summary = session.summary
    lines = [
        _BANNER,
        "",
        "Corpus ingestion complete",
        "",
        f"Planned missing : {summary.backlog_count}",
        f"Completed       : {summary.completed}",
        f"Skipped         : {summary.skipped}",
        f"Failed          : {summary.failed}",
        f"Candles added   : {summary.candles_added}",
        f"Remaining       : {summary.remaining}",
    ]
    if summary.skip_reasons:
        lines.append("")
        lines.append("Skipped reasons:")
        for reason, count in summary.skip_reasons:
            lines.append(f"  - {reason} ({count})")
    if summary.failed_chunks:
        lines.append("")
        lines.append("Failed chunks (retry on the next run):")
        for chunk in summary.failed_chunks:
            lines.append(f"  - {chunk}")
    lines.append("")
    lines.append(_BANNER)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safe, resumable historical corpus ingestion (Checkpoint 3B).",
    )
    parser.add_argument("--start", required=True, help="window start (YYYY-MM-DD or ISO)")
    parser.add_argument("--end", required=True, help="window end (YYYY-MM-DD or ISO)")
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=(
            "upstox-historical (default) / local-deterministic / "
            "in-memory-import"
        ),
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        help="one or more timeframe labels (default: 15m 1D).",
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
        "--data-dir",
        default=None,
        help="storage root (default ./data/historical).",
    )
    parser.add_argument(
        "--label",
        default="",
        help="descriptive run label (carried onto the plan / provenance).",
    )
    args = parser.parse_args(argv)

    timeframes = tuple(
        tf.strip() for tf in args.timeframes if tf and tf.strip()
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
    if not start < end:
        print("ERROR: --start must be strictly before --end.", file=sys.stderr)
        return 2

    # ---- CREDENTIAL PRECHECK (before ANY ingestion step) --------------
    if args.provider == "upstox-historical":
        try:
            require_upstox_token()
        except CorpusIngestionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        planner, service = build_planner_and_service(
            args.provider,
            data_directory=args.data_dir,
            timeframes=timeframes,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - report honestly, never crash
        print(f"ERROR: failed to build planner/service: {exc}", file=sys.stderr)
        return 1

    config = CorpusIngestionConfig(
        provider=args.provider,
        require_upstox_token=(args.provider == "upstox-historical"),
        label=args.label,
    )

    lines: list[str] = [
        "HISTORICAL CORPUS INGESTION",
        "===========================",
        "",
        f"Provider : {args.provider}",
        f"Window   : {start.isoformat()} -> {end.isoformat()}",
    ]
    print("\n".join(lines))

    engine = CorpusIngestionEngine(
        planner,
        service,
        config,
        reporter=lambda line: print(line, flush=True),
    )
    try:
        session = engine.run(
            start=start,
            end=end,
            instruments=instruments,
            label=args.label,
        )
    except CorpusIngestionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - run-level failure reporting
        print(f"ERROR: corpus ingestion failed: {exc}", file=sys.stderr)
        return 1

    print("")
    print(format_summary(session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
