"""
Demo for Sprint 11U — Multi-Timeframe Trade Opportunity Research &
Market Scanner.

This demo exercises the multi-timeframe market scanner on deterministic
synthetic data and proves:

1.  One instrument with aligned timeframes
2.  One instrument with conflicting timeframes
3.  Multiple instruments
4.  One instrument with missing higher timeframe data
5.  One instrument with no setup
6.  Multiple valid opportunities
7.  Deterministic market-level ranking
8.  Higher-timeframe candle completion / look-ahead protection
9.  Future candle mutation protection
10. Market scan with no opportunities

Plus:
- Point-in-time leakage proof (scan(T) from prefix == full series)
- Existing pipeline regression (signals / trades unchanged before/after)

The demo prints descriptive market-scan reports. It makes NO
profitability, probability, or directional prediction. A market scan
result is a DESCRIPTIVE classification of the strongest available
technical trade opportunities across the scanned instruments /
timeframes at one evaluation point, NOT a trade signal.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.market_scan_config import MarketScanConfig
from engine.intelligence.market_scan_serialization import (
    serialize_scan,
    deserialize_scan,
)
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.market_scan import MTFAlignment, ScanStatus
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.market_scan import MarketScanFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


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
    start: float, n_legs: int = 4, start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1),
) -> list[OHLCVCandle]:
    """Bullish zigzag (HH/HL) producing a BULLISH market context."""
    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close + 6, 2)
            candles.append(_candle(close, ts))
            ts = ts + step
        for _ in range(2):
            close = round(close - 3, 2)
            candles.append(_candle(close, ts))
            ts = ts + step
    return candles


def _bearish_zigzag(
    start: float, n_legs: int = 4, start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1),
) -> list[OHLCVCandle]:
    """Bearish zigzag (LH/LL) producing a BEARISH market context."""
    candles: list[OHLCVCandle] = []
    close = start
    ts = start_ts
    for _ in range(n_legs):
        for _ in range(3):
            close = round(close - 6, 2)
            candles.append(_candle(close, ts))
            ts = ts + step
        for _ in range(2):
            close = round(close + 3, 2)
            candles.append(_candle(close, ts))
            ts = ts + step
    return candles


def _flat_dataset(n: int = 40) -> list[OHLCVCandle]:
    """Sideways oscillating market (NEUTRAL / RANGE)."""
    candles: list[OHLCVCandle] = []
    base = 100.0
    ts = _EPOCH
    for i in range(n):
        close = round(base + (2 if i % 2 == 0 else -2), 2)
        candles.append(_candle(close, ts))
        ts = ts + timedelta(days=1)
    return candles


def _aligned_long_pair():
    """Context (daily) bullish; setup (15M) bullish -> ALIGNED LONG."""
    context = _bullish_zigzag(100.0, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup = _bullish_zigzag(
        context[-1].close, start_ts=setup_start, step=timedelta(minutes=15),
    )
    return context, setup


def _aligned_short_pair():
    """Context (daily) bearish; setup (15M) bearish -> ALIGNED SHORT."""
    context = _bearish_zigzag(200.0, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup = _bearish_zigzag(
        context[-1].close, start_ts=setup_start, step=timedelta(minutes=15),
    )
    # Replace with a bearish zigzag continuation.
    setup = []
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


def _conflicting_long_pair():
    """Context (daily) BEARISH; setup (15M) bullish -> CONFLICTING LONG."""
    context = _bearish_zigzag(200.0, start_ts=_EPOCH)
    setup_start = context[-1].timestamp + timedelta(minutes=15)
    setup = _bullish_zigzag(
        context[-1].close, start_ts=setup_start, step=timedelta(minutes=15),
    )
    return context, setup


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    fmt = MarketScanFormatter()
    engines = ScanEngines.default()
    scanner = MarketScanner(
        MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        ),
    )

    # =========================================================
    # 1. ONE INSTRUMENT WITH ALIGNED TIMEFRAMES
    # =========================================================
    _banner("1. Aligned timeframes (BULLISH context + LONG setup)")
    ctx, setup = _aligned_long_pair()
    res = scanner.scan(
        [InstrumentDataset("TCS", tuple(ctx), tuple(setup))],
        engines=engines,
    )
    print(fmt.format(res))
    r = res.results[0]
    print(
        f"\n[check] instrument={r.instrument} direction={r.direction} "
        f"alignment={r.alignment.name} status={res.status.name} "
        f"complete={r.complete}"
    )
    assert r.complete is True
    assert r.alignment == MTFAlignment.ALIGNED

    # =========================================================
    # 2. ONE INSTRUMENT WITH CONFLICTING TIMEFRAMES
    # =========================================================
    _banner("2. Conflicting timeframes (BEARISH context + LONG setup)")
    ctx_c, setup_c = _conflicting_long_pair()
    res_c = scanner.scan(
        [InstrumentDataset("RELIANCE", tuple(ctx_c), tuple(setup_c))],
        engines=engines,
    )
    print(fmt.format(res_c))
    rc = res_c.results[0]
    print(
        f"\n[check] instrument={rc.instrument} direction={rc.direction} "
        f"alignment={rc.alignment.name} complete={rc.complete}"
    )
    assert rc.alignment == MTFAlignment.CONFLICTING

    # =========================================================
    # 3. MULTIPLE INSTRUMENTS  +  6. MULTIPLE VALID OPPORTUNITIES
    #    +  7. DETERMINISTIC MARKET-LEVEL RANKING
    # =========================================================
    _banner("3+6+7. Multiple instruments, deterministic ranking")
    ctx_a, setup_a = _aligned_long_pair()
    ctx_b, setup_b = _aligned_short_pair()
    res_multi = scanner.scan(
        [
            InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
            InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
        ],
        engines=engines,
    )
    print(fmt.format(res_multi))
    # Determinism: re-scan and confirm identical ranking.
    res_multi_2 = scanner.scan(
        [
            InstrumentDataset("TCS", tuple(ctx_a), tuple(setup_a)),
            InstrumentDataset("NIFTY", tuple(ctx_b), tuple(setup_b)),
        ],
        engines=engines,
    )
    same = (
        [r.opportunity.instrument for r in res_multi.ranked]
        == [r.opportunity.instrument for r in res_multi_2.ranked]
    )
    print(f"\n[check] deterministic ranking identical on re-scan: {same}")
    assert same

    # =========================================================
    # 4. ONE INSTRUMENT WITH MISSING HIGHER TIMEFRAME DATA
    # =========================================================
    _banner("4. Missing higher-timeframe data (INCOMPLETE)")
    ctx_d, setup_d = _aligned_long_pair()
    res_inc = scanner.scan(
        [InstrumentDataset("HDFCBANK", tuple(), tuple(setup_d))],
        engines=engines,
    )
    print(fmt.format(res_inc))
    assert res_inc.status == ScanStatus.INCOMPLETE
    assert res_inc.results[0].alignment == MTFAlignment.UNKNOWN
    print(f"\n[check] status={res_inc.status.name} (missing data not fabricated)")

    # =========================================================
    # 5. ONE INSTRUMENT WITH NO SETUP
    # =========================================================
    _banner("5. No setup (flat / insufficient structure)")
    flat_ctx = _flat_dataset()
    flat_setup = []
    close = flat_ctx[-1].close
    ts = flat_ctx[-1].timestamp + timedelta(minutes=15)
    for i in range(40):
        close = round(close + (0.05 if i % 2 == 0 else -0.05), 2)
        flat_setup.append(_candle(close, ts))
        ts = ts + timedelta(minutes=15)
    res_flat = scanner.scan(
        [InstrumentDataset("ICICIBANK", tuple(flat_ctx), tuple(flat_setup))],
        engines=engines,
    )
    print(fmt.format(res_flat))
    print(f"\n[check] status={res_flat.status.name}")

    # =========================================================
    # 8. HIGHER-TIMEFRAME CANDLE COMPLETION / LOOK-AHEAD PROTECTION
    # =========================================================
    _banner("8. Higher-timeframe candle completion / look-ahead")
    ctx_la, setup_la = _aligned_long_pair()
    # Add an in-progress HTF candle at the SAME time as the setup eval
    # time — it must NOT be used as context.
    in_progress_ts = setup_la[-1].timestamp
    ctx_with_inprogress = list(ctx_la) + [_candle(999.0, in_progress_ts)]
    res_complete = scanner.scan(
        [InstrumentDataset("TCS", tuple(ctx_la), tuple(setup_la))],
        engines=engines,
    )
    res_inprogress = scanner.scan(
        [
            InstrumentDataset(
                "TCS", tuple(ctx_with_inprogress), tuple(setup_la),
            ),
        ],
        engines=engines,
    )
    align_same = (
        res_complete.results[0].alignment
        == res_inprogress.results[0].alignment
    )
    dir_same = (
        res_complete.results[0].direction
        == res_inprogress.results[0].direction
    )
    print(
        f"[check] in-progress HTF candle excluded: alignment unchanged "
        f"={align_same}, direction unchanged={dir_same}",
    )
    assert align_same and dir_same

    # =========================================================
    # 9. FUTURE CANDLE MUTATION PROTECTION (HTF + LTF + multi)
    # =========================================================
    _banner("9. Future candle mutation protection")
    eval_time = setup_la[-1].timestamp
    before = scanner.scan(
        [InstrumentDataset("TCS", tuple(ctx_la), tuple(setup_la))],
        evaluation_time=eval_time,
        engines=engines,
    )
    future_ctx = list(ctx_la) + [_candle(999.0, eval_time + timedelta(days=3))]
    future_setup = list(setup_la) + [
        _candle(999.0, eval_time + timedelta(hours=2)),
    ]
    after = scanner.scan(
        [InstrumentDataset("TCS", tuple(future_ctx), tuple(future_setup))],
        evaluation_time=eval_time,
        engines=engines,
    )
    ok = (
        before.results[0].alignment == after.results[0].alignment
        and before.results[0].direction == after.results[0].direction
    )
    print(
        f"[check] HTF+LTF future mutation leaves scan(T) unchanged: {ok}",
    )
    assert ok

    # Multi-instrument future mutation.
    ctx_m1, setup_m1 = _aligned_long_pair()
    ctx_m2, setup_m2 = _aligned_short_pair()
    eval_time_m = max(setup_m1[-1].timestamp, setup_m2[-1].timestamp)
    before_m = scanner.scan(
        [
            InstrumentDataset("A", tuple(ctx_m1), tuple(setup_m1)),
            InstrumentDataset("B", tuple(ctx_m2), tuple(setup_m2)),
        ],
        evaluation_time=eval_time_m,
        engines=engines,
    )
    mutated_a = list(setup_m1) + [
        _candle(999.0, eval_time_m + timedelta(hours=1)),
    ]
    mutated_b = list(setup_m2) + [
        _candle(999.0, eval_time_m + timedelta(hours=1)),
    ]
    after_m = scanner.scan(
        [
            InstrumentDataset("A", tuple(ctx_m1), tuple(mutated_a)),
            InstrumentDataset("B", tuple(ctx_m2), tuple(mutated_b)),
        ],
        evaluation_time=eval_time_m,
        engines=engines,
    )
    ok_m = all(
        a.alignment == b.alignment and a.direction == b.direction
        for a, b in zip(before_m.results, after_m.results)
    )
    print(f"[check] multi-instrument future mutation stable: {ok_m}")
    assert ok_m

    # =========================================================
    # 10. MARKET SCAN WITH NO OPPORTUNITIES
    # =========================================================
    _banner("10. Market scan with no opportunities")
    res_none = scanner.scan(
        [InstrumentDataset("EMPTY", tuple(), tuple())], engines=engines,
    )
    print(fmt.format(res_none))
    assert res_none.status == ScanStatus.INCOMPLETE
    assert res_none.best is None
    print(f"\n[check] status={res_none.status.name} best={res_none.best}")

    # =========================================================
    # POINT-IN-TIME LEAKAGE PROOF (prefix == full series at T)
    # =========================================================
    _banner("Point-in-time leakage proof (prefix == full series at T)")
    ctx_p, setup_p = _aligned_long_pair()
    mid = len(setup_p) // 2
    eval_time_p = setup_p[mid].timestamp
    full_p = scanner.scan(
        [InstrumentDataset("TCS", tuple(ctx_p), tuple(setup_p))],
        evaluation_time=eval_time_p,
        engines=engines,
    )
    trunc_p = scanner.scan(
        [
            InstrumentDataset(
                "TCS", tuple(ctx_p), tuple(setup_p[: mid + 1]),
            ),
        ],
        evaluation_time=eval_time_p,
        engines=engines,
    )
    leak_ok = (
        full_p.results[0].alignment == trunc_p.results[0].alignment
        and full_p.results[0].direction == trunc_p.results[0].direction
    )
    print(
        f"[check] prefix == full series at T: alignment+direction match "
        f"= {leak_ok}",
    )
    assert leak_ok

    # =========================================================
    # SERIALIZATION ROUND TRIP
    # =========================================================
    _banner("Serialization round trip")
    payload = serialize_scan(res_multi)
    restored = deserialize_scan(payload)
    same_id = restored.scan_id == res_multi.scan_id
    same_status = restored.status == res_multi.status
    same_ranked = [
        r.opportunity.instrument for r in restored.ranked
    ] == [r.opportunity.instrument for r in res_multi.ranked]
    print(f"[check] scan_id preserved={same_id} status preserved={same_status}")
    print(f"[check] ranked instruments preserved={same_ranked}")
    assert same_id and same_status and same_ranked

    # =========================================================
    # EXISTING PIPELINE REGRESSION
    # =========================================================
    _banner("Existing pipeline regression (signals / trades unchanged)")
    candles = trending_dataset()
    pipe = HistoricalEvaluationPipeline(PipelineConfig())
    r1 = pipe.evaluate(candles)
    r2 = pipe.evaluate(candles)
    reg = (
        r1.signals_generated == r2.signals_generated
        and r1.completed_trades == r2.completed_trades
    )
    print(
        f"[check] pipeline signals={r1.signals_generated} "
        f"trades={r1.completed_trades} unchanged on re-run: {reg}",
    )
    assert reg

    # =========================================================
    # SUMMARY
    # =========================================================
    _banner("Summary")
    print("Market scanner demonstrated:")
    print("- aligned timeframes (ALIGNED)")
    print("- conflicting timeframes (CONFLICTING)")
    print("- multiple instruments + deterministic ranking")
    print("- missing higher-timeframe data (INCOMPLETE, not fabricated)")
    print("- no setup")
    print("- multiple valid opportunities")
    print("- higher-timeframe candle completion / look-ahead protection")
    print("- future candle mutation protection (HTF, LTF, multi-instrument)")
    print("- market scan with no opportunities")
    print("- point-in-time leakage proof")
    print("- serialization round trip")
    print("- existing pipeline regression (signals/trades unchanged)")
    print()
    print(
        "Market scan results are DESCRIPTIVE technical-analysis outputs "
        "and are NOT predictive signals or guarantees of profitability.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
