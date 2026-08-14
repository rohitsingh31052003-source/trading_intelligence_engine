"""
Trade opportunity filter / ranking engine (Sprint 11T).

``TradeOpportunityEngine`` turns one or more Sprint 11S
``TradeDecision`` objects into a deterministic, descriptive TRADE
OPPORTUNITY view: which candidates are eligible to be considered an
opportunity, which are filtered out, and which is the best available
opportunity at this evaluation point. It is the fifth step of the
separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)  <- this layer
    6. TRADE VALIDATION             (future)
    7. SIGNAL / EXECUTION           (future)

The engine is deterministic, pure, future-leakage safe and independent
from the historical evaluation runner. It reads ONLY the already-computed
``TradeDecision`` (Sprint 11S), which is itself derived from
``candles[:T+1]`` only; it inspects no candles directly and therefore
cannot introduce look-ahead bias.

DESIGN PRINCIPLE — distinct from Sprint 11S:

Sprint 11S answers "How strong is this candidate?" (per-candidate
decision classification + score). Sprint 11T answers "Should this
candidate actually be surfaced as the best available trade opportunity
at this evaluation point?" (eligibility filter + relative ranking +
opportunity status). The opportunity layer reuses the Sprint 11S
``TradeDecision`` verbatim (by reference); it does NOT recompute
candidate / decision logic, scores, classifications, geometry or
risk/reward.

DESIGN PRINCIPLE — descriptive evidence, not prediction:

An opportunity status (BEST / ALTERNATIVE / WATCH / NO_OPPORTUNITY)
identifies the best AVAILABLE technical opportunity among the
eligible candidates. It is NOT a probability of success, NOT a
profitability prediction, and NOT a trading recommendation.

DESIGN PRINCIPLE — honest geometry:

Incomplete entry / stop / target geometry is reported honestly. No
risk/reward value is ever fabricated. A candidate with incomplete
geometry may remain a WATCH opportunity (depending on config) but, by
default, can never be the BEST opportunity unless the configured
ranking determines it is strictly strongest among clean peers.

DESIGN PRINCIPLE — LONG / SHORT symmetry:

Direction never biases the ranking. A SHORT candidate never ranks
above a LONG candidate (or vice versa) merely because of its
direction; direction only influences the ranking through the
underlying evidence / decision scores. The final tie-breaker orders
LONG before SHORT only as a deterministic, direction-symmetric
last resort when ALL evidence-based keys are identical.

DESIGN PRINCIPLE — no manufactured winner:

A best opportunity is NEVER manufactured. When no candidate is
eligible, ``best`` is ``None``. An alternative is NEVER manufactured
either: an ALTERNATIVE_OPPORTUNITY is produced ONLY when a strictly
stronger eligible candidate is surfaced as the best opportunity; when
only one eligible opportunity exists, no alternative is produced.

BEST-OPPORTUNITY CAPS:

The strongest eligible candidate is the BEST opportunity ONLY when it
also clears the configured best-opportunity caps (no conflicting
evidence when ``require_no_conflict_for_best``, complete geometry when
``require_geometry_for_best``, and confluence at or above
``min_confluence_for_best``). If the strongest eligible candidate
fails a best cap, NO best opportunity is surfaced (``best=None``): the
engine never promotes a weaker candidate to best over a stronger one
that was merely capped. This keeps the ranking strict, obvious and
honest — there is no "the best candidate was conflicted so the
second-best became best" silent substitution.

ALTERNATIVE eligibility uses the SAME caps: an alternative must itself
be best-quality (clean, complete geometry, sufficient confluence), so
a conflicting / incomplete-geometry / low-confluence eligible candidate
is a WATCH opportunity, never an alternative. An alternative is
surfaced ONLY when a strictly stronger best exists.

OPPORTUNITY RANKING POLICY (applied in order, ties by the next key,
finally a direction-symmetric deterministic tie-break; no randomness):

1. ELIGIBILITY — ineligible candidates never rank as surfaced
   opportunities.
2. DECISION_CLASSIFICATION — stronger Sprint 11S classification first
   (PREFERRED > QUALIFIED > WATCH > REJECTED).
3. DECISION_SCORE — higher Sprint 11S decision score first.
4. GEOMETRY_COMPLETENESS — complete geometry first.
5. RISK_REWARD — higher risk/reward ratio first (absent last; never
   fabricated).
6. CONFLUENCE — higher confluence score first.
7. CONFLICT_FREE — fewer conflicting evidence sources first.
8. SETUP_STRENGTH — more supporting evidence sources first.
9. DETERMINISTIC_TIEBREAK — direction order (LONG < SHORT < NONE), then
   evaluation index asc, then entry reference asc. Direction only
   matters here as a final, direction-symmetric last resort; it never
   makes a SHORT outrank a LONG (or vice versa) on evidence.

This is intelligence, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g.
``from engine.intelligence.trade_opportunity import TradeOpportunityEngine``.
"""

