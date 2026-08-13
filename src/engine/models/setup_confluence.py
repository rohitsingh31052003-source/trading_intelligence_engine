"""
Domain models for the market setup / confluence intelligence layer
(Sprint 11Q).

These models describe a *setup assessment*: a structured, interpretable
combination of the existing candle-pattern evidence (Sprint 11O) and
market-context evidence (Sprint 11P) into a single descriptive view
that answers, at an evaluation point ``T``:

    1. What directional bias does the current evidence suggest?
    2. Is the market structurally aligned with that bias?
    3. Is price in a technically meaningful location?
    4. Are there candle patterns supporting the context?
    5. How many independent pieces of evidence agree?
    6. Is there enough confluence to classify the point as a potential
       setup?

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Evidence is captured as named, interpretable items — NOT as a single
  numeric aggregation. A reviewer can read ``supporting_evidence`` and
  ``conflicting_evidence`` and understand *why* a classification was
  reached without rerunning the pipeline.
* A ``SetupAssessment`` is DESCRIPTIVE. ``POTENTIAL_SETUP`` means the
  technical evidence currently forms a coherent *candidate* worth
  further evaluation. It is NOT a prediction, guarantee, profitability
  claim, or trading recommendation.
* No raw candle data is duplicated; the assessment references only the
  already-computed pattern / context evidence plus its own derived
  labels.
* Optional fields use ``None`` (or explicit ``UNKNOWN`` members) so
  "unobserved" is never silently reported as "aligned / false".
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SetupDirection(Enum):
    """
    Directional bias attributed to a setup assessment.

    The direction is the *net* descriptive bias suggested by the
    combined evidence. It is ``UNKNOWN`` when the evidence is
    insufficient, entirely neutral, or evenly conflicting.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class SetupClassification(Enum):
    """
    Classification of the confluence at an evaluation point.

    Semantics (descriptive only — never a prediction):

    NO_SETUP
        Insufficient evidence, strongly conflicting evidence, or no
        meaningful directional alignment.

    WATCH
        Some directional evidence exists, but the confluence is not
        strong enough to form a coherent candidate setup.

    POTENTIAL_SETUP
        Multiple independent evidence sources agree, the directional
        bias is clear, and no disqualifying conflict exists.

    IMPORTANT: ``POTENTIAL_SETUP`` does NOT mean profitable, high
    probability, guaranteed, a trading recommendation, or
    live-trading ready. It only means the technical evidence currently
    forms a coherent candidate worth further evaluation.
    """

    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    POTENTIAL_SETUP = "POTENTIAL_SETUP"


class EvidenceAlignment(Enum):
    """
    How an individual piece of evidence relates to the candidate
    setup direction.

    ALIGNED
        The evidence direction matches the candidate bias and supports
        the setup.

    CONFLICTING
        The evidence direction opposes the candidate bias and weakens
        the setup.

    NEUTRAL
        The evidence carries no directional information (e.g. a doji,
        an inside bar, a neutral trend).

    ABSENT
        No evidence of this kind is available at the evaluation point
        (e.g. no candle pattern, no confirmed structure).
    """

    ALIGNED = "ALIGNED"
    CONFLICTING = "CONFLICTING"
    NEUTRAL = "NEUTRAL"
    ABSENT = "ABSENT"


class EvidenceSource(Enum):
    """
    The independent source an evidence item was derived from.

    Each source counts at most once toward the confluence score so
    that "many agreeing evidence sources" reflects genuinely
    independent observations rather than duplicated signals.
    """

    TREND = "TREND"
    STRUCTURE = "STRUCTURE"
    CANDLE = "CANDLE"
    LOCATION = "LOCATION"
    RANGE = "RANGE"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """
    One interpretable piece of setup evidence.

    Attributes:

    source
        The independent evidence source (trend / structure / candle /
        location / range).

    direction
        Directional bias carried by this evidence (BULLISH / BEARISH /
        NEUTRAL / UNKNOWN). ``UNKNOWN`` is used when the evidence
        exists but carries no usable directional information.

    alignment
        How this evidence relates to the candidate setup direction.

    label
        Short human-readable description of the observed value (e.g.
        ``"BULLISH"``, ``"HIGHER_HIGH / HIGHER_LOW"``, ``"HAMMER"``,
        ``"NEAR_SUPPORT"``, ``"IN_RANGE"``).

    reason
        Human-readable explanation of why this evidence was classified
        the way it was.
    """

    source: EvidenceSource
    direction: SetupDirection
    alignment: EvidenceAlignment
    label: str
    reason: str


