const fs = require('node:fs')
const path = require('node:path')

const { UpdateProtocolError } = require('./update-errors.cjs')

const SEMVER_RE = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
const COMMIT_RE = /^[a-fA-F0-9]{7,64}$/

function isPlainObject(value) {
  return Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
}

function parseSemver(value) {
  if (typeof value !== 'string') return null
  const match = SEMVER_RE.exec(value)
  if (!match) return null
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ? match[4].split('.') : [],
  }
}

function compareSemver(left, right) {
  const a = typeof left === 'string' ? parseSemver(left) : left
  const b = typeof right === 'string' ? parseSemver(right) : right
  if (!a || !b) {
    throw new UpdateProtocolError('Invalid semantic version comparison.', 'UPDATE_VERSION_INVALID')
  }
  for (const field of ['major', 'minor', 'patch']) {
    if (a[field] !== b[field]) return a[field] < b[field] ? -1 : 1
  }
  if (a.prerelease.length === 0 && b.prerelease.length === 0) return 0
  if (a.prerelease.length === 0) return 1
  if (b.prerelease.length === 0) return -1

  const length = Math.max(a.prerelease.length, b.prerelease.length)
  for (let index = 0; index < length; index += 1) {
    const av = a.prerelease[index]
    const bv = b.prerelease[index]
    if (av === undefined) return -1
    if (bv === undefined) return 1
    if (av === bv) continue
    const aNumber = /^\d+$/.test(av)
    const bNumber = /^\d+$/.test(bv)
    if (aNumber && bNumber) return Number(av) < Number(bv) ? -1 : 1
    if (aNumber) return -1
    if (bNumber) return 1
    return av < bv ? -1 : 1
  }
  return 0
}

function validateVersionMetadata(metadata, { requireReleaseIdentity = false } = {}) {
  if (!isPlainObject(metadata) || metadata.schema_version !== 1) {
    throw new UpdateProtocolError('Unsupported version metadata.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (typeof metadata.product_id !== 'string' || !metadata.product_id) {
    throw new UpdateProtocolError('Version metadata has no product_id.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (!parseSemver(metadata.version)) {
    throw new UpdateProtocolError('Version metadata has invalid version.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (!['stable', 'beta'].includes(metadata.channel)) {
    throw new UpdateProtocolError('Version metadata has invalid channel.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (!parseSemver(metadata.updater_version)) {
    throw new UpdateProtocolError('Version metadata has invalid updater_version.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (typeof metadata.commit !== 'string' || !COMMIT_RE.test(metadata.commit)) {
    throw new UpdateProtocolError('Version metadata has invalid commit.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (typeof metadata.build_time !== 'string' || Number.isNaN(Date.parse(metadata.build_time))) {
    throw new UpdateProtocolError('Version metadata has invalid build_time.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (typeof metadata.build_dirty !== 'boolean') {
    throw new UpdateProtocolError('Version metadata has invalid build_dirty.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (typeof metadata.tag !== 'string') {
    throw new UpdateProtocolError('Version metadata has invalid tag.', 'UPDATE_VERSION_METADATA_INVALID')
  }
  if (requireReleaseIdentity && (!metadata.tag || metadata.build_dirty)) {
    throw new UpdateProtocolError(
      'Release builds require an exact clean Git tag.',
      'UPDATE_RELEASE_IDENTITY_INVALID',
    )
  }
  return metadata
}

function readVersionMetadata(metadataPath, options = {}) {
  let parsed
  try {
    parsed = JSON.parse(fs.readFileSync(metadataPath, 'utf8'))
  } catch (error) {
    throw new UpdateProtocolError(
      `Unable to read version metadata: ${error.message}`,
      'UPDATE_VERSION_METADATA_READ_FAILED',
    )
  }
  return validateVersionMetadata(parsed, options)
}

function createVersionInfo(metadata, { updateSource = null } = {}) {
  validateVersionMetadata(metadata)
  return {
    product_id: metadata.product_id,
    current_version: metadata.version,
    latest_version: null,
    channel: metadata.channel,
    tag: metadata.tag,
    commit: metadata.commit,
    build_time: metadata.build_time,
    updater_version: metadata.updater_version,
    update_status: 'idle',
    release_notes: null,
    package_size: null,
    update_method: null,
    minimum_updater_version: null,
    update_source: updateSource
      ? {
          provider: updateSource.provider,
          configured: updateSource.configured,
          repository: updateSource.repository || null,
        }
      : null,
  }
}

function versionMetadataPath({ appIsPackaged, resourcesPath, projectRoot }) {
  return appIsPackaged
    ? path.join(resourcesPath, 'version-metadata.json')
    : path.join(projectRoot, 'build', 'version-metadata.json')
}

module.exports = {
  COMMIT_RE,
  SEMVER_RE,
  compareSemver,
  createVersionInfo,
  parseSemver,
  readVersionMetadata,
  validateVersionMetadata,
  versionMetadataPath,
}
