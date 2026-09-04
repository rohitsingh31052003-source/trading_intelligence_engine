"""Reference / simulated broker adapter (Checkpoint 17.4).

This module provides the FIRST CONCRETE :class:`BrokerAdapter` implementation
against the frozen broker-neutral contract (Checkpoint 17.2). It is a
REFERENCE / SIMULATED / TEST-SAFE adapter:

* It NEVER connects to any real broker (see the strict non-goals in the
  Checkpoint 17.4 brief: no real-broker SDK, no broker API calls, no broker
  credentials, no live order submission).
* It NEVER makes network requests (no HTTP, no sockets, no WebSockets).
* It NEVER requires credentials / API keys / bearer tokens.
* It NEVER submits real orders.
* It is safe to execute in an offline CI environment.

Its purpose is to PROVE that a concrete adapter can implement the
broker-neutral contract while keeping all broker-specific / request-specific
translation and behavior isolated behind the adapter boundary.

CORE INVARIANT (Checkpoint 17.4):

* The generic :class:`BrokerAdapter` contract defines WHAT the execution
  system needs.
* The concrete adapter defines HOW a particular broker-like execution
  environment represents that request.
* Broker-specific semantics NEVER move upward into the generic contract.

ADAPTER-OWNED TRANSLATION:

The reference adapter owns a deliberately simple deterministic
representation (``ReferenceBrokerRequest`` / ``ReferenceBrokerResponse``) that
the generic execution system never needs to understand::

    ExecutionCommand
            |   (adapter-owned translation)
            v
    ReferenceBrokerAdapter
            |   (adapter-owned request)
            v
    ReferenceBrokerRequest
            |   (adapter-owned simulation)
            v
    ReferenceBrokerResponse
            |   (adapter-owned normalization)
            v
    AdapterResult   (broker-neutral)

The translation boundaries are isolated inside this module:

* instrument/symbol representation  -> ``REFERENCE_EXCHANGE:INSTRUMENT``
* exchange representation           -> ``REFERENCE_EXCHANGE``
* order type                        -> LONG -> BUY / SHORT -> SELL
* product/variety                   -> ``REFERENCE_PRODUCT``
* quantity representation           -> verbatim ``Decimal`` (never increased)
* price representation              -> verbatim ``Decimal`` (never altered)
* client_order_id                   -> deterministic (17.2 ``derive_client_order_id``)
* idempotency key                   -> deterministic (17.2 ``derive_idempotency_key``)

RESULT / ERROR NORMALIZATION:

Every internal / simulated outcome is normalized at the boundary into the
existing broker-neutral :class:`AdapterResult` / :class:`BrokerError`
taxonomy. The core system NEVER receives reference-adapter-specific
exceptions or raw reference response objects.

EXECUTION MODE BINDING:

The adapter is explicitly bound to one :class:`ExecutionMode` (PAPER or
LIVE). It uses the existing ``validate_adapter_mode`` / ``select_adapter``
infrastructure rather than a parallel mode-selection system. A paper command
can never silently reach a live adapter and vice versa.

IDEMPOTENCY:

The adapter uses the existing deterministic identity mechanisms from
Checkpoint 17.2 (``derive_client_order_id`` / ``derive_idempotency_key``).
It NEVER generates random client identities and NEVER generates a new client
identity per ``submit()`` call. It additionally demonstrates ADAPTER /
REFERENCE-BROKER duplicate detection: submitting the same ``client_order_id``
twice (through a direct adapter call) reports ``broker_status="duplicate"``.
This does NOT claim a real broker's idempotency guarantees.

RECONCILIATION:

The adapter exercises the existing reconcile contract: ``submit()`` ->
ambiguous result -> ``UNKNOWN`` -> ``reconcile()`` -> confirmed result. An
unresolved outcome is never converted into a false failure, and blind retry
through reconciliation is prohibited by the frozen contract.

DETERMINISTIC SCENARIOS:

The adapter supports deterministic scenario injection (see
:data:`REFERENCE_ADAPTER_SCENARIOS`) sufficient to prove the generic
contract: accepted, rejected, failed, timeout, unknown, reconcile accepted,
reconcile rejected, reconcile unknown, duplicate, unsupported operation,
unsupported instrument, unsupported order type, validation failure,
malformed internal, cancelled, filled, partially filled, restart.

No real broker is defined here; this module has no network, no credentials,
no broker SDK, no specific-broker reference, no product codes from any real
broker, no exchange/routing assumptions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from engine.intelligence.broker_adapter_contract import (
    AdapterCapabilities,
    BrokerAdapter,
    derive_client_order_id,
    derive_idempotency_key,
    validate_adapter_mode,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerError,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionCommand, ExecutionMode

#: Prefix for deterministic reference-broker order ids.
REFERENCE_BROKER_ORDER_ID_PREFIX = "refbrk-"

#: Length of SHA-256 hex digest prefix used in identity strings.
_ID_DIGEST_LENGTH = 16

#: The reference exchange identifier (a deliberately generic placeholder).
REFERENCE_EXCHANGE = "REF"

#: The reference product / variety (a deliberately generic placeholder).
REFERENCE_PRODUCT = "REF-CASH"

#: Deterministic direction -> order-type mapping (adapter-owned).
_DIRECTION_TO_ORDER_TYPE: dict[str, str] = {
    "LONG": "BUY",
    "SHORT": "SELL",
}


#: Recognized deterministic reference-adapter submit/reconcile/cancel scenarios.
REFERENCE_ADAPTER_SCENARIOS: tuple[str, ...] = (
    "accepted",
    "rejected",
    "failed",
    "timeout",
    "unknown",
    "reconcile_accepted",
    "reconcile_rejected",
    "reconcile_unknown",
    "duplicate",
    "restart",
    "cancelled",
    "filled",
    "partially_filled",
    "unsupported_operation",
    "unsupported_instrument",
    "unsupported_order_type",
    "validation_failure",
    "malformed_internal",
)

#: Internal response statuses the reference simulation can emit.
_REFERENCE_STATUSES: tuple[str, ...] = (
    "accepted",
    "rejected",
    "failed",
    "timeout",
    "unknown",
    "submitted",
    "cancelled",
    "filled",
    "partially_filled",
    "duplicate",
)

#: Internal error kinds the reference simulation can attach to a response.
_REFERENCE_ERROR_KINDS: tuple[str, ...] = (
    "validation",
    "unsupported_operation",
    "unsupported_instrument",
    "unsupported_order_type",
    "broker_rejection",
    "timeout",
    "unknown_outcome",
    "malformed_response",
    "internal",
)

#: Response statuses that record an order in the reference order book.
_ORDER_RECORDING_STATUSES: frozenset[str] = frozenset(
    {
        "accepted",
        "submitted",
        "cancelled",
        "filled",
        "partially_filled",
        "duplicate",
        "restart",
    }
)


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


def derive_reference_broker_order_id(
    *,
    client_order_id: str,
    operation: str,
    scenario: str,
) -> str:
    """Derive a deterministic reference-broker order id.

    A real broker generates its own order id; this helper documents the
    deterministic contract the reference simulation relies on so tests can
    assert broker-order-id stability without randomness.
    """

    payload = {
        "client_order_id": client_order_id,
        "operation": operation,
        "scenario": scenario,
    }
    return _sha256_prefix(payload, REFERENCE_BROKER_ORDER_ID_PREFIX)


# ============================================================
# ADAPTER-OWNED REQUEST MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class ReferenceBrokerRequest:
    """Adapter-owned broker request representation (reference simulation).

    This model is ISOLATED from the core domain models. It does NOT leak
    into :class:`ExecutionCommand`, Trading Intelligence, or Authorization,
    and it is NOT part of the generic :class:`BrokerAdapter` contract. It
    contains only the information the simulated adapter needs.

    Attributes:
        symbol:
            Adapter-owned instrument/symbol representation
            (``REF:<INSTRUMENT>``).
        exchange:
            Adapter-owned exchange representation (``REF``).
        order_type:
            Adapter-owned order type (``BUY`` / ``SELL`` derived from
            direction).
        product:
            Adapter-owned product / variety (``REF-CASH``).
        quantity:
            Verbatim authorized quantity (``Decimal`` or ``None``). Never
            increased.
        price:
            Verbatim authorized entry price (``Decimal`` or ``None``). Never
            altered.
        stop_price / target_price:
            Verbatim authorized stop / target levels (``Decimal`` or
            ``None``).
        client_order_id:
            Deterministic broker-facing client order id (17.2 identity).
        idempotency_key:
            Deterministic broker-facing idempotency key (17.2 identity).
        execution_mode:
            The bound execution mode name (``PAPER`` / ``LIVE``).
        created_at:
            The command creation timestamp (caller-supplied upstream).
    """

    symbol: str
    exchange: str
    order_type: str
    product: str
    quantity: Decimal | None
    price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    client_order_id: str
    idempotency_key: str
    execution_mode: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if not self.exchange or not self.exchange.strip():
            raise ValueError("exchange must be a non-empty string.")
        if not self.order_type or not self.order_type.strip():
            raise ValueError("order_type must be a non-empty string.")
        if not self.product or not self.product.strip():
            raise ValueError("product must be a non-empty string.")
        for name in ("quantity", "price", "stop_price", "target_price"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(
                    f"{name} must be a Decimal or None; "
                    f"got {type(value).__name__!r}."
                )
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id must be a non-empty string.")
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")
        if self.execution_mode not in ("PAPER", "LIVE"):
            raise ValueError(
                f"execution_mode must be 'PAPER' or 'LIVE'; "
                f"got {self.execution_mode!r}."
            )
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware when present.")

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection (Decimal -> str)."""

        def _num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "order_type": self.order_type,
            "product": self.product,
            "quantity": _num(self.quantity),
            "price": _num(self.price),
            "stop_price": _num(self.stop_price),
            "target_price": _num(self.target_price),
            "client_order_id": self.client_order_id,
            "idempotency_key": self.idempotency_key,
            "execution_mode": self.execution_mode,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }


