"""
Domain models for the historical outcome-analysis boundary (Checkpoint 9.5).

Checkpoint 9.5 establishes the research-only boundary between a historical
setup candidate (observed at time ``T``) and its subsequent historical
observations. It is the forward-only observation layer of the Checkpoint 9
research pipeline:

    Phase 6C Corpus (CorpusEvaluationPoint)
                |
        Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
                |
        Checkpoint 9.5 Outcome Analysis Boundary (this layer)
                |
        Future: outcome aggregation / evidence / setup quality

Checkpoint 9.5 is RESEARCH BOUNDARY ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT an evidence / quality engine.
* It consumes a :class:`~engine.models.historical_setup_discovery.HistoricalSetupCandidate`
  (information known at ``T``) and the historical candles available AFTER
  ``T``, and produces a structured observation. It does NOT call the
  decision engine, does NOT generate trade candidates, does NOT create
  paper trades and does NOT compute outcomes, evidence or setup quality.
* The EXISTING live path, the Sprint 11W outcome layer, the Phase 6D setup
  research layer and the Checkpoint 9.1 discovery layer remain
  authoritative and are untouched by these models.

DESIGN PRINCIPLE — forward-only, no leakage (HARD REQUIREMENT):

The outcome-analysis component inspects ONLY candles with a timestamp
strictly greater than the candidate's evaluation time. The candidate
(information known at ``T``) is fixed before observation begins and is
NEVER mutated. The two concerns (what was known at ``T`` vs. what happened
afterwards) are kept strictly separate: this model carries the OBSERVATION
side only.

This boundary does NOT define winning/losing trades, targets, stops, or
setup quality. It represents ONLY the deterministic, point-in-time-safe
availability of future observations after a candidate. Future candles are
inspected solely because this is a HISTORICAL outcome-analysis layer; the
same component applied to a live stream would be structurally prevented
from seeing future data.

DESIGN PRINCIPLE — no fabricated values:

Missing / insufficient future data is reported via
:class:`ObservationStatus` — never silently treated as a determinate
outcome. An observation with no future candles receives
:attr:`INSUFFICIENT_DATA`, never a fabricated "available" status.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.ohlcv import OHLCVCandle


class ObservationStatus(Enum):
    """
    The descriptive observation status for a historical setup candidate.

    This enum is DELIBERATELY DISTINCT from Sprint 11W's
    :class:`~engine.models.historical_outcome.OutcomeStatus`
    (TARGET_HIT / STOP_HIT / BOTH_TOUCHED / EXPIRED / NO_GEOMETRY /
    INSUFFICIENT_DATA) and from Phase 6D's
    :class:`~engine.models.setup_research.SetupResearchStatus`.
    Sprint 11W describes what happened to a TRADE opportunity (entry / stop
    / target geometry); Checkpoint 9.5 describes the AVAILABILITY of future
    observations after a STRUCTURAL setup candidate (which carries no trade
    geometry) was detected.

    AVAILABLE
        At least one future candle is available after the candidate's
        evaluation time. The observation window is populated and the
        candidate's future is observable.

    INSUFFICIENT_DATA
        No future candles are available after the candidate's evaluation
        time (the forward window is empty). The observation cannot be
        formed. Never silently treated as a determinate outcome.
    """

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

    @property
    def is_available(self) -> bool:
        """Whether the observation window is populated."""
        return self is ObservationStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class SetupObservation:
    """
    ONE historical observation of a setup candidate's future.

    The observation is DESCRIPTIVE. It reports what future candles are
    available after a :class:`HistoricalSetupCandidate` was detected at
    ``T``, using ONLY candles that closed strictly after ``T``. It is NOT
    a prediction, NOT a probability of success, NOT a profitability
    guarantee and NOT a trading recommendation.

    The underlying :class:`HistoricalSetupCandidate` is retained BY
    REFERENCE and never modified.

    OBSERVATION-TIME PRICE ANCHOR (Checkpoint 9.7):

    ``reference_price`` is the observation-time market price at evaluation
    time ``T``. It is defined as the **close of the latest completed
    setup-timeframe candle at or before ``T``** — i.e. the close of the
    last candle in the corpus setup slice (which contains only candles
    with ``timestamp <= T`` by construction).

    Semantic definition:

    * **Price field used:** ``close``.
    * **Timestamp represented:** the timestamp of the last candle in the
      corpus setup slice (the latest completed setup candle at/before ``T``).
    * **Is it the close of the latest completed candle at/before ``T``?**
      Yes — by definition.
    * **Why this is point-in-time safe:** the corpus setup slice is a
      prefix of the stored series bounded by ``timestamp <= T``; no candle
      with ``timestamp > T`` can contribute to the slice, so no future
      candle influences the reference price.

    This anchor is ``None`` when the observation-time price cannot be
    determined (status :attr:`INSUFFICIENT_DATA`). It is never guessed
    or fabricated.

    Attributes:

    candidate
        The :class:`HistoricalSetupCandidate` (information known at ``T``)
        this observation was evaluated for.

    future_candles
        Candles with a timestamp strictly greater than the candidate's
        evaluation time, in chronological order. The forward-only filter
        is applied by the observation engine; this tuple contains ONLY
        strictly-after-``T`` candles.

    observation_status
        The :class:`ObservationStatus`.

    reference_price
        The observation-time market price at ``T`` (close of the latest
        completed setup candle at/before ``T``), or ``None`` when it
        cannot be determined. When
        :attr:`observation_status` is :attr:`ObservationStatus.AVAILABLE`,
        this must be a positive float. When it is
        :attr:`ObservationStatus.INSUFFICIENT_DATA`, this must be ``None``.

    reason
        Human-readable, descriptive explanation of the observation status.
    """

    candidate: HistoricalSetupCandidate
    future_candles: tuple[OHLCVCandle, ...]
    observation_status: ObservationStatus
    reference_price: float | None = None
    reason: str = ""

    @property
    def evaluation_time(self) -> datetime:
        """The candidate's evaluation timestamp (information known at T)."""
        return self.candidate.evaluation_time

    @property
    def instrument(self) -> str:
        return self.candidate.instrument

    @property
    def setup_timeframe(self) -> str:
        return self.candidate.setup_timeframe

    @property
    def context_timeframe(self) -> str:
        return self.candidate.context_timeframe

    @property
    def future_candle_count(self) -> int:
        """Number of future candles in the observation window."""
        return len(self.future_candles)

    def __post_init__(self) -> None:
        if (
            self.observation_status is ObservationStatus.AVAILABLE
            and not self.future_candles
        ):
            raise ValueError(
                "An AVAILABLE observation must carry at least one "
                "future candle."
            )
        if (
            self.observation_status is ObservationStatus.INSUFFICIENT_DATA
            and self.future_candles
        ):
            raise ValueError(
                "An INSUFFICIENT_DATA observation must carry no "
                "future candles."
            )
        if (
            self.observation_status is ObservationStatus.AVAILABLE
            and self.reference_price is None
        ):
            raise ValueError(
                "An AVAILABLE observation must carry a reference_price "
                "(the observation-time price at T)."
            )
        if (
            self.observation_status is ObservationStatus.AVAILABLE
            and self.reference_price is not None
            and self.reference_price <= 0
        ):
            raise ValueError(
                "An AVAILABLE observation must carry a positive "
                f"reference_price; got {self.reference_price}."
            )
        if (
            self.observation_status is ObservationStatus.INSUFFICIENT_DATA
            and self.reference_price is not None
        ):
            raise ValueError(
                "An INSUFFICIENT_DATA observation must NOT carry a "
                "reference_price."
            )


