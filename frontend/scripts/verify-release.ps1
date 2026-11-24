param(
  [string]$InstallerPath = ''
)

$ErrorActionPreference = 'Stop'
$FrontendRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ReleaseRoot = Join-Path $FrontendRoot 'release'
$UnpackedRoot = Join-Path $ReleaseRoot 'win-unpacked'
$ManifestPath = Join-Path $ReleaseRoot 'RELEASE-MANIFEST.json'
$PackagePath = Join-Path $FrontendRoot 'package.json'

function Get-Sha256Hex([string]$Path) {
  $Hasher = [System.Security.Cryptography.SHA256]::Create()
  $Stream = [System.IO.File]::OpenRead($Path)
  try {
    $HashBytes = $Hasher.ComputeHash($Stream)
  } finally {
    $Stream.Dispose()
    $Hasher.Dispose()
  }
  return ([System.BitConverter]::ToString($HashBytes)).Replace('-', '')
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "Release manifest is missing: $ManifestPath"
}
if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
  throw "package.json is missing: $PackagePath"
}

$Package = [System.IO.File]::ReadAllText($PackagePath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$Manifest = [System.IO.File]::ReadAllText($ManifestPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$Build = $Package.build
$Nsis = $Build.nsis
$ExpectedName = "$($Build.productName)-$($Package.version)-x64.exe"

if (-not $InstallerPath) {
  $InstallerPath = Join-Path $ReleaseRoot $Manifest.installer.path.Split('/')[-1]
}
$InstallerPath = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
  throw "NSIS installer is missing: $InstallerPath"
}
if ([System.IO.Path]::GetFileName($InstallerPath) -ne $ExpectedName) {
  throw "Installer name mismatch. Expected $ExpectedName, got $([System.IO.Path]::GetFileName($InstallerPath))."
}

if ($Nsis.oneClick -ne $false) { throw 'NSIS oneClick must be false.' }
if ($Nsis.perMachine -ne $false) { throw 'NSIS perMachine must be false.' }
if ($Nsis.allowToChangeInstallationDirectory -ne $true) { throw 'Install directory selection must be enabled.' }
if ($Nsis.createStartMenuShortcut -ne $true) { throw 'Start menu shortcut must be enabled.' }
if ($Nsis.runAfterFinish -ne $false) { throw 'NSIS runAfterFinish must remain false; update restart is owned by the handoff.' }
if ($Nsis.deleteAppDataOnUninstall -ne $false) { throw 'Uninstall must not delete application data.' }

$RequiredResources = @(
  (Join-Path $UnpackedRoot "resources\backend\Backend.exe"),
  (Join-Path $UnpackedRoot "resources\ffmpeg\ffmpeg.exe"),
  (Join-Path $UnpackedRoot "resources\ffmpeg\LICENSE.txt"),
  (Join-Path $UnpackedRoot "resources\ffmpeg\BUILD-INFO.json"),
  (Join-Path $UnpackedRoot "resources\ffmpeg\VERSION.txt"),
  (Join-Path $UnpackedRoot "resources\ffmpeg\SOURCE-INFO.txt"),
  (Join-Path $UnpackedRoot "resources\app.asar"),
  (Join-Path $UnpackedRoot "resources\version-metadata.json"),
  (Join-Path $UnpackedRoot "resources\update\keyring.json"),
  (Join-Path $UnpackedRoot "resources\update\update-source.json"),
  (Join-Path $UnpackedRoot "resources\icon.png"),
  (Join-Path $FrontendRoot "build\icon.ico"),
  (Join-Path $FrontendRoot "build\icon.png"),
  (Join-Path $UnpackedRoot "LICENSE.electron.txt"),
  (Join-Path $UnpackedRoot "LICENSES.chromium.html")
)
foreach ($Path in $RequiredResources) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Required release resource is missing: $Path"
  }
}

