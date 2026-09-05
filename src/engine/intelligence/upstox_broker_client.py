"""Upstox broker client boundary (Checkpoint 17.7).

This module defines the adapter-owned broker CLIENT boundary. It is the ONLY
module that may reference Upstox API endpoints/URLs and attach the
Authorization header in a FUTURE real-HTTP implementation. In Checkpoint 17.7
there is NO network implementation: the module defines the
:class:`UpstoxBrokerClient` Protocol and a deterministic, in-memory
:class:`MockUpstoxBrokerClient` used by every 17.7 test.

CRITICAL SAFETY RULES (Checkpoint 17.7):

* The client protocol itself performs NO network operations. The only
  implementation used by 17.7 tests is the in-memory mock. A future real
  HTTP client belongs to a later checkpoint and must be explicitly
  authorized.
* The client owns the authentication handshake / credential injection in a
  future implementation; in 17.7 the mock simply records whether a
  credential provider yielded a token (fail closed when empty).
* The client owns the redaction rule: any error text that could contain an
  ``Authorization: Bearer <token>`` or a token env-var value is scrubbed
  BEFORE it reaches the adapter / normalization.
* This module imports NO core domain models, NO network libraries, and NO
  broker SDK. It imports only its own adapter-owned models and the
  credential-provider module.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxCancelResponse,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderData,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
    UpstoxPlaceOrderResponse,
)
from engine.intelligence.upstox_credential_provider import (
    SENSITIVE_TOKEN_ENV_NAMES,
    UpstoxCredentialProvider,
)

#: Defensive redaction patterns -- a credential value must never surface in
#: an error/reason string (mirrors the corpus-ingestion redaction rule).
_BEARER_RE = re.compile(r"Bearer\s+\S+")


def _env_value_pattern(name: str) -> re.Pattern[str]:
    """Regex matching ``<NAME>=<value>`` or ``<NAME>: <value>``.

    The value runs to the next whitespace, comma, quote, or end-of-string so
    it is scrubbed together with the env-var name (a bare ``name=<redacted>``
    replacement would leave the raw value visible right after the marker).
    """

    return re.compile(
        re.escape(name) + r"\s*[=:]\s*([^\s,;'\"]+)"
    )

#: Deterministic mock-client scenario vocabulary (17.6 Section 38).
MOCK_UPSTOX_CLIENT_SCENARIOS: tuple[str, ...] = (
    "accepted",
    "submitted",
    "rejected",
    "failed",
    "unknown",
    "validation_failure",
    "insufficient_funds",
    "invalid_instrument",
    "invalid_order_type",
    "timeout",
    "unknown_outcome",
    "reconcile_accepted",
    "reconcile_rejected",
    "reconcile_unknown",
    "duplicate",
    "malformed_response",
    "rate_limit",
    "broker_unavailable",
    "authentication_failure",
    "restart",
    "cancelled",
    "filled",
    "partially_filled",
    "cancellation_timeout",
    "cancellation_race_fill",
    "internal",
)


def redact_sensitive(text: str) -> str:
    """Scrub credential material from an error/reason string.

    Removes ``Authorization: Bearer <token>`` patterns and the token
    env-var names. Deterministic and safe to call on any text.
    """

    redacted = _BEARER_RE.sub("Bearer <redacted>", str(text))
    for name in SENSITIVE_TOKEN_ENV_NAMES:
        redacted = _env_value_pattern(name).sub(
            lambda m: f"{name}=<redacted>", redacted
        )
    return redacted


@runtime_checkable
class UpstoxBrokerClient(Protocol):
    """Adapter-owned broker client protocol (no network in 17.7).

    A future real-HTTP implementation of this protocol is the ONLY module
    allowed to construct Upstox API URLs and attach the Authorization header.
    The protocol methods return adapter-owned response models or a tagged
    :class:`UpstoxClientFailure`; they never raise broker-specific exceptions
    into the adapter.
    """

    name: str

    def place_order(
        self, request: UpstoxBrokerRequest
    ) -> UpstoxPlaceOrderResponse | UpstoxClientFailure:
        """Submit a place-order request; return a response or tagged failure."""
        ...

    def get_order(
        self, tag: str, order_id: str | None = None
    ) -> UpstoxOrderStateResponse | UpstoxClientFailure:
        """Look up an order by tag (primary) or broker order id (fallback)."""
        ...

    def cancel_order(self, order_id: str) -> UpstoxCancelResponse | UpstoxClientFailure:
        """Request cancellation of an order by broker order id."""
        ...

    def check_health(self) -> bool:
        """Return whether the client is healthy (no network in 17.7)."""
        ...


class MockUpstoxBrokerClient:
    """Deterministic, in-memory, network-free Upstox broker client (17.7).

    This is the ONLY broker client used by 17.7 tests. It implements the
    :class:`UpstoxBrokerClient` protocol with deterministic scenario
    injection and records every operation so tests can assert broker-neutrality,
    idempotency, and reconciliation semantics.

    Attributes:
        name:
            Client name (``"upstox-mock"``).
        submit_scenario / reconcile_scenario / cancel_scenario:
            Deterministic scenario applied to each operation.
        credential_provider:
            Injected :class:`UpstoxCredentialProvider` (fake in tests). The
            mock checks whether the provider yields a non-empty token and
            fails closed (authentication failure) when empty.
        health_ok:
            Whether ``check_health()`` reports healthy.
        submissions / reconciliations / cancels:
            Operation logs (deterministic order).
        orders:
            Deterministic in-memory order book keyed by tag (for
            reconcile-by-tag and duplicate detection).
    """

    def __init__(
        self,
        *,
        name: str = "upstox-mock",
        submit_scenario: str = "accepted",
        reconcile_scenario: str = "reconcile_accepted",
        cancel_scenario: str = "accepted",
        credential_provider: UpstoxCredentialProvider | None = None,
        health_ok: bool = True,
    ) -> None:
        for label, scenario in (
            ("submit", submit_scenario),
            ("reconcile", reconcile_scenario),
            ("cancel", cancel_scenario),
        ):
            if scenario not in MOCK_UPSTOX_CLIENT_SCENARIOS:
                raise ValueError(
                    f"Unknown {label}_scenario {scenario!r}; expected one of "
                    f"{MOCK_UPSTOX_CLIENT_SCENARIOS}."
                )
        self.name = name
        self.submit_scenario = submit_scenario
        self.reconcile_scenario = reconcile_scenario
        self.cancel_scenario = cancel_scenario
        self.credential_provider = credential_provider
        self.health_ok = bool(health_ok)
        self.submissions: list[UpstoxBrokerRequest] = []
        self.reconciliations: list[tuple[str, str | None]] = []
        self.cancels: list[str] = []
        self._orders: dict[str, UpstoxOrderStateResponse] = {}

    # ---------------------------------------------------------
    # CREDENTIAL GATE (fail closed)
    # ---------------------------------------------------------

    def _token_available(self) -> bool:
        if self.credential_provider is None:
            # No provider injected -> no credential -> fail closed.
            return False
        token = self.credential_provider.get_access_token()
        return bool(token) if isinstance(token, str) else False

    # ---------------------------------------------------------
    # PLACE ORDER
    # ---------------------------------------------------------

    def place_order(
        self, request: UpstoxBrokerRequest
    ) -> UpstoxPlaceOrderResponse | UpstoxClientFailure:
        """Submit a place-order request (deterministic scenario)."""

        self.submissions.append(request)
        if not self._token_available():
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION,
                message="Upstox execution access token is unavailable (fail closed).",
            )
        if self.submit_scenario == "authentication_failure":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION,
                message="Upstox rejected the access token (invalid/expired).",
            )
        if self.submit_scenario == "timeout":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.TIMEOUT,
                message="Upstox request timed out (ambiguous outcome).",
            )
        if self.submit_scenario == "unknown_outcome":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message="Upstox returned an unconfirmable outcome.",
            )
        if self.submit_scenario == "malformed_response":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message="Upstox returned a malformed response envelope.",
            )
        if self.submit_scenario == "rate_limit":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.RATE_LIMIT,
                message="Upstox rate limit reached (ambiguous for submission).",
            )
        if self.submit_scenario == "broker_unavailable":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.BROKER_UNAVAILABLE,
                message="Upstox broker is unavailable.",
            )
        if self.submit_scenario == "failed":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.INTERNAL,
                message="Upstox client internal failure.",
            )
        if self.submit_scenario == "unknown":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message="Upstox returned an unknown outcome.",
            )
        if self.submit_scenario == "rejected":
            return UpstoxPlaceOrderResponse(
                status="error",
                order_data=None,
                error_code="UDAPI1000",
                error_message="Upstox rejected the order.",
            )
        if self.submit_scenario == "validation_failure":
            return UpstoxPlaceOrderResponse(
                status="error",
                order_data=None,
                error_code="UDAPI1008",
                error_message="Price required (validation failure).",
            )
        if self.submit_scenario == "insufficient_funds":
            return UpstoxPlaceOrderResponse(
                status="error",
                order_data=None,
                error_code="UDAPI100041",
                error_message="Insufficient funds / margin.",
            )
        if self.submit_scenario == "invalid_instrument":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.INVALID_INSTRUMENT,
                message="Upstox instrument token not found.",
            )
        if self.submit_scenario == "invalid_order_type":
            return UpstoxPlaceOrderResponse(
                status="error",
                order_data=None,
                error_code="UDAPI1056",
                error_message="Invalid order type.",
            )
        if self.submit_scenario == "internal":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.INTERNAL,
                message="Upstox client internal failure.",
            )
        # Success-family scenarios: accepted / submitted / duplicate / restart /
        # filled / partially_filled / cancelled (submission-time).
        order_id = _mock_order_id(request.client_order_id, "place", self.submit_scenario)
        self._orders[request.tag] = UpstoxOrderStateResponse(
            order_id=order_id,
            tag=request.tag,
            status=UpstoxOrderState.OPEN,
            reason="Upstox accepted the order (mocked).",
        )
        if self.submit_scenario == "duplicate":
            return UpstoxPlaceOrderResponse(
                status="success",
                order_data=UpstoxOrderData(order_ids=(order_id,)),
            )
        if self.submit_scenario == "restart":
            return UpstoxPlaceOrderResponse(
                status="success",
                order_data=UpstoxOrderData(order_ids=(order_id,)),
            )
        if self.submit_scenario == "submitted":
            # Confirmed SUBMITTED (pending) outcome -- the lifecycle stays
            # in-flight (not terminal), allowing duplicate-guard tests.
            return UpstoxOrderStateResponse(
                order_id=order_id,
                tag=request.tag,
                status=UpstoxOrderState.OPEN,
                reason="Upstox confirmed the order is submitted (pending).",
            )
        # Default: accepted (and filled/partially_filled/cancelled map to
        # accepted at submission time -- the state is confirmed later).
        return UpstoxPlaceOrderResponse(
            status="success",
            order_data=UpstoxOrderData(order_ids=(order_id,)),
        )

    # ---------------------------------------------------------
    # GET ORDER (reconciliation)
    # ---------------------------------------------------------

    def get_order(
        self, tag: str, order_id: str | None = None
    ) -> UpstoxOrderStateResponse | UpstoxClientFailure:
        """Look up an order by tag (primary) or broker order id (fallback)."""

        self.reconciliations.append((tag, order_id))
        if not self._token_available():
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION,
                message="Upstox execution access token is unavailable (fail closed).",
            )
        if self.reconcile_scenario == "reconcile_unknown":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message="Upstox reconciliation could not determine the outcome.",
            )
        if self.reconcile_scenario == "reconcile_rejected":
            return UpstoxOrderStateResponse(
                order_id=order_id or _mock_order_id(tag, "reconcile", "rejected"),
                tag=tag,
                status=UpstoxOrderState.REJECTED,
                reason="Upstox reconciliation confirmed rejection.",
            )
        if self.reconcile_scenario == "malformed_response":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message="Upstox reconciliation returned a malformed response.",
            )
        if self.reconcile_scenario == "rate_limit":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.RATE_LIMIT,
                message="Upstox reconciliation rate limited (retry later).",
            )
        if self.reconcile_scenario == "broker_unavailable":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.BROKER_UNAVAILABLE,
                message="Upstox broker unavailable during reconciliation.",
            )
        # Default: reconcile_accepted (or a recorded order book entry).
        recorded = self._orders.get(tag)
        if recorded is not None:
            return recorded
        return UpstoxOrderStateResponse(
            order_id=order_id or _mock_order_id(tag, "reconcile", "accepted"),
            tag=tag,
            status=UpstoxOrderState.ACCEPTED,
            reason="Upstox reconciliation confirmed the order was accepted.",
        )

    # ---------------------------------------------------------
    # CANCEL ORDER
    # ---------------------------------------------------------

    def cancel_order(self, order_id: str) -> UpstoxCancelResponse | UpstoxClientFailure:
        """Request cancellation of an order by broker order id."""

        self.cancels.append(order_id)
        if not self._token_available():
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION,
                message="Upstox execution access token is unavailable (fail closed).",
            )
        if self.cancel_scenario == "cancellation_timeout":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.TIMEOUT,
                message="Upstox cancellation request timed out (ambiguous).",
            )
        if self.cancel_scenario == "cancellation_race_fill":
            return UpstoxCancelResponse(
                status="error",
                order_id=order_id,
            )
        if self.cancel_scenario == "cancelled":
            return UpstoxCancelResponse(status="success", order_id=order_id)
        if self.cancel_scenario == "rejected":
            return UpstoxCancelResponse(status="error", order_id=order_id)
        # Default: accepted cancellation.
        return UpstoxCancelResponse(status="success", order_id=order_id)

    # ---------------------------------------------------------
    # HEALTH
    # ---------------------------------------------------------

    def check_health(self) -> bool:
        """Return whether the client is healthy (no network in 17.7)."""
        return self.health_ok and self._token_available()

    # ---------------------------------------------------------
    # ORDER BOOK (deterministic, for reconcile-by-tag tests)
    # ---------------------------------------------------------

    def record_order(self, response: UpstoxOrderStateResponse) -> None:
        """Record an order-book entry keyed by tag (deterministic)."""
        self._orders[response.tag] = response

    @property
    def orders(self) -> tuple[UpstoxOrderStateResponse, ...]:
        """Deterministically ordered snapshot of recorded order-book entries."""
        return tuple(sorted(self._orders.values(), key=lambda r: (r.tag, r.order_id)))


def _mock_order_id(client_order_id: str, operation: str, scenario: str) -> str:
    """Deterministic mock broker order id (test-only; not a real broker id)."""

    import hashlib

    payload = f"{client_order_id}|{operation}|{scenario}".encode("utf-8")
    return "upstox-mock-" + hashlib.sha256(payload).hexdigest()[:16]


__all__ = [
    "MOCK_UPSTOX_CLIENT_SCENARIOS",
    "MockUpstoxBrokerClient",
    "UpstoxBrokerClient",
    "redact_sensitive",
]
