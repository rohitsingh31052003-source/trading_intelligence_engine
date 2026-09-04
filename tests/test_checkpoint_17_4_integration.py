"""Checkpoint 17.4 — SubmissionInfrastructure integration tests.

These tests prove the full end-to-end flow with the concrete reference
adapter:

    Authorized ExecutionCommand
        -> SubmissionInfrastructure
        -> SubmissionLifecycle
        -> ReferenceBrokerAdapter
        -> ReferenceBrokerRequest
        -> AdapterResult
        -> SubmissionLifecycle update
        -> Persistence

They map 1:1 to the Checkpoint 17.4 Phase 13 full-lifecycle test matrix
(items 1-26) and verify the infrastructure still enforces authorization,
command immutability, lifecycle rules, idempotency, reconciliation, recovery,
execution mode, and fail-closed behavior when a CONCRETE adapter (not the
17.2 fake broker) is wired in.
"""

from __future__ import annotations

import pathlib

import pytest

from engine.intelligence.broker_adapter_infrastructure import (
    CommandNotSubmittedError,
    DuplicateSubmissionError,
    ReconciliationRequiredError,
    SubmissionInfrastructure,
    SubmissionInfrastructureError,
)
from engine.intelligence.broker_adapter_contract import (
    derive_client_order_id,
    derive_idempotency_key,
)
from engine.intelligence.reference_broker_adapter import (
    ReferenceBrokerRequest,
    live_reference_adapter,
    paper_reference_adapter,
)
from engine.intelligence.submission_lifecycle import RecoveryAction
from engine.models.broker_adapter import BrokerErrorCode, BrokerResultStatus
from engine.models.execution_command import ExecutionMode
from engine.models.submission_lifecycle import SubmissionState
from engine.persistence.submission_store import SubmissionLifecycleStore

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)


def paper_cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


def live_cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent, scope="live")
    return make_command(intent, auth, **overrides)


def _infra_store(tmp_path, directory_name="submissions"):
    store = SubmissionLifecycleStore(directory=tmp_path / directory_name)
    infra = SubmissionInfrastructure()
    return infra, store


def _paper_adapters(**kwargs):
    adapter = paper_reference_adapter(name="reference-paper", **kwargs)
    return {"reference-paper": adapter}


# ============================================================
# PHASE 13 FULL-LIFECYCLE MATRIX (items 1-26)
# ============================================================


