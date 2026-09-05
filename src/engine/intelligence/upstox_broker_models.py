"""Upstox broker-specific models — adapter-owned (Checkpoint 17.7).

This module defines the broker-SPECIFIC request / response / error model
types for the Upstox broker adapter. It is the adapter-owned translation
boundary: these models are ISOLATED from the core domain models and are NOT
part of the generic :class:`engine.intelligence.broker_adapter_contract.BrokerAdapter`
contract.

CRITICAL ISOLATION RULES (Checkpoint 17.7):

* This module imports ONLY stdlib (``dataclasses`` / ``decimal`` /
  ``datetime`` / ``enum`` / ``typing``). It NEVER imports core domain models
  (``ExecutionCommand`` / ``TradePlan`` / ``OperationalTradeIntent`` /
  ``SubmissionLifecycle`` / broker-neutral result models), never imports a
  network library, never imports credential material, and never imports a
  broker SDK.
* These models MUST NOT leak into ``ExecutionCommand``, ``TradePlan``,
  ``TradeIntent``, ``SubmissionLifecycle``, core persistence, or the
  broker-neutral result models (``AdapterResult`` / ``BrokerError``).
* Everything the core needs is normalized into the broker-neutral result
  taxonomy AT the adapter boundary in
  :mod:`engine.intelligence.upstox_broker_adapter`.

The Upstox-specific facts encoded here are the ones verified from public
Upstox developer documentation in Checkpoint 17.6 (Section 42):
transaction types ``BUY``/``SELL``, order types ``MARKET``/``LIMIT``/``SL``/
``SL-M``, products ``D``/``I``/``MTF``, validity ``DAY``/``IOC``, instrument
tokens ``NSE_EQ|INE...`` / ``NSE_INDEX|Nifty 50``, and the response envelope
``status`` (``success``/``error``) + ``data``. Facts that could NOT be
verified are labeled UNKNOWN / REQUIRES IMPLEMENTATION-TIME VERIFICATION and
the adapter fails closed where they matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

# ============================================================
# ENUMS (broker-specific, adapter-owned)
# ============================================================


class UpstoxTransactionType(Enum):
    """Upstox transaction (side) type (VERIFIED FROM PUBLIC DOCUMENTATION)."""

    BUY = "BUY"
    SELL = "SELL"


class UpstoxOrderType(Enum):
    """Upstox order types (VERIFIED FROM PUBLIC DOCUMENTATION).

    ``SL`` is a stop-LIMIT (requires ``price`` + ``trigger_price``);
    ``SL-M`` is a stop-MARKET (``price`` = 0, ``trigger_price`` set). The
    exact SL/SL-M semantics REQUIRE IMPLEMENTATION-TIME VERIFICATION and the
    adapter fails closed when a requested semantic cannot be expressed with
    confidence.
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class UpstoxProduct(Enum):
    """Upstox product codes (VERIFIED FROM PUBLIC DOCUMENTATION).

    First adapter scope supports only ``D`` (delivery); ``I`` (intraday) and
    ``MTF`` (margin trading facility) are recognized members but map to
    ``supports() == False`` until implemented.
    """

    D = "D"
    I = "I"
    MTF = "MTF"


class UpstoxValidity(Enum):
    """Upstox validity types (VERIFIED FROM PUBLIC DOCUMENTATION).

    First adapter scope supports only ``DAY``; ``IOC`` is a recognized member
    but maps to ``supports() == False`` until implemented.
    """

    DAY = "DAY"
    IOC = "IOC"


class UpstoxExchange(Enum):
    """Upstox exchange segments derived from the instrument-token prefix.

    First adapter scope supports NSE equities (``NSE_EQ|...``) and the NIFTY
    50 index (``NSE_INDEX|...``). ``BSE_EQ|...`` is a recognized member but
    maps to ``supports() == False`` until implemented.
    """

    NSE = "NSE"
    BSE = "BSE"


class UpstoxOrderState(Enum):
    """Upstox order-status vocabulary (documented families).

    The exact enumeration REQUIRE IMPLEMENTATION-TIME VERIFICATION against the
    current Upstox documents; the adapter maps KNOWN members here and maps
    any unrecognized status string to an unknown outcome (never forced into
    an unsafe mapping).

    ``OPEN`` maps to ``SUBMITTED`` (pending/working) or ``ACCEPTED`` when the
    broker confirms acceptance; the adapter uses ``ACCEPTED`` for a confirmed
    acceptance outcome and ``OPEN`` for a pending/working order.
    """

    OPEN = "open"
    ACCEPTED = "accepted"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    UNKNOWN = "unknown"


