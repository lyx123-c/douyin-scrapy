param(
  [string]$FfmpegExe = ''
)

$ErrorActionPreference = 'Stop'
$FrontendRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$VendorRoot = Join-Path $FrontendRoot 'vendor\ffmpeg'
$InfoPath = Join-Path $VendorRoot 'BUILD-INFO.json'
$LicensePath = Join-Path $VendorRoot 'LICENSE.txt'
$VersionPath = Join-Path $VendorRoot 'VERSION.txt'
$SourceInfoPath = Join-Path $VendorRoot 'SOURCE-INFO.txt'

if (-not $FfmpegExe) {
  $FfmpegExe = Join-Path $VendorRoot 'ffmpeg.exe'
}

$FfmpegExe = [System.IO.Path]::GetFullPath($FfmpegExe)
foreach ($requiredPath in @($FfmpegExe, $InfoPath, $LicensePath, $VersionPath, $SourceInfoPath)) {
  if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
    throw "Required FFmpeg resource is missing: $requiredPath"
  }
}

$Config = Get-Content -LiteralPath $InfoPath -Raw | ConvertFrom-Json
$Binary = Get-Item -LiteralPath $FfmpegExe
if ($Binary.Length -ne [long]$Config.binary_bytes) {
  throw "FFmpeg size mismatch. Expected $($Config.binary_bytes), got $($Binary.Length)."
}

$Hasher = [System.Security.Cryptography.SHA256]::Create()
$Stream = [System.IO.File]::OpenRead($FfmpegExe)
try {
  $HashBytes = $Hasher.ComputeHash($Stream)
} finally {
  $Stream.Dispose()
  $Hasher.Dispose()
}
$Hash = ([System.BitConverter]::ToString($HashBytes)).Replace('-', '')
if ($Hash -ne $Config.binary_sha256) {
  throw "FFmpeg SHA256 mismatch. Expected $($Config.binary_sha256), got $Hash."
}

$VersionOutput = (& $FfmpegExe -hide_banner -version 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
  throw "ffmpeg -version failed with exit code $LASTEXITCODE."
}
if ($VersionOutput -notmatch [regex]::Escape($Config.version)) {
  throw "FFmpeg version does not match BUILD-INFO.json: $($Config.version)"
}
if ($VersionOutput -notmatch '--enable-gpl') {
  throw 'FFmpeg GPL build flag is missing.'
}
if ($VersionOutput -notmatch '--enable-version3') {
  throw 'FFmpeg version3 build flag is missing.'
}

$VersionFile = Get-Content -LiteralPath $VersionPath -Raw
if ($VersionFile -notmatch [regex]::Escape($Config.version)) {
  throw 'VERSION.txt does not match BUILD-INFO.json.'
}
$LicenseText = Get-Content -LiteralPath $LicensePath -Raw
if ($LicenseText -notmatch 'GNU GENERAL PUBLIC LICENSE' -or $LicenseText -notmatch 'Version 3') {
  throw 'GPLv3 license text is missing or invalid.'
}

$Encoders = (& $FfmpegExe -hide_banner -encoders 2>&1 | Out-String)
$Decoders = (& $FfmpegExe -hide_banner -decoders 2>&1 | Out-String)
$Muxers = (& $FfmpegExe -hide_banner -muxers 2>&1 | Out-String)

foreach ($Name in $Config.required_encoders) {
  $Pattern = '(?m)^\s*[VAS][^\r\n]*\s+' + [regex]::Escape([string]$Name) + '\s'
  if ($Encoders -notmatch $Pattern) {
    throw "Required encoder is missing: $Name"
  }
}
foreach ($Name in $Config.required_decoders) {
  $Pattern = '(?m)^\s*[VAS][^\r\n]*\s+' + [regex]::Escape([string]$Name) + '\s'
  if ($Decoders -notmatch $Pattern) {
    throw "Required decoder is missing: $Name"
  }
}
foreach ($Name in $Config.required_muxers) {
  $Pattern = '(?m)^\s*E\s+' + [regex]::Escape([string]$Name) + '\s'
  if ($Muxers -notmatch $Pattern) {
    throw "Required muxer is missing: $Name"
  }
}

[ordered]@{
  path = $FfmpegExe
  version = $Config.version
  sha256 = $Hash
  license = $Config.license
  decoders = @($Config.required_decoders)
  encoders = @($Config.required_encoders)
  muxers = @($Config.required_muxers)
} | ConvertTo-Json -Depth 4
