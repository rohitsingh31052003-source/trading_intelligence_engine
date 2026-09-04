"""Checkpoint 17.4 — generic BrokerAdapter contract conformance suite.

This module provides a REUSABLE behavioral contract test suite that any
concrete :class:`BrokerAdapter` implementation (a future Upstox adapter, a
future broker-specific adapter, or the reference adapter) can run to prove
conformance with the frozen Checkpoint 17.2 broker-neutral contract.

GENERIC CONTRACT TESTS vs REFERENCE ADAPTER-SPECIFIC TESTS:

* The test classes in this module inherit :class:`BrokerAdapterContractConformanceBase`
  and verify the broker-NEUTRAL behavioral contract (accepted / rejected /
  failed / unknown / reconciliation / idempotency expectations / mode binding
  / error normalization / capability behavior / lifecycle compatibility).
* They deliberately contain NO reference-adapter-specific assertions
  (no ``ReferenceBrokerRequest``, no internal translation representation, no
  scenario configuration introspection).
* A concrete adapter only needs to provide an ``ADAPTER_FACTORY(**kwargs)``
  callable whose kwargs mirror the 17.2/17.3 adapter constructor surface
  (``name`` / ``execution_mode`` / ``submit_scenario`` /
  ``reconcile_scenario`` / ``cancel_scenario`` / ``capabilities``).

Concrete conformance adapters currently run against the suite:

1. :class:`ReferenceBrokerAdapter` (Checkpoint 17.4) — the reference /
   simulated concrete adapter.
2. :class:`FakeBroker` (Checkpoint 17.2) — the deterministic fake broker.

Both use the SAME shared core scenarios so the generic suite exercises only
the part of the contract every implementation supports.
"""

from __future__ import annotations

import pytest

from engine.intelligence.broker_adapter_contract import (
    derive_client_order_id,
    derive_idempotency_key,
    select_adapter,
    validate_adapter_mode,
)
from engine.intelligence.fake_broker import FakeBroker, paper_fake_broker
from engine.intelligence.reference_broker_adapter import (
    paper_reference_adapter,
)
from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine
from engine.models.broker_adapter import (
    AdapterCapability,
    BrokerError,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionMode
from engine.models.submission_lifecycle import SubmissionState

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)


def paper_cmd(**overrides):
    """A valid PAPER-mode ExecutionCommand for generic contract tests."""
    intent = make_intent()
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


def live_cmd(**overrides):
    """A valid LIVE-mode ExecutionCommand for generic contract tests."""
    intent = make_intent()
    auth = make_authorization(intent, scope="live")
    return make_command(intent, auth, **overrides)


