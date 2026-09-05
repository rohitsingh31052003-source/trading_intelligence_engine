"""Checkpoint 18.2 sandbox read-only verification service.

This module ORCHESTRATES the controlled Upstox Sandbox read-only
verification:

    startup guard (frozen 17.8) -> transport credential gate
        -> READ-ONLY HTTP GETs (profile / order details / order history)
        -> response validation (fail closed)
        -> broker-neutral audit entries + aggregate verification

It is the ONLY production module that wires the frozen
:class:`UpstoxBrokerClient` protocol implementation
(:class:`~engine.intelligence.upstox_sandbox_transport.UpstoxSandboxTransport`)
into a read-only verification flow. It performs NO order-affecting operation
---- the transport itself blocks ``place_order`` / ``cancel_order``.

Credential rule (Checkpoint 18.2 rule #2 / #28):

* The credential is read ONLY from the injected provider (the concrete
  ``EnvironmentUpstoxCredentialProvider`` reads ``UPSTOX_EXECUTION_ACCESS_TOKEN``
  lazily). The analytics token ``UPSTOX_ANALYTICS_TOKEN`` is NEVER read here.
* The token VALUE is never stored, logged, persisted, included in exceptions,
  included in audit records, or included in this module's repr/str. Only a
  boolean ``token_available`` flag crosses into the audit trail.
* When the token is missing / empty / malformed the verification FAILS
  CLOSED with ``token_available=False`` and ``real_sandbox_connected=False``.
* The opt-in gate is REQUIRED: unless ``CHECKPOINT_17_8_REAL_BROKER=1`` the
  verification records UNVERIFIED and never issues a request (reuses the
  frozen repository-wide convention; a new ``CHECKPOINT_18_2_SANDBOX`` is not
  needed because the 17.8 gate is already the single real-broker gate).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from engine.intelligence.controlled_broker_validation import (
    ValidationCredentialProvider,
    controlled_broker_startup_guard,
    real_broker_integration_enabled,
)
from engine.intelligence.upstox_broker_client import UpstoxBrokerClient, redact_sensitive
from engine.intelligence.upstox_broker_models import UpstoxClientFailure, UpstoxErrorKind
from engine.intelligence.upstox_sandbox_transport import (
    ENDPOINT_ORDER_DETAILS,
    ENDPOINT_ORDER_HISTORY,
    ENDPOINT_PROFILE,
    UpstoxProfileResponse,
    UpstoxSandboxTransport,
)
from engine.models.broker_adapter import (
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.sandbox_readonly_verification import (
    ReadOnlyOperationType,
    SandboxReadOnlyVerification,
    SandboxVerificationAuditEntry,
    SandboxVerificationResult,
    VerificationClassification,
    VerificationEnvironment,
)

#: Broker identity recognized by the controlled-verification boundary.
_BROKER_IDENTITY = "upstox"
#: Environment reported for the controlled verification.
_ENVIRONMENT = VerificationEnvironment.SANDBOX
#: Execution mode required by the startup guard.
_EXPECTED_MODE = "PAPER"
#: Capabilities required for the guarded read-only verification.
_REQUIRED_CAPABILITIES: tuple[str, ...] = ("SUBMIT", "RECONCILE")


@runtime_checkable
class ClockProvider(Protocol):
    """Simple clock boundary so verification is deterministic in tests."""

    def utcnow(self) -> _dt.datetime:  # pragma: no cover - protocol
        ...


def _default_clock() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _map_failure(
    failure: UpstoxClientFailure,
) -> tuple[VerificationClassification, BrokerResultStatus | None, BrokerErrorCode | None, BrokerErrorCategory | None]:
    """Map a transport failure into the broker-neutral audit vocabulary.

    Ambiguous kinds map to AMBIGUOUS / UNKNOWN (reconciliation semantics
    preserved). Everything else maps to FAILED with the matching code. The
    token value can never appear here (``redact_sensitive`` scrubs it).
    """

    kind = failure.kind
    if kind in (
        UpstoxErrorKind.TIMEOUT,
        UpstoxErrorKind.UNKNOWN_OUTCOME,
        UpstoxErrorKind.MALFORMED_RESPONSE,
    ):
        code = (BrokerErrorCode.TIMEOUT if kind is UpstoxErrorKind.TIMEOUT
                else BrokerErrorCode.MALFORMED_RESPONSE if kind is UpstoxErrorKind.MALFORMED_RESPONSE
                else BrokerErrorCode.UNKNOWN_OUTCOME)
        return (
            VerificationClassification.AMBIGUOUS,
            BrokerResultStatus.UNKNOWN,
            code,
            BrokerErrorCategory.AMBIGUOUS,
        )
    if kind is UpstoxErrorKind.AUTHENTICATION:
        return (
            VerificationClassification.FAILED,
            BrokerResultStatus.FAILED,
            BrokerErrorCode.AUTHENTICATION_FAILURE,
            BrokerErrorCategory.TRANSPORT,
        )
    if kind is UpstoxErrorKind.AUTHORIZATION:
        return (
            VerificationClassification.FAILED,
            BrokerResultStatus.FAILED,
            BrokerErrorCode.AUTHORIZATION_FAILURE,
            BrokerErrorCategory.BROKER_REJECTION,
        )
    if kind is UpstoxErrorKind.RATE_LIMIT:
        return (
            VerificationClassification.AMBIGUOUS,
            BrokerResultStatus.UNKNOWN,
            BrokerErrorCode.RATE_LIMIT,
            BrokerErrorCategory.TRANSPORT,
        )
    if kind is UpstoxErrorKind.NETWORK:
        return (
            VerificationClassification.FAILED,
            BrokerResultStatus.FAILED,
            BrokerErrorCode.NETWORK_FAILURE,
            BrokerErrorCategory.TRANSPORT,
        )
    if kind is UpstoxErrorKind.BROKER_UNAVAILABLE:
        return (
            VerificationClassification.FAILED,
            BrokerResultStatus.FAILED,
            BrokerErrorCode.BROKER_UNAVAILABLE,
            BrokerErrorCategory.TRANSPORT,
        )
    if kind is UpstoxErrorKind.VALIDATION:
        return (
            VerificationClassification.FAILED,
            BrokerResultStatus.FAILED,
            BrokerErrorCode.VALIDATION_FAILURE,
            BrokerErrorCategory.VALIDATION,
        )
    return (
        VerificationClassification.FAILED,
        BrokerResultStatus.FAILED,
        BrokerErrorCode.INTERNAL_ADAPTER_FAILURE,
        BrokerErrorCategory.INTERNAL,
    )


class SandboxReadOnlyVerifier:
    """Deterministic orchestrator for the controlled read-only verification.

    Attributes:
        transport:
            The injected read-only transport (implements the frozen
            :class:`UpstoxBrokerClient` protocol). ``None`` is allowed and
            yields UNVERIFIED (no connectivity path exists).
        credential_provider:
            The injected credential provider (lazy env provider by default).
        clock:
            Injectable clock for deterministic timestamps (tests).
        base_url / api_path / timeout_seconds / user_agent:
            Passed through to a freshly built transport when none is given
            (the default URL is the official read-only API base; the sandbox
            place-order base is never used here because no order endpoint is
            ever invoked).
        broker_order_ids:
            Optional tuple of existing broker order ids to check read-only
            (reconciliation data must pre-exist; the verifier NEVER creates
            an order to test). Default () -> reconciliation is recorded as
            NOT VERIFIED / skipped honestly.
    """

    def __init__(
        self,
        *,
        transport: Any = None,
        credential_provider: Any = None,
        clock: ClockProvider | None = None,
        base_url: str = "https://api.upstox.com",
        api_path: str = "/v2",
        timeout_seconds: int = 30,
        user_agent: str = "python-urllib/upstox-sandbox-readonly-transport",
        broker_order_ids: tuple[str, ...] = (),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.transport = transport
        self.credential_provider = credential_provider
        self.clock = clock
        if transport is None:
            self.transport = UpstoxSandboxTransport(
                base_url=base_url,
                api_path=api_path,
                timeout_seconds=timeout_seconds,
                credential_provider=credential_provider,
                user_agent=user_agent,
            )
        self.base_url = base_url
        self.api_path = api_path
        self.timeout_seconds = int(timeout_seconds)
        self.user_agent = user_agent
        if not isinstance(broker_order_ids, tuple) or not all(
            isinstance(v, str) and v for v in broker_order_ids
        ):
            raise ValueError("broker_order_ids must be a tuple of non-empty strings.")
        self.broker_order_ids = broker_order_ids

    # ---------------------------------------------------------
    # TIMESTAMP HELPERS
    # ---------------------------------------------------------

    def _now(self) -> _dt.datetime:
        if self.clock is not None:
            value = self.clock.utcnow()
        else:
            value = _default_clock()
        if isinstance(value, _dt.datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=_dt.timezone.utc)
            return value
        raise TypeError("clock must yield a datetime.")

    # ---------------------------------------------------------
    # CREDENTIAL GATE (token VALUE never leaves provider/transport)
    # ---------------------------------------------------------

    def token_available(self) -> bool:
        if self.credential_provider is None:
            return False
        try:
            token = self.credential_provider.get_access_token()
        except Exception:
            return False
        return isinstance(token, str) and bool(token)

    def _scrub_token(self, text: str) -> str:
        """Redact the ACTUAL token value from any detail string.

        Defense-in-depth: the transport already scrubs its own messages, but
        a fake transport or an unexpected path could surface the token value;
        this guarantees it can never reach an audit record.
        """

        text = redact_sensitive(text)
        if self.credential_provider is not None:
            try:
                token = self.credential_provider.get_access_token()
            except Exception:
                token = ""
            if isinstance(token, str) and token and token in text:
                text = text.replace(token, "<redacted>")
        return text

    def _credential_provider_for_guard(self) -> ValidationCredentialProvider | Any | None:
        return self.credential_provider

    def guard_result(self) -> Any:
        """Run the frozen controlled-broker startup guard (no network)."""

        return controlled_broker_startup_guard(
            broker_identity=_BROKER_IDENTITY,
            execution_mode=_EXPECTED_MODE,
            environment=_ENVIRONMENT.value,
            credential_provider=self.credential_provider,
            capability_names=_REQUIRED_CAPABILITIES,
            required_config={
                "base_url": self.base_url,
                "api_path": self.api_path,
                "timeout_seconds": str(self.timeout_seconds),
                "user_agent": self.user_agent,
            },
            required_config_keys=("base_url", "api_path", "timeout_seconds"),
            expected_environment=_ENVIRONMENT.value,
            expected_mode=_EXPECTED_MODE,
        )

    # ---------------------------------------------------------
    # READ-ONLY VERIFICATION
    # ---------------------------------------------------------

    def verify(self, *, opts: Mapping[str, Any] | None = None) -> SandboxReadOnlyVerification:
        """Run ONE read-only sandbox verification.

        Flow (fail closed at every step):

        1. If the opt-in gate (``CHECKPOINT_17_8_REAL_BROKER``) is not
           enabled, record UNVERIFIED and stop (no request is issued).
        2. If no credential is available / malformed, record UNVERIFIED and
           stop (no request is issued).
        3. Run the frozen startup guard; on any unmet precondition record
           UNVERIFIED and stop.
        4. Run the read-only checks (profile identity; order details +
           history for pre-existing order ids; health) and record one audit
           entry each.
        5. Aggregate into :class:`SandboxReadOnlyVerification`
           (``real_sandbox_connected`` True ONLY when environment SANDBOX +
           token + guard + at least one positive profile identity).
        """

        options = dict(opts or {})
        started_at = self._now()

        if not real_broker_integration_enabled():
            conclusion = (
                "Read-only verification NOT performed: the real-broker "
                "opt-in gate CHECKPOINT_17_8_REAL_BROKER is not enabled "
                "(fail closed; no request issued)."
            )
            return SandboxReadOnlyVerification(
                verification_id=SandboxReadOnlyVerification._identity(
                    broker=_BROKER_IDENTITY,
                    environment=_ENVIRONMENT,
                    started_at=started_at,
                    completed_at=self._now(),
                    token_available=False,
                    gate_passed=False,
                    real_sandbox_connected=False,
                    audit_entries=(),
                ),
                broker=_BROKER_IDENTITY,
                environment=_ENVIRONMENT,
                started_at=started_at,
                completed_at=self._now(),
                token_available=False,
                gate_passed=False,
                real_sandbox_connected=False,
                reconciliation_result="NOT_VERIFIED",
                conclusion=conclusion,
            )

        token_ok = self.token_available()
        guard = self.guard_result()
        gate_passed = bool(token_ok and getattr(guard, "is_safe", False))

        if not gate_passed:
            conclusion = (
                "Read-only verification NOT performed: startup guard or "
                "credential precondition not met (fail closed; no request "
                "issued)."
            )
            audit_entries = (
                SandboxVerificationAuditEntry(
                    operation_type=ReadOnlyOperationType.HEALTH,
                    environment=_ENVIRONMENT,
                    endpoint_category="guard",
                    request_purpose="controlled-broker startup guard",
                    performed_at=started_at,
                    classification=VerificationClassification.UNVERIFIED,
                    normalized_status=None,
                    audit_id=SandboxVerificationAuditEntry._identity(
                        operation_type=ReadOnlyOperationType.HEALTH,
                        environment=_ENVIRONMENT,
                        endpoint_category="guard",
                        request_purpose="controlled-broker startup guard",
                        performed_at=started_at,
                        classification=VerificationClassification.UNVERIFIED,
                        normalized_status=None,
                        broker_order_id=None,
                        client_order_id=None,
                        error_code=None,
                        error_category=None,
                        reconciliation_result="",
                        detail="startup guard / credential precondition failed",
                    ),
                    detail="startup guard / credential precondition failed",
                ),
            )
            return SandboxReadOnlyVerification(
                verification_id=SandboxReadOnlyVerification._identity(
                    broker=_BROKER_IDENTITY,
                    environment=_ENVIRONMENT,
                    started_at=started_at,
                    completed_at=self._now(),
                    token_available=token_ok,
                    gate_passed=False,
                    real_sandbox_connected=False,
                    audit_entries=audit_entries,
                ),
                broker=_BROKER_IDENTITY,
                environment=_ENVIRONMENT,
                started_at=started_at,
                completed_at=self._now(),
                token_available=token_ok,
                gate_passed=False,
                real_sandbox_connected=False,
                reconciliation_result="NOT_VERIFIED",
                audit_entries=audit_entries,
                conclusion=conclusion,
            )

        # Guard passed + token available: perform READ-ONLY checks.
        audit: list[SandboxVerificationAuditEntry] = []
        detail_results: list[SandboxVerificationResult] = []

        profile = self.transport.get_profile()
        if isinstance(profile, UpstoxProfileResponse):
            audit.append(
                SandboxVerificationAuditEntry(
                    operation_type=ReadOnlyOperationType.PROFILE,
                    environment=_ENVIRONMENT,
                    endpoint_category=ENDPOINT_PROFILE,
                    request_purpose="read-only identity / capability verification",
                    performed_at=self._now(),
                    classification=VerificationClassification.SUCCESS,
                    normalized_status=BrokerResultStatus.ACCEPTED,
                    audit_id=SandboxVerificationAuditEntry._identity(
                        operation_type=ReadOnlyOperationType.PROFILE,
                        environment=_ENVIRONMENT,
                        endpoint_category=ENDPOINT_PROFILE,
                        request_purpose="read-only identity / capability verification",
                        performed_at=self._now(),
                        classification=VerificationClassification.SUCCESS,
                        normalized_status=BrokerResultStatus.ACCEPTED,
                        broker_order_id=None,
                        client_order_id=None,
                        error_code=None,
                        error_category=None,
                        reconciliation_result="",
                        detail="profile identity verified (masked)",
                    ),
                    detail="profile identity verified (masked)",
                )
            )
            detail_results.append(
                SandboxVerificationResult(
                    classification=VerificationClassification.SUCCESS,
                    normalized_status=BrokerResultStatus.ACCEPTED,
                    endpoint_category=ENDPOINT_PROFILE,
                    detail="profile identity verified (masked)",
                )
            )
        else:
            cls, norm, code, cat = _map_failure(profile)
            audit.append(SandboxVerificationAuditEntry(
                operation_type=ReadOnlyOperationType.PROFILE,
                environment=_ENVIRONMENT,
                endpoint_category=ENDPOINT_PROFILE,
                request_purpose="read-only identity / capability verification",
                performed_at=self._now(),
                classification=cls,
                normalized_status=norm,
                audit_id=SandboxVerificationAuditEntry._identity(
                    operation_type=ReadOnlyOperationType.PROFILE,
                    environment=_ENVIRONMENT,
                    endpoint_category=ENDPOINT_PROFILE,
                    request_purpose="read-only identity / capability verification",
                    performed_at=self._now(),
                    classification=cls,
                    normalized_status=norm,
                    broker_order_id=None,
                    client_order_id=None,
                    error_code=code,
                    error_category=cat,
                    reconciliation_result="",
                    detail=self._scrub_token(getattr(profile, "message", "")),
                ),
                error_code=code,
                error_category=cat,
                detail=self._scrub_token(getattr(profile, "message", "")),
            ))
            detail_results.append(
                SandboxVerificationResult(
                    classification=cls,
                    normalized_status=norm,
                    endpoint_category=ENDPOINT_PROFILE,
                    detail=self._scrub_token(getattr(profile, "message", "")),
                )
            )

        # Reconciliation checks over PRE-EXISTING broker order ids only.
        reconciliation = "NOT_VERIFIED"
        if not self.broker_order_ids:
            audit.append(SandboxVerificationAuditEntry(
                operation_type=ReadOnlyOperationType.ORDER_HISTORY,
                environment=_ENVIRONMENT,
                endpoint_category=ENDPOINT_ORDER_HISTORY,
                request_purpose="read-only reconciliation verification",
                performed_at=self._now(),
                classification=VerificationClassification.UNVERIFIED,
                normalized_status=None,
                audit_id=SandboxVerificationAuditEntry._identity(
                    operation_type=ReadOnlyOperationType.ORDER_HISTORY,
                    environment=_ENVIRONMENT,
                    endpoint_category=ENDPOINT_ORDER_HISTORY,
                    request_purpose="read-only reconciliation verification",
                    performed_at=self._now(),
                    classification=VerificationClassification.UNVERIFIED,
                    normalized_status=None,
                    broker_order_id=None,
                    client_order_id=None,
                    error_code=None,
                    error_category=None,
                    reconciliation_result="",
                    detail="no existing order ids supplied; reconciliation NOT VERIFIED",
                ),
                reconciliation_result="",
                detail="no existing order ids supplied; reconciliation NOT VERIFIED",
            ))
        else:
            outcomes: list[str] = []
            for order_id in sorted(self.broker_order_ids):
                result = self.transport.get_order(tag="", order_id=order_id)
                if isinstance(result, UpstoxClientFailure):
                    cls, norm, code, cat = _map_failure(result)
                    outcomes.append(f"{order_id}:{cls.value}")
                    audit.append(SandboxVerificationAuditEntry(
                        operation_type=ReadOnlyOperationType.ORDER_DETAILS,
                        environment=_ENVIRONMENT,
                        endpoint_category=ENDPOINT_ORDER_DETAILS,
                        request_purpose="read-only order-details lookup (existing order)",
                        performed_at=self._now(),
                        classification=cls,
                        normalized_status=norm,
                        audit_id=SandboxVerificationAuditEntry._identity(
                            operation_type=ReadOnlyOperationType.ORDER_DETAILS,
                            environment=_ENVIRONMENT,
                            endpoint_category=ENDPOINT_ORDER_DETAILS,
                            request_purpose="read-only order-details lookup (existing order)",
                            performed_at=self._now(),
                            classification=cls,
                            normalized_status=norm,
                            broker_order_id=order_id,
                            client_order_id=None,
                            error_code=code,
                            error_category=cat,
                            reconciliation_result="",
                            detail=self._scrub_token(getattr(result, "message", "")),
                        ),
                        broker_order_id=order_id,
                        error_code=code,
                        error_category=cat,
                        detail=self._scrub_token(getattr(result, "message", "")),
                    ))
                    detail_results.append(
                        SandboxVerificationResult(
                            classification=cls,
                            normalized_status=norm,
                            endpoint_category=ENDPOINT_ORDER_DETAILS,
                            detail=self._scrub_token(getattr(result, "message", "")),
                        )
                    )
                else:
                    states = getattr(result, "status", None)
                    from engine.intelligence.upstox_broker_models import UpstoxOrderState
                    raw_state = states.value if hasattr(states, "value") else str(states)
                    normalized = {
                        UpstoxOrderState.COMPLETE.value: BrokerResultStatus.FILLED,
                        UpstoxOrderState.CANCELLED.value: BrokerResultStatus.CANCELLED,
                        UpstoxOrderState.REJECTED.value: BrokerResultStatus.REJECTED,
                        UpstoxOrderState.OPEN.value: BrokerResultStatus.SUBMITTED,
                        UpstoxOrderState.ACCEPTED.value: BrokerResultStatus.ACCEPTED,
                        UpstoxOrderState.PARTIALLY_FILLED.value: BrokerResultStatus.PARTIALLY_FILLED,
                    }.get(raw_state.lower(), BrokerResultStatus.UNKNOWN)
                    classification = (
                        VerificationClassification.SUCCESS
                        if normalized is not BrokerResultStatus.UNKNOWN
                        else VerificationClassification.AMBIGUOUS
                    )
                    outcomes.append(f"{order_id}:{classification.value}->{normalized.value}")
                    audit.append(SandboxVerificationAuditEntry(
                        operation_type=ReadOnlyOperationType.ORDER_DETAILS,
                        environment=_ENVIRONMENT,
                        endpoint_category=ENDPOINT_ORDER_DETAILS,
                        request_purpose="read-only order-details lookup (existing order)",
                        performed_at=self._now(),
                        classification=classification,
                        normalized_status=normalized,
                        audit_id=SandboxVerificationAuditEntry._identity(
                            operation_type=ReadOnlyOperationType.ORDER_DETAILS,
                            environment=_ENVIRONMENT,
                            endpoint_category=ENDPOINT_ORDER_DETAILS,
                            request_purpose="read-only order-details lookup (existing order)",
                            performed_at=self._now(),
                            classification=classification,
                            normalized_status=normalized,
                            broker_order_id=order_id,
                            client_order_id=None,
                            error_code=None if classification is VerificationClassification.SUCCESS else BrokerErrorCode.UNKNOWN_OUTCOME,
                            error_category=None if classification is VerificationClassification.SUCCESS else BrokerErrorCategory.AMBIGUOUS,
                            reconciliation_result="",
                            detail=redact_sensitive(getattr(result, "reason", "") or raw_state),
                        ),
                        broker_order_id=order_id,
                        error_code=None if classification is VerificationClassification.SUCCESS else BrokerErrorCode.UNKNOWN_OUTCOME,
                        error_category=None if classification is VerificationClassification.SUCCESS else BrokerErrorCategory.AMBIGUOUS,
                        detail=redact_sensitive(getattr(result, "reason", "") or raw_state),
                    ))
                    detail_results.append(
                        SandboxVerificationResult(
                            classification=classification,
                            normalized_status=normalized,
                            endpoint_category=ENDPOINT_ORDER_DETAILS,
                            detail=redact_sensitive(getattr(result, "reason", "") or raw_state),
                        )
                    )
            reconciliation = ";".join(outcomes) if outcomes else "NOT_VERIFIED"

        completed_at = self._now()
        profile_broker = ""
        profile_user_type = ""
        profile_exchanges: tuple[str, ...] = ()
        profile_products: tuple[str, ...] = ()
        profile_order_types: tuple[str, ...] = ()
        profile_is_active = False
        profile_user_id_present = False
        if isinstance(profile, UpstoxProfileResponse):
            profile_broker = profile.broker
            profile_user_type = profile.user_type
            profile_exchanges = profile.exchanges
            profile_products = profile.products
            profile_order_types = profile.order_types
            profile_is_active = profile.is_active
            profile_user_id_present = profile.user_id_present

        real_connected = bool(gate_passed and isinstance(profile, UpstoxProfileResponse))

        conclusion_lines = [
            "Read-only sandbox verification completed. ",
        ]
        if real_connected:
            conclusion_lines.append(
                "Real sandbox connectivity ESTABLISHED for read-only "
                "verification (profile identity verified). "
            )
        else:
            conclusion_lines.append(
                "Real sandbox connectivity NOT established (fail closed); "
                "connectivity is NOT VERIFIED for this environment. "
            )
        conclusion_lines.append(
            "Sandbox connectivity and read-only verification do NOT "
            "authorize live trading."
        )

        return SandboxReadOnlyVerification(
            verification_id=SandboxReadOnlyVerification._identity(
                broker=_BROKER_IDENTITY,
                environment=_ENVIRONMENT,
                started_at=started_at,
                completed_at=completed_at,
                token_available=token_ok,
                gate_passed=gate_passed,
                real_sandbox_connected=real_connected,
                audit_entries=tuple(audit),
            ),
            broker=_BROKER_IDENTITY,
            environment=_ENVIRONMENT,
            started_at=started_at,
            completed_at=completed_at,
            token_available=token_ok,
            gate_passed=gate_passed,
            real_sandbox_connected=real_connected,
            profile_broker=profile_broker,
            profile_user_type=profile_user_type,
            profile_exchanges=profile_exchanges,
            profile_products=profile_products,
            profile_order_types=profile_order_types,
            profile_is_active=profile_is_active,
            profile_user_id_present=profile_user_id_present,
            reconciliation_result=reconciliation,
            audit_entries=tuple(audit),
            conclusion="".join(conclusion_lines),
        )


__all__ = [
    "ClockProvider",
    "SandboxReadOnlyVerifier",
    "_map_failure",
    "_default_clock",
]