@dataclass(frozen=True, slots=True)
class ForwardReturnObservation:
    """
    A direction-neutral fixed-horizon forward price-return observation.

    Represents the price movement over a horizon of ``horizon_candles``
    completed future candles after a historical setup candidate observed at
    time ``T``:

        candidate observation at T (reference_price)
        -> N completed future candles
        -> price movement over that horizon

    The metric is DIRECTION-NEUTRAL. It reports the fractional price change
    from the observation-time reference price to the closing price of the
    Nth future candle. It does NOT classify the result as a win, loss,
    profitable, successful, bullish trade, or bearish trade. It is simply a
    historical forward price movement.

    ``endpoint_price`` and ``forward_return`` are ``None`` when fewer than
    ``horizon_candles`` future candles are available (status
    :attr:`ObservationStatus.INSUFFICIENT_DATA`); they are never computed
    over a partial horizon.

    Attributes:
        candidate
            The :class:`HistoricalSetupCandidate` (information known at ``T``),
            retained by reference and never mutated.
        reference_price
            The observation-time reference price (the price at ``T``).
        horizon_candles
            The requested horizon ``N`` — number of completed future candles.
        endpoint_price
            The closing price of the Nth future candle, or ``None`` when
            insufficient data.
        forward_return
            The fractional forward return
            ``(endpoint_price - reference_price) / reference_price``, or
            ``None`` when insufficient data.
        observation_status
            :attr:`ObservationStatus.AVAILABLE` when the return is computed;
            :attr:`ObservationStatus.INSUFFICIENT_DATA` when fewer than
            ``horizon_candles`` future candles are available.
        reason
            Human-readable explanation of the observation status.
    """

    candidate: HistoricalSetupCandidate
    reference_price: float
    horizon_candles: int
    endpoint_price: float | None
    forward_return: float | None
    observation_status: ObservationStatus
    reason: str = ""

    @property
    def evaluation_time(self) -> datetime:
        """The candidate's evaluation timestamp (information known at T)."""
        return self.candidate.evaluation_time

    @property
    def instrument(self) -> str:
        return self.candidate.instrument

    @property
    def setup_timeframe(self) -> str:
        return self.candidate.setup_timeframe

    @property
    def context_timeframe(self) -> str:
        return self.candidate.context_timeframe

    def __post_init__(self) -> None:
        if (
            self.observation_status is ObservationStatus.AVAILABLE
            and self.endpoint_price is None
        ):
            raise ValueError(
                "An AVAILABLE forward-return observation must carry an "
                "endpoint_price."
            )
        if (
            self.observation_status is ObservationStatus.AVAILABLE
            and self.forward_return is None
        ):
            raise ValueError(
                "An AVAILABLE forward-return observation must carry a "
                "forward_return."
            )
        if (
            self.observation_status is ObservationStatus.INSUFFICIENT_DATA
            and self.endpoint_price is not None
        ):
            raise ValueError(
                "An INSUFFICIENT_DATA forward-return observation must NOT "
                "carry an endpoint_price."
            )
        if (
            self.observation_status is ObservationStatus.INSUFFICIENT_DATA
            and self.forward_return is not None
        ):
            raise ValueError(
                "An INSUFFICIENT_DATA forward-return observation must NOT "
                "carry a forward_return."
            )


