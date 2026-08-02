from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.bos import BOSEngine
from engine.intelligence.choch import CHOCHEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import (
    StructureAnalysisEngine,
)
from engine.intelligence.swings import SwingEngine


def main():

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

    bos_engine = BOSEngine()
    bos = bos_engine.analyze(analysis)

    choch_engine = CHOCHEngine()
    result = choch_engine.analyze(
        structures,
        analysis,
        bos,
    )

    print("=== Change of Character ===")
    print()

    print(f"Previous Bias : {result.previous_bias.name}")
    print(f"New Bias      : {result.new_bias.name}")
    print(f"Detected      : {result.detected}")
    print(f"Type          : {result.choch_type.name}")
    print(f"Confidence    : {result.confidence:.1f}")

    print("\nReasons:")

    for reason in result.reasons:
        print(f" - {reason}")


if __name__ == "__main__":
    main()
