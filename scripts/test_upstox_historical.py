#!/usr/bin/env python3
"""
Deterministic UPSTOX HISTORICAL PROVIDER demo.

Visibly proves the Upstox historical provider works against a
FAKE/injected HTTP backend: provider contract, availability, 15m
support, instrument-key resolution, URL + Authorization header
construction (without leaking the token), reverse-chronological
normalization, monthly chunking, multi-chunk merging, OHLCV conversion,
+05:30 -> UTC timestamp normalization, error handling, service + store
integration, idempotent re-ingestion, and NO regression to the Yahoo /
deterministic providers.

The demo makes NO real network calls and requires NO Upstox token. A
REAL live check against the Upstox API is a separate operator action
(see ``scripts/verify_upstox_live.py``).
"""

from __future__ import annotations

import json as _json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine.data.historical_provider import (
    UpstoxHistoricalDataProvider,
    _upstox_monthly_chunks,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.models.historical_data import (
    HistoricalDataRequest,
    HistoricalIngestionStatus,
    ProviderResponseStatus,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_TOKEN = "demo-test-token"
_START = datetime(2022, 12, 1, tzinfo=UTC)
_END = datetime(2023, 1, 1, tzinfo=UTC)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _ist(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(_IST)


def _row(ts: datetime, open_p=2400.0) -> list:
    return [
        ts.isoformat(), open_p, open_p + 10.0, open_p - 10.0,
        open_p + 5.0, 100000.0, 0,
    ]


def _payload(rows) -> dict:
    return {"status": "success", "candles": rows, "meta": {}}


def _request(instrument="RELIANCE", timeframe="15m", start=_START, end=_END):
    return HistoricalDataRequest(instrument, timeframe, start, end)


class _FakeUrlopen:
    """Fake urlopen receiving (request, token) — records + serves."""

    def __init__(self, handlers):
        self.calls: list[tuple[str, dict, str]] = []
        self.handlers = handlers

    def __call__(self, request, token):
        self.calls.append((request.full_url, dict(request.headers), token))
        payload = self.handlers.get(request.full_url, _payload([]))
        if callable(payload):
            return payload()
        if isinstance(payload, Exception):
            raise payload
        return _json.dumps(payload).encode("utf-8")


def _chunk_url(from_date: str, to_date: str) -> str:
    return (
        "https://api.upstox.com/v3/historical-candle/"
        f"NSE_EQ|INE002A01018/minutes/15/{to_date}/{from_date}"
    )


def _reliance_rows(start_date: str, n: int) -> list:
    start = _ist(f"{start_date}T09:15:00+05:30")
    rows = [
        _row(start + timedelta(minutes=15 * i), 2400.0 + i) for i in range(n)
    ]
    return list(reversed(rows))  # API returns newest first


def main() -> int:
    print("=" * 72)
    print("UPSTOX HISTORICAL PROVIDER — DETERMINISTIC DEMO (no network)")
    print("=" * 72)

    # 1. Provider construction + contract.
    p = UpstoxHistoricalDataProvider(token=_TOKEN)
    report(
        "1. provider name is upstox-historical",
        p.provider_name == "upstox-historical",
    )
    report(
        "2. availability requires a token",
        p.is_available() is True and (
            not UpstoxHistoricalDataProvider().is_available()
        ),
    )

    # 2. Timeframe support.
    report(
        "3. supports RELIANCE/15m, not 1D",
        p.supports("RELIANCE", "15m") is True
        and p.supports("RELIANCE", "1D") is False,
    )

    # 3. Instrument-key resolution.
    report(
        "4. RELIANCE -> NSE_EQ|INE002A01018",
        p.resolve_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018",
    )
    try:
        p.resolve_instrument_key("NIFTY")
        ok_unknown = False
    except KeyError:
        ok_unknown = True
    report("5. unknown instrument fails clearly (KeyError)", ok_unknown)

    # 4. Fake-backed fetch: URL/headers, reverse-order normalization,
    #    +05:30 -> UTC, OHLCV parsing.
    rows = _reliance_rows("2022-12-01", 5)
    fake = _FakeUrlopen({_chunk_url("2022-12-01", "2023-01-01"): _payload(rows)})
    p = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake)
    response = p.fetch(_request())
    report(
        "6. fetch returns OK with 5 candles (chronological)",
        response.status is ProviderResponseStatus.OK
        and len(response.candles) == 5
        and [c.timestamp for c in response.candles]
        == sorted(c.timestamp for c in response.candles),
    )
    url, headers, _token = fake.calls[0]
    report(
        "7. exact Upstox URL constructed",
        url == _chunk_url("2022-12-01", "2023-01-01"),
    )
    report(
        "8. Bearer Authorization header (token never logged)",
        headers["Authorization"] == f"Bearer {_TOKEN}"
        and _TOKEN not in url,
    )
    c0 = response.candles[0]
    report(
        "9. +05:30 timestamp normalized to UTC, OHLCV converted",
        c0.timestamp == datetime(2022, 12, 1, 3, 45, tzinfo=UTC)
        and c0.open == 2400.0 and c0.high == 2410.0
        and c0.low == 2390.0 and c0.close == 2405.0 and c0.volume == 100000.0,
    )

    # 5. Monthly chunking + multi-month merge.
    chunks = _upstox_monthly_chunks(
        datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC),
    )
    report("10. a 12-month range splits into 12 monthly chunks", len(chunks) == 12)
    handlers = {
        _chunk_url("2022-12-01", "2023-01-01"):
            _payload(_reliance_rows("2022-12-01", 3)),
        _chunk_url("2023-01-01", "2023-02-01"):
            _payload(_reliance_rows("2023-01-02", 2)),
    }
    fake2 = _FakeUrlopen(handlers)
    p2 = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake2)
    merged = p2.fetch(_request(end=datetime(2023, 2, 1, tzinfo=UTC)))
    report(
        "11. multiple monthly responses combined chronologically",
        merged.status is ProviderResponseStatus.OK
        and len(merged.candles) == 5
        and [c.timestamp for c in merged.candles]
        == sorted([c.timestamp for c in merged.candles])
        and len(fake2.calls) == 2,
    )

    # 6. Failure handling never fabricates.
    fake3 = _FakeUrlopen({
        _chunk_url("2022-12-01", "2023-01-01"): RuntimeError("boom"),
    })
    p3 = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake3)
    failed = p3.fetch(_request())
    report(
        "12. failed month -> honest ERROR (never fabricated data)",
        failed.status is ProviderResponseStatus.ERROR,
    )

    # 7. Service + store integration, idempotency.
    with TemporaryDirectory() as tmp:
        store = HistoricalDataStore(tmp)
        fake4 = _FakeUrlopen({
            _chunk_url("2022-12-01", "2023-01-01"):
                _payload(_reliance_rows("2022-12-01", 4)),
        })
        p4 = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake4)
        svc = HistoricalMarketDataService(provider=p4, store=store)
        result = svc.ingest(_request(), reference_now=_END)
        report(
            "13. service + canonical store persist Upstox data",
            result.fetch.status is HistoricalIngestionStatus.AVAILABLE
            and result.store.total_candles == 4
            and result.store.reload_verified is True,
            result.store.path,
        )
        second = svc.ingest(_request(), reference_now=_END)
        report(
            "14. idempotent re-ingestion (no duplicates)",
            second.store.records_added == 0
            and second.store.total_candles == 4,
        )
        loaded = svc.load_historical("RELIANCE", "15m")
        report(
            "15. candles stored under the canonical structure",
            loaded.count == 4
            and (Path(tmp) / "RELIANCE" / "15m" / "candles.json").exists()
            and all(c.timestamp.tzinfo is not None for c in loaded.candles),
        )

    # 8. No regression to Yahoo / deterministic providers.
    from engine.data.historical_provider import (
        DeterministicLocalHistoricalProvider,
        YahooHistoricalDataProvider,
    )

    y = YahooHistoricalDataProvider()
    d = DeterministicLocalHistoricalProvider()
    report(
        "16. Yahoo / deterministic providers unchanged",
        y.provider_name == "yahoo-historical"
        and y.resolve_symbol("RELIANCE") == "RELIANCE.NS"
        and d.provider_name == "local-deterministic"
        and d.supports("RELIANCE", "15m") is True,
    )

    # 9. Existing pipeline baseline regression.
    from engine.pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
        trending_dataset,
    )

    pipeline_result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
        trending_dataset(),
    )
    report(
        "17. existing pipeline baseline unchanged (4 signals / 3 trades)",
        pipeline_result.signals_generated == 4
        and pipeline_result.performance.completed_trades == 3,
    )

    print("=" * 72)
    print(f"Upstox provider demo completed successfully ({_checks} checks passed).")
    print("NOTE: this demo used a fake HTTP backend — no real Upstox API")
    print("call was made. For a real live check set UPSTOX_ANALYTICS_TOKEN")
    print("and run scripts/verify_upstox_live.py.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())