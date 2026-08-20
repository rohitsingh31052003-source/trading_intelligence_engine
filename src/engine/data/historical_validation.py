"""
Historical DATA VALIDATION layer (Product Phase 6A).

Deliberate, explicit validation for historical candles. It REUSES the
existing ``DataValidator`` per-candle semantics and the ``OHLCVCandle``
structural guarantees (high >= low, open/close within range, non-negative
volume) — it never duplicates that logic.

Validation rules (deterministic):

1. Naive timestamps are REJECTED (``NAIVE_TIMESTAMP``) — never silently
   accepted.
2. Aware timestamps are normalized to UTC
   (:func:`engine.data.historical_times.normalize_to_utc`).
3. Future-dated candles (``timestamp > reference_now``) are REJECTED
   (``FUTURE_DATED``) — the production ingestion path never ingests
   future data. A request with a future ``end`` is likewise rejected at
   the service boundary unless the caller deliberately set
   ``allow_future_end=True``.
4. Missing required OHLC values / missing timestamps / malformed records
   are REJECTED (defensive; providers may return arbitrary objects).
5. Invalid OHLC relationships and negative volume are REJECTED (the
   ``OHLCVCandle`` construction contract).
6. Duplicate timestamps: the FIRST occurrence is kept; subsequent ones
   are REJECTED as ``DUPLICATE_TIMESTAMP``.
7. Out-of-order sequences are sorted chronologically; the issue is
   reported as ``UNORDERED`` (honest, never silent).
8. An empty provider response is reported as ``EMPTY_RESPONSE``.

The result carries accepted candles (UTC, sorted, de-duplicated) plus
every rejected-record issue. No invalid record is silently repaired.
No candle is fabricated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from engine.data.historical_times import normalize_to_utc
from engine.data.validator import DataValidator
from engine.models.historical_data import (
    HistoricalDataError,
    HistoricalDataIssue,
)
from engine.models.ohlcv import OHLCVCandle


#: Defensive tuple of the attributes every well-formed candle must carry.
_REQUIRED_ATTRS = ("timestamp", "open", "high", "low", "close", "volume")


class HistoricalDataValidator:
    """
    Explicit validation for historical candles (stateless, deterministic).
    """

    @staticmethod
    def validate(
        records: Iterable[object],
        *,
        instrument: str,
        timeframe: str,
        reference_now: datetime,
        allow_future: bool = False,
    ) -> tuple[tuple[OHLCVCandle, ...], tuple[HistoricalDataIssue, ...]]:
        """
        Validate raw provider records.

        Returns ``(accepted, issues)``. Accepted candles are normalized
        to UTC, chronologically sorted and de-duplicated. Issues are in
        the order detected (deterministic).
        """

        accepted: list[OHLCVCandle] = []
        issues: list[HistoricalDataIssue] = []
        seen: set[datetime] = set()

        for record in records:
            candle, issue = HistoricalDataValidator._validate_one(
                record,
                instrument=instrument,
                timeframe=timeframe,
                reference_now=reference_now,
                allow_future=allow_future,
            )
            if issue is not None:
                issues.append(issue)
                continue
            assert candle is not None
            if candle.timestamp in seen:
                issues.append(
                    HistoricalDataIssue(
                        error=HistoricalDataError.DUPLICATE_TIMESTAMP,
                        reason=(
                            f"{instrument}/{timeframe}: duplicate timestamp "
                            f"{candle.timestamp.isoformat()} rejected "
                            "(first occurrence kept)."
                        ),
                        instrument=instrument,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                    ),
                )
                continue
            seen.add(candle.timestamp)
            accepted.append(candle)

        # Chronological ordering (provider-order invariance). An input
        # that was not already sorted is reported honestly as UNORDERED.
        timestamps = [c.timestamp for c in accepted]
        if timestamps != sorted(timestamps):
            issues.append(
                HistoricalDataIssue(
                    error=HistoricalDataError.UNORDERED,
                    reason=(
                        f"{instrument}/{timeframe}: out-of-order timestamps "
                        "normalized to chronological order."
                    ),
                    instrument=instrument,
                    timeframe=timeframe,
                ),
            )
            accepted.sort(key=lambda c: c.timestamp)

        return tuple(accepted), tuple(issues)

    @staticmethod
    def empty_issue(instrument: str, timeframe: str) -> HistoricalDataIssue:
        """Issue for an empty provider response."""

        return HistoricalDataIssue(
            error=HistoricalDataError.EMPTY_RESPONSE,
            reason=(
                f"{instrument}/{timeframe}: provider returned an empty "
                "response; nothing ingested."
            ),
            instrument=instrument,
            timeframe=timeframe,
        )

    @staticmethod
    def _validate_one(
        record: object,
        *,
        instrument: str,
        timeframe: str,
        reference_now: datetime,
        allow_future: bool,
    ) -> tuple[OHLCVCandle | None, HistoricalDataIssue | None]:
        """Validate one record defensively; return candle or issue."""

        if not all(
            hasattr(record, attr) for attr in _REQUIRED_ATTRS
        ):
            return None, HistoricalDataIssue(
                error=HistoricalDataError.MALFORMED_RESPONSE,
                reason=(
                    f"{instrument}/{timeframe}: provider record is malformed "
                    "(missing required OHLCV attributes); rejected."
                ),
                instrument=instrument,
                timeframe=timeframe,
            )
        if not isinstance(record, OHLCVCandle):
            return None, HistoricalDataIssue(
                error=HistoricalDataError.MALFORMED_RESPONSE,
                reason=(
                    f"{instrument}/{timeframe}: provider record is not an "
                    "OHLCVCandle; rejected."
                ),
                instrument=instrument,
                timeframe=timeframe,
            )

        ts = getattr(record, "timestamp")
        if ts is None:
            return None, HistoricalDataIssue(
                error=HistoricalDataError.MISSING_TIMESTAMP,
                reason=(
                    f"{instrument}/{timeframe}: record is missing a "
                    "timestamp; rejected."
                ),
                instrument=instrument,
                timeframe=timeframe,
            )
        normalized_ts = normalize_to_utc(ts)
        if normalized_ts is None:
            return None, HistoricalDataIssue(
                error=HistoricalDataError.NAIVE_TIMESTAMP,
                reason=(
                    f"{instrument}/{timeframe}: naive timestamp "
                    f"{ts.isoformat() if isinstance(ts, datetime) else ts!r} "
                    "rejected (timezone awareness is required)."
                ),
                instrument=instrument,
                timeframe=timeframe,
                timestamp=ts if isinstance(ts, datetime) else None,
            )

        if any(getattr(record, attr) is None for attr in _REQUIRED_ATTRS[1:]):
            return None, HistoricalDataIssue(
                error=HistoricalDataError.MISSING_OHLC_VALUE,
                reason=(
                    f"{instrument}/{timeframe}: record is missing a required "
                    "OHLC value; rejected."
                ),
                instrument=instrument,
                timeframe=timeframe,
                timestamp=normalized_ts,
            )

        if not allow_future and normalized_ts > reference_now:
            return None, HistoricalDataIssue(
                error=HistoricalDataError.FUTURE_DATED,
                reason=(
                    f"{instrument}/{timeframe}: future-dated candle "
                    f"{normalized_ts.isoformat()} rejected (look-ahead "
                    "protection)."
                ),
                instrument=instrument,
                timeframe=timeframe,
                timestamp=normalized_ts,
            )

        # The OHLCVCandle constructor already enforces the OHLC contract;
        # a second defensive pass mirrors the existing DataValidator
        # semantics so providers returning raw OHLCVCandle objects that
        # were constructed before their values were tampered (or exotic
        # bool/nan floats) are still caught here.
        try:
            DataValidator.validate_candle(record)
        except (ValueError, TypeError) as exc:
            category = HistoricalDataError.INVALID_OHLC
            if "Volume" in str(exc):
                category = HistoricalDataError.NEGATIVE_VOLUME
            elif "cannot be lower than low" in str(exc):
                category = HistoricalDataError.HIGH_BELOW_LOW
            return None, HistoricalDataIssue(
                error=category,
                reason=f"{instrument}/{timeframe}: {exc}; rejected.",
                instrument=instrument,
                timeframe=timeframe,
                timestamp=normalized_ts,
            )

        candle = OHLCVCandle(
            timestamp=normalized_ts,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            volume=record.volume,
        )
        return candle, None


__all__ = ["HistoricalDataValidator"]
