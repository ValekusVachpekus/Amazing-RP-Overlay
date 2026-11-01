param([string]$BundlePath = '.cache/dist/AmazingLawOverlay')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundleDirectory = Join-Path $projectRoot $BundlePath
if (-not (Test-Path -LiteralPath (Join-Path $bundleDirectory 'AmazingLawOverlay.exe'))) {
    throw 'Build the executable first.'
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs/V2.md') -Destination (Join-Path $bundleDirectory 'README.md')
# No local config, .env, laws or browser profiles are copied into a release.
foreach ($clientMode in @('manager', 'overlay', 'backend', 'install-browser')) {
    $batchText = "@echo off`r`ncd /d `"%~dp0`"`r`nstart `"`" AmazingLawOverlay.exe $clientMode`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bundleDirectory "start_$clientMode.bat"), $batchText)
}
$nativeCandidates = @(
    (Join-Path $projectRoot '.cache/dist/native-latest/AmazingLawClient.dll'),
    (Join-Path $projectRoot 'native/out/Release/AmazingLawClient.dll'),
    (Join-Path $projectRoot 'native/out-local/AmazingLawClient.dll')
) | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Item -LiteralPath $_ }
$newestNative = $nativeCandidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$nativeDirectory = if ($newestNative) { $newestNative.DirectoryName } else { '' }
if (-not $nativeDirectory) { throw 'Build the native client before packaging the complete release.' }
foreach ($artifactName in @('AmazingLawClient.dll', 'AmazingLawClient.asi')) {
    $artifactPath = Join-Path $nativeDirectory $artifactName
    if (Test-Path -LiteralPath $artifactPath) {
        Copy-Item -LiteralPath $artifactPath -Destination $bundleDirectory
    } else {
        throw "Missing native artifact: $artifactPath"
    }
}
Write-Output "Portable package: $bundleDirectory"
