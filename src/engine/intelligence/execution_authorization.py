"""
Execution Authorization Engine (Checkpoint 15.3).

This module provides the :class:`ExecutionAuthorizationEngine`, a stateless
engine that evaluates whether a specific :class:`OperationalTradeIntent` is
eligible for authorization and can record an explicit authorization decision.

The engine is NOT execution. It terminates at
:class:`~engine.models.execution_authorization.ExecutionAuthorization`.
It does NOT construct execution commands, place orders, contact brokers,
manage positions, or perform any broker-related operations.

Design rules:

* Stateless engine (no mutable state, no caching, no registry).
* Pure delegation to the existing :func:`~engine.models.execution_authorization.create_authorization`
  factory for authorization record construction. The factory is the single
  source of truth for identity computation, timestamp validation, and
  immutable artifact construction.
* The engine is responsible for eligibility evaluation, policy enforcement,
  and the explicit authorization workflow.
* Eligibility and authorization are separate concepts. ``EligibilityResult``
  answers "does this intent satisfy policy conditions?" and
  ``AuthorizationDecision`` answers "has an explicit authorization been
  recorded for this eligible intent?".
* The engine validates the intent; it does NOT become a second planning
  engine. It never recalculates entry, stop, target, quantity, planned risk,
  maximum risk, or risk/reward.
* No ``datetime.now()`` / ``datetime.utcnow()``. The caller supplies the
  evaluation timestamp.
* Fail-closed: any unknown, missing, or contradictory condition must NOT
  produce ``AUTHORIZED``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.operational_trade_intent import OperationalTradeIntent
from engine.models.trade_plan import RiskPlanStatus


# ============================================================
# RESULT TYPES
# ============================================================


class EligibilityResult:
    """Result of an eligibility evaluation for an OperationalTradeIntent.

    Attributes:
        eligible: Whether the intent is eligible for authorization.
        reasons: Tuple of human-readable reasons explaining the verdict.
    """

    def __init__(self, *, eligible: bool, reasons: tuple[str, ...]) -> None:
        self.eligible = eligible
        self.reasons = reasons

    def __repr__(self) -> str:
        status = "ELIGIBLE" if self.eligible else "NOT_ELIGIBLE"
        return f"EligibilityResult({status}, reasons={self.reasons!r})"


class AuthorizationDecision:
    """Result of an explicit authorization request.

    Attributes:
        authorized: Whether the intent was explicitly authorized.
        authorization: The immutable ExecutionAuthorization record, or None.
        reasons: Tuple of human-readable reasons explaining the verdict.
    """

    def __init__(
        self,
        *,
        authorized: bool,
        authorization: ExecutionAuthorization | None,
        reasons: tuple[str, ...],
    ) -> None:
        self.authorized = authorized
        self.authorization = authorization
        self.reasons = reasons

    def __repr__(self) -> str:
        status = "AUTHORIZED" if self.authorized else "NOT_AUTHORIZED"
        auth_id = self.authorization.authorization_id if self.authorization else None
        return (
            f"AuthorizationDecision({status}, "
            f"authorization_id={auth_id!r}, reasons={self.reasons!r})"
        )


# ============================================================
# ENGINE
# ============================================================


class ExecutionAuthorizationEngine:
    """Evaluate eligibility and record explicit authorization decisions.

    The engine is STATELESS across calls. It holds no mutable state, no
    cache, no registry. Repeated calls with the same inputs produce
    equivalent results.

    The engine performs NO market analysis, NO decision logic, NO
    prediction, NO execution, NO paper-trading lifecycle management.
    It ONLY evaluates intent eligibility and delegates authorization
    record construction to the existing factory.

    Responsibilities:

    * Evaluate whether an :class:`OperationalTradeIntent` satisfies the
      objective policy conditions required before authorization can be
      granted (``evaluate_eligibility``).
    * Record an explicit authorization decision for an eligible intent
      (``authorize``).
    * Fail closed on any unknown, missing, or contradictory condition.
    """

    def evaluate_eligibility(
        self,
        intent: OperationalTradeIntent,
        evaluation_timestamp: datetime,
    ) -> EligibilityResult:
        """Evaluate whether an intent is eligible for authorization.

        Eligibility is a SYSTEM determination based on structural validity.
        It does NOT constitute authorization. An eligible intent requires
        an explicit authorization decision to become AUTHORIZED.

        Args:
            intent:
                The :class:`OperationalTradeIntent` to evaluate.
            evaluation_timestamp:
                Timezone-aware timestamp representing "now" for the
                evaluation. The engine NEVER calls ``datetime.now()``.
                The caller supplies this value.

        Returns:
            An :class:`EligibilityResult` indicating whether the intent
            is eligible and any reasons for the verdict.
        """
        reasons: list[str] = []

        # 1. Intent must exist.
        if intent is None:
            return EligibilityResult(eligible=False, reasons=("Intent is missing.",))

        # 2. Intent must be structurally valid (correct type).
        if not isinstance(intent, OperationalTradeIntent):
            return EligibilityResult(
                eligible=False,
                reasons=(
                    f"Expected OperationalTradeIntent; "
                    f"got {type(intent).__name__!r}.",
                ),
            )

        # 3. intent_id must be present and valid.
        if not intent.intent_id or not intent.intent_id.startswith("intent-"):
            reasons.append("intent_id is missing or invalid.")

        # 4. content_fingerprint must be present and valid.
        if (
            not intent.content_fingerprint
            or not intent.content_fingerprint.startswith("fp-")
        ):
            reasons.append("content_fingerprint is missing or invalid.")

        # 5. Required operational fields must be valid.
        if not intent.instrument or not intent.instrument.strip():
            reasons.append("instrument is empty.")
        if intent.direction not in ("LONG", "SHORT"):
            reasons.append(f"direction must be LONG or SHORT; got {intent.direction!r}.")
        if not intent.timeframe or not intent.timeframe.strip():
            reasons.append("timeframe is empty.")

        # 6. Entry must be valid (positive Decimal or None).
        if intent.entry is not None and intent.entry <= 0:
            reasons.append(f"entry must be positive; got {intent.entry!r}.")

        # 7. Stop must be valid (positive Decimal or None).
        if intent.stop is not None and intent.stop <= 0:
            reasons.append(f"stop must be positive; got {intent.stop!r}.")

        # 8. Target must be valid (positive Decimal or None).
        if intent.target_1 is not None and intent.target_1 <= 0:
            reasons.append(f"target_1 must be positive; got {intent.target_1!r}.")

        # 9. Quantity must be valid (positive Decimal or None).
        if intent.quantity is not None and intent.quantity <= 0:
            reasons.append(f"quantity must be positive; got {intent.quantity!r}.")

        # 10. Planned risk must be valid (non-negative Decimal or None).
        if intent.planned_risk is not None and intent.planned_risk < 0:
            reasons.append(
                f"planned_risk must be non-negative; got {intent.planned_risk!r}."
            )

        # 11. Maximum risk must be valid (positive Decimal or None).
        if intent.maximum_risk is not None and intent.maximum_risk <= 0:
            reasons.append(
                f"maximum_risk must be positive; got {intent.maximum_risk!r}."
            )

        # 12. Planned risk must not exceed maximum risk.
        if (
            intent.planned_risk is not None
            and intent.maximum_risk is not None
            and intent.planned_risk > intent.maximum_risk
        ):
            reasons.append(
                f"planned_risk ({intent.planned_risk}) exceeds "
                f"maximum_risk ({intent.maximum_risk})."
            )

        # 13. Risk-plan status must be valid.
        if not isinstance(intent.risk_plan_status, RiskPlanStatus):
            reasons.append("risk_plan_status is not a valid RiskPlanStatus.")
        elif not intent.risk_plan_status.is_valid:
            reasons.append(
                f"risk_plan_status is {intent.risk_plan_status.value}; "
                f"must be VALID."
            )

        # 14. Intent must not have expired at the evaluation timestamp.
        if intent.valid_until is not None:
            try:
                is_expired = evaluation_timestamp >= intent.valid_until
            except TypeError:
                is_expired = True
            if is_expired:
                reasons.append(
                    f"Intent expired at {evaluation_timestamp.isoformat()}; "
                    f"valid_until is {intent.valid_until.isoformat()}."
                )

        # 15. Evaluation timestamp must be timezone-aware.
        if evaluation_timestamp.tzinfo is None:
            reasons.append("evaluation_timestamp must be timezone-aware.")

        if reasons:
            return EligibilityResult(eligible=False, reasons=tuple(reasons))

        return EligibilityResult(eligible=True, reasons=())

    def authorize(
        self,
        intent: OperationalTradeIntent,
        evaluation_timestamp: datetime,
        *,
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
    ) -> AuthorizationDecision:
        """Record an explicit authorization decision for an intent.

        Authorization is an explicit operation. Eligibility alone does NOT
        create an AUTHORIZED record. The caller must deliberately request
        authorization by supplying explicit authorization inputs.

        The workflow is:

        1. Evaluate eligibility (system policy check).
        2. If eligible, delegate to the ``create_authorization`` factory
           to construct the immutable authorization record.

        Args:
            intent:
                The :class:`OperationalTradeIntent` to authorize.
            evaluation_timestamp:
                Timezone-aware timestamp for eligibility evaluation.
                The engine NEVER calls ``datetime.now()``.
            authorized_at:
                Timezone-aware timestamp when authorization was granted.
                Caller-supplied.
            valid_from:
                Timezone-aware timestamp when authorization becomes
                effective. Must satisfy ``valid_from >= authorized_at``.
            expires_at:
                Timezone-aware timestamp when authorization expires.
                Must satisfy ``expires_at > valid_from`` and, when the
                intent carries a ``valid_until``, ``expires_at <=
                intent.valid_until``.
            issuer:
                Who/what granted the authorization. Must be non-empty.
            authorization_method:
                How the authorization was granted. Must be non-empty.
            scope:
                What is permitted under this authorization. Must be
                non-empty.
            policy_reference:
                Which policy version or rule set was applied. Must be
                non-empty.
            safety_check_summary:
                Which safety gates were evaluated and passed. Must be
                non-empty.
            label:
                Optional caller-supplied identity label.
            metadata:
                Optional caller-supplied metadata tuple (sorted pairs).

        Returns:
            An :class:`AuthorizationDecision` indicating whether the
            intent was authorized and any reasons for the verdict.
        """
        reasons: list[str] = []

        # Step 1: Evaluate eligibility.
        eligibility = self.evaluate_eligibility(intent, evaluation_timestamp)
        if not eligibility.eligible:
            return AuthorizationDecision(
                authorized=False,
                authorization=None,
                reasons=tuple(eligibility.reasons),
            )

        # Step 2: Validate explicit authorization inputs.
        input_errors: list[str] = []
        if not issuer or not issuer.strip():
            input_errors.append("issuer must be non-empty.")
        if not authorization_method or not authorization_method.strip():
            input_errors.append("authorization_method must be non-empty.")
        if not scope or not scope.strip():
            input_errors.append("scope must be non-empty.")
        if not policy_reference or not policy_reference.strip():
            input_errors.append("policy_reference must be non-empty.")
        if not safety_check_summary or not safety_check_summary.strip():
            input_errors.append("safety_check_summary must be non-empty.")

        if input_errors:
            return AuthorizationDecision(
                authorized=False,
                authorization=None,
                reasons=tuple(input_errors),
            )

        # Step 3: Delegate to the authoritative factory.
        try:
            authorization = create_authorization(
                intent=intent,
                status=AuthorizationStatus.AUTHORIZED,
                authorized_at=authorized_at,
                valid_from=valid_from,
                expires_at=expires_at,
                issuer=issuer,
                authorization_method=authorization_method,
                scope=scope,
                policy_reference=policy_reference,
                safety_check_summary=safety_check_summary,
                label=label,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            return AuthorizationDecision(
                authorized=False,
                authorization=None,
                reasons=(f"Authorization creation failed: {exc}",),
            )

        return AuthorizationDecision(
            authorized=True,
            authorization=authorization,
            reasons=(),
        )


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "AuthorizationDecision",
    "EligibilityResult",
    "ExecutionAuthorizationEngine",
]
