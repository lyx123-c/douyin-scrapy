param(
  [string]$PythonExe = $env:DOUYIN_PYTHON_PATH
)

$ErrorActionPreference = 'Stop'
$FrontendRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $FrontendRoot))
$ManifestScript = Join-Path $PSScriptRoot 'generate_release_manifest.py'

if (-not $PythonExe) {
  $candidates = @(
    (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.venv\Scripts\python.exe'),
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    (Get-Command py -ErrorAction SilentlyContinue).Source
  )
  $PythonExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
  throw 'Python executable was not found. Set DOUYIN_PYTHON_PATH and retry.'
}
if (-not (Test-Path -LiteralPath $ManifestScript -PathType Leaf)) {
  throw "Release manifest script is missing: $ManifestScript"
}

& $PythonExe $ManifestScript
if ($LASTEXITCODE -ne 0) {
  throw "Release manifest generation failed with exit code $LASTEXITCODE."
}
