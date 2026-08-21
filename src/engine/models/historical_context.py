"""
Domain models for historical + current intelligence (Product Phase 6E).

Product Phase 6E connects the completed Product Phase 6D historical
setup research output to the EXISTING current-market analysis. It
answers, for a current evaluation point ``T``:

    "Given the current instrument, timeframe, setup / structure
    characteristics and evaluation time T, what historical evidence is
    available for comparable setups?"

Phase 6E is an EVIDENCE INTEGRATION layer, not a trading strategy:

* It is NOT a decision engine, NOT a scoring layer, NOT a prediction
  engine, NOT a geometry layer, NOT a paper-trading layer.
* The existing decision architecture (Sprints 11S/11T, the dashboard
  actionability mirror, the Product Phase 4 trade plan and the Product
  Phase 5 paper-trading operations) remains AUTHORITATIVE. Historical
  evidence NEVER creates, modifies, upgrades, downgrades or overrides
  the existing decision classification, actionability, direction,
  geometry, trade plan or paper-trading eligibility.
* The output is DESCRIPTIVE historical context. It is NOT a prediction,
  NOT a probability of future success, NOT a profitability guarantee and
  NOT a trading recommendation.

DESIGN PRINCIPLE — strict point-in-time separation:

* The matching criteria carry ONLY information legitimately available at
  the current evaluation time ``T`` (instrument / timeframes / reused
  Sprint 11Q-11U setup + structure labels). No current future
  information is used to decide whether a historical occurrence is
  comparable.
* A historical occurrence is eligible only when it occurred strictly
  BEFORE ``T`` AND its reused Sprint 11W outcome was already fully
  resolved at ``T`` (``outcome_timestamp <= T`` or ambiguous with no
  outcome timestamp). A historical outcome that was still open at ``T``
  is EXCLUDED — the system at ``T`` could not have known it.

DESIGN PRINCIPLE — no fabricated values:

Evidence that is unavailable (no persisted research, no comparable
occurrence, no already-resolved outcome) is reported via an explicit
availability status with ``strength = None`` and ``statistics = None`` —
never padded with empty/default statistics that could be misread as
evidence. Statistics reuse the Sprint 11X computation and the strength
reuses the Sprint 11Y vocabulary via the Phase 6D sample gates; nothing
is recomputed with new semantics and nothing is invented.

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
from enum import Enum

from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_performance import HistoricalPerformanceStatistics


#: Model contract version carried onto results (audit metadata).
HISTORICAL_CONTEXT_VERSION = "historical-context-v1"

#: Fixed, non-configurable disclaimer (NOT a model field default that a
#: caller could weaken; surfaced on every context + report).
HISTORICAL_CONTEXT_LIMITATIONS: tuple[str, ...] = (
    "Historical evidence is descriptive and observational. It is NOT a "
    "prediction, NOT a probability of success, NOT a profitability "
    "guarantee and NOT a trading recommendation.",
    "Historical evidence NEVER modifies the authoritative existing "
    "decision / actionability / geometry / trade plan / paper-trading "
    "eligibility.",
    "Historical evidence is matched only on information available at "
    "the evaluation time T; no future information is used.",
)


# ============================================================
# EVIDENCE REQUEST (matching criteria at T)
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceRequest:
    """
    The matching criteria for ONE historical evidence lookup at a
    current evaluation time ``T``.

    Every field is information legitimately available at ``T``: the
    identity (instrument / timeframes / evaluation timestamp) and the
    reused Sprint 11Q-11U setup / structure labels produced by the
    existing current-market analysis. NO field carries future
    information.

    Attributes:

    instrument / setup_timeframe / context_timeframe
        Canonical identity of the current assessment.

    evaluation_time
        The current evaluation point ``T`` (timezone-aware). Only
        historical occurrences strictly before ``T`` whose outcome was
        already resolved at ``T`` are eligible.

    setup_type / direction / trend_state / range_state / mtf_alignment
        Optional reused Sprint 11R / 11P / 11U labels. ``""`` disables
        that comparison dimension. These select genuinely comparable
        historical situations; they never change detection or decisions.
    """

    instrument: str
    setup_timeframe: str = "15m"
    context_timeframe: str = "1D"
    evaluation_time: datetime | None = None
    setup_type: str = ""
    direction: str = ""
    trend_state: str = ""
    range_state: str = ""
    mtf_alignment: str = ""

    def __post_init__(self) -> None:
        instrument = str(self.instrument).strip().upper()
        if not instrument:
            raise ValueError("instrument must be non-empty.")
        object.__setattr__(self, "instrument", instrument)

        if self.evaluation_time is not None and self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")

        for name in (
            "setup_timeframe",
            "context_timeframe",
            "setup_type",
            "direction",
            "trend_state",
            "range_state",
            "mtf_alignment",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string.")
            object.__setattr__(self, name, value.strip().upper())

    @property
    def match_dimensions(self) -> tuple[tuple[str, str], ...]:
        """The active (non-empty) comparison dimensions, in a fixed order."""

        return tuple(
            (name, getattr(self, name))
            for name in (
                "setup_type",
                "direction",
                "trend_state",
                "range_state",
                "mtf_alignment",
            )
            if getattr(self, name)
        )

    @property
    def match_key(self) -> str:
        """
        A deterministic human-readable comparison key.

        ``"<INSTRUMENT>|<SETUP_TF>|<DIM=value>|..."`` — empty when no
        comparison dimension is active beyond identity.
        """

        parts = [self.instrument, self.setup_timeframe]
        parts.extend(f"{name}={value}" for name, value in self.match_dimensions)
        return "|".join(parts)


# ============================================================
# AVAILABILITY STATUS (NOT an evidence-strength enum)
# ============================================================


class HistoricalContextStatus(Enum):
    """
    Availability of historical evidence for a current assessment.

    This is an AVAILABILITY status, deliberately DISTINCT from the
    reused Sprint 11Y
    :class:`~engine.models.historical_evidence.EvidenceStrength`
    vocabulary (which Phase 6E reuses unchanged for the strength of
    whatever evidence IS available).

    AVAILABLE
        At least one comparable historical occurrence with an outcome
        already resolved at ``T`` was found. The context carries the
        reused statistics + the reused evidence strength.

    NO_MATCH
        Persisted Phase 6D research exists, but no comparable
        historical occurrence (with an outcome already resolved at
        ``T``) was found. This is an OBSERVED result, not a data
        problem; nothing is fabricated.

    RESEARCH_UNAVAILABLE
        No persisted Phase 6D research exists for the requested
        instrument / timeframe pair (missing research corpus or store).
        Never silently treated as "no comparable setups exist".
    """

    AVAILABLE = "AVAILABLE"
    NO_MATCH = "NO_MATCH"
    RESEARCH_UNAVAILABLE = "RESEARCH_UNAVAILABLE"

    @property
    def is_available(self) -> bool:
        return self is HistoricalContextStatus.AVAILABLE


# ============================================================
# HISTORICAL EVIDENCE CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceContext:
    """
    The typed result of ONE Phase 6E historical evidence lookup for a
    current assessment at ``T``.

    DESCRIPTIVE ONLY. The context NEVER carries a decision, a score, a
    recommendation, an entry/stop/target or any trading directive. It
    carries only what the Phase 6D research actually supports: counts,
    the reused Sprint 11X statistics (by value, computed over the
    matched reused Sprint 11W outcomes) and the reused Sprint 11Y
    evidence strength.

    Attributes:

    context_id
        Deterministic identity (``"hectx-" + sha256[:16]``) of the
        canonical (request + research ids + matched occurrence times +
        status) tuple. Same inputs -> same id; no wall-clock.

    request
        The matching criteria used (retained by reference for audit).

    status
        The explicit availability status.

    match_key
        The deterministic comparison key actually used.

    comparable_occurrences
        Number of comparable historical occurrences found (occurred
        strictly before ``T``).

    completed_outcomes / ambiguous_count / unresolved_count
        Outcome status counts over the matched occurrences. Only
        occurrences whose outcome was already resolved at ``T``
        contribute to ``statistics`` / ``strength``.

    statistics
        The reused Sprint 11X
        :class:`HistoricalPerformanceStatistics` over the matched
        already-resolved outcomes, or ``None`` when unavailable. NEVER
        fabricated.

    strength
        The reused Sprint 11Y
        :class:`~engine.models.historical_evidence.EvidenceStrength`, or
        ``None`` when unavailable. NEVER a second strength vocabulary.

    research_ids
        The Phase 6D research result ids this context was derived from
        (provenance / reference to the research corpus).

    reason / limitations / label / metadata
        Descriptive explanation + the fixed limitations + run identity.
    """

    context_id: str
    request: HistoricalEvidenceRequest
    status: HistoricalContextStatus
    match_key: str = ""
    comparable_occurrences: int = 0
    completed_outcomes: int = 0
    ambiguous_count: int = 0
    unresolved_count: int = 0
    statistics: HistoricalPerformanceStatistics | None = None
    strength: EvidenceStrength | None = None
    research_ids: tuple[str, ...] = ()
    reason: str = ""
    limitations: tuple[str, ...] = HISTORICAL_CONTEXT_LIMITATIONS
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not str(self.context_id).strip():
            raise ValueError("context_id must be non-empty.")
        for name in (
            "comparable_occurrences",
            "completed_outcomes",
            "ambiguous_count",
            "unresolved_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if self.status is HistoricalContextStatus.AVAILABLE:
            if self.strength is None or self.statistics is None:
                raise ValueError(
                    "an AVAILABLE context must carry the reused statistics "
                    "and evidence strength.",
                )
        else:
            if self.strength is not None or self.statistics is not None:
                raise ValueError(
                    "a non-AVAILABLE context must NOT carry statistics or "
                    "an evidence strength (unavailable is explicit, never "
                    "fabricated).",
                )
        if self.comparable_occurrences == 0 and self.status is HistoricalContextStatus.AVAILABLE:
            raise ValueError(
                "an AVAILABLE context must carry at least one comparable "
                "occurrence.",
            )
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    @property
    def is_available(self) -> bool:
        return self.status.is_available

    @property
    def win_rate(self) -> float | None:
        return self.statistics.win_rate if self.statistics is not None else None

    @property
    def average_realized_r(self) -> float | None:
        return (
            self.statistics.average_realized_r
            if self.statistics is not None
            else None
        )

    @property
    def median_realized_r(self) -> float | None:
        return (
            self.statistics.median_realized_r
            if self.statistics is not None
            else None
        )

    @property
    def profit_factor(self) -> float | None:
        return (
            self.statistics.profit_factor
            if self.statistics is not None
            else None
        )


__all__ = [
    "HISTORICAL_CONTEXT_LIMITATIONS",
    "HISTORICAL_CONTEXT_VERSION",
    "HistoricalContextStatus",
    "HistoricalEvidenceContext",
    "HistoricalEvidenceRequest",
]
