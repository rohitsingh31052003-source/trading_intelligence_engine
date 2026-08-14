"""
Domain model for the end-to-end historical evaluation pipeline
(Sprint 11F).

The pipeline result is an immutable snapshot of everything that
happened when a historical candle sequence was evaluated through
the full intelligence stack:

    Analysis
      -> Structure
      -> Liquidity
      -> Confluence
      -> Decision
      -> Signal
      -> Validation
      -> Performance Analytics

The model deliberately does NOT duplicate the internals of every
engine. It stores the structured results each engine already
produces, plus a compact set of counters that make debugging the
walk-forward behaviour straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.models.candle_pattern import CandlePattern
from engine.models.market_context import MarketContext
from engine.models.opportunity import TradeOpportunity
from engine.models.performance import PerformanceAnalytics
from engine.models.setup_confluence import SetupAssessment
from engine.models.signal import SignalResult
from engine.models.trade_candidate import TradeCandidate
from engine.models.trade_decision import TradeDecision
from engine.models.validation import ValidationResult


@dataclass(frozen=True, slots=True)
class PipelineEvaluationPoint:
    """
    One evaluation point in the walk-forward sequence.

    Captures what the pipeline produced at a single historical
    index ``T`` using only candles up to and including ``T``.

    A point may produce no decision, a non-eligible decision, or
    an eligible signal. Eligible signals are forwarded to the
    validation engine and recorded here.
    """

    index: int

    timestamp: Any | None

    decision_direction: str

    decision_status: str

    signal_state: str

    signal: SignalResult | None

    validation: ValidationResult | None

    reason: str = ""

    suppressed: bool = False

    # Candle / price-action patterns whose triggering candle is
    # at this evaluation index, computed from candles[:T+1].
    # Additive evidence only; it is NOT consumed by the existing
    # confluence/decision/signal logic, so existing behaviour is
    # preserved.
    patterns: tuple[CandlePattern, ...] = field(
        default_factory=tuple,
    )

    # Market context (Sprint 11P): descriptive price-structure
    # intelligence (trend / range / support-resistance context)
    # computed from candles[:T+1]. Additive evidence only; it is NOT
    # consumed by the existing confluence/decision/signal logic, so
    # existing signal / trade behaviour is preserved. ``None`` when no
    # market-context engine was configured or the point was produced
    # before the integration.
    market_context: MarketContext | None = None

    # Setup / confluence assessment (Sprint 11Q): descriptive
    # combination of the candle-pattern evidence (Sprint 11O) and
    # market-context evidence (Sprint 11P) into a single structured
    # setup view. Additive evidence only; it is NOT consumed by the
    # existing confluence/decision/signal logic, so existing signal /
    # trade behaviour is preserved. ``None`` when no setup-confluence
    # engine was configured or the point was produced before the
    # integration.
    setup_assessment: SetupAssessment | None = None

    # Trade candidate (Sprint 11R): a structured, descriptive trade
    # candidate derived from the candle-pattern evidence (Sprint 11O),
    # market-context evidence (Sprint 11P) and setup/confluence
    # assessment (Sprint 11Q). Additive evidence only; it is NOT
    # consumed by the existing confluence/decision/signal logic, so
    # existing signal / trade behaviour is preserved. ``None`` when no
    # trade-candidate engine was configured or the point was produced
    # before the integration. A trade candidate is NOT a trade signal.
    trade_candidate: TradeCandidate | None = None

    # Trade decision (Sprint 11S): a deterministic, descriptive
    # decision over the Sprint 11R trade candidate, expressing the
    # strength/completeness of the available technical evidence as a
    # ``Decision Score`` and a decision classification
    # (REJECTED / WATCH / QUALIFIED / PREFERRED). Additive evidence
    # only; it is NOT consumed by the existing confluence/decision/
    # signal logic, so existing signal / trade behaviour is preserved.
    # ``None`` when no trade-decision engine was configured or the
    # point was produced before the integration. A trade decision is
    # NOT a trade signal, NOT a probability, and NOT a guarantee of
    # profitability.
    trade_decision: TradeDecision | None = None

    # Trade opportunity (Sprint 11T): a deterministic, descriptive
    # trade opportunity view over the Sprint 11S trade decision,
    # expressing whether the candidate should be surfaced as the best
    # available trade opportunity at this evaluation point, and its
    # rank/status (BEST_OPPORTUNITY / ALTERNATIVE_OPPORTUNITY / WATCH /
    # NO_OPPORTUNITY). Additive evidence only; it is NOT consumed by
    # the existing confluence/decision/signal logic, so existing signal
    # / trade behaviour is preserved. ``None`` when no
    # trade-opportunity engine was configured, when no trade decision
    # exists at this point, or the point was produced before the
    # integration. A trade opportunity is NOT a trade signal, NOT a
    # probability, and NOT a guarantee of profitability.
    trade_opportunity: TradeOpportunity | None = None

    @property
    def produced_signal(self) -> bool:
        return self.signal is not None

    @property
    def eligible(self) -> bool:
        return self.signal is not None and self.signal.eligible

    @property
    def validated(self) -> bool:
        return self.validation is not None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """
    Immutable result of ``HistoricalEvaluationPipeline.evaluate``.

    The counts expose the full walk-forward funnel so downstream
    code can answer:

        "What happened to the historical candle sequence?"

    Count semantics:

    candles_processed
        Total number of candles supplied to the pipeline.

    evaluation_points
        Number of indices that were actually evaluated. Indices
        skipped due to insufficient history are not counted.

    decisions_generated
        Number of evaluation points that reached the decision
        engine. Every evaluation point generates a decision
        (possibly NOT_READY), so this equals
        ``evaluation_points``.

    eligible_decisions
        Decisions whose downstream signal became eligible.

    signals_generated
        Number of eligible signals produced.

    signals_validated
        Number of signals that were forwarded to the validation
        engine. Under the one-active-signal policy this is <=
        ``signals_generated`` only when signals are suppressed
        by an active validation; otherwise it matches
        ``signals_generated``.

    completed_trades
        Signals that resolved to a terminal WIN or LOSS outcome.

    The result also retains the engine outputs and the aggregate
    ``PerformanceAnalytics`` produced by the existing
    performance engine.
    """

    # -----------------------------------------------------
    # COUNTS / FUNNEL
    # -----------------------------------------------------

    candles_processed: int
    evaluation_points: int
    decisions_generated: int
    eligible_decisions: int
    signals_generated: int
    signals_validated: int
    completed_trades: int

    # -----------------------------------------------------
    # ENGINE OUTPUTS
    # -----------------------------------------------------

    evaluation_points_sequence: tuple[PipelineEvaluationPoint, ...] = field(
        default_factory=tuple,
    )

    signals: tuple[SignalResult, ...] = field(
        default_factory=tuple,
    )

    validation_results: tuple[ValidationResult, ...] = field(
        default_factory=tuple,
    )

    # Every candle pattern detected across the walk-forward
    # evaluation. Each pattern is attributed to the index of its
    # triggering candle and was computed using only
    # candles[:index+1]. Additive evidence; not consumed by the
    # existing signal/decision logic.
    patterns: tuple[CandlePattern, ...] = field(
        default_factory=tuple,
    )

    performance: PerformanceAnalytics | None = None

    # -----------------------------------------------------
    # HELPERS
    # -----------------------------------------------------

    @property
    def has_signals(self) -> bool:
        return self.signals_generated > 0

    @property
    def has_performance(self) -> bool:
        return self.performance is not None

    def validated_points(self) -> tuple[PipelineEvaluationPoint, ...]:
        return tuple(p for p in self.evaluation_points_sequence if p.validated)