class UpstoxErrorKind(Enum):
    """Internal (adapter-owned) classification of a client/parse failure.

    These are the adapter-internal error kinds produced by the
    :class:`~engine.intelligence.upstox_broker_client.UpstoxBrokerClient`
    boundary and normalized at the adapter into the broker-neutral
    :class:`~engine.models.broker_adapter.BrokerError` taxonomy. They are NOT
    part of the broker-neutral contract and never cross the adapter boundary.
    """

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    INVALID_INSTRUMENT = "invalid_instrument"
    VALIDATION = "validation"
    INVALID_ORDER_TYPE = "invalid_order_type"
    INVALID_PRODUCT = "invalid_product"
    INVALID_EXCHANGE = "invalid_exchange"
    BROKER_REJECTION = "broker_rejection"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    BROKER_UNAVAILABLE = "broker_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    UNKNOWN_OUTCOME = "unknown_outcome"
    INTERNAL = "internal"
    DUPLICATE = "duplicate"


# ============================================================
# REQUEST MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class UpstoxBrokerRequest:
    """Adapter-owned Upstox place-order request (frozen+slots).

    This is the broker-specific representation an
    :class:`~engine.intelligence.upstox_broker_client.UpstoxBrokerClient`
    would send to the Upstox V3 API. It is isolated from core domain models.

    Attributes:
        instrument_token:
            Upstox instrument token (``NSE_EQ|INE...`` / ``NSE_INDEX|...``).
        transaction_type:
            :class:`UpstoxTransactionType` (BUY/SELL).
        quantity:
            Verbatim authorized quantity (``Decimal``). Never increased.
        product:
            :class:`UpstoxProduct` (first scope ``D``).
        validity:
            :class:`UpstoxValidity` (first scope ``DAY``).
        order_type:
            :class:`UpstoxOrderType` (MARKET/LIMIT/SL/SL-M).
        price:
            Verbatim entry price (``Decimal``). Never altered.
        trigger_price:
            Verbatim stop level (``Decimal`` or None). Never altered.
        tag:
            Broker-facing application identity derived deterministically from
            ``client_order_id`` (see the adapter's tag mapper).
        client_order_id:
            The frozen deterministic client order id (``co-`` + sha256[:16]).
        idempotency_key:
            The frozen deterministic idempotency key (``idem-`` + sha256[:16]).
            Used for local duplicate detection only; NEVER claimed to be a
            broker-side idempotency key.
        execution_mode:
            The bound execution mode name (``PAPER`` / ``LIVE``).
        created_at:
            Timezone-aware command creation timestamp (caller-supplied).
    """

    instrument_token: str
    transaction_type: UpstoxTransactionType
    quantity: Decimal
    product: UpstoxProduct
    validity: UpstoxValidity
    order_type: UpstoxOrderType
    price: Decimal
    trigger_price: Decimal | None
    tag: str
    client_order_id: str
    idempotency_key: str
    execution_mode: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.instrument_token or not self.instrument_token.strip():
            raise ValueError("instrument_token must be a non-empty string.")
        if not isinstance(self.transaction_type, UpstoxTransactionType):
            raise TypeError(
                "transaction_type must be an UpstoxTransactionType."
            )
        if not isinstance(self.quantity, Decimal) or self.quantity <= 0:
            raise ValueError("quantity must be a positive Decimal.")
        if not isinstance(self.product, UpstoxProduct):
            raise TypeError("product must be an UpstoxProduct.")
        if not isinstance(self.validity, UpstoxValidity):
            raise TypeError("validity must be an UpstoxValidity.")
        if not isinstance(self.order_type, UpstoxOrderType):
            raise TypeError("order_type must be an UpstoxOrderType.")
        if not isinstance(self.price, Decimal):
            raise TypeError("price must be a Decimal.")
        if self.trigger_price is not None and not isinstance(self.trigger_price, Decimal):
            raise TypeError("trigger_price must be a Decimal or None.")
        if not self.tag or not self.tag.strip():
            raise ValueError("tag must be a non-empty string.")
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id must be a non-empty string.")
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")
        if self.execution_mode not in ("PAPER", "LIVE"):
            raise ValueError("execution_mode must be 'PAPER' or 'LIVE'.")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware when present.")

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection (Decimal -> str)."""
        return {
            "instrument_token": self.instrument_token,
            "transaction_type": self.transaction_type.value,
            "quantity": str(self.quantity),
            "product": self.product.value,
            "validity": self.validity.value,
            "order_type": self.order_type.value,
            "price": str(self.price),
            "trigger_price": None if self.trigger_price is None else str(self.trigger_price),
            "tag": self.tag,
            "client_order_id": self.client_order_id,
            "idempotency_key": self.idempotency_key,
            "execution_mode": self.execution_mode,
            "created_at": self.created_at.isoformat() if self.created_at is not None else None,
        }


# ============================================================
# RESPONSE MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class UpstoxOrderData:
    """Adapter-owned Upstox order response ``data.order_ids`` entry.

    Attributes:
        order_ids:
            Tuple of broker-generated order ids (the 17.6 design:
            place-order response returns ``data.order_ids`` -- an array.
            First scope handles a SINGLE id deterministically; a multi-id
            response is a CONCERNn mapping to UNKNOWN unless confirmed.
    """

    order_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.order_ids:
            raise ValueError("order_ids must carry at least one id.")
        for oid_ in self.order_ids:
            if not oid_ or not isinstance(oid_, str):
                raise TypeError("order_ids must be a tuple of non-empty strings.")


@dataclass(frozen=True, slots=True)
class UpstoxPlaceOrderResponse:
    """Adapter-owned Upstox place-order response envelope (status + data)."""

    status: str
    order_data: UpstoxOrderData | None
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("success", "error"):
            raise ValueError(f"Unrecognized Upstox response status {self.status!r}; "
                             f"expected 'success' or 'error'.")
        if self.status == "success" and self.order_data is None:
            raise ValueError("success response must carry order_data.")
        if self.status == "success" and (self.error_code or self.error_message):
            raise ValueError("success response must not carry error fields.")
        if self.status == "error" and self.order_data is not None:
            raise ValueError("error response must not carry order_data.")
        if self.status == "error" and not self.error_code and not self.error_message:
            raise ValueError("error response must carry an error code or message.")

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_error(self) -> bool:
        return self.status == "error"


@dataclass(frozen=True, slots=True)
class UpstoxOrderStateResponse:
    """Adapter-owned Upstox order lookup / reconciliation response.

    This represents a single authoritative order-history record for a
    reconciliation lookup. ``order_id`` is the broker-generated order id;
    ``tags`` is the tuple of Upstox tags attached to the order (the
    application identity); ``quantity`` / ``filled_quantity`` are in broker
    quantity units (never fabricated); ``status`` is a
    :class:`UpstoxOrderState` member after the provider-state mapper.
    """

    order_id: str
    tag: str
    quantity: Decimal | None = None
    filled_quantity: Decimal | None = None
    status: UpstoxOrderState = UpstoxOrderState.UNKNOWN
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.order_id or not self.order_id.strip():
            raise ValueError("order_id must be a non-empty string.")
        for attr in ("quantity", "filled_quantity"):
            value = getattr(self, attr)
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"{attr} must be a Decimal or None.")
        if not isinstance(self.status, UpstoxOrderState):
            raise TypeError("status must be an UpstoxOrderState.")


@dataclass(frozen=True, slots=True)
class UpstoxCancelResponse:
    """Adapter-owned Upstox cancel-order response."""

    status: str
    order_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("success", "error"):
            raise ValueError(f"Unrecognized Upstox cancel status {self.status!r}.")
        if self.status == "success" and not self.order_id:
            raise ValueError("success cancel response must carry order_id.")
        if self.status == "error":
            # An error cancel may still carry an order_id (broker confirmation).
            pass


@dataclass(frozen=True, slots=True)
class UpstoxClientFailure:
    """Adapter-owned client-level failure with an error KINDD.

    The client boundary catches transport/parse/HTTP envelope failures and
    returns/presents this tagged failure so the adapter can normalize it into
    a broker-neutral :class:`BrokerError` without leaking exception types.
    """

    kind: UpstoxErrorKind
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, UpstoxErrorKind):
            raise TypeError("kind must be an UpstoxErrorKind.")
        if not self.message or not self.message.strip():
            raise ValueError("message must be a non-empty string.")