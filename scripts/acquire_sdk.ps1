# Optional local SDK, for machines with MSVC but no installed Windows SDK.
# Downloads Microsoft NuGet packages into the ignored workspace cache only.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sdkCache = Join-Path $projectRoot '.cache/sdk'
New-Item -ItemType Directory -Force -Path $sdkCache | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($packageName in @('microsoft.windows.sdk.cpp', 'microsoft.windows.sdk.cpp.x86')) {
    $packageDirectory = Join-Path $sdkCache $packageName
    if (-not (Test-Path -LiteralPath $packageDirectory)) {
        $archivePath = Join-Path $sdkCache "$packageName.zip"
        $packageUrl = "https://api.nuget.org/v3-flatcontainer/$packageName/10.0.22621.2428/$packageName.10.0.22621.2428.nupkg"
        & curl.exe -fL --retry 2 --connect-timeout 15 --max-time 240 -o $archivePath $packageUrl
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $packageName" }
        [System.IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $packageDirectory)
    }
    Write-Output $packageDirectory
}