from __future__ import annotations

from datetime import datetime

from engine.config.trade_opportunity_config import TradeOpportunityConfig
from engine.models.opportunity import (
    EligibilityReason,
    EligibilityStatus,
    OpportunityStatus,
    TradeOpportunity,
    TradeOpportunityRanking,
)
from engine.models.trade_candidate import CandidateDirection, CandidateStatus
from engine.models.trade_decision import DecisionClassification, TradeDecision


# Classification strength ordering (reused from Sprint 11S via the
# ``rank_value`` property) so the opportunity layer never re-invents
# the relative strength of decision classifications.
def _decision_strength(decision: TradeDecision) -> int:
    return decision.classification.rank_value


# Direction order used ONLY as the final, direction-symmetric
# tie-breaker. LONG before SHORT before NONE; this never makes a
# SHORT outrank a LONG on evidence — it only breaks exact evidence
# ties deterministically.
_DIRECTION_ORDER = {
    CandidateDirection.LONG: 0,
    CandidateDirection.SHORT: 1,
    CandidateDirection.NONE: 2,
}


class TradeOpportunityEngine:
    """
    Produce a deterministic, descriptive trade opportunity view over
    one or more Sprint 11S ``TradeDecision`` objects.

    Public API:

        evaluate(decision, index, timestamp) -> TradeOpportunity
        rank(decisions, index, timestamp) -> TradeOpportunityRanking

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(
        self,
        config: TradeOpportunityConfig | None = None,
    ) -> None:
        self.config = config or TradeOpportunityConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def evaluate(
        self,
        decision: TradeDecision,
        index: int,
        timestamp: datetime | None = None,
    ) -> TradeOpportunity:
        """
        Produce a descriptive ``TradeOpportunity`` over a single
        decision.

        This is a STANDALONE evaluation (no peer context): it reports
        the eligibility verdict and a provisional status
        (ELIGIBLE -> WATCH, INELIGIBLE -> NO_OPPORTUNITY) with a
        provisional rank. The relative BEST / ALTERNATIVE assignment is
        performed ONLY by ``rank``, which has the peer context.

        ``index`` and ``timestamp`` identify the evaluation point (the
        pipeline passes the trigger candle's index / timestamp). The
        decision is retained by reference and never modified.
        """

        reasons = self._evaluate_eligibility(decision)
        eligible = all(r.passed for r in reasons)
        eligibility = (
            EligibilityStatus.ELIGIBLE
            if eligible
            else EligibilityStatus.INELIGIBLE
        )
        rejection = "" if eligible else self._rejection_summary(reasons)

        # Provisional status/rank satisfy the model invariants without
        # peer context. ``rank`` finalizes the relative assignment.
        if eligible:
            status = OpportunityStatus.WATCH
            rank = 1
        else:
            status = OpportunityStatus.NO_OPPORTUNITY
            rank = 0

        return TradeOpportunity(
            timestamp=timestamp,
            evaluation_index=index,
            decision=decision,
            direction=decision.direction,
            rank=rank,
            status=status,
            eligibility=eligibility,
            eligibility_reasons=tuple(reasons),
            decision_classification=decision.classification.name,
            decision_score=decision.decision_score,
            geometry_complete=decision.geometry_complete,
            confluence_score=decision.confluence_score,
            supporting_count=decision.supporting_count,
            conflicting_count=decision.conflicting_count,
            risk_reward_ratio=decision.risk_reward_ratio,
            rejection_reason=rejection,
            ranking_reason=self._standalone_reason(eligible, decision),
        )

    def rank(
        self,
        decisions,
        index: int,
        timestamp: datetime | None = None,
    ) -> TradeOpportunityRanking:
        """
        Produce a deterministic, descriptive ranking over one or more
        decisions.

        Eligible candidates are ranked strongest-first by the
        documented opportunity ranking policy. The strongest eligible
        candidate is the BEST opportunity ONLY when it also clears the
        best-opportunity caps; otherwise NO best is surfaced and every
        eligible candidate is a WATCH (an alternative is never
        manufactured without a strictly stronger best). Lower-ranked
        eligible candidates are ALTERNATIVE_OPPORTUNITY, subject to the
        configured ``max_surfaced_opportunities`` cap (best counts as 1
        surfaced); the remainder are WATCH. Ineligible candidates are
        NO_OPPORTUNITY with rank 0 and appear last.
        """

        decision_list = list(decisions)

        # ----------------------------------------------------------
        # Phase 1: per-candidate eligibility evaluation.
        # ----------------------------------------------------------
        per_decision: list[tuple[TradeDecision, list[EligibilityReason]]] = []
        eligible_decisions: list[TradeDecision] = []
        for d in decision_list:
            reasons = self._evaluate_eligibility(d)
            per_decision.append((d, reasons))
            if all(r.passed for r in reasons):
                eligible_decisions.append(d)

        # ----------------------------------------------------------
        # Phase 2: deterministic ranking of eligible candidates.
        # ----------------------------------------------------------
        eligible_sorted = sorted(
            eligible_decisions,
            key=self._ranking_key,
        )

        # ----------------------------------------------------------
        # Phase 3: best-opportunity caps on the strongest eligible.
        # The strongest eligible candidate is the BEST opportunity ONLY
        # when it clears the best caps; otherwise NO best is surfaced
        # (a weaker candidate is never promoted to best over a stronger
        # one that was merely capped). Each candidate's best-cap verdict
        # is re-evaluated in Phase 4 to decide alternative eligibility.
        # ----------------------------------------------------------
        best: TradeDecision | None = None
        if eligible_sorted:
            top = eligible_sorted[0]
            if self._best_downgrade_reason(top) is None:
                best = top

        # ----------------------------------------------------------
        # Phase 4: assign final rank + status per eligible candidate,
        # in eligible-sorted order. Ranks are 1-based unique.
        #
        # An ALTERNATIVE must itself be best-quality (it must clear the
        # same best caps as the best), so a candidate failing any cap is
        # a WATCH opportunity, never an alternative. An alternative is
        # surfaced ONLY when a strictly stronger best exists.
        # ----------------------------------------------------------
        surfaced_count = 0
        # decision identity -> (rank, status, ranking_reason)
        final_map: dict[int, tuple[int, OpportunityStatus, str]] = {}
        for i, d in enumerate(eligible_sorted):
            rank_i = i + 1
            cap_reason = self._best_downgrade_reason(d)
            if d is best:
                # Strongest eligible and cleared the best caps.
                status_i = OpportunityStatus.BEST_OPPORTUNITY
                reason_i = self._best_reason(d)
                surfaced_count += 1
            elif best is not None and cap_reason is None:
                # There IS a best (strictly stronger surfaced) AND this
                # candidate is best-quality -> alternative, subject to
                # the surfaced cap.
                cap = self.config.max_surfaced_opportunities
                if cap is not None and (surfaced_count + 1) > cap:
                    status_i = OpportunityStatus.WATCH
                    reason_i = self._watch_capped_reason(d, rank_i)
                else:
                    status_i = OpportunityStatus.ALTERNATIVE_OPPORTUNITY
                    reason_i = self._alternative_reason(d, rank_i)
                    surfaced_count += 1
            else:
                # Either no best surfaced, or this candidate is not
                # best-quality (failing a best cap). Eligible but a WATCH
                # opportunity — an alternative is never manufactured
                # without a strictly stronger best, and a weaker /
                # conflicting / incomplete-geometry candidate is never
                # surfaced as an alternative.
                status_i = OpportunityStatus.WATCH
                if cap_reason is not None:
                    reason_i = cap_reason
                else:
                    reason_i = self._watch_reason(d, rank_i)
            final_map[id(d)] = (rank_i, status_i, reason_i)

        # ----------------------------------------------------------
        # Phase 5: assemble the final ordered opportunity list.
        # Order: BEST (if any), then ALTERNATIVEs (rank asc), then
        # WATCHes (rank asc), then NO_OPPORTUNITY (ineligible) last.
        # ----------------------------------------------------------
        surfaced: list[tuple[int, TradeDecision]] = []
        watches: list[tuple[int, TradeDecision]] = []
        ineligible: list[TradeDecision] = []
        for d, reasons in per_decision:
            entry = final_map.get(id(d))
            if entry is None:
                ineligible.append(d)
                continue
            rank_i, status_i, _ = entry
            if status_i in (
                OpportunityStatus.BEST_OPPORTUNITY,
                OpportunityStatus.ALTERNATIVE_OPPORTUNITY,
            ):
                surfaced.append((rank_i, d))
            else:
                watches.append((rank_i, d))
        surfaced.sort(key=lambda item: item[0])
        watches.sort(key=lambda item: item[0])

        ordered_decisions = (
            [d for _, d in surfaced]
            + [d for _, d in watches]
            + ineligible
        )

        opportunities: list[TradeOpportunity] = []
        # decision identity -> eligibility reasons (for the final build).
        reasons_map: dict[int, list[EligibilityReason]] = {
            id(d): r for d, r in per_decision
        }
        for d in ordered_decisions:
            opportunities.append(
                self._build_final(
                    d, reasons_map[id(d)], index, timestamp, final_map,
                ),
            )

        best_op = next(
            (o for o in opportunities if o.is_best),
            None,
        )
        eligible_count = len(eligible_decisions)
        rationale = self._ranking_rationale(
            opportunities, best_op, eligible_count,
        )

        return TradeOpportunityRanking(
            timestamp=timestamp,
            evaluation_index=index,
            opportunities=tuple(opportunities),
            candidate_count=len(opportunities),
            eligible_count=eligible_count,
            best=best_op,
            rationale=rationale,
        )

    # ========================================================
    # ELIGIBILITY
    # ========================================================

    def _evaluate_eligibility(
        self,
        decision: TradeDecision,
    ) -> list[EligibilityReason]:
        """
        Evaluate every opportunity eligibility gate.

        A candidate must pass ALL active gates to be ELIGIBLE. Each
        gate is recorded so the verdict is fully auditable.
        """

        candidate = decision.candidate
        reasons: list[EligibilityReason] = []

        # Gate 1: candidate status.
        allowed_statuses = self.config.allowed_candidate_statuses
        passed = candidate.status in allowed_statuses
        reasons.append(
            EligibilityReason(
                gate="candidate_status",
                passed=passed,
                reason=(
                    f"candidate status {candidate.status.name} "
                    f"in allowed {self._names(allowed_statuses)}"
                    if passed
                    else f"candidate status {candidate.status.name} "
                    f"not in allowed {self._names(allowed_statuses)}"
                ),
            ),
        )

        # Gate 2: decision classification.
        allowed_classes = self.config.allowed_decision_classifications
        passed = decision.classification in allowed_classes
        reasons.append(
            EligibilityReason(
                gate="decision_classification",
                passed=passed,
                reason=(
                    f"decision {decision.classification.name} "
                    f"in allowed {self._names(allowed_classes)}"
                    if passed
                    else f"decision {decision.classification.name} "
                    f"not in allowed {self._names(allowed_classes)}"
                ),
            ),
        )

        # Gate 3: decision score.
        score = decision.decision_score
        min_score = self.config.min_decision_score
        passed = score >= min_score
        reasons.append(
            EligibilityReason(
                gate="decision_score",
                passed=passed,
                reason=(
                    f"score {score} >= min {min_score}"
                    if passed
                    else f"score {score} below min {min_score}"
                ),
            ),
        )

        # Gate 4: geometry (optional).
        if self.config.require_geometry:
            passed = decision.geometry_complete
            reasons.append(
                EligibilityReason(
                    gate="geometry",
                    passed=passed,
                    reason=(
                        "geometry complete"
                        if passed
                        else "geometry incomplete (required)"
                    ),
                ),
            )

        # Gate 5: risk/reward (optional, only when available).
        if self.config.min_risk_reward_ratio is not None:
            ratio = decision.risk_reward_ratio
            min_rr = self.config.min_risk_reward_ratio
            if ratio is None:
                # Missing ratio is NOT treated as a failure (reported
                # honestly); the gate is satisfied trivially.
                passed = True
                reason = "risk/reward unavailable; gate not applied"
            else:
                passed = ratio >= min_rr
                reason = (
                    f"risk/reward {ratio:.2f} >= min {min_rr}"
                    if passed
                    else f"risk/reward {ratio:.2f} below min {min_rr}"
                )
            reasons.append(
                EligibilityReason(gate="risk_reward", passed=passed, reason=reason),
            )

        # Gate 6: conflict (optional disqualification).
        if self.config.disqualify_on_conflict:
            conflicts = decision.conflicting_count
            passed = conflicts == 0
            reasons.append(
                EligibilityReason(
                    gate="conflict",
                    passed=passed,
                    reason=(
                        "no conflicting evidence"
                        if passed
                        else f"{conflicts} conflicting source(s) disqualify"
                    ),
                ),
            )

        return reasons

    # ========================================================
    # RANKING
    # ========================================================

    def _ranking_key(self, decision: TradeDecision) -> tuple:
        """
        Deterministic ranking key (strongest first) among ELIGIBLE
        candidates.

        Applied in the documented priority order; ties at each priority
        are broken by the next priority, and finally by a fully
        deterministic, direction-symmetric tie-breaker. No randomness.
        Direction only matters through the underlying evidence / scores
        until the final tie-break.

        Order:

        1. decision classification strength (desc)
        2. decision score (desc)
        3. geometry complete first
        4. risk/reward ratio (desc, absent last; never fabricated)
        5. confluence score (desc)
        6. fewer conflicting evidence sources
        7. setup strength (supporting evidence count, desc)
        8. direction order (LONG < SHORT < NONE) — final tie-break
        9. evaluation index (asc)
        10. entry reference (asc) — final stable tie-break
        """

        c = decision.candidate
        ratio = decision.risk_reward_ratio
        direction_order = _DIRECTION_ORDER[c.direction]
        entry = c.entry_reference
        return (
            -_decision_strength(decision),
            -decision.decision_score,
            0 if decision.geometry_complete else 1,
            -(ratio if ratio is not None else -1.0),
            -decision.confluence_score,
            decision.conflicting_count,
            -decision.supporting_count,
            direction_order,
            decision.evaluation_index,
            entry if entry is not None else float("inf"),
        )

    # ========================================================
    # BEST CAPS
    # ========================================================

    def _best_downgrade_reason(
        self,
        decision: TradeDecision,
    ) -> str | None:
        """
        Return a downgrade reason if the strongest eligible candidate
        cannot be the BEST opportunity, else None.

        Caps (each can only prevent BEST, never promote):

        * conflicting evidence when ``require_no_conflict_for_best``.
        * incomplete geometry when ``require_geometry_for_best``.
        * confluence below ``min_confluence_for_best``.

        These same caps gate ALTERNATIVE eligibility: an alternative
        must itself be best-quality, so a candidate failing any cap is a
        WATCH opportunity (never an alternative).
        """

        d = decision
        if (
            self.config.require_no_conflict_for_best
            and d.conflicting_count > 0
        ):
            return (
                "Conflicting evidence present; not surfaced as best or "
                "alternative opportunity (downgraded to watch)."
            )
        if self.config.require_geometry_for_best and not d.geometry_complete:
            return (
                "Incomplete trade geometry; not surfaced as best or "
                "alternative opportunity (downgraded to watch; geometry "
                "reported honestly)."
            )
        if d.confluence_score < self.config.min_confluence_for_best:
            return (
                f"Confluence {d.confluence_score} below best minimum "
                f"{self.config.min_confluence_for_best}; not surfaced "
                "as best or alternative opportunity."
            )
        return None

    # ========================================================
    # FINAL BUILD
    # ========================================================

    def _build_final(
        self,
        decision: TradeDecision,
        reasons: list[EligibilityReason],
        index: int,
        timestamp: datetime | None,
        final_map: dict[int, tuple[int, OpportunityStatus, str]],
    ) -> TradeOpportunity:
        """
        Build the final ``TradeOpportunity`` for a decision, applying
        the finalized rank/status/reason from ``final_map`` (eligible)
        or NO_OPPORTUNITY + rank 0 (ineligible).
        """

        eligible = all(r.passed for r in reasons)
        rejection = "" if eligible else self._rejection_summary(reasons)
        if eligible:
            rank, status, ranking_reason = final_map[id(decision)]
        else:
            rank, status, ranking_reason = (
                0,
                OpportunityStatus.NO_OPPORTUNITY,
                self._no_opportunity_reason(decision, rejection),
            )
        return TradeOpportunity(
            timestamp=timestamp,
            evaluation_index=index,
            decision=decision,
            direction=decision.direction,
            rank=rank,
            status=status,
            eligibility=(
                EligibilityStatus.ELIGIBLE
                if eligible
                else EligibilityStatus.INELIGIBLE
            ),
            eligibility_reasons=tuple(reasons),
            decision_classification=decision.classification.name,
            decision_score=decision.decision_score,
            geometry_complete=decision.geometry_complete,
            confluence_score=decision.confluence_score,
            supporting_count=decision.supporting_count,
            conflicting_count=decision.conflicting_count,
            risk_reward_ratio=decision.risk_reward_ratio,
            rejection_reason=rejection,
            ranking_reason=ranking_reason,
        )

    # ========================================================
    # RATIONALE
    # ========================================================

    def _standalone_reason(
        self,
        eligible: bool,
        decision: TradeDecision,
    ) -> str:
        if eligible:
            return (
                f"Eligible opportunity candidate ({decision.direction}, "
                f"decision {decision.classification.name}, score "
                f"{decision.decision_score}). Relative best/alternative "
                "assignment requires peer context. Descriptive only; not "
                "predictive and not a guarantee of profitability."
            )
        return (
            "Ineligible for an opportunity (filtered). Descriptive only; "
            "not predictive and not a guarantee of profitability."
        )

    def _best_reason(self, decision: TradeDecision) -> str:
        rr = (
            f"{decision.risk_reward_ratio:.2f}"
            if decision.risk_reward_ratio is not None
            else "unavailable"
        )
        return (
            f"Highest-ranked eligible opportunity based on configured "
            f"decision strength ({decision.classification.name}, score "
            f"{decision.decision_score}), "
            f"{'complete' if decision.geometry_complete else 'incomplete'} "
            f"trade geometry, risk/reward {rr}, confluence "
            f"{decision.confluence_score} and "
            f"{decision.conflicting_count} conflicting source(s). "
            "Descriptive only; not predictive and not a guarantee of "
            "profitability."
        )

    def _alternative_reason(self, decision: TradeDecision, rank: int) -> str:
        return (
            f"Eligible opportunity ranked {rank} (below the best). "
            f"Decision {decision.classification.name}, score "
            f"{decision.decision_score}, confluence "
            f"{decision.confluence_score}, "
            f"{decision.conflicting_count} conflicting source(s). "
            "Descriptive only; not predictive and not a guarantee of "
            "profitability."
        )

    def _watch_reason(self, decision: TradeDecision, rank: int) -> str:
        return (
            f"Eligible opportunity ranked {rank} but classified WATCH "
            "(no best surfaced; an alternative is never manufactured "
            "without a strictly stronger best). Decision "
            f"{decision.classification.name}, score "
            f"{decision.decision_score}. Descriptive only; not "
            "predictive and not a guarantee of profitability."
        )

    def _watch_capped_reason(self, decision: TradeDecision, rank: int) -> str:
        cap = self.config.max_surfaced_opportunities
        return (
            f"Eligible opportunity ranked {rank} but capped at WATCH "
            f"(max_surfaced_opportunities={cap}). Descriptive only; not "
            "predictive and not a guarantee of profitability."
        )

    def _no_opportunity_reason(
        self,
        decision: TradeDecision,
        rejection: str,
    ) -> str:
        return (
            f"No opportunity (filtered): {rejection or 'ineligible'}. "
            "Descriptive only; not predictive and not a guarantee of "
            "profitability."
        )

    def _ranking_rationale(
        self,
        ordered: list[TradeOpportunity],
        best: TradeOpportunity | None,
        eligible_count: int,
    ) -> str:
        if not ordered:
            return "No candidates to rank."
        if best is not None:
            return (
                f"Best opportunity identified at rank 1 "
                f"({best.direction}, {best.status.name}, decision "
                f"{best.decision_classification}, score "
                f"{best.decision_score}) among {eligible_count} eligible "
                f"candidate(s). Ranking is descriptive; not predictive "
                "and not a guarantee of profitability."
            )
        return (
            f"No best opportunity identified ({eligible_count} eligible "
            "candidate(s) but none cleared the best-opportunity caps). "
            "Ranking is descriptive; not predictive and not a guarantee "
            "of profitability."
        )

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _rejection_summary(reasons: list[EligibilityReason]) -> str:
        failed = [r for r in reasons if not r.passed]
        if not failed:
            return ""
        return "; ".join(f"{r.gate}: {r.reason}" for r in failed)

    @staticmethod
    def _names(values) -> str:
        return "{" + ", ".join(v.name for v in values) + "}"


__all__ = ["TradeOpportunityEngine"]
