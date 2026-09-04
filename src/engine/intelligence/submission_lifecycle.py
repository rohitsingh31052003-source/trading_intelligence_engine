"""Submission lifecycle engine (Checkpoint 17.2 Phases 5/8/9/10).

This module provides the :class:`SubmissionLifecycleEngine`, a stateless,
pure orchestrator for the broker-neutral submission / order lifecycle.

CRITICAL INVARIANT:

* :class:`~engine.models.execution_command.ExecutionCommand` is an IMMUTABLE
  historical instruction. This engine NEVER mutates a command.
* :class:`~engine.models.submission_lifecycle.SubmissionLifecycle` snapshots
  are immutable records associated with ``command_id``. Progression produces
  NEW immutable snapshot records (never in-place mutation).

Authority:

* Authorization occurs BEFORE this boundary. The engine never authorizes.
* The engine accepts an ALREADY-AUTHORIZED immutable command (created via
  :func:`~engine.models.execution_command.create_execution_command`) and an
  adapter (a :class:`~engine.intelligence.broker_adapter_contract.BrokerAdapter`).
* The adapter's bound execution_mode is verified against the command's
  execution_mode BEFORE every operation -- a paper/live mismatch raises
  ValueError and nothing is submitted (no silent cross).

Reconcile-before-retry (Phase 8 - CRITICAL SAFETY REQUIREMENT):

* A UNKNOWN submission state means "the broker may or may not have accepted
  the request". Blind retry is PROHIBITED.
* request_submission on an existing UNKNOWN lifecycle raises ValueError --
  the caller MUST reconcile first.
* reconcile_submission queries the adapter with the SAME deterministic
  client_order_id and advances state ONLY on a confirmed outcome.
* If reconciliation still cannot determine the outcome, the lifecycle stays
  UNKNOWN and a retry remains prohibited.

Restart recovery (Phase 9):

* restart_recovery is a pure decision function over a persisted lifecycle
  snapshot (loaded after a process restart):
    * CREATED / SUBMISSION_REQUESTED (pre_submission=True) -> SAFE_TO_SUBMIT
      (the broker has never been contacted; a fresh submission with the same
       deterministic idempotency identity is safe).
    * SUBMISSION_REQUESTED (pre_submission=False) / SUBMITTED / UNKNOWN ->
      RECONCILE_REQUIRED (the broker MAY have received the request).
    * terminal states -> NO_ACTION.
* The contract NEVER assumes that the absence of a local acknowledgement
  means the broker did not receive the order.

Duplicate prevention (Phase 7):

* The deterministic client_order_id derived from command_id (+ broker
  context) is reused for every submission of the same command.
* request_submission refuses to re-submit a command that already has a
  terminal or in-flight lifecycle for the same command_id.

No datetime.now()/datetime.utcnow() -- all timestamps are caller-supplied.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from engine.intelligence.broker_adapter_contract import (
    BrokerAdapter,
    derive_client_order_id,
    validate_adapter_mode,
)
from engine.models.broker_adapter import AdapterResult, BrokerResultStatus
from engine.models.execution_command import ExecutionCommand
from engine.models.submission_lifecycle import (
    SubmissionEvent,
    SubmissionLifecycle,
    SubmissionState,
    create_submission_lifecycle,
)


class RecoveryAction(Enum):
    """Deterministic restart-recovery action for a persisted lifecycle."""

    SAFE_TO_SUBMIT = "SAFE_TO_SUBMIT"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    NO_ACTION = "NO_ACTION"
    INVALID = "INVALID"


_RESULT_TO_STATE: dict[BrokerResultStatus, SubmissionState] = {
    BrokerResultStatus.SUBMITTED: SubmissionState.SUBMITTED,
    BrokerResultStatus.ACCEPTED: SubmissionState.ACCEPTED,
    BrokerResultStatus.PARTIALLY_FILLED: SubmissionState.PARTIALLY_FILLED,
    BrokerResultStatus.FILLED: SubmissionState.FILLED,
    BrokerResultStatus.CANCELLED: SubmissionState.CANCELLED,
    BrokerResultStatus.REJECTED: SubmissionState.REJECTED,
    BrokerResultStatus.UNKNOWN: SubmissionState.UNKNOWN,
}


def _allowed_transition(current: SubmissionState, target: SubmissionState) -> bool:
    """Whether a transition from ``current`` to ``target`` is allowed.

    * CREATED -> SUBMISSION_REQUESTED / FAILED / CANCELLED.
    * SUBMISSION_REQUESTED -> SUBMITTED / ACCEPTED / REJECTED / UNKNOWN /
      FAILED (pre_submission flips to False once the request hits the wire).
    * SUBMITTED -> ACCEPTED / REJECTED / UNKNOWN / PARTIALLY_FILLED /
      CANCELLED / FAILED (later/reconciliation results).
    * ACCEPTED -> PARTIALLY_FILLED / FILLED / REJECTED / CANCELLED / FAILED /
      UNKNOWN.
    * PARTIALLY_FILLED -> FILLED / REJECTED / CANCELLED / UNKNOWN / FAILED.
    * UNKNOWN -> any known outcome (SUBMITTED / ACCEPTED / REJECTED /
      PARTIALLY_FILLED / FILLED / CANCELLED / FAILED). Reconciliation MUST
      resolve UNKNOWN before any retry.
    * FILLED / CANCELLED / REJECTED / FAILED are absorbing.
    """

    table: dict[SubmissionState, set[SubmissionState]] = {
        SubmissionState.CREATED: {
            SubmissionState.SUBMISSION_REQUESTED,
            SubmissionState.FAILED,
            SubmissionState.CANCELLED,
        },
        SubmissionState.SUBMISSION_REQUESTED: {
            SubmissionState.SUBMITTED,
            SubmissionState.ACCEPTED,
            SubmissionState.REJECTED,
            SubmissionState.UNKNOWN,
            SubmissionState.FAILED,
        },
        SubmissionState.SUBMITTED: {
            SubmissionState.ACCEPTED,
            SubmissionState.REJECTED,
            SubmissionState.UNKNOWN,
            SubmissionState.PARTIALLY_FILLED,
            SubmissionState.CANCELLED,
            SubmissionState.FAILED,
        },
        SubmissionState.ACCEPTED: {
            SubmissionState.PARTIALLY_FILLED,
            SubmissionState.FILLED,
            SubmissionState.REJECTED,
            SubmissionState.CANCELLED,
            SubmissionState.FAILED,
            SubmissionState.UNKNOWN,
        },
        SubmissionState.PARTIALLY_FILLED: {
            SubmissionState.FILLED,
            SubmissionState.REJECTED,
            SubmissionState.CANCELLED,
            SubmissionState.UNKNOWN,
            SubmissionState.FAILED,
        },
        SubmissionState.UNKNOWN: {
            SubmissionState.UNKNOWN,
            SubmissionState.SUBMITTED,
            SubmissionState.ACCEPTED,
            SubmissionState.REJECTED,
            SubmissionState.PARTIALLY_FILLED,
            SubmissionState.FILLED,
            SubmissionState.CANCELLED,
            SubmissionState.FAILED,
        },
        SubmissionState.FILLED: set(),
        SubmissionState.CANCELLED: set(),
        SubmissionState.REJECTED: set(),
        SubmissionState.FAILED: set(),
    }
    return target in table.get(current, set())


def _state_for_result(result: AdapterResult) -> SubmissionState:
    """Normalize an AdapterResult into a lifecycle state (fail closed)."""

    if result.status is BrokerResultStatus.FAILED:
        return SubmissionState.FAILED
    try:
        return _RESULT_TO_STATE[result.status]
    except KeyError:
        raise ValueError(
            f"Cannot map adapter result status {result.status.value!r} "
            f"to a submission state (fail closed)."
        ) from None


def _is_pre_submission(state: SubmissionState) -> bool:
    """Whether the state is known-not-yet-submitted (no wire contact yet)."""

    return state in (SubmissionState.CREATED, SubmissionState.SUBMISSION_REQUESTED)


def _result_detail(result: AdapterResult) -> tuple[tuple[str, str], ...]:
    """Deterministic detail pairs for a result event."""

    out: list[tuple[str, str]] = []
    if result.broker_status:
        out.append(("broker_status", result.broker_status))
    if result.error is not None:
        out.append(("error_code", result.error.code.value))
        out.append(("error_category", result.error.category.value))
    return tuple(sorted(out))


def _event_id(hint: str) -> str:
    """Deterministic event-id hint (not a full identity hash)."""

    return f"submission-event-{hint}"


def _event_hash_for(command_id: str, state: SubmissionState, created_at: datetime) -> str:
    """Deterministic event identity hash for provenance."""

    payload = f"{command_id}|{state.name}|{created_at.isoformat()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class SubmissionLifecycleEngine:
    """Stateless orchestrator for the submission / order lifecycle.

    The engine holds no mutable state, no cache, no registry. Callers supply
    the immutable command (already authorized), the broker-neutral adapter,
    timestamps, and the current lifecycle snapshot (loaded from a store).
    """

    # ---------------------------------------------------------
    # SUBMIT
    # ---------------------------------------------------------

    def request_submission(
        self,
        *,
        command: ExecutionCommand,
        adapter: BrokerAdapter,
        created_at: datetime,
        lifecycle: SubmissionLifecycle | None = None,
        broker_context: str = "default",
        label: str = "",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> SubmissionLifecycle:
        """Request submission of an already-authorized command.

        The engine NEVER authorizes: it only starts tracking the execution
        attempt for an already-authoritative command.
        """

        if not isinstance(command, ExecutionCommand):
            raise TypeError(
                f"command must be an ExecutionCommand; "
                f"got {type(command).__name__!r}."
            )
        validate_adapter_mode(
            adapter_execution_mode=adapter.execution_mode, command=command
        )

        client_order_id = derive_client_order_id(
            command_id=command.command_id, broker_context=broker_context
        )

        if lifecycle is None:
            return self._fresh(
                command=command,
                state=SubmissionState.SUBMISSION_REQUESTED,
                client_order_id=client_order_id,
                created_at=created_at,
                reason="Submission requested.",
                label=label,
                metadata=metadata,
            )

        if not isinstance(lifecycle, SubmissionLifecycle):
            raise TypeError(
                f"lifecycle must be a SubmissionLifecycle or None; "
                f"got {type(lifecycle).__name__!r}."
            )
        if lifecycle.command_id != command.command_id:
            raise ValueError(
                f"Lifecycle command_id {lifecycle.command_id!r} does not match "
                f"command {command.command_id!r}."
            )
        if lifecycle.client_order_id != client_order_id:
            raise ValueError(
                f"Lifecycle client_order_id {lifecycle.client_order_id!r} does "
                f"not match derived {client_order_id!r} (broker_context "
                f"mismatch?)."
            )

        if lifecycle.state.is_terminal:
            raise ValueError(
                f"Command {command.command_id!r} already has a terminal "
                f"lifecycle state {lifecycle.state.value}; refusing duplicate "
                f"submission."
            )
        if lifecycle.state is SubmissionState.UNKNOWN:
            raise ValueError(
                f"Command {command.command_id!r} is in UNKNOWN state from an "
                f"earlier submission; reconciliation is REQUIRED before any "
                f"retry. Blind retry is prohibited by the contract."
            )
        if lifecycle.state in (
            SubmissionState.SUBMISSION_REQUESTED,
            SubmissionState.SUBMITTED,
            SubmissionState.ACCEPTED,
            SubmissionState.PARTIALLY_FILLED,
        ):
            raise ValueError(
                f"Command {command.command_id!r} already has an in-flight "
                f"lifecycle ({lifecycle.state.value}); refusing duplicate "
                f"submission."
            )

        return self._advance_from(
            current=lifecycle,
            target=SubmissionState.SUBMISSION_REQUESTED,
            created_at=created_at,
            reason="Submission requested (retry after explicit recovery).",
        )

    def submit(
        self,
        *,
        command: ExecutionCommand,
        adapter: BrokerAdapter,
        created_at: datetime,
        lifecycle: SubmissionLifecycle | None = None,
        broker_context: str = "default",
        label: str = "",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> SubmissionLifecycle:
        """Start a submission attempt and record the adapter's result.

        Composes request_submission + record_result so a caller can move
        directly to the state the adapter confirmed in one step.
        """

        requested = self.request_submission(
            command=command,
            adapter=adapter,
            created_at=created_at,
            lifecycle=lifecycle,
            broker_context=broker_context,
            label=label,
            metadata=metadata,
        )
        try:
            result = adapter.submit(command)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Adapter rejected submission before dispatch: {exc}"
            ) from exc
        return self.record_result(
            lifecycle=requested,
            result=result,
            created_at=created_at,
            event_hint="submit",
        )

    # ---------------------------------------------------------
    # RECONCILE BEFORE RETRY
    # ---------------------------------------------------------

    def reconcile_submission(
        self,
        *,
        lifecycle: SubmissionLifecycle,
        adapter: BrokerAdapter,
        created_at: datetime,
    ) -> SubmissionLifecycle:
        """Reconcile an ambiguous submission state with the broker.

        Reconciliation queries the broker using the SAME deterministic
        client_order_id; it NEVER sends a new order. The lifecycle may remain
        UNKNOWN when reconciliation cannot determine the outcome -- blind
        retry therefore remains prohibited.
        """

        if not isinstance(lifecycle, SubmissionLifecycle):
            raise TypeError(
                f"lifecycle must be a SubmissionLifecycle; "
                f"got {type(lifecycle).__name__!r}."
            )
        _validate_adapter_mode_for_lifecycle(adapter, lifecycle)
        result = adapter.reconcile(lifecycle.client_order_id)
        return self.record_result(
            lifecycle=lifecycle,
            result=result,
            created_at=created_at,
            event_hint="reconcile",
        )

    # ---------------------------------------------------------
    # CANCEL
    # ---------------------------------------------------------

    def request_cancellation(
        self,
        *,
        lifecycle: SubmissionLifecycle,
        adapter: BrokerAdapter,
        created_at: datetime,
    ) -> SubmissionLifecycle:
        """Request cancellation of an in-flight submission (adapter CANCEL)."""

        if not isinstance(lifecycle, SubmissionLifecycle):
            raise TypeError(
                f"lifecycle must be a SubmissionLifecycle; "
                f"got {type(lifecycle).__name__!r}."
            )
        _validate_adapter_mode_for_lifecycle(adapter, lifecycle)
        result = adapter.cancel(lifecycle.client_order_id)
        return self.record_result(
            lifecycle=lifecycle,
            result=result,
            created_at=created_at,
            event_hint="cancel",
        )

    # ---------------------------------------------------------
    # RECORD RESULT
    # ---------------------------------------------------------

    def record_result(
        self,
        *,
        lifecycle: SubmissionLifecycle,
        result: AdapterResult,
        created_at: datetime,
        event_hint: str = "",
    ) -> SubmissionLifecycle:
        """Record an :class:`AdapterResult` and advance the lifecycle snapshot.

        This is the single authoritative result-ingestion point: it maps a
        broker-neutral :class:`AdapterResult` onto a lifecycle state and
        validates the transition. Terminal results are absorbing.
        """

        if not isinstance(lifecycle, SubmissionLifecycle):
            raise TypeError(
                f"lifecycle must be a SubmissionLifecycle; "
                f"got {type(lifecycle).__name__!r}."
            )
        if not isinstance(result, AdapterResult):
            raise TypeError(
                f"result must be an AdapterResult; "
                f"got {type(result).__name__!r}."
            )
        target = _state_for_result(result)
        if not _allowed_transition(lifecycle.state, target):
            raise ValueError(
                f"Illegal lifecycle transition: "
                f"{lifecycle.state.value} -> {target.value}. "
                f"See the documented transition table."
            )
        reason = result.reason or (result.broker_status or target.value.lower())
        event = SubmissionEvent(
            event_id=_event_id(event_hint or target.value.lower()),
            submission_id=lifecycle.submission_id,
            state=target,
            created_at=created_at,
            reason=reason,
            detail=_result_detail(result),
        )
        return create_submission_lifecycle(
            command_id=lifecycle.command_id,
            state=target,
            client_order_id=lifecycle.client_order_id,
            pre_submission=_is_pre_submission(target),
            created_at=created_at,
            events=lifecycle.events + (event,),
            broker_order_id=result.broker_order_id,
            reason=reason,
            label=lifecycle.label,
            metadata=lifecycle.metadata,
        )

    # ---------------------------------------------------------
    # RESTART RECOVERY
    # ---------------------------------------------------------

    def restart_recovery(self, lifecycle: SubmissionLifecycle) -> RecoveryAction:
        """Deterministic recovery decision for a persisted lifecycle snapshot."""

        if not isinstance(lifecycle, SubmissionLifecycle):
            return RecoveryAction.INVALID
        if lifecycle.state is SubmissionState.CREATED:
            return RecoveryAction.SAFE_TO_SUBMIT
        if (
            lifecycle.state is SubmissionState.SUBMISSION_REQUESTED
            and lifecycle.pre_submission
        ):
            return RecoveryAction.SAFE_TO_SUBMIT
        if (
            lifecycle.state is SubmissionState.SUBMISSION_REQUESTED
            and not lifecycle.pre_submission
        ):
            return RecoveryAction.RECONCILE_REQUIRED
        if lifecycle.state in (
            SubmissionState.SUBMITTED,
            SubmissionState.UNKNOWN,
        ):
            return RecoveryAction.RECONCILE_REQUIRED
        if lifecycle.state.is_terminal:
            return RecoveryAction.NO_ACTION
        return RecoveryAction.SAFE_TO_SUBMIT

    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------

    def resolve_adapter(
        self,
        command: ExecutionCommand,
        adapters: dict[str, BrokerAdapter],
        *,
        preferred: str | None = None,
    ) -> BrokerAdapter:
        """Select a mode-matched adapter (delegates to the shared factory)."""

        from engine.intelligence.broker_adapter_contract import select_adapter

        return select_adapter(adapters, command, preferred=preferred)

    def _fresh(
        self,
        *,
        command: ExecutionCommand,
        state: SubmissionState,
        client_order_id: str,
        created_at: datetime,
        reason: str,
        label: str,
        metadata: tuple[tuple[str, str], ...],
    ) -> SubmissionLifecycle:
        """Construct a fresh lifecycle with a single accurate event."""

        lifecycle = create_submission_lifecycle(
            command_id=command.command_id,
            state=state,
            client_order_id=client_order_id,
            pre_submission=_is_pre_submission(state),
            created_at=created_at,
            reason=reason,
            label=label,
            metadata=metadata,
        )
        event = SubmissionEvent(
            event_id=_event_hash_for(command.command_id, state, created_at),
            submission_id=lifecycle.submission_id,
            state=state,
            created_at=created_at,
            reason=reason,
        )
        return create_submission_lifecycle(
            command_id=command.command_id,
            state=state,
            client_order_id=client_order_id,
            pre_submission=_is_pre_submission(state),
            created_at=created_at,
            events=(event,),
            reason=reason,
            label=label,
            metadata=metadata,
        )

    def _advance_from(
        self,
        *,
        current: SubmissionLifecycle,
        target: SubmissionState,
        created_at: datetime,
        reason: str,
    ) -> SubmissionLifecycle:
        if not _allowed_transition(current.state, target):
            raise ValueError(
                f"Illegal lifecycle transition: "
                f"{current.state.value} -> {target.value}."
            )
        event = SubmissionEvent(
            event_id=_event_id(target.value.lower()),
            submission_id=current.submission_id,
            state=target,
            created_at=created_at,
            reason=reason,
        )
        return create_submission_lifecycle(
            command_id=current.command_id,
            state=target,
            client_order_id=current.client_order_id,
            pre_submission=_is_pre_submission(target),
            created_at=created_at,
            events=current.events + (event,),
            reason=reason,
            label=current.label,
            metadata=current.metadata,
        )


def _validate_adapter_mode_for_lifecycle(
    adapter: BrokerAdapter, lifecycle: SubmissionLifecycle
) -> None:
    """Verify the adapter's mode against the lifecycle's recorded command mode.

    The mode is recorded as deterministic metadata (``cp17_mode``) at
    lifecycle creation. It was fixed at command creation from the
    authorization and cannot silently change.
    """

    meta = dict(lifecycle.metadata)
    recorded = meta.get("cp17_mode")
    if recorded:
        from engine.models.execution_command import ExecutionMode

        expected = ExecutionMode.LIVE if recorded == "LIVE" else ExecutionMode.PAPER
        if adapter.execution_mode is not expected:
            raise ValueError(
                f"Execution-mode mismatch during reconciliation/cancellation: "
                f"adapter is {adapter.execution_mode.value} but lifecycle "
                f"records {recorded}."
            )


__all__ = [
    "RecoveryAction",
    "SubmissionLifecycleEngine",
]