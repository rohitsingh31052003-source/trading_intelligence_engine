"""
Shared selection core: criteria evaluation and deterministic ranking
(Sprint 11N).

This module hosts the selection logic that is COMMON to experiment-level
and suite-level selection:

* evidence gating (structural, not merely reported),
* explicit criteria evaluation (missing evidence is never positive),
* deterministic candidate ranking with stable tie-breaking,
* descriptive conclusion generation.

It operates purely on the unified :class:`SelectionCandidate`
projection, so the experiment and suite engines only differ in how they
project their persisted source data into candidates. No trading,
validation, pipeline, research, registry, query or suite logic is
duplicated here.

Design rules:

* No duplication of any existing logic. A candidate's metric fields are
  already read from persisted data by the engine that built it.

* Evidence safety is structural. INSUFFICIENT / PARTIAL entities are
  marked NOT_ELIGIBLE and can never become CANDIDATE or SELECTED. A
  winner is never manufactured when no eligible SUFFICIENT candidate
  exists.

* Missing evidence is NEVER treated as positive evidence.

* Determinism. The ranking is a pure function of the candidate metric
  fields; ties are broken by ascending entity id so identical inputs
  always produce identical outputs. No random selection.

* No print() inside this module.
"""

from __future__ import annotations

from typing import Sequence

from engine.models.experiment import ExperimentEvidenceStatus
from engine.models.selection import (
    PromotionDecision,
    RejectionReason,
    SelectionCandidate,
    SelectionCriteria,
    SelectionResult,
    SelectionStatus,
    SelectionType,
    SelectedResult,
)
from engine.selection.identity import SelectionIdentity


# ============================================================
# EVIDENCE GATING
# ============================================================


def _is_eligible(status: ExperimentEvidenceStatus) -> bool:
    """
    Structural evidence gating for an EXPERIMENT candidate.

    Only SUFFICIENT evidence is eligible to become a CANDIDATE (and
    therefore to be SELECTED). INSUFFICIENT and PARTIAL evidence are
    NOT_ELIGIBLE -- an INSUFFICIENT experiment can never become
    CANDIDATE or SELECTED, and a PARTIAL experiment can never become
    SELECTED.
    """

    return status == ExperimentEvidenceStatus.SUFFICIENT


# ============================================================
# CRITERIA EVALUATION
# ============================================================


def _evaluate_criteria(
    candidate: SelectionCandidate,
    criteria: SelectionCriteria,
) -> str | None:
    """
    Evaluate the explicit criteria against a SUFFICIENT candidate.

    Returns ``None`` when the candidate satisfies every active criterion
    (it becomes a CANDIDATE), or the deterministic rejection reason
    string naming the FIRST failing criterion otherwise.

    Missing evidence is NEVER treated as positive evidence:

    * ``require_robust`` rejects ``robust is None`` (not assessed) and
      ``robust is False``.
    * ``require_oos_evidence`` rejects the absence of OOS evidence
      (``oos_expectancy is None`` or ``oos_trades == 0``).
    * ``require_reproducible`` rejects ``reproducible is False``.
    """

    if (
        criteria.require_evidence_status is not None
        and candidate.evidence_status != criteria.require_evidence_status
    ):
        return (
            f"evidence status {candidate.evidence_status.value} does not "
            f"match required {criteria.require_evidence_status.value}"
        )

    if (
        criteria.min_expectancy is not None
        and candidate.expectancy < criteria.min_expectancy
    ):
        return (
            f"expectancy {candidate.expectancy:.4f} below minimum "
            f"{criteria.min_expectancy:.4f}"
        )

    if (
        criteria.min_total_r is not None
        and candidate.total_r < criteria.min_total_r
    ):
        return (
            f"total R {candidate.total_r:.4f} below minimum "
            f"{criteria.min_total_r:.4f}"
        )

    if (
        criteria.max_drawdown_r is not None
        and candidate.max_drawdown_r > criteria.max_drawdown_r
    ):
        return (
            f"max drawdown {candidate.max_drawdown_r:.4f} exceeds "
            f"maximum {criteria.max_drawdown_r:.4f}"
        )

    if criteria.require_robust and candidate.robust is not True:
        if candidate.robust is None:
            return "robustness required but not assessed (missing robustness)"
        return "robustness required but the experiment is not robust"

    if criteria.require_oos_evidence and not candidate.has_oos_evidence:
        return "out-of-sample evidence required but none available"

    if criteria.require_reproducible and not candidate.reproducible:
        return "reproducibility required but the entity is not reproducible"

    if (
        criteria.min_completed_trades is not None
        and candidate.completed_trades < criteria.min_completed_trades
    ):
        return (
            f"completed trades {candidate.completed_trades} below minimum "
            f"{criteria.min_completed_trades}"
        )

    return None


# ============================================================
# DETERMINISTIC RANKING
# ============================================================


