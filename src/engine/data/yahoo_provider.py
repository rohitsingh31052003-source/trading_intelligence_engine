"""
Yahoo Finance market data provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math

import yfinance as yf

from engine.data.base_provider import BaseDataProvider
from engine.models.ohlcv import OHLCVCandle
from engine.data.normalizer import DataNormalizer


class YahooFinanceProvider(BaseDataProvider):
    """
    Yahoo Finance implementation.
    """

    def __init__(self) -> None:
        super().__init__("Yahoo Finance")

    def connect(self) -> None:
        """
        Yahoo Finance requires no persistent connection.
        """
        self._connected = True

    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[OHLCVCandle]:
        """
        Download historical OHLCV data.
        """

        data = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            progress=False,
            auto_adjust=False,
        )

        if data.empty:
            raise ValueError(
                f"No market data returned for "
                f"{symbol} between "
                f"{start.date()} and {end.date()}."
            )
        data = DataNormalizer.normalize(data)

        data = data.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
    ]
)
        # Flatten MultiIndex columns.

        candles: list[OHLCVCandle] = []

        for timestamp, row in data.iterrows():
            timestamp = (
                timestamp.to_pydatetime()
                if hasattr(timestamp, "to_pydatetime")
                else timestamp
            )

            open_price = float(row["Open"])
            high_price = float(row["High"])
            low_price = float(row["Low"])
            close_price = float(row["Close"])
            volume = float(row["Volume"])

            if any(
                math.isnan(v)
                for v in (
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
    )
):
                continue

            candles.append(
                OHLCVCandle(
                    timestamp=timestamp,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
    )
)

        return candles

    def get_latest(
        self,
        symbol: str,
        interval: str,
    ) -> OHLCVCandle:

        end = datetime.now()
        if interval == "1d":
            end -= timedelta(days=1)
            start = end - timedelta(days=10)

        candles = self.get_history(
            symbol=symbol,
            start=start,
            end=end,
            interval=interval,
        )

        if not candles:
            raise ValueError("No latest candle available.")

        return candles[-1]

    def fetch(
        self,
        symbol: str,
        interval: str = "1d",
        lookback_days: int = 180,
    ):

        if not self.connected:
            self.connect()

        end = datetime.now()
        if interval == "1d":
            end -= timedelta(days=1)
            start = end - timedelta(days=lookback_days)

        return self.get_history(
            symbol=symbol,
            start=start,
            end=end,
            interval=interval,
    )