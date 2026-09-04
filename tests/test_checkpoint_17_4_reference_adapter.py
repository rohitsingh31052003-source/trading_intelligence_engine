"""Checkpoint 17.4 — ReferenceBrokerAdapter-specific tests.

These tests verify the reference / simulated concrete adapter in detail:
internal translation, deterministic simulated responses, reference request /
response representation, error normalization, mode binding, capabilities,
idempotency mechanics, reconciliation behavior, deterministic scenario
coverage, and the no-network / broker-neutrality source audits.

GENERIC contract conformance (shared with any future adapter) lives in
``tests/test_checkpoint_17_4_contract_conformance.py``; this file is
reference-adapter-specific by design.
"""

from __future__ import annotations

import ast
import pathlib
import re
from decimal import Decimal

import pytest

from engine.intelligence.broker_adapter_contract import (
    derive_client_order_id,
    derive_idempotency_key,
)
from engine.intelligence.reference_broker_adapter import (
    REFERENCE_ADAPTER_SCENARIOS,
    REFERENCE_BROKER_ORDER_ID_PREFIX,
    REFERENCE_EXCHANGE,
    REFERENCE_PRODUCT,
    ReferenceBrokerAdapter,
    ReferenceBrokerRequest,
    ReferenceBrokerResponse,
    ReferenceSimulation,
    derive_reference_broker_order_id,
    live_reference_adapter,
    paper_reference_adapter,
    _translate_command,
)
from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine
from engine.models.broker_adapter import (
    AdapterCapability,
    AdapterResult,
    BrokerErrorCategory,
    BrokerErrorCode,
    BrokerResultStatus,
)
from engine.models.execution_command import ExecutionMode

from tests._checkpoint17_2_fixtures import (
    make_authorization,
    make_command,
    make_intent,
    utc,
)


def paper_cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


def live_cmd(**overrides):
    intent = make_intent()
    auth = make_authorization(intent, scope="live")
    return make_command(intent, auth, **overrides)


def short_cmd(**overrides):
    intent = make_intent(
        plan_id="plan-short-abc",
        entry=Decimal("99.50"),
        stop=Decimal("105.00"),
        target_1=Decimal("90.00"),
        engine_risk_distance=Decimal("5.50"),
        engine_reward_distance=Decimal("9.50"),
        engine_risk_reward_ratio=Decimal("1.727"),
        direction="SHORT",
    )
    auth = make_authorization(intent)
    return make_command(intent, auth, **overrides)


# ============================================================
# A. REQUEST / RESPONSE MODEL CONTRACT
# ============================================================


