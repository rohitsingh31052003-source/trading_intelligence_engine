"""
Domain models for the historical evidence aggregation boundary
(Checkpoint 9.9).

Checkpoint 9.9 establishes the research-layer boundary that associates
multiple historical observations with the same setup criterion. It is
the aggregation boundary between the Checkpoint 9.5/9.8 outcome
observations and future statistical evidence analysis / setup quality
layers:

    Phase 6C Corpus (CorpusEvaluationPoint)
            |
    Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
            |
    Checkpoint 9.5/9.8 Outcome Observations (ForwardReturnObservation,
                                      PriceExcursionObservation per occurrence)
            |
    Checkpoint 9.9 Evidence Aggregation Boundary (this layer)
            |
    Future: statistical evidence analysis / setup quality

Checkpoint 9.9 is AGGREGATION BOUNDARY ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT a statistical evidence engine.
* It collects individual historical occurrences (each carrying a
  ForwardReturnObservation paired with a PriceExcursionObservation)
  under a shared setup criterion. It does NOT compute averages, medians,
  win rates, loss rates, expectancy, Sharpe ratios, statistical
  significance, predictive power, or quality scores.
* The EXISTING Sprint 11Y evidence layer, the Checkpoint 9.5/9.8 outcome
  layer, and the Checkpoint 9.1 discovery layer remain authoritative and
  untouched by these models.

DESIGN PRINCIPLE — preserve occurrence-level information:

Each historical occurrence must remain traceable to its observation
time and candidate. The paired excursion values
(``max_upward_excursion``, ``max_downward_excursion``) must remain
associated with their originating occurrence. Observations are NOT
collapsed into aggregate statistics; they are collected immutably.

DESIGN PRINCIPLE — explicit insufficient-data states:

Occurrences with insufficient future data (fewer than ``horizon_candles``
future candles available after T) are preserved in the batch with their
``INSUFFICIENT_DATA`` status explicitly represented. They are never
silently dropped or converted into false determinate observations.

DESIGN PRINCIPLE — deterministic aggregation:

The aggregation operation is deterministic: identical input occurrences
produce an identical batch. Deduplication is by ``occurrence_id``
(first occurrence wins). Ordering is chronological by
``(evaluation_time, occurrence_id)``. Source observations are never
mutated.

DESIGN PRINCIPLE — no leakage:

The aggregation boundary consumes already-evaluated observations
(Checkpoint 9.5/9.8). It never re-reads candles, never re-evaluates
outcomes, never uses future information, and never mutates the
decisions made at time ``T``. The point-in-time boundary is preserved:
discovery uses only information at T, outcomes use future data only
after T, evidence aggregation does not alter or feed information
backward into discovery.

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
from datetime import datetime

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_outcome import (
    ForwardReturnObservation,
    ObservationStatus,
    PriceExcursionObservation,
)


def _compute_batch_id(
    criterion_key: str,
    occurrences: tuple[SetupEvidenceOccurrence, ...],
) -> str:
    """Compute a deterministic batch identity hash."""
    identity = criterion_key + "|" + ",".join(o.occurrence_id for o in occurrences)
    return "evidence-batch-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SetupEvidenceOccurrence:
    """
    ONE occurrence of a setup criterion with its paired neutral
    observations.

    Preserves occurrence-level information:

    * The :class:`HistoricalSetupCandidate` (information known at ``T``),
      retained by reference and never mutated.
    * The :class:`ForwardReturnObservation` for this occurrence (the
      direction-neutral fixed-horizon forward price return), retained by
      reference.
    * The :class:`PriceExcursionObservation` for this occurrence (the
      paired max upward/downward excursions), retained by reference.

    The paired excursion values (``max_upward_excursion``,
    ``max_downward_excursion``) remain associated with their originating
    occurrence via the ``price_excursion`` field. They are NOT split
    into unrelated observations.

    When either observation carries
    :attr:`ObservationStatus.INSUFFICIENT_DATA`, the corresponding
    field is ``None`` — the insufficient-data state is explicitly
    represented, never silently dropped.

    Attributes:

    occurrence_id
        Deterministic identifier for this occurrence (derived from the
        candidate identity and evaluation time).

    candidate
        The :class:`HistoricalSetupCandidate` (information known at ``T``),
        retained by reference and never mutated.

    forward_return
        The :class:`ForwardReturnObservation` for this occurrence, or
        ``None`` when insufficient future data. Retained by reference.

    price_excursion
        The :class:`PriceExcursionObservation` for this occurrence, or
        ``None`` when insufficient future data. Retained by reference.
    """

    occurrence_id: str
    candidate: HistoricalSetupCandidate
    forward_return: ForwardReturnObservation | None
    price_excursion: PriceExcursionObservation | None

    @property
    def evaluation_time(self) -> datetime:
        """The candidate's evaluation timestamp (information known at T)."""
        return self.candidate.evaluation_time

    @property
    def instrument(self) -> str:
        return self.candidate.instrument

    @property
    def setup_timeframe(self) -> str:
        return self.candidate.setup_timeframe

    @property
    def context_timeframe(self) -> str:
        return self.candidate.context_timeframe

    @property
    def has_sufficient_data(self) -> bool:
        """Whether both observations carry AVAILABLE status."""
        return (
            self.forward_return is not None
            and self.forward_return.observation_status is ObservationStatus.AVAILABLE
            and self.price_excursion is not None
            and self.price_excursion.observation_status is ObservationStatus.AVAILABLE
        )

    @property
    def is_insufficient_data(self) -> bool:
        """Whether either observation is missing or INSUFFICIENT_DATA."""
        return not self.has_sufficient_data

    def __post_init__(self) -> None:
        if self.forward_return is not None:
            if self.forward_return.candidate is not self.candidate:
                raise ValueError(
                    "forward_return candidate does not match occurrence candidate."
                )
        if self.price_excursion is not None:
            if self.price_excursion.candidate is not self.candidate:
                raise ValueError(
                    "price_excursion candidate does not match occurrence candidate."
                )


