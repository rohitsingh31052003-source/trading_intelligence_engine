"""
Domain models for the evidence-conditioned strategy intelligence
layer (Sprint 11Z).

Sprint 11X answers: "How did the opportunities historically produced
by the engine perform, in aggregate?".
Sprint 11Y answers: "What historical evidence is strong enough, weak
enough, or insufficient to be useful to downstream intelligence?".
Sprint 11Z answers: "What does the historical evidence actually tell
us about this type of opportunity?" — a conservative, descriptive
STRATEGY-level interpretation built ON TOP of the already-computed
Sprint 11X performance statistics and Sprint 11Y evidence-strength
classifications.

CRITICAL ARCHITECTURE:

* Sprint 11X = OBSERVED historical performance (win rate, realized R,
  profit factor, MFE / MAE, ...).
* Sprint 11Y = EVIDENCE STRENGTH / adequacy of that evidence
  (sample-size driven, hard-gated; a small sample is never strong
  regardless of its observed win rate).
* Sprint 11Z = INTERPRETATION and strategy-level assessment derived
  from those existing outputs. It does NOT recompute performance
  statistics, does NOT re-classify evidence strength, does NOT
  re-evaluate outcomes, does NOT re-read candles, and does NOT use
  future information.

The three concerns are kept STRICTLY SEPARATE and are surfaced
separately in the models and in the report:

* The OBSERVED historical result (reused
  :class:`HistoricalPerformanceStatistics`).
* The EVIDENCE STRENGTH (reused :class:`EvidenceStrength`).
* The STRATEGY INTERPRETATION (:class:`StrategyAssessmentStatus` +
  ``interpretation`` / ``limitations`` text).

DESIGN PRINCIPLE — no predictive claims, no statistical claims:

A :class:`StrategyEvidenceAssessment` is DESCRIPTIVE. It is NOT a
trading signal, NOT a price prediction, NOT a probability of success,
NOT a profitability guarantee, NOT a buy / sell recommendation, and
NOT a statistical-significance claim. The :class:`StrategyAssessmentStatus`
is a deterministic mapping from the reused
:class:`EvidenceStrength`; a tiny cohort with an impressive observed
win rate is NEVER promoted to strong historical support, because the
underlying Sprint 11Y strength hard-gates on sample size.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY, never silently
converted to zero:

* No matching cohort (lookup) -> :attr:`CohortMatchStatus.NO_MATCH`
  with ``matched_cohort = None`` and ``assessment = None``.
* Insufficient sample -> the reused
  :attr:`EvidenceStrength.INSUFFICIENT` propagates to
  :attr:`StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE`.
* Unavailable metric -> ``None`` (delegated to the reused statistics;
  never fabricated).
* ``BOTH_TOUCHED`` / ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` outcomes
  are preserved exactly by the reused statistics (they never
  contribute to win / loss rates or R aggregates); Sprint 11Z inherits
  that honesty unchanged.

DESIGN PRINCIPLE — no leakage:

The layer consumes ALREADY-COMPUTED Sprint 11X analytics and Sprint
11Y evidence. It never inspects future market candles, never
re-evaluates outcomes using future data, and never modifies the
historical replay semantics established in Sprint 11V / 11W.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional metrics reuse ``None`` so "unavailable" is never silently a
  real value.
* The reused :class:`HistoricalPerformanceStatistics`,
  :class:`EvidenceStrength` and :class:`CohortSpec` are referenced
  (never mutated, never recomputed).
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceCohort,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)


class StrategyAssessmentStatus(Enum):
    """
    The conservative, descriptive strategy-level interpretation of a
    cohort's historical evidence.

    This is a DETERMINISTIC mapping from the reused
    :class:`EvidenceStrength` (Sprint 11Y). It is NOT derived from the
    observed win rate alone and is NOT a statistical-significance
    claim. A cohort with a small sample is never promoted to a
    stronger status merely because its observed result is impressive
    — the underlying evidence strength hard-gates on sample size.

    INSUFFICIENT_EVIDENCE
        The cohort does not have enough historical observation to
        support any strategy-level conclusion. The observed metrics
        (if any) are reported but must NOT be treated as reliable
        evidence. Maps from :attr:`EvidenceStrength.INSUFFICIENT`.

    LIMITED_EVIDENCE
        Some historical observation exists, but not enough resolved /
        valid-R observation to support a confident interpretation.
        The evidence is directional at best and should be treated
        cautiously. Maps from :attr:`EvidenceStrength.WEAK`.

    SUPPORTIVE_EVIDENCE
        A meaningful amount of historical observation supports the
        cohort's metrics. The evidence is usable for a conservative
        strategy interpretation, but not yet strong. Maps from
        :attr:`EvidenceStrength.MODERATE`.

    STRONGER_HISTORICAL_SUPPORT
        A substantial amount of historical observation supports the
        cohort's metrics. Even the strongest historical support is
        DESCRIPTIVE and does NOT guarantee future performance or
        imply live-trading readiness. Maps from
        :attr:`EvidenceStrength.STRONG`.

    The mapping is intentionally conservative and one-to-one with the
    reused evidence strength; no statistical hypothesis test is
    performed.
    """

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"
    SUPPORTIVE_EVIDENCE = "SUPPORTIVE_EVIDENCE"
    STRONGER_HISTORICAL_SUPPORT = "STRONGER_HISTORICAL_SUPPORT"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (strongest interpretation first)."""

        return {
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT: 0,
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE: 1,
            StrategyAssessmentStatus.LIMITED_EVIDENCE: 2,
            StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE: 3,
        }[self]

    @property
    def is_actionable(self) -> bool:
        """Whether the interpretation is at least usable (not INSUFFICIENT)."""

        return self != StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE


