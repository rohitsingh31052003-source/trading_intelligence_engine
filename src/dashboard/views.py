"""
Dashboard presentation models (productization layer).

These are READ-ONLY presentation projections built FROM the reused
engine outputs. They implement NO trading, scoring, prediction,
decision or geometry logic. Every numeric value they carry is either:

* an OBSERVED / reused value copied verbatim from an existing engine
  output (entry, stop, target, decision classification, opportunity
  status, MTF alignment, evidence strength, ...), or
* a DERIVED presentation value computed by a documented deterministic
  mapping from existing outputs (risk/reward distances, the
  ``ActionabilityState``).

Nothing is fabricated. When the engine did not produce a value, the
presentation model carries an explicit ``None`` / ``"unavailable"`` /
``ActionabilityState.UNAVAILABLE`` sentinel — the dashboard NEVER
invents entry / stop / target / risk-reward to make the UI look
complete.

CRITICAL — trade geometry reuse contract:

The existing Sprint 11R ``TradeCandidate`` (reached via the Sprint 11U
``InstrumentScanResult.decision.candidate``) ALREADY carries:

* ``entry_reference``  -> dashboard entry
* ``stop_reference``   -> dashboard stop loss / invalidation level
* ``target_reference`` -> dashboard target 1
* ``risk_distance`` / ``reward_distance`` / ``risk_reward_ratio``
* ``setup_type``, ``geometry_complete``

The dashboard reuses these verbatim. There is NO second target in the
architecture; ``target_2`` is therefore surfaced as ``None`` with an
explicit ``target_2_supported = False`` flag — never invented.

CRITICAL — actionability is a presentation MIRROR, not a new score:

``ActionabilityState`` is a deterministic, documented mapping from the
AUTHORITATIVE existing outputs (Sprint 11S decision classification +
Sprint 11T opportunity status + scan completeness). It NEVER re-scores,
NEVER re-ranks, NEVER overrides the existing decision, and NEVER
produces a BUY/SELL/ENTER/EXIT/HOLD recommendation. A PREFERRED setup is
descriptive only — it does NOT predict success or profitability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.paper_trade import PaperTrade


class ActionabilityState(Enum):
    """
    Presentation-level actionability / review state for the trade-review
    interface.

    This is a DETERMINISTIC MIRROR of the AUTHORITATIVE existing outputs
    (scan completeness + Sprint 11S decision classification + Sprint 11T
    opportunity status + Sprint 11R geometry completeness + Sprint 11Y
    evidence strength). It is NOT a new predictive score, NOT a
    probability, NOT a confidence percentage, and NOT a BUY/SELL/ENTER/
    EXIT/HOLD recommendation. The mapping is documented in
    :func:`derive_actionability` and never introduces information not
    present in the existing outputs.

    The states answer one question for the trader: *is this something
    worth reviewing right now, and if not, why?*

    INVALID
        The scan was INCOMPLETE (missing timeframe / context data), no
        instrument / timeframe data was available, or no decision /
        opportunity could be produced. Nothing actionable can be shown;
        no value is fabricated. (Replaces the earlier ``UNAVAILABLE``.)

    NO_OPPORTUNITY
        The existing engine produced no opportunity (opportunity status
        ``NO_OPPORTUNITY``) or the decision was ``REJECTED``. There is
        no trade setup to monitor.

    TRADE_GEOMETRY_UNAVAILABLE
        A decision / opportunity exists and is eligible, but the Sprint
        11R trade candidate did not produce complete geometry
        (entry / stop / target). The setup cannot be reviewed as a
        concrete trade because the structural references are not all
        available — none is fabricated to make the panel look complete.

    INSUFFICIENT_EVIDENCE
        Complete geometry exists AND an offline historical evidence
        corpus is attached AND the matched cohort's Sprint 11Y evidence
        strength is ``INSUFFICIENT`` (sample below the configured
        minimum). The setup is technically complete but the historical
        evidence for this kind of setup is too thin to be reliable.
        Note: a MISSING corpus (evidence UNAVAILABLE) is NOT the same as
        INSUFFICIENT evidence — missing evidence is surfaced as a
        warning but does not downgrade a complete setup below
        ``READY_FOR_REVIEW``.

    READY_FOR_REVIEW
        A qualified / preferred decision with an ELIGIBLE opportunity
        AND complete trade geometry AND evidence that is not
        ``INSUFFICIENT`` (sufficient, or no corpus attached). This is
        the "worth reviewing" state. It is DESCRIPTIVE ONLY — it does
        NOT predict success, profitability, or a winning trade.

    WAIT
        A monitorable technical setup exists (decision ``WATCH`` /
        opportunity ``WATCH`` / eligible but not qualified-or-above)
        that does not yet meet the ``READY_FOR_REVIEW`` bar. Watch, do
        not review as a concrete trade yet.
    """

    INVALID = "INVALID"
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    TRADE_GEOMETRY_UNAVAILABLE = "TRADE_GEOMETRY_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    WAIT = "WAIT"

    @property
    def is_actionable(self) -> bool:
        """Whether the state represents a complete, reviewable setup."""

        return self is ActionabilityState.READY_FOR_REVIEW

    @property
    def is_reviewable(self) -> bool:
        """Whether the state represents anything worth a trader's attention."""

        return self in (
            ActionabilityState.READY_FOR_REVIEW,
            ActionabilityState.WAIT,
            ActionabilityState.INSUFFICIENT_EVIDENCE,
        )


@dataclass(frozen=True, slots=True)
class ActionabilityDetail:
    """
    The presentation actionability state paired with a human-readable
    DERIVED reason explaining how the state was reached.

    The reason is DESCRIPTIVE presentation text generated by
    :func:`derive_actionability` from the existing outputs only. It is
    NOT a trading recommendation and NOT a new intelligence value.
    """

    state: ActionabilityState = ActionabilityState.INVALID
    reason: str = ""


def derive_actionability(
    *,
    complete: bool,
    decision_classification: str,
    opportunity_status: str,
    eligible: bool,
    geometry_available: bool = False,
    evidence_strength: str | None = None,
) -> ActionabilityState:
    """
    Derive the presentation :class:`ActionabilityState` from the
    AUTHORITATIVE existing outputs only.

    The mapping is deterministic and documented. It NEVER re-scores and
    NEVER introduces information not present in the existing outputs.

    Inputs:

    * ``complete`` — Sprint 11U scan completeness (reused).
    * ``decision_classification`` — Sprint 11S classification name
      (``REJECTED`` / ``WATCH`` / ``QUALIFIED`` / ``PREFERRED``).
    * ``opportunity_status`` — Sprint 11T opportunity status name.
    * ``eligible`` — Sprint 11T eligibility flag (reused).
    * ``geometry_available`` — Sprint 11R ``TradeCandidate.geometry_complete``
      (reused verbatim; ``False`` when no candidate / incomplete geometry).
    * ``evidence_strength`` — Sprint 11Y ``EvidenceStrength`` name when an
      offline evidence corpus is attached, else ``None`` (no corpus).

    Mapping (priority order — first match wins):

    1. Scan not complete, or no decision / no opportunity produced
       -> ``INVALID``.
    2. Opportunity status ``NO_OPPORTUNITY`` OR decision ``REJECTED``
       -> ``NO_OPPORTUNITY``.
    3. Eligible opportunity but geometry NOT complete
       -> ``TRADE_GEOMETRY_UNAVAILABLE`` (cannot review a concrete trade
       without entry / stop / target; nothing fabricated).
    4. Eligible opportunity WITH complete geometry:
       a. Evidence attached AND strength == ``INSUFFICIENT``
          -> ``INSUFFICIENT_EVIDENCE``.
       b. Decision ``PREFERRED`` or ``QUALIFIED`` -> ``READY_FOR_REVIEW``.
       c. Decision ``WATCH`` (complete geometry, rare) -> ``WAIT``.
    5. Otherwise monitorable (``WATCH`` decision / ``WATCH`` opportunity /
       eligible-but-below-bar) -> ``WAIT``.
    6. Else -> ``NO_OPPORTUNITY``.

    A missing evidence corpus (``evidence_strength is None``) does NOT
    downgrade a complete setup — it is surfaced as a separate warning by
    the service, not as an actionability downgrade.
    """

    dc = (decision_classification or "").upper()
    os_ = (opportunity_status or "").upper()
    ev = (evidence_strength or "").upper() or None

    # 1. Incomplete / nothing produced.
    if not complete or not dc or not os_:
        return ActionabilityState.INVALID

    # 2. Filtered out by the existing opportunity layer or rejected.
    if os_ == "NO_OPPORTUNITY" or dc == "REJECTED":
        return ActionabilityState.NO_OPPORTUNITY

    # 3. Eligible but geometry incomplete -> cannot review a concrete trade.
    if eligible and not geometry_available:
        return ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE

    # 4. Eligible WITH complete geometry.
    if eligible and geometry_available:
        if ev == "INSUFFICIENT":
            return ActionabilityState.INSUFFICIENT_EVIDENCE
        if dc in ("PREFERRED", "QUALIFIED"):
            return ActionabilityState.READY_FOR_REVIEW
        return ActionabilityState.WAIT

    # 5. Otherwise monitorable.
    if dc in ("WATCH", "QUALIFIED", "PREFERRED") or os_ in (
        "WATCH",
        "ALTERNATIVE_OPPORTUNITY",
        "BEST_OPPORTUNITY",
    ):
        return ActionabilityState.WAIT

    # 6. Nothing to surface.
    return ActionabilityState.NO_OPPORTUNITY


