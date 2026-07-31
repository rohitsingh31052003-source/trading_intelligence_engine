"""
Utilities for normalizing market data from different providers.
"""

from __future__ import annotations

import pandas as pd


class DataNormalizer:
    """
    Normalize market data into a consistent structure.
    """

    REQUIRED_COLUMNS = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    @staticmethod
    def normalize(df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize a market DataFrame.
        """

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if "Adj Close" in df.columns:
            df = df.drop(columns=["Adj Close"])

        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }

        df = df.rename(columns=rename_map)

        missing = [
            column
            for column in DataNormalizer.REQUIRED_COLUMNS
            if column not in df.columns
        ]

        if missing:
            raise ValueError(
                f"Missing required columns: {', '.join(missing)}"
            )

        return df