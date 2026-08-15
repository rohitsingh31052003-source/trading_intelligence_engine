"""
Domain models for the historical opportunity outcome evaluation layer
(Sprint 11W).

Sprint 11V answers: "What opportunity did the existing intelligence
pipeline identify at time ``T``?". Sprint 11W answers: "What happened
to that opportunity AFTER ``T``?".

These models describe a HISTORICAL, FORWARD-ONLY outcome evaluation of
an opportunity that was generated at a point in time. The outcome is
evaluated using ONLY candles that closed strictly after the evaluation
timestamp, within a configured evaluation horizon. The opportunity /
decision generated at ``T`` is NEVER recalculated using future data and
is NEVER mutated.

A :class:`HistoricalOutcome` is NOT a trading signal, NOT a prediction,
NOT a probability of success, NOT a profitability guarantee and NOT a
trading recommendation. It is a DESCRIPTIVE, point-in-time historical
evaluation of what price did after an opportunity was identified.

DESIGN PRINCIPLE — forward-only, no leakage:

The outcome evaluator inspects ONLY future candles (timestamp strictly
greater than the evaluation timestamp). The decision / opportunity at
``T`` is fixed before outcome evaluation begins. The two concerns
(what the engine knew at ``T`` vs. what happened afterwards) are kept
strictly separate: this model carries the OUTCOME side only.

DESIGN PRINCIPLE — no fabricated values:

Missing values remain ``None`` / unavailable. An opportunity with
incomplete geometry (missing entry / stop / target, or non-positive
risk) receives an explicit :attr:`OutcomeStatus.NO_GEOMETRY` outcome
— never a fabricated target hit, stop hit or R-multiple. An opportunity
with no future candles receives :attr:`OutcomeStatus.INSUFFICIENT_DATA`.
A same-candle ambiguity (a single candle touching BOTH stop and target
with no intrabar ordering) is represented explicitly as
:attr:`OutcomeStatus.BOTH_TOUCHED` with ``realized_r = None`` — a winner
or loser is NEVER manufactured.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional fields use ``None`` so "unobserved" / "unavailable" / "ambiguous"
  is never silently reported as a real value.
* ``__post_init__`` validates internal consistency so the engine never
  produces contradictory states and hand-construction bugs surface early.
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OutcomeStatus(Enum):
    """
    The descriptive outcome status for a historical opportunity.

    This enum is DELIBERATELY DISTINCT from Sprint 11T's
    :class:`~engine.models.opportunity.OpportunityStatus`
    (NO_OPPORTUNITY / WATCH / ALTERNATIVE_OPPORTUNITY / BEST_OPPORTUNITY)
    and from Sprint 11S's
    :class:`~engine.models.trade_decision.DecisionClassification`.
    Sprint 11T describes whether a candidate should be surfaced as an
    opportunity at ``T``; Sprint 11W describes what happened to that
    opportunity AFTER ``T``.

    TARGET_HIT
        Price reached the target level (favorable excursion) within the
        evaluation horizon, BEFORE the stop was touched. A determinate,
        favorable historical outcome. ``exit_price`` is the target
        level; ``realized_r`` is the favorable R-multiple.

    STOP_HIT
        Price reached the stop level (adverse excursion) within the
        evaluation horizon, BEFORE the target was touched. A
        determinate, adverse historical outcome. ``exit_price`` is the
        stop level; ``realized_r`` is the adverse R-multiple.

    BOTH_TOUCHED
        A SINGLE candle touched BOTH the stop and the target, and
        intrabar ordering is unavailable, so the first touch cannot be
        determined deterministically. The outcome is represented
        explicitly as ambiguous — a winner or loser is NEVER
        manufactured. ``realized_r`` is ``None``.

    EXPIRED
        Neither the target nor the stop was reached within the
        configured evaluation horizon. ``exit_price`` is the close of
        the last evaluated candle (mark-to-close at horizon end);
        ``realized_r`` is the mark-to-close R-multiple.

    NO_GEOMETRY
        The opportunity's entry / stop / target geometry is incomplete
        or invalid (a missing level, or non-positive risk), so no
        determinate outcome can be evaluated. No outcome, exit price,
        MFE, MAE or R-multiple is fabricated.

    INSUFFICIENT_DATA
        No future candles are available after the evaluation timestamp
        within the evaluation horizon, so the outcome cannot be
        evaluated. Never a directional conclusion.
    """

    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    BOTH_TOUCHED = "BOTH_TOUCHED"
    EXPIRED = "EXPIRED"
    NO_GEOMETRY = "NO_GEOMETRY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

    @property
    def is_determinate(self) -> bool:
        """Whether the outcome is a determinate terminal outcome.

        A determinate outcome carries a known exit price and (except
        for the explicitly ambiguous ``BOTH_TOUCHED``) a realized
        R-multiple. ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA`` are not
        determinate (no outcome was evaluated).
        """

        return self in (
            OutcomeStatus.TARGET_HIT,
            OutcomeStatus.STOP_HIT,
            OutcomeStatus.BOTH_TOUCHED,
            OutcomeStatus.EXPIRED,
        )

    @property
    def is_ambiguous(self) -> bool:
        """Whether the outcome is explicitly ambiguous (no R fabricated)."""

        return self == OutcomeStatus.BOTH_TOUCHED


@dataclass(frozen=True, slots=True)
class OutcomeSubject:
    """
    A lightweight, serializable projection of an opportunity generated
    at a point in time, carrying exactly the information the
    forward-only outcome evaluator needs.

    The outcome evaluator is deliberately DECOUPLED from the heavy
    Sprint 11R/11S/11T reference objects (``TradeCandidate``,
    ``TradeDecision``, ``TradeOpportunity``), which are not always
    available after serialization. This projection carries the
    entry / stop / target / direction / identity fields the evaluator
    needs, extracted from the live scan result before evaluation.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``).

    direction
        Directional intent (``"LONG"`` / ``"SHORT"`` / ``"NONE"`` /
        ``""``). Only ``LONG`` / ``SHORT`` carry evaluable geometry.

    evaluation_timestamp
        The timestamp of the opportunity (the close of the latest
        completed setup-timeframe candle at ``T``). The evaluator
        inspects ONLY candles with a timestamp strictly greater than
        this.

    entry
        Entry price reference, when available. ``None`` when geometry
        is incomplete.

    stop
        Stop price reference, when available. ``None`` when geometry
        is incomplete.

    target
        Target price reference, when available. ``None`` when geometry
        is incomplete.

    decision_classification
        Sprint 11S decision classification name (reused verbatim for
        identity / reporting), or ``""`` when no decision exists.

    decision_score
        Sprint 11S decision score total (reused verbatim), or ``0``.

    opportunity_status
        Sprint 11T opportunity status name (reused verbatim), or
        ``""`` when no opportunity exists.

    rank
        1-based market-level rank among eligible opportunities (reused
        from the scan), or ``0`` when ineligible / unranked.

    scan_id
        The scan identifier the opportunity belongs to (for traceability).

    setup_timeframe
        The setup-timeframe label the future candles must come from.
    """

    instrument: str
    direction: str
    evaluation_timestamp: datetime | None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    decision_classification: str = ""
    decision_score: int = 0
    opportunity_status: str = ""
    rank: int = 0
    scan_id: str = ""
    setup_timeframe: str = ""

    @property
    def has_geometry(self) -> bool:
        """Whether entry, stop and target are all available."""

        return (
            self.entry is not None
            and self.stop is not None
            and self.target is not None
        )

    @property
    def is_directional(self) -> bool:
        """Whether the subject carries a LONG / SHORT directional intent."""

        return self.direction in ("LONG", "SHORT")


@dataclass(frozen=True, slots=True)
class HistoricalOutcome:
    """
    A descriptive, forward-only historical outcome for one opportunity.

    The outcome is DESCRIPTIVE. It reports what price did after an
    opportunity was identified at ``T``, evaluated using ONLY candles
    that closed strictly after ``T`` within the configured evaluation
    horizon. It is NOT a prediction, NOT a probability of success, NOT
    a profitability guarantee, and NOT a trading recommendation.

    The underlying :class:`OutcomeSubject` is retained BY REFERENCE and
    never modified.

    Attributes:

    subject
        The :class:`OutcomeSubject` (the opportunity projection at
        ``T``) this outcome was evaluated for.

    outcome_status
        The :class:`OutcomeStatus`.

    outcome_timestamp
        Timestamp of the candle that determined the outcome (the
        target / stop / both-touched candle), when determinate.
        ``None`` for ``EXPIRED`` (no single determining candle),
        ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA``.

    exit_price
        The exit price. For ``TARGET_HIT`` / ``STOP_HIT`` this is the
        target / stop level. For ``EXPIRED`` this is the close of the
        last evaluated candle (mark-to-close). ``None`` for
        ``BOTH_TOUCHED`` (ambiguous — no single exit fabricated),
        ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA``.

    bars_held
        Number of forward candles evaluated up to and including the
        determining candle. For ``EXPIRED`` this is the number of
        candles evaluated within the horizon. ``0`` for
        ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA``.

    mfe
        Maximum favorable excursion (absolute price) over the forward
        window. ``None`` when geometry is incomplete / no data.

    mae
        Maximum adverse excursion (absolute price) over the forward
        window. ``None`` when geometry is incomplete / no data.

    mfe_r
        Maximum favorable excursion in R-multiples (``mfe / risk``),
        when risk is valid. ``None`` otherwise.

    mae_r
        Maximum adverse excursion in R-multiples (``mae / risk``),
        when risk is valid. ``None`` otherwise.

    realized_r
        Realized R-multiple for a determinate exit. For ``TARGET_HIT``
        / ``STOP_HIT`` / ``EXPIRED`` this is the R-multiple at the
        exit price. ``None`` for ``BOTH_TOUCHED`` (ambiguous — never
        fabricated), ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA``.

    risk
        Absolute risk per unit (``abs(entry - stop)``), when entry and
        stop are valid and risk is positive. ``None`` otherwise.

    reason
        Human-readable, descriptive explanation of the outcome.
        Descriptive only.
    """

    subject: OutcomeSubject
    outcome_status: OutcomeStatus
    outcome_timestamp: datetime | None = None
    exit_price: float | None = None
    bars_held: int = 0
    mfe: float | None = None
    mae: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    realized_r: float | None = None
    risk: float | None = None
    reason: str = ""

    @property
    def instrument(self) -> str:
        return self.subject.instrument

    @property
    def direction(self) -> str:
        return self.subject.direction

    @property
    def evaluation_timestamp(self) -> datetime | None:
        return self.subject.evaluation_timestamp

    @property
    def is_determinate(self) -> bool:
        return self.outcome_status.is_determinate

    def __post_init__(self) -> None:
        """Validate internal consistency (guards hand-construction bugs)."""

        status = self.outcome_status
        # NO_GEOMETRY / INSUFFICIENT_DATA carry no fabricated values.
        if status in (OutcomeStatus.NO_GEOMETRY, OutcomeStatus.INSUFFICIENT_DATA):
            if self.exit_price is not None:
                raise ValueError(
                    f"{status.name} must not carry a fabricated exit price.",
                )
            if self.realized_r is not None:
                raise ValueError(
                    f"{status.name} must not carry a fabricated realized R.",
                )
            if self.bars_held != 0:
                raise ValueError(
                    f"{status.name} must carry bars_held == 0.",
                )
        # BOTH_TOUCHED never fabricates a realized R or a single exit.
        if status == OutcomeStatus.BOTH_TOUCHED:
            if self.realized_r is not None:
                raise ValueError(
                    "BOTH_TOUCHED must not carry a fabricated realized R.",
                )


@dataclass(frozen=True, slots=True)
class ReplayOutcomePoint:
    """
    One point in a historical replay outcome report: the evaluation
    timestamp paired with the per-opportunity outcomes evaluated at
    that point.

    Attributes:

    evaluation_time
        The timestamp this point was evaluated at.

    outcomes
        Tuple of :class:`HistoricalOutcome`, one per eligible
        opportunity identified in the scan at this point. Empty when
        the scan surfaced no eligible opportunities.
    """

    evaluation_time: datetime
    outcomes: tuple[HistoricalOutcome, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return len(self.outcomes) == 0


@dataclass(frozen=True, slots=True)
class ReplayOutcomeReport:
    """
    The deterministic result of evaluating historical outcomes across a
    full replay.

    The report is DESCRIPTIVE. It pairs the opportunities the existing
    intelligence pipeline identified at each point in time (Sprint 11V)
    with what price did afterwards (Sprint 11W), WITHOUT rerunning the
    pipeline and WITHOUT using future information to influence the
    decisions made at ``T``.

    Attributes:

    report_id
        Deterministic report identifier (``"outcomes-"`` + sha256[:16]
        of the canonical report identity).

    instruments
        Sorted tuple of instrument names evaluated.

    timeframes
        The ``(context_timeframe, setup_timeframe)`` pair used.

    points
        Tuple of :class:`ReplayOutcomePoint`, one per evaluation
        timestamp, in order.

    rationale
        Human-readable, descriptive summary of the report.
    """

    report_id: str
    instruments: tuple[str, ...] = field(default_factory=tuple)
    timeframes: tuple[str, str] = ("", "")
    points: tuple[ReplayOutcomePoint, ...] = field(default_factory=tuple)
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether the report evaluated no points."""

        return len(self.points) == 0

    @property
    def outcome_count(self) -> int:
        """Total number of outcomes across all points."""

        return sum(len(p.outcomes) for p in self.points)


__all__ = [
    "HistoricalOutcome",
    "OutcomeStatus",
    "OutcomeSubject",
    "ReplayOutcomePoint",
    "ReplayOutcomeReport",
]