def derive_actionability_reason(state: ActionabilityState) -> str:
    """
    Produce the documented, descriptive DERIVED reason for an
    :class:`ActionabilityState`.

    The reason is presentation text only — it explains how the state was
    reached and never adds a trading recommendation.
    """

    reasons = {
        ActionabilityState.INVALID: (
            "Analysis is incomplete or no decision / opportunity was "
            "produced for this instrument / timeframe. No directional "
            "conclusion, geometry or evidence is fabricated."
        ),
        ActionabilityState.NO_OPPORTUNITY: (
            "The existing engine produced no opportunity or the decision "
            "was REJECTED. There is no trade setup to monitor."
        ),
        ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE: (
            "An eligible opportunity exists but the trade candidate did not "
            "produce complete geometry (entry / stop / target). The setup "
            "cannot be reviewed as a concrete trade; no level is fabricated."
        ),
        ActionabilityState.INSUFFICIENT_EVIDENCE: (
            "Complete trade geometry exists but the matched historical "
            "evidence cohort is INSUFFICIENT (sample below the configured "
            "minimum). The setup is technically complete but the historical "
            "evidence is too thin to be reliable. Descriptive only."
        ),
        ActionabilityState.READY_FOR_REVIEW: (
            "A qualified / preferred decision with an eligible opportunity "
            "and complete trade geometry. Worth reviewing. Descriptive "
            "only — does NOT predict success, profitability, or a winning "
            "trade."
        ),
        ActionabilityState.WAIT: (
            "A monitorable technical setup exists but it does not yet meet "
            "the complete-geometry + qualified-or-above bar for review. "
            "Watch; do not review as a concrete trade yet."
        ),
    }
    return reasons.get(state, "")


@dataclass(frozen=True, slots=True)
class GeometryView:
    """
    Presentation view of the trade geometry REUSED verbatim from the
    Sprint 11R ``TradeCandidate``.

    Every field is ``None`` when the engine did not produce it. Nothing
    is fabricated. ``geometry_available`` is the honest flag the UI uses
    to decide whether to render a trade panel or an "unavailable" panel.

    Attributes:

    direction
        ``"LONG"`` / ``"SHORT"`` / ``"NONE"`` / ``""`` (reused).

    entry
        Entry price reference (reused ``entry_reference``), or ``None``.

    stop
        Stop loss price reference (reused ``stop_reference``), or ``None``.

    target_1
        Target price reference (reused ``target_reference``), or ``None``.

    target_2
        Always ``None`` — the architecture produces a single structural
        target. Surfaced honestly; never invented.

    target_2_supported
        Always ``False`` — documents that a second target is not part of
        the current architecture.

    risk_distance
        Absolute risk per unit (reused), or ``None``.

    reward_distance
        Absolute reward per unit (reused), or ``None``.

    risk_reward_ratio
        ``reward / risk`` (reused), or ``None``.

    invalidation_level
        The price beyond which the setup is invalidated. For the existing
        architecture this IS the stop level (the structural level the
        candidate is invalidated against). Reused verbatim — never
        invented. ``None`` when no stop exists.

    geometry_available
        Whether entry AND stop AND target_1 are all present (i.e. the
        candidate's ``geometry_complete``). When ``False`` the trade
        panel shows "TRADE GEOMETRY UNAVAILABLE".

    geometry_complete_source
        The reused ``TradeCandidate.geometry_complete`` flag, surfaced
        unmodified for audit.
    """

    direction: str = ""
    entry: float | None = None
    stop: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    target_2_supported: bool = False
    risk_distance: float | None = None
    reward_distance: float | None = None
    risk_reward_ratio: float | None = None
    invalidation_level: float | None = None
    geometry_available: bool = False
    geometry_complete_source: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """
    Presentation view of the historical evidence REUSED from the
    Sprint 11Y / 11Z / 12A / 12B / 12E chain.

    The dashboard does NOT recompute evidence. When no offline evidence
    corpus is attached, every field is the honest "unavailable"
    sentinel and ``available = False`` — never a fabricated strength,
    win rate or sample size.

    Attributes:

    available
        Whether an offline historical evidence report was attached.

    evidence_strength
        Sprint 11Y ``EvidenceStrength`` name (``INSUFFICIENT`` /
        ``WEAK`` / ``MODERATE`` / ``STRONG``), or ``"UNAVAILABLE"``.

    strategy_interpretation
        Sprint 11Z ``StrategyAssessmentStatus`` name, or
        ``"UNAVAILABLE"``.

    cohort_key
        Human-readable matched cohort key, or ``""`` when no match.

    sample_size
        Number of historical observations in the matched cohort, or
        ``0`` / ``None`` when unavailable.

    win_rate
        Observed historical win rate of the matched cohort (reused 11X
        statistics), or ``None`` when unavailable. NEVER a fabricated
        percentage.

    avg_realized_r
        Observed average realized R (reused 11X), or ``None``.

    profit_factor
        Observed profit factor (reused 11X), or ``None``.

    limitations
        Human-readable limitations string (reused / descriptive).
    """

    available: bool = False
    evidence_strength: str = "UNAVAILABLE"
    strategy_interpretation: str = "UNAVAILABLE"
    cohort_key: str = ""
    sample_size: int | None = None
    win_rate: float | None = None
    avg_realized_r: float | None = None
    profit_factor: float | None = None
    limitations: str = ""


@dataclass(frozen=True, slots=True)
class HistoricalContextView:
    """
    Presentation view of the Product Phase 6E historical evidence
    context (comparable historical setups) attached to a current
    assessment.

    ADDITIVE and CONTEXTUAL ONLY. This view NEVER modifies the
    authoritative existing decision / actionability / geometry / trade
    plan / paper-trading eligibility. When no Phase 6D historical
    research is attached, or no comparable already-resolved historical
    occurrence exists, the fields are the honest "unavailable" sentinels
    — never fabricated statistics.

    Attributes:

    available
        Whether matched historical evidence exists (status AVAILABLE).

    status
        Phase 6E availability status name (``AVAILABLE`` / ``NO_MATCH``
        / ``RESEARCH_UNAVAILABLE``), or ``"UNAVAILABLE"`` when no Phase
        6D store is attached at all.

    evidence_strength
        The REUSED Sprint 11Y ``EvidenceStrength`` name
        (``INSUFFICIENT`` / ``WEAK`` / ``MODERATE`` / ``STRONG``), or
        ``"UNAVAILABLE"`` when no evidence is available. NEVER a second
        strength vocabulary.

    match_key
        The deterministic comparison key used.

    comparable_occurrences / completed_outcomes / ambiguous_count /
    unresolved_count
        Counts over the matched comparable historical occurrences.

    win_rate / average_realized_r / median_realized_r / profit_factor
        REUSED Sprint 11X statistics over the matched already-resolved
        outcomes, or ``None`` when unavailable. NEVER fabricated.

    research_ids
        The Phase 6D research result ids this context was derived from
        (provenance).

    reason / limitations
        Descriptive explanation + the fixed limitations.
    """

    available: bool = False
    status: str = "UNAVAILABLE"
    evidence_strength: str = "UNAVAILABLE"
    match_key: str = ""
    comparable_occurrences: int = 0
    completed_outcomes: int = 0
    ambiguous_count: int = 0
    unresolved_count: int = 0
    win_rate: float | None = None
    average_realized_r: float | None = None
    median_realized_r: float | None = None
    profit_factor: float | None = None
    research_ids: tuple[str, ...] = ()
    reason: str = ""
    limitations: str = ""