# ============================================================
# ADAPTER-OWNED RESPONSE MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class ReferenceBrokerResponse:
    """Adapter-owned broker response representation (reference simulation).

    This model is isolated from the core domain models and is NOT part of
    the generic :class:`BrokerAdapter` contract. The adapter normalizes it
    into a broker-neutral :class:`AdapterResult` at the boundary.
    """

    client_order_id: str
    status: str
    broker_order_id: str | None = None
    reason: str = ""
    error_kind: str | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id must be a non-empty string.")
        if self.status not in _REFERENCE_STATUSES:
            raise ValueError(
                f"Unknown reference-broker status {self.status!r}; "
                f"expected one of {_REFERENCE_STATUSES}."
            )
        if self.broker_order_id is not None and not isinstance(
            self.broker_order_id, str
        ):
            raise TypeError("broker_order_id must be a string or None.")
        if self.error_kind is not None and self.error_kind not in _REFERENCE_ERROR_KINDS:
            raise ValueError(
                f"Unknown reference-broker error kind {self.error_kind!r}; "
                f"expected one of {_REFERENCE_ERROR_KINDS}."
            )

    @property
    def is_failure(self) -> bool:
        """Whether this internal response represents a failure outcome."""
        return self.status in ("rejected", "failed", "timeout", "unknown")


