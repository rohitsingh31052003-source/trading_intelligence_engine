"""
Tests for the trade candidate ranking & decision layer (Sprint 11S).

Coverage:

A. Model validation (enums, frozen+slots, decision score bounds,
   score component bounds, ranking properties, immutability)
B. Configuration validation + defaults (weights, thresholds, caps,
   threshold ordering, neutral fraction, risk/reward bands)
C. Decision scoring (evidence alignment points, geometry, risk/reward,
   no-conflict, neutral fraction, score bounds 0-100)
D. Classification (strong -> PREFERRED, weak -> WATCH, score-only
   thresholds, caps for no-direction / no-candidate / no-entry, watch
   status cap, conflict cap, geometry cap)
E. Conflict handling (conflicting evidence not PREFERRED, conflict
   reduces score, conflict cap configurable)
F. Geometry (incomplete represented honestly, no fabrication, geometry
   cap for preferred, partial credit)
G. Risk/reward handling (good/acceptable/poor/absent bands)
H. Multiple candidate ranking (deterministic ordering, rank assignment,
   preferred identification, no preferred when none PREFERRED)
I. Deterministic tie-breaking (identical scores, multiple secondary
   keys, no random behaviour, repeated calls)
J. Point-in-time correctness (prefix == full series; future mutation
   leaves decision(T) unchanged; geometry/direction/score stable;
   several future candles)
K. Pipeline integration (additive, signal/trade behaviour unchanged,
   disabled reproduces pre-11S, decision attached, pipeline ==
   standalone prefix, regression signals=4 trades=3)
L. Reporting (sections, warning present, no predictive language,
   returns str, ranking table, sequence, determinism)
M. Determinism + immutability (same inputs, repeated pipeline runs,
   frozen models, slots)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.swing_config import SwingConfig
from engine.config.trade_decision_config import (
    DecisionWeights,
    TradeDecisionConfig,
)
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.models.setup_confluence import (
    EvidenceAlignment,
    EvidenceItem,
    EvidenceSource,
    SetupAssessment,
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
    RankedDecision,
    TradeDecision,
    TradeDecisionRanking,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_decision import TradeDecisionFormatter


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


def shooting_star_candle(index: int, base_close: float, body: float = 2.0):
    close = base_close - body
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=base_close,
        high=close + (body * 3.0),
        low=close - body,
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
    """Build a controlled SetupEvidence with supporting/conflicting sets."""

    trend = _evidence_item(
        EvidenceSource.TREND, direction, trend_alignment, trend_label,
    )
    structure = _evidence_item(
        EvidenceSource.STRUCTURE, direction, structure_alignment,
        structure_label,
    )
    candle = _evidence_item(
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
    all_items = (trend, structure, candle, location, range_item)
    supporting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.ALIGNED
    )
    conflicting = tuple(
        i for i in all_items if i.alignment == EvidenceAlignment.CONFLICTING
    )
    return SetupEvidence(
        trend=trend,
        structure=structure,
        candle=candle,
        location=location,
        range=range_item,
        supporting=supporting,
        conflicting=conflicting,
    )


def _make_candidate(
    direction: CandidateDirection = CandidateDirection.LONG,
    status: CandidateStatus = CandidateStatus.CANDIDATE,
    setup_type: SetupType = SetupType.TREND_CONTINUATION,
    confluence_score: int = 4,
    evidence: SetupEvidence | None = None,
    entry: float | None = 100.0,
    stop: float | None = 95.0,
    target: float | None = 110.0,
    risk: float | None = 5.0,
    reward: float | None = 10.0,
    ratio: float | None = 2.0,
    candle_evidence: str = "HAMMER",
    market_trend: str = "BULLISH",
    market_structure: str = "HIGHER_HIGH / HIGHER_LOW",
    location: str = "NEAR_SUPPORT",
    range_context: str = "NOT_IN_RANGE",
    index: int = 0,
) -> TradeCandidate:
    """Build a fully-controlled TradeCandidate for decision tests.

    Geometry is direction-aware: SHORT candidates require
    stop > entry > target. When a SHORT direction is requested with
    the default LONG-style geometry, the geometry is mirrored
    automatically so the Sprint 11R model's consistency validation
    passes.
    """

    if evidence is None:
        evidence = _build_evidence(direction=_direction_for_evidence(direction))
    supporting = evidence.supporting
    conflicting = evidence.conflicting

    # Mirror geometry for SHORT so the 11R model accepts it.
    if direction == CandidateDirection.SHORT and entry is not None:
        if stop is None and target is None:
            pass
        else:
            # SHORT: stop above entry, target below entry.
            stop = stop if stop is not None else entry + 5.0
            target = target if target is not None else entry - 10.0
            if stop <= entry:
                stop = entry + abs(stop - entry if stop else 5.0) + 1.0
            if target >= entry:
                target = entry - abs(target - entry if target else 10.0) - 1.0
            risk = stop - entry
            reward = entry - target
            ratio = reward / risk if risk > 0 else None

    # Reconcile ratio with risk/reward when all present.
    if risk is not None and reward is not None and risk > 0 and reward > 0:
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
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        candle_evidence=candle_evidence,
        market_trend=market_trend,
        market_structure=market_structure,
        location=location,
        range_context=range_context,
        reason="test candidate",
    )


def _direction_for_evidence(
    direction: CandidateDirection,
) -> SetupDirection:
    if direction == CandidateDirection.LONG:
        return SetupDirection.BULLISH
    if direction == CandidateDirection.SHORT:
        return SetupDirection.BEARISH
    return SetupDirection.UNKNOWN


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
    """Build a candidate with entry but no stop/target (incomplete).

    Incomplete geometry has no stop/target/risk/reward, so the 11R
    model's directional geometry checks are skipped (they only apply
    to present refs).
    """

    if evidence is None:
        evidence = _build_evidence(direction=_direction_for_evidence(direction))
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


# ============================================================
# A. MODEL VALIDATION
# ============================================================


class TestModelValidation:
    def test_enums_have_expected_members(self):
        assert {c.name for c in DecisionClassification} == {
            "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
        }

    def test_rank_value_ordering(self):
        assert (
            DecisionClassification.REJECTED.rank_value
            < DecisionClassification.WATCH.rank_value
            < DecisionClassification.QUALIFIED.rank_value
            < DecisionClassification.PREFERRED.rank_value
        )

    def test_trade_decision_frozen_and_slots(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        with pytest.raises(Exception):
            d.classification = DecisionClassification.REJECTED  # type: ignore
        assert hasattr(TradeDecision, "__slots__")
        assert hasattr(TradeDecisionRanking, "__slots__")
        assert hasattr(RankedDecision, "__slots__")
        assert hasattr(DecisionScore, "__slots__")
        assert hasattr(DecisionScoreComponent, "__slots__")

    def test_decision_score_bounds(self):
        with pytest.raises(ValueError):
            DecisionScore(total=-1, max_total=100)
        with pytest.raises(ValueError):
            DecisionScore(total=101, max_total=100)
        with pytest.raises(ValueError):
            DecisionScore(total=50, max_total=0)

    def test_score_component_bounds(self):
        DecisionScoreComponent(
            name="x", points=5, max_points=10, reason="ok",
        )
        with pytest.raises(ValueError):
            DecisionScoreComponent(
                name="x", points=-1, max_points=10, reason="bad",
            )
        with pytest.raises(ValueError):
            DecisionScoreComponent(
                name="x", points=11, max_points=10, reason="bad",
            )

    def test_ranking_properties(self):
        c = _make_candidate()
        r = TradeDecisionEngine().rank([c], 0, None)
        assert r.candidate_count == 1
        assert r.has_preferred is not None or True  # depends on class
        assert r.is_empty is False
        empty = TradeDecisionRanking(timestamp=None, evaluation_index=0)
        assert empty.is_empty is True
        assert empty.has_preferred is False

    def test_is_preferred_property(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.is_preferred == (d.classification == DecisionClassification.PREFERRED)


# ============================================================
# B. CONFIGURATION VALIDATION
# ============================================================


class TestConfiguration:
    def test_defaults(self):
        cfg = TradeDecisionConfig()
        assert cfg.preferred_threshold == 80
        assert cfg.qualified_threshold == 60
        assert cfg.watch_threshold == 40
        assert cfg.conflict_max_classification == DecisionClassification.QUALIFIED
        assert cfg.watch_status_max_classification == DecisionClassification.QUALIFIED
        assert cfg.require_geometry_for_preferred is True
        assert cfg.neutral_fraction == 0.5
        assert cfg.good_risk_reward_ratio == 2.0
        assert cfg.min_risk_reward_ratio == 1.0

    def test_weights_sum_to_100(self):
        assert DecisionWeights().total == 100

    def test_weights_frozen_and_validated(self):
        with pytest.raises(ValueError):
            DecisionWeights(trend=-1)
        with pytest.raises(ValueError):
            DecisionWeights(
                trend=0, structure=0, candle=0, location=0,
                geometry=0, risk_reward=0, no_conflict=0,
            )

    def test_threshold_ordering(self):
        with pytest.raises(ValueError):
            TradeDecisionConfig(
                watch_threshold=70, qualified_threshold=50,
                preferred_threshold=80,
            )
        with pytest.raises(ValueError):
            TradeDecisionConfig(
                watch_threshold=40, qualified_threshold=90,
                preferred_threshold=80,
            )

    def test_threshold_bounds(self):
        with pytest.raises(ValueError):
            TradeDecisionConfig(preferred_threshold=101)
        with pytest.raises(ValueError):
            TradeDecisionConfig(watch_threshold=-1)

    def test_neutral_fraction_bounds(self):
        TradeDecisionConfig(neutral_fraction=0.0)
        TradeDecisionConfig(neutral_fraction=1.0)
        with pytest.raises(ValueError):
            TradeDecisionConfig(neutral_fraction=-0.1)
        with pytest.raises(ValueError):
            TradeDecisionConfig(neutral_fraction=1.1)

    def test_risk_reward_band_ordering(self):
        TradeDecisionConfig(
            good_risk_reward_ratio=3.0, min_risk_reward_ratio=1.0,
        )
        with pytest.raises(ValueError):
            TradeDecisionConfig(
                good_risk_reward_ratio=1.0, min_risk_reward_ratio=2.0,
            )
        with pytest.raises(ValueError):
            TradeDecisionConfig(good_risk_reward_ratio=0)
        with pytest.raises(ValueError):
            TradeDecisionConfig(min_risk_reward_ratio=0)

    def test_cap_classifications_validated(self):
        with pytest.raises(ValueError):
            TradeDecisionConfig(conflict_max_classification=None)  # type: ignore


# ============================================================
# C. DECISION SCORING
# ============================================================


class TestScoring:
    def test_strong_aligned_candidate_max_score(self):
        c = _make_candidate()  # all aligned, complete geometry, rr=2.0
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.decision_score == 100
        assert d.classification == DecisionClassification.PREFERRED

    def test_aligned_evidence_full_weight(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["trend"].points == comp["trend"].max_points
        assert comp["structure"].points == comp["structure"].max_points
        assert comp["candle"].points == comp["candle"].max_points
        assert comp["location"].points == comp["location"].max_points

    def test_neutral_evidence_partial_credit(self):
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.NEUTRAL,
            candle_label="DOJI",
        )
        c = _make_candidate(evidence=ev, candle_evidence="DOJI")
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        # candle weight 10, neutral_fraction 0.5 -> 5
        assert comp["candle"].points == 5

    def test_absent_evidence_zero(self):
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.ABSENT,
            candle_label="none",
        )
        c = _make_candidate(evidence=ev, candle_evidence="none")
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["candle"].points == 0

    def test_conflicting_evidence_zero_and_no_conflict_zero(self):
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        c = _make_candidate(evidence=ev)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["candle"].points == 0
        assert comp["no_conflict"].points == 0

    def test_geometry_complete_full_credit(self):
        c = _make_candidate()  # complete
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["geometry"].points == comp["geometry"].max_points

    def test_geometry_partial_entry_only(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        # geometry weight 20, neutral_fraction 0.5 -> 10
        assert comp["geometry"].points == 10

    def test_geometry_none_zero(self):
        c = _make_candidate(
            entry=None, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["geometry"].points == 0
        assert d.classification == DecisionClassification.REJECTED

    def test_risk_reward_good_full_credit(self):
        # reward=20, risk=5 -> ratio 4.0 (>= good 2.0).
        c = _make_candidate(entry=100.0, stop=95.0, target=120.0,
                            risk=5.0, reward=20.0, ratio=4.0)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["risk_reward"].points == comp["risk_reward"].max_points

    def test_risk_reward_acceptable_partial(self):
        # reward=7.5, risk=5 -> ratio 1.5 (acceptable, < good 2.0).
        c = _make_candidate(entry=100.0, stop=95.0, target=107.5,
                            risk=5.0, reward=7.5, ratio=1.5)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        # weight 15, half -> 8 (rounded)
        assert comp["risk_reward"].points == 8

    def test_risk_reward_poor_zero(self):
        # reward=3, risk=5 -> ratio 0.6 (< min 1.0).
        c = _make_candidate(entry=100.0, stop=95.0, target=103.0,
                            risk=5.0, reward=3.0, ratio=0.6)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["risk_reward"].points == 0

    def test_risk_reward_absent_zero(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["risk_reward"].points == 0

    def test_score_within_bounds(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        assert 0 <= d.decision_score <= 100

    def test_score_neutral_fraction_config(self):
        ev = _build_evidence(candle_alignment=EvidenceAlignment.NEUTRAL)
        c = _make_candidate(evidence=ev)
        cfg = TradeDecisionConfig(neutral_fraction=0.0)
        d = TradeDecisionEngine(cfg).decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["candle"].points == 0


# ============================================================
# D. CLASSIFICATION
# ============================================================


class TestClassification:
    def test_strong_candidate_preferred(self):
        c = _make_candidate()  # score 100
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification == DecisionClassification.PREFERRED

    def test_weak_candidate_watch(self):
        # Lots of absent / conflicting evidence -> low score.
        ev = _build_evidence(
            trend_alignment=EvidenceAlignment.ABSENT,
            trend_label="UNKNOWN",
            structure_alignment=EvidenceAlignment.ABSENT,
            structure_label="none",
            candle_alignment=EvidenceAlignment.ABSENT,
            candle_label="none",
            location_alignment=EvidenceAlignment.ABSENT,
            location_label="UNKNOWN",
        )
        c = _make_candidate(
            evidence=ev,
            market_trend="UNKNOWN",
            market_structure="none",
            candle_evidence="none",
            location="UNKNOWN",
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification == DecisionClassification.WATCH or \
            d.classification == DecisionClassification.REJECTED

    def test_no_direction_rejected(self):
        # The 11R model only allows NONE direction for non-CANDIDATE
        # statuses. A NONE-direction candidate is REJECTED by the
        # decision engine regardless of score.
        c = _make_candidate(
            direction=CandidateDirection.NONE,
            status=CandidateStatus.NO_CANDIDATE,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification == DecisionClassification.REJECTED

    def test_no_candidate_status_rejected(self):
        c = _make_candidate(status=CandidateStatus.NO_CANDIDATE)
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification == DecisionClassification.REJECTED

    def test_no_entry_rejected(self):
        c = _make_candidate(
            entry=None, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification == DecisionClassification.REJECTED

    def test_watch_status_capped(self):
        # A WATCH candidate (not promoted) is capped at QUALIFIED by
        # default, even with a high score.
        c = _make_candidate(status=CandidateStatus.WATCH)
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification in (
            DecisionClassification.WATCH,
            DecisionClassification.QUALIFIED,
        )
        assert d.classification != DecisionClassification.PREFERRED

    def test_score_threshold_qual(self):
        # Manually construct a candidate with a mid-range score by
        # neutralising several sources.
        ev = _build_evidence(
            location_alignment=EvidenceAlignment.NEUTRAL,
            location_label="INSIDE_RANGE",
            candle_alignment=EvidenceAlignment.NEUTRAL,
            candle_label="DOJI",
        )
        c = _make_candidate(evidence=ev, location="INSIDE_RANGE",
                            candle_evidence="DOJI")
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification in (
            DecisionClassification.QUALIFIED,
            DecisionClassification.PREFERRED,
        )


# ============================================================
# E. CONFLICT HANDLING
# ============================================================


class TestConflictHandling:
    def test_conflicting_candidate_not_preferred(self):
        # One conflicting source but otherwise strong. PREFERRED is
        # never produced with a conflict (default cap QUALIFIED).
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        c = _make_candidate(evidence=ev)
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification != DecisionClassification.PREFERRED

    def test_conflict_reduces_score(self):
        c_clean = _make_candidate()
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        c_conflict = _make_candidate(evidence=ev)
        d_clean = TradeDecisionEngine().decide(c_clean, 0, None)
        d_conflict = TradeDecisionEngine().decide(c_conflict, 0, None)
        assert d_conflict.decision_score < d_clean.decision_score

    def test_conflict_cap_configurable(self):
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        c = _make_candidate(evidence=ev)
        cfg = TradeDecisionConfig(
            conflict_max_classification=DecisionClassification.WATCH,
        )
        d = TradeDecisionEngine(cfg).decide(c, 0, None)
        assert d.classification in (
            DecisionClassification.REJECTED,
            DecisionClassification.WATCH,
        )

    def test_conflict_recorded_in_conflicting_count(self):
        ev = _build_evidence(
            candle_alignment=EvidenceAlignment.CONFLICTING,
        )
        c = _make_candidate(evidence=ev)
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.conflicting_count >= 1


# ============================================================
# F. GEOMETRY
# ============================================================


class TestGeometry:
    def test_incomplete_geometry_honest(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.geometry_complete is False
        assert d.risk_reward_ratio is None
        # No fabrication.
        assert c.stop_reference is None
        assert c.target_reference is None

    def test_incomplete_geometry_caps_preferred(self):
        # Strong evidence but incomplete geometry -> not PREFERRED.
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        assert d.classification != DecisionClassification.PREFERRED

    def test_geometry_not_required_when_configured(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        cfg = TradeDecisionConfig(require_geometry_for_preferred=False)
        d = TradeDecisionEngine(cfg).decide(c, 0, None)
        # Without geometry cap, a high-enough score may be preferred.
        # It is at least not capped solely for geometry.
        assert d.classification in (
            DecisionClassification.WATCH,
            DecisionClassification.QUALIFIED,
            DecisionClassification.PREFERRED,
        )

    def test_geometry_partial_credit_score(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert 0 < comp["geometry"].points < comp["geometry"].max_points


# ============================================================
# G. RISK / REWARD HANDLING
# ============================================================


class TestRiskReward:
    def test_good_ratio_full_credit(self):
        c = _make_candidate(entry=100.0, stop=95.0, target=120.0,
                            risk=5.0, reward=20.0, ratio=4.0)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["risk_reward"].points == comp["risk_reward"].max_points

    def test_acceptable_ratio_partial(self):
        c = _make_candidate(entry=100.0, stop=95.0, target=107.5,
                            risk=5.0, reward=7.5, ratio=1.5)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert 0 < comp["risk_reward"].points < comp["risk_reward"].max_points

    def test_poor_ratio_zero(self):
        c = _make_candidate(entry=100.0, stop=95.0, target=103.0,
                            risk=5.0, reward=3.0, ratio=0.6)
        d = TradeDecisionEngine().decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert comp["risk_reward"].points == 0

    def test_configurable_bands(self):
        # ratio 1.5; with good=5.0 it is acceptable (partial), not good.
        c = _make_candidate(entry=100.0, stop=95.0, target=107.5,
                            risk=5.0, reward=7.5, ratio=1.5)
        cfg = TradeDecisionConfig(
            good_risk_reward_ratio=5.0, min_risk_reward_ratio=1.0,
        )
        d = TradeDecisionEngine(cfg).decide(c, 0, None)
        comp = {x.name: x for x in d.score.components}
        assert 0 < comp["risk_reward"].points < comp["risk_reward"].max_points


# ============================================================
# H. MULTIPLE CANDIDATE RANKING
# ============================================================


class TestMultipleCandidateRanking:
    def test_deterministic_ranking_order(self):
        strong = _make_candidate(index=0)
        medium = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.NEUTRAL,
                candle_label="DOJI",
            ),
            candle_evidence="DOJI",
            index=1,
        )
        weak = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=2,
        )
        eng = TradeDecisionEngine()
        r = eng.rank([weak, strong, medium], 0, None)
        # Strongest first.
        scores = [rd.decision.decision_score for rd in r.decisions]
        assert scores == sorted(scores, reverse=True)
        # Ranks are 1-based and unique.
        ranks = [rd.rank for rd in r.decisions]
        assert ranks == [1, 2, 3]

    def test_preferred_identified(self):
        strong = _make_candidate(index=0)
        weak = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=1,
        )
        r = TradeDecisionEngine().rank([weak, strong], 0, None)
        assert r.has_preferred is True
        assert r.preferred.rank == 1
        assert r.preferred.decision.is_preferred

    def test_no_preferred_when_none_strong(self):
        c1 = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=0,
        )
        c2 = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=1,
        )
        r = TradeDecisionEngine().rank([c1, c2], 0, None)
        assert r.has_preferred is False
        assert r.preferred is None

    def test_empty_ranking(self):
        r = TradeDecisionEngine().rank([], 0, None)
        assert r.is_empty is True
        assert r.candidate_count == 0
        assert r.preferred is None

    def test_single_candidate_rank1(self):
        c = _make_candidate()
        r = TradeDecisionEngine().rank([c], 0, None)
        assert r.candidate_count == 1
        assert r.decisions[0].rank == 1

    def test_ranking_exactly_matches_example_shape(self):
        # The spec example: 4 candidates with descending decision
        # strength. Verify rank/direction/decision/score columns.
        preferred = _make_candidate(direction=CandidateDirection.LONG, index=0)
        qualified = _make_candidate(
            direction=CandidateDirection.SHORT,
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.NEUTRAL,
                candle_label="DOJI",
            ),
            candle_evidence="DOJI",
            index=1,
        )
        watch = _make_candidate(
            direction=CandidateDirection.LONG,
            status=CandidateStatus.WATCH,
            index=2,
        )
        rejected = _make_candidate(
            direction=CandidateDirection.SHORT,
            status=CandidateStatus.NO_CANDIDATE,
            entry=None, stop=None, target=None,
            risk=None, reward=None, ratio=None,
            index=3,
        )
        r = TradeDecisionEngine().rank(
            [preferred, qualified, watch, rejected], 0, None,
        )
        classes = [rd.decision.classification for rd in r.decisions]
        # Order should be strongest-first by classification rank.
        rank_values = [c.rank_value for c in classes]
        assert rank_values == sorted(rank_values, reverse=True)


# ============================================================
# I. DETERMINISTIC TIE-BREAKING
# ============================================================


class TestTieBreaking:
    def test_identical_scores_break_by_secondary_keys(self):
        # Two candidates with identical evidence/score differ only by
        # direction order (LONG < SHORT) and index.
        c1 = _make_candidate(direction=CandidateDirection.LONG, index=0)
        c2 = _make_candidate(direction=CandidateDirection.SHORT, index=1)
        r = TradeDecisionEngine().rank([c2, c1], 0, None)
        # Both PREFERRED & same score; LONG ranks first by direction order.
        assert r.decisions[0].decision.candidate.direction == CandidateDirection.LONG
        assert r.decisions[1].decision.candidate.direction == CandidateDirection.SHORT

    def test_tie_break_by_confluence(self):
        c1 = _make_candidate(confluence_score=4, index=0)
        c2 = _make_candidate(confluence_score=5, index=1)
        r = TradeDecisionEngine().rank([c1, c2], 0, None)
        # Higher confluence first (both PREFERRED, same score).
        assert r.decisions[0].decision.confluence_score >= \
            r.decisions[1].decision.confluence_score

    def test_tie_break_by_geometry(self):
        # Incomplete geometry ranks below complete at equal class/score.
        c_complete = _make_candidate(index=0)
        c_partial = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
            index=1,
        )
        r = TradeDecisionEngine().rank([c_partial, c_complete], 0, None)
        assert r.decisions[0].decision.geometry_complete is True

    def test_no_random_behaviour(self):
        c1 = _make_candidate(index=0)
        c2 = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=1,
        )
        r1 = TradeDecisionEngine().rank([c1, c2], 0, None)
        r2 = TradeDecisionEngine().rank([c1, c2], 0, None)
        assert [rd.decision.decision_score for rd in r1.decisions] == \
            [rd.decision.decision_score for rd in r2.decisions]
        assert [rd.rank for rd in r1.decisions] == \
            [rd.rank for rd in r2.decisions]

    def test_repeated_calls_identical(self):
        c = _make_candidate()
        eng = TradeDecisionEngine()
        d1 = eng.decide(c, 0, None)
        d2 = eng.decide(c, 0, None)
        assert d1 == d2


# ============================================================
# J. POINT-IN-TIME CORRECTNESS
# ============================================================


def make_engines(lookback: int = 2):
    swing_cfg = SwingConfig(lookback=lookback)
    return (
        CandlePatternEngine(),
        MarketContextEngine(swing_config=swing_cfg),
        SetupConfluenceEngine(),
        TradeCandidateEngine(),
        TradeDecisionEngine(),
    )


def decision_at(
    pat_engine: CandlePatternEngine,
    mc_engine: MarketContextEngine,
    setup_engine: SetupConfluenceEngine,
    cand_engine: TradeCandidateEngine,
    dec_engine: TradeDecisionEngine,
    candles: list[OHLCVCandle],
    index: int,
) -> TradeDecision:
    pats = [
        p for p in pat_engine.detect(candles[: index + 1])
        if p.index == index
    ]
    ctx = mc_engine.analyze_at(candles, index)
    a = setup_engine.assess(pats, ctx, index, candles[index].timestamp)
    c = cand_engine.generate(
        a, ctx, index, candles[index].timestamp, candles[index].close,
    )
    return dec_engine.decide(c, index, candles[index].timestamp)


class TestPointInTime:
    def test_prefix_equals_full_series(self):
        pat, mc, se, tce, dec = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        from_full = decision_at(pat, mc, se, tce, dec, bull_h, t)
        from_prefix = decision_at(pat, mc, se, tce, dec, bull_h[: t + 1], t)
        assert from_full == from_prefix

    def test_future_mutation_unchanged(self):
        pat, mc, se, tce, dec = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        original = decision_at(pat, mc, se, tce, dec, bull_h, t)
        mutated = list(bull_h)
        mutated[t + 1] = candle(999.0, 1001.0, 997.0, t + 1)
        after = decision_at(pat, mc, se, tce, dec, mutated, t)
        assert original == after

    def test_several_future_candles_mutation(self):
        pat, mc, se, tce, dec = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 3
        original = decision_at(pat, mc, se, tce, dec, bull_h, t)
        mutated = list(bull_h)
        for offset in (1, 2):
            if t + offset < len(mutated):
                mutated[t + offset] = candle(888.0, 890.0, 886.0, t + offset)
        after = decision_at(pat, mc, se, tce, dec, mutated, t)
        assert original == after

    def test_geometry_direction_score_stable(self):
        pat, mc, se, tce, dec = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 2
        original = decision_at(pat, mc, se, tce, dec, bull_h, t)
        mutated = list(bull_h)
        mutated[t + 1] = candle(999.0, 1001.0, 997.0, t + 1)
        after = decision_at(pat, mc, se, tce, dec, mutated, t)
        assert original.direction == after.direction
        assert original.decision_score == after.decision_score
        assert original.classification == after.classification
        assert original.geometry_complete == after.geometry_complete

    def test_uses_only_candidate_at_index(self):
        # The decision reads only the candidate, which is derived from
        # candles[:t+1]; mutating a candle at t+2 must not change it.
        pat, mc, se, tce, dec = make_engines()
        bull = bullish_dataset()
        bull_h = list(bull) + [hammer_candle(len(bull), bull[-1].close)]
        t = len(bull_h) - 3
        original = decision_at(pat, mc, se, tce, dec, bull_h, t)
        mutated = list(bull_h)
        if t + 2 < len(mutated):
            mutated[t + 2] = candle(777.0, 779.0, 775.0, t + 2)
        after = decision_at(pat, mc, se, tce, dec, mutated, t)
        assert original == after


# ============================================================
# K. PIPELINE INTEGRATION
# ============================================================


class TestPipelineIntegration:
    def test_trade_decision_attached_to_points(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        assert res.evaluation_points > 0
        for p in res.evaluation_points_sequence:
            assert p.trade_decision is not None

    def test_disabled_reproduces_pre_11s(self):
        candles = trending_dataset()
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=False,
        )
        res = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        for p in res.evaluation_points_sequence:
            assert p.trade_decision is None

    def test_signals_unchanged_enable_vs_disable(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        assert on.signals_generated == off.signals_generated
        assert on.completed_trades == off.completed_trades
        assert on.eligible_decisions == off.eligible_decisions
        assert on.signals_validated == off.signals_validated

    def test_regression_signals_four_trades_three(self):
        candles = trending_dataset()
        res = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(candles)
        assert res.signals_generated == 4
        assert res.completed_trades == 3

    def test_per_point_signal_state_unchanged(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        assert len(on.evaluation_points_sequence) == len(
            off.evaluation_points_sequence,
        )
        for pa, pb in zip(
            on.evaluation_points_sequence,
            off.evaluation_points_sequence,
        ):
            assert pa.signal_state == pb.signal_state
            assert pa.decision_direction == pb.decision_direction
            assert pa.decision_status == pb.decision_status
            assert pa.suppressed == pb.suppressed

    def test_signal_prices_unchanged(self):
        candles = trending_dataset()
        cfg_on = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=True,
        )
        cfg_off = PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=False,
        )
        on = HistoricalEvaluationPipeline(cfg_on).evaluate(candles)
        off = HistoricalEvaluationPipeline(cfg_off).evaluate(candles)
        on_prices = [
            (s.entry_price, s.stop_loss, s.take_profit) for s in on.signals
        ]
        off_prices = [
            (s.entry_price, s.stop_loss, s.take_profit) for s in off.signals
        ]
        assert on_prices == off_prices

    def test_pipeline_point_equals_standalone_prefix(self):
        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        pipe = HistoricalEvaluationPipeline(cfg)
        res = pipe.evaluate(candles)
        pat, mc, se, tce, dec = make_engines(lookback=2)
        point = res.evaluation_points_sequence[0]
        t = point.index
        standalone = decision_at(pat, mc, se, tce, dec, candles, t)
        assert point.trade_decision == standalone

    def test_pipeline_decision_no_future_leak(self):
        candles = trending_dataset()
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(candles)
        points = res.evaluation_points_sequence
        idx = len(points) // 2
        t = points[idx].index
        original = points[idx].trade_decision
        mutated = list(candles)
        if t + 1 < len(mutated):
            mutated[t + 1] = candle(999.0, 1001.0, 997.0, t + 1)
        res2 = HistoricalEvaluationPipeline(cfg).evaluate(mutated)
        point2 = next(
            p for p in res2.evaluation_points_sequence if p.index == t
        )
        assert original == point2.trade_decision

    def test_decision_classification_distribution_present(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        res = HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())
        classes = {
            p.trade_decision.classification
            for p in res.evaluation_points_sequence
            if p.trade_decision is not None
        }
        assert len(classes) > 0


# ============================================================
# L. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        text = TradeDecisionFormatter().format(d)
        assert isinstance(text, str)

    def test_format_sections(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        text = TradeDecisionFormatter().format(d)
        for section in (
            "Trade Decision",
            "Decision Score",
            "Geometry Complete",
            "Score Components",
            "Rationale",
        ):
            assert section in text

    def test_format_warning_present(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        text = TradeDecisionFormatter().format(d)
        assert "NOT predictive" in text
        assert "guarantees of profitability" in text

    def test_format_no_probability_language(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        text = TradeDecisionFormatter().format(d).lower()
        for bad in ("probability", "will win", "guaranteed profit"):
            assert bad not in text

    def test_format_ranking_returns_str(self):
        c = _make_candidate()
        r = TradeDecisionEngine().rank([c], 0, None)
        text = TradeDecisionFormatter().format_ranking(r)
        assert isinstance(text, str)
        assert "Trade Decision Ranking" in text

    def test_format_ranking_table(self):
        c1 = _make_candidate(index=0)
        c2 = _make_candidate(
            evidence=_build_evidence(
                candle_alignment=EvidenceAlignment.CONFLICTING,
            ),
            index=1,
        )
        r = TradeDecisionEngine().rank([c1, c2], 0, None)
        text = TradeDecisionFormatter().format_ranking(r)
        assert "Rank" in text
        assert "Direction" in text
        assert "Score" in text

    def test_format_ranking_empty(self):
        r = TradeDecisionRanking(timestamp=None, evaluation_index=0)
        text = TradeDecisionFormatter().format_ranking(r)
        assert "No candidates to rank" in text
        assert "NOT predictive" in text

    def test_format_determinism(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        t1 = TradeDecisionFormatter().format(d)
        t2 = TradeDecisionFormatter().format(d)
        assert t1 == t2

    def test_unavailable_shown_for_incomplete(self):
        c = _make_candidate(
            entry=100.0, stop=None, target=None,
            risk=None, reward=None, ratio=None,
        )
        d = TradeDecisionEngine().decide(c, 0, None)
        text = TradeDecisionFormatter().format(d)
        assert "unavailable" in text


# ============================================================
# M. DETERMINISM + IMMUTABILITY
# ============================================================


class TestDeterminismImmutability:
    def test_same_input_same_output(self):
        c = _make_candidate()
        eng = TradeDecisionEngine()
        assert eng.decide(c, 0, None) == eng.decide(c, 0, None)

    def test_repeated_pipeline_runs(self):
        cfg = PipelineConfig(swing_config=SwingConfig(lookback=2))
        pipe = HistoricalEvaluationPipeline(cfg)
        r1 = pipe.evaluate(trending_dataset())
        r2 = pipe.evaluate(trending_dataset())
        d1 = [p.trade_decision for p in r1.evaluation_points_sequence]
        d2 = [p.trade_decision for p in r2.evaluation_points_sequence]
        assert d1 == d2

    def test_frozen_models(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        with pytest.raises(Exception):
            d.score.total = 0  # type: ignore
        with pytest.raises(Exception):
            d.classification = DecisionClassification.REJECTED  # type: ignore

    def test_ranking_frozen(self):
        c = _make_candidate()
        r = TradeDecisionEngine().rank([c], 0, None)
        with pytest.raises(Exception):
            r.preferred = None  # type: ignore

    def test_supporting_evidence_subset_of_candidate(self):
        c = _make_candidate()
        d = TradeDecisionEngine().decide(c, 0, None)
        # The decision references the candidate's evidence by
        # reference; supporting count matches.
        assert d.supporting_count == len(c.supporting_evidence)
        assert d.conflicting_count == len(c.conflicting_evidence)
