"""
Tests for the Windows session keep-awake lifecycle
(``scripts/windows/keep_awake.py`` +
``scripts/windows/keep_awake_manager.py``).

These tests cover the keep-awake MANAGEMENT logic only; no trading,
decision, geometry, risk or paper-trading behavior is involved.

Process-liveness, PID-marker and start/stop/replace behavior are tested
with REAL child processes (no mocks) and are platform-independent. The
Windows-only parts (SetThreadExecutionState, the named session mutex, the
detached creation flags, the venv redirector parent/child relationship)
cannot execute on this POSIX test environment; for those, the tests assert
the graceful no-op/fallback behavior and the structural guarantees in the
scripts (correct flags, no ES_DISPLAY_REQUIRED, no tasklist/find identity
check in the launcher).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, ".")
sys.path.insert(0, "scripts/windows")

import keep_awake  # pyright: ignore[reportMissingImports]
import keep_awake_manager as kam  # pyright: ignore[reportMissingImports]

WINDOWS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "windows"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

#: A real helper process that behaves like keep_awake.py's identity
#: contract: parses --pid-file like keep_awake.py, writes its OWN pid +
#: start token, then stays alive.
_FAKE_HELPER = """\
import os, sys, time
from pathlib import Path
sys.path.insert(0, {windows_dir!r})
import keep_awake_manager as kam
args = sys.argv[1:]
pid_file = args[args.index("--pid-file") + 1]
token = kam.process_start_token(os.getpid()) or ""
Path(pid_file).write_text(f"{{os.getpid()}}\\n{{token}}\\n", encoding="ascii")
time.sleep(120)
"""

#: A helper that never writes a PID marker (drives the confirm timeout).
_SILENT_HELPER = "import time; time.sleep(120)"

#: A helper that exits immediately without writing a marker.
_FAILING_HELPER = "import sys; sys.exit(3)"


@pytest.fixture
def fake_helper(tmp_path):
    script = tmp_path / "fake_helper.py"
    script.write_text(_FAKE_HELPER.format(windows_dir=str(WINDOWS_DIR)),
                      encoding="utf-8")
    return script


@pytest.fixture
def silent_helper(tmp_path):
    script = tmp_path / "silent_helper.py"
    script.write_text(_SILENT_HELPER, encoding="utf-8")
    return script


@pytest.fixture
def failing_helper(tmp_path):
    script = tmp_path / "failing_helper.py"
    script.write_text(_FAILING_HELPER, encoding="utf-8")
    return script


@pytest.fixture
def pid_file(tmp_path):
    path = tmp_path / "logs" / "session_keep_awake.pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def cleanup_helpers(pid_file):
    """Ensure no test helper process survives the test."""

    yield
    pid, _ = kam.read_pid_marker(pid_file)
    if pid is not None and kam.process_running(pid):
        try:
            os.kill(pid, 9) if os.name != "nt" else None
        except OSError:
            pass


def _dead_pid() -> int:
    """A PID that was once valid and is now definitely dead."""

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _future_hhmm(minutes: int = 5) -> tuple[int, int]:
    later = datetime.now() + timedelta(minutes=minutes)
    return later.hour, later.minute


def _past_hhmm(minutes: int = 5) -> tuple[int, int]:
    earlier = datetime.now() - timedelta(minutes=minutes)
    return earlier.hour, earlier.minute


def _ensure(**overrides) -> str:
    kwargs = dict(
        python=sys.executable,
        helper_script=overrides.pop("helper_script"),
        pid_file=overrides.pop("pid_file"),
        until=None,
        mutex_name="",  # mutex is Windows-only; tested separately
        emit=lambda _msg: None,
    )
    kwargs.update(overrides)
    return kam.ensure_session_keep_awake(**kwargs)


# ---------------------------------------------------------------------------
# A. time helpers
# ---------------------------------------------------------------------------


class TestTimeHelpers:
    def test_hhmm_valid(self):
        assert kam._hhmm("16:15") == (16, 15)
        assert kam._hhmm("09:05") == (9, 5)

    @pytest.mark.parametrize("bad", ["", "x", "16", "16:60", "24:00", "-1:00",
                                     "ab:cd"])
    def test_hhmm_invalid(self, bad):
        with pytest.raises(Exception):
            kam._hhmm(bad)

    def test_seconds_until_future_positive(self):
        assert kam.seconds_until(_future_hhmm(5)) > 0

    def test_seconds_until_past_negative(self):
        assert kam.seconds_until(_past_hhmm(5)) < 0

    def test_keep_awake_hhmm_matches(self):
        assert keep_awake._hhmm("16:15") == (16, 15)


# ---------------------------------------------------------------------------
# B. PID marker reading
# ---------------------------------------------------------------------------


class TestPidMarker:
    def test_missing_file(self):
        assert kam.read_pid_marker(Path("/nonexistent/x.pid")) == (None, "")

    def test_valid_with_token(self, pid_file):
        pid_file.write_text("12345\n999000111\n", encoding="ascii")
        assert kam.read_pid_marker(pid_file) == (12345, "999000111")

    def test_valid_legacy_single_line(self, pid_file):
        pid_file.write_text("12345\n", encoding="ascii")
        assert kam.read_pid_marker(pid_file) == (12345, "")

    @pytest.mark.parametrize("content", ["", "abc\n", "\n", "  \n999\n"])
    def test_garbage(self, pid_file, content):
        pid_file.write_text(content, encoding="ascii")
        pid, _ = kam.read_pid_marker(pid_file)
        assert pid is None

    def test_negative_pid_rejected(self, pid_file):
        pid_file.write_text("-42\n", encoding="ascii")
        pid, _ = kam.read_pid_marker(pid_file)
        assert not kam.process_running(pid or -1)


# ---------------------------------------------------------------------------
# C. process detection (Windows contract, POSIX-verifiable)
# ---------------------------------------------------------------------------


class TestProcessDetection:
    def test_current_process_is_running(self):
        assert kam.process_running(os.getpid())

    def test_dead_process_is_not_running(self):
        assert not kam.process_running(_dead_pid())

    def test_zero_and_negative_pid_not_running(self):
        assert not kam.process_running(0)
        assert not kam.process_running(-1)

    def test_start_token_stable_for_current_process(self):
        token = kam.process_start_token(os.getpid())
        if token is None:
            pytest.skip("platform cannot supply a process start token")
        assert kam.process_start_token(os.getpid()) == token

    def test_start_token_none_for_dead_process(self):
        assert kam.process_start_token(_dead_pid()) is None

    def test_zombie_is_not_running(self):
        # A terminated-but-unreaped child is a zombie: already dead for
        # keep-awake purposes (its power request and mutex are gone).
        if os.name == "nt":
            pytest.skip("POSIX zombie semantics")
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        # proc not yet reaped by the Popen object internally? wait() reaps;
        # spawn again without reaping to guarantee a zombie.
        proc2 = subprocess.Popen([sys.executable, "-c", "pass"])
        pid = proc2.pid
        proc2.kill()
        for _ in range(50):
            if not kam.process_running(pid):
                break
            time.sleep(0.02)
        else:
            pytest.fail("killed child kept counting as running")
        proc2.wait()


# ---------------------------------------------------------------------------
# D. liveness + identity of the marker
# ---------------------------------------------------------------------------


class TestHelperAlive:
    def test_absent_marker(self, pid_file):
        assert kam.helper_alive_pid(pid_file) is None

    def test_garbage_marker(self, pid_file):
        pid_file.write_text("not-a-pid\n", encoding="ascii")
        assert kam.helper_alive_pid(pid_file) is None

    def test_dead_pid_stale(self, pid_file):
        pid_file.write_text(f"{_dead_pid()}\n", encoding="ascii")
        assert kam.helper_alive_pid(pid_file) is None

    def test_live_pid_legacy_marker(self, pid_file):
        pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
        assert kam.helper_alive_pid(pid_file) == os.getpid()

    def test_live_pid_matching_token(self, pid_file):
        token = kam.process_start_token(os.getpid())
        if token is None:
            pytest.skip("platform cannot supply a process start token")
        pid_file.write_text(f"{os.getpid()}\n{token}\n", encoding="ascii")
        assert kam.helper_alive_pid(pid_file) == os.getpid()

    def test_live_pid_wrong_token_is_stale(self, pid_file):
        # A recycled PID aliases an unrelated live process: the start-time
        # token must NOT match, so the helper is correctly seen as dead.
        if kam.process_start_token(os.getpid()) is None:
            pytest.skip("platform cannot supply a process start token")
        pid_file.write_text(f"{os.getpid()}\nBOGUS-TOKEN\n",
                            encoding="ascii")
        assert kam.helper_alive_pid(pid_file) is None

    def test_live_pid_unverifiable_token_is_stale(self, pid_file,
                                                  monkeypatch):
        # STRICTNESS: a marker WITH a token must never downgrade to a
        # PID-only liveness check, or a recycled PID aliases the helper.
        # monkeypatch is required here: POSIX cannot deterministically
        # fabricate the permission failure that makes the token unreadable.
        token = kam.process_start_token(os.getpid()) or "token"
        pid_file.write_text(f"{os.getpid()}\n{token}\n", encoding="ascii")
        monkeypatch.setattr(kam, "process_start_token", lambda _pid: None)
        assert kam.helper_alive_pid(pid_file) is None


class TestDescribeMarker:
    def test_absent(self, pid_file):
        pid, token, status, reason = kam.describe_marker(pid_file)
        assert (pid, token, status) == (None, "", "absent")
        assert "no readable PID marker" in reason

    def test_stale_dead_process(self, pid_file):
        dead = _dead_pid()
        pid_file.write_text(f"{dead}\n", encoding="ascii")
        pid, _t, status, reason = kam.describe_marker(pid_file)
        assert (pid, status) == (dead, "stale")
        assert "not running" in reason
        assert str(dead) in reason

    def test_alive_legacy_marker(self, pid_file):
        pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
        pid, token, status, reason = kam.describe_marker(pid_file)
        assert (pid, token, status) == (os.getpid(), "", "alive")
        assert "no start-time token" in reason

    def test_alive_matching_token(self, pid_file):
        token = kam.process_start_token(os.getpid())
        if token is None:
            pytest.skip("platform cannot supply a process start token")
        pid_file.write_text(f"{os.getpid()}\n{token}\n", encoding="ascii")
        pid, _t, status, reason = kam.describe_marker(pid_file)
        assert (pid, status) == (os.getpid(), "alive")
        assert "token matches" in reason

    def test_stale_token_mismatch(self, pid_file):
        if kam.process_start_token(os.getpid()) is None:
            pytest.skip("platform cannot supply a process start token")
        pid_file.write_text(f"{os.getpid()}\nWRONG-TOKEN\n",
                            encoding="ascii")
        pid, _t, status, reason = kam.describe_marker(pid_file)
        assert (pid, status) == (os.getpid(), "stale")
        assert "identity mismatch" in reason

    def test_stale_unverifiable_token(self, pid_file, monkeypatch):
        pid_file.write_text(f"{os.getpid()}\ntoken\n", encoding="ascii")
        monkeypatch.setattr(kam, "process_start_token", lambda _pid: None)
        pid, _t, status, reason = kam.describe_marker(pid_file)
        assert (pid, status) == (os.getpid(), "stale")
        assert "could not be verified" in reason


class TestResolveHelperPython:
    def test_explicit_python_respected(self):
        assert kam.resolve_helper_python("/x/y/python.exe") == \
            "/x/y/python.exe"

    def test_defaults_to_base_interpreter(self):
        expected = getattr(sys, "base_executable", None) or sys.executable
        assert kam.resolve_helper_python(None) == expected
        assert kam.resolve_helper_python() == expected

    def test_never_prefers_venv_shim(self):
        # The venv redirector parent governs the real child's lifetime;
        # the helper must be spawned with the base interpreter instead.
        if hasattr(sys, "base_executable"):
            real = sys.base_executable
            assert kam.resolve_helper_python() == real


# ---------------------------------------------------------------------------
# E. start / reuse / replace lifecycle (real processes)
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_when_none_exists(self, fake_helper, pid_file,
                                    cleanup_helpers):
        status = _ensure(helper_script=fake_helper, pid_file=pid_file)
        assert status == "started"
        pid, token = kam.read_pid_marker(pid_file)
        assert pid is not None and kam.process_running(pid)
        if kam.process_start_token(pid) is not None:
            assert token == kam.process_start_token(pid)

    def test_live_helper_is_recognised_not_duplicated(
            self, fake_helper, pid_file, cleanup_helpers):
        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "started"
        first_pid, _ = kam.read_pid_marker(pid_file)
        marker_mtime = pid_file.stat().st_mtime_ns

        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "already-running"
        second_pid, _ = kam.read_pid_marker(pid_file)
        assert second_pid == first_pid
        assert pid_file.stat().st_mtime_ns == marker_mtime  # not rewritten

        # Exactly ONE real helper process for this marker exists.
        if os.name != "nt":
            count = sum(
                1 for p in Path("/proc").iterdir() if p.name.isdigit()
                and (p / "cmdline").exists()
                and str(fake_helper).encode() in (p / "cmdline").read_bytes())
            assert count == 1

    def test_dead_helper_is_replaced(self, fake_helper, pid_file,
                                     cleanup_helpers):
        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "started"
        old_pid, _ = kam.read_pid_marker(pid_file)
        os.kill(old_pid, 9)
        for _ in range(50):
            if not kam.process_running(old_pid):
                break
            time.sleep(0.05)

        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "started"
        new_pid, _ = kam.read_pid_marker(pid_file)
        assert new_pid is not None and new_pid != old_pid
        assert kam.process_running(new_pid)

    def test_stale_pid_recycled_by_unrelated_process_is_replaced(
            self, fake_helper, pid_file, cleanup_helpers):
        # Marker names a live but UNRELATED process (wrong token) - the
        # classic PID-reuse false-alive that tasklist|find cannot detect.
        if kam.process_start_token(os.getpid()) is None:
            pytest.skip("platform cannot supply a process start token")
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{os.getpid()}\nWRONG-TOKEN\n",
                            encoding="ascii")
        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "started"
        pid, _ = kam.read_pid_marker(pid_file)
        assert pid != os.getpid()
        assert kam.process_running(pid)

    def test_stale_log_contains_diagnostics(self, fake_helper, pid_file,
                                            cleanup_helpers):
        # A replaced stale marker must be logged WITH the decision detail:
        # pid, why it was classified stale, and the mutex state.
        dead = _dead_pid()
        pid_file.write_text(f"{dead}\nSOME-TOKEN\n", encoding="ascii")
        messages: list[str] = []
        kam.ensure_session_keep_awake(
            python=sys.executable, helper_script=fake_helper,
            pid_file=pid_file, mutex_name="", emit=messages.append)
        stale_lines = [m for m in messages if "is stale" in m]
        assert stale_lines, f"no stale classification logged: {messages}"
        assert str(dead) in stale_lines[0]
        assert "not running" in stale_lines[0]
        assert "mutex currently held" in stale_lines[0]

    def test_confirmation_timeout_is_best_effort(
            self, silent_helper, pid_file, cleanup_helpers):
        messages: list[str] = []
        status = kam.ensure_session_keep_awake(
            python=sys.executable, helper_script=silent_helper,
            pid_file=pid_file, mutex_name="", confirm_timeout=0.5,
            emit=messages.append)
        assert status == "confirmation-timeout"
        assert any("PID confirmation timed out" in m for m in messages)

    def test_helper_exiting_at_startup_is_best_effort(
            self, failing_helper, pid_file):
        messages: list[str] = []
        status = kam.ensure_session_keep_awake(
            python=sys.executable, helper_script=failing_helper,
            pid_file=pid_file, mutex_name="", confirm_timeout=2.0,
            emit=messages.append)
        assert status == "start-failed"
        assert any("exited during startup" in m for m in messages)

    def test_start_failure_is_best_effort(self, pid_file):
        status = kam.ensure_session_keep_awake(
            python=str(Path("/nonexistent/python")),
            helper_script="x.py", pid_file=pid_file, mutex_name="",
            emit=lambda _m: None)
        assert status == "start-failed"


# ---------------------------------------------------------------------------
# F. session-end behavior + marker cleanup
# ---------------------------------------------------------------------------


class TestSessionEnd:
    def test_not_started_after_session_end(self, fake_helper, pid_file):
        status = _ensure(helper_script=fake_helper, pid_file=pid_file,
                         until=_past_hhmm(5))
        assert status == "session-ended"
        assert not pid_file.exists()

    def test_stale_marker_removed_after_session_end(
            self, fake_helper, pid_file):
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{_dead_pid()}\n", encoding="ascii")
        status = _ensure(helper_script=fake_helper, pid_file=pid_file,
                         until=_past_hhmm(5))
        assert status == "session-ended"
        assert not pid_file.exists()

    def test_live_helper_not_touched_after_session_end(
            self, fake_helper, pid_file, cleanup_helpers):
        # The helper owns its own exit at --until; the manager must not
        # kill or replace a live helper even when called past session end.
        assert _ensure(helper_script=fake_helper, pid_file=pid_file) \
            == "started"
        pid, _ = kam.read_pid_marker(pid_file)
        status = _ensure(helper_script=fake_helper, pid_file=pid_file,
                         until=_past_hhmm(5))
        assert status == "already-running"
        assert kam.process_running(pid)


class TestKeepAwakePidFile:
    def test_write_contains_pid_and_token(self, tmp_path):
        marker = tmp_path / "s.pid"
        keep_awake._write_pid_file(str(marker))
        pid, _token = kam.read_pid_marker(marker)
        assert pid == os.getpid()

    def test_remove_only_when_owner(self, tmp_path):
        marker = tmp_path / "s.pid"
        keep_awake._write_pid_file(str(marker))
        keep_awake._remove_pid_file(str(marker))
        assert not marker.exists()

    def test_remove_leaves_foreign_marker(self, tmp_path):
        marker = tmp_path / "s.pid"
        marker.write_text("424242\ntoken\n", encoding="ascii")
        keep_awake._remove_pid_file(str(marker))
        assert marker.exists()  # belongs to another helper; never removed


# ---------------------------------------------------------------------------
# G. Windows-only machinery: graceful off-Windows + structural guarantees
# ---------------------------------------------------------------------------


class TestWindowsSpecifics:
    def test_mutex_unavailable_off_windows(self):
        if os.name == "nt":
            pytest.skip("Windows-only assertions run off-Windows here")
        assert kam.acquire_session_mutex("Local\\Test.KeepAwake") == (
            "unavailable", None)
        assert not kam.mutex_exists("Local\\Test.KeepAwake")
        kam.release_session_mutex(None)  # no-op, must not raise

    def test_helper_noop_off_windows(self, tmp_path):
        if os.name == "nt":
            pytest.skip("non-Windows behavior")
        marker = tmp_path / "s.pid"
        assert keep_awake.main(["--pid-file", str(marker),
                                "--until", "16:15"]) == 0
        assert not marker.exists()

    def test_helper_singleton_before_power_request(self):
        # On Windows a duplicate helper must exit BEFORE acquiring the
        # power request or writing the PID marker; verified structurally
        # within main() here (the mutex itself cannot be exercised
        # off-Windows).
        source = (WINDOWS_DIR / "keep_awake.py").read_text(encoding="utf-8")
        main_body = source[source.index("def main("):]
        acquire = main_body.index("acquire_session_mutex")
        power = main_body.index("_set_execution_state("
                                "ES_CONTINUOUS | ES_SYSTEM_REQUIRED)")
        assert acquire < power

    def test_launcher_uses_manager_not_tasklist(self):
        bat = (WINDOWS_DIR / "run_paper_trading_cycle.bat").read_text(
            encoding="utf-8")
        assert "keep_awake_manager.py" in bat
        # The old fragile identity check is gone (tasklist/find may remain
        # only in comments, never as a command).
        for line in bat.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("rem"):
                continue
            assert "tasklist" not in stripped
            assert "find.exe" not in stripped
        assert "keep_awake.py" in bat
        assert "--until" in bat and "--max-minutes" in bat

    def test_no_display_required_in_power_call(self):
        # ES_DISPLAY_REQUIRED must never be defined or passed; the screen
        # must stay allowed to turn off. Comments may MENTION it.
        source = (WINDOWS_DIR / "keep_awake.py").read_text(encoding="utf-8")
        assert "ES_DISPLAY_REQUIRED = 0x" not in source
        for line in source.splitlines():
            if "_set_execution_state(" in line:
                assert "ES_DISPLAY" not in line

    def test_power_request_flags_preserved(self):
        source = (WINDOWS_DIR / "keep_awake.py").read_text(encoding="utf-8")
        assert "ES_CONTINUOUS = 0x80000000" in source
        assert "ES_SYSTEM_REQUIRED = 0x00000001" in source
        assert "_set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)" \
            in source
        assert "_set_execution_state(ES_CONTINUOUS)" in source


# ---------------------------------------------------------------------------
# H. manager CLI: best-effort contract
# ---------------------------------------------------------------------------


class TestManagerCli:
    def test_cli_returns_zero_on_success(self, fake_helper, pid_file,
                                         cleanup_helpers):
        result = subprocess.run(
            [sys.executable, str(WINDOWS_DIR / "keep_awake_manager.py"),
             "--helper", str(fake_helper), "--pid-file", str(pid_file),
             "--mutex-name", ""],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0
        assert "keep-awake started for session" in result.stdout

    def test_cli_returns_zero_when_session_ended(self, fake_helper,
                                                 pid_file):
        past = _past_hhmm(5)
        result = subprocess.run(
            [sys.executable, str(WINDOWS_DIR / "keep_awake_manager.py"),
             "--helper", str(fake_helper), "--pid-file", str(pid_file),
             "--until", f"{past[0]:02d}:{past[1]:02d}", "--mutex-name", ""],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0
        assert "already reached; not starting" in result.stdout

    def test_cli_returns_zero_on_confirmation_timeout(self, silent_helper,
                                                      pid_file,
                                                      cleanup_helpers):
        result = subprocess.run(
            [sys.executable, str(WINDOWS_DIR / "keep_awake_manager.py"),
             "--helper", str(silent_helper), "--pid-file", str(pid_file),
             "--mutex-name", "", "--confirm-timeout", "0.5"],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0
        assert "PID confirmation timed out" in result.stdout
