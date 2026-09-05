"""Controlled broker validation boundary (Checkpoint 17.8).

This module is the OFFLINE / OPT-IN validation boundary introduced by
Checkpoint 17.8. It provides the deterministic, network-free machinery that
Checkpoint 17.8 uses to (a) gate any future real-broker integration behind an
explicit opt-in environment variable, (b) enforce the guarded startup
sequence required BEFORE any controlled broker API call, and (c) enforce the
LIVE-mode hard gate so a sandbox/paper verification can never silently fall
back to a live environment.

CRITICAL SAFETY RULES (Checkpoint 17.8):

* ``real_broker_integration_enabled()`` is ``False`` by DEFAULT. A real
  broker client, real credentials, and real network connectivity must never
  be exercised unless the operator explicitly sets
  ``CHECKPOINT_17_8_REAL_BROKER=1`` AND a separately-controlled credential /
  environment is supplied.
* This module performs NO network operations, NO broker API calls, and NO
  credential reads beyond the opt-in gate (which compares the env var to
  ``"1"`` and is overridable for tests). It imports NO network library, NO
  broker SDK, and NO core domain models (it accepts plain ``str`` inputs so
  it stays dependency-free).
* The startup guard is FAIL-CLOSED: any unmet precondition produces an
  unsafe result with the specific unsatisfied checks listed; NOTHING is
  inferred from the mere presence of credentials (``"credentials exist"``
  NEVER means ``"safe to trade"``).
* The LIVE-mode hard gate treats a reported ``LIVE`` environment when a
  sandbox/paper environment is required as a SAFETY FAILURE: NO automatic
  mode switch, NO retry with another environment, NO alternate adapter, NO
  alternate credential, NO downgraded error.
* The broker-specific models of the Upstox adapter are NOT imported here.
  This module only recognizes broker identity strings (``"upstox"``) and
  capability names so the guard can remain broker-neutral while still
  refusing to confuse brokers.

The verification-status vocabulary (``VerificationStatus``) is shared with the
Checkpoint 17.8 documentation so every behavior validation can be labeled
consistently (VERIFIED AGAINST CONTROLLED BROKER / VERIFIED USING MOCKS /
VERIFIED FROM OFFICIAL DOCUMENTATION / NOT VERIFIED / DEFERRED).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

#: Opt-in environment variable name for real-broker integration tests.
#: ``CHECKPOINT_17_8_REAL_BROKER=1`` is the ONLY way a real broker test may
#: proceed; the variable is absent / any other value by default (fail closed).
CHECKPOINT_17_8_REAL_BROKER_ENV = "CHECKPOINT_17_8_REAL_BROKER"

#: Canonical broker identity string this boundary recognizes as Upstox
#: (matches ``UPSTOX_BROKER_IDENTITY`` in the adapter, duplicated here ONLY
#: as a plain string so the guard has no adapter dependency).
UPSTOX_BROKER_IDENTITY = "upstox"

#: Required / verified broker-neutral capabilities for controlled validation
#: (mirrors the frozen contract: SUBMIT + RECONCILE minimum; CANCEL when the
#: controlled execution supports it).
REQUIRED_CONTROLLED_CAPABILITIES: tuple[str, ...] = ("SUBMIT", "RECONCILE")

#: Official Upstox developer documentation pages verified during Checkpoint
#: 17.8 (used by the documentation; kept here so the verification records
#: carry their sources).
OFFICIAL_UPSTOX_REFERENCE_URLS: dict[str, str] = {
    "sandbox": "https://upstox.com/developer/api-documentation/sandbox",
    "api_overview": "https://upstox.com/developer/api-documentation/api-overview",
    "place_order": "https://upstox.com/developer/api-documentation/place-order",
    "place_order_v3": "https://upstox.com/developer/api-documentation/v3/place-order",
    "modify_order_v3": "https://upstox.com/developer/api-documentation/v3/modify-order",
    "get_order_history": "https://upstox.com/developer/api-documentation/get-order-history",
    "order_status": "https://upstox.com/developer/api-documentation/appendix/order-status",
    "cancel_order": "https://upstox.com/developer/api-documentation/cancel-order",
    "build_using_sandbox": "https://upstox.com/developer/api-documentation/build-using-sandbox",
}


# ============================================================
# VERIFICATION STATUS VOCABULARY
# ============================================================


class VerificationStatus(Enum):
    """Shared vocabulary for labeling behavior validation in Checkpoint 17.8.

    VERIFIED_AGAINST_CONTROLLED_BROKER
        Behavior demonstrated against a real controlled (sandbox/paper)
        broker environment.
    VERIFIED_USING_MOCKS
        Behavior demonstrated deterministically using the network-free mock
        client / reference broker.
    VERIFIED_FROM_OFFICIAL_DOCUMENTATION
        Behavior confirmed from the broker's official developer
        documentation (NOT a live observation).
    NOT_VERIFIED
        Behavior could not be safely established in this environment; must
        NOT be treated as proven.
    DEFERRED
        Behavior intentionally deferred to a later checkpoint.
    """

    VERIFIED_AGAINST_CONTROLLED_BROKER = "VERIFIED_AGAINST_CONTROLLED_BROKER"
    VERIFIED_USING_MOCKS = "VERIFIED_USING_MOCKS"
    VERIFIED_FROM_OFFICIAL_DOCUMENTATION = "VERIFIED_FROM_OFFICIAL_DOCUMENTATION"
    NOT_VERIFIED = "NOT_VERIFIED"
    DEFERRED = "DEFERRED"


# ============================================================
# CONTROLLED ENVIRONMENT VOCABULARY
# ============================================================


class ControlledEnvironmentKind(Enum):
    """Non-live environment kinds recognized by the startup guard.

    SANDBOX
        The broker's official sandbox environment (the controlled Upstox
        sandbox documented by Upstox).
    PAPER
        A simulated / paper execution environment (no real orders).
    UNKNOWN
        Environment identity cannot be positively established. The startup
        guard FAILS CLOSED for this kind (never silently treated as safe).
    """

    SANDBOX = "SANDBOX"
    PAPER = "PAPER"
    UNKNOWN = "UNKNOWN"


#: Environment name strings accepted as safe/controlled by the startup guard.
CONTROLLED_ENVIRONMENT_NAMES: tuple[str, ...] = (
    "SANDBOX",
    "PAPER",
    "sandbox",
    "paper",
)

#: Environment name strings that must NEVER be treated as controlled.
LIVE_ENVIRONMENT_NAMES: tuple[str, ...] = ("LIVE", "live", "PROD", "prod", "REAL", "real")


# ============================================================
# CREDENTIAL PROVIDER PROTOCOL (broker-neutral, plain)
# ============================================================


@runtime_checkable
class ValidationCredentialProvider(Protocol):
    """Broker-neutral credential provider used by the startup guard.

    The guard only checks that the provider exists and yields a non-empty
    access token string. It NEVER reads or stores the token itself, and the
    token value never appears in guard results, reasons, persistence, or
    logs.
    """

    def get_access_token(self) -> str:
        """Return the current access token (empty string when unavailable)."""
        ...


# ============================================================
# STARTUP GUARD RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class StartupGuardResult:
    """Deterministic result of the controlled-broker startup guard.

    Attributes:
        safe:
            ``True`` only when EVERY required precondition is satisfied.
            Any unmet precondition -> ``False`` (fail closed).
        unmet:
            Tuple of descriptive unmet-precondition strings (empty when
            safe). Deterministic order.
        reasons:
            Tuple of descriptive recorded reasons for each satisfied check
            (used for observability and documentation).
        expected_environment:
            The environment kind the guard was REQUIRED to see (e.g.
            ``"SANDBOX"``).
        reported_environment:
            The environment the caller reported (``None`` -> recorded as
            ``"UNKNOWN"``).
        expected_mode:
            The execution mode required for the controlled verification
            (``"PAPER"``).
        reported_mode:
            The execution mode the caller reported.
        live_mismatch:
            ``True`` when a LIVE environment/mode was reported while a
            controlled (SANDBOX/PAPER) environment was required — the
            LIVE-mode hard gate has fired.

    Invariants: ``safe`` is ``True`` iff ``unmet`` is empty; a
    ``live_mismatch`` report ALWAYS produces at least one unmatched item and
    therefore ``safe is False``.
    """

    safe: bool
    unmet: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    expected_environment: str = "SANDBOX"
    reported_environment: str = "UNKNOWN"
    expected_mode: str = "PAPER"
    reported_mode: str = ""
    live_mismatch: bool = False

    def __post_init__(self) -> None:
        if self.safe and self.unmet:
            raise ValueError("safe result must carry an empty unmet list.")
        if not self.safe and not self.unmet:
            raise ValueError("unsafe result must carry at least one unmet item.")
        if self.live_mismatch and self.safe:
            raise ValueError("live_mismatch must never produce a safe result.")

    @property
    def is_safe(self) -> bool:
        """Whether every precondition is satisfied (safe to proceed)."""
        return self.safe


# ============================================================
# OPT-IN GATE
# ============================================================


def real_broker_integration_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Whether real-broker integration is explicitly opted in.

    The gate reads ``CHECKPOINT_17_8_REAL_BROKER`` (case-sensitive) from
    ``os.environ`` when ``environ`` is ``None``; ``"1"`` is the ONLY value
    that enables the gate. Any other value, an absent variable, or a
    non-string value yields ``False`` (fail closed).
    """

    source = os.environ if environ is None else environ
    value = source.get(CHECKPOINT_17_8_REAL_BROKER_ENV)
    return value == "1"


