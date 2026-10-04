@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先双击 run.bat 完成首次安装。
    pause
    exit /b 1
)
echo 请先关闭运行中的数据库窗口，再继续离线备份。运行时请使用网页一键备份。
pause
".venv\Scripts\python.exe" scripts\backup.py
pause
