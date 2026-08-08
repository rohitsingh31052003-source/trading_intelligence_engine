
"""
Confluence Engine Demo

Sprint 11A

Demonstrates the ConfluenceEngine using controlled example
evidence and prints the directional balance of the result.
"""

from types import SimpleNamespace

from engine.intelligence.confluence import ConfluenceEngine
from engine.models.confluence import ConfluenceDirection


def make_analysis(bias):
    """Create a minimal structure-analysis object."""
    return SimpleNamespace(
        bias=bias,
    )


def make_trend(state):
    """Create a minimal trend object."""
    return SimpleNamespace(
        state=state,
    )


def make_bos(
    detected=False,
    bos_type=None,
):
    """Create a minimal BOS object."""
    return SimpleNamespace(
        detected=detected,
        type=bos_type,
    )


def make_choch(
    detected=False,
    choch_type=None,
):
    """Create a minimal CHOCH object."""
    return SimpleNamespace(
        detected=detected,
        type=choch_type,
    )


def print_result(result):
    """Print the ConfluenceResult in a readable format."""

    print()
    print("=" * 60)
    print("                 CONFLUENCE RESULT")
    print("=" * 60)

    print(
        f"Direction        : "
        f"{result.direction.value}"
    )

    print(
        f"Confidence       : "
        f"{result.confidence:.1f}"
    )

    print(
        f"Dominant Score   : "
        f"{result.score:.1f}"
    )

    print(
        f"Bullish Score    : "
        f"{result.bullish_score:.1f}"
    )

    print(
        f"Bearish Score    : "
        f"{result.bearish_score:.1f}"
    )

    print(
        f"Net Score        : "
        f"{result.net_score:.1f}"
    )

    print(
        f"Conflict         : "
        f"{result.conflict}"
    )

    # Liquidity contribution is identified from the
    # visible evidence entries.
    liquidity_score = sum(
        item.score
        for item in result.evidence
        if item.name == "Liquidity"
    )

    print(
        f"Liquidity Score  : "
        f"{liquidity_score:.1f}"
    )

    print()

    if result.direction == ConfluenceDirection.BULLISH:
        decision = "BULLISH DIRECTIONAL EDGE"

    elif result.direction == ConfluenceDirection.BEARISH:
        decision = "BEARISH DIRECTIONAL EDGE"

    else:
        decision = "NO DIRECTIONAL EDGE"

    print(
        f"Decision         : "
        f"{decision}"
    )

    print()
    print("## Evidence")

    if not result.evidence:
        print("No evidence available.")

    else:
        for item in result.evidence:

            print(
                f"- {item.name:<15} | "
                f"{item.direction.value:<8} | "
                f"{item.score:>5.1f} | "
                f"{item.strength.value:<8}"
            )

            print(
                f"  {item.reason}"
            )

    print()

    print("## Reasons")

    for reason in result.reasons:
        print(
            f"- {reason}"
        )

    print("=" * 60)
    print()


def main():
    """
    Run a controlled ConfluenceEngine demonstration.

    The demo intentionally creates the inputs locally.
    Nothing is assumed to already exist in this script.
    """

    engine = ConfluenceEngine()

    # ---------------------------------------------------------
    # CREATE INPUTS
    # ---------------------------------------------------------

    analysis = make_analysis(
        "BULLISH"
    )

    trend = make_trend(
        "BEARISH"
    )

    bos = make_bos(
        detected=True,
        bos_type="BULLISH",
    )

    choch = make_choch(
        detected=True,
        choch_type="BEARISH",
    )

    # No liquidity events are required for this basic demo.
    #
    # If your LiquidityEvent model is available, these can
    # later be replaced with real liquidity events.
    liquidity_events = []

    # ---------------------------------------------------------
    # RUN CONFLUENCE
    # ---------------------------------------------------------

    result = engine.analyze(
        analysis=analysis,
        bos=bos,
        choch=choch,
        trend=trend,
        liquidity_events=liquidity_events,
    )

    # ---------------------------------------------------------
    # DISPLAY RESULT
    # ---------------------------------------------------------

    print_result(result)


if __name__ == "__main__":
    main()
