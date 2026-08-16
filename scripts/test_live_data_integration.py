"""
Product Phase 1 — LIVE / NEAR-LIVE MARKET-DATA INTEGRATION demo / smoke test.

Proves the completed-candle boundary, freshness semantics, provider
abstraction and live (Yahoo) provider normalization / failure handling —
WITHOUT making real network calls (a fake backend stands in for
yfinance). The existing intelligence engine semantics are NOT altered by
the provider or freshness layer.

Run::

    python scripts/test_live_data_integration.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from dashboard.data_provider import (
    FIXTURE_INSTRUMENTS,
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
    YahooDataProvider,
    classify_freshness,
    make_provider,
    split_completed_candles,
)
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
)
from engine.models.ohlcv import OHLCVCandle

_CHECKS: list[tuple[str, str]] = []


def _check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


NOW = datetime(2025, 6, 2, 12, 0, tzinfo=UTC)


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts, open=close, high=close + 1.0, low=close - 1.0,
        close=close, volume=1000.0,
    )


class _FakeBackend:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], list[OHLCVCandle]] = {}
        self.raise_on: set[tuple[str, str]] = set()

    def connect(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def get_history(self, symbol, start, end, interval):
        key = (symbol, interval)
        if key in self.raise_on:
            raise RuntimeError("simulated network failure")
        return list(self.responses.get(key, []))


def main() -> None:
    _banner("Product Phase 1 — Live / near-live market-data integration")

    _banner("1. Provider abstraction + factory")
    p = make_provider()
    _check("default provider is fixture", type(p).__name__ == "FixtureDataProvider")
    y = make_provider("yahoo")
    _check("yahoo provider selectable", type(y).__name__ == "YahooDataProvider")
    _check("fixture data_source label", p.data_source == "fixture")
    _check("yahoo data_source label", y.data_source == "yahoo")

    _banner("2. Fixture provider compatibility (deterministic)")
    s = p.fetch("NIFTY", "15m")
    _check("fixture available", s.available is True)
    _check("fixture completed candles only", s.forming_setup_candle is None)
    _check(
        "fixture latest completed set",
        s.latest_completed_candle_timestamp == s.setup_candles[-1].timestamp,
    )
    s2 = p.fetch("NIFTY", "15m")
    _check("fixture deterministic across calls", s == s2)

    _banner("3. Live provider normalization (no network)")
    backend = _FakeBackend()
    completed = [
        _candle(NOW - timedelta(minutes=15 * (5 - i)), 100.0 + i)
        for i in range(5)
    ]
    forming = _candle(NOW, 999.0)
    backend.responses[("^NSEI", "15m")] = completed + [forming]
    backend.responses[("^NSEI", "1d")] = [
        _candle(NOW - timedelta(days=3), 95.0),
    ]
    yp = YahooDataProvider(provider=backend)
    rs = yp.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
    _check("live available", rs.available is True)
    _check("forming candle excluded from engine input", forming not in rs.setup_candles)
    _check("forming candle kept for display", rs.forming_setup_candle is forming)
    _check(
        "latest completed boundary set",
        rs.latest_completed_candle_timestamp == completed[-1].timestamp,
    )

    _banner("4. Symbol mapping isolated inside provider")
    _check("NIFTY -> ^NSEI", yp.resolve_symbol("NIFTY") == "^NSEI")
    _check("RELIANCE -> RELIANCE.NS", yp.resolve_symbol("RELIANCE") == "RELIANCE.NS")
    _check("unknown passthrough", yp.resolve_symbol("FOO") == "FOO")

    _banner("5. Completed-candle boundary helper")
    r = split_completed_candles(completed + [forming], "15m", NOW)
    _check("boundary completed count", len(r.completed) == 5)
    _check("boundary forming detected", r.forming is forming)
    future = _candle(NOW + timedelta(minutes=30), 200.0)
    rf = split_completed_candles(completed + [future], "15m", NOW)
    _check("future candle rejected", future in rf.rejected_future)

    _banner("6. Freshness / staleness (data quality, not a signal)")
    _check(
        "fresh recent = CURRENT",
        classify_freshness(
            latest_completed_timestamp=NOW - timedelta(seconds=10),
            reference_now=NOW, staleness_seconds=60,
            provider_status=ProviderStatus.OK,
        ) is FreshnessState.CURRENT,
    )
    _check(
        "stale old = STALE",
        classify_freshness(
            latest_completed_timestamp=NOW - timedelta(days=5),
            reference_now=NOW, staleness_seconds=60,
            provider_status=ProviderStatus.OK,
        ) is FreshnessState.STALE,
    )
    _check(
        "no completed = UNAVAILABLE",
        classify_freshness(
            latest_completed_timestamp=None, reference_now=NOW,
            staleness_seconds=60, provider_status=ProviderStatus.OK,
        ) is FreshnessState.UNAVAILABLE,
    )

    _banner("7. Failure handling (honest, never fabricated)")
    err_backend = _FakeBackend()
    err_backend.raise_on.add(("^NSEI", "15m"))
    errp = YahooDataProvider(provider=err_backend)
    es = errp.fetch("NIFTY", "15m", reference_now=NOW)
    _check("error reported unavailable", es.available is False)
    _check("provider status ERROR", es.provider_status is ProviderStatus.ERROR)
    _check("no fixture fallback leaked", es.setup_candles == ())
    empty_backend = _FakeBackend()
    emptyp = YahooDataProvider(provider=empty_backend)
    empty_s = emptyp.fetch("NIFTY", "15m", reference_now=NOW)
    _check("empty response = EMPTY", empty_s.provider_status is ProviderStatus.EMPTY)

    _banner("8. Unsupported instrument / timeframe")
    fs = p.fetch("BOGUS", "15m")
    _check("fixture unknown instrument UNSUPPORTED", fs.provider_status is ProviderStatus.UNSUPPORTED)
    ts = yp.fetch("NIFTY", "7d", reference_now=NOW)
    _check("yahoo unsupported timeframe UNSUPPORTED", ts.provider_status is ProviderStatus.UNSUPPORTED)

    _banner("9. Dashboard integration + no-look-ahead")
    # Pin the provider clock so the forming candle is detected
    # deterministically (the service delegates to the provider boundary).
    ref_now = NOW + timedelta(minutes=1)
    _orig_fetch = yp.fetch

    def _pinned_fetch(instrument, setup_timeframe, lookback_bars=300, **kw):
        return _orig_fetch(
            instrument, setup_timeframe, lookback_bars, reference_now=ref_now,
        )

    yp.fetch = _pinned_fetch  # type: ignore[method-assign]
    svc = DashboardAnalysisService(provider=yp)
    v = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
    _check("data source view populated", v.data_source.data_source == "yahoo")
    _check("freshness surfaced", v.data_source.freshness_state in ("CURRENT", "STALE"))
    _check("forming flag surfaced", v.data_source.forming_candle_present is True)
    _check("no BUY/SELL language", "BUY" not in v.decision.decision_classification)

    _banner("10. Existing pipeline baseline (signals=4, trades=3)")
    from engine.pipeline import (
        HistoricalEvaluationPipeline, PipelineConfig, trending_dataset,
    )
    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(trending_dataset())
    _check("pipeline signals_generated == 4", result.signals_generated == 4)
    _check("pipeline completed_trades == 3", result.performance.completed_trades == 3)

    _banner("Summary")
    n_pass = sum(1 for _, st in _CHECKS if st == "PASS")
    n_fail = sum(1 for _, st in _CHECKS if st == "FAIL")
    print(f"  {n_pass} PASS / {n_fail} FAIL / {len(_CHECKS)} total")
    print()
    print(
        "Product Phase 1 results are DESCRIPTIVE ONLY. Live data integration does "
        "NOT constitute live trading, does NOT predict the market, and does NOT "
        "modify the existing decision engine. The existing intelligence "
        "architecture determines structure, trend, setup, decision, evidence and "
        "geometry; the provider only supplies trustworthy completed-candle data."
    )
    print()
    print("Product Phase 1 demo completed successfully.")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
