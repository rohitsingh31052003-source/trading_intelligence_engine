"""Checkpoint 17.7 — UpstoxBrokerAdapter contract conformance + adapter tests.

This module proves the concrete :class:`UpstoxBrokerAdapter` conforms to the
frozen broker-neutral :class:`BrokerAdapter` contract (using the REUSABLE
:class:`BrokerAdapterContractConformanceBase` suite already run against the
reference adapter and the fake broker) and adds Upstox-adapter-specific tests:

* request / translation mapping (ExecutionCommand -> UpstoxBrokerRequest)
* instrument mapping (verified map + fail-closed unknown)
* order-type / product / validity / exchange mapping
* capability mapping (supports / check)
* client-order-id / tag mapping (deterministic, restart-stable)
* error mapping (client failures + Upstox error codes -> broker-neutral)
* state mapping (Upstox order states -> BrokerResultStatus)
* response validation (success envelope vs. error envelope vs. malformed)
* cancellation behavior (valid / race-with-fill / timeout -> UNKNOWN)
* reconciliation behavior (accepted / rejected / unknown)

EVERY test runs against the network-free
:class:`~engine.intelligence.upstox_broker_client.MockUpstoxBrokerClient`.
No real broker, no SDK, no credentials, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.intelligence.broker_adapter_contract import (
    AdapterCapabilities,
    derive_client_order_id,
    select_adapter,
    validate_adapter_mode,
)
from engine.intelligence.upstox_broker_adapter import (
    UpstoxBrokerAdapter,
    UpstoxBrokerConfig,
    derive_upstox_tag,
    live_upstox_adapter,
    map_order_state,
    normalize_client_failure,
    paper_upstox_adapter,
)
from engine.intelligence.upstox_broker_client import (
    MockUpstoxBrokerClient,
    redact_sensitive,
)
from engine.intelligence.upstox_broker_models import (
    UpstoxBrokerRequest,
    UpstoxClientFailure,
    UpstoxErrorKind,
    UpstoxOrderState,
    UpstoxOrderType,
    UpstoxProduct,
    UpstoxTransactionType,
    UpstoxValidity,
)
from engine.intelligence.upstox_credential_provider import (
    StaticUpstoxCredentialProvider,
)
from engine.models.broker_adapter import (
    AdapterCapability,
    BrokerError,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionCommand, ExecutionMode

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
)
from tests.test_checkpoint_17_4_contract_conformance import (
    BrokerAdapterContractConformanceBase,
    paper_cmd,
)


_UTC = timezone.utc


def _cmd(**overrides):
    """Build a valid command; default PAPER mode."""
    intent = make_intent()
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


def _live_cmd(**overrides):
    """Build a valid LIVE-mode command."""
    intent = make_intent()
    auth = make_authorization(intent, scope="live")
    return make_command(intent, auth, **overrides)


def _raw_command(execution_mode=ExecutionMode.PAPER):
    """Hand-build a valid ExecutionCommand (slots-based; no __dict__)."""
    return ExecutionCommand(
        command_id="cmd-" + "a" * 16,
        authorization_id="auth-x",
        intent_id="intent-x",
        content_fingerprint="fp-x",
        instrument="NIFTY",
        direction="LONG",
        entry=Decimal("100.50"),
        stop=Decimal("95.00"),
        target=Decimal("110.00"),
        quantity=Decimal("10"),
        planned_risk=Decimal("55.00"),
        maximum_risk=Decimal("100.00"),
        execution_mode=execution_mode,
        created_at=datetime(2026, 9, 1, tzinfo=_UTC),
    )


def client(**kwargs):
    """A network-free mock Upstox client with a fake token provider."""
    kwargs.setdefault("credential_provider", StaticUpstoxCredentialProvider("fake"))
    return MockUpstoxBrokerClient(**kwargs)


class TestUpstoxBrokerAdapterContractConformance(BrokerAdapterContractConformanceBase):
    """The Upstox adapter conforms to the generic broker-neutral contract."""

    ADAPTER_FACTORY = staticmethod(paper_upstox_adapter)


# ============================================================
# REQUEST TRANSLATION
# ============================================================


class TestRequestTranslation:
    def test_submit_translates_command_to_upstox_request(self):
        adapter = paper_upstox_adapter()
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.ACCEPTED
        req = adapter.dispatched_requests[-1]
        assert isinstance(req, UpstoxBrokerRequest)
        assert req.instrument_token == "NSE_INDEX|Nifty 50"
        assert req.transaction_type is UpstoxTransactionType.BUY  # LONG -> BUY
        assert req.quantity == Decimal("10")
        assert req.product is UpstoxProduct.D
        assert req.validity is UpstoxValidity.DAY

    def test_short_direction_maps_to_sell(self):
        # Valid SHORT geometry: stop ABOVE entry.
        intent = make_intent(direction="SHORT", entry="100.50", stop="105.00")
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        adapter.submit(cmd)
        req = adapter.dispatched_requests[-1]
        assert req.transaction_type is UpstoxTransactionType.SELL

    def test_entry_price_passed_verbatim(self):
        intent = make_intent(entry="100.50")
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        adapter.submit(cmd)
        req = adapter.dispatched_requests[-1]
        assert req.price == Decimal("100.50")

    def test_stop_passed_verbatim_when_present(self):
        intent = make_intent(stop="95.00")
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        adapter.submit(cmd)
        req = adapter.dispatched_requests[-1]
        assert req.trigger_price == Decimal("95.00")

    def test_fractional_quantity_is_never_increased(self):
        intent = make_intent(quantity=Decimal("10.5"))
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        adapter.submit(cmd)
        req = adapter.dispatched_requests[-1]
        assert req.quantity == Decimal("10")  # floored, never increased

    def test_fractional_quantity_below_one_fails_closed(self):
        intent = make_intent(quantity=Decimal("0.5"))
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        with pytest.raises(ValueError):
            adapter.submit(cmd)

    def test_target_not_transmitted(self):
        """The Upstox place-order request carries NO target (documented loss)."""
        adapter = paper_upstox_adapter()
        adapter.submit(_cmd())
        req = adapter.dispatched_requests[-1]
        assert not hasattr(req, "target")


# ============================================================
# INSTRUMENT MAPPING
# ============================================================


class TestInstrumentMapping:
    def test_verified_instruments_supported(self):
        adapter = paper_upstox_adapter()
        for symbol in ("RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "NIFTY"):
            intent = make_intent(instrument=symbol)
            cmd = make_command(intent, make_authorization(intent))
            assert adapter.supports(cmd) is True

    def test_unknown_instrument_fails_closed(self):
        adapter = paper_upstox_adapter()
        intent = make_intent(instrument="NOTANINSTRUMENT")
        cmd = make_command(intent, make_authorization(intent))
        assert adapter.supports(cmd) is False
        with pytest.raises(ValueError):
            adapter.check(cmd)
        with pytest.raises(ValueError):
            adapter.submit(cmd)

    def test_custom_instrument_map(self):
        config = UpstoxBrokerConfig(
            instrument_key_map={"CUSTOM": "NSE_EQ|INE000000000"}
        )
        adapter = UpstoxBrokerAdapter(client=client(), config=config)
        intent = make_intent(instrument="CUSTOM")
        cmd = make_command(intent, make_authorization(intent))
        assert adapter.supports(cmd) is True

    def test_unsupported_token_prefix_fails_closed(self):
        config = UpstoxBrokerConfig(
            instrument_key_map={"BAD": "BSE_EQ|INE000000000"}
        )
        adapter = UpstoxBrokerAdapter(client=client(), config=config)
        intent = make_intent(instrument="BAD")
        cmd = make_command(intent, make_authorization(intent))
        assert adapter.supports(cmd) is False


# ============================================================
# ORDER TYPE / PRODUCT / VALIDITY / EXCHANGE MAPPING
# ============================================================


class TestOrderSemanticsMapping:
    def test_limit_order_type(self):
        adapter = paper_upstox_adapter()
        adapter.submit(_cmd())
        req = adapter.dispatched_requests[-1]
        assert req.order_type is UpstoxOrderType.LIMIT

    def test_long_stop_below_entry_valid(self):
        adapter = paper_upstox_adapter()
        assert adapter.supports(_cmd()) is True

    def test_long_stop_above_entry_fails_closed(self):
        intent = make_intent(entry="100.50", stop="105.00")
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        assert adapter.supports(cmd) is False
        with pytest.raises(ValueError):
            adapter.check(cmd)

    def test_short_stop_below_entry_fails_closed(self):
        intent = make_intent(direction="SHORT", entry="100.50", stop="95.00")
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        assert adapter.supports(cmd) is False

    def test_missing_entry_fails_closed(self):
        intent = make_intent(entry=None)
        cmd = make_command(intent, make_authorization(intent))
        adapter = paper_upstox_adapter()
        assert adapter.supports(cmd) is False


# ============================================================
# CAPABILITY MAPPING
# ============================================================


class TestCapabilityMapping:
    def test_capabilities_require_submit_and_reconcile(self):
        adapter = paper_upstox_adapter()
        caps = adapter.capabilities
        assert AdapterCapability.SUBMIT in caps.capabilities
        assert AdapterCapability.RECONCILE in caps.capabilities
        assert caps.supports_cancel is True

    def test_supports_never_raises_for_non_command(self):
        adapter = paper_upstox_adapter()
        assert adapter.supports(object()) is False  # type: ignore[arg-type]

    def test_unadvertised_cancel_raises_value_error(self):
        adapter = paper_upstox_adapter(
            capabilities=(AdapterCapability.SUBMIT, AdapterCapability.RECONCILE)
        )
        with pytest.raises(ValueError):
            adapter.cancel("co-test")

    def test_mode_mismatch_fails_closed(self):
        adapter = paper_upstox_adapter()
        with pytest.raises(ValueError):
            validate_adapter_mode(
                adapter_execution_mode=ExecutionMode.PAPER,
                command=_raw_command(ExecutionMode.LIVE),
            )


# ============================================================
# CLIENT ORDER ID / TAG MAPPING
# ============================================================


class TestClientOrderIdTag:
    def test_client_order_id_is_deterministic(self):
        cmd = _cmd()
        one = derive_client_order_id(command_id=cmd.command_id)
        two = derive_client_order_id(command_id=cmd.command_id)
        assert one == two
        assert one.startswith("co-")

    def test_tag_is_deterministic_and_bounded(self):
        cid = derive_client_order_id(command_id=_cmd().command_id)
        tag = derive_upstox_tag(cid)
        assert tag.startswith("uptag-")
        assert len(tag) == len("uptag-") + 12
        assert derive_upstox_tag(cid) == derive_upstox_tag(cid)

    def test_tag_maps_to_upstox_request(self):
        adapter = paper_upstox_adapter()
        cmd = _cmd()
        adapter.submit(cmd)
        req = adapter.dispatched_requests[-1]
        expected_tag = derive_upstox_tag(
            derive_client_order_id(command_id=cmd.command_id)
        )
        assert req.tag == expected_tag

    def test_same_command_same_tag_across_restart(self):
        cmd = _cmd()
        adapter1 = paper_upstox_adapter()
        adapter1.submit(cmd)
        adapter2 = paper_upstox_adapter()
        adapter2.submit(cmd)
        assert (
            adapter1.dispatched_requests[-1].tag
            == adapter2.dispatched_requests[-1].tag
        )


# ============================================================
# ERROR NORMALIZATION
# ============================================================


class TestErrorNormalization:
    def test_timeout_normalizes_to_unknown(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.TIMEOUT, message="Upstox timed out."
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.TIMEOUT
        assert result.error.category is BrokerErrorCategory.AMBIGUOUS

    def test_unknown_outcome_normalizes_to_unknown(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.UNKNOWN_OUTCOME, message="unknown"
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.UNKNOWN_OUTCOME

    def test_authentication_failure_is_failed_transport(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.AUTHENTICATION, message="invalid token"
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.AUTHENTICATION_FAILURE
        assert result.error.category is BrokerErrorCategory.TRANSPORT

    def test_rate_limit_is_failed_transport(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.RATE_LIMIT, message="rate limit"
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.RATE_LIMIT

    def test_malformed_response_is_unknown(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.MALFORMED_RESPONSE, message="malformed"
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.MALFORMED_RESPONSE

    def test_internal_failure_is_failed_internal(self):
        failure = UpstoxClientFailure(
            kind=UpstoxErrorKind.INTERNAL, message="internal"
        )
        result = normalize_client_failure(failure)
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.INTERNAL_ADAPTER_FAILURE

    def test_rejected_submission_normalizes_to_rejected(self):
        adapter = paper_upstox_adapter(submit_scenario="rejected")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.REJECTED
        assert result.error.code is BrokerErrorCode.BROKER_REJECTION

    def test_validation_failure_normalizes(self):
        adapter = paper_upstox_adapter(submit_scenario="validation_failure")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.VALIDATION_FAILURE

    def test_insufficient_funds_normalizes_to_rejected(self):
        adapter = paper_upstox_adapter(submit_scenario="insufficient_funds")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.REJECTED
        assert result.error.code is BrokerErrorCode.BROKER_REJECTION

    def test_invalid_instrument_normalizes(self):
        adapter = paper_upstox_adapter(submit_scenario="invalid_instrument")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.UNSUPPORTED_INSTRUMENT

    def test_invalid_order_type_normalizes(self):
        adapter = paper_upstox_adapter(submit_scenario="invalid_order_type")
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS

    def test_unknown_error_code_normalizes_to_unknown(self):
        from engine.intelligence.upstox_broker_models import (
            UpstoxPlaceOrderResponse,
        )

        class _CustomClient:
            def __init__(self, base):
                self.base = base

            def place_order(self, request):
                return UpstoxPlaceOrderResponse(
                    status="error",
                    order_data=None,
                    error_code="UDAPI99999",
                    error_message="some unknown error",
                )

            def get_order(self, tag, order_id=None):
                return self.base.get_order(tag, order_id)

            def cancel_order(self, order_id):
                return self.base.cancel_order(order_id)

            def check_health(self):
                return True

        adapter = paper_upstox_adapter()
        adapter.client = _CustomClient(adapter.client)  # type: ignore[assignment]
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.UNKNOWN_OUTCOME


# ============================================================
# ORDER-STATE MAPPING
# ============================================================


class TestOrderStateMapping:
    def test_open_maps_to_submitted(self):
        assert map_order_state(UpstoxOrderState.OPEN) is BrokerResultStatus.SUBMITTED

    def test_complete_maps_to_filled(self):
        assert map_order_state(UpstoxOrderState.COMPLETE) is BrokerResultStatus.FILLED

    def test_cancelled_maps_to_cancelled(self):
        assert map_order_state(UpstoxOrderState.CANCELLED) is BrokerResultStatus.CANCELLED

    def test_rejected_maps_to_rejected(self):
        assert map_order_state(UpstoxOrderState.REJECTED) is BrokerResultStatus.REJECTED

    def test_partially_filled_maps_to_partially_filled(self):
        assert (
            map_order_state(UpstoxOrderState.PARTIALLY_FILLED)
            is BrokerResultStatus.PARTIALLY_FILLED
        )

    def test_unknown_maps_to_unknown(self):
        assert map_order_state(UpstoxOrderState.UNKNOWN) is BrokerResultStatus.UNKNOWN


# ============================================================
# RESPONSE VALIDATION
# ============================================================


class TestResponseValidation:
    def test_malformed_success_envelope_fails_closed(self):
        from engine.intelligence.upstox_broker_models import (
            UpstoxPlaceOrderResponse,
        )

        class _BadClient:
            def place_order(self, request):
                # A success envelope without order_data is NOT a valid
                # response -- constructing it raises ValueError (fail closed).
                return UpstoxPlaceOrderResponse(
                    status="success", order_data=None
                )

        # The normalizer must reject the malformed envelope. The adapter's
        # normalization handles it; the mock construction raises ValueError.
        with pytest.raises(ValueError):
            UpstoxPlaceOrderResponse(status="success", order_data=None)

    def test_multi_order_id_response_is_unknown(self):
        from engine.intelligence.upstox_broker_models import (
            UpstoxOrderData,
            UpstoxPlaceOrderResponse,
        )

        class _MultiClient:
            def place_order(self, request):
                return UpstoxPlaceOrderResponse(
                    status="success",
                    order_data=UpstoxOrderData(order_ids=("o1", "o2")),
                )

            def get_order(self, tag, order_id=None):
                return UpstoxClientFailure(
                    kind=UpstoxErrorKind.UNKNOWN_OUTCOME, message="n/a"
                )

            def cancel_order(self, order_id):
                return UpstoxClientFailure(
                    kind=UpstoxErrorKind.UNKNOWN_OUTCOME, message="n/a"
                )

            def check_health(self):
                return True

        adapter = paper_upstox_adapter()
        adapter.client = _MultiClient()  # type: ignore[assignment]
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.MALFORMED_RESPONSE


# ============================================================
# RECONCILIATION
# ============================================================


class TestReconciliation:
    def test_reconcile_accepted(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_accepted")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.ACCEPTED
        assert result.error is None

    def test_reconcile_rejected(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_rejected")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.REJECTED
        assert result.error.category is BrokerErrorCategory.BROKER_REJECTION

    def test_reconcile_unknown_stays_unknown(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_unknown")
        result = adapter.reconcile("co-test")
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.is_ambiguous

    def test_reconcile_uses_tag_primary_lookup(self):
        adapter = paper_upstox_adapter()
        cmd = _cmd()
        adapter.submit(cmd)
        cid = adapter.submissions[-1][1]
        adapter.reconcile(cid)
        assert adapter.client.reconciliations[-1][0] == derive_upstox_tag(cid)

    def test_reconcile_never_creates_a_false_failure(self):
        adapter = paper_upstox_adapter(reconcile_scenario="reconcile_unknown")
        result = adapter.reconcile("co-test")
        assert result.status is not BrokerResultStatus.FAILED


# ============================================================
# CANCELLATION
# ============================================================


class TestCancellation:
    def test_cancel_success(self):
        adapter = paper_upstox_adapter()
        result = adapter.cancel("co-test")
        assert result.status is BrokerResultStatus.CANCELLED

    def test_cancel_timeout_is_unknown(self):
        adapter = paper_upstox_adapter(cancel_scenario="cancellation_timeout")
        result = adapter.cancel("co-test")
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.TIMEOUT

    def test_cancel_race_with_fill_is_rejected_not_false_cancellation(self):
        adapter = paper_upstox_adapter(cancel_scenario="cancellation_race_fill")
        result = adapter.cancel("co-test")
        assert result.status is BrokerResultStatus.REJECTED


# ============================================================
# CREDENTIAL BOUNDARY
# ============================================================


class TestCredentialBoundary:
    def test_missing_credential_fails_closed(self):
        adapter = paper_upstox_adapter()
        adapter.client.credential_provider = None
        result = adapter.submit(_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is BrokerErrorCode.AUTHENTICATION_FAILURE

    def test_live_factory_requires_token(self):
        with pytest.raises(ValueError):
            live_upstox_adapter(
                client=MockUpstoxBrokerClient(credential_provider=None)
            )

    def test_live_factory_with_token(self):
        adapter = live_upstox_adapter()
        assert adapter.execution_mode is ExecutionMode.LIVE

    def test_no_credentials_in_results(self):
        adapter = paper_upstox_adapter(submit_scenario="rejected")
        result = adapter.submit(_cmd())
        assert result.error is not None
        assert "fake" not in result.error.message
        assert "token" not in result.error.message.lower()

    def test_redaction_scrubs_credentials(self):
        text = "Authorization: Bearer SECRET and UPSTOX_EXECUTION_ACCESS_TOKEN=SECRET"
        redacted = redact_sensitive(text)
        assert "SECRET" not in redacted
        assert "Bearer <redacted>" in redacted


# ============================================================
# MODE BINDING / SELECTION
# ============================================================


class TestModeBinding:
    def test_select_adapter_matches_mode(self):
        paper = paper_upstox_adapter()
        selected = select_adapter(
            {"upstox-paper": paper}, _cmd(), preferred="upstox-paper"
        )
        assert selected is paper

    def test_select_adapter_fails_closed_on_missing_mode(self):
        paper = paper_upstox_adapter()
        with pytest.raises(ValueError):
            select_adapter({"upstox-paper": paper}, _raw_command(ExecutionMode.LIVE))

    def test_no_live_paper_fallback(self):
        paper = paper_upstox_adapter()
        live = live_upstox_adapter()
        assert paper.execution_mode is ExecutionMode.PAPER
        assert live.execution_mode is ExecutionMode.LIVE