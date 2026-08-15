"""
Deterministic serialization for the decision intelligence foundation
results (Sprint 12A).

The decision-intelligence context is persisted as a self-describing,
canonical JSON document so it can be reloaded later WITHOUT re-running
the Sprint 11Z lookup, the Sprint 11Y evidence classification, the
Sprint 11X analytics, the outcome evaluation or the pipeline.

Serialization scope (intentional, matching the Sprint 11U / 11W / 11X
/ 11Y / 11Z serialization discipline):

The lightweight decision-intelligence projections are persisted IN
FULL:

* :class:`DecisionIntelligenceContext` — context identity, the reused
  :class:`OpportunityProfile`, the read-only
  :class:`ExistingDecisionSummary` (embedded by value), the reused
  :class:`OpportunityEvidenceLookup` (embedded: profile, match status,
  matched spec, the matched :class:`HistoricalEvidenceCohort` and the
  :class:`StrategyEvidenceAssessment` with its embedded reused
  :class:`HistoricalPerformanceStatistics` / :class:`EvidenceStrength`
  / :class:`StrategyAssessmentStatus`), the
  :class:`DecisionContextStatus`, the factors, the surfaced
  observed-performance / evidence-strength / strategy-interpretation
  references (embedded by value), rationale, limitations, label,
  metadata.

The reused :class:`HistoricalPerformanceStatistics`,
:class:`HistoricalEvidenceCohort`, :class:`StrategyEvidenceAssessment`
and :class:`OpportunityEvidenceLookup` are embedded and reconstructed
exactly. The heavy per-outcome reference objects are NOT involved (the
decision-intelligence result is already a pure context over already-
computed evidence), so the round trip is LOSSLESS for every field the
decision-intelligence model carries.

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

from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionEvidenceFactor,
    DecisionIntelligenceContext,
    DecisionIntelligenceFactor,
    ExistingDecisionSummary,
)
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


#: Schema version for persisted decision-intelligence documents.
DECISION_INTELLIGENCE_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_context(context: DecisionIntelligenceContext) -> str:
    """Serialize a :class:`DecisionIntelligenceContext` to canonical JSON."""

    payload = {
        "schema_version": DECISION_INTELLIGENCE_SCHEMA_VERSION,
        "context": _to_json(context),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_context_bytes(context: DecisionIntelligenceContext) -> bytes:
    """Serialize a context to canonical JSON bytes."""

    return serialize_context(context).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_context(payload: str) -> DecisionIntelligenceContext:
    """Reconstruct a :class:`DecisionIntelligenceContext` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_json(parsed.get("context"), DecisionIntelligenceContext)


def parse_decision_intelligence_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted decision-intelligence
    document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _check_schema(parsed: Any) -> None:
    version = parsed.get("schema_version") if isinstance(parsed, dict) else None
    if version != DECISION_INTELLIGENCE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported decision-intelligence schema version "
            f"{version!r}; supported is "
            f"{DECISION_INTELLIGENCE_SCHEMA_VERSION}.",
        )


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_context_json(context: DecisionIntelligenceContext) -> str:
    """Canonical (sorted-key) JSON text for a context."""

    return serialize_context(context)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a decision-intelligence model value to JSON-safe form.

    Enums carry BOTH their member name AND their class name
    (``__enum_class__``) so the deserializer resolves the EXACT enum
    class unambiguously. This matters because several 12A enums share
    member names (e.g. ``DecisionContextStatus.EVIDENCE_UNAVAILABLE``
    and ``DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE``; and
    ``DecisionContextStatus.INSUFFICIENT_EVIDENCE`` vs
    ``StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE``). The class-
    qualified tag removes any ambiguity the heuristic decoder would
    otherwise get wrong.
    """

    if value is None:
        return None

    if isinstance(value, Enum):
        return {
            "__enum__": value.name,
            "__enum_class__": type(value).__name__,
        }

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
    "DecisionIntelligenceContext": DecisionIntelligenceContext,
    "DecisionIntelligenceFactor": DecisionIntelligenceFactor,
    "ExistingDecisionSummary": ExistingDecisionSummary,
    "OpportunityEvidenceLookup": OpportunityEvidenceLookup,
    "OpportunityProfile": OpportunityProfile,
    "StrategyEvidenceAssessment": StrategyEvidenceAssessment,
    "CohortComparison": CohortComparison,
    "CohortComparisonMetric": CohortComparisonMetric,
    "CohortSpec": CohortSpec,
    "HistoricalEvidenceCohort": HistoricalEvidenceCohort,
    "HistoricalPerformanceStatistics": HistoricalPerformanceStatistics,
}

# Mapping of enum name -> enum class.
_ENUMS = {
    "DecisionContextStatus": DecisionContextStatus,
    "DecisionEvidenceFactor": DecisionEvidenceFactor,
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
            cls_name = value.get("__enum_class__")
            if cls_name is not None:
                cls = _ENUMS.get(cls_name)
                if cls is expected_type:
                    return expected_type[value["__enum__"]]
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
        cls_name = raw.get("__enum_class__")
        if cls_name is not None:
            enum_cls = _ENUMS.get(cls_name)
            if enum_cls is not None:
                try:
                    return enum_cls[name]
                except KeyError:
                    pass
        # Backward-compatible fallback: try every enum class heuristically.
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
    "DECISION_INTELLIGENCE_SCHEMA_VERSION",
    "canonical_context_json",
    "deserialize_context",
    "parse_decision_intelligence_header",
    "serialize_context",
    "serialize_context_bytes",
]