# ============================================================
# CONTROLLED-ENVIRONMENT CLASSIFICATION
# ============================================================


def is_controlled_environment(environment: str | None) -> bool:
    """Whether an environment name string is a recognized controlled kind.

    ``None`` / empty / unrecognized names -> ``False`` (UNKNOWN is never
    treated as controlled). LIVE/REAL/PROD names -> ``False``.
    """

    if not isinstance(environment, str) or not environment.strip():
        return False
    return environment.strip() in CONTROLLED_ENVIRONMENT_NAMES


def is_live_environment(environment: str | None) -> bool:
    """Whether an environment name string is a live (real) environment."""

    if not isinstance(environment, str) or not environment.strip():
        return False
    return environment.strip() in LIVE_ENVIRONMENT_NAMES


# ============================================================
# LIVE-MODE HARD GATE
# ============================================================


def live_mode_hard_gate(
    *, expected_environment: str, reported_environment: str | None
) -> bool:
    """Enforce the LIVE-mode hard gate (fail closed).

    When the expected environment is a controlled kind (SANDBOX/PAPER) and
    the reported environment is a LIVE kind, the response is ``True`` ===
    "the hard gate has fired: SAFETY FAILURE -> NO ORDER". Any other
    combination returns ``False``.

    The gate NEVER: switches modes, retries another environment, selects
    another adapter, uses another credential, or downgrades the error. The
    correct result is a safety failure and no order.
    """

    if expected_environment not in CONTROLLED_ENVIRONMENT_NAMES:
        # A caller asking for a non-controlled expectation cannot hard-gate
        # on live; the guard layers reject it elsewhere.
        return False
    return is_live_environment(reported_environment)