# ============================================================
# REFERENCE SIMULATION (deterministic, in-memory, offline)
# ============================================================


class ReferenceSimulation:
    """Deterministic in-memory reference broker simulation (adapter-owned).

    The simulation NEVER contacts a network and NEVER requires credentials.
    It produces deterministic responses from configured scenarios and records
    every operation (``submissions`` / ``reconciliations`` / ``cancels`` /
    ``orders``) so tests can assert broker-neutrality and idempotency
    semantics.
    """

    def __init__(
        self,
        *,
        submit_scenario: str = "accepted",
        reconcile_scenario: str = "reconcile_accepted",
        cancel_scenario: str = "accepted",
    ) -> None:
        self.submit_scenario = self._validate_scenario(submit_scenario, "submit")
        self.reconcile_scenario = self._validate_scenario(
            reconcile_scenario, "reconcile"
        )
        self.cancel_scenario = self._validate_scenario(cancel_scenario, "cancel")
        self._orders: dict[str, ReferenceBrokerRequest] = {}
        self.submissions: list[ReferenceBrokerRequest] = []
        self.reconciliations: list[str] = []
        self.cancels: list[str] = []

    @staticmethod
    def _validate_scenario(scenario: str, operation: str) -> str:
        if scenario not in REFERENCE_ADAPTER_SCENARIOS:
            raise ValueError(
                f"Unknown {operation}_scenario {scenario!r}; "
                f"expected one of {REFERENCE_ADAPTER_SCENARIOS}."
            )
        return scenario

    @property
    def orders(self) -> tuple[ReferenceBrokerRequest, ...]:
        """Deterministically ordered snapshot of recorded reference orders."""
        return tuple(
            sorted(self._orders.values(), key=lambda r: (r.client_order_id, r.symbol))
        )

    # ---------------------------------------------------------
    # OPERATIONS
    # ---------------------------------------------------------

    def submit(self, request: ReferenceBrokerRequest) -> ReferenceBrokerResponse:
        """Simulate a submit; record the request and return a response.

        ADAPTER / REFERENCE-BROKER DUPLICATE DETECTION: when the same
        ``client_order_id`` was already recorded by an earlier accepting
        submission, the simulation reports ``broker_status="duplicate"``
        (deterministic dedupe). This is an adapter-level mechanism and does
        NOT claim a real broker's idempotency guarantees.
        """

        self.submissions.append(request)
        response = self._build_response(
            self.submit_scenario, request.client_order_id, "submit"
        )
        already_recorded = request.client_order_id in self._orders
        if (
            already_recorded
            and response.status in _ORDER_RECORDING_STATUSES
            and response.status != "duplicate"
        ):
            response = ReferenceBrokerResponse(
                client_order_id=request.client_order_id,
                status="duplicate",
                broker_order_id=response.broker_order_id,
                reason="Reference broker deduplicated via client order id.",
            )
        if response.status in _ORDER_RECORDING_STATUSES:
            self._orders[request.client_order_id] = request
        return response

    def reconcile(self, client_order_id: str) -> ReferenceBrokerResponse:
        """Simulate a reconcile (query) by client order id; no order sent."""

        self.reconciliations.append(client_order_id)
        return self._build_response(
            self.reconcile_scenario, client_order_id, "reconcile"
        )

    def cancel(self, client_order_id: str) -> ReferenceBrokerResponse:
        """Simulate a cancel request by client order id."""

        self.cancels.append(client_order_id)
        return self._build_response(
            self.cancel_scenario, client_order_id, "cancel"
        )

    # ---------------------------------------------------------
    # INTERNAL
    # ---------------------------------------------------------

    def _build_response(
        self, scenario: str, client_order_id: str, operation: str
    ) -> ReferenceBrokerResponse:
        """Deterministic internal response for a scenario."""

        broker_order_id = derive_reference_broker_order_id(
            client_order_id=client_order_id,
            operation=operation,
            scenario=scenario,
        )
        if scenario == "accepted":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="accepted",
                broker_order_id=broker_order_id,
                reason="Reference broker accepted the order.",
            )
        if scenario == "rejected":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="rejected",
                broker_order_id=broker_order_id,
                reason="Reference broker rejected the order.",
                error_kind="broker_rejection",
            )
        if scenario == "failed":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                broker_order_id=broker_order_id,
                reason="Reference broker internal failure.",
                error_kind="internal",
            )
        if scenario == "timeout":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="timeout",
                reason="Reference broker request timed out (ambiguous).",
                error_kind="timeout",
            )
        if scenario == "unknown":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="unknown",
                reason="Reference broker returned an unknown outcome.",
                error_kind="unknown_outcome",
            )
        if scenario == "reconcile_accepted":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="accepted",
                broker_order_id=broker_order_id,
                reason="Reference broker reconciliation confirmed acceptance.",
            )
        if scenario == "reconcile_rejected":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="rejected",
                broker_order_id=broker_order_id,
                reason="Reference broker reconciliation confirmed rejection.",
                error_kind="broker_rejection",
            )
        if scenario == "reconcile_unknown":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="unknown",
                reason="Reference broker reconciliation could not determine outcome.",
                error_kind="unknown_outcome",
            )
        if scenario == "duplicate":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="duplicate",
                broker_order_id=broker_order_id,
                reason="Reference broker deduplicated via client order id.",
            )
        if scenario == "restart":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="submitted",
                broker_order_id=broker_order_id,
                reason="Reference broker reports submission after restart.",
            )
        if scenario == "cancelled":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="cancelled",
                broker_order_id=broker_order_id,
                reason="Reference broker confirmed cancellation.",
            )
        if scenario == "filled":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="filled",
                broker_order_id=broker_order_id,
                reason="Reference broker confirmed a full fill.",
            )
        if scenario == "partially_filled":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="partially_filled",
                broker_order_id=broker_order_id,
                reason="Reference broker confirmed a partial fill.",
            )
        if scenario == "unsupported_operation":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                reason="Reference broker does not support the requested operation.",
                error_kind="unsupported_operation",
            )
        if scenario == "unsupported_instrument":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                reason="Reference broker does not support the requested instrument.",
                error_kind="unsupported_instrument",
            )
        if scenario == "unsupported_order_type":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                reason="Reference broker does not support the requested order type.",
                error_kind="unsupported_order_type",
            )
        if scenario == "validation_failure":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                reason="Reference broker rejected the request before submission.",
                error_kind="validation",
            )
        if scenario == "malformed_internal":
            return ReferenceBrokerResponse(
                client_order_id=client_order_id,
                status="failed",
                reason="Reference broker returned a malformed response.",
                error_kind="malformed_response",
            )
        raise ValueError(f"Unknown scenario {scenario!r}.")


