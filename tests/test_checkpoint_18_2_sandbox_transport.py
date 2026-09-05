"""Checkpoint 18.2 — Upstox Sandbox read-only transport tests (OFFLINE).

Deterministic, network-free tests of the real-HTTP read-only transport
:class:`~engine.intelligence.upstox_sandbox_transport.UpstoxSandboxTransport`
using an injected fake ``urlopen``. These tests prove the transport boundary:

* READ-ONLY ONLY: ``place_order`` / ``cancel_order`` raise ``ValueError``.
* The token is attached ONLY to the ``Authorization`` header of the request
  object built inside the transport and never surfaces in any result,
  exception, or audit field.
* Response validation fails closed: empty / malformed / wrong-shape /
  unknown-status / missing-order-id / multi-record / unknown-state all map
  to tagged :class:`UpstoxClientFailure` (ambiguous kinds -> UNKNOWN
  semantics).
* HTTP status / network / timeout handling (401/403/429/404/5xx/4xx,
  URLError, TimeoutError, malformed JSON, empty body).
* No automatic retry (a single read is attempted once).

NO network calls are made. NO credentials are used (fake token values only).
"""

from __future__ import annotations

import gzip
import json
import socket
import urllib.error
from decimal import Decimal

import pytest

from engine.intelligence.upstox_broker_models import (
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
)
from engine.intelligence.upstox_sandbox_transport import (
    DEFAULT_UPSTOX_API_BASE_URL,
    ENDPOINT_ORDER_DETAILS,
    ENDPOINT_ORDER_HISTORY,
    ENDPOINT_PROFILE,
    UPSTOX_SANDBOX_USER_AGENT,
    UpstoxProfileResponse,
    UpstoxSandboxTransport,
    _coerce_order_state,
    _decode_body,
)

#: Fake token value (deliberately non-secret test value; never a real token).
_FAKE_TOKEN = "test_sandbox_token_value_18_2"
_SECRET_MARKER = "SECRET_TOKEN_1234"


class _Provider:
    def __init__(self, token: str = _FAKE_TOKEN) -> None:
        self._token = token if isinstance(token, str) else ""

    def get_access_token(self) -> str:
        return self._token


class _FakeHeaders(dict):
    def __init__(self, **kw: object) -> None:
        super().__init__({k.replace("_", "-"): v for k, v in kw.items()})

    def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
        value = dict.get(self, key)
        if value is None:
            value = dict.get(self, key.lower())
        if value is None:
            value = dict.get(self, key.replace("-", "_"))
        return str(value) if value is not None else default


class _FakeResponse:
    def __init__(self, body: str | bytes, status: int = 200, encoding: str = "") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = int(status)
        self.headers = _FakeHeaders(Content_Encoding=encoding)  # type: ignore[assignment]

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status


class _FakeHTTPError:
    def __init__(self, status: int, body: str) -> None:
        self.code = int(status)
        self._body = body.encode("utf-8")
        self.headers = _FakeHeaders()

    def read(self) -> bytes:
        return self._body


def _profile_body(broker: str = "UPSTOX", **overrides: object) -> dict:
    data: dict[str, object] = {
        "broker": broker,
        "user_type": "individual",
        "exchanges": ["NSE"],
        "products": ["D"],
        "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
        "user_id": "******",
        "is_active": True,
    }
    data.update(overrides)
    return {"status": "success", "data": data}


def _order_details_body(order_id: str = "240108010445130", status: str = "complete") -> dict:
    return {
        "status": "success",
        "data": {
            "exchange": "NSE",
            "product": "D",
            "price": 571.0,
            "quantity": 1,
            "status": status,
            "tag": "uptag-abc123",
            "instrument_token": "NSE_EQ|INE062A01020",
            "placed_by": "******",
            "trading_symbol": "SBIN",
            "order_type": "LIMIT",
            "validity": "DAY",
            "trigger_price": 0.0,
            "transaction_type": "BUY",
            "average_price": 570.95,
            "filled_quantity": 1,
            "pending_quantity": 0,
            "order_id": order_id,
            "order_timestamp": "2026-09-04 13:25:13",
        },
    }


