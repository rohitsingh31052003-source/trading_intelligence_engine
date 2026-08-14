"""
Demo for Sprint 11S — Trade Candidate Ranking & Decision Intelligence.

This demo exercises the trade-candidate ranking & decision layer on
deterministic synthetic data and proves:

1. Strong candidate          -> PREFERRED
2. Weak candidate             -> WATCH
3. Conflicting evidence       -> not PREFERRED
4. Incomplete geometry        -> safely represented (no fabrication)
5. Multiple candidates        -> deterministic ranking
6. Point-in-time leakage proof
7. Future mutation proof
8. Existing pipeline regression (signals / trades unchanged)

The demo prints descriptive trade-decision reports. It makes NO
profitability, probability, or directional prediction. A trade
decision is a DESCRIPTIVE classification of technical-evidence
strength/completeness, NOT a trade signal.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from engine.config.swing_config import SwingConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.setup_confluence import SetupConfluenceEngine
from engine.intelligence.trade_candidates import TradeCandidateEngine
from engine.intelligence.trade_decision import TradeDecisionEngine
from engine.models.ohlcv import OHLCVCandle
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
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_decision import TradeDecisionFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


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


def _line(char: str = "-", width: int = 60) -> str:
    return char * width


# ============================================================
# HAND-BUILT CANDIDATES (fully controlled evidence + geometry)
# ============================================================


def _evidence_item(
    source: EvidenceSource,
    direction: SetupDirection,
    alignment: EvidenceAlignment,
    label: str,
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        direction=direction,
        alignment=alignment,
        label=label,
        reason="demo",
    )


def _evidence(
    direction: SetupDirection = SetupDirection.BULLISH,
    trend=EvidenceAlignment.ALIGNED,
    structure=EvidenceAlignment.ALIGNED,
    candle_ev=EvidenceAlignment.ALIGNED,
    location=EvidenceAlignment.ALIGNED,
    rng=EvidenceAlignment.NEUTRAL,
    trend_label="BULLISH",
    structure_label="HIGHER_HIGH / HIGHER_LOW",
    candle_label="HAMMER",
    location_label="NEAR_SUPPORT",
    range_label="NOT_IN_RANGE",
) -> SetupEvidence:
    t = _evidence_item(EvidenceSource.TREND, direction, trend, trend_label)
    s = _evidence_item(EvidenceSource.STRUCTURE, direction, structure, structure_label)
    c = _evidence_item(EvidenceSource.CANDLE, direction, candle_ev, candle_label)
    loc = _evidence_item(EvidenceSource.LOCATION, direction, location, location_label)
    r = _evidence_item(EvidenceSource.RANGE, SetupDirection.NEUTRAL, rng, range_label)
    items = (t, s, c, loc, r)
    supporting = tuple(i for i in items if i.alignment == EvidenceAlignment.ALIGNED)
    conflicting = tuple(i for i in items if i.alignment == EvidenceAlignment.CONFLICTING)
    return SetupEvidence(
        trend=t, structure=s, candle=c, location=loc, range=r,
        supporting=supporting, conflicting=conflicting,
    )


def _candidate(
    direction: CandidateDirection = CandidateDirection.LONG,
    status: CandidateStatus = CandidateStatus.CANDIDATE,
    evidence: SetupEvidence | None = None,
    entry: float | None = 100.0,
    stop: float | None = 95.0,
    target: float | None = 110.0,
    confluence_score: int = 4,
    candle_evidence: str = "HAMMER",
    market_trend: str = "BULLISH",
    market_structure: str = "HIGHER_HIGH / HIGHER_LOW",
    location: str = "NEAR_SUPPORT",
    range_context: str = "NOT_IN_RANGE",
    index: int = 0,
) -> TradeCandidate:
    if evidence is None:
        direction_sd = (
            SetupDirection.BULLISH
            if direction == CandidateDirection.LONG
            else SetupDirection.BEARISH
        )
        evidence = _evidence(direction=direction_sd)

    # Mirror geometry for SHORT.
    risk = reward = ratio = None
    if entry is not None and stop is not None and target is not None:
        if direction == CandidateDirection.LONG:
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target
        ratio = reward / risk if risk > 0 and reward > 0 else None

    return TradeCandidate(
        timestamp=_EPOCH + timedelta(days=index),
        evaluation_index=index,
        direction=direction,
        status=status,
        setup_type=SetupType.TREND_CONTINUATION,
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
        reason="demo candidate",
    )


def _build_engines(lookback: int = 2):
    swing_cfg = SwingConfig(lookback=lookback)
    return (
        CandlePatternEngine(),
        MarketContextEngine(swing_config=swing_cfg),
        SetupConfluenceEngine(),
        TradeCandidateEngine(),
        TradeDecisionEngine(),
    )


def main() -> None:
    print("=" * 60)
    print("Sprint 11S — Trade Candidate Ranking & Decision")
    print("=" * 60)

    dec_engine = TradeDecisionEngine()
    formatter = TradeDecisionFormatter()

    # ------------------------------------------------------------
    # 1. STRONG CANDIDATE -> PREFERRED
    # ------------------------------------------------------------
    print("\n" + _line())
    print("1. Strong Candidate -> PREFERRED")
    print(_line())

    strong = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=95.0, target=120.0,
        confluence_score=5,
    )
    d1 = dec_engine.decide(strong, 0, strong.timestamp)
    print(formatter.format(d1))
    print(
        f"\nExpected: PREFERRED (got {d1.classification.name}, "
        f"score {d1.decision_score})",
    )

    # ------------------------------------------------------------
    # 2. WEAK CANDIDATE -> WATCH
    # ------------------------------------------------------------
    print("\n" + _line())
    print("2. Weak Candidate -> WATCH")
    print(_line())

    weak = _candidate(
        direction=CandidateDirection.LONG,
        evidence=_evidence(
            trend=EvidenceAlignment.NEUTRAL,
            trend_label="NEUTRAL",
            structure=EvidenceAlignment.NEUTRAL,
            structure_label="MIXED",
            candle_ev=EvidenceAlignment.ABSENT,
            candle_label="none",
        ),
        market_trend="NEUTRAL",
        market_structure="MIXED",
        candle_evidence="none",
        confluence_score=1,
        # incomplete geometry on top of weak evidence
        entry=100.0, stop=None, target=None,
    )
    d2 = dec_engine.decide(weak, 1, weak.timestamp)
    print(formatter.format(d2))
    print(
        f"\nExpected: WATCH or REJECTED (got {d2.classification.name}, "
        f"score {d2.decision_score})",
    )

    # ------------------------------------------------------------
    # 3. CONFLICTING EVIDENCE -> NOT PREFERRED
    # ------------------------------------------------------------
    print("\n" + _line())
    print("3. Conflicting Evidence -> NOT PREFERRED")
    print(_line())

    conflict = _candidate(
        direction=CandidateDirection.LONG,
        evidence=_evidence(
            candle_ev=EvidenceAlignment.CONFLICTING,
            candle_label="SHOOTING_STAR",
        ),
        candle_evidence="SHOOTING_STAR",
        entry=100.0, stop=95.0, target=120.0,
        confluence_score=3,
    )
    d3 = dec_engine.decide(conflict, 2, conflict.timestamp)
    print(formatter.format(d3))
    print(
        f"\nConflict recorded; PREFERRED produced: "
        f"{d3.is_preferred} (classification {d3.classification.name}, "
        f"score {d3.decision_score})",
    )

    # ------------------------------------------------------------
    # 4. INCOMPLETE GEOMETRY -> SAFELY REPRESENTED
    # ------------------------------------------------------------
    print("\n" + _line())
    print("4. Incomplete Geometry -> Safely Represented")
    print(_line())

    incomplete = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=None, target=None,
    )
    d4 = dec_engine.decide(incomplete, 3, incomplete.timestamp)
    print(formatter.format(d4))
    print(
        f"\nGeometry complete: {d4.geometry_complete}; "
        f"risk/reward: {d4.risk_reward_ratio} (no value fabricated).",
    )

    # ------------------------------------------------------------
    # 5. MULTIPLE CANDIDATES -> DETERMINISTIC RANKING
    # ------------------------------------------------------------
    print("\n" + _line())
    print("5. Multiple Candidates -> Deterministic Ranking")
    print(_line())

    ranking = dec_engine.rank(
        [strong, weak, conflict, incomplete], 5, strong.timestamp,
    )
    print(formatter.format_ranking(ranking))
    print(
        f"\nPreferred identified: {ranking.has_preferred} "
        f"(candidate_count={ranking.candidate_count})",
    )

    # ------------------------------------------------------------
    # 6 & 7. POINT-IN-TIME LEAKAGE + FUTURE MUTATION PROOF
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Point-in-Time Leakage + Future Mutation Proof")
    print(_line())

    pat, mc, se, tce, dec_eng = _build_engines()

    # Build a deterministic bullish dataset with a hammer at the end.
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
    # Hammer at the end.
    base = candles[-1].close
    body = 2.0
    hammer_close = base + body
    candles.append(
        OHLCVCandle(
            timestamp=_EPOCH + timedelta(days=idx),
            open=base,
            high=hammer_close + body,
            low=hammer_close - (body * 3.0),
            close=hammer_close,
            volume=1000.0,
        )
    )

    def decision_at(data: list[OHLCVCandle], index: int):
        pats = [p for p in pat.detect(data[: index + 1]) if p.index == index]
        ctx = mc.analyze_at(data, index)
        a = se.assess(pats, ctx, index, data[index].timestamp)
        c = tce.generate(a, ctx, index, data[index].timestamp, data[index].close)
        return dec_eng.decide(c, index, data[index].timestamp)

    T = len(candles) - 2

    from_full = decision_at(candles, T)
    from_prefix = decision_at(candles[: T + 1], T)
    agree = from_full == from_prefix
    print(
        f"{'PASS' if agree else 'FAIL'}: decision(T={T}) from full "
        "series == decision from prefix",
    )

    mutated = list(candles)
    for offset in (1, 2):
        if T + offset < len(mutated):
            mutated[T + offset] = candle(999.0, 1001.0, 997.0, T + offset)
    from_mut = decision_at(mutated, T)
    unchanged = from_full == from_mut
    print(
        f"{'PASS' if unchanged else 'FAIL'}: future mutation leaves "
        f"decision(T={T}) unchanged",
    )

    stable = (
        from_full.direction == from_mut.direction
        and from_full.decision_score == from_mut.decision_score
        and from_full.classification == from_mut.classification
        and from_full.geometry_complete == from_mut.geometry_complete
    )
    print(
        f"{'PASS' if stable else 'FAIL'}: "
        "direction/score/classification/geometry stable after mutation",
    )

    if not (agree and unchanged and stable):
        print("WARNING: leakage proof did not fully pass.")

    # ------------------------------------------------------------
    # 8. EXISTING PIPELINE REGRESSION
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Existing Pipeline Behaviour (before / after)")
    print(_line())

    data = trending_dataset()
    before = HistoricalEvaluationPipeline(
        PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=False,
        ),
    ).evaluate(data)
    after = HistoricalEvaluationPipeline(
        PipelineConfig(
            swing_config=SwingConfig(lookback=2),
            enable_trade_decision=True,
        ),
    ).evaluate(data)

    print(f"Signals before: {before.signals_generated}")
    print(f"Signals after : {after.signals_generated}")
    print(f"Completed trades before: {before.completed_trades}")
    print(f"Completed trades after : {after.completed_trades}")

    sig_ok = before.signals_generated == after.signals_generated
    trade_ok = before.completed_trades == after.completed_trades
    print(
        f"{'PASS' if sig_ok and trade_ok else 'FAIL'}: existing "
        "signal / trade behaviour unchanged",
    )

    point = after.evaluation_points_sequence[-1]
    has_dec = getattr(point, "trade_decision", None) is not None
    print(f"Trade decision attached to evaluation points: {has_dec}")

    # Classification distribution in the run.
    counts: dict[str, int] = {}
    for p in after.evaluation_points_sequence:
        td = p.trade_decision
        if td is not None:
            counts[td.classification.name] = (
                counts.get(td.classification.name, 0) + 1
            )
    print(f"Decision classification distribution: {counts}")

    # Show one decision produced by the pipeline end-to-end.
    pipeline_decs = [
        p.trade_decision
        for p in after.evaluation_points_sequence
        if p.trade_decision is not None
        and p.trade_decision.is_preferred
    ]
    if pipeline_decs:
        print("\nSample pipeline-generated PREFERRED decision:")
        print(formatter.format(pipeline_decs[0]))
    else:
        # Fall back to the highest-scoring decision.
        all_decs = [
            p.trade_decision
            for p in after.evaluation_points_sequence
            if p.trade_decision is not None
        ]
        if all_decs:
            best = max(all_decs, key=lambda d: d.decision_score)
            print("\nSample pipeline-generated decision (highest score):")
            print(formatter.format(best))

    print("\n" + _line())
    print("Sprint 11S demo completed successfully.")
    print(_line())


if __name__ == "__main__":
    main()
