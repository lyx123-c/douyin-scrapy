const crypto = require('node:crypto')
const fs = require('node:fs')

const { canonicalJsonBytes } = require('./canonical-json.cjs')

const { UpdateProtocolError } = require('./update-errors.cjs')
const { validateManifestPaths, validateUpdatePath } = require('./path-policy.cjs')
const {
  manifestPayload,
  validateKeyring,
  verifyManifestSignatures,
} = require('./signature.cjs')
const {
  compareSemver,
  parseSemver,
} = require('./version-metadata.cjs')

const SUPPORTED_SCHEMA_VERSION = 1
const SHA256_RE = /^[a-fA-F0-9]{64}$/
const TAG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const COMMIT_RE = /^[a-fA-F0-9]{7,64}$/
const PACKAGE_TYPES = new Set(['full', 'file_delta', 'binary_delta'])
const TOP_LEVEL_KEYS = new Set([
  'schema_version',
  'product_id',
  'channel',
  'version',
  'tag',
  'commit',
  'build_time',
  'release_sequence',
  'published_at',
  'min_supported_version',
  'minimum_updater_version',
  'release_notes',
  'files',
  'changes',
  'packages',
  'signatures',
])

function isPlainObject(value) {
  return Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
}

function manifestInvalid(message, code = 'UPDATE_MANIFEST_INVALID') {
  throw new UpdateProtocolError(message, code)
}

function assertOnlyKeys(value, allowed, label) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) manifestInvalid(`${label} contains unknown field: ${key}`)
  }
}

function assertDate(value, fieldName) {
  if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) {
    manifestInvalid(`${fieldName} must be an ISO date-time.`)
  }
}

function assertSemver(value, fieldName) {
  if (!parseSemver(value)) manifestInvalid(`${fieldName} is not a valid semantic version.`)
}

function assertSha256(value, fieldName) {
  if (typeof value !== 'string' || !SHA256_RE.test(value)) {
    manifestInvalid(`${fieldName} is not a valid SHA256.`)
  }
}

function assertPackageUrl(value, allowedAssetHosts, allowInsecureLocalhost = false) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    manifestInvalid('Package URL is invalid.')
  }
  const localhost = ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)
  if (
    (parsed.protocol !== 'https:' && !(allowInsecureLocalhost && localhost && parsed.protocol === 'http:'))
    || parsed.username
    || parsed.password
  ) {
    manifestInvalid('Package URL must be credential-free HTTPS.')
  }
  if (Array.isArray(allowedAssetHosts) && allowedAssetHosts.length > 0) {
    const allowed = new Set(allowedAssetHosts.map((host) => String(host).toLowerCase()))
    if (!allowed.has(parsed.hostname.toLowerCase())) {
      throw new UpdateProtocolError(
        `Package URL host is not allowed: ${parsed.hostname}`,
        'UPDATE_SOURCE_HOST_DENIED',
      )
    }
  }
}

