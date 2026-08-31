"""
Historical outcome-analysis boundary (Checkpoint 9.5).

This module defines the research-layer boundary for historical outcome
analysis. It sits between the Checkpoint 9.1 setup discovery layer and
future outcome aggregation / evidence / setup quality layers.

    Phase 6C Corpus (CorpusEvaluationPoint)
                |
        Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
                |
        Checkpoint 9.5 Outcome Analysis Boundary (this layer)
                |
        Future: outcome aggregation / evidence / setup quality

Checkpoint 9.5 is BOUNDARY ONLY:

* It defines the protocol an outcome-analysis component must implement.
* It provides a minimal deterministic implementation suitable for
  testing and as a placeholder for future analyzers.
* It does NOT implement BUY/SELL signals, order generation, execution
  logic, outcome metrics, scoring/ranking, machine learning or a large
  generic outcome-analysis engine.
* It consumes a :class:`~engine.models.historical_setup_discovery.HistoricalSetupCandidate`
  (information known at ``T``) and the historical candles available AFTER
  ``T``, and produces a structured observation.

POINT-IN-TIME: the component operates on an already-detected candidate and
the candles available after its evaluation time. No future candle leakage
is possible because the candidate represents information known at ``T`` and
the observation engine inspects ONLY candles with a timestamp strictly
greater than ``T``. The candidate is NEVER mutated.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.historical_setup_outcome import MinimalObservationEngine
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from engine.models.historical_setup_discovery import HistoricalSetupCandidate
from engine.models.historical_setup_outcome import (
    ForwardReturnObservation,
    ObservationStatus,
    SetupObservation,
)
from engine.models.ohlcv import OHLCVCandle


@runtime_checkable
class HistoricalOutcomeAnalysisProtocol(Protocol):
    """
    Domain-facing contract for historical outcome analysis.

    An outcome-analysis component accepts a historical setup candidate
    (observed at time ``T``) and the historical candles available after
    ``T``, and returns a structured observation. The contract guarantees:

    * Deterministic output for the same input.
    * No trading or execution behavior is invoked.
    * No future-candle leakage (only candles strictly after ``T`` are
      inspected; the candidate is never mutated).
    """

    def observe(
        self,
        candidate: HistoricalSetupCandidate,
        future_candles: Sequence[OHLCVCandle],
    ) -> SetupObservation: ...


class MinimalObservationEngine:
    """
    Minimal deterministic outcome-analysis engine (Checkpoint 9.5).

    This engine implements the :class:`HistoricalOutcomeAnalysisProtocol`
    with the smallest possible deterministic logic: it filters the supplied
    candles to those strictly after the candidate's evaluation time and
    reports whether the forward observation window is populated.

    The engine NEVER mutates the candidate. It inspects ONLY candles with
    a timestamp strictly greater than the candidate's evaluation time.
    When the forward window is empty, the observation carries
    :attr:`~engine.models.historical_setup_outcome.ObservationStatus.INSUFFICIENT_DATA`;
    when at least one future candle is available, it carries
    :attr:`~engine.models.historical_setup_outcome.ObservationStatus.AVAILABLE`.

    This is a placeholder implementation — future checkpoints can replace
    the engine with a real outcome analyzer without changing the protocol
    boundary.
    """

    def observe(
        self,
        candidate: HistoricalSetupCandidate,
        future_candles: Sequence[OHLCVCandle],
    ) -> SetupObservation:
        """
        Observe the future of ``candidate`` using only the supplied
        ``future_candles`` (candles available after the candidate's
        evaluation time).

        The engine filters the supplied candles to those with a timestamp
        strictly greater than the candidate's evaluation time. When the
        forward window is empty, the observation carries
        :attr:`ObservationStatus.INSUFFICIENT_DATA`. When at least one
        future candle is available, the observation carries
        :attr:`ObservationStatus.AVAILABLE`.

        The result is deterministic and descriptive. The candidate is
        NEVER mutated.
        """

        evaluation_time = candidate.evaluation_time
        forward = tuple(
            c for c in future_candles if c.timestamp > evaluation_time
        )

        if not forward:
            return SetupObservation(
                candidate=candidate,
                future_candles=forward,
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
                reason=(
                    "No future candles are available after the evaluation "
                    "time. The observation cannot be formed."
                ),
            )

        return SetupObservation(
            candidate=candidate,
            future_candles=forward,
            observation_status=ObservationStatus.AVAILABLE,
            reason=(
                f"{len(forward)} future candle(s) available after the "
                "evaluation time."
            ),
        )


__all__ = [
    "ForwardReturnEngine",
    "ForwardReturnProtocol",
    "HistoricalOutcomeAnalysisProtocol",
    "MinimalObservationEngine",
]


@runtime_checkable
class ForwardReturnProtocol(Protocol):
    """
    Domain-facing contract for the fixed-horizon forward-return metric.

    A forward-return component consumes a :class:`SetupObservation`
    (produced by the Checkpoint 9.5 observation engine) together with an
    observation-time reference price and a horizon ``N``, and returns a
    direction-neutral :class:`ForwardReturnObservation`.

    The contract guarantees:

    * Deterministic output for the same input.
    * The candidate is retained by reference and never mutated.
    * No trading or execution behavior is invoked.
    * No future-candle leakage: the endpoint candle is one of the
      already-observed future candles (all strictly after ``T``).
    """

    def observe_forward_return(
        self,
        observation: SetupObservation,
        reference_price: float,
        horizon_candles: int,
    ) -> ForwardReturnObservation: ...


class ForwardReturnEngine:
    """
    Minimal deterministic fixed-horizon forward-return engine (Checkpoint 9.6).

    This engine implements the :class:`ForwardReturnProtocol`. It consumes a
    :class:`SetupObservation` (Checkpoint 9.5) and computes a
    direction-neutral fractional price return over the first ``N`` completed
    future candles:

        forward_return = (endpoint_price - reference_price) / reference_price

    where ``endpoint_price`` is the close of the Nth future candle (the
    candle at index ``N-1`` in the observation's ``future_candles`` tuple,
    which is strictly after the candidate's evaluation time by construction).

    The metric is DIRECTION-NEUTRAL: it reports a historical price movement,
    not a win/loss, not a trade, not a classification.

    The reference price is the observation-time price (the price ``at T``).
    It is supplied explicitly by the caller and is not derived from any
    candle slice — this keeps the metric free of data-slicing mechanics and
    makes the reference point auditable.

    If fewer than ``horizon_candles`` future candles are available, the
    observation carries :attr:`ObservationStatus.INSUFFICIENT_DATA` and no
    partial-horizon return is computed. ``horizon_candles`` must be positive;
    a zero or negative horizon yields
    :attr:`ObservationStatus.INSUFFICIENT_DATA`.
    """

    def observe_forward_return(
        self,
        observation: SetupObservation,
        reference_price: float,
        horizon_candles: int,
    ) -> ForwardReturnObservation:
        """
        Compute the direction-neutral forward return for ``observation``.

        The reference price is the observation-time price (price at ``T``),
        supplied explicitly by the caller. The endpoint price is the close
        of the Nth future candle. The horizon ``N`` must be positive.

        If ``horizon_candles`` is not positive, or fewer than
        ``horizon_candles`` future candles are available, the result carries
        :attr:`ObservationStatus.INSUFFICIENT_DATA` and no return is
        computed.

        The candidate is retained by reference and never mutated.
        """

        if horizon_candles < 1:
            return ForwardReturnObservation(
                candidate=observation.candidate,
                reference_price=reference_price,
                horizon_candles=horizon_candles,
                endpoint_price=None,
                forward_return=None,
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
                reason=(
                    "Horizon must be a positive integer; got "
                    f"{horizon_candles}. No forward return computed."
                ),
            )

        if observation.future_candle_count < horizon_candles:
            return ForwardReturnObservation(
                candidate=observation.candidate,
                reference_price=reference_price,
                horizon_candles=horizon_candles,
                endpoint_price=None,
                forward_return=None,
                observation_status=ObservationStatus.INSUFFICIENT_DATA,
                reason=(
                    f"Only {observation.future_candle_count} future candle(s) "
                    f"available; {horizon_candles} required. No partial-"
                    "horizon return computed."
                ),
            )

        endpoint_candle = observation.future_candles[horizon_candles - 1]
        endpoint_price = endpoint_candle.close
        forward_return = (endpoint_price - reference_price) / reference_price

        return ForwardReturnObservation(
            candidate=observation.candidate,
            reference_price=reference_price,
            horizon_candles=horizon_candles,
            endpoint_price=endpoint_price,
            forward_return=forward_return,
            observation_status=ObservationStatus.AVAILABLE,
            reason=(
                f"Forward return over {horizon_candles} candle(s): "
                f"reference={reference_price}, endpoint={endpoint_price}."
            ),
        )
