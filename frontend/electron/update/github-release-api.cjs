const { UpdateProtocolError } = require('./update-errors.cjs')

const GITHUB_API_ORIGIN = 'https://api.github.com'
const API_HEADERS = Object.freeze({
  Accept: 'application/vnd.github+json',
  'Cache-Control': 'no-cache',
  'User-Agent': 'DouyinWorkbench-Updater',
  'X-GitHub-Api-Version': '2022-11-28',
})

function networkError(error) {
  return new UpdateProtocolError(
    error?.name === 'AbortError' ? 'Update source request timed out.' : 'Unable to reach update source.',
    'UPDATE_NETWORK_ERROR',
    { cause: error?.message || String(error) },
  )
}

function assertAllowedHost(value, hosts, label) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new UpdateProtocolError(`Invalid ${label} URL.`, 'UPDATE_SOURCE_URL_INVALID')
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
    throw new UpdateProtocolError(`${label} URL must be credential-free HTTPS.`, 'UPDATE_SOURCE_URL_INVALID')
  }
  if (!hosts.includes(parsed.hostname.toLowerCase())) {
    throw new UpdateProtocolError(
      `${label} host is not allowed: ${parsed.hostname}`,
      'UPDATE_SOURCE_HOST_DENIED',
    )
  }
}

function downloadNetworkError(error, stage) {
  return new UpdateProtocolError(
    error?.name === 'AbortError'
      ? 'Update package request timed out.'
      : 'Unable to reach update package source.',
    'UPDATE_DOWNLOAD_NETWORK_ERROR',
    {
      stage,
      cause: error?.message || String(error),
    },
  )
}

function parseGithubReleaseDownloadUrl(value) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' || parsed.hostname.toLowerCase() !== 'github.com') {
    return null
  }
  const parts = parsed.pathname.split('/').filter(Boolean)
  if (
    parts.length < 6
    || parts[2] !== 'releases'
    || parts[3] !== 'download'
  ) {
    return null
  }
  return {
    repository: `${parts[0]}/${parts[1]}`,
    tag: decodeURIComponent(parts[4]),
    assetName: decodeURIComponent(parts.slice(5).join('/')),
  }
}

async function resolveGithubReleaseAssetUrl(downloadUrl, {
  fetchImpl,
  signal,
}) {
  const parsed = parseGithubReleaseDownloadUrl(downloadUrl)
  if (!parsed) return downloadUrl

  const releaseUrl = `${GITHUB_API_ORIGIN}/repos/${parsed.repository}/releases/tags/${encodeURIComponent(parsed.tag)}`
  let releaseResponse
  try {
    releaseResponse = await fetchImpl(releaseUrl, {
      method: 'GET',
      redirect: 'follow',
      headers: API_HEADERS,
      signal,
    })
  } catch (error) {
    throw downloadNetworkError(error, 'resolve_release')
  }
  if (releaseResponse.status === 404) {
    throw new UpdateProtocolError(
      `Update release was not found: ${parsed.repository}@${parsed.tag}`,
      'UPDATE_DOWNLOAD_NOT_FOUND',
      { stage: 'resolve_release', status: 404 },
    )
  }
  if (!releaseResponse.ok) {
    throw new UpdateProtocolError(
      `Update release lookup failed with HTTP ${releaseResponse.status}.`,
      'UPDATE_DOWNLOAD_HTTP_ERROR',
      { stage: 'resolve_release', status: releaseResponse.status },
    )
  }

  let release
  try {
    release = await releaseResponse.json()
  } catch (error) {
    throw new UpdateProtocolError(
      'Update release metadata is invalid.',
      'UPDATE_DOWNLOAD_METADATA_INVALID',
      { stage: 'resolve_release', cause: error?.message || String(error) },
    )
  }
  const asset = Array.isArray(release?.assets)
    ? release.assets.find((item) => item?.name === parsed.assetName)
    : null
  if (!asset?.url) {
    throw new UpdateProtocolError(
      `Update package asset was not found: ${parsed.assetName}`,
      'UPDATE_DOWNLOAD_NOT_FOUND',
      { stage: 'resolve_asset', status: 404 },
    )
  }
  return asset.url
}

async function fetchManifestViaGithubApi(config, {
  tag = '',
  fetchImpl,
  signal,
  channel,
}) {
  const assetName = config.manifest_asset || `${channel}.json`
  const releasePath = tag
    ? `tags/${encodeURIComponent(tag)}`
    : 'latest'
  const releaseUrl = `${GITHUB_API_ORIGIN}/repos/${config.repository}/releases/${releasePath}`

  let releaseResponse
  try {
    releaseResponse = await fetchImpl(releaseUrl, {
      method: 'GET',
      redirect: 'follow',
      headers: API_HEADERS,
      signal,
    })
  } catch (error) {
    throw networkError(error)
  }
  if (releaseResponse.status === 404) {
    throw new UpdateProtocolError(
      `Update source release was not found: ${config.repository}@${tag || 'latest'}`,
      'UPDATE_SOURCE_NOT_FOUND',
      { status: 404, repository: config.repository, tag: tag || null },
    )
  }
  if (!releaseResponse.ok) {
    throw new UpdateProtocolError(
      `Update source returned HTTP ${releaseResponse.status}.`,
      'UPDATE_SOURCE_HTTP_ERROR',
      { status: releaseResponse.status },
    )
  }

  let release
  try {
    release = await releaseResponse.json()
  } catch (error) {
    throw new UpdateProtocolError('Update source returned invalid release metadata.', 'UPDATE_MANIFEST_INVALID', {
      cause: error?.message || String(error),
    })
  }
  const asset = Array.isArray(release?.assets)
    ? release.assets.find((item) => item?.name === assetName)
    : null
  if (!asset?.url) {
    throw new UpdateProtocolError(`Release asset was not found: ${assetName}`, 'UPDATE_SOURCE_NOT_FOUND')
  }

  let manifestResponse
  try {
    manifestResponse = await fetchImpl(asset.url, {
      method: 'GET',
      redirect: 'follow',
      headers: {
        Accept: 'application/octet-stream',
        'Cache-Control': 'no-cache',
        'User-Agent': 'DouyinWorkbench-Updater',
      },
      signal,
    })
  } catch (error) {
    throw networkError(error)
  }
  if (manifestResponse.status === 404) {
    throw new UpdateProtocolError('Update source asset was not found.', 'UPDATE_SOURCE_NOT_FOUND')
  }
  if (!manifestResponse.ok) {
    throw new UpdateProtocolError(
      `Update source returned HTTP ${manifestResponse.status}.`,
      'UPDATE_SOURCE_HTTP_ERROR',
      { status: manifestResponse.status },
    )
  }

  assertAllowedHost(
    manifestResponse.url || asset.url,
    config.allowed_manifest_hosts,
    'Manifest response',
  )
  let manifest
  try {
    manifest = await manifestResponse.json()
  } catch (error) {
    throw new UpdateProtocolError('Update source returned invalid JSON.', 'UPDATE_MANIFEST_INVALID', {
      cause: error?.message || String(error),
    })
  }
  return {
    manifest,
    request_url: releaseUrl,
    response_url: manifestResponse.url || asset.url,
    channel,
  }
}

module.exports = {
  fetchManifestViaGithubApi,
  parseGithubReleaseDownloadUrl,
  resolveGithubReleaseAssetUrl,
}
