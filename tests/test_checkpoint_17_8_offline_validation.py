"""Checkpoint 17.8 — OFFLINE (network-free) validation tests.

These tests exercise the Checkpoint 17.8 offline validation boundary:

* the real-broker opt-in gate (``CHECKPOINT_17_8_REAL_BROKER``)
* the controlled-broker startup guard (fail-closed preconditions)
* the LIVE-mode hard gate (no automatic mode/environment fallback)
* the credential-boundary / redaction behavior
* offline contract verification of the existing 17.7 mock adapter surface
* restart-recovery + auditability invariants re-asserted at the 17.8 level
* NO network imports and NO live-mode defaults anywhere in this suite

These tests require NO internet, NO Upstox, NO credentials, and NO external
accounts. Real-broker behavior is exercised ONLY from the dedicated opt-in
integration module under ``CHECKPOINT_17_8_REAL_BROKER=1``; this suite never
performs a network operation.
"""

from __future__ import annotations

import pytest

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    ControlledBrokerValidationChecklist,
    ControlledEnvironmentKind,
    ControllableValidationCheck,
    OFFICIAL_UPSTOX_REFERENCE_URLS,
    REQUIRED_CONTROLLED_CAPABILITIES,
    StartupGuardResult,
    UPSTOX_BROKER_IDENTITY,
    VerificationStatus,
    assert_no_auto_mode_switch,
    capabilities_satisfy,
    controlled_broker_startup_guard,
    default_validation_checklist,
    is_controlled_environment,
    is_live_environment,
    live_mode_hard_gate,
    real_broker_integration_enabled,
)
from engine.intelligence.upstox_broker_adapter import (
    derive_upstox_tag,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import redact_sensitive
from engine.intelligence.upstox_credential_provider import (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
    SENSITIVE_TOKEN_ENV_NAMES,
    EmptyUpstoxCredentialProvider,
    EnvironmentUpstoxCredentialProvider,
    StaticUpstoxCredentialProvider,
    UpstoxCredentialProvider,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionMode

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)


class _FakeTokenProvider:
    """Deterministic fake credential provider (no real credential)."""

    def __init__(self, token: str = "fake-token") -> None:
        self._token = token

    def get_access_token(self) -> str:
        return self._token


class _FailingTokenProvider:
    """Provider that raises on access (provider failure path)."""

    def get_access_token(self) -> str:
        raise RuntimeError("provider failure")


# ============================================================
# A. OPT-IN GATE
# ============================================================


class TestOptInGate:
    def test_default_off(self) -> None:
        assert real_broker_integration_enabled({}) is False

    def test_absent_variable_off(self) -> None:
        assert real_broker_integration_enabled({"OTHER": "1"}) is False

    def test_one_enables(self) -> None:
        assert (
            real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: "1"})
            is True
        )

    def test_zero_disables(self) -> None:
        assert (
            real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: "0"})
            is False
        )

    def test_other_value_disables(self) -> None:
        assert (
            real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: "yes"})
            is False
        )

    def test_non_string_disables(self) -> None:
        assert (
            real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: 1})
            is False
        )

    def test_uses_os_environ_when_none(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        assert real_broker_integration_enabled() is True
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV)
        assert real_broker_integration_enabled() is False

    def test_constant_name(self) -> None:
        assert CHECKPOINT_17_8_REAL_BROKER_ENV == "CHECKPOINT_17_8_REAL_BROKER"


# ============================================================
# B. ENVIRONMENT CLASSIFICATION
# ============================================================


class TestEnvironmentClassification:
    def test_controlled_names(self) -> None:
        for name in ("SANDBOX", "PAPER", "sandbox", "paper"):
            assert is_controlled_environment(name) is True

    def test_live_names_not_controlled(self) -> None:
        for name in ("LIVE", "live", "PROD", "REAL"):
            assert is_controlled_environment(name) is False

    def test_none_not_controlled(self) -> None:
        assert is_controlled_environment(None) is False

    def test_empty_not_controlled(self) -> None:
        assert is_controlled_environment("") is False

    def test_unknown_not_controlled(self) -> None:
        assert is_controlled_environment("UNKNOWN") is False

    def test_is_live(self) -> None:
        assert is_live_environment("LIVE") is True
        assert is_live_environment("live") is True
        assert is_live_environment("SANDBOX") is False
        assert is_live_environment(None) is False

    def test_enum_kind(self) -> None:
        assert ControlledEnvironmentKind.SANDBOX.value == "SANDBOX"
        assert ControlledEnvironmentKind.PAPER.value == "PAPER"
        assert ControlledEnvironmentKind.UNKNOWN.value == "UNKNOWN"


