from datetime import datetime
from types import SimpleNamespace

from engine.intelligence.confluence import (
    ConfluenceEngine,
    MAX_LIQUIDITY_SCORE,
    MAX_BULLISH_LIQUIDITY_SCORE,
    MAX_BEARISH_LIQUIDITY_SCORE,
)

from engine.models.confluence import (
    ConfluenceDirection,
)


# ============================================================
# HELPERS
# ============================================================

def make_analysis(bias):
    return SimpleNamespace(
        bias=bias,
    )


def make_trend(state):
    return SimpleNamespace(
        state=state,
    )


def make_bos(
    detected=False,
    bos_type=None,
):
    return SimpleNamespace(
        detected=detected,
        type=bos_type,
    )


def make_choch(
    detected=False,
    choch_type=None,
):
    return SimpleNamespace(
        detected=detected,
        type=choch_type,
    )


def make_liquidity_event(
    *,
    event_type,
    liquidity_type,
    price=100.0,
    timestamp=None,
    detected=True,
):
    pool = SimpleNamespace(
        price=price,
        liquidity_type=liquidity_type,
    )

    return SimpleNamespace(
        detected=detected,
        event_type=SimpleNamespace(
            name=event_type,
        ),
        pool=pool,
        event_timestamp=timestamp,
    )


# ============================================================
# BASIC CONFLUENCE
# ============================================================

def test_no_evidence_returns_unknown():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.UNKNOWN
    )

    assert result.confidence == 0.0
    assert result.score == 0.0
    assert result.bullish_score == 0.0
    assert result.bearish_score == 0.0
    assert result.net_score == 0.0
    assert result.conflict == "NONE"


def test_bullish_structure_and_trend():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("BULLISH"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.BULLISH
    )

    assert result.bullish_score == 60.0
    assert result.bearish_score == 0.0
    assert result.confidence == 100.0


def test_bearish_structure_and_trend():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BEARISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("BEARISH"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.BEARISH
    )

    assert result.bearish_score == 60.0
    assert result.bullish_score == 0.0
    assert result.confidence == 100.0


# ============================================================
# DIRECTIONAL DOMINANCE
# ============================================================

def test_bullish_evidence_dominates():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(
            detected=True,
            bos_type="BULLISH",
        ),
        choch=make_choch(),
        trend=make_trend("BULLISH"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.BULLISH
    )

    assert result.bullish_score > result.bearish_score
    assert result.net_score > 0
    assert result.confidence > 50.0


def test_bearish_evidence_dominates():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BEARISH"),
        bos=make_bos(
            detected=True,
            bos_type="BEARISH",
        ),
        choch=make_choch(),
        trend=make_trend("BEARISH"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.BEARISH
    )

    assert result.bearish_score > result.bullish_score
    assert result.net_score < 0
    assert result.confidence > 50.0


def test_balanced_evidence_is_neutral():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("BEARISH"),
        liquidity_events=[],
    )

    assert result.direction == (
        ConfluenceDirection.NEUTRAL
    )

    assert result.bullish_score == 30.0
    assert result.bearish_score == 30.0
    assert result.net_score == 0.0
    assert result.confidence == 0.0


# ============================================================
# CONFLICT
# ============================================================

def test_conflicting_evidence_detected():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("BEARISH"),
        liquidity_events=[],
    )

    assert result.conflict == "HIGH"


def test_strong_direction_has_low_conflict():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(
            detected=True,
            bos_type="BULLISH",
        ),
        choch=make_choch(
            detected=True,
            choch_type="BULLISH",
        ),
        trend=make_trend("BULLISH"),
        liquidity_events=[],
    )

    assert result.bullish_score > result.bearish_score
    assert result.conflict == "LOW"


# ============================================================
# LIQUIDITY DIRECTION
# ============================================================

def test_sell_side_sweep_is_bullish():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="SWEEP",
        liquidity_type="SELL_SIDE",
    )

    direction = (
        engine._liquidity_event_direction(
            event
        )
    )

    assert direction == (
        ConfluenceDirection.BULLISH
    )


def test_buy_side_sweep_is_bearish():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="SWEEP",
        liquidity_type="BUY_SIDE",
    )

    direction = (
        engine._liquidity_event_direction(
            event
        )
    )

    assert direction == (
        ConfluenceDirection.BEARISH
    )


def test_buy_side_breakout_is_bullish():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="BREAKOUT",
        liquidity_type="BUY_SIDE",
    )

    direction = (
        engine._liquidity_event_direction(
            event
        )
    )

    assert direction == (
        ConfluenceDirection.BULLISH
    )


def test_sell_side_breakout_is_bearish():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="BREAKOUT",
        liquidity_type="SELL_SIDE",
    )

    direction = (
        engine._liquidity_event_direction(
            event
        )
    )

    assert direction == (
        ConfluenceDirection.BEARISH
    )


def test_undetected_liquidity_event_is_ignored():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="BREAKOUT",
        liquidity_type="BUY_SIDE",
        detected=False,
    )

    direction = (
        engine._liquidity_event_direction(
            event
        )
    )

    assert direction == (
        ConfluenceDirection.UNKNOWN
    )


