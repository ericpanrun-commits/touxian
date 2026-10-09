param(
  [switch]$Install,
  [ValidateRange(1, 65535)][int]$ApiPort = 8765,
  [ValidateRange(1, 65535)][int]$WebPort = 5173,
  [string]$PythonExecutable = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root "..\.venv\Scripts\python.exe"
$apiProcess = $null
$previousApiPort = $env:ATTENTION_API_PORT
$previousWebPort = $env:ATTENTION_WEB_PORT
Push-Location $root

function Test-LocalPort([int]$Port) {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $connection = $client.ConnectAsync("127.0.0.1", $Port)
    return ($connection.Wait(300) -and $client.Connected)
  } catch { return $false }
  finally { $client.Dispose() }
}

try {
  $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
  if ($Install) {
    & $npm ci
    if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败。" }
    if (-not (Test-Path -LiteralPath $venvPython)) {
      & $PythonExecutable -m venv (Join-Path $root "..\.venv")
      if ($LASTEXITCODE -ne 0) { throw "Python 环境创建失败。可使用 -PythonExecutable 指定 Python 路径。" }
    }
    & $venvPython -m pip install -r server\requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw "后端依赖安装失败。" }
  }
  if (-not (Test-Path -LiteralPath $venvPython)) { throw "尚未安装依赖，请先运行 .\start.ps1 -Install。" }
  if (-not (Test-Path -LiteralPath "node_modules\vite\bin\vite.js")) { throw "前端依赖不存在，请先运行 .\start.ps1 -Install。" }
  if ($ApiPort -eq $WebPort) { throw "前端和后端需要使用不同端口。" }
  if ((Test-LocalPort $ApiPort) -or (Test-LocalPort $WebPort)) { throw "端口已被占用，请更换 -ApiPort 或 -WebPort；没有停止其他应用。" }

  $env:ATTENTION_API_PORT = "$ApiPort"
  $env:ATTENTION_WEB_PORT = "$WebPort"
  & $npm run build
  if ($LASTEXITCODE -ne 0) { throw "手机页面构建失败，请检查上方错误。" }
  $logDirectory = Join-Path $root "..\data\attention-desk\logs"
  New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
  $logStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
  $stdoutLog = Join-Path $logDirectory "backend-$logStamp.out.log"
  $stderrLog = Join-Path $logDirectory "backend-$logStamp.err.log"
  $apiProcess = Start-Process -FilePath $venvPython -ArgumentList @("-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", "$ApiPort", "--workers", "1") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

  $ready = $false
  $startupDeadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $startupDeadline) {
    $apiProcess.Refresh()
    if ($apiProcess.HasExited) { break }
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health?check_integrations=false" -NoProxy -TimeoutSec 3
      if ($health.status -eq "ok") { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 200 }
  }
  if (-not $ready) {
    if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 18 | Write-Host }
    throw "后端未能启动，请检查上方错误和 $stderrLog。"
  }
  Write-Host "工作台：http://127.0.0.1:$WebPort"
  Write-Host "后端已就绪。按 Ctrl+C 关闭本次前后端。"
  & $npm run dev -- --host 127.0.0.1 --port $WebPort --strictPort
  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) { throw "前端已退出（退出码 $LASTEXITCODE）。" }
} finally {
  if ($null -ne $apiProcess) {
    $apiProcess.Refresh()
    if (-not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue }
  }
  $env:ATTENTION_API_PORT = $previousApiPort
  $env:ATTENTION_WEB_PORT = $previousWebPort
  Pop-Location
}
