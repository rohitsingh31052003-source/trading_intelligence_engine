"""
Demo for market structure detection.
"""

from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.swings import SwingEngine


def main() -> None:
    provider = YahooFinanceProvider()

    candles = provider.get_history(
        symbol="RELIANCE.NS",
        start=datetime(2025, 1, 1),
        end=datetime(2025, 2, 1),
        interval="1d",
    )

    swing_engine = SwingEngine(SwingConfig())

    swings = swing_engine.detect(candles)

    structure_engine = MarketStructureEngine()

    structure = structure_engine.analyze(swings)

    print("\n=== Market Structure ===\n")

    for point in structure:
        print(
            f"{point.swing.timestamp.date()} "
            f"{point.swing.swing_type.name:<5} "
            f"{point.structure.name}"
        )


if __name__ == "__main__":
    main()