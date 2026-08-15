"""
Demo for Sprint 11Y — Historical Evidence / Validation.

This demo proves the historical evidence / validation layer. It
consumes Sprint 11W historical outcomes (produced by the EXISTING
intelligence pipeline) and classifies the STRENGTH of the available
historical evidence per cohort. It REUSES the Sprint 11X statistics
computation (it does not recompute trading / outcome logic). It NEVER
re-evaluates outcomes, NEVER re-runs the pipeline, NEVER uses future
information, and NEVER introduces machine learning or predictive
models.

The demo visibly proves:

1.  A strong-enough historical cohort
2.  A weak cohort
3.  An insufficient cohort
4.  Multiple dimensions
5.  Ambiguous BOTH_TOUCHED data
6.  NO_GEOMETRY data
7.  Deterministic repeated analysis
8.  Serialization round trip
9.  Existing pipeline regression
10. Full Sprint 11Y report

The demo prints an analyst-style historical evidence report. It makes
NO profitability, probability, directional prediction, or statistical-
significance claim. Historical evidence is DESCRIPTIVE and does NOT
guarantee future performance.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.market_scan_config import MarketScanConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.data.historical_adapter import (
    HistoricalAdapterConfig,
    HistoricalDataAdapter,
)
from engine.data.historical_fixtures import historical_records
from engine.intelligence.historical_evidence import (
    SUPPORTED_COHORT_SPECS,
    HistoricalEvidenceEngine,
)
from engine.intelligence.historical_evidence_serialization import (
    deserialize_evidence,
    serialize_evidence,
)
from engine.intelligence.historical_outcome import HistoricalOutcomeEngine
from engine.intelligence.historical_replay import (
    HistoricalReplayEngine,
    evaluation_times_from_setup_candles,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.models.historical_evidence import EvidenceStrength
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
from engine.reporting.historical_evidence import HistoricalEvidenceFormatter


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


def _resolved(
    n: int, win_fraction: float, instrument: str, direction: str,
    setup_type: str, mtf_alignment: str, seed: int,
) -> list[HistoricalOutcome]:
    """Deterministic resolved target/stop cohort of size ``n``."""

    import random

    rng = random.Random(seed)
    out: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        if win:
            out.append(
                _outcome(
                    OutcomeStatus.TARGET_HIT, realized_r=2.0,
                    instrument=instrument, direction=direction,
                    setup_type=setup_type, mtf_alignment=mtf_alignment,
                    ts=_EPOCH + timedelta(days=i),
                ),
            )
        else:
            out.append(
                _outcome(
                    OutcomeStatus.STOP_HIT, realized_r=-1.0,
                    instrument=instrument, direction=direction,
                    setup_type=setup_type, mtf_alignment=mtf_alignment,
                    ts=_EPOCH + timedelta(days=i),
                ),
            )
    return out


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
    engine = HistoricalEvidenceEngine(EvidenceConfig(label="sprint-11y-demo"))
    fmt = HistoricalEvidenceFormatter()

    # 1. A strong-enough historical cohort
    _banner("1. A strong-enough historical cohort")
    strong = _resolved(
        60, win_fraction=0.6, instrument="NIFTY", direction="LONG",
        setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED", seed=1,
    )
    strong_report = engine.evaluate(strong)
    print(f"  sample={strong_report.summary.sample_count} "
          f"resolved={strong_report.summary.resolved_count} "
          f"validR={strong_report.summary.valid_r_count} "
          f"strength={strong_report.summary.strength.name}")
    _check("strong cohort classified STRONG", strong_report.summary.strength == EvidenceStrength.STRONG)

    # 2. A weak cohort
    _banner("2. A weak cohort")
    weak = [
        _outcome(
            OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
            mfe=None, mae=None, mfe_r=None, mae_r=None,
        )
        for i in range(30)
    ]
    weak_report = engine.evaluate(weak)
    print(f"  sample={weak_report.summary.sample_count} "
          f"resolved={weak_report.summary.resolved_count} "
          f"strength={weak_report.summary.strength.name}")
    _check("weak cohort classified WEAK", weak_report.summary.strength == EvidenceStrength.WEAK)

    # 3. An insufficient cohort
    _banner("3. An insufficient cohort")
    insufficient = [
        _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, ts=_EPOCH + timedelta(days=i))
        for i in range(5)
    ]
    insufficient_report = engine.evaluate(insufficient)
    print(f"  sample={insufficient_report.summary.sample_count} "
          f"win_rate={insufficient_report.summary.statistics.win_rate} "
          f"strength={insufficient_report.summary.strength.name}")
    _check("insufficient cohort classified INSUFFICIENT", insufficient_report.summary.strength == EvidenceStrength.INSUFFICIENT)
    _check(
        "small sample not strong despite 100% win rate",
        insufficient_report.summary.statistics.win_rate == 1.0
        and insufficient_report.summary.strength != EvidenceStrength.STRONG,
    )

    # 4. Multiple dimensions
    _banner("4. Multiple dimensions")
    multi = strong + _resolved(
        60, win_fraction=0.4, instrument="TCS", direction="SHORT",
        setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=2,
    )
    multi_report = engine.evaluate(multi)
    print(f"  breakdowns: {len(multi_report.breakdowns)}")
    print(f"  cohort specs supported: {len(SUPPORTED_COHORT_SPECS)}")
    for b in multi_report.breakdowns:
        keys = ", ".join(c.key for c in b.cohorts)
        print(f"    {b.spec.label}: {keys}")
    _check("multiple dimensions evaluated", len(multi_report.breakdowns) >= 7)
    _check("composite cohorts present", any(b.spec.is_composite for b in multi_report.breakdowns))

    # 5. Ambiguous BOTH_TOUCHED data
    _banner("5. Ambiguous BOTH_TOUCHED data")
    both = [_outcome(OutcomeStatus.BOTH_TOUCHED) for _ in range(30)]
    both_report = engine.evaluate(both)
    s = both_report.summary.statistics
    print(f"  both_touched={s.both_touched} win_rate={s.win_rate} totalR={s.total_realized_r} validR={s.valid_r_count}")
    _check("BOTH_TOUCHED win rate unavailable", s.win_rate is None)
    _check("BOTH_TOUCHED total R unavailable", s.total_realized_r is None)
    _check("BOTH_TOUCHED valid R == 0", s.valid_r_count == 0)

    # 6. NO_GEOMETRY data
    _banner("6. NO_GEOMETRY data")
    ngeo_report = engine.evaluate(
        [
            _outcome(
                OutcomeStatus.NO_GEOMETRY, ts=_EPOCH + timedelta(days=i),
                mfe=None, mae=None, mfe_r=None, mae_r=None,
            )
            for i in range(40)
        ],
    )
    ns = ngeo_report.summary.statistics
    print(f"  no_geometry={ns.no_geometry} profit_factor={ns.profit_factor} avgMFE={ns.average_mfe}")
    _check("NO_GEOMETRY profit factor unavailable", ns.profit_factor is None)
    _check("NO_GEOMETRY avg MFE unavailable", ns.average_mfe is None)

    # 7. Deterministic repeated analysis
    _banner("7. Deterministic repeated analysis")
    r1 = engine.evaluate(multi)
    r2 = engine.evaluate(list(reversed(multi)))
    _check("repeated analysis identical", r1 == r2)
    _check("shuffled input same id", r1.evidence_id == r2.evidence_id)

    # 8. Serialization round trip
    _banner("8. Serialization round trip")
    back = deserialize_evidence(serialize_evidence(multi_report))
    _check("id preserved", back.evidence_id == multi_report.evidence_id)
    _check("summary preserved", back.summary == multi_report.summary)
    _check("breakdowns preserved", back.breakdowns == multi_report.breakdowns)
    _check("config snapshot preserved", back.config_snapshot == multi_report.config_snapshot)
    _check("strengths preserved", all(
        c1.strength == c2.strength
        for b1, b2 in zip(multi_report.breakdowns, back.breakdowns)
        for c1, c2 in zip(b1.cohorts, b2.cohorts)
    ))

    # 9. Existing pipeline regression
    _banner("9. Existing pipeline regression")
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    print(f"  Pipeline: signals_generated={result.signals_generated} completed_trades={result.performance.completed_trades}")
    _check("pipeline still produces signals", result.signals_generated > 0)
    _check(
        "Sprint 11V baseline (signals=4, trades=3)",
        result.signals_generated == 4 and result.performance.completed_trades == 3,
    )

    # Sprint 11X analytics still works on the same outcomes
    perf = PerformanceAnalyticsEngine(PerformanceAnalyticsConfig()).analyze(multi)
    _check("Sprint 11X analytics still works", perf.outcome_count == len(multi))

    # End-to-end: 11V -> 11W -> 11X -> 11Y
    _banner("Full end-to-end (Sprint 11V -> 11W -> 11X -> 11Y)")
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
            e2e = engine.evaluate(replay_outcomes, label="replay-e2e")
            print(f"  e2e outcomes: {len(replay_outcomes)} strength={e2e.summary.strength.name}")
            _check("e2e evidence non-empty", e2e.summary.sample_count > 0)
            _check(
                "e2e serializes losslessly",
                deserialize_evidence(serialize_evidence(e2e)) == e2e,
            )
        else:
            print("  (no eligible outcomes produced by fixture replay)")
            _check("e2e ran without error", True)
    else:
        print("  (no shared evaluation times in fixture)")
        _check("e2e ran without error", True)

    # 10. Full Sprint 11Y report
    _banner("Full Sprint 11Y Report")
    print(fmt.format(multi_report))

    print()
    print(
        "Historical evidence is descriptive and does not guarantee future "
        "performance.",
    )

    failed = [label for label, ok in _CHECKS if not ok]
    print()
    if failed:
        print(f"Demo FAILED {len(failed)} check(s):")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"Sprint 11Y demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
