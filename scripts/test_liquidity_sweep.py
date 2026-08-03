from datetime import datetime, timedelta

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.liquidity import LiquidityEngine
from engine.intelligence.liquidity_sweep import LiquiditySweepEngine
from engine.intelligence.swings import SwingEngine


provider = YahooFinanceProvider()

end = datetime.now()
start = end - timedelta(days=180)

candles = provider.get_history(
    symbol="AAPL",
    start=start,
    end=end,
    interval="1d",
)

swings = SwingEngine().detect(candles)

pools = LiquidityEngine().detect(swings)

sweeps = LiquiditySweepEngine().analyze(
    pools,
    candles,
)

print("\n=== Liquidity Sweeps ===\n")

for sweep in sweeps:

    print(
        sweep.pool.liquidity_type.value.replace("_", " ")
    )
    print()

    print(f"Pool Price     : {sweep.pool.price:.2f}")

    print(
        f"Detected       : {'YES' if sweep.detected else 'NO'}"
    )

    if sweep.detected:

        print(f"Sweep Price    : {sweep.sweep_price:.2f}")

        print(
            f"Time           : {sweep.sweep_timestamp.date()}"
        )

    print(f"Confidence     : {sweep.confidence:.0f}")

    print()

    print("Reasons")

    for reason in sweep.reasons:
        print(f" - {reason}")

    print("\n--------------------------------\n")