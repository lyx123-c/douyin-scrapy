const { spawn } = require('node:child_process')
const path = require('node:path')

function escapePowerShellLiteral(value) {
  return String(value).replace(/'/g, "''")
}

function buildHandoffScript({ electronPid, installerPath, waitSeconds = 90 }) {
  if (!Number.isSafeInteger(electronPid) || electronPid <= 0) {
    throw new Error('Invalid Electron PID for installer handoff.')
  }
  if (!path.isAbsolute(String(installerPath || ''))) {
    throw new Error('Installer path must be absolute.')
  }
  if (path.extname(installerPath).toLowerCase() !== '.exe') {
    throw new Error('Installer path must point to an EXE.')
  }
  if (!Number.isSafeInteger(waitSeconds) || waitSeconds < 5 || waitSeconds > 600) {
    throw new Error('Invalid installer handoff timeout.')
  }

  return [
    "$ErrorActionPreference = 'Stop'",
    `$targetPid = ${electronPid}`,
    `$installer = '${escapePowerShellLiteral(path.resolve(installerPath))}'`,
    `$waitSeconds = ${waitSeconds}`,
    '$target = Get-Process -Id $targetPid -ErrorAction SilentlyContinue',
    'if ($target) {',
    '  try {',
    '    Wait-Process -Id $targetPid -Timeout $waitSeconds -ErrorAction Stop',
    '  } catch {',
    '    if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) { exit 2 }',
    '  }',
    '}',
    "Start-Process -FilePath $installer -ErrorAction Stop",
  ].join('\r\n')
}

function encodePowerShell(script) {
  return Buffer.from(script, 'utf16le').toString('base64')
}

function launchInstallerAfterExit({
  electronPid,
  installerPath,
  spawnImpl = spawn,
  platform = process.platform,
}) {
  if (platform !== 'win32') {
    throw new Error('Installer handoff is only supported on Windows.')
  }
  const script = buildHandoffScript({ electronPid, installerPath })
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
  launchInstallerAfterExit,
}
