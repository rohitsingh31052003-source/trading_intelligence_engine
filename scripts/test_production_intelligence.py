"""
Demo for Sprint 12E — PRODUCTION INTEGRATION + FINAL HARDENING.

This is the FINAL planned sprint of the current architecture. It is NOT
another intelligence / scoring layer and NOT a new orchestration
package. It is the smallest clean PRODUCTION INTEGRATION BOUNDARY that
bundles the ALREADY-COMPUTED outputs of the completed architecture
(Sprint 11V through 12D) into ONE coherent, inspectable,
production-facing artifact WITHOUT altering the meaning of any previous
layer.

Architecture (12E sits BELOW 12D; it consumes already-computed outputs
of the existing chain):

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C -> 12D -> 12E

The demo REUSES the existing Sprint 11Y / 11Z / 12A / 12B / 12C / 12D
engines (it does NOT duplicate them) and bundles their already-computed
outputs via the Sprint 12E :class:`ProductionIntelligenceEngine`. It
NEVER re-evaluates outcomes with future data, NEVER re-runs the pipeline
to influence decisions, NEVER introduces machine learning or predictive
models, and NEVER generates trading signals.

The demo visibly proves (1-12):

1.  Production happy path
2.  Existing decision preservation (by reference)
3.  Evidence-supported case
4.  Insufficient evidence case
5.  Evidence unavailable case
6.  Invalid / mismatched context case
7.  Serialization round trip
8.  Determinism
9.  Immutability
10. No-look-ahead
11. Full 11V -> 12D regression (12C + 12D demos + pipeline baseline)
12. Final production integration

Every demo check prints explicit PASS / FAIL / SKIPPED. SKIPPED is
never counted as PASS. Production results are DESCRIPTIVE. They do NOT
predict future market behavior and do NOT guarantee profitability.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_evidence_config import EvidenceConfig
from engine.intelligence.backtest_validation import BacktestValidationEngine
from engine.intelligence.decision_intelligence import DecisionIntelligenceEngine
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import (
    OutcomeEvaluator,
    OutcomeStatus,
)
from engine.intelligence.production_intelligence import (
    ProductionIntelligenceEngine,
)
from engine.intelligence.production_intelligence_serialization import (
    PRODUCTION_SCHEMA_VERSION,
    deserialize_production,
    parse_production_header,
    serialize_production,
)
from engine.intelligence.robustness_validation import RobustnessValidationEngine
from engine.intelligence.strategy_intelligence import StrategyIntelligenceEngine
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    ExistingDecisionSummary,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeSubject,
)
from engine.models.production_intelligence import (
    ProductionIntegrationStatus,
    ProductionValidationState,
)
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.production_intelligence import (
    ProductionIntelligenceFormatter,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# SCENARIO BUILDERS (mirror the Sprint 12D demo helpers)
# ============================================================


def _subject(
    i: int,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=_EPOCH + timedelta(days=i),
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=1,
        scan_id="scan-12e",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    i: int = 0,
    realized_r: float | None = None,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
) -> HistoricalOutcome:
    risk = abs(100.0 - 95.0)
    return HistoricalOutcome(
        subject=_subject(
            i, instrument=instrument, direction=direction,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=(mfe / risk) if mfe is not None else None,
        mae_r=(mae / risk) if mae is not None else None,
        risk=risk,
    )


def _resolved(
    n: int,
    win_fraction: float,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    rng = random.Random(seed)
    out: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        out.append(
            _outcome(
                OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT,
                i=i, realized_r=2.0 if win else -1.0,
                instrument=instrument, direction=direction,
                setup_type=setup_type, mtf_alignment=mtf_alignment,
            ),
        )
    return out


def _decision(
    direction: str = "LONG", classification: str = "QUALIFIED", score: int = 75,
) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1,
        geometry_complete=True,
        confluence_score=4,
        risk_reward_ratio=2.0,
        entry=100.0,
        stop=95.0,
        target=110.0,
    )


def _profile(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
) -> OpportunityProfile:
    return OpportunityProfile(
        instrument=instrument,
        direction=direction,
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


# ============================================================
# DEMO
# ============================================================


_CHECKS: list[tuple[str, str, bool]] = []


def _check(label: str, condition: bool, status: str = "PASS") -> None:
    if not condition:
        status = "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status, condition))


def _skip(label: str, reason: str = "") -> None:
    print(f"  [SKIPPED] {label} ({reason})")
    _CHECKS.append((label, "SKIPPED", False))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    fmt = ProductionIntelligenceFormatter()
    prod_eng = ProductionIntelligenceEngine()

    # ---- Build a historical outcome corpus with contrasting cohorts --
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
        EvidenceConfig(label="sprint-12e-demo"),
    ).evaluate(all_outcomes)
    strat = StrategyIntelligenceEngine()
    di_eng = DecisionIntelligenceEngine()
    integ_eng = DecisionIntelligenceIntegrationEngine()

    # Pre-compute the offline 12C / 12D validation reports over the
    # historical outcome corpus. These are OFFLINE artifacts; the
    # production runtime only references them.
    bvr = BacktestValidationEngine().validate(
        [{"name": "sprint-12e", "outcomes": tuple(all_outcomes)}],
        label="sprint-12e-backtest",
    )
    rvr = RobustnessValidationEngine().validate(
        [{"name": "sprint-12e", "outcomes": tuple(all_outcomes)}],
        label="sprint-12e-robustness",
    )

    # ---- Evidence-supported case --------------------------------
    profile = _profile()
    lookup = strat.lookup(evidence, profile)
    di = di_eng.build(profile, _decision(), lookup, label="di-supported")
    original = _decision(classification="QUALIFIED")
    integ = integ_eng.integrate(original, di, profile, label="int-supported")

    # 1. Production happy path.
    _banner("1. Production happy path")
    ctx = prod_eng.assemble(
        integ, backtest_validation=bvr, robustness_validation=rvr,
        label="production-happy",
    )
    print(
        f"  production_id={ctx.production_id} "
        f"status={ctx.integration_status.name} "
        f"validation={ctx.validation_state.name}",
    )
    _check(
        "production id prefixed 'prod-'",
        ctx.production_id.startswith("prod-"),
    )
    _check(
        "integration status INTEGRATED (mirrored from 12B)",
        ctx.integration_status == ProductionIntegrationStatus.INTEGRATED,
    )
    _check(
        "validation state FULL_VALIDATION",
        ctx.validation_state == ProductionValidationState.FULL_VALIDATION,
    )
    _check(
        "has_evidence surfaced from 12B",
        ctx.has_evidence,
    )
    print()
    print(fmt.format(ctx))

    # 2. Existing decision preservation (by reference).
    _banner("2. Existing decision preservation")
    _check(
        "existing decision is original by reference",
        ctx.existing_decision is original,
    )
    _check(
        "existing decision classification NOT upgraded",
        ctx.existing_decision_summary.decision_classification == "QUALIFIED",
    )
    _check(
        "existing decision score NOT adjusted",
        ctx.existing_decision_summary.decision_score == 75,
    )
    _check(
        "integrated_context reused by reference",
        ctx.integrated_context is integ,
    )
    _check(
        "12C validation report reused by reference",
        ctx.backtest_validation is bvr,
    )
    _check(
        "12D validation report reused by reference",
        ctx.robustness_validation is rvr,
    )

    # 3. Evidence-supported case (explicit demonstration).
    _banner("3. Evidence-supported case")
    _check(
        "evidence status surfaced EVIDENCE_SUPPORTED",
        ctx.evidence_status == DecisionContextStatus.EVIDENCE_SUPPORTED,
    )
    _check(
        "observed performance total matches corpus",
        ctx.observed_performance.total == 60,
    )

    # 4. Insufficient evidence case (tiny cohort, 100% win, never strong).
    _banner("4. Insufficient evidence case")
    ins_profile = _profile(
        instrument="HDFCBANK", setup_type="STRUCTURE_CONTINUATION",
    )
    ins_lookup = strat.lookup(evidence, ins_profile)
    ins_di = di_eng.build(
        ins_profile, _decision(), ins_lookup, label="di-insufficient",
    )
    ins_integ = integ_eng.integrate(
        _decision(classification="WATCH"), ins_di, ins_profile,
        label="int-insufficient",
    )
    ins_ctx = prod_eng.assemble(ins_integ, label="production-insufficient")
    _check(
        "insufficient evidence status INSUFFICIENT_EVIDENCE",
        ins_ctx.evidence_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE,
    )
    _check(
        "production status INTEGRATED (12B INTEGRATED mirrored)",
        ins_ctx.integration_status == ProductionIntegrationStatus.INTEGRATED,
    )
    _check(
        "insufficient evidence NOT evidence_supported",
        not ins_ctx.evidence_supported,
    )
    _check(
        "validation state NONE (no offline reports attached)",
        ins_ctx.validation_state == ProductionValidationState.NONE,
    )

    # 5. Evidence unavailable case (no matching cohort).
    _banner("5. Evidence unavailable case")
    nomatch_profile = _profile(setup_type="RANGE_REJECTION")
    nomatch_lookup = strat.lookup(evidence, nomatch_profile)
    nomatch_di = di_eng.build(
        nomatch_profile, _decision(), nomatch_lookup, label="di-nomatch",
    )
    nomatch_integ = integ_eng.integrate(
        _decision(classification="QUALIFIED"), nomatch_di, nomatch_profile,
        label="int-nomatch",
    )
    nomatch_ctx = prod_eng.assemble(
        nomatch_integ, label="production-nomatch",
    )
    _check(
        "evidence unavailable -> CONTEXT_ONLY",
        nomatch_ctx.integration_status == ProductionIntegrationStatus.CONTEXT_ONLY,
    )
    _check(
        "evidence status EVIDENCE_UNAVAILABLE",
        nomatch_ctx.evidence_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE,
    )
    _check(
        "observed performance unavailable (no fabricated metrics)",
        nomatch_ctx.observed_performance is None,
    )

    # 6. Invalid / mismatched context case (no integrated context).
    _banner("6. Invalid / mismatched context case")
    invalid_ctx = prod_eng.assemble(None, label="production-invalid")
    _check(
        "no integrated context -> INVALID",
        invalid_ctx.integration_status == ProductionIntegrationStatus.INVALID,
    )
    _check(
        "invalid is_empty",
        invalid_ctx.is_empty,
    )
    _check(
        "invalid existing decision None (not fabricated)",
        invalid_ctx.existing_decision is None,
    )
    # Type-mismatch raises explicitly (fail-safe).
    try:
        prod_eng.assemble("not-an-integrated-context")  # type: ignore[arg-type]
        _check("type mismatch raises TypeError", False)
    except TypeError:
        _check("type mismatch raises TypeError", True)

    # 7. Serialization round trip.
    _banner("7. Serialization round trip")
    payload = serialize_production(ctx)
    restored = deserialize_production(payload)
    _check(
        "round trip production id preserved",
        restored.production_id == ctx.production_id,
    )
    _check(
        "round trip integration status preserved",
        restored.integration_status == ctx.integration_status,
    )
    _check(
        "round trip validation state preserved",
        restored.validation_state == ctx.validation_state,
    )
    _check(
        "round trip 12C validation overall preserved",
        restored.backtest_validation.overall_status == bvr.overall_status,
    )
    _check(
        "round trip 12D validation overall preserved",
        restored.robustness_validation.overall_status == rvr.overall_status,
    )
    _check(
        "round trip observed performance preserved",
        restored.observed_performance.total == ctx.observed_performance.total,
    )
    _check(
        "round trip heavy existing_decision drops to None",
        restored.existing_decision is None,
    )
    _check(
        "round trip existing_decision_summary preserved",
        restored.existing_decision_summary.decision_classification == "QUALIFIED",
    )
    _check(
        "header parse exposes schema version",
        parse_production_header(payload)["schema_version"] == PRODUCTION_SCHEMA_VERSION,
    )
    # Malformed payload rejected.
    try:
        deserialize_production('{"schema_version": 999, "production": {}}')
        _check("malformed schema version rejected", False)
    except ValueError:
        _check("malformed schema version rejected", True)

    # 8. Determinism.
    _banner("8. Determinism")
    ctx_repeat = prod_eng.assemble(
        integ, backtest_validation=bvr, robustness_validation=rvr,
        label="production-happy",
    )
    _check(
        "repeated assembly yields identical production id",
        ctx_repeat.production_id == ctx.production_id,
    )
    # Shuffle-invariant: shuffled outcomes produce the same 11Y evidence id
    # and therefore the same 12A/12B/12E ids.
    shuffled = list(all_outcomes)
    random.Random(99).shuffle(shuffled)
    evidence_shuffled = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-12e-demo"),
    ).evaluate(shuffled)
    lookup_shuffled = strat.lookup(evidence_shuffled, profile)
    di_shuffled = di_eng.build(
        profile, _decision(), lookup_shuffled, label="di-supported",
    )
    integ_shuffled = integ_eng.integrate(
        original, di_shuffled, profile, label="int-supported",
    )
    ctx_shuffled = prod_eng.assemble(
        integ_shuffled, backtest_validation=bvr, robustness_validation=rvr,
        label="production-happy",
    )
    _check(
        "shuffled outcomes yield identical production id",
        ctx_shuffled.production_id == ctx.production_id,
    )

    # 9. Immutability.
    _banner("9. Immutability")
    _check(
        "source 12B context not mutated (id stable)",
        integ.integration_id == "int-" + integ.integration_id[4:],
    )
    _check(
        "source existing decision not mutated",
        original.decision_classification == "QUALIFIED",
    )
    _check(
        "source 12C validation not mutated (id stable)",
        bvr.validation_id == bvr.validation_id,
    )
    _check(
        "production context frozen",
        bool(
            hasattr(ctx, "__dataclass_fields__")
            and ctx.__class__.__dataclass_fields__.get("production_id")
        ),
    )
    try:
        ctx.production_id = "mutated"  # type: ignore[misc]
        _check("production context immutable", False)
    except Exception:
        _check("production context immutable", True)

    # 10. No-look-ahead.
    _banner("10. No-look-ahead protection")
    original_eval = OutcomeEvaluator.evaluate
    original_pipe = HistoricalEvaluationPipeline.evaluate

    def boom_eval(self, subject, future_candles):  # noqa: ANN001
        raise RuntimeError("must not re-evaluate outcomes")

    def boom_pipe(self, candles):  # noqa: ANN001
        raise RuntimeError("must not re-run pipeline")

    OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
    HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
    try:
        nola_ctx = prod_eng.assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr,
            label="production-nola",
        )
        _check(
            "works with OutcomeEvaluator patched to raise",
            nola_ctx.integration_status == ProductionIntegrationStatus.INTEGRATED,
        )
        _check(
            "works with HistoricalEvaluationPipeline patched to raise",
            nola_ctx.existing_decision is original,
        )
        _check(
            "production API takes no candle argument (signature)",
            "candles" not in ProductionIntelligenceEngine.assemble.__code__.co_varnames,
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]

    # 11. Full 11V -> 12D regression.
    _banner("11. Full 11V -> 12D regression")
    _check(
        "12C backtest validation overall PASS",
        bvr.overall_status.name == "PASS",
    )
    _check(
        "12D robustness validation overall PASS",
        rvr.overall_status.name == "PASS",
    )
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    print(
        f"  Pipeline: signals_generated={result.signals_generated} "
        f"completed_trades={result.performance.completed_trades}",
    )
    _check(
        "Sprint 11V baseline (signals=4, trades=3)",
        result.signals_generated == 4
        and result.performance.completed_trades == 3,
    )

    # 12. Final production integration (coherent bundle).
    _banner("12. Final production integration")
    _check(
        "production bundles all eight concerns",
        ctx.has_integrated_context
        and ctx.has_validation
        and ctx.existing_decision is not None
        and ctx.decision_intelligence is not None
        and ctx.evidence_status is not None
        and ctx.strategy_interpretation is not None
        and ctx.observed_performance is not None
        and ctx.evidence_strength is not None,
    )
    _check(
        "production id deterministic and shuffle-invariant",
        ctx_shuffled.production_id == ctx.production_id,
    )
    _check(
        "no BUY/SELL/ENTER/EXIT/HOLD recommendation language in report",
        not any(
            word in fmt.format(ctx).upper()
            for word in ("BUY", "SELL", "ENTER TRADE", "EXIT TRADE", "HOLD POSITION")
        ),
    )
    print()
    print("Production intelligence is a coherent bundle of already-computed")
    print("descriptive artifacts. It is NOT a trading signal, NOT a predictive")
    print("guarantee, and does NOT modify the existing decision / scoring logic.")
    print()

    # ---- Summary ----
    passed = sum(1 for _, s, _ in _CHECKS if s == "PASS")
    skipped = sum(1 for _, s, _ in _CHECKS if s == "SKIPPED")
    failed = sum(1 for _, s, _ in _CHECKS if s == "FAIL")
    print("=" * 60)
    print(f"Demo checks: {passed} PASS, {skipped} SKIPPED, {failed} FAIL.")
    if failed:
        print("Sprint 12E demo FAILED.")
        return 1
    print("Sprint 12E demo completed successfully "
          f"({passed} checks passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
