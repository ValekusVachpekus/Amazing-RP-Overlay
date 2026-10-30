$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundleDirectory = Join-Path $projectRoot 'dist/AmazingLawOverlay'
if (-not (Test-Path -LiteralPath (Join-Path $bundleDirectory 'AmazingLawOverlay.exe'))) {
    throw 'Build the executable first.'
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs/V2.md') -Destination (Join-Path $bundleDirectory 'README.md')
# No local config, .env, laws or browser profiles are copied into a release.
foreach ($clientMode in @('manager', 'overlay', 'backend', 'install-browser')) {
    $batchText = "@echo off`r`ncd /d `"%~dp0`"`r`nstart `"`" AmazingLawOverlay.exe $clientMode`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bundleDirectory "start_$clientMode.bat"), $batchText)
}
$nativeDirectory = Join-Path $projectRoot 'native/out/Release'
if (-not (Test-Path -LiteralPath (Join-Path $nativeDirectory 'AmazingLawClient.dll'))) {
    $nativeDirectory = Join-Path $projectRoot 'native/out-local'
}
foreach ($artifactName in @('AmazingLawClient.dll', 'AmazingLawClient.asi')) {
    $artifactPath = Join-Path $nativeDirectory $artifactName
    if (Test-Path -LiteralPath $artifactPath) {
        Copy-Item -LiteralPath $artifactPath -Destination $bundleDirectory
    }
}
Write-Output "Portable package: $bundleDirectory"
