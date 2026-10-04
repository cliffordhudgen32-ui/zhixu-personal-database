@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
echo 正在打开知序桌面控制台…
if exist ".venv\Scripts\python.exe" goto checkdeps
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if errorlevel 1 goto nopython
    python -m venv .venv
)
if errorlevel 1 goto failed
:checkdeps
".venv\Scripts\python.exe" -c "import fastapi,uvicorn,sqlalchemy,alembic,multipart,markdown,bleach,dotenv,rapidocr,onnxruntime,fastembed,pypdfium2,PIL" >nul 2>nul
if not errorlevel 1 goto checktk
echo 首次使用正在安装依赖，需要联网。完成后可离线使用。
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 goto failed
:checktk
".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
if errorlevel 1 goto notk
if /i "%~1"=="--check-ui" (
    ".venv\Scripts\python.exe" "scripts\desktop_control.py" --check-ui
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "scripts\desktop_control.py"
) else (
    start "" ".venv\Scripts\python.exe" "scripts\desktop_control.py"
)
exit /b 0
:nopython
echo 未找到 Python 3.11 或以上版本。请安装 Python 并勾选 Add Python to PATH。
echo 下载地址：https://www.python.org/downloads/windows/
pause
exit /b 1
:notk
echo Python 缺少 Tkinter。请重新运行 Python 安装程序并启用 Tcl/Tk and IDLE。
pause
exit /b 1
:failed
echo 控制台未能打开，请查看上方提示，确认磁盘空间和首次安装网络。
pause
exit /b 1
