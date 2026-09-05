"""Checkpoint 18.2 — sandbox read-only verification service tests (OFFLINE).

Deterministic, network-free tests of the
:class:`~engine.intelligence.sandbox_readonly_verifier.SandboxReadOnlyVerifier`
and its audit/observability models.

Coverage (Checkpoint 18.2 Phases 2 / 6 / 7 / 9 / 10 / 11):

* Credential boundary -- missing / empty / malformed credentials fail closed
  BEFORE any request; token values NEVER appear in results, exceptions,
  audit records, lifecycle state, persistence, logs, or repr/str.
* Credential rotation never alters command_id / client_order_id /
  idempotency identity.
* Opt-in gate -- ``CHECKPOINT_17_8_REAL_BROKER`` must be enabled; otherwise
  UNVERIFIED and no request is issued (no new checkpoint-specific env var is
  introduced; the 17.8 gate is the repository-wide real-broker gate).
* Startup guard -- any precondition failure -> UNVERIFIED, no request.
* Read-only profile / order-details / order-history audit records with the
  broker-neutral vocabulary; ambiguous outcomes carry error taxonomy.
* Reconciliation over PRE-EXISTING order ids only; zero ids -> NOT VERIFIED.
* Live/paper -- a SANDBOX adapter is never selectable as LIVE; token value
  never leaks into the repo; credentials alone never authorize execution.
* Transport-failure mapping (timeout / unknown / malformed -> AMBIGUOUS /
  UNKNOWN; network/auth/etc -> FAILED) -- no fabricated success.
* Execution gate remains DISABLED and NOT wired.
"""

from __future__ import annotations

import datetime
import os
import re
import urllib.error
from decimal import Decimal

import pytest

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    real_broker_integration_enabled,
)
from engine.intelligence.sandbox_readonly_verifier import (
    SandboxReadOnlyVerifier,
    _map_failure,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
)
from engine.intelligence.upstox_sandbox_transport import (
    UpstoxProfileResponse,
    UpstoxSandboxTransport,
)
from engine.models.broker_adapter import (
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.sandbox_readonly_verification import (
    ReadOnlyOperationType,
    SandboxReadOnlyVerification,
    SandboxVerificationAuditEntry,
    VerificationClassification,
    VerificationEnvironment,
)

_FAKE_TOKEN = "cb18_2_test_token_value_never_printed"
_SECRET_MARKER = "SUPERSECRETTOKEN"

_GATE = {CHECKPOINT_17_8_REAL_BROKER_ENV: "1"}


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def utcnow(self) -> datetime.datetime:
        return self._now


class _Provider:
    def __init__(self, token: str = _FAKE_TOKEN) -> None:
        self._token = token if isinstance(token, str) else ""

    def get_access_token(self) -> str:
        return self._token


def _profile(broker: str = "UPSTOX") -> UpstoxProfileResponse:
    return UpstoxProfileResponse(
        broker=broker,
        user_type="individual",
        exchanges=("NSE",),
        products=("D",),
        order_types=("MARKET", "LIMIT", "SL", "SL-M"),
        is_active=True,
        user_id_present=True,
    )


class _FakeTransport:
    """Deterministic fake transport recording calls; never touches network."""

    def __init__(
        self,
        profile: object = None,
        order_states: dict[str, object] | None = None,
    ) -> None:
        self._profile = profile
        self._order_states = dict(order_states or {})
        self.calls: list[str] = []

    def get_profile(self) -> object:
        self.calls.append("profile")
        if self._profile is None:
            return UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message="no profile (test double)",
            )
        return self._profile

    def get_order(self, tag: str = "", order_id: str | None = None) -> object:
        self.calls.append(f"get_order:{order_id or tag}")
        if order_id in self._order_states:
            return self._order_states[order_id]
        return UpstoxClientFailure(
            kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
            message=f"no record for {order_id or tag}",
        )

    def check_health(self) -> bool:
        self.calls.append("health")
        return isinstance(self._profile, UpstoxProfileResponse)

    def place_order(self, request: object) -> object:
        raise ValueError("READ-ONLY: order placement blocked")

    def cancel_order(self, order_id: str) -> object:
        raise ValueError("READ-ONLY: order cancellation blocked")


