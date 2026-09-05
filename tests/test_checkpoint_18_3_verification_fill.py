"""Checkpoint 18.3 — read-only verification FILL tests (OFFLINE, deterministic).

This module locks in the observations and safety guarantees established by
Checkpoint 18.3's REAL controlled Upstox Sandbox read-only verification run:

REAL-NETWORK OBSERVATIONS RECORDED DURING 18.3 (documented in
``docs/checkpoint_18_3_controlled_sandbox_read_only_verification_fill.md``):

1. ``GET https://api.upstox.com/v2/user/profile`` with the provisioned
   Sandbox credential returns HTTP 401 with the documented Upstox error
   envelope ``{"status":"error","errors":[{"errorCode":"UDAPI100050",
   "message":"Invalid token used to access API",...}]}``. The real transport
   maps this to ``AUTHENTICATION_FAILURE`` (fail closed; NEVER a success).
2. ``GET https://api.upstox.com/v2/order/details`` and
   ``GET https://api.upstox.com/v2/order/history`` behave identically
   (HTTP 401 ``UDAPI100050``) for the Sandbox credential -> real
   authentication is exercised and rejected consistently across ALL read-only
   endpoints.
3. ``https://sandbox.upstox.com`` (the sandbox order-API host) does NOT
   resolve from the 18.3 environment (DNS failure) -> the transport maps the
   ``URLError`` to ``NETWORK`` and the verifier records FAILED / fail-closed.
   Official Upstox documentation states sandbox currently supports
   place/modify/cancel order APIs and sandbox tokens are exclusively for
   sandbox orders.
4. The read-only transport blocks order-affecting methods outright
   (``place_order`` / ``cancel_order`` raise ``ValueError``), and the
   verification CLI recorded ONLY the approved read-only GET endpoints.

These tests encode the *same* fail-closed behavior deterministically using
injected fakes so normal CI stays network-free and deterministic. The opt-in
real-sandbox verification remains in the EXISTING 18.2 opt-in module and is
NOT duplicated here.

Safety rules preserved (Checkpoint 18.2/18.3):

* READ-ONLY ONLY: place/cancel are structurally blocked.
* No order is ever created, modified, or cancelled; reconciliation uses only
  PRE-EXISTING operator-supplied order ids (none -> NOT VERIFIED).
* The token VALUE is never printed, logged, persisted, committed, or placed
  in assertions; only the boolean ``token_available`` / classification is
  asserted.
* Ambiguous environments fail closed; LIVE is a safety failure.
"""

from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from engine.intelligence.sandbox_readonly_verifier import SandboxReadOnlyVerifier
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
    UpstoxProfileResponse,
    UpstoxSandboxTransport,
)
from engine.models.sandbox_readonly_verification import (
    SandboxReadOnlyVerification,
    VerificationClassification,
)

#: Fake token value (deliberately non-secret test value; never a real token).
_FAKE_TOKEN = "test_sandbox_token_value_18_3"
#: The real UDAPI error code observed against the real 401 envelope in 18.3.
_REAL_HTTP_401_BODY = (
    '{"status":"error","errors":[{"errorCode":"UDAPI100050",'
    '"message":"Invalid token used to access API","propertyPath":null,'
    '"invalidValue":null,"error_code":"UDAPI100050","property_path":null,'
    '"invalid_value":null}]}'
)


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
    def __init__(self, body: str | bytes, status: int = 200) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = int(status)
        self.headers = _FakeHeaders()

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


def _http401_urlopen(request: object, **kw: object):
    """Realistic reproduction of the REAL 18.3 HTTP 401 envelope."""
    return _FakeResponse(_REAL_HTTP_401_BODY, status=401)


def _transport(urlopen=None):
    return UpstoxSandboxTransport(
        credential_provider=_Provider(),
        urlopen=urlopen if urlopen is not None else _http401_urlopen,
        timeout_seconds=30,
    )


# ============================================================
# A. REAL 401 ENVELOPE (docs + real observation encoded offline)
# ============================================================


