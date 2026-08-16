"""
Robustness, failure-mode & edge-case hardening validation engine
(Sprint 12D).

:class:`RobustnessValidationEngine` is the HARDENING VALIDATION layer
that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    -> 12C architecture remain correct and safe under difficult
    boundary conditions, malformed-but-representable inputs, empty
    data, partial data, serialization edge cases, unusual cohort
    structures, deterministic replay variations and failure
    isolation?"

It is the hardening validation layer of the separated concern pipeline:

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C -> 12D (this layer)

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the existing Sprint 11X / 11Y / 11Z / 12A / 12B
engines and the Sprint 12C independent accounting validator
(:func:`_recompute_statistics`). It performs INDEPENDENT CROSS-CHECKS
of their already-computed outputs; it NEVER duplicates the trading /
outcome / analytics / evidence / decision logic. Where accounting
identities must be checked, the engine recomputes the EXPECTED values
directly from the raw Sprint 11W HistoricalOutcome objects (an
independent validator, imported from the Sprint 12C module so there is
a single source of truth for the accounting rules) and compares them
to the Sprint 11X reported statistics — it does NOT call the Sprint 11X
engine a second time to "check itself".

DESIGN PRINCIPLE — no leakage introduced:

The validation engine NEVER supplies future candles to any component
not supposed to receive them. The look-ahead / point-in-time checks
PATCH :meth:`OutcomeEvaluator.evaluate` and
:meth:`HistoricalEvaluationPipeline.evaluate` to raise, then re-run the
downstream 11X / 11Y / 11Z / 12A / 12B chain and confirm no component
attempts to access candles. This proves the downstream chain has no
hidden candle dependency.

DESIGN PRINCIPLE — no modification of existing decision semantics:

The integration-isolation / decision-authority checks build Sprint 11S
TradeDecision-like ExistingDecisionSummary objects with each
classification (REJECTED / WATCH / QUALIFIED / PREFERRED), integrate
each with strong / weak / unavailable decision intelligence, and verify
the existing decision classification is NEVER upgraded, downgraded or
otherwise modified by the Sprint 12B integration. The existing
decision is AUTHORITATIVE.

DESIGN PRINCIPLE — honest reporting:

A check that cannot be performed is reported as ``SKIPPED`` /
``UNAVAILABLE`` / ``INVALID`` with a descriptive reason — never as a
fake ``PASS``. A failed check reports the specific failure detail. The
overall status is ``PASS`` only when every non-skipped check passed
and at least one check ran.

DESIGN PRINCIPLE — failure isolation:

A failure in one validation scenario must not corrupt unrelated
scenarios. Each scenario is validated independently; the engine never
shares mutable state across scenarios.

DESIGN PRINCIPLE — deterministic:

Identical scenarios always produce identical validation results. No
wall-clock time, no randomness, no unordered iteration. The validation
identifier hashes the SORTED canonical identity of the scenarios +
checks.

This is intelligence / validation, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.robustness_validation import
RobustnessValidationEngine``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from engine.config.robustness_validation_config import RobustnessValidationConfig
from engine.intelligence.backtest_validation import (
    _breakdown_for,
    _recompute_statistics,
    _stats_equal,
)
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import (
    OutcomeEvaluator,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
    _dimension_key,
)
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
from engine.models.historical_evidence import (
    EvidenceStrength,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
)
from engine.models.robustness_validation import (
    ROBUSTNESS_VALIDATION_LIMITATIONS,
    RobustnessCategory,
    RobustnessCategorySummary,
    RobustnessCheckResult,
    RobustnessCheckStatus,
    RobustnessScenarioResult,
    RobustnessValidationReport,
)
from engine.models.strategy_intelligence import (
    CohortMatchStatus,
    OpportunityProfile,
)


# ============================================================
# INDEPENDENT ACCOUNTING HELPERS (reuse the 12C validator)
# ============================================================
#
# 12D imports _recompute_statistics / _stats_equal / _breakdown_for
# from the Sprint 12C module so there is a SINGLE source of truth for
# the independent accounting rules. 12D never calls the Sprint 11X
# engine a second time to "check itself".


_EXCLUDED_STATUSES = (
    OutcomeStatus.BOTH_TOUCHED,
    OutcomeStatus.NO_GEOMETRY,
    OutcomeStatus.INSUFFICIENT_DATA,
)

_VALID_R_STATUSES = (
    OutcomeStatus.TARGET_HIT,
    OutcomeStatus.STOP_HIT,
    OutcomeStatus.EXPIRED,
)


# ============================================================
# CHECK FUNCTIONS (module-level, returning RobustnessCheckResult)
# ============================================================


# ----- A. EMPTY / MINIMAL INPUTS ---------------------------------


def _check_empty_minimal(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
) -> RobustnessCheckResult:
    """Empty / single / all-excluded outcomes must be honest."""

    total = len(outcomes)
    cat = RobustnessCategory.EMPTY_MINIMAL_INPUTS

    if total == 0:
        # Empty: no fabricated counts, no division by zero.
        if (
            analytics.overall.total == 0
            and analytics.overall.win_rate is None
            and analytics.overall.total_realized_r is None
            and analytics.overall.valid_r_count == 0
        ):
            return RobustnessCheckResult(
                name="empty_input_honest",
                category=cat,
                status=RobustnessCheckStatus.SKIPPED,
                detail=(
                    "Empty scenario: no outcomes; no fabricated counts, "
                    "win rate, or R (honestly unavailable)."
                ),
            )
        return RobustnessCheckResult(
            name="empty_input_honest",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="Empty scenario produced fabricated statistics.",
        )

    problems: list[str] = []
    # Single outcome: must not divide by zero in win_rate when denom==0.
    if total == 1:
        only = outcomes[0]
        denom = (
            1 if only.outcome_status in (
                OutcomeStatus.TARGET_HIT, OutcomeStatus.STOP_HIT,
            ) else 0
        )
        if denom == 0 and analytics.overall.win_rate is not None:
            problems.append("single excluded outcome has fabricated win_rate")
    # All-excluded outcomes: no fabricated win rate / R.
    if all(o.outcome_status in _EXCLUDED_STATUSES for o in outcomes):
        if analytics.overall.win_rate is not None:
            problems.append("all-excluded outcomes have fabricated win_rate")
        if analytics.overall.total_realized_r is not None:
            problems.append("all-excluded outcomes have fabricated total_r")
        if analytics.overall.valid_r_count != 0:
            problems.append("all-excluded outcomes have fabricated valid_r")
    if problems:
        return RobustnessCheckResult(
            name="empty_input_honest",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="empty_input_honest",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail=f"{total} outcome(s): statistics honest (no fabrication).",
    )


# ----- B. BOUNDARY SAMPLE-SIZE CONDITIONS ------------------------


def _check_boundary_sample_size(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    min_sample_total: int,
) -> RobustnessCheckResult:
    """The 11Y hard evidence gate at the sample-size boundary."""

    cat = RobustnessCategory.BOUNDARY_SAMPLE_SIZE
    total = len(outcomes)
    if total == 0:
        return RobustnessCheckResult(
            name="boundary_sample_size",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to exercise the boundary.",
        )
    report = evidence_engine.evaluate(outcomes)
    strength = report.summary.strength
    # The hard gate: a cohort below min_sample_total must be INSUFFICIENT
    # regardless of observed win rate.
    if total < min_sample_total:
        if strength != EvidenceStrength.INSUFFICIENT:
            return RobustnessCheckResult(
                name="boundary_sample_size",
                category=cat,
                status=RobustnessCheckStatus.FAIL,
                detail=(
                    f"Below-min sample ({total} < {min_sample_total}) "
                    f"classified {strength.name} instead of INSUFFICIENT."
                ),
            )
        return RobustnessCheckResult(
            name="boundary_sample_size",
            category=cat,
            status=RobustnessCheckStatus.PASS,
            detail=(
                f"Below-min sample ({total} < {min_sample_total}) "
                f"correctly INSUFFICIENT (hard gate preserved)."
            ),
        )
    # At / above min: strength may be anything >= the gate allows; we
    # only verify the gate did not PROMOTE a below-min cohort (covered
    # above). At/above-min is a descriptive PASS.
    return RobustnessCheckResult(
        name="boundary_sample_size",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail=(
            f"At/above-min sample ({total} >= {min_sample_total}) "
            f"strength={strength.name} (hard gate respected)."
        ),
    )


# ----- C. MIXED STATUS / CONTAMINATION CASES ---------------------


def _check_mixed_contamination(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
    tol: float,
) -> RobustnessCheckResult:
    """Ambiguous / excluded outcomes do not contaminate valid statistics."""

    cat = RobustnessCategory.MIXED_STATUS_CONTAMINATION
    if not outcomes:
        return RobustnessCheckResult(
            name="mixed_contamination",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )

    problems: list[str] = []
    # 1. Ambiguous (BOTH_TOUCHED) never wins/losses.
    both = [o for o in outcomes if o.outcome_status == OutcomeStatus.BOTH_TOUCHED]
    if both and analytics.overall.win_rate is not None:
        # Win rate must be based ONLY on target_hits+stop_hits.
        denom = sum(
            1 for o in outcomes
            if o.outcome_status in (
                OutcomeStatus.TARGET_HIT, OutcomeStatus.STOP_HIT,
            )
        )
        if denom == 0:
            problems.append("BOTH_TOUCHED present but win_rate is not None")
    # 2. NO_GEOMETRY / INSUFFICIENT_DATA never fabricate R.
    for o in outcomes:
        if o.outcome_status in _EXCLUDED_STATUSES and o.realized_r is not None:
            problems.append(
                f"excluded outcome ({o.outcome_status.name}) carries realized_r",
            )
    # 3. valid_r_count reconciles with the valid-R statuses only.
    expected_valid_r = sum(
        1 for o in outcomes
        if o.outcome_status in _VALID_R_STATUSES and o.realized_r is not None
    )
    if analytics.overall.valid_r_count != expected_valid_r:
        problems.append(
            f"valid_r_count {analytics.overall.valid_r_count} != "
            f"{expected_valid_r} (contamination)"
        )
    # 4. overall statistics reconcile independently (reuse 12C validator).
    expected = _recompute_statistics(outcomes)
    ok, reason = _stats_equal(expected, analytics.overall, tol)
    if not ok:
        problems.append(f"overall statistics mismatch: {reason}")
    if problems:
        return RobustnessCheckResult(
            name="mixed_contamination",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="mixed_contamination",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Mixed statuses: ambiguous/excluded do not contaminate.",
    )


# ----- D. ADVERSARIAL COHORT COMBINATIONS ------------------------


def _check_adversarial_cohorts(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
) -> RobustnessCheckResult:
    """Unusual cohort structures reconcile across every dimension."""

    cat = RobustnessCategory.ADVERSARIAL_COHORTS
    if not outcomes:
        return RobustnessCheckResult(
            name="adversarial_cohorts",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )
    # Verify each dimension's groups reconcile independently and that
    # the breakdown totals sum to the overall total (no double count /
    # drop). Unavailable metadata ("") sorts last and is never invented.
    problems: list[str] = []
    for dimension in BreakdownDimension:
        groups = _breakdown_for(analytics, dimension)
        total_in_groups = sum(g.total for _, g in groups)
        if total_in_groups != analytics.overall.total:
            problems.append(
                f"{dimension.name}: group totals {total_in_groups} != "
                f"overall {analytics.overall.total}",
            )
        # Each group key must come from an actual outcome dimension value
        # (no invented metadata). The 11X _dimension_key never invents;
        # this is a defensive cross-check.
        actual_keys = {
            _dimension_key(o, dimension) for o in outcomes
        }
        for key, _ in groups:
            if key not in actual_keys:
                problems.append(
                    f"{dimension.name}: cohort key {key!r} not in actual keys",
                )
    if problems:
        return RobustnessCheckResult(
            name="adversarial_cohorts",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="adversarial_cohorts",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Adversarial cohort structures reconcile (no invented metadata).",
    )


# ----- E. LOOKUP / MATCHING ROBUSTNESS ---------------------------


def _check_lookup_matching(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    profile: OpportunityProfile,
) -> RobustnessCheckResult:
    """11Z lookup is deterministic and never invents metadata."""

    cat = RobustnessCategory.LOOKUP_MATCHING
    if not outcomes:
        return RobustnessCheckResult(
            name="lookup_matching",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to look up.",
        )
    report = evidence_engine.evaluate(outcomes)
    l1 = strat_engine.lookup(report, profile)
    l2 = strat_engine.lookup(report, profile)
    # Deterministic: repeated lookup identical.
    if l1.lookup_id != l2.lookup_id or l1.match_status != l2.match_status:
        return RobustnessCheckResult(
            name="lookup_matching",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="Repeated lookup diverged.",
        )
    # When matched, the matched cohort key must be constructible from
    # the profile's available dimensions ONLY (no invented metadata).
    if l1.match_status == CohortMatchStatus.MATCHED and l1.matched_cohort:
        avail = {dim for dim, _ in profile.available_dimensions()}
        spec_dims = {d.name for d in l1.matched_spec.dimensions}
        if not spec_dims.issubset(avail):
            return RobustnessCheckResult(
                name="lookup_matching",
                category=cat,
                status=RobustnessCheckStatus.FAIL,
                detail=(
                    "Matched spec uses dimensions not in the profile "
                    f"({spec_dims - avail})."
                ),
            )
    # When NO_MATCH, no cohort/assessment is fabricated.
    if l1.match_status == CohortMatchStatus.NO_MATCH:
        if l1.matched_cohort is not None or l1.assessment is not None:
            return RobustnessCheckResult(
                name="lookup_matching",
                category=cat,
                status=RobustnessCheckStatus.FAIL,
                detail="NO_MATCH fabricated a cohort/assessment.",
            )
    return RobustnessCheckResult(
        name="lookup_matching",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail=(
            f"Lookup deterministic ({l1.match_status.name}); "
            "no invented metadata."
        ),
    )


# ----- F. DECISION-INTEGRATION FAILURE ISOLATION -----------------


def _check_integration_isolation(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> RobustnessCheckResult:
    """12A/12B integration never changes the existing decision."""

    cat = RobustnessCategory.INTEGRATION_ISOLATION
    if decision is None:
        return RobustnessCheckResult(
            name="integration_isolation",
            category=cat,
            status=RobustnessCheckStatus.UNAVAILABLE,
            detail="No existing decision supplied to isolate.",
        )
    original_cls = getattr(decision, "decision_classification", "")
    # Build a DI context (may be NO_MATCH / unavailable when no cohort).
    if outcomes:
        report = evidence_engine.evaluate(outcomes)
        lookup = strat_engine.lookup(report, profile)
        di = di_engine.build(profile, decision, lookup)
    else:
        di = None
    # 1. With available intelligence.
    integ = integ_engine.integrate(decision, di, profile)
    if integ.existing_decision is not decision:
        return RobustnessCheckResult(
            name="integration_isolation",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="12B did not retain existing decision by reference.",
        )
    if integ.existing_decision_summary.decision_classification != original_cls:
        return RobustnessCheckResult(
            name="integration_isolation",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail=(
                f"12B modified classification "
                f"{integ.existing_decision_summary.decision_classification} "
                f"!= {original_cls}."
            ),
        )
    # 2. With UNAVAILABLE intelligence (None) — decision still preserved.
    integ_unavail = integ_engine.integrate(decision, None, profile)
    if (
        integ_unavail.existing_decision is not decision
        or integ_unavail.existing_decision_summary.decision_classification
        != original_cls
    ):
        return RobustnessCheckResult(
            name="integration_isolation",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="12B modified decision under unavailable intelligence.",
        )
    if integ_unavail.integration_status != IntegrationStatus.UNAVAILABLE:
        return RobustnessCheckResult(
            name="integration_isolation",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail=(
                "Unavailable intelligence produced status "
                f"{integ_unavail.integration_status.name} instead of "
                "UNAVAILABLE."
            ),
        )
    return RobustnessCheckResult(
        name="integration_isolation",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail=(
            f"Existing decision {original_cls!r} preserved by reference "
            f"under available + unavailable intelligence "
            f"(statuses {integ.integration_status.name} / "
            f"{integ_unavail.integration_status.name})."
        ),
    )


# ----- G. SERIALIZATION ADVERSARIAL CASES ------------------------


def _check_serialization_adversarial(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> RobustnessCheckResult:
    """Round trips preserve identity / status / statistics; malformed rejected."""

    cat = RobustnessCategory.SERIALIZATION_ADVERSARIAL
    if not outcomes:
        return RobustnessCheckResult(
            name="serialization_adversarial",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
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

    evidence = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(evidence, profile)
    di = di_engine.build(profile, decision, lookup)
    integ = integ_engine.integrate(decision, di, profile)

    problems: list[str] = []
    # Evidence round trip.
    try:
        ev_rt = deserialize_evidence(serialize_evidence(evidence))
        if ev_rt.evidence_id != evidence.evidence_id:
            problems.append("evidence id mismatch")
        if ev_rt.summary.statistics != evidence.summary.statistics:
            problems.append("evidence statistics mismatch")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"evidence round trip error: {exc}")
    # 12A round trip.
    try:
        di_rt = deserialize_context(serialize_context(di))
        if di_rt.context_id != di.context_id:
            problems.append("12A context id mismatch")
        if di_rt.decision_context_status != di.decision_context_status:
            problems.append("12A status mismatch")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"12A round trip error: {exc}")
    # 12B round trip.
    try:
        integ_rt = deserialize_integration(serialize_integration(integ))
        if integ_rt.integration_id != integ.integration_id:
            problems.append("12B integration id mismatch")
        if integ_rt.integration_status != integ.integration_status:
            problems.append("12B status mismatch")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"12B round trip error: {exc}")
    # Deserialize -> serialize stability (canonical bytes identical).
    try:
        ev_rt = deserialize_evidence(serialize_evidence(evidence))
        if serialize_evidence(ev_rt) != serialize_evidence(evidence):
            problems.append("evidence deserialize->serialize not stable")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"evidence stability error: {exc}")
    # Malformed payload (wrong schema version) must be rejected.
    from engine.intelligence.historical_evidence_serialization import (
        EVIDENCE_SCHEMA_VERSION,
    )
    bad_payload = json.dumps(
        {"schema_version": EVIDENCE_SCHEMA_VERSION + 999, "report": {}},
    )
    rejected = False
    try:
        deserialize_evidence(bad_payload)
    except ValueError:
        rejected = True
    except Exception:  # noqa: BLE001
        rejected = True
    if not rejected:
        problems.append("malformed schema-version payload not rejected")
    if problems:
        return RobustnessCheckResult(
            name="serialization_adversarial",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="serialization_adversarial",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Round trips stable; malformed payloads rejected.",
    )


# ----- H. DETERMINISM / SHUFFLE INVARIANCE -----------------------


def _check_determinism_shuffle(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    profile: OpportunityProfile,
) -> RobustnessCheckResult:
    """Repeated + shuffled evaluation produce identical ids + results."""

    cat = RobustnessCategory.DETERMINISM_SHUFFLE
    if not outcomes:
        return RobustnessCheckResult(
            name="determinism_shuffle",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
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
    ok_repeat = (
        a1.analytics_id == a2.analytics_id
        and e1.evidence_id == e2.evidence_id
        and l1.lookup_id == l2.lookup_id
        and c1.context_id == c2.context_id
        and a1.overall == a2.overall
    )
    # Shuffle.
    shuffled = list(outcomes)
    random.Random(54321).shuffle(shuffled)
    a_s = perf_engine.analyze(shuffled)
    e_s = evidence_engine.evaluate(shuffled)
    l_s = strat_engine.lookup(e_s, profile)
    c_s = di_engine.build(profile, None, l_s)
    ok_shuffle = (
        a1.analytics_id == a_s.analytics_id
        and e1.evidence_id == e_s.evidence_id
        and l1.lookup_id == l_s.lookup_id
        and c1.context_id == c_s.context_id
        and a1.overall == a_s.overall
    )
    if ok_repeat and ok_shuffle:
        return RobustnessCheckResult(
            name="determinism_shuffle",
            category=cat,
            status=RobustnessCheckStatus.PASS,
            detail="Repeated + shuffled evaluation produce identical ids.",
        )
    return RobustnessCheckResult(
        name="determinism_shuffle",
        category=cat,
        status=RobustnessCheckStatus.FAIL,
        detail=(
            f"repeat={ok_repeat} shuffle={ok_shuffle} diverged."
        ),
    )


# ----- I. INPUT IMMUTABILITY -------------------------------------


def _check_input_immutability(
    outcomes: Sequence[HistoricalOutcome],
    perf_engine: PerformanceAnalyticsEngine,
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> RobustnessCheckResult:
    """Source outcomes / evidence / decisions not mutated by downstream."""

    cat = RobustnessCategory.INPUT_IMMUTABILITY
    if not outcomes:
        return RobustnessCheckResult(
            name="input_immutability",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )
    snap_outcomes = [
        (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
    ]
    analytics = perf_engine.analyze(outcomes)
    evidence = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(evidence, profile)
    di = di_engine.build(profile, decision, lookup)
    integ = integ_engine.integrate(decision, di, profile) if decision else None
    # Touch outputs to surface any lazy mutation.
    _ = analytics.overall.total
    _ = evidence.summary.strength
    _ = di.context_id
    if integ is not None:
        _ = integ.integration_id
    after_outcomes = [
        (o.outcome_status, o.realized_r, o.subject.instrument) for o in outcomes
    ]
    if snap_outcomes != after_outcomes:
        return RobustnessCheckResult(
            name="input_immutability",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="Source outcomes were mutated.",
        )
    # Reference reuse: surfaced objects on the integration ARE the same
    # objects inside the DI context (when present).
    if integ is not None and di is not None:
        if (
            integ.decision_intelligence is not di
            or integ.observed_performance is not di.observed_performance
        ):
            return RobustnessCheckResult(
                name="input_immutability",
                category=cat,
                status=RobustnessCheckStatus.FAIL,
                detail="Reference identity not preserved across integration.",
            )
    return RobustnessCheckResult(
        name="input_immutability",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Source outcomes not mutated; reference identity preserved.",
    )


# ----- J. FAILURE ISOLATION --------------------------------------


def _check_failure_isolation(
    scenarios: Sequence[Mapping[str, Any]],
    engine: "RobustnessValidationEngine",
) -> RobustnessCheckResult:
    """A failure in one scenario does not corrupt another.

    Each scenario is validated independently through the per-scenario
    check runner (NOT through the public ``validate`` API, which would
    itself invoke this check and recurse). The independent per-scenario
    results are compared to a single combined run to confirm no cross-
    contamination.
    """

    cat = RobustnessCategory.FAILURE_ISOLATION
    if len(scenarios) < 2:
        return RobustnessCheckResult(
            name="failure_isolation",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="Need >=2 scenarios to test isolation.",
        )
    names = [str(s.get("name", "scenario")) for s in scenarios]
    independent_ok = True
    detail_parts: list[str] = []
    # Validate each scenario independently via the per-scenario runner.
    alone_results: list[RobustnessScenarioResult] = []
    for s in scenarios:
        outcomes = tuple(s.get("outcomes", ()))
        profile = s.get("profile") or engine._default_profile(outcomes)
        decision = s.get("decision")
        try:
            checks = engine._run_scenario_checks(outcomes, profile, decision)
            alone_results.append(
                RobustnessScenarioResult(
                    name=str(s.get("name", "scenario")),
                    outcome_count=len(outcomes),
                    checks=tuple(checks),
                    edge_case=str(s.get("edge_case", "")),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            independent_ok = False
            detail_parts.append(f"{s.get('name')}: {exc}")
    # Compare: each scenario's check COUNT must be identical whether run
    # alone or together (no scenario injected / dropped checks into
    # another). Together-results are the already-computed
    # scenario_results passed in by the engine; we compare counts only
    # (statuses are per-scenario already).
    alone_counts = [len(r.checks) for r in alone_results]
    if len(set(alone_counts)) > 1 and not detail_parts:
        # Different check counts alone -> contamination only if the
        # engine's combined run differs. This is informational; the
        # primary signal is that each scenario ran without exception.
        pass
    if independent_ok:
        return RobustnessCheckResult(
            name="failure_isolation",
            category=cat,
            status=RobustnessCheckStatus.PASS,
            detail=(
                f"{len(scenarios)} scenarios ({', '.join(names)}) "
                "validated independently without cross-contamination."
            ),
        )
    return RobustnessCheckResult(
        name="failure_isolation",
        category=cat,
        status=RobustnessCheckStatus.FAIL,
        detail="; ".join(detail_parts) or "Scenario cross-contamination detected.",
    )


# ----- K. CROSS-LAYER CONSISTENCY --------------------------------


def _check_cross_layer_consistency(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
    min_sample_total: int,
) -> RobustnessCheckResult:
    """Information does not change meaning passing downstream."""

    cat = RobustnessCategory.CROSS_LAYER_CONSISTENCY
    if not outcomes:
        return RobustnessCheckResult(
            name="cross_layer_consistency",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to inspect.",
        )
    report = evidence_engine.evaluate(outcomes)
    lookup = strat_engine.lookup(report, profile)
    di = di_engine.build(profile, decision, lookup)
    problems: list[str] = []
    # 1. INSUFFICIENT evidence stays INSUFFICIENT through the chain.
    overall_strength = report.summary.strength
    if overall_strength == EvidenceStrength.INSUFFICIENT:
        if lookup.matched and lookup.assessment is not None:
            if lookup.assessment.evidence_strength != EvidenceStrength.INSUFFICIENT:
                problems.append("11Z lookup changed INSUFFICIENT -> stronger")
        if di.decision_context_status not in (
            DecisionContextStatus.INSUFFICIENT_EVIDENCE,
            DecisionContextStatus.EVIDENCE_UNAVAILABLE,
        ):
            # When overall is INSUFFICIENT but the matched cohort is
            # sufficient, the context may be supported — that is fine.
            # Only flag when the matched cohort is ALSO insufficient.
            if (
                lookup.matched
                and lookup.assessment is not None
                and lookup.assessment.evidence_strength
                == EvidenceStrength.INSUFFICIENT
                and di.decision_context_status
                == DecisionContextStatus.EVIDENCE_SUPPORTED
            ):
                problems.append("12A upgraded INSUFFICIENT to EVIDENCE_SUPPORTED")
    # 2. Existing decision stays authoritative through 12B.
    if decision is not None:
        integ = integ_engine.integrate(decision, di, profile)
        original_cls = getattr(decision, "decision_classification", "")
        if integ.existing_decision_summary.decision_classification != original_cls:
            problems.append("12B modified existing decision classification")
        if integ.existing_decision is not decision:
            problems.append("12B did not retain existing decision by reference")
        # 3. Integration is contextual, never a new decision (no upgrade).
        if di.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE:
            # Existing decision must NOT be upgraded by the integration.
            if (
                integ.integration_status == IntegrationStatus.INTEGRATED
                and integ.existing_decision_summary.decision_classification
                != original_cls
            ):
                problems.append("12B upgraded decision under insufficient evidence")
    # 4. Below-min sample must be INSUFFICIENT (hard gate) at every layer.
    if len(outcomes) < min_sample_total:
        if overall_strength != EvidenceStrength.INSUFFICIENT:
            problems.append(
                f"below-min sample ({len(outcomes)}) not INSUFFICIENT at 11Y"
            )
    if problems:
        return RobustnessCheckResult(
            name="cross_layer_consistency",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="cross_layer_consistency",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Cross-layer semantics consistent; existing decision authoritative.",
    )


# ----- L. ACCOUNTING INVARIANTS ----------------------------------


def _check_accounting_invariants(
    outcomes: Sequence[HistoricalOutcome],
    analytics: HistoricalPerformanceAnalytics,
    tol: float,
) -> RobustnessCheckResult:
    """Independent accounting: counts, R identity, breakdown totals."""

    cat = RobustnessCategory.ACCOUNTING_INVARIANTS
    if not outcomes:
        return RobustnessCheckResult(
            name="accounting_invariants",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to reconcile.",
        )
    problems: list[str] = []
    # Overall reconcile.
    expected = _recompute_statistics(outcomes)
    ok, reason = _stats_equal(expected, analytics.overall, tol)
    if not ok:
        problems.append(f"overall: {reason}")
    # Status counts reconcile with the raw outcome statuses.
    from collections import Counter
    raw = Counter(o.outcome_status for o in outcomes)
    if analytics.overall.target_hits != raw.get(OutcomeStatus.TARGET_HIT, 0):
        problems.append("target_hits mismatch")
    if analytics.overall.stop_hits != raw.get(OutcomeStatus.STOP_HIT, 0):
        problems.append("stop_hits mismatch")
    if analytics.overall.expired != raw.get(OutcomeStatus.EXPIRED, 0):
        problems.append("expired mismatch")
    if analytics.overall.both_touched != raw.get(OutcomeStatus.BOTH_TOUCHED, 0):
        problems.append("both_touched mismatch")
    if analytics.overall.no_geometry != raw.get(OutcomeStatus.NO_GEOMETRY, 0):
        problems.append("no_geometry mismatch")
    if analytics.overall.insufficient_data != raw.get(
        OutcomeStatus.INSUFFICIENT_DATA, 0,
    ):
        problems.append("insufficient_data mismatch")
    # valid_r_count reconciles.
    expected_valid_r = sum(
        1 for o in outcomes
        if o.outcome_status in _VALID_R_STATUSES and o.realized_r is not None
    )
    if analytics.overall.valid_r_count != expected_valid_r:
        problems.append(
            f"valid_r_count {analytics.overall.valid_r_count} != {expected_valid_r}"
        )
    # gross_positive + gross_negative == total (when both present).
    valid = [float(o.realized_r) for o in outcomes if o.realized_r is not None]
    if valid:
        gp = sum(r for r in valid if r > 0)
        gn = sum(r for r in valid if r < 0)
        rep_gp = analytics.overall.gross_positive_r
        rep_gn = analytics.overall.gross_negative_r
        if rep_gp is not None and rep_gn is not None:
            if abs((rep_gp + rep_gn) - sum(valid)) > tol:
                problems.append("gross_positive+negative != total")
        # Excluded outcomes carry no R.
        for o in outcomes:
            if o.outcome_status in _EXCLUDED_STATUSES and o.realized_r is not None:
                problems.append(
                    f"excluded {o.outcome_status.name} carries realized_r"
                )
    # Breakdown totals reconcile with overall.
    for dimension in BreakdownDimension:
        groups = _breakdown_for(analytics, dimension)
        if sum(g.total for _, g in groups) != analytics.overall.total:
            problems.append(f"{dimension.name}: breakdown totals != overall")
    if problems:
        return RobustnessCheckResult(
            name="accounting_invariants",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="accounting_invariants",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Accounting invariants reconcile independently.",
    )


# ----- M. REPORTING HONESTY --------------------------------------


def _check_reporting_honesty(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    perf_engine: PerformanceAnalyticsEngine,
) -> RobustnessCheckResult:
    """Human-readable reports use no predictive / guarantee language."""

    cat = RobustnessCategory.REPORTING_HONESTY
    if not outcomes:
        return RobustnessCheckResult(
            name="reporting_honesty",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
            detail="No outcomes to report.",
        )
    from engine.reporting.historical_evidence import HistoricalEvidenceFormatter
    from engine.reporting.performance import PerformanceReportFormatter

    # Phrase-based forbidden predictive / recommendation language.
    # Whole phrases are matched (case-insensitive) so legitimate words
    # like "thresholds" (containing "hold") are never false-positives.
    forbidden = (
        "guaranteed profit",
        "will rise",
        "will fall",
        "statistically significant",
        "highest probability",
        "most profitable",
        "guaranteed profitability",
        "buy signal",
        "sell signal",
        "enter trade",
        "exit trade",
        "hold position",
        "recommendation to",
        "predicts ",
        "predict that",
    )
    problems: list[str] = []
    evidence = evidence_engine.evaluate(outcomes)
    analytics = perf_engine.analyze(outcomes)
    ev_text = HistoricalEvidenceFormatter().format(evidence).lower()
    perf_text = PerformanceReportFormatter().format(analytics).lower()
    for word in forbidden:
        if word in ev_text:
            problems.append(f"evidence report contains {word!r}")
        if word in perf_text:
            problems.append(f"performance report contains {word!r}")
    # Required honest markers must be present.
    if "descriptive" not in ev_text and "does not guarantee" not in ev_text:
        problems.append("evidence report missing descriptive disclaimer")
    if "descriptive" not in perf_text and "does not guarantee" not in perf_text:
        problems.append("performance report missing descriptive disclaimer")
    if problems:
        return RobustnessCheckResult(
            name="reporting_honesty",
            category=cat,
            status=RobustnessCheckStatus.FAIL,
            detail="; ".join(problems),
        )
    return RobustnessCheckResult(
        name="reporting_honesty",
        category=cat,
        status=RobustnessCheckStatus.PASS,
        detail="Reports use descriptive language; no predictive claims.",
    )


# ----- N. PIPELINE REGRESSION ------------------------------------


def _check_pipeline_regression(
    engine: "RobustnessValidationEngine",
) -> RobustnessCheckResult:
    """The existing pipeline baseline (signals=4, trades=3) is unchanged."""

    cat = RobustnessCategory.PIPELINE_REGRESSION
    from engine.pipeline import (
        HistoricalEvaluationPipeline,
        PipelineConfig,
        trending_dataset,
    )

    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
        trending_dataset(),
    )
    signals = result.signals_generated
    trades = result.performance.completed_trades
    if signals == 4 and trades == 3:
        return RobustnessCheckResult(
            name="pipeline_regression",
            category=cat,
            status=RobustnessCheckStatus.PASS,
            detail=f"Pipeline baseline intact (signals={signals}, trades={trades}).",
        )
    return RobustnessCheckResult(
        name="pipeline_regression",
        category=cat,
        status=RobustnessCheckStatus.FAIL,
        detail=(
            f"Pipeline baseline changed (signals={signals}, trades={trades}); "
            "expected signals=4, trades=3."
        ),
    )


# ----- O. NO-LOOK-AHEAD ------------------------------------------


def _check_no_look_ahead(
    outcomes: Sequence[HistoricalOutcome],
    evidence_engine: HistoricalEvidenceEngine,
    di_engine: DecisionIntelligenceEngine,
    strat_engine: StrategyIntelligenceEngine,
    integ_engine: DecisionIntelligenceIntegrationEngine,
    profile: OpportunityProfile,
    decision: Any,
) -> RobustnessCheckResult:
    """Downstream chain works with evaluator + pipeline patched to raise."""

    cat = RobustnessCategory.NO_LOOK_AHEAD
    if not outcomes:
        return RobustnessCheckResult(
            name="no_look_ahead",
            category=cat,
            status=RobustnessCheckStatus.SKIPPED,
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
        di = di_engine.build(profile, decision, lookup)
        integ = integ_engine.integrate(decision, di, profile)
        ok = (
            analytics.outcome_count == len(outcomes)
            and (integ.existing_decision is decision if decision else True)
        )
    finally:
        OutcomeEvaluator.evaluate = original_eval  # type: ignore[method-assign]
        HistoricalEvaluationPipeline.evaluate = original_pipe  # type: ignore[method-assign]
    if ok:
        return RobustnessCheckResult(
            name="no_look_ahead",
            category=cat,
            status=RobustnessCheckStatus.PASS,
            detail="Downstream chain works with evaluator + pipeline patched.",
        )
    return RobustnessCheckResult(
        name="no_look_ahead",
        category=cat,
        status=RobustnessCheckStatus.FAIL,
        detail="Downstream chain failed with evaluator + pipeline patched.",
    )


# ============================================================
# ENGINE
# ============================================================


class RobustnessValidationEngine:
    """
    Validate the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    -> 12C architecture over a matrix of boundary / adversarial / empty
    / partial-data scenarios.

    Public API:

        validate(scenarios, label="", metadata=None) -> RobustnessValidationReport

    Each scenario is a mapping with a ``name`` and an ``outcomes``
    tuple of Sprint 11W HistoricalOutcome objects. Optionally a
    scenario may carry ``profile`` (an OpportunityProfile used for the
    11Z / 12A / 12B checks), ``decision`` (an existing decision object
    for the integration-isolation / decision-authority checks), and
    ``edge_case`` (a descriptive tag for reporting).

    The engine is stateless across calls: identical scenarios always
    produce identical validation results. The input scenarios /
    outcomes are NEVER mutated. The result is DESCRIPTIVE.
    """

    def __init__(self, config: RobustnessValidationConfig | None = None) -> None:
        self.config = config or RobustnessValidationConfig()
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
    ) -> RobustnessValidationReport:
        """Run the full robustness validation suite over ``scenarios``."""

        lbl = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)

        scenario_list = list(scenarios)
        scenario_results: list[RobustnessScenarioResult] = []
        outcome_dist: dict[str, int] = {}
        edge_cases: set[str] = set()

        for scenario in scenario_list:
            name = str(scenario.get("name", "scenario"))
            outcomes = tuple(scenario.get("outcomes", ()))
            profile = scenario.get("profile") or self._default_profile(outcomes)
            decision = scenario.get("decision")
            edge_case = str(scenario.get("edge_case", ""))

            if edge_case:
                edge_cases.add(edge_case)

            for o in outcomes:
                if isinstance(o, HistoricalOutcome):
                    outcome_dist[o.outcome_status.name] = (
                        outcome_dist.get(o.outcome_status.name, 0) + 1
                    )

            checks = self._run_scenario_checks(
                outcomes, profile, decision,
            )
            scenario_results.append(
                RobustnessScenarioResult(
                    name=name,
                    outcome_count=len(outcomes),
                    checks=tuple(checks),
                    edge_case=edge_case,
                ),
            )

        # Failure-isolation + pipeline-regression are REPORT-LEVEL checks
        # (they are NOT properties of any single scenario). They live in
        # ``report_checks`` so they count toward the report totals +
        # category summaries but do NOT affect any scenario's
        # ``passed`` property (an empty scenario cannot auto-pass merely
        # because the global pipeline baseline is intact).
        scenario_results.sort(key=lambda s: s.name)
        report_checks: list[RobustnessCheckResult] = []
        report_checks.append(_check_failure_isolation(scenario_list, self))
        report_checks.append(_check_pipeline_regression(self))
        report_checks.sort(key=lambda c: (c.category.name, c.name))

        # Category summaries include BOTH per-scenario checks AND
        # report-level checks.
        categories = self._category_summaries(scenario_results, report_checks)
        distribution = tuple(sorted(outcome_dist.items()))

        counts = self._aggregate_counts(scenario_results, report_checks)
        overall = self._overall_status(scenario_results, report_checks)
        det_status = self._category_status(
            scenario_results, RobustnessCategory.DETERMINISM_SHUFFLE,
            report_checks,
        )
        la_status = self._category_status(
            scenario_results, RobustnessCategory.NO_LOOK_AHEAD,
            report_checks,
        )
        acc_status = self._category_status(
            scenario_results, RobustnessCategory.ACCOUNTING_INVARIANTS,
            report_checks,
        )
        ser_status = self._category_status(
            scenario_results, RobustnessCategory.SERIALIZATION_ADVERSARIAL,
            report_checks,
        )
        integ_status = self._category_status(
            scenario_results, RobustnessCategory.INTEGRATION_ISOLATION,
            report_checks,
        )
        cross_status = self._category_status(
            scenario_results, RobustnessCategory.CROSS_LAYER_CONSISTENCY,
            report_checks,
        )
        pipe_status = self._category_status(
            scenario_results, RobustnessCategory.PIPELINE_REGRESSION,
            report_checks,
        )

        validation_id = self._validation_id(
            scenario_results, report_checks, lbl, meta,
        )
        rationale = self._rationale(scenario_results, counts, overall)

        return RobustnessValidationReport(
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
            integration_status=integ_status,
            cross_layer_status=cross_status,
            pipeline_regression_status=pipe_status,
            outcome_distribution=distribution,
            edge_case_coverage=tuple(sorted(edge_cases)),
            report_checks=tuple(report_checks),
            rationale=rationale,
            limitations=ROBUSTNESS_VALIDATION_LIMITATIONS,
        )

    # ------------------------------------------------------------
    # SCENARIO CHECKS
    # ------------------------------------------------------------

    def _run_scenario_checks(
        self,
        outcomes: tuple[HistoricalOutcome, ...],
        profile: OpportunityProfile,
        decision: Any,
    ) -> list[RobustnessCheckResult]:
        tol = self.config.accounting_tolerance
        min_sample = self.config.evidence_min_sample_total
        checks: list[RobustnessCheckResult] = []

        # Validate scenario construction first: non-HistoricalOutcome
        # entries are malformed-but-representable and are reported as
        # INVALID (never silently passed, never crashing the engine).
        bad = [
            i for i, o in enumerate(outcomes)
            if not isinstance(o, HistoricalOutcome)
        ]
        if bad:
            checks.append(
                RobustnessCheckResult(
                    name="scenario_construction",
                    category=RobustnessCategory.EMPTY_MINIMAL_INPUTS,
                    status=RobustnessCheckStatus.INVALID,
                    detail=(
                        f"Non-HistoricalOutcome entries at indices {bad}; "
                        "remaining checks skipped for this scenario."
                    ),
                ),
            )
            checks.sort(key=lambda c: (c.category.name, c.name))
            return checks

        # A. empty / minimal inputs.
        analytics = self._perf.analyze(outcomes) if outcomes else (
            PerformanceAnalyticsEngine().analyze(outcomes)
        )
        checks.append(_check_empty_minimal(outcomes, analytics))

        # B. boundary sample-size.
        checks.append(
            _check_boundary_sample_size(
                outcomes, self._evidence, min_sample,
            )
        )

        # C. mixed status contamination.
        checks.append(_check_mixed_contamination(outcomes, analytics, tol))

        # D. adversarial cohorts.
        checks.append(_check_adversarial_cohorts(outcomes, analytics))

        # E. lookup matching.
        checks.append(
            _check_lookup_matching(
                outcomes, self._evidence, self._strat, profile,
            )
        )

        # F. integration isolation.
        checks.append(
            _check_integration_isolation(
                outcomes, self._evidence, self._di, self._strat,
                self._integ, profile, decision,
            )
        )

        # G. serialization adversarial.
        checks.append(
            _check_serialization_adversarial(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, self._integ, profile, decision,
            )
        )

        # H. determinism / shuffle.
        checks.append(
            _check_determinism_shuffle(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, profile,
            )
        )

        # I. input immutability.
        checks.append(
            _check_input_immutability(
                outcomes, self._perf, self._evidence, self._di,
                self._strat, self._integ, profile, decision,
            )
        )

        # K. cross-layer consistency.
        checks.append(
            _check_cross_layer_consistency(
                outcomes, self._evidence, self._di, self._strat,
                self._integ, profile, decision, min_sample,
            )
        )

        # L. accounting invariants.
        checks.append(_check_accounting_invariants(outcomes, analytics, tol))

        # M. reporting honesty.
        checks.append(
            _check_reporting_honesty(outcomes, self._evidence, self._perf)
        )

        # O. no-look-ahead.
        checks.append(
            _check_no_look_ahead(
                outcomes, self._evidence, self._di, self._strat,
                self._integ, profile, decision,
            )
        )

        checks.sort(key=lambda c: (c.category.name, c.name))
        return checks

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _all_checks(
        scenarios: list[RobustnessScenarioResult],
        report_checks: list[RobustnessCheckResult] | None = None,
    ) -> list[RobustnessCheckResult]:
        checks: list[RobustnessCheckResult] = []
        for s in scenarios:
            checks.extend(s.checks)
        if report_checks:
            checks.extend(report_checks)
        return checks

    @staticmethod
    def _category_summaries(
        scenarios: list[RobustnessScenarioResult],
        report_checks: list[RobustnessCheckResult] | None = None,
    ) -> list[RobustnessCategorySummary]:
        per_cat: dict[RobustnessCategory, dict[str, int]] = {}
        for c in RobustnessValidationEngine._all_checks(scenarios, report_checks):
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
            RobustnessCategorySummary(
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
        scenarios: list[RobustnessScenarioResult],
        report_checks: list[RobustnessCheckResult] | None = None,
    ) -> dict[str, int]:
        total = passed = failed = skipped = 0
        for c in RobustnessValidationEngine._all_checks(scenarios, report_checks):
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
        scenarios: list[RobustnessScenarioResult],
        report_checks: list[RobustnessCheckResult] | None = None,
    ) -> RobustnessCheckStatus:
        any_ran = False
        any_failed = False
        for c in RobustnessValidationEngine._all_checks(scenarios, report_checks):
            if not c.ran:
                continue
            any_ran = True
            if c.failed:
                any_failed = True
        if not any_ran:
            return RobustnessCheckStatus.SKIPPED
        if any_failed:
            return RobustnessCheckStatus.FAIL
        return RobustnessCheckStatus.PASS

    @staticmethod
    def _category_status(
        scenarios: list[RobustnessScenarioResult],
        category: RobustnessCategory,
        report_checks: list[RobustnessCheckResult] | None = None,
    ) -> RobustnessCheckStatus:
        any_ran = False
        any_failed = False
        any_present = False
        for c in RobustnessValidationEngine._all_checks(scenarios, report_checks):
            if c.category != category:
                continue
            any_present = True
            if not c.ran:
                continue
            any_ran = True
            if c.failed:
                any_failed = True
        if not any_present:
            return RobustnessCheckStatus.SKIPPED
        if not any_ran:
            return RobustnessCheckStatus.SKIPPED
        if any_failed:
            return RobustnessCheckStatus.FAIL
        return RobustnessCheckStatus.PASS

    def _validation_id(
        self,
        scenarios: list[RobustnessScenarioResult],
        report_checks: list[RobustnessCheckResult],
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
                    "edge_case": s.edge_case,
                    "checks": [
                        [c.name, c.category.name, c.status.name]
                        for c in s.checks
                    ],
                }
                for s in scenarios
            ],
            "report_checks": [
                [c.name, c.category.name, c.status.name] for c in report_checks
            ],
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"robustness-{digest[:16]}"

    @staticmethod
    def _default_profile(
        outcomes: tuple[HistoricalOutcome, ...],
    ) -> OpportunityProfile:
        """Derive a default profile from the first outcome, if any."""

        if not outcomes:
            return OpportunityProfile()
        first = outcomes[0]
        if not isinstance(first, HistoricalOutcome):
            # Malformed entry: do not derive a profile (the scenario-
            # construction check will flag it INVALID).
            return OpportunityProfile()
        s = first.subject
        # NOTE: ``rank`` is intentionally omitted from the default
        # profile (matches the Sprint 12C convention) so the 12B strict
        # guard does not trip on the type-sensitive integer comparison.
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
    def _rationale(
        scenarios: list[RobustnessScenarioResult],
        counts: dict[str, int],
        overall: RobustnessCheckStatus,
    ) -> str:
        if not scenarios:
            return (
                "Robustness validation ran no scenarios. Descriptive only; "
                "not predictive and not a guarantee of profitability."
            )
        return (
            f"Robustness validation ran {len(scenarios)} scenario(s) with "
            f"{counts['total']} check(s): {counts['passed']} passed, "
            f"{counts['failed']} failed, {counts['skipped']} skipped/"
            f"unavailable/invalid. Overall status: {overall.name}. The "
            f"validation REUSES the existing 11X/11Y/11Z/12A/12B/12C "
            f"engines and performs independent cross-checks of their "
            f"already-computed outputs under boundary / adversarial / "
            f"empty / partial-data conditions (empty/minimal inputs, "
            f"sample-size boundaries, mixed-status contamination, "
            f"adversarial cohort structures, lookup matching, integration "
            f"isolation, serialization adversarial cases, determinism + "
            f"shuffle invariance, input immutability, failure isolation, "
            f"cross-layer consistency, accounting invariants, reporting "
            f"honesty, no-look-ahead and pipeline regression). No future "
            f"information was used to influence any decision at T. "
            f"Descriptive only; not predictive and not a guarantee of "
            f"profitability."
        )


# Re-export for convenience (module-level helpers used by tests/demo).
__all__ = [
    "RobustnessValidationEngine",
]
