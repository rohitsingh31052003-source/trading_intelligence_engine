<#
.SYNOPSIS
    Registers (or updates) the Windows Task Scheduler job that runs the
    EXISTING paper-trading cycle every 15 minutes during the Indian equity
    market session (Mon-Fri, 09:15 -> 16:00 local PC time).

.DESCRIPTION
    Automation only. This script does NOT modify any trading strategy,
    decision, geometry, risk, lifecycle, or paper-trading logic. It merely
    schedules scripts\windows\run_paper_trading_cycle.bat, which in turn
    runs the existing scripts\run_paper_trading_cycle.py operator CLI.

    Schedule model (Task Scheduler, PC local time - the trading PC is
    expected to be set to IST):
      * Days:        Monday - Friday
      * First run:   -MarketOpen (default 09:15, NSE session open)
      * Repetition:  every -IntervalMinutes (default 15)
      * Last run:    MarketOpen + -SessionMinutes (default 405 min -> 16:00;
                     covers the final 15m candle closing 15:30 plus provider
                     lag)

    Overlap protection (defense in depth):
      * Task Scheduler "If the task is already running: Do not start a new
        instance" (MultipleInstances = IgnoreNew)
      * Hard per-run time limit (default 14 min < 15 min interval)
      * The .bat launcher also holds an atomic lock directory.

    NO REAL ORDERS ARE SENT - PAPER TRADING ONLY.

.PARAMETER TaskName
    Name of the scheduled task (default TradingIntelligence.PaperTradingCycle).

.PARAMETER ProjectRoot
    Repository root containing scripts\run_paper_trading_cycle.py.
    Defaults to the parent of this script's directory.

.PARAMETER MarketOpen
    First run of the day, HH:mm PC-local time (default 09:15 = NSE open IST).
    If this PC is NOT set to IST, pass the equivalent local time.

.PARAMETER IntervalMinutes
    Repetition interval in minutes (default 15).

.PARAMETER SessionMinutes
    Length of the daily repetition window in minutes (default 405 = 6h45m,
    i.e. 09:15 -> 16:00).

.PARAMETER ExecutionTimeLimitMinutes
    Hard kill for a single run (default 14). Must stay below IntervalMinutes
    so a hung cycle can never block the next one.

.PARAMETER WakeToRun
    Opt-in. Also registers "Wake the computer to run this task". OFF by
    default: this script deliberately makes NO power-related changes unless
    you explicitly ask for them. The PC must stay powered on regardless.

.PARAMETER RunWhetherLoggedOnOrNot
    Opt-in. Registers the task with logon type S4U so it can run when no
    user is logged on. OFF by default (runs only while you are logged on).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\Install-PaperTradingTask.ps1

.EXAMPLE
    # PC not in IST (e.g. UTC): NSE 09:15 IST == 03:45 UTC
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\Install-PaperTradingTask.ps1 -MarketOpen 03:45
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "TradingIntelligence.PaperTradingCycle",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$MarketOpen = "09:15",
    [ValidateRange(1, 720)][int]$IntervalMinutes = 15,
    [ValidateRange(30, 720)][int]$SessionMinutes = 405,
    [ValidateRange(1, 60)][int]$ExecutionTimeLimitMinutes = 14,
    [switch]$WakeToRun,
    [switch]$RunWhetherLoggedOnOrNot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($SessionMinutes -le $IntervalMinutes) {
    throw "SessionMinutes ($SessionMinutes) must be greater than IntervalMinutes ($IntervalMinutes)."
}
if ($ExecutionTimeLimitMinutes -ge $IntervalMinutes) {
    throw "ExecutionTimeLimitMinutes ($ExecutionTimeLimitMinutes) must be below IntervalMinutes ($IntervalMinutes) so a hung cycle can never overlap the next one."
}
$openAt = [datetime]::ParseExact($MarketOpen, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)

$launcher = Join-Path $ProjectRoot "scripts\windows\run_paper_trading_cycle.bat"
$cycleCli = Join-Path $ProjectRoot "scripts\run_paper_trading_cycle.py"
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
foreach ($required in @($launcher, $cycleCli)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required file not found: $required"
    }
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Warning "Project virtualenv not found at $venvPython - the launcher will fall back to 'python' on PATH. Create the venv or ensure the right interpreter is on PATH."
}

Write-Host "Project root : $ProjectRoot"
Write-Host "Launcher     : $launcher"
Write-Host "Python (venv): $(if (Test-Path -LiteralPath $venvPython) { $venvPython } else { '(not found - PATH fallback)' })"

# --- Trigger: Mon-Fri at MarketOpen, repeat every N minutes for the session
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At $openAt

# Repetition pattern: nested assignment ($trigger.Repetition.Interval = ...)
# fails on PowerShell 7 because the returned Repetition CimInstance is a
# detached copy (PropertyNotFound). The compatible approach (5.1 AND 7) is
# to build the repetition via a temporary -Once trigger -- the only
# parameter set exposing -RepetitionInterval/-RepetitionDuration -- and
# assign the WHOLE .Repetition object onto the weekly trigger.
$repetitionSource = New-ScheduledTaskTrigger -Once -At $openAt `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Minutes $SessionMinutes)
$trigger.Repetition = $repetitionSource.Repetition

# --- Action: run the launcher via cmd.exe from the project root
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$launcher`"`"" `
    -WorkingDirectory $ProjectRoot

# --- Settings: no overlapping instances, bounded run time, catch-up run
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $ExecutionTimeLimitMinutes) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
if ($WakeToRun) { $settings.WakeToRun = $true }

# --- Principal: current user; interactive unless S4U requested
$logonType = if ($RunWhetherLoggedOnOrNot) { "S4U" } else { "Interactive" }
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType $logonType `
    -RunLevel Limited

$description = @"
Runs the existing paper-trading cycle (scripts\run_paper_trading_cycle.py)
every $IntervalMinutes minutes during the Indian equity session
(Mon-Fri $MarketOpen -> $($openAt.AddMinutes($SessionMinutes).ToString('HH:mm')) PC-local time).
DASHBOARD_PROVIDER=yahoo. PAPER TRADING ONLY - no real orders are sent.
"@

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $trigger `
        -Action $action `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force | Out-Null
}

$lastRun = $openAt.AddMinutes($SessionMinutes).ToString("HH:mm")
Write-Host ""
Write-Host "Scheduled task registered:" -ForegroundColor Green
Write-Host "  Name            : $TaskName"
Write-Host "  Days            : Monday - Friday (PC-local time)"
Write-Host "  First run       : $MarketOpen  (NSE open, IST)"
Write-Host "  Every           : $IntervalMinutes minutes"
Write-Host "  Last run        : $lastRun  (covers the 15:30 close + provider lag)"
Write-Host "  Overlap policy  : IgnoreNew (a running cycle is never duplicated)"
Write-Host "  Per-run limit   : $ExecutionTimeLimitMinutes minutes"
Write-Host "  Wake PC         : $([bool]$WakeToRun)"
Write-Host "  Logon type      : $logonType"
Write-Host ""
Write-Host "Manage:"
Write-Host "  Enable   : Enable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Disable  : Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Run now  : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Remove   : scripts\windows\Remove-PaperTradingTask.ps1"
Write-Host "  Logs     : $ProjectRoot\logs\paper_trading\"
