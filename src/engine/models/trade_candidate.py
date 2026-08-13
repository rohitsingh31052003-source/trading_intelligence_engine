"""
Domain models for trade candidate generation (Sprint 11R).

A ``TradeCandidate`` is a structured, DESCRIPTIVE candidate derived from
the existing intelligence produced by Sprints 11O (candle patterns),
11P (market context) and 11Q (setup / confluence). It is NOT a
BUY/SELL trading signal, NOT a prediction, and NOT a guarantee of
profitability. It is a candidate for further validation / evaluation.

The pipeline of concerns is deliberately separated:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)  <- this layer
    4. TRADE VALIDATION             (future)
    5. SIGNAL / EXECUTION           (future)

This layer implements only step 3.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently reported as a real value. In particular, entry / stop /
  target / risk / reward / R:R are ``None`` when the required
  structural references are unavailable or the geometry is invalid.
* ``__post_init__`` validates internal consistency: a candidate may
  not be constructed with contradictory directional geometry (e.g. a
  LONG candidate with a positive ``risk_distance`` whose stop is not
  below the entry). The engine never produces such states; the check
  guards against hand-construction bugs.
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.setup_confluence import (
    EvidenceItem,
    SetupClassification,
)


class CandidateDirection(Enum):
    """
    Directional intent attributed to a trade candidate.

    A candidate carries LONG / SHORT only when it reaches CANDIDATE
    status (or a directional WATCH). NONE is used when no directional
    candidate exists. This is deliberately a separate enum from the
    signal layer's ``SignalDirection``: a candidate is not yet a
    signal.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class CandidateStatus(Enum):
    """
    Promotion status of a trade candidate.

    NO_CANDIDATE
        No trade opportunity at this point: insufficient evidence,
        conflicting evidence, or no directional alignment.

    WATCH
        Directional evidence exists and is worth monitoring, but the
        confluence / evidence quality is not strong enough to form a
        trade candidate (or the setup was downgraded, e.g. an active
        range when range setups are disallowed).

    CANDIDATE
        A structured trade candidate: clear directional bias, enough
        aligned evidence, no disqualifying conflict. Entry / stop /
        target are populated when the structural references are
        available; otherwise the candidate is reported as
        geometrically incomplete rather than fabricated.
    """

    NO_CANDIDATE = "NO_CANDIDATE"
    WATCH = "WATCH"
    CANDIDATE = "CANDIDATE"