$VersionMetadataPath = Join-Path $UnpackedRoot 'resources\version-metadata.json'
$VersionMetadata = [System.IO.File]::ReadAllText($VersionMetadataPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
if ($VersionMetadata.version -ne $Package.version) { throw 'Version metadata does not match package.json.' }
if ($VersionMetadata.product_id -ne $Build.appId) { throw 'Version metadata product_id does not match appId.' }
$UpdateSource = [System.IO.File]::ReadAllText((Join-Path $UnpackedRoot 'resources\update\update-source.json'), [System.Text.Encoding]::UTF8) | ConvertFrom-Json
if ($UpdateSource.provider -ne 'github_releases') { throw 'Unsupported update source provider.' }
if ($UpdateSource.repository -eq 'lyx123-c/douyin-scrapy' -and -not $UpdateSource.allow_development_repository_as_update_source) {
  throw 'Development repository requires explicit public update-source approval.'
}
$UpdateKeyring = [System.IO.File]::ReadAllText((Join-Path $UnpackedRoot 'resources\update\keyring.json'), [System.Text.Encoding]::UTF8) | ConvertFrom-Json
if ($UpdateKeyring.schema_version -ne 1) { throw 'Unsupported update keyring schema.' }

$BuildIconHash = Get-Sha256Hex (Join-Path $FrontendRoot 'build\icon.png')
$PackagedIconHash = Get-Sha256Hex (Join-Path $UnpackedRoot 'resources\icon.png')
if ($BuildIconHash -ne $PackagedIconHash) {
  throw 'Packaged window icon does not match build/icon.png.'
}

$ForbiddenNames = @(
  'ffprobe.exe',
  'ffplay.exe',
  'douyin_videos.csv',
  'video_analytics.csv',
  'download_queue.json'
)
foreach ($Name in $ForbiddenNames) {
  $Matches = Get-ChildItem -LiteralPath (Join-Path $UnpackedRoot 'resources') -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ieq $Name }
  if ($Matches) {
    throw "Forbidden file found in resources: $($Matches[0].FullName)"
  }
}

foreach ($DirectoryName in @('data', 'tasks', 'logs', 'browser-profile')) {
  $DirectoryPath = Join-Path (Join-Path $UnpackedRoot 'resources') $DirectoryName
  if (Test-Path -LiteralPath $DirectoryPath) {
    throw "Forbidden user data directory found in resources: $DirectoryPath"
  }
}

$AsarHelper = Join-Path $FrontendRoot 'node_modules\@electron\asar\bin\asar.js'
$AsarPath = Join-Path $UnpackedRoot 'resources\app.asar'
$AsarList = (& node $AsarHelper list $AsarPath 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
  throw "Unable to inspect app.asar: $AsarList"
}
foreach ($Pattern in @('ffmpeg.exe', 'ffprobe.exe', 'ffplay.exe', 'vendor/ffmpeg', 'douyin_videos.csv', 'video_analytics.csv', 'download_queue.json')) {
  if ($AsarList -match [regex]::Escape($Pattern)) {
    throw "Forbidden entry found in app.asar: $Pattern"
  }
}

$Hasher = [System.Security.Cryptography.SHA256]::Create()
$Stream = [System.IO.File]::OpenRead($InstallerPath)
try {
  $HashBytes = $Hasher.ComputeHash($Stream)
} finally {
  $Stream.Dispose()
  $Hasher.Dispose()
}
$InstallerHash = ([System.BitConverter]::ToString($HashBytes)).Replace('-', '')
if ($InstallerHash -ne $Manifest.installer.sha256) {
  throw 'Installer SHA256 does not match RELEASE-MANIFEST.json.'
}
if ((Get-Item -LiteralPath $InstallerPath).Length -ne [long]$Manifest.installer.bytes) {
  throw 'Installer size does not match RELEASE-MANIFEST.json.'
}

[ordered]@{
  installer = $InstallerPath
  installerBytes = (Get-Item -LiteralPath $InstallerPath).Length
  installerSha256 = $InstallerHash
  version = $Package.version
  productName = $Build.productName
  nsisOneClick = $Nsis.oneClick
  nsisPerMachine = $Nsis.perMachine
  installDirectorySelectable = $Nsis.allowToChangeInstallationDirectory
  startMenuShortcut = $Nsis.createStartMenuShortcut
  runAfterFinish = $Nsis.runAfterFinish
  desktopShortcut = $Nsis.createDesktopShortcut
  deleteAppDataOnUninstall = $Nsis.deleteAppDataOnUninstall
  backendResource = $true
  ffmpegResource = $true
  windowIcon = $true
  windowIconSha256 = $PackagedIconHash
  appAsarClean = $true
  versionMetadata = $true
  updateSourceConfigured = [bool]$UpdateSource.repository
  updateActiveKeyCount = @($UpdateKeyring.keys | Where-Object { $_.status -eq 'active' }).Count
  manifest = $ManifestPath
} | ConvertTo-Json -Depth 4
