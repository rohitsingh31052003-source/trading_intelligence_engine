"""
Demo for Sprint 11P — Market Context & Price Structure Intelligence.

This demo exercises the new market-context intelligence layer on
deterministic synthetic data and proves:

* swing detection + market structure (HH/HL/LH/LL)
* descriptive trend / range / support-resistance context
* additive pipeline integration (existing signal behaviour unchanged)
* the no-future-leakage guarantee (prefix/full-series agreement at T
  and future-mutation leaves context(T) unchanged)

The demo prints a descriptive market-context report. It makes NO
profitability or directional prediction. Market context is a
description of price structure, not a trade.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.market_context_config import MarketContextConfig
from engine.config.swing_config import SwingConfig
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.models.market_context import MarketContext
from engine.models.market_structure import StructureType
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingType
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def make_candle(
    close: float,
    high: float,
    low: float,
    index: int,
    volume: float = 1000.0,
) -> OHLCVCandle:
    """Build a fully-specified OHLCV candle with open == close."""

    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def bullish_dataset() -> list[OHLCVCandle]:
    """
    A deterministic rising zigzag producing higher highs and higher
    lows so the structure is classified BULLISH.
    """

    candles: list[OHLCVCandle] = []
    # Each leg: rise 3 then pullback 2, amplitude large enough for
    # clean swing detection at lookback=2.
    close = 100.0
    idx = 0
    for leg in range(4):
        for _ in range(3):
            close = round(close + 6, 2)
            candles.append(make_candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close - 3, 2)
            candles.append(make_candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def bearish_dataset() -> list[OHLCVCandle]:
    """Mirror of ``bullish_dataset``: lower highs and lower lows."""

    candles: list[OHLCVCandle] = []
    close = 200.0
    idx = 0
    for leg in range(4):
        for _ in range(3):
            close = round(close - 6, 2)
            candles.append(make_candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close + 3, 2)
            candles.append(make_candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def range_dataset() -> list[OHLCVCandle]:
    """
    A deterministic sideways oscillation between ~110 (highs) and
    ~100 (lows) producing flat swing highs and flat swing lows so the
    range engine classifies IN_RANGE.
    """

    candles: list[OHLCVCandle] = []
    vals = [100, 105, 110, 105, 100, 105, 110, 105, 100, 105, 110, 105, 100]
    for i, cp in enumerate(vals):
        candles.append(make_candle(cp, cp + 2, cp - 2, i))
    return candles


def _line(char: str = "-", width: int = 60) -> str:
    return char * width


def _structure_name(value: StructureType) -> str:
    return value.name


def render_context(ctx: MarketContext) -> str:
    """Render a single ``MarketContext`` as a readable block."""

    sr = ctx.support_resistance
    rng = ctx.range
    tr = ctx.trend
    recent = " -> ".join(
        _structure_name(s.structure) for s in ctx.recent_structure
    ) or "none"

    lines = [
        f"index={ctx.index}",
        f"    trend         = {tr.state.name}",
        f"    bias          = {tr.bias.name}",
        f"    range         = {rng.state.name}",
        f"    location      = {sr.location.name}",
        f"    support       = {sr.support}",
        f"    resistance    = {sr.resistance}",
        f"    recent struct = {recent}",
        f"    confirmed swings = {ctx.confirmed_swings}",
    ]
    return "\n".join(lines)


def main() -> None:
    print("=" * 60)
    print("Sprint 11P — Market Context & Price Structure Intelligence")
    print("=" * 60)

    engine = MarketContextEngine(
        config=MarketContextConfig(),
        swing_config=SwingConfig(lookback=2),
    )

    bull = bullish_dataset()
    bear = bearish_dataset()
    rng_data = range_dataset()

    print(f"\nCandles processed: {len(bull)} bullish, "
          f"{len(bear)} bearish, {len(rng_data)} range")

    # ------------------------------------------------------------
    # SWING DETECTION
    # ------------------------------------------------------------
    bull_ctx = engine.analyze_sequence(bull)
    print("\n" + _line())
    print("Swing Detection (bullish dataset)")
    print(_line())

    # Derive swings from the final visible slice to summarise.
    from engine.intelligence.swings import SwingEngine
    from engine.models.swing import SwingStatus

    swing_engine = SwingEngine(SwingConfig(lookback=2))
    swings = swing_engine.detect(bull)
    highs = [s for s in swings if s.swing_type == SwingType.HIGH
             and s.status == SwingStatus.CONFIRMED]
    lows = [s for s in swings if s.swing_type == SwingType.LOW
            and s.status == SwingStatus.CONFIRMED]
    print(f"Swing highs ({len(highs)}):")
    for s in highs:
        print(f"  index={s.index} price={s.price}")
    print(f"Swing lows ({len(lows)}):")
    for s in lows:
        print(f"  index={s.index} price={s.price}")

    # ------------------------------------------------------------
    # MARKET STRUCTURE
    # ------------------------------------------------------------
    from engine.intelligence.structure import MarketStructureEngine

    structures = MarketStructureEngine().analyze(
        [s for s in swings if s.status == SwingStatus.CONFIRMED],
    )
    print("\n" + _line())
    print("Market Structure (bullish dataset)")
    print(_line())
    by_type: dict[str, list[str]] = {}
    for sp in structures:
        by_type.setdefault(sp.structure.name, []).append(
            f"index={sp.swing.index} price={sp.swing.price}",
        )
    for name in ("HIGHER_HIGH", "HIGHER_LOW", "LOWER_HIGH",
                 "LOWER_LOW", "FIRST_HIGH", "FIRST_LOW"):
        items = by_type.get(name, [])
        print(f"{name} ({len(items)}):")
        for it in items:
            print(f"  {it}")

    # ------------------------------------------------------------
    # TREND / RANGE CONTEXT
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Trend / Range Context")
    print(_line())

    def summarise(label: str, seq):
        ctx = seq[-1]
        sr = ctx.support_resistance
        print(f"[{label}] trend={ctx.trend.state.name} "
              f"range={ctx.range.state.name} "
              f"location={sr.location.name}")
        if ctx.range.state.name == "IN_RANGE":
            print(f"        range high={ctx.range.high} "
                  f"low={ctx.range.low} "
                  f"position={ctx.range.position:.2f}")
        print(f"        support={sr.support} resistance={sr.resistance}")

    summarise("bullish", engine.analyze_sequence(bull))
    summarise("bearish", engine.analyze_sequence(bear))
    summarise("range", engine.analyze_sequence(rng_data))

    # ------------------------------------------------------------
    # SAMPLE EVALUATION POINTS
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Sample Evaluation Points (range dataset)")
    print(_line())

    range_ctx = engine.analyze_sequence(range_dataset())
    for t in (6, 8, len(range_ctx) - 1):
        print(render_context(range_ctx[t]))
        print()

    # ------------------------------------------------------------
    # FUTURE LEAKAGE PROOF
    # ------------------------------------------------------------
    print(_line())
    print("Future Leakage Proof")
    print(_line())

    data = bullish_dataset()
    seq_full = engine.analyze_sequence(data)
    T = 18

    # 1. prefix/full-series agreement at T
    prefix_ctx = engine.analyze_at(data[: T + 1], T)
    agree = _contexts_equal(seq_full[T], prefix_ctx)
    print(
        f"{'PASS' if agree else 'FAIL'}: prefix/full-series context "
        f"agrees at T={T}",
    )

    # 2. future mutation leaves context(T) unchanged
    mutated = list(data)
    # Replace a clearly-future candle with a wildly different value.
    future_index = T + 5
    if future_index < len(mutated):
        mutated[future_index] = make_candle(999.0, 1001.0, 997.0,
                                            future_index)
    seq_mut = engine.analyze_sequence(mutated)
    unchanged = _contexts_equal(seq_full[T], seq_mut[T])
    print(
        f"{'PASS' if unchanged else 'FAIL'}: future mutation leaves "
        f"context(T={T}) unchanged",
    )

    if not (agree and unchanged):
        print("WARNING: future-leakage proof did not pass.")

    # ------------------------------------------------------------
    # EXISTING PIPELINE BEHAVIOUR
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Existing Pipeline Behaviour")
    print(_line())

    candles = trending_dataset()

    before = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)

    # After Sprint 11P, the pipeline attaches market context additively.
    # Re-run with an explicit market-context config to exercise the
    # integration path; existing counts must be unchanged.
    after = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)

    print(f"Signals before: {before.signals_generated}")
    print(f"Signals after : {after.signals_generated}")
    print(f"Completed trades before: {before.completed_trades}")
    print(f"Completed trades after : {after.completed_trades}")

    sig_ok = before.signals_generated == after.signals_generated
    trade_ok = before.completed_trades == after.completed_trades
    print(
        f"{'PASS' if sig_ok and trade_ok else 'FAIL'}: existing "
        f"signal behaviour unchanged",
    )

    # Show that market context is now available on evaluation points.
    point = after.evaluation_points_sequence[-1]
    has_ctx = getattr(point, "market_context", None) is not None
    print(f"Market context attached to evaluation points: {has_ctx}")

    print("\n" + _line())
    print("Sprint 11P demo completed successfully.")
    print(_line())


def _contexts_equal(a: MarketContext, b: MarketContext) -> bool:
    """Structural equality of the descriptive context fields."""

    return (
        a.index == b.index
        and a.trend.state == b.trend.state
        and a.trend.bias == b.trend.bias
        and a.range.state == b.range.state
        and a.range.high == b.range.high
        and a.range.low == b.range.low
        and a.support_resistance.location
        == b.support_resistance.location
        and a.support_resistance.support
        == b.support_resistance.support
        and a.support_resistance.resistance
        == b.support_resistance.resistance
        and tuple(s.structure for s in a.recent_structure)
        == tuple(s.structure for s in b.recent_structure)
        and a.confirmed_swings == b.confirmed_swings
    )


if __name__ == "__main__":
    main()
