"""Live execution gate design (Checkpoint 17.9).

This module defines the ARCHITECTURE for a future live-execution gate. It is
a DESIGN + TEST artifact ONLY. It is NOT wired into any submission path, is
NOT enabled, and CANNOT by itself permit live trading.

CRITICAL SAFETY STATEMENT (Checkpoint 17.9):

* LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.9.
* CHECKPOINT 17.9 DOES NOT AUTHORIZE REAL-MONEY ORDER SUBMISSION.
* The gate is a deterministic, fail-closed EVALUATION function. It returns
  ``ALLOWED`` only when EVERY mandatory condition is simultaneously
  satisfied. If ANY mandatory condition is missing or unverifiable, it
  returns ``NOT_ALLOWED`` with the specific blocking reasons.
* The gate is NOT equivalent to ``if credential_exists: allow_live()``.
  Credentials are necessary but NOT sufficient for live execution.
* The gate is DISABLED by default. There is no ``live_enabled = True``
  shortcut anywhere in the repository.
* This module imports NO network libraries, NO broker SDK, NO credentials,
  and NO core execution models. It is a pure, stateless, deterministic
  decision function over a caller-supplied condition snapshot.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* ``LiveExecutionGate.evaluate`` is pure and deterministic: identical input
  always produces the identical verdict.
* The gate NEVER mutates any input, NEVER calls a broker, NEVER reads
  credentials, and NEVER submits anything.
* Every mandatory condition is independently testable and independently
  reported. A single missing condition blocks the whole verdict.
* The verdict carries the full set of blocking reasons (auditable), the
  satisfied condition count, and a deterministic verdict id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Prefix for deterministic gate verdict ids.
GATE_VERDICT_PREFIX = "gate-"

#: Length of the SHA-256 digest prefix used in verdict ids.
_ID_DIGEST_LENGTH = 16


# ============================================================
# GATE STATE
# ============================================================


class LiveExecutionGateState(Enum):
    """The future live-execution gate state machine (Phase 48).

    DISABLED
        The gate is not configured / not enabled. This is the DEFAULT and
        the only safe resting state for the current architecture.
    CONFIGURED
        The gate has been configured (broker, adapter, environment,
        credential provenance declared) but no precheck has run.
    PRECHECK
        Startup / readiness prechecks are in progress.
    AUTHORIZED
        The explicit live-execution authorization has been recorded.
    GATE_VERIFIED
        Every mandatory gate condition has been verified simultaneously.
    READY
        The gate is verified and ready for an explicitly authorized
        submission attempt.
    BLOCKED
        Any failure / missing condition / safety override moved the gate
        to BLOCKED. BLOCKED is absorbing for the current attempt.

    The progression is DISABLED -> CONFIGURED -> PRECHECK -> AUTHORIZED ->
    GATE_VERIFIED -> READY. Any failure -> BLOCKED. This module only defines
    the state vocabulary; it does NOT implement a stateful gate object (the
    evaluation function below is stateless and deterministic).
    """

    DISABLED = "DISABLED"
    CONFIGURED = "CONFIGURED"
    PRECHECK = "PRECHECK"
    AUTHORIZED = "AUTHORIZED"
    GATE_VERIFIED = "GATE_VERIFIED"
    READY = "READY"
    BLOCKED = "BLOCKED"


# ============================================================
# GATE VERDICT
# ============================================================


class GateVerdict(Enum):
    """The deterministic gate verdict.

    ALLOWED
        EVERY mandatory condition is simultaneously satisfied and the gate
        is explicitly enabled + explicitly authorized. This verdict is
        returned ONLY by the positive matrix; it is never the default.
    NOT_ALLOWED
        At least one mandatory condition is missing / false / unverifiable.
        This is the fail-closed default.
    """

    ALLOWED = "ALLOWED"
    NOT_ALLOWED = "NOT_ALLOWED"


# ============================================================
# MANDATORY CONDITIONS (Phase 5)
# ============================================================


#: The 20 mandatory gate conditions, in canonical order. Each condition is a
#: single, independently-testable boolean predicate supplied by the caller.
#: The gate fails closed if ANY is False.
MANDATORY_GATE_CONDITIONS: tuple[str, ...] = (
    "explicit_live_mode",            # 1. explicit live mode
    "correct_broker",                # 2. correct broker
    "correct_adapter",              # 3. correct adapter
    "valid_live_credential",        # 4. valid live credential
    "credential_provenance",        # 5. credential provenance verified
    "authorization_state",          # 6. authorization state AUTHORIZED
    "command_validity",             # 7. command validity
    "risk_quantity_constraints",    # 8. risk/quantity constraints
    "capability_support",           # 9. capability support
    "environment_identity",         # 10. environment identity
    "operator_explicit_authorization",  # 11. operator/user explicit authorization
    "execution_gate_enabled",       # 12. execution gate enabled
    "startup_safety_checks",        # 13. startup safety checks
    "broker_health_readiness",      # 14. broker health/readiness
    "reconciliation_readiness",     # 15. reconciliation readiness
    "audit_readiness",              # 16. audit readiness
    "no_outstanding_unknown",       # 17. no outstanding UNKNOWN affecting execution
    "no_conflicting_recovery",      # 18. no conflicting recovery state
    "no_configuration_ambiguity",   # 19. no configuration ambiguity
    "no_safety_override_active",    # 20. no safety override active
)


# ============================================================
# INPUT SNAPSHOT
# ============================================================


@dataclass(frozen=True, slots=True)
class LiveExecutionGateInput:
    """A caller-supplied snapshot of the conditions the gate must evaluate.

    Every field is a plain boolean (or an explicit None sentinel meaning
    "unverifiable"). The gate NEVER inspects credentials, commands, brokers,
    or authorization objects directly: the caller reduces those to booleans
    through the existing fail-closed validation layers. This keeps the gate
    a pure, broker-neutral, credential-free decision function.

    Attributes:
        conditions:
            Mapping of condition name -> bool. Missing keys are treated as
            False (fail closed). Unknown keys are ignored (documented).
        gate_enabled:
            Explicitly True only when the gate itself is enabled. Default
            False. There is no implicit enablement.
        explicit_operator_authorization:
            Explicitly True only when a recorded, time-bounded, explicit
            operator authorization exists for this specific intent. Default
            False. Never inferred from env vars / credentials / config.
        label / metadata:
            Optional caller-supplied audit fields.
    """

    conditions: tuple[tuple[str, bool], ...] = ()
    gate_enabled: bool = False
    explicit_operator_authorization: bool = False
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        normalized: list[tuple[str, bool]] = []
        for name, value in self.conditions:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("condition names must be non-empty strings.")
            if not isinstance(value, bool):
                raise TypeError(
                    f"condition {name!r} must be a bool; "
                    f"got {type(value).__name__!r}."
                )
            normalized.append((name, value))
        normalized.sort()
        object.__setattr__(self, "conditions", tuple(normalized))
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata)))

    def condition(self, name: str) -> bool:
        """Return the condition value (False when missing / unknown)."""
        for key, value in self.conditions:
            if key == name:
                return value
        return False

    def satisfied_count(self) -> int:
        """Count of satisfied mandatory conditions."""
        return sum(
            1 for name in MANDATORY_GATE_CONDITIONS if self.condition(name)
        )


# ============================================================
# VERDICT
# ============================================================


@dataclass(frozen=True, slots=True)
class LiveExecutionGateVerdict:
    """The deterministic gate verdict.

    Attributes:
        verdict:
            :class:`GateVerdict` (ALLOWED / NOT_ALLOWED).
        blocking_reasons:
            Sorted tuple of the mandatory conditions that were False /
            missing (auditable). Empty only when ALLOWED.
        satisfied_count:
            Number of satisfied mandatory conditions (out of
            len(MANDATORY_GATE_CONDITIONS)).
        mandatory_count:
            Total number of mandatory conditions.
        gate_enabled / explicit_operator_authorization:
            Echoed from the input for auditability.
        verdict_id:
            Deterministic ``gate-`` + sha256[:16] of the canonical input +
            verdict.
        rationale:
            Descriptive broker-neutral rationale.
    """

    verdict: GateVerdict
    blocking_reasons: tuple[str, ...]
    satisfied_count: int
    mandatory_count: int
    gate_enabled: bool
    explicit_operator_authorization: bool
    verdict_id: str
    rationale: str

    @property
    def is_allowed(self) -> bool:
        return self.verdict is GateVerdict.ALLOWED

    @property
    def is_blocked(self) -> bool:
        return self.verdict is GateVerdict.NOT_ALLOWED

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON-safe projection."""
        return {
            "verdict": self.verdict.value,
            "blocking_reasons": list(self.blocking_reasons),
            "satisfied_count": int(self.satisfied_count),
            "mandatory_count": int(self.mandatory_count),
            "gate_enabled": bool(self.gate_enabled),
            "explicit_operator_authorization": bool(
                self.explicit_operator_authorization
            ),
            "verdict_id": self.verdict_id,
            "rationale": self.rationale,
        }


