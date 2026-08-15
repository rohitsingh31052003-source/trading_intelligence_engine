"""
Historical evidence / validation engine (Sprint 11Y).

:class:`HistoricalEvidenceEngine` evaluates the STRENGTH of the
available historical evidence for cohorts of Sprint 11W
:class:`~engine.models.historical_outcome.HistoricalOutcome` objects.
It is the evidence / validation layer of the separated concern pipeline:

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
   11. EVIDENCE / VALIDATION        (Sprint 11Y)  <- this layer

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the Sprint 11X statistics computation
(:func:`engine.intelligence.performance_analytics._compute_statistics`)
and the Sprint 11X dimension-key / canonical-ordering helpers. It does
NOT recompute any trading / outcome / performance logic. It adds the
EVIDENCE-STRENGTH classification layer on top of the reused statistics.

DESIGN PRINCIPLE — observed result vs evidence strength:

The reused :class:`HistoricalPerformanceStatistics` describe the
OBSERVED historical result (win rate, realized R, profit factor, MFE /
MAE). The :class:`EvidenceStrength` classification describes whether
that observed result is backed by ENOUGH historical observation to be
considered reliable / meaningful evidence. These are NOT the same.
Evidence strength is driven primarily by SAMPLE SIZE and RESOLVED
observation counts (hard gates), never by the magnitude of the
observed win rate alone.

DESIGN PRINCIPLE — sample size is a hard gate:

A cohort whose total sample is below ``min_sample_total`` is ALWAYS
INSUFFICIENT, regardless of how favourable its observed win rate or
realized R may be. A small sample is never promoted to a stronger
evidence level merely because its observed result is impressive.

DESIGN PRINCIPLE — no statistical claims:

The engine does NOT perform statistical hypothesis tests and does NOT
use terms such as "statistically significant". It performs
deterministic, threshold-based classification of observation counts
plus descriptive-metric context in the rationale.

DESIGN PRINCIPLE — no leakage:

The engine consumes ALREADY-COMPUTED historical outcomes (evaluated
forward-only by Sprint 11W). It never re-reads candles, never
re-evaluates outcomes, never uses future information, and never mutates
the decisions made at time ``T``. The point-in-time correctness
established in Sprint 11V and Sprint 11W is preserved unchanged.

DESIGN PRINCIPLE — honest outcome handling:

Sprint 11W semantics are preserved unchanged (via the reused
statistics): BOTH_TOUCHED is ambiguous (never a win / loss, never
contributes to R); NO_GEOMETRY / INSUFFICIENT_DATA carry no fabricated
values; EXPIRED is mark-to-close; TARGET_HIT / STOP_HIT are resolved
favourable / adverse outcomes.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. Cohort ordering is canonical
(reused from Sprint 11X) then lexicographic; composite specs order by
the first dimension then the second. The evidence id hashes SORTED
outcome identities so a shuffled input yields the same id.

This is intelligence / analysis, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.historical_evidence import
HistoricalEvidenceEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Mapping

from engine.config.historical_evidence_config import EvidenceConfig
from engine.intelligence.performance_analytics import (
    _canonical_for,
    _compute_statistics,
    _dimension_key,
    _ordered_keys,
)
from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceBreakdown,
    HistoricalEvidenceCohort,
    HistoricalEvidenceReport,
    HistoricalEvidenceSummary,
)
from engine.models.historical_outcome import HistoricalOutcome
from engine.models.historical_performance import BreakdownDimension


# ============================================================
# SUPPORTED COHORT SPECS (controlled; no combinatorial explosion)
# ============================================================

#: The seven single-dimension cohort specs (mirroring Sprint 11X
#: breakdown dimensions).
_SINGLE_SPECS: tuple[CohortSpec, ...] = tuple(
    CohortSpec((dim,))
    for dim in (
        BreakdownDimension.INSTRUMENT,
        BreakdownDimension.DIRECTION,
        BreakdownDimension.MTF_ALIGNMENT,
        BreakdownDimension.SETUP_TYPE,
        BreakdownDimension.DECISION,
        BreakdownDimension.OPPORTUNITY_STATUS,
        BreakdownDimension.OPPORTUNITY_RANK,
    )
)

#: A controlled, documented set of composite (two-dimension) cohort
#: specs. These cover the practically useful factor combinations
#: without an uncontrolled combinatorial explosion. Each is justified:
#: setup behaviour differs by direction and by higher-timeframe
#: alignment; decision quality differs by rank; setup type differs by
#: decision; instrument behaviour differs by setup type.
_COMPOSITE_SPECS: tuple[CohortSpec, ...] = (
    CohortSpec((BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION)),
    CohortSpec(
        (BreakdownDimension.SETUP_TYPE, BreakdownDimension.MTF_ALIGNMENT),
    ),
    CohortSpec((BreakdownDimension.DECISION, BreakdownDimension.OPPORTUNITY_RANK)),
    CohortSpec((BreakdownDimension.SETUP_TYPE, BreakdownDimension.DECISION)),
    CohortSpec((BreakdownDimension.INSTRUMENT, BreakdownDimension.SETUP_TYPE)),
)

#: The full, ordered, supported set of cohort specs (singles first, in
#: the canonical Sprint 11X dimension order, then the documented
#: composites). This is the default set evaluated by the engine.
SUPPORTED_COHORT_SPECS: tuple[CohortSpec, ...] = _SINGLE_SPECS + _COMPOSITE_SPECS


# ============================================================
# COHORT KEY + ORDERING
# ============================================================


def _cohort_key(
    outcome: HistoricalOutcome, spec: CohortSpec,
) -> str:
    """The combined cohort key for one outcome under one spec."""

    parts = [_dimension_key(outcome, dim) for dim in spec.dimensions]
    return "|".join(parts)


def _ordered_cohort_keys(
    keys: Iterable[str], spec: CohortSpec,
) -> list[str]:
    """
    Order cohort keys deterministically.

    For single-dimension specs the canonical order (reused from
    Sprint 11X) is applied first, then lexicographic for any remaining
    keys, with the ``""`` unavailable sentinel last. For composite
    specs the first dimension's canonical order dominates; ties are
    broken by the second dimension's canonical order, then
    lexicographically, with any ``""`` segment sorting last.
    """

    keys = list(keys)
    if not spec.is_composite:
        canonical = _canonical_for(spec.dimensions[0])
        return _ordered_keys(keys, canonical)

    # Composite: order by first-dimension canonical, then second.
    first_canonical = _canonical_for(spec.dimensions[0])
    second_canonical = _canonical_for(spec.dimensions[1])

    def _sort_key(k: str) -> tuple:
        first, _, second = k.partition("|")
        first_rank = _rank_in(first_canonical, first)
        second_rank = _rank_in(second_canonical, second)
        # Unavailable ("") sorts last within each segment.
        first_unavail = 1 if first == "" else 0
        second_unavail = 1 if second == "" else 0
        return (
            first_unavail,
            first_rank,
            second_unavail,
            second_rank,
            first,
            second,
        )

    return sorted(keys, key=_sort_key)


def _rank_in(canonical: Sequence[str], value: str) -> int:
    """Index of ``value`` in ``canonical``, or a large number if absent."""

    for i, name in enumerate(canonical):
        if name == value:
            return i
    return len(canonical)


# ============================================================
# EVIDENCE CLASSIFICATION
# ============================================================


def _observed_context(
    stats, config: EvidenceConfig,
) -> str:
    """
    Build a descriptive OBSERVED-RESULT context string (used in the
    rationale). This is DESCRIPTIVE CONTEXT ONLY — it never upgrades
    evidence strength on its own (the sample gates do that).
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
    if stats.average_mfe_r is not None:
        parts.append(f"average MFE {stats.average_mfe_r:.2f}R")
    if stats.average_mae_r is not None:
        parts.append(f"average MAE {stats.average_mae_r:.2f}R")
    return "; ".join(parts) if parts else "no observed metrics available"


