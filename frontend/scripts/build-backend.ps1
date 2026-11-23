param(
  [string]$PythonExe = $env:DOUYIN_PYTHON_PATH
)

$ErrorActionPreference = 'Stop'
$FrontendRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $FrontendRoot))
$SpecPath = Join-Path $FrontendRoot 'backend.spec'
$RequirementsBuildPath = Join-Path $ProjectRoot 'requirements-build.txt'
$DistRoot = Join-Path $FrontendRoot 'backend-dist'
$BackendOutput = Join-Path $DistRoot 'Backend'
$WorkRoot = Join-Path $FrontendRoot 'backend-build\work'

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

& $PythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller is not installed. Run: `"$PythonExe`" -m pip install -r `"$RequirementsBuildPath`""
}

if (Test-Path -LiteralPath $BackendOutput) {
  $resolvedOutput = [System.IO.Path]::GetFullPath($BackendOutput)
  if (-not $resolvedOutput.StartsWith($FrontendRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean build output outside frontend: $resolvedOutput"
  }
  Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot | Out-Null

& $PythonExe -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath $DistRoot `
  --workpath $WorkRoot `
  $SpecPath
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed with exit code: $LASTEXITCODE"
}

$BrowserRevision = (& $PythonExe -c "import json,pathlib,patchright; p=pathlib.Path(patchright.__file__).parent/'driver'/'package'/'browsers.json'; print(next(i['revision'] for i in json.loads(p.read_text(encoding='utf-8'))['browsers'] if i['name']=='chromium'))").Trim()
if (-not $BrowserRevision) {
  throw 'Could not read Chromium revision from patchright browsers.json.'
}

$BrowserCacheRoot = $env:PLAYWRIGHT_BROWSERS_PATH
if (-not $BrowserCacheRoot -or $BrowserCacheRoot -eq '0') {
  $BrowserCacheRoot = Join-Path $env:LOCALAPPDATA 'ms-playwright'
}
$BrowserSource = Join-Path $BrowserCacheRoot "chromium-$BrowserRevision"
if (-not (Test-Path -LiteralPath $BrowserSource)) {
  throw "Chromium $BrowserRevision was not found. Run: `"$PythonExe`" -m patchright install chromium"
}

$BrowserDestinationRoot = Join-Path $BackendOutput 'playwright-browsers'
$BrowserDestination = Join-Path $BrowserDestinationRoot "chromium-$BrowserRevision"
New-Item -ItemType Directory -Force -Path $BrowserDestinationRoot | Out-Null
Copy-Item -LiteralPath $BrowserSource -Destination $BrowserDestination -Recurse -Force

$Manifest = [ordered]@{
  schema = 1
  built_at = (Get-Date).ToString('o')
  python = (& $PythonExe --version).Trim()
  pyinstaller = (& $PythonExe -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
  patchright = (& $PythonExe -c "from importlib.metadata import version; print(version('patchright'))").Trim()
  scrapling = (& $PythonExe -c "from importlib.metadata import version; print(version('scrapling'))").Trim()
  chromium_revision = $BrowserRevision
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BackendOutput 'runtime-manifest.json') -Encoding UTF8

$BackendSize = (Get-ChildItem -LiteralPath $BackendOutput -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output "Backend build complete: $BackendOutput"
Write-Output "Chromium revision: $BrowserRevision"
Write-Output "Backend size bytes: $BackendSize"
