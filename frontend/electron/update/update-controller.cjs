const path = require('node:path')

const { verifyArtifactFile, verifyUpdateManifest } = require('./manifest-verifier.cjs')
const { downloadUpdatePackage } = require('./package-downloader.cjs')
const { UpdateProtocolError } = require('./update-errors.cjs')
const { fetchUpdateManifest } = require('./update-source.cjs')

const UPDATE_STATUSES = Object.freeze({
  IDLE: 'IDLE',
  CHECKING: 'CHECKING',
  UP_TO_DATE: 'UP_TO_DATE',
  UPDATE_AVAILABLE: 'UPDATE_AVAILABLE',
  SOURCE_NOT_CONFIGURED: 'SOURCE_NOT_CONFIGURED',
  NETWORK_ERROR: 'NETWORK_ERROR',
  MANIFEST_INVALID: 'MANIFEST_INVALID',
  SIGNATURE_INVALID: 'SIGNATURE_INVALID',
  VERSION_UNSUPPORTED: 'VERSION_UNSUPPORTED',
  DOWNLOADING: 'DOWNLOADING',
  DOWNLOAD_FAILED: 'DOWNLOAD_FAILED',
  DOWNLOAD_CANCELLED: 'DOWNLOAD_CANCELLED',
  READY_TO_INSTALL: 'READY_TO_INSTALL',
  UPDATE_SUCCEEDED: 'UPDATE_SUCCEEDED',
  UPDATE_FAILED: 'UPDATE_FAILED',
  INSTALLING: 'INSTALLING',
  INSTALL_FAILED: 'INSTALL_FAILED',
})

const NETWORK_ERROR_CODES = new Set([
  'UPDATE_NETWORK_ERROR',
  'UPDATE_SOURCE_NOT_FOUND',
  'UPDATE_SOURCE_HTTP_ERROR',
  'UPDATE_DOWNLOAD_HTTP_ERROR',
  'UPDATE_DOWNLOAD_NETWORK_ERROR',
])

const SIGNATURE_ERROR_CODES = new Set([
  'UPDATE_SIGNATURE_INVALID',
  'UPDATE_SIGNATURE_KEY_UNKNOWN',
  'UPDATE_SIGNATURE_KEY_REVOKED',
  'UPDATE_SIGNATURE_KEY_UNAVAILABLE',
  'UPDATE_SIGNATURE_ACTIVE_KEY_REQUIRED',
])

const VERSION_ERROR_CODES = new Set([
  'UPDATE_VERSION_DOWNGRADE',
  'UPDATE_CURRENT_VERSION_UNSUPPORTED',
  'UPDATE_UPDATER_TOO_OLD',
  'UPDATE_BASE_VERSION_INCOMPATIBLE',
  'UPDATE_REPLAY_DETECTED',
])

function mapProtocolError(error) {
  if (!(error instanceof UpdateProtocolError)) {
    return UPDATE_STATUSES.MANIFEST_INVALID
  }
  if (error.code === 'UPDATE_SOURCE_NOT_CONFIGURED') return UPDATE_STATUSES.SOURCE_NOT_CONFIGURED
  if (NETWORK_ERROR_CODES.has(error.code)) return UPDATE_STATUSES.NETWORK_ERROR
  if (SIGNATURE_ERROR_CODES.has(error.code)) return UPDATE_STATUSES.SIGNATURE_INVALID
  if (VERSION_ERROR_CODES.has(error.code)) return UPDATE_STATUSES.VERSION_UNSUPPORTED
  return UPDATE_STATUSES.MANIFEST_INVALID
}

function finiteSize(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}

class UpdateController {
  constructor({
    currentVersion,
    currentUpdaterVersion,
    productId,
    channel = 'stable',
    sourceConfig,
    keyring,
    tempRoot,
    fetchManifestImpl = fetchUpdateManifest,
    onStateChanged = null,
    prepareInstall,
    recoverInstall = null,
    launchInstaller,
    fetchImpl = globalThis.fetch,
    now = () => new Date(),
    allowInsecureLocalhost = false,
  }) {
    this.currentVersion = currentVersion
    this.currentUpdaterVersion = currentUpdaterVersion
    this.productId = productId
    this.channel = channel
    this.sourceConfig = sourceConfig
    this.keyring = keyring
    this.tempRoot = path.resolve(tempRoot)
    this.fetchManifestImpl = fetchManifestImpl
    this.onStateChanged = onStateChanged
    this.prepareInstall = prepareInstall
    this.recoverInstall = recoverInstall
    this.launchInstaller = launchInstaller
    this.fetchImpl = fetchImpl
    this.now = now
    this.allowInsecureLocalhost = allowInsecureLocalhost
    this.activeDownload = null
    this.latestManifest = null
    this.selectedPackage = null
    this.installerPath = null
    this.state = {
      status: UPDATE_STATUSES.IDLE,
      current_version: currentVersion,
      latest_version: null,
      channel,
      published_at: null,
      release_notes: null,
      package_size: null,
      package_type: null,
      downloaded_bytes: 0,
      total_bytes: 0,
      percent: 0,
      bytes_per_second: 0,
      can_cancel: false,
      error_code: null,
      error_message: null,
      updated_at: now().toISOString(),
    }
  }

