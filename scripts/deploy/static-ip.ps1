#Requires -RunAsAdministrator
# Gives this machine a fixed address, so the media PC, the laptop and the tunnel always
# find it. Keeps the address it already has (the router gave it, so nothing else uses
# it) and goes back to automatic addressing by itself if the network is not reachable
# afterwards: run over SSH, a mistake here must not leave the machine unreachable.
#
# Run from a scheduled task, not from the SSH session it may interrupt:
#   powershell -ExecutionPolicy Bypass -File static-ip.ps1
param(
    [string]$Interface = 'Ethernet',
    [string]$Address = '192.168.1.62',
    [int]$Prefix = 24,
    [string]$Gateway = '192.168.1.1',
    [string[]]$Dns = @('192.168.1.1', '1.1.1.1'),
    [string]$Log = 'C:\ragsetup\static-ip.log'
)
function Log($text) { "$(Get-Date -Format s) $text" | Add-Content $Log -Encoding UTF8 }

function Reachable {
    if (Test-Connection $Gateway -Count 2 -Quiet) { return $true }
    try { Resolve-DnsName github.com -Server $Gateway -DnsOnly -ErrorAction Stop | Out-Null; return $true } catch { return $false }
}

Log "fixing $Interface at $Address/$Prefix via $Gateway"
try {
    Set-NetIPInterface -InterfaceAlias $Interface -Dhcp Disabled -ErrorAction Stop
    Get-NetIPAddress -InterfaceAlias $Interface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
    Get-NetRoute -InterfaceAlias $Interface -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
    New-NetIPAddress -InterfaceAlias $Interface -IPAddress $Address -PrefixLength $Prefix -DefaultGateway $Gateway -ErrorAction Stop | Out-Null
    Set-DnsClientServerAddress -InterfaceAlias $Interface -ServerAddresses $Dns
    Start-Sleep 5
    if (-not (Reachable)) { throw 'the gateway does not answer' }
    Log 'fixed address in place'
} catch {
    Log "FAILED ($_); back to automatic addressing"
    Set-NetIPInterface -InterfaceAlias $Interface -Dhcp Enabled
    Set-DnsClientServerAddress -InterfaceAlias $Interface -ResetServerAddresses
    ipconfig /renew | Out-Null
}
