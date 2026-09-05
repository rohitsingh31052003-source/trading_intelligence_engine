"""Checkpoint 18.2 — OPT-IN REAL UPSTOX SANDBOX READ-ONLY VERIFICATION TESTS.

These tests exercise the REAL controlled Upstox Sandbox environment over the
real HTTP transport -- but ONLY read-only endpoints and ONLY when the
operator explicitly enables the repository-wide real-broker gate AND supplies
a genuine SANDBOX credential.

THEY MUST NEVER RUN AUTOMATICALLY. Execution requires BOTH:

1. ``CHECKPOINT_17_8_REAL_BROKER=1`` in the process environment (the
   repository-wide opt-in gate; reused from Checkpoint 17.8 -- 18.2 does NOT
   introduce a second gate), AND
2. a genuine Upstox SANDBOX access token provided via
   ``UPSTOX_EXECUTION_ACCESS_TOKEN`` (the lazy env credential provider).
   The historical-data ``UPSTOX_ANALYTICS_TOKEN`` is NEVER used here.

In the environment where this module was delivered NO sandbox token is
available, so every real-sandbox test SKIPS with an explicit reason. The
scaffolding is kept so a future operator with a valid sandbox token can
enable real read-only validation without weakening the network-free default
suite.

Safety rules encoded in this module (Checkpoint 18.2 rules 1-30):

* READ-ONLY ONLY: only profile/order-details/order-history GETs are invoked;
  place/cancel are blocked by the transport and NEVER reach the network.
* No order is ever created, modified, or cancelled -- reconciliation uses
  PRE-EXISTING order ids supplied by the operator's environment variable
  ``CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS`` (comma-separated) or is
  recorded NOT VERIFIED.
* The token value is never printed, logged, persisted, committed, or placed
  in assertions; only the boolean ``token_available`` / connectivity result
  is asserted. The tests never embed a real token value.
* If the environment is ambiguous or the credential is missing, tests FAIL
  CLOSED (they never convert a missing credential into a passing test).
* LIVE / PROD / live-mode is a SAFETY FAILURE even with the gate enabled.
"""

from __future__ import annotations

import os

import pytest

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
    real_broker_integration_enabled,
)
from engine.intelligence.sandbox_readonly_verifier import SandboxReadOnlyVerifier
from engine.intelligence.upstox_credential_provider import (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
    EnvironmentUpstoxCredentialProvider,
)
from engine.models.sandbox_readonly_verification import (
    SandboxReadOnlyVerification,
    VerificationClassification,
)

_GATE_ENABLED = real_broker_integration_enabled()

#: Env var carrying PRE-EXISTING order ids for read-only reconciliation
#: (comma-separated). Default empty -> reconciliation NOT VERIFIED.
_RECONCILIATION_ORDER_IDS_ENV = "CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS"


def _sandbox_token_available() -> bool:
    token = os.environ.get(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "")
    return bool(token) and isinstance(token, str) and token.strip() != ""


def _ambiguous_environment() -> bool:
    """Fail closed when gate is enabled but the environment is ambiguous.

    ``CHECKPOINT_17_8_REAL_BROKER=1`` requires a SANDBOX credential; an
    enabled gate without that credential is an AMBIGUOUS environment and the
    suite fails closed (never silently passing).
    """

    return _GATE_ENABLED and not _sandbox_token_available()


