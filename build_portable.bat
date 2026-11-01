@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Create the project .venv first, for example with run_manager.bat.
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements_backend.txt pyinstaller
if errorlevel 1 exit /b 1
call build_native.bat
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name AmazingLawOverlay --distpath dist/portable-build --workpath build/portable-build --collect-all playwright --collect-all bs4 --collect-all openai --collect-all dotenv portable_entry.py
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package_portable.ps1 -BundlePath dist/portable-build/AmazingLawOverlay
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts/collect_licenses.py --bundle-path dist/portable-build/AmazingLawOverlay
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts/check_portable.py dist/portable-build/AmazingLawOverlay/AmazingLawOverlay.exe --settings-check --browser-check
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts/publish_release.py
if errorlevel 1 exit /b 1
echo Complete portable package copied to Release.
exit /b %errorlevel%