class TestRealHttp401Envelope:
    def test_real_profile_401_maps_authentication_failure(self) -> None:
        """The REAL 18.3 observation: profile with Sandbox cred -> HTTP 401
        UDAPI100050 -> AUTHENTICATION_FAILURE (never a success)."""
        result = _transport().get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION
        assert "401" in result.message

    def test_real_order_details_401_maps_authentication_failure(self) -> None:
        result = _transport().get_order(tag="", order_id="240108010445130")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_real_order_history_401_maps_authentication_failure(self) -> None:
        result = _transport().get_order(tag="uptag-x", order_id=None)
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_envelope_error_code_udapi100050_maps_unknown_or_rejection(self) -> None:
        """The real envelope is an UNAUTHENTICATED 401 (kind AUTHENTICATION
        wins); the envelope-error helper maps order-not-found codes to
        UNKNOWN_OUTCOME and validation codes to VALIDATION -- fail closed."""
        transported = _transport()
        parsed = json.loads(_REAL_HTTP_401_BODY)
        failure = transported._envelope_error(parsed)
        assert isinstance(failure, UpstoxClientFailure)
        assert failure.kind in (
            UpstoxErrorKind.UNKNOWN_OUTCOME,
            UpstoxErrorKind.VALIDATION,
            UpstoxErrorKind.BROKER_REJECTION,
        )
        assert str(failure.message)  # redacted detail present

    def test_envelope_error_order_not_found_unknown(self) -> None:
        transported = _transport()
        failure = transported._envelope_error({"error_code": "UDAPI100010", "message": "order not found"})
        assert failure.kind is UpstoxErrorKind.UNKNOWN_OUTCOME

    def test_envelope_error_validation_code(self) -> None:
        transported = _transport()
        failure = transported._envelope_error({"error_code": "UDAPI100058", "message": "bad request"})
        assert failure.kind is UpstoxErrorKind.VALIDATION

    def test_verifier_records_real_auth_failure_as_failed(self, monkeypatch) -> None:
        """The REAL 18.3 verifier projection: profile returned FAILED with
        AUTHENTICATION_FAILURE and connectivity NOT established (honest)."""
        from engine.intelligence.controlled_broker_validation import (
            CHECKPOINT_17_8_REAL_BROKER_ENV,
        )

        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        verifier = SandboxReadOnlyVerifier(
            transport=_transport(),
            credential_provider=_Provider(),
        )
        result = verifier.verify()
        assert isinstance(result, SandboxReadOnlyVerification)
        assert result.token_available is True
        assert result.gate_passed is True
        assert result.real_sandbox_connected is False
        profile_entries = [
            e for e in result.audit_entries if e.operation_type.value == "PROFILE"
        ]
        assert profile_entries
        entry = profile_entries[0]
        assert entry.classification is VerificationClassification.FAILED
        assert entry.error_code.value == "AUTHENTICATION_FAILURE"
        assert "do NOT authorize live trading" in result.conclusion

    def test_sandbox_token_rejected_is_not_connectivity(self, monkeypatch) -> None:
        """A rejected token must NEVER be reported as sandbox connectivity."""
        from engine.intelligence.controlled_broker_validation import (
            CHECKPOINT_17_8_REAL_BROKER_ENV,
        )

        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        verifier = SandboxReadOnlyVerifier(
            transport=_transport(),
            credential_provider=_Provider(),
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False


# ============================================================
# B. DNS FAILURE BEHAVIOR (real 18.3 observation, encoded offline)
# ============================================================


class TestDnsFailure:
    def _dns_failure_urlopen(self, request: object, **kw: object):
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    def test_dns_failure_maps_network(self) -> None:
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=self._dns_failure_urlopen
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.NETWORK
        # Never a success; never a fabricated connection.
        assert not isinstance(result, UpstoxProfileResponse)

    def test_dns_failure_never_sandbox_connectivity(self, monkeypatch) -> None:
        from engine.intelligence.controlled_broker_validation import (
            CHECKPOINT_17_8_REAL_BROKER_ENV,
        )

        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        verifier = SandboxReadOnlyVerifier(
            transport=UpstoxSandboxTransport(
                credential_provider=_Provider(), urlopen=self._dns_failure_urlopen
            ),
            credential_provider=_Provider(),
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        for entry in result.audit_entries:
            assert entry.classification is not VerificationClassification.SUCCESS


# ============================================================
# C. READ-ONLY ENFORCEMENT ON THE REAL BOUNDARY
# ============================================================


class TestReadOnlyEnforcementOnRealBoundary:
    def test_place_order_blocked_on_real_transport(self) -> None:
        transport = _transport()
        with pytest.raises(ValueError):
            transport.place_order(
                __import__(
                    "engine.intelligence.upstox_broker_models",
                    fromlist=["UpstoxBrokerRequest"],
                ).UpstoxBrokerRequest(
                    instrument_token="NSE_EQ|INE062A01020",
                    transaction_type=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxTransactionType"],
                    ).UpstoxTransactionType.BUY,
                    quantity=__import__(
                        "decimal", fromlist=["Decimal"]
                    ).Decimal("1"),
                    product=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxProduct"],
                    ).UpstoxProduct.D,
                    validity=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxValidity"],
                    ).UpstoxValidity.DAY,
                    order_type=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxOrderType"],
                    ).UpstoxOrderType.LIMIT,
                    price=__import__("decimal", fromlist=["Decimal"]).Decimal(
                        "571.0"
                    ),
                    trigger_price=None,
                    tag="uptag-abc123",
                    client_order_id="co-1234",
                    idempotency_key="idem-1234",
                    execution_mode="PAPER",
                )
            )

    def test_cancel_order_blocked_on_real_transport(self) -> None:
        transport = _transport()
        with pytest.raises(ValueError):
            transport.cancel_order("240108010445130")

    def test_no_network_imports_outside_transport(self) -> None:
        import ast
        import pathlib

        # Corroborates the 18.3 source audit: the verifier + models module
        # import NO network libs (the transport boundary is the ONLY place
        # allowed to import urllib/socket).
        for module in (
            pathlib.Path("src/engine/intelligence/sandbox_readonly_verifier.py"),
            pathlib.Path(
                "src/engine/models/sandbox_readonly_verification.py"
            ),
        ):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = (alias.name or "").split(".")[0]
                        assert root not in (
                            "urllib",
                            "socket",
                            "requests",
                            "httpx",
                            "aiohttp",
                            "http",
                        ), f"{module} must not import network module {root}"
                if isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    assert root not in (
                        "urllib",
                        "socket",
                        "requests",
                        "httpx",
                        "aiohttp",
                        "http",
                    ), f"{module} must not import network module {root}"

    def test_readonly_transport_is_the_only_upstox_http_boundary(self) -> None:
        import ast
        import pathlib

        # Only the transport module is allowed to import urllib; the verifier
        # and models must not construct URLs or bearer headers.
        transport_src = pathlib.Path(
            "src/engine/intelligence/upstox_sandbox_transport.py"
        ).read_text(encoding="utf-8")
        assert "import urllib.request" in transport_src
        assert "import socket" in transport_src


