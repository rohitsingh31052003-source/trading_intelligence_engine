"""
Demo for Sprint 11T — Trade Opportunity Filtering & Ranking.

This demo exercises the trade-opportunity filter / ranking layer on
deterministic synthetic data and proves:

1. Strong single opportunity      -> BEST_OPPORTUNITY
2. Multiple candidates             -> one clearly outranks the rest
3. LONG vs SHORT                   -> deterministic, symmetric ranking
4. Incomplete geometry             -> honestly WATCH (never best/alt)
5. Conflicting evidence            -> never silently best/alt (WATCH)
6. No valid candidate              -> NO_OPPORTUNITY
7. Poor R:R                        -> handled honestly
8. Tie-breaking                    -> deterministic, direction-symmetric

Plus:
- Point-in-time leakage proof (opportunity(T) from prefix == full series)
- Future mutation proof (opportunity(T) unchanged by future candles)
- Existing pipeline regression (signals / trades unchanged before/after)

The demo prints descriptive trade-opportunity reports. It makes NO
profitability, probability, or directional prediction. A trade
opportunity is a DESCRIPTIVE classification of the best AVAILABLE
technical opportunity among the eligible candidates at a point in time,
NOT a trade signal.
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
from engine.intelligence.trade_opportunity import TradeOpportunityEngine
from engine.models.ohlcv import OHLCVCandle
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
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.trade_opportunity import TradeOpportunityFormatter


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
    target: float | None = 120.0,
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
        TradeOpportunityEngine(),
    )


def _print_summary(label: str, ranking) -> None:
    print(f"\n--- {label} ---")
    print(f"candidate_count={ranking.candidate_count} eligible={ranking.eligible_count}")
    for op in ranking.opportunities:
        print(
            f"  rank={op.rank} dir={op.direction:<5} "
            f"status={op.status.name:<22} "
            f"decision={op.decision_classification:<9} "
            f"score={op.decision_score:<3} "
            f"elig={op.eligibility.name}"
        )
    if ranking.has_best:
        b = ranking.best
        print(
            f"  -> BEST: rank {b.rank}, {b.direction}, "
            f"score {b.decision_score}, R:R {b.risk_reward_ratio}",
        )
    else:
        print("  -> BEST: none")


def main() -> None:
    print("=" * 60)
    print("Sprint 11T — Trade Opportunity Filtering & Ranking")
    print("=" * 60)

    dec_engine = TradeDecisionEngine()
    opp_engine = TradeOpportunityEngine()
    formatter = TradeOpportunityFormatter()

    # ------------------------------------------------------------
    # 1. STRONG SINGLE OPPORTUNITY -> BEST_OPPORTUNITY
    # ------------------------------------------------------------
    print("\n" + _line())
    print("1. Strong Single Opportunity -> BEST_OPPORTUNITY")
    print(_line())

    strong = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=95.0, target=120.0,
        confluence_score=5,
    )
    d1 = dec_engine.decide(strong, 0, strong.timestamp)
    r1 = opp_engine.rank([d1], 0, strong.timestamp)
    _print_summary("single strong", r1)
    print(formatter.format(r1.best))

    # ------------------------------------------------------------
    # 2. MULTIPLE CANDIDATES -> one clearly outranks
    # ------------------------------------------------------------
    print("\n" + _line())
    print("2. Multiple Candidates -> One Clearly Outranks")
    print(_line())

    weaker = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=97.0, target=104.0,  # poor R:R but complete
        confluence_score=3,
    )
    d_weak = dec_engine.decide(weaker, 1, weaker.timestamp)
    r2 = opp_engine.rank([d1, d_weak], 2, strong.timestamp)
    _print_summary("strong vs weaker", r2)

    # ------------------------------------------------------------
    # 3. LONG vs SHORT -> deterministic, symmetric ranking
    # ------------------------------------------------------------
    print("\n" + _line())
    print("3. LONG vs SHORT -> Deterministic Symmetric Ranking")
    print(_line())

    short = _candidate(
        direction=CandidateDirection.SHORT,
        entry=100.0, stop=105.0, target=80.0,
        confluence_score=5,
    )
    d_short = dec_engine.decide(short, 3, short.timestamp)
    r3 = opp_engine.rank([d1, d_short], 4, strong.timestamp)
    _print_summary("LONG vs SHORT", r3)
    print(
        f"  Both best-quality; deterministic winner by score "
        f"(LONG {d1.decision_score} vs SHORT {d_short.decision_score}).",
    )

    # ------------------------------------------------------------
    # 4. INCOMPLETE GEOMETRY -> honestly WATCH (never best/alt)
    # ------------------------------------------------------------
    print("\n" + _line())
    print("4. Incomplete Geometry -> Honestly WATCH")
    print(_line())

    incomplete = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=None, target=None,
        confluence_score=4,
    )
    d_inc = dec_engine.decide(incomplete, 5, incomplete.timestamp)
    r4 = opp_engine.rank([d_inc], 5, incomplete.timestamp)
    _print_summary("incomplete geometry", r4)
    print(
        f"  Geometry complete: {d_inc.geometry_complete}; "
        f"R:R {d_inc.risk_reward_ratio} (no value fabricated).",
    )

    # ------------------------------------------------------------
    # 5. CONFLICTING EVIDENCE -> never silently best/alt
    # ------------------------------------------------------------
    print("\n" + _line())
    print("5. Conflicting Evidence -> WATCH (never best/alt)")
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
    d_conf = dec_engine.decide(conflict, 6, conflict.timestamp)
    r5 = opp_engine.rank([d_conf], 6, conflict.timestamp)
    _print_summary("conflicting", r5)
    print(f"  Conflicts recorded: {d_conf.conflicting_count}")

    # ------------------------------------------------------------
    # 6. NO VALID CANDIDATE -> NO_OPPORTUNITY
    # ------------------------------------------------------------
    print("\n" + _line())
    print("6. No Valid Candidate -> NO_OPPORTUNITY")
    print(_line())

    none_cand = _candidate(
        direction=CandidateDirection.NONE,
        status=CandidateStatus.NO_CANDIDATE,
        entry=None, stop=None, target=None,
        confluence_score=0,
        evidence=_evidence(
            trend=EvidenceAlignment.ABSENT, trend_label="UNKNOWN",
            structure=EvidenceAlignment.ABSENT, structure_label="none",
            candle_ev=EvidenceAlignment.ABSENT, candle_label="none",
            location=EvidenceAlignment.ABSENT, location_label="UNKNOWN",
            rng=EvidenceAlignment.ABSENT, range_label="UNKNOWN",
        ),
        candle_evidence="none",
        market_trend="UNKNOWN",
        market_structure="none",
        location="UNKNOWN",
        range_context="UNKNOWN",
    )
    d_none = dec_engine.decide(none_cand, 7, none_cand.timestamp)
    r6 = opp_engine.rank([d_none], 7, none_cand.timestamp)
    _print_summary("no candidate", r6)

    # ------------------------------------------------------------
    # 7. POOR R:R -> handled honestly
    # ------------------------------------------------------------
    print("\n" + _line())
    print("7. Poor R:R -> Handled Honestly")
    print(_line())

    poor_rr = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=95.0, target=101.0,  # R:R 0.2 (poor)
        confluence_score=4,
    )
    d_poor = dec_engine.decide(poor_rr, 8, poor_rr.timestamp)
    # With a min R:R eligibility gate configured to 1.0, poor R:R is
    # filtered; without the gate it surfaces honestly as WATCH.
    r7_default = opp_engine.rank([d_poor], 8, poor_rr.timestamp)
    _print_summary("poor R:R (default config)", r7_default)
    from engine.config.trade_opportunity_config import TradeOpportunityConfig
    opp_rr = TradeOpportunityEngine(
        TradeOpportunityConfig(min_risk_reward_ratio=1.0),
    )
    r7_gated = opp_rr.rank([d_poor], 8, poor_rr.timestamp)
    _print_summary("poor R:R (min_risk_reward_ratio=1.0)", r7_gated)

    # ------------------------------------------------------------
    # 8. TIE-BREAKING -> deterministic, direction-symmetric
    # ------------------------------------------------------------
    print("\n" + _line())
    print("8. Tie-Breaking -> Deterministic, Direction-Symmetric")
    print(_line())

    tie_long = _candidate(
        direction=CandidateDirection.LONG,
        entry=100.0, stop=95.0, target=120.0,
        confluence_score=5, index=9,
    )
    tie_short = _candidate(
        direction=CandidateDirection.SHORT,
        entry=100.0, stop=105.0, target=80.0,
        confluence_score=5, index=9,
    )
    d_tie_l = dec_engine.decide(tie_long, 9, tie_long.timestamp)
    d_tie_s = dec_engine.decide(tie_short, 9, tie_short.timestamp)
    # Equal scores -> deterministic tie-break (LONG before SHORT only
    # as a final, direction-symmetric last resort).
    r8 = opp_engine.rank([d_tie_s, d_tie_l], 9, tie_long.timestamp)
    _print_summary("tie (identical evidence, opposite direction)", r8)
    print(
        f"  Identical scores ({d_tie_l.decision_score}=="
        f"{d_tie_s.decision_score}); deterministic rank order applied.",
    )

    # ------------------------------------------------------------
    # POINT-IN-TIME LEAKAGE + FUTURE MUTATION PROOF
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Point-in-Time Leakage + Future Mutation Proof")
    print(_line())

    pat, mc, se, tce, dec_eng, opp_eng = _build_engines()

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

    def opportunity_at(data: list[OHLCVCandle], index: int):
        pats = [p for p in pat.detect(data[: index + 1]) if p.index == index]
        ctx = mc.analyze_at(data, index)
        a = se.assess(pats, ctx, index, data[index].timestamp)
        c = tce.generate(a, ctx, index, data[index].timestamp, data[index].close)
        d = dec_eng.decide(c, index, data[index].timestamp)
        return opp_eng.evaluate(d, index, data[index].timestamp)

    T = len(candles) - 2

    from_full = opportunity_at(candles, T)
    from_prefix = opportunity_at(candles[: T + 1], T)
    agree = from_full == from_prefix
    print(
        f"{'PASS' if agree else 'FAIL'}: opportunity(T={T}) from full "
        "series == opportunity from prefix",
    )

    mutated = list(candles)
    for offset in (1, 2):
        if T + offset < len(mutated):
            mutated[T + offset] = candle(999.0, 1001.0, 997.0, T + offset)
    from_mut = opportunity_at(mutated, T)
    unchanged = from_full == from_mut
    print(
        f"{'PASS' if unchanged else 'FAIL'}: future mutation leaves "
        f"opportunity(T={T}) unchanged",
    )

    stable = (
        from_full.status == from_mut.status
        and from_full.rank == from_mut.rank
        and from_full.direction == from_mut.direction
        and from_full.eligibility == from_mut.eligibility
    )
    print(
        f"{'PASS' if stable else 'FAIL'}: "
        "status/rank/direction/eligibility stable after mutation",
    )

    if not (agree and unchanged and stable):
        print("WARNING: leakage proof did not fully pass.")

    # ------------------------------------------------------------
    # EXISTING PIPELINE REGRESSION
    # ------------------------------------------------------------
    print("\n" + _line())
    print("Existing Pipeline Behaviour (before / after)")
    print(_line())

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
    has_opp = getattr(point, "trade_opportunity", None) is not None
    print(f"Trade opportunity attached to evaluation points: {has_opp}")

    # Status distribution in the run.
    counts: dict[str, int] = {}
    for p in after.evaluation_points_sequence:
        op = p.trade_opportunity
        if op is not None:
            counts[op.status.name] = (
                counts.get(op.status.name, 0) + 1
            )
    print(f"Opportunity status distribution: {counts}")

    # Show one opportunity produced by the pipeline end-to-end.
    best_ops = [
        p.trade_opportunity
        for p in after.evaluation_points_sequence
        if p.trade_opportunity is not None
        and p.trade_opportunity.is_best
    ]
    if best_ops:
        print("\nSample pipeline-generated BEST opportunity:")
        print(formatter.format(best_ops[0]))
    else:
        all_ops = [
            p.trade_opportunity
            for p in after.evaluation_points_sequence
            if p.trade_opportunity is not None
        ]
        if all_ops:
            # Fall back to the highest-scored opportunity.
            best = max(all_ops, key=lambda o: o.decision_score)
            print("\nSample pipeline-generated opportunity (highest score):")
            print(formatter.format(best))

    print("\n" + _line())
    print("Sprint 11T demo completed successfully.")
    print(_line())


if __name__ == "__main__":
    main()
