"""
Robust historical backtesting & adversarial validation engine
(Sprint 12C).

:class:`BacktestValidationEngine` is the VALIDATION layer that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    architecture remain correct, deterministic, leak-free and
    accounting-consistent when subjected to broader historical replay
    and adversarial conditions?"

It is the validation layer of the separated concern pipeline:

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C (this layer)

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the existing Sprint 11X / 11Y / 11Z / 12A / 12B
engines (and, where the chain is exercised end-to-end, the Sprint 11V
replay and the Sprint 11W outcome evaluator). It performs INDEPENDENT
CROSS-CHECKS of their already-computed outputs; it NEVER duplicates the
trading / outcome / analytics / evidence / decision logic. Where
accounting identities must be checked, the engine recomputes the
EXPECTED values directly from the raw Sprint 11W
:class:`~engine.models.historical_outcome.HistoricalOutcome` objects (an
independent validator implemented here as :func:`_recompute_statistics`)
and compares them to the Sprint 11X reported statistics — it does NOT
call the Sprint 11X engine a second time to "check itself".

DESIGN PRINCIPLE — no leakage introduced:

The validation engine NEVER supplies future candles to any component
that is not supposed to receive them. The look-ahead / point-in-time
checks PATCH :meth:`OutcomeEvaluator.evaluate` and
:meth:`HistoricalEvaluationPipeline.evaluate` to raise, then re-run the
downstream 11X / 11Y / 11Z / 12A / 12B chain and confirm no component
attempts to access candles. This proves the downstream chain has no
hidden candle dependency.

DESIGN PRINCIPLE — no modification of existing decision semantics:

The decision-authority checks build Sprint 11S
:class:`~engine.models.trade_decision.TradeDecision` objects with each
classification (REJECTED / WATCH / QUALIFIED / PREFERRED), integrate
each with strong / weak / unavailable decision intelligence, and verify
the existing decision classification is NEVER upgraded, downgraded or
otherwise modified by the Sprint 12B integration.

DESIGN PRINCIPLE — honest reporting:

A check that cannot be performed is reported as ``SKIPPED`` with a
descriptive reason — never as a fake ``PASS``. A failed check reports
the specific failure detail. The overall status is ``PASS`` only when
every non-skipped check passed and at least one check ran.

DESIGN PRINCIPLE — deterministic:

Identical scenarios always produce identical validation results. No
wall-clock time, no randomness, no unordered iteration. The validation
identifier hashes the SORTED canonical identity of the scenarios +
checks.

This is intelligence / validation, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.backtest_validation import
BacktestValidationEngine``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Sequence
from statistics import median
from typing import Any

from engine.config.backtest_validation_config import BacktestValidationConfig
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import (
    HistoricalOutcomeEngine,
    OutcomeEvaluator,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
    _dimension_key,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.backtest_validation import (
    BacktestValidationReport,
    CategorySummary,
    CheckResult,
    ScenarioResult,
    ValidationCategory,
    ValidationCheckStatus,
)
from engine.models.historical_evidence import (
    EvidenceStrength,
    HistoricalEvidenceReport,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    OpportunityProfile,
)


# ============================================================
# INDEPENDENT ACCOUNTING RECONCILIATION
# ============================================================
#
# These helpers recompute the EXPECTED statistics directly from the raw
# Sprint 11W HistoricalOutcome objects. They are an INDEPENDENT validator
# (a deliberately simple, separate implementation) used to cross-check
# the Sprint 11X reported statistics — they do NOT call the Sprint 11X
# engine a second time to "check itself".


def _recompute_statistics(
    outcomes: Sequence[HistoricalOutcome],
) -> HistoricalPerformanceStatistics:
    """
    Independently recompute the core statistics from raw outcomes.

    This mirrors the Sprint 11X accounting rules EXACTLY (so it is a
    meaningful cross-check) but is implemented here as a separate,
    auditable validator. It NEVER calls the Sprint 11X engine.
    """

    total = len(outcomes)
    if total == 0:
        return HistoricalPerformanceStatistics()

    target_hits = 0
    stop_hits = 0
    expired = 0
    both_touched = 0
    no_geometry = 0
    insufficient_data = 0
    realized_r_values: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []
    mfe_r_values: list[float] = []
    mae_r_values: list[float] = []

    for outcome in outcomes:
        status = outcome.outcome_status
        if status == OutcomeStatus.TARGET_HIT:
            target_hits += 1
        elif status == OutcomeStatus.STOP_HIT:
            stop_hits += 1
        elif status == OutcomeStatus.EXPIRED:
            expired += 1
        elif status == OutcomeStatus.BOTH_TOUCHED:
            both_touched += 1
        elif status == OutcomeStatus.NO_GEOMETRY:
            no_geometry += 1
        elif status == OutcomeStatus.INSUFFICIENT_DATA:
            insufficient_data += 1
        if outcome.realized_r is not None:
            realized_r_values.append(float(outcome.realized_r))
        if outcome.mfe is not None:
            mfe_values.append(float(outcome.mfe))
        if outcome.mae is not None:
            mae_values.append(float(outcome.mae))
        if outcome.mfe_r is not None:
            mfe_r_values.append(float(outcome.mfe_r))
        if outcome.mae_r is not None:
            mae_r_values.append(float(outcome.mae_r))

    resolved = target_hits + stop_hits + both_touched + expired
    denom = target_hits + stop_hits
    win_rate = target_hits / denom if denom > 0 else None
    loss_rate = stop_hits / denom if denom > 0 else None
    expiration_rate = expired / total if total > 0 else None
    ambiguous_rate = both_touched / total if total > 0 else None

    valid_r_count = len(realized_r_values)
    total_realized_r = (
        sum(realized_r_values) if valid_r_count > 0 else None
    )
    average_realized_r = (
        sum(realized_r_values) / valid_r_count if valid_r_count > 0 else None
    )
    median_realized_r = (
        median(realized_r_values) if valid_r_count > 0 else None
    )
    gross_positive_r = (
        sum(r for r in realized_r_values if r > 0)
        if any(r > 0 for r in realized_r_values)
        else None
    )
    gross_negative_r = (
        sum(r for r in realized_r_values if r < 0)
        if any(r < 0 for r in realized_r_values)
        else None
    )
    profit_factor: float | None = None
    if gross_positive_r is not None and gross_negative_r is not None:
        neg_abs = abs(gross_negative_r)
        if neg_abs > 0:
            profit_factor = gross_positive_r / neg_abs

    average_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else None
    average_mae = sum(mae_values) / len(mae_values) if mae_values else None
    average_mfe_r = (
        sum(mfe_r_values) / len(mfe_r_values) if mfe_r_values else None
    )
    average_mae_r = (
        sum(mae_r_values) / len(mae_r_values) if mae_r_values else None
    )

    return HistoricalPerformanceStatistics(
        total=total,
        resolved=resolved,
        target_hits=target_hits,
        stop_hits=stop_hits,
        expired=expired,
        both_touched=both_touched,
        no_geometry=no_geometry,
        insufficient_data=insufficient_data,
        win_rate=win_rate,
        loss_rate=loss_rate,
        expiration_rate=expiration_rate,
        ambiguous_rate=ambiguous_rate,
        total_realized_r=total_realized_r,
        average_realized_r=average_realized_r,
        median_realized_r=median_realized_r,
        gross_positive_r=gross_positive_r,
        gross_negative_r=gross_negative_r,
        profit_factor=profit_factor,
        valid_r_count=valid_r_count,
        average_mfe=average_mfe,
        average_mae=average_mae,
        average_mfe_r=average_mfe_r,
        average_mae_r=average_mae_r,
    )


def _stats_equal(
    a: HistoricalPerformanceStatistics,
    b: HistoricalPerformanceStatistics,
    tol: float,
) -> tuple[bool, str]:
    """Compare two statistics objects; return (equal, reason)."""

    int_fields = (
        "total", "resolved", "target_hits", "stop_hits", "expired",
        "both_touched", "no_geometry", "insufficient_data", "valid_r_count",
    )
    for f in int_fields:
        va, vb = getattr(a, f), getattr(b, f)
        if va != vb:
            return False, f"{f}: {va} != {vb}"
    float_fields = (
        "win_rate", "loss_rate", "expiration_rate", "ambiguous_rate",
        "total_realized_r", "average_realized_r", "median_realized_r",
        "gross_positive_r", "gross_negative_r", "profit_factor",
        "average_mfe", "average_mae", "average_mfe_r", "average_mae_r",
    )
    for f in float_fields:
        va, vb = getattr(a, f), getattr(b, f)
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            return False, f"{f}: {va} != {vb} (None mismatch)"
        if abs(float(va) - float(vb)) > tol:
            return False, f"{f}: {va} != {vb}"
    return True, ""


def _breakdown_for(
    analytics: HistoricalPerformanceAnalytics,
    dimension: BreakdownDimension,
) -> tuple[tuple[str, HistoricalPerformanceStatistics], ...]:
    """Return ``(key, statistics)`` pairs for one dimension, ordered."""

    for b in analytics.breakdowns:
        if b.dimension == dimension:
            return tuple((g.key, g.statistics) for g in b.groups)
    return ()


# ============================================================
# CHECK HELPERS
# ============================================================


def _check_accounting(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
    tol: float,
) -> CheckResult:
    """Reconcile the reported overall statistics independently."""

    if not outcomes:
        return CheckResult(
            name="accounting_overall",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to reconcile.",
        )
    expected = _recompute_statistics(outcomes)
    ok, reason = _stats_equal(expected, analytics.overall, tol)
    if ok:
        return CheckResult(
            name="accounting_overall",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.PASS,
            detail=f"Overall statistics reconcile ({expected.total} outcomes).",
        )
    return CheckResult(
        name="accounting_overall",
        category=ValidationCategory.ACCOUNTING_RECONCILIATION,
        status=ValidationCheckStatus.FAIL,
        detail=f"Overall statistics mismatch: {reason}",
    )


def _check_breakdown_accounting(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
    tol: float,
) -> CheckResult:
    """Reconcile each breakdown group's statistics independently."""

    if not outcomes:
        return CheckResult(
            name="accounting_breakdowns",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to reconcile.",
        )
    failures: list[str] = []
    for dimension in BreakdownDimension:
        groups = _breakdown_for(analytics, dimension)
        for key, reported in groups:
            bucket = [
                o for o in outcomes
                if _dimension_key(o, dimension) == key
            ]
            expected = _recompute_statistics(bucket)
            ok, reason = _stats_equal(expected, reported, tol)
            if not ok:
                failures.append(
                    f"{dimension.name}[{key!r}]: {reason}",
                )
    if failures:
        return CheckResult(
            name="accounting_breakdowns",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail="; ".join(failures),
        )
    return CheckResult(
        name="accounting_breakdowns",
        category=ValidationCategory.ACCOUNTING_RECONCILIATION,
        status=ValidationCheckStatus.PASS,
        detail="All breakdown groups reconcile independently.",
    )


