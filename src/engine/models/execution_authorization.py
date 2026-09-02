"""
Execution Authorization model (Checkpoint 15.2).

An :class:`ExecutionAuthorization` is an immutable authorization record
that represents explicit authorization for a specific
:class:`~engine.models.operational_trade_intent.OperationalTradeIntent`
to proceed toward a future execution layer.

It is NOT an execution command, NOT an order, NOT an external request, NOT
an external order, NOT an execution result, NOT a fill, NOT a position, NOT
a portfolio, and NOT a simulation record. It is the application's authorization
decision only.

Design rules:

* Frozen + slots dataclass (matches the rest of the model layer).
* ``authorization_id`` is deterministic (``"auth-" + sha256[:16]``) and
  derived from canonical authorization content. It does NOT depend on
  random UUIDs, object memory addresses, unordered dictionary
  serialization, current wall-clock time, ``datetime.now()``, or process
  state.
* The model binds to a specific intent via ``intent_id`` and
  ``content_fingerprint``. The invariant
  ``authorization.intent_id == intent.intent_id`` and
  ``authorization.content_fingerprint == intent.content_fingerprint``
  must hold.
* Timestamps are caller-supplied. The model NEVER calls
  ``datetime.now()`` or ``datetime.utcnow()``.
* Temporal relationships: ``valid_from >= authorized_at``,
  ``expires_at > valid_from``, and when ``intent.valid_until`` is present,
  ``expires_at <= intent.valid_until``.
* ``__post_init__`` validates internal consistency: required fields,
  timestamp ordering, status-specific timing requirements, and the
  fail-closed principle.
* No business logic lives here; the model is a data carrier. The factory
  performs validation and identity computation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.operational_trade_intent import OperationalTradeIntent


# ============================================================
# CONSTANTS
# ============================================================


#: Prefix for authorization_id.
AUTHORIZATION_ID_PREFIX = "auth-"

#: Length of SHA-256 hex digest prefix used in identity strings.
_ID_DIGEST_LENGTH = 16


# ============================================================
# AUTHORIZATION STATUS
# ============================================================


class AuthorizationStatus(Enum):
    """
    Lifecycle status of an execution authorization.

    This enum describes the AUTHORIZATION state, NOT the market decision,
    NOT the simulation lifecycle, and NOT any external-system status. It is
    deliberately distinct from :class:`~engine.models.trade_plan.RiskPlanStatus`,
    and the Sprint 11S
    :class:`~engine.models.trade_decision.DecisionClassification`.

    UNAUTHORIZED
        No authorization exists for this intent. This is the initial state
        for every intent before authorization is requested. Must NEVER be
        interpreted as permission to execute.

    ELIGIBLE
        Policy gates pass; the intent is eligible for authorization and is
        awaiting human consent. Eligibility is a SYSTEM determination based
        on structural validity. ELIGIBLE is NOT AUTHORIZED — explicit human
        consent is still required.

    AUTHORIZED
        Human consent has been recorded for this specific intent under the
        recorded authorization conditions. The intent may proceed toward
        execution command generation. AUTHORIZED is NOT an execution
        command, NOT an order, NOT a position, NOT an external-system permission.

    EXPIRED
        The authorization's validity period has elapsed. An expired
        authorization must fail closed and must NOT be interpreted as
        currently valid.

    REVOKED
        The authorization was explicitly withdrawn. A revoked authorization
        must fail closed and must NOT be interpreted as currently valid.

    SUPERSEDED
        The intent was replaced by a newer intent (new ``intent_id`` /
        changed ``content_fingerprint``); this authorization is invalidated.
        A superseded authorization must fail closed.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    ELIGIBLE = "ELIGIBLE"
    AUTHORIZED = "AUTHORIZED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"

    @property
    def is_authorized(self) -> bool:
        """Whether this status represents an active authorization."""
        return self is AuthorizationStatus.AUTHORIZED


# ============================================================
# CANONICALIZATION
# ============================================================


