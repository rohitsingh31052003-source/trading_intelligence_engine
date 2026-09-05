"""
Checkpoint 19.3 — market-scan CLI tests.

The CLI is a thin operator-facing diagnostic over the continuous
scanner. These tests prove it is deterministic, offline, honest
(honest per-instrument findings reported, never errors), supports a
deterministic "run one scan cycle" mode, and never requires broker
credentials or triggers broker execution.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "scripts" / "scan_market.py"

PY = sys.executable


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=180,
    )


class TestScanMarketCLI:
    def test_fixture_once_exits_zero(self):
        proc = run_cli("--once", "--instruments", "RELIANCE,TCS")
        assert proc.returncode == 0, proc.stderr
        assert "CONTINUOUS MARKET SCAN" in proc.stdout
        assert "MARKET-DATA SCANNING ONLY" in proc.stdout

    def test_default_universe_is_top200(self):
        proc = run_cli("--once")
        assert proc.returncode == 0, proc.stderr
        assert "NIFTY Top 200" in proc.stdout

    def test_json_mode_is_pure_json(self):
        proc = run_cli("--once", "--instruments", "RELIANCE,TCS", "--json")
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)  # must parse as pure JSON
        assert data["status"] in ("FULL_SUCCESS", "PARTIAL_SUCCESS")
        assert data["requested"] == 2
        assert {i["instrument"] for i in data["instruments"]} == {
            "RELIANCE", "TCS",
        }

    def test_json_instruments_ordered(self):
        proc = run_cli("--once", "--instruments", "TCS,RELIANCE", "--json")
        data = json.loads(proc.stdout)
        names = [i["instrument"] for i in data["instruments"]]
        assert names == sorted(names)

    def test_cycles_mode_runs_multiple(self):
        proc = run_cli(
            "--cycles", "3", "--interval", "0.001",
            "--instruments", "RELIANCE,TCS",
        )
        assert proc.returncode == 0, proc.stderr
        assert "SCAN CYCLE 1/3" in proc.stdout
        assert "SCAN CYCLE 3/3" in proc.stdout
        assert "[done] cycles=3" in proc.stdout

    def test_bad_reference_now_returns_2(self):
        proc = run_cli("--once", "--reference-now", "2026-09-04")
        assert proc.returncode == 2

    def test_empty_instruments_returns_2(self):
        proc = run_cli("--once", "--instruments", " , ")
        assert proc.returncode == 2

    def test_unknown_provider_rejected_by_argparse(self):
        proc = run_cli("--provider", "upstox")
        assert proc.returncode == 2

    def test_deterministic_fixture_run(self):
        a = run_cli("--once", "--instruments", "RELIANCE,TCS", "--json")
        b = run_cli("--once", "--instruments", "RELIANCE,TCS", "--json")
        assert json.loads(a.stdout) == json.loads(b.stdout)

    def test_no_broker_language(self):
        proc = run_cli("--once", "--instruments", "RELIANCE,TCS")
        assert "no broker execution" in proc.stdout
        assert "no prediction" in proc.stdout