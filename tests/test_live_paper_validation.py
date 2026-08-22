"""
Tests for Product Phase 6F — LIVE PAPER VALIDATION.

Phase 6F validates the completed Phase 6A-6E historical-research pipeline
against real / near-live market data using PAPER TRADING ONLY. It is a
THIN ORCHESTRATION layer: the existing provider + completed-candle
boundary + analysis + authoritative decision + Phase 6E historical
evidence context + existing paper-trading operations + existing
paper-trade lifecycle are REUSED unchanged. Phase 6F implements NO new
market analysis, NO decision logic, NO geometry, NO position sizing, NO
outcome engine, NO prediction / probability and NO BUY/SELL/ENTER/EXIT/
HOLD recommendation.

Coverage areas (A-J per the Phase 6F specification):

A.  observation model (deterministic id, serialization, schema)
B.  point-in-time (setup <= T, context < T, research <= T, outcomes > T)
C.  historical evidence (AVAILABLE / NO_MATCH / RESEARCH_UNAVAILABLE /
    missing never fabricated)
D.  decision preservation (evidence cannot modify decision /
    actionability / direction / geometry / TradePlan)
E.  paper trading (eligible creates through existing operations;
    rejected / no-opportunity creates none; duplicates; lifecycle reused)
F.  outcomes (active remains active; completed records outcome; no
    fabricated outcome)
G.  failure isolation (instrument failure / provider failure / lookup
    failure / persistence failure)
H.  persistence (atomic write, reload, corruption, idempotency,
    provenance, schema version)
I.  multi-instrument (existing configurable universe; no hard-coded five)
J.  regression (pipeline baseline 4/3; operations semantics unchanged;
    existing APIs importable)

Tests use injected fake providers / fake services — NO live Yahoo
network dependency; fully deterministic.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from engine.config.paper_trade_config import PaperTradeConfig
from engine.config.trade_plan_config import TradePlanConfig
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.intelligence.trade_planning import TradePlanningEngine
from engine.intelligence.live_validation_serialization import (
    LIVE_VALIDATION_SCHEMA_VERSION,
    deserialize_observation,
    serialize_observation,
    serialize_observation_bytes,
)
from engine.models.live_validation import (
    LIVE_VALIDATION_LIMITATIONS,
    LiveValidationCycleResult,
    LiveValidationCycleStatus,
    LiveValidationObservation,
    LiveValidationStatus,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperTradeStatus
from engine.reporting.live_validation import LiveValidationFormatter

from dashboard.data_provider import (
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
)
from dashboard.live_validation import (
    LIVE_VALIDATION_DISCLAIMER,
    LivePaperValidation,
    LiveValidationConfig,
)
from dashboard.live_validation_store import (
    LiveValidationIntegrityError,
    LiveValidationNotFoundError,
    LiveValidationStore,
    LiveValidationStoreError,
    default_live_validation_directory,
)
from dashboard.paper_trade_store import PaperTradeStore
from dashboard.views import (
    ActionabilityState,
    DashboardTradeView,
    DataSourceView,
    DecisionView,
    GeometryView,
    HistoricalContextView,
)


UTC = timezone.utc
T0 = datetime(2024, 1, 1, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=15)


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _candle(ts, o, h, l, c, v=1000.0):
    return OHLCVCandle(ts, o, h, l, c, v)


def _series(
    instrument,
    candles,
    *,
    available=True,
    provider_status=ProviderStatus.OK,
    freshness=FreshnessState.CURRENT,
    latest_completed=None,
    reason="",
):
    candles = tuple(candles)
    latest = latest_completed or (candles[-1].timestamp if candles else None)
    return InstrumentSeries(
        instrument=instrument,
        setup_candles=candles,
        context_candles=(),
        available=available and bool(candles),
        reason=reason,
        data_source="fake",
        provider_status=provider_status,
        freshness_state=freshness if available else FreshnessState.UNAVAILABLE,
        latest_candle_timestamp=latest,
        latest_completed_candle_timestamp=latest,
        forming_setup_candle=None,
        last_successful_fetch_time=latest,
        rejected_future_count=0,
    )


def _unavailable_series(instrument, *, reason="no data", status=ProviderStatus.EMPTY):
    return _series(
        instrument, (), available=False, provider_status=status, reason=reason,
    )


def _strong_context(instrument="NIFTY"):
    """A canned AVAILABLE Phase 6E context view (STRONG evidence)."""

    return HistoricalContextView(
        available=True,
        status="AVAILABLE",
        evidence_strength="STRONG",
        match_key=f"{instrument}|15M",
        comparable_occurrences=25,
        completed_outcomes=25,
        win_rate=0.6,
        average_realized_r=1.2,
        median_realized_r=1.5,
        profit_factor=2.0,
        research_ids=(f"setup-research-{instrument.lower()}",),
        reason="strong historical support",
    )


def _trade_view(
    instrument,
    *,
    actionability=ActionabilityState.READY_FOR_REVIEW,
    decision="QUALIFIED",
    direction="LONG",
    entry=100.0,
    stop=98.0,
    target=104.0,
    risk_distance=2.0,
    reward_distance=4.0,
    risk_reward_ratio=2.0,
    setup_type="TREND_CONTINUATION",
    evaluation_timestamp=None,
    complete=True,
    data_source="fake",
    provider_status=ProviderStatus.OK,
    freshness=FreshnessState.CURRENT,
    historical_context=None,
):
    """Build a canned :class:`DashboardTradeView` for the fake service."""

    geom_complete = entry is not None and stop is not None and target is not None
    return DashboardTradeView(
        instrument=instrument,
        context_timeframe="1D",
        setup_timeframe="15m",
        evaluation_timestamp=evaluation_timestamp,
        scan_status="OPPORTUNITIES_FOUND" if complete else "INCOMPLETE",
        complete=complete,
        decision=DecisionView(
            decision_classification=decision,
            decision_score=80,
            opportunity_status="BEST_OPPORTUNITY" if complete else "",
            rank=1,
            eligible=True,
            confluence_score=4,
            rationale="",
        ),
        geometry=GeometryView(
            direction=direction,
            entry=entry,
            stop=stop,
            target_1=target,
            target_2=None,
            target_2_supported=False,
            risk_distance=risk_distance,
            reward_distance=reward_distance,
            risk_reward_ratio=risk_reward_ratio,
            invalidation_level=stop,
            geometry_available=geom_complete,
            geometry_complete_source=geom_complete,
        ),
        setup_type=setup_type,
        actionability=actionability,
        data_source=DataSourceView(
            data_source=data_source,
            provider_status=(
                provider_status.value
                if hasattr(provider_status, "value")
                else provider_status
            ),
            freshness_state=(
                freshness.value if hasattr(freshness, "value") else freshness
            ),
        ),
        historical_context=historical_context or HistoricalContextView(),
    )


class _FakeProvider:
    """Fake provider serving canned :class:`InstrumentSeries` (NO network)."""

    DATA_SOURCE = "fake"

    def __init__(self, series_map=None):
        self._series_map = series_map or {}
        self.freshness_config = None

    def set(self, instrument, series_or_callable):
        self._series_map[instrument] = series_or_callable

    def is_timeframe_supported(self, setup_timeframe):
        return setup_timeframe in ("15M", "15m")

    def fetch(self, instrument, setup_timeframe, lookback_bars=300, *, reference_now=None):
        entry = self._series_map.get(instrument)
        if entry is None:
            return _unavailable_series(instrument, reason="not configured")
        if callable(entry):
            return entry()
        return entry

    def last_updated(self, instrument, setup_timeframe):
        return self.fetch(instrument, setup_timeframe).latest_completed_candle_timestamp


class _FakeService:
    """Minimal dashboard-service stand-in (real planner + paper engine)."""

    def __init__(self, store, provider=None, views=None):
        self.paper_trade_store = store
        self.provider = provider or _FakeProvider()
        self._views = dict(views or {})
        self.paper_trading_engine = PaperTradingEngine(PaperTradeConfig())
        self.trade_planning_engine = TradePlanningEngine(TradePlanConfig())

    def set_view(self, instrument, view):
        self._views[instrument] = view

    def available_instruments(self):
        return tuple(sorted(self._views.keys()))

    def analyze(self, request):
        view = self._views.get(request.instrument)
        if view is None:
            return DashboardTradeView(
                instrument=request.instrument,
                setup_timeframe=request.setup_timeframe,
                complete=False,
                data_source=DataSourceView(
                    data_source="fake",
                    provider_status=ProviderStatus.UNSUPPORTED,
                    freshness_state=FreshnessState.UNAVAILABLE,
                ),
            )
        if callable(view):
            return view(request)
        return view


def _validation(store_pair, service, **cfg_kwargs):
    """Build a :class:`LivePaperValidation` over a fake service."""

    vstore, _ = store_pair
    config = LiveValidationConfig(**cfg_kwargs) if cfg_kwargs else LiveValidationConfig(
        account_capital="100000", risk_percent="1",
    )
    return LivePaperValidation(service, config=config, validation_store=vstore)


def _stores(tmp_path):
    pstore = PaperTradeStore(directory=tmp_path / "paper_trades")
    vstore = LiveValidationStore(tmp_path / "live_validation")
    return vstore, pstore


def _observation(
    instrument="NIFTY",
    *,
    validation_id="lval-test-0001",
    evaluation_timestamp=T0,
    validation_status=LiveValidationStatus.OBSERVED,
    paper_trade_id="",
    outcome_status="",
    outcome_timestamp=None,
    realized_r=None,
    **kwargs,
):
    defaults = dict(
        validation_id=validation_id,
        instrument=instrument,
        evaluation_timestamp=evaluation_timestamp,
        provider="fake",
        provider_status="OK",
        freshness_state="CURRENT",
        decision_classification="QUALIFIED",
        actionability="READY_FOR_REVIEW",
        direction="LONG",
        geometry_available=True,
        validation_status=validation_status,
        paper_trade_id=paper_trade_id,
        outcome_status=outcome_status,
        outcome_timestamp=outcome_timestamp,
        realized_r=realized_r,
    )
    defaults.update(kwargs)
    return LiveValidationObservation(**defaults)


# ============================================================
# A. OBSERVATION MODEL
# ============================================================


class TestObservationModel:
    def test_deterministic_id_stable_for_same_setup(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"])
        r2 = val.run_once(instruments=["NIFTY"])
        assert r1.observations[0].validation_id == r2.observations[0].validation_id
        assert r1.observations[0].validation_id.startswith("lval-")

    def test_different_setup_different_id(self):
        o1 = _observation("NIFTY", validation_id="lval-a")
        o2 = _observation("RELIANCE", validation_id="lval-b")
        assert o1.validation_id != o2.validation_id

    def test_model_frozen_slots(self):
        obs = _observation()
        with pytest.raises(Exception):
            obs.instrument = "X"  # frozen
        assert not hasattr(obs, "__dict__")  # slots

    def test_trade_referencing_status_requires_trade_id(self):
        with pytest.raises(ValueError):
            _observation(validation_status=LiveValidationStatus.PAPER_TRADE_CREATED)
        with pytest.raises(ValueError):
            _observation(validation_status=LiveValidationStatus.COMPLETED,
                         outcome_status="TARGET_HIT")

    def test_non_referencing_status_rejects_trade_id(self):
        with pytest.raises(ValueError):
            _observation(
                validation_status=LiveValidationStatus.REJECTED,
                paper_trade_id="pt-x",
            )

    def test_completed_requires_outcome_status(self):
        with pytest.raises(ValueError):
            _observation(
                validation_status=LiveValidationStatus.COMPLETED,
                paper_trade_id="pt-x",
            )

    def test_realized_r_only_on_completed(self):
        with pytest.raises(ValueError):
            _observation(realized_r=Decimal("1.5"))
        obs = _observation(
            validation_status=LiveValidationStatus.COMPLETED,
            paper_trade_id="pt-x",
            outcome_status="TARGET_HIT",
            outcome_timestamp=T0 + 4 * STEP,
            realized_r=Decimal("2.0"),
        )
        assert obs.realized_r == Decimal("2.0")

    def test_completed_ambiguous_r_none_allowed(self):
        obs = _observation(
            validation_status=LiveValidationStatus.COMPLETED,
            paper_trade_id="pt-x",
            outcome_status="BOTH_TOUCHED",
            outcome_timestamp=T0 + STEP,
        )
        assert obs.realized_r is None

    def test_instrument_normalized(self):
        assert _observation(" nifty ").instrument == "NIFTY"

    def test_empty_validation_id_rejected(self):
        with pytest.raises(ValueError):
            _observation(validation_id=" ")

    def test_empty_instrument_rejected(self):
        with pytest.raises(ValueError):
            _observation("")

    def test_serialization_round_trip(self):
        obs = _observation(
            validation_status=LiveValidationStatus.COMPLETED,
            paper_trade_id="pt-abc",
            outcome_status="TARGET_HIT",
            outcome_timestamp=T0 + 4 * STEP,
            realized_r=Decimal("2.0"),
            historical_context_status="AVAILABLE",
            historical_evidence_strength="STRONG",
            historical_sample_size=25,
            historical_win_rate=0.6,
            historical_average_realized_r=1.2,
            historical_profit_factor=2.0,
            research_ids=("setup-research-nifty",),
            recorded_at=T0,
            updated_at=T0 + STEP,
            revision=2,
            warnings=("w1",),
            label="lbl",
            metadata=(("k", "v"),),
        )
        loaded = deserialize_observation(serialize_observation(obs))
        assert loaded == obs

    def test_serialization_deterministic_bytes(self):
        obs = _observation()
        assert serialize_observation_bytes(obs) == serialize_observation_bytes(obs)

    def test_schema_version_carried(self):
        payload = json.loads(serialize_observation(_observation()))
        assert payload["schema_version"] == LIVE_VALIDATION_SCHEMA_VERSION == 1

    def test_future_schema_rejected(self):
        payload = json.loads(serialize_observation(_observation()))
        payload["schema_version"] = 99
        with pytest.raises(ValueError):
            deserialize_observation(json.dumps(payload))

    def test_malformed_json_rejected(self):
        with pytest.raises(ValueError):
            deserialize_observation("{not json")

    def test_missing_observation_key_rejected(self):
        with pytest.raises(ValueError):
            deserialize_observation(json.dumps({"schema_version": 1}))

    def test_non_object_rejected(self):
        with pytest.raises(ValueError):
            deserialize_observation(json.dumps([1, 2, 3]))

    def test_limitations_fixed(self):
        obs = _observation()
        assert obs.limitations == LIVE_VALIDATION_LIMITATIONS
        assert any("paper trading only" in l for l in obs.limitations)
        assert any("does not predict" in l for l in obs.limitations)

    def test_cycle_result_model(self):
        result = LiveValidationCycleResult()
        assert result.is_empty
        assert result.status is LiveValidationCycleStatus.NOT_READY
        assert result.observations_with_status(LiveValidationStatus.ERROR) == ()


# ============================================================
# B. POINT-IN-TIME
# ============================================================


class TestPointInTime:
    def test_run_once_has_no_future_parameters(self):
        sig = inspect.signature(LivePaperValidation.run_once)
        for forbidden in ("future", "future_candles", "lookahead"):
            assert forbidden not in sig.parameters

    def test_outcome_evaluator_not_called(self, tmp_path, monkeypatch):
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        def _boom(self, *a, **k):
            raise AssertionError("OutcomeEvaluator must NOT be called")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _boom)
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        assert result.status is LiveValidationCycleStatus.READY

    def test_historical_pipeline_not_called(self, tmp_path, monkeypatch):
        from engine.pipeline.historical_pipeline import HistoricalEvaluationPipeline

        def _boom(self, *a, **k):
            raise AssertionError("HistoricalEvaluationPipeline must NOT be called")

        monkeypatch.setattr(HistoricalEvaluationPipeline, "evaluate", _boom)
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        assert result.status is LiveValidationCycleStatus.READY

    def test_fixed_T_unaffected_by_appended_future_candles(self, tmp_path):
        """Appending future candles must not change the fixed-T observation."""

        vstore, pstore = _stores(tmp_path)
        ts = T0
        candles = [_candle(ts, 100, 101, 99, 100)]
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"], reference_now=ts)

        # Append future candles; keep the analysis evaluation time fixed.
        future = candles + [
            _candle(ts + i * STEP, 200, 201, 199, 200) for i in range(1, 5)
        ]
        svc.provider.set("NIFTY", _series("NIFTY", future))
        r2 = val.run_once(instruments=["NIFTY"], reference_now=ts)
        o1, o2 = r1.observations[0], r2.observations[0]
        assert o1.validation_id == o2.validation_id
        assert o1.decision_classification == o2.decision_classification
        assert o1.evaluation_timestamp == o2.evaluation_timestamp
        assert r2.observations_recorded == 0

    def test_outcome_timestamp_after_evaluation(self, tmp_path):
        """A completed trade's outcome resolves strictly AFTER T."""

        vstore, pstore = _stores(tmp_path)
        # Entry at 100 (candle 0); target 104 hit on candle 3.
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),
            _candle(T0 + 2 * STEP, 100.5, 102, 100, 101),
            _candle(T0 + 3 * STEP, 101, 105, 100.5, 104.5),
        ]
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", candles[:1]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=T0))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"], reference_now=T0)
        assert r1.observations[0].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )

        # Advance: serve all candles; the trade should resolve TARGET_HIT.
        # The second cycle's view is NOT eligible (no new trade created).
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        later_ts = candles[-1].timestamp
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.WAIT,
            decision="WATCH",
            evaluation_timestamp=later_ts,
        ))
        val.run_once(instruments=["NIFTY"], reference_now=later_ts)
        # The T1 observation advanced to COMPLETED with outcome > T.
        refreshed = vstore.load(r1.observations[0].validation_id)
        assert refreshed.validation_status is LiveValidationStatus.COMPLETED
        assert refreshed.outcome_status == "TARGET_HIT"
        assert refreshed.outcome_timestamp is not None
        assert refreshed.outcome_timestamp > refreshed.evaluation_timestamp
        assert refreshed.realized_r is not None


