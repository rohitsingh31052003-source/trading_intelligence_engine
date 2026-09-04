"""Broker adapter infrastructure service (Checkpoint 17.3).

This module provides the thin orchestration infrastructure that binds the
already-defined broker-neutral contract into one safe end-to-end flow:

    Persisted ExecutionCommand
        -> Submission Lifecycle Creation
        -> Submission Persistence
        -> Adapter Selection
        -> BrokerAdapter.submit()
        -> FakeBroker (test-only)
        -> AdapterResult
        -> Submission Lifecycle transition
        -> Persistence

The service composes ONLY already-authored components:

* :class:`~engine.models.execution_command.ExecutionCommand` -- the
  immutable authorized instruction (frozen, Checkpoint 16.2).
* :class:`~engine.intelligence.submission_lifecycle.SubmissionLifecycleEngine`
  -- the authoritative transition / reconcile / recovery authority (17.2).
* :class:`~engine.persistence.submission_store.SubmissionLifecycleStore`
  -- atomic, broker-neutral lifecycle persistence (17.2).
* :class:`~engine.intelligence.broker_adapter_contract.BrokerAdapter`
  -- the broker-neutral adapter contract (17.2).
* :class:`~engine.intelligence.fake_broker.FakeBroker` -- deterministic,
  network-free, credential-free, test-only double (17.2).

CRITICAL INVARIANTS (see also the Checkpoint 17.3 audit document):

* ExecutionCommand is NEVER mutated by this infrastructure.
* Authorization stays strictly upstream -- the service has NO authorization
  authority and calls NO authorization code.
* The submission lifecycle is kept SEPARATE from the command and references
  it only via ``command_id``.
* Timeout / ambiguous broker outcomes become ``UNKNOWN`` and are NEVER
  converted into a definitive failure or success (engine authority).
* ``UNKNOWN`` requires reconciliation; blind retry is prohibited (the engine's
  ``request_submission`` refuses UNKNOWN; the service surfaces
  :class:`ReconciliationRequiredError`).
* Unknown / ambiguous state survives restart (persisted via the store).
* Deterministic identity survives restart: ``command_id`` -> deterministic
  ``client_order_id`` -> deterministic ``idempotency_key`` (documented).
* Duplicate command processing is detectable: the store's
  ``load_by_command`` duplicate-submission guard plus the service's pre-submit
  check surface a typed :class:`DuplicateSubmissionError`.
* Paper/live mode can never silently cross: adapter selection uses
  ``select_adapter`` (fail-closed on mismatch/missing), and the execution
  mode is recorded on the lifecycle metadata (``cp17_mode``) so later
  reconcile/cancel verify the mode binding (engine
  ``_validate_adapter_mode_for_lifecycle``).
* Broker-specific errors do NOT leak into the core contract: only the
  :class:`~engine.models.broker_adapter.AdapterResult` / broker-neutral
  error taxonomy crosses the boundary.
* The persisted lifecycle state is broker-neutral: no broker SDK objects,
  no broker-specific response models, no credentials, no URLs (see the
  ``audit`` surface below).
* No external network communication occurs in this module and in all
  Checkpoint 17.3 code.
* No real broker integration exists.

APPLICATION-LEVEL vs BROKER-LEVEL IDEMPOTENCY:

* The deterministic ``client_order_id`` (and the derived ``idempotency_key``)
  provide APPLICATION-LEVEL idempotency identity -- stable across restart,
  used to suppress duplicate in-process and post-restart submissions.
* They do NOT by themselves guarantee BROKER-LEVEL idempotency -- the
  broker-facing adapter owns the actual broker-side idempotency mechanism
  (broker-specific keys, dedupe semantics, etc.) and must use broker-side
  mechanisms where available (for a future broker integration).
* This distinction is documented on the audit surface below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.intelligence.broker_adapter_contract import (
    BrokerAdapter,
    derive_client_order_id,
    derive_idempotency_key,
)
from engine.intelligence.submission_lifecycle import (
    RecoveryAction,
    SubmissionLifecycleEngine,
)
from engine.models.execution_command import ExecutionCommand
from engine.models.submission_lifecycle import (
    SubmissionLifecycle,
    SubmissionState,
)
from engine.persistence.exceptions import (
    SubmissionIntegrityError,
    SubmissionNotFoundError,
)
from engine.persistence.submission_store import SubmissionLifecycleStore

#: Metadata key recording the command's execution mode on the lifecycle.
_MODE_METADATA_KEY = "cp17_mode"


class SubmissionInfrastructureError(Exception):
    """Base error for the broker adapter infrastructure service."""


class DuplicateSubmissionError(SubmissionInfrastructureError):
    """The command already has an in-flight / active lifecycle; no resubmit."""


class ReconciliationRequiredError(SubmissionInfrastructureError):
    """The command is in UNKNOWN state; reconciliation must precede retry."""


class CommandNotSubmittedError(SubmissionInfrastructureError):
    """No lifecycle exists yet for the command; nothing has been submitted."""


@dataclass(frozen=True, slots=True)
class SubmissionAuditRow:
    """Read-only broker-neutral audit row for one persisted submission."""

    command_id: str
    submission_id: str
    state: SubmissionState
    client_order_id: str
    idempotency_key: str
    requires_reconciliation: bool
    reconciliation_performed: bool
    retry_allowed: bool
    terminal: bool
    pre_submission: bool
    created_at: datetime
    event_count: int
    last_reason: str

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection for audits / tests."""
        return {
            "command_id": self.command_id,
            "submission_id": self.submission_id,
            "state": self.state.value,
            "client_order_id": self.client_order_id,
            "idempotency_key": self.idempotency_key,
            "requires_reconciliation": self.requires_reconciliation,
            "reconciliation_performed": bool(self.reconciliation_performed),
            "retry_allowed": bool(self.retry_allowed),
            "terminal": bool(self.terminal),
            "pre_submission": bool(self.pre_submission),
            "created_at": self.created_at.isoformat(),
            "event_count": int(self.event_count),
            "last_reason": self.last_reason,
        }


