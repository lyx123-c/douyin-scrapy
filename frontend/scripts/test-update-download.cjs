const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { buildHandoffScript } = require('../electron/update/installer-handoff.cjs')
const { downloadUpdatePackage } = require('../electron/update/package-downloader.cjs')
const { createKeyringEntry, signManifest } = require('../electron/update/signature.cjs')
const { UpdateController, UPDATE_STATUSES } = require('../electron/update/update-controller.cjs')

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

function streamResponse(bytes, { url, contentType = 'application/octet-stream', status = 200 } = {}) {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(bytes))
      controller.close()
    },
  })
  return {
    ok: status >= 200 && status < 300,
    status,
    url,
    headers: new Headers({
      'content-type': contentType,
      'content-length': String(bytes.length),
    }),
    body,
  }
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
  assert.equal(ready.installer_path, controller.getInstallerPath())
  assert.ok(fs.existsSync(controller.getInstallerPath()))
  assert.equal(path.dirname(controller.getInstallerPath()), tempRoot)
  assert.equal(path.basename(controller.getInstallerPath()), 'DouyinWorkbench-1.3.0-x64.exe')
  assert.equal(fs.readdirSync(path.dirname(controller.getInstallerPath())).some((name) => name.endsWith('.part')), false)

  await controller.install()
  assert.equal(launchedPath, controller.getInstallerPath())
  assert.ok(states.includes(UPDATE_STATUSES.CHECKING))
  assert.ok(states.includes(UPDATE_STATUSES.DOWNLOADING))
  assert.ok(states.includes(UPDATE_STATUSES.READY_TO_INSTALL))
})

test('GitHub package download resolves the asset through api.github.com', async (t) => {
  const packageBytes = crypto.randomBytes(64 * 1024)
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-github-api-download-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  const calls = []
  const fetchImpl = async (url) => {
    calls.push(url)
    if (url === 'https://api.github.com/repos/lyx123-c/douyin-scrapy/releases/tags/v1.3.0') {
      return {
        ok: true,
        status: 200,
        url,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          assets: [{
            name: 'installer.exe',
            url: 'https://api.github.com/repos/lyx123-c/douyin-scrapy/releases/assets/123',
          }],
        }),
      }
    }
    if (url === 'https://api.github.com/repos/lyx123-c/douyin-scrapy/releases/assets/123') {
      return streamResponse(packageBytes, {
        url: 'https://release-assets.githubusercontent.com/github-production-release-asset/installer.exe',
      })
    }
    throw new Error(`unexpected URL: ${url}`)
  }

  const result = await downloadUpdatePackage({
    url: 'https://github.com/lyx123-c/douyin-scrapy/releases/download/v1.3.0/installer.exe',
    expectedSize: packageBytes.length,
    expectedSha256: sha256(packageBytes),
    tempRoot,
    filename: 'installer.exe',
    allowedHosts: [
      'api.github.com',
      'github.com',
      'release-assets.githubusercontent.com',
    ],
    preferGithubApi: true,
    fetchImpl,
  })

  assert.equal(calls.length, 2)
  assert.match(calls[0], /api\.github\.com\/repos\/lyx123-c\/douyin-scrapy\/releases\/tags\/v1\.3\.0/)
  assert.equal(result.size, packageBytes.length)
  assert.equal(result.sha256, sha256(packageBytes))
  assert.equal(fs.readFileSync(result.path).equals(packageBytes), true)
})

