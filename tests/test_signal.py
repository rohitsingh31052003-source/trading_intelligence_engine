"""
Tests for the Signal Generation Engine (Sprint 11C).
"""

from datetime import datetime, timedelta

from engine.intelligence.signal import (
    SignalContext,
    SignalEngine,
)
from engine.models.confluence import (
    ConfluenceDirection,
    ConfluenceEvidence,
    ConfluenceResult,
    EvidenceStrength,
)
from engine.models.decision import (
    DecisionContext,
    DecisionDirection,
    DecisionEvidence,
    DecisionStatus,
    SetupQuality,
)
from engine.models.signal import (
    EntrySource,
    SignalDirection,
    SignalState,
)


# ============================================================
# HELPERS
# ============================================================


def make_evidence(
    name,
    direction,
    score,
    strength=EvidenceStrength.STRONG,
    reason="test evidence",
):
    return ConfluenceEvidence(
        name=name,
        direction=direction,
        score=score,
        strength=strength,
        reason=reason,
    )


def make_confluence(
    *,
    direction="BULLISH",
    confidence=80.0,
    bullish_score=80.0,
    bearish_score=0.0,
    net_score=None,
    conflict="LOW",
    evidence=None,
):

    if net_score is None:
        net_score = bullish_score - bearish_score

    return ConfluenceResult(
        direction=ConfluenceDirection[direction],
        confidence=confidence,
        score=max(bullish_score, bearish_score),
        bullish_score=bullish_score,
        bearish_score=bearish_score,
        net_score=net_score,
        conflict=conflict,
        liquidity_score=0.0,
        evidence=tuple(evidence or []),
        reasons=tuple(
            item.reason for item in (evidence or [])
        ),
    )


def make_decision(
    *,
    direction=DecisionDirection.BULLISH,
    confidence=80.0,
    setup_quality=SetupQuality.STRONG,
    status=DecisionStatus.READY,
    trade_eligible=True,
    conflict="NONE",
    bullish_score=80.0,
    bearish_score=0.0,
    net_score=80.0,
    evidence_quality=85.0,
    evidence=None,
    reasons=None,
) -> DecisionContext:

    return DecisionContext(
        direction=direction,
        confidence=confidence,
        setup_quality=setup_quality,
        status=status,
        trade_eligible=trade_eligible,
        bullish_score=bullish_score,
        bearish_score=bearish_score,
        net_score=net_score,
        conflict=conflict,
        evidence_quality=evidence_quality,
        evidence=tuple(evidence or []),
        reasons=tuple(reasons or []),
    )


def ready_bullish_decision() -> DecisionContext:
    return make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=82.0,
        setup_quality=SetupQuality.STRONG,
        status=DecisionStatus.READY,
        trade_eligible=True,
        conflict="NONE",
        bullish_score=82.0,
        bearish_score=0.0,
        net_score=82.0,
        evidence_quality=85.0,
        evidence=[
            DecisionEvidence(
                name="Trend",
                direction=DecisionDirection.BULLISH,
                score=30.0,
                reason="Trend is bullish.",
            ),
        ],
        reasons=["Decision is bullish."],
    )


def ready_bearish_decision() -> DecisionContext:
    return make_decision(
        direction=DecisionDirection.BEARISH,
        confidence=78.0,
        setup_quality=SetupQuality.STRONG,
        status=DecisionStatus.READY,
        trade_eligible=True,
        conflict="LOW",
        bullish_score=0.0,
        bearish_score=78.0,
        net_score=-78.0,
        evidence_quality=80.0,
        evidence=[
            DecisionEvidence(
                name="Trend",
                direction=DecisionDirection.BEARISH,
                score=30.0,
                reason="Trend is bearish.",
            ),
        ],
        reasons=["Decision is bearish."],
    )


# ============================================================
# ELIGIBILITY
# ============================================================


def test_eligible_bullish_decision_produces_bullish_signal():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert result.direction == SignalDirection.LONG
    assert result.state == SignalState.LONG
    assert result.eligible is True


def test_eligible_bearish_decision_produces_bearish_signal():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert result.direction == SignalDirection.SHORT
    assert result.state == SignalState.SHORT
    assert result.eligible is True


def test_neutral_decision_produces_no_signal():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.NEUTRAL,
        status=DecisionStatus.NOT_READY,
        trade_eligible=False,
        setup_quality=SetupQuality.INVALID,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.state == SignalState.NO_SIGNAL
    assert result.eligible is False
    assert result.direction == SignalDirection.NONE


