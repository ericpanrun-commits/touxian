param([switch]$Check, [switch]$Remove)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonWindowless = (Join-Path $projectRoot '..\.venv\Scripts\pythonw.exe')
$taskName = 'AttentionDesk-Local'
if ($Check) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { $task | Select-Object TaskName,State,Actions } else { Write-Output '尚未注册 Attention Desk 登录启动。' }
    return
}
if ($Remove) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.Actions.WorkingDirectory -ne $projectRoot) { throw '启动项属于另一个目录，未移除。' }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    return
}
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw '项目 Python 环境尚未安装。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'dist\index.html'))) { throw '请先构建工作台前端。' }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.WorkingDirectory -ne $projectRoot) { throw '启动项名称已被其他目录使用，未覆盖。' }
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument '-m server.desktop_entrypoint' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output '已注册当前用户登录启动；未立即启动程序，也不会自动登录微信。'
