#!/usr/bin/env python3
"""Session keep-awake MANAGER for the paper-trading scheduler (Windows).

Automation utility only: this script contains NO trading logic. It is the
single place that decides whether the session keep-awake helper
(``keep_awake.py``) needs to be (re)started, replacing the previous
batch-file logic built on ``tasklist.exe`` / ``find.exe``.

Why this exists (root cause of the repeated helper restarts):
    * With a Python virtual environment, ``.venv\\Scripts\\python.exe`` is a
      redirector that spawns the REAL base interpreter (e.g.
      ``C:\\Python314\\python.exe``) as a CHILD process. Both processes
      expose the SAME command line, so any identity check based on matching
      the command line (tasklist / WMIC / Get-CimInstance) sees TWO
      processes and can capture the redirector's PID. The redirector exits
      once the real interpreter runs, so the recorded PID goes stale and
      the next cycle starts a REPLACEMENT helper even though the real
      helper is still alive.
    * ``tasklist /FI "PID eq X" | find "python"`` proves only that SOME
      python process owns that PID. Windows recycles PIDs, so a stale PID
      file can alias an unrelated process (false "alive"), and it cannot
      tell the venv redirector from the real interpreter.
    * ``start "..." /min`` returns immediately; the PID file is written
      asynchronously by the child, so a launcher-side read/confirm races.

Design (simplest robust fix):
    1. The helper is spawned with the REAL base interpreter
       (``sys.base_executable``), NOT the venv redirector
       (``.venv\\Scripts\\python.exe``). The Python venv launcher is a shim
       whose OWN lifetime governs the real child interpreter it spawns:
       when the shim exits, the child dies too. That parent/child duality
       - not the PID check - was the real cause of helpers dying silently
       shortly after each cycle and being replaced as "stale". The
       helper script uses only stdlib imports, so no venv activation is
       required and spawning the base interpreter directly is safe.
    2. The PID file is written ONLY by the helper itself, using
       ``os.getpid()`` plus that process's start-time token. Identity is
       verified STRICTLY by PID + start time via ``OpenProcess`` /
       ``GetProcessTimes`` (POSIX: ``os.kill(pid, 0)`` + ``/proc``). A
       marker that carries a token is NEVER downgraded to a PID-only
       liveness check.
    3. The helper additionally holds a NAMED WINDOWS MUTEX for the whole
       session. If a second helper is ever started (detection failure,
       concurrent launch, manual run), it sees the mutex is already held
       and exits immediately WITHOUT acquiring the power request, so there
       can never be two active keep-awake holders.
    4. The helper is started DETACHED (no console, new process group,
       breakaway from the Task Scheduler job object where permitted), and
       its interpreter is a single native process, so it is independent of
       the launcher: it survives the cycle and cannot be killed by a
       console Ctrl+C / window close that terminates the trading cycle.
    5. After starting, the manager waits (bounded) for the helper-written
       PID file and confirms the recorded PID is genuinely alive, logging
       the full determination detail when a marker is classified stale.

Best-effort: ``main`` ALWAYS returns 0 so the paper-trading cycle itself
can never fail because of keep-awake management.

Stdlib only. Safe (no-op start) on non-Windows platforms.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

#: Named session mutex: the OS-level singleton guarantee. Held by the live
#: helper for its whole lifetime; auto-released by Windows on any exit.
DEFAULT_MUTEX_NAME = r"Local\TradingIntelligence.KeepAwake.Session"

# Windows API constants.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_QUERY_INFORMATION = 0x0400
_ERROR_ALREADY_EXISTS = 183
_ERROR_FILE_NOT_FOUND = 2
_STILL_ACTIVE = 259
_SYNCHRONIZE = 0x00100000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_CONFIRM_POLL_SECONDS = 0.25


# ---------------------------------------------------------------------------
# Process identity (Windows-native, dependency-free; POSIX fallback for tests)
# ---------------------------------------------------------------------------


def _posix_state(pid: int) -> str | None:
    """Single-letter /proc state ("R"/"S"/"Z"/...), or None if unreadable."""

    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return data[data.rindex(")") + 2]
    except (OSError, ValueError, IndexError):
        return None


def process_running(pid: int) -> bool:
    """True when a process with ``pid`` currently exists.

    A POSIX ZOMBIE (terminated but not yet reaped by its parent) counts as
    NOT running: a killed helper that lingers as a zombie has already
    released its power request and mutex, so it must be replaced, not
    mistaken for alive.
    """

    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            # OpenProcess alone cannot be trusted: a terminated process is
            # still openable while ANY handle to it exists (e.g. the
            # subprocess handle of a finished child). The exit code is the
            # authoritative liveness signal.
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)):
                return True  # uncertain -> assume alive (safe)
            if exit_code.value != _STILL_ACTIVE:
                return False
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:  # POSIX: signal 0 probes existence without delivering a signal
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if _posix_state(pid) == "Z":
        return False
    return True


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32)]


def _windows_start_token(pid: int) -> str | None:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        # A terminated process is still openable while ANY handle to it
        # exists (e.g. the subprocess handle a parent retains for a
        # finished child), and GetProcessTimes would still return its
        # creation time. A dead process has no usable identity, so it
        # must yield None (mirrors the POSIX zombie handling).
        exit_code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            if exit_code.value != _STILL_ACTIVE:
                return None
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel_time = _FILETIME()
        user_time = _FILETIME()
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time))
        if not ok:
            return None
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _posix_start_token(pid: int) -> str | None:
    if _posix_state(pid) == "Z":  # zombies have no usable identity
        return None
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    # Field 2 (comm) is parenthesised and may contain spaces/parens, so
    # parse from the final ')'. starttime is field 22 -> index 19 here.
    try:
        rest = data[data.rindex(")") + 2:].split()
    except ValueError:
        return None
    if len(rest) <= 19:
        return None
    return rest[19]


def process_start_token(pid: int) -> str | None:
    """A token uniquely identifying THIS process instance (its start time).

    Two processes that reuse the same PID have different start times, so a
    marker recording PID + token cannot alias a recycled PID. Returns None
    when the platform cannot supply a start time (checks then degrade to
    PID liveness only).
    """

    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_start_token(pid)
    return _posix_start_token(pid)


# ---------------------------------------------------------------------------
# Session singleton mutex (Windows only; no-op elsewhere)
# ---------------------------------------------------------------------------


def acquire_session_mutex(name: str) -> tuple[str, int | None]:
    """Create/open the named session mutex.

    Returns ``("acquired", handle)`` when THIS process now holds the
    singleton, ``("already-exists", None)`` when another live process holds
    it, and ``("unavailable", None)`` off-Windows or on API failure (the
    caller must then fall back to PID-file detection alone). The caller
    MUST keep the handle alive for the duration of the session; Windows
    releases it automatically on process exit, so a stale mutex is
    impossible.
    """

    if os.name != "nt":
        return "unavailable", None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        return "unavailable", None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return "already-exists", None
    return "acquired", handle


def release_session_mutex(handle: int | None) -> None:
    if not handle or os.name != "nt":
        return
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def mutex_exists(name: str) -> bool:
    """True when the named session mutex currently exists (helper alive)."""

    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


# ---------------------------------------------------------------------------
# PID marker file
# ---------------------------------------------------------------------------


def read_pid_marker(pid_file: str | Path) -> tuple[int | None, str]:
    """Read the marker. Returns (pid or None, start token or "")."""

    try:
        text = Path(pid_file).read_text(encoding="ascii")
    except OSError:
        return None, ""
    lines = text.splitlines()
    try:
        pid = int(lines[0].strip())
    except (IndexError, ValueError):
        return None, ""
    token = lines[1].strip() if len(lines) > 1 else ""
    return pid, token


def helper_alive_pid(pid_file: str | Path) -> int | None:
    """Return the live helper PID, or None when the marker is stale/absent.

    STRICT identity:
    * A marker whose recorded PID has terminated is stale - even if the
      PID number has since been recycled by an unrelated process.
    * When the marker carries a start-time token, the token MUST be
      obtainable AND must match; a marker with a token is never
      downgraded to a PID-only liveness check (PID reuse must never
      alias the helper).
    * A marker WITHOUT a token (legacy / hand-written) can only be
      accepted via PID liveness alone; the helper always writes a token.
    """

    pid, marker_token, _status, _reason = describe_marker(pid_file)
    return pid if _status == "alive" else None


def describe_marker(pid_file: str | Path
                    ) -> tuple[int | None, str, str, str]:
    """Diagnostic snapshot of the marker's identity determination.

    Returns ``(pid, marker_token, status, reason)`` where status is one of
    ``"alive"`` / ``"stale"`` / ``"absent"`` and ``reason`` is a compact,
    non-sensitive audit string explaining every decision step (PID,
    marker token, OpenProcess result, observed token, token comparison).
    """

    pid, marker_token = read_pid_marker(pid_file)
    if pid is None:
        return None, marker_token, "absent", "no readable PID marker"
    if not process_running(pid):
        return (pid, marker_token, "stale",
                f"pid {pid} not running (OpenProcess failed or the "
                f"process exited)")
    if marker_token:
        observed = process_start_token(pid)
        if observed is None:
            return (pid, marker_token, "stale",
                    f"pid {pid} running but its start-time token could "
                    f"not be verified (marker token {marker_token}); "
                    "refusing a PID-only identity")
        if observed != marker_token:
            return (pid, marker_token, "stale",
                    f"pid {pid} identity mismatch: marker token "
                    f"{marker_token} != observed token {observed} "
                    "(PID recycled by another process)")
        return (pid, marker_token, "alive",
                f"pid {pid} running; start-time token matches "
                f"({marker_token})")
    return (pid, marker_token, "alive",
            f"pid {pid} running; marker carries no start-time token "
            "(accepted by PID liveness only)")


def resolve_helper_python(python: str | None = None) -> str:
    """Interpreter used to spawn the helper.

    Prefer the REAL base interpreter (``sys.base_executable`` on Python
    3.11+): spawning a venv redirector (``.venv\\Scripts\\python.exe``)
    creates the helper as a CHILD of that shim, and the shim's exit kills
    the helper - exactly the silent-death mode that produced repeated
    restarts. The helper imports only stdlib + its sibling module, so it
    needs no venv activation.
    """

    if python:
        return python
    return getattr(sys, "base_executable", None) or sys.executable


def remove_pid_marker(pid_file: str | Path) -> None:
    try:
        Path(pid_file).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Detached start
# ---------------------------------------------------------------------------


def start_detached(cmd: list[str], helper_log: str | Path | None = None,
                   warnings: list[str] | None = None) -> subprocess.Popen:
    """Start ``cmd`` fully detached from this process and its console.

    Windows: DETACHED_PROCESS (no console -> immune to console Ctrl+C /
    window close that can kill the trading cycle) + new process group, and
    a best-effort breakaway from the Task Scheduler job object so a
    task-level time limit cannot take the helper down with the cycle.
    POSIX: start_new_session (used by the cross-platform tests only).
    """

    stream = None
    kwargs: dict = {"stdin": subprocess.DEVNULL, "close_fds": True}
    if helper_log:
        Path(helper_log).parent.mkdir(parents=True, exist_ok=True)
        stream = open(helper_log, "a", encoding="utf-8")
        kwargs["stdout"] = stream
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        if os.name == "nt":
            base_flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
            try:
                return subprocess.Popen(
                    cmd, creationflags=base_flags | _CREATE_BREAKAWAY_FROM_JOB,
                    **kwargs)
            except OSError:
                if warnings is not None:
                    warnings.append(
                        "job-object breakaway unavailable; helper started "
                        "without it (it is still console-detached)")
                return subprocess.Popen(cmd, creationflags=base_flags, **kwargs)
        kwargs["start_new_session"] = True
        return subprocess.Popen(cmd, **kwargs)
    finally:
        if stream is not None:
            stream.close()


# ---------------------------------------------------------------------------
# Time helpers (kept local so this script stays standalone)
# ---------------------------------------------------------------------------


def _hhmm(text: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = text.strip().split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"{text!r} is not an HH:MM time")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise argparse.ArgumentTypeError(f"{text!r} is not an HH:MM time")
    return hour, minute


def seconds_until(until: tuple[int, int], now: datetime | None = None) -> float:
    now = now or datetime.now()
    target = now.replace(hour=until[0], minute=until[1],
                         second=0, microsecond=0)
    return (target - now).total_seconds()


def _fmt_until(until: tuple[int, int]) -> str:
    return f"{until[0]:02d}:{until[1]:02d}"


# ---------------------------------------------------------------------------
# The one decision: reuse, replace or start
# ---------------------------------------------------------------------------


def ensure_session_keep_awake(
    *,
    python: str | None = None,
    helper_script: str | Path,
    pid_file: str | Path,
    until: tuple[int, int] | None = None,
    max_minutes: float = 600.0,
    mutex_name: str = DEFAULT_MUTEX_NAME,
    confirm_timeout: float = 20.0,
    helper_log: str | Path | None = None,
    emit=print,
) -> str:
    """Guarantee exactly one live session keep-awake helper.

    Returns one of: ``"already-running"``, ``"started"``,
    ``"session-ended"``, ``"confirmation-timeout"``, ``"start-failed"``.
    Never raises; the trading cycle must never fail because of this.
    """

    marker = Path(pid_file)
    old_pid, marker_token, status, reason = describe_marker(marker)
    mutex_held = bool(mutex_name) and mutex_exists(mutex_name)
    if status == "alive" and old_pid is not None:
        emit(f"keep-awake already running (pid {old_pid}; {reason})")
        return "already-running"

    if until is not None and seconds_until(until) <= 0:
        emit(f"keep-awake: session end {_fmt_until(until)} already reached; "
             "not starting")
        if marker.exists():
            remove_pid_marker(marker)
        return "session-ended"

    if status == "stale" and old_pid is not None:
        emit(f"keep-awake PID {old_pid} is stale ({reason}; session "
             f"mutex currently held: {mutex_held}); replacing")
    if marker.exists():
        remove_pid_marker(marker)

    emit("starting session keep-awake helper")
    cmd = [resolve_helper_python(python), str(helper_script),
           "--pid-file", str(marker),
           "--max-minutes", f"{max_minutes:g}"]
    if until is not None:
        cmd += ["--until", _fmt_until(until)]
    if mutex_name:
        cmd += ["--mutex-name", mutex_name]

    try:
        proc = start_detached(cmd, helper_log)
    except OSError as exc:
        emit(f"WARNING: could not start keep-awake helper ({exc}); "
             "continuing without it")
        return "start-failed"

    deadline = time.monotonic() + max(confirm_timeout, 0.0)
    while True:
        confirmed_pid, _tok, confirm_status, confirm_reason = describe_marker(
                marker)
        if confirm_status == "alive" and confirmed_pid is not None:
            until_text = (f"until {_fmt_until(until)}" if until is not None
                          else "no session end")
            emit(f"keep-awake started for session ({until_text}), "
                 f"pid {confirmed_pid} ({confirm_reason})")
            return "started"
        exit_code = proc.poll()
        if exit_code is not None:
            # The child exited before confirming. When another helper holds
            # the session mutex, the child correctly refused to duplicate;
            # the existing helper remains authoritative.
            if mutex_name and mutex_exists(mutex_name):
                emit("keep-awake already running "
                     "(session mutex held by the live helper)")
                return "already-running"
            emit(f"WARNING: keep-awake helper exited during startup "
                 f"(exit code {exit_code}); continuing without it")
            return "start-failed"
        if time.monotonic() >= deadline:
            break
        time.sleep(_CONFIRM_POLL_SECONDS)

    if mutex_name and mutex_exists(mutex_name):
        # The helper is alive (holds the mutex) but slow to write its
        # marker; treat it as running rather than risking a duplicate.
        emit("keep-awake already running "
             "(session mutex held by the live helper)")
        return "already-running"
    _pid, _tok, _status, final_reason = describe_marker(marker)
    emit(f"WARNING: keep-awake started but PID confirmation timed out "
         f"({final_reason}); continuing")
    return "confirmation-timeout"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", required=True,
                        help="Path to keep_awake.py.")
    parser.add_argument("--pid-file", required=True,
                        help="Session keep-awake PID marker file.")
    parser.add_argument("--until", type=_hhmm, default=None, metavar="HH:MM",
                        help="Session end (local wall-clock).")
    parser.add_argument("--max-minutes", type=float, default=600.0,
                        help="Helper hard cap in minutes.")
    parser.add_argument("--mutex-name", default=DEFAULT_MUTEX_NAME,
                        help="Named session mutex (empty disables).")
    parser.add_argument("--confirm-timeout", type=float, default=20.0,
                        help="Seconds to wait for the helper PID marker.")
    parser.add_argument("--helper-log", default="",
                        help="Append the helper's own output here.")
    parser.add_argument("--python", default="",
                        help="Explicit interpreter for the helper (default: "
                             "the base interpreter - never the venv shim).")
    args = parser.parse_args(argv)

    def emit(message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] {message}", flush=True)

    try:
        ensure_session_keep_awake(
            python=args.python or None,
            helper_script=args.helper,
            pid_file=args.pid_file,
            until=args.until,
            max_minutes=args.max_minutes,
            mutex_name=args.mutex_name,
            confirm_timeout=args.confirm_timeout,
            helper_log=args.helper_log or None,
            emit=emit,
        )
    except Exception as exc:  # best-effort: never fail the trading cycle
        emit(f"WARNING: keep-awake manager error ({exc}); continuing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
