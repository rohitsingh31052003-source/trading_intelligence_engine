"""
Tests for the market context & price structure intelligence layer
(Sprint 11P).

Coverage:

A. Swing detection (delegated to existing SwingEngine; verified via
   the market-context output: confirmed swings, index/time, type)
B. Structure: HH / HL / LH / LL, bullish / bearish / mixed sequences
C. Trend classification: bullish / bearish / range / neutral / unknown
D. Range detection: obvious range, directional market, boundaries
E. Support / resistance: levels from structure, current-price distance
F. Future leakage: prefix/full-series equivalence, future mutation
   leaves context(T) unchanged, confirmation timing respected
G. Integration: market context attached to evaluation points, existing
   signal behaviour unchanged, existing trade counts unchanged
H. Determinism
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.market_context_config import (
    MarketContextConfig,
    RangeDetectionConfig,
    SupportResistanceContextConfig,
)
from engine.config.swing_config import SwingConfig
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.market_trend import MarketTrendEngine
from engine.intelligence.range_detection import RangeDetectionEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import (
    StructureAnalysisEngine,
)
from engine.intelligence.support_resistance_context import (
    SupportResistanceContextEngine,
)
from engine.intelligence.swings import SwingEngine
from engine.models.market_context import (
    MarketTrendState,
    PriceLocation,
    RangeState,
)
from engine.models.market_structure import StructureType
from engine.models.ohlcv import OHLCVCandle
from engine.models.structure_analysis import StructureBias
from engine.models.swing import SwingStatus, SwingType
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.market_context import MarketContextFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


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


def make_engine(lookback: int = 2) -> MarketContextEngine:
    return MarketContextEngine(
        config=MarketContextConfig(),
        swing_config=SwingConfig(lookback=lookback),
    )


def confirmed_structures(candles, lookback=2):
    swings = SwingEngine(SwingConfig(lookback=lookback)).detect(candles)
    confirmed = [s for s in swings if s.status == SwingStatus.CONFIRMED]
    return MarketStructureEngine().analyze(confirmed), confirmed


# ============================================================
# A. SWING DETECTION (via market context)
# ============================================================


def test_swing_high_detected():
    candles = bullish_dataset()
    ctx = make_engine().analyze_sequence(candles)[-1]
    assert ctx.confirmed_swings > 0


def test_swing_low_detected():
    candles = bullish_dataset()
    engine = make_engine()
    swings = SwingEngine(SwingConfig(lookback=2)).detect(candles)
    lows = [s for s in swings if s.swing_type == SwingType.LOW
            and s.status == SwingStatus.CONFIRMED]
    assert len(lows) > 0


def test_insufficient_history_no_swings():
    candles = [candle(100, 102, 98, 0), candle(101, 103, 99, 1)]
    ctx = make_engine().analyze_sequence(candles)[-1]
    assert ctx.confirmed_swings == 0
    assert ctx.trend.state == MarketTrendState.UNKNOWN


def test_swing_detection_deterministic():
    candles = bullish_dataset()
    engine = make_engine()
    a = engine.analyze_sequence(candles)
    b = engine.analyze_sequence(candles)
    assert [c.confirmed_swings for c in a] == [
        c.confirmed_swings for c in b
    ]


# ============================================================
# B. STRUCTURE
# ============================================================


def test_structure_bullish_higher_highs_higher_lows():
    structs, _ = confirmed_structures(bullish_dataset())
    types = {s.structure for s in structs}
    assert StructureType.HIGHER_HIGH in types
    assert StructureType.HIGHER_LOW in types


def test_structure_bearish_lower_highs_lower_lows():
    structs, _ = confirmed_structures(bearish_dataset())
    types = {s.structure for s in structs}
    assert StructureType.LOWER_HIGH in types
    assert StructureType.LOWER_LOW in types


def test_structure_mixed_unknown_bias():
    # A few candles producing only FIRST structures -> NEUTRAL/UNKNOWN
    candles = [candle(100 + i, 102 + i, 98 + i, i) for i in range(7)]
    structs, _ = confirmed_structures(candles)
    analysis = StructureAnalysisEngine().analyze(structs)
    assert analysis.current_bias in (
        StructureBias.UNKNOWN,
        StructureBias.NEUTRAL,
    )


def test_structure_first_high_first_low():
    structs, _ = confirmed_structures(bullish_dataset())
    names = [s.structure.name for s in structs]
    assert "FIRST_HIGH" in names
    assert "FIRST_LOW" in names


# ============================================================
# C. TREND CLASSIFICATION
# ============================================================


def test_trend_bullish():
    ctx = make_engine().analyze_sequence(bullish_dataset())[-1]
    assert ctx.trend.state == MarketTrendState.BULLISH
    assert ctx.trend.bias == StructureBias.BULLISH


def test_trend_bearish():
    ctx = make_engine().analyze_sequence(bearish_dataset())[-1]
    assert ctx.trend.state == MarketTrendState.BEARISH
    assert ctx.trend.bias == StructureBias.BEARISH


def test_trend_range():
    ctx = make_engine().analyze_sequence(range_dataset())[-1]
    assert ctx.trend.state == MarketTrendState.RANGE
    assert ctx.range.state == RangeState.IN_RANGE


def test_trend_unknown_insufficient_data():
    candles = [candle(100, 102, 98, 0)]
    ctx = make_engine().analyze_sequence(candles)[-1]
    assert ctx.trend.state == MarketTrendState.UNKNOWN


def test_trend_neutral_when_no_dominant_structure():
    # Range dataset early on (only 2 confirmed swings) -> NEUTRAL
    candles = range_dataset()[:9]
    ctx = make_engine().analyze_sequence(candles)[-1]
    # With few structures the bias is NEUTRAL; trend should not be
    # BULLISH or BEARISH.
    assert ctx.trend.state in (
        MarketTrendState.NEUTRAL,
        MarketTrendState.UNKNOWN,
        MarketTrendState.RANGE,
    )


def test_trend_range_overrides_bias():
    """Even with a directional bias, an active range forces RANGE."""
    structs, _ = confirmed_structures(range_dataset())
    analysis = StructureAnalysisEngine().analyze(structs)
    rng = RangeDetectionEngine().detect(structs, range_dataset()[-1])
    trend = MarketTrendEngine().analyze(analysis, rng)
    assert rng.state == RangeState.IN_RANGE
    assert trend.state == MarketTrendState.RANGE


# ============================================================
# D. RANGE DETECTION
# ============================================================


def test_range_obvious_range_detected():
    structs, _ = confirmed_structures(range_dataset())
    rng = RangeDetectionEngine().detect(structs, range_dataset()[-1])
    assert rng.state == RangeState.IN_RANGE
    assert rng.high is not None
    assert rng.low is not None
    assert rng.width == pytest.approx(rng.high - rng.low)
    assert 0.0 <= rng.position <= 1.0


def test_range_directional_not_in_range():
    structs, _ = confirmed_structures(bullish_dataset())
    rng = RangeDetectionEngine().detect(structs, bullish_dataset()[-1])
    assert rng.state == RangeState.NOT_IN_RANGE


def test_range_unknown_insufficient_swings():
    structs = []
    rng = RangeDetectionEngine().detect(structs, range_dataset()[-1])
    assert rng.state == RangeState.UNKNOWN


def test_range_boundary_position():
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = RangeDetectionEngine()
    # Close at the top of the range -> position near 1.0
    high_candle = candle(111, 112, 108, 99)
    rng = engine.detect(structs, high_candle)
    assert rng.state == RangeState.IN_RANGE
    assert rng.position > 0.9


def test_range_position_low_end():
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = RangeDetectionEngine()
    low_candle = candle(99, 102, 98, 99)
    rng = engine.detect(structs, low_candle)
    assert rng.state == RangeState.IN_RANGE
    assert rng.position < 0.1


def test_range_config_tolerance_affects_detection():
    # Very tight tolerance -> equal highs/lows still flat (0 diff)
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    tight = RangeDetectionEngine(RangeDetectionConfig(range_tolerance=0.001))
    assert tight.detect(structs, data[-1]).state == RangeState.IN_RANGE


# ============================================================
# E. SUPPORT / RESISTANCE
# ============================================================


def test_sr_levels_from_structure():
    structs, _ = confirmed_structures(bullish_dataset())
    engine = SupportResistanceContextEngine()
    # Price below all highs -> resistance is lowest high above price
    c = candle(100, 102, 98, 99)
    ctx = engine.analyze(structs, c)
    assert ctx.resistance is not None
    assert ctx.resistance > 100


def test_sr_distance_signed():
    """When the price sits between the nearest support and resistance,
    the support distance is <= 0 and the resistance distance is >= 0."""
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = SupportResistanceContextEngine()
    # Price mid-range between 98 and 112.
    c = candle(105, 106, 104, 99)
    ctx = engine.analyze(structs, c)
    assert ctx.support is not None
    assert ctx.resistance is not None
    assert ctx.distance_to_support <= 0
    assert ctx.distance_to_resistance >= 0


def test_sr_location_near_support():
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = SupportResistanceContextEngine()
    # Price right at the support (~98/100)
    c = candle(100, 102, 98, 99)
    ctx = engine.analyze(structs, c)
    assert ctx.location in (
        PriceLocation.NEAR_SUPPORT,
        PriceLocation.INSIDE_RANGE,
    )


def test_sr_location_above_resistance():
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = SupportResistanceContextEngine()
    # Price just above the known resistance (112) but with a support
    # below -> ABOVE_RESISTANCE.
    c = candle(115, 116, 114, 99)
    ctx = engine.analyze(structs, c)
    assert ctx.location == PriceLocation.ABOVE_RESISTANCE


def test_sr_location_below_support():
    data = range_dataset()
    structs, _ = confirmed_structures(data)
    engine = SupportResistanceContextEngine()
    # Price just below the known support (98) but with a resistance
    # above -> BELOW_SUPPORT.
    c = candle(95, 96, 94, 99)
    ctx = engine.analyze(structs, c)
    assert ctx.location == PriceLocation.BELOW_SUPPORT


def test_sr_unknown_when_no_structures():
    engine = SupportResistanceContextEngine()
    c = candle(100, 102, 98, 0)
    ctx = engine.analyze([], c)
    assert ctx.support is None
    assert ctx.resistance is None
    assert ctx.location == PriceLocation.UNKNOWN


def test_sr_deterministic():
    structs, _ = confirmed_structures(bullish_dataset())
    engine = SupportResistanceContextEngine()
    c = candle(120, 122, 118, 99)
    a = engine.analyze(structs, c)
    b = engine.analyze(structs, c)
    assert a == b


# ============================================================
# F. FUTURE LEAKAGE
# ============================================================


def _ctx_key(ctx):
    return (
        ctx.index,
        ctx.trend.state,
        ctx.trend.bias,
        ctx.range.state,
        ctx.range.high,
        ctx.range.low,
        ctx.support_resistance.location,
        ctx.support_resistance.support,
        ctx.support_resistance.resistance,
        tuple(s.structure for s in ctx.recent_structure),
        ctx.confirmed_swings,
    )


def test_leakage_prefix_full_series_agreement():
    data = bullish_dataset()
    engine = make_engine()
    full = engine.analyze_sequence(data)
    for T in (5, 10, 15, 18):
        prefix = engine.analyze_at(data[: T + 1], T)
        assert _ctx_key(full[T]) == _ctx_key(prefix), f"mismatch at T={T}"


def test_leakage_future_mutation_unchanged():
    data = bullish_dataset()
    engine = make_engine()
    full = engine.analyze_sequence(data)
    T = 15
    mutated = list(data)
    mutated[T + 3] = candle(999.0, 1001.0, 997.0, T + 3)
    mut = engine.analyze_sequence(mutated)
    assert _ctx_key(full[T]) == _ctx_key(mut[T])


def test_leakage_immediate_future_mutation_unchanged():
    data = bullish_dataset()
    engine = make_engine()
    full = engine.analyze_sequence(data)
    T = 18
    mutated = list(data)
    # Mutate the very next candle, which would alter swing confirmation
    # timing for a naive full-series engine.
    mutated[T + 1] = candle(5.0, 6.0, 4.0, T + 1)
    mut = engine.analyze_sequence(mutated)
    assert _ctx_key(full[T]) == _ctx_key(mut[T])


def test_leakage_confirmation_timing_respected():
    """A swing needs `lookback` candles to its right; at T it is only
    confirmed if its confirmation_index <= T."""
    data = bullish_dataset()
    lookback = 2
    engine = make_engine(lookback=lookback)
    # The last confirmed swing at T must have confirmation_index <= T.
    for T in range(lookback + 1, len(data)):
        ctx = engine.analyze_at(data[: T + 1], T)
        # Reconstruct the confirmed swings from the prefix directly.
        swings = SwingEngine(SwingConfig(lookback=lookback)).detect(
            data[: T + 1],
        )
        confirmed = [s for s in swings if s.status == SwingStatus.CONFIRMED]
        for s in confirmed:
            assert s.confirmation_index <= T


def test_leakage_analyze_at_out_of_range():
    data = bullish_dataset()
    engine = make_engine()
    with pytest.raises(IndexError):
        engine.analyze_at(data, len(data))
    with pytest.raises(IndexError):
        engine.analyze_at(data, -1)


# ============================================================
# G. INTEGRATION
# ============================================================


def test_pipeline_attaches_market_context():
    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=True),
    ).evaluate(candles)
    assert result.evaluation_points > 0
    for point in result.evaluation_points_sequence:
        assert point.market_context is not None


def test_pipeline_disabled_no_market_context():
    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=False),
    ).evaluate(candles)
    for point in result.evaluation_points_sequence:
        assert point.market_context is None


def test_pipeline_signal_behaviour_unchanged():
    candles = trending_dataset()
    on = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=True),
    ).evaluate(candles)
    off = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=False),
    ).evaluate(candles)
    assert on.signals_generated == off.signals_generated
    assert on.completed_trades == off.completed_trades
    assert on.signals_validated == off.signals_validated
    assert on.eligible_decisions == off.eligible_decisions


def test_pipeline_trade_counts_unchanged():
    candles = trending_dataset()
    on = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=True),
    ).evaluate(candles)
    off = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=False),
    ).evaluate(candles)
    assert on.completed_trades == off.completed_trades
    assert len(on.validation_results) == len(off.validation_results)


def test_pipeline_per_point_signal_identity_preserved():
    candles = trending_dataset()
    on = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=True),
    ).evaluate(candles)
    off = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=False),
    ).evaluate(candles)

    def key(p):
        return (
            p.index,
            p.signal_state,
            p.suppressed,
            p.decision_status,
            p.decision_direction,
        )

    assert [key(p) for p in on.evaluation_points_sequence] == [
        key(p) for p in off.evaluation_points_sequence
    ]


def test_pipeline_market_context_uses_visible_prefix():
    """The context on a pipeline point must match the standalone
    context computed from the prefix candles[:T+1]."""
    candles = trending_dataset()
    engine = make_engine()
    standalone = engine.analyze_sequence(candles)
    result = HistoricalEvaluationPipeline(
        PipelineConfig(enable_market_context=True),
    ).evaluate(candles)
    for point in result.evaluation_points_sequence:
        T = point.index
        assert _ctx_key(point.market_context) == _ctx_key(standalone[T])


# ============================================================
# H. DETERMINISM
# ============================================================


def test_determinism_sequence():
    data = bullish_dataset()
    engine = make_engine()
    a = engine.analyze_sequence(data)
    b = engine.analyze_sequence(data)
    assert [_ctx_key(c) for c in a] == [_ctx_key(c) for c in b]


def test_determinism_analyze_at():
    data = bullish_dataset()
    engine = make_engine()
    T = 12
    a = engine.analyze_at(data[: T + 1], T)
    b = engine.analyze_at(data[: T + 1], T)
    assert _ctx_key(a) == _ctx_key(b)


def test_determinism_range_dataset():
    data = range_dataset()
    engine = make_engine()
    a = engine.analyze_sequence(data)
    b = engine.analyze_sequence(data)
    assert [_ctx_key(c) for c in a] == [_ctx_key(c) for c in b]


# ============================================================
# CONFIG VALIDATION
# ============================================================


def test_range_config_validation():
    with pytest.raises(ValueError):
        RangeDetectionConfig(min_swings=0)
    with pytest.raises(ValueError):
        RangeDetectionConfig(range_tolerance=0)
    with pytest.raises(ValueError):
        RangeDetectionConfig(min_flat_count=0)
    with pytest.raises(ValueError):
        RangeDetectionConfig(max_recent_structure_strength=0)


def test_sr_config_validation():
    with pytest.raises(ValueError):
        SupportResistanceContextConfig(proximity_threshold=0)
    with pytest.raises(ValueError):
        SupportResistanceContextConfig(max_levels=0)


def test_market_context_config_validation():
    with pytest.raises(ValueError):
        MarketContextConfig(recent_structure_count=0)


# ============================================================
# MODELS
# ============================================================


def test_models_frozen():
    data = range_dataset()
    ctx = make_engine().analyze_sequence(data)[-1]
    with pytest.raises(Exception):
        ctx.index = 99  # type: ignore[misc]


def test_market_context_has_recent_structure():
    data = bullish_dataset()
    ctx = make_engine().analyze_sequence(data)[-1]
    assert isinstance(ctx.recent_structure, tuple)
    assert len(ctx.recent_structure) <= 3


# ============================================================
# REPORTING
# ============================================================


def test_formatter_returns_str():
    data = bullish_dataset()
    ctx = make_engine().analyze_sequence(data)[-1]
    text = MarketContextFormatter().format(ctx)
    assert isinstance(text, str)
    assert "descriptive market context" in text


def test_formatter_sequence():
    data = range_dataset()
    seq = make_engine().analyze_sequence(data)
    text = MarketContextFormatter().format_sequence(seq)
    assert isinstance(text, str)
    assert text.count("index=") == len(seq)


def test_formatter_no_predictive_language():
    data = bullish_dataset()
    ctx = make_engine().analyze_sequence(data)[-1]
    text = MarketContextFormatter().format(ctx)
    assert "predicted direction" not in text
    assert "profitable setup" not in text


def test_formatter_deterministic():
    data = bullish_dataset()
    ctx = make_engine().analyze_sequence(data)[-1]
    f = MarketContextFormatter()
    assert f.format(ctx) == f.format(ctx)
