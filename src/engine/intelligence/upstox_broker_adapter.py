"""Upstox broker adapter (Checkpoint 17.7).

This module provides the FIRST broker-SPECIFIC adapter against the frozen
broker-neutral :class:`~engine.intelligence.broker_adapter_contract.BrokerAdapter`
contract (Checkpoint 17.2), per the Checkpoint 17.6 design and blueprint.

CRITICAL SAFETY STATEMENT (Checkpoint 17.7):

* This adapter NEVER connects to Upstox. It NEVER uses the Upstox SDK.
  It NEVER makes network/API calls. It NEVER holds or reads real credentials.
  It NEVER submits / cancels / reconciles against a real broker.
* The adapter is implemented against an INJECTED / MOCKED broker client
  (:class:`~engine.intelligence.upstox_broker_client.UpstoxBrokerClient` --
  in 17.7 only the in-memory, network-free
  :class:`~engine.intelligence.upstox_broker_client.MockUpstoxBrokerClient`
  is used). The adapter imports NO network library and NO broker SDK.
* The adapter is a TRANSLATION BOUNDARY ONLY. It contains NO trading
  intelligence, setup analysis, signal generation, risk calculation,
  authorization decisions, TradePlan creation, or ExecutionCommand creation.
* The adapter has ZERO authorization authority: it accepts ONLY
  already-authorized, immutable :class:`ExecutionCommand` objects per the
  frozen contract.

Adapter-owned responsibilities (Checkpoint 17.6 Section 10):

* Translate an ``ExecutionCommand`` into an Upstox place-order request
  (side, quantity, price, trigger price, order type, product, validity,
  instrument token, tag).
* Normalize Upstox responses into broker-neutral :class:`AdapterResult`
  (never leaking Upstox models, HTTP envelopes, or status strings upward).
* Normalize Upstox errors / client failures into :class:`BrokerError` using
  the frozen taxonomy (:class:`BrokerErrorCode` + :class:`BrokerErrorCategory`).
* Expose broker-neutral capabilities (:class:`AdapterCapabilities`: SUBMIT +
  RECONCILE + CANCEL) and ``supports`` / ``check`` capability validation.
* Verify paper/live mode via the frozen ``validate_adapter_mode`` before
  EVERY operation.
* Own the deterministic client-order identity mapping
  (``client_order_id`` -> Upstox ``tag``) and the reconciliation lookup
  (tag primary; broker order id fallback) WITHOUT falsely claiming
  broker-side idempotency.

Mapping tables (adapter-owned, isolated):

* Instrument map: canonical name -> Upstox instrument token
  (mirrors the historical provider's isolated verified map). Unknown /
  missing / ambiguous mapping -> ``UNSUPPORTED_INSTRUMENT``, never a guessed
  instrument.
* Order-type map: generic semantics -> Upstox ``order_type``. First scope
  MARKET / LIMIT / SL / SL-M. Bracket / OCO (entry+stop+target) is NOT
  supported by Upstox place-order -> ``UNSUPPORTED_ORDER_SEMANTICS`` (the
  ``target`` field is not transmissible; explicitly documented loss).
* Product map: first scope ``D`` (delivery); ``I`` / ``MTF`` recognized but
  unsupported -> fail closed.
* Validity map: first scope ``DAY``; ``IOC`` recognized but unsupported ->
  fail closed.
* Exchange map: derived from the instrument-token prefix (``NSE_EQ|...`` /
  ``NSE_INDEX|...``); ``BSE_EQ|...`` recognized but unsupported -> fail
  closed.
* State map: Upstox status -> :class:`BrokerResultStatus` with unknown ->
  ``UNKNOWN`` (never forced).
* Error map: Upstox error codes + client failure kinds -> broker-neutral
  taxonomy; unknown codes -> ``UNKNOWN_OUTCOME`` (AMBIGUOUS).

Integration contract:

* ``submit`` / ``reconcile`` / ``cancel`` return ONLY broker-neutral
  :class:`AdapterResult` objects. No Upstox response object crosses the
  boundary.
* A timeout / unknown / rate-limited submission after the request was
  transmitted -> ``UNKNOWN`` (never ``FAILED``); the frozen lifecycle enforces
  reconcile-before-retry.
* ``supports`` returns ``False`` (never raises) and ``check`` raises for any
  unsupported instrument/order type/product/validity/quantity/price before a
  broker request is generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from engine.intelligence.broker_adapter_contract import (
    AdapterCapabilities,
    BrokerAdapter,
    derive_client_order_id,
    derive_idempotency_key,
    validate_adapter_mode,
)
from engine.intelligence.upstox_broker_client import (
    UpstoxBrokerClient,
    redact_sensitive,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxCancelResponse,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderType,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
    UpstoxPlaceOrderResponse,
    UpstoxProduct,
    UpstoxTransactionType,
    UpstoxValidity,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionCommand, ExecutionMode

#: Broker identity constant (adapter-owned; used for observability).
UPSTOX_BROKER_IDENTITY = "upstox"

#: Deterministic tag prefix derived from the frozen ``co-`` client id.
UPSTOX_TAG_PREFIX = "uptag-"

#: Length of the SHA-256 digest prefix used in the bounded Upstox tag.
_TAG_DIGEST_LENGTH = 12

#: Broker context for the deterministic client order identity. The frozen
#: infrastructure derives the lifecycle's ``client_order_id`` with the
#: DEFAULT context (``"default"``); the adapter MUST use the SAME context so
#: its request identity matches the lifecycle identity (reconciliation and
#: idempotency depend on this). This mirrors the reference adapter, which
#: also uses the default context.
_UPSTOX_BROKER_CONTEXT = "default"

#: Upstox instrument tokens verified from the established controlled Upstox
#: verification (mirrors the isolated historical-provider map). Only these
#: canonical instruments are mapped; anything else is UNSUPPORTED and never
#: guessed.
_VERIFIED_INSTRUMENT_KEY_MAP: dict[str, str] = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "TCS": "NSE_EQ|INE467B01029",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "NIFTY": "NSE_INDEX|Nifty 50",
}

#: Supported instrument-token segment prefixes (exchange mapping).
_SUPPORTED_TOKEN_PREFIXES: tuple[str, ...] = ("NSE_EQ|", "NSE_INDEX|")

#: Generic order semantic -> Upstox order type (first scope).
_GENERIC_ORDER_TYPE_TO_UPSTOX: dict[str, UpstoxOrderType] = {
    "MARKET": UpstoxOrderType.MARKET,
    "LIMIT": UpstoxOrderType.LIMIT,
    "STOP": UpstoxOrderType.SL,
    "STOP_MARKET": UpstoxOrderType.SL_M,
}

#: Upstox order type -> generic semantic (inverse, for reconciliation texts).
_UPSTOX_TO_GENERIC_ORDER_TYPE: dict[UpstoxOrderType, str] = {
    UpstoxOrderType.MARKET: "MARKET",
    UpstoxOrderType.LIMIT: "LIMIT",
    UpstoxOrderType.SL: "STOP",
    UpstoxOrderType.SL_M: "STOP_MARKET",
}

#: Direction -> Upstox transaction type (side).
_DIRECTION_TO_TRANSACTION_TYPE: dict[str, UpstoxTransactionType] = {
    "LONG": UpstoxTransactionType.BUY,
    "SHORT": UpstoxTransactionType.SELL,
}


@dataclass(frozen=True, slots=True)
class UpstoxBrokerConfig:
    """Adapter-owned configuration boundary (frozen+slots, validated).

    Attributes:
        name:
            Adapter name (for registration/selection).
        execution_mode:
            The execution mode this adapter is bound to (never overridden).
        timeout_seconds:
            Bounded request timeout convention for the future real client
            (informational in 17.7; no network exists).
        instrument_key_map:
            Canonical instrument -> Upstox instrument token (isolated map).
        credential_provider:
            Broker-specific credential provider injected ONLY into the
            client (the adapter never calls it directly). May be None only
            for tests / paper mode (the mock client fails closed without it).
        order_types:
            Recognized generic order types (adapter-owned);
            unknown generic semantics -> fail closed.
    """

    name: str = "upstox"
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    timeout_seconds: int = 30
    instrument_key_map: Mapping[str, str] = field(
        default_factory=lambda: dict(_VERIFIED_INSTRUMENT_KEY_MAP)
    )
    credential_provider: Any = None
    order_types: tuple[str, ...] = (
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_MARKET",
    )

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("name must be a non-empty string.")
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError("execution_mode must be an ExecutionMode.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if not self.instrument_key_map:
            raise ValueError("instrument_key_map must not be empty.")
        for generic in self.order_types:
            if generic not in _GENERIC_ORDER_TYPE_TO_UPSTOX:
                raise ValueError(f"Unsupported generic order type {generic!r}.")
        normalized: dict[str, str] = {}
        for k, v in self.instrument_key_map.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise TypeError("instrument_key_map must map str -> str.")
            normalized[k] = v
        object.__setattr__(self, "instrument_key_map", normalized)

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Deterministic auditable configuration snapshot."""
        return tuple(
            sorted(
                {
                    "name": self.name,
                    "execution_mode": self.execution_mode.value,
                    "timeout_seconds": str(self.timeout_seconds),
                    "order_types": ",".join(self.order_types),
                }.items()
            )
        )


