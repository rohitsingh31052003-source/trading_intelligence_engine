"""
Demo for Sprint 11W — Historical Opportunity Outcome Evaluation.

This demo proves the forward-only outcome evaluation layer. It reuses
the EXISTING intelligence pipeline (Sprints 11A-11V) to identify
opportunities at a point in time ``T``, then evaluates what price did
AFTER ``T`` using ONLY candles that closed strictly after ``T`` and
within a configured evaluation horizon. The decision / opportunity at
``T`` is NEVER recalculated using future data and is NEVER mutated.

The demo visibly proves:

1.  LONG target hit
2.  LONG stop hit
3.  SHORT target hit
4.  SHORT stop hit
5.  BOTH_TOUCHED ambiguity (LONG + SHORT)
6.  Incomplete geometry -> NO_GEOMETRY
7.  Expiration -> EXPIRED
8.  MFE / MAE (absolute + R-normalized)
9.  Multiple opportunities (each evaluated independently)
10. Point-in-time protection (decision at T stable; forward-only window)
11. Future mutation outside the horizon does not change the outcome
12. Deterministic repeated evaluation
13. Serialization round trip
14. Existing pipeline signal/trade regression

The demo prints an analyst-style historical outcome report. It makes
NO profitability, probability, or directional prediction. A historical
outcome is a DESCRIPTIVE evaluation of what price did after an
opportunity was identified, NOT a trade signal and NOT a guarantee.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.market_scan_config import MarketScanConfig
from engine.data.historical_adapter import (
    HistoricalAdapterConfig,
    HistoricalDataAdapter,
)
from engine.data.historical_fixtures import historical_records
from engine.intelligence.historical_outcome import (
    HistoricalOutcomeEngine,
    OutcomeEvaluator,
)
from engine.intelligence.historical_outcome_serialization import (
    deserialize_outcome,
    deserialize_outcome_report,
    serialize_outcome,
    serialize_outcome_report,
)
from engine.intelligence.historical_replay import (
    HistoricalReplayEngine,
    evaluation_times_from_setup_candles,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.historical_outcome import HistoricalOutcomeFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _candle(
    close: float,
    ts: datetime,
    high: float | None = None,
    low: float | None = None,
) -> OHLCVCandle:
    if high is None:
        high = close + 1.0
    if low is None:
        low = close - 1.0
    return OHLCVCandle(ts, close, high, low, close, 1000.0)


def _future(closes: list[float]) -> list[OHLCVCandle]:
    return [_candle(c, _EPOCH + timedelta(days=i + 1)) for i, c in enumerate(closes)]


def _future_hl(pairs: list[tuple[float, float, float]]) -> list[OHLCVCandle]:
    return [
        _candle(c, _EPOCH + timedelta(days=i + 1), high=h, low=l)
        for i, (c, h, l) in enumerate(pairs)
    ]


def _long(entry=100.0, stop=95.0, target=110.0, instrument="NIFTY", rank=1) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction="LONG",
        evaluation_timestamp=_EPOCH,
        entry=entry, stop=stop, target=target,
        decision_classification="QUALIFIED", decision_score=70,
        opportunity_status="BEST_OPPORTUNITY", rank=rank,
        scan_id="scan-demo", setup_timeframe="15M",
    )


def _short(entry=100.0, stop=105.0, target=90.0, instrument="RELIANCE", rank=1) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction="SHORT",
        evaluation_timestamp=_EPOCH,
        entry=entry, stop=stop, target=target,
        decision_classification="QUALIFIED", decision_score=65,
        opportunity_status="BEST_OPPORTUNITY", rank=rank,
        scan_id="scan-demo", setup_timeframe="15M",
    )


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _check(label: str, ok: bool) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def main() -> int:
    ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=20))
    fmt = HistoricalOutcomeFormatter()

    _banner("Sprint 11W — Historical Opportunity Outcome Evaluation")

    # 1. LONG target hit
    _banner("1. LONG target hit")
    o = ev.evaluate(_long(), _future([105, 112, 108]))
    print(fmt.format(o))
    _check("LONG target hit", o.outcome_status == OutcomeStatus.TARGET_HIT)
    _check("realized R = 2.0", abs((o.realized_r or 0) - 2.0) < 1e-9)

    # 2. LONG stop hit
    _banner("2. LONG stop hit")
    o = ev.evaluate(_long(), _future([98, 94, 90]))
    print(fmt.format(o))
    _check("LONG stop hit", o.outcome_status == OutcomeStatus.STOP_HIT)
    _check("realized R = -1.0", abs((o.realized_r or 0) - (-1.0)) < 1e-9)

    # 3. SHORT target hit
    _banner("3. SHORT target hit")
    o = ev.evaluate(_short(), _future([95, 88, 92]))
    print(fmt.format(o))
    _check("SHORT target hit", o.outcome_status == OutcomeStatus.TARGET_HIT)

    # 4. SHORT stop hit
    _banner("4. SHORT stop hit")
    o = ev.evaluate(_short(), _future([102, 106, 108]))
    print(fmt.format(o))
    _check("SHORT stop hit", o.outcome_status == OutcomeStatus.STOP_HIT)

    # 5. BOTH_TOUCHED ambiguity
    _banner("5. BOTH_TOUCHED ambiguity (LONG + SHORT)")
    fut_both = _future_hl([(100, 115, 90)])
    o_long = ev.evaluate(_long(), fut_both)
    o_short = ev.evaluate(_short(), fut_both)
    print(fmt.format(o_long))
    _check("LONG both touched", o_long.outcome_status == OutcomeStatus.BOTH_TOUCHED)
    _check("LONG no fabricated R", o_long.realized_r is None)
    _check("SHORT both touched", o_short.outcome_status == OutcomeStatus.BOTH_TOUCHED)
    _check("SHORT no fabricated R", o_short.realized_r is None)

    # 6. Incomplete geometry
    _banner("6. Incomplete geometry -> NO_GEOMETRY")
    ngeo = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
    o = ev.evaluate(ngeo, _future([105, 112]))
    print(fmt.format(o))
    _check("NO_GEOMETRY", o.outcome_status == OutcomeStatus.NO_GEOMETRY)

    # 7. Expiration
    _banner("7. Expiration -> EXPIRED")
    ev_exp = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
    o = ev_exp.evaluate(_long(), _future([101, 102, 103, 112]))
    print(fmt.format(o))
    _check("EXPIRED", o.outcome_status == OutcomeStatus.EXPIRED)
    _check("mark-to-close exit", o.exit_price == 103.0)

    # 8. MFE / MAE
    _banner("8. MFE / MAE")
    o = ev.evaluate(_long(), _future_hl([(105, 106, 99), (107, 108, 97)]))
    print(fmt.format(o))
    _check("MFE = 8.0", abs((o.mfe or 0) - 8.0) < 1e-9)
    _check("MAE = 3.0", abs((o.mae or 0) - 3.0) < 1e-9)
    _check("MFE R = 1.6", abs((o.mfe_r or 0) - 1.6) < 1e-9)
    _check("MAE R = 0.6", abs((o.mae_r or 0) - 0.6) < 1e-9)

    # 9. Multiple opportunities
    _banner("9. Multiple opportunities (each evaluated independently)")
    subjects_futures = [
        (_long(instrument="ICICIBANK", rank=1), _future([105, 112])),
        (_long(instrument="NIFTY", rank=2), _future([98, 94])),
        (_short(instrument="RELIANCE", rank=3), _future([95, 88])),
        (_long(instrument="TCS", rank=4), _future([101, 102, 103])),
    ]
    outs = [ev.evaluate(s, f) for s, f in subjects_futures]
    for o in outs:
        print(fmt.format(o))
    _check(
        "4 distinct instruments",
        {o.instrument for o in outs} == {"ICICIBANK", "NIFTY", "RELIANCE", "TCS"},
    )
    _check(
        "mixed outcomes",
        {o.outcome_status for o in outs}
        == {OutcomeStatus.TARGET_HIT, OutcomeStatus.STOP_HIT, OutcomeStatus.EXPIRED},
    )

    # 10. Point-in-time protection
    _banner("10. Point-in-time protection")
    subj = _long()
    o_expired = ev.evaluate(subj, _future([101, 102, 103]))
    o_hit = ev.evaluate(subj, _future([101, 102, 112]))
    _check(
        "future mutation changes outcome",
        o_expired.outcome_status != o_hit.outcome_status,
    )
    _check(
        "decision at T unchanged (subject not mutated)",
        subj.entry == 100.0 and subj.direction == "LONG",
    )
    # Forward-only: include the at-T candle; it must not count.
    at_t = _candle(100.0, _EPOCH)
    o_fwd = ev.evaluate(subj, [at_t, *_future([112])])
    _check("at-T candle excluded (bars_held == 1)", o_fwd.bars_held == 1)
    _check("forward-only target hit", o_fwd.outcome_status == OutcomeStatus.TARGET_HIT)

    # 11. Future mutation outside horizon
    _banner("11. Future mutation outside horizon")
    ev_h = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
    base = _future([101, 102, 103, 112, 94])
    mutated = _future([101, 102, 103, 200, 1])
    o1 = ev_h.evaluate(subj, base)
    o2 = ev_h.evaluate(subj, mutated)
    _check("outcome unchanged by post-horizon mutation", o1 == o2)

    # 12. Deterministic repeated evaluation
    _banner("12. Deterministic repeated evaluation")
    fut = _future([105, 108, 112])
    o_a = ev.evaluate(subj, fut)
    o_b = ev.evaluate(subj, fut)
    _check("repeated evaluation identical", o_a == o_b)

    # 13. Serialization round trip
    _banner("13. Serialization round trip")
    payload = serialize_outcome(o_a)
    o_back = deserialize_outcome(payload)
    _check("outcome status preserved", o_back.outcome_status == o_a.outcome_status)
    _check("exit preserved", o_back.exit_price == o_a.exit_price)
    _check("R preserved", o_back.realized_r == o_a.realized_r)
    _check("MFE preserved", o_back.mfe == o_a.mfe)
    _check("instrument preserved", o_back.subject.instrument == o_a.subject.instrument)
    _check("direction preserved", o_back.subject.direction == o_a.subject.direction)
    _check("timestamp preserved", o_back.subject.evaluation_timestamp == o_a.subject.evaluation_timestamp)

    # 14. Existing pipeline regression
    _banner("14. Existing pipeline regression")
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    print(
        f"  Pipeline: signals_generated={result.signals_generated} "
        f"completed_trades={result.performance.completed_trades}",
    )
    _check("pipeline still produces signals", result.signals_generated > 0)
    _check(
        "Sprint 11V baseline (signals=4, trades=3)",
        result.signals_generated == 4 and result.performance.completed_trades == 3,
    )

    # Full replay integration report
    _banner("Full replay outcome report (Sprint 11V -> 11W integration)")
    adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
    dataset = adapter.normalize(historical_records())
    times = evaluation_times_from_setup_candles(dataset, "15M", count=4, min_history=10)
    replay = HistoricalReplayEngine(MarketScanConfig()).replay(dataset, times)
    engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
    report = engine.evaluate_replay(replay, dataset)
    print(fmt.format_report(report))
    _check("report has outcomes", report.outcome_count > 0)

    # Report serialization round trip
    rback = deserialize_outcome_report(serialize_outcome_report(report))
    _check(
        "report round trip preserves id + count",
        rback.report_id == report.report_id
        and rback.outcome_count == report.outcome_count,
    )

    print()
    print("Sprint 11W demo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
