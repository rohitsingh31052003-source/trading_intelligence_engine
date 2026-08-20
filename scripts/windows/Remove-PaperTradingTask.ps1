<#
.SYNOPSIS
    Disables or completely removes the paper-trading cycle scheduled task.

.DESCRIPTION
    Automation management only; touches nothing in the trading system.

    Default behaviour: UNREGISTERS the task entirely.
    Use -DisableOnly to keep the registration but stop all future runs
    (re-enable later with Enable-ScheduledTask).

.EXAMPLE
    # Temporarily stop the schedule (keeps the task registered)
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\Remove-PaperTradingTask.ps1 -DisableOnly

.EXAMPLE
    # Remove the task completely
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\Remove-PaperTradingTask.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "TradingIntelligence.PaperTradingCycle",
    [switch]$DisableOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Scheduled task '$TaskName' is not registered. Nothing to do."
    exit 0
}

if ($DisableOnly) {
    if ($PSCmdlet.ShouldProcess($TaskName, "Disable scheduled task")) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    Write-Host "Scheduled task '$TaskName' DISABLED (still registered)."
    Write-Host "Re-enable with: Enable-ScheduledTask -TaskName '$TaskName'"
} else {
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Write-Host "Scheduled task '$TaskName' REMOVED."
    Write-Host "Re-create with: scripts\windows\Install-PaperTradingTask.ps1"
}
