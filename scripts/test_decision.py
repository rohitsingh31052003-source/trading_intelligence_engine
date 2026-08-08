from engine.intelligence.confluence import (
    ConfluenceEngine,
)
from engine.intelligence.decision import (
    DecisionEngine,
)
from engine.models.confluence import (
    ConfluenceDirection,
)


def main():

    confluence_engine = ConfluenceEngine()
    decision_engine = DecisionEngine()

    # ---------------------------------------------------------
    # Demonstration scenario
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

    decision = decision_engine.analyze(
        confluence
    )

    print()
    print("=" * 60)
    print("                 DECISION CONTEXT")
    print("=" * 60)
    print()

    print(
        f"Direction        : "
        f"{decision.direction.value}"
    )

    print(
        f"Confidence       : "
        f"{decision.confidence:.1f}"
    )

    print(
        f"Setup Quality    : "
        f"{decision.setup_quality.value}"
    )

    print(
        f"Status           : "
        f"{decision.status.value}"
    )

    print(
        f"Trade Eligible   : "
        f"{'YES' if decision.trade_eligible else 'NO'}"
    )

    print(
        f"Bullish Score    : "
        f"{decision.bullish_score:.1f}"
    )

    print(
        f"Bearish Score    : "
        f"{decision.bearish_score:.1f}"
    )

    print(
        f"Net Score        : "
        f"{decision.net_score:.1f}"
    )

    print(
        f"Conflict         : "
        f"{decision.conflict}"
    )

    print(
        f"Evidence Quality : "
        f"{decision.evidence_quality:.1f}"
    )

    print()
    print("## Reasons")
    print()

    for reason in decision.reasons:
        print(f"- {reason}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()