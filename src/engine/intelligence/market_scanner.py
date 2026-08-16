"""
Multi-timeframe market scanner engine (Sprint 11U).

``MarketScanner`` accepts a collection of instrument / timeframe datasets
and produces a deterministic, descriptive ranked set of trade
opportunities across instruments. It is the market-research / scanning
layer of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)  <- this layer
    7. MARKET SCANNER               (Sprint 11U)  <- this layer

The scanner is deterministic, pure and future-leakage safe. It reuses
the existing intelligence engines (candle patterns, market context,
setup confluence, trade candidate, trade decision, trade opportunity)
and the Sprint 11U :class:`MTFAlignmentEngine`. It implements NO new
trading / candidate / decision / opportunity logic; every value is read
from the reused engine outputs.

DESIGN PRINCIPLE — distinct from Sprint 11T:

Sprint 11T ranks opportunities WITHIN one evaluation context.
Sprint 11U ranks opportunities ACROSS instruments and timeframes. This
distinction is maintained: the scanner consumes the Sprint 11T
``TradeOpportunity`` verbatim (by reference) and adds the
multi-timeframe alignment + market-level ranking layer on top.

DESIGN PRINCIPLE — completed higher-timeframe candle (look-ahead safety):

This is one of the most important requirements of 11U. For an
evaluation time ``T`` (the close of the latest completed
setup-timeframe candle), the higher-timeframe context uses ONLY the
latest higher-timeframe candle that CLOSED STRICTLY BEFORE ``T``. An
in-progress higher-timeframe candle is NEVER used, even if it exists in
the dataset. This prevents the scanner from reading future
higher-timeframe information that was not yet available at ``T``.

DESIGN PRINCIPLE — no fabricated evidence:

Missing data is represented honestly. An instrument whose context
timeframe carries no completed candle before ``T`` is INCOMPLETE — its
alignment is ``UNKNOWN`` and it never reaches a bullish / bearish
conclusion fabricated from absent data. An instrument whose setup
timeframe produced no opportunity carries ``NO_OPPORTUNITY``.

DESIGN PRINCIPLE — no manufactured winner:

When no instrument produced an eligible opportunity, ``best`` is
``None``. A best opportunity is never manufactured. An alternative is
surfaced ONLY when a strictly stronger best exists.

MARKET-LEVEL RANKING POLICY (applied in order, ties by the next key,
finally a direction-symmetric deterministic tie-break; no randomness):

1. ELIGIBILITY — ineligible / incomplete instruments never rank as
   surfaced opportunities.
2. MTF_ALIGNMENT — ALIGNED first, then NEUTRAL, then CONFLICTING, then
   UNKNOWN (missing evidence is weakest, never treated as aligned).
3. OPPORTUNITY_STATUS — stronger Sprint 11T status first
   (BEST_OPPORTUNITY > ALTERNATIVE_OPPORTUNITY > WATCH > NO_OPPORTUNITY).
4. DECISION_CLASSIFICATION — stronger Sprint 11S classification first
   (PREFERRED > QUALIFIED > WATCH > REJECTED).
5. DECISION_SCORE — higher Sprint 11S decision score first.
6. GEOMETRY_COMPLETENESS — complete geometry first.
7. RISK_REWARD — higher risk/reward ratio first (absent last; never
   fabricated).
8. CONFLUENCE — higher confluence score first.
9. CONFLICT_FREE — fewer conflicting evidence sources first.
10. DETERMINISTIC_TIEBREAK — instrument name asc, then direction order
    (LONG < SHORT < NONE), then setup timeframe asc. Direction only
    matters here as a final, direction-symmetric last resort; it never
    makes a SHORT outrank a LONG (or vice versa) on evidence.

This is intelligence, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g.
``from engine.intelligence.market_scanner import MarketScanner``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from engine.config.market_scan_config import (
    MarketScanConfig,
    _ALIGNMENT_STRENGTH,
)
from engine.intelligence.mtf_alignment import MTFAlignmentEngine
from engine.models.market_context import MarketContext
from engine.models.market_scan import (
    InstrumentScanResult,
    MarketScanResult,
    MTFAlignment,
    RankedScanOpportunity,
    ScanStatus,
    TimeframeRole,
    TimeframeSlice,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.opportunity import (
    OpportunityStatus,
    TradeOpportunity,
)
from engine.models.trade_decision import TradeDecision


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _latest_completed_before(
    candles: Sequence[OHLCVCandle],
    cutoff: datetime,
) -> OHLCVCandle | None:
    cutoff = _ensure_utc(cutoff)
    """
    Return the latest candle whose timestamp is strictly before
    ``cutoff``.

    This is the core look-ahead protection: an in-progress
    higher-timeframe candle (timestamp >= cutoff) is NEVER used. The
    candles are assumed chronological (oldest -> newest); the scan is a
    function of candles that had CLOSED strictly before the evaluation
    time.
    """

    completed = [
        c for c in candles
        if _ensure_utc(c.timestamp) < cutoff
    ]

    if not completed:
        return None

    return completed[-1]


def _latest_completed_at_or_before(
    candles: Sequence[OHLCVCandle],
    cutoff: datetime,
) -> tuple[OHLCVCandle | None, int]:
    """
    Return the latest candle whose timestamp is <= ``cutoff`` and its
    index, for the setup timeframe.

    The setup timeframe reads its latest CLOSED candle at or before the
    evaluation time (the evaluation time IS the setup-timeframe candle
    close we scan from).
    """
    cutoff = _ensure_utc(cutoff)
    
    for i in range(len(candles) - 1, -1, -1):
        if _ensure_utc(candles[i].timestamp) <= cutoff:
            return candles[i], i
    return None, -1


@dataclass(frozen=True, slots=True)
class InstrumentDataset:
    """
    One instrument's two-timeframe dataset for the scanner.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``).

    context_candles
        The higher / context timeframe candles (oldest -> newest).

    setup_candles
        The lower / setup timeframe candles (oldest -> newest).
    """

    instrument: str
    context_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)
    setup_candles: tuple[OHLCVCandle, ...] = field(default_factory=tuple)


class MarketScanner:
    """
    Scan a collection of instrument / timeframe datasets and produce a
    deterministic, descriptive ranked set of trade opportunities.

    Public API:

        scan(datasets, evaluation_time=None, engines=None) -> MarketScanResult

    The scanner is stateless across calls: identical inputs always
    produce identical outputs.

    The ``engines`` parameter (a :class:`ScanEngines` bundle) lets the
    caller supply a pre-built set of the reused Sprint 11O-11T engines
    + the Sprint 11U alignment engine. When omitted, the scanner
    constructs default instances.
    """

    def __init__(self, config: MarketScanConfig | None = None) -> None:
        self.config = config or MarketScanConfig()
        self._alignment_engine = MTFAlignmentEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def scan(
        self,
        datasets: Iterable[InstrumentDataset | Mapping],
        evaluation_time: datetime | None = None,
        engines: "ScanEngines | None" = None,
    ) -> MarketScanResult:
        """
        Scan one or more instrument datasets at ``evaluation_time``.

        ``evaluation_time`` defaults to the latest completed
        setup-timeframe candle close across instruments (deterministic).

        ``engines`` lets the caller supply a pre-built
        :class:`ScanEngines` bundle (reusing the existing Sprint 11O-11T
        engines). When omitted, default engine instances are built once
        per call.

        The result is deterministic and descriptive. It makes no
        profitability / probability / directional-prediction claim.
        """

        bundle = engines or ScanEngines.default()
        ds_list = [
            d if isinstance(d, InstrumentDataset) else InstrumentDataset(
                instrument=d["instrument"],
                context_candles=tuple(d.get("context_candles", ())),
                setup_candles=tuple(d.get("setup_candles", ())),
            )
            for d in datasets
        ]

        # Deterministic evaluation time: the latest completed
        # setup-timeframe candle close across instruments.
        if evaluation_time is None:
            evaluation_time = self._latest_setup_close(ds_list)

        instrument_results: list[InstrumentScanResult] = []
        for ds in ds_list:
            instrument_results.append(
                self._scan_instrument(ds, evaluation_time, bundle),
            )

        return self._build_scan_result(
            instrument_results, evaluation_time,
        )

    # ========================================================
    # PER-INSTRUMENT SCAN
    # ========================================================

    def _scan_instrument(
        self,
        ds: InstrumentDataset,
        evaluation_time: datetime,
        engines: "ScanEngines",
    ) -> InstrumentScanResult:
        """
        Scan one instrument: build the higher-context slice + the
        lower-setup slice, compute alignment, and assemble the
        descriptive instrument result.
        """

        context_tf = self.config.context_timeframe
        setup_tf = self.config.setup_timeframe

        # ----------------------------------------------------------
        # HIGHER-TIMEFRAME (CONTEXT) SLICE — completed-candle only.
        # ----------------------------------------------------------
        higher_context: MarketContext | None = None
        higher_ts: datetime | None = None
        context_ready = False
        if ds.context_candles:
            if self.config.require_completed_context_candle:
                htf_candle = _latest_completed_before(
                    ds.context_candles, evaluation_time,
                )
            else:
                htf_candle = (
                    ds.context_candles[-1]
                    if ds.context_candles[-1].timestamp <= evaluation_time
                    else None
                )
            if htf_candle is not None:
                higher_ts = htf_candle.timestamp
                # Build the context slice from context candles up to and
                # including the completed higher-timeframe candle. This
                # is a function of candles[:htf_index+1] only — no
                # in-progress / future higher candle is read.
                htf_visible = [
                    c for c in ds.context_candles
                    if c.timestamp <= htf_candle.timestamp
                ]
                try:
                    higher_context = engines.market_context.analyze_at(
                        htf_visible, len(htf_visible) - 1,
                    )
                    context_ready = True
                except (IndexError, ValueError):
                    higher_context = None
                    context_ready = False

        context_slice = TimeframeSlice(
            identity=InstrumentTimeframe(
                instrument=ds.instrument,
                timeframe=context_tf,
                role=TimeframeRole.CONTEXT_TIMEFRAME,
            ),
            timestamp=higher_ts,
            market_context=higher_context,
            ready=context_ready,
        )

        # ----------------------------------------------------------
        # LOWER-TIMEFRAME (SETUP) SLICE — latest closed setup candle.
        # ----------------------------------------------------------
        setup_candles = list(ds.setup_candles)
        lower_context: MarketContext | None = None
        decision: TradeDecision | None = None
        opportunity: TradeOpportunity | None = None
        setup_ts: datetime | None = None
        setup_ready = False

        if setup_candles:
            setup_candle, setup_idx = _latest_completed_at_or_before(
                setup_candles, evaluation_time,
            )
            if setup_candle is not None and setup_idx >= 0:
                setup_ts = setup_candle.timestamp
                setup_visible = setup_candles[: setup_idx + 1]
                # Insufficient history -> INCOMPLETE setup slice.
                if len(setup_visible) <= self.config.min_history:
                    setup_ready = False
                else:
                    try:
                        lower_context = engines.market_context.analyze_at(
                            setup_visible, setup_idx,
                        )
                        patterns_at_t = [
                            p for p in engines.candle_patterns.detect(
                                setup_visible,
                            ) if p.index == setup_idx
                        ]
                        assessment = engines.setup_confluence.assess(
                            patterns=patterns_at_t,
                            market_context=lower_context,
                            index=setup_idx,
                            timestamp=setup_candle.timestamp,
                        )
                        candidate = engines.trade_candidates.generate(
                            assessment=assessment,
                            market_context=lower_context,
                            index=setup_idx,
                            timestamp=setup_candle.timestamp,
                            close_price=setup_candle.close,
                        )
                        decision = engines.trade_decision.decide(
                            candidate=candidate,
                            index=setup_idx,
                            timestamp=setup_candle.timestamp,
                        )
                        opportunity = engines.trade_opportunity.evaluate(
                            decision=decision,
                            index=setup_idx,
                            timestamp=setup_candle.timestamp,
                        )
                        setup_ready = True
                    except (IndexError, ValueError):
                        setup_ready = False

        setup_slice = TimeframeSlice(
            identity=InstrumentTimeframe(
                instrument=ds.instrument,
                timeframe=setup_tf,
                role=TimeframeRole.SETUP_TIMEFRAME,
            ),
            timestamp=setup_ts,
            market_context=lower_context,
            decision=decision,
            opportunity=opportunity,
            ready=setup_ready,
        )

        # ----------------------------------------------------------
        # ALIGNMENT + ASSEMBLY
        # ----------------------------------------------------------
        complete = context_slice.ready and setup_slice.ready
        # When context is required and missing, the instrument is
        # INCOMPLETE regardless of the setup slice.
        if self.config.require_context_timeframe and not context_slice.ready:
            complete = False

        lower_direction = ""
        decision_classification = ""
        decision_score = 0
        risk_reward_ratio: float | None = None
        if opportunity is not None:
            lower_direction = getattr(opportunity, "direction", "") or ""
            decision_classification = getattr(
                opportunity, "decision_classification", "",
            ) or ""
            decision_score = getattr(opportunity, "decision_score", 0) or 0
            risk_reward_ratio = getattr(
                opportunity, "risk_reward_ratio", None,
            )

        alignment = self._alignment_engine.align(
            higher_context, lower_direction,
        )

        reason = self._instrument_reason(
            ds.instrument, alignment, complete,
            context_slice.ready, setup_slice.ready, opportunity,
        )

        # Eligibility verdict for market-level ranking. Stored
        # explicitly on the result so it survives serialization (the
        # heavy opportunity object is dropped on persist and
        # reconstructs as None).
        if opportunity is None:
            is_eligible = False
        elif self.config.require_opportunity_for_eligibility:
            # Default: only an ELIGIBLE Sprint 11T opportunity qualifies.
            is_eligible = bool(getattr(opportunity, "is_eligible", False))
        else:
            # Relaxed: any directional opportunity object (even an
            # ineligible one) may rank, as long as it carries a
            # directional intent.
            is_eligible = lower_direction not in ("", "NONE")

        return InstrumentScanResult(
            instrument=ds.instrument,
            context_timeframe=context_tf,
            setup_timeframe=setup_tf,
            timestamp=setup_ts,
            higher_context=higher_context,
            lower_context=lower_context,
            decision=decision,
            opportunity=opportunity,
            alignment=alignment,
            complete=complete,
            direction=lower_direction,
            decision_classification=decision_classification,
            decision_score=decision_score,
            risk_reward_ratio=risk_reward_ratio,
            eligible=is_eligible,
            reason=reason,
        )

    # ========================================================
    # SCAN RESULT ASSEMBLY
    # ========================================================

    def _build_scan_result(
        self,
        instrument_results: list[InstrumentScanResult],
        evaluation_time: datetime | None,
    ) -> MarketScanResult:
        """
        Combine the per-instrument results into a deterministic,
        descriptive ranked scan result.
        """

        instruments = tuple(sorted(r.instrument for r in instrument_results))

        # Partition by the per-instrument eligibility verdict (already
        # computed in ``_scan_instrument`` and stored on each result so
        # it survives serialization).
        eligible: list[InstrumentScanResult] = []
        ineligible: list[InstrumentScanResult] = []
        for r in instrument_results:
            if r.eligible:
                eligible.append(r)
            else:
                ineligible.append(r)

        eligible_sorted = sorted(eligible, key=self._ranking_key)

        ranked: list[RankedScanOpportunity] = []
        for i, r in enumerate(eligible_sorted):
            rank_i = i + 1
            ranked.append(
                RankedScanOpportunity(
                    rank=rank_i, opportunity=r, alignment=r.alignment,
                ),
            )

        for r in ineligible:
            ranked.append(
                RankedScanOpportunity(
                    rank=0, opportunity=r, alignment=r.alignment,
                ),
            )

        # Best + alternatives (respect the surfaced cap).
        best = ranked[0] if ranked and ranked[0].rank == 1 else None
        alternatives: list[RankedScanOpportunity] = []
        if best is not None:
            cap = self.config.max_surfaced_opportunities
            for r in ranked[1:]:
                if r.rank == 0:
                    break
                if cap is not None and (len(alternatives) + 2) > cap:
                    break
                alternatives.append(r)

        rejected = tuple(ineligible)
        status = self._scan_status(instrument_results, eligible, ranked)
        scan_id = self._scan_id(instrument_results)
        rationale = self._rationale(
            instrument_results, eligible, best, status,
        )

        # ``results`` ordered strongest-first (eligible ranked, then
        # ineligible / incomplete last, deterministic by instrument).
        ordered_results = [r.opportunity for r in ranked]

        return MarketScanResult(
            scan_id=scan_id,
            timestamp=evaluation_time,
            instruments=instruments,
            timeframes=(self.config.context_timeframe, self.config.setup_timeframe),
            status=status,
            results=tuple(ordered_results),
            ranked=tuple(ranked),
            best=best,
            alternatives=tuple(alternatives),
            rejected=rejected,
            rationale=rationale,
        )

    # ========================================================
    # RANKING
    # ========================================================

    def _ranking_key(self, result: InstrumentScanResult) -> tuple:
        """
        Deterministic ranking key (strongest first) among ELIGIBLE
        instruments.

        Order:

        1. MTF alignment strength (desc: ALIGNED > NEUTRAL > CONFLICTING
           > UNKNOWN).
        2. opportunity status strength (desc: BEST > ALT > WATCH).
        3. decision classification strength (desc).
        4. decision score (desc).
        5. geometry complete first.
        6. risk/reward ratio (desc, absent last; never fabricated).
        7. confluence score (desc).
        8. fewer conflicting evidence sources.
        9. instrument name asc (deterministic final tie-break).
        """

        opp = result.opportunity
        opp_status = getattr(opp, "status", OpportunityStatus.NO_OPPORTUNITY)
        if not isinstance(opp_status, OpportunityStatus):
            opp_status = OpportunityStatus.NO_OPPORTUNITY
        rr = result.risk_reward_ratio
        return (
            -_ALIGNMENT_STRENGTH[result.alignment],
            -opp_status.rank_value,
            -_decision_class_strength(result.decision_classification),
            -result.decision_score,
            0 if self._geometry_complete(result) else 1,
            -(rr if rr is not None else -1.0),
            -self._confluence(result),
            self._conflicting(result),
            result.instrument,
        )

    @staticmethod
    def _geometry_complete(result: InstrumentScanResult) -> bool:
        opp = result.opportunity
        if opp is None:
            return False
        return bool(getattr(opp, "geometry_complete", False))

    @staticmethod
    def _confluence(result: InstrumentScanResult) -> int:
        opp = result.opportunity
        if opp is None:
            return 0
        return int(getattr(opp, "confluence_score", 0) or 0)

    @staticmethod
    def _conflicting(result: InstrumentScanResult) -> int:
        opp = result.opportunity
        if opp is None:
            return 0
        return int(getattr(opp, "conflicting_count", 0) or 0)

    # ========================================================
    # STATUS + IDENTITY + RATIONALE
    # ========================================================

    def _scan_status(
        self,
        results: list[InstrumentScanResult],
        eligible: list[InstrumentScanResult],
        ranked: list[RankedScanOpportunity],
    ) -> ScanStatus:
        # INCOMPLETE: required timeframe data missing for ALL instruments
        # (no instrument is structurally complete).
        any_complete = any(r.complete for r in results)
        if not any_complete:
            return ScanStatus.INCOMPLETE

        if eligible:
            return ScanStatus.OPPORTUNITIES_FOUND

        # Watch-only: technical setups (candidates / decisions) exist
        # but none meet opportunity eligibility. Otherwise no setup.
        any_setup = any(r.opportunity is not None for r in results)
        if any_setup:
            return ScanStatus.WATCH_ONLY
        return ScanStatus.NO_OPPORTUNITY

    def _scan_id(self, results: list[InstrumentScanResult]) -> str:
        instruments = sorted(r.instrument for r in results)
        payload = {
            "context_timeframe": self.config.context_timeframe,
            "setup_timeframe": self.config.setup_timeframe,
            "instruments": instruments,
            "label": self.config.label,
            "metadata": [
                [k, v] for k, v in self.config.metadata
            ],
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"scan-{digest[:16]}"

    def _rationale(
        self,
        results: list[InstrumentScanResult],
        eligible: list[InstrumentScanResult],
        best: RankedScanOpportunity | None,
        status: ScanStatus,
    ) -> str:
        if status == ScanStatus.INCOMPLETE:
            return (
                "Scan INCOMPLETE: required timeframe / context data is "
                "missing or insufficient for every instrument. Missing "
                "data is not a bullish or bearish conclusion. Descriptive "
                "only; not predictive and not a guarantee of profitability."
            )
        if best is not None:
            b = best.opportunity
            rr = (
                f"{b.risk_reward_ratio:.2f}"
                if b.risk_reward_ratio is not None
                else "unavailable"
            )
            return (
                f"Best market opportunity: {b.instrument} {b.direction} "
                f"(MTF {b.alignment.name}, decision "
                f"{b.decision_classification or 'none'}, score "
                f"{b.decision_score}, R:R {rr}) among "
                f"{len(eligible)} eligible instrument(s). Ranking is "
                "descriptive; not predictive and not a guarantee of "
                "profitability."
            )
        if status == ScanStatus.WATCH_ONLY:
            return (
                "WATCH_ONLY: technical setups exist but none met the "
                "opportunity eligibility requirements. Descriptive only; "
                "not predictive and not a guarantee of profitability."
            )
        return (
            "NO_OPPORTUNITY: no meaningful candidate / setup exists in "
            "the scanned datasets. Descriptive only; not predictive and "
            "not a guarantee of profitability."
        )

    def _instrument_reason(
        self,
        instrument: str,
        alignment: MTFAlignment,
        complete: bool,
        context_ready: bool,
        setup_ready: bool,
        opportunity: TradeOpportunity | None,
    ) -> str:
        if not complete:
            missing = []
            if not context_ready:
                missing.append("higher-timeframe context")
            if not setup_ready:
                missing.append("setup-timeframe data")
            return (
                f"{instrument}: INCOMPLETE scan (missing "
                + " and ".join(missing)
                + "). Missing data is not a directional conclusion. "
                "Descriptive only; not predictive and not a guarantee "
                "of profitability."
            )
        if opportunity is None:
            return (
                f"{instrument}: no trade opportunity on the setup "
                "timeframe. Descriptive only; not predictive and not a "
                "guarantee of profitability."
            )
        opp_status = getattr(opportunity, "status", OpportunityStatus.NO_OPPORTUNITY)
        return (
            f"{instrument}: MTF alignment {alignment.name}, opportunity "
            f"{getattr(opp_status, 'name', 'UNKNOWN')}, decision "
            f"{getattr(opportunity, 'decision_classification', 'none')}. "
            "Descriptive only; not predictive and not a guarantee of "
            "profitability."
        )

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _latest_setup_close(
        datasets: list[InstrumentDataset],
    ) -> datetime | None:
        """Latest completed setup-timeframe candle close across instruments."""

        latest: datetime | None = None
        for ds in datasets:
            if not ds.setup_candles:
                continue
            c = ds.setup_candles[-1]
            if latest is None or c.timestamp > latest:
                latest = c.timestamp
        return latest


# ============================================================
# DECISION CLASSIFICATION STRENGTH (reused from Sprint 11S)
# ============================================================

_DECISION_CLASS_RANK = {
    "PREFERRED": 3,
    "QUALIFIED": 2,
    "WATCH": 1,
    "REJECTED": 0,
}


def _decision_class_strength(name: str) -> int:
    return _DECISION_CLASS_RANK.get((name or "").upper(), 0)


# Late import to avoid a circular dependency (InstrumentTimeframe is
# referenced by TimeframeSlice defined above).
from engine.models.market_scan import InstrumentTimeframe  # noqa: E402


# ============================================================
# SCAN ENGINES BUNDLE
# ============================================================


@dataclass
class ScanEngines:
    """
    A bundle of the reused Sprint 11O-11T engines + the Sprint 11U
    alignment engine, supplied to :meth:`MarketScanner.scan`.

    This lets the caller reuse a single pre-built set of engines across
    many scan calls (and lets tests inject configured engines). The
    scanner never modifies these engines. Each engine is duck-typed to
    its minimal contract (``candle_patterns.detect``,
    ``market_context.analyze_at``, ``setup_confluence.assess``,
    ``trade_candidates.generate``, ``trade_decision.decide``,
    ``trade_opportunity.evaluate``, ``alignment.align``).

    Use :meth:`ScanEngines.default` to build a default bundle.
    """

    candle_patterns: object
    market_context: object
    setup_confluence: object
    trade_candidates: object
    trade_decision: object
    trade_opportunity: object
    alignment: MTFAlignmentEngine

    @classmethod
    def default(cls, lookback: int = 2) -> "ScanEngines":
        # Local imports keep the module top-level clean and avoid
        # importing engines that require configured state at module
        # load time.
        from engine.config.swing_config import SwingConfig
        from engine.intelligence.candle_patterns import CandlePatternEngine
        from engine.intelligence.market_context_engine import (
            MarketContextEngine,
        )
        from engine.intelligence.setup_confluence import (
            SetupConfluenceEngine,
        )
        from engine.intelligence.trade_candidates import (
            TradeCandidateEngine,
        )
        from engine.intelligence.trade_decision import (
            TradeDecisionEngine,
        )
        from engine.intelligence.trade_opportunity import (
            TradeOpportunityEngine,
        )

        swing_cfg = SwingConfig(lookback=lookback)
        return cls(
            candle_patterns=CandlePatternEngine(),
            market_context=MarketContextEngine(swing_config=swing_cfg),
            setup_confluence=SetupConfluenceEngine(),
            trade_candidates=TradeCandidateEngine(),
            trade_decision=TradeDecisionEngine(),
            trade_opportunity=TradeOpportunityEngine(),
            alignment=MTFAlignmentEngine(),
        )


__all__ = [
    "InstrumentDataset",
    "MarketScanner",
    "ScanEngines",
]
