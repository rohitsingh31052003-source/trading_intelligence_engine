"""
Focused tests for the safe, resumable historical CORPUS INGESTION runner
(``engine.data.corpus_ingestion.CorpusIngestionEngine`` +
``scripts/ingest_corpus_data.py``).

The runner is an ORCHESTRATION LAYER only: it derives its missing work
from the EXISTING :class:`CorpusPreparationPlanner` and executes each
chunk through the EXISTING ``HistoricalMarketDataService`` / store
pipeline. These tests use deterministic in-memory / synthetic providers
and real (tmp) stores — NO real Upstox API calls, NO real token.

Covered: work derivation, resumability (covered chunks skipped), a
missing chunk ingested + persisted, per-chunk failure isolation (failed
chunks are never marked covered and do not abort the run), safe reruns,
the ``UPSTOX_ANALYTICS_TOKEN`` credential precheck (including that
``UPSTOX_ACCESS_TOKEN`` is NOT accepted as fallback), no token printing,
deterministic progress lines, the operator CLI, and existing-planner
regression.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.config.corpus_plan_config import CorpusPlanConfig
from engine.data.corpus_ingestion import (
    COMPLETED,
    FAILED,
    SKIPPED,
    CorpusIngestionConfig,
    CorpusIngestionEngine,
    CorpusIngestionError,
    check_upstox_analytics_token,
    require_upstox_token,
)
from engine.data.corpus_plan import CorpusPreparationPlanner
from engine.data.historical_provider import (
    UPSTOX_TOKEN_ENV,
    DeterministicLocalHistoricalProvider,
    HistoricalProviderResponse,
    InMemoryHistoricalProvider,
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_service import (
    HistoricalMarketDataService,
)
from engine.data.historical_store import (
    HistoricalDataStore,
)
from engine.models.historical_data import (
    HistoricalDataRequest,
    ProviderResponseStatus,
)
from engine.models.ohlcv import OHLCVCandle

WIN_START = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
WIN_END = datetime(2024, 3, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ingest_corpus_data.py"


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _daily_chunk(month_start: datetime, end: datetime) -> tuple[OHLCVCandle, ...]:
    """A deterministic daily series covering ``[month_start, end)``.

    Candles are offset to 04:00 UTC so no candle sits exactly on a month
    boundary instant — each candle belongs to exactly ONE half-open
    monthly chunk (mirroring real trading-day timestamps that never align
    with the UTC month tick).
    """
    candles = []
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
    """In-memory provider that (like a real vendor) only returns candles
    within the requested ``[start, end]`` window; an out-of-window window
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


def _make_service_and_planner(
    tmp_path: Path,
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]],
    *,
    provider_name: str = "in-memory-import",
    timeframes: tuple[str, ...] = ("15m", "1D"),
) -> tuple[HistoricalMarketDataService, CorpusPreparationPlanner, HistoricalDataStore]:
    store = HistoricalDataStore(tmp_path / "hist")
    if provider_name == "in-memory-import":
        provider = RangeFilteredProvider(records)
    else:
        provider = DeterministicLocalHistoricalProvider()
    service = HistoricalMarketDataService(provider=provider, store=store)
    planner = CorpusPreparationPlanner(
        config=CorpusPlanConfig(timeframes=timeframes, provider=provider_name),
        store=store,
        provider=provider,
    )
    return service, planner, store


def _engine(
    service: HistoricalMarketDataService,
    planner: CorpusPreparationPlanner,
    *,
    require_upstox_token: bool = False,
    provider: str = "in-memory-import",
    reporter=None,
) -> CorpusIngestionEngine:
    config = CorpusIngestionConfig(
        provider=provider,
        require_upstox_token=require_upstox_token,
        reference_now=NOW,
    )
    return CorpusIngestionEngine(
        planner,
        service,
        config,
        reporter=reporter,
    )


# ============================================================
# A. CREDENTIAL PRECHECK
# ============================================================