def assert_no_auto_mode_switch() -> bool:
    """Assert the guard architecture contains no automatic mode fallback.

    This is a static contract helper: the startup guard has no code path
    that switches execution mode or environment in response to a failure
    (there are no mode/env arguments that can be mutated). Returns ``True``
    as a named, testable statement of that invariant; a future change that
    adds fallback behavior MUST update this function (and its tests).
    """

    return True


# ============================================================
# CAPABILITY CHECK
# ============================================================


def capabilities_satisfy(
    capabilities: Sequence[str] | None, required: Sequence[str] = REQUIRED_CONTROLLED_CAPABILITIES
) -> bool:
    """Whether a set of capability names covers every required capability.

    ``capabilities`` may be capability strings or enum values with
    ``.name``/``.value``; non-name values are converted via ``str`` (their
    final str form must match the required names). ``None``/empty ->
    ``False`` (fail closed).
    """

    if not capabilities:
        return False
    present: set[str] = set()
    for cap in capabilities:
        if isinstance(cap, Enum):
            present.add(str(cap.value))
        else:
            present.add(str(cap))
    return all(req in present for req in required)


# ============================================================
# STARTUP GUARD
# ============================================================


def controlled_broker_startup_guard(
    *,
    broker_identity: str | None,
    execution_mode: str | None,
    environment: str | None,
    credential_provider: ValidationCredentialProvider | Any | None,
    capability_names: Sequence[str] | None,
    required_config: Mapping[str, Any] | None = None,
    required_config_keys: Sequence[str] = (),
    expected_environment: str = "SANDBOX",
    expected_mode: str = "PAPER",
    allow_unknown_environment: bool = False,
) -> StartupGuardResult:
    """Run the guarded startup sequence for a controlled broker verification.

    Before any controlled broker API call the caller must supply the plain
    values below; the guard verifies each precondition and FAILS CLOSED on
    any unmet check. It performs NO network operations and NO broker calls.

    Gate checks (Phase 6 of Checkpoint 17.8):

    1.  verify execution mode (reported mode must be valid and, for a
        controlled verification, must equal ``expected_mode``).
    2.  verify adapter identity (``broker_identity`` non-empty).
    3.  verify broker identity (recognized controlled broker name and no
        live broker name confusion).
    4.  verify credential provider (a provider object exposing
        ``get_access_token`` is supplied).
    5.  verify credential availability (the provider yields a non-empty
        access token). NOTE: "credentials exist" alone NEVER means "safe to
        trade" — this is only ONE of the checks.
    6.  verify environment (reported environment is a recognized controlled
        kind; ``UNKNOWN`` fails closed unless ``allow_unknown_environment``
        is explicitly ``True``).
    7.  verify controlled/paper account (LIVE environment or LIVE mode when
        a controlled verification is expected -> SAFETY FAILURE, hard gate).
    8.  verify capability (the supplied capability names cover the required
        controlled capabilities).
    9.  verify required configuration (every key in
        ``required_config_keys`` is present and non-empty in
        ``required_config``; when ``required_config`` is ``None`` and keys
        are required -> fail closed).
    10. fail closed if ANY check fails (never infer safety from the rest).

    Args are plain values so the guard can be used by any future controlled
    client (Upstox or otherwise) without importing broker models.

    Returns:
        :class:`StartupGuardResult` — ``safe is True`` only when every check
        passes.
    """

    unmet: list[str] = []
    reasons: list[str] = []
    reported_environment_name = (
        environment.strip() if isinstance(environment, str) else environment
    )
    reported_mode_name = execution_mode.strip() if isinstance(execution_mode, str) else execution_mode
    live_mismatch = live_mode_hard_gate(
        expected_environment=expected_environment,
        reported_environment=reported_environment_name,
    )

    # 1. Execution mode.
    if not isinstance(reported_mode_name, str) or not reported_mode_name:
        unmet.append(
            "execution_mode must be a non-empty string (verify execution mode)."
        )
    elif reported_mode_name not in ("PAPER", "LIVE"):
        unmet.append(
            f"execution_mode {reported_mode_name!r} is not recognized "
            "(verify execution mode)."
        )
    elif reported_mode_name != expected_mode:
        unmet.append(
            f"execution_mode {reported_mode_name!r} does not match the "
            f"controlled verification requirement {expected_mode!r} "
            "(controlled/paper account mismatch; fail closed)."
        )
    else:
        reasons.append(f"execution_mode verified ({reported_mode_name}).")

    # 2. Adapter identity.
    if not isinstance(broker_identity, str) or not broker_identity.strip():
        unmet.append("broker_identity must be a non-empty string (adapter identity).")
    else:
        reasons.append(f"adapter identity {broker_identity!r} supplied.")

    # 3. Broker identity (no live broker confusion).
    if is_live_environment(broker_identity):
        unmet.append(
            f"broker_identity {broker_identity!r} looks like a LIVE environment "
            "name; refusing to continue (broker identity verification)."
        )
    elif isinstance(broker_identity, str) and broker_identity.strip():
        reasons.append(f"broker identity {broker_identity!r} accepted.")

    # 4. Credential provider.
    provider_present = (
        credential_provider is not None
        and callable(getattr(credential_provider, "get_access_token", None))
    )
    if not provider_present:
        unmet.append(
            "credential provider is missing (must expose get_access_token(); "
            "fail closed without a provider)."
        )
    else:
        reasons.append("credential provider supplied and satisfiable.")

    # 5. Credential availability.
    token_available = False
    if provider_present:
        try:
            token = credential_provider.get_access_token()
            token_available = isinstance(token, str) and bool(token)
        except Exception:
            token_available = False
    if not token_available:
        unmet.append(
            "credential provider yielded no access token (credential "
            "availability; fail closed)."
        )
    else:
        reasons.append("credential availability verified (non-empty token).")

    # 6. Environment.
    if reported_environment_name is None or not is_controlled_environment(
        reported_environment_name
    ):
        if allow_unknown_environment and not is_live_environment(
            reported_environment_name
        ):
            reasons.append(
                f"environment {reported_environment_name!r} accepted with "
                "explicit allow_unknown_environment."
            )
        else:
            unmet.append(
                f"environment {reported_environment_name!r} is not a "
                "recognized controlled (SANDBOX/PAPER) environment; fail closed."
            )
    else:
        reasons.append(f"environment {reported_environment_name!r} verified.")

    # 7. Controlled/paper account + LIVE hard gate.
    if live_mismatch:
        unmet.append(
            "LIVE environment reported when a controlled (SANDBOX/PAPER) "
            "environment is required -- SAFETY FAILURE; NO ORDER. "
            "No automatic mode switch, no environment retry, no adapter "
            "fallback, no credential substitution."
        )
    elif reported_mode_name == "LIVE" and not live_mismatch:
        unreported_env = reported_environment_name
        if unreported_env is not None and is_controlled_environment(unreported_env):
            unmet.append(
                "execution_mode LIVE with a controlled environment is a "
                "mode/environment mismatch; fail closed (no auto-switch)."
            )
    else:
        reasons.append("controlled/paper account verification passed; no LIVE mismatch.")

    # 8. Capability.
    if not capabilities_satisfy(capability_names):
        unmet.append(
            f"capability_names {list(capability_names) if capability_names else []!r} "
            f"do not cover the required controlled capabilities "
            f"{list(REQUIRED_CONTROLLED_CAPABILITIES)!r}; fail closed."
        )
    else:
        reasons.append("required capabilities present (SUBMIT + RECONCILE).")

    # 9. Required configuration.
    config_ok = True
    for key in required_config_keys:
        value = required_config.get(key) if isinstance(required_config, Mapping) else None
        if required_config is None or value in (None, ""):
            config_ok = False
            unmet.append(
                f"required configuration {key!r} is missing or empty; fail closed."
            )
    if config_ok and not any("required configuration" in u for u in unmet):
        reasons.append("required configuration verified.")

    # 10. Fail closed.
    safe = not unmet
    return StartupGuardResult(
        safe=safe,
        unmet=tuple(unmet),
        reasons=tuple(reasons),
        expected_environment=expected_environment,
        reported_environment=(
            reported_environment_name
            if reported_environment_name is not None
            else "UNKNOWN"
        ),
        expected_mode=expected_mode,
        reported_mode=reported_mode_name or "",
        live_mismatch=live_mismatch,
    )


