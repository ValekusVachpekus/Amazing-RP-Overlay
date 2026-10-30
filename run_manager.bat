@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.10 -m venv .venv
    if errorlevel 1 goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements_backend.txt
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto :error
start "" /b .venv\Scripts\pythonw.exe -m amazing_overlay manager
exit /b 0
:error
echo Manager setup failed.
pause
exit /b 1
