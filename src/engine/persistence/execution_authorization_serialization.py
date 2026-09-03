"""
Execution authorization persistence — serialization (Checkpoint 15.5).

Deterministic, self-describing JSON serialization for
:class:`~engine.models.execution_authorization.ExecutionAuthorization`
records.

Design rules:

* Schema-versioned. The schema version is written at the top of every
  persisted document and validated BEFORE any model reconstruction.
* Deterministic. Sorted keys, stable value encoding. No ``repr()`` /
  memory addresses. No nondeterministic values in persisted identity.
* Lossless. Decimal values are stored as strings. Datetime values are
  stored as ISO-8601 strings. Enums are stored by their stable member
  name. Tuples are preserved as tuples.
* No ``pickle`` / ``eval`` / ``exec``. Only ``json`` + ``Decimal`` +
  ``datetime``.
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
)

#: Schema version for persisted authorization documents.
AUTHORIZATION_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_authorization(authorization: ExecutionAuthorization) -> str:
    """Serialize an :class:`ExecutionAuthorization` to canonical JSON text."""

    payload = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorization": _to_json(authorization),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_authorization_bytes(authorization: ExecutionAuthorization) -> bytes:
    """Serialize an authorization to canonical JSON bytes."""

    return serialize_authorization(authorization).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_authorization(payload: str) -> ExecutionAuthorization:
    """Reconstruct an :class:`ExecutionAuthorization` from JSON text.

    Raises ``ValueError`` for an unsupported schema version, malformed
    JSON, or a missing ``authorization`` key.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed authorization JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "Malformed authorization payload: expected a JSON object."
        )
    version = parsed.get("schema_version")
    if version != AUTHORIZATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported authorization schema version {version!r}; "
            f"supported is {AUTHORIZATION_SCHEMA_VERSION}."
        )
    if "authorization" not in parsed:
        raise ValueError(
            "Malformed authorization payload: missing 'authorization' key."
        )
    return _from_json(parsed.get("authorization"), ExecutionAuthorization)


def parse_authorization_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse of a persisted authorization document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing the model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_authorization_json(authorization: ExecutionAuthorization) -> str:
    """Canonical (sorted-key) JSON text for an authorization."""

    return serialize_authorization(authorization)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode an authorization model value to JSON-safe form."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
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
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


# Mapping of tagged dataclass name -> model class.
_DATACLASSES: dict[str, type] = {
    "ExecutionAuthorization": ExecutionAuthorization,
}

# Mapping of enum name -> enum class.
_ENUMS: dict[str, type] = {
    "AuthorizationStatus": AuthorizationStatus,
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
        if isinstance(value, (int, float)):
            return Decimal(str(value))
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
        cls = expected_type
        fields_payload = (
            value.get("fields", {}) if isinstance(value, dict) else {}
        )
        kwargs: dict[str, Any] = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(fields_payload.get(f.name), f.type)
        return cls(**kwargs)
    if expected_type in (tuple, list, str, int, float, bool):
        if expected_type in (tuple, list):
            if isinstance(value, dict) and "__tuple__" in value:
                return tuple(value["__tuple__"])
            return tuple(value) if isinstance(value, list) else value
        return value
    return value


def _decode_field(raw: Any, ftype: Any = None) -> Any:
    """Decode a single dataclass field from its JSON-safe form."""

    if raw is None:
        return None
    if isinstance(raw, dict) and "__decimal__" in raw:
        return Decimal(raw["__decimal__"])
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
        kwargs: dict[str, Any] = {}
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
    "AUTHORIZATION_SCHEMA_VERSION",
    "canonical_authorization_json",
    "deserialize_authorization",
    "parse_authorization_header",
    "serialize_authorization",
    "serialize_authorization_bytes",
]
