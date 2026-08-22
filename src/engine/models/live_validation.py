"""
Domain models for live paper validation (Product Phase 6F).

Product Phase 6F validates the completed Phase 6A-6E historical-research
pipeline against real / near-live market data during the live market
session, using PAPER TRADING ONLY. It answers:

    "When the system sees a current market state, what did historical
    research say about similar setups, and how does that historical
    expectation compare with the actual subsequent paper-trading
    outcome?"

Phase 6F is an OBSERVATIONAL VALIDATION layer, not a trading system:

* It is NOT a decision engine, NOT a scoring layer, NOT a prediction
  engine, NOT a geometry layer, NOT a new outcome engine, NOT a broker
  and NOT an execution engine.
* The existing decision architecture (Sprints 11S/11T, the dashboard
  actionability mirror, the Product Phase 4 trade plan and the Product
  Phase 5 paper-trading operations) remains AUTHORITATIVE. Phase 6E
  historical evidence is recorded ALONGSIDE the current decision as
  descriptive context; it NEVER creates, modifies, upgrades, downgrades
  or overrides the existing decision classification, actionability,
  direction, geometry, trade plan or paper-trading eligibility.
* The output is DESCRIPTIVE. It is NOT a prediction, NOT a probability
  of future success, NOT a profitability guarantee and NOT a trading
  recommendation. NO BUY/SELL/ENTER/EXIT/HOLD recommendation is produced.

DESIGN PRINCIPLE — references, not duplication:

A validation observation NEVER embeds the historical dataset, the full
market-data series or the full paper-trade record. It REFERENCES the
persisted Phase 6D research (``research_ids``) and the persisted paper
trade (``paper_trade_id``); the existing stores remain the single source
of truth for the heavy records.

DESIGN PRINCIPLE — no fabricated values:

Unavailable evidence / outcome fields are explicit empty / ``None``
sentinels — never padded with defaults that could be misread as real
evidence or a real outcome. A rejected / no-opportunity observation is
still a VALID validation observation.

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
from decimal import Decimal
from enum import Enum


#: Model contract version carried onto observations (audit metadata).
LIVE_VALIDATION_VERSION = "live-validation-v1"

#: Fixed, non-configurable disclaimer (NOT a model field default that a
#: caller could weaken; surfaced on every observation + report).
LIVE_VALIDATION_LIMITATIONS: tuple[str, ...] = (
    "Historical evidence is observational research context. It does not "
    "predict future returns, guarantee profitability, or override the "
    "authoritative decision engine.",
    "This system performs paper trading only. No real orders are placed.",
    "Paper-trade results never rewrite the original system decision; a "
    "paper-trade outcome is a SEPARATE concern from the authoritative "
    "decision classification.",
    "Only completed candles are used for analysis and outcome evaluation; "
    "the forming candle never enters the intelligence or decision engine.",
)


# ============================================================
# VALIDATION STATUS (per-observation; non-predictive)
# ============================================================


class LiveValidationStatus(Enum):
    """
    The validation state of ONE live validation observation.

    DELIBERATELY non-predictive: these statuses describe WHAT WAS
    OBSERVED, never what is expected to happen. A rejected or
    no-opportunity observation is still a valid validation observation.

    OBSERVED
        The current market state + historical evidence context were
        recorded; no paper trade was referenced this cycle (e.g. a
        WATCH / QUALIFIED decision that was not eligible, or an existing
        trade untouched by this cycle).

    PAPER_TRADE_CREATED
        The existing authoritative opportunity was eligible and a paper
        trade was created THIS cycle through the existing paper-trading
        operations layer.

    PAPER_TRADE_ACTIVE
        The referenced paper trade is still non-terminal
        (WAITING_FOR_ENTRY / OPEN) at the end of this cycle.

    COMPLETED
        The referenced paper trade reached a terminal state; the actual
        paper-trading outcome is recorded (realized R only when the
        existing engine provides it — never fabricated).

    NO_OPPORTUNITY
        The current analysis produced no opportunity / no usable
        decision (an honest observation, not a failure).

    REJECTED
        The authoritative existing decision was REJECTED (an honest
        observation, not a failure).

    NOT_READY
        The validation layer is not initialized (e.g. no validation
        store attached).

    ERROR
        The instrument failed this cycle (provider failure, analysis
        failure, persistence failure, ...). Failure is recorded
        explicitly, never silently converted into success.
    """

    OBSERVED = "OBSERVED"
    PAPER_TRADE_CREATED = "PAPER_TRADE_CREATED"
    PAPER_TRADE_ACTIVE = "PAPER_TRADE_ACTIVE"
    COMPLETED = "COMPLETED"
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    REJECTED = "REJECTED"
    NOT_READY = "NOT_READY"
    ERROR = "ERROR"

    @property
    def references_trade(self) -> bool:
        """Whether this status must carry a ``paper_trade_id``."""

        return self in (
            LiveValidationStatus.PAPER_TRADE_CREATED,
            LiveValidationStatus.PAPER_TRADE_ACTIVE,
            LiveValidationStatus.COMPLETED,
        )

    @property
    def is_open(self) -> bool:
        """Whether the observation may still advance (trade unresolved)."""

        return self in (
            LiveValidationStatus.PAPER_TRADE_CREATED,
            LiveValidationStatus.PAPER_TRADE_ACTIVE,
        )


class LiveValidationCycleStatus(Enum):
    """
    The aggregate state of ONE :meth:`run_once` validation cycle.

    DELIBERATELY DISTINCT from the reused operations
    :class:`~dashboard.paper_trade_operations.OperationalStatus` (the
    paper-trading cycle concern) — the validation cycle is a separate,
    descriptive concern.

    READY
        The cycle completed and at least one instrument produced a
        validation observation.

    NO_DATA
        No instrument produced usable completed data this cycle.

    STALE
        The cycle completed but the latest completed candle for every
        analysed instrument was stale (data-quality warning; the cycle
        still ran on completed candles only).

    ERROR
        Every instrument failed this cycle.

    NOT_READY
        The validation layer is not initialized (no validation store).
    """

    READY = "READY"
    NO_DATA = "NO_DATA"
    STALE = "STALE"
    ERROR = "ERROR"
    NOT_READY = "NOT_READY"


# ============================================================
# VALIDATION OBSERVATION
# ============================================================


@dataclass(frozen=True, slots=True)
class LiveValidationObservation:
    """
    ONE auditable live validation observation for one instrument at one
    evaluation time ``T``.

    The observation bundles THREE strictly separate, already-computed
    concerns, recorded side by side WITHOUT merging them into one score:

    1. The CURRENT market state + authoritative existing decision
       (decision classification / actionability / direction / geometry
       availability — reused VERBATIM from the existing analysis view).
    2. The Phase 6E HISTORICAL EVIDENCE context (availability status,
       reused evidence strength, sample size, lightweight descriptive
       statistics + the persisted ``research_ids`` provenance reference).
    3. The PAPER-TRADING outcome (paper-trade id reference, outcome
       status / timestamp, realized R when the existing engine provides
       it — never fabricated).

    Attributes:

    validation_id
        Deterministic identity (``"lval-" + sha256[:16]``) of the
        canonical SETUP identity (instrument / timeframes / evaluation
        timestamp / provider / decision / actionability / direction /
        historical-context status). Same setup at the same ``T`` ->
        same id (idempotent observation handling). Outcome fields are
        NOT part of the identity, so an outcome-advancing update keeps
        the same id (append / history semantics via ``revision``).

    instrument / setup_timeframe / context_timeframe
        Canonical identity of the current assessment.

    evaluation_timestamp
        The evaluation point ``T`` (close of the latest COMPLETED setup
        candle). The forming candle is never used.

    provider / provider_status / freshness_state
        Reused Product Phase 1 data-source concerns (DATA QUALITY only).

    decision_classification
        The authoritative existing Sprint 11S decision classification
        name, reused VERBATIM (never renamed to BUY/SELL, never
        upgraded / downgraded).

    actionability / direction / geometry_available
        Reused VERBATIM from the existing analysis view.

    historical_context_status
        Phase 6E availability status name (``AVAILABLE`` / ``NO_MATCH``
        / ``RESEARCH_UNAVAILABLE`` / ``UNAVAILABLE``).

    historical_evidence_strength
        The reused Sprint 11Y ``EvidenceStrength`` name, or
        ``"UNAVAILABLE"``. NEVER a second strength vocabulary.

    historical_sample_size
        Number of comparable historical occurrences (0 when none).
        UNAVAILABLE evidence is reported with sample size 0 and is never
        conflated with INSUFFICIENT evidence.

    historical_win_rate / historical_average_realized_r /
    historical_profit_factor
        Lightweight DESCRIPTIVE statistics reused from the Phase 6E
        context (summary scalars only — the full dataset is referenced
        via ``research_ids``, never duplicated). ``None`` when
        unavailable; never fabricated.

    research_ids
        Provenance: the persisted Phase 6D research result ids this
        observation's historical context was derived from.

    paper_trade_id
        Reference to the persisted paper trade created / tracked for
        this observation, or ``""`` when none. The full paper-trade
        record lives in the existing paper-trade store.

    outcome_status
        The reused paper-trade outcome / lifecycle state name
        (``WAITING_FOR_ENTRY`` / ``OPEN`` / ``TARGET_HIT`` /
        ``STOP_HIT`` / ``BOTH_TOUCHED`` / ``EXPIRED`` /
        ``MANUAL_CLOSE`` / ``NO_GEOMETRY`` / ``CANCELLED``), or ``""``
        when no trade is referenced.

    outcome_timestamp
        The completion timestamp (the existing trade's
        ``exit_timestamp``), or ``None``. Structurally strictly after
        the evaluation timestamp (entry / exit happen on completed
        candles AFTER creation).

    realized_r
        The realized R-multiple provided by the EXISTING paper-trading
        engine (``Decimal``), or ``None`` (ambiguous / unresolved / no
        trade). NEVER fabricated.

    validation_status
        The :class:`LiveValidationStatus` of this observation.

    recorded_at / updated_at / revision
        Append / history metadata: ``recorded_at`` is the first cycle
        time the observation was persisted; ``updated_at`` the latest;
        ``revision`` increments on every outcome-advancing update. All
        caller-supplied (deterministic; no wall-clock in tests).

    warnings / limitations / label / metadata
        Descriptive warnings + the fixed limitations + run identity.
    """

    validation_id: str
    instrument: str
    setup_timeframe: str = "15m"
    context_timeframe: str = "1D"
    evaluation_timestamp: datetime | None = None
    provider: str = ""
    provider_status: str = ""
    freshness_state: str = ""
    decision_classification: str = ""
    actionability: str = ""
    direction: str = ""
    geometry_available: bool = False
    historical_context_status: str = "UNAVAILABLE"
    historical_evidence_strength: str = "UNAVAILABLE"
    historical_sample_size: int = 0
    historical_win_rate: float | None = None
    historical_average_realized_r: float | None = None
    historical_profit_factor: float | None = None
    research_ids: tuple[str, ...] = ()
    paper_trade_id: str = ""
    outcome_status: str = ""
    outcome_timestamp: datetime | None = None
    realized_r: Decimal | None = None
    validation_status: LiveValidationStatus = LiveValidationStatus.OBSERVED
    recorded_at: datetime | None = None
    updated_at: datetime | None = None
    revision: int = 0
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = LIVE_VALIDATION_LIMITATIONS
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not str(self.validation_id).strip():
            raise ValueError("validation_id must be non-empty.")
        instrument = str(self.instrument).strip().upper()
        if not instrument:
            raise ValueError("instrument must be non-empty.")
        object.__setattr__(self, "instrument", instrument)
        # Timestamps are caller-supplied (deterministic; no wall-clock in
        # tests). No tz-awareness enforcement: the reused operations /
        # paper-trading layers accept both naive and aware datetimes.
        if self.historical_sample_size < 0:
            raise ValueError("historical_sample_size must be non-negative.")
        if self.revision < 0:
            raise ValueError("revision must be non-negative.")
        # Trade-reference invariants: a trade-referencing status must carry
        # a paper_trade_id; a non-referencing status must not.
        if self.validation_status.references_trade and not self.paper_trade_id:
            raise ValueError(
                f"{self.validation_status.value} must carry a paper_trade_id."
            )
        if not self.validation_status.references_trade and self.paper_trade_id:
            raise ValueError(
                f"{self.validation_status.value} must NOT carry a paper_trade_id."
            )
        # COMPLETED must carry an outcome classification; realized R is only
        # meaningful on a completed observation (None allowed: ambiguous).
        if self.validation_status is LiveValidationStatus.COMPLETED:
            if not self.outcome_status:
                raise ValueError("a COMPLETED observation must carry outcome_status.")
        elif self.realized_r is not None or self.outcome_timestamp is not None:
            raise ValueError(
                "realized_r / outcome_timestamp are only valid on a COMPLETED "
                "observation (never fabricated for unresolved observations)."
            )
        if not isinstance(self.realized_r, (Decimal, type(None))):
            raise ValueError("realized_r must be a Decimal or None.")
        object.__setattr__(self, "research_ids", tuple(self.research_ids))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    @property
    def paper_trade_created(self) -> bool:
        """Whether this observation references a paper trade."""

        return bool(self.paper_trade_id)

    @property
    def has_historical_evidence(self) -> bool:
        """Whether historical evidence was AVAILABLE (never fabricated)."""

        return self.historical_context_status == "AVAILABLE"


# ============================================================
# VALIDATION CYCLE RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class LiveValidationCycleResult:
    """
    The result of ONE live paper validation cycle (:meth:`run_once`).

    All fields are descriptive projections of the reused components
    (existing analysis + Phase 6E context + existing paper-trading
    operations). FROZEN + slots. The ``cycle_id`` is deterministic
    (``"lvcycle-" + sha256[:16]``).

    Attributes:

    cycle_id
        Deterministic validation cycle id.

    status
        The :class:`LiveValidationCycleStatus` of the cycle.

    operations_cycle_id
        Reference to the reused existing paper-trading operations cycle
        this validation cycle orchestrated (provenance; the operations
        cycle is NOT duplicated).

    reference_now / started_at / completed_at
        Caller-supplied deterministic cycle boundaries (no wall-clock in
        tests).

    provider
        Name of the data source used this cycle (``"fixture"`` /
        ``"yahoo"``).

    instruments_scanned
        Number of instruments processed this cycle.

    observations
        Tuple of :class:`LiveValidationObservation` (sorted by
        instrument).

    observations_recorded / observations_updated
        Count of NEW observations persisted this cycle / count of
        previously-recorded observations whose referenced paper-trade
        outcome advanced this cycle (append / history semantics).

    paper_trades_created / duplicates_skipped
        Reused from the existing operations cycle.

    errors / warnings / rationale / limitations
        Descriptive honesty fields (failure isolation is explicit).
    """

    cycle_id: str = ""
    status: LiveValidationCycleStatus = LiveValidationCycleStatus.NOT_READY
    operations_cycle_id: str = ""
    reference_now: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    provider: str = ""
    instruments_scanned: int = 0
    observations: tuple[LiveValidationObservation, ...] = field(default_factory=tuple)
    observations_recorded: int = 0
    observations_updated: int = 0
    paper_trades_created: int = 0
    duplicates_skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    limitations: str = ""

    @property
    def is_empty(self) -> bool:
        return self.instruments_scanned == 0

    def observations_with_status(
        self, status: LiveValidationStatus,
    ) -> tuple[LiveValidationObservation, ...]:
        return tuple(o for o in self.observations if o.validation_status is status)


__all__ = [
    "LIVE_VALIDATION_LIMITATIONS",
    "LIVE_VALIDATION_VERSION",
    "LiveValidationCycleResult",
    "LiveValidationCycleStatus",
    "LiveValidationObservation",
    "LiveValidationStatus",
]