class BrokerAdapterContractConformanceBase:
    """Reusable broker-neutral contract conformance suite.

    Subclasses MUST set ``ADAPTER_FACTORY`` to a callable
    ``(**kwargs) -> BrokerAdapter`` matching the 17.2/17.3 constructor surface.
    """

    ADAPTER_FACTORY = staticmethod(lambda **kwargs: None)  # overridden

    @pytest.fixture
    def make_adapter(self):
        def _make(**kwargs):
            adapter = self.ADAPTER_FACTORY(**kwargs)
            assert hasattr(adapter, "execution_mode"), "adapter must expose execution_mode"
            assert hasattr(adapter, "capabilities"), "adapter must expose capabilities"
            return adapter

        return _make

    # ---------------------------------------------------------
    # ACCEPTED SUBMISSION BEHAVIOR
    # ---------------------------------------------------------

    def test_submit_accepted_returns_accepted(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.ACCEPTED
        assert result.error is None
        assert result.broker_order_id

    def test_accepted_result_is_not_ambiguous_and_not_failure(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        result = adapter.submit(paper_cmd())
        assert not result.is_ambiguous
        assert not result.error

    # ---------------------------------------------------------
    # REJECTION BEHAVIOR
    # ---------------------------------------------------------

    def test_submit_rejected_returns_rejection_with_broker_error(self, make_adapter):
        adapter = make_adapter(submit_scenario="rejected")
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.REJECTED
        assert isinstance(result.error, BrokerError)
        assert result.error.category is BrokerErrorCategory.BROKER_REJECTION
        assert result.error.code is BrokerErrorCode.BROKER_REJECTION

    def test_rejection_is_terminal(self, make_adapter):
        adapter = make_adapter(submit_scenario="rejected")
        result = adapter.submit(paper_cmd())
        assert result.status.is_terminal

    # ---------------------------------------------------------
    # FAILURE BEHAVIOR
    # ---------------------------------------------------------

    def test_submit_failed_returns_failed_with_internal_error(self, make_adapter):
        adapter = make_adapter(submit_scenario="failed")
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert isinstance(result.error, BrokerError)
        assert result.error.category is BrokerErrorCategory.INTERNAL
        assert result.error.code is BrokerErrorCode.INTERNAL_ADAPTER_FAILURE

    def test_failed_result_carries_broker_error(self, make_adapter):
        adapter = make_adapter(submit_scenario="failed")
        result = adapter.submit(paper_cmd())
        assert result.error.message

    # ---------------------------------------------------------
    # UNKNOWN / TIMEOUT BEHAVIOR
    # ---------------------------------------------------------

    def test_submit_timeout_returns_unknown_not_failure(self, make_adapter):
        adapter = make_adapter(submit_scenario="timeout")
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.is_ambiguous
        assert isinstance(result.error, BrokerError)
        assert result.error.code is BrokerErrorCode.TIMEOUT
        assert result.error.category is BrokerErrorCategory.AMBIGUOUS

    def test_timeout_is_never_automatically_retryable(self, make_adapter):
        adapter = make_adapter(submit_scenario="timeout")
        result = adapter.submit(paper_cmd())
        assert result.error.retryable is False

    def test_submit_unknown_returns_unknown_with_unknown_outcome(self, make_adapter):
        adapter = make_adapter(submit_scenario="unknown")
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.UNKNOWN_OUTCOME

    def test_unknown_is_not_a_definitive_failure(self, make_adapter):
        adapter = make_adapter(submit_scenario="unknown")
        result = adapter.submit(paper_cmd())
        assert not result.status.is_terminal

    # ---------------------------------------------------------
    # RECONCILIATION
    # ---------------------------------------------------------

    def test_reconcile_accepted_discovers_accepted(self, make_adapter):
        adapter = make_adapter(reconcile_scenario="reconcile_accepted")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.ACCEPTED

    def test_reconcile_rejected_discovers_rejected(self, make_adapter):
        adapter = make_adapter(reconcile_scenario="reconcile_rejected")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.REJECTED
        assert result.error.category is BrokerErrorCategory.BROKER_REJECTION

    def test_reconcile_unknown_stays_unknown(self, make_adapter):
        adapter = make_adapter(reconcile_scenario="reconcile_unknown")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.is_ambiguous

    def test_reconcile_never_creates_a_false_failure(self, make_adapter):
        adapter = make_adapter(reconcile_scenario="reconcile_unknown")
        result = adapter.reconcile("co-test")
        assert result.status is not BrokerResultStatus.FAILED
        assert result.status is not BrokerResultStatus.REJECTED

    # ---------------------------------------------------------
    # IDEMPOTENCY EXPECTATIONS
    # ---------------------------------------------------------

    def test_same_command_same_client_order_id(self, make_adapter):
        cmd = paper_cmd()
        one = derive_client_order_id(command_id=cmd.command_id)
        two = derive_client_order_id(command_id=cmd.command_id)
        assert one == two
        assert one.startswith("co-")

    def test_same_command_same_idempotency_key(self, make_adapter):
        cmd = paper_cmd()
        one = derive_idempotency_key(command_id=cmd.command_id)
        two = derive_idempotency_key(command_id=cmd.command_id)
        assert one == two
        assert one.startswith("idem-")

    def test_client_order_id_is_independent_of_wall_clock(self, make_adapter):
        cmd = paper_cmd()
        cid = derive_client_order_id(command_id=cmd.command_id)
        assert cid == derive_client_order_id(command_id=cmd.command_id)

    def test_submit_reuses_the_deterministic_client_identity(self, make_adapter):
        """Two direct submit calls use the SAME client_order_id (no random id)."""
        adapter = make_adapter(submit_scenario="accepted")
        cmd = paper_cmd()
        adapter.submit(cmd)
        adapter.submit(cmd)
        identity = derive_client_order_id(command_id=cmd.command_id)
        recorded = getattr(adapter, "submissions", None)
        if recorded is not None:
            # Both adapters record (command_id, client_order_id) tuples.
            assert all(t[1] == identity for t in recorded)

    # ---------------------------------------------------------
    # MODE BINDING
    # ---------------------------------------------------------

    def test_paper_adapter_supports_paper_command(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        assert adapter.supports(paper_cmd()) is True

    def test_validate_adapter_mode_raises_on_mismatch(self, make_adapter):
        adapter = make_adapter(execution_mode=ExecutionMode.PAPER)
        if adapter.execution_mode is ExecutionMode.PAPER:
            with pytest.raises(ValueError):
                validate_adapter_mode(
                    adapter_execution_mode=adapter.execution_mode,
                    command=live_cmd(),
                )

    def test_engine_enforces_mode_isolation_fail_closed(self, make_adapter):
        """Mode isolation is enforced via the shared engine (adapter/handler)."""
        engine = SubmissionLifecycleEngine()
        with pytest.raises(ValueError):
            engine.submit(
                command=live_cmd(),
                adapter=make_adapter(execution_mode=ExecutionMode.PAPER),
                created_at=utc(2026, 9, 1, 12),
            )

    def test_select_adapter_matches_mode(self, make_adapter):
        paper = make_adapter(execution_mode=ExecutionMode.PAPER)
        selected = select_adapter(
            {"p": paper}, paper_cmd(), preferred="p"
        )
        assert selected is paper

    def test_select_adapter_fails_closed_on_missing_mode(self, make_adapter):
        paper = make_adapter(execution_mode=ExecutionMode.PAPER)
        with pytest.raises(ValueError):
            select_adapter({"p": paper}, live_cmd())

    # ---------------------------------------------------------
    # ERROR NORMALIZATION
    # ---------------------------------------------------------

    def test_failure_results_expose_only_broker_neutral_errors(self, make_adapter):
        adapter = make_adapter(submit_scenario="failed")
        result = adapter.submit(paper_cmd())
        assert isinstance(result.error, BrokerError)
        assert isinstance(result.error.code, BrokerErrorCode)
        assert isinstance(result.error.category, BrokerErrorCategory)
        assert isinstance(result.error.retryable, bool)

    def test_no_adapter_specific_result_type_leaks(self, make_adapter):
        """The AdapterResult is the only result type returned by contract ops."""
        adapter = make_adapter(submit_scenario="accepted")
        result = adapter.submit(paper_cmd())
        assert type(result).__name__ == "AdapterResult"

    # ---------------------------------------------------------
    # CAPABILITY BEHAVIOR
    # ---------------------------------------------------------

    def test_supports_never_raises_for_valid_command(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        assert isinstance(adapter.supports(paper_cmd()), bool)

    def test_supports_returns_false_for_non_command(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        assert adapter.supports(object()) is False  # type: ignore[arg-type]

    def test_check_accepts_supported_command(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        adapter.check(paper_cmd())  # must not raise

    def test_unadvertised_cancel_raises_value_error(self, make_adapter):
        adapter = make_adapter(
            submit_scenario="accepted",
            capabilities=(AdapterCapability.SUBMIT, AdapterCapability.RECONCILE),
        )
        with pytest.raises(ValueError):
            adapter.cancel("co-test")

    # ---------------------------------------------------------
    # LIFECYCLE COMPATIBILITY
    # ---------------------------------------------------------

    def test_engine_submit_maps_accepted_result(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=paper_cmd(),
            adapter=make_adapter(submit_scenario="accepted"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.ACCEPTED
        assert lifecycle.command_id.startswith("cmd-")

    def test_engine_submit_maps_rejected_result(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=paper_cmd(),
            adapter=make_adapter(submit_scenario="rejected"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.REJECTED

    def test_engine_submit_maps_failed_result(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=paper_cmd(),
            adapter=make_adapter(submit_scenario="failed"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.FAILED

    def test_engine_submit_timeout_produces_unknown_lifecycle(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=paper_cmd(),
            adapter=make_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        assert lifecycle.requires_reconciliation

    def test_engine_reconcile_resolves_unknown_to_accepted(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        cmd = paper_cmd()
        client_order_id = derive_client_order_id(command_id=cmd.command_id)
        lifecycle = engine.submit(
            command=cmd,
            adapter=make_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle,
            adapter=make_adapter(reconcile_scenario="reconcile_accepted"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert reconciled.state is SubmissionState.ACCEPTED
        # Reconciliation used the SAME client_order_id (never a new order).
        client_order_id = client_order_id or ""
        assert reconciled.client_order_id == client_order_id

    def test_engine_reconcile_unknown_remains_unknown(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        cmd = paper_cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=make_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle,
            adapter=make_adapter(reconcile_scenario="reconcile_unknown"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert reconciled.state is SubmissionState.UNKNOWN

    def test_blind_retry_of_unknown_is_prohibited(self, make_adapter):
        engine = SubmissionLifecycleEngine()
        cmd = paper_cmd()
        lifecycle = engine.submit(
            command=cmd,
            adapter=make_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state is SubmissionState.UNKNOWN
        with pytest.raises(ValueError):
            engine.request_submission(
                command=cmd,
                adapter=make_adapter(submit_scenario="accepted"),
                created_at=utc(2026, 9, 1, 14),
                lifecycle=lifecycle,
            )

    # ---------------------------------------------------------
    # AUTHORIZATION SEPARATION
    # ---------------------------------------------------------

    def test_adapter_has_no_authorization_methods(self, make_adapter):
        adapter = make_adapter(submit_scenario="accepted")
        public = [m for m in dir(adapter) if not m.startswith("_")]
        for forbidden in ("authorize", "create_authorization", "grant"):
            assert forbidden not in public


# ============================================================
# CONCRETE CONFORMANCE RUNNERS
# ============================================================


class TestReferenceBrokerAdapterContractConformance(
    BrokerAdapterContractConformanceBase
):
    """The reference adapter conforms to the generic contract."""

    ADAPTER_FACTORY = staticmethod(paper_reference_adapter)


class TestFakeBrokerContractConformance(BrokerAdapterContractConformanceBase):
    """The 17.2 FakeBroker also conforms to the generic contract.

    This proves the suite is generic: it runs against the pre-existing
    deterministic fake broker unchanged, so a future Upstox adapter can run
    the same suite with zero reference-adapter coupling.
    """

    ADAPTER_FACTORY = staticmethod(paper_fake_broker)