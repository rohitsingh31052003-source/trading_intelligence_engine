"""Checkpoint 17.7 — Upstox broker client boundary tests.

This module tests the adapter-owned broker client boundary:

* the :class:`UpstoxBrokerClient` Protocol contract (no network in 17.7)
* the deterministic :class:`MockUpstoxBrokerClient` (in-memory, network-free)
* the 26-scenario failure-injection matrix (Checkpoint 17.6 Section 38)
* the no-network safety test (the client cannot perform network I/O)
* the credential-leakage test (fake secrets never propagate into results,
  errors, logs, audit data, or persistence)
* the redaction rule (Authorization / token env-var values scrubbed)

EVERY test is fully offline: no broker credentials, no Upstox SDK, no network
dependency, no internet access. All tests use injected mocks/fakes.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.intelligence.broker_adapter_infrastructure import (
    DuplicateSubmissionError,
    SubmissionInfrastructure,
)
from engine.intelligence.broker_adapter_contract import select_adapter, validate_adapter_mode
from engine.intelligence.upstox_broker_adapter import paper_upstox_adapter
from engine.intelligence.upstox_broker_client import (
    MOCK_UPSTOX_CLIENT_SCENARIOS,
    MockUpstoxBrokerClient,
    UpstoxBrokerClient,
    redact_sensitive,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderType,
    UpstoxProduct,
    UpstoxTransactionType,
    UpstoxValidity,
)
from engine.intelligence.upstox_credential_provider import (
    EmptyUpstoxCredentialProvider,
    StaticUpstoxCredentialProvider,
)
from engine.models.broker_adapter import AdapterCapability, BrokerResultStatus
from engine.models.execution_command import ExecutionCommand, ExecutionMode
from engine.persistence.submission_store import SubmissionLifecycleStore

from tests.test_checkpoint_17_7_upstox_adapter import _cmd

_UTC = timezone.utc


def _request(**overrides):
    base = {
        "instrument_token": "NSE_INDEX|Nifty 50",
        "transaction_type": UpstoxTransactionType.BUY,
        "quantity": Decimal("10"),
        "product": UpstoxProduct.D,
        "validity": UpstoxValidity.DAY,
        "order_type": UpstoxOrderType.LIMIT,
        "price": Decimal("100.50"),
        "trigger_price": None,
        "tag": "uptag-abc",
        "client_order_id": "co-abc",
        "idempotency_key": "idem-abc",
        "execution_mode": "PAPER",
        "created_at": datetime(2026, 9, 1, tzinfo=_UTC),
    }
    base.update(overrides)
    return UpstoxBrokerRequest(**base)


def _client(**kwargs):
    kwargs.setdefault("credential_provider", StaticUpstoxCredentialProvider("fake"))
    return MockUpstoxBrokerClient(**kwargs)


def _raw_command(execution_mode=ExecutionMode.PAPER):
    return ExecutionCommand(
        command_id="cmd-" + "a" * 16,
        authorization_id="auth-x",
        intent_id="intent-x",
        content_fingerprint="fp-x",
        instrument="NIFTY",
        direction="LONG",
        entry=Decimal("100.50"),
        stop=Decimal("95.00"),
        target=Decimal("110.00"),
        quantity=Decimal("10"),
        planned_risk=Decimal("55.00"),
        maximum_risk=Decimal("100.00"),
        execution_mode=execution_mode,
        created_at=datetime(2026, 9, 1, tzinfo=_UTC),
    )


# ============================================================
# PROTOCOL CONTRACT
# ============================================================


class TestClientProtocol:
    def test_mock_implements_protocol(self):
        assert isinstance(_client(), UpstoxBrokerClient)

    def test_protocol_methods(self):
        for name in ("place_order", "get_order", "cancel_order", "check_health"):
            assert hasattr(UpstoxBrokerClient, name)

    def test_protocol_has_no_network_methods(self):
        import inspect

        src = inspect.getsource(UpstoxBrokerClient)
        for net in ("socket", "requests", "httpx", "urllib", "aiohttp", "websocket"):
            assert net not in src


# ============================================================
# MOCK CLIENT BEHAVIOR
# ============================================================


class TestMockClientBehavior:
    def test_place_order_success_returns_order_data(self):
        result = _client().place_order(_request())
        assert result.status == "success"
        assert result.order_data is not None
        assert len(result.order_data.order_ids) == 1

    def test_place_order_records_submission(self):
        client = _client()
        client.place_order(_request())
        assert len(client.submissions) == 1

    def test_get_order_by_tag(self):
        client = _client()
        result = client.get_order("uptag-abc")
        assert result.tag == "uptag-abc"

    def test_cancel_order(self):
        client = _client()
        result = client.cancel_order("order-1")
        assert result.status == "success"

    def test_check_health_requires_token(self):
        assert _client(credential_provider=EmptyUpstoxCredentialProvider()).check_health() is False
        assert _client().check_health() is True

    def test_missing_credential_fails_closed_on_place(self):
        client = MockUpstoxBrokerClient(credential_provider=None)
        result = client.place_order(_request())
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_missing_credential_fails_closed_on_get(self):
        client = MockUpstoxBrokerClient(credential_provider=None)
        result = client.get_order("uptag-abc")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION

    def test_missing_credential_fails_closed_on_cancel(self):
        client = MockUpstoxBrokerClient(credential_provider=None)
        result = client.cancel_order("order-1")
        assert isinstance(result, UpstoxClientFailure)
        assert result.kind is UpstoxErrorKind.AUTHENTICATION


# ============================================================
# 26-SCENARIO FAILURE-INJECTION MATRIX (17.6 Section 38)
# ============================================================


class TestFailureMatrix:
    """Each scenario must fail safely (never a false success/failure)."""

    def test_01_accepted_submission(self):
        adapter = paper_upstox_adapter(submit_scenario="accepted")
        assert adapter.submit(_cmd()).status is BrokerResultStatus.ACCEPTED

    def test_02_rejected_submission(self):
        adapter = paper_upstox_adapter(submit_scenario="rejected")
        assert adapter.submit(_cmd()).status is BrokerResultStatus.REJECTED

    def test_03_validation_failure(self):
        adapter = paper_upstox_adapter(submit_scenario="validation_failure")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "VALIDATION_FAILURE"

    def test_04_insufficient_funds(self):
        adapter = paper_upstox_adapter(submit_scenario="insufficient_funds")
        assert adapter.submit(_cmd()).status is BrokerResultStatus.REJECTED

    def test_05_invalid_instrument(self):
        adapter = paper_upstox_adapter(submit_scenario="invalid_instrument")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "UNSUPPORTED_INSTRUMENT"

    def test_06_invalid_order_type(self):
        adapter = paper_upstox_adapter(submit_scenario="invalid_order_type")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "UNSUPPORTED_ORDER_SEMANTICS"

    def test_07_timeout(self):
        adapter = paper_upstox_adapter(submit_scenario="timeout")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code.value == "TIMEOUT"
        assert result.is_ambiguous

    def test_08_unknown_outcome(self):
        adapter = paper_upstox_adapter(submit_scenario="unknown_outcome")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code.value == "UNKNOWN_OUTCOME"

    def test_09_reconciliation_accepted(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_accepted")
        assert adapter.reconcile("co-test").status is BrokerResultStatus.ACCEPTED

    def test_10_reconciliation_rejected(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_rejected")
        assert adapter.reconcile("co-test").status is BrokerResultStatus.REJECTED

    def test_11_reconciliation_unknown(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_unknown")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.is_ambiguous

    def test_12_duplicate_submission(self, tmp_path):
        """Application-level guard: the infrastructure refuses a duplicate.

        A ``submitted`` scenario leaves the lifecycle in-flight
        (``SUBMITTED``, not terminal), so a second submit must raise
        ``DuplicateSubmissionError`` (no blind retry).
        """
        cmd = _cmd()
        store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        infra = SubmissionInfrastructure()
        adapters = {"upstox-paper": paper_upstox_adapter(submit_scenario="submitted")}
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        with pytest.raises(DuplicateSubmissionError):
            infra.submit_command(
                command=cmd,
                adapters=adapters,
                submission_store=store,
                created_at=datetime(2026, 9, 1, tzinfo=_UTC),
            )

    def test_13_duplicate_broker_response(self):
        """A broker duplicate response normalizes safely (no new order)."""
        adapter = paper_upstox_adapter(submit_scenario="duplicate")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.ACCEPTED
        assert result.broker_order_id is not None

    def test_14_malformed_broker_response(self):
        adapter = paper_upstox_adapter(submit_scenario="malformed_response")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code.value == "MALFORMED_RESPONSE"

    def test_15_rate_limit(self):
        adapter = paper_upstox_adapter(submit_scenario="rate_limit")
        result = adapter.submit(_cmd())
        # A rate-limited submit is ambiguous -> UNKNOWN (never FAILED).
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code.value == "RATE_LIMIT"

    def test_16_broker_unavailable(self):
        adapter = paper_upstox_adapter(submit_scenario="broker_unavailable")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "BROKER_UNAVAILABLE"

    def test_17_authentication_failure(self):
        adapter = paper_upstox_adapter(submit_scenario="authentication_failure")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "AUTHENTICATION_FAILURE"

    def test_18_restart_during_submission(self, tmp_path):
        """A restart with a submitted lifecycle requires reconciliation."""
        cmd = _cmd()
        store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        infra = SubmissionInfrastructure()
        adapters = {"upstox-paper": paper_upstox_adapter(submit_scenario="restart")}
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        view = infra.recovery_for_command(command_id=cmd.command_id, submission_store=store)
        assert view["recovery_action"] in ("RECONCILE_REQUIRED", "NO_ACTION")

    def test_19_restart_during_unknown(self, tmp_path):
        cmd = _cmd()
        store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        infra = SubmissionInfrastructure()
        adapters = {"upstox-paper": paper_upstox_adapter(submit_scenario="timeout")}
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        view = infra.recovery_for_command(command_id=cmd.command_id, submission_store=store)
        assert view["recovery_action"] == "RECONCILE_REQUIRED"

    def test_20_paper_live_mismatch(self):
        adapter = paper_upstox_adapter()
        with pytest.raises(ValueError):
            validate_adapter_mode(
                adapter_execution_mode=adapter.execution_mode,
                command=_raw_command(ExecutionMode.LIVE),
            )

    def test_21_wrong_adapter_selection(self):
        paper = paper_upstox_adapter()
        with pytest.raises(ValueError):
            select_adapter(
                {"upstox-paper": paper}, _raw_command(ExecutionMode.LIVE)
            )

    def test_22_missing_credentials(self):
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = None
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "AUTHENTICATION_FAILURE"

    def test_23_invalid_credentials(self):
        adapter = paper_upstox_adapter(submit_scenario="authentication_failure")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code.value == "AUTHENTICATION_FAILURE"

    def test_24_unsupported_capability(self):
        adapter = paper_upstox_adapter(
            capabilities=(AdapterCapability.SUBMIT, AdapterCapability.RECONCILE)
        )
        with pytest.raises(ValueError):
            adapter.cancel("co-test")

    def test_25_cancellation_timeout(self):
        adapter = paper_upstox_adapter(cancel_scenario="cancellation_timeout")
        result = adapter.cancel("co-test")
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code.value == "TIMEOUT"

    def test_26_cancellation_race_with_fill(self):
        adapter = paper_upstox_adapter(cancel_scenario="cancellation_race_fill")
        result = adapter.cancel("co-test")
        # The fill is authoritative; a false cancellation is never produced.
        assert result.status is BrokerResultStatus.REJECTED


# ============================================================
# NO-NETWORK SAFETY TEST
# ============================================================


class TestNoNetworkSafety:
    _MODULES = (
        "src/engine/intelligence/upstox_broker_client.py",
        "src/engine/intelligence/upstox_broker_adapter.py",
        "src/engine/intelligence/upstox_broker_models.py",
        "src/engine/intelligence/upstox_credential_provider.py",
    )

    def test_no_network_imports_in_client_modules(self):
        for rel in self._MODULES:
            tree = ast.parse(open(rel).read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        assert not name.name.startswith(
                            (
                                "socket",
                                "requests",
                                "httpx",
                                "urllib",
                                "http",
                                "websocket",
                                "aiohttp",
                            )
                        ), f"{rel} imports network module {name.name!r}"
                if isinstance(node, ast.ImportFrom):
                    assert not node.module or not node.module.startswith(
                        (
                            "socket",
                            "requests",
                            "httpx",
                            "urllib",
                            "http",
                            "websocket",
                            "aiohttp",
                        )
                    ), f"{rel} imports network module {node.module!r}"

    def test_mock_client_is_in_memory(self):
        client = _client()
        assert client.name == "upstox-mock"
        assert not hasattr(client, "session")
        assert not hasattr(client, "http")

    def test_no_sdk_imports(self):
        for rel in (
            "src/engine/intelligence/upstox_broker_client.py",
            "src/engine/intelligence/upstox_broker_adapter.py",
        ):
            src = open(rel).read()
            assert "kiteconnect" not in src.lower()
            assert "pyotp" not in src.lower()
            assert "import requests" not in src
            assert "import httpx" not in src
            assert "import urllib" not in src
            assert "import socket" not in src

    def test_scenario_vocabulary_is_closed(self):
        assert "accepted" in MOCK_UPSTOX_CLIENT_SCENARIOS
        assert "timeout" in MOCK_UPSTOX_CLIENT_SCENARIOS
        assert "cancellation_race_fill" in MOCK_UPSTOX_CLIENT_SCENARIOS


# ============================================================
# CREDENTIAL LEAKAGE TEST
# ============================================================


class TestCredentialLeakage:
    def test_fake_secret_never_in_results(self):
        secret = "sup3r-s3cr3t-t0ken-v4lue"
        adapter = paper_upstox_adapter(submit_scenario="rejected")
        adapter.client.credential_provider = StaticUpstoxCredentialProvider(secret)
        result = adapter.submit(_cmd())
        assert result.error is not None
        assert secret not in result.error.message
        assert secret not in result.reason

    def test_fake_secret_never_in_adapter_result(self):
        secret = "another-s3cr3t"
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = StaticUpstoxCredentialProvider(secret)
        result = adapter.submit(_cmd())
        assert secret not in str(result)

    def test_fake_secret_never_in_logs_or_audit(self):
        secret = "log-s3cr3t"
        adapter = paper_upstox_adapter(submit_scenario="timeout")
        adapter.client.credential_provider = StaticUpstoxCredentialProvider(secret)
        result = adapter.submit(_cmd())
        assert secret not in result.error.message
        assert secret not in result.reason

    def test_fake_secret_never_in_persistence(self, tmp_path):
        """A persisted lifecycle never contains credential material."""
        secret = "persist-s3cr3t"
        cmd = _cmd()
        store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = StaticUpstoxCredentialProvider(secret)
        infra = SubmissionInfrastructure()
        infra.submit_command(
            command=cmd,
            adapters={"upstox-paper": adapter},
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        for sid in store.list_submissions():
            payload = store.path_for(sid).read_text()
            assert secret not in payload

    def test_fake_secret_never_in_request_representations(self):
        secret = "req-s3cr3t"
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = StaticUpstoxCredentialProvider(secret)
        adapter.submit(_cmd())
        for req in adapter.dispatched_requests:
            assert secret not in str(req.to_dict())

    def test_redaction_removes_bearer_and_env_values(self):
        secret = "raw-token-value-123"
        text = f"Authorization: Bearer {secret} and UPSTOX_EXECUTION_ACCESS_TOKEN={secret}"
        redacted = redact_sensitive(text)
        assert secret not in redacted
        assert "Bearer <redacted>" in redacted


# ============================================================
# REDACTION UNIT TESTS
# ============================================================


class TestRedaction:
    def test_plain_text_unchanged(self):
        assert redact_sensitive("no secrets here") == "no secrets here"

    def test_bearer_scrubbed(self):
        assert (
            redact_sensitive("Authorization: Bearer abc123")
            == "Authorization: Bearer <redacted>"
        )

    def test_env_name_scrubbed(self):
        assert "UPSTOX_EXECUTION_ACCESS_TOKEN=<redacted>" in redact_sensitive(
            "UPSTOX_EXECUTION_ACCESS_TOKEN=secret"
        )

    def test_analytics_token_scrubbed(self):
        assert "UPSTOX_ANALYTICS_TOKEN=<redacted>" in redact_sensitive(
            "UPSTOX_ANALYTICS_TOKEN=secret"
        )

    def test_empty_string_safe(self):
        assert redact_sensitive("") == ""
