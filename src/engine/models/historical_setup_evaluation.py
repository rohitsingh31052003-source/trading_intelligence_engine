"""
Domain models for the setup evaluation boundary (Checkpoint 10.3).

Checkpoint 10.3 establishes the first explicit setup-evaluation layer in
the trading-intelligence architecture. It consumes an already-aggregated
:class:`~engine.models.historical_setup_evidence.SetupEvidenceBatch`
(Checkpoint 9.9) and produces a deterministic, immutable evaluation
result that answers one question:

    Does the available historical evidence provide enough information
    to evaluate this setup?

without answering:

    Should I BUY or SELL this setup?

The evaluation layer is strictly downstream of the Checkpoint 9 research
pipeline:

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
    Checkpoint 10.3 Setup Evaluation (this layer)

Checkpoint 10.3 is EVALUATION BOUNDARY ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT a confidence model.
* It consumes an already-aggregated :class:`SetupEvidenceBatch`
  (Checkpoint 9.9) and evaluates data sufficiency. It does NOT re-read
  candles, NOT re-evaluate outcomes, NOT re-run discovery, NOT compute
  statistics, NOT compute quality indicators.
* The Checkpoint 9.9 evidence boundary and all upstream layers remain
  authoritative and are untouched by these models.

DESIGN PRINCIPLE — smallest architecturally appropriate input:

The evaluation layer consumes only :class:`SetupEvidenceBatch`. It does
NOT consume :class:`SetupEvidenceStatisticsReport` or
:class:`SetupQualityReport` because data sufficiency evaluation requires
only occurrence-level counts (total, sufficient, insufficient) — not
derived statistics (means, medians) or quality indicators (proportions,
consistency). The batch already exposes all needed counts via its
properties and occurrences.

DESIGN PRINCIPLE — explicit data sufficiency states:

The evaluation result distinguishes three explicit states:

* ``NO_DATA`` — No historical occurrences exist for this setup criterion.
  There is nothing to evaluate.
* ``INSUFFICIENT_DATA`` — Occurrences exist, but one or more required
  metrics lack sufficient future observations. The available evidence
  is not enough to produce an evaluation.
* ``EVALUABLE`` — The relevant metrics have enough available observations
  to produce an evaluation.

These states are descriptive only. ``INSUFFICIENT_DATA`` does NOT imply
the setup is bad — it means there is not enough evidence to evaluate it.
``NO_DATA`` does NOT imply the setup is invalid — it means no historical
occurrences were found.

DESIGN PRINCIPLE — no forbidden trading semantics:

The evaluation result does NOT contain: BUY, SELL, LONG, SHORT, WIN,
LOSS, PROFITABLE, STOP LOSS, TARGET, RISK/REWARD, POSITION SIZE, TRADE
SCORE, CONFIDENCE SCORE, BLACK-BOX QUALITY SCORE, EXPECTANCY, SHARPE,
or PREDICTION. It describes only whether the historical evidence is
sufficient for evaluation.

DESIGN PRINCIPLE — deterministic:

For identical input evidence, ``evaluate_setup`` produces identical
output. The ``evaluation_id`` is derived deterministically from the
batch identity and evaluation parameters. No wall-clock time, no
randomness, no unordered iteration.

DESIGN PRINCIPLE — no leakage:

The evaluation layer consumes an already-aggregated
:class:`SetupEvidenceBatch`. It never re-reads candles, never re-evaluates
outcomes, and never feeds information backward into discovery. The
point-in-time separation is preserved.

DESIGN PRINCIPLE — no mutation:

The evaluation layer does not mutate the source batch, its occurrences,
or any upstream objects. All inputs are retained by reference and never
modified.

DESIGN PRINCIPLE — no reuse of incompatible abstractions:

The following existing abstractions are NOT reused because their
semantics are incompatible with structural setup evidence evaluation:

* Sprint 11Y :class:`~engine.models.historical_evidence.EvidenceStrength`
  — operates on trade-level outcomes (Sprint 11W HistoricalOutcome with
  entry/stop/target geometry), not structural setup observations.
* Sprint 11Z :class:`~engine.models.strategy_intelligence.StrategyAssessmentStatus`
  — strategy-level interpretation built on trade-outcome evidence.
* Sprint 12A :class:`~engine.models.decision_intelligence.DecisionContextStatus`
  — decision-intelligence context for trade opportunities.
* Sprint 11Q :class:`~engine.models.setup_confluence.SetupClassification`
  — observation-time confluence classification, not evidence sufficiency.
* Sprint 11S :class:`~engine.models.trade_decision.DecisionScore`
  — trading decision score, explicitly forbidden.
* Sprint 11I :class:`~engine.models.research.DataSufficiencyReport`
  — research-pipeline data sufficiency (trades, regimes, OOS), not
  setup-evidence sufficiency.

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

from engine.models.historical_setup_evidence import SetupEvidenceBatch
from engine.models.historical_setup_outcome import ObservationStatus


class SetupEvaluationStatus(Enum):
    """
    The descriptive data-sufficiency status of the historical evidence
    for a setup criterion.

    This enum describes whether the available historical evidence is
    sufficient to evaluate a setup. It does NOT describe whether the
    setup is good or bad, profitable or unprofitable, or whether to
    trade it.

    NO_DATA
        No historical occurrences exist for this setup criterion. There
        is nothing to evaluate. This does NOT imply the setup is invalid
        — it means no historical occurrences were found.

    INSUFFICIENT_DATA
        Occurrences exist, but one or more required metrics lack
        sufficient future observations. The available evidence is not
        enough to produce an evaluation. This does NOT imply the setup
        is bad — it means there is not enough evidence to evaluate it.

    EVALUABLE
        The relevant metrics have enough available observations to
        produce an evaluation. This does NOT imply the setup is good or
        profitable — it means there is enough evidence to evaluate it.
    """

    NO_DATA = "NO_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    EVALUABLE = "EVALUABLE"

    @property
    def is_evaluable(self) -> bool:
        """Whether the evidence is sufficient for evaluation."""
        return self is SetupEvaluationStatus.EVALUABLE

    @property
    def has_data(self) -> bool:
        """Whether any historical occurrences exist."""
        return self is not SetupEvaluationStatus.NO_DATA


def _compute_evaluation_id(
    batch_id: str,
    min_observations_for_evaluation: int,
) -> str:
    """Compute a deterministic evaluation identity hash."""
    identity = f"{batch_id}|min_obs={min_observations_for_evaluation}"
    return "setup-eval-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SetupEvaluationResult:
    """
    Immutable, deterministic evaluation result for a setup criterion.

    Consumes a :class:`SetupEvidenceBatch` (Checkpoint 9.9) and produces
    a descriptive evaluation of whether the available historical evidence
    is sufficient to evaluate the setup.

    The result is free of trading decisions. It answers only: "Does the
    available historical evidence provide enough information to evaluate
    this setup?"

    Attributes:

    evaluation_id
        Deterministic identifier derived from the batch identity and
        evaluation parameters.
    batch_id
        The source :class:`SetupEvidenceBatch` identifier (traceability).
    criterion_key
        The setup criterion identifier shared by all occurrences.
    instrument
        Canonical instrument name.
    setup_timeframe
        Canonical setup timeframe label.
    context_timeframe
        Canonical context timeframe label.
    evaluation_status
        The descriptive data-sufficiency status.
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
        The minimum observation threshold used for this evaluation.
    reason
        Human-readable explanation of the evaluation result.
    """

    evaluation_id: str
    batch_id: str
    criterion_key: str
    instrument: str
    setup_timeframe: str
    context_timeframe: str
    evaluation_status: SetupEvaluationStatus
    total_occurrence_count: int
    sufficient_data_count: int
    insufficient_data_count: int
    forward_return_observation_count: int
    upward_excursion_observation_count: int
    downward_excursion_observation_count: int
    min_observations_for_evaluation: int
    reason: str

    def __post_init__(self) -> None:
        if self.total_occurrence_count < 0:
            raise ValueError("total_occurrence_count must be non-negative.")
        if self.sufficient_data_count < 0:
            raise ValueError("sufficient_data_count must be non-negative.")
        if self.insufficient_data_count < 0:
            raise ValueError("insufficient_data_count must be non-negative.")
        if (
            self.sufficient_data_count + self.insufficient_data_count
            != self.total_occurrence_count
        ):
            raise ValueError(
                "sufficient_data_count + insufficient_data_count must equal "
                "total_occurrence_count."
            )
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

        # Status-specific invariants.
        if self.evaluation_status is SetupEvaluationStatus.NO_DATA:
            if self.total_occurrence_count != 0:
                raise ValueError(
                    "NO_DATA requires total_occurrence_count == 0."
                )
        elif self.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "INSUFFICIENT_DATA requires total_occurrence_count > 0."
                )
            if (
                self.forward_return_observation_count >= self.min_observations_for_evaluation
                and self.upward_excursion_observation_count >= self.min_observations_for_evaluation
            ):
                raise ValueError(
                    "INSUFFICIENT_DATA requires at least one metric below "
                    "min_observations_for_evaluation."
                )
        elif self.evaluation_status is SetupEvaluationStatus.EVALUABLE:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "EVALUABLE requires total_occurrence_count > 0."
                )
            if (
                self.forward_return_observation_count < self.min_observations_for_evaluation
                or self.upward_excursion_observation_count < self.min_observations_for_evaluation
            ):
                raise ValueError(
                    "EVALUABLE requires all metrics at or above "
                    "min_observations_for_evaluation."
                )


def evaluate_setup(
    batch: SetupEvidenceBatch,
    *,
    min_observations_for_evaluation: int = 10,
) -> SetupEvaluationResult:
    """
    Evaluate whether the historical evidence is sufficient to evaluate a setup.

    Consumes a :class:`SetupEvidenceBatch` (Checkpoint 9.9) and produces
    a deterministic, immutable evaluation result. The evaluation counts
    occurrences with sufficient data for each required metric and compares
    against the minimum threshold.

    Required metrics:
    * Forward return — occurrences whose :class:`ForwardReturnObservation`
      carries :attr:`ObservationStatus.AVAILABLE`.
    * Price excursion — occurrences whose :class:`PriceExcursionObservation`
      carries :attr:`ObservationStatus.AVAILABLE`.

    The source batch and its occurrences are never mutated.

    Args:
        batch: The :class:`SetupEvidenceBatch` to evaluate (Checkpoint 9.9).
        min_observations_for_evaluation: The minimum number of observations
            required per metric for the evidence to be considered evaluable.
            Must be at least 1. Default: 10.

    Returns:
        An immutable :class:`SetupEvaluationResult`.

    Raises:
        ValueError: If ``min_observations_for_evaluation < 1``.
    """
    if min_observations_for_evaluation < 1:
        raise ValueError(
            "min_observations_for_evaluation must be at least 1."
        )

    total = batch.total_occurrences
    sufficient = batch.sufficient_data_count
    insufficient = batch.insufficient_data_count

    # Count per-metric observations.
    forward_return_observation_count = 0
    upward_excursion_observation_count = 0
    downward_excursion_observation_count = 0

    for occ in batch.occurrences:
        if (
            occ.forward_return is not None
            and occ.forward_return.observation_status
            is ObservationStatus.AVAILABLE
        ):
            forward_return_observation_count += 1

        if (
            occ.price_excursion is not None
            and occ.price_excursion.observation_status
            is ObservationStatus.AVAILABLE
        ):
            upward_excursion_observation_count += 1
            downward_excursion_observation_count += 1

    # Determine evaluation status.
    if total == 0:
        evaluation_status = SetupEvaluationStatus.NO_DATA
        reason = "No historical occurrences exist for this setup criterion."
    elif (
        forward_return_observation_count < min_observations_for_evaluation
        or upward_excursion_observation_count < min_observations_for_evaluation
    ):
        evaluation_status = SetupEvaluationStatus.INSUFFICIENT_DATA
        reason = _build_insufficient_reason(
            forward_return_observation_count=forward_return_observation_count,
            upward_excursion_observation_count=upward_excursion_observation_count,
            min_observations_for_evaluation=min_observations_for_evaluation,
        )
    else:
        evaluation_status = SetupEvaluationStatus.EVALUABLE
        reason = (
            "All required metrics meet the minimum observation threshold "
            f"({min_observations_for_evaluation})."
        )

    evaluation_id = _compute_evaluation_id(
        batch.batch_id,
        min_observations_for_evaluation,
    )

    return SetupEvaluationResult(
        evaluation_id=evaluation_id,
        batch_id=batch.batch_id,
        criterion_key=batch.criterion_key,
        instrument=batch.instrument,
        setup_timeframe=batch.setup_timeframe,
        context_timeframe=batch.context_timeframe,
        evaluation_status=evaluation_status,
        total_occurrence_count=total,
        sufficient_data_count=sufficient,
        insufficient_data_count=insufficient,
        forward_return_observation_count=forward_return_observation_count,
        upward_excursion_observation_count=upward_excursion_observation_count,
        downward_excursion_observation_count=downward_excursion_observation_count,
        min_observations_for_evaluation=min_observations_for_evaluation,
        reason=reason,
    )


def _build_insufficient_reason(
    forward_return_observation_count: int,
    upward_excursion_observation_count: int,
    min_observations_for_evaluation: int,
) -> str:
    """Build a human-readable reason for INSUFFICIENT_DATA status."""
    parts: list[str] = []
    if forward_return_observation_count < min_observations_for_evaluation:
        parts.append(
            f"forward return observations ({forward_return_observation_count}) "
            f"below minimum ({min_observations_for_evaluation})"
        )
    if upward_excursion_observation_count < min_observations_for_evaluation:
        parts.append(
            f"price excursion observations ({upward_excursion_observation_count}) "
            f"below minimum ({min_observations_for_evaluation})"
        )
    return "Insufficient data: " + "; ".join(parts) + "."


__all__ = [
    "SetupEvaluationResult",
    "SetupEvaluationStatus",
    "evaluate_setup",
]
