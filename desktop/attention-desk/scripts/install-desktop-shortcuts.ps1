param(
  [string]$DesktopDirectory = [Environment]::GetFolderPath('Desktop'),
  [string]$ProgramsDirectory = [Environment]::GetFolderPath('Programs')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonWindowless = [IO.Path]::GetFullPath((Join-Path $projectRoot '../.venv/Scripts/pythonw.exe'))
$launcher = Join-Path $PSScriptRoot 'attention-desk-launcher.pyw'
$icon = Join-Path $projectRoot 'public/attention-desk.ico'
foreach ($required in @($pythonWindowless, $launcher, $icon)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "缺少启动文件：$required" }
}
$shell = New-Object -ComObject WScript.Shell
try {
  foreach ($directory in @($DesktopDirectory, $ProgramsDirectory)) {
    if (-not $directory) { throw '无法找到当前用户的桌面或开始菜单。' }
    $directory = [IO.Path]::GetFullPath($directory)
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $path = Join-Path $directory 'Attention Desk.lnk'
    if (Test-Path -LiteralPath $path) {
      $old = $shell.CreateShortcut($path)
      if ($old.TargetPath -ne $pythonWindowless -or $old.Arguments -ne ('"' + $launcher + '"')) {
        throw "已有同名快捷方式，未覆盖：$path"
      }
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $pythonWindowless
    $shortcut.Arguments = '"' + $launcher + '"'
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = '打开 Attention Desk；需要时在后台启动工作台与采集服务。'
    $shortcut.IconLocation = "$icon,0"
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Output $path
  }
} finally {
  [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null
}
