from datetime import datetime

from engine.config.swing_config import SwingConfig
from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.bos import BOSEngine
from engine.intelligence.choch import CHOCHEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import StructureAnalysisEngine
from engine.intelligence.swings import SwingEngine
from engine.intelligence.trend import TrendEngine
from engine.models import trend


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

    print(f"State         : {trend.state.name}")
    print(f"Confidence    : {trend.confidence}%")

    print()
    print("Evidence")
    print()

    print(f"Structure     : {trend.evidence.structure_score}")
    print(f"Sequence      : {trend.evidence.sequence_score}")
    print(f"BOS           : {trend.evidence.bos_score}")
    print(f"CHOCH         : {trend.evidence.choch_score}")
    print(f"Quality       : {trend.evidence.quality_score}")

    print("-----------------------")
    print(f"Total         : {trend.evidence.total}")


if __name__ == "__main__":
    main()