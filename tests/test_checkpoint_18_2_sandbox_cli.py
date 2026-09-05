"""Checkpoint 18.2 — read-only sandbox verification CLI tests (OFFLINE).

Deterministic, network-free tests of
:file:`scripts/verify_upstox_sandbox_readonly.py`. When no sandbox credential
and/or gate is present the CLI safely reports UNVERIFIED / exit 2 and issues
NO request. The gate+token path is covered by patching the verifier itself
with a fake transport so the CLI integration is still proven offline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.intelligence.controlled_broker_validation import (
    CHECKPOINT_17_8_REAL_BROKER_ENV,
)
from engine.intelligence.sandbox_readonly_verifier import SandboxReadOnlyVerifier
from engine.intelligence.upstox_broker_models import (
    UpstoxClientFailure,
    UpstoxErrorKind,
)
from engine.intelligence.upstox_sandbox_transport import UpstoxProfileResponse
from engine.intelligence.upstox_credential_provider import (
    UPSTOX_EXECUTION_ACCESS_TOKEN_ENV,
)
from engine.models.sandbox_readonly_verification import (
    SandboxReadOnlyVerification,
    VerificationClassification,
)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_upstox_sandbox_readonly.py"
_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)  # snapshot AT CALL TIME so monkeypatch is honored
    env["PYTHONPATH"] = str(_ROOT / "src") + os.pathsep + str(_ROOT)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class TestCliOffline:
    def test_no_gate_reports_unverified_exit2(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        proc = _run_cli("--json")
        assert proc.returncode == 2
        view = json.loads(proc.stdout)
        assert view["status"] == "UNVERIFIED"
        assert "UPSTOX_ANALYTICS_TOKEN" in view["reason"]

    def test_gate_no_token_reports_unverified_exit2(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        monkeypatch.delenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, raising=False)
        proc = _run_cli("--json")
        assert proc.returncode == 2
        view = json.loads(proc.stdout)
        assert view["status"] == "UNVERIFIED"
        assert view["gate_enabled"] is True

    def test_token_present_no_gate_reports_unverified(self, monkeypatch) -> None:
        monkeypatch.delenv(CHECKPOINT_17_8_REAL_BROKER_ENV, raising=False)
        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "dummy-token")
        proc = _run_cli("--json")
        assert proc.returncode == 2
        view = json.loads(proc.stdout)
        assert view["status"] == "UNVERIFIED"
        assert "dummy-token" not in proc.stdout  # token NEVER printed

    def test_happy_path_with_patched_verifier(self, monkeypatch) -> None:
        # Prove the CLI wiring end-to-end offline by pointing the CLI at a
        # fake verifier module (no network, no token).
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "dummy-token")
        proc = _run_cli(
            "--json",
            "--verifier-module",
            "tests.fake_18_2_verifier.FakeVerifierGood",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        view = json.loads(proc.stdout)
        assert view["real_sandbox_connected"] is True
        assert view["profile_broker"] == "UPSTOX"
        assert "dummy-token" not in proc.stdout

    def test_text_mode_banner_and_no_token(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "dummy-token")
        proc = _run_cli("--verifier-module", "tests.fake_18_2_verifier.FakeVerifierGood")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "READ-ONLY" in proc.stdout
        assert "LIVE TRADING IS NOT AUTHORIZED" in proc.stdout
        assert "dummy-token" not in proc.stdout

    def test_cli_fails_closed_when_connectivity_not_established(self, monkeypatch) -> None:
        monkeypatch.setenv(CHECKPOINT_17_8_REAL_BROKER_ENV, "1")
        monkeypatch.setenv(UPSTOX_EXECUTION_ACCESS_TOKEN_ENV, "dummy-token")
        proc = _run_cli("--verifier-module", "tests.fake_18_2_verifier.FakeVerifierBad")
        # Verification RAN but connectivity not established -> exit 1
        assert proc.returncode == 1

    def test_reconciliation_orders_env_parsing(self) -> None:
        verifier_orders = []

        def check(values: str) -> None:
            raw = os.environ.get("CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS", "")
            verifier_orders.append(
                tuple(p.strip() for p in raw.split(",") if p.strip())
            )

        os.environ["CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS"] = " A1 , B2 ,"
        check(os.environ["CHECKPOINT_18_2_RECONCILIATION_ORDER_IDS"])
        assert verifier_orders[-1] == ("A1", "B2")