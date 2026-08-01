from datetime import datetime
from unittest import result

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.swings import SwingEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import StructureAnalysisEngine
from engine.intelligence.bos import BOSEngine

from engine.config.swing_config import SwingConfig


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
    result = bos_engine.analyze(analysis)

    print("=== Break of Structure ===")
    print()

    print(f"Current Bias      : {analysis.bias.name}")

    if analysis.latest:
        print(f"Latest Structure  : {analysis.latest.structure.name}")
        print(f"Latest Swing      : {analysis.latest.swing.swing_type.name}")
        print(f"Latest Price      : {analysis.latest.swing.price:.2f}")

    print()

    print(f"Detected          : {result.detected}")
    print(f"Type              : {result.bos_type.name}")
    print(f"Confidence        : {result.confidence:.1f}")

    if result.trigger_swing:
        print(f"Trigger Swing     : {result.trigger_swing.swing_type.name}")
        print(f"Trigger Price     : {result.trigger_swing.price:.2f}")

    print("\nReasons:")
    for reason in result.reasons:
        print(f" - {reason}")

if __name__ == "__main__":
    main()