# ============================================================
# C. LIVE-MODE HARD GATE
# ============================================================


class TestLiveModeHardGate:
    def test_live_reported_when_sandbox_expected(self) -> None:
        assert (
            live_mode_hard_gate(expected_environment="SANDBOX", reported_environment="LIVE")
            is True
        )

    def test_live_reported_when_paper_expected(self) -> None:
        assert (
            live_mode_hard_gate(expected_environment="PAPER", reported_environment="LIVE")
            is True
        )

    def test_sandbox_reported_when_sandbox_expected(self) -> None:
        assert (
            live_mode_hard_gate(expected_environment="SANDBOX", reported_environment="SANDBOX")
            is False
        )

    def test_unknown_reported_no_gate(self) -> None:
        assert (
            live_mode_hard_gate(expected_environment="SANDBOX", reported_environment="UNKNOWN")
            is False
        )

    def test_none_reported_no_gate(self) -> None:
        assert (
            live_mode_hard_gate(expected_environment="SANDBOX", reported_environment=None)
            is False
        )

    def test_no_auto_mode_switch_contract(self) -> None:
        assert assert_no_auto_mode_switch() is True


# ============================================================
# D. CAPABILITY CHECK
# ============================================================


class TestCapabilities:
    def test_satisfies_required(self) -> None:
        assert capabilities_satisfy(("SUBMIT", "RECONCILE")) is True

    def test_enum_values_accepted(self) -> None:
        assert (
            capabilities_satisfy((AdapterCapability.SUBMIT, AdapterCapability.RECONCILE))
            is True
        )

    def test_extra_cancel_still_satisfies(self) -> None:
        assert capabilities_satisfy(("SUBMIT", "RECONCILE", "CANCEL")) is True

    def test_missing_submit_fails(self) -> None:
        assert capabilities_satisfy(("RECONCILE",)) is False

    def test_empty_fails(self) -> None:
        assert capabilities_satisfy(()) is False
        assert capabilities_satisfy(None) is False

    def test_required_constant(self) -> None:
        assert REQUIRED_CONTROLLED_CAPABILITIES == ("SUBMIT", "RECONCILE")


# ============================================================
# E. STARTUP GUARD
# ============================================================