@dataclass(frozen=True, slots=True)
class SetupEvidenceBatch:
    """
    Immutable collection of occurrences for the same setup criterion.

    Each occurrence remains individually traceable to its observation
    time and candidate. The paired excursion values remain associated
    with their originating occurrence. No statistical aggregation is
    performed; this is a collection, not a summary.

    Attributes:

    batch_id
        Deterministic identifier (``"evidence-batch-"`` + SHA-256 prefix).

    criterion_key
        The setup criterion identifier shared by all occurrences in this
        batch (e.g. ``"NIFTY|15m|1D"`` or a richer metadata key).

    instrument
        Canonical instrument name (derived from occurrences).

    setup_timeframe
        Canonical setup timeframe label (derived from occurrences).

    context_timeframe
        Canonical context timeframe label (derived from occurrences).

    occurrences
        Tuple of :class:`SetupEvidenceOccurrence`, ordered
        chronologically by ``(evaluation_time, occurrence_id)``.
    """

    batch_id: str
    criterion_key: str
    instrument: str
    setup_timeframe: str
    context_timeframe: str
    occurrences: tuple[SetupEvidenceOccurrence, ...]

    @property
    def total_occurrences(self) -> int:
        """Total number of occurrences in the batch."""
        return len(self.occurrences)

    @property
    def sufficient_data_count(self) -> int:
        """Number of occurrences with both observations AVAILABLE."""
        return sum(1 for o in self.occurrences if o.has_sufficient_data)

    @property
    def insufficient_data_count(self) -> int:
        """Number of occurrences with insufficient data."""
        return sum(1 for o in self.occurrences if o.is_insufficient_data)

    @property
    def is_empty(self) -> bool:
        """Whether the batch contains no occurrences."""
        return not self.occurrences

    def __post_init__(self) -> None:
        if self.total_occurrences < 0:
            raise ValueError("total_occurrences must be non-negative.")
        if (
            self.sufficient_data_count + self.insufficient_data_count
            != self.total_occurrences
        ):
            raise ValueError(
                "sufficient_data_count + insufficient_data_count must equal "
                "total_occurrences."
            )


def aggregate_evidence(
    occurrences: tuple[SetupEvidenceOccurrence, ...],
    *,
    criterion_key: str = "",
) -> SetupEvidenceBatch:
    """
    Deterministically aggregate occurrences into a batch.

    Deduplication is by ``occurrence_id`` (first occurrence wins).
    Ordering is chronological by ``(evaluation_time, occurrence_id)``.
    Source observations are never mutated. No statistical aggregation
    is performed.

    Args:
        occurrences: The occurrences to aggregate.
        criterion_key: The setup criterion identifier. When empty and
            occurrences are present, derived from the first occurrence's
            ``(instrument, setup_timeframe, context_timeframe)``.

    Returns:
        An immutable :class:`SetupEvidenceBatch`.
    """
    seen: dict[str, SetupEvidenceOccurrence] = {}
    for occ in occurrences:
        if occ.occurrence_id not in seen:
            seen[occ.occurrence_id] = occ

    sorted_occs = sorted(
        seen.values(), key=lambda o: (o.evaluation_time, o.occurrence_id)
    )

    if criterion_key == "" and sorted_occs:
        first = sorted_occs[0]
        criterion_key = (
            f"{first.instrument}|{first.setup_timeframe}|{first.context_timeframe}"
        )

    instrument = sorted_occs[0].instrument if sorted_occs else ""
    setup_tf = sorted_occs[0].setup_timeframe if sorted_occs else ""
    ctx_tf = sorted_occs[0].context_timeframe if sorted_occs else ""

    batch_id = _compute_batch_id(criterion_key, tuple(sorted_occs))

    return SetupEvidenceBatch(
        batch_id=batch_id,
        criterion_key=criterion_key,
        instrument=instrument,
        setup_timeframe=setup_tf,
        context_timeframe=ctx_tf,
        occurrences=tuple(sorted_occs),
    )


__all__ = [
    "SetupEvidenceBatch",
    "SetupEvidenceOccurrence",
    "aggregate_evidence",
]