# ============================================================
# C. HISTORICAL EVIDENCE
# ============================================================


class TestHistoricalEvidence:
    def _run(self, tmp_path, historical_context, **view_kwargs):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY", evaluation_timestamp=ts, historical_context=historical_context,
            **view_kwargs,
        ))
        val = _validation((vstore, pstore), svc)
        return val.run_once(instruments=["NIFTY"]).observations[0]

    def test_available_evidence_attached(self, tmp_path):
        obs = self._run(tmp_path, _strong_context("NIFTY"))
        assert obs.historical_context_status == "AVAILABLE"
        assert obs.historical_evidence_strength == "STRONG"
        assert obs.historical_sample_size == 25
        assert obs.historical_win_rate == 0.6
        assert obs.historical_average_realized_r == 1.2
        assert obs.historical_profit_factor == 2.0
        assert obs.research_ids == ("setup-research-nifty",)
        assert obs.has_historical_evidence

    def test_no_match_honest(self, tmp_path):
        obs = self._run(tmp_path, HistoricalContextView(
            available=False, status="NO_MATCH", evidence_strength="UNAVAILABLE",
        ))
        assert obs.historical_context_status == "NO_MATCH"
        assert obs.historical_evidence_strength == "UNAVAILABLE"
        assert obs.historical_sample_size == 0
        assert obs.historical_win_rate is None
        assert not obs.has_historical_evidence

    def test_research_unavailable_honest(self, tmp_path):
        obs = self._run(tmp_path, HistoricalContextView(
            available=False, status="RESEARCH_UNAVAILABLE",
            evidence_strength="UNAVAILABLE",
        ))
        assert obs.historical_context_status == "RESEARCH_UNAVAILABLE"
        assert obs.historical_sample_size == 0

    def test_unavailable_default_never_fabricated(self, tmp_path):
        obs = self._run(tmp_path, HistoricalContextView())
        assert obs.historical_context_status == "UNAVAILABLE"
        assert obs.historical_evidence_strength == "UNAVAILABLE"
        assert obs.historical_sample_size == 0
        assert obs.historical_win_rate is None
        assert obs.historical_average_realized_r is None
        assert obs.historical_profit_factor is None
        assert obs.research_ids == ()

    def test_unavailable_not_conflated_with_insufficient(self, tmp_path):
        unavailable = self._run(tmp_path, HistoricalContextView())
        insufficient = self._run(tmp_path, HistoricalContextView(
            available=True, status="AVAILABLE", evidence_strength="INSUFFICIENT",
            comparable_occurrences=2, completed_outcomes=2,
            win_rate=1.0, average_realized_r=2.0, profit_factor=None,
            research_ids=("r1",),
        ))
        assert unavailable.historical_evidence_strength == "UNAVAILABLE"
        assert insufficient.historical_evidence_strength == "INSUFFICIENT"

    def test_broken_evidence_attachment_isolated(self, tmp_path):
        class _ExplodingContext:
            @property
            def status(self):
                raise RuntimeError("lookup exploded")

        obs = self._run(tmp_path, _ExplodingContext())
        # Honest UNAVAILABLE sentinels + warning; the observation itself
        # still succeeds (the current assessment is not corrupted).
        assert obs.historical_context_status == "UNAVAILABLE"
        assert obs.validation_status is not LiveValidationStatus.ERROR
        assert any("historical evidence context unavailable" in w for w in obs.warnings)


