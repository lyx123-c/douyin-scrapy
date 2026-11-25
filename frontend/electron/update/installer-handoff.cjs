const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

function escapePowerShellLiteral(value) {
  return String(value).replace(/'/g, "''")
}

function validateAbsolutePath(value, label, extension = null) {
  if (!path.isAbsolute(String(value || ''))) {
    throw new Error(`${label} must be absolute.`)
  }
  if (extension && path.extname(value).toLowerCase() !== extension) {
    throw new Error(`${label} must use ${extension} extension.`)
  }
}

function isPathWithin(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate))
  return relative === ''
    || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
}

function stageInstallerForExecution({
  installerPath,
  installRoot,
  stagingBase = path.join(os.tmpdir(), 'DouyinWorkbenchUpdates'),
}) {
  validateAbsolutePath(installerPath, 'Installer path', '.exe')
  validateAbsolutePath(installRoot, 'Install root')
  validateAbsolutePath(stagingBase, 'Installer staging root')

  const sourcePath = path.resolve(installerPath)
  if (!fs.existsSync(sourcePath) || !fs.statSync(sourcePath).isFile()) {
    throw new Error('Verified installer file is missing.')
  }

  const resolvedInstallRoot = path.resolve(installRoot)
  if (!isPathWithin(resolvedInstallRoot, sourcePath)) {
    return {
      path: sourcePath,
      directory: null,
    }
  }

  const resolvedStagingBase = path.resolve(stagingBase)
  if (isPathWithin(resolvedInstallRoot, resolvedStagingBase)) {
    throw new Error('Installer staging root must be outside the installation directory.')
  }

  fs.mkdirSync(resolvedStagingBase, { recursive: true })
  const stagingDirectory = fs.mkdtempSync(path.join(resolvedStagingBase, 'install-'))
  const stagedPath = path.join(stagingDirectory, path.basename(sourcePath))
  if (isPathWithin(resolvedInstallRoot, stagedPath)) {
    fs.rmSync(stagingDirectory, { recursive: true, force: true })
    throw new Error('Staged installer path must be outside the installation directory.')
  }

  try {
    fs.copyFileSync(sourcePath, stagedPath, fs.constants.COPYFILE_EXCL)
  } catch (error) {
    fs.rmSync(stagingDirectory, { recursive: true, force: true })
    throw error
  }

  return {
    path: stagedPath,
    directory: stagingDirectory,
  }
}