def _classify(
    stats, config: EvidenceConfig,
) -> tuple[EvidenceStrength, str]:
    """
    Classify the evidence strength for one cohort's statistics.

    Returns the :class:`EvidenceStrength` and a descriptive rationale.
    Sample size is a HARD GATE: below ``min_sample_total`` -> always
    INSUFFICIENT regardless of observed metrics.
    """

    total = stats.total
    resolved = stats.resolved
    valid_r = stats.valid_r_count
    context = _observed_context(stats, config)

    # HARD GATE: insufficient sample.
    if total < config.min_sample_total:
        strength = EvidenceStrength.INSUFFICIENT
        rationale = (
            f"Insufficient evidence: sample size {total} is below the "
            f"configured minimum {config.min_sample_total}. Observed "
            f"metrics are reported but must NOT be treated as reliable "
            f"evidence. ({context}) Descriptive only; not a statistical "
            f"test and not a guarantee of future performance."
        )
        return strength, rationale

    # HARD GATE: not enough resolved / valid-R observation for WEAK+.
    if resolved < config.min_resolved or valid_r < config.min_valid_r:
        strength = EvidenceStrength.WEAK
        missing = []
        if resolved < config.min_resolved:
            missing.append(
                f"resolved outcomes {resolved} < {config.min_resolved}",
            )
        if valid_r < config.min_valid_r:
            missing.append(
                f"valid R observations {valid_r} < {config.min_valid_r}",
            )
        rationale = (
            f"Weak evidence: sample size {total} meets the minimum "
            f"{config.min_sample_total} but {'; '.join(missing)}. "
            f"Observed metrics are directional at best and should be "
            f"treated cautiously. ({context}) Descriptive only; not a "
            f"statistical test and not a guarantee of future performance."
        )
        return strength, rationale

    # STRONG gates.
    if (
        total >= config.strong_min_sample
        and resolved >= config.strong_min_resolved
        and valid_r >= config.strong_min_valid_r
    ):
        strength = EvidenceStrength.STRONG
        rationale = (
            f"Strong evidence: sample size {total}, resolved outcomes "
            f"{resolved} and valid R observations {valid_r} all meet or "
            f"exceed the strong thresholds "
            f"({config.strong_min_sample}/{config.strong_min_resolved}/"
            f"{config.strong_min_valid_r}). ({context}) STRONG evidence "
            f"is still DESCRIPTIVE and does NOT guarantee future "
            f"performance; no statistical hypothesis test was performed."
        )
        return strength, rationale

    # Otherwise MODERATE.
    strength = EvidenceStrength.MODERATE
    rationale = (
        f"Moderate evidence: sample size {total}, resolved outcomes "
        f"{resolved} and valid R observations {valid_r} meet the minimum "
        f"thresholds but not all strong thresholds. ({context}) "
        f"Descriptive only; not a statistical test and not a guarantee "
        f"of future performance."
    )
    return strength, rationale