# ============================================================
# D. DECISION PRESERVATION
# ============================================================


class TestDecisionPreservation:
    def test_strong_evidence_does_not_upgrade_rejected(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="REJECTED",
            evaluation_timestamp=ts,
            historical_context=_strong_context("NIFTY"),
        ))
        val = _validation((vstore, pstore), svc)
        obs = val.run_once(instruments=["NIFTY"]).observations[0]
        assert obs.decision_classification == "REJECTED"
        assert obs.validation_status is LiveValidationStatus.REJECTED
        assert obs.paper_trade_id == ""
        assert pstore.list_trades() == []

    def test_evidence_does_not_create_opportunity(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="WATCH",
            evaluation_timestamp=ts,
            historical_context=_strong_context("NIFTY"),
        ))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        assert result.paper_trades_created == 0
        assert result.observations[0].validation_status is (
            LiveValidationStatus.NO_OPPORTUNITY
        )

    def test_evidence_does_not_create_geometry(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        # Incomplete geometry (no target) + STRONG evidence -> still no trade.
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE,
            decision="QUALIFIED",
            target=None,
            evaluation_timestamp=ts,
            historical_context=_strong_context("NIFTY"),
        ))
        val = _validation((vstore, pstore), svc)
        obs = val.run_once(instruments=["NIFTY"]).observations[0]
        assert not obs.geometry_available
        assert obs.paper_trade_id == ""
        assert pstore.list_trades() == []

    def test_view_values_reused_verbatim(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.WAIT,
            decision="WATCH",
            direction="SHORT",
            evaluation_timestamp=ts,
            historical_context=_strong_context("NIFTY"),
        ))
        val = _validation((vstore, pstore), svc)
        obs = val.run_once(instruments=["NIFTY"]).observations[0]
        assert obs.decision_classification == "WATCH"
        assert obs.actionability == ActionabilityState.WAIT.value
        assert obs.direction == "SHORT"
        assert obs.validation_status is LiveValidationStatus.OBSERVED

    def test_trade_plan_reused_verbatim(self, tmp_path):
        """The created paper trade reuses the existing engine geometry + plan."""

        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        obs = val.run_once(instruments=["NIFTY"]).observations[0]
        trade = pstore.load(obs.paper_trade_id)
        assert str(trade.entry) == "100.0"
        assert str(trade.stop) == "98.0"
        assert str(trade.target_1) == "104.0"
        assert trade.target_2 is None and trade.target_2_supported is False
        assert str(trade.engine_risk_distance) == "2.0"
        assert str(trade.engine_reward_distance) == "4.0"
        # maximum_risk = capital * risk% / 100 = 100000 * 1 / 100 = 1000
        assert trade.maximum_risk == Decimal("1000")
        assert trade.existing_decision == "QUALIFIED"