class TestReferenceModels:
    def test_request_is_frozen_and_slotted(self):
        from dataclasses import FrozenInstanceError

        req = _translate_command(paper_cmd())
        with pytest.raises(FrozenInstanceError):
            req.symbol = "mutated"  # type: ignore[misc]

    def test_request_rejects_empty_symbol(self):
        with pytest.raises(ValueError):
            ReferenceBrokerRequest(
                symbol="",
                exchange=REFERENCE_EXCHANGE,
                order_type="BUY",
                product=REFERENCE_PRODUCT,
                quantity=None,
                price=None,
                stop_price=None,
                target_price=None,
                client_order_id="co-x",
                idempotency_key="idem-x",
                execution_mode="PAPER",
            )

    def test_request_rejects_non_decimal_price(self):
        with pytest.raises(TypeError):
            ReferenceBrokerRequest(
                symbol="REF:NIFTY",
                exchange=REFERENCE_EXCHANGE,
                order_type="BUY",
                product=REFERENCE_PRODUCT,
                quantity=None,
                price=100.5,  # type: ignore[arg-type]
                stop_price=None,
                target_price=None,
                client_order_id="co-x",
                idempotency_key="idem-x",
                execution_mode="PAPER",
            )

    def test_request_rejects_unknown_execution_mode(self):
        with pytest.raises(ValueError):
            ReferenceBrokerRequest(
                symbol="REF:NIFTY",
                exchange=REFERENCE_EXCHANGE,
                order_type="BUY",
                product=REFERENCE_PRODUCT,
                quantity=None,
                price=None,
                stop_price=None,
                target_price=None,
                client_order_id="co-x",
                idempotency_key="idem-x",
                execution_mode="SIM",  # type: ignore[arg-type]
            )

    def test_request_rejects_naive_created_at(self):
        import datetime

        with pytest.raises(ValueError):
            ReferenceBrokerRequest(
                symbol="REF:NIFTY",
                exchange=REFERENCE_EXCHANGE,
                order_type="BUY",
                product=REFERENCE_PRODUCT,
                quantity=None,
                price=None,
                stop_price=None,
                target_price=None,
                client_order_id="co-x",
                idempotency_key="idem-x",
                execution_mode="PAPER",
                created_at=datetime.datetime(2026, 9, 1),  # naive
            )

    def test_request_to_dict_is_deterministic(self):
        req = _translate_command(paper_cmd())
        assert req.to_dict() == req.to_dict()
        assert req.to_dict()["symbol"].endswith(":NIFTY")
        assert isinstance(req.to_dict()["quantity"], str)

    def test_response_is_frozen_and_validates_status(self):
        from dataclasses import FrozenInstanceError

        resp = ReferenceBrokerResponse(
            client_order_id="co-x", status="accepted", broker_order_id="brk-1"
        )
        with pytest.raises(FrozenInstanceError):
            resp.status = "mutated"  # type: ignore[misc]
        with pytest.raises(ValueError):
            ReferenceBrokerResponse(client_order_id="co-x", status="bogus")

    def test_response_rejects_unknown_error_kind(self):
        with pytest.raises(ValueError):
            ReferenceBrokerResponse(
                client_order_id="co-x",
                status="failed",
                error_kind="not-a-kind",
            )

    def test_response_failure_property(self):
        assert ReferenceBrokerResponse(
            client_order_id="co-x", status="failed", error_kind="internal"
        ).is_failure
        assert not ReferenceBrokerResponse(
            client_order_id="co-x", status="accepted"
        ).is_failure


# ============================================================
# B. ADAPTER-OWNED TRANSLATION
# ============================================================


class TestAdapterOwnedTranslation:
    def test_symbol_uses_reference_exchange_prefix(self):
        req = _translate_command(paper_cmd())
        assert req.symbol == f"{REFERENCE_EXCHANGE}:NIFTY"
        assert req.exchange == REFERENCE_EXCHANGE

    def test_direction_maps_to_order_type(self):
        long_req = _translate_command(paper_cmd())
        assert long_req.order_type == "BUY"
        short_req = _translate_command(short_cmd())
        assert short_req.order_type == "SELL"

    def test_quantity_and_price_preserved_verbatim(self):
        cmd = paper_cmd()
        req = _translate_command(cmd)
        assert req.quantity == cmd.quantity
        assert req.price == cmd.entry
        assert req.stop_price == cmd.stop
        assert req.target_price == cmd.target

    def test_client_order_id_and_idempotency_key_deterministic(self):
        cmd = paper_cmd()
        req = _translate_command(cmd)
        assert req.client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )
        assert req.idempotency_key == derive_idempotency_key(
            command_id=cmd.command_id
        )

    def test_translation_does_not_mutate_command(self):
        cmd = paper_cmd()
        before = cmd.command_id
        _translate_command(cmd)
        assert cmd.command_id == before

    def test_translation_isolation_all_fields_are_generic(self):
        """The request contains only adapter-owned generic representation."""
        req = _translate_command(paper_cmd())
        assert req.client_order_id.startswith("co-")
        assert req.idempotency_key.startswith("idem-")
        assert req.order_type in ("BUY", "SELL")


# ============================================================
# C. RESULT / ERROR NORMALIZATION
# ============================================================


