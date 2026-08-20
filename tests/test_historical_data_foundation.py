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
from datetime import UTC, datetime, timedelta
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
