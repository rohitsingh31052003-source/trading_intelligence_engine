"""
Execution Command model (Checkpoint 16.2).

An :class:`ExecutionCommand` is an immutable, broker-neutral snapshot of an
already-authorized :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`.
It represents the exact authorized command that may be handed to a future
execution adapter.

It is NOT a broker order, NOT a broker request, NOT a position, NOT a fill,
NOT an execution result, NOT an account/portfolio object, and NOT an
authorization artifact.

Design rules:

* Frozen + slots dataclass (matches the rest of the model layer).
* ``command_id`` is deterministic (``"cmd-" + sha256[:16]``) and derived from
  canonical command content. It does NOT depend on random UUIDs, object memory
  addresses, unordered dictionary serialization, current wall-clock time,
  ``datetime.now()``, or process state.
* The model binds explicitly to ``authorization_id``, ``intent_id``, and
  ``content_fingerprint``. The factory verifies
  ``authorization.intent_id == intent.intent_id`` and
  ``authorization.content_fingerprint == intent.content_fingerprint``.
* ``execution_mode`` is derived from the authorization's ``scope`` field and
  cannot be independently chosen by the caller.
* ``__post_init__`` validates internal consistency: required fields, direction,
  risk invariant (``planned_risk <= maximum_risk``), and execution mode
  validity.
* The factory performs fail-closed authorization verification: only an
  ``AUTHORIZED`` authorization with matching intent binding and content
  fingerprint may produce a command.
* No business logic lives here; the model is a data carrier. The factory
  performs validation and identity computation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
)
from engine.models.operational_trade_intent import (
    FINGERPRINT_PREFIX,
    INTENT_ID_PREFIX,
    OperationalTradeIntent,
    _canonical_value,
)


# ============================================================
# CONSTANTS
# ============================================================

#: Prefix for command_id.
COMMAND_ID_PREFIX = "cmd-"

#: Length of SHA-256 hex digest prefix used in identity strings.
_ID_DIGEST_LENGTH = 16

#: Schema/model version for ExecutionCommand.
EXECUTION_COMMAND_VERSION = 1


# ============================================================
# EXECUTION MODE
# ============================================================


class ExecutionMode(Enum):
    """
    Execution mode for an authorized command.

    The mode is derived from the authorization's ``scope`` field and cannot
    be independently chosen by the command factory caller.

    PAPER
        Simulation — no real orders. Derived from authorization scope
        ``"paper"`` (case-insensitive).

    LIVE
        Real trading — real orders. Derived from authorization scope
        ``"live"`` (case-insensitive).
    """

    PAPER = "PAPER"
    LIVE = "LIVE"


# ============================================================
# CANONICALIZATION
# ============================================================


def _canonical_command_payload(
    *,
    authorization_id: str,
    intent_id: str,
    content_fingerprint: str,
    instrument: str,
    direction: str,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
    quantity: Decimal | None,
    planned_risk: Decimal | None,
    maximum_risk: Decimal | None,
    execution_mode: ExecutionMode,
) -> dict[str, Any]:
    """Build the canonical identity payload for command_id generation.

    The identity captures the binding + authoritative economic content that
    defines the command. Operational metadata (timestamps, labels, metadata)
    is excluded so the identity remains stable across operational context
    changes that do not alter the authorized command content.
    """

    return {
        "authorization_id": _canonical_value(authorization_id),
        "intent_id": _canonical_value(intent_id),
        "content_fingerprint": _canonical_value(content_fingerprint),
        "instrument": _canonical_value(instrument),
        "direction": _canonical_value(direction),
        "entry": _canonical_value(entry),
        "stop": _canonical_value(stop),
        "target": _canonical_value(target),
        "quantity": _canonical_value(quantity),
        "planned_risk": _canonical_value(planned_risk),
        "maximum_risk": _canonical_value(maximum_risk),
        "execution_mode": _canonical_value(execution_mode),
    }


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


def _normalize_metadata(
    metadata: tuple[tuple[str, str], ...] | Any,
) -> tuple[tuple[str, str], ...]:
    """Normalize caller-supplied metadata to a sorted tuple of pairs."""

    if not metadata:
        return ()
    out: list[tuple[str, str]] = []
    for k, v in metadata:
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("metadata keys and values must be strings.")
        out.append((k, v))
    out.sort()
    return tuple(out)


def _derive_execution_mode_from_scope(scope: str) -> ExecutionMode:
    """Derive ExecutionMode from authorization scope.

    The scope is a descriptive string on the authorization. This function
    maps recognized scope values to the canonical ExecutionMode enum.

    Raises ValueError for unrecognized scope values (fail closed).
    """

    normalized = scope.strip().lower()
    if normalized == "paper":
        return ExecutionMode.PAPER
    if normalized == "live":
        return ExecutionMode.LIVE
    raise ValueError(
        f"Unrecognized authorization scope {scope!r}; "
        f"expected 'paper' or 'live'."
    )


# ============================================================
# MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    """
    An immutable, broker-neutral snapshot of an already-authorized intent.

    This artifact represents the exact authorized command that may be handed
    to a future execution adapter. It does NOT place orders, contact brokers,
    manage positions, or calculate P&L.

    Attributes:

    command_id
        Deterministic command identity (``"cmd-" + sha256[:16]``) derived
        from canonical command content. Two semantically identical commands
        produce the same identity.

    authorization_id
        The ``authorization_id`` of the authorizing
        :class:`~engine.models.execution_authorization.ExecutionAuthorization`.
        Must match ``authorization.authorization_id``.

    intent_id
        The ``intent_id`` of the authorized
        :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`.
        Must match ``intent.intent_id``.

    content_fingerprint
        The ``content_fingerprint`` of the authorized intent at authorization
        time. Must match ``intent.content_fingerprint``. Used to verify that
        the intent has not changed since authorization.

    instrument
        Canonical instrument name (copied verbatim from intent).

    direction
        Trade direction (``"LONG"`` / ``"SHORT"``), copied verbatim from
        intent. The command does NOT translate to broker BUY/SELL semantics.

    entry / stop / target
        Engine geometry levels copied VERBATIM from intent (``Decimal`` or
        ``None``). The command NEVER recomputes these.

    quantity
        Position quantity (``Decimal`` or ``None``). Copied verbatim from
        intent.

    planned_risk
        Maximum planned loss (``Decimal`` or ``None``). Copied verbatim.

    maximum_risk
        Risk limit bound (``Decimal`` or ``None``). Copied verbatim.

    execution_mode
        :class:`ExecutionMode` derived from the authorization's ``scope``
        field. PAPER for simulation; LIVE for real trading. The caller
        cannot independently choose a different mode.

    created_at
        Timezone-aware command creation timestamp. Caller-supplied; the
        model NEVER generates this silently.

    valid_from
        Timezone-aware command validity start, or ``None`` if not set.
        Caller-supplied.

    valid_until
        Timezone-aware command validity end, or ``None`` if not set.
        Caller-supplied.

    label / metadata
        Optional caller-supplied identity / metadata (audit trail).

    version
        Schema/model version (default 1). NOT a strategy version, NOT a
        broker version, NOT an execution version.
    """

    # Identity / binding
    command_id: str
    authorization_id: str
    intent_id: str
    content_fingerprint: str

    # Instrument / direction
    instrument: str
    direction: str

    # Geometry (copied verbatim from intent)
    entry: Decimal | None
    stop: Decimal | None
    target: Decimal | None

    # Position / risk (copied verbatim from intent)
    quantity: Decimal | None
    planned_risk: Decimal | None
    maximum_risk: Decimal | None

    # Execution mode (derived from authorization scope)
    execution_mode: ExecutionMode

    # Timestamps
    created_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    # Audit trail
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    version: int = EXECUTION_COMMAND_VERSION

    def __post_init__(self) -> None:
        """Validate internal consistency.

        The factory never produces inconsistent states; these checks guard
        against hand-construction bugs and enforce the fail-closed
        principle.
        """

        # Identity fields must be non-empty.
        if not self.command_id:
            raise ValueError("command_id must be non-empty.")
        if not self.command_id.startswith(COMMAND_ID_PREFIX):
            raise ValueError(
                f"command_id must start with {COMMAND_ID_PREFIX!r}."
            )
        if not self.authorization_id:
            raise ValueError("authorization_id must be non-empty.")
        if not self.intent_id:
            raise ValueError("intent_id must be non-empty.")
        if not self.content_fingerprint:
            raise ValueError("content_fingerprint must be non-empty.")
        if not self.content_fingerprint.startswith(FINGERPRINT_PREFIX):
            raise ValueError(
                f"content_fingerprint must start with {FINGERPRINT_PREFIX!r}."
            )

        # Instrument must be non-empty.
        if not self.instrument or not self.instrument.strip():
            raise ValueError("instrument must be non-empty.")

        # Direction must be a recognized value.
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(
                f"direction must be LONG or SHORT; got {self.direction!r}."
            )

        # Timestamps must be timezone-aware.
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
        if self.valid_from is not None and self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware.")
        if self.valid_until is not None and self.valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware.")

        # Timestamp relationships.
        if self.valid_from is not None and self.valid_from < self.created_at:
            raise ValueError("valid_from must be >= created_at.")
        if (
            self.valid_until is not None
            and self.valid_from is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("valid_until must be > valid_from.")

        # Risk invariant: planned_risk <= maximum_risk when both present.
        if (
            self.planned_risk is not None
            and self.maximum_risk is not None
            and self.planned_risk > self.maximum_risk
        ):
            raise ValueError(
                "planned_risk must not exceed maximum_risk "
                f"({self.planned_risk} > {self.maximum_risk})."
            )

        # Quantity must be positive when present.
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError(
                f"quantity must be positive when present; got {self.quantity!r}."
            )

        # Version validation.
        if self.version < 1:
            raise ValueError("version must be >= 1.")

    @property
    def is_paper(self) -> bool:
        """Whether this command is a paper (simulation) command."""

        return self.execution_mode is ExecutionMode.PAPER

    @property
    def is_live(self) -> bool:
        """Whether this command is a live (real trading) command."""

        return self.execution_mode is ExecutionMode.LIVE


# ============================================================
# FACTORY
# ============================================================


def create_execution_command(
    *,
    intent: OperationalTradeIntent,
    authorization: ExecutionAuthorization,
    created_at: datetime,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> ExecutionCommand:
    """Create an ExecutionCommand from an authorized OperationalTradeIntent.

    This is a pure, deterministic factory. It verifies the authorization
    binding, copies authoritative values by value, generates a deterministic
    ``command_id``, and does NOT mutate the intent or authorization, access
    trade geometry, access market data, invoke paper trading, or access any
    external system code.

    The ``execution_mode`` is derived from the authorization's ``scope``
    field (``"paper"`` → :attr:`ExecutionMode.PAPER`, ``"live"`` →
    :attr:`ExecutionMode.LIVE`). The caller cannot independently choose a
    different mode.

    Args:
        intent:
            The :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`
            being commanded. Consumed by value (fields extracted); never
            mutated.
        authorization:
            The :class:`~engine.models.execution_authorization.ExecutionAuthorization`
            granting permission for this intent. Must be in
            ``AUTHORIZED`` status with matching intent binding and content
            fingerprint.
        created_at:
            Timezone-aware command creation timestamp. Caller-supplied; the
            factory NEVER generates this silently.
        valid_from:
            Timezone-aware command validity start, or ``None`` to default
            to ``created_at``. Caller-supplied.
        valid_until:
            Timezone-aware command validity end, or ``None`` for no expiry.
            Caller-supplied.
        label:
            Optional caller-supplied identity label.
        metadata:
            Optional caller-supplied metadata tuple (sorted pairs).

    Returns:
        An immutable :class:`ExecutionCommand`.

    Raises:
        TypeError:
            If ``intent`` is not an :class:`OperationalTradeIntent` or
            ``authorization`` is not an :class:`ExecutionAuthorization`.
        ValueError:
            If the authorization is not ``AUTHORIZED``, the intent binding
            or content fingerprint does not match, the execution mode cannot
            be derived from the authorization scope, required fields are
            missing, or the risk invariant is violated.
    """

    # --- Type validation ---
    if not isinstance(intent, OperationalTradeIntent):
        raise TypeError(
            f"Expected an OperationalTradeIntent instance; "
            f"got {type(intent).__name__!r}."
        )
    if not isinstance(authorization, ExecutionAuthorization):
        raise TypeError(
            f"Expected an ExecutionAuthorization instance; "
            f"got {type(authorization).__name__!r}."
        )

    # --- Authorization state verification (fail closed) ---
    if authorization.status is not AuthorizationStatus.AUTHORIZED:
        raise ValueError(
            f"Authorization must be AUTHORIZED; got {authorization.status.value}."
        )

    # --- Intent binding verification ---
    if authorization.intent_id != intent.intent_id:
        raise ValueError(
            "Authorization intent_id mismatch: "
            f"authorization={authorization.intent_id!r}, "
            f"intent={intent.intent_id!r}."
        )

    # --- Content fingerprint verification ---
    if authorization.content_fingerprint != intent.content_fingerprint:
        raise ValueError(
            "Authorization content_fingerprint mismatch: "
            f"authorization={authorization.content_fingerprint!r}, "
            f"intent={intent.content_fingerprint!r}."
        )

    # --- Execution mode derivation from authorization scope ---
    try:
        execution_mode = _derive_execution_mode_from_scope(authorization.scope)
    except ValueError:
        raise ValueError(
            f"Cannot derive execution_mode from authorization scope {authorization.scope!r}; "
            f"expected 'paper' or 'live'."
        )

    # --- Normalize metadata ---
    normalized_metadata = _normalize_metadata(metadata)

    # --- Default validity window ---
    effective_valid_from = valid_from if valid_from is not None else created_at

    # --- Compute deterministic command_id ---
    identity_payload = _canonical_command_payload(
        authorization_id=authorization.authorization_id,
        intent_id=intent.intent_id,
        content_fingerprint=intent.content_fingerprint,
        instrument=intent.instrument,
        direction=intent.direction,
        entry=intent.entry,
        stop=intent.stop,
        target=intent.target_1,
        quantity=intent.quantity,
        planned_risk=intent.planned_risk,
        maximum_risk=intent.maximum_risk,
        execution_mode=execution_mode,
    )
    command_id = _sha256_prefix(identity_payload, COMMAND_ID_PREFIX)

    # --- Construct the immutable record ---
    return ExecutionCommand(
        command_id=command_id,
        authorization_id=authorization.authorization_id,
        intent_id=intent.intent_id,
        content_fingerprint=intent.content_fingerprint,
        instrument=intent.instrument,
        direction=intent.direction,
        entry=intent.entry,
        stop=intent.stop,
        target=intent.target_1,
        quantity=intent.quantity,
        planned_risk=intent.planned_risk,
        maximum_risk=intent.maximum_risk,
        execution_mode=execution_mode,
        created_at=created_at,
        valid_from=effective_valid_from,
        valid_until=valid_until,
        label=label,
        metadata=normalized_metadata,
        version=EXECUTION_COMMAND_VERSION,
    )


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "COMMAND_ID_PREFIX",
    "EXECUTION_COMMAND_VERSION",
    "ExecutionCommand",
    "ExecutionMode",
    "create_execution_command",
]
