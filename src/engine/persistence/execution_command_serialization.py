"""
Execution command persistence — serialization (Checkpoint 16.5).

Deterministic, self-describing JSON serialization for
:class:`~engine.models.execution_command.ExecutionCommand` records.

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

from engine.models.execution_command import (
    COMMAND_ID_PREFIX,
    EXECUTION_COMMAND_VERSION,
    ExecutionCommand,
    ExecutionMode,
)

#: Schema version for persisted command documents.
COMMAND_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_command(command: ExecutionCommand) -> str:
    """Serialize an :class:`ExecutionCommand` to canonical JSON text."""

    payload = {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "command": _to_json(command),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_command_bytes(command: ExecutionCommand) -> bytes:
    """Serialize a command to canonical JSON bytes."""

    return serialize_command(command).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_command(payload: str) -> ExecutionCommand:
    """Reconstruct an :class:`ExecutionCommand` from JSON text.

    Raises ``ValueError`` for an unsupported schema version, malformed
    JSON, or a missing ``command`` key.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed command JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "Malformed command payload: expected a JSON object."
        )
    version = parsed.get("schema_version")
    if version != COMMAND_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported command schema version {version!r}; "
            f"supported is {COMMAND_SCHEMA_VERSION}."
        )
    if "command" not in parsed:
        raise ValueError(
            "Malformed command payload: missing 'command' key."
        )
    return _from_json(parsed.get("command"), ExecutionCommand)


def parse_command_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse of a persisted command document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing the model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_command_json(command: ExecutionCommand) -> str:
    """Canonical (sorted-key) JSON text for a command."""

    return serialize_command(command)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a command model value to JSON-safe form."""

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
    "ExecutionCommand": ExecutionCommand,
}

# Mapping of enum name -> enum class.
_ENUMS: dict[str, type] = {
    "ExecutionMode": ExecutionMode,
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
    "COMMAND_SCHEMA_VERSION",
    "canonical_command_json",
    "deserialize_command",
    "parse_command_header",
    "serialize_command",
    "serialize_command_bytes",
]
