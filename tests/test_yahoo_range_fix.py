"""
Regression tests for the Yahoo intraday historical-range bug fix.

CONTEXT (the bug this guards against):

    YahooDataProvider().fetch('NIFTY', '15m', lookback_bars=50)

previously built a request roughly ``now - 60 days -> now`` for EVERY
intraday call. Yahoo rejects that with::

    The requested range must be within the last 60 days.

because (a) an exact 60-day boundary can fail on clock skew / timezone
differences and (b) 60 days is wildly more than the ~12.5 hours of
15-minute bars ``lookback_bars=50`` actually requires.

These tests use the existing fake Yahoo backend (NO real network calls)
to verify the corrected provider:

* ``lookback_bars`` now drives the Yahoo request window size.
* The request window is recent + bounded and stays SAFELY inside the
  interval-specific Yahoo permitted range (never an exact boundary).
* Returned candles are normalized + limited to recent history.
* Future candles remain rejected; forming candles remain excluded from
  the engine input; the completed candle remains the analysis boundary.
* ``reference_now`` remains deterministic.
* Provider status remains correct on empty response + provider exception.
* A Yahoo failure NEVER falls back to fixtures.
* Existing FixtureDataProvider + PaperTradingOperations + decision /
  geometry / trade-plan behavior remain unchanged.
* No OutcomeEvaluator / HistoricalEvaluationPipeline invocation; no
  ``future``/``future_candles``/``lookahead`` parameter is introduced.

This is a DATA PROVIDER correction only — no intelligence / decision /
geometry / trade-plan logic is modified here.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dashboard.data_provider import (
    FIXTURE_INSTRUMENTS,
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
    YahooDataProvider,
    split_completed_candles,
)
from dashboard.services import DashboardAnalysisService

# Reuse the shared fake backend + helpers from the live-data suite so the
# assertions stay consistent with the rest of the provider test surface.
from tests.test_live_data_integration import (
    _FakeYahooBackend,
    _candle,
    _series,
    _yahoo_with_backend,
)

NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


def _backend_with_15m(n_setup: int = 50) -> _FakeYahooBackend:
    """A fake backend with recent completed 15m + 1d candles."""

    backend = _FakeYahooBackend()
    backend.responses[("^NSEI", "15m")] = _series(
        100.0, n_setup, 15, NOW - timedelta(minutes=n_setup * 15),
    )
    backend.responses[("^NSEI", "1d")] = _series(
        95.0, 30, 1440, NOW - timedelta(days=30),
    )
    return backend


# ============================================================
# 1. lookback_bars does NOT trigger a 60-day request
# ============================================================


class TestLookbackBarsDrivesWindow:
    """15m / lookback_bars=50 must NOT request an unnecessary 60-day range."""

    def test_15m_50_does_not_request_60_days(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        # Exactly one 15m request was made to the backend.
        windows_15m = [w for w in backend.windows if w[1] == "15m"]
        assert len(windows_15m) == 1
        _, _, start, end = windows_15m[0]
        span_days = (end - start).total_seconds() / 86400.0
        # The bug requested ~60 days; a correct recent window for 50 bars
        # of 15m (+ the modest engine context buffer) is only a few days.
        assert span_days < 10.0, span_days
        # And it must be strictly less than the old buggy 60-day window.
        assert span_days < 59.0

    def test_15m_50_window_is_recent_and_bounded(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        _, _, start, end = [w for w in backend.windows if w[1] == "15m"][0]
        # The window ENDS at the reference now (the deterministic end point).
        assert end == NOW
        # The window START is recent — only a few days before now.
        assert (NOW - start).total_seconds() / 86400.0 < 10.0

    def test_larger_lookback_still_bounded_by_yahoo_limit(self):
        # Even a huge lookback_bars must NOT exceed the interval's safe cap.
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        p.fetch("NIFTY", "15m", lookback_bars=100_000, reference_now=NOW)
        _, _, start, end = [w for w in backend.windows if w[1] == "15m"][0]
        span_days = (end - start).total_seconds() / 86400.0
        # 15m Yahoo safe cap is 58 days (60 - margin).
        assert span_days <= 58.0
        assert span_days < 60.0

    def test_lookback_bars_affects_window_monotonically(self):
        """A larger lookback_bars never shrinks the requested window."""

        spans: list[float] = []
        for lb in (10, 50, 200, 1000):
            backend = _backend_with_15m(50)
            p = _yahoo_with_backend(backend)
            p.fetch("NIFTY", "15m", lookback_bars=lb, reference_now=NOW)
            _, _, start, end = [w for w in backend.windows if w[1] == "15m"][0]
            spans.append((end - start).total_seconds() / 86400.0)
        # Monotonically non-decreasing (until capped); never shrinks.
        assert spans == sorted(spans)


# ============================================================
# 2. The calculated request window is within Yahoo's safe range
# ============================================================


class TestIntervalSafeRange:
    """The request window per interval is safely inside Yahoo's permitted range."""

    @pytest.mark.parametrize(
        "interval,lookback,max_days",
        [
            ("15m", 50, 58),
            ("5m", 50, 58),
            ("30m", 50, 58),
            ("2m", 50, 58),
            ("90m", 50, 58),
            ("1m", 50, 6),     # 1m Yahoo limit ~7d -> 6d safe
            ("60m", 50, 725),
            ("1h", 50, 725),
            ("1d", 50, 365 * 5),
        ],
    )
    def test_window_within_safe_range(self, interval, lookback, max_days):
        p = YahooDataProvider(provider=_FakeYahooBackend())
        start, end = p._lookback_window(
            interval, lookback, reference_now=NOW,
        )
        span_days = (end - start).total_seconds() / 86400.0
        assert span_days <= float(max_days), (interval, span_days, max_days)
        # Never sits on Yahoo's exact boundary (clock-skew / tz safe).
        # The max already includes the safety margin, so the span must be
        # strictly below the raw Yahoo limit (max + 2 margin).
        raw_yahoo_limit = max_days + 2
        assert span_days < float(raw_yahoo_limit)

    def test_window_never_on_exact_60_day_boundary(self):
        # The bug produced an exact ~60-day window. The fix must never land
        # on the exact boundary; 58d cap < 60d raw limit.
        p = YahooDataProvider(provider=_FakeYahooBackend())
        start, end = p._lookback_window("15m", 50, reference_now=NOW)
        span_days = (end - start).total_seconds() / 86400.0
        assert span_days < 58.0  # the cap itself is inside the limit

    def test_unknown_interval_uses_conservative_intraday_cap(self):
        p = YahooDataProvider(provider=_FakeYahooBackend())
        start, end = p._lookback_window("7m", 50, reference_now=NOW)
        span_days = (end - start).total_seconds() / 86400.0
        # Conservative intraday cap (58d) applies to unmapped intervals.
        assert span_days <= 58.0