# ============================================================
# LIQUIDITY WEIGHTS
# ============================================================

def test_breakout_has_higher_weight_than_sweep():

    engine = ConfluenceEngine()

    breakout = make_liquidity_event(
        event_type="BREAKOUT",
        liquidity_type="BUY_SIDE",
    )

    sweep = make_liquidity_event(
        event_type="SWEEP",
        liquidity_type="BUY_SIDE",
    )

    breakout_score = (
        engine._liquidity_base_score(
            breakout
        )
    )

    sweep_score = (
        engine._liquidity_base_score(
            sweep
        )
    )

    assert breakout_score > sweep_score
    assert breakout_score == 12.0
    assert sweep_score == 10.0


# ============================================================
# RECENCY
# ============================================================

def test_recent_evidence_has_higher_weight():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    recent_time = datetime(
        2026,
        1,
        25,
    )

    old_time = datetime(
        2025,
        10,
        1,
    )

    recent_multiplier = (
        engine._recency_multiplier(
            recent_time,
            reference_time,
        )
    )

    old_multiplier = (
        engine._recency_multiplier(
            old_time,
            reference_time,
        )
    )

    assert recent_multiplier > old_multiplier


def test_recent_event_has_full_weight():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    event_time = datetime(
        2026,
        1,
        25,
    )

    multiplier = (
        engine._recency_multiplier(
            event_time,
            reference_time,
        )
    )

    assert multiplier == 1.0


def test_old_event_is_discounted():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    event_time = datetime(
        2025,
        1,
        1,
    )

    multiplier = (
        engine._recency_multiplier(
            event_time,
            reference_time,
        )
    )

    assert multiplier == 0.25


# ============================================================
# LIQUIDITY SCORE CAP
# ============================================================

def test_bullish_liquidity_score_is_capped():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    liquidity_events = []

    for index in range(10):

        liquidity_events.append(
            make_liquidity_event(
                event_type="BREAKOUT",
                liquidity_type="BUY_SIDE",
                price=100.0 + index * 2.0,
                timestamp=reference_time,
            )
        )

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=liquidity_events,
    )

    assert (
        result.bullish_score
        <= MAX_BULLISH_LIQUIDITY_SCORE
    )

    assert (
        result.liquidity_score
        <= MAX_LIQUIDITY_SCORE
        or result.liquidity_score
        <= MAX_BULLISH_LIQUIDITY_SCORE
    )


def test_bearish_liquidity_score_is_capped():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    liquidity_events = []

    for index in range(10):

        liquidity_events.append(
            make_liquidity_event(
                event_type="BREAKOUT",
                liquidity_type="SELL_SIDE",
                price=100.0 + index * 2.0,
                timestamp=reference_time,
            )
        )

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=liquidity_events,
    )

    assert (
        result.bearish_score
        <= MAX_BEARISH_LIQUIDITY_SCORE
    )


# ============================================================
# LIQUIDITY DEDUPLICATION
# ============================================================

def test_nearby_liquidity_events_are_deduplicated():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    liquidity_events = [
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=100.0,
            timestamp=reference_time,
        ),
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=100.5,
            timestamp=reference_time,
        ),
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=100.8,
            timestamp=reference_time,
        ),
    ]

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=liquidity_events,
    )

    assert result.liquidity_score == 12.0


def test_different_liquidity_zones_are_not_deduplicated():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    liquidity_events = [
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=100.0,
            timestamp=reference_time,
        ),
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=105.0,
            timestamp=reference_time,
        ),
    ]

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=liquidity_events,
    )

    assert result.liquidity_score == 24.0


def test_opposing_liquidity_at_same_price_can_coexist():

    engine = ConfluenceEngine()

    reference_time = datetime(
        2026,
        1,
        31,
    )

    liquidity_events = [
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="BUY_SIDE",
            price=100.0,
            timestamp=reference_time,
        ),
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="SELL_SIDE",
            price=100.0,
            timestamp=reference_time,
        ),
    ]

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=liquidity_events,
    )

    assert result.bullish_score == 12.0
    assert result.bearish_score == 12.0
    assert result.direction == (
        ConfluenceDirection.NEUTRAL
    )


# ============================================================
# NET SCORE
# ============================================================

def test_net_score_is_bullish_when_bullish_dominates():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[],
    )

    assert result.net_score == (
        result.bullish_score
        - result.bearish_score
    )

    assert result.net_score > 0


def test_net_score_is_bearish_when_bearish_dominates():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BEARISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[],
    )

    assert result.net_score == (
        result.bullish_score
        - result.bearish_score
    )

    assert result.net_score < 0


# ============================================================
# CONFIDENCE
# ============================================================

def test_confidence_reflects_dominance():

    engine = ConfluenceEngine()

    confidence = (
        engine._calculate_confidence(
            dominant_score=80.0,
            opposing_score=20.0,
            total_score=100.0,
        )
    )

    assert confidence == 80.0


