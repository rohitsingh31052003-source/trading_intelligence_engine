from types import SimpleNamespace

from engine.intelligence.validation import (
    SignalValidationEngine,
)


def make_signal():

    return SimpleNamespace(
        direction="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
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


def main():

    engine = SignalValidationEngine()

    signal = make_signal()

    candles = [
        make_candle(
            high=101.0,
            low=99.0,
        ),
        make_candle(
            high=102.0,
            low=99.5,
        ),
        make_candle(
            high=103.0,
            low=100.0,
        ),
        make_candle(
            high=105.0,
            low=101.0,
        ),
    ]

    result = engine.validate(
        signal,
        candles,
    )

    print()
    print("=" * 60)

    print(
        f"Validation       : "
        f"{result.status.value}"
    )

    print(
        f"Exit Reason      : "
        f"{result.exit_reason.value}"
    )

    print()

    print(
        f"Entry Price      : "
        f"{result.entry_price:.2f}"
    )

    print(
        f"Stop Loss        : "
        f"{result.stop_loss:.2f}"
    )

    print(
        f"Take Profit      : "
        f"{result.take_profit:.2f}"
    )

    print()

    print(
        f"Entry Triggered  : "
        f"{'YES' if result.entry_triggered else 'NO'}"
    )

    print(
        f"Exit Price       : "
        f"{result.exit_price:.2f}"
        if result.exit_price is not None
        else "Exit Price       : NONE"
    )

    print()

    print(
        f"Realized R       : "
        f"{result.realized_r:.2f}"
        if result.realized_r is not None
        else "Realized R       : NONE"
    )

    print(
        f"MFE              : "
        f"{result.mfe_r:.2f}R"
    )

    print(
        f"MAE              : "
        f"{result.mae_r:.2f}R"
    )

    print()

    print(
        f"Candles Evaluated: "
        f"{result.candles_evaluated}"
    )

    print(
        f"Duration         : "
        f"{result.duration_candles} candles"
    )

    print()

    print("Reason")

    print(
        f"- {result.reason}"
    )

    for detail in result.details:
        print(
            f"- {detail}"
        )

    print("=" * 60)


if __name__ == "__main__":
    main()