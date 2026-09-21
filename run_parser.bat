@echo off
cd /d "%~dp0"

echo ========================================
echo Amazing Online Laws Parser
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    py -3.10 -m venv .venv
    if errorlevel 1 (
        python -m venv .venv
    )
)

echo [2/3] Installing Playwright...
.venv\Scripts\python.exe -m pip install -r requirements_playwright.txt
if errorlevel 1 goto :error

echo [3/3] Installing Chromium...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Starting parser...
echo.
.venv\Scripts\python.exe -u playwright_laws_parser.py

echo.
pause
exit /b 0

:error
echo.
echo ERROR during installation.
pause
exit /b 1
