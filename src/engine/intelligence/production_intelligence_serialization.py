"""
Deterministic serialization for the PRODUCTION INTEGRATION + FINAL
HARDENING results (Sprint 12E).

The production intelligence context is persisted as a self-describing,
canonical JSON document so it can be reloaded later WITHOUT re-running
the Sprint 12B integration build, the Sprint 12A decision-intelligence
build, the Sprint 11Z lookup, the Sprint 11Y evidence classification,
the Sprint 11X analytics, the outcome evaluation, the pipeline, OR the
12C / 12D validation engines.

Serialization scope (intentional, matching the Sprint 11U / 11W / 11X /
11Y / 11Z / 12A / 12B serialization discipline):

The lightweight production projections are persisted IN FULL:

* :class:`ProductionIntelligenceContext` — production identity, the
  :class:`ProductionIntegrationStatus`, the
  :class:`ProductionValidationState`, the rationale / limitations /
  label / metadata,
* the reused Sprint 12B :class:`IntegratedDecisionContext` — embedded
  BY VALUE by delegating to the existing Sprint 12B serializer
  (:func:`serialize_integration` / :func:`deserialize_integration`),
  which itself embeds the reused 12A context + 11X / 11Y / 11Z models
  losslessly,
* the optional reused Sprint 12C
  :class:`BacktestValidationReport` — embedded BY VALUE by delegating
  to the existing Sprint 12C serializer
  (:func:`serialize_validation` / :func:`deserialize_validation`),
* the optional reused Sprint 12D
  :class:`RobustnessValidationReport` — embedded BY VALUE by delegating
  to the existing Sprint 12D serializer
  (:func:`serialize_robustness` / :func:`deserialize_robustness`).

The heavy ORIGINAL existing-decision reference object (carried by the
12B context) is retained BY REFERENCE on the LIVE result but is NOT
persisted; on reload it reconstructs as ``None`` (regenerable by the
caller), matching the 12B serialization discipline. The 12B
:attr:`existing_decision_summary` projection + integration id + status
+ decision-intelligence context are all persisted losslessly, so the
production audit view survives the round trip.

Every production enum carries a small type tag (``__enum__`` +
``__enum_class__``) so the deserializer reconstructs the EXACT model
type. The persisted text uses sorted keys so identical results always
produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() /
  memory addresses. No nondeterministic values in the ids.
* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.
* No global mutable state. Pure functions. The encoder / decoder
  delegates to the existing 12B / 12C / 12D serializers for every
  embedded model; the only local types are the two production enums.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from engine.intelligence.backtest_validation_serialization import (
    deserialize_validation,
    serialize_validation,
)
from engine.intelligence.decision_intelligence_integration_serialization import (
    deserialize_integration,
    serialize_integration,
)
from engine.intelligence.robustness_validation_serialization import (
    deserialize_robustness,
    serialize_robustness,
)
from engine.models.production_intelligence import (
    ProductionIntelligenceContext,
    ProductionIntegrationStatus,
    ProductionValidationState,
)


#: Schema version for persisted production documents.
PRODUCTION_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_production(context: ProductionIntelligenceContext) -> str:
    """Serialize a :class:`ProductionIntelligenceContext` to canonical JSON."""

    payload = {
        "schema_version": PRODUCTION_SCHEMA_VERSION,
        "production": _to_payload(context),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_production_bytes(context: ProductionIntelligenceContext) -> bytes:
    """Serialize a production context to canonical JSON bytes."""

    return serialize_production(context).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_production(payload: str) -> ProductionIntelligenceContext:
    """Reconstruct a :class:`ProductionIntelligenceContext` from JSON text."""

    parsed = json.loads(payload)
    _check_schema(parsed)
    return _from_payload(parsed.get("production"))


def parse_production_header(payload: str) -> dict[str, Any]:
    """
    Cheap header-only parse of a persisted production document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing any model.
    """

    return json.loads(payload)


def _check_schema(parsed: Any) -> None:
    version = parsed.get("schema_version") if isinstance(parsed, dict) else None
    if version != PRODUCTION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported production schema version {version!r}; "
            f"supported is {PRODUCTION_SCHEMA_VERSION}.",
        )


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_production_json(context: ProductionIntelligenceContext) -> str:
    """Canonical (sorted-key) JSON text for a production context."""

    return serialize_production(context)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================

#: Local enum map for the two production enums. Every embedded 12B /
#: 12C / 12D model is reconstructed by delegating to the corresponding
#: existing deserializer, so no dataclass map is needed here.
_ENUMS = {
    "ProductionIntegrationStatus": ProductionIntegrationStatus,
    "ProductionValidationState": ProductionValidationState,
}


def _to_payload(context: ProductionIntelligenceContext) -> dict[str, Any]:
    """
    Encode a :class:`ProductionIntelligenceContext` to a JSON-safe
    payload.

    The reused 12B integrated context, the 12C backtest validation
    report and the 12D robustness validation report are each embedded
    BY VALUE by delegating to their EXISTING serializers (which return
    self-describing canonical-JSON strings). Those strings are stored
    verbatim and re-parsed on load, so every embedded model is
    reconstructed by its OWN deserializer (no duplication of the 12B /
    12C / 12D reconstruction logic). The heavy original
    existing-decision reference reconstructs as ``None`` (inherited from
    the 12B discipline).
    """

    integrated_raw: str | None = None
    if context.integrated_context is not None:
        integrated_raw = serialize_integration(context.integrated_context)

    backtest_raw: str | None = None
    if context.backtest_validation is not None:
        backtest_raw = serialize_validation(context.backtest_validation)

    robustness_raw: str | None = None
    if context.robustness_validation is not None:
        robustness_raw = serialize_robustness(context.robustness_validation)

    return {
        "production_id": context.production_id,
        "integration_status": _enum_to_json(context.integration_status),
        "validation_state": _enum_to_json(context.validation_state),
        "integrated_context": integrated_raw,
        "backtest_validation": backtest_raw,
        "robustness_validation": robustness_raw,
        "rationale": context.rationale,
        "limitations": context.limitations,
        "label": context.label,
        "metadata": _tuple_to_json(context.metadata),
    }


def _enum_to_json(value: Enum | None) -> Any:
    if value is None:
        return None
    return {"__enum__": value.name, "__enum_class__": type(value).__name__}


def _tuple_to_json(value: tuple[tuple[str, str], ...]) -> Any:
    return {
        "__tuple__": [[list(p) for p in value]],
    }


def _decode_field(raw: Any) -> Any:
    """Decode a single production-level field from its JSON-safe form."""

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
        for enum_cls in _ENUMS.values():
            try:
                return enum_cls[name]
            except KeyError:
                continue
        return name

    if isinstance(raw, dict) and "__tuple__" in raw:
        items = raw["__tuple__"]
        if items and isinstance(items[0], list) and len(items[0]) == 2:
            # metadata: list of [k, v] pairs
            return tuple(
                (str(p[0]), str(p[1]))
                for p in items
                if isinstance(p, list) and len(p) == 2
            )
        return tuple(_decode_field(item) for item in items)

    return raw


def _from_payload(raw: Any) -> ProductionIntelligenceContext:
    """Reconstruct a :class:`ProductionIntelligenceContext` from its payload."""

    if not isinstance(raw, dict):
        raise ValueError("Malformed production payload: expected a mapping.")

    integrated_raw = raw.get("integrated_context")
    integrated_context = None
    if integrated_raw is not None:
        integrated_context = deserialize_integration(integrated_raw)

    backtest_raw = raw.get("backtest_validation")
    backtest_validation = None
    if backtest_raw is not None:
        backtest_validation = deserialize_validation(backtest_raw)

    robustness_raw = raw.get("robustness_validation")
    robustness_validation = None
    if robustness_raw is not None:
        robustness_validation = deserialize_robustness(robustness_raw)

    metadata = _decode_field(raw.get("metadata")) or ()

    return ProductionIntelligenceContext(
        production_id=raw["production_id"],
        integrated_context=integrated_context,
        backtest_validation=backtest_validation,
        robustness_validation=robustness_validation,
        integration_status=_decode_field(raw.get("integration_status"))
        or ProductionIntegrationStatus.INVALID,
        validation_state=_decode_field(raw.get("validation_state"))
        or ProductionValidationState.NONE,
        rationale=raw.get("rationale", ""),
        limitations=raw.get("limitations", ""),
        label=raw.get("label", ""),
        metadata=metadata,
    )


__all__ = [
    "PRODUCTION_SCHEMA_VERSION",
    "canonical_production_json",
    "deserialize_production",
    "parse_production_header",
    "serialize_production",
    "serialize_production_bytes",
]
