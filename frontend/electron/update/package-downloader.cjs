const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const { resolveGithubReleaseAssetUrl } = require('./github-release-api.cjs')
const { verifyArtifactFile } = require('./manifest-verifier.cjs')
const { UpdateProtocolError } = require('./update-errors.cjs')

const fsp = fs.promises
const SAFE_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.exe$/
const RETRYABLE_DOWNLOAD_CODES = new Set([
  'UPDATE_DOWNLOAD_NETWORK_ERROR',
  'UPDATE_DOWNLOAD_IO_ERROR',
])

function safeHostname(value) {
  try {
    return new URL(value).hostname
  } catch {
    return null
  }
}

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

function throwIfCancelled(signal) {
  if (signal?.aborted) {
    throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
  }
}

async function waitForRetry(signal, delayMs) {
  throwIfCancelled(signal)
  await new Promise((resolve) => setTimeout(resolve, delayMs))
  throwIfCancelled(signal)
}

async function fetchWithTimeout(fetchImpl, url, {
  headers,
  signal,
  timeoutMs,
}) {
  const requestController = new AbortController()
  const abortRequest = () => requestController.abort()
  if (signal?.aborted) {
    requestController.abort()
  } else {
    signal?.addEventListener('abort', abortRequest, { once: true })
  }
  const timeout = setTimeout(() => requestController.abort(), timeoutMs)
  try {
    return await fetchImpl(url, {
      method: 'GET',
      redirect: 'follow',
      headers,
      signal: requestController.signal,
    })
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abortRequest)
  }
}

async function readChunkWithTimeout(reader, timeoutMs) {
  let timeout = null
  try {
    return await Promise.race([
      reader.read(),
      new Promise((_, reject) => {
        timeout = setTimeout(() => {
          reject(new Error('download_stream_timeout'))
        }, timeoutMs)
      }),
    ])
  } finally {
    if (timeout) clearTimeout(timeout)
  }
}