# ============================================================
# COHORT + BREAKDOWN BUILDING
# ============================================================


def _build_cohort(
    spec: CohortSpec,
    key: str,
    outcomes: Sequence[HistoricalOutcome],
    config: EvidenceConfig,
) -> HistoricalEvidenceCohort:
    stats = _compute_statistics(outcomes)
    strength, rationale = _classify(stats, config)
    return HistoricalEvidenceCohort(
        spec=spec,
        key=key,
        statistics=stats,
        strength=strength,
        sample_count=stats.total,
        resolved_count=stats.resolved,
        valid_r_count=stats.valid_r_count,
        rationale=rationale,
    )


def _build_breakdown(
    outcomes: Sequence[HistoricalOutcome],
    spec: CohortSpec,
    config: EvidenceConfig,
) -> HistoricalEvidenceBreakdown:
    buckets: dict[str, list[HistoricalOutcome]] = {}
    for outcome in outcomes:
        key = _cohort_key(outcome, spec)
        buckets.setdefault(key, []).append(outcome)

    keys = _ordered_cohort_keys(buckets.keys(), spec)
    cohorts = tuple(
        _build_cohort(spec, k, buckets[k], config) for k in keys
    )
    return HistoricalEvidenceBreakdown(spec=spec, cohorts=cohorts)


# ============================================================
# ENGINE
# ============================================================


