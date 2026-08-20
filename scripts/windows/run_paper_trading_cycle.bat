@echo off
rem ==========================================================================
rem  Paper-Trading Cycle Launcher (Windows)
rem
rem  Runs the EXISTING operator CLI (scripts\run_paper_trading_cycle.py)
rem  exactly once. This launcher is orchestration only: it does NOT modify
rem  any trading strategy / decision / geometry / risk / lifecycle logic.
rem
rem  What it guarantees:
rem    1. Runs from the PROJECT ROOT regardless of the caller's working
rem       directory (the paper-trade store defaults to .\paper_trades).
rem    2. Uses the project's .venv interpreter when present, else python.
rem    3. Sets DASHBOARD_PROVIDER=yahoo (live / near-live data).
rem    4. Prevents overlapping instances via an atomic lock directory.
rem    5. Appends all output to a per-day log file under logs\paper_trading.
rem    6. Writes a trace line BEFORE anything can fail, so scheduled-task
rem       failures are always diagnosable.
rem    7. Holds a session-scoped keep-awake power request (system only, NOT
rem       display) from the first cycle until just after the final cycle.
rem
rem  Any extra arguments are passed through to run_paper_trading_cycle.py
rem  (e.g. --instruments NIFTY,RELIANCE --capital 100000 --risk-percent 1).
rem
rem  NO REAL ORDERS ARE SENT - PAPER TRADING ONLY.
rem
rem  NOTE: this file MUST keep Windows CRLF line endings. Unix LF endings
rem  make cmd.exe fail to resolve goto labels ("The system cannot find the
rem  batch label specified") and abort with exit code 1 before any logging.
rem ==========================================================================
setlocal EnableExtensions DisableDelayedExpansion

rem --- Resolve the project root from this script's location -----------------
rem     (this file lives in <root>\scripts\windows\)
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%..\.."
if errorlevel 1 (
    echo [%DATE% %TIME%] FATAL: cannot enter project root from "%SCRIPT_DIR%" >> "%SCRIPT_DIR%launcher_fatal.log"
    exit /b 1
)
set "PROJECT_ROOT=%CD%"

rem --- Log directory + per-day log file (set up before anything can fail) ---
rem     The log date stamp uses only the built-in %DATE% variable (sanitised
rem     for filename use). Do NOT shell out to 'powershell' here: under Task
rem     Scheduler PATH is minimal and the bare 'powershell' command may not
rem     resolve, which previously aborted the launcher with errorlevel 9009.
set "LOG_DIR=%PROJECT_ROOT%\logs\paper_trading"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "TODAY=%DATE:/=-%"
set "TODAY=%TODAY: =_%"
if not defined TODAY set "TODAY=unknown"
set "LOG_FILE=%LOG_DIR%\paper_trading_cycle_%TODAY%.log"
echo [%DATE% %TIME%] launcher invoked (project root: %PROJECT_ROOT%) >> "%LOG_FILE%"

rem --- Overlap prevention (atomic lock directory) ---------------------------
rem     mkdir fails with errorlevel 1 if the directory already exists.
set "LOCK_DIR=%LOG_DIR%\cycle.lock"
if exist "%LOCK_DIR%" goto check_stale_lock
goto acquire_lock

:check_stale_lock
rem A stale lock can survive a crash / killed run. Clear it only when it is
rem clearly stale (older than 20 minutes; the scheduled task is limited to
rem 14 minutes, so 20 minutes is a safe margin). Uses the ABSOLUTE %WINDIR%
rem path to the built-in Windows PowerShell 5.1 executable (always present
rem on Windows; PATH is unreliable under Task Scheduler). If PowerShell
rem cannot run, assume STALE: an active cycle finishes within 14 minutes
rem anyway, and task-level IgnoreNew already prevents a duplicate start.
rem QUOTING NOTE: inside for /f ('...') the executable path must NOT be
rem wrapped in quotes - cmd /c then misapplies its strip-outer-quotes rule
rem and mangles the line ("...powershell.exe" -NoProfile -Command "if is
rem not recognized"). The path contains no spaces, so it stays unquoted;
rem only the PowerShell -Command argument is quoted.
set "PS_EXE=%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "LOCK_STATE=STALE"
if exist "%PS_EXE%" (
    for /f %%i in ('%PS_EXE% -NoProfile -Command "if ((Get-Date) - (Get-Item -LiteralPath '%LOCK_DIR%').CreationTime -gt [TimeSpan]::FromMinutes(20)) {'STALE'} else {'ACTIVE'}"') do set "LOCK_STATE=%%i"
)
if /i "%LOCK_STATE%"=="STALE" rmdir "%LOCK_DIR%" 2>nul

:acquire_lock
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 goto skip_overlap
goto run_cycle

:skip_overlap
echo [%DATE% %TIME%] SKIP: previous paper-trading cycle still running >> "%LOG_DIR%\paper_trading_cycle_overlap.log"
popd
exit /b 0

:run_cycle
rem --- Environment ----------------------------------------------------------
set "DASHBOARD_PROVIDER=yahoo"

rem --- Python interpreter: project .venv first, PATH fallback ---------------
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

rem --- Session keep-awake (ONE helper for the whole 09:15-16:00 session) ----
rem     The first cycle of the day starts ONE keep-awake helper holding
rem     ES_CONTINUOUS|ES_SYSTEM_REQUIRED (deliberately NOT ES_DISPLAY_REQUIRED:
rem     the screen may still turn off). It runs CONTINUOUSLY across all cycles
rem     and stops itself at SESSION_END (just after the final 16:00 cycle,
rem     whose Task Scheduler execution limit is 14 minutes). If the helper
rem     crashes or is killed, Windows auto-releases the power request on
rem     process exit and the next cycle (<= 15 minutes later) starts a fresh
rem     instance - detected via the PID file + tasklist. No power plan is
rem     modified; nothing persists after the helper exits.
set "SESSION_END=16:15"
set "AWAKE_PID_FILE=%LOG_DIR%\session_keep_awake.pid"
if not exist "%AWAKE_PID_FILE%" goto awake_start
set /p AWAKE_PID=<"%AWAKE_PID_FILE%"
if not defined AWAKE_PID goto awake_start
"%WINDIR%\System32\tasklist.exe" /FI "PID eq %AWAKE_PID%" /NH 2>nul | "%WINDIR%\System32\find.exe" /I "python" >nul
if errorlevel 1 goto awake_start
echo [%DATE% %TIME%] keep-awake already running (pid %AWAKE_PID%) >> "%LOG_FILE%"
goto awake_done

:awake_start
if exist "%AWAKE_PID_FILE%" del /f /q "%AWAKE_PID_FILE%" 2>nul
start "keep-awake" /min "%PYTHON%" "%SCRIPT_DIR%keep_awake.py" --pid-file "%AWAKE_PID_FILE%" --until %SESSION_END% --max-minutes 600
echo [%DATE% %TIME%] keep-awake started for session (until %SESSION_END%) >> "%LOG_FILE%"

:awake_done
rem --- Run ONE cycle, appending stdout + stderr to the log ------------------
echo [%DATE% %TIME%] cycle start (provider: %DASHBOARD_PROVIDER%, python: %PYTHON%) >> "%LOG_FILE%"
"%PYTHON%" "%PROJECT_ROOT%\scripts\run_paper_trading_cycle.py" %* >> "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] cycle finished, exit code %RC% >> "%LOG_FILE%"

rem --- Release the lock ------------------------------------------------------
rmdir "%LOCK_DIR%" 2>nul
popd
exit /b %RC%
