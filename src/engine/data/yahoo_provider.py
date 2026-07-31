"""
Yahoo Finance market data provider.
"""

from __future__ import annotations

from datetime import datetime

import yfinance as yf

from engine.data.base_provider import BaseDataProvider
from engine.models.ohlcv import OHLCVCandle


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

        # Flatten MultiIndex columns.
        if data.columns.nlevels > 1:
            data.columns = data.columns.get_level_values(0)

        candles: list[OHLCVCandle] = []

        for timestamp, row in data.iterrows():
            candles.append(
                OHLCVCandle(
                    timestamp=timestamp.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )

        return candles

    def get_latest(
        self,
        symbol: str,
        interval: str,
    ) -> OHLCVCandle:
        candles = self.get_history(
            symbol=symbol,
            start=datetime.now(),
            end=datetime.now(),
            interval=interval,
        )

        if not candles:
            raise ValueError("No latest candle available.")

        return candles[-1]