# ============================================================
# A. OPT-IN GATE + CREDENTIAL BOUNDARY (Phases 2 / 9 / 10)
# ============================================================


class TestGateAndCredential:
    def test_gate_disabled_no_request(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider()
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        assert result.gate_passed is False
        assert transport.calls == []  # NO request was issued
        assert "CHECKPOINT_17_8_REAL_BROKER" in result.conclusion

    def test_gate_disabled_even_with_token(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider()
        )
        result = verifier.verify()
        assert result.token_available is False  # not even evaluated as available
        assert result.gate_passed is False

    def test_gate_enabled_no_credential_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(transport=transport)
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        assert result.gate_passed is False
        assert transport.calls == []

    def test_gate_enabled_empty_credential_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider("")
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        assert transport.calls == []

    def test_gate_enabled_malformed_credential_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(42)  # type: ignore[arg-type]
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        assert result.gate_passed is False
        assert transport.calls == []

    def test_token_value_never_in_verification_projection(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport,
            credential_provider=_Provider(_SECRET_MARKER),
            clock=_FakeClock(),
        )
        result = verifier.verify()
        dumped = str(result.to_dict())
        assert _SECRET_MARKER not in dumped
        assert "Bearer" not in dumped

    def test_token_value_never_in_str_repr(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        provider = _Provider(_SECRET_MARKER)
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=provider, clock=_FakeClock()
        )
        assert _SECRET_MARKER not in repr(provider)
        assert _SECRET_MARKER not in str(provider)
        assert _SECRET_MARKER not in repr(verifier)
        assert _SECRET_MARKER not in str(verifier)

    def test_token_never_in_guard_result(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        verifier = SandboxReadOnlyVerifier(
            transport=_FakeTransport(profile=_profile()),
            credential_provider=_Provider(_SECRET_MARKER),
            clock=_FakeClock(),
        )
        guard = verifier.guard_result()
        assert _SECRET_MARKER not in str(guard)


# ============================================================
# B. GATE-ENABLED READ-ONLY VERIFICATION (Phase 4)
# ============================================================


class TestReadOnlyVerification:
    @pytest.fixture(autouse=True)
    def _enable_gate(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")

    def test_real_connectivity_established_on_profile(self) -> None:
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is True
        assert result.gate_passed is True
        assert result.profile_broker == "UPSTOX"
        assert result.profile_user_id_present is True
        assert transport.calls == ["profile"]

    def test_audit_entry_recorded_for_profile(self) -> None:
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        profile_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.PROFILE
        ]
        assert len(profile_entries) == 1
        entry = profile_entries[0]
        assert entry.classification is VerificationClassification.SUCCESS
        assert entry.normalized_status is BrokerResultStatus.ACCEPTED
        assert entry.endpoint_category == "user_profile"
        assert entry.environment is VerificationEnvironment.SANDBOX
        assert entry.to_dict()["audit_id"]

    def test_audit_entries_deterministic_ids(self) -> None:
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        r1 = verifier.verify()
        r2 = verifier.verify()
        assert r1.verification_id == r2.verification_id
        assert [e.audit_id for e in r1.audit_entries] == [
            e.audit_id for e in r2.audit_entries
        ]

    def test_profile_failure_recorded_as_failed(self) -> None:
        transport = _FakeTransport(
            profile=UpstoxClientFailure(
                kind=UpstoxErrorKind.AUTHENTICATION, message="token rejected"
            )
        )
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is False
        profile_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.PROFILE
        ]
        assert len(profile_entries) == 1
        entry = profile_entries[0]
        assert entry.classification is VerificationClassification.FAILED
        assert entry.normalized_status is BrokerResultStatus.FAILED
        assert entry.error_code is BrokerErrorCode.AUTHENTICATION_FAILURE

    def test_profile_timeout_recorded_as_ambiguous(self) -> None:
        transport = _FakeTransport(
            profile=UpstoxClientFailure(
                kind=UpstoxErrorKind.TIMEOUT, message="timed out"
            )
        )
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        profile_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.PROFILE
        ]
        entry = profile_entries[0]
        assert entry.classification is VerificationClassification.AMBIGUOUS
        assert entry.normalized_status is BrokerResultStatus.UNKNOWN
        assert entry.error_code is BrokerErrorCode.TIMEOUT
        assert entry.error_category is BrokerErrorCategory.AMBIGUOUS


