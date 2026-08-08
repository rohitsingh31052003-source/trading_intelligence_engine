"""
Demo script for the Signal Generation Engine (Sprint 11C).

Runs the full pipeline:

    Confluence Engine (11A)
      -> Decision Engine (11B)
      -> Signal Engine (11C)

and prints the resulting trade setup.

Numerical values come from the demonstration data below;
they are not hardcoded in the print formatting.
"""

from engine.intelligence.confluence import (
    ConfluenceEngine,
)
from engine.intelligence.decision import (
    DecisionEngine,
)
from engine.intelligence.signal import (
    SignalContext,
    SignalEngine,
)
from engine.models.confluence import (
    ConfluenceDirection,
)


def main():
    """
    Build a bullish demonstration scenario and print the
    resulting SignalResult.
    """

    confluence_engine = ConfluenceEngine()
    decision_engine = DecisionEngine()
    signal_engine = SignalEngine()

    # ---------------------------------------------------------
    # Demonstration scenario (11A inputs)
    # ---------------------------------------------------------

    analysis = type(
        "Analysis",
        (),
        {
            "bias": "BULLISH",
        },
    )()

    trend = type(
        "Trend",
        (),
        {
            "state": "BULLISH",
        },
    )()

    bos = type(
        "BOS",
        (),
        {
            "detected": True,
            "type": "BULLISH",
        },
    )()

    choch = type(
        "CHOCH",
        (),
        {
            "detected": False,
            "type": None,
        },
    )()

    confluence = confluence_engine.analyze(
        analysis=analysis,
        bos=bos,
        choch=choch,
        trend=trend,
        liquidity_events=[],
    )

    decision = decision_engine.analyze(confluence)

    # ---------------------------------------------------------
    # Signal context (11C inputs)
    #
    # Numerical values are demonstration data. The trigger
    # close, structural low (stop anchor) and structural
    # high (target anchor) are the only market context
    # supplied to the Signal Engine.
    # ---------------------------------------------------------

    context = SignalContext(
        trigger_close=100.0,
        liquidity_level=98.0,
        structure_break_level=104.0,
    )

    signal = signal_engine.analyze(decision, context)

    # ---------------------------------------------------------
    # Output
    # ---------------------------------------------------------

    print()
    print("=" * 60)
    print("                   SIGNAL RESULT")
    print("=" * 60)
    print()

    print(
        f"Direction        : "
        f"{decision.direction.value}"
    )

    print(
        f"Confidence       : "
        f"{signal.confidence:.1f}"
    )

    print(
        f"Signal State     : "
        f"{signal.state.value}"
    )

    print(
        f"Signal Quality   : "
        f"{signal.quality.value}"
    )

    print(
        f"Trade Eligible   : "
        f"{'YES' if signal.eligible else 'NO'}"
    )

    print()

    if signal.entry_price is not None:
        print(
            f"Entry Price      : "
            f"{signal.entry_price:.2f}"
        )
    else:
        print("Entry Price      : N/A")

    if signal.stop_loss is not None:
        print(
            f"Stop Loss        : "
            f"{signal.stop_loss:.2f}"
        )
    else:
        print("Stop Loss        : N/A")

    if signal.take_profit is not None:
        print(
            f"Take Profit      : "
            f"{signal.take_profit:.2f}"
        )
    else:
        print("Take Profit      : N/A")

    print()

    print(
        f"Risk / Unit      : "
        f"{signal.risk_per_unit:.2f}"
    )

    print(
        f"Reward / Unit    : "
        f"{signal.reward_per_unit:.2f}"
    )

    print(
        f"Risk / Reward    : "
        f"{signal.risk_reward_ratio:.2f}"
    )

    print()

    if signal.invalidation.price is not None:
        invalidation_text = (
            f"Price below "
            f"{signal.invalidation.price:.2f}"
            if signal.direction.value == "LONG"
            else f"Price above "
            f"{signal.invalidation.price:.2f}"
        )
    else:
        invalidation_text = signal.invalidation.condition

    print(f"Invalidation     : {invalidation_text}")

    print()
    print("## Reasons")
    print()

    for reason in signal.reasons:
        print(f"* {reason}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
