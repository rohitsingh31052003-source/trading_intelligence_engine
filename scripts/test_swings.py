from datetime import UTC, datetime

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.swings import SwingEngine
from engine.config.swing_config import SwingConfig

provider = YahooFinanceProvider()
provider.connect()

candles = provider.get_history(
    symbol="RELIANCE.NS",
    start=datetime(2025, 1, 1, tzinfo=UTC),
    end=datetime(2025, 2, 1, tzinfo=UTC),
    interval="1d",
)

engine = SwingEngine(SwingConfig(lookback=2))

swings = engine.detect(candles)

print(f"Detected {len(swings)} swings\n")

for swing in swings:
    print(
    f"{swing.swing_type.name:<5} "
    f"{swing.timestamp.date()} "
    f"Price={swing.price:.2f} "
    f"Move={swing.evidence.move_percent:.2f}% "
    f"Confidence={swing.evidence.confidence:.1f}"
    )