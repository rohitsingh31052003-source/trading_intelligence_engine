"""
Market scan-cycle report formatter (Checkpoint 19.3).

Deterministic, stateless text renderer for a Checkpoint 19.3
:class:`engine.models.continuous_scan.MarketScanCycleResult`. Returns
``str`` (no ``print()`` inside). DESCRIPTIVE ONLY — it reports data
availability / market state; it is NOT a trading signal, NOT a
prediction and NOT a recommendation.

Following the 11O-19.2 reporting convention, this formatter is imported
via its full path (NOT re-exported from ``reporting/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from engine.models.continuous_scan import (
    MarketScanCycleResult,
    MarketScanStatus,
    ScannerState,
)


class MarketScanCycleFormatter:
    """Stateless, deterministic renderer for scan-cycle results."""

    def __init__(self, width: int = 80) -> None:
        if width < 1:
            raise ValueError("width must be >= 1.")
        self.width = width

    def _wrap(self, text: str, indent: str = "") -> str:
        words = text.split()
        if not words:
            return ""
        lines: list[str] = []
        current = indent
        for word in words:
            if current == indent:
                current = indent + word
            elif len(current) + 1 + len(word) <= self.width:
                current += " " + word
            else:
                lines.append(current)
                current = indent + word
        if current:
            lines.append(current)
        return "\n".join(lines)

    @staticmethod
    def _session_label(result: MarketScanCycleResult) -> str:
        session = result.market_session
        if session is None:
            return "unknown"
        label = str(getattr(session, "value", session))
        if result.seconds_until_next_open is not None:
            label += f" (next NSE open in {int(result.seconds_until_next_open)}s)"
        else:
            label += " (next-open estimate unavailable)"
        return label

    def format_status(self, status: MarketScanStatus | ScannerState) -> str:
        """Human-readable status label (used by CLIs / summaries)."""

        if isinstance(status, ScannerState):
            return (
                "SCANNING"
                if status is ScannerState.SCANNING
                else str(status.value)
            )
        return str(status.value)

    def format(self, result: MarketScanCycleResult, *, per_symbol: bool = True) -> str:
        """A complete scan-cycle report for one cycle (deterministic)."""

        meta = result.metadata
        lines = [
            "CONTINUOUS MARKET SCAN — CYCLE REPORT",
            "",
            f"Cycle id            : {result.cycle_id}",
            f"Status              : {result.status.value}",
            f"Reference now       : {result.reference_now.isoformat()}",
            f"Timeframe           : {result.timeframe}",
            f"Market session      : {self._session_label(result)}",
            f"Source              : {result.source.value}"
            + (f" ({result.manual_run_id})" if result.manual_run_id else ""),
            "",
        ]
        if meta is not None:
            lines += [
                "CYCLE METADATA",
                (
                    f"  requested universe : {meta.requested_universe_size} "
                    f"(attempted {meta.attempted_instrument_count})"
                ),
                f"  successful         : {meta.successful_instrument_count}",
                f"  unavailable        : {meta.unavailable_instrument_count}",
                f"  failed             : {meta.failed_instrument_count}",
                (
                    f"  duration           : "
                    f"{meta.duration_seconds if meta.duration_seconds is not None else 'n/a'}"
                    f" s"
                ),
                "",
            ]
        if result.error:
            lines.append("CYCLE ERROR")
            lines.append(self._wrap(f"  {result.error}", indent="  "))
            lines.append("")

        if per_symbol and result.results:
            lines.append("PER-SYMBOL MARKET STATE")
            header = (
                f"  {'INSTRUMENT':<22} {'STATUS':<26} {'FRESH':<6} "
                f"{'DATA':<6} {'BARS':<6} LAST BAR"
            )
            lines.append(header)
            for symbol in result.results:
                lines.append(
                    f"  {symbol.instrument:<22} {symbol.status.value:<26} "
                    f"{'yes' if symbol.fresh else 'no':<6} "
                    f"{'yes' if symbol.available else 'no':<6} "
                    f"{symbol.coverage.candle_count if symbol.coverage else 0:<6} "
                    f"{symbol.latest_bar_timestamp.isoformat() if symbol.latest_bar_timestamp else 'none'}"
                )
            lines.append("")
        lines.append(
            "DISCLAIMER: this scan reports market-data availability and "
            "market state only. It does not predict market behavior, does "
            "not identify trade setups, does not constitute a trading "
            "recommendation, and does not authorize any broker execution.",
        )
        return "\n".join(lines)

    def format_summary(self, result: MarketScanCycleResult) -> str:
        """Compact one-line-per-cycle summary (for CLIs / logs)."""

        meta = result.metadata
        parts = [
            f"cycle={result.cycle_id}",
            f"status={result.status.value}",
            f"requested={meta.requested_universe_size if meta else 'n/a'}",
            f"successful={result.successful_count}",
            f"unavailable={result.unavailable_count}",
            f"failed={result.failed_count}",
        ]
        return "MARKET SCAN SUMMARY " + " ".join(parts)


class ContinuousScannerStatusFormatter:
    """Deterministic scanner lifecycle-state renderer."""

    def __init__(self) -> None:
        self.cycle_formatter = MarketScanCycleFormatter()

    def format_state(self, state: ScannerState) -> str:
        return f"scanner_state={state.value}"


__all__ = [
    "ContinuousScannerStatusFormatter",
    "MarketScanCycleFormatter",
]