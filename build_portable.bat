@echo off
setlocal
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -r requirements_backend.txt pyinstaller
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name AmazingLawOverlay --collect-all playwright --collect-all bs4 --collect-all openai --collect-all dotenv portable_entry.py
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package_portable.ps1
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts/collect_licenses.py
exit /b %errorlevel%
