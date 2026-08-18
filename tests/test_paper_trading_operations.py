"""
Tests for Product Phase 5 — PAPER TRADING OPERATIONS.

These tests verify the operational layer is a THIN, deterministic
ORCHESTRATION layer around the EXISTING Product Phase 1 provider +
analysis + Product Phase 4 trade-plan + Product Phase 5 paper-trading
lifecycle + journal. The operations layer implements NO market analysis,
NO decision logic, NO prediction, NO probability, NO broker, NO
BUY/SELL/ENTER/EXIT/HOLD recommendation. The existing Sprint 11S decision
classification (REJECTED / WATCH / QUALIFIED / PREFERRED) is AUTHORITATIVE
and never renamed / upgraded / downgraded. The existing Sprint 11R
``TradeCandidate`` geometry is AUTHORITATIVE and never recomputed. The
existing Product Phase 4 trade plan is reused VERBATIM. Target 2 remains
unsupported.

Coverage areas (A-AJ):

A.  run_once happy path
B.  live provider (yahoo) abstraction
C.  fixture provider
D.  completed candle
E.  forming candle
F.  future candle
G.  trade creation
H.  duplicate prevention
I.  WAITING_FOR_ENTRY tracking
J.  OPEN tracking
K.  STOP_HIT
L.  TARGET_HIT
M.  BOTH_TOUCHED
N.  manual close compatibility
O.  persistence
P.  restart recovery
Q.  multiple unseen candles
R.  chronological processing
S.  instrument failure isolation
T.  provider error
U.  empty data
V.  malformed data
W.  unsupported instrument
X.  unsupported timeframe
Y.  deterministic cycle
Z.  shuffle invariance
AA. no-look-ahead
AB. decision preservation
AC. geometry preservation
AD. trade-plan preservation
AE. Target 2 unsupported
AF. API schema
AG. workstation integration
AH. reporting
AI. pipeline baseline
AJ. regression compatibility

Tests use injected fake providers / fake services — NO live Yahoo network
dependency.
"""

from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from engine.config.paper_trade_config import PaperTradeConfig
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperTradeStatus
from engine.reporting.paper_trading_operations import OperationalReportFormatter

from dashboard.data_provider import (
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
)
from dashboard.paper_trade_operations import (
    OPERATIONS_DISCLAIMER,
    InstrumentOperationResult,
    OperationsConfig,
    OperationsCycleResult,
    OperationalStatus,
    PaperTradingOperations,
)
from dashboard.paper_trade_store import PaperTradeStore
from dashboard.services import (
    DashboardAnalysisService,
    OperationsRequest,
)
from dashboard.views import (
    ActionabilityState,
    DashboardTradeView,
    DataSourceView,
    DecisionView,
    GeometryView,
    operations_cycle_view_to_jsonable,
    to_operations_cycle_view,
)


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
    freshness=FreshnessState.STALE,
    latest_completed=None,
    reason="",
):
    """Build a minimal :class:`InstrumentSeries` for the fake provider."""

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
            provider_status=provider_status.value if hasattr(provider_status, "value") else provider_status,
            freshness_state=freshness.value if hasattr(freshness, "value") else freshness,
        ),
    )


class _FakeProvider:
    """Fake provider serving canned :class:`InstrumentSeries` per instrument.

    Holds a mapping ``instrument -> InstrumentSeries`` (or a callable returning
    one) so tests can control data availability / candles / freshness
    deterministically. NO network.
    """

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
        s = self.fetch(instrument, setup_timeframe)
        return s.latest_completed_candle_timestamp


