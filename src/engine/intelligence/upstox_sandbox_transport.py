"""Upstox Sandbox read-only HTTP transport (Checkpoint 18.2).

This module is the FIRST REAL-HTTP boundary of the execution architecture. It
implements the frozen :class:`UpstoxBrokerClient` protocol (Checkpoint 17.7)
but ONLY for READ-ONLY operations against the controlled Upstox Sandbox /
read-only environment. It is the ONLY module allowed to construct Upstox API
URLs and attach the ``Authorization: Bearer`` header.

CRITICAL SAFETY RULES (Checkpoint 18.2):

* READ-ONLY ONLY: this transport performs NO order-affecting operation.
  ``place_order`` and ``cancel_order`` RAISE ``ValueError`` -- this transport
  can never create, modify, or cancel an order.
* The credential is obtained ONLY from an injected
  :class:`UpstoxCredentialProvider` at this network boundary. The transport
  never stores the token, never logs it, never persists it, never includes it
  in an exception, and scrubs it from every message via
  ``redact_sensitive``.
* The token is attached ONLY to the ``Authorization`` header of the request
  object built here; the request object never leaves this module and is never
  serialized into any audit / persistence / result.
* The core engine (intelligence/domain/persistence layers) gains NO
  requests/httpx/urllib calls, NO raw HTTP logic, NO bearer-token
  construction, NO Upstox URLs, and NO HTTP response parsing. ALL of that is
  confined to this transport boundary.
* Every response is validated before it leaves this module: HTTP status
  handling, success/error envelope handling, missing/unexpected fields,
  wrong types, empty arrays, multiple records, missing broker order id,
  unknown status, unknown error code, authentication/authorization failure,
  rate-limit, timeout, and network failure all FAIL CLOSED into a tagged
  :class:`UpstoxClientFailure` (ambiguous outcomes -> ``UNKNOWN_OUTCOME`` /
  ``TIMEOUT`` / ``MALFORMED_RESPONSE``; never a fabricated success).
* No automatic retry of any kind. A single read is attempted once per call.

The endpoints used here are READ-ONLY and were verified from the official
Upstox developer documentation (Checkpoint 18.2):

* ``GET /v2/user/profile``  -- Get Profile (read-only identity)
* ``GET /v2/order/details`` -- Get Order Details (read-only order status)
* ``GET /v2/order/history`` -- Get Order History (read-only; array; by
  ``order_id`` OR ``tag``)

The base URL is a configuration value (``base_url``); the default is the
documented API base ``https://api.upstox.com``. The 17.8 documentation
recorded the sandbox place-order base ``https://sandbox.upstox.com``; for the
read-only endpoints the official docs show ``https://api.upstox.com``. The
base URL is never hard-coded into the core.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from engine.intelligence.upstox_broker_client import (
    UpstoxBrokerClient,
    redact_sensitive,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxCancelResponse,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
    UpstoxPlaceOrderResponse,
)

#: Read-only endpoint categories (audit vocabulary).
ENDPOINT_PROFILE = "user_profile"
ENDPOINT_ORDER_DETAILS = "order_details"
ENDPOINT_ORDER_HISTORY = "order_history"

#: Default base URL for the Upstox API (read-only endpoints; official docs).
DEFAULT_UPSTOX_API_BASE_URL = "https://api.upstox.com"
#: API path prefix for the V2 read-only endpoints.
DEFAULT_UPSTOX_API_PATH = "/v2"

#: User-Agent sent on every request (the Upstox gateway rejects urllib's
#: default ``Python-urllib/x.y`` UA -- mirrors the historical-provider fix).
UPSTOX_SANDBOX_USER_AGENT = "python-urllib/upstox-sandbox-readonly-transport"


class UpstoxTransportError(Exception):
    """Transport-level exception carrying a tagged :class:`UpstoxClientFailure`.

    ``UpstoxClientFailure`` is a plain frozen dataclass (not an exception);
    this wrapper lets the transport propagate a failure from ``_execute`` to
    the read-only operation methods, which catch it and RETURN the tagged
    failure. The failure's ``message`` has already been redacted.
    """

    def __init__(self, failure: UpstoxClientFailure) -> None:
        if not isinstance(failure, UpstoxClientFailure):
            raise TypeError("failure must be an UpstoxClientFailure.")
        super().__init__(redact_sensitive(failure.message))
        self.failure = failure


def _decode_body(data: bytes, encoding: str) -> str:
    """Decode a raw HTTP body honoring ``Content-Encoding``.

    urllib does not auto-decompress ``gzip`` / ``deflate`` responses; the
    transport requests ``gzip, deflate`` so it must decode them itself.
    Deterministic and safe on any body.
    """

    if not data:
        return ""
    lowered = (encoding or "").lower()
    if lowered == "gzip":
        return gzip.decompress(data).decode("utf-8", errors="replace")
    if lowered in ("deflate", "x-deflate"):
        try:
            return zlib.decompress(data).decode("utf-8", errors="replace")
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS).decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class UpstoxProfileResponse:
    """Adapter-owned read-only profile response (masked, no sensitive values).

    Only non-sensitive identity/capability facts are carried: broker,
    user_type, exchanges, products, order_types and active state. The
    account identifier (UCC) is NEVER stored -- only a boolean flag records
    whether the broker returned one (``user_id_present``), so no sensitive
    account information can leak into audit / persistence / logs.
    """

    broker: str
    user_type: str
    exchanges: tuple[str, ...]
    products: tuple[str, ...]
    order_types: tuple[str, ...]
    is_active: bool
    user_id_present: bool = False

    def __post_init__(self) -> None:
        if not self.broker or not self.broker.strip():
            raise ValueError("broker must be a non-empty string.")
        if not isinstance(self.user_type, str):
            raise TypeError("user_type must be a string.")
        for label, values in (
            ("exchanges", self.exchanges),
            ("products", self.products),
            ("order_types", self.order_types),
        ):
            if not isinstance(values, tuple) or not all(
                isinstance(v, str) for v in values
            ):
                raise TypeError(f"{label} must be a tuple of strings.")

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection (no sensitive values)."""
        return {
            "broker": self.broker,
            "user_type": self.user_type,
            "exchanges": tuple(sorted(self.exchanges)),
            "products": tuple(sorted(self.products)),
            "order_types": tuple(sorted(self.order_types)),
            "is_active": bool(self.is_active),
            "user_id_present": bool(self.user_id_present),
        }