# ============================================================
# E. PAPER TRADING
# ============================================================


class TestPaperTrading:
    def test_eligible_creates_paper_trade_through_operations(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        obs = result.observations[0]
        assert result.paper_trades_created == 1
        assert obs.validation_status is LiveValidationStatus.PAPER_TRADE_CREATED
        assert obs.paper_trade_id.startswith("pt-")
        # The trade exists in the EXISTING paper-trade store (reused layer).
        assert obs.paper_trade_id in pstore.list_trades()
        trade = pstore.load(obs.paper_trade_id)
        assert trade.status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_rejected_creates_none(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="REJECTED",
            evaluation_timestamp=ts,
        ))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        assert result.paper_trades_created == 0
        assert pstore.list_trades() == []

    def test_no_opportunity_creates_none(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="WATCH",
            evaluation_timestamp=ts,
        ))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])
        assert result.paper_trades_created == 0
        assert pstore.list_trades() == []

    def test_duplicate_handling_remains_authoritative(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"])
        r2 = val.run_once(instruments=["NIFTY"])
        assert r1.paper_trades_created == 1
        assert r2.paper_trades_created == 0
        assert r2.duplicates_skipped == 1
        assert len(pstore.list_trades()) == 1
        # Same observation id (idempotent), now ACTIVE (revision advanced).
        assert r2.observations[0].validation_id == r1.observations[0].validation_id
        assert r2.observations[0].validation_status is (
            LiveValidationStatus.PAPER_TRADE_ACTIVE
        )
        assert r2.observations[0].paper_trade_id == r1.observations[0].paper_trade_id

    def test_existing_lifecycle_reused(self, tmp_path):
        """Entry detected by the EXISTING paper-trading engine on completed
        candles; the observation tracks the same lifecycle."""

        vstore, pstore = _stores(tmp_path)
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),   # entry touch (last)
        ]
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", candles[:1]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=T0))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"], reference_now=T0)
        pid = r1.observations[0].paper_trade_id
        # Second cycle: entry touches on the LAST completed candle; the
        # current view is no longer eligible (no new trade created).
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        later = candles[-1].timestamp
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.WAIT,
            decision="WATCH",
            evaluation_timestamp=later,
        ))
        r2 = val.run_once(instruments=["NIFTY"], reference_now=later)
        assert r2.paper_trades_created == 0
        trade = pstore.load(pid)
        assert trade.status is PaperTradeStatus.OPEN
        assert r2.observations[0].paper_trade_id == pid
        assert r2.observations[0].outcome_status == "OPEN"
        assert r2.observations[0].validation_status is (
            LiveValidationStatus.PAPER_TRADE_ACTIVE
        )