class SetupType(Enum):
    """
    Conservative catalog of trade-setup types.

    Only types that can be justified from the existing 11O-11Q
    evidence are populated. When the available evidence cannot
    reliably distinguish a specific type, the conservative generic
    ``SETUP_CANDIDATE`` is used. The system never pretends to know
    more than the evidence supports.

    TREND_CONTINUATION
        Directional trend aligned with the structure and price is at
        a constructive pullback location (e.g. near support in an
        uptrend, near resistance in a downtrend).

    BREAKOUT
        Price has moved beyond a structural level in the candidate
        direction (above resistance for LONG, below support for
        SHORT).

    STRUCTURE_CONTINUATION
        Structure is aligned with the candidate direction but the
        descriptive trend is not, and price is at a constructive
        location. A structure-led continuation candidate.

    RANGE_REJECTION
        Reserved for range-bound rejections. By default the candidate
        engine disallows range setups (``allow_range_setups=False``),
        so this type is not populated by the default configuration;
        it exists so a future, explicit range-setup policy can use it
        without reshaping the model.

    SETUP_CANDIDATE
        Conservative generic fallback: the evidence supports a
        directional candidate but cannot be reliably classified into
        a more specific type.
    """

    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT = "BREAKOUT"
    STRUCTURE_CONTINUATION = "STRUCTURE_CONTINUATION"
    RANGE_REJECTION = "RANGE_REJECTION"
    SETUP_CANDIDATE = "SETUP_CANDIDATE"


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    """
    A deterministic, descriptive trade candidate at one evaluation
    point.

    A ``TradeCandidate`` is NOT a trade signal. It is a structured
    candidate for further validation / evaluation. It makes no
    profitability or predictive claim.

    Attributes:

    timestamp
        Timestamp of the triggering candle, when available.

    evaluation_index
        Chronological index of the evaluation point.

    direction
        Candidate directional intent (LONG / SHORT / NONE).

    status
        Promotion status (NO_CANDIDATE / WATCH / CANDIDATE).

    setup_type
        Conservative setup type classification. ``SETUP_CANDIDATE``
        (generic) is used when the evidence cannot reliably
        distinguish a more specific type, and for non-candidate
        points.

    setup_classification
        The Sprint 11Q ``SetupClassification`` that produced this
        candidate (NO_SETUP / WATCH / POTENTIAL_SETUP). Reused
        verbatim so the candidate layer never re-invents the setup
        classification logic.

    entry_reference
        Objective entry price reference derived from information
        available at the evaluation point (the trigger candle close).
        ``None`` for non-candidate points.

    stop_reference
        Structural stop derived from confirmed market structure
        available at the evaluation point (below a recent swing low
        for LONG, above a recent swing high for SHORT). ``None`` when
        no suitable structural level exists or for non-candidate
        points.

    target_reference
        Deterministic target derived from the opposing structural
        level available at the evaluation point (next resistance for
        LONG, next support for SHORT). ``None`` when no suitable
        level exists or for non-candidate points. Represented
        explicitly as unavailable rather than invented.

    risk_distance
        Absolute risk per unit. ``None`` when entry / stop are
        unavailable or the risk is non-positive (rejected). For LONG
        ``risk = entry - stop``; for SHORT ``risk = stop - entry``.

    reward_distance
        Absolute reward per unit. ``None`` when entry / target are
        unavailable or the reward is non-positive (rejected). For
        LONG ``reward = target - entry``; for SHORT
        ``reward = entry - target``.

    risk_reward_ratio
        ``reward_distance / risk_distance``. ``None`` when either
        distance is unavailable / invalid. Never manufactured.

    confluence_score
        Count of independent evidence sources aligned with the
        candidate direction (reused from the Sprint 11Q setup
        assessment). An integer in ``[0, 5]``. Descriptive count,
        NOT a probability.

    supporting_evidence
        Tuple of Sprint 11Q ``EvidenceItem`` aligned with the
        candidate direction.

    conflicting_evidence
        Tuple of Sprint 11Q ``EvidenceItem`` opposing the candidate
        direction.

    candle_evidence
        Short label of the candle pattern evidence (reused from 11Q).

    market_trend
        Short label of the descriptive market trend (reused from 11Q).

    market_structure
        Short label of the recent structure sequence (reused from 11Q).

    location
        Short label of the price location relative to support /
        resistance (reused from 11Q).

    range_context
        Short label of the range / regime state (reused from 11Q).

    reason
        Human-readable summary of why this candidate status was
        reached.
    """

    timestamp: datetime | None
    evaluation_index: int
    direction: CandidateDirection
    status: CandidateStatus
    setup_type: SetupType
    setup_classification: SetupClassification

    entry_reference: float | None = None
    stop_reference: float | None = None
    target_reference: float | None = None
    risk_distance: float | None = None
    reward_distance: float | None = None
    risk_reward_ratio: float | None = None

    confluence_score: int = 0
    supporting_evidence: tuple[EvidenceItem, ...] = field(
        default_factory=tuple,
    )
    conflicting_evidence: tuple[EvidenceItem, ...] = field(
        default_factory=tuple,
    )
    candle_evidence: str = "none"
    market_trend: str = "UNKNOWN"
    market_structure: str = "none"
    location: str = "UNKNOWN"
    range_context: str = "UNKNOWN"
    reason: str = ""

    # ------------------------------------------------------------
    # DERIVED PROPERTIES
    # ------------------------------------------------------------

    @property
    def is_candidate(self) -> bool:
        """Whether this point reached CANDIDATE status."""

        return self.status == CandidateStatus.CANDIDATE

    @property
    def geometry_complete(self) -> bool:
        """
        Whether entry, stop and target are all available AND the
        risk / reward geometry is valid (positive risk and reward).

        A CANDIDATE may be geometrically incomplete when the
        structural references needed for the stop or target are not
        available at the evaluation point. Incompleteness is reported
        honestly (``None``), never fabricated.
        """

        return (
            self.entry_reference is not None
            and self.stop_reference is not None
            and self.target_reference is not None
            and self.risk_distance is not None
            and self.risk_distance > 0
            and self.reward_distance is not None
            and self.reward_distance > 0
            and self.risk_reward_ratio is not None
        )

    # ------------------------------------------------------------
    # CONSISTENCY VALIDATION
    # ------------------------------------------------------------

    def __post_init__(self) -> None:
        """
        Validate internal consistency.

        The engine never produces inconsistent states; this guards
        against hand-construction bugs. Only the relationships that
        are present (non-None) are checked, so partially-populated
        candidates (e.g. incomplete geometry) remain valid.
        """

        entry = self.entry_reference
        stop = self.stop_reference
        target = self.target_reference
        risk = self.risk_distance
        reward = self.reward_distance
        ratio = self.risk_reward_ratio

        # A non-candidate carries no directional geometry.
        if self.status != CandidateStatus.CANDIDATE:
            if self.direction == CandidateDirection.NONE:
                # NONE direction with no geometry is always fine.
                pass
            # WATCH may carry a directional intent without geometry.
            return

        # From here: CANDIDATE status.
        if self.direction == CandidateDirection.NONE:
            raise ValueError(
                "A CANDIDATE must carry a directional intent "
                "(LONG or SHORT), not NONE.",
            )

        # Risk consistency: when entry + stop + risk are all present,
        # risk must match the directional definition and be positive.
        if entry is not None and stop is not None and risk is not None:
            if self.direction == CandidateDirection.LONG:
                expected = entry - stop
            else:
                expected = stop - entry
            if abs(expected - risk) > 1e-9:
                raise ValueError(
                    "risk_distance is inconsistent with entry / stop "
                    "and direction.",
                )
            if risk <= 0:
                raise ValueError(
                    "risk_distance must be positive when populated.",
                )

        # Reward consistency.
        if entry is not None and target is not None and reward is not None:
            if self.direction == CandidateDirection.LONG:
                expected = target - entry
            else:
                expected = entry - target
            if abs(expected - reward) > 1e-9:
                raise ValueError(
                    "reward_distance is inconsistent with entry / "
                    "target and direction.",
                )
            if reward <= 0:
                raise ValueError(
                    "reward_distance must be positive when populated.",
                )

        # Ratio consistency.
        if risk is not None and reward is not None and ratio is not None:
            if risk <= 0:
                raise ValueError(
                    "risk_reward_ratio requires positive risk.",
                )
            if abs((reward / risk) - ratio) > 1e-9:
                raise ValueError(
                    "risk_reward_ratio is inconsistent with "
                    "reward / risk.",
                )

        # Directional geometry ordering (when refs present):
        # LONG requires stop < entry < target; SHORT the mirror.
        if entry is not None and stop is not None:
            if self.direction == CandidateDirection.LONG and stop >= entry:
                raise ValueError(
                    "LONG candidate requires stop below entry.",
                )
            if self.direction == CandidateDirection.SHORT and stop <= entry:
                raise ValueError(
                    "SHORT candidate requires stop above entry.",
                )
        if entry is not None and target is not None:
            if self.direction == CandidateDirection.LONG and target <= entry:
                raise ValueError(
                    "LONG candidate requires target above entry.",
                )
            if self.direction == CandidateDirection.SHORT and target >= entry:
                raise ValueError(
                    "SHORT candidate requires target below entry.",
                )


__all__ = [
    "CandidateDirection",
    "CandidateStatus",
    "SetupType",
    "TradeCandidate",
]
