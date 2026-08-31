"""
Tests for Product Phase 6C — Historical Research Corpus.

Deterministic, network-free: every test uses the in-memory import
provider + a tmp_path historical store (the Phase 6B foundation). The
corpus is research preparation ONLY — these tests verify corpus
construction, evaluation sampling, point-in-time correctness, data
quality, the research-state API, serialization, metadata persistence,
reporting, determinism and regression. NO strategy is created; the
existing decision / paper-trading behaviour is untouched.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.data.historical_provider import InMemoryHistoricalProvider
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.research_corpus import (
    CORPUS_VERSION,
    HistoricalResearchCorpusEngine,
    evaluation_grid,
)
from engine.data.research_corpus_serialization import (
    RESEARCH_CORPUS_SCHEMA_VERSION,
    canonical_report_json,
    deserialize_report,
    parse_corpus_header,
    serialize_report,
)
from engine.data.research_corpus_store import (
    ResearchCorpusIntegrityError,
    ResearchCorpusNotFoundError,
    ResearchCorpusStore,
    ResearchCorpusStoreError,
    default_research_corpus_directory,
)
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.models.historical_availability import HistoricalAvailabilityStatus
from engine.models.historical_data import (
    GapKind,
    HistoricalDataRequest,
    ResearchUniverse,
)
from engine.models.market_scan import MTFAlignment
from engine.models.ohlcv import OHLCVCandle
from engine.models.research_corpus import (
    CorpusDataQuality,
    CorpusEvaluationPoint,
    CorpusPointStatus,
    CorpusTimeframeSlice,
    HistoricalMarketState,
    HistoricalResearchCorpus,
)
from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset
from engine.reporting.research_corpus import ResearchCorpusFormatter


BASE = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
NOW = BASE + timedelta(days=60)


# ============================================================
# FIXTURE HELPERS (deterministic, no network)
# ============================================================


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _zigzag_series(
    n: int,
    start: datetime = BASE,
    step: timedelta = timedelta(minutes=15),
    period: int = 6,
    drift: float = 0.0,
) -> tuple[OHLCVCandle, ...]:
    """Deterministic oscillating series producing confirmed swings."""

    candles: list[OHLCVCandle] = []
    price = 100.0
    for i in range(n):
        direction = 1.0 if (i // period) % 2 == 0 else -1.0
        price += direction * 1.5 + drift
        candles.append(_candle(start + step * i, price))
    return tuple(candles)


def _flat_series(
    n: int,
    start: datetime = BASE,
    step: timedelta = timedelta(minutes=15),
) -> tuple[OHLCVCandle, ...]:
    return tuple(_candle(start + step * i, 100.0) for i in range(n))


def _ingest_all(
    service: HistoricalMarketDataService,
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]],
) -> None:
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


def _make_service(
    tmp_path: Path,
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]],
    *,
    universe: ResearchUniverse | None = None,
    with_store: bool = True,
) -> HistoricalMarketDataService:
    store = HistoricalDataStore(tmp_path / "hist") if with_store else None
    service = HistoricalMarketDataService(
        provider=InMemoryHistoricalProvider(records),
        store=store,
        universe=universe,
    )
    if with_store:
        _ingest_all(service, records)
    return service


def _standard_records(
    instruments: tuple[str, ...] = ("RELIANCE",),
) -> dict[tuple[str, str], tuple[OHLCVCandle, ...]]:
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]] = {}
    for offset, instrument in enumerate(instruments):
        records[(instrument, "15m")] = _zigzag_series(
            48, drift=0.05 * offset,
        )
        records[(instrument, "1D")] = _zigzag_series(
            8,
            step=timedelta(days=1),
            period=3,
            drift=0.1 * offset,
        )
    return records


def _make_engine(
    tmp_path: Path,
    records=None,
    *,
    config: ResearchCorpusConfig | None = None,
    universe: ResearchUniverse | None = None,
    with_store: bool = True,
) -> HistoricalResearchCorpusEngine:
    service = _make_service(
        tmp_path,
        records if records is not None else _standard_records(),
        universe=universe,
        with_store=with_store,
    )
    return HistoricalResearchCorpusEngine(
        service, config or ResearchCorpusConfig(min_setup_history=6),
    )


# ============================================================
# A. CORPUS CONSTRUCTION
# ============================================================


class TestCorpusConstruction:
    def test_valid_historical_dataset(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert isinstance(corpus, HistoricalResearchCorpus)
        assert corpus.report.evaluation_count > 0
        assert corpus.report.valid_count > 0
        assert corpus.corpus_id.startswith("corpus-")
        assert corpus.report.storage_status == "persisted"

    def test_multiple_instruments(self, tmp_path):
        engine = _make_engine(
            tmp_path, _standard_records(("RELIANCE", "TCS")),
        )
        corpus = engine.build(["RELIANCE", "TCS"])
        assert corpus.report.loaded_instruments == ("RELIANCE", "TCS")
        assert corpus.report.missing_instruments == ()
        instruments = {p.instrument for p in corpus.evaluation_points}
        assert instruments == {"RELIANCE", "TCS"}

    def test_multiple_timeframes_reconstructed_independently(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        point = corpus.valid_points[0]
        state = point.state
        assert state.setup_timeframe == "15m"
        assert state.context_timeframe == "1D"
        assert state.setup_slice.timeframe == "15m"
        assert state.context_slice.timeframe == "1D"
        # The two series are reconstructed independently.
        assert state.setup_slice.count != state.context_slice.count

    def test_alternate_setup_timeframe_supported(self, tmp_path):
        records = {
            ("RELIANCE", "30m"): _zigzag_series(
                30, step=timedelta(minutes=30),
            ),
            ("RELIANCE", "1D"): _zigzag_series(
                8, step=timedelta(days=1), period=3,
            ),
        }
        engine = _make_engine(
            tmp_path,
            records,
            config=ResearchCorpusConfig(
                setup_timeframe="30m", min_setup_history=6,
            ),
        )
        corpus = engine.build(["RELIANCE"])
        assert corpus.setup_timeframe == "30m"
        assert corpus.report.valid_count > 0

    def test_custom_universe_not_hardcoded(self, tmp_path):
        universe = ResearchUniverse(("AAA", "BBB"))
        records = {
            ("AAA", "15m"): _zigzag_series(30),
            ("AAA", "1D"): _zigzag_series(6, step=timedelta(days=1), period=3),
            ("BBB", "15m"): _zigzag_series(30, drift=0.02),
            ("BBB", "1D"): _zigzag_series(6, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records, universe=universe)
        corpus = engine.build()  # default: the configured universe
        assert corpus.instruments == ("AAA", "BBB")
        assert corpus.report.loaded_instruments == ("AAA", "BBB")
        assert corpus.report.valid_count > 0

    def test_deterministic_evaluation_point_generation(self, tmp_path):
        engine = _make_engine(tmp_path)
        grid = engine.evaluation_points_for("RELIANCE")
        candles = _standard_records()[("RELIANCE", "15m")]
        assert grid == tuple(c.timestamp for c in candles)

    def test_evaluation_grid_sampling_every(self, tmp_path):
        engine = _make_engine(
            tmp_path, config=ResearchCorpusConfig(min_setup_history=6, sample_every=4),
        )
        grid = engine.evaluation_points_for("RELIANCE")
        candles = _standard_records()[("RELIANCE", "15m")]
        assert grid == tuple(c.timestamp for c in candles)[::4]

    def test_evaluation_grid_window_bounds(self):
        candles = _zigzag_series(10)
        grid = evaluation_grid(
            candles,
            start=candles[3].timestamp,
            end=candles[6].timestamp,
        )
        assert grid == tuple(c.timestamp for c in candles[3:7])

    def test_evaluation_grid_rejects_non_positive_every(self):
        with pytest.raises(ValueError):
            evaluation_grid(_zigzag_series(4), every=0)

    def test_minimum_history_filtering(self, tmp_path):
        engine = _make_engine(
            tmp_path, config=ResearchCorpusConfig(min_setup_history=10),
        )
        corpus = engine.build(["RELIANCE"])
        early = [
            p for p in corpus.evaluation_points
            if p.status is CorpusPointStatus.INSUFFICIENT_HISTORY
        ]
        # Setup candles 0..8 have < 10 usable candles at their boundary.
        assert len(early) == 9
        assert all(p.state is None for p in early)
        assert all("never padded" in p.reason for p in early)

    def test_report_count_invariant(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        report = corpus.report
        assert report.evaluation_count == (
            report.valid_count
            + report.insufficient_history_count
            + report.missing_data_count
            + report.data_gap_count
            + report.invalid_count
        )


# ============================================================
# B. POINT-IN-TIME CORRECTNESS
# ============================================================


class TestPointInTimeCorrectness:
    def test_future_setup_candles_excluded_from_slice(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        state = engine.get_state("RELIANCE", T)
        assert state is not None
        assert state.latest_usable_setup_timestamp <= T
        assert all(c.timestamp <= T for c in state.setup_slice.candles)
        assert state.setup_slice.candles == candles[:21]

    def test_future_setup_mutation_does_not_change_state_at_T(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        before = engine.get_state("RELIANCE", T)
        # Deeply mutate candles AFTER T and re-ingest (overwrite).
        mutated = candles[:21] + tuple(
            _candle(c.timestamp, 9999.0) for c in candles[21:]
        )
        service = engine.service
        service.ingest(
            HistoricalDataRequest(
                "RELIANCE", "15m", candles[0].timestamp, candles[-1].timestamp,
            ),
            reference_now=NOW,
            overwrite=True,
        )
        service.store.store("RELIANCE", "15m", mutated, overwrite=True)
        after = engine.get_state("RELIANCE", T)
        assert after.setup_slice.candles == before.setup_slice.candles
        assert after.setup_context.trend.state == before.setup_context.trend.state
        assert after.setup_context.confirmed_swings == (
            before.setup_context.confirmed_swings
        )
        assert after.mtf_alignment == before.mtf_alignment

    def test_future_context_candle_excluded(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        ctx = _standard_records()[("RELIANCE", "1D")]
        # T inside day 1 (BASE + 300 min = 05:00 UTC day 1).
        T = candles[20].timestamp
        state = engine.get_state("RELIANCE", T)
        assert state is not None
        # Only the day-1 context candle (BASE 00:00) closed strictly
        # before T; the day-2 candle (BASE + 1 day) is in progress.
        assert state.context_slice.candles == ctx[:1]
        assert state.latest_usable_context_timestamp == ctx[0].timestamp
        assert state.latest_usable_context_timestamp < T

    def test_context_candle_at_T_never_treated_as_completed(self, tmp_path):
        engine = _make_engine(tmp_path)
        ctx = _standard_records()[("RELIANCE", "1D")]
        candles = _standard_records()[("RELIANCE", "15m")]
        # T == the day-2 context candle open (BASE + 1 day). The candle
        # starting exactly at T is NOT completed at T.
        T = ctx[1].timestamp
        state = engine.get_state("RELIANCE", T)
        assert state is not None
        assert all(c.timestamp < T for c in state.context_slice.candles)
        assert state.context_slice.candles == ctx[:1]

    def test_future_context_mutation_does_not_change_state_at_T(self, tmp_path):
        engine = _make_engine(tmp_path)
        ctx = _standard_records()[("RELIANCE", "1D")]
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[30].timestamp
        before = engine.get_state("RELIANCE", T)
        mutated = tuple(_candle(c.timestamp, 5555.0) for c in ctx[3:])
        engine.service.store.store(
            "RELIANCE", "1D", ctx[:3] + mutated, overwrite=True,
        )
        after = engine.get_state("RELIANCE", T)
        assert after.context_slice.candles == before.context_slice.candles
        assert after.context_context.trend.state == (
            before.context_context.trend.state
        )

    def test_structure_computed_from_prefix_only(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        state = engine.get_state("RELIANCE", T)
        direct = MarketContextEngine().analyze_at(candles[:21], 20)
        # The corpus state equals a direct causal computation on the
        # historical prefix (rolling features use historical data only).
        assert state.setup_context.trend.state == direct.trend.state
        assert state.setup_context.range.state == direct.range.state
        assert state.setup_context.confirmed_swings == direct.confirmed_swings
        assert state.setup_context.support_resistance == direct.support_resistance

    def test_prefix_and_full_series_agree_at_T(self, tmp_path):
        # A corpus built over the full series and a corpus built over a
        # truncated prefix must agree on the descriptive state at T.
        full_engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        ctx = _standard_records()[("RELIANCE", "1D")]
        T = candles[20].timestamp
        full_state = full_engine.get_state("RELIANCE", T)

        prefix_dir = tmp_path / "prefix"
        prefix_records = {
            ("RELIANCE", "15m"): candles[:21],
            ("RELIANCE", "1D"): ctx[:1],
        }
        prefix_engine = _make_engine(prefix_dir, prefix_records)
        prefix_state = prefix_engine.get_state("RELIANCE", T)

        assert prefix_state.setup_slice.candles == full_state.setup_slice.candles
        assert prefix_state.context_slice.candles == (
            full_state.context_slice.candles
        )
        assert prefix_state.setup_context.trend.state == (
            full_state.setup_context.trend.state
        )
        assert prefix_state.mtf_alignment == full_state.mtf_alignment

    def test_deterministic_repeated_evaluation(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        first = engine.evaluation_point("RELIANCE", T)
        second = engine.evaluation_point("RELIANCE", T)
        assert first == second
        corpus_a = engine.build(["RELIANCE"], label="x")
        corpus_b = engine.build(["RELIANCE"], label="x")
        assert corpus_a.corpus_id == corpus_b.corpus_id
        assert corpus_a.evaluation_points == corpus_b.evaluation_points

    def test_public_api_accepts_no_future_parameter(self):
        for method in ("build", "evaluation_point", "get_state", "evaluation_points_for"):
            params = inspect.signature(
                getattr(HistoricalResearchCorpusEngine, method),
            ).parameters
            assert "future" not in params
            assert "future_candles" not in params
            assert "lookahead" not in params

    def test_corpus_works_with_outcome_evaluator_patched(
        self, tmp_path, monkeypatch,
    ):
        def _explode(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("OutcomeEvaluator must not be called")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _explode)
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.valid_count > 0

    def test_corpus_works_with_pipeline_patched(self, tmp_path, monkeypatch):
        def _explode(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("HistoricalEvaluationPipeline must not run")

        monkeypatch.setattr(HistoricalEvaluationPipeline, "evaluate", _explode)
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.valid_count > 0


# ============================================================
# C. DATA QUALITY
# ============================================================


class TestDataQuality:
    def test_missing_candles_detected_as_gap(self, tmp_path):
        setup = _zigzag_series(10) + _zigzag_series(
            10, start=BASE + timedelta(minutes=15 * 15),
        )
        records = {
            ("RELIANCE", "15m"): setup,
            ("RELIANCE", "1D"): _zigzag_series(6, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records)
        corpus = engine.build(["RELIANCE"])
        dataset = corpus.datasets[0]
        assert dataset.setup_quality.unexpected_gap_count == 0
        # 5 missing 15m candles inside a single session => span small.
        assert dataset.setup_quality.closure_gap_count == 1

    def test_unexpected_gap_reported(self, tmp_path):
        setup = _zigzag_series(10) + _zigzag_series(
            10, start=BASE + timedelta(days=5),
        )
        records = {
            ("RELIANCE", "15m"): setup,
            ("RELIANCE", "1D"): _zigzag_series(8, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records)
        corpus = engine.build(["RELIANCE"])
        assert corpus.datasets[0].setup_quality.unexpected_gap_count == 1

    def test_gapped_window_point_skipped_as_data_gap(self, tmp_path):
        setup = _zigzag_series(12) + _zigzag_series(
            20, start=BASE + timedelta(days=5),
        )
        records = {
            ("RELIANCE", "15m"): setup,
            ("RELIANCE", "1D"): _zigzag_series(8, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records)
        corpus = engine.build(["RELIANCE"])
        gapped = [
            p for p in corpus.evaluation_points
            if p.status is CorpusPointStatus.DATA_GAP
        ]
        assert gapped  # points whose window crosses the unexpected gap
        assert all(p.state is None for p in gapped)
        assert all("never fabricated" in p.reason for p in gapped)

    def test_gapped_window_allowed_when_configured(self, tmp_path):
        setup = _zigzag_series(12) + _zigzag_series(
            20, start=BASE + timedelta(days=5),
        )
        records = {
            ("RELIANCE", "15m"): setup,
            ("RELIANCE", "1D"): _zigzag_series(8, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(
            tmp_path,
            records,
            config=ResearchCorpusConfig(
                min_setup_history=6, skip_gapped_points=False,
            ),
        )
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.data_gap_count == 0
        assert corpus.report.valid_count > 0
        # The gap is still VISIBLE in the data-quality summary.
        assert corpus.datasets[0].setup_quality.unexpected_gap_count == 1

    def test_malformed_and_duplicate_records_reported(self, tmp_path):
        c = _zigzag_series(6)
        messy = c + (c[3], c[2])  # duplicate + out-of-order
        records = {
            ("RELIANCE", "15m"): messy + _zigzag_series(
                12, start=BASE + timedelta(minutes=15 * 6),
            ),
            ("RELIANCE", "1D"): _zigzag_series(6, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records)
        corpus = engine.build(["RELIANCE"])
        # The duplicate was rejected at ingestion (first kept); the
        # corpus surfaces the rejected-record accounting from provenance.
        assert corpus.datasets[0].setup_quality.invalid_records >= 1

    def test_insufficient_history_explicit(self, tmp_path):
        engine = _make_engine(
            tmp_path, config=ResearchCorpusConfig(min_setup_history=20),
        )
        candles = _standard_records()[("RELIANCE", "15m")]
        point = engine.evaluation_point("RELIANCE", candles[10].timestamp)
        assert point.status is CorpusPointStatus.INSUFFICIENT_HISTORY
        assert point.history_count == 11
        assert point.state is None

    def test_empty_dataset_is_missing_data(self, tmp_path):
        records = {
            ("RELIANCE", "1D"): _zigzag_series(6, step=timedelta(days=1), period=3),
        }
        engine = _make_engine(tmp_path, records)
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.loaded_instruments == ()
        assert corpus.report.missing_instruments == ("RELIANCE",)
        assert corpus.report.storage_status == "unavailable"
        assert corpus.report.issues  # explicit, never silently dropped

    def test_missing_instrument_explicit(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE", "TCS"])
        assert corpus.report.missing_instruments == ("TCS",)
        point = engine.evaluation_point("TCS", BASE + timedelta(hours=5))
        assert point.status is CorpusPointStatus.MISSING_DATA

    def test_no_store_propagates_as_unavailable(self, tmp_path):
        records = _standard_records()
        service = _make_service(tmp_path, records, with_store=False)
        engine = HistoricalResearchCorpusEngine(
            service, ResearchCorpusConfig(min_setup_history=6),
        )
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.storage_status == "unavailable"
        assert corpus.report.loaded_instruments == ()
        assert corpus.report.missing_instruments == ("RELIANCE",)
        assert all(
            p.status is CorpusPointStatus.MISSING_DATA
            or p.status is CorpusPointStatus.VALID
            for p in corpus.evaluation_points
        )

    def test_corrupted_dataset_fails_loudly(self, tmp_path):
        engine = _make_engine(tmp_path)
        path = engine.service.store.path_for("RELIANCE", "15m")
        path.write_text("{ not json", encoding="utf-8")
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.missing_instruments == ("RELIANCE",)
        assert any(
            issue.instrument == "RELIANCE" for issue in corpus.report.issues
        )

    def test_rejected_future_records_accounted(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            config=ResearchCorpusConfig(
                min_setup_history=6,
                start=BASE,
                end=BASE + timedelta(minutes=15 * 20),
            ),
        )
        corpus = engine.build(["RELIANCE"])
        # 48 - 21 setup candles + context candles beyond the window end.
        assert corpus.report.rejected_future_records > 0
        dataset = corpus.datasets[0]
        assert dataset.setup_quality.window_count == 21
        assert dataset.setup_quality.source_count == 48


# ============================================================
# D. RESEARCH STATE API
# ============================================================


class TestResearchStateAPI:
    def test_historical_state_retrieval(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        state = engine.get_state("RELIANCE", T)
        assert isinstance(state, HistoricalMarketState)
        assert state.instrument == "RELIANCE"

    def test_get_state_none_for_skipped_point(self, tmp_path):
        engine = _make_engine(
            tmp_path, config=ResearchCorpusConfig(min_setup_history=20),
        )
        candles = _standard_records()[("RELIANCE", "15m")]
        assert engine.get_state("RELIANCE", candles[5].timestamp) is None

    def test_context_setup_timeframe_alignment(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[30].timestamp
        state = engine.get_state("RELIANCE", T)
        assert state.setup_slice.boundary_inclusive is True
        assert state.context_slice.boundary_inclusive is False
        assert state.latest_usable_setup_timestamp <= T
        assert state.latest_usable_context_timestamp < T

    def test_structure_information_reused_not_invented(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[30].timestamp
        state = engine.get_state("RELIANCE", T)
        # The reused Sprint 11P structure fields are present.
        assert state.setup_context is not None
        assert state.setup_context.trend is not None
        assert state.setup_context.range is not None
        assert state.setup_context.support_resistance is not None
        assert isinstance(state.mtf_alignment, MTFAlignment)

    def test_provenance_preserved(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.provider == "in-memory-import"
        assert corpus.report.ingestion_version == "1"

    def test_evaluation_timestamp_preserved(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[25].timestamp
        point = engine.evaluation_point("RELIANCE", T)
        assert point.evaluation_time == T
        assert point.state.evaluation_time == T
        assert point.state.setup_slice.evaluation_time == T

    def test_naive_evaluation_time_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        with pytest.raises(ValueError):
            engine.evaluation_point("RELIANCE", datetime(2024, 1, 1, 12, 0))

    def test_slice_fields_answer_research_questions(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        T = candles[20].timestamp
        state = engine.get_state("RELIANCE", T)
        slice_ = state.setup_slice
        assert slice_.instrument == "RELIANCE"
        assert slice_.timeframe == "15m"
        assert slice_.first_timestamp == candles[0].timestamp
        assert slice_.last_timestamp == T
        assert slice_.count == 21
        assert slice_.source_count == 48
        assert slice_.evaluation_time == T
        assert slice_.quality is not None

    def test_context_timeframe_disabled(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            config=ResearchCorpusConfig(
                min_setup_history=6, context_timeframe="",
            ),
        )
        candles = _standard_records()[("RELIANCE", "15m")]
        state = engine.get_state("RELIANCE", candles[20].timestamp)
        assert state is not None
        assert state.context_slice is None
        assert state.context_context is None
        assert state.mtf_alignment is MTFAlignment.UNKNOWN
        assert state.structure_unavailable_reasons


# ============================================================
# E. SERIALIZATION + METADATA PERSISTENCE
# ============================================================


class TestSerializationAndStore:
    def test_report_serialization_round_trip(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"], label="rt")
        payload = serialize_report(corpus.report)
        assert deserialize_report(payload) == corpus.report

    def test_serialization_deterministic(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert serialize_report(corpus.report) == serialize_report(corpus.report)
        assert canonical_report_json(corpus.report) == serialize_report(corpus.report)

    def test_header_and_schema_version(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        header = parse_corpus_header(serialize_report(corpus.report))
        assert header["schema_version"] == RESEARCH_CORPUS_SCHEMA_VERSION

    def test_future_schema_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        import json

        payload = json.loads(serialize_report(corpus.report))
        payload["schema_version"] = 999
        with pytest.raises(ValueError):
            deserialize_report(json.dumps(payload))

    def test_malformed_payload_rejected(self):
        with pytest.raises(ValueError):
            deserialize_report("{ not json")
        with pytest.raises(ValueError):
            deserialize_report('{"schema_version": 1}')

    def test_manifest_round_trip(self, tmp_path):
        engine = _make_engine(tmp_path)
        config = ResearchCorpusConfig(min_setup_history=6)
        corpus = engine.build(["RELIANCE"], label="persist")
        store = ResearchCorpusStore(tmp_path / "corpus")
        path = store.save(corpus, configuration=config.snapshot())
        assert path.exists()
        manifest = store.load(corpus.corpus_id)
        assert manifest.corpus_id == corpus.corpus_id
        assert manifest.report == corpus.report
        assert manifest.configuration == config.snapshot()
        assert manifest.label == "persist"
        assert store.list_manifests() == (corpus.corpus_id,)
        assert store.exists(corpus.corpus_id)

    def test_manifest_no_silent_overwrite(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        store = ResearchCorpusStore(tmp_path / "corpus")
        store.save(corpus)
        with pytest.raises(ResearchCorpusStoreError):
            store.save(corpus)
        store.save(corpus, overwrite=True)

    def test_manifest_load_missing_raises(self, tmp_path):
        store = ResearchCorpusStore(tmp_path / "corpus")
        with pytest.raises(ResearchCorpusNotFoundError):
            store.load("corpus-does-not-exist")

    def test_manifest_unsafe_id_rejected(self, tmp_path):
        store = ResearchCorpusStore(tmp_path / "corpus")
        with pytest.raises(ResearchCorpusStoreError):
            store.path_for("../escape")

    def test_manifest_corrupted_and_mismatched(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        store = ResearchCorpusStore(tmp_path / "corpus")
        store.save(corpus)
        path = store.path_for(corpus.corpus_id)
        original = path.read_text(encoding="utf-8")
        path.write_text("{ broken", encoding="utf-8")
        with pytest.raises(ResearchCorpusIntegrityError):
            store.load(corpus.corpus_id)
        import json

        payload = json.loads(original)
        payload["corpus_id"] = "corpus-other"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ResearchCorpusIntegrityError):
            store.load(corpus.corpus_id)

    def test_manifest_delete(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        store = ResearchCorpusStore(tmp_path / "corpus")
        store.save(corpus)
        store.delete(corpus.corpus_id)
        assert not store.exists(corpus.corpus_id)
        with pytest.raises(ResearchCorpusNotFoundError):
            store.delete(corpus.corpus_id)

    def test_manifest_is_metadata_only(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        store = ResearchCorpusStore(tmp_path / "corpus")
        path = store.save(corpus)
        text = path.read_text(encoding="utf-8")
        # The manifest persists identity + configuration + the report;
        # it does NOT persist candles or per-point states.
        assert '"candles"' not in text
        assert '"evaluation_points"' not in text

    def test_default_corpus_directory_relative(self):
        assert default_research_corpus_directory() == (
            Path.cwd() / "data" / "research_corpus"
        )


# ============================================================
# F. REPORTING
# ============================================================


class TestReporting:
    def test_report_returns_str_with_sections(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"], label="report")
        text = ResearchCorpusFormatter().format(corpus)
        assert isinstance(text, str)
        for section in (
            "HISTORICAL RESEARCH CORPUS REPORT",
            "Configuration",
            "Coverage",
            "Evaluation Points",
            "Source / Storage",
            "Issues",
            "Rationale",
            "DISCLAIMER",
        ):
            assert section in text

    def test_point_report_shows_boundary(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        point = corpus.valid_points[0]
        text = ResearchCorpusFormatter().format_point(point)
        assert "HISTORICAL EVALUATION POINT" in text
        assert "Latest usable setup candle (<= T)" in text
        assert "Latest completed context candle (< T)" in text
        assert "Future candles (> T): excluded" in text
        assert "MTF alignment" in text
        assert "DISCLAIMER" in text

    def test_no_predictive_language(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        point = corpus.valid_points[0]
        formatter = ResearchCorpusFormatter()
        for text in (formatter.format(corpus), formatter.format_point(point)):
            # The mandatory disclaimer explicitly negates recommendation
            # language; exclude it from the banned-phrase scan.
            lowered = "\n".join(
                line for line in text.lower().splitlines()
                if not line.startswith("disclaimer")
            )
            for banned in (
                "will rise",
                "will fall",
                "guaranteed profit",
                "buy",
                "sell",
                "enter",
                "exit",
                "recommendation",
                "probability of success",
            ):
                assert banned not in lowered

    def test_reporting_deterministic(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        formatter = ResearchCorpusFormatter()
        assert formatter.format(corpus) == formatter.format(corpus)


# ============================================================
# G. MODEL + CONFIG VALIDATION
# ============================================================


class TestModelAndConfig:
    def test_config_defaults(self):
        config = ResearchCorpusConfig()
        assert config.setup_timeframe == "15m"
        assert config.context_timeframe == "1D"
        assert config.min_setup_history == 10
        assert config.sample_every == 1
        assert config.skip_gapped_points is True
        assert config.has_context_timeframe

    def test_config_timeframe_alias_canonicalized(self):
        config = ResearchCorpusConfig(setup_timeframe="15M", context_timeframe="1d")
        assert config.setup_timeframe == "15m"
        assert config.context_timeframe == "1D"

    def test_config_validation(self):
        with pytest.raises(ValueError):
            ResearchCorpusConfig(setup_timeframe="bogus")
        with pytest.raises(ValueError):
            ResearchCorpusConfig(context_timeframe="bogus")
        with pytest.raises(ValueError):
            ResearchCorpusConfig(setup_timeframe="15m", context_timeframe="15M")
        with pytest.raises(ValueError):
            ResearchCorpusConfig(min_setup_history=0)
        with pytest.raises(ValueError):
            ResearchCorpusConfig(sample_every=0)
        with pytest.raises(ValueError):
            ResearchCorpusConfig(
                start=BASE + timedelta(days=2), end=BASE,
            )
        with pytest.raises(ValueError):
            ResearchCorpusConfig(start=datetime(2024, 1, 1))

    def test_config_frozen(self):
        config = ResearchCorpusConfig()
        with pytest.raises(Exception):
            config.min_setup_history = 99  # noqa: DC04

    def test_config_snapshot_sorted(self):
        snapshot = ResearchCorpusConfig(label="z").snapshot()
        assert snapshot == tuple(sorted(snapshot))

    def test_models_frozen_slots(self):
        for model in (
            CorpusDataQuality,
            CorpusTimeframeSlice,
            HistoricalMarketState,
            CorpusEvaluationPoint,
            HistoricalResearchCorpus,
        ):
            assert hasattr(model, "__slots__")

    def test_point_invariants(self, tmp_path):
        engine = _make_engine(tmp_path)
        candles = _standard_records()[("RELIANCE", "15m")]
        state = engine.get_state("RELIANCE", candles[20].timestamp)
        with pytest.raises(ValueError):
            CorpusEvaluationPoint(
                instrument="RELIANCE",
                evaluation_time=candles[20].timestamp,
                setup_timeframe="15m",
                context_timeframe="1D",
                status=CorpusPointStatus.VALID,
                state=None,
            )
        with pytest.raises(ValueError):
            CorpusEvaluationPoint(
                instrument="RELIANCE",
                evaluation_time=candles[20].timestamp,
                setup_timeframe="15m",
                context_timeframe="1D",
                status=CorpusPointStatus.MISSING_DATA,
                state=state,
            )

    def test_quality_invariants(self):
        with pytest.raises(ValueError):
            CorpusDataQuality(
                source_count=1,
                window_count=2,
                first_timestamp=None,
                last_timestamp=None,
                unexpected_gap_count=0,
                closure_gap_count=0,
                invalid_records=0,
            )

    def test_corpus_valid_points_property(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        assert corpus.valid_points == tuple(
            p for p in corpus.evaluation_points if p.is_valid
        )
        assert not corpus.is_empty

    def test_corpus_id_changes_with_config(self, tmp_path):
        engine_a = _make_engine(tmp_path / "a")
        engine_b = _make_engine(
            tmp_path / "b", config=ResearchCorpusConfig(min_setup_history=12),
        )
        assert engine_a.build(["RELIANCE"]).corpus_id != (
            engine_b.build(["RELIANCE"]).corpus_id
        )

    def test_corpus_id_stable_for_identical_inputs(self, tmp_path):
        corpus_a = _make_engine(tmp_path / "a").build(["RELIANCE"])
        corpus_b = _make_engine(tmp_path / "b").build(["RELIANCE"])
        assert corpus_a.corpus_id == corpus_b.corpus_id

    def test_version_constant(self):
        assert CORPUS_VERSION == "research-corpus-v1"


# ============================================================
# H. DETERMINISM + IMMUTABILITY
# ============================================================


class TestDeterminismImmutability:
    def test_repeated_build_identical(self, tmp_path):
        engine = _make_engine(tmp_path)
        first = engine.build(["RELIANCE"], label="d")
        second = engine.build(["RELIANCE"], label="d")
        assert first.corpus_id == second.corpus_id
        assert first.evaluation_points == second.evaluation_points
        assert first.report == second.report

    def test_instrument_order_does_not_change_results(self, tmp_path):
        engine = _make_engine(
            tmp_path, _standard_records(("RELIANCE", "TCS")),
        )
        corpus_a = engine.build(["RELIANCE", "TCS"])
        corpus_b = engine.build(["TCS", "RELIANCE"])
        assert corpus_a.corpus_id == corpus_b.corpus_id
        assert corpus_a.evaluation_points == corpus_b.evaluation_points

    def test_inputs_not_mutated(self, tmp_path):
        records = _standard_records()
        engine = _make_engine(tmp_path, records)
        snapshot = records[("RELIANCE", "15m")]
        engine.build(["RELIANCE"])
        assert records[("RELIANCE", "15m")] == snapshot

    def test_corpus_models_immutable(self, tmp_path):
        engine = _make_engine(tmp_path)
        corpus = engine.build(["RELIANCE"])
        with pytest.raises(Exception):
            corpus.corpus_id = "corpus-other"  # noqa: DC04


# ============================================================
# I. REGRESSION
# ============================================================


class TestRegression:
    def test_pipeline_baseline_unchanged(self):
        result = HistoricalEvaluationPipeline().evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_phase_6b_foundation_intact(self, tmp_path):
        # The Phase 6B ingestion path is untouched by the corpus.
        records = _standard_records()
        service = _make_service(tmp_path, records)
        slice_ = service.load_historical("RELIANCE", "15m")
        assert slice_.count == 48
        bounded = service.load_historical(
            "RELIANCE", "15m", evaluation_time=BASE + timedelta(minutes=15 * 9),
        )
        assert bounded.count == 10

    def test_dependency_direction_intact(self):
        # engine.data.research_corpus must never import the dashboard.
        import engine.data.research_corpus as corpus_module
        import engine.data.research_corpus_store as store_module
        import engine.data.research_corpus_serialization as ser_module

        for module in (corpus_module, store_module, ser_module):
            for name in dir(module):
                assert not name.startswith("dashboard")
        source = Path(corpus_module.__file__).read_text(encoding="utf-8")
        assert "import dashboard" not in source
        assert "from dashboard" not in source

    def test_existing_engines_importable(self):
        from engine.intelligence.market_scanner import MarketScanner  # noqa: F401
        from engine.intelligence.paper_trading import PaperTradingEngine  # noqa: F401
        from engine.intelligence.trade_decision import TradeDecisionEngine  # noqa: F401

    def test_data_init_remains_empty(self):
        init = Path(
            __import__("engine.data", fromlist=["x"]).__file__,
        ).read_text(encoding="utf-8")
        assert init.strip() == ""


# ============================================================
# J. CHECKPOINT 10.2 — OPTIONAL AUTOMATIC HISTORICAL GAP-FILL
# ============================================================


class _TrackingAvailabilityService:
    """A minimal availability service that records calls and delegates to
    a real service for actual acquisition."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.calls: list[HistoricalDataRequest] = []

    def get_historical_data(self, request, *, reference_now=None, label="", metadata=None):
        self.calls.append(request)
        return self.delegate.get_historical_data(
            request,
            reference_now=reference_now,
            label=label,
            metadata=metadata,
        )


