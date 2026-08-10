"""
Deterministic serialization for experiment results (Sprint 11K).

The persistence layer stores completed ``ExperimentResult`` objects
as canonical JSON so they can be retrieved and compared later
WITHOUT rerunning the underlying trading pipeline.

Design rules:

* Deterministic.
  The same experiment object always produces the same serialized
  representation when its content is unchanged. Output uses sorted
  keys and stable value encoding. No ``repr()``, no memory
  addresses, no insertion-order dependence.

* Self-describing.
  Every non-primitive value carries a small type tag so the
  deserializer can reconstruct the exact model type without
  parsing type annotations (which may be ``Any`` or unions).

* Faithful projection, not raw dump.
  The structured research / evaluation views (statistics,
  robustness, walk-forward, OOS, data sufficiency, leakage,
  reproducibility, summary) are persisted in full. The raw
  walk-forward / pipeline engine outputs retained *by reference*
  for future layers (``EvaluationReport.result``,
  ``ResearchReport.result``, ``WalkForwardSelectionReport.out_of_sample_result``,
  ``OutOfSampleReport.in_sample_results`` /
  ``OutOfSampleReport.out_of_sample_results``) are NOT persisted:
  they are heavy per-evaluation-point / per-trade raw engine
  outputs that are regenerable by rerunning and were never part of
  the persisted research record. They are reconstructed as
  ``None`` / empty so loaded results are still valid model
  instances. This is documented and intentional.

* No external dependencies.
  Only the standard library is used (``json``, ``importlib``,
  ``datetime``, ``dataclasses``, ``enum``).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from engine.models.experiment import ExperimentResult
from engine.models.registry import SCHEMA_VERSION, ExperimentRecordHeader


# ============================================================
# RAW-RESULT FIELD EXCLUSION
# ============================================================

#: Fields that hold raw engine outputs retained *by reference* and
#: are therefore NOT persisted. They are reconstructed as ``None``.
#:
#: Keyed by ``"<ClassName>.<field_name>"`` so the rule is precise
#: and does not accidentally drop unrelated fields that happen to
#: share a name.
_RAW_RESULT_FIELDS: frozenset[str] = frozenset(
    {
        # The raw PipelineResult retained by the evaluation report.
        "EvaluationReport.result",
        # The raw PipelineResult retained by the research report.
        "ResearchReport.result",
        # The raw PipelineResult of the walk-forward evaluation run.
        "WalkForwardSelectionReport.out_of_sample_result",
        # Raw per-trade validation result tuples on the OOS report.
        "OutOfSampleReport.in_sample_results",
        "OutOfSampleReport.out_of_sample_results",
    }
)


# ============================================================
# TYPE TAGS
# ============================================================

#: Reserved keys used as type discriminators in the serialized
#: representation. They are intentionally short and prefixed to
#: avoid colliding with real model field names.
_TAG_TYPE = "__type__"
_TAG_ENUM = "__enum__"
_TAG_DATETIME = "__datetime__"
_TAG_TUPLE = "__tuple__"
_TAG_ENGINE = "__engine__"
_TAG_BYTES = "__bytes__"


# ============================================================
# CANONICAL SERIALIZATION
# ============================================================


def serialize_experiment(result: ExperimentResult) -> str:
    """
    Serialize an :class:`ExperimentResult` to canonical JSON text.

    The output is deterministic: identical experiment content
    always yields an identical string (sorted keys, stable value
    encoding). Suitable for byte-level identity comparison and for
    on-disk storage.
    """

    payload = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": result.experiment_id,
        "result": _to_jsonable(result, owner=None, field_name=None),
    }

    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_experiment_bytes(result: ExperimentResult) -> bytes:
    """Byte form of :func:`serialize_experiment` (UTF-8 encoded)."""

    return serialize_experiment(result).encode("utf-8")


# ============================================================
# SERIALIZER
# ============================================================


def _to_jsonable(
    value: Any,
    owner: type | None,
    field_name: str | None,
) -> Any:
    """
    Recursively convert a value into a JSON-serializable,
    self-describing structure.

    ``owner`` / ``field_name`` (when traversing a dataclass field)
    are used to detect raw-result fields that must be dropped.
    """

    if value is None:
        return None

    # bool BEFORE int (bool is a subclass of int in Python).
    if isinstance(value, bool):
        return value

    if isinstance(value, Enum):
        return {
            _TAG_ENUM: _qualified(type(value)),
            "name": value.name,
        }

    if isinstance(value, datetime):
        return {_TAG_DATETIME: value.isoformat()}

    if isinstance(value, (bytes, bytearray)):
        return {
            _TAG_BYTES: bytes(value).decode("utf-8", errors="replace"),
        }

    if isinstance(value, (str, int, float)):
        return value

    # Dataclass handling (frozen or mutable).
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        owner_name = cls.__name__
        out: dict[str, Any] = {_TAG_TYPE: _qualified(cls)}
        for f in fields(value):
            fname = f.name
            child = getattr(value, fname)
            if _is_raw_result_field(owner_name, fname):
                # Raw engine output retained by reference: drop it.
                continue
            out[fname] = _to_jsonable(
                child, owner=cls, field_name=fname
            )
        return out

    if isinstance(value, Mapping):
        return {
            str(k): _to_jsonable(v, owner=type(value), field_name=None)
            for k, v in value.items()
        }

    if isinstance(value, tuple):
        return {
            _TAG_TUPLE: [
                _to_jsonable(item, owner=type(value), field_name=None)
                for item in value
            ]
        }

    if isinstance(value, list):
        return [
            _to_jsonable(item, owner=type(value), field_name=None)
            for item in value
        ]

    if isinstance(value, (set, frozenset)):
        return {
            _TAG_TUPLE: [
                _to_jsonable(item, owner=type(value), field_name=None)
                for item in sorted(value, key=_sort_key)
            ]
        }

    # Non-dataclass, non-enum object (e.g. an embedded engine
    # instance such as ``SegmentationConfig.regime_engine``).
    # Represent it by its stable fully-qualified class name and
    # reconstruct it with a no-arg constructor on load.
    return _engine_token(value)


def _is_raw_result_field(owner_name: str, field_name: str) -> bool:
    """Whether ``owner_name.field_name`` is a dropped raw-result field."""

    return f"{owner_name}.{field_name}" in _RAW_RESULT_FIELDS


def _engine_token(value: Any) -> dict[str, str]:
    """Encode an embedded engine instance as a reconstructable token."""

    return {_TAG_ENGINE: _qualified(type(value))}


def _qualified(cls: type) -> str:
    """Fully-qualified name ``module.Class`` for a class."""

    module = getattr(cls, "__module__", "") or ""
    qualname = getattr(cls, "__qualname__", cls.__name__)
    return f"{module}.{qualname}" if module else qualname


def _sort_key(value: Any) -> tuple[int, Any]:
    """Stable sort key for set canonicalization."""

    if isinstance(value, (str, int, float, bool)):
        return (0, value)
    return (1, repr(value))


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_experiment(payload: str | bytes | Mapping[str, Any]) -> ExperimentResult:
    """
    Deserialize a persisted record back into an
    :class:`ExperimentResult`.

    The top-level ``schema_version`` is validated against the
    current :data:`SCHEMA_VERSION`; an unsupported version raises
    :class:`UnsupportedSchemaVersionError` before any model
    reconstruction is attempted.
    """

    raw = _coerce_to_mapping(payload)

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        from engine.registry.exceptions import (
            UnsupportedSchemaVersionError,
        )

        raise UnsupportedSchemaVersionError(
            found=schema_version, supported=SCHEMA_VERSION
        )

    if "result" not in raw:
        from engine.registry.exceptions import (
            ExperimentPersistenceError,
        )

        raise ExperimentPersistenceError(
            "Persisted record is missing the 'result' section."
        )

    result = _from_jsonable(raw["result"])

    if not isinstance(result, ExperimentResult):
        from engine.registry.exceptions import (
            ExperimentIntegrityError,
        )

        raise ExperimentIntegrityError(
            "Persisted 'result' did not deserialize to an "
            "ExperimentResult."
        )

    return result


def parse_record(payload: str | bytes | Mapping[str, Any]) -> ExperimentRecordHeader:
    """
    Parse ONLY the header of a persisted record.

    Useful for cheap inspection / listing without reconstructing
    the full result. Raises :class:`UnsupportedSchemaVersionError`
    on a future schema version.
    """

    raw = _coerce_to_mapping(payload)
    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        from engine.registry.exceptions import (
            UnsupportedSchemaVersionError,
        )

        raise UnsupportedSchemaVersionError(
            found=schema_version, supported=SCHEMA_VERSION
        )

    return ExperimentRecordHeader(
        schema_version=int(schema_version),
        experiment_id=str(raw.get("experiment_id", "")),
    )


def canonical_json(payload: str | bytes | Mapping[str, Any]) -> str:
    """
    Return the canonical (sorted-key) JSON text for a record.

    Used for byte-level identity / audit comparisons. Accepts
    either a JSON string or an already-parsed mapping.
    """

    raw = _coerce_to_mapping(payload)
    return json.dumps(raw, sort_keys=True, ensure_ascii=False)


# ============================================================
# DESERIALIZER
# ============================================================


def _coerce_to_mapping(payload: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            from engine.registry.exceptions import (
                ExperimentPersistenceError,
            )

            raise ExperimentPersistenceError(
                f"Persisted record is not valid JSON: {exc}"
            ) from exc
    from engine.registry.exceptions import ExperimentPersistenceError

    raise ExperimentPersistenceError(
        f"Unsupported payload type for deserialization: "
        f"{type(payload).__name__!r}."
    )


def _from_jsonable(value: Any) -> Any:
    """Recursively reconstruct a value from its self-describing form."""

    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (str, int, float)):
        return value

    if isinstance(value, Mapping):
        # Detect a type tag (any of the reserved keys).
        if _TAG_ENUM in value:
            cls = _resolve(value[_TAG_ENUM])
            return cls[value["name"]]
        if _TAG_DATETIME in value:
            return datetime.fromisoformat(value[_TAG_DATETIME])
        if _TAG_BYTES in value:
            return value[_TAG_BYTES].encode("utf-8")
        if _TAG_TUPLE in value:
            return tuple(
                _from_jsonable(item) for item in value[_TAG_TUPLE]
            )
        if _TAG_ENGINE in value:
            return _instantiate_engine(value[_TAG_ENGINE])
        if _TAG_TYPE in value:
            cls = _resolve(value[_TAG_TYPE])
            kwargs = {
                k: _from_jsonable(v)
                for k, v in value.items()
                if k != _TAG_TYPE
            }
            return cls(**kwargs)
        # Plain mapping: reconstruct as dict.
        return {
            str(k): _from_jsonable(v) for k, v in value.items()
        }

    if isinstance(value, list):
        return [_from_jsonable(item) for item in value]

    return value


# ============================================================
# TYPE RESOLUTION
# ============================================================


def _resolve(qualified_name: str) -> type:
    """Import and return the class identified by ``module.Class``."""

    if "." not in qualified_name:
        raise ImportError(
            f"Cannot resolve type {qualified_name!r}: missing module "
            f"prefix."
        )

    module_name, _, class_name = qualified_name.rpartition(".")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(
            f"Type {qualified_name!r} not found in module "
            f"{module_name!r}."
        ) from exc


def _instantiate_engine(qualified_name: str) -> Any:
    """Instantiate an embedded engine by its no-arg constructor."""

    cls = _resolve(qualified_name)
    return cls()