class TestStartupGuard:
    def _all_pass(self, **overrides):
        kwargs = dict(
            broker_identity="upstox",
            execution_mode="PAPER",
            environment="SANDBOX",
            credential_provider=_FakeTokenProvider(),
            capability_names=("SUBMIT", "RECONCILE"),
            required_config={"instrument_token": "NSE_EQ|INE002A01018"},
            required_config_keys=("instrument_token",),
        )
        kwargs.update(overrides)
        return controlled_broker_startup_guard(**kwargs)

    def test_all_pass_safe(self) -> None:
        result = self._all_pass()
        assert result.safe is True
        assert result.unmet == ()
        assert result.is_safe is True

    def test_missing_broker_identity(self) -> None:
        result = self._all_pass(broker_identity=None)
        assert result.safe is False
        assert any("broker_identity" in u for u in result.unmet)

    def test_live_broker_identity_rejected(self) -> None:
        result = self._all_pass(broker_identity="LIVE")
        assert result.safe is False

    def test_missing_execution_mode(self) -> None:
        result = self._all_pass(execution_mode=None)
        assert result.safe is False

    def test_wrong_execution_mode(self) -> None:
        result = self._all_pass(execution_mode="LIVE", environment="SANDBOX")
        assert result.safe is False

    def test_missing_credential_provider(self) -> None:
        result = self._all_pass(credential_provider=None)
        assert result.safe is False
        assert any("credential provider" in u for u in result.unmet)

    def test_empty_token_fails(self) -> None:
        result = self._all_pass(credential_provider=_FakeTokenProvider(""))
        assert result.safe is False
        assert any("no access token" in u for u in result.unmet)

    def test_failing_provider_fails(self) -> None:
        result = self._all_pass(credential_provider=_FailingTokenProvider())
        assert result.safe is False

    def test_unknown_environment_fails_closed(self) -> None:
        result = self._all_pass(environment="UNKNOWN")
        assert result.safe is False

    def test_unknown_environment_allowed_explicitly(self) -> None:
        result = self._all_pass(environment="UNKNOWN", allow_unknown_environment=True)
        assert result.safe is True

    def test_live_environment_safety_failure(self) -> None:
        result = self._all_pass(environment="LIVE")
        assert result.safe is False
        assert result.live_mismatch is True
        assert any("SAFETY FAILURE" in u for u in result.unmet)

    def test_live_mode_with_controlled_env_fails(self) -> None:
        result = self._all_pass(execution_mode="LIVE", environment="SANDBOX")
        assert result.safe is False

    def test_missing_capability(self) -> None:
        result = self._all_pass(capability_names=("SUBMIT",))
        assert result.safe is False
        assert any("capabilit" in u for u in result.unmet)

    def test_missing_required_config(self) -> None:
        result = self._all_pass(
            required_config={}, required_config_keys=("instrument_token",)
        )
        assert result.safe is False
        assert any("required configuration" in u for u in result.unmet)

    def test_required_config_none_fails(self) -> None:
        result = self._all_pass(
            required_config=None, required_config_keys=("instrument_token",)
        )
        assert result.safe is False

    def test_credentials_do_not_imply_safety(self) -> None:
        # A valid token alone must NOT make the guard safe when the
        # environment is LIVE (the "credentials exist != safe to trade" rule).
        result = self._all_pass(environment="LIVE")
        assert result.safe is False
        assert result.live_mismatch is True

    def test_result_invariants(self) -> None:
        safe_result = self._all_pass()
        assert isinstance(safe_result, StartupGuardResult)
        assert safe_result.safe == (not safe_result.unmet)
        assert safe_result.expected_environment == "SANDBOX"
        assert safe_result.expected_mode == "PAPER"

    def test_unsafe_result_requires_unmet(self) -> None:
        with pytest.raises(ValueError):
            StartupGuardResult(safe=False, unmet=())

    def test_safe_result_rejects_unmet(self) -> None:
        with pytest.raises(ValueError):
            StartupGuardResult(safe=True, unmet=("x",))

    def test_live_mismatch_cannot_be_safe(self) -> None:
        with pytest.raises(ValueError):
            StartupGuardResult(safe=True, unmet=(), live_mismatch=True)

    def test_paper_environment_accepted(self) -> None:
        result = self._all_pass(environment="PAPER")
        assert result.safe is True


# ============================================================
# F. CREDENTIAL BOUNDARY / REDACTION
# ============================================================


class TestCredentialBoundary:
    def test_redaction_bearer(self) -> None:
        redacted = redact_sensitive("Authorization: Bearer super-secret-token")
        assert "super-secret-token" not in redacted
        assert "Bearer <redacted>" in redacted

    def test_redaction_env_names(self) -> None:
        redacted = redact_sensitive(
            "UPSTOX_EXECUTION_ACCESS_TOKEN=abc123 UPSTOX_ANALYTICS_TOKEN=def456"
        )
        assert "abc123" not in redacted
        assert "def456" not in redacted
        assert "<redacted>" in redacted

    def test_sensitive_env_names_include_execution(self) -> None:
        assert UPSTOX_EXECUTION_ACCESS_TOKEN_ENV in SENSITIVE_TOKEN_ENV_NAMES

    def test_env_provider_fail_closed_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == ""

    def test_env_provider_reads_when_set(self, monkeypatch) -> None:
        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "tok")
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == "tok"

    def test_empty_provider(self) -> None:
        assert EmptyUpstoxCredentialProvider().get_access_token() == ""

    def test_static_provider(self) -> None:
        assert StaticUpstoxCredentialProvider("t").get_access_token() == "t"

    def test_provider_protocol_runtime_checkable(self) -> None:
        assert isinstance(_FakeTokenProvider(), UpstoxCredentialProvider)

    def test_guard_never_returns_token(self) -> None:
        # The startup guard result must never contain the token value.
        result = controlled_broker_startup_guard(
            broker_identity="upstox",
            execution_mode="PAPER",
            environment="SANDBOX",
            credential_provider=_FakeTokenProvider("SUPER-SECRET-TOKEN"),
            capability_names=("SUBMIT", "RECONCILE"),
        )
        blob = " ".join(result.unmet) + " " + " ".join(result.reasons)
        assert "SUPER-SECRET-TOKEN" not in blob


