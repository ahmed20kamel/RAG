# One-time setup so the assistant is there without anybody starting it.
#
# RUN THIS AS ADMINISTRATOR. Everything here needs it, and nothing else in the project
# does — the app itself runs as an ordinary user.
#
# Three things are arranged:
#
#   1. The firewall lets the office reach port 8080, on the private profile only. If
#      this machine is ever on a public network the rule does not apply there.
#   2. Docker's own Windows service is set to start automatically, so the Qdrant
#      container comes back after a restart without Docker Desktop being opened by hand.
#   3. A scheduled task runs the server.
#
# About the trigger. Ollama, which provides embeddings, runs as a tray application in a
# user session — there is no Windows service for it — so the task is triggered at logon
# rather than at boot. A machine that reboots and sits at the login screen will not
# serve anything. If this is the office's server machine, enable automatic sign-in for
# it, or leave it signed in.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1 -Remove

param(
    [int]$Port = 8080,
    [string]$TaskName = 'RAG Knowledge Base',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'serve.ps1'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error 'Run this from an Administrator PowerShell window.'
    }
}

Assert-Administrator

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName 'RAG Knowledge Base (LAN)' -ErrorAction SilentlyContinue
    Write-Output 'Removed the scheduled task and the firewall rule.'
    Write-Output 'Docker was left set to start automatically; change it back if you want to.'
    return
}

# 1. the firewall -----------------------------------------------------------
Remove-NetFirewallRule -DisplayName 'RAG Knowledge Base (LAN)' -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -DisplayName 'RAG Knowledge Base (LAN)' `
    -Description 'Inbound access to the company knowledge assistant from the office network.' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -Profile Private -Enabled True | Out-Null
Write-Output "Firewall: port $Port open on the private profile."

# 2. Docker, so the index survives a restart --------------------------------
$docker = Get-Service -Name 'com.docker.service' -ErrorAction SilentlyContinue
if ($docker) {
    Set-Service -Name 'com.docker.service' -StartupType Automatic
    if ($docker.Status -ne 'Running') { Start-Service -Name 'com.docker.service' }
    Write-Output 'Docker: service set to start automatically.'
} else {
    Write-Warning 'Docker service not found. Qdrant will only come back when Docker Desktop is opened.'
}

# 3. the scheduled task -----------------------------------------------------
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Restart on failure, and never stop a long-running server for taking too long.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

# The user's own account, not SYSTEM: the app reads this profile's virtual environment
# and talks to Ollama in this session.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Serves the company knowledge assistant on the office network.' | Out-Null

Write-Output "Scheduled task '$TaskName': runs at sign-in for $env:USERNAME."
Write-Output ''
Write-Output 'Done. To start it now without signing out:'
Write-Output "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Output ''
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Select-Object -First 1).IPAddress
Write-Output "Staff open:  http://${ip}:$Port"
