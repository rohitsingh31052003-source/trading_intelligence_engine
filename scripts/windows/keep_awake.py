#!/usr/bin/env python3
"""Session-scoped keep-awake power request for the paper-trading scheduler.

Automation utility only: this script contains NO trading logic. It raises a
Windows power request so S0 Modern Standby cannot suspend the system during
the 09:15-16:00 paper-trading session, while leaving the display free to
turn off.

Mechanism (Windows-native, no dependencies):
    SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

  * ES_SYSTEM_REQUIRED keeps the SYSTEM awake. ES_DISPLAY_REQUIRED is
    deliberately NOT used, so the screen may still turn off normally.
  * The request is scoped to THIS process: it is released when the script
    exits, and Windows releases it automatically even if the process is
    killed or crashes, so no request can leak after a crash / task kill.

Session lifecycle (controlled by run_paper_trading_cycle.bat):
    * The first cycle of the session starts ONE instance of this script
      with --until <session end> and --pid-file <marker>. Subsequent
      cycles see the live PID and leave it running.
    * The script exits by itself when the --until wall-clock time is
      reached (shortly after the final 16:00 cycle), or earlier if the
      optional stop file appears, and always before --max-minutes.
    * If the script crashes or is terminated, the PID file goes stale;
      the next scheduled cycle (<= 15 minutes later) starts a fresh
      instance. Windows has already released the power request.

Stdlib only. Does nothing (exits 0) on non-Windows platforms.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import time
from datetime import datetime
from pathlib import Path

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

POLL_SECONDS = 2.0


def _hhmm(text: str) -> tuple[int, int]:
    """argparse type: parse an 'HH:MM' wall-clock time (24h, local)."""

    try:
        hour_text, minute_text = text.strip().split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"{text!r} is not an HH:MM time")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise argparse.ArgumentTypeError(f"{text!r} is not an HH:MM time")
    return hour, minute


def _seconds_until(until: tuple[int, int], now: datetime | None = None) -> float:
    """Seconds from ``now`` until today's ``until`` time (negative if past)."""

    now = now or datetime.now()
    target = now.replace(hour=until[0], minute=until[1], second=0, microsecond=0)
    return (target - now).total_seconds()


def _set_execution_state(state: int) -> int:
    return ctypes.windll.kernel32.SetThreadExecutionState(state)  # type: ignore[attr-defined]


def _write_pid_file(pid_file: str) -> None:
    path = Path(pid_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="ascii")


def _remove_pid_file(pid_file: str) -> None:
    """Remove the pid file only if it still belongs to this process."""

    try:
        path = Path(pid_file)
        if path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop-file", default="",
                        help="Exit (releasing the request) once this file exists.")
    parser.add_argument("--pid-file", default="",
                        help="Write this process's PID here while running.")
    parser.add_argument("--until", type=_hhmm, default=None, metavar="HH:MM",
                        help="Session end: exit at this local wall-clock time. "
                             "If it has already passed today, exit immediately "
                             "WITHOUT acquiring the power request.")
    parser.add_argument("--max-minutes", type=float, default=20.0,
                        help="Hard cap: exit even without a stop file / until.")
    args = parser.parse_args(argv)

    if os.name != "nt":  # nothing to do outside Windows
        return 0

    budget_seconds = args.max_minutes * 60.0
    if args.until is not None:
        until_text = f"{args.until[0]:02d}:{args.until[1]:02d}"
        seconds = _seconds_until(args.until)
        if seconds <= 0:
            print(f"keep-awake: session end {until_text} already reached; "
                  "not starting (no power request acquired)")
            return 0
        budget_seconds = min(budget_seconds, seconds)

    previous = _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if previous == 0:  # per MSDN, 0 means the call failed
        print("keep-awake: SetThreadExecutionState failed; continuing without it")
    else:
        print("keep-awake: system power request ACTIVE "
              "(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)")

    if args.pid_file:
        _write_pid_file(args.pid_file)

    deadline = time.monotonic() + budget_seconds
    reason = "time limit reached"
    try:
        while time.monotonic() < deadline:
            if args.stop_file and os.path.exists(args.stop_file):
                reason = "stop file detected"
                break
            time.sleep(POLL_SECONDS)
        else:
            reason = "session end / time limit reached"
    except KeyboardInterrupt:
        reason = "interrupted"
    except Exception as exc:  # never let the helper break the trading cycle
        reason = f"unexpected error: {exc}"
    finally:
        if previous != 0:
            _set_execution_state(ES_CONTINUOUS)
        if args.pid_file:
            _remove_pid_file(args.pid_file)
        print(f"keep-awake: system power request RELEASED ({reason})")

    return 0  # keep-awake is best-effort; never fail the cycle


if __name__ == "__main__":
    raise SystemExit(main())
