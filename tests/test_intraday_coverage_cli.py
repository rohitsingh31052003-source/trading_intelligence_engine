"""
Checkpoint 19.2 — coverage diagnostic CLI tests.

The CLI is a thin operator-facing diagnostic over the existing coverage
engine. These tests prove it is deterministic, offline, honest
(honest per-instrument findings reported, never errors), and that it
never requires broker credentials or triggers broker execution.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "scripts" / "check_intraday_coverage.py"

PY = sys.executable


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=180,
    )


class TestCLICoverage:
    def test_fixture_run_exits_zero(self):
        proc = run_cli("--provider", "fixture")
        assert proc.returncode == 0, proc.stderr
        assert "INTRADAY DATA COVERAGE REPORT" in proc.stdout
        assert "assessed=" in proc.stdout
        assert "no broker execution" in proc.stdout

    def test_fixture_count_is_honest(self):
        proc = run_cli("--provider", "fixture")
        assert "unsupported_instrument" in proc.stdout
        assert ": 196" in proc.stdout and "unsupported_instrument" in proc.stdout

    def test_custom_instruments(self):
        proc = run_cli("--provider", "fixture", "--instruments", "RELIANCE,TCS")
        assert proc.returncode == 0
        assert "universe        : custom (2)" in proc.stdout

    def test_json_mode_is_pure_json(self):
        proc = run_cli("--provider", "fixture", "--json")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)  # must parse as pure JSON
        assert data["assessed"] == 200
        assert data["supported"] == 4
        assert data["provider"] == "fixture"

    def test_bad_reference_now_returns_2(self):
        proc = run_cli("--provider", "fixture", "--reference-now", "2026-09-04")
        assert proc.returncode == 2
        assert "must be timezone-aware" in proc.stderr

    def test_empty_instruments_returns_2(self):
        proc = run_cli("--provider", "fixture", "--instruments", " , ")
        assert proc.returncode == 2

    def test_unknown_provider_rejected_by_argparse(self):
        proc = run_cli("--provider", "bogus")
        assert proc.returncode == 2

    def test_deterministic(self):
        a = run_cli("--provider", "fixture", "--instruments", "RELIANCE")
        b = run_cli("--provider", "fixture", "--instruments", "RELIANCE")
        assert a.stdout == b.stdout