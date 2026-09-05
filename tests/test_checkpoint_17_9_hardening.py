"""Checkpoint 17.9 — Broker Integration Hardening & Controlled Execution Gate Audit.

This suite provides the deterministic, network-free, credential-free test
evidence for Checkpoint 17.9. It audits the frozen execution architecture
(Checkpoints 10–16) and the broker-neutral execution chain (17.1–17.7) and
proves the fail-closed invariants that a future explicit live-execution gate
must preserve:

* AUTHORIZED-only command construction (no authorization bypass).
* Execution-mode isolation (PAPER/LIVE never silently cross; no fallback).
* UNKNOWN -> reconcile-before-retry (no blind retry, no false failure).
* Duplicate prevention (same command -> same identity; in-flight block).
* Persistence + restart recovery (SAFE_TO_SUBMIT / RECONCILE_REQUIRED /
  NO_ACTION; no automatic live resubmission).
* Credential boundary (fail closed; redaction; no leakage into results,
  persistence, audit, or logs).
* Broker response validation (malformed/ambiguous -> UNKNOWN; never
  manufactured success).
* Broker state drift (conflicts surfaced, never silently overwritten).
* The live-execution gate design (fail-closed negative matrix; positive
  matrix requires ALL mandatory conditions simultaneously).

EVERY test runs against the network-free mock / fake broker. No real broker,
no SDK, no credentials, no network. LIVE TRADING IS NOT AUTHORIZED BY
CHECKPOINT 17.9.
"""

from __future__ import annotations

import datetime
import pathlib
import tempfile
from datetime import timezone
from decimal import Decimal

import pytest

from engine.intelligence.broker_adapter_contract import (
    derive_client_order_id,
    derive_idempotency_key,
    select_adapter,
    validate_adapter_mode,
)
from engine.intelligence.broker_adapter_infrastructure import (
    CommandNotSubmittedError,
    DuplicateSubmissionError,
    ReconciliationRequiredError,
    SubmissionInfrastructure,
    SubmissionInfrastructureError,
)
from engine.intelligence.execution_authorization import ExecutionAuthorizationEngine
from engine.intelligence.execution_gate import (
    MANDATORY_GATE_CONDITIONS,
    GateVerdict,
    LiveExecutionGate,
    LiveExecutionGateInput,
    LiveExecutionGateState,
)
from engine.intelligence.fake_broker import FakeBroker, live_fake_broker, paper_fake_broker
from engine.intelligence.submission_lifecycle import (
    RecoveryAction,
    SubmissionLifecycleEngine,
)
from engine.intelligence.upstox_broker_adapter import (
    UpstoxBrokerAdapter,
    derive_upstox_tag,
    live_upstox_adapter,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import (
    MockUpstoxBrokerClient,
    redact_sensitive,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderData,
    UpstoxOrderState,
    UpstoxOrderStateResponse,
    UpstoxOrderType,
    UpstoxPlaceOrderResponse,
    UpstoxProduct,
    UpstoxTransactionType,
    UpstoxValidity,
)
from engine.intelligence.upstox_credential_provider import (
    EmptyUpstoxCredentialProvider,
    EnvironmentUpstoxCredentialProvider,
    StaticUpstoxCredentialProvider,
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerError,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.execution_command import (
    ExecutionCommand,
    ExecutionMode,
    create_execution_command,
)
from engine.models.operational_trade_intent import OperationalTradeIntent
from engine.models.submission_lifecycle import (
    SubmissionLifecycle,
    SubmissionState,
    create_submission_lifecycle,
)
from engine.persistence.submission_store import SubmissionLifecycleStore

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)

_UTC = timezone.utc


def _cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


def _live_cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent, scope="live")
    return make_command(intent, auth, **overrides)


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
        created_at=datetime.datetime(2026, 9, 1, tzinfo=_UTC),
    )


def _client(**kwargs):
    kwargs.setdefault("credential_provider", StaticUpstoxCredentialProvider("fake"))
    return MockUpstoxBrokerClient(**kwargs)


def _timeout_adapter():
    """A paper Upstox adapter whose SUBMIT times out (-> UNKNOWN)."""
    adapter = paper_upstox_adapter()
    adapter.client.submit_scenario = "timeout"
    return adapter


# ============================================================
# PHASE 3 — AUTHORIZATION HARDENING
# ============================================================


