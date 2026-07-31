from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SwingConfig:
    """
    Configuration for SwingEngine.
    """

    lookback: int = 2

    confirmation_candles: int = 2

    minimum_move_percent: float = 1.0