"""
Demo for Sprint 12A — Decision Intelligence Foundation.

This demo proves the decision intelligence foundation layer. It
consumes the ALREADY-COMPUTED Sprint 11Z opportunity evidence lookup
(itself built from already-computed Sprint 11X performance statistics
and Sprint 11Y evidence-strength classifications) and produces a
structured, explainable, decision-process-facing DECISION INTELLIGENCE
context.

The demo REUSES the Sprint 11X statistics, the Sprint 11Y evidence
classification and the Sprint 11Z strategy interpretation (it does NOT
recompute them). It NEVER re-evaluates outcomes, NEVER re-runs the
pipeline, NEVER uses future information, NEVER introduces machine
learning or predictive models, NEVER generates trading signals, and
NEVER modifies the existing decision / scoring logic.

The demo visibly proves:

1. Current opportunity with supportive historical evidence
2. Current opportunity with insufficient evidence
3. Current opportunity with unavailable / missing cohort
4. Evidence-aware decision context
5. Rationale generation
6. Serialization round trip
7. Determinism
8. No-look-ahead
9. Existing pipeline regression

The demo prints an analyst-style decision intelligence report. It
makes NO profitability, probability, directional prediction,
statistical-significance, buy / sell / enter / exit / hold, or
trading-recommendation claim. Historical evidence is DESCRIPTIVE and
does NOT guarantee future performance.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_evidence_config import EvidenceConfig
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_serialization import (
    deserialize_context,
    serialize_context,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionEvidenceFactor,
    ExistingDecisionSummary,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.decision_intelligence import (
    DecisionIntelligenceFormatter,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _subject(
    instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION",
    mtf_alignment="ALIGNED", ts=_EPOCH,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=100.0, stop=95.0, target=110.0,
        decision_classification="QUALIFIED",
        decision_score=70,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1, scan_id="scan-12a", setup_timeframe="15M",
        setup_type=setup_type, mtf_alignment=mtf_alignment,
    )


def _resolved(n, win_fraction, instrument, direction, setup_type, mtf_alignment, seed):
    import random
    rng = random.Random(seed)
    out = []
    for i in range(n):
        win = rng.random() < win_fraction
        out.append(
            HistoricalOutcome(
                subject=_subject(
                    instrument=instrument, direction=direction,
                    setup_type=setup_type, mtf_alignment=mtf_alignment,
                    ts=_EPOCH + timedelta(days=i),
                ),
                outcome_status=(
                    OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT
                ),
                realized_r=2.0 if win else -1.0,
                mfe=5.0, mae=2.0, mfe_r=1.0, mae_r=0.4, risk=5.0,
            ),
        )
    return out


def _decision(direction="LONG", score=75) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification="QUALIFIED",
        decision_score=score,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1,
        geometry_complete=True,
        confluence_score=4,
        risk_reward_ratio=2.0,
        entry=100.0, stop=95.0, target=110.0,
    )


def _profile(
    instrument="NIFTY", direction="LONG",
    setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
) -> OpportunityProfile:
    return OpportunityProfile(
        instrument=instrument, direction=direction,
        setup_type=setup_type, mtf_alignment=mtf_alignment,
    )


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
    fmt = DecisionIntelligenceFormatter()

    # Build a historical outcome corpus with contrasting cohorts.
    long_trend = _resolved(
        60, win_fraction=0.6, instrument="NIFTY", direction="LONG",
        setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED", seed=1,
    )
    short_breakout = _resolved(
        60, win_fraction=0.4, instrument="TCS", direction="SHORT",
        setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=2,
    )
    insufficient = _resolved(
        5, win_fraction=1.0, instrument="HDFCBANK", direction="LONG",
        setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED", seed=3,
    )
    all_outcomes = long_trend + short_breakout + insufficient
    evidence = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-12a-demo"),
    ).evaluate(all_outcomes)

    strat = StrategyIntelligenceEngine()
    eng = DecisionIntelligenceEngine()

    # 1. Current opportunity with supportive historical evidence.
    _banner("1. Current opportunity with supportive historical evidence")
    profile_supported = OpportunityProfile(
        instrument="NIFTY", direction="LONG",
        setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
    )
    lookup_supported = strat.lookup(evidence, profile_supported)
    ctx_supported = eng.build(
        profile_supported, _decision(), lookup_supported, label="supported",
    )
    print(
        f"  match={ctx_supported.matched} "
        f"strength={ctx_supported.evidence_strength.name} "
        f"status={ctx_supported.decision_context_status.name} "
        f"win_rate={ctx_supported.observed_performance.win_rate:.2f}",
    )
    _check(
        "supportive context classified EVIDENCE_SUPPORTED",
        ctx_supported.decision_context_status
        == DecisionContextStatus.EVIDENCE_SUPPORTED,
    )
    _check(
        "supportive context has HISTORICAL_SUPPORT_PRESENT factor",
        DecisionEvidenceFactor.HISTORICAL_SUPPORT_PRESENT
        in [f.factor for f in ctx_supported.factors],
    )
    _check(
        "supportive context has evidence",
        ctx_supported.has_evidence and ctx_supported.evidence_supported,
    )
    print()
    print(fmt.format(ctx_supported))

    # 2. Current opportunity with insufficient evidence.
    _banner("2. Current opportunity with insufficient evidence")
    profile_insuf = OpportunityProfile(
        instrument="HDFCBANK", direction="LONG",
        setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED",
    )
    lookup_insuf = strat.lookup(evidence, profile_insuf)
    ctx_insuf = eng.build(profile_insuf, _decision(), lookup_insuf, label="insufficient")
    print(
        f"  match={ctx_insuf.matched} "
        f"strength={ctx_insuf.evidence_strength.name} "
        f"status={ctx_insuf.decision_context_status.name} "
        f"win_rate={ctx_insuf.observed_performance.win_rate}",
    )
    _check(
        "insufficient context classified INSUFFICIENT_EVIDENCE",
        ctx_insuf.decision_context_status
        == DecisionContextStatus.INSUFFICIENT_EVIDENCE,
    )
    _check(
        "small sample not supported despite 100% win rate",
        ctx_insuf.observed_performance.win_rate == 1.0
        and ctx_insuf.decision_context_status
        != DecisionContextStatus.EVIDENCE_SUPPORTED,
    )
    _check(
        "insufficient context has INSUFFICIENT_HISTORICAL_EVIDENCE factor",
        DecisionEvidenceFactor.INSUFFICIENT_HISTORICAL_EVIDENCE
        in [f.factor for f in ctx_insuf.factors],
    )

    # 3. Current opportunity with unavailable / missing cohort.
    _banner("3. Current opportunity with unavailable / missing cohort")
    profile_nomatch = OpportunityProfile(setup_type="RANGE_REJECTION")
    lookup_nomatch = strat.lookup(evidence, profile_nomatch)
    ctx_nomatch = eng.build(
        profile_nomatch, _decision(), lookup_nomatch, label="no-match",
    )
    print(
        f"  match={ctx_nomatch.matched} "
        f"status={ctx_nomatch.decision_context_status.name} "
        f"observed={ctx_nomatch.observed_performance} "
        f"strength={ctx_nomatch.evidence_strength}",
    )
    _check(
        "no-match context classified EVIDENCE_UNAVAILABLE",
        ctx_nomatch.decision_context_status
        == DecisionContextStatus.EVIDENCE_UNAVAILABLE,
    )
    _check(
        "no-match context has no fabricated metrics",
        ctx_nomatch.observed_performance is None
        and ctx_nomatch.evidence_strength is None
        and ctx_nomatch.strategy_interpretation is None,
    )
    _check(
        "no-match context has EVIDENCE_UNAVAILABLE factor",
        DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE
        in [f.factor for f in ctx_nomatch.factors],
    )
    print()
    print(fmt.format(ctx_nomatch))

    # 4. Evidence-aware decision context (limited / weak evidence).
    _banner("4. Evidence-aware decision context (limited evidence)")
    # Build a WEAK cohort: total >= 30 but resolved < 10.
    weak_outcomes = []
    for i in range(5):
        weak_outcomes.append(
            HistoricalOutcome(
                subject=_subject(ts=_EPOCH + timedelta(days=i)),
                outcome_status=OutcomeStatus.TARGET_HIT,
                realized_r=2.0, mfe=5.0, mae=2.0, mfe_r=1.0, mae_r=0.4, risk=5.0,
            ),
        )
    for i in range(5, 35):
        weak_outcomes.append(
            HistoricalOutcome(
                subject=_subject(ts=_EPOCH + timedelta(days=i)),
                outcome_status=OutcomeStatus.BOTH_TOUCHED,
                realized_r=None, mfe=5.0, mae=2.0, mfe_r=1.0, mae_r=0.4, risk=5.0,
            ),
        )
    weak_evidence = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-12a-demo-weak"),
    ).evaluate(weak_outcomes)
    weak_lookup = strat.lookup(weak_evidence, _profile())
    ctx_weak = eng.build(_profile(), _decision(), weak_lookup, label="weak")
    print(
        f"  match={ctx_weak.matched} "
        f"strength={ctx_weak.evidence_strength.name} "
        f"status={ctx_weak.decision_context_status.name}",
    )
    _check(
        "weak context classified EVIDENCE_LIMITED",
        ctx_weak.decision_context_status
        == DecisionContextStatus.EVIDENCE_LIMITED,
    )
    _check(
        "weak context has HISTORICAL_CAUTION_PRESENT factor",
        DecisionEvidenceFactor.HISTORICAL_CAUTION_PRESENT
        in [f.factor for f in ctx_weak.factors],
    )
    _check(
        "weak context evidence available but not supported",
        ctx_weak.has_evidence and not ctx_weak.evidence_supported,
    )

    # 5. Rationale generation.
    _banner("5. Rationale generation")
    print("Supported rationale:")
    print(f"  {ctx_supported.rationale}")
    print()
    print("Insufficient rationale:")
    print(f"  {ctx_insuf.rationale}")
    print()
    print("No-match rationale:")
    print(f"  {ctx_nomatch.rationale}")
    _check(
        "rationale mentions decision context status",
        ctx_supported.decision_context_status.name in ctx_supported.rationale,
    )
    _check(
        "rationale states existing decision not modified",
        "not modified" in ctx_supported.rationale.lower()
        or "without alteration" in ctx_supported.rationale.lower(),
    )
    _check(
        "rationale has no predictive language",
        all(
            term not in ctx_supported.rationale.lower()
            for term in ("will win", "guaranteed", "high probability", "statistically significant")
        ),
    )

    # 6. Serialization round trip.
    _banner("6. Serialization round trip")
    for label, ctx in [
        ("supported", ctx_supported),
        ("insufficient", ctx_insuf),
        ("no-match", ctx_nomatch),
        ("weak", ctx_weak),
    ]:
        rt = deserialize_context(serialize_context(ctx))
        ok = rt == ctx and rt.context_id == ctx.context_id
        _check(f"{label} context round trip lossless", ok)

    # 7. Determinism.
    _banner("7. Determinism")
    ctx_again = eng.build(
        profile_supported, _decision(), lookup_supported, label="supported",
    )
    _check("repeated build identical", ctx_again == ctx_supported)
    _check(
        "repeated build same id",
        ctx_again.context_id == ctx_supported.context_id,
    )
    # Shuffled-equivalent outcomes produce the same context id.
    evidence_shuffled = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-12a-demo"),
    ).evaluate(list(reversed(all_outcomes)))
    lookup_shuffled = strat.lookup(evidence_shuffled, profile_supported)
    ctx_shuffled = eng.build(
        profile_supported, _decision(), lookup_shuffled, label="supported",
    )
    _check(
        "shuffled outcomes same context id",
        ctx_shuffled.context_id == ctx_supported.context_id,
    )
    _check(
        "shuffled outcomes same status + factors",
        ctx_shuffled.decision_context_status == ctx_supported.decision_context_status
        and ctx_shuffled.factors == ctx_supported.factors,
    )

    # 8. No-look-ahead.
    _banner("8. No-look-ahead")
    from engine.intelligence.historical_outcome import OutcomeEvaluator

    original_eval = OutcomeEvaluator.evaluate

    def boom_eval(self, subject, future_candles):  # noqa: ANN001
        raise RuntimeError("must not re-evaluate outcomes")

    OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
    original_pipe = HistoricalEvaluationPipeline.evaluate

    def boom_pipe(self, candles):  # noqa: ANN001
        raise RuntimeError("must not re-run pipeline")

    HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
    try:
        ctx_no_future = eng.build_from_report(
            evidence, profile_supported, _decision(), label="supported",
        )
        _check(
            "works with OutcomeEvaluator patched to raise",
            ctx_no_future.matched,
        )
        _check(
            "works with HistoricalEvaluationPipeline patched to raise",
            ctx_no_future.decision_context_status
            == DecisionContextStatus.EVIDENCE_SUPPORTED,
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]

    # Input immutability: outcomes unchanged.
    originals = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    eng.build(profile_supported, _decision(), lookup_supported)
    eng.build_from_report(evidence, profile_supported, _decision())
    after = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    _check("outcomes not mutated", originals == after)

    # 9. Existing pipeline regression.
    _banner("9. Existing pipeline regression")
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

    print()
    print(
        "Historical evidence is descriptive and does not guarantee future "
        "performance. The existing decision / scoring logic is not modified "
        "by this context.",
    )

    failed = [label for label, ok in _CHECKS if not ok]
    print()
    if failed:
        print(f"Demo FAILED {len(failed)} check(s):")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"Sprint 12A demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
