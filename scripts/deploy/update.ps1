# Brings an installed copy up to date with GitHub. Run every few minutes by the
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
& $git fetch --quiet origin $Branch
if ($LASTEXITCODE -ne 0) { Log 'fetch from GitHub failed'; exit 1 }
$current = (& $git rev-parse --verify --quiet HEAD)
$published = (& $git rev-parse "origin/$Branch")
if (-not $Force -and $current -eq $published) { exit 0 }

$changed = if ($current) { @(& $git diff --name-only $current $published) } else { @('requirements.txt', 'migrations/') }
Log "updating $current -> $published ($($changed.Count) files changed)"
& $git reset --hard --quiet "origin/$Branch"
Apply $changed

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
