"""
Deterministic serialization for the backtest validation report
(Sprint 12C).

The validation report is persisted as a self-describing, canonical JSON
document so it can be reloaded later WITHOUT re-running the validation
suite (and therefore WITHOUT re-running any of the downstream 11X /
11Y / 11Z / 12A / 12B engines).

Serialization scope (intentional, matching the Sprint 11U / 11W / 11X
/ 11Y / 11Z / 12A / 12B discipline):

The lightweight validation projections are persisted IN FULL:

* :class:`BacktestValidationReport` — validation identity, the overall
  + per-category statuses, scenario / check / passed / failed / skipped
  counts, the outcome distribution, label, metadata, rationale.
* :class:`ScenarioResult` — name, outcome count, checks.
* :class:`CheckResult` — name, category, status, detail.
* :class:`CategorySummary` — category + counts.

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__tuple__``) so the deserializer
reconstructs the EXACT model type. Enums are stored by their stable
member name. The persisted text uses sorted keys so identical results
always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() /
  memory addresses. No nondeterministic values in the ids.
* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.
* No global mutable state. Pure functions.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from engine.models.backtest_validation import (
    BacktestValidationReport,
    CategorySummary,
    CheckResult,
    ScenarioResult,
    ValidationCategory,
    ValidationCheckStatus,
)


#: Schema version for persisted backtest-validation documents.
BACKTEST_VALIDATION_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_validation(report: BacktestValidationReport) -> str:
    """Serialize a :class:`BacktestValidationReport` to canonical JSON."""

    payload = {
        "schema_version": BACKTEST_VALIDATION_SCHEMA_VERSION,
        "report": _to_json(report),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_validation_bytes(report: BacktestValidationReport) -> bytes:
    """Serialize a validation report to canonical JSON bytes."""

    return serialize_validation(report).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_validation(payload: str) -> BacktestValidationReport:
    """Reconstruct a :class:`BacktestValidationReport` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_json(parsed.get("report"), BacktestValidationReport)


def parse_validation_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted validation document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _check_schema(parsed: Any) -> None:
    version = parsed.get("schema_version") if isinstance(parsed, dict) else None
    if version != BACKTEST_VALIDATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported backtest-validation schema version {version!r}; "
            f"supported is {BACKTEST_VALIDATION_SCHEMA_VERSION}.",
        )


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_validation_json(report: BacktestValidationReport) -> str:
    """Canonical (sorted-key) JSON text for a validation report."""

    return serialize_validation(report)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================

_ENUMS = {
    "ValidationCheckStatus": ValidationCheckStatus,
    "ValidationCategory": ValidationCategory,
}

_DATACLASSES = {
    "BacktestValidationReport": BacktestValidationReport,
    "ScenarioResult": ScenarioResult,
    "CheckResult": CheckResult,
    "CategorySummary": CategorySummary,
}


def _to_json(value: Any) -> Any:
    """Recursively encode a validation model value to JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, Enum):
        return {"__enum__": value.name, "__enum_class__": type(value).__name__}

    if isinstance(value, (list, tuple)):
        return {"__tuple__": [_to_json(item) for item in value]}

    if isinstance(value, dict):
        return {str(k): _to_json(v) for k, v in value.items()}

    if isinstance(value, (str, int, float, bool)):
        return value

    # Dataclass instance (check via __dataclass_fields__ to avoid
    # importing is_dataclass for the small local model set).
    if hasattr(value, "__dataclass_fields__") and not isinstance(value, type):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {
                f.name: _to_json(getattr(value, f.name))
                for f in value.__dataclass_fields__.values()  # type: ignore[attr-defined]
            },
        }

    return str(value)


def _from_json(value: Any, expected_type: type | None = None) -> Any:
    """Reconstruct a value from its JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, dict) and "__enum__" in value:
        name = value["__enum__"]
        cls_name = value.get("__enum_class__")
        if cls_name is not None:
            enum_cls = _ENUMS.get(cls_name)
            if enum_cls is not None:
                try:
                    return enum_cls[name]
                except KeyError:
                    pass
        for enum_cls in _ENUMS.values():
            try:
                return enum_cls[name]
            except KeyError:
                continue
        return name

    if isinstance(value, dict) and "__dataclass__" in value:
        cls_name = value["__dataclass__"]
        cls = _DATACLASSES.get(cls_name)
        if cls is None:
            return None
        fields_payload = value.get("fields", {})
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            field_type = f.type
            kwargs[f.name] = _from_json(
                fields_payload.get(f.name),
                field_type if isinstance(field_type, type) else None,
            )
        return cls(**kwargs)

    if isinstance(value, dict) and "__tuple__" in value:
        return tuple(_from_json(item) for item in value["__tuple__"])

    if isinstance(value, list):
        return tuple(_from_json(item) for item in value)

    if isinstance(value, dict):
        return {k: _from_json(v) for k, v in value.items()}

    return value


__all__ = [
    "BACKTEST_VALIDATION_SCHEMA_VERSION",
    "canonical_validation_json",
    "deserialize_validation",
    "parse_validation_header",
    "serialize_validation",
    "serialize_validation_bytes",
]