class TestResultAndErrorNormalization:
    @pytest.mark.parametrize(
        ("scenario", "status", "error_code"),
        [
            ("accepted", BrokerResultStatus.ACCEPTED, None),
            ("rejected", BrokerResultStatus.REJECTED, BrokerErrorCode.BROKER_REJECTION),
            ("failed", BrokerResultStatus.FAILED, BrokerErrorCode.INTERNAL_ADAPTER_FAILURE),
            ("timeout", BrokerResultStatus.UNKNOWN, BrokerErrorCode.TIMEOUT),
            ("unknown", BrokerResultStatus.UNKNOWN, BrokerErrorCode.UNKNOWN_OUTCOME),
            ("restart", BrokerResultStatus.SUBMITTED, None),
            ("cancelled", BrokerResultStatus.CANCELLED, None),
            ("filled", BrokerResultStatus.FILLED, None),
            ("partially_filled", BrokerResultStatus.PARTIALLY_FILLED, None),
            ("reconcile_accepted", BrokerResultStatus.ACCEPTED, None),
            ("reconcile_rejected", BrokerResultStatus.REJECTED, BrokerErrorCode.BROKER_REJECTION),
            ("reconcile_unknown", BrokerResultStatus.UNKNOWN, BrokerErrorCode.UNKNOWN_OUTCOME),
            ("duplicate", BrokerResultStatus.ACCEPTED, None),
        ],
    )
    def test_scenario_produces_expected_broker_neutral_status(
        self, scenario, status, error_code
    ):
        adapter = paper_reference_adapter(submit_scenario=scenario)
        result = adapter.submit(paper_cmd())
        assert result.status is status
        if error_code is None:
            assert result.error is None
        else:
            assert result.error is not None
            assert result.error.code is error_code

    @pytest.mark.parametrize(
        ("scenario", "error_code", "category"),
        [
            ("unsupported_operation", BrokerErrorCode.UNSUPPORTED_OPERATION, BrokerErrorCategory.VALIDATION),
            ("unsupported_instrument", BrokerErrorCode.UNSUPPORTED_INSTRUMENT, BrokerErrorCategory.VALIDATION),
            ("unsupported_order_type", BrokerErrorCode.UNSUPPORTED_ORDER_SEMANTICS, BrokerErrorCategory.VALIDATION),
            ("validation_failure", BrokerErrorCode.VALIDATION_FAILURE, BrokerErrorCategory.VALIDATION),
            ("malformed_internal", BrokerErrorCode.MALFORMED_RESPONSE, BrokerErrorCategory.AMBIGUOUS),
        ],
    )
    def test_error_kind_normalized_to_broker_neutral_taxonomy(
        self, scenario, error_code, category
    ):
        adapter = paper_reference_adapter(submit_scenario=scenario)
        result = adapter.submit(paper_cmd())
        assert result.status is BrokerResultStatus.FAILED
        assert result.error.code is error_code
        assert result.error.category is category

    def test_adapter_never_emits_adapter_specific_exception(self):
        """Errors are normalized; core receives no custom exception types."""
        adapter = paper_reference_adapter(submit_scenario="malformed_internal")
        result = adapter.submit(paper_cmd())
        assert isinstance(result, AdapterResult)
        assert type(result.error).__name__ == "BrokerError"

    def test_normalized_unknown_has_no_order_id(self):
        """An ambiguous outcome carries no fabricated broker order id."""
        adapter = paper_reference_adapter(submit_scenario="timeout")
        result = adapter.submit(paper_cmd())
        assert result.broker_order_id is None

    def test_reference_broker_order_id_is_deterministic(self):
        one = derive_reference_broker_order_id(
            client_order_id="co-x", operation="submit", scenario="accepted"
        )
        two = derive_reference_broker_order_id(
            client_order_id="co-x", operation="submit", scenario="accepted"
        )
        assert one == two
        assert one.startswith(REFERENCE_BROKER_ORDER_ID_PREFIX)


# ============================================================
# D. MODE BINDING
# ============================================================


class TestExecutionModeBinding:
    def test_paper_factory_is_bound_to_paper(self):
        assert paper_reference_adapter().execution_mode is ExecutionMode.PAPER

    def test_live_factory_is_bound_to_live(self):
        assert live_reference_adapter().execution_mode is ExecutionMode.LIVE

    def test_paper_adapter_rejects_live_command(self):
        adapter = paper_reference_adapter()
        assert adapter.supports(live_cmd()) is False
        with pytest.raises(ValueError):
            adapter.submit(live_cmd())

    def test_live_adapter_rejects_paper_command(self):
        adapter = live_reference_adapter()
        assert adapter.supports(paper_cmd()) is False
        with pytest.raises(ValueError):
            adapter.submit(paper_cmd())

    def test_mode_cannot_be_silently_overridden(self):
        adapter = paper_reference_adapter()
        assert adapter.execution_mode is ExecutionMode.PAPER
        assert adapter.execution_mode is not ExecutionMode.LIVE