@dataclass(frozen=True, slots=True)
class PriceExcursionObservation:
    """
    A direction-neutral fixed-horizon future price-excursion observation.

    Represents the maximum price range observed over a horizon of
    ``horizon_candles`` completed future candles after a historical setup
    candidate observed at time ``T``:

        candidate observation at T (reference_price)
        -> N completed future candles
        -> maximum upward / downward price excursion over that window

    The metric is DIRECTION-NEUTRAL. It reports the maximum upward and
    downward price movements relative to the observation-time reference
    price. It does NOT classify the result as a win, loss, profitable,
    successful, bullish trade, or bearish trade. It is simply a historical
    price range observation.

    The excursions are defined as:

        max_upward_excursion = (max_high - reference_price) / reference_price
        max_downward_excursion = (min_low - reference_price) / reference_price

    where ``max_high`` is the highest high and ``min_low`` is the lowest low
    among the first ``N`` future candles (all strictly after ``T``).

    ``max_upward_excursion``, ``max_downward_excursion``, ``max_high`` and
    ``min_low`` are ``None`` when fewer than ``horizon_candles`` future
    candles are available (status :attr:`ObservationStatus.INSUFFICIENT_DATA`);
    they are never computed over a partial window.

    Attributes:
        candidate
            The :class:`HistoricalSetupCandidate` (information known at ``T``),
            retained by reference and never mutated.
        reference_price
            The observation-time reference price (the price at ``T``).
        horizon_candles
            The requested horizon ``N`` — number of completed future candles.
        max_upward_excursion
            ``(max_high - reference_price) / reference_price``, or ``None``
            when insufficient data.
        max_downward_excursion
            ``(min_low - reference_price) / reference_price``, or ``None``
            when insufficient data.
        max_high
            The highest high over the window, or ``None`` when insufficient
            data.
        min_low
            The lowest low over the window, or ``None`` when insufficient
            data.
        observation_status
            :attr:`ObservationStatus.AVAILABLE` when the excursions are
            computed; :attr:`ObservationStatus.INSUFFICIENT_DATA` when fewer
            than ``horizon_candles`` future candles are available.
        reason
            Human-readable explanation of the observation status.
    """

    candidate: HistoricalSetupCandidate
    reference_price: float
    horizon_candles: int
    max_upward_excursion: float | None
    max_downward_excursion: float | None
    max_high: float | None
    min_low: float | None
    observation_status: ObservationStatus
    reason: str = ""

    @property
    def evaluation_time(self) -> datetime:
        """The candidate's evaluation timestamp (information known at T)."""
        return self.candidate.evaluation_time

    @property
    def instrument(self) -> str:
        return self.candidate.instrument

    @property
    def setup_timeframe(self) -> str:
        return self.candidate.setup_timeframe

    @property
    def context_timeframe(self) -> str:
        return self.candidate.context_timeframe

    def __post_init__(self) -> None:
        if self.observation_status is ObservationStatus.AVAILABLE:
            if self.max_upward_excursion is None:
                raise ValueError(
                    "An AVAILABLE price-excursion observation must carry "
                    "a max_upward_excursion."
                )
            if self.max_downward_excursion is None:
                raise ValueError(
                    "An AVAILABLE price-excursion observation must carry "
                    "a max_downward_excursion."
                )
            if self.max_high is None:
                raise ValueError(
                    "An AVAILABLE price-excursion observation must carry "
                    "a max_high."
                )
            if self.min_low is None:
                raise ValueError(
                    "An AVAILABLE price-excursion observation must carry "
                    "a min_low."
                )
        if self.observation_status is ObservationStatus.INSUFFICIENT_DATA:
            if self.max_upward_excursion is not None:
                raise ValueError(
                    "An INSUFFICIENT_DATA price-excursion observation must "
                    "NOT carry a max_upward_excursion."
                )
            if self.max_downward_excursion is not None:
                raise ValueError(
                    "An INSUFFICIENT_DATA price-excursion observation must "
                    "NOT carry a max_downward_excursion."
                )
            if self.max_high is not None:
                raise ValueError(
                    "An INSUFFICIENT_DATA price-excursion observation must "
                    "NOT carry a max_high."
                )
            if self.min_low is not None:
                raise ValueError(
                    "An INSUFFICIENT_DATA price-excursion observation must "
                    "NOT carry a min_low."
                )


__all__ = [
    "ForwardReturnObservation",
    "ObservationStatus",
    "PriceExcursionObservation",
    "SetupObservation",
]
