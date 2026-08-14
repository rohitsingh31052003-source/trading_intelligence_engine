"""
Deterministic serialization for market scan results (Sprint 11U).

The scan result is persisted as a self-describing, canonical JSON
document so it can be reloaded later WITHOUT rerunning the underlying
trading / candidate / decision / opportunity engines.

Serialization scope (intentional, documented, matching the Sprint 11K
registry discipline):

The lightweight, descriptive scan projections are persisted IN FULL:
the scan id, evaluation timestamp, instruments, timeframes, scan
status, and the per-instrument ``InstrumentScanResult`` projection
(instrument, timeframes, timestamp, alignment, complete, direction,
decision_classification, decision_score, risk_reward_ratio, reason,
eligible). The heavy per-engine outputs retained BY REFERENCE for
future layers are NOT persisted and reconstruct as ``None``:
``InstrumentScanResult.higher_context``,
``InstrumentScanResult.lower_context``,
``InstrumentScanResult.decision``,
``InstrumentScanResult.opportunity`` (these are regenerable by
rerunning the scan, never part of the persisted scan record).

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__tuple__``) so the deserializer
reconstructs the EXACT model type. Enums are stored by their stable
member name. The persisted text uses sorted keys so identical scans
always produce identical bytes (suitable for byte-level audit /
identity comparison).

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No nondeterministic values in the scan id.

* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.

* No global mutable state. Pure functions.
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from engine.models.market_scan import (
    InstrumentScanResult,
    MarketScanResult,
    MTFAlignment,
    RankedScanOpportunity,
    ScanStatus,
    TimeframeRole,
)


#: Schema version for persisted market scan documents.
SCANNER_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_scan(result: MarketScanResult) -> str:
    """
    Serialize a :class:`MarketScanResult` to canonical JSON text.

    The schema version is written at the top of the document. Keys are
    sorted for determinism. Heavy per-engine outputs are dropped
    (reconstruct as ``None``) — only the lightweight projections are
    persisted.
    """

    payload = {
        "schema_version": SCANNER_SCHEMA_VERSION,
        "scan_id": result.scan_id,
        "timestamp": _to_json(result.timestamp),
        "instruments": _to_json(tuple(result.instruments)),
        "timeframes": _to_json(tuple(result.timeframes)),
        "status": _to_json(result.status),
        "results": _to_json(tuple(result.results)),
        "ranked": _to_json(tuple(result.ranked)),
        "best": _to_json(result.best),
        "alternatives": _to_json(tuple(result.alternatives)),
        "rejected": _to_json(tuple(result.rejected)),
        "rationale": result.rationale,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_scan_bytes(result: MarketScanResult) -> bytes:
    """Serialize a market scan result to canonical JSON bytes."""

    return serialize_scan(result).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_scan(payload: str) -> MarketScanResult:
    """Reconstruct a :class:`MarketScanResult` from JSON text."""

    parsed = json.loads(payload)
    version = parsed.get("schema_version")
    if version != SCANNER_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported market scan schema version {version!r}; "
            f"supported is {SCANNER_SCHEMA_VERSION}.",
        )
    return _from_parsed(parsed)


def parse_scan_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted market scan document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _from_parsed(parsed: dict[str, Any]) -> MarketScanResult:
    """Reconstruct a MarketScanResult from a parsed mapping."""

    ranked = tuple(
        _from_json(r, RankedScanOpportunity)
        for r in _as_seq(parsed.get("ranked"))
    )
    return MarketScanResult(
        scan_id=parsed.get("scan_id", ""),
        timestamp=_from_json(parsed.get("timestamp"), datetime),
        instruments=tuple(
            _from_json(i, str) for i in _as_seq(parsed.get("instruments"))
        ),
        timeframes=tuple(
            _from_json(t, str) for t in _as_seq(parsed.get("timeframes"))
        ),
        status=_from_json(parsed.get("status"), ScanStatus),
        results=tuple(
            _from_json(r, InstrumentScanResult)
            for r in _as_seq(parsed.get("results"))
        ),
        ranked=ranked,
        best=(
            _from_json(parsed["best"], RankedScanOpportunity)
            if parsed.get("best") is not None
            else None
        ),
        alternatives=tuple(
            _from_json(a, RankedScanOpportunity)
            for a in _as_seq(parsed.get("alternatives"))
        ),
        rejected=tuple(
            _from_json(r, InstrumentScanResult)
            for r in _as_seq(parsed.get("rejected"))
        ),
        rationale=parsed.get("rationale", ""),
    )


def _as_seq(value: Any) -> list:
    """Unwrap a tagged tuple / plain list into a list for iteration."""

    if value is None:
        return []
    if isinstance(value, dict) and "__tuple__" in value:
        return list(value["__tuple__"])
    if isinstance(value, list):
        return value
    return []


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_scan_json(result: MarketScanResult) -> str:
    """
    Canonical (sorted-key) JSON text for a market scan result.

    Identical scans always produce identical text. Suitable for
    byte-level identity / audit comparisons.
    """

    return serialize_scan(result)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a scan model value to JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, Enum):
        return {"__enum__": value.name}

    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}

    if isinstance(value, InstrumentScanResult):
        # Heavy per-engine reference outputs (higher_context,
        # lower_context, decision, opportunity) are intentionally NOT
        # persisted: they are regenerable by rerunning the scan and
        # never part of the persisted scan record. They reconstruct as
        # None (scope discipline, matching the Sprint 11K registry).
        fields = {
            f.name: _to_json(getattr(value, f.name))
            for f in value.__dataclass_fields__.values()  # type: ignore[attr-defined]
            if f.name not in (
                "higher_context", "lower_context", "decision", "opportunity",
            )
        }
        return {
            "__dataclass__": "InstrumentScanResult",
            "fields": fields,
        }

    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {
                f.name: _to_json(getattr(value, f.name))
                for f in value.__dataclass_fields__.values()  # type: ignore[attr-defined]
            },
        }

    if isinstance(value, (list, tuple)):
        return {"__tuple__": [_to_json(item) for item in value]}

    if isinstance(value, dict):
        return {str(k): _to_json(v) for k, v in value.items()}

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


# Mapping of tagged dataclass name -> model class.
_DATACLASSES = {
    "InstrumentScanResult": InstrumentScanResult,
    "RankedScanOpportunity": RankedScanOpportunity,
    "MarketScanResult": MarketScanResult,
}

# Mapping of enum name -> enum class.
_ENUMS = {
    "MTFAlignment": MTFAlignment,
    "ScanStatus": ScanStatus,
    "TimeframeRole": TimeframeRole,
}


def _from_json(value: Any, expected_type: type) -> Any:
    """Reconstruct a value of ``expected_type`` from its JSON-safe form."""

    if value is None:
        return None

    if expected_type is datetime:
        if isinstance(value, dict) and "__datetime__" in value:
            return datetime.fromisoformat(value["__datetime__"])
        return value

    if expected_type in _ENUMS.values():
        if isinstance(value, dict) and "__enum__" in value:
            return expected_type[value["__enum__"]]
        if isinstance(value, str):
            return expected_type[value]
        return value

    if expected_type in _DATACLASSES.values():
        cls = expected_type
        fields_payload = value.get("fields", {}) if isinstance(value, dict) else {}
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name))
        return cls(**kwargs)

    if expected_type in (tuple, list, str, int, float, bool):
        if expected_type in (tuple, list):
            if isinstance(value, dict) and "__tuple__" in value:
                return tuple(value["__tuple__"])
            return tuple(value) if isinstance(value, list) else value
        return value

    return value


def _decode_field(raw: Any) -> Any:
    """
    Decode a single dataclass field from its JSON-safe form.

    The field type annotation is a string (from
    ``from __future__ import annotations``); we decode heuristically
    based on the raw value's shape so we do not need to resolve
    forward-reference strings.
    """

    if raw is None:
        return None

    if isinstance(raw, dict) and "__datetime__" in raw:
        return datetime.fromisoformat(raw["__datetime__"])

    if isinstance(raw, dict) and "__enum__" in raw:
        name = raw["__enum__"]
        for enum_cls in _ENUMS.values():
            try:
                return enum_cls[name]
            except KeyError:
                continue
        return name

    if isinstance(raw, dict) and "__dataclass__" in raw:
        cls_name = raw["__dataclass__"]
        cls = _DATACLASSES.get(cls_name)
        if cls is None:
            return None
        fields_payload = raw.get("fields", {})
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name))
        return cls(**kwargs)

    if isinstance(raw, dict) and "__tuple__" in raw:
        return tuple(
            _decode_field(item) for item in raw["__tuple__"]
        )

    if isinstance(raw, list):
        return tuple(_decode_field(item) for item in raw)

    if isinstance(raw, dict):
        return {k: _decode_field(v) for k, v in raw.items()}

    return raw


__all__ = [
    "SCANNER_SCHEMA_VERSION",
    "canonical_scan_json",
    "deserialize_scan",
    "parse_scan_header",
    "serialize_scan",
    "serialize_scan_bytes",
]