# ============================================================
# ADAPTER-OWNED TRANSLATION
# ============================================================


def _translate_command(command: ExecutionCommand) -> ReferenceBrokerRequest:
    """Translate an ExecutionCommand into the adapter-owned request.

    This is the adapter-owned translation boundary: the generic execution
    system never needs to understand the reference request representation.
    """

    try:
        order_type = _DIRECTION_TO_ORDER_TYPE[command.direction]
    except KeyError:
        raise ValueError(
            f"Reference adapter cannot translate direction {command.direction!r} "
            f"to an order type (fail closed)."
        ) from None
    return ReferenceBrokerRequest(
        symbol=f"{REFERENCE_EXCHANGE}:{command.instrument}",
        exchange=REFERENCE_EXCHANGE,
        order_type=order_type,
        product=REFERENCE_PRODUCT,
        quantity=command.quantity,
        price=command.entry,
        stop_price=command.stop,
        target_price=command.target,
        client_order_id=derive_client_order_id(command_id=command.command_id),
        idempotency_key=derive_idempotency_key(command_id=command.command_id),
        execution_mode=command.execution_mode.value,
        created_at=command.created_at,
    )


# ============================================================
# ADAPTER RESULT NORMALIZATION
# ============================================================


_ERROR_KIND_TO_CODE: dict[str, BrokerErrorCode] = {
    "validation": BrokerErrorCode.VALIDATION_FAILURE,
    "unsupported_operation": BrokerErrorCode.UNSUPPORTED_OPERATION,
    "unsupported_instrument": BrokerErrorCode.UNSUPPORTED_INSTRUMENT,
    "unsupported_order_type": BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,
    "broker_rejection": BrokerErrorCode.BROKER_REJECTION,
    "timeout": BrokerErrorCode.TIMEOUT,
    "unknown_outcome": BrokerErrorCode.UNKNOWN_OUTCOME,
    "malformed_response": BrokerErrorCode.MALFORMED_RESPONSE,
    "internal": BrokerErrorCode.INTERNAL_ADAPTER_FAILURE,
}


