# Explicitly approved: retain exactly one DWORD through a USER-initiated reboot.
# No shutdown/reboot, scheduled task, WeChat UI, login or message operation.
[CmdletBinding()]
param([ValidateSet('Inspect', 'Apply', 'Restore')][string]$Action = 'Inspect')
$ErrorActionPreference = 'Stop'
$experimentRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../artifacts/pyweixin-spike/force-uia-reboot-experiment'))
$null = [IO.Directory]::CreateDirectory($experimentRoot)
$registryPath = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accessibility'
$registryName = 'ForceUIAForQt'
$backupPath = Join-Path $experimentRoot 'registry-before.json'
$statusPath = Join-Path $experimentRoot 'status.json'

function Read-RegistryState([Microsoft.Win32.RegistryView]$View) {
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $View)
    $key = $null
    try {
        $key = $hive.OpenSubKey($registryPath, $false)
        $result = [ordered]@{ key_exists = $null -ne $key; value_exists = $false }
        if ($null -ne $key -and $key.GetValueNames() -contains $registryName) {
            $result.value_exists = $true
            $result.kind = [string]$key.GetValueKind($registryName)
            $result.value = $key.GetValue($registryName)
        }
        return $result
    } finally {
        if ($null -ne $key) { $key.Dispose() }
        $hive.Dispose()
    }
}

function Write-Record([string]$Name, $Record) {
    $temporary = Join-Path $experimentRoot ($Name + '.tmp')
    [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject $Record -Depth 12), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporary, (Join-Path $experimentRoot $Name), $true)
}

function Remove-ExperimentValue($Original) {
    if ($Original.view_64.value_exists) { throw 'Original value was not absent; refusing this recovery path.' }
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        $key = $hive.OpenSubKey($registryPath, $true)
        if ($null -ne $key) {
            try {
                if ($key.GetValueNames() -contains $registryName) {
                    if ($key.GetValueKind($registryName) -ne [Microsoft.Win32.RegistryValueKind]::DWord -or $key.GetValue($registryName) -ne 1) {
                        throw 'Value changed externally; preserving it and reporting a restore conflict.'
                    }
                    $key.DeleteValue($registryName, $false)
                }
                $empty = $key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0
            } finally { $key.Dispose() }
            # Remove only a key created by this experiment, and only if empty.
            if (-not $Original.view_64.key_exists -and $empty) { $hive.DeleteSubKey($registryPath, $false) }
        }
    } finally { $hive.Dispose() }
}

$snapshot = [ordered]@{
    at = [DateTimeOffset]::Now.ToString('o')
    last_boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')
    hive = 'HKEY_LOCAL_MACHINE'
    subkey = $registryPath
    value_name = $registryName
    view_64 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
    view_32 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry32)
}
if ($Action -eq 'Inspect') {
    Write-Record 'inspection.json' $snapshot
    $snapshot | ConvertTo-Json -Depth 8
    exit 0
}
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Windows administrator authorization is required for this one-value experiment.'
}

if ($Action -eq 'Apply') {
    if ([IO.File]::Exists($backupPath)) { throw 'Existing experiment backup: refusing to overwrite or reapply.' }
    if ($snapshot.view_64.value_exists) { throw 'The value already exists; no registry mutation was performed.' }
    Write-Record 'registry-before.json' $snapshot
    $status = [ordered]@{
        state = 'applying'
        applied_at = [DateTimeOffset]::Now.ToString('o')
        applied_by_pid = $PID
        boot_before = $snapshot.last_boot
        hklm_value_writes = 0
        scope = 'Keep one 64-bit DWORD until user reboot and read-only retest, then restore.'
        automatic_windows_reboot = $false
        messages_sent = 0
    }
    Write-Record 'status.json' $status
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        $key = $hive.CreateSubKey($registryPath, $true)
        try { $key.SetValue($registryName, 1, [Microsoft.Win32.RegistryValueKind]::DWord) }
        finally { $key.Dispose() }
        $status.hklm_value_writes = 1
        $status.actual_64 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
        if ($status.actual_64.kind -ne 'DWord' -or $status.actual_64.value -ne 1) { throw 'Registry readback mismatch' }
        $status.state = 'applied_pending_manual_reboot'
    } catch {
        $status.error = $_.Exception.Message
        try { Remove-ExperimentValue $snapshot; $status.state = 'apply_failed_restored' }
        catch { $status.state = 'restore_error'; $status.restore_error = $_.Exception.Message }
    } finally {
        $hive.Dispose()
        Write-Record 'status.json' $status
    }
    $status | ConvertTo-Json -Depth 10
    if ($status.state -ne 'applied_pending_manual_reboot') { exit 1 }
    exit 0
}

if (-not [IO.File]::Exists($backupPath) -or -not [IO.File]::Exists($statusPath)) {
    throw 'No experiment journal exists; no registry mutation was performed.'
}
$original = [IO.File]::ReadAllText($backupPath) | ConvertFrom-Json
$status = [IO.File]::ReadAllText($statusPath) | ConvertFrom-Json -AsHashtable
if ($status.state -eq 'restored') { $status | ConvertTo-Json -Depth 10; exit 0 }
if ($status.state -notin @('applying', 'applied_pending_manual_reboot', 'restore_error')) {
    throw 'The experiment is not in a recoverable applied state.'
}
try {
    Remove-ExperimentValue $original
    $status.after_64 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
    $status.after_32 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry32)
    $status.restoration_matches = ((ConvertTo-Json $status.after_64 -Compress) -eq (ConvertTo-Json $original.view_64 -Compress)) -and
        ((ConvertTo-Json $status.after_32 -Compress) -eq (ConvertTo-Json $original.view_32 -Compress))
    $status.state = if ($status.restoration_matches) { 'restored' } else { 'restored_with_unrelated_key_changes_preserved' }
} catch {
    $status.state = 'restore_error'
    $status.restore_error = $_.Exception.Message
} finally {
    $status.restored_at = [DateTimeOffset]::Now.ToString('o')
    Write-Record 'status.json' $status
}
$status | ConvertTo-Json -Depth 10
if ($status.state -ne 'restored') { exit 1 }