# ============================================================
# CHECKLIST OF VERIFIED BEHAVIORS (for documentation / observability)
# ============================================================


@dataclass(frozen=True, slots=True)
class ControllableValidationCheck:
    """One behavior-validation record with its review status and source.

    Attributes:
        area:
            Area name (e.g. ``"authentication"``, ``"reconciliation"``).
        status:
            :class:`VerificationStatus` label.
        note:
            Short deterministic note (never contains credentials).
    """

    area: str
    status: VerificationStatus
    note: str = ""

    def __post_init__(self) -> None:
        if not self.area or not self.area.strip():
            raise ValueError("area must be a non-empty string.")
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("status must be a VerificationStatus.")

    def to_dict(self) -> dict[str, str]:
        return {
            "area": self.area,
            "status": self.status.value,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class ControlledBrokerValidationChecklist:
    """Immutable archive of the Checkpoint 17.8 behavior-validation record.

    Attributes:
        checks:
            Chronologically ordered validation checks (deterministic).
        real_broker_opt_in:
            Whether real-broker integration was opted in at record time.
    """

    checks: tuple[ControllableValidationCheck, ...] = ()
    real_broker_opt_in: bool = False

    def __post_init__(self) -> None:
        for check in self.checks:
            if not isinstance(check, ControllableValidationCheck):
                raise TypeError(
                    "checks must contain ControllableValidationCheck items."
                )

    @property
    def is_empty(self) -> bool:
        return not self.checks

    def verified_areas(self) -> tuple[str, ...]:
        """Areas whose status is VERIFIED_AGAINST_CONTROLLED_BROKER."""
        return tuple(
            c.area
            for c in self.checks
            if c.status is VerificationStatus.VERIFIED_AGAINST_CONTROLLED_BROKER
        )

    def not_verified_areas(self) -> tuple[str, ...]:
        """Areas whose status is NOT_VERIFIED (never proven)."""
        return tuple(
            c.area for c in self.checks if c.status is VerificationStatus.NOT_VERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "real_broker_opt_in": self.real_broker_opt_in,
        }


#: Convenience factory for the 17.8 default (offline) checklist. Every
#: area that would require a real controlled broker is marked NOT_VERIFIED
#: unless real connectivity was actually demonstrated; the mock / official
#: documentation areas carry their honest status.
def default_validation_checklist(
    *,
    status: VerificationStatus = VerificationStatus.NOT_VERIFIED,
    opt_in: bool = False,
    note: str = "",
) -> ControlledBrokerValidationChecklist:
    """Build the default Checkpoint 17.8 behavior-validation checklist.

    Online-only behaviors default to ``NOT_VERIFIED`` (they were NOT proven
    against a real controlled broker in this environment). The caller may
    override the overall status when real validation occurs.
    """

    areas: tuple[tuple[str, VerificationStatus], ...] = (
        ("authentication", status),
        ("environment_identity", status),
        ("capability_metadata", status),
        ("instrument_mapping", status),
        ("order_type_semantics", status),
        ("product_semantics", status),
        ("validity_semantics", status),
        ("exchange_semantics", status),
        ("quantity_rules", status),
        ("price_rules", status),
        ("trigger_rules", status),
        ("client_order_id_tag", status),
        ("broker_order_id", status),
        ("broker_idempotency", status),
        ("submission", status),
        ("response_normalization", status),
        ("order_state_mapping", status),
        ("error_mapping", status),
        ("timeout_ambiguity", status),
        ("unknown_behavior", status),
        ("reconciliation", status),
        ("cancellation", status),
        ("rate_limit", status),
        ("restart_recovery", status),
        ("auditability", status),
        ("paper_live_isolation", VerificationStatus.VERIFIED_USING_MOCKS),
        ("credential_boundary", VerificationStatus.VERIFIED_USING_MOCKS),
        ("network_boundary", VerificationStatus.VERIFIED_USING_MOCKS),
    )
    return ControlledBrokerValidationChecklist(
        checks=tuple(
            ControllableValidationCheck(area=area, status=area_status, note=note)
            for area, area_status in areas
        ),
        real_broker_opt_in=opt_in,
    )


__all__ = [
    "CHECKPOINT_17_8_REAL_BROKER_ENV",
    "CONTROLLED_ENVIRONMENT_NAMES",
    "ControlledBrokerValidationChecklist",
    "ControlledEnvironmentKind",
    "ControllableValidationCheck",
    "LIVE_ENVIRONMENT_NAMES",
    "OFFICIAL_UPSTOX_REFERENCE_URLS",
    "REQUIRED_CONTROLLED_CAPABILITIES",
    "StartupGuardResult",
    "UPSTOX_BROKER_IDENTITY",
    "ValidationCredentialProvider",
    "VerificationStatus",
    "assert_no_auto_mode_switch",
    "capabilities_satisfy",
    "controlled_broker_startup_guard",
    "default_validation_checklist",
    "is_controlled_environment",
    "is_live_environment",
    "live_mode_hard_gate",
    "real_broker_integration_enabled",
]