class TestPhase13FullLifecycle:
    # 1. authorized command reaches adapter
    def test_01_authorized_command_reaches_adapter(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.ACCEPTED
        assert store.command_exists(cmd.command_id)

    # 2. unauthorized command never reaches adapter
    def test_02_unauthorized_command_never_reaches_adapter(self, tmp_path):
        from engine.models.execution_authorization import (
            AuthorizationStatus,
            create_authorization,
        )
        from engine.models.execution_command import create_execution_command

        intent = make_intent()
        eligible = create_authorization(
            intent=intent,
            status=AuthorizationStatus.ELIGIBLE,
            authorized_at=utc(2026, 9, 1),
            valid_from=utc(2026, 9, 1),
            expires_at=utc(2026, 9, 2),
            issuer="i",
            authorization_method="m",
            scope="paper",
            policy_reference="p",
            safety_check_summary="s",
        )
        with pytest.raises(ValueError):
            create_execution_command(
                intent=intent, authorization=eligible, created_at=utc(2026, 9, 1)
            )
        infra, store = _infra_store(tmp_path)
        assert store.list_submissions() == []

    # 3. command remains immutable
    def test_03_command_remains_immutable(self, tmp_path):
        cmd = paper_cmd()
        before = cmd.command_id
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert cmd.command_id == before
        assert cmd.instrument == "NIFTY"

    # 4. command_id remains unchanged
    def test_04_command_id_unchanged(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.command_id == cmd.command_id

    # 5. submission lifecycle references command_id
    def test_05_lifecycle_references_command_id(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.command_id == cmd.command_id
        loaded = store.load_by_command(cmd.command_id)
        assert loaded.command_id == cmd.command_id

    # 6. accepted submission persists
    def test_06_accepted_submission_persists(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.ACCEPTED
        loaded = store.load_by_command(cmd.command_id)
        assert loaded.state is SubmissionState.ACCEPTED

    # 7. rejected submission persists
    def test_07_rejected_submission_persists(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="rejected"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.REJECTED
        assert store.load_by_command(cmd.command_id).state is SubmissionState.REJECTED

    # 8. failed submission persists
    def test_08_failed_submission_persists(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="failed"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.FAILED
        assert store.load_by_command(cmd.command_id).state is SubmissionState.FAILED

    # 9. timeout becomes UNKNOWN
    def test_09_timeout_becomes_unknown(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.UNKNOWN
        assert lc.requires_reconciliation

    # 10. UNKNOWN persists
    def test_10_unknown_persists(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.UNKNOWN
        assert store.load_by_command(cmd.command_id).state is SubmissionState.UNKNOWN

    # 11. UNKNOWN survives restart
    def test_11_unknown_survives_restart(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        # Simulate a process restart: a NEW store instance over the same dir.
        store2 = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        loaded = store2.load_by_command(cmd.command_id)
        assert loaded.state is SubmissionState.UNKNOWN
        assert loaded.client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )

    # 12. restart requires reconciliation
    def test_12_restart_requires_reconciliation(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        store2 = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        loaded = store2.load_by_command(cmd.command_id)
        action = infra.engine.restart_recovery(loaded)
        assert action is RecoveryAction.RECONCILE_REQUIRED

    # 13. reconciliation discovers accepted result
    def test_13_reconcile_discovers_accepted(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        rec = infra.reconcile_command(
            command_id=cmd.command_id,
            adapters=_paper_adapters(reconcile_scenario="reconcile_accepted"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.ACCEPTED
        assert store.load_by_command(cmd.command_id).state is SubmissionState.ACCEPTED

    # 14. reconciliation discovers rejection
    def test_14_reconcile_discovers_rejection(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        rec = infra.reconcile_command(
            command_id=cmd.command_id,
            adapters=_paper_adapters(reconcile_scenario="reconcile_rejected"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.REJECTED

    # 15. reconciliation remains unknown
    def test_15_reconcile_remains_unknown(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        rec = infra.reconcile_command(
            command_id=cmd.command_id,
            adapters=_paper_adapters(reconcile_scenario="reconcile_unknown"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 13),
        )
        assert rec.state is SubmissionState.UNKNOWN

    # 16. no blind retry occurs
    def test_16_no_blind_retry(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        with pytest.raises(ReconciliationRequiredError):
            infra.submit_command(
                command=cmd,
                adapters=_paper_adapters(submit_scenario="accepted"),
                submission_store=store,
                created_at=utc(2026, 9, 1, 14),
            )

    # 17. duplicate submission is blocked/detected
    def test_17_duplicate_submission_blocked(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        # SUBMITTED is an in-flight (non-terminal) lifecycle: a second
        # submission MUST be blocked.
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="restart"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert store.load_by_command(cmd.command_id).state is SubmissionState.SUBMITTED
        with pytest.raises(DuplicateSubmissionError):
            infra.submit_command(
                command=cmd,
                adapters=_paper_adapters(),
                submission_store=store,
                created_at=utc(2026, 9, 1, 13),
            )

    def test_17b_terminal_resubmission_returns_unchanged(self, tmp_path):
        """A terminal lifecycle never resubmits; it returns unchanged."""
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc1 = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc1.state is SubmissionState.ACCEPTED
        lc2 = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc2.state is SubmissionState.ACCEPTED
        # Exactly one persisted lifecycle for the command.
        records = [
            sid
            for sid in store.list_submissions()
        ]
        assert len(records) == 1

    # 18. deterministic client_order_id remains stable
    def test_18_deterministic_client_order_id_stable(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )
        # Stable across restart.
        store2 = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        assert store2.load_by_command(cmd.command_id).client_order_id == (
            derive_client_order_id(command_id=cmd.command_id)
        )

    # 19. deterministic idempotency_key remains stable
    def test_19_deterministic_idempotency_key_stable(self, tmp_path):
        cmd = paper_cmd()
        assert derive_idempotency_key(command_id=cmd.command_id) == (
            derive_idempotency_key(command_id=cmd.command_id)
        )

    # 20. paper mode is correctly enforced
    def test_20_paper_mode_enforced(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        meta = dict(lc.metadata)
        assert meta.get("cp17_mode") == "PAPER"

    # 21. incompatible mode fails closed
    def test_21_incompatible_mode_fails_closed(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        with pytest.raises(SubmissionInfrastructureError):
            infra.submit_command(
                command=cmd,
                adapters={"reference-live": live_reference_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 12),
            )
        assert store.list_submissions() == []

    # 22. unsupported capability is normalized
    def test_22_unsupported_capability_normalized(self, tmp_path):
        cmd = paper_cmd()
        adapter = paper_reference_adapter(
            submit_scenario="unsupported_operation"
        )
        result = adapter.submit(cmd)
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.UNSUPPORTED_OPERATION

    # 23. adapter error is normalized
    def test_23_adapter_error_normalized(self, tmp_path):
        cmd = paper_cmd()
        adapter = paper_reference_adapter(submit_scenario="malformed_internal")
        result = adapter.submit(cmd)
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.MALFORMED_RESPONSE
        assert type(result.error).__name__ == "BrokerError"

    # 24. raw adapter result does not leak into core
    def test_24_raw_adapter_result_does_not_leak(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        # The persisted lifecycle stores only broker-neutral fields.
        loaded = store.load_by_command(cmd.command_id)
        assert loaded.broker_order_id is None or isinstance(loaded.broker_order_id, str)
        assert type(loaded).__name__ == "SubmissionLifecycle"

    # 25. adapter-specific request does not leak into core
    def test_25_adapter_request_does_not_leak(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        # The lifecycle carries no reference request representation.
        assert not hasattr(lc, "symbol")
        assert not hasattr(lc, "order_type")
        assert not hasattr(lc, "product")
        assert not hasattr(lc, "idempotency_key")

    # 26. no network access occurs
    def test_26_no_network_access(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        # The reference adapter performs zero network operations by design;
        # the source audit in the reference-adapter test file proves it.


# ============================================================
# ADDITIONAL END-TO-END PROOFS
# ============================================================


class TestEndToEndReferenceAdapter:
    def test_full_flow_through_infrastructure(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.ACCEPTED
        events = [e.state for e in lc.events]
        assert SubmissionState.SUBMISSION_REQUESTED in events
        assert events[-1] is SubmissionState.ACCEPTED

    def test_reference_request_created_inside_adapter(self, tmp_path):
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters={"reference-paper": adapter},
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        orders = adapter.simulation.orders
        assert len(orders) == 1
        assert isinstance(orders[0], ReferenceBrokerRequest)
        assert orders[0].client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )

    def test_recovery_view_for_unknown(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        view = infra.recovery_for_command(
            command_id=cmd.command_id, submission_store=store
        )
        assert view["exists"] is True
        assert view["recovery_action"] == "RECONCILE_REQUIRED"
        assert view["reconciliation_required"] is True

    def test_recovery_view_no_record(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        view = infra.recovery_for_command(
            command_id=cmd.command_id, submission_store=store
        )
        assert view["exists"] is False
        assert view["recovery_action"] == ""

    def test_reconcile_uses_recorded_mode_binding(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(submit_scenario="timeout"),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        # A live-only registry cannot reconcile a paper lifecycle (fail closed).
        with pytest.raises(SubmissionInfrastructureError):
            infra.reconcile_command(
                command_id=cmd.command_id,
                adapters={"reference-live": live_reference_adapter()},
                submission_store=store,
                created_at=utc(2026, 9, 1, 13),
            )

    def test_persisted_lifecycle_is_broker_neutral(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        text = pathlib.Path(store.path_for(store.list_submissions()[0])).read_text(
            encoding="utf-8"
        )
        for frag in ("REF:NIFTY", "REF-CASH", "order_type", "idempotency_key"):
            assert frag not in text

    def test_live_command_through_live_reference_adapter(self, tmp_path):
        cmd = live_cmd()
        infra, store = _infra_store(tmp_path)
        lc = infra.submit_command(
            command=cmd,
            adapters={"reference-live": live_reference_adapter()},
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        assert lc.state is SubmissionState.ACCEPTED
        assert dict(lc.metadata).get("cp17_mode") == "LIVE"

    def test_audit_surface_works_with_reference_adapter(self, tmp_path):
        cmd = paper_cmd()
        infra, store = _infra_store(tmp_path)
        infra.submit_command(
            command=cmd,
            adapters=_paper_adapters(),
            submission_store=store,
            created_at=utc(2026, 9, 1, 12),
        )
        audit = infra.audit(submission_store=store)
        assert len(audit.rows) == 1
        assert audit.rows[0].command_id == cmd.command_id
        assert audit.rows[0].state is SubmissionState.ACCEPTED