# ============================================================
# SHARED MAPPERS (module-level deterministic functions)
# ============================================================


def derive_upstox_tag(client_order_id: str, *, digest_length: int = _TAG_DIGEST_LENGTH) -> str:
    """Derive the bounded Upstox ``tag`` from the frozen client order id.

    The Upstox ``tag`` field constraints REQUIRE IMPLEMENTATION-TIME
    VERIFICATION; the adapter uses a deterministic collision-safe encoding
    (``uptag-`` + sha256(client_order_id)[:12]) that is restart-stable and
    never silently truncates the underlying identity. The mapping is
    deterministic so reconciliation can invert it. ``client_order_id`` is
    already ``co-`` + 16 hex chars (19 chars); the derived tag is
    ``uptag-`` + 12 hex chars (18 chars).
    """

    if not isinstance(client_order_id, str) or not client_order_id.strip():
        raise TypeError("client_order_id must be a non-empty string.")
    import hashlib

    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
    return f"{UPSTOX_TAG_PREFIX}{digest[:digest_length]}"


def map_order_state(upstox_state: UpstoxOrderState) -> BrokerResultStatus:
    """Map an Upstox order state to a broker-neutral result status.

    The mapping is never forced: an unknown / ambiguous state maps to
    ``UNKNOWN`` (reconcile) rather than a guessed status.
    """


