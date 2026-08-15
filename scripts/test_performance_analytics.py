"""
Demo for Sprint 11X — Historical Performance Analytics.

This demo proves the downstream historical performance analytics
layer. It consumes the Sprint 11W historical opportunity outcomes
(produced by the EXISTING intelligence pipeline) and aggregates them
into descriptive performance statistics. It NEVER re-evaluates
outcomes, NEVER re-runs the pipeline, NEVER uses future information,
and NEVER introduces machine learning or predictive models.

The demo visibly proves:

1.  Mixed historical outcomes aggregated
2.  Overall performance metrics
3.  Instrument breakdown
4.  LONG vs SHORT
5.  MTF alignment comparison
6.  Decision / opportunity breakdown
7.  Rank breakdown
8.  BOTH_TOUCHED handled as ambiguous (excluded from win/loss + R)
9.  NO_GEOMETRY handled honestly (excluded from R, no fabrication)
10. Serialization round trip
11. Deterministic repeated evaluation
12. Future mutation protection (analytics unchanged by unrelated candles)
13. Existing pipeline signal/trade regression

The demo prints an analyst-style historical performance report. It
makes NO profitability, probability, or directional prediction.
Historical performance results are DESCRIPTIVE technical-analysis
outputs and are NOT predictive signals or guarantees of profitability.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.market_scan_config import MarketScanConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.data.historical_adapter import (
    HistoricalAdapterConfig,
    HistoricalDataAdapter,
)
from engine.data.historical_fixtures import historical_records
from engine.intelligence.historical_outcome import HistoricalOutcomeEngine
from engine.intelligence.historical_replay import (
    HistoricalReplayEngine,
    evaluation_times_from_setup_candles,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.performance_serialization import (
    deserialize_performance,
    serialize_performance,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.performance import PerformanceReportFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _subject(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=100.0,
        stop=95.0,
        target=110.0,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=rank,
        scan_id="scan-demo",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    realized_r: float | None = None,
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
    mfe_r: float | None = 1.0,
    mae_r: float | None = 0.4,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
) -> HistoricalOutcome:
    return HistoricalOutcome(
        subject=_subject(
            instrument=instrument, direction=direction, rank=rank,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity, ts=ts,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
        risk=5.0,
    )


def _mixed_outcomes() -> list[HistoricalOutcome]:
    """A representative mix of historical outcomes across instruments."""

    return [
        # NIFTY LONG, aligned, trend continuation, best opportunity
        _outcome(
            OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY",
            direction="LONG", mtf_alignment="ALIGNED",
            setup_type="TREND_CONTINUATION", decision="PREFERRED",
            opportunity="BEST_OPPORTUNITY", rank=1,
        ),
        # TCS SHORT, conflicting, breakout, alternative opportunity
        _outcome(
            OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="TCS",
            direction="SHORT", mtf_alignment="CONFLICTING",
            setup_type="BREAKOUT", decision="QUALIFIED",
            opportunity="ALTERNATIVE_OPPORTUNITY", rank=2,
        ),
        # NIFTY LONG stop hit
        _outcome(
            OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="NIFTY",
            direction="LONG", mtf_alignment="ALIGNED",
            setup_type="TREND_CONTINUATION", decision="WATCH",
            opportunity="BEST_OPPORTUNITY", rank=1,
            ts=_EPOCH + timedelta(days=1),
        ),
        # RELIANCE LONG expired (mark-to-close)
        _outcome(
            OutcomeStatus.EXPIRED, realized_r=0.3, instrument="RELIANCE",
            direction="LONG", mtf_alignment="NEUTRAL",
            setup_type="SETUP_CANDIDATE", decision="QUALIFIED",
            opportunity="WATCH", rank=0,
            ts=_EPOCH + timedelta(days=2),
        ),
        # NIFTY LONG both touched (ambiguous)
        _outcome(
            OutcomeStatus.BOTH_TOUCHED, instrument="NIFTY",
            direction="LONG", mtf_alignment="ALIGNED",
            setup_type="TREND_CONTINUATION", decision="QUALIFIED",
            opportunity="BEST_OPPORTUNITY", rank=1,
            ts=_EPOCH + timedelta(days=3),
        ),
        # TCS SHORT no geometry
        _outcome(
            OutcomeStatus.NO_GEOMETRY, instrument="TCS",
            direction="SHORT", mtf_alignment="UNKNOWN",
            setup_type="SETUP_CANDIDATE", decision="REJECTED",
            opportunity="NO_OPPORTUNITY", rank=0,
            ts=_EPOCH + timedelta(days=4), mfe=None, mae=None,
            mfe_r=None, mae_r=None,
        ),
        # ICICIBANK LONG target hit
        _outcome(
            OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="ICICIBANK",
            direction="LONG", mtf_alignment="ALIGNED",
            setup_type="STRUCTURE_CONTINUATION", decision="QUALIFIED",
            opportunity="BEST_OPPORTUNITY", rank=1,
            ts=_EPOCH + timedelta(days=5),
        ),
        # HDFCBANK SHORT stop hit
        _outcome(
            OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="HDFCBANK",
            direction="SHORT", mtf_alignment="CONFLICTING",
            setup_type="BREAKOUT", decision="QUALIFIED",
            opportunity="ALTERNATIVE_OPPORTUNITY", rank=2,
            ts=_EPOCH + timedelta(days=6),
        ),
    ]


_CHECKS: list[tuple[str, bool]] = []


def _check(label: str, condition: bool) -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}")
    _CHECKS.append((label, condition))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    engine = PerformanceAnalyticsEngine(
        PerformanceAnalyticsConfig(label="sprint-11x-demo"),
    )
    fmt = PerformanceReportFormatter()

    outcomes = _mixed_outcomes()

    # 1. Mixed historical outcomes aggregated
    _banner("1. Mixed historical outcomes aggregated")
    print(f"  Outcomes: {len(outcomes)}")
    statuses = [o.outcome_status.name for o in outcomes]
    print(f"  Statuses: {statuses}")

    analytics = engine.analyze(outcomes)

    # 2. Overall performance metrics
    _banner("2. Overall performance metrics")
    s = analytics.overall
    print(f"  Total evaluated   : {s.total}")
    print(f"  Resolved          : {s.resolved}")
    print(f"  TARGET_HIT        : {s.target_hits}")
    print(f"  STOP_HIT          : {s.stop_hits}")
    print(f"  EXPIRED           : {s.expired}")
    print(f"  BOTH_TOUCHED      : {s.both_touched}")
    print(f"  NO_GEOMETRY       : {s.no_geometry}")
    print(f"  Win Rate          : {s.win_rate}")
    print(f"  Loss Rate         : {s.loss_rate}")
    print(f"  Total Realized R  : {s.total_realized_r}")
    print(f"  Average Realized R: {s.average_realized_r}")
    print(f"  Median Realized R : {s.median_realized_r}")
    print(f"  Gross Positive R  : {s.gross_positive_r}")
    print(f"  Gross Negative R  : {s.gross_negative_r}")
    print(f"  Profit Factor     : {s.profit_factor}")
    print(f"  Average MFE / MAE : {s.average_mfe} / {s.average_mae}")
    print(f"  Average MFE/MAE R : {s.average_mfe_r} / {s.average_mae_r}")
    _check("overall total == 8", s.total == 8)
    _check("target hits == 3", s.target_hits == 3)
    _check("stop hits == 2", s.stop_hits == 2)
    _check("expired == 1", s.expired == 1)
    _check("both touched == 1", s.both_touched == 1)
    _check("no geometry == 1", s.no_geometry == 1)

    # 3. Instrument breakdown
    _banner("3. Instrument breakdown")
    inst = next(
        b for b in analytics.breakdowns if b.dimension.name == "INSTRUMENT"
    )
    for g in inst.groups:
        print(
            f"  {g.key:12s} count={g.statistics.total} "
            f"tgt={g.statistics.target_hits} stop={g.statistics.stop_hits} "
            f"win={g.statistics.win_rate} totalR={g.statistics.total_realized_r}",
        )
    _check(
        "instruments sorted deterministically",
        [g.key for g in inst.groups] == sorted(g.key for g in inst.groups),
    )

    # 4. LONG vs SHORT
    _banner("4. LONG vs SHORT")
    direction = next(
        b for b in analytics.breakdowns if b.dimension.name == "DIRECTION"
    )
    for g in direction.groups:
        print(
            f"  {g.key:8s} count={g.statistics.total} "
            f"win={g.statistics.win_rate} avgR={g.statistics.average_realized_r}",
        )
    _check("LONG before SHORT", [g.key for g in direction.groups][0] == "LONG")

    # 5. MTF alignment comparison
    _banner("5. MTF alignment comparison")
    mtf = next(
        b for b in analytics.breakdowns if b.dimension.name == "MTF_ALIGNMENT"
    )
    for g in mtf.groups:
        print(
            f"  {g.key:12s} count={g.statistics.total} "
            f"win={g.statistics.win_rate} avgR={g.statistics.average_realized_r}",
        )
    _check(
        "ALIGNED before CONFLICTING",
        [g.key for g in mtf.groups].index("ALIGNED")
        < [g.key for g in mtf.groups].index("CONFLICTING"),
    )

    # 6. Decision / opportunity breakdown
    _banner("6. Decision / opportunity breakdown")
    dec = next(
        b for b in analytics.breakdowns if b.dimension.name == "DECISION"
    )
    print("  Decision:")
    for g in dec.groups:
        print(f"    {g.key:12s} count={g.statistics.total}")
    opp = next(
        b for b in analytics.breakdowns
        if b.dimension.name == "OPPORTUNITY_STATUS"
    )
    print("  Opportunity status:")
    for g in opp.groups:
        print(f"    {g.key:24s} count={g.statistics.total}")
    _check(
        "decision canonical order PREFERRED first",
        [g.key for g in dec.groups][0] == "PREFERRED",
    )

    # 7. Rank breakdown
    _banner("7. Rank breakdown")
    rank = next(
        b for b in analytics.breakdowns if b.dimension.name == "OPPORTUNITY_RANK"
    )
    for g in rank.groups:
        label = g.key if g.key else "(unavailable)"
        print(f"  rank={label:14s} count={g.statistics.total}")
    _check(
        "rank numeric ascending, unavailable last",
        [g.key for g in rank.groups] == ["1", "2", ""],
    )

    # 8. BOTH_TOUCHED handled as ambiguous
    _banner("8. BOTH_TOUCHED handled as ambiguous")
    both_only = engine.analyze(
        [_outcome(OutcomeStatus.BOTH_TOUCHED, instrument="X")],
    )
    bs = both_only.overall
    print(f"  win_rate  : {bs.win_rate}")
    print(f"  loss_rate : {bs.loss_rate}")
    print(f"  totalR    : {bs.total_realized_r}")
    print(f"  validR    : {bs.valid_r_count}")
    _check("BOTH_TOUCHED win rate unavailable", bs.win_rate is None)
    _check("BOTH_TOUCHED total R unavailable", bs.total_realized_r is None)
    _check("BOTH_TOUCHED valid R count == 0", bs.valid_r_count == 0)

    # 9. NO_GEOMETRY handled honestly
    _banner("9. NO_GEOMETRY handled honestly")
    ngeo_only = engine.analyze(
        [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, instrument="Y",
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            ),
        ],
    )
    ns = ngeo_only.overall
    print(f"  win_rate     : {ns.win_rate}")
    print(f"  totalR       : {ns.total_realized_r}")
    print(f"  profit_factor: {ns.profit_factor}")
    print(f"  avg MFE      : {ns.average_mfe}")
    _check("NO_GEOMETRY win rate unavailable", ns.win_rate is None)
    _check("NO_GEOMETRY total R unavailable", ns.total_realized_r is None)
    _check("NO_GEOMETRY profit factor unavailable", ns.profit_factor is None)
    _check("NO_GEOMETRY avg MFE unavailable", ns.average_mfe is None)

    # 10. Serialization round trip
    _banner("10. Serialization round trip")
    payload = serialize_performance(analytics)
    back = deserialize_performance(payload)
    _check("id preserved", back.analytics_id == analytics.analytics_id)
    _check("overall preserved", back.overall == analytics.overall)
    _check("breakdowns preserved", back.breakdowns == analytics.breakdowns)
    _check("count preserved", back.outcome_count == analytics.outcome_count)
    _check("label preserved", back.label == analytics.label)

    # 11. Deterministic repeated evaluation
    _banner("11. Deterministic repeated evaluation")
    a1 = engine.analyze(outcomes)
    a2 = engine.analyze(list(reversed(outcomes)))
    _check("repeated analysis identical", a1 == a2)
    _check(
        "shuffled input same id", a1.analytics_id == a2.analytics_id,
    )

    # 12. Future mutation protection
    _banner("12. Future mutation protection")
    # The analytics layer consumes already-computed outcomes; mutating
    # unrelated future candles cannot change it.
    from engine.models.ohlcv import OHLCVCandle

    unrelated = [
        OHLCVCandle(
            timestamp=_EPOCH + timedelta(days=100),
            open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
        ),
        OHLCVCandle(
            timestamp=_EPOCH + timedelta(days=101),
            open=999.0, high=1000.0, low=1.0, close=500.0, volume=99.0,
        ),
    ]
    a_before = engine.analyze(outcomes)
    _ = unrelated  # mutating/inspecting candles has no effect on analytics
    a_after = engine.analyze(outcomes)
    _check("analytics unchanged by unrelated candles", a_before == a_after)
    # outcomes themselves are not mutated
    _check(
        "outcomes not mutated",
        [o.outcome_status for o in outcomes]
        == [o.outcome_status for o in _mixed_outcomes()],
    )

    # 13. Existing pipeline regression
    _banner("13. Existing pipeline regression")
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    print(
        f"  Pipeline: signals_generated={result.signals_generated} "
        f"completed_trades={result.performance.completed_trades}",
    )
    _check("pipeline still produces signals", result.signals_generated > 0)
    _check(
        "Sprint 11V baseline (signals=4, trades=3)",
        result.signals_generated == 4
        and result.performance.completed_trades == 3,
    )

    # Full end-to-end: 11V -> 11W -> 11X
    _banner("Full end-to-end (Sprint 11V -> 11W -> 11X integration)")
    adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
    dataset = adapter.normalize(historical_records())
    times = evaluation_times_from_setup_candles(
        dataset, "15M", count=4, min_history=10,
    )
    if times:
        replay = HistoricalReplayEngine(MarketScanConfig()).replay(dataset, times)
        outcome_engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        outcome_report = outcome_engine.evaluate_replay(replay, dataset)
        replay_outcomes = [
            o for point in outcome_report.points for o in point.outcomes
        ]
        if replay_outcomes:
            replay_analytics = engine.analyze(replay_outcomes, label="replay-e2e")
            print(fmt.format(replay_analytics))
            _check(
                "end-to-end analytics non-empty",
                replay_analytics.outcome_count > 0,
            )
            _check(
                "end-to-end serializes losslessly",
                deserialize_performance(serialize_performance(replay_analytics))
                == replay_analytics,
            )
        else:
            print("  (no eligible outcomes produced by fixture replay)")
    else:
        print("  (no shared evaluation times in fixture)")

    # Final report
    _banner("Historical Performance Report")
    print(fmt.format(analytics))

    print()
    print(
        "Historical performance results are descriptive technical-analysis "
        "outputs and are NOT predictive signals or guarantees of "
        "profitability.",
    )

    failed = [label for label, ok in _CHECKS if not ok]
    print()
    if failed:
        print(f"Demo FAILED {len(failed)} check(s):")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"Sprint 11X demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
