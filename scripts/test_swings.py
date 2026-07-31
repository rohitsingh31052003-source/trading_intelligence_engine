from datetime import UTC, datetime

from engine.data.yahoo_provider import YahooFinanceProvider
from engine.intelligence.swings import SwingEngine

provider = YahooFinanceProvider()
provider.connect()

candles = provider.get_history(
    symbol="RELIANCE.NS",
    start=datetime(2025, 1, 1, tzinfo=UTC),
    end=datetime(2025, 2, 1, tzinfo=UTC),
    interval="1d",
)

engine = SwingEngine(lookback=2)

swings = engine.detect(candles)

print(f"Detected {len(swings)} swings\n")

for swing in swings:
    print(swing)