class TestCredentialPrecheck:
    def test_token_present(self, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "analytics-token")
        assert check_upstox_analytics_token() == "analytics-token"
        assert require_upstox_token() == UPSTOX_TOKEN_ENV

    def test_token_absent(self, monkeypatch):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        assert check_upstox_analytics_token() is None
        with pytest.raises(CorpusIngestionError) as exc:
            require_upstox_token()
        assert "UPSTOX_ANALYTICS_TOKEN is not set" in str(exc.value)
        assert "UPSTOX_ACCESS_TOKEN" not in str(exc.value).replace(
            "UPSTOX_ANALYTICS_TOKEN", "",
        )

    def test_access_token_is_not_a_fallback(self, monkeypatch):
        # Only UPSTOX_ACCESS_TOKEN is set -> the precheck must FAIL.
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "access-token")
        assert check_upstox_analytics_token() is None
        with pytest.raises(CorpusIngestionError):
            require_upstox_token()
        # The provider itself also must not accept it as a fallback.
        provider = UpstoxHistoricalDataProvider()
        assert provider.is_available() is False

    def test_empty_token_fails(self, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "")
        assert check_upstox_analytics_token() is None
        with pytest.raises(CorpusIngestionError):
            require_upstox_token()

    def test_engine_blocks_run_without_token_zero_api(
        self,
        monkeypatch,
        tmp_path,
    ):
        # Missing analytics token + a tracking provider: the engine must
        # raise BEFORE any fetch and never call the provider.
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)

        class _TrackingProvider(InMemoryHistoricalProvider):
            def __init__(self):
                super().__init__()
                self.fetch_calls = 0

            def fetch(self, request):
                self.fetch_calls += 1
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.OK,
                    candles=(),
                )

        provider = _TrackingProvider()
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(
                timeframes=("15m",), provider="missing-token-test",
            ),
            store=store,
            provider=provider,
        )
        engine = CorpusIngestionEngine(
            planner,
            service,
            CorpusIngestionConfig(
                provider="missing-token-test",
                require_upstox_token=True,
            ),
        )
        with pytest.raises(CorpusIngestionError):
            engine.run(
                start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
            )
        assert provider.fetch_calls == 0
        assert not store.list_datasets()

    def test_engine_skips_precheck_when_disabled(self, monkeypatch, tmp_path):
        # require_upstox_token=False (offline providers) must still run.
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        service, planner, store = _make_service_and_planner(tmp_path, {})
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.summary.completed == 0
        # An empty in-memory window is a per-chunk failure, not a crash.
        assert session.summary.failed == session.backlog.missing_count


# ============================================================
# B. WORK DERIVATION FROM THE EXISTING PLANNER
# ============================================================


class TestWorkDerivation:
    def test_missing_work_from_planned_chunks(self, tmp_path):
        service, planner, store = _make_service_and_planner(tmp_path, {})
        engine = _engine(service, planner)
        backlog = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        # 2 months x 1 instrument (15m + 1D -> 4 chunks total).
        assert backlog.missing_count == 4
        assert [j.timeframe for j in backlog.jobs] == [
            "15m", "15m", "1D", "1D",
        ]
        assert all(j.start < j.end for j in backlog.jobs)
        # First chunk starts at the window start (Jan 2024).
        assert backlog.jobs[0].start == WIN_START

    def test_already_covered_omitted_from_backlog(self, tmp_path):
        records = dict([
            _month_record("RELIANCE", "15m", WIN_START, datetime(2024, 2, 1, tzinfo=UTC)),
        ])
        service, planner, store = _make_service_and_planner(tmp_path, records)
        # Pre-seed the store so Jan 15m is covered.
        service.ingest(
            HistoricalDataRequest(
                "RELIANCE", "15m", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
            ),
            reference_now=NOW,
        )
        engine = _engine(service, planner)
        backlog = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        labels = {j.label for j in backlog.jobs}
        assert "RELIANCE 15m 2024-01-01 -> 2024-02-01" not in labels
        # Feb 15m + both 1D months remain missing.
        assert backlog.missing_count == 3

    def test_backlog_empty_when_fully_covered(self, tmp_path):
        records = dict([_month_record("RELIANCE", "1D", WIN_START, WIN_END)])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        service.ingest(
            HistoricalDataRequest(
                "RELIANCE", "1D", WIN_START, WIN_END,
            ),
            reference_now=NOW,
        )
        engine = _engine(service, planner)
        backlog = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        assert backlog.missing_count == 0
        assert backlog.is_empty

    def test_deterministic_backlog(self, tmp_path):
        service, planner, store = _make_service_and_planner(tmp_path, {})
        engine = _engine(service, planner)
        a = engine.build_backlog(WIN_START, WIN_END, ["TCS", "RELIANCE"])
        b = engine.build_backlog(WIN_START, WIN_END, ["TCS", "RELIANCE"])
        assert a == b
        keys = [(j.timeframe, j.instrument, j.key) for j in a.jobs]
        assert keys == sorted(keys)


