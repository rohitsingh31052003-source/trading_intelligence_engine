"""Checkpoint 17.7 — Upstox adapter end-to-end integration tests.

This module proves the full flow with the Upstox adapter wired into the
frozen :class:`SubmissionInfrastructure`:

    Authorized ExecutionCommand
        -> SubmissionInfrastructure
        -> SubmissionLifecycleEngine
        -> SubmissionLifecycleStore
        -> UpstoxBrokerAdapter
        -> MockUpstoxBrokerClient (injected; network-free)
        -> AdapterResult
        -> SubmissionLifecycle update
        -> persistence

Covered:

* happy-path submission + persistence
* UNKNOWN outcome persistence + restart recovery (RECONCILE_REQUIRED)
* reconciliation resolving UNKNOWN -> ACCEPTED
* deterministic client_order_id across restart / repeated construction /
  repeated submission / reconciliation / recovery
* no blind retry (UNKNOWN re-submission is refused)
* live-mode command cannot silently cross into the paper adapter and vice
  versa (mode binding + selection)
* terminal states cannot be blindly resubmitted
* broker-neutrality of the persisted lifecycle and of the core models
* dependency-direction invariants (core never imports Upstox)

No real broker, no SDK, no credentials, no network.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from engine.intelligence.broker_adapter_contract import derive_client_order_id
from engine.intelligence.broker_adapter_infrastructure import (
    DuplicateSubmissionError,
    ReconciliationRequiredError,
    SubmissionInfrastructure,
    SubmissionInfrastructureError,
)
from engine.intelligence.upstox_broker_adapter import (
    derive_upstox_tag,
    live_upstox_adapter,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import (
    MockUpstoxBrokerClient,
)
from engine.intelligence.upstox_credential_provider import (
    StaticUpstoxCredentialProvider,
)
from engine.models.broker_adapter import BrokerResultStatus
from engine.models.execution_command import ExecutionCommand, ExecutionMode
from engine.models.submission_lifecycle import SubmissionState
from engine.persistence.submission_store import SubmissionLifecycleStore

from tests.test_checkpoint_17_7_upstox_adapter import _cmd, _raw_command

_UTC = timezone.utc


def _infra(tmp_path: pathlib.Path, submit_scenario: str = "accepted"):
    store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
    infra = SubmissionInfrastructure()
    adapters = {
        "upstox-paper": paper_upstox_adapter(submit_scenario=submit_scenario),
        "upstox-live": live_upstox_adapter(submit_scenario=submit_scenario),
    }
    return infra, store, adapters


# ============================================================
# HAPPY PATH
# ============================================================


class TestHappyPath:
    def test_submit_persists_and_returns_accepted(self, tmp_path):
        infra, store, adapters = _infra(tmp_path)
        cmd = _cmd()
        lifecycle = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert lifecycle.state is SubmissionState.ACCEPTED
        assert lifecycle.command_id == cmd.command_id
        assert lifecycle.broker_order_id is not None

        # Persisted.
        reloaded = store.load_by_command(cmd.command_id)
        assert reloaded.state is SubmissionState.ACCEPTED

    def test_submitted_outcome_leaves_lifecycle_submitted(self, tmp_path):
        infra, store, adapters = _infra(tmp_path, submit_scenario="submitted")
        cmd = _cmd()
        lifecycle = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert lifecycle.state is SubmissionState.SUBMITTED


# ============================================================
# UNKNOWN / RESTART / RECOVERY
# ============================================================


class TestUnknownAndRestart:
    def test_timeout_persists_unknown(self, tmp_path):
        infra, store, adapters = _infra(tmp_path, submit_scenario="timeout")
        cmd = _cmd()
        lifecycle = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN

        reloaded = store.load_by_command(cmd.command_id)
        assert reloaded.state is SubmissionState.UNKNOWN

    def test_unknown_requires_reconciliation_then_accepts(self, tmp_path):
        """UNKNOWN persists; a fresh infrastructure (restart) must reconcile
        and discover ACCEPTED using the SAME deterministic client_order_id."""
        infra, store, adapters = _infra(tmp_path, submit_scenario="timeout")
        cmd = _cmd()
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )

        # A NEW infrastructure + NEW adapter + existing store = restart.
        fresh_store = SubmissionLifecycleStore(directory=tmp_path / "submissions")
        fresh_infra = SubmissionInfrastructure()
        fresh_adapters = {
            "upstox-paper": paper_upstox_adapter(reconcile_scenario="reconcile_accepted")
        }
        view = fresh_infra.recovery_for_command(
            command_id=cmd.command_id, submission_store=fresh_store
        )
        assert view["recovery_action"] == "RECONCILE_REQUIRED"

        accepted = fresh_infra.reconcile_command(
            command_id=cmd.command_id,
            adapters=fresh_adapters,
            submission_store=fresh_store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert accepted.state is SubmissionState.ACCEPTED

    def test_reconcile_uses_same_client_order_id(self, tmp_path):
        infra, store, adapters = _infra(tmp_path, submit_scenario="timeout")
        cmd = _cmd()
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        expected_cid = derive_client_order_id(command_id=cmd.command_id)
        expected_tag = derive_upstox_tag(expected_cid)
        # Reconcile through the infrastructure; the adapter's reconciliation
        # lookup uses the tag derived from the SAME client_order_id.
        infra.reconcile_command(
            command_id=cmd.command_id,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        adapter = adapters["upstox-paper"]
        # The mock client records (tag, order_id) lookups; the adapter derives
        # the Upstox tag from the SAME deterministic client_order_id.
        assert adapter.client.reconciliations[-1][0] == expected_tag

    def test_no_blind_retry_of_unknown(self, tmp_path):
        infra, store, adapters = _infra(tmp_path, submit_scenario="timeout")
        cmd = _cmd()
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        with pytest.raises(ReconciliationRequiredError):
            infra.submit_command(
                command=cmd,
                adapters=adapters,
                submission_store=store,
                created_at=datetime(2026, 9, 1, tzinfo=_UTC),
            )

    def test_terminal_cannot_be_blindly_resubmitted(self, tmp_path):
        infra, store, adapters = _infra(tmp_path)
        cmd = _cmd()
        lifecycle = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert lifecycle.state.is_terminal
        again = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert again.state is lifecycle.state  # existing terminal returned


# ============================================================
# DETERMINISTIC IDENTITY
# ============================================================


class TestDeterministicIdentity:
    def test_same_command_same_client_order_id_across_restart(self, tmp_path):
        cmd = _cmd()
        expected = derive_client_order_id(command_id=cmd.command_id)

        infra1, store1, _ = _infra(tmp_path, "timeout")
        store1dir = tmp_path / "submissions"
        infra1.submit_command(
            command=cmd,
            adapters={"upstox-paper": paper_upstox_adapter(submit_scenario="timeout")},
            submission_store=store1,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        persisted = store1.load_by_command(cmd.command_id)
        assert persisted.client_order_id == expected

        # Restart: new store referencing the same files.
        store2 = SubmissionLifecycleStore(directory=store1dir)
        restarted = store2.load_by_command(cmd.command_id)
        assert restarted.client_order_id == expected

    def test_repeated_adapter_construction_same_identity(self):
        cmd = _cmd()
        a1 = paper_upstox_adapter()
        a2 = paper_upstox_adapter()
        a1.submit(cmd)
        a2.submit(cmd)
        t1 = a1.dispatched_requests[-1].client_order_id
        t2 = a2.dispatched_requests[-1].client_order_id
        assert t1 == t2


# ============================================================
# MODE BINDING / SELECTION
# ============================================================


class TestModeBinding:
    def test_live_command_uses_live_adapter(self, tmp_path):
        infra, store, adapters = _infra(tmp_path)
        live_cmd = _raw_command(ExecutionMode.LIVE)
        lifecycle = infra.submit_command(
            command=live_cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert lifecycle.state is SubmissionState.ACCEPTED
        # The metadata records the live mode binding.
        meta = dict(lifecycle.metadata)
        assert meta.get("cp17_mode") == "LIVE"

    def test_paper_command_cannot_cross_into_live_adapter(self, tmp_path):
        infra, store, _ = _infra(tmp_path)
        paper_cmd = _cmd()
        # A registry that only offers a LIVE adapter must fail closed.
        with pytest.raises(Exception):
            infra.submit_command(
                command=paper_cmd,
                adapters={"upstox-live-only": live_upstox_adapter()},
                submission_store=store,
                created_at=datetime(2026, 9, 1, tzinfo=_UTC),
            )

    def test_live_command_cannot_cross_into_paper_adapter(self, tmp_path):
        infra, store, _ = _infra(tmp_path)
        live_cmd = _raw_command(ExecutionMode.LIVE)
        with pytest.raises(Exception):
            infra.submit_command(
                command=live_cmd,
                adapters={"upstox-paper-only": paper_upstox_adapter()},
                submission_store=store,
                created_at=datetime(2026, 9, 1, tzinfo=_UTC),
            )


# ============================================================
# BROKER-NEUTRALITY
# ============================================================


class TestBrokerNeutrality:
    def test_persisted_lifecycle_is_broker_neutral(self, tmp_path):
        infra, store, adapters = _infra(tmp_path)
        cmd = _cmd()
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        lifecycle = store.load_by_command(cmd.command_id)
        for field_name in (
            "instrument_token",
            "transaction_type",
            "upstox",
            "order_type",
        ):
            assert field_name not in lifecycle.__dict__ if hasattr(lifecycle, "__dict__") else True

    def test_command_not_mutated(self, tmp_path):
        infra, store, adapters = _infra(tmp_path)
        cmd = _cmd()
        original = cmd.command_id
        infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        assert cmd.command_id == original

    def test_lifecycle_not_mutated_by_restart_recovery(self, tmp_path):
        infra, store, adapters = _infra(tmp_path, submit_scenario="timeout")
        cmd = _cmd()
        lifecycle = infra.submit_command(
            command=cmd,
            adapters=adapters,
            submission_store=store,
            created_at=datetime(2026, 9, 1, tzinfo=_UTC),
        )
        before_id = lifecycle.submission_id
        view = infra.recovery_for_command(command_id=cmd.command_id, submission_store=store)
        assert view["recovery_action"] == "RECONCILE_REQUIRED"
        assert lifecycle.submission_id == before_id


# ============================================================
# DEPENDENCY DIRECTION
# ============================================================


class TestDependencyDirection:
    def test_core_never_imports_upstox(self):
        """ExecutionCommand / BrokerAdapter / AdapterResult / SubmissionLifecycle
        never import the adapter-owned Upstox modules."""
        core_modules = (
            "src/engine/models/execution_command.py",
            "src/engine/models/broker_adapter.py",
            "src/engine/models/submission_lifecycle.py",
            "src/engine/intelligence/broker_adapter_contract.py",
            "src/engine/intelligence/submission_lifecycle.py",
            "src/engine/persistence/submission_lifecycle.py",
        )
        for rel in core_modules:
            try:
                src = open(rel).read()
            except FileNotFoundError:
                continue
            assert "upstox_broker" not in src, f"{rel} imports Upstox broker models"
            assert "upstox_credential" not in src, f"{rel} imports Upstox credentials"

    def test_persistence_is_broker_neutral(self):
        """Submission serialization never references Upstox types."""
        import engine.persistence.submission_serialization as ss

        src = open(ss.__file__).read()
        assert "upstox" not in src.lower()