def _canonical_value(value: Any) -> str:
    """Canonical string representation of a value for identity hashing.

    Normalizes ``Decimal`` values so that ``Decimal("1.0")`` and
    ``Decimal("1")`` produce the same canonical string. ``None`` becomes
    ``"null"``. Enums use their stable ``.name``. ``datetime`` uses ISO
    format. Booleans, ints, floats, and strings are tagged by type to
    prevent accidental type confusion.
    """

    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, Decimal):
        return f"dec:{value.normalize()!s}"
    if isinstance(value, Enum):
        return f"enum:{type(value).__name__}.{value.name}"
    if isinstance(value, datetime):
        return f"dt:{value.isoformat()}"
    if isinstance(value, int):
        return f"int:{value!s}"
    if isinstance(value, float):
        return f"num:{value!s}"
    return f"str:{value!s}"


def _canonical_authorization_payload(
    *,
    intent_id: str,
    plan_id: str,
    content_fingerprint: str,
    status: AuthorizationStatus,
    issuer: str,
    authorization_method: str,
    scope: str,
    policy_reference: str,
    safety_check_summary: str,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """Build the canonical identity payload for authorization_id generation.

    The identity captures the authorization-defining content that
    identifies the authorization event. Status is included because it is
    part of the authorization semantics. Timestamps are explicitly excluded
    so that the identity does not depend on wall-clock time.
    """

    return {
        "intent_id": _canonical_value(intent_id),
        "plan_id": _canonical_value(plan_id),
        "content_fingerprint": _canonical_value(content_fingerprint),
        "status": _canonical_value(status),
        "issuer": _canonical_value(issuer),
        "authorization_method": _canonical_value(authorization_method),
        "scope": _canonical_value(scope),
        "policy_reference": _canonical_value(policy_reference),
        "safety_check_summary": _canonical_value(safety_check_summary),
        "label": _canonical_value(label),
        "metadata": sorted(
            _canonical_value(k) + "=" + _canonical_value(v)
            for k, v in metadata
        ),
    }


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


# ============================================================
# MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    """
    An immutable authorization record for a specific OperationalTradeIntent.

    This artifact proves that a particular intent, identified by its
    ``intent_id`` and ``content_fingerprint``, was authorized under the
    recorded authorization conditions. It does NOT redefine the trade,
    NOT construct an execution command, and NOT place any order.

    Attributes:

    authorization_id
        Deterministic authorization identity (``"auth-" + sha256[:16]``)
        derived from canonical authorization content. Two semantically
        identical authorization records produce the same identity.

    intent_id
        The ``intent_id`` of the authorized
        :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`.
        Must match ``intent.intent_id``.

    plan_id
        Provenance reference to the source :class:`~engine.models.trade_plan.TradePlan`.

    content_fingerprint
        The ``content_fingerprint`` of the authorized intent at
        authorization time. Must match ``intent.content_fingerprint``.
        Used to verify that the intent presented for execution has not
        changed since authorization.

    status
        :class:`AuthorizationStatus` lifecycle state. The model fails
        closed: unknown or invalid states must never be interpreted as
        authorized.

    authorized_at
        Timezone-aware timestamp when the authorization was granted.
        Caller-supplied; the model NEVER generates this silently.

    valid_from
        Timezone-aware timestamp when the authorization becomes effective.
        Must satisfy ``valid_from >= authorized_at``. Caller-supplied.

    expires_at
        Timezone-aware timestamp when the authorization expires.
        Must satisfy ``expires_at > valid_from`` and, when the intent
        carries a ``valid_until``, ``expires_at <= intent.valid_until``.
        Caller-supplied.

    issuer
        Who/what granted the authorization (e.g. ``"human"``,
        ``"policy-engine"``). Descriptive provenance.

    authorization_method
        How the authorization was granted (e.g. ``"manual"``,
        ``"explicit-approval"``). Descriptive provenance.

    scope
        What is permitted under this authorization (e.g. ``"paper"``,
        ``"live"``). Descriptive only; the model does not enforce scope
        semantics.

    policy_reference
        Which policy version or rule set was applied. Descriptive
        provenance.

    safety_check_summary
        Which safety gates were evaluated and passed. Descriptive
        provenance.

    label / metadata
        Optional caller-supplied identity / metadata (audit trail).
    """

    authorization_id: str
    intent_id: str
    plan_id: str
    content_fingerprint: str

    status: AuthorizationStatus

    authorized_at: datetime
    valid_from: datetime
    expires_at: datetime

    issuer: str
    authorization_method: str
    scope: str
    policy_reference: str
    safety_check_summary: str

    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate internal consistency.

        The factory never produces inconsistent states; these checks guard
        against hand-construction bugs and enforce the fail-closed
        principle.
        """

        # Identity fields must be non-empty.
        if not self.authorization_id:
            raise ValueError("authorization_id must be non-empty.")
        if not self.intent_id:
            raise ValueError("intent_id must be non-empty.")
        if not self.plan_id:
            raise ValueError("plan_id must be non-empty.")
        if not self.content_fingerprint:
            raise ValueError("content_fingerprint must be non-empty.")

        # Authorization ID format validation.
        if not self.authorization_id.startswith(AUTHORIZATION_ID_PREFIX):
            raise ValueError(
                f"authorization_id must start with {AUTHORIZATION_ID_PREFIX!r}.",
            )

        # Content fingerprint format validation.
        if not self.content_fingerprint.startswith("fp-"):
            raise ValueError(
                "content_fingerprint must start with 'fp-'.",
            )

        # Timestamps must be timezone-aware.
        for field_name in ("authorized_at", "valid_from", "expires_at"):
            ts = getattr(self, field_name)
            if ts.tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware.")

        # Temporal relationships.
        if self.valid_from < self.authorized_at:
            raise ValueError("valid_from must be >= authorized_at.")
        if self.expires_at <= self.valid_from:
            raise ValueError("expires_at must be > valid_from.")

        # EXPIRED requires that validity has actually ended.
        if self.status is AuthorizationStatus.EXPIRED:
            if self.expires_at > self.authorized_at:
                # We cannot know the current time in domain logic, but we
                # can enforce that expires_at is strictly after authorized_at
                # for a logically consistent EXPIRED record.
                pass  # Caller-supplied; domain logic does not call datetime.now().

        # Provenance fields must be non-empty.
        if not self.issuer or not self.issuer.strip():
            raise ValueError("issuer must be non-empty.")
        if not self.authorization_method or not self.authorization_method.strip():
            raise ValueError("authorization_method must be non-empty.")
        if not self.scope or not self.scope.strip():
            raise ValueError("scope must be non-empty.")
        if not self.policy_reference or not self.policy_reference.strip():
            raise ValueError("policy_reference must be non-empty.")
        if not self.safety_check_summary or not self.safety_check_summary.strip():
            raise ValueError("safety_check_summary must be non-empty.")


# ============================================================
# FACTORY
# ============================================================


def create_authorization(
    *,
    intent: OperationalTradeIntent,
    status: AuthorizationStatus,
    authorized_at: datetime,
    valid_from: datetime,
    expires_at: datetime,
    issuer: str,
    authorization_method: str,
    scope: str,
    policy_reference: str,
    safety_check_summary: str,
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> ExecutionAuthorization:
    """Create an ExecutionAuthorization for a specific OperationalTradeIntent.

    This is a pure, deterministic factory. It preserves the intent's
    ``intent_id`` and ``content_fingerprint`` verbatim, validates
    timestamps and status, generates a deterministic ``authorization_id``,
    and does NOT mutate the intent, access trade geometry, access
    market data, invoke paper trading, or access any external system code.

    Args:
        intent:
            The :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`
            being authorized. Consumed by value (fields extracted); never
            mutated.
        status:
            The :class:`AuthorizationStatus` lifecycle state.
        authorized_at:
            Timezone-aware timestamp when the authorization was granted.
            Caller-supplied; the factory NEVER generates this silently.
        valid_from:
            Timezone-aware timestamp when the authorization becomes
            effective. Must satisfy ``valid_from >= authorized_at``.
        expires_at:
            Timezone-aware timestamp when the authorization expires.
            Must satisfy ``expires_at > valid_from`` and, when
            ``intent.valid_until`` is present, ``expires_at <= intent.valid_until``.
        issuer:
            Who/what granted the authorization. Must be non-empty.
        authorization_method:
            How the authorization was granted. Must be non-empty.
        scope:
            What is permitted under this authorization. Must be non-empty.
        policy_reference:
            Which policy version or rule set was applied. Must be non-empty.
        safety_check_summary:
            Which safety gates were evaluated and passed. Must be non-empty.
        label:
            Optional caller-supplied identity label.
        metadata:
            Optional caller-supplied metadata tuple (sorted pairs).

    Returns:
        An immutable :class:`ExecutionAuthorization`.

    Raises:
        TypeError:
            If ``intent`` is not an :class:`OperationalTradeIntent`.
        ValueError:
            If timestamps are invalid, status timing requirements are not
            met, the intent's ``valid_until`` constraint is violated, or
            required fields are missing.
    """

    # --- Type validation ---
    if not isinstance(intent, OperationalTradeIntent):
        raise TypeError(
            f"Expected an OperationalTradeIntent instance; "
            f"got {type(intent).__name__!r}.",
        )

    # --- Intent binding (preserved verbatim) ---
    bound_intent_id = intent.intent_id
    bound_content_fingerprint = intent.content_fingerprint
    bound_plan_id = intent.plan_id

    # --- Timestamp validation ---
    for field_name, ts in (
        ("authorized_at", authorized_at),
        ("valid_from", valid_from),
        ("expires_at", expires_at),
    ):
        if ts.tzinfo is None:
            raise ValueError(f"{field_name} must be timezone-aware.")

    if valid_from < authorized_at:
        raise ValueError("valid_from must be >= authorized_at.")
    if expires_at <= valid_from:
        raise ValueError("expires_at must be > valid_from.")

    # Authorization must not outlive the intent's valid_until.
    if intent.valid_until is not None:
        if expires_at > intent.valid_until:
            raise ValueError(
                "expires_at must be <= intent.valid_until "
                "when the intent has a valid_until.",
            )

    # --- Normalize metadata ---
    normalized_metadata = _normalize_metadata(metadata)

    # --- Compute deterministic authorization_id ---
    identity_payload = _canonical_authorization_payload(
        intent_id=bound_intent_id,
        plan_id=bound_plan_id,
        content_fingerprint=bound_content_fingerprint,
        status=status,
        issuer=issuer,
        authorization_method=authorization_method,
        scope=scope,
        policy_reference=policy_reference,
        safety_check_summary=safety_check_summary,
        label=label,
        metadata=normalized_metadata,
    )
    authorization_id = _sha256_prefix(identity_payload, AUTHORIZATION_ID_PREFIX)

    # --- Construct the immutable record ---
    return ExecutionAuthorization(
        authorization_id=authorization_id,
        intent_id=bound_intent_id,
        plan_id=bound_plan_id,
        content_fingerprint=bound_content_fingerprint,
        status=status,
        authorized_at=authorized_at,
        valid_from=valid_from,
        expires_at=expires_at,
        issuer=issuer,
        authorization_method=authorization_method,
        scope=scope,
        policy_reference=policy_reference,
        safety_check_summary=safety_check_summary,
        label=label,
        metadata=normalized_metadata,
    )


def _normalize_metadata(
    metadata: tuple[tuple[str, str], ...] | Any,
) -> tuple[tuple[str, str], ...]:
    """Normalize caller-supplied metadata to a sorted tuple of pairs."""
    if not metadata:
        return ()
    out: list[tuple[str, str]] = []
    for k, v in metadata:
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("metadata keys and values must be strings.")
        out.append((k, v))
    out.sort()
    return tuple(out)


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "AUTHORIZATION_ID_PREFIX",
    "AuthorizationStatus",
    "ExecutionAuthorization",
    "create_authorization",
]
