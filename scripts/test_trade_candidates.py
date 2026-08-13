"""
Demo for Sprint 11R — Trade Candidate Generation.

This demo exercises the trade-candidate generation layer on
deterministic synthetic data and proves:

* a strong bullish setup  -> LONG CANDIDATE
* a strong bearish setup   -> SHORT CANDIDATE
* conflicting evidence     -> WATCH / NO_CANDIDATE (no automatic trade)
* insufficient structure/context -> NO_CANDIDATE
* invalid stop/target geometry -> safely reported as incomplete
  (no fabricated risk/reward)
* the no-future-leakage guarantee (prefix/full-series agreement at T
  and future-mutation leaves candidate(T) unchanged)
* additive pipeline integration (existing signal / trade behaviour
  unchanged; candidate attached to evaluation points)

The demo prints descriptive trade-candidate reports. It makes NO
profitability or directional prediction. A trade candidate is a
descriptive candidate for further validation, NOT a trade signal.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.swing_config import SwingConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_candidates import TradeCandidateFormatter


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


def _line(char: str = "-", width: int = 60) -> str:
    return char * width


def build_engines(lookback: int = 2):
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
):
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    a = setup_engine.assess(pats, ctx, index, candles[index].timestamp)
    return cand_engine.generate(a, ctx, index, candles[index].timestamp,
                                 candles[index].close)


def main() -> None:
    print("=" * 60)
    print("Sprint 11R — Trade Candidate Generation")
    print("=" * 60)

    pat_engine, mc_engine, setup_engine, cand_engine = build_engines()
    formatter = TradeCandidateFormatter()

    # ------------------------------------------------------------
    # 1. STRONG BULLISH SETUP -> LONG CANDIDATE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("1. Strong Bullish Setup -> LONG Candidate")
    print(_line())

    bull = bullish_dataset()
    bull_with_hammer = list(bull) + [
        hammer_candle(len(bull), bull[-1].close)
    ]
    t = len(bull_with_hammer) - 1
    c = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine,
        bull_with_hammer, t,
    )
    print(formatter.format(c))
    print(
        f"\nExpected: LONG + CANDIDATE "
        f"(got {c.direction.name} + {c.status.name}, setup "
        f"{c.setup_type.name})",
    )
    print(f"Geometry complete: {c.geometry_complete}")

    # ------------------------------------------------------------
    # 2. STRONG BEARISH SETUP -> SHORT CANDIDATE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("2. Strong Bearish Setup -> SHORT Candidate")
    print(_line())

    bear = bearish_dataset()
    bear_with_star = list(bear) + [
        shooting_star_candle(len(bear), bear[-1].close)
    ]
    t = len(bear_with_star) - 1
    c = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine,
        bear_with_star, t,
    )
    print(formatter.format(c))
    print(
        f"\nExpected: SHORT + CANDIDATE "
        f"(got {c.direction.name} + {c.status.name}, setup "
        f"{c.setup_type.name})",
    )

    # ------------------------------------------------------------
    # 3. CONFLICTING EVIDENCE -> WATCH / NO_CANDIDATE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("3. Conflicting Evidence (bullish structure + bearish candle)")
    print(_line())

    conflict = list(bull) + [
        shooting_star_candle(len(bull), bull[-1].close)
    ]
    t = len(conflict) - 1
    c = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine, conflict, t,
    )
    print(formatter.format(c))
    has_conflict = len(c.conflicting_evidence) > 0
    auto = c.status.name == "CANDIDATE"
    print(
        f"\nConflict recorded: {has_conflict}; "
        f"automatic candidate produced: {auto} "
        f"(status {c.status.name}).",
    )

    # ------------------------------------------------------------
    # 4. INSUFFICIENT STRUCTURE / CONTEXT -> NO_CANDIDATE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("4. Insufficient Structure / Context -> NO_CANDIDATE")
    print(_line())

    minimal = range_dataset()[:3]  # too few swings / structure
    t = len(minimal) - 1
    c = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine, minimal, t,
    )
    print(formatter.format(c))

    # ------------------------------------------------------------
    # 5. INVALID STOP / TARGET -> INCOMPLETE CANDIDATE
    # ------------------------------------------------------------
    print("\n" + _line())
    print("5. Invalid Stop / Target -> Safely Incomplete")
    print(_line())

    # The bullish breakout candidate above the last resistance has a
    # stop (nearest support below) but no target (resistance is below
    # the entry). Demonstrate honest "unavailable" reporting and a
    # hand-constructed case with no market context (no levels).
    bull2 = bullish_dataset()
    t = len(bull2) - 1
    pats = [
        p for p in pat_engine.detect(bull2[: t + 1]) if p.index == t
    ]
    ctx2 = mc_engine.analyze_at(bull2, t)
    a2 = setup_engine.assess(pats, ctx2, t, bull2[t].timestamp)
    # Force the candidate engine to see no market context -> no
    # structural levels -> geometry incomplete.
    c_incomplete = cand_engine.generate(
        a2, None, t, bull2[t].timestamp, bull2[t].close,
    )
    print(formatter.format(c_incomplete))
    print(
        f"\nWith no market context, stop/target are "
        f"unavailable (risk/reward: {c_incomplete.risk_reward_ratio}); "
        "no value fabricated.",
    )

    # ------------------------------------------------------------
    # 6. POINT-IN-TIME LEAKAGE TEST
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Point-in-Time Leakage Test")
    print(_line())

    data = bull_with_hammer
    T = len(data) - 2  # a point with real context

    from_full = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine, data, T,
    )
    from_prefix = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine,
        data[: T + 1], T,
    )
    agree = from_full == from_prefix
    print(
        f"{'PASS' if agree else 'FAIL'}: candidate(T={T}) from full "
        "series == candidate from prefix",
    )

    # ------------------------------------------------------------
    # 7. FUTURE CANDLE MUTATION TEST
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Future Candle Mutation Test")
    print(_line())

    mutated = list(data)
    future_index = T + 1
    mutated[future_index] = make_candle(
        999.0, 1001.0, 997.0, future_index,
    )
    from_mut = candidate_at(
        pat_engine, mc_engine, setup_engine, cand_engine, mutated, T,
    )
    unchanged = from_full == from_mut
    print(
        f"{'PASS' if unchanged else 'FAIL'}: future mutation leaves "
        f"candidate(T={T}) unchanged",
    )

    # Verify the geometry / direction / setup at T are all stable.
    geom_stable = (
        from_full.entry_reference == from_mut.entry_reference
        and from_full.stop_reference == from_mut.stop_reference
        and from_full.target_reference == from_mut.target_reference
        and from_full.direction == from_mut.direction
        and from_full.setup_type == from_mut.setup_type
        and from_full.status == from_mut.status
    )
    print(
        f"{'PASS' if geom_stable else 'FAIL'}: "
        "stop/target/entry/direction/setup unchanged after mutation",
    )

    if not (agree and unchanged and geom_stable):
        print("WARNING: leakage proof did not fully pass.")

    # ------------------------------------------------------------
    # 8. EXISTING PIPELINE BEHAVIOUR (before / after)
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Existing Pipeline Behaviour (before / after)")
    print(_line())

    candles = trending_dataset()
    before = HistoricalEvaluationPipeline(
        PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=False,
        ),
    ).evaluate(candles)
    after = HistoricalEvaluationPipeline(
        PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_candidates=True,
        ),
    ).evaluate(candles)

    print(f"Signals before: {before.signals_generated}")
    print(f"Signals after : {after.signals_generated}")
    print(f"Completed trades before: {before.completed_trades}")
    print(f"Completed trades after : {after.completed_trades}")

    sig_ok = before.signals_generated == after.signals_generated
    trade_ok = before.completed_trades == after.completed_trades
    print(
        f"{'PASS' if sig_ok and trade_ok else 'FAIL'}: existing "
        "signal / trade behaviour unchanged",
    )

    point = after.evaluation_points_sequence[-1]
    has_cand = getattr(point, "trade_candidate", None) is not None
    print(f"Trade candidate attached to evaluation points: {has_cand}")

    # Status distribution in the run.
    counts = {"NO_CANDIDATE": 0, "WATCH": 0, "CANDIDATE": 0}
    for p in after.evaluation_points_sequence:
        tc = p.trade_candidate
        if tc is not None:
            counts[tc.status.name] = counts.get(tc.status.name, 0) + 1
    print(f"Candidate status distribution: {counts}")

    # Show one candidate produced by the pipeline end-to-end.
    pipeline_cands = [
        p.trade_candidate for p in after.evaluation_points_sequence
        if p.trade_candidate is not None
        and p.trade_candidate.is_candidate
    ]
    if pipeline_cands:
        print("\nSample pipeline-generated CANDIDATE:")
        print(formatter.format(pipeline_cands[0]))

    print("\n" + _line())
    print("Sprint 11R demo completed successfully.")
    print(_line())


if __name__ == "__main__":
    main()