def _normalize_response(response: ReferenceBrokerResponse) -> AdapterResult:
    """Normalize an internal reference-broker response into AdapterResult.

    This is the adapter-owned normalization boundary: the core system
    receives ONLY the broker-neutral :class:`AdapterResult` / error taxonomy,
    never the reference response object or adapter-specific exceptions.
    """

    status = response.status
    if status == "accepted":
        return AdapterResult(
            status=BrokerResultStatus.ACCEPTED,
            broker_order_id=response.broker_order_id,
            broker_status="accepted",
            reason=response.reason,
        )
    if status == "submitted":
        return AdapterResult(
            status=BrokerResultStatus.SUBMITTED,
            broker_order_id=response.broker_order_id,
            broker_status="submitted",
            reason=response.reason,
        )
    if status == "cancelled":
        return AdapterResult(
            status=BrokerResultStatus.CANCELLED,
            broker_order_id=response.broker_order_id,
            broker_status="cancelled",
            reason=response.reason,
        )
    if status == "filled":
        return AdapterResult(
            status=BrokerResultStatus.FILLED,
            broker_order_id=response.broker_order_id,
            broker_status="filled",
            reason=response.reason,
        )
    if status == "partially_filled":
        return AdapterResult(
            status=BrokerResultStatus.PARTIALLY_FILLED,
            broker_order_id=response.broker_order_id,
            broker_status="partially_filled",
            reason=response.reason,
        )
    if status == "duplicate":
        return AdapterResult(
            status=BrokerResultStatus.ACCEPTED,
            broker_order_id=response.broker_order_id,
            broker_status="duplicate",
            reason=response.reason,
        )
    if status == "rejected":
        return AdapterResult.rejected(
            reason=response.reason, broker_order_id=response.broker_order_id
        )
    if status == "failed":
        code = _ERROR_KIND_TO_CODE.get(
            response.error_kind or "internal", BrokerErrorCode.INTERNAL_ADAPTER_FAILURE
        )
        return AdapterResult.failed(
            code=code, reason=response.reason, broker_order_id=response.broker_order_id
        )
    if status == "timeout":
        return AdapterResult.unknown(
            code=BrokerErrorCode.TIMEOUT, reason=response.reason
        )
    if status == "unknown":
        return AdapterResult.unknown(
            code=BrokerErrorCode.UNKNOWN_OUTCOME, reason=response.reason
        )
    raise ValueError(
        f"Reference broker produced an un-normalizable status {status!r} "
        f"(fail closed)."
    )