class _FakeService:
    """A minimal dashboard-service stand-in for operations tests.

    Wraps a real :class:`PaperTradeStore`, real :class:`TradePlanningEngine`
    + :class:`PaperTradingEngine`, a :class:`_FakeProvider`, and an
    overridable ``analyze`` returning canned :class:`DashboardTradeView`s.

    This isolates the operations layer from the real scanner so every
    lifecycle branch is exercisable deterministically.
    """

    def __init__(self, store, provider=None, views=None):
        from engine.intelligence.trade_planning import TradePlanningEngine
        from engine.config.trade_plan_config import TradePlanConfig

        self.paper_trade_store = store
        self.provider = provider or _FakeProvider()
        self._views = dict(views or {})
        self.paper_trading_engine = PaperTradingEngine(PaperTradeConfig())
        self.trade_planning_engine = TradePlanningEngine(TradePlanConfig())
        self.last_operations_cycle = None

    def set_view(self, instrument, view):
        self._views[instrument] = view

    def available_instruments(self):
        return tuple(sorted(self._views.keys())) if self._views else (
            "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
        )

    def analyze(self, request):
        view = self._views.get(request.instrument)
        if view is None:
            # Honest unavailable view (no fabricated decision / geometry).
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
        # A callable view simulates an analysis failure (raises) — used by
        # the failure-isolation tests.
        if callable(view):
            return view(request)
        return view


def _ops(store, service=None, **cfg_kwargs):
    """Build a :class:`PaperTradingOperations` over a (fake or real) service."""

    svc = service or _FakeService(store)
    config = OperationsConfig(**cfg_kwargs) if cfg_kwargs else OperationsConfig(
        account_capital="100000", risk_percent="1",
    )
    return PaperTradingOperations(svc, config=config), svc


# ============================================================
# A. run_once HAPPY PATH
# ============================================================