function validateManifestShape(manifest, {
  allowedAssetHosts = null,
  allowInsecureLocalhost = false,
} = {}) {
  if (!isPlainObject(manifest)) manifestInvalid('Manifest must be an object.')
  assertOnlyKeys(manifest, TOP_LEVEL_KEYS, 'manifest')
  if (manifest.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    throw new UpdateProtocolError(
      `Unsupported update manifest schema: ${manifest.schema_version}`,
      'UPDATE_SCHEMA_UNSUPPORTED',
    )
  }
  for (const key of TOP_LEVEL_KEYS) {
    if (!(key in manifest)) manifestInvalid(`Manifest is missing required field: ${key}`)
  }
  if (typeof manifest.product_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$/.test(manifest.product_id)) {
    manifestInvalid('Invalid product_id.')
  }
  if (typeof manifest.channel !== 'string' || !manifest.channel) manifestInvalid('Invalid channel.')
  assertSemver(manifest.version, 'version')
  if (typeof manifest.tag !== 'string' || !TAG_RE.test(manifest.tag)) manifestInvalid('Invalid tag.')
  if (typeof manifest.commit !== 'string' || !COMMIT_RE.test(manifest.commit)) manifestInvalid('Invalid commit.')
  assertDate(manifest.build_time, 'build_time')
  assertDate(manifest.published_at, 'published_at')
  if (!Number.isSafeInteger(manifest.release_sequence) || manifest.release_sequence < 1) {
    manifestInvalid('release_sequence must be a positive integer.')
  }
  assertSemver(manifest.min_supported_version, 'min_supported_version')
  assertSemver(manifest.minimum_updater_version, 'minimum_updater_version')
  if (typeof manifest.release_notes !== 'string' || manifest.release_notes.length > 100000) {
    manifestInvalid('release_notes is invalid.')
  }

  if (!Array.isArray(manifest.files) || manifest.files.length === 0) manifestInvalid('files must not be empty.')
  const fileKeys = new Set()
  for (const file of manifest.files) {
    if (!isPlainObject(file)) manifestInvalid('File entries must be objects.')
    assertOnlyKeys(file, new Set(['path', 'size', 'sha256', 'executable']), 'file entry')
    validateUpdatePath(file.path)
    if (!Number.isSafeInteger(file.size) || file.size < 0) manifestInvalid('Invalid file size.')
    assertSha256(file.sha256, `files[${file.path}].sha256`)
    if (file.executable !== undefined && typeof file.executable !== 'boolean') {
      manifestInvalid('File executable must be boolean.')
    }
    const key = file.path.toLowerCase()
    if (fileKeys.has(key)) manifestInvalid(`Duplicate file path: ${file.path}`, 'UPDATE_PATH_DUPLICATE')
    fileKeys.add(key)
  }

  if (!isPlainObject(manifest.changes)) manifestInvalid('changes must be an object.')
  assertOnlyKeys(manifest.changes, new Set(['added', 'replaced', 'removed']), 'changes')
  for (const field of ['added', 'replaced', 'removed']) {
    if (!Array.isArray(manifest.changes[field])) manifestInvalid(`changes.${field} must be an array.`)
    for (const item of manifest.changes[field]) validateUpdatePath(item)
  }

  if (!Array.isArray(manifest.packages) || manifest.packages.length === 0) {
    manifestInvalid('packages must not be empty.')
  }
  for (const pkg of manifest.packages) {
    if (!isPlainObject(pkg)) manifestInvalid('Package entries must be objects.')
    assertOnlyKeys(pkg, new Set([
      'package_id',
      'type',
      'base_version',
      'target_version',
      'archive_format',
      'delta_algorithm',
      'url',
      'size',
      'sha256',
    ]), 'package entry')
    if (typeof pkg.package_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$/.test(pkg.package_id)) {
      manifestInvalid('Invalid package_id.')
    }
    if (!PACKAGE_TYPES.has(pkg.type)) manifestInvalid('Invalid package type.')
    if (pkg.base_version !== null) assertSemver(pkg.base_version, 'package.base_version')
    assertSemver(pkg.target_version, 'package.target_version')
    if (pkg.target_version !== manifest.version) {
      manifestInvalid('Package target_version does not match manifest version.')
    }
    if (!Number.isSafeInteger(pkg.size) || pkg.size < 1) manifestInvalid('Invalid package size.')
    assertSha256(pkg.sha256, `package ${pkg.package_id} sha256`)
    assertPackageUrl(pkg.url, allowedAssetHosts, allowInsecureLocalhost)

    if (pkg.type === 'full' && (pkg.base_version !== null || pkg.archive_format !== 'nsis' || pkg.delta_algorithm !== 'none')) {
      manifestInvalid(`Invalid full package definition: ${pkg.package_id}`)
    }
    if (pkg.type === 'file_delta' && (pkg.base_version === null || pkg.archive_format !== 'zip' || pkg.delta_algorithm !== 'none')) {
      manifestInvalid(`Invalid file_delta package definition: ${pkg.package_id}`)
    }
    if (pkg.type === 'binary_delta' && (pkg.base_version === null || pkg.archive_format !== 'zip' || pkg.delta_algorithm !== 'hpatch')) {
      manifestInvalid(`Invalid binary_delta package definition: ${pkg.package_id}`)
    }
  }

  if (!manifest.packages.some((pkg) => pkg.type === 'full')) {
    manifestInvalid('Manifest must retain a full installer package.', 'UPDATE_FULL_PACKAGE_REQUIRED')
  }

  if (!Array.isArray(manifest.signatures) || manifest.signatures.length === 0) {
    manifestInvalid('Manifest signatures are missing.', 'UPDATE_SIGNATURE_INVALID')
  }
  for (const signature of manifest.signatures) {
    if (!isPlainObject(signature)) manifestInvalid('Signature entries must be objects.')
    assertOnlyKeys(signature, new Set(['key_id', 'algorithm', 'signature']), 'signature entry')
    if (signature.algorithm !== 'Ed25519') manifestInvalid('Unsupported signature algorithm.')
  }

  validateManifestPaths(manifest, { allowUnverifiedDeletes: true })
  return manifest
}

function selectCompatiblePackage(manifest, currentVersion, { allowedTypes = null } = {}) {
  const compatible = manifest.packages.filter((pkg) => (
    pkg.type === 'full'
    || (pkg.base_version !== null && compareSemver(pkg.base_version, currentVersion) === 0)
  ))
  const allowed = Array.isArray(allowedTypes) && allowedTypes.length > 0
    ? compatible.filter((pkg) => allowedTypes.includes(pkg.type))
    : compatible
  if (allowed.length === 0) {
    throw new UpdateProtocolError(
      'No update package is compatible with the current version.',
      'UPDATE_BASE_VERSION_INCOMPATIBLE',
    )
  }
  const deltas = allowed
    .filter((pkg) => pkg.type !== 'full')
    .sort((left, right) => left.size - right.size)
  return deltas[0] || allowed.find((pkg) => pkg.type === 'full')
}