def historical_context_view_to_jsonable(view: HistoricalContextView) -> dict[str, Any]:
    """JSON-serializable projection of a :class:`HistoricalContextView`."""

    return {
        "available": view.available,
        "status": view.status,
        "evidence_strength": view.evidence_strength,
        "match_key": view.match_key,
        "comparable_occurrences": view.comparable_occurrences,
        "completed_outcomes": view.completed_outcomes,
        "ambiguous_count": view.ambiguous_count,
        "unresolved_count": view.unresolved_count,
        "win_rate": view.win_rate,
        "average_realized_r": view.average_realized_r,
        "median_realized_r": view.median_realized_r,
        "profit_factor": view.profit_factor,
        "research_ids": list(view.research_ids),
        "reason": view.reason,
        "limitations": view.limitations,
    }


@dataclass(frozen=True, slots=True)
class MarketOverviewView:
    """
    Presentation view of the market context REUSED from the Sprint 11P
    ``MarketContext`` (higher + lower timeframe) and the Sprint 11U
    ``InstrumentScanResult``.

    Every field uses an honest "unavailable" / ``UNKNOWN`` sentinel when
    the engine did not produce it. No level, trend or swing is invented.

    Attributes:

    last_price
        Close of the latest completed setup candle, or ``None``.

    latest_candle_timestamp
        Timestamp of the latest completed setup candle, or ``None``.

    htf_trend
        Higher-timeframe descriptive trend state name, or ``"UNKNOWN"``.

    ltf_trend
        Lower-timeframe descriptive trend state name, or ``"UNKNOWN"``.

    range_state
        Lower-timeframe range state name, or ``"UNKNOWN"``.

    recent_structure
        Human-readable recent structure sequence (e.g. ``"HH, HL"``), or
        ``""`` when insufficient.

    support
        Nearest confirmed support (reused), or ``None``.

    resistance
        Nearest confirmed resistance (reused), or ``None``.

    price_location
        Descriptive price location name, or ``"UNKNOWN"``.

    mtf_alignment
        Sprint 11U ``MTFAlignment`` name, or ``"UNKNOWN"``.

    confirmed_swings
        Number of confirmed swings on the setup timeframe, or ``0``.

    htf_trend_label / ltf_trend_label
        Convenience aliases surfaced for the template.

    data_stale
        Whether the latest candle is older than the configured staleness
        threshold (honest warning flag).
    """

    last_price: float | None = None
    latest_candle_timestamp: datetime | None = None
    htf_trend: str = "UNKNOWN"
    ltf_trend: str = "UNKNOWN"
    range_state: str = "UNKNOWN"
    recent_structure: str = ""
    support: float | None = None
    resistance: float | None = None
    price_location: str = "UNKNOWN"
    mtf_alignment: str = "UNKNOWN"
    confirmed_swings: int = 0
    data_stale: bool = False


@dataclass(frozen=True, slots=True)
class DecisionView:
    """
    Presentation view of the EXISTING decision REUSED verbatim from the
    Sprint 11S ``TradeDecision`` and Sprint 11T ``TradeOpportunity``.

    The decision is AUTHORITATIVE. The dashboard never renames it into
    BUY/SELL and never upgrades / downgrades it.

    Attributes:

    decision_classification
        Sprint 11S classification name (``REJECTED`` / ``WATCH`` /
        ``QUALIFIED`` / ``PREFERRED``), or ``""`` when no decision.

    decision_score
        Sprint 11S decision score total (descriptive evidence
        strength/completeness; NOT a probability), or ``0``.

    opportunity_status
        Sprint 11T opportunity status name, or ``""`` when no
        opportunity.

    rank
        1-based market-level rank among eligible opportunities, or ``0``.

    eligible
        Whether the opportunity passed the eligibility gates (reused).

    confluence_score
        Count of aligned evidence sources (reused), or ``0``.

    rationale
        Reused descriptive rationale, or ``""``.
    """

    decision_classification: str = ""
    decision_score: int = 0
    opportunity_status: str = ""
    rank: int = 0
    eligible: bool = False
    confluence_score: int = 0
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class DataSourceView:
    """
    Presentation view of the DATA SOURCE / FRESHNESS metadata (Product
    Phase 1).

    This is DATA QUALITY / PRODUCT STATE only. It NEVER alters the
    intelligence engine's decision semantics — it is surfaced as metadata
    + presentation warnings. Every field is either an OBSERVED value
    copied verbatim from the provider's :class:`InstrumentSeries` or a
    DERIVED label. Nothing is fabricated: a missing/failed provider
    response is reported honestly as ``UNAVAILABLE`` / ``NOT_READY`` /
    ``ERROR``.

    Attributes:

    data_source
        Name of the data source (``"fixture"`` / ``"yahoo"`` / ``""``).

    provider_status
        :class:`dashboard.data_provider.ProviderStatus` name, or ``""``.

    freshness_state
        :class:`dashboard.data_provider.FreshnessState` name, or ``""``.
        DATA QUALITY only — never a trading signal.

    latest_candle_timestamp
        Timestamp of the latest candle the provider saw (may be a
        forming candle), or ``None``.

    latest_completed_candle_timestamp
        Timestamp of the latest COMPLETED setup candle — the analysis
        boundary. The service uses this as ``evaluation_time``.

    forming_candle_present
        Whether a currently-forming setup candle exists (DISPLAY ONLY;
        never fed to the engine).

    last_successful_fetch_time
        When the provider last successfully fetched data, or ``None``.

    rejected_future_count
        Number of future-dated candles rejected by the boundary (honest
        reporting of malformed provider output).
    """

    data_source: str = ""
    provider_status: str = ""
    freshness_state: str = ""
    latest_candle_timestamp: datetime | None = None
    latest_completed_candle_timestamp: datetime | None = None
    forming_candle_present: bool = False
    last_successful_fetch_time: datetime | None = None
    rejected_future_count: int = 0


@dataclass(frozen=True, slots=True)
class DashboardTradeView:
    """
    The single coherent presentation artifact for one instrument at one
    evaluation point — the trade-review view.

    This model SURFACES existing engine outputs and adds DERIVED
    presentation fields (``actionability`` + ``actionability_detail``).
    It is NOT a new intelligence layer. Every field documents whether it
    is OBSERVED (reused) or DERIVED (presentation).

    Attributes:

    instrument
        Canonical instrument name.

    context_timeframe / setup_timeframe
        The timeframe pair used by the scan (reused).

    evaluation_timestamp
        The evaluation point (latest completed setup candle close), or
        ``None``.

    scan_status
        Sprint 11U ``ScanStatus`` name (reused).

    complete
        Whether the instrument scan was structurally complete (reused).

    market_overview
        :class:`MarketOverviewView` (reused Sprint 11P / 11U).

    decision
        :class:`DecisionView` (reused Sprint 11S / 11T).

    geometry
        :class:`GeometryView` (reused Sprint 11R candidate).

    evidence
        :class:`EvidenceView` (reused Sprint 11Y/11Z/12A/12B/12E; honest
        "unavailable" when no offline corpus attached).

    setup_type
        Sprint 11R setup type name (reused), or ``""``.

    actionability
        DERIVED presentation :class:`ActionabilityState` (documented
        deterministic mirror of the existing decision/opportunity +
        geometry completeness + evidence strength).

    actionability_detail
        DERIVED :class:`ActionabilityDetail` (the state + a descriptive
        reason explaining how it was reached). Presentation only.

    reason
        Reused descriptive reason from the scan result, or ``""``.

    warnings
        Tuple of human-readable honesty warnings (data stale, geometry
        incomplete, evidence insufficient, ...). Descriptive only.
    """

    instrument: str = ""
    context_timeframe: str = ""
    setup_timeframe: str = ""
    evaluation_timestamp: datetime | None = None
    scan_status: str = "NO_OPPORTUNITY"
    complete: bool = False
    market_overview: MarketOverviewView = field(
        default_factory=MarketOverviewView,
    )
    decision: DecisionView = field(default_factory=DecisionView)
    geometry: GeometryView = field(default_factory=GeometryView)
    evidence: EvidenceView = field(default_factory=EvidenceView)
    setup_type: str = ""
    actionability: ActionabilityState = ActionabilityState.INVALID
    actionability_detail: ActionabilityDetail = field(
        default_factory=ActionabilityDetail,
    )
    data_source: DataSourceView = field(default_factory=DataSourceView)
    historical_context: HistoricalContextView = field(
        default_factory=HistoricalContextView,
    )
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_actionable_geometry(self) -> bool:
        """Whether a reviewable setup WITH complete geometry exists."""

        return (
            self.actionability.is_actionable
            and self.geometry.geometry_available
        )


def _fmt_optional(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.4f}"