def _order_history_body(order_id: str = "240108010445130", statuses: tuple[str, ...] = ("open", "complete")) -> dict:
    rows: list[dict[str, object]] = []
    for idx, status in enumerate(statuses):
        rows.append(
            {
                "exchange": "NSE",
                "product": "D",
                "price": 571.35,
                "quantity": 1,
                "status": status,
                "tag": "uptag-abc123",
                "validity": "DAY",
                "average_price": 571.4 if status == "complete" else 0.0,
                "order_id": order_id,
                "order_type": "LIMIT",
                "order_timestamp": f"2026-09-04 13:25:{10 + idx:02d}",
                "filled_quantity": 1 if status == "complete" else 0,
                "transaction_type": "SELL",
                "variety": "SIMPLE",
            }
        )
    return {"status": "success", "data": rows}


def _recorded_urlopen(expected: str):
    """Return an urlopen that records requests and returns a configurable body."""

    calls: list[dict[str, str]] = []

    def urlopen(request: object, **kw: object):
        req = request
        full_url = getattr(req, "full_url", "")
        headers = getattr(req, "headers", {}) or {}
        auth = headers.get("Authorization", "") if hasattr(headers, "get") else ""
        calls.append(
            {
                "url": str(full_url),
                "auth_present": auth.startswith("Bearer "),
                "auth_value": auth,
            }
        )
        return _FakeResponse(expected)

    return urlopen, calls


# ============================================================
# A. READ-ONLY BOUNDARY
# ============================================================


class TestReadOnlyBoundary:
    def test_place_order_blocked(self) -> None:
        from engine.intelligence.upstox_broker_models import UpstoxBrokerRequest

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=lambda *a, **k: _FakeResponse("")
        )
        request = UpstoxBrokerRequest(
            instrument_token="NSE_EQ|INE062A01020",
            transaction_type=__import__(
                "engine.intelligence.upstox_broker_models", fromlist=["UpstoxTransactionType"]
            ).UpstoxTransactionType.BUY,
            quantity=Decimal("1"),
            product=__import__(
                "engine.intelligence.upstox_broker_models", fromlist=["UpstoxProduct"]
            ).UpstoxProduct.D,
            validity=__import__(
                "engine.intelligence.upstox_broker_models", fromlist=["UpstoxValidity"]
            ).UpstoxValidity.DAY,
            order_type=__import__(
                "engine.intelligence.upstox_broker_models", fromlist=["UpstoxOrderType"]
            ).UpstoxOrderType.LIMIT,
            price=Decimal("571.0"),
            trigger_price=None,
            tag="uptag-abc123",
            client_order_id="co-1234",
            idempotency_key="idem-1234",
            execution_mode="PAPER",
        )
        with pytest.raises(ValueError):
            transport.place_order(request)

    def test_cancel_order_blocked(self) -> None:
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=lambda *a, **k: _FakeResponse("")
        )
        with pytest.raises(ValueError):
            transport.cancel_order("oid-test")

    def test_missing_credential_fails_closed(self) -> None:
        # No provider -> no request is ever issued.
        def explode(*args: object, **kwargs: object) -> object:
            raise AssertionError("no request may be issued without a credential")

        transport = UpstoxSandboxTransport(urlopen=explode)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_empty_credential_fails_closed(self) -> None:
        def explode(*args: object, **kwargs: object) -> object:
            raise AssertionError("no request may be issued with an empty credential")

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(""), urlopen=explode
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_request_has_expected_headers(self) -> None:
        urlopen, calls = _recorded_urlopen(_profile_body())
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(_SECRET_MARKER), urlopen=urlopen
        )
        transport.get_profile()
        assert len(calls) == 1
        call = calls[0]
        assert call["url"].startswith("https://api.upstox.com/v2/user/profile")
        assert call["auth_present"] is True
        assert _SECRET_MARKER in call["auth_value"]  # it IS in the header buffer
        assert call["url"].endswith("/user/profile")

    def test_token_never_in_failure_message(self) -> None:
        urlopen, _ = _recorded_urlopen(
            json.dumps({"status": "error", "error_code": "UDAPI100045", "error_message": f"bad {_SECRET_MARKER}"})
        )
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(_SECRET_MARKER), urlopen=urlopen
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert _SECRET_MARKER not in result.message
        assert "Bearer" not in result.message

    def test_no_automatic_retry(self) -> None:
        count: list[int] = []

        def urlopen(request: object, **kwargs: object):
            count.append(1)
            raise urllib.error.URLError("connection refused")

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=urlopen
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.NETWORK
        assert len(count) == 1  # exactly ONE attempt, no retry loop


