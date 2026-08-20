#!/usr/bin/env python3
"""
Product Phase 6A demo — Historical Market Data Foundation.

Visibly proves the foundation works end-to-end: bounded ingestion via
the deterministic local provider, idempotent storage, provenance,
validation, gap detection, look-ahead protection, dashboard status
surface, and existing-path regression. Every check prints an explicit
PASS/FAIL; the demo exits non-zero on any failure. DATA FOUNDATION
ONLY — no prediction, no evidence computed.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine.data.historical_provider import (  # noqa: E402
    DeterministicLocalHistoricalProvider,
    InMemoryHistoricalProvider,
)
from engine.data.historical_service import HistoricalMarketDataService  # noqa: E402
from engine.data.historical_store import HistoricalDataStore  # noqa: E402
from engine.data.historical_gaps import detect_gaps  # noqa: E402
from engine.models.historical_data import (  # noqa: E402
    HistoricalDataRequest,
    HistoricalIngestionStatus,
    GapKind,
)
from engine.models.ohlcv import OHLCVCandle  # noqa: E402

_NOW = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
_START = datetime(2024, 1, 1, tzinfo=UTC)
_END = datetime(2024, 1, 20, tzinfo=UTC)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _candle(day: int, base: datetime = _START, close: float = 100.0):
    ts = base + timedelta(days=day)
    return OHLCVCandle(ts, close, close + 2.0, close - 2.0, close, 1000.0)


def _series(n: int, base: datetime = _START):
    return tuple(_candle(i, base) for i in range(n))


def _request(instrument="NIFTY", timeframe="1D", start=_START, end=_END):
    return HistoricalDataRequest(instrument, timeframe, start, end)


def main() -> int:
    print("=" * 72)
    print("PRODUCT PHASE 6A — HISTORICAL MARKET DATA FOUNDATION — DEMO")
    print("=" * 72)

    # 1. Bounded ingestion via deterministic local provider.
    with TemporaryDirectory() as tmp:
        store = HistoricalDataStore(tmp)
        svc = HistoricalMarketDataService(
            provider=DeterministicLocalHistoricalProvider(),
            store=store,
        )
        result = svc.ingest(_request())
        report(
            "1. bounded ingestion succeeds",
            result.fetch.status is HistoricalIngestionStatus.AVAILABLE
            and len(result.fetch.candles) == 20,
            f"{result.fetch.accepted_count} candles accepted",
        )

        # 2. Idempotent storage.
        second = svc.ingest(_request())
        report(
            "2. same ingestion twice -> no duplicates",
            second.store.records_added == 0 and second.store.total_candles == 20,
        )

        # 3. Provenance captured.
        prov = result.fetch.provenance
        lines = store.load_provenance("NIFTY", "1D")
        report(
            "3. provenance retained per ingestion",
            bool(lines)
            and prov.records_received == 20
            and prov.records_rejected == 0
            and prov.provider == "local-deterministic",
            f"{len(lines)} provenance lines",
        )

        # 4. Validation rules.
        messy = (
            _candle(0), _candle(0),  # duplicate
            _candle(2), _candle(1),  # out of order
            _candle(300),            # future-dated (2024-10-27 > _NOW)
        )
        svc_mixed = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider({("NIFTY", "1D"): messy}),
            store=store,
        )
        friday = _candle(0, datetime(2024, 1, 5, tzinfo=UTC))
        monday2 = _candle(3, datetime(2024, 1, 5, tzinfo=UTC))
        big_a = _candle(0)
        big_b = _candle(10)
        mixed_result = svc_mixed.fetch_historical(
            _request(instrument="NIFTY"),
            reference_now=_NOW,
        )
        _ = (friday, monday2, big_a, big_b)  # silence unused in demo shell
        report(
            "4. validation rejects duplicates / future / sorts order",
            mixed_result.status in (
                HistoricalIngestionStatus.PARTIAL,
                HistoricalIngestionStatus.INVALID,
            )
            and len(mixed_result.issues) >= 3,
            f"{len(mixed_result.issues)} issue(s)",
        )

        # 5. Gap detection.
        gaps = detect_gaps((friday, monday2), "1D")
        report(
            "5a. weekend gap classified as possible closure",
            gaps and gaps[0].kind is GapKind.POSSIBLE_MARKET_CLOSURE,
        )
        big_gaps = detect_gaps((big_a, big_b), "1D")
        report(
            "5b. large gap classified as unexpected",
            big_gaps and big_gaps[0].kind is GapKind.UNEXPECTED_GAP,
        )
        report(
            "5c. valid sequence has no gaps",
            detect_gaps(_series(5), "1D") == (),
        )

        # 6. Look-ahead protection.
        boundary = _candle(4).timestamp
        loaded = svc.load_historical("NIFTY", "1D", evaluation_time=boundary)
        report(
            "6a. evaluation boundary excludes later candles",
            loaded.count == 5 and all(
                [c.timestamp <= boundary for c in loaded.candles],
            ),
        )
        import inspect

        has_no_future_param = all(
            not ({"future", "future_candles", "lookahead"}
                 & set(inspect.signature(
                     getattr(HistoricalMarketDataService, name),
                 ).parameters))
            for name in ("fetch_historical", "ingest", "load_historical",
                         "validate_historical")
        )
        # Fixed-T stability when future candles are appended.
        before = svc.load_historical(
            "NIFTY", "1D", evaluation_time=boundary,
        ).candles
        svc.ingest(_request(end=datetime(2024, 2, 29, tzinfo=UTC)))
        after = svc.load_historical(
            "NIFTY", "1D", evaluation_time=boundary,
        ).candles
        report(
            "6b. no hidden future-candle parameter in the public API",
            has_no_future_param,
        )
        report(
            "6c. same dataset at fixed T unchanged when future appended",
            before == after,
        )

        # 7. Multiple instruments / timeframes.
        svc_multi = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider(
                {
                    ("NIFTY", "1D"): _series(2),
                    ("RELIANCE", "1D"): _series(3),
                    ("NIFTY", "15m"): _series(4),
                },
            ),
            store=store,
        )
        svc_multi.ingest(_request(instrument="RELIANCE"))
        svc_multi.ingest(_request(timeframe="15m"))
        report(
            "7. multiple instruments / timeframes stored independently",
            len(store.load_provenance("RELIANCE", "1D"))
            and len(store.load_candles("NIFTY", "15m")) >= 4,
        )

        # 8. Provider ordering invariance / deterministic serialization.
        shuffled = (_candle(3), _candle(1), _candle(2), _candle(0))
        ordered = (_candle(0), _candle(1), _candle(2), _candle(3))
        svc_a = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider({("NIFTY", "1D"): shuffled}),
        )
        svc_b = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider({("NIFTY", "1D"): ordered}),
        )
        ra = svc_a.fetch_historical(_request())
        rb = svc_b.fetch_historical(_request())
        report(
            "8. provider ordering differences -> canonical chronological order",
            ra.candles == rb.candles,
        )

        # 9. Empty / provider error never crash.
        svc_empty = HistoricalMarketDataService(provider=InMemoryHistoricalProvider())
        empty = svc_empty.fetch_historical(_request())
        report(
            "9. empty provider response reported honestly",
            empty.status is HistoricalIngestionStatus.EMPTY,
        )

    # 10. Dashboard status surface.
    from dashboard.app import create_app
    from dashboard.services import default_service
    from fastapi.testclient import TestClient

    svc_dash = default_service()
    client = TestClient(create_app(svc_dash))
    api = client.get("/api/historical-data")
    page = client.get("/historical-data")
    report(
        "10. dashboard status surface (empty state honest)",
        api.status_code == 200 and page.status_code == 200
        and api.json()["dataset_count"] == 0,
    )

    # 11. Existing live pipeline regression (trending dataset baseline).
    from engine.pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
        trending_dataset,
    )

    pipeline_result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
        trending_dataset(),
    )
    report(
        "11. existing pipeline baseline unchanged (4 signals / 3 trades)",
        pipeline_result.signals_generated == 4
        and pipeline_result.performance.completed_trades == 3,
    )

    print("=" * 72)
    print(f"Product Phase 6A demo completed successfully ({_checks} checks passed).")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
