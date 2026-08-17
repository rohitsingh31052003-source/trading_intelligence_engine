"""
Deterministic serialization for trade plans (Product Phase 4).

The trade plan is persisted as a self-describing, canonical JSON document
so it can be reloaded later WITHOUT recomputing the risk calculation.

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__decimal__`` / ``__tuple__``) so
the deserializer reconstructs the EXACT model type. Enums are stored by
their stable member name. ``Decimal`` values are stored as strings so
monetary precision is preserved across the round trip (binary
floating-point is never used for money). The persisted text uses sorted
keys so identical plans always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No nondeterministic values in the plan id.

* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.

* No ``pickle`` / ``eval`` / ``exec``. Only ``json`` + ``Decimal``.

* No global mutable state. Pure functions.

The round trip is LOSSLESS for every audit-relevant field: plan_id,
instrument, timeframe, direction, existing decision, actionability,
account_capital, risk_percent, maximum_risk, entry / stop / target_1,
engine_risk_distance / engine_reward_distance / engine_risk_reward_ratio,
target_2 (always None) / target_2_supported (always False), quantity,
planned_risk, planned_reward, quantity_status, risk_plan_status,
quantity_spec_available, warnings, rationale, label, metadata.
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.trade_plan import (
    QuantityStatus,
    RiskPlanStatus,
    TradePlan,
)


#: Schema version for persisted trade-plan documents.
TRADE_PLAN_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_trade_plan(plan: TradePlan) -> str:
    """Serialize a :class:`TradePlan` to canonical JSON text."""

    payload = {
        "schema_version": TRADE_PLAN_SCHEMA_VERSION,
        "plan": _to_json(plan),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_trade_plan_bytes(plan: TradePlan) -> bytes:
    """Serialize a trade plan to canonical JSON bytes."""

    return serialize_trade_plan(plan).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_trade_plan(payload: str) -> TradePlan:
    """Reconstruct a :class:`TradePlan` from JSON text.

    Raises ``ValueError`` for an unsupported schema version, malformed
    JSON, or a missing ``plan`` key.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed trade-plan JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Malformed trade-plan payload: expected a JSON object.")
    version = parsed.get("schema_version")
    if version != TRADE_PLAN_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported trade-plan schema version {version!r}; "
            f"supported is {TRADE_PLAN_SCHEMA_VERSION}.",
        )
    if "plan" not in parsed:
        raise ValueError("Malformed trade-plan payload: missing 'plan' key.")
    return _from_json(parsed.get("plan"), TradePlan)


def parse_trade_plan_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse of a persisted trade-plan document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing the model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_trade_plan_json(plan: TradePlan) -> str:
    """Canonical (sorted-key) JSON text for a trade plan."""

    return serialize_trade_plan(plan)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a trade-plan model value to JSON-safe form."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
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
_DATACLASSES = {
    "TradePlan": TradePlan,
}

# Mapping of enum name -> enum class. Both enums have distinct member
# names so a single lookup by name is unambiguous; we still try each in
# order for robustness.
_ENUMS = {
    "RiskPlanStatus": RiskPlanStatus,
    "QuantityStatus": QuantityStatus,
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
    if expected_type in (tuple, list, str, int, float, bool):
        if expected_type in (tuple, list):
            if isinstance(value, dict) and "__tuple__" in value:
                return tuple(value["__tuple__"])
            return tuple(value) if isinstance(value, list) else value
        return value
    return value


def _decode_field(raw: Any) -> Any:
    """Decode a single dataclass field from its JSON-safe form."""

    if raw is None:
        return None
    if isinstance(raw, dict) and "__decimal__" in raw:
        return Decimal(raw["__decimal__"])
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
    "TRADE_PLAN_SCHEMA_VERSION",
    "canonical_trade_plan_json",
    "deserialize_trade_plan",
    "parse_trade_plan_header",
    "serialize_trade_plan",
    "serialize_trade_plan_bytes",
]
