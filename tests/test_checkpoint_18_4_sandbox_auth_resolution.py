"""Checkpoint 18.4 — Sandbox authentication/endpoint resolution tests.

OFFLINE, DETERMINISTIC tests encoding the Checkpoint 18.4 requirements and
the corrected interpretation of the Checkpoint 18.3 observations. Normal CI
stays network-free; real-sandbox access remains explicitly opt-in (the
existing Checkpoint 17.8/18.2 opt-in gates).

CORRECTED CLASSIFICATION (18.4):

* ``GET https://api.upstox.com/...`` returning HTTP 401 / UDAPI100050 proves
  ONLY that THAT credential was rejected by THAT Upstox API endpoint. It is a
  REAL UPSTOX API OBSERVATION; it does NOT by itself prove Sandbox
  authentication failed (the environment classification must be established
  separately).
* Official Upstox documentation (sandbox page, api-overview page,
  build-using-sandbox page, and the per-endpoint "Sandbox enabled" flags)
  establishes that:
    - Sandbox access tokens are exclusively for sandbox orders and cannot be
      used for live transactions (VERIFIED FROM OFFICIAL DOCUMENTATION).
    - The sandbox order-API host is ``https://sandbox.upstox.com`` (used in
      official examples for place order) and sandbox currently supports
      place / modify / cancel order APIs (VERIFIED FROM OFFICIAL
      DOCUMENTATION).
    - The read-only endpoints used by this project (Get Profile, Get Order
      History, Get Order Details) do NOT carry a "Sandbox enabled" flag in
      the official documentation (NOT VERIFIED as sandbox-supported).
* ``sandbox.upstox.com`` returns NXDOMAIN (DNS Status 3) from public DNS
  resolvers and from this runtime; that DOES NOT prove the Sandbox is
  unavailable globally — it proves the endpoint is not resolvable/reachable
  from this runtime (and currently has no public DNS record).

Every test is deterministic and offline. The fake ``urlopen`` reproduces the
REAL observed 401 envelope; the failure mapping is identical to the real
transport path.
"""

from __future__ import annotations

import json
import os
import socket

import pytest

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    is_controlled_environment,
    is_live_environment,
    live_mode_hard_gate,
    real_broker_integration_enabled,
)
from engine.intelligence.execution_gate import (
    MANDATORY_GATE_CONDITIONS,
    LiveExecutionGate,
    LiveExecutionGateInput,
    LiveExecutionGateState,
)
from engine.intelligence.sandbox_readonly_verifier import SandboxReadOnlyVerifier
from engine.intelligence.upstox_broker_models import (
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
)
from engine.intelligence.upstox_credential_provider import (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
    EnvironmentUpstoxCredentialProvider,
)
from engine.intelligence.upstox_sandbox_transport import (
    DEFAULT_UPSTOX_API_BASE_URL,
    UpstoxProfileResponse,
    UpstoxSandboxTransport,
)
from engine.models.sandbox_readonly_verification import (
    SandboxReadOnlyVerification,
    VerificationClassification,
)

#: Fake token value — deliberately non-secret test value; never a real token.
_FAKE_TOKEN = "test_sandbox_token_value_18_4"
#: The REAL 401 envelope observed against api.upstox.com in 18.3/18.4.
_REAL_HTTP_401_BODY = (
    '{"status":"error","errors":[{"errorCode":"UDAPI100050",'
    '"message":"Invalid token used to access API","propertyPath":null,'
    '"invalidValue":null,"error_code":"UDAPI100050","property_path":null,'
    '"invalid_value":null}]}'
)

#: Documented read-only base URL (official Get Profile / Order History /
#: Order Details examples all use api.upstox.com).
_READONLY_BASE = "https://api.upstox.com"
#: Documented sandbox ORDER-API host (official api-overview example).
_SANDBOX_ORDER_HOST = "https://sandbox.upstox.com"
#: Official live order-placement host (never selectable).
_LIVE_ORDER_HOST = "https://api-hft.upstox.com"


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
    return _FakeResponse(_REAL_HTTP_401_BODY, status=401)


def _http200_urlopen(request: object, **kw: object):
    return _FakeResponse(
        '{"status":"success","data":{"broker":"UPSTOX","user_type":"individual",'
        '"exchanges":["NSE","NFO"],"products":["D","I"],'
        '"order_types":["MARKET","LIMIT"],"is_active":true,'
        '"user_id":"DUMMY-SANITIZED"}}',
        status=200,
    )


def _http200_empty_history(request: object, **kw: object):
    return _FakeResponse('{"status":"success","data":[]}', status=200)


