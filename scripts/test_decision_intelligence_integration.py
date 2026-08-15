"""
Demo for Sprint 12B — Controlled Decision Intelligence Integration.

This demo proves the controlled INTEGRATION BOUNDARY between the Sprint
12A decision-intelligence foundation and the existing decision
architecture. It consumes the ALREADY-COMPUTED Sprint 12A
``DecisionIntelligenceContext`` (itself built from already-computed
Sprint 11X / 11Y / 11Z evidence) and attaches it to an EXISTING
DECISION, preserving the existing decision EXACTLY as-is while surfacing
the decision intelligence as additional, auditable, descriptive context.

The demo REUSES the Sprint 11X statistics, the Sprint 11Y evidence
classification, the Sprint 11Z strategy interpretation and the Sprint 12A
decision-intelligence context (it does NOT recompute them). It NEVER
re-evaluates outcomes, NEVER re-runs the pipeline, NEVER uses future
information, NEVER introduces machine learning or predictive models,
NEVER generates trading signals, and NEVER modifies the existing
decision / scoring logic.

The demo visibly proves:

1. Successful integration (INTEGRATED)
2. Existing decision preservation (by reference: ``is``)
3. Context attachment (decision intelligence reused by reference)
4. Unavailable intelligence (UNAVAILABLE)
5. Context-only integration (CONTEXT_ONLY, no matching cohort)
6. Invalid integration (INVALID, no existing decision)
7. Deterministic behavior (repeated + shuffled-input invariance)
8. Serialization round trip
9. Full 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B end-to-end flow
   + no-look-ahead protection + pipeline regression

The integration makes NO profitability, probability, directional
prediction, statistical-significance, buy / sell / enter / exit / hold,
or trading-recommendation claim. Decision intelligence is CONTEXTUAL
EVIDENCE, NOT a replacement decision; historical evidence is DESCRIPTIVE
and does NOT guarantee future performance.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.decision_intelligence_integration_config import (
    DecisionIntelligenceIntegrationConfig,
)
from engine.config.historical_evidence_config import EvidenceConfig
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.decision_intelligence_integration_serialization import (
    INTEGRATION_SCHEMA_VERSION,
    deserialize_integration,
    serialize_integration,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegrationStatus,
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
from engine.reporting.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationFormatter,
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
        rank=1, scan_id="scan-12b", setup_timeframe="15M",
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


def _decision(direction="LONG", classification="QUALIFIED", score=75):
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
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
    fmt = DecisionIntelligenceIntegrationFormatter()

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
        EvidenceConfig(label="sprint-12b-demo"),
    ).evaluate(all_outcomes)

    strat = StrategyIntelligenceEngine()
    di_eng = DecisionIntelligenceEngine()
    eng = DecisionIntelligenceIntegrationEngine()

    profile = _profile()
    lookup = strat.lookup(evidence, profile)
    di = di_eng.build(profile, _decision(), lookup, label="di")
    print(
        f"  DI context: status={di.decision_context_status.name} "
        f"strength={di.evidence_strength.name} matched={di.matched}",
    )

    # 1. Successful integration (INTEGRATED).
    _banner("1. Successful integration")
    original = _decision(classification="QUALIFIED")
    integ = eng.integrate(original, di, profile, label="integrated")
    print(
        f"  integration_id={integ.integration_id} "
        f"status={integ.integration_status.name} "
        f"evidence={integ.evidence_status.name}",
    )
    _check(
        "supported evidence integrated as INTEGRATED",
        integ.integration_status == IntegrationStatus.INTEGRATED,
    )
    _check(
        "integration id prefixed 'int-'",
        integ.integration_id.startswith("int-"),
    )
    _check(
        "evidence status surfaced from DI",
        integ.evidence_status == DecisionContextStatus.EVIDENCE_SUPPORTED,
    )
    print()
    print(fmt.format(integ))

    # 2. Existing decision preservation (by reference).
    _banner("2. Existing decision preservation")
    _check(
        "existing decision is original by reference",
        integ.existing_decision is original,
    )
    _check(
        "existing decision classification NOT upgraded",
        integ.existing_decision_summary.decision_classification == "QUALIFIED",
    )
    _check(
        "existing decision score NOT adjusted",
        integ.existing_decision_summary.decision_score == 75,
    )

    # 3. Context attachment (DI reused by reference).
    _banner("3. Context attachment")
    _check(
        "decision intelligence reused by reference",
        integ.decision_intelligence is di,
    )
    _check(
        "observed performance reused by reference",
        integ.observed_performance is di.observed_performance,
    )
    _check(
        "contextual factors reused from DI",
        integ.contextual_factors == di.factors,
    )
    _check(
        "DI context not mutated (id stable)",
        di.context_id == di_eng.build(profile, _decision(), lookup, label="di").context_id,
    )

    # 4. Unavailable intelligence (UNAVAILABLE).
    _banner("4. Unavailable intelligence")
    integ_unavail = eng.integrate(original, None, profile, label="unavailable")
    print(f"  status={integ_unavail.integration_status.name}")
    _check(
        "no DI supplied -> UNAVAILABLE",
        integ_unavail.integration_status == IntegrationStatus.UNAVAILABLE,
    )
    _check(
        "no fabricated evidence when unavailable",
        integ_unavail.evidence_status is None
        and integ_unavail.observed_performance is None
        and integ_unavail.evidence_strength is None,
    )
    _check(
        "existing decision preserved when unavailable",
        integ_unavail.existing_decision is original,
    )

    # 5. Context-only integration (CONTEXT_ONLY, no matching cohort).
    _banner("5. Context-only integration (no matching cohort)")
    nomatch_profile = OpportunityProfile(setup_type="RANGE_REJECTION")
    nomatch_lookup = strat.lookup(evidence, nomatch_profile)
    nomatch_di = di_eng.build(
        nomatch_profile, _decision(), nomatch_lookup, label="nomatch",
    )
    integ_ctx = eng.integrate(original, nomatch_di, nomatch_profile, label="ctx-only")
    print(f"  status={integ_ctx.integration_status.name}")
    _check(
        "no-match DI -> CONTEXT_ONLY",
        integ_ctx.integration_status == IntegrationStatus.CONTEXT_ONLY,
    )
    _check(
        "context-only existing decision unchanged",
        integ_ctx.existing_decision_summary.decision_classification == "QUALIFIED",
    )
    _check(
        "context-only has no fabricated observed performance",
        integ_ctx.observed_performance is None,
    )

    # 6. Invalid integration (INVALID, no existing decision).
    _banner("6. Invalid integration (no existing decision)")
    integ_invalid = eng.integrate(None, di, profile, label="invalid")
    print(f"  status={integ_invalid.integration_status.name}")
    _check(
        "no existing decision -> INVALID",
        integ_invalid.integration_status == IntegrationStatus.INVALID,
    )
    _check(
        "invalid has no existing decision",
        not integ_invalid.has_existing_decision,
    )
    # Strict guard.
    try:
        DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(strict=True),
        ).integrate(original, di, _profile(instrument="TCS"))
        _check("strict guard raises on inconsistent profile", False)
    except ValueError:
        _check("strict guard raises on inconsistent profile", True)

    # 7. Deterministic behavior.
    _banner("7. Deterministic behavior")
    integ_again = eng.integrate(original, di, profile, label="integrated")
    _check("repeated integration identical", integ_again == integ)
    _check(
        "repeated integration same id",
        integ_again.integration_id == integ.integration_id,
    )
    evidence_shuffled = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-12b-demo"),
    ).evaluate(list(reversed(all_outcomes)))
    lookup_shuffled = strat.lookup(evidence_shuffled, profile)
    di_shuffled = di_eng.build(profile, _decision(), lookup_shuffled, label="di")
    integ_shuffled = eng.integrate(original, di_shuffled, profile, label="integrated")
    _check(
        "shuffled outcomes same integration id",
        integ_shuffled.integration_id == integ.integration_id,
    )
    _check(
        "shuffled outcomes same status + factors",
        integ_shuffled.integration_status == integ.integration_status
        and integ_shuffled.contextual_factors == integ.contextual_factors,
    )

    # 8. Serialization round trip.
    _banner("8. Serialization round trip")
    for label, ctx in [
        ("integrated", integ),
        ("unavailable", integ_unavail),
        ("context-only", integ_ctx),
        ("invalid", integ_invalid),
    ]:
        rt = deserialize_integration(serialize_integration(ctx))
        ok = (
            rt.integration_id == ctx.integration_id
            and rt.integration_status == ctx.integration_status
            and rt.existing_decision_summary == ctx.existing_decision_summary
        )
        _check(f"{label} round trip preserves id/status/summary", ok)
    _check(
        "schema version is 1",
        INTEGRATION_SCHEMA_VERSION == 1,
    )
    _check(
        "heavy existing_decision ref drops on load (regenerable)",
        deserialize_integration(serialize_integration(integ)).existing_decision is None,
    )

    # 9. Full 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B end-to-end flow.
    _banner("9. Full 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B chain")
    e2e_profile = OpportunityProfile(
        instrument="NIFTY", direction="LONG",
        setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
    )
    e2e_lookup = strat.lookup(evidence, e2e_profile)
    e2e_di = di_eng.build(
        e2e_profile,
        _decision(direction="LONG", classification="QUALIFIED"),
        e2e_lookup,
        label="e2e-12b",
        metadata={"source": "12b-e2e"},
    )
    e2e_original = _decision(direction="LONG", classification="QUALIFIED")
    e2e_integ = eng.integrate(e2e_original, e2e_di, e2e_profile, label="e2e-12b")
    print(
        f"  e2e: status={e2e_integ.integration_status.name} "
        f"strength={e2e_integ.evidence_strength.name} "
        f"strategy={e2e_integ.strategy_interpretation.name} "
        f"observations={e2e_integ.observed_performance.total}",
    )
    _check(
        "e2e real evidence flows to the integration boundary",
        e2e_integ.integration_status == IntegrationStatus.INTEGRATED
        and e2e_integ.observed_performance.total == 60,
    )
    _check(
        "e2e existing decision flows unchanged to the boundary",
        e2e_integ.existing_decision is e2e_original
        and e2e_integ.existing_decision_summary.decision_classification == "QUALIFIED",
    )

    # No-look-ahead: patch OutcomeEvaluator + pipeline to raise.
    _banner("9a. No-look-ahead protection")
    original_eval = OutcomeEvaluator.evaluate

    def boom_eval(self, subject, future_candles):  # noqa: ANN001
        raise RuntimeError("must not re-evaluate outcomes")

    OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
    original_pipe = HistoricalEvaluationPipeline.evaluate

    def boom_pipe(self, candles):  # noqa: ANN001
        raise RuntimeError("must not re-run pipeline")

    HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
    try:
        integ_no_future = eng.integrate(original, di, profile, label="nola")
        _check(
            "works with OutcomeEvaluator patched to raise",
            integ_no_future.integration_status == IntegrationStatus.INTEGRATED,
        )
        _check(
            "works with HistoricalEvaluationPipeline patched to raise",
            integ_no_future.existing_decision is original,
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]

    # Input immutability: outcomes unchanged.
    originals = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    eng.integrate(original, di, profile)
    di_eng.build_from_report(evidence, profile, _decision())
    after = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    _check("outcomes not mutated by integration", originals == after)

    # Pipeline regression.
    _banner("9b. Pipeline regression")
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
        "Decision intelligence is contextual evidence attached to the "
        "existing decision. It is descriptive, does NOT guarantee future "
        "performance, does NOT constitute a trading recommendation, and "
        "does NOT modify the existing decision / scoring logic.",
    )

    failed = [label for label, ok in _CHECKS if not ok]
    print()
    if failed:
        print(f"Demo FAILED {len(failed)} check(s):")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"Sprint 12B demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