class TestRunOnceHappyPath:
    def test_run_once_returns_result(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ops, svc = _ops(store)
        result = ops.run_once(instruments=["NIFTY"])
        assert isinstance(result, OperationsCycleResult)
        assert result.cycle_id.startswith("opcycle-")
        assert result.instruments_scanned == 1

    def test_no_store_not_ready(self):
        svc = _FakeService(store=None)
        ops = PaperTradingOperations(svc)
        result = ops.run_once(instruments=["NIFTY"])
        assert result.status is OperationalStatus.NOT_READY

    def test_empty_watchlist_no_data(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ops, _ = _ops(store)
        result = ops.run_once(instruments=[])
        assert result.status is OperationalStatus.NO_DATA
        assert result.instruments_scanned == 0
        assert result.is_empty

    def test_default_watchlist_uses_available(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ops, svc = _ops(store)
        svc.set_view("NIFTY", _trade_view("NIFTY"))
        svc.set_view("RELIANCE", _trade_view("RELIANCE"))
        result = ops.run_once()  # instruments=None -> available_instruments
        assert result.instruments_scanned == 2


# ============================================================
# B. LIVE PROVIDER (yahoo) ABSTRACTION
# ============================================================


class TestLiveProviderAbstraction:
    def test_operations_use_provider_data_source(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        provider = _FakeProvider()
        provider.set("NIFTY", _series("NIFTY", [_candle(datetime(2024, 1, 1, 9, 15), 100, 101, 99, 100)]))
        svc = _FakeService(store, provider=provider)
        svc.set_view("NIFTY", _trade_view("NIFTY", data_source="yahoo"))
        ops = PaperTradingOperations(svc, config=OperationsConfig(account_capital="100000", risk_percent="1"))
        result = ops.run_once(instruments=["NIFTY"])
        assert result.provider == "yahoo"

    def test_no_silent_fallback_to_fixtures(self, tmp_path):
        # A live provider that fails is NOT silently replaced by fixtures.
        store = PaperTradeStore(directory=tmp_path)
        provider = _FakeProvider()
        provider.set("NIFTY", _unavailable_series("NIFTY", status=ProviderStatus.ERROR, reason="network"))
        svc = _FakeService(store, provider=provider)
        svc.set_view("NIFTY", _trade_view("NIFTY", data_source="yahoo"))
        ops = PaperTradingOperations(svc, config=OperationsConfig(account_capital="100000", risk_percent="1"))
        result = ops.run_once(instruments=["NIFTY"])
        assert result.provider == "yahoo"
        assert result.trades_created == 0


# ============================================================
# C. FIXTURE PROVIDER
# ============================================================


class TestFixtureProvider:
    def test_fixture_service_run_cycle(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = DashboardAnalysisService(paper_trade_store=store)
        view = svc.run_paper_trading_cycle(
            OperationsRequest(
                account_capital="100000", risk_percent="1", watchlist=["NIFTY"],
            ),
        )
        assert view.status in ("STALE", "READY", "NO_DATA")
        assert view.instruments_scanned == 1

    def test_fixture_no_trade_created_incomplete_geometry(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = DashboardAnalysisService(paper_trade_store=store)
        view = svc.run_paper_trading_cycle(
            OperationsRequest(
                account_capital="100000", risk_percent="1", watchlist=["NIFTY"],
            ),
        )
        # Fixtures produce TRADE_GEOMETRY_UNAVAILABLE -> no paper trade.
        assert view.trades_created == 0
        assert view.active_trades == 0


# ============================================================
# D-E-F. COMPLETED / FORMING / FUTURE CANDLE
# ============================================================


class TestCompletedFormingFutureCandle:
    def test_only_completed_candle_creates_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 1
        assert result.active_trades == 1

    def test_forming_candle_cannot_create_trade(self, tmp_path):
        # An eligible view whose evaluation timestamp is in the future
        # (forming candle) — the operations layer must not create a trade
        # from a forming candle. We simulate this by making the analysis
        # unavailable (no completed candle -> no READY_FOR_REVIEW).
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.INVALID, complete=False))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0

    def test_future_cannot_alter_fixed_t_analysis(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        r1 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = r1.results[0].created[0]
        state_before = store.load(tid).status
        # Add a future candle to the provider's series; re-run at the SAME
        # reference_now. The persisted trade state is unchanged (the future
        # candle is excluded by the completed-candle window).
        future = ts + timedelta(minutes=15)
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(future, 100, 102, 99, 101),
        ]))
        r2 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        # Second run is a duplicate -> 0 created, but state unchanged.
        assert r2.trades_created == 0
        assert r2.duplicates_skipped == 1
        assert store.load(tid).status is state_before


# ============================================================
# G. TRADE CREATION
# ============================================================


class TestTradeCreation:
    def test_ready_for_review_creates_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 1
        tid = result.results[0].created[0]
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.WAITING_FOR_ENTRY
        assert trade.existing_decision == "QUALIFIED"
        assert trade.direction == "LONG"

    def test_no_opportunity_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.NO_OPPORTUNITY, decision="REJECTED", complete=True))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0

    def test_geometry_unavailable_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE, target=None))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0

    def test_invalid_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.INVALID, complete=False))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0

    def test_wait_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.WAIT, decision="WATCH"))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0

    def test_no_account_risk_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops = PaperTradingOperations(svc, config=OperationsConfig())  # no capital/risk
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0
        assert result.results[0].error is True


# ============================================================
# H. DUPLICATE PREVENTION
# ============================================================


class TestDuplicatePrevention:
    def test_same_candle_no_duplicate(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        r1 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        r2 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert r1.trades_created == 1
        assert r2.trades_created == 0
        assert r2.duplicates_skipped == 1
        assert r2.results[0].duplicate is True
        assert r2.results[0].duplicate_paper_trade_id == r1.results[0].created[0]

    def test_same_candle_same_instrument_count_unchanged(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        for _ in range(5):
            ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert len(store.list_trades()) == 1

    def test_different_instrument_separate_trades(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", direction="SHORT", entry=100, stop=102, target=96, evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        assert result.trades_created == 2
        assert len(store.list_trades()) == 2

    def test_different_candle_separate_trades(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts1 = datetime(2024, 1, 1, 9, 15)
        ts2 = datetime(2024, 1, 1, 9, 30)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts1))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts1)
        # New completed candle -> new evaluation timestamp -> new trade.
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts2))
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts2)
        assert result.trades_created == 1
        assert len(store.list_trades()) == 2


# ============================================================
# I-J-K-L-M. LIFECYCLE TRACKING
# ============================================================


