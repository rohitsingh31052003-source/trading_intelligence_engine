"""
Deterministic serialization for historical outcome results (Sprint 11W).

The outcome result is persisted as a self-describing, canonical JSON
document so it can be reloaded later WITHOUT rerunning the underlying
outcome evaluator.

Serialization scope (intentional, matching the Sprint 11U scan
serialization discipline):

The lightweight, descriptive outcome projections are persisted IN FULL:
the outcome status, outcome timestamp, exit price, bars held, MFE / MAE
(absolute + R-normalized), realized R, risk, reason, and the embedded
:class:`OutcomeSubject` projection (instrument, direction, evaluation
timestamp, entry / stop / target, decision classification / score,
opportunity status, rank, scan id, setup timeframe). The heavy
per-engine reference outputs are NOT involved (the outcome evaluator
is already decoupled from them via :class:`OutcomeSubject`), so the
round trip is lossless for every field the outcome model carries.

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__datetime__`` / ``__tuple__``)
so the deserializer reconstructs the EXACT model type. Enums are stored
by their stable member name. The persisted text uses sorted keys so
identical outcomes always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No nondeterministic values in the report id.

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

from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
    ReplayOutcomePoint,
    ReplayOutcomeReport,
)


#: Schema version for persisted outcome documents.
OUTCOME_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_outcome(outcome: HistoricalOutcome) -> str:
    """Serialize a :class:`HistoricalOutcome` to canonical JSON text."""

    payload = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "outcome": _to_json(outcome),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_outcome_bytes(outcome: HistoricalOutcome) -> bytes:
    """Serialize a historical outcome to canonical JSON bytes."""

    return serialize_outcome(outcome).encode("utf-8")


def serialize_outcome_report(report: ReplayOutcomeReport) -> str:
    """Serialize a :class:`ReplayOutcomeReport` to canonical JSON text."""

    payload = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "report": _to_json(report),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_outcome(payload: str) -> HistoricalOutcome:
    """Reconstruct a :class:`HistoricalOutcome` from JSON text."""

    parsed = json.loads(payload)
    version = parsed.get("schema_version")
    if version != OUTCOME_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported outcome schema version {version!r}; "
            f"supported is {OUTCOME_SCHEMA_VERSION}.",
        )
    return _from_json(parsed.get("outcome"), HistoricalOutcome)


def deserialize_outcome_report(payload: str) -> ReplayOutcomeReport:
    """Reconstruct a :class:`ReplayOutcomeReport` from JSON text."""

    parsed = json.loads(payload)
    version = parsed.get("schema_version")
    if version != OUTCOME_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported outcome schema version {version!r}; "
            f"supported is {OUTCOME_SCHEMA_VERSION}.",
        )
    return _from_json(parsed.get("report"), ReplayOutcomeReport)


def parse_outcome_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted outcome document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_outcome_json(outcome: HistoricalOutcome) -> str:
    """Canonical (sorted-key) JSON text for a historical outcome."""

    return serialize_outcome(outcome)


def canonical_outcome_report_json(report: ReplayOutcomeReport) -> str:
    """Canonical (sorted-key) JSON text for a replay outcome report."""

    return serialize_outcome_report(report)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode an outcome model value to JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, Enum):
        return {"__enum__": value.name}

    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}

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
    "HistoricalOutcome": HistoricalOutcome,
    "OutcomeSubject": OutcomeSubject,
    "ReplayOutcomePoint": ReplayOutcomePoint,
    "ReplayOutcomeReport": ReplayOutcomeReport,
}

# Mapping of enum name -> enum class.
_ENUMS = {
    "OutcomeStatus": OutcomeStatus,
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
        fields_payload = (
            value.get("fields", {}) if isinstance(value, dict) else {}
        )
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
        return tuple(_decode_field(item) for item in raw["__tuple__"])

    if isinstance(raw, list):
        return tuple(_decode_field(item) for item in raw)

    if isinstance(raw, dict):
        return {k: _decode_field(v) for k, v in raw.items()}

    return raw


__all__ = [
    "OUTCOME_SCHEMA_VERSION",
    "canonical_outcome_json",
    "canonical_outcome_report_json",
    "deserialize_outcome",
    "deserialize_outcome_report",
    "parse_outcome_header",
    "serialize_outcome",
    "serialize_outcome_bytes",
    "serialize_outcome_report",
]