function buildHandoffScript({
  electronPid,
  installerPath,
  stagingDirectory = null,
  appExePath,
  installRoot,
  targetVersion,
  resultPath,
  waitSeconds = 90,
  installTimeoutSeconds = 600,
}) {
  if (!Number.isSafeInteger(electronPid) || electronPid <= 0) {
    throw new Error('Invalid Electron PID for installer handoff.')
  }
  validateAbsolutePath(installerPath, 'Installer path', '.exe')
  if (stagingDirectory) {
    validateAbsolutePath(stagingDirectory, 'Installer staging directory')
  }
  validateAbsolutePath(appExePath, 'Application path', '.exe')
  validateAbsolutePath(installRoot, 'Install root')
  validateAbsolutePath(resultPath, 'Result path', '.json')
  if (typeof targetVersion !== 'string' || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(targetVersion)) {
    throw new Error('Invalid target version.')
  }
  if (!Number.isSafeInteger(waitSeconds) || waitSeconds < 5 || waitSeconds > 600) {
    throw new Error('Invalid installer handoff timeout.')
  }
  if (!Number.isSafeInteger(installTimeoutSeconds) || installTimeoutSeconds < 30 || installTimeoutSeconds > 3600) {
    throw new Error('Invalid installer timeout.')
  }

  const installDir = path.resolve(installRoot)
  const versionMetadataPath = path.join(installDir, 'resources', 'version-metadata.json')

  return [
    "$ErrorActionPreference = 'Stop'",
    `$targetPid = ${electronPid}`,
    `$installer = '${escapePowerShellLiteral(path.resolve(installerPath))}'`,
    stagingDirectory
      ? `$installerStagingDirectory = '${escapePowerShellLiteral(path.resolve(stagingDirectory))}'`
      : '$installerStagingDirectory = $null',
    `$appExe = '${escapePowerShellLiteral(path.resolve(appExePath))}'`,
    `$installRoot = '${escapePowerShellLiteral(installDir)}'`,
    `$targetVersion = '${escapePowerShellLiteral(targetVersion)}'`,
    `$resultPath = '${escapePowerShellLiteral(path.resolve(resultPath))}'`,
    `$versionMetadataPath = '${escapePowerShellLiteral(versionMetadataPath)}'`,
    `$waitSeconds = ${waitSeconds}`,
    `$installTimeoutSeconds = ${installTimeoutSeconds}`,
    '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
    '$installRootPrefix = $installRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar',
    'function Get-InstallRootProcesses {',
    '  @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue | Where-Object {',
    '    $processPath = $_.ExecutablePath',
    '    $processPath -and [System.IO.Path]::GetFullPath($processPath).StartsWith($installRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)',
    '  })',
    '}',
    'function Remove-InstallerStaging {',
    '  if ($installerStagingDirectory -and [System.IO.Directory]::Exists($installerStagingDirectory)) {',
    '    Remove-Item -LiteralPath $installerStagingDirectory -Recurse -Force -ErrorAction SilentlyContinue',
    '  }',
    '}',
    'function Write-UpdateResult([string]$status, [string]$stage, [string]$message, $installerExitCode, $actualVersion) {',
    '  $payload = [ordered]@{',
    '    schema_version = 1',
    '    status = $status',
    '    stage = $stage',
    '    message = $message',
    '    target_version = $targetVersion',
    '    actual_version = $actualVersion',
    '    installer_exit_code = $installerExitCode',
    '    install_root = $installRoot',
    '    updated_at = (Get-Date).ToUniversalTime().ToString("o")',
    '  }',
    '  [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($resultPath)) | Out-Null',
    '  [System.IO.File]::WriteAllText($resultPath, ($payload | ConvertTo-Json -Depth 5), $utf8NoBom)',
    '}',
    "Write-UpdateResult 'waiting' 'wait_electron_exit' '等待旧版本完全退出。' $null $null",
    '$target = Get-Process -Id $targetPid -ErrorAction SilentlyContinue',
    'if ($target) {',
    '  try {',
    '    Wait-Process -Id $targetPid -Timeout $waitSeconds -ErrorAction Stop',
    '  } catch {',
    '    if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {',
    "      Write-UpdateResult 'failed' 'electron_exit_timeout' 'Electron 未在超时前退出。' $null $null",
    '      exit 2',
    '    }',
    '  }',
    '}',
    '$remainingInstallProcesses = @(Get-InstallRootProcesses)',
    'if ($remainingInstallProcesses.Count -gt 0) {',
    "  Write-UpdateResult 'waiting' 'wait_app_processes' '等待应用子进程完全退出。' $null $null",
    '  try {',
    '    Wait-Process -Id @($remainingInstallProcesses.ProcessId) -Timeout $waitSeconds -ErrorAction Stop',
    '  } catch {',
    '    if (@(Get-InstallRootProcesses).Count -gt 0) {',
    "      Write-UpdateResult 'failed' 'app_process_exit_timeout' '应用进程未在超时前完全退出。' $null $null",
    '      Remove-InstallerStaging',
    '      exit 2',
    '    }',
    '  }',
    '  if (@(Get-InstallRootProcesses).Count -gt 0) {',
    "    Write-UpdateResult 'failed' 'app_process_exit_timeout' '应用进程未在超时前完全退出。' $null $null",
    '    Remove-InstallerStaging',
    '    exit 2',
    '  }',
    '}',
    "Write-UpdateResult 'installing' 'installer_running' '正在执行 NSIS 覆盖安装。' $null $null",
    '$installerProcess = $null',
    'try {',
    '  $installerProcess = Start-Process -FilePath $installer -ArgumentList "/S" -PassThru -ErrorAction Stop',
    '} catch {',
    '  Remove-InstallerStaging',
    "  Write-UpdateResult 'failed' 'installer_start_failed' '无法启动 NSIS 安装程序。' $null $null",
    '  exit 3',
    '}',
    'if (-not $installerProcess.WaitForExit($installTimeoutSeconds * 1000)) {',
    "  Write-UpdateResult 'failed' 'installer_timeout' 'NSIS 安装程序超时。' $null $null",
    '  Remove-InstallerStaging',
    '  exit 3',
    '}',
    '$installerExitCode = $installerProcess.ExitCode',
    'Remove-InstallerStaging',
    'if ($installerExitCode -ne 0) {',
    "  Write-UpdateResult 'failed' 'installer_exit_nonzero' 'NSIS 安装程序返回非零退出码。' $installerExitCode $null",
    '  if (Test-Path -LiteralPath $appExe) { Start-Process -FilePath $appExe -ArgumentList "--update-recovery" }',
    '  exit 4',
    '}',
    'if (-not (Test-Path -LiteralPath $appExe) -or -not (Test-Path -LiteralPath $versionMetadataPath)) {',
    "  Write-UpdateResult 'failed' 'installed_files_missing' '安装后未找到应用或版本元数据。' $installerExitCode $null",
    '  if (Test-Path -LiteralPath $appExe) { Start-Process -FilePath $appExe -ArgumentList "--update-recovery" }',
    '  exit 5',
    '}',
    '$actualVersion = (Get-Content -LiteralPath $versionMetadataPath -Raw | ConvertFrom-Json).version',
    'if ($actualVersion -ne $targetVersion) {',
    "  Write-UpdateResult 'failed' 'installed_version_mismatch' '安装后的程序版本与目标版本不一致。' $installerExitCode $actualVersion",
    '  if (Test-Path -LiteralPath $appExe) { Start-Process -FilePath $appExe -ArgumentList "--update-recovery" }',
    '  exit 6',
    '}',
    "Write-UpdateResult 'installed' 'restart_pending' '安装完成，准备重启新版本。' $installerExitCode $actualVersion",
    'Start-Process -FilePath $appExe -ArgumentList "--post-update"',
  ].join('\r\n')
}

