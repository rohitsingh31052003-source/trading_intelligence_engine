"""
Canonical timeframe + timestamp utilities for the historical market-data
foundation (Product Phase 6A).

This module is the SINGLE deterministic definition of the historical
timeframe vocabulary and the UTC normalization rule. It implements NO
trading / scoring / prediction / decision logic.

TIMEFRAME CANONICALIZATION:

The repository has historically used mixed timeframe labels
(``"15M"``/``"15m"``, ``"1D"``/``"1d"``, ``"60m"``/``"1h"``). The
historical-data foundation canonicalizes to a stable lowercase-minute /
uppercase-day vocabulary (``"15m"``, ``"1h"``, ``"1D"``) via
:func:`canonical_timeframe` so storage layout, provider contracts and
gap detection share one spelling. Unknown labels resolve to ``None``
and are reported as ``UNSUPPORTED_TIMEFRAME`` (never guessed).

TIMESTAMP NORMALIZATION (Product Phase 6A §5):

* Preserve timezone awareness.
* All timestamps are normalized to UTC internally
  (:func:`normalize_to_utc`).
* Naive timestamps are NEVER silently accepted: :func:`normalize_to_utc`
  returns ``None`` for a naive value and the validation layer records a
  ``NAIVE_TIMESTAMP`` issue (the record is rejected, not repaired).
"""

from __future__ import annotations

from datetime import UTC, datetime


#: Canonical timeframe -> duration of one candle in seconds. This is the
#: engine-level historical vocabulary (independent of the dashboard's
#: presentation mapping, which it intentionally overlaps).
HISTORICAL_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "90m": 5400,
    "4h": 14400,
    "1D": 86400,
}

#: Accepted aliases -> canonical label. Case differences and common
#: legacy spellings are normalized here so callers never need to guess.
_TIMEFRAME_ALIASES: dict[str, str] = {
    "1m": "1m", "1M": "1m",
    "2m": "2m", "2M": "2m",
    "3m": "3m", "3M": "3m",
    "5m": "5m", "5M": "5m",
    "15m": "15m", "15M": "15m",
    "30m": "30m", "30M": "30m",
    "60m": "1h", "60M": "1h",
    "1h": "1h", "1H": "1h",
    "90m": "90m", "90M": "90m",
    "4h": "4h", "4H": "4h",
    "1d": "1D", "1D": "1D", "1day": "1D", "1DAY": "1D",
}


def canonical_timeframe(timeframe: str) -> str | None:
    """
    Return the canonical timeframe label, or ``None`` when unknown.

    Unknown labels are NEVER guessed or synthesized — the caller reports
    ``UNSUPPORTED_TIMEFRAME`` honestly.
    """

    if not isinstance(timeframe, str):
        return None
    return _TIMEFRAME_ALIASES.get(timeframe.strip())


def timeframe_seconds(timeframe: str) -> int | None:
    """Canonical candle duration in seconds, or ``None`` when unknown."""

    canonical = canonical_timeframe(timeframe)
    if canonical is None:
        return None
    return HISTORICAL_TIMEFRAME_SECONDS[canonical]


def supported_timeframes() -> tuple[str, ...]:
    """Canonical supported timeframe labels (deterministic order)."""

    return tuple(HISTORICAL_TIMEFRAME_SECONDS)


def normalize_to_utc(timestamp: datetime) -> datetime | None:
    """
    Normalize an aware timestamp to UTC.

    Returns ``None`` for a naive timestamp — naive values are NEVER
    silently accepted or repaired; the caller records a
    ``NAIVE_TIMESTAMP`` validation issue instead.
    """

    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(UTC)


__all__ = [
    "HISTORICAL_TIMEFRAME_SECONDS",
    "canonical_timeframe",
    "normalize_to_utc",
    "supported_timeframes",
    "timeframe_seconds",
]