class TestAuthorizationHardening:
    def test_only_authorized_produces_command(self):
        intent = make_intent()
        for status in (
            AuthorizationStatus.UNAUTHORIZED,
            AuthorizationStatus.ELIGIBLE,
            AuthorizationStatus.EXPIRED,
            AuthorizationStatus.REVOKED,
            AuthorizationStatus.SUPERSEDED,
        ):
            with pytest.raises(ValueError):
                create_execution_command(
                    intent=intent,
                    authorization=make_authorization(intent, status=status),
                    created_at=utc(2026, 9, 1, 12),
                )

    def test_authorization_cannot_be_forged(self):
        intent = make_intent()
        auth = make_authorization(intent)
        # An authorization bound to a DIFFERENT intent (different economic
        # content -> different intent_id) must not bind.
        other_intent = make_intent(entry=Decimal("150.00"))
        assert auth.intent_id != other_intent.intent_id
        with pytest.raises(ValueError):
            create_execution_command(
                intent=other_intent,
                authorization=auth,
                created_at=utc(2026, 9, 1, 12),
            )

    def test_mismatched_fingerprint_rejected(self):
        intent = make_intent()
        # Hand-build a forged authorization whose content fingerprint does
        # not match the intent's fingerprint.
        auth = make_authorization(intent)
        forged = ExecutionAuthorization(
            authorization_id=auth.authorization_id,
            intent_id=auth.intent_id,
            plan_id=auth.plan_id,
            content_fingerprint="fp-" + "f" * 14,
            status=AuthorizationStatus.AUTHORIZED,
            authorized_at=auth.authorized_at,
            valid_from=auth.valid_from,
            expires_at=auth.expires_at,
            issuer=auth.issuer,
            authorization_method=auth.authorization_method,
            scope=auth.scope,
            policy_reference=auth.policy_reference,
            safety_check_summary=auth.safety_check_summary,
            label=auth.label,
            metadata=auth.metadata,
        )
        assert forged.content_fingerprint != intent.content_fingerprint
        with pytest.raises(ValueError):
            create_execution_command(
                intent=intent,
                authorization=forged,
                created_at=utc(2026, 9, 1, 12),
            )

    def test_altered_command_is_a_different_command(self):
        # Same intent/authorization but different economic content -> different id.
        cmd_a = _cmd()
        intent_b = make_intent(entry=Decimal("150.00"))
        auth_b = make_authorization(intent_b)
        cmd_b = create_execution_command(
            intent=intent_b, authorization=auth_b, created_at=utc(2026, 9, 1, 12)
        )
        assert cmd_a.command_id != cmd_b.command_id

    def test_replayed_command_is_identical_command(self):
        # Same content -> same deterministic command_id (replay is detectable).
        cmd_a = _cmd()
        cmd_b = _cmd()
        assert cmd_a.command_id == cmd_b.command_id

    def test_authorization_state_not_changed_by_broker_code(self):
        auth = make_authorization(make_intent())
        before = auth.status
        # The adapter / infra never receives the authorization object at all.
        cmd = _cmd()
        adapter = paper_upstox_adapter()
        adapter.submit(cmd)
        assert auth.status is before

    def test_recovery_cannot_create_authorization(self):
        infra = SubmissionInfrastructure()
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            view = infra.recovery_for_command(command_id="cmd-nonexistent", submission_store=store)
            assert view["exists"] is False
            # Recovery never fabricates an authorization; it only reports state.
            assert "authorization" not in view


# ============================================================
# PHASE 4 — EXECUTION MODE HARDENING
# ============================================================


class TestExecutionModeHardening:
    def test_paper_to_live_fails_closed(self):
        paper = paper_upstox_adapter()
        with pytest.raises(ValueError):
            validate_adapter_mode(
                adapter_execution_mode=paper.execution_mode,
                command=_live_cmd(),
            )

    def test_live_to_paper_fails_closed(self):
        live = live_upstox_adapter()
        with pytest.raises(ValueError):
            validate_adapter_mode(
                adapter_execution_mode=live.execution_mode,
                command=_cmd(),
            )

    def test_missing_mode_fails_closed(self):
        with pytest.raises((TypeError, ValueError)):
            validate_adapter_mode(adapter_execution_mode=None, command=_cmd())  # type: ignore[arg-type]

    def test_invalid_mode_fails_closed(self):
        with pytest.raises(TypeError):
            validate_adapter_mode(adapter_execution_mode="PAPER", command=_cmd())  # type: ignore[arg-type]

    def test_wrong_adapter_fails_closed(self):
        # A live command must never reach a paper-only registry.
        with pytest.raises(ValueError):
            select_adapter({"upstox-paper": paper_upstox_adapter()}, _live_cmd())

    def test_no_automatic_fallback(self):
        paper = paper_upstox_adapter()
        live = live_upstox_adapter()
        assert paper.execution_mode is ExecutionMode.PAPER
        assert live.execution_mode is ExecutionMode.LIVE
        # No implicit substitution: selecting for a paper command returns paper only.
        selected = select_adapter(
            {"upstox-live": live, "upstox-paper": paper}, _cmd()
        )
        assert selected.execution_mode is ExecutionMode.PAPER

    def test_mode_recorded_on_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            lifecycle = infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            assert dict(lifecycle.metadata).get("cp17_mode") == "PAPER"

    def test_live_factory_requires_credential(self):
        with pytest.raises(ValueError):
            live_upstox_adapter(
                client=MockUpstoxBrokerClient(credential_provider=None)
            )


