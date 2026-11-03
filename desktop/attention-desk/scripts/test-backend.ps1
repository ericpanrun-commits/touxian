param([string]$Python = $env:ERIC_PYTHON)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Python) {
    $candidate = Join-Path (Split-Path -Parent $projectRoot) '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $candidate) { $Python = $candidate }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Set ERIC_PYTHON to the Python environment containing the project dependencies.' }
$evidenceRoot = Join-Path $projectRoot 'artifacts/backend-verification'
$runRoot = [IO.Path]::GetFullPath((Join-Path $evidenceRoot ([Guid]::NewGuid().ToString('N'))))
if (-not $runRoot.StartsWith(([IO.Path]::GetFullPath($evidenceRoot) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'Test output must remain in the project artifacts directory.' }
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $runRoot 'temporary') --junitxml (Join-Path $runRoot 'results.xml')
    $testResult = $LASTEXITCODE
    Write-Output ('Backend evidence: ' + (Join-Path $runRoot 'results.xml'))
} finally { Pop-Location }
exit $testResult
