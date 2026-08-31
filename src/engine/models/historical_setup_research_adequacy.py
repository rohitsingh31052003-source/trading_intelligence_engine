"""
Domain models for the historical setup research-adequacy boundary
(Checkpoint 10.7).

Checkpoint 10.7 establishes the historical setup research-adequacy layer
on top of the Checkpoint 10.6 historical setup research-report boundary.
It consumes a single
:class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
and produces a deterministic, immutable adequacy classification that
answers one question:

    Is the historical setup research report adequate for downstream
    descriptive research?

without answering:

    Is this setup profitable?
    Should I trade this setup?
    Should I buy or sell?
    Will this setup work in the future?
    Will this setup succeed in the future?

    Phase 6C Corpus (CorpusEvaluationPoint)
            |
    Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
            |
    Checkpoint 9.5/9.8 Outcome Observations (ForwardReturnObservation,
                                      PriceExcursionObservation)
            |
    Checkpoint 9.9 Evidence Aggregation (SetupEvidenceBatch)
            |
    Checkpoint 9.10 Statistical Evidence Analysis (SetupEvidenceStatisticsReport)
            |
    Checkpoint 9.11 Setup Quality Analysis (SetupQualityReport)
            |
    Checkpoint 10.3 Setup Evaluation (SetupEvaluationResult)
            |
    Checkpoint 10.4 Historical Behavioral Assessment (HistoricalSetupBehaviorReport)
            |
    Checkpoint 10.5 Historical Setup Quality Interpretation
            |
    Checkpoint 10.6 Historical Setup Research Report
            |
    Checkpoint 10.7 Historical Setup Research Adequacy (this layer)

Checkpoint 10.7 is RESEARCH-ADEQUACY CLASSIFICATION ONLY:

* It is NOT a trading strategy, NOT a forecasting engine, NOT a decision
  engine, NOT a scoring layer, NOT a ranking layer, NOT a profitability
  analysis layer.
* It consumes a single
  :class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
  (Checkpoint 10.6) and classifies whether the completed research report
  is adequate for downstream descriptive research. It does NOT re-read
  candles, NOT re-evaluate outcomes, NOT re-run discovery, NOT recompute
  statistics, NOT recompose behavior, NOT reinterpret quality, NOT
  regenerate the research report.
* The Checkpoint 9.9 evidence boundary and all upstream layers remain
  authoritative and are untouched by these models.

DESIGN PRINCIPLE — smallest sufficient input:

The adequacy layer consumes exactly one already-created output:

* :class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
  — provides the evaluation state, all occurrence counts, the minimum
  observation threshold, and full traceability to upstream layers.

No smaller combination suffices: the research report is the single
composition point that already aggregates the evaluation state and all
traceability identifiers. The adequacy layer reads these already-computed
values and classifies research adequacy.

DESIGN PRINCIPLE — "adequate" means adequate for descriptive research only:

The word "adequate" in this layer means:

    adequate to perform the defined historical descriptive analysis.

It does NOT mean:

    good setup
    high-quality opportunity
    likely to succeed
    tradeable

``ADEQUATE_FOR_DESCRIPTIVE_RESEARCH`` means the historical setup evidence
is sufficiently populated for descriptive historical research. It does NOT
imply future validity, profitability, trading usefulness, strategy
validity, future success, or recommendation.

This distinction is preserved explicitly in model docstrings, function
docstrings, classification logic, tests, and AGENTS.md.

DESIGN PRINCIPLE — explicit adequacy states:

The adequacy classification distinguishes three explicit states:

* ``NO_RESEARCH_DATA`` — No historical setup observations exist. No
  descriptive research can be performed.
* ``INSUFFICIENT_RESEARCH_DATA`` — Historical observations exist, but
  the available sample does not satisfy the established evaluation
  requirement (below ``min_observations_for_evaluation``).
* ``ADEQUATE_FOR_DESCRIPTIVE_RESEARCH`` — The historical setup evidence
  is sufficiently populated for descriptive historical research.

These states describe research adequacy only. ``INSUFFICIENT_RESEARCH_DATA``
does NOT imply the setup is bad — it means the available sample does not
satisfy the established evaluation requirement. ``NO_RESEARCH_DATA`` does
NOT imply the setup is invalid — it means no historical observations
exist.

DESIGN PRINCIPLE — classification rules use existing evaluation semantics:

The classification rules use the existing Checkpoint 10.3/10.6 evaluation
semantics rather than inventing a new statistical framework:

* ``NO_RESEARCH_DATA`` when ``evaluation_status == NO_DATA``
* ``INSUFFICIENT_RESEARCH_DATA`` when ``evaluation_status == INSUFFICIENT_DATA``
* ``ADEQUATE_FOR_DESCRIPTIVE_RESEARCH`` when ``evaluation_status == EVALUABLE``

The exact threshold comes from ``min_observations_for_evaluation`` on the
report. No second competing threshold is introduced.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero is a valid observation. A missing value is ``None``.
An insufficient-observation state is explicit. The four states (real 0.0,
None/missing, INSUFFICIENT_DATA, NO_DATA) are never conflated.

DESIGN PRINCIPLE — deterministic:

For identical input, ``assess_historical_setup_research_adequacy``
produces identical output. The ``adequacy_id`` is derived deterministically
from the report identity. No wall-clock time, no randomness, no
unordered iteration.

DESIGN PRINCIPLE — no leakage:

The adequacy layer consumes a single already-computed
:class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`.
It never re-reads candles, never re-evaluates outcomes, and never feeds
information backward into discovery. The point-in-time separation is
preserved.

DESIGN PRINCIPLE — no mutation:

The adequacy layer does not mutate the source research report or any
upstream objects.

DESIGN PRINCIPLE — no reuse of incompatible abstractions:

The following existing abstractions are NOT reused because their
semantics are incompatible with neutral historical research adequacy:

* :class:`~engine.models.historical_setup_evaluation.SetupEvaluationStatus`
  — describes evidence-evaluation sufficiency (NO_DATA/INSUFFICIENT_DATA/
  EVALUABLE), not research-report adequacy. The adequacy layer creates
  its own enum (NO_RESEARCH_DATA/INSUFFICIENT_RESEARCH_DATA/
  ADEQUATE_FOR_DESCRIPTIVE_RESEARCH) to preserve the semantic distinction
  between "evidence is evaluable" and "research is adequate for downstream
  descriptive analysis".
* :class:`~engine.models.historical_setup_quality_interpretation.EvidenceAvailability`
  — describes historical data availability, not research adequacy.
* Sprint 11Y :class:`~engine.models.historical_evidence.EvidenceStrength`
  — operates on trade-level outcomes, not structural setup observations.
* Sprint 11I :class:`~engine.models.research.DataSufficiencyReport`
  — research-pipeline data sufficiency (trades, regimes, OOS), not
  setup-evidence research adequacy.
* Sprint 11S :class:`~engine.models.trade_decision.DecisionScore`
  — trading decision score, explicitly forbidden.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from engine.models.historical_setup_evaluation import SetupEvaluationStatus
from engine.models.historical_setup_research_report import (
    HistoricalSetupResearchReport,
)


class ResearchAdequacy(Enum):
    """
    Descriptive classification of whether a completed historical setup
    research report is adequate for downstream descriptive research.

    This enum describes research-data adequacy only. It does NOT describe
    whether the setup is good or bad, profitable or unprofitable, or
    whether to trade it.

    NO_RESEARCH_DATA
        No historical setup observations exist, so no descriptive research
        can be performed. This does NOT imply the setup is invalid — it
        means no historical observations were found.

    INSUFFICIENT_RESEARCH_DATA
        Historical observations exist, but the available sample does not
        satisfy the established evaluation requirement. This does NOT
        imply the setup is bad — it means the available sample is below
        the evaluation threshold.

    ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
        The historical setup evidence is sufficiently populated for
        descriptive historical research. This does NOT imply future
        validity, profitability, trading usefulness, strategy validity,
        future success, or recommendation.
    """

    NO_RESEARCH_DATA = "NO_RESEARCH_DATA"
    INSUFFICIENT_RESEARCH_DATA = "INSUFFICIENT_RESEARCH_DATA"
    ADEQUATE_FOR_DESCRIPTIVE_RESEARCH = "ADEQUATE_FOR_DESCRIPTIVE_RESEARCH"

    @property
    def is_adequate(self) -> bool:
        """Whether the research is adequate for descriptive research."""
        return self is ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH

    @property
    def has_research_data(self) -> bool:
        """Whether any historical setup observations exist."""
        return self is not ResearchAdequacy.NO_RESEARCH_DATA


def _compute_adequacy_id(report_id: str) -> str:
    """Compute a deterministic adequacy identity hash."""
    return (
        "historical-setup-adequacy-"
        + hashlib.sha256(report_id.encode("utf-8")).hexdigest()[:16]
    )


@dataclass(frozen=True, slots=True)
class HistoricalSetupResearchAdequacy:
    """
    Immutable, deterministic historical setup research-adequacy
    classification (Checkpoint 10.7).

    Consumes a
    :class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
    (Checkpoint 10.6) and classifies whether the completed research
    report is adequate for downstream descriptive research.

    The result is free of trading decisions. It answers only: "Is the
    historical setup research report adequate for downstream descriptive
    research?"

    Identity fields:

    adequacy_id
        Deterministic identifier derived from the report identity.
    report_id
        The source :class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
        identifier (traceability to research report).
    interpretation_id
        The source interpretation identifier (traceability to interpretation).
    behavior_report_id
        The source behavior-report identifier (traceability to behavior).
    evaluation_id
        The source evaluation identifier (traceability to evaluation).
    batch_id
        The source evidence-batch identifier (traceability to evidence).
    criterion_key
        The setup criterion identifier shared by all occurrences.
    instrument
        Canonical instrument name.
    setup_timeframe
        Canonical setup timeframe label.
    context_timeframe
        Canonical context timeframe label.

    Source evaluation state fields:

    evaluation_status
        The descriptive data-sufficiency status (NO_DATA /
        INSUFFICIENT_DATA / EVALUABLE).
    total_occurrence_count
        Total number of occurrences in the batch.
    sufficient_data_count
        Number of occurrences with both observations AVAILABLE.
    insufficient_data_count
        Number of occurrences with insufficient data.
    forward_return_observation_count
        Number of occurrences with a sufficient forward-return observation.
    upward_excursion_observation_count
        Number of occurrences with a sufficient price-excursion observation
        (upward).
    downward_excursion_observation_count
        Number of occurrences with a sufficient price-excursion observation
        (downward).
    min_observations_for_evaluation
        The minimum observation threshold used for evaluation.

    Adequacy classification:

    adequacy
        Descriptive classification of whether the research report is
        adequate for downstream descriptive research.
    reason
        Human-readable explanation of why the classification was assigned.
    """

    # Identity
    adequacy_id: str
    report_id: str
    interpretation_id: str
    behavior_report_id: str
    evaluation_id: str
    batch_id: str
    criterion_key: str
    instrument: str
    setup_timeframe: str
    context_timeframe: str

    # Source evaluation state
    evaluation_status: SetupEvaluationStatus
    total_occurrence_count: int
    sufficient_data_count: int
    insufficient_data_count: int
    forward_return_observation_count: int
    upward_excursion_observation_count: int
    downward_excursion_observation_count: int
    min_observations_for_evaluation: int

    # Adequacy classification
    adequacy: ResearchAdequacy
    reason: str

    @property
    def is_adequate(self) -> bool:
        """Whether the research is adequate for descriptive research."""
        return self.adequacy.is_adequate

    @property
    def has_research_data(self) -> bool:
        """Whether any historical setup observations exist."""
        return self.adequacy.has_research_data

    def __post_init__(self) -> None:
        # --- Count non-negativity ---
        if self.total_occurrence_count < 0:
            raise ValueError("total_occurrence_count must be non-negative.")
        if self.sufficient_data_count < 0:
            raise ValueError("sufficient_data_count must be non-negative.")
        if self.insufficient_data_count < 0:
            raise ValueError("insufficient_data_count must be non-negative.")
        if self.forward_return_observation_count < 0:
            raise ValueError(
                "forward_return_observation_count must be non-negative."
            )
        if self.upward_excursion_observation_count < 0:
            raise ValueError(
                "upward_excursion_observation_count must be non-negative."
            )
        if self.downward_excursion_observation_count < 0:
            raise ValueError(
                "downward_excursion_observation_count must be non-negative."
            )

        # --- Count sum invariants ---
        if (
            self.sufficient_data_count + self.insufficient_data_count
            != self.total_occurrence_count
        ):
            raise ValueError(
                "sufficient_data_count + insufficient_data_count must equal "
                "total_occurrence_count."
            )
        if self.forward_return_observation_count > self.total_occurrence_count:
            raise ValueError(
                "forward_return_observation_count cannot exceed "
                "total_occurrence_count."
            )
        if self.upward_excursion_observation_count > self.total_occurrence_count:
            raise ValueError(
                "upward_excursion_observation_count cannot exceed "
                "total_occurrence_count."
            )
        if (
            self.downward_excursion_observation_count
            > self.total_occurrence_count
        ):
            raise ValueError(
                "downward_excursion_observation_count cannot exceed "
                "total_occurrence_count."
            )

        if self.min_observations_for_evaluation < 1:
            raise ValueError(
                "min_observations_for_evaluation must be at least 1."
            )

        # --- Status-specific invariants ---
        if self.evaluation_status is SetupEvaluationStatus.NO_DATA:
            if self.total_occurrence_count != 0:
                raise ValueError(
                    "NO_DATA requires total_occurrence_count == 0."
                )
            if self.adequacy is not ResearchAdequacy.NO_RESEARCH_DATA:
                raise ValueError(
                    "NO_DATA requires adequacy == NO_RESEARCH_DATA."
                )
        elif self.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "INSUFFICIENT_DATA requires total_occurrence_count > 0."
                )
            if self.adequacy is not ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA:
                raise ValueError(
                    "INSUFFICIENT_DATA requires adequacy == "
                    "INSUFFICIENT_RESEARCH_DATA."
                )
        elif self.evaluation_status is SetupEvaluationStatus.EVALUABLE:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "EVALUABLE requires total_occurrence_count > 0."
                )
            if (
                self.adequacy
                is not ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
            ):
                raise ValueError(
                    "EVALUABLE requires adequacy == "
                    "ADEQUATE_FOR_DESCRIPTIVE_RESEARCH."
                )


def _classify_adequacy(
    evaluation_status: SetupEvaluationStatus,
) -> ResearchAdequacy:
    """Classify research adequacy from the evaluation status."""
    if evaluation_status is SetupEvaluationStatus.NO_DATA:
        return ResearchAdequacy.NO_RESEARCH_DATA
    elif evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
        return ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA
    else:
        return ResearchAdequacy.ADEQUATE_FOR_DESCRIPTIVE_RESEARCH


def _build_reason(
    adequacy: ResearchAdequacy,
    evaluation_status: SetupEvaluationStatus,
    total_occurrence_count: int,
    forward_return_observation_count: int,
    min_observations_for_evaluation: int,
) -> str:
    """Build a human-readable reason for the adequacy classification."""
    if adequacy is ResearchAdequacy.NO_RESEARCH_DATA:
        return (
            "No historical setup observations exist for this setup "
            "criterion, so no descriptive research can be performed."
        )

    if adequacy is ResearchAdequacy.INSUFFICIENT_RESEARCH_DATA:
        return (
            f"Historical observations exist ({total_occurrence_count} "
            f"occurrences, {forward_return_observation_count} "
            f"forward-return observations), but the available sample "
            f"is below the evaluation threshold of "
            f"{min_observations_for_evaluation}. The research report "
            f"is not adequate for downstream descriptive research."
        )

    # ADEQUATE_FOR_DESCRIPTIVE_RESEARCH
    return (
        f"Historical setup evidence is sufficiently populated "
        f"({total_occurrence_count} occurrences, "
        f"{forward_return_observation_count} forward-return "
        f"observations, meeting the evaluation threshold of "
        f"{min_observations_for_evaluation}). The research report "
        f"is adequate for downstream descriptive research."
    )


def assess_historical_setup_research_adequacy(
    report: HistoricalSetupResearchReport,
) -> HistoricalSetupResearchAdequacy:
    """
    Assess whether a completed historical setup research report is
    adequate for downstream descriptive research.

    Consumes a
    :class:`~engine.models.historical_setup_research_report.HistoricalSetupResearchReport`
    (Checkpoint 10.6) and produces a deterministic, immutable adequacy
    classification.

    The function does NOT recompute statistics — it reads the established
    upstream evaluation state and classifies research adequacy. The source
    report is never mutated.

    Classification rules:

    * ``NO_RESEARCH_DATA`` when ``evaluation_status == NO_DATA``
    * ``INSUFFICIENT_RESEARCH_DATA`` when ``evaluation_status == INSUFFICIENT_DATA``
    * ``ADEQUATE_FOR_DESCRIPTIVE_RESEARCH`` when ``evaluation_status == EVALUABLE``

    Args:
        report: The :class:`HistoricalSetupResearchReport` from
            Checkpoint 10.6. Provides the evaluation state, occurrence
            counts, minimum observation threshold, and full traceability.

    Returns:
        An immutable :class:`HistoricalSetupResearchAdequacy`.

    Raises:
        TypeError: If ``report`` is not a
            :class:`HistoricalSetupResearchReport`.
    """
    if not isinstance(report, HistoricalSetupResearchReport):
        raise TypeError(
            "report must be a HistoricalSetupResearchReport, "
            f"got {type(report).__name__!r}."
        )

    adequacy = _classify_adequacy(report.evaluation_status)

    reason = _build_reason(
        adequacy=adequacy,
        evaluation_status=report.evaluation_status,
        total_occurrence_count=report.total_occurrence_count,
        forward_return_observation_count=(
            report.forward_return_observation_count
        ),
        min_observations_for_evaluation=(
            report.min_observations_for_evaluation
        ),
    )

    adequacy_id = _compute_adequacy_id(report.report_id)

    return HistoricalSetupResearchAdequacy(
        adequacy_id=adequacy_id,
        report_id=report.report_id,
        interpretation_id=report.interpretation_id,
        behavior_report_id=report.behavior_report_id,
        evaluation_id=report.evaluation_id,
        batch_id=report.batch_id,
        criterion_key=report.criterion_key,
        instrument=report.instrument,
        setup_timeframe=report.setup_timeframe,
        context_timeframe=report.context_timeframe,
        evaluation_status=report.evaluation_status,
        total_occurrence_count=report.total_occurrence_count,
        sufficient_data_count=report.sufficient_data_count,
        insufficient_data_count=report.insufficient_data_count,
        forward_return_observation_count=(
            report.forward_return_observation_count
        ),
        upward_excursion_observation_count=(
            report.upward_excursion_observation_count
        ),
        downward_excursion_observation_count=(
            report.downward_excursion_observation_count
        ),
        min_observations_for_evaluation=(
            report.min_observations_for_evaluation
        ),
        adequacy=adequacy,
        reason=reason,
    )


__all__ = [
    "HistoricalSetupResearchAdequacy",
    "ResearchAdequacy",
    "assess_historical_setup_research_adequacy",
]
