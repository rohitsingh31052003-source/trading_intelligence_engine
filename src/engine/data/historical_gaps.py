"""
Deterministic GAP DETECTION for historical series (Product Phase 6A).

The detector reports missing expected candles WITHOUT treating every
market closure as an error and WITHOUT fabricating missing candles. It
distinguishes three outcomes:

* VALID TRADING SEQUENCE — no gaps detected (no ``HistoricalGap``
  records).
* POSSIBLE MARKET CLOSURE — the gap is plausibly a weekend / short
  non-trading window / plausible exchange-holiday span. Reported for
  transparency; NOT a data-quality failure.
* UNEXPECTED DATA GAP — the gap exceeds the plausible closure window.

CLASSIFICATION RULE (deterministic, no exchange-calendar subsystem):

1. For each consecutive pair of candles, compute the expected step (the
   timeframe duration). A pair exactly one step apart contributes no
   gap.
2. For a wider pair, the number of expected-but-missing candles is
   ``round(span / step) - 1``. A pair contributes a gap only when at
   least one expected candle is missing.
3. A gap is ``POSSIBLE_MARKET_CLOSURE`` when its span is <= the
   configured closure window (default 2 days) OR when every missing
   expected timestamp falls on a weekend (Saturday / Sunday). Otherwise
   it is an ``UNEXPECTED_GAP``.

The default closure window is deliberately small and documented: a few
days of weekend + plausible holiday coverage, never a claim of full
market-calendar knowledge. Missing candles are NEVER synthesized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from engine.data.historical_times import timeframe_seconds
from engine.models.historical_data import GapKind, HistoricalGap
from engine.models.ohlcv import OHLCVCandle


@dataclass(frozen=True, slots=True)
class GapDetectionConfig:
    """
    Gap-detection thresholds.

    closure_seconds
        Maximum span (in seconds) classified as a plausible market
        closure when the weekend rule does not already apply. Default 2
        days (weekend window for daily candles).
    """

    closure_seconds: int = 2 * 86400

    def __post_init__(self) -> None:
        if self.closure_seconds < 0:
            raise ValueError("closure_seconds must be non-negative.")


DEFAULT_GAP_CONFIG = GapDetectionConfig()


def detect_gaps(
    candles: Sequence[OHLCVCandle],
    timeframe: str,
    config: GapDetectionConfig | None = None,
) -> tuple[HistoricalGap, ...]:
    """
    Detect gaps in a chronologically ordered candle series.

    Returns a deterministically ordered tuple of :class:`HistoricalGap`
    (empty for a valid trading sequence). The input is never mutated.
    """

    if len(candles) < 2:
        return ()
    cfg = config or DEFAULT_GAP_CONFIG
    step = timeframe_seconds(timeframe)
    if step is None or step <= 0:
        # Unknown step: every wider-than-one pair is unexpected (we
        # cannot estimate the expected cadence). Reported honestly.
        step = 0

    gaps: list[HistoricalGap] = []
    for prev, nxt in zip(candles, candles[1:]):
        span = (nxt.timestamp - prev.timestamp).total_seconds()
        if span <= 0:
            continue
        if step <= 0:
            expected_step = span
        else:
            expected_step = step
        missing = int(round(span / expected_step)) - 1
        if missing <= 0:
            continue
        missing_ts = [
            prev.timestamp + timedelta(seconds=expected_step * i)
            for i in range(1, missing + 1)
        ]
        weekend_only = all(ts.weekday() >= 5 for ts in missing_ts)
        if weekend_only or span <= cfg.closure_seconds:
            kind = GapKind.POSSIBLE_MARKET_CLOSURE
            reason = (
                "plausible market closure (weekend / short non-trading "
                "window); not a data-quality failure."
            )
        else:
            kind = GapKind.UNEXPECTED_GAP
            reason = (
                "unexpected data gap; missing candles are reported, never "
                "fabricated."
            )
        gaps.append(
            HistoricalGap(
                kind=kind,
                previous_timestamp=prev.timestamp,
                next_timestamp=nxt.timestamp,
                missing_count=missing,
                span_seconds=float(span),
                reason=reason,
            ),
        )
    return tuple(gaps)


__all__ = [
    "DEFAULT_GAP_CONFIG",
    "GapDetectionConfig",
    "detect_gaps",
]
