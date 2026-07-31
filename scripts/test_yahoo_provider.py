from datetime import UTC, datetime

from engine.data.yahoo_provider import YahooFinanceProvider

provider = YahooFinanceProvider()
provider.connect()

candles = provider.get_history(
    symbol="RELIANCE.NS",
    start=datetime(2025, 1, 1),
    end=datetime(2025, 2, 1),
    interval="1d",
)


print(f"Provider: {provider.provider_name}")
print(f"Connected: {provider.is_available()}")
print(f"Candles downloaded: {len(candles)}")

if candles:
    print()
    print("First Candle:")
    print(candles[0])
    print()
    print("Last Candle:")
    print(candles[-1])