"""
Deterministic serialization for the historical market-data foundation
(Product Phase 6A).

Canonical JSON encoding for candles / provenance / dataset slices used
by the persistence layer and the CLI. Sorted keys, stable value
encoding, ISO timestamps — identical data always produces identical
bytes (tested). No ``pickle`` / ``eval`` / ``exec``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Iterable

from engine.models.historical_data import (
    GapKind,
    HistoricalDataIssue,
    HistoricalGap,
    HistoricalIngestionStatus,
    HistoricalProvenance,
)
from engine.models.ohlcv import OHLCVCandle

HISTORICAL_SCHEMA_VERSION = 1


def candle_to_dict(candle: OHLCVCandle) -> dict[str, Any]:
    """Canonical candle mapping (deterministic)."""

    return {
        "timestamp": candle.timestamp.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def candle_from_dict(payload: dict[str, Any]) -> OHLCVCandle:
    """Rebuild a candle from its canonical mapping (ISO timestamp, UTC)."""

    ts = payload.get("timestamp")
    timestamp = (
        datetime.fromisoformat(ts) if isinstance(ts, str) else None
    )
    return OHLCVCandle(
        timestamp=timestamp,  # type: ignore[arg-type]
        open=payload.get("open"),
        high=payload.get("high"),
        low=payload.get("low"),
        close=payload.get("close"),
        volume=payload.get("volume"),
    )


def serialize_candles(candles: Iterable[OHLCVCandle]) -> str:
    """Serialize a candle collection to canonical sorted-key JSON."""

    return json.dumps(
        [candle_to_dict(c) for c in candles],
        sort_keys=True,
        separators=(",", ":"),
    )


def serialize_provenance(provenance: HistoricalProvenance) -> str:
    """Serialize provenance to canonical sorted-key JSON (one line)."""

    payload = {
        "schema_version": HISTORICAL_SCHEMA_VERSION,
        "provider": provenance.provider,
        "instrument": provenance.instrument,
        "timeframe": provenance.timeframe,
        "requested_start": provenance.requested_start.isoformat(),
        "requested_end": provenance.requested_end.isoformat(),
        "actual_first_candle": (
            provenance.actual_first_candle.isoformat()
            if provenance.actual_first_candle
            else None
        ),
        "actual_last_candle": (
            provenance.actual_last_candle.isoformat()
            if provenance.actual_last_candle
            else None
        ),
        "ingestion_timestamp": provenance.ingestion_timestamp.isoformat(),
        "records_received": provenance.records_received,
        "records_accepted": provenance.records_accepted,
        "records_rejected": provenance.records_rejected,
        "status": provenance.status.name,
        "reason": provenance.reason,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def serialize_issue(issue: HistoricalDataIssue) -> dict[str, Any]:
    """Serialize one validation issue (dict form for JSON payloads)."""

    return {
        "error": issue.error.name,
        "reason": issue.reason,
        "instrument": issue.instrument,
        "timeframe": issue.timeframe,
        "timestamp": (
            issue.timestamp.isoformat() if issue.timestamp else None
        ),
    }


def serialize_gap(gap: HistoricalGap) -> dict[str, Any]:
    """Serialize one gap record (dict form for JSON payloads)."""

    return {
        "kind": gap.kind.name,
        "previous_timestamp": gap.previous_timestamp.isoformat(),
        "next_timestamp": gap.next_timestamp.isoformat(),
        "missing_count": gap.missing_count,
        "span_seconds": gap.span_seconds,
        "reason": gap.reason,
    }


__all__ = [
    "HISTORICAL_SCHEMA_VERSION",
    "candle_from_dict",
    "candle_to_dict",
    "serialize_candles",
    "serialize_gap",
    "serialize_issue",
    "serialize_provenance",
]