# ============================================================
# F. OUTCOMES
# ============================================================


class TestOutcomes:
    def _advance_to(self, tmp_path, candles):
        """Create at T0 (eligible), then advance with completed candles.

        The second cycle's view is NOT eligible (WAIT) so no new trade is
        created — the cycle tracks the EXISTING trade through the reused
        lifecycle only.
        """

        vstore, pstore = _stores(tmp_path)
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", candles[:1]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=T0))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["NIFTY"], reference_now=T0)
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        last_ts = candles[-1].timestamp
        svc.set_view("NIFTY", _trade_view(
            "NIFTY",
            actionability=ActionabilityState.WAIT,
            decision="WATCH",
            evaluation_timestamp=last_ts,
        ))
        r2 = val.run_once(instruments=["NIFTY"], reference_now=last_ts)
        return vstore, pstore, r1, r2

    def test_active_trade_remains_active(self, tmp_path):
        # Entry touches on the LAST completed candle -> OPEN, unresolved.
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),
        ]
        vstore, pstore, r1, r2 = self._advance_to(tmp_path, candles)
        obs = vstore.load(r1.observations[0].validation_id)
        assert obs.validation_status is LiveValidationStatus.PAPER_TRADE_ACTIVE
        assert obs.outcome_status == "OPEN"
        assert obs.realized_r is None
        assert obs.outcome_timestamp is None

    def test_completed_trade_records_outcome(self, tmp_path):
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),
            _candle(T0 + 2 * STEP, 100.5, 105, 100, 104.5),  # target 104 hit
        ]
        vstore, pstore, r1, r2 = self._advance_to(tmp_path, candles)
        obs = vstore.load(r1.observations[0].validation_id)
        assert obs.validation_status is LiveValidationStatus.COMPLETED
        assert obs.outcome_status == "TARGET_HIT"
        assert obs.outcome_timestamp is not None
        assert obs.realized_r is not None
        assert obs.realized_r > 0

    def test_no_fabricated_outcome_at_creation(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        obs = val.run_once(instruments=["NIFTY"]).observations[0]
        assert obs.outcome_status == "WAITING_FOR_ENTRY"
        assert obs.realized_r is None
        assert obs.outcome_timestamp is None

    def test_both_touched_ambiguous_never_fabricated(self, tmp_path):
        # One candle touches BOTH target (104) and stop (98) -> BOTH_TOUCHED.
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),
            _candle(T0 + 2 * STEP, 100.5, 105, 97, 101),  # both touched
        ]
        vstore, pstore, r1, r2 = self._advance_to(tmp_path, candles)
        obs = vstore.load(r1.observations[0].validation_id)
        assert obs.validation_status is LiveValidationStatus.COMPLETED
        assert obs.outcome_status == "BOTH_TOUCHED"
        assert obs.realized_r is None  # ambiguous — never manufactured

    def test_revision_increments_on_outcome_advance(self, tmp_path):
        candles = [
            _candle(T0 + 0 * STEP, 100, 101, 99, 100),
            _candle(T0 + 1 * STEP, 100, 105, 99.5, 104.5),
        ]
        vstore, pstore, r1, r2 = self._advance_to(tmp_path, candles)
        obs = vstore.load(r1.observations[0].validation_id)
        assert obs.revision >= 1
        history = vstore.load_history(obs.validation_id)
        assert len(history) >= 2  # creation revision + advance revision


# ============================================================
# G. FAILURE ISOLATION
# ============================================================


