# Starts the knowledge assistant for the office, once its dependencies are actually up.
#
# The app needs three things, and on this machine none of them is a Windows service:
# Qdrant runs in a container under Docker Desktop, embeddings come from the Ollama
# tray app, and generation comes from another machine on the network. All of those
# arrive some seconds or minutes after the computer does.
#
# So this waits instead of assuming. A server that starts before its index exists comes
# up looking healthy and fails every question, which is worse than one that is briefly
# not there yet: the first looks like a broken product, the second like a slow start.
#
# Run by the scheduled task, or by hand:  powershell -File scripts\serve.ps1

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$LogDir = Join-Path $Root 'data\logs'
$Log = Join-Path $LogDir ('serve-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))

# How long to keep waiting for a dependency before starting anyway. Starting anyway is
# deliberate: the app reports an unreachable service on its health page, and that is more
# use to whoever is looking into it than a task that quietly never ran.
$WaitSeconds = 300
$PollSeconds = 5

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = '{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Read-DotEnv([string]$Path) {
    # Only to discover the port for the readiness check — the app reads .env itself.
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $name, $value = $line -split '=', 2
        $values[$name.Trim()] = $value.Trim()
    }
    return $values
}

function Wait-ForEndpoint([string]$Name, [string]$Url, [int]$Timeout) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    $announced = $false
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing | Out-Null
            Write-Log "$Name is up."
            return $true
        } catch {
            if (-not $announced) {
                Write-Log "waiting for $Name at $Url ..."
                $announced = $true
            }
            Start-Sleep -Seconds $PollSeconds
        }
    }
    Write-Log "$Name did not answer within ${Timeout}s — starting anyway; the health page will show it as unreachable."
    return $false
}

Write-Log '--- starting the knowledge assistant ---'

if (-not (Test-Path $Python)) {
    Write-Log "FATAL: no virtual environment at $Python"
    exit 1
}

$settings = Read-DotEnv (Join-Path $Root '.env')
$qdrant = if ($settings.ContainsKey('QDRANT_URL')) { $settings['QDRANT_URL'] } else { 'http://localhost:6333' }
$embeddings = if ($settings.ContainsKey('EMBEDDING_BASE_URL') -and $settings['EMBEDDING_BASE_URL']) {
    $settings['EMBEDDING_BASE_URL']
} else {
    'http://localhost:11434'
}

# Docker Desktop brings the container back on its own — the container is set to restart
# unless it was stopped on purpose — so this waits for the result rather than issuing
# a start of its own.
Wait-ForEndpoint -Name 'Qdrant' -Url "$qdrant/readyz" -Timeout $WaitSeconds | Out-Null
Wait-ForEndpoint -Name 'Ollama (embeddings)' -Url "$embeddings/api/tags" -Timeout $WaitSeconds | Out-Null

Set-Location $Root
$env:ENABLE_KNOWLEDGE_LAYER = 'true'
$env:PYTHONIOENCODING = 'utf-8'

$host_ = if ($settings.ContainsKey('APP_HOST')) { $settings['APP_HOST'] } else { '0.0.0.0' }
$port = if ($settings.ContainsKey('APP_PORT')) { $settings['APP_PORT'] } else { '8080' }
Write-Log "serving on http://${host_}:${port}  (log: $Log)"

# Launched through cmd rather than a PowerShell pipeline, on purpose.
#
# Uvicorn writes its ordinary startup lines to stderr, and PowerShell 5.1 wraps every
# stderr line from a native program in an error record. With ErrorActionPreference set
# to Stop, the server announcing itself was enough to kill the script that had just
# started it. Redirecting at the shell level keeps one combined log and leaves
# PowerShell out of the stream entirely.
$ErrorActionPreference = 'Continue'
$command = '"{0}" -u "{1}" >> "{2}" 2>&1' -f $Python, (Join-Path $Root 'run.py'), $Log
& cmd.exe /c $command

Write-Log "the server exited with code $LASTEXITCODE"
exit $LASTEXITCODE