class HistoricalEvidenceEngine:
    """
    Evaluate the strength of historical evidence for cohorts of Sprint
    11W historical outcomes.

    Public API:

        evaluate(outcomes, label="", metadata=None, specs=None)
            -> HistoricalEvidenceReport

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The input outcomes are NEVER mutated.

    The result is DESCRIPTIVE. It makes no profitability, probability,
    directional prediction, or statistical-significance claim.
    """

    def __init__(
        self,
        config: EvidenceConfig | None = None,
        specs: Iterable[CohortSpec] | None = None,
    ) -> None:
        self.config = config or EvidenceConfig()
        self.specs: tuple[CohortSpec, ...] = (
            tuple(specs) if specs is not None else SUPPORTED_COHORT_SPECS
        )
        self._validate_specs(self.specs)

    @staticmethod
    def _validate_specs(specs: tuple[CohortSpec, ...]) -> None:
        if not specs:
            raise ValueError("At least one CohortSpec must be provided.")
        for spec in specs:
            if not isinstance(spec, CohortSpec):
                raise TypeError("Each spec must be a CohortSpec.")

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def evaluate(
        self,
        outcomes: Iterable[HistoricalOutcome],
        label: str = "",
        metadata: Mapping[str, str] | None = None,
        specs: Iterable[CohortSpec] | None = None,
    ) -> HistoricalEvidenceReport:
        """
        Evaluate the historical evidence for ``outcomes``.

        ``label`` and ``metadata`` override the config's label /
        metadata when supplied (mirroring the Sprint 11W / 11X
        convention). ``specs`` overrides the engine's cohort specs when
        supplied (allowing a caller to evaluate a subset of cohorts).
        The result is deterministic and descriptive.
        """

        outcome_list = list(outcomes)
        label = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)
        active_specs = (
            tuple(specs) if specs is not None else self.specs
        )
        self._validate_specs(active_specs)

        overall_stats = _compute_statistics(outcome_list)
        overall_strength, overall_rationale = _classify(
            overall_stats, self.config,
        )
        summary = HistoricalEvidenceSummary(
            statistics=overall_stats,
            strength=overall_strength,
            sample_count=overall_stats.total,
            resolved_count=overall_stats.resolved,
            valid_r_count=overall_stats.valid_r_count,
            rationale=overall_rationale,
        )

        breakdowns = tuple(
            _build_breakdown(outcome_list, spec, self.config)
            for spec in active_specs
        )

        cohort_count = sum(len(b.cohorts) for b in breakdowns)
        sufficient = sum(
            1 for b in breakdowns for c in b.cohorts if c.is_sufficient
        )
        insufficient = cohort_count - sufficient

        evidence_id = self._evidence_id(
            outcome_list, label, meta, active_specs,
        )
        rationale = self._rationale(
            outcome_list, summary, sufficient, insufficient, cohort_count,
        )

        return HistoricalEvidenceReport(
            evidence_id=evidence_id,
            summary=summary,
            breakdowns=breakdowns,
            cohort_count=cohort_count,
            sufficient_cohort_count=sufficient,
            insufficient_cohort_count=insufficient,
            label=label,
            metadata=meta,
            config_snapshot=self.config.snapshot(),
            rationale=rationale,
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

    def _evidence_id(
        self,
        outcomes: Sequence[HistoricalOutcome],
        label: str,
        metadata: tuple[tuple[str, str], ...],
        specs: tuple[CohortSpec, ...],
    ) -> str:
        """
        Deterministic evidence identifier.

        Computed from the canonical representation of the outcomes'
        identities (instrument / direction / evaluation timestamp /
        outcome status / realized R) SORTED, plus label + metadata +
        the evaluated cohort specs + the config thresholds. The outcome
        identities are sorted before hashing so the id is independent of
        input ordering (a shuffled input yields the same id). No
        wall-clock time, no nondeterminism.
        """

        identities = sorted(
            (
                {
                    "instrument": o.subject.instrument,
                    "direction": o.subject.direction,
                    "evaluation_timestamp": (
                        o.subject.evaluation_timestamp.isoformat()
                        if o.subject.evaluation_timestamp is not None
                        else None
                    ),
                    "outcome_status": o.outcome_status.name,
                    "realized_r": o.realized_r,
                }
                for o in outcomes
            ),
            key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
        )
        payload = {
            "label": label,
            "metadata": [list(p) for p in metadata],
            "specs": [
                [d.name for d in spec.dimensions] for spec in specs
            ],
            "config": [list(p) for p in self.config.snapshot()],
            "outcomes": identities,
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"evidence-{digest[:16]}"

    def _rationale(
        self,
        outcomes: Sequence[HistoricalOutcome],
        summary: HistoricalEvidenceSummary,
        sufficient: int,
        insufficient: int,
        cohort_count: int,
    ) -> str:
        if not outcomes:
            return (
                "Historical evidence evaluation aggregated no outcomes. "
                "Descriptive only; not predictive and not a guarantee of "
                "profitability."
            )
        return (
            f"Historical evidence evaluation aggregated "
            f"{summary.sample_count} outcome(s) across {cohort_count} "
            f"cohort(s). Overall evidence strength: "
            f"{summary.strength.name}. {sufficient} cohort(s) have at "
            f"least usable evidence; {insufficient} cohort(s) are "
            f"insufficient. Evidence strength is driven primarily by "
            f"sample size and resolved observation counts (hard gates); "
            f"a small sample is never treated as strong evidence merely "
            f"because its observed win rate is high. Outcomes were "
            f"evaluated forward-only by Sprint 11W (no future "
            f"information influenced the decision at T); this evidence "
            f"layer aggregates already-computed outcomes only. No "
            f"statistical hypothesis test was performed. Descriptive "
            f"only; not predictive and not a guarantee of profitability."
        )


__all__ = [
    "HistoricalEvidenceEngine",
    "SUPPORTED_COHORT_SPECS",
]