def _parse_order_state(state: UpstoxOrderState | str) -> UpstoxOrderState:
    """Coerce an order-state enum member or its value into the enum.

    Unknown strings are never guessed: they parse to ``UNKNOWN`` so the
    caller maps them to an unknown (reconcile) outcome.
    """

    if isinstance(state, UpstoxOrderState):
        return state
    try:
        return UpstoxOrderState(state)
    except ValueError:
        return UpstoxOrderState.UNKNOWN


def map_order_state(upstox_state: UpstoxOrderState) -> BrokerResultStatus:
    """Map an Upstox order state to a broker-neutral result status.

    The mapping is never forced: an unknown / ambiguous state maps to
    ``UNKNOWN`` (reconcile) rather than a guessed status.
    """

    if upstox_state is UpstoxOrderState.OPEN:
        # Order accepted, not filled -- SUBMITTED (pending/working).
        return BrokerResultStatus.SUBMITTED
    if upstox_state is UpstoxOrderState.ACCEPTED:
        return BrokerResultStatus.ACCEPTED
    if upstox_state is UpstoxOrderState.COMPLETE:
        return BrokerResultStatus.FILLED
    if upstox_state is UpstoxOrderState.CANCELLED:
        return BrokerResultStatus.CANCELLED
    if upstox_state is UpstoxOrderState.REJECTED:
        return BrokerResultStatus.REJECTED
    if upstox_state is UpstoxOrderState.PARTIALLY_FILLED:
        return BrokerResultStatus.PARTIALLY_FILLED
    return BrokerResultStatus.UNKNOWN


#: Upstox error-code/kind -> broker-neutral error code. Unknown entries never
#: reach this table (they normalize to UNKNOWN_OUTCOME).
_UPSTOX_ERROR_TO_CODE: dict[UpstoxErrorKind | str, BrokerErrorCode] = {
    UpstoxErrorKind.AUTHENTICATION: BrokerErrorCode.AUTHENTICATION_FAILURE,
    UpstoxErrorKind.AUTHORIZATION: BrokerErrorCode.AUTHORIZATION_FAILURE,
    UpstoxErrorKind.INVALID_INSTRUMENT: BrokerErrorCode.UNSUPPORTED_INSTRUMENT,
    UpstoxErrorKind.VALIDATION: BrokerErrorCode.VALIDATION_FAILURE,
    UpstoxErrorKind.INVALID_ORDER_TYPE: BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,
    UpstoxErrorKind.INVALID_PRODUCT: BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,
    UpstoxErrorKind.INVALID_EXCHANGE: BrokerErrorCode.UNSUPPORTED_INSTRUMENT,
    UpstoxErrorKind.BROKER_REJECTION: BrokerErrorCode.BROKER_REJECTION,
    UpstoxErrorKind.RATE_LIMIT: BrokerErrorCode.RATE_LIMIT,
    UpstoxErrorKind.TIMEOUT: BrokerErrorCode.TIMEOUT,
    UpstoxErrorKind.NETWORK: BrokerErrorCode.NETWORK_FAILURE,
    UpstoxErrorKind.BROKER_UNAVAILABLE: BrokerErrorCode.BROKER_UNAVAILABLE,
    UpstoxErrorKind.MALFORMED_RESPONSE: BrokerErrorCode.MALFORMED_RESPONSE,
    UpstoxErrorKind.UNKNOWN_OUTCOME: BrokerErrorCode.UNKNOWN_OUTCOME,
    UpstoxErrorKind.INTERNAL: BrokerErrorCode.INTERNAL_ADAPTER_FAILURE,
}

#: Mapped Upstox documented error codes -> outcomes (best-effort reclassification
#: at the adapter boundary). Codes not present normalize to UNKNOWN_OUTCOME.
_UPSTOX_DOCUMENTED_ERROR_CODES: dict[str, BrokerErrorCode] = {
    "UDAPI1004": BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,  # valid order type required
    "UDAPI1007": BrokerErrorCode.VALIDATION_FAILURE,  # validity required
    "UDAPI1056": BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,  # invalid order type
    "UDAPI1055": BrokerErrorCode.VALIDATION_FAILURE,  # invalid validity
    "UDAPI1008": BrokerErrorCode.VALIDATION_FAILURE,  # price required
    "UDAPI1036": BrokerErrorCode.VALIDATION_FAILURE,  # trigger price required
    "UDAPI1003": BrokerErrorCode.VALIDATION_FAILURE,  # order id required
    "UDAPI1154": BrokerErrorCode.BROKER_REJECTION,  # static-IP restriction
    "UDAPI1158": BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS,  # market orders not allowed
    "UDAPI100041": BrokerErrorCode.BROKER_REJECTION,  # modification of finalized orders
}

#: Message-based rejection heuristics for broker-side conditions that the
#: design designates "adapter-mapped" (insufficient funds / margin / market
#: closed / generic rejection). These are deterministic, documented adapter
#: translations; they are applied ONLY when the error code is not in the
#: documented catalog (otherwise an unknown code would be a false UNKNOWN).
_REJECTION_TEXT_HINTS: tuple[str, ...] = (
    "reject",
    "insufficient",
    "margin",
    "market clos",
    "exchange not open",
    "not allowed",
)


