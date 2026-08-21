"""
Domain models for historical setup research (Product Phase 6D).

Product Phase 6D answers, for the EXISTING setup / structure
architecture (Sprints 11O-11S), over the Product Phase 6C historical
research corpus:

    "When the existing setup structure appeared historically under
    comparable conditions, what happened afterward?"

Phase 6D is RESEARCH ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a new
  decision engine, NOT a scoring layer, NOT a paper-trading layer.
* It reuses the existing deterministic setup detection
  (:class:`~engine.intelligence.setup_confluence.SetupConfluenceEngine`,
  :class:`~engine.intelligence.trade_candidates.TradeCandidateEngine`,
  :class:`~engine.intelligence.trade_decision.TradeDecisionEngine`) and
  the existing forward-only outcome evaluation
  (:class:`~engine.intelligence.historical_outcome.OutcomeEvaluator`)
  VERBATIM. The existing decision architecture remains authoritative.
* A historical outcome / evidence record is DESCRIPTIVE and
  OBSERVATIONAL. It is NOT a prediction, NOT a probability of future
  success, NOT a profitability guarantee and NOT a trading
  recommendation.

DESIGN PRINCIPLE — strict point-in-time separation:

* DETECTION uses only data with ``timestamp <= T`` on the setup
  timeframe and ``timestamp < T`` on the context timeframe (the Phase
  6C corpus guarantees this structurally).
* OUTCOME measurement uses only candles with ``timestamp > T`` within
  the configured forward horizon (the reused Sprint 11W
  :class:`OutcomeEvaluator` enforces this structurally).
* No model carries a hidden future-candle parameter.

DESIGN PRINCIPLE — no fabricated values:

Missing / incomplete geometry, ambiguous same-candle touches and
insufficient forward data are reported via the reused
:class:`~engine.models.historical_outcome.OutcomeStatus` vocabulary —
never padded, repaired or synthesized. Insufficient evidence is
explicit; an empty corpus is NEVER reported as "no setups exist".

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import HistoricalOutcome, OutcomeStatus
from engine.models.historical_performance import HistoricalPerformanceStatistics


# ============================================================
# RESEARCH STATUS VOCABULARY
# ============================================================


class SetupResearchStatus(Enum):
    """
    Explicit overall status of ONE historical setup research run.

    RESEARCHED
        At least one historical setup occurrence was detected and
        evaluated. The result carries observations + evidence.

    NO_OCCURRENCES
        At least one VALID evaluation point was examined, but the
        existing setup architecture detected NO setup occurrence. This
        is an OBSERVED result ("the setup did not occur"), NOT a data
        problem.

    INSUFFICIENT_DATA
        Historical data exists for the request but no evaluation point
        satisfied the minimum-history / data-quality requirements, so
        no point could be researched. Distinct from NO_OCCURRENCES.

    CORPUS_UNAVAILABLE
        No stored historical corpus data exists for the requested
        instrument / timeframe (missing or empty dataset). Research
        cannot run; never silently treated as "no occurrences".

    INVALID_REQUEST
        The research request is inconsistent with the corpus
        configuration (e.g. timeframe mismatch). No research produced
        from an invalid request.
    """

    RESEARCHED = "RESEARCHED"
    NO_OCCURRENCES = "NO_OCCURRENCES"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    CORPUS_UNAVAILABLE = "CORPUS_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"

    @property
    def has_observations(self) -> bool:
        """Only RESEARCHED carries researched observations."""

        return self is SetupResearchStatus.RESEARCHED


# ============================================================
# RESEARCH REQUEST
# ============================================================


@dataclass(frozen=True, slots=True)
class SetupResearchRequest:
    """
    A typed, deterministic historical setup research request.

    The SAME corpus + the SAME request + the SAME research
    configuration ALWAYS produce the SAME research result. No field
    depends on wall-clock time.

    Attributes:

    instrument
        Canonical instrument name (normalized to stripped upper case).

    setup_timeframe / context_timeframe
        Timeframe labels (canonicalized by the engine against the
        corpus configuration). ``""`` context disables the context
        filter comparison.

    start_time / end_time
        Optional research window bounds for the DETECTION grid
        (timezone-aware, ``start_time < end_time``). The outcome
        horizon may legitimately extend beyond ``end_time`` — the
        window bounds DETECTION points, not forward outcome
        measurement.

    forward_horizon
        Maximum number of forward setup-timeframe candles inspected
        for the realized outcome after each occurrence (>= 1).

    minimum_history
        Minimum number of usable setup-timeframe candles required at an
        evaluation boundary for the point to be researched (>= 1). A
        stricter requirement than the corpus minimum is honoured; a
        weaker one never loosens the corpus gate.

    trend_filter / range_filter / mtf_alignment_filter /
    setup_type_filter / direction_filter
        Optional structural / regime filters (reused Sprint 11P / 11R /
        11U enum member names). ``""`` = no filter on that dimension.
        Filters select which detected occurrences are researched; they
        never change the detection itself.

    label / metadata
        Descriptive run identity carried onto the result.
    """

    instrument: str
    setup_timeframe: str = "15m"
    context_timeframe: str = "1D"
    start_time: datetime | None = None
    end_time: datetime | None = None
    forward_horizon: int = 20
    minimum_history: int = 10
    trend_filter: str = ""
    range_filter: str = ""
    mtf_alignment_filter: str = ""
    setup_type_filter: str = ""
    direction_filter: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        instrument = str(self.instrument).strip().upper()
        if not instrument:
            raise ValueError("instrument must be non-empty.")
        object.__setattr__(self, "instrument", instrument)

        if self.forward_horizon < 1:
            raise ValueError("forward_horizon must be >= 1.")
        if self.minimum_history < 1:
            raise ValueError("minimum_history must be >= 1.")

        for name in ("start_time", "end_time"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if (
            self.start_time is not None
            and self.end_time is not None
            and not self.start_time < self.end_time
        ):
            raise ValueError("start_time must be strictly before end_time.")

        for name in (
            "trend_filter",
            "range_filter",
            "mtf_alignment_filter",
            "setup_type_filter",
            "direction_filter",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string.")
            object.__setattr__(self, name, value.strip().upper())

        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    @property
    def has_filters(self) -> bool:
        """Whether any structural / regime filter is active."""

        return any(
            (
                self.trend_filter,
                self.range_filter,
                self.mtf_alignment_filter,
                self.setup_type_filter,
                self.direction_filter,
            )
        )


# ============================================================
# SETUP OCCURRENCE (detection side at T)
# ============================================================


@dataclass(frozen=True, slots=True)
class SetupOccurrence:
    """
    ONE detected historical occurrence of the EXISTING setup structure
    at an evaluation time ``T``.

    Every field is a VERBATIM projection of the reused Sprint 11O-11S
    engine outputs computed on the Phase 6C point-in-time state at
    ``T`` (data ``<= T`` on the setup timeframe, strictly completed
    ``< T`` on the context timeframe). Nothing is recomputed,
    reinterpreted or invented. A rejected / non-candidate setup keeps
    its existing classification — it is never silently converted into
    an opportunity.

    Attributes:

    instrument / setup_timeframe / context_timeframe / evaluation_time
        Identity + the preserved evaluation timestamp.

    setup_classification / setup_direction / confluence_score
        Reused Sprint 11Q setup assessment (classification name,
        direction name, confluence score).

    candidate_status / setup_type
        Reused Sprint 11R trade-candidate status + setup type names.

    decision_classification / decision_score
        Reused Sprint 11S decision classification name + decision
        score (the existing decision engine remains authoritative).

    direction
        Candidate directional intent (``LONG`` / ``SHORT`` / ``NONE``).

    trend_state / range_state / mtf_alignment
        Reused Sprint 11P / 11U regime / structure context names (the
        comparable-conditions dimensions for regime-aware research).

    geometry_available
        Whether the reused Sprint 11R candidate carries complete
        geometry (entry + stop + target + positive risk + reward).

    entry / stop / target
        The reused candidate references, only when actually available;
        ``None`` otherwise (never invented).

    reason
        Human-readable descriptive explanation.
    """

    instrument: str
    setup_timeframe: str
    context_timeframe: str
    evaluation_time: datetime
    setup_classification: str
    setup_direction: str
    confluence_score: int
    candidate_status: str
    decision_classification: str
    decision_score: int
    direction: str
    setup_type: str
    trend_state: str
    range_state: str
    mtf_alignment: str
    geometry_available: bool
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")
        if self.geometry_available:
            if self.entry is None or self.stop is None or self.target is None:
                raise ValueError(
                    "a geometry-available occurrence must carry entry, "
                    "stop and target.",
                )

    @property
    def is_directional(self) -> bool:
        """Whether the occurrence carries a LONG / SHORT intent."""

        return self.direction in ("LONG", "SHORT")


# ============================================================
# RESEARCH OBSERVATION (occurrence + realized outcome)
# ============================================================


@dataclass(frozen=True, slots=True)
class SetupResearchObservation:
    """
    ONE researched historical observation: the detected setup
    occurrence at ``T`` paired with its realized forward-only
    historical outcome.

    The :class:`SetupOccurrence` (detection side, data ``<= T``) and
    the reused Sprint 11W :class:`HistoricalOutcome` (outcome side,
    data strictly ``> T``) are kept as SEPARATE concerns — the two are
    never merged into a single value. The outcome is retained by
    reference and never mutated.

    The observation is DESCRIPTIVE. It reports what price did after the
    existing setup structure occurred at ``T``. It is NOT a prediction,
    NOT a probability of success, NOT a profitability guarantee and NOT
    a trading recommendation.
    """

    occurrence: SetupOccurrence
    outcome: HistoricalOutcome

    @property
    def outcome_status(self) -> OutcomeStatus:
        return self.outcome.outcome_status

    @property
    def evaluation_time(self) -> datetime:
        return self.occurrence.evaluation_time

    @property
    def is_completed(self) -> bool:
        """TARGET_HIT / STOP_HIT / EXPIRED (a determinate outcome)."""

        return self.outcome_status in (
            OutcomeStatus.TARGET_HIT,
            OutcomeStatus.STOP_HIT,
            OutcomeStatus.EXPIRED,
        )

    @property
    def is_ambiguous(self) -> bool:
        """BOTH_TOUCHED (explicitly ambiguous; no R fabricated)."""

        return self.outcome_status is OutcomeStatus.BOTH_TOUCHED


# ============================================================
# HISTORICAL EVIDENCE RECORD (aggregated)
# ============================================================


@dataclass(frozen=True, slots=True)
class SetupEvidence:
    """
    A DESCRIPTIVE aggregated historical-evidence record over a set of
    researched observations (overall or one regime/structure group).

    The observed-performance metrics are the reused Sprint 11X
    :class:`HistoricalPerformanceStatistics` computed over the reused
    Sprint 11W outcomes (BY REFERENCE, never recomputed). The strength
    classification reuses the Sprint 11Y
    :class:`~engine.models.historical_evidence.EvidenceStrength`
    vocabulary (sample size is a HARD GATE — a tiny sample is never
    promoted regardless of how favourable the observed result is).

    NOTHING is fabricated: sample sizes, win rates, averages and
    medians are computed ONLY from the available historical
    observations; unavailable metrics remain ``None``. No expectancy,
    confidence or statistical-significance claim is made. Evidence is
    DESCRIPTIVE and OBSERVATIONAL — never predictive.

    Attributes:

    key
        Group identity (``"OVERALL"`` or ``"<DIMENSION>:<value>"``).

    dimension
        The grouping dimension name (``""`` for the overall record).

    sample_size / occurrence_count
        Number of researched observations in this group.

    completed_outcomes
        TARGET_HIT + STOP_HIT + EXPIRED (determinate outcomes).

    ambiguous_count
        BOTH_TOUCHED (explicitly ambiguous outcomes).

    unresolved_count
        INSUFFICIENT_DATA (no forward data within the horizon).

    no_geometry_count
        NO_GEOMETRY (incomplete geometry; nothing fabricated).

    win_count / loss_count / expired_count
        TARGET_HIT / STOP_HIT / EXPIRED counts.

    statistics
        The reused Sprint 11X statistics (BY REFERENCE).

    strength
        The reused Sprint 11Y evidence-strength classification.

    reason
        Human-readable descriptive explanation.
    """

    key: str
    dimension: str
    sample_size: int
    occurrence_count: int
    completed_outcomes: int
    ambiguous_count: int
    unresolved_count: int
    no_geometry_count: int
    win_count: int
    loss_count: int
    expired_count: int
    statistics: HistoricalPerformanceStatistics | None
    strength: EvidenceStrength
    reason: str = ""

    def __post_init__(self) -> None:
        total = (
            self.completed_outcomes
            + self.ambiguous_count
            + self.unresolved_count
            + self.no_geometry_count
        )
        if total != self.occurrence_count:
            raise ValueError(
                "occurrence_count must equal completed + ambiguous + "
                "unresolved + no_geometry counts.",
            )
        if self.sample_size != self.occurrence_count:
            raise ValueError("sample_size must equal occurrence_count.")

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
    def average_mfe(self) -> float | None:
        return (
            self.statistics.average_mfe if self.statistics is not None else None
        )

    @property
    def average_mae(self) -> float | None:
        return (
            self.statistics.average_mae if self.statistics is not None else None
        )

    @property
    def win_rate(self) -> float | None:
        return (
            self.statistics.win_rate if self.statistics is not None else None
        )

    @property
    def is_sufficient(self) -> bool:
        return self.strength.is_sufficient


# ============================================================
# RESEARCH RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class SetupResearchResult:
    """
    The typed, serializable result of ONE historical setup research run.

    Reproducible from ``corpus + research request + research
    configuration``: the ``research_id`` is a deterministic hash of the
    canonical identity (request + corpus configuration + evaluation
    grid + detected occurrences + label/metadata). No wall-clock time,
    no randomness, no unordered iteration.

    The result explicitly distinguishes RESEARCHED / NO_OCCURRENCES /
    INSUFFICIENT_DATA / CORPUS_UNAVAILABLE / INVALID_REQUEST so a data
    problem is NEVER silently reported as "no setups exist".

    DESCRIPTIVE ONLY: historical evidence is observational. It is not a
    prediction, recommendation, or guarantee of future performance.
    """

    research_id: str
    request: SetupResearchRequest
    status: SetupResearchStatus
    points_examined: int = 0
    valid_points: int = 0
    occurrences_detected: int = 0
    occurrence_count: int = 0
    completed_outcomes: int = 0
    ambiguous_count: int = 0
    unresolved_count: int = 0
    observations: tuple[SetupResearchObservation, ...] = ()
    evidence: SetupEvidence | None = None
    grouped_evidence: tuple[SetupEvidence, ...] = ()
    skip_counts: tuple[tuple[str, int], ...] = ()
    rationale: str = ""
    limitations: tuple[str, ...] = ()
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "points_examined",
            "valid_points",
            "occurrences_detected",
            "occurrence_count",
            "completed_outcomes",
            "ambiguous_count",
            "unresolved_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if self.occurrence_count != len(self.observations):
            raise ValueError("occurrence_count must equal len(observations).")
        if (
            self.completed_outcomes + self.ambiguous_count + self.unresolved_count
            > self.occurrence_count
        ):
            raise ValueError(
                "completed + ambiguous + unresolved cannot exceed the "
                "occurrence count.",
            )

    @property
    def has_occurrences(self) -> bool:
        return self.occurrence_count > 0

    @property
    def is_researched(self) -> bool:
        return self.status is SetupResearchStatus.RESEARCHED


__all__ = [
    "SetupEvidence",
    "SetupOccurrence",
    "SetupResearchObservation",
    "SetupResearchRequest",
    "SetupResearchResult",
    "SetupResearchStatus",
]
