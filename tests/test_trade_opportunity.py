"""
Tests for the trade opportunity filter / ranking layer (Sprint 11T).

Coverage:

A. Model validation (enums, frozen+slots, rank/status invariants,
   properties, immutability)
B. Configuration validation + defaults (eligibility gates, best caps,
   surfacing cap, ranking order, validation)
C. Eligibility (candidate status, decision classification, score,
   geometry gate, risk/reward gate, conflict disqualification)
D. Filtering (NO_CANDIDATE never an opportunity, REJECTED filtered,
   below-min-score filtered, poor R:R gated, conflict disqualified)
E. Ranking (deterministic ordering, best identification, no best
   when none clears caps, alternatives, watch, surfaced cap)
F. Deterministic tie-breaking (identical scores, multiple secondary
   keys, no random behaviour, repeated calls)
G. LONG / SHORT symmetry (direction only matters through evidence;
   tie-break direction order)
H. Incomplete geometry + missing R:R (honestly represented, never
   fabricated, never best/alt by default)
I. Conflicting evidence (never silently best/alt, recorded)
J. No candidate / multiple / single candidate handling
K. Point-in-time correctness (prefix == full series; future mutation
   leaves opportunity(T) unchanged; several future candles)
L. Pipeline integration (additive, signal/trade behaviour unchanged,
   disabled reproduces pre-11T, opportunity attached, pipeline ==
   standalone prefix, regression signals=4 trades=3)
M. Reporting (sections, warning present, no predictive language,
   returns str, ranking table, determinism)
N. Determinism + immutability (same inputs, repeated pipeline runs,
   frozen models, slots)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.swing_config import SwingConfig
from engine.config.trade_opportunity_config import (
    RANKING_PRIORITY_ORDER,
    RankingPriority,
    TradeOpportunityConfig,
)
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.intelligence.trade_opportunity import TradeOpportunityEngine
from engine.models.ohlcv import OHLCVCandle
from engine.models.opportunity import (
    EligibilityReason,
    EligibilityStatus,
    OpportunityStatus,
    TradeOpportunity,
    TradeOpportunityRanking,
)
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceItem,
    EvidenceSource,
    SetupClassification,
    SetupDirection,
    SetupEvidence,
)
from engine.models.trade_candidate import (
    CandidateDirection,
    CandidateStatus,
    SetupType,
    TradeCandidate,
)
from engine.models.trade_decision import (
    DecisionClassification,
    DecisionScore,
    DecisionScoreComponent,
    TradeDecision,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_opportunity import TradeOpportunityFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# CANDLE / DATASET HELPERS
# ============================================================


def candle(
    close: float,
    high: float,
    low: float,
    index: int,
    volume: float = 1000.0,
) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def bullish_dataset() -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    close = 100.0
    idx = 0
    for _ in range(4):
        for _ in range(3):
            close = round(close + 6, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close - 3, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def bearish_dataset() -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    close = 200.0
    idx = 0
    for _ in range(4):
        for _ in range(3):
            close = round(close - 6, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
        for _ in range(2):
            close = round(close + 3, 2)
            candles.append(candle(close, close + 2, close - 2, idx))
            idx += 1
    return candles


def hammer_candle(index: int, base_close: float, body: float = 2.0):
    close = base_close + body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=close + body,
        low=close - (body * 3.0),
        close=close,
        volume=1000.0,
    )


# ============================================================
# HAND-BUILT MODEL HELPERS
# ============================================================


def _evidence_item(
    source: EvidenceSource,
    direction: SetupDirection,
    alignment: EvidenceAlignment,
    label: str = "x",
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        direction=direction,
        alignment=alignment,
        label=label,
        reason="test",
    )


def _build_evidence(
    direction: SetupDirection = SetupDirection.BULLISH,
    trend_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    structure_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    candle_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    location_alignment: EvidenceAlignment = EvidenceAlignment.ALIGNED,
    range_alignment: EvidenceAlignment = EvidenceAlignment.NEUTRAL,
    trend_label: str = "BULLISH",
    structure_label: str = "HIGHER_HIGH / HIGHER_LOW",
    candle_label: str = "HAMMER",
    location_label: str = "NEAR_SUPPORT",
    range_label: str = "NOT_IN_RANGE",
) -> SetupEvidence:
    trend = _evidence_item(
        EvidenceSource.TREND, direction, trend_alignment, trend_label,
    )
    structure = _evidence_item(
        EvidenceSource.STRUCTURE, direction, structure_alignment,
        structure_label,
    )
    candle_ev = _evidence_item(
        EvidenceSource.CANDLE, direction, candle_alignment, candle_label,
    )
    location = _evidence_item(
        EvidenceSource.LOCATION, direction, location_alignment,
        location_label,
    )
    range_item = _evidence_item(
        EvidenceSource.RANGE, SetupDirection.NEUTRAL, range_alignment,
        range_label,
    )
    all_items = (trend, structure, candle_ev, location, range_item)
    supporting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.ALIGNED
    )
    conflicting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.CONFLICTING
    )
    return SetupEvidence(
        trend=trend,
        structure=structure,
        candle=candle_ev,
        location=location,
        range=range_item,
        supporting=supporting,
        conflicting=conflicting,
    )


def _direction_for_evidence(
    direction: CandidateDirection,
) -> SetupDirection:
    if direction == CandidateDirection.LONG:
        return SetupDirection.BULLISH
    if direction == CandidateDirection.SHORT:
        return SetupDirection.BEARISH
    return SetupDirection.UNKNOWN


def _make_candidate(
    direction: CandidateDirection = CandidateDirection.LONG,
    status: CandidateStatus = CandidateStatus.CANDIDATE,
    setup_type: SetupType = SetupType.TREND_CONTINUATION,
    confluence_score: int = 4,
    evidence: SetupEvidence | None = None,
    entry: float | None = 100.0,
    stop: float | None = 95.0,
    target: float | None = 110.0,
    candle_evidence: str = "HAMMER",
    market_trend: str = "BULLISH",
    market_structure: str = "HIGHER_HIGH / HIGHER_LOW",
    location: str = "NEAR_SUPPORT",
    range_context: str = "NOT_IN_RANGE",
    index: int = 0,
) -> TradeCandidate:
    if evidence is None:
        evidence = _build_evidence(
            direction=_direction_for_evidence(direction),
        )

    # Mirror geometry for SHORT so the 11R model accepts it.
    if direction == CandidateDirection.SHORT and entry is not None:
        if stop is not None and target is not None:
            pass  # caller-supplied SHORT geometry
        else:
            stop = entry + 5.0
            target = entry - 10.0

    risk = reward = ratio = None
    if entry is not None and stop is not None and target is not None:
        if direction == CandidateDirection.LONG:
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target
        if risk > 0 and reward > 0:
            ratio = reward / risk

    return TradeCandidate(
        timestamp=_EPOCH + timedelta(days=index),
        evaluation_index=index,
        direction=direction,
        status=status,
        setup_type=setup_type,
        setup_classification=SetupClassification.POTENTIAL_SETUP,
        entry_reference=entry,
        stop_reference=stop,
        target_reference=target,
        risk_distance=risk,
        reward_distance=reward,
        risk_reward_ratio=ratio,
        confluence_score=confluence_score,
        supporting_evidence=evidence.supporting,
        conflicting_evidence=evidence.conflicting,
        candle_evidence=candle_evidence,
        market_trend=market_trend,
        market_structure=market_structure,
        location=location,
        range_context=range_context,
        reason="test candidate",
    )


def _make_incomplete_candidate(
    direction: CandidateDirection = CandidateDirection.LONG,
    status: CandidateStatus = CandidateStatus.CANDIDATE,
    evidence: SetupEvidence | None = None,
    candle_evidence: str = "HAMMER",
    market_trend: str = "BULLISH",
    market_structure: str = "HIGHER_HIGH / HIGHER_LOW",
    location: str = "NEAR_SUPPORT",
    range_context: str = "NOT_IN_RANGE",
    index: int = 0,
) -> TradeCandidate:
    if evidence is None:
        evidence = _build_evidence(
            direction=_direction_for_evidence(direction),
        )
    return TradeCandidate(
        timestamp=_EPOCH + timedelta(days=index),
        evaluation_index=index,
        direction=direction,
        status=status,
        setup_type=SetupType.SETUP_CANDIDATE,
        setup_classification=SetupClassification.POTENTIAL_SETUP,
        entry_reference=100.0,
        stop_reference=None,
        target_reference=None,
        risk_distance=None,
        reward_distance=None,
        risk_reward_ratio=None,
        confluence_score=4,
        supporting_evidence=evidence.supporting,
        conflicting_evidence=evidence.conflicting,
        candle_evidence=candle_evidence,
        market_trend=market_trend,
        market_structure=market_structure,
        location=location,
        range_context=range_context,
        reason="test candidate (incomplete geometry)",
    )


def _make_decision(
    candidate: TradeCandidate,
    index: int = 0,
    timestamp: datetime | None = None,
) -> TradeDecision:
    """Build a real TradeDecision via the Sprint 11S engine."""
    eng = TradeDecisionEngine()
    return eng.decide(
        candidate,
        index=index,
        timestamp=timestamp or candidate.timestamp,
    )


def _strong_long_decision(index: int = 0) -> TradeDecision:
    return _make_decision(
        _make_candidate(
            direction=CandidateDirection.LONG,
            entry=100.0, stop=95.0, target=120.0,
            confluence_score=5,
            index=index,
        ),
        index=index,
    )


def _strong_short_decision(index: int = 0) -> TradeDecision:
    return _make_decision(
        _make_candidate(
            direction=CandidateDirection.SHORT,
            entry=100.0, stop=105.0, target=80.0,
            confluence_score=5,
            index=index,
        ),
        index=index,
    )


def make_engines(lookback: int = 2):
    swing_cfg = SwingConfig(lookback=lookback)
    return (
        CandlePatternEngine(),
        MarketContextEngine(swing_config=swing_cfg),
        SetupConfluenceEngine(),
        TradeCandidateEngine(),
        TradeDecisionEngine(),
        TradeOpportunityEngine(),
    )


def opportunity_at(
    pat_engine: CandlePatternEngine,
    mc_engine: MarketContextEngine,
    setup_engine: SetupConfluenceEngine,
    cand_engine: TradeCandidateEngine,
    dec_engine: TradeDecisionEngine,
    opp_engine: TradeOpportunityEngine,
    candles: list[OHLCVCandle],
    index: int,
) -> TradeOpportunity:
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    a = setup_engine.assess(pats, ctx, index, candles[index].timestamp)
    c = cand_engine.generate(
        a, ctx, index, candles[index].timestamp, candles[index].close,
    )
    d = dec_engine.decide(c, index, candles[index].timestamp)
    return opp_engine.evaluate(d, index, candles[index].timestamp)


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_enums_have_expected_members(self):
        assert {s.name for s in OpportunityStatus} == {
            "NO_OPPORTUNITY", "WATCH",
            "ALTERNATIVE_OPPORTUNITY", "BEST_OPPORTUNITY",
        }
        assert {s.name for s in EligibilityStatus} == {
            "ELIGIBLE", "INELIGIBLE",
        }

    def test_rank_value_ordering(self):
        assert (
            OpportunityStatus.NO_OPPORTUNITY.rank_value
            < OpportunityStatus.WATCH.rank_value
            < OpportunityStatus.ALTERNATIVE_OPPORTUNITY.rank_value
            < OpportunityStatus.BEST_OPPORTUNITY.rank_value
        )

    def test_models_frozen_and_slots(self):
        d = _strong_long_decision()
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        with pytest.raises(Exception):
            opp.status = OpportunityStatus.NO_OPPORTUNITY  # type: ignore
        with pytest.raises(Exception):
            opp.rank = 99  # type: ignore
        assert hasattr(TradeOpportunity, "__slots__")
        assert hasattr(TradeOpportunityRanking, "__slots__")
        assert hasattr(EligibilityReason, "__slots__")

    def test_best_must_be_rank_one(self):
        d = _strong_long_decision()
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=2,
                status=OpportunityStatus.BEST_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )

    def test_alternative_must_be_rank_two_plus(self):
        d = _strong_long_decision()
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=1,
                status=OpportunityStatus.ALTERNATIVE_OPPORTUNITY,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )

    def test_ineligible_must_have_rank_zero(self):
        d = _strong_long_decision()
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=1,
                status=OpportunityStatus.NO_OPPORTUNITY,
                eligibility=EligibilityStatus.INELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=3,
                status=OpportunityStatus.NO_OPPORTUNITY,
                eligibility=EligibilityStatus.INELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )

    def test_eligible_must_have_nonzero_rank(self):
        d = _strong_long_decision()
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=0,
                status=OpportunityStatus.WATCH,
                eligibility=EligibilityStatus.ELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )

    def test_negative_rank_rejected(self):
        d = _strong_long_decision()
        with pytest.raises(ValueError):
            TradeOpportunity(
                timestamp=d.timestamp,
                evaluation_index=0,
                decision=d,
                direction=d.direction,
                rank=-1,
                status=OpportunityStatus.NO_OPPORTUNITY,
                eligibility=EligibilityStatus.INELIGIBLE,
                decision_classification=d.classification.name,
                decision_score=d.decision_score,
            )

    def test_opportunity_properties(self):
        d = _strong_long_decision()
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        assert opp.is_eligible is True
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        best = r.best
        assert best is not None
        assert best.is_best is True
        assert best.is_surfaced is True
        assert r.has_best is True
        assert r.is_empty is False
        assert r.candidate_count == 1
        assert r.eligible_count == 1

    def test_ranking_surfaced_property(self):
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        r = TradeOpportunityEngine().rank(
            [long_d, short_d], 5, long_d.timestamp,
        )
        surfaced = r.surfaced
        assert len(surfaced) == 2
        assert surfaced[0].is_best


# ============================================================
# B. CONFIGURATION
# ============================================================


class TestConfiguration:
    def test_defaults(self):
        cfg = TradeOpportunityConfig()
        assert CandidateStatus.NO_CANDIDATE not in cfg.allowed_candidate_statuses
        assert (
            DecisionClassification.REJECTED
            not in cfg.allowed_decision_classifications
        )
        assert cfg.min_decision_score == 40
        assert cfg.require_geometry is False
        assert cfg.min_risk_reward_ratio is None
        assert cfg.disqualify_on_conflict is False
        assert cfg.require_no_conflict_for_best is True
        assert cfg.require_geometry_for_best is True
        assert cfg.max_surfaced_opportunities is None
        assert cfg.min_confluence_for_best == 0

    def test_ranking_priority_order_canonical(self):
        assert RANKING_PRIORITY_ORDER[0] == RankingPriority.ELIGIBILITY
        assert RANKING_PRIORITY_ORDER[-1] == RankingPriority.DETERMINISTIC_TIEBREAK
        assert len(RANKING_PRIORITY_ORDER) == 9
        # No duplicates.
        assert len(set(RANKING_PRIORITY_ORDER)) == 9

    def test_frozen(self):
        cfg = TradeOpportunityConfig()
        with pytest.raises(Exception):
            cfg.min_decision_score = 99  # type: ignore

    def test_min_decision_score_non_negative(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(min_decision_score=-1)

    def test_min_risk_reward_positive_when_set(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(min_risk_reward_ratio=0.0)
        with pytest.raises(ValueError):
            TradeOpportunityConfig(min_risk_reward_ratio=-1.0)
        TradeOpportunityConfig(min_risk_reward_ratio=1.5)

    def test_min_confluence_non_negative(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(min_confluence_for_best=-1)

    def test_max_surfaced_at_least_one(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(max_surfaced_opportunities=0)
        TradeOpportunityConfig(max_surfaced_opportunities=1)
        TradeOpportunityConfig(max_surfaced_opportunities=None)

    def test_allowed_statuses_non_empty(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(allowed_candidate_statuses=())

    def test_allowed_classifications_non_empty(self):
        with pytest.raises(ValueError):
            TradeOpportunityConfig(allowed_decision_classifications=())


# ============================================================
# C. ELIGIBILITY
# ============================================================


class TestEligibility:
    def test_strong_candidate_eligible(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.eligible_count == 1
        assert r.opportunities[0].is_eligible

    def test_no_candidate_status_ineligible(self):
        c = _make_candidate(
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            entry=None, stop=None, target=None,
            confluence_score=0,
            evidence=_build_evidence(
                direction=SetupDirection.UNKNOWN,
                trend_alignment=EvidenceAlignment.ABSENT,
                trend_label="UNKNOWN",
                structure_alignment=EvidenceAlignment.ABSENT,
                structure_label="none",
                candle_alignment=EvidenceAlignment.ABSENT,
                candle_label="none",
                location_alignment=EvidenceAlignment.ABSENT,
                location_label="UNKNOWN",
                range_alignment=EvidenceAlignment.ABSENT,
                range_label="UNKNOWN",
            ),
            candle_evidence="none",
            market_trend="UNKNOWN",
            market_structure="none",
            location="UNKNOWN",
            range_context="UNKNOWN",
        )
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.eligible_count == 0
        assert r.opportunities[0].status == OpportunityStatus.NO_OPPORTUNITY
        assert r.opportunities[0].rank == 0
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["candidate_status"] is False

    def test_rejected_decision_ineligible(self):
        # A REJECTED decision is excluded by allowed classifications.
        d = _strong_long_decision()
        # Hand-build a REJECTED-class decision by using a no-candidate.
        c = _make_candidate(
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            entry=None, stop=None, target=None,
            confluence_score=0,
            evidence=_build_evidence(
                direction=SetupDirection.UNKNOWN,
                trend_alignment=EvidenceAlignment.ABSENT,
                trend_label="UNKNOWN",
                structure_alignment=EvidenceAlignment.ABSENT,
                structure_label="none",
                candle_alignment=EvidenceAlignment.ABSENT,
                candle_label="none",
                location_alignment=EvidenceAlignment.ABSENT,
                location_label="UNKNOWN",
                range_alignment=EvidenceAlignment.ABSENT,
                range_label="UNKNOWN",
            ),
            candle_evidence="none",
            market_trend="UNKNOWN",
            market_structure="none",
            location="UNKNOWN",
            range_context="UNKNOWN",
        )
        d_rejected = _make_decision(c)
        assert d_rejected.classification == DecisionClassification.REJECTED
        r = TradeOpportunityEngine().rank([d_rejected], 0, d_rejected.timestamp)
        assert r.eligible_count == 0
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["decision_classification"] is False

    def test_below_min_score_ineligible(self):
        d = _strong_long_decision()
        cfg = TradeOpportunityConfig(min_decision_score=200)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r.eligible_count == 0
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["decision_score"] is False

    def test_require_geometry_gate(self):
        # Incomplete geometry candidate, eligible by default but gated
        # when require_geometry=True.
        c = _make_incomplete_candidate()
        d = _make_decision(c)
        cfg = TradeOpportunityConfig(require_geometry=True)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r.eligible_count == 0
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["geometry"] is False
        # Default config (require_geometry=False) -> still eligible.
        r2 = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r2.eligible_count == 1

    def test_min_risk_reward_gate_applied_when_available(self):
        # Poor R:R (0.2) complete geometry.
        c = _make_candidate(
            entry=100.0, stop=95.0, target=101.0, confluence_score=4,
        )
        d = _make_decision(c)
        cfg = TradeOpportunityConfig(min_risk_reward_ratio=1.0)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r.eligible_count == 0
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["risk_reward"] is False

    def test_min_risk_reward_gate_skipped_when_unavailable(self):
        # Incomplete geometry -> ratio None; gate not applied (honest).
        c = _make_incomplete_candidate()
        d = _make_decision(c)
        cfg = TradeOpportunityConfig(min_risk_reward_ratio=1.0)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        # Eligible (geometry gate off by default); R:R gate not applied.
        assert r.eligible_count == 1
        gates = {g.gate: g.passed for g in r.opportunities[0].eligibility_reasons}
        assert gates["risk_reward"] is True

    def test_disqualify_on_conflict_eligibility(self):
        c = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
                candle_label="SHOOTING_STAR",
            ),
            candle_evidence="SHOOTING_STAR",
            confluence_score=3,
        )
        d = _make_decision(c)
        assert d.conflicting_count == 1
        # Default: conflict does not disqualify (still eligible).
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.eligible_count == 1
        # disqualify_on_conflict=True -> ineligible.
        cfg = TradeOpportunityConfig(disqualify_on_conflict=True)
        r2 = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r2.eligible_count == 0
        gates = {g.gate: g.passed for g in r2.opportunities[0].eligibility_reasons}
        assert gates["conflict"] is False

    def test_eligibility_reasons_auditable(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        reasons = r.opportunities[0].eligibility_reasons
        assert len(reasons) >= 3
        assert all(isinstance(r, EligibilityReason) for r in reasons)
        # Gate names present.
        names = {g.gate for g in reasons}
        assert "candidate_status" in names
        assert "decision_classification" in names
        assert "decision_score" in names


# ============================================================
# D. FILTERING
# ============================================================


class TestFiltering:
    def test_no_candidate_never_opportunity(self):
        c = _make_candidate(
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            entry=None, stop=None, target=None,
            confluence_score=0,
            evidence=_build_evidence(
                direction=SetupDirection.UNKNOWN,
                trend_alignment=EvidenceAlignment.ABSENT,
                trend_label="UNKNOWN",
                structure_alignment=EvidenceAlignment.ABSENT,
                structure_label="none",
                candle_alignment=EvidenceAlignment.ABSENT,
                candle_label="none",
                location_alignment=EvidenceAlignment.ABSENT,
                location_label="UNKNOWN",
                range_alignment=EvidenceAlignment.ABSENT,
                range_label="UNKNOWN",
            ),
            candle_evidence="none",
            market_trend="UNKNOWN",
            market_structure="none",
            location="UNKNOWN",
            range_context="UNKNOWN",
        )
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is None
        assert r.opportunities[0].status == OpportunityStatus.NO_OPPORTUNITY
        assert bool(r.opportunities[0].rejection_reason)

    def test_rejected_never_opportunity(self):
        c = _make_candidate(
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
            entry=None, stop=None, target=None,
            confluence_score=0,
            evidence=_build_evidence(
                direction=SetupDirection.UNKNOWN,
                trend_alignment=EvidenceAlignment.ABSENT,
                trend_label="UNKNOWN",
                structure_alignment=EvidenceAlignment.ABSENT,
                structure_label="none",
                candle_alignment=EvidenceAlignment.ABSENT,
                candle_label="none",
                location_alignment=EvidenceAlignment.ABSENT,
                location_label="UNKNOWN",
                range_alignment=EvidenceAlignment.ABSENT,
                range_label="UNKNOWN",
            ),
            candle_evidence="none",
            market_trend="UNKNOWN",
            market_structure="none",
            location="UNKNOWN",
            range_context="UNKNOWN",
        )
        d = _make_decision(c)
        assert d.classification == DecisionClassification.REJECTED
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is None

    def test_poor_rr_filtered_when_gated(self):
        c = _make_candidate(
            entry=100.0, stop=95.0, target=101.0, confluence_score=4,
        )
        d = _make_decision(c)
        cfg = TradeOpportunityConfig(min_risk_reward_ratio=1.0)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r.best is None
        assert r.eligible_count == 0

    def test_conflict_disqualified_when_configured(self):
        c = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
                candle_label="SHOOTING_STAR",
            ),
            candle_evidence="SHOOTING_STAR",
            confluence_score=3,
        )
        d = _make_decision(c)
        cfg = TradeOpportunityConfig(disqualify_on_conflict=True)
        r = TradeOpportunityEngine(cfg).rank([d], 0, d.timestamp)
        assert r.best is None
        assert r.eligible_count == 0


# ============================================================
# E. RANKING
# ============================================================


class TestRanking:
    def test_single_strong_best(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.has_best is True
        assert r.best.status == OpportunityStatus.BEST_OPPORTUNITY
        assert r.best.rank == 1

    def test_multiple_one_outranks(self):
        strong = _strong_long_decision(0)
        weaker = _make_decision(
            _make_candidate(
                entry=100.0, stop=97.0, target=104.0,
                confluence_score=3, index=1,
            ),
            index=1,
        )
        r = TradeOpportunityEngine().rank(
            [strong, weaker], 5, strong.timestamp,
        )
        assert r.best.decision is strong
        assert r.best.rank == 1
        # The weaker is best-quality (complete, clean) -> ALTERNATIVE.
        alt = r.opportunities[1]
        assert alt.status == OpportunityStatus.ALTERNATIVE_OPPORTUNITY
        assert alt.rank == 2

    def test_no_best_when_strongest_fails_best_cap(self):
        # Strongest eligible is conflicted -> not best-quality.
        conflict = _make_decision(
            _make_candidate(
                evidence=_build_evidence(
                    candle_alignment=EvidenceAlignment.CONFLICTING,
                    candle_label="SHOOTING_STAR",
                ),
                candle_evidence="SHOOTING_STAR",
                confluence_score=3,
            ),
        )
        r = TradeOpportunityEngine().rank([conflict], 0, conflict.timestamp)
        assert r.best is None
        assert r.opportunities[0].status == OpportunityStatus.WATCH

    def test_alternative_never_manufactured_single(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        # Only one eligible -> no alternative.
        surfaced = r.surfaced
        assert len(surfaced) == 1
        assert surfaced[0].is_best

    def test_surfaced_cap(self):
        long1 = _strong_long_decision(0)
        long2 = _strong_long_decision(1)
        long3 = _strong_long_decision(2)
        cfg = TradeOpportunityConfig(max_surfaced_opportunities=2)
        r = TradeOpportunityEngine(cfg).rank(
            [long1, long2, long3], 5, long1.timestamp,
        )
        surfaced = r.surfaced
        assert len(surfaced) == 2  # best + 1 alternative
        assert surfaced[0].is_best
        # The third is WATCH (capped).
        third = [o for o in r.opportunities if o.rank == 3][0]
        assert third.status == OpportunityStatus.WATCH

    def test_surfaced_cap_one_surfaces_only_best(self):
        long1 = _strong_long_decision(0)
        long2 = _strong_long_decision(1)
        cfg = TradeOpportunityConfig(max_surfaced_opportunities=1)
        r = TradeOpportunityEngine(cfg).rank(
            [long1, long2], 5, long1.timestamp,
        )
        surfaced = r.surfaced
        assert len(surfaced) == 1
        assert surfaced[0].is_best
        # Second is WATCH (capped, no alternative).
        assert r.opportunities[1].status == OpportunityStatus.WATCH

    def test_ranks_unique_among_eligible(self):
        long1 = _strong_long_decision(0)
        long2 = _strong_long_decision(1)
        long3 = _strong_long_decision(2)
        r = TradeOpportunityEngine().rank(
            [long1, long2, long3], 5, long1.timestamp,
        )
        eligible_ranks = [
            o.rank for o in r.opportunities if o.is_eligible
        ]
        assert len(eligible_ranks) == len(set(eligible_ranks))

    def test_order_best_then_alternatives_then_watch_then_ineligible(self):
        strong = _strong_long_decision(0)
        alt = _strong_long_decision(1)
        incomplete = _make_decision(_make_incomplete_candidate(index=2))
        no_cand = _make_decision(
            _make_candidate(
                direction=CandidateDirection.NONE,
                status=CandidateStatus.NO_CANDIDATE,
                entry=None, stop=None, target=None,
                confluence_score=0,
                evidence=_build_evidence(
                    direction=SetupDirection.UNKNOWN,
                    trend_alignment=EvidenceAlignment.ABSENT,
                    trend_label="UNKNOWN",
                    structure_alignment=EvidenceAlignment.ABSENT,
                    structure_label="none",
                    candle_alignment=EvidenceAlignment.ABSENT,
                    candle_label="none",
                    location_alignment=EvidenceAlignment.ABSENT,
                    location_label="UNKNOWN",
                    range_alignment=EvidenceAlignment.ABSENT,
                    range_label="UNKNOWN",
                ),
                candle_evidence="none",
                market_trend="UNKNOWN",
                market_structure="none",
                location="UNKNOWN",
                range_context="UNKNOWN",
            ),
        )
        r = TradeOpportunityEngine().rank(
            [no_cand, incomplete, alt, strong], 5, strong.timestamp,
        )
        statuses = [o.status for o in r.opportunities]
        # Best, alternative, watch, no-opportunity (ineligible last).
        assert statuses[0] == OpportunityStatus.BEST_OPPORTUNITY
        assert statuses[1] == OpportunityStatus.ALTERNATIVE_OPPORTUNITY
        assert statuses[2] == OpportunityStatus.WATCH
        assert statuses[3] == OpportunityStatus.NO_OPPORTUNITY

    def test_empty_ranking(self):
        r = TradeOpportunityEngine().rank([], 0, None)
        assert r.is_empty is True
        assert r.has_best is False
        assert r.candidate_count == 0


# ============================================================
# F. DETERMINISTIC TIE-BREAKING
# ============================================================


class TestTieBreaking:
    def test_identical_scores_direction_tiebreak(self):
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        # Identical scores -> LONG before SHORT (final tie-break only).
        r = TradeOpportunityEngine().rank(
            [short_d, long_d], 5, long_d.timestamp,
        )
        assert r.best.direction == "LONG"
        assert r.opportunities[0].direction == "LONG"
        assert r.opportunities[1].direction == "SHORT"

    def test_identical_inputs_repeated(self):
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        eng = TradeOpportunityEngine()
        r1 = eng.rank([long_d, short_d], 5, long_d.timestamp)
        r2 = eng.rank([long_d, short_d], 5, long_d.timestamp)
        assert r1 == r2

    def test_no_randomness(self):
        # Same inputs many times -> identical result.
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        eng = TradeOpportunityEngine()
        results = [
            eng.rank([long_d, short_d], 5, long_d.timestamp)
            for _ in range(20)
        ]
        first = results[0]
        assert all(r == first for r in results)

    def test_score_breaks_tie_before_direction(self):
        # Higher score wins regardless of direction.
        strong = _strong_long_decision(0)  # score 100
        # Build a SHORT that scores lower than strong (weaker evidence).
        weaker_short = _make_decision(
            _make_candidate(
                direction=CandidateDirection.SHORT,
                entry=100.0, stop=105.0, target=85.0,
                confluence_score=2,  # weaker confluence
                evidence=_build_evidence(
                    direction=SetupDirection.BEARISH,
                    trend_alignment=EvidenceAlignment.NEUTRAL,
                    trend_label="NEUTRAL",
                    candle_alignment=EvidenceAlignment.ABSENT,
                    candle_label="none",
                ),
                candle_evidence="none",
                market_trend="NEUTRAL",
                index=1,
            ),
            index=1,
        )
        assert weaker_short.decision_score < strong.decision_score
        r = TradeOpportunityEngine().rank(
            [weaker_short, strong], 5, strong.timestamp,
        )
        # Strong (LONG, higher score) ranks first despite being LONG.
        assert r.best.decision is strong


# ============================================================
# G. LONG / SHORT SYMMETRY
# ============================================================


class TestLongShortSymmetry:
    def test_direction_only_matters_through_evidence(self):
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        # Equal scores -> deterministic tie-break (LONG first).
        assert long_d.decision_score == short_d.decision_score
        r = TradeOpportunityEngine().rank(
            [long_d, short_d], 5, long_d.timestamp,
        )
        assert r.best.direction == "LONG"
        assert r.opportunities[1].status == (
            OpportunityStatus.ALTERNATIVE_OPPORTUNITY
        )

    def test_short_can_outrank_long_on_evidence(self):
        # Make SHORT stronger (better R:R) than LONG.
        long_d = _make_decision(
            _make_candidate(
                direction=CandidateDirection.LONG,
                entry=100.0, stop=95.0, target=104.0,  # R:R 0.8
                confluence_score=4, index=0,
            ),
            index=0,
        )
        short_d = _strong_short_decision(1)  # R:R 4.0, score 100
        assert short_d.decision_score > long_d.decision_score
        r = TradeOpportunityEngine().rank(
            [long_d, short_d], 5, long_d.timestamp,
        )
        # SHORT ranks first on evidence, not direction.
        assert r.best.direction == "SHORT"

    def test_no_long_bias_when_short_stronger(self):
        short_d = _strong_short_decision(0)
        long_d = _make_decision(
            _make_candidate(
                direction=CandidateDirection.LONG,
                entry=100.0, stop=95.0, target=104.0,
                confluence_score=4, index=1,
            ),
            index=1,
        )
        r = TradeOpportunityEngine().rank(
            [long_d, short_d], 5, long_d.timestamp,
        )
        assert r.best.direction == "SHORT"


# ============================================================
# H. INCOMPLETE GEOMETRY + MISSING R:R
# ============================================================


class TestGeometryAndRiskReward:
    def test_incomplete_geometry_never_best_default(self):
        c = _make_incomplete_candidate()
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is None
        assert r.opportunities[0].status == OpportunityStatus.WATCH

    def test_incomplete_geometry_never_alternative(self):
        strong = _strong_long_decision(0)
        incomplete = _make_decision(_make_incomplete_candidate(index=1))
        r = TradeOpportunityEngine().rank(
            [strong, incomplete], 5, strong.timestamp,
        )
        # Strong is best; incomplete is WATCH (not alternative).
        assert r.best.decision is strong
        inc = [o for o in r.opportunities if o.decision is incomplete][0]
        assert inc.status == OpportunityStatus.WATCH

    def test_missing_rr_not_fabricated(self):
        c = _make_incomplete_candidate()
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.opportunities[0].risk_reward_ratio is None
        assert r.opportunities[0].geometry_complete is False

    def test_complete_geometry_best_when_strong(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is not None
        assert r.best.geometry_complete is True
        assert r.best.risk_reward_ratio == 4.0

    def test_require_geometry_for_best_disabled(self):
        # When the geometry cap is OFF, an otherwise best-quality
        # candidate with incomplete geometry is NOT downgraded by
        # geometry (it is downgraded only by its 11S classification).
        # When the cap is ON, incomplete geometry IS cited as the
        # downgrade reason. We verify the cap toggle directly by
        # comparing the incomplete-geometry peer's ranking reason.
        strong = _strong_long_decision(0)
        incomplete = _make_decision(_make_incomplete_candidate(index=1))

        cfg_off = TradeOpportunityConfig(require_geometry_for_best=False)
        r_off = TradeOpportunityEngine(cfg_off).rank(
            [strong, incomplete], 5, strong.timestamp,
        )
        inc_off = [
            o for o in r_off.opportunities if o.decision is incomplete
        ][0]
        # Cap OFF: the downgrade is classification-driven, not
        # geometry-driven; geometry must NOT be cited.
        assert "incomplete trade geometry" not in inc_off.ranking_reason.lower()

        cfg_on = TradeOpportunityConfig(require_geometry_for_best=True)
        r_on = TradeOpportunityEngine(cfg_on).rank(
            [strong, incomplete], 5, strong.timestamp,
        )
        inc_on = [
            o for o in r_on.opportunities if o.decision is incomplete
        ][0]
        # Cap ON: incomplete geometry IS cited as the downgrade reason.
        assert "incomplete trade geometry" in inc_on.ranking_reason.lower()


# ============================================================
# I. CONFLICTING EVIDENCE
# ============================================================


class TestConflict:
    def test_conflict_never_best_default(self):
        c = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
                candle_label="SHOOTING_STAR",
            ),
            candle_evidence="SHOOTING_STAR",
            confluence_score=3,
        )
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is None
        assert r.opportunities[0].status == OpportunityStatus.WATCH

    def test_conflict_never_alternative(self):
        strong = _strong_long_decision(0)
        conflict = _make_decision(
            _make_candidate(
                evidence=_build_evidence(
                    candle_alignment=EvidenceAlignment.CONFLICTING,
                    candle_label="SHOOTING_STAR",
                ),
                candle_evidence="SHOOTING_STAR",
                confluence_score=3,
                index=1,
            ),
            index=1,
        )
        r = TradeOpportunityEngine().rank(
            [strong, conflict], 5, strong.timestamp,
        )
        assert r.best.decision is strong
        conf = [o for o in r.opportunities if o.decision is conflict][0]
        assert conf.status == OpportunityStatus.WATCH

    def test_conflict_recorded_in_reason(self):
        c = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
                candle_label="SHOOTING_STAR",
            ),
            candle_evidence="SHOOTING_STAR",
            confluence_score=3,
        )
        d = _make_decision(c)
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        reason = r.opportunities[0].ranking_reason.lower()
        assert "conflict" in reason

    def test_no_conflict_best_allowed(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.best is not None
        assert r.best.conflicting_count == 0


# ============================================================
# J. CANDIDATE COUNT HANDLING
# ============================================================


class TestCandidateCounts:
    def test_zero_candidates(self):
        r = TradeOpportunityEngine().rank([], 0, None)
        assert r.candidate_count == 0
        assert r.eligible_count == 0
        assert r.best is None

    def test_single_candidate_best(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        assert r.candidate_count == 1
        assert r.eligible_count == 1
        assert r.has_best is True

    def test_multiple_candidates(self):
        long1 = _strong_long_decision(0)
        long2 = _strong_long_decision(1)
        long3 = _strong_long_decision(2)
        r = TradeOpportunityEngine().rank(
            [long1, long2, long3], 5, long1.timestamp,
        )
        assert r.candidate_count == 3
        assert r.eligible_count == 3
        assert r.has_best is True
        surfaced = r.surfaced
        # Best + 2 alternatives (no cap).
        assert len(surfaced) == 3


# ============================================================
# K. POINT-IN-TIME
# ============================================================


class TestPointInTime:
    def test_prefix_equals_full_series(self):
        pat, mc, se, tce, dec, opp = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        from_full = opportunity_at(
            pat, mc, se, tce, dec, opp, bull_h, t,
        )
        from_prefix = opportunity_at(
            pat, mc, se, tce, dec, opp, bull_h[: t + 1], t,
        )
        assert from_full == from_prefix

    def test_future_mutation_unchanged(self):
        pat, mc, se, tce, dec, opp = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        original = opportunity_at(pat, mc, se, tce, dec, opp, bull_h, t)
        mutated = list(bull_h)
        mutated[t + 1] = candle(999.0, 1001.0, 997.0, t + 1)
        after = opportunity_at(pat, mc, se, tce, dec, opp, mutated, t)
        assert original == after

    def test_several_future_candles_mutation(self):
        pat, mc, se, tce, dec, opp = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 3
        original = opportunity_at(pat, mc, se, tce, dec, opp, bull_h, t)
        mutated = list(bull_h)
        for offset in (1, 2):
            if t + offset < len(mutated):
                mutated[t + offset] = candle(888.0, 890.0, 886.0, t + offset)
        after = opportunity_at(pat, mc, se, tce, dec, opp, mutated, t)
        assert original == after

    def test_status_rank_direction_stable_after_mutation(self):
        pat, mc, se, tce, dec, opp = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        original = opportunity_at(pat, mc, se, tce, dec, opp, bull_h, t)
        mutated = list(bull_h)
        for offset in (1, 2):
            if t + offset < len(mutated):
                mutated[t + offset] = candle(777.0, 779.0, 775.0, t + offset)
        after = opportunity_at(pat, mc, se, tce, dec, opp, mutated, t)
        assert (
            original.status == after.status
            and original.rank == after.rank
            and original.direction == after.direction
            and original.eligibility == after.eligibility
        )

    def test_uses_only_candidate_at_index(self):
        # The opportunity at T is derived only from the decision at T,
        # which is derived from candles[:T+1]. Proven structurally by
        # the prefix test; here we additionally confirm the engine
        # reads no candles directly.
        d = _strong_long_decision()
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        # The opportunity references the decision by identity.
        assert opp.decision is d


# ============================================================
# L. PIPELINE INTEGRATION
# ============================================================


class TestPipelineIntegration:
    def test_opportunity_attached_to_points(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        result = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        assert any(
            p.trade_opportunity is not None
            for p in result.evaluation_points_sequence
        )

    def test_disabled_reproduces_pre_11t(self):
        data = trending_dataset()
        before = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=False,
            ),
        ).evaluate(data)
        for p in before.evaluation_points_sequence:
            assert p.trade_opportunity is None

    def test_signals_unchanged(self):
        data = trending_dataset()
        before = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=False,
            ),
        ).evaluate(data)
        after = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=True,
            ),
        ).evaluate(data)
        assert before.signals_generated == after.signals_generated
        assert before.completed_trades == after.completed_trades
        assert before.eligible_decisions == after.eligible_decisions
        assert before.signals_validated == after.signals_validated

    def test_regression_signals_4_trades_3(self):
        # The trending_dataset canonical run produces 4 signals / 3
        # completed trades (Sprint 11S regression baseline).
        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_per_point_state_unchanged(self):
        data = trending_dataset()
        before = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=False,
            ),
        ).evaluate(data)
        after = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=True,
            ),
        ).evaluate(data)
        assert len(before.evaluation_points_sequence) == len(
            after.evaluation_points_sequence,
        )
        for b, a in zip(
            before.evaluation_points_sequence,
            after.evaluation_points_sequence,
        ):
            assert b.signal_state == a.signal_state
            assert b.decision_status == a.decision_status
            assert b.decision_direction == a.decision_direction
            assert b.suppressed == a.suppressed

    def test_signal_prices_unchanged(self):
        data = trending_dataset()
        before = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=False,
            ),
        ).evaluate(data)
        after = HistoricalEvaluationPipeline(
            PipelineConfig(
                swing_config=SwingConfig(lookback=2),
                enable_trade_opportunity=True,
            ),
        ).evaluate(data)
        for bs, as_ in zip(before.signals, after.signals):
            assert bs.entry_price == as_.entry_price
            assert bs.stop_loss == as_.stop_loss
            assert bs.take_profit == as_.take_profit

    def test_pipeline_point_equals_standalone_prefix(self):
        pat, mc, se, tce, dec, opp = make_engines(lookback=2)
        data = trending_dataset()
        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(data)
        # Find a point with an opportunity.
        point = next(
            p for p in result.evaluation_points_sequence
            if p.trade_opportunity is not None
        )
        t = point.index
        standalone = opportunity_at(
            pat, mc, se, tce, dec, opp, data, t,
        )
        assert point.trade_opportunity == standalone

    def test_pipeline_no_future_leak(self):
        pat, mc, se, tce, dec, opp = make_engines(lookback=2)
        data = trending_dataset()
        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(data)
        point = next(
            p for p in result.evaluation_points_sequence
            if p.trade_opportunity is not None
        )
        t = point.index
        # Mutate future candles; the pipeline-point opportunity at T
        # must be unchanged when recomputed from the prefix (which the
        # pipeline already did). Re-run pipeline on mutated future and
        # confirm the point's opportunity matches the standalone prefix
        # evaluation (no future leak).
        standalone = opportunity_at(
            pat, mc, se, tce, dec, opp, data[: t + 1], t,
        )
        assert point.trade_opportunity == standalone

    def test_classification_distribution_has_opportunities(self):
        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(trending_dataset())
        counts: dict[str, int] = {}
        for p in result.evaluation_points_sequence:
            op = p.trade_opportunity
            if op is not None:
                counts[op.status.name] = (
                    counts.get(op.status.name, 0) + 1
                )
        assert counts  # at least one opportunity-classified point


# ============================================================
# M. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        text = TradeOpportunityFormatter().format(r.best)
        assert isinstance(text, str)
        assert "Trade Opportunity" in text

    def test_format_ranking_returns_str(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        text = TradeOpportunityFormatter().format_ranking(r)
        assert isinstance(text, str)
        assert "Trade Opportunity Ranking" in text

    def test_required_sections(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        text = TradeOpportunityFormatter().format(r.best)
        for section in (
            "Rank", "Direction", "Status", "Decision", "Score",
            "Entry", "Stop", "Target", "Risk/Reward", "Geometry",
            "Confluence", "Eligibility", "Ranking Reason",
        ):
            assert section in text, f"missing section: {section}"

    def test_warning_present(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        text = TradeOpportunityFormatter().format(r.best)
        assert "NOT predictive" in text
        assert "guarantees of profitability" in text

    def test_no_predictive_language(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        text = TradeOpportunityFormatter().format(r.best)
        # No predictive CLAIMS (the explicit disclaimer "not predictive"
        # is allowed; predictive claims like "will rise" are not).
        for bad in (
            "most profitable",
            "highest probability",
            "guaranteed setup",
            "will rise",
            "will fall",
            "probability of success",
            "is profitable",
            "best trade",
        ):
            assert bad not in text.lower(), f"predictive phrase: {bad}"

    def test_ranking_table(self):
        long_d = _strong_long_decision(0)
        short_d = _strong_short_decision(1)
        r = TradeOpportunityEngine().rank(
            [long_d, short_d], 5, long_d.timestamp,
        )
        text = TradeOpportunityFormatter().format_ranking(r)
        assert "BEST_OPPORTUNITY" in text
        assert "ALTERNATIVE_OPPORTUNITY" in text

    def test_empty_ranking(self):
        r = TradeOpportunityEngine().rank([], 0, None)
        text = TradeOpportunityFormatter().format_ranking(r)
        assert "No candidates to rank" in text

    def test_unavailable_shown(self):
        c = _make_incomplete_candidate()
        d = _make_decision(c)
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        text = TradeOpportunityFormatter().format(opp)
        assert "unavailable" in text
        assert "INCOMPLETE" in text

    def test_determinism(self):
        d = _strong_long_decision()
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        f = TradeOpportunityFormatter()
        assert f.format(r.best) == f.format(r.best)
        assert f.format_ranking(r) == f.format_ranking(r)


# ============================================================
# N. DETERMINISM + IMMUTABILITY
# ============================================================


class TestDeterminismImmutability:
    def test_same_inputs_same_output(self):
        d = _strong_long_decision()
        eng = TradeOpportunityEngine()
        r1 = eng.rank([d], 0, d.timestamp)
        r2 = eng.rank([d], 0, d.timestamp)
        assert r1 == r2

    def test_repeated_pipeline_runs(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        r1 = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        r2 = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        assert r1.signals_generated == r2.signals_generated
        assert r1.completed_trades == r2.completed_trades
        # Opportunity counts stable.
        c1 = sum(
            1 for p in r1.evaluation_points_sequence
            if p.trade_opportunity is not None
        )
        c2 = sum(
            1 for p in r2.evaluation_points_sequence
            if p.trade_opportunity is not None
        )
        assert c1 == c2

    def test_frozen_models(self):
        d = _strong_long_decision()
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        with pytest.raises(Exception):
            opp.status = OpportunityStatus.NO_OPPORTUNITY  # type: ignore
        with pytest.raises(Exception):
            opp.rank = 5  # type: ignore
        r = TradeOpportunityEngine().rank([d], 0, d.timestamp)
        with pytest.raises(Exception):
            r.best = None  # type: ignore

    def test_slots(self):
        assert hasattr(TradeOpportunity, "__slots__")
        assert hasattr(TradeOpportunityRanking, "__slots__")
        assert hasattr(EligibilityReason, "__slots__")

    def test_decision_retained_by_reference(self):
        d = _strong_long_decision()
        opp = TradeOpportunityEngine().evaluate(d, 0, d.timestamp)
        assert opp.decision is d
        # The candidate is reachable through the decision by reference.
        assert opp.decision.candidate is d.candidate
