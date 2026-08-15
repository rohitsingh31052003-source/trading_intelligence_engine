"""
Deterministic serialization for the controlled decision intelligence
INTEGRATION results (Sprint 12B).

The integrated decision context is persisted as a self-describing,
canonical JSON document so it can be reloaded later WITHOUT re-running
the Sprint 12A decision-intelligence build, the Sprint 11Z lookup, the
Sprint 11Y evidence classification, the Sprint 11X analytics, the
outcome evaluation or the pipeline.

Serialization scope (intentional, matching the Sprint 11U / 11W / 11X /
11Y / 11Z / 12A serialization discipline):

The lightweight integration projections are persisted IN FULL:

* :class:`IntegratedDecisionContext` — integration identity, the
  :attr:`IntegrationStatus`, the read-only
  :class:`ExistingDecisionSummary` projection (embedded by value), the
  reused :class:`OpportunityProfile`, the reused Sprint 12A
  :class:`DecisionIntelligenceContext` (embedded by value: profile,
  match status, matched cohort, strategy assessment, reused statistics
  / strength / interpretation, 12A decision-context status, factors,
  rationale, limitations, label, metadata), the surfaced
  evidence-status / strategy-interpretation enums, the surfaced
  observed-performance / evidence-strength references (embedded by
  value), the contextual factors, rationale, limitations, label,
  metadata.

The heavy ORIGINAL existing-decision object (e.g. a Sprint 11S
:class:`TradeDecision` carrying candidate references) is retained BY
REFERENCE on the LIVE result but is NOT part of the persisted document;
on reload :attr:`IntegratedDecisionContext.existing_decision`
reconstructs as ``None`` (regenerable by the caller). The
:attr:`existing_decision_summary` projection preserves the audit view
losslessly, and the integration id / status / decision-intelligence
context are all persisted, so the round trip is LOSSLESS for every
field the integration audit view carries (identity, enums, decision
intelligence, integration status, factors, rationale).

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__tuple__``) so the deserializer
reconstructs the EXACT model type. Enums are stored by their stable
member name (plus class name for disambiguation, reusing the Sprint 12A
encoder discipline). The persisted text uses sorted keys so identical
results always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() /
  memory addresses. No nondeterministic values in the ids.
* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.
* No global mutable state. Pure functions. The encoder / decoder
  helpers are reused from the Sprint 12A serializer (which already
  handle every embedded 12A / 11X / 11Y / 11Z model); the 12B
  IntegrationStatus enum is added to the local enum map.
"""

from __future__ import annotations

import json
from typing import Any

from engine.intelligence.decision_intelligence_serialization import (
    _DATACLASSES as _DI_DATACLASSES,
)
from engine.intelligence.decision_intelligence_serialization import (
    _ENUMS as _DI_ENUMS,
)
from engine.intelligence.decision_intelligence_serialization import (
    _to_json,
)
from engine.models.decision_intelligence import (
    DecisionIntelligenceContext,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
    IntegrationStatus,
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


#: Schema version for persisted integration documents.
INTEGRATION_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_integration(context: IntegratedDecisionContext) -> str:
    """Serialize an :class:`IntegratedDecisionContext` to canonical JSON."""

    payload = {
        "schema_version": INTEGRATION_SCHEMA_VERSION,
        "integration": _to_payload(context),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_integration_bytes(context: IntegratedDecisionContext) -> bytes:
    """Serialize an integration context to canonical JSON bytes."""

    return serialize_integration(context).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_integration(payload: str) -> IntegratedDecisionContext:
    """Reconstruct an :class:`IntegratedDecisionContext` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_payload(parsed.get("integration"))


def parse_integration_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted integration document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _check_schema(parsed: Any) -> None:
    version = parsed.get("schema_version") if isinstance(parsed, dict) else None
    if version != INTEGRATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported integration schema version {version!r}; "
            f"supported is {INTEGRATION_SCHEMA_VERSION}.",
        )


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_integration_json(context: IntegratedDecisionContext) -> str:
    """Canonical (sorted-key) JSON text for an integration context."""

    return serialize_integration(context)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_payload(context: IntegratedDecisionContext) -> dict[str, Any]:
    """
    Encode an :class:`IntegratedDecisionContext` to a JSON-safe payload.

    The heavy original ``existing_decision`` reference object is NOT
    encoded (it reconstructs as ``None`` on load); the
    ``existing_decision_summary`` projection is encoded losslessly so
    the audit view survives the round trip. Every other field is
    encoded via the reused Sprint 12A :func:`_to_json` encoder, which
    tags enums (with class name) / dataclasses / tuples so the decoder
    reconstructs the EXACT model type.
    """

    return {
        "integration_id": context.integration_id,
        "existing_decision": None,  # heavy ref; regenerable by caller
        "existing_decision_summary": _to_json(context.existing_decision_summary),
        "profile": _to_json(context.profile),
        "decision_intelligence": _to_json(context.decision_intelligence),
        "integration_status": _to_json(context.integration_status),
        "evidence_status": _to_json(context.evidence_status),
        "strategy_interpretation": _to_json(context.strategy_interpretation),
        "observed_performance": _to_json(context.observed_performance),
        "evidence_strength": _to_json(context.evidence_strength),
        "contextual_factors": _to_json(context.contextual_factors),
        "rationale": context.rationale,
        "limitations": context.limitations,
        "label": context.label,
        "metadata": _to_json(context.metadata),
    }


# Local dataclass / enum maps = the Sprint 12A maps (which already cover
# every embedded 12A / 11X / 11Y / 11Z model) PLUS the 12B
# IntegrationStatus enum. The decoder resolves the EXACT model type from
# the class-qualified enum tag and the dataclass name tag.
_DATACLASSES = dict(_DI_DATACLASSES)
_ENUMS = {
    **_DI_ENUMS,
    "IntegrationStatus": IntegrationStatus,
}


def _decode_field(raw: Any) -> Any:
    """
    Decode a single field from its JSON-safe form.

    Mirrors the Sprint 12A :func:`_decode_field` but resolves enums via
    the local (extended) enum map so the 12B ``IntegrationStatus`` is
    reconstructed correctly alongside the reused 12A / 11Z enums.
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


def _from_payload(raw: Any) -> IntegratedDecisionContext:
    """Reconstruct an :class:`IntegratedDecisionContext` from its payload."""

    if not isinstance(raw, dict):
        raise ValueError("Malformed integration payload: expected a mapping.")
    return IntegratedDecisionContext(
        integration_id=raw["integration_id"],
        existing_decision=None,  # heavy ref; not persisted
        existing_decision_summary=_decode_field(raw.get("existing_decision_summary")),
        profile=_decode_field(raw.get("profile")),
        decision_intelligence=_decode_field(raw.get("decision_intelligence")),
        integration_status=_decode_field(raw.get("integration_status")),
        evidence_status=_decode_field(raw.get("evidence_status")),
        strategy_interpretation=_decode_field(raw.get("strategy_interpretation")),
        observed_performance=_decode_field(raw.get("observed_performance")),
        evidence_strength=_decode_field(raw.get("evidence_strength")),
        contextual_factors=_decode_field(raw.get("contextual_factors")) or (),
        rationale=raw.get("rationale", ""),
        limitations=raw.get("limitations", ""),
        label=raw.get("label", ""),
        metadata=_decode_field(raw.get("metadata")) or (),
    )


__all__ = [
    "INTEGRATION_SCHEMA_VERSION",
    "canonical_integration_json",
    "deserialize_integration",
    "parse_integration_header",
    "serialize_integration",
    "serialize_integration_bytes",
]