def test_unknown_decision_produces_no_signal():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.UNKNOWN,
        status=DecisionStatus.NOT_READY,
        trade_eligible=False,
        setup_quality=SetupQuality.INVALID,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.state == SignalState.NO_SIGNAL


def test_not_ready_decision_produces_no_signal():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        status=DecisionStatus.NOT_READY,
        trade_eligible=False,
        setup_quality=SetupQuality.WEAK,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.state == SignalState.NO_SIGNAL


def test_ineligible_decision_produces_no_signal():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        status=DecisionStatus.READY,
        trade_eligible=False,
        setup_quality=SetupQuality.MODERATE,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.state == SignalState.NO_SIGNAL


def test_none_decision_produces_no_signal():

    engine = SignalEngine()

    result = engine.analyze(None)

    assert result.state == SignalState.NO_SIGNAL
    assert result.eligible is False


# ============================================================
# ENTRY
# ============================================================


def test_bullish_entry_is_valid():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert result.entry_price == 100.0
    assert result.entry_source == EntrySource.TRIGGER_CLOSE
    assert result.entry_price > 0


def test_bearish_entry_is_valid():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert result.entry_price == 100.0
    assert result.entry_source == EntrySource.TRIGGER_CLOSE


def test_missing_entry_is_rejected():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=None),
    )

    assert result.state == SignalState.INVALID
    assert result.eligible is False
    assert result.entry_price is None


def test_supplied_entry_takes_precedence():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            structure_break_level=99.0,
            supplied_entry=101.0,
        ),
    )

    assert result.entry_price == 101.0
    assert result.entry_source == EntrySource.SUPPLIED


def test_structure_break_entry_used_when_no_trigger_close():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=None,
            structure_break_level=102.0,
        ),
    )

    assert result.entry_price == 102.0
    assert result.entry_source == EntrySource.STRUCTURE_BREAK


# ============================================================
# STOP LOSS
# ============================================================


def test_bullish_stop_is_below_entry():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
        ),
    )

    assert result.stop_loss is not None
    assert result.stop_loss < result.entry_price
    # Structural low used as stop.
    assert result.stop_loss == 98.0


def test_bearish_stop_is_above_entry():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=102.0,
        ),
    )

    assert result.stop_loss is not None
    assert result.stop_loss > result.entry_price
    assert result.stop_loss == 102.0


def test_invalid_bullish_stop_rejected():
    """
    If the only structural level is above the entry for a
    LONG, the engine falls back to a deterministic stop
    below the entry rather than producing an invalid stop.
    """

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=105.0,  # wrong side for long stop
        ),
    )

    assert result.stop_loss is not None
    assert result.stop_loss < result.entry_price


def test_invalid_bearish_stop_rejected():
    """
    Symmetric to the bullish case: a structural level on the
    wrong side for a SHORT stop yields a fallback stop above
    the entry.
    """

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=95.0,  # wrong side for short stop
        ),
    )

    assert result.stop_loss is not None
    assert result.stop_loss > result.entry_price


# ============================================================
# TAKE PROFIT
# ============================================================


def test_bullish_target_is_above_entry():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=104.0,
        ),
    )

    assert result.take_profit is not None
    assert result.take_profit > result.entry_price


def test_bearish_target_is_below_entry():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=96.0,
        ),
    )

    assert result.take_profit is not None
    assert result.take_profit < result.entry_price


def test_invalid_target_rejected():
    """
    A structural target on the wrong side is ignored and the
    deterministic fallback target is used instead.
    """

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
        ),
    )

    # 98 is below entry -> cannot be a long target, so the
    # fallback target (above entry) is used.
    assert result.take_profit is not None
    assert result.take_profit > result.entry_price


# ============================================================
# RISK
# ============================================================


def test_correct_long_risk():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
        ),
    )

    assert result.risk_per_unit == 2.0


def test_correct_short_risk():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=102.0,
        ),
    )

    assert result.risk_per_unit == 2.0


def test_zero_risk_setup_rejected():
    """
    A setup whose risk is not positive must be rejected.

    The public analyze() path structurally prevents a zero
    or negative risk (stops are validated to sit on the
    correct side of the entry). This test exercises the
    risk guard directly on a real engine instance to confirm
    that a degenerate risk yields a zero ratio and that no
    eligible signal ever carries a non-positive risk.
    """

    engine = SignalEngine()

    # Direct exercise of the risk/reward guard with a
    # degenerate entry == stop (zero risk).
    risk, reward, ratio = engine._calculate_risk_reward(
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_loss=100.0,
        take_profit=104.0,
    )

    assert risk == 0.0
    assert ratio == 0.0

    # And every eligible signal produced via analyze() must
    # have strictly positive risk.
    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    assert result.eligible is True
    assert result.risk_per_unit > 0


