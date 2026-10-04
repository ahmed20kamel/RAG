#Requires -RunAsAdministrator
# One-time: turns the installed copy in C:\RAG into a clone of the GitHub repository and
# keeps it updated on its own. Run once, as administrator, on the machine that serves the
# system:
#   powershell -ExecutionPolicy Bypass -File .\setup-media.ps1
#
# GitHub is reached with a deploy key: a key that belongs to this machine only and can
# read this one repository, nothing else. Nothing is stored that could push code.
param(
    [string]$Root = 'C:\RAG',
    [string]$Repo = 'git@github.com:ahmed20kamel/RAG.git',
    [string]$Branch = 'main'
)
$ErrorActionPreference = 'Stop'
$git = 'C:\Program Files\Git\cmd\git.exe'
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$keygen = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

Step '1. Git'
if (-not (Test-Path $git)) {
    winget install --id Git.Git -e --silent --scope machine --accept-package-agreements --accept-source-agreements
    if (-not (Test-Path $git)) { throw 'Git did not install. Install it from https://git-scm.com/download/win and run this again.' }
}
& $git --version

Step '2. A deploy key for this machine'
$deploy = Join-Path $Root '.deploy'
New-Item -ItemType Directory -Force $deploy | Out-Null
$key = Join-Path $deploy 'id_ed25519'
if (-not (Test-Path $key)) {
    cmd /c "`"$keygen`" -t ed25519 -q -N `"`" -C media-pc-01-deploy -f `"$key`""
}
# The update task runs as SYSTEM, and OpenSSH ignores a key that anyone else could
# read: owned by SYSTEM, readable by SYSTEM and administrators only.
# /reset first: ssh-keygen gives its creator an explicit entry that /grant:r keeps.
icacls $key /reset | Out-Null
icacls $key /inheritance:r | Out-Null
icacls $key /setowner 'NT AUTHORITY\SYSTEM' | Out-Null
icacls $key /grant:r 'NT AUTHORITY\SYSTEM:F' 'BUILTIN\Administrators:F' | Out-Null
Write-Host "`nAdd this key to GitHub: repository > Settings > Deploy keys > Add deploy key" -ForegroundColor Yellow
Write-Host "(title: media-pc-01, leave 'Allow write access' OFF)`n" -ForegroundColor Yellow
Get-Content "$key.pub"
Read-Host "`nPress Enter after adding the key on GitHub"

Step '3. Linking C:\RAG to the repository'
$sshCommand = "$($ssh -replace '\\','/') -i $($key -replace '\\','/') -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$(($deploy -replace '\\','/'))/known_hosts"
# The update task runs as SYSTEM, a different owner than whoever runs this script.
& $git config --system --add safe.directory ($Root -replace '\\', '/')
Push-Location $Root
if (-not (Test-Path (Join-Path $Root '.git'))) { & $git init --quiet; & $git remote add origin $Repo }
& $git config core.sshCommand $sshCommand
& $git fetch --quiet origin $Branch
if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'Could not read the repository. Was the deploy key added?' }
# Tracked files take the published version; the database, case files, .env and the
# vector store are not tracked, so they stay exactly as they are.
& $git reset --hard --quiet "origin/$Branch"
Pop-Location
Write-Host "  now at $(& $git -C $Root rev-parse --short HEAD)"

Step '4. Updating every minute'
$update = Join-Path $Root 'scripts\deploy\update.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$update`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName 'RAG Update' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Step '5. First update now'
& powershell -NoProfile -ExecutionPolicy Bypass -File $update -Force
Get-Content (Join-Path $Root 'data\logs\update.log') -Tail 5

Write-Host "`n== DONE" -ForegroundColor Green
Write-Host '  Every push to GitHub reaches this machine within a minute.'
Write-Host "  Log: $Root\data\logs\update.log"