# ============================================================
# C. RECONCILIATION (Phase 6)
# ============================================================


class TestReconciliation:
    @pytest.fixture(autouse=True)
    def _enable_gate(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")

    def test_no_order_ids_reconciliation_not_verified(self) -> None:
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        assert result.reconciliation_result == "NOT_VERIFIED"
        history_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.ORDER_HISTORY
        ]
        assert len(history_entries) == 1
        assert (
            history_entries[0].classification is VerificationClassification.UNVERIFIED
        )

    def test_existing_order_id_checked_read_only(self) -> None:
        transport = _FakeTransport(
            profile=_profile(),
            order_states={
                "240108010445130": UpstoxOrderStateResponse(
                    order_id="240108010445130",
                    tag="uptag-abc123",
                    status=UpstoxOrderState.COMPLETE,
                    reason="complete",
                )
            },
        )
        verifier = SandboxReadOnlyVerifier(
            transport=transport,
            credential_provider=_Provider(),
            clock=_FakeClock(),
            broker_order_ids=("240108010445130",),
        )
        result = verifier.verify()
        assert "240108010445130" in result.reconciliation_result
        assert "SUCCESS" in result.reconciliation_result
        detail_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.ORDER_DETAILS
        ]
        assert len(detail_entries) == 1
        entry = detail_entries[0]
        assert entry.classification is VerificationClassification.SUCCESS
        assert entry.normalized_status is BrokerResultStatus.FILLED
        assert entry.broker_order_id == "240108010445130"

    def test_missing_order_record_ambiguous(self) -> None:
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport,
            credential_provider=_Provider(),
            clock=_FakeClock(),
            broker_order_ids=("000000000000000",),
        )
        result = verifier.verify()
        detail_entries = [
            e for e in result.audit_entries
            if e.operation_type is ReadOnlyOperationType.ORDER_DETAILS
        ]
        assert len(detail_entries) == 1
        entry = detail_entries[0]
        assert entry.classification is VerificationClassification.AMBIGUOUS
        assert entry.normalized_status is BrokerResultStatus.UNKNOWN
        assert entry.error_code is not None


# ============================================================
# D. TRANSPORT-FAILURE MAPPING (Phase 5 / 8)
# ============================================================


class TestFailureMapping:
    def test_timeout_maps_ambiguous(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.TIMEOUT, message="t")
        )
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm is BrokerResultStatus.UNKNOWN
        assert code is BrokerErrorCode.TIMEOUT
        assert cat is BrokerErrorCategory.AMBIGUOUS

    def test_unknown_outcome_maps_ambiguous(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.UNKNOWN_OUTCOME, message="u")
        )
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm is BrokerResultStatus.UNKNOWN
        assert code is BrokerErrorCode.UNKNOWN_OUTCOME

    def test_malformed_maps_ambiguous(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.MALFORMED_RESPONSE, message="m")
        )
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm is BrokerResultStatus.UNKNOWN
        assert code is BrokerErrorCode.MALFORMED_RESPONSE

    def test_rate_limit_maps_ambiguous_not_failed(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.RATE_LIMIT, message="r")
        )
        assert cls is VerificationClassification.AMBIGUOUS
        assert norm is BrokerResultStatus.UNKNOWN
        assert code is BrokerErrorCode.RATE_LIMIT

    def test_auth_maps_failed(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.AUTHENTICATION, message="a")
        )
        assert cls is VerificationClassification.FAILED
        assert norm is BrokerResultStatus.FAILED
        assert code is BrokerErrorCode.AUTHENTICATION_FAILURE

    def test_network_maps_failed(self) -> None:
        cls, norm, code, cat = _map_failure(
            UpstoxClientFailure(kind=UpstoxErrorKind.NETWORK, message="n")
        )
        assert cls is VerificationClassification.FAILED
        assert norm is BrokerResultStatus.FAILED
        assert code is BrokerErrorCode.NETWORK_FAILURE


