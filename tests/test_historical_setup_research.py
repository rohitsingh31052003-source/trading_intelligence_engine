"""
Tests for Product Phase 6D — Historical Setup Research.

Deterministic, network-free: every test uses the in-memory import
provider + a tmp_path historical store (the Phase 6B foundation) +
the Phase 6C corpus engine. Phase 6D is RESEARCH ONLY — it reuses the
existing setup detection (11O/11Q/11R/11S) and forward-only outcome
evaluation (11W) VERBATIM; the existing decision architecture remains
authoritative. Historical evidence is descriptive and observational —
never a prediction, recommendation, or guarantee.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.config.setup_research_config import SetupResearchConfig
from engine.data.historical_provider import InMemoryHistoricalProvider
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.research_corpus import HistoricalResearchCorpusEngine
from engine.data.setup_research import (
    MINIMUM_HISTORY_SKIP,
    SETUP_RESEARCH_VERSION,
    HistoricalSetupResearchEngine,
)
from engine.data.setup_research_serialization import (
    SETUP_RESEARCH_SCHEMA_VERSION,
    canonical_result_json,
    deserialize_result,
    parse_result_header,
    serialize_result,
    serialize_result_bytes,
)
from engine.data.setup_research_store import (
    SetupResearchIntegrityError,
    SetupResearchNotFoundError,
    SetupResearchStore,
    SetupResearchStoreError,
    default_setup_research_directory,
)
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.models.historical_data import HistoricalDataRequest
from engine.models.historical_outcome import OutcomeStatus
from engine.models.ohlcv import OHLCVCandle
from engine.models.setup_research import (
    SetupEvidence,
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)
from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset
from engine.reporting.setup_research import SetupResearchFormatter


BASE = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
NOW = BASE + timedelta(days=60)
STEP = timedelta(minutes=15)


# ============================================================
# FIXTURE HELPERS (deterministic, no network)
# ============================================================


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 2.0, close - 2.0, close, 1000.0)


def _hammer(ts: datetime, base_close: float, body: float = 2.0) -> OHLCVCandle:
    close = base_close + body
    return OHLCVCandle(ts, base_close, close + body, close - 3 * body, close, 1000.0)


def _trending_series(cycles: int = 6, tail: int = 20) -> tuple[OHLCVCandle, ...]:
    candles: list[OHLCVCandle] = []
    price = 100.0
    idx = 0
    for _ in range(cycles):
        for _ in range(3):
            price = round(price + 4.0, 2)
            candles.append(_candle(BASE + STEP * idx, price))
            idx += 1
        for _ in range(2):
            price = round(price - 2.0, 2)
            candles.append(_candle(BASE + STEP * idx, price))
            idx += 1
        candles.append(_hammer(BASE + STEP * idx, price))
        idx += 1
        price = round(price + 2.0, 2)
    for _ in range(tail):
        price = round(price + 0.5, 2)
        candles.append(_candle(BASE + STEP * idx, price))
        idx += 1
    return tuple(candles)


def _flat_series(n: int = 40) -> tuple[OHLCVCandle, ...]:
    return tuple(_candle(BASE + STEP * i, 100.0) for i in range(n))


def _daily_series(n: int = 10) -> tuple[OHLCVCandle, ...]:
    return tuple(_candle(BASE + timedelta(days=i), 100.0 + i) for i in range(n))


def _service(
    tmp_path: Path,
    setup: tuple[OHLCVCandle, ...] | None,
    context: tuple[OHLCVCandle, ...] | None = None,
    instrument: str = "NIFTY",
    name: str = "store",
) -> HistoricalMarketDataService:
    records = {}
    if setup:
        records[(instrument, "15m")] = setup
    if context:
        records[(instrument, "1D")] = context
    provider = InMemoryHistoricalProvider(records)
    service = HistoricalMarketDataService(
        provider=provider,
        store=HistoricalDataStore(tmp_path / name),
    )
    for (inst, timeframe), candles in records.items():
        if not candles:
            continue
        service.ingest(
            HistoricalDataRequest(
                inst,
                timeframe,
                candles[0].timestamp,
                candles[-1].timestamp + timedelta(seconds=1),
            ),
            reference_now=NOW,
        )
    return service


def _engine(
    service: HistoricalMarketDataService,
    *,
    min_setup_history: int = 5,
    config: SetupResearchConfig | None = None,
) -> HistoricalSetupResearchEngine:
    corpus = HistoricalResearchCorpusEngine(
        service,
        ResearchCorpusConfig(
            setup_timeframe="15m",
            context_timeframe="1D",
            min_setup_history=min_setup_history,
        ),
    )
    return HistoricalSetupResearchEngine(corpus, config or SetupResearchConfig())


def _request(**overrides) -> SetupResearchRequest:
    kwargs = {
        "instrument": "NIFTY",
        "minimum_history": 5,
        "forward_horizon": 10,
    }
    kwargs.update(overrides)
    return SetupResearchRequest(**kwargs)


def _result(engine: HistoricalSetupResearchEngine, **overrides) -> SetupResearchResult:
    return engine.research(_request(**overrides))


# ============================================================
# A. RESEARCH REQUEST VALIDATION
# ============================================================


class TestRequestValidation:
    def test_valid_request(self):
        request = _request()
        assert request.instrument == "NIFTY"
        assert request.forward_horizon == 10
        assert request.minimum_history == 5
        assert not request.has_filters

    def test_instrument_normalized(self):
        request = _request(instrument="  nifty ")
        assert request.instrument == "NIFTY"

    def test_empty_instrument_rejected(self):
        with pytest.raises(ValueError):
            _request(instrument=" ")

    def test_naive_start_rejected(self):
        with pytest.raises(ValueError):
            _request(start_time=datetime(2024, 1, 1))

    def test_naive_end_rejected(self):
        with pytest.raises(ValueError):
            _request(end_time=datetime(2024, 1, 2))

    def test_invalid_date_range_rejected(self):
        with pytest.raises(ValueError):
            _request(start_time=BASE + STEP, end_time=BASE)

    def test_invalid_horizon_rejected(self):
        with pytest.raises(ValueError):
            _request(forward_horizon=0)

    def test_invalid_minimum_history_rejected(self):
        with pytest.raises(ValueError):
            _request(minimum_history=0)

    def test_invalid_timeframe_status(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = engine.research(_request(setup_timeframe="12x"))
        assert result.status is SetupResearchStatus.INVALID_REQUEST

    def test_timeframe_mismatch_status(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = engine.research(_request(setup_timeframe="1D", context_timeframe="15m"))
        assert result.status is SetupResearchStatus.INVALID_REQUEST

    def test_context_disabled_vs_corpus_status(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = engine.research(_request(context_timeframe=""))
        assert result.status is SetupResearchStatus.INVALID_REQUEST

    def test_unsupported_instrument(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = engine.research(_request(instrument="UNKNOWN"))
        assert result.status is SetupResearchStatus.CORPUS_UNAVAILABLE

    def test_filter_flags(self):
        request = _request(direction_filter="LONG", trend_filter="BULLISH")
        assert request.has_filters

    def test_metadata_sorted(self):
        request = _request(metadata=(("b", "1"), ("a", "2")))
        assert request.metadata == (("a", "2"), ("b", "1"))


# ============================================================
# B. OCCURRENCE DETECTION
# ============================================================


class TestOccurrenceDetection:
    def test_occurrence_detected(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert result.status is SetupResearchStatus.RESEARCHED
        assert result.occurrence_count >= 2
        assert all(
            o.occurrence.setup_classification == "POTENTIAL_SETUP"
            for o in result.observations
        )

    def test_no_occurrence(self, tmp_path):
        engine = _engine(_service(tmp_path, _flat_series(), _daily_series()))
        result = _result(engine)
        assert result.status is SetupResearchStatus.NO_OCCURRENCES
        assert result.occurrence_count == 0
        assert result.evidence is None

    def test_multiple_occurrences_deterministic_order(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        detected = engine.detect(_request())
        assert len(detected) >= 2
        times = [o.evaluation_time for o in detected]
        assert times == sorted(times)

    def test_repeated_detection_identical(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        first = engine.detect(_request())
        second = engine.detect(_request())
        assert [o.evaluation_time for o in first] == [o.evaluation_time for o in second]

    def test_watch_not_an_occurrence_by_default(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        detected = engine.detect(_request())
        assert all(o.setup_classification == "POTENTIAL_SETUP" for o in detected)

    def test_watch_occurrence_when_configured(self, tmp_path):
        engine = _engine(
            _service(tmp_path, _trending_series(), _daily_series()),
            config=SetupResearchConfig(
                occurrence_classifications=("WATCH", "POTENTIAL_SETUP"),
            ),
        )
        detected = engine.detect(_request())
        assert any(o.setup_classification == "WATCH" for o in detected)

    def test_existing_decision_classification_preserved(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        classifications = {o.occurrence.decision_classification for o in result.observations}
        assert classifications <= {"REJECTED", "WATCH", "QUALIFIED", "PREFERRED"}
        # A geometry-incomplete candidate must NOT be silently promoted.
        for observation in result.observations:
            if not observation.occurrence.geometry_available:
                assert observation.occurrence.decision_classification != "PREFERRED"


# ============================================================
# C. POINT-IN-TIME SAFETY
# ============================================================


class TestPointInTimeSafety:
    def test_setup_uses_only_data_up_to_t(self, tmp_path):
        setup = _trending_series()
        engine = _engine(_service(tmp_path, setup, _daily_series()))
        full = engine.detect(_request())
        # Truncate detection window to the first two occurrences.
        cutoff = full[1].evaluation_time + STEP
        truncated = engine.detect(_request(end_time=cutoff))
        assert [o.evaluation_time for o in truncated] == [
            o.evaluation_time for o in full if o.evaluation_time < cutoff
        ]

    def test_future_candle_changes_outcome_not_detection(self, tmp_path):
        setup = _trending_series()
        context = _daily_series()
        engine = _engine(_service(tmp_path, setup, context))
        request = _request()
        base = engine.research(request)
        occurrence = next(
            o for o in base.observations if o.occurrence.geometry_available
        )
        idx = next(
            i for i, c in enumerate(setup) if c.timestamp == occurrence.evaluation_time
        )
        mutated = list(setup)
        victim = mutated[idx + 1]
        mutated[idx + 1] = OHLCVCandle(
            victim.timestamp,
            occurrence.occurrence.stop - 5,
            occurrence.occurrence.stop - 5,
            occurrence.occurrence.stop - 10,
            occurrence.occurrence.stop - 5,
            victim.volume,
        )
        mutated_engine = _engine(
            _service(tmp_path, tuple(mutated), context, name="mutated"),
        )
        mutated_result = mutated_engine.research(request)
        # Detection AT T is identical (a future candle cannot change
        # whether the setup existed at T).
        base_at_t = next(
            o for o in base.observations
            if o.evaluation_time == occurrence.evaluation_time
        )
        mutated_at_t = next(
            o for o in mutated_result.observations
            if o.evaluation_time == occurrence.evaluation_time
        )
        assert (
            base_at_t.occurrence.setup_classification
            == mutated_at_t.occurrence.setup_classification
        )
        assert base_at_t.occurrence.direction == mutated_at_t.occurrence.direction
        assert base_at_t.occurrence.entry == mutated_at_t.occurrence.entry
        assert base_at_t.occurrence.stop == mutated_at_t.occurrence.stop
        assert base_at_t.occurrence.target == mutated_at_t.occurrence.target
        # The outcome may legitimately change (the mutation is inside
        # the forward horizon).
        assert base_at_t.outcome_status is OutcomeStatus.TARGET_HIT
        assert mutated_at_t.outcome_status is OutcomeStatus.STOP_HIT

    def test_mutation_beyond_horizon_unchanged(self, tmp_path):
        setup = _trending_series()
        context = _daily_series()
        engine = _engine(_service(tmp_path, setup, context))
        request = _request()
        base = engine.research(request)
        last = base.observations[-1]
        idx = next(
            i
            for i, c in enumerate(setup)
            if c.timestamp == last.evaluation_time
        )
        beyond = idx + request.forward_horizon + 3
        assert beyond < len(setup)
        mutated = list(setup)
        victim = mutated[beyond]
        mutated[beyond] = OHLCVCandle(
            victim.timestamp,
            victim.open,
            victim.high * 10,
            victim.low / 10,
            victim.close / 2,
            victim.volume,
        )
        mutated_engine = _engine(
            _service(tmp_path, tuple(mutated), context, name="mutated"),
        )
        mutated_result = mutated_engine.research(request)
        assert [
            (o.evaluation_time, o.outcome_status, o.outcome.realized_r)
            for o in base.observations
        ] == [
            (o.evaluation_time, o.outcome_status, o.outcome.realized_r)
            for o in mutated_result.observations
        ]

    def test_context_never_consumes_at_or_after_t(self, tmp_path):
        setup = _trending_series()
        context = _daily_series()
        engine = _engine(_service(tmp_path, setup, context))
        request = _request()
        base = engine.detect(request)
        # Mutate the context candle that sits AT each occurrence time: it
        # must never be consumed (context is strictly < T).
        mutated_context = list(context)
        for occurrence in base:
            for i, candle in enumerate(mutated_context):
                if candle.timestamp >= occurrence.evaluation_time:
                    mutated_context[i] = OHLCVCandle(
                        candle.timestamp,
                        candle.open + 50,
                        candle.high + 50,
                        candle.low + 50,
                        candle.close + 50,
                        candle.volume,
                    )
                    break
        mutated_engine = _engine(
            _service(tmp_path, setup, tuple(mutated_context)),
        )
        assert [o.evaluation_time for o in engine.detect(request)] == [
            o.evaluation_time for o in mutated_engine.detect(request)
        ]

    def test_no_forbidden_api_parameters(self):
        for method in ("research", "detect"):
            signature = inspect.signature(
                getattr(HistoricalSetupResearchEngine, method),
            )
            forbidden = {"future", "future_candles", "lookahead", "future_window"}
            assert not (
                set(signature.parameters) & forbidden
            ), f"{method} must not accept a future/lookahead parameter"

    def test_detection_has_no_outcome_dependency(self, tmp_path, monkeypatch):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        monkeypatch.setattr(
            OutcomeEvaluator,
            "evaluate",
            lambda self, subject, future_candles: (_ for _ in ()).throw(
                RuntimeError("outcome evaluator must not be called"),
            ),
        )
        assert engine.detect(_request())

    def test_detection_has_no_pipeline_dependency(self, tmp_path, monkeypatch):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        monkeypatch.setattr(
            HistoricalEvaluationPipeline,
            "evaluate",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("pipeline must not be called"),
            ),
        )
        assert engine.detect(_request())


# ============================================================
# D. OUTCOME CALCULATION
# ============================================================


class TestOutcomeCalculation:
    def test_favorable_outcome_target_hit(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        hits = [
            o for o in result.observations
            if o.outcome_status is OutcomeStatus.TARGET_HIT
        ]
        assert hits
        assert hits[0].outcome.exit_price == hits[0].occurrence.target
        assert hits[0].outcome.realized_r is not None

    def test_adverse_outcome_stop_hit(self, tmp_path):
        request = _request()
        setup, occurrence = self._first_geometry(tmp_path, request)
        mutated = list(setup)
        idx = next(i for i, c in enumerate(setup) if c.timestamp == occurrence.evaluation_time)
        stop = occurrence.occurrence.stop or 0.0
        victim = mutated[idx + 1]
        mutated[idx + 1] = OHLCVCandle(
            victim.timestamp, stop - 5, stop - 5, stop - 10, stop - 5,
            victim.volume,
        )
        engine = _engine(
            _service(tmp_path, tuple(mutated), _daily_series(), name="mutated"),
        )
        result = engine.research(request)
        outcome = next(
            o for o in result.observations
            if o.evaluation_time == occurrence.evaluation_time
        )
        assert outcome.outcome_status is OutcomeStatus.STOP_HIT
        assert outcome.outcome.exit_price == outcome.occurrence.stop

    def test_unresolved_horizon_insufficient_data(self, tmp_path):
        engine = _engine(
            _service(tmp_path, _trending_series(cycles=6, tail=0), _daily_series()),
        )
        result = _result(engine)
        assert any(
            o.outcome_status is OutcomeStatus.INSUFFICIENT_DATA
            for o in result.observations
        )

    def test_expired_when_levels_untouched(self, tmp_path):
        request = _request()
        setup, occurrence = self._first_geometry(tmp_path, request)
        idx = next(
            i
            for i, c in enumerate(setup)
            if c.timestamp == occurrence.evaluation_time
        )
        entry = occurrence.occurrence.entry or 0.0
        mutated = list(setup)
        for j in range(idx + 1, min(idx + 1 + request.forward_horizon, len(setup))):
            victim = mutated[j]
            mutated[j] = OHLCVCandle(
                victim.timestamp, entry, entry + 0.5, entry - 0.5, entry,
                victim.volume,
            )
        engine = _engine(
            _service(tmp_path, tuple(mutated), _daily_series(), name="mutated"),
        )
        result = engine.research(request)
        outcome = next(
            o for o in result.observations
            if o.evaluation_time == occurrence.evaluation_time
        )
        assert outcome.outcome_status is OutcomeStatus.EXPIRED
        assert outcome.outcome.exit_price is not None
        assert outcome.outcome.realized_r is not None

    def test_ambiguous_same_candle_both_touched(self, tmp_path):
        request = _request()
        setup, occurrence = self._first_geometry(tmp_path, request)
        mutated = list(setup)
        idx = next(i for i, c in enumerate(setup) if c.timestamp == occurrence.evaluation_time)
        stop, target = occurrence.occurrence.stop, occurrence.occurrence.target
        victim = mutated[idx + 1]
        mutated[idx + 1] = OHLCVCandle(
            victim.timestamp, victim.open, (target or 0) + 50, (stop or 0) - 50,
            victim.close, victim.volume,
        )
        engine = _engine(
            _service(tmp_path, tuple(mutated), _daily_series(), name="mutated"),
        )
        result = engine.research(request)
        outcome = next(
            o for o in result.observations
            if o.evaluation_time == occurrence.evaluation_time
        )
        assert outcome.outcome_status is OutcomeStatus.BOTH_TOUCHED
        assert outcome.outcome.realized_r is None
        assert outcome.outcome.exit_price is None

    def test_missing_geometry_explicit(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        no_geometry = [
            o for o in result.observations
            if o.outcome_status is OutcomeStatus.NO_GEOMETRY
        ]
        assert no_geometry
        for observation in no_geometry:
            assert not observation.occurrence.geometry_available

    def test_mfe_mae_present(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        hits = [
            o for o in result.observations
            if o.outcome_status is OutcomeStatus.TARGET_HIT
        ]
        assert all(o.outcome.mfe is not None and o.outcome.mae is not None for o in hits)
        assert all(o.outcome.mfe_r is not None for o in hits)

    def test_risk_computed(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        for observation in result.observations:
            if observation.occurrence.geometry_available:
                expected = abs(
                    observation.occurrence.entry - observation.occurrence.stop
                )
                assert observation.outcome.risk == pytest.approx(expected)

    def _first_geometry(
        self, tmp_path, request: SetupResearchRequest,
    ) -> tuple[tuple[OHLCVCandle, ...], SetupResearchObservation]:
        setup = _trending_series()
        engine = _engine(_service(tmp_path, setup, _daily_series()))
        result = engine.research(request)
        occurrence = next(
            o for o in result.observations if o.occurrence.geometry_available
        )
        return setup, occurrence


# ============================================================
# E. AGGREGATION
# ============================================================


class TestAggregation:
    def test_sample_size_matches(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert result.evidence is not None
        assert result.evidence.sample_size == result.occurrence_count

    def test_count_invariants(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        evidence = result.evidence
        assert evidence is not None
        assert (
            evidence.completed_outcomes
            + evidence.ambiguous_count
            + evidence.unresolved_count
            + evidence.no_geometry_count
        ) == evidence.occurrence_count
        assert (
            result.completed_outcomes
            + result.ambiguous_count
            + result.unresolved_count
        ) <= result.occurrence_count

    def test_averages_and_median_computed(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        evidence = result.evidence
        assert evidence is not None
        values = [
            o.outcome.realized_r
            for o in result.observations
            if o.outcome.realized_r is not None
        ]
        if values:
            expected_avg = sum(values) / len(values)
            assert evidence.average_realized_r == pytest.approx(expected_avg)
            assert evidence.median_realized_r is not None

    def test_insufficient_sample_marked(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        insufficient = [
            g for g in result.grouped_evidence if g.strength.name == "INSUFFICIENT"
        ]
        assert insufficient
        config = SetupResearchConfig()
        assert all(g.sample_size < config.min_sample_total for g in insufficient)

    def test_unavailable_evidence_when_no_observations(self, tmp_path):
        engine = _engine(_service(tmp_path, _flat_series(), _daily_series()))
        result = _result(engine)
        assert result.evidence is None
        assert result.grouped_evidence == ()

    def test_strength_gates_with_config(self, tmp_path):
        config = SetupResearchConfig(
            min_sample_total=2,
            min_resolved=1,
            min_valid_r=1,
            strong_min_sample=200,
        )
        engine = _engine(
            _service(tmp_path, _trending_series(), _daily_series()),
            config=config,
        )
        result = _result(engine)
        # Overall sample passes the lowered gates but not the strong gates.
        assert result.evidence is not None
        assert result.evidence.strength.name in ("MODERATE", "STRONG")
        weak_by_group = [
            g for g in result.grouped_evidence if g.strength.name == "WEAK"
        ]
        for group in weak_by_group:
            stats = group.statistics
            assert stats is not None
            assert (
                stats.resolved < config.min_resolved
                or stats.valid_r_count < config.min_valid_r
            )

    def test_evidence_statistics_reused_by_reference(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert result.evidence is not None
        assert result.evidence.statistics is not None
        assert result.evidence.statistics.total == result.occurrence_count


# ============================================================
# F. REGIME / STRUCTURE FILTERING
# ============================================================


class TestRegimeFilter:
    def test_matching_direction_filter(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine, direction_filter="LONG")
        assert result.status is SetupResearchStatus.RESEARCHED
        assert all(o.occurrence.direction == "LONG" for o in result.observations)

    def test_non_matching_direction_filter(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine, direction_filter="SHORT")
        assert result.status is SetupResearchStatus.NO_OCCURRENCES
        assert result.occurrence_count == 0

    def test_setup_type_filter(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        full = _result(engine)
        filtered = _result(engine, setup_type_filter="SETUP_CANDIDATE")
        assert filtered.status is SetupResearchStatus.RESEARCHED
        assert all(
            o.occurrence.setup_type == "SETUP_CANDIDATE" for o in filtered.observations
        )
        assert filtered.occurrences_detected == full.occurrences_detected
        assert filtered.occurrence_count < full.occurrence_count

    def test_trend_filter_matching(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine, trend_filter="BULLISH")
        assert result.status is SetupResearchStatus.RESEARCHED
        assert all(o.occurrence.trend_state == "BULLISH" for o in result.observations)

    def test_multiple_filters_and(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(
            engine,
            direction_filter="LONG",
            setup_type_filter="SETUP_CANDIDATE",
            trend_filter="BULLISH",
        )
        assert all(
            o.occurrence.direction == "LONG"
            and o.occurrence.setup_type == "SETUP_CANDIDATE"
            and o.occurrence.trend_state == "BULLISH"
            for o in result.observations
        )

    def test_deterministic_filtering(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        first = _result(engine, direction_filter="LONG")
        second = _result(engine, direction_filter="LONG")
        assert [o.evaluation_time for o in first.observations] == [
            o.evaluation_time for o in second.observations
        ]

    def test_mtf_alignment_filter(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        all_result = _result(engine)
        if all_result.observations:
            alignment = all_result.observations[0].occurrence.mtf_alignment
            result = _result(engine, mtf_alignment_filter=alignment)
            assert all(
                o.occurrence.mtf_alignment == alignment
                for o in result.observations
            )


# ============================================================
# G. SERIALIZATION
# ============================================================


class TestSerialization:
    def test_round_trip_lossless(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        payload = serialize_result(result)
        assert deserialize_result(payload) == result

    def test_bytes_round_trip(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        payload = serialize_result_bytes(result)
        assert deserialize_result(payload) == result

    def test_deterministic_bytes(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert serialize_result_bytes(result) == serialize_result_bytes(result)

    def test_canonical_json(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert canonical_result_json(result) == canonical_result_json(result)

    def test_header_parse(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        header = parse_result_header(serialize_result(result))
        assert header["research_id"] == result.research_id
        assert header["status"] == result.status.name
        assert header["schema_version"] == SETUP_RESEARCH_SCHEMA_VERSION

    def test_future_schema_rejected(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        payload = json.loads(serialize_result(result))
        payload["schema_version"] = SETUP_RESEARCH_SCHEMA_VERSION + 1
        with pytest.raises(ValueError, match="schema version"):
            deserialize_result(json.dumps(payload))

    def test_malformed_payload_rejected(self):
        with pytest.raises(ValueError):
            deserialize_result("{ not json")

    def test_missing_body_rejected(self):
        payload = json.dumps({"schema_version": SETUP_RESEARCH_SCHEMA_VERSION})
        with pytest.raises(ValueError, match="result body"):
            deserialize_result(payload)

    def test_empty_result_round_trip(self, tmp_path):
        engine = _engine(_service(tmp_path, _flat_series(), _daily_series()))
        result = _result(engine)
        assert deserialize_result(serialize_result(result)) == result


# ============================================================
# H. STORE PERSISTENCE
# ============================================================


class TestStore:
    def _result(self, tmp_path: Path) -> SetupResearchResult:
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        return _result_for(engine, {})

    def test_save_load_round_trip(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        path = store.save(result)
        assert path.exists()
        assert store.load(result.research_id) == result

    def test_exists_and_list(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        store.save(result)
        assert store.exists(result.research_id)
        assert result.research_id in store.list_results()

    def test_no_silent_overwrite(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        store.save(result)
        with pytest.raises(SetupResearchStoreError):
            store.save(result)

    def test_overwrite_allowed(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        store.save(result)
        store.save(result, overwrite=True)

    def test_load_missing_raises(self, tmp_path):
        store = SetupResearchStore(tmp_path / "research")
        with pytest.raises(SetupResearchNotFoundError):
            store.load("nope")

    def test_delete(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        store.save(result)
        store.delete(result.research_id)
        assert not store.exists(result.research_id)

    def test_integrity_mismatch_rejected(self, tmp_path):
        result = self._result(tmp_path)
        store = SetupResearchStore(tmp_path / "research")
        path = store.save(result)
        payload = json.loads(path.read_text())
        payload["result"]["fields"]["research_id"] = "other-id"
        path.write_text(json.dumps(payload))
        with pytest.raises(SetupResearchIntegrityError):
            store.load(result.research_id)

    def test_unsafe_id_rejected(self, tmp_path):
        store = SetupResearchStore(tmp_path / "research")
        with pytest.raises(SetupResearchStoreError):
            store.path_for("../evil")

    def test_default_directory_relative(self):
        assert default_setup_research_directory() == (
            Path.cwd() / "data" / "setup_research"
        )


def _result_for(engine, overrides) -> SetupResearchResult:
    return engine.research(_request(**overrides))


# ============================================================
# I. CORPUS INTERACTION
# ============================================================


class TestCorpusInteraction:
    def test_corpus_unavailable_missing_dataset(self, tmp_path):
        engine = _engine(_service(tmp_path, None, None))
        result = _result(engine)
        assert result.status is SetupResearchStatus.CORPUS_UNAVAILABLE

    def test_corpus_unavailable_empty_dataset(self, tmp_path):
        provider = InMemoryHistoricalProvider({("NIFTY", "15m"): ()})
        service = HistoricalMarketDataService(
            provider=provider, store=HistoricalDataStore(tmp_path / "store"),
        )
        engine = _engine(service)
        result = _result(engine)
        assert result.status is SetupResearchStatus.CORPUS_UNAVAILABLE

    def test_insufficient_history(self, tmp_path):
        engine = _engine(
            _service(tmp_path, _flat_series(n=6), _daily_series(n=2)),
            min_setup_history=20,
        )
        result = _result(engine)
        assert result.status is SetupResearchStatus.INSUFFICIENT_DATA

    def test_request_minimum_history_stricter(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = engine.research(_request(minimum_history=200))
        assert dict(result.skip_counts).get(MINIMUM_HISTORY_SKIP, 0) > 0
        assert result.status is SetupResearchStatus.INSUFFICIENT_DATA

    def test_data_gap_skip_counted(self, tmp_path):
        setup = _trending_series()
        # Remove middle candles to create an unexpected gap.
        gapped = tuple(
            candle for i, candle in enumerate(setup) if i not in (20, 21, 22)
        )
        engine = _engine(_service(tmp_path, gapped, _daily_series()))
        result = _result(engine)
        assert isinstance(result.skip_counts, tuple)


# ============================================================
# J. REPORTING
# ============================================================


class TestReporting:
    def test_returns_str(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert isinstance(SetupResearchFormatter().format(result), str)

    def test_required_sections(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        text = SetupResearchFormatter().format(result)
        for section in (
            "PHASE 6D",
            "Request",
            "Evaluation Points",
            "Occurrences & Outcomes",
            "Evidence",
            "Rationale",
            "Limitations",
        ):
            assert section in text

    def test_disclaimer_present(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        text = SetupResearchFormatter().format(result)
        assert "descriptive and observational" in text
        assert "not a prediction" in text

    def test_no_fabricated_metrics_language(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        text = SetupResearchFormatter().format(result)
        assert "guaranteed profit" not in text.lower()
        assert "expected return" not in text.lower()

    def test_empty_result_format(self, tmp_path):
        engine = _engine(_service(tmp_path, _flat_series(), _daily_series()))
        result = _result(engine)
        text = SetupResearchFormatter().format(result)
        assert "Evidence" not in text.split("Rationale")[0]

    def test_deterministic_output(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        formatter = SetupResearchFormatter()
        assert formatter.format(result) == formatter.format(result)

    def test_formatter_validation(self):
        with pytest.raises(ValueError):
            SetupResearchFormatter(precision=-1)
        with pytest.raises(ValueError):
            SetupResearchFormatter(width=10)


# ============================================================
# K. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_occurrence_geometry_invariant(self):
        with pytest.raises(ValueError):
            SetupOccurrence(
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=BASE,
                setup_classification="POTENTIAL_SETUP",
                setup_direction="BULLISH",
                confluence_score=3,
                candidate_status="CANDIDATE",
                decision_classification="PREFERRED",
                decision_score=90,
                direction="LONG",
                setup_type="BREAKOUT",
                trend_state="BULLISH",
                range_state="NOT_IN_RANGE",
                mtf_alignment="ALIGNED",
                geometry_available=True,
                entry=None,
            )

    def test_occurrence_naive_time_rejected(self):
        with pytest.raises(ValueError):
            SetupOccurrence(
                instrument="NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=datetime(2024, 1, 1),
                setup_classification="POTENTIAL_SETUP",
                setup_direction="BULLISH",
                confluence_score=3,
                candidate_status="CANDIDATE",
                decision_classification="PREFERRED",
                decision_score=90,
                direction="LONG",
                setup_type="BREAKOUT",
                trend_state="BULLISH",
                range_state="NOT_IN_RANGE",
                mtf_alignment="ALIGNED",
                geometry_available=False,
            )

    def test_evidence_count_invariant(self):
        with pytest.raises(ValueError):
            SetupEvidence(
                key="OVERALL",
                dimension="",
                sample_size=2,
                occurrence_count=1,
                completed_outcomes=0,
                ambiguous_count=0,
                unresolved_count=0,
                no_geometry_count=0,
                win_count=0,
                loss_count=0,
                expired_count=0,
                statistics=None,
                strength=_strength("INSUFFICIENT"),
            )

    def test_result_count_invariant(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        with pytest.raises(ValueError):
            SetupResearchResult(
                research_id="x",
                request=result.request,
                status=result.status,
                occurrence_count=1,
            )

    def test_frozen_and_slots(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        result = _result(engine)
        assert not hasattr(result, "__dict__")
        with pytest.raises(Exception):
            result.status = SetupResearchStatus.NO_OCCURRENCES  # type: ignore

    def test_status_properties(self):
        assert SetupResearchStatus.RESEARCHED.has_observations
        assert not SetupResearchStatus.NO_OCCURRENCES.has_observations
        assert not SetupResearchStatus.INSUFFICIENT_DATA.has_observations
        assert not SetupResearchStatus.CORPUS_UNAVAILABLE.has_observations
        assert not SetupResearchStatus.INVALID_REQUEST.has_observations


def _strength(name: str):
    from engine.models.historical_evidence import EvidenceStrength

    return EvidenceStrength[name]


# ============================================================
# L. REGRESSION / END-TO-END
# ============================================================


class TestRegression:
    def test_pipeline_baseline_intact(self):
        result = HistoricalEvaluationPipeline().evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_deterministic_research_id(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        first = _result(engine)
        second = _result(engine)
        assert first.research_id == second.research_id

    def test_different_request_different_id(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        first = _result(engine)
        second = _result_for(engine, {"forward_horizon": 5})
        assert first.research_id != second.research_id

    def test_result_observations_match_detect(self, tmp_path):
        engine = _engine(_service(tmp_path, _trending_series(), _daily_series()))
        request = _request()
        detected = engine.detect(request)
        result = engine.research(request)
        assert [o.evaluation_time for o in detected] == [
            o.evaluation_time for o in result.observations
        ]

    def test_store_excluded_suffixes(self, tmp_path):
        # The .research.json suffix must not pollute the corpus manifest
        # listing (mirrors the Phase 6C separation).
        result = _result(_engine(_service(tmp_path, _trending_series(), _daily_series())))
        store = SetupResearchStore(tmp_path / "research")
        store.save(result)
        assert store.list_results() == (result.research_id,)