# ============================================================
# CONCRETE REFERENCE BROKER ADAPTER
# ============================================================


class ReferenceBrokerAdapter:
    """Concrete reference / simulated broker adapter (Checkpoint 17.4).

    Implements the frozen :class:`BrokerAdapter` contract. All broker-specific
    translation and behavior is isolated inside this adapter.

    Attributes:
        name:
            Adapter name (for registration/selection).
        execution_mode:
            The execution mode this adapter is bound to (PAPER or LIVE).
            NEVER silently overridden.
        capabilities:
            Declared :class:`AdapterCapabilities`.
        submit_scenario / reconcile_scenario / cancel_scenario:
            Deterministic scenario applied to each operation.
        unsupported_instruments:
            Instruments the adapter does NOT support (capability boundary).
        simulation:
            The adapter-owned :class:`ReferenceSimulation` (injectable for
            tests; a fresh deterministic one is created by default).
    """

    def __init__(
        self,
        *,
        name: str = "reference",
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        submit_scenario: str = "accepted",
        reconcile_scenario: str = "reconcile_accepted",
        cancel_scenario: str = "accepted",
        capabilities: tuple[AdapterCapability, ...] = (
            AdapterCapability.SUBMIT,
            AdapterCapability.RECONCILE,
            AdapterCapability.CANCEL,
        ),
        unsupported_instruments: tuple[str, ...] = (),
        simulation: ReferenceSimulation | None = None,
    ) -> None:
        if not isinstance(execution_mode, ExecutionMode):
            raise TypeError(
                f"execution_mode must be an ExecutionMode; "
                f"got {type(execution_mode).__name__!r}."
            )
        self.name = name
        self.execution_mode = execution_mode
        self.capabilities = AdapterCapabilities(
            capabilities=tuple(capabilities), execution_mode=execution_mode
        )
        self.submit_scenario = submit_scenario
        self.reconcile_scenario = reconcile_scenario
        self.cancel_scenario = cancel_scenario
        self.unsupported_instruments = frozenset(unsupported_instruments)
        self.simulation = (
            simulation
            if simulation is not None
            else ReferenceSimulation(
                submit_scenario=submit_scenario,
                reconcile_scenario=reconcile_scenario,
                cancel_scenario=cancel_scenario,
            )
        )

    # ---------------------------------------------------------
    # BROKER ADAPTER CONTRACT
    # ---------------------------------------------------------

    def submit(self, command: ExecutionCommand) -> AdapterResult:
        """Submit an already-authorized command (deterministic scenario)."""

        _validate_command(command)
        validate_adapter_mode(
            adapter_execution_mode=self.execution_mode, command=command
        )
        self.check(command)
        request = _translate_command(command)
        response = self.simulation.submit(request)
        return _normalize_response(response)

    def reconcile(self, client_order_id: str) -> AdapterResult:
        """Reconcile an earlier submission by its client order id."""

        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise TypeError("client_order_id must be a non-empty string.")
        response = self.simulation.reconcile(client_order_id)
        return _normalize_response(response)

    def cancel(self, client_order_id: str) -> AdapterResult:
        """Request cancellation (deterministic scenario)."""

        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise TypeError("client_order_id must be a non-empty string.")
        if not self.capabilities.supports_cancel:
            raise ValueError(
                f"ReferenceBrokerAdapter {self.name!r} does not support CANCEL."
            )
        response = self.simulation.cancel(client_order_id)
        return _normalize_response(response)

    def supports(self, command: ExecutionCommand) -> bool:
        """Capability boundary: whether this adapter supports the command.

        Returns ``False`` (never raises) for a non-command, an unsupported
        instrument, or an execution-mode mismatch.
        """

        try:
            self._validate_capability(command)
        except (TypeError, ValueError):
            return False
        return True

    def check(self, command: ExecutionCommand) -> None:
        """Pre-validation: raises on unsupported instrument / mode mismatch."""

        self._validate_capability(command)

    # ---------------------------------------------------------
    # INTERNAL
    # ---------------------------------------------------------

    def _validate_capability(self, command: ExecutionCommand) -> None:
        """Deterministic capability validation (never mutates the command)."""

        _validate_command(command)
        validate_adapter_mode(
            adapter_execution_mode=self.execution_mode, command=command
        )
        if command.instrument in self.unsupported_instruments:
            raise ValueError(
                f"ReferenceBrokerAdapter {self.name!r} does not support "
                f"instrument {command.instrument!r} "
                f"(capability boundary; fail closed)."
            )