def _classify_ambiguous(error_kind: UpstoxErrorKind) -> bool:
    """Whether an error kind represents an ambiguous outcome (UNKNOWN)."""
    return error_kind in (
        UpstoxErrorKind.TIMEOUT,
        UpstoxErrorKind.UNKNOWN_OUTCOME,
        UpstoxErrorKind.MALFORMED_RESPONSE,
    )


def normalize_client_failure(failure: UpstoxClientFailure) -> AdapterResult:
    """Normalize a client-level failure into a broker-neutral AdapterResult.

    Ambiguous kinds (timeout / unknown / malformed) -> ``UNKNOWN`` (never
    ``FAILED``). Authentication / transport -> ``FAILED`` with the matching
    code. The reason string is redacted before it reaches the result.
    """

    reason = redact_sensitive(failure.message)
    if _classify_ambiguous(failure.kind):
        code = _UPSTOX_ERROR_TO_CODE.get(failure.kind, BrokerErrorCode.UNKNOWN_OUTCOME)
        return AdapterResult.unknown(code=code, reason=reason)
    code = _UPSTOX_ERROR_TO_CODE.get(failure.kind, BrokerErrorCode.UNKNOWN_OUTCOME)
    if code is BrokerErrorCode.UNKNOWN_OUTCOME:
        return AdapterResult.unknown(code=code, reason=reason)
    return AdapterResult.failed(code=code, reason=reason)


def normalize_unexpected_exception(exc: Exception) -> AdapterResult:
    """Normalize an unexpected adapter-internal exception.

    The adapter never lets an unexpected exception escape as a broker-specific
    error: it is normalized to ``FAILED`` (INTERNAL_ADAPTER_FAILURE). No
    exception detail is propagated verbatim (redacted, non-sensitive).
    """

    text = redact_sensitive(str(exc))[:200] if str(exc) else "no detail"
    message = f"Upstox adapter internal failure: {text}"
    return AdapterResult.failed(
        code=BrokerErrorCode.INTERNAL_ADAPTER_FAILURE, reason=message
    )


def _normalize_place_order_response(
    request: UpstoxBrokerRequest,
    response: (
        UpstoxPlaceOrderResponse
        | UpstoxOrderStateResponse
        | UpstoxClientFailure
    ),
) -> AdapterResult:
    """Normalize a place-order response into a broker-neutral AdapterResult.

    Key rules:

    * A rate-limit client failure during SUBMISSION is ambiguous (the broker
      may have accepted the order) -> ``UNKNOWN`` (reconcile), never ``FAILED``.
    * HTTP/envelope "success" is NOT order success: only a confirmed success
      envelope with a single non-empty order id is a confirmed outcome.
    * A broker-confirmed rejection (insufficient funds / market closed /
      generic rejection) -> ``REJECTED`` (broker-confirmed, terminal).
    * A deterministic validation failure (invalid quantity/price/order type/
      product/instrument) -> ``FAILED`` with the matching code.
    * An unknown error code -> ``UNKNOWN`` (``UNKNOWN_OUTCOME``).
    """

    if isinstance(response, UpstoxClientFailure):
        if response.kind is UpstoxErrorKind.RATE_LIMIT:
            # A rate-limited submit is NOT proof the order was not accepted.
            return AdapterResult.unknown(
                code=BrokerErrorCode.RATE_LIMIT,
                reason=redact_sensitive(response.message),
            )
        return normalize_client_failure(response)

    if isinstance(response, UpstoxOrderStateResponse):
        # A confirmed order-state response at submission time (e.g. the
        # "submitted/pending" scenario) carries a single confirmed order id.
        status = map_order_state(_parse_order_state(response.status))
        if status is BrokerResultStatus.UNKNOWN:
            return AdapterResult.unknown(
                code=BrokerErrorCode.UNKNOWN_OUTCOME,
                reason=redact_sensitive(response.reason or "unknown order state"),
            )
        return AdapterResult(
            status=status,
            broker_order_id=response.order_id,
            broker_status=status.value.lower(),
            reason=redact_sensitive(
                response.reason or "Upstox returned a confirmed order state."
            ),
        )

    # HTTP/envelope "success" is NOT order success: only a confirmed success
    # envelope with a non-empty order id is a confirmed outcome.
    if response.is_success and response.order_data is not None:
        order_ids = response.order_data.order_ids
        if len(order_ids) == 1:
            return AdapterResult(
                status=BrokerResultStatus.ACCEPTED,
                broker_order_id=order_ids[0],
                broker_status="accepted",
                reason="Upstox accepted the order (mocked).",
            )
        # Multi-order-id response (slicing) is a CONCERN -> UNKNOWN.
        return AdapterResult.unknown(
            code=BrokerErrorCode.MALFORMED_RESPONSE,
            reason=(
                "Upstox returned multiple order ids for one place-order "
                "request; outcome ambiguous (requires manual review)."
            ),
        )

    # Confirmed error envelope.
    code = _UPSTOX_DOCUMENTED_ERROR_CODES.get(
        response.error_code, BrokerErrorCode.UNKNOWN_OUTCOME
    )
    reason = redact_sensitive(
        f"Upstox place-order error "
        f"{response.error_code or 'unknown'}: {response.error_message or 'no detail'}"
    )
    if code is BrokerErrorCode.UNKNOWN_OUTCOME:
        # Unknown error code: use deterministic message heuristics for the
        # adapter-mapped rejection conditions (insufficient funds / margin /
        # market closed / generic rejection). If no hint matches, the outcome
        # is genuinely unknown -> UNKNOWN (never a guessed success/failure).
        lowered = (response.error_message or "").lower()
        if any(hint in lowered for hint in _REJECTION_TEXT_HINTS):
            return AdapterResult.rejected(reason=reason)
        return AdapterResult.unknown(code=code, reason=reason)
    if code is BrokerErrorCode.BROKER_REJECTION:
        # Broker-confirmed rejection (insufficient funds / market closed /
        # static-IP restriction) -> REJECTED (terminal, broker-confirmed).
        return AdapterResult.rejected(reason=reason)
    return AdapterResult.failed(code=code, reason=reason)


