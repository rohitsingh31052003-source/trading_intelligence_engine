#!/usr/bin/env python3
"""
LIVE operator verification for the Upstox historical provider.

Performs ONE real monthly Upstox API request and works ONLY when the
``UPSTOX_ANALYTICS_TOKEN`` environment variable is set:

    RELIANCE / 15m / 2022-12-01 -> 2023-01-01

Demonstrates: HTTP 200, non-zero candles, valid timestamps, valid OHLCV,
correct chronological normalization (the API returns candles in REVERSE
chronological order).

AUTHENTICATION SAFETY:

* The token is read from ``UPSTOX_ANALYTICS_TOKEN``.
* It is sent ONLY in the ``Authorization: Bearer <token>`` header.
* It is NEVER printed, NEVER logged, NEVER written to any file.

EXIT CODES: 0 on success; 1 when the token is missing or the Upstox API
call failed / returned no data; 2 on bad CLI arguments.

This script is OPTIONAL and network-dependent — the normal test suite
and the deterministic demo never touch the network.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine.data.historical_provider import (
    UPSTOX_TOKEN_ENV,
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.models.historical_data import (
    HistoricalDataRequest,
    HistoricalIngestionStatus,
)

DEFAULT_START = "2022-12-01"
DEFAULT_END = "2023-01-01"


def _parse_timestamp(value: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid timestamp {value!r} (expected YYYY-MM-DD or ISO).",
        )
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live Upstox historical provider verification (one month).",
    )
    parser.add_argument("--instrument", default="RELIANCE")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="optional storage root; when set the fetched month is also "
        "ingested into the existing HistoricalDataStore",
    )
    args = parser.parse_args(argv)

    token = os.environ.get(UPSTOX_TOKEN_ENV)
    if not token:
        print(
            f"{UPSTOX_TOKEN_ENV} is not set. Set it to the Upstox Analytics "
            "API token and retry (the token is used only in the Bearer "
            "header and is never printed).",
        )
        return 1

    print("UPSTOX LIVE VERIFICATION")
    print("=" * 72)
    print("Provider        : upstox-historical")
    print("Instrument      : " + args.instrument)
    print("Timeframe       : " + args.timeframe)
    print("Range           : " + args.start + " -> " + args.end)
    print()

    provider = UpstoxHistoricalDataProvider()
    if not provider.is_available():
        print(f"FAIL: {UPSTOX_TOKEN_ENV} set but provider unavailable.")
        return 1

    try:
        request = HistoricalDataRequest(
            instrument=args.instrument,
            timeframe=args.timeframe,
            start=_parse_timestamp(args.start),
            end=_parse_timestamp(args.end),
            provider=provider.provider_name,
        )
    except ValueError as exc:
        print(f"FAIL: invalid request: {exc}")
        return 2

    if args.data_dir:
        store = HistoricalDataStore(args.data_dir)
        svc = HistoricalMarketDataService(provider=provider, store=store)
        result = svc.ingest(request)
        fetch = result.fetch
        store_result = result.store
    else:
        svc = HistoricalMarketDataService(provider=provider)
        fetch = svc.fetch_historical(request)
        store_result = None

    p = fetch.provenance
    http_ok = fetch.status is not HistoricalIngestionStatus.ERROR
    print(f"HTTP status      : {'OK' if http_ok else 'ERROR'}")
    print(f"Provider status  : {fetch.status.name}")
    print(f"Requested Start  : {p.requested_start.isoformat()}")
    print(f"Requested End    : {p.requested_end.isoformat()}")
    print(f"Records Received : {p.records_received}")
    print(f"Records Accepted : {p.records_accepted}")
    print(f"Records Rejected : {p.records_rejected}")
    first = p.actual_first_candle
    last = p.actual_last_candle
    print(f"First Candle     : {first.isoformat() if first else 'unavailable'}")
    print(f"Last Candle      : {last.isoformat() if last else 'unavailable'}")
    print()

    candles = fetch.candles
    ok = bool(candles)
    if ok:
        chrono = [c.timestamp for c in candles] == sorted(
            [c.timestamp for c in candles],
        )
        tz_ok = all(c.timestamp.tzinfo is not None for c in candles)
        ohlcv_ok = all(
            c.high >= c.low >= 0 and c.open >= c.low and c.open <= c.high
            and c.close >= c.low and c.close <= c.high and c.volume >= 0
            for c in candles
        )
        print("Chronology        : " + ("PASS" if chrono else "FAIL"))
        print("Timezone aware    : " + ("PASS" if tz_ok else "FAIL"))
        print("OHLCV valid       : " + ("PASS" if ohlcv_ok else "FAIL"))
        ok = ok and chrono and tz_ok and ohlcv_ok
        print()
        print(f"Verification result: {'PASS' if ok else 'FAIL'}")
    else:
        print("Verification result: FAIL (no candles returned)")
        print("Reason:", fetch.issues[0].reason if fetch.issues else p.reason)

    if store_result is not None:
        print()
        print(f"Stored           : {store_result.path}")
        print(f"Records Added    : {store_result.records_added}")
        print(f"Total Stored     : {store_result.total_candles}")
        reloaded = store.load_historical(args.instrument, args.timeframe)
        if reloaded.count > 0:
            print(
                f"Reload check     : PASS ({reloaded.count} candles reloaded)",
            )
        else:
            print("Reload check     : FAIL")

    print("=" * 72)
    print("HISTORICAL DATA ONLY — no prediction, no evidence computed.")
    print("The Upstox Analytics token was used only in the Bearer header")
    print("and was never printed.")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())