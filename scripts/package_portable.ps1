param(
    [string]$BundlePath = '.cache/dist/portable-build/AmazingHelper',
    [Parameter(Mandatory=$true)][string]$NativeDirectory
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundleDirectory = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $BundlePath))
if (-not (Test-Path -LiteralPath (Join-Path $bundleDirectory 'AmazingHelper.exe'))) { throw 'Build AmazingHelper.exe first.' }
$nativeRoot = (Resolve-Path -LiteralPath $NativeDirectory).Path
foreach ($artifactName in @('AmazingLawClient.dll', 'AmazingLawClient.asi', 'AmazingHelperInjector.exe')) {
    $artifactPath = Join-Path $nativeRoot $artifactName
    if (-not (Test-Path -LiteralPath $artifactPath)) { throw "Missing native artifact: $artifactPath" }
    Copy-Item -LiteralPath $artifactPath -Destination $bundleDirectory
}
# Compatibility commands only; the user launches the EXE or an installed shortcut.
foreach ($mode in @('launcher','manager','overlay','backend','install-browser')) {
    $name = if ($mode -eq 'launcher') { 'start.bat' } else { "start_$mode.bat" }
    $content = "@echo off`r`ncd /d `"%~dp0`"`r`nstart `"`" AmazingHelper.exe $mode`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bundleDirectory $name), $content)
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs/RELEASE.md') -Destination (Join-Path $bundleDirectory 'README.md')
Write-Output "Portable package: $bundleDirectory"