# ============================================================
# B. PROFILE RESPONSE VALIDATION
# ============================================================


class TestProfileValidation:
    def _transport(self, body: str | bytes, status: int = 200) -> UpstoxSandboxTransport:
        return UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=lambda *a, **k: _FakeResponse(body, status=status),
        )

    def test_valid_profile(self) -> None:
        transport = self._transport(json.dumps(_profile_body()))
        result = transport.get_profile()
        assert isinstance(result, UpstoxProfileResponse)
        assert result.broker == "UPSTOX"
        assert result.is_active is True
        assert result.user_id_present is True
        assert "NSE" in result.exchanges
        assert "LIMIT" in result.order_types

    def test_profile_user_id_masked_still_flag(self) -> None:
        transport = self._transport(json.dumps(_profile_body(user_id="UCC12345")))
        result = transport.get_profile()
        assert isinstance(result, UpstoxProfileResponse)
        # The value itself is NEVER retained -- only the boolean.
        assert result.user_id_present is True
        assert not any("UCC12345" in v for v in result.to_dict().values() if isinstance(v, str))

    def test_profile_error_envelope(self) -> None:
        body = {"status": "error", "error_code": "UDAPI100058", "error_message": "No segments"}
        result = self._transport(json.dumps(body)).get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.VALIDATION

    def test_profile_unknown_envelope(self) -> None:
        result = self._transport(json.dumps({"status": "weird"})).get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_profile_missing_data(self) -> None:
        result = self._transport(json.dumps({"status": "success"})).get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_profile_data_not_mapping(self) -> None:
        result = self._transport(json.dumps({"status": "success", "data": []})).get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_profile_empty_body(self) -> None:
        result = self._transport("").get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_profile_non_json(self) -> None:
        result = self._transport("<html>not json</html>").get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_profile_json_array_not_object(self) -> None:
        result = self._transport("[1,2,3]").get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE


# ============================================================
# C. ORDER DETAILS / HISTORY VALIDATION + RECONCILIATION MAPPING
# ============================================================


class TestOrderLookupValidation:
    def _details_transport(self, body: str | bytes, status: int = 200) -> UpstoxSandboxTransport:
        return UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=lambda *a, **k: _FakeResponse(body, status=status),
        )

    def test_row_detail_url_uses_order_id(self) -> None:
        urlopen, calls = _recorded_urlopen(json.dumps(_order_details_body()))
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=urlopen
        )
        result = transport.get_order(tag="", order_id="240108010445130")
        assert isinstance(result, UpstoxOrderStateResponse)
        assert "order_id=240108010445130" in calls[0]["url"]

    def test_history_url_uses_tag(self) -> None:
        urlopen, calls = _recorded_urlopen(json.dumps(_order_history_body()))
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=urlopen
        )
        result = transport.get_order(tag="uptag-abc123")
        assert isinstance(result, UpstoxOrderStateResponse)
        assert "tag=uptag-abc123" in calls[0]["url"]
        # History array reduces to the LATEST record -> complete -> FILLED
        assert result.status is UpstoxOrderState.COMPLETE

    def test_history_empty_array_is_unknown(self) -> None:
        result = self._details_transport(
            json.dumps({"status": "success", "data": []})
        ).get_order(tag="uptag-abc123")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.UNKNOWN_OUTCOME

    def test_history_multiple_distinct_orders_is_ambiguous(self) -> None:
        body = _order_history_body()
        body["data"] = [
            {**body["data"][0], "order_id": "AAAA1"},
            {**body["data"][1], "order_id": "BBBB2"},
        ]
        result = self._details_transport(json.dumps(body)).get_order(tag="uptag")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_details_missing_order_id_is_unknown(self) -> None:
        body = _order_details_body()
        body["data"].pop("order_id")
        result = self._details_transport(json.dumps(body)).get_order(
            tag="", order_id="x"
        )
        assert isinstance(result, UpstoxOrderStateResponse)
        assert result.status is UpstoxOrderState.UNKNOWN
        assert result.order_id.startswith("unknown-")

    def test_unknown_status_string_maps_unknown(self) -> None:
        body = _order_details_body(status="extra super unknown state")
        result = self._details_transport(json.dumps(body)).get_order(
            tag="", order_id="oid"
        )
        assert isinstance(result, UpstoxOrderStateResponse)
        assert result.status is UpstoxOrderState.UNKNOWN

    def test_known_status_strings_map(self) -> None:
        cases: dict[str, UpstoxOrderState] = {
            "complete": UpstoxOrderState.COMPLETE,
            "rejected": UpstoxOrderState.REJECTED,
            "cancelled": UpstoxOrderState.CANCELLED,
            "open": UpstoxOrderState.OPEN,
            "accepted": UpstoxOrderState.ACCEPTED,
            "partially_filled": UpstoxOrderState.PARTIALLY_FILLED,
        }
        for raw, expected in cases.items():
            assert _coerce_order_state(raw) is expected

    def test_details_error_envelope_order_not_found(self) -> None:
        body = {"status": "error", "error_code": "UDAPI100010", "error_message": "Order not found"}
        result = self._details_transport(json.dumps(body)).get_order(
            tag="", order_id="nonexistent"
        )
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.UNKNOWN_OUTCOME

    def test_details_validation_error(self) -> None:
        body = {"status": "error", "error_code": "UDAPI1010", "error_message": "Order id accepts only alphanumeric"}
        result = self._details_transport(json.dumps(body)).get_order(
            tag="", order_id="!!"
        )
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.VALIDATION


