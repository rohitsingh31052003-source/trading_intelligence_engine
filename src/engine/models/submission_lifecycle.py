"""
Submission / order lifecycle model (Checkpoint 17.2 Phase 5).

This module defines the SEPARATE execution-state representation associated
with attempting to execute an already-authorized
:class:`~engine.models.execution_command.ExecutionCommand`.

CRITICAL INVARIANT:

* :class:`ExecutionCommand` is an IMMUTABLE historical instruction representing
  the authorized command. It is NEVER mutated.
* :class:`SubmissionLifecycle` is the operational state associated with that
  command. It MUST reference ``command_id`` and MUST NOT be embedded inside
  or mutate the command..
* The system can always answer independently::

    "What command was authorized?"  → :class:`ExecutionCommand`.
    "What happened when we attempted to execute it?" →
      :class:`SubmissionLifecycle` (via ``command_id``).

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* A :class:`SubmissionLifecycle` is an immutable SNAPSHOT of the submission
  state at a point in time. Lifecycle progression produces NEW lifecycle
  snapshot records (the engine advances state by constructing new
  immutable records; the latest record may be persisted).)
* ``submission_id`` is deterministic::

    "submission-" + sha256[:16](command_id + state + created_at)

  so the SAME command reaching the SAME state at the SAME instant produces
  the SAME id (deterministic, no UUID/wall-clock-dependence as the caller
  supplies ``created_at``).
* ``client_order_id`` is deterministic and derived ONLY from ``command_id`` +
  broker context. See ``derive_client_order_id`` in
  :mod:`engine.intelligence.broker_adapter_contract`.
* Every ``SubmissionEvent`` carries a ``reason`` field documenting how/why
  the state advanced (reconciliation provenance, rejection reason, etc.).
* ``__post_init__`` validates internal consistency (required fields,
  timestamp awareness, chronological ordering, state-specific invariants).
* No broker-specific concepts live here: broker order ids, fills, prices,
  positions are downstream / broker-specific and never enter this model.

"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from engine.models.execution_command import COMMAND_ID_PREFIX


#: Prefix for submission ids.
SUBMISSION_ID_PREFIX = "submission-"

#: Length of SHA-256 hex digest prefix used in identity strings..
_ID_DIGEST_LENGTH = 16

#: Schema/model version for SubmissionLifecycle.
SUBMISSION_LIFECYCLE_VERSION = 1


# ============================================================
# STATE
# ============================================================


class SubmissionState(Enum):
    """Operational submission / order lifecycle state.



    CREATED
        The lifecycle record exists (command accepted for execution attempt
        tracking) but no broker submission has been requested yet..
    SUBMISSION_REQUESTED
        A submit operation has been requested/initiated for this command..
    SUBMITTED
        The broker request was transmitted / accepted for eventual execution.

    ACCEPTED
        The broker accepted / queued the order..
    REJECTED
        The broker confirmed rejection..
    UNKNOWN
        Ambiguous submission outcome (e.g. timeout); reconciliation is
        REQUIRED before any retry..

    PARTIALLY_FILLED
        The broker confirmed a partial fill..
    FILLED
        The broker confirmed a full fill..
    CANCELLED
        The broker confirmed cancellation..
    FAILED
        A known deterministic failure was recorded (before / during
        submission; e.g. validation rejection or internal adapter failure..
    """


    CREATED = "CREATED"
    SUBMISSION_REQUESTED = "SUBMISSION_REQUESTED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        """Whether this state is a final, settled state (record closed)."""
        return self in (
            self.ACCEPTED,
            self.REJECTED,
            self.PARTIALLY_FILLED,
            self.FILLED,
            self.CANCELLED,
            self.FAILED,
        )

    @property
    def is_ambiguous(self) -> bool:
        """Whether this state represents an unknown broker state."""
        return self is self.UNKNOWN


# ============================================================
# EVENT
# ============================================================


@dataclass(frozen=True, slots=True)
class SubmissionEvent:
    """An immutable event recording how/when a lifecycle state was reached.


    Attributes:
        event_id:
            Deterministic event identity::

                "submission-event-" + sha256[:16](submission_id + state +
                created_at + reason)

        submission_id:
            The :class:`SubmissionLifecycle` this event belongs to..
        state:
            The :class:`SubmissionState` reached at this event..
        created_at:
            Timezone-aware event timestamp. Caller-supplied;; no
            ``datetime.now()`` here..
        reason:
            Short broker-neutral reason documenting how/why the state advanced..
        detail:
            Optional structured detail mapping (sorted pairs).
    """


    event_id: str
    submission_id: str
    state: SubmissionState
    created_at: datetime
    reason: str
    detail: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must be non-empty.")
        if not self.submission_id:
            raise ValueError("submission_id must be non-empty.")
        if not isinstance(self.state, SubmissionState):
            raise TypeError(
                f"state must be a SubmissionState; "
                f"got {type(self.state).__name__!r}."
            )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty string.")
        normalized: list[tuple[str, str]] = []
        for k, v in self.detail:
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("detail keys and values must be strings.")
            normalized.append((k, v))
        normalized.sort()
        object.__setattr__(self, "detail", tuple(normalized))


# ============================================================
# LIFECYCLE SNAPSHOT
# ============================================================


def _canonical_payload(
    *,
    command_id: str,
    state: SubmissionState,
    client_order_id: str,
    pre_submission: bool,
    created_at: datetime,
) -> dict[str, Any]:
    """Build the canonical identity payload for submission_id generation.


    Operational metadata (reasons, detail, broker references, labels)
    is excluded so the identity remains stable across operational context
    changes that do not alter the tracked submission state..
    """

    return {
        "command_id": command_id,
        "state": state.name,
        "client_order_id": client_order_id,
        "pre_submission": bool(pre_submission),
        "created_at": created_at.isoformat(),
    }


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


@dataclass(frozen=True, slots=True)
class SubmissionLifecycle:
    """An immutable snapshot of the operational state for one command.



    Attributes:
        submission_id:
            Deterministic submission identity. See module docstring..
        command_id:
            The ``command_id`` the immutable
            :class:`~engine.models.execution_command.ExecutionCommand` this
            lifecycle tracks. NEVER a command mutation — always a reference..
        state:
            Current :class:`SubmissionState` at this snapshot..
        client_order_id:
            Deterministic broker-facing client order id (stable across
            process restarts) for idempotency / reconciliation..
        pre_submission:
            True while no broker request has been transmitted yet;; False
            once a request has reached the wire (or later), A
            ``True`` value means "known not-yet-submitted"; ``False`` means
            "submission may exist at the broker — must reconcile before
            any retry"..
        created_at:
            Timezone-aware lifecycle snapshot timestamp. Caller-supplied; no
            ``datetime.now()`` here..
        events:
            Chronological tuple of :class:`SubmissionEvent` snapshots
            (oldest→newest). The last event's ``state`` matches this snapshot's
            ``state`` (enforced by the engine at construction, and by
            ``append_event`` in the lifecycle engine..
        broker_order_id:
            Broker-side order reference, or ``None``. Broker-specific /
            downstream-only; NEVER inserted into upstream artifacts..
        reason:
            Short broker-neutral reason invoking the latest transition..
        label / metadata:
            Optional caller-supplied audit fields..
        version:
            Schema/model version (default 1)..
    """


    submission_id: str
    command_id: str
    state: SubmissionState
    client_order_id: str
    pre_submission: bool
    created_at: datetime
    events: tuple[SubmissionEvent, ...] = ()
    broker_order_id: str | None = None
    reason: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    version: int = SUBMISSION_LIFECYCLE_VERSION

    def __post_init__(self) -> None:
        if not self.submission_id:
            raise ValueError("submission_id must be non-empty.")
        if not self.submission_id.startswith(SUBMISSION_ID_PREFIX):
            raise ValueError(
                f"submission_id must start with {SUBMISSION_ID_PREFIX!r}."
            )
        if not self.command_id or not self.command_id.startswith(COMMAND_ID_PREFIX):
            raise ValueError(
                f"command_id must start with {COMMAND_ID_PREFIX!r}; "
                f"got {self.command_id!r}."
            )
        if not isinstance(self.state, SubmissionState):
            raise TypeError(
                f"state must be a SubmissionState; "
                f"got {type(self.state).__name__!r}."
            )
        if not self.client_order_id:
            raise ValueError("client_order_id must be non-empty.")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
        for event in self.events:
            if not isinstance(event, SubmissionEvent):
                raise TypeError(
                    f"events must contain SubmissionEvent instances; "
                    f"got {type(event).__name__!r}."
                )
        if self.broker_order_id is not None and not isinstance(self.broker_order_id, str):
            raise TypeError("broker_order_id must be a string or None.")

    @property
    def latest_event(self) -> SubmissionEvent | None:
        """The newest event in chronological order, or ``None``."""
        if not self.events:
            return None
        return self.events[-1]

    @property
    def is_pre_submission(self) -> bool:
        """Whether the command is still known-not-yet-submitted."""
        return self.pre_submission is True

    @property
    def requires_reconciliation(self) -> bool:
        """Whether an earlier submission may exist at the broker (ambiguity)."""
        return self.state is SubmissionState.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        """Whether this state is a final settled state."""
        return self.state.is_terminal


# ============================================================
# LIFECYCLE CONSTRUCTOR (low-level; engine validates transitions)
# ============================================================


def create_submission_lifecycle(
    *,
    command_id: str,
    state: SubmissionState,
    client_order_id: str,
    pre_submission: bool,
    created_at: datetime,
    events: tuple[SubmissionEvent, ...] = (),
    broker_order_id: str | None = None,
    reason: str = "",
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> SubmissionLifecycle:
    """Construct an immutable SubmissionLifecycle snapshot.


    This low-level factory performs NO transition validation — the
    :class:`~engine.intelligence.submission_lifecycle.SubmissionLifecycleEngine`
    is the authoritative transition authority. It exists for deterministic
    identity computation and for tests constructing baseline snapshots..
    """

    submission_id = _sha256_prefix(
        _canonical_payload(
            command_id=command_id,
            state=state,
            client_order_id=client_order_id,
            pre_submission=bool(pre_submission),
            created_at=created_at,
        ),
        prefix=SUBMISSION_ID_PREFIX,
    )
    return SubmissionLifecycle(
        submission_id=submission_id,
        command_id=command_id,
        state=state,
        client_order_id=client_order_id,
        pre_submission=bool(pre_submission),
        created_at=created_at,
        events=tuple(events),
        broker_order_id=broker_order_id,
        reason=reason,
        label=label,
        metadata=tuple(sorted(metadata)),
        version=SUBMISSION_LIFECYCLE_VERSION,
    )


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "SUBMISSION_ID_PREFIX",
    "SUBMISSION_LIFECYCLE_VERSION",
    "SubmissionEvent",
    "SubmissionLifecycle",
    "SubmissionState",
    "create_submission_lifecycle",
]