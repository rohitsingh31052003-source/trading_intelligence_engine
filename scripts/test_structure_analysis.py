"""
Demo for market structure analysis.
"""

from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import (
    StructureAnalysisEngine,
)
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

    structures = structure_engine.analyze(swings)

    analysis_engine = StructureAnalysisEngine()

    analysis = analysis_engine.analyze(structures)

    print("\n=== Structure Analysis ===\n")

    print(f"Bias: {analysis.bias.name}")
    print(f"Bullish Sequence: {analysis.bullish_sequence}")
    print(f"Bearish Sequence: {analysis.bearish_sequence}")
    print(f"Structure Intact: {analysis.structure_intact}")

    print("\nLatest Structure:")

    if analysis.latest is not None:
        print(analysis.latest.structure.name)
    else:
        print("None")


if __name__ == "__main__":
    main()