#: The deterministic one-to-one mapping from the reused Sprint 11Y
#: :class:`EvidenceStrength` to the Sprint 11Z
#: :class:`StrategyAssessmentStatus`. This is the core reuse contract:
#: Sprint 11Z does NOT re-classify evidence — it interprets the
#: already-classified strength.
EVIDENCE_TO_STRATEGY: dict[EvidenceStrength, StrategyAssessmentStatus] = {
    EvidenceStrength.INSUFFICIENT: StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE,
    EvidenceStrength.WEAK: StrategyAssessmentStatus.LIMITED_EVIDENCE,
    EvidenceStrength.MODERATE: StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE,
    EvidenceStrength.STRONG: StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
}


class CohortMatchStatus(Enum):
    """
    The outcome of a current-opportunity evidence lookup.

    MATCHED
        A historical cohort matching the provided opportunity
        characteristics was found in the Sprint 11Y evidence report,
        and a :class:`StrategyEvidenceAssessment` was produced for it.

    NO_MATCH
        No historical cohort matching the provided opportunity
        characteristics exists. The lookup is explicit and honest:
        ``matched_cohort`` and ``assessment`` are ``None``. No
        evidence is fabricated.
    """

    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class OpportunityProfile:
    """
    The characteristics of a CURRENT opportunity used to look up the
    matching historical cohort / evidence.

    Each field corresponds to a supported Sprint 11X / 11Y breakdown
    dimension. Fields are optional; an unavailable / unspecified
    characteristic is represented as the empty string ``""`` (for
    string fields) or ``0`` (for ``rank``), matching the Sprint 11X /
    11Y "unavailable" sentinel convention. The lookup uses ONLY the
    non-empty / non-zero fields to find the most specific matching
    cohort; no metadata is invented.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``), or ``""`` when
        not specified.

    direction
        Directional intent (``"LONG"`` / ``"SHORT"`` / ``"NONE"``),
        or ``""`` when not specified.

    setup_type
        Sprint 11R setup-type name, or ``""`` when not specified.

    mtf_alignment
        Sprint 11U MTF-alignment name, or ``""`` when not specified.

    decision
        Sprint 11S decision-classification name, or ``""`` when not
        specified.

    opportunity_status
        Sprint 11T opportunity-status name, or ``""`` when not
        specified.

    rank
        1-based market-level rank among eligible opportunities, or
        ``0`` when not specified / ineligible.
    """

    instrument: str = ""
    direction: str = ""
    setup_type: str = ""
    mtf_alignment: str = ""
    decision: str = ""
    opportunity_status: str = ""
    rank: int = 0

    def available_dimensions(self) -> tuple[tuple[str, str], ...]:
        """
        The ``(dimension_name, value)`` pairs the lookup can use,
        deterministically ordered (the canonical
        :class:`OpportunityProfile` field order). Only non-empty /
        non-zero fields are returned; unavailable fields are omitted
        so the lookup never matches on invented metadata.
        """

        pairs: list[tuple[str, str]] = []
        if self.instrument:
            pairs.append(("INSTRUMENT", self.instrument))
        if self.direction:
            pairs.append(("DIRECTION", self.direction))
        if self.setup_type:
            pairs.append(("SETUP_TYPE", self.setup_type))
        if self.mtf_alignment:
            pairs.append(("MTF_ALIGNMENT", self.mtf_alignment))
        if self.decision:
            pairs.append(("DECISION", self.decision))
        if self.opportunity_status:
            pairs.append(("OPPORTUNITY_STATUS", self.opportunity_status))
        if self.rank:
            pairs.append(("OPPORTUNITY_RANK", str(self.rank)))
        return tuple(pairs)

    @property
    def is_empty(self) -> bool:
        """Whether the profile carries no usable characteristic."""

        return not self.available_dimensions()


