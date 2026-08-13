"""
Demo for Sprint 11Q — Market Setup / Confluence Intelligence.

This demo exercises the new setup/confluence intelligence layer on
deterministic synthetic data and proves:

* strong bullish confluence  -> BULLISH + POTENTIAL_SETUP
* strong bearish confluence  -> BEARISH + POTENTIAL_SETUP
* conflicting evidence       -> conflict recorded (not silently bullish)
* neutral / range market     -> NEUTRAL / NO_SETUP or capped at WATCH
* the no-future-leakage guarantee (prefix/full-series agreement at T
  and future-mutation leaves setup(T) unchanged)
* additive pipeline integration (existing signal / trade behaviour
  unchanged)

The demo prints descriptive setup assessment reports. It makes NO
profitability or directional prediction. A setup assessment is a
description of combined technical evidence, not a trade.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.setup_confluence_config import SetupConfluenceConfig
from engine.config.swing_config import SwingConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.setup_confluence import SetupAssessmentFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def make_candle(
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
    """Rising zigzag -> higher highs / higher lows (BULLISH)."""

    candles: list[OHLCVCandle] = []
    close = 100.0
    idx = 0
    for _ in range(4):
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
    """Falling zigzag -> lower highs / lower lows (BEARISH)."""

    candles: list[OHLCVCandle] = []
    close = 200.0
    idx = 0
    for _ in range(4):
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
    """Sideways oscillation -> IN_RANGE."""

    vals = [100, 105, 110, 105, 100, 105, 110, 105, 100, 105, 110, 105, 100]
    return [make_candle(cp, cp + 2, cp - 2, i) for i, cp in enumerate(vals)]


def hammer_candle(
    index: int,
    base_close: float,
    body: float = 2.0,
) -> OHLCVCandle:
    """A bullish hammer shape continuing from ``base_close``.

    Long lower wick, small upper wick, small bullish body. The close
    stays near ``base_close`` so the candle injects bullish candle
    evidence without disrupting the surrounding price structure.
    """

    close = base_close + body
    low = close - (body * 3.0)
    high = close + body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def shooting_star_candle(
    index: int,
    base_close: float,
    body: float = 2.0,
) -> OHLCVCandle:
    """A bearish shooting-star shape continuing from ``base_close``.

    Long upper wick, small lower wick, small bearish body. The close
    stays near ``base_close`` so the candle injects bearish candle
    evidence without disrupting the surrounding price structure.
    """

    close = base_close - body
    high = close + (body * 3.0)
    low = close - body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _line(char: str = "-", width: int = 60) -> str:
    return char * width


def build_engines(lookback: int = 2):
    swing_cfg = SwingConfig(lookback=lookback)
    return (
        CandlePatternEngine(),
        MarketContextEngine(swing_config=swing_cfg),
        SetupConfluenceEngine(SetupConfluenceConfig()),
    )


def setup_at(
    pat_engine: CandlePatternEngine,
    mc_engine: MarketContextEngine,
    setup_engine: SetupConfluenceEngine,
    candles: list[OHLCVCandle],
    index: int,
):
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    return setup_engine.assess(pats, ctx, index, candles[index].timestamp)


def main() -> None:
    print("=" * 60)
    print("Sprint 11Q — Market Setup / Confluence Intelligence")
    print("=" * 60)

    pat_engine, mc_engine, setup_engine = build_engines()
    formatter = SetupAssessmentFormatter()

    # ------------------------------------------------------------
    # 1. STRONG BULLISH CONFLUENCE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("1. Strong Bullish Confluence")
    print(_line())

    bull = bullish_dataset()
    # Append a hammer continuing from the last close to inject bullish
    # candle evidence while preserving the bullish structure.
    bull_with_hammer = list(bull) + [
        hammer_candle(len(bull), bull[-1].close)
    ]
    t = len(bull_with_hammer) - 1
    a = setup_at(pat_engine, mc_engine, setup_engine, bull_with_hammer, t)
    print(formatter.format(a))
    print(
        f"\nExpected: BULLISH + POTENTIAL_SETUP "
        f"(got {a.direction.name} + {a.classification.name})",
    )

    # ------------------------------------------------------------
    # 2. STRONG BEARISH CONFLUENCE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("2. Strong Bearish Confluence")
    print(_line())

    bear = bearish_dataset()
    bear_with_star = list(bear) + [
        shooting_star_candle(len(bear), bear[-1].close)
    ]
    t = len(bear_with_star) - 1
    a = setup_at(pat_engine, mc_engine, setup_engine, bear_with_star, t)
    print(formatter.format(a))
    print(
        f"\nExpected: BEARISH + POTENTIAL_SETUP "
        f"(got {a.direction.name} + {a.classification.name})",
    )

    # ------------------------------------------------------------
    # 3. CONFLICTING EVIDENCE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("3. Conflicting Evidence (bullish structure + bearish candle)")
    print(_line())

    conflict = list(bull) + [
        shooting_star_candle(len(bull), bull[-1].close)
    ]
    t = len(conflict) - 1
    a = setup_at(pat_engine, mc_engine, setup_engine, conflict, t)
    print(formatter.format(a))
    has_conflict = len(a.evidence.conflicting) > 0
    print(
        f"\nConflict recorded: {has_conflict} "
        f"(conflicting sources: "
        f"{[i.source.name for i in a.evidence.conflicting]})",
    )
    print(
        "Classification must not incorrectly treat all evidence as "
        "bullish.",
    )

    # ------------------------------------------------------------
    # 4. NEUTRAL / RANGE MARKET
    # ------------------------------------------------------------
    print("\n" + _line())
    print("4. Neutral / Range Market")
    print(_line())

    rng = range_dataset()
    t = len(rng) - 1
    a = setup_at(pat_engine, mc_engine, setup_engine, rng, t)
    print(formatter.format(a))
    print(
        f"\nRange market -> classification capped at WATCH at most "
        f"(got {a.classification.name}).",
    )

    # ------------------------------------------------------------
    # 5. NO_SETUP (insufficient evidence)
    # ------------------------------------------------------------
    print("\n" + _line())
    print("5. Insufficient Structure -> NO_SETUP")
    print(_line())

    minimal = rng[:3]  # too few swings / structure
    t = len(minimal) - 1
    a = setup_at(pat_engine, mc_engine, setup_engine, minimal, t)
    print(formatter.format(a))

    # ------------------------------------------------------------
    # 6. FUTURE LEAKAGE PROOF
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Future Leakage Proof")
    print(_line())

    data = bull_with_hammer
    T = len(data) - 2  # a point with real context

    from_full = setup_at(
        pat_engine, mc_engine, setup_engine, data, T,
    )
    from_prefix = setup_at(
        pat_engine, mc_engine, setup_engine, data[: T + 1], T,
    )
    agree = from_full == from_prefix
    print(
        f"{'PASS' if agree else 'FAIL'}: prefix/full-series setup "
        f"agrees at T={T}",
    )

    mutated = list(data)
    future_index = T + 1
    mutated[future_index] = make_candle(
        999.0, 1001.0, 997.0, future_index,
    )
    from_mut = setup_at(
        pat_engine, mc_engine, setup_engine, mutated, T,
    )
    unchanged = from_full == from_mut
    print(
        f"{'PASS' if unchanged else 'FAIL'}: future mutation leaves "
        f"setup(T={T}) unchanged",
    )

    if not (agree and unchanged):
        print("WARNING: future-leakage proof did not pass.")

    # ------------------------------------------------------------
    # 7. EXISTING PIPELINE BEHAVIOUR (unchanged)
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Existing Pipeline Behaviour")
    print(_line())

    candles = trending_dataset()

    before = HistoricalEvaluationPipeline(
        PipelineConfig(enable_setup_confluence=False),
    ).evaluate(candles)
    after = HistoricalEvaluationPipeline(
        PipelineConfig(enable_setup_confluence=True),
    ).evaluate(candles)

    print(f"Signals before: {before.signals_generated}")
    print(f"Signals after : {after.signals_generated}")
    print(f"Completed trades before: {before.completed_trades}")
    print(f"Completed trades after : {after.completed_trades}")

    sig_ok = before.signals_generated == after.signals_generated
    trade_ok = before.completed_trades == after.completed_trades
    print(
        f"{'PASS' if sig_ok and trade_ok else 'FAIL'}: existing "
        f"signal / trade behaviour unchanged",
    )

    point = after.evaluation_points_sequence[-1]
    has_setup = getattr(point, "setup_assessment", None) is not None
    print(f"Setup assessment attached to evaluation points: {has_setup}")

    # Distribution of classifications in the run.
    counts = {"NO_SETUP": 0, "WATCH": 0, "POTENTIAL_SETUP": 0}
    for p in after.evaluation_points_sequence:
        sa = p.setup_assessment
        if sa is not None:
            counts[sa.classification.name] = counts.get(
                sa.classification.name, 0,
            ) + 1
    print(f"Classification distribution: {counts}")

    print("\n" + _line())
    print("Sprint 11Q demo completed successfully.")
    print(_line())


if __name__ == "__main__":
    main()
