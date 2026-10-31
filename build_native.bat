@echo off
setlocal
cd /d "%~dp0"
if exist ".cache\sdk\microsoft.windows.sdk.cpp\c\Include\10.0.22621.0\um\Windows.h" (
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_local_sdk.ps1
    if errorlevel 1 goto :error
    echo Built native\out-local\AmazingLawClient.dll and .asi
    exit /b 0
)
cmake -S native -B native/out -G "Visual Studio 17 2022" -A Win32
if errorlevel 1 goto :error
cmake --build native/out --config Release
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync_native_artifacts.ps1 -NativeDirectory "%~dp0native\out\Release"
if errorlevel 1 goto :error
echo Built native\out\Release\AmazingLawClient.dll and .asi
exit /b 0
:error
echo Native build failed.
pause
exit /b 1
