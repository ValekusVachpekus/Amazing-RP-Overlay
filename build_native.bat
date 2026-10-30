@echo off
setlocal
cd /d "%~dp0"
cmake -S native -B native/out -G "Visual Studio 17 2022" -A Win32
if errorlevel 1 goto :error
cmake --build native/out --config Release
if errorlevel 1 goto :error
echo Built native\out\Release\AmazingLawClient.dll and .asi
exit /b 0
:error
echo Native build failed.
pause
exit /b 1