test('interrupted GitHub download resumes with a Range request', async (t) => {
  const packageBytes = crypto.randomBytes(64 * 1024)
  const splitAt = packageBytes.length / 2
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-range-resume-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  const calls = []
  let assetRequests = 0
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options })
    if (url.includes('/releases/tags/')) {
      return {
        ok: true,
        status: 200,
        url,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          assets: [{ name: 'installer.exe', url: 'https://api.github.com/assets/resume' }],
        }),
      }
    }
    if (url === 'https://api.github.com/assets/resume') {
      assetRequests += 1
      if (assetRequests === 1) {
        let sent = false
        const body = new ReadableStream({
          pull(controller) {
            if (!sent) {
              sent = true
              controller.enqueue(new Uint8Array(packageBytes.subarray(0, splitAt)))
              return
            }
            controller.error(new Error('terminated'))
          },
        })
        return {
          ok: true,
          status: 200,
          url: 'https://release-assets.githubusercontent.com/installer.exe',
          headers: new Headers({
            'content-type': 'application/octet-stream',
            'content-length': String(packageBytes.length),
          }),
          body,
        }
      }
      return streamResponse(packageBytes.subarray(splitAt), {
        url: 'https://release-assets.githubusercontent.com/installer.exe',
        status: 206,
      })
    }
    throw new Error(`unexpected URL: ${url}`)
  }

  const result = await downloadUpdatePackage({
    url: 'https://github.com/owner/repo/releases/download/v1.3.0/installer.exe',
    expectedSize: packageBytes.length,
    expectedSha256: sha256(packageBytes),
    tempRoot,
    filename: 'installer.exe',
    allowedHosts: ['api.github.com', 'github.com', 'release-assets.githubusercontent.com'],
    preferGithubApi: true,
    maxAttempts: 3,
    retryDelayMs: 1,
    fetchImpl,
  })

  const assetCalls = calls.filter((call) => call.url === 'https://api.github.com/assets/resume')
  assert.equal(assetCalls.length, 2)
  assert.equal(assetCalls[0].options.headers.Range, undefined)
  assert.equal(assetCalls[1].options.headers.Range, `bytes=${splitAt}-`)
  assert.equal(fs.readFileSync(result.path).equals(packageBytes), true)
})

test('HTML error page is rejected instead of treated as an installer', async (t) => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-html-download-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  const fetchImpl = async (url) => {
    if (url.includes('/releases/tags/')) {
      return {
        ok: true,
        status: 200,
        url,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          assets: [{ name: 'installer.exe', url: 'https://api.github.com/assets/404-page' }],
        }),
      }
    }
    return streamResponse(Buffer.from('<html>error</html>'), {
      url: 'https://release-assets.githubusercontent.com/error.html',
      contentType: 'text/html',
    })
  }

  await assert.rejects(
    downloadUpdatePackage({
      url: 'https://github.com/owner/repo/releases/download/v1.3.0/installer.exe',
      expectedSize: 18,
      expectedSha256: 'a'.repeat(64),
      tempRoot,
      filename: 'installer.exe',
      allowedHosts: ['api.github.com', 'github.com', 'release-assets.githubusercontent.com'],
      preferGithubApi: true,
      fetchImpl,
    }),
    (error) => (
      error.code === 'UPDATE_DOWNLOAD_INVALID_CONTENT_TYPE'
      && error.details?.stage === 'download_response'
    ),
  )
  assert.equal(require('node:fs').existsSync(path.join(tempRoot, 'installer.exe')), false)
})

test('HTTP download failures preserve status and failure stage', async (t) => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-http-status-'))
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
  const fetchImpl = async (url) => {
    if (url.includes('/releases/tags/')) {
      return {
        ok: true,
        status: 200,
        url,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          assets: [{ name: 'installer.exe', url: 'https://api.github.com/assets/forbidden' }],
        }),
      }
    }
    return {
      ok: false,
      status: 403,
      url: 'https://api.github.com/assets/forbidden',
      headers: new Headers({ 'content-type': 'application/json' }),
      body: null,
    }
  }

  await assert.rejects(
    downloadUpdatePackage({
      url: 'https://github.com/owner/repo/releases/download/v1.3.0/installer.exe',
      expectedSize: 100,
      expectedSha256: 'a'.repeat(64),
      tempRoot,
      filename: 'installer.exe',
      allowedHosts: ['api.github.com', 'github.com'],
      preferGithubApi: true,
      fetchImpl,
    }),
    (error) => (
      error.code === 'UPDATE_DOWNLOAD_HTTP_ERROR'
      && error.details?.status === 403
      && error.details?.stage === 'download_response'
    ),
  )
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
  assert.equal(failed.installer_path, null)
  assert.equal(controller.getInstallerPath(), null)
  assert.equal(
    fs.readdirSync(tempRoot).some((name) => name.endsWith('.exe') || name.endsWith('.part')),
    false,
  )
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
  assert.equal(result.installer_path, null)
  assert.equal(controller.getInstallerPath(), null)
  assert.equal(
    fs.readdirSync(tempRoot).some((name) => name.endsWith('.exe') || name.endsWith('.part')),
    false,
  )
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