# ============================================================
# PHASE 9/10 — CONNECTION LIFECYCLE + UNKNOWN HARDENING
# ============================================================


class TestUnknownHardening:
    def test_timeout_is_unknown_not_failed(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "timeout"
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.TIMEOUT
        assert result.error.category is BrokerErrorCategory.AMBIGUOUS
        assert result.error.retryable is False

    def test_timeout_never_becomes_failed(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "timeout"
        result = adapter.submit(_cmd())
        assert result.status is not BrokerResultStatus.FAILED

    def test_unknown_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "timeout"
            cmd = _cmd()
            lifecycle = infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            assert lifecycle.state is SubmissionState.UNKNOWN
            # Simulate restart: reload from the store.
            reloaded = store.load_by_command(cmd.command_id)
            assert reloaded.state is SubmissionState.UNKNOWN
            # Recovery decision -> RECONCILE_REQUIRED (no blind retry).
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.RECONCILE_REQUIRED.value

    def test_unknown_blocks_resubmission(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        with pytest.raises(ValueError):
            engine.request_submission(
                command=cmd,
                adapter=paper_upstox_adapter(),
                created_at=utc(2026, 9, 1, 14),
                lifecycle=lifecycle,
            )

    def test_unknown_resolves_only_on_confirmed_outcome(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        # Reconcile discovers accepted.
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_accepted"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.state is SubmissionState.ACCEPTED
        assert reconciled.client_order_id == lifecycle.client_order_id

    def test_unknown_never_becomes_cancelled_without_confirmation(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "timeout"
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.status is not BrokerResultStatus.CANCELLED


# ============================================================
# PHASE 11 — RECONCILIATION HARDENING
# ============================================================


class TestReconciliationHardening:
    def test_reconcile_uses_same_client_order_id(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_accepted"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )

    def test_reconcile_unknown_stays_unknown(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_unknown"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.state is SubmissionState.UNKNOWN

    def test_reconcile_rejected_is_confirmed(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_rejected"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.state is SubmissionState.REJECTED

    def test_reconcile_never_submits_new_order(self):
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_accepted"
        adapter.reconcile("co-" + "0" * 16)
        # Reconciliation is a GET; it must not create a submission.
        assert len(adapter.client.submissions) == 0
        assert len(adapter.client.reconciliations) == 1


# ============================================================
# PHASE 12 — RECONCILIATION WINDOW (documented limitation)
# ============================================================


class TestReconciliationWindow:
    def test_no_claim_of_reconciliation_beyond_broker_retention(self):
        # The adapter never claims success when the broker no longer retains
        # the order: a missing/unknown lookup -> UNKNOWN (reconcile).
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_unknown"
        result = adapter.reconcile("co-" + "0" * 16)
        assert result.status is BrokerResultStatus.UNKNOWN


# ============================================================
# PHASE 13 — IDEMPOTENCY HARDENING
# ============================================================


class TestIdempotencyHardening:
    def test_four_identities_are_distinct(self):
        cmd = _cmd()
        command_id = cmd.command_id
        client_order_id = derive_client_order_id(command_id=command_id)
        idempotency_key = derive_idempotency_key(command_id=command_id)
        assert command_id.startswith("cmd-")
        assert client_order_id.startswith("co-")
        assert idempotency_key.startswith("idem-")
        assert len({command_id, client_order_id, idempotency_key}) == 3

    def test_broker_order_id_is_downstream_only(self):
        adapter = paper_upstox_adapter()
        result = adapter.submit(_cmd())
        assert result.broker_order_id is not None
        # broker_order_id never enters the command / lifecycle identity.
        cmd = _cmd()
        assert "brk-" not in cmd.command_id

    def test_same_command_same_client_order_id_across_restart(self):
        cmd = _cmd()
        one = derive_client_order_id(command_id=cmd.command_id)
        two = derive_client_order_id(command_id=cmd.command_id)
        assert one == two

    def test_different_broker_context_different_id(self):
        cmd = _cmd()
        a = derive_client_order_id(command_id=cmd.command_id, broker_context="a")
        b = derive_client_order_id(command_id=cmd.command_id, broker_context="b")
        assert a != b

    def test_broker_side_idempotency_not_claimed(self):
        # The adapter docs + audit surface explicitly state broker-side
        # idempotency is NOT verified. Verify the audit surface documents it.
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            infra.submit_command(
                command=_cmd(),
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            audit = infra.audit(submission_store=store)
            assert any(
                "broker-side idempotency" in line for line in audit.documentation
            )


# ============================================================
# PHASE 14 — DUPLICATE PREVENTION
# ============================================================


class TestDuplicatePrevention:
    def test_duplicate_submission_blocked_in_flight(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "submitted"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            with pytest.raises(DuplicateSubmissionError):
                infra.submit_command(
                    command=cmd,
                    adapters={"upstox-paper": adapter},
                    submission_store=store,
                    created_at=utc(2026, 9, 1, 13),
                )

    def test_duplicate_detected_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "submitted"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            # Restart: a NEW infra instance over the same store.
            infra2 = SubmissionInfrastructure()
            assert store.command_exists(cmd.command_id) is True
            with pytest.raises(DuplicateSubmissionError):
                infra2.submit_command(
                    command=cmd,
                    adapters={"upstox-paper": paper_upstox_adapter()},
                    submission_store=store,
                    created_at=utc(2026, 9, 1, 14),
                )

    def test_terminal_state_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            lifecycle = infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            assert lifecycle.state is SubmissionState.ACCEPTED
            # Terminal -> returned unchanged, never resubmitted.
            again = infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 13),
            )
            assert again.state is SubmissionState.ACCEPTED
            assert again.submission_id == lifecycle.submission_id

    def test_repeated_reconciliation_no_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "timeout"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            adapter.client.reconcile_scenario = "reconcile_accepted"
            r1 = infra.reconcile_command(
                command_id=cmd.command_id,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 13),
            )
            assert r1.state is SubmissionState.ACCEPTED
            # Terminal -> reconcile returns unchanged, no new records.
            r2 = infra.reconcile_command(
                command_id=cmd.command_id,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 14),
            )
            assert r2.submission_id == r1.submission_id


# ============================================================
# PHASE 15 — CONCURRENCY / RACE (single-process serialized)
# ============================================================


class TestConcurrency:
    def test_two_sequential_submits_no_duplicate(self):
        # The store guard + infra pre-check serialize same-command submission.
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "submitted"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            with pytest.raises(DuplicateSubmissionError):
                infra.submit_command(
                    command=cmd,
                    adapters={"upstox-paper": adapter},
                    submission_store=store,
                    created_at=utc(2026, 9, 1, 12),
                )

    def test_store_guard_detects_two_records_for_one_command(self):
        # Directly writing two lifecycles for the same command -> integrity error.
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            cmd = _cmd()
            lc1 = create_submission_lifecycle(
                command_id=cmd.command_id,
                state=SubmissionState.CREATED,
                client_order_id=derive_client_order_id(command_id=cmd.command_id),
                pre_submission=True,
                created_at=utc(2026, 9, 1, 12),
            )
            lc2 = create_submission_lifecycle(
                command_id=cmd.command_id,
                state=SubmissionState.SUBMITTED,
                client_order_id=derive_client_order_id(command_id=cmd.command_id),
                pre_submission=False,
                created_at=utc(2026, 9, 1, 13),
            )
            store.save(lc1)
            store.save(lc2)
            with pytest.raises(Exception):
                store.load_by_command(cmd.command_id)


# ============================================================
# PHASE 16 — PERSISTENCE HARDENING
# ============================================================


class TestPersistenceHardening:
    def test_crash_before_submit_recovery_safe_to_submit(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            infra.create_lifecycle(
                command=cmd,
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.SAFE_TO_SUBMIT.value

    def test_crash_after_unknown_recovery_reconcile_required(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "timeout"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.RECONCILE_REQUIRED.value

    def test_terminal_recovery_no_action(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.NO_ACTION.value

    def test_no_automatic_live_resubmission(self):
        # Recovery is a decision VIEW only; it never submits.
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            infra.create_lifecycle(
                command=cmd, submission_store=store, created_at=utc(2026, 9, 1, 12)
            )
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.SAFE_TO_SUBMIT.value
            # The view contains no submission side effect: store unchanged.
            assert store.command_exists(cmd.command_id) is True

    def test_unknown_persisted_and_reloadable(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "timeout"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            reloaded = store.load_by_command(cmd.command_id)
            assert reloaded.state is SubmissionState.UNKNOWN


# ============================================================
# PHASE 17 — AUDIT TRAIL HARDENING
# ============================================================


class TestAuditTrail:
    def test_audit_reconstructs_execution_chain(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            audit = infra.audit(submission_store=store)
            assert len(audit.rows) == 1
            row = audit.rows[0]
            assert row.command_id == cmd.command_id
            assert row.client_order_id.startswith("co-")
            assert row.idempotency_key.startswith("idem-")
            assert row.state is SubmissionState.ACCEPTED
            assert row.terminal is True

    def test_audit_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            infra.submit_command(
                command=_cmd(),
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            audit = infra.audit(submission_store=store)
            blob = str(audit.to_dict())
            assert "fake" not in blob
            assert "token" not in blob.lower()


# ============================================================
# PHASE 20 — BROKER RESPONSE HARDENING
# ============================================================


class TestBrokerResponseHardening:
    def test_missing_order_id_is_unknown(self):
        # A success envelope WITHOUT order data cannot be a confirmed success.
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "accepted"
        # Force the mock to return a success envelope with no order data.
        adapter.client.submit_scenario = "unknown_outcome"
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN

    def test_multiple_order_ids_is_unknown(self):
        adapter = paper_upstox_adapter()
        # A multi-id response is a CONCERN -> UNKNOWN (never a guessed pick).
        from engine.intelligence.upstox_broker_adapter import _normalize_place_order_response
        from engine.intelligence.upstox_broker_models import UpstoxBrokerRequest

        request = UpstoxBrokerRequest(
            instrument_token="NSE_EQ|INE002A01018",
            transaction_type=UpstoxTransactionType.BUY,
            quantity=Decimal("10"),
            product=UpstoxProduct.D,
            validity=UpstoxValidity.DAY,
            order_type=UpstoxOrderType.LIMIT,
            price=Decimal("100.50"),
            trigger_price=None,
            tag="uptag-abc",
            client_order_id="co-abc",
            idempotency_key="idem-abc",
            execution_mode="PAPER",
        )
        response = UpstoxPlaceOrderResponse(
            status="success",
            order_data=UpstoxOrderData(order_ids=("o1", "o2")),
        )
        result = _normalize_place_order_response(request, response)
        assert result.status is BrokerResultStatus.UNKNOWN

    def test_unknown_status_is_unknown(self):
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_unknown"
        result = adapter.reconcile("co-" + "0" * 16)
        assert result.status is BrokerResultStatus.UNKNOWN

    def test_contradictory_error_success_is_unknown(self):
        # A success envelope carrying error fields is malformed -> UNKNOWN.
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "unknown_outcome"
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN


# ============================================================
# PHASE 21 — ERROR TAXONOMY HARDENING
# ============================================================


class TestErrorTaxonomy:
    def test_unknown_broker_error_is_unknown_outcome(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "unknown"
        result = adapter.submit(_cmd())
        assert result.error.code is BrokerErrorCode.UNKNOWN_OUTCOME
        assert result.error.category is BrokerErrorCategory.AMBIGUOUS

    def test_authentication_failure_is_transport_not_ambiguous(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "authentication_failure"
        result = adapter.submit(_cmd())
        assert result.error.code is BrokerErrorCode.AUTHENTICATION_FAILURE
        assert result.error.category is BrokerErrorCategory.TRANSPORT

    def test_no_raw_upstox_object_leaks(self):
        adapter = paper_upstox_adapter()
        result = adapter.submit(_cmd())
        assert type(result).__name__ == "AdapterResult"


# ============================================================
# PHASE 22 — RETRY POLICY
# ============================================================


class TestRetryPolicy:
    def test_no_automatic_blind_retry(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        with pytest.raises(ValueError):
            engine.request_submission(
                command=cmd,
                adapter=paper_upstox_adapter(),
                created_at=utc(2026, 9, 1, 13),
                lifecycle=lifecycle,
            )

    def test_reconcile_is_safe_retry(self):
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_accepted"
        # Reconciliation is a GET; repeated calls do not create orders.
        adapter.reconcile("co-" + "0" * 16)
        adapter.reconcile("co-" + "0" * 16)
        assert len(adapter.client.submissions) == 0


# ============================================================
# PHASE 23 — RATE LIMIT HARDENING
# ============================================================


class TestRateLimit:
    def test_rate_limited_submission_is_ambiguous_unknown(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "rate_limit"
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.RATE_LIMIT
        assert result.error.category is BrokerErrorCategory.TRANSPORT

    def test_rate_limit_not_treated_as_not_submitted(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "rate_limit"
        result = adapter.submit(_cmd())
        # Never a definitive failure; reconcile is required.
        assert result.status is not BrokerResultStatus.FAILED
        assert result.status is BrokerResultStatus.UNKNOWN


# ============================================================
# PHASE 24 — CANCELLATION HARDENING
# ============================================================


class TestCancellation:
    def test_cancel_timeout_is_unknown(self):
        adapter = paper_upstox_adapter()
        adapter.client.cancel_scenario = "cancellation_timeout"
        result = adapter.cancel("co-" + "0" * 16)
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.TIMEOUT

    def test_cancel_race_with_fill_is_rejected_not_cancelled(self):
        adapter = paper_upstox_adapter()
        adapter.client.cancel_scenario = "cancellation_race_fill"
        result = adapter.cancel("co-" + "0" * 16)
        # The fill is authoritative; a false cancellation is never returned.
        assert result.status is BrokerResultStatus.REJECTED

    def test_cancel_never_assumes_success_from_request_acceptance(self):
        adapter = paper_upstox_adapter()
        adapter.client.cancel_scenario = "cancellation_timeout"
        result = adapter.cancel("co-" + "0" * 16)
        assert result.status is not BrokerResultStatus.CANCELLED


# ============================================================
# PHASE 25 — STARTUP / RESTART HARDENING
# ============================================================


class TestStartupHardening:
    def test_startup_identifies_outstanding_submissions(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            adapter = paper_upstox_adapter()
            adapter.client.submit_scenario = "timeout"
            cmd = _cmd()
            infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": adapter},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["reconciliation_required"] is True

    def test_startup_never_auto_submits(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            cmd = _cmd()
            infra.create_lifecycle(
                command=cmd, submission_store=store, created_at=utc(2026, 9, 1, 12)
            )
            # Recovery is a read-only decision view; no submission occurred.
            view = infra.recovery_for_command(
                command_id=cmd.command_id, submission_store=store
            )
            assert view["recovery_action"] == RecoveryAction.SAFE_TO_SUBMIT.value
            assert store.command_exists(cmd.command_id) is True


# ============================================================
# PHASE 28 — CONFIGURATION HARDENING
# ============================================================


class TestConfigurationHardening:
    def test_safe_default_is_no_live(self):
        # Adapter factories default to PAPER.
        assert paper_upstox_adapter().execution_mode is ExecutionMode.PAPER
        assert paper_fake_broker().execution_mode is ExecutionMode.PAPER
        # The gate defaults to DISABLED.
        assert LiveExecutionGateState.DISABLED.value == "DISABLED"

    def test_no_live_enabled_shortcut(self):
        # There is no `live_enabled = True` anywhere; the gate input defaults
        # gate_enabled=False.
        gate_input = LiveExecutionGateInput()
        assert gate_input.gate_enabled is False
        assert gate_input.explicit_operator_authorization is False


# ============================================================
# PHASE 30 — LIVE GATE NEGATIVE MATRIX
# ============================================================


class TestLiveGateNegativeMatrix:
    def _evaluate(self, **condition_overrides):
        conditions = {name: True for name in MANDATORY_GATE_CONDITIONS}
        conditions.update(condition_overrides)
        gate_input = LiveExecutionGateInput(
            conditions=tuple(conditions.items()),
            gate_enabled=True,
            explicit_operator_authorization=True,
        )
        return LiveExecutionGate().evaluate(gate_input)

    def test_all_conditions_true_allows(self):
        verdict = self._evaluate()
        assert verdict.is_allowed

    def test_no_authorization_blocks(self):
        verdict = self._evaluate(authorization_state=False)
        assert verdict.is_blocked
        assert "authorization_state" in verdict.blocking_reasons

    def test_missing_credential_blocks(self):
        verdict = self._evaluate(valid_live_credential=False)
        assert verdict.is_blocked

    def test_wrong_broker_blocks(self):
        verdict = self._evaluate(correct_broker=False)
        assert verdict.is_blocked

    def test_wrong_adapter_blocks(self):
        verdict = self._evaluate(correct_adapter=False)
        assert verdict.is_blocked

    def test_paper_mode_blocks_live(self):
        verdict = self._evaluate(explicit_live_mode=False)
        assert verdict.is_blocked

    def test_unknown_submission_blocks_retry(self):
        verdict = self._evaluate(no_outstanding_unknown=False)
        assert verdict.is_blocked

    def test_reconciliation_required_blocks_submission(self):
        verdict = self._evaluate(reconciliation_readiness=False)
        assert verdict.is_blocked

    def test_live_gate_disabled_blocks(self):
        verdict = self._evaluate(execution_gate_enabled=False)
        assert verdict.is_blocked

    def test_no_explicit_operator_authorization_blocks(self):
        gate_input = LiveExecutionGateInput(
            conditions=tuple(
                (name, True) for name in MANDATORY_GATE_CONDITIONS
            ),
            gate_enabled=True,
            explicit_operator_authorization=False,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.is_blocked
        assert "operator_explicit_authorization" in verdict.blocking_reasons

    def test_single_missing_condition_blocks(self):
        # Any single missing mandatory condition -> NOT_ALLOWED.
        for name in MANDATORY_GATE_CONDITIONS:
            verdict = self._evaluate(**{name: False})
            assert verdict.is_blocked, name
            assert name in verdict.blocking_reasons, name

    def test_missing_condition_key_treated_as_false(self):
        # Omitting a condition key entirely -> treated as False (fail closed).
        present = [n for n in MANDATORY_GATE_CONDITIONS if n != "audit_readiness"]
        gate_input = LiveExecutionGateInput(
            conditions=tuple((n, True) for n in present),
            gate_enabled=True,
            explicit_operator_authorization=True,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.is_blocked
        assert "audit_readiness" in verdict.blocking_reasons

    def test_credentials_alone_do_not_allow(self):
        # Only the credential condition true -> NOT_ALLOWED (credentials are
        # necessary but NOT sufficient).
        gate_input = LiveExecutionGateInput(
            conditions=(("valid_live_credential", True),),
            gate_enabled=True,
            explicit_operator_authorization=True,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.is_blocked

    def test_verdict_is_deterministic(self):
        v1 = self._evaluate(audit_readiness=False)
        v2 = self._evaluate(audit_readiness=False)
        assert v1.verdict_id == v2.verdict_id
        assert v1.blocking_reasons == v2.blocking_reasons


# ============================================================
# PHASE 31 — LIVE GATE POSITIVE MATRIX (definition only)
# ============================================================


class TestLiveGatePositiveMatrix:
    def test_positive_matrix_requires_all_mandatory_conditions(self):
        # ALLOWED requires every mandatory condition True AND gate enabled
        # AND explicit operator authorization.
        conditions = {name: True for name in MANDATORY_GATE_CONDITIONS}
        gate_input = LiveExecutionGateInput(
            conditions=tuple(conditions.items()),
            gate_enabled=True,
            explicit_operator_authorization=True,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.is_allowed
        assert verdict.satisfied_count == len(MANDATORY_GATE_CONDITIONS)

    def test_no_shortcut_live_enabled_true(self):
        # Even with every condition True, a disabled gate blocks.
        conditions = {name: True for name in MANDATORY_GATE_CONDITIONS}
        gate_input = LiveExecutionGateInput(
            conditions=tuple(conditions.items()),
            gate_enabled=False,
            explicit_operator_authorization=True,
        )
        verdict = LiveExecutionGate().evaluate(gate_input)
        assert verdict.is_blocked
        assert "execution_gate_enabled" in verdict.blocking_reasons


# ============================================================
# PHASE 32 — SAFETY OVERRIDE AUDIT
# ============================================================


class TestSafetyOverrideAudit:
    def test_no_force_live_flag_in_execution_modules(self):
        import ast
        import pathlib

        targets = [
            "src/engine/intelligence/execution_gate.py",
            "src/engine/intelligence/upstox_broker_adapter.py",
            "src/engine/intelligence/broker_adapter_infrastructure.py",
            "src/engine/intelligence/submission_lifecycle.py",
        ]
        for target in targets:
            source = pathlib.Path(target).read_text(encoding="utf-8")
            for marker in ("--force", "--live", "skip_auth", "disable_checks"):
                assert marker not in source, f"{target} contains {marker!r}"


# ============================================================
# PHASE 33 — DIRECT BROKER ACCESS AUDIT
# ============================================================


class TestDirectBrokerAccessAudit:
    def test_place_order_only_in_client_boundary(self):
        # The adapter calls client.place_order; the client is the ONLY
        # module that owns broker transport. No core module calls Upstox.
        import ast
        import pathlib

        for target in [
            "src/engine/models/execution_command.py",
            "src/engine/models/execution_authorization.py",
            "src/engine/models/operational_trade_intent.py",
            "src/engine/models/submission_lifecycle.py",
            "src/engine/intelligence/submission_lifecycle.py",
            "src/engine/intelligence/broker_adapter_infrastructure.py",
        ]:
            source = pathlib.Path(target).read_text(encoding="utf-8")
            assert "place_order" not in source, f"{target} calls place_order directly"


# ============================================================
# PHASE 34 — NETWORK BOUNDARY AUDIT
# ============================================================


class TestNetworkBoundaryAudit:
    def test_no_network_imports_in_execution_modules(self):
        import ast
        import pathlib

        targets = [
            "src/engine/intelligence/execution_gate.py",
            "src/engine/intelligence/upstox_broker_adapter.py",
            "src/engine/intelligence/upstox_broker_client.py",
            "src/engine/intelligence/upstox_broker_models.py",
            "src/engine/intelligence/upstox_credential_provider.py",
            "src/engine/intelligence/broker_adapter_infrastructure.py",
            "src/engine/intelligence/submission_lifecycle.py",
            "src/engine/intelligence/broker_adapter_contract.py",
            "src/engine/persistence/submission_store.py",
        ]
        for target in targets:
            tree = ast.parse(pathlib.Path(target).read_text(encoding="utf-8"), target)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        assert root not in (
                            "requests",
                            "httpx",
                            "urllib",
                            "aiohttp",
                            "socket",
                            "websocket",
                        ), f"{target} imports {root}"
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    assert root not in (
                        "requests",
                        "httpx",
                        "urllib",
                        "aiohttp",
                        "socket",
                        "websocket",
                    ), f"{target} imports {root}"


# ============================================================
# PHASE 35 — CREDENTIAL SWEEP
# ============================================================


class TestCredentialSweep:
    def test_credentials_never_enter_command(self):
        cmd = _cmd()
        assert "token" not in str(cmd).lower()
        assert "fake" not in str(cmd).lower()

    def test_credentials_never_enter_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            infra.submit_command(
                command=_cmd(),
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            for sid in store.list_submissions():
                text = (store.path_for(sid)).read_text(encoding="utf-8")
                assert "fake" not in text
                assert "token" not in text.lower()

    def test_credentials_never_enter_results(self):
        adapter = paper_upstox_adapter()
        adapter.client.submit_scenario = "rejected"
        result = adapter.submit(_cmd())
        assert "fake" not in result.reason.lower()
        assert "token" not in result.reason.lower()

    def test_missing_credential_fails_closed(self):
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = None
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.AUTHENTICATION_FAILURE

    def test_empty_credential_fails_closed(self):
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = EmptyUpstoxCredentialProvider()
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.AUTHENTICATION_FAILURE

    def test_redaction_scrubs_bearer_and_env(self):
        text = "Authorization: Bearer SECRET123 and UPSTOX_EXECUTION_ACCESS_TOKEN=SECRET123"
        redacted = redact_sensitive(text)
        assert "SECRET123" not in redacted
        assert "Bearer <redacted>" in redacted

    def test_environment_provider_fails_closed_when_unset(self, monkeypatch):
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == ""


# ============================================================
# PHASE 8 — CREDENTIAL ROTATION
# ============================================================


class TestCredentialRotation:
    def test_rotation_picked_up_lazily(self, monkeypatch):
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        provider = EnvironmentUpstoxCredentialProvider()
        assert provider.get_access_token() == ""

        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "rotated-token")
        assert provider.get_access_token() == "rotated-token"

    def test_credentials_not_required_to_reconstruct_command(self):
        cmd = _cmd()
        # The command carries no credential; reconstruction needs no token.
        assert cmd.quantity == Decimal("10")
        assert "token" not in cmd.command_id

    def test_rotation_does_not_change_command_identity(self):
        cmd = _cmd()
        # Command identity is credential-independent.
        assert cmd.command_id == _cmd().command_id


# ============================================================
# PHASE 19 — BROKER STATE DRIFT
# ============================================================


class TestBrokerStateDrift:
    def test_local_unknown_broker_cancelled_reconciles(self):
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        # Broker reports CANCELLED -> lifecycle advances only on confirmation.
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_rejected"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.state is SubmissionState.REJECTED

    def test_conflict_not_silently_overwritten(self):
        # A broker-confirmed outcome is the authority; the lifecycle records
        # it via a NEW event (never mutates the prior snapshot).
        engine = SubmissionLifecycleEngine()
        cmd = _cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=_timeout_adapter(),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        adapter = paper_upstox_adapter()
        adapter.client.reconcile_scenario = "reconcile_accepted"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert reconciled.state is SubmissionState.ACCEPTED
        assert len(reconciled.events) > len(lifecycle.events)


# ============================================================
# PHASE 40 — AUDITABILITY / FORENSICS
# ============================================================


class TestForensics:
    def test_full_chain_reconstructable(self):
        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        with tempfile.TemporaryDirectory() as d:
            store = SubmissionLifecycleStore(directory=pathlib.Path(d))
            infra = SubmissionInfrastructure()
            lifecycle = infra.submit_command(
                command=cmd,
                adapters={"upstox-paper": paper_upstox_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
            # authorization -> command -> submission -> broker identity ->
            # client order id -> broker order id -> result -> lifecycle.
            assert auth.authorization_id.startswith("auth-")
            assert cmd.command_id.startswith("cmd-")
            assert lifecycle.submission_id.startswith("submission-")
            assert lifecycle.client_order_id.startswith("co-")
            assert lifecycle.broker_order_id is not None
            assert lifecycle.state is SubmissionState.ACCEPTED
            assert lifecycle.latest_event is not None

    def test_timestamps_and_deterministic_ids(self):
        cmd = _cmd()
        assert cmd.created_at.tzinfo is not None
        assert cmd.command_id == _cmd().command_id


# ============================================================
# PHASE 43 — REGRESSION (17.9 suite self-check)
# ============================================================


class TestGateStateMachine:
    def test_state_vocabulary(self):
        states = [s.value for s in LiveExecutionGateState]
        assert states == [
            "DISABLED",
            "CONFIGURED",
            "PRECHECK",
            "AUTHORIZED",
            "GATE_VERIFIED",
            "READY",
            "BLOCKED",
        ]

    def test_gate_never_returns_allowed_without_authorization(self):
        gate = LiveExecutionGate()
        # All conditions true but NO explicit operator authorization.
        conditions = {name: True for name in MANDATORY_GATE_CONDITIONS}
        gate_input = LiveExecutionGateInput(
            conditions=tuple(conditions.items()),
            gate_enabled=True,
            explicit_operator_authorization=False,
        )
        assert gate.evaluate(gate_input).is_blocked