# ============================================================
# E. CAPABILITIES
# ============================================================


class TestCapabilities:
    def test_default_capabilities(self):
        adapter = paper_reference_adapter()
        caps = adapter.capabilities.capabilities
        assert AdapterCapability.SUBMIT in caps
        assert AdapterCapability.RECONCILE in caps
        assert AdapterCapability.CANCEL in caps

    def test_cancel_supported_by_default(self):
        adapter = paper_reference_adapter()
        assert adapter.capabilities.supports_cancel
        result = adapter.cancel("co-x")
        assert result.status is BrokerResultStatus.ACCEPTED

    def test_cancel_without_capability_raises(self):
        adapter = paper_reference_adapter(
            capabilities=(AdapterCapability.SUBMIT, AdapterCapability.RECONCILE)
        )
        assert not adapter.capabilities.supports_cancel
        with pytest.raises(ValueError):
            adapter.cancel("co-x")

    def test_supports_returns_false_for_unsupported_instrument(self):
        adapter = paper_reference_adapter(unsupported_instruments=("NIFTY",))
        assert adapter.supports(paper_cmd()) is False

    def test_check_raises_for_unsupported_instrument(self):
        adapter = paper_reference_adapter(unsupported_instruments=("NIFTY",))
        with pytest.raises(ValueError):
            adapter.check(paper_cmd())

    def test_check_accepts_supported_instrument(self):
        adapter = paper_reference_adapter()
        adapter.check(paper_cmd())

    def test_supports_returns_false_for_non_command(self):
        adapter = paper_reference_adapter()
        assert adapter.supports(object()) is False  # type: ignore[arg-type]


# ============================================================
# F. IDEMPOTENCY MECHANICS
# ============================================================


class TestIdempotency:
    def test_same_command_same_client_identity(self):
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        adapter.submit(cmd)
        expected = derive_client_order_id(command_id=cmd.command_id)
        assert all(r.client_order_id == expected for r in adapter.simulation.submissions)

    def test_adapter_level_duplicate_detection(self):
        """Direct double-submit of the same command reports broker dedupe."""
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        first = adapter.submit(cmd)
        second = adapter.submit(cmd)
        assert first.status is BrokerResultStatus.ACCEPTED
        assert second.status is BrokerResultStatus.ACCEPTED
        assert second.broker_status == "duplicate"

    def test_different_commands_different_client_identity(self):
        cmd_a = paper_cmd()
        intent_b = make_intent(plan_id="plan-identity-b")
        cmd_b = make_command(intent_b, make_authorization(intent_b))
        assert cmd_a.command_id != cmd_b.command_id
        assert derive_client_order_id(command_id=cmd_a.command_id) != derive_client_order_id(
            command_id=cmd_b.command_id
        )

    def test_no_random_client_identity_generation(self):
        """The adapter never generates a new client identity per submit."""
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        adapter.submit(cmd)
        identities = {r.client_order_id for r in adapter.simulation.submissions}
        assert len(identities) == 1


# ============================================================
# G. RECONCILIATION
# ============================================================


