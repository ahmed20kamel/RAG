# Scheduled maintenance: backups, a weekly restore drill, and the nightly quality gate.
#
# Runs as the current user; no Administrator window is needed. Each task runs even if
# the machine was off at the scheduled time, as soon as it is next on (StartWhenAvailable).
#
#   RAG Backup          daily 02:00    scripts\backup.py
#   RAG Backup Drill    Sunday 03:00   scripts\backup.py --drill
#   RAG Quality Gate    daily 03:30    scripts\quality_gate.py   (offline, core, retrieval)
#
# Results land in data\logs\backup.log, data\logs\backup_status.json and
# tests\eval\results\gate_latest.json; the monitoring page reads the backup status and
# raises an alert when the last good backup is older than BACKUP_MAX_AGE_HOURS.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-maintenance-tasks.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install-maintenance-tasks.ps1 -Remove

param(
    [switch]$Remove,
    [string]$BackupTime = '02:00',
    [string]$GateTime = '03:30'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Names = @('RAG Backup', 'RAG Backup Drill', 'RAG Quality Gate')

if ($Remove) {
    foreach ($name in $Names) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Output 'Removed the maintenance tasks.'
    return
}

if (-not (Test-Path $Python)) {
    Write-Error "No virtual environment at $Python. Create it first (see SETUP.md)."
}

function New-Task([string]$Name, [string]$Arguments, $Trigger, [int]$Hours) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours $Hours) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $settings -Principal $principal `
        -Description 'Knowledge assistant maintenance. See scripts\install-maintenance-tasks.ps1.' | Out-Null
    Write-Output "Scheduled: $Name"
}

New-Task 'RAG Backup' "`"$Root\scripts\backup.py`"" `
    (New-ScheduledTaskTrigger -Daily -At $BackupTime) 1
New-Task 'RAG Backup Drill' "`"$Root\scripts\backup.py`" --drill" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '03:00') 1
New-Task 'RAG Quality Gate' "`"$Root\scripts\quality_gate.py`"" `
    (New-ScheduledTaskTrigger -Daily -At $GateTime) 2

Write-Output ''
Write-Output 'Set BACKUP_MIRROR_DIR in .env to a second disk or an internal share:'
Write-Output 'a backup on the same disk as the data does not survive that disk.'
