@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.10 -m venv .venv
    if errorlevel 1 goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements_backend.txt
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m amazing_overlay backend
exit /b %errorlevel%
:error
echo Backend setup failed.
pause
exit /b 1
