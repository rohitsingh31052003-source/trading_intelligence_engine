"""Deterministic fake broker / fake adapter (Checkpoint 17.2 Phase 13).

This module provides a deterministic, network-free :class:`FakeBroker` that
implements the broker-neutral :class:`BrokerAdapter` contract so the broker
boundary can be tested without any external service.

The fake broker NEVER:

* connects to any external service,
* requires credentials,
* submits real orders,
* accesses the filesystem or network.

It simulates, per configured scenario:

* accepted submission (``accepted``),
* explicit rejection (``rejected``),
* deterministic failure (``failed``),
* timeout -> UNKNOWN (``timeout``),
* unknown/ambiguous outcome (``unknown``),
* reconciliation discovers accepted order (``reconcile_accepted``),
* reconciliation discovers rejected order (``reconcile_rejected``),
* reconciliation remains unknown (``reconcile_unknown``),
* duplicate submission attempt (``duplicate``),
* restart/recovery scenario (``restart``).

Scenario behaviour is fully deterministic: the same scenario + same command
produces the same :class:`AdapterResult`. The fake broker records every
operation it receives (``submissions`` / ``reconciliations`` / ``cancels``)
so tests can assert broker-neutrality and idempotency semantics.
"""

from __future__ import annotations

from typing import Any

from engine.intelligence.broker_adapter_contract import (
    AdapterCapabilities,
    BrokerAdapter,
    derive_client_order_id,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerErrorCode,
    BrokerResultStatus,
    derive_broker_order_id,
)
from engine.models.execution_command import ExecutionCommand, ExecutionMode


#: Recognized deterministic fake-broker scenarios.
FAKE_BROKER_SCENARIOS = (
    "accepted",
    "rejected",
    "failed",
    "timeout",
    "unknown",
    "reconcile_accepted",
    "reconcile_rejected",
    "reconcile_unknown",
    "duplicate",
    "restart",
)


