"""
Deterministic serialization for historical performance analytics
(Sprint 11X).

The performance analytics result is persisted as a self-describing,
canonical JSON document so it can be reloaded later WITHOUT
re-aggregating the underlying historical outcomes.

Serialization scope (intentional, matching the Sprint 11U / 11W
serialization discipline):

The lightweight, descriptive analytics projections are persisted IN
FULL: the analytics identity, the overall
:class:`HistoricalPerformanceStatistics`, every
:class:`HistoricalPerformanceBreakdown` and its
:class:`HistoricalPerformanceGroup` rows (key + statistics), the
outcome count, label, metadata and rationale. The heavy per-outcome
reference objects are NOT involved (the analytics result is already a
pure aggregate), so the round trip is lossless for every field the
analytics model carries.

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__tuple__``) so the deserializer
reconstructs the EXACT model type. Enums are stored by their stable
member name. The persisted text uses sorted keys so identical analytics
always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No nondeterministic values in the analytics id.

* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.

* No global mutable state. Pure functions.
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from enum import Enum
from typing import Any

from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
    HistoricalPerformanceBreakdown,
    HistoricalPerformanceGroup,
    HistoricalPerformanceStatistics,
)


#: Schema version for persisted performance-analytics documents.
PERFORMANCE_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_performance(analytics: HistoricalPerformanceAnalytics) -> str:
    """Serialize a :class:`HistoricalPerformanceAnalytics` to canonical JSON."""

    payload = {
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "analytics": _to_json(analytics),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_performance_bytes(analytics: HistoricalPerformanceAnalytics) -> bytes:
    """Serialize performance analytics to canonical JSON bytes."""

    return serialize_performance(analytics).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_performance(payload: str) -> HistoricalPerformanceAnalytics:
    """Reconstruct a :class:`HistoricalPerformanceAnalytics` from JSON text."""

    parsed = json.loads(payload)
    version = parsed.get("schema_version")
    if version != PERFORMANCE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported performance schema version {version!r}; "
            f"supported is {PERFORMANCE_SCHEMA_VERSION}.",
        )
    return _from_json(parsed.get("analytics"), HistoricalPerformanceAnalytics)


def parse_performance_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted performance document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_performance_json(analytics: HistoricalPerformanceAnalytics) -> str:
    """Canonical (sorted-key) JSON text for performance analytics."""

    return serialize_performance(analytics)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode an analytics model value to JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, Enum):
        return {"__enum__": value.name}

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
    "HistoricalPerformanceAnalytics": HistoricalPerformanceAnalytics,
    "HistoricalPerformanceBreakdown": HistoricalPerformanceBreakdown,
    "HistoricalPerformanceGroup": HistoricalPerformanceGroup,
    "HistoricalPerformanceStatistics": HistoricalPerformanceStatistics,
}

# Mapping of enum name -> enum class.
_ENUMS = {
    "BreakdownDimension": BreakdownDimension,
}


def _from_json(value: Any, expected_type: type) -> Any:
    """Reconstruct a value of ``expected_type`` from its JSON-safe form."""

    if value is None:
        return None

    if expected_type in _ENUMS.values():
        if isinstance(value, dict) and "__enum__" in value:
            return expected_type[value["__enum__"]]
        if isinstance(value, str):
            return expected_type[value]
        return value

    if expected_type in _DATACLASSES.values():
        cls = expected_type
        fields_payload = (
            value.get("fields", {}) if isinstance(value, dict) else {}
        )
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name))
        return cls(**kwargs)

    return value


def _decode_field(raw: Any) -> Any:
    """
    Decode a single dataclass field from its JSON-safe form.

    Decodes heuristically based on the raw value's shape so forward-
    reference string annotations do not need to be resolved.
    """

    if raw is None:
        return None

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
        return tuple(_decode_field(item) for item in raw["__tuple__"])

    if isinstance(raw, list):
        return tuple(_decode_field(item) for item in raw)

    if isinstance(raw, dict):
        return {k: _decode_field(v) for k, v in raw.items()}

    return raw


__all__ = [
    "PERFORMANCE_SCHEMA_VERSION",
    "canonical_performance_json",
    "deserialize_performance",
    "parse_performance_header",
    "serialize_performance",
    "serialize_performance_bytes",
]
