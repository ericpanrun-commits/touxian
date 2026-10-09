param([int]$Port = 8473, [switch]$FirewallOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$interfaces = @()
if (-not $FirewallOnly) { foreach ($adapter in @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up')) {
    if ($adapter.InterfaceDescription -match 'VPN|Tailscale|Hyper-V|Virtual|TAP|Tunnel|WireGuard') { continue }
    $profile = @(Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue) | Select-Object -First 1
    $gateway = @(Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue) | Select-Object -First 1
    if (-not $gateway) { continue }
    foreach ($address in @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
        $interfaces += @{id = [string]$adapter.ifIndex; name = $adapter.Name; address = $address.IPAddress; profile = if ($profile) { [string]$profile.NetworkCategory } else { 'Unknown' }}
    }
} }
$firewall = @{private_enabled = $null; public_enabled = $null; matching_private_allow = $false; checked = $false}
if ($FirewallOnly) { try {
    $firewall.private_enabled = [bool](Get-NetFirewallProfile -Name Private).Enabled
    $firewall.public_enabled = [bool](Get-NetFirewallProfile -Name Public).Enabled
    $filters = @(Get-NetFirewallPortFilter -ErrorAction Stop | Where-Object { [string]$_.Protocol -eq 'TCP' -and @($_.LocalPort) -contains [string]$Port })
    foreach ($filter in $filters) {
        foreach ($rule in @($filter | Get-NetFirewallRule -ErrorAction Stop)) {
            if ([string]$rule.Enabled -eq 'True' -and [string]$rule.Direction -eq 'Inbound' -and [string]$rule.Action -eq 'Allow' -and [string]$rule.Profile -eq 'Private') { $firewall.matching_private_allow = $true }
        }
    }
    $firewall.checked = $true
} catch { } }
@{interfaces = $interfaces; firewall = $firewall} | ConvertTo-Json -Depth 5 -Compress
