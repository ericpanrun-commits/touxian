# Explicitly authorized Phase 0 experiment. No WeChat operations run elevated.
[CmdletBinding()]
param(
    [switch]$InspectOnly,
    [ValidateRange(300, 1800)][int]$LeaseSeconds = 1200
)
$ErrorActionPreference = 'Stop'
$experimentRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../artifacts/pyweixin-spike/qt-accessibility-pass/force-uia-controlled-test'))
$null = [IO.Directory]::CreateDirectory($experimentRoot)
$registryPath = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accessibility'
$registryName = 'ForceUIAForQt'

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
    $json = ConvertTo-Json -InputObject $Record -Depth 12
    $temporary = Join-Path $experimentRoot ($Name + '.tmp')
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporary, (Join-Path $experimentRoot $Name), $true)
}

$before = [ordered]@{
    captured_at = [DateTimeOffset]::Now.ToString('o')
    hive = 'HKEY_LOCAL_MACHINE'
    subkey = $registryPath
    value_name = $registryName
    view_64 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
    view_32 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry32)
}
if ($InspectOnly) {
    Write-Record 'inspection.json' $before
    $before | ConvertTo-Json -Depth 8
    exit 0
}

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This narrow registry experiment requires Windows administrator approval.'
}
if ($before.view_64.key_exists -or $before.view_64.value_exists) {
    throw 'Original registry state changed since approval; no write was performed.'
}
if ([IO.File]::Exists((Join-Path $experimentRoot 'registry-before.json'))) {
    throw 'A previous experiment record exists; refusing to overwrite its backup.'
}
Write-Record 'registry-before.json' $before
$status = [ordered]@{
    state = 'starting'
    pid = $PID
    started_at = [DateTimeOffset]::Now.ToString('o')
    lease_expires_at = [DateTimeOffset]::Now.AddSeconds($LeaseSeconds).ToString('o')
    hklm_value_writes = 0
    windows_restart = $false
    messages_sent = 0
}
Write-Record 'status.json' $status
$hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
$creationAttempted = $false
try {
    $creationAttempted = $true
    $key = $hive.CreateSubKey($registryPath, $true)
    try {
        # The approved scope is exactly one DWORD in the 64-bit registry view.
        $key.SetValue($registryName, 1, [Microsoft.Win32.RegistryValueKind]::DWord)
        $status.hklm_value_writes = 1
    } finally { $key.Dispose() }
    $status.applied = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
    if ($status.applied.kind -ne 'DWord' -or $status.applied.value -ne 1) { throw 'Readback mismatch' }
    $status.state = 'applied'
    Write-Record 'status.json' $status
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($deadline.Elapsed.TotalSeconds -lt $LeaseSeconds) {
        if ([IO.File]::Exists((Join-Path $experimentRoot 'finish.flag'))) {
            $status.finish_reason = 'parent_requested_rollback'
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $status.Contains('finish_reason')) { $status.finish_reason = 'lease_expired' }
} catch {
    $status.error_type = $_.Exception.GetType().FullName
    $status.error = $_.Exception.Message
} finally {
    try {
        if ($creationAttempted) {
            $key = $hive.OpenSubKey($registryPath, $true)
            if ($null -ne $key) {
                try {
                    if ($key.GetValueNames() -contains $registryName) {
                        if ($key.GetValueKind($registryName) -ne [Microsoft.Win32.RegistryValueKind]::DWord -or $key.GetValue($registryName) -ne 1) {
                            throw 'Rollback conflict: the value was independently changed; preserving it.'
                        }
                        $key.DeleteValue($registryName, $false)
                    }
                    $empty = $key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0
                } finally { $key.Dispose() }
                # Nonrecursive removal only. Preserve unrelated values/subkeys.
                if ($empty) { $hive.DeleteSubKey($registryPath, $false) }
            }
        }
        $status.after_64 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry64)
        $status.after_32 = Read-RegistryState ([Microsoft.Win32.RegistryView]::Registry32)
        $status.restoration_matches = -not $status.after_64.key_exists -and -not $status.after_64.value_exists -and
            ((ConvertTo-Json $status.after_32 -Compress) -eq (ConvertTo-Json $before.view_32 -Compress))
        $status.state = if ($status.restoration_matches) { 'rolled_back' } else { 'rollback_incomplete' }
    } catch {
        $status.state = 'rollback_error'
        $status.rollback_error = $_.Exception.Message
    } finally {
        $hive.Dispose()
        $status.finished_at = [DateTimeOffset]::Now.ToString('o')
        Write-Record 'status.json' $status
    }
}
