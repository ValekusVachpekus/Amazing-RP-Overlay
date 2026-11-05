param([Parameter(Mandatory=$true)][string]$NativeDirectory)

# Update only the canonical development output. Staged bundles have manifests
# and must be rebuilt explicitly, never patched behind the publisher's back.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceLibrary = Get-Item -LiteralPath (Join-Path $NativeDirectory 'AmazingLawClient.dll')
if (-not $sourceLibrary) { throw 'Build AmazingLawClient.dll first.' }

$distDirectory = Join-Path $projectRoot '.cache/dist'
$latestDirectory = Join-Path $distDirectory 'native-latest'
New-Item -ItemType Directory -Force -Path $latestDirectory | Out-Null
$destinations = @(
    (Join-Path $latestDirectory 'AmazingLawClient.dll'),
    (Join-Path $latestDirectory 'AmazingLawClient.asi')
)
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
