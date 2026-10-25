#Requires -RunAsAdministrator
# The startup tasks of a machine that serves the system, as the media PC has them: Qdrant
# and the app as SYSTEM at boot (nobody needs to be signed in), the app reachable from
# the office network on its port, and the minute-by-minute update from GitHub.
# The nightly backup and review tasks are added by update.ps1 on its first run.
#
#   powershell -ExecutionPolicy Bypass -File scripts\deploy\install-server-tasks.ps1
param(
    [string]$Root = 'C:\RAG',
    [int]$AppPort = 8080,
    [int]$Workers = 6,
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'

function Register-BootTask($name, $command) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$command`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "  task '$name'"
}

New-Item -ItemType Directory -Force (Join-Path $Root 'data\logs') | Out-Null

# This machine reads documents faster than the media PC: more files at once.
$envFile = Join-Path $Root '.env'
$lines = [IO.File]::ReadAllLines($envFile)
$lines = $lines | ForEach-Object { if ($_ -like 'INGESTION_WORKERS=*') { "INGESTION_WORKERS=$Workers" } else { $_ } }
[IO.File]::WriteAllLines($envFile, $lines, (New-Object Text.UTF8Encoding($false)))

Register-BootTask 'RAG Qdrant' (Join-Path $Root 'start-qdrant.cmd')
Register-BootTask 'RAG Server' (Join-Path $Root 'start-server.cmd')

$update = Join-Path $Root 'scripts\deploy\update.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$update`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName 'RAG Update' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "  task 'RAG Update'"

# The deploy key, readable by SYSTEM and administrators only, or OpenSSH ignores it.
$key = Join-Path $Root '.deploy\id_ed25519'
if (Test-Path $key) {
    icacls $key /reset | Out-Null
    icacls $key /inheritance:r | Out-Null
    icacls $key /setowner '*S-1-5-18' | Out-Null
    icacls $key /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    $git = 'C:\Program Files\Git\cmd\git.exe'
    $deploy = (Join-Path $Root '.deploy') -replace '\\', '/'
    & $git -C $Root config core.sshCommand "C:/Windows/System32/OpenSSH/ssh.exe -i $deploy/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$deploy/known_hosts"
}

# The office network only: whatever the network profile, nothing from outside it.
Remove-NetFirewallRule -DisplayName 'RAG Knowledge Base (LAN)' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'RAG Knowledge Base (LAN)' -Direction Inbound -Protocol TCP -LocalPort $AppPort `
    -RemoteAddress LocalSubnet -Action Allow -Profile Any | Out-Null
Write-Host "  port $AppPort open to the office network"

if (-not $NoStart) {
    Start-ScheduledTask 'RAG Qdrant'
    Start-ScheduledTask 'RAG Server'
    Write-Host '  started'
}
