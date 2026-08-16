"""
Tests for Product Phase 1 — LIVE / NEAR-LIVE MARKET-DATA INTEGRATION.

These tests verify the completed-candle boundary, freshness semantics,
provider abstraction, live (Yahoo) provider normalization / failure
handling and the no-look-ahead guarantees introduced / hardened in
Product Phase 1 — WITHOUT making real network calls (a fake provider
stands in for yfinance).

Coverage areas (A-X):

A. Provider abstraction
B. Fixture provider compatibility
C. Live provider normalization
D. Valid candle handling
E. Malformed candle handling
F. Empty response
G. Provider failure
H. Unsupported instrument
I. Unsupported timeframe
J. Completed-candle detection
K. Forming-candle exclusion
L. Future timestamp protection
M. Stale-data detection
N. Fresh-data detection
O. Dashboard live-data integration
P. Dashboard unavailable state
Q. Dashboard stale state
R. No-look-ahead
S. Existing decision preservation
T. Existing trade geometry preservation
U. Target 2 remains unsupported
V. Existing pipeline baseline
W. Existing fixture behavior
X. Serialization / backward compatibility

The intelligence engine semantics are NEVER altered by the provider or
freshness layer. Freshness is DATA QUALITY / PRODUCT STATE, not a
trading signal.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import (
    CandleBoundaryResult,
    FIXTURE_INSTRUMENTS,
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_DURATION_SECONDS,
    YahooDataProvider,
    classify_freshness,
    context_timeframe_for,
    make_provider,
    split_completed_candles,
    timeframe_duration_seconds,
)
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    default_service,
)
from dashboard.views import DataSourceView, to_jsonable
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# SHARED HELPERS
# ============================================================


def _candle(
    ts: datetime,
    close: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
) -> OHLCVCandle:
    o = close if open_ is None else open_
    h = high if high is not None else max(o, close) + 1.0
    lo = low if low is not None else min(o, close) - 1.0
    return OHLCVCandle(
        timestamp=ts,
        open=o,
        high=h,
        low=lo,
        close=close,
        volume=volume,
    )


def _series(close_start: float, n: int, step_min: int, start: datetime) -> list[OHLCVCandle]:
    """Build n chronological completed candles at `step_min`-minute spacing."""
    out: list[OHLCVCandle] = []
    for i in range(n):
        ts = start + timedelta(minutes=step_min * i)
        out.append(_candle(ts, close_start + i * 1.0))
    return out


class _FakeYahooBackend:
    """
    A fake stand-in for :class:`YahooFinanceProvider` that returns canned
    candles (or simulates failures) WITHOUT touching the network.

    It exposes the same ``get_history(symbol, start, end, interval)``
    contract the real provider adapter calls, so the YahooDataProvider
    can be exercised deterministically.
    """

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], list[OHLCVCandle]] = {}
        self.raise_on: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    def connect(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[OHLCVCandle]:
        self.calls.append((symbol, interval))
        key = (symbol, interval)
        if key in self.raise_on:
            raise RuntimeError(f"simulated network failure for {key}")
        if key not in self.responses:
            return []
        return list(self.responses[key])


def _yahoo_with_backend(
    backend: _FakeYahooBackend,
    *,
    freshness_config: FreshnessConfig | None = None,
) -> YahooDataProvider:
    """Build a YahooDataProvider wired to a fake backend (no network)."""

    return YahooDataProvider(
        freshness_config=freshness_config,
        provider=backend,
    )


@pytest.fixture
def fixture_service() -> DashboardAnalysisService:
    return DashboardAnalysisService()


@pytest.fixture
def client(fixture_service: DashboardAnalysisService) -> TestClient:
    return TestClient(create_app(service=fixture_service))


NOW = datetime(2025, 6, 2, 12, 0, tzinfo=UTC)


# ============================================================
# A. PROVIDER ABSTRACTION
# ============================================================


class TestProviderAbstraction:
    def test_protocol_members(self):
        from dashboard.data_provider import DashboardDataProvider

        assert hasattr(DashboardDataProvider, "is_timeframe_supported")
        assert hasattr(DashboardDataProvider, "fetch")
        assert hasattr(DashboardDataProvider, "last_updated")

    def test_make_provider_fixture_default(self):
        p = make_provider()
        assert type(p).__name__ == "FixtureDataProvider"

    def test_make_provider_yahoo(self):
        p = make_provider("yahoo")
        assert type(p).__name__ == "YahooDataProvider"

    def test_make_provider_unknown_falls_back_to_fixture(self):
        p = make_provider("nonexistent")
        assert type(p).__name__ == "FixtureDataProvider"

    def test_factory_passes_freshness_config(self):
        cfg = FreshnessConfig(default_staleness_seconds=42)
        p = make_provider("fixture", freshness_config=cfg)
        assert p.freshness_config.default_staleness_seconds == 42

    def test_timeframe_duration_known(self):
        assert timeframe_duration_seconds("15m") == 900
        assert timeframe_duration_seconds("1D") == 86400
        assert timeframe_duration_seconds("nope") is None

    def test_timeframe_duration_table_complete(self):
        for tf in SUPPORTED_TIMEFRAMES:
            assert timeframe_duration_seconds(tf) is not None, tf


# ============================================================
# B. FIXTURE PROVIDER COMPATIBILITY
# ============================================================


class TestFixtureProviderCompatibility:
    def test_fixture_returns_completed_candles(self):
        p = make_provider("fixture")
        s = p.fetch("NIFTY", "15m")
        assert s.available is True
        assert s.data_source == "fixture"
        assert s.provider_status is ProviderStatus.OK
        assert s.setup_candles
        assert s.latest_completed_candle_timestamp == s.setup_candles[-1].timestamp

    def test_fixture_no_forming_candle(self):
        p = make_provider("fixture")
        s = p.fetch("NIFTY", "15m")
        assert s.forming_setup_candle is None
        assert s.rejected_future_count == 0

    def test_fixture_context_candles_present(self):
        p = make_provider("fixture")
        s = p.fetch("NIFTY", "15m")
        assert s.context_candles  # 1D context

    def test_fixture_deterministic_across_calls(self):
        p = make_provider("fixture")
        a = p.fetch("RELIANCE", "15m")
        b = p.fetch("RELIANCE", "15m")
        assert a == b
        assert a.last_successful_fetch_time == b.last_successful_fetch_time

    def test_fixture_last_updated_matches_completed(self):
        p = make_provider("fixture")
        assert p.last_updated("TCS", "15m") == p.fetch("TCS", "15m").latest_completed_candle_timestamp


# ============================================================
# C. LIVE PROVIDER NORMALIZATION
# ============================================================


class TestLiveProviderNormalization:
    def test_yahoo_normalizes_to_completed_candles(self):
        backend = _FakeYahooBackend()
        # 5 completed 15m candles + 1 forming candle (last).
        candles = _series(100.0, 5, 15, NOW - timedelta(minutes=5 * 15))
        forming = _candle(NOW, 105.0)  # opens at NOW, still forming
        candles.append(forming)
        backend.responses[("^NSEI", "15m")] = candles
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))

        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert s.available is True
        assert s.data_source == "yahoo"
        assert s.provider_status is ProviderStatus.OK
        # The forming candle must NOT be in the engine input.
        assert forming not in s.setup_candles
        assert s.forming_setup_candle is forming
        assert s.latest_completed_candle_timestamp == candles[-2].timestamp

    def test_yahoo_symbol_mapping_isolated(self):
        backend = _FakeYahooBackend()
        backend.responses[("^NSEI", "15m")] = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        assert p.resolve_symbol("NIFTY") == "^NSEI"
        assert p.resolve_symbol("RELIANCE") == "RELIANCE.NS"
        assert p.resolve_symbol("UNKNOWN") == "UNKNOWN"  # passthrough
        p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert backend.calls[0] == ("^NSEI", "15m")

    def test_yahoo_custom_symbol_map(self):
        backend = _FakeYahooBackend()
        backend.responses[("ZZZZ", "15m")] = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        backend.responses[("ZZZZ", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = YahooDataProvider(
            symbol_map={"NIFTY": "ZZZZ"}, provider=backend,
        )
        assert p.resolve_symbol("NIFTY") == "ZZZZ"
        p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert ("ZZZZ", "15m") in backend.calls

    def test_yahoo_normalizes_naive_timestamps_to_aware_utc(self):
        # The underlying YahooFinanceProvider may return NAIVE timestamps
        # (e.g. daily data via to_pydatetime). The provider MUST normalize
        # them to tz-aware UTC so downstream scanner comparisons never
        # raise "can't compare offset-naive and offset-aware datetimes".
        backend = _FakeYahooBackend()
        # Naive daily candles (as YahooFinanceProvider produces for 1d).
        naive_candles = [
            OHLCVCandle(
                timestamp=(NOW - timedelta(days=3 - i)).replace(tzinfo=None),
                open=100.0 + i, high=101.0 + i, low=99.0 + i,
                close=100.5 + i, volume=1000.0,
            )
            for i in range(3)
        ]
        backend.responses[("^NSEI", "1d")] = naive_candles
        backend.responses[("^NSEI", "15m")] = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        # Every candle handed to the engine must be tz-aware UTC.
        assert s.available is True
        for c in s.setup_candles:
            assert c.timestamp.tzinfo is not None, c
        for c in s.context_candles:
            assert c.timestamp.tzinfo is not None, c
        # The latest completed context timestamp is aware and comparable
        # against an aware reference (no TypeError).
        assert s.latest_completed_candle_timestamp.tzinfo is not None


# ============================================================
# D. VALID CANDLE HANDLING
# ============================================================


class TestValidCandleHandling:
    def test_valid_candles_pass_through(self):
        backend = _FakeYahooBackend()
        candles = _series(100.0, 4, 15, NOW - timedelta(minutes=4 * 15))
        backend.responses[("^NSEI", "15m")] = candles
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert s.available is True
        assert len(s.setup_candles) == 4
        assert s.setup_candles == tuple(candles)


# ============================================================
# E. MALFORMED CANDLE HANDLING
# ============================================================


class TestMalformedCandleHandling:
    def test_invalid_ohlc_dropped(self):
        backend = _FakeYahooBackend()
        good = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        # Impossible OHLC (high < low) — OHLCVCandle rejects at construction,
        # so simulate a malformed-then-rejected entry via a low-level insert
        # by using a valid-constructed candle that still fails DataValidator?
        # DataValidator.validate_candle mirrors OHLCVCandle.__post_init__, so
        # we cannot construct an invalid OHLCVCandle. Instead, simulate a
        # future-dated candle (rejected by the boundary) and an invalid
        # volume via monkey-patching the backend to return a candle with
        # negative volume — but OHLCVCandle rejects that too. So we test the
        # boundary's own rejection path: a future candle is rejected.
        future = _candle(NOW + timedelta(minutes=15), 999.0)
        backend.responses[("^NSEI", "15m")] = good + [future]
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert s.available is True
        assert future not in s.setup_candles
        assert s.rejected_future_count == 1

    def test_empty_after_invalid_is_unavailable(self):
        backend = _FakeYahooBackend()
        # All candles are future-dated relative to NOW -> none completed.
        future = _series(100.0, 3, 15, NOW + timedelta(minutes=15))
        backend.responses[("^NSEI", "15m")] = future
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.available is False
        assert s.provider_status is ProviderStatus.EMPTY
        assert s.rejected_future_count == 3


# ============================================================
# F. EMPTY RESPONSE
# ============================================================


class TestEmptyResponse:
    def test_yahoo_empty_response(self):
        backend = _FakeYahooBackend()
        # No response registered -> get_history returns []
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.available is False
        assert s.provider_status is ProviderStatus.EMPTY
        assert s.freshness_state is FreshnessState.UNAVAILABLE
        assert "no data" in s.reason.lower() or "returned no data" in s.reason.lower()


# ============================================================
# G. PROVIDER FAILURE
# ============================================================


class TestProviderFailure:
    def test_yahoo_network_failure(self):
        backend = _FakeYahooBackend()
        backend.raise_on.add(("^NSEI", "15m"))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.available is False
        assert s.provider_status is ProviderStatus.ERROR
        assert "provider error" in s.reason.lower()

    def test_yahoo_not_ready_when_no_backend(self, monkeypatch):
        # A Yahoo provider with NO usable backend (yfinance missing OR
        # init failed) -> NOT_READY. This must be tested WITHOUT making
        # a real network call, so we force the YahooFinanceProvider
        # import/construction to fail deterministically regardless of
        # whether yfinance is installed on the host.
        def _boom(*args, **kwargs):
            raise ImportError("simulated yfinance unavailable")

        # ``YahooDataProvider.__init__`` imports YahooFinanceProvider
        # lazily; patch the engine module so the import fails.
        monkeypatch.setattr(
            "engine.data.yahoo_provider.YahooFinanceProvider", _boom,
        )
        p = YahooDataProvider(provider=None)
        assert p._provider is None  # construction failed gracefully
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.available is False
        assert s.provider_status is ProviderStatus.NOT_READY
        assert s.data_source == "yahoo"
        assert "unavailable" in s.reason.lower()

    def test_no_fixture_fallback_on_live_failure(self):
        # A failed live request must NOT substitute fixture data.
        backend = _FakeYahooBackend()
        backend.raise_on.add(("^NSEI", "15m"))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.data_source == "yahoo"
        assert not s.available
        # No fixture candles leaked in.
        assert s.setup_candles == ()


# ============================================================
# H. UNSUPPORTED INSTRUMENT
# ============================================================


class TestUnsupportedInstrument:
    def test_fixture_unknown_instrument(self):
        p = make_provider("fixture")
        s = p.fetch("BOGUS", "15m")
        assert s.available is False
        assert s.provider_status is ProviderStatus.UNSUPPORTED
        assert "not in fixture set" in s.reason

    def test_yahoo_unknown_instrument_passes_through(self):
        # Unknown instruments pass through verbatim to Yahoo.
        backend = _FakeYahooBackend()
        backend.responses[("BOGUS", "15m")] = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        backend.responses[("BOGUS", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("BOGUS", "15m", reference_now=NOW + timedelta(minutes=1))
        assert s.available is True
        assert ("BOGUS", "15m") in backend.calls


# ============================================================
# I. UNSUPPORTED TIMEFRAME
# ============================================================


class TestUnsupportedTimeframe:
    def test_yahoo_unsupported_timeframe(self):
        backend = _FakeYahooBackend()
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "7d", reference_now=NOW)
        assert s.available is False
        assert s.provider_status is ProviderStatus.UNSUPPORTED
        assert "not supported" in s.reason.lower()

    def test_fixture_unsupported_timeframe(self):
        p = make_provider("fixture")
        s = p.fetch("NIFTY", "1h")
        assert s.available is False
        assert s.provider_status is ProviderStatus.UNSUPPORTED


# ============================================================
# J. COMPLETED-CANDLE DETECTION
# ============================================================


class TestCompletedCandleDetection:
    def test_completed_includes_only_closed(self):
        candles = _series(100.0, 3, 15, NOW - timedelta(minutes=3 * 15))
        # forming candle opened at NOW - 5min, closes NOW + 10min
        forming = _candle(NOW - timedelta(minutes=5), 110.0)
        candles.append(forming)
        r = split_completed_candles(candles, "15m", NOW)
        assert len(r.completed) == 3
        assert forming not in r.completed
        assert r.forming is forming
        assert r.latest_completed_timestamp == candles[2].timestamp

    def test_latest_completed_timestamp(self):
        candles = _series(100.0, 5, 15, NOW - timedelta(minutes=5 * 15))
        r = split_completed_candles(candles, "15m", NOW)
        assert r.latest_completed_timestamp == candles[-1].timestamp

    def test_dedupes_by_timestamp(self):
        c = _series(100.0, 2, 15, NOW - timedelta(minutes=30))
        dup = _candle(c[0].timestamp, 50.0)
        r = split_completed_candles(list(c) + [dup], "15m", NOW)
        assert len(r.completed) == 2

    def test_sorts_chronologically(self):
        c1 = _series(100.0, 2, 15, NOW - timedelta(minutes=30))
        out_of_order = [c1[1], c1[0]]
        r = split_completed_candles(out_of_order, "15m", NOW)
        assert r.completed[0].timestamp < r.completed[1].timestamp


# ============================================================
# K. FORMING-CANDLE EXCLUSION
# ============================================================


class TestFormingCandleExclusion:
    def test_forming_candle_excluded_from_engine_input(self):
        backend = _FakeYahooBackend()
        completed = _series(100.0, 4, 15, NOW - timedelta(minutes=4 * 15))
        forming = _candle(NOW, 105.0)
        backend.responses[("^NSEI", "15m")] = completed + [forming]
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW + timedelta(minutes=1))
        assert forming not in s.setup_candles
        assert s.forming_setup_candle is forming

    def test_forming_candle_does_not_change_analysis(self, monkeypatch):
        # Analysis with the forming candle present in the raw feed must
        # equal analysis without it (the boundary strips it either way).
        backend = _FakeYahooBackend()
        completed = _series(100.0, 30, 15, NOW - timedelta(minutes=30 * 15))
        backend.responses[("^NSEI", "15m")] = completed
        backend.responses[("^NSEI", "1d")] = _series(95.0, 20, 1440, NOW - timedelta(days=20))
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)

        v1 = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))

        # Now add a forming candle to the raw feed and re-run.
        forming = _candle(NOW, 9999.0, high=10000.0, low=9998.0)
        backend.responses[("^NSEI", "15m")] = completed + [forming]
        v2 = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))

        assert v1.decision.decision_classification == v2.decision.decision_classification
        assert v1.decision.decision_score == v2.decision.decision_score
        assert v1.geometry.entry == v2.geometry.entry
        assert v1.geometry.stop == v2.geometry.stop
        assert v1.geometry.target_1 == v2.geometry.target_1


# ============================================================
# L. FUTURE TIMESTAMP PROTECTION
# ============================================================


class TestFutureTimestampProtection:
    def test_future_candles_rejected(self):
        candles = _series(100.0, 2, 15, NOW - timedelta(minutes=30))
        future = _candle(NOW + timedelta(minutes=30), 200.0)
        r = split_completed_candles(candles + [future], "15m", NOW)
        assert future in r.rejected_future
        assert future not in r.completed

    def test_provider_reports_rejected_future_count(self):
        backend = _FakeYahooBackend()
        good = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        future = _candle(NOW + timedelta(minutes=30), 200.0)
        backend.responses[("^NSEI", "15m")] = good + [future]
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.rejected_future_count == 1
        assert future not in s.setup_candles


# ============================================================
# M. STALE-DATA DETECTION
# ============================================================


class TestStaleDataDetection:
    def test_classify_stale(self):
        old_ts = NOW - timedelta(days=10)
        st = classify_freshness(
            latest_completed_timestamp=old_ts,
            reference_now=NOW,
            staleness_seconds=60,
            provider_status=ProviderStatus.OK,
        )
        assert st is FreshnessState.STALE

    def test_fixture_data_reported_stale(self):
        p = make_provider("fixture")
        s = p.fetch("NIFTY", "15m")
        assert s.freshness_state is FreshnessState.STALE


# ============================================================
# N. FRESH-DATA DETECTION
# ============================================================


class TestFreshDataDetection:
    def test_classify_current(self):
        recent = NOW - timedelta(seconds=30)
        st = classify_freshness(
            latest_completed_timestamp=recent,
            reference_now=NOW,
            staleness_seconds=60,
            provider_status=ProviderStatus.OK,
        )
        assert st is FreshnessState.CURRENT

    def test_yahoo_fresh_data(self):
        backend = _FakeYahooBackend()
        recent = _series(100.0, 3, 15, NOW - timedelta(minutes=45))
        backend.responses[("^NSEI", "15m")] = recent
        backend.responses[("^NSEI", "1d")] = _series(95.0, 3, 1440, NOW - timedelta(days=3))
        p = _yahoo_with_backend(
            backend,
            freshness_config=FreshnessConfig(default_staleness_seconds=3600),
        )
        s = p.fetch("NIFTY", "15m", reference_now=NOW)
        assert s.freshness_state is FreshnessState.CURRENT

    def test_classify_unavailable_when_no_completed(self):
        st = classify_freshness(
            latest_completed_timestamp=None,
            reference_now=NOW,
            staleness_seconds=60,
            provider_status=ProviderStatus.OK,
        )
        assert st is FreshnessState.UNAVAILABLE

    def test_classify_unavailable_on_error(self):
        recent = NOW - timedelta(seconds=10)
        st = classify_freshness(
            latest_completed_timestamp=recent,
            reference_now=NOW,
            staleness_seconds=3600,
            provider_status=ProviderStatus.ERROR,
        )
        assert st is FreshnessState.UNAVAILABLE

    def test_freshness_config_per_timeframe_override(self):
        cfg = FreshnessConfig(
            default_staleness_seconds=60,
            timeframe_overrides=(("1D", 172800),),
        )
        assert cfg.staleness_seconds_for("1m") == 60
        assert cfg.staleness_seconds_for("1D") == 172800
        assert cfg.staleness_seconds_for("1d") == 172800  # case-insensitive


# ============================================================
# O. DASHBOARD LIVE-DATA INTEGRATION
# ============================================================


class TestDashboardLiveDataIntegration:
    def test_data_source_view_populated_for_fixture(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.data_source.data_source == "fixture"
        assert v.data_source.provider_status == "OK"
        assert v.data_source.latest_completed_candle_timestamp is not None

    def test_api_exposes_data_source_block(self, client):
        r = client.get("/api/analysis?instrument=NIFTY&timeframe=15m")
        j = r.json()
        assert "data_source" in j
        assert j["data_source"]["data_source"] == "fixture"
        assert j["data_source"]["freshness_state"] == "STALE"
        assert "latest_completed_candle_timestamp" in j["data_source"]

    def test_health_exposes_data_source_and_supported_timeframes(self, client):
        r = client.get("/health")
        j = r.json()
        assert j["data_source"] == "fixture"
        assert "supported_timeframes" in j
        assert "15m" in j["supported_timeframes"]

    def test_live_provider_service_view(self):
        backend = _FakeYahooBackend()
        backend.responses[("^NSEI", "15m")] = _series(100.0, 30, 15, NOW - timedelta(minutes=30 * 15))
        backend.responses[("^NSEI", "1d")] = _series(95.0, 20, 1440, NOW - timedelta(days=20))
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)
        v = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
        assert v.data_source.data_source == "yahoo"
        assert v.data_source.provider_status == "OK"


# ============================================================
# P. DASHBOARD UNAVAILABLE STATE
# ============================================================


class TestDashboardUnavailableState:
    def test_unavailable_instrument_view(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="BOGUS", setup_timeframe="15m"),
        )
        assert v.actionability.value == "INVALID"
        assert v.complete is False
        assert v.data_source.data_source == "fixture"
        assert v.data_source.provider_status == "UNSUPPORTED"

    def test_live_provider_failure_unavailable(self):
        backend = _FakeYahooBackend()
        backend.raise_on.add(("^NSEI", "15m"))
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)
        v = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
        assert v.complete is False
        assert v.data_source.provider_status == "ERROR"
        # No fixture fallback leaked into the engine input.
        assert v.geometry.entry is None
        assert any("ERROR" in w for w in v.warnings)


# ============================================================
# Q. DASHBOARD STALE STATE
# ============================================================


class TestDashboardStaleState:
    def test_stale_warning_surfaced(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.data_source.freshness_state == "STALE"
        assert any("STALE" in w for w in v.warnings)

    def test_stale_does_not_block_analysis(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # Stale data still produces an honest analysis over completed candles.
        assert v.complete is True


# ============================================================
# R. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_service_does_not_call_outcome_evaluator(self, fixture_service, monkeypatch):
        import engine.intelligence.historical_outcome as ho

        def _boom(*a, **k):
            raise RuntimeError("outcome evaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.actionability is not None

    def test_service_does_not_call_pipeline(self, fixture_service, monkeypatch):
        import engine.pipeline.historical_pipeline as hp

        def _boom(*a, **k):
            raise RuntimeError("pipeline must not be called")

        monkeypatch.setattr(hp.HistoricalEvaluationPipeline, "evaluate", _boom)
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.actionability is not None

    def test_public_api_has_no_future_candle_argument(self):
        sig = inspect.signature(DashboardAnalysisService.analyze)
        assert "future" not in sig.parameters
        assert "future_candles" not in sig.parameters

    def test_provider_fetch_has_no_future_candle_argument(self):
        from dashboard.data_provider import FixtureDataProvider, YahooDataProvider

        for cls in (FixtureDataProvider, YahooDataProvider):
            sig = inspect.signature(cls.fetch)
            assert "future" not in sig.parameters
            assert "future_candles" not in sig.parameters

    def test_future_candle_does_not_change_fixed_T(self):
        from engine.config.market_scan_config import MarketScanConfig
        from engine.intelligence.market_scanner import (
            InstrumentDataset,
            MarketScanner,
            ScanEngines,
        )
        from engine.data.historical_fixtures import (
            historical_candles_by_instrument,
        )

        data = historical_candles_by_instrument(("NIFTY",), "1D", "15M")
        setup = data["NIFTY"]["15M"]
        ctx = data["NIFTY"]["1D"]
        T = setup[-1].timestamp
        svc = DashboardAnalysisService(provider=_StaticProvider(ctx, setup))
        v1 = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))

        mutated = setup + [
            OHLCVCandle(
                timestamp=T + timedelta(minutes=15),
                open=9999.0, high=10000.0, low=9998.0, close=9999.0,
                volume=10**9,
            )
        ]
        cfg = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        ds = InstrumentDataset(
            instrument="NIFTY", context_candles=tuple(ctx),
            setup_candles=tuple(mutated),
        )
        scan = MarketScanner(cfg).scan(
            [ds], evaluation_time=T, engines=ScanEngines.default(),
        )
        r = scan.results[0]
        assert v1.decision.decision_classification == r.decision_classification
        assert v1.geometry.entry == getattr(
            r.decision.candidate, "entry_reference", None,
        )

    def test_live_forming_candle_not_in_engine_input(self, monkeypatch):
        backend = _FakeYahooBackend()
        completed = _series(100.0, 30, 15, NOW - timedelta(minutes=30 * 15))
        forming = _candle(NOW, 9999.0, high=10000.0, low=9998.0)
        backend.responses[("^NSEI", "15m")] = completed + [forming]
        backend.responses[("^NSEI", "1d")] = _series(95.0, 20, 1440, NOW - timedelta(days=20))
        p = _yahoo_with_backend(backend)
        ref = NOW + timedelta(minutes=1)
        # Pin the provider's clock so the boundary is deterministic.
        orig_fetch = p.fetch
        monkeypatch.setattr(
            p,
            "fetch",
            lambda inst, tf, lookback_bars=300, **kw: orig_fetch(
                inst, tf, lookback_bars, reference_now=ref,
            ),
        )
        s = p.fetch("NIFTY", "15m")
        assert forming not in s.setup_candles
        assert s.forming_setup_candle is forming
        assert s.latest_completed_candle_timestamp == completed[-1].timestamp
        # The service then uses this latest_completed_candle_timestamp as
        # the evaluation point — never the forming candle.
        svc = DashboardAnalysisService(provider=p)
        v = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
        assert v.data_source.forming_candle_present is True
        assert v.evaluation_timestamp == s.latest_completed_candle_timestamp


class _StaticProvider:
    """Provider returning a fixed candle series (for no-look-ahead tests)."""

    def __init__(self, context, setup):
        self._context = tuple(context)
        self._setup = tuple(setup)
        self.freshness_config = FreshnessConfig()

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return setup_timeframe in ("15M", "15m")

    def fetch(self, instrument, setup_timeframe, lookback_bars=300):
        return InstrumentSeries(
            instrument=instrument,
            context_candles=self._context,
            setup_candles=self._setup,
            available=bool(self._setup),
            data_source="static",
            provider_status=ProviderStatus.OK,
            latest_completed_candle_timestamp=(
                self._setup[-1].timestamp if self._setup else None
            ),
        )

    def last_updated(self, instrument, setup_timeframe):
        return self._setup[-1].timestamp if self._setup else None


# ============================================================
# S. EXISTING DECISION PRESERVATION
# ============================================================


class TestExistingDecisionPreservation:
    def test_decision_classification_authoritative(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # The classification is one of the existing authoritative values.
        assert v.decision.decision_classification in (
            "", "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
        )

    def test_decision_not_renamed_to_buy_sell(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert "BUY" not in v.decision.decision_classification
        assert "SELL" not in v.decision.decision_classification
        assert "ENTER" not in v.decision.decision_classification
        assert "EXIT" not in v.decision.decision_classification
        assert "HOLD" not in v.decision.decision_classification


# ============================================================
# T. EXISTING TRADE GEOMETRY PRESERVATION
# ============================================================


class TestExistingTradeGeometryPreservation:
    def test_geometry_sourced_from_candidate(self, fixture_service):
        from engine.intelligence.market_scanner import (
            InstrumentDataset,
            MarketScanner,
            ScanEngines,
        )
        from engine.config.market_scan_config import MarketScanConfig
        from engine.data.historical_fixtures import (
            historical_candles_by_instrument,
        )

        data = historical_candles_by_instrument(("NIFTY",), "1D", "15M")
        setup = data["NIFTY"]["15M"]
        ctx = data["NIFTY"]["1D"]
        cfg = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        ds = InstrumentDataset(
            instrument="NIFTY", context_candles=tuple(ctx),
            setup_candles=tuple(setup),
        )
        scan = MarketScanner(cfg).scan(
            [ds], evaluation_time=setup[-1].timestamp,
            engines=ScanEngines.default(),
        )
        r = scan.results[0]
        cand = getattr(r.decision, "candidate", None) if r.decision else None
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        if cand is not None:
            assert v.geometry.entry == getattr(cand, "entry_reference", None)
            assert v.geometry.stop == getattr(cand, "stop_reference", None)
            assert v.geometry.target_1 == getattr(cand, "target_reference", None)

    def test_geometry_not_recomputed_in_dashboard(self, fixture_service):
        # The dashboard reuses geometry verbatim — no second target invented.
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.geometry.target_2 is None
        assert v.geometry.target_2_supported is False


# ============================================================
# U. TARGET 2 REMAINS UNSUPPORTED
# ============================================================


class TestTarget2Unsupported:
    def test_target_2_none(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.geometry.target_2 is None
        assert v.geometry.target_2_supported is False

    def test_api_target_2_unsupported(self, client):
        j = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
        assert j["geometry"]["target_2"] is None
        assert j["geometry"]["target_2_supported"] is False


# ============================================================
# V. EXISTING PIPELINE BASELINE
# ============================================================


class TestExistingPipelineBaseline:
    def test_pipeline_signals_4_trades_3(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )

        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
            trending_dataset(),
        )
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3


# ============================================================
# W. EXISTING FIXTURE BEHAVIOR
# ============================================================


class TestExistingFixtureBehavior:
    def test_fixture_instruments_unchanged(self):
        assert set(FIXTURE_INSTRUMENTS) == {
            "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
        }

    def test_fixture_supported_timeframes_label(self):
        assert "15m" in SUPPORTED_TIMEFRAMES
        assert "1D" in SUPPORTED_TIMEFRAMES

    def test_fixture_analysis_deterministic(self, fixture_service):
        a = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        b = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert to_jsonable(a) == to_jsonable(b)


# ============================================================
# X. SERIALIZATION / BACKWARD COMPATIBILITY
# ============================================================


class TestSerializationBackwardCompat:
    def test_instrument_series_backward_compat_construction(self):
        # Old keyword construction (no new fields) still works.
        s = InstrumentSeries(
            instrument="X",
            context_candles=(),
            setup_candles=(),
            available=False,
            reason="legacy",
        )
        assert s.instrument == "X"
        assert s.data_source == ""
        assert s.provider_status is ProviderStatus.NOT_READY
        assert s.freshness_state is FreshnessState.UNAVAILABLE

    def test_data_source_view_defaults(self):
        v = DataSourceView()
        assert v.data_source == ""
        assert v.provider_status == ""
        assert v.freshness_state == ""
        assert v.latest_completed_candle_timestamp is None
        assert v.forming_candle_present is False

    def test_to_jsonable_includes_data_source(self, fixture_service):
        v = fixture_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        j = to_jsonable(v)
        assert "data_source" in j
        assert j["data_source"]["data_source"] == "fixture"

    def test_candle_boundary_result_defaults(self):
        r = CandleBoundaryResult()
        assert r.completed == ()
        assert r.forming is None
        assert r.rejected_future == ()
        assert r.latest_completed_timestamp is None

    def test_default_service_accepts_freshness_and_symbol_map(self):
        svc = default_service(
            "fixture",
            freshness_config=FreshnessConfig(default_staleness_seconds=10),
        )
        assert svc.freshness_config.default_staleness_seconds == 10

    def test_default_service_yahoo_with_symbol_map(self, monkeypatch):
        # No network: force the Yahoo provider NOT_READY (simulating
        # yfinance missing / init failed) so construction succeeds
        # gracefully and analysis degrades to an honest unavailable
        # view — regardless of whether yfinance is installed on the host.
        def _boom(*args, **kwargs):
            raise ImportError("simulated yfinance unavailable")

        monkeypatch.setattr(
            "engine.data.yahoo_provider.YahooFinanceProvider", _boom,
        )
        svc = default_service(
            "yahoo", symbol_map={"NIFTY": "^NSEI"},
        )
        v = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
        # Not ready -> unavailable, honest, no crash, no fixture fallback.
        assert v.complete is False
        assert v.data_source.data_source == "yahoo"
        assert v.data_source.provider_status == "NOT_READY"
        assert v.geometry.entry is None  # no fabricated geometry
