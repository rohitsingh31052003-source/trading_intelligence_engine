"""
Evidence-conditioned strategy intelligence engine (Sprint 11Z).

:class:`StrategyIntelligenceEngine` consumes the ALREADY-COMPUTED
Sprint 11X performance analytics and Sprint 11Y evidence-strength
outputs and produces structured, conservative strategy assessments.
It is the strategy-intelligence layer of the separated concern
pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)
    7. MARKET SCANNER               (Sprint 11U)
    8. HISTORICAL REPLAY            (Sprint 11V)
    9. OUTCOME EVALUATION           (Sprint 11W)
   10. PERFORMANCE ANALYTICS        (Sprint 11X)
   11. EVIDENCE / VALIDATION        (Sprint 11Y)
   12. STRATEGY INTELLIGENCE        (Sprint 11Z)  <- this layer

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the Sprint 11X :class:`HistoricalPerformanceStatistics`
and the Sprint 11Y :class:`EvidenceStrength` /
:class:`HistoricalEvidenceCohort` / :class:`HistoricalEvidenceReport` /
:class:`CohortSpec` / :data:`SUPPORTED_COHORT_SPECS`. It does NOT
recompute any performance statistics, does NOT re-classify evidence
strength, does NOT re-evaluate outcomes, does NOT re-read candles,
and does NOT use future information. It adds the conservative
strategy-INTERPRETATION layer on top.

The core classification is the FIXED, one-to-one
:data:`~engine.models.strategy_intelligence.EVIDENCE_TO_STRATEGY`
mapping from the reused evidence strength to the strategy assessment
status. A tiny cohort with an impressive observed win rate is NEVER
promoted to strong historical support, because the underlying Sprint
11Y strength hard-gates on sample size (Sprint 11Z inherits that gate
unchanged).

DESIGN PRINCIPLE — three separate concerns:

The assessment surfaces, WITHOUT merging:

* the OBSERVED historical result (reused statistics),
* the EVIDENCE STRENGTH (reused strength), and
* the STRATEGY INTERPRETATION (status + interpretation / limitations
  text).

These are never conflated in the model or in the report.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY:

* No matching cohort (lookup) -> :attr:`CohortMatchStatus.NO_MATCH`
  with ``matched_cohort = None`` and ``assessment = None``.
* Insufficient sample -> :attr:`StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE`
  (propagated from :attr:`EvidenceStrength.INSUFFICIENT`).
* Unavailable metric -> ``None`` (delegated to the reused statistics;
  never fabricated).
* ``BOTH_TOUCHED`` / ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` outcomes
  are preserved exactly by the reused statistics; Sprint 11Z inherits
  that honesty unchanged.
* A missing cohort in a comparison is recorded via
  ``cohort_a_present`` / ``cohort_b_present``; all metrics are
  ``None`` — never fabricated.

DESIGN PRINCIPLE — no statistical claims:

The comparison does NOT claim that one cohort is statistically
superior to another. No statistical procedure exists in the project
to support such a claim. Relative observations (e.g. "cohort A has
more observations") are descriptive only.

DESIGN PRINCIPLE — no leakage:

The engine consumes ALREADY-COMPUTED Sprint 11X / 11Y evidence. It
never inspects future market candles, never re-evaluates outcomes
using future data, and never modifies the historical replay
semantics established in Sprint 11V / 11W. The point-in-time
correctness is preserved unchanged.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock
time, no randomness, no unordered iteration. Identifiers hash the
SORTED canonical identity so a shuffled-equivalent input yields the
same id. Cohort selection (lookup) is deterministic (most specific
matching spec, tie-broken by :data:`SUPPORTED_COHORT_SPECS` order).

This is intelligence / analysis, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via
full paths, e.g. ``from engine.intelligence.strategy_intelligence
import StrategyIntelligenceEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from engine.config.strategy_intelligence_config import StrategyIntelligenceConfig
from engine.intelligence.historical_evidence import SUPPORTED_COHORT_SPECS
from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceCohort,
    HistoricalEvidenceReport,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    EVIDENCE_TO_STRATEGY,
    CohortComparison,
    CohortComparisonMetric,
    CohortMatchStatus,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
    StrategyEvidenceAssessment,
)


# ============================================================
# COHORT LOOKUP HELPERS
# ============================================================


#: The canonical Sprint 11X / 11Y dimension order, reused so the
#: lookup iterates dimensions deterministically.
_DIMENSION_ORDER: tuple[BreakdownDimension, ...] = (
    BreakdownDimension.INSTRUMENT,
    BreakdownDimension.DIRECTION,
    BreakdownDimension.MTF_ALIGNMENT,
    BreakdownDimension.SETUP_TYPE,
    BreakdownDimension.DECISION,
    BreakdownDimension.OPPORTUNITY_STATUS,
    BreakdownDimension.OPPORTUNITY_RANK,
)


def _profile_value(profile: OpportunityProfile, dim: BreakdownDimension) -> str:
    """
    The cohort-key value a profile carries for one dimension, using the
    SAME convention as the Sprint 11X ``_dimension_key`` (``""`` for
    unavailable; ``str(rank)`` for a non-zero rank). This keeps the
    lookup key compatible with the already-computed 11Y cohort keys.
    """

    if dim == BreakdownDimension.INSTRUMENT:
        return profile.instrument or ""
    if dim == BreakdownDimension.DIRECTION:
        return profile.direction or ""
    if dim == BreakdownDimension.MTF_ALIGNMENT:
        return profile.mtf_alignment or ""
    if dim == BreakdownDimension.SETUP_TYPE:
        return profile.setup_type or ""
    if dim == BreakdownDimension.DECISION:
        return profile.decision or ""
    if dim == BreakdownDimension.OPPORTUNITY_STATUS:
        return profile.opportunity_status or ""
    if dim == BreakdownDimension.OPPORTUNITY_RANK:
        return str(profile.rank) if profile.rank != 0 else ""
    return ""


def _expected_key(profile: OpportunityProfile, spec: CohortSpec) -> str:
    """The expected cohort key for ``profile`` under ``spec``."""

    return "|".join(_profile_value(profile, dim) for dim in spec.dimensions)


def _spec_matches_profile(
    spec: CohortSpec, profile: OpportunityProfile,
) -> bool:
    """
    Whether a supported ``spec`` is fully satisfiable by the profile's
    available (non-empty) dimension values. A spec is satisfiable when
    EVERY dimension it requires has a non-empty profile value — so the
    lookup never matches on invented metadata.
    """

    return all(_profile_value(profile, dim) != "" for dim in spec.dimensions)


def _spec_match_count(
    spec: CohortSpec, profile: OpportunityProfile,
) -> int:
    """Number of dimensions in ``spec`` the profile actually provides."""

    return sum(
        1 for dim in spec.dimensions if _profile_value(profile, dim) != ""
    )


def _find_cohort(
    report: HistoricalEvidenceReport,
    spec: CohortSpec,
    key: str,
) -> HistoricalEvidenceCohort | None:
    """Find a cohort by (spec, key) in an already-computed 11Y report."""

    for breakdown in report.breakdowns:
        if breakdown.spec.dimensions != spec.dimensions:
            continue
        for cohort in breakdown.cohorts:
            if cohort.key == key:
                return cohort
    return None


def _select_lookup_spec(
    profile: OpportunityProfile,
    max_dimensions: int,
) -> CohortSpec | None:
    """
    Deterministically select the most specific supported cohort spec
    satisfiable by ``profile``.

    Preference order (deterministic):

    1. The spec with the MOST matching dimensions (most specific),
       capped at ``max_dimensions`` (mirrors the Sprint 11Y controlled
       composite-cohort limit so the lookup never expands beyond the
       supported cohort specs).
    2. Ties broken by :data:`SUPPORTED_COHORT_SPECS` order.

    A spec is a candidate only when it is fully satisfiable (every
    required dimension has a non-empty profile value) AND its dimension
    count is within ``max_dimensions``. Returns ``None`` when no
    candidate exists (the profile carries no usable characteristic).
    """

    candidates: list[tuple[int, int, CohortSpec]] = []
    for index, spec in enumerate(SUPPORTED_COHORT_SPECS):
        if len(spec.dimensions) > max_dimensions:
            continue
        if not _spec_matches_profile(spec, profile):
            continue
        match_count = _spec_match_count(spec, profile)
        candidates.append((match_count, index, spec))

    if not candidates:
        return None

    # Most matching dimensions first, then SUPPORTED_COHORT_SPECS order.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


# ============================================================
# INTERPRETATION + LIMITATIONS WORDING
# ============================================================


def _observed_context(
    stats: HistoricalPerformanceStatistics,
    config: StrategyIntelligenceConfig,
) -> str:
    """
    Descriptive OBSERVED-RESULT context for the interpretation wording.
    DESCRIPTIVE CONTEXT ONLY — it never upgrades the assessment status
    (the reused evidence strength does that).
    """

    parts: list[str] = []
    if stats.win_rate is not None:
        favorable = stats.win_rate >= config.favorable_win_rate
        parts.append(
            f"observed win rate {stats.win_rate:.2f} "
            f"({'favourable' if favorable else 'not favourable'} "
            f"descriptively)",
        )
    else:
        parts.append("observed win rate unavailable (no resolved target/stop)")
    if stats.average_realized_r is not None:
        favorable = stats.average_realized_r >= config.favorable_avg_r
        parts.append(
            f"average realized R {stats.average_realized_r:.2f} "
            f"({'favourable' if favorable else 'not favourable'} "
            f"descriptively)",
        )
    if stats.profit_factor is not None:
        favorable = stats.profit_factor >= config.favorable_profit_factor
        parts.append(
            f"profit factor {stats.profit_factor:.2f} "
            f"({'favourable' if favorable else 'not favourable'} "
            f"descriptively)",
        )
    return "; ".join(parts) if parts else "no observed metrics available"


_STATUS_INTERPRETATION: dict[StrategyAssessmentStatus, str] = {
    StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE: (
        "The historical evidence for this cohort is INSUFFICIENT: the "
        "sample size is below the configured minimum. The observed "
        "metrics are reported but must NOT be treated as reliable "
        "evidence. No strategy-level conclusion is supported."
    ),
    StrategyAssessmentStatus.LIMITED_EVIDENCE: (
        "The historical evidence for this cohort is LIMITED: some "
        "observation exists but not enough resolved / valid-R "
        "observation. The evidence is directional at best and should "
        "be treated cautiously; no confident strategy conclusion is "
        "supported."
    ),
    StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE: (
        "The historical evidence for this cohort is SUPPORTIVE: a "
        "meaningful amount of observation supports the cohort's "
        "metrics. The evidence is usable for a conservative strategy "
        "interpretation, but not yet strong. Still descriptive and not "
        "predictive."
    ),
    StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT: (
        "The historical evidence for this cohort provides STRONGER "
        "HISTORICAL SUPPORT: a substantial amount of observation "
        "supports the cohort's metrics. Even the strongest historical "
        "support is DESCRIPTIVE and does NOT guarantee future "
        "performance or imply live-trading readiness."
    ),
}


def _limitations(
    strength: EvidenceStrength,
    stats: HistoricalPerformanceStatistics,
) -> str:
    """Descriptive limitations statement."""

    parts: list[str] = [
        f"Evidence strength {strength.name} is driven primarily by "
        f"sample size and resolved observation counts (hard-gated by "
        f"Sprint 11Y); a small sample is never promoted to stronger "
        f"evidence merely because its observed win rate is high.",
        f"Sample size {stats.total}, resolved {stats.resolved}, valid-R "
        f"{stats.valid_r_count}.",
        "No statistical hypothesis test was performed.",
        "Historical evidence is descriptive and does not guarantee "
        "future performance.",
    ]
    if stats.both_touched > 0:
        parts.append(
            f"{stats.both_touched} ambiguous BOTH_TOUCHED outcome(s) "
            "are excluded from win/loss and R aggregates (never a "
            "fabricated win/loss).",
        )
    if stats.no_geometry > 0:
        parts.append(
            f"{stats.no_geometry} NO_GEOMETRY outcome(s) carry no "
            "fabricated R values.",
        )
    if stats.insufficient_data > 0:
        parts.append(
            f"{stats.insufficient_data} INSUFFICIENT_DATA outcome(s) "
            "carry no directional conclusion.",
        )
    return " ".join(parts)


# ============================================================
# DETERMINISTIC IDS
# ============================================================


def _stats_identity(stats: HistoricalPerformanceStatistics) -> dict[str, Any]:
    """A deterministic, JSON-safe identity for reused statistics."""

    return {
        "total": stats.total,
        "resolved": stats.resolved,
        "target_hits": stats.target_hits,
        "stop_hits": stats.stop_hits,
        "expired": stats.expired,
        "both_touched": stats.both_touched,
        "no_geometry": stats.no_geometry,
        "insufficient_data": stats.insufficient_data,
        "win_rate": stats.win_rate,
        "loss_rate": stats.loss_rate,
        "expiration_rate": stats.expiration_rate,
        "ambiguous_rate": stats.ambiguous_rate,
        "total_realized_r": stats.total_realized_r,
        "average_realized_r": stats.average_realized_r,
        "median_realized_r": stats.median_realized_r,
        "gross_positive_r": stats.gross_positive_r,
        "gross_negative_r": stats.gross_negative_r,
        "profit_factor": stats.profit_factor,
        "valid_r_count": stats.valid_r_count,
        "average_mfe": stats.average_mfe,
        "average_mae": stats.average_mae,
        "average_mfe_r": stats.average_mfe_r,
        "average_mae_r": stats.average_mae_r,
    }


def _assessment_id(
    spec: CohortSpec,
    key: str,
    stats: HistoricalPerformanceStatistics,
    strength: EvidenceStrength,
    status: StrategyAssessmentStatus,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic assessment identifier (``"strat-"`` + sha256[:16])."""

    payload = {
        "spec": [d.name for d in spec.dimensions],
        "key": key,
        "statistics": _stats_identity(stats),
        "evidence_strength": strength.name,
        "assessment_status": status.name,
        "label": label,
        "metadata": [list(p) for p in metadata],
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"strat-{digest[:16]}"


def _comparison_id(
    spec: CohortSpec,
    key_a: str,
    key_b: str,
    a: StrategyEvidenceAssessment | None,
    b: StrategyEvidenceAssessment | None,
) -> str:
    """Deterministic comparison identifier (``"compare-"`` + sha256[:16])."""

    # The comparison id is SYMMETRIC in the two cohorts: it is derived
    # from the SORTED pair of cohort keys and the SORTED pair of
    # assessment ids, so comparing A vs B yields the same id as
    # comparing B vs A. The comparison object itself remains ordered
    # (value_a / value_b for the metrics reflect the caller's order);
    # only the id is order-independent.
    key_pair = sorted([key_a, key_b])

    def _assess_id(x: StrategyEvidenceAssessment | None) -> str:
        return x.assessment_id if x is not None else ""

    assess_pair = sorted([_assess_id(a), _assess_id(b)])

    payload = {
        "spec": [d.name for d in spec.dimensions],
        "keys": key_pair,
        "assessments": assess_pair,
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"compare-{digest[:16]}"


def _lookup_id(
    profile: OpportunityProfile,
    matched_spec: CohortSpec | None,
    matched_key: str,
    matched_strength: str,
) -> str:
    """Deterministic lookup identifier (``"lookup-"`` + sha256[:16])."""

    payload = {
        "profile": [
            [d, v] for d, v in profile.available_dimensions()
        ],
        "matched_spec": (
            [d.name for d in matched_spec.dimensions]
            if matched_spec is not None else None
        ),
        "matched_key": matched_key,
        "matched_strength": matched_strength,
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"lookup-{digest[:16]}"


# ============================================================
# ENGINE
# ============================================================


class StrategyIntelligenceEngine:
    """
    Produce conservative, evidence-conditioned strategy assessments
    from already-computed Sprint 11X / 11Y evidence.

    Public API:

        assess_cohort(cohort, label="", metadata=None) -> StrategyEvidenceAssessment
        assess(report, spec, key, label="", metadata=None) -> StrategyEvidenceAssessment | None
        compare(report, spec, key_a, key_b) -> CohortComparison
        lookup(report, profile, label="", metadata=None) -> OpportunityEvidenceLookup

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The input evidence report / cohorts are
    NEVER mutated. The result is DESCRIPTIVE. It makes no
    profitability, probability, directional prediction,
    statistical-significance, or trading-recommendation claim.
    """

    def __init__(self, config: StrategyIntelligenceConfig | None = None) -> None:
        self.config = config or StrategyIntelligenceConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def assess_cohort(
        self,
        cohort: HistoricalEvidenceCohort,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> StrategyEvidenceAssessment:
        """
        Produce a strategy assessment from a single Sprint 11Y
        :class:`HistoricalEvidenceCohort`.

        Reuses the cohort's statistics + evidence strength verbatim;
        applies the fixed
        :data:`~engine.models.strategy_intelligence.EVIDENCE_TO_STRATEGY`
        mapping. Never recomputes statistics or re-classifies evidence.
        """

        label = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)
        stats = cohort.statistics
        strength = cohort.strength
        status = EVIDENCE_TO_STRATEGY[strength]
        context = _observed_context(stats, self.config)
        interpretation = (
            f"{_STATUS_INTERPRETATION[status]} ({context}) "
            "Descriptive only; not a prediction and not a guarantee of "
            "future performance."
        )
        limitations = _limitations(strength, stats)
        assessment_id = _assessment_id(
            cohort.spec, cohort.key, stats, strength, status, label, meta,
        )
        return StrategyEvidenceAssessment(
            assessment_id=assessment_id,
            spec=cohort.spec,
            cohort_key=cohort.key,
            observed_performance=stats,
            evidence_strength=strength,
            assessment_status=status,
            sample_count=cohort.sample_count,
            resolved_count=cohort.resolved_count,
            valid_r_count=cohort.valid_r_count,
            interpretation=interpretation,
            limitations=limitations,
            label=label,
            metadata=meta,
        )

    def assess(
        self,
        report: HistoricalEvidenceReport,
        spec: CohortSpec,
        key: str,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> StrategyEvidenceAssessment | None:
        """
        Produce a strategy assessment for the cohort identified by
        ``spec`` + ``key`` in an already-computed 11Y evidence report.

        Returns ``None`` when no such cohort exists (honest fallback —
        no assessment is fabricated). Never recomputes statistics or
        re-classifies evidence; never re-reads candles.
        """

        cohort = _find_cohort(report, spec, key)
        if cohort is None:
            return None
        return self.assess_cohort(cohort, label=label, metadata=metadata)

    def compare(
        self,
        report: HistoricalEvidenceReport,
        spec: CohortSpec,
        key_a: str,
        key_b: str,
    ) -> CohortComparison:
        """
        Produce a DESCRIPTIVE comparison of two cohorts in an
        already-computed 11Y evidence report.

        The comparison exposes existing observed metrics and evidence
        strengths for both cohorts. It does NOT claim that one cohort
        is statistically superior; no statistical procedure exists in
        the project to support such a claim. When either cohort is
        absent, the comparison records that explicitly and every
        metric is ``None`` (never fabricated).
        """

        cohort_a = _find_cohort(report, spec, key_a)
        cohort_b = _find_cohort(report, spec, key_b)
        present_a = cohort_a is not None
        present_b = cohort_b is not None

        assessment_a = (
            self.assess_cohort(cohort_a) if present_a else None
        )
        assessment_b = (
            self.assess_cohort(cohort_b) if present_b else None
        )

        stats_a = cohort_a.statistics if present_a else None
        stats_b = cohort_b.statistics if present_b else None

        metrics = self._comparison_metrics(stats_a, stats_b)
        notes = self._comparison_notes(
            spec, key_a, key_b, present_a, present_b,
            stats_a, stats_b, assessment_a, assessment_b,
        )
        comparison_id = _comparison_id(
            spec, key_a, key_b, assessment_a, assessment_b,
        )
        return CohortComparison(
            comparison_id=comparison_id,
            spec=spec,
            cohort_a_key=key_a,
            cohort_b_key=key_b,
            cohort_a_present=present_a,
            cohort_b_present=present_b,
            assessment_a=assessment_a,
            assessment_b=assessment_b,
            metrics=metrics,
            notes=notes,
            disclaimer=(
                "Comparison is descriptive only. No statistical "
                "procedure is available to claim one cohort is "
                "superior; relative observations are descriptive. "
                "Historical evidence does not guarantee future "
                "performance."
            ),
        )

    def lookup(
        self,
        report: HistoricalEvidenceReport,
        profile: OpportunityProfile,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> OpportunityEvidenceLookup:
        """
        Look up the historical evidence available for a CURRENT
        opportunity's characteristics.

        Reuses the Sprint 11Y :data:`SUPPORTED_COHORT_SPECS` and the
        already-computed cohorts in ``report``. The lookup selects the
        most specific matching cohort (deterministically) and produces
        a :class:`StrategyEvidenceAssessment` for it. If no matching
        cohort exists, the result is explicit
        (:attr:`CohortMatchStatus.NO_MATCH`, ``matched_cohort = None``,
        ``assessment = None``); no evidence is fabricated.
        """

        label = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)

        spec = _select_lookup_spec(
            profile, self.config.lookup_max_dimensions,
        )
        if spec is None:
            return self._no_match_lookup(
                report, profile, label, meta,
                "No supported cohort spec is satisfiable by the "
                "provided opportunity characteristics (no usable "
                "dimension value supplied).",
            )

        key = _expected_key(profile, spec)
        cohort = _find_cohort(report, spec, key)
        if cohort is None:
            return self._no_match_lookup(
                report, profile, label, meta,
                f"No historical cohort matches spec {spec.label} key "
                f"{key!r} in the evidence report.",
            )

        assessment = self.assess_cohort(cohort, label=label, metadata=metadata)
        limitations = self._lookup_limitations(
            profile, spec, cohort, matched=True,
        )
        lookup_id = _lookup_id(
            profile, spec, cohort.key, cohort.strength.name,
        )
        return OpportunityEvidenceLookup(
            lookup_id=lookup_id,
            profile=profile,
            match_status=CohortMatchStatus.MATCHED,
            matched_spec=spec,
            matched_cohort=cohort,
            assessment=assessment,
            limitations=limitations,
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _normalize_metadata(
        override: Mapping[str, str] | None,
        fallback: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if override is None:
            return tuple(sorted(fallback))
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))

    def _no_match_lookup(
        self,
        report: HistoricalEvidenceReport,
        profile: OpportunityProfile,
        label: str,
        metadata: tuple[tuple[str, str], ...],
        reason: str,
    ) -> OpportunityEvidenceLookup:
        del report  # not used for a no-match result
        limitations = (
            f"{reason} No evidence is fabricated; the assessment is "
            "explicitly unavailable. Historical evidence is descriptive "
            "and does not guarantee future performance."
        )
        lookup_id = _lookup_id(profile, None, "", "")
        return OpportunityEvidenceLookup(
            lookup_id=lookup_id,
            profile=profile,
            match_status=CohortMatchStatus.NO_MATCH,
            matched_spec=None,
            matched_cohort=None,
            assessment=None,
            limitations=limitations,
        )

    def _lookup_limitations(
        self,
        profile: OpportunityProfile,
        spec: CohortSpec,
        cohort: HistoricalEvidenceCohort,
        matched: bool,
    ) -> str:
        del matched  # signature kept for clarity
        dims_used = ", ".join(d.name for d in spec.dimensions)
        parts = [
            f"Lookup used the {len(spec.dimensions)}-dimension cohort "
            f"spec {spec.label} ({dims_used}) selected as the most "
            f"specific match for the provided opportunity "
            f"characteristics.",
            f"Matched cohort key {cohort.key!r} with sample size "
            f"{cohort.sample_count}, evidence strength "
            f"{cohort.strength.name}.",
            "The assessment is DESCRIPTIVE and reuses the already-"
            "computed Sprint 11X / 11Y evidence; no outcomes were "
            "re-evaluated and no future information was inspected.",
            "Historical evidence is descriptive and does not guarantee "
            "future performance.",
        ]
        if profile.available_dimensions():
            provided = ", ".join(
                f"{d}={v}" for d, v in profile.available_dimensions()
            )
            parts.append(f"Opportunity characteristics provided: {provided}.")
        return " ".join(parts)

    def _comparison_metrics(
        self,
        stats_a: HistoricalPerformanceStatistics | None,
        stats_b: HistoricalPerformanceStatistics | None,
    ) -> tuple[CohortComparisonMetric, ...]:
        """Build the deterministic, descriptive metric comparison rows."""

        def _metric(
            name: str,
            va: float | None,
            vb: float | None,
        ) -> CohortComparisonMetric:
            if va is None or vb is None:
                delta: float | None = None
                if va is None and vb is None:
                    note = "both unavailable"
                elif va is None:
                    note = "cohort A unavailable"
                else:
                    note = "cohort B unavailable"
            else:
                delta = va - vb
                if delta > 0:
                    note = "cohort A higher (descriptive)"
                elif delta < 0:
                    note = "cohort B higher (descriptive)"
                else:
                    note = "equal (descriptive)"
            return CohortComparisonMetric(
                name=name, value_a=va, value_b=vb, delta=delta, note=note,
            )

        a = stats_a
        b = stats_b
        opp_a = float(a.total) if a is not None else None
        opp_b = float(b.total) if b is not None else None
        res_a = float(a.resolved) if a is not None else None
        res_b = float(b.resolved) if b is not None else None
        vr_a = float(a.valid_r_count) if a is not None else None
        vr_b = float(b.valid_r_count) if b is not None else None

        return (
            _metric("Opportunity Count", opp_a, opp_b),
            _metric("Resolved Count", res_a, res_b),
            _metric("Valid R Count", vr_a, vr_b),
            _metric(
                "Win Rate",
                a.win_rate if a is not None else None,
                b.win_rate if b is not None else None,
            ),
            _metric(
                "Average Realized R",
                a.average_realized_r if a is not None else None,
                b.average_realized_r if b is not None else None,
            ),
            _metric(
                "Total Realized R",
                a.total_realized_r if a is not None else None,
                b.total_realized_r if b is not None else None,
            ),
            _metric(
                "Profit Factor",
                a.profit_factor if a is not None else None,
                b.profit_factor if b is not None else None,
            ),
        )

    def _comparison_notes(
        self,
        spec: CohortSpec,
        key_a: str,
        key_b: str,
        present_a: bool,
        present_b: bool,
        stats_a: HistoricalPerformanceStatistics | None,
        stats_b: HistoricalPerformanceStatistics | None,
        assessment_a: StrategyEvidenceAssessment | None,
        assessment_b: StrategyEvidenceAssessment | None,
    ) -> tuple[str, ...]:
        """Deterministic, descriptive relative observations (no claims)."""

        notes: list[str] = []
        if not present_a:
            notes.append(
                f"Cohort A {key_a!r} is absent from the evidence report "
                f"for spec {spec.label}; its metrics are unavailable.",
            )
        if not present_b:
            notes.append(
                f"Cohort B {key_b!r} is absent from the evidence report "
                f"for spec {spec.label}; its metrics are unavailable.",
            )
        if not (present_a and present_b):
            return tuple(notes)

        a = stats_a
        b = stats_b
        assert a is not None and b is not None

        if a.total != b.total:
            larger = "A" if a.total > b.total else "B"
            notes.append(
                f"Cohort {larger} has more historical observations "
                f"({max(a.total, b.total)} vs "
                f"{min(a.total, b.total)}); descriptive only.",
            )
        else:
            notes.append(
                "Both cohorts have the same number of historical "
                "observations (descriptive only).",
            )

        sa = assessment_a.evidence_strength if assessment_a else None
        sb = assessment_b.evidence_strength if assessment_b else None
        if sa is not None and sb is not None:
            if sa.rank_value < sb.rank_value:
                notes.append(
                    f"Cohort A has stronger evidence ({sa.name}) than "
                    f"cohort B ({sb.name}); descriptive only.",
                )
            elif sb.rank_value < sa.rank_value:
                notes.append(
                    f"Cohort B has stronger evidence ({sb.name}) than "
                    f"cohort A ({sa.name}); descriptive only.",
                )
            else:
                notes.append(
                    f"Both cohorts have the same evidence strength "
                    f"({sa.name}); descriptive only.",
                )

        if (
            a.win_rate is not None
            and b.win_rate is not None
            and a.win_rate != b.win_rate
        ):
            higher = "A" if a.win_rate > b.win_rate else "B"
            notes.append(
                f"Cohort {higher} has a higher observed win rate "
                f"(descriptive only).",
            )

        notes.append(
            "No statistical procedure is available to claim one cohort "
            "is superior; this comparison is descriptive only.",
        )
        return tuple(notes)


__all__ = ["StrategyIntelligenceEngine"]
