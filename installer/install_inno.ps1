# Installs only the pinned compiler into this disposable build checkout.
# No administrator rights, paid service, package manager or global PATH changes.
$ErrorActionPreference = 'Stop'
$compilerRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'tools\InnoSetup'))
$toolsRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'tools'))
if (-not $compilerRoot.StartsWith($toolsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Compiler destination is outside the build tools directory.'
}
if (Test-Path -LiteralPath $compilerRoot) {
    throw 'Compiler destination already exists. Use a fresh build checkout.'
}
$downloadRoot = Join-Path $PSScriptRoot 'downloads'
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
$downloadFile = Join-Path $downloadRoot 'innosetup-6.7.3.exe'
$expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
if (-not (Test-Path -LiteralPath $downloadFile)) {
    Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $downloadFile -TimeoutSec 120
}
if ((Get-FileHash -LiteralPath $downloadFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Inno Setup download checksum mismatch.'
}
$compilerSignature = Get-AuthenticodeSignature -LiteralPath $downloadFile
if ($compilerSignature.Status -ne 'Valid' -or
    $compilerSignature.SignerCertificate.Subject -notmatch '(^|, )CN=Pyrsys B\.V\.(,|$)') {
    throw 'Inno Setup signature or expected publisher could not be verified.'
}
$installArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
    '/CURRENTUSER', '/NOICONS', '/TASKS=', ('/DIR="' + $compilerRoot + '"'))
$compilerInstall = Start-Process -FilePath $downloadFile -ArgumentList $installArguments -WindowStyle Hidden -Wait -PassThru
if ($compilerInstall.ExitCode -ne 0 -or
    -not (Test-Path -LiteralPath (Join-Path $compilerRoot 'ISCC.exe'))) {
    throw "Compiler installation failed ($($compilerInstall.ExitCode))."
}
Write-Output 'Verified Inno Setup 6.7.3 is ready inside installer/tools/InnoSetup.'
