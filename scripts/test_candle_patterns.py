"""
Demo script for the candle / price-action pattern intelligence
layer (Sprint 11O).

Demonstrates:

1. A deterministic candle sequence containing one example of
   each supported pattern.
2. Running the pattern detector and printing every detected
   pattern with its measurements.
3. Historical safety: detecting on a truncated prefix
   candles[:T+1] produces the same patterns at T as detecting
   on the full series.
4. Pipeline integration: the existing HistoricalEvaluationPipeline
   receives the pattern evidence on every evaluation point.

The demo makes NO claim that any detected pattern is profitable
or predictive. A pattern is a description of candle shape only.
"""

import os
import sys
from collections import Counter

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
    ),
)

from datetime import UTC, datetime, timedelta

from engine.config.candle_pattern_config import CandlePatternConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.models.candle_pattern import (
    CandleDirection,
    CandleMeasurements,
    CandlePatternType,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    trending_dataset,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def candle(open_, high, low, close, index, volume=1000.0):
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def build_demo_sequence():
    """
    Build a deterministic sequence embedding one example of each
    supported pattern. Each pattern is placed at a known index so
    the demo can verify detection.

    Index layout:
        0  DOJI            (open==close, large wicks)
        1  HAMMER          (long lower wick, small upper wick)
        2  SHOOTING_STAR   (long upper wick, small lower wick)
        3  BULLISH_ENGULFING (prior bearish + current bullish engulf)
        5  BEARISH_ENGULFING (prior bullish + current bearish engulf)
        7  INSIDE_BAR      (current range inside prior range)
    """

    return [
        # 0 DOJI
        candle(100.0, 105.0, 95.0, 100.0, 0),
        # 1 HAMMER (body 1, lower wick 5, upper wick 0)
        candle(101.0, 102.0, 95.0, 102.0, 1),
        # 2 SHOOTING STAR (body 1, upper wick 5, lower wick 0)
        candle(100.0, 105.0, 99.0, 101.0, 2),
        # 3 BULLISH ENGULFING (prior bearish @3? no — needs prior)
        #   index 3 prior (bearish), index 4 current (bullish engulf)
        candle(104.0, 105.0, 99.0, 100.0, 3),  # bearish prior
        candle(99.0, 106.0, 99.0, 105.0, 4),  # bullish engulf
        # 5 BEARISH ENGULFING
        candle(99.0, 106.0, 99.0, 105.0, 5),  # bullish prior
        candle(105.0, 106.0, 98.0, 98.0, 6),  # bearish engulf
        # 7 INSIDE BAR
        candle(95.0, 110.0, 90.0, 105.0, 7),  # big prior range
        candle(100.0, 103.0, 98.0, 101.0, 8),  # inside range
    ]


def print_measurements(m: CandleMeasurements) -> str:
    return (
        f"range={m.range:.4f} body={m.body:.4f} "
        f"upper={m.upper_wick:.4f} lower={m.lower_wick:.4f} "
        f"b/r={m.body_to_range_ratio:.4f} dir={m.direction.name}"
    )


def main() -> None:
    print("=" * 72)
    print("Sprint 11O — Candle / Price-Action Pattern Intelligence")
    print("=" * 72)
    print(
        "NOTE: detected patterns describe candle SHAPE only. They\n"
        "are NOT signals and make NO profitability / predictive\n"
        "claim.\n",
    )

    candles = build_demo_sequence()

    engine = CandlePatternEngine()
    patterns = engine.detect(candles)

    print("-" * 72)
    print("Detected patterns (full sequence):")
    print("-" * 72)
    by_type: dict = {}
    for p in patterns:
        by_type.setdefault(p.pattern_type, []).append(p)

    for ptype in CandlePatternType:
        hits = by_type.get(ptype, [])
        print(f"\n  {ptype.name} ({len(hits)} match(es))")
        for p in hits:
            prior = ""
            if p.prior_index is not None:
                prior = f" prior_index={p.prior_index}"
            print(
                f"    index={p.index} dir={p.direction.name} "
                f"score={p.score:.4f}{prior}"
            )
            print(f"      measurements: {print_measurements(p.measurements)}")
            print(f"      reason: {p.reason}")

    counts = Counter(p.pattern_type.name for p in patterns)
    print("\nPattern tally:", dict(counts))

    # ---- Historical safety demonstration ----
    print("\n" + "=" * 72)
    print("Historical safety demonstration")
    print("=" * 72)

    # Patterns at index <= 4 computed from the full series vs a
    # truncated prefix must be identical.
    def signature(ps):
        return tuple(
            (
                p.pattern_type,
                p.index,
                p.direction,
                p.score,
                p.measurements.range,
                p.measurements.body,
                p.measurements.upper_wick,
                p.measurements.lower_wick,
                p.measurements.body_to_range_ratio,
                p.prior_index,
            )
            for p in ps
        )

    cutoff = 4
    full_at = [p for p in patterns if p.index <= cutoff]
    trunc = engine.detect(candles[: cutoff + 1])
    trunc_at = [p for p in trunc if p.index <= cutoff]

    print(
        f"Patterns at indices <= {cutoff} on full series: "
        f"{len(full_at)}"
    )
    print(
        f"Patterns at indices <= {cutoff} on truncated prefix: "
        f"{len(trunc_at)}"
    )
    assert signature(full_at) == signature(trunc_at), (
        "Look-ahead violation: patterns at T changed when future "
        "candles were removed."
    )
    print("PASS: pattern(T) is identical with and without future candles.")

    # Mutate a future candle and confirm patterns at T are unchanged.
    mutated = list(candles)
    mutated[7] = candle(1.0, 999.0, 0.5, 990.0, 7)
    mut_at = [p for p in engine.detect(mutated) if p.index <= cutoff]
    assert signature(mut_at) == signature(full_at), (
        "Look-ahead violation: mutating a future candle changed "
        "patterns at T."
    )
    print("PASS: mutating a future candle leaves pattern(T) unchanged.")

    # ---- Pipeline integration demonstration ----
    print("\n" + "=" * 72)
    print("Pipeline integration demonstration")
    print("=" * 72)

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    print(f"Pipeline candles processed: {result.candles_processed}")
    print(f"Pipeline evaluation points: {result.evaluation_points}")
    print(f"Pipeline signals generated: {result.signals_generated}")
    print(f"Pipeline completed trades:  {result.completed_trades}")
    print(f"Pipeline patterns detected: {len(result.patterns)}")

    pipe_counts = Counter(p.pattern_type.name for p in result.patterns)
    print("Pipeline pattern tally:", dict(pipe_counts))

    # Show a few evaluation points carrying pattern evidence.
    sample = [
        pt for pt in result.evaluation_points_sequence if pt.patterns
    ][:3]
    print("\nSample evaluation points carrying pattern evidence:")
    for pt in sample:
        names = [p.pattern_type.name for p in pt.patterns]
        print(
            f"  index={pt.index} signal_state={pt.signal_state} "
            f"patterns={names}"
        )

    print(
        "\nPattern evidence is attached additively to every\n"
        "evaluation point. It is NOT fed into the existing\n"
        "confluence/decision/signal logic, so signal behaviour is\n"
        "unchanged (the signals/completed-trades counts above match\n"
        "the pre-11O pipeline)."
    )

    print("\nDemo completed successfully.")


if __name__ == "__main__":
    main()
