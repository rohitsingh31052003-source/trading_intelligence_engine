"""
Deterministic JSON serialization for historical setup research results
(Product Phase 6D).

Self-describing, schema-versioned, deterministic (sorted keys, stable
value encoding, no ``repr()`` / memory addresses / wall-clock). The
round trip is LOSSLESS for every audit field of
:class:`~engine.models.setup_research.SetupResearchResult`, including
the embedded reused Sprint 11W
:class:`~engine.models.historical_outcome.HistoricalOutcome` /
:class:`~engine.models.historical_outcome.OutcomeSubject` and the
reused Sprint 11X
:class:`~engine.models.historical_performance.HistoricalPerformanceStatistics`
/ Sprint 11Y
:class:`~engine.models.historical_evidence.EvidenceStrength` objects
(already lightweight, serializable projections).

Follows the established repository convention (``__enum__`` +
``__enum_class__`` / ``__dataclass__`` / ``__datetime__`` /
``__tuple__`` type tags; schema version checked BEFORE model
reconstruction; future versions rejected). No ``pickle`` / ``eval`` /
``exec``.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import HistoricalPerformanceStatistics
from engine.models.setup_research import (
    SetupEvidence,
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)


SETUP_RESEARCH_SCHEMA_VERSION = 1


def _to_json(value: Any) -> Any:
    """Recursively encode a research model value to JSON-safe form."""

    if value is None:
        return None

    if isinstance(value, Enum):
        return {"__enum__": value.name, "__enum_class__": type(value).__name__}

    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}

    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {
                f.name: _to_json(getattr(value, f.name))
                for f in fields(value)
            },
        }

    if isinstance(value, (list, tuple)):
        return {"__tuple__": [_to_json(item) for item in value]}

    if isinstance(value, dict):
        return {str(k): _to_json(v) for k, v in value.items()}

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


_DATACLASSES = {
    "SetupResearchRequest": SetupResearchRequest,
    "SetupOccurrence": SetupOccurrence,
    "SetupResearchObservation": SetupResearchObservation,
    "SetupEvidence": SetupEvidence,
    "SetupResearchResult": SetupResearchResult,
    "HistoricalOutcome": HistoricalOutcome,
    "OutcomeSubject": OutcomeSubject,
    "HistoricalPerformanceStatistics": HistoricalPerformanceStatistics,
}

_ENUMS = {
    "SetupResearchStatus": SetupResearchStatus,
    "EvidenceStrength": EvidenceStrength,
    "OutcomeStatus": OutcomeStatus,
}


def _decode_field(raw: Any) -> Any:
    """Decode a single value from its JSON-safe tagged form."""

    if raw is None:
        return None

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
        cls_name = raw["__dataclass__"]
        cls = _DATACLASSES.get(cls_name)
        if cls is None:
            raise ValueError(f"unsupported dataclass payload {cls_name!r}.")
        fields_payload = raw.get("fields", {})
        kwargs = {}
        for f in fields(cls):
            kwargs[f.name] = _decode_field(fields_payload.get(f.name))
        return cls(**kwargs)

    if isinstance(raw, dict) and "__tuple__" in raw:
        return tuple(_decode_field(item) for item in raw["__tuple__"])

    if isinstance(raw, list):
        return tuple(_decode_field(item) for item in raw)

    if isinstance(raw, dict):
        return {k: _decode_field(v) for k, v in raw.items()}

    return raw


def _result_to_dict(result: SetupResearchResult) -> dict[str, Any]:
    return _to_json(result)


def _result_from_dict(payload: dict[str, Any]) -> SetupResearchResult:
    decoded = _decode_field(payload)
    if not isinstance(decoded, SetupResearchResult):
        raise ValueError("payload does not encode a SetupResearchResult.")
    return decoded


def serialize_result(result: SetupResearchResult) -> str:
    """Serialize a research result to a schema-versioned JSON string."""

    return json.dumps(
        {
            "schema_version": SETUP_RESEARCH_SCHEMA_VERSION,
            "result": _result_to_dict(result),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def serialize_result_bytes(result: SetupResearchResult) -> bytes:
    return serialize_result(result).encode("utf-8")


def deserialize_result(payload: str | bytes) -> SetupResearchResult:
    """Reconstruct a research result; validates the schema version FIRST."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"malformed setup research payload: {exc}") from exc
    if not isinstance(parsed, dict) or "schema_version" not in parsed:
        raise ValueError("payload is missing the schema_version header.")
    version = parsed["schema_version"]
    if version != SETUP_RESEARCH_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported setup research schema version {version!r}; "
            f"supported is {SETUP_RESEARCH_SCHEMA_VERSION}.",
        )
    result_payload = parsed.get("result")
    if not isinstance(result_payload, dict):
        raise ValueError("payload is missing the research result body.")
    return _result_from_dict(result_payload)


def canonical_result_json(result: SetupResearchResult) -> str:
    """Deterministic canonical JSON (sorted keys) of the result body."""

    return json.dumps(_result_to_dict(result), sort_keys=True, separators=(",", ":"))


def parse_result_header(payload: str | bytes) -> dict[str, Any]:
    """Cheap header-only parse (schema version + research id + status)."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("payload is not a JSON object.")
    body = parsed.get("result") or {}
    fields_payload = body.get("fields") or {}

    def _scalar(name: str) -> Any:
        value = fields_payload.get(name)
        if isinstance(value, dict) and "__enum__" in value:
            return value["__enum__"]
        return value

    return {
        "schema_version": parsed.get("schema_version"),
        "research_id": _scalar("research_id"),
        "status": _scalar("status"),
    }


__all__ = [
    "SETUP_RESEARCH_SCHEMA_VERSION",
    "canonical_result_json",
    "deserialize_result",
    "parse_result_header",
    "serialize_result",
    "serialize_result_bytes",
]
