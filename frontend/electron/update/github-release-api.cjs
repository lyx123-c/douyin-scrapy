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
    throw new UpdateProtocolError('Update source release was not found.', 'UPDATE_SOURCE_NOT_FOUND')
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
}
