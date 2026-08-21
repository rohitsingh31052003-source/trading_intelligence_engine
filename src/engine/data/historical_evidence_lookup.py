"""
Historical evidence lookup engine (Product Phase 6E).

This module connects the completed Product Phase 6D historical setup
research output to the EXISTING current-market analysis:

    Phase 6C historical corpus
                |
    Phase 6D historical setup research (PERSISTED, REUSED as-is)
                |
    Phase 6E historical evidence lookup (this module)
                |
    Current market assessment (existing scanner/analysis)
                |
    Combined current + historical intelligence view (presentation)

Phase 6E is CONTEXTUAL INTELLIGENCE, not a new trading decision engine:

* The existing decision architecture remains AUTHORITATIVE. This engine
  produces historical CONTEXT ONLY; it never creates, modifies,
  upgrades, downgrades or overrides the existing decision
  classification, actionability, direction, geometry, trade plan or
  paper-trading eligibility.
* It consumes PERSISTED Phase 6D research results via the existing
  :class:`~engine.data.setup_research_store.SetupResearchStore`. It
  NEVER re-runs the Phase 6D research process (detection, outcome
  evaluation or evidence aggregation).
* The output is DESCRIPTIVE. It is NOT a prediction, NOT a probability
  of future success, NOT a profitability guarantee and NOT a trading
  recommendation.

POINT-IN-TIME GUARANTEE (structural):

* The matching criteria
  (:class:`~engine.models.historical_context.HistoricalEvidenceRequest`)
  carry ONLY information available at the current evaluation time ``T``.
* A historical occurrence is eligible ONLY when it occurred strictly
  BEFORE ``T`` (``occurrence.evaluation_time < T``).
* Of those, an occurrence contributes to statistics / strength ONLY when
  its reused Sprint 11W outcome was already resolved at ``T``
  (``outcome.outcome_timestamp <= T``, or an ambiguous outcome with no
  outcome timestamp). A historical outcome still open at ``T`` is
  EXCLUDED — at ``T`` nobody could have known it.
* The public API accepts NO ``future`` / ``future_candles`` /
  ``lookahead`` parameter. It never reads candles at all.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.historical_evidence_lookup import HistoricalEvidenceLookupEngine
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from engine.config.setup_research_config import SetupResearchConfig
from engine.data.historical_times import canonical_timeframe
from engine.data.setup_research_store import SetupResearchStore
from engine.models.historical_context import (
    HISTORICAL_CONTEXT_LIMITATIONS,
    HISTORICAL_CONTEXT_VERSION,
    HistoricalContextStatus,
    HistoricalEvidenceContext,
    HistoricalEvidenceRequest,
)
from engine.models.setup_research import (
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchResult,
    SetupResearchStatus,
)
from engine.intelligence.performance_analytics import _compute_statistics
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import OutcomeStatus


class HistoricalEvidenceLookupEngine:
    """
    Find historical evidence relevant to a current assessment at ``T``.

    ORCHESTRATION / LOOKUP ONLY: every occurrence, outcome, statistic
    and strength is read from PERSISTED Phase 6D research outputs. The
    Phase 6D research process is NEVER re-run here. Stateless across
    calls; inputs are never mutated.
    """

    def __init__(
        self,
        store: SetupResearchStore | None = None,
        config: SetupResearchConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or SetupResearchConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def lookup(
        self,
        request: HistoricalEvidenceRequest,
        *,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalEvidenceContext:
        """
        Find historical evidence comparable to the current assessment.

        Reads the persisted Phase 6D research results from the
        configured store (never re-runs research), filters occurrences
        by the point-in-time eligibility rules + the request's
        comparison dimensions, and aggregates the matched already
        resolved outcomes via the REUSED Sprint 11X statistics
        computation and the REUSED Sprint 11Y strength vocabulary (via
        the Phase 6D sample gates). NEVER fabricates evidence: when
        nothing comparable is available the status is explicit
        (``NO_MATCH`` / ``RESEARCH_UNAVAILABLE``) and
        ``statistics`` / ``strength`` are ``None``.
        """

        research_found, results = self._results_for(request)
        if not research_found:
            return self._finalize(
                request,
                status=HistoricalContextStatus.RESEARCH_UNAVAILABLE,
                match_key=request.match_key,
                comparable=(),
                resolved=(),
                reason=(
                    "no persisted Phase 6D historical setup research "
                    "result exists for this instrument/timeframe pair; "
                    "historical evidence is UNAVAILABLE (never fabricated).",
                ),
                label=label,
                metadata=metadata,
            )

        comparable: list[SetupOccurrence] = []
        resolved: list[SetupResearchObservation] = []
        research_ids: list[str] = []
        for result in results:
            research_ids.append(result.research_id)
            for observation in result.observations:
                occurrence = observation.occurrence
                if not self._eligible_at(request.evaluation_time, occurrence):
                    continue
                if not self._matches(request, occurrence):
                    continue
                comparable.append(occurrence)
                if self._outcome_resolved_at(request.evaluation_time, observation):
                    resolved.append(observation)

        comparable_sorted = tuple(
            sorted(comparable, key=lambda o: (o.evaluation_time, o.reason)),
        )
        resolved_sorted = tuple(
            sorted(resolved, key=lambda o: (o.evaluation_time, o.outcome.outcome_status.name)),
        )

        if not resolved_sorted:
            return self._finalize(
                request,
                status=HistoricalContextStatus.NO_MATCH,
                match_key=request.match_key,
                comparable=comparable_sorted,
                resolved=(),
                reason=(
                    f"{len(comparable_sorted)} comparable historical "
                    "occurrence(s) found, but none had an outcome state "
                    "already known at the evaluation time; historical "
                    "evidence is UNAVAILABLE (never fabricated)."
                    if comparable_sorted
                    else "no comparable historical occurrence was found in "
                    "the persisted Phase 6D research; historical evidence "
                    "is UNAVAILABLE (never fabricated)."
                ),
                research_ids=tuple(sorted(set(research_ids))),
                label=label,
                metadata=metadata,
            )

        return self._finalize(
            request,
            status=HistoricalContextStatus.AVAILABLE,
            match_key=request.match_key,
            comparable=comparable_sorted,
            resolved=resolved_sorted,
            reason=(
                f"{len(comparable_sorted)} comparable historical "
                f"occurrence(s); {len(resolved_sorted)} with an outcome "
                "state already known at the evaluation time. Historical "
                "evidence is descriptive and observational; it does NOT "
                "modify the authoritative existing decision and does NOT "
                "predict future performance."
            ),
            research_ids=tuple(sorted(set(research_ids))),
            label=label,
            metadata=metadata,
        )

    # ------------------------------------------------------------
    # INTERNAL — RESEARCH RETRIEVAL (persisted 6D output only)
    # ------------------------------------------------------------

    def _results_for(
        self, request: HistoricalEvidenceRequest,
    ) -> tuple[bool, tuple[SetupResearchResult, ...]]:
        """
        Load persisted Phase 6D research results for the requested
        instrument / timeframe pair. Research is NEVER re-run here.

        Returns ``(research_found, researched_results)``: ``research_found``
        is True when ANY persisted research exists for the pair (even an
        honest NO_OCCURRENCES / INSUFFICIENT_DATA result — research
        exists, nothing matched), while ``researched_results`` carries
        only results with usable observations.
        """

        if self.store is None:
            return False, ()
        research_found = False
        matched: list[SetupResearchResult] = []
        for research_id in self.store.list_results():
            result = self.store.load(research_id)
            req = result.request
            if req.instrument != request.instrument:
                continue
            if not self._same_timeframe(request.setup_timeframe, req.setup_timeframe):
                continue
            if not self._same_timeframe(
                request.context_timeframe, req.context_timeframe,
            ):
                continue
            research_found = True
            if result.status is not SetupResearchStatus.RESEARCHED:
                # Honest non-results (NO_OCCURRENCES / INSUFFICIENT_DATA /
                # CORPUS_UNAVAILABLE / INVALID_REQUEST) carry no usable
                # observations; they are skipped, never treated as
                # evidence.
                continue
            matched.append(result)
        return research_found, tuple(sorted(matched, key=lambda r: r.research_id))

    @staticmethod
    def _same_timeframe(left: str, right: str) -> bool:
        """Canonical timeframe comparison (``15M`` == ``15m`` == ``15min``)."""

        if not left or not right:
            return not left and not right
        return canonical_timeframe(left) == canonical_timeframe(right)

    @staticmethod
    def _eligible_at(
        evaluation_time, occurrence: SetupOccurrence,
    ) -> bool:
        """
        A historical occurrence is eligible only when it occurred
        strictly BEFORE the current evaluation time ``T``. When no
        evaluation time is supplied the lookup degenerates honestly to
        "occurrences on record" (used only by tooling; the dashboard
        always supplies ``T``).
        """

        if evaluation_time is None:
            return True
        return occurrence.evaluation_time < evaluation_time

    @staticmethod
    def _outcome_resolved_at(
        evaluation_time, observation: SetupResearchObservation,
    ) -> bool:
        """
        An occurrence contributes to statistics/strength only when its
        reused Sprint 11W outcome was already resolved at ``T``:
        ``outcome_timestamp <= T`` (or an ambiguous outcome carrying no
        outcome timestamp at all — ambiguity is a terminal state).
        """

        if evaluation_time is None:
            return True
        stamp = observation.outcome.outcome_timestamp
        status = observation.outcome.outcome_status
        if stamp is None:
            # Terminal outcome states carrying no single determining
            # candle (per the Sprint 11W model contract) are resolved by
            # construction: BOTH_TOUCHED (ambiguous — no exit
            # fabricated), EXPIRED (mark-to-close), NO_GEOMETRY and
            # INSUFFICIENT_DATA. None of them fabricates an exit/R.
            return status in (
                OutcomeStatus.BOTH_TOUCHED,
                OutcomeStatus.EXPIRED,
                OutcomeStatus.NO_GEOMETRY,
                OutcomeStatus.INSUFFICIENT_DATA,
            )
        return stamp <= evaluation_time

    @staticmethod
    def _matches(
        request: HistoricalEvidenceRequest, occurrence: SetupOccurrence,
    ) -> bool:
        """
        Comparable-situation matching over the reused Phase 6D setup /
        research vocabulary. Every active (non-empty) request dimension
        must equal the occurrence's reused label (logical AND).
        """

        pairs = (
            ("setup_type", occurrence.setup_type),
            ("direction", occurrence.direction),
            ("trend_state", occurrence.trend_state),
            ("range_state", occurrence.range_state),
            ("mtf_alignment", occurrence.mtf_alignment),
        )
        for name, occurrence_value in pairs:
            wanted = getattr(request, name)
            if wanted and wanted != occurrence_value:
                return False
        return True

    # ------------------------------------------------------------
    # INTERNAL — EVIDENCE AGGREGATION (reused semantics)
    # ------------------------------------------------------------

    def _aggregate(
        self, resolved: tuple[SetupResearchObservation, ...],
    ) -> tuple[object, EvidenceStrength, str]:
        """
        Aggregate statistics + strength over the matched already
        resolved outcomes. Statistics are the REUSED Sprint 11X
        ``_compute_statistics`` over the REUSED Sprint 11W
        ``HistoricalOutcome`` objects. Strength reuses the Sprint 11Y
        ``EvidenceStrength`` vocabulary via the Phase 6D sample gates
        (sample size is a HARD GATE). Nothing is recomputed with new
        semantics; nothing is fabricated.
        """

        cfg = self.config
        outcomes = tuple(observation.outcome for observation in resolved)
        stats = _compute_statistics(outcomes)

        if stats.total < cfg.min_sample_total:
            strength = EvidenceStrength.INSUFFICIENT
            reason = (
                f"sample size {stats.total} < minimum "
                f"{cfg.min_sample_total}; the evidence is INSUFFICIENT "
                "regardless of the observed result (sample size is a "
                "hard gate)."
            )
        elif stats.resolved < cfg.min_resolved or stats.valid_r_count < cfg.min_valid_r:
            strength = EvidenceStrength.WEAK
            reason = (
                f"resolved {stats.resolved} / valid-R "
                f"{stats.valid_r_count} observations below the minimum "
                f"gates ({cfg.min_resolved}/{cfg.min_valid_r}); the "
                "evidence is WEAK."
            )
        elif (
            stats.total >= cfg.strong_min_sample
            and stats.resolved >= cfg.strong_min_resolved
            and stats.valid_r_count >= cfg.strong_min_valid_r
        ):
            strength = EvidenceStrength.STRONG
            reason = (
                f"sample {stats.total}, resolved {stats.resolved} and "
                f"valid-R {stats.valid_r_count} observations all meet "
                "the strong gates; the evidence is STRONG. Even STRONG "
                "evidence is descriptive and does NOT guarantee future "
                "performance."
            )
        else:
            strength = EvidenceStrength.MODERATE
            reason = (
                f"sample {stats.total}, resolved {stats.resolved} and "
                f"valid-R {stats.valid_r_count} observations meet the "
                "minimum gates but not the strong gates; the evidence "
                "is MODERATE."
            )
        return stats, strength, reason

    # ------------------------------------------------------------
    # INTERNAL — RESULT ASSEMBLY
    # ------------------------------------------------------------

    def _finalize(
        self,
        request: HistoricalEvidenceRequest,
        *,
        status: HistoricalContextStatus,
        match_key: str,
        comparable: tuple[SetupOccurrence, ...],
        resolved: tuple[SetupResearchObservation, ...],
        reason: str,
        research_ids: tuple[str, ...] = (),
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalEvidenceContext:
        statistics = None
        strength = None
        completed = ambiguous = unresolved = 0
        if status is HistoricalContextStatus.AVAILABLE:
            statistics, strength, strength_reason = self._aggregate(resolved)
            reason = f"{reason} {strength_reason}"
            for observation in resolved:
                status_name = observation.outcome.outcome_status
                if status_name in (
                    OutcomeStatus.TARGET_HIT,
                    OutcomeStatus.STOP_HIT,
                    OutcomeStatus.EXPIRED,
                ):
                    completed += 1
                elif status_name is OutcomeStatus.BOTH_TOUCHED:
                    ambiguous += 1
                else:
                    unresolved += 1

        context_id = self._context_id(
            request, status, comparable, resolved, research_ids, label, metadata,
        )
        return HistoricalEvidenceContext(
            context_id=context_id,
            request=request,
            status=status,
            match_key=match_key,
            comparable_occurrences=len(comparable),
            completed_outcomes=completed,
            ambiguous_count=ambiguous,
            unresolved_count=unresolved,
            statistics=statistics,
            strength=strength,
            research_ids=research_ids,
            reason=reason,
            limitations=HISTORICAL_CONTEXT_LIMITATIONS,
            label=label,
            metadata=tuple(metadata) if metadata else (),
        )

    @staticmethod
    def _context_id(
        request: HistoricalEvidenceRequest,
        status: HistoricalContextStatus,
        comparable: tuple[SetupOccurrence, ...],
        resolved: tuple[SetupResearchObservation, ...],
        research_ids: tuple[str, ...],
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> str:
        canonical = {
            "version": HISTORICAL_CONTEXT_VERSION,
            "instrument": request.instrument,
            "setup_timeframe": request.setup_timeframe,
            "context_timeframe": request.context_timeframe,
            "evaluation_time": (
                request.evaluation_time.isoformat()
                if request.evaluation_time is not None
                else ""
            ),
            "dimensions": request.match_dimensions,
            "status": status.name,
            "occurrences": [
                (o.evaluation_time.isoformat(), o.setup_type, o.direction)
                for o in comparable
            ],
            "resolved": [
                (o.evaluation_time.isoformat(), o.outcome.outcome_status.name)
                for o in resolved
            ],
            "research_ids": list(research_ids),
            "label": label,
            "metadata": sorted(tuple(metadata)) if metadata else [],
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str).encode("utf-8"),
        ).hexdigest()
        return f"hectx-{digest[:16]}"


__all__ = ["HistoricalEvidenceLookupEngine"]
