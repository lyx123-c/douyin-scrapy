const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const { verifyArtifactFile } = require('./manifest-verifier.cjs')
const { UpdateProtocolError } = require('./update-errors.cjs')

const fsp = fs.promises
const SAFE_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.exe$/

function validateDownloadUrl(value, {
  allowedHosts,
  allowInsecureLocalhost = false,
}) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new UpdateProtocolError('Download URL is invalid.', 'UPDATE_DOWNLOAD_URL_INVALID')
  }
  const localhost = ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)
  if (parsed.protocol !== 'https:' && !(allowInsecureLocalhost && localhost && parsed.protocol === 'http:')) {
    throw new UpdateProtocolError('Download URL must use HTTPS.', 'UPDATE_DOWNLOAD_URL_INVALID')
  }
  if (parsed.username || parsed.password) {
    throw new UpdateProtocolError('Download URL must not contain credentials.', 'UPDATE_DOWNLOAD_URL_INVALID')
  }
  if (!allowInsecureLocalhost || !localhost) {
    const allowed = new Set((allowedHosts || []).map((host) => String(host).toLowerCase()))
    if (!allowed.has(parsed.hostname.toLowerCase())) {
      throw new UpdateProtocolError(
        `Download host is not allowed: ${parsed.hostname}`,
        'UPDATE_DOWNLOAD_HOST_DENIED',
      )
    }
  }
  return parsed
}

async function downloadUpdatePackage({
  url,
  expectedSize,
  expectedSha256,
  tempRoot,
  filename,
  allowedHosts,
  allowInsecureLocalhost = false,
  fetchImpl = globalThis.fetch,
  signal = null,
  onProgress = null,
}) {
  if (typeof fetchImpl !== 'function') {
    throw new UpdateProtocolError('Fetch implementation is unavailable.', 'UPDATE_DOWNLOAD_NETWORK_ERROR')
  }
  if (!Number.isSafeInteger(expectedSize) || expectedSize < 1) {
    throw new UpdateProtocolError('Expected package size is invalid.', 'UPDATE_DOWNLOAD_SIZE_INVALID')
  }
  if (typeof expectedSha256 !== 'string' || !/^[a-fA-F0-9]{64}$/.test(expectedSha256)) {
    throw new UpdateProtocolError('Expected package SHA256 is invalid.', 'UPDATE_DOWNLOAD_HASH_INVALID')
  }
  if (!SAFE_FILENAME_RE.test(filename)) {
    throw new UpdateProtocolError('Download filename is invalid.', 'UPDATE_DOWNLOAD_FILENAME_INVALID')
  }

  validateDownloadUrl(url, { allowedHosts, allowInsecureLocalhost })
  const root = path.resolve(tempRoot)
  const rootExisted = fs.existsSync(root)
  await fsp.mkdir(root, { recursive: true })
  const finalPath = path.join(root, filename)
  const partPath = `${finalPath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.part`
  let fileHandle = null
  let downloaded = 0
  let response = null
  const startedAt = Date.now()
  let lastProgressAt = 0

  function reportProgress(force = false) {
    if (typeof onProgress !== 'function') return
    const now = Date.now()
    if (!force && now - lastProgressAt < 200) return
    lastProgressAt = now
    const elapsed = Math.max(0.001, (now - startedAt) / 1000)
    onProgress({
      downloadedBytes: downloaded,
      totalBytes: expectedSize,
      percent: Math.min(100, Number(((downloaded / expectedSize) * 100).toFixed(2))),
      bytesPerSecond: Math.round(downloaded / elapsed),
    })
  }

  try {
    reportProgress(true)
    try {
      response = await fetchImpl(url, {
        method: 'GET',
        redirect: 'follow',
        headers: { Accept: 'application/octet-stream' },
        signal,
      })
    } catch (error) {
      if (signal?.aborted || error?.name === 'AbortError') {
        throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
      }
      throw new UpdateProtocolError(
        'Unable to download update package.',
        'UPDATE_DOWNLOAD_NETWORK_ERROR',
        { cause: error?.message || String(error) },
      )
    }

    if (!response.ok) {
      throw new UpdateProtocolError(
        `Update package download failed with HTTP ${response.status}.`,
        'UPDATE_DOWNLOAD_HTTP_ERROR',
        { status: response.status },
      )
    }
    validateDownloadUrl(response.url || url, { allowedHosts, allowInsecureLocalhost })
    if (!response.body?.getReader) {
      throw new UpdateProtocolError('Download response has no readable body.', 'UPDATE_DOWNLOAD_NETWORK_ERROR')
    }

    fileHandle = await fsp.open(partPath, 'wx')
    const reader = response.body.getReader()
    while (true) {
      if (signal?.aborted) {
        throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
      }
      const { done, value } = await reader.read()
      if (done) break
      const chunk = Buffer.from(value)
      downloaded += chunk.length
      if (downloaded > expectedSize) {
        throw new UpdateProtocolError('Download exceeds the signed package size.', 'UPDATE_DOWNLOAD_SIZE_MISMATCH')
      }
      await fileHandle.write(chunk)
      reportProgress()
    }

    await fileHandle.sync()
    await fileHandle.close()
    fileHandle = null
    reportProgress(true)

    if (downloaded !== expectedSize) {
      throw new UpdateProtocolError(
        `Downloaded size mismatch: expected ${expectedSize}, got ${downloaded}.`,
        'UPDATE_DOWNLOAD_SIZE_MISMATCH',
      )
    }

    await verifyArtifactFile(partPath, { expectedSize, expectedSha256 })
    await fsp.rm(finalPath, { force: true })
    await fsp.rename(partPath, finalPath)
    return {
      path: finalPath,
      size: expectedSize,
      sha256: expectedSha256.toLowerCase(),
    }
  } catch (error) {
    if (fileHandle) await fileHandle.close().catch(() => {})
    await fsp.rm(partPath, { force: true }).catch(() => {})
    if (!rootExisted) await fsp.rmdir(root).catch(() => {})
    if (signal?.aborted && error?.code !== 'UPDATE_DOWNLOAD_CANCELLED') {
      throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
    }
    throw error
  }
}

module.exports = {
  downloadUpdatePackage,
  validateDownloadUrl,
}
