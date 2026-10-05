# Brings an installed copy up to date with GitHub. Run every minute by the
# "RAG Update" task; does nothing when there is nothing new.
#
# Only code moves. The database, the case files, .env and the vector store are ignored by
# git, so a reset to the published code never touches them. A new version that does not
# start is rolled back to the one that did, and the log says so.
param(
    [string]$Root = 'C:\RAG',
    [string]$Branch = 'main',
    [int]$AppPort = 8080,
    [switch]$Force
)
$ErrorActionPreference = 'Continue'
$git = 'C:\Program Files\Git\cmd\git.exe'
$py = Join-Path $Root '.venv\Scripts\python.exe'
$log = Join-Path $Root 'data\logs\update.log'
function Log($message) { "$(Get-Date -Format 's') $message" | Add-Content -Path $log -Encoding UTF8 }

function Restart-Server {
    Stop-ScheduledTask -TaskName 'RAG Server' -ErrorAction SilentlyContinue
    # Stopping the task ends its cmd.exe; the Python it started has to be ended by name.
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*run.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-NetTCPConnection -State Listen -LocalPort $AppPort -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-ScheduledTask -TaskName 'RAG Server'
    for ($i = 0; $i -lt 90; $i++) {
        try {
            $page = Invoke-WebRequest "http://127.0.0.1:$AppPort/" -UseBasicParsing -TimeoutSec 5
            if ($page.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep 2
    }
    return $false
}

function Apply($changed) {
    if ($changed -match '^requirements\.txt$') {
        & $py -m pip install --quiet -r (Join-Path $Root 'requirements.txt')
        Log "  libraries updated (pip exit $LASTEXITCODE)"
    }
    if ($changed -match '^migrations/') {
        Push-Location $Root
        & $py -m alembic upgrade head | Out-Null
        Log "  database migrated (alembic exit $LASTEXITCODE)"
        Pop-Location
    }
}

Set-Location $Root

# The nightly jobs install themselves: whatever this machine is missing is registered
# here, so a new job reaches it with the code, not with a visit.
function Ensure-Task($name, $trigger, $arguments, $logName) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { return }
    $logFile = Join-Path $Root "data\logs\$logName"
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$py`" $arguments >> `"$logFile`" 2>&1`"" -WorkingDirectory $Root
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Log "  scheduled task '$name' installed"
}
Ensure-Task 'RAG Backup' (New-ScheduledTaskTrigger -Daily -At 2:00am) 'scripts/backup.py' 'backup.log'
Ensure-Task 'RAG Backup Drill' (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3:00am) 'scripts/backup.py --drill' 'backup.log'
Ensure-Task 'RAG Nightly' (New-ScheduledTaskTrigger -Daily -At 2:30am) 'scripts/nightly_review.py' 'nightly.log'

# Settings live in .env, which git never carries. A format the code learns to read is
# added to the machine's allowed list here, once; the server restarts to pick it up.
$configChanged = $false
function Ensure-AllowedExtensions([string[]]$wanted) {
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) { return }
    $lines = [IO.File]::ReadAllLines($envFile)
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -notlike 'ALLOWED_EXTENSIONS=*') { continue }
        $current = ($lines[$i] -replace '^ALLOWED_EXTENSIONS=', '').Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        $missing = $wanted | Where-Object { $current -notcontains $_ }
        if (-not $missing) { return }
        $lines[$i] = 'ALLOWED_EXTENSIONS=' + (($current + $missing) -join ',')
        [IO.File]::WriteAllLines($envFile, $lines, (New-Object Text.UTF8Encoding($false)))
        Log "  allowed formats extended: $($missing -join ',')"
        $script:configChanged = $true
        return
    }
}
Ensure-AllowedExtensions @('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp')

# Git writes to stderr; caught as text so the log says why a fetch failed.
$fetch = & cmd /c "`"$git`" fetch --quiet origin $Branch 2>&1"
if ($LASTEXITCODE -ne 0) { Log "fetch from GitHub failed: $(($fetch | Select-Object -Last 2) -join ' ')"; exit 1 }
$current = (& $git rev-parse --verify --quiet HEAD)
$published = (& $git rev-parse "origin/$Branch")
if (-not $Force -and $current -eq $published) {
    if ($configChanged) {
        if (Restart-Server) { Log '  restarted for the new settings' } else { Log '  RESTART FOR NEW SETTINGS FAILED - check data\logs\server.log' }
    }
    exit 0
}

# A restart ends every answer in progress. While someone is waiting on one, the update
# waits for the next minute - up to ten times, so an always-busy server still updates.
$deferFile = Join-Path $Root 'data\logs\update-deferred.txt'
$deferred = if (Test-Path $deferFile) { [int](Get-Content $deferFile -Raw) } else { 0 }
$busy = $false
try { $busy = (Invoke-RestMethod "http://127.0.0.1:$AppPort/api/system/busy" -TimeoutSec 5).busy } catch { }
if ($busy -and -not $Force -and $deferred -lt 10) {
    Set-Content -Path $deferFile -Value ($deferred + 1)
    if ($deferred -eq 0) { Log "update to $published waiting: a question is being answered" }
    exit 0
}
Remove-Item $deferFile -ErrorAction SilentlyContinue

$changed = if ($current) { @(& $git diff --name-only $current $published) } else { @('requirements.txt', 'migrations/') }
Log "updating $current -> $published ($($changed.Count) files changed)"
& $git reset --hard --quiet "origin/$Branch"
Apply $changed

# One-time data repairs travel with the code too. Each runs once; its marker is written
# only when it succeeded, so a failed run is tried again on the next update.
$repair = Join-Path $Root 'scripts\fix_legacy_data.py'
$repairDone = Join-Path $Root 'data\logs\legacy-fix-done.txt'
if ((Test-Path $repair) -and -not (Test-Path $repairDone)) {
    Push-Location $Root
    & cmd /c "`"$py`" `"$repair`" --apply >> `"$(Join-Path $Root 'data\logs\legacy_fix.log')`" 2>&1"
    if ($LASTEXITCODE -eq 0) { Set-Content -Path $repairDone -Value (Get-Date -Format 's'); Log '  legacy data repaired (see data\reports\legacy_fix.md)' }
    else { Log "  legacy data repair failed (exit $LASTEXITCODE) - see data\logs\legacy_fix.log" }
    Pop-Location
}

if (Restart-Server) {
    Log "  running $published"
    exit 0
}

Log "  new version did not start; rolling back to $current"
if ($current) {
    & $git reset --hard --quiet $current
    if (Restart-Server) { Log "  rolled back, running $current" } else { Log '  ROLLBACK DID NOT START EITHER - check data\logs\server.log' }
}
exit 1
