param([string]$App = 'zqu-workspace-worker', [string]$Fly = "$env:USERPROFILE/.fly/bin/fly.exe")
$ErrorActionPreference = 'Stop'
if ($App -ne 'zqu-workspace-worker') { throw 'This project preflight only accepts the isolated school worker app.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
& $Fly config validate --config (Join-Path $projectRoot 'fly.toml')
if ($LASTEXITCODE -ne 0) { throw 'Invalid worker configuration.' }
$appsText = & $Fly apps list --json
if ($LASTEXITCODE -ne 0) { throw 'Unable to audit existing apps.' }
$apps = $appsText | ConvertFrom-Json
$existing = @($apps | Where-Object name -EQ $App)
if ($existing.Count -eq 0) {
    Write-Output 'BLOCKED BY USER APPROVAL: the independent worker app/machine/volume do not exist. No resources were created.'
    exit 2
}
$machineText = & $Fly machines list --app $App --json
if ($LASTEXITCODE -ne 0) { throw 'Unable to audit machines.' }
$volumeText = & $Fly volumes list --app $App --json
if ($LASTEXITCODE -ne 0) { throw 'Unable to audit persistent volumes.' }
$machines = @($machineText | ConvertFrom-Json)
$volumes = @($volumeText | ConvertFrom-Json)
[pscustomobject]@{ App = $App; Machines = $machines.Count; Volumes = $volumes.Count; ReadOnly = $true } | ConvertTo-Json
if ($machines.Count -gt 1 -or $volumes.Count -ne 1) { throw 'Expected one persistent volume and at most one worker machine. Audit before deployment.' }
Write-Output 'Read-only preflight passed. This is not authorization to create, deploy, scale, or activate.'
