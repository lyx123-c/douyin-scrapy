const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { buildHandoffScript } = require('../electron/update/installer-handoff.cjs')
const { createKeyringEntry, signManifest } = require('../electron/update/signature.cjs')
const { UpdateController, UPDATE_STATUSES } = require('../electron/update/update-controller.cjs')

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

function makeKey() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519')
  return {
    keyId: 'release-test',
    privateKey,
    entry: createKeyringEntry({ keyId: 'release-test', publicKey, status: 'active' }),
  }
}

function makeManifest({ packageUrl, packageBytes, declaredSha256 = null }) {
  return {
    schema_version: 1,
    product_id: 'com.local.douyinworkbench',
    channel: 'stable',
    version: '1.3.0',
    tag: 'v1.3.0',
    commit: 'abcdef1234567890',
    build_time: '2026-11-23T10:00:00.000Z',
    release_sequence: 20,
    published_at: '2026-11-23T10:05:00.000Z',
    min_supported_version: '1.0.0',
    minimum_updater_version: '0.1.0',
    release_notes: 'Full installer update.',
    files: [
      { path: 'program/runtime.txt', size: 1, sha256: 'a'.repeat(64) },
    ],
    changes: { added: [], replaced: [], removed: [] },
    packages: [{
      package_id: 'full-1.3.0',
      type: 'full',
      base_version: null,
      target_version: '1.3.0',
      archive_format: 'nsis',
      delta_algorithm: 'none',
      url: packageUrl,
      size: packageBytes.length,
      sha256: declaredSha256 || sha256(packageBytes),
    }],
    signatures: [],
  }
}

async function startServer(handler) {
  const server = http.createServer(handler)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  return {
    server,
    url: `http://127.0.0.1:${address.port}`,
  }
}

function makeController({ manifest, tempRoot, onStateChanged }) {
  const key = makeKey()
  const signed = signManifest(manifest, [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  const sourceConfig = {
    schema_version: 1,
    provider: 'github_releases',
    repository: 'example/douyin-updates',
    allowed_manifest_hosts: ['github.com'],
    allowed_asset_hosts: ['127.0.0.1'],
    request_timeout_ms: 5000,
    configured: true,
  }
  let controller
  controller = new UpdateController({
    currentVersion: '1.2.0',
    currentUpdaterVersion: '0.1.0',
    productId: 'com.local.douyinworkbench',
    channel: 'stable',
    sourceConfig,
    keyring,
    tempRoot,
    fetchManifestImpl: async () => ({ manifest: signed, channel: 'stable' }),
    prepareInstall: async () => {},
    launchInstaller: async () => {},
    allowInsecureLocalhost: true,
    onStateChanged,
  })
  return controller
}

test('full installer update checks, downloads, verifies and becomes installable', async (t) => {
  const packageBytes = crypto.randomBytes(128 * 1024)
  const server = await startServer((request, response) => {
    if (request.url !== '/installer.exe') {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': packageBytes.length,
    })
    response.end(packageBytes)
  })
  t.after(() => new Promise((resolve) => server.server.close(resolve)))
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-download-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))

  const manifest = makeManifest({
    packageUrl: `${server.url}/installer.exe`,
    packageBytes,
  })
  const states = []
  let launchedPath = null
  const controller = makeController({
    manifest,
    tempRoot,
    onStateChanged: (state) => states.push(state.status),
  })
  controller.launchInstaller = async (installerPath) => {
    launchedPath = installerPath
  }

  assert.equal((await controller.check()).status, UPDATE_STATUSES.UPDATE_AVAILABLE)
  const ready = await controller.startDownload()
  assert.equal(ready.status, UPDATE_STATUSES.READY_TO_INSTALL)
  assert.equal(ready.percent, 100)
  assert.ok(fs.existsSync(controller.getInstallerPath()))
  assert.equal(fs.readdirSync(path.dirname(controller.getInstallerPath())).some((name) => name.endsWith('.part')), false)

  await controller.install()
  assert.equal(launchedPath, controller.getInstallerPath())
  assert.ok(states.includes(UPDATE_STATUSES.CHECKING))
  assert.ok(states.includes(UPDATE_STATUSES.DOWNLOADING))
  assert.ok(states.includes(UPDATE_STATUSES.READY_TO_INSTALL))
})

