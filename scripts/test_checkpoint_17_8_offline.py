"""Checkpoint 17.8 offline validation demo (deterministic, network-free).

Visibly demonstrates (1-12):

  1. the real-broker opt-in gate is OFF by default (CHECKPOINT_17_8_REAL_BROKER)
  2. the controlled-broker startup guard passes for a controlled (SANDBOX,
     PAPER) configuration
  3. the startup guard fails closed on each missing precondition
     (mode / broker identity / credential provider / token / environment /
     capability / required config)
  4. "credentials exist != safe to trade": a valid token with a LIVE
     environment still produces a SAFETY FAILURE (no order)
  5. the LIVE-mode hard gate fires for LIVE env/mode against a controlled
     requirement and never switches modes / environments / adapters
  6. paper/live isolation: PAPER adapter + LIVE command -> FAIL CLOSED;
     LIVE adapter + PAPER command -> FAIL CLOSED
  7. credential redaction: Bearer tokens and token env-var values never
     appear in error/reason strings
  8. UNKNOWN stays UNKNOWN (timeout/unknown/malformed -> UNKNOWN, never
     FAILED) and reconciliation is required (no blind retry)
  9. restart recovery: UNKNOWN persists and recovers as RECONCILE_REQUIRED
 10. auditability: the submission audit is broker-neutral and contains no
     credential material
 11. no network imports and no real HTTP client in the 17.8 additions
 12. the 17.7 mock adapter surface still works offline (accepted / unknown /
     reconcile)

This demo runs fully offline (fixture data, mock clients, no internet, no
real credentials). It makes NO network calls and submits NO orders.

Ends with "Checkpoint 17.8 offline demo completed successfully (N checks
passed)." Exits 0.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from datetime import datetime, timezone

from engine.intelligence.broker_adapter_infrastructure import (
    SubmissionInfrastructure,
)
from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    VerificationStatus,
    controlled_broker_startup_guard,
    default_validation_checklist,
    live_mode_hard_gate,
    real_broker_integration_enabled,
)
from engine.intelligence.upstox_broker_adapter import (
    live_upstox_adapter,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import redact_sensitive
from engine.intelligence.upstox_credential_provider import (
    StaticUpstoxCredentialProvider,
)
from engine.models.broker_adapter import BrokerResultStatus
from engine.models.execution_command import ExecutionMode
from engine.persistence.submission_store import SubmissionLifecycleStore
from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)

_PASS = 0


def check(label: str, ok: bool) -> None:
    global _PASS
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}")
    if not ok:
        sys.exit(1)
    _PASS += 1


class _FakeTokenProvider:
    def __init__(self, token: str = "fake-token") -> None:
        self._token = token

    def get_access_token(self) -> str:
        return self._token


def main() -> None:
    print("Checkpoint 17.8 — offline controlled-broker validation demo")
    print("=" * 70)
    print("PAPER-ONLY. NO network calls. NO orders are submitted.")

    # 1. Opt-in gate default OFF.
    check(
        "real-broker opt-in gate is OFF by default",
        real_broker_integration_enabled({}) is False
        and real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: "0"}) is False
        and real_broker_integration_enabled({CHECKPOINT_17_8_REAL_BROKER_ENV: "1"}) is True,
    )

    # 2. Startup guard passes for controlled config.
    happy = controlled_broker_startup_guard(
        broker_identity="upstox",
        execution_mode="PAPER",
        environment="SANDBOX",
        credential_provider=_FakeTokenProvider(),
        capability_names=("SUBMIT", "RECONCILE"),
        required_config={"instrument_token": "NSE_EQ|INE002A01018"},
        required_config_keys=("instrument_token",),
        expected_environment="SANDBOX",
        expected_mode="PAPER",
    )
    check(
        "startup guard passes for controlled (SANDBOX/PAPER) config",
        happy.is_safe,
    )

    # 3. Fail closed on each missing precondition.
    failing = controlled_broker_startup_guard(
        broker_identity=None,
        execution_mode="LIVE",
        environment="UNKNOWN",
        credential_provider=None,
        capability_names=("RECONCILE",),
        required_config=None,
        required_config_keys=("instrument_token",),
    )
    check(
        "startup guard fails closed on multiple missing preconditions",
        not failing.is_safe and len(failing.unmet) >= 7,
    )

    # 4. Credentials do NOT imply safety.
    live_with_token = controlled_broker_startup_guard(
        broker_identity="upstox",
        execution_mode="PAPER",
        environment="LIVE",
        credential_provider=_FakeTokenProvider("valid-looking-token"),
        capability_names=("SUBMIT", "RECONCILE"),
    )
    check(
        "credentials exist != safe to trade (LIVE env -> SAFETY FAILURE)",
        not live_with_token.is_safe and live_with_token.live_mismatch is True,
    )

    # 5. LIVE-mode hard gate.
    check(
        "LIVE-mode hard gate fires for LIVE env when SANDBOX is required",
        live_mode_hard_gate(expected_environment="SANDBOX", reported_environment="LIVE") is True,
    )
    check(
        "LIVE-mode hard gate does NOT fire for SANDBOX env",
        live_mode_hard_gate(expected_environment="SANDBOX", reported_environment="SANDBOX") is False,
    )

    # 6. Paper/live isolation.
    intent = make_intent()
    auth = make_authorization(intent)
    paper_command = make_command(intent, auth)
    live_intent = make_intent()
    live_auth = make_authorization(live_intent, scope="live")
    live_command = make_command(live_intent, live_auth)

    paper_adapter = paper_upstox_adapter()
    try:
        paper_adapter.submit(live_command)
        chapter6 = False
    except ValueError:
        chapter6 = True
    check("PAPER adapter + LIVE command -> FAIL CLOSED", chapter6)

    live_adapter = live_upstox_adapter()
    try:
        live_adapter.submit(paper_command)
        chapter6b = False
    except ValueError:
        chapter6b = True
    check("LIVE adapter + PAPER command -> FAIL CLOSED", chapter6b)
    check(
        "mode-bound adapters (paper=PAPER, live=LIVE, no fallback)",
        paper_adapter.execution_mode is ExecutionMode.PAPER
        and live_adapter.execution_mode is ExecutionMode.LIVE,
    )

    # 7. Credential redaction.
    redacted = redact_sensitive(
        "Authorization: Bearer SUPER-SECRET "
        "UPSTOX_EXECUTION_ACCESS_TOKEN=also-secret"
    )
    check(
        "credential redaction (Bearer + token env values scrubbed)",
        "SUPER-SECRET" not in redacted
        and "also-secret" not in redacted
        and "<redacted>" in redacted,
    )

    # 8. UNKNOWN stays UNKNOWN and reconciliation is required.
    unknown_adapter = paper_upstox_adapter(submit_scenario="unknown")
    unknown_result = unknown_adapter.submit(paper_command)
    check(
        "timeout/unknown submission -> UNKNOWN (never FAILED)",
        unknown_result.status is BrokerResultStatus.UNKNOWN,
    )

    reconcile_adapter = paper_upstox_adapter(
        submit_scenario="unknown", reconcile_scenario="reconcile_accepted"
    )
    reconcile_adapter.submit(paper_command)
    reconcile_result = reconcile_adapter.reconcile(paper_command.command_id)
    check("UNKNOWN -> reconcile -> confirmed ACCEPTED", reconcile_result.status is BrokerResultStatus.ACCEPTED)

    # 9. Restart recovery.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = SubmissionLifecycleStore(directory=str(tmp))
        infra = SubmissionInfrastructure()
        recovered_cmd = make_command(make_intent(), make_authorization(make_intent(), scope="paper"))
        # NOTE: rebuild command with a matching authorization for idempotent id.
        recovered_cmd = make_command(
            make_intent(plan_id="plan-recovery-17-8"),
            make_authorization(make_intent(plan_id="plan-recovery-17-8"), scope="paper"),
        )
        lifecycle = infra.submit_command(
            command=recovered_cmd,
            adapters={"upstox-paper": paper_upstox_adapter(submit_scenario="unknown")},
            submission_store=store,
            created_at=utc(2026, 9, 1, 12, 0),
        )
        check(
            "UNKNOWN persists after submission",
            lifecycle.state.value == "UNKNOWN",
        )
        recovery = infra.recovery_for_command(
            command_id=recovered_cmd.command_id, submission_store=store
        )
        check(
            "restart recovery: UNKNOWN -> RECONCILE_REQUIRED (never auto-resubmit)",
            recovery["recovery_action"] == "RECONCILE_REQUIRED"
            and recovery["reconciliation_required"] is True,
        )
        audit = infra.audit(submission_store=store)
        blob = str(audit.to_dict())
        check(
            "auditability: broker-neutral + no credential material",
            len(audit.rows) == 1
            and "access_token" not in blob
            and "Authorization" not in blob
            and "Bearer" not in blob,
        )

    # 10. No network imports / mock-only additions.
    import inspect

    from engine.intelligence import controlled_broker_validation as cmod

    source = inspect.getsource(cmod)
    check(
        "17.8 additions contain no network imports",
        all(
            f not in source
            for f in (
                "import requests",
                "import urllib",
                "import httpx",
                "import socket",
                "import http",
            )
        ),
    )

    # 11. Verification-status vocabulary is honest.
    checklist = default_validation_checklist(
        status=VerificationStatus.NOT_VERIFIED, opt_in=False
    )
    check(
        "default validation checklist labels online behavior NOT VERIFIED",
        "reconciliation" in checklist.not_verified_areas()
        and "submission" in checklist.not_verified_areas(),
    )

    print("=" * 70)
    print(
        f"Checkpoint 17.8 offline demo completed successfully "
        f"({_PASS} checks passed)."
    )
    print("Real broker connectivity is NOT VERIFIED (no controlled sandbox")
    print("credential available). LIVE TRADING IS NOT AUTHORIZED BY CHECKPOINT 17.8.")


if __name__ == "__main__":
    main()