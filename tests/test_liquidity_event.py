
from datetime import datetime, timedelta

from engine.config.liquidity_event_config import LiquidityEventConfig
from engine.intelligence.liquidity_event import LiquidityEventEngine
from engine.models.liquidity import (
    LiquidityPool,
    LiquidityStatus,
    LiquidityType,
)
from engine.models.liquidity_event import (
    LiquidityEventType,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.strength import StrengthCategory


# ============================================================
# HELPERS
# ============================================================

def make_candle(
    *,
    timestamp,
    open,
    high,
    low,
    close,
    volume=1000,
):
    """
    Create a valid OHLCV candle.

    IMPORTANT:
    open and close must be between low and high.
    """

    return OHLCVCandle(
        timestamp=timestamp,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def make_pool(
    *,
    liquidity_type,
    price=280.0,
    created_at=None,
):
    """Create a test liquidity pool."""

    if created_at is None:
        created_at = datetime(2026, 1, 1)

    return LiquidityPool(
        price=price,
        liquidity_type=liquidity_type,
        created_at=created_at,
        swing_count=3,
        strength=30.0,
        category=StrengthCategory.MODERATE,
        status=LiquidityStatus.ACTIVE,
    )


def make_engine(
    *,
    confirmation_candles=3,
):
    """Create an engine with a controlled test configuration."""

    config = LiquidityEventConfig(
        confirmation_candles=confirmation_candles,
    )

    return LiquidityEventEngine(config=config)


# ============================================================
# BASIC TESTS
# ============================================================

def test_empty_input_returns_empty_list():

    engine = make_engine()

    result = engine.analyze([], [])

    assert result == []


def test_pool_never_breached():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=275,
            high=279,
            low=273,
            close=278,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=278,
            high=279,
            low=274,
            close=277,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.event_type == LiquidityEventType.NONE
    assert result.evidence.liquidity_breached is False
    assert result.confidence == 0.0


# ============================================================
# BUY-SIDE
# ============================================================

def test_buy_side_breakout_after_confirmation():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),

        # Confirmation 1
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=290,
            low=283,
            close=288,
        ),

        # Confirmation 2
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=288,
            high=293,
            low=286,
            close=291,
        ),

        # Confirmation 3
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=291,
            high=295,
            low=289,
            close=294,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.event_type == LiquidityEventType.BREAKOUT
    assert result.evidence.liquidity_breached is True
    assert result.evidence.continuation_confirmed is True
    assert result.evidence.rejection_confirmed is False
    assert result.confidence > 0
    assert result.confidence <= 100


def test_buy_side_sweep_after_two_candles():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),

        # Rejection
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),

        # Additional candle
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=278,
            high=281,
            low=277,
            close=279,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.event_type == LiquidityEventType.SWEEP
    assert result.evidence.liquidity_breached is True
    assert result.evidence.rejection_confirmed is True
    assert result.evidence.continuation_confirmed is False
    assert result.confidence > 0
    assert result.confidence <= 100


def test_buy_side_equal_high_is_not_breach():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=280,
            low=278,
            close=279,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.event_type == LiquidityEventType.NONE
    assert result.evidence.liquidity_breached is False


# ============================================================
# SELL-SIDE
# ============================================================

def test_sell_side_breakout_after_confirmation():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=281,
            high=282,
            low=273,
            close=275,
        ),

        # Confirmation 1
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=275,
            high=278,
            low=271,
            close=273,
        ),

        # Confirmation 2
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=273,
            high=276,
            low=268,
            close=270,
        ),

        # Confirmation 3
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=270,
            high=272,
            low=265,
            close=267,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.event_type == LiquidityEventType.BREAKOUT
    assert result.evidence.liquidity_breached is True
    assert result.evidence.continuation_confirmed is True
    assert result.evidence.rejection_confirmed is False
    assert result.confidence > 0
    assert result.confidence <= 100


def test_sell_side_sweep_after_two_candles():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=281,
            high=282,
            low=273,
            close=275,
        ),

        # Rejection back above liquidity
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=275,
            high=283,
            low=274,
            close=282,
        ),

        # Additional candle
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=282,
            high=284,
            low=279,
            close=281,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.event_type == LiquidityEventType.SWEEP
    assert result.evidence.liquidity_breached is True
    assert result.evidence.rejection_confirmed is True
    assert result.evidence.continuation_confirmed is False
    assert result.confidence > 0
    assert result.confidence <= 100


def test_sell_side_equal_low_is_not_breach():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=281,
            high=282,
            low=280,
            close=281,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.event_type == LiquidityEventType.NONE
    assert result.evidence.liquidity_breached is False


# ============================================================
# CONFIRMATION WINDOW
# ============================================================

def test_custom_confirmation_window():

    config = LiquidityEventConfig(
        confirmation_candles=5,
    )

    assert config.confirmation_candles == 5


def test_default_confirmation_window():

    config = LiquidityEventConfig()

    assert config.confirmation_candles == 3


def test_sweep_can_be_confirmed_on_third_candle():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),

        # No rejection yet
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=281,
            close=283,
        ),

        # Rejection
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=283,
            high=284,
            low=276,
            close=278,
        ),

        # Extra candle
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=278,
            high=281,
            low=277,
            close=279,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.SWEEP
    assert result.detected is True
    assert result.evidence.rejection_confirmed is True


