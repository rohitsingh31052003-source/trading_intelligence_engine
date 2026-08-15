"""
Demo for Sprint 11Z — Evidence-Conditioned Strategy Intelligence.

This demo proves the strategy intelligence layer. It consumes
ALREADY-COMPUTED Sprint 11X performance analytics and Sprint 11Y
evidence-strength outputs and produces structured, conservative
strategy assessments. It REUSES the Sprint 11X statistics and the
Sprint 11Y evidence classification (it does NOT recompute them). It
NEVER re-evaluates outcomes, NEVER re-runs the pipeline, NEVER uses
future information, NEVER introduces machine learning or predictive
models, and NEVER generates trading signals.

The demo visibly proves:

A. A sufficiently supported historical cohort
B. An insufficient cohort
C. A current opportunity evidence lookup
D. A comparison between two cohorts
E. Serialization round trip
F. Explicit limitations / disclaimer

The demo prints an analyst-style strategy intelligence report. It
makes NO profitability, probability, directional prediction, or
statistical-significance claim. Historical evidence is DESCRIPTIVE
and does NOT guarantee future performance.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.historical_evidence_config import EvidenceConfig
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.strategy_intelligence import StrategyIntelligenceEngine
from engine.intelligence.strategy_intelligence_serialization import (
    deserialize_assessment,
    deserialize_comparison,
    deserialize_lookup,
    serialize_assessment,
    serialize_comparison,
    serialize_lookup,
)
from engine.models.historical_evidence import CohortSpec
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import BreakdownDimension
from engine.models.strategy_intelligence import (
    CohortMatchStatus,
    OpportunityProfile,
    StrategyAssessmentStatus,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.strategy_intelligence import StrategyIntelligenceFormatter


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
        rank=1, scan_id="scan-z", setup_timeframe="15M",
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
    fmt = StrategyIntelligenceFormatter()

    # Build a historical outcome corpus with two contrasting cohorts.
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
        EvidenceConfig(label="sprint-11z-demo"),
    ).evaluate(all_outcomes)

    eng = StrategyIntelligenceEngine()
    setup_dir_spec = CohortSpec(
        (BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION),
    )

    # A. A sufficiently supported historical cohort
    _banner("A. A sufficiently supported historical cohort")
    a_assessment = eng.assess(
        evidence, setup_dir_spec, "TREND_CONTINUATION|LONG",
        label="supported-cohort",
    )
    print(
        f"  cohort=TREND_CONTINUATION|LONG "
        f"sample={a_assessment.sample_count} "
        f"strength={a_assessment.evidence_strength.name} "
        f"status={a_assessment.assessment_status.name} "
        f"win_rate={a_assessment.observed_performance.win_rate}",
    )
    _check(
        "supported cohort classified STRONGER_HISTORICAL_SUPPORT",
        a_assessment.assessment_status
        == StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
    )
    _check(
        "supported cohort is_supported",
        a_assessment.is_supported,
    )
    print()
    print(fmt.format(a_assessment))

    # B. An insufficient cohort
    _banner("B. An insufficient cohort")
    b_assessment = eng.assess(
        evidence, setup_dir_spec, "STRUCTURE_CONTINUATION|LONG",
        label="insufficient-cohort",
    )
    print(
        f"  cohort=STRUCTURE_CONTINUATION|LONG "
        f"sample={b_assessment.sample_count} "
        f"strength={b_assessment.evidence_strength.name} "
        f"status={b_assessment.assessment_status.name} "
        f"win_rate={b_assessment.observed_performance.win_rate}",
    )
    _check(
        "insufficient cohort classified INSUFFICIENT_EVIDENCE",
        b_assessment.assessment_status
        == StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE,
    )
    _check(
        "small sample not strong despite 100% win rate",
        b_assessment.observed_performance.win_rate == 1.0
        and b_assessment.assessment_status
        != StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
    )
    _check(
        "insufficient cohort not actionable",
        not b_assessment.has_evidence,
    )

    # C. A current opportunity evidence lookup
    _banner("C. A current opportunity evidence lookup")
    profile = OpportunityProfile(
        instrument="NIFTY", direction="LONG",
        setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
    )
    lookup = eng.lookup(evidence, profile, label="current-lookup")
    print(f"  profile: {dict(profile.available_dimensions())}")
    print(
        f"  match={lookup.match_status.name} "
        f"spec={lookup.matched_spec.label if lookup.matched_spec else None} "
        f"key={lookup.matched_cohort.key if lookup.matched_cohort else None}",
    )
    _check("lookup matched", lookup.match_status == CohortMatchStatus.MATCHED)
    _check(
        "lookup used most specific composite spec",
        lookup.matched_spec is not None and lookup.matched_spec.is_composite,
    )
    _check(
        "lookup assessment consistent with cohort",
        lookup.assessment is not None
        and lookup.assessment.cohort_key == lookup.matched_cohort.key,
    )
    print()
    print(fmt.format_lookup(lookup))

    # Also show an honest NO_MATCH lookup.
    _banner("C2. A current opportunity evidence lookup (no match)")
    nomatch = eng.lookup(
        evidence, OpportunityProfile(setup_type="RANGE_REJECTION"),
    )
    print(f"  match={nomatch.match_status.name} assessment={nomatch.assessment}")
    _check(
        "no-match lookup is explicit",
        nomatch.match_status == CohortMatchStatus.NO_MATCH
        and nomatch.assessment is None
        and nomatch.matched_cohort is None,
    )

    # D. A comparison between two cohorts
    _banner("D. A comparison between two cohorts")
    cmp = eng.compare(
        evidence, setup_dir_spec,
        "TREND_CONTINUATION|LONG", "BREAKOUT|SHORT",
    )
    print(f"  both_present={cmp.both_present} metrics={len(cmp.metrics)}")
    for m in cmp.metrics:
        print(
            f"    {m.name:<22} A={m.value_a} B={m.value_b} "
            f"delta={m.delta} ({m.note})",
        )
    _check("comparison both cohorts present", cmp.both_present)
    _check("comparison has 7 metric rows", len(cmp.metrics) == 7)
    _check(
        "comparison notes descriptive (no superiority claim)",
        all("statistically superior" not in n.lower() for n in cmp.notes),
    )
    _check(
        "comparison disclaimer present",
        "No statistical procedure" in cmp.disclaimer,
    )
    print()
    print(fmt.format_comparison(cmp))

    # E. Serialization round trip
    _banner("E. Serialization round trip")
    assert deserialize_assessment(serialize_assessment(a_assessment)) == a_assessment
    assert deserialize_comparison(serialize_comparison(cmp)) == cmp
    assert deserialize_lookup(serialize_lookup(lookup)) == lookup
    _check(
        "assessment round trip lossless",
        deserialize_assessment(serialize_assessment(a_assessment)) == a_assessment,
    )
    _check(
        "comparison round trip lossless",
        deserialize_comparison(serialize_comparison(cmp)) == cmp,
    )
    _check(
        "lookup round trip lossless",
        deserialize_lookup(serialize_lookup(lookup)) == lookup,
    )
    _check(
        "no-match lookup round trip lossless",
        deserialize_lookup(serialize_lookup(nomatch)) == nomatch,
    )

    # Determinism + no-look-ahead + input immutability
    _banner("Determinism / no-look-ahead / input immutability")
    a_again = eng.assess(
        evidence, setup_dir_spec, "TREND_CONTINUATION|LONG",
        label="supported-cohort",
    )
    _check("repeated assessment identical", a_again == a_assessment)
    _check(
        "repeated assessment same id",
        a_again.assessment_id == a_assessment.assessment_id,
    )

    # Shuffled-equivalent outcomes produce the same evidence + same
    # assessment id (shuffle-invariance through the 11Y sorted
    # identity and the cohort-based assessment id).
    evidence_shuffled = HistoricalEvidenceEngine(
        EvidenceConfig(label="sprint-11z-demo"),
    ).evaluate(list(reversed(all_outcomes)))
    a_shuffled = eng.assess(
        evidence_shuffled, setup_dir_spec, "TREND_CONTINUATION|LONG",
        label="supported-cohort",
    )
    _check(
        "shuffled outcomes same assessment id",
        a_shuffled.assessment_id == a_assessment.assessment_id,
    )

    # Input immutability: outcomes / evidence report unchanged.
    originals = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    eng.assess(evidence, setup_dir_spec, "TREND_CONTINUATION|LONG")
    eng.lookup(evidence, profile)
    eng.compare(
        evidence, setup_dir_spec,
        "TREND_CONTINUATION|LONG", "BREAKOUT|SHORT",
    )
    after = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in all_outcomes
    ]
    _check("outcomes not mutated", originals == after)

    # Existing pipeline regression
    _banner("Existing pipeline regression")
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

    # F. Explicit limitations / disclaimer
    _banner("F. Explicit limitations / disclaimer")
    print("Assessment limitations:")
    print(f"  {a_assessment.limitations}")
    print()
    print("Insufficient-cohort limitations:")
    print(f"  {b_assessment.limitations}")
    print()
    print("Comparison disclaimer:")
    print(f"  {cmp.disclaimer}")
    _check(
        "limitations mention no statistical hypothesis test",
        "No statistical hypothesis test was performed" in a_assessment.limitations,
    )
    _check(
        "limitations mention no future guarantee",
        "does not guarantee future performance" in a_assessment.limitations,
    )
    _check(
        "report contains descriptive warning",
        "does not guarantee future performance"
        in fmt.format(a_assessment),
    )

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
    print(f"Sprint 11Z demo completed successfully ({len(_CHECKS)} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
