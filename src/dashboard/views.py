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
from enum import Enum
from typing import Any


class ActionabilityState(Enum):
    """
    Presentation-level actionability state.

    This is a DETERMINISTIC MIRROR of the existing decision / opportunity
    outputs. It is NOT a new predictive score, NOT a probability, NOT a
    confidence percentage, and NOT a BUY/SELL recommendation. The mapping
    is documented in :func:`derive_actionability`.

    UNAVAILABLE
        The scan was INCOMPLETE (missing timeframe / context data) or no
        decision / opportunity could be produced. Nothing actionable can
        be shown; no value is fabricated.

    NO_OPPORTUNITY
        The existing engine produced no opportunity (opportunity status
        ``NO_OPPORTUNITY``) or the decision was ``REJECTED``. There is
        no trade setup to monitor.

    WATCH
        The existing decision is ``WATCH`` and/or the opportunity status
        is ``WATCH``. A monitorable technical setup exists but it is not
        strong / clean enough to be a qualified or preferred setup.

    QUALIFIED_SETUP
        The existing decision is ``QUALIFIED`` and the opportunity is
        eligible / surfaced. A coherent technical setup exists. This is
        DESCRIPTIVE and does NOT predict success.

    PREFERRED_SETUP
        The existing decision is ``PREFERRED`` (typically the best
        eligible opportunity). The strongest available technical setup.
        DESCRIPTIVE only — never a guarantee or a trading recommendation.
    """

    UNAVAILABLE = "UNAVAILABLE"
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    WATCH = "WATCH"
    QUALIFIED_SETUP = "QUALIFIED_SETUP"
    PREFERRED_SETUP = "PREFERRED_SETUP"

    @property
    def is_actionable(self) -> bool:
        """Whether the state represents a qualified/preferred setup."""

        return self in (
            ActionabilityState.QUALIFIED_SETUP,
            ActionabilityState.PREFERRED_SETUP,
        )


def derive_actionability(
    *,
    complete: bool,
    decision_classification: str,
    opportunity_status: str,
    eligible: bool,
) -> ActionabilityState:
    """
    Derive the presentation :class:`ActionabilityState` from the
    AUTHORITATIVE existing outputs only.

    The mapping is deterministic and documented. It NEVER re-scores and
    NEVER introduces information not present in the existing outputs.

    Mapping (priority order):

    1. Scan not complete, or no decision/opportunity produced
       -> ``UNAVAILABLE``.
    2. Opportunity status ``NO_OPPORTUNITY`` OR decision ``REJECTED``
       -> ``NO_OPPORTUNITY``.
    3. Decision ``PREFERRED`` (and eligible / surfaced)
       -> ``PREFERRED_SETUP``.
    4. Decision ``QUALIFIED`` (and eligible / surfaced)
       -> ``QUALIFIED_SETUP``.
    5. Otherwise (decision ``WATCH`` / opportunity ``WATCH`` / eligible
       but not qualified-or-above) -> ``WATCH``.

    When the decision / opportunity are present but not eligible, the
    state falls through to ``NO_OPPORTUNITY`` (filtered out by the
    existing opportunity layer) — never manufactured as actionable.
    """

    dc = (decision_classification or "").upper()
    os_ = (opportunity_status or "").upper()

    # 1. Incomplete / nothing produced.
    if not complete or not dc or not os_:
        return ActionabilityState.UNAVAILABLE

    # 2. Filtered out by the existing opportunity layer or rejected.
    if os_ == "NO_OPPORTUNITY" or dc == "REJECTED":
        return ActionabilityState.NO_OPPORTUNITY

    # 3/4. Qualified-or-above require an eligible / surfaced opportunity.
    if dc == "PREFERRED" and eligible:
        return ActionabilityState.PREFERRED_SETUP
    if dc == "QUALIFIED" and eligible:
        return ActionabilityState.QUALIFIED_SETUP

    # 5. Otherwise monitorable.
    if dc in ("WATCH", "QUALIFIED", "PREFERRED") or os_ in (
        "WATCH",
        "ALTERNATIVE_OPPORTUNITY",
        "BEST_OPPORTUNITY",
    ):
        return ActionabilityState.WATCH

    return ActionabilityState.NO_OPPORTUNITY


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
class DashboardTradeView:
    """
    The single coherent presentation artifact for one instrument at one
    evaluation point.

    This model SURFACES existing engine outputs and adds exactly ONE
    derived presentation field (``actionability``). It is NOT a new
    intelligence layer. Every field documents whether it is OBSERVED
    (reused) or DERIVED (presentation).

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
        deterministic mirror of the existing decision/opportunity).

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
    actionability: ActionabilityState = ActionabilityState.UNAVAILABLE
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_actionable_geometry(self) -> bool:
        """Whether a qualified/preferred setup WITH geometry exists."""

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
    """

    mo = view.market_overview
    ts = mo.latest_candle_timestamp
    eval_ts = view.evaluation_timestamp
    return {
        "instrument": view.instrument,
        "context_timeframe": view.context_timeframe,
        "setup_timeframe": view.setup_timeframe,
        "evaluation_timestamp": eval_ts.isoformat() if eval_ts else None,
        "scan_status": view.scan_status,
        "complete": view.complete,
        "setup_type": view.setup_type,
        "actionability": view.actionability.value,
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
        "geometry": {
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
        },
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
    }


__all__ = [
    "ActionabilityState",
    "DashboardTradeView",
    "DecisionView",
    "EvidenceView",
    "GeometryView",
    "MarketOverviewView",
    "derive_actionability",
    "to_jsonable",
]