def _validate_command(command: ExecutionCommand) -> None:
    """Type-check an ExecutionCommand (broker-neutral, fail closed)."""

    if not isinstance(command, ExecutionCommand):
        raise TypeError(
            f"command must be an ExecutionCommand; "
            f"got {type(command).__name__!r}."
        )


#: Convenience factory for a PAPER-mode reference adapter.
def paper_reference_adapter(**kwargs: Any) -> ReferenceBrokerAdapter:
    """Build a PAPER-mode reference adapter (default)."""
    kwargs.setdefault("execution_mode", ExecutionMode.PAPER)
    kwargs.setdefault("name", "reference-paper")
    return ReferenceBrokerAdapter(**kwargs)


#: Convenience factory for a LIVE-mode reference adapter.
def live_reference_adapter(**kwargs: Any) -> ReferenceBrokerAdapter:
    """Build a LIVE-mode reference adapter."""
    kwargs.setdefault("execution_mode", ExecutionMode.LIVE)
    kwargs.setdefault("name", "reference-live")
    return ReferenceBrokerAdapter(**kwargs)


__all__ = [
    "REFERENCE_ADAPTER_SCENARIOS",
    "REFERENCE_BROKER_ORDER_ID_PREFIX",
    "REFERENCE_EXCHANGE",
    "REFERENCE_PRODUCT",
    "ReferenceBrokerAdapter",
    "ReferenceBrokerRequest",
    "ReferenceBrokerResponse",
    "ReferenceSimulation",
    "derive_reference_broker_order_id",
    "live_reference_adapter",
    "paper_reference_adapter",
]