class TestReconciliation:
    def test_submit_timeout_then_reconcile_accepted(self):
        cmd = paper_cmd()
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=cmd,
            adapter=paper_reference_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        assert lifecycle.state.name == "UNKNOWN"
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle,
            adapter=paper_reference_adapter(
                reconcile_scenario="reconcile_accepted"
            ),
            created_at=utc(2026, 9, 1, 13),
        )
        assert reconciled.state.name == "ACCEPTED"

    def test_submit_timeout_then_reconcile_rejected(self):
        cmd = paper_cmd()
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=cmd,
            adapter=paper_reference_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle,
            adapter=paper_reference_adapter(
                reconcile_scenario="reconcile_rejected"
            ),
            created_at=utc(2026, 9, 1, 13),
        )
        assert reconciled.state.name == "REJECTED"

    def test_submit_timeout_then_reconcile_unknown_stays_unknown(self):
        cmd = paper_cmd()
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=cmd,
            adapter=paper_reference_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        reconciled = engine.reconcile_submission(
            lifecycle=lifecycle,
            adapter=paper_reference_adapter(
                reconcile_scenario="reconcile_unknown"
            ),
            created_at=utc(2026, 9, 1, 13),
        )
        assert reconciled.state.name == "UNKNOWN"

    def test_reconcile_uses_same_client_order_id(self):
        cmd = paper_cmd()
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=cmd,
            adapter=paper_reference_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        adapter = paper_reference_adapter(
            reconcile_scenario="reconcile_accepted"
        )
        engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert adapter.simulation.reconciliations == [lifecycle.client_order_id]

    def test_reconcile_never_sends_a_new_order(self):
        cmd = paper_cmd()
        engine = SubmissionLifecycleEngine()
        lifecycle = engine.submit(
            command=cmd,
            adapter=paper_reference_adapter(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 12),
        )
        adapter = paper_reference_adapter(
            reconcile_scenario="reconcile_accepted"
        )
        engine.reconcile_submission(
            lifecycle=lifecycle, adapter=adapter, created_at=utc(2026, 9, 1, 13)
        )
        assert len(adapter.simulation.submissions) == 0


# ============================================================
# H. DETERMINISTIC SCENARIO COVERAGE
# ============================================================


class TestDeterministicScenarios:
    def test_all_scenarios_are_recognized(self):
        for scenario in REFERENCE_ADAPTER_SCENARIOS:
            adapter = paper_reference_adapter(submit_scenario=scenario)
            result = adapter.submit(paper_cmd())
            assert isinstance(result, AdapterResult)

    def test_repeated_submission_is_identical(self):
        cmd = paper_cmd()
        a = paper_reference_adapter(submit_scenario="accepted")
        b = paper_reference_adapter(submit_scenario="accepted")
        r1 = a.submit(cmd)
        r2 = b.submit(cmd)
        assert r1.status is r2.status
        assert r1.broker_order_id == r2.broker_order_id

    def test_simulation_rejects_unknown_scenario(self):
        with pytest.raises(ValueError):
            ReferenceSimulation(submit_scenario="bogus")

    def test_adapter_rejects_unknown_scenario(self):
        with pytest.raises(ValueError):
            paper_reference_adapter(submit_scenario="bogus")

    def test_simulation_records_operations(self):
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        adapter.reconcile("co-x")
        adapter.cancel("co-x")
        assert len(adapter.simulation.submissions) == 1
        assert adapter.simulation.reconciliations == ["co-x"]
        assert adapter.simulation.cancels == ["co-x"]

    def test_orders_snapshot_is_deterministically_ordered(self):
        cmd = paper_cmd()
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        orders = adapter.simulation.orders
        assert len(orders) == 1
        assert orders[0].client_order_id == derive_client_order_id(
            command_id=cmd.command_id
        )


# ============================================================
# I. NO-NETWORK / BROKER-NEUTRALITY SOURCE AUDIT
# ============================================================


