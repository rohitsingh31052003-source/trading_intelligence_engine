"""
Decision intelligence foundation engine (Sprint 12A).

:class:`DecisionIntelligenceEngine` consumes the ALREADY-COMPUTED
Sprint 11Z :class:`OpportunityEvidenceLookup` (itself built from
already-computed Sprint 11X performance statistics and Sprint 11Y
evidence-strength classifications) and produces a structured,
explainable, decision-process-facing DECISION INTELLIGENCE context.

It is the decision-intelligence foundation layer of the separated
concern pipeline:

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
   12. STRATEGY INTELLIGENCE        (Sprint 11Z)
   13. DECISION INTELLIGENCE        (Sprint 12A)  <- this layer

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the Sprint 11Z
:class:`~engine.models.strategy_intelligence.OpportunityEvidenceLookup`
(which embeds the matched
:class:`~engine.models.historical_evidence.HistoricalEvidenceCohort`,
the reused Sprint 11X
:class:`~engine.models.historical_performance.HistoricalPerformanceStatistics`,
the reused Sprint 11Y
:class:`~engine.models.historical_evidence.EvidenceStrength` and the
Sprint 11Z :class:`~engine.models.strategy_intelligence.StrategyEvidenceAssessment`).
It does NOT recompute any performance statistics, does NOT re-classify
evidence strength, does NOT re-interpret strategy, does NOT rebuild
cohort matching, does NOT re-evaluate outcomes, does NOT re-read
candles, and does NOT use future information. It adds the
decision-process-facing context layer on top.

The core classification is the FIXED, one-to-one
:data:`~engine.models.decision_intelligence.ASSESSMENT_TO_CONTEXT`
mapping from the reused Sprint 11Z strategy assessment status to the
Sprint 12A decision context status (plus the NO_MATCH case). A tiny
cohort with an impressive observed win rate is NEVER promoted to
EVIDENCE_SUPPORTED, because the underlying Sprint 11Y strength
hard-gates on sample size (Sprint 12A inherits that gate unchanged).

DESIGN PRINCIPLE — six separate concerns:

The context surfaces, WITHOUT merging:

* the CURRENT OPPORTUNITY identity (reused profile),
* the EXISTING DECISION (read-only summary projection — represented,
  never altered),
* the OBSERVED historical result (reused statistics),
* the EVIDENCE STRENGTH (reused strength),
* the STRATEGY INTERPRETATION (reused assessment status), and
* the DECISION INTELLIGENCE view (status + factors + rationale +
  limitations).

These are never conflated in the model or in the report.

DESIGN PRINCIPLE — no replacement of existing decision logic:

Sprint 12A does NOT replace or rewrite the existing scoring, signal
generation, decision classification, ranking, opportunity selection,
risk geometry or execution logic. The
:class:`~engine.models.decision_intelligence.ExistingDecisionSummary`
is a read-only PROJECTION. The layer is an INFORMATION / CONTEXT layer
presented to the existing decision process without altering it. It
does NOT produce BUY / SELL / ENTER / EXIT / HOLD recommendations or
automated order generation.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY:

* No matching cohort (11Z lookup NO_MATCH) ->
  :attr:`~engine.models.decision_intelligence.DecisionContextStatus.EVIDENCE_UNAVAILABLE`
  with ``observed_performance = None``, ``evidence_strength = None``
  and ``strategy_interpretation = None``.
* Insufficient sample ->
  :attr:`~engine.models.decision_intelligence.DecisionContextStatus.INSUFFICIENT_EVIDENCE`
  (propagated from the reused
  :attr:`~engine.models.historical_evidence.EvidenceStrength.INSUFFICIENT`).
* Unavailable metric -> ``None`` (delegated to the reused statistics;
  never fabricated).
* ``BOTH_TOUCHED`` / ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` outcomes
  are preserved exactly by the reused statistics; Sprint 12A inherits
  that honesty unchanged.

DESIGN PRINCIPLE — no statistical claims:

The factors are DESCRIPTIVE TOKENS, NOT numerical weights and NOT a
predictive score. The layer does NOT perform statistical hypothesis
tests and does NOT use terms such as "statistically significant".

DESIGN PRINCIPLE — no leakage:

The engine consumes ALREADY-COMPUTED Sprint 11Z evidence. It never
inspects future market candles, never re-evaluates outcomes using
future data, never calls the outcome evaluator, never re-runs the
pipeline, and never modifies the historical replay semantics
established in Sprint 11V / 11W. The point-in-time correctness is
preserved unchanged.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. Identifiers hash the SORTED
canonical identity so a shuffled-equivalent input yields the same id.
Factor ordering is deterministic (canonical enum order).

This is intelligence / analysis, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via
full paths, e.g. ``from engine.intelligence.decision_intelligence
import DecisionIntelligenceEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from engine.config.decision_intelligence_config import (
    DecisionIntelligenceConfig,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.decision_intelligence import (
    ASSESSMENT_TO_CONTEXT,
    DecisionContextStatus,
    DecisionEvidenceFactor,
    DecisionIntelligenceContext,
    DecisionIntelligenceFactor,
    ExistingDecisionSummary,
)
from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceCohort,
    HistoricalEvidenceReport,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    CohortMatchStatus,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
)


# ============================================================
# CONTEXT-STATUS DERIVATION
# ============================================================


def _context_status_from_lookup(
    lookup: OpportunityEvidenceLookup,
) -> DecisionContextStatus:
    """
    Derive the decision context status from the reused 11Z lookup.

    A NO_MATCH lookup (or a lookup without an assessment) maps to
    EVIDENCE_UNAVAILABLE. Otherwise the reused strategy assessment
    status is mapped via the fixed
    :data:`ASSESSMENT_TO_CONTEXT` contract.
    """

    if not lookup.matched or lookup.assessment is None:
        return DecisionContextStatus.EVIDENCE_UNAVAILABLE
    return ASSESSMENT_TO_CONTEXT[lookup.assessment.assessment_status]


# ============================================================
# FACTOR DERIVATION
# ============================================================


def _favorable_metric_reasons(
    stats: HistoricalPerformanceStatistics,
    config: DecisionIntelligenceConfig,
) -> list[str]:
    """Descriptive reasons for each favourable observed metric."""

    reasons: list[str] = []
    if (
        stats.win_rate is not None
        and stats.win_rate >= config.favorable_win_rate
    ):
        reasons.append(
            f"observed win rate {stats.win_rate:.2f} >= "
            f"{config.favorable_win_rate:.2f} (descriptive)",
        )
    if (
        stats.average_realized_r is not None
        and stats.average_realized_r >= config.favorable_avg_r
    ):
        reasons.append(
            f"average realized R {stats.average_realized_r:.2f} >= "
            f"{config.favorable_avg_r:.2f} (descriptive)",
        )
    if (
        stats.profit_factor is not None
        and stats.profit_factor >= config.favorable_profit_factor
    ):
        reasons.append(
            f"profit factor {stats.profit_factor:.2f} >= "
            f"{config.favorable_profit_factor:.2f} (descriptive)",
        )
    return reasons


def _unfavorable_metric_reasons(
    stats: HistoricalPerformanceStatistics,
    config: DecisionIntelligenceConfig,
) -> list[str]:
    """Descriptive reasons for each adverse observed metric."""

    reasons: list[str] = []
    if (
        stats.win_rate is not None
        and stats.win_rate < config.favorable_win_rate
    ):
        reasons.append(
            f"observed win rate {stats.win_rate:.2f} < "
            f"{config.favorable_win_rate:.2f} (descriptive)",
        )
    if (
        stats.average_realized_r is not None
        and stats.average_realized_r < config.adverse_avg_r
    ):
        reasons.append(
            f"average realized R {stats.average_realized_r:.2f} < "
            f"{config.adverse_avg_r:.2f} (descriptive)",
        )
    if (
        stats.profit_factor is not None
        and stats.profit_factor < config.adverse_profit_factor
    ):
        reasons.append(
            f"profit factor {stats.profit_factor:.2f} < "
            f"{config.adverse_profit_factor:.2f} (descriptive)",
        )
    return reasons


def _build_factors(
    lookup: OpportunityEvidenceLookup,
    status: DecisionContextStatus,
    config: DecisionIntelligenceConfig,
) -> tuple[DecisionIntelligenceFactor, ...]:
    """
    Build the deterministic, ordered decision-intelligence factors.

    Factors are descriptive tokens (no numerical weights, no
    predictive score). They are ordered by
    :attr:`DecisionEvidenceFactor.rank_value`. Favourable / unfavourable
    characteristic factors are emitted ONLY when the evidence is at
    least usable (sufficient), so a tiny cohort is never flagged
    favourable / unfavourable merely because its observed win rate is
    extreme.
    """

    emitted: list[DecisionIntelligenceFactor] = []

    if not lookup.matched or lookup.assessment is None:
        emitted.append(
            DecisionIntelligenceFactor(
                factor=DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE,
                reason=(
                    "No matching historical cohort exists for the "
                    "current opportunity's characteristics; no "
                    "evidence is fabricated."
                ),
            ),
        )
        return tuple(emitted)

    assessment = lookup.assessment
    strength = assessment.evidence_strength
    stats = assessment.observed_performance
    sufficient = strength.is_sufficient

    if status == DecisionContextStatus.EVIDENCE_SUPPORTED:
        emitted.append(
            DecisionIntelligenceFactor(
                factor=DecisionEvidenceFactor.HISTORICAL_SUPPORT_PRESENT,
                reason=(
                    f"Relevant historical evidence is available and at "
                    f"least usable (evidence strength {strength.name}, "
                    f"sample {stats.total}, resolved {stats.resolved}, "
                    f"valid-R {stats.valid_r_count}). The existing "
                    f"decision process is informed that supporting "
                    f"historical observation exists; still descriptive "
                    f"and not predictive."
                ),
            ),
        )

    if status == DecisionContextStatus.EVIDENCE_LIMITED:
        emitted.append(
            DecisionIntelligenceFactor(
                factor=DecisionEvidenceFactor.HISTORICAL_CAUTION_PRESENT,
                reason=(
                    f"The historical evidence is limited / weak "
                    f"(evidence strength {strength.name}, sample "
                    f"{stats.total}, resolved {stats.resolved}). The "
                    f"evidence is directional at best and should be "
                    f"treated cautiously."
                ),
            ),
        )

    if status == DecisionContextStatus.INSUFFICIENT_EVIDENCE:
        emitted.append(
            DecisionIntelligenceFactor(
                factor=DecisionEvidenceFactor.INSUFFICIENT_HISTORICAL_EVIDENCE,
                reason=(
                    f"The historical evidence is INSUFFICIENT (evidence "
                    f"strength {strength.name}, sample {stats.total} "
                    f"below the configured minimum). The observed "
                    f"metrics must NOT be treated as reliable evidence; "
                    f"insufficient evidence is never transformed into a "
                    f"positive or negative trading decision."
                ),
            ),
        )

    # Favourable / unfavourable characteristics are reported ONLY when
    # the evidence is at least usable, so a tiny cohort's extreme
    # observed win rate never produces a misleading characteristic.
    if sufficient:
        fav_reasons = _favorable_metric_reasons(stats, config)
        if fav_reasons:
            emitted.append(
                DecisionIntelligenceFactor(
                    factor=DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS,
                    reason=(
                        "Favourable observed historical characteristic(s) "
                        "present: " + "; ".join(fav_reasons)
                        + ". Descriptive context only; does NOT upgrade "
                        "the decision context status."
                    ),
                ),
            )
        unfav_reasons = _unfavorable_metric_reasons(stats, config)
        if unfav_reasons:
            emitted.append(
                DecisionIntelligenceFactor(
                    factor=DecisionEvidenceFactor.UNFAVORABLE_HISTORICAL_CHARACTERISTICS,
                    reason=(
                        "Unfavourable observed historical characteristic(s) "
                        "present: " + "; ".join(unfav_reasons)
                        + ". Descriptive context only; never transforms "
                        "the evidence into a negative trading decision."
                    ),
                ),
            )

    emitted.sort(key=lambda f: f.factor.rank_value)
    return tuple(emitted)


# ============================================================
# RATIONALE + LIMITATIONS
# ============================================================


_CONTEXT_RATIONALE: dict[DecisionContextStatus, str] = {
    DecisionContextStatus.EVIDENCE_SUPPORTED: (
        "Relevant historical evidence is available and at least usable; "
        "the existing decision process is informed that supporting "
        "historical observation exists."
    ),
    DecisionContextStatus.EVIDENCE_LIMITED: (
        "Relevant historical evidence exists but is limited / weak; the "
        "existing decision process is informed that the evidence is "
        "directional at best."
    ),
    DecisionContextStatus.INSUFFICIENT_EVIDENCE: (
        "Historical evidence for this opportunity type is INSUFFICIENT; "
        "the observed metrics must NOT be treated as reliable evidence. "
        "Insufficient evidence is never transformed into a positive or "
        "negative trading decision."
    ),
    DecisionContextStatus.EVIDENCE_UNAVAILABLE: (
        "No matching historical cohort exists for the current "
        "opportunity's characteristics; no relevant historical evidence "
        "is available. No evidence is fabricated."
    ),
}


def _rationale(
    profile: OpportunityProfile,
    existing_decision: ExistingDecisionSummary,
    lookup: OpportunityEvidenceLookup,
    status: DecisionContextStatus,
    factors: tuple[DecisionIntelligenceFactor, ...],
) -> str:
    """Build the deterministic, descriptive rationale."""

    parts: list[str] = []
    if profile.available_dimensions():
        chars = ", ".join(
            f"{d}={v}" for d, v in profile.available_dimensions()
        )
        parts.append(f"Current opportunity characteristics: {chars}.")
    else:
        parts.append(
            "Current opportunity characteristics: (none provided).",
        )

    if existing_decision.has_decision:
        parts.append(
            f"Existing decision: classification "
            f"{existing_decision.decision_classification or 'none'}, "
            f"score {existing_decision.decision_score}, direction "
            f"{existing_decision.direction or 'none'}. The existing "
            f"decision is represented WITHOUT alteration."
        )
    else:
        parts.append(
            "Existing decision: none provided (the decision-intelligence "
            "context carries no existing-decision projection).",
        )

    parts.append(f"Decision context status: {status.name}.")
    parts.append(_CONTEXT_RATIONALE[status])

    if lookup.matched and lookup.assessment is not None:
        a = lookup.assessment
        parts.append(
            f"Matched historical cohort (spec {lookup.matched_spec.label}, "
            f"key {lookup.matched_cohort.key!r}): evidence strength "
            f"{a.evidence_strength.name}, strategy interpretation "
            f"{a.assessment_status.name}, sample {a.sample_count}."
        )
    else:
        parts.append(
            "No matching historical cohort was found for the provided "
            "opportunity characteristics.",
        )

    factor_names = ", ".join(f.factor.name for f in factors) or "none"
    parts.append(f"Decision-intelligence factors: {factor_names}.")

    parts.append(
        "Historical evidence is descriptive and does not guarantee future "
        "performance; the existing decision / scoring logic is not "
        "modified by this context."
    )
    return " ".join(parts)


def _limitations(
    lookup: OpportunityEvidenceLookup,
    status: DecisionContextStatus,
) -> str:
    """Build the deterministic, descriptive limitations statement."""

    parts: list[str] = [
        "The decision-intelligence context is an INFORMATION / CONTEXT "
        "layer; it does NOT replace the existing scoring, signal "
        "generation, decision classification, ranking, opportunity "
        "selection, risk geometry or execution logic.",
        "Evidence strength is driven primarily by sample size and "
        "resolved observation counts (hard-gated by Sprint 11Y); a "
        "small sample is never promoted to stronger evidence merely "
        "because its observed win rate is high.",
        "No statistical hypothesis test was performed.",
        "Historical evidence is descriptive and does not guarantee "
        "future performance.",
    ]
    if not status.evidence_available:
        parts.append(
            "No matching historical cohort exists; observed performance, "
            "evidence strength and strategy interpretation are "
            "unavailable (never fabricated).",
        )
    else:
        a = lookup.assessment
        assert a is not None
        stats = a.observed_performance
        parts.append(
            f"Sample size {stats.total}, resolved {stats.resolved}, "
            f"valid-R {stats.valid_r_count}.",
        )
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
# DETERMINISTIC ID
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


def _decision_identity(d: ExistingDecisionSummary) -> dict[str, Any]:
    """A deterministic, JSON-safe identity for the existing-decision summary."""

    return {
        "direction": d.direction,
        "decision_classification": d.decision_classification,
        "decision_score": d.decision_score,
        "opportunity_status": d.opportunity_status,
        "rank": d.rank,
        "geometry_complete": d.geometry_complete,
        "confluence_score": d.confluence_score,
        "risk_reward_ratio": d.risk_reward_ratio,
        "entry": d.entry,
        "stop": d.stop,
        "target": d.target,
    }


def _context_id(
    profile: OpportunityProfile,
    existing_decision: ExistingDecisionSummary,
    lookup: OpportunityEvidenceLookup,
    status: DecisionContextStatus,
    factors: tuple[DecisionIntelligenceFactor, ...],
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic context identifier (``"di-"`` + sha256[:16])."""

    # The lookup.lookup_id is already deterministic (and shuffle-
    # invariant via Sprint 11Y / 11Z), so embedding it makes the
    # context id shuffle-invariant for equivalent evidence.
    payload = {
        "profile": [[d, v] for d, v in profile.available_dimensions()],
        "existing_decision": _decision_identity(existing_decision),
        "lookup_id": lookup.lookup_id,
        "decision_context_status": status.name,
        "factors": [[f.factor.name, f.reason] for f in factors],
        "label": label,
        "metadata": [list(p) for p in metadata],
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"di-{digest[:16]}"


# ============================================================
# ENGINE
# ============================================================


class DecisionIntelligenceEngine:
    """
    Produce an evidence-aware decision-intelligence context for a
    current opportunity from the ALREADY-COMPUTED Sprint 11Z evidence
    lookup.

    Public API:

        build(profile, existing_decision, lookup, label, metadata)
            -> DecisionIntelligenceContext
        build_from_report(report, profile, existing_decision,
            strategy_engine, label, metadata)
            -> DecisionIntelligenceContext

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The input lookup / report / profile /
    decision are NEVER mutated. The result is DESCRIPTIVE. It makes no
    profitability, probability, directional prediction, statistical-
    significance, or trading-recommendation claim, and it does NOT
    modify the existing decision / scoring logic.
    """

    def __init__(self, config: DecisionIntelligenceConfig | None = None) -> None:
        self.config = config or DecisionIntelligenceConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def build(
        self,
        profile: OpportunityProfile,
        existing_decision: ExistingDecisionSummary | None,
        lookup: OpportunityEvidenceLookup,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> DecisionIntelligenceContext:
        """
        Build a decision-intelligence context from an ALREADY-COMPUTED
        Sprint 11Z :class:`OpportunityEvidenceLookup`.

        Reuses the lookup (matched cohort + strategy assessment +
        reused statistics / strength) verbatim; applies the fixed
        :data:`ASSESSMENT_TO_CONTEXT` mapping. Never recomputes
        statistics, re-classifies evidence, re-interprets strategy,
        re-evaluates outcomes, re-reads candles, or modifies the
        existing decision.
        """

        lbl = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)
        decision = existing_decision or ExistingDecisionSummary()
        status = _context_status_from_lookup(lookup)
        factors = _build_factors(lookup, status, self.config)

        observed: HistoricalPerformanceStatistics | None = None
        strength: EvidenceStrength | None = None
        interpretation: StrategyAssessmentStatus | None = None
        if lookup.matched and lookup.assessment is not None:
            a = lookup.assessment
            observed = a.observed_performance
            strength = a.evidence_strength
            interpretation = a.assessment_status

        rationale = _rationale(profile, decision, lookup, status, factors)
        limitations = _limitations(lookup, status)
        context_id = _context_id(
            profile, decision, lookup, status, factors, lbl, meta,
        )
        return DecisionIntelligenceContext(
            context_id=context_id,
            profile=profile,
            existing_decision=decision,
            lookup=lookup,
            decision_context_status=status,
            factors=factors,
            observed_performance=observed,
            evidence_strength=strength,
            strategy_interpretation=interpretation,
            rationale=rationale,
            limitations=limitations,
            label=lbl,
            metadata=meta,
        )

    def build_from_report(
        self,
        report: HistoricalEvidenceReport,
        profile: OpportunityProfile,
        existing_decision: ExistingDecisionSummary | None = None,
        strategy_engine: StrategyIntelligenceEngine | None = None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> DecisionIntelligenceContext:
        """
        Convenience: run the Sprint 11Z evidence lookup on an
        already-computed 11Y evidence ``report`` and build the
        decision-intelligence context from the resulting lookup.

        This does NOT rebuild cohort matching, does NOT recompute 11X
        analytics, does NOT re-classify 11Y evidence and does NOT
        re-interpret 11Z strategy — it delegates the lookup to the
        (reused or default) :class:`StrategyIntelligenceEngine` and
        then delegates to :meth:`build`.
        """

        engine = strategy_engine or StrategyIntelligenceEngine()
        lookup = engine.lookup(report, profile)
        return self.build(
            profile, existing_decision, lookup, label=label, metadata=metadata,
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
            return fallback
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))


__all__ = ["DecisionIntelligenceEngine"]