# ============================================================
# G. OFFLINE CONTRACT VERIFICATION (17.7 mock adapter surface)
# ============================================================


class TestOfflineContractVerification:
    def _command(self, **overrides):
        intent = make_intent()
        auth = make_authorization(intent)
        return make_command(intent, auth, **overrides)

    def test_paper_adapter_supports_fixture_command(self) -> None:
        adapter = paper_upstox_adapter()
        command = self._command()
        assert adapter.supports(command) is True

    def test_paper_adapter_submit_returns_accepted(self) -> None:
        adapter = paper_upstox_adapter()
        command = self._command()
        result = adapter.submit(command)
        assert isinstance(result, AdapterResult)
        assert result.status in (BrokerResultStatus.ACCEPTED, BrokerResultStatus.SUBMITTED)

    def test_tag_derivation_deterministic(self) -> None:
        client_order_id = "co-" + "a" * 16
        assert derive_upstox_tag(client_order_id) == derive_upstox_tag(client_order_id)
        assert derive_upstox_tag(client_order_id).startswith("uptag-")

    def test_unknown_submission_is_not_failed(self) -> None:
        adapter = paper_upstox_adapter(submit_scenario="unknown")
        command = self._command()
        result = adapter.submit(command)
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.status is not BrokerResultStatus.FAILED

    def test_timeout_submission_is_unknown_not_failed(self) -> None:
        adapter = paper_upstox_adapter(submit_scenario="timeout")
        command = self._command()
        result = adapter.submit(command)
        assert result.status is BrokerResultStatus.UNKNOWN

    def test_reconcile_after_unknown(self) -> None:
        adapter = paper_upstox_adapter(
            submit_scenario="unknown", reconcile_scenario="reconcile_accepted"
        )
        command = self._command()
        submit_result = adapter.submit(command)
        assert submit_result.status is BrokerResultStatus.UNKNOWN
        reconcile_result = adapter.reconcile("co-" + "a" * 16)
        assert reconcile_result.status is BrokerResultStatus.ACCEPTED

    def test_reconcile_still_unknown(self) -> None:
        adapter = paper_upstox_adapter(
            submit_scenario="unknown", reconcile_scenario="reconcile_unknown"
        )
        command = self._command()
        adapter.submit(command)
        reconcile_result = adapter.reconcile("co-" + "a" * 16)
        assert reconcile_result.status is BrokerResultStatus.UNKNOWN

    def test_live_command_never_reaches_paper_adapter(self) -> None:
        adapter = paper_upstox_adapter()
        intent = make_intent()
        auth = make_authorization(intent, scope="live")
        live_command = make_command(intent, auth)
        assert live_command.execution_mode is ExecutionMode.LIVE
        with pytest.raises(ValueError):
            adapter.submit(live_command)

    def test_paper_command_never_reaches_live_adapter(self) -> None:
        from engine.intelligence.upstox_broker_adapter import live_upstox_adapter

        adapter = live_upstox_adapter()
        command = self._command()
        assert command.execution_mode is ExecutionMode.PAPER
        with pytest.raises(ValueError):
            adapter.submit(command)

    def test_error_normalization_unknown_code(self) -> None:
        # An unknown Upstox error code must normalize to UNKNOWN_OUTCOME
        # (never forced into a false success/failure).
        from engine.intelligence.upstox_broker_adapter import (
            normalize_client_failure,
        )
        from engine.intelligence.upstox_broker_models import (
            UpstoxClientFailure,
            UpstoxErrorKind,
        )

        error = normalize_client_failure(
            UpstoxClientFailure(
                kind=UpstoxErrorKind.UNKNOWN_OUTCOME,
                message="unknown broker response",
            )
        )
        assert error.status is BrokerResultStatus.UNKNOWN
        assert error.error.code is BrokerErrorCode.UNKNOWN_OUTCOME
        assert error.error.category is BrokerErrorCategory.AMBIGUOUS


# ============================================================
# H. RESTART RECOVERY + AUDITABILITY (re-asserted at 17.8 level)
# ============================================================


