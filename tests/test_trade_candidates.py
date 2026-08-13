"""
Tests for the trade candidate generation layer (Sprint 11R).

Coverage:

A. Model validation (valid LONG / SHORT / no-candidate, invalid
   direction/reference combinations, invalid risk, invalid reward,
   invalid R:R, immutability)
B. Configuration validation + defaults
C. Candidate generation (bullish -> LONG, bearish -> SHORT,
   conflicting evidence -> no automatic candidate, insufficient
   evidence -> no candidate, range market handled conservatively,
   missing entry / stop / target)
D. Setup-type classification (BREAKOUT / TREND_CONTINUATION /
   STRUCTURE_CONTINUATION / generic fallback)
E. Risk/reward (LONG and SHORT, missing refs, invalid risk/reward)
F. Point-in-time correctness (prefix == full series; future mutation
   leaves candidate(T) unchanged; geometry/direction/setup stable)
G. Pipeline integration (additive, signal/trade behaviour unchanged,
   disabled reproduces pre-11R, candidate attached, pipeline ==
   standalone prefix)
H. Reporting (sections, warning present, no predictive language,
   returns str, sequence, determinism)
I. Determinism + immutability
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.swing_config import SwingConfig
from engine.config.trade_candidate_config import TradeCandidateConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.models.market_context import (
    MarketContext,
    MarketTrend,
    MarketTrendState,
    PriceLocation,
    RangeContext,
    RangeState,
    SupportResistanceContext,
)
from engine.models.market_structure import StructurePoint, StructureType
from engine.models.ohlcv import OHLCVCandle
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceItem,
    EvidenceSource,
    SetupAssessment,
    SetupClassification,
    SetupDirection,
    SetupEvidence,
)
from engine.models.structure_analysis import StructureBias
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)
from engine.models.trade_candidate import (
    CandidateDirection,
    CandidateStatus,
    SetupType,
    TradeCandidate,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_candidates import TradeCandidateFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# CANDLE / DATASET HELPERS
# ============================================================


def candle(
    close: float,
    high: float,
    low: float,
    index: int,
    volume: float = 1000.0,
) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def bullish_dataset() -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    close = 100.0
    idx = 0
    for _ in range(4):
        for _ in range(3):
            close = round(close + 6, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close - 3, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def bearish_dataset() -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    close = 200.0
    idx = 0
    for _ in range(4):
        for _ in range(3):
            close = round(close - 6, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close + 3, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def range_dataset() -> list[OHLCVCandle]:
    vals = [100, 105, 110, 105, 100, 105, 110, 105, 100, 105, 110, 105, 100]
    return [candle(cp, cp + 2, cp - 2, i) for i, cp in enumerate(vals)]


def hammer_candle(index: int, base_close: float, body: float = 2.0):
    close = base_close + body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=close + body,
        low=close - (body * 3.0),
        close=close,
        volume=1000.0,
    )


def shooting_star_candle(index: int, base_close: float, body: float = 2.0):
    close = base_close - body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=close + (body * 3.0),
        low=close - body,
        close=close,
        volume=1000.0,
    )


def make_engines(lookback: int = 2):
    swing_cfg = SwingConfig(lookback=lookback)
    return (
        CandlePatternEngine(),
        MarketContextEngine(swing_config=swing_cfg),
        SetupConfluenceEngine(),
        TradeCandidateEngine(),
    )


def candidate_at(
    pat_engine: CandlePatternEngine,
    mc_engine: MarketContextEngine,
    setup_engine: SetupConfluenceEngine,
    cand_engine: TradeCandidateEngine,
    candles: list[OHLCVCandle],
    index: int,
) -> TradeCandidate:
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    a = setup_engine.assess(pats, ctx, index, candles[index].timestamp)
    return cand_engine.generate(
        a, ctx, index, candles[index].timestamp, candles[index].close,
    )


# ============================================================
# HAND-BUILT MODEL HELPERS
# ============================================================


def _swing(price: float, stype: SwingType, index: int = 0) -> SwingPoint:
    return SwingPoint(
        timestamp=_EPOCH + timedelta(days=index),
        index=index,
        price=price,
        swing_type=stype,
        confirmation_index=index,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        strength=SwingStrength.NORMAL,
    )


def _structure(stype: StructureType, price: float, index: int = 0):
    return StructurePoint(
        swing=_swing(price, _swing_type_for(stype), index),
        structure=stype,
    )


def _swing_type_for(stype: StructureType) -> SwingType:
    if stype in (
        StructureType.HIGHER_HIGH,
        StructureType.LOWER_HIGH,
        StructureType.FIRST_HIGH,
    ):
        return SwingType.HIGH
    return SwingType.LOW


def _make_context(
    trend_state: MarketTrendState = MarketTrendState.UNKNOWN,
    bias: StructureBias = StructureBias.UNKNOWN,
    structure_intact: bool = False,
    range_state: RangeState = RangeState.UNKNOWN,
    location: PriceLocation = PriceLocation.UNKNOWN,
    support: float | None = None,
    resistance: float | None = None,
    recent_structures: tuple[StructureType, ...] = (),
    index: int = 0,
) -> MarketContext:
    rng_high = rng_low = rng_width = rng_position = None
    if range_state == RangeState.IN_RANGE:
        rng_high = 110.0
        rng_low = 100.0
        rng_width = 10.0
        rng_position = 0.5
    return MarketContext(
        index=index,
        trend=MarketTrend(
            state=trend_state,
            bias=bias,
            structure_intact=structure_intact,
            reasons=["test"],
        ),
        range=RangeContext(
            state=range_state,
            high=rng_high,
            low=rng_low,
            width=rng_width,
            position=rng_position,
            reason="test",
        ),
        support_resistance=SupportResistanceContext(
            support=support,
            resistance=resistance,
            distance_to_support=None,
            distance_to_resistance=None,
            location=location,
        ),
        recent_structure=tuple(
            _structure(st, 100.0) for st in recent_structures
        ),
        confirmed_swings=len(recent_structures),
    )


def _evidence_item(
    source: EvidenceSource,
    direction: SetupDirection,
    alignment: EvidenceAlignment,
    label: str = "x",
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        direction=direction,
        alignment=alignment,
        label=label,
        reason="test",
    )


def _make_assessment(
    direction: SetupDirection = SetupDirection.BULLISH,
    classification: SetupClassification = SetupClassification.POTENTIAL_SETUP,
    confluence_score: int = 3,
    trend_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    structure_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    candle_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    location_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    range_alignment: EvidenceAlignment = EvidenceAlignment.NEUTRAL,
    candle_evidence: str = "HAMMER",
    structure_evidence: str = "HIGHER_HIGH / HIGHER_LOW",
    trend_evidence: str = "BULLISH",
    location_evidence: str = "NEAR_SUPPORT",
    regime_evidence: str = "NOT_IN_RANGE",
    index: int = 0,
) -> SetupAssessment:
    """Build a fully-controlled SetupAssessment for candidate tests."""

    trend = _evidence_item(
        EvidenceSource.TREND, direction, trend_alignment, trend_evidence,
    )
    structure = _evidence_item(
        EvidenceSource.STRUCTURE, direction, structure_alignment,
        structure_evidence,
    )
    candle = _evidence_item(
        EvidenceSource.CANDLE, direction, candle_alignment, candle_evidence,
    )
    location = _evidence_item(
        EvidenceSource.LOCATION, direction, location_alignment,
        location_evidence,
    )
    range_item = _evidence_item(
        EvidenceSource.RANGE, SetupDirection.NEUTRAL, range_alignment,
        regime_evidence,
    )

    all_items = (trend, structure, candle, location, range_item)
    supporting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.ALIGNED
    )
    conflicting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.CONFLICTING
    )

    return SetupAssessment(
        index=index,
        timestamp=_EPOCH + timedelta(days=index),
        direction=direction,
        classification=classification,
        confluence_score=confluence_score,
        evidence=SetupEvidence(
            trend=trend,
            structure=structure,
            candle=candle,
            location=location,
            range=range_item,
            supporting=supporting,
            conflicting=conflicting,
        ),
        candle_evidence=candle_evidence,
        structure_evidence=structure_evidence,
        trend_evidence=trend_evidence,
        location_evidence=location_evidence,
        regime_evidence=regime_evidence,
        reason="test setup",
    )


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_enums_have_expected_members(self):
        assert {d.name for d in CandidateDirection} == {
            "LONG", "SHORT", "NONE",
        }
        assert {s.name for s in CandidateStatus} == {
            "NO_CANDIDATE", "WATCH", "CANDIDATE",
        }
        assert SetupType.SETUP_CANDIDATE.name == "SETUP_CANDIDATE"

    def test_trade_candidate_frozen_and_slots(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.NO_SETUP,
        )
        with pytest.raises(Exception):
            c.direction = CandidateDirection.LONG  # type: ignore
        assert hasattr(TradeCandidate, "__slots__")

    def test_is_candidate_property(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.LONG,
            status=CandidateStatus.CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.POTENTIAL_SETUP,
            entry_reference=100.0,
            stop_reference=95.0,
            target_reference=110.0,
            risk_distance=5.0,
            reward_distance=10.0,
            risk_reward_ratio=2.0,
        )
        assert c.is_candidate is True
        assert c.geometry_complete is True

    def test_geometry_complete_false_when_refs_missing(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.LONG,
            status=CandidateStatus.CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.POTENTIAL_SETUP,
            entry_reference=100.0,
            stop_reference=None,
            target_reference=None,
        )
        assert c.geometry_complete is False

    def test_valid_long_candidate(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.LONG,
            status=CandidateStatus.CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.POTENTIAL_SETUP,
            entry_reference=100.0,
            stop_reference=95.0,
            target_reference=110.0,
            risk_distance=5.0,
            reward_distance=10.0,
            risk_reward_ratio=2.0,
        )
        assert c.direction == CandidateDirection.LONG
        assert c.risk_reward_ratio == 2.0

    def test_valid_short_candidate(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.SHORT,
            status=CandidateStatus.CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.POTENTIAL_SETUP,
            entry_reference=100.0,
            stop_reference=105.0,
            target_reference=90.0,
            risk_distance=5.0,
            reward_distance=10.0,
            risk_reward_ratio=2.0,
        )
        assert c.direction == CandidateDirection.SHORT
        assert c.risk_reward_ratio == 2.0

    def test_no_candidate_representation(self):
        c = TradeCandidate(
            timestamp=None,
            evaluation_index=0,
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            setup_type=SetupType.SETUP_CANDIDATE,
            setup_classification=SetupClassification.NO_SETUP,
        )
        assert c.direction == CandidateDirection.NONE
        assert c.entry_reference is None
        assert c.risk_reward_ratio is None

    def test_candidate_must_have_direction(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.NONE,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
            )

    def test_long_requires_stop_below_entry(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=105.0,  # above entry
                target_reference=110.0,
                risk_distance=5.0,
                reward_distance=10.0,
                risk_reward_ratio=2.0,
            )

    def test_short_requires_stop_above_entry(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.SHORT,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=95.0,  # below entry
                target_reference=90.0,
                risk_distance=5.0,
                reward_distance=10.0,
                risk_reward_ratio=2.0,
            )

    def test_long_requires_target_above_entry(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=95.0,
                target_reference=98.0,  # below entry
                risk_distance=5.0,
                reward_distance=-2.0,
                risk_reward_ratio=-0.4,
            )

    def test_invalid_risk_rejected(self):
        # Non-positive risk rejected at construction.
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=100.0,  # zero risk
                target_reference=110.0,
                risk_distance=0.0,
                reward_distance=10.0,
                risk_reward_ratio=float("inf"),
            )

    def test_invalid_reward_rejected(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=95.0,
                target_reference=95.0,  # reward negative
                risk_distance=5.0,
                reward_distance=-5.0,
                risk_reward_ratio=-1.0,
            )

    def test_invalid_rr_ratio_rejected(self):
        # ratio inconsistent with reward/risk.
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=95.0,
                target_reference=110.0,
                risk_distance=5.0,
                reward_distance=10.0,
                risk_reward_ratio=3.0,  # wrong
            )

    def test_risk_inconsistent_with_entry_stop_rejected(self):
        with pytest.raises(ValueError):
            TradeCandidate(
                timestamp=None,
                evaluation_index=0,
                direction=CandidateDirection.LONG,
                status=CandidateStatus.CANDIDATE,
                setup_type=SetupType.SETUP_CANDIDATE,
                setup_classification=SetupClassification.POTENTIAL_SETUP,
                entry_reference=100.0,
                stop_reference=95.0,
                target_reference=110.0,
                risk_distance=99.0,  # inconsistent
                reward_distance=10.0,
                risk_reward_ratio=10.0 / 99.0,
            )


# ============================================================
# B. CONFIGURATION
# ============================================================


class TestConfiguration:
    def test_defaults(self):
        cfg = TradeCandidateConfig()
        assert cfg.min_confluence_for_candidate == 3
        assert cfg.allow_range_setups is False
        assert cfg.min_risk_reward_ratio is None

    def test_min_confluence_below_one_rejected(self):
        with pytest.raises(ValueError):
            TradeCandidateConfig(min_confluence_for_candidate=0)

    def test_min_rr_non_positive_rejected(self):
        with pytest.raises(ValueError):
            TradeCandidateConfig(min_risk_reward_ratio=0.0)
        with pytest.raises(ValueError):
            TradeCandidateConfig(min_risk_reward_ratio=-1.0)

    def test_min_rr_positive_accepted(self):
        cfg = TradeCandidateConfig(min_risk_reward_ratio=1.5)
        assert cfg.min_risk_reward_ratio == 1.5

    def test_config_frozen(self):
        cfg = TradeCandidateConfig()
        with pytest.raises(Exception):
            cfg.min_confluence_for_candidate = 5  # type: ignore


# ============================================================
# C. CANDIDATE GENERATION
# ============================================================


class TestCandidateGeneration:
    def test_no_assessment_returns_no_candidate(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, 100.0)
        assert c.status == CandidateStatus.NO_CANDIDATE
        assert c.direction == CandidateDirection.NONE

    def test_bullish_potential_setup_becomes_long_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=4,
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            range_state=RangeState.NOT_IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
            recent_structures=(
                StructureType.HIGHER_HIGH, StructureType.HIGHER_LOW,
            ),
        )
        c = eng.generate(a, ctx, 0, _EPOCH, 100.0)
        assert c.status == CandidateStatus.CANDIDATE
        assert c.direction == CandidateDirection.LONG
        assert c.entry_reference == 100.0
        assert c.stop_reference == 95.0
        assert c.target_reference == 110.0
        assert c.risk_distance == 5.0
        assert c.reward_distance == 10.0
        assert c.risk_reward_ratio == 2.0
        assert c.geometry_complete is True

    def test_bearish_potential_setup_becomes_short_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BEARISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=4,
            trend_evidence="BEARISH",
            structure_evidence="LOWER_HIGH / LOWER_LOW",
            candle_evidence="SHOOTING_STAR",
            location_evidence="NEAR_RESISTANCE",
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            range_state=RangeState.NOT_IN_RANGE,
            location=PriceLocation.NEAR_RESISTANCE,
            support=90.0,
            resistance=105.0,
            recent_structures=(
                StructureType.LOWER_HIGH, StructureType.LOWER_LOW,
            ),
        )
        c = eng.generate(a, ctx, 0, _EPOCH, 100.0)
        assert c.status == CandidateStatus.CANDIDATE
        assert c.direction == CandidateDirection.SHORT
        assert c.entry_reference == 100.0
        assert c.stop_reference == 105.0
        assert c.target_reference == 90.0
        assert c.risk_distance == 5.0
        assert c.reward_distance == 10.0
        assert c.risk_reward_ratio == 2.0

    def test_no_setup_classification_returns_no_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            classification=SetupClassification.NO_SETUP,
            confluence_score=0,
        )
        ctx = _make_context()
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.status == CandidateStatus.NO_CANDIDATE
        assert c.entry_reference is None

    def test_watch_classification_returns_watch_not_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            classification=SetupClassification.WATCH,
            confluence_score=1,
        )
        ctx = _make_context(support=95.0, resistance=110.0)
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.status == CandidateStatus.WATCH
        # WATCH carries no trade geometry.
        assert c.entry_reference is None
        assert c.stop_reference is None
        assert c.risk_reward_ratio is None

    def test_conflicting_evidence_no_automatic_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.status == CandidateStatus.WATCH
        assert len(c.conflicting_evidence) > 0
        assert c.entry_reference is None

    def test_insufficient_confluence_downgraded_to_watch(self):
        eng = TradeCandidateConfig  # noqa
        cfg = TradeCandidateConfig(min_confluence_for_candidate=5)
        e = TradeCandidateEngine(cfg)
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = e.generate(a, ctx, 0, None, 100.0)
        assert c.status == CandidateStatus.WATCH
        assert c.entry_reference is None

    def test_neutral_direction_potential_setup_downgraded_to_watch(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.NEUTRAL,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(support=95.0, resistance=110.0)
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.status == CandidateStatus.WATCH
        assert c.direction == CandidateDirection.NONE

    def test_range_market_handled_conservatively(self):
        eng = TradeCandidateEngine()  # allow_range_setups=False default
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            range_state=RangeState.IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=100.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 105.0)
        assert c.status == CandidateStatus.WATCH
        assert "range" in c.reason.lower()

    def test_range_setup_allowed_when_configured(self):
        e = TradeCandidateEngine(
            TradeCandidateConfig(allow_range_setups=True),
        )
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            range_state=RangeState.IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=100.0,
            resistance=110.0,
        )
        c = e.generate(a, ctx, 0, None, 105.0)
        assert c.status == CandidateStatus.CANDIDATE

    def test_missing_entry_when_close_none(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, None)
        # No entry -> no geometry; still a CANDIDATE (status from setup)
        # but geometrically incomplete.
        assert c.status == CandidateStatus.CANDIDATE
        assert c.entry_reference is None
        assert c.stop_reference is None
        assert c.target_reference is None
        assert c.geometry_complete is False

    def test_missing_stop_when_no_support(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=None,  # no support level
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.stop_reference is None
        assert c.risk_distance is None
        assert c.risk_reward_ratio is None

    def test_missing_target_when_no_resistance(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=None,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.target_reference is None
        assert c.risk_distance is None  # can't compute ratio
        assert c.risk_reward_ratio is None

    def test_no_market_context_geometry_incomplete(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        c = eng.generate(a, None, 0, None, 100.0)
        assert c.status == CandidateStatus.CANDIDATE
        assert c.stop_reference is None
        assert c.target_reference is None
        assert c.geometry_complete is False

    def test_min_rr_gate_downgrades_poor_ratio_candidate(self):
        e = TradeCandidateEngine(
            TradeCandidateConfig(min_risk_reward_ratio=3.0),
        )
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            range_state=RangeState.NOT_IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,  # small risk
            resistance=101.0,  # small reward -> poor R:R
        )
        c = e.generate(a, ctx, 0, None, 100.0)
        # risk=2, reward=1 -> R:R 0.5 < 3 -> downgraded.
        assert c.status == CandidateStatus.WATCH
        assert "risk/reward" in c.reason.lower()

    def test_min_rr_gate_does_not_reject_incomplete_geometry(self):
        e = TradeCandidateEngine(
            TradeCandidateConfig(min_risk_reward_ratio=3.0),
        )
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=None,  # no target -> incomplete
        )
        c = e.generate(a, ctx, 0, None, 100.0)
        # Incomplete geometry is NOT rejected by the R:R gate.
        assert c.status == CandidateStatus.CANDIDATE

    def test_evidence_preserved_on_candidate(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=4,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert len(c.supporting_evidence) == a.confluence_score
        assert c.candle_evidence == "HAMMER"
        assert c.market_trend == "BULLISH"
        assert c.market_structure == "HIGHER_HIGH / HIGHER_LOW"
        assert c.location == "NEAR_SUPPORT"
        assert c.range_context == "NOT_IN_RANGE"


# ============================================================
# D. SETUP TYPE CLASSIFICATION
# ============================================================


class TestSetupType:
    def _bullish_candidate(self, eng, location, trend_aligned=True):
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            trend_alignment=(
                EvidenceAlignment.ALIGNED
                if trend_aligned
                else EvidenceAlignment.NEUTRAL
            ),
        )
        ctx = _make_context(
            trend_state=(
                MarketTrendState.BULLISH
                if trend_aligned
                else MarketTrendState.NEUTRAL
            ),
            bias=StructureBias.BULLISH if trend_aligned
            else StructureBias.NEUTRAL,
            range_state=RangeState.NOT_IN_RANGE,
            location=location,
            support=95.0,
            resistance=110.0,
        )
        return eng.generate(a, ctx, 0, None, 100.0)

    def test_long_above_resistance_is_breakout(self):
        c = self._bullish_candidate(
            TradeCandidateEngine(), PriceLocation.ABOVE_RESISTANCE,
        )
        assert c.setup_type == SetupType.BREAKOUT

    def test_long_near_support_trend_aligned_is_trend_continuation(self):
        c = self._bullish_candidate(
            TradeCandidateEngine(), PriceLocation.NEAR_SUPPORT,
            trend_aligned=True,
        )
        assert c.setup_type == SetupType.TREND_CONTINUATION

    def test_long_near_support_trend_not_aligned_is_structure_continuation(
        self,
    ):
        c = self._bullish_candidate(
            TradeCandidateEngine(), PriceLocation.NEAR_SUPPORT,
            trend_aligned=False,
        )
        assert c.setup_type == SetupType.STRUCTURE_CONTINUATION

    def test_long_other_location_is_generic(self):
        c = self._bullish_candidate(
            TradeCandidateEngine(), PriceLocation.INSIDE_RANGE,
        )
        assert c.setup_type == SetupType.SETUP_CANDIDATE

    def test_short_below_support_is_breakout(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BEARISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            trend_evidence="BEARISH",
            structure_evidence="LOWER_HIGH / LOWER_LOW",
            candle_evidence="SHOOTING_STAR",
            location_evidence="BELOW_SUPPORT",
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            range_state=RangeState.NOT_IN_RANGE,
            location=PriceLocation.BELOW_SUPPORT,
            support=90.0,
            resistance=105.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.setup_type == SetupType.BREAKOUT

    def test_short_near_resistance_trend_aligned_is_trend_continuation(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BEARISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            trend_evidence="BEARISH",
            structure_evidence="LOWER_HIGH / LOWER_LOW",
            candle_evidence="SHOOTING_STAR",
            location_evidence="NEAR_RESISTANCE",
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            range_state=RangeState.NOT_IN_RANGE,
            location=PriceLocation.NEAR_RESISTANCE,
            support=90.0,
            resistance=105.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.setup_type == SetupType.TREND_CONTINUATION

    def test_non_candidate_has_generic_setup_type(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            classification=SetupClassification.NO_SETUP,
            confluence_score=0,
        )
        c = eng.generate(a, _make_context(), 0, None, 100.0)
        assert c.setup_type == SetupType.SETUP_CANDIDATE


# ============================================================
# E. RISK / REWARD
# ============================================================


class TestRiskReward:
    def test_long_risk_reward(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=115.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.risk_distance == 5.0
        assert c.reward_distance == 15.0
        assert c.risk_reward_ratio == 3.0

    def test_short_risk_reward(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BEARISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            trend_evidence="BEARISH",
            structure_evidence="LOWER_HIGH / LOWER_LOW",
            candle_evidence="SHOOTING_STAR",
            location_evidence="NEAR_RESISTANCE",
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_RESISTANCE,
            support=85.0,
            resistance=105.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.risk_distance == 5.0
        assert c.reward_distance == 15.0
        assert c.risk_reward_ratio == 3.0

    def test_long_stop_on_wrong_side_invalidates_geometry(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        # support above entry -> stop cannot be on correct side.
        ctx = _make_context(
            location=PriceLocation.BELOW_SUPPORT,
            support=105.0,  # above entry 100
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.stop_reference is None
        assert c.risk_distance is None
        assert c.risk_reward_ratio is None

    def test_short_stop_on_wrong_side_invalidates_geometry(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BEARISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
            trend_evidence="BEARISH",
            location_evidence="NEAR_RESISTANCE",
            candle_evidence="SHOOTING_STAR",
            structure_evidence="LOWER_HIGH / LOWER_LOW",
        )
        # resistance below entry -> stop cannot be above entry.
        ctx = _make_context(
            location=PriceLocation.ABOVE_RESISTANCE,
            support=90.0,
            resistance=95.0,  # below entry 100
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.stop_reference is None
        assert c.risk_distance is None

    def test_long_target_on_wrong_side_invalidates_geometry(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        # resistance below entry -> target cannot be above entry.
        ctx = _make_context(
            location=PriceLocation.ABOVE_RESISTANCE,
            support=90.0,
            resistance=95.0,  # below entry 100
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.target_reference is None
        assert c.reward_distance is None
        assert c.risk_reward_ratio is None


# ============================================================
# F. POINT-IN-TIME CORRECTNESS
# ============================================================


class TestPointInTime:
    def test_prefix_equals_full_series_at_T(self):
        pat, mc, se, tce = make_engines()
        data = list(bullish_dataset()) + [
            hammer_candle(len(bullish_dataset()), bullish_dataset()[-1].close)
        ]
        T = len(data) - 2
        full = candidate_at(pat, mc, se, tce, data, T)
        prefix = candidate_at(pat, mc, se, tce, data[: T + 1], T)
        assert full == prefix

    def test_future_mutation_leaves_candidate_unchanged(self):
        pat, mc, se, tce = make_engines()
        data = list(bullish_dataset())
        T = len(data) - 3
        full = candidate_at(pat, mc, se, tce, data, T)
        mutated = list(data)
        future_index = T + 1
        mutated[future_index] = candle(999.0, 1001.0, 997.0, future_index)
        mut = candidate_at(pat, mc, se, tce, mutated, T)
        assert full == mut

    def test_geometry_direction_setup_stable_after_mutation(self):
        pat, mc, se, tce = make_engines()
        data = list(bullish_dataset()) + [
            hammer_candle(len(bullish_dataset()), bullish_dataset()[-1].close)
        ]
        T = len(data) - 2
        full = candidate_at(pat, mc, se, tce, data, T)
        mutated = list(data)
        mutated[T + 1] = candle(999.0, 1001.0, 997.0, T + 1)
        mut = candidate_at(pat, mc, se, tce, mutated, T)
        assert full.entry_reference == mut.entry_reference
        assert full.stop_reference == mut.stop_reference
        assert full.target_reference == mut.target_reference
        assert full.direction == mut.direction
        assert full.setup_type == mut.setup_type
        assert full.status == mut.status
        assert full.risk_reward_ratio == mut.risk_reward_ratio

    def test_uses_only_assessment_and_context_at_index(self):
        pat, mc, se, tce = make_engines()
        data = list(bearish_dataset()) + [
            shooting_star_candle(
                len(bearish_dataset()), bearish_dataset()[-1].close,
            )
        ]
        T = len(data) - 1
        c = candidate_at(pat, mc, se, tce, data, T)
        # The candidate's index must equal T (no future leak in index).
        assert c.evaluation_index == T


# ============================================================
# G. PIPELINE INTEGRATION
# ============================================================


class TestPipelineIntegration:
    def test_trade_candidate_attached_to_points(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        assert res.evaluation_points > 0
        for p in res.evaluation_points_sequence:
            assert p.trade_candidate is not None

    def test_disabled_reproduces_pre_11r(self):
        candles = trending_dataset()
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=False,
        )
        res = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        for p in res.evaluation_points_sequence:
            assert p.trade_candidate is None

    def test_signals_unchanged_enable_vs_disable(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        assert on.signals_generated == off.signals_generated
        assert on.completed_trades == off.completed_trades
        assert on.eligible_decisions == off.eligible_decisions
        assert on.signals_validated == off.signals_validated

    def test_per_point_signal_state_unchanged(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        assert len(on.evaluation_points_sequence) == len(
            off.evaluation_points_sequence,
        )
        for pa, pb in zip(
            on.evaluation_points_sequence,
            off.evaluation_points_sequence,
        ):
            assert pa.signal_state == pb.signal_state
            assert pa.decision_direction == pb.decision_direction
            assert pa.decision_status == pb.decision_status
            assert pa.suppressed == pb.suppressed

    def test_signal_prices_unchanged(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        on_prices = [
            (s.entry_price, s.stop_loss, s.take_profit) for s in on.signals
        ]
        off_prices = [
            (s.entry_price, s.stop_loss, s.take_profit) for s in off.signals
        ]
        assert on_prices == off_prices

    def test_pipeline_point_equals_standalone_prefix(self):
        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        pipe = HistoricalEvaluationPipeline(cfg)
        res = pipe.evaluate(candles)
        pat, mc, se, tce = make_engines(lookback=2)
        point = res.evaluation_points_sequence[0]
        t = point.index
        standalone = candidate_at(pat, mc, se, tce, candles, t)
        assert point.trade_candidate == standalone

    def test_pipeline_candidate_status_distribution_has_candidates(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        statuses = {
            p.trade_candidate.status for p in res.evaluation_points_sequence
        }
        # The trending dataset produces at least one CANDIDATE.
        assert CandidateStatus.CANDIDATE in statuses

    def test_pipeline_candidate_no_future_leak(self):
        """Pipeline candidate(T) is unaffected by mutating candle T+1."""

        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(candles)
        # Pick a mid-sequence point with a successor.
        points = res.evaluation_points_sequence
        idx = len(points) // 2
        t = points[idx].index
        original = points[idx].trade_candidate

        mutated = list(candles)
        if t + 1 < len(mutated):
            mutated[t + 1] = candle(999.0, 1001.0, 997.0, t + 1)
        res2 = HistoricalEvaluationPipeline(cfg).evaluate(mutated)
        point2 = next(
            p for p in res2.evaluation_points_sequence if p.index == t
        )
        assert original == point2.trade_candidate


# ============================================================
# H. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, 100.0)
        text = TradeCandidateFormatter().format(c)
        assert isinstance(text, str)

    def test_format_contains_required_sections(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, _EPOCH, 100.0)
        text = TradeCandidateFormatter().format(c)
        for section in [
            "Trade Candidate",
            "Index",
            "Timestamp",
            "Direction",
            "Status",
            "Setup",
            "Entry",
            "Stop",
            "Target",
            "Risk",
            "Reward",
            "Risk/Reward",
            "Confluence",
            "Supporting Evidence",
            "Conflicting Evidence",
            "Reason",
        ]:
            assert section in text

    def test_format_contains_warning(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, 100.0)
        text = TradeCandidateFormatter().format(c)
        assert "NOT predictive signals" in text
        assert "guarantees of profitability" in text

    def test_format_no_predictive_language(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, _EPOCH, 100.0)
        text = TradeCandidateFormatter().format(c)
        low = text.lower()
        assert "will be profitable" not in low
        assert "guaranteed" not in low
        assert "recommendation" not in low

    def test_format_unavailable_shown(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, None)
        text = TradeCandidateFormatter().format(c)
        assert "unavailable" in text

    def test_format_sequence(self):
        eng = TradeCandidateEngine()
        c1 = eng.generate(None, None, 0, None, 100.0)
        c2 = eng.generate(None, None, 1, None, 101.0)
        text = TradeCandidateFormatter().format_sequence([c1, c2])
        assert text.count("Trade Candidate") == 2
        # Warning emitted once for the sequence.
        assert text.count("NOT predictive signals") == 1

    def test_format_determinism(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, 100.0)
        f = TradeCandidateFormatter()
        assert f.format(c) == f.format(c)


# ============================================================
# I. DETERMINISM + IMmutability
# ============================================================


class TestDeterminismImmutability:
    def test_same_input_same_output(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c1 = eng.generate(a, ctx, 0, None, 100.0)
        c2 = eng.generate(a, ctx, 0, None, 100.0)
        assert c1 == c2

    def test_candidate_frozen(self):
        c = TradeCandidateEngine().generate(None, None, 0, None, 100.0)
        with pytest.raises(Exception):
            c.status = CandidateStatus.CANDIDATE  # type: ignore

    def test_repeated_pipeline_runs_deterministic(self):
        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        r1 = HistoricalEvaluationPipeline(cfg).evaluate(candles)
        r2 = HistoricalEvaluationPipeline(cfg).evaluate(candles)
        tcs1 = [p.trade_candidate for p in r1.evaluation_points_sequence]
        tcs2 = [p.trade_candidate for p in r2.evaluation_points_sequence]
        assert tcs1 == tcs2

    def test_supporting_evidence_is_subset_of_assessment(self):
        eng = TradeCandidateEngine()
        a = _make_assessment(
            direction=SetupDirection.BULLISH,
            classification=SetupClassification.POTENTIAL_SETUP,
            confluence_score=3,
        )
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=95.0,
            resistance=110.0,
        )
        c = eng.generate(a, ctx, 0, None, 100.0)
        assert c.confluence_score == a.confluence_score
        # The candidate reuses the assessment's evidence verbatim.
        assert c.supporting_evidence == tuple(a.evidence.supporting)
        assert c.conflicting_evidence == tuple(a.evidence.conflicting)