# ============================================================
# REJECTION STRENGTH
# ============================================================

def test_strong_rejection():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=280,
            high=290,
            low=278,
            close=289,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=289,
            high=290,
            low=275,
            close=276,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.SWEEP
    assert result.evidence.rejection_strength > 70
    assert result.evidence.rejection_strength <= 100


def test_weak_rejection():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # Breach
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=282,
            low=278,
            close=281,
        ),

        # Weak rejection:
        # only slightly below liquidity
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=281,
            high=282,
            low=279,
            close=279.5,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.SWEEP
    assert result.evidence.rejection_confirmed is True
    assert result.evidence.rejection_strength == min(100.0, max(0.0, result.evidence.rejection_strength))


# ============================================================
# EVIDENCE
# ============================================================

def test_liquidity_breach_evidence():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.evidence.liquidity_breached is True
    assert result.evidence.candles_checked >= 1


def test_continuation_confirmed():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=285,
            low=278,
            close=284,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=284,
            high=290,
            low=282,
            close=288,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=288,
            high=294,
            low=286,
            close=292,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=292,
            high=296,
            low=290,
            close=294,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.evidence.continuation_confirmed is True
    assert result.evidence.rejection_confirmed is False


def test_rejection_not_continuation():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.evidence.rejection_confirmed is True
    assert result.evidence.continuation_confirmed is False


# ============================================================
# CONFIDENCE
# ============================================================

def test_sweep_confidence_uses_evidence():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=290,
            low=278,
            close=289,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=289,
            high=290,
            low=275,
            close=276,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.SWEEP
    assert result.confidence > 70
    assert result.confidence <= 100


def test_breakout_confidence_uses_evidence():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=285,
            low=278,
            close=284,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=284,
            high=290,
            low=282,
            close=288,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=288,
            high=294,
            low=286,
            close=292,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=292,
            high=296,
            low=290,
            close=294,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.BREAKOUT
    assert result.confidence > 70
    assert result.confidence <= 100


def test_confidence_never_exceeds_100():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=280,
            high=300,
            low=270,
            close=299,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=299,
            high=300,
            low=260,
            close=260,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=260,
            high=265,
            low=250,
            close=255,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.confidence <= 100


def test_confidence_never_negative():

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    engine = make_engine()

    result = engine.analyze([pool], [])[0]

    assert result.confidence >= 0


# ============================================================
# TIMESTAMP
# ============================================================

def test_timestamp_matches_breach_candle():

    pool_time = datetime(2026, 1, 1)

    breach_time = pool_time + timedelta(days=1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=breach_time,
            open=279,
            high=287,
            low=278,
            close=285,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.event_timestamp == breach_time


# ============================================================
# TIMESTAMP FILTERING
# ============================================================

def test_candles_before_pool_are_ignored():

    pool_time = datetime(2026, 1, 2)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        # This breaches the pool price but happened BEFORE
        # the pool existed. It must be ignored.
        make_candle(
            timestamp=datetime(2026, 1, 1),
            open=281,
            high=290,
            low=279,
            close=289,
        ),

        # No breach after pool creation.
        make_candle(
            timestamp=datetime(2026, 1, 3),
            open=275,
            high=279,
            low=273,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.event_type == LiquidityEventType.NONE


# ============================================================
# LAST-CANDLE EDGE CASE
# ============================================================

def test_breach_on_last_candle_waits_for_confirmation():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=275,
            high=279,
            low=273,
            close=278,
        ),

        # Breach is the final available candle.
        # There is no confirmation candle yet.
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=279,
            high=287,
            low=278,
            close=285,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.event_type == LiquidityEventType.NONE
    assert result.evidence.liquidity_breached is True
    assert result.evidence.candles_checked == 0


# ============================================================
# REASONS
# ============================================================

def test_reasons_are_populated_for_sweep():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert len(result.reasons) > 0

    reasons = " ".join(result.reasons).lower()

    assert "liquidity" in reasons


def test_reasons_are_populated_for_breakout():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=285,
            low=278,
            close=284,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=284,
            high=290,
            low=282,
            close=288,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=3),
            open=288,
            high=294,
            low=286,
            close=292,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=4),
            open=292,
            high=296,
            low=290,
            close=294,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert len(result.reasons) > 0

    reasons = " ".join(result.reasons).lower()

    assert "liquidity" in reasons


# ============================================================
# EVENT MODEL INTEGRITY
# ============================================================

def test_event_contains_evidence():

    pool_time = datetime(2026, 1, 1)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=280.0,
        created_at=pool_time,
    )

    candles = [
        make_candle(
            timestamp=pool_time + timedelta(days=1),
            open=279,
            high=287,
            low=278,
            close=285,
        ),
        make_candle(
            timestamp=pool_time + timedelta(days=2),
            open=285,
            high=286,
            low=276,
            close=278,
        ),
    ]

    engine = make_engine()

    result = engine.analyze([pool], candles)[0]

    assert result.evidence is not None
    assert result.evidence.candles_checked >= 1


def test_event_type_is_valid():

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    engine = make_engine()

    result = engine.analyze([pool], [])[0]

    assert result.event_type in (
        LiquidityEventType.NONE,
        LiquidityEventType.SWEEP,
        LiquidityEventType.BREAKOUT,
        LiquidityEventType.FAILED_BREAKOUT,
    )

