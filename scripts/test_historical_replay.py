"""
Demo for Sprint 11V — Historical Market Data Integration &
End-to-End Validation.

This demo proves the EXISTING trading-intelligence pipeline (Sprints
11A-11U) can consume realistic historical OHLCV data exactly as if it
were seeing the market sequentially in real time, without using future
information. It reuses the existing data validator, market context,
setup / confluence, trade candidate, trade decision, trade opportunity
and multi-timeframe market scanner engines — NO intelligence is
duplicated.

The demo visibly proves:

1.  Historical data loaded
2.  Multiple instruments processed
3.  Multiple timeframes processed
4.  End-to-end scan generated
5.  Best opportunity identified when available
6.  Incomplete data handled honestly
7.  HTF incomplete candle protection
8.  Prefix/full-series equality
9.  Future mutation protection
10. Replay determinism
11. Serialization round trip
12. Existing pipeline signal/trade regression

The demo prints an analyst-style end-to-end market scan report. It
makes NO profitability, probability, or directional prediction. A
historical replay / market scan result is a DESCRIPTIVE classification
of the strongest available technical trade opportunities across the
scanned instruments / timeframes at one evaluation point, NOT a trade
signal.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.market_scan_config import MarketScanConfig
from engine.data.historical_adapter import (
    HistoricalAdapterConfig,
    HistoricalDataAdapter,
)
from engine.data.historical_fixtures import (
    historical_records,
    invalid_high_below_low,
)
from engine.intelligence.historical_replay import (
    HistoricalReplayEngine,
    evaluation_times_from_setup_candles,
)
from engine.intelligence.market_scan_serialization import (
    deserialize_scan,
    serialize_scan,
)
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.historical_replay import HistoricalReplayFormatter


_EPOCH = datetime(2025, 1, 6, tzinfo=UTC)


def _candle(close: float, ts: datetime, spread: float = 2.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=1000.0,
    )


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    fmt = HistoricalReplayFormatter()
    engines = ScanEngines.default()
    scan_config = MarketScanConfig(
        context_timeframe="1D", setup_timeframe="15M", min_history=10,
    )
    adapter = HistoricalDataAdapter(
        HistoricalAdapterConfig(timeframes=("1D", "15M"), min_history=10),
    )

    # =========================================================
    # 1. HISTORICAL DATA LOADED
    # =========================================================
    _banner("1. Historical data loaded")
    records = historical_records()
    print(f"Loaded {len(records)} historical OHLCV records.")

    # =========================================================
    # 2 + 3. MULTIPLE INSTRUMENTS + MULTIPLE TIMEFRAMES PROCESSED
    # =========================================================
    _banner("2+3. Multiple instruments + multiple timeframes processed")
    dataset = adapter.normalize(records)
    print(f"Instruments : {', '.join(dataset.instruments)}")
    print(f"Valid series: {dataset.valid_count}")
    print(f"Incomplete  : {dataset.incomplete_count}")
    print(f"Invalid     : {dataset.invalid_count}")
    for inst in dataset.instruments:
        for tf in dataset.get(inst).timeframes:
            s = dataset.get(inst).series[tf]
            print(
                f"  {inst}/{tf}: {s.quality.name} candles={s.candle_count}",
            )
    assert dataset.valid_count == 10
    assert dataset.invalid_count == 0

    # Show invalid-data handling too.
    bad_ds = adapter.normalize([invalid_high_below_low()])
    print(
        f"\n[check] invalid record rejected: invalid_count="
        f"{bad_ds.invalid_count}",
    )
    assert bad_ds.invalid_count >= 1

    # =========================================================
    # 4 + 5. END-TO-END SCAN + BEST OPPORTUNITY IDENTIFIED
    # =========================================================
    _banner("4+5. End-to-end scan + best opportunity identified")
    replay = HistoricalReplayEngine(scan_config)
    times = evaluation_times_from_setup_candles(
        dataset, "15M", count=3, min_history=12,
    )
    print(f"Evaluation timestamps: {len(times)}")
    result = replay.replay(dataset, times, engines=engines)
    print(f"Replay ID: {result.replay_id}")
    for p in result.points:
        best = (
            p.scan.best.opportunity.instrument if p.scan.has_best else "none"
        )
        print(
            f"  T={p.evaluation_time} status={p.scan.status.name} "
            f"eligible={p.scan.eligible_count} best={best}",
        )
    assert result.points[-1].scan.has_best is True

    # Print the full analyst-style report.
    print()
    print(fmt.format(result))

    # =========================================================
    # 6. INCOMPLETE DATA HANDLED HONESTLY
    # =========================================================
    _banner("6. Incomplete data handled honestly")
    from engine.models.historical import (
        DataQuality,
        HistoricalDataset,
        HistoricalInstrumentData,
        TimeframeSeries,
    )
    empty = HistoricalDataset(
        data={
            "NONE": HistoricalInstrumentData(
                instrument="NONE",
                series={
                    "1D": TimeframeSeries("NONE", "1D", (), DataQuality.INCOMPLETE),
                    "15M": TimeframeSeries("NONE", "15M", (), DataQuality.INCOMPLETE),
                },
            ),
        },
    )
    res_incomplete = replay.replay(empty, [_EPOCH], engines=engines)
    print(
        f"[check] empty instrument scan status="
        f"{res_incomplete.points[0].scan.status.name} best="
        f"{res_incomplete.points[0].scan.best}",
    )
    assert res_incomplete.points[0].scan.status.name == "INCOMPLETE"
    assert res_incomplete.points[0].scan.best is None

    # =========================================================
    # 7. HTF INCOMPLETE CANDLE PROTECTION
    # =========================================================
    _banner("7. HTF incomplete candle protection")
    scanner = MarketScanner(scan_config)
    nifty_ctx = list(dataset.context_candles("NIFTY", "1D"))
    nifty_setup = list(dataset.setup_candles("NIFTY", "15M"))
    eval_time = nifty_setup[-1].timestamp
    in_progress_ts = eval_time
    ctx_with_inprogress = nifty_ctx + [_candle(999.0, in_progress_ts)]
    res_clean = scanner.scan(
        [InstrumentDataset("NIFTY", tuple(nifty_ctx), tuple(nifty_setup))],
        evaluation_time=eval_time, engines=engines,
    )
    res_inprogress = scanner.scan(
        [
            InstrumentDataset(
                "NIFTY", tuple(ctx_with_inprogress), tuple(nifty_setup),
            ),
        ],
        evaluation_time=eval_time, engines=engines,
    )
    htf_ok = (
        res_clean.results[0].alignment == res_inprogress.results[0].alignment
        and res_clean.results[0].direction == res_inprogress.results[0].direction
    )
    print(
        f"[check] in-progress HTF candle excluded: scan(T) unchanged = {htf_ok}",
    )
    assert htf_ok

    # =========================================================
    # 8. PREFIX/FULL-SERIES EQUALITY
    # =========================================================
    _banner("8. Prefix/full-series equality (point-in-time)")
    mid = len(nifty_setup) // 2
    eval_time_p = nifty_setup[mid].timestamp
    full_p = scanner.scan(
        [InstrumentDataset("NIFTY", tuple(nifty_ctx), tuple(nifty_setup))],
        evaluation_time=eval_time_p, engines=engines,
    )
    trunc_p = scanner.scan(
        [
            InstrumentDataset(
                "NIFTY", tuple(nifty_ctx), tuple(nifty_setup[: mid + 1]),
            ),
        ],
        evaluation_time=eval_time_p, engines=engines,
    )
    prefix_ok = (
        full_p.results[0].alignment == trunc_p.results[0].alignment
        and full_p.results[0].direction == trunc_p.results[0].direction
        and full_p.results[0].decision_score == trunc_p.results[0].decision_score
    )
    print(f"[check] prefix == full series at T: {prefix_ok}")
    assert prefix_ok

    # =========================================================
    # 9. FUTURE MUTATION PROTECTION (HTF + LTF + multi-instrument)
    # =========================================================
    _banner("9. Future mutation protection (HTF + LTF + multi-instrument)")
    before = scanner.scan(
        [InstrumentDataset("NIFTY", tuple(nifty_ctx), tuple(nifty_setup))],
        evaluation_time=eval_time, engines=engines,
    )
    future_ctx = nifty_ctx + [_candle(999.0, eval_time + timedelta(days=3))]
    future_setup = nifty_setup + [
        _candle(999.0, eval_time + timedelta(minutes=15)),
    ]
    after = scanner.scan(
        [
            InstrumentDataset(
                "NIFTY", tuple(future_ctx), tuple(future_setup),
            ),
        ],
        evaluation_time=eval_time, engines=engines,
    )
    future_ok = (
        before.results[0].alignment == after.results[0].alignment
        and before.results[0].direction == after.results[0].direction
    )
    print(f"[check] HTF+LTF future mutation leaves scan(T) unchanged: {future_ok}")
    assert future_ok

    instruments = ("NIFTY", "RELIANCE", "TCS")
    multi_ds = [
        InstrumentDataset(
            inst,
            dataset.context_candles(inst, "1D"),
            list(dataset.setup_candles(inst, "15M")),
        )
        for inst in instruments
    ]
    eval_time_m = max(
        dataset.setup_candles(inst, "15M")[-1].timestamp for inst in instruments
    )
    before_m = scanner.scan(multi_ds, evaluation_time=eval_time_m, engines=engines)
    mutated_m = [
        InstrumentDataset(
            d.instrument,
            d.context_candles,
            tuple(d.setup_candles)
            + (_candle(999.0, eval_time_m + timedelta(minutes=15)),),
        )
        for d in multi_ds
    ]
    after_m = scanner.scan(
        mutated_m, evaluation_time=eval_time_m, engines=engines,
    )
    multi_ok = all(
        a.alignment == b.alignment and a.direction == b.direction
        for a, b in zip(before_m.results, after_m.results)
    )
    print(f"[check] multi-instrument future mutation stable: {multi_ok}")
    assert multi_ok

    # =========================================================
    # 10. REPLAY DETERMINISM
    # =========================================================
    _banner("10. Replay determinism")
    r1 = replay.replay(dataset, times, engines=engines)
    r2 = replay.replay(dataset, times, engines=engines)
    det_ok = (
        r1.replay_id == r2.replay_id
        and [p.scan.scan_id for p in r1.points] == [
            p.scan.scan_id for p in r2.points
        ]
        and [
            [r.opportunity.instrument for r in p.scan.ranked]
            for p in r1.points
        ] == [
            [r.opportunity.instrument for r in p.scan.ranked]
            for p in r2.points
        ]
    )
    print(f"[check] replay deterministic across re-runs: {det_ok}")
    assert det_ok

    # Replay == direct point-in-time evaluation.
    direct_ok = True
    for point, t in zip(r1.points, times):
        direct_ds = [
            InstrumentDataset(
                inst,
                dataset.context_candles(inst, "1D"),
                tuple(
                    c for c in dataset.setup_candles(inst, "15M")
                    if c.timestamp <= t
                ),
            )
            for inst in dataset.instruments
        ]
        direct = scanner.scan(direct_ds, evaluation_time=t, engines=engines)
        if point.scan.status != direct.status:
            direct_ok = False
            break
        if [
            r.opportunity.instrument for r in point.scan.ranked
        ] != [r.opportunity.instrument for r in direct.ranked]:
            direct_ok = False
            break
    print(f"[check] replay == direct point-in-time eval: {direct_ok}")
    assert direct_ok

    # =========================================================
    # 11. SERIALIZATION ROUND TRIP
    # =========================================================
    _banner("11. Serialization round trip")
    last_scan = r1.points[-1].scan
    payload = serialize_scan(last_scan)
    restored = deserialize_scan(payload)
    ser_ok = (
        restored.scan_id == last_scan.scan_id
        and restored.timestamp == last_scan.timestamp
        and restored.status == last_scan.status
        and restored.instruments == last_scan.instruments
        and [
            (r.rank, r.opportunity.instrument, r.opportunity.direction,
             r.opportunity.decision_classification, r.opportunity.decision_score,
             r.alignment.name)
            for r in restored.ranked
        ] == [
            (r.rank, r.opportunity.instrument, r.opportunity.direction,
             r.opportunity.decision_classification, r.opportunity.decision_score,
             r.alignment.name)
            for r in last_scan.ranked
        ]
        and (restored.best.opportunity.instrument if restored.has_best else None)
        == (last_scan.best.opportunity.instrument if last_scan.has_best else None)
    )
    print(f"[check] serialization round trip preserves identity+ranking: {ser_ok}")
    assert ser_ok

    # =========================================================
    # 12. EXISTING PIPELINE SIGNAL/TRADE REGRESSION
    # =========================================================
    _banner("12. Existing pipeline signal/trade regression")
    candles = trending_dataset()
    pipe = HistoricalEvaluationPipeline(PipelineConfig())
    pr1 = pipe.evaluate(candles)
    pr2 = pipe.evaluate(candles)
    reg = (
        pr1.signals_generated == pr2.signals_generated
        and pr1.completed_trades == pr2.completed_trades
        and pr1.signals_generated == 4
        and pr1.completed_trades == 3
    )
    print(
        f"[check] pipeline signals={pr1.signals_generated} "
        f"trades={pr1.completed_trades} unchanged (baseline 4/3): {reg}",
    )
    assert reg

    # =========================================================
    # SUMMARY
    # =========================================================
    _banner("Summary")
    print("Sprint 11V demonstrated:")
    print("- historical data loaded + normalized (VALID/INVALID/INCOMPLETE)")
    print("- multiple instruments processed (isolation preserved)")
    print("- multiple timeframes processed (1D context + 15M setup)")
    print("- end-to-end scan generated via the EXISTING intelligence pipeline")
    print("- best opportunity identified when available (never manufactured)")
    print("- incomplete data handled honestly (never a directional conclusion)")
    print("- HTF incomplete candle protection (look-ahead safe)")
    print("- prefix/full-series equality (point-in-time correct)")
    print("- future mutation protection (HTF, LTF, multi-instrument)")
    print("- replay determinism + replay == direct point-in-time eval")
    print("- serialization round trip preserves identity + ranking")
    print("- existing pipeline signals/trades regression unchanged (4/3)")
    print()
    print(
        "Historical replay / market scan results are DESCRIPTIVE "
        "technical-analysis outputs and are NOT predictive signals or "
        "guarantees of profitability.",
    )
    print()
    print("Sprint 11V demo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