@dataclass(frozen=True)
class StrategyEvidenceAssessment:
    """
    An evidence-backed strategy assessment for ONE historical cohort.

    The assessment is DESCRIPTIVE. It combines, WITHOUT recomputing:

    * the OBSERVED historical result (reused
      :class:`HistoricalPerformanceStatistics`),
    * the EVIDENCE STRENGTH (reused :class:`EvidenceStrength`), and
    * the STRATEGY INTERPRETATION (:class:`StrategyAssessmentStatus`
      + ``interpretation`` / ``limitations``).

    It is NOT a trading signal, NOT a price prediction, NOT a
    probability of success, NOT a profitability guarantee, NOT a buy /
    sell recommendation, and NOT a statistical-significance claim.

    Attributes:

    assessment_id
        Deterministic identifier (``"strat-"`` + sha256[:16] of the
        canonical assessment identity).

    spec
        The :class:`CohortSpec` this assessment's cohort belongs to.

    cohort_key
        The cohort key (the combined dimension values, joined by
        ``"|"`` for composite specs). The empty string ``""``
        represents "unavailable" metadata (never invented).

    observed_performance
        The reused :class:`HistoricalPerformanceStatistics` (the
        OBSERVED historical result). Referenced, never recomputed.

    evidence_strength
        The reused :class:`EvidenceStrength` (the EVIDENCE QUALITY).

    assessment_status
        The :class:`StrategyAssessmentStatus` (the STRATEGY
        INTERPRETATION). Deterministic mapping from
        ``evidence_strength``.

    sample_count
        Total outcomes in the cohort
        (== ``observed_performance.total``; repeated for convenient
        downstream consumption).

    resolved_count
        Resolved outcomes in the cohort
        (== ``observed_performance.resolved``; repeated).

    valid_r_count
        Valid realized-R observations
        (== ``observed_performance.valid_r_count``; repeated).

    interpretation
        Human-readable, conservative, descriptive interpretation of
        what the historical evidence tells us about this type of
        opportunity. Descriptive only; never predictive.

    limitations
        Human-readable, descriptive statement of the limitations of
        the assessment (sample size, no statistical test, no future
        guarantee, BOTH_TOUCHED / NO_GEOMETRY / INSUFFICIENT_DATA
        handling). Descriptive only.

    label
        Optional descriptive label identifying the assessment.

    metadata
        Optional descriptive metadata (sorted key/value pairs).
    """

    assessment_id: str
    spec: CohortSpec
    cohort_key: str
    observed_performance: HistoricalPerformanceStatistics
    evidence_strength: EvidenceStrength
    assessment_status: StrategyAssessmentStatus
    sample_count: int
    resolved_count: int
    valid_r_count: int
    interpretation: str
    limitations: str
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def has_evidence(self) -> bool:
        """Whether the assessment is backed by at least usable evidence."""

        return self.assessment_status.is_actionable

    @property
    def is_supported(self) -> bool:
        """
        Whether the assessment carries at least SUPPORTIVE evidence
        (the strongest two interpretation levels).
        """

        return self.assessment_status in (
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE,
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
        )


@dataclass(frozen=True)
class CohortComparisonMetric:
    """
    One descriptive metric comparison between two cohorts.

    The delta is ``value_a - value_b`` when both values are available,
    and ``None`` when either side is unavailable (never fabricated).
    The values themselves are ``None`` when unavailable for that
    cohort.

    Attributes:

    name
        Human-readable metric name.

    value_a
        The metric value for cohort A, or ``None`` when unavailable.

    value_b
        The metric value for cohort B, or ``None`` when unavailable.

    delta
        ``value_a - value_b`` when both available, else ``None``.

    note
        Descriptive note (e.g. which side is larger), never a
        statistical-superiority claim.
    """

    name: str
    value_a: float | None
    value_b: float | None
    delta: float | None
    note: str