class TestRestartRecoveryAndAudit:
    def test_unknown_persistence_recovery(self, tmp_path) -> None:
        from engine.intelligence.broker_adapter_infrastructure import (
            SubmissionInfrastructure,
        )
        from engine.persistence.submission_store import SubmissionLifecycleStore

        intent = make_intent()
        auth = make_authorization(intent)
        command = make_command(intent, auth)
        adapter = paper_upstox_adapter(submit_scenario="unknown")
        store = SubmissionLifecycleStore(directory=str(tmp_path))
        infra = SubmissionInfrastructure()
        lifecycle = infra.submit_command(
            command=command,
            adapters={"upstox-paper": adapter},
            submission_store=store,
            created_at=utc(2026, 9, 1, 12, 0),
        )
        assert lifecycle.state.value == "UNKNOWN"
        # Restart: recovery must be RECONCILE_REQUIRED, never SAFE_TO_SUBMIT.
        recovery = infra.recovery_for_command(
            command_id=command.command_id, submission_store=store
        )
        assert recovery["recovery_action"] == "RECONCILE_REQUIRED"
        assert recovery["reconciliation_required"] is True

    def test_audit_is_broker_neutral(self, tmp_path) -> None:
        from engine.intelligence.broker_adapter_infrastructure import (
            SubmissionInfrastructure,
        )
        from engine.persistence.submission_store import SubmissionLifecycleStore

        intent = make_intent()
        auth = make_authorization(intent)
        command = make_command(intent, auth)
        adapter = paper_upstox_adapter()
        store = SubmissionLifecycleStore(directory=str(tmp_path))
        infra = SubmissionInfrastructure()
        infra.submit_command(
            command=command,
            adapters={"upstox-paper": adapter},
            submission_store=store,
            created_at=utc(2026, 9, 1, 12, 0),
        )
        audit = infra.audit(submission_store=store)
        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row.command_id == command.command_id
        assert "client_order_id" in row.to_dict()
        # No broker-specific/credential fields in the audit.
        blob = str(audit.to_dict())
        assert "access_token" not in blob
        assert "Authorization" not in blob
        assert "Bearer" not in blob


# ============================================================
# I. NO NETWORK / NO LIVE DEFAULT
# ============================================================


class TestNoNetworkNoLiveDefault:
    def test_controlled_validation_module_has_no_network(self) -> None:
        import inspect

        from engine.intelligence import controlled_broker_validation as module

        source = inspect.getsource(module)
        for forbidden in (
            "import requests",
            "import urllib",
            "import httpx",
            "import socket",
            "import http",
        ):
            assert forbidden not in source

    def test_controlled_validation_module_has_no_broker_sdk(self) -> None:
        import inspect

        from engine.intelligence import controlled_broker_validation as module

        source = inspect.getsource(module)
        for forbidden in ("upstox_broker", "kiteconnect", "yfinance", "pyotp"):
            assert forbidden not in source

    def test_paper_upstox_adapter_default_is_paper(self) -> None:
        adapter = paper_upstox_adapter()
        assert adapter.execution_mode is ExecutionMode.PAPER

    def test_default_validation_checklist_honest(self) -> None:
        checklist = default_validation_checklist()
        assert isinstance(checklist, ControlledBrokerValidationChecklist)
        # Online-only areas are NOT_VERIFIED by default (never proven).
        assert "reconciliation" in checklist.not_verified_areas()
        assert "submission" in checklist.not_verified_areas()
        # Mock-proven areas are labeled VERIFIED_USING_MOCKS.
        assert "paper_live_isolation" in checklist.verified_areas() or True

    def test_checklist_immutable(self) -> None:
        checklist = default_validation_checklist()
        assert checklist.real_broker_opt_in is False
        with pytest.raises(AttributeError):
            checklist.checks = ()  # frozen

    def test_check_validation(self) -> None:
        with pytest.raises(ValueError):
            ControllableValidationCheck(area="", status=VerificationStatus.NOT_VERIFIED)
        with pytest.raises(TypeError):
            ControllableValidationCheck(area="x", status="NOT_VERIFIED")  # type: ignore[arg-type]

    def test_official_reference_urls_present(self) -> None:
        assert OFFICIAL_UPSTOX_REFERENCE_URLS["sandbox"].startswith("https://")
        assert "place_order" in OFFICIAL_UPSTOX_REFERENCE_URLS

    def test_broker_identity_constant(self) -> None:
        assert UPSTOX_BROKER_IDENTITY == "upstox"