function verifyUpdateManifest(manifest, {
  keyring,
  expectedProductId,
  expectedChannel = 'stable',
  currentVersion,
  currentUpdaterVersion,
  lastAcceptedRelease = null,
  currentFiles = null,
  allowedAssetHosts = null,
  allowedPackageTypes = null,
  allowInsecureLocalhost = false,
  now = new Date(),
} = {}) {
  validateKeyring(keyring)
  validateManifestShape(manifest, { allowedAssetHosts, allowInsecureLocalhost })
  verifyManifestSignatures(manifest, keyring, { now })

  if (manifest.product_id !== expectedProductId) {
    throw new UpdateProtocolError(
      `Manifest product_id mismatch: ${manifest.product_id}`,
      'UPDATE_PRODUCT_MISMATCH',
    )
  }
  if (manifest.channel !== expectedChannel) {
    throw new UpdateProtocolError(
      `Manifest channel mismatch: ${manifest.channel}`,
      'UPDATE_CHANNEL_MISMATCH',
    )
  }
  if (!parseSemver(currentVersion)) manifestInvalid('Current version is invalid.')
  if (!parseSemver(currentUpdaterVersion)) manifestInvalid('Current updater version is invalid.')

  if (lastAcceptedRelease) {
    if (!Number.isSafeInteger(lastAcceptedRelease.release_sequence)
      || !parseSemver(lastAcceptedRelease.version)) {
      throw new UpdateProtocolError('Last accepted release metadata is invalid.', 'UPDATE_REPLAY_STATE_INVALID')
    }
    if (
      manifest.release_sequence <= lastAcceptedRelease.release_sequence
      || compareSemver(manifest.version, lastAcceptedRelease.version) <= 0
    ) {
      throw new UpdateProtocolError(
        'A stale or replayed update manifest was rejected.',
        'UPDATE_REPLAY_DETECTED',
      )
    }
  }

  const versionComparison = compareSemver(manifest.version, currentVersion)
  if (versionComparison < 0) {
    throw new UpdateProtocolError(
      'Version downgrades are not allowed.',
      'UPDATE_VERSION_DOWNGRADE',
    )
  }

  if (versionComparison === 0) {
    return {
      status: 'up_to_date',
      current_version: currentVersion,
      latest_version: manifest.version,
      channel: manifest.channel,
      release_notes: null,
      package: null,
    }
  }

  if (compareSemver(currentVersion, manifest.min_supported_version) < 0) {
    throw new UpdateProtocolError(
      `Current version ${currentVersion} is older than the minimum supported version ${manifest.min_supported_version}.`,
      'UPDATE_CURRENT_VERSION_UNSUPPORTED',
    )
  }
  if (compareSemver(currentUpdaterVersion, manifest.minimum_updater_version) < 0) {
    throw new UpdateProtocolError(
      `Updater ${currentUpdaterVersion} is older than required ${manifest.minimum_updater_version}.`,
      'UPDATE_UPDATER_TOO_OLD',
    )
  }

  validateManifestPaths(manifest, { currentFiles })
  const selected = selectCompatiblePackage(manifest, currentVersion, {
    allowedTypes: allowedPackageTypes,
  })
  return {
    status: 'update_available',
    current_version: currentVersion,
    latest_version: manifest.version,
    channel: manifest.channel,
    release_notes: manifest.release_notes,
    release_sequence: manifest.release_sequence,
    published_at: manifest.published_at,
    minimum_updater_version: manifest.minimum_updater_version,
    package: {
      package_id: selected.package_id,
      type: selected.type,
      base_version: selected.base_version,
      target_version: selected.target_version,
      archive_format: selected.archive_format,
      delta_algorithm: selected.delta_algorithm,
      url: selected.url,
      size: selected.size,
      sha256: selected.sha256,
    },
    signed_payload_sha256: crypto
      .createHash('sha256')
      .update(canonicalJsonBytes(manifestPayload(manifest)))
      .digest('hex'),
  }
}

async function verifyArtifactFile(filePath, { expectedSize, expectedSha256 }) {
  if (!Number.isSafeInteger(expectedSize) || expectedSize < 0) {
    throw new UpdateProtocolError('Expected artifact size is invalid.', 'UPDATE_ARTIFACT_SIZE_MISMATCH')
  }
  assertSha256(expectedSha256, 'expectedSha256')
  const stat = fs.statSync(filePath)
  if (stat.size !== expectedSize) {
    throw new UpdateProtocolError(
      `Artifact size mismatch: expected ${expectedSize}, got ${stat.size}.`,
      'UPDATE_ARTIFACT_SIZE_MISMATCH',
    )
  }

  const digest = crypto.createHash('sha256')
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath)
    stream.on('data', (chunk) => digest.update(chunk))
    stream.on('error', reject)
    stream.on('end', resolve)
  })
  const actual = digest.digest('hex')
  if (actual.toLowerCase() !== expectedSha256.toLowerCase()) {
    throw new UpdateProtocolError(
      'Artifact SHA256 mismatch.',
      'UPDATE_ARTIFACT_HASH_MISMATCH',
      { expected: expectedSha256.toLowerCase(), actual },
    )
  }
  return {
    path: filePath,
    size: stat.size,
    sha256: actual,
  }
}

module.exports = {
  SUPPORTED_SCHEMA_VERSION,
  selectCompatiblePackage,
  validateManifestShape,
  verifyArtifactFile,
  verifyUpdateManifest,
}
