"""
Paper trading & real-world validation demo (Product Phase 5).

Proves the paper-trading layer is a THIN, honest, deterministic recording
/ validation layer around the EXISTING trade opportunity / decision /
geometry / Phase 4 trade plan. The layer implements NO market analysis,
NO decision logic, NO prediction, NO probability, NO broker, NO
BUY/SELL/ENTER/EXIT/HOLD recommendation. The existing Sprint 11S decision
classification (REJECTED / WATCH / QUALIFIED / PREFERRED) is AUTHORITATIVE
and never renamed / upgraded / downgraded. The existing Sprint 11R
``TradeCandidate`` geometry is AUTHORITATIVE and never recomputed. The
existing Product Phase 4 trade plan is reused VERBATIM.

Visibly demonstrates (1-22):

1.  Paper trade creation
2.  Valid / invalid states
3.  Entry tracking
4.  Stop detection
5.  Target detection
6.  Ambiguous BOTH_TOUCHED behavior
7.  P&L / R calculation
8.  Persistence
9.  Reload
10. Performance aggregation
11. Workstation / API integration
12. No-look-ahead (OutcomeEvaluator + pipeline patched to raise)
13. Future-candle protection
14. Forming-candle protection
15. Decimal accounting
16. Decision preservation (LOSS does not rewrite decision)
17. Trade geometry preservation
18. Trade plan preservation
19. Target 2 remains unsupported
20. Serialization round trip
21. Deterministic ID
22. Pipeline baseline (signals_generated=4, completed_trades=3)

Every demo check prints explicit PASS / FAIL / SKIPPED. The demo exits
0 on success. Paper trading is DESCRIPTIVE ONLY — it does NOT predict,
does NOT guarantee profitability, does NOT place any real order, and
does NOT constitute financial advice.

Run::

    python scripts/test_paper_trading.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.paper_trade_store import PaperTradeStore
from dashboard.services import (
    DashboardAnalysisService,
    PaperTradeManualCloseRequest,
    PaperTradeRequest,
    PaperTradeTrackRequest,
)
from dashboard.views import to_paper_trade_view
from engine.config.paper_trade_config import PaperTradeConfig
from engine.intelligence.paper_trade_performance import (
    PaperTradePerformanceEngine,
)
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.intelligence.paper_trading_serialization import (
    deserialize_paper_trade,
    serialize_paper_trade,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperExitReason, PaperTradeStatus
from engine.reporting.paper_trading import (
    PaperTradeFormatter,
    PaperTradeJournalFormatter,
    PaperTradePerformanceFormatter,
)


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


def _make_long_trade(eng, **kw):
    defaults = dict(
        instrument="NIFTY", timeframe="15m", direction="LONG",
        existing_decision="QUALIFIED", setup_type="TREND_CONTINUATION",
        entry=Decimal("100"), stop=Decimal("98"), target_1=Decimal("104"),
        engine_risk_distance=Decimal("2"), engine_reward_distance=Decimal("4"),
        engine_risk_reward_ratio=Decimal("2"),
        planned_quantity=Decimal("10"), planned_risk=Decimal("20"),
        account_capital=Decimal("1000"), risk_percent=Decimal("2"),
        maximum_risk=Decimal("20"),
        created_at=datetime(2024, 1, 1, 9, 15),
        evaluation_timestamp=datetime(2024, 1, 1, 9, 15),
    )
    defaults.update(kw)
    return eng.create(**defaults)


# ============================================================
# DEMONSTRATIONS
# ============================================================


def demo_creation() -> None:
    _banner("1. Paper trade creation")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    _check("id starts with pt-", t.paper_trade_id.startswith("pt-"))
    _check("status WAITING_FOR_ENTRY", t.status is PaperTradeStatus.WAITING_FOR_ENTRY)
    _check("direction LONG", t.direction == "LONG")
    _check("entry preserved", t.entry == Decimal("100"))
    _check("target_2 unsupported", t.target_2 is None and t.target_2_supported is False)


def demo_valid_invalid_states() -> None:
    _banner("2. Valid / invalid states")
    eng = PaperTradingEngine()
    # Invalid: non-directional -> INVALIDATED.
    inv = _make_long_trade(eng, direction="NONE")
    _check("non-directional -> INVALIDATED", inv.status is PaperTradeStatus.INVALIDATED)
    _check("INVALIDATED has NO_GEOMETRY exit_reason", inv.exit_reason is PaperExitReason.NO_GEOMETRY)
    _check("INVALIDATED no fabricated entry", inv.actual_entry_price is None)
    # Invalid: zero risk -> INVALIDATED.
    inv2 = _make_long_trade(eng, engine_risk_distance=Decimal("0"), stop=Decimal("100"))
    _check("zero risk -> INVALIDATED", inv2.status is PaperTradeStatus.INVALIDATED)


def demo_entry_tracking() -> None:
    _banner("3. Entry tracking")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
    _check("entry -> OPEN", out.status is PaperTradeStatus.OPEN)
    _check("actual entry = reference", out.actual_entry_price == Decimal("100"))
    _check("entry timestamp set", out.entry_timestamp == datetime(2024, 1, 1, 9, 30))


def demo_stop_detection() -> None:
    _banner("4. Stop detection")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97.5)
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    _check("stop -> CLOSED", out.status is PaperTradeStatus.CLOSED)
    _check("exit_reason STOP_HIT", out.exit_reason is PaperExitReason.STOP_HIT)
    _check("exit price = stop", out.actual_exit_price == Decimal("98"))
    _check("realized_r = -1", out.realized_r == Decimal("-1"))


def demo_target_detection() -> None:
    _banner("5. Target detection")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    _check("target -> CLOSED", out.status is PaperTradeStatus.CLOSED)
    _check("exit_reason TARGET_HIT", out.exit_reason is PaperExitReason.TARGET_HIT)
    _check("exit price = target", out.actual_exit_price == Decimal("104"))
    _check("realized_r = 2", out.realized_r == Decimal("2"))


def demo_both_touched() -> None:
    _banner("6. Ambiguous BOTH_TOUCHED behavior")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 97, 101)  # both touched
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    _check("both touched -> CLOSED", out.status is PaperTradeStatus.CLOSED)
    _check("exit_reason BOTH_TOUCHED", out.exit_reason is PaperExitReason.BOTH_TOUCHED)
    _check("realized_r None (no fabrication)", out.realized_r is None)
    _check("realized_pnl None (no fabrication)", out.realized_pnl is None)
    _check("exit price None (no fabrication)", out.actual_exit_price is None)
    _check("not a win", not out.exit_reason.is_win)
    _check("not a loss", not out.exit_reason.is_loss)


def demo_pnl_r() -> None:
    _banner("7. P&L / R calculation")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    # R = (104-100)/2 = 2; P&L = (104-100)*10 = 40
    _check("realized_r = 2", out.realized_r == Decimal("2"))
    _check("realized_pnl = 40", out.realized_pnl == Decimal("40"))
    _check("Decimal type preserved", isinstance(out.realized_r, Decimal) and isinstance(out.realized_pnl, Decimal))
    # Manual close P&L
    t2 = _make_long_trade(eng, created_at=datetime(2024, 1, 2, 9, 15))
    m1 = _candle(datetime(2024, 1, 2, 9, 30), 101, 102, 99, 101)
    opened = eng.track(t2, completed_candles=[m1], reference_now=datetime(2024, 1, 2, 9, 45))
    closed = eng.close_manually(opened, exit_price=Decimal("103"), exit_timestamp=datetime(2024, 1, 2, 10, 0))
    _check("manual close MANUAL_CLOSE", closed.exit_reason is PaperExitReason.MANUAL_CLOSE)
    _check("manual realized_r = 1.5", closed.realized_r == Decimal("1.5"))
    _check("manual realized_pnl = 30", closed.realized_pnl == Decimal("30"))


def demo_persistence(tmpdir) -> None:
    _banner("8. Persistence")
    store = PaperTradeStore(directory=tmpdir)
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    store.save(t)
    _check("exists after save", store.exists(t.paper_trade_id))
    _check("listed", t.paper_trade_id in store.list_trades())


def demo_reload(tmpdir) -> None:
    _banner("9. Reload (restart simulation)")
    store = PaperTradeStore(directory=tmpdir)
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    store.save(t)
    # Simulate restart: a NEW store instance on the same directory.
    store2 = PaperTradeStore(directory=tmpdir)
    loaded = store2.load(t.paper_trade_id)
    _check("reload same id", loaded.paper_trade_id == t.paper_trade_id)
    _check("reload same entry", loaded.entry == t.entry)
    _check("reload same status", loaded.status is t.status)


def demo_performance() -> None:
    _banner("10. Performance aggregation")
    eng = PaperTradingEngine()
    perf_eng = PaperTradePerformanceEngine()
    t1 = _make_long_trade(eng, instrument="A")
    t2 = _make_long_trade(eng, instrument="B", created_at=datetime(2024, 1, 2, 9, 15))
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2_win = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
    c2_loss = _candle(datetime(2024, 1, 2, 9, 45), 99, 100, 97, 97)
    m1 = _candle(datetime(2024, 1, 2, 9, 30), 101, 102, 99, 101)
    win = eng.track(t1, completed_candles=[c1, c2_win], reference_now=datetime(2024, 1, 1, 10, 0))
    loss = eng.track(t2, completed_candles=[m1, c2_loss], reference_now=datetime(2024, 1, 2, 10, 0))
    a = perf_eng.analyze([win, loss])
    _check("total = 2", a.overall.total == 2)
    _check("wins = 1", a.overall.wins == 1)
    _check("losses = 1", a.overall.losses == 1)
    _check("win_rate = 0.5", a.overall.win_rate == 0.5)
    _check("valid_r_count = 2", a.overall.valid_r_count == 2)
    _check("instrument breakdown present", any(
        b.dimension.value == "INSTRUMENT" for b in a.breakdowns
    ))
    text = PaperTradePerformanceFormatter().format(a)
    _check("performance report returns str", isinstance(text, str))


def demo_workstation_api(tmpdir) -> None:
    _banner("11. Workstation / API integration")
    store = PaperTradeStore(directory=tmpdir)
    svc = DashboardAnalysisService(paper_trade_store=store)
    app = create_app(service=svc)
    client = TestClient(app)
    # Existing routes preserved.
    r = client.get("/health")
    _check("health 200", r.status_code == 200)
    r = client.get("/paper-trading")
    _check("paper-trading page 200", r.status_code == 200)
    r = client.get("/api/paper-trades")
    _check("api paper-trades 200", r.status_code == 200)
    _check("empty journal", r.json()["is_empty"] is True)
    # Create + list.
    r = client.post(
        "/api/paper-trades",
        params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
    )
    _check("create paper trade 200", r.status_code == 200)
    pid = r.json()["paper_trade_id"]
    _check("created id starts with pt-", pid.startswith("pt-"))
    r = client.get("/api/paper-trades")
    _check("journal now non-empty", not r.json()["is_empty"])
    # Existing workstation route preserved.
    r = client.get("/workstation")
    _check("workstation route preserved", r.status_code == 200)


def demo_no_lookahead() -> None:
    _banner("12. No-look-ahead (patch-to-raise)")
    from engine.intelligence import historical_outcome as ho
    from engine.pipeline import historical_pipeline as hp
    orig_oe = ho.OutcomeEvaluator.evaluate
    orig_hp = hp.HistoricalEvaluationPipeline.evaluate

    def _boom_oe(*a, **kw):
        raise AssertionError("OutcomeEvaluator must not be called")

    def _boom_hp(*a, **kw):
        raise AssertionError("pipeline must not be called")

    ho.OutcomeEvaluator.evaluate = _boom_oe
    hp.HistoricalEvaluationPipeline.evaluate = _boom_hp
    try:
        eng = PaperTradingEngine()
        t = _make_long_trade(eng)
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        _check("track works with evaluators patched", out.status is PaperTradeStatus.CLOSED)
    finally:
        ho.OutcomeEvaluator.evaluate = orig_oe
        hp.HistoricalEvaluationPipeline.evaluate = orig_hp
    _check("OutcomeEvaluator restored", ho.OutcomeEvaluator.evaluate is orig_oe)
    _check("pipeline restored", hp.HistoricalEvaluationPipeline.evaluate is orig_hp)


def demo_future_candle_protection() -> None:
    _banner("13. Future-candle protection")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)  # target
    res = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    # A future candle that would have hit the stop must NOT alter the resolved trade.
    c_future = _candle(datetime(2024, 1, 1, 10, 30), 99, 100, 95, 95)
    res2 = eng.track(res, completed_candles=[c1, c2, c_future], reference_now=datetime(2024, 1, 1, 11, 0))
    _check("resolved trade unchanged by future candle", res2.exit_reason is PaperExitReason.TARGET_HIT)
    _check("realized_r unchanged", res2.realized_r == Decimal("2"))
    # Earlier state unaffected by future candles.
    first = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
    _check("first state OPEN", first.status is PaperTradeStatus.OPEN)
    c_future2 = _candle(datetime(2024, 1, 1, 11, 0), 99, 100, 95, 95)
    second = eng.track(t, completed_candles=[c1, c_future2], reference_now=datetime(2024, 1, 1, 9, 45))
    _check("future candle excluded (state still OPEN)", second.status is PaperTradeStatus.OPEN)


def demo_forming_candle_protection() -> None:
    _banner("14. Forming-candle protection")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    forming = _candle(datetime(2024, 1, 1, 11, 0), 101, 105, 95, 105)  # ts > ref
    out = eng.track(t, completed_candles=[forming], reference_now=datetime(2024, 1, 1, 9, 45))
    _check("forming candle excluded (still WAITING)", out.status is PaperTradeStatus.WAITING_FOR_ENTRY)


def demo_decimal_accounting() -> None:
    _banner("15. Decimal accounting")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng, entry=Decimal("25000"), stop=Decimal("24800"), target_1=Decimal("25600"),
                         engine_risk_distance=Decimal("200"), engine_reward_distance=Decimal("600"),
                         engine_risk_reward_ratio=Decimal("3"), planned_quantity=Decimal("5"),
                         planned_risk=Decimal("1000"))
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 25010, 25020, 24990, 25010)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 25010, 25700, 25000, 25650)
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    _check("decimal realized_r = 3", out.realized_r == Decimal("3"))
    _check("decimal realized_pnl = 3000", out.realized_pnl == Decimal("3000"))
    _check("Decimal type", isinstance(out.realized_pnl, Decimal))


def demo_decision_preservation() -> None:
    _banner("16. Decision preservation (LOSS does not rewrite decision)")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng, existing_decision="QUALIFIED")
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97)  # stop
    out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    _check("LOSS occurred", out.exit_reason is PaperExitReason.STOP_HIT)
    _check("decision stays QUALIFIED", out.existing_decision == "QUALIFIED")
    _check("no BUY/SELL label", "BUY" not in out.direction and "SELL" not in out.direction)


def demo_geometry_preservation() -> None:
    _banner("17. Trade geometry preservation")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    _check("entry preserved", t.entry == Decimal("100"))
    _check("stop preserved", t.stop == Decimal("98"))
    _check("target preserved", t.target_1 == Decimal("104"))
    _check("risk_distance preserved", t.engine_risk_distance == Decimal("2"))
    _check("reward_distance preserved", t.engine_reward_distance == Decimal("4"))


def demo_plan_preservation() -> None:
    _banner("18. Trade plan preservation")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    _check("planned_quantity preserved", t.planned_quantity == Decimal("10"))
    _check("planned_risk preserved", t.planned_risk == Decimal("20"))
    _check("maximum_risk preserved", t.maximum_risk == Decimal("20"))
    _check("account_capital preserved", t.account_capital == Decimal("1000"))
    _check("risk_percent preserved", t.risk_percent == Decimal("2"))


def demo_target2_unsupported() -> None:
    _banner("19. Target 2 remains unsupported")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    _check("target_2 None", t.target_2 is None)
    _check("target_2_supported False", t.target_2_supported is False)


def demo_serialization() -> None:
    _banner("20. Serialization round trip")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
    closed = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    loaded = deserialize_paper_trade(serialize_paper_trade(closed))
    _check("id round trips", loaded.paper_trade_id == closed.paper_trade_id)
    _check("status round trips", loaded.status is PaperTradeStatus.CLOSED)
    _check("exit_reason round trips", loaded.exit_reason is PaperExitReason.TARGET_HIT)
    _check("realized_r round trips", loaded.realized_r == Decimal("2"))
    _check("deterministic bytes", serialize_paper_trade(closed) == serialize_paper_trade(closed))


def demo_deterministic_id() -> None:
    _banner("21. Deterministic ID")
    eng = PaperTradingEngine()
    kw = dict(
        instrument="NIFTY", timeframe="15m", direction="LONG",
        existing_decision="QUALIFIED", entry=Decimal("100"), stop=Decimal("98"),
        target_1=Decimal("104"), engine_risk_distance=Decimal("2"),
        created_at=datetime(2024, 1, 1, 9, 15),
    )
    t1 = eng.create(**kw)
    t2 = eng.create(**kw)
    _check("same inputs same id", t1.paper_trade_id == t2.paper_trade_id)
    t3 = eng.create(created_at=datetime(2024, 1, 1, 9, 30), **{k: v for k, v in kw.items() if k != "created_at"})
    _check("different created_at different id", t1.paper_trade_id != t3.paper_trade_id)
    t4 = eng.create(sequence=1, **kw)
    _check("distinct sequence distinct id", t1.paper_trade_id != t4.paper_trade_id)


def demo_pipeline_baseline() -> None:
    _banner("22. Pipeline baseline (signals=4, trades=3)")
    from engine.pipeline.datasets import trending_dataset
    from engine.pipeline.historical_pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
    )
    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
    _check("signals_generated == 4", result.signals_generated == 4)
    _check("completed_trades == 3", result.completed_trades == 3)


def demo_reports() -> None:
    _banner("Reports")
    eng = PaperTradingEngine()
    t = _make_long_trade(eng)
    c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
    c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
    closed = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
    fmt_text = PaperTradeFormatter().format(closed)
    _check("formatter returns str", isinstance(fmt_text, str))
    _check("report has disclaimer", "BUY/SELL" in fmt_text or "does NOT guarantee" in fmt_text)
    jrn_text = PaperTradeJournalFormatter().format([closed])
    _check("journal formatter returns str", isinstance(jrn_text, str))
    print("\n--- Sample paper-trade report ---")
    print(fmt_text)


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="paper_trading_demo_")
    try:
        demo_creation()
        demo_valid_invalid_states()
        demo_entry_tracking()
        demo_stop_detection()
        demo_target_detection()
        demo_both_touched()
        demo_pnl_r()
        demo_persistence(tmpdir)
        demo_reload(tmpdir)
        demo_performance()
        demo_workstation_api(tempfile.mkdtemp(prefix="paper_trading_api_"))
        demo_no_lookahead()
        demo_future_candle_protection()
        demo_forming_candle_protection()
        demo_decimal_accounting()
        demo_decision_preservation()
        demo_geometry_preservation()
        demo_plan_preservation()
        demo_target2_unsupported()
        demo_serialization()
        demo_deterministic_id()
        demo_pipeline_baseline()
        demo_reports()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("=" * 60)
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"Demo checks: {passed} PASS / {failed} FAIL / {len(_CHECKS)} total")
    if failed:
        print("Product Phase 5 demo FAILED.")
        sys.exit(1)
    print("Product Phase 5 paper-trading demo completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