class TestNoNetworkAndBrokerNeutrality:
    _FORBIDDEN_IMPORTS = (
        "import socket",
        "import requests",
        "import httpx",
        "import urllib",
        "from urllib",
        "import http",
        "urlopen",
        "import aiohttp",
        "import websocket",
        "import websockets",
    )

    _FORBIDDEN_BROKER = (
        "Upstox",
        "Zerodha",
        "kiteconnect",
        "yfinance",
        "pyotp",
        "place_order",
        "KiteConnect",
    )

    def _module_text(self) -> str:
        return pathlib.Path(
            "src/engine/intelligence/reference_broker_adapter.py"
        ).read_text(encoding="utf-8")

    def test_no_network_imports(self):
        text = self._module_text()
        for frag in self._FORBIDDEN_IMPORTS:
            assert frag not in text

    def test_no_real_broker_reference(self):
        text = self._module_text()
        for frag in self._FORBIDDEN_BROKER:
            assert frag not in text

    def test_no_credentials_or_tokens(self):
        text = self._module_text()
        for frag in ("Authorization:", "Bearer ", "api_key", "api-key", "access_token"):
            assert frag not in text

    def test_no_network_stdlib_import_nodes(self):
        tree = ast.parse(self._module_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(
                        ("socket", "http", "urllib", "requests", "httpx")
                    ), f"forbidden import {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("socket", "http", "urllib", "requests", "httpx"), (
                    f"forbidden import {node.module}"
                )

    def test_module_has_no_credentials_attributes(self):
        """The adapter exposes no credential / token attributes."""
        adapter = paper_reference_adapter()
        public = [m for m in dir(adapter) if not m.startswith("_")]
        for forbidden in ("api_key", "token", "secret", "password", "credential"):
            assert forbidden not in public


# ============================================================
# J. DEPENDENCY DIRECTION AUDIT
# ============================================================


class TestDependencyDirection:
    def test_generic_contract_does_not_import_reference_adapter(self):
        text = pathlib.Path(
            "src/engine/intelligence/broker_adapter_contract.py"
        ).read_text(encoding="utf-8")
        assert "reference_broker_adapter" not in text

    def test_execution_command_does_not_import_reference_adapter(self):
        text = pathlib.Path(
            "src/engine/models/execution_command.py"
        ).read_text(encoding="utf-8")
        assert "reference_broker_adapter" not in text

    def test_submission_lifecycle_engine_does_not_import_reference_adapter(self):
        text = pathlib.Path(
            "src/engine/intelligence/submission_lifecycle.py"
        ).read_text(encoding="utf-8")
        assert "reference_broker_adapter" not in text

    def test_infrastructure_does_not_import_reference_adapter(self):
        text = pathlib.Path(
            "src/engine/intelligence/broker_adapter_infrastructure.py"
        ).read_text(encoding="utf-8")
        assert "reference_broker_adapter" not in text

    def test_reference_adapter_imports_only_contract_and_models(self):
        """The concrete adapter depends on the generic contract, not vice versa."""
        tree = ast.parse(
            pathlib.Path(
                "src/engine/intelligence/reference_broker_adapter.py"
            ).read_text(encoding="utf-8")
        )
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert "engine.intelligence.broker_adapter_contract" in imports
        assert "engine.models.broker_adapter" in imports
        assert "engine.models.execution_command" in imports
        # No reverse / analysis / paper-trading dependency.
        for forbidden in (
            "engine.intelligence.submission_lifecycle",
            "engine.intelligence.broker_adapter_infrastructure",
            "engine.intelligence.trade_planning",
            "engine.intelligence.paper_trading",
            "engine.models.trade_plan",
            "engine.models.paper_trade",
        ):
            assert forbidden not in imports


# ============================================================
# K. AUTHORIZATION SEPARATION
# ============================================================


class TestAuthorizationSeparation:
    def test_adapter_has_no_authorization_methods(self):
        adapter = paper_reference_adapter()
        public = [m for m in dir(adapter) if not m.startswith("_")]
        for forbidden in ("authorize", "create_authorization", "grant", "authorization"):
            assert forbidden not in public

    def test_adapter_cannot_create_a_command(self):
        adapter = paper_reference_adapter()
        public = [m for m in dir(adapter) if not m.startswith("_")]
        assert "create_command" not in public
        assert "create_execution_command" not in public


# ============================================================
# L. IMMUTABILITY / NO MUTATION
# ============================================================


class TestNoMutation:
    def test_submit_does_not_mutate_command(self):
        cmd = paper_cmd()
        before = cmd.command_id
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        assert cmd.command_id == before
        assert cmd.instrument == "NIFTY"

    def test_simulation_does_not_mutate_request(self):
        cmd = paper_cmd()
        req = _translate_command(cmd)
        before = req.to_dict()
        adapter = paper_reference_adapter()
        adapter.submit(cmd)
        assert req.to_dict() == before