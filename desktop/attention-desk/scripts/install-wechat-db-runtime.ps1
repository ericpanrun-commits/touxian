#requires -Version 7.0
param([string]$PythonExecutable = '')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonExecutable) { $PythonExecutable=(Join-Path $projectRoot '..\.venv\Scripts\python.exe') }
if (-not (Test-Path -LiteralPath $PythonExecutable)) { throw '请指定 Python 3.12 的完整路径。' }
$runtimePath=Join-Path $projectRoot 'artifacts\wechat-db0\venv'
$runtimePython=Join-Path $runtimePath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $PythonExecutable -m venv --without-pip $runtimePath
    if ($LASTEXITCODE) { throw '隔离 Python 环境创建失败。' }
}
& $runtimePython -m ensurepip --upgrade
if ($LASTEXITCODE) { throw '隔离安装工具初始化失败。' }
& $runtimePython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'server\requirements-wechat-db.lock.txt')
if ($LASTEXITCODE) { throw '隔离读取依赖安装失败。' }
Write-Output '只读 DB 依赖已安装到项目 artifacts；原有 Attention Desk 环境未修改。'
