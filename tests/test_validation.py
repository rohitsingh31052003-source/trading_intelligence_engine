from types import SimpleNamespace

from engine.intelligence.validation import (
    SignalValidationEngine,
)

from engine.models.validation import (
    ExitReason,
    ValidationStatus,
)


def make_signal(
    direction="LONG",
    entry=100.0,
    stop_loss=98.0,
    take_profit=104.0,
):
    return SimpleNamespace(
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


def make_candle(
    high,
    low,
    timestamp=None,
):
    return SimpleNamespace(
        high=high,
        low=low,
        timestamp=timestamp,
    )


# ============================================================
# LONG
# ============================================================


def test_long_take_profit():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=105,
            low=100,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.WIN
    assert result.exit_reason == ExitReason.TAKE_PROFIT
    assert result.entry_triggered is True
    assert result.exit_price == 104.0
    assert result.realized_r == 2.0


def test_long_stop_loss():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=100,
            low=97,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.LOSS
    assert result.exit_reason == ExitReason.STOP_LOSS
    assert result.exit_price == 98.0
    assert result.realized_r == -1.0


def test_long_entry_not_triggered():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=99,
            low=95,
        ),
        make_candle(
            high=99.5,
            low=96,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.NOT_TRIGGERED
    assert result.entry_triggered is False


def test_long_expiry():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=102,
            low=99.5,
        ),
        make_candle(
            high=103,
            low=100,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.EXPIRED
    assert result.entry_triggered is True
    assert result.exit_price is None


def test_long_both_levels_touched_is_ambiguous():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=105,
            low=97,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.AMBIGUOUS
    assert result.exit_reason == ExitReason.BOTH_TOUCHED
    assert result.realized_r is None


# ============================================================
# SHORT
# ============================================================


def test_short_take_profit():

    engine = SignalValidationEngine()

    signal = make_signal(
        direction="SHORT",
        entry=100.0,
        stop_loss=102.0,
        take_profit=96.0,
    )

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=100,
            low=95,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.WIN
    assert result.exit_reason == ExitReason.TAKE_PROFIT
    assert result.exit_price == 96.0
    assert result.realized_r == 2.0


def test_short_stop_loss():

    engine = SignalValidationEngine()

    signal = make_signal(
        direction="SHORT",
        entry=100.0,
        stop_loss=102.0,
        take_profit=96.0,
    )

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=103,
            low=99,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.LOSS
    assert result.exit_reason == ExitReason.STOP_LOSS
    assert result.exit_price == 102.0
    assert result.realized_r == -1.0


def test_short_entry_not_triggered():

    engine = SignalValidationEngine()

    signal = make_signal(
        direction="SHORT",
        entry=100.0,
        stop_loss=102.0,
        take_profit=96.0,
    )

    candles = [
        make_candle(
            high=105,
            low=101,
        ),
        make_candle(
            high=106,
            low=102,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.NOT_TRIGGERED


def test_short_both_levels_touched_is_ambiguous():

    engine = SignalValidationEngine()

    signal = make_signal(
        direction="SHORT",
        entry=100.0,
        stop_loss=102.0,
        take_profit=96.0,
    )

    candles = [
        make_candle(
            high=103,
            low=95,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.status == ValidationStatus.AMBIGUOUS


# ============================================================
# RISK METRICS
# ============================================================


def test_long_mfe_and_mae():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=102,
            low=99,
        ),
        make_candle(
            high=103,
            low=99,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    assert result.entry_triggered is True
    assert result.mfe_r == 1.5
    assert result.mae_r == -0.5


def test_empty_candles_leave_signal_open():

    engine = SignalValidationEngine()

    signal = make_signal()

    result = engine.validate(
        signal,
        [],
    )

    assert result.status == ValidationStatus.OPEN
    assert result.entry_triggered is False


def test_max_candles_limits_validation_window():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=101,
            low=99,
        ),
        make_candle(
            high=105,
            low=100,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
        max_candles=1,
    )

    assert result.status == ValidationStatus.EXPIRED

def test_zero_max_candles_does_not_crash():
    engine = SignalValidationEngine()

    signal = make_signal()

    result = engine.validate(
        signal,
        [
            make_candle(
                high=105.0,
                low=95.0,
            )
        ],
        max_candles=0,
    )

    assert result.status == ValidationStatus.OPEN
    assert result.exit_reason == ExitReason.NONE
    assert result.candles_evaluated == 0
    assert result.entry_triggered is False


def test_invalid_ohlc_candles_do_not_crash():
    engine = SignalValidationEngine()

    signal = make_signal()

    result = engine.validate(
        signal,
        [
            make_candle(
                high=None,
                low=None,
            ),
            make_candle(
                high=95.0,
                low=105.0,
            ),
        ],
    )

    assert result.status == ValidationStatus.OPEN
    assert result.exit_reason == ExitReason.NONE
    assert result.entry_triggered is False