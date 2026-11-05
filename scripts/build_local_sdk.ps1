param([switch]$HelpersOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$msvcRoot = Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio/2022/Community/VC/Tools/MSVC'
$env:VSINSTALLDIR = Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio/2022/Community'
$msvcVersion = Get-ChildItem -LiteralPath $msvcRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
if (-not $msvcVersion) { throw 'Install MSVC C++ build tools first.' }
$sdkRoot = Join-Path $projectRoot '.cache/sdk/microsoft.windows.sdk.cpp/c'
$sdkX86 = Join-Path $projectRoot '.cache/sdk/microsoft.windows.sdk.cpp.x86/c'
$sdkInclude = Join-Path $sdkRoot 'Include/10.0.22621.0'
if (-not (Test-Path -LiteralPath $sdkInclude)) { throw 'Run scripts/acquire_sdk.ps1 first.' }
$compilerDirectory = Join-Path $msvcVersion.FullName 'bin/Hostx64/x86'
$env:PATH = "$compilerDirectory;$env:PATH"
$env:INCLUDE = (Join-Path $msvcVersion.FullName 'include') + ';' + (Join-Path $msvcVersion.FullName 'atlmfc/include') + ';' + (Join-Path $sdkInclude 'ucrt') + ';' + (Join-Path $sdkInclude 'shared') + ';' + (Join-Path $sdkInclude 'um')
$env:LIB = (Join-Path $msvcVersion.FullName 'lib/x86') + ';' + (Join-Path $msvcVersion.FullName 'atlmfc/lib/x86') + ';' + (Join-Path $sdkX86 'ucrt/x86') + ';' + (Join-Path $sdkX86 'um/x86')
$compilerPath = (Join-Path $compilerDirectory 'cl.exe').Replace('\', '/')
$resourceCompiler = (Join-Path $sdkRoot 'bin/10.0.22621.0/x64/rc.exe').Replace('\', '/')
$manifestTool = (Join-Path $sdkRoot 'bin/10.0.22621.0/x64/mt.exe').Replace('\', '/')
$buildDirectory = Join-Path $projectRoot 'native/out-local'
& cmake --fresh -S (Join-Path $projectRoot 'native') -B $buildDirectory -G Ninja "-DCMAKE_C_COMPILER=$compilerPath" "-DCMAKE_CXX_COMPILER=$compilerPath" "-DCMAKE_RC_COMPILER=$resourceCompiler" "-DCMAKE_MT=$manifestTool" -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed.' }
if ($HelpersOnly) {
    & cmake --build $buildDirectory --config Release --target AmazingHelperInjector overlay_test_injector
} else {
    & cmake --build $buildDirectory --config Release
}
if ($LASTEXITCODE -ne 0) { throw 'Native build failed.' }
if (-not $HelpersOnly) { & (Join-Path $PSScriptRoot 'sync_native_artifacts.ps1') -NativeDirectory $buildDirectory }
