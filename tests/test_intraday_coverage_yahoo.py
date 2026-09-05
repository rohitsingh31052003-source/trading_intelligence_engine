"""
Checkpoint 19.2 — live/near-live Yahoo provider coverage path.

Proves the OPTIONAL live Yahoo provider is exercised through the SAME
coverage layer deterministically (injected fake backend — no network,
no credentials, no live market dependency):

* capability declaration is explicit (Yahoo declares support for any
  instrument via pass-through semantics — a capability claim, never a
  data-coverage claim);
* a served fresh series classifies VALID;
* a stale series classifies STALE;
* an empty response classifies EMPTY;
* a provider failure classifies PROVIDER_ERROR;
* the completed-candle boundary (no future candles) is enforced.

These tests are SEPARATE from the deterministic fixture suite only
because they exercise the optional Yahoo adapter; they remain fully
offline and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dashboard.data_provider import YahooDataProvider
from dashboard.intraday_coverage import (
    IntradayCoverageEngine,
    IntradayCoverageStatus,
)
from engine.models.ohlcv import OHLCVCandle

NOW = datetime(2026, 9, 4, 5, 30, tzinfo=UTC)  # Fri 11:00 IST


class _FakeYahooBackend:
    """Deterministic stand-in for the Yahoo provider (no network)."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], list[OHLCVCandle]] = {}
        self.raise_on: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    def connect(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def get_history(self, symbol, start, end, interval):
        key = (symbol, interval)
        self.calls.append(key)
        if key in self.raise_on:
            raise RuntimeError("simulated network failure")
        return list(self.responses.get(key, []))


def _candle(ts: datetime) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts, open=100, high=101, low=99, close=100, volume=1000,
    )


def _yahoo(backend: _FakeYahooBackend) -> YahooDataProvider:
    return YahooDataProvider(provider=backend)


def _fresh_series() -> list[OHLCVCandle]:
    end = datetime(2026, 9, 4, 5, 15, tzinfo=UTC)  # 10:45 IST
    return [_candle(end - timedelta(minutes=15 * i)) for i in range(20)]


class TestYahooCapability:
    def test_yahoo_declares_support_for_any_instrument(self):
        engine = IntradayCoverageEngine(provider=_yahoo(_FakeYahooBackend()))
        caps = engine.provider_capabilities(["RELIANCE", "INVENTED"], "15m")
        assert all(c.supported for c in caps)

    def test_yahoo_unsupported_timeframe(self):
        # "3m" is a canonical intraday timeframe but NOT a Yahoo interval.
        engine = IntradayCoverageEngine(provider=_yahoo(_FakeYahooBackend()))
        caps = engine.provider_capabilities(["RELIANCE"], "3m")
        assert all(not c.supported for c in caps)
        assert all(c.reason for c in caps)


class TestYahooCoverage:
    def test_fresh_series_valid(self):
        backend = _FakeYahooBackend()
        backend.responses[("RELIANCE.NS", "15m")] = _fresh_series()
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        res = engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert res.status is IntradayCoverageStatus.VALID
        assert res.candle_count == 20

    def test_stale_series(self):
        backend = _FakeYahooBackend()
        # Last candle at 09:30 IST (2 x 15m before 11:00 IST).
        series = [
            _candle(datetime(2026, 9, 4, 3, 0, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 3, 15, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 3, 30, tzinfo=UTC)),
        ]
        backend.responses[("RELIANCE.NS", "15m")] = series
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        res = engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert res.status is IntradayCoverageStatus.STALE

    def test_empty_response(self):
        backend = _FakeYahooBackend()
        backend.responses[("RELIANCE.NS", "15m")] = []
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        res = engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert res.status is IntradayCoverageStatus.EMPTY

    def test_provider_failure(self):
        backend = _FakeYahooBackend()
        backend.raise_on.add(("RELIANCE.NS", "15m"))
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        res = engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert res.status is IntradayCoverageStatus.PROVIDER_ERROR

    def test_future_candle_rejected(self):
        backend = _FakeYahooBackend()
        series = [
            _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 6, 0, tzinfo=UTC)),  # future
        ]
        backend.responses[("RELIANCE.NS", "15m")] = series
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        res = engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert res.rejected_future_count == 1
        assert res.candle_count == 1
        assert any(i.code == "FUTURE_DATED" for i in res.issues)

    def test_symbol_mapping_used(self):
        backend = _FakeYahooBackend()
        backend.responses[("RELIANCE.NS", "15m")] = _fresh_series()
        engine = IntradayCoverageEngine(provider=_yahoo(backend))
        engine.assess_instrument("RELIANCE", reference_now=NOW)
        assert ("RELIANCE.NS", "15m") in backend.calls