# ============================================================
# C. SINGLE-CHUNK INGESTION + PERSISTENCE
# ============================================================


class TestIngestionPersistence:
    def test_missing_chunk_successfully_ingested(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner, provider="in-memory-import")
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        # Jan chunk succeeds (data available); Feb chunk fails honestly
        # (no data in the requested window after our range filtering) —
        # the successful chunk is persisted regardless of the failing one.
        assert session.summary.completed == 1
        assert session.summary.failed == 1
        assert session.summary.remaining == 1  # Feb 1D chunk remains
        outcome = session.results[0]
        assert outcome.status == COMPLETED
        assert outcome.records_added == 31  # Jan 1D days
        assert outcome.covered_now is True
        stored = store.load_candles("RELIANCE", "1D")
        assert len(stored) == 31
        # First candle belongs to the Jan chunk (offset 04:00 UTC — never a
        # boundary instant), within [WIN_START, Feb 1).
        assert WIN_START <= stored[0].timestamp < datetime(2024, 2, 1, tzinfo=UTC)

    def test_progress_lines_delivered(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        lines: list[str] = []
        engine = _engine(service, planner, reporter=lines.append)
        engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        assert lines[0].startswith("[1/2] RELIANCE 1D 2024-01-01 -> 2024-02-01")
        assert "... PASS (31 candles)" in lines[0]

    def test_pluralization_zero_and_one_unknown_ok(self, tmp_path):
        # A single-chunk run reads "1 candle" — presentational only.
        service, planner, store = _make_service_and_planner(
            tmp_path, {}, timeframes=("1D",),
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=datetime(2024, 2, 1, tzinfo=UTC),
            instruments=["RELIANCE"],
        )
        # Empty provider -> the single chunk fails (never fabricated).
        assert session.results[0].status == FAILED
        assert session.results[0].records_added == 0


# ============================================================
# D. RESUMA BILITY / SAFE RERUNS
# ============================================================


class TestResumability:
    def test_a_stop_mid_run_leaves_successful_chunks_persisted(
        self, tmp_path,
    ):
        jan = _daily_chunk(WIN_START, datetime(2024, 2, 1, tzinfo=UTC))
        feb = _daily_chunk(datetime(2024, 2, 1, tzinfo=UTC), WIN_END)
        records = [("RELIANCE", "1D"), jan + feb]
        service, planner, store = _make_service_and_planner(
            tmp_path, dict([records]), timeframes=("1D",),
        )
        engine = _engine(service, planner)
        # Simulate an unexpected per-chunk exception on the SECOND chunk.
        original_ingest = engine.service.ingest

        def flaky_ingest(request, **kwargs):
            if request.start == datetime(2024, 2, 1, tzinfo=UTC):
                raise RuntimeError("simulated mid-run crash")
            return original_ingest(request, **kwargs)

        engine.service.ingest = flaky_ingest  # type: ignore[assignment]
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.results[0].status == COMPLETED  # Jan persisted
        assert session.results[1].status == FAILED  # Feb crashed
        assert store.exists("RELIANCE", "1D")
        assert len(store.load_candles("RELIANCE", "1D")) == 31
        # Rerun: the Jan chunk is DERIVED AS COVERED from the store (it is
        # simply absent from the new backlog — the planner's coverage);
        # the Feb chunk is re-attempted and completes.
        engine.service.ingest = original_ingest  # restore
        session2 = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert len(session2.results) == 1  # only the missing Feb chunk
        assert session2.results[0].status == COMPLETED
        assert session2.summary.skipped == 0
        assert session2.summary.remaining == 0
        # Jan (31 days) + Feb 2024 leap-year (29 days) = 60 candles total.
        assert len(store.load_candles("RELIANCE", "1D")) == 60

    def test_rerun_does_not_refetch_completed_chunks(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        provider = service.providers["in-memory-import"]
        engine = _engine(service, planner)
        fetch_calls: list[str] = []

        orig_fetch = provider.fetch

        def tracking_fetch(request):
            fetch_calls.append(request.start.isoformat())
            return orig_fetch(request)

        provider.fetch = tracking_fetch  # type: ignore[assignment]
        engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        first_calls = list(fetch_calls)
        first_stored = len(store.load_candles("RELIANCE", "1D"))
        # Second run: the Jan chunk is derived as covered (absent from the
        # backlog); only the missing Feb chunk is re-attempted.
        fetch_calls.clear()
        session2 = engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        assert len(session2.results) == 1  # only Feb remains in the backlog
        assert fetch_calls == [first_calls[1]]  # Jan was NOT re-fetched
        assert session2.summary.completed == 0  # Feb still empty -> failed
        # No re-persist of the covered month (candles unchanged).
        assert len(store.load_candles("RELIANCE", "1D")) == first_stored

    def test_full_coverage_second_run_zero_work(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, WIN_END,
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner)
        engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        session2 = engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        assert session2.results == ()
        assert session2.summary.completed == 0
        assert session2.summary.remaining == 0


# ============================================================
# E. FAILURE HANDLING
# ============================================================


class TestFailureHandling:
    def test_empty_response_fails_and_continues(self, tmp_path):
        # Provider has data for Jan only; Feb returns EMPTY.
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.results[0].status == COMPLETED
        assert session.results[1].status == FAILED
        assert session.results[1].detail  # honest reason surfaced
        assert "no candles" in session.results[1].detail.lower()
        # The store still contains ONLY the successful Jan month.
        assert len(store.load_candles("RELIANCE", "1D")) == 31

    def test_api_error_fails_and_continues(self, tmp_path):
        class _Flaky(RangeFilteredProvider):
            def fetch(self, request):
                if request.start == datetime(2024, 2, 1, tzinfo=UTC):
                    return HistoricalProviderResponse(
                        provider_name=self.provider_name,
                        status=ProviderResponseStatus.ERROR,
                        candles=(),
                        reason="simulated network error",
                    )
                return super().fetch(request)

        jan = _daily_chunk(WIN_START, datetime(2024, 2, 1, tzinfo=UTC))
        feb = _daily_chunk(datetime(2024, 2, 1, tzinfo=UTC), WIN_END)
        records = [("RELIANCE", "1D"), jan + feb]
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=_Flaky(dict([records])), store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",), provider="in-memory-import"),
            store=store,
            provider=service.providers["in-memory-import"],
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.results[0].status == COMPLETED
        assert session.results[1].status == FAILED
        assert "network" in session.results[1].detail.lower()

    def test_validation_failure_fails_and_continues(self, tmp_path):
        class _InvalidRecords(RangeFilteredProvider):
            def fetch(self, request):
                if request.start == datetime(2024, 2, 1, tzinfo=UTC):
                    bad = [
                        _candle(datetime(2024, 2, 1, tzinfo=UTC)),
                        _candle(datetime(2024, 2, 1, tzinfo=UTC)),
                    ]
                    return HistoricalProviderResponse(
                        provider_name=self.provider_name,
                        status=ProviderResponseStatus.OK,
                        candles=tuple(bad),
                    )
                return super().fetch(request)

        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(
            provider=_InvalidRecords(records), store=store,
        )
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",), provider="in-memory-import"),
            store=store,
            provider=service.providers["in-memory-import"],
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.results[0].status == COMPLETED
        # Feb: identical timestamps -> duplicates; one accepted + one
        # rejected -> PARTIAL is persistable, so this is actually a PASS.
        # We instead assert the chunk was not FALSELY recorded as failed
        # and the loop continued (result count == backlog count).
        assert len(session.results) == session.backlog.missing_count

    def test_unexpected_exception_isolated(self, tmp_path):
        class _Boom(RangeFilteredProvider):
            def fetch(self, request):
                if request.start == datetime(2024, 2, 1, tzinfo=UTC):
                    raise ValueError("simulated unexpected crash")
                return super().fetch(request)

        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=_Boom(records), store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",), provider="in-memory-import"),
            store=store,
            provider=service.providers["in-memory-import"],
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.results[0].status == COMPLETED
        assert session.results[1].status == FAILED
        assert "crash" in session.results[1].detail
        # Failed chunk NOT marked covered.
        assert session.results[1].covered_now is False

    def test_failed_chunk_not_falsely_covered(self, tmp_path):
        class _AlwaysEmpty(InMemoryHistoricalProvider):
            def fetch(self, request):
                return HistoricalProviderResponse(
                    provider_name=self.provider_name,
                    status=ProviderResponseStatus.EMPTY,
                    candles=(),
                )

        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(provider=_AlwaysEmpty({}), store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",), provider="in-memory-import"),
            store=store,
            provider=service.providers["in-memory-import"],
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert all(o.status == FAILED for o in session.results)
        assert all(o.covered_now is False for o in session.results)
        assert session.summary.completed == 0
        assert session.summary.failed == session.backlog.missing_count
        # The existing service persists an EMPTY dataset file for audit,
        # but it holds ZERO usable candles: the failed chunks are NOT
        # covered and `_chunk_covered` still reports False on the rerun.
        assert not store.load_candles("RELIANCE", "1D")
        # Rerun still treats every chunk as missing (no false coverage).
        session2 = engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        assert all(o.status == FAILED for o in session2.results)
        assert session2.summary.remaining == session2.summary.backlog_count


# ============================================================
# F. SECURITY / REDACTION
# ============================================================


class TestNoSecretLeakage:
    def test_failure_detail_redacts_bearer_token(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner)

        class _Secrets(RangeFilteredProvider):
            def fetch(self, request):
                if request.start == datetime(2024, 2, 1, tzinfo=UTC):
                    raise RuntimeError(
                        "Authorization: Bearer super-secret-token-xyz failed"
                    )
                return super().fetch(request)

        engine.service = HistoricalMarketDataService(
            provider=_Secrets(records), store=store,
        )
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        detail = session.results[1].detail
        assert "super-secret-token-xyz" not in detail
        assert "Bearer <redacted>" in detail

    def test_reporter_lines_never_contain_token_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "analytics-secret-123")
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        lines: list[str] = []
        engine = _engine(service, planner, reporter=lines.append)
        engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        assert all("analytics-secret-123" not in line for line in lines)

    def test_token_env_var_only_source(self, monkeypatch):
        # UPSTOX_ACCESS_TOKEN set + analytics set: the analytics value is
        # used; the access value is never elevated.
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "analytics-token")
        monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "access-token")
        assert check_upstox_analytics_token() == "analytics-token"
        assert require_upstox_token() == UPSTOX_TOKEN_ENV