def to_jsonable(view: DashboardTradeView) -> dict[str, Any]:
    """
    Convert a :class:`DashboardTradeView` into a JSON-serializable dict
    for the API endpoint and the chart payload.

    Deterministic and presentation-only. No engine value is mutated.

    The response separates the five concerns the trade-review interface
    needs — ``market_overview``, ``decision``, ``geometry`` (also aliased
    as ``trade_geometry``), ``evidence`` and ``actionability`` — and never
    collapses them into a single ``signal`` / ``score`` object. The bare
    ``actionability`` string is kept for backward compatibility; the
    ``actionability_detail`` object carries the state + the descriptive
    DERIVED reason.
    """

    mo = view.market_overview
    ts = mo.latest_candle_timestamp
    eval_ts = view.evaluation_timestamp
    geometry = {
        "direction": view.geometry.direction,
        "entry": view.geometry.entry,
        "stop": view.geometry.stop,
        "target_1": view.geometry.target_1,
        "target_2": view.geometry.target_2,
        "target_2_supported": view.geometry.target_2_supported,
        "risk_distance": view.geometry.risk_distance,
        "reward_distance": view.geometry.reward_distance,
        "risk_reward_ratio": view.geometry.risk_reward_ratio,
        "invalidation_level": view.geometry.invalidation_level,
        "geometry_available": view.geometry.geometry_available,
        "entry_fmt": _fmt_optional(view.geometry.entry),
        "stop_fmt": _fmt_optional(view.geometry.stop),
        "target_1_fmt": _fmt_optional(view.geometry.target_1),
        "risk_distance_fmt": _fmt_optional(view.geometry.risk_distance),
        "reward_distance_fmt": _fmt_optional(
            view.geometry.reward_distance,
        ),
        "risk_reward_ratio_fmt": _fmt_optional(
            view.geometry.risk_reward_ratio,
        ),
        "invalidation_level_fmt": _fmt_optional(
            view.geometry.invalidation_level,
        ),
    }
    return {
        "instrument": view.instrument,
        "context_timeframe": view.context_timeframe,
        "setup_timeframe": view.setup_timeframe,
        "evaluation_timestamp": eval_ts.isoformat() if eval_ts else None,
        "scan_status": view.scan_status,
        "complete": view.complete,
        "setup_type": view.setup_type,
        "actionability": view.actionability.value,
        "actionability_detail": {
            "state": view.actionability_detail.state.value,
            "reason": view.actionability_detail.reason,
        },
        "reason": view.reason,
        "warnings": list(view.warnings),
        "market_overview": {
            "last_price": mo.last_price,
            "latest_candle_timestamp": ts.isoformat() if ts else None,
            "htf_trend": mo.htf_trend,
            "ltf_trend": mo.ltf_trend,
            "range_state": mo.range_state,
            "recent_structure": mo.recent_structure,
            "support": mo.support,
            "resistance": mo.resistance,
            "price_location": mo.price_location,
            "mtf_alignment": mo.mtf_alignment,
            "confirmed_swings": mo.confirmed_swings,
            "data_stale": mo.data_stale,
        },
        "decision": {
            "decision_classification": view.decision.decision_classification,
            "decision_score": view.decision.decision_score,
            "opportunity_status": view.decision.opportunity_status,
            "rank": view.decision.rank,
            "eligible": view.decision.eligible,
            "confluence_score": view.decision.confluence_score,
            "rationale": view.decision.rationale,
        },
        "geometry": geometry,
        # Alias so a trade-review consumer can read the trade geometry
        # under a domain-named key. Identical content, by reference.
        "trade_geometry": geometry,
        # Product Phase 6E — ADDITIVE historical context (comparable
        # historical setups). Never modifies the authoritative decision /
        # geometry; honest "unavailable" when no Phase 6D research is
        # attached.
        "historical_context": historical_context_view_to_jsonable(
            view.historical_context,
        ),
        "evidence": {
            "available": view.evidence.available,
            "evidence_strength": view.evidence.evidence_strength,
            "strategy_interpretation": view.evidence.strategy_interpretation,
            "cohort_key": view.evidence.cohort_key,
            "sample_size": view.evidence.sample_size,
            "win_rate": view.evidence.win_rate,
            "avg_realized_r": view.evidence.avg_realized_r,
            "profit_factor": view.evidence.profit_factor,
            "limitations": view.evidence.limitations,
        },
        "data_source": {
            "data_source": view.data_source.data_source,
            "provider_status": view.data_source.provider_status,
            "freshness_state": view.data_source.freshness_state,
            "latest_candle_timestamp": (
                view.data_source.latest_candle_timestamp.isoformat()
                if view.data_source.latest_candle_timestamp
                else None
            ),
            "latest_completed_candle_timestamp": (
                view.data_source.latest_completed_candle_timestamp.isoformat()
                if view.data_source.latest_completed_candle_timestamp
                else None
            ),
            "forming_candle_present": view.data_source.forming_candle_present,
            "last_successful_fetch_time": (
                view.data_source.last_successful_fetch_time.isoformat()
                if view.data_source.last_successful_fetch_time
                else None
            ),
            "rejected_future_count": view.data_source.rejected_future_count,
        },
    }


# ============================================================
# MULTI-INSTRUMENT SCANNER PRESENTATION (Product Phase 2)
# ============================================================


#: Deterministic presentational rank for each Sprint 11S decision
#: classification. Lower sorts first. This is PRESENTATIONAL ORDERING
#: ONLY (stronger classification first) — it is NOT a probability, NOT a
#: predictive score, and NEVER renames the classification. The mapping
#: is fixed and documented; it cannot be configured because configuring
#: it would risk silently re-ranking the AUTHORITATIVE decision.
_DECISION_RANK: dict[str, int] = {
    "PREFERRED": 0,
    "QUALIFIED": 1,
    "WATCH": 2,
    "REJECTED": 3,
}

#: Deterministic presentational rank for each presentation
#: :class:`ActionabilityState`. Lower sorts first (most reviewable first).
#: PRESENTATIONAL ORDERING ONLY.
_ACTIONABILITY_RANK: dict[ActionabilityState, int] = {
    ActionabilityState.READY_FOR_REVIEW: 0,
    ActionabilityState.INSUFFICIENT_EVIDENCE: 1,
    ActionabilityState.WAIT: 2,
    ActionabilityState.TRADE_GEOMETRY_UNAVAILABLE: 3,
    ActionabilityState.NO_OPPORTUNITY: 4,
    ActionabilityState.INVALID: 5,
}

#: Deterministic presentational rank for each Sprint 11Y evidence
#: strength. Lower sorts first (stronger evidence first). A missing
#: corpus (``"UNAVAILABLE"`` / unknown) sorts LAST so that an
#: opportunity is never promoted on the basis of absent evidence.
_EVIDENCE_RANK: dict[str, int] = {
    "STRONG": 0,
    "MODERATE": 1,
    "WEAK": 2,
    "INSUFFICIENT": 3,
    "UNAVAILABLE": 4,
}

#: Deterministic presentational rank for Product Phase 1 freshness.
#: Lower sorts first (freshest first). Freshness is DATA QUALITY ONLY —
#: it never alters the decision; it is a tie-breaker AFTER decision /
#: actionability / evidence.
_FRESHNESS_RANK: dict[str, int] = {
    "CURRENT": 0,
    "STALE": 1,
    "UNAVAILABLE": 2,
    "INVALID": 2,
}


