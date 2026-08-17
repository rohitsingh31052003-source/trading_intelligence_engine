"""
Configuration for the paper trading & real-world validation layer
(Product Phase 5).

All thresholds live here; no magic numbers are embedded in the engine.
The defaults are deliberately conservative and deterministic. They are
NOT calibrated to any market; they express interpretable, rule-based
paper-trading constraints.

This config governs ONLY the paper-trade lifecycle / accounting. It
MUST NOT make the following configurable (those are AUTHORITATIVE
existing semantics and are never re-configurable by Phase 5):

* the existing decision semantics (REJECTED / WATCH / QUALIFIED /
  PREFERRED)
* the existing actionability semantics
* the existing trade geometry (entry / stop / target / R:R)
* Target 2 support (always unsupported)
* the existing Product Phase 4 trade plan (account capital / risk % /
  quantity / planned risk / planned reward — reused verbatim)
* evidence semantics

The core lifecycle / accounting remains deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class PaperTradeConfig:
    """
    Configuration for :class:`~engine.intelligence.paper_trading.PaperTradingEngine`.

    Threshold semantics (documented in the engine):

    max_entry_bars
        Maximum number of completed candles to wait for the entry
        condition after the paper trade is created. If the entry
        reference is not touched within this window the paper trade
        remains ``WAITING_FOR_ENTRY`` (it is NOT force-entered). Positive
        int. Default ``20``.

    max_holding_bars
        Maximum number of completed candles to monitor for a stop /
        target exit AFTER entry. If neither is reached within this
        horizon the paper trade is resolved as ``EXPIRED``
        (mark-to-close at the last in-window candle). Positive int.
        Default ``50``.

    monetary_precision
        Number of decimal places retained for monetary ``Decimal`` values
        (realized P&L). Applied only at the presentation / rounding
        boundary; the underlying calculations use full ``Decimal``
        precision. Non-negative. Default ``2``.

    label / metadata
        Optional identity / metadata carried onto paper trades for audit.
    """

    max_entry_bars: int = 20
    max_holding_bars: int = 50
    monetary_precision: int = 2
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.max_entry_bars, int) or isinstance(
            self.max_entry_bars, bool,
        ):
            raise ValueError("max_entry_bars must be an int.")
        if self.max_entry_bars <= 0:
            raise ValueError("max_entry_bars must be positive.")
        if not isinstance(self.max_holding_bars, int) or isinstance(
            self.max_holding_bars, bool,
        ):
            raise ValueError("max_holding_bars must be an int.")
        if self.max_holding_bars <= 0:
            raise ValueError("max_holding_bars must be positive.")
        if not isinstance(self.monetary_precision, int) or isinstance(
            self.monetary_precision, bool,
        ):
            raise ValueError("monetary_precision must be an int.")
        if self.monetary_precision < 0:
            raise ValueError("monetary_precision must be non-negative.")
        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be a tuple of (str, str) pairs.")
        for pair in self.metadata:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("metadata entries must be (str, str) pairs.")
            if not isinstance(pair[0], str) or not isinstance(pair[1], str):
                raise ValueError("metadata keys and values must be strings.")

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Sorted (name, value) pairs for auditability."""

        return (
            ("label", self.label),
            ("max_entry_bars", str(self.max_entry_bars)),
            ("max_holding_bars", str(self.max_holding_bars)),
            ("monetary_precision", str(self.monetary_precision)),
        )


__all__ = ["PaperTradeConfig"]