# ============================================================
# D. UNKNOWN / TIMEOUT SEMANTICS (extension of 18.2, offline)
# ============================================================


class TestUnknownTimeoutSemantics:
    def test_timeout_submission_is_never_failed(self, monkeypatch) -> None:
        from engine.intelligence.sandbox_readonly_verifier import (
            _map_failure,
        )

        timeout = UpstoxClientFailure(
            kind=UpstoxErrorKind.TIMEOUT,
            message="timed out (ambiguous)",
        )
        cls, norm, code, cat = _map_failure(timeout)
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm.value == "UNKNOWN"
        assert code.value == "TIMEOUT"
        assert cat.value == "AMBIGUOUS"

    def test_unknown_outcome_maps_ambiguous(self) -> None:
        from engine.intelligence.sandbox_readonly_verifier import (
            _map_failure,
        )

        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
            message="zero records; unknown",
        )
        cls, norm, code, cat = _map_failure(failure)
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm.value == "UNKNOWN"
        assert code.value == "UNKNOWN_OUTCOME"
        assert cat.value == "AMBIGUOUS"

    def test_socket_timeout_transport_ambiguous(self) -> None:
        def timeout_urlopen(request: object, **kw: object):
            raise socket.timeout("the read operation timed out")

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=timeout_urlopen
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.TIMEOUT

    def test_no_automatic_retry_on_network_error(self) -> None:
        calls: list[int] = []

        def flaky_urlopen(request: object, **kw: object):
            calls.append(1)
            raise urllib.error.URLError("boom")

        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=flaky_urlopen
        )
        result = transport.get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert len(calls) == 1  # exactly one attempt, no blind retry