class FakeBroker:
    """Deterministic fake broker implementing the :class:`BrokerAdapter`.

    Attributes:
        name: Adapter name (for registration/selection).
        execution_mode: The execution mode this fake is bound to (PAPER or
            LIVE). NEVER silently overridden.
        capabilities: Declared :class:`AdapterCapabilities`.
        submit_scenario: The scenario applied to ``submit``.
        reconcile_scenario: The scenario applied to ``reconcile``.
        cancel_scenario: The scenario applied to ``cancel``.
        submissions / reconciliations / cancels: Operation logs (lists of
            (command_id, client_order_id) tuples) for assertions.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        submit_scenario: str = "accepted",
        reconcile_scenario: str = "reconcile_accepted",
        cancel_scenario: str = "accepted",
        capabilities: tuple[AdapterCapability, ...] = (
            AdapterCapability.SUBMIT,
            AdapterCapability.RECONCILE,
            AdapterCapability.CANCEL,
        ),
    ) -> None:
        if submit_scenario not in FAKE_BROKER_SCENARIOS:
            raise ValueError(
                f"Unknown submit_scenario {submit_scenario!r}; "
                f"expected one of {FAKE_BROKER_SCENARIOS}."
            )
        if reconcile_scenario not in FAKE_BROKER_SCENARIOS:
            raise ValueError(
                f"Unknown reconcile_scenario {reconcile_scenario!r}; "
                f"expected one of {FAKE_BROKER_SCENARIOS}."
            )
        if cancel_scenario not in FAKE_BROKER_SCENARIOS:
            raise ValueError(
                f"Unknown cancel_scenario {cancel_scenario!r}; "
                f"expected one of {FAKE_BROKER_SCENARIOS}."
            )
        self.name = name
        self.execution_mode = execution_mode
        self.capabilities = AdapterCapabilities(
            capabilities=tuple(capabilities), execution_mode=execution_mode
        )
        self.submit_scenario = submit_scenario
        self.reconcile_scenario = reconcile_scenario
        self.cancel_scenario = cancel_scenario
        self.submissions: list[tuple[str, str]] = []
        self.reconciliations: list[tuple[str, str]] = []
        self.cancels: list[tuple[str, str]] = []

    # ---------------------------------------------------------
    # BROKER ADAPTER CONTRACT
    # ---------------------------------------------------------

    def submit(self, command: ExecutionCommand) -> AdapterResult:
        """Submit an already-authorized command (deterministic scenario)."""

        if not isinstance(command, ExecutionCommand):
            raise TypeError(
                f"command must be an ExecutionCommand; "
                f"got {type(command).__name__!r}."
            )
        client_order_id = derive_client_order_id(command_id=command.command_id)
        self.submissions.append((command.command_id, client_order_id))
        return self._result_for(self.submit_scenario, client_order_id, "submit")

    def reconcile(self, client_order_id: str) -> AdapterResult:
        """Reconcile an earlier submission by its client order id."""

        self.reconciliations.append(("", client_order_id))
        return self._result_for(
            self.reconcile_scenario, client_order_id, "reconcile"
        )

    def cancel(self, client_order_id: str) -> AdapterResult:
        """Request cancellation (deterministic scenario)."""

        if AdapterCapability.CANCEL not in self.capabilities.capabilities:
            raise ValueError(
                f"FakeBroker {self.name!r} does not support CANCEL."
            )
        self.cancels.append(("", client_order_id))
        return self._result_for(self.cancel_scenario, client_order_id, "cancel")

    def supports(self, command: ExecutionCommand) -> bool:
        """Capability boundary: this fake supports every command."""
        return isinstance(command, ExecutionCommand)

    def check(self, command: ExecutionCommand) -> None:
        """Pre-validation: no-op for the fake (everything is supported)."""
        return None

    # ---------------------------------------------------------
    # INTERNAL
    # ---------------------------------------------------------

    def _result_for(self, scenario: str, client_order_id: str, operation: str) -> AdapterResult:
        """Deterministic AdapterResult for a scenario."""

        broker_order_id = derive_broker_order_id(
            client_order_id=client_order_id,
            operation=operation,
            scenario=scenario,
        )
        if scenario == "accepted":
            return AdapterResult(
                status=BrokerResultStatus.ACCEPTED,
                broker_order_id=broker_order_id,
                broker_status="accepted",
                reason="Fake broker accepted the order.",
            )
        if scenario == "rejected":
            return AdapterResult.rejected(
                reason="Fake broker rejected the order.",
                broker_order_id=broker_order_id,
            )
        if scenario == "failed":
            return AdapterResult.failed(
                code=BrokerErrorCode.INTERNAL_ADAPTER_FAILURE,
                reason="Fake broker internal failure.",
                broker_order_id=broker_order_id,
            )
        if scenario == "timeout":
            return AdapterResult.unknown(
                code=BrokerErrorCode.TIMEOUT,
                reason="Fake broker request timed out (ambiguous).",
            )
        if scenario == "unknown":
            return AdapterResult.unknown(
                code=BrokerErrorCode.UNKNOWN_OUTCOME,
                reason="Fake broker returned an unknown outcome.",
            )
        if scenario == "reconcile_accepted":
            return AdapterResult(
                status=BrokerResultStatus.ACCEPTED,
                broker_order_id=broker_order_id,
                broker_status="accepted",
                reason="Fake broker reconciliation confirmed acceptance.",
            )
        if scenario == "reconcile_rejected":
            return AdapterResult.rejected(
                reason="Fake broker reconciliation confirmed rejection.",
                broker_order_id=broker_order_id,
            )
        if scenario == "reconcile_unknown":
            return AdapterResult.unknown(
                code=BrokerErrorCode.UNKNOWN_OUTCOME,
                reason="Fake broker reconciliation could not determine outcome.",
            )
        if scenario == "duplicate":
            return AdapterResult(
                status=BrokerResultStatus.ACCEPTED,
                broker_order_id=broker_order_id,
                broker_status="duplicate",
                reason="Fake broker deduplicated via client order id.",
            )
        if scenario == "restart":
            return AdapterResult(
                status=BrokerResultStatus.SUBMITTED,
                broker_order_id=broker_order_id,
                broker_status="submitted",
                reason="Fake broker reports submission after restart.",
            )
        raise ValueError(f"Unknown scenario {scenario!r}.")


#: Convenience factory for a paper fake broker.
def paper_fake_broker(**kwargs: Any) -> FakeBroker:
    """Build a PAPER-mode fake broker (default)."""
    kwargs.setdefault("execution_mode", ExecutionMode.PAPER)
    return FakeBroker(**kwargs)


#: Convenience factory for a live fake broker.
def live_fake_broker(**kwargs: Any) -> FakeBroker:
    """Build a LIVE-mode fake broker."""
    kwargs.setdefault("execution_mode", ExecutionMode.LIVE)
    return FakeBroker(**kwargs)


__all__ = [
    "FAKE_BROKER_SCENARIOS",
    "FakeBroker",
    "live_fake_broker",
    "paper_fake_broker",
]