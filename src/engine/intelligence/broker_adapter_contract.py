"""
Broker-neutral Broker Adapter contract (Checkpoint 17.2 Phases 2/7/10/11).

This module defines the abstract broker-neutral adapter contract that a
future broker-specific adapter implements without contaminating the core
Trading Intelligence Engine.

The contract is NOT an authorization authority:

* It never decides whether a trade is authorized.
* It never creates authorization.
* It never overrides authorization.
* It never changes risk decisions.
* It never generates trading signals.
* It never modifies trade plans.
* It never reinterprets strategy semantics.
* It never makes discretionary trading decisions.

Authorization occurs BEFORE the adapter boundary. The adapter consumes
an ALREADY-AUTHORIZED, IMMUTABLE
:class:`~engine.models.execution_command.ExecutionCommand` (or an invariant
immutable projection thereof) and reports typed broker-neutral results via
:class:`~engine.models.broker_adapter.AdapterResult`.

Design rules:

* The adapter implements at minimum ``submit`` and ``reconcile``.
* It exposes its supported capabilities via ``supported_capabilities`` and its
  bound execution mode via ``execution_mode`` so the calling layer can verify
  paper/live isolation BEFORE any operation.
* ``submit``:
    - Raises ``ValueError``/``TypeError`` on deterministic pre-submission
      rejection (never returns a fake success). Callers may treat these
      as validation failures (see ``BrokerErrorCode.VALIDATION_FAILURE``).
    - Returns ``AdapterResult`` with a typed status + error taxonomy.
    - NEVER treats a timeout/lost response as a definitive failure — it
      returns ``UNKNOWN`` so reconciliation is required.
* ``reconcile``:
    - Queries the broker for an existing order using ONLY the deterministic
      ``client_order_id`` identity.
    - Returns an ``AdapterResult`` (``ACCEPTED``/``FILLED``/``REJECTED``/
      ``UNKNOWN`` etc.) normalized at the boundary.
* ``cancel``:
    - Optional (see ``AdapterCapability.CANCEL``). Requests cancellation; a
      timeout -> ``UNKNOWN`` (reconciliation still governs retry).
* Idempotency: deterministic ``client_order_id``/``idempotency key`` derived
  from ``command_id`` + broker context. The broker-neutral layer does NOT
  pretend these alone guarantee broker-side deduplication — broker-specific
  idempotency semantics are documented as a limitation and the adapter
  must use broker-side mechanisms where available.
* Mode isolation:
    - A paper-authorized command must NEVER silently reach a live adapter.
    - A live-authorized command must NEVER silently be routed to a paper
      adapter. Adapter factories select/verify mode from the command's
      ``execution_mode`` (never silently overridden).

No real broker is defined here; this module has no network, no credentials,
no broker SDK, no specific-broker reference, no product codes, no order types,
no exchange/routing assumptions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from engine.models.broker_adapter import AdapterCapability, AdapterResult
from engine.models.execution_command import ExecutionCommand, ExecutionMode


#: Prefix for deterministic client order ids.
CLIENT_ORDER_ID_PREFIX = "co-"
#: Prefix for deterministic idempotency keys.
IDEMPOTENCY_KEY_PREFIX = "idem-"
#: Length of SHA-256 hex digest prefix used in identity strings.
_ID_DIGEST_LENGTH = 16


# ============================================================
# CAPABILITY / ERROR BEHAVIOR
# ============================================================


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """The adapter.s declared broker-neutral capabilities + execution mode.


    Attributes:
        capabilities:
            Tuple of :class:`AdapterCapability` members supported by this
            adapter (must include at least ``SUBMIT`` and``RECONCILE``).
        execution_mode:
            The :class:`ExecutionMode` this adapter is bound to. NEVER
            silently overridden..
    """

    capabilities: tuple[AdapterCapability, ...]
    execution_mode: ExecutionMode

    def __post_init__(self) -> None:
        for cap in self.capabilities:
            if not isinstance(cap, AdapterCapability):
                raise TypeError(
                    f"capabilities must contain AdapterCapability members; "
                    f"got {type(cap).__name__!r}."
                )
        if AdapterCapability.SUBMIT not in self.capabilities:
            raise ValueError("An adapter must support SUBMIT.")
        if AdapterCapability.RECONCILE not in self.capabilities:
            raise ValueError("An adapter must support RECONCILE.")
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError(
                f"execution_mode must be an ExecutionMode; "
                f"got {type(self.execution_mode).__name__!r}."
            )

    @property
    def supports_cancel(self) -> bool:
        """Whether the adapter supports the cancel operation."""
        return AdapterCapability.CANCEL in self.capabilities

    def grant(self, cap: AdapterCapability) -> bool:
        """Whether the adapter supports ``cap``."""
        return cap in self.capabilities


# ============================================================
# CLIENT ORDER IDENTITY
# ============================================================


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


def derive_client_order_id(
    *,
    command_id: str,
    broker_context: str = "default",
) -> str:
    """Derive the deterministic broker-facing client order id.

    The id:

    * Is deterministic — same ``command_id`` + same broker context produce
      the same id across process restarts.
    * Is derived from IMMUTABLE command identity (no random process state,
      no broker response dependence, no wall-clock impact).
    * Does not expose unnecessary internal information.
    * Is the SAME for every submission of the same command (so retries
      reuse it and the broker can dedupe).
    * Different broker contexts produce different ids (a live adapter and a
      paper adapter never share a client order id).

    Note: this deterministic application-level id does NOT by itself
    guarantee broker-side idempotency. The broker-facing adapter owns the
    actual idempotency mechanism and must use broker-side mechanisms where
    available.
    """

    payload = {
        "command_id": command_id,
        "broker_context": broker_context,
    }
    return _sha256_prefix(payload, CLIENT_ORDER_ID_PREFIX)


def derive_idempotency_key(
    *,
    command_id: str,
    broker_context: str = "default",
) -> str:
    """Derive a deterministic idempotency key for broker-side deduplication.

    See :func:`derive_client_order_id` for the identity contract. The key is
    deliberately a SEPARATE string so brokers that require a distinct key
    field can use it without exposing a raw canonical payload.
    """

    payload = {
        "command_id": command_id,
        "broker_context": broker_context,
    }
    return _sha256_prefix(payload, IDEMPOTENCY_KEY_PREFIX)


def validate_adapter_mode(
    *,
    adapter_execution_mode: ExecutionMode,
    command: ExecutionCommand,
) -> None:
    """Fail-closed mode verification: adapter mode vs command execution mode.


    Raises:
        ValueError: If the adapter's bound execution mode differs from the
        command's ``execution_mode`` — a paper/live boundary violation that
        must NEVER be silently crossed.. The adapter/handler is responsible for
        calling this BEFORE any submit/reconcile/cancel operation..
    """

    if not isinstance(adapter_execution_mode, ExecutionMode):
        raise TypeError(
            f"adapter_execution_mode must be an ExecutionMode; "
            f"got {type(adapter_execution_mode).__name__!r}."
        )
    if not isinstance(command, ExecutionCommand):
        raise TypeError(
            f"command must be an ExecutionCommand; "
            f"got {type(command).__name__!r}."
        )
    if adapter_execution_mode is not command.execution_mode:
        raise ValueError(
            f"Execution-mode mismatch: adapter is bound to "
            f"{adapter_execution_mode.value} but command {command.command_id!r} "
            f"is {command.execution_mode.value}. Paper/live modes must never "
            f"silently cross."
        )


# ============================================================
# BROKER ADAPTER PROTOCOL
# ============================================================


@runtime_checkable
class BrokerAdapter(Protocol):
    """Broker-neutral adapter protocol a broker-specific adapter implements.



    Implementations:

    * MUST be broker-specific translation layers ONLY.
    * MUST expose deterministic :attr:`capabilities` (with SUBMIT + RECONCILE).
    * MUST NOT authorize, create commands, own planning, own portfolio,
      mutate upstream artifacts, or silently alter authorized economic meaning..
    * MUST normalize broker-specific failures to the broker-neutral
      :class:`~engine.models.broker_adapter.AdapterResult` / error taxonomy
      at the boundary (no broker-specific exception classes leak upward).
    * MUST treat timeout / lost-response via ``UNKNOWN`` (reconcile before retry)..
    * MUST accept ONLY already-authorized, immutable commands (the adapter
      has zero authorization authority..
    * MUST expose its bound execution mode via ``execution_mode`` so the
      caller can verify paper/live isolation BEFORE any operation..
    """


    capabilities: AdapterCapabilities
    execution_mode: ExecutionMode

    def submit(self, command: ExecutionCommand) -> AdapterResult:
        """Submit an already-authorized command.

        Returns a typed :class:`AdapterResult`. Raises ``TypeError``/``ValueError``
        on deterministic pre-submission rejection.
        """

    def reconcile(self, client_order_id: str) -> AdapterResult:
        """Reconcile an earlier submission by its deterministic client order id.

        Returns a typed :class:`AdapterResult` (``ACCEPTED`` / ``REJECTED`` /
        ``FILLED`` / ``UNKNOWN`` etc.) normalized at the boundary; no
        broker-specific types escape.
        """

    def cancel(self, client_order_id: str) -> AdapterResult:
        """Optionally request cancellation of an in-flight submission.

        Raise ``ValueError`` when :attr:`capabilities` does not include
        :attr:`AdapterCapability.CANCEL`.
        """

    def supports(self, command: ExecutionCommand) -> bool:
        """Whether this adapter supports the command's capabilities.

        Returns ``False`` (never raises) for an unsupported instrument,
        unsupported order semantics, unsupported quantity constraints, or
        unsupported execution behavior — the capability boundary (Phase 11).
        """

    def check(self, command: ExecutionCommand) -> None:
        """Optionally pre-validate a command against broker constraints.

        Raise ``ValueError``/``TypeError`` for a deterministic pre-submission
        rejection (unsupported instrument / order semantics / quantity
        constraints / execution behavior). Never mutates the command.
        """


# ============================================================
# ADAPTER SELECTION FACTORY
# ============================================================


def select_adapter(
    adapters: dict[str, BrokerAdapter],
    command: ExecutionCommand,
    *,
    preferred: str | None = None,
) -> BrokerAdapter:
    """Deterministically select a compatible broker adapter for a command.



    The selection rule is fail-closed:

    1. If ``adapters`` is empty → ``ValueError``.

    2. If a preferred adapter name is supplied it must exist and its mode must
       match the command — otherwise ``ValueError`` (no silent substitution)..
    3. If no preferred adapter is supplied the adapters matching the command's
       ``execution_mode`` are considered. The names of matching adapters are
       sorted lexicographically and the FIRST (smallest name) is selected —
       deterministic, no randomness, no unordered-dict-iteration influence..
    4. If no adapter matches the command's mode → ``ValueError`` (a
       paper-authorized command must never silently reach a live adapter and
       vice versa)..
    """

    if not isinstance(command, ExecutionCommand):
        raise TypeError(
            f"command must be an ExecutionCommand; "
            f"got {type(command).__name__!r}."
        )
    if not adapters:
        raise ValueError("No adapters are registered.")

    if preferred is not None:
        if preferred not in adapters:
            raise ValueError(
                f"Preferred adapter {preferred!r} is not registered; "
                f"registered adapters are {sorted(adapters)!r}."
            )
        candidate = adapters[preferred]
        if not isinstance(candidate, BrokerAdapter):
            raise TypeError(
                f"Adapter {preferred!r} does not implement the BrokerAdapter "
                f"contract; got {type(candidate).__name__!r}."
            )
        validate_adapter_mode(
            adapter_execution_mode=candidate.execution_mode, command=command
        )
        return candidate

    matches = sorted(
        (name for name, adapter in adapters.items()
         if isinstance(adapter, BrokerAdapter)
         and adapter.execution_mode is command.execution_mode),
        )
    if not matches:
        raise ValueError(
            f"No adapter matches command mode {command.execution_mode.value}; "
            f"registered modes are "
            f"{sorted({a.execution_mode.value for a in adapters.values()})!r}."
        )
    return adapters[matches[0]]


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "CLIENT_ORDER_ID_PREFIX",
    "IDEMPOTENCY_KEY_PREFIX",
    "AdapterCapabilities",
    "BrokerAdapter",
    "derive_client_order_id",
    "derive_idempotency_key",
    "select_adapter",
    "validate_adapter_mode",
]