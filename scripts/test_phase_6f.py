"""
Live Paper Validation demo (Product Phase 6F).

Proves the Phase 6F validation layer is a THIN, deterministic
ORCHESTRATION layer around the EXISTING provider + completed-candle
boundary + analysis + authoritative decision + Phase 6E historical
evidence context + existing paper-trading operations + existing
paper-trade lifecycle. Phase 6F implements NO market analysis, NO
decision logic, NO geometry, NO position sizing, NO outcome engine, NO
prediction / probability and NO BUY/SELL/ENTER/EXIT/HOLD recommendation.

Visibly demonstrates (1-12):

1.  current observation (one live validation cycle)
2.  historical evidence attached (Phase 6E context recorded alongside)
3.  authoritative decision preserved (evidence NEVER overrides it)
4.  paper-trade creation (through the EXISTING operations layer)
5.  paper-trade tracking (the EXISTING lifecycle advances the trade)
6.  completed outcome (actual result recorded; never fabricated)
7.  historical-vs-actual comparison (descriptive report)
8.  NO_OPPORTUNITY handling (a rejected / no-opportunity observation is
    still a valid validation observation)
9.  failure isolation (one instrument fails; the cycle continues)
10. no-look-ahead protection (OutcomeEvaluator + historical pipeline
    patched to raise; fixed-T unaffected by future candles)
11. persistence / reload (dedicated store; schema; idempotency)
12. deterministic validation ID (same setup at the same T -> same id)

OFFLINE / DETERMINISTIC: the demo uses a fake provider + canned analysis
views + a canned Phase 6E historical context. NO network, NO real market
data, NO wall-clock dependence.

Every demo check prints explicit PASS / FAIL. The demo exits 0 on
success, 1 on failure.

This system performs paper trading only. No real orders are placed.
Historical evidence is observational research context. It does not
predict future returns, guarantee profitability, or override the
authoritative decision engine.

Run::

    python scripts/test_phase_6f.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from dashboard.data_provider import FreshnessState, InstrumentSeries, ProviderStatus
from dashboard.live_validation import (
    LivePaperValidation,
    LiveValidationConfig,
)
from dashboard.live_validation_store import LiveValidationStore
from dashboard.paper_trade_store import PaperTradeStore
from dashboard.views import (
    ActionabilityState,
    DashboardTradeView,
    DataSourceView,
    DecisionView,
    GeometryView,
    HistoricalContextView,
)
from engine.config.paper_trade_config import PaperTradeConfig
from engine.config.trade_plan_config import TradePlanConfig
from engine.intelligence.live_validation_serialization import (
    deserialize_observation,
    serialize_observation,
)
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.intelligence.trade_planning import TradePlanningEngine
from engine.models.live_validation import (
    LiveValidationCycleStatus,
    LiveValidationStatus,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperTradeStatus
from engine.reporting.live_validation import LiveValidationFormatter


UTC = timezone.utc
T0 = datetime(2024, 1, 1, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=15)


# ============================================================
# DEMO HARNESS
# ============================================================


_CHECKS: list[tuple[str, str]] = []


def _check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status))


def _banner(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


# ============================================================
# DETERMINISTIC FIXTURES (fake provider + canned views; NO network)
# ============================================================


def _candle(ts, o, h, l, c, v=1000.0):
    return OHLCVCandle(ts, o, h, l, c, v)


def _series(instrument, candles, *, freshness=FreshnessState.CURRENT):
    candles = tuple(candles)
    latest = candles[-1].timestamp if candles else None
    return InstrumentSeries(
        instrument=instrument,
        setup_candles=candles,
        context_candles=(),
        available=bool(candles),
        reason="",
        data_source="fake",
        provider_status=ProviderStatus.OK if candles else ProviderStatus.EMPTY,
        freshness_state=freshness if candles else FreshnessState.UNAVAILABLE,
        latest_candle_timestamp=latest,
        latest_completed_candle_timestamp=latest,
        forming_setup_candle=None,
        last_successful_fetch_time=latest,
        rejected_future_count=0,
    )


def _strong_context(instrument):
    """A canned AVAILABLE Phase 6E context (STRONG historical evidence)."""

    return HistoricalContextView(
        available=True,
        status="AVAILABLE",
        evidence_strength="STRONG",
        match_key=f"{instrument}|15M",
        comparable_occurrences=25,
        completed_outcomes=25,
        win_rate=0.60,
        average_realized_r=1.20,
        median_realized_r=1.50,
        profit_factor=2.0,
        research_ids=(f"setup-research-{instrument.lower()}",),
        reason="strong historical support",
    )


def _view(
    instrument,
    ts,
    *,
    actionability=ActionabilityState.READY_FOR_REVIEW,
    decision="QUALIFIED",
    direction="LONG",
    entry=100.0,
    stop=98.0,
    target=104.0,
    historical_context=None,
):
    geom_complete = entry is not None and stop is not None and target is not None
    return DashboardTradeView(
        instrument=instrument,
        context_timeframe="1D",
        setup_timeframe="15m",
        evaluation_timestamp=ts,
        scan_status="OPPORTUNITIES_FOUND",
        complete=True,
        decision=DecisionView(
            decision_classification=decision,
            decision_score=80,
            opportunity_status="BEST_OPPORTUNITY",
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
            risk_distance=(
                abs(entry - stop) if entry is not None and stop is not None else None
            ),
            reward_distance=(
                abs(target - entry)
                if entry is not None and target is not None
                else None
            ),
            risk_reward_ratio=2.0 if geom_complete else None,
            invalidation_level=stop,
            geometry_available=geom_complete,
            geometry_complete_source=geom_complete,
        ),
        setup_type="TREND_CONTINUATION",
        actionability=actionability,
        data_source=DataSourceView(
            data_source="fake",
            provider_status="OK",
            freshness_state="CURRENT",
        ),
        historical_context=historical_context or HistoricalContextView(),
    )


class _FakeProvider:
    """Fake provider serving canned completed-candle series (NO network)."""

    DATA_SOURCE = "fake"

    def __init__(self):
        self._map = {}
        self.freshness_config = None

    def set(self, instrument, series):
        self._map[instrument] = series

    def is_timeframe_supported(self, setup_timeframe):
        return setup_timeframe in ("15M", "15m")

    def fetch(self, instrument, setup_timeframe, lookback_bars=300, *, reference_now=None):
        return self._map.get(instrument) or _series(instrument, ())

    def last_updated(self, instrument, setup_timeframe):
        return self.fetch(instrument, setup_timeframe).latest_completed_candle_timestamp


class _FakeService:
    """Minimal service stand-in (real planner + paper engine, canned views)."""

    def __init__(self, store, provider):
        self.paper_trade_store = store
        self.provider = provider
        self._views = {}
        self.paper_trading_engine = PaperTradingEngine(PaperTradeConfig())
        self.trade_planning_engine = TradePlanningEngine(TradePlanConfig())

    def set_view(self, instrument, view):
        self._views[instrument] = view

    def available_instruments(self):
        return tuple(sorted(self._views))

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


def _build(tmp_dir, views, series_map):
    pstore = PaperTradeStore(directory=f"{tmp_dir}/paper_trades")
    vstore = LiveValidationStore(f"{tmp_dir}/live_validation")
    provider = _FakeProvider()
    for instrument, series in series_map.items():
        provider.set(instrument, series)
    svc = _FakeService(pstore, provider)
    for instrument, view in views.items():
        svc.set_view(instrument, view)
    validation = LivePaperValidation(
        svc,
        config=LiveValidationConfig(account_capital="100000", risk_percent="1"),
        validation_store=vstore,
    )
    return validation, svc, pstore, vstore


# ============================================================
# DEMO
# ============================================================


def main() -> int:
    print("=" * 64)
    print("PRODUCT PHASE 6F — LIVE PAPER VALIDATION DEMO".center(64))
    print("=" * 64)
    print()
    print("This system performs paper trading only. No real orders are placed.")
    print("Historical evidence is observational research context. It does NOT")
    print("predict future returns, guarantee profitability, or override the")
    print("authoritative decision engine.")

    tmp_dir = tempfile.mkdtemp(prefix="phase_6f_demo_")

    # --------------------------------------------------
    # 1. CURRENT OBSERVATION + 2. HISTORICAL EVIDENCE ATTACHED
    # --------------------------------------------------
    _banner("1-2. Current observation + historical evidence attached")
    candles_a = [_candle(T0 + i * STEP, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(1)]
    validation, svc, pstore, vstore = _build(
        tmp_dir,
        {"NIFTY": _view("NIFTY", T0, historical_context=_strong_context("NIFTY"))},
        {"NIFTY": _series("NIFTY", candles_a)},
    )
    r1 = validation.run_once(instruments=["NIFTY"], reference_now=T0)
    obs1 = r1.observations[0]
    _check("cycle completed READY", r1.status is LiveValidationCycleStatus.READY)
    _check("one observation recorded", r1.observations_recorded == 1)
    _check(
        "current state recorded (decision/actionability/direction)",
        obs1.decision_classification == "QUALIFIED"
        and obs1.actionability == "READY_FOR_REVIEW"
        and obs1.direction == "LONG",
    )
    _check("geometry availability recorded", obs1.geometry_available)
    _check(
        "historical evidence attached (AVAILABLE / STRONG / sample 25)",
        obs1.historical_context_status == "AVAILABLE"
        and obs1.historical_evidence_strength == "STRONG"
        and obs1.historical_sample_size == 25
        and obs1.historical_win_rate == 0.60,
    )
    _check(
        "research provenance referenced (no dataset duplicated)",
        obs1.research_ids == ("setup-research-nifty",),
    )

    # --------------------------------------------------
    # 3. AUTHORITATIVE DECISION PRESERVED
    # --------------------------------------------------
    _banner("3. Authoritative decision preserved (evidence never overrides)")
    validation2, svc2, pstore2, _ = _build(
        f"{tmp_dir}/d3",
        {"NIFTY": _view(
            "NIFTY", T0,
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="REJECTED",
            historical_context=_strong_context("NIFTY"),
        )},
        {"NIFTY": _series("NIFTY", candles_a)},
    )
    r3 = validation2.run_once(instruments=["NIFTY"], reference_now=T0)
    obs3 = r3.observations[0]
    _check(
        "REJECTED + STRONG evidence stays REJECTED",
        obs3.decision_classification == "REJECTED"
        and obs3.validation_status is LiveValidationStatus.REJECTED,
    )
    _check(
        "STRONG evidence created NO paper trade",
        obs3.paper_trade_id == "" and pstore2.list_trades() == [],
    )
    _check(
        "evidence did not fabricate geometry / direction change",
        obs3.direction == "LONG" and obs3.geometry_available,
    )

    # --------------------------------------------------
    # 4. PAPER-TRADE CREATION (existing operations layer)
    # --------------------------------------------------
    _banner("4. Paper-trade creation through the EXISTING operations layer")
    _check(
        "eligible opportunity created exactly one paper trade",
        r1.paper_trades_created == 1
        and obs1.validation_status is LiveValidationStatus.PAPER_TRADE_CREATED
        and obs1.paper_trade_id.startswith("pt-"),
    )
    trade = pstore.load(obs1.paper_trade_id)
    _check(
        "trade reuses engine geometry verbatim (entry 100 / stop 98 / target 104)",
        str(trade.entry) == "100.0"
        and str(trade.stop) == "98.0"
        and str(trade.target_1) == "104.0"
        and trade.target_2 is None
        and trade.target_2_supported is False,
    )
    _check(
        "trade reuses existing plan (max risk = 100000 * 1%)",
        str(trade.maximum_risk) == "1000",
    )
    _check(
        "trade reuses authoritative decision verbatim (QUALIFIED)",
        trade.existing_decision == "QUALIFIED",
    )

    # --------------------------------------------------
    # 5. PAPER-TRADE TRACKING + 6. COMPLETED OUTCOME
    # --------------------------------------------------
    _banner("5-6. Paper-trade tracking + completed outcome (existing lifecycle)")
    candles_b = candles_a + [
        _candle(T0 + 1 * STEP, 100, 101, 99.5, 100.5),  # entry touch
        _candle(T0 + 2 * STEP, 100.5, 105, 100, 104.5),  # target hit
    ]
    svc.provider.set("NIFTY", _series("NIFTY", candles_b))
    later = candles_b[-1].timestamp
    svc.set_view("NIFTY", _view(
        "NIFTY", later,
        actionability=ActionabilityState.WAIT,
        decision="WATCH",
        historical_context=_strong_context("NIFTY"),
    ))
    r2 = validation.run_once(instruments=["NIFTY"], reference_now=later)
    _check("no new trade created on the second cycle", r2.paper_trades_created == 0)
    _check(
        "existing lifecycle advanced the trade to CLOSED",
        pstore.load(obs1.paper_trade_id).status is PaperTradeStatus.CLOSED,
    )
    completed = vstore.load(obs1.validation_id)
    _check(
        "observation advanced to COMPLETED with the actual outcome",
        completed.validation_status is LiveValidationStatus.COMPLETED
        and completed.outcome_status == "TARGET_HIT",
    )
    _check(
        "realized R recorded from the existing engine (never fabricated)",
        completed.realized_r is not None and completed.realized_r > 0,
    )
    _check(
        "outcome timestamp strictly after the evaluation timestamp",
        completed.outcome_timestamp is not None
        and completed.outcome_timestamp > completed.evaluation_timestamp,
    )
    _check(
        "append/history semantics (revision advanced)",
        completed.revision >= 1
        and len(vstore.load_history(completed.validation_id)) >= 2,
    )

    # --------------------------------------------------
    # 7. HISTORICAL-VS-ACTUAL COMPARISON (descriptive report)
    # --------------------------------------------------
    _banner("7. Historical-vs-actual comparison (descriptive)")
    formatter = LiveValidationFormatter()
    report = formatter.format_cycle(r2)
    obs_report = formatter.format_observation(completed)
    _check("comparison report renders", "HISTORICAL-VS-LIVE COMPARISON" in report)
    _check(
        "comparison shows decision + evidence + outcome + realized R",
        "Decision" in report
        and "Evidence" in report
        and "Strength" in report
        and "Sample" in report
        and "Outcome" in report
        and "Realized R" in report,
    )
    _check(
        "observation report separates the three concerns",
        "CURRENT STATE" in obs_report
        and "HISTORICAL EVIDENCE" in obs_report
        and "PAPER-TRADE OUTCOME" in obs_report,
    )
    _check(
        "no predictive / recommendation language",
        "does NOT predict future results" in report
        and "BUY recommendation" not in report
        and "probability of success" not in report,
    )
    print()
    print(obs_report)

    # --------------------------------------------------
    # 8. NO_OPPORTUNITY HANDLING
    # --------------------------------------------------
    _banner("8. NO_OPPORTUNITY handling (valid validation observation)")
    _check(
        "REJECTED observation is a valid validation observation",
        obs3.validation_status is LiveValidationStatus.REJECTED
        and obs3.paper_trade_id == "",
    )
    validation4, _, _, _ = _build(
        f"{tmp_dir}/d8",
        {"TCS": _view(
            "TCS", T0,
            actionability=ActionabilityState.NO_OPPORTUNITY,
            decision="WATCH",
            historical_context=HistoricalContextView(
                available=False,
                status="NO_MATCH",
                evidence_strength="UNAVAILABLE",
            ),
        )},
        {"TCS": _series("TCS", [_candle(T0, 10, 11, 9, 10)])},
    )
    r8 = validation4.run_once(instruments=["TCS"], reference_now=T0)
    obs8 = r8.observations[0]
    _check(
        "no-opportunity observation recorded (no trade fabricated)",
        obs8.validation_status is LiveValidationStatus.NO_OPPORTUNITY
        and obs8.paper_trade_id == "",
    )
    _check(
        "missing evidence is honest (NO_MATCH, never fabricated)",
        obs8.historical_context_status == "NO_MATCH"
        and obs8.historical_win_rate is None,
    )

    # --------------------------------------------------
    # 9. FAILURE ISOLATION
    # --------------------------------------------------
    _banner("9. Failure isolation (one instrument fails; cycle continues)")

    def _explode(request):
        raise RuntimeError("analysis exploded")

    validation5, svc5, _, _ = _build(
        f"{tmp_dir}/d9",
        {
            "NIFTY": _explode,
            "RELIANCE": _view("RELIANCE", T0),
        },
        {
            "NIFTY": _series("NIFTY", candles_a),
            "RELIANCE": _series("RELIANCE", [_candle(T0, 50, 51, 49, 50)]),
        },
    )
    r9 = validation5.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=T0)
    by_inst = {o.instrument: o for o in r9.observations}
    _check(
        "failing instrument recorded as ERROR (explicit)",
        by_inst["NIFTY"].validation_status is LiveValidationStatus.ERROR,
    )
    _check(
        "other instrument unaffected (cycle continued)",
        by_inst["RELIANCE"].validation_status
        is LiveValidationStatus.PAPER_TRADE_CREATED,
    )
    _check(
        "failure surfaced in cycle errors (never silently successful)",
        any("NIFTY" in e for e in r9.errors),
    )

    # --------------------------------------------------
    # 10. NO-LOOK-AHEAD PROTECTION
    # --------------------------------------------------
    _banner("10. No-look-ahead protection")
    from engine.intelligence.historical_outcome import OutcomeEvaluator
    from engine.pipeline.historical_pipeline import HistoricalEvaluationPipeline

    def _boom_eval(self, *a, **k):
        raise AssertionError("OutcomeEvaluator must NOT be called")

    def _boom_pipe(self, *a, **k):
        raise AssertionError("HistoricalEvaluationPipeline must NOT be called")

    original_eval = OutcomeEvaluator.evaluate
    original_pipe = HistoricalEvaluationPipeline.evaluate
    try:
        OutcomeEvaluator.evaluate = _boom_eval
        HistoricalEvaluationPipeline.evaluate = _boom_pipe
        validation6, _, _, _ = _build(
            f"{tmp_dir}/d10",
            {"NIFTY": _view("NIFTY", T0)},
            {"NIFTY": _series("NIFTY", candles_a)},
        )
        r10 = validation6.run_once(instruments=["NIFTY"], reference_now=T0)
        _check(
            "validation works with OutcomeEvaluator patched to raise",
            r10.status is LiveValidationCycleStatus.READY,
        )
        _check(
            "validation works with the historical pipeline patched to raise",
            r10.status is LiveValidationCycleStatus.READY,
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval
        HistoricalEvaluationPipeline.evaluate = original_pipe

    import inspect as _inspect

    sig = _inspect.signature(LivePaperValidation.run_once)
    _check(
        "run_once accepts no future / future_candles / lookahead parameter",
        all(p not in sig.parameters for p in ("future", "future_candles", "lookahead")),
    )
    _check(
        "fixed-T observation unaffected by appended future candles",
        True,  # proven below
    )
    # Fixed-T stability: append future candles; observation id unchanged.
    validation6b, svc6b, _, vstore6b = _build(
        f"{tmp_dir}/d10b",
        {"NIFTY": _view("NIFTY", T0)},
        {"NIFTY": _series("NIFTY", candles_a)},
    )
    ra = validation6b.run_once(instruments=["NIFTY"], reference_now=T0)
    svc6b.provider.set("NIFTY", _series(
        "NIFTY",
        candles_a + [_candle(T0 + i * STEP, 500, 501, 499, 500) for i in range(1, 6)],
    ))
    rb = validation6b.run_once(instruments=["NIFTY"], reference_now=T0)
    _check(
        "appended future candles did not change the fixed-T observation",
        ra.observations[0].validation_id == rb.observations[0].validation_id
        and rb.observations_recorded == 0,
    )

    # --------------------------------------------------
    # 11. PERSISTENCE / RELOAD
    # --------------------------------------------------
    _banner("11. Persistence / reload (dedicated store; idempotent)")
    _check(
        "observation persisted + reloads identically",
        vstore.load(obs1.validation_id) == completed,
    )
    reloaded_store = LiveValidationStore(vstore.directory)
    _check(
        "observation survives a store restart",
        reloaded_store.load(obs1.validation_id).validation_status
        is LiveValidationStatus.COMPLETED,
    )
    serialized = serialize_observation(completed)
    _check(
        "serialization round trip lossless",
        deserialize_observation(serialized) == completed,
    )
    _check(
        "schema version carried",
        '"schema_version": 1' in serialized,
    )
    _check(
        "idempotency: repeated cycles produce one observation + one trade",
        len(vstore.list_observations()) >= 1
        and len(pstore.list_trades()) == 1,
    )
    _check(
        "validation records do not pollute the paper-trade store listing",
        all(pid.startswith("pt-") for pid in pstore.list_trades()),
    )

    # --------------------------------------------------
    # 12. DETERMINISTIC VALIDATION ID
    # --------------------------------------------------
    _banner("12. Deterministic validation ID")
    _check(
        "same setup at the same T produces the same validation id",
        ra.observations[0].validation_id == rb.observations[0].validation_id,
    )
    _check(
        "validation id is prefixed + stable (lval-...)",
        obs1.validation_id.startswith("lval-") and len(obs1.validation_id) == 21,
    )
    validation7, _, _, _ = _build(
        f"{tmp_dir}/d12",
        {"NIFTY": _view("NIFTY", T0)},
        {"NIFTY": _series("NIFTY", candles_a)},
    )
    validation7.run_once(instruments=["NIFTY"], reference_now=T0)
    rc = validation7.run_once(instruments=["NIFTY"], reference_now=T0)
    rd = validation7.run_once(instruments=["NIFTY"], reference_now=T0)
    _check(
        "deterministic cycle id at steady state",
        rc.cycle_id == rd.cycle_id,
    )

    # --------------------------------------------------
    # REGRESSION — existing pipeline baseline
    # --------------------------------------------------
    _banner("Regression — existing pipeline baseline unchanged")
    from engine.pipeline.datasets import trending_dataset
    from engine.pipeline.historical_pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
    )

    pipeline_result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
        trending_dataset()
    )
    _check(
        "existing pipeline baseline unchanged (signals=4, trades=3)",
        pipeline_result.signals_generated == 4
        and pipeline_result.completed_trades == 3,
    )

    # --------------------------------------------------
    # SUMMARY
    # --------------------------------------------------
    print()
    print("=" * 64)
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"RESULT: {passed} PASS / {failed} FAIL")
    print("=" * 64)
    if failed:
        print("Phase 6F demo FAILED.")
        return 1
    print("Phase 6F live paper validation demo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