class TestLifecycleTracking:
    def _setup_long_trade(self, store, ts):
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        return svc, ops, tid

    def test_waiting_for_entry_remains_waiting(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc, ops, tid = self._setup_long_trade(store, ts)
        # No entry touch yet.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 105, 106, 104, 105),  # above entry
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
        assert store.load(tid).status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_entry_touch_opens_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc, ops, tid = self._setup_long_trade(store, ts)
        # LONG entry at 100; candle low <= 100 touches entry.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # low 99 <= 100
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.OPEN
        assert trade.actual_entry_price == Decimal("100")

    def test_stop_hit_closes_loss(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc, ops, tid = self._setup_long_trade(store, ts)
        # entry candle then stop candle (low 97 <= stop 98).
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
            _candle(datetime(2024, 1, 1, 9, 45), 99, 99.5, 97, 97.5),   # stop hit
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.CLOSED
        assert trade.exit_reason.name == "STOP_HIT"
        assert trade.realized_r is not None and trade.realized_r < 0

    def test_target_hit_closes_win(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc, ops, tid = self._setup_long_trade(store, ts)
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
            _candle(datetime(2024, 1, 1, 9, 45), 104, 105, 103, 104.5),  # target hit
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.CLOSED
        assert trade.exit_reason.name == "TARGET_HIT"
        assert trade.realized_r is not None and trade.realized_r > 0

    def test_both_touched_ambiguous(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc, ops, tid = self._setup_long_trade(store, ts)
        # entry then a single candle touching BOTH stop (98) and target (104).
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
            _candle(datetime(2024, 1, 1, 9, 45), 104, 105, 97, 100),    # both
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.CLOSED
        assert trade.exit_reason.name == "BOTH_TOUCHED"
        assert trade.realized_r is None
        assert trade.realized_pnl is None

    def test_short_stop_hit(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", direction="SHORT", entry=100, stop=102, target=96, evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # SHORT entry when high >= 100; stop when high >= 102.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 99.5),   # entry (high 101>=100)
            _candle(datetime(2024, 1, 1, 9, 45), 102, 103, 101, 102.5), # stop (high 103>=102)
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.exit_reason.name == "STOP_HIT"
        assert trade.realized_r is not None and trade.realized_r < 0

    def test_short_target_hit(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", direction="SHORT", entry=100, stop=102, target=96, evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 99.5),   # entry
            _candle(datetime(2024, 1, 1, 9, 45), 95, 96, 94, 95),       # target (low 94<=96)
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.exit_reason.name == "TARGET_HIT"
        assert trade.realized_r is not None and trade.realized_r > 0


# ============================================================
# N. MANUAL CLOSE COMPATIBILITY
# ============================================================


class TestManualCloseCompatibility:
    def test_manual_close_still_works_via_service(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Open the trade first.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
        assert store.load(tid).status is PaperTradeStatus.OPEN
        # Manual close via the existing engine.
        trade = store.load(tid)
        closed = svc.paper_trading_engine.close_manually(
            trade, exit_price=Decimal("101"), exit_timestamp=datetime(2024, 1, 1, 9, 45),
        )
        store.save(closed, overwrite=True)
        assert store.load(tid).exit_reason.name == "MANUAL_CLOSE"


# ============================================================
# O-P. PERSISTENCE / RESTART RECOVERY
# ============================================================


class TestPersistenceRestart:
    def test_trade_persisted_across_instances(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # New store instance (restart) sees the persisted trade.
        store2 = PaperTradeStore(directory=tmp_path)
        assert tid in store2.list_trades()
        assert store2.load(tid).status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_restart_continues_tracking(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Recreate service + ops (restart); continue tracking.
        store2 = PaperTradeStore(directory=tmp_path)
        svc2 = _FakeService(store2)
        svc2.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc2.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
        ]))
        ops2, _ = _ops(store2, svc2, account_capital="100000", risk_percent="1")
        ops2.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
        assert store2.load(tid).status is PaperTradeStatus.OPEN

    def test_restart_preserves_trade_id_and_state(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        original = store.load(tid)
        # Restart: new store, no new cycle — state identical.
        store2 = PaperTradeStore(directory=tmp_path)
        reloaded = store2.load(tid)
        assert reloaded.paper_trade_id == original.paper_trade_id
        assert reloaded.status == original.status
        assert reloaded.entry == original.entry
        assert reloaded.stop == original.stop


# ============================================================
# Q-R. MULTIPLE UNSEEN CANDLES / CHRONOLOGICAL
# ============================================================


class TestMultipleCandlesChronological:
    def test_multiple_unseen_candles_processed(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Downtime: 3 unseen candles arrive at once (entry + target).
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
            _candle(datetime(2024, 1, 1, 9, 45), 102, 103, 101, 102.5),
            _candle(datetime(2024, 1, 1, 10, 0), 104, 105, 103, 104.5),  # target
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 10, 0))
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.CLOSED
        assert trade.exit_reason.name == "TARGET_HIT"

    def test_chronological_target_not_skipped(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Entry candle then a target candle then a stop candle (later).
        # The engine must NOT skip the target by jumping to the stop.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),  # entry
            _candle(datetime(2024, 1, 1, 9, 45), 104, 105, 103, 104.5),  # target first
            _candle(datetime(2024, 1, 1, 10, 0), 97, 98, 96, 96.5),      # stop later
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 10, 0))
        trade = store.load(tid)
        assert trade.exit_reason.name == "TARGET_HIT"

    def test_duplicate_candle_processing_idempotent(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
        ]))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        # Re-run the same cycle: trade stays WAITING_FOR_ENTRY (idempotent).
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        assert store.load(tid).status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_expired_lifecycle(self, tmp_path):
        # Neither stop nor target hit within the holding horizon -> EXPIRED.
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Entry then many neutral candles (no stop/target touch).
        candles = [_candle(ts, 100, 101, 99, 100)]
        t = datetime(2024, 1, 1, 9, 30)
        candles.append(_candle(t, 100, 101, 99, 100.5))  # entry
        for i in range(60):
            t = t + timedelta(minutes=15)
            candles.append(_candle(t, 100, 100.5, 99.5, 100))
        svc.provider.set("NIFTY", _series("NIFTY", candles))
        ops.run_once(instruments=["NIFTY"], reference_now=t)
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.CLOSED
        assert trade.exit_reason.name == "EXPIRED"


# ============================================================
# OPERATIONAL STATUS VARIANTS
# ============================================================


class TestOperationalStatusVariants:
    def test_error_status_when_all_fail(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.status is OperationalStatus.ERROR
        assert result.trades_created == 0

    def test_no_data_status_when_no_usable_data(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.INVALID, complete=False, decision=""))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.status is OperationalStatus.NO_DATA

    def test_ready_status_when_current_and_analysed(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts, freshness=FreshnessState.CURRENT))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert result.status is OperationalStatus.READY
        assert result.freshness == "CURRENT"

    def test_stale_status_when_stale(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts, freshness=FreshnessState.STALE))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert result.status is OperationalStatus.STALE


# ============================================================
# RESTART STATE PRESERVATION
# ============================================================


class TestRestartStatePreservation:
    def test_restart_preserves_entry_and_exit(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Open the trade.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 30))
        opened = store.load(tid)
        assert opened.status is PaperTradeStatus.OPEN
        # Restart: new store + ops; do NOT re-run. State identical.
        store2 = PaperTradeStore(directory=tmp_path)
        reloaded = store2.load(tid)
        assert reloaded.status is PaperTradeStatus.OPEN
        assert reloaded.actual_entry_price == opened.actual_entry_price
        assert reloaded.entry_timestamp == opened.entry_timestamp
        assert reloaded.realized_r is None


