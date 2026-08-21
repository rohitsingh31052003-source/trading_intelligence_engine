"""
Historical setup research engine (Product Phase 6D).

This module consumes the Product Phase 6C historical research corpus
(:class:`~engine.data.research_corpus.HistoricalResearchCorpusEngine`)
and answers, for the EXISTING setup / structure architecture
(Sprints 11O-11S):

    "When the existing setup structure appeared historically under
    comparable conditions, what happened afterward?"

    Phase 6C Historical Research Corpus
                |
       historical evaluation point T
                |
       existing setup/structure logic (REUSED VERBATIM)
                |
       setup occurrence detected
                |
       historical outcome (REUSED Sprint 11W, forward-only)
                |
       evidence aggregation (REUSED Sprint 11X/11Y semantics)
                |
       Historical Research Evidence

Phase 6D is RESEARCH ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a new
  decision engine, NOT a scoring layer, NOT a paper-trading layer.
* It reuses the existing deterministic components VERBATIM:
  :class:`~engine.intelligence.candle_patterns.CandlePatternEngine`
  (11O), :class:`~engine.intelligence.setup_confluence.SetupConfluenceEngine`
  (11Q), :class:`~engine.intelligence.trade_candidates.TradeCandidateEngine`
  (11R), :class:`~engine.intelligence.trade_decision.TradeDecisionEngine`
  (11S) for DETECTION, and
  :class:`~engine.intelligence.historical_outcome.OutcomeEvaluator`
  (11W) for the forward-only OUTCOME, plus the Sprint 11X statistics
  computation and the Sprint 11Y evidence-strength vocabulary for
  EVIDENCE. NO setup / decision / outcome / evidence logic is
  re-implemented. The existing decision architecture remains
  authoritative: a rejected / non-candidate setup keeps its existing
  classification.
* The output is DESCRIPTIVE historical evidence. It is NOT a
  prediction, NOT a probability of future success, NOT a profitability
  guarantee and NOT a trading recommendation.

POINT-IN-TIME CORRECTNESS (NON-NEGOTIABLE, structural):

* DETECTION: for every evaluation time ``T`` the corpus supplies ONLY
  candles with ``timestamp <= T`` (setup timeframe) and strictly
  completed context candles with ``timestamp < T`` (the Phase 6C
  guarantee). A future candle after ``T`` can NEVER change whether the
  setup existed at ``T``.
* OUTCOME: the reused Sprint 11W evaluator inspects ONLY candles with
  ``timestamp > T`` truncated to the configured forward horizon.
  Mutating candles beyond the horizon never changes the outcome.
* The public API accepts NO ``future`` / ``future_candles`` /
  ``lookahead`` parameter.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.setup_research import HistoricalSetupResearchEngine
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from engine.config.historical_outcome_config import OutcomeConfig
from engine.config.setup_research_config import SetupResearchConfig
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_times import canonical_timeframe
from engine.data.research_corpus import HistoricalResearchCorpusEngine
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.historical_outcome import OutcomeEvaluator
from engine.intelligence.performance_analytics import _compute_statistics
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.research_corpus import CorpusPointStatus
from engine.models.setup_research import (
    SetupEvidence,
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)


#: Research format version carried onto results (research metadata).
SETUP_RESEARCH_VERSION = "setup-research-v1"

#: Explicit skip-reason bucket for VALID corpus points that do not meet
#: the research request's (stricter) minimum-history requirement.
MINIMUM_HISTORY_SKIP = "MINIMUM_HISTORY"


class HistoricalSetupResearchEngine:
    """
    Search the Phase 6C corpus for historical occurrences of the
    EXISTING setup structures and evaluate what happened afterward.

    ORCHESTRATION ONLY: every detection value is read from the reused
    Sprint 11O/11P/11Q/11R/11S engines run on the Phase 6C
    point-in-time state; every outcome value is read from the reused
    Sprint 11W evaluator; every evidence metric reuses the Sprint 11X
    statistics computation. Stateless across calls. Inputs are never
    mutated.
    """

    def __init__(
        self,
        corpus: HistoricalResearchCorpusEngine,
        config: SetupResearchConfig | None = None,
        *,
        pattern_engine: CandlePatternEngine | None = None,
        setup_engine: SetupConfluenceEngine | None = None,
        candidate_engine: TradeCandidateEngine | None = None,
        decision_engine: TradeDecisionEngine | None = None,
    ) -> None:
        self.corpus = corpus
        self.config = config or SetupResearchConfig()
        self._patterns = pattern_engine or CandlePatternEngine()
        self._setups = setup_engine or SetupConfluenceEngine()
        self._candidates = candidate_engine or TradeCandidateEngine()
        self._decisions = decision_engine or TradeDecisionEngine()

    # ------------------------------------------------------------
    # PUBLIC API — DETECTION ONLY
    # ------------------------------------------------------------

    def detect(self, request: SetupResearchRequest) -> tuple[SetupOccurrence, ...]:
        """
        Detect historical setup occurrences for ``request``.

        DETECTION ONLY — no outcome evaluation. This method exists so
        the detection boundary is independently auditable: detection
        NEVER touches the outcome evaluator and NEVER reads a candle
        after ``T``. Filtered occurrences are included here (the
        request filters select occurrences for research; they do not
        change detection). Returns occurrences in deterministic
        chronological grid order.
        """

        occurrences, _, _, _ = self._scan(request)
        return occurrences

    # ------------------------------------------------------------
    # PUBLIC API — FULL RESEARCH
    # ------------------------------------------------------------

    def research(
        self,
        request: SetupResearchRequest,
        *,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> SetupResearchResult:
        """
        Run the full historical setup research for ``request``.

        Detection (data ``<= T`` / context ``< T``) and outcome
        measurement (data strictly ``> T`` within the configured
        forward horizon) are kept strictly separate. The result is
        deterministic, serializable and DESCRIPTIVE ONLY.
        """

        error = self._request_error(request)
        if error is not None:
            return self._empty_result(
                request, SetupResearchStatus.INVALID_REQUEST, error,
                label=label, metadata=metadata,
            )

        series = self._load_series(request)
        if series is None:
            return self._empty_result(
                request,
                SetupResearchStatus.CORPUS_UNAVAILABLE,
                (
                    f"no stored historical corpus data exists for "
                    f"{request.instrument}/{request.setup_timeframe}; the "
                    "corpus is unavailable (never reported as 'no setups')."
                ),
                label=label,
                metadata=metadata,
            )
        if not series:
            return self._empty_result(
                request,
                SetupResearchStatus.CORPUS_UNAVAILABLE,
                (
                    f"the stored historical dataset "
                    f"{request.instrument}/{request.setup_timeframe} is "
                    "empty; the corpus is unavailable."
                ),
                label=label,
                metadata=metadata,
            )

        occurrences, points, valid_points, skip_counts = self._scan(request)

        if not occurrences and points == 0:
            return self._finalize(
                request=request,
                status=SetupResearchStatus.INSUFFICIENT_DATA,
                reason=(
                    "no evaluation point satisfied the minimum-history / "
                    "data-quality requirements; no point could be "
                    "researched (distinct from 'no occurrences')."
                ),
                points_examined=points,
                valid_points=valid_points,
                occurrences=(),
                skip_counts=skip_counts,
                series=series,
                label=label,
                metadata=metadata,
            )

        matched = tuple(o for o in occurrences if self._matches(request, o))

        if not matched:
            status = (
                SetupResearchStatus.NO_OCCURRENCES
                if valid_points > 0
                else SetupResearchStatus.INSUFFICIENT_DATA
            )
            reason = (
                f"{valid_points} valid evaluation point(s) examined; the "
                "existing setup architecture detected no matching "
                "occurrence (an observed result, not a data problem)."
                if status is SetupResearchStatus.NO_OCCURRENCES
                else (
                    "no evaluation point satisfied the minimum-history / "
                    "data-quality requirements; no point could be "
                    "researched."
                )
            )
            return self._finalize(
                request=request,
                status=status,
                reason=reason,
                points_examined=points,
                valid_points=valid_points,
                occurrences=matched,
                occurrences_detected=len(occurrences),
                skip_counts=skip_counts,
                series=series,
                label=label,
                metadata=metadata,
            )

        observations = tuple(
            SetupResearchObservation(
                occurrence=occurrence,
                outcome=self._evaluate_outcome(request, occurrence, series),
            )
            for occurrence in matched
        )
        return self._finalize(
            request=request,
            status=SetupResearchStatus.RESEARCHED,
            reason=(
                f"{len(observations)} historical occurrence(s) of the "
                "existing setup structure detected and evaluated over "
                f"{valid_points} valid evaluation point(s)."
            ),
            points_examined=points,
            valid_points=valid_points,
            occurrences=matched,
            occurrences_detected=len(occurrences),
            skip_counts=skip_counts,
            series=series,
            observations=observations,
            label=label,
            metadata=metadata,
        )

    # ------------------------------------------------------------
    # INTERNAL — REQUEST VALIDATION
    # ------------------------------------------------------------

    def _request_error(self, request: SetupResearchRequest) -> str | None:
        """Validate the request against the corpus configuration."""

        cfg = self.corpus.config
        setup = canonical_timeframe(request.setup_timeframe)
        if setup is None:
            return (
                f"setup_timeframe {request.setup_timeframe!r} is not a "
                "supported timeframe."
            )
        if setup != cfg.setup_timeframe:
            return (
                f"setup_timeframe {setup!r} does not match the corpus "
                f"configuration {cfg.setup_timeframe!r}."
            )
        if request.context_timeframe:
            context = canonical_timeframe(request.context_timeframe)
            if context is None:
                return (
                    f"context_timeframe {request.context_timeframe!r} is "
                    "not a supported timeframe."
                )
            if context != cfg.context_timeframe:
                return (
                    f"context_timeframe {context!r} does not match the "
                    f"corpus configuration {cfg.context_timeframe!r}."
                )
        elif cfg.has_context_timeframe:
            return (
                "context_timeframe is disabled in the request but the "
                f"corpus is configured with {cfg.context_timeframe!r}."
            )
        return None

    # ------------------------------------------------------------
    # INTERNAL — DATA LOADING
    # ------------------------------------------------------------

    def _load_series(
        self, request: SetupResearchRequest,
    ) -> tuple[OHLCVCandle, ...] | None:
        """
        The full stored setup-timeframe series for outcome measurement.

        The window bounds are intentionally NOT applied here: the
        research window bounds DETECTION points; the forward outcome
        horizon may legitimately extend beyond the window end. The
        reused evaluator structurally inspects only candles strictly
        after each occurrence's evaluation time within the horizon.
        ``None`` when the corpus dataset is unavailable.
        """

        service: HistoricalMarketDataService = self.corpus.service
        try:
            slice_ = service.load_historical(
                request.instrument,
                canonical_timeframe(request.setup_timeframe)
                or request.setup_timeframe,
            )
        except Exception:
            return None
        return slice_.candles

    # ------------------------------------------------------------
    # INTERNAL — DETECTION SCAN
    # ------------------------------------------------------------

    def _scan(
        self, request: SetupResearchRequest,
    ) -> tuple[
        tuple[SetupOccurrence, ...],
        int,
        int,
        tuple[tuple[str, int], ...],
    ]:
        """
        Scan the canonical corpus evaluation grid for occurrences.

        Returns (occurrences, points_examined, valid_points,
        skip_counts). Points below the request's minimum-history
        requirement are counted as explicit MINIMUM_HISTORY skips.
        """

        grid = self.corpus.evaluation_points_for(
            request.instrument,
            start=request.start_time,
            end=request.end_time,
        )
        occurrences: list[SetupOccurrence] = []
        valid_points = 0
        skips: dict[str, int] = {}
        for evaluation_time in grid:
            point = self.corpus.evaluation_point(
                request.instrument, evaluation_time,
            )
            if point.status is not CorpusPointStatus.VALID:
                skips[point.status.name] = skips.get(point.status.name, 0) + 1
                continue
            if point.history_count < max(
                request.minimum_history, self.corpus.config.min_setup_history,
            ):
                skips[MINIMUM_HISTORY_SKIP] = skips.get(MINIMUM_HISTORY_SKIP, 0) + 1
                continue
            valid_points += 1
            occurrence = self._detect_at(point)
            if occurrence is not None:
                occurrences.append(occurrence)
        return (
            tuple(occurrences),
            len(grid),
            valid_points,
            tuple(sorted(skips.items())),
        )

    def _detect_at(self, point) -> SetupOccurrence | None:
        """
        Detect ONE occurrence at a VALID corpus evaluation point.

        Reuses the existing 11O -> 11Q -> 11R -> 11S chain VERBATIM on
        the point-in-time prefix (data ``<= T``). Returns ``None`` when
        the existing setup classification is not an occurrence
        classification. The existing decision classification is
        preserved verbatim — never converted into an opportunity.
        """

        state = point.state
        prefix = state.setup_slice.candles
        index = len(prefix) - 1
        timestamp = point.evaluation_time

        patterns = self._patterns.detect(prefix)
        at_index = [p for p in patterns if p.index == index]
        assessment = self._setups.assess(
            at_index, state.setup_context, index, timestamp,
        )
        if assessment.classification.name not in self.config.occurrence_classifications:
            return None

        candidate = self._candidates.generate(
            assessment,
            state.setup_context,
            index,
            timestamp,
            close_price=prefix[-1].close,
        )
        decision = self._decisions.decide(candidate, index, timestamp)

        context = state.setup_context
        return SetupOccurrence(
            instrument=point.instrument,
            setup_timeframe=point.setup_timeframe,
            context_timeframe=point.context_timeframe,
            evaluation_time=timestamp,
            setup_classification=assessment.classification.name,
            setup_direction=assessment.direction.name,
            confluence_score=assessment.confluence_score,
            candidate_status=candidate.status.name,
            decision_classification=decision.classification.name,
            decision_score=decision.decision_score,
            direction=candidate.direction.name,
            setup_type=candidate.setup_type.name,
            trend_state=context.trend.state.name,
            range_state=context.range.state.name,
            mtf_alignment=state.mtf_alignment.name,
            geometry_available=candidate.geometry_complete,
            entry=candidate.entry_reference,
            stop=candidate.stop_reference,
            target=candidate.target_reference,
            reason=(
                f"Existing setup structure detected at {timestamp.isoformat()}: "
                f"{assessment.classification.name}/{assessment.direction.name} "
                f"(confluence {assessment.confluence_score}); candidate "
                f"{candidate.status.name}; decision "
                f"{decision.classification.name} (authoritative, preserved)."
            ),
        )

    def _matches(
        self, request: SetupResearchRequest, occurrence: SetupOccurrence,
    ) -> bool:
        """Whether an occurrence satisfies the request's filters."""

        if request.trend_filter and occurrence.trend_state != request.trend_filter:
            return False
        if request.range_filter and occurrence.range_state != request.range_filter:
            return False
        if (
            request.mtf_alignment_filter
            and occurrence.mtf_alignment != request.mtf_alignment_filter
        ):
            return False
        if request.setup_type_filter and occurrence.setup_type != request.setup_type_filter:
            return False
        if request.direction_filter and occurrence.direction != request.direction_filter:
            return False
        return True

    # ------------------------------------------------------------
    # INTERNAL — OUTCOME EVALUATION (forward-only)
    # ------------------------------------------------------------

    def _evaluate_outcome(
        self,
        request: SetupResearchRequest,
        occurrence: SetupOccurrence,
        series: tuple[OHLCVCandle, ...],
    ) -> HistoricalOutcome:
        """
        Evaluate the realized forward-only outcome for one occurrence.

        Builds the reused Sprint 11W :class:`OutcomeSubject` from the
        reused Sprint 11R candidate references (geometry VERBATIM;
        missing levels stay ``None`` -> NO_GEOMETRY, never invented)
        and delegates to the reused Sprint 11W
        :class:`OutcomeEvaluator`, which structurally inspects ONLY
        candles strictly after ``T`` within the request's forward
        horizon. Same-candle stop+target touches resolve to the
        explicit BOTH_TOUCHED policy — never an arbitrary OHLC order.
        """

        subject = OutcomeSubject(
            instrument=occurrence.instrument,
            direction=occurrence.direction,
            evaluation_timestamp=occurrence.evaluation_time,
            entry=occurrence.entry,
            stop=occurrence.stop,
            target=occurrence.target,
            decision_classification=occurrence.decision_classification,
            decision_score=occurrence.decision_score,
            opportunity_status="",
            rank=0,
            scan_id="",
            setup_timeframe=occurrence.setup_timeframe,
            setup_type=occurrence.setup_type,
            mtf_alignment=occurrence.mtf_alignment,
        )
        evaluator = OutcomeEvaluator(
            OutcomeConfig(max_holding_bars=request.forward_horizon),
        )
        return evaluator.evaluate(subject, series)

    # ------------------------------------------------------------
    # INTERNAL — EVIDENCE AGGREGATION
    # ------------------------------------------------------------

    def _evidence(
        self,
        key: str,
        dimension: str,
        observations: tuple[SetupResearchObservation, ...],
    ) -> SetupEvidence:
        """
        Aggregate ONE descriptive evidence record over observations.

        Reuses the Sprint 11X statistics computation over the reused
        Sprint 11W outcomes and the Sprint 11Y evidence-strength
        vocabulary. Nothing fabricated: counts are counts; unavailable
        metrics remain ``None``; a small sample is NEVER promoted.
        """

        cfg = self.config
        outcomes = tuple(observation.outcome for observation in observations)
        stats = _compute_statistics(outcomes)

        completed = stats.target_hits + stats.stop_hits + stats.expired
        ambiguous = stats.both_touched
        unresolved = stats.insufficient_data
        no_geometry = stats.no_geometry

        if stats.total < cfg.min_sample_total:
            strength = EvidenceStrength.INSUFFICIENT
            reason = (
                f"sample size {stats.total} < minimum {cfg.min_sample_total}; "
                "the evidence is INSUFFICIENT regardless of the observed "
                "result (sample size is a hard gate)."
            )
        elif (
            stats.resolved < cfg.min_resolved
            or stats.valid_r_count < cfg.min_valid_r
        ):
            strength = EvidenceStrength.WEAK
            reason = (
                f"resolved {stats.resolved} / valid-R {stats.valid_r_count} "
                f"observations below the minimum gates "
                f"({cfg.min_resolved}/{cfg.min_valid_r}); the evidence is "
                "WEAK."
            )
        elif (
            stats.total >= cfg.strong_min_sample
            and stats.resolved >= cfg.strong_min_resolved
            and stats.valid_r_count >= cfg.strong_min_valid_r
        ):
            strength = EvidenceStrength.STRONG
            reason = (
                f"sample {stats.total}, resolved {stats.resolved} and "
                f"valid-R {stats.valid_r_count} observations all meet the "
                "strong gates; the evidence is STRONG. Even STRONG "
                "evidence is descriptive and does NOT guarantee future "
                "performance."
            )
        else:
            strength = EvidenceStrength.MODERATE
            reason = (
                f"sample {stats.total}, resolved {stats.resolved} and "
                f"valid-R {stats.valid_r_count} observations meet the "
                "minimum gates but not the strong gates; the evidence is "
                "MODERATE."
            )

        return SetupEvidence(
            key=key,
            dimension=dimension,
            sample_size=stats.total,
            occurrence_count=stats.total,
            completed_outcomes=completed,
            ambiguous_count=ambiguous,
            unresolved_count=unresolved,
            no_geometry_count=no_geometry,
            win_count=stats.target_hits,
            loss_count=stats.stop_hits,
            expired_count=stats.expired,
            statistics=stats,
            strength=strength,
            reason=reason,
        )

    def _group_key(self, dimension: str, occurrence: SetupOccurrence) -> str:
        return {
            "SETUP_TYPE": occurrence.setup_type,
            "DIRECTION": occurrence.direction,
            "TREND": occurrence.trend_state,
            "RANGE_STATE": occurrence.range_state,
            "MTF_ALIGNMENT": occurrence.mtf_alignment,
        }[dimension]

    def _grouped_evidence(
        self,
        observations: tuple[SetupResearchObservation, ...],
    ) -> tuple[SetupEvidence, ...]:
        """Descriptive per-dimension evidence groups (deterministic)."""

        grouped: list[SetupEvidence] = []
        for dimension in self.config.group_dimensions:
            buckets: dict[str, list[SetupResearchObservation]] = {}
            for observation in observations:
                value = self._group_key(dimension, observation.occurrence)
                buckets.setdefault(value, []).append(observation)
            for value in sorted(buckets):
                grouped.append(
                    self._evidence(
                        key=f"{dimension}:{value}",
                        dimension=dimension,
                        observations=tuple(buckets[value]),
                    ),
                )
        return tuple(grouped)

    # ------------------------------------------------------------
    # INTERNAL — RESULT ASSEMBLY
    # ------------------------------------------------------------

    def _research_id(
        self,
        request: SetupResearchRequest,
        occurrences: tuple[SetupOccurrence, ...],
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> str:
        identity = {
            "version": SETUP_RESEARCH_VERSION,
            "request": {
                "instrument": request.instrument,
                "setup_timeframe": request.setup_timeframe,
                "context_timeframe": request.context_timeframe,
                "start_time": (
                    request.start_time.isoformat() if request.start_time else ""
                ),
                "end_time": (
                    request.end_time.isoformat() if request.end_time else ""
                ),
                "forward_horizon": request.forward_horizon,
                "minimum_history": request.minimum_history,
                "trend_filter": request.trend_filter,
                "range_filter": request.range_filter,
                "mtf_alignment_filter": request.mtf_alignment_filter,
                "setup_type_filter": request.setup_type_filter,
                "direction_filter": request.direction_filter,
            },
            "config": list(self.config.snapshot()),
            "corpus": list(self.corpus.config.snapshot()),
            "occurrences": [
                (o.instrument, o.evaluation_time.isoformat())
                for o in occurrences
            ],
            "label": label,
            "metadata": sorted((str(k), str(v)) for k, v in (metadata or ())),
        }
        text = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return "setup-research-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _empty_result(
        self,
        request: SetupResearchRequest,
        status: SetupResearchStatus,
        reason: str,
        *,
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> SetupResearchResult:
        return self._finalize(
            request=request,
            status=status,
            reason=reason,
            points_examined=0,
            valid_points=0,
            occurrences=(),
            label=label,
            metadata=metadata,
        )

    def _finalize(
        self,
        *,
        request: SetupResearchRequest,
        status: SetupResearchStatus,
        reason: str,
        points_examined: int,
        valid_points: int,
        occurrences: tuple[SetupOccurrence, ...],
        occurrences_detected: int | None = None,
        skip_counts: tuple[tuple[str, int], ...] = (),
        series: tuple[OHLCVCandle, ...] = (),
        observations: tuple[SetupResearchObservation, ...] = (),
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> SetupResearchResult:
        completed = sum(1 for o in observations if o.is_completed)
        ambiguous = sum(1 for o in observations if o.is_ambiguous)
        unresolved = sum(
            1
            for o in observations
            if o.outcome_status is OutcomeStatus.INSUFFICIENT_DATA
        )
        evidence = (
            self._evidence("OVERALL", "", observations) if observations else None
        )
        grouped = self._grouped_evidence(observations) if observations else ()
        limitations = (
            "Historical evidence is descriptive and observational. It is "
            "not a prediction, recommendation, or guarantee of future "
            "performance.",
            "Detection uses only data <= T (context strictly < T); "
            "outcomes use only data > T within the configured forward "
            "horizon.",
            "Missing geometry, ambiguous same-candle touches and "
            "insufficient forward data are reported explicitly via the "
            "reused outcome status vocabulary; nothing is fabricated.",
            "Sample size is a hard gate: a small sample is never "
            "promoted regardless of the observed result.",
        )
        if series:
            data_quality = (
                f"{request.instrument}/{request.setup_timeframe}: "
                f"{len(series)} stored candle(s) available for outcome "
                "measurement."
            )
        else:
            data_quality = "corpus series unavailable."
        return SetupResearchResult(
            research_id=self._research_id(request, occurrences, label, metadata),
            request=request,
            status=status,
            points_examined=points_examined,
            valid_points=valid_points,
            occurrences_detected=(
                occurrences_detected
                if occurrences_detected is not None
                else len(occurrences)
            ),
            occurrence_count=len(observations),
            completed_outcomes=completed,
            ambiguous_count=ambiguous,
            unresolved_count=unresolved,
            observations=observations,
            evidence=evidence,
            grouped_evidence=grouped,
            skip_counts=skip_counts,
            rationale=f"{status.name}: {reason} Data: {data_quality}",
            limitations=limitations,
            label=label,
            metadata=tuple(sorted((str(k), str(v)) for k, v in (metadata or ()))),
        )


__all__ = [
    "MINIMUM_HISTORY_SKIP",
    "SETUP_RESEARCH_VERSION",
    "HistoricalSetupResearchEngine",
]