test('SHA256 mismatch never creates a runnable installer', async (t) => {
  const packageBytes = crypto.randomBytes(32 * 1024)
  const server = await startServer((_request, response) => {
    response.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': packageBytes.length,
    })
    response.end(packageBytes)
  })
  t.after(() => new Promise((resolve) => server.server.close(resolve)))
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-bad-sha-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))

  const manifest = makeManifest({
    packageUrl: `${server.url}/installer.exe`,
    packageBytes,
    declaredSha256: 'f'.repeat(64),
  })
  const controller = makeController({ manifest, tempRoot })
  assert.equal((await controller.check()).status, UPDATE_STATUSES.UPDATE_AVAILABLE)
  const failed = await controller.startDownload()
  assert.equal(failed.status, UPDATE_STATUSES.DOWNLOAD_FAILED)
  assert.equal(failed.error_code, 'UPDATE_ARTIFACT_HASH_MISMATCH')
  assert.equal(controller.getInstallerPath(), null)
  const versionDir = path.join(tempRoot, '1.3.0')
  assert.equal(fs.existsSync(versionDir), false)
})

test('cancelling a download leaves no partial or executable update package', async (t) => {
  const chunk = crypto.randomBytes(16 * 1024)
  const server = await startServer((_request, response) => {
    response.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': chunk.length * 20,
    })
    let sent = 0
    const timer = setInterval(() => {
      if (sent >= 20) {
        clearInterval(timer)
        response.end()
        return
      }
      sent += 1
      response.write(chunk)
    }, 50)
    response.on('close', () => clearInterval(timer))
  })
  t.after(() => new Promise((resolve) => server.server.close(resolve)))
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-cancel-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  const packageBytes = Buffer.concat(Array.from({ length: 20 }, () => chunk))
  const manifest = makeManifest({
    packageUrl: `${server.url}/installer.exe`,
    packageBytes,
  })
  let controller
  let cancelled = false
  controller = makeController({
    manifest,
    tempRoot,
    onStateChanged: (state) => {
      if (!cancelled && state.status === UPDATE_STATUSES.DOWNLOADING && state.downloaded_bytes > 0) {
        cancelled = true
        controller.cancelDownload()
      }
    },
  })
  assert.equal((await controller.check()).status, UPDATE_STATUSES.UPDATE_AVAILABLE)
  const result = await controller.startDownload()
  assert.equal(result.status, UPDATE_STATUSES.DOWNLOAD_CANCELLED)
  assert.equal(controller.getInstallerPath(), null)
  assert.equal(fs.existsSync(path.join(tempRoot, '1.3.0')), false)
})

test('installer launch failure keeps the verified package and requests backend recovery', async (t) => {
  const packageBytes = crypto.randomBytes(16 * 1024)
  const server = await startServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': packageBytes.length })
    response.end(packageBytes)
  })
  t.after(() => new Promise((resolve) => server.server.close(resolve)))
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-install-fail-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  let recovered = 0
  const controller = makeController({
    manifest: makeManifest({ packageUrl: `${server.url}/installer.exe`, packageBytes }),
    tempRoot,
  })
  controller.recoverInstall = async () => { recovered += 1 }
  controller.launchInstaller = async () => { throw new Error('handoff spawn failed') }
  await controller.check()
  await controller.startDownload()
  const installerPath = controller.getInstallerPath()
  await assert.rejects(controller.install(), /handoff spawn failed/)
  assert.equal(controller.getState().status, UPDATE_STATUSES.INSTALL_FAILED)
  assert.equal(recovered, 1)
  assert.ok(fs.existsSync(installerPath))
})