# ============================================================
# D. HTTP STATUS / NETWORK / TIMEOUT HANDLING (fail closed)
# ============================================================


class TestHttpAndNetworkFailures:
    def _transport(self, urlopen: object) -> UpstoxSandboxTransport:
        return UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=urlopen  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize(
        "status,expected_kind",
        [
            (401, UpstoxErrorKind.AUTHENTICATION),
            (403, UpstoxErrorKind.AUTHORIZATION),
            (429, UpstoxErrorKind.RATE_LIMIT),
            (404, UpstoxErrorKind.UNKNOWN_OUTCOME),
            (500, UpstoxErrorKind.BROKER_UNAVAILABLE),
            (503, UpstoxErrorKind.BROKER_UNAVAILABLE),
            (400, UpstoxErrorKind.BROKER_REJECTION),
        ],
    )
    def test_http_error_mapping(self, status: int, expected_kind: UpstoxErrorKind) -> None:
        def urlopen(request: object, **kwargs: object):
            raise urllib.error.HTTPError(
                "url", int(status), "err", {}, None
            )

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is expected_kind

    def test_http_error_with_body_parsed_but_kind_wins(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise urllib.error.HTTPError("url", 429, "rate limit", {}, None)

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.RATE_LIMIT

    def test_urlerror_network_failure(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise urllib.error.URLError("connection refused")

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.NETWORK

    def test_socket_timeout_ambiguous(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise socket.timeout("timed out")

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.TIMEOUT

    def test_timeout_error_ambiguous(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise TimeoutError("timed out")

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.TIMEOUT

    def test_arbitrary_exception_network(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise RuntimeError("boom")

        transport = self._transport(urlopen)
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.NETWORK

    def test_gzip_decoding(self) -> None:
        raw = gzip.compress(json.dumps(_profile_body()).encode("utf-8"))
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=lambda *a, **k: _FakeResponse(raw, encoding="gzip"),
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxProfileResponse)
        assert result.broker == "UPSTOX"

    def test_deflate_decoding(self) -> None:
        import zlib

        raw = zlib.compress(json.dumps(_profile_body()).encode("utf-8"))
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=lambda *a, **k: _FakeResponse(raw, encoding="deflate"),
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxProfileResponse)

    def test_decode_body_helpers(self) -> None:
        assert _decode_body(b"", "") == ""
        assert _decode_body(b"plain", "") == "plain"


# ============================================================
# E. CHECK HEALTH
# ============================================================


class TestCheckHealth:
    def test_health_ok(self) -> None:
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=lambda *a, **k: _FakeResponse(json.dumps(_profile_body())),
        )
        assert transport.check_health() is True

    def test_health_false_on_failure(self) -> None:
        def urlopen(request: object, **kwargs: object):
            raise urllib.error.URLError("refused")

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=urlopen
        )
        assert transport.check_health() is False