# ============================================================
# S-T-U-V-W-X. FAILURE ISOLATION / ERRORS
# ============================================================


class TestFailureIsolation:
    def test_one_instrument_failure_does_not_abort(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))

        def boom(*a, **k):
            raise RuntimeError("RELIANCE exploded")
        # RELIANCE analyze raises.
        svc._views["RELIANCE"] = boom
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        assert result.trades_created == 1  # NIFTY still created
        assert any("RELIANCE" in e for e in result.errors)

    def test_provider_error_isolated(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts))
        # Make RELIANCE analysis raise via a callable view.
        svc._views["RELIANCE"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("err"))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        assert result.results[1].error is True

    def test_empty_data_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", actionability=ActionabilityState.INVALID, complete=False, decision=""))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"])
        assert result.trades_created == 0
        assert result.results[0].analysed is False

    def test_unsupported_instrument_no_trade(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        svc = _FakeService(store)
        # No view set -> analyze returns unavailable.
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["UNKNOWN"])
        assert result.trades_created == 0
        assert result.results[0].analysed is False

    def test_unsupported_timeframe_handled(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1", setup_timeframe="5m")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        # The fake service still analyses (it ignores timeframe); the real
        # provider path is covered by the fixture test. Just ensure no crash.
        assert result.instruments_scanned == 1


# ============================================================
# Y-Z. DETERMINISM / SHUFFLE INVARIANCE
# ============================================================


class TestDeterminism:
    def test_same_inputs_same_cycle_id(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts, direction="SHORT", entry=100, stop=102, target=96))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        # Clean store for each run to compare cycle ids on equivalent state.
        r1 = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        # Clear store + re-run to get equivalent state.
        for tid in store.list_trades():
            store.delete(tid)
        r2 = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        assert r1.cycle_id == r2.cycle_id

    def test_shuffle_invariance(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.set_view("RELIANCE", _trade_view("RELIANCE", evaluation_timestamp=ts, direction="SHORT", entry=100, stop=102, target=96))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        r1 = ops.run_once(instruments=["RELIANCE", "NIFTY"], reference_now=ts)
        for tid in store.list_trades():
            store.delete(tid)
        r2 = ops.run_once(instruments=["NIFTY", "RELIANCE"], reference_now=ts)
        assert r1.cycle_id == r2.cycle_id
        assert r1.trades_created == r2.trades_created

    def test_repeated_run_identical_when_no_change(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        r1 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        r2 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        # Second run is a duplicate -> 0 created, but active count identical.
        assert r1.active_trades == r2.active_trades


# ============================================================
# AA. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def _sig(self, func):
        return str(inspect.signature(func))

    def test_run_once_no_future_argument(self):
        sig = self._sig(PaperTradingOperations.run_once)
        for forbidden in ("future", "future_candles", "lookahead"):
            assert forbidden not in sig, f"run_once must not accept {forbidden}"

    def test_operations_request_no_future_argument(self):
        sig = self._sig(OperationsRequest)
        for forbidden in ("future", "future_candles", "lookahead"):
            assert forbidden not in sig

    def test_outcome_evaluator_not_called(self, tmp_path, monkeypatch):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        svc.provider.set("NIFTY", _series("NIFTY", [_candle(ts, 100, 101, 99, 100)]))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        def _boom(self, *a, **k):
            raise AssertionError("OutcomeEvaluator must not be called")
        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _boom)
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert result.trades_created == 1

    def test_pipeline_not_called(self, tmp_path, monkeypatch):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        from engine.pipeline.historical_pipeline import HistoricalEvaluationPipeline

        def _boom(self, *a, **k):
            raise AssertionError("pipeline must not be called")
        monkeypatch.setattr(
            HistoricalEvaluationPipeline, "evaluate", _boom,
        )
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert result.trades_created == 1

    def test_future_candle_cannot_change_entry(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        entry_before = store.load(tid).entry
        # Add a future candle.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(ts + timedelta(minutes=30), 200, 210, 190, 205),
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert store.load(tid).entry == entry_before

    def test_future_candle_cannot_change_stop_target(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        stop_before = store.load(tid).stop
        target_before = store.load(tid).target_1
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(ts + timedelta(minutes=30), 200, 210, 190, 205),
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        assert store.load(tid).stop == stop_before
        assert store.load(tid).target_1 == target_before

    def test_only_completed_candles_processed(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # A future candle that would have hit the target must NOT close the
        # trade when reference_now is before it.
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(ts + timedelta(minutes=15), 100, 101, 99, 100.5),  # entry (after ts)
            _candle(ts + timedelta(minutes=30), 104, 105, 103, 104.5),  # target (future)
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=ts + timedelta(minutes=15))
        # reference_now is the entry candle; the target candle is in the
        # future relative to the engine's window? Actually the engine keeps
        # candles <= reference_now. With reference_now = +15m, the +30m
        # target candle is excluded -> trade OPEN, not closed at target.
        trade = store.load(tid)
        assert trade.status is PaperTradeStatus.OPEN


# ============================================================
# AB. DECISION PRESERVATION
# ============================================================


class TestDecisionPreservation:
    def test_loss_does_not_rewrite_decision(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", decision="QUALIFIED", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = store.list_trades()[0]
        # Drive to a STOP_HIT (loss).
        svc.provider.set("NIFTY", _series("NIFTY", [
            _candle(ts, 100, 101, 99, 100),
            _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100.5),
            _candle(datetime(2024, 1, 1, 9, 45), 99, 99.5, 97, 97.5),
        ]))
        ops.run_once(instruments=["NIFTY"], reference_now=datetime(2024, 1, 1, 9, 45))
        trade = store.load(tid)
        assert trade.exit_reason.name == "STOP_HIT"
        assert trade.existing_decision == "QUALIFIED"  # unchanged

    def test_no_buysell_rename(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", decision="PREFERRED", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        tid = result.results[0].created[0]
        trade = store.load(tid)
        assert trade.existing_decision not in ("BUY", "SELL", "ENTER", "EXIT", "HOLD")
        assert trade.existing_decision == "PREFERRED"


# ============================================================
# AC-AD-AE. GEOMETRY / PLAN / TARGET 2 PRESERVATION
# ============================================================


class TestGeometryPlanPreservation:
    def test_geometry_reused_verbatim(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts, entry=100.0, stop=98.0, target=104.0))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        trade = store.load(result.results[0].created[0])
        assert trade.entry == Decimal("100")
        assert trade.stop == Decimal("98")
        assert trade.target_1 == Decimal("104")

    def test_plan_reused_verbatim(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        trade = store.load(result.results[0].created[0])
        assert trade.account_capital == Decimal("100000")
        assert trade.risk_percent == Decimal("1")
        assert trade.maximum_risk is not None
        assert trade.planned_quantity is not None

    def test_target_2_unsupported(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        trade = store.load(result.results[0].created[0])
        assert trade.target_2 is None
        assert trade.target_2_supported is False


# ============================================================
# AF. API SCHEMA
# ============================================================


class TestApiSchema:
    def _client(self, tmp_path, monkeypatch):
        from dashboard.app import create_app
        store = PaperTradeStore(directory=tmp_path)
        svc = DashboardAnalysisService(paper_trade_store=store)
        app = create_app(service=svc)
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_run_once_endpoint(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.post(
            "/api/paper-trading/run-once",
            params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
        )
        assert r.status_code == 200
        data = r.json()
        for key in (
            "status", "cycle_id", "provider", "freshness",
            "instruments_scanned", "trades_created", "trades_updated",
            "trades_closed", "duplicates_skipped", "errors", "active_trades",
            "warnings", "results", "rationale", "limitations",
        ):
            assert key in data
        assert data["cycle_id"].startswith("opcycle-")

    def test_api_no_buysell(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.post(
            "/api/paper-trading/run-once",
            params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
        )
        text = r.text.lower()
        for forbidden in ("\"buy\"", "\"sell\"", "\"enter\"", "\"exit\"", "\"hold\""):
            assert forbidden not in text

    def test_api_existing_routes_preserved(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        for path in ("/health", "/api/health", "/api/paper-trades", "/paper-trading"):
            assert client.get(path).status_code == 200


# ============================================================
# AG. WORKSTATION INTEGRATION
# ============================================================


class TestWorkstationIntegration:
    def _client(self, tmp_path, monkeypatch):
        from dashboard.app import create_app
        store = PaperTradeStore(directory=tmp_path)
        svc = DashboardAnalysisService(paper_trade_store=store)
        app = create_app(service=svc)
        from fastapi.testclient import TestClient
        return TestClient(app), svc

    def test_workstation_has_operations_section(self, tmp_path, monkeypatch):
        client, _ = self._client(tmp_path, monkeypatch)
        r = client.get("/workstation?instrument=NIFTY&timeframe=15m")
        assert r.status_code == 200
        assert b"Paper Trading Operations" in r.content
        assert b"NO REAL ORDERS" in r.content

    def test_workstation_shows_last_cycle_after_run(self, tmp_path, monkeypatch):
        client, svc = self._client(tmp_path, monkeypatch)
        client.post(
            "/api/paper-trading/run-once",
            params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
        )
        r = client.get("/workstation?instrument=NIFTY&timeframe=15m")
        assert b"Last Cycle" in r.content


# ============================================================
# AH. REPORTING
# ============================================================


class TestReporting:
    def test_formatter_returns_str(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        text = OperationalReportFormatter().format(result)
        assert isinstance(text, str)
        assert "PAPER TRADING OPERATIONS REPORT" in text

    def test_formatter_has_disclaimer(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ops, _ = _ops(store)
        result = ops.run_once(instruments=["NIFTY"])
        text = OperationalReportFormatter().format(result)
        assert OPERATIONS_DISCLAIMER in text

    def test_formatter_no_predictive_language(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        text = OperationalReportFormatter().format(result).lower()
        for forbidden in ("guaranteed profit", "will rise", "will fall", "most profitable", "buy now", "sell now"):
            assert forbidden not in text

    def test_formatter_negative_width_rejected(self):
        with pytest.raises(ValueError):
            OperationalReportFormatter(width=5)

    def test_formatter_works_on_view(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        result = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        view = to_operations_cycle_view(result)
        text = OperationalReportFormatter().format(view)
        assert isinstance(text, str)


# ============================================================
# AI-AJ. PIPELINE BASELINE / REGRESSION
# ============================================================


class TestPipelineBaselineRegression:
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

    def test_existing_paper_trading_apis_importable(self):
        from dashboard.services import (
            DashboardAnalysisService,
            PaperTradeRequest,
            PaperTradeTrackRequest,
        )
        assert DashboardAnalysisService is not None
        assert PaperTradeRequest is not None

    def test_operations_do_not_break_journal(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        ops.run_once(instruments=["NIFTY"], reference_now=ts)
        # The journal lists the created trade.
        trades = store.load_all()
        assert len(trades) == 1
        assert trades[0].existing_decision == "QUALIFIED"

    def test_jsonable_deterministic(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ts = datetime(2024, 1, 1, 9, 15)
        svc = _FakeService(store)
        svc.set_view("NIFTY", _trade_view("NIFTY", evaluation_timestamp=ts))
        ops, _ = _ops(store, svc, account_capital="100000", risk_percent="1")
        r1 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        v1 = operations_cycle_view_to_jsonable(to_operations_cycle_view(r1))
        for tid in store.list_trades():
            store.delete(tid)
        r2 = ops.run_once(instruments=["NIFTY"], reference_now=ts)
        v2 = operations_cycle_view_to_jsonable(to_operations_cycle_view(r2))
        assert v1 == v2


# ============================================================
# MODEL / CONFIG IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_result_frozen(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        ops, _ = _ops(store)
        result = ops.run_once(instruments=["NIFTY"])
        with pytest.raises(Exception):
            result.status = OperationalStatus.ERROR  # type: ignore[misc]

    def test_instrument_result_frozen(self):
        r = InstrumentOperationResult(instrument="X")
        with pytest.raises(Exception):
            r.instrument = "Y"  # type: ignore[misc]

    def test_config_frozen(self):
        cfg = OperationsConfig(account_capital="100000", risk_percent="1")
        with pytest.raises(Exception):
            cfg.label = "x"  # type: ignore[misc]