function isRetryableDownloadError(error) {
  if (!(error instanceof UpdateProtocolError)) return true
  if (RETRYABLE_DOWNLOAD_CODES.has(error.code)) return true
  return (
    error.code === 'UPDATE_DOWNLOAD_HTTP_ERROR'
    && (
      error.details?.status === 429
      || Number(error.details?.status) >= 500
    )
  )
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
  preferGithubApi = false,
  requestTimeoutMs = 60000,
  maxAttempts = 4,
  retryDelayMs = 1000,
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
  if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1000 || requestTimeoutMs > 300000) {
    throw new UpdateProtocolError('Download request timeout is invalid.', 'UPDATE_DOWNLOAD_TIMEOUT_INVALID')
  }
  if (!Number.isSafeInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 10) {
    throw new UpdateProtocolError('Download retry count is invalid.', 'UPDATE_DOWNLOAD_RETRY_INVALID')
  }

  let downloadUrl = url
  let currentStage = 'resolve_asset'
  if (preferGithubApi) {
    downloadUrl = await resolveGithubReleaseAssetUrl(url, {
      fetchImpl,
      signal,
    })
  }
  validateDownloadUrl(downloadUrl, { allowedHosts, allowInsecureLocalhost })
  const root = path.resolve(tempRoot)
  const rootExisted = fs.existsSync(root)
  await fsp.mkdir(root, { recursive: true })
  const finalPath = path.join(root, filename)
  const partPath = `${finalPath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.part`
  let fileHandle = null
  let downloaded = 0
  const startedAt = Date.now()
  let lastProgressAt = 0
  currentStage = 'request_headers'

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
    let lastError = null
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      throwIfCancelled(signal)
      currentStage = 'request_headers'
      const headers = { Accept: 'application/octet-stream' }
      if (downloaded > 0) {
        headers.Range = `bytes=${downloaded}-`
      }

      let response
      try {
        response = await fetchWithTimeout(fetchImpl, downloadUrl, {
          headers,
          signal,
          timeoutMs: requestTimeoutMs,
        })
      } catch (error) {
        if (signal?.aborted) {
          throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
        }
        lastError = new UpdateProtocolError(
          error?.name === 'AbortError'
            ? 'Update package request timed out waiting for response.'
            : `Unable to connect to update package source: ${error?.message || String(error)}`,
          'UPDATE_DOWNLOAD_NETWORK_ERROR',
          {
            stage: currentStage,
            attempt,
            cause: error?.name === 'AbortError' ? 'request_timeout' : (error?.message || String(error)),
            host: safeHostname(downloadUrl),
          },
        )
        if (attempt >= maxAttempts) throw lastError
        await waitForRetry(signal, retryDelayMs * attempt)
        continue
      }

      if (response.status === 416 && downloaded === expectedSize) {
        break
      }
      if (!response.ok) {
        throw new UpdateProtocolError(
          `Update package download failed with HTTP ${response.status}.`,
          'UPDATE_DOWNLOAD_HTTP_ERROR',
          {
            stage: 'download_response',
            status: response.status,
            attempt,
            host: safeHostname(response.url || downloadUrl),
          },
        )
      }
      validateDownloadUrl(response.url || downloadUrl, { allowedHosts, allowInsecureLocalhost })
      const contentType = String(response.headers?.get?.('content-type') || '').toLowerCase()
      if (
        contentType.includes('text/html')
        || contentType.includes('application/json')
        || contentType.startsWith('text/plain')
      ) {
        throw new UpdateProtocolError(
          'Update package response is not an executable binary.',
          'UPDATE_DOWNLOAD_INVALID_CONTENT_TYPE',
          {
            stage: 'download_response',
            content_type: contentType,
            host: safeHostname(response.url || downloadUrl),
          },
        )
      }

      if (downloaded > 0 && response.status === 200) {
        await fileHandle?.close().catch(() => {})
        fileHandle = null
        downloaded = 0
        await fsp.rm(partPath, { force: true })
      }

      const declaredLength = Number(response.headers?.get?.('content-length'))
      const expectedLength = expectedSize - downloaded
      if (
        Number.isFinite(declaredLength)
        && declaredLength > 0
        && declaredLength !== expectedLength
      ) {
        throw new UpdateProtocolError(
          `Update package Content-Length mismatch: expected ${expectedLength}, got ${declaredLength}.`,
          'UPDATE_DOWNLOAD_SIZE_MISMATCH',
          { stage: 'download_response', expected: expectedLength, actual: declaredLength },
        )
      }
      if (!response.body?.getReader) {
        throw new UpdateProtocolError('Download response has no readable body.', 'UPDATE_DOWNLOAD_NETWORK_ERROR')
      }

      currentStage = 'write_temp_file'
      fileHandle = await fsp.open(partPath, downloaded > 0 ? 'a' : 'w')
      const reader = response.body.getReader()
      try {
        while (true) {
          throwIfCancelled(signal)
          const { done, value } = await readChunkWithTimeout(reader, requestTimeoutMs)
          if (done) break
          const chunk = Buffer.from(value)
          downloaded += chunk.length
          if (downloaded > expectedSize) {
            throw new UpdateProtocolError('Download exceeds the signed package size.', 'UPDATE_DOWNLOAD_SIZE_MISMATCH')
          }
          await fileHandle.write(chunk)
          reportProgress()
        }
      } catch (error) {
        await reader.cancel().catch(() => {})
        if (signal?.aborted) {
          throw new UpdateProtocolError('Download was cancelled.', 'UPDATE_DOWNLOAD_CANCELLED')
        }
        if (error instanceof UpdateProtocolError) throw error
        lastError = new UpdateProtocolError(
          `Update package stream interrupted at ${downloaded} bytes: ${error?.message || String(error)}`,
          'UPDATE_DOWNLOAD_NETWORK_ERROR',
          {
            stage: 'download_body',
            attempt,
            downloaded_bytes: downloaded,
            cause: error?.message || String(error),
          },
        )
        await fileHandle.close().catch(() => {})
        fileHandle = null
        if (attempt >= maxAttempts) throw lastError
        await waitForRetry(signal, retryDelayMs * attempt)
        continue
      }

      await fileHandle.sync()
      await fileHandle.close()
      fileHandle = null
      if (downloaded < expectedSize) {
        lastError = new UpdateProtocolError(
          `Downloaded size mismatch: expected ${expectedSize}, got ${downloaded}.`,
          'UPDATE_DOWNLOAD_SIZE_MISMATCH',
          { stage: 'download_body', attempt, downloaded_bytes: downloaded },
        )
        if (attempt >= maxAttempts) throw lastError
        await waitForRetry(signal, retryDelayMs * attempt)
        continue
      }
      break
    }

    reportProgress(true)

    currentStage = 'verify_size'
    if (downloaded !== expectedSize) {
      throw new UpdateProtocolError(
        `Downloaded size mismatch: expected ${expectedSize}, got ${downloaded}.`,
        'UPDATE_DOWNLOAD_SIZE_MISMATCH',
      )
    }

    currentStage = 'verify_sha256'
    try {
      await verifyArtifactFile(partPath, { expectedSize, expectedSha256 })
    } catch (error) {
      if (error instanceof UpdateProtocolError) {
        error.details = { ...(error.details || {}), stage: currentStage }
      }
      throw error
    }
    currentStage = 'finalize_file'
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
    if (error instanceof UpdateProtocolError) {
      if (!error.details?.stage) {
        error.details = { ...(error.details || {}), stage: currentStage }
      }
      throw error
    }
    const wrapped = new UpdateProtocolError(
      `Update package download failed during ${currentStage}: ${error?.message || String(error)}`,
      'UPDATE_DOWNLOAD_IO_ERROR',
      { stage: currentStage, cause: error?.message || String(error) },
    )
    if (isRetryableDownloadError(wrapped) && maxAttempts > 1) {
      throw wrapped
    }
    throw wrapped
  }
}

module.exports = {
  downloadUpdatePackage,
  validateDownloadUrl,
}