@dataclass(frozen=True, slots=True)
class WatchlistRowView:
    """
    One row of the multi-instrument scanner view (Product Phase 2).

    This is a READ-ONLY presentation projection that WRAPS the reused
    :class:`DashboardTradeView` for one instrument + timeframe and adds
    NOTHING new: every displayed value (decision, actionability,
    geometry, evidence, freshness, data source) is read from the reused
    trade view. The scanner invents NO new score, NO probability, NO
    predictive ranking.

    Attributes:

    instrument
        Canonical instrument name (reused).

    view
        The reused :class:`DashboardTradeView` for this instrument /
        timeframe. Retained BY REFERENCE; never modified.

    error
        ``True`` when this instrument could not be analysed (provider
        failure / unsupported instrument / unsupported timeframe / empty
        data / invalid analysis). The row then carries an honest
        ``ActionabilityState.INVALID`` view with a descriptive reason —
        never a fabricated opportunity. Failure isolation: one bad
        symbol never aborts the whole scan.

    rank
        1-based PRESENTATIONAL order within the scan (1 = most
        reviewable). This is NOT a market-level intelligence rank and
        NOT a probability — it is the deterministic sort position
        produced by :func:`scanner_rank_key`.
    """

    instrument: str = ""
    view: DashboardTradeView = field(default_factory=DashboardTradeView)
    error: bool = False
    rank: int = 0

    @property
    def decision_classification(self) -> str:
        return self.view.decision.decision_classification

    @property
    def actionability(self) -> ActionabilityState:
        return self.view.actionability

    @property
    def evidence_strength(self) -> str:
        return self.view.evidence.evidence_strength

    @property
    def freshness_state(self) -> str:
        return self.view.data_source.freshness_state

    @property
    def geometry_available(self) -> bool:
        return self.view.geometry.geometry_available

    @property
    def setup_type(self) -> str:
        return self.view.setup_type

    @property
    def direction(self) -> str:
        return self.view.geometry.direction

    @property
    def entry(self) -> float | None:
        return self.view.geometry.entry

    @property
    def stop(self) -> float | None:
        return self.view.geometry.stop

    @property
    def target_1(self) -> float | None:
        return self.view.geometry.target_1

    @property
    def risk_reward_ratio(self) -> float | None:
        return self.view.geometry.risk_reward_ratio

    @property
    def complete(self) -> bool:
        return self.view.complete


def scanner_rank_key(row: WatchlistRowView) -> tuple[int, int, int, int, int, str]:
    """
    Build the deterministic PRESENTATIONAL ordering key for a row.

    This is **presentation ordering**, NOT a trading score, NOT a
    probability, and NOT a new intelligence layer. Every component is
    read from the AUTHORITATIVE existing outputs (Sprint 11S decision
    classification, the derived actionability mirror, Sprint 11Y
    evidence strength, Product Phase 1 freshness, Sprint 11R geometry
    completeness). The mapping is fixed and documented.

    Order (lower sorts first — most reviewable / freshest first):

    1. Decision classification strength
       (``PREFERRED`` < ``QUALIFIED`` < ``WATCH`` < ``REJECTED`` < none).
    2. Actionability / readiness mirror
       (``READY_FOR_REVIEW`` < ``INSUFFICIENT_EVIDENCE`` < ``WAIT`` <
       ``TRADE_GEOMETRY_UNAVAILABLE`` < ``NO_OPPORTUNITY`` < ``INVALID``).
    3. Evidence strength (when an offline corpus is attached)
       (``STRONG`` < ``MODERATE`` < ``WEAK`` < ``INSUFFICIENT`` <
       ``UNAVAILABLE``). Missing evidence sorts LAST — an opportunity is
       never promoted on absent evidence.
    4. Geometry availability (complete geometry first).
    5. Freshness (``CURRENT`` < ``STALE`` < ``UNAVAILABLE`` / ``INVALID``)
       — DATA QUALITY tie-break only, never alters the decision.
    6. Instrument name ascending — the final deterministic tie-break so
       two scans of identical data always produce identical ordering
       regardless of input watchlist order.

    Direction (LONG / SHORT) is DELIBERATELY NOT a ranking key: the
    scanner never biases a LONG above a SHORT (or vice versa) on
    direction alone.
    """

    return (
        _DECISION_RANK.get(
            (row.decision_classification or "").upper(), 4,
        ),
        _ACTIONABILITY_RANK.get(row.actionability, 5),
        _EVIDENCE_RANK.get((row.evidence_strength or "").upper(), 4),
        0 if row.geometry_available else 1,
        _FRESHNESS_RANK.get((row.freshness_state or "").upper(), 2),
        row.instrument,
    )


@dataclass(frozen=True, slots=True)
class WatchlistScanView:
    """
    The multi-instrument scanner view for one watchlist + timeframe
    (Product Phase 2).

    This is a READ-ONLY bundle of reused per-instrument
    :class:`DashboardTradeView` outputs, ordered by the deterministic
    PRESENTATIONAL key (:func:`scanner_rank_key`). It implements NO new
    intelligence, NO scoring and NO prediction. Each row reuses the
    existing decision / geometry / evidence / freshness verbatim.

    Attributes:

    watchlist_instruments
        Tuple of canonical instrument names scanned (deterministic
        sorted order, independent of input order).

    setup_timeframe / context_timeframe
        The timeframe pair used for every instrument (reused).

    rows
        Tuple of :class:`WatchlistRowView` ordered by
        :func:`scanner_rank_key` (presentational, not predictive).

    total / analyzed / errored / actionable_count
        Descriptive counts. ``errored`` counts rows that could not be
        analysed (failure isolation — they appear last as INVALID and
        never abort the scan). ``actionable_count`` counts rows whose
        actionability is :attr:`ActionabilityState.READY_FOR_REVIEW`.

    warnings
        Tuple of human-readable honesty warnings (descriptive only).

    rationale
        Descriptive explanation of the presentational ordering.
    """

    watchlist_instruments: tuple[str, ...] = ()
    setup_timeframe: str = ""
    context_timeframe: str = ""
    rows: tuple[WatchlistRowView, ...] = ()
    total: int = 0
    analyzed: int = 0
    errored: int = 0
    actionable_count: int = 0
    warnings: tuple[str, ...] = ()
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.rows) == 0

    @property
    def has_errors(self) -> bool:
        return self.errored > 0


def scan_view_to_jsonable(scan: WatchlistScanView) -> dict[str, Any]:
    """
    Convert a :class:`WatchlistScanView` into a JSON-serializable dict.

    Deterministic and presentation-only. Each row surfaces the reused
    trade view's headline fields WITHOUT recomputing anything. The five
    concerns (decision / geometry / evidence / actionability / data
    source) are kept separate — never collapsed into one signal / score.
    """

    rows = []
    for row in scan.rows:
        v = row.view
        rows.append(
            {
                "rank": row.rank,
                "instrument": row.instrument,
                "error": row.error,
                "complete": row.complete,
                "data_source": v.data_source.data_source,
                "provider_status": v.data_source.provider_status,
                "freshness_state": v.data_source.freshness_state,
                "latest_completed_candle_timestamp": (
                    v.data_source.latest_completed_candle_timestamp.isoformat()
                    if v.data_source.latest_completed_candle_timestamp
                    else None
                ),
                "decision_classification": v.decision.decision_classification,
                "decision_score": v.decision.decision_score,
                "opportunity_status": v.decision.opportunity_status,
                "eligible": v.decision.eligible,
                "actionability": v.actionability.value,
                "actionability_reason": v.actionability_detail.reason,
                "evidence_strength": v.evidence.evidence_strength,
                "evidence_available": v.evidence.available,
                "setup_type": v.setup_type,
                "direction": v.geometry.direction,
                "geometry_available": v.geometry.geometry_available,
                "entry": v.geometry.entry,
                "stop": v.geometry.stop,
                "target_1": v.geometry.target_1,
                "target_2": v.geometry.target_2,
                "target_2_supported": v.geometry.target_2_supported,
                "risk_distance": v.geometry.risk_distance,
                "reward_distance": v.geometry.reward_distance,
                "risk_reward_ratio": v.geometry.risk_reward_ratio,
                "mtf_alignment": v.market_overview.mtf_alignment,
                "htf_trend": v.market_overview.htf_trend,
                "ltf_trend": v.market_overview.ltf_trend,
                "review_url": (
                    f"/?instrument={row.instrument}"
                    f"&timeframe={scan.setup_timeframe}"
                ),
            },
        )
    return {
        "watchlist_instruments": list(scan.watchlist_instruments),
        "setup_timeframe": scan.setup_timeframe,
        "context_timeframe": scan.context_timeframe,
        "rows": rows,
        "total": scan.total,
        "analyzed": scan.analyzed,
        "errored": scan.errored,
        "actionable_count": scan.actionable_count,
        "warnings": list(scan.warnings),
        "rationale": scan.rationale,
    }


