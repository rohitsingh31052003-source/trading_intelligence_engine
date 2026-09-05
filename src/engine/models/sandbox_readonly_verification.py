"""Checkpoint 18.2 read-only verification / audit models (frozen+slots).

These models are the SAFE observability surface of the Checkpoint 18.2
controlled sandbox connectivity stage. They record a broker-neutral audit
trail of read-only broker verification: broker, environment, operation type,
endpoint category (never credentials), timestamp, response classification,
normalized result, broker order id (only when non-sensitive and appropriate),
reconciliation result, and error category/code.

CRITICAL SAFETY RULES (Checkpoint 18.2):

* NEVER record: access token, Authorization header, bearer token, secrets,
  raw credential-bearing HTTP headers, or sensitive request data. The
  logger / verifier design guarantees the token value is structurally absent
  from every audit field -- only the environment-var NAME is referenced in
  redaction, never the value.
* The audit records remain broker-neutral where they cross into core: they
  use the broker-neutral :class:`BrokerResultStatus` /
  :class:`BrokerErrorCode` / :class:`BrokerErrorCategory` vocabulary and
  opaque endpoint-category strings.
* ``broker_order_id`` and ``client_order_id`` are recorded ONLY when
  explicitly supplied (caller decides they are non-sensitive and appropriate
  for that verification); a read-only sandbox verification of an existing
  order record may record the broker order id because it is not a secret --
  but the default is ``None``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from engine.models.broker_adapter import (
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)

#: Prefix for deterministic verification audit ids.
VALIDATION_AUDIT_ID_PREFIX = "valaudit-"
_ID_DIGEST_LENGTH = 16


class ReadOnlyOperationType(Enum):
    """The kind of read-only broker operation performed.

    PROFILE
        Read-only identity / capability verification (Get Profile).
    ORDER_DETAILS
        Read-only single-record order lookup (Get Order Details).
    ORDER_HISTORY
        Read-only order-history array lookup (Get Order History).
    HEALTH
        Read-only reachability / authentication probe.
    """

    PROFILE = "PROFILE"
    ORDER_DETAILS = "ORDER_DETAILS"
    ORDER_HISTORY = "ORDER_HISTORY"
    HEALTH = "HEALTH"


class VerificationEnvironment(Enum):
    """Environment reported for the controlled verification.

    SANDBOX
        The broker's official sandbox environment.
    PAPER
        A simulated / paper execution environment.
    UNKNOWN
        Environment identity cannot be positively established (fail closed).
    """

    SANDBOX = "SANDBOX"
    PAPER = "PAPER"
    UNKNOWN = "UNKNOWN"


class VerificationClassification(Enum):
    """Response classification of a read-only broker verification.

    SUCCESS
        The operation completed and the broker response was positively
        validated.
    FAILED
        A known deterministic failure (validation / transport / auth / 5xx).
    AMBIGUOUS
        The outcome could not be deterministically established (timeout /
        malformed / unknown / multiple records / zero records). Never
        converted into SUCCESS without broker-confirmed evidence.
    UNVERIFIED
        The operation was not performed (e.g. no credential / gate blocked).
        A skipped verification is explicitly NOT a success.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class SandboxVerificationAuditEntry:
    """A single deterministic audit entry for a read-only verification.

    Fields never carry credentials. ``endpoint_category`` is an opaque
    category string (e.g. ``user_profile``); ``request_purpose`` is a short
    descriptive purpose. ``detail`` carries the redacted broker-neutral
    reason (already scrubbed via ``redact_sensitive`` by the caller).
    """

    operation_type: ReadOnlyOperationType
    environment: VerificationEnvironment
    endpoint_category: str
    request_purpose: str
    performed_at: datetime
    classification: VerificationClassification
    normalized_status: BrokerResultStatus | None
    audit_id: str
    broker_order_id: str | None = None
    client_order_id: str | None = None
    error_code: BrokerErrorCode | None = None
    error_category: BrokerErrorCategory | None = None
    reconciliation_result: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.operation_type, ReadOnlyOperationType):
            raise TypeError("operation_type must be a ReadOnlyOperationType.")
        if not isinstance(self.environment, VerificationEnvironment):
            raise TypeError("environment must be a VerificationEnvironment.")
        if not isinstance(self.classification, VerificationClassification):
            raise TypeError("classification must be a VerificationClassification.")
        if not isinstance(self.endpoint_category, str) or not self.endpoint_category.strip():
            raise ValueError("endpoint_category must be a non-empty string.")
        if not isinstance(self.request_purpose, str) or not self.request_purpose.strip():
            raise ValueError("request_purpose must be a non-empty string.")
        if not isinstance(self.performed_at, datetime):
            raise TypeError("performed_at must be a datetime.")
        if self.performed_at.tzinfo is None:
            raise ValueError("performed_at must be timezone-aware.")
        if self.normalized_status is not None and not isinstance(
            self.normalized_status, BrokerResultStatus
        ):
            raise TypeError("normalized_status must be a BrokerResultStatus or None.")
        if self.error_code is not None and not isinstance(self.error_code, BrokerErrorCode):
            raise TypeError("error_code must be a BrokerErrorCode or None.")
        if self.error_category is not None and not isinstance(
            self.error_category, BrokerErrorCategory
        ):
            raise TypeError("error_category must be a BrokerErrorCategory or None.")
        # Ambiguous / failed classifications must carry the corresponding
        # error taxonomy fields (fail closed; an audit entry never hides an
        # ambiguous outcome behind a success-looking row).
        if self.classification is VerificationClassification.AMBIGUOUS:
            if self.error_code is None:
                raise ValueError(
                    "An AMBIGUOUS audit entry must carry an error_code "
                    "(never an empty audit row for an ambiguous outcome)."
                )
            if self.normalized_status is not BrokerResultStatus.UNKNOWN:
                raise ValueError(
                    "An AMBIGUOUS audit entry must carry normalized_status "
                    "UNKNOWN (never a success on an ambiguous outcome)."
                )
        if self.classification is VerificationClassification.FAILED:
            if self.error_code is None:
                raise ValueError("A FAILED audit entry must carry an error_code.")
            if self.normalized_status is not BrokerResultStatus.FAILED:
                raise ValueError(
                    "A FAILED audit entry must carry normalized_status FAILED."
                )

    @classmethod
    def _identity(
        cls,
        *,
        operation_type: ReadOnlyOperationType,
        environment: VerificationEnvironment,
        endpoint_category: str,
        request_purpose: str,
        performed_at: datetime,
        classification: VerificationClassification,
        normalized_status: BrokerResultStatus | None,
        broker_order_id: str | None,
        client_order_id: str | None,
        error_code: BrokerErrorCode | None,
        error_category: BrokerErrorCategory | None,
        reconciliation_result: str,
        detail: str,
    ) -> str:
        payload: dict[str, Any] = {
            "operation_type": operation_type.value,
            "environment": environment.value,
            "endpoint_category": endpoint_category,
            "request_purpose": request_purpose,
            "performed_at": performed_at.isoformat(),
            "classification": classification.value,
            "normalized_status": None if normalized_status is None else normalized_status.value,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "error_code": None if error_code is None else error_code.value,
            "error_category": (
                None if error_category is None else error_category.value
            ),
            "reconciliation_result": reconciliation_result,
            "detail": detail,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{VALIDATION_AUDIT_ID_PREFIX}{digest[:_ID_DIGEST_LENGTH]}"

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection (never any credential)."""
        return {
            "audit_id": self.audit_id,
            "operation_type": self.operation_type.value,
            "environment": self.environment.value,
            "endpoint_category": self.endpoint_category,
            "request_purpose": self.request_purpose,
            "performed_at": self.performed_at.isoformat(),
            "classification": self.classification.value,
            "normalized_status": (
                None if self.normalized_status is None else self.normalized_status.value
            ),
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "error_code": None if self.error_code is None else self.error_code.value,
            "error_category": (
                None if self.error_category is None else self.error_category.value
            ),
            "reconciliation_result": self.reconciliation_result,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SandboxReadOnlyVerification:
    """Aggregate result of the Checkpoint 18.2 read-only verification.

    Deterministic identity (``verification_id = "roverify-" + sha256[:16]``)
    over the canonical inputs. Never carries credentials or sensitive account
    information; only the masked profile facts bookended by the audit trail.

    For determining whether real sandbox connectivity was established, the
    authoritative field is ``real_sandbox_connected`` -- True ONLY when the
    environment was SANDBOX, a genuine token was available, the guard passed,
    and the verification ran to completion. The default for this repository
    delivery is False (no controlled credential is available), so the
    verification honestly reports REAL SANDBOX CONNECTIVITY as NOT VERIFIED.
    """

    verification_id: str
    broker: str
    environment: VerificationEnvironment
    started_at: datetime
    completed_at: datetime
    token_available: bool
    gate_passed: bool
    real_sandbox_connected: bool
    profile_broker: str = ""
    profile_user_type: str = ""
    profile_exchanges: tuple[str, ...] = ()
    profile_products: tuple[str, ...] = ()
    profile_order_types: tuple[str, ...] = ()
    profile_is_active: bool = False
    profile_user_id_present: bool = False
    reconciliation_result: str = ""
    audit_entries: tuple[SandboxVerificationAuditEntry, ...] = ()
    conclusion: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.environment, VerificationEnvironment):
            raise TypeError("environment must be a VerificationEnvironment.")
        if not isinstance(self.audit_entries, tuple):
            raise TypeError("audit_entries must be a tuple.")
        for entry in self.audit_entries:
            if not isinstance(entry, SandboxVerificationAuditEntry):
                raise TypeError("audit_entries must contain audit entries.")
        if self.profile_user_id_present and self.profile_broker != "UPSTOX":
            # A profile identity is only meaningful when the broker returned
            # it; keep this a soft invariant (never a hard requirement).
            pass

    @classmethod
    def _identity(
        cls,
        *,
        broker: str,
        environment: VerificationEnvironment,
        started_at: datetime,
        completed_at: datetime,
        token_available: bool,
        gate_passed: bool,
        real_sandbox_connected: bool,
        audit_entries: tuple[SandboxVerificationAuditEntry, ...],
    ) -> str:
        payload: dict[str, Any] = {
            "broker": broker,
            "environment": environment.value,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "token_available": bool(token_available),
            "gate_passed": bool(gate_passed),
            "real_sandbox_connected": bool(real_sandbox_connected),
            "audit_entries": [entry.to_dict() for entry in audit_entries],
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"roverify-{digest[:_ID_DIGEST_LENGTH]}"

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection (never any credential)."""
        return {
            "verification_id": self.verification_id,
            "broker": self.broker,
            "environment": self.environment.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "token_available": bool(self.token_available),
            "gate_passed": bool(self.gate_passed),
            "real_sandbox_connected": bool(self.real_sandbox_connected),
            "profile_broker": self.profile_broker,
            "profile_user_type": self.profile_user_type,
            "profile_exchanges": tuple(sorted(self.profile_exchanges)),
            "profile_products": tuple(sorted(self.profile_products)),
            "profile_order_types": tuple(sorted(self.profile_order_types)),
            "profile_is_active": bool(self.profile_is_active),
            "profile_user_id_present": bool(self.profile_user_id_present),
            "reconciliation_result": self.reconciliation_result,
            "audit_entries": [entry.to_dict() for entry in self.audit_entries],
            "conclusion": self.conclusion,
        }


@dataclass(frozen=True, slots=True)
class SandboxVerificationResult:
    """Simplified redacted outcome of one read-only operation.

    Used to carry a per-operation outcome with a redacted detail string back
    to the caller without leaking raw broker response bodies.
    """

    classification: VerificationClassification
    normalized_status: BrokerResultStatus | None
    endpoint_category: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.classification, VerificationClassification):
            raise TypeError("classification must be a VerificationClassification.")
        if self.normalized_status is not None and not isinstance(
            self.normalized_status, BrokerResultStatus
        ):
            raise TypeError("normalized_status must be a BrokerResultStatus or None.")
        if not isinstance(self.endpoint_category, str) or not self.endpoint_category.strip():
            raise ValueError("endpoint_category must be a non-empty string.")
        if not isinstance(self.detail, str):
            raise TypeError("detail must be a string.")


__all__ = [
    "VALIDATION_AUDIT_ID_PREFIX",
    "ReadOnlyOperationType",
    "SandboxReadOnlyVerification",
    "SandboxVerificationAuditEntry",
    "SandboxVerificationResult",
    "VerificationClassification",
    "VerificationEnvironment",
]