#Requires -RunAsAdministrator
# One-time: makes this machine (Rag-pc) the model server for the knowledge system.
# Run once, as administrator, on Rag-pc:
#   powershell -ExecutionPolicy Bypass -File .\setup-rag-pc.ps1
#
# What it does, and nothing else:
#   1. reports the graphics card, so the model size can be chosen for it;
#   2. installs Ollama and runs it as a startup task, so it serves with nobody logged in;
#   3. opens the model port to the two machines that use it, and to no one else;
#   4. downloads the models the system uses;
#   5. lets the administrator laptop manage this machine over SSH with its key only
#      (no passwords), from that laptop's address only;
#   6. stops the machine from sleeping: a server that sleeps stops answering.
param(
    # The machines allowed to use the model: the media PC that serves the system and
    # the administrator laptop.
    [string[]]$Clients = @('192.168.1.16', '192.168.1.26'),
    [string]$Admin = '192.168.1.26',
    [string]$AdminKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP2SKzJdeWP2qeDwj5jEvJTWXg4V+0xHs5UwaqkxR4nJ rag-laptop-admin',
    [string[]]$Models = @('qwen3.8:27b', 'qwen2.5vl:7b', 'bge-m3:latest'),
    [string]$ModelDir = 'C:\ollama\models'
)
$ErrorActionPreference = 'Stop'
$report = Join-Path ([Environment]::GetFolderPath('Desktop')) 'rag-pc-setup.txt'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan; Add-Content $report "`n== $text" }
function Note($text) { Write-Host "  $text"; Add-Content $report "  $text" }
Set-Content $report "Rag-pc setup $(Get-Date -Format 'yyyy-MM-dd HH:mm')"

Step '1. Graphics card'
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | ForEach-Object { Note $_ }
} else {
    Get-CimInstance Win32_VideoController | ForEach-Object { Note "$($_.Name) (driver $($_.DriverVersion))" }
    Note 'nvidia-smi not found: install the NVIDIA driver before using the card.'
}
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.*' } | ForEach-Object { Note "IP: $($_.IPAddress)" }

Step '2. Ollama'
function Find-Ollama {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        'C:\Program Files\Ollama\ollama.exe',
        (Get-Command ollama -ErrorAction SilentlyContinue).Source
    )) { if ($candidate -and (Test-Path $candidate)) { return $candidate } }
    return $null
}
$ollama = Find-Ollama
if (-not $ollama) {
    winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
    $ollama = Find-Ollama
}
if (-not $ollama) {
    $setup = Join-Path $env:TEMP 'OllamaSetup.exe'
    Invoke-WebRequest 'https://ollama.com/download/OllamaSetup.exe' -OutFile $setup
    Start-Process $setup -ArgumentList '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES' -Wait
    $ollama = Find-Ollama
}
if (-not $ollama) { throw 'Ollama did not install. Install it from https://ollama.com/download and run this again.' }
# Copied out of the installing user's profile, so the startup task does not depend on it.
$serverDir = 'C:\ollama\bin'
New-Item -ItemType Directory -Force $serverDir, $ModelDir | Out-Null
Copy-Item (Join-Path (Split-Path $ollama) '*') $serverDir -Recurse -Force
$server = Join-Path $serverDir 'ollama.exe'
Note "Ollama: $(& $server --version 2>&1 | Select-Object -Last 1)"

# Listens on the network; keeps the answer model and the picture model loaded together
# (the card has the room), so a picture no longer waits for a long answer to finish.
$settings = @{
    OLLAMA_HOST              = '0.0.0.0:11434'
    OLLAMA_MODELS            = $ModelDir
    OLLAMA_KEEP_ALIVE        = '30m'
    OLLAMA_MAX_LOADED_MODELS = '2'
    OLLAMA_FLASH_ATTENTION   = '1'
}
foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Machine') }

# One server only: the tray app would start a second one at every login.
Get-Process 'ollama app', 'ollama' -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item "$([Environment]::GetFolderPath('Startup'))\Ollama.lnk" -ErrorAction SilentlyContinue
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Ollama' -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $server -Argument 'serve'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'Ollama Server' -Action $action -Trigger $trigger -Principal $principal -Settings $taskSettings -Force | Out-Null
Start-ScheduledTask -TaskName 'Ollama Server'
$up = $false
foreach ($i in 1..30) {
    try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep 2 }
}
if (-not $up) { throw 'The Ollama server did not start. See Task Scheduler > Ollama Server.' }
Note 'Ollama server running at startup, as SYSTEM'

Step '3. Firewall'
Remove-NetFirewallRule -DisplayName 'RAG Ollama' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'RAG Ollama' -Direction Inbound -Protocol TCP -LocalPort 11434 `
    -RemoteAddress $Clients -Action Allow -Profile Any | Out-Null
Note "Port 11434 open to: $($Clients -join ', ')"

Step '4. Models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
foreach ($model in $Models) {
    Note "Downloading $model ..."
    & $server pull $model
    if ($LASTEXITCODE -ne 0) { Note "FAILED: $model" }
}
& $server list | ForEach-Object { Note $_ }

Step '5. Remote management (SSH, key only, from the administrator laptop)'
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
if ($capability.State -ne 'Installed') { Add-WindowsCapability -Online -Name $capability.Name | Out-Null }
Set-Service sshd -StartupType Automatic
Start-Service sshd
$keys = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content $keys $AdminKey -Encoding ascii
# By SID: the group names differ on a Windows installed in another language.
icacls $keys /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
$config = 'C:\ProgramData\ssh\sshd_config'
$text = Get-Content $config -Raw
$text = $text -replace '(?m)^#?\s*PasswordAuthentication\s+\w+', 'PasswordAuthentication no'
if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = "PasswordAuthentication no`r`n" + $text }
Set-Content $config $text -Encoding ascii
Restart-Service sshd
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
Remove-NetFirewallRule -DisplayName 'RAG SSH' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'RAG SSH' -Direction Inbound -Protocol TCP -LocalPort 22 `
    -RemoteAddress $Admin -Action Allow -Profile Any | Out-Null
Note "SSH: key only, from $Admin only; user $env:USERNAME"

Step '6. No sleep'
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Note 'Sleep and hibernation off on mains power'

Write-Host "`n== DONE" -ForegroundColor Green
Write-Host "  Report: $report"