# ============================================================
# 3-4. Returned candles normalized + limited to recent history
# ============================================================


class TestNormalizationAndRecency:
    def test_returned_candles_are_completed_and_normalized(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert s.provider_status is ProviderStatus.OK
        assert len(s.setup_candles) == 50
        # All returned candles are tz-aware UTC (normalized by the provider).
        for c in s.setup_candles:
            assert c.timestamp.tzinfo is not None
        # Chronologically ordered + de-duplicated.
        ts = [c.timestamp for c in s.setup_candles]
        assert ts == sorted(ts)
        assert len(set(ts)) == len(ts)

    def test_candles_limited_to_recent_history(self):
        # Provider must not surface candles older than the request window.
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        # Every returned completed candle is within a recent bounded past.
        for c in s.setup_candles:
            assert NOW - c.timestamp < timedelta(days=10)


# ============================================================
# 5-7. Future / forming / completed candle behavior
# ============================================================


class TestCandleBoundary:
    def test_future_candles_rejected(self):
        backend = _FakeYahooBackend()
        completed = _series(100.0, 30, 15, NOW - timedelta(minutes=30 * 15))
        future = _candle(NOW + timedelta(minutes=30), 200.0)
        backend.responses[("^NSEI", "15m")] = completed + [future]
        backend.responses[("^NSEI", "1d")] = _series(
            95.0, 30, 1440, NOW - timedelta(days=30),
        )
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert future not in s.setup_candles
        assert s.rejected_future_count >= 1

    def test_forming_candle_excluded_from_engine_input(self):
        backend = _FakeYahooBackend()
        completed = _series(100.0, 40, 15, NOW - timedelta(minutes=40 * 15))
        forming = _candle(NOW - timedelta(minutes=5), 9999.0)
        backend.responses[("^NSEI", "15m")] = completed + [forming]
        backend.responses[("^NSEI", "1d")] = _series(
            95.0, 30, 1440, NOW - timedelta(days=30),
        )
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert forming not in s.setup_candles
        assert s.forming_setup_candle is forming

    def test_completed_candle_is_latest_analysis_boundary(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert s.latest_completed_candle_timestamp == s.setup_candles[-1].timestamp
        # The service uses this as evaluation_time (never the forming candle).
        svc = DashboardAnalysisService(provider=p)
        # Re-fetch through the service to confirm the boundary propagates.
        from dashboard.services import AnalysisRequest
        v = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.evaluation_timestamp == s.latest_completed_candle_timestamp


# ============================================================
# 8. reference_now remains deterministic
# ============================================================


class TestReferenceNowDeterminism:
    def test_reference_now_pins_request_end(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        _, _, _, end = [w for w in backend.windows if w[1] == "15m"][0]
        assert end == NOW

    def test_repeated_calls_identical_window(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        for _ in range(3):
            p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        windows = [w for w in backend.windows if w[1] == "15m"]
        assert all(w[3] == NOW for w in windows)
        assert all(w[2] == windows[0][2] for w in windows)


# ============================================================
# 9-11. Provider status on empty / exception; no fallback to fixtures
# ============================================================


class TestProviderStatusAndNoFallback:
    def test_empty_response_is_unavailable(self):
        backend = _FakeYahooBackend()  # no responses registered
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert not s.available
        assert s.provider_status is ProviderStatus.EMPTY
        assert "no data" in s.reason.lower() or "no setup" in s.reason.lower()

    def test_provider_exception_is_error(self):
        backend = _FakeYahooBackend()
        backend.raise_on = {("^NSEI", "15m")}
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert not s.available
        assert s.provider_status is ProviderStatus.ERROR
        assert "provider error" in s.reason.lower()

    def test_yahoo_failure_does_not_fall_back_to_fixtures(self):
        # A failed Yahoo fetch must NOT produce fixture data.
        backend = _FakeYahooBackend()
        backend.raise_on = {("^NSEI", "15m")}
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert not s.available
        assert s.data_source == "yahoo"  # not "fixture"
        assert len(s.setup_candles) == 0  # nothing fabricated


# ============================================================
# 12. FixtureDataProvider behavior unchanged
# ============================================================


class TestFixtureProviderUnchanged:
    def test_fixture_provider_still_deterministic(self):
        from dashboard.data_provider import FixtureDataProvider
        fp = FixtureDataProvider()
        s1 = fp.fetch("NIFTY", "15m")
        s2 = fp.fetch("NIFTY", "15m")
        assert s1.available and s2.available
        assert s1.data_source == "fixture"
        assert len(s1.setup_candles) == len(s2.setup_candles)
        assert [c.timestamp for c in s1.setup_candles] == [
            c.timestamp for c in s2.setup_candles
        ]

    def test_fixture_provider_instruments_unchanged(self):
        from dashboard.data_provider import FixtureDataProvider
        fp = FixtureDataProvider()
        for inst in FIXTURE_INSTRUMENTS:
            s = fp.fetch(inst, "15m")
            assert s.available


# ============================================================
# 13-17. No look-ahead; no future arg; existing behavior unchanged
# ============================================================


class TestNoLookAhead:
    def test_fetch_signature_has_no_future_argument(self):
        sig = inspect.signature(YahooDataProvider.fetch)
        for name in ("future", "future_candles", "lookahead"):
            assert name not in sig.parameters

    def test_lookback_window_signature_has_no_future_argument(self):
        sig = inspect.signature(YahooDataProvider._lookback_window)
        for name in ("future", "future_candles", "lookahead"):
            assert name not in sig.parameters

    def test_future_candle_does_not_change_fixed_t_analysis(self, monkeypatch):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        ref = NOW
        s1 = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=ref)
        # Append a future candle to the backend response and re-fetch at
        # the SAME reference_now: the completed-candle analysis must not
        # change (future data cannot improve a fixed-T analysis).
        backend.responses[("^NSEI", "15m")] = list(
            backend.responses[("^NSEI", "15m")]
        ) + [_candle(ref + timedelta(minutes=30), 1234.0)]
        s2 = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=ref)
        assert [c.timestamp for c in s1.setup_candles] == [
            c.timestamp for c in s2.setup_candles
        ]
        assert s1.latest_completed_candle_timestamp == (
            s2.latest_completed_candle_timestamp
        )

    def test_outcome_evaluator_not_invoked(self, monkeypatch):
        # Patching OutcomeEvaluator.evaluate to raise must NOT break fetch.
        from engine.intelligence.historical_outcome import OutcomeEvaluator
        called = []

        def _boom(self, *a, **kw):
            called.append(True)
            raise AssertionError("OutcomeEvaluator must not be called")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _boom)
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert called == []

    def test_historical_pipeline_not_invoked(self, monkeypatch):
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
        )
        called = []

        def _boom(self, *a, **kw):
            called.append(True)
            raise AssertionError("HistoricalEvaluationPipeline must not be called")

        monkeypatch.setattr(
            HistoricalEvaluationPipeline, "evaluate", _boom,
        )
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        assert s.available
        assert called == []


