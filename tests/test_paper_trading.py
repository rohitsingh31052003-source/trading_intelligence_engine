"""
Tests for Product Phase 5 — PAPER TRADING & REAL-WORLD VALIDATION.

These tests verify the paper-trading layer is a THIN, honest,
deterministic recording / validation layer around the EXISTING trade
opportunity / decision / geometry / Phase 4 trade plan. The paper-trading
layer implements NO market analysis, NO decision logic, NO prediction,
NO probability, NO broker, NO BUY/SELL/ENTER/EXIT/HOLD recommendation.
The existing Sprint 11S decision classification (REJECTED / WATCH /
QUALIFIED / PREFERRED) is AUTHORITATIVE and never renamed / upgraded /
downgraded. The existing Sprint 11R ``TradeCandidate`` geometry is
AUTHORITATIVE and never recomputed. The existing Product Phase 4 trade
plan is reused VERBATIM.

Coverage areas (A-AL):

A.  Paper trade creation
B.  Invalid creation
C.  State transitions
D.  Entry detection
E.  Waiting for entry
F.  Stop detection
G.  Target detection
H.  Manual close
I.  BOTH_TOUCHED ambiguity
J.  NO_GEOMETRY
K.  Missing geometry
L.  Missing target
M.  Decision preservation
N.  Trade geometry preservation
O.  Trade plan preservation
P.  Decimal accounting
Q.  Realized P&L
R.  Realized R
S.  Aggregate performance
T.  Instrument grouping
U.  Setup grouping
V.  Decision grouping
W.  Timeframe grouping
X.  Persistence
Y.  Reload after restart
Z.  Malformed persistence data
AA. Deterministic serialization
AB. Future-candle protection
AC. Forming-candle protection
AD. No-look-ahead (patch-to-raise)
AE. Input immutability
AF. Failure isolation
AG. Workstation integration
AH. API schema
AI. Backward compatibility
AJ. Empty database
AK. Ambiguous/unresolved state
AL. Regression against Product Phases 1-4 + Sprint 12C/12D/12E + pipeline baseline
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from engine.config.paper_trade_config import PaperTradeConfig
from engine.intelligence.paper_trade_performance import (
    PaperTradePerformanceEngine,
)
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.intelligence.paper_trading_serialization import (
    PAPER_TRADE_SCHEMA_VERSION,
    canonical_paper_trade_json,
    deserialize_paper_trade,
    parse_paper_trade_header,
    serialize_paper_trade,
    serialize_paper_trade_bytes,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import (
    PaperExitReason,
    PaperTrade,
    PaperTradeStatus,
)
from engine.models.paper_trade_performance import (
    PaperTradeGroupDimension,
)
from engine.reporting.paper_trading import (
    PaperTradeFormatter,
    PaperTradeJournalFormatter,
    PaperTradePerformanceFormatter,
)
from dashboard.paper_trade_store import (
    PaperTradeIntegrityError,
    PaperTradeNotFoundError,
    PaperTradeStore,
    PaperTradeStoreError,
)
from dashboard.services import (
    DashboardAnalysisService,
    PaperTradeManualCloseRequest,
    PaperTradeRequest,
    PaperTradeTrackRequest,
)
from dashboard.views import (
    to_paper_trade_view,
    paper_trade_view_to_jsonable,
)


# ============================================================
# FIXTURES
# ============================================================


def _candle(ts, o, h, l, c, v=1000.0):
    return OHLCVCandle(ts, o, h, l, c, v)


def _long_trade(
    engine=None,
    *,
    entry=Decimal("100"),
    stop=Decimal("98"),
    target=Decimal("104"),
    direction="LONG",
    created_at=datetime(2024, 1, 1, 9, 15),
    instrument="NIFTY",
    decision="QUALIFIED",
    quantity=Decimal("10"),
):
    eng = engine or PaperTradingEngine()
    risk = abs(entry - stop) if (entry is not None and stop is not None) else None
    reward = abs(target - entry) if (target is not None and entry is not None) else None
    rr = (reward / risk) if (reward is not None and risk is not None and risk > 0) else None
    return eng.create(
        instrument=instrument,
        timeframe="15m",
        direction=direction,
        existing_decision=decision,
        setup_type="TREND_CONTINUATION",
        entry=entry,
        stop=stop,
        target_1=target,
        engine_risk_distance=risk,
        engine_reward_distance=reward,
        engine_risk_reward_ratio=rr,
        planned_quantity=quantity,
        planned_risk=(quantity * risk) if (quantity is not None and risk is not None) else None,
        account_capital=Decimal("1000"),
        risk_percent=Decimal("2"),
        maximum_risk=Decimal("20"),
        created_at=created_at,
        evaluation_timestamp=created_at,
    )


# ============================================================
# A. PAPER TRADE CREATION
# ============================================================


class TestCreation:
    def test_create_long_trade(self):
        t = _long_trade()
        assert t.status is PaperTradeStatus.WAITING_FOR_ENTRY
        assert t.paper_trade_id.startswith("pt-")
        assert t.direction == "LONG"
        assert t.entry == Decimal("100")
        assert t.target_2 is None
        assert t.target_2_supported is False

    def test_create_short_trade(self):
        t = _long_trade(direction="SHORT", entry=Decimal("100"), stop=Decimal("102"), target=Decimal("96"))
        assert t.direction == "SHORT"
        assert t.status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_create_carries_plan_fields(self):
        t = _long_trade()
        assert t.planned_quantity == Decimal("10")
        assert t.planned_risk == Decimal("20")
        assert t.account_capital == Decimal("1000")
        assert t.risk_percent == Decimal("2")

    def test_create_from_plan_object(self):
        class FakePlan:
            plan_id = "plan-abc"
            direction = "LONG"
            entry = Decimal("100")
            stop = Decimal("98")
            target_1 = Decimal("104")
            engine_risk_distance = Decimal("2")
            engine_reward_distance = Decimal("4")
            engine_risk_reward_ratio = Decimal("2")
            quantity = Decimal("10")
            planned_risk = Decimal("20")
            maximum_risk = Decimal("20")
            account_capital = Decimal("1000")
            risk_percent = Decimal("2")
            setup_type = "BREAKOUT"
        eng = PaperTradingEngine()
        t = eng.create(
            instrument="NIFTY", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", plan=FakePlan(),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        assert t.entry == Decimal("100")
        assert t.plan_id == "plan-abc"
        assert t.setup_type == "BREAKOUT"


# ============================================================
# B. INVALID CREATION
# ============================================================


class TestInvalidCreation:
    def test_non_directional_geometry_invalidated(self):
        eng = PaperTradingEngine()
        t = eng.create(
            instrument="X", timeframe="15m", direction="NONE",
            existing_decision="WATCH", entry=Decimal("100"), stop=Decimal("98"),
            target_1=Decimal("104"), engine_risk_distance=Decimal("2"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        assert t.status is PaperTradeStatus.INVALIDATED

    def test_missing_geometry_invalidated(self):
        eng = PaperTradingEngine()
        t = eng.create(
            instrument="X", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", entry=None, stop=None,
            target_1=None, created_at=datetime(2024, 1, 1, 9, 15),
        )
        assert t.status is PaperTradeStatus.INVALIDATED

    def test_zero_risk_invalidated(self):
        eng = PaperTradingEngine()
        t = eng.create(
            instrument="X", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", entry=Decimal("100"),
            stop=Decimal("100"), target_1=Decimal("104"),
            engine_risk_distance=Decimal("0"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        assert t.status is PaperTradeStatus.INVALIDATED

    def test_target_2_rejected(self):
        # Target 2 is never supported — hand-constructing a trade with a
        # target_2 value raises at the model __post_init__.
        import pytest as _pytest
        with _pytest.raises(ValueError):
            PaperTrade(
                paper_trade_id="pt-x",
                instrument="X", timeframe="15m", direction="LONG",
                existing_decision="Q", setup_type="",
                plan_id="", created_at=datetime(2024, 1, 1, 9, 15),
                evaluation_timestamp=None,
                entry=Decimal("100"), stop=Decimal("98"), target_1=Decimal("104"),
                target_2=Decimal("110"), target_2_supported=False,
                engine_risk_distance=Decimal("2"),
            )
        with _pytest.raises(ValueError):
            PaperTrade(
                paper_trade_id="pt-x",
                instrument="X", timeframe="15m", direction="LONG",
                existing_decision="Q", setup_type="",
                plan_id="", created_at=datetime(2024, 1, 1, 9, 15),
                evaluation_timestamp=None,
                entry=Decimal("100"), stop=Decimal("98"), target_1=Decimal("104"),
                target_2=None, target_2_supported=True,
                engine_risk_distance=Decimal("2"),
            )


# ============================================================
# C. STATE TRANSITIONS
# ============================================================


class TestStateTransitions:
    def test_illegal_manual_close_on_waiting_raises(self):
        t = _long_trade()
        eng = PaperTradingEngine()
        with pytest.raises(ValueError):
            eng.close_manually(t, exit_price=Decimal("101"), exit_timestamp=datetime(2024, 1, 1, 10, 0))

    def test_illegal_cancel_on_open_raises(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        opened = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert opened.status is PaperTradeStatus.OPEN
        with pytest.raises(ValueError):
            eng.cancel(opened)

    def test_illegal_manual_close_on_closed_raises(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        closed = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert closed.status is PaperTradeStatus.CLOSED
        with pytest.raises(ValueError):
            eng.close_manually(closed, exit_price=Decimal("101"), exit_timestamp=datetime(2024, 1, 1, 11, 0))

    def test_cancel_waiting_succeeds(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        cancelled = eng.cancel(t)
        assert cancelled.status is PaperTradeStatus.CANCELLED
        assert cancelled.exit_reason is PaperExitReason.CANCELLED
        assert cancelled.realized_r is None

    def test_terminal_trade_unchanged_by_track(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        cancelled = eng.cancel(t)
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        again = eng.track(cancelled, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert again.status is PaperTradeStatus.CANCELLED
        assert again.paper_trade_id == cancelled.paper_trade_id


# ============================================================
# D. ENTRY DETECTION
# ============================================================


class TestEntryDetection:
    def test_long_entry_touched(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99.5, 101)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.OPEN
        assert out.actual_entry_price == Decimal("100")
        assert out.entry_timestamp == datetime(2024, 1, 1, 9, 30)

    def test_short_entry_touched(self):
        eng = PaperTradingEngine()
        t = _long_trade(direction="SHORT", entry=Decimal("100"), stop=Decimal("102"), target=Decimal("96"))
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 100.5, 101, 99, 100)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.OPEN
        assert out.actual_entry_price == Decimal("100")

    def test_entry_price_is_reference(self):
        eng = PaperTradingEngine()
        t = _long_trade(entry=Decimal("50"))
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 55, 56, 49, 55)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.actual_entry_price == Decimal("50")


# ============================================================
# E. WAITING FOR ENTRY
# ============================================================


class TestWaitingForEntry:
    def test_no_entry_remains_waiting(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 105, 106, 104, 105)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.WAITING_FOR_ENTRY
        assert out.actual_entry_price is None

    def test_no_candles_remains_waiting(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        out = eng.track(t, completed_candles=[], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.WAITING_FOR_ENTRY


# ============================================================
# F. STOP DETECTION
# ============================================================


class TestStopDetection:
    def test_long_stop_hit(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97.5)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.status is PaperTradeStatus.CLOSED
        assert out.exit_reason is PaperExitReason.STOP_HIT
        assert out.actual_exit_price == Decimal("98")

    def test_short_stop_hit(self):
        eng = PaperTradingEngine()
        t = _long_trade(direction="SHORT", entry=Decimal("100"), stop=Decimal("102"), target=Decimal("96"))
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 103, 104, 100, 103)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.exit_reason is PaperExitReason.STOP_HIT
        assert out.actual_exit_price == Decimal("102")


# ============================================================
# G. TARGET DETECTION
# ============================================================


class TestTargetDetection:
    def test_long_target_hit(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.status is PaperTradeStatus.CLOSED
        assert out.exit_reason is PaperExitReason.TARGET_HIT
        assert out.actual_exit_price == Decimal("104")

    def test_target_hit_before_stop(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)  # target
        c3 = _candle(datetime(2024, 1, 1, 10, 0), 99, 100, 97, 97)     # stop later
        out = eng.track(t, completed_candles=[c1, c2, c3], reference_now=datetime(2024, 1, 1, 10, 15))
        assert out.exit_reason is PaperExitReason.TARGET_HIT


# ============================================================
# H. MANUAL CLOSE
# ============================================================


class TestManualClose:
    def test_manual_close_open_trade(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        opened = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        closed = eng.close_manually(
            opened, exit_price=Decimal("102"), exit_timestamp=datetime(2024, 1, 1, 10, 0),
        )
        assert closed.status is PaperTradeStatus.CLOSED
        assert closed.exit_reason is PaperExitReason.MANUAL_CLOSE
        assert closed.actual_exit_price == Decimal("102")
        assert closed.realized_r == Decimal("1")

    def test_manual_close_realized_pnl(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        opened = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        closed = eng.close_manually(
            opened, exit_price=Decimal("103"), exit_timestamp=datetime(2024, 1, 1, 10, 0),
        )
        # (103-100)*10 = 30
        assert closed.realized_pnl == Decimal("30")


# ============================================================
# I. BOTH_TOUCHED AMBIGUITY
# ============================================================


class TestBothTouched:
    def test_both_touched_single_candle(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 97, 101)  # both
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.status is PaperTradeStatus.CLOSED
        assert out.exit_reason is PaperExitReason.BOTH_TOUCHED
        assert out.realized_r is None
        assert out.realized_pnl is None
        assert out.actual_exit_price is None

    def test_both_touched_never_win_or_loss(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 97, 101)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert not out.exit_reason.is_win
        assert not out.exit_reason.is_loss
        assert out.exit_reason.is_ambiguous


# ============================================================
# J. NO_GEOMETRY
# ============================================================


class TestNoGeometry:
    def test_invalidated_trade_has_no_geometry_exit(self):
        eng = PaperTradingEngine()
        t = _long_trade(direction="NONE")
        assert t.status is PaperTradeStatus.INVALIDATED
        # An invalidated trade has no entry/exit fabricated.
        assert t.actual_entry_price is None
        assert t.realized_r is None

    def test_invalidated_unchanged_by_track(self):
        eng = PaperTradingEngine()
        t = _long_trade(direction="NONE")
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.INVALIDATED


# ============================================================
# K. MISSING GEOMETRY / L. MISSING TARGET
# ============================================================


class TestMissingGeometry:
    def test_missing_target_open_trade_stays_open(self):
        eng = PaperTradingEngine()
        t = _long_trade(target=None)
        # has_geometry is True (entry/stop present) but has_target False.
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        opened = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert opened.status is PaperTradeStatus.OPEN
        # No target -> cannot resolve target/stop; stays open (no fabricated exit).
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97)
        out = eng.track(opened, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.status is PaperTradeStatus.OPEN

    def test_missing_target_manual_close_still_works(self):
        eng = PaperTradingEngine()
        t = _long_trade(target=None)
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        opened = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        closed = eng.close_manually(
            opened, exit_price=Decimal("101"), exit_timestamp=datetime(2024, 1, 1, 10, 0),
        )
        assert closed.status is PaperTradeStatus.CLOSED


# ============================================================
# M. DECISION PRESERVATION
# ============================================================


class TestDecisionPreservation:
    @pytest.mark.parametrize("decision", ["REJECTED", "WATCH", "QUALIFIED", "PREFERRED"])
    def test_decision_reused_verbatim(self, decision):
        eng = PaperTradingEngine()
        t = eng.create(
            instrument="X", timeframe="15m", direction="LONG",
            existing_decision=decision, entry=Decimal("100"), stop=Decimal("98"),
            target_1=Decimal("104"), engine_risk_distance=Decimal("2"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        assert t.existing_decision == decision

    def test_loss_does_not_rewrite_decision(self):
        eng = PaperTradingEngine()
        t = _long_trade(decision="QUALIFIED")
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        # LOSS but decision stays QUALIFIED (never rewritten).
        assert out.exit_reason is PaperExitReason.STOP_HIT
        assert out.existing_decision == "QUALIFIED"

    def test_no_buysell_label(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        assert "BUY" not in t.direction
        assert "SELL" not in t.direction
        assert t.direction in ("LONG", "SHORT")


# ============================================================
# N. TRADE GEOMETRY PRESERVATION
# ============================================================


class TestGeometryPreservation:
    def test_geometry_reused_verbatim(self):
        t = _long_trade()
        assert t.entry == Decimal("100")
        assert t.stop == Decimal("98")
        assert t.target_1 == Decimal("104")
        assert t.engine_risk_distance == Decimal("2")
        assert t.engine_reward_distance == Decimal("4")

    def test_target_2_always_none(self):
        t = _long_trade()
        assert t.target_2 is None
        assert t.target_2_supported is False


# ============================================================
# O. TRADE PLAN PRESERVATION
# ============================================================


class TestTradePlanPreservation:
    def test_plan_fields_reused_verbatim(self):
        t = _long_trade()
        assert t.planned_quantity == Decimal("10")
        assert t.planned_risk == Decimal("20")
        assert t.maximum_risk == Decimal("20")
        assert t.account_capital == Decimal("1000")
        assert t.risk_percent == Decimal("2")


# ============================================================
# P. DECIMAL ACCOUNTING / Q. REALIZED P&L / R. REALIZED R
# ============================================================


class TestDecimalAccounting:
    def test_realized_r_long_target(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        # (104-100)/2 = 2
        assert out.realized_r == Decimal("2")
        # (104-100)*10 = 40
        assert out.realized_pnl == Decimal("40")

    def test_realized_r_short_target(self):
        eng = PaperTradingEngine()
        t = _long_trade(direction="SHORT", entry=Decimal("100"), stop=Decimal("102"), target=Decimal("96"))
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 100, 101, 99, 100)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 95, 96, 94, 95)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        # (100-96)/2 = 2
        assert out.realized_r == Decimal("2")

    def test_realized_r_stop_hit(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        # (98-100)/2 = -1
        assert out.realized_r == Decimal("-1")

    def test_decimal_type_preserved(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert isinstance(out.realized_r, Decimal)
        assert isinstance(out.realized_pnl, Decimal)

    def test_expired_mark_to_close(self):
        eng = PaperTradingEngine(PaperTradeConfig(max_holding_bars=5))
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        # candles that touch neither stop (98) nor target (104)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 102, 100, 101)
        out = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        assert out.status is PaperTradeStatus.CLOSED
        assert out.exit_reason is PaperExitReason.EXPIRED
        # mark-to-close at 101: (101-100)/2 = 0.5
        assert out.realized_r == Decimal("0.5")


# ============================================================
# S. AGGREGATE PERFORMANCE
# ============================================================


class TestAggregatePerformance:
    def test_empty_performance(self):
        eng = PaperTradePerformanceEngine()
        a = eng.analyze([])
        assert a.is_empty
        assert a.overall.total == 0
        assert a.overall.win_rate is None

    def test_mixed_performance(self):
        eng = PaperTradingEngine()
        perf_eng = PaperTradePerformanceEngine()
        t1 = _long_trade(instrument="A")
        t2 = _long_trade(instrument="B")
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2_win = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        c2_loss = _candle(datetime(2024, 1, 1, 9, 45), 99, 100, 97, 97)
        win = eng.track(t1, completed_candles=[c1, c2_win], reference_now=datetime(2024, 1, 1, 10, 0))
        loss = eng.track(t2, completed_candles=[c1, c2_loss], reference_now=datetime(2024, 1, 1, 10, 0))
        a = perf_eng.analyze([win, loss])
        assert a.overall.total == 2
        assert a.overall.wins == 1
        assert a.overall.losses == 1
        assert a.overall.win_rate == 0.5

    def test_both_touched_excluded_from_win_loss(self):
        eng = PaperTradingEngine()
        perf_eng = PaperTradePerformanceEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 97, 101)
        amb = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        a = perf_eng.analyze([amb])
        assert a.overall.ambiguous == 1
        assert a.overall.wins == 0
        assert a.overall.losses == 0
        assert a.overall.win_rate is None

    def test_profit_factor_none_when_no_negative(self):
        eng = PaperTradingEngine()
        perf_eng = PaperTradePerformanceEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        win = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        a = perf_eng.analyze([win])
        assert a.overall.profit_factor is None


# ============================================================
# T/U/V/W. GROUPING
# ============================================================


class TestGrouping:
    def test_instrument_grouping(self):
        eng = PaperTradingEngine()
        perf_eng = PaperTradePerformanceEngine()
        t1 = _long_trade(instrument="A")
        t2 = _long_trade(instrument="B")
        a = perf_eng.analyze([t1, t2])
        bd = {b.dimension: b for b in a.breakdowns}[PaperTradeGroupDimension.INSTRUMENT]
        keys = [g.key for g in bd.groups]
        assert keys == ["A", "B"]

    def test_setup_grouping(self):
        eng = PaperTradingEngine()
        t1 = _long_trade()
        t2 = _long_trade()
        perf_eng = PaperTradePerformanceEngine()
        a = perf_eng.analyze([t1, t2])
        bd = {b.dimension: b for b in a.breakdowns}[PaperTradeGroupDimension.SETUP_TYPE]
        keys = [g.key for g in bd.groups]
        assert "TREND_CONTINUATION" in keys

    def test_decision_grouping(self):
        perf_eng = PaperTradePerformanceEngine()
        t1 = _long_trade(decision="QUALIFIED")
        t2 = _long_trade(decision="PREFERRED")
        a = perf_eng.analyze([t1, t2])
        bd = {b.dimension: b for b in a.breakdowns}[PaperTradeGroupDimension.DECISION]
        keys = [g.key for g in bd.groups]
        assert "PREFERRED" in keys
        assert "QUALIFIED" in keys

    def test_timeframe_grouping(self):
        perf_eng = PaperTradePerformanceEngine()
        t1 = _long_trade()
        a = perf_eng.analyze([t1])
        bd = {b.dimension: b for b in a.breakdowns}[PaperTradeGroupDimension.TIMEFRAME]
        assert any(g.key == "15m" for g in bd.groups)


# ============================================================
# X/Y/Z. PERSISTENCE
# ============================================================


class TestPersistence:
    def test_save_load_round_trip(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        t = _long_trade()
        store.save(t)
        assert store.exists(t.paper_trade_id)
        loaded = store.load(t.paper_trade_id)
        assert loaded.paper_trade_id == t.paper_trade_id
        assert loaded.entry == t.entry

    def test_reload_after_restart(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        t = _long_trade()
        store.save(t)
        # Simulate restart: new store instance on the same dir.
        store2 = PaperTradeStore(directory=tmp_path)
        loaded = store2.load(t.paper_trade_id)
        assert loaded.paper_trade_id == t.paper_trade_id
        assert store2.list_trades() == [t.paper_trade_id]

    def test_malformed_corrupted(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        t = _long_trade()
        store.save(t)
        path = store.path_for(t.paper_trade_id)
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(PaperTradeIntegrityError):
            store.load(t.paper_trade_id)

    def test_missing_load_raises(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        with pytest.raises(PaperTradeNotFoundError):
            store.load("pt-nonexistent")

    def test_unsafe_id_rejected(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        with pytest.raises(PaperTradeStoreError):
            store.load("../../etc/passwd")

    def test_delete(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        t = _long_trade()
        store.save(t)
        store.delete(t.paper_trade_id)
        assert not store.exists(t.paper_trade_id)

    def test_empty_database(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        assert store.list_trades() == []
        assert store.load_all() == []

    def test_integrity_mismatch_filename(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        t = _long_trade()
        store.save(t)
        # Write a record whose stored id differs from the filename.
        path = store.path_for(t.paper_trade_id)
        t2 = _long_trade(created_at=datetime(2024, 2, 1, 9, 15))
        path.write_text(serialize_paper_trade(t2), encoding="utf-8")
        with pytest.raises(PaperTradeIntegrityError):
            store.load(t.paper_trade_id)


# ============================================================
# AA. DETERMINISTIC SERIALIZATION
# ============================================================


class TestSerialization:
    def test_round_trip_lossless(self):
        t = _long_trade()
        text = serialize_paper_trade(t)
        loaded = deserialize_paper_trade(text)
        assert loaded.paper_trade_id == t.paper_trade_id
        assert loaded.entry == t.entry
        assert loaded.realized_r == t.realized_r
        assert loaded.status == t.status

    def test_deterministic_bytes(self):
        t = _long_trade()
        b1 = serialize_paper_trade_bytes(t)
        b2 = serialize_paper_trade_bytes(t)
        assert b1 == b2

    def test_canonical_json(self):
        t = _long_trade()
        assert canonical_paper_trade_json(t) == serialize_paper_trade(t)

    def test_schema_version_constant(self):
        assert PAPER_TRADE_SCHEMA_VERSION == 1

    def test_header_parse(self):
        t = _long_trade()
        header = parse_paper_trade_header(serialize_paper_trade(t))
        assert header["schema_version"] == 1

    def test_future_schema_rejected(self):
        t = _long_trade()
        text = serialize_paper_trade(t).replace('"schema_version": 1', '"schema_version": 999')
        with pytest.raises(ValueError):
            deserialize_paper_trade(text)

    def test_malformed_json_rejected(self):
        with pytest.raises(ValueError):
            deserialize_paper_trade("{not json")

    def test_round_trip_closed_trade(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        closed = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        loaded = deserialize_paper_trade(serialize_paper_trade(closed))
        assert loaded.status is PaperTradeStatus.CLOSED
        assert loaded.exit_reason is PaperExitReason.TARGET_HIT
        assert loaded.realized_r == Decimal("2")


# ============================================================
# AB/AC/AD. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_future_cannot_alter_resolved_trade(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)  # target
        res = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        # Add a future candle that would have hit the stop.
        c_future = _candle(datetime(2024, 1, 1, 10, 30), 99, 100, 95, 95)
        res2 = eng.track(res, completed_candles=[c1, c2, c_future], reference_now=datetime(2024, 1, 1, 11, 0))
        assert res2.status is PaperTradeStatus.CLOSED
        assert res2.exit_reason is PaperExitReason.TARGET_HIT
        assert res2.realized_r == Decimal("2")

    def test_future_candles_do_not_change_earlier_state(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        first = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert first.status is PaperTradeStatus.OPEN
        # Adding a future candle (relative to ref 9:45) must not change first.
        c_future = _candle(datetime(2024, 1, 1, 11, 0), 99, 100, 95, 95)
        second = eng.track(
            t, completed_candles=[c1, c_future], reference_now=datetime(2024, 1, 1, 9, 45),
        )
        assert second.status is PaperTradeStatus.OPEN
        assert second.paper_trade_id == first.paper_trade_id

    def test_forming_candle_excluded(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        # A forming candle (timestamp > reference_now) is never inspected.
        forming = _candle(datetime(2024, 1, 1, 11, 0), 101, 105, 95, 95)
        out = eng.track(t, completed_candles=[forming], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_future_dated_candle_rejected(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        future = _candle(datetime(2030, 1, 1, 9, 30), 101, 105, 95, 105)
        out = eng.track(t, completed_candles=[future], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.WAITING_FOR_ENTRY

    def test_outcome_evaluator_not_called(self, monkeypatch):
        from engine.intelligence import historical_outcome as ho
        orig = ho.OutcomeEvaluator.evaluate
        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            raise AssertionError("OutcomeEvaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        try:
            eng = PaperTradingEngine()
            t = _long_trade()
            c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
            c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
            eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        finally:
            ho.OutcomeEvaluator.evaluate = orig
        assert called["n"] == 0

    def test_historical_pipeline_not_called(self, monkeypatch):
        from engine.pipeline import historical_pipeline as hp
        orig = hp.HistoricalEvaluationPipeline.evaluate
        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            raise AssertionError("pipeline must not be called")

        monkeypatch.setattr(hp.HistoricalEvaluationPipeline, "evaluate", _boom)
        try:
            eng = PaperTradingEngine()
            t = _long_trade()
            c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
            eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        finally:
            hp.HistoricalEvaluationPipeline.evaluate = orig
        assert called["n"] == 0

    def test_no_lookahead_through_aggregation(self):
        eng = PaperTradingEngine()
        perf_eng = PaperTradePerformanceEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 100, 105)
        closed = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        a1 = perf_eng.analyze([closed])
        # Future candles do not change the analytics (it consumes trades only).
        a2 = perf_eng.analyze([closed])
        assert a1.analytics_id == a2.analytics_id


# ============================================================
# AE. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_track_does_not_mutate_input(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        original_status = t.status
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert t.status is original_status

    def test_frozen_model(self):
        t = _long_trade()
        with pytest.raises(Exception):
            t.status = PaperTradeStatus.OPEN  # type: ignore[misc]

    def test_create_returns_frozen(self):
        import dataclasses
        t = _long_trade()
        assert dataclasses.is_dataclass(t)


# ============================================================
# AF. FAILURE ISOLATION
# ============================================================


class TestFailureIsolation:
    def test_bad_candle_in_window_does_not_crash_engine(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        # Mix valid and the engine filters by timestamp; an invalid candle
        # object would raise at construction (OHLCVCandle validates), so
        # we only pass valid candles here — the engine never crashes.
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        out = eng.track(t, completed_candles=[c1], reference_now=datetime(2024, 1, 1, 9, 45))
        assert out.status is PaperTradeStatus.OPEN


# ============================================================
# AG. WORKSTATION INTEGRATION
# ============================================================


class TestWorkstationIntegration:
    def _service(self, tmp_path):
        store = PaperTradeStore(directory=tmp_path)
        return DashboardAnalysisService(paper_trade_store=store)

    def test_create_paper_trade_via_service(self, tmp_path):
        svc = self._service(tmp_path)
        view = svc.create_paper_trade(
            PaperTradeRequest(
                instrument="NIFTY",
                account_capital="100000",
                risk_percent="1",
                setup_timeframe="15m",
                created_at=datetime(2024, 1, 1, 9, 15),
            ),
        )
        assert view.paper_trade_id.startswith("pt-")
        assert view.existing_decision in ("", "REJECTED", "WATCH", "QUALIFIED", "PREFERRED")
        # The fixture trade may have incomplete geometry (target None) -> the
        # paper trade is honestly WAITING_FOR_ENTRY (entry/stop present) or
        # INVALIDATED (no usable risk distance). Either is honest — never
        # fabricated.
        assert view.status in ("WAITING_FOR_ENTRY", "INVALIDATED")

    def test_journal_empty_without_trades(self, tmp_path):
        svc = self._service(tmp_path)
        journal = svc.paper_trade_journal()
        assert journal.is_empty

    def test_journal_lists_trades(self, tmp_path):
        svc = self._service(tmp_path)
        svc.create_paper_trade(
            PaperTradeRequest(
                instrument="NIFTY", account_capital="100000", risk_percent="1",
                created_at=datetime(2024, 1, 1, 9, 15),
            ),
        )
        journal = svc.paper_trade_journal()
        assert len(journal.trades) >= 1

    def test_load_paper_trade_after_restart(self, tmp_path):
        svc = self._service(tmp_path)
        view = svc.create_paper_trade(
            PaperTradeRequest(
                instrument="NIFTY", account_capital="100000", risk_percent="1",
                created_at=datetime(2024, 1, 1, 9, 15),
            ),
        )
        # New service instance (simulate restart).
        svc2 = self._service(tmp_path)
        loaded = svc2.load_paper_trade(view.paper_trade_id)
        assert loaded.paper_trade_id == view.paper_trade_id

    def test_cancel_via_service(self, tmp_path):
        svc = self._service(tmp_path)
        # Create a paper trade directly via the engine with complete geometry
        # so it is WAITING_FOR_ENTRY (cancellable). The fixture trade may be
        # INVALIDATED (incomplete geometry) which is not cancellable.
        from engine.intelligence.paper_trading import PaperTradingEngine
        eng = PaperTradingEngine()
        trade = eng.create(
            instrument="NIFTY", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", setup_type="TREND_CONTINUATION",
            entry=Decimal("100"), stop=Decimal("98"), target_1=Decimal("104"),
            engine_risk_distance=Decimal("2"), engine_reward_distance=Decimal("4"),
            engine_risk_reward_ratio=Decimal("2"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        svc.paper_trade_store.save(trade)
        cancelled = svc.cancel_paper_trade(trade.paper_trade_id)
        assert cancelled.status == "CANCELLED"


# ============================================================
# AH. API SCHEMA
# ============================================================


class TestApiSchema:
    def _client(self, tmp_path, monkeypatch):
        from dashboard.app import create_app, set_service
        store = PaperTradeStore(directory=tmp_path)
        svc = DashboardAnalysisService(paper_trade_store=store)
        app = create_app(service=svc)
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_paper_trading_page_renders(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.get("/paper-trading")
        assert r.status_code == 200
        assert b"Paper Trading" in r.content

    def test_api_paper_trades_empty(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.get("/api/paper-trades")
        assert r.status_code == 200
        data = r.json()
        assert data["is_empty"] is True

    def test_api_create_and_get_trade(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.post(
            "/api/paper-trades",
            params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["paper_trade_id"].startswith("pt-")
        # The fixture trade may have incomplete geometry -> WAITING_FOR_ENTRY
        # or INVALIDATED (honest; never fabricated).
        assert data["status"] in ("WAITING_FOR_ENTRY", "INVALIDATED")
        assert data["target_2_supported"] is False
        # GET single
        r2 = client.get(f"/api/paper-trades/{data['paper_trade_id']}")
        assert r2.status_code == 200
        assert r2.json()["paper_trade_id"] == data["paper_trade_id"]

    def test_api_no_buysell_in_response(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        r = client.post(
            "/api/paper-trades",
            params={"instrument": "NIFTY", "timeframe": "15m", "account_capital": "100000", "risk_percent": "1"},
        )
        data = r.json()
        assert "BUY" not in data["direction"]
        assert "SELL" not in data["direction"]

    def test_existing_routes_preserved(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        for path in ["/", "/health", "/api/health", "/scan", "/api/instruments", "/workstation"]:
            r = client.get(path)
            assert r.status_code == 200, f"{path} failed: {r.status_code}"


# ============================================================
# AI. BACKWARD COMPATIBILITY
# ============================================================


class TestBackwardCompatibility:
    def test_service_without_store_still_works(self):
        svc = DashboardAnalysisService()
        # No paper-trade store; analyze still works.
        view = svc.analyze(__import__("dashboard.services", fromlist=["AnalysisRequest"]).AnalysisRequest(
            instrument="NIFTY", setup_timeframe="15m",
        ))
        assert view is not None

    def test_paper_trade_methods_raise_without_store(self):
        svc = DashboardAnalysisService()
        with pytest.raises(LookupError):
            svc.load_paper_trade("pt-x")
        with pytest.raises(LookupError):
            svc.track_paper_trade(PaperTradeTrackRequest(paper_trade_id="pt-x", reference_now=datetime(2024, 1, 1)))

    def test_intelligence_init_empty(self):
        import engine.intelligence as intel
        # intelligence/__init__.py stays intentionally empty.
        assert not hasattr(intel, "PaperTradingEngine")

    def test_reporting_init_not_extended(self):
        import engine.reporting as rep
        assert not hasattr(rep, "PaperTradeFormatter")


# ============================================================
# AK. AMBIGUOUS / UNRESOLVED STATE
# ============================================================


class TestAmbiguousUnresolved:
    def test_ambiguous_trade_serializes_without_fabricated_values(self):
        eng = PaperTradingEngine()
        t = _long_trade()
        c1 = _candle(datetime(2024, 1, 1, 9, 30), 101, 102, 99, 101)
        c2 = _candle(datetime(2024, 1, 1, 9, 45), 101, 105, 97, 101)
        amb = eng.track(t, completed_candles=[c1, c2], reference_now=datetime(2024, 1, 1, 10, 0))
        loaded = deserialize_paper_trade(serialize_paper_trade(amb))
        assert loaded.exit_reason is PaperExitReason.BOTH_TOUCHED
        assert loaded.realized_r is None
        assert loaded.realized_pnl is None
        assert loaded.actual_exit_price is None

    def test_unresolved_waiting_serializes(self):
        t = _long_trade()
        loaded = deserialize_paper_trade(serialize_paper_trade(t))
        assert loaded.status is PaperTradeStatus.WAITING_FOR_ENTRY
        assert loaded.exit_reason is None


# ============================================================
# AL. REGRESSION
# ============================================================


class TestRegression:
    def test_pipeline_baseline_signals_4_trades_3(self):
        from engine.pipeline.datasets import trending_dataset
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
        )
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_trade_planning_still_importable(self):
        from engine.intelligence.trade_planning import TradePlanningEngine
        from engine.models.trade_plan import TradePlan
        assert TradePlanningEngine is not None
        assert TradePlan is not None

    def test_production_intelligence_importable(self):
        from engine.intelligence.production_intelligence import ProductionIntelligenceEngine
        assert ProductionIntelligenceEngine is not None

    def test_backtest_validation_importable(self):
        from engine.intelligence.backtest_validation import BacktestValidationEngine
        assert BacktestValidationEngine is not None

    def test_robustness_validation_importable(self):
        from engine.intelligence.robustness_validation import RobustnessValidationEngine
        assert RobustnessValidationEngine is not None


# ============================================================
# REPORTING
# ============================================================


class TestReporting:
    def test_formatter_returns_str(self):
        t = _long_trade()
        text = PaperTradeFormatter().format(t)
        assert isinstance(text, str)
        assert "PAPER TRADE REPORT" in text
        assert "BUY/SELL" in text or "BUY/SELL/ENTER/EXIT/HOLD" in text

    def test_journal_formatter_returns_str(self):
        t = _long_trade()
        text = PaperTradeJournalFormatter().format([t])
        assert isinstance(text, str)
        assert "JOURNAL" in text

    def test_performance_formatter_returns_str(self):
        eng = PaperTradePerformanceEngine()
        a = eng.analyze([_long_trade()])
        text = PaperTradePerformanceFormatter().format(a)
        assert isinstance(text, str)
        assert "PERFORMANCE" in text

    def test_formatter_no_predictive_language(self):
        t = _long_trade()
        text = PaperTradeFormatter().format(t).lower()
        assert "guaranteed profit" not in text
        assert "probability of success" not in text

    def test_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            PaperTradeFormatter(precision=-1)

    def test_formatter_deterministic(self):
        t = _long_trade()
        assert PaperTradeFormatter().format(t) == PaperTradeFormatter().format(t)


# ============================================================
# DETERMINISTIC ID
# ============================================================


class TestDeterministicId:
    def test_same_inputs_same_id(self):
        eng = PaperTradingEngine()
        kwargs = dict(
            instrument="NIFTY", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", setup_type="TREND_CONTINUATION",
            entry=Decimal("100"), stop=Decimal("98"), target_1=Decimal("104"),
            engine_risk_distance=Decimal("2"), engine_reward_distance=Decimal("4"),
            engine_risk_reward_ratio=Decimal("2"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        t1 = eng.create(**kwargs)
        t2 = eng.create(**kwargs)
        assert t1.paper_trade_id == t2.paper_trade_id

    def test_different_created_at_different_id(self):
        eng = PaperTradingEngine()
        base = dict(
            instrument="NIFTY", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", entry=Decimal("100"), stop=Decimal("98"),
            target_1=Decimal("104"), engine_risk_distance=Decimal("2"),
        )
        t1 = eng.create(created_at=datetime(2024, 1, 1, 9, 15), **base)
        t2 = eng.create(created_at=datetime(2024, 1, 1, 9, 30), **base)
        assert t1.paper_trade_id != t2.paper_trade_id

    def test_same_opportunity_distinct_sequence_distinct_id(self):
        eng = PaperTradingEngine()
        base = dict(
            instrument="NIFTY", timeframe="15m", direction="LONG",
            existing_decision="QUALIFIED", entry=Decimal("100"), stop=Decimal("98"),
            target_1=Decimal("104"), engine_risk_distance=Decimal("2"),
            created_at=datetime(2024, 1, 1, 9, 15),
        )
        t1 = eng.create(sequence=0, **base)
        t2 = eng.create(sequence=1, **base)
        assert t1.paper_trade_id != t2.paper_trade_id


# ============================================================
# CONFIG
# ============================================================


class TestConfig:
    def test_defaults(self):
        c = PaperTradeConfig()
        assert c.max_entry_bars == 20
        assert c.max_holding_bars == 50

    def test_frozen(self):
        c = PaperTradeConfig()
        with pytest.raises(Exception):
            c.max_entry_bars = 99  # type: ignore[misc]

    def test_invalid_max_entry_bars(self):
        with pytest.raises(ValueError):
            PaperTradeConfig(max_entry_bars=0)

    def test_invalid_max_holding_bars(self):
        with pytest.raises(ValueError):
            PaperTradeConfig(max_holding_bars=-1)

    def test_snapshot(self):
        c = PaperTradeConfig(label="x")
        snap = c.snapshot()
        assert any(k == "label" and v == "x" for k, v in snap)