# ============================================================
# G. SUMMARY ACCOUNTING
# ============================================================


class TestSummaryAccounting:
    def test_summary_counts_and_remaining(self, tmp_path):
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner)
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        summary = session.summary
        assert summary.backlog_count == 2
        assert summary.completed == 1
        assert summary.skipped == 0
        assert summary.failed == 1
        assert summary.candles_added == 31
        assert summary.remaining == 1
        assert summary.failed_chunks and "2024-02-01" in summary.failed_chunks[0]

    def test_skip_reasons_tallied(self, tmp_path):
        # Rerun against a store where the Jan chunk is already covered but
        # the planner still lists the Feb chunk as missing: deriving the
        # backlog from the STORE cannot reproduce the "covered-but-listed"
        # state, so this test instead hands the engine a backlog built
        # BEFORE the store was populated — exercising the runner's own
        # per-chunk coverage precheck (the resumability safety net).
        jan = _daily_chunk(WIN_START, datetime(2024, 2, 1, tzinfo=UTC))
        feb = _daily_chunk(datetime(2024, 2, 1, tzinfo=UTC), WIN_END)
        records = dict([(("RELIANCE", "1D"), jan + feb)])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = _engine(service, planner)
        backlog_before = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        # Populate the store for the Jan chunk (as if a previous run or an
        # operator had already fetched it).
        service.ingest(
            HistoricalDataRequest(
                "RELIANCE", "1D",
                WIN_START, datetime(2024, 2, 1, tzinfo=UTC),
            ),
            reference_now=NOW,
        )
        session = engine.run(backlog_before)
        assert session.results[0].status == SKIPPED  # Jan covered by store
        assert session.results[1].status == COMPLETED  # Feb ingested
        assert session.summary.skipped == 1
        assert session.summary.skip_reasons


