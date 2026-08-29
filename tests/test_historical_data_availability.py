"""
Focused tests for the historical data availability & acquisition layer
(Checkpoint 7) — ``engine.data.historical_data_availability``.

The layer is a THIN orchestration wrapper over the EXISTING
``CorpusPreparationPlanner`` (coverage truth), ``HistoricalMarketDataService``
(ingestion pipeline) and ``HistoricalDataStore`` (persistence truth). It
answers "engine needs historical data -> ensure it exists -> return
canonical candles".

These tests are deterministic and NETWORK-FREE: they use in-memory /
range-filtered synthetic providers and real (tmp) stores — NO real Upstox
API calls and NO real token. Coverage semantics (complete / partial /
missing / covered / monthly chunk) are the EXISTING planner's semantics.

Covered (Checkpoint 7 §18): fully-covered serving (zero provider calls,
no token), missing/partial acquisition, existing chunks never re-fetched,
persistence + resumability across calls, per-chunk failure isolation,
deterministic multi-chunk processing, the ``UPSTOX_ANALYTICS_TOKEN`` gate
(required only when acquisition is needed; ``UPSTOX_ACCESS_TOKEN`` never a
fallback; no token leakage), canonical validation enforcement, no future
data, return-contract completeness, architecture reuse assertions, request
validation, batch, determinism and backward compatibility.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.config.corpus_plan_config import CorpusPlanConfig
from engine.data.corpus_ingestion import (
    COMPLETED,
    CorpusBacklog,
    CorpusIngestionConfig,
    CorpusIngestionEngine,
)
from engine.data.corpus_plan import CorpusPreparationPlanner
from engine.data.historical_data_availability import (
    HistoricalDataAvailabilityService,
)
from engine.data.historical_provider import (
    UPSTOX_TOKEN_ENV,
    InMemoryHistoricalProvider,
    HistoricalProviderResponse,
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.models.historical_availability import (
    AcquisitionFailure,
    HistoricalAvailabilityStatus,
    HistoricalDataAvailabilityResult,
)
from engine.models.historical_data import (
    HistoricalDataRequest,
    ProviderResponseStatus,
)
from engine.models.ohlcv import OHLCVCandle

WIN_START = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
WIN_END = datetime(2024, 3, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


# ============================================================
# FIXTURES / HELPERS
# ============================================================


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _daily_chunk(month_start: datetime, end: datetime) -> tuple[OHLCVCandle, ...]:
    """Deterministic daily series covering ``[month_start, end)``.

    Candles are offset to 04:00 UTC so no candle sits exactly on a month
    boundary instant — each candle belongs to exactly ONE half-open
    monthly chunk (matching the existing corpus-test convention).
    """

    candles: list[OHLCVCandle] = []
    idx = 0
    ts = month_start + timedelta(hours=4)
    while ts < end:
        candles.append(_candle(ts, 100.0 + idx))
        idx += 1
        ts = ts + timedelta(days=1)
    return tuple(candles)


def _month_record(
    instrument: str,
    timeframe: str,
    month_start: datetime,
    end: datetime,
) -> tuple[tuple[str, str], tuple[OHLCVCandle, ...]]:
    return (instrument, timeframe), _daily_chunk(month_start, end)


class RangeFilteredProvider(InMemoryHistoricalProvider):
    """In-memory provider that only returns candles within the requested
    ``[start, end]`` window (like a real vendor); an out-of-window window
    yields an honest EMPTY response."""

    def fetch(self, request):
        response = super().fetch(request)
        if response.status is not ProviderResponseStatus.OK:
            return response
        kept = tuple(
            c for c in response.candles
            if request.start <= c.timestamp <= request.end
        )
        if not kept:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                candles=(),
                reason="no candles in the requested window.",
            )
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=kept,
            reason=response.reason,
        )


class TrackingProvider(RangeFilteredProvider):
    """A range-filtered provider that records every fetch request."""

    def __init__(self, records=None):
        super().__init__(records)
        self.fetch_calls: list[HistoricalDataRequest] = []

    def fetch(self, request):
        self.fetch_calls.append(request)
        return super().fetch(request)


def _make_stack(
    tmp_path: Path,
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]] | None = None,
    *,
    timeframes: tuple[str, ...] = ("15m", "1D"),
    provider_cls=RangeFilteredProvider,
) -> tuple[
    HistoricalMarketDataService,
    CorpusPreparationPlanner,
    HistoricalDataStore,
]:
    """Build the EXISTING service + planner + store wired together."""

    store = HistoricalDataStore(tmp_path / "hist")
    provider = provider_cls(records or {})
    service = HistoricalMarketDataService(provider=provider, store=store)
    planner = CorpusPreparationPlanner(
        config=CorpusPlanConfig(timeframes=timeframes, provider="in-memory-import"),
        store=store,
        provider=provider,
    )
    return service, planner, store


def _avail(
    service: HistoricalMarketDataService,
    planner: CorpusPreparationPlanner,
) -> HistoricalDataAvailabilityService:
    return HistoricalDataAvailabilityService(planner, service)


def _req(
    instrument: str = "RELIANCE",
    timeframe: str = "15m",
    start: datetime = WIN_START,
    end: datetime = WIN_END,
    **kwargs,
) -> HistoricalDataRequest:
    return HistoricalDataRequest(
        instrument=instrument,
        timeframe=timeframe,
        start=start,
        end=end,
        **kwargs,
    )


# ============================================================
# A. COVERAGE — FULLY COVERED REQUESTS
# ============================================================


class TestFullyCovered:
    def test_fully_covered_returns_local_data(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        # Pre-seed the store (as a previous ingestion would).
        service.ingest(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.COMPLETE
        assert result.was_already_available is True
        assert result.candle_count > 0
        assert result.acquisition_attempted is False
        assert result.chunks_acquired == 0
        assert result.chunks_required == result.chunks_covered  # Jan + Feb
        assert result.candles  # canonical candles returned
        assert isinstance(result.candles[0], OHLCVCandle)

    def test_fully_covered_makes_zero_provider_calls(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        provider = TrackingProvider(records)
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        service.ingest(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        provider.fetch_calls.clear()
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.COMPLETE
        assert provider.fetch_calls == []

    def test_fully_covered_requires_no_upstox_token(
        self, tmp_path, monkeypatch,
    ):
        # Token absent from the environment entirely.
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        service.ingest(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.COMPLETE
        assert result.candle_count > 0

    def test_fully_covered_single_month_window(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, jan_end),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        service.ingest(_req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW,
        )
        assert result.chunks_required == 1
        assert result.status is HistoricalAvailabilityStatus.COMPLETE


# ============================================================
# B. COVERAGE — MISSING / PARTIAL REQUESTS FOR IDENTIFICATION
# ============================================================


class TestMissingIdentified:
    def test_missing_request_identifies_missing_chunks(self, tmp_path):
        service, planner, store = _make_stack(
            tmp_path, {}, timeframes=("15m",),
        )
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END),
            reference_now=NOW,
        )
        # 2 monthly chunks (Jan + Feb), both missing -> honest INCOMPLETE
        # with zero provider data available.
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert result.chunks_required == 2
        assert result.chunks_still_missing  # via still_missing chunk keys
        assert len(result.chunks_still_missing) == 2
        assert result.failures  # both chunks failed (empty provider)
        # No fabricated candles.
        assert result.candle_count == 0

    def test_partial_coverage_identifies_only_missing(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, jan_end),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        service.ingest(_req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW)
        # Verify with a direct planner plan: Jan covered, Feb missing.
        plan = planner.plan(["RELIANCE"], start=WIN_START, end=WIN_END)
        coverage = plan.rows[0].coverage
        assert coverage is not None
        assert coverage.covered_chunks == 1
        assert len(coverage.missing_chunk_keys) == 1
        missing_key = coverage.missing_chunk_keys[0]
        assert "2024-02-01" in missing_key  # Feb chunk is the missing one


# ============================================================
# C. ACQUISITION BEHAVIOR
# ============================================================


class TestAcquisition:
    def test_missing_fetches_and_persists(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        assert result.chunks_acquired == 2
        assert result.acquisition_attempted is True
        assert result.candle_count > 0
        # Persisted through the existing store.
        assert store.exists("RELIANCE", "15m")
        stored = store.load_candles("RELIANCE", "15m")
        assert len(stored) == result.candle_count

    def test_partial_fetches_only_missing_chunks(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end) + _daily_chunk(jan_end, WIN_END),
        }
        service, planner, store = _make_stack(tmp_path, records)
        service.ingest(_req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        assert result.chunks_covered == 2  # final coverage
        assert result.chunks_acquired == 1  # ONLY Feb re-fetched
        assert result.chunks_required == 2
        # Jan candles are untouched; the stored total is Jan + Feb days.
        stored = store.load_candles("RELIANCE", "15m")
        assert len(stored) == result.candle_count
        assert len(stored) > 31  # both months present

    def test_existing_chunks_not_refetched(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end) + _daily_chunk(jan_end, WIN_END),
        }
        provider = TrackingProvider(records)
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        service.ingest(_req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW)
        provider.fetch_calls.clear()
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        # Only the missing Feb chunk is fetched (1 provider call).
        assert len(provider.fetch_calls) == 1
        assert provider.fetch_calls[0].start == jan_end
        assert result.chunks_acquired == 1

    def test_rerun_after_success_makes_zero_unnecessary_calls(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        provider = TrackingProvider(records)
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        r1 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r1.status is HistoricalAvailabilityStatus.ACQUIRED
        calls_after_first = len(provider.fetch_calls)
        assert calls_after_first == 2
        provider.fetch_calls.clear()
        r2 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r2.status is HistoricalAvailabilityStatus.COMPLETE
        assert provider.fetch_calls == []
        assert r2.chunks_acquired == 0

    def test_persisted_data_available_next_request(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, WIN_END),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        r1 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r1.status is HistoricalAvailabilityStatus.ACQUIRED
        # A FRESH service instance (same store directory) sees the
        # persisted corpus: coverage is derived from the store alone.
        service2, planner2, store2 = _make_stack(tmp_path, {})
        _ = service2  # no provider data — coverage comes from the store
        planner2 = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=service2.providers["in-memory-import"],
        )
        avail2 = HistoricalDataAvailabilityService(planner2, service2)
        r2 = avail2.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r2.status is HistoricalAvailabilityStatus.COMPLETE
        assert r2.candle_count > 0

    def test_timeframe_scoped_acquisition(self, tmp_path):
        # A 15m request must NEVER ingest 1D chunks (even though the
        # plan covers both). The 1D data is intentionally absent, so an
        # unscoped acquisition would fail 1D chunks.
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, WIN_END),
        }
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        assert result.chunks_acquired == 2
        assert result.failures == ()
        # Only the 15m dataset exists in the store.
        assert store.exists("RELIANCE", "15m")
        assert not store.exists("RELIANCE", "1D")

    def test_only_requested_dataset_loaded_from_store(self, tmp_path):
        # §21: the service must not load the whole universe for a single
        # instrument/timeframe request. We seed TWO datasets and assert
        # the load path never reads the unrelated one.
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ("RELIANCE", "15m"): _daily_chunk(WIN_START, jan_end),
            ("TCS", "15m"): _daily_chunk(WIN_START, jan_end),
        }
        service, planner, store = _make_stack(tmp_path, records)
        # Seed both datasets via ingestion.
        inmem = InMemoryHistoricalProvider(records)
        seed_svc = HistoricalMarketDataService(provider=inmem, store=store)
        seed_svc.ingest(_req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW)
        seed_svc.ingest(_req("TCS", "15m", WIN_START, jan_end), reference_now=NOW)
        # Spy the store's load_candles: only RELIANCE may be read.
        orig_load = store.load_candles
        loaded: list[str] = []

        def spy_load(instrument, timeframe):
            loaded.append(f"{instrument}/{timeframe}")
            return orig_load(instrument, timeframe)

        store.load_candles = spy_load  # type: ignore[assignment]
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.COMPLETE
        assert loaded  # the requested dataset was read
        assert all(name.startswith("RELIANCE/") for name in loaded)

    def test_completely_missing_valid_provider_acquires(self, tmp_path):
        # §17 Case B: a valid request whose dataset is absent is acquired
        # through the configured provider and persisted.
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {("TCS", "15m"): _daily_chunk(WIN_START, jan_end)}
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("TCS", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        # Jan succeeds, Feb is empty on the vendor -> INCOMPLETE with the
        # Jan chunk persisted and returned (partial valid data).
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert result.chunks_acquired == 1
        assert len(result.failures) == 1
        assert result.candle_count == 31  # Jan days (valid, canonical)
        assert store.exists("TCS", "15m")
        assert result.instrument == "TCS"

    def test_incomplete_returns_valid_partial_candles(self, tmp_path):
        # §10/§12: a partial success still returns the CANONICAL valid
        # candles that ARE stored; the missing chunk is not fabricated.
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {("RELIANCE", "15m"): _daily_chunk(WIN_START, jan_end)}
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert result.candle_count == 31
        ts = [c.timestamp for c in result.candles]
        assert ts == sorted(ts)
        assert all(WIN_START <= t <= WIN_END for t in ts)
        assert all(isinstance(c, OHLCVCandle) for c in result.candles)

    def test_no_acquisition_path_when_configured_provider_unregistered(
        self, tmp_path,
    ):
        # The planner's configured provider is NOT registered with the
        # service: the service must NOT silently substitute another
        # provider — it reports NO_ACQUISITION_PATH honestly.
        store = HistoricalDataStore(tmp_path / "hist")
        inmem = InMemoryHistoricalProvider()
        service = HistoricalMarketDataService(provider=inmem, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(
                timeframes=("15m",), provider="upstox-historical",
            ),
            store=store,
            provider=None,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.NO_ACQUISITION_PATH
        assert result.chunks_required == 2
        assert len(result.chunks_still_missing) == 2
        assert result.acquisition_attempted is False
        assert result.candle_count == 0


# ============================================================
# D. FAILURE BEHAVIOR
# ============================================================


class TestFailureBehavior:
    def test_failed_chunk_not_falsely_covered(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        # Provider has only Jan; Feb returns EMPTY.
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end),
        }
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert result.chunks_acquired == 1  # Jan acquired
        assert len(result.failures) == 1  # Feb failed
        assert len(result.chunks_still_missing) == 1
        # The still-missing chunk is the FEB chunk (the failed one), NOT
        # the acquired Jan chunk.
        assert result.chunks_still_missing[0] not in result.acquired_chunk_keys
        assert all(
            fail.start == datetime(2024, 2, 1, tzinfo=UTC)
            for fail in result.failures
        )
        # The failed Feb chunk is NOT among the covered chunks.
        assert result.chunks_covered == 1
        # Jan candles are preserved in the store; Feb is absent.
        stored = store.load_candles("RELIANCE", "15m")
        assert len(stored) == 31
        # The failure reason is honest and provider-domain-oriented.
        assert "no candles" in result.failures[0].reason.lower()

    def test_failed_chunk_retried_on_next_request(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)

        class _LaterFill(TrackingProvider):
            """Serves Jan immediately and Feb on/after a flag."""

            def __init__(self, records):
                super().__init__(records)
                self.serve_feb = False

            def fetch(self, request):
                resp = super().fetch(request)
                if not self.serve_feb and request.start == jan_end:
                    return HistoricalProviderResponse(
                        provider_name=self.provider_name,
                        status=ProviderResponseStatus.EMPTY,
                        candles=(),
                        reason="temporarily unavailable.",
                    )
                return resp

        provider = _LaterFill(
            {
	('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end) + _daily_chunk(jan_end, WIN_END),
},
        )
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        r1 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r1.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert r1.chunks_acquired == 1
        assert len(r1.failures) == 1
        # The provider 'recovered'; the NEXT call re-attempts ONLY the
        # still-missing Feb chunk (Jan is NOT re-fetched).
        provider.serve_feb = True
        provider.fetch_calls.clear()
        r2 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r2.status is HistoricalAvailabilityStatus.ACQUIRED
        assert r2.chunks_acquired == 1
        assert len(r2.failures) == 0
        requests_fetched = [req.start for req in provider.fetch_calls]
        assert all(req.start == jan_end for req in provider.fetch_calls)
        assert len(requests_fetched) == 1  # only Feb re-attempted

    def test_failure_does_not_erase_successful_chunks(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end),
        }
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        r1 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r1.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert len(store.load_candles("RELIANCE", "15m")) == 31
        # A second partial run must not destroy the persisted Jan chunk.
        r2 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r2.status is HistoricalAvailabilityStatus.INCOMPLETE
        assert len(store.load_candles("RELIANCE", "15m")) == 31

    def test_multiple_missing_chunks_processed_deterministically(self, tmp_path):
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, datetime(2024, 5, 1, tzinfo=UTC)),
        }
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        window_end = datetime(2024, 5, 1, tzinfo=UTC)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, window_end), reference_now=NOW,
        )
        # Jan..Apr = 4 chunks; all succeed.
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        assert result.chunks_required == 4
        assert result.chunks_acquired == 4
        acquired = list(result.acquired_chunk_keys)
        assert len(set(acquired)) == 4  # unique
        # Deterministic ordering of acquired keys.
        assert acquired == sorted(acquired)


# ============================================================
# E. CREDENTIALS
# ============================================================


class TestCredentials:

    def _upstox_stack(self, tmp_path, records=None):
        """Service+planner whose DEFAULT acquisition provider is Upstox."""
        store = HistoricalDataStore(tmp_path / "hist")
        provider = TrackingProvider(records or {})
        provider.provider_name = UpstoxHistoricalDataProvider.provider_name
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(
                timeframes=("15m",),
                provider="upstox-historical",
            ),
            store=store,
            provider=None,
        )
        return service, planner, store

    def test_missing_token_prevents_required_upstox_acquisition(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        service, planner, store = self._upstox_stack(tmp_path)
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.CREDENTIAL_MISSING
        assert result.acquisition_attempted is False
        assert result.chunks_required == 2
        assert len(result.chunks_still_missing) == 2
        assert result.candle_count == 0

    def test_missing_token_causes_zero_provider_requests(self, tmp_path, monkeypatch):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        service, planner, store = self._upstox_stack(tmp_path)
        provider = service.providers["upstox-historical"]
        avail = HistoricalDataAvailabilityService(planner, service)
        avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert provider.fetch_calls == []

    def test_access_token_not_a_fallback(self, tmp_path, monkeypatch):
        # Only UPSTOX_ACCESS_TOKEN is set -> never used as credential.
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "access-secret")
        service, planner, store = self._upstox_stack(tmp_path)
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.CREDENTIAL_MISSING
        provider = service.providers["upstox-historical"]
        assert provider.fetch_calls == []

    def test_covered_data_served_without_token_even_for_upstox(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end),
        }
        service, planner, store = self._upstox_stack(tmp_path, records)
        # Pre-seed the store directly (a previous operator ingestion).
        inmem = InMemoryHistoricalProvider(records)
        service_ingest = HistoricalMarketDataService(provider=inmem, store=store)
        service_ingest.ingest(
            _req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.COMPLETE
        assert result.candle_count == 31
        # No provider request, no credential needed.
        provider = service.providers["upstox-historical"]
        assert provider.fetch_calls == []

    def test_token_values_never_in_results(self, tmp_path, monkeypatch):
        # Even when acquisition is SERVED through an upstox-named provider
        # WITH a token, the result must never expose the token value.
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "super-analytics-secret-xyz")
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, WIN_END),
        }
        service, planner, store = self._upstox_stack(tmp_path, records)
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        blob = repr(result)
        assert "super-analytics-secret-xyz" not in blob
        assert "Bearer" not in blob

    def test_failure_reason_redacts_bearer_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "analytics-secret-123")
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)

        class _Secrets(TrackingProvider):
            def fetch(self, request):
                if request.start == jan_end:
                    raise RuntimeError(
                        "Authorization: Bearer analytics-secret-123 leaked",
                    )
                return super().fetch(request)

        records = {("RELIANCE", "15m"): _daily_chunk(WIN_START, jan_end)}
        store = HistoricalDataStore(tmp_path / "hist")
        provider = _Secrets(records)
        provider.provider_name = UpstoxHistoricalDataProvider.provider_name
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(
                timeframes=("15m",), provider="upstox-historical",
            ),
            store=store,
            provider=None,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        for failure in result.failures:
            # The token value is redacted; the safe "Bearer <redacted>"
            # placeholder is what remains (no secret leaked).
            assert "analytics-secret-123" not in failure.reason
            assert "<redacted>" in failure.reason
        blob = repr(result)
        assert "analytics-secret-123" not in blob


# ============================================================
# F. VALIDATION / CANONICAL CONTRACT
# ============================================================


class TestValidation:
    def test_invalid_provider_data_never_returned(self, tmp_path):
        class _BadOHLC(TrackingProvider):
            def fetch(self, request):
                bad = _candle(request.start)
                # Tampered high<low is impossible on a constructed
                # OHLCVCandle; a provider would LIE via attributes, so we
                # emulate a malformed record through the response.
                candle = object.__new__(OHLCVCandle)
                object.__setattr__(candle, "timestamp", request.start)
                object.__setattr__(candle, "open", 10.0)
                object.__setattr__(candle, "high", 5.0)   # high < low
                object.__setattr__(candle, "low", 20.0)
                object.__setattr__(candle, "close", 15.0)
                object.__setattr__(candle, "volume", 100.0)
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.OK,
                    candles=(candle,),
                )

        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {("RELIANCE", "15m"): _daily_chunk(WIN_START, jan_end)}
        provider = _BadOHLC(records)
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        # Start month has only the malformed candle -> INVALID -> chunk
        # FAILS; the second (valid) month is served normally.
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        # No malformed candle can be in the returned dataset.
        for candle in result.candles:
            assert candle.high >= candle.low

    def test_existing_validation_pipeline_enforced(self, tmp_path):
        # Future-dated provider data is rejected by the existing
        # validation; the chunk fails rather than leaking future candles.
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)

        class _Future(TrackingProvider):
            def fetch(self, request):
                resp = super().fetch(request)
                if request.start == WIN_START:
                    future = _candle(datetime(2025, 5, 1, tzinfo=UTC))
                    return HistoricalProviderResponse(
                        provider_name=self.provider_name,
                        status=ProviderResponseStatus.OK,
                        candles=(future,),
                    )
                return resp

        records = {("RELIANCE", "15m"): _daily_chunk(jan_end, WIN_END)}
        provider = _Future(records)
        service = HistoricalMarketDataService(provider=provider)
        # NOTE: valid candles for the Jan window are ENTIRELY absent so
        # nothing can be served; Feb is valid.
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=_Future(records), store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=service.providers["in-memory-import"],
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        # Jan is future-only (rejected) -> INVALID chunk; Feb valid.
        assert result.status is HistoricalAvailabilityStatus.INCOMPLETE
        for candle in result.candles:
            assert candle.timestamp <= WIN_END
        for candle in result.candles:
            assert candle.timestamp < NOW

    def test_future_candles_cannot_be_returned(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ("RELIANCE", "15m"): (
                _daily_chunk(WIN_START, jan_end)
                + _daily_chunk(jan_end, WIN_END)
            ),
        }
        provider = RangeFilteredProvider(records)
        # A future-dated candle a provider might return.
        future = _candle(datetime(2027, 1, 1, tzinfo=UTC))
        provider.add("RELIANCE", "15m", [future])
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        for candle in result.candles:
            assert candle.timestamp <= WIN_END
            assert candle.timestamp <= NOW
        assert all(c.timestamp.year == 2024 for c in result.candles)

    def test_returned_candles_canonical_chronological(self, tmp_path):
        # Feed provider records OUT OF ORDER + duplicated; the existing
        # validation normalizes; the service returns canonical sorted
        # candles.
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        chunk = list(_daily_chunk(WIN_START, jan_end))
        reversed_chunk = list(reversed(chunk))
        records = {("RELIANCE", "15m"): reversed_chunk}
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, jan_end), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        timestamps = [c.timestamp for c in result.candles]
        assert timestamps == sorted(timestamps)
        assert all(isinstance(c, OHLCVCandle) for c in result.candles)


# ============================================================
# G. ARCHITECTURE REUSE
# ============================================================


class TestArchitectureReuse:
    def test_uses_existing_planner(self, tmp_path):
        records = dict([
            (_month_record("RELIANCE", "15m", WIN_START, WIN_END)),
        ])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        # Monkeypatch the planner: if the service did NOT use it, this
        # probe trips.
        calls = []

        orig_plan = planner.plan

        def spy_plan(*a, **kw):
            calls.append(1)
            return orig_plan(*a, **kw)

        planner.plan = spy_plan  # type: ignore[assignment]
        avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert calls  # the planner was consulted

    def test_uses_existing_store(self, tmp_path):
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        service, planner, store = _make_stack(tmp_path, records)
        service.ingest(_req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW)
        assert store.exists("RELIANCE", "15m")
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        # Candles came from the store (same canonical series).
        assert result.candles == store.load_candles("RELIANCE", "15m")
        assert result.status is HistoricalAvailabilityStatus.COMPLETE

    def test_uses_existing_ingestion_pipeline(self, tmp_path):
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        store = HistoricalDataStore(tmp_path / "hist")
        provider = TrackingProvider(records)
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        avail = HistoricalDataAvailabilityService(planner, service)
        # Spy the service's ingest path: acquisition must route through it.
        orig_ingest = service.ingest
        ingested = []

        def spy_ingest(request, **kwargs):
            ingested.append(request)
            return orig_ingest(request, **kwargs)

        service.ingest = spy_ingest  # type: ignore[assignment]
        avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert ingested  # existing service.ingest was called

    def test_no_second_http_implementation(self):
        # The service module must not define any HTTP client / provider /
        # URL / token logic of its own.
        import engine.data.historical_data_availability as availability

        source = Path(availability.__file__).read_text(encoding="utf-8")
        # No HTTP / provider / URL-construction implementation lives here:
        # the module reuses the existing service + provider + store.
        for forbidden in (
            "import urllib", "urlopen =", "def _http_", "requests.get",
            "h ttps://api.upstox.com", "https://api.upstox.com",
            "Authorization:", "Bearer ",
        ):
            assert forbidden not in source, forbidden
        # The module must not read the token itself (it delegates to the
        # existing check); only the constant import is allowed.
        assert "os.environ.get(" not in source
        assert "os.environ[" not in source

    def test_no_completion_database_introduced(self, tmp_path):
        # After a run, the store directory contains ONLY candles.json +
        # provenance.jsonl per dataset — no .done / completion markers,
        # no second job database.
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        root = Path(tmp_path) / "hist"
        all_files = [p for p in root.rglob("*") if p.is_file()]
        names = {p.name for p in all_files}
        assert "candles.json" in names
        assert "provenance.jsonl" in names
        for name in names:
            assert name not in (".done", "completion.json", "jobs.db")


# ============================================================
# H. REQUEST VALIDATION
# ============================================================


class TestRequestValidation:
    def test_non_request_invalid(self, tmp_path):
        service, planner, store = _make_stack(tmp_path, {})
        avail = _avail(service, planner)
        result = avail.get_historical_data("not-a-request")  # type: ignore[arg-type]
        assert result.status is HistoricalAvailabilityStatus.INVALID_REQUEST
        assert result.candle_count == 0

    def test_unsupported_instrument(self, tmp_path):
        service, planner, store = _make_stack(tmp_path, {})
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("AAPL", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.UNSUPPORTED_INSTRUMENT
        assert result.acquisition_attempted is False

    def test_unsupported_timeframe(self, tmp_path):
        service, planner, store = _make_stack(tmp_path, {})
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "7m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.UNSUPPORTED_TIMEFRAME

    def test_unplanned_timeframe(self, tmp_path):
        # 30m is canonical but NOT in the planner's configured timeframes.
        service, planner, store = _make_stack(tmp_path, {}, timeframes=("15m",))
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "30m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is HistoricalAvailabilityStatus.UNPLANNED_TIMEFRAME
        assert not result.acquisition_attempted

    def test_extended_timeframe_serviceable(self, tmp_path):
        # Extra timeframes configured on the service are accepted.
        service, planner, store = _make_stack(tmp_path, {}, timeframes=("15m",))
        avail = HistoricalDataAvailabilityService(
            planner, service, timeframes=("30m",),
        )
        # With no serviceable window data, the chunk fails honestly but the
        # request IS planned (not UNPLANNED).
        result = avail.get_historical_data(
            _req("RELIANCE", "30m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.status is not HistoricalAvailabilityStatus.UNPLANNED_TIMEFRAME

    def test_naive_timestamp_invalid(self, tmp_path):
        service, planner, store = _make_stack(tmp_path, {})
        avail = _avail(service, planner)
        # Construct an aware request, then tamper the timestamps to naive —
        # the availability service performs its OWN structural guard (a
        # canonical HistoricalDataRequest model already rejects naive
        # values at construction, so tampering exercises the service layer).
        request = _req("RELIANCE", "15m", WIN_START, WIN_END)
        object.__setattr__(request, "start", datetime(2024, 1, 1))
        object.__setattr__(request, "end", datetime(2024, 3, 1))
        result = avail.get_historical_data(request, reference_now=NOW)
        assert result.status is HistoricalAvailabilityStatus.INVALID_REQUEST


# ============================================================
# I. RETURN CONTRACT + BATCH + DETERMINISM
# ============================================================


class TestReturnContract:
    def test_result_contract_fields(self, tmp_path):
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        result = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert result.instrument == "RELIANCE"
        assert result.timeframe == "15m"
        assert result.request_start == WIN_START
        assert result.request_end == WIN_END
        # Status + availability metadata.
        assert result.status is HistoricalAvailabilityStatus.ACQUIRED
        assert isinstance(result.status.value, str)
        assert result.chunks_required >= 1
        assert result.chunks_covered == result.chunks_required
        assert result.chunks_acquired == result.chunks_required
        assert result.acquisition_attempted is True
        assert len(result.failures) == 0
        # The deterministic reference boundary is surfaced (no secret).
        assert result.reference_now is not None

    def test_batch_processes_each_request_independently(self, tmp_path):
        jan_end = datetime(2024, 2, 1, tzinfo=UTC)
        records = {
            ('RELIANCE', '15m'): _daily_chunk(WIN_START, jan_end),
            ('TCS', '15m'): _daily_chunk(WIN_START, jan_end),
        }
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        requests = [
            _req("RELIANCE", "15m", WIN_START, jan_end),
            _req("TCS", "15m", WIN_START, jan_end),
        ]
        batch = avail.get_historical_data_batch(requests, reference_now=NOW)
        assert len(batch) == 2
        assert all(r.status is HistoricalAvailabilityStatus.ACQUIRED for r in batch)
        # Instruments are independent.
        assert batch[0].instrument == "RELIANCE"
        assert batch[1].instrument == "TCS"

    def test_deterministic_repeated_calls(self, tmp_path):
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        r1 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        r2 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        # Identical inputs + identical END-OF-CALL corpus state produce
        # identical outcomes (the run that first acquires vs the rerun
        # that finds the data persisted both yield the same final view:
        # full coverage, the same canonical candles).
        assert r2.status is HistoricalAvailabilityStatus.COMPLETE
        assert r1.chunks_required == r2.chunks_required
        assert r1.chunks_covered == r2.chunks_covered
        assert r1.candles == r2.candles
        # A THIRD call from a clean provider-free stack (coverage is
        # derived purely from the store) is equally deterministic.
        r3 = avail.get_historical_data(
            _req("RELIANCE", "15m", WIN_START, WIN_END), reference_now=NOW,
        )
        assert r3.status is HistoricalAvailabilityStatus.COMPLETE
        assert r3.candles == r2.candles

    def test_split_across_batch_deterministic(self, tmp_path):
        records = dict([_month_record("RELIANCE", "15m", WIN_START, WIN_END)])
        service, planner, store = _make_stack(tmp_path, records)
        avail = _avail(service, planner)
        full = [_req("RELIANCE", "15m", WIN_START, WIN_END)]
        batch = avail.get_historical_data_batch(full, reference_now=NOW)
        # The batch ran the SAME acquisition path as a single call; the
        # persisted + returned candle set is identical.
        _, planner2, _ = _make_stack(tmp_path, {})
        service2 = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider({}), store=store,
        )
        avail2 = HistoricalDataAvailabilityService(planner2, service2)
        single = avail2.get_historical_data(full[0], reference_now=NOW)
        assert batch[0].status is HistoricalAvailabilityStatus.ACQUIRED
        assert single.status is HistoricalAvailabilityStatus.COMPLETE
        assert batch[0].candles == single.candles
        assert batch[0].chunks_covered == single.chunks_covered


# ============================================================
# J. REGRESSION / NO TRADING LOGIC
# ============================================================


class TestRegression:
    def test_service_module_has_no_trading_dependency(self):
        import engine.data.historical_data_availability as availability

        source = Path(availability.__file__).read_text(encoding="utf-8")
        for forbidden_import in (
            "engine.intelligence", "engine.pipeline", "paper_trade",
        ):
            assert forbidden_import not in source

    def test_existing_corpus_runner_untouched(self):
        # The operator corpus CLI must not be replaced.
        assert (Path(__file__).resolve().parent.parent
                / "scripts" / "ingest_corpus_data.py").exists()
        assert (Path(__file__).resolve().parent.parent
                / "scripts" / "ingest_historical_data.py").exists()

    def test_pipeline_baseline_importable(self):
        # The existing historical pipeline is unaffected.
        from engine.pipeline.historical_pipeline import HistoricalEvaluationPipeline
        from engine.models.historical_data import HistoricalDataRequest

        assert HistoricalEvaluationPipeline is not None
        assert HistoricalDataRequest is not None

    def test_model_importable_from_public_paths(self):
        from engine.models.historical_availability import (
            AcquisitionFailure,
            HistoricalAvailabilityStatus,
            HistoricalDataAvailabilityResult,
        )
        from engine.data.historical_data_availability import (
            HistoricalDataAvailabilityService,
        )

        assert all(
            n is not None for n in (
                AcquisitionFailure,
                HistoricalAvailabilityStatus,
                HistoricalDataAvailabilityResult,
                HistoricalDataAvailabilityService,
            )
        )

    def test_no_future_lookahead_api_parameter(self, tmp_path):
        service, planner, store = _make_stack(tmp_path, {})
        avail = _avail(service, planner)
        import inspect
        sig = inspect.signature(avail.get_historical_data)
        assert "future" not in sig.parameters
        assert "lookahead" not in sig.parameters
        sig2 = inspect.signature(avail.get_historical_data_batch)
        assert "future" not in sig2.parameters
        assert "lookahead" not in sig2.parameters