def _rank_key(candidate: SelectionCandidate) -> tuple:
    """
    Deterministic sort key for ranking CANDIDATEs.

    Lower tuple sorts FIRST (i.e. is "better"). The priority order, per
    the Sprint 11N specification, is:

        1. sufficient evidence  (all candidates are SUFFICIENT here, so
           this is a constant discriminator kept for clarity),
        2. robustness            (robust first; missing robustness is
           NOT treated as robust),
        3. OOS evidence          (has OOS first; missing OOS is NOT
           treated as successful OOS),
        4. expectancy            (higher first),
        5. total R               (higher first),
        6. lower maximum drawdown (lower first),
        7. reproducibility       (reproducible first),
        8. entity id             (ascending -- deterministic tie-break;
           no random selection).

    Ties on every metric are broken by ascending entity id so two
    identical persisted datasets always produce the same selected
    entity.
    """

    sufficient_rank = 0 if candidate.eligible else 1
    robust_rank = 0 if candidate.robust is True else 1
    oos_rank = 0 if candidate.has_oos_evidence else 1

    return (
        sufficient_rank,
        robust_rank,
        oos_rank,
        -candidate.expectancy,
        -candidate.total_r,
        candidate.max_drawdown_r,
        0 if candidate.reproducible else 1,
        candidate.entity_id,
    )


def _rank_candidates(
    candidates: Sequence[SelectionCandidate],
) -> list[SelectionCandidate]:
    """
    Return the candidates sorted by the deterministic ranking key.

    The input is assumed to already be CANDIDATEs (SUFFICIENT + passing
    all criteria). The first element of the result is the SELECTED
    entity.
    """

    return sorted(candidates, key=_rank_key)


# ============================================================
# SELECTION ASSEMBLY
# ============================================================


def build_selection_result(
    identity: SelectionIdentity,
    candidates: Sequence[SelectionCandidate],
) -> SelectionResult:
    """
    Assemble a :class:`SelectionResult` from the projected candidates.

    This applies the structural evidence gating, the explicit criteria
    evaluation and the deterministic ranking, then marks exactly one
    CANDIDATE as SELECTED (or none when no eligible SUFFICIENT candidate
    exists). It also builds the rejection reasons, the promotion
    decision and the descriptive conclusions.

    The candidates MUST be supplied in ascending entity-id order so the
    output ordering is deterministic.
    """

    criteria = identity.criteria

    # Step 1: structural evidence gating + explicit criteria evaluation.
    evaluated: list[SelectionCandidate] = []
    rejected: list[RejectionReason] = []

    eligible_candidates: list[SelectionCandidate] = []

    for candidate in candidates:
        if not candidate.eligible:
            reason = _ineligibility_reason(candidate)
            evaluated.append(
                _with_status(
                    candidate,
                    SelectionStatus.NOT_ELIGIBLE,
                    reason,
                )
            )
            rejected.append(
                RejectionReason(
                    entity_id=candidate.entity_id,
                    status=SelectionStatus.NOT_ELIGIBLE,
                    reason=reason,
                    evidence_status=candidate.evidence_status,
                )
            )
            continue

        failure = _evaluate_criteria(candidate, criteria)
        if failure is not None:
            evaluated.append(
                _with_status(
                    candidate,
                    SelectionStatus.REJECTED,
                    failure,
                )
            )
            rejected.append(
                RejectionReason(
                    entity_id=candidate.entity_id,
                    status=SelectionStatus.REJECTED,
                    reason=failure,
                    evidence_status=candidate.evidence_status,
                )
            )
            continue

        eligible_candidates.append(
            _with_status(candidate, SelectionStatus.CANDIDATE, None)
        )

    # Step 2: deterministic ranking among CANDIDATEs.
    ranked = _rank_candidates(eligible_candidates)

    # Step 3: promote at most ONE candidate.
    selected: SelectedResult | None = None
    if ranked:
        top = ranked[0]
        ranked[0] = _with_status(top, SelectionStatus.SELECTED, None)
        selected = SelectedResult(
            entity_id=top.entity_id,
            label=top.label,
            selection_type=top.selection_type,
            rationale=_selection_rationale(top, len(ranked)),
        )

    # Reassemble the full candidate list in ascending entity-id order,
    # with statuses applied.
    final = _reassemble(evaluated, ranked)

    promotion = _promotion_decision(selected, len(ranked))

    conclusions = _conclusions(
        selection_type=identity.selection_type,
        total=len(final),
        candidate_count=len(ranked),
        selected=selected,
    )

    return SelectionResult(
        selection_id=identity.selection_id,
        label=identity.label,
        selection_type=identity.selection_type,
        criteria=identity.criteria,
        candidates=tuple(final),
        rejected=tuple(rejected),
        selected=selected,
        promotion=promotion,
        conclusions=tuple(conclusions),
        metadata=dict(identity.metadata),
    )


