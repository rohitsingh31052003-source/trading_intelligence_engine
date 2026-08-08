"""
Liquidity Event Demo

Pipeline
--------
Yahoo Finance
    ↓
Swing Engine
    ↓
Liquidity Engine
    ↓
Liquidity Event Engine
    ↓
Liquidity Event Report
"""

from datetime import datetime, timedelta

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.liquidity import LiquidityEngine
from engine.intelligence.liquidity_event import LiquidityEventEngine
from engine.intelligence.swings import SwingEngine


def print_event(event):
    """Print a detailed liquidity event report."""

    print("=" * 60)
    print("                 LIQUIDITY EVENT")
    print("=" * 60)
    print()

    print(event.pool.liquidity_type.value.replace("_", " "))
    print()

    print(f"Pool Price       : {event.pool.price:.2f}")
    print(f"Event            : {event.event_type.value}")
    print(
        f"Detected         : "
        f"{'YES' if event.detected else 'NO'}"
    )

    if event.event_price is not None:
        print(f"Event Price      : {event.event_price:.2f}")

    if event.event_timestamp is not None:
        print(
            f"Event Time       : "
            f"{event.event_timestamp.strftime('%Y-%m-%d')}"
        )

    print()
    print(f"Confidence       : {event.confidence:.1f}")

    print()
    print("Evidence")
    print("-" * 60)

    evidence = event.evidence

    print(
        f"Liquidity Breach       : "
        f"{'YES' if evidence.liquidity_breached else 'NO'}"
    )

    print(
        f"Rejection Confirmed    : "
        f"{'YES' if evidence.rejection_confirmed else 'NO'}"
    )

    print(
        f"Continuation Confirmed : "
        f"{'YES' if evidence.continuation_confirmed else 'NO'}"
    )

    print(
        f"Candles Checked        : "
        f"{evidence.candles_checked}"
    )

    print(
        f"Rejection Strength     : "
        f"{evidence.rejection_strength:.1f}"
    )

    print("-" * 60)

    print()
    print("Reasons")

    for reason in event.reasons:
        print(f" - {reason}")

    print()
    print("-" * 60)
    print()


def main():

    # ---------------------------------------------------------
    # 1. Download market data
    # ---------------------------------------------------------

    provider = YahooFinanceProvider()

    candles = provider.fetch(
        symbol="AAPL",
        interval="1d",
        lookback_days=180,
    )

    print(f"Loaded {len(candles)} candles.")
    print()

    # ---------------------------------------------------------
    # 2. Detect swings
    # ---------------------------------------------------------

    swing_engine = SwingEngine()

    swings = swing_engine.detect(candles)

    print(f"Detected {len(swings)} swings.")
    print()

    # ---------------------------------------------------------
    # 3. Detect liquidity pools
    # ---------------------------------------------------------

    liquidity_engine = LiquidityEngine()

    pools = liquidity_engine.detect(swings)

    print(f"Detected {len(pools)} liquidity pools.")
    print()

    if not pools:
        print("No liquidity pools detected.")
        return

    # ---------------------------------------------------------
    # 4. Analyze liquidity events
    # ---------------------------------------------------------

    event_engine = LiquidityEventEngine()

    events = event_engine.analyze(
        pools=pools,
        candles=candles,
    )

    # ---------------------------------------------------------
    # 5. Print results
    # ---------------------------------------------------------

    print("=" * 60)
    print("              LIQUIDITY EVENTS")
    print("=" * 60)
    print()

    for event in events:
        print_event(event)


if __name__ == "__main__":
    main()