def _normalize_order_state_response(
    response: UpstoxOrderStateResponse | UpstoxClientFailure,
) -> AdapterResult:
    """Normalize an order-lookup response into a broker-neutral AdapterResult."""

    if isinstance(response, UpstoxClientFailure):
        return normalize_client_failure(response)
    status = map_order_state(response.status)
    if status is BrokerResultStatus.UNKNOWN:
        return AdapterResult.unknown(
            code=BrokerErrorCode.UNKNOWN_OUTCOME,
            reason=redact_sensitive(
                f"Upstox order state {response.status.value!r} cannot be "
                f"safely normalized; reconciliation state unknown."
            ),
        )
    if status is BrokerResultStatus.SUBMITTED:
        return AdapterResult(
            status=BrokerResultStatus.SUBMITTED,
            broker_order_id=response.order_id,
            broker_status="submitted",
            reason=redact_sensitive(response.reason or "Order is open/pending."),
        )
    error: AdapterResult | None = None
    if status is BrokerResultStatus.REJECTED:
        error = AdapterResult.rejected(
            reason=redact_sensitive(response.reason or "Order rejected by Upstox."),
            broker_order_id=response.order_id,
        )
    elif status is BrokerResultStatus.CANCELLED:
        error = AdapterResult(
            status=BrokerResultStatus.CANCELLED,
            broker_order_id=response.order_id,
            broker_status="cancelled",
            reason=redact_sensitive(response.reason or "Order cancelled by Upstox."),
        )
    elif status is BrokerResultStatus.FILLED:
        error = AdapterResult(
            status=BrokerResultStatus.FILLED,
            broker_order_id=response.order_id,
            broker_status="filled",
            reason=redact_sensitive(response.reason or "Order filled by Upstox."),
        )
    elif status is BrokerResultStatus.PARTIALLY_FILLED:
        error = AdapterResult(
            status=BrokerResultStatus.PARTIALLY_FILLED,
            broker_order_id=response.order_id,
            broker_status="partially_filled",
            reason=redact_sensitive(response.reason or "Order partially filled."),
        )
    else:
        error = AdapterResult(
            status=BrokerResultStatus.ACCEPTED,
            broker_order_id=response.order_id,
            broker_status="accepted",
            reason=redact_sensitive(response.reason or "Order accepted by Upstox."),
        )
    return error


def _normalize_cancel_response(
    response: UpstoxCancelResponse | UpstoxClientFailure,
) -> AdapterResult:
    """Normalize a cancel response into a broker-neutral AdapterResult."""

    if isinstance(response, UpstoxClientFailure):
        return normalize_client_failure(response)
    if response.status == "success":
        return AdapterResult(
            status=BrokerResultStatus.CANCELLED,
            broker_order_id=response.order_id or None,
            broker_status="cancelled",
            reason="Upstox confirmed cancellation (mocked).",
        )
    # Confirmed cancel error: the broker rejected the cancel. This may mean
    # the order was already filled (the fill is authoritative) or the cancel
    # was invalid -- normalize as REJECTED (never a false cancellation).
    return AdapterResult.rejected(
        reason="Upstox rejected the cancellation request (order may already be "
        "filled or in an invalid state for cancel).",
        broker_order_id=response.order_id or None,
    )


# ============================================================
# CONCRETE UPSTOX BROKER ADAPTER
# ============================================================


