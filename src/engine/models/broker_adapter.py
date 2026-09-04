"""
Broker-neutral broker adapter contract models (Checkpoint 17.2).

This module defines the broker-NEUTRAL contract that a future broker-specific
adapter must implement. It contains NO broker SDK imports, NO broker-specific
exception classes, NO broker-specific response models, NO specific-broker 
any-broker references, and NO credentials.

The contract distinguishes typed result categories that downstream layers can
reason about without understanding any specific broker:

* ``SUBMITTED`` / ``ACCEPTED`` / ``PARTIALLY_FILLED`` / ``FILLED`` /
  ``CANCELLED`` -- submission progressed and the broker confirmed the outcome.
* ``REJECTED`` -- broker-confirmed rejection.
* ``FAILED`` -- known deterministic failure (validation rejection, internal
  adapter failure, transport failure etc.).
* ``UNKNOWN`` -- ambiguous submission state: the broker may or may not have
  accepted the request. A timeout is NEVER automatically a definitive failure.

Error taxonomy: every failure carries a single broker-neutral
:class:`BrokerErrorCode` plus a deterministic :class:`BrokerErrorCategory`
separating deterministic pre-submission rejection (VALIDATION),
broker-confirmed rejection (BROKER_REJECTION), transport/network failure
(TRANSPORT), ambiguous outcome (AMBIGUOUS), and internal adapter failure
(INTERNAL). A timeout / malformed response / unknown outcome is classified
AMBIGUOUS and therefore NEVER automatically retryable -- reconciliation is
required before any retry.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* :class:`BrokerError` reports a single broker-neutral error code.
* :class:`AdapterResult` is the single typed result envelope for adapter
  operations (submit / reconcile / cancel).
* Capability names are broker-neutral (``SUBMIT`` / ``RECONCILE`` / ``CANCEL``).
* ``AdapterResult.__post_init__`` is fail-closed: failure-like statuses MUST
  carry a :class:`BrokerError`; non-failure statuses MUST NOT.
* No business logic lives here; this module is data-carrier + validation only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Length of SHA-256 hex digest prefix used in deterministic order ids.
_ID_DIGEST_LENGTH = 16


# ============================================================
# RESULT STATUS
# ============================================================


class BrokerResultStatus(Enum):
    """Typed broker-neutral result status for adapter operations.

    SUBMITTED
        The broker received the submission request but the final outcome is
        not yet known.
    ACCEPTED
        The broker accepted / queued the order.
    PARTIALLY_FILLED
        The broker confirmed a partial fill; final outcome still pending.
    FILLED
        The broker confirmed a full fill.
    CANCELLED
        The broker confirmed cancellation.
    REJECTED
        The broker confirmed rejection.
    FAILED
        A known deterministic failure (validation, unsupported capability,
        transport failure, internal adapter failure).
    UNKNOWN
        Ambiguous outcome: the broker state is not known (e.g. timeout).
        Reconciliation is required before any retry.
    """

    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        """Whether this result is a final, known outcome."""
        return self in (
            self.ACCEPTED,
            self.PARTIALLY_FILLED,
            self.FILLED,
            self.CANCELLED,
            self.REJECTED,
            self.FAILED,
        )

    @property
    def is_ambiguous(self) -> bool:
        """Whether this result represents an unknown broker state."""
        return self is self.UNKNOWN


# ============================================================
# ERROR TAXONOMY
# ============================================================


class BrokerErrorCode(Enum):
    """Broker-neutral error taxonomy (Checkpoint 17.2 Phase 4)."""

    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    UNSUPPORTED_ORDER_SEMANTICS = "UNSUPPORTED_ORDER_SEMANTICS"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    TIMEOUT = "TIMEOUT"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    BROKER_REJECTION = "BROKER_REJECTION"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    INTERNAL_ADAPTER_FAILURE = "INTERNAL_ADAPTER_FAILURE"


class BrokerErrorCategory(Enum):
    """Broker-neutral error category separating concern boundaries.

    VALIDATION
        Deterministic request rejection BEFORE submission (unsupported
        instrument / order semantics / operation). Not retryable until the
        request is corrected.
    BROKER_REJECTION
        Broker-confirmed rejection. A fresh attempt after explicit review may
        be considered, but it is never "retry now".
    TRANSPORT
        Transport/network failure (network, rate limit, broker unavailable,
        authentication failure). Retryable in principle but broker state is
        not definitively known after submission has been attempted.
    AMBIGUOUS
        Ambiguous outcome: the broker state is unknown (timeout, malformed
        response, unknown outcome). Reconciliation is required before any
        retry; blind retry is prohibited by the contract.
    INTERNAL
        Internal adapter failure. Not retryable until the defect is fixed.
    """

    VALIDATION = "VALIDATION"
    BROKER_REJECTION = "BROKER_REJECTION"
    TRANSPORT = "TRANSPORT"
    AMBIGUOUS = "AMBIGUOUS"
    INTERNAL = "INTERNAL"


#: Deterministic code -> category mapping (fail closed on unknown codes).
_ERROR_CODE_CATEGORY: dict[BrokerErrorCode, BrokerErrorCategory] = {
    BrokerErrorCode.VALIDATION_FAILURE: BrokerErrorCategory.VALIDATION,
    BrokerErrorCode.UNSUPPORTED_OPERATION: BrokerErrorCategory.VALIDATION,
    BrokerErrorCode.UNSUPPORTED_INSTRUMENT: BrokerErrorCategory.VALIDATION,
    BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS: BrokerErrorCategory.VALIDATION,
    BrokerErrorCode.AUTHENTICATION_FAILURE: BrokerErrorCategory.TRANSPORT,
    BrokerErrorCode.AUTHORIZATION_FAILURE: BrokerErrorCategory.BROKER_REJECTION,
    BrokerErrorCode.RATE_LIMIT: BrokerErrorCategory.TRANSPORT,
    BrokerErrorCode.NETWORK_FAILURE: BrokerErrorCategory.TRANSPORT,
    BrokerErrorCode.TIMEOUT: BrokerErrorCategory.AMBIGUOUS,
    BrokerErrorCode.BROKER_UNAVAILABLE: BrokerErrorCategory.TRANSPORT,
    BrokerErrorCode.BROKER_REJECTION: BrokerErrorCategory.BROKER_REJECTION,
    BrokerErrorCode.MALFORMED_RESPONSE: BrokerErrorCategory.AMBIGUOUS,
    BrokerErrorCode.UNKNOWN_OUTCOME: BrokerErrorCategory.AMBIGUOUS,
    BrokerErrorCode.INTERNAL_ADAPTER_FAILURE: BrokerErrorCategory.INTERNAL,
}

#: Deterministic retryable flags per error code. A timeout / malformed /
#: unknown outcome is NEVER automatically retryable -- reconciliation first.
_ERROR_CODE_RETRYABLE: dict[BrokerErrorCode, bool] = {
    BrokerErrorCode.VALIDATION_FAILURE: False,
    BrokerErrorCode.UNSUPPORTED_OPERATION: False,
    BrokerErrorCode.UNSUPPORTED_INSTRUMENT: False,
    BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS: False,
    BrokerErrorCode.AUTHENTICATION_FAILURE: True,
    BrokerErrorCode.AUTHORIZATION_FAILURE: False,
    BrokerErrorCode.RATE_LIMIT: True,
    BrokerErrorCode.NETWORK_FAILURE: True,
    BrokerErrorCode.TIMEOUT: False,
    BrokerErrorCode.BROKER_UNAVAILABLE: True,
    BrokerErrorCode.BROKER_REJECTION: False,
    BrokerErrorCode.MALFORMED_RESPONSE: False,
    BrokerErrorCode.UNKNOWN_OUTCOME: False,
    BrokerErrorCode.INTERNAL_ADAPTER_FAILURE: False,
}


# ============================================================
# BROKER ERROR
# ============================================================


@dataclass(frozen=True, slots=True)
class BrokerError:
    """A single broker-neutral error.

    Attributes:
        code:
            A :class:`BrokerErrorCode` member. Known codes only (fail closed).
        message:
            Human-readable broker-neutral message. Never contains credentials,
            tokens, or Authorization headers.
        category:
            Derived deterministic :class:`BrokerErrorCategory`.
        retryable:
            Derived deterministic retryability flag.
    """

    code: BrokerErrorCode
    message: str
    category: BrokerErrorCategory
    retryable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.code, BrokerErrorCode):
            raise TypeError(
                f"code must be a BrokerErrorCode; "
                f"got {type(self.code).__name__!r}."
            )
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")

    @classmethod
    def for_code(cls, code: BrokerErrorCode, message: str) -> "BrokerError":
        """Build a BrokerError with the deterministic category/retryable flag."""
        try:
            category = _ERROR_CODE_CATEGORY[code]
            retryable = _ERROR_CODE_RETRYABLE[code]
        except KeyError:
            raise ValueError(f"Unknown BrokerErrorCode {code!r}.") from None
        return cls(code=code, message=message, category=category, retryable=retryable)

    @property
    def is_ambiguous(self) -> bool:
        """Whether this error represents an ambiguous (unknown) outcome."""
        return self.category is BrokerErrorCategory.AMBIGUOUS


# ============================================================
# ADAPTER RESULT
# ============================================================


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


#: Prefix used for deterministic fake broker order ids.
BROKER_ORDER_ID_PREFIX = "brk-"


def derive_broker_order_id(*, client_order_id: str, operation: str, scenario: str) -> str:
    """Derive a deterministic broker-side order id for deterministic fakes.

    A real broker generates its own order id; this helper documents the
    deterministic contract that deterministic fake / adapter-compatible tests
    rely on. It is broker-neutral and used ONLY in test scaffolds.
    """

    payload = {
        "client_order_id": client_order_id,
        "operation": operation,
        "scenario": scenario,
    }
    return _sha256_prefix(payload, BROKER_ORDER_ID_PREFIX)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Typed broker-neutral result envelope for adapter operations.

    Attributes:
        status:
            A :class:`BrokerResultStatus` member.
        error:
            :class:`BrokerError` or None. Required for REJECTED / FAILED /
            UNKNOWN results; MUST be None for non-failure results (fail closed).
        broker_order_id:
            Broker-generated order reference, or None. Broker-specific and
            downstream-only; never inserted into upstream artifacts.
        broker_status:
            Short broker-status descriptor normalized at the adapter boundary,
            or "".
        reason:
            Short broker-neutral human-readable reason, or "".
    """

    status: BrokerResultStatus
    error: BrokerError | None = None
    broker_order_id: str | None = None
    broker_status: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, BrokerResultStatus):
            raise TypeError(
                f"status must be a BrokerResultStatus; "
                f"got {type(self.status).__name__!r}."
            )
        if self.error is not None and not isinstance(self.error, BrokerError):
            raise TypeError(
                f"error must be a BrokerError or None; "
                f"got {type(self.error).__name__!r}."
            )
        failure_like = self.status in (
            BrokerResultStatus.REJECTED,
            BrokerResultStatus.FAILED,
            BrokerResultStatus.UNKNOWN,
        )
        if failure_like and self.error is None:
            raise ValueError(
                f"A {self.status.value} result must carry a BrokerError; "
                f"got error=None."
            )
        if not failure_like and self.error is not None:
            raise ValueError(
                f"A {self.status.value} result must NOT carry a BrokerError; "
                f"got {self.error.code.value!r}."
            )

    @property
    def is_ambiguous(self) -> bool:
        """Whether this result needs reconciliation before retry."""
        return self.status is BrokerResultStatus.UNKNOWN

    @classmethod
    def rejected(cls, *, reason: str, broker_order_id: str | None = None) -> "AdapterResult":
        """Factory for a broker-confirmed rejection."""
        return cls(
            status=BrokerResultStatus.REJECTED,
            error=BrokerError.for_code(BrokerErrorCode.BROKER_REJECTION, reason),
            broker_order_id=broker_order_id,
            reason=reason,
        )

    @classmethod
    def failed(cls, *, code: BrokerErrorCode, reason: str, broker_order_id: str | None = None) -> "AdapterResult":
        """Factory for a known deterministic failure."""
        return cls(
            status=BrokerResultStatus.FAILED,
            error=BrokerError.for_code(code, reason),
            broker_order_id=broker_order_id,
            reason=reason,
        )

    @classmethod
    def unknown(cls, *, code: BrokerErrorCode, reason: str, broker_order_id: str | None = None) -> "AdapterResult":
        """Factory for an ambiguous outcome that requires reconciliation."""
        return cls(
            status=BrokerResultStatus.UNKNOWN,
            error=BrokerError.for_code(code, reason),
            broker_order_id=broker_order_id,
            reason=reason,
        )


# ============================================================
# ADAPTER CAPABILITY
# ============================================================


class AdapterCapability(Enum):
    """Broker-neutral adapter capabilities for the capability boundary.

    SUBMIT
        The adapter can submit a command.
    RECONCILE
        The adapter can reconcile (query) an earlier submission by client
        order id.
    CANCEL
        The adapter can request cancellation of an in-flight submission.
    """

    SUBMIT = "SUBMIT"
    RECONCILE = "RECONCILE"
    CANCEL = "CANCEL"


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "BROKER_ORDER_ID_PREFIX",
    "AdapterCapability",
    "AdapterResult",
    "BrokerError",
    "BrokerErrorCategory",
    "BrokerErrorCode",
    "BrokerResultStatus",
    "derive_broker_order_id",
]
