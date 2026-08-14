"""
Historical evaluation pipeline (Sprint 11F).

The ``HistoricalEvaluationPipeline`` connects the existing
intelligence engines into a single deterministic, walk-forward
evaluation of a historical candle sequence:

    OHLC candles
        ↓
    Analysis   (swings -> structure -> structure analysis)
        ↓
    Structure  (BOS / CHOCH / trend)
        ↓
    Liquidity  (pools -> events)
        ↓
    Confluence
        ↓
    Decision
        ↓
    Signal
        ↓   (future candles only)
    Validation
        ↓
    Performance Analytics

Design rules honoured by this module:

* No look-ahead bias.
  At evaluation point ``T`` only ``candles[:T+1]`` is fed to
  every analysis/structure/liquidity/confluence/decision/signal
  engine. Validation receives only ``candles[T+1:]``.

* One active signal at a time.
  When an eligible signal is generated it is validated
  immediately over its future window. The next evaluation point
  that may produce a new signal is advanced to
  ``T + 1 + validation.candles_evaluated`` so two validations
  never overlap. This is a documented deterministic policy.

* Not every candle produces a signal.
  Non-eligible decisions and suppressed (overlapping) signals
  simply continue to the next evaluation point.

* Insufficient history is skipped, never raised.
  Evaluation begins only after a configurable minimum history.

* Input is never mutated.
  Slicing produces new lists; candles are immutable dataclasses.

* Performance statistics are delegated.
  The pipeline forwards completed ``ValidationResult`` objects
  to the existing ``PerformanceAnalyticsEngine``.

The individual intelligence engines are NOT modified or
rewrapped. The only adaptation performed here is converting the
real engine result objects into the lightweight attribute views
the confluence engine reads (it inspects ``analysis.bias``,
``bos.type`` and ``choch.type`` via ``getattr``). This is pure
orchestration glue, not engine redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from engine.config.liquidity_config import LiquidityConfig
from engine.config.liquidity_event_config import (
    LiquidityEventConfig,
)
from engine.config.swing_config import SwingConfig
from engine.config.candle_pattern_config import (
    CandlePatternConfig,
)
from engine.config.market_context_config import MarketContextConfig
from engine.config.setup_confluence_config import SetupConfluenceConfig
from engine.config.trade_candidate_config import TradeCandidateConfig
from engine.config.trade_decision_config import TradeDecisionConfig
from engine.config.trade_opportunity_config import TradeOpportunityConfig
from engine.intelligence.bos import BOSEngine
from engine.intelligence.choch import CHOCHEngine
from engine.intelligence.confluence import ConfluenceEngine
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.decision import DecisionEngine
from engine.intelligence.liquidity import LiquidityEngine
from engine.intelligence.liquidity_event import (
    LiquidityEventEngine,
)
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.intelligence.trade_opportunity import TradeOpportunityEngine
from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.signal import (
    SignalContext,
    SignalEngine,
)
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import (
    StructureAnalysisEngine,
)
from engine.intelligence.swings import SwingEngine
from engine.intelligence.trend import TrendEngine
from engine.intelligence.validation import (
    SignalValidationEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import (
    PipelineEvaluationPoint,
    PipelineResult,
)
from engine.models.signal import SignalState
from engine.models.structure_analysis import StructureBias
from engine.models.validation import ValidationStatus


# ============================================================
# ADAPTER VIEWS
# ============================================================
#
# The confluence engine reads directional attributes defensively
# via getattr: ``analysis.bias``, ``bos.type`` and
# ``choch.type``. The real engine result models expose
# ``current_bias``, ``bos_type`` and ``choch_type`` instead.
#
# Rather than modify the confluence engine (which is out of
# scope for an integration sprint), the pipeline constructs tiny
# immutable views that expose the attributes the downstream
# engine expects. This keeps every engine unchanged while still
# wiring the real models together end-to-end.


@dataclass(frozen=True, slots=True)
class _AnalysisView:
    """
    Attribute view of ``StructureAnalysis`` for the confluence
    engine.

    Exposes ``bias`` (mapped from ``current_bias``) so the
    confluence engine's ``getattr(analysis, "bias", None)``
    lookup resolves to a directional value.
    """

    bias: StructureBias


@dataclass(frozen=True, slots=True)
class _BOSView:
    """Attribute view of ``BOSResult`` exposing ``type``."""

    detected: bool
    type: Any


@dataclass(frozen=True, slots=True)
class _CHOCHView:
    """Attribute view of ``CHOCHResult`` exposing ``type``."""

    detected: bool
    type: Any


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """
    Configuration for ``HistoricalEvaluationPipeline``.

    All thresholds live here; no magic numbers are embedded in
    the orchestration logic.
    """

    # Minimum number of candles that must exist before the
    # first evaluation point is considered. This guarantees the
    # swing engine has enough confirmed swings to produce
    # meaningful structure.
    min_history: int = 10

    # Maximum number of future candles the validation engine may
    # inspect for each signal. ``None`` defers to the validation
    # engine's own default (50).
    max_validation_candles: int | None = None

    # When True, a chronological ordering check is performed on
    # the input candles. The pipeline assumes oldest -> newest;
    # it never re-sorts.
    enforce_chronological_order: bool = True

    # Engine configurations are passed straight through to the
    # existing engines.
    swing_config: SwingConfig = field(default_factory=SwingConfig)
    liquidity_config: LiquidityConfig = field(
        default_factory=LiquidityConfig,
    )
    liquidity_event_config: LiquidityEventConfig = field(
        default_factory=LiquidityEventConfig,
    )

    # Candle / price-action pattern configuration (Sprint 11O).
    # The pattern engine runs additively: its evidence is
    # attached to each evaluation point but is NOT fed into the
    # existing confluence/decision/signal logic, so existing
    # behaviour is preserved.
    candle_pattern_config: CandlePatternConfig = field(
        default_factory=CandlePatternConfig,
    )

    # Market context configuration (Sprint 11P).
    # ``enable_market_context`` toggles the additive market-context
    # intelligence. When enabled, a descriptive ``MarketContext``
    # (trend / range / support-resistance) is computed from
    # candles[:T+1] and attached to each evaluation point. It is NOT
    # fed into the existing confluence/decision/signal logic, so
    # existing signal / trade behaviour is preserved. Disabling it
    # reproduces the pre-11P pipeline exactly (market_context=None).
    enable_market_context: bool = True
    market_context_config: MarketContextConfig = field(
        default_factory=MarketContextConfig,
    )

    # Setup / confluence configuration (Sprint 11Q).
    # ``enable_setup_confluence`` toggles the additive setup assessment.
    # When enabled, a descriptive ``SetupAssessment`` is computed by
    # combining the candle-pattern evidence (Sprint 11O) and the
    # market-context evidence (Sprint 11P) computed from
    # candles[:T+1]. It is NOT fed into the existing
    # confluence/decision/signal logic, so existing signal / trade
    # behaviour is preserved. Disabling it reproduces the pre-11Q
    # pipeline exactly (setup_assessment=None).
    enable_setup_confluence: bool = True
    setup_confluence_config: SetupConfluenceConfig = field(
        default_factory=SetupConfluenceConfig,
    )

    # Trade candidate configuration (Sprint 11R).
    # ``enable_trade_candidates`` toggles the additive trade-candidate
    # generation. When enabled, a descriptive ``TradeCandidate`` is
    # derived from the candle-pattern evidence (Sprint 11O), the
    # market-context evidence (Sprint 11P) and the setup/confluence
    # assessment (Sprint 11Q), all computed from candles[:T+1]. The
    # trade-candidate engine reads no candles directly (only the
    # trigger close price as a scalar), so it cannot introduce
    # look-ahead bias. It is NOT fed into the existing
    # confluence/decision/signal logic, so existing signal / trade
    # behaviour is preserved. Disabling it reproduces the pre-11R
    # pipeline exactly (trade_candidate=None). A trade candidate is
    # NOT a trade signal.
    enable_trade_candidates: bool = True
    trade_candidate_config: TradeCandidateConfig = field(
        default_factory=TradeCandidateConfig,
    )

    # Trade decision configuration (Sprint 11S).
    # ``enable_trade_decision`` toggles the additive trade-decision /
    # ranking layer. When enabled, a descriptive ``TradeDecision`` is
    # produced from the Sprint 11R trade candidate (which is itself
    # derived from candles[:T+1]). The decision engine reads only the
    # already-computed candidate; it reads no candles directly, so it
    # cannot introduce look-ahead bias. It is NOT fed into the existing
    # confluence/decision/signal logic, so existing signal / trade
    # behaviour is preserved. Disabling it reproduces the pre-11S
    # pipeline exactly (trade_decision=None). A trade decision is NOT
    # a trade signal, NOT a probability, and NOT a guarantee of
    # profitability.
    enable_trade_decision: bool = True
    trade_decision_config: TradeDecisionConfig = field(
        default_factory=TradeDecisionConfig,
    )

    # Trade opportunity configuration (Sprint 11T).
    # ``enable_trade_opportunity`` toggles the additive trade-opportunity
    # filter / ranking layer. When enabled, a descriptive
    # ``TradeOpportunity`` is produced from the Sprint 11S trade
    # decision (which is itself derived from candles[:T+1]). The
    # opportunity engine reads only the already-computed trade decision;
    # it reads no candles directly, so it cannot introduce look-ahead
    # bias. It is NOT fed into the existing confluence/decision/signal
    # logic, so existing signal / trade behaviour is preserved.
    # Disabling it reproduces the pre-11T pipeline exactly
    # (trade_opportunity=None). A trade opportunity is NOT a trade
    # signal, NOT a probability, and NOT a guarantee of profitability.
    enable_trade_opportunity: bool = True
    trade_opportunity_config: TradeOpportunityConfig = field(
        default_factory=TradeOpportunityConfig,
    )


# ============================================================
# PIPELINE
# ============================================================


class HistoricalEvaluationPipeline:
    """
    Orchestrate the existing intelligence engines into a single
    deterministic, walk-forward historical evaluation.

    Public API:

        evaluate(candles) -> PipelineResult

    The pipeline is stateless across calls: identical inputs
    always produce identical outputs.
    """

    def __init__(
        self,
        config: PipelineConfig | None = None,
    ) -> None:

        self.config = config or PipelineConfig()

        # Existing engines are constructed once and reused.
        self._swing_engine = SwingEngine(self.config.swing_config)
        self._structure_engine = MarketStructureEngine()
        self._structure_analysis_engine = StructureAnalysisEngine()
        self._bos_engine = BOSEngine()
        self._choch_engine = CHOCHEngine()
        self._trend_engine = TrendEngine()
        self._liquidity_engine = LiquidityEngine(
            self.config.liquidity_config,
        )
        self._liquidity_event_engine = LiquidityEventEngine(
            self.config.liquidity_event_config,
        )
        self._confluence_engine = ConfluenceEngine()
        self._decision_engine = DecisionEngine()
        self._signal_engine = SignalEngine()
        self._validation_engine = SignalValidationEngine()
        self._performance_engine = PerformanceAnalyticsEngine()
        self._pattern_engine = CandlePatternEngine(
            self.config.candle_pattern_config,
        )
        # Sprint 11P market-context intelligence. Constructed only
        # when enabled; otherwise ``None`` and every evaluation point
        # carries ``market_context=None`` (exact pre-11P behaviour).
        self._market_context_engine: MarketContextEngine | None = (
            MarketContextEngine(
                config=self.config.market_context_config,
                swing_config=self.config.swing_config,
            )
            if self.config.enable_market_context
            else None
        )
        # Sprint 11Q setup/confluence intelligence. Constructed only
        # when enabled; otherwise ``None`` and every evaluation point
        # carries ``setup_assessment=None`` (exact pre-11Q behaviour).
        # The engine consumes the already-computed pattern + market
        # context evidence; it reads no candles directly, so it cannot
        # introduce look-ahead bias and does not alter the existing
        # confluence/decision/signal flow.
        self._setup_confluence_engine: SetupConfluenceEngine | None = (
            SetupConfluenceEngine(
                config=self.config.setup_confluence_config,
            )
            if self.config.enable_setup_confluence
            else None
        )
        # Sprint 11R trade-candidate generation. Constructed only when
        # enabled; otherwise ``None`` and every evaluation point
        # carries ``trade_candidate=None`` (exact pre-11R behaviour).
        # The engine consumes the already-computed setup assessment +
        # market context + the trigger close (a scalar); it reads no
        # candles directly, so it cannot introduce look-ahead bias and
        # does not alter the existing confluence/decision/signal flow.
        self._trade_candidate_engine: TradeCandidateEngine | None = (
            TradeCandidateEngine(
                config=self.config.trade_candidate_config,
            )
            if self.config.enable_trade_candidates
            else None
        )
        # Sprint 11S trade-decision / ranking layer. Constructed only
        # when enabled; otherwise ``None`` and every evaluation point
        # carries ``trade_decision=None`` (exact pre-11S behaviour).
        # The engine consumes the already-computed trade candidate
        # (derived from candles[:t+1]); it reads no candles directly,
        # so it cannot introduce look-ahead bias and does not alter the
        # existing confluence/decision/signal flow.
        self._trade_decision_engine: TradeDecisionEngine | None = (
            TradeDecisionEngine(
                config=self.config.trade_decision_config,
            )
            if self.config.enable_trade_decision
            else None
        )
        # Sprint 11T trade-opportunity filter / ranking layer.
        # Constructed only when enabled; otherwise ``None`` and every
        # evaluation point carries ``trade_opportunity=None`` (exact
        # pre-11T behaviour). The engine consumes the already-computed
        # trade decision (derived from candles[:t+1]); it reads no
        # candles directly, so it cannot introduce look-ahead bias and
        # does not alter the existing confluence/decision/signal flow.
        self._trade_opportunity_engine: TradeOpportunityEngine | None = (
            TradeOpportunityEngine(
                config=self.config.trade_opportunity_config,
            )
            if self.config.enable_trade_opportunity
            else None
        )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def evaluate(
        self,
        candles: Iterable[OHLCVCandle],
    ) -> PipelineResult:
        """
        Evaluate a chronological candle sequence end-to-end.

        Returns an immutable ``PipelineResult``.
        """

        # Defensive copy: never mutate the caller's collection.
        # Candles themselves are immutable frozen dataclasses.
        history = list(candles)

        total = len(history)

        if total == 0:
            return self._empty_result(total)

        if self.config.enforce_chronological_order:
            self._check_chronological(history)

        points: list[PipelineEvaluationPoint] = []
        signals: list = []
        validations: list = []
        all_patterns: list = []

        eligible_decisions = 0
        signals_generated = 0
        signals_validated = 0
        completed_trades = 0

        # The next index at which a new signal may be generated.
        # This implements the "one active signal at a time"
        # policy: while a validation window is active, new
        # eligible signals are suppressed.
        next_signal_index = 0

        for t in range(self._first_evaluation_index(), total):

            decision_direction = "UNKNOWN"
            decision_status = "NOT_READY"
            signal_state = SignalState.NO_SIGNAL.name
            signal = None
            validation = None
            reason = ""

            # ---------------------------------------------
            # WALK-FORWARD ANALYSIS (no look-ahead)
            #
            # Every analysis/structure/liquidity/confluence/
            # decision/signal engine receives only the slice
            # candles[:t+1].
            # ---------------------------------------------

            visible = history[: t + 1]

            trigger_candle = visible[-1]

            # ---------------------------------------------
            # CANDLE / PRICE-ACTION PATTERNS (Sprint 11O)
            #
            # Additive evidence only. Computed from the visible
            # slice (candles[:t+1]) and attached to this point.
            # The patterns attributed to index t use only
            # candles[t-1] and candles[t]; no future candle is
            # read. This evidence is NOT fed into the confluence
            # /decision/signal engines below, so existing signal
            # behaviour is unchanged.
            # ---------------------------------------------
            detected = self._pattern_engine.detect(visible)
            patterns_at_t = tuple(
                p for p in detected if p.index == t
            )
            all_patterns.extend(patterns_at_t)

            # ---------------------------------------------
            # MARKET CONTEXT (Sprint 11P)
            #
            # Additive evidence only. Computed from the visible slice
            # (candles[:t+1]) and attached to this point. It is NOT
            # fed into the confluence/decision/signal engines below,
            # so existing signal behaviour is unchanged. The context
            # at t depends only on candles[:t+1] (the swing engine
            # confirms a swing only after its right-side candles are
            # present), so no future-confirmed structure can leak in.
            # ---------------------------------------------
            market_context_at_t = (
                self._market_context_engine.analyze_at(visible, t)
                if self._market_context_engine is not None
                else None
            )

            # ---------------------------------------------
            # SETUP / CONFLUENCE ASSESSMENT (Sprint 11Q)
            #
            # Additive evidence only. Computed by combining the
            # candle-pattern evidence (patterns_at_t) and the
            # market-context evidence (market_context_at_t), both of
            # which are already derived from candles[:t+1]. The setup
            # engine reads no candles directly, so it cannot introduce
            # look-ahead bias. It is NOT fed into the
            # confluence/decision/signal engines below, so existing
            # signal / trade behaviour is unchanged.
            # ---------------------------------------------
            setup_assessment_at_t = (
                self._setup_confluence_engine.assess(
                    patterns=patterns_at_t,
                    market_context=market_context_at_t,
                    index=t,
                    timestamp=trigger_candle.timestamp,
                )
                if self._setup_confluence_engine is not None
                else None
            )

            # ---------------------------------------------
            # TRADE CANDIDATE (Sprint 11R)
            #
            # Additive evidence only. Derived from the already-computed
            # setup assessment + market context (both from
            # candles[:t+1]) plus the trigger close (a scalar from
            # candle t). The candidate engine reads no candles
            # directly, so it cannot introduce look-ahead bias. It is
            # NOT fed into the confluence/decision/signal engines
            # below, so existing signal / trade behaviour is unchanged.
            # ---------------------------------------------
            trade_candidate_at_t = (
                self._trade_candidate_engine.generate(
                    assessment=setup_assessment_at_t,
                    market_context=market_context_at_t,
                    index=t,
                    timestamp=trigger_candle.timestamp,
                    close_price=trigger_candle.close,
                )
                if self._trade_candidate_engine is not None
                else None
            )

            # ---------------------------------------------
            # TRADE DECISION (Sprint 11S)
            #
            # Additive evidence only. Derived from the already-computed
            # trade candidate (itself from candles[:t+1]). The decision
            # engine reads no candles directly, so it cannot introduce
            # look-ahead bias. It is NOT fed into the
            # confluence/decision/signal engines below, so existing
            # signal / trade behaviour is unchanged. Only produced when
            # a trade candidate exists (a None candidate carries no
            # decision). A trade decision is NOT a trade signal, NOT a
            # probability, and NOT a guarantee of profitability.
            # ---------------------------------------------
            trade_decision_at_t = (
                self._trade_decision_engine.decide(
                    candidate=trade_candidate_at_t,
                    index=t,
                    timestamp=trigger_candle.timestamp,
                )
                if (
                    self._trade_decision_engine is not None
                    and trade_candidate_at_t is not None
                )
                else None
            )

            # ---------------------------------------------
            # TRADE OPPORTUNITY (Sprint 11T)
            #
            # Additive evidence only. Derived from the already-computed
            # trade decision (itself from candles[:t+1]). The
            # opportunity engine reads no candles directly, so it cannot
            # introduce look-ahead bias. It is NOT fed into the
            # confluence/decision/signal engines below, so existing
            # signal / trade behaviour is unchanged. Only produced when
            # a trade decision exists (a None decision carries no
            # opportunity). A trade opportunity is NOT a trade signal,
            # NOT a probability, and NOT a guarantee of profitability.
            # ---------------------------------------------
            trade_opportunity_at_t = (
                self._trade_opportunity_engine.evaluate(
                    decision=trade_decision_at_t,
                    index=t,
                    timestamp=trigger_candle.timestamp,
                )
                if (
                    self._trade_opportunity_engine is not None
                    and trade_decision_at_t is not None
                )
                else None
            )

            swings = self._swing_engine.detect(visible)

            if not swings:
                reason = "Insufficient swings for structure analysis."
                points.append(
                    self._point(
                        t,
                        trigger_candle,
                        decision_direction,
                        decision_status,
                        signal_state,
                        signal,
                        validation,
                        reason,
                        patterns=patterns_at_t,
                        market_context=market_context_at_t,
                        setup_assessment=setup_assessment_at_t,
                        trade_candidate=trade_candidate_at_t,
                        trade_decision=trade_decision_at_t,
                        trade_opportunity=trade_opportunity_at_t,
                    )
                )
                continue

            structures = self._structure_engine.analyze(swings)
            analysis = self._structure_analysis_engine.analyze(
                structures,
            )
            bos = self._bos_engine.analyze(analysis)
            choch = self._choch_engine.analyze(
                structures,
                analysis,
                bos,
            )
            trend = self._trend_engine.analyze(
                analysis,
                bos,
                choch,
            )

            pools = self._liquidity_engine.detect(swings)
            events = self._liquidity_event_engine.analyze(
                pools,
                visible,
            )

            confluence = self._confluence_engine.analyze(
                analysis=_AnalysisView(bias=analysis.current_bias),
                bos=_BOSView(
                    detected=bos.detected,
                    type=bos.bos_type,
                ),
                choch=_CHOCHView(
                    detected=choch.detected,
                    type=choch.choch_type,
                ),
                trend=trend,
                liquidity_events=events,
                reference_time=trigger_candle.timestamp,
            )

            decision = self._decision_engine.analyze(confluence)

            decision_direction = decision.direction.name
            decision_status = decision.status.name

            signal = self._build_signal(
                decision,
                visible,
                pools,
            )

            signal_state = (
                signal.state.name if signal is not None else signal_state
            )

            if signal is not None and signal.eligible:
                eligible_decisions += 1

            # ---------------------------------------------
            # SIGNAL GATING + VALIDATION (future candles only)
            # ---------------------------------------------

            if (
                signal is not None
                and signal.eligible
                and signal.state
                in (SignalState.LONG, SignalState.SHORT)
            ):

                if t < next_signal_index:
                    # An existing validation is still active.
                    # Suppress this overlapping signal: keep the
                    # would-be signal for inspection but perform
                    # no second validation.
                    reason = (
                        "Eligible signal suppressed: an active "
                        "validation is in progress."
                    )
                    points.append(
                        self._point(
                            t,
                            trigger_candle,
                            decision_direction,
                            decision_status,
                            signal_state,
                            signal,
                            None,
                            reason,
                            suppressed=True,
                            patterns=patterns_at_t,
                            market_context=market_context_at_t,
                            setup_assessment=setup_assessment_at_t,
                            trade_candidate=trade_candidate_at_t,
                            trade_decision=trade_decision_at_t,
                            trade_opportunity=trade_opportunity_at_t,
                        )
                    )
                    continue

                signals_generated += 1
                signals.append(signal)

                future = history[t + 1:]

                validation = self._validation_engine.validate(
                    signal,
                    future,
                    max_candles=self.config.max_validation_candles,
                )

                validations.append(validation)
                signals_validated += 1

                if validation.status in (
                    ValidationStatus.WIN,
                    ValidationStatus.LOSS,
                ):
                    completed_trades += 1

                # Advance past the validation window so the
                # next signal cannot overlap this one. If the
                # signal resolved (terminal status) the window
                # is exactly the candles consumed; if it is
                # still OPEN (no future candles) evaluation
                # simply continues from the next index because
                # no further data exists.
                if validation.status == ValidationStatus.OPEN:
                    next_signal_index = t + 1
                else:
                    next_signal_index = (
                        t + 1 + max(validation.candles_evaluated, 1)
                    )

                reason = (
                    f"Signal validated as {validation.status.name}."
                )

            elif signal is not None and signal.eligible:
                reason = (
                    "Signal eligible but not in a directional "
                    "state; no validation performed."
                )

            elif signal is not None:
                reason = self._no_signal_reason(signal, decision)

            else:
                reason = "No signal generated."

            points.append(
                self._point(
                    t,
                    trigger_candle,
                    decision_direction,
                    decision_status,
                    signal_state,
                    signal,
                    validation,
                    reason,
                    patterns=patterns_at_t,
                    market_context=market_context_at_t,
                    setup_assessment=setup_assessment_at_t,
                    trade_candidate=trade_candidate_at_t,
                    trade_decision=trade_decision_at_t,
                    trade_opportunity=trade_opportunity_at_t,
                )
            )

        performance = self._performance_engine.analyze(validations)

        return PipelineResult(
            candles_processed=total,
            evaluation_points=len(points),
            decisions_generated=len(points),
            eligible_decisions=eligible_decisions,
            signals_generated=signals_generated,
            signals_validated=signals_validated,
            completed_trades=completed_trades,
            evaluation_points_sequence=tuple(points),
            signals=tuple(signals),
            validation_results=tuple(validations),
            patterns=tuple(all_patterns),
            performance=performance,
        )

    # ========================================================
    # SIGNAL CONTEXT
    # ========================================================

    def _build_signal(
        self,
        decision,
        visible: list[OHLCVCandle],
        pools,
    ):
        """
        Build the ``SignalContext`` from information available at
        the trigger candle only.
        """

        trigger_close = visible[-1].close

        # Structural / liquidity anchors known at T.
        structure_break_level = self._structure_break_level(pools)
        liquidity_level = self._liquidity_anchor_level(pools)

        context = SignalContext(
            trigger_close=trigger_close,
            structure_break_level=structure_break_level,
            liquidity_level=liquidity_level,
            reference_time=visible[-1].timestamp,
        )

        return self._signal_engine.analyze(decision, context)

    # ========================================================
    # STRUCTURAL ANCHORS
    # ========================================================

    def _structure_break_level(self, pools) -> float | None:
        """
        Most recent buy-side pool price known at T.

        Used as a structural target/entry anchor. Returns None
        when no buy-side liquidity exists yet.
        """

        buy_side = [
            pool.price
            for pool in pools
            if pool.liquidity_type.name == "BUY_SIDE"
        ]

        if not buy_side:
            return None

        return max(buy_side)

    def _liquidity_anchor_level(self, pools) -> float | None:
        """
        Most recent sell-side pool price known at T.

        Used as a structural stop anchor for LONG setups.
        Returns None when no sell-side liquidity exists yet.
        """

        sell_side = [
            pool.price
            for pool in pools
            if pool.liquidity_type.name == "SELL_SIDE"
        ]

        if not sell_side:
            return None

        return min(sell_side)

    # ========================================================
    # HELPERS
    # ========================================================

    def _first_evaluation_index(self) -> int:
        """
        First index eligible for evaluation.

        Honours the minimum-history requirement. The pipeline
        never fabricates indicators or structure when the
        initial history is too short; it simply skips.
        """

        return max(self.config.min_history, 1)

    def _check_chronological(
        self,
        candles: list[OHLCVCandle],
    ) -> None:
        """
        Validate that candles are supplied oldest -> newest.

        The pipeline never re-sorts. A violation is a caller
        contract error and is surfaced explicitly.
        """

        previous = None

        for candle in candles:
            ts = candle.timestamp

            if previous is not None and ts < previous:
                raise ValueError(
                    "Candles must be supplied in chronological "
                    "order (oldest -> newest). The pipeline does "
                    "not re-sort input."
                )

            previous = ts

    def _point(
        self,
        index: int,
        trigger_candle: OHLCVCandle,
        decision_direction: str,
        decision_status: str,
        signal_state: str,
        signal,
        validation,
        reason: str,
        suppressed: bool = False,
        patterns: tuple = (),
        market_context=None,
        setup_assessment=None,
        trade_candidate=None,
        trade_decision=None,
        trade_opportunity=None,
    ) -> PipelineEvaluationPoint:

        return PipelineEvaluationPoint(
            index=index,
            timestamp=trigger_candle.timestamp,
            decision_direction=decision_direction,
            decision_status=decision_status,
            signal_state=signal_state,
            signal=signal,
            validation=validation,
            reason=reason,
            suppressed=suppressed,
            patterns=patterns,
            market_context=market_context,
            setup_assessment=setup_assessment,
            trade_candidate=trade_candidate,
            trade_decision=trade_decision,
            trade_opportunity=trade_opportunity,
        )

    def _no_signal_reason(self, signal, decision) -> str:
        """
        Explain why an eligible decision did not yield a signal.
        """

        if signal.state == SignalState.NO_SIGNAL:
            return (
                f"Decision is {decision.direction.name.lower()} "
                "but no directional signal was produced."
            )

        if signal.state == SignalState.INVALID:
            return "Signal rejected: setup is invalid."

        return "No signal generated."

    def _empty_result(self, total: int) -> PipelineResult:
        """
        Result for an empty candle collection.

        Performance analytics over zero validation results is
        delegated to the performance engine, which never raises
        on empty input.
        """

        performance = self._performance_engine.analyze([])

        return PipelineResult(
            candles_processed=total,
            evaluation_points=0,
            decisions_generated=0,
            eligible_decisions=0,
            signals_generated=0,
            signals_validated=0,
            completed_trades=0,
            evaluation_points_sequence=tuple(),
            signals=tuple(),
            validation_results=tuple(),
            performance=performance,
        )