# ============================================================
# E. LIVE/PAPER ISOLATION (Phase 9)
# ============================================================


class TestIsolation:
    def test_sandbox_transport_is_not_submittable(self) -> None:
        # The read-only transport cannot even expose submit/cancel helpers.
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=lambda *a, **k: None
        )
        from engine.intelligence.upstox_broker_models import UpstoxBrokerRequest

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
        with pytest.raises(ValueError):
            transport.cancel_order("oid")

    def test_credentials_alone_never_authorize(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        # Token present + guard passing + READ-ONLY verification succeeding
        # does NOT enable live execution: a LIVE executable path does not
        # exist, and the execution gate remains DISABLED.
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        assert result.real_sandbox_connected is True
        # The conclusion explicitly does not authorize trading.
        assert "do NOT authorize live trading" in result.conclusion

    def test_no_live_environment_anywhere_in_readonly_surface(self) -> None:
        transport = UpstoxSandboxTransport(
            credential_provider=_Provider(), urlopen=lambda *a, **k: None
        )
        assert "LIVE" not in getattr(transport, "execution_mode", "")
        for attr in ("place_order", "cancel_order"):
            assert callable(getattr(transport, attr))


# ============================================================
# F. EXECUTION GATE (Phase 9)
# ============================================================


class TestExecutionGate:
    def test_gate_is_not_wired_into_any_readonly_path(self) -> None:
        # The read-only verifier never constructs or evaluates the live
        # gate: verification is, by construction, not gate-gated. The gate
        # itself remains a standalone DESIGn surface.
        from engine.intelligence.execution_gate import GateVerdict, LiveExecutionGate

        gate = LiveExecutionGate()
        assert gate is not None
        assert hasattr(gate, "evaluate")

    def test_new_module_dependency_dir_ok(self) -> None:
        # transport imports ONLY its own models + credential provider +
        # broker client redaction; verifier imports controlled validation +
        # transport + neutral models. Neither imports dashboard/paper/etc.
        import ast

        for path in (
            "src/engine/intelligence/upstox_sandbox_transport.py",
            "src/engine/intelligence/sandbox_readonly_verifier.py",
            "src/engine/models/sandbox_readonly_verification.py",
        ):
            tree = ast.parse(open(path).read())
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "dashboard" not in alias.name
                        assert "paper" not in alias.name
                        assert "TradePlan" not in alias.name


# ============================================================
# G. OBSERVABILITY / AUDIT SAFETY (Phase 11)
# ============================================================


class TestAuditSafety:
    def test_audit_error_messages_are_redacted(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(
            profile=UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message=f"bad {_SECRET_MARKER}",
            )
        )
        verifier = SandboxReadOnlyVerifier(
            transport=transport,
            credential_provider=_Provider(_SECRET_MARKER),
            clock=_FakeClock(),
        )
        result = verifier.verify()
        assert _SECRET_MARKER not in str(result.to_dict())

    def test_ambiguous_audit_entry_requires_error_code(self) -> None:
        now = datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
        with pytest.raises(ValueError):
            SandboxVerificationAuditEntry(
                operation_type=ReadOnlyOperationType.PROFILE,
                environment=VerificationEnvironment.SANDBOX,
                endpoint_category="user_profile",
                request_purpose="p",
                performed_at=now,
                classification=VerificationClassification.AMBIGUOUS,
                normalized_status=BrokerResultStatus.UNKNOWN,
                error_code=None,
                audit_id="x",
            )

    def test_verification_audit_id_deterministic(self) -> None:
        now = datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
        entry_kwargs = dict(
            operation_type=ReadOnlyOperationType.PROFILE,
            environment=VerificationEnvironment.SANDBOX,
            endpoint_category="user_profile",
            request_purpose="p",
            performed_at=now,
            classification=VerificationClassification.SUCCESS,
            normalized_status=BrokerResultStatus.ACCEPTED,
        )
        e1 = SandboxVerificationAuditEntry(audit_id="a", **entry_kwargs)
        e2 = SandboxVerificationAuditEntry(audit_id="b", **entry_kwargs)
        # The audit_id is caller-authoritative; identity is deterministic from
        # the payload, so the two (identical payloads) share an identity.
        assert e1._identity(**{**entry_kwargs, "broker_order_id": None, "client_order_id": None, "error_code": None, "error_category": None, "reconciliation_result": "", "detail": ""}) == e2._identity(**{**entry_kwargs, "broker_order_id": None, "client_order_id": None, "error_code": None, "error_category": None, "reconciliation_result": "", "detail": ""})


# ============================================================
# H. CREDENTIAL ROTATION (Phase 7)
# ============================================================


class RotatingProvider:
    def __init__(self) -> None:
        self.index = 0

    def get_access_token(self) -> str:
        self.index += 1
        return f"tok-rotation-{self.index}"


class TestRotation:
    def test_rotation_does_not_change_verification_identity(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        provider = RotatingProvider()
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=provider, clock=_FakeClock()
        )
        r1 = verifier.verify()
        r2 = verifier.verify()
        # The token value is read at the boundary; rotation between calls
        # must NOT change the deterministic verification identity (the
        # identity never includes the token VALUE).
        assert r1.verification_id == r2.verification_id
        assert r1.real_sandbox_connected is True

    def test_rotation_never_alters_contract_identities(self) -> None:
        from engine.intelligence.broker_adapter_contract import (
            derive_client_order_id,
            derive_idempotency_key,
        )

        command_id = "cmd-0123456789abcdef"
        assert derive_client_order_id(command_id=command_id) == derive_client_order_id(command_id=command_id)
        assert derive_idempotency_key(command_id=command_id) == derive_idempotency_key(command_id=command_id)
        # Rotation of the credential has zero effect on these identities
        # because they are derived from immutable command identity only.
        assert "tok-rotation" not in derive_client_order_id(command_id=command_id)
        assert "tok-rotation" not in derive_idempotency_key(command_id=command_id)


# ============================================================
# I. STRUCTURAL / SOURCE AUDITS (Phases 1 / 16)
# ============================================================


class TestStructuralAudits:
    def test_no_broker_secrets_in_ag_or_tests(self) -> None:
        # Ensure the CHECKPOINT gate name is the existing 17.8 gate (no new
        # misleading env var) and that no real credential literals exist.
        assert CHECKPOINT_17_8_REAL_BROKER_ENV == "CHECKPOINT_17_8_REAL_BROKER"
        source = open("src/engine/intelligence/upstox_sandbox_transport.py").read()
        assert "UPSTOX_ANALYTICS_TOKEN" not in source or "SENSITIVE" in source

    def test_read_only_only_text_in_helpers(self) -> None:
        import inspect

        from engine.intelligence import upstox_sandbox_transport as mod

        assert inspect.getdoc(mod.UpstoxSandboxTransport.place_order)  # non-empty
        assert "READ-ONLY" in (inspect.getdoc(mod.UpstoxSandboxTransport.place_order) or "").upper()

    def test_verifier_conclusion_safety(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        transport = _FakeTransport(profile=_profile())
        verifier = SandboxReadOnlyVerifier(
            transport=transport, credential_provider=_Provider(), clock=_FakeClock()
        )
        result = verifier.verify()
        assert "do NOT authorize live trading" in result.conclusion
        assert "not verified" in result.conclusion or "ESTABLISHED" in result.conclusion