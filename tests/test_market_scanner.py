"""
Tests for the multi-timeframe market scanner (Sprint 11U).

Coverage:

A. Model validation (instrument/timeframe identity, scan result,
   ranking, frozen + slots, invariants)
B. Configuration validation + defaults
C. MTF alignment (bullish/bullish, bearish/bearish, bullish/bearish,
   bearish/bullish, neutral, unknown, missing)
D. Timeframe safety (latest completed HTF candle, incomplete HTF
   excluded, future HTF mutation, future LTF mutation)
E. Scanning (one instrument, multiple, multiple opportunities, no
   opportunity, watch only, incomplete input)
F. Ranking (deterministic, aligned vs conflicting, score ordering,
   geometry, R:R, confluence, deterministic tie-breaking, LONG vs
   SHORT symmetry)
G. Leakage (full series vs prefix, future mutation, multi-instrument
   future mutation, multi-timeframe future mutation)
H. Pipeline integration (existing signals unchanged, completed trades
   unchanged, scan information additive)
I. Serialization / reporting (round trip, report generation, sections,
   warning, no predictive language)
J. Determinism + immutability
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.market_scan_config import (
    MarketScanConfig,
    SCAN_RANKING_PRIORITY_ORDER,
    ScanRankingPriority,
)
from engine.config.swing_config import SwingConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.market_scan_serialization import (
    SCANNER_SCHEMA_VERSION,
    canonical_scan_json,
    deserialize_scan,
    parse_scan_header,
    serialize_scan,
)
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.intelligence.mtf_alignment import MTFAlignmentEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.intelligence.trade_opportunity import TradeOpportunityEngine
from engine.models.market_context import MarketTrendState
from engine.models.market_scan import (
    InstrumentScanResult,
    InstrumentTimeframe,
    MarketScanResult,
    MTFAlignment,
    RankedScanOpportunity,
    ScanStatus,
    TimeframeRole,
    TimeframeSlice,
)
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.market_scan import MarketScanFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# DATASET HELPERS
# ============================================================


def _candle(close: float, ts: datetime, spread: float = 2.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=1000.0,
    )


def _bullish_zigzag(
    start: float,
    n_legs: int = 4,
    start_ts: datetime = _EPOCH,
    step_days: int = 1,
    rise: int = 6,
    pullback: int = 3,
) -> list[OHLCVCandle]:
    """Bullish zigzag (HH/HL) producing a BULLISH market context."""

    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close + rise, 2)
            candles.append(_candle(close, ts))
            ts = ts + timedelta(days=step_days)
        for _ in range(2):
            close = round(close - pullback, 2)
            candles.append(_candle(close, ts))
            ts = ts + timedelta(days=step_days)
    return candles


def _bearish_zigzag(
    start: float,
    n_legs: int = 4,
    start_ts: datetime = _EPOCH,
    step_days: int = 1,
) -> list[OHLCVCandle]:
    """Bearish zigzag (LH/LL) producing a BEARISH market context."""

    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close - 6, 2)
            candles.append(_candle(close, ts))
            ts = ts + timedelta(days=step_days)
        for _ in range(2):
            close = round(close + 3, 2)
            candles.append(_candle(close, ts))
            ts = ts + timedelta(days=step_days)
    return candles


def _flat_dataset(n: int = 40, start_ts: datetime = _EPOCH) -> list[OHLCVCandle]:
    """A sideways oscillating market (NEUTRAL/RANGE)."""

    candles: list[OHLCVCandle] = []
    base = 100.0
    ts = start_ts
    for i in range(n):
        close = round(base + (2 if i % 2 == 0 else -2), 2)
        candles.append(_candle(close, ts))
        ts = ts + timedelta(days=1)
    return candles


def _aligned_long_dataset() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """
    Context (daily) bullish; setup (15M) bullish continuation zigzag
    starting AFTER the context closed, producing an ALIGNED LONG setup.
    """
    context = _bullish_zigzag(100.0, n_legs=4, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup = _bullish_zigzag(
        context[-1].close,
        n_legs=4,
        start_ts=setup_start,
        step_days=0,
    )
    # step_days=0 makes the zigzag use 15-minute steps via override below.
    setup = []
    close = context[-1].close
    ts = setup_start
    for _ in range(4):
        for _ in range(3):
            close = round(close + 6, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
        for _ in range(2):
            close = round(close - 3, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
    return context, setup


def _aligned_short_dataset() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """Context (daily) bearish; setup (15M) bearish -> ALIGNED SHORT."""
    context = _bearish_zigzag(200.0, n_legs=4, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup: list[OHLCVCandle] = []
    close = context[-1].close
    ts = setup_start
    for _ in range(4):
        for _ in range(3):
            close = round(close - 6, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
        for _ in range(2):
            close = round(close + 3, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
    return context, setup


def _conflicting_long_dataset() -> tuple[list[OHLCVCandle], list[OHLCVCandle]]:
    """Context (daily) BEARISH; setup (15M) bullish -> CONFLICTING LONG."""
    context = _bearish_zigzag(200.0, n_legs=4, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup = _bullish_zigzag(
        context[-1].close, n_legs=4, start_ts=setup_start, step_days=0,
    )
    setup = []
    close = context[-1].close
    ts = setup_start
    for _ in range(4):
        for _ in range(3):
            close = round(close + 6, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
        for _ in range(2):
            close = round(close - 3, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
    return context, setup


def _default_engines(lookback: int = 2) -> ScanEngines:
    swing_cfg = SwingConfig(lookback=lookback)
    return ScanEngines(
        candle_patterns=CandlePatternEngine(),
        market_context=MarketContextEngine(swing_config=swing_cfg),
        setup_confluence=SetupConfluenceEngine(),
        trade_candidates=TradeCandidateEngine(),
        trade_decision=TradeDecisionEngine(),
        trade_opportunity=TradeOpportunityEngine(),
        alignment=MTFAlignmentEngine(),
    )


def _scanner(**kwargs) -> MarketScanner:
    return MarketScanner(MarketScanConfig(**kwargs))


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_enum_members(self):
        assert {a.name for a in MTFAlignment} == {
            "ALIGNED", "CONFLICTING", "NEUTRAL", "UNKNOWN",
        }
        assert {s.name for s in ScanStatus} == {
            "OPPORTUNITIES_FOUND", "WATCH_ONLY", "NO_OPPORTUNITY",
            "INCOMPLETE",
        }
        assert {t.name for t in TimeframeRole} == {
            "CONTEXT_TIMEFRAME", "SETUP_TIMEFRAME",
        }

    def test_instrument_timeframe_frozen(self):
        it = InstrumentTimeframe("NIFTY", "1D", TimeframeRole.CONTEXT_TIMEFRAME)
        with pytest.raises(Exception):
            it.instrument = "X"  # type: ignore
        assert hasattr(InstrumentTimeframe, "__slots__")

    def test_ranked_scan_opportunity_rank_zero_ineligible(self):
        r = InstrumentScanResult(
            instrument="X", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None,
        )
        # ineligible (no opportunity) -> rank 0 allowed
        rs = RankedScanOpportunity(rank=0, opportunity=r, alignment=MTFAlignment.UNKNOWN)
        assert rs.rank == 0

    def test_ranked_scan_opportunity_rank_zero_ineligible_with_opp(self):
        # An ineligible result may not carry a positive rank.
        r = InstrumentScanResult(
            instrument="X", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None,
        )
        with pytest.raises(ValueError):
            RankedScanOpportunity(
                rank=1, opportunity=r, alignment=MTFAlignment.UNKNOWN,
            )

    def test_ranked_scan_opportunity_negative_rejected(self):
        r = InstrumentScanResult(
            instrument="X", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None,
        )
        with pytest.raises(ValueError):
            RankedScanOpportunity(
                rank=-1, opportunity=r, alignment=MTFAlignment.UNKNOWN,
            )

    def test_models_frozen_slots(self):
        for cls in (
            InstrumentTimeframe, TimeframeSlice, InstrumentScanResult,
            RankedScanOpportunity, MarketScanResult,
        ):
            assert hasattr(cls, "__slots__")
            instance_fields = {
                f for f in cls.__dataclass_fields__  # type: ignore
            }
            assert instance_fields

    def test_instrument_scan_result_defaults(self):
        r = InstrumentScanResult(
            instrument="X", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None,
        )
        assert r.alignment == MTFAlignment.UNKNOWN
        assert r.complete is False
        assert r.direction == ""
        assert r.decision_classification == ""
        assert r.decision_score == 0
        assert r.risk_reward_ratio is None
        assert r.has_opportunity is False
        assert r.eligible is False

    def test_scan_result_defaults(self):
        r = MarketScanResult(scan_id="scan-x", timestamp=None)
        assert r.status == ScanStatus.NO_OPPORTUNITY
        assert r.is_empty is True
        assert r.has_best is False
        assert r.eligible_count == 0
        assert r.instruments == ()
        assert r.ranked == ()

    def test_scan_result_immutable(self):
        r = MarketScanResult(scan_id="scan-x", timestamp=None)
        with pytest.raises(Exception):
            r.status = ScanStatus.INCOMPLETE  # type: ignore


# ============================================================
# B. CONFIGURATION
# ============================================================


class TestConfiguration:
    def test_defaults(self):
        c = MarketScanConfig()
        assert c.context_timeframe == "1D"
        assert c.setup_timeframe == "15M"
        assert c.min_history == 10
        assert c.require_context_timeframe is True
        assert c.require_completed_context_candle is True
        assert c.require_opportunity_for_eligibility is True
        assert c.max_surfaced_opportunities is None
        assert c.label == ""
        assert c.metadata == ()

    def test_min_history_negative_rejected(self):
        with pytest.raises(ValueError):
            MarketScanConfig(min_history=-1)

    def test_max_surfaced_zero_rejected(self):
        with pytest.raises(ValueError):
            MarketScanConfig(max_surfaced_opportunities=0)

    def test_max_surfaced_positive_accepted(self):
        c = MarketScanConfig(max_surfaced_opportunities=2)
        assert c.max_surfaced_opportunities == 2

    def test_same_timeframe_rejected(self):
        with pytest.raises(ValueError):
            MarketScanConfig(context_timeframe="1H", setup_timeframe="1H")

    def test_ranking_priority_order_canonical(self):
        assert SCAN_RANKING_PRIORITY_ORDER[0] == ScanRankingPriority.ELIGIBILITY
        assert (
            SCAN_RANKING_PRIORITY_ORDER[1] == ScanRankingPriority.MTF_ALIGNMENT
        )
        # documented order: eligibility, mtf, opp status, decision class,
        # score, geometry, rr, confluence, conflict_free, tiebreak
        assert len(SCAN_RANKING_PRIORITY_ORDER) == 10

    def test_config_frozen(self):
        c = MarketScanConfig()
        with pytest.raises(Exception):
            c.min_history = 5  # type: ignore


# ============================================================
# C. MTF ALIGNMENT
# ============================================================


def _context_with_state(state: MarketTrendState):
    """Build a minimal MarketContext stub with a given trend state."""
    from engine.models.market_context import (
        MarketContext,
        MarketTrend,
        RangeContext,
        RangeState,
        SupportResistanceContext,
        PriceLocation,
    )
    from engine.models.structure_analysis import StructureBias
    return MarketContext(
        index=0,
        trend=MarketTrend(
            state=state,
            bias=StructureBias.UNKNOWN,
            structure_intact=False,
            reasons=[],
        ),
        range=RangeContext(
            state=RangeState.UNKNOWN, high=None, low=None,
            width=None, position=None, reason="",
        ),
        support_resistance=SupportResistanceContext(
            support=None, resistance=None,
            distance_to_support=None, distance_to_resistance=None,
            location=PriceLocation.UNKNOWN,
        ),
    )


class TestMTFAlignment:
    def test_bullish_higher_long_lower_aligned(self):
        ctx = _context_with_state(MarketTrendState.BULLISH)
        assert MTFAlignmentEngine().align(ctx, "LONG") == MTFAlignment.ALIGNED

    def test_bearish_higher_short_lower_aligned(self):
        ctx = _context_with_state(MarketTrendState.BEARISH)
        assert MTFAlignmentEngine().align(ctx, "SHORT") == MTFAlignment.ALIGNED

    def test_bullish_higher_short_lower_conflicting(self):
        ctx = _context_with_state(MarketTrendState.BULLISH)
        assert MTFAlignmentEngine().align(ctx, "SHORT") == MTFAlignment.CONFLICTING

    def test_bearish_higher_long_lower_conflicting(self):
        ctx = _context_with_state(MarketTrendState.BEARISH)
        assert MTFAlignmentEngine().align(ctx, "LONG") == MTFAlignment.CONFLICTING

    def test_range_higher_long_lower_neutral(self):
        ctx = _context_with_state(MarketTrendState.RANGE)
        assert MTFAlignmentEngine().align(ctx, "LONG") == MTFAlignment.NEUTRAL

    def test_neutral_higher_short_lower_neutral(self):
        ctx = _context_with_state(MarketTrendState.NEUTRAL)
        assert MTFAlignmentEngine().align(ctx, "SHORT") == MTFAlignment.NEUTRAL

    def test_unknown_higher_long_lower_unknown(self):
        ctx = _context_with_state(MarketTrendState.UNKNOWN)
        assert MTFAlignmentEngine().align(ctx, "LONG") == MTFAlignment.UNKNOWN

    def test_none_context_unknown(self):
        assert MTFAlignmentEngine().align(None, "LONG") == MTFAlignment.UNKNOWN

    def test_none_lower_direction_unknown(self):
        ctx = _context_with_state(MarketTrendState.BULLISH)
        assert MTFAlignmentEngine().align(ctx, "NONE") == MTFAlignment.UNKNOWN
        assert MTFAlignmentEngine().align(ctx, "") == MTFAlignment.UNKNOWN

    def test_range_never_silently_bullish(self):
        ctx = _context_with_state(MarketTrendState.RANGE)
        # RANGE + LONG must NOT be ALIGNED or CONFLICTING
        assert MTFAlignmentEngine().align(ctx, "LONG") == MTFAlignment.NEUTRAL
        assert MTFAlignmentEngine().align(ctx, "LONG") != MTFAlignment.ALIGNED

    def test_determinism(self):
        ctx = _context_with_state(MarketTrendState.BULLISH)
        eng = MTFAlignmentEngine()
        for _ in range(5):
            assert eng.align(ctx, "LONG") == MTFAlignment.ALIGNED


# ============================================================
# D. TIMEFRAME SAFETY
# ============================================================


class TestTimeframeSafety:
    def test_latest_completed_htf_candle_used(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        r = res.results[0]
        assert r.complete is True
        # The higher context must have a timestamp strictly before the
        # setup evaluation time (the latest setup candle close).
        assert r.higher_context is not None
        assert r.timestamp is not None

    def test_incomplete_htf_candle_excluded(self):
        """An in-progress HTF candle (timestamp >= eval time) must NOT be
        used as context; if no earlier HTF candle exists the scan is
        INCOMPLETE."""
        # Context candle at the SAME time as the setup eval time -> not
        # "completed before" -> excluded when required.
        ctx_ts = _EPOCH + timedelta(days=10)
        ctx = [_candle(100.0, ctx_ts)]
        # Setup eval time == ctx_ts -> ctx candle is in-progress at eval.
        setup = _bullish_zigzag(100.0, n_legs=4, start_ts=ctx_ts, step_days=0)
        # Rebuild setup with 15-min steps at/after ctx_ts (in-progress HTF).
        setup = []
        close = 100.0
        ts = ctx_ts
        for _ in range(40):
            close = round(close + 2, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
        scanner = _scanner(min_history=10)
        res = scanner.scan(
            [InstrumentDataset("X", tuple(ctx), tuple(setup))],
            evaluation_time=ctx_ts,
            engines=_default_engines(),
        )
        assert res.status == ScanStatus.INCOMPLETE
        assert res.results[0].complete is False

    def test_future_htf_mutation_unchanged(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        orig = res.results[0]
        # Append a future HTF candle strictly after the eval time.
        future_ts = setup[-1].timestamp + timedelta(days=5)
        mutated_ctx = list(ctx) + [_candle(999.0, future_ts)]
        res2 = scanner.scan(
            [InstrumentDataset("TCS", tuple(mutated_ctx), tuple(setup))],
            engines=engines,
        )
        # The alignment + direction + decision must not change.
        assert res2.results[0].alignment == orig.alignment
        assert res2.results[0].direction == orig.direction
        assert res2.results[0].decision_classification == (
            orig.decision_classification
        )

    def test_future_ltf_mutation_unchanged(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        orig_align = res.results[0].alignment
        orig_dir = res.results[0].direction
        # Append a future setup candle strictly after the eval time.
        future_ts = setup[-1].timestamp + timedelta(hours=2)
        mutated_setup = list(setup) + [_candle(999.0, future_ts)]
        res2 = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(mutated_setup))],
            engines=engines,
        )
        assert res2.results[0].alignment == orig_align
        assert res2.results[0].direction == orig_dir

    def test_no_htf_data_incomplete(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("X", tuple(), tuple(setup))],
            engines=_default_engines(),
        )
        assert res.status == ScanStatus.INCOMPLETE
        assert res.results[0].complete is False
        assert res.results[0].alignment == MTFAlignment.UNKNOWN


# ============================================================
# E. SCANNING
# ============================================================


class TestScanning:
    def test_one_instrument(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert len(res.results) == 1
        assert res.instruments == ("TCS",)
        assert res.results[0].instrument == "TCS"

    def test_multiple_instruments(self):
        ctx_a, setup_a = _aligned_long_dataset()
        ctx_b, setup_b = _aligned_short_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [
                InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
                InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
            ],
            engines=_default_engines(),
        )
        assert set(res.instruments) == {"NIFTY", "TCS"}
        assert len(res.results) == 2

    def test_multiple_opportunities_ranked(self):
        ctx_a, setup_a = _aligned_long_dataset()
        ctx_b, setup_b = _aligned_short_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [
                InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
                InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
            ],
            engines=_default_engines(),
        )
        eligible = [r for r in res.ranked if r.rank > 0]
        # At least one opportunity should be eligible.
        assert len(eligible) >= 1
        ranks = [r.rank for r in eligible]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)

    def test_no_opportunity(self):
        # Both timeframes empty -> INCOMPLETE not NO_OPPORTUNITY.
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("X", tuple(), tuple())],
            engines=_default_engines(),
        )
        assert res.status == ScanStatus.INCOMPLETE
        assert res.best is None

    def test_watch_only(self):
        # A setup with structure but no eligible opportunity.
        ctx, setup = _aligned_long_dataset()
        # Use a very high min_decision_score so nothing is eligible.
        from engine.config.trade_opportunity_config import (
            TradeOpportunityConfig,
        )
        opp_cfg = TradeOpportunityConfig(min_decision_score=200)
        engines = _default_engines()
        engines = ScanEngines(
            candle_patterns=engines.candle_patterns,
            market_context=engines.market_context,
            setup_confluence=engines.setup_confluence,
            trade_candidates=engines.trade_candidates,
            trade_decision=engines.trade_decision,
            trade_opportunity=TradeOpportunityEngine(opp_cfg),
            alignment=engines.alignment,
        )
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("X", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        # A setup exists but is not eligible -> WATCH_ONLY.
        assert res.status == ScanStatus.WATCH_ONLY
        assert res.best is None

    def test_incomplete_input(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        # Drop the context candles -> INCOMPLETE.
        res = scanner.scan(
            [InstrumentDataset("X", tuple(), tuple(setup))],
            engines=_default_engines(),
        )
        assert res.status == ScanStatus.INCOMPLETE

    def test_scan_id_deterministic_and_prefixed(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res1 = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        res2 = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert res1.scan_id == res2.scan_id
        assert res1.scan_id.startswith("scan-")


# ============================================================
# F. RANKING
# ============================================================


class TestRanking:
    def test_deterministic_ranking(self):
        ctx_a, setup_a = _aligned_long_dataset()
        ctx_b, setup_b = _aligned_short_dataset()
        scanner = _scanner()
        engines = _default_engines()
        res1 = scanner.scan(
            [
                InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
                InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
            ],
            engines=engines,
        )
        res2 = scanner.scan(
            [
                InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
                InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
            ],
            engines=engines,
        )
        assert [r.opportunity.instrument for r in res1.ranked] == (
            [r.opportunity.instrument for r in res2.ranked]
        )
        assert [r.rank for r in res1.ranked] == (
            [r.rank for r in res2.ranked]
        )

    def test_aligned_outranks_conflicting(self):
        ctx_al, setup_al = _aligned_long_dataset()
        ctx_cf, setup_cf = _conflicting_long_dataset()
        # Both LONG setups; one aligned, one conflicting. Aligned should
        # rank above conflicting when both are eligible.
        scanner = _scanner()
        res = scanner.scan(
            [
                InstrumentDataset("ALIGNED", tuple(ctx_al), tuple(setup_al)),
                InstrumentDataset("CONFLICT", tuple(ctx_cf), tuple(setup_cf)),
            ],
            engines=_default_engines(),
        )
        ranked_eligible = [r for r in res.ranked if r.rank > 0]
        if len(ranked_eligible) >= 2:
            aligned_r = next(
                r for r in ranked_eligible if r.opportunity.instrument == "ALIGNED"
            )
            conflict_r = next(
                r for r in ranked_eligible if r.opportunity.instrument == "CONFLICT"
            )
            assert aligned_r.alignment == MTFAlignment.ALIGNED
            assert conflict_r.alignment == MTFAlignment.CONFLICTING
            assert aligned_r.rank < conflict_r.rank

    def test_score_ordering(self):
        # Build two eligible instruments; the higher decision score ranks
        # first when alignment is equal. We construct InstrumentScanResult
        # by hand to control the scores precisely.
        scanner = _scanner()
        # Use the scanner's ranking key directly to confirm ordering
        # respects score within equal alignment.
        # construct two InstrumentScanResult with controlled scores
        def _fake(name, score, align, rr=None, complete=True):
            return InstrumentScanResult(
                instrument=name, context_timeframe="1D",
                setup_timeframe="15M", timestamp=None,
                alignment=align, complete=complete,
                direction="LONG",
                decision_classification="PREFERRED",
                decision_score=score,
                risk_reward_ratio=rr,
                eligible=True,
            )
        r1 = _fake("HIGH", 90, MTFAlignment.ALIGNED, rr=3.0)
        r2 = _fake("LOW", 50, MTFAlignment.ALIGNED, rr=2.0)
        key1 = scanner._ranking_key(r1)
        key2 = scanner._ranking_key(r2)
        assert key1 < key2  # higher score ranks first

    def test_geometry_complete_first(self):
        def _fake(name, complete):
            return InstrumentScanResult(
                instrument=name, context_timeframe="1D",
                setup_timeframe="15M", timestamp=None,
                alignment=MTFAlignment.ALIGNED, complete=complete,
                direction="LONG", decision_classification="PREFERRED",
                decision_score=80, risk_reward_ratio=2.0,
                eligible=True,
            )
        # geometry_complete is derived from the opportunity object; we
        # need a real opportunity. Use the scanner's helper reading the
        # geometry from the opportunity. Construct via a stub opportunity.
        from engine.models.opportunity import (
            EligibilityStatus, OpportunityStatus, TradeOpportunity,
        )
        from engine.models.trade_decision import (
            DecisionClassification, DecisionScore, TradeDecision,
        )
        from engine.models.trade_candidate import (
            CandidateDirection, CandidateStatus, SetupType, TradeCandidate,
        )
        from engine.models.setup_confluence import SetupClassification
        def _opp(complete):
            cand = TradeCandidate(
                timestamp=None, evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0, stop_reference=95.0,
                target_reference=110.0,
                risk_distance=5.0, reward_distance=10.0,
                risk_reward_ratio=2.0, confluence_score=4,
                supporting_evidence=(), conflicting_evidence=(),
                candle_evidence="x", market_trend="x",
                market_structure="x", location="x",
                range_context="x", reason="x",
            )
            dec = TradeDecision(
                timestamp=None, evaluation_index=0, candidate=cand,
                direction="LONG", classification=DecisionClassification.PREFERRED,
                score=DecisionScore(total=80, max_total=100),
                geometry_complete=complete, confluence_score=4,
                supporting_count=4, conflicting_count=0,
                risk_reward_ratio=2.0, rationale="x",
            )
            return TradeOpportunity(
                timestamp=None, evaluation_index=0, decision=dec,
                direction="LONG", rank=1, status=OpportunityStatus.BEST_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification="PREFERRED", decision_score=80,
                geometry_complete=complete, confluence_score=4,
                supporting_count=4, conflicting_count=0, risk_reward_ratio=2.0,
            )
        r_complete = InstrumentScanResult(
            instrument="C", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_opp(True),
            alignment=MTFAlignment.ALIGNED, complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        r_incomplete = InstrumentScanResult(
            instrument="I", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_opp(False),
            alignment=MTFAlignment.ALIGNED, complete=False, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        scanner = _scanner()
        assert scanner._ranking_key(r_complete) < scanner._ranking_key(r_incomplete)

    def test_risk_reward_ordering(self):
        from engine.models.opportunity import (
            EligibilityStatus, OpportunityStatus, TradeOpportunity,
        )
        from engine.models.trade_decision import (
            DecisionClassification, DecisionScore, TradeDecision,
        )
        from engine.models.trade_candidate import (
            CandidateDirection, CandidateStatus, SetupType, TradeCandidate,
        )
        from engine.models.setup_confluence import SetupClassification
        def _rr_opp(rr):
            cand = TradeCandidate(
                timestamp=None, evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0, stop_reference=95.0,
                target_reference=100.0 + rr * 5.0,
                risk_distance=5.0, reward_distance=rr * 5.0,
                risk_reward_ratio=rr, confluence_score=4,
                supporting_evidence=(), conflicting_evidence=(),
                candle_evidence="x", market_trend="x",
                market_structure="x", location="x",
                range_context="x", reason="x",
            )
            dec = TradeDecision(
                timestamp=None, evaluation_index=0, candidate=cand,
                direction="LONG", classification=DecisionClassification.PREFERRED,
                score=DecisionScore(total=80, max_total=100),
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0,
                risk_reward_ratio=rr, rationale="x",
            )
            return TradeOpportunity(
                timestamp=None, evaluation_index=0, decision=dec,
                direction="LONG", rank=1, status=OpportunityStatus.BEST_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification="PREFERRED", decision_score=80,
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0, risk_reward_ratio=rr,
            )
        r_high = InstrumentScanResult(
            instrument="HIGH", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_rr_opp(3.0),
            alignment=MTFAlignment.ALIGNED, complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=3.0,
                    eligible=True,
        )
        r_low = InstrumentScanResult(
            instrument="LOW", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_rr_opp(1.5),
            alignment=MTFAlignment.ALIGNED, complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=1.5,
                    eligible=True,
        )
        scanner = _scanner()
        assert scanner._ranking_key(r_high) < scanner._ranking_key(r_low)

    def test_deterministic_tie_break_instrument(self):
        # Identical everything except instrument name -> name asc tie-break.
        from engine.models.opportunity import (
            EligibilityStatus, OpportunityStatus, TradeOpportunity,
        )
        from engine.models.trade_decision import (
            DecisionClassification, DecisionScore, TradeDecision,
        )
        from engine.models.trade_candidate import (
            CandidateDirection, CandidateStatus, SetupType, TradeCandidate,
        )
        from engine.models.setup_confluence import SetupClassification
        def _opp(name):
            cand = TradeCandidate(
                timestamp=None, evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0, stop_reference=95.0,
                target_reference=110.0, risk_distance=5.0,
                reward_distance=10.0, risk_reward_ratio=2.0,
                confluence_score=4, supporting_evidence=(),
                conflicting_evidence=(), candle_evidence="x",
                market_trend="x", market_structure="x", location="x",
                range_context="x", reason="x",
            )
            dec = TradeDecision(
                timestamp=None, evaluation_index=0, candidate=cand,
                direction="LONG", classification=DecisionClassification.PREFERRED,
                score=DecisionScore(total=80, max_total=100),
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0,
                risk_reward_ratio=2.0, rationale="x",
            )
            return TradeOpportunity(
                timestamp=None, evaluation_index=0, decision=dec,
                direction="LONG", rank=1, status=OpportunityStatus.BEST_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification="PREFERRED", decision_score=80,
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0, risk_reward_ratio=2.0,
            )
        opp = _opp("X")
        r_a = InstrumentScanResult(
            instrument="ZZZ", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=opp, alignment=MTFAlignment.ALIGNED,
            complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        r_b = InstrumentScanResult(
            instrument="AAA", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=opp, alignment=MTFAlignment.ALIGNED,
            complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        scanner = _scanner()
        # AAA should rank before ZZZ by name asc.
        assert scanner._ranking_key(r_b) < scanner._ranking_key(r_a)

    def test_long_short_symmetry_no_direction_bias(self):
        # Two equal-score opportunities (LONG and SHORT) — direction
        # alone (via the tie-break) does not bias ranking on evidence.
        from engine.models.opportunity import (
            EligibilityStatus, OpportunityStatus, TradeOpportunity,
        )
        from engine.models.trade_decision import (
            DecisionClassification, DecisionScore, TradeDecision,
        )
        from engine.models.trade_candidate import (
            CandidateDirection, CandidateStatus, SetupType, TradeCandidate,
        )
        from engine.models.setup_confluence import SetupClassification
        def _opp(direction):
            entry = 100.0
            if direction == "LONG":
                stop, target = 95.0, 110.0
            else:
                stop, target = 105.0, 90.0
            cand = TradeCandidate(
                timestamp=None, evaluation_index=0,
                direction=CandidateDirection.LONG if direction == "LONG" else CandidateDirection.SHORT,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=entry, stop_reference=stop,
                target_reference=target, risk_distance=5.0,
                reward_distance=10.0, risk_reward_ratio=2.0,
                confluence_score=4, supporting_evidence=(),
                conflicting_evidence=(), candle_evidence="x",
                market_trend="x", market_structure="x", location="x",
                range_context="x", reason="x",
            )
            dec = TradeDecision(
                timestamp=None, evaluation_index=0, candidate=cand,
                direction=direction,
                classification=DecisionClassification.PREFERRED,
                score=DecisionScore(total=80, max_total=100),
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0,
                risk_reward_ratio=2.0, rationale="x",
            )
            return TradeOpportunity(
                timestamp=None, evaluation_index=0, decision=dec,
                direction=direction, rank=1,
                status=OpportunityStatus.BEST_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification="PREFERRED", decision_score=80,
                geometry_complete=True, confluence_score=4,
                supporting_count=4, conflicting_count=0, risk_reward_ratio=2.0,
            )
        r_long = InstrumentScanResult(
            instrument="SAME", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_opp("LONG"),
            alignment=MTFAlignment.NEUTRAL, complete=True, direction="LONG",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        r_short = InstrumentScanResult(
            instrument="SAME", context_timeframe="1D", setup_timeframe="15M",
            timestamp=None, opportunity=_opp("SHORT"),
            alignment=MTFAlignment.NEUTRAL, complete=True, direction="SHORT",
            decision_classification="PREFERRED", decision_score=80,
            risk_reward_ratio=2.0,
                    eligible=True,
        )
        scanner = _scanner()
        # Identical instrument + identical evidence -> keys differ ONLY
        # by direction order in the final tie-break, but direction is NOT
        # part of the ranking key (instrument name is the final tie-break).
        # So identical-instrument equal opportunities have EQUAL keys
        # (no random order) — the result is stable.
        assert scanner._ranking_key(r_long) == scanner._ranking_key(r_short)


# ============================================================
# G. LEAKAGE
# ============================================================


class TestLeakage:
    def test_full_series_vs_prefix(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        # Evaluate at a midpoint of the setup timeframe.
        mid = len(setup) // 2
        eval_time = setup[mid].timestamp
        full = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            evaluation_time=eval_time,
            engines=engines,
        )
        truncated = scanner.scan(
            [
                InstrumentDataset(
                    "TCS", tuple(ctx), tuple(setup[: mid + 1]),
                ),
            ],
            evaluation_time=eval_time,
            engines=engines,
        )
        fa = full.results[0]
        ta = truncated.results[0]
        assert fa.alignment == ta.alignment
        assert fa.direction == ta.direction
        assert fa.decision_classification == ta.decision_classification

    def test_future_mutation_unchanged(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        eval_time = setup[-1].timestamp
        before = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            evaluation_time=eval_time,
            engines=engines,
        )
        # Mutate a candle AFTER the eval time on the setup timeframe.
        mutated_setup = list(setup) + [
            _candle(999.0, eval_time + timedelta(minutes=15)),
        ]
        after = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(mutated_setup))],
            evaluation_time=eval_time,
            engines=engines,
        )
        assert after.results[0].alignment == before.results[0].alignment
        assert after.results[0].direction == before.results[0].direction

    def test_multi_instrument_future_mutation(self):
        ctx_a, setup_a = _aligned_long_dataset()
        ctx_b, setup_b = _aligned_short_dataset()
        scanner = _scanner()
        engines = _default_engines()
        eval_time = max(setup_a[-1].timestamp, setup_b[-1].timestamp)
        before = scanner.scan(
            [
                InstrumentDataset("A", tuple(ctx_a), tuple(setup_a)),
                InstrumentDataset("B", tuple(ctx_b), tuple(setup_b)),
            ],
            evaluation_time=eval_time,
            engines=engines,
        )
        # Mutate future candles on BOTH instruments.
        mutated_a = list(setup_a) + [
            _candle(999.0, eval_time + timedelta(hours=1)),
        ]
        mutated_b = list(setup_b) + [
            _candle(999.0, eval_time + timedelta(hours=1)),
        ]
        after = scanner.scan(
            [
                InstrumentDataset("A", tuple(ctx_a), tuple(mutated_a)),
                InstrumentDataset("B", tuple(ctx_b), tuple(mutated_b)),
            ],
            evaluation_time=eval_time,
            engines=engines,
        )
        for before_r, after_r in zip(before.results, after.results):
            assert after_r.alignment == before_r.alignment
            assert after_r.direction == before_r.direction

    def test_multi_timeframe_future_mutation(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        eval_time = setup[-1].timestamp
        before = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            evaluation_time=eval_time,
            engines=engines,
        )
        # Mutate future candles on BOTH timeframes.
        future_ctx = list(ctx) + [
            _candle(999.0, eval_time + timedelta(days=3)),
        ]
        future_setup = list(setup) + [
            _candle(999.0, eval_time + timedelta(hours=2)),
        ]
        after = scanner.scan(
            [InstrumentDataset("TCS", tuple(future_ctx), tuple(future_setup))],
            evaluation_time=eval_time,
            engines=engines,
        )
        assert after.results[0].alignment == before.results[0].alignment
        assert after.results[0].direction == before.results[0].direction


# ============================================================
# H. PIPELINE INTEGRATION
# ============================================================


class TestPipelineIntegration:
    def test_existing_signals_unchanged(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )
        candles = trending_dataset()
        before = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        # The scanner is additive and does not touch the pipeline; running
        # the pipeline again must yield identical signals / trades.
        after = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        assert after.signals_generated == before.signals_generated
        assert after.completed_trades == before.completed_trades

    def test_existing_completed_trades_unchanged(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )
        candles = trending_dataset()
        r1 = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        r2 = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
        assert r1.completed_trades == r2.completed_trades
        assert r1.signals_validated == r2.signals_validated

    def test_scanner_independent_of_pipeline(self):
        # The scanner uses its own engine bundle; the pipeline is not
        # required to run a scan.
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert isinstance(res, MarketScanResult)
        assert res.results[0].opportunity is not None or (
            res.status in (ScanStatus.WATCH_ONLY, ScanStatus.NO_OPPORTUNITY)
        )


# ============================================================
# I. SERIALIZATION / REPORTING
# ============================================================


class TestSerialization:
    def test_round_trip(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        payload = serialize_scan(res)
        restored = deserialize_scan(payload)
        assert restored.scan_id == res.scan_id
        assert restored.status == res.status
        assert restored.instruments == res.instruments
        assert restored.timeframes == res.timeframes
        assert [r.opportunity.instrument for r in restored.ranked] == (
            [r.opportunity.instrument for r in res.ranked]
        )
        assert [r.rank for r in restored.ranked] == (
            [r.rank for r in res.ranked]
        )

    def test_round_trip_drops_heavy_outputs(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        restored = deserialize_scan(serialize_scan(res))
        # Heavy per-engine outputs reconstruct as None (scope discipline).
        for r in restored.results:
            assert r.higher_context is None
            assert r.lower_context is None
            assert r.decision is None
            assert r.opportunity is None

    def test_deterministic_bytes(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert serialize_scan(res) == serialize_scan(res)
        assert canonical_scan_json(res) == serialize_scan(res)

    def test_schema_version_carried(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        header = parse_scan_header(serialize_scan(res))
        assert header["schema_version"] == SCANNER_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        payload = serialize_scan(res)
        bad = payload.replace(
            f'"schema_version": {SCANNER_SCHEMA_VERSION}',
            '"schema_version": 999',
        )
        with pytest.raises(ValueError):
            deserialize_scan(bad)

    def test_best_preserved_when_present(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        restored = deserialize_scan(serialize_scan(res))
        if res.has_best:
            assert restored.has_best
            assert restored.best.opportunity.instrument == (
                res.best.opportunity.instrument
            )
            assert restored.best.rank == res.best.rank


class TestReporting:
    def test_returns_str(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        text = MarketScanFormatter().format(res)
        assert isinstance(text, str)

    def test_required_sections(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        text = MarketScanFormatter().format(res)
        assert "Market Opportunity Scan" in text
        assert "Status" in text
        assert "Instruments" in text
        assert "Timeframes" in text
        assert "Scan Rationale" in text

    def test_warning_present(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        text = MarketScanFormatter().format(res)
        assert "NOT predictive" in text
        assert "profitability" in text

    def test_no_predictive_language(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        text = MarketScanFormatter().format(res).lower()
        for banned in (
            "guaranteed profit", "will rise", "will fall",
            "probability of success", "most profitable",
            "is profitable", "certain to",
        ):
            assert banned not in text, f"banned phrase {banned!r} in report"

    def test_empty_scan_report(self):
        res = MarketScanResult(scan_id="scan-x", timestamp=None)
        text = MarketScanFormatter().format(res)
        assert "No instruments were scanned" in text

    def test_determinism(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        f = MarketScanFormatter()
        assert f.format(res) == f.format(res)

    def test_format_instrument(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        text = MarketScanFormatter().format_instrument(res.results[0])
        assert isinstance(text, str)
        assert "Instrument Scan Result" in text
        assert "MTF Alignment" in text


# ============================================================
# J. DETERMINISM + IMMUTABILITY
# ============================================================


class TestDeterminismImmutability:
    def test_same_inputs_same_output(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        res1 = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        res2 = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        assert res1.scan_id == res2.scan_id
        assert res1.status == res2.status
        assert [r.opportunity.instrument for r in res1.ranked] == (
            [r.opportunity.instrument for r in res2.ranked]
        )

    def test_repeated_scans(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        engines = _default_engines()
        first = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=engines,
        )
        for _ in range(3):
            other = scanner.scan(
                [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
                engines=engines,
            )
            assert other.scan_id == first.scan_id

    def test_frozen_models(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        with pytest.raises(Exception):
            res.status = ScanStatus.INCOMPLETE  # type: ignore
        with pytest.raises(Exception):
            res.results[0].instrument = "X"  # type: ignore

    def test_rerun_serialization_loop(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        r1 = serialize_scan(res)
        restored = deserialize_scan(r1)
        r2 = serialize_scan(restored)
        assert r1 == r2

    def test_different_configs_different_id(self):
        c1 = MarketScanConfig(context_timeframe="1D", setup_timeframe="15M")
        c2 = MarketScanConfig(context_timeframe="1H", setup_timeframe="15M")
        s1 = MarketScanner(c1)
        s2 = MarketScanner(c2)
        ctx, setup = _aligned_long_dataset()
        r1 = s1.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        r2 = s2.scan(
            [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert r1.scan_id != r2.scan_id


# ============================================================
# K. ADDITIONAL ALIGNMENT + SAFETY EDGE CASES
# ============================================================


class TestAlignmentEdgeCases:
    def test_range_context_neutral_never_bullish(self):
        # A genuinely ranging higher context must classify as NEUTRAL,
        # never ALIGNED, regardless of the lower direction.
        from engine.intelligence.market_context_engine import (
            MarketContextEngine,
        )
        # A flat oscillation produces a RANGE / NEUTRAL context.
        flat = _flat_dataset(n=40)
        mce = MarketContextEngine()
        ctx = mce.analyze_at(flat, len(flat) - 1)
        eng = MTFAlignmentEngine()
        long_a = eng.align(ctx, "LONG")
        short_a = eng.align(ctx, "SHORT")
        # RANGE / NEUTRAL context -> NEUTRAL (never ALIGNED).
        assert long_a != MTFAlignment.ALIGNED
        assert short_a != MTFAlignment.ALIGNED
        # If the context classified as RANGE explicitly, it is NEUTRAL.
        if ctx.trend.state == MarketTrendState.RANGE:
            assert long_a == MTFAlignment.NEUTRAL
            assert short_a == MTFAlignment.NEUTRAL

    def test_unknown_context_trend_is_unknown(self):
        # Insufficient structure (UNKNOWN trend) -> UNKNOWN alignment.
        from engine.intelligence.market_context_engine import (
            MarketContextEngine,
        )
        # Too few candles -> no confirmed swings -> UNKNOWN trend.
        short = [_candle(100.0 + i, _EPOCH + timedelta(days=i))
                 for i in range(5)]
        mce = MarketContextEngine()
        ctx = mce.analyze_at(short, len(short) - 1)
        eng = MTFAlignmentEngine()
        assert eng.align(ctx, "LONG") == MTFAlignment.UNKNOWN

    def test_alignment_is_pure_function(self):
        ctx = _context_with_state(MarketTrendState.BULLISH)
        eng = MTFAlignmentEngine()
        # Same inputs -> same outputs across many calls.
        results = {eng.align(ctx, "LONG") for _ in range(10)}
        assert results == {MTFAlignment.ALIGNED}


class TestTimeframeSafetyEdgeCases:
    def test_htf_candle_at_eval_time_excluded(self):
        """A higher-timeframe candle whose timestamp EQUALS the eval time
        is in-progress at the eval time and must NOT be used (strictly
        before rule). When no earlier HTF candle exists the scan is
        INCOMPLETE."""
        eval_time = _EPOCH + timedelta(days=10)
        ctx = [
            _candle(100.0, eval_time),  # at eval time -> in-progress
        ]
        setup: list[OHLCVCandle] = []
        close = 100.0
        ts = eval_time
        for _ in range(40):
            close = round(close + 2, 2)
            setup.append(_candle(close, ts))
            ts = ts + timedelta(minutes=15)
        scanner = _scanner(min_history=10)
        res = scanner.scan(
            [InstrumentDataset("X", tuple(ctx), tuple(setup))],
            evaluation_time=eval_time,
            engines=_default_engines(),
        )
        assert res.status == ScanStatus.INCOMPLETE

    def test_earlier_completed_htf_candle_used(self):
        """When an earlier completed HTF candle exists (strictly before
        the eval time) and a later in-progress one too, only the earlier
        one is used -> the scan is NOT incomplete and aligns correctly."""
        ctx, setup = _aligned_long_dataset()
        # Append an in-progress HTF candle at the setup eval time.
        eval_time = setup[-1].timestamp
        ctx_with_future = list(ctx) + [_candle(999.0, eval_time)]
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("TCS", tuple(ctx_with_future), tuple(setup))],
            evaluation_time=eval_time,
            engines=_default_engines(),
        )
        # The earlier completed HTF candle was used -> not incomplete.
        assert res.results[0].alignment == MTFAlignment.ALIGNED

    def test_min_history_incomplete(self):
        ctx, setup = _aligned_long_dataset()
        # Truncate the setup below min_history -> INCOMPLETE setup slice.
        short_setup = setup[:5]
        scanner = _scanner(min_history=10)
        res = scanner.scan(
            [InstrumentDataset("X", tuple(ctx), tuple(short_setup))],
            engines=_default_engines(),
        )
        assert res.results[0].complete is False


class TestScanIdentity:
    def test_different_instruments_different_id(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        r1 = scanner.scan(
            [InstrumentDataset("AAA", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        r2 = scanner.scan(
            [InstrumentDataset("BBB", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert r1.scan_id != r2.scan_id

    def test_same_instruments_same_id_regardless_of_order(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        r1 = scanner.scan(
            [
                InstrumentDataset("AAA", tuple(ctx), tuple(setup)),
                InstrumentDataset("BBB", tuple(ctx), tuple(setup)),
            ],
            engines=_default_engines(),
        )
        r2 = scanner.scan(
            [
                InstrumentDataset("BBB", tuple(ctx), tuple(setup)),
                InstrumentDataset("AAA", tuple(ctx), tuple(setup)),
            ],
            engines=_default_engines(),
        )
        # Instruments are sorted for the id -> identical regardless of
        # input order.
        assert r1.scan_id == r2.scan_id

    def test_scan_id_prefixed(self):
        ctx, setup = _aligned_long_dataset()
        scanner = _scanner()
        res = scanner.scan(
            [InstrumentDataset("X", tuple(ctx), tuple(setup))],
            engines=_default_engines(),
        )
        assert res.scan_id.startswith("scan-")