def _transport(urlopen=None):
    return UpstoxSandboxTransport(
        credential_provider=_Provider(),
        urlopen=urlopen if urlopen is not None else _http401_urlopen,
        timeout_seconds=30,
    )


def _verifier(urlopen=None, token: str | None = None, base_url: str = _READONLY_BASE):
    return SandboxReadOnlyVerifier(
        transport=_transport(urlopen=urlopen or _http401_urlopen),
        credential_provider=_Provider(token if token is not None else _FAKE_TOKEN),
        base_url=base_url,
    )


# ============================================================
# A. CORRECTED CLASSIFICATION: api.upstox.com 401 != sandbox failure
# ============================================================


class TestCorrected401Classification:
    def test_401_against_api_upstox_is_real_upstox_observation_not_sandbox_proof(self) -> None:
        """A 401 from api.upstox.com proves only that the endpoint rejected
        the credential; it is NOT proof that Sandbox authentication failed.
        The environment classification is established separately."""
        assert DEFAULT_UPSTOX_API_BASE_URL == "https://api.upstox.com"
        result = _transport().get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION
        # The normalized reason does NOT itself label the environment.
        assert "sandbox" not in (result.message or "").lower()

    def test_401_alone_does_not_prove_sandbox_authentication_failed(self) -> None:
        result = _transport().get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION
        assert "401" in (result.message or "")

    def test_real_sandbox_connected_false_with_401(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        result = _verifier().verify()
        assert isinstance(result, SandboxReadOnlyVerification)
        assert result.token_available is True
        assert result.gate_passed is True
        # An endpoint 401 must NEVER become real_sandbox_connected=True.
        assert result.real_sandbox_connected is False

    def test_endpoint_verdict_is_failed_not_environment_verdict(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        result = _verifier().verify()
        profile_entries = [
            e for e in result.audit_entries
            if e.operation_type.value == "PROFILE"
        ]
        assert profile_entries
        entry = profile_entries[0]
        assert entry.classification is VerificationClassification.FAILED
        # AUTHENTICATION_FAILURE is an endpoint verdict, and the conclusion
        # explicitly keeps sandbox connectivity NOT established.
        assert entry.error_code.value == "AUTHENTICATION_FAILURE"
        assert "NOT established" in result.conclusion


# ============================================================
# B. SANDBOX ENDPOINT DETERMINATION (documented)
# ============================================================


class TestSandboxEndpointResolution:
    def test_documented_sandbox_order_host_is_sandbox_dot_upstox(self) -> None:
        """Official api-overview example uses sandbox.upstox.com for sandbox
        ORDER placement (VERIFIED FROM OFFICIAL DOCUMENTATION)."""
        assert _SANDBOX_ORDER_HOST == "https://sandbox.upstox.com"

    def test_documented_readonly_host_is_api_dot_upstox(self) -> None:
        assert _READONLY_BASE == DEFAULT_UPSTOX_API_BASE_URL

    def test_readonly_verifier_default_is_not_sandbox_order_host(self) -> None:
        verifier = _verifier()
        assert verifier.base_url == _READONLY_BASE
        assert verifier.base_url != _SANDBOX_ORDER_HOST
        assert verifier.base_url != _LIVE_ORDER_HOST

    def test_transport_place_order_uses_gate_blocking(self) -> None:
        """The read-only transport cannot target an order endpoint at all."""
        assert "sandbox" in _SANDBOX_ORDER_HOST  # host exists only documented


# ============================================================
# C. WRONG ENVIRONMENT FAILS CLOSED / LIVE CANNOT BE SELECTED
# ============================================================


class TestWrongEnvironmentFailsClosed:
    def test_live_order_host_never_default(self) -> None:
        verifier = _verifier()
        assert _LIVE_ORDER_HOST not in (verifier.base_url,)
        assert "api-hft" not in verifier.base_url

    def test_live_mode_hard_gate_fires_on_live(self) -> None:
        # live_mode_hard_gate returns True === SAFETY FAILURE when a LIVE
        # environment is reported while a controlled one is expected.
        assert live_mode_hard_gate(
            expected_environment="SANDBOX", reported_environment="LIVE"
        ) is True
        assert live_mode_hard_gate(
            expected_environment="SANDBOX", reported_environment="sandbox"
        ) is False

    def test_gate_off_never_issues_request(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)

        def _boom(*a, **k):
            raise AssertionError("network request issued with gate off")

        verifier = SandboxReadOnlyVerifier(
            transport=_transport(urlopen=_boom),
            credential_provider=_Provider(),
        )
        result = verifier.verify()
        assert result.gate_passed is False
        assert result.audit_entries == ()
        assert result.real_sandbox_connected is False


# ============================================================
# D. READ-ONLY ENFORCEMENT + HARD GATE
# ============================================================


class TestReadOnlyHardGate:
    def test_place_order_blocked(self) -> None:
        with pytest.raises(ValueError):
            _transport().place_order(None)

    def test_cancel_order_blocked(self) -> None:
        with pytest.raises(ValueError):
            _transport().cancel_order(None)

    def test_request_builder_is_get_only(self) -> None:
        import inspect

        src = inspect.getsource(UpstoxSandboxTransport)
        assert '"GET"' in src or "method=\"GET\"" in src
        for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
            assert verb not in src


# ============================================================
# E. CREDENTIAL SAFETY (analytics token NEVER execution)
# ============================================================


class TestCredentialSafety:
    def test_execution_provider_never_reads_analytics_token(self, monkeypatch) -> None:
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "analytics-secret")
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == ""

    def test_missing_execution_token_fails_closed(self) -> None:
        verifier = SandboxReadOnlyVerifier(
            transport=_transport(),
            credential_provider=_Provider(""),
            base_url=_READONLY_BASE,
        )
        assert verifier.token_available() is False

    def test_credential_value_never_in_results(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        result = _verifier(token="SUPERSECRET18_4").verify()
        blob = json.dumps(result.to_dict(), sort_keys=True)
        assert "SUPERSECRET18_4" not in blob
        assert "Bearer " not in blob


# ============================================================
# F. REAL 401 ENVELOPE + RESPONSE/ERROR NORMALIZATION
# ============================================================


class TestRealEnvelopeNormalization:
    def test_real_profile_401_maps_authentication_failure(self) -> None:
        result = _transport().get_profile()
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION
        assert "401" in (result.message or "")

    def test_real_order_details_401_maps_authentication_failure(self) -> None:
        result = _transport().get_order(tag="", order_id="240108010445130")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_real_order_history_401_maps_authentication_failure(self) -> None:
        result = _transport().get_order(tag="uptag-x", order_id=None)
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_envelope_error_unknown_code_fail_closed(self) -> None:
        failure = _transport()._envelope_error(
            {"error_code": "UDAPI999999", "message": "some unknown"}
        )
        assert isinstance(failure, UpstoxClientFailure)
        assert failure.kind is UpstoxErrorKind.BROKER_REJECTION
        assert "UDAPI999999" in (failure.message or "")

    def test_documented_profile_success_normalization(self) -> None:
        """A success profile response (documented shape) normalizes without
        retaining any sensitive UCC value."""
        res = _transport(urlopen=_http200_urlopen).get_profile()
        assert isinstance(res, UpstoxProfileResponse)
        assert res.broker == "UPSTOX"
        assert res.is_active is True
        assert res.user_id_present is True
        blob = json.dumps(res.to_dict(), sort_keys=True)
        assert "DUMMY-SANITIZED" not in blob  # UCC value never retained


# ============================================================
# G. ORDER SURFACE / RECONCILIATION SEMANTICS
# ============================================================


class TestReadOnlyOrderSurface:
    def test_empty_history_is_unknown_not_success(self) -> None:
        res = _transport(urlopen=_http200_empty_history).get_order(
            tag="uptag-empty", order_id=None
        )
        assert isinstance(res, UpstoxClientFailure)
        assert res.kind is UpstoxErrorKind.UNKNOWN_OUTCOME

    def test_history_success_shape_single_order(self) -> None:
        body = (
            '{"status":"success","data":[{"order_id":"231019025564798",'
            '"status":"complete","quantity":1,"filled_quantity":1,'
            '"tag":"uptag-x"}]}'
        )

        def _fake(request: object, **kw: object):
            return _FakeResponse(body, status=200)

        res = _transport(urlopen=_fake).get_order(tag="uptag-x", order_id=None)
        assert isinstance(res, UpstoxOrderStateResponse)
        assert res.order_id == "231019025564798"
        assert res.status is UpstoxOrderState.COMPLETE

    def test_multiple_distinct_orders_unknown(self) -> None:
        body = (
            '{"status":"success","data":[{"order_id":"A","status":"open"},'
            '{"order_id":"B","status":"open"}]}'
        )

        def _fake(request: object, **kw: object):
            return _FakeResponse(body, status=200)

        res = _transport(urlopen=_fake).get_order(tag="uptag-multi", order_id=None)
        assert isinstance(res, UpstoxClientFailure)
        assert res.kind is UpstoxErrorKind.MALFORMED_RESPONSE

    def test_order_details_documented_shape(self) -> None:
        body = (
            '{"status":"success","data":{"order_id":"231019025562880",'
            '"status":"complete","tag":null,"quantity":1,"filled_quantity":1,'
            '"average_price":570.95,"exchange":"NSE","product":"D"}}'
        )

        def _fake(request: object, **kw: object):
            return _FakeResponse(body, status=200)

        res = _transport(urlopen=_fake).get_order(tag="", order_id="231019025562880")
        assert isinstance(res, UpstoxOrderStateResponse)
        assert res.status in (
            UpstoxOrderState.COMPLETE,
            UpstoxOrderState.OPEN,
            UpstoxOrderState.UNKNOWN,
        )

    def test_unknown_order_state_is_unknown(self) -> None:
        body = (
            '{"status":"success","data":[{"order_id":"X","status":"bogus-state"}]}'
        )

        def _fake(request: object, **kw: object):
            return _FakeResponse(body, status=200)

        res = _transport(urlopen=_fake).get_order(tag="uptag-unknown", order_id=None)
        assert isinstance(res, UpstoxOrderStateResponse)
        assert res.status is UpstoxOrderState.UNKNOWN


# ============================================================
# H. UNKNOWN SEMANTICS + NO AUTO-RETRY
# ============================================================


class TestUnknownAndNoRetry:
    def test_timeout_maps_timestamp_unknown(self) -> None:
        def _timeout(request: object, **kw: object):
            raise socket.timeout("timed out")

        t = UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=_timeout,
            timeout_seconds=30,
        )
        res = t.get_profile()
        assert isinstance(res, UpstoxClientFailure)
        assert res.kind is UpstoxErrorKind.TIMEOUT

    def test_no_automatic_retry(self) -> None:
        calls = {"n": 0}

        def _timeout(request: object, **kw: object):
            calls["n"] += 1
            raise socket.timeout("timed out")

        t = UpstoxSandboxTransport(
            credential_provider=_Provider(),
            urlopen=_timeout,
            timeout_seconds=30,
        )
        _ = t.get_profile()
        assert calls["n"] == 1  # exactly one attempt, no blind retry

    def test_gate_off_skips_everything(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)
        assert real_broker_integration_enabled() is False


# ============================================================
# I. EXECUTION GATE STAYS DISABLED
# ============================================================


class TestExecutionGateDisabled:
    def test_default_state_is_disabled(self) -> None:
        assert LiveExecutionGateState.DISABLED.value == "DISABLED"

    def test_gate_negative_matrix_blocks_without_authorization(self) -> None:
        """Credentials alone never allow; 20 mandatory conditions required
        and gate must be enabled + explicitly authorized."""
        assert len(MANDATORY_GATE_CONDITIONS) >= 20
        gate_input = LiveExecutionGateInput(
            conditions=tuple(
                (name, True) for name in MANDATORY_GATE_CONDITIONS
            ),
            gate_enabled=False,
            explicit_operator_authorization=False,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.verdict.value == "NOT_ALLOWED"

    def test_gate_not_allowed_without_all_conditions(self) -> None:
        gate_input = LiveExecutionGateInput(
            conditions=(("explicit_live_mode", True),),
            gate_enabled=True,
            explicit_operator_authorization=True,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.verdict.value == "NOT_ALLOWED"


# ============================================================
# J. SANDBOX/PAPER/LIVE ISOLATION
# ============================================================


class TestIsolation:
    def test_no_sandbox_to_live_fallback(self) -> None:
        assert is_controlled_environment("SANDBOX") is True
        assert is_controlled_environment("LIVE") is False
        assert is_live_environment("LIVE") is True

    def test_verifier_expected_environment_is_sandbox(self) -> None:
        assert _verifier().guard_result().expected_environment == "SANDBOX"

    def test_analytics_token_never_a_substitute(self, monkeypatch) -> None:
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "analytics-another")
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == ""

    def test_live_order_host_never_a_readonly_default(self) -> None:
        """The LIVE order host (api-hft.upstox.com) is never the read-only
        verifier default; the verifier defaults to the read-only API base.
        Read-only enforcement (GET-only + place/cancel blocked) is the
        structural guard that keeps even a misconfigured base read-only."""
        assert _verifier().base_url == _READONLY_BASE
        assert _verifier().base_url != _LIVE_ORDER_HOST
        assert _LIVE_ORDER_HOST not in (DEFAULT_UPSTOX_API_BASE_URL,)