@dataclass(frozen=True, slots=True)
class WorkstationView:
    """
    The LIVE TRADING WORKSTATION view (Product Phase 3) — one coherent
    presentation artifact bundling the multi-instrument watchlist status
    (reused :class:`WatchlistScanView`) with the selected instrument's
    detailed trade-review (reused :class:`DashboardTradeView`).

    This is PURE ORCHESTRATION + PRESENTATION. It implements NO new
    intelligence, NO scoring, NO prediction. Every value is read from
    the reused scan + trade-review outputs produced by the EXISTING
    :meth:`DashboardAnalysisService.scan_watchlist` +
    :meth:`DashboardAnalysisService.analyze`. It is the coherent
    "monitor the watchlist + inspect one instrument" surface a human
    trader uses for intraday market monitoring and trade review.

    The workstation is DESCRIPTIVE ONLY. It does NOT guarantee future
    performance, does NOT constitute a trading recommendation, and does
    NOT modify the existing decision / scoring logic. The existing
    decision classification (REJECTED / WATCH / QUALIFIED / PREFERRED)
    is reused verbatim — never renamed to BUY/SELL, never upgraded /
    downgraded.

    Attributes:

    selected_instrument
        Canonical instrument name currently selected for detailed
        review (reused). May be ``""`` when the watchlist is empty.

    setup_timeframe / context_timeframe
        The timeframe pair used (reused). ``context_timeframe`` may be
        ``""`` when no context could be derived.

    scan
        The reused :class:`WatchlistScanView` (watchlist status table).
        Retained BY REFERENCE; never modified.

    selected_view
        The reused :class:`DashboardTradeView` for the selected
        instrument / timeframe (detailed review). Retained BY REFERENCE;
        never modified. ``None`` only when nothing could be analysed
        (e.g. empty watchlist / unsupported timeframe).

    refresh_token
        A deterministic token identifying the current analysis snapshot
        (the latest completed candle timestamp of the selected view, or
        ``""`` when unavailable). Used by the manual refresh control so
        repeated clicks re-run the analysis over the latest completed
        candle. This is NEVER a cache key derived from wall-clock during
        fixture analysis — it is the honest evaluation boundary. There
        is NO background polling; refresh is always a deliberate manual
        action.

    rationale
        Descriptive explanation that the workstation is a coherent
        presentation bundle of already-computed descriptive artifacts,
        not a prediction engine.

    limitations
        Tuple of human-readable honesty limitations (consolidated from
        the selected view's warnings + workstation-level limitations).
        Descriptive only.

    Notes:
        The ``why`` explanation (why the selected instrument is in its
        current state) is synthesized by :func:`workstation_why` from
        the REUSED outputs only — it is presentation text, never a new
        intelligence value.
    """

    selected_instrument: str = ""
    setup_timeframe: str = ""
    context_timeframe: str = ""
    scan: WatchlistScanView = field(default_factory=WatchlistScanView)
    selected_view: DashboardTradeView | None = None
    refresh_token: str = ""
    rationale: str = ""
    limitations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_selected(self) -> bool:
        """Whether a detailed selected-instrument view is present."""

        return self.selected_view is not None

    @property
    def is_empty(self) -> bool:
        """Whether the watchlist scan produced no rows."""

        return self.scan.is_empty


def workstation_why(view: WorkstationView) -> str:
    """
    Synthesize a descriptive "why is the selected instrument in its
    current state?" explanation from the REUSED outputs only.

    This is PRESENTATION TEXT — it strings together the reused
    actionability reason, decision classification, scan reason and
    warnings. It introduces NO new intelligence, NO prediction, NO
    recommendation. When no selected view exists it states that
    honestly.
    """

    if not view.has_selected or view.selected_view is None:
        return (
            "No instrument is selected for detailed review, or no "
            "analysis could be produced for the selected instrument / "
            "timeframe. Select an instrument from the watchlist and "
            "refresh."
        )
    sv = view.selected_view
    parts: list[str] = []
    parts.append(
        f"{view.selected_instrument} ({view.setup_timeframe} setup / "
        f"{view.context_timeframe or 'unavailable'} context) is in the "
        f"{sv.actionability.value} review state.",
    )
    if sv.actionability_detail.reason:
        parts.append(sv.actionability_detail.reason)
    dc = sv.decision.decision_classification
    if dc:
        parts.append(
            f"The existing engine decision classification is {dc} "
            "(authoritative; never renamed to BUY/SELL).",
        )
    if sv.reason:
        parts.append(f"Scan reason: {sv.reason}")
    if sv.warnings:
        parts.append(
            "Active limitations: " + "; ".join(sv.warnings),
        )
    return " ".join(parts)


def workstation_view_to_jsonable(view: WorkstationView) -> dict[str, Any]:
    """
    Convert a :class:`WorkstationView` into a JSON-serializable dict.

    Deterministic and presentation-only. Reuses
    :func:`scan_view_to_jsonable` + :func:`to_jsonable` for the embedded
    scan + trade view; no value is recomputed. The five concerns
    (decision / geometry / evidence / actionability / data source) stay
    separate on the selected view; the scan rows keep their separate
    fields too — never collapsed into one signal / score.
    """

    selected = view.selected_view
    return {
        "selected_instrument": view.selected_instrument,
        "setup_timeframe": view.setup_timeframe,
        "context_timeframe": view.context_timeframe,
        "has_selected": view.has_selected,
        "is_empty": view.is_empty,
        "refresh_token": view.refresh_token,
        "rationale": view.rationale,
        "limitations": list(view.limitations),
        "why": workstation_why(view),
        "scan": scan_view_to_jsonable(view.scan),
        "selected_view": to_jsonable(selected) if selected is not None else None,
    }


# ============================================================
# TRADE PLAN VIEW (Product Phase 4 — risk & trade planning)
# ============================================================


@dataclass(frozen=True, slots=True)
class TradePlanView:
    """
    Presentation view of a risk / trade plan (Product Phase 4).

    This is a READ-ONLY presentation projection of an already-computed
    :class:`~engine.models.trade_plan.TradePlan`. It implements NO
    calculation, NO prediction, NO recommendation. Every value is either
    an OBSERVED value copied verbatim from the plan model (account
    capital, risk %, maximum risk, entry / stop / target, engine risk /
    reward / R:R, quantity, planned risk / reward, status) or a DERIVED
    presentation value (the formatted strings).

    The view separates the same concerns the plan keeps separate:
    ACCOUNT RISK, TRADE GEOMETRY (reused verbatim from the Sprint 11R
    candidate), POSITION SIZE, STATUS. It NEVER collapses them into one
    signal / score. It NEVER renames the existing decision to BUY/SELL
    and NEVER upgrades / downgrades it. Target 2 remains ``None`` with
    ``target_2_supported = False``.

    Attributes are thin projections of the plan model fields; see
    :class:`~engine.models.trade_plan.TradePlan` for the authoritative
    semantics.
    """

    plan_id: str = ""
    instrument: str = ""
    timeframe: str = ""
    direction: str = ""
    existing_decision: str = ""
    actionability: str = ""
    account_capital: Decimal | None = None
    risk_percent: Decimal | None = None
    maximum_risk: Decimal | None = None
    entry: Decimal | None = None
    stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    target_2_supported: bool = False
    engine_risk_distance: Decimal | None = None
    engine_reward_distance: Decimal | None = None
    engine_risk_reward_ratio: Decimal | None = None
    quantity: Decimal | None = None
    planned_risk: Decimal | None = None
    planned_reward: Decimal | None = None
    quantity_status: str = "UNSIZED"
    risk_plan_status: str = "GEOMETRY_UNAVAILABLE"
    quantity_spec_available: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        """Whether the risk plan produced a usable, sized position."""

        return self.risk_plan_status == "VALID"

    @property
    def has_geometry(self) -> bool:
        """Whether the engine geometry entry/stop (risk) is available."""

        return (
            self.entry is not None
            and self.stop is not None
            and self.engine_risk_distance is not None
            and self.engine_risk_distance > 0
        )


def trade_plan_view_to_jsonable(view: TradePlanView) -> dict[str, Any]:
    """Convert a :class:`TradePlanView` into a JSON-serializable dict.

    Deterministic and presentation-only. ``Decimal`` values are rendered
    as their string form so monetary precision survives the JSON round
    trip; a parallel ``_float`` field is included for convenience
    consumers. No value is recomputed.
    """

    def _dec(d: Decimal | None) -> str | None:
        return None if d is None else str(d)

    def _decf(d: Decimal | None) -> float | None:
        return None if d is None else float(d)

    return {
        "plan_id": view.plan_id,
        "instrument": view.instrument,
        "timeframe": view.timeframe,
        "direction": view.direction,
        "existing_decision": view.existing_decision,
        "actionability": view.actionability,
        "account_capital": _dec(view.account_capital),
        "account_capital_float": _decf(view.account_capital),
        "risk_percent": _dec(view.risk_percent),
        "risk_percent_float": _decf(view.risk_percent),
        "maximum_risk": _dec(view.maximum_risk),
        "maximum_risk_float": _decf(view.maximum_risk),
        "entry": _dec(view.entry),
        "entry_float": _decf(view.entry),
        "stop": _dec(view.stop),
        "stop_float": _decf(view.stop),
        "target_1": _dec(view.target_1),
        "target_1_float": _decf(view.target_1),
        "target_2": _dec(view.target_2),
        "target_2_supported": view.target_2_supported,
        "engine_risk_distance": _dec(view.engine_risk_distance),
        "engine_risk_distance_float": _decf(view.engine_risk_distance),
        "engine_reward_distance": _dec(view.engine_reward_distance),
        "engine_reward_distance_float": _decf(view.engine_reward_distance),
        "engine_risk_reward_ratio": _dec(view.engine_risk_reward_ratio),
        "engine_risk_reward_ratio_float": _decf(view.engine_risk_reward_ratio),
        "quantity": _dec(view.quantity),
        "quantity_float": _decf(view.quantity),
        "planned_risk": _dec(view.planned_risk),
        "planned_risk_float": _decf(view.planned_risk),
        "planned_reward": _dec(view.planned_reward),
        "planned_reward_float": _decf(view.planned_reward),
        "quantity_status": view.quantity_status,
        "risk_plan_status": view.risk_plan_status,
        "quantity_spec_available": view.quantity_spec_available,
        "warnings": list(view.warnings),
        "rationale": view.rationale,
        "label": view.label,
        "metadata": [[k, v] for k, v in view.metadata],
        "is_valid": view.is_valid,
        "has_geometry": view.has_geometry,
    }


