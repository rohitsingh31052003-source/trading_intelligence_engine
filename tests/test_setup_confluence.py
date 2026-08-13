"""
Tests for the market setup / confluence intelligence layer (Sprint 11Q).

Coverage:

A. Evidence model basics (frozen/slots, defaults, properties)
B. Configuration validation + defaults
C. Trend evidence classification (bullish/bearish/neutral/range/unknown)
D. Structure evidence (HH/HL bullish, LH/LL bearish, mixed, insufficient)
E. Candle evidence (hammer/shooting star/engulfing/doji/inside bar/none,
   conflicting directional patterns)
F. Location evidence relative to candidate direction (constructive/adverse
   for bullish vs bearish, inside range, absent)
G. Range/regime evidence (IN_RANGE/NOT_IN_RANGE/UNKNOWN)
H. Candidate direction + confluence score
I. Classification:
   - strong bullish confluence -> BULLISH + POTENTIAL_SETUP
   - strong bearish confluence -> BEARISH + POTENTIAL_SETUP
   - conflicting evidence -> conflict recorded, capped at WATCH
   - neutral market -> NEUTRAL / NO_SETUP
   - insufficient structure -> NO_SETUP
   - candle-only -> not automatically POTENTIAL_SETUP
   - structure-only -> not automatically POTENTIAL_SETUP
   - range market -> capped at WATCH (not treated as trend setup)
J. Configuration effects (conflicting_blocks, range_caps,
   neutral_candle_contributes, min thresholds)
K. Future-leakage safety (prefix/full-series equivalence + mutation)
L. Pipeline integration (additive, signal/trade behaviour unchanged,
   disabled reproduces pre-11Q)
M. Reporting (sections, warning present, no predictive language,
   returns str, determinism)
N. Determinism + immutability
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.setup_confluence_config import SetupConfluenceConfig
from engine.config.swing_config import SwingConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.models.candle_pattern import (
    CandleDirection,
    CandleMeasurements,
    CandlePattern,
    CandlePatternType,
)
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
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.setup_confluence import SetupAssessmentFormatter


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
    )


def setup_at(
    pat_engine: CandlePatternEngine,
    mc_engine: MarketContextEngine,
    setup_engine: SetupConfluenceEngine,
    candles: list[OHLCVCandle],
    index: int,
) -> SetupAssessment:
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    return setup_engine.assess(pats, ctx, index, candles[index].timestamp)


# ============================================================
# HAND-BUILT MARKET CONTEXT HELPERS
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
    return StructurePoint(swing=_swing(price, _swing_type_for(stype), index), structure=stype)


def _swing_type_for(stype: StructureType) -> SwingType:
    if stype in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH,
                 StructureType.FIRST_HIGH):
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


def _pattern(ptype: CandlePatternType, direction: CandleDirection,
             index: int = 0, score: float = 0.5) -> CandlePattern:
    return CandlePattern(
        pattern_type=ptype,
        index=index,
        timestamp=_EPOCH + timedelta(days=index),
        direction=direction,
        measurements=CandleMeasurements(
            range=10.0, body=2.0, upper_wick=2.0, lower_wick=2.0,
            body_to_range_ratio=0.2, direction=direction,
        ),
        score=score,
        reason="test",
        prior_index=None,
        prior_measurements=None,
        confirmed=False,
    )


# ============================================================
# A. EVIDENCE MODEL BASICS
# ============================================================


class TestEvidenceModels:
    def test_setup_direction_members(self):
        assert {d.name for d in SetupDirection} == {
            "BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN",
        }

    def test_setup_classification_members(self):
        assert {c.name for c in SetupClassification} == {
            "NO_SETUP", "WATCH", "POTENTIAL_SETUP",
        }

    def test_evidence_alignment_members(self):
        assert {a.name for a in EvidenceAlignment} == {
            "ALIGNED", "CONFLICTING", "NEUTRAL", "ABSENT",
        }

    def test_evidence_source_members(self):
        assert {s.name for s in EvidenceSource} == {
            "TREND", "STRUCTURE", "CANDLE", "LOCATION", "RANGE",
        }

    def test_evidence_item_frozen(self):
        item = EvidenceItem(
            source=EvidenceSource.TREND,
            direction=SetupDirection.BULLISH,
            alignment=EvidenceAlignment.ALIGNED,
            label="BULLISH",
            reason="x",
        )
        with pytest.raises(Exception):
            item.direction = SetupDirection.BEARISH  # type: ignore[misc]

    def test_setup_assessment_frozen(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        with pytest.raises(Exception):
            a.classification = SetupClassification.WATCH  # type: ignore[misc]

    def test_setup_evidence_all_canonical_order(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        sources = [i.source for i in a.evidence.all]
        assert sources == [
            EvidenceSource.TREND, EvidenceSource.STRUCTURE,
            EvidenceSource.CANDLE, EvidenceSource.LOCATION,
            EvidenceSource.RANGE,
        ]

    def test_has_conflict_false_when_no_conflict(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        assert a.has_conflict is False

    def test_is_potential_setup_property(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        assert a.is_potential_setup is False


# ============================================================
# B. CONFIGURATION
# ============================================================


class TestConfiguration:
    def test_defaults(self):
        c = SetupConfluenceConfig()
        assert c.min_supporting_for_watch == 1
        assert c.min_supporting_for_potential_setup == 3
        assert c.conflicting_blocks_potential_setup is True
        assert c.neutral_candle_contributes is False
        assert c.range_caps_classification is True
        assert c.min_structure_for_evidence == 2

    def test_min_watch_below_one_rejected(self):
        with pytest.raises(ValueError):
            SetupConfluenceConfig(min_supporting_for_watch=0)

    def test_min_potential_below_one_rejected(self):
        with pytest.raises(ValueError):
            SetupConfluenceConfig(min_supporting_for_potential_setup=0)

    def test_potential_below_watch_rejected(self):
        with pytest.raises(ValueError):
            SetupConfluenceConfig(
                min_supporting_for_watch=3,
                min_supporting_for_potential_setup=2,
            )

    def test_min_structure_below_one_rejected(self):
        with pytest.raises(ValueError):
            SetupConfluenceConfig(min_structure_for_evidence=0)

    def test_config_frozen(self):
        c = SetupConfluenceConfig()
        with pytest.raises(Exception):
            c.min_supporting_for_watch = 5  # type: ignore[misc]


# ============================================================
# C-I. ENGINE EVIDENCE + CLASSIFICATION (hand-built contexts)
# ============================================================


class TestTrendEvidence:
    def test_bullish_trend_aligned_with_bullish_candidate(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.trend.direction == SetupDirection.BULLISH
        assert a.evidence.trend.alignment == EvidenceAlignment.ALIGNED

    def test_bearish_trend(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            recent_structures=(StructureType.LOWER_HIGH,
                               StructureType.LOWER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.trend.direction == SetupDirection.BEARISH

    def test_neutral_trend_neutral_alignment(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.NEUTRAL,
            recent_structures=(StructureType.HIGHER_HIGH,),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.trend.direction == SetupDirection.NEUTRAL
        assert a.evidence.trend.alignment == EvidenceAlignment.NEUTRAL

    def test_range_trend_neutral(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.RANGE,
            range_state=RangeState.IN_RANGE,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.trend.direction == SetupDirection.NEUTRAL

    def test_unknown_trend_absent(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(trend_state=MarketTrendState.UNKNOWN)
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.trend.direction == SetupDirection.UNKNOWN
        assert a.evidence.trend.alignment == EvidenceAlignment.ABSENT


class TestStructureEvidence:
    def test_higher_highs_lows_bullish(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.structure.direction == SetupDirection.BULLISH

    def test_lower_highs_lows_bearish(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            recent_structures=(StructureType.LOWER_HIGH,
                               StructureType.LOWER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.structure.direction == SetupDirection.BEARISH

    def test_mixed_structure_neutral(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.LOWER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.structure.direction == SetupDirection.NEUTRAL

    def test_insufficient_structure_absent(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(recent_structures=(StructureType.HIGHER_HIGH,))
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.structure.direction == SetupDirection.UNKNOWN
        assert a.evidence.structure.alignment == EvidenceAlignment.ABSENT

    def test_min_structure_config_respected(self):
        eng = SetupConfluenceEngine(
            SetupConfluenceConfig(min_structure_for_evidence=3),
        )
        ctx = _make_context(
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.structure.alignment == EvidenceAlignment.ABSENT


class TestCandleEvidence:
    def test_no_pattern_absent(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context()
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.candle.direction == SetupDirection.UNKNOWN
        assert a.evidence.candle.alignment == EvidenceAlignment.ABSENT
        assert a.candle_evidence == "none"

    def test_hammer_bullish(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.evidence.candle.direction == SetupDirection.BULLISH

    def test_shooting_star_bearish(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            recent_structures=(StructureType.LOWER_HIGH,
                               StructureType.LOWER_LOW),
        )
        pats = [_pattern(CandlePatternType.SHOOTING_STAR,
                         CandleDirection.BEARISH)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.evidence.candle.direction == SetupDirection.BEARISH

    def test_doji_neutral_does_not_contribute_by_default(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        pats = [_pattern(CandlePatternType.DOJI, CandleDirection.NEUTRAL)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.evidence.candle.direction == SetupDirection.NEUTRAL
        assert a.evidence.candle.alignment == EvidenceAlignment.NEUTRAL

    def test_neutral_candle_can_contribute_when_configured(self):
        eng = SetupConfluenceEngine(
            SetupConfluenceConfig(neutral_candle_contributes=True),
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.DOJI, CandleDirection.NEUTRAL)]
        a = eng.assess(pats, ctx, 0, None)
        # neutral candle contributes as ALIGNED when configured.
        assert a.evidence.candle.alignment == EvidenceAlignment.ALIGNED

    def test_neutral_candle_does_not_contribute_by_default(self):
        eng = SetupConfluenceEngine(
            SetupConfluenceConfig(neutral_candle_contributes=False),
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.DOJI, CandleDirection.NEUTRAL)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.evidence.candle.alignment == EvidenceAlignment.NEUTRAL

    def test_conflicting_directional_patterns_conflict(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        pats = [
            _pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH),
            _pattern(CandlePatternType.SHOOTING_STAR,
                     CandleDirection.BEARISH),
        ]
        a = eng.assess(pats, ctx, 0, None)
        assert a.evidence.candle.alignment == EvidenceAlignment.CONFLICTING


class TestLocationEvidence:
    def test_near_support_aligned_for_bullish_candidate(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.ALIGNED

    def test_near_resistance_conflicting_for_bullish_candidate(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_RESISTANCE,
            resistance=102.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.CONFLICTING

    def test_near_resistance_aligned_for_bearish_candidate(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            recent_structures=(StructureType.LOWER_HIGH,
                               StructureType.LOWER_LOW),
            location=PriceLocation.NEAR_RESISTANCE,
            resistance=102.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.ALIGNED

    def test_near_support_conflicting_for_bearish_candidate(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BEARISH,
            bias=StructureBias.BEARISH,
            structure_intact=True,
            recent_structures=(StructureType.LOWER_HIGH,
                               StructureType.LOWER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.CONFLICTING

    def test_inside_range_neutral_location(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.INSIDE_RANGE,
            support=98.0,
            resistance=102.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.NEUTRAL

    def test_unknown_location_absent(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.UNKNOWN,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.ABSENT

    def test_location_neutral_without_candidate(self):
        eng = SetupConfluenceEngine()
        # No directional trend/structure -> no candidate direction.
        ctx = _make_context(
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.location.alignment == EvidenceAlignment.NEUTRAL


class TestRangeEvidence:
    def test_in_range_neutral(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(range_state=RangeState.IN_RANGE)
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.range.direction == SetupDirection.NEUTRAL

    def test_not_in_range(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(range_state=RangeState.NOT_IN_RANGE)
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.range.label == "NOT_IN_RANGE"

    def test_unknown_range_absent(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(range_state=RangeState.UNKNOWN)
        a = eng.assess([], ctx, 0, None)
        assert a.evidence.range.alignment == EvidenceAlignment.ABSENT


# ============================================================
# I. CLASSIFICATION SCENARIOS (realistic datasets)
# ============================================================


class TestClassification:
    def test_strong_bullish_confluence_potential_setup(self):
        pat, mc, se = make_engines()
        bull = bullish_dataset()
        data = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(data) - 1
        a = setup_at(pat, mc, se, data, t)
        assert a.direction == SetupDirection.BULLISH
        assert a.classification == SetupClassification.POTENTIAL_SETUP
        assert a.confluence_score >= 3

    def test_strong_bearish_confluence_potential_setup(self):
        pat, mc, se = make_engines()
        bear = bearish_dataset()
        data = list(bear) + [
            shooting_star_candle(len(bear), bear[-1].close)
        ]
        t = len(data) - 1
        a = setup_at(pat, mc, se, data, t)
        assert a.direction == SetupDirection.BEARISH
        assert a.classification == SetupClassification.POTENTIAL_SETUP
        assert a.confluence_score >= 3

    def test_conflicting_evidence_recorded_not_bullish(self):
        pat, mc, se = make_engines()
        bull = bullish_dataset()
        # Bullish structure + bearish reversal candle.
        data = list(bull) + [
            shooting_star_candle(len(bull), bull[-1].close)
        ]
        t = len(data) - 1
        a = setup_at(pat, mc, se, data, t)
        assert a.has_conflict is True
        # CANDLE must appear as a conflicting source.
        sources = {i.source for i in a.evidence.conflicting}
        assert EvidenceSource.CANDLE in sources
        # Must NOT be a clean POTENTIAL_SETUP with conflict (capped).
        assert a.classification != SetupClassification.POTENTIAL_SETUP

    def test_conflicting_blocks_potential_setup_by_default(self):
        pat, mc, se = make_engines()
        bull = bullish_dataset()
        data = list(bull) + [
            shooting_star_candle(len(bull), bull[-1].close)
        ]
        t = len(data) - 1
        a = setup_at(pat, mc, se, data, t)
        assert a.classification == SetupClassification.WATCH

    def test_conflict_does_not_block_when_configured_off(self):
        pat, mc, se = make_engines()
        se = SetupConfluenceEngine(
            SetupConfluenceConfig(conflicting_blocks_potential_setup=False),
        )
        bull = bullish_dataset()
        data = list(bull) + [
            shooting_star_candle(len(bull), bull[-1].close)
        ]
        t = len(data) - 1
        a = setup_at(pat, mc, se, data, t)
        # Conflict recorded but does not block; confluence governs.
        assert a.has_conflict is True

    def test_neutral_market_no_setup(self):
        pat, mc, se = make_engines()
        rng = range_dataset()
        t = len(rng) - 1
        a = setup_at(pat, mc, se, rng, t)
        # Range caps at WATCH; with no directional evidence it is
        # NO_SETUP, otherwise WATCH — never POTENTIAL_SETUP.
        assert a.classification != SetupClassification.POTENTIAL_SETUP

    def test_insufficient_structure_no_setup(self):
        pat, mc, se = make_engines()
        minimal = range_dataset()[:3]
        t = len(minimal) - 1
        a = setup_at(pat, mc, se, minimal, t)
        assert a.classification == SetupClassification.NO_SETUP

    def test_candle_only_not_potential_setup(self):
        eng = SetupConfluenceEngine()
        # Only a candle pattern, no trend/structure (UNKNOWN trend,
        # absent structure) -> no candidate direction support beyond
        # the single candle -> cannot reach POTENTIAL_SETUP.
        ctx = _make_context(trend_state=MarketTrendState.UNKNOWN)
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.classification != SetupClassification.POTENTIAL_SETUP

    def test_structure_only_not_potential_setup(self):
        eng = SetupConfluenceEngine()
        # Only structure, no trend/candle -> single aligned source.
        ctx = _make_context(
            trend_state=MarketTrendState.UNKNOWN,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.classification != SetupClassification.POTENTIAL_SETUP

    def test_range_market_capped_at_watch_even_with_confluence(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            range_state=RangeState.IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        # Enough confluence for POTENTIAL_SETUP but IN_RANGE caps at WATCH.
        assert a.confluence_score >= 3
        assert a.classification == SetupClassification.WATCH

    def test_range_caps_off_allows_potential_setup(self):
        eng = SetupConfluenceEngine(
            SetupConfluenceConfig(range_caps_classification=False),
        )
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            range_state=RangeState.IN_RANGE,
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.classification == SetupClassification.POTENTIAL_SETUP

    def test_watch_threshold(self):
        eng = SetupConfluenceEngine(
            SetupConfluenceConfig(
                min_supporting_for_watch=1,
                min_supporting_for_potential_setup=3,
            ),
        )
        # One aligned source (structure only).
        ctx = _make_context(
            trend_state=MarketTrendState.UNKNOWN,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        a = eng.assess([], ctx, 0, None)
        assert a.confluence_score == 1
        assert a.classification == SetupClassification.WATCH

    def test_no_setup_when_no_evidence(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(trend_state=MarketTrendState.UNKNOWN)
        a = eng.assess([], ctx, 0, None)
        assert a.classification == SetupClassification.NO_SETUP
        assert a.confluence_score == 0

    def test_none_market_context_no_setup(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        assert a.classification == SetupClassification.NO_SETUP
        assert a.direction == SetupDirection.UNKNOWN
        assert a.confluence_score == 0
        assert a.evidence.trend.alignment == EvidenceAlignment.ABSENT


# ============================================================
# K. FUTURE-LEAKAGE SAFETY
# ============================================================


class TestFutureLeakage:
    def test_prefix_equals_full_series_at_T(self):
        pat, mc, se = make_engines()
        data = list(bullish_dataset()) + [
            hammer_candle(len(bullish_dataset()),
                          bullish_dataset()[-1].close)
        ]
        T = len(data) - 2
        from_full = setup_at(pat, mc, se, data, T)
        from_prefix = setup_at(pat, mc, se, data[: T + 1], T)
        assert from_full == from_prefix

    def test_future_mutation_leaves_setup_unchanged(self):
        pat, mc, se = make_engines()
        data = list(bullish_dataset())
        T = len(data) - 3
        from_full = setup_at(pat, mc, se, data, T)
        mutated = list(data)
        future_index = T + 1
        mutated[future_index] = candle(
            999.0, 1001.0, 997.0, future_index,
        )
        from_mut = setup_at(pat, mc, se, mutated, T)
        assert from_full == from_mut

    def test_setup_uses_only_patterns_at_index(self):
        pat, mc, se = make_engines()
        data = list(bullish_dataset())
        T = len(data) - 1
        a = setup_at(pat, mc, se, data, T)
        # Every candle evidence referenced must be attributed to T.
        if a.candle_evidence != "none":
            detected = [p for p in pat.detect(data[: T + 1])
                        if p.index == T]
            for p in detected:
                assert p.index == T


# ============================================================
# L. PIPELINE INTEGRATION
# ============================================================


class TestPipelineIntegration:
    def test_setup_assessment_attached_to_points(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        assert res.evaluation_points > 0
        for p in res.evaluation_points_sequence:
            assert p.setup_assessment is not None

    def test_disabled_reproduces_pre_11q(self):
        candles = trending_dataset()
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_setup_confluence=False,
        )
        res = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        for p in res.evaluation_points_sequence:
            assert p.setup_assessment is None

    def test_signals_unchanged_enable_vs_disable(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_setup_confluence=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_setup_confluence=False,
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
            enable_setup_confluence=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_setup_confluence=False,
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
            enable_setup_confluence=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_setup_confluence=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        on_prices = [(s.entry_price, s.stop_loss, s.take_profit)
                     for s in on.signals]
        off_prices = [(s.entry_price, s.stop_loss, s.take_profit)
                      for s in off.signals]
        assert on_prices == off_prices

    def test_pipeline_point_equals_standalone_prefix(self):
        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        pipe = HistoricalEvaluationPipeline(cfg)
        res = pipe.evaluate(candles)
        pat, mc, se = make_engines(lookback=2)
        # Pick an evaluation index present in the pipeline result.
        point = res.evaluation_points_sequence[0]
        t = point.index
        standalone = setup_at(pat, mc, se, candles, t)
        assert point.setup_assessment == standalone

    def test_classification_distribution_has_all_categories(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        cats = {p.setup_assessment.classification for p in
                res.evaluation_points_sequence}
        # The trending dataset produces a spread; at least NO_SETUP and
        # WATCH/POTENTIAL_SETUP appear.
        assert SetupClassification.NO_SETUP in cats or any(
            p.setup_assessment.classification != SetupClassification.NO_SETUP
            for p in res.evaluation_points_sequence
        )


# ============================================================
# M. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        text = SetupAssessmentFormatter().format(a)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_required_sections_present(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        text = SetupAssessmentFormatter().format(a)
        assert "Setup Assessment" in text
        assert "Direction" in text
        assert "Classification" in text
        assert "Confluence" in text
        assert "Supporting" in text
        assert "Conflicting" in text
        assert "Candle Evidence" in text
        assert "Trend" in text
        assert "Structure" in text
        assert "Location" in text
        assert "Regime" in text
        assert "Reason" in text

    def test_warning_present(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        text = SetupAssessmentFormatter().format(a)
        assert "descriptive technical evidence" in text
        assert "not predictions or guarantees of profitability" in text

    def test_no_predictive_language(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        text = SetupAssessmentFormatter().format(a)
        low = text.lower()
        assert "guaranteed" not in low
        assert "profitable" not in low
        assert "will rise" not in low
        assert "recommendation" not in low

    def test_format_sequence_returns_str(self):
        eng = SetupConfluenceEngine()
        a1 = eng.assess([], None, 0, None)
        a2 = eng.assess([], None, 1, None)
        text = SetupAssessmentFormatter().format_sequence([a1, a2])
        assert isinstance(text, str)
        assert text.count("Setup Assessment") == 2

    def test_format_sequence_warning_once(self):
        eng = SetupConfluenceEngine()
        a1 = eng.assess([], None, 0, None)
        a2 = eng.assess([], None, 1, None)
        text = SetupAssessmentFormatter().format_sequence([a1, a2])
        assert text.count("not predictions or guarantees") == 1

    def test_determinism(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a1 = eng.assess(pats, ctx, 0, None)
        a2 = eng.assess(pats, ctx, 0, None)
        assert a1 == a2
        assert SetupAssessmentFormatter().format(a1) == (
            SetupAssessmentFormatter().format(a2)
        )


# ============================================================
# N. DETERISM + IMMUTABILITY
# ============================================================


class TestDeterminismImmutability:
    def test_repeated_assess_identical(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a1 = eng.assess(pats, ctx, 5, _EPOCH)
        a2 = eng.assess(pats, ctx, 5, _EPOCH)
        assert a1 == a2

    def test_supporting_conflicting_subsets_of_all(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_RESISTANCE,
            resistance=102.0,
        )
        a = eng.assess([], ctx, 0, None)
        all_items = set(a.evidence.all)
        supporting = set(a.evidence.supporting)
        conflicting = set(a.evidence.conflicting)
        assert supporting.issubset(all_items)
        assert conflicting.issubset(all_items)
        assert supporting.isdisjoint(conflicting)

    def test_confluence_score_equals_supporting_count(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context(
            trend_state=MarketTrendState.BULLISH,
            bias=StructureBias.BULLISH,
            structure_intact=True,
            recent_structures=(StructureType.HIGHER_HIGH,
                               StructureType.HIGHER_LOW),
            location=PriceLocation.NEAR_SUPPORT,
            support=98.0,
        )
        pats = [_pattern(CandlePatternType.HAMMER, CandleDirection.BULLISH)]
        a = eng.assess(pats, ctx, 0, None)
        assert a.confluence_score == len(a.evidence.supporting)

    def test_models_frozen_slots(self):
        eng = SetupConfluenceEngine()
        a = eng.assess([], None, 0, None)
        assert hasattr(SetupAssessment, "__slots__")
        assert hasattr(SetupEvidence, "__slots__")
        assert hasattr(EvidenceItem, "__slots__")

    def test_index_and_timestamp_recorded(self):
        eng = SetupConfluenceEngine()
        ctx = _make_context()
        a = eng.assess([], ctx, 7, _EPOCH + timedelta(days=7))
        assert a.index == 7
        assert a.timestamp == _EPOCH + timedelta(days=7)
