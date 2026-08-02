from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.bos import BOSEngine
from engine.intelligence.choch import CHOCHEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import StructureAnalysisEngine
from engine.intelligence.swings import SwingEngine
from engine.intelligence.trend import TrendEngine


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

    structures = MarketStructureEngine().analyze(swings)

    analysis = StructureAnalysisEngine().analyze(structures)

    bos = BOSEngine().analyze(analysis)

    choch = CHOCHEngine().analyze(
        structures,
        analysis,
        bos,
    )

    trend = TrendEngine().analyze(
        analysis,
        bos,
        choch,
    )

    print("=== Market Trend ===")
    print()

    print(f"State          : {trend.state.name}")
    print(f"Confidence     : {trend.confidence:.1f}")
    print(f"Previous Bias  : {trend.previous_bias.name}")
    print(f"Current Bias   : {trend.current_bias.name}")

    print()
    print("Reasons:")

    for reason in trend.reasons:
        print(f" - {reason}")


if __name__ == "__main__":
    main()