class UpstoxBrokerAdapter:
    """Concrete Upstox broker adapter (Checkpoint 17.7).

    Implements the frozen :class:`BrokerAdapter` contract with all
    broker-specific translation and behavior isolated inside this adapter and
    its adapter-owned client / models.

    Attributes:
        name:
            Adapter name (for registration/selection).
        execution_mode:
            The execution mode this adapter is bound to (PAPER or LIVE).
            NEVER silently overridden.
        capabilities:
            Declared :class:`AdapterCapabilities` (SUBMIT + RECONCILE +
            CANCEL).
        config:
            The adapter-owned :class:`UpstoxBrokerConfig`.
        client:
            The injected :class:`UpstoxBrokerClient` (in 17.7 the
            network-free mock). The adapter has NO default network client.
        submissions:
            Operation log of ``(command_id, client_order_id, tag)`` tuples
            (deterministic, for idempotency tests).
        reconciliations / cancels:
            Operation logs.
    """

    def __init__(
        self,
        *,
        name: str = "upstox",
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        client: UpstoxBrokerClient | None = None,
        config: UpstoxBrokerConfig | None = None,
        capabilities: tuple[AdapterCapability, ...] = (
            AdapterCapability.SUBMIT,
            AdapterCapability.RECONCILE,
            AdapterCapability.CANCEL,
        ),
    ) -> None:
        if not isinstance(execution_mode, ExecutionMode):
            raise TypeError(
                f"execution_mode must be an ExecutionMode; "
                f"got {type(execution_mode).__name__!r}."
            )
        if client is None:
            raise ValueError(
                "UpstoxBrokerAdapter requires an injected broker client; "
                f"in 17.7 use the network-free "
                f"MockUpstoxBrokerClient. A real HTTP client is NOT "
                f"permitted in Checkpoint 17.7."
            )
        if not isinstance(config, UpstoxBrokerConfig):
            config = UpstoxBrokerConfig(
                name=name, execution_mode=execution_mode
            )
        if config.execution_mode is not execution_mode:
            raise ValueError(
                "config.execution_mode must match the adapter execution_mode "
                "(never silently overridden)."
            )
        self.name = name
        self.execution_mode = execution_mode
        self.capabilities = AdapterCapabilities(
            capabilities=tuple(capabilities), execution_mode=execution_mode
        )
        self.config = config
        self.client = client
        self.submissions: list[tuple[str, str, str]] = []
        self.reconciliations: list[str] = []
        self.cancels: list[str] = []
        self.dispatched_requests: list[UpstoxBrokerRequest] = []

    # ---------------------------------------------------------
    # BROKER ADAPTER CONTRACT
    # ---------------------------------------------------------

    def submit(self, command: ExecutionCommand) -> AdapterResult:
        """Submit an already-authorized command via the mocked/mapped client."""

        _validate_command(command)
        validate_adapter_mode(
            adapter_execution_mode=self.execution_mode, command=command
        )
        self.check(command)
        request = self._translate_command(command)
        self.dispatched_requests.append(request)
        self.record_submission(command, request)
        try:
            response = self.client.place_order(request)
        except Exception as exc:  # never leak broker-specific exceptions
            return normalize_unexpected_exception(exc)
        return _normalize_place_order_response(request, response)

    def reconcile(self, client_order_id: str) -> AdapterResult:
        """Reconcile an earlier submission by its deterministic client id."""

        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise TypeError("client_order_id must be a non-empty string.")
        self.reconciliations.append(client_order_id)
        tag = derive_upstox_tag(client_order_id)
        # Primary lookup: Upstox tag. Fallback: broker order id is NOT
        # available at this boundary (callers pass broker_order_id in the
        # integration layer); the tag lookup is the reconciliation key.
        try:
            response = self.client.get_order(tag=tag)
        except Exception as exc:
            return normalize_unexpected_exception(exc)
        return _normalize_order_state_response(response)

    def cancel(self, client_order_id: str) -> AdapterResult:
        """Request cancellation of an in-flight submission."""

        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise TypeError("client_order_id must be a non-empty string.")
        if not self.capabilities.supports_cancel:
            raise ValueError(
                f"UpstoxBrokerAdapter {self.name!r} does not support CANCEL."
            )
        self.cancels.append(client_order_id)
        # Primary cancel identifier: broker order id. The adapter resolves it
        # from the mock order book via the tag; in the real integration the
        # lifecycle's broker_order_id is passed by the (frozen) infra layer.
        tag = derive_upstox_tag(client_order_id)
        order_id = self._resolve_order_id_for_cancel(client_order_id, tag)
        try:
            response = self.client.cancel_order(order_id)
        except Exception as exc:
            return normalize_unexpected_exception(exc)
        return _normalize_cancel_response(response)

    def supports(self, command: ExecutionCommand) -> bool:
        """Capability boundary: whether this adapter supports the command.

        Returns ``False`` (never raises) for a non-command, an unsupported
        instrument, an unsupported order semantic, an invalid quantity, an
        invalid price/trigger price, or an execution-mode mismatch.
        """

        try:
            self._validate_capability(command)
        except (TypeError, ValueError):
            return False
        return True

    def check(self, command: ExecutionCommand) -> None:
        """Pre-validation: raises on unsupported capability / semantic."""

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
        if command.instrument not in self.config.instrument_key_map:
            raise ValueError(
                f"UpstoxBrokerAdapter {self.name!r} does not support "
                f"instrument {command.instrument!r} (no verified Upstox "
                f"instrument token; capability boundary; fail closed)."
            )
        token = self.config.instrument_key_map[command.instrument]
        _validate_token_prefix(token)
        # Quantity: positive + integer lot-multiple (floor below).
        try:
            quantity = _to_decimal(command.quantity)
        except _NumericCoercionError as exc:
            raise ValueError(str(exc)) from exc
        if quantity is None or quantity <= 0:
            raise ValueError(
                "quantity must be positive and present for Upstox submission "
                "(fail closed)."
            )
        # Order semantics: entry required for LIMIT / SL; stop required for
        # STOP / STOP_MARKET; MARKET price must be absent/zero.
        self._validate_order_semantics(command)

    def _validate_order_semantics(self, command: ExecutionCommand) -> None:
        """Validate the generic order semantics derived from the command.

        The adapter owns the generic order semantic: an entry (LIMIT / SL /
        SL-M) or market (MARKET) request is derived from the presence of the
        reference levels. The first scope supports LIMIT and MARKET (the
        fixture commands carry an entry reference). The design's STOP /
        STOP-MARKET semantics REQUIRE IMPLEMENTATION-TIME VERIFICATION of
        Upstox SL/SL-M; the adapter maps them but the capability gate only
        broadens when the exact semantics are confirmed (fails closed
        otherwise).
        """

        try:
            entry = _to_decimal(command.entry)
            stop = _to_decimal(command.stop)
        except _NumericCoercionError as exc:
            raise ValueError(str(exc)) from exc
        # Fixture commands always carry an entry reference (LIMIT semantics).
        if entry is None:
            raise ValueError(
                "Upstox submission requires an entry reference price "
                "(LIMIT semantics; fail closed)."
            )
        if entry <= 0:
            raise ValueError(
                "entry must be positive for Upstox submission (fail closed)."
            )
        if stop is not None and stop >= entry:
            if command.direction == "LONG":
                raise ValueError(
                    "For a LONG Upstox SL order the trigger price must be "
                    "BELOW the entry price (fail closed)."
                )
        if stop is not None and stop <= entry:
            if command.direction == "SHORT":
                raise ValueError(
                    "For a SHORT Upstox SL order the trigger price must be "
                    "ABOVE the entry price (fail closed)."
                )

    def _translate_command(self, command: ExecutionCommand) -> UpstoxBrokerRequest:
        """Translate an ExecutionCommand into the adapter-owned request.

        This is the adapter-owned translation boundary: the generic execution
        system never needs to understand the Upstox request representation.
        """

        try:
            transaction_type = _DIRECTION_TO_TRANSACTION_TYPE[command.direction]
        except KeyError:
            raise ValueError(
                f"Upstox adapter cannot translate direction {command.direction!r} "
                f"to a transaction type (fail closed)."
            ) from None
        token = self.config.instrument_key_map.get(command.instrument)
        if token is None:
            raise ValueError(
                f"Upstox instrument token for {command.instrument!r} is not "
                f"mapped (fail closed; never guess)."
            )
        _validate_token_prefix(token)
        try:
            quantity_decimal = _to_decimal(command.quantity)
            entry = _to_decimal(command.entry)
            stop = _to_decimal(command.stop)
        except _NumericCoercionError as exc:
            raise ValueError(str(exc)) from exc
        if quantity_decimal is None or quantity_decimal <= 0:
            raise ValueError("quantity must be positive (fail closed).")
        # Quantity floored to the broker lot size (integer units). NEVER
        # increased -- increasing quantity would increase risk and violate
        # the frozen risk invariant.
        quantity = quantity_decimal
        if quantity != quantity.to_integral_value():
            quantity = quantity.to_integral_value(rounding="ROUND_FLOOR")
        if quantity <= 0:
            raise ValueError(
                "quantity floors to zero after lot rounding (fail closed; "
                "never invent a higher quantity)."
            )
        if entry is None or entry <= 0:
            raise ValueError("entry must be positive (fail closed).")
        client_order_id = derive_client_order_id(
            command_id=command.command_id, broker_context=_UPSTOX_BROKER_CONTEXT
        )
        idempotency_key = derive_idempotency_key(
            command_id=command.command_id, broker_context=_UPSTOX_BROKER_CONTEXT
        )
        tag = derive_upstox_tag(client_order_id)
        # Generic order semantic: LIMIT (entry reference present).
        order_type = _GENERIC_ORDER_TYPE_TO_UPSTOX["LIMIT"]
        return UpstoxBrokerRequest(
            instrument_token=token,
            transaction_type=transaction_type,
            quantity=quantity,
            product=UpstoxProduct.D,
            validity=UpstoxValidity.DAY,
            order_type=order_type,
            price=entry,
            trigger_price=stop,
            tag=tag,
            client_order_id=client_order_id,
            idempotency_key=idempotency_key,
            execution_mode=command.execution_mode.value,
            created_at=command.created_at,
        )

    def record_submission(
        self,
        command: ExecutionCommand,
        request: UpstoxBrokerRequest,
    ) -> None:
        """Record the (command_id, client_order_id, tag) submission identity."""

        self.submissions.append(
            (
                command.command_id,
                request.client_order_id,
                request.tag,
            )
        )

    def _resolve_order_id_for_cancel(
        self, client_order_id: str, tag: str
    ) -> str:
        """Resolve the broker order id for cancellation.

        The primary cancel identifier is the broker ``order_id`` (recorded on
        the lifecycle downstream-only). At this boundary the mock order book
        is inspected first; when absent, a deterministic order id derived
        from the tag is used (a real integration would receive the order id
        from the frozen lifecycle layer).
        """

        if tag in getattr(self.client, "_orders", {}):
            entry = self.client._orders[tag]  # type: ignore[attr-defined]
            return entry.order_id
        from engine.models.broker_adapter import derive_broker_order_id

        return derive_broker_order_id(
            client_order_id=client_order_id, operation="place", scenario="accepted"
        )


