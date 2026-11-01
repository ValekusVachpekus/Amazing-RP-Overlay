param([string]$NativeDirectory = '')

# Keep existing injector paths current as well as the canonical latest path.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($NativeDirectory) {
    $sourceLibrary = Get-Item -LiteralPath (Join-Path $NativeDirectory 'AmazingLawClient.dll')
} else {
    $buildCandidates = @(
        (Join-Path $projectRoot 'native/out-local/AmazingLawClient.dll'),
        (Join-Path $projectRoot 'native/out/Release/AmazingLawClient.dll')
    )
    $sourceLibrary = $buildCandidates | Where-Object { Test-Path -LiteralPath $_ } |
        ForEach-Object { Get-Item -LiteralPath $_ } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $sourceLibrary) { throw 'Build AmazingLawClient.dll first.' }

$distDirectory = Join-Path $projectRoot '.cache/dist'
$latestDirectory = Join-Path $distDirectory 'native-latest'
New-Item -ItemType Directory -Force -Path $latestDirectory | Out-Null
$destinations = @(
    (Join-Path $latestDirectory 'AmazingLawClient.dll'),
    (Join-Path $latestDirectory 'AmazingLawClient.asi')
)
$destinations += Get-ChildItem -LiteralPath $distDirectory -File -Recurse |
    Where-Object { $_.Name -in @('AmazingLawClient.dll', 'AmazingLawClient.asi') } |
    Select-Object -ExpandProperty FullName
$sourceHash = (Get-FileHash -LiteralPath $sourceLibrary.FullName -Algorithm SHA256).Hash
$blocked = @()
foreach ($destination in ($destinations | Sort-Object -Unique)) {
    try {
        if ((Test-Path -LiteralPath $destination) -and
            (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $sourceHash) {
            continue
        }
        Copy-Item -LiteralPath $sourceLibrary.FullName -Destination $destination -Force
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $sourceHash) {
            throw 'Copied artifact hash mismatch.'
        }
        Write-Output "Updated: $destination"
    } catch {
        $blocked += $destination
        Write-Warning "Could not update $destination. Close the process using that file before rebuilding."
    }
}
Write-Output "Inject after restarting the game: $(Join-Path $latestDirectory 'AmazingLawClient.dll')"
Write-Output "SHA256: $sourceHash"
if ($blocked.Count) { throw 'Some older DLL copies are locked; native-latest remains the canonical output.' }
