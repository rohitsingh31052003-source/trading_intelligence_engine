#!/usr/bin/env python3
"""
Operator-facing CLI: run exactly ONE historical data ingestion
(Product Phase 6A — Historical Market Data Foundation).

This script is a THIN command-line interface over the EXISTING
:class:`engine.data.historical_service.HistoricalMarketDataService`. It
implements NO trading intelligence, NO scoring, NO prediction, NO
decision / geometry / evidence logic. The existing decision engine,
trade geometry, trade plan, paper-trading operations and the live
Yahoo completed-candle boundary remain AUTHORITATIVE and are untouched
by this ingestion path.

Usage::

    python scripts/ingest_historical_data.py \
        --instrument NIFTY --timeframe 1D --start 2024-01-01 --end 2024-12-31

Provider selection (``--provider``): ``local-deterministic`` (default,
no network / no API key), ``in-memory-import``, ``yahoo-historical``
(optional; only when yfinance/pandas are installed). The provider is
replaceable without changing the research layer.

Exit codes: 0 on AVAILABLE / PARTIAL ingestion (per-instrument
validation failures are reported honestly, never treated as CLI
failure); 1 on EMPTY / INVALID / ERROR or a service/runtime exception;
2 for bad CLI args.

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

from engine.data.historical_provider import (  # noqa: E402
    DeterministicLocalHistoricalProvider,
    InMemoryHistoricalProvider,
    YahooHistoricalDataProvider,
)
from engine.data.historical_service import (  # noqa: E402
    HistoricalMarketDataService,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
    default_historical_data_directory,
)
from engine.data.historical_times import supported_timeframes  # noqa: E402
from engine.models.historical_data import (  # noqa: E402
    HistoricalDataRequest,
    HistoricalIngestionStatus,
)

DEFAULT_TIMEFRAME = "1D"
DEFAULT_PROVIDER = "local-deterministic"

_BANNER = "HISTORICAL DATA ONLY — no prediction, no evidence computed"

_PROVIDERS = {
    DEFAULT_PROVIDER: DeterministicLocalHistoricalProvider,
    "in-memory-import": InMemoryHistoricalProvider,
    "yahoo-historical": YahooHistoricalDataProvider,
}


def make_service(
    provider_name: str = DEFAULT_PROVIDER,
    *,
    data_directory: str | Path | None = None,
) -> HistoricalMarketDataService:
    """Build the ingestion service with the selected provider."""

    try:
        provider_cls = _PROVIDERS[provider_name]
    except KeyError:
        raise SystemExit(
            f"Unknown provider {provider_name!r}; supported: "
            f"{sorted(_PROVIDERS)}.",
        ) from None
    provider = provider_cls()
    store = HistoricalDataStore(data_directory or default_historical_data_directory())
    return HistoricalMarketDataService(provider=provider, store=store)


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


def build_request(
    instrument: str,
    timeframe: str,
    start: str,
    end: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    allow_future_end: bool = False,
) -> HistoricalDataRequest:
    """Build a validated request (exits 2 on invalid input)."""

    try:
        return HistoricalDataRequest(
            instrument=instrument,
            timeframe=timeframe,
            start=_parse_timestamp(start),
            end=_parse_timestamp(end),
            provider=provider,
            allow_future_end=allow_future_end,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid request: {exc}") from exc


def format_report(result) -> str:
    """Render the ingestion report from an existing result (format only)."""

    fetch = result.fetch if hasattr(result, "fetch") else result
    store = result.store if hasattr(result, "store") else None
    p = fetch.provenance
    lines = [
        _BANNER,
        "",
        "HISTORICAL DATA INGESTION",
        "",
        f"Instrument      : {p.instrument}",
        f"Timeframe       : {p.timeframe}",
        f"Provider        : {p.provider}",
        f"Requested Start : {p.requested_start.isoformat()}",
        f"Requested End   : {p.requested_end.isoformat()}",
        "",
        f"Records Received: {p.records_received}",
        f"Records Accepted: {p.records_accepted}",
        f"Records Rejected: {p.records_rejected}",
        f"First Candle    : "
        f"{p.actual_first_candle.isoformat() if p.actual_first_candle else 'unavailable'}",
        f"Last Candle     : "
        f"{p.actual_last_candle.isoformat() if p.actual_last_candle else 'unavailable'}",
        "",
    ]
    rejected = [
        (i.error.name, i.reason)
        for i in fetch.issues
        if fetch.provenance.status is HistoricalIngestionStatus.PARTIAL
        or fetch.provenance.status is HistoricalIngestionStatus.INVALID
    ]
    status = "PASS" if fetch.status is HistoricalIngestionStatus.AVAILABLE else (
        "PARTIAL"
        if fetch.status is HistoricalIngestionStatus.PARTIAL
        else f"FAIL ({fetch.status.value})"
    )
    lines.append(f"Validation      : {status}")
    future_issues = [
        i for i in fetch.issues if i.error.name == "FUTURE_DATED"
    ]
    dup_issues = [
        i for i in fetch.issues if i.error.name == "DUPLICATE_TIMESTAMP"
    ]
    chron_issues = [
        i for i in fetch.issues if i.error.name == "UNORDERED"
    ]
    future_check = "PASS" if not future_issues else f"FAIL ({len(future_issues)} rejected)"
    dup_check = "PASS" if not dup_issues else f"FAIL ({len(dup_issues)} rejected)"
    chron_check = (
        "PASS"
        if not chron_issues
        else f"NORMALIZED ({len(chron_issues)} issue(s))"
    )
    lines.extend(
        [
            f"No future candles: {future_check}",
            f"Duplicate check : {dup_check}",
            f"Chronology      : {chron_check}",
            "",
        ],
    )
    if fetch.gaps:
        closure = sum(1 for g in fetch.gaps if g.kind.name == "POSSIBLE_MARKET_CLOSURE")
        unexpected = sum(1 for g in fetch.gaps if g.kind.name == "UNEXPECTED_GAP")
        lines.append(
            f"Gaps detected   : {closure} possible closure, {unexpected} unexpected",
        )
        lines.append("")
    if store is not None:
        lines.extend(
            [
                f"Stored          : {store.path}",
                f"Records Added   : {store.records_added}",
                f"Total Stored    : {store.total_candles}",
                "",
            ],
        )
    if rejected:
        lines.append("Rejected records:")
        for name, reason in rejected[:10]:
            lines.append(f"  - [{name}] {reason}")
        if len(rejected) > 10:
            lines.append(f"  ... and {len(rejected) - 10} more")
        lines.append("")
    lines.append("INGESTION COMPLETE" if status in ("PASS", "PARTIAL") else "INGESTION FAILED")
    lines.append("")
    lines.append(_BANNER)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest historical OHLCV market data (Phase 6A).",
    )
    parser.add_argument("--instrument", required=True, help="e.g. NIFTY")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help="e.g. 1D / 15m")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD or ISO")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD or ISO")
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help="local-deterministic (default) / in-memory-import / yahoo-historical",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="storage root (default ./data/historical)",
    )
    parser.add_argument(
        "--allow-future-end",
        action="store_true",
        help="controlled import flag: permit a future end timestamp",
    )
    args = parser.parse_args(argv)

    service = make_service(args.provider, data_directory=args.data_dir)
    request = build_request(
        args.instrument,
        args.timeframe,
        args.start,
        args.end,
        provider=args.provider,
        allow_future_end=args.allow_future_end,
    )
    try:
        result = service.ingest(request)
    except Exception as exc:  # noqa: BLE001 - report honestly, never crash
        print(f"INGESTION FAILED: {exc}")
        return 1
    print(format_report(result))
    ok = result.fetch.status in (
        HistoricalIngestionStatus.AVAILABLE,
        HistoricalIngestionStatus.PARTIAL,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