# ============================================================
# PAPER TRADING (Product Phase 5) — presentation projections
# ============================================================


@dataclass(frozen=True, slots=True)
class PaperTradeView:
    """
    Presentation view of a :class:`~engine.models.paper_trade.PaperTrade`.

    A READ-ONLY projection. It implements NO calculation, NO prediction,
    NO recommendation. Every value is copied verbatim from the
    paper-trade model (or derived as a presentation string). The system
    decision (``existing_decision``) is AUTHORITATIVE and is never
    renamed / upgraded / downgraded. A paper-trade RESULT is a SEPARATE
    concern from the system DECISION. Target 2 remains ``None`` with
    ``target_2_supported = False``.

    Attributes are thin projections of the paper-trade model fields; see
    :class:`~engine.models.paper_trade.PaperTrade` for the authoritative
    semantics.
    """

    paper_trade_id: str = ""
    instrument: str = ""
    timeframe: str = ""
    direction: str = ""
    existing_decision: str = ""
    setup_type: str = ""
    plan_id: str = ""
    created_at: datetime | None = None
    evaluation_timestamp: datetime | None = None
    entry: Decimal | None = None
    stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    target_2_supported: bool = False
    engine_risk_distance: Decimal | None = None
    engine_reward_distance: Decimal | None = None
    engine_risk_reward_ratio: Decimal | None = None
    planned_quantity: Decimal | None = None
    planned_risk: Decimal | None = None
    maximum_risk: Decimal | None = None
    account_capital: Decimal | None = None
    risk_percent: Decimal | None = None
    status: str = "WAITING_FOR_ENTRY"
    entry_timestamp: datetime | None = None
    actual_entry_price: Decimal | None = None
    exit_timestamp: datetime | None = None
    actual_exit_price: Decimal | None = None
    exit_reason: str = ""
    realized_r: Decimal | None = None
    realized_pnl: Decimal | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    sequence: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in ("CLOSED", "CANCELLED", "INVALIDATED")

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"


def paper_trade_view_to_jsonable(view: PaperTradeView) -> dict[str, Any]:
    """Convert a :class:`PaperTradeView` into a JSON-serializable dict.

    Deterministic and presentation-only. ``Decimal`` values are rendered
    as their string form so monetary precision survives the JSON round
    trip; a parallel ``_float`` field is included for convenience
    consumers. No value is recomputed.
    """

    def _dec(d: Decimal | None) -> str | None:
        return None if d is None else str(d)

    def _decf(d: Decimal | None) -> float | None:
        return None if d is None else float(d)

    def _ts(t: datetime | None) -> str | None:
        return None if t is None else t.isoformat()

    return {
        "paper_trade_id": view.paper_trade_id,
        "instrument": view.instrument,
        "timeframe": view.timeframe,
        "direction": view.direction,
        "existing_decision": view.existing_decision,
        "setup_type": view.setup_type,
        "plan_id": view.plan_id,
        "created_at": _ts(view.created_at),
        "evaluation_timestamp": _ts(view.evaluation_timestamp),
        "entry": _dec(view.entry),
        "entry_float": _decf(view.entry),
        "stop": _dec(view.stop),
        "stop_float": _decf(view.stop),
        "target_1": _dec(view.target_1),
        "target_1_float": _decf(view.target_1),
        "target_2": _dec(view.target_2),
        "target_2_supported": view.target_2_supported,
        "engine_risk_distance": _dec(view.engine_risk_distance),
        "engine_risk_distance_float": _decf(view.engine_risk_distance),
        "engine_reward_distance": _dec(view.engine_reward_distance),
        "engine_reward_distance_float": _decf(view.engine_reward_distance),
        "engine_risk_reward_ratio": _dec(view.engine_risk_reward_ratio),
        "engine_risk_reward_ratio_float": _decf(view.engine_risk_reward_ratio),
        "planned_quantity": _dec(view.planned_quantity),
        "planned_quantity_float": _decf(view.planned_quantity),
        "planned_risk": _dec(view.planned_risk),
        "planned_risk_float": _decf(view.planned_risk),
        "maximum_risk": _dec(view.maximum_risk),
        "maximum_risk_float": _decf(view.maximum_risk),
        "account_capital": _dec(view.account_capital),
        "account_capital_float": _decf(view.account_capital),
        "risk_percent": _dec(view.risk_percent),
        "risk_percent_float": _decf(view.risk_percent),
        "status": view.status,
        "entry_timestamp": _ts(view.entry_timestamp),
        "actual_entry_price": _dec(view.actual_entry_price),
        "actual_entry_price_float": _decf(view.actual_entry_price),
        "exit_timestamp": _ts(view.exit_timestamp),
        "actual_exit_price": _dec(view.actual_exit_price),
        "actual_exit_price_float": _decf(view.actual_exit_price),
        "exit_reason": view.exit_reason,
        "realized_r": _dec(view.realized_r),
        "realized_r_float": _decf(view.realized_r),
        "realized_pnl": _dec(view.realized_pnl),
        "realized_pnl_float": _decf(view.realized_pnl),
        "label": view.label,
        "metadata": [[k, v] for k, v in view.metadata],
        "sequence": view.sequence,
        "is_terminal": view.is_terminal,
        "is_open": view.is_open,
    }


@dataclass(frozen=True, slots=True)
class PaperTradeJournalView:
    """
    Presentation view of the paper-trading journal + performance.

    Bundles the ordered list of paper-trade views + the descriptive
    performance analytics (when computed). The five concerns (system
    decision / geometry / plan / lifecycle / result) stay separate on
    every trade row — never collapsed into one signal / score.
    """

    trades: tuple[PaperTradeView, ...] = field(default_factory=tuple)
    performance: dict[str, Any] | None = None
    rationale: str = ""
    limitations: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.trades) == 0


def paper_trade_journal_view_to_jsonable(view: PaperTradeJournalView) -> dict[str, Any]:
    """Convert a :class:`PaperTradeJournalView` into a JSON dict."""

    return {
        "trades": [paper_trade_view_to_jsonable(t) for t in view.trades],
        "performance": view.performance,
        "rationale": view.rationale,
        "limitations": view.limitations,
        "is_empty": view.is_empty,
    }


def to_paper_trade_view(trade: PaperTrade) -> PaperTradeView:
    """Project a :class:`~engine.models.paper_trade.PaperTrade` into a view.

    Pure projection — every value is copied verbatim. No value is
    recomputed; no decision / geometry / plan semantics are duplicated.
    """

    return PaperTradeView(
        paper_trade_id=trade.paper_trade_id,
        instrument=trade.instrument,
        timeframe=trade.timeframe,
        direction=trade.direction,
        existing_decision=trade.existing_decision,
        setup_type=trade.setup_type,
        plan_id=trade.plan_id,
        created_at=trade.created_at,
        evaluation_timestamp=trade.evaluation_timestamp,
        entry=trade.entry,
        stop=trade.stop,
        target_1=trade.target_1,
        target_2=trade.target_2,
        target_2_supported=trade.target_2_supported,
        engine_risk_distance=trade.engine_risk_distance,
        engine_reward_distance=trade.engine_reward_distance,
        engine_risk_reward_ratio=trade.engine_risk_reward_ratio,
        planned_quantity=trade.planned_quantity,
        planned_risk=trade.planned_risk,
        maximum_risk=trade.maximum_risk,
        account_capital=trade.account_capital,
        risk_percent=trade.risk_percent,
        status=trade.status.value,
        entry_timestamp=trade.entry_timestamp,
        actual_entry_price=trade.actual_entry_price,
        exit_timestamp=trade.exit_timestamp,
        actual_exit_price=trade.actual_exit_price,
        exit_reason=trade.exit_reason.value if trade.exit_reason else "",
        realized_r=trade.realized_r,
        realized_pnl=trade.realized_pnl,
        label=trade.label,
        metadata=trade.metadata,
        sequence=trade.sequence,
    )


