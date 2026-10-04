@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
echo 正在检查 Python 环境…
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
if not errorlevel 1 goto start
echo 首次使用正在安装依赖，需要联网。安装完成后可离线使用。
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 goto failed
:start
".venv\Scripts\python.exe" scripts\launch.py
if errorlevel 1 goto failed
exit /b 0
:nopython
echo 未找到 Python 3.11 或以上版本。请安装 Python 并勾选 Add Python to PATH，再双击此文件。
echo 下载地址：https://www.python.org/downloads/windows/
pause
exit /b 1
:failed
echo 启动未完成。请查看上方提示或数据目录中的 logs\app.log，确认磁盘空间与首次安装网络连接。
pause
exit /b 1