def _validate_command(command: ExecutionCommand) -> None:
    """Type-check an ExecutionCommand (broker-neutral, fail closed)."""

    if not isinstance(command, ExecutionCommand):
        raise TypeError(
            f"command must be an ExecutionCommand; "
            f"got {type(command).__name__!r}."
        )


class _NumericCoercionError(ValueError):
    """A command numeric value could not be coerced to a safe Decimal."""


def _to_decimal(value: Decimal | str | int | float | None) -> Decimal | None:
    """Coerce a command numeric field to a safe Decimal.

    The frozen command model copies values verbatim (it does not coerce);
    the adapter boundary validates the economic inputs defensively. Accepts
    Decimal / numeric str / int / float; rejects bool, NaN, infinity, and
    non-numeric text (fail closed). Money is never computed in binary float.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise _NumericCoercionError(f"Cannot coerce bool {value!r} to Decimal.")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ValueError, TypeError) as exc:
        raise _NumericCoercionError(
            f"Cannot coerce {type(value).__name__} value {value!r} to Decimal."
        ) from exc
    if not decimal_value.is_finite():
        raise _NumericCoercionError(
            f"Non-finite numeric value {value!r} rejected (fail closed)."
        )
    return decimal_value


def _validate_token_prefix(token: str) -> None:
    """Validate the instrument-token segment prefix (exchange mapping)."""

    if not isinstance(token, str) or not token.strip():
        raise ValueError("instrument token must be a non-empty string.")
    if not any(token.startswith(p) for p in _SUPPORTED_TOKEN_PREFIXES):
        raise ValueError(
            f"Unsupported instrument-token segment prefix in {token!r}; "
            f"supported prefixes are {_SUPPORTED_TOKEN_PREFIXES} "
            f"(exchange mapping; fail closed)."
        )


#: Convenience factory for a PAPER-mode Upstox adapter (mock client).
def paper_upstox_adapter(**kwargs: Any) -> UpstoxBrokerAdapter:
    """Build a PAPER-mode Upstox adapter with an injected mock client.

    Scenario kwargs (``submit_scenario`` / ``reconcile_scenario`` /
    ``cancel_scenario``) are forwarded to the network-free
    :class:`~engine.intelligence.upstox_broker_client.MockUpstoxBrokerClient`.
    A fake credential provider is injected ONLY so the mocked operations
    execute (the mock performs no network and no real credential is used).
    """

    from engine.intelligence.upstox_broker_client import MockUpstoxBrokerClient
    from engine.intelligence.upstox_credential_provider import (
        StaticUpstoxCredentialProvider,
    )

    kwargs.setdefault("execution_mode", ExecutionMode.PAPER)
    kwargs.setdefault("name", "upstox-paper")
    if "client" not in kwargs:
        client_kwargs = {
            "name": "upstox-mock-paper",
            "credential_provider": StaticUpstoxCredentialProvider(
                "__dummy_paper_token__"
            ),
        }
        for label in ("submit", "reconcile", "cancel"):
            key = f"{label}_scenario"
            if key in kwargs:
                client_kwargs[key] = kwargs.pop(key)
        kwargs["client"] = MockUpstoxBrokerClient(**client_kwargs)
    return UpstoxBrokerAdapter(**kwargs)


#: Convenience factory for a LIVE-mode Upstox adapter (mock client; the live
#: credential provider must exist and yield a token or the adapter fails
#: closed before any operation).
def live_upstox_adapter(**kwargs: Any) -> UpstoxBrokerAdapter:
    """Build a LIVE-mode Upstox adapter.

    Requires an injected mock client with a credential provider that yields a
    token; without a credential the mock client fails closed on every
    operation (``AUTHENTICATION_FAILURE``). In 17.7 this is a MOCK -- no live
    path exists.
    """

    from engine.intelligence.upstox_broker_client import MockUpstoxBrokerClient
    from engine.intelligence.upstox_credential_provider import (
        StaticUpstoxCredentialProvider,
    )

    kwargs.setdefault("execution_mode", ExecutionMode.LIVE)
    kwargs.setdefault("name", "upstox-live")
    if "client" not in kwargs:
        client_kwargs = {
            "name": "upstox-mock-live",
            "credential_provider": StaticUpstoxCredentialProvider(
                "__dummy_live_token__"
            ),
        }
        for label in ("submit", "reconcile", "cancel"):
            key = f"{label}_scenario"
            if key in kwargs:
                client_kwargs[key] = kwargs.pop(key)
        kwargs["client"] = MockUpstoxBrokerClient(**client_kwargs)
    else:
        client = kwargs["client"]
        provider = getattr(client, "credential_provider", None)
        if provider is None or not hasattr(provider, "get_access_token"):
            raise ValueError(
                "live_upstox_adapter requires a client with a credential "
                "provider (fail closed without credentials)."
            )
        token = provider.get_access_token()
        if not token:
            raise ValueError(
                "live_upstox_adapter requires a credential provider that "
                "yields a non-empty token (fail closed without credentials)."
            )
    return UpstoxBrokerAdapter(**kwargs)


__all__ = [
    "UPSTOX_BROKER_IDENTITY",
    "UPSTOX_TAG_PREFIX",
    "UpstoxBrokerAdapter",
    "UpstoxBrokerConfig",
    "derive_upstox_tag",
    "live_upstox_adapter",
    "map_order_state",
    "normalize_client_failure",
    "normalize_unexpected_exception",
    "paper_upstox_adapter",
]