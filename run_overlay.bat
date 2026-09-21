@echo off
setlocal
cd /d "%~dp0"

title Law Overlay

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.10 -m venv .venv
    if errorlevel 1 python -m venv .venv
)

echo Installing/updating PySide6...
.venv\Scripts\python.exe -m pip install -r requirements_overlay.txt
if errorlevel 1 goto :error

echo.
echo Starting Law Overlay in background...
start "" /b .venv\Scripts\pythonw.exe law_overlay.py

echo Started.
timeout /t 2 >nul
exit /b 0

:error
echo.
echo Failed to install dependencies.
pause
exit /b 1
