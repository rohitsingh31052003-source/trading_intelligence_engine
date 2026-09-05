"""Checkpoint 18.2 — fake verifier test seam (OFFLINE, no network).

Standalone module importable by the CLI subprocess test seam
(``--verifier-module tests.fake_18_2_verifier.FakeVerifierGood``). The fake
verifiers never touch a network and never hold a real credential.
"""

from __future__ import annotations

import datetime

from engine.models.sandbox_readonly_verification import (
    SandboxReadOnlyVerification,
    VerificationEnvironment,
)


def _base(real_connected: bool, profile_broker: str = "") -> SandboxReadOnlyVerification:
    now = datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
    return SandboxReadOnlyVerification(
        verification_id="roverify-test000000000",
        broker="upstox",
        environment=VerificationEnvironment.SANDBOX,
        started_at=now,
        completed_at=now,
        token_available=True,
        gate_passed=True,
        real_sandbox_connected=bool(real_connected),
        profile_broker=profile_broker,
        profile_user_type="individual",
        profile_is_active=bool(real_connected),
        audit_entries=(),
        conclusion=(
            "Read-only sandbox verification completed. Sandbox connectivity "
            "and read-only verification do NOT authorize live trading."
        ),
    )


class FakeVerifierGood:
    """CLI test seam: reports positive real sandbox connectivity."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def verify(self) -> SandboxReadOnlyVerification:
        return _base(real_connected=True, profile_broker="UPSTOX")


class FakeVerifierBad:
    """CLI test seam: verification ran but connectivity was NOT established."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def verify(self) -> SandboxReadOnlyVerification:
        return _base(real_connected=False)


__all__ = ["FakeVerifierBad", "FakeVerifierGood"]