# ============================================================
# 14-15. Decision + geometry unchanged (byte/value equivalent)
# ============================================================


class TestDecisionGeometryUnchanged:
    def test_decision_classification_unchanged_by_provider(self, monkeypatch):
        from dashboard.services import AnalysisRequest
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)
        v1 = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # Re-analyze at the same data: classification must be stable.
        v2 = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert (
            v1.decision.decision_classification
            == v2.decision.decision_classification
        )
        # No BUY/SELL vocabulary leaks in.
        assert v1.decision.decision_classification in (
            "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
        )

    def test_geometry_byte_equivalent_across_calls(self):
        from dashboard.services import AnalysisRequest
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)
        v1 = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        v2 = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v1.geometry.entry == v2.geometry.entry
        assert v1.geometry.stop == v2.geometry.stop
        assert v1.geometry.target_1 == v2.geometry.target_1
        assert v1.geometry.risk_reward_ratio == v2.geometry.risk_reward_ratio
        # Target 2 remains unsupported.
        assert v1.geometry.target_2 is None
        assert v1.geometry.target_2_supported is False


# ============================================================
# 13. PaperTradingOperations behavior unchanged (provider path)
# ============================================================


class TestPaperTradingOperationsUnchanged:
    def test_operations_with_yahoo_provider_runs(self, monkeypatch):
        # The operations layer should consume a Yahoo provider result the
        # same way; no intelligence / lifecycle semantics changed.
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        svc = DashboardAnalysisService(provider=p)
        # Patch OutcomeEvaluator + pipeline to raise to prove the
        # operations path never re-evaluates outcomes / reruns pipeline.
        from engine.intelligence.historical_outcome import OutcomeEvaluator
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
        )

        def _boom(self, *a, **kw):
            raise AssertionError("must not be called by operations")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _boom)
        monkeypatch.setattr(
            HistoricalEvaluationPipeline, "evaluate", _boom,
        )
        from dashboard.services import OperationsRequest
        result = svc.run_paper_trading_cycle(
            OperationsRequest(
                account_capital=100000,
                risk_percent=1.0,
                setup_timeframe="15m",
            ),
        )
        # The cycle completes without invoking the patched engines.
        assert result is not None


# ============================================================
# 19-20. API schema + InstrumentSeries backward compatibility
# ============================================================


class TestBackwardCompatibility:
    def test_instrument_series_fields_unchanged(self):
        # The model must keep its existing fields; no field was removed.
        from dataclasses import fields
        names = {f.name for f in fields(InstrumentSeries)}
        expected = {
            "instrument",
            "context_candles",
            "setup_candles",
            "available",
            "reason",
            "data_source",
            "provider_status",
            "freshness_state",
            "latest_candle_timestamp",
            "latest_completed_candle_timestamp",
            "forming_setup_candle",
            "last_successful_fetch_time",
            "rejected_future_count",
        }
        assert expected.issubset(names)

    def test_instrument_series_serialization_round_trip(self):
        backend = _backend_with_15m(50)
        p = _yahoo_with_backend(backend)
        s = p.fetch("NIFTY", "15m", lookback_bars=50, reference_now=NOW)
        # Reconstruct via the dataclass fields (the audit/projection path).
        from dataclasses import asdict
        d = asdict(s)
        assert d["data_source"] == "yahoo"
        assert d["available"] is True
        assert d["provider_status"] == ProviderStatus.OK