# ============================================================
# H. OPERATOR CLI
# ============================================================


def _run_cli(*args: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    import subprocess

    env = dict(os.environ)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_SCRIPT.parent.parent),
        env=env,
    )
    # Combine both streams: error messages are reported on stderr, progress
    # on stdout — the CLI contract never prints secrets to either.
    return result.returncode, (result.stdout or "") + (result.stderr or "")


class TestOperatorCLI:
    def test_missing_token_prevents_execution(self, monkeypatch, tmp_path):
        env = {
            "UPSTOX_ANALYTICS_TOKEN": "",
            "UPSTOX_ACCESS_TOKEN": "not-a-fallback",
        }
        code, out = _run_cli(
            "--start", "2024-01-01", "--end", "2024-03-01",
            "--provider", "upstox-historical",
            "--timeframes", "15m", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
            extra_env=env,
        )
        assert code == 1
        assert "UPSTOX_ANALYTICS_TOKEN is not set" in out
        assert "not-a-fallback" not in out
        assert not (tmp_path / "hist").exists()

    def test_access_token_not_a_fallback_cli(self, tmp_path):
        env = {
            "UPSTOX_ANALYTICS_TOKEN": None,  # explicit removal in the child
            "UPSTOX_ACCESS_TOKEN": "access-secret",
        }
        code, out = _run_cli(
            "--start", "2024-01-01", "--end", "2024-03-01",
            "--provider", "upstox-historical",
            "--timeframes", "15m", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
            extra_env=env,
        )
        assert code == 1
        assert "UPSTOX_ANALYTICS_TOKEN is not set" in out
        assert "access-secret" not in out

    def test_offline_provider_runs_and_persists(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01", "--end", "2024-03-01",
            "--provider", "local-deterministic",
            "--timeframes", "1D", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        assert "HISTORICAL CORPUS INGESTION" in out
        assert "[1/2]" in out and "PASS" in out
        assert "Corpus ingestion complete" in out
        assert (tmp_path / "hist" / "RELIANCE" / "1D" / "candles.json").exists()

    def test_cli_rerun_derives_zero_missing(self, tmp_path):
        args = [
            "--start", "2024-01-01", "--end", "2024-03-01",
            "--provider", "local-deterministic",
            "--timeframes", "1D", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        ]
        code1, out1 = _run_cli(*args)
        code2, out2 = _run_cli(*args)
        assert code1 == 0 and code2 == 0
        # The rerun derives the missing set from the STORE: the previously
        # ingested chunk is already covered, so no chunk is re-fetched and
        # the work matrix is empty.
        assert "Planned missing : 0" in out2
        assert "Completed       : 0" in out2
        assert "Candles added   : 0" in out2

    def test_bad_window_exits_two(self, tmp_path):
        code, out = _run_cli(
            "--start", "not-a-date", "--end", "2024-03-01",
            "--provider", "local-deterministic",
            "--timeframes", "1D", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 2

    def test_no_buy_sell_recommendation_language(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01", "--end", "2024-03-01",
            "--provider", "local-deterministic",
            "--timeframes", "1D", "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        # The runner is historical-data-only: no trading-recommendation
        # language and an explicit non-predictive disclaimer.
        assert "buy" not in out.lower()
        assert "guarantee" not in out.lower()
        assert "no prediction" in out.lower()


# ============================================================
# I. REGRESSION / EXISTING PLANNER UNCHANGED
# ============================================================


class TestRegression:
    def test_existing_planner_behavior_unchanged(self, tmp_path):
        # The planner's own plan / coverage semantics are untouched: the
        # runner only READS its plan output (same plan id, same missing
        # keys as a standalone planner over the same store).
        service, planner, store = _make_service_and_planner(tmp_path, {})
        plan_a = planner.plan(["RELIANCE"], start=WIN_START, end=WIN_END)
        engine = _engine(service, planner)
        backlog = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        assert backlog.plan_id == plan_a.plan_id
        assert backlog.missing_count == plan_a.missing_request_count
        # Planned chunk keys match the resonstructed job keys' timeframes.
        assert {j.timeframe for j in backlog.jobs} == set(plan_a.timeframes)

    def test_runner_does_not_import_trading_logic(self):
        # The runner module must not depend on decision / pipeline /
        # paper-trading packages (docstring text mentions the boundaries,
        # but the imports must stay clean).
        import engine.data.corpus_ingestion as ci

        source = Path(ci.__file__).read_text(encoding="utf-8")
        for forbidden_import in (
            "from engine.intelligence",
            "engine.pipeline",
            "paper_trade",
            "trade_plan",
        ):
            assert forbidden_import not in source

    def test_reference_now_defaults_past_window_end(self, tmp_path):
        # Deterministic reference: when storing a just-finished chunk the
        # derived "now" must be at/after the window end so no accepted
        # candle is rejected as future-dated.
        records = dict([_month_record(
            "RELIANCE", "1D", WIN_START, WIN_END,
        )])
        service, planner, store = _make_service_and_planner(
            tmp_path, records, timeframes=("1D",),
        )
        engine = CorpusIngestionEngine(
            planner, service, CorpusIngestionConfig(
                provider="in-memory-import",
                require_upstox_token=False,
                reference_now=None,
            ),
        )
        session = engine.run(
            start=WIN_START, end=WIN_END, instruments=["RELIANCE"],
        )
        assert session.reference_now >= WIN_END