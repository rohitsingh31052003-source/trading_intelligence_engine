"""
Domain models for the multi-timeframe market scanner (Sprint 11U).

These models describe a deterministic, descriptive MARKET OPPORTUNITY
SCANNER view over a collection of instrument / timeframe datasets. The
scanner is the next layer of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)  <- this layer
    7. MARKET SCANNER               (Sprint 11U)  <- this layer
    8. TRADE VALIDATION             (future)
    9. SIGNAL / EXECUTION           (future)

A ``MarketScanResult`` is NOT a trading signal. It is a DESCRIPTIVE
classification of the strongest available technical trade opportunities
across multiple instruments and timeframes at one point in time. It is
NOT a probability of success, NOT a profitability prediction, NOT a
guarantee, and NOT a trading recommendation.

DESIGN PRINCIPLE — reuse, do not re-invent:

The scanner layer reuses the existing Sprint 11P ``MarketContext``
(higher-timeframe context), the Sprint 11S ``TradeDecision`` (lower /
execution-timeframe decision) and the Sprint 11T ``TradeOpportunity``
(lower / execution-timeframe opportunity) BY REFERENCE. No candidate,
decision, opportunity, score, classification, geometry or risk/reward
logic is duplicated.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional fields use ``None`` (or explicit ``UNKNOWN`` members) so
  "unobserved" / "unavailable" is never silently reported as a real
  value. In particular, higher-timeframe context and the lower-timeframe
  opportunity are ``None`` when the required timeframe data is missing
  (an INCOMPLETE scan, never a bullish / bearish conclusion fabricated
  from absent data).
* No business logic lives here; the models are data carriers.
* ``__post_init__`` validates internal consistency so the engine never
  produces contradictory states and hand-construction bugs surface early.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TimeframeRole(Enum):
    """
    The role a timeframe plays in the multi-timeframe scan.

    CONTEXT_TIMEFRAME
        The higher timeframe that provides directional / structural
        context. Its latest COMPLETED candle (closed strictly before the
        evaluation time) is the source of context — an in-progress
        higher-timeframe candle is never used, to avoid look-ahead.

    SETUP_TIMEFRAME
        The lower / execution timeframe that provides the trade setup.
        Its latest completed candle at the evaluation time carries the
        candidate / decision / opportunity.

    The scanner supports one context timeframe + one setup timeframe in
    this initial implementation; an arbitrary timeframe graph is out of
    scope.
    """

    CONTEXT_TIMEFRAME = "CONTEXT_TIMEFRAME"
    SETUP_TIMEFRAME = "SETUP_TIMEFRAME"


class MTFAlignment(Enum):
    """
    Descriptive alignment between the higher-timeframe context and the
    lower-timeframe opportunity direction.

    This enum is DELIBERATELY DISTINCT from the Sprint 11Q
    ``EvidenceAlignment`` (ALIGNED / CONFLICTING / NEUTRAL / ABSENT).
    Sprint 11Q describes per-source setup confluence; Sprint 11U
    describes the multi-timeframe relationship between the
    higher-timeframe context and the lower-timeframe opportunity.

    ALIGNED
        The higher-timeframe context direction matches the
        lower-timeframe opportunity direction (e.g. BULLISH higher
        context + LONG lower opportunity).

    CONFLICTING
        The higher-timeframe context direction opposes the
        lower-timeframe opportunity direction (e.g. BEARISH higher
        context + LONG lower opportunity).

    NEUTRAL
        The higher-timeframe context is directional but neither aligned
        nor conflicting with the opportunity — this occurs when the
        higher context is RANGE / NEUTRAL (the context is neither
        bullish nor bearish, so it neither supports nor opposes the
        setup). RANGE / NEUTRAL is NEVER silently interpreted as
        bullish or bearish.

    UNKNOWN
        The higher-timeframe context is unavailable / insufficient, OR
        the lower-timeframe opportunity carries no directional intent.
        Missing evidence is never fabricated: a context that cannot be
        classified is UNKNOWN, not bullish or bearish.
    """

    ALIGNED = "ALIGNED"
    CONFLICTING = "CONFLICTING"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class ScanStatus(Enum):
    """
    Descriptive status of a complete market scan.

    OPPORTUNITIES_FOUND
        At least one eligible opportunity exists across the scanned
        instruments / timeframes.

    WATCH_ONLY
        The market contains technical setups (candidates / decisions
        observed) but NONE meet the opportunity eligibility
        requirements; opportunities exist technically but none are
        surfaceable.

    NO_OPPORTUNITY
        No meaningful candidate / setup exists anywhere in the scanned
        datasets.

    INCOMPLETE
        Required timeframe / context data is missing or insufficient to
        perform the requested scan (e.g. the higher timeframe is empty
        or no completed higher-timeframe candle is available before the
        evaluation time). Missing data is NEVER confused with a bearish
        or bullish conclusion.
    """

    OPPORTUNITIES_FOUND = "OPPORTUNITIES_FOUND"
    WATCH_ONLY = "WATCH_ONLY"
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class InstrumentTimeframe:
    """
    Identity of one instrument / timeframe dataset in the scan.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``, ``"RELIANCE"``).

    timeframe
        Canonical timeframe label (e.g. ``"1D"``, ``"1H"``, ``"15M"``).

    role
        The :class:`TimeframeRole` this dataset plays in the scan.
    """

    instrument: str
    timeframe: str
    role: TimeframeRole


@dataclass(frozen=True, slots=True)
class TimeframeSlice:
    """
    A point-in-time slice of one timeframe dataset.

    Bundles the descriptive intelligence available for one instrument /
    timeframe at the evaluation time ``T``. Every field is derived ONLY
    from the candles that had CLOSED strictly before (context) or at
    (setup) ``T``; no future candle is read (the scanner enforces this
    structurally).

    Attributes:

    identity
        The :class:`InstrumentTimeframe` this slice belongs to.

    timestamp
        The timestamp identifying the information available at the
        evaluation point. For a context timeframe this is the close
        timestamp of the latest COMPLETED higher-timeframe candle; for a
        setup timeframe this is the close timestamp of the latest
        completed setup-timeframe candle.

    market_context
        The Sprint 11P :class:`MarketContext` for this timeframe (the
        higher-timeframe context when ``role == CONTEXT_TIMEFRAME``;
        the lower-timeframe context when ``role == SETUP_TIMEFRAME``).
        ``None`` when insufficient data is available to compute context.

    decision
        The Sprint 11S :class:`TradeDecision` for this timeframe, when
        ``role == SETUP_TIMEFRAME``. ``None`` for context timeframes
        (the higher timeframe provides context, not a trade decision).

    opportunity
        The Sprint 11T :class:`TradeOpportunity` for this timeframe,
        when ``role == SETUP_TIMEFRAME``. ``None`` for context
        timeframes.

    ready
        Whether the slice carries the minimum data required for its
        role. A context slice is ready when a completed
        higher-timeframe candle + market context is available; a setup
        slice is ready when a completed setup-timeframe candle is
        available. When ``False`` the scan is INCOMPLETE for this
        instrument.
    """

    identity: InstrumentTimeframe
    timestamp: datetime | None
    market_context: object | None = None
    decision: object | None = None
    opportunity: object | None = None
    ready: bool = False


@dataclass(frozen=True, slots=True)
class InstrumentScanResult:
    """
    The multi-timeframe scan result for a single instrument.

    Combines the higher-timeframe (context) slice and the
    lower-timeframe (setup) slice into one descriptive, alignment-aware
    view. The underlying ``MarketContext``, ``TradeDecision`` and
    ``TradeOpportunity`` are retained BY REFERENCE and never modified,
    so no candidate / decision / opportunity logic is duplicated.

    Attributes:

    instrument
        Canonical instrument name.

    context_timeframe
        The context-timeframe label (e.g. ``"1D"``).

    setup_timeframe
        The setup-timeframe label (e.g. ``"15M"``).

    timestamp
        The evaluation timestamp (the latest completed
        setup-timeframe candle close), when available.

    higher_context
        The higher-timeframe :class:`MarketContext` slice, when
        available. ``None`` when the higher timeframe is missing /
        insufficient — reported honestly, never fabricated.

    lower_context
        The lower-timeframe :class:`MarketContext`, when available.

    decision
        The lower-timeframe :class:`TradeDecision`, when a setup exists.

    opportunity
        The lower-timeframe :class:`TradeOpportunity`, when an
        opportunity was evaluated.

    alignment
        The :class:`MTFAlignment` between the higher context and the
        lower opportunity direction.

    complete
        Whether the instrument scan is structurally complete (both
        timeframes carried usable data and the setup timeframe reached
        the candidate / decision stage). When ``False`` the instrument
        is INCOMPLETE — missing data, never a directional conclusion.

    direction
        The opportunity direction (reused verbatim for convenient
        access), or ``""`` when no opportunity exists.

    decision_classification
        The Sprint 11S decision classification name (reused verbatim),
        or ``""`` when no decision exists.

    decision_score
        The Sprint 11S decision score total (reused verbatim), or ``0``.

    risk_reward_ratio
        The opportunity's risk/reward ratio, when available (reused
        verbatim). ``None`` when geometry is incomplete.

    reason
        Human-readable, descriptive explanation of the instrument's
        alignment + eligibility verdict. Descriptive only.
    """

    instrument: str
    context_timeframe: str
    setup_timeframe: str
    timestamp: datetime | None
    higher_context: object | None = None
    lower_context: object | None = None
    decision: object | None = None
    opportunity: object | None = None
    alignment: MTFAlignment = MTFAlignment.UNKNOWN
    complete: bool = False
    direction: str = ""
    decision_classification: str = ""
    decision_score: int = 0
    risk_reward_ratio: float | None = None
    # Stored explicitly (not derived from ``opportunity``) so the
    # eligibility verdict SURVIVES serialization (the heavy
    # ``opportunity`` object is dropped on persist and reconstructs as
    # ``None``; the stored flag preserves the verdict for the
    # ``RankedScanOpportunity`` invariant).
    eligible: bool = False
    reason: str = ""

    @property
    def has_opportunity(self) -> bool:
        """Whether this instrument produced an opportunity object."""

        return self.opportunity is not None


@dataclass(frozen=True, slots=True)
class RankedScanOpportunity:
    """
    A market-level ranked opportunity: an instrument scan result paired
    with its 1-based deterministic market-level rank.

    Rank is 1-based among the ELIGIBLE opportunities only. ``0`` when
    the instrument produced no eligible opportunity (it appears last).

    Attributes:

    rank
        1-based deterministic rank among eligible opportunities
        (1 = strongest). ``0`` when ineligible / no opportunity.

    opportunity
        The :class:`InstrumentScanResult` this rank was assigned to.

    alignment
        The :class:`MTFAlignment` (reused verbatim for convenient
        access from the result).
    """

    rank: int
    opportunity: InstrumentScanResult
    alignment: MTFAlignment = MTFAlignment.UNKNOWN

    def __post_init__(self) -> None:
        if self.rank < 0:
            raise ValueError("rank must be non-negative.")
        if self.rank == 0 and self.opportunity.eligible:
            raise ValueError(
                "An eligible opportunity must carry a 1-based rank.",
            )
        if self.rank > 0 and not self.opportunity.eligible:
            raise ValueError(
                "An ineligible opportunity must carry rank 0.",
            )


@dataclass(frozen=True, slots=True)
class MarketScanResult:
    """
    A deterministic, descriptive result of scanning one or more
    instruments across a context + setup timeframe pair.

    The scan is DESCRIPTIVE. It identifies the strongest available
    technical trade opportunities across the scanned instruments /
    timeframes at one evaluation point. It is NOT a prediction, NOT a
    profitability claim, NOT a probability of success, and NOT a
    trading recommendation.

    Attributes:

    scan_id
        Deterministic scan identifier (``"scan-"`` + sha256[:16] of the
        canonical scan identity). ``""`` when the scanner was invoked
        without an identity (e.g. a one-off inline scan).

    timestamp
        The evaluation timestamp (latest completed setup-timeframe
        candle close across instruments), when available.

    instruments
        Sorted tuple of instrument names scanned.

    timeframes
        The ``(context_timeframe, setup_timeframe)`` labels used.

    status
        The :class:`ScanStatus` of the scan.

    results
        Tuple of :class:`InstrumentScanResult` for every instrument,
        ordered strongest-first (eligible ranked opportunities first,
        then ineligible / incomplete last).

    ranked
        Tuple of :class:`RankedScanOpportunity` covering every
        instrument, ordered by market-level rank (eligible first, then
        ineligible / incomplete with rank 0 last).

    best
        The best (rank 1) :class:`RankedScanOpportunity` when an
        eligible opportunity exists, otherwise ``None``. A best
        opportunity is NEVER manufactured: when no instrument is
        eligible, this is ``None``.

    alternatives
        Tuple of :class:`RankedScanOpportunity` ranked 2+ (the
        alternatives below the best). Empty when no best is surfaced or
        only one eligible opportunity exists.

    rejected
        Tuple of :class:`InstrumentScanResult` that were ineligible /
        incomplete / no-opportunity (rank 0), for inspection.

    rationale
        Human-readable, descriptive summary of the scan.
    """

    scan_id: str
    timestamp: datetime | None
    instruments: tuple[str, ...] = field(default_factory=tuple)
    timeframes: tuple[str, str] = ("", "")
    status: ScanStatus = ScanStatus.NO_OPPORTUNITY
    results: tuple[InstrumentScanResult, ...] = field(
        default_factory=tuple,
    )
    ranked: tuple[RankedScanOpportunity, ...] = field(
        default_factory=tuple,
    )
    best: RankedScanOpportunity | None = None
    alternatives: tuple[RankedScanOpportunity, ...] = field(
        default_factory=tuple,
    )
    rejected: tuple[InstrumentScanResult, ...] = field(
        default_factory=tuple,
    )
    rationale: str = ""

    @property
    def has_best(self) -> bool:
        """Whether a best opportunity was identified."""

        return self.best is not None

    @property
    def is_empty(self) -> bool:
        """Whether the scan evaluated no instruments."""

        return len(self.results) == 0

    @property
    def eligible_count(self) -> int:
        """Number of instruments that produced an eligible opportunity."""

        return len(self.ranked) - sum(
            1 for r in self.ranked if r.rank == 0
        )


__all__ = [
    "InstrumentScanResult",
    "InstrumentTimeframe",
    "MarketScanResult",
    "MTFAlignment",
    "RankedScanOpportunity",
    "ScanStatus",
    "TimeframeRole",
    "TimeframeSlice",
]
