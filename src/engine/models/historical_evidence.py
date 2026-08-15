"""
Domain models for the historical evidence / validation layer
(Sprint 11Y).

Sprint 11X answers: "How did the opportunities historically produced
by the engine perform, in aggregate?".
Sprint 11Y answers: "What historical evidence is strong enough, weak
enough, or insufficient to be useful to downstream intelligence?".

These models describe a DESCRIPTIVE EVIDENCE assessment computed on top
of already-aggregated Sprint 11X performance statistics. The evidence
layer consumes already-computed Sprint 11W :class:`HistoricalOutcome`
objects, reuses the Sprint 11X statistics computation (it does NOT
recompute trading / outcome logic), and classifies the STRENGTH of the
available historical evidence per cohort.

CRITICAL DISTINCTION — "observed historical result" vs "evidence
strength":

The :class:`HistoricalPerformanceStatistics` (reused verbatim from
Sprint 11X) describe the OBSERVED historical result (win rate, realized
R, profit factor, MFE / MAE, ...). The :class:`EvidenceStrength`
classification describes whether that observed result is supported by
ENOUGH historical observation to be considered reliable / meaningful
evidence. These are NOT the same thing:

* A cohort with one historical trade at +2R has an OBSERVED win rate of
  100% but INSUFFICIENT evidence (the sample is too small to be
  meaningful).
* A cohort with 100 historical trades at a modest but consistent result
  may have STRONG evidence even though its observed win rate is
  unremarkable.

Evidence strength is driven primarily by SAMPLE SIZE and RESOLVED
observation counts (hard gates), never by the magnitude of the
observed win rate alone. A small sample is never promoted to STRONG
merely because its win rate is high.

DESIGN PRINCIPLE — no statistical claims:

The evidence layer does NOT perform statistical hypothesis tests. It
does NOT use terms such as "statistically significant". It performs
deterministic, threshold-based classification of observation counts and
descriptive metrics. The thresholds are explicit, configurable and
documented on :class:`engine.config.historical_evidence_config.EvidenceConfig`.

DESIGN PRINCIPLE — no leakage:

The evidence layer consumes ALREADY-COMPUTED historical outcomes
(evaluated forward-only by Sprint 11W). It never re-reads candles,
never re-evaluates outcomes, never uses future information, and never
mutates the decisions made at time ``T``.

DESIGN PRINCIPLE — honest outcome handling:

Sprint 11W semantics are preserved unchanged:

* ``BOTH_TOUCHED`` is ambiguous — never a win or a loss; its
  ``realized_r`` is ``None`` so it never contributes to R aggregates.
* ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` carry no fabricated values.
* ``EXPIRED`` is a mark-to-close result.
* ``TARGET_HIT`` / ``STOP_HIT`` are resolved favorable / adverse
  outcomes.

These models are data carriers; no business logic lives here.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional metrics reuse ``None`` so "unavailable" is never silently a
  real value (delegated to the reused statistics).
* Cohort specs are tuples of :class:`BreakdownDimension` (reused from
  Sprint 11X) so single-dimension and composite cohorts share one type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)


class EvidenceStrength(Enum):
    """
    The descriptive strength of the available historical evidence for a
    cohort.

    This classification describes EVIDENCE QUALITY (how much reliable
    historical observation backs the cohort's metrics), NOT whether the
    cohort was profitable. It is driven primarily by sample size and
    resolved observation counts.

    INSUFFICIENT
        The cohort does not have enough historical observation to
        produce meaningful evidence. The observed metrics (if any) are
        reported but must NOT be treated as reliable evidence by
        downstream intelligence. Typically the total sample is below
        the configured minimum.

    WEAK
        Some historical observation exists, but not enough resolved /
        valid-R observation to support moderate or strong evidence.
        Observed metrics are directional at best and should be treated
        cautiously.

    MODERATE
        A meaningful amount of historical observation supports the
        cohort's metrics. The evidence is usable but not yet strong.

    STRONG
        A substantial amount of historical observation (sample size,
        resolved outcomes and valid-R observations all above the
        strong thresholds) supports the cohort's metrics. Even STRONG
        evidence is DESCRIPTIVE and does NOT guarantee future
        performance.

    The thresholds separating these levels are configurable
    (:class:`engine.config.historical_evidence_config.EvidenceConfig`)
    and documented. No statistical hypothesis test is performed.
    """

    INSUFFICIENT = "INSUFFICIENT"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (strongest first)."""

        return {
            EvidenceStrength.STRONG: 0,
            EvidenceStrength.MODERATE: 1,
            EvidenceStrength.WEAK: 2,
            EvidenceStrength.INSUFFICIENT: 3,
        }[self]

    @property
    def is_sufficient(self) -> bool:
        """Whether the evidence is at least usable (not INSUFFICIENT)."""

        return self != EvidenceStrength.INSUFFICIENT


@dataclass(frozen=True)
class CohortSpec:
    """
    A specification of a cohort dimension: an ordered tuple of one or
    more :class:`BreakdownDimension` values whose combined values form
    the cohort key.

    A single-dimension cohort spec contains one element (e.g.
    ``(BreakdownDimension.SETUP_TYPE,)``). A composite cohort spec
    contains two elements (e.g.
    ``(BreakdownDimension.SETUP_TYPE, BreakdownDimension.DIRECTION)``).
    Composite cohort specs are intentionally limited to a controlled,
    documented set (see
    :data:`engine.intelligence.historical_evidence.SUPPORTED_COHORT_SPECS`)
    to avoid an uncontrolled combinatorial explosion.

    Attributes:

    dimensions
        The ordered tuple of :class:`BreakdownDimension` forming the
        cohort key.
    """

    dimensions: tuple[BreakdownDimension, ...]

    def __post_init__(self) -> None:
        if not self.dimensions:
            raise ValueError("CohortSpec must contain at least one dimension.")
        if len(self.dimensions) > 2:
            raise ValueError(
                "CohortSpec supports at most two dimensions "
                "(single + one composite) to avoid combinatorial explosion.",
            )

    @property
    def is_composite(self) -> bool:
        """Whether this is a multi-dimension (composite) cohort spec."""

        return len(self.dimensions) > 1

    @property
    def label(self) -> str:
        """A deterministic human-readable label for the cohort spec."""

        return " + ".join(d.name for d in self.dimensions)


@dataclass(frozen=True)
class HistoricalEvidenceCohort:
    """
    One evaluated evidence cohort: a cohort key paired with the reused
    :class:`HistoricalPerformanceStatistics` for the outcomes in that
    cohort, plus the :class:`EvidenceStrength` classification and a
    descriptive rationale explaining why the cohort received its
    classification.

    Attributes:

    spec
        The :class:`CohortSpec` this cohort belongs to.

    key
        The cohort key (the combined dimension values, joined by
        ``"|"`` for composite specs). The empty string ``""``
        represents "unavailable" metadata (never invented).

    statistics
        The reused :class:`HistoricalPerformanceStatistics` for
        outcomes in this cohort (the OBSERVED historical result).

    strength
        The :class:`EvidenceStrength` classification (the EVIDENCE
        QUALITY).

    sample_count
        Total number of outcomes in the cohort (== ``statistics.total``;
        repeated for convenient downstream consumption without
        navigating the statistics).

    resolved_count
        Number of resolved outcomes in the cohort
        (== ``statistics.resolved``; repeated for convenience).

    valid_r_count
        Number of valid realized-R observations in the cohort
        (== ``statistics.valid_r_count``; repeated for convenience).

    rationale
        Human-readable, descriptive explanation of why the cohort
        received its evidence classification. Descriptive only.
    """

    spec: CohortSpec
    key: str
    statistics: HistoricalPerformanceStatistics
    strength: EvidenceStrength
    sample_count: int
    resolved_count: int
    valid_r_count: int
    rationale: str

    @property
    def is_sufficient(self) -> bool:
        """Whether the cohort's evidence is at least usable."""

        return self.strength.is_sufficient


@dataclass(frozen=True)
class HistoricalEvidenceBreakdown:
    """
    The evaluated evidence cohorts for one :class:`CohortSpec`.

    Cohorts are ordered DETERMINISTICALLY by the engine (canonical
    order first per dimension, then lexicographic for any remaining
    keys; for composite specs the first dimension dominates, then the
    second), never by unordered iteration.

    Attributes:

    spec
        The :class:`CohortSpec`.

    cohorts
        Tuple of :class:`HistoricalEvidenceCohort`, deterministically
        ordered.
    """

    spec: CohortSpec
    cohorts: tuple[HistoricalEvidenceCohort, ...] = field(
        default_factory=tuple,
    )

    @property
    def is_empty(self) -> bool:
        return len(self.cohorts) == 0


@dataclass(frozen=True)
class HistoricalEvidenceSummary:
    """
    The overall evidence summary across all evaluated outcomes.

    The overall cohort treats every outcome as one cohort (no
    dimension). Its statistics are the OBSERVED overall result (reused
    from Sprint 11X); its strength is the EVIDENCE QUALITY of the whole
    collection.

    Attributes:

    statistics
        The reused overall :class:`HistoricalPerformanceStatistics`.

    strength
        The overall :class:`EvidenceStrength`.

    sample_count
        Total outcomes evaluated (== ``statistics.total``).

    resolved_count
        Total resolved outcomes (== ``statistics.resolved``).

    valid_r_count
        Total valid realized-R observations
        (== ``statistics.valid_r_count``).

    rationale
        Descriptive explanation of the overall evidence classification.
    """

    statistics: HistoricalPerformanceStatistics
    strength: EvidenceStrength
    sample_count: int
    resolved_count: int
    valid_r_count: int
    rationale: str

    @property
    def is_sufficient(self) -> bool:
        return self.strength.is_sufficient


@dataclass(frozen=True)
class HistoricalEvidenceReport:
    """
    The aggregate historical evidence / validation result.

    The result is DESCRIPTIVE. It classifies the strength of historical
    evidence per cohort. It is NOT a prediction, NOT a probability of
    success, NOT a profitability guarantee, NOT a trading recommendation,
    and NOT a statistical hypothesis test result.

    Attributes:

    evidence_id
        Deterministic identifier (``"evidence-"`` + sha256[:16] of the
        canonical evidence identity).

    summary
        The :class:`HistoricalEvidenceSummary` across all outcomes.

    breakdowns
        Tuple of :class:`HistoricalEvidenceBreakdown`, one per
        supported :class:`CohortSpec`, deterministically ordered.

    cohort_count
        Total number of cohorts evaluated across all breakdowns.

    sufficient_cohort_count
        Number of cohorts whose evidence is at least usable (not
        INSUFFICIENT).

    insufficient_cohort_count
        Number of cohorts whose evidence is INSUFFICIENT.

    label
        Optional descriptive label identifying the evidence run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).

    config_snapshot
        A sorted tuple of ``(threshold_name, str(value))`` pairs
        capturing the evidence configuration thresholds used, for
        auditability / reproducibility.

    rationale
        Human-readable, descriptive summary. Descriptive only.
    """

    evidence_id: str
    summary: HistoricalEvidenceSummary
    breakdowns: tuple[HistoricalEvidenceBreakdown, ...] = field(
        default_factory=tuple,
    )
    cohort_count: int = 0
    sufficient_cohort_count: int = 0
    insufficient_cohort_count: int = 0
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    config_snapshot: tuple[tuple[str, str], ...] = ()
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether the evidence report aggregated no outcomes."""

        return self.summary.sample_count == 0


__all__ = [
    "CohortSpec",
    "EvidenceStrength",
    "HistoricalEvidenceBreakdown",
    "HistoricalEvidenceCohort",
    "HistoricalEvidenceReport",
    "HistoricalEvidenceSummary",
]
