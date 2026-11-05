$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $projectRoot '.cache/tools'
$compiler = Join-Path $tools 'inno/ISCC.exe'
if (Test-Path -LiteralPath $compiler) { Write-Output $compiler; exit 0 }
New-Item -ItemType Directory -Force -Path $tools | Out-Null
$download = Join-Path $tools 'innosetup.exe'
Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe' -OutFile $download
if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne '8A5A6711C3766A6E5FB74F280BA0E3551E719CE94538D901FDBD1F8F9920B3F0') {
    throw 'Inno Setup download hash mismatch'
}
if ((Get-AuthenticodeSignature -LiteralPath $download).Status -ne 'Valid') { throw 'Invalid Inno Setup signature' }
$argsInno = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR="' + (Join-Path $tools 'inno') + '"'))
$process = Start-Process -FilePath $download -ArgumentList $argsInno -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compiler)) { throw 'Inno Setup compiler installation failed' }
Write-Output $compiler
