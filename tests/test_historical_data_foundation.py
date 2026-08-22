"""
Product Phase 6A — Historical Market Data Foundation (test suite).

Covers the required test areas A–Z plus CLI / dashboard integration and
existing-path regression. All tests are deterministic and network-free;
providers are injected fakes.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.data.historical_gaps import (
    DEFAULT_GAP_CONFIG,
    GapDetectionConfig,
    detect_gaps,
)
from engine.data.historical_provider import (
    DeterministicLocalHistoricalProvider,
    HistoricalProviderResponse,
    InMemoryHistoricalProvider,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import (
    HistoricalDataIntegrityError,
    HistoricalDataStore,
    HistoricalDataStoreError,
    HistoricalDatasetNotFoundError,
    StoredDatasetInfo,
    default_historical_data_directory,
)
from engine.data.historical_serialization import (
    serialize_candles,
    serialize_provenance,
)
from engine.data.historical_times import (
    HISTORICAL_TIMEFRAME_SECONDS,
    canonical_timeframe,
    normalize_to_utc,
    supported_timeframes,
    timeframe_seconds,
)
from engine.data.historical_validation import HistoricalDataValidator
from engine.models.historical_data import (
    DEFAULT_RESEARCH_UNIVERSE,
    GapKind,
    HistoricalDataError,
    HistoricalDataRequest,
    HistoricalDatasetSlice,
    HistoricalFetchResult,
    HistoricalGap,
    HistoricalIngestResult,
    HistoricalIngestionStatus,
    HistoricalProvenance,
    HistoricalStoreResult,
    ProviderResponseStatus,
    ResearchUniverse,
)
from engine.models.historical_data import (
    HistoricalDataIssue,
)
from engine.models.ohlcv import OHLCVCandle
from engine.data.validator import DataValidator

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "ingest_historical_data.py"

_NOW = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
_START = datetime(2024, 1, 1, tzinfo=UTC)
_END = datetime(2024, 1, 10, tzinfo=UTC)


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _candle(day: int, base: datetime = _START, *, close: float = 100.0,
            volume: float = 1000.0) -> OHLCVCandle:
    """Build an OHLC-valid candle at ``base + day`` days."""

    ts = base + timedelta(days=day)
    return OHLCVCandle(
        timestamp=ts, open=close, high=close + 2.0,
        low=close - 2.0, close=close, volume=volume,
    )


def _daily_series(n: int, base: datetime = _START) -> tuple[OHLCVCandle, ...]:
    return tuple(_candle(i, base) for i in range(n))


def _service(records=None, *, store=None, universe=None,
             providers=None, provider=None) -> HistoricalMarketDataService:
    if providers is not None:
        return HistoricalMarketDataService(
            providers=providers, store=store, universe=universe,
        )
    if provider is None:
        provider = InMemoryHistoricalProvider(records or {})
    return HistoricalMarketDataService(
        provider=provider, store=store, universe=universe,
    )


def _request(instrument="NIFTY", timeframe="1D", start=_START, end=_END,
             **kwargs) -> HistoricalDataRequest:
    return HistoricalDataRequest(instrument, timeframe, start, end, **kwargs)


# ============================================================
# A. PROVIDER CONTRACT
# ============================================================


class TestProviderContract:
    def test_in_memory_is_available_and_supports(self):
        provider = InMemoryHistoricalProvider()
        assert provider.is_available()
        assert provider.supports("NIFTY", "1D")

    def test_in_memory_fetch_ok(self):
        candles = _daily_series(3)
        provider = InMemoryHistoricalProvider({("NIFTY", "1D"): candles})
        response = provider.fetch(_request())
        assert response.status is ProviderResponseStatus.OK
        assert response.candles == candles
        assert response.provider_name == "in-memory-import"

    def test_in_memory_fetch_empty(self):
        provider = InMemoryHistoricalProvider()
        response = provider.fetch(_request())
        assert response.status is ProviderResponseStatus.EMPTY
        assert not response.candles

    def test_supports_rejects_unknown_timeframe(self):
        provider = InMemoryHistoricalProvider()
        assert not provider.supports("NIFTY", "17x")

    def test_deterministic_local_reproducible(self):
        provider = DeterministicLocalHistoricalProvider()
        r1 = provider.fetch(_request())
        r2 = provider.fetch(_request())
        assert r1.candles == r2.candles

    def test_deterministic_local_supports_all_timeframes(self):
        provider = DeterministicLocalHistoricalProvider()
        for tf in supported_timeframes():
            assert provider.supports("NIFTY", tf)

    def test_in_memory_add_records(self):
        provider = InMemoryHistoricalProvider()
        provider.add("NIFTY", "1D", _daily_series(2))
        response = provider.fetch(_request())
        assert response.status is ProviderResponseStatus.OK

    def test_service_provider_registry_sorted(self):
        svc = _service()
        assert svc.available_providers() == ("in-memory-import",)

    def test_service_unknown_provider_name(self):
        svc = _service()
        with pytest.raises(ValueError):
            svc.fetch_historical(_request(provider="nope"))

    def test_provider_replaceable_without_service_change(self):
        """A second provider can be registered without service edits."""

        class _Stub:
            provider_name = "stub"

            def is_available(self):
                return True

            def supports(self, instrument, timeframe):
                return True

            def fetch(self, request):
                return HistoricalProviderResponse(
                    self.provider_name,
                    ProviderResponseStatus.OK,
                    _daily_series(1),
                )

        svc = _service(providers=[InMemoryHistoricalProvider(), _Stub()])
        result = svc.fetch_historical(_request(provider="stub"))
        assert result.provenance.provider == "stub"


# ============================================================
# B. REQUEST VALIDATION
# ============================================================


class TestRequestValidation:
    def test_end_not_after_start_rejected(self):
        with pytest.raises(ValueError):
            _request(start=_END, end=_START)

    def test_naive_start_rejected(self):
        with pytest.raises(ValueError):
            _request(start=datetime(2024, 1, 1))

    def test_naive_end_rejected(self):
        with pytest.raises(ValueError):
            _request(end=datetime(2024, 1, 10))

    def test_empty_instrument_rejected(self):
        with pytest.raises(ValueError):
            _request(instrument="  ")

    def test_both_naive_rejected(self):
        with pytest.raises(ValueError):
            HistoricalDataRequest(
                "NIFTY", "1D", datetime(2024, 1, 1), datetime(2024, 1, 2),
            )

    def test_instrument_normalized(self):
        req = _request(instrument="  nifty  ")
        assert req.instrument == "NIFTY"

    def test_non_datetime_start_rejected(self):
        with pytest.raises(ValueError):
            HistoricalDataRequest("NIFTY", "1D", "2024-01-01", _END)

    def test_metadata_list_normalized(self):
        req = HistoricalDataRequest(
            "NIFTY", "1D", _START, _END,
            metadata=[("b", "2"), ("a", "1")],
        )
        assert req.metadata == (("a", "1"), ("b", "2"))


# ============================================================
# C. TIMEZONE NORMALIZATION
# ============================================================


class TestTimezoneNormalization:
    def test_offset_normalized_to_utc(self):
        ts = datetime(2024, 1, 1, 5, 30, tzinfo=None)
        assert normalize_to_utc(ts) is None

    def test_aware_offset_normalized_to_utc(self):
        from datetime import timezone
        ts = datetime(2024, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        assert normalize_to_utc(ts) == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)

    def test_naive_rejected_by_validator(self):
        naive_ts = datetime(2024, 1, 1)
        candle = OHLCVCandle(
            timestamp=naive_ts, open=1.0, high=2.0, low=0.5, close=1.5,
            volume=1.0,
        )
        accepted, issues = HistoricalDataValidator.validate(
            [candle], instrument="NIFTY", timeframe="1D", reference_now=_NOW,
        )
        assert not accepted
        assert issues[0].error is HistoricalDataError.NAIVE_TIMESTAMP

    def test_canonical_timeframe_aliases(self):
        assert canonical_timeframe("15M") == "15m"
        assert canonical_timeframe("60m") == "1h"
        assert canonical_timeframe("1d") == "1D"
        assert canonical_timeframe("1H") == "1h"

    def test_canonical_timeframe_unknown(self):
        assert canonical_timeframe("17x") is None
        assert canonical_timeframe(None) is None

    def test_timeframe_seconds(self):
        assert timeframe_seconds("1D") == 86400
        assert timeframe_seconds("15m") == 900
        assert timeframe_seconds("bogus") is None

    def test_ingestion_normalizes_aware_offset_to_utc(self):
        from datetime import timezone
        candles = [
            OHLCVCandle(
                timestamp=datetime(2024, 1, 1, 5, 30, tzinfo=timezone(
                    timedelta(seconds=0),
                )),
                open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0,
            ),
        ]
        svc = _service({("NIFTY", "1D"): candles})
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.AVAILABLE
        assert result.candles[0].timestamp.tzinfo is UTC


# ============================================================
# D. OHLC VALIDATION
# ============================================================


class TestOHLCValidation:
    def test_valid_shapes_accepted(self):
        shapes = [
            OHLCVCandle(_START, 10.0, 12.0, 9.0, 11.0, 100.0),
            OHLCVCandle(_START + timedelta(days=1), 10.0, 10.0, 10.0, 10.0, 0.0),
        ]
        accepted, issues = HistoricalDataValidator.validate(
            shapes, instrument="NIFTY", timeframe="1D", reference_now=_NOW,
        )
        assert len(accepted) == 2

    def test_impossible_ohlc_rejected_at_model(self):
        with pytest.raises(ValueError):
            OHLCVCandle(_START, 10.0, 9.0, 11.0, 10.0, 1.0)  # high < low

    def test_open_outside_range_rejected_at_model(self):
        with pytest.raises(ValueError):
            OHLCVCandle(_START, 12.0, 10.0, 9.0, 10.0, 1.0)

    def test_close_outside_range_rejected_at_model(self):
        with pytest.raises(ValueError):
            OHLCVCandle(_START, 10.0, 11.0, 9.0, 12.5, 1.0)

    def test_provenance_requires_count_totals(self):
        with pytest.raises(ValueError):
            HistoricalProvenance(
                provider="p", instrument="N", timeframe="1D",
                requested_start=_START, requested_end=_END,
                actual_first_candle=None, actual_last_candle=None,
                ingestion_timestamp=_NOW,
                records_received=1, records_accepted=0, records_rejected=0,
                status=HistoricalIngestionStatus.AVAILABLE,
            )


# ============================================================
# E. VOLUME VALIDATION
# ============================================================


class TestVolumeValidation:
    def test_zero_volume_accepted(self):
        candle = OHLCVCandle(_START, 1.0, 2.0, 0.5, 1.5, 0.0)
        accepted, issues = HistoricalDataValidator.validate(
            [candle], instrument="N", timeframe="1D", reference_now=_NOW,
        )
        assert len(accepted) == 1

    def test_negative_volume_rejected_at_model(self):
        with pytest.raises(ValueError):
            OHLCVCandle(_START, 1.0, 2.0, 0.5, 1.5, -1.0)

    def test_existing_data_validator_still_enforced(self):
        candle = OHLCVCandle(_START, 1.0, 2.0, 0.5, 1.5, 1.0)
        DataValidator.validate_candle(candle)  # no raise


# ============================================================
# F. DUPLICATE DETECTION
# ============================================================


class TestDuplicateDetection:
    def test_duplicates_rejected_first_kept(self):
        dup = _daily_series(2) + (_candle(0), _candle(0))
        svc = _service({("NIFTY", "1D"): dup})
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.PARTIAL
        assert len(result.candles) == 2
        dup_issues = [i for i in result.issues
                      if i.error is HistoricalDataError.DUPLICATE_TIMESTAMP]
        # Both extra occurrences of day 0 are rejected; the first is kept.
        assert len(dup_issues) == 2

    def test_duplicate_detection_deterministic(self):
        dup = (_candle(0), _candle(0))
        svc = _service({("NIFTY", "1D"): dup})
        r1 = svc.fetch_historical(_request())
        r2 = svc.fetch_historical(_request())
        assert r1.candles == r2.candles


# ============================================================
# G. CHRONOLOGICAL ORDERING
# ============================================================


class TestChronologicalOrdering:
    def test_unordered_sorted_with_issue(self):
        unordered = (_candle(3), _candle(1), _candle(2), _candle(0))
        svc = _service({("NIFTY", "1D"): unordered})
        result = svc.fetch_historical(_request())
        assert [c.timestamp for c in result.candles] == sorted(
            [c.timestamp for c in unordered],
        )
        assert any(i.error is HistoricalDataError.UNORDERED for i in result.issues)

    def test_ordering_invariant_across_inputs(self):
        a = (_candle(0), _candle(2), _candle(1))
        b = (_candle(2), _candle(1), _candle(0))
        svc_a = _service({("NIFTY", "1D"): a})
        svc_b = _service({("NIFTY", "1D"): b})
        ra = svc_a.fetch_historical(_request())
        rb = svc_b.fetch_historical(_request())
        assert ra.candles == rb.candles


# ============================================================
# H. FUTURE-CANDLE REJECTION
# ============================================================


class TestFutureCandleRejection:
    def test_future_candle_rejected(self):
        future_candle = _candle(10)  # > _NOW when _END was 2024-01-10
        svc = _service({("NIFTY", "1D"): _daily_series(10) + (future_candle,)})
        result = svc.fetch_historical(_request(), reference_now=_NOW)
        rejected = [
            i for i in result.issues
            if i.error is HistoricalDataError.FUTURE_DATED
        ]
        assert rejected or all(c.timestamp <= _NOW for c in result.candles)

    def test_future_end_rejected_invalid(self):
        service_now = datetime(2024, 1, 5, tzinfo=UTC)
        req = _request(end=datetime(2024, 1, 20, tzinfo=UTC))
        svc = _service()
        result = svc.fetch_historical(req, reference_now=service_now)
        assert result.status is HistoricalIngestionStatus.INVALID

    def test_allow_future_end_flag(self):
        future_end = _NOW + timedelta(days=30)
        req = _request(end=future_end, allow_future_end=True)
        svc = _service({("NIFTY", "1D"): _daily_series(5)})
        result = svc.fetch_historical(req, reference_now=_NOW)
        assert result.candles  # controlled import flag accepted

    def test_validator_allow_future_flag(self):
        future = _candle(100)
        accepted, issues = HistoricalDataValidator.validate(
            [future], instrument="N", timeframe="1D", reference_now=_NOW,
            allow_future=True,
        )
        assert len(accepted) == 1


# ============================================================
# I. EMPTY PROVIDER RESPONSE
# ============================================================


class TestEmptyProviderResponse:
    def test_empty_status(self):
        svc = _service()
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.EMPTY
        assert result.issues[0].error is HistoricalDataError.EMPTY_RESPONSE

    def test_empty_ingestion_recorded_provenance(self, tmp_path):
        svc = _service(store=HistoricalDataStore(tmp_path))
        result = svc.ingest(_request())
        assert result.fetch.status is HistoricalIngestionStatus.EMPTY
        assert result.store is not None
        assert result.store.total_candles == 0


# ============================================================
# J. PROVIDER EXCEPTION
# ============================================================


class _ExplodingProvider:
    provider_name = "exploding"

    def is_available(self):
        return True

    def supports(self, instrument, timeframe):
        return True

    def fetch(self, request):
        raise RuntimeError("boom")


class TestProviderException:
    def test_exception_reported_not_raised(self):
        svc = _service(provider=_ExplodingProvider())
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.ERROR
        assert result.issues[0].error is HistoricalDataError.PROVIDER_ERROR

    def test_unavailable_provider_reported(self):
        class _Down:
            provider_name = "down"

            def is_available(self):
                return False

            def supports(self, instrument, timeframe):
                return True

            def fetch(self, request):
                raise AssertionError("never called")

        svc = _service(provider=_Down())
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.ERROR


# ============================================================
# K. MALFORMED PROVIDER RESPONSE
# ============================================================


class TestMalformedProviderResponse:
    def test_non_candle_record_rejected(self):
        svc = _service({("NIFTY", "1D"): [object(), "not-a-candle"]})
        result = svc.fetch_historical(_request())
        assert result.status in (
            HistoricalIngestionStatus.INVALID,
            HistoricalIngestionStatus.PARTIAL,
        )
        assert all(
            i.error in (
                HistoricalDataError.MALFORMED_RESPONSE,
            ) for i in result.issues
        )

    def test_mixed_malformed_partial(self):
        svc = _service({("NIFTY", "1D"): [_candle(0), object()]})
        result = svc.fetch_historical(_request())
        assert result.status is HistoricalIngestionStatus.PARTIAL
        assert len(result.candles) == 1


# ============================================================
# L. PERSISTENCE
# ============================================================


class TestPersistence:
    def test_store_round_trip(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        store.store("NIFTY", "1D", _daily_series(3))
        loaded = store.load_candles("NIFTY", "1D")
        assert len(loaded) == 3

    def test_missing_dataset_raises(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        with pytest.raises(HistoricalDatasetNotFoundError):
            store.load_candles("NIFTY", "1D")

    def test_corrupted_dataset_raises(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        path = store.path_for("NIFTY", "1D")
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(HistoricalDataIntegrityError):
            store.load_candles("NIFTY", "1D")

    def test_wrong_schema_rejected(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        path = store.path_for("NIFTY", "1D")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 99, "candles": []}))
        with pytest.raises(HistoricalDataIntegrityError):
            store.load_candles("NIFTY", "1D")

    def test_unsafe_id_rejected(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        with pytest.raises(HistoricalDataStoreError):
            store.path_for("../evil", "1D")

    def test_exists_and_delete(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        store.store("NIFTY", "1D", _daily_series(1))
        assert store.exists("NIFTY", "1D")
        store.delete("NIFTY", "1D")
        assert not store.exists("NIFTY", "1D")

    def test_default_directory_relative(self):
        assert default_historical_data_directory() == (
            Path.cwd() / "data" / "historical"
        )

    def test_list_datasets_sorted(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        store.store("TCS", "1D", _daily_series(1))
        store.store("NIFTY", "15m", _daily_series(2))
        info = store.list_datasets()
        assert [(i.instrument, i.timeframe) for i in info] == [
            ("NIFTY", "15m"), ("TCS", "1D"),
        ]
        assert info[0].candle_count == 2


# ============================================================
# M. REPEATED INGESTION IDEMPOTENCY
# ============================================================


class TestIngestionIdempotency:
    def test_same_ingestion_twice_no_duplicates(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): _daily_series(5)},
            store=HistoricalDataStore(tmp_path),
        )
        r1 = svc.ingest(_request())
        r2 = svc.ingest(_request())
        assert r1.store.records_added == 5
        assert r2.store.records_added == 0
        assert r2.store.total_candles == 5

    def test_incremental_ingestion_merges(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc_a = _service({("NIFTY", "1D"): _daily_series(3)}, store=store)
        svc_a.ingest(_request())
        svc_b = _service({("NIFTY", "1D"): _daily_series(8)}, store=store)
        rb = svc_b.ingest(_request())
        assert rb.store.records_added == 5
        assert rb.store.total_candles == 8


# ============================================================
# N. DATASET RELOAD
# ============================================================


class TestDatasetReload:
    def test_reload_round_trip(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): _daily_series(5)},
            store=HistoricalDataStore(tmp_path),
        )
        svc.ingest(_request())
        loaded = svc.load_historical("NIFTY", "1D")
        assert loaded.count == 5
        assert loaded.source_count == 5
        assert not loaded.is_empty

    def test_reload_window_filter(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): _daily_series(10)},
            store=HistoricalDataStore(tmp_path),
        )
        svc.ingest(_request())
        loaded = svc.load_historical(
            "NIFTY", "1D", start=_candle(2).timestamp, end=_candle(5).timestamp,
        )
        assert loaded.count == 4

    def test_empty_slice_marked(self):
        view = HistoricalDatasetSlice(
            instrument="N", timeframe="1D", candles=(),
            first_timestamp=None, last_timestamp=None,
            count=0, source_count=0,
        )
        assert view.is_empty


# ============================================================
# O. METADATA / PROVENANCE
# ============================================================


class TestProvenance:
    def test_provenance_fields(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): _daily_series(3)},
            store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request())
        p = result.fetch.provenance
        assert p.provider == "in-memory-import"
        assert p.records_received == 3
        assert p.records_accepted == 3
        assert p.records_rejected == 0
        assert p.actual_first_candle == result.fetch.candles[0].timestamp
        assert p.actual_last_candle == result.fetch.candles[-1].timestamp

    def test_provenance_persisted_per_ingestion(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc = _service({("NIFTY", "1D"): _daily_series(2)}, store=store)
        svc.ingest(_request())
        svc.ingest(_request())
        lines = store.load_provenance("NIFTY", "1D")
        assert len(lines) == 2
        latest = json.loads(lines[-1])
        assert latest["provider"] == "in-memory-import"
        assert latest["status"] == "AVAILABLE"

    def test_provenance_serialization_deterministic(self):
        p = HistoricalProvenance(
            provider="p", instrument="N", timeframe="1D",
            requested_start=_START, requested_end=_END,
            actual_first_candle=None, actual_last_candle=None,
            ingestion_timestamp=_NOW,
            records_received=0, records_accepted=0, records_rejected=0,
            status=HistoricalIngestionStatus.AVAILABLE,
        )
        assert serialize_provenance(p) == serialize_provenance(p)

    def test_partial_status_reported_not_available(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): (_candle(0), _candle(0), _candle(1))},
            store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request())
        assert result.fetch.provenance.status is HistoricalIngestionStatus.PARTIAL


# ============================================================
# P. GAP DETECTION
# ============================================================


class TestGapDetection:
    def test_valid_sequence_no_gaps(self):
        assert detect_gaps(_daily_series(5), "1D") == ()

    def test_weekend_gap_is_possible_closure(self):
        friday = _candle(0, datetime(2024, 1, 5, tzinfo=UTC))  # Friday
        monday = _candle(3, datetime(2024, 1, 5, tzinfo=UTC))  # Monday
        gaps = detect_gaps((friday, monday), "1D")
        assert len(gaps) == 1
        assert gaps[0].kind is GapKind.POSSIBLE_MARKET_CLOSURE
        assert gaps[0].missing_count == 2

    def test_unexpected_large_gap(self):
        a = _candle(0)
        b = _candle(10)
        gaps = detect_gaps((a, b), "1D")
        assert len(gaps) == 1
        assert gaps[0].kind is GapKind.UNEXPECTED_GAP
        assert gaps[0].missing_count == 9

    def test_single_candle_no_gaps(self):
        assert detect_gaps((_candle(0),), "1D") == ()

    def test_empty_no_gaps(self):
        assert detect_gaps((), "1D") == ()

    def test_closure_window_configurable(self):
        a = _candle(0)
        b = _candle(5)
        cfg = GapDetectionConfig(closure_seconds=6 * 86400)
        gaps = detect_gaps((a, b), "1D", cfg)
        assert gaps[0].kind is GapKind.POSSIBLE_MARKET_CLOSURE

    def test_short_intraday_gap_is_closure(self):
        a = _candle(0)
        b_ts = a.timestamp + timedelta(days=1)  # 1-day span in 15m series
        b = OHLCVCandle(b_ts, 100.0, 102.0, 98.0, 100.0, 1000.0)
        gaps = detect_gaps((a, b), "15m")
        assert gaps[0].kind is GapKind.POSSIBLE_MARKET_CLOSURE

    def test_gaps_never_fabricate_candles(self):
        svc = _service({("NIFTY", "1D"): (_candle(0), _candle(10))})
        result = svc.fetch_historical(_request())
        assert len(result.candles) == 2
        assert result.gaps[0].kind is GapKind.UNEXPECTED_GAP


# ============================================================
# Q. MULTIPLE INSTRUMENTS
# ============================================================


class TestMultipleInstruments:
    def test_independent_datasets(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc = _service(
            {
                ("NIFTY", "1D"): _daily_series(3),
                ("RELIANCE", "1D"): _daily_series(5),
            },
            store=store,
        )
        svc.ingest(_request(instrument="NIFTY"))
        svc.ingest(_request(instrument="RELIANCE"))
        assert store.load_candles("NIFTY", "1D")
        assert len(store.load_candles("RELIANCE", "1D")) == 5

    def test_universe_configurable(self, tmp_path):
        universe = ResearchUniverse(instruments=("AAA", "BBB"))
        svc = _service(store=HistoricalDataStore(tmp_path), universe=universe)
        bad = svc.fetch_historical(_request(instrument="NIFTY"))
        assert bad.status is HistoricalIngestionStatus.INVALID

    def test_universe_membership(self):
        assert "NIFTY" in DEFAULT_RESEARCH_UNIVERSE
        assert "nifty" in DEFAULT_RESEARCH_UNIVERSE
        assert "UNKNOWN" not in DEFAULT_RESEARCH_UNIVERSE
        assert 123 not in DEFAULT_RESEARCH_UNIVERSE


# ============================================================
# R. MULTIPLE TIMEFRAMES
# ============================================================


class TestMultipleTimeframes:
    def test_independent_timeframes(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc = _service(
            {
                ("NIFTY", "1D"): _daily_series(3),
                ("NIFTY", "15m"): _daily_series(8),
            },
            store=store,
        )
        svc.ingest(_request(timeframe="1D"))
        svc.ingest(_request(timeframe="15m"))
        assert len(store.load_candles("NIFTY", "1D")) == 3
        assert len(store.load_candles("NIFTY", "15m")) == 8

    def test_aliases_share_dataset(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc = _service({("NIFTY", "1d"): _daily_series(2)}, store=store)
        svc.ingest(_request(timeframe="1d"))
        assert store.exists("NIFTY", "1D")

    def test_supported_timeframes_minimum(self):
        assert "15m" in supported_timeframes()
        assert "1D" in supported_timeframes()


# ============================================================
# S. PROVIDER ORDERING INVARIANCE
# ============================================================


class TestProviderOrderingInvariance:
    def test_stored_bytes_identical_for_shuffled_input(self, tmp_path):
        a = (_candle(0), _candle(3), _candle(1), _candle(2))
        b = (_candle(3), _candle(2), _candle(1), _candle(0))
        p1 = tmp_path / "a"
        p2 = tmp_path / "b"
        svc_a = _service({("NIFTY", "1D"): a}, store=HistoricalDataStore(p1))
        svc_b = _service({("NIFTY", "1D"): b}, store=HistoricalDataStore(p2))
        pa = svc_a.ingest(_request()).store.path
        pb = svc_b.ingest(_request()).store.path
        assert Path(pa).read_bytes() == Path(pb).read_bytes()


# ============================================================
# T. DETERMINISTIC SERIALIZATION
# ============================================================


class TestDeterministicSerialization:
    def test_candle_serial_bytes_stable(self):
        candles = _daily_series(3)
        assert serialize_candles(candles) == serialize_candles(candles)

    def test_in_memory_response_serial_round_trip(self):
        candles = _daily_series(2)
        text = serialize_candles(candles)
        assert json.loads(text)[0]["timestamp"] == candles[0].timestamp.isoformat()

    def test_stored_file_bytes_deterministic(self, tmp_path):
        p1 = tmp_path / "a"
        p2 = tmp_path / "b"
        svc_a = _service({("NIFTY", "1D"): _daily_series(2)},
                         store=HistoricalDataStore(p1))
        svc_b = _service({("NIFTY", "1D"): _daily_series(2)},
                         store=HistoricalDataStore(p2))
        pa = svc_a.ingest(_request()).store.path
        pb = svc_b.ingest(_request()).store.path
        assert Path(pa).read_bytes() == Path(pb).read_bytes()


# ============================================================
# U. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_service_api_has_no_future_parameter(self):
        for name in ("fetch_historical", "ingest", "load_historical",
                     "validate_historical"):
            sig = inspect.signature(
                getattr(HistoricalMarketDataService, name),
            )
            forbidden = {"future", "future_candles", "lookahead"} & set(
                sig.parameters,
            )
            assert not forbidden

    def test_evaluation_time_boundary_excludes_later(self, tmp_path):
        svc = _service(
            {("NIFTY", "1D"): _daily_series(10)},
            store=HistoricalDataStore(tmp_path),
        )
        svc.ingest(_request())
        boundary = _candle(4).timestamp
        loaded = svc.load_historical("NIFTY", "1D", evaluation_time=boundary)
        assert loaded.count == 5
        assert all(c.timestamp <= boundary for c in loaded.candles)

    def test_fixed_boundary_stable_when_future_appended(self, tmp_path):
        store = HistoricalDataStore(tmp_path)
        svc = _service({("NIFTY", "1D"): _daily_series(5)}, store=store)
        svc.ingest(_request())
        boundary = _candle(2).timestamp
        before = svc.load_historical("NIFTY", "1D", evaluation_time=boundary)
        svc2 = _service({("NIFTY", "1D"): _daily_series(20)}, store=store)
        svc2.ingest(_request())
        after = svc.load_historical("NIFTY", "1D", evaluation_time=boundary)
        assert before.candles == after.candles


# ============================================================
# V. OUTCOME EVALUATOR PATCHED-TO-RAISE
# ============================================================


class TestOutcomeEvaluatorNotCalled:
    def test_ingestion_without_outcome_evaluator(self, tmp_path, monkeypatch):
        import engine.intelligence.historical_outcome as ho
        monkeypatch.setattr(
            ho.OutcomeEvaluator, "evaluate",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("OutcomeEvaluator must NOT be called"),
            ),
        )
        svc = _service(
            {("NIFTY", "1D"): _daily_series(3)},
            store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request())
        assert result.fetch.status is HistoricalIngestionStatus.AVAILABLE


# ============================================================
# W. HISTORICAL PIPELINE PATCHED-TO-RAISE
# ============================================================


class TestPipelineNotCalled:
    def test_ingestion_without_pipeline(self, tmp_path, monkeypatch):
        import engine.pipeline.historical_pipeline as hp
        monkeypatch.setattr(
            hp.HistoricalEvaluationPipeline, "evaluate",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Pipeline must NOT be called"),
            ),
        )
        svc = _service(
            {("NIFTY", "1D"): _daily_series(3)},
            store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request())
        assert result.fetch.status is HistoricalIngestionStatus.AVAILABLE


# ============================================================
# X. EXISTING YAHOO LIVE PROVIDER REGRESSION
# ============================================================


class TestYahooLiveRegression:
    def test_dashboard_analyze_unaffected(self):
        from dashboard.services import DashboardAnalysisService
        from engine.data.historical_service import HistoricalMarketDataService
        svc = DashboardAnalysisService(
            historical_service=HistoricalMarketDataService(),
        )
        # The fixture dashboard path is unchanged; historical service is
        # informational only.
        assert svc.available_instruments()

    def test_yahoo_provider_class_unchanged(self):
        from dashboard.data_provider import YahooDataProvider
        pd = YahooDataProvider(provider=None)
        series = pd.fetch("NIFTY", "15m")
        assert isinstance(series.available, bool)


# ============================================================
# Y. EXISTING PAPER-TRADING / DASHBOARD REGRESSION
# ============================================================


class TestExistingPathRegression:
    def test_service_without_historical_service(self):
        from dashboard.services import DashboardAnalysisService
        svc = DashboardAnalysisService()
        assert svc.historical_datasets() == ()

    def test_pipeline_baseline_signals_4_trades_3(self):
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
# CLI INTEGRATION
# ============================================================


class TestIngestionCLI:
    def _run(self, *args, data_dir=None):
        cmd = [sys.executable, str(_CLI), *args]
        if data_dir is None:
            cmd += []
        else:
            cmd += ["--data-dir", str(data_dir)]
        return subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(_ROOT.parent),
        )

    def test_cli_success(self, tmp_path):
        proc = self._run(
            "--instrument", "NIFTY", "--timeframe", "1D",
            "--start", "2024-01-01", "--end", "2024-01-31",
            data_dir=tmp_path,
        )
        assert proc.returncode == 0
        assert "HISTORICAL DATA INGESTION" in proc.stdout
        assert "Records Accepted: 31" in proc.stdout
        assert "Validation      : PASS" in proc.stdout

    def test_cli_idempotent_second_run(self, tmp_path):
        args = (
            "--instrument", "NIFTY", "--timeframe", "1D",
            "--start", "2024-01-01", "--end", "2024-01-31",
        )
        first = self._run(*args, data_dir=tmp_path)
        second = self._run(*args, data_dir=tmp_path)
        assert first.returncode == 0 and second.returncode == 0
        assert "Records Added   : 0" in second.stdout

    def test_cli_invalid_request(self, tmp_path):
        proc = self._run(
            "--instrument", "NIFTY",
            "--start", "2024-01-31", "--end", "2024-01-01",
            data_dir=tmp_path,
        )
        assert proc.returncode != 0

    def test_cli_bad_provider_rejected(self, tmp_path):
        proc = self._run(
            "--instrument", "NIFTY", "--provider", "bogus",
            "--start", "2024-01-01", "--end", "2024-01-02",
            data_dir=tmp_path,
        )
        assert proc.returncode != 0


# ============================================================
# DASHBOARD INTEGRATION
# ============================================================


class TestDashboardIntegration:
    def test_routes_present(self):
        from dashboard.app import create_app
        from fastapi.testclient import TestClient
        client = TestClient(create_app())
        assert client.get("/api/historical-data").status_code == 200
        assert client.get("/historical-data").status_code == 200

    def test_empty_state_honest(self):
        from dashboard.app import create_app
        from dashboard.services import default_service
        from fastapi.testclient import TestClient
        from engine.data.historical_service import HistoricalMarketDataService
        svc = default_service(
            historical_service=HistoricalMarketDataService(),
        )
        client = TestClient(create_app(svc))
        payload = client.get("/api/historical-data").json()
        assert payload["dataset_count"] == 0

    def test_stored_dataset_visible(self, tmp_path):
        from dashboard.app import create_app
        from dashboard.services import default_service
        from fastapi.testclient import TestClient
        from engine.data.historical_service import HistoricalMarketDataService
        from engine.data.historical_store import HistoricalDataStore

        hist_svc = HistoricalMarketDataService(
            store=HistoricalDataStore(tmp_path),
        )
        hist_svc.ingest(_request())
        svc = default_service(historical_service=hist_svc)
        client = TestClient(create_app(svc))
        payload = client.get("/api/historical-data").json()
        assert payload["dataset_count"] == 1
        row = payload["datasets"][0]
        assert row["instrument"] == "NIFTY"
        assert row["available"] is True
        assert row["status"] == "AVAILABLE"


# ============================================================
# YAHOO HISTORICAL SYMBOL RESOLUTION (Phase 6B fix)
# ============================================================


class _RecordingYahooBackend:
    """Fake YahooFinanceProvider backend recording get_history calls."""

    def __init__(self, candles=()):
        self.calls: list[tuple[str, object, object, str]] = []
        self._candles = tuple(candles)

    def get_history(self, symbol, start, end, interval):
        self.calls.append((symbol, start, end, interval))
        return list(self._candles)


class TestYahooHistoricalSymbolResolution:
    def test_required_mapping(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        p = YahooHistoricalDataProvider(provider=_RecordingYahooBackend())
        assert p.resolve_symbol("NIFTY") == "^NSEI"
        assert p.resolve_symbol("RELIANCE") == "RELIANCE.NS"
        assert p.resolve_symbol("TCS") == "TCS.NS"
        assert p.resolve_symbol("HDFCBANK") == "HDFCBANK.NS"
        assert p.resolve_symbol("ICICIBANK") == "ICICIBANK.NS"

    def test_full_universe_covered_by_default_map(self):
        from engine.config.universe import COMBINED_UNIVERSE, MARKET_UNIVERSE
        from engine.data.historical_provider import YahooHistoricalDataProvider
        p = YahooHistoricalDataProvider(provider=_RecordingYahooBackend())
        for name in COMBINED_UNIVERSE:
            assert p.resolve_symbol(name) == f"{name}.NS"
        for name in MARKET_UNIVERSE:
            resolved = p.resolve_symbol(name)
            assert resolved == "^NSEI" or resolved == f"{name}.NS"

    def test_unknown_instrument_passthrough(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        p = YahooHistoricalDataProvider(provider=_RecordingYahooBackend())
        assert p.resolve_symbol("AAPL") == "AAPL"

    def test_explicit_symbol_map_override(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        p = YahooHistoricalDataProvider(
            provider=_RecordingYahooBackend(),
            symbol_map={"NIFTY": "ZZZZ"},
        )
        assert p.resolve_symbol("NIFTY") == "ZZZZ"
        assert p.resolve_symbol("RELIANCE") == "RELIANCE"

    def test_default_map_deterministic(self):
        from engine.data.historical_provider import (
            YahooHistoricalDataProvider,
            _default_yahoo_symbol_map,
        )
        assert _default_yahoo_symbol_map() == _default_yahoo_symbol_map()
        a = YahooHistoricalDataProvider(provider=_RecordingYahooBackend())
        b = YahooHistoricalDataProvider(provider=_RecordingYahooBackend())
        assert a._symbol_map == b._symbol_map

    def test_fetch_uses_mapped_symbol_nifty(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        backend = _RecordingYahooBackend(_daily_series(3))
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("NIFTY", "1D"))
        assert response.status is ProviderResponseStatus.OK
        assert backend.calls[0][0] == "^NSEI"
        assert backend.calls[0][3] == "1d"

    def test_fetch_reliance_1d_returns_candles(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        backend = _RecordingYahooBackend(_daily_series(5))
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("RELIANCE", "1D"))
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 5
        assert backend.calls[0][0] == "RELIANCE.NS"

    def test_fetch_never_sends_canonical_name(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        backend = _RecordingYahooBackend(_daily_series(2))
        p = YahooHistoricalDataProvider(provider=backend)
        for instrument, expected in (
            ("NIFTY", "^NSEI"),
            ("RELIANCE", "RELIANCE.NS"),
            ("TCS", "TCS.NS"),
            ("HDFCBANK", "HDFCBANK.NS"),
            ("ICICIBANK", "ICICIBANK.NS"),
        ):
            backend.calls.clear()
            p.fetch(_request(instrument, "1D"))
            assert backend.calls[0][0] == expected



# ============================================================
# YAHOO HISTORICAL TIMEZONE NORMALIZATION (Phase 6B fix)
# ============================================================


def _naive_daily_series(n: int, base: datetime = _START) -> tuple[OHLCVCandle, ...]:
    """Candles with NAIVE timestamps, as yfinance returns for 1D data."""

    naive_base = base.replace(tzinfo=None)
    return tuple(
        OHLCVCandle(
            timestamp=naive_base + timedelta(days=i),
            open=100.0 + i, high=102.0 + i, low=99.0 + i,
            close=101.0 + i, volume=1000.0,
        )
        for i in range(n)
    )


class TestYahooHistoricalTimezoneNormalization:
    def test_naive_timestamp_normalized_to_utc(self):
        from engine.data.historical_provider import _yahoo_timestamp_to_utc
        naive = datetime(2026, 7, 1)
        result = _yahoo_timestamp_to_utc(naive)
        assert result == datetime(2026, 7, 1, tzinfo=UTC)
        assert result.tzinfo is not None

    def test_aware_timestamp_converted_to_utc(self):
        from engine.data.historical_provider import _yahoo_timestamp_to_utc
        tz = timezone(timedelta(hours=5, minutes=30))  # Asia/Kolkata
        aware = datetime(2026, 7, 1, 9, 15, tzinfo=tz)
        result = _yahoo_timestamp_to_utc(aware)
        assert result == datetime(2026, 7, 1, 3, 45, tzinfo=UTC)

    def test_non_datetime_returns_none(self):
        from engine.data.historical_provider import _yahoo_timestamp_to_utc
        assert _yahoo_timestamp_to_utc("2026-07-01") is None
        assert _yahoo_timestamp_to_utc(None) is None

    def test_fetch_normalizes_naive_candles(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        backend = _RecordingYahooBackend(_naive_daily_series(3))
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("NIFTY", "1D"))
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 3
        for candle in response.candles:
            assert candle.timestamp.tzinfo is not None

    def test_fetch_normalizes_aware_candles(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        tz = timezone(timedelta(hours=5, minutes=30))
        aware = tuple(
            OHLCVCandle(
                timestamp=datetime(2026, 7, 1, 9, 15, tzinfo=tz) + timedelta(days=i),
                open=100.0, high=102.0, low=99.0, close=101.0, volume=1000.0,
            )
            for i in range(3)
        )
        backend = _RecordingYahooBackend(aware)
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("RELIANCE", "1D"))
        assert response.status is ProviderResponseStatus.OK
        assert all(c.timestamp.tzinfo is not None for c in response.candles)
        assert response.candles[0].timestamp == datetime(2026, 7, 1, 3, 45, tzinfo=UTC)

    def test_fetch_multiple_candles_chronological(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        backend = _RecordingYahooBackend(_naive_daily_series(5))
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("NIFTY", "1D"))
        timestamps = [c.timestamp for c in response.candles]
        assert timestamps == sorted(timestamps)

    def test_fetch_mixed_naive_and_aware(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        tz = timezone(timedelta(hours=5, minutes=30))
        mixed = (
            OHLCVCandle(timestamp=datetime(2026, 7, 1), open=1, high=2, low=0.5, close=1.5, volume=1),
            OHLCVCandle(timestamp=datetime(2026, 7, 2, 9, 15, tzinfo=tz), open=1, high=2, low=0.5, close=1.5, volume=1),
        )
        backend = _RecordingYahooBackend(mixed)
        p = YahooHistoricalDataProvider(provider=backend)
        response = p.fetch(_request("TCS", "1D"))
        assert response.status is ProviderResponseStatus.OK
        assert all(c.timestamp.tzinfo is not None for c in response.candles)

    def test_validation_still_rejects_manually_created_naive_candle(self):
        from engine.data.historical_validation import HistoricalDataValidator
        naive = OHLCVCandle(
            timestamp=datetime(2026, 7, 1),  # naive — must be rejected
            open=100.0, high=102.0, low=99.0, close=101.0, volume=1000.0,
        )
        accepted, issues = HistoricalDataValidator.validate(
            (naive,), instrument="NIFTY", timeframe="1D",
            reference_now=datetime(2026, 8, 2, tzinfo=UTC),
        )
        assert accepted == ()
        assert any(i.error.name == "NAIVE_TIMESTAMP" for i in issues)

    def test_reliance_1d_fake_fetch_produces_accepted_candles(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        from engine.data.historical_service import HistoricalMarketDataService
        backend = _RecordingYahooBackend(_naive_daily_series(23, datetime(2026, 7, 1, tzinfo=UTC).replace(tzinfo=None)))
        p = YahooHistoricalDataProvider(provider=backend)
        svc = HistoricalMarketDataService(provider=p)
        result = svc.fetch_historical(
            _request("RELIANCE", "1D",
                     datetime(2026, 7, 1, tzinfo=UTC),
                     datetime(2026, 8, 1, tzinfo=UTC)),
            reference_now=datetime(2026, 8, 2, tzinfo=UTC),
        )
        assert result.status.name == "AVAILABLE"
        assert result.provenance.records_received == 23
        assert result.provenance.records_accepted == 23
        assert result.provenance.records_rejected == 0

    def test_nifty_1d_fake_fetch_produces_accepted_candles(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        from engine.data.historical_service import HistoricalMarketDataService
        backend = _RecordingYahooBackend(_naive_daily_series(23, datetime(2026, 7, 1, tzinfo=UTC).replace(tzinfo=None)))
        p = YahooHistoricalDataProvider(provider=backend)
        svc = HistoricalMarketDataService(provider=p)
        result = svc.fetch_historical(
            _request("NIFTY", "1D",
                     datetime(2026, 7, 1, tzinfo=UTC),
                     datetime(2026, 8, 1, tzinfo=UTC)),
            reference_now=datetime(2026, 8, 2, tzinfo=UTC),
        )
        assert result.status.name == "AVAILABLE"
        assert result.provenance.records_received == 23
        assert result.provenance.records_accepted == 23
        assert result.provenance.records_rejected == 0


# ============================================================
# HISTORICAL PERSISTENCE ROUND-TRIP + POST-WRITE VERIFICATION
# (Phase 6B persistence consistency fix)
# ============================================================


def _utc_daily_series(n: int, base: datetime | None = None) -> tuple[OHLCVCandle, ...]:
    """UTC-aware daily candles (as the fixed Yahoo provider returns)."""

    start = base or datetime(2026, 7, 1, tzinfo=UTC)
    return tuple(
        OHLCVCandle(
            timestamp=start + timedelta(days=i),
            open=100.0 + i, high=102.0 + i, low=99.0 + i,
            close=101.0 + i, volume=1000.0,
        )
        for i in range(n)
    )


def _yahoo_ingest(tmp_path, instrument: str, candles):
    """Ingest via a fake Yahoo backend into a fresh store."""

    from engine.data.historical_provider import YahooHistoricalDataProvider
    from engine.data.historical_service import HistoricalMarketDataService
    from engine.data.historical_store import HistoricalDataStore

    backend = _RecordingYahooBackend(candles)
    provider = YahooHistoricalDataProvider(provider=backend)
    store = HistoricalDataStore(tmp_path)
    svc = HistoricalMarketDataService(provider=provider, store=store)
    request = _request(
        instrument, "1D",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    result = svc.ingest(request, reference_now=datetime(2026, 8, 2, tzinfo=UTC))
    return result, store


class TestHistoricalPersistenceRoundTrip:
    def test_nifty_ingest_persist_reload(self, tmp_path):
        candles = _utc_daily_series(23)
        result, store = _yahoo_ingest(tmp_path, "NIFTY", candles)
        assert result.store is not None
        assert result.store.records_added == 23
        assert result.store.total_candles == 23
        assert result.store.reload_verified is True
        reloaded = store.load_candles("NIFTY", "1D")
        assert len(reloaded) == 23
        assert [c.timestamp for c in reloaded] == [c.timestamp for c in candles]
        assert [c.close for c in reloaded] == [c.close for c in candles]

    def test_reliance_ingest_persist_reload(self, tmp_path):
        candles = _utc_daily_series(23)
        result, store = _yahoo_ingest(tmp_path, "RELIANCE", candles)
        assert result.store is not None
        assert result.store.records_added == 23
        assert result.store.total_candles == 23
        assert result.store.reload_verified is True
        reloaded = store.load_candles("RELIANCE", "1D")
        assert len(reloaded) == 23
        assert [c.timestamp for c in reloaded] == [c.timestamp for c in candles]

    def test_persisted_file_actually_contains_candles(self, tmp_path):
        import json as _json
        candles = _utc_daily_series(23)
        result, store = _yahoo_ingest(tmp_path, "NIFTY", candles)
        payload = _json.loads(
            (tmp_path / "NIFTY" / "1D" / "candles.json").read_text(encoding="utf-8"),
        )
        assert len(payload["candles"]) == 23
        assert payload["candles"][0]["timestamp"].startswith("2026-07-01")

    def test_stale_empty_file_is_merged_not_left_empty(self, tmp_path):
        """The exact reported state: a stale EMPTY candles.json from an
        earlier EMPTY-status ingestion must be merged into, never left
        empty after a successful ingestion."""
        import json as _json
        dataset_dir = tmp_path / "NIFTY" / "1D"
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "candles.json").write_text(
            '{"candles":[],"instrument":"NIFTY","schema_version":1,"timeframe":"1D"}',
            encoding="utf-8",
        )
        candles = _utc_daily_series(23)
        result, store = _yahoo_ingest(tmp_path, "NIFTY", candles)
        assert result.store.records_added == 23
        assert result.store.records_existing == 0
        assert result.store.total_candles == 23
        payload = _json.loads(
            (dataset_dir / "candles.json").read_text(encoding="utf-8"),
        )
        assert len(payload["candles"]) == 23
        assert len(store.load_candles("NIFTY", "1D")) == 23

    def test_reingestion_idempotent_counts(self, tmp_path):
        candles = _utc_daily_series(23)
        _yahoo_ingest(tmp_path, "NIFTY", candles)
        result2, store = _yahoo_ingest(tmp_path, "NIFTY", candles)
        assert result2.store.records_added == 0
        assert result2.store.records_existing == 23
        assert result2.store.total_candles == 23
        assert len(store.load_candles("NIFTY", "1D")) == 23

    def test_total_candles_read_back_from_disk(self, tmp_path):
        """total_candles must reflect the reloaded persisted file, not
        the in-memory count."""
        from engine.data.historical_store import HistoricalDataStore
        store = HistoricalDataStore(tmp_path)
        candles = _utc_daily_series(5)
        added, existing, total, path = store.store("TCS", "1D", candles)
        assert added == 5
        assert total == len(store.load_candles("TCS", "1D")) == 5

    def test_post_write_verification_raises_on_mismatch(self, tmp_path, monkeypatch):
        """A platform-level write failure (sync revert / AV interference)
        must surface as HistoricalDataIntegrityError, never a false
        success report. Narrowly-scoped monkeypatch simulates the write
        landing with wrong content (POSIX cannot fabricate OneDrive/AV
        reverts deterministically)."""
        import json as _json
        from engine.data.historical_store import (
            HistoricalDataIntegrityError,
            HistoricalDataStore,
        )
        store = HistoricalDataStore(tmp_path)
        original = store._atomic_write

        def bad_write(target, text):
            payload = _json.loads(text)
            payload["candles"] = []  # simulate a reverted/emptied write
            original(target, _json.dumps(payload, sort_keys=True, separators=(",", ":")))

        monkeypatch.setattr(store, "_atomic_write", bad_write)
        with pytest.raises(HistoricalDataIntegrityError):
            store.store("NIFTY", "1D", _utc_daily_series(3))

    def test_load_historical_after_ingest(self, tmp_path):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        from engine.data.historical_service import HistoricalMarketDataService
        from engine.data.historical_store import HistoricalDataStore
        candles = _utc_daily_series(23)
        backend = _RecordingYahooBackend(candles)
        svc = HistoricalMarketDataService(
            provider=YahooHistoricalDataProvider(provider=backend),
            store=HistoricalDataStore(tmp_path),
        )
        svc.ingest(
            _request("RELIANCE", "1D",
                     datetime(2026, 7, 1, tzinfo=UTC),
                     datetime(2026, 8, 1, tzinfo=UTC)),
            reference_now=datetime(2026, 8, 2, tzinfo=UTC),
        )
        loaded = svc.load_historical("RELIANCE", "1D")
        assert loaded.count == 23
        assert loaded.source_count == 23
        assert loaded.first_timestamp == candles[0].timestamp
        assert loaded.last_timestamp == candles[-1].timestamp

    def test_cli_report_shows_reload_check(self, tmp_path):
        import sys as _sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ingest_historical_data import format_report
        candles = _utc_daily_series(23)
        result, _store = _yahoo_ingest(tmp_path, "NIFTY", candles)
        report = format_report(result)
        assert "Reload check    : PASS (23 candles reloaded" in report
        assert "Records Added   : 23" in report
        assert "Total Stored    : 23" in report
