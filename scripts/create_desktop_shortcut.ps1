$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controller = Join-Path $projectRoot 'scripts\desktop_control.py'
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $controller) -or -not (Test-Path -LiteralPath $pythonw)) {
    throw '请先双击 control.bat 完成环境安装，再创建桌面快捷方式。'
}
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { throw '未找到当前用户桌面目录。' }
$shortcutPath = Join-Path $desktop '知序数据库.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $controller + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = '知序 · 个人知识、经验、案例与数字记忆数据库'
$shortcut.IconLocation = (Join-Path $projectRoot 'app\static\app.ico') + ',0'
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output $shortcutPath