  getState() {
    return { ...this.state }
  }

  getInstallerPath() {
    return this.installerPath
  }

  applyStartupResult(result) {
    if (!result || result.schema_version !== 1) return null
    if (result.status === 'installed' && result.target_version === this.currentVersion) {
      return this.setState(UPDATE_STATUSES.UPDATE_SUCCEEDED, {
        latest_version: this.currentVersion,
        published_at: result.updated_at || null,
        package_size: null,
        package_type: 'full',
        percent: 100,
        error_code: null,
        error_message: null,
      })
    }
    if (result.status === 'failed' || result.status === 'installed') {
      return this.setState(UPDATE_STATUSES.UPDATE_FAILED, {
        latest_version: result.target_version || null,
        error_code: result.stage || 'UPDATE_INSTALL_FAILED',
        error_message: result.message || '更新安装失败。',
        can_cancel: false,
      })
    }
    return null
  }

  setState(status, patch = {}) {
    this.state = {
      ...this.state,
      ...patch,
      status,
      updated_at: this.now().toISOString(),
    }
    this.onStateChanged?.(this.getState())
    return this.getState()
  }

  async check() {
    if ([UPDATE_STATUSES.CHECKING, UPDATE_STATUSES.DOWNLOADING, UPDATE_STATUSES.INSTALLING].includes(this.state.status)) {
      throw new UpdateProtocolError('An update operation is already in progress.', 'UPDATE_OPERATION_BUSY')
    }
    this.latestManifest = null
    this.selectedPackage = null
    this.installerPath = null
    this.setState(UPDATE_STATUSES.CHECKING, {
      latest_version: null,
      published_at: null,
      release_notes: null,
      package_size: null,
      package_type: null,
      downloaded_bytes: 0,
      total_bytes: 0,
      percent: 0,
      bytes_per_second: 0,
      error_code: null,
      error_message: null,
    })

    if (!this.sourceConfig?.configured) {
      return this.setState(UPDATE_STATUSES.SOURCE_NOT_CONFIGURED, {
        error_code: 'UPDATE_SOURCE_NOT_CONFIGURED',
        error_message: '公共更新仓库尚未配置。',
      })
    }
    if (!this.keyring?.keys?.length) {
      return this.setState(UPDATE_STATUSES.SIGNATURE_INVALID, {
        error_code: 'UPDATE_SIGNATURE_ACTIVE_KEY_REQUIRED',
        error_message: '生产更新公钥尚未配置。',
      })
    }

    let manifest
    try {
      const response = await this.fetchManifestImpl(this.sourceConfig, {
        channel: this.channel,
        fetchImpl: this.fetchImpl,
      })
      manifest = response.manifest
    } catch (error) {
      const status = mapProtocolError(error)
      return this.setState(status, {
        error_code: error?.code || 'UPDATE_MANIFEST_INVALID',
        error_message: error?.message || String(error),
      })
    }

    try {
      const verification = verifyUpdateManifest(manifest, {
        keyring: this.keyring,
        expectedProductId: this.productId,
        expectedChannel: this.channel,
        currentVersion: this.currentVersion,
        currentUpdaterVersion: this.currentUpdaterVersion,
        allowedAssetHosts: this.sourceConfig.allowed_asset_hosts,
        allowedPackageTypes: ['full'],
        allowInsecureLocalhost: this.allowInsecureLocalhost,
        now: this.now(),
      })
      if (verification.status === 'up_to_date') {
        return this.setState(UPDATE_STATUSES.UP_TO_DATE, {
          latest_version: verification.latest_version,
          published_at: manifest.published_at,
          release_notes: null,
          package_size: null,
          package_type: null,
        })
      }

      this.latestManifest = manifest
      this.selectedPackage = verification.package
      return this.setState(UPDATE_STATUSES.UPDATE_AVAILABLE, {
        latest_version: verification.latest_version,
        channel: verification.channel,
        published_at: verification.published_at,
        release_notes: verification.release_notes,
        package_size: finiteSize(verification.package.size),
        package_type: verification.package.type,
        total_bytes: finiteSize(verification.package.size),
      })
    } catch (error) {
      const status = mapProtocolError(error)
      return this.setState(status, {
        latest_version: manifest?.version || null,
        error_code: error?.code || 'UPDATE_MANIFEST_INVALID',
        error_message: error?.message || String(error),
      })
    }
  }

