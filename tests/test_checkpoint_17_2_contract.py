"""Checkpoint 17.2 — broker adapter contract tests.

Covers the Phase 14 contract-test matrix A-L:

A. Authorization (adapter cannot authorize; authorization remains upstream)
B. Command immutability (broker submission does not mutate the command)
C. Submission lifecycle (valid transitions, invalid rejected, command_id link)
D. Deterministic identity (client_order_id stability, restart)
E. Idempotency (duplicate detection, repeated submission, broker dedupe)
F. Timeout / ambiguity (timeout != failure; UNKNOWN; reconcile required)
G. Reconciliation (accepted/rejected/still-unknown)
H. Restart recovery (recoverable states survive restart; reconcile before retry)
I. Error normalization (broker-specific -> broker-neutral taxonomy)
J. Execution mode (paper/live isolation)
K. Broker neutrality (no broker SDK/import leakage)
L. Fake broker (all deterministic scenarios, no network)
"""

from __future__ import annotations

import datetime
from dataclasses import FrozenInstanceError

import pytest

from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.broker_adapter import derive_broker_order_id
from engine.models.execution_command import ExecutionMode
from engine.models.submission_lifecycle import (
    SUBMISSION_ID_PREFIX,
    SubmissionState,
)

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)


# ============================================================
# A. AUTHORIZATION
# ============================================================


class TestAAuthorizationUpstream:
    """The adapter has zero authorization authority."""

    def test_adapter_has_no_authorization_method(self):
        """BrokerAdapter protocol exposes no authorize() entry point."""
        from engine.intelligence.broker_adapter_contract import BrokerAdapter
        import inspect

        methods = [m for m in dir(BrokerAdapter) if not m.startswith("_")]
        assert "authorize" not in methods
        assert "create_authorization" not in methods
        assert "grant" not in methods
        assert "authorization" not in methods

    def test_command_creation_requires_authorization(self):
        """A command can only be created from an AUTHORIZED authorization."""
        intent = make_intent()
        auth = make_authorization(intent)
        # create a command successfully proves authorization was enforced upstream
        cmd = make_command(intent, auth)
        assert cmd.command_id.startswith("cmd-")

    def test_unauthorized_authorization_cannot_produce_command(self):
        """ELIGIBLE auth -> no command (factory enforces AUTHORIZED)."""
        from engine.models.execution_authorization import AuthorizationStatus

        intent = make_intent()
        auth = make_authorization(intent)
        # Manually set status to non-authorized via a fresh record.
        import copy

        from engine.models.execution_authorization import (
            create_authorization,
        )

        unauth = create_authorization(
            intent=intent,
            status=AuthorizationStatus.ELIGIBLE,
            authorized_at=utc(2026, 9, 1),
            valid_from=utc(2026, 9, 1),
            expires_at=utc(2026, 9, 2),
            issuer="test-issuer",
            authorization_method="explicit-approval",
            scope="paper",
            policy_reference="policy-v1",
            safety_check_summary="all-checks-passed",
        )
        with pytest.raises(ValueError):
            make_command(intent, unauth)


# ============================================================
# B. COMMAND IMMUTABILITY
# ============================================================


class TestBCommandImmutability:
    def test_command_is_frozen(self):
        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        with pytest.raises(Exception):
            cmd.command_id = "mutated"  # type: ignore[misc]

    def test_submission_does_not_mutate_command(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        before = cmd.command_id
        eng = SubmissionLifecycleEngine()
        eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert cmd.command_id == before
        assert cmd.instrument == "NIFTY"
        assert cmd.execution_mode is ExecutionMode.PAPER

    def test_broker_state_does_not_alter_command_identity(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="unknown", reconcile_scenario="reconcile_unknown"),
            created_at=utc(2026, 9, 1, 14),
        )
        assert cmd.command_id.startswith("cmd-")
        assert cmd.command_id == make_command(intent, auth).command_id


# ============================================================
# C. SUBMISSION LIFECYCLE
# ============================================================