class TestFailureIsolation:
    def test_one_instrument_failure_does_not_abort(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)

        def _explode(request):
            raise RuntimeError("analysis exploded")

        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.provider.set("RELIANCE", _series("RELIANCE", [_candle(ts, 50, 51, 49, 50)]))
        svc.set_view("NIFTY", _explode)
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY", "RELIANCE"])
        by_instrument = {o.instrument: o for o in result.observations}
        assert by_instrument["NIFTY"].validation_status is LiveValidationStatus.ERROR
        assert by_instrument["RELIANCE"].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )
        assert any("NIFTY" in e for e in result.errors)

    def test_provider_failure_recorded_explicitly(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        # No series configured -> provider returns unavailable series; the
        # fake service has no view -> unavailable view (UNSUPPORTED).
        svc.provider.set("RELIANCE", _series("RELIANCE", [_candle(ts, 50, 51, 49, 50)]))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY", "RELIANCE"])
        by_instrument = {o.instrument: o for o in result.observations}
        # NIFTY: no usable completed data -> explicit failure, not success.
        assert by_instrument["NIFTY"].validation_status is LiveValidationStatus.ERROR
        assert by_instrument["NIFTY"].paper_trade_id == ""
        assert by_instrument["RELIANCE"].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )

    def test_historical_lookup_failure_isolated(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0

        class _ExplodingContext:
            @property
            def status(self):
                raise RuntimeError("lookup exploded")

        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.provider.set("RELIANCE", _series("RELIANCE", [_candle(ts, 50, 51, 49, 50)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY", evaluation_timestamp=ts,
            historical_context=_ExplodingContext(),
        ))
        svc.set_view("RELIANCE", _trade_view(
            "RELIANCE", evaluation_timestamp=ts,
            historical_context=_strong_context("RELIANCE"),
        ))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY", "RELIANCE"])
        by_instrument = {o.instrument: o for o in result.observations}
        # Lookup failure -> honest UNAVAILABLE evidence; the instrument is
        # still validated (the trade is still created through operations).
        assert by_instrument["NIFTY"].historical_context_status == "UNAVAILABLE"
        assert by_instrument["NIFTY"].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )
        assert by_instrument["RELIANCE"].historical_context_status == "AVAILABLE"

    def test_persistence_failure_explicit(self, tmp_path, monkeypatch):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.provider.set("RELIANCE", _series("RELIANCE", [_candle(ts, 50, 51, 49, 50)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)

        real_save = vstore.save
        calls = {"n": 0}

        def _flaky(observation, **kwargs):
            calls["n"] += 1
            if observation.instrument == "NIFTY":
                raise LiveValidationStoreError("disk full")
            return real_save(observation, **kwargs)

        monkeypatch.setattr(vstore, "save", _flaky)
        result = val.run_once(instruments=["NIFTY", "RELIANCE"])
        by_instrument = {o.instrument: o for o in result.observations}
        assert by_instrument["NIFTY"].validation_status is LiveValidationStatus.ERROR
        assert any("persistence failed" in w for w in by_instrument["NIFTY"].warnings)
        assert by_instrument["RELIANCE"].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )
        assert any("persistence failed" in e for e in result.errors)

    def test_validation_store_none_not_ready(self, tmp_path):
        _, pstore = _stores(tmp_path)
        svc = _FakeService(pstore)
        val = LivePaperValidation(svc, validation_store=None)
        result = val.run_once(instruments=["NIFTY"])
        assert result.status is LiveValidationCycleStatus.NOT_READY

    def test_outcome_unavailable_when_trade_unloadable(self, tmp_path, monkeypatch):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)

        real_load = pstore.load

        def _flaky_load(pid):
            raise LiveValidationStoreError("journal unreadable")

        monkeypatch.setattr(pstore, "load", _flaky_load)
        result = val.run_once(instruments=["NIFTY"])
        obs = result.observations[0]
        # The trade was created by operations, but the outcome could not be
        # loaded -> honest warning, no fabricated outcome.
        assert any("could not be loaded" in w for w in obs.warnings)
        assert obs.realized_r is None
        monkeypatch.setattr(pstore, "load", real_load)


# ============================================================
# H. PERSISTENCE
# ============================================================


class TestPersistence:
    def test_atomic_write_no_temp_leftover(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        vstore.save(obs)
        leftovers = list(Path(vstore.directory).glob("*.tmp"))
        assert leftovers == []

    def test_reload_round_trip(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation(
            validation_status=LiveValidationStatus.COMPLETED,
            paper_trade_id="pt-abc",
            outcome_status="TARGET_HIT",
            outcome_timestamp=T0 + 4 * STEP,
            realized_r=Decimal("2.0"),
            research_ids=("setup-research-nifty",),
        )
        vstore.save(obs)
        loaded = vstore.load(obs.validation_id)
        assert loaded == obs

    def test_reload_after_restart(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        vstore.save(obs)
        # A NEW store instance over the same directory simulates a restart.
        vstore2 = LiveValidationStore(vstore.directory)
        assert vstore2.load(obs.validation_id) == obs
        assert vstore2.list_observations() == [obs.validation_id]

    def test_corruption_detected(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        path = vstore.save(obs)
        path.write_text("{corrupted json", encoding="utf-8")
        with pytest.raises(LiveValidationIntegrityError):
            vstore.load(obs.validation_id)

    def test_filename_id_mismatch_detected(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        text = serialize_observation(obs)
        other = vstore.directory / "lval-other.validation"
        vstore.directory.mkdir(parents=True, exist_ok=True)
        other.write_text(text, encoding="utf-8")
        with pytest.raises(LiveValidationIntegrityError):
            vstore.load("lval-other")

    def test_future_schema_detected(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        payload = json.loads(serialize_observation(obs))
        payload["schema_version"] = 99
        path = vstore.path_for(obs.validation_id)
        vstore.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(LiveValidationIntegrityError):
            vstore.load(obs.validation_id)

    def test_no_silent_overwrite(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        vstore.save(obs)
        with pytest.raises(LiveValidationStoreError):
            vstore.save(obs)
        vstore.save(obs, overwrite=True)  # explicit overwrite allowed

    def test_idempotent_cycle(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        val.run_once(instruments=["NIFTY"])
        val.run_once(instruments=["NIFTY"])
        val.run_once(instruments=["NIFTY"])
        assert len(vstore.list_observations()) == 1
        assert len(pstore.list_trades()) == 1

    def test_provenance_retained(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation(research_ids=("setup-research-a", "setup-research-b"))
        vstore.save(obs)
        assert vstore.load(obs.validation_id).research_ids == (
            "setup-research-a", "setup-research-b",
        )

    def test_history_journal_appended(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        vstore.save(obs)
        vstore.save(obs, overwrite=True)
        history = vstore.load_history(obs.validation_id)
        assert len(history) == 2
        assert all(h["validation_id"] == obs.validation_id for h in history)

    def test_missing_raises_not_found(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        with pytest.raises(LiveValidationNotFoundError):
            vstore.load("lval-missing")

    def test_delete_removes_record_and_history(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        obs = _observation()
        vstore.save(obs)
        vstore.delete(obs.validation_id)
        assert not vstore.exists(obs.validation_id)
        assert vstore.load_history(obs.validation_id) == []

    def test_unsafe_id_rejected(self, tmp_path):
        vstore, _ = _stores(tmp_path)
        with pytest.raises(LiveValidationStoreError):
            vstore.path_for("../evil")

    def test_default_directory_relative(self):
        path = default_live_validation_directory()
        assert not path.is_absolute() or str(path).startswith(str(Path.cwd()))
        assert path.name == "live_validation"

    def test_observation_files_do_not_pollute_paper_store(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        shared = tmp_path / "shared"
        vstore2 = LiveValidationStore(shared)
        pstore2 = PaperTradeStore(directory=shared)
        vstore2.save(_observation())
        # PaperTradeStore lists only *.json paper trades; the
        # *.validation.json / *.history.jsonl files must not appear.
        assert pstore2.list_trades() == []


# ============================================================
# I. MULTI-INSTRUMENT (existing configurable universe)
# ============================================================


class TestMultiInstrument:
    def test_default_universe_from_service(self, tmp_path):
        """instruments=None -> the EXISTING configured universe mechanism
        (service.available_instruments); nothing hard-coded."""

        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        for inst in ("ALPHA", "BETA", "GAMMA"):
            svc.provider.set(inst, _series(inst, [_candle(ts, 10, 11, 9, 10)]))
            svc.set_view(inst, _trade_view(inst, evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once()
        assert result.instruments_scanned == 3
        assert [o.instrument for o in result.observations] == [
            "ALPHA", "BETA", "GAMMA",
        ]

    def test_not_hardcoded_five_symbols(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        for inst in ("ONLYONE", "ONLYTWO"):
            svc.provider.set(inst, _series(inst, [_candle(ts, 10, 11, 9, 10)]))
            svc.set_view(inst, _trade_view(inst, evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once()
        assert result.instruments_scanned == 2

    def test_custom_instruments_sorted(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        for inst in ("ZZZ", "AAA"):
            svc.provider.set(inst, _series(inst, [_candle(ts, 10, 11, 9, 10)]))
            svc.set_view(inst, _trade_view(inst, evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["ZZZ", "AAA"])
        assert [o.instrument for o in result.observations] == ["AAA", "ZZZ"]

    def test_shuffle_invariance(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        for inst in ("ZZZ", "AAA", "MMM"):
            svc.provider.set(inst, _series(inst, [_candle(ts, 10, 11, 9, 10)]))
            svc.set_view(inst, _trade_view(inst, evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        r1 = val.run_once(instruments=["ZZZ", "AAA", "MMM"], reference_now=ts)
        r2 = val.run_once(instruments=["MMM", "ZZZ", "AAA"], reference_now=ts)
        assert [o.instrument for o in r1.observations] == [
            o.instrument for o in r2.observations
        ]

    def test_per_instrument_outcomes_independent(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.provider.set("RELIANCE", _series("RELIANCE", [_candle(ts, 50, 51, 49, 50)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view(
            "RELIANCE",
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="REJECTED",
            evaluation_timestamp=ts,
        ))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY", "RELIANCE"])
        by_instrument = {o.instrument: o for o in result.observations}
        assert by_instrument["NIFTY"].validation_status is (
            LiveValidationStatus.PAPER_TRADE_CREATED
        )
        assert by_instrument["RELIANCE"].validation_status is (
            LiveValidationStatus.REJECTED
        )
        assert len(pstore.list_trades()) == 1


# ============================================================
# J. REGRESSION
# ============================================================


class TestRegression:
    def test_pipeline_baseline_4_3(self):
        from engine.pipeline.datasets import trending_dataset
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
        )

        candles = trending_dataset()
        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_validation_does_not_change_operations_outcome(self, tmp_path):
        """The validation layer reuses the operations cycle; the paper-trade
        outcome is identical to a direct operations run."""

        from dashboard.paper_trade_operations import (
            OperationsConfig,
            PaperTradingOperations,
        )

        ts = T0
        candles = [_candle(ts, 100, 101, 99, 100)]

        # Direct operations run.
        pstore_direct = PaperTradeStore(directory=tmp_path / "direct")
        svc_direct = _FakeService(pstore_direct)
        svc_direct.provider.set("NIFTY", _series("NIFTY", candles))
        svc_direct.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops = PaperTradingOperations(
            svc_direct,
            config=OperationsConfig(account_capital="100000", risk_percent="1"),
        )
        direct = ops.run_once(instruments=["NIFTY"])

        # Validation run (orchestrates the same operations layer).
        vstore, pstore = _stores(tmp_path / "val")
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=["NIFTY"])

        assert direct.trades_created == result.paper_trades_created == 1
        direct_trade = pstore_direct.load(direct.results[0].created[0])
        val_trade = pstore.load(result.observations[0].paper_trade_id)
        # Same engine geometry + plan (decision/geometry/plan unchanged).
        assert direct_trade.entry == val_trade.entry
        assert direct_trade.stop == val_trade.stop
        assert direct_trade.target_1 == val_trade.target_1
        assert direct_trade.maximum_risk == val_trade.maximum_risk
        assert direct_trade.planned_quantity == val_trade.planned_quantity
        assert direct_trade.existing_decision == val_trade.existing_decision

    def test_existing_apis_importable(self):
        from dashboard.paper_trade_operations import PaperTradingOperations  # noqa: F401
        from dashboard.paper_trade_store import PaperTradeStore  # noqa: F401
        from engine.data.historical_evidence_lookup import (  # noqa: F401
            HistoricalEvidenceLookupEngine,
        )
        from engine.data.setup_research_store import SetupResearchStore  # noqa: F401
        from dashboard.services import (  # noqa: F401
            DashboardAnalysisService,
            HistoricalEvidenceSource,
        )

    def test_reporting_init_not_extended(self):
        import engine.reporting as reporting

        assert not hasattr(reporting, "LiveValidationFormatter")

    def test_intelligence_init_empty(self):
        import engine.intelligence as intelligence

        assert not hasattr(intelligence, "LivePaperValidation")

    def test_determinism_repeated_cycle(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        val = _validation((vstore, pstore), svc)
        val.run_once(instruments=["NIFTY"], reference_now=ts)
        # Steady state: the same completed candle re-validated produces an
        # identical cycle identity (no new state between runs).
        r2 = val.run_once(instruments=["NIFTY"], reference_now=ts)
        r3 = val.run_once(instruments=["NIFTY"], reference_now=ts)
        assert r2.cycle_id == r3.cycle_id

    def test_empty_watchlist_no_data(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        svc = _FakeService(pstore)
        val = _validation((vstore, pstore), svc)
        result = val.run_once(instruments=[])
        assert result.status is LiveValidationCycleStatus.NO_DATA
        assert result.is_empty


# ============================================================
# REPORTING
# ============================================================


class TestReporting:
    def _cycle(self, tmp_path):
        vstore, pstore = _stores(tmp_path)
        ts = T0
        svc = _FakeService(pstore)
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        svc.set_view("NIFTY", _trade_view(
            "NIFTY", evaluation_timestamp=ts,
            historical_context=_strong_context("NIFTY"),
        ))
        val = _validation((vstore, pstore), svc)
        return val.run_once(instruments=["NIFTY"])

    def test_format_cycle_returns_str(self, tmp_path):
        text = LiveValidationFormatter().format_cycle(self._cycle(tmp_path))
        assert isinstance(text, str)

    def test_format_cycle_sections(self, tmp_path):
        text = LiveValidationFormatter().format_cycle(self._cycle(tmp_path))
        for section in (
            "LIVE PAPER VALIDATION REPORT",
            "Cycle ID:",
            "HISTORICAL-VS-LIVE COMPARISON",
            "Decision",
            "Actionability",
            "Evidence",
            "Strength",
            "Sample",
            "Outcome",
            "Realized R",
            "Validation",
            "RATIONALE",
            "LIMITATIONS",
        ):
            assert section in text

    def test_format_observation_sections(self, tmp_path):
        obs = self._cycle(tmp_path).observations[0]
        text = LiveValidationFormatter().format_observation(obs)
        for section in (
            "LIVE VALIDATION OBSERVATION",
            "CURRENT STATE",
            "HISTORICAL EVIDENCE",
            "PAPER-TRADE OUTCOME",
            "VALIDATION STATUS",
        ):
            assert section in text

    def test_disclaimer_present(self, tmp_path):
        text = LiveValidationFormatter().format_cycle(self._cycle(tmp_path))
        assert "does not predict future returns" in text
        assert "paper trading only" in text.lower().replace("  ", " ") or (
            "No real orders are placed" in text
        )

    def test_no_recommendation_language(self, tmp_path):
        text = LiveValidationFormatter().format_cycle(self._cycle(tmp_path))
        for forbidden in (
            "BUY recommendation",
            "SELL recommendation",
            "guaranteed profit",
            "will rise",
            "will fall",
            "probability of success",
        ):
            assert forbidden not in text

    def test_comparison_is_descriptive(self, tmp_path):
        text = LiveValidationFormatter().format_cycle(self._cycle(tmp_path))
        assert "does NOT predict future results" in text

    def test_unavailable_shown(self):
        obs = _observation()
        text = LiveValidationFormatter().format_observation(obs)
        assert "unavailable" in text

    def test_empty_cycle(self):
        text = LiveValidationFormatter().format_cycle(LiveValidationCycleResult())
        assert "(no observations)" in text

    def test_deterministic_output(self, tmp_path):
        result = self._cycle(tmp_path)
        fmt = LiveValidationFormatter()
        assert fmt.format_cycle(result) == fmt.format_cycle(result)

    def test_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            LiveValidationFormatter(precision=-1)

    def test_disclaimer_constant(self):
        assert "does not predict future returns" in LIVE_VALIDATION_DISCLAIMER
        assert "No real orders are placed" in LIVE_VALIDATION_DISCLAIMER


# ============================================================
# OPERATOR CLI (scheduler entry point)
# ============================================================


class TestOperatorCLI:
    def test_cli_defaults(self):
        import run_live_paper_validation as cli

        args = cli.parse_args([])
        assert args.timeframe == "15m"
        assert args.capital == "100000"
        assert args.risk_percent == "1"
        assert args.instruments == ",".join(cli.DEFAULT_INSTRUMENTS)
        # The universe is the EXISTING configured universe, not a
        # hard-coded five-symbol list.
        assert len(cli.DEFAULT_INSTRUMENTS) > 5

    def test_cli_arg_validation(self):
        import run_live_paper_validation as cli

        with pytest.raises(SystemExit):
            cli.parse_args(["--capital", "0"])
        with pytest.raises(SystemExit):
            cli.parse_args(["--risk-percent", "-1"])
        with pytest.raises(SystemExit):
            cli.parse_args(["--capital", "not-a-number"])

    def test_cli_parse_instruments(self):
        import run_live_paper_validation as cli

        assert cli.parse_instruments("nifty, reliance ,NIFTY") == (
            "NIFTY", "RELIANCE",
        )
        with pytest.raises(ValueError):
            cli.parse_instruments("")

    def test_provider_env_defaulted_not_overwritten(self, monkeypatch):
        import run_live_paper_validation as cli

        monkeypatch.delenv("DASHBOARD_PROVIDER", raising=False)
        assert cli._resolve_provider_name() == "yahoo"
        assert os.environ["DASHBOARD_PROVIDER"] == "yahoo"
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli._resolve_provider_name() == "fixture"

    def test_empty_instruments_config_error(self, capsys):
        import run_live_paper_validation as cli

        assert cli.main(["--instruments", ""]) == 2
        assert "empty watchlist" in capsys.readouterr().err

    def test_runtime_failure_returns_nonzero(self, monkeypatch, capsys):
        import run_live_paper_validation as cli
        import dashboard.services as services

        def _boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(services, "default_service", _boom)
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli.main([]) == 1
        assert "could not be executed" in capsys.readouterr().err

    def test_end_to_end_fixture_cycle(self, monkeypatch, tmp_path, capsys):
        import run_live_paper_validation as cli

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli.main(["--instruments", "NIFTY,RELIANCE"]) == 0
        out = capsys.readouterr().out
        assert cli.PAPER_TRADING_BANNER in out
        assert cli.DESCRIPTIVE_BANNER in out
        assert "LIVE PAPER VALIDATION CYCLE" in out
        assert "lvcycle-" in out
        assert "NIFTY" in out
        # Validation observations persisted to the default directory.
        store = LiveValidationStore(tmp_path / "data" / "live_validation")
        assert len(store.list_observations()) >= 1

    def test_end_to_end_deterministic_cycle_id(self, monkeypatch, tmp_path, capsys):
        import run_live_paper_validation as cli

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli.main(["--instruments", "NIFTY"]) == 0
        first = capsys.readouterr().out
        assert cli.main(["--instruments", "NIFTY"]) == 0
        second = capsys.readouterr().out
        import re as _re

        id1 = _re.search(r"lvcycle-[0-9a-f]{16}", first).group(0)
        id2 = _re.search(r"lvcycle-[0-9a-f]{16}", second).group(0)
        assert id1 == id2

    def test_report_has_no_recommendation_language(self, monkeypatch, tmp_path, capsys):
        import run_live_paper_validation as cli

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli.main(["--instruments", "NIFTY"]) == 0
        out = capsys.readouterr().out
        for forbidden in (
            "BUY recommendation",
            "SELL recommendation",
            "guaranteed profit",
            "probability of success",
        ):
            assert forbidden not in out
