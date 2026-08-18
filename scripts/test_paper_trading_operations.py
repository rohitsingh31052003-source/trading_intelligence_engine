"""
Paper Trading Operations demo (Product Phase 5 operational increment).

Proves the operational layer is a THIN, deterministic ORCHESTRATION layer
around the EXISTING Product Phase 1 provider + analysis + Product Phase 4
trade-plan + Product Phase 5 paper-trading lifecycle + journal. The layer
implements NO market analysis, NO decision logic, NO prediction, NO
probability, NO broker, NO BUY/SELL/ENTER/EXIT/HOLD recommendation. The
existing Sprint 11S decision classification is AUTHORITATIVE and never
renamed / upgraded / downgraded. The existing Sprint 11R trade geometry is
AUTHORITATIVE and never recomputed. The existing Product Phase 4 trade plan
is reused VERBATIM. Target 2 remains unsupported.

Visibly demonstrates (1-18):

1.  real / near-live provider abstraction
2.  one operational cycle
3.  completed-candle enforcement
4.  trade creation
5.  duplicate prevention
6.  entry tracking
7.  stop / target tracking
8.  BOTH_TOUCHED honesty
9.  persistence
10. restart recovery
11. multiple-candle recovery
12. failure isolation
13. no-look-ahead
14. decision preservation
15. geometry preservation
16. trade-plan preservation
17. workstation / API integration
18. deterministic behavior

Every demo check prints explicit PASS / FAIL. The demo exits 0 on success.
Paper trading is DESCRIPTIVE ONLY — it does NOT predict, does NOT guarantee
profitability, does NOT place any real order, and does NOT constitute
financial advice.

Run::

    python scripts/test_paper_trading_operations.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import FreshnessState, InstrumentSeries, ProviderStatus
from dashboard.paper_trade_operations import (
    OperationalStatus,
    OperationsConfig,
    PaperTradingOperations,
)
from dashboard.paper_trade_store import PaperTradeStore
from dashboard.services import DashboardAnalysisService, OperationsRequest
from dashboard.views import (
    ActionabilityState,
    DashboardTradeView,
    DataSourceView,
    DecisionView,
    GeometryView,
    OperationsCycleView,
    operations_cycle_view_to_jsonable,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperTradeStatus
from engine.reporting.paper_trading_operations import OperationalReportFormatter


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
    print("=" * 60)
    print(title)
    print("=" * 60)


def _candle(ts, o, h, l, c, v=1000.0):
    return OHLCVCandle(ts, o, h, l, c, v)


# ============================================================
# FAKE PROVIDER + SERVICE (NO NETWORK)
# ============================================================


class _FakeProvider:
    DATA_SOURCE = "fake-live"

    def __init__(self):
        self._series = {}
        self.freshness_config = None

    def set(self, instrument, candles, *, available=True, freshness=FreshnessState.CURRENT):
        candles = tuple(candles)
        latest = candles[-1].timestamp if candles else None
        self._series[instrument] = InstrumentSeries(
            instrument=instrument,
            setup_candles=candles,
            context_candles=(),
            available=available and bool(candles),
            data_source=self.DATA_SOURCE,
            provider_status=ProviderStatus.OK,
            freshness_state=freshness,
            latest_candle_timestamp=latest,
            latest_completed_candle_timestamp=latest,
        )

    def is_timeframe_supported(self, tf):
        return tf in ("15M", "15m")

    def fetch(self, instrument, setup_timeframe, lookback_bars=300, *, reference_now=None):
        return self._series.get(
            instrument,
            InstrumentSeries(
                instrument=instrument, available=False, data_source=self.DATA_SOURCE,
                provider_status=ProviderStatus.UNSUPPORTED,
                freshness_state=FreshnessState.UNAVAILABLE,
            ),
        )


class _FakeService:
    """Minimal dashboard-service stand-in for the demo (NO network)."""

    def __init__(self, store, provider=None):
        from engine.intelligence.trade_planning import TradePlanningEngine
        from engine.config.trade_plan_config import TradePlanConfig

        self.paper_trade_store = store
        self.provider = provider or _FakeProvider()
        self._views = {}
        self.paper_trading_engine = PaperTradingEngine_for_demo()
        self.trade_planning_engine = TradePlanningEngine(TradePlanConfig())
        self.last_operations_cycle = None

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
                    data_source="fake-live",
                    provider_status=ProviderStatus.UNSUPPORTED,
                    freshness_state=FreshnessState.UNAVAILABLE,
                ),
            )
        # A callable view simulates an analysis failure (raises) — used by
        # the failure-isolation demo.
        if callable(view):
            return view(request)
        return view


def PaperTradingEngine_for_demo():
    from engine.intelligence.paper_trading import PaperTradingEngine
    from engine.config.paper_trade_config import PaperTradeConfig
    return PaperTradingEngine(PaperTradeConfig())


def _trade_view(instrument, *, decision="QUALIFIED", direction="LONG",
                entry=100.0, stop=98.0, target=104.0, eval_ts=None,
                actionability=ActionabilityState.READY_FOR_REVIEW):
    geom = entry is not None and stop is not None and target is not None
    return DashboardTradeView(
        instrument=instrument,
        context_timeframe="1D",
        setup_timeframe="15m",
        evaluation_timestamp=eval_ts,
        scan_status="OPPORTUNITIES_FOUND",
        complete=True,
        decision=DecisionView(
            decision_classification=decision, decision_score=80,
            opportunity_status="BEST_OPPORTUNITY", rank=1, eligible=True,
            confluence_score=4, rationale="",
        ),
        geometry=GeometryView(
            direction=direction, entry=entry, stop=stop, target_1=target,
            target_2=None, target_2_supported=False, risk_distance=2.0,
            reward_distance=4.0, risk_reward_ratio=2.0, invalidation_level=stop,
            geometry_available=geom, geometry_complete_source=geom,
        ),
        setup_type="TREND_CONTINUATION",
        actionability=actionability,
        data_source=DataSourceView(
            data_source="fake-live", provider_status="OK", freshness_state="CURRENT",
        ),
    )


def _ops(store, provider):
    svc = _FakeService(store, provider)
    cfg = OperationsConfig(account_capital="100000", risk_percent="1")
    return PaperTradingOperations(svc, config=cfg), svc


# ============================================================
# DEMO SCENARIOS
# ============================================================


def _fresh_dir():
    return tempfile.mkdtemp(prefix="paper_trading_ops_")


def demo_provider_abstraction_and_cycle(tmpdir):
    _banner("1-2. Provider abstraction + one operational cycle")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    _check("provider is fake-live", result.provider == "fake-live")
    _check("cycle_id is deterministic", result.cycle_id.startswith("opcycle-"))
    _check("one instrument scanned", result.instruments_scanned == 1)


def demo_completed_candle_enforcement(tmpdir):
    _banner("3. Completed-candle enforcement")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    _check("trade created from completed candle", result.trades_created == 1)
    # A forming candle (no completed eval timestamp) -> no trade.
    svc.set_view("RELIANCE", _trade_view("RELIANCE", eval_ts=None, actionability=ActionabilityState.INVALID))
    result2 = ops.run_once(instruments=["RELIANCE"], reference_now=ts)
    _check("forming / invalid -> no trade", result2.trades_created == 0)


def demo_trade_creation_and_duplicate_prevention(tmpdir):
    _banner("4-5. Trade creation + duplicate prevention")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    r1 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    _check("trade created", r1.trades_created == 1)
    r2 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    _check("duplicate prevented (0 created)", r2.trades_created == 0)
    _check("duplicate skipped count", r2.duplicates_skipped == 1)
    _check("only one trade persisted", len(store.list_trades()) == 1)


def demo_entry_stop_target_tracking(tmpdir):
    _banner("6-7. Entry / stop / target tracking")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    ops.run_once(instruments=["NIFTY"], reference_now=ts)
    tid = store.list_trades()[0]
    # Entry + target.
    provider.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
        _candle(datetime(2024, 1, 1, 9, 45), 104, 105, 103, 104.5),  # target
    ])
    ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
    trade = store.load(tid)
    _check("entry confirmed (OPEN->CLOSED target)", trade.status is PaperTradeStatus.CLOSED)
    _check("target hit", trade.exit_reason.name == "TARGET_HIT")
    _check("realized R > 0", trade.realized_r is not None and trade.realized_r > 0)


def demo_both_touched_honesty(tmpdir):
    _banner("8. BOTH_TOUCHED honesty")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    ops.run_once(instruments=["NIFTY"], reference_now=ts)
    tid = store.list_trades()[0]
    provider.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
        _candle(datetime(2024, 1, 1, 9, 45), 104, 105, 97, 100),    # both
    ])
    ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
    trade = store.load(tid)
    _check("both touched ambiguous", trade.exit_reason.name == "BOTH_TOUCHED")
    _check("no fabricated realized R", trade.realized_r is None)
    _check("no fabricated realized P&L", trade.realized_pnl is None)


def demo_persistence_and_restart(tmpdir):
    _banner("9-10. Persistence + restart recovery")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    ops.run_once(instruments=["NIFTY"], reference_now=ts)
    tid = store.list_trades()[0]
    # New store (restart).
    store2 = PaperTradeStore(directory=tmpdir)
    _check("trade persisted across restart", tid in store2.list_trades())
    provider2 = _FakeProvider()
    provider2.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
    ])
    ops2, svc2 = _ops(store2, provider2)
    svc2.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    ops2.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
    _check("restart continues tracking (OPEN)", store2.load(tid).status is PaperTradeStatus.OPEN)


def demo_multiple_candle_recovery(tmpdir):
    _banner("11. Multiple-candle recovery (chronological)")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    ops.run_once(instruments=["NIFTY"], reference_now=ts)
    tid = store.list_trades()[0]
    # Downtime: 3 unseen candles arrive at once (entry + target).
    provider.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
        _candle(datetime(2024, 1, 1, 9, 45), 102, 103, 101, 102.5),
        _candle(datetime(2024, 1, 1, 10, 0), 104, 105, 103, 104.5),  # target
    ])
    ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 10, 0))
    trade = store.load(tid)
    _check("target not skipped (chronological)", trade.exit_reason.name == "TARGET_HIT")


def demo_failure_isolation(tmpdir):
    _banner("12. Failure isolation")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))

    def boom(req):
        raise RuntimeError("RELIANCE exploded")
    svc._views["RELIANCE"] = boom
    result = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
    _check("NIFTY still processed", result.trades_created == 1)
    _check("RELIANCE error isolated", any("RELIANCE" in e for e in result.errors))


def demo_no_lookahead(tmpdir):
    _banner("13. No-look-ahead")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    # Patch OutcomeEvaluator + pipeline to raise.
    from engine.intelligence.historical_outcome import OutcomeEvaluator
    from engine.pipeline.historical_pipeline import HistoricalEvaluationPipeline

    orig_oe = OutcomeEvaluator.evaluate
    orig_hp = HistoricalEvaluationPipeline.evaluate

    def _boom_oe(self, *a, **k):
        raise AssertionError("OutcomeEvaluator must not be called")

    def _boom_hp(self, *a, **k):
        raise AssertionError("pipeline must not be called")

    OutcomeEvaluator.evaluate = _boom_oe
    HistoricalEvaluationPipeline.evaluate = _boom_hp
    try:
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    finally:
        OutcomeEvaluator.evaluate = orig_oe
        HistoricalEvaluationPipeline.evaluate = orig_hp
    _check("operations work with evaluator+pipeline patched to raise", result.trades_created == 1)
    _check("OutcomeEvaluator restored", OutcomeEvaluator.evaluate is orig_oe)
    _check("pipeline restored", HistoricalEvaluationPipeline.evaluate is orig_hp)

    # Future candle cannot alter a fixed-T analysis.
    provider.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(ts + timedelta(minutes=30), 200, 210, 190, 205),
    ])
    state_before = store.load(result.results[0].created[0]).status
    ops.run_once(instruments=["NIFTY"], reference_now=ts)
    _check("future candle does not change fixed-T state", store.load(result.results[0].created[0]).status is state_before)


def demo_decision_geometry_plan_preservation(tmpdir):
    _banner("14-16. Decision / geometry / plan preservation")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", decision="QUALIFIED", eval_ts=ts))
    result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    tid = result.results[0].created[0]
    trade = store.load(tid)
    _check("decision preserved (QUALIFIED)", trade.existing_decision == "QUALIFIED")
    _check("no BUY/SELL rename", trade.existing_decision not in ("BUY", "SELL", "ENTER", "EXIT", "HOLD"))
    _check("geometry entry reused", trade.entry == Decimal("100"))
    _check("geometry stop reused", trade.stop == Decimal("98"))
    _check("geometry target reused", trade.target_1 == Decimal("104"))
    _check("plan capital reused", trade.account_capital == Decimal("100000"))
    _check("plan risk% reused", trade.risk_percent == Decimal("1"))
    _check("target 2 unsupported", trade.target_2 is None and trade.target_2_supported is False)

    # Drive to a LOSS; decision must NOT be rewritten.
    provider.set("NIFTY", [
        _candle(ts, 100, 101, 99, 100),
        _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
        _candle(datetime(2024, 1, 1, 9, 45), 99, 99.5, 97, 97.5),
    ])
    ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
    trade = store.load(tid)
    _check("LOSS does not rewrite decision", trade.existing_decision == "QUALIFIED")
    _check("LOSS exit reason STOP_HIT", trade.exit_reason.name == "STOP_HIT")


def demo_workstation_api_integration(tmpdir):
    _banner("17. Workstation / API integration")
    store = PaperTradeStore(directory=tmpdir)
    svc = DashboardAnalysisService(paper_trade_store=store)
    app = create_app(service=svc)
    client = TestClient(app)
    r = client.get("/workstation?instrument=NIFTY&timeframe=15m")
    _check("workstation renders", r.status_code == 200)
    _check("operations section present", b"Paper Trading Operations" in r.content)
    _check("NO REAL ORDERS banner", b"NO REAL ORDERS" in r.content)
    r = client.post(
        "/api/paper-trading/run-once",
        params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
    )
    _check("run-once endpoint 200", r.status_code == 200)
    data = r.json()
    _check("cycle_id in response", data["cycle_id"].startswith("opcycle-"))
    _check("status in response", "status" in data)
    _check("existing routes preserved", client.get("/api/paper-trades").status_code == 200)
    # Workstation now shows the last cycle.
    r = client.get("/workstation?instrument=NIFTY&timeframe=15m")
    _check("workstation shows last cycle", b"Last Cycle" in r.content)


def demo_determinism(tmpdir):
    _banner("18. Deterministic behavior")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    provider.set("RELIANCE", [_candle(ts, 100, 102, 98, 101)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    svc.set_view("RELIANCE", _trade_view("RELIANCE", direction="SHORT", entry=100, stop=102, target=96, eval_ts=ts))
    r1 = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
    j1 = operations_cycle_view_to_jsonable(OperationsCycleView(
        cycle_id=r1.cycle_id, status=r1.status.value,
    ))
    # Clear + re-run with shuffled order.
    for tid in store.list_trades():
        store.delete(tid)
    r2 = ops.run_once(instruments=["RELIANCE", "NIFTY"], reference_now=ts)
    _check("same cycle_id on equivalent state", r1.cycle_id == r2.cycle_id)
    _check("same trades_created", r1.trades_created == r2.trades_created)
    _check("jsonable deterministic", j1["cycle_id"] == r2.cycle_id)


def demo_reporting(tmpdir):
    _banner("Reports")
    store = PaperTradeStore(directory=tmpdir)
    provider = _FakeProvider()
    ts = datetime(2024, 1, 1, 9, 15)
    provider.set("NIFTY", [_candle(ts, 100, 101, 99, 100)])
    ops, svc = _ops(store, provider)
    svc.set_view("NIFTY", _trade_view("NIFTY", eval_ts=ts))
    result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
    from dashboard.views import to_operations_cycle_view
    text = OperationalReportFormatter().format(to_operations_cycle_view(result))
    _check("formatter returns str", isinstance(text, str))
    _check("report has disclaimer", "paper trading only" in text.lower())
    _check("no predictive language", "guaranteed profit" not in text.lower())
    print("\n--- Sample operations report ---")
    print(text)


def demo_pipeline_baseline():
    _banner("Pipeline baseline (signals=4, trades=3)")
    from engine.pipeline.datasets import trending_dataset
    from engine.pipeline.historical_pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
    )
    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
    _check("signals_generated == 4", result.signals_generated == 4)
    _check("completed_trades == 3", result.completed_trades == 3)


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    print("=" * 60)
    print("PAPER TRADING OPERATIONS DEMO")
    print("=" * 60)
    print("This system performs PAPER TRADING ONLY. No real orders are placed.")
    dirs = []

    def _newdir():
        d = _fresh_dir()
        dirs.append(d)
        return d

    try:
        demo_provider_abstraction_and_cycle(_newdir())
        demo_completed_candle_enforcement(_newdir())
        demo_trade_creation_and_duplicate_prevention(_newdir())
        demo_entry_stop_target_tracking(_newdir())
        demo_both_touched_honesty(_newdir())
        demo_persistence_and_restart(_newdir())
        demo_multiple_candle_recovery(_newdir())
        demo_failure_isolation(_newdir())
        demo_no_lookahead(_newdir())
        demo_decision_geometry_plan_preservation(_newdir())
        demo_workstation_api_integration(_newdir())
        demo_determinism(_newdir())
        demo_reporting(_newdir())
        demo_pipeline_baseline()
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

    print()
    print("=" * 60)
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"Demo checks: {passed} PASS / {failed} FAIL / {len(_CHECKS)} total")
    if failed:
        print("Paper Trading Operations demo FAILED.")
        sys.exit(1)
    print("Paper Trading Operations demo completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
