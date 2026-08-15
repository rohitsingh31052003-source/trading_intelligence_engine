"""
Deterministic serialization for the evidence-conditioned strategy
intelligence results (Sprint 11Z).

The strategy results are persisted as self-describing, canonical JSON
documents so they can be reloaded later WITHOUT re-evaluating the
underlying historical outcomes, performance analytics or evidence
classification.

Serialization scope (intentional, matching the Sprint 11U / 11W / 11X
/ 11Y serialization discipline):

The lightweight strategy projections are persisted IN FULL:

* :class:`StrategyEvidenceAssessment` — assessment identity, cohort
  spec + key, the reused :class:`HistoricalPerformanceStatistics`
  (embedded by value), the reused :class:`EvidenceStrength`, the
  :class:`StrategyAssessmentStatus`, sample / resolved / valid-R
  counts, interpretation, limitations, label, metadata.
* :class:`CohortComparison` — comparison identity, spec, both cohort
  keys, presence flags, both assessments (embedded), the metric rows,
  the descriptive notes and the disclaimer.
* :class:`OpportunityEvidenceLookup` — lookup identity, profile, match
  status, matched spec, the matched :class:`HistoricalEvidenceCohort`
  (embedded: spec + key + reused statistics + strength + counts +
  rationale), the produced assessment, and the limitations.

The reused :class:`HistoricalPerformanceStatistics` are embedded and
reconstructed exactly. The :class:`HistoricalEvidenceCohort` matched
by a lookup is embedded (its lightweight fields only — the reused
statistics are embedded too). The heavy per-outcome reference objects
are NOT involved (the strategy result is already a pure aggregate over
already-computed evidence), so the round trip is LOSSLESS for every
field the strategy models carry.

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
from dataclasses import is_dataclass
from enum import Enum
from typing import Any

from engine.models.historical_evidence import (
    CohortSpec,
    EvidenceStrength,
    HistoricalEvidenceCohort,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    CohortComparison,
    CohortComparisonMetric,
    CohortMatchStatus,
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
    StrategyEvidenceAssessment,
)


#: Schema version for persisted strategy-intelligence documents.
STRATEGY_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_assessment(assessment: StrategyEvidenceAssessment) -> str:
    """Serialize a :class:`StrategyEvidenceAssessment` to canonical JSON."""

    payload = {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "assessment": _to_json(assessment),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_assessment_bytes(assessment: StrategyEvidenceAssessment) -> bytes:
    """Serialize an assessment to canonical JSON bytes."""

    return serialize_assessment(assessment).encode("utf-8")


def serialize_comparison(comparison: CohortComparison) -> str:
    """Serialize a :class:`CohortComparison` to canonical JSON."""

    payload = {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "comparison": _to_json(comparison),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_comparison_bytes(comparison: CohortComparison) -> bytes:
    """Serialize a comparison to canonical JSON bytes."""

    return serialize_comparison(comparison).encode("utf-8")


def serialize_lookup(lookup: OpportunityEvidenceLookup) -> str:
    """Serialize an :class:`OpportunityEvidenceLookup` to canonical JSON."""

    payload = {
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "lookup": _to_json(lookup),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_lookup_bytes(lookup: OpportunityEvidenceLookup) -> bytes:
    """Serialize a lookup to canonical JSON bytes."""

    return serialize_lookup(lookup).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_assessment(payload: str) -> StrategyEvidenceAssessment:
    """Reconstruct a :class:`StrategyEvidenceAssessment` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_json(parsed.get("assessment"), StrategyEvidenceAssessment)


def deserialize_comparison(payload: str) -> CohortComparison:
    """Reconstruct a :class:`CohortComparison` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_json(parsed.get("comparison"), CohortComparison)


def deserialize_lookup(payload: str) -> OpportunityEvidenceLookup:
    """Reconstruct an :class:`OpportunityEvidenceLookup` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_json(parsed.get("lookup"), OpportunityEvidenceLookup)


def parse_strategy_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted strategy document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _check_schema(parsed: Any) -> None:
    version = parsed.get("schema_version") if isinstance(parsed, dict) else None
    if version != STRATEGY_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported strategy schema version {version!r}; "
            f"supported is {STRATEGY_SCHEMA_VERSION}.",
        )


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_assessment_json(assessment: StrategyEvidenceAssessment) -> str:
    """Canonical (sorted-key) JSON text for an assessment."""

    return serialize_assessment(assessment)


def canonical_comparison_json(comparison: CohortComparison) -> str:
    """Canonical (sorted-key) JSON text for a comparison."""

    return serialize_comparison(comparison)


def canonical_lookup_json(lookup: OpportunityEvidenceLookup) -> str:
    """Canonical (sorted-key) JSON text for a lookup."""

    return serialize_lookup(lookup)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a strategy model value to JSON-safe form."""

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
    "StrategyEvidenceAssessment": StrategyEvidenceAssessment,
    "CohortComparison": CohortComparison,
    "CohortComparisonMetric": CohortComparisonMetric,
    "OpportunityEvidenceLookup": OpportunityEvidenceLookup,
    "OpportunityProfile": OpportunityProfile,
    "CohortSpec": CohortSpec,
    "HistoricalEvidenceCohort": HistoricalEvidenceCohort,
    "HistoricalPerformanceStatistics": HistoricalPerformanceStatistics,
}

# Mapping of enum name -> enum class.
_ENUMS = {
    "StrategyAssessmentStatus": StrategyAssessmentStatus,
    "CohortMatchStatus": CohortMatchStatus,
    "EvidenceStrength": EvidenceStrength,
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
    "STRATEGY_SCHEMA_VERSION",
    "canonical_assessment_json",
    "canonical_comparison_json",
    "canonical_lookup_json",
    "deserialize_assessment",
    "deserialize_comparison",
    "deserialize_lookup",
    "parse_strategy_header",
    "serialize_assessment",
    "serialize_assessment_bytes",
    "serialize_comparison",
    "serialize_comparison_bytes",
    "serialize_lookup",
    "serialize_lookup_bytes",
]