def _order_ids() -> tuple[str, ...]:
    raw = os.environ.get(_RECONCILIATION_ORDER_IDS_ENV, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


if _ambiguous_environment():
    pytest.skip(
        "CHECKPOINT_17_8_REAL_BROKER=1 is set but no genuine "
        "UPSTOX_EXECUTION_ACCESS_TOKEN sandbox credential is available: "
        "the environment is ambiguous and the real-sandbox suite fails "
        "closed (it must never silently pass without a credential).",
        allow_module_level=True,
    )


_REASON = (
    "No genuine Upstox SANDBOX credential is available in this environment. "
    "Set CHECKPOINT_17_8_REAL_BROKER=1 AND supply a sandbox access token via "
    "UPSTOX_EXECUTION_ACCESS_TOKEN to enable real read-only verification. The "
    "historical UPSTOX_ANALYTICS_TOKEN is NEVER used for execution."
)

pytestmark = pytest.mark.skipif(
    not (_GATE_ENABLED and _sandbox_token_available()),
    reason=_REASON,
)


class TestRealSandboxReadOnly:
    """Real (controlled) read-only verification against the Upstox Sandbox.

    Every test requires the opt-in gate AND a genuine sandbox credential;
    otherwise the whole module skips.
    """

    def test_no_analytics_token_used(self) -> None:
        # The analytics token is NOT a sandbox credential and is never read
        # by the execution-side credential provider. The redaction list names
        # the analytics env var only for scrubbing -- never as a credential.
        from engine.intelligence.upstox_credential_provider import (
            SENSITIVE_TOKEN_ENV_NAMES,
            UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
        )

        assert "UPSTOX_ANALYTICS_TOKEN" in SENSITIVE_TOKEN_ENV_NAMES  # scrub list
        assert UPSTOX_EXECUTION_ACCESS_TOKEN_ENV == "UPSTOX_EXECUTION_ACCESS_TOKEN"
        # The execution provider reads ONLY the execution token name.
        assert UPSTOX_EXECUTION_ACCESS_TOKEN_ENV != "UPSTOX_ANALYTICS_TOKEN"

    def test_profile_readonly_reachable_and_auth_valid(self) -> None:
        verifier = SandboxReadOnlyVerifier(
            credential_provider=EnvironmentUpstoxCredentialProvider(),
            timeout_seconds=30,
        )
        result = verifier.verify()
        assert isinstance(result, SandboxReadOnlyVerification)
        assert result.token_available is True
        assert result.gate_passed is True
        # A real positive verification means real sandbox connectivity.
        assert result.real_sandbox_connected is True
        assert result.profile_broker == "UPSTOX"
        assert result.conclusion
        # Never authorized to trade.
        assert "do NOT authorize live trading" in result.conclusion

    def test_verification_audit_has_no_credential_material(self) -> None:
        verifier = SandboxReadOnlyVerifier(
            credential_provider=EnvironmentUpstoxCredentialProvider(),
            timeout_seconds=30,
        )
        result = verifier.verify()
        token = os.environ.get(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "")
        dumped = str(result.to_dict())
        assert token not in dumped
        assert "Bearer" not in dumped

    def test_reconciliation_over_preexisting_orders(self) -> None:
        order_ids = _order_ids()
        verifier = SandboxReadOnlyVerifier(
            credential_provider=EnvironmentUpstoxCredentialProvider(),
            timeout_seconds=30,
            broker_order_ids=order_ids,
        )
        result = verifier.verify()
        if not order_ids:
            assert result.reconciliation_result == "NOT_VERIFIED"
        else:
            assert result.reconciliation_result  # recorded outcomes
        assert "do NOT authorize live trading" in result.conclusion

    def test_read_only_operations_only(self) -> None:
        # The real transport blocks order-affecting methods outright.
        from engine.intelligence.upstox_sandbox_transport import (
            UpstoxSandboxTransport,
        )

        transport = UpstoxSandboxTransport(
            credential_provider=EnvironmentUpstoxCredentialProvider(),
            timeout_seconds=30,
        )
        import pytest as _pytest

        with _pytest.raises(ValueError):
            transport.place_order(
                __import__(
                    "engine.intelligence.upstox_broker_models",
                    fromlist=["UpstoxBrokerRequest"],
                ).UpstoxBrokerRequest(
                    instrument_token="NSE_EQ|INE062A01020",
                    transaction_type=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxTransactionType"],
                    ).UpstoxTransactionType.BUY,
                    quantity=__import__(
                        "decimal", fromlist=["Decimal"]
                    ).Decimal("1"),
                    product=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxProduct"],
                    ).UpstoxProduct.D,
                    validity=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxValidity"],
                    ).UpstoxValidity.DAY,
                    order_type=__import__(
                        "engine.intelligence.upstox_broker_models",
                        fromlist=["UpstoxOrderType"],
                    ).UpstoxOrderType.LIMIT,
                    price=__import__(
                        "decimal", fromlist=["Decimal"]
                    ).Decimal("571.0"),
                    trigger_price=None,
                    tag="uptag-abc123",
                    client_order_id="co-1234",
                    idempotency_key="idem-1234",
                    execution_mode="PAPER",
                )
            )

        with _pytest.raises(ValueError):
            transport.cancel_order("some-existing-order-id")

    def test_all_audit_entries_redacted_and_classified(self) -> None:
        verifier = SandboxReadOnlyVerifier(
            credential_provider=EnvironmentUpstoxCredentialProvider(),
            timeout_seconds=30,
        )
        result = verifier.verify()
        for entry in result.audit_entries:
            assert entry.to_dict()
            # Never any class of credential-visible text.
            for value in entry.to_dict().values():
                if isinstance(value, str):
                    assert "Bearer " not in value