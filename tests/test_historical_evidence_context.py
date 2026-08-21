"""
Tests for Product Phase 6E — Historical + Current Intelligence.

Phase 6E connects the completed Phase 6D historical setup research
output to the EXISTING current-market analysis as DESCRIPTIVE
historical context. The existing decision architecture remains
AUTHORITATIVE: historical evidence NEVER creates, modifies, upgrades,
downgrades or overrides the existing decision / actionability /
direction / geometry / trade plan / paper-trading eligibility.

All tests are deterministic and offline (hand-built persisted research
results or the deterministic dashboard fixtures; no network).
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dashboard.data_provider import FixtureDataProvider
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    HistoricalEvidenceSource,
    OperationsRequest,
    default_service,
)
from dashboard.views import DashboardTradeView, HistoricalContextView, to_jsonable
from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.config.setup_research_config import SetupResearchConfig
from engine.data.historical_evidence_lookup import HistoricalEvidenceLookupEngine
from engine.data.historical_fixtures import historical_candles_by_instrument
from engine.data.historical_provider import InMemoryHistoricalProvider
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.research_corpus import HistoricalResearchCorpusEngine
from engine.data.setup_research import HistoricalSetupResearchEngine
from engine.data.setup_research_store import SetupResearchStore
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.intelligence.performance_analytics import _compute_statistics
from engine.models.historical_context import (
    HistoricalContextStatus,
    HistoricalEvidenceContext,
    HistoricalEvidenceRequest,
)
from engine.models.historical_data import HistoricalDataRequest
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.setup_research import (
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)
from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset
from engine.reporting.historical_evidence_lookup import (
    HistoricalEvidenceContextFormatter,
)

BASE = datetime(2025, 1, 20, tzinfo=UTC)
STEP = timedelta(minutes=15)
CURRENT_T = datetime(2025, 1, 25, 5, 0, tzinfo=UTC)  # fixture evaluation point


# ============================================================
# HELPERS — hand-built persisted Phase 6D research results
# ============================================================


def _subject(instrument: str, ts: datetime, direction: str = "LONG") -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=100.0,
        stop=98.0,
        target=104.0,
        decision_classification="QUALIFIED",
        decision_score=80,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1,
        scan_id="scan-test",
        setup_timeframe="15m",
    )


def _outcome(
    instrument: str,
    ts: datetime,
    status: OutcomeStatus = OutcomeStatus.TARGET_HIT,
    *,
    direction: str = "LONG",
    outcome_ts: datetime | None = None,
) -> HistoricalOutcome:
    if outcome_ts is None and status in (
        OutcomeStatus.TARGET_HIT,
        OutcomeStatus.STOP_HIT,
    ):
        outcome_ts = ts + 4 * STEP
    subject = _subject(instrument, ts, direction)
    if status is OutcomeStatus.TARGET_HIT:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=status,
            outcome_timestamp=outcome_ts,
            exit_price=104.0,
            bars_held=4,
            mfe=5.0,
            mae=1.0,
            mfe_r=2.5,
            mae_r=0.5,
            realized_r=2.0,
            risk=2.0,
            reason="target hit",
        )
    if status is OutcomeStatus.STOP_HIT:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=status,
            outcome_timestamp=outcome_ts,
            exit_price=98.0,
            bars_held=4,
            mfe=1.0,
            mae=3.0,
            mfe_r=0.5,
            mae_r=1.5,
            realized_r=-1.0,
            risk=2.0,
            reason="stop hit",
        )
    if status is OutcomeStatus.EXPIRED:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=status,
            outcome_timestamp=None,
            exit_price=101.0,
            bars_held=20,
            mfe=2.0,
            mae=1.0,
            mfe_r=1.0,
            mae_r=0.5,
            realized_r=0.5,
            risk=2.0,
            reason="expired mark-to-close",
        )
    if status is OutcomeStatus.BOTH_TOUCHED:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=status,
            outcome_timestamp=outcome_ts,
            exit_price=None,
            bars_held=1,
            mfe=5.0,
            mae=3.0,
            mfe_r=2.5,
            mae_r=1.5,
            realized_r=None,
            risk=2.0,
            reason="same-candle both touched (ambiguous)",
        )
    if status is OutcomeStatus.NO_GEOMETRY:
        return HistoricalOutcome(
            subject=OutcomeSubject(
                instrument=instrument,
                direction=direction,
                evaluation_timestamp=ts,
                entry=None,
                stop=None,
                target=None,
            ),
            outcome_status=status,
            reason="incomplete geometry",
        )
    return HistoricalOutcome(
        subject=subject,
        outcome_status=OutcomeStatus.INSUFFICIENT_DATA,
        reason="no forward data",
    )


def _occurrence(
    instrument: str,
    ts: datetime,
    *,
    setup_type: str = "SETUP_CANDIDATE",
    direction: str = "LONG",
    trend_state: str = "BULLISH",
    range_state: str = "NOT_IN_RANGE",
    mtf_alignment: str = "ALIGNED",
) -> SetupOccurrence:
    return SetupOccurrence(
        instrument=instrument,
        setup_timeframe="15m",
        context_timeframe="1D",
        evaluation_time=ts,
        setup_classification="POTENTIAL_SETUP",
        setup_direction="BULLISH",
        confluence_score=4,
        candidate_status="CANDIDATE",
        decision_classification="QUALIFIED",
        decision_score=80,
        direction=direction,
        setup_type=setup_type,
        trend_state=trend_state,
        range_state=range_state,
        mtf_alignment=mtf_alignment,
        geometry_available=True,
        entry=100.0,
        stop=98.0,
        target=104.0,
        reason="test occurrence",
    )


def _research_result(
    instrument: str,
    observations: tuple[SetupResearchObservation, ...],
    *,
    status: SetupResearchStatus = SetupResearchStatus.RESEARCHED,
    research_id: str | None = None,
) -> SetupResearchResult:
    completed = sum(1 for o in observations if o.is_completed)
    ambiguous = sum(1 for o in observations if o.is_ambiguous)
    unresolved = sum(
        1
        for o in observations
        if o.outcome_status is OutcomeStatus.INSUFFICIENT_DATA
    )
    return SetupResearchResult(
        research_id=research_id or f"setup-research-test-{instrument.lower()}",
        request=SetupResearchRequest(instrument),
        status=status,
        points_examined=max(len(observations), 1),
        valid_points=max(len(observations), 1),
        occurrences_detected=len(observations),
        occurrence_count=len(observations),
        completed_outcomes=completed,
        ambiguous_count=ambiguous,
        unresolved_count=unresolved,
        observations=observations,
    )


def _observation(
    instrument: str,
    ts: datetime,
    status: OutcomeStatus = OutcomeStatus.TARGET_HIT,
    *,
    outcome_ts: datetime | None = None,
    **occ_kwargs,
) -> SetupResearchObservation:
    return SetupResearchObservation(
        occurrence=_occurrence(instrument, ts, **occ_kwargs),
        outcome=_outcome(instrument, ts, status, outcome_ts=outcome_ts),
    )


def _save(store: SetupResearchStore, result: SetupResearchResult) -> None:
    store.save(result)


def _store_with(tmp_path: Path, *results: SetupResearchResult) -> SetupResearchStore:
    store = SetupResearchStore(tmp_path / "research")
    for result in results:
        _save(store, result)
    return store


def _request(
    instrument: str = "HDFCBANK",
    evaluation_time: datetime | None = CURRENT_T,
    **dimensions,
) -> HistoricalEvidenceRequest:
    return HistoricalEvidenceRequest(
        instrument,
        setup_timeframe="15m",
        context_timeframe="1D",
        evaluation_time=evaluation_time,
        **dimensions,
    )


#: 25 hand-built historical occurrences (strictly before CURRENT_T) with
#: already-resolved TARGET_HIT outcomes -> STRONG evidence under the
#: default Phase 6D sample gates.
def _strong_result(instrument: str = "HDFCBANK") -> SetupResearchResult:
    observations = tuple(
        _observation(instrument, BASE + i * STEP, OutcomeStatus.TARGET_HIT)
        for i in range(25)
    )
    return _research_result(instrument, observations)


# ============================================================
# HELPERS — deterministic fixture-based 6C/6D chain (dashboard path)
# ============================================================


def _fixture_store(tmp_path: Path, config: SetupResearchConfig | None = None) -> SetupResearchStore:
    by_inst = historical_candles_by_instrument()
    records = {(i, tf): c for i, tfs in by_inst.items() for tf, c in tfs.items()}
    service = HistoricalMarketDataService(
        provider=InMemoryHistoricalProvider(records),
        store=HistoricalDataStore(tmp_path / "historical"),
    )
    reference = records[("NIFTY", "15M")][-1].timestamp + timedelta(days=1)
    for (inst, tf), candles in records.items():
        service.ingest(
            HistoricalDataRequest(
                inst, tf, candles[0].timestamp, candles[-1].timestamp + timedelta(seconds=1),
            ),
            reference_now=reference,
        )
    corpus_engine = HistoricalResearchCorpusEngine(
        service,
        ResearchCorpusConfig(
            setup_timeframe="15m", context_timeframe="1D", min_setup_history=5,
        ),
    )
    research_engine = HistoricalSetupResearchEngine(corpus_engine, config)
    store = SetupResearchStore(tmp_path / "research")
    for inst in by_inst:
        store.save(research_engine.research(SetupResearchRequest(inst)))
    return store


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_request_normalizes_instrument(self):
        assert _request("  hdfcbank ").instrument == "HDFCBANK"

    def test_request_empty_instrument_rejected(self):
        with pytest.raises(ValueError):
            _request("   ")

    def test_request_naive_evaluation_time_rejected(self):
        with pytest.raises(ValueError):
            _request(evaluation_time=datetime(2025, 1, 25, 5, 0))

    def test_request_non_string_dimension_rejected(self):
        with pytest.raises(ValueError):
            HistoricalEvidenceRequest("NIFTY", setup_type=123)  # type: ignore[arg-type]

    def test_available_context_requires_statistics_and_strength(self):
        with pytest.raises(ValueError):
            HistoricalEvidenceContext(
                context_id="hectx-x",
                request=_request(),
                status=HistoricalContextStatus.AVAILABLE,
                comparable_occurrences=1,
            )

    def test_unavailable_context_rejects_statistics_and_strength(self):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(3)
        )
        stats = _compute_statistics(tuple(o.outcome for o in observations))
        with pytest.raises(ValueError):
            HistoricalEvidenceContext(
                context_id="hectx-x",
                request=_request(),
                status=HistoricalContextStatus.NO_MATCH,
                statistics=stats,
                strength=EvidenceStrength.WEAK,
            )

    def test_available_context_requires_occurrences(self):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(3)
        )
        stats = _compute_statistics(tuple(o.outcome for o in observations))
        with pytest.raises(ValueError):
            HistoricalEvidenceContext(
                context_id="hectx-x",
                request=_request(),
                status=HistoricalContextStatus.AVAILABLE,
                comparable_occurrences=0,
                statistics=stats,
                strength=EvidenceStrength.WEAK,
            )

    def test_models_frozen_and_slots(self):
        request = _request()
        with pytest.raises(FrozenInstanceError):
            request.instrument = "X"  # type: ignore[misc]
        assert not hasattr(request, "__dict__")
        assert not hasattr(HistoricalContextView(), "__dict__")

    def test_match_dimensions_and_key_deterministic(self):
        request = _request(setup_type="breakout", direction="long")
        assert request.match_dimensions == (
            ("setup_type", "BREAKOUT"),
            ("direction", "LONG"),
        )
        assert request.match_key == "HDFCBANK|15M|setup_type=BREAKOUT|direction=LONG"

    def test_status_properties(self):
        assert HistoricalContextStatus.AVAILABLE.is_available
        assert not HistoricalContextStatus.NO_MATCH.is_available
        assert not HistoricalContextStatus.RESEARCH_UNAVAILABLE.is_available


# ============================================================
# B. HISTORICAL EVIDENCE AVAILABILITY
# ============================================================


class TestEvidenceAvailability:
    def test_matching_evidence_returned(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(setup_type="SETUP_CANDIDATE"))
        assert ctx.status is HistoricalContextStatus.AVAILABLE
        assert ctx.is_available
        assert ctx.comparable_occurrences == 25
        assert ctx.completed_outcomes == 25
        assert ctx.strength is EvidenceStrength.STRONG
        assert ctx.statistics is not None

    def test_no_matching_evidence_is_unavailable(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(setup_type="BREAKOUT"))  # mismatch
        assert ctx.status is HistoricalContextStatus.NO_MATCH
        assert ctx.strength is None
        assert ctx.statistics is None
        assert ctx.comparable_occurrences == 0
        assert ctx.win_rate is None

    def test_no_store_is_research_unavailable(self):
        engine = HistoricalEvidenceLookupEngine(store=None)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.RESEARCH_UNAVAILABLE

    def test_no_research_for_instrument_is_research_unavailable(self, tmp_path):
        store = _store_with(tmp_path, _strong_result("NIFTY"))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request("HDFCBANK"))
        assert ctx.status is HistoricalContextStatus.RESEARCH_UNAVAILABLE

    def test_non_researched_result_is_no_match_not_unavailable(self, tmp_path):
        result = _research_result(
            "HDFCBANK", (), status=SetupResearchStatus.NO_OCCURRENCES,
        )
        store = _store_with(tmp_path, result)
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.NO_MATCH
        assert ctx.strength is None

    def test_insufficient_sample_uses_existing_vocabulary(self, tmp_path):
        # 3 occurrences, all wins -> still INSUFFICIENT (hard gate).
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(3)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.AVAILABLE
        assert ctx.strength is EvidenceStrength.INSUFFICIENT
        assert ctx.win_rate == pytest.approx(1.0)

    def test_existing_6d_statistics_preserved(self, tmp_path):
        outcomes = (
            _outcome("HDFCBANK", BASE, OutcomeStatus.TARGET_HIT),
            _outcome("HDFCBANK", BASE + STEP, OutcomeStatus.STOP_HIT),
            _outcome("HDFCBANK", BASE + 2 * STEP, OutcomeStatus.EXPIRED),
        )
        observations = tuple(
            SetupResearchObservation(
                occurrence=_occurrence("HDFCBANK", BASE + i * STEP),
                outcome=outcomes[i],
            )
            for i in range(3)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        expected = _compute_statistics(outcomes)
        assert ctx.statistics is not None
        assert ctx.statistics.win_rate == expected.win_rate
        assert ctx.statistics.average_realized_r == expected.average_realized_r
        assert ctx.statistics.median_realized_r == expected.median_realized_r
        assert ctx.statistics.profit_factor == expected.profit_factor
        assert ctx.statistics.average_mfe == expected.average_mfe
        assert ctx.statistics.average_mae == expected.average_mae

    def test_strength_is_reused_11y_enum(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert isinstance(ctx.strength, EvidenceStrength)


# ============================================================
# C. EVIDENCE MATCHING (existing 6D vocabulary)
# ============================================================


class TestEvidenceMatching:
    def test_setup_type_matches(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, setup_type="BREAKOUT"),
            _observation("HDFCBANK", BASE + STEP, setup_type="SETUP_CANDIDATE"),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(setup_type="BREAKOUT"))
        assert ctx.comparable_occurrences == 1

    def test_direction_matches(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, direction="LONG"),
            _observation(
                "HDFCBANK", BASE + STEP, direction="SHORT",
                outcome_ts=None,
            ),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        assert engine.lookup(_request(direction="LONG")).comparable_occurrences == 1
        assert engine.lookup(_request(direction="SHORT")).comparable_occurrences == 1

    def test_trend_state_matches(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, trend_state="BULLISH"),
            _observation("HDFCBANK", BASE + STEP, trend_state="BEARISH"),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(trend_state="BEARISH"))
        assert ctx.comparable_occurrences == 1

    def test_range_state_matches(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, range_state="NOT_IN_RANGE"),
            _observation("HDFCBANK", BASE + STEP, range_state="IN_RANGE"),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(range_state="IN_RANGE"))
        assert ctx.comparable_occurrences == 1

    def test_mtf_alignment_matches(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, mtf_alignment="ALIGNED"),
            _observation("HDFCBANK", BASE + STEP, mtf_alignment="CONFLICTING"),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(mtf_alignment="CONFLICTING"))
        assert ctx.comparable_occurrences == 1

    def test_multiple_dimensions_are_logical_and(self, tmp_path):
        observations = (
            _observation("HDFCBANK", BASE, setup_type="BREAKOUT", direction="LONG"),
            _observation("HDFCBANK", BASE + STEP, setup_type="BREAKOUT", direction="SHORT"),
            _observation("HDFCBANK", BASE + 2 * STEP, setup_type="SETUP_CANDIDATE", direction="LONG"),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request(setup_type="BREAKOUT", direction="LONG"))
        assert ctx.comparable_occurrences == 1

    def test_timeframe_canonical_matching(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        request = HistoricalEvidenceRequest(
            "HDFCBANK",
            setup_timeframe="15M",  # alias for canonical "15m"
            context_timeframe="1d",
            evaluation_time=CURRENT_T,
        )
        ctx = engine.lookup(request)
        assert ctx.status is HistoricalContextStatus.AVAILABLE

    def test_instrument_isolation(self, tmp_path):
        store = _store_with(
            tmp_path, _strong_result("NIFTY"), _strong_result("HDFCBANK"),
        )
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request("HDFCBANK"))
        assert all(r.startswith("setup-research-test-hdfcbank") for r in ctx.research_ids)
        ctx2 = engine.lookup(_request("TCS"))
        assert ctx2.status is HistoricalContextStatus.RESEARCH_UNAVAILABLE

    def test_timeframe_mismatch_not_matched(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        request = HistoricalEvidenceRequest(
            "HDFCBANK",
            setup_timeframe="5m",
            context_timeframe="1D",
            evaluation_time=CURRENT_T,
        )
        assert engine.lookup(request).status is HistoricalContextStatus.RESEARCH_UNAVAILABLE


# ============================================================
# D. STRICT POINT-IN-TIME SAFETY
# ============================================================


class TestPointInTimeSafety:
    def test_occurrence_at_exactly_t_excluded(self, tmp_path):
        observations = (
            _observation("HDFCBANK", CURRENT_T),  # AT T -> excluded
            _observation("HDFCBANK", BASE),  # before T -> included
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.comparable_occurrences == 1

    def test_occurrence_after_t_excluded(self, tmp_path):
        observations = (
            _observation("HDFCBANK", CURRENT_T + STEP),  # future -> excluded
            _observation("HDFCBANK", BASE),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.comparable_occurrences == 1

    def test_unresolved_at_t_outcome_excluded_from_statistics(self, tmp_path):
        # Occurrence before T but its outcome resolved AFTER T: counted as
        # comparable but contributes NO statistics/strength.
        later = CURRENT_T + 4 * STEP
        observations = (
            _observation("HDFCBANK", BASE, outcome_ts=later),
            _observation("HDFCBANK", BASE + STEP),  # resolved at T
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.comparable_occurrences == 2
        assert ctx.completed_outcomes == 1
        assert ctx.statistics is not None
        assert ctx.statistics.total == 1

    def test_all_unresolved_at_t_is_no_match(self, tmp_path):
        later = CURRENT_T + 4 * STEP
        observations = (
            _observation("HDFCBANK", BASE, outcome_ts=later),
            _observation("HDFCBANK", BASE + STEP, outcome_ts=later),
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.NO_MATCH
        assert ctx.comparable_occurrences == 2
        assert ctx.strength is None
        assert ctx.statistics is None

    def test_ambiguous_outcome_counted_honestly(self, tmp_path):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP, OutcomeStatus.BOTH_TOUCHED)
            for i in range(3)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.ambiguous_count == 3
        assert ctx.completed_outcomes == 0
        assert ctx.statistics is not None
        assert ctx.statistics.win_rate is None  # never fabricated from ambiguity

    def test_expired_outcome_counts_as_completed(self, tmp_path):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP, OutcomeStatus.EXPIRED)
            for i in range(3)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.completed_outcomes == 3
        assert ctx.ambiguous_count == 0

    def test_no_geometry_outcome_never_fabricates_levels(self, tmp_path):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP, OutcomeStatus.NO_GEOMETRY)
            for i in range(2)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.statistics is not None
        assert ctx.statistics.win_rate is None
        assert ctx.statistics.total_realized_r is None

    def test_lookup_signature_has_no_future_parameter(self):
        signature = inspect.signature(HistoricalEvidenceLookupEngine.lookup)
        forbidden = {"future", "future_candles", "lookahead", "candles"}
        assert forbidden.isdisjoint(signature.parameters)

    def test_source_signature_has_no_future_parameter(self):
        signature = inspect.signature(HistoricalEvidenceSource.context_for)
        forbidden = {"future", "future_candles", "lookahead", "candles"}
        assert forbidden.isdisjoint(signature.parameters)

    def test_repeated_lookup_deterministic(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        first = engine.lookup(_request())
        second = engine.lookup(_request())
        assert first == second
        assert first.context_id == second.context_id
        assert first.context_id.startswith("hectx-")

    def test_lookup_does_not_evaluate_outcomes(self, tmp_path, monkeypatch):
        def _explode(self, *args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("outcome evaluator must not be called")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _explode)
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.AVAILABLE

    def test_lookup_does_not_run_pipeline(self, tmp_path, monkeypatch):
        def _explode(self, *args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("pipeline must not be called")

        monkeypatch.setattr(HistoricalEvaluationPipeline, "evaluate", _explode)
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.status is HistoricalContextStatus.AVAILABLE

    def test_later_t_sees_strictly_more_history(self, tmp_path):
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(5)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        early = engine.lookup(_request(evaluation_time=BASE + 3 * STEP))
        late = engine.lookup(_request(evaluation_time=CURRENT_T))
        assert early.comparable_occurrences < late.comparable_occurrences

    def test_different_t_different_context_id(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        a = engine.lookup(_request(evaluation_time=BASE + 3 * STEP))
        b = engine.lookup(_request(evaluation_time=CURRENT_T))
        assert a.context_id != b.context_id

    def test_inputs_not_mutated(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        result = store.load("setup-research-test-hdfcbank")
        before = result
        engine = HistoricalEvidenceLookupEngine(store)
        engine.lookup(_request())
        assert store.load("setup-research-test-hdfcbank") == before


# ============================================================
# E. EVIDENCE STRENGTH GATES (reused 6D config / 11Y vocabulary)
# ============================================================


class TestEvidenceStrengthGates:
    def test_weak_strength(self, tmp_path):
        cfg = SetupResearchConfig(
            min_sample_total=3, min_resolved=5, min_valid_r=5,
            strong_min_sample=50, strong_min_resolved=30, strong_min_valid_r=30,
        )
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(3)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store, cfg)
        ctx = engine.lookup(_request())
        assert ctx.strength is EvidenceStrength.WEAK

    def test_moderate_strength(self, tmp_path):
        cfg = SetupResearchConfig(
            min_sample_total=3, min_resolved=3, min_valid_r=3,
            strong_min_sample=50, strong_min_resolved=30, strong_min_valid_r=30,
        )
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP) for i in range(5)
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store, cfg)
        ctx = engine.lookup(_request())
        assert ctx.strength is EvidenceStrength.MODERATE

    def test_strong_strength(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.strength is EvidenceStrength.STRONG

    def test_count_invariants(self, tmp_path):
        outcomes = (
            OutcomeStatus.TARGET_HIT,
            OutcomeStatus.STOP_HIT,
            OutcomeStatus.BOTH_TOUCHED,
            OutcomeStatus.EXPIRED,
        )
        observations = tuple(
            _observation("HDFCBANK", BASE + i * STEP, outcomes[i])
            for i in range(len(outcomes))
        )
        store = _store_with(tmp_path, _research_result("HDFCBANK", observations))
        engine = HistoricalEvidenceLookupEngine(store)
        ctx = engine.lookup(_request())
        assert ctx.comparable_occurrences == 4
        assert ctx.completed_outcomes + ctx.ambiguous_count + ctx.unresolved_count == 4
        assert ctx.completed_outcomes == 3
        assert ctx.ambiguous_count == 1


# ============================================================
# F. REPORTING
# ============================================================


class TestReporting:
    def test_returns_str(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(_request())
        report = HistoricalEvidenceContextFormatter().format(ctx)
        assert isinstance(report, str) and report

    def test_required_sections(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(_request())
        report = HistoricalEvidenceContextFormatter().format(ctx)
        for section in (
            "HISTORICAL EVIDENCE CONTEXT",
            "CURRENT ASSESSMENT",
            "EVIDENCE AVAILABILITY",
            "OCCURRENCES",
            "HISTORICAL OUTCOME STATISTICS",
            "PROVENANCE",
            "RATIONALE",
            "LIMITATIONS",
            "WARNING",
        ):
            assert section in report

    def test_disclaimer_present(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(_request())
        report = HistoricalEvidenceContextFormatter().format(ctx)
        assert "NOT a prediction" in report
        assert "NEVER modifies the authoritative existing decision" in report

    def test_unavailable_shown_honestly(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(
            _request(setup_type="BREAKOUT"),
        )
        report = HistoricalEvidenceContextFormatter().format(ctx)
        assert "NO_MATCH" in report
        assert "UNAVAILABLE" in report
        assert "unavailable (no matched already-resolved outcomes)" in report

    def test_no_predictive_language(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(_request())
        report = HistoricalEvidenceContextFormatter().format(ctx).lower()
        for phrase in (
            "will rise",
            "will fall",
            "guaranteed profit",
            "you should buy",
            "you should sell",
            "enter now",
            "predicted direction",
        ):
            assert phrase not in report

    def test_deterministic_report(self, tmp_path):
        store = _store_with(tmp_path, _strong_result())
        ctx = HistoricalEvidenceLookupEngine(store).lookup(_request())
        formatter = HistoricalEvidenceContextFormatter()
        assert formatter.format(ctx) == formatter.format(ctx)

    def test_formatter_validation(self):
        with pytest.raises(ValueError):
            HistoricalEvidenceContextFormatter(precision=-1)
        with pytest.raises(ValueError):
            HistoricalEvidenceContextFormatter(width=5)


# ============================================================
# G. DASHBOARD INTEGRATION
# ============================================================


def _analyze(
    tmp_path: Path | None = None,
    *,
    instrument: str = "NIFTY",
    with_store: bool = True,
    store: SetupResearchStore | None = None,
) -> DashboardTradeView:
    source = None
    if with_store:
        if store is None:
            store = _fixture_store(tmp_path)
        source = HistoricalEvidenceSource(store)
    service = DashboardAnalysisService(
        provider=FixtureDataProvider(),
        historical_evidence_source=source,
    )
    return service.analyze(AnalysisRequest(instrument=instrument, setup_timeframe="15M"))


class TestDashboardIntegration:
    def test_no_source_is_honestly_unavailable(self):
        view = _analyze(with_store=False)
        assert view.historical_context.status == "UNAVAILABLE"
        assert not view.historical_context.available

    def test_matching_historical_context_attached(self, tmp_path):
        view = _analyze(tmp_path, instrument="NIFTY")
        assert view.historical_context.status == "AVAILABLE"
        assert view.historical_context.available
        assert view.historical_context.comparable_occurrences >= 1
        assert view.historical_context.evidence_strength in (
            "INSUFFICIENT", "WEAK", "MODERATE", "STRONG",
        )

    def test_historical_context_is_additive_in_json(self, tmp_path):
        view = _analyze(tmp_path)
        payload = to_jsonable(view)
        assert "historical_context" in payload
        block = payload["historical_context"]
        for key in (
            "available", "status", "evidence_strength", "match_key",
            "comparable_occurrences", "completed_outcomes", "ambiguous_count",
            "unresolved_count", "win_rate", "average_realized_r",
            "median_realized_r", "profit_factor", "research_ids", "reason",
            "limitations",
        ):
            assert key in block

    def test_existing_json_keys_unchanged(self, tmp_path):
        with_source = to_jsonable(_analyze(tmp_path, instrument="NIFTY"))
        without_source = to_jsonable(_analyze(instrument="NIFTY", with_store=False))
        for key in without_source:
            if key == "historical_context":
                continue
            assert with_source[key] == without_source[key]
        assert set(without_source) <= set(with_source)

    def test_failure_isolated(self, tmp_path):
        class _ExplodingSource:
            def context_for(self, *args, **kwargs):
                raise RuntimeError("boom")

        service = DashboardAnalysisService(
            provider=FixtureDataProvider(),
            historical_evidence_source=_ExplodingSource(),
        )
        view = service.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15M"))
        assert view.historical_context.status == "UNAVAILABLE"
        assert view.decision.decision_classification == "QUALIFIED"

    def test_rejected_plus_strong_evidence_stays_rejected(self, tmp_path):
        # HDFCBANK current decision is REJECTED. Attach STRONG evidence.
        store = _store_with(tmp_path, _strong_result("HDFCBANK"))
        view = _analyze(instrument="HDFCBANK", store=store)
        assert view.historical_context.status == "AVAILABLE"
        assert view.historical_context.evidence_strength == "STRONG"
        assert view.decision.decision_classification == "REJECTED"
        assert view.actionability.name == "NO_OPPORTUNITY"

    def test_no_opportunity_plus_evidence_stays_no_opportunity(self, tmp_path):
        store = _store_with(tmp_path, _strong_result("HDFCBANK"))
        view = _analyze(instrument="HDFCBANK", store=store)
        assert view.actionability.name == "NO_OPPORTUNITY"
        assert view.decision.eligible is False

    def test_qualified_plus_unavailable_stays_qualified(self, tmp_path):
        # NIFTY decision QUALIFIED; attach a store with NO NIFTY research.
        store = _store_with(tmp_path, _strong_result("HDFCBANK"))
        view = _analyze(instrument="NIFTY", store=store)
        assert view.historical_context.status == "RESEARCH_UNAVAILABLE"
        assert view.decision.decision_classification == "QUALIFIED"

    def test_decision_unchanged_with_and_without_evidence(self, tmp_path):
        store = _fixture_store(tmp_path)
        for inst in ("NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"):
            with_src = _analyze(instrument=inst, store=store)
            without_src = _analyze(instrument=inst, with_store=False)
            assert with_src.decision == without_src.decision
            assert with_src.actionability is without_src.actionability
            assert with_src.actionability_detail == without_src.actionability_detail
            assert with_src.geometry == without_src.geometry
            assert with_src.market_overview == without_src.market_overview
            assert with_src.warnings == without_src.warnings

    def test_evidence_never_fabricates_geometry(self, tmp_path):
        # NIFTY geometry is INCOMPLETE (no target); matching evidence
        # must NOT fabricate the missing levels.
        store = _store_with(tmp_path, _strong_result("NIFTY"))
        with_src = _analyze(instrument="NIFTY", store=store)
        without_src = _analyze(instrument="NIFTY", with_store=False)
        assert with_src.decision.decision_classification == "QUALIFIED"
        assert with_src.geometry.geometry_available is False
        assert with_src.geometry == without_src.geometry
        assert with_src.geometry.target_1 is None
        assert with_src.geometry.target_2 is None
        assert with_src.geometry.target_2_supported is False
        assert with_src.geometry.risk_reward_ratio is None

    def test_historical_context_view_frozen(self):
        view = HistoricalContextView()
        with pytest.raises(FrozenInstanceError):
            view.available = True  # type: ignore[misc]

    def test_default_service_passthrough(self, tmp_path):
        store = _store_with(tmp_path, _strong_result("NIFTY"))
        service = default_service(
            historical_evidence_source=HistoricalEvidenceSource(store),
        )
        assert service.historical_evidence_source is not None


# ============================================================
# H. PAPER TRADING (evidence never creates a paper trade)
# ============================================================


class TestPaperTradingAuthority:
    def _cycle(self, service, instruments):
        return service.run_paper_trading_cycle(
            OperationsRequest(
                account_capital="100000",
                risk_percent="1",
                setup_timeframe="15M",
                watchlist=list(instruments),
            ),
        )

    def test_rejected_plus_strong_creates_no_paper_trade(self, tmp_path):
        store = _store_with(tmp_path, _strong_result("HDFCBANK"))
        service = DashboardAnalysisService(
            provider=FixtureDataProvider(),
            historical_evidence_source=HistoricalEvidenceSource(store),
        )
        cycle = self._cycle(service, ["HDFCBANK"])
        assert cycle.trades_created == 0

    def test_cycle_identical_with_and_without_evidence(self, tmp_path):
        store = _fixture_store(tmp_path)
        instruments = ["NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]
        with_src = self._cycle(
            DashboardAnalysisService(
                provider=FixtureDataProvider(),
                historical_evidence_source=HistoricalEvidenceSource(store),
            ),
            instruments,
        )
        without_src = self._cycle(
            DashboardAnalysisService(provider=FixtureDataProvider()),
            instruments,
        )
        assert with_src.trades_created == without_src.trades_created
        assert with_src.trades_updated == without_src.trades_updated
        assert with_src.trades_closed == without_src.trades_closed
        assert with_src.instruments_scanned == without_src.instruments_scanned
        assert with_src.results == without_src.results

    def test_pipeline_baseline_unchanged(self):
        from engine.config.swing_config import SwingConfig
        from engine.pipeline import PipelineConfig

        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3


# ============================================================
# I. BACKWARD COMPATIBILITY
# ============================================================


class TestBackwardCompatibility:
    def test_trade_view_defaults_to_unavailable_context(self):
        view = DashboardTradeView()
        assert view.historical_context.status == "UNAVAILABLE"
        payload = to_jsonable(view)
        assert payload["historical_context"]["available"] is False

    def test_api_analysis_contains_additive_block(self, tmp_path):
        from starlette.testclient import TestClient

        from dashboard.app import create_app

        store = _fixture_store(tmp_path)
        service = DashboardAnalysisService(
            provider=FixtureDataProvider(),
            historical_evidence_source=HistoricalEvidenceSource(store),
        )
        client = TestClient(create_app(service))
        response = client.get("/api/analysis?instrument=NIFTY&timeframe=15M")
        assert response.status_code == 200
        payload = response.json()
        assert "historical_context" in payload
        assert payload["decision"]["decision_classification"] == "QUALIFIED"

    def test_api_analysis_backward_compatible_without_source(self):
        from starlette.testclient import TestClient

        from dashboard.app import create_app

        client = TestClient(
            create_app(DashboardAnalysisService(provider=FixtureDataProvider())),
        )
        response = client.get("/api/analysis?instrument=NIFTY&timeframe=15M")
        assert response.status_code == 200
        payload = response.json()
        assert payload["historical_context"]["status"] == "UNAVAILABLE"

    def test_source_without_store_is_unavailable(self):
        source = HistoricalEvidenceSource()
        view = source.context_for(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15M"),
            "15M",
            "1D",
            _analyze(instrument="NIFTY", with_store=False).market_overview and None,
            None,
        )
        assert view.status == "UNAVAILABLE"

    def test_engine_not_reexported_from_data_package(self):
        # data/__init__.py stays intentionally empty — full-path import only.
        with pytest.raises(ImportError):
            from engine.data import HistoricalEvidenceLookupEngine  # noqa: F401

    def test_formatter_not_reexported_from_reporting_package(self):
        with pytest.raises(ImportError):
            from engine.reporting import HistoricalEvidenceContextFormatter  # noqa: F401


# ============================================================
# J. END-TO-END (corpus -> research -> lookup -> current view)
# ============================================================


class TestEndToEnd:
    def test_full_chain_deterministic(self, tmp_path):
        store_a = _fixture_store(tmp_path / "a")
        store_b = _fixture_store(tmp_path / "b")
        view_a = _analyze(instrument="NIFTY", store=store_a)
        view_b = _analyze(instrument="NIFTY", store=store_b)
        assert view_a.historical_context == view_b.historical_context

    def test_historical_context_tracks_current_assessment(self, tmp_path):
        store = _fixture_store(tmp_path)
        view = _analyze(instrument="NIFTY", store=store)
        ctx = view.historical_context
        assert ctx.status == "AVAILABLE"
        assert ctx.match_key.startswith("NIFTY|15M|")
        # The match key carries the reused setup/structure labels of the
        # current assessment (Phase 6D vocabulary).
        assert "setup_type=" in ctx.match_key

    def test_provenance_references_6d_research(self, tmp_path):
        store = _fixture_store(tmp_path)
        view = _analyze(instrument="NIFTY", store=store)
        assert view.historical_context.research_ids
        assert all(
            rid.startswith("setup-research-")
            for rid in view.historical_context.research_ids
        )
