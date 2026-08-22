"""
Deterministic serialization for live paper validation observations
(Product Phase 6F).

A :class:`~engine.models.live_validation.LiveValidationObservation` is
persisted as a self-describing, canonical JSON document so it can be
reloaded later WITHOUT rerunning the validation cycle. This is the
persistence format used by the dashboard live-validation store
(``dashboard/live_validation_store.py``).

Follows the established repository convention (``__enum__`` /
``__dataclass__`` / ``__decimal__`` / ``__datetime__`` / ``__tuple__``
type tags; schema version checked BEFORE model reconstruction; future
versions rejected). ``Decimal`` values are stored as strings so R
precision survives the round trip. The persisted text uses sorted keys
so identical observations always produce identical bytes. No ``pickle``
/ ``eval`` / ``exec``.

The round trip is LOSSLESS for every audit field: validation_id,
instrument, timeframes, evaluation timestamp, provider / provider status
/ freshness, decision classification, actionability, direction, geometry
availability, historical context status / strength / sample size /
descriptive statistics, research ids (provenance), paper-trade id
reference, outcome status / timestamp, realized R, validation status,
recorded_at / updated_at / revision, warnings, limitations, label,
metadata.

``intelligence/__init__.py`` stays intentionally empty — import via the
full path:

    from engine.intelligence.live_validation_serialization import (
        serialize_observation, deserialize_observation,
    )
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.live_validation import (
    LiveValidationObservation,
    LiveValidationStatus,
)


#: Schema version for persisted live-validation documents.
LIVE_VALIDATION_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_observation(observation: LiveValidationObservation) -> str:
    """Serialize a :class:`LiveValidationObservation` to canonical JSON."""

    payload = {
        "schema_version": LIVE_VALIDATION_SCHEMA_VERSION,
        "validation_id": observation.validation_id,
        "observation": _to_json(observation),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_observation_bytes(observation: LiveValidationObservation) -> bytes:
    """Serialize an observation to canonical JSON bytes."""

    return serialize_observation(observation).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_observation(payload: str) -> LiveValidationObservation:
    """Reconstruct a :class:`LiveValidationObservation` from JSON text.

    Raises ``ValueError`` for an unsupported schema version, malformed
    JSON, a non-object payload, or a missing ``observation`` key.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed live-validation JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "Malformed live-validation payload: expected a JSON object."
        )
    version = parsed.get("schema_version")
    if version != LIVE_VALIDATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported live-validation schema version {version!r}; "
            f"supported is {LIVE_VALIDATION_SCHEMA_VERSION}.",
        )
    if "observation" not in parsed:
        raise ValueError(
            "Malformed live-validation payload: missing 'observation' key."
        )
    return _from_json(parsed.get("observation"), LiveValidationObservation)


def parse_observation_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse of a persisted observation document."""

    return json.loads(payload)


def canonical_observation_json(observation: LiveValidationObservation) -> str:
    """Canonical (sorted-key) JSON text for an observation."""

    return serialize_observation(observation)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a live-validation model value to JSON-safe form."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, Enum):
        return {"__enum__": value.name, "__enum_class__": type(value).__name__}
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
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


_DATACLASSES = {
    "LiveValidationObservation": LiveValidationObservation,
}

_ENUMS = {
    "LiveValidationStatus": LiveValidationStatus,
}


def _from_json(value: Any, expected_type: type) -> Any:
    """Reconstruct a value of ``expected_type`` from its JSON-safe form."""

    if value is None:
        return None
    if expected_type is Decimal:
        if isinstance(value, dict) and "__decimal__" in value:
            return Decimal(value["__decimal__"])
        if isinstance(value, str):
            return Decimal(value)
        return value
    if expected_type is datetime:
        if isinstance(value, dict) and "__datetime__" in value:
            return datetime.fromisoformat(value["__datetime__"])
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
    if expected_type in _ENUMS.values():
        if isinstance(value, dict) and "__enum__" in value:
            return expected_type[value["__enum__"]]
        if isinstance(value, str):
            return expected_type[value]
        return value
    if expected_type in _DATACLASSES.values():
        fields_payload = value.get("fields", {}) if isinstance(value, dict) else {}
        kwargs = {}
        for f in expected_type.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name), f.type)
        return expected_type(**kwargs)
    return _decode_field(value)


def _decode_field(raw: Any, ftype: Any = None) -> Any:
    """Decode a single dataclass field from its JSON-safe form."""

    if raw is None:
        return None
    if isinstance(raw, dict) and "__decimal__" in raw:
        return Decimal(raw["__decimal__"])
    if isinstance(raw, dict) and "__datetime__" in raw:
        return datetime.fromisoformat(raw["__datetime__"])
    if isinstance(raw, dict) and "__enum__" in raw:
        cls_name = raw.get("__enum_class__", "")
        name = raw["__enum__"]
        enum_cls = _ENUMS.get(cls_name)
        if enum_cls is not None:
            return enum_cls[name]
        for candidate in _ENUMS.values():
            try:
                return candidate[name]
            except KeyError:
                continue
        return name
    if isinstance(raw, dict) and "__dataclass__" in raw:
        cls = _DATACLASSES.get(raw["__dataclass__"])
        if cls is None:
            return None
        fields_payload = raw.get("fields", {})
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name), f.type)
        return cls(**kwargs)
    if isinstance(raw, dict) and "__tuple__" in raw:
        return tuple(_decode_field(item) for item in raw["__tuple__"])
    if isinstance(raw, list):
        return tuple(_decode_field(item) for item in raw)
    if isinstance(raw, dict):
        return {k: _decode_field(v) for k, v in raw.items()}
    return raw


__all__ = [
    "LIVE_VALIDATION_SCHEMA_VERSION",
    "canonical_observation_json",
    "deserialize_observation",
    "parse_observation_header",
    "serialize_observation",
    "serialize_observation_bytes",
]
