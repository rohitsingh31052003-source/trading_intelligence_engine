"""Checkpoint 17.8 — REAL-BROKER INTEGRATION TESTS (EXPLICIT OPT-IN).

These tests exercise the real controlled Upstox sandbox/paper environment.

THEY MUST NEVER RUN AUTOMATICALLY.

Execution requires BOTH:

1. ``CHECKPOINT_17_8_REAL_BROKER=1`` in the process environment (the
   repository-wide opt-in gate), AND
2. an actual controlled (non-live) Upstox environment reachable with a
   separately-provided sandbox credential.

In the environment where this module was delivered NO controlled sandbox
credential was available, so every real-broker test here is SKIPPED at
runtime with an explicit reason. The scaffolding is kept so that a future
operator with a valid controlled credential can enable real validation
without weakening the network-free default suite.

Safety rules encoded in this module:

* No network operation happens unless the opt-in gate AND a controlled
  environment AND a controlled credential provider are ALL present.
* A LIVE environment or a LIVE-mode command is FAIL CLOSED (SAFETY FAILURE)
  even when the opt-in gate is enabled.
* No credential is ever committed, logged, persisted, or printed; the fake
  token values here are deliberately non-secret test values.
* The controlled client sits BEHIND the ``UpstoxBrokerClient`` protocol and
  the frozen broker-neutral contract is never modified.
"""

from __future__ import annotations

import pytest

from engine.intelligence.controlled_broker_validation import (
    controlled_broker_startup_guard,
    real_broker_integration_enabled,
)
from engine.intelligence.upstox_broker_adapter import (
    UpstoxBrokerAdapter,
    UpstoxBrokerConfig,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import UpstoxBrokerClient
from engine.intelligence.upstox_credential_provider import (
    StaticUpstoxCredentialProvider,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionMode

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
)

#: Reason recorded when the controlled environment is not available.
_NO_CONTROLLED_ENV_REASON = (
    "No controlled Upstox sandbox/paper credential is available in this "
    "environment; real-broker connectivity is NOT VERIFIED for Checkpoint "
    "17.8. Set CHECKPOINT_17_8_REAL_BROKER=1 and supply a controlled "
    "sandbox credential to run these tests."
)


def _has_controlled_credential() -> bool:
    """Whether a controlled credential exists for the real integration.

    In this delivery the execution access-token env var is NOT set and MUST
    NOT be synthesized from the historical analytics token. Returns False so
    the real-broker tests skip.
    """

    return False


def _real_broker_enabled() -> bool:
    """Full real-broker gate: opt-in env AND controlled credential."""

    return real_broker_integration_enabled() and _has_controlled_credential()


pytestmark = pytest.mark.skipif(
    not _real_broker_enabled(),
    reason=_NO_CONTROLLED_ENV_REASON + (
        " (opt-in gate not enabled)" if not real_broker_integration_enabled() else ""
    ),
)


class TestControlledConnectivityScaffold:
    """Scaffold for future controlled Upstox connectivity validation.

    Every test here requires the opt-in gate AND a controlled credential. In
    environments where neither is present the entire class is skipped.
    """

    def test_startup_guard_passes_for_controlled(self) -> None:
        # A controlled (sandbox, PAPER) configuration with a provider must
        # pass the guard before any connectivity attempt.
        result = controlled_broker_startup_guard(
            broker_identity="upstox",
            execution_mode="PAPER",
            environment="SANDBOX",
            credential_provider=StaticUpstoxCredentialProvider("__test_only__"),
            capability_names=("SUBMIT", "RECONCILE"),
            required_config={"sandbox_endpoint": "https://sandbox.upstox.com"},
            required_config_keys=("sandbox_endpoint",),
        )
        assert result.is_safe

    def test_live_env_never_passes_guard(self) -> None:
        # Even with the opt-in gate enabled, a LIVE environment is a SAFETY
        # FAILURE (Phase 7 LIVE-mode hard gate).
        result = controlled_broker_startup_guard(
            broker_identity="upstox",
            execution_mode="PAPER",
            environment="LIVE",
            credential_provider=StaticUpstoxCredentialProvider("__test_only__"),
            capability_names=("SUBMIT", "RECONCILE"),
        )
        assert result.is_safe is False
        assert result.live_mismatch is True

    def test_live_mode_never_passes_guard(self) -> None:
        result = controlled_broker_startup_guard(
            broker_identity="upstox",
            execution_mode="LIVE",
            environment="SANDBOX",
            credential_provider=StaticUpstoxCredentialProvider("__test_only__"),
            capability_names=("SUBMIT", "RECONCILE"),
        )
        assert result.is_safe is False

    def test_no_network_client_in_this_checkpoint(self) -> None:
        # Checkpoint 17.8 delivers NO real HTTP client: the only client
        # implementations are the mock (17.7) and a future controlled
        # client that has not yet received a controlled credential.
        from engine.intelligence.upstox_broker_client import MockUpstoxBrokerClient

        assert issubclass(MockUpstoxBrokerClient, UpstoxBrokerClient)  # runtime checkable

    def test_adapter_requires_injected_client(self) -> None:
        # The adapter never creates a network client by itself; without an
        # injected client it fails closed.
        with pytest.raises(ValueError):
            UpstoxBrokerAdapter(client=None)

    def test_paper_adapter_mode_bound(self) -> None:
        adapter = paper_upstox_adapter()
        assert adapter.execution_mode is ExecutionMode.PAPER
        assert adapter.capabilities.execution_mode is ExecutionMode.PAPER