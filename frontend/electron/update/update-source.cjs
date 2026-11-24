const fs = require('node:fs')

const { fetchManifestViaGithubApi } = require('./github-release-api.cjs')

const { UpdateProtocolError } = require('./update-errors.cjs')

const REPOSITORY_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const ASSET_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

function isPlainObject(value) {
  return Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
}

function parseUrl(value, hosts, label) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new UpdateProtocolError(`Invalid ${label} URL.`, 'UPDATE_SOURCE_URL_INVALID')
  }
  if (parsed.protocol !== 'https:') {
    throw new UpdateProtocolError(`${label} URL must use HTTPS.`, 'UPDATE_SOURCE_URL_INVALID')
  }
  if (parsed.username || parsed.password) {
    throw new UpdateProtocolError(`${label} URL must not contain credentials.`, 'UPDATE_SOURCE_URL_INVALID')
  }
  if (!hosts.includes(parsed.hostname.toLowerCase())) {
    throw new UpdateProtocolError(
      `${label} host is not allowed: ${parsed.hostname}`,
      'UPDATE_SOURCE_HOST_DENIED',
    )
  }
  return parsed
}

function validateUpdateSourceConfig(config, { developmentRepository = 'lyx123-c/douyin-scrapy' } = {}) {
  if (!isPlainObject(config) || config.schema_version !== 1) {
    throw new UpdateProtocolError('Unsupported update source configuration.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }
  if (config.provider !== 'github_releases') {
    throw new UpdateProtocolError('Only github_releases is supported in U1.1.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }
  if (typeof config.repository !== 'string') {
    throw new UpdateProtocolError('Update repository must be a string.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }
  if (config.repository) {
    if (!REPOSITORY_RE.test(config.repository)) {
      throw new UpdateProtocolError('Update repository must use owner/repo format.', 'UPDATE_SOURCE_CONFIG_INVALID')
    }
    if (config.repository.toLowerCase() === String(developmentRepository).toLowerCase()) {
      const explicitlyAllowed = config.allow_development_repository_as_update_source === true
      if (!explicitlyAllowed) {
        throw new UpdateProtocolError(
          'The development repository requires explicit public-update-source approval.',
          'UPDATE_SOURCE_REPOSITORY_CONFLICT',
        )
      }
    }
  }
  if (!Array.isArray(config.allowed_manifest_hosts) || config.allowed_manifest_hosts.length === 0) {
    throw new UpdateProtocolError('Manifest host allowlist is missing.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }
  if (!Array.isArray(config.allowed_asset_hosts) || config.allowed_asset_hosts.length === 0) {
    throw new UpdateProtocolError('Asset host allowlist is missing.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }
  const timeout = Number(config.request_timeout_ms)
  if (!Number.isFinite(timeout) || timeout < 1000 || timeout > 120000) {
    throw new UpdateProtocolError('Update source timeout is invalid.', 'UPDATE_SOURCE_CONFIG_INVALID')
  }

  return {
    ...config,
    allowed_manifest_hosts: config.allowed_manifest_hosts.map((host) => String(host).toLowerCase()),
    allowed_asset_hosts: config.allowed_asset_hosts.map((host) => String(host).toLowerCase()),
    request_timeout_ms: timeout,
    configured: Boolean(config.repository),
  }
}

function readUpdateSourceConfig(configPath, options = {}) {
  let config
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
  } catch (error) {
    throw new UpdateProtocolError(
      `Unable to read update source configuration: ${error.message}`,
      'UPDATE_SOURCE_CONFIG_READ_FAILED',
    )
  }
  return validateUpdateSourceConfig(config, options)
}

function releaseAssetUrl(config, assetName, tag = '') {
  if (!ASSET_NAME_RE.test(assetName)) {
    throw new UpdateProtocolError('Invalid release asset name.', 'UPDATE_SOURCE_URL_INVALID')
  }
  if (!config.configured) {
    throw new UpdateProtocolError(
      'Public update repository is not configured.',
      'UPDATE_SOURCE_NOT_CONFIGURED',
    )
  }
  const releasePath = tag
    ? `releases/download/${encodeURIComponent(tag)}`
    : 'releases/latest/download'
  return `https://github.com/${config.repository}/${releasePath}/${encodeURIComponent(assetName)}`
}

async function fetchUpdateManifest(config, {
  tag = '',
  fetchImpl = globalThis.fetch,
  channel = 'stable',
} = {}) {
  if (typeof fetchImpl !== 'function') {
    throw new UpdateProtocolError('Fetch implementation is unavailable.', 'UPDATE_NETWORK_ERROR')
  }
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), config.request_timeout_ms)
  let apiError = null
  let response
  const url = releaseAssetUrl(config, config.manifest_asset || `${channel}.json`, tag)
  try {
    if (config.prefer_github_api) {
      try {
        return await fetchManifestViaGithubApi(config, {
          tag,
          fetchImpl,
          signal: controller.signal,
          channel,
        })
      } catch (error) {
        apiError = error
        if (controller.signal.aborted) throw error
      }
    }

    response = await fetchImpl(url, {
      method: 'GET',
      redirect: 'follow',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
      },
      signal: controller.signal,
    })
  } catch (error) {
    if (apiError) throw apiError
    throw new UpdateProtocolError(
      error?.name === 'AbortError' ? 'Update source request timed out.' : 'Unable to reach update source.',
      'UPDATE_NETWORK_ERROR',
      { cause: error?.message || String(error) },
    )
  } finally {
    clearTimeout(timeout)
  }

  if (response.status === 404) {
    if (apiError) throw apiError
    throw new UpdateProtocolError('Update source asset was not found.', 'UPDATE_SOURCE_NOT_FOUND')
  }
  if (!response.ok) {
    throw new UpdateProtocolError(
      `Update source returned HTTP ${response.status}.`,
      'UPDATE_SOURCE_HTTP_ERROR',
      { status: response.status },
    )
  }

  parseUrl(response.url || url, config.allowed_manifest_hosts, 'Manifest response')
  let manifest
  try {
    manifest = await response.json()
  } catch (error) {
    throw new UpdateProtocolError(
      'Update source returned invalid JSON.',
      'UPDATE_MANIFEST_INVALID',
      { cause: error?.message || String(error) },
    )
  }
  return {
    manifest,
    request_url: url,
    response_url: response.url || url,
    channel,
  }
}

function validatePackageUrl(config, value) {
  return parseUrl(value, config.allowed_asset_hosts, 'Update package').toString()
}

module.exports = {
  fetchUpdateManifest,
  readUpdateSourceConfig,
  releaseAssetUrl,
  validatePackageUrl,
  validateUpdateSourceConfig,
}