  async startDownload() {
    if (this.state.status === UPDATE_STATUSES.DOWNLOADING) {
      throw new UpdateProtocolError('A download is already running.', 'UPDATE_DOWNLOAD_BUSY')
    }
    if (!this.latestManifest || !this.selectedPackage || this.selectedPackage.type !== 'full') {
      throw new UpdateProtocolError('No verified full installer package is available.', 'UPDATE_PACKAGE_UNAVAILABLE')
    }

    this.activeDownload = new AbortController()
    this.setState(UPDATE_STATUSES.DOWNLOADING, {
      downloaded_bytes: 0,
      total_bytes: this.selectedPackage.size,
      percent: 0,
      bytes_per_second: 0,
      can_cancel: true,
      error_code: null,
      error_message: null,
    })

    try {
      const filename = `DouyinWorkbench-${this.latestManifest.version}-x64.exe`
      const result = await downloadUpdatePackage({
        url: this.selectedPackage.url,
        expectedSize: this.selectedPackage.size,
        expectedSha256: this.selectedPackage.sha256,
        tempRoot: path.join(this.tempRoot, this.latestManifest.version),
        filename,
        allowedHosts: this.sourceConfig.allowed_asset_hosts,
        allowInsecureLocalhost: this.allowInsecureLocalhost,
        preferGithubApi: this.sourceConfig.prefer_github_api === true,
        requestTimeoutMs: this.sourceConfig.request_timeout_ms,
        fetchImpl: this.fetchImpl,
        signal: this.activeDownload.signal,
        onProgress: (progress) => {
          this.setState(UPDATE_STATUSES.DOWNLOADING, {
            downloaded_bytes: progress.downloadedBytes,
            total_bytes: progress.totalBytes,
            percent: progress.percent,
            bytes_per_second: progress.bytesPerSecond,
          })
        },
      })
      this.installerPath = result.path
      this.setState(UPDATE_STATUSES.READY_TO_INSTALL, {
        downloaded_bytes: result.size,
        total_bytes: result.size,
        percent: 100,
        bytes_per_second: 0,
        can_cancel: false,
      })
      return this.getState()
    } catch (error) {
      this.installerPath = null
      const cancelled = error?.code === 'UPDATE_DOWNLOAD_CANCELLED'
      if (!cancelled) {
        console.error(
          '[update] download failed',
          JSON.stringify({
            code: error?.code || 'UPDATE_DOWNLOAD_FAILED',
            stage: error?.details?.stage || null,
            status: error?.details?.status || null,
            host: error?.details?.host || null,
            cause: error?.details?.cause || null,
          }),
        )
      }
      return this.setState(
        cancelled ? UPDATE_STATUSES.DOWNLOAD_CANCELLED : UPDATE_STATUSES.DOWNLOAD_FAILED,
        {
          can_cancel: false,
          error_code: error?.code || 'UPDATE_DOWNLOAD_FAILED',
          error_message: error?.message || String(error),
        },
      )
    } finally {
      this.activeDownload = null
    }
  }

  cancelDownload() {
    if (!this.activeDownload) {
      throw new UpdateProtocolError('No active update download.', 'UPDATE_DOWNLOAD_NOT_ACTIVE')
    }
    this.activeDownload.abort()
    return { cancelling: true }
  }

  async install() {
    if (this.state.status !== UPDATE_STATUSES.READY_TO_INSTALL || !this.installerPath || !this.selectedPackage) {
      throw new UpdateProtocolError('No verified installer is ready.', 'UPDATE_INSTALLER_NOT_READY')
    }

    this.setState(UPDATE_STATUSES.INSTALLING, {
      error_code: null,
      error_message: null,
    })
    try {
      await verifyArtifactFile(this.installerPath, {
        expectedSize: this.selectedPackage.size,
        expectedSha256: this.selectedPackage.sha256,
      })
      await this.prepareInstall()
      await this.launchInstaller(this.installerPath, this.latestManifest.version)
      return this.getState()
    } catch (error) {
      if (typeof this.recoverInstall === 'function') {
        await this.recoverInstall().catch(() => {})
      }
      this.setState(UPDATE_STATUSES.INSTALL_FAILED, {
        error_code: error?.code || 'UPDATE_INSTALL_FAILED',
        error_message: error?.message || String(error),
      })
      throw error
    }
  }
}

module.exports = {
  UPDATE_STATUSES,
  UpdateController,
  mapProtocolError,
}