function encodePowerShell(script) {
  return Buffer.from(script, 'utf16le').toString('base64')
}

function launchInstallerAfterExit({
  electronPid,
  installerPath,
  stagingDirectory = null,
  appExePath,
  installRoot,
  targetVersion,
  resultPath,
  spawnImpl = spawn,
  platform = process.platform,
}) {
  if (platform !== 'win32') {
    throw new Error('Installer handoff is only supported on Windows.')
  }
  const script = buildHandoffScript({
    electronPid,
    installerPath,
    stagingDirectory,
    appExePath,
    installRoot,
    targetVersion,
    resultPath,
  })
  const executable = path.join(
    process.env.SystemRoot || 'C:\\Windows',
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe',
  )
  const child = spawnImpl(executable, [
    '-NoProfile',
    '-NonInteractive',
    '-WindowStyle',
    'Hidden',
    '-EncodedCommand',
    encodePowerShell(script),
  ], {
    detached: true,
    windowsHide: true,
    stdio: 'ignore',
  })
  if (!child?.pid) throw new Error('Unable to start the installer handoff process.')
  child.unref()
  return {
    pid: child.pid,
    script,
  }
}

module.exports = {
  buildHandoffScript,
  encodePowerShell,
  stageInstallerForExecution,
  launchInstallerAfterExit,
}
