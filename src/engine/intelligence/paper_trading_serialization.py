"""
Deterministic serialization for paper trades (Product Phase 5).

A paper trade is persisted as a self-describing, canonical JSON document
so it can be reloaded later WITHOUT recomputing the lifecycle /
accounting. This is the persistence format used by the dashboard
paper-trade store (``dashboard/paper_trade_store.py``).

Every non-primitive value carries a small type tag
(``__enum__`` / ``__dataclass__`` / ``__decimal__`` / ``__datetime__`` /
``__tuple__``) so the deserializer reconstructs the EXACT model type.
Enums are stored by their stable member name. ``Decimal`` values are
stored as strings so monetary precision is preserved across the round
trip (binary floating-point is never used for money). ``datetime``
values are stored as ISO-8601 strings. The persisted text uses sorted
keys so identical paper trades always produce identical bytes.

Design rules:

* Deterministic. Sorted keys, stable value encoding. No repr() / memory
  addresses. No nondeterministic values in the paper-trade id.
* Self-describing. The schema version is written at the top of every
  document and checked by the loader before any model reconstruction.
* No ``pickle`` / ``eval`` / ``exec``. Only ``json`` + ``Decimal`` +
  ``datetime``.
* No global mutable state. Pure functions.

The round trip is LOSSLESS for every audit-relevant field: paper_trade_id,
instrument, timeframe, direction, existing decision, setup type, plan id,
created_at, evaluation timestamp, entry / stop / target_1, target_2
(always None) / target_2_supported (always False), engine risk / reward /
R:R, planned quantity / planned risk / maximum risk / account capital /
risk percent, status, entry timestamp / actual entry price, exit
timestamp / actual exit price / exit reason, realized R / realized P&L,
label, metadata, sequence.
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.paper_trade import (
    PaperExitReason,
    PaperTrade,
    PaperTradeStatus,
)


#: Schema version for persisted paper-trade documents.
PAPER_TRADE_SCHEMA_VERSION = 1


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_paper_trade(trade: PaperTrade) -> str:
    """Serialize a :class:`PaperTrade` to canonical JSON text."""

    payload = {
        "schema_version": PAPER_TRADE_SCHEMA_VERSION,
        "paper_trade": _to_json(trade),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def serialize_paper_trade_bytes(trade: PaperTrade) -> bytes:
    """Serialize a paper trade to canonical JSON bytes."""

    return serialize_paper_trade(trade).encode("utf-8")


# ============================================================
# DESERIALIZATION
# ============================================================


def deserialize_paper_trade(payload: str) -> PaperTrade:
    """Reconstruct a :class:`PaperTrade` from JSON text.

    Raises ``ValueError`` for an unsupported schema version, malformed
    JSON, or a missing ``paper_trade`` key.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed paper-trade JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Malformed paper-trade payload: expected a JSON object.")
    version = parsed.get("schema_version")
    if version != PAPER_TRADE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported paper-trade schema version {version!r}; "
            f"supported is {PAPER_TRADE_SCHEMA_VERSION}.",
        )
    if "paper_trade" not in parsed:
        raise ValueError("Malformed paper-trade payload: missing 'paper_trade' key.")
    return _from_json(parsed.get("paper_trade"), PaperTrade)


def parse_paper_trade_header(payload: str) -> dict[str, Any]:
    """Cheap header-only parse of a persisted paper-trade document.

    Returns the parsed mapping. The caller is expected to inspect the
    ``schema_version`` before reconstructing the model.
    """

    return json.loads(payload)


# ============================================================
# CANONICAL JSON (for audit / identity comparison)
# ============================================================


def canonical_paper_trade_json(trade: PaperTrade) -> str:
    """Canonical (sorted-key) JSON text for a paper trade."""

    return serialize_paper_trade(trade)


# ============================================================
# ENCODE / DECODE HELPERS
# ============================================================


def _to_json(value: Any) -> Any:
    """Recursively encode a paper-trade model value to JSON-safe form."""

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
_DATACLASSES = {
    "PaperTrade": PaperTrade,
}

# Mapping of enum name -> enum class. The two enums have distinct member
# names so a single lookup by name is unambiguous; we still try each in
# order for robustness.
_ENUMS = {
    "PaperTradeStatus": PaperTradeStatus,
    "PaperExitReason": PaperExitReason,
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
        kwargs = {}
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
        kwargs = {}
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
    "PAPER_TRADE_SCHEMA_VERSION",
    "canonical_paper_trade_json",
    "deserialize_paper_trade",
    "parse_paper_trade_header",
    "serialize_paper_trade",
    "serialize_paper_trade_bytes",
]