@dataclass(frozen=True, slots=True)
class InstrumentOperationRowView:
    """
    Presentation view of one :class:`InstrumentOperationResult`.

    A READ-ONLY projection. It implements NO calculation, NO prediction, NO
    recommendation. Every value is copied verbatim from the operational
    result. The system decision (``decision_classification``) is
    AUTHORITATIVE; a paper-trade RESULT never rewrites it.
    """

    instrument: str = ""
    analysed: bool = False
    actionability: str = ""
    eligible_for_paper_trade: bool = False
    decision_classification: str = ""
    direction: str = ""
    evaluation_timestamp: datetime | None = None
    provider_status: str = ""
    freshness_state: str = ""
    created: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    closed: tuple[str, ...] = field(default_factory=tuple)
    duplicate: bool = False
    duplicate_paper_trade_id: str = ""
    error: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OperationsCycleView:
    """
    Presentation view of an :class:`OperationsCycleResult`.

    A READ-ONLY projection of one operational cycle. It implements NO
    calculation, NO prediction, NO recommendation. Paper trading is
    observational validation only; no real order is placed. The existing
    decision engine remains authoritative.

    Attributes mirror :class:`OperationsCycleResult`; see that model for the
    authoritative semantics. ``status`` is the
    :class:`~dashboard.paper_trade_operations.OperationalStatus` name.
    """

    cycle_id: str = ""
    status: str = "NOT_READY"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reference_now: datetime | None = None
    provider: str = ""
    freshness: str = ""
    instruments_scanned: int = 0
    instruments_analysed: int = 0
    trades_created: int = 0
    trades_updated: int = 0
    trades_closed: int = 0
    duplicates_skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    active_trades: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    results: tuple[InstrumentOperationRowView, ...] = field(default_factory=tuple)
    rationale: str = ""
    limitations: str = ""

    @property
    def is_empty(self) -> bool:
        return self.instruments_scanned == 0


def to_operations_cycle_view(result: Any) -> OperationsCycleView:
    """Project an :class:`OperationsCycleResult` into a view (pure projection)."""

    rows = tuple(
        InstrumentOperationRowView(
            instrument=r.instrument,
            analysed=r.analysed,
            actionability=r.actionability,
            eligible_for_paper_trade=r.eligible_for_paper_trade,
            decision_classification=r.decision_classification,
            direction=r.direction,
            evaluation_timestamp=r.evaluation_timestamp,
            provider_status=r.provider_status,
            freshness_state=r.freshness_state,
            created=tuple(r.created),
            updated=tuple(r.updated),
            closed=tuple(r.closed),
            duplicate=r.duplicate,
            duplicate_paper_trade_id=r.duplicate_paper_trade_id,
            error=r.error,
            reason=r.reason,
        )
        for r in result.results
    )
    return OperationsCycleView(
        cycle_id=result.cycle_id,
        status=result.status.value,
        started_at=result.started_at,
        completed_at=result.completed_at,
        reference_now=result.reference_now,
        provider=result.provider,
        freshness=result.freshness,
        instruments_scanned=result.instruments_scanned,
        instruments_analysed=result.instruments_analysed,
        trades_created=result.trades_created,
        trades_updated=result.trades_updated,
        trades_closed=result.trades_closed,
        duplicates_skipped=result.duplicates_skipped,
        errors=tuple(result.errors),
        active_trades=result.active_trades,
        warnings=tuple(result.warnings),
        results=rows,
        rationale=result.rationale,
        limitations=result.limitations,
    )


def operations_cycle_view_to_jsonable(view: OperationsCycleView) -> dict[str, Any]:
    """Convert an :class:`OperationsCycleView` into a JSON-serializable dict.

    Deterministic and presentation-only. No value is recomputed.
    """

    def _ts(t: datetime | None) -> str | None:
        return None if t is None else t.isoformat()

    return {
        "status": view.status,
        "cycle_id": view.cycle_id,
        "started_at": _ts(view.started_at),
        "completed_at": _ts(view.completed_at),
        "reference_now": _ts(view.reference_now),
        "provider": view.provider,
        "freshness": view.freshness,
        "instruments_scanned": view.instruments_scanned,
        "instruments_analysed": view.instruments_analysed,
        "trades_created": view.trades_created,
        "trades_updated": view.trades_updated,
        "trades_closed": view.trades_closed,
        "duplicates_skipped": view.duplicates_skipped,
        "errors": list(view.errors),
        "active_trades": view.active_trades,
        "warnings": list(view.warnings),
        "results": [
            {
                "instrument": r.instrument,
                "analysed": r.analysed,
                "actionability": r.actionability,
                "eligible_for_paper_trade": r.eligible_for_paper_trade,
                "decision_classification": r.decision_classification,
                "direction": r.direction,
                "evaluation_timestamp": _ts(r.evaluation_timestamp),
                "provider_status": r.provider_status,
                "freshness_state": r.freshness_state,
                "created": list(r.created),
                "updated": list(r.updated),
                "closed": list(r.closed),
                "duplicate": r.duplicate,
                "duplicate_paper_trade_id": r.duplicate_paper_trade_id,
                "error": r.error,
                "reason": r.reason,
            }
            for r in view.results
        ],
        "rationale": view.rationale,
        "limitations": view.limitations,
        "is_empty": view.is_empty,
    }


# ============================================================
# HISTORICAL DATA STATUS (Product Phase 6A)
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalDatasetStatusView:
    """
    Read-only presentation status for ONE stored historical dataset.

    Product Phase 6A is DATA FOUNDATION ONLY: this view NEVER carries
    win rates, average R, profit factors, expected returns, trade
    recommendations or any historical "evidence" — those belong to the
    FUTURE Product Phase 6B historical research corpus.

    Every field is OBSERVED (reused verbatim from the historical-data
    foundation's persisted records / provenance) — nothing is
    recomputed in the dashboard.

    Attributes:

    instrument / timeframe
        Stored identity.

    available
        Whether a stored dataset exists for this identity.

    provider
        Provider name of the most recent ingestion (from persisted
        provenance), or "unavailable" when unknown.

    status
        The persisted :class:`HistoricalIngestionStatus` of the most
        recent ingestion as a string, or "UNAVAILABLE" when nothing is
        stored. Completeness is never claimed beyond that status.

    record_count
        Number of stored candles.

    first_timestamp / last_timestamp
        Stored series bounds (ISO strings), or None.

    reason
        Descriptive provenance reason (empty on success).
    """

    instrument: str
    timeframe: str
    available: bool
    provider: str = "unavailable"
    status: str = "UNAVAILABLE"
    record_count: int = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    reason: str = ""


def historical_dataset_view_to_jsonable(view: HistoricalDatasetStatusView) -> dict:
    """JSON projection of the historical dataset status view."""

    return {
        "instrument": view.instrument,
        "timeframe": view.timeframe,
        "available": view.available,
        "provider": view.provider,
        "status": view.status,
        "record_count": view.record_count,
        "first_timestamp": view.first_timestamp,
        "last_timestamp": view.last_timestamp,
        "reason": view.reason,
    }


__all__ = [
    "ActionabilityDetail",
    "ActionabilityState",
    "DashboardTradeView",
    "DataSourceView",
    "DecisionView",
    "EvidenceView",
    "GeometryView",
    "HistoricalDatasetStatusView",
    "InstrumentOperationRowView",
    "MarketOverviewView",
    "OperationsCycleView",
    "PaperTradeJournalView",
    "PaperTradeView",
    "TradePlanView",
    "WatchlistRowView",
    "WatchlistScanView",
    "WorkstationView",
    "derive_actionability",
    "derive_actionability_reason",
    "historical_dataset_view_to_jsonable",
    "operations_cycle_view_to_jsonable",
    "paper_trade_journal_view_to_jsonable",
    "paper_trade_view_to_jsonable",
    "scan_view_to_jsonable",
    "scanner_rank_key",
    "to_jsonable",
    "to_operations_cycle_view",
    "to_paper_trade_view",
    "trade_plan_view_to_jsonable",
    "workstation_why",
    "workstation_view_to_jsonable",
]