@dataclass(frozen=True)
class CohortComparison:
    """
    A DESCRIPTIVE comparison of two supported cohort assessments.

    The comparison exposes the existing observed metrics for both
    cohorts and the existing evidence strengths. It is DESCRIPTIVE
    ONLY. It does NOT claim that one cohort is statistically superior
    to the other: no statistical procedure exists in the project to
    support such a claim. Relative observations (e.g. "cohort A has
    more observations") are descriptive.

    When either cohort is absent from the evidence report (no
    matching cohort), the comparison records that explicitly via
    :attr:`cohort_a_present` / :attr:`cohort_b_present` and every
    metric is ``None`` — never fabricated.

    Attributes:

    comparison_id
        Deterministic identifier (``"compare-"`` + sha256[:16] of the
        canonical comparison identity).

    spec
        The :class:`CohortSpec` both cohorts belong to.

    cohort_a_key / cohort_b_key
        The cohort keys being compared.

    cohort_a_present / cohort_b_present
        Whether each cohort was found in the evidence report. A
        missing cohort is represented explicitly (never silently).

    assessment_a / assessment_b
        The :class:`StrategyEvidenceAssessment` for each cohort, or
        ``None`` when the cohort is absent.

    metrics
        Tuple of :class:`CohortComparisonMetric`, deterministically
        ordered, exposing opportunity count, resolved count, valid-R
        count, win rate, average realized R, total realized R, profit
        factor and (descriptive) evidence-strength rank.

    notes
        Tuple of descriptive relative observations (deterministic).
        Never statistical-superiority claims.

    disclaimer
        The explicit descriptive-only disclaimer.
    """

    comparison_id: str
    spec: CohortSpec
    cohort_a_key: str
    cohort_b_key: str
    cohort_a_present: bool
    cohort_b_present: bool
    assessment_a: StrategyEvidenceAssessment | None
    assessment_b: StrategyEvidenceAssessment | None
    metrics: tuple[CohortComparisonMetric, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
    disclaimer: str = ""

    @property
    def both_present(self) -> bool:
        """Whether both cohorts were found in the evidence report."""

        return self.cohort_a_present and self.cohort_b_present


@dataclass(frozen=True)
class OpportunityEvidenceLookup:
    """
    The result of looking up the historical evidence available for a
    CURRENT opportunity's characteristics.

    The lookup answers: "What historical evidence is available for
    this type of opportunity?". If evidence is unavailable or
    insufficient, the result is explicit (``NO_MATCH`` or an
    :class:`StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE`
    assessment); no evidence is fabricated.

    Attributes:

    lookup_id
        Deterministic identifier (``"lookup-"`` + sha256[:16] of the
        canonical lookup identity).

    profile
        The :class:`OpportunityProfile` used for the lookup.

    match_status
        The :class:`CohortMatchStatus` (``MATCHED`` or ``NO_MATCH``).

    matched_spec
        The :class:`CohortSpec` of the matched cohort, or ``None``
        when no cohort matched.

    matched_cohort
        The matched :class:`HistoricalEvidenceCohort` (reused, by
        reference), or ``None`` when no cohort matched.

    assessment
        The :class:`StrategyEvidenceAssessment` for the matched
        cohort, or ``None`` when no cohort matched.

    limitations
        Human-readable, descriptive statement of the lookup's
        limitations (which dimensions were used, whether a match was
        found, sample-size caveats). Descriptive only.
    """

    lookup_id: str
    profile: OpportunityProfile
    match_status: CohortMatchStatus
    matched_spec: CohortSpec | None
    matched_cohort: HistoricalEvidenceCohort | None
    assessment: StrategyEvidenceAssessment | None
    limitations: str

    @property
    def matched(self) -> bool:
        """Whether a matching historical cohort was found."""

        return self.match_status == CohortMatchStatus.MATCHED

    @property
    def is_empty(self) -> bool:
        """Whether no matching cohort was found (no evidence available)."""

        return self.match_status == CohortMatchStatus.NO_MATCH


__all__ = [
    "CohortComparison",
    "CohortComparisonMetric",
    "CohortMatchStatus",
    "EVIDENCE_TO_STRATEGY",
    "OpportunityEvidenceLookup",
    "OpportunityProfile",
    "StrategyAssessmentStatus",
    "StrategyEvidenceAssessment",
]