test('startup update result reports success or failure after restart', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-result-'))
  try {
    const key = makeKey()
    const controller = new UpdateController({
      currentVersion: '1.3.0',
      currentUpdaterVersion: '0.1.0',
      productId: 'com.local.douyinworkbench',
      sourceConfig: { configured: false, allowed_asset_hosts: [] },
      keyring: { schema_version: 1, keys: [key.entry], updated_at: null },
      tempRoot,
      prepareInstall: async () => {},
      launchInstaller: async () => {},
    })
    controller.applyStartupResult({
      schema_version: 1,
      status: 'installed',
      target_version: '1.3.0',
      actual_version: '1.3.0',
      updated_at: '2026-11-23T10:00:00.000Z',
    })
    assert.equal(controller.getState().status, UPDATE_STATUSES.UPDATE_SUCCEEDED)
    controller.applyStartupResult({
      schema_version: 1,
      status: 'failed',
      stage: 'installed_version_mismatch',
      message: 'version mismatch',
      target_version: '1.4.0',
    })
    assert.equal(controller.getState().status, UPDATE_STATUSES.UPDATE_FAILED)
    assert.equal(controller.getState().error_code, 'installed_version_mismatch')
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('unconfigured source and invalid signatures surface safe controller states', async () => {
  const key = makeKey()
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-u12-state-'))
  try {
    const sourceController = new UpdateController({
      currentVersion: '1.2.0',
      currentUpdaterVersion: '0.1.0',
      productId: 'com.local.douyinworkbench',
      sourceConfig: { configured: false, allowed_asset_hosts: [] },
      keyring: { schema_version: 1, keys: [key.entry], updated_at: null },
      tempRoot,
      prepareInstall: async () => {},
      launchInstaller: async () => {},
    })
    assert.equal((await sourceController.check()).status, UPDATE_STATUSES.SOURCE_NOT_CONFIGURED)

    const emptyKeyringController = new UpdateController({
      currentVersion: '1.2.0',
      currentUpdaterVersion: '0.1.0',
      productId: 'com.local.douyinworkbench',
      sourceConfig: { configured: true, allowed_asset_hosts: ['github.com'] },
      keyring: { schema_version: 1, keys: [], updated_at: null },
      tempRoot,
      prepareInstall: async () => {},
      launchInstaller: async () => {},
    })
    assert.equal((await emptyKeyringController.check()).status, UPDATE_STATUSES.SIGNATURE_INVALID)

    const packageBytes = Buffer.from('signed package')
    const manifest = signManifest(makeManifest({
      packageUrl: 'https://github.com/example/updates/releases/download/v1.3.0/setup.exe',
      packageBytes,
    }), [{ keyId: key.keyId, privateKey: key.privateKey }])
    manifest.release_notes = 'tampered'
    const signatureController = new UpdateController({
      currentVersion: '1.2.0',
      currentUpdaterVersion: '0.1.0',
      productId: 'com.local.douyinworkbench',
      sourceConfig: {
        configured: true,
        allowed_asset_hosts: ['github.com'],
      },
      keyring: { schema_version: 1, keys: [key.entry], updated_at: null },
      tempRoot,
      fetchManifestImpl: async () => ({ manifest }),
      prepareInstall: async () => {},
      launchInstaller: async () => {},
    })
    assert.equal((await signatureController.check()).status, UPDATE_STATUSES.SIGNATURE_INVALID)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('installer handoff waits for the Electron process and never uses a fixed sleep', () => {
  const script = buildHandoffScript({
    electronPid: 1234,
    installerPath: 'C:\\Temp\\verified-installer.exe',
    appExePath: 'C:\\Temp\\DouyinWorkbench\\DouyinWorkbench.exe',
    installRoot: 'C:\\Temp\\DouyinWorkbench',
    targetVersion: '0.2.0',
    resultPath: 'C:\\Temp\\update-result.json',
  })
  assert.match(script, /Wait-Process -Id \$targetPid -Timeout \$waitSeconds/)
  assert.match(script, /ArgumentList "\/S"/)
  assert.match(script, /WaitForExit/)
  assert.match(script, /\$actualVersion -ne \$targetVersion/)
  assert.match(script, /Start-Process -FilePath \$appExe -ArgumentList "--post-update"/)
  assert.match(script, /Start-Process -FilePath \$installer/)
  assert.doesNotMatch(script, /Start-Sleep|timeout\s+\/t/i)
})