# ============================================================
# INTERNAL HELPERS
# ============================================================


def _with_status(
    candidate: SelectionCandidate,
    status: SelectionStatus,
    reason: str | None,
) -> SelectionCandidate:
    """Return a copy of ``candidate`` with the given status/reason."""

    from dataclasses import replace

    return replace(candidate, status=status, rejection_reason=reason)


def _ineligibility_reason(candidate: SelectionCandidate) -> str:
    """Deterministic reason for an ineligible (non-SUFFICIENT) entity."""

    status = candidate.evidence_status
    if status == ExperimentEvidenceStatus.INSUFFICIENT:
        return (
            "INSUFFICIENT evidence: cannot become a CANDIDATE or be "
            "SELECTED"
        )
    if status == ExperimentEvidenceStatus.PARTIAL:
        return (
            "PARTIAL evidence: cannot be SELECTED (provisional only)"
        )
    return f"evidence status {status.value} is not eligible for selection"


def _selection_rationale(
    candidate: SelectionCandidate,
    candidate_count: int,
) -> str:
    """
    Deterministic, descriptive rationale for the selected entity.

    Descriptive only; never a predictive claim or live-trading
    readiness statement.
    """

    parts = [
        f"{candidate.entity_id} was ranked first among "
        f"{candidate_count} eligible SUFFICIENT candidate(s).",
        "Ranking priority: sufficient evidence, robustness, "
        "out-of-sample evidence, expectancy, total R, lower maximum "
        "drawdown, reproducibility.",
    ]
    return " ".join(parts)


def _promotion_decision(
    selected: SelectedResult | None,
    candidate_count: int,
) -> PromotionDecision:
    """Build the promotion decision summarising the outcome."""

    if selected is not None:
        return PromotionDecision(
            promoted=True,
            selected=selected,
            rationale=(
                f"Promoted {selected.entity_id} (descriptive, among "
                f"{candidate_count} eligible SUFFICIENT candidate(s))."
            ),
        )

    return PromotionDecision(
        promoted=False,
        selected=None,
        rationale=(
            "No eligible SUFFICIENT candidate existed; no entity was "
            "promoted (no winner manufactured)."
        ),
    )


def _conclusions(
    selection_type: SelectionType,
    total: int,
    candidate_count: int,
    selected: SelectedResult | None,
) -> list[str]:
    """Descriptive, non-predictive conclusions."""

    conclusions: list[str] = []

    noun = "suite" if selection_type == SelectionType.SUITE else "experiment"
    plural = noun + "s"

    if total == 0:
        conclusions.append(f"No persisted {plural} were evaluated.")
        conclusions.append(
            "Selection findings are descriptive, not predictive; "
            "historical experiment results do not predict future "
            "market performance."
        )
        return conclusions

    conclusions.append(f"Evaluated {total} persisted {plural}.")

    ineligible = total - candidate_count - (
        1 if selected is not None else 0
    )
    if ineligible:
        conclusions.append(
            f"{ineligible} {plural} were NOT_ELIGIBLE or REJECTED and "
            f"could not be promoted."
        )

    if candidate_count == 0 and selected is None:
        conclusions.append(
            "No eligible SUFFICIENT candidate existed; a selected "
            f"{noun} is NOT declared."
        )
    else:
        conclusions.append(
            f"{candidate_count} eligible SUFFICIENT candidate(s) were "
            f"ranked."
        )
        if selected is not None:
            conclusions.append(
                f"Selected {noun} (descriptive): {selected.entity_id}."
            )

    conclusions.append(
        "The selection is DESCRIPTIVE ONLY and is not predictive."
    )
    conclusions.append(
        "Historical performance does not predict future market "
        "performance; selection does NOT imply live-trading readiness."
    )

    return conclusions


def _reassemble(
    evaluated: Sequence[SelectionCandidate],
    ranked: Sequence[SelectionCandidate],
) -> list[SelectionCandidate]:
    """
    Reassemble the full candidate list in ascending entity-id order.

    ``evaluated`` holds NOT_ELIGIBLE / REJECTED / CANDIDATE entries (the
    CANDIDATE entries here are pre-promotion). ``ranked`` holds the
    ranked CANDIDATEs, with the first possibly promoted to SELECTED. We
    discard the pre-promotion CANDIDATE entries from ``evaluated`` and
    use the (possibly promoted) ``ranked`` entries instead, then sort
    everything by entity id for a deterministic baseline.
    """

    ranked_ids = {c.entity_id for c in ranked}
    non_candidate = [c for c in evaluated if c.entity_id not in ranked_ids]
    combined = list(non_candidate) + list(ranked)
    combined.sort(key=lambda c: c.entity_id)
    return combined


__all__ = [
    "_evaluate_criteria",
    "_is_eligible",
    "_rank_key",
    "build_selection_result",
]
