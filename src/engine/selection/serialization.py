"""
Deterministic serialization for selection decisions (Sprint 11N).

The selection decision is persisted as a self-describing, canonical
JSON document so it can be reloaded later WITHOUT rerunning the trading
pipeline, the experiment runner or the suite runner.

Every non-primitive value carries a small type tag (``__enum__``) so the
deserializer reconstructs the EXACT model type. Enums are stored by their
stable member name. The persisted text uses sorted keys so identical
decisions always produce identical bytes (suitable for byte-level audit
/ identity comparison).

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No timestamps.

* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.

* No global mutable state. Pure functions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from engine.models.selection import (
    PromotionDecision,
    RejectionReason,
    SELECTION_SCHEMA_VERSION,
    SelectionCandidate,
    SelectionCriteria,
    SelectionResult,
    SelectionStatus,
    SelectionType,
    SelectedResult,
)
from engine.models.experiment import ExperimentEvidenceStatus


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_selection(result: SelectionResult) -> str:
    """
    Serialize a :class:`SelectionResult` to canonical JSON text.

    The schema version is written at the top of the document. Keys are
    sorted for determinism.
    """

    payload = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_id": result.selection_id,
        "label": result.label,
        "selection_type": _to_json(result.selection_type),
        "criteria": _to_json(result.criteria),
        "candidates": _to_json(tuple(result.candidates)),
        "rejected": _to_json(tuple(result.rejected)),
        "selected": _to_json(result.selected),
        "promotion": _to_json(result.promotion),
        "conclusions": list(result.conclusions),
        "metadata": dict(result.metadata),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_selection_bytes(result: SelectionResult) -> bytes:
    """Serialize a selection decision to canonical JSON bytes."""

    return serialize_selection(result).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_selection(payload: str) -> SelectionResult:
    """Reconstruct a :class:`SelectionResult` from JSON text."""

    parsed = json.loads(payload)
    return _from_parsed(parsed)


def parse_selection_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted selection document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _from_parsed(parsed: dict[str, Any]) -> SelectionResult:
    """Reconstruct a SelectionResult from a parsed mapping."""

    return SelectionResult(
        selection_id=parsed["selection_id"],
        label=parsed.get("label", ""),
        selection_type=_from_json(parsed["selection_type"], SelectionType),
        criteria=_from_json(parsed["criteria"], SelectionCriteria),
        candidates=tuple(
            _from_json(c, SelectionCandidate)
            for c in _as_seq(parsed.get("candidates"))
        ),
        rejected=tuple(
            _from_json(r, RejectionReason)
            for r in _as_seq(parsed.get("rejected"))
        ),
        selected=(
            _from_json(parsed["selected"], SelectedResult)
            if parsed.get("selected") is not None
            else None
        ),
        promotion=(
            _from_json(parsed["promotion"], PromotionDecision)
            if parsed.get("promotion") is not None
            else None
        ),
        conclusions=tuple(parsed.get("conclusions", [])),
        metadata=dict(parsed.get("metadata", {})),
    )


def _as_seq(value: Any) -> list:
    """
    Unwrap a tagged tuple / plain list into a list for iteration.

    Selection sequence fields are stored as ``{"__tuple__": [...]}``;
    fall back to a plain list for forward-compatibility.
    """

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


def canonical_selection_json(result: SelectionResult) -> str:
    """
    Canonical (sorted-key) JSON text for a selection decision.

    Identical decisions always produce identical text. Suitable for
    byte-level identity / audit comparisons.
    """

    return serialize_selection(result)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a selection model value to JSON-safe form."""

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
    "SelectionCriteria": SelectionCriteria,
    "RejectionReason": RejectionReason,
    "SelectionCandidate": SelectionCandidate,
    "SelectedResult": SelectedResult,
    "PromotionDecision": PromotionDecision,
    "SelectionResult": SelectionResult,
}

# Mapping of enum name -> enum class, for fields we persist.
_ENUMS = {
    "SelectionStatus": SelectionStatus,
    "SelectionType": SelectionType,
    "ExperimentEvidenceStatus": ExperimentEvidenceStatus,
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
        fields_payload = value.get("fields", {}) if isinstance(value, dict) else {}
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            raw = fields_payload.get(f.name)
            kwargs[f.name] = _decode_field(f.type, raw)
        return cls(**kwargs)

    if expected_type in (tuple, list):
        if isinstance(value, dict) and "__tuple__" in value:
            return tuple(value["__tuple__"])
        return tuple(value) if isinstance(value, list) else value

    if expected_type in (str, int, float, bool):
        return value

    return value


def _decode_field(field_type: Any, raw: Any) -> Any:
    """
    Decode a single dataclass field from its JSON-safe form.

    The field type annotation is a string (from ``from __future__ import
    annotations``); we decode heuristically based on the raw value's
    shape so we do not need to resolve forward-reference strings.
    """

    if raw is None:
        return None

    # Enum-encoded values.
    if isinstance(raw, dict) and "__enum__" in raw:
        name = raw["__enum__"]
        for enum_cls in _ENUMS.values():
            try:
                return enum_cls[name]
            except KeyError:
                continue
        return name

    # Dataclass-encoded values.
    if isinstance(raw, dict) and "__dataclass__" in raw:
        cls_name = raw["__dataclass__"]
        cls = _DATACLASSES.get(cls_name)
        if cls is None:
            return raw
        fields_payload = raw.get("fields", {})
        kwargs = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            kwargs[f.name] = _decode_field(f.type, fields_payload.get(f.name))
        return cls(**kwargs)

    # Tuple-encoded values.
    if isinstance(raw, dict) and "__tuple__" in raw:
        return tuple(
            _decode_field(None, item) for item in raw["__tuple__"]
        )

    # Plain list -> tuple (selection models use tuples for sequences).
    if isinstance(raw, list):
        return tuple(_decode_field(None, item) for item in raw)

    # Plain dict -> mapping (e.g. metadata, parameter_values).
    if isinstance(raw, dict):
        return {
            k: _decode_field(None, v) for k, v in raw.items()
        }

    return raw


__all__ = [
    "canonical_selection_json",
    "deserialize_selection",
    "parse_selection_header",
    "serialize_selection",
    "serialize_selection_bytes",
]