@dataclass(frozen=True, slots=True)
class SubmissionInfrastructureAudit:
    """Deterministic broker-neutral audit summary of persisted submissions.

    Answers the Checkpoint 17.3 auditability questions (Phase 12) from the
    persisted store alone -- no engine calls, no broker calls.

    Attributes:
        rows: Chronologically sorted audit rows, one per distinct command
            (the NEWEST persisted record wins for each command).
        duplicate_commands: Commands with MORE than one persisted record (a
            crash artifact from interrupted snapshot replacement; requires
            manual review; the store's ``load_by_command`` guard surfaces it
            fail-closed).
        store_directory: The store directory audited.
        documentation: Descriptive broker-neutral auditability notes.
    """

    rows: tuple[SubmissionAuditRow, ...]
    duplicate_commands: tuple[str, ...]
    store_directory: str
    documentation: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "duplicate_commands": list(self.duplicate_commands),
            "store_directory": self.store_directory,
            "documentation": list(self.documentation),
        }


class SubmissionInfrastructure:
    """Stateless orchestration service for the broker adapter infrastructure.

    The service composes the 17.3/17.2 components; it holds no mutable
    state, no cache, no registry, and never mutates commands, lifecycles or
    adapters. All timestamps are caller-supplied (no ``datetime.now()``).

    Persistence contract:

    * Exactly ONE persisted record per command (the CURRENT lifecycle
      snapshot) is maintained, matching the frozen store's single-active
      record guard.
    * Advancing a snapshot is persisted FIRST, then the prior snapshot(s)
      for the same command are deleted (never lose the newest state; a crash
      between the two surfaces a ``SubmissionIntegrityError`` from
      ``load_by_command`` -- fail-closed, ambiguous storage, documented).
    * Re-persisting the SAME snapshot is idempotent (the store's identical
      content save silently succeeds).
    """

    def __init__(
        self,
        *,
        engine: SubmissionLifecycleEngine | None = None,
        default_broker_context: str = "default",
    ) -> None:
        self._engine = engine if engine is not None else SubmissionLifecycleEngine()
        self._default_broker_context = default_broker_context

    @property
    def engine(self) -> SubmissionLifecycleEngine:
        return self._engine

    # ------------------------------------------------------------------
    # CREATE (dormant lifecycle for crash-before-submit scenarios)
    # ------------------------------------------------------------------

    def create_lifecycle(
        self,
        *,
        command: ExecutionCommand,
        submission_store: SubmissionLifecycleStore,
        created_at: datetime,
        broker_context: str | None = None,
        label: str = "",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> SubmissionLifecycle:
        """Create and persist a CREATED (dormant) lifecycle for a command.

        This supports the crash-before-submit scenario (persistence CASE 1):
        the command is authorized and persisted, a submission lifecycle
        record is created, but no broker request is transmitted yet. A
        process crash here leaves a CREATED record that recovery classifies
        as ``SAFE_TO_SUBMIT`` (the broker was never contacted).

        Policy (deterministic, fail-closed):

        * No lifecycle yet -> a CREATED snapshot is persisted.
        * CREATED lifecycle already exists -> the existing snapshot is
          returned UNCHANGED (idempotent creation).
        * UNKNOWN / in-flight / terminal lifecycle -> raises
          :class:`DuplicateSubmissionError` / :class:`ReconciliationRequiredError`
          per the same policy as submit (no second lifecycle is ever created).

        Args:
            command: The already-authorized immutable command.
            submission_store: The lifecycle persistence store.
            created_at: Timezone-aware creation timestamp. Caller-supplied.
            broker_context: Deterministic client-order-identity context.
            label / metadata: Caller-supplied audit fields. The service
                force-adds the ``cp17_mode`` execution-mode binding.
        """

        if not isinstance(command, ExecutionCommand):
            raise TypeError(
                f"command must be an ExecutionCommand; "
                f"got {type(command).__name__!r}."
            )
        if not isinstance(submission_store, SubmissionLifecycleStore):
            raise TypeError(
                f"submission_store must be a SubmissionLifecycleStore; "
                f"got {type(submission_store).__name__!r}."
            )
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")

        effective_context = broker_context or self._default_broker_context
        effective_metadata = tuple(
            sorted(list(metadata) + [(_MODE_METADATA_KEY, command.execution_mode.value)])
        )

        existing: SubmissionLifecycle | None = None
        try:
            existing = submission_store.load_by_command(command.command_id)
        except SubmissionNotFoundError:
            existing = None
        except SubmissionIntegrityError as exc:
            raise SubmissionInfrastructureError(
                f"Persisted lifecycle state for command {command.command_id!r} "
                f"is ambiguous; manual review required; no blind retry. ({exc})"
            ) from exc

        if existing is not None:
            if existing.state.is_terminal:
                return existing
            if existing.state is SubmissionState.UNKNOWN:
                raise ReconciliationRequiredError(
                    f"Command {command.command_id!r} is in UNKNOWN state; "
                    f"reconciliation is required before any retry."
                )
            if existing.state is not SubmissionState.CREATED:
                raise DuplicateSubmissionError(
                    f"Command {command.command_id!r} already has an in-flight "
                    f"lifecycle ({existing.state.value}); a second lifecycle "
                    f"must never be created."
                )
            return existing

        from engine.models.submission_lifecycle import (
            SubmissionEvent,
            create_submission_lifecycle,
        )

        client_order_id = derive_client_order_id(
            command_id=command.command_id, broker_context=effective_context
        )
        lifecycle = create_submission_lifecycle(
            command_id=command.command_id,
            state=SubmissionState.CREATED,
            client_order_id=client_order_id,
            pre_submission=True,
            created_at=created_at,
            events=(),
            reason="Submission lifecycle created (no broker request transmitted).",
            label=label,
            metadata=effective_metadata,
        )
        event = SubmissionEvent(
            event_id="submission-event-created",
            submission_id=lifecycle.submission_id,
            state=SubmissionState.CREATED,
            created_at=created_at,
            reason="Submission lifecycle created (no broker request transmitted).",
        )
        lifecycle = create_submission_lifecycle(
            command_id=lifecycle.command_id,
            state=lifecycle.state,
            client_order_id=lifecycle.client_order_id,
            pre_submission=lifecycle.pre_submission,
            created_at=lifecycle.created_at,
            events=(event,),
            reason=lifecycle.reason,
            label=lifecycle.label,
            metadata=lifecycle.metadata,
        )
        self._persist_replacing(submission_store, lifecycle, previous=None)
        return lifecycle

    # ------------------------------------------------------------------
    # SUBMIT
    # ------------------------------------------------------------------

    def submit_command(
        self,
        *,
        command: ExecutionCommand,
        adapters: dict[str, BrokerAdapter],
        submission_store: SubmissionLifecycleStore,
        created_at: datetime,
        broker_context: str | None = None,
        label: str = "",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> SubmissionLifecycle:
        """Submit an already-authorized command end-to-end and persist it.

        Flow::

            load existing lifecycle (if any) -> duplicate/ambiguity guard ->
            select adapter (fail-closed mode match) -> engine.submit ->
            persist new snapshot -> replace (delete) prior snapshot(s).

        Policy (deterministic, fail-closed):

        * No lifecycle yet -> fresh submission (persisted).
        * CREATED lifecycle -> advanced to SUBMISSION_REQUESTED -> adapter ->
          new snapshot persisted (replaces the dormant snapshot).
        * SUBMISSION_REQUESTED / SUBMITTED / ACCEPTED / PARTIALLY_FILLED ->
          :class:`DuplicateSubmissionError` (in-flight -- no blind retry).
        * UNKNOWN -> :class:`ReconciliationRequiredError` (reconcile first;
          blind retry is prohibited by the contract).
        * Terminal states -> the existing lifecycle is returned UNCHANGED
          (terminal submission states do NOT resubmit).

        Args:
            command: The already-authorized immutable command to submit.
            adapters: Adapter registry (name -> broker-neutral adapter).
                Selection via ``select_adapter`` (mode-matched, fail-closed);
                an empty registry fails closed.
            submission_store: The lifecycle persistence store.
            created_at: Timezone-aware submission timestamp. Caller-supplied.
            broker_context: Deterministic client-order-identity context;
                defaults to the service default.
            label / metadata: Caller-supplied audit fields (sorted pairs).
                The service force-adds the ``cp17_mode`` execution-mode
                binding to the metadata so later reconcile/cancel verify the
                mode binding.

        Returns:
            The NEW persisted lifecycle snapshot (terminal states return the
            existing snapshot unchanged).

        Raises:
            TypeError: Non-ExecutionCommand or non-store inputs.
            DuplicateSubmissionError: In-flight lifecycle already exists.
            ReconciliationRequiredError: UNKNOWN lifecycle requires
                reconciliation before retry.
            SubmissionInfrastructureError: Adapter/engine failure or
                ambiguous persisted state (never a false success).
        """

        if not isinstance(command, ExecutionCommand):
            raise TypeError(
                f"command must be an ExecutionCommand; "
                f"got {type(command).__name__!r}."
            )
        if not isinstance(submission_store, SubmissionLifecycleStore):
            raise TypeError(
                f"submission_store must be a SubmissionLifecycleStore; "
                f"got {type(submission_store).__name__!r}."
            )
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")

        effective_context = broker_context or self._default_broker_context
        effective_metadata = tuple(
            sorted(list(metadata) + [(_MODE_METADATA_KEY, command.execution_mode.value)])
        )

        existing: SubmissionLifecycle | None = None
        try:
            existing = submission_store.load_by_command(command.command_id)
        except SubmissionNotFoundError:
            existing = None
        except SubmissionIntegrityError as exc:
            raise SubmissionInfrastructureError(
                "Persisted lifecycle state for the command is ambiguous "
                f"(multiple records claim {command.command_id!r}; stale "
                f"snapshot replacement left artifacts). Manual review required "
                f"before any further submission; no blind retry. ({exc})"
            ) from exc

        if existing is not None:
            if existing.state.is_terminal:
                return existing
            if existing.state is SubmissionState.UNKNOWN:
                raise ReconciliationRequiredError(
                    f"Command {command.command_id!r} is in UNKNOWN state; "
                    f"reconciliation is required before any retry. Blind retry "
                    f"is prohibited by the frozen contract."
                )
            if existing.state in (
                SubmissionState.SUBMISSION_REQUESTED,
                SubmissionState.SUBMITTED,
                SubmissionState.ACCEPTED,
                SubmissionState.PARTIALLY_FILLED,
            ):
                raise DuplicateSubmissionError(
                    f"Command {command.command_id!r} already has an in-flight "
                    f"lifecycle ({existing.state.value}; submission "
                    f"{existing.submission_id!r}). Exactly one active submission "
                    f"per command is allowed."
                )
            # CREATED -> allowed (advance to SUBMISSION_REQUESTED then submit).

        try:
            adapter = self._engine.resolve_adapter(command, adapters)
        except (ValueError, TypeError) as exc:
            raise SubmissionInfrastructureError(
                f"Adapter selection failed (fail-closed; no silent mode cross "
                f"or fallback): {exc}"
            ) from exc
        try:
            lifecycle = self._engine.submit(
                command=command,
                adapter=adapter,
                created_at=created_at,
                lifecycle=existing,
                broker_context=effective_context,
                label=label,
                metadata=effective_metadata,
            )
        except (ValueError, TypeError) as exc:
            raise SubmissionInfrastructureError(
                f"Submission failed (no false success): {exc}"
            ) from exc

        self._persist_replacing(submission_store, lifecycle, previous=existing)
        return lifecycle

    # ------------------------------------------------------------------
    # RECONCILE
    # ------------------------------------------------------------------

    def reconcile_command(
        self,
        *,
        command_id: str,
        adapters: dict[str, BrokerAdapter],
        submission_store: SubmissionLifecycleStore,
        created_at: datetime,
    ) -> SubmissionLifecycle:
        """Reconcile an ambiguous / in-flight submission with the adapter.

        Flow::

            load lifecycle (by id or command_id) -> mode-match adapter ->
            engine.reconcile_submission -> persist new snapshot -> replace
            prior.

        Policy (deterministic):

        * No lifecycle -> :class:`CommandNotSubmittedError`.
        * Terminal lifecycle -> returned UNCHANGED (NO_ACTION -- nothing to
          reconcile; terminal states do not resubmit/re-attempt).
        * Non-terminal (UNKNOWN / SUBMITTED / ACCEPTED / PARTIALLY_FILLED)
          -> queried with the SAME deterministic ``client_order_id`` via
          ``adapter.reconcile``; the lifecycle advances ONLY on a confirmed
          outcome. A still-unknown reconciliation response leaves the
          lifecycle ``UNKNOWN`` and a retry remains prohibited.

        Args:
            command_id: The immutable ``command_id`` the lifecycle tracks (a
                ``submission_id`` is ALSO accepted and resolved via the store).
            adapters: Adapter registry (mode-matched against the lifecycle's
                recorded ``cp17_mode`` binding -- no paper/live cross).
            submission_store: The lifecycle persistence store.
            created_at: Timezone-aware reconciliation timestamp. Caller-supplied.

        Returns:
            The NEW persisted lifecycle snapshot (terminal returned unchanged).

        Raises:
            CommandNotSubmittedError: No lifecycle is stored for the command.
            SubmissionInfrastructureError: Ambiguous persisted state or engine
                failure (never a false success).
        """

        if not isinstance(submission_store, SubmissionLifecycleStore):
            raise TypeError(
                f"submission_store must be a SubmissionLifecycleStore; "
                f"got {type(submission_store).__name__!r}."
            )
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")

        lifecycle = self._resolve_lifecycle(submission_store, command_id)
        if lifecycle.state.is_terminal:
            return lifecycle

        adapter = self._resolve_adapter_for_lifecycle(lifecycle, adapters)
        try:
            reconciled = self._engine.reconcile_submission(
                lifecycle=lifecycle,
                adapter=adapter,
                created_at=created_at,
            )
        except (ValueError, TypeError) as exc:
            raise SubmissionInfrastructureError(
                f"Reconciliation failed (no false result): {exc}"
            ) from exc

        self._persist_replacing(submission_store, reconciled, previous=lifecycle)
        return reconciled

    # ------------------------------------------------------------------
    # RECOVERY
    # ------------------------------------------------------------------

    def recovery_for_command(
        self,
        *,
        command_id: str,
        submission_store: SubmissionLifecycleStore,
    ) -> dict[str, Any]:
        """Deterministic restart-recovery decision view for a persisted command.

        Returns a descriptive mapping::

            {
                "exists": bool,
                "state": state name or "",
                "recovery_action": RecoveryAction value or "",
                "reconciliation_required": bool,
                "terminal": bool,
                "pre_submission": bool,
                "duplicates": list[str] or [],
                "reason": str,
            }

        Semantics (mirrors the frozen engine's ``restart_recovery``):

        * No persisted lifecycle -> ``exists False``, ``recovery_action ""``
          (fresh command -- a fresh submission is allowed; no prior contact).
        * CREATED / SUBMISSION_REQUESTED (pre_submission=True) ->
          ``SAFE_TO_SUBMIT`` (the broker has never been contacted).
        * SUBMISSION_REQUESTED (pre_submission=False) / SUBMITTED / UNKNOWN ->
          ``RECONCILE_REQUIRED`` (the broker MAY have received the request).
        * Terminal states -> ``NO_ACTION``.

        The view NEVER auto-submits, never auto-reconciles, and never converts
        an unknown outcome into a success/failure (fail-closed).
        """

        if not isinstance(submission_store, SubmissionLifecycleStore):
            raise TypeError(
                f"submission_store must be a SubmissionLifecycleStore; "
                f"got {type(submission_store).__name__!r}."
            )
        try:
            lifecycle = submission_store.load_by_command(command_id)
        except SubmissionNotFoundError:
            return {
                "exists": False,
                "state": "",
                "recovery_action": "",
                "reconciliation_required": False,
                "terminal": False,
                "pre_submission": False,
                "duplicates": [],
                "reason": (
                    "No persisted lifecycle exists for the command; no prior "
                    "broker contact; a fresh submission is allowed."
                ),
            }
        except SubmissionIntegrityError as exc:
            return {
                "exists": True,
                "state": "",
                "recovery_action": "",
                "reconciliation_required": True,
                "terminal": False,
                "pre_submission": False,
                "duplicates": list(submission_store.list_submissions()),
                "reason": (
                    f"Ambiguous persisted lifecycle state ({exc}); manual "
                    f"review required; no blind retry."
                ),
            }
        action = self._engine.restart_recovery(lifecycle)
        return {
            "exists": True,
            "state": lifecycle.state.value,
            "recovery_action": action.value,
            "reconciliation_required": bool(lifecycle.requires_reconciliation),
            "terminal": bool(lifecycle.is_terminal),
            "pre_submission": bool(lifecycle.pre_submission),
            "duplicates": [],
            "reason": _recovery_reason(lifecycle, action),
        }

    # ------------------------------------------------------------------
    # AUDIT
    # ------------------------------------------------------------------

    def audit(
        self, *, submission_store: SubmissionLifecycleStore
    ) -> SubmissionInfrastructureAudit:
        """Build a deterministic broker-neutral audit summary from the store.

        The audit answers the Checkpoint 17.3 Phase-12 questions directly:

        * which ExecutionCommand was authorized / what command_id -- every
          row carries ``command_id`` (from the persisted record bindings).
        * what submission attempt was associated with it -- ``submission_id``.
        * what client_order_id / idempotency identity was used --
          ``client_order_id`` / ``idempotency_key`` (derived deterministically).
        * what lifecycle state exists now -- ``state``.
        * was reconciliation required -- ``requires_reconciliation``.
        * was reconciliation performed -- ``reconciliation_performed`` (the
          event history contains a reconcile event).
        * what was the normalized outcome -- ``state`` + ``last_reason``.
        * was a retry allowed -- ``retry_allowed`` (SAFE_TO_SUBMIT-like
          states; False for UNKNOWN / in-flight / terminal).
        * did the system ever attempt an unsafe duplicate submission --
          ``duplicate_commands`` (crash artifacts) plus the store's
          ``load_by_command`` guard surface them fail-closed; no code path
          creates a second active lifecycle for the same command.

        Returns:
            :class:`SubmissionInfrastructureAudit` with one row per distinct
            command (the NEWEST persisted record wins; crash artifacts are
            reported separately as ``duplicate_commands``).
        """

        if not isinstance(submission_store, SubmissionLifecycleStore):
            raise TypeError(
                f"submission_store must be a SubmissionLifecycleStore; "
                f"got {type(submission_store).__name__!r}."
            )
        by_command: dict[str, list[tuple[datetime, str]]] = {}
        loaded: dict[str, SubmissionLifecycle] = {}
        for sid in submission_store.list_submissions():
            try:
                lifecycle = submission_store.load(sid)
            except Exception:
                continue
            loaded[sid] = lifecycle
            by_command.setdefault(lifecycle.command_id, []).append(
                (lifecycle.created_at, sid)
            )
        rows: list[SubmissionAuditRow] = []
        duplicates: list[str] = []
        for command_id in sorted(by_command):
            entries = sorted(by_command[command_id])
            if len(entries) > 1:
                duplicates.append(command_id)
            created_at, sid = entries[-1]
            lifecycle = loaded[sid]
            client_order_id = derive_client_order_id(command_id=command_id)
            idempotency_key = derive_idempotency_key(command_id=command_id)
            action = self._engine.restart_recovery(lifecycle)
            has_reconcile_event = any(
                "reconcile" in (e.reason or "").lower()
                or "reconcile" in e.event_id.lower()
                for e in lifecycle.events
            )
            rows.append(
                SubmissionAuditRow(
                    command_id=command_id,
                    submission_id=lifecycle.submission_id,
                    state=lifecycle.state,
                    client_order_id=client_order_id,
                    idempotency_key=idempotency_key,
                    requires_reconciliation=bool(
                        lifecycle.requires_reconciliation
                    ),
                    reconciliation_performed=bool(has_reconcile_event),
                    retry_allowed=bool(
                        action is RecoveryAction.SAFE_TO_SUBMIT
                        and not lifecycle.is_terminal
                    ),
                    terminal=bool(lifecycle.is_terminal),
                    pre_submission=bool(lifecycle.pre_submission),
                    created_at=lifecycle.created_at,
                    event_count=len(lifecycle.events),
                    last_reason=lifecycle.reason
                    or (
                        lifecycle.latest_event.reason
                        if lifecycle.latest_event
                        else ""
                    ),
                )
            )
        rows.sort(key=lambda r: (r.created_at, r.submission_id))
        documentation = (
            "Persistence is broker-neutral: no broker SDK objects, credentials, "
            "order models, URLs or products are stored; only the broker-neutral "
            "lifecycle snapshot + deterministic identity are persisted.",
            "Application-level idempotency: the deterministic client_order_id / "
            "idempotency_key derived from command_id provide restart-stable "
            "duplicate identity. They do NOT by themselves guarantee broker-side "
            "idempotency -- a future broker adapter owns the actual broker-side "
            "mechanism and must use broker idempotency where available.",
            "Terminal submission states never resubmit; UNKNOWN never retries "
            "blindly (reconciliation required); in-flight lifecycles never "
            "duplicate.",
            "A crash between snapshot-persist-and-prior-delete can leave two "
            "records for one command -- surfaced via duplicate_commands + the "
            "store's load_by_command guard (fail-closed; no blind retry; manual "
            "review).",
        )
        return SubmissionInfrastructureAudit(
            rows=tuple(rows),
            duplicate_commands=tuple(duplicates),
            store_directory=str(submission_store.directory),
            documentation=documentation,
        )

    # ------------------------------------------------------------------
    # INTERNAL
    # ------------------------------------------------------------------

    def _resolve_lifecycle(
        self, store: SubmissionLifecycleStore, subject: str
    ) -> SubmissionLifecycle:
        """Resolve a lifecycle by ``command_id`` or ``submission_id``."""

        if not subject or not isinstance(subject, str):
            raise ValueError("command_id/submission_id must be a non-empty string.")
        try:
            return store.load_by_command(subject)
        except SubmissionNotFoundError:
            pass
        except SubmissionIntegrityError as exc:
            raise SubmissionInfrastructureError(
                f"Ambiguous persisted lifecycle state (multiple records claim "
                f"{subject!r}); manual review required. ({exc})"
            ) from exc
        try:
            return store.load(subject)
        except SubmissionNotFoundError:
            raise CommandNotSubmittedError(
                f"No submission lifecycle is stored for {subject!r}."
            ) from None
        return store.load(subject)

    def _resolve_adapter_for_lifecycle(
        self,
        lifecycle: SubmissionLifecycle,
        adapters: dict[str, BrokerAdapter],
    ) -> BrokerAdapter:
        """Select the adapter bound to the lifecycle's recorded execution mode."""

        meta = dict(lifecycle.metadata)
        recorded_mode = meta.get(_MODE_METADATA_KEY, "")
        if not recorded_mode:
            raise SubmissionInfrastructureError(
                "Lifecycle has no recorded cp17_mode execution-mode binding; "
                "mode cannot be verified for reconciliation (fail-closed). Use "
                "the infrastructure submit path (which records the binding) for "
                "all lifecycles tracked by this service."
            )
        from engine.models.execution_command import ExecutionMode

        expected = (
            ExecutionMode.LIVE if recorded_mode == "LIVE" else ExecutionMode.PAPER
        )
        matches: list[BrokerAdapter] = [
            adapter
            for adapter in adapters.values()
            if isinstance(adapter, BrokerAdapter)
            and adapter.execution_mode is expected
        ]
        if not matches:
            raise SubmissionInfrastructureError(
                f"No adapter matches recorded execution mode {expected.value}; "
                f"registered modes are "
                f"{sorted({a.execution_mode.value for a in adapters.values()})!r}. "
                f"Paper/live modes must never silently cross."
            )
        matches.sort(key=lambda a: a.name)
        return matches[0]

    def _persist_replacing(
        self,
        store: SubmissionLifecycleStore,
        new: SubmissionLifecycle,
        *,
        previous: SubmissionLifecycle | None,
    ) -> None:
        """Persist the NEW snapshot first, then remove prior snapshot(s).

        The store enforces exactly-one-active-record-per-command; because the
        snapshot ``submission_id`` changes on transition, the infra must
        replace rather than merely append. Order matters: write the new
        snapshot first so the current state is never lost; a crash after
        write-before-delete leaves two records -> the store's
        ``load_by_command`` guard raises ``SubmissionIntegrityError``
        (fail-closed, no blind retry; the audit's ``duplicate_commands``
        names them).
        """

        store.save(new, overwrite=False)
        if previous is not None and previous.submission_id != new.submission_id:
            try:
                store.delete(previous.submission_id)
            except SubmissionNotFoundError:
                pass
        # Remove any other stale records for the command (crash artifacts from
        # earlier interrupted replacements) -- only if their id differs.
        try:
            existing = store.load_by_command(new.command_id)
        except Exception:
            return  # ambiguous; leave for manual review (audit flags it).
        if existing.submission_id != new.submission_id:
            try:
                store.delete(existing.submission_id)
            except (SubmissionNotFoundError, SubmissionIntegrityError):
                pass


# ============================================================
# MODULE-LEVEL HELPERS
# ============================================================


def _recovery_reason(lifecycle: SubmissionLifecycle, action: RecoveryAction) -> str:
    """Deterministic human-readable recovery reason for a lifecycle."""

    if action is RecoveryAction.SAFE_TO_SUBMIT:
        return (
            "No ambiguous broker contact; a fresh submission with the same "
            "deterministic idempotency identity is safe."
        )
    if action is RecoveryAction.RECONCILE_REQUIRED:
        return (
            f"Lifecycle is {lifecycle.state.value} with pre_submission="
            f"{lifecycle.pre_submission}; the broker may have received the "
            f"request -- reconciliation is required before any retry."
        )
    if action is RecoveryAction.NO_ACTION:
        return (
            f"Lifecycle is terminal ({lifecycle.state.value}); transaction "
            f"complete -- no further action; terminal states do not resubmit."
        )
    return f"Unknown recovery action {action.value!r}; fail-closed."


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "CommandNotSubmittedError",
    "DuplicateSubmissionError",
    "ReconciliationRequiredError",
    "SubmissionAuditRow",
    "SubmissionInfrastructure",
    "SubmissionInfrastructureAudit",
    "SubmissionInfrastructureError",
]