@dataclass(frozen=True, slots=True)
class SetupEvidence:
    """
    The structured, interpretable evidence behind one setup assessment.

    Every directional evidence source is represented exactly once, in
    its own ``EvidenceItem``, so a reviewer can see precisely which
    sources agree and which conflict. This deliberately avoids
    collapsing the evidence into a single opaque number.

    Attributes:

    trend
        Evidence derived from the descriptive market trend state.

    structure
        Evidence derived from the recent confirmed market structure
        (HH / HL / LH / LL sequence).

    candle
        Evidence derived from the candle / price-action pattern(s)
        attributed to the evaluation point.

    location
        Evidence derived from the price location relative to the
        nearest support / resistance context, evaluated relative to
        the candidate setup direction.

    range
        Evidence derived from the consolidation / range state.

    supporting
        Tuple of evidence items aligned with the candidate direction
        (a convenience view over the above fields).

    conflicting
        Tuple of evidence items opposing the candidate direction.
    """

    trend: EvidenceItem
    structure: EvidenceItem
    candle: EvidenceItem
    location: EvidenceItem
    range: EvidenceItem
    supporting: tuple[EvidenceItem, ...] = field(
        default_factory=tuple,
    )
    conflicting: tuple[EvidenceItem, ...] = field(
        default_factory=tuple,
    )

    @property
    def all(self) -> tuple[EvidenceItem, ...]:
        """All five evidence sources in canonical order."""

        return (
            self.trend,
            self.structure,
            self.candle,
            self.location,
            self.range,
        )


@dataclass(frozen=True, slots=True)
class SetupAssessment:
    """
    A complete setup / confluence assessment at one evaluation point.

    The assessment is DESCRIPTIVE. It is not a trade signal, not a
    prediction, and not a guarantee of profitability. See
    ``SetupClassification`` for the exact semantics of each class.

    Attributes:

    index
        Chronological index of the evaluation point.

    timestamp
        Timestamp of the triggering candle, when available.

    direction
        Net descriptive directional bias suggested by the evidence.

    classification
        The confluence classification (NO_SETUP / WATCH /
        POTENTIAL_SETUP).

    confluence_score
        Count of independent evidence sources ALIGNED with the
        candidate direction. An integer in ``[0, 5]``. This is a count
        of agreeing sources, NOT a probability or weighted score.

    evidence
        The full structured evidence (each source as its own item,
        plus the supporting / conflicting convenience views).

    candle_evidence
        Short label(s) of the candle pattern(s) attributed to this
        point (e.g. ``"HAMMER"``), for quick scanning. Empty when no
        pattern is present.

    structure_evidence
        Short label of the recent structure sequence (e.g.
        ``"HIGHER_HIGH / HIGHER_LOW"``).

    trend_evidence
        Short label of the trend state (e.g. ``"BULLISH"``).

    location_evidence
        Short label of the price location (e.g. ``"NEAR_SUPPORT"``).

    regime_evidence
        Short label of the range / regime state (e.g. ``"IN_RANGE"``
        or ``"NOT_IN_RANGE"``).

    reason
        Human-readable summary of why this classification was reached.
    """

    index: int
    timestamp: datetime | None
    direction: SetupDirection
    classification: SetupClassification
    confluence_score: int
    evidence: SetupEvidence
    candle_evidence: str
    structure_evidence: str
    trend_evidence: str
    location_evidence: str
    regime_evidence: str
    reason: str

    @property
    def has_conflict(self) -> bool:
        """Whether any evidence source conflicts with the direction."""

        return len(self.evidence.conflicting) > 0

    @property
    def is_potential_setup(self) -> bool:
        """Whether the assessment reached ``POTENTIAL_SETUP``."""

        return self.classification == SetupClassification.POTENTIAL_SETUP