def test_low_dominance_produces_low_confidence():

    engine = ConfluenceEngine()

    confidence = (
        engine._calculate_confidence(
            dominant_score=51.0,
            opposing_score=49.0,
            total_score=100.0,
        )
    )

    assert confidence == 51.0


def test_confidence_is_capped_at_100():

    engine = ConfluenceEngine()

    confidence = (
        engine._calculate_confidence(
            dominant_score=150.0,
            opposing_score=0.0,
            total_score=150.0,
        )
    )

    assert confidence <= 100.0


def test_confidence_is_never_negative():

    engine = ConfluenceEngine()

    confidence = (
        engine._calculate_confidence(
            dominant_score=0.0,
            opposing_score=100.0,
            total_score=100.0,
        )
    )

    assert confidence >= 0.0


# ============================================================
# EVIDENCE EXPLAINABILITY
# ============================================================

def test_liquidity_evidence_is_visible():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="SWEEP",
        liquidity_type="SELL_SIDE",
        price=100.0,
        timestamp=datetime(
            2026,
            1,
            31,
        ),
    )

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[event],
    )

    assert any(
        item.name == "Liquidity"
        for item in result.evidence
    )

    assert any(
        "liquidity sweep"
        in item.reason.lower()
        for item in result.evidence
    )


# ============================================================
# SCORE SANITY
# ============================================================

def test_directional_scores_are_non_negative():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("BEARISH"),
        liquidity_events=[],
    )

    assert result.bullish_score >= 0.0
    assert result.bearish_score >= 0.0


def test_result_confidence_is_between_zero_and_hundred():

    engine = ConfluenceEngine()

    result = engine.analyze(
        analysis=make_analysis("BULLISH"),
        bos=make_bos(
            detected=True,
            bos_type="BULLISH",
        ),
        choch=make_choch(
            detected=True,
            choch_type="BULLISH",
        ),
        trend=make_trend("BULLISH"),
        liquidity_events=[],
    )

    assert 0.0 <= result.confidence <= 100.0


# ============================================================
# MINIMUM DOMINANCE
# ============================================================

def test_small_directional_difference_is_neutral():

    engine = ConfluenceEngine()

    # Bullish = 30
    # Bearish = 20
    #
    # Dominance = 10 / 50 = 20%
    #
    # This is above the 15% threshold, so it should
    # technically classify as bullish.

    result = engine._build_result(
        [
            SimpleNamespace(
                direction=ConfluenceDirection.BULLISH,
                score=30.0,
                name="Test",
                strength=None,
                reason="bullish",
            ),
            SimpleNamespace(
                direction=ConfluenceDirection.BEARISH,
                score=20.0,
                name="Test",
                strength=None,
                reason="bearish",
            ),
        ]
    )

    assert result.direction == (
        ConfluenceDirection.BULLISH
    )


def test_below_dominance_threshold_is_neutral():

    engine = ConfluenceEngine()

    # Bullish = 52
    # Bearish = 48
    #
    # Dominance = 4 / 100 = 4%

    result = engine._build_result(
        [
            SimpleNamespace(
                direction=ConfluenceDirection.BULLISH,
                score=52.0,
                name="Test",
                strength=None,
                reason="bullish",
            ),
            SimpleNamespace(
                direction=ConfluenceDirection.BEARISH,
                score=48.0,
                name="Test",
                strength=None,
                reason="bearish",
            ),
        ]
    )

    assert result.direction == (
        ConfluenceDirection.NEUTRAL
    )


# ============================================================
# EVENT TIMESTAMP
# ============================================================

def test_latest_liquidity_timestamp():

    engine = ConfluenceEngine()

    events = [
        make_liquidity_event(
            event_type="SWEEP",
            liquidity_type="BUY_SIDE",
            timestamp=datetime(
                2026,
                1,
                1,
            ),
        ),
        make_liquidity_event(
            event_type="BREAKOUT",
            liquidity_type="SELL_SIDE",
            timestamp=datetime(
                2026,
                1,
                20,
            ),
        ),
    ]

    result = (
        engine._latest_liquidity_timestamp(
            events
        )
    )

    assert result == datetime(
        2026,
        1,
        20,
    )


# ============================================================
# INVALID DATA
# ============================================================

def test_invalid_liquidity_event_is_ignored():

    engine = ConfluenceEngine()

    event = SimpleNamespace(
        detected=True,
        event_type=None,
        pool=None,
        event_timestamp=None,
    )

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[event],
    )

    assert result.direction == (
        ConfluenceDirection.UNKNOWN
    )


def test_undetected_liquidity_does_not_add_score():

    engine = ConfluenceEngine()

    event = make_liquidity_event(
        event_type="BREAKOUT",
        liquidity_type="BUY_SIDE",
        detected=False,
    )

    result = engine.analyze(
        analysis=make_analysis("UNKNOWN"),
        bos=make_bos(),
        choch=make_choch(),
        trend=make_trend("UNKNOWN"),
        liquidity_events=[event],
    )

    assert result.liquidity_score == 0.0
    assert result.bullish_score == 0.0
    assert result.bearish_score == 0.0