def _verdict_id(
    conditions: tuple[tuple[str, bool], ...],
    gate_enabled: bool,
    explicit_operator_authorization: bool,
    verdict: GateVerdict,
) -> str:
    """Deterministic verdict identity (no wall-clock, no randomness)."""

    payload = {
        "conditions": sorted(conditions),
        "gate_enabled": bool(gate_enabled),
        "explicit_operator_authorization": bool(explicit_operator_authorization),
        "verdict": verdict.value,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{GATE_VERDICT_PREFIX}{digest[:_ID_DIGEST_LENGTH]}"


# ============================================================
# GATE
# ============================================================


class LiveExecutionGate:
    """Deterministic, stateless, fail-closed live-execution gate (DESIGN).

    The gate is a PURE EVALUATION FUNCTION. It holds no state, calls no
    broker, reads no credentials, and submits nothing. It is NOT wired into
    any submission path in Checkpoint 17.9.

    The positive matrix (Phase 31) requires ALL mandatory conditions
    simultaneously:

    * every mandatory condition True,
    * the gate explicitly enabled,
    * an explicit operator authorization recorded.

    If any mandatory condition is False/missing, OR the gate is not enabled,
    OR no explicit operator authorization exists, the verdict is
    NOT_ALLOWED (fail closed). There is no shortcut and no implicit
    enablement.
    """

    def evaluate(
        self,
        gate_input: LiveExecutionGateInput,
    ) -> LiveExecutionGateVerdict:
        """Evaluate the gate against a caller-supplied condition snapshot.

        Returns:
            :class:`LiveExecutionGateVerdict` -- ALLOWED only when every
            mandatory condition is True AND the gate is explicitly enabled
            AND an explicit operator authorization is recorded; otherwise
            NOT_ALLOWED with the blocking reasons.
        """

        if not isinstance(gate_input, LiveExecutionGateInput):
            raise TypeError(
                f"gate_input must be a LiveExecutionGateInput; "
                f"got {type(gate_input).__name__!r}."
            )

        blocking: list[str] = []
        for name in MANDATORY_GATE_CONDITIONS:
            if not gate_input.condition(name):
                blocking.append(name)

        if not gate_input.gate_enabled:
            blocking.append("execution_gate_enabled")
        if not gate_input.explicit_operator_authorization:
            blocking.append("operator_explicit_authorization")

        satisfied = gate_input.satisfied_count()
        mandatory_count = len(MANDATORY_GATE_CONDITIONS)

        if not blocking:
            verdict = GateVerdict.ALLOWED
            rationale = (
                "Every mandatory live-execution gate condition is "
                "simultaneously satisfied, the gate is explicitly enabled, "
                "and an explicit operator authorization is recorded. "
                "This verdict is a gate-architecture test result; it does "
                "NOT by itself authorize live trading."
            )
        else:
            verdict = GateVerdict.NOT_ALLOWED
            rationale = (
                "The live-execution gate is fail-closed: "
                f"{len(blocking)} mandatory condition(s) are missing or "
                "unverifiable, or the gate is not explicitly enabled, or no "
                "explicit operator authorization is recorded. "
                "Credentials are necessary but NOT sufficient for live "
                "execution."
            )

        return LiveExecutionGateVerdict(
            verdict=verdict,
            blocking_reasons=tuple(sorted(set(blocking))),
            satisfied_count=satisfied,
            mandatory_count=mandatory_count,
            gate_enabled=bool(gate_input.gate_enabled),
            explicit_operator_authorization=bool(
                gate_input.explicit_operator_authorization
            ),
            verdict_id=_verdict_id(
                gate_input.conditions,
                gate_input.gate_enabled,
                gate_input.explicit_operator_authorization,
                verdict,
            ),
            rationale=rationale,
        )


# ============================================================
# PUBLIC SURFACE
# ============================================================


__all__ = [
    "GATE_VERDICT_PREFIX",
    "GateVerdict",
    "LiveExecutionGate",
    "LiveExecutionGateInput",
    "LiveExecutionGateState",
    "LiveExecutionGateVerdict",
    "MANDATORY_GATE_CONDITIONS",
]
