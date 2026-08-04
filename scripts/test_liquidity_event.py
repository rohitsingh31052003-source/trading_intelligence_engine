"""
Demo script for Liquidity Event Engine.
"""

from datetime import datetime, timedelta

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.swings import SwingEngine
from engine.intelligence.liquidity import LiquidityEngine
from engine.intelligence.liquidity_event import LiquidityEventEngine


def main():

    provider = YahooFinanceProvider()

    end = datetime.now()
    start = end - timedelta(days=180)

    candles = provider.fetch(
        symbol="AAPL",
        interval="1d",
        lookback_days=180,
    )

    swing_engine = SwingEngine()
    liquidity_engine = LiquidityEngine()
    event_engine = LiquidityEventEngine()

    swings = swing_engine.detect(candles)

    pools = liquidity_engine.detect(swings)

    events = event_engine.analyze(
        pools,
        candles,
    )

    print()
    print("=" * 60)
    print("           LIQUIDITY EVENTS")
    print("=" * 60)

    if not events:
        print("No liquidity events detected.")
        return

    for event in events:

        direction = (
            "BUY SIDE"
            if event.pool.liquidity_type.value == "BUY_SIDE"
            else "SELL SIDE"
        )

        print()
        print(direction)
        print("-" * 60)

        print(f"Pool Price      : {event.pool.price:.2f}")
        print(f"Event           : {event.event_type.value}")
        print(
            f"Detected        : {'YES' if event.detected else 'NO'}"
        )
        print(f"Confidence      : {event.confidence:.0f}")

        if event.event_price is not None:
            print(
                f"Event Price     : {event.event_price:.2f}"
            )

        if event.event_timestamp is not None:
            print(
                "Time            : "
                f"{event.event_timestamp:%Y-%m-%d %H:%M}"
            )

        print()
        print("Reasons")

        for reason in event.reasons:
            print(f" - {reason}")

        print()
        print("-" * 60)


if __name__ == "__main__":
    main()