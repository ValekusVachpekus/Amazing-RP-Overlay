@echo off
setlocal
cd /d "%~dp0..\.."
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
call scripts/build/build_native.bat
if errorlevel 1 exit /b 1
set "NATIVE_DIR=native/out/Release"
if exist ".cache/sdk/microsoft.windows.sdk.cpp/c/Include/10.0.22621.0/um/Windows.h" set "NATIVE_DIR=native/out-local"
if "%~1"=="--stage-only" (
    .venv\Scripts\python.exe scripts/build_distribution.py --native-dir "%NATIVE_DIR%"
) else (
    .venv\Scripts\python.exe scripts/build_distribution.py --native-dir "%NATIVE_DIR%" --publish
)
exit /b %errorlevel%