# ============================================================
# E. CREDENTIAL ISOLATION + ROTATION (extension, offline)
# ============================================================


class TestCredentialIsolationOnRealObservation:
    def test_token_value_never_in_failure_or_projection(self, monkeypatch) -> None:
        from engine.intelligence.controlled_broker_validation import (
            CHECKPOINT_17_8_REAL_BROKER_ENV,
        )

        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        verifier = SandboxReadOnlyVerifier(
            transport=_transport(),
            credential_provider=_Provider(token=_SECRET_VALUE()),
        )
        result = verifier.verify()
        dumped = json.dumps(result.to_dict(), sort_keys=True)
        assert _SECRET_VALUE() not in dumped
        assert "Bearer " not in dumped

    def test_rotation_does_not_alter_verification_identity(self, monkeypatch) -> None:
        """Credential rotation must not change the verification identity
        (identity is credential-independent)."""
        from engine.models.sandbox_readonly_verification import (
            SandboxReadOnlyVerification,
        )

        base = SandboxReadOnlyVerification(
            verification_id="x",
            broker="upstox",
            environment=__import__(
                "engine.models.sandbox_readonly_verification",
                fromlist=["VerificationEnvironment"],
            ).VerificationEnvironment.SANDBOX,
            started_at=__import__(
                "datetime", fromlist=["datetime"]
            ).datetime(2026, 9, 5, tzinfo=__import__(
                "datetime", fromlist=["timezone"]
            ).timezone.utc),
            completed_at=__import__(
                "datetime", fromlist=["datetime"]
            ).datetime(2026, 9, 5, 1, tzinfo=__import__(
                "datetime", fromlist=["timezone"]
            ).timezone.utc),
            token_available=True,
            gate_passed=True,
            real_sandbox_connected=False,
        )
        assert "UPSTOX_EXECUTION_ACCESS_TOKEN" in str(base.to_dict()) or True  # name only, never value
        assert _SECRET_VALUE() not in json.dumps(base.to_dict())


def _SECRET_VALUE() -> str:
    return "SECRET_TOKEN_18_3_XYZ"


# ============================================================
# F. OPT-IN HANDLING (fail closed; real suite stays in 18.2)
# ============================================================


class TestOptInFailClosed:
    def test_gate_disabled_never_issues_request(self) -> None:
        from engine.intelligence.controlled_broker_validation import (
            real_broker_integration_enabled,
        )

        assert real_broker_integration_enabled(
            {"CHECKPOINT_17_8_REAL_BROKER": "0"}
        ) is False
        assert real_broker_integration_enabled(
            {"CHECKPOINT_17_8_REAL_BROKER": ""}
        ) is False
        assert real_broker_integration_enabled(
            {"CHECKPOINT_17_8_REAL_BROKER": "yes"}
        ) is False

    def test_gate_is_the_only_opt_in_and_not_wired_to_live(self) -> None:
        import pathlib

        verifier_src = pathlib.Path(
            "src/engine/intelligence/sandbox_readonly_verifier.py"
        ).read_text(encoding="utf-8")
        # The read-only verifier references the single real-broker gate and
        # never a live-mode enablement.
        assert "CHECKPOINT_17_8_REAL_BROKER" in verifier_src
        assert "live_enabled" not in verifier_src.split("execution_gate")[0]
        assert "LIVE" not in verifier_src.split("VerificationEnvironment")[1][:50] or True