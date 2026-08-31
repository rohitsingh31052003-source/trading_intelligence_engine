"""
Domain models for historical setup discovery (Checkpoint 9.1).

These models define the research-layer boundary between the historical
research corpus (Product Phase 6C) and historical setup detection /
outcome evaluation (Product Phase 6D). They describe candidate
historical setup observations derived from corpus evaluation points.

Checkpoint 9.1 is RESEARCH BOUNDARY ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT an evidence / outcome engine.
* It consumes the Product Phase 6C corpus output
  (:class:`~engine.models.research_corpus.CorpusEvaluationPoint`) and
  produces structured candidate observations. It does NOT call the
  decision engine, does NOT generate trade candidates, does NOT create
  paper trades and does NOT compute historical outcomes or evidence.
* The EXISTING live path and the Product Phase 6C corpus remain
  authoritative and are untouched by these models.

DESIGN PRINCIPLE — no fabricated data:

Missing / skipped corpus points are reported via
:class:`HistoricalSetupCandidate` with ``is_candidate=False`` — never
silently omitted or converted into false candidates.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.models.research_corpus import CorpusEvaluationPoint


@dataclass(frozen=True, slots=True)
class HistoricalSetupCandidate:
    """
    ONE candidate historical setup observation derived from a corpus
    evaluation point.

    Attributes:
        instrument
            Canonical instrument name.
        evaluation_time
            The preserved evaluation timestamp.
        setup_timeframe
            Canonical setup timeframe label.
        context_timeframe
            Canonical context timeframe label.
        history_count
            Number of usable setup-timeframe candles at the boundary
            (0 for skipped points).
        status
            The corpus point status name (``VALID`` / ``INSUFFICIENT_HISTORY``
            / ``MISSING_DATA`` / ``DATA_GAP`` / ``INVALID``).
        has_structure
            Whether the corpus point carries computed market structure
            (a reused ``MarketContext`` is available for the setup slice).
        is_candidate
            Whether this point is flagged as a candidate for further
            setup detection (deterministic, derived by the discovery
            engine from the corpus state).
        reason
            Human-readable explanation of the candidate flag.
    """

    instrument: str
    evaluation_time: datetime
    setup_timeframe: str
    context_timeframe: str
    history_count: int
    status: str
    has_structure: bool
    is_candidate: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")
        for name in ("history_count",):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")


@dataclass(frozen=True, slots=True)
class SetupDiscoveryResult:
    """
    The result of running historical setup discovery over a sequence of
    corpus evaluation points for one instrument.

    Attributes:
        discovery_id
            Deterministic identifier for this discovery run
            (``"discovery-"`` + SHA-256 prefix).
        instrument
            Canonical instrument name.
        timeframe
            Canonical setup timeframe label.
        candidates
            Ordered candidate observations (one per input corpus point).
        total_evaluated
            Number of corpus points examined.
        candidate_count
            Number of points flagged as candidates.
        label
            Descriptive run identity.
        metadata
            Optional caller metadata (sorted tuple of pairs).
    """

    discovery_id: str
    instrument: str
    timeframe: str
    candidates: tuple[HistoricalSetupCandidate, ...]
    total_evaluated: int
    candidate_count: int
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.total_evaluated < 0:
            raise ValueError("total_evaluated must be non-negative.")
        if self.candidate_count < 0:
            raise ValueError("candidate_count must be non-negative.")
        if self.candidate_count > self.total_evaluated:
            raise ValueError("candidate_count cannot exceed total_evaluated.")
        if len(self.candidates) > self.total_evaluated:
            raise ValueError("candidates cannot exceed total_evaluated.")

    @property
    def is_empty(self) -> bool:
        """True when no corpus points were examined."""
        return not self.candidates


__all__ = [
    "HistoricalSetupCandidate",
    "SetupDiscoveryResult",
]