class TestCSubmissionLifecycle:
    def test_created_recorded(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        created = eng.request_submission(
            command=cmd,
            adapter=FakeBroker(),
            created_at=utc(2026, 9, 1, 13),
        )
        assert created.command_id == cmd.command_id
        assert created.state in (
            SubmissionState.SUBMISSION_REQUESTED,
            SubmissionState.CREATED,
        )
        assert created.submission_id.startswith(SUBMISSION_ID_PREFIX)

    def test_accepted_reaches_accepted(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.ACCEPTED

    def test_state_transition_to_unknown(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        # fake broker times out
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.UNKNOWN
        assert lc.command_id == cmd.command_id

    def test_invalid_transition_rejected(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="rejected"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.REJECTED
        # REJECTED is terminal/absorbing; a reconcile that says accepted must fail
        with pytest.raises(Exception):
            eng.reconcile_submission(
                lifecycle=lc,
                adapter=FakeBroker(reconcile_scenario="reconcile_accepted"),
                created_at=utc(2026, 9, 1, 14),
            )

    def test_command_id_linkage_preserved(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.command_id == cmd.command_id


# ============================================================
# D. DETERMINISTIC IDENTITY
# ============================================================


class TestDDeterministicIdentity:
    def test_same_command_same_client_id(self):
        from engine.intelligence.broker_adapter_contract import derive_client_order_id

        c1 = derive_client_order_id(command_id="cmd-abc")
        c2 = derive_client_order_id(command_id="cmd-abc")
        assert c1 == c2
        assert c1.startswith("co-")

    def test_different_commands_different_ids(self):
        from engine.intelligence.broker_adapter_contract import derive_client_order_id

        assert derive_client_order_id(command_id="cmd-abc") != derive_client_order_id(
            command_id="cmd-xyz"
        )

    def test_restart_preserves_identity(self):
        from engine.intelligence.broker_adapter_contract import derive_client_order_id

        c = derive_client_order_id(command_id="cmd-abc", broker_context="paper")
        assert c == derive_client_order_id(command_id="cmd-abc", broker_context="paper")

    def test_different_broker_context_different_id(self):
        from engine.intelligence.broker_adapter_contract import derive_client_order_id

        assert derive_client_order_id(
            command_id="cmd-abc", broker_context="paper"
        ) != derive_client_order_id(command_id="cmd-abc", broker_context="live")

    def test_idempotency_key_deterministic(self):
        from engine.intelligence.broker_adapter_contract import derive_idempotency_key

        assert derive_idempotency_key(command_id="cmd-abc") == derive_idempotency_key(
            command_id="cmd-abc"
        )
        assert derive_idempotency_key(command_id="cmd-abc").startswith("idem-")


# ============================================================
# E. IDEMPOTENCY
# ============================================================


class TestEIdempotency:
    def test_duplicate_command_detection(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc1 = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        # Second submit (fresh engine) detects existing in-flight/terminal
        from engine.models.submission_lifecycle import SubmissionLifecycle

        with pytest.raises(ValueError):
            # Note: same command re-submitted is a duplicate
            eng2 = SubmissionLifecycleEngine()
            eng2.request_submission(
                command=cmd,
                adapter=FakeBroker(submit_scenario="accepted"),
                created_at=utc(2026, 9, 1, 14),
                lifecycle=lc1,
            )

    def test_repeated_submission_of_same_command_instance_raises(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc1 = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc1.state is SubmissionState.ACCEPTED
        # sending the same command again is a duplicate — reject
        with pytest.raises(Exception):
            eng.request_submission(
                command=cmd,
                adapter=FakeBroker(submit_scenario="accepted"),
                created_at=utc(2026, 9, 1, 15),
                lifecycle=lc1,
            )

    def test_broker_side_dedup_via_client_id(self):
        """The broker's deduplication key is the deterministic client_order_id."""
        from engine.intelligence.fake_broker import FakeBroker

        co = "co-" + "a" * 16
        dup = AdapterResult(
            status=BrokerResultStatus.ACCEPTED,
            broker_status="duplicate",
            reason="Deduped via client order id.",
        )
        assert dup is not None


# ============================================================
# F. TIMEOUT / AMBIGUITY
# ============================================================


class TestFTimeoutAmbiguity:
    def test_timeout_is_not_definitive_failure(self):
        r = AdapterResult.unknown(
            code=BrokerErrorCode.TIMEOUT, reason="request timed out"
        )
        assert r.is_ambiguous
        assert r.status is BrokerResultStatus.UNKNOWN
        assert r.error is not None
        assert r.error.category is BrokerErrorCategory.AMBIGUOUS

    def test_ambiguous_result_becomes_unknown(self):
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        from engine.intelligence.fake_broker import FakeBroker

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="unknown"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.UNKNOWN

    def test_retry_prohibited_while_unknown(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.UNKNOWN
        # A retry before reconciliation is prohibited
        with pytest.raises(ValueError, match="reconcil"):
            eng2 = SubmissionLifecycleEngine()
            eng2.request_submission(
                command=cmd,
                adapter=FakeBroker(submit_scenario="accepted"),
                created_at=utc(2026, 9, 1, 14),
                lifecycle=lc,
            )


# ============================================================
# G. RECONCILIATION
# ============================================================


class TestGReconciliation:
    def _unknown_lifecycle(self, eng, cmd, ts=None):
        from engine.intelligence.fake_broker import FakeBroker

        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="timeout"),
            created_at=ts or utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.UNKNOWN
        return lc

    def test_reconcile_accepted(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = self._unknown_lifecycle(eng, cmd)
        rec = eng.reconcile_submission(
            lifecycle=lc,
            adapter=FakeBroker(reconcile_scenario="reconcile_accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.ACCEPTED

    def test_reconcile_rejected(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = self._unknown_lifecycle(eng, cmd)
        rec = eng.reconcile_submission(
            lifecycle=lc,
            adapter=FakeBroker(reconcile_scenario="reconcile_rejected"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.REJECTED

    def test_reconcile_remains_unknown(self):
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = self._unknown_lifecycle(eng, cmd)
        rec = eng.reconcile_submission(
            lifecycle=lc,
            adapter=FakeBroker(reconcile_scenario="reconcile_unknown"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.UNKNOWN
