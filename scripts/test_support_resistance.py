from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.support_resistance import StructuralLevelsEngine
from engine.intelligence.swings import SwingEngine


def main():

    provider = YahooFinanceProvider()

    try:
        candles = provider.get_history(
            symbol="RELIANCE.NS",
            start=datetime(2025, 1, 1),
            end=datetime(2025, 2, 10),
            interval="1d",
        )

    except Exception as error:
        print("=== Data Download Failed ===")
        print(error)
        return

    swings = SwingEngine(
        SwingConfig()
    ).detect(candles)

    # Optional but keeps the pipeline consistent
    MarketStructureEngine().analyze(swings)

    levels = StructuralLevelsEngine().detect(swings)

    print("=== Structural Levels ===")
    print()

    if not levels:
        print("No structural levels detected.")
        return

    for level in levels:

        print(level.level_type.name)
        print()

        print(f"Price      : {level.price:.2f}")
        print(f"Touches    : {level.touches}")
        print(f"Strength   : {level.strength:.1f}")
        print(f"Status     : {level.status.name}")

        print("-" * 28)


if __name__ == "__main__":
    main()