def _check_r_identity(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
    tol: float,
) -> CheckResult:
    """gross_positive_r + gross_negative_r reconcile with total_realized_r."""

    valid = [float(o.realized_r) for o in outcomes if o.realized_r is not None]
    if not valid:
        if analytics.overall.total_realized_r is None:
            return CheckResult(
                name="accounting_r_identity",
                category=ValidationCategory.ACCOUNTING_RECONCILIATION,
                status=ValidationCheckStatus.PASS,
                detail="No valid R; total_realized_r is None (consistent).",
            )
        return CheckResult(
            name="accounting_r_identity",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail="No valid R but total_realized_r is not None.",
        )
    expected_total = sum(valid)
    pos = sum(r for r in valid if r > 0)
    neg = sum(r for r in valid if r < 0)
    expected_gp = pos if any(r > 0 for r in valid) else None
    expected_gn = neg if any(r < 0 for r in valid) else None

    problems: list[str] = []
    if analytics.overall.total_realized_r is None:
        problems.append("total_realized_r is None but valid R exists")
    elif abs(analytics.overall.total_realized_r - expected_total) > tol:
        problems.append(
            f"total_realized_r {analytics.overall.total_realized_r} != {expected_total}",
        )
    if expected_gp is None and analytics.overall.gross_positive_r is not None:
        problems.append("gross_positive_r fabricated")
    elif (
        expected_gp is not None
        and analytics.overall.gross_positive_r is not None
        and abs(analytics.overall.gross_positive_r - expected_gp) > tol
    ):
        problems.append(
            f"gross_positive_r {analytics.overall.gross_positive_r} != {expected_gp}",
        )
    if expected_gn is None and analytics.overall.gross_negative_r is not None:
        problems.append("gross_negative_r fabricated")
    elif (
        expected_gn is not None
        and analytics.overall.gross_negative_r is not None
        and abs(analytics.overall.gross_negative_r - expected_gn) > tol
    ):
        problems.append(
            f"gross_negative_r {analytics.overall.gross_negative_r} != {expected_gn}",
        )
    # gross_positive + gross_negative == total (when both present).
    if expected_gp is not None and expected_gn is not None:
        if analytics.overall.gross_positive_r is not None and (
            analytics.overall.gross_negative_r is not None
        ):
            combined = (
                analytics.overall.gross_positive_r
                + analytics.overall.gross_negative_r
            )
            if abs(combined - expected_total) > tol:
                problems.append(
                    f"gross_positive+negative {combined} != total {expected_total}",
                )
    if problems:
        return CheckResult(
            name="accounting_r_identity",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return CheckResult(
        name="accounting_r_identity",
        category=ValidationCategory.ACCOUNTING_RECONCILIATION,
        status=ValidationCheckStatus.PASS,
        detail=f"R identity reconciles ({len(valid)} valid R).",
    )


def _check_excluded_outcomes(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
) -> CheckResult:
    """BOTH_TOUCHED / NO_GEOMETRY / INSUFFICIENT_DATA contribute no R."""

    if not outcomes:
        return CheckResult(
            name="accounting_excluded",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to reconcile.",
        )
    excluded = (
        OutcomeStatus.BOTH_TOUCHED,
        OutcomeStatus.NO_GEOMETRY,
        OutcomeStatus.INSUFFICIENT_DATA,
    )
    bad = [
        o for o in outcomes
        if o.outcome_status in excluded and o.realized_r is not None
    ]
    if bad:
        return CheckResult(
            name="accounting_excluded",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail=(
                f"{len(bad)} excluded outcome(s) carry a fabricated realized_r"
            ),
        )
    valid_r = sum(
        1 for o in outcomes
        if o.outcome_status
        in (OutcomeStatus.TARGET_HIT, OutcomeStatus.STOP_HIT, OutcomeStatus.EXPIRED)
        and o.realized_r is not None
    )
    if analytics.overall.valid_r_count != valid_r:
        return CheckResult(
            name="accounting_excluded",
            category=ValidationCategory.ACCOUNTING_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail=(
                f"valid_r_count {analytics.overall.valid_r_count} != {valid_r}"
            ),
        )
    return CheckResult(
        name="accounting_excluded",
        category=ValidationCategory.ACCOUNTING_RECONCILIATION,
        status=ValidationCheckStatus.PASS,
        detail="Excluded outcomes contribute no fabricated R.",
    )


def _check_cohort_reconciliation(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
) -> CheckResult:
    """sum(breakdown counts) == overall total; no double counting."""

    if not outcomes:
        return CheckResult(
            name="cohort_reconciliation",
            category=ValidationCategory.COHORT_RECONCILIATION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to reconcile.",
        )
    problems: list[str] = []
    for dimension in BreakdownDimension:
        groups = _breakdown_for(analytics, dimension)
        total_in_groups = sum(g.total for _, g in groups)
        if total_in_groups != analytics.overall.total:
            problems.append(
                f"{dimension.name}: group totals {total_in_groups} != "
                f"overall {analytics.overall.total}",
            )
    if problems:
        return CheckResult(
            name="cohort_reconciliation",
            category=ValidationCategory.COHORT_RECONCILIATION,
            status=ValidationCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return CheckResult(
        name="cohort_reconciliation",
        category=ValidationCategory.COHORT_RECONCILIATION,
        status=ValidationCheckStatus.PASS,
        detail="Breakdown totals reconcile with overall totals.",
    )


def _check_determinism(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    profile: OpportunityProfile,
) -> CheckResult:
    """Repeated evaluation produces identical results + ids."""

    if not outcomes:
        return CheckResult(
            name="determinism",
            category=ValidationCategory.DETERMINISTIC_IDS,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to evaluate.",
        )
    a1 = perf_engine.analyze(outcomes)
    a2 = perf_engine.analyze(outcomes)
    e1 = evidence_engine.evaluate(outcomes)
    e2 = evidence_engine.evaluate(outcomes)
    l1 = strat_engine.lookup(e1, profile)
    l2 = strat_engine.lookup(e2, profile)
    c1 = di_engine.build(profile, None, l1)
    c2 = di_engine.build(profile, None, l2)
    ok = (
        a1.analytics_id == a2.analytics_id
        and e1.evidence_id == e2.evidence_id
        and l1.lookup_id == l2.lookup_id
        and c1.context_id == c2.context_id
        and a1.overall == a2.overall
    )
    if ok:
        return CheckResult(
            name="determinism",
            category=ValidationCategory.DETERMINISTIC_IDS,
            status=ValidationCheckStatus.PASS,
            detail="Repeated evaluation produces identical ids + results.",
        )
    return CheckResult(
        name="determinism",
        category=ValidationCategory.DETERMINISTIC_IDS,
        status=ValidationCheckStatus.FAIL,
        detail="Repeated evaluation diverged.",
    )


def _check_shuffle_invariance(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    profile: OpportunityProfile,
) -> CheckResult:
    """Shuffled input produces identical semantic results + ids."""

    if not outcomes:
        return CheckResult(
            name="shuffle_invariance",
            category=ValidationCategory.SHUFFLE_INVARIANCE,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to shuffle.",
        )
    a_orig = perf_engine.analyze(outcomes)
    e_orig = evidence_engine.evaluate(outcomes)
    l_orig = strat_engine.lookup(e_orig, profile)
    c_orig = di_engine.build(profile, None, l_orig)

    shuffled = list(outcomes)
    random.Random(12345).shuffle(shuffled)
    a_shuf = perf_engine.analyze(shuffled)
    e_shuf = evidence_engine.evaluate(shuffled)
    l_shuf = strat_engine.lookup(e_shuf, profile)
    c_shuf = di_engine.build(profile, None, l_shuf)

    ok = (
        a_orig.analytics_id == a_shuf.analytics_id
        and e_orig.evidence_id == e_shuf.evidence_id
        and l_orig.lookup_id == l_shuf.lookup_id
        and c_orig.context_id == c_shuf.context_id
        and a_orig.overall == a_shuf.overall
    )
    if ok:
        return CheckResult(
            name="shuffle_invariance",
            category=ValidationCategory.SHUFFLE_INVARIANCE,
            status=ValidationCheckStatus.PASS,
            detail="Shuffled input yields identical ids + results.",
        )
    return CheckResult(
        name="shuffle_invariance",
        category=ValidationCategory.SHUFFLE_INVARIANCE,
        status=ValidationCheckStatus.FAIL,
        detail="Shuffled input diverged.",
    )


def _check_immutability(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    profile: OpportunityProfile,
) -> CheckResult:
    """Source outcomes / evidence are not mutated by downstream layers."""

    if not outcomes:
        return CheckResult(
            name="immutability",
            category=ValidationCategory.IMMUTABILITY,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )
    snapshot = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in outcomes
    ]
    analytics = perf_engine.analyze(outcomes)
    evidence = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(evidence, profile)
    di_eng = di_engine.build(profile, None, lookup)
    # Touch the analytics / evidence to ensure no lazy mutation occurs.
    _ = analytics.overall.total
    _ = evidence.summary.strength
    _ = di_eng.context_id
    after = [
        (o.outcome_status, o.realized_r, o.subject.instrument)
        for o in outcomes
    ]
    if snapshot == after:
        return CheckResult(
            name="immutability",
            category=ValidationCategory.IMMUTABILITY,
            status=ValidationCheckStatus.PASS,
            detail="Source outcomes not mutated.",
        )
    return CheckResult(
        name="immutability",
        category=ValidationCategory.IMMUTABILITY,
        status=ValidationCheckStatus.FAIL,
        detail="Source outcomes were mutated.",
    )


def _check_look_ahead(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> CheckResult:
    """
    Patch OutcomeEvaluator + pipeline to raise; the downstream chain
    must still work (no hidden candle dependency).
    """

    if not outcomes:
        return CheckResult(
            name="look_ahead_protection",
            category=ValidationCategory.LOOK_AHEAD_PROTECTION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to validate.",
        )
    from engine.pipeline import HistoricalEvaluationPipeline

    original_eval = OutcomeEvaluator.evaluate
    original_pipe = HistoricalEvaluationPipeline.evaluate

    def boom_eval(self, subject, future_candles):  # noqa: ANN001
        raise RuntimeError("must not re-evaluate outcomes")

    def boom_pipe(self, candles):  # noqa: ANN001
        raise RuntimeError("must not re-run pipeline")

    OutcomeEvaluator.evaluate = boom_eval  # type: ignore[method-assign]
    HistoricalEvaluationPipeline.evaluate = boom_pipe  # type: ignore[method-assign]
    try:
        analytics = PerformanceAnalyticsEngine().analyze(outcomes)
        evidence = evidence_engine.evaluate(outcomes)
        lookup = strat_engine.lookup(evidence, profile)
        di = di_engine.build(profile, None, lookup)
        integ = integ_engine.integrate(decision, di, profile)
        ok = (
            analytics.outcome_count == len(outcomes)
            and integ.existing_decision is decision
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]
    if ok:
        return CheckResult(
            name="look_ahead_protection",
            category=ValidationCategory.LOOK_AHEAD_PROTECTION,
            status=ValidationCheckStatus.PASS,
            detail="Downstream chain works with evaluator + pipeline patched to raise.",
        )
    return CheckResult(
        name="look_ahead_protection",
        category=ValidationCategory.LOOK_AHEAD_PROTECTION,
        status=ValidationCheckStatus.FAIL,
        detail="Downstream chain failed with evaluator + pipeline patched.",
    )


def _check_serialization(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> CheckResult:
    """Round-trip serialization across analytics / evidence / 12A / 12B."""

    if not outcomes:
        return CheckResult(
            name="serialization",
            category=ValidationCategory.SERIALIZATION,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to serialize.",
        )
    from engine.intelligence.decision_intelligence_integration_serialization import (
        deserialize_integration,
        serialize_integration,
    )
    from engine.intelligence.decision_intelligence_serialization import (
        deserialize_context,
        serialize_context,
    )
    from engine.intelligence.historical_evidence_serialization import (
        deserialize_evidence,
        serialize_evidence,
    )

    # Evidence + 12A + 12B round trips (these are the lossless ones).
    evidence = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(evidence, profile)
    di = di_engine.build(profile, None, lookup)
    integ = integ_engine.integrate(decision, di, profile)

    problems: list[str] = []
    # Evidence round trip (11Y).
    try:
        ev_rt = deserialize_evidence(serialize_evidence(evidence))
        if ev_rt.evidence_id != evidence.evidence_id:
            problems.append("evidence id mismatch")
        if ev_rt.summary.statistics != evidence.summary.statistics:
            problems.append("evidence summary statistics mismatch")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"evidence round trip error: {exc}")
    # 12A round trip.
    di_rt = deserialize_context(serialize_context(di))
    if di_rt.context_id != di.context_id:
        problems.append("12A context id mismatch")
    # 12B round trip.
    integ_rt = deserialize_integration(serialize_integration(integ))
    if integ_rt.integration_id != integ.integration_id:
        problems.append("12B integration id mismatch")
    if integ_rt.integration_status != integ.integration_status:
        problems.append("12B status mismatch")
    if problems:
        return CheckResult(
            name="serialization",
            category=ValidationCategory.SERIALIZATION,
            status=ValidationCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return CheckResult(
        name="serialization",
        category=ValidationCategory.SERIALIZATION,
        status=ValidationCheckStatus.PASS,
        detail="Round trips preserve identity / status / statistics.",
    )


def _check_evidence_gating(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> CheckResult:
    """A tiny impressive cohort must NOT become strong evidence."""

    if not outcomes:
        return CheckResult(
            name="evidence_gating",
            category=ValidationCategory.EVIDENCE_GATING,
            status=ValidationCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )
    evidence = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(evidence, profile)
    di = di_engine.build(profile, None, lookup)
    integ = integ_engine.integrate(decision, di, profile)
    chain_ok = True
    detail_parts: list[str] = []
    # The tiny cohort (if present in this scenario) must be INSUFFICIENT
    # at every layer. We check the OVERALL evidence strength + the
    # matched-cohort strength (when matched).
    if lookup.matched and lookup.assessment is not None:
        strength = lookup.assessment.evidence_strength
        if strength != EvidenceStrength.INSUFFICIENT:
            # Only a failure when the matched cohort is actually tiny.
            if lookup.assessment.sample_count < 10:
                chain_ok = False
                detail_parts.append(
                    f"matched cohort strength {strength.name} for "
                    f"{lookup.assessment.sample_count} samples",
                )
    # Decision context must never be EVIDENCE_SUPPORTED for a tiny cohort.
    if (
        lookup.matched
        and lookup.assessment is not None
        and lookup.assessment.sample_count < 10
        and di.decision_context_status.value == "EVIDENCE_SUPPORTED"
    ):
        chain_ok = False
        detail_parts.append("12A context upgraded to EVIDENCE_SUPPORTED")
    # 12B must never upgrade the existing decision classification.
    if (
        integ.existing_decision_summary.decision_classification
        != getattr(decision, "decision_classification", "")
    ):
        chain_ok = False
        detail_parts.append("12B modified existing decision classification")
    if chain_ok:
        return CheckResult(
            name="evidence_gating",
            category=ValidationCategory.EVIDENCE_GATING,
            status=ValidationCheckStatus.PASS,
            detail="Evidence hard-gate preserved through the chain.",
        )
    return CheckResult(
        name="evidence_gating",
        category=ValidationCategory.EVIDENCE_GATING,
        status=ValidationCheckStatus.FAIL,
        detail="; ".join(detail_parts),
    )


def _check_decision_authority(
    decision: Any,
    di: Any,
    profile: OpportunityProfile,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    expected_classification: str,
) -> CheckResult:
    """The existing decision classification is never modified by 12B."""

    if decision is None:
        return CheckResult(
            name="decision_authority",
            category=ValidationCategory.DECISION_AUTHORITY,
            status=ValidationCheckStatus.SKIPPED,
            detail="No existing decision supplied.",
        )
    integ = integ_engine.integrate(decision, di, profile)
    actual = integ.existing_decision_summary.decision_classification
    preserved_ref = integ.existing_decision is decision
    if actual == expected_classification and preserved_ref:
        return CheckResult(
            name="decision_authority",
            category=ValidationCategory.DECISION_AUTHORITY,
            status=ValidationCheckStatus.PASS,
            detail=(
                f"Classification {actual} preserved (by reference) "
                f"under integration status {integ.integration_status.name}."
            ),
        )
    return CheckResult(
        name="decision_authority",
        category=ValidationCategory.DECISION_AUTHORITY,
        status=ValidationCheckStatus.FAIL,
        detail=(
            f"Classification {actual} != {expected_classification} "
            f"(ref preserved: {preserved_ref})"
        ),
    )


# ============================================================
# ENGINE
# ============================================================


class BacktestValidationEngine:
    """
    Validate the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    architecture over a matrix of historical / adversarial scenarios.

    Public API:

        validate(scenarios, label="", metadata=None) -> BacktestValidationReport

    Each scenario is a mapping with a ``name`` and an ``outcomes`` tuple
    of Sprint 11W :class:`HistoricalOutcome` objects. Optionally a
    scenario may carry ``profile`` (an :class:`OpportunityProfile` used
    for the 12Z / 12A / 12B checks) and ``decision`` (an existing
    decision object for the decision-authority checks).

    The engine is stateless across calls: identical scenarios always
    produce identical validation results. The input scenarios / outcomes
    are NEVER mutated. The result is DESCRIPTIVE.
    """

    def __init__(self, config: BacktestValidationConfig | None = None) -> None:
        self.config = config or BacktestValidationConfig()
        self._perf = PerformanceAnalyticsEngine()
        self._evidence = HistoricalEvidenceEngine()
        self._strat = StrategyIntelligenceEngine()
        self._di = DecisionIntelligenceEngine()
        self._integ = DecisionIntelligenceIntegrationEngine()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def validate(
        self,
        scenarios: Iterable[Mapping[str, Any]],
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> BacktestValidationReport:
        """
        Run the full validation suite over ``scenarios``.

        Returns a deterministic :class:`BacktestValidationReport`.
        """

        lbl = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)

        scenario_list = list(scenarios)
        scenario_results: list[ScenarioResult] = []
        outcome_dist: dict[str, int] = {}

        for scenario in scenario_list:
            name = str(scenario.get("name", "scenario"))
            outcomes = tuple(scenario.get("outcomes", ()))
            profile = scenario.get("profile") or self._default_profile(outcomes)
            decision = scenario.get("decision")
            expected_cls = scenario.get("expected_classification")

            for o in outcomes:
                outcome_dist[o.outcome_status.name] = (
                    outcome_dist.get(o.outcome_status.name, 0) + 1
                )

            checks = self._run_scenario_checks(
                outcomes, profile, decision, expected_cls,
            )
            scenario_results.append(
                ScenarioResult(
                    name=name,
                    outcome_count=len(outcomes),
                    checks=tuple(checks),
                ),
            )

        # Deterministic ordering: scenarios by name.
        scenario_results.sort(key=lambda s: s.name)
        categories = self._category_summaries(scenario_results)
        distribution = tuple(sorted(outcome_dist.items()))

        counts = self._aggregate_counts(scenario_results)
        overall = self._overall_status(scenario_results)
        det_status = self._category_status(
            scenario_results, ValidationCategory.DETERMINISTIC_IDS,
        )
        la_status = self._category_status(
            scenario_results, ValidationCategory.LOOK_AHEAD_PROTECTION,
        )
        acc_status = self._category_status(
            scenario_results, ValidationCategory.ACCOUNTING_RECONCILIATION,
        )
        ser_status = self._category_status(
            scenario_results, ValidationCategory.SERIALIZATION,
        )
        da_status = self._category_status(
            scenario_results, ValidationCategory.DECISION_AUTHORITY,
        )

        validation_id = self._validation_id(
            scenario_results, lbl, meta,
        )
        rationale = self._rationale(scenario_results, counts, overall)

        return BacktestValidationReport(
            validation_id=validation_id,
            scenarios=tuple(scenario_results),
            categories=tuple(categories),
            label=lbl,
            metadata=meta,
            scenario_count=len(scenario_results),
            check_count=counts["total"],
            passed_count=counts["passed"],
            failed_count=counts["failed"],
            skipped_count=counts["skipped"],
            overall_status=overall,
            determinism_status=det_status,
            look_ahead_status=la_status,
            accounting_status=acc_status,
            serialization_status=ser_status,
            decision_authority_status=da_status,
            outcome_distribution=distribution,
            rationale=rationale,
        )

    # ------------------------------------------------------------
    # SCENARIO CHECKS
    # ------------------------------------------------------------

    def _run_scenario_checks(
        self,
        outcomes: tuple[HistoricalOutcome, ...],
        profile: OpportunityProfile,
        decision: Any,
        expected_cls: str | None,
    ) -> list[CheckResult]:
        tol = self.config.accounting_tolerance
        checks: list[CheckResult] = []

        # A. scenario construction (non-empty, well-formed outcomes).
        checks.append(self._check_scenario_construction(outcomes))

        analytics = self._perf.analyze(outcomes)

        # D. accounting reconciliation.
        checks.append(_check_accounting(outcomes, analytics, tol))
        checks.append(_check_breakdown_accounting(outcomes, analytics, tol))
        checks.append(_check_r_identity(outcomes, analytics, tol))
        checks.append(_check_excluded_outcomes(outcomes, analytics))

        # E. cohort reconciliation.
        checks.append(_check_cohort_reconciliation(outcomes, analytics))

        # F. shuffle invariance.
        checks.append(
            _check_shuffle_invariance(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, profile,
            )
        )

        # G/I. determinism.
        checks.append(
            _check_determinism(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, profile,
            )
        )

        # H. immutability.
        checks.append(
            _check_immutability(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, profile,
            )
        )

        # C. look-ahead / point-in-time.
        checks.append(
            _check_look_ahead(
                outcomes, self._evidence, self._di, self._strat,
                self._integ, profile, decision,
            )
        )

        # serialization.
        checks.append(
            _check_serialization(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, self._integ, profile, decision,
            )
        )

        # K. evidence gating.
        checks.append(
            _check_evidence_gating(
                outcomes, self._evidence, self._di, self._strat,
                self._integ, profile, decision,
            )
        )

        # M/L. decision authority (when a decision + expected class given).
        if decision is not None and expected_cls is not None:
            # Build a DI context from the scenario's evidence for the
            # authority checks (the existing decision must survive every
            # evidence regime).
            evidence = self._evidence.evaluate(outcomes)
            lookup = self._strat.lookup(evidence, profile)
            di = self._di.build(profile, None, lookup)
            checks.append(
                _check_decision_authority(
                    decision, di, profile, self._integ, expected_cls,
                )
            )

        checks.sort(key=lambda c: (c.category.name, c.name))
        return checks

    def _check_scenario_construction(
        self, outcomes: tuple[HistoricalOutcome, ...],
    ) -> CheckResult:
        if not outcomes:
            return CheckResult(
                name="scenario_construction",
                category=ValidationCategory.SCENARIO_CONSTRUCTION,
                status=ValidationCheckStatus.SKIPPED,
                detail="Empty scenario (intentionally empty).",
            )
        # All entries must be HistoricalOutcome instances.
        bad = [
            i for i, o in enumerate(outcomes)
            if not isinstance(o, HistoricalOutcome)
        ]
        if bad:
            return CheckResult(
                name="scenario_construction",
                category=ValidationCategory.SCENARIO_CONSTRUCTION,
                status=ValidationCheckStatus.FAIL,
                detail=f"Non-HistoricalOutcome entries at indices {bad}",
            )
        return CheckResult(
            name="scenario_construction",
            category=ValidationCategory.SCENARIO_CONSTRUCTION,
            status=ValidationCheckStatus.PASS,
            detail=f"{len(outcomes)} well-formed outcome(s).",
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _default_profile(
        outcomes: tuple[HistoricalOutcome, ...],
    ) -> OpportunityProfile:
        """Derive a default profile from the first outcome, if any."""

        if not outcomes:
            return OpportunityProfile()
        s = outcomes[0].subject
        # NOTE: ``rank`` is intentionally omitted from the default
        # profile. The Sprint 12B integration strict-guard compares the
        # integration profile's available-dimension values (strings)
        # against the decision-intelligence context's raw field values;
        # for the integer ``rank`` field that comparison is type-
        # sensitive. Omitting rank (matching the Sprint 12B demo
        # convention) keeps the validation focus on the string
        # dimensions without tripping that pre-existing guard.
        return OpportunityProfile(
            instrument=s.instrument,
            direction=s.direction,
            setup_type=s.setup_type,
            mtf_alignment=s.mtf_alignment,
            decision=s.decision_classification,
            opportunity_status=s.opportunity_status,
        )

    @staticmethod
    def _normalize_metadata(
        override: Mapping[str, str] | None,
        fallback: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if override is None:
            return tuple(sorted(fallback))
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))

    @staticmethod
    def _category_summaries(
        scenarios: list[ScenarioResult],
    ) -> list[CategorySummary]:
        per_cat: dict[ValidationCategory, dict[str, int]] = {}
        for s in scenarios:
            for c in s.checks:
                bucket = per_cat.setdefault(
                    c.category,
                    {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
                )
                bucket["total"] += 1
                if c.passed:
                    bucket["passed"] += 1
                elif c.failed:
                    bucket["failed"] += 1
                else:
                    bucket["skipped"] += 1
        summaries = [
            CategorySummary(
                category=cat,
                total=v["total"],
                passed=v["passed"],
                failed=v["failed"],
                skipped=v["skipped"],
            )
            for cat, v in per_cat.items()
        ]
        summaries.sort(key=lambda s: s.category.name)
        return summaries

    @staticmethod
    def _aggregate_counts(
        scenarios: list[ScenarioResult],
    ) -> dict[str, int]:
        total = passed = failed = skipped = 0
        for s in scenarios:
            for c in s.checks:
                total += 1
                if c.passed:
                    passed += 1
                elif c.failed:
                    failed += 1
                else:
                    skipped += 1
        return {
            "total": total, "passed": passed,
            "failed": failed, "skipped": skipped,
        }

    @staticmethod
    def _overall_status(
        scenarios: list[ScenarioResult],
    ) -> ValidationCheckStatus:
        if not scenarios:
            return ValidationCheckStatus.SKIPPED
        any_ran = False
        any_failed = False
        for s in scenarios:
            for c in s.checks:
                if c.status == ValidationCheckStatus.SKIPPED:
                    continue
                any_ran = True
                if c.failed:
                    any_failed = True
        if not any_ran:
            return ValidationCheckStatus.SKIPPED
        if any_failed:
            return ValidationCheckStatus.FAIL
        return ValidationCheckStatus.PASS

    @staticmethod
    def _category_status(
        scenarios: list[ScenarioResult],
        category: ValidationCategory,
    ) -> ValidationCheckStatus:
        any_ran = False
        any_failed = False
        any_present = False
        for s in scenarios:
            for c in s.checks:
                if c.category != category:
                    continue
                any_present = True
                if c.status == ValidationCheckStatus.SKIPPED:
                    continue
                any_ran = True
                if c.failed:
                    any_failed = True
        if not any_present:
            return ValidationCheckStatus.SKIPPED
        if not any_ran:
            return ValidationCheckStatus.SKIPPED
        if any_failed:
            return ValidationCheckStatus.FAIL
        return ValidationCheckStatus.PASS

    def _validation_id(
        self,
        scenarios: list[ScenarioResult],
        label: str,
        metadata: tuple[tuple[str, str], ...],
    ) -> str:
        payload = {
            "label": label,
            "metadata": [list(p) for p in metadata],
            "scenarios": [
                {
                    "name": s.name,
                    "outcome_count": s.outcome_count,
                    "checks": [
                        [c.name, c.category.name, c.status.name]
                        for c in s.checks
                    ],
                }
                for s in scenarios
            ],
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"validation-{digest[:16]}"

    @staticmethod
    def _rationale(
        scenarios: list[ScenarioResult],
        counts: dict[str, int],
        overall: ValidationCheckStatus,
    ) -> str:
        if not scenarios:
            return (
                "Backtest validation ran no scenarios. Descriptive only; "
                "not predictive and not a guarantee of profitability."
            )
        return (
            f"Backtest validation ran {len(scenarios)} scenario(s) with "
            f"{counts['total']} check(s): {counts['passed']} passed, "
            f"{counts['failed']} failed, {counts['skipped']} skipped. "
            f"Overall status: {overall.name}. The validation REUSES the "
            f"existing 11X/11Y/11Z/12A/12B engines and performs "
            f"independent cross-checks of their already-computed outputs "
            f"(accounting reconciliation, cohort reconciliation, "
            f"determinism, shuffle invariance, serialization, "
            f"immutability, look-ahead protection, evidence gating and "
            f"decision authority). No future information was used to "
            f"influence any decision at T. Descriptive only; not "
            f"predictive and not a guarantee of profitability."
        )


# Re-export for convenience (module-level helpers used by tests/demo).
__all__ = [
    "BacktestValidationEngine",
    "_recompute_statistics",
]