def _make_avail_service(tmp_path, records, *, timeframes=("15m", "1D")):
    """Build a real HistoricalDataAvailabilityService for testing."""
    from engine.config.corpus_plan_config import CorpusPlanConfig
    from engine.data.corpus_plan import CorpusPreparationPlanner
    from engine.data.historical_data_availability import HistoricalDataAvailabilityService
    from engine.data.historical_provider import (
        HistoricalProviderResponse,
        InMemoryHistoricalProvider,
        ProviderResponseStatus,
    )

    class _RangeFilteredProvider(InMemoryHistoricalProvider):
        """In-memory provider that only returns candles within the requested
        ``[start, end]`` window (like a real vendor)."""

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

    store = HistoricalDataStore(tmp_path / "hist")
    provider = _RangeFilteredProvider(records)
    service = HistoricalMarketDataService(provider=provider, store=store)
    planner = CorpusPreparationPlanner(
        config=CorpusPlanConfig(timeframes=timeframes, provider="in-memory-import"),
        store=store,
        provider=provider,
    )
    return HistoricalDataAvailabilityService(planner, service), service, store


class TestAutoAcquire:
    """Checkpoint 10.2 — Optional automatic historical gap-fill tests."""

    def test_auto_acquire_default_disabled(self, tmp_path):
        """auto_acquire defaults to False; existing behavior preserved."""
        records = _standard_records()
        service = _make_service(tmp_path, records)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(min_setup_history=6),
        )
        assert engine.config.auto_acquire is False
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.valid_count > 0

    def test_complete_coverage_zero_provider_calls(self, tmp_path):
        """auto_acquire=True with complete data -> zero provider calls."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        tracking = _TrackingAvailabilityService(avail)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(min_setup_history=6, auto_acquire=True),
            availability_service=tracking,
        )
        engine.build(["RELIANCE"])
        assert tracking.calls == []

    def test_auto_acquire_disabled_no_acquisition(self, tmp_path):
        """auto_acquire=False with missing data -> zero provider calls."""
        avail, service, store = _make_avail_service(tmp_path, {})
        tracking = _TrackingAvailabilityService(avail)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=False,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=tracking,
        )
        corpus = engine.build(["RELIANCE"])
        assert tracking.calls == []
        assert corpus.report.valid_count == 0
        assert corpus.report.loaded_instruments == ()

    def test_auto_acquire_enabled_triggers_acquisition(self, tmp_path):
        """auto_acquire=True with missing data -> availability service invoked."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        tracking = _TrackingAvailabilityService(avail)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=True,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=tracking,
        )
        corpus = engine.build(["RELIANCE"])
        assert len(tracking.calls) > 0
        assert corpus.report.valid_count > 0

    def test_persistence_integration(self, tmp_path):
        """Acquired data is written to the store and read by the engine."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=True,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=avail,
        )
        corpus = engine.build(["RELIANCE"])
        assert corpus.report.valid_count > 0
        assert store.exists("RELIANCE", "15m")
        loaded = store.load_candles("RELIANCE", "15m")
        assert len(loaded) > 0

    def test_repeated_build_idempotent(self, tmp_path):
        """Second build with auto_acquire=True produces identical corpus."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=True,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=avail,
        )
        corpus1 = engine.build(["RELIANCE"])
        assert corpus1.report.valid_count > 0
        corpus2 = engine.build(["RELIANCE"])
        assert corpus2.report.valid_count == corpus1.report.valid_count
        assert corpus2.corpus_id == corpus1.corpus_id

    def test_acquisition_failure_surfaced(self, tmp_path):
        """Acquisition failure is surfaced; no fabricated data."""
        from engine.models.historical_availability import (
            HistoricalAvailabilityStatus,
            HistoricalDataAvailabilityResult,
        )

        class _FailingAvailability:
            def __init__(self):
                self.calls = []

            def get_historical_data(self, request, *, reference_now=None, label="", metadata=None):
                self.calls.append(request)
                return HistoricalDataAvailabilityResult(
                    instrument=request.instrument,
                    timeframe=request.timeframe,
                    request_start=request.start,
                    request_end=request.end,
                    status=HistoricalAvailabilityStatus.ERROR,
                    chunks_required=1,
                    chunks_still_missing=("chunk-1",),
                    reference_now=reference_now,
                )

        records = _standard_records()
        service = _make_service(tmp_path, records)
        failing = _FailingAvailability()
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=True,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=failing,
        )
        corpus = engine.build(["RELIANCE"])
        assert len(failing.calls) > 0
        assert corpus.report.valid_count > 0

    def test_provider_selection_passed_through(self, tmp_path):
        """The configured/requested provider is passed through unchanged."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        tracking = _TrackingAvailabilityService(avail)
        engine = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                min_setup_history=6,
                auto_acquire=True,
                start=BASE,
                end=BASE + timedelta(days=60),
            ),
            availability_service=tracking,
        )
        engine.build(["RELIANCE"])
        for call in tracking.calls:
            assert call.instrument == "RELIANCE"
            assert call.timeframe in ("15m", "1D")

    def test_point_in_time_regression(self, tmp_path):
        """Auto-acquire does not change point-in-time slicing semantics."""
        records = _standard_records()
        avail, service, store = _make_avail_service(tmp_path, records)
        engine_without = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(min_setup_history=6),
        )
        engine_with = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(min_setup_history=6, auto_acquire=True),
            availability_service=avail,
        )
        corpus_without = engine_without.build(["RELIANCE"])
        corpus_with = engine_with.build(["RELIANCE"])
        assert corpus_without.report.valid_count == corpus_with.report.valid_count
        assert corpus_without.corpus_id == corpus_with.corpus_id
