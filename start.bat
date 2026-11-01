@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" .venv\Scripts\pythonw.exe -m amazing_overlay launcher
    exit /b 0
)
if exist "Release\AmazingLawOverlay.exe" (
    start "" Release\AmazingLawOverlay.exe launcher
    exit /b 0
)
echo Run scripts\run\run_manager.bat once to prepare Python, or download the complete Release folder.
pause
