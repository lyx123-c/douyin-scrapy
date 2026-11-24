param(
  [string]$Repository = 'lyx123-c/douyin-scrapy',
  [Parameter(Mandatory = $true)][string]$Tag,
  [Parameter(Mandatory = $true)][string]$Title,
  [Parameter(Mandatory = $true)][string]$NotesFile,
  [Parameter(Mandatory = $true)][string[]]$Assets
)

$ErrorActionPreference = 'Stop'
$Token = $env:GITHUB_TOKEN
if (-not $Token) {
  throw 'GITHUB_TOKEN is required. The token is read from the environment and is never written to the repository.'
}
if (-not (Test-Path -LiteralPath $NotesFile -PathType Leaf)) {
  throw "Release notes file is missing: $NotesFile"
}
foreach ($Asset in $Assets) {
  if (-not (Test-Path -LiteralPath $Asset -PathType Leaf)) {
    throw "Release asset is missing: $Asset"
  }
}

$RemoteTag = git ls-remote --tags origin "refs/tags/$Tag"
if (-not $RemoteTag) {
  throw "Tag is not present on origin: $Tag"
}

$ApiBase = "https://api.github.com/repos/$Repository"
$Headers = @{
  Authorization = "Bearer $Token"
  Accept = 'application/vnd.github+json'
  'User-Agent' = 'DouyinWorkbench-Release'
}
try {
  $Existing = Invoke-RestMethod -Uri "$ApiBase/releases/tags/$Tag" -Headers $Headers -TimeoutSec 30
  throw "A release already exists for tag ${Tag}: $($Existing.html_url)"
} catch {
  if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ne 404) {
    throw
  }
}

$Body = @{
  tag_name = $Tag
  target_commitish = 'main'
  name = $Title
  body = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $NotesFile), [System.Text.Encoding]::UTF8)
  draft = $true
  prerelease = $false
} | ConvertTo-Json -Depth 4

$Release = Invoke-RestMethod -Method Post -Uri "$ApiBase/releases" -Headers $Headers -ContentType 'application/json' -Body $Body -TimeoutSec 60
$Client = [System.Net.Http.HttpClient]::new()
$Client.Timeout = [TimeSpan]::FromHours(2)
$Client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token)
$Client.DefaultRequestHeaders.UserAgent.ParseAdd('DouyinWorkbench-Release')

try {
  foreach ($Asset in $Assets) {
    $ResolvedAsset = (Resolve-Path -LiteralPath $Asset).Path
    $AssetName = [System.IO.Path]::GetFileName($ResolvedAsset)
    $UploadBase = ([string]$Release.upload_url) -replace '\{.*$', ''
    $UploadUri = "$UploadBase?name=$([Uri]::EscapeDataString($AssetName))"
    $Stream = [System.IO.File]::OpenRead($ResolvedAsset)
    try {
      $Content = [System.Net.Http.StreamContent]::new($Stream)
      $Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/octet-stream')
      $Response = $Client.PostAsync($UploadUri, $Content).GetAwaiter().GetResult()
      $Text = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
      if (-not $Response.IsSuccessStatusCode) {
        throw "Asset upload failed for ${AssetName} (HTTP $([int]$Response.StatusCode)): $Text"
      }
    } finally {
      if ($Content) { $Content.Dispose() }
      $Stream.Dispose()
    }
  }

  $PublishedAssets = Invoke-RestMethod -Uri "$ApiBase/releases/$($Release.id)/assets?per_page=100" -Headers $Headers -TimeoutSec 60
  foreach ($Asset in $Assets) {
    $AssetName = [System.IO.Path]::GetFileName((Resolve-Path -LiteralPath $Asset).Path)
    $Remote = $PublishedAssets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    if (-not $Remote) {
      throw "Uploaded asset is missing from draft release: $AssetName"
    }
    if ([long]$Remote.size -ne (Get-Item -LiteralPath $Asset).Length) {
      throw "Uploaded asset size mismatch: $AssetName"
    }
  }

  $PublishBody = @{ draft = $false } | ConvertTo-Json
  $Published = Invoke-RestMethod -Method Patch -Uri "$ApiBase/releases/$($Release.id)" -Headers $Headers -ContentType 'application/json' -Body $PublishBody -TimeoutSec 60
  [ordered]@{
    repository = $Repository
    tag = $Tag
    release_url = $Published.html_url
    upload_verified = $true
    assets = @($PublishedAssets | Select-Object name,size,browser_download_url)
  } | ConvertTo-Json -Depth 6
} finally {
  $Client.Dispose()
}