def _coerce_order_state(raw: str) -> UpstoxOrderState:
    """Coerce a raw Upstox order-status string into the enum.

    Unknown / unrecognized status strings are NEVER guessed: they coerce to
    ``UNKNOWN`` so the caller maps them to an unknown (reconcile) outcome.
    Mirrors the adapter's ``_parse_order_state`` using the enum's own
    coercion (no domain logic duplicated).
    """

    if not isinstance(raw, str) or not raw.strip():
        return UpstoxOrderState.UNKNOWN
    try:
        return UpstoxOrderState(raw.strip().lower())
    except ValueError:
        return UpstoxOrderState.UNKNOWN


class UpstoxSandboxTransport:
    """Real-HTTP read-only Upstox Sandbox transport (implements the protocol).

    Implements the frozen :class:`UpstoxBrokerClient` protocol for the
    read-only surface (``get_order`` / ``check_health``) plus the dedicated
    read-only ``get_profile``. Order-affecting protocol methods
    (``place_order`` / ``cancel_order``) RAISE ``ValueError``.

    The HTTP layer is injectable (``urlopen``) so tests are fully
    deterministic and network-free; production uses ``urllib.request.urlopen``
    at this transport boundary only.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_UPSTOX_API_BASE_URL,
        api_path: str = DEFAULT_UPSTOX_API_PATH,
        timeout_seconds: int = 30,
        credential_provider: Any = None,
        urlopen: Callable[..., Any] | None = None,
        user_agent: str = UPSTOX_SANDBOX_USER_AGENT,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string.")
        if not isinstance(api_path, str) or not api_path.strip():
            raise ValueError("api_path must be a non-empty string.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path if api_path.startswith("/") else "/" + api_path
        self.timeout_seconds = int(timeout_seconds)
        self.credential_provider = credential_provider
        self.urlopen = urlopen if urlopen is not None else urllib.request.urlopen
        self.user_agent = user_agent

    # ---------------------------------------------------------
    # CREDENTIAL GATE (fail closed; token never leaves this module)
    # ---------------------------------------------------------

    def _token(self) -> str:
        if self.credential_provider is None:
            return ""
        value = self.credential_provider.get_access_token()
        return value if isinstance(value, str) else ""

    # ---------------------------------------------------------
    # REQUEST BUILDING (URL + headers; never logged/serialized)
    # ---------------------------------------------------------

    def _build_request(self, path: str, query: Mapping[str, str]) -> urllib.request.Request:
        token = self._token()
        if not token:
            raise ValueError(
                "Upstox execution access token is unavailable (fail closed; "
                "no read-only request is issued without a credential)."
            )
        url = f"{self.base_url}{self.api_path}{path}"
        if query:
            parts = "&".join(
                f"{urllib.parse.quote(str(k), safe='')}="
                f"{urllib.parse.quote(str(v), safe='')}"
                for k, v in sorted(query.items())
            )
            url = f"{url}?{parts}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        return request

    # ---------------------------------------------------------
    # HTTP EXECUTION + RESPONSE VALIDATION (fail closed)
    # ---------------------------------------------------------

    def _execute(self, path: str, query: Mapping[str, str]) -> tuple[int, str, Mapping[str, str]]:
        """Perform ONE read-only GET and return (status, body, headers).

        Raises:
            :class:`UpstoxClientFailure` for every transport/parse failure
            (timeout / network / malformed / auth / rate-limit / 5xx / 4xx).
        """

        try:
            request = self._build_request(path, query)
            response = self.urlopen(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            return self._classify_http_error(exc)
        except urllib.error.URLError as exc:
            reason = (
                str(exc.reason)
                if getattr(exc, "reason", None) is not None
                else "network failure"
            )
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.NETWORK,
                message=redact_sensitive(
                    f"Upstox read-only request failed at the network: {reason}"
                ),
            )
            raise UpstoxTransportError(failure) from exc
        except socket.timeout as exc:
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.TIMEOUT,
                message=redact_sensitive(
                    "Upstox read-only request timed out (ambiguous; reconcile before retry)."
                ),
            )
            raise UpstoxTransportError(failure) from exc
        except TimeoutError as exc:
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.TIMEOUT,
                message=redact_sensitive(
                    "Upstox read-only request timed out (ambiguous; reconcile before retry)."
                ),
            )
            raise UpstoxTransportError(failure) from exc
        except ValueError as exc:
            # Missing / malformed credential -> fail closed BEFORE any
            # request is issued. Redacted; never contains the token value.
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION,
                message=redact_sensitive(
                    "Upstox execution access token is unavailable (fail closed; "
                    "no read-only request is issued without a credential)."
                ),
            )
            raise UpstoxTransportError(failure) from exc
        except Exception as exc:
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.NETWORK,
                message=redact_sensitive(
                    f"Upstox read-only request failed at the transport: "
                    f"{type(exc).__name__}."
                ),
            )
            raise UpstoxTransportError(failure) from exc

        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "getcode", lambda: None)()
        status = int(status) if isinstance(status, int) else 0
        raw_body = getattr(response, "read", lambda: b"")()
        if not isinstance(raw_body, bytes):
            raw_body = b""
        encoding = ""
        try:
            headers = response.headers if hasattr(response, "headers") else {}
            encoding = (
                headers.get("Content-Encoding", "")
                if hasattr(headers, "get")
                else ""
            )
        except Exception:
            encoding = ""
        body = _decode_body(raw_body, encoding)
        return status, body, {}

    def _classify_http_error(self, exc: urllib.error.HTTPError) -> tuple[int, str, Mapping[str, str]]:
        status = int(getattr(exc, "code", 0) or 0)
        raw_body = b""
        try:
            raw_body = exc.read()
        except Exception:
            raw_body = b""
        encoding = ""
        try:
            headers = exc.headers if hasattr(exc, "headers") else {}
            encoding = (
                headers.get("Content-Encoding", "")
                if hasattr(headers, "get")
                else ""
            )
        except Exception:
            encoding = ""
        body = _decode_body(raw_body, encoding)
        return status, body, {}

    # ---------------------------------------------------------
    # ENVELOPE PARSING (success / error / malformed -> fail closed)
    # ---------------------------------------------------------

    def _token(self) -> str:
        if self.credential_provider is None:
            return ""
        value = self.credential_provider.get_access_token()
        return value if isinstance(value, str) else ""

    def _scrub_token(self, text: str) -> str:
        """Redact the ACTUAL token value from a message if it ever appears.

        The token value is only ever known inside this transport boundary; a
        defensive scrub guarantees it cannot surface in a failure message even
        if a broker response body echoed it. ``redact_sensitive`` handles the
        ``Bearer <token>`` and ``<NAME>=<value>`` patterns; this additionally
        removes the raw token value itself.
        """

        text = redact_sensitive(text)
        token = self._token()
        if token and token in text:
            text = text.replace(token, "<redacted>")
        return text

    def _parse_json(self, status: int, body: str) -> dict[str, Any]:
        if not body or not body.strip():
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive("Upstox returned an empty response body."),
            )
            raise UpstoxTransportError(failure)
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError) as exc:
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox returned a non-JSON response body (malformed)."
                ),
            )
            raise UpstoxTransportError(failure) from exc
        if not isinstance(parsed, dict):
            failure = UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox response body is not a JSON object (malformed)."
                ),
            )
            raise UpstoxTransportError(failure)
        return parsed

    def _classify_status_code(self, status: int) -> UpstoxErrorKind | None:
        """Map an HTTP status to a failure kind (None = 2xx handled below)."""
        if 200 <= status < 300:
            return None
        if status == 401:
            return UpstoxErrorKind.AUTHENTICATION
        if status == 403:
            return UpstoxErrorKind.AUTHORIZATION
        if status == 429:
            return UpstoxErrorKind.RATE_LIMIT
        if status == 404:
            return UpstoxErrorKind.UNKNOWN_OUTCOME
        if 500 <= status < 600:
            return UpstoxErrorKind.BROKER_UNAVAILABLE
        return UpstoxErrorKind.BROKER_REJECTION

    def _envelope_error(self, parsed: dict[str, Any]) -> UpstoxClientFailure:
        code = parsed.get("error_code", "") or ""
        message = parsed.get("error_message", "") or parsed.get("message", "") or "no detail"
        text = self._scrub_token(
            f"Upstox read-only error {code or 'unknown'}: {message}"
        )
        if code in ("UDAPI100010",):  # Order not found
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME, message=text
            )
        if code in ("UDAPI100058", "UDAPI100059", "UDAPI1010"):
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.VALIDATION, message=text
            )
        return UpstoxClientFailure(kind=UpstoxErrorKind.BROKER_REJECTION, message=text)

    # ---------------------------------------------------------
    # READ-ONLY OPERATIONS
    # ---------------------------------------------------------

    def get_profile(self) -> UpstoxProfileResponse | UpstoxClientFailure:
        """Read-only identity verification (GET /v2/user/profile)."""

        try:
            status, body, _ = self._execute("/user/profile", {})
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        kind = self._classify_status_code(status)
        if kind is not None:
            return UpstoxClientFailure(
                kind=kind,
                message=redact_sensitive(
                    f"Upstox read-only profile request failed with HTTP {status}."
                ),
            )
        try:
            parsed = self._parse_json(status, body)
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        if parsed.get("status") == "error":
            return self._envelope_error(parsed)
        if parsed.get("status") != "success":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox profile response has an unrecognized status envelope."
                ),
            )
        data = parsed.get("data")
        if not isinstance(data, dict):
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox profile response is missing a valid data object."
                ),
            )
        broker = data.get("broker", "")
        user_type = data.get("user_type", "")
        exchanges = tuple(
            sorted(str(v) for v in data.get("exchanges", []) if isinstance(v, str))
        )
        products = tuple(
            sorted(str(v) for v in data.get("products", []) if isinstance(v, str))
        )
        order_types = tuple(
            sorted(str(v) for v in data.get("order_types", []) if isinstance(v, str))
        )
        is_active = bool(data.get("is_active", False))
        user_id_present = bool(data.get("user_id")) and isinstance(
            data.get("user_id"), str
        )
        return UpstoxProfileResponse(
            broker=broker,
            user_type=user_type,
            exchanges=exchanges,
            products=products,
            order_types=order_types,
            is_active=is_active,
            user_id_present=user_id_present,
        )

    def get_order(
        self, tag: str, order_id: str | None = None
    ) -> UpstoxOrderStateResponse | UpstoxClientFailure:
        """Read-only order lookup / reconciliation (GET /v2/order/*).

        Primary key: ``order_id`` -> ``/v2/order/details`` (single record).
        Fallback: ``tag`` -> ``/v2/order/history`` (array reduced to the
        latest record for a single order; multiple distinct orders -> UNKNOWN;
        empty -> UNKNOWN_OUTCOME; never a fabricated match).
        """

        if order_id:
            return self._fetch_order_details(order_id)
        return self._fetch_order_history_by_tag(tag)

    def _fetch_order_details(self, order_id: str) -> UpstoxOrderStateResponse | UpstoxClientFailure:
        if not isinstance(order_id, str) or not order_id.strip():
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.VALIDATION,
                message=redact_sensitive("order_id must be a non-empty string."),
            )
        try:
            status, body, _ = self._execute("/order/details", {"order_id": order_id})
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        kind = self._classify_status_code(status)
        if kind is not None:
            return UpstoxClientFailure(
                kind=kind,
                message=redact_sensitive(
                    f"Upstox read-only order-details request failed with HTTP {status}."
                ),
            )
        try:
            parsed = self._parse_json(status, body)
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        if parsed.get("status") == "error":
            return self._envelope_error(parsed)
        if parsed.get("status") != "success":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox order-details response has an unrecognized status envelope."
                ),
            )
        data = parsed.get("data")
        if not isinstance(data, dict):
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox order-details response is missing a valid data object."
                ),
            )
        return self._record_from_dict(data)

    def _fetch_order_history_by_tag(self, tag: str) -> UpstoxOrderStateResponse | UpstoxClientFailure:
        if not isinstance(tag, str) or not tag.strip():
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.VALIDATION,
                message=redact_sensitive("tag must be a non-empty string."),
            )
        try:
            status, body, _ = self._execute("/order/history", {"tag": tag})
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        kind = self._classify_status_code(status)
        if kind is not None:
            return UpstoxClientFailure(
                kind=kind,
                message=redact_sensitive(
                    f"Upstox read-only order-history request failed with HTTP {status}."
                ),
            )
        try:
            parsed = self._parse_json(status, body)
        except UpstoxTransportError as exc:
            failure = exc.failure
            return failure
        if parsed.get("status") == "error":
            return self._envelope_error(parsed)
        if parsed.get("status") != "success":
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox order-history response has an unrecognized status envelope."
                ),
            )
        data = parsed.get("data")
        if not isinstance(data, list):
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox order-history response data must be an array."
                ),
            )
        if not data:
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message=redact_sensitive(
                    "Upstox order-history returned zero records for the tag "
                    "(no match; never a fabricated outcome)."
                ),
            )
        records: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                return UpstoxClientFailure(
                    kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                    message=redact_sensitive(
                        "Upstox order-history contains a non-object record."
                    ),
                )
            records.append(row)
        # Multiple DISTINCT orders for one tag -> ambiguous (never arbitrary).
        order_ids = {
            str(r.get("order_id", ""))
            for r in records
            if isinstance(r.get("order_id"), str) and r.get("order_id")
        }
        if len(order_ids) > 1:
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.MALFORMED_RESPONSE,
                message=redact_sensitive(
                    "Upstox order-history returned multiple distinct orders for "
                    "one tag; outcome ambiguous (requires manual review)."
                ),
            )
        # Reduce to the LATEST record (deterministic by order_timestamp then
        # list order) for the single order.
        latest = records[-1]
        for row in records:
            if _record_timestamp(row) > _record_timestamp(latest):
                latest = row
        return self._record_from_dict(latest)

    def _record_from_dict(self, data: dict[str, Any]) -> UpstoxOrderStateResponse:
        order_id = data.get("order_id", "")
        if not isinstance(order_id, str) or not order_id:
            return UpstoxOrderStateResponse(
                order_id=_unknown_order_id(),
                tag=str(data.get("tag", "") or ""),
                status=UpstoxOrderState.UNKNOWN,
                reason=self._scrub_token(
                    "Upstox order record is missing a broker order id (unknown)."
                ),
            )
        raw_status = data.get("status", "")
        state = _coerce_order_state(str(raw_status))
        quantity = _to_decimal_or_none(data.get("quantity"))
        filled_quantity = _to_decimal_or_none(data.get("filled_quantity"))
        return UpstoxOrderStateResponse(
            order_id=order_id,
            tag=str(data.get("tag", "") or ""),
            quantity=quantity,
            filled_quantity=filled_quantity,
            status=state,
            reason=self._scrub_token(
                f"Upstox read-only order record status {raw_status!r}."
            ),
        )

    def check_health(self) -> bool:
        """Read-only reachability / authentication probe (GET /v2/user/profile)."""

        result = self.get_profile()
        if isinstance(result, UpstoxClientFailure):
            return False
        return bool(result.is_active)

    # ---------------------------------------------------------
    # BLOCKED ORDER-AFFECTING OPERATIONS (READ-ONLY TRANSPORT)
    # ---------------------------------------------------------

    def place_order(
        self, request: UpstoxBrokerRequest
    ) -> UpstoxPlaceOrderResponse | UpstoxClientFailure:
        """BLOCKED: this transport is READ-ONLY and can never place an order."""

        raise ValueError(
            "UpstoxSandboxTransport is READ-ONLY: order placement is not "
            "permitted in Checkpoint 18.2 (no order can be created)."
        )

    def cancel_order(self, order_id: str) -> UpstoxCancelResponse | UpstoxClientFailure:
        """BLOCKED: this transport is READ-ONLY and can never cancel an order."""

        raise ValueError(
            "UpstoxSandboxTransport is READ-ONLY: order cancellation is not "
            "permitted in Checkpoint 18.2 (no order can be cancelled)."
        )


def _record_timestamp(row: dict[str, Any]) -> str:
    value = row.get("order_timestamp", "")
    return str(value) if isinstance(value, str) else ""


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal_value = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return None
    if not decimal_value.is_finite():
        return None
    return decimal_value


def _unknown_order_id() -> str:
    digest = hashlib.sha256(b"upstox-readonly-unknown-order").hexdigest()
    return f"unknown-{digest[:16]}"


__all__ = [
    "DEFAULT_UPSTOX_API_BASE_URL",
    "DEFAULT_UPSTOX_API_PATH",
    "ENDPOINT_ORDER_DETAILS",
    "ENDPOINT_ORDER_HISTORY",
    "ENDPOINT_PROFILE",
    "UPSTOX_SANDBOX_USER_AGENT",
    "UpstoxProfileResponse",
    "UpstoxSandboxTransport",
    "UpstoxTransportError",
    "_coerce_order_state",
    "_decode_body",
]