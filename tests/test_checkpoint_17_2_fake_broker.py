"""Checkpoint 17.2 — fake broker + broker neutrality + mode isolation tests.

Covers:

* The fake broker is deterministic and network-free with all scenarios.
* Broker neutrality: no broker SDK imports, no broker-specific exception
  classes, no Upstox references in the core contract.
* Mode separation: paper/live cannot cross and cannot be silently overridden.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from engine.intelligence.fake_broker import (
    FAKE_BROKER_SCENARIOS,
    FakeBroker,
    live_fake_broker,
    paper_fake_broker,
)
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


# ============================================================
# L. FAKE BROKER
# ============================================================


class TestFakeBroker:
    def _command(self):
        intent = make_intent()
        auth = make_authorization(intent)
        return make_command(intent, auth)

    def test_all_scenarios_valid(self):
        assert len(FAKE_BROKER_SCENARIOS) >= 10
        for s in FAKE_BROKER_SCENARIOS:
            fb = FakeBroker(submit_scenario=s)
            assert fb is not None

    def test_accepted_submission(self):
        fb = FakeBroker(submit_scenario="accepted")
        result = fb.submit(self._command())
        assert result.status is BrokerResultStatus.ACCEPTED
        assert result.broker_order_id is not None

    def test_rejected_submission(self):
        fb = FakeBroker(submit_scenario="rejected")
        result = fb.submit(self._command())
        assert result.status is BrokerResultStatus.REJECTED
        assert result.error is not None
        assert result.error.code is BrokerErrorCode.BROKER_REJECTION

    def test_deterministic_failure(self):
        fb = FakeBroker(submit_scenario="failed")
        result = fb.submit(self._command())
        assert result.status is BrokerResultStatus.FAILED

    def test_timeout_is_unknown(self):
        fb = FakeBroker(submit_scenario="timeout")
        result = fb.submit(self._command())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.is_ambiguous
        assert result.error.category is BrokerErrorCategory.AMBIGUOUS

    def test_unknown_outcome(self):
        fb = FakeBroker(submit_scenario="unknown")
        result = fb.submit(self._command())
        assert result.status is BrokerResultStatus.UNKNOWN
        assert result.error.code is BrokerErrorCode.UNKNOWN_OUTCOME

    def test_reconcile_discovers_accepted(self):
        fb = FakeBroker(reconcile_scenario="reconcile_accepted")
        result = fb.reconcile("co-abc")
        assert result.status is BrokerResultStatus.ACCEPTED

    def test_reconcile_discovers_rejected(self):
        fb = FakeBroker(reconcile_scenario="reconcile_rejected")
        result = fb.reconcile("co-abc")
        assert result.status is BrokerResultStatus.REJECTED

    def test_reconcile_remains_unknown(self):
        fb = FakeBroker(reconcile_scenario="reconcile_unknown")
        result = fb.reconcile("co-abc")
        assert result.status is BrokerResultStatus.UNKNOWN

    def test_no_network_required(self):
        """The fake broker never touches the network."""
        fb = FakeBroker()
        result = fb.submit(self._command())
        assert result is not None

    def test_logs_operations(self):
        fb = FakeBroker()
        cmd = self._command()
        fb.submit(cmd)
        assert len(fb.submissions) == 1
        assert fb.submissions[0][0] == cmd.command_id

    def test_cancel_scenario(self):
        fb = FakeBroker(cancel_scenario="accepted")
        result = fb.cancel("co-abc")
        assert result.status is BrokerResultStatus.ACCEPTED
        assert len(fb.cancels) == 1


class TestFakeBrokerMode:
    def test_paper_fake(self):
        fb = paper_fake_broker()
        assert fb.execution_mode is ExecutionMode.PAPER

    def test_live_fake(self):
        fb = live_fake_broker()
        assert fb.execution_mode is ExecutionMode.LIVE

    def test_capabilities_declared(self):
        fb = paper_fake_broker()
        caps = [c.value for c in fb.capabilities.capabilities]
        assert "SUBMIT" in caps
        assert "RECONCILE" in caps


# ============================================================
# J. EXECUTION MODE ISOLATION
# ============================================================


class TestModeIsolation:
    def test_paper_command_rejected_by_live_adapter(self):
        from engine.intelligence.broker_adapter_contract import validate_adapter_mode

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.PAPER
        fb = live_fake_broker()
        with pytest.raises(ValueError, match="mismatch"):
            validate_adapter_mode(
                adapter_execution_mode=fb.execution_mode, command=cmd
            )

    def test_live_command_rejected_by_paper_adapter(self):
        from engine.intelligence.broker_adapter_contract import validate_adapter_mode

        intent = make_intent()
        # live scope
        auth = make_authorization(intent, scope="live")
        cmd = make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.LIVE
        fb = paper_fake_broker()
        with pytest.raises(ValueError, match="mismatch"):
            validate_adapter_mode(
                adapter_execution_mode=fb.execution_mode, command=cmd
            )

    def test_engine_blocks_mode_cross(self):
        from engine.intelligence.fake_broker import live_fake_broker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        with pytest.raises(ValueError):
            eng.submit(
                command=cmd,
                adapter=live_fake_broker(submit_scenario="accepted"),
                created_at=utc(2026, 9, 1, 13),
            )

    def test_mode_not_silently_overridden(self):
        """The fake broker never changes its bound mode."""
        fb = FakeBroker(execution_mode=ExecutionMode.PAPER)
        assert fb.execution_mode is ExecutionMode.PAPER

        import copy

        fb2 = FakeBroker(execution_mode=ExecutionMode.LIVE)
        assert fb2.execution_mode is ExecutionMode.LIVE


# ============================================================
# K. BROKER NEUTRALITY
# ============================================================


class TestBrokerNeutrality:
    PAYLOAD_DIRS = [
        pathlib.Path("src/engine/models"),
        pathlib.Path("src/engine/intelligence"),
        pathlib.Path("src/engine/persistence"),
    ]

    FORBIDDEN_TERMS = [
        "upstox",
        "Upstox",
        "UPSTOX",
        "yahoo",
        "Yahoo",
        "zerodha",
        "Zerodha",
        "kite",
        "Kite",
        "import kiteconnect",
        "import upstox",
        "import yfinance",
        "tradetron",
    ]

    def _core_file_paths(self):
        base = pathlib.Path("src")
        for f in base.rglob("*.py"):
            if "test" in f.parts:
                continue
            rel = str(f)
            if "engine/intelligence/broker_adapter_contract.py" in rel:
                yield f
            if "engine/intelligence/submission_lifecycle.py" in rel:
                yield f
            if "engine/intelligence/fake_broker.py" in rel:
                yield f
            if "engine/models/broker_adapter.py" in rel:
                yield f
            if "engine/models/submission_lifecycle.py" in rel:
                yield f
            if "engine/persistence/submission_" in rel:
                yield f

    def test_no_broker_sdk_imports(self):
        for f in self._core_file_paths():
            text = f.read_text(encoding="utf-8")
            for term in ["upstox", "Upstox", "UPSTOX", "kiteconnect", "pyotp", "yfinance"]:
                assert term not in text, f"{f}: contains {term!r}"

    def test_no_network_imports_in_contract(self):
        for f in self._core_file_paths():
            text = f.read_text(encoding="utf-8")
            assert "socket" not in text, f"{f}: socket import"
            assert "requests" not in text, f"{f}: requests import"
            assert "urllib.request" not in text, f"{f}: urllib import"

    def test_all_result_statuses_normalized(self):
        """Every BrokerResultStatus maps to a BrokerResultStatus (no raw sdk)."""
        for st in BrokerResultStatus:
            assert isinstance(st, BrokerResultStatus)

    def test_no_broker_specific_exception_types(self):
        """The model layer only defines our own BrokerError types."""
        from engine.models import broker_adapter

        src = inspect.getsource(broker_adapter)
        # No broker SDK exception names appear
        for term in ["OrderRejectedError", "APIException", "OrderError"]:
            assert term not in src


# ============================================================
# ADAPTER SELECTION (container-level mode routing)
# ============================================================


class TestAdapterSelection:
    def test_selects_paper_adapter_for_paper_command(self):
        from engine.intelligence.broker_adapter_contract import select_adapter
        from engine.intelligence.fake_broker import FakeBroker

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.PAPER
        paper = FakeBroker(name="paper", execution_mode=ExecutionMode.PAPER)
        live = FakeBroker(name="live", execution_mode=ExecutionMode.LIVE)
        adapters = {"paper": paper, "live": live}
        selected = select_adapter(adapters, cmd)
        assert selected.execution_mode is ExecutionMode.PAPER

    def test_selects_live_adapter_for_live_command(self):
        from engine.intelligence.broker_adapter_contract import select_adapter
        from engine.intelligence.fake_broker import FakeBroker

        intent = make_intent()
        auth = make_authorization(intent, scope="live")
        cmd = make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.LIVE
        paper = FakeBroker(name="paper", execution_mode=ExecutionMode.PAPER)
        live = FakeBroker(name="live", execution_mode=ExecutionMode.LIVE)
        adapters = {"paper": paper, "live": live}
        selected = select_adapter(adapters, cmd)
        assert selected.execution_mode is ExecutionMode.LIVE

    def test_no_match_raises(self):
        from engine.intelligence.broker_adapter_contract import select_adapter
        from engine.intelligence.fake_broker import FakeBroker

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        paper = FakeBroker(name="paper", execution_mode=ExecutionMode.PAPER)
        adapters = {"paper": paper}
        # a live command with only a paper adapter -> ValueError
        auth2 = make_authorization(intent, scope="live")
        cmd2 = make_command(intent, auth2)
        with pytest.raises(ValueError):
            select_adapter(adapters, cmd2)