# ============================================================
# REWARD
# ============================================================


def test_correct_long_reward():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=104.0,
        ),
    )

    # entry 100 (trigger), stop fallback ~98, target 104.
    assert result.reward_per_unit == 4.0


def test_correct_short_reward():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=96.0,
        ),
    )

    # entry 100, stop fallback ~102, target 96.
    assert result.reward_per_unit == 4.0


# ============================================================
# RISK / REWARD
# ============================================================


def test_correct_risk_reward_calculation():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    # entry 100 (trigger), stop 98, target 104.
    assert result.risk_per_unit == 2.0
    assert result.reward_per_unit == 4.0
    assert result.risk_reward_ratio == 2.0


def test_minimum_risk_reward_accepted():
    """
    A setup exactly at the minimum R:R is accepted.
    """

    engine = SignalEngine()

    # entry 100, stop 98 -> risk 2. target 103 -> reward 3.
    # ratio 1.5 exactly at the threshold.
    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=103.0,
        ),
    )

    assert result.risk_reward_ratio == 1.5
    assert result.risk_reward_ratio >= engine.MIN_RISK_REWARD
    assert result.eligible is True


def test_below_minimum_risk_reward_rejected():
    """
    A setup below the minimum R:R must not be eligible.
    """

    engine = SignalEngine()

    # entry 100, stop 98 -> risk 2. target 100.5 -> reward
    # 0.5. ratio 0.25.
    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=100.5,
        ),
    )

    assert result.risk_reward_ratio < engine.MIN_RISK_REWARD
    assert result.eligible is False
    assert result.state == SignalState.INVALID


# ============================================================
# CONFIDENCE
# ============================================================


def test_confidence_is_preserved():

    engine = SignalEngine()

    decision = ready_bullish_decision()

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.confidence == decision.confidence


def test_confidence_is_bounded_between_zero_and_hundred():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=150.0,
        status=DecisionStatus.READY,
        trade_eligible=True,
        setup_quality=SetupQuality.STRONG,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert 0.0 <= result.confidence <= 100.0
    assert result.confidence == 100.0


def test_negative_confidence_is_bounded():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=-25.0,
        status=DecisionStatus.READY,
        trade_eligible=True,
        setup_quality=SetupQuality.STRONG,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.confidence == 0.0


# ============================================================
# QUALITY
# ============================================================


def test_strong_setup_classified_correctly():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=75.0,
        setup_quality=SetupQuality.STRONG,
        status=DecisionStatus.READY,
        trade_eligible=True,
        conflict="NONE",
        evidence_quality=80.0,
    )

    result = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    assert result.quality in (
        SetupQuality.STRONG,
        SetupQuality.EXCELLENT,
    )


def test_weak_setup_classified_correctly():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=50.0,
        setup_quality=SetupQuality.WEAK,
        status=DecisionStatus.READY,
        trade_eligible=True,
        conflict="LOW",
        evidence_quality=55.0,
    )

    result = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    assert result.quality == SetupQuality.WEAK


def test_invalid_setup_classified_correctly():

    engine = SignalEngine()

    decision = make_decision(
        direction=DecisionDirection.BULLISH,
        confidence=80.0,
        setup_quality=SetupQuality.INVALID,
        status=DecisionStatus.NOT_READY,
        trade_eligible=False,
    )

    result = engine.analyze(
        decision,
        SignalContext(trigger_close=100.0),
    )

    assert result.quality == SetupQuality.INVALID


# ============================================================
# INVALIDATION
# ============================================================


def test_long_invalidation_exposed():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    assert result.invalidation.price == 98.0
    assert "98.00" in result.invalidation.condition
    assert result.invalidation.price == result.stop_loss


def test_short_invalidation_exposed():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bearish_decision(),
        SignalContext(
            trigger_close=100.0,
            liquidity_level=102.0,
            structure_break_level=96.0,
        ),
    )

    assert result.invalidation.price == 102.0
    assert "102.00" in result.invalidation.condition
    assert result.invalidation.price == result.stop_loss


def test_invalidation_information_exposed():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert result.invalidation is not None
    assert result.invalidation.condition
    assert result.invalidation.price is not None


# ============================================================
# DETERMINISM
# ============================================================


def test_same_input_produces_same_output():

    engine = SignalEngine()

    decision = ready_bullish_decision()
    context = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
    )

    first = engine.analyze(decision, context)
    second = engine.analyze(decision, context)

    assert first == second


def test_determinism_across_engine_instances():

    decision = ready_bearish_decision()
    context = SignalContext(
        trigger_close=100.0,
        liquidity_level=102.0,
        structure_break_level=96.0,
    )

    first = SignalEngine().analyze(decision, context)
    second = SignalEngine().analyze(decision, context)

    assert first == second


# ============================================================
# NO LOOK-AHEAD
# ============================================================


def test_no_lookahead_signal_unchanged_by_future_candles():
    """
    Generating a signal at candle T must not depend on any
    candle after T. Appending future candles to the context
    must not change the result.
    """

    engine = SignalEngine()

    reference = datetime(2024, 1, 10, 12, 0)

    decision = ready_bullish_decision()

    context = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
        reference_time=reference,
    )

    baseline = engine.analyze(decision, context)

    # Simulate "future" information by advancing the
    # reference time well beyond T. The signal generated at
    # T must be identical because the engine only ever
    # consumes the supplied context, never future candles.
    future_context = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
        reference_time=reference + timedelta(days=365),
    )

    future = engine.analyze(decision, future_context)

    assert baseline == future


def test_no_lookahead_does_not_inspect_future_levels():
    """
    A liquidity level that only exists "in the future"
    (i.e. not supplied to the context at T) cannot influence
    the signal generated at T.
    """

    engine = SignalEngine()

    decision = ready_bullish_decision()

    # At T, only the trigger close and a stop anchor are
    # known. No target level is supplied, so the engine uses
    # the deterministic fallback target.
    baseline = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
        ),
    )

    fallback_target = baseline.take_profit
    assert fallback_target is not None

    # Now a "future" target level (104) is supplied as part
    # of the information available at a later T'. Because it
    # was never supplied at T, the baseline signal must NOT
    # have used it.
    with_future_target = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=98.0,
            structure_break_level=104.0,
        ),
    )

    assert with_future_target.take_profit == 104.0
    # The baseline used the fallback, not the future level.
    # The fallback (risk 2 * default RR 2.0) also yields 104,
    # so pick a stop anchor that makes the fallback differ
    # from the future level to make the no-look-ahead point
    # unambiguous.
    baseline_wide = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=96.0,
        ),
    )

    # risk 4 -> fallback target 108, not 104.
    assert baseline_wide.take_profit == 108.0

    with_future_target_wide = engine.analyze(
        decision,
        SignalContext(
            trigger_close=100.0,
            liquidity_level=96.0,
            structure_break_level=104.0,
        ),
    )

    # When 104 IS supplied, the structural target is used.
    assert with_future_target_wide.take_profit == 104.0

    # The baseline (no 104 supplied) never used 104.
    assert baseline_wide.take_profit != 104.0


def test_reference_time_does_not_change_entry_stop_target():
    """
    reference_time is only used to assert recency; it must
    never alter the deterministic geometry of the setup.
    """

    engine = SignalEngine()

    decision = ready_bullish_decision()

    ctx_no_time = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
    )

    ctx_with_time = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
        reference_time=datetime(2024, 6, 1),
    )

    a = engine.analyze(decision, ctx_no_time)
    b = engine.analyze(decision, ctx_with_time)

    assert a.entry_price == b.entry_price
    assert a.stop_loss == b.stop_loss
    assert a.take_profit == b.take_profit
    assert a.risk_reward_ratio == b.risk_reward_ratio


# ============================================================
# RESULT MODEL
# ============================================================


def test_signal_result_is_immutable():
    """
    SignalResult must be immutable (frozen dataclass).
    """

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    try:
        result.entry_price = 999.0  # type: ignore[misc]
    except Exception:
        return

    raise AssertionError(
        "SignalResult should be immutable."
    )


def test_signal_result_contains_required_fields():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    for field_name in (
        "direction",
        "state",
        "entry_price",
        "stop_loss",
        "take_profit",
        "risk_per_unit",
        "reward_per_unit",
        "risk_reward_ratio",
        "confidence",
        "quality",
        "eligible",
        "invalidation",
        "reasons",
    ):
        assert hasattr(result, field_name), (
            f"Missing field: {field_name}"
        )


def test_reasons_are_generated():

    engine = SignalEngine()

    result = engine.analyze(
        ready_bullish_decision(),
        SignalContext(trigger_close=100.0),
    )

    assert len(result.reasons) > 0
    assert any(
        "bullish" in reason.lower()
        for reason in result.reasons
    )
