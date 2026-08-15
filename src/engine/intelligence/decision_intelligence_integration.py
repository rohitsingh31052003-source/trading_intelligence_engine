"""
Controlled decision intelligence INTEGRATION engine (Sprint 12B).

:class:`DecisionIntelligenceIntegrationEngine` consumes the ALREADY-
COMPUTED Sprint 12A :class:`DecisionIntelligenceContext` (itself built
from already-computed Sprint 11X / 11Y / 11Z evidence) and attaches it
to an EXISTING DECISION, producing an
:class:`IntegratedDecisionContext` that preserves the existing decision
EXACTLY as-is while surfacing the decision intelligence as additional,
auditable, descriptive context.

It is the integration boundary of the separated concern pipeline:

    Existing Decision
        ↓
    Decision Intelligence Context   (reused Sprint 12A, by reference)
        ↓
    Integrated Decision Context     (Sprint 12B — this layer)

Architecture (unchanged in direction, 12B sits BELOW 12A):

    11X -> 11Y -> 11Z -> 12A -> 12B

DESIGN PRINCIPLE — the existing decision is AUTHORITATIVE:

The engine retains the ORIGINAL existing decision object BY REFERENCE
and NEVER modifies it. Decision intelligence is CONTEXTUAL EVIDENCE
attached to the existing decision, NOT a replacement decision. The
existing decision classification / score / rank are represented WITHOUT
alteration; evidence-supported context is NEVER interpreted as an
upgrade (or downgrade) of the existing decision. There is no BUY / SELL
/ ENTER / EXIT / HOLD recommendation, no probability, no score
adjustment, no hidden weight, no re-ranking and no re-selection.

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the Sprint 12A :class:`DecisionIntelligenceContext`
(which embeds the matched cohort, the reused 11X statistics, the reused
11Y evidence strength, the 11Z strategy assessment and the 12A
decision-context status + factors). It does NOT recompute performance
statistics, does NOT re-classify evidence strength, does NOT re-interpret
strategy, does NOT rebuild cohort matching, does NOT re-evaluate
outcomes, does NOT re-read candles and does NOT use future information.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY:

* No existing decision supplied (``None``) -> ``INVALID``; nothing to
  integrate into.
* Existing decision present but no decision intelligence supplied
  (``None``) -> ``UNAVAILABLE``; no evidence fabricated.
* Existing decision + decision intelligence with NO matching cohort
  (12A ``EVIDENCE_UNAVAILABLE``) -> ``CONTEXT_ONLY``; informational
  context only.
* Existing decision + decision intelligence with available evidence ->
  ``INTEGRATED``.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. The integration id hashes the
canonical identity (existing-decision projection + 12A context id +
status + profile + factors + label + metadata); because the embedded 12A
context id is already shuffle-invariant (via Sprint 11Y / 11Z), the
integration id is shuffle-invariant for equivalent evidence.

DESIGN PRINCIPLE — no leakage:

The engine consumes ALREADY-COMPUTED Sprint 12A context. Its public API
takes NO candle / future-market-data argument. It never inspects future
market candles, never re-evaluates outcomes, never calls the outcome
evaluator, never re-runs the pipeline, and never modifies the historical
replay semantics established in Sprint 11V / 11W.

This is intelligence / integration, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.decision_intelligence_integration
import DecisionIntelligenceIntegrationEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from engine.config.decision_intelligence_integration_config import (
    DecisionIntelligenceIntegrationConfig,
)
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionIntelligenceContext,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
    IntegrationStatus,
)
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    OpportunityProfile,
    StrategyAssessmentStatus,
)


# ============================================================
# EXISTING-DECISION PROJECTION
# ============================================================


def _to_decision_summary(obj: Any) -> ExistingDecisionSummary:
    """
    Derive a read-only :class:`ExistingDecisionSummary` projection from
    an existing decision object.

    The original object is NEVER mutated. When the object is already an
    :class:`ExistingDecisionSummary` it is returned as-is (identity
    preserved). Otherwise the relevant fields are read DEFENSIVELY via
    ``getattr`` so the integration accepts a Sprint 11S
    :class:`~engine.models.trade_decision.TradeDecision`, a Sprint 11T
    :class:`~engine.models.opportunity.TradeOpportunity`, or any
    duck-typed object exposing the same attribute names. When the object
    is ``None`` (or unrecognized) an empty summary is returned — no value
    is fabricated.
    """

    if obj is None:
        return ExistingDecisionSummary()
    if isinstance(obj, ExistingDecisionSummary):
        return obj

    # Defensive read of the well-known decision / opportunity fields.
    direction = _str_attr(obj, "direction")
    classification = _classification_attr(obj)
    score = _int_attr(obj, "decision_score")
    opportunity_status = _opportunity_status_attr(obj)
    rank = _int_attr(obj, "rank")
    geometry_complete = bool(_attr(obj, "geometry_complete", False))
    confluence_score = _int_attr(obj, "confluence_score")
    risk_reward_ratio = _float_attr(obj, "risk_reward_ratio")
    entry = _float_attr(obj, "entry") or _float_attr(obj, "entry_reference")
    stop = _float_attr(obj, "stop") or _float_attr(obj, "stop_reference")
    target = _float_attr(obj, "target") or _float_attr(obj, "target_reference")
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status=opportunity_status,
        rank=rank,
        geometry_complete=geometry_complete,
        confluence_score=confluence_score,
        risk_reward_ratio=risk_reward_ratio,
        entry=entry,
        stop=stop,
        target=target,
    )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _str_attr(obj: Any, name: str) -> str:
    value = _attr(obj, name, "")
    if value is None:
        return ""
    # Enum members (e.g. CandidateDirection) -> name; else str.
    if hasattr(value, "name"):
        return str(value.name)
    return str(value)


def _int_attr(obj: Any, name: str) -> int:
    value = _attr(obj, name, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_attr(obj: Any, name: str) -> float | None:
    value = _attr(obj, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _classification_attr(obj: Any) -> str:
    # TradeDecision exposes ``classification`` (a DecisionClassification
    # enum); TradeOpportunity / ExistingDecisionSummary expose the name
    # directly via ``decision_classification``.
    direct = _attr(obj, "decision_classification", "")
    if direct:
        return _str_value(direct)
    cls = _attr(obj, "classification", None)
    return _str_value(cls)


def _opportunity_status_attr(obj: Any) -> str:
    direct = _attr(obj, "opportunity_status", "")
    if direct:
        return _str_value(direct)
    status = _attr(obj, "status", None)
    return _str_value(status)


def _str_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "name"):
        return str(value.name)
    return str(value)


# ============================================================
# INTEGRATION STATUS DERIVATION
# ============================================================


def _derive_status(
    existing_decision: Any,
    decision_intelligence: DecisionIntelligenceContext | None,
) -> IntegrationStatus:
    """
    Derive the deterministic, mutually-exclusive integration status.

    Priority order (documented on :class:`IntegrationStatus`):

    1. No existing decision -> INVALID (nothing to integrate into).
    2. Existing decision but no decision intelligence -> UNAVAILABLE.
    3. Existing decision + DI with no available evidence -> CONTEXT_ONLY.
    4. Existing decision + DI with available evidence -> INTEGRATED.
    """

    if existing_decision is None:
        return IntegrationStatus.INVALID
    if decision_intelligence is None:
        return IntegrationStatus.UNAVAILABLE
    if decision_intelligence.has_evidence:
        return IntegrationStatus.INTEGRATED
    return IntegrationStatus.CONTEXT_ONLY


# ============================================================
# PROFILE CONSISTENCY GUARD
# ============================================================


def _profile_inconsistencies(
    integration_profile: OpportunityProfile,
    di_context: DecisionIntelligenceContext,
) -> list[str]:
    """
    Detect non-empty dimensions where the integration profile disagrees
    with the decision-intelligence context's profile.

    Returns a deterministic list of human-readable inconsistency
    descriptions (empty when consistent). The guard NEVER modifies the
    existing decision; it only reports profile disagreement so a
    decision-intelligence context computed for a DIFFERENT opportunity
    is never silently integrated.
    """

    issues: list[str] = []
    if di_context is None:
        return issues
    di_profile = di_context.profile
    for dim, value in integration_profile.available_dimensions():
        di_value = _profile_dim_value(di_profile, dim)
        if di_value != "" and di_value is not None and di_value != value:
            issues.append(
                f"{dim}: integration profile '{value}' vs "
                f"decision-intelligence profile '{di_value}'",
            )
    return issues


def _profile_dim_value(profile: OpportunityProfile, dim: str) -> Any:
    return getattr(profile, _PROFILE_FIELD.get(dim, dim), "")


_PROFILE_FIELD = {
    "INSTRUMENT": "instrument",
    "DIRECTION": "direction",
    "SETUP_TYPE": "setup_type",
    "MTF_ALIGNMENT": "mtf_alignment",
    "DECISION": "decision",
    "OPPORTUNITY_STATUS": "opportunity_status",
    "OPPORTUNITY_RANK": "rank",
}


# ============================================================
# DETERMINISTIC ID
# ============================================================


def _decision_identity(d: ExistingDecisionSummary) -> dict[str, Any]:
    """A deterministic, JSON-safe identity for the decision projection."""

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


def _integration_id(
    summary: ExistingDecisionSummary,
    di_context: DecisionIntelligenceContext | None,
    profile: OpportunityProfile,
    status: IntegrationStatus,
    factors: tuple,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic integration identifier (``"int-"`` + sha256[:16])."""

    # The 12A context_id is already deterministic and shuffle-invariant
    # (via Sprint 11Y / 11Z), so embedding it makes the integration id
    # shuffle-invariant for equivalent evidence.
    payload = {
        "existing_decision": _decision_identity(summary),
        "decision_intelligence_id": (
            di_context.context_id if di_context is not None else None
        ),
        "profile": [[dim, val] for dim, val in profile.available_dimensions()],
        "integration_status": status.name,
        "factors": [[f.factor.name, f.reason] for f in factors],
        "label": label,
        "metadata": [list(p) for p in metadata],
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"int-{digest[:16]}"


# ============================================================
# RATIONALE + LIMITATIONS
# ============================================================


_STATUS_RATIONALE: dict[IntegrationStatus, str] = {
    IntegrationStatus.INTEGRATED: (
        "Decision intelligence with available historical evidence was "
        "successfully attached to the existing decision as evidence-aware "
        "context. The existing decision is preserved without alteration; "
        "the attached context is descriptive and does not upgrade, "
        "downgrade, replace or otherwise modify the existing decision."
    ),
    IntegrationStatus.CONTEXT_ONLY: (
        "A decision intelligence context was attached to the existing "
        "decision, but it carries no available historical evidence (no "
        "matching cohort). The intelligence is informational context only; "
        "it does not constitute supportive evidence and does not alter the "
        "existing decision."
    ),
    IntegrationStatus.UNAVAILABLE: (
        "An existing decision is present but no decision intelligence "
        "context was supplied. No evidence is fabricated; the existing "
        "decision stands unchanged and the integration records that no "
        "decision intelligence is available."
    ),
    IntegrationStatus.INVALID: (
        "No existing decision was supplied, so the decision intelligence "
        "could not be integrated into a decision. The integration result "
        "carries an explicit invalid state; no decision intelligence "
        "context is fabricated."
    ),
}


def _rationale(
    summary: ExistingDecisionSummary,
    di_context: DecisionIntelligenceContext | None,
    profile: OpportunityProfile,
    status: IntegrationStatus,
    inconsistencies: list[str],
) -> str:
    """Build the deterministic, descriptive integration rationale."""

    parts: list[str] = []

    if profile.available_dimensions():
        chars = ", ".join(f"{d}={v}" for d, v in profile.available_dimensions())
        parts.append(f"Current opportunity characteristics: {chars}.")
    else:
        parts.append("Current opportunity characteristics: (none provided).")

    if summary.has_decision:
        parts.append(
            f"Existing decision: classification "
            f"{summary.decision_classification or 'none'}, score "
            f"{summary.decision_score}, direction "
            f"{summary.direction or 'none'}. The existing decision is "
            f"preserved WITHOUT alteration (retained by reference).",
        )
    else:
        parts.append(
            "Existing decision: none supplied; the integration has no "
            "existing decision to attach decision intelligence to.",
        )

    parts.append(f"Integration status: {status.name}.")
    parts.append(_STATUS_RATIONALE[status])

    if di_context is not None:
        parts.append(
            f"Attached decision intelligence: context id "
            f"{di_context.context_id}, decision-context status "
            f"{di_context.decision_context_status.name}.",
        )
        if di_context.matched and di_context.evidence_strength is not None:
            parts.append(
                f"Matched historical cohort: evidence strength "
                f"{di_context.evidence_strength.name}, strategy "
                f"interpretation "
                f"{(di_context.strategy_interpretation.name if di_context.strategy_interpretation else 'unavailable')}.",
            )
        else:
            parts.append(
                "No matching historical cohort for the attached decision "
                "intelligence context.",
            )
    else:
        parts.append(
            "No decision intelligence context was attached.",
        )

    if inconsistencies:
        parts.append(
            "Profile inconsistency detected: " + "; ".join(inconsistencies)
            + ". The integration proceeded with the supplied integration "
            "profile; verify that the decision intelligence context was "
            "computed for the same opportunity.",
        )

    parts.append(
        "Decision intelligence is contextual evidence, NOT a replacement "
        "decision; historical evidence is descriptive and does not "
        "guarantee future performance."
    )
    return " ".join(parts)


def _limitations(
    di_context: DecisionIntelligenceContext | None,
    status: IntegrationStatus,
    inconsistencies: list[str],
) -> str:
    """Build the deterministic, descriptive integration limitations."""

    parts: list[str] = [
        "The integration boundary is a CONTEXT / AUDIT layer; it does NOT "
        "replace the existing scoring, signal generation, decision "
        "classification, ranking, opportunity selection, risk geometry or "
        "execution logic. The existing decision is retained by reference "
        "and is NEVER modified by the integration.",
        "Decision intelligence is contextual evidence attached to the "
        "existing decision; evidence-supported context is NEVER "
        "interpreted as an upgrade (or downgrade) of the existing "
        "decision, and the integration produces NO BUY / SELL / ENTER / "
        "EXIT / HOLD recommendation, NO probability and NO score "
        "adjustment.",
        "No statistical hypothesis test was performed.",
        "Historical evidence is descriptive and does not guarantee future "
        "performance.",
    ]
    if status == IntegrationStatus.INVALID:
        parts.append(
            "No existing decision was supplied; the integration result "
            "carries no integrated decision.",
        )
    elif status == IntegrationStatus.UNAVAILABLE:
        parts.append(
            "No decision intelligence context was supplied; no evidence is "
            "fabricated.",
        )
    elif status == IntegrationStatus.CONTEXT_ONLY:
        parts.append(
            "The attached decision intelligence carries no available "
            "historical evidence (no matching cohort); it is informational "
            "context only and does not constitute supportive evidence.",
        )
    else:
        if di_context is not None and di_context.evidence_strength is not None:
            parts.append(
                f"Evidence strength is {di_context.evidence_strength.name} "
                "(driven primarily by sample size and resolved observation "
                "counts, hard-gated by Sprint 11Y); a small sample is never "
                "promoted to stronger evidence merely because its observed "
                "win rate is high.",
            )
        if di_context is not None and di_context.observed_performance is not None:
            stats = di_context.observed_performance
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
    if inconsistencies:
        parts.append(
            "A profile inconsistency was detected between the integration "
            "profile and the decision-intelligence context profile; the "
            "integration proceeded with the supplied integration profile.",
        )
    return " ".join(parts)


# ============================================================
# ENGINE
# ============================================================


class DecisionIntelligenceIntegrationEngine:
    """
    Attach an ALREADY-COMPUTED Sprint 12A decision-intelligence context
    to an EXISTING DECISION, preserving the existing decision exactly
    as-is.

    Public API:

        integrate(existing_decision, decision_intelligence, profile,
                  label, metadata)
            -> IntegratedDecisionContext

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The existing decision / decision
    intelligence / profile are NEVER mutated. The result is DESCRIPTIVE.
    It makes no profitability, probability, directional prediction,
    statistical-significance, or trading-recommendation claim, and it
    does NOT modify the existing decision / scoring logic. Its public
    API takes NO candle / future-market-data argument.
    """

    def __init__(
        self,
        config: DecisionIntelligenceIntegrationConfig | None = None,
    ) -> None:
        self.config = config or DecisionIntelligenceIntegrationConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def integrate(
        self,
        existing_decision: Any,
        decision_intelligence: DecisionIntelligenceContext | None,
        profile: OpportunityProfile | None = None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> IntegratedDecisionContext:
        """
        Integrate an existing decision with an ALREADY-COMPUTED Sprint
        12A :class:`DecisionIntelligenceContext`.

        Reuses the decision-intelligence context verbatim (matched
        cohort + strategy assessment + reused statistics / strength +
        12A decision-context status + factors). Never recomputes
        statistics, re-classifies evidence, re-interprets strategy,
        re-evaluates outcomes, re-reads candles, or modifies the
        existing decision. Determines the deterministic
        :class:`IntegrationStatus` and produces explicit rationale /
        limitations / audit information.
        """

        lbl = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)
        prof = profile or self._default_profile(decision_intelligence)

        # Profile consistency guard (audit only; never modifies inputs).
        inconsistencies: list[str] = []
        if decision_intelligence is not None and prof is not None:
            inconsistencies = _profile_inconsistencies(prof, decision_intelligence)
            if inconsistencies and self.config.strict:
                raise ValueError(
                    "Inconsistent opportunity profile between the existing "
                    "decision and the decision intelligence context: "
                    + "; ".join(inconsistencies)
                    + ". Pass a consistent profile, or set "
                    "strict=False to record the inconsistency instead of "
                    "raising.",
                )

        status = _derive_status(existing_decision, decision_intelligence)
        summary = _to_decision_summary(existing_decision)

        evidence_status: DecisionContextStatus | None = None
        strategy_interpretation: StrategyAssessmentStatus | None = None
        observed: HistoricalPerformanceStatistics | None = None
        strength: EvidenceStrength | None = None
        factors: tuple = ()
        if decision_intelligence is not None:
            evidence_status = decision_intelligence.decision_context_status
            strategy_interpretation = decision_intelligence.strategy_interpretation
            observed = decision_intelligence.observed_performance
            strength = decision_intelligence.evidence_strength
            factors = decision_intelligence.factors

        rationale = _rationale(summary, decision_intelligence, prof, status, inconsistencies)
        limitations = _limitations(decision_intelligence, status, inconsistencies)
        integration_id = _integration_id(
            summary, decision_intelligence, prof, status, factors, lbl, meta,
        )
        return IntegratedDecisionContext(
            integration_id=integration_id,
            existing_decision=existing_decision,
            existing_decision_summary=summary,
            profile=prof,
            decision_intelligence=decision_intelligence,
            integration_status=status,
            evidence_status=evidence_status,
            strategy_interpretation=strategy_interpretation,
            observed_performance=observed,
            evidence_strength=strength,
            contextual_factors=factors,
            rationale=rationale,
            limitations=limitations,
            label=lbl,
            metadata=meta,
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _default_profile(
        decision_intelligence: DecisionIntelligenceContext | None,
    ) -> OpportunityProfile:
        """Default the integration profile to the DI context's profile."""

        if decision_intelligence is not None:
            return decision_intelligence.profile
        return OpportunityProfile()

    @staticmethod
    def _normalize_metadata(
        override: Mapping[str, str] | None,
        fallback: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if override is None:
            return fallback
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))


__all__ = ["DecisionIntelligenceIntegrationEngine"]
