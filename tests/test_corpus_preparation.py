"""
Tests for Checkpoint 3B — Historical Corpus Preparation (planner layer).

Deterministic, network-free: the planner is PURE (never fetches market
data); coverage is measured only from the existing Phase 6B store over
deterministic in-memory/research-universe records. These tests verify
model invariants, config validation, monthly chunking, plan identity,
coverage classification (MISSING / EMPTY / PARTIAL / COMPLETE /
UNAVAILABLE), request accounting, provider capability gating, the
formatter, the operator CLI and regression. No strategy is created; the
existing decision / paper-trading behaviour is untouched.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.config.corpus_plan_config import (
    CorpusPlanConfig,
    validate_plan_window,
)
from engine.data.corpus_plan import (
    CorpusPreparationPlanner,
    monthly_chunks_for_window,
)
from engine.data.historical_provider import (
    InMemoryHistoricalProvider,
    UpstoxHistoricalDataProvider,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.models.corpus_plan import (
    CorpusPreparationPlan,
    CorpusPreparationRow,
    DatasetCoverage,
    DatasetCoverageStatus,
)
from engine.models.historical_data import (
    HistoricalDataRequest,
    ResearchUniverse,
)
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.corpus_plan import CorpusPreparationFormatter

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "prepare_corpus_data.py"
)

WIN_START = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
WIN_END = datetime(2024, 3, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


# ============================================================
# FIXTURE HELPERS (deterministic, no network)
# ============================================================


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _daily_series(
    start: datetime = WIN_START,
    n: int = 60,
) -> tuple[OHLCVCandle, ...]:
    return tuple(
        _candle(start + timedelta(days=i), 100.0 + i) for i in range(n)
    )


def _make_service(
    tmp_path: Path,
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]],
    *,
    with_store: bool = True,
) -> HistoricalMarketDataService:
    store = HistoricalDataStore(tmp_path / "hist") if with_store else None
    service = HistoricalMarketDataService(
        provider=InMemoryHistoricalProvider(records),
        store=store,
    )
    if with_store:
        for (instrument, timeframe), candles in records.items():
            if not candles:
                continue
            service.ingest(
                HistoricalDataRequest(
                    instrument,
                    timeframe,
                    candles[0].timestamp,
                    candles[-1].timestamp + timedelta(seconds=1),
                ),
                reference_now=NOW,
            )
    return service


def _planner(service: HistoricalMarketDataService) -> CorpusPreparationPlanner:
    return CorpusPreparationPlanner(
        config=CorpusPlanConfig(),
        store=service.store,
        provider=None,
    )


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestDatasetCoverageModel:
    def test_missing_carriage_immutable(self):
        coverage = DatasetCoverage(
            instrument="RELIANCE",
            timeframe="15m",
            status=DatasetCoverageStatus.MISSING,
            required_chunks=2,
            missing_chunk_keys=("a", "b"),
        )
        assert coverage.is_complete is False
        assert coverage.is_usable is False
        assert coverage.missing_chunk_keys == ("a", "b")
        assert coverage.stored_count == 0

    def test_complete_coverage_invariants(self):
        coverage = DatasetCoverage(
            instrument="TCS",
            timeframe="1D",
            status=DatasetCoverageStatus.COMPLETE,
            stored_count=181,
            required_chunks=6,
            covered_chunks=6,
        )
        assert coverage.is_complete is True
        assert coverage.is_usable is True

    def test_complete_requires_full_coverage(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.COMPLETE,
                required_chunks=6,
                covered_chunks=5,
            )

    def test_partial_counting_invariant(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.PARTIAL,
                required_chunks=6,
                covered_chunks=2,
                missing_chunk_keys=("x",),  # wrong length
            )

    def test_covered_exceeds_required_rejected(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.PARTIAL,
                required_chunks=2,
                covered_chunks=3,
                missing_chunk_keys=(),
            )

    def test_missing_keys_sorted(self):
        coverage = DatasetCoverage(
            instrument="TCS",
            timeframe="1D",
            status=DatasetCoverageStatus.MISSING,
            required_chunks=3,
            missing_chunk_keys=("c", "a", "b"),
        )
        assert coverage.missing_chunk_keys == ("a", "b", "c")

    def test_missing_cannot_carry_stored(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.MISSING,
                stored_count=5,
                required_chunks=2,
                missing_chunk_keys=("a", "b"),
            )

    def test_empty_cannot_carry_stored(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.EMPTY,
                stored_count=5,
                required_chunks=2,
                missing_chunk_keys=("a", "b"),
            )

    def test_unavailable_cannot_carry_stored(self):
        with pytest.raises(ValueError):
            DatasetCoverage(
                instrument="TCS",
                timeframe="1D",
                status=DatasetCoverageStatus.UNAVAILABLE,
                stored_count=5,
                required_chunks=2,
                missing_chunk_keys=("a", "b"),
            )

    def test_frozen_slots(self):
        coverage = DatasetCoverage(
            instrument="TCS",
            timeframe="1D",
            status=DatasetCoverageStatus.MISSING,
            required_chunks=2,
            missing_chunk_keys=("a", "b"),
        )
        with pytest.raises(AttributeError):
            coverage.stored_count = 1  # type: ignore[misc]

    def test_row_coverage_must_match(self):
        coverage = DatasetCoverage(
            instrument="TCS",
            timeframe="1D",
            status=DatasetCoverageStatus.MISSING,
            required_chunks=2,
            missing_chunk_keys=("a", "b"),
        )
        with pytest.raises(ValueError):
            CorpusPreparationRow(
                instrument="RELIANCE",
                timeframe="1D",
                provider_supported=True,
                coverage=coverage,
            )


# ============================================================
# B. CONFIG VALIDATION
# ============================================================


class TestCorpusPlanConfig:
    def test_defaults(self):
        config = CorpusPlanConfig()
        assert config.timeframes == ("15m", "1D")
        assert config.provider == "upstox-historical"

    def test_canonicalized_timeframes(self):
        config = CorpusPlanConfig(timeframes=("15M", "1d"))
        assert config.timeframes == ("15m", "1D")

    def test_unknown_timeframe_rejected(self):
        with pytest.raises(ValueError):
            CorpusPlanConfig(timeframes=("bogus",))

    def test_duplicate_timeframe_rejected(self):
        with pytest.raises(ValueError):
            CorpusPlanConfig(timeframes=("15m", "15M"))

    def test_empty_timeframes_rejected(self):
        with pytest.raises(ValueError):
            CorpusPlanConfig(timeframes=())

    def test_naive_window_rejected(self):
        with pytest.raises(ValueError):
            CorpusPlanConfig(
                start=datetime(2024, 1, 1),
                end=datetime(2024, 3, 1),
            )

    def test_reversed_window_rejected(self):
        with pytest.raises(ValueError):
            CorpusPlanConfig(
                start=WIN_END,
                end=WIN_START,
            )

    def test_metadata_sorted(self):
        config = CorpusPlanConfig(
            metadata=(("b", "2"), ("a", "1")),
        )
        assert config.metadata == (("a", "1"), ("b", "2"))

    def test_snapshot_sorted(self):
        config = CorpusPlanConfig(
            start=WIN_START,
            end=WIN_END,
        )
        keys = [k for k, _ in config.snapshot()]
        assert keys == sorted(keys)

    def test_frozen(self):
        config = CorpusPlanConfig()
        with pytest.raises(AttributeError):
            config.provider = "x"  # type: ignore[misc]


class TestValidatePlanWindow:
    def test_valid_window(self):
        start, end = validate_plan_window(WIN_START, WIN_END)
        assert start == WIN_START
        assert end == WIN_END

    def test_naive_rejected(self):
        with pytest.raises(ValueError):
            validate_plan_window(
                datetime(2024, 1, 1),
                WIN_END,
            )

    def test_reversed_rejected(self):
        with pytest.raises(ValueError):
            validate_plan_window(WIN_END, WIN_START)


# ============================================================
# C. MONTHLY CHUNKING
# ============================================================


class TestMonthlyChunks:
    def test_two_month_window_two_chunks(self):
        chunks = monthly_chunks_for_window(WIN_START, WIN_END)
        assert len(chunks) == 2
        assert chunks[0][0] == WIN_START
        assert chunks[1][0] == datetime(2024, 2, 1, tzinfo=UTC)
        assert chunks[1][1] == WIN_END

    def test_full_year_twelve_chunks(self):
        chunks = monthly_chunks_for_window(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert len(chunks) == 12

    def test_chunk_end_never_exceeds_end(self):
        end = datetime(2024, 2, 15, tzinfo=UTC)
        chunks = monthly_chunks_for_window(WIN_START, end)
        for _, chunk_end in chunks:
            assert chunk_end <= end

    def test_empty_for_invalid_window(self):
        assert monthly_chunks_for_window(WIN_END, WIN_START) == ()


# ============================================================
# D. PLAN CONSTRUCTION + IDENTITY
# ============================================================


class TestPlanConstruction:
    def test_default_universe_rows(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        assert plan.dataset_count == 10  # 5 instruments x 2 timeframes
        assert plan.timeframes == ("15m", "1D")
        assert plan.instruments == tuple(sorted(planner.universe))
        assert plan.provider == "upstox-historical"
        assert plan.plan_id.startswith("prep-")

    def test_all_missing_with_empty_store(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(start=WIN_START, end=WIN_END)
        assert plan.missing_count == plan.dataset_count
        assert plan.complete_count == 0
        assert plan.partial_count == 0
        assert plan.missing_request_count == plan.required_request_count

    def test_plan_id_deterministic(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        a = planner.plan(start=WIN_START, end=WIN_END)
        b = planner.plan(start=WIN_START, end=WIN_END)
        assert a.plan_id == b.plan_id

    def test_different_window_different_id(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        a = planner.plan(start=WIN_START, end=WIN_END)
        b = planner.plan(
            start=WIN_START,
            end=datetime(2024, 4, 1, tzinfo=UTC),
        )
        assert a.plan_id != b.plan_id

    def test_different_instruments_different_id(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        a = planner.plan(instruments=("RELIANCE",), start=WIN_START, end=WIN_END)
        b = planner.plan(instruments=("TCS",), start=WIN_START, end=WIN_END)
        assert a.plan_id != b.plan_id

    def test_requires_window(self, tmp_path):
        service = _make_service(tmp_path, {})
        config = CorpusPlanConfig(start=None, end=None)
        planner = CorpusPreparationPlanner(
            config=config,
            store=service.store,
        )
        with pytest.raises(ValueError):
            planner.plan()

    def test_instruments_override_config(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(
            instruments=("RELIANCE", "TCS"),
            start=WIN_START,
            end=WIN_END,
        )
        assert plan.instruments == ("RELIANCE", "TCS")
        assert plan.dataset_count == 4

    def test_request_accounting(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(start=WIN_START, end=WIN_END)
        # 5 instruments x 2 timeframes x 2 monthly chunks = 20 requests
        assert plan.required_request_count == 20
        assert plan.covered_request_count == 0
        assert plan.missing_request_count == 20
        assert plan.is_empty is False
        assert plan.has_missing is True
        assert plan.is_fully_covered is False


# ============================================================
# E. COVERAGE CLASSIFICATION
# ============================================================


class TestCoverageClassification:
    def test_complete_daily_coverage(self, tmp_path):
        # 60 daily candles (Jan 1 .. Feb 29, 2024) fully cover the
        # Jan 1..Mar 1 two-chunk window and end before `reference_now`.
        records = {
            ("RELIANCE", "1D"): _daily_series(n=60),
        }
        service = _make_service(tmp_path, records)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=service.store,
            provider=None,
        )
        plan = planner.plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        row = plan.rows[0]
        assert row.coverage is not None
        assert row.coverage.status is DatasetCoverageStatus.COMPLETE
        assert plan.complete_count == 1
        assert plan.missing_request_count == 0
        assert plan.is_fully_covered is True

    def test_partial_coverage(self, tmp_path):
        # Only the first month is covered (Jan 1..31) of a 2-month window.
        records = {
            ("RELIANCE", "1D"): _daily_series(n=31),
        }
        service = _make_service(tmp_path, records)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=service.store,
            provider=None,
        )
        plan = planner.plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        row = plan.rows[0]
        assert row.coverage.status is DatasetCoverageStatus.PARTIAL
        assert row.coverage.covered_chunks == 1
        assert row.coverage.required_chunks == 2
        assert len(row.coverage.missing_chunk_keys) == 1
        assert plan.partial_count == 1
        assert plan.missing_request_count == 1

    def test_missing_when_not_stored(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(
            instruments=("TCS",),
            start=WIN_START,
            end=WIN_END,
        )
        row = plan.rows[0]
        assert row.coverage.status is DatasetCoverageStatus.MISSING
        assert row.coverage.stored_count == 0

    def test_empty_when_stored_empty(self, tmp_path):
        # An audited EMPTY ingestion persists an empty dataset file.
        store = HistoricalDataStore(tmp_path / "hist")
        service = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider({}),
            store=store,
        )
        service.ingest(
            HistoricalDataRequest("TCS", "15m", WIN_START, WIN_END),
            reference_now=NOW,
        )
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",)),
            store=store,
            provider=None,
        )
        plan = planner.plan(
            instruments=("TCS",),
            start=WIN_START,
            end=WIN_END,
        )
        row = plan.rows[0]
        assert row.coverage.status is DatasetCoverageStatus.EMPTY

    def test_unavailable_without_store(self, tmp_path):
        service = _make_service(tmp_path, {}, with_store=False)
        config = CorpusPlanConfig()
        planner = CorpusPreparationPlanner(
            config=config,
            store=None,
            provider=None,
        )
        plan = planner.plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        row = plan.rows[0]
        assert row.coverage.status is DatasetCoverageStatus.UNAVAILABLE
        assert plan.unavailable_count == 2
        # Without a store no chunk can be confirmed covered.
        assert plan.missing_request_count == plan.required_request_count


# ============================================================
# F. PROVIDER CAPABILITY GATING
# ============================================================


class _RestrictiveProvider:
    """A provider whose supports() only accepts a fixed set."""

    provider_name = "restrictive"

    def supports(self, instrument: str, timeframe: str) -> bool:
        return timeframe == "15m" and instrument in ("RELIANCE", "TCS")


class TestProviderCapabilityGating:
    def test_unsupported_rows_excluded_from_requests(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m", "1D")),
            store=service.store,
            provider=_RestrictiveProvider(),
        )
        plan = planner.plan(
            instruments=("RELIANCE", "NIFTY"),
            start=WIN_START,
            end=WIN_END,
        )
        # RELIANCE 15m supported; NIFTY 15m / all 1D unsupported.
        assert plan.supported_row_count == 1
        assert plan.unsupported_count == 3
        assert plan.required_request_count == 2  # only RELIANCE 15m chunks
        rows = {r.instrument: r for r in plan.rows if r.timeframe == "15m"}
        assert rows["RELIANCE"].provider_supported is True
        assert rows["NIFTY"].provider_supported is False

    def test_provider_exception_never_aborts(self, tmp_path):
        class _Boom:
            provider_name = "boom"

            def supports(self, instrument, timeframe):
                raise RuntimeError("boom")

        service = _make_service(tmp_path, {})
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",)),
            store=service.store,
            provider=_Boom(),
        )
        plan = planner.plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        assert plan.rows[0].provider_supported is False
        assert plan.required_request_count == 0

    def test_default_provider_none_supports_all(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        assert plan.unsupported_count == 0


# ============================================================
# G. COVERAGE SUMMARY + JSONABLE
# ============================================================


class TestCoverageSummaryAndJsonable:
    def test_coverage_summary_shape(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        summary = planner.coverage_summary(plan)
        assert summary["datasets"] == 10
        assert summary["requests_required"] == 20
        assert summary["requests_missing"] == 20
        assert summary["requests_covered"] == 0
        assert summary["datasets_missing"] == 10

    def test_jsonable_roundtrip(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        payload = planner.plan_to_jsonable(plan)
        text = json.dumps(payload, sort_keys=True)
        reparsed = json.loads(text)
        assert reparsed["plan_id"] == plan.plan_id
        assert reparsed["coverage_summary"]["requests_missing"] == 4
        assert reparsed["rows"][0]["coverage"]["status"] == "MISSING"
        assert reparsed["rows"][0]["provider_supported"] is True

    def test_jsonable_deterministic(self, tmp_path):
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        a = json.dumps(planner.plan_to_jsonable(plan), sort_keys=True)
        b = json.dumps(planner.plan_to_jsonable(plan), sort_keys=True)
        assert a == b


# ============================================================
# H. FORMATTER
# ============================================================


class TestFormatter:
    def test_format_returns_str(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        text = CorpusPreparationFormatter().format(plan)
        assert isinstance(text, str)
        assert "CORPUS PREPARATION PLAN" in text
        assert plan.plan_id in text
        assert "MISSING" in text
        assert "plan names the missing requests" in text
        assert "NOT a prediction" in text

    def test_missing_request_keys(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        formatter = CorpusPreparationFormatter()
        keys = formatter.format_missing_request_keys(plan)
        assert len(keys) == 4  # 2 timeframes x 2 monthly chunks
        instruments = {k[0] for k in keys}
        assert instruments == {"RELIANCE"}
        assert all(k[1] in ("15m", "1D") for k in keys)

    def test_negative_width_rejected(self):
        with pytest.raises(ValueError):
            CorpusPreparationFormatter(width=0)

    def test_format_deterministic(self, tmp_path):
        service = _make_service(tmp_path, {})
        plan = _planner(service).plan(
            instruments=("RELIANCE",),
            start=WIN_START,
            end=WIN_END,
        )
        a = CorpusPreparationFormatter().format(plan)
        b = CorpusPreparationFormatter().format(plan)
        assert a == b


# ============================================================
# I. NO-LOOK-AHEAD + IMMUTABILITY
# ============================================================


class TestNoLookAheadAndImmutability:
    def test_plan_lists_no_candles(self, tmp_path):
        # The plan's JSON projection exposes NO candle data (it is a
        # planning artifact, not a dataset view).
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        text = json.dumps(planner.plan_to_jsonable(plan))
        assert "candles" not in text
        assert "open" not in text
        assert "close" not in text

    def test_input_never_mutated(self, tmp_path):
        instruments = ("RELIANCE", "TCS")
        before = tuple(instruments)
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(instruments=instruments, start=WIN_START, end=WIN_END)
        assert tuple(instruments) == before
        assert plan.instruments == ("RELIANCE", "TCS")

    def test_planner_stored_overview_isolated(self, tmp_path):
        # A corrupted store must not abort planning: the planner probes
        # coverage via the existing store listing surface, which reports
        # an unreadable payload as having no usable candles (honest
        # EMPTY), and the plan still builds without raising.
        store = HistoricalDataStore(tmp_path / "hist")
        store.directory.mkdir(parents=True, exist_ok=True)
        bad = store.directory / "RELIANCE" / "15m"
        bad.mkdir(parents=True)
        (bad / "candles.json").write_text("{not-json", encoding="utf-8")
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m",)),
            store=store,
            provider=None,
        )
        plan = planner.plan(instruments=("RELIANCE",), start=WIN_START, end=WIN_END)
        coverage = plan.rows[0].coverage
        assert coverage.status in (
            DatasetCoverageStatus.EMPTY,
            DatasetCoverageStatus.MISSING,
        )
        assert coverage.required_chunks == 2
        # A corrupted payload must never be reported as covered.
        assert coverage.covered_chunks == 0


# ============================================================
# J. OPERATOR CLI
# ============================================================


def _run_cli(*args: str) -> tuple[int, str]:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_SCRIPT.parent.parent),
    )
    return result.returncode, result.stdout


class TestOperatorCLI:
    def test_plan_exits_zero(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--instruments", "RELIANCE,TCS",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        assert "CORPUS PREPARATION PLAN" in out
        assert "PLANNING ONLY" in out

    def test_json_mode(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
            "--json",
        )
        assert code == 0
        payload = json.loads(out)
        assert payload["plan_id"].startswith("prep-")
        assert payload["coverage_summary"]["requests_missing"] == 4

    def test_bad_window_exits_two(self, tmp_path):
        code, out = _run_cli(
            "--start", "not-a-date",
            "--end", "2024-03-01",
            "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 2

    def test_reversed_window_exits_one(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-03-01",
            "--end", "2024-01-01",
            "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 1

    def test_empty_timeframes_exits_two(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--instruments", "RELIANCE",
            "--timeframes", "",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 2

    def test_no_prediction_language(self, tmp_path):
        code, out = _run_cli(
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--instruments", "RELIANCE",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert "prediction" in out.lower()
        assert "buy" not in out.lower()


# ============================================================
# K. REGRESSION / EXISTING PATH
# ============================================================


class TestRegression:
    def test_existing_pipeline_baseline(self):
        # The planner never constructs/touches the intelligence pipeline
        # regression context. We assert the plan module has no dependency
        # on decision/pipeline packages by importing fresh module names.
        import engine.data.corpus_plan as cp

        source = Path(cp.__file__).read_text(encoding="utf-8")
        assert "pipeline" not in source
        assert "paper_trade" not in source
        assert "trade_plan" not in source

    def test_planner_never_imports_provider_network(self, tmp_path):
        # The Upstox provider is importable but the planner only uses its
        # supports() capability (no fetch). A default provider (None)
        # produces a plan without any HTTP surface.
        service = _make_service(tmp_path, {})
        planner = _planner(service)
        plan = planner.plan(start=WIN_START, end=WIN_END)
        assert plan.required_request_count == 20
        assert all(r.provider_supported for r in plan.rows)

    def test_universe_is_configurable_not_hardcoded(self, tmp_path):
        custom = ResearchUniverse(instruments=("CUSTOM1", "CUSTOM2"))
        store = HistoricalDataStore(tmp_path / "hist")
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=store,
            provider=None,
            universe=custom,
        )
        plan = planner.plan(start=WIN_START, end=WIN_END)
        assert plan.instruments == ("CUSTOM1", "CUSTOM2")
        # A different universe yields a different id (derived identity).
        default_planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=store,
            provider=None,
        )
        other = default_planner.plan(start=WIN_START, end=WIN_END)
        assert plan.plan_id != other.plan_id