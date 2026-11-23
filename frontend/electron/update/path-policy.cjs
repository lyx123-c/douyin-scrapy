const { UpdateProtocolError } = require('./update-errors.cjs')

const ALLOWED_SCOPES = Object.freeze(['program', 'updater'])
const MAX_RELATIVE_PATH_LENGTH = 512
const MAX_SEGMENT_LENGTH = 200
const WINDOWS_RESERVED_NAME = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i

function invalidPath(value, reason) {
  throw new UpdateProtocolError(
    `Unsafe update path${value ? ` (${value})` : ''}: ${reason}`,
    'UPDATE_PATH_INVALID',
    { path: value, reason },
  )
}

function validateUpdatePath(value) {
  if (typeof value !== 'string' || !value) invalidPath(value, 'path must be a non-empty string')
  if (value.length > MAX_RELATIVE_PATH_LENGTH) invalidPath(value, 'path is too long')
  if (value.includes('\\')) invalidPath(value, 'backslashes are not allowed')
  if (value.startsWith('/') || value.endsWith('/')) invalidPath(value, 'absolute or directory path')
  if (/[\u0000-\u001f\u007f]/.test(value)) invalidPath(value, 'control characters are not allowed')

  const segments = value.split('/')
  if (segments.length < 2) invalidPath(value, 'path must include an allowed scope')
  if (!ALLOWED_SCOPES.includes(segments[0])) {
    invalidPath(value, `scope must be one of: ${ALLOWED_SCOPES.join(', ')}`)
  }

  for (const segment of segments) {
    if (!segment || segment === '.' || segment === '..') {
      invalidPath(value, 'empty and traversal segments are not allowed')
    }
    if (segment.length > MAX_SEGMENT_LENGTH) invalidPath(value, 'path segment is too long')
    if (segment.endsWith('.') || segment.endsWith(' ')) {
      invalidPath(value, 'Windows-normalized trailing dots or spaces are not allowed')
    }
    if (segment.includes(':')) invalidPath(value, 'Windows drive or alternate data stream syntax is not allowed')
    if (WINDOWS_RESERVED_NAME.test(segment)) {
      invalidPath(value, 'Windows reserved device names are not allowed')
    }
  }

  return value
}

function normalizedKey(value) {
  return validateUpdatePath(value).toLowerCase()
}

function validatePathList(values, fieldName) {
  if (!Array.isArray(values)) {
    throw new UpdateProtocolError(`${fieldName} must be an array.`, 'UPDATE_MANIFEST_INVALID')
  }
  const seen = new Set()
  return values.map((value) => {
    const validated = validateUpdatePath(value)
    const key = validated.toLowerCase()
    if (seen.has(key)) {
      throw new UpdateProtocolError(
        `${fieldName} contains duplicate path: ${value}`,
        'UPDATE_PATH_DUPLICATE',
      )
    }
    seen.add(key)
    return validated
  })
}

function validateFileInventory(files) {
  if (!Array.isArray(files) || files.length === 0) {
    throw new UpdateProtocolError('Manifest files must be a non-empty array.', 'UPDATE_MANIFEST_INVALID')
  }

  const seen = new Set()
  for (const file of files) {
    if (!file || typeof file !== 'object' || Array.isArray(file)) {
      throw new UpdateProtocolError('Manifest file entries must be objects.', 'UPDATE_MANIFEST_INVALID')
    }
    const path = validateUpdatePath(file.path)
    const key = path.toLowerCase()
    if (seen.has(key)) {
      throw new UpdateProtocolError(
        `Manifest file inventory contains duplicate path: ${path}`,
        'UPDATE_PATH_DUPLICATE',
      )
    }
    seen.add(key)
  }
  return seen
}

function validateManifestPaths(manifest, {
  currentFiles = null,
  allowUnverifiedDeletes = false,
} = {}) {
  const inventory = validateFileInventory(manifest.files)
  const changes = manifest.changes
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) {
    throw new UpdateProtocolError('Manifest changes must be an object.', 'UPDATE_MANIFEST_INVALID')
  }

  const added = validatePathList(changes.added, 'changes.added')
  const replaced = validatePathList(changes.replaced, 'changes.replaced')
  const removed = validatePathList(changes.removed, 'changes.removed')
  const current = currentFiles === null ? null : new Set(validatePathList(currentFiles, 'currentFiles').map((item) => item.toLowerCase()))

  for (const path of [...added, ...replaced]) {
    if (!inventory.has(path.toLowerCase())) {
      throw new UpdateProtocolError(
        `Changed file is missing from target inventory: ${path}`,
        'UPDATE_MANIFEST_INVALID',
      )
    }
  }

  for (const path of removed) {
    if (inventory.has(path.toLowerCase())) {
      throw new UpdateProtocolError(
        `Removed file is still present in target inventory: ${path}`,
        'UPDATE_MANIFEST_INVALID',
      )
    }
    if (current === null) {
      if (allowUnverifiedDeletes) continue
      throw new UpdateProtocolError(
        `Deletion requires the installed file inventory: ${path}`,
        'UPDATE_CURRENT_INVENTORY_REQUIRED',
      )
    }
    if (!current.has(path.toLowerCase())) {
      throw new UpdateProtocolError(
        `Refusing to delete a file not owned by the installed version: ${path}`,
        'UPDATE_DELETE_NOT_OWNED',
      )
    }
  }

  if (current !== null) {
    for (const path of added) {
      if (current.has(path.toLowerCase())) {
        throw new UpdateProtocolError(
          `Added path already exists in the installed version: ${path}`,
          'UPDATE_ADDED_PATH_ALREADY_EXISTS',
        )
      }
    }
    for (const path of replaced) {
      if (!current.has(path.toLowerCase())) {
        throw new UpdateProtocolError(
          `Replaced path is absent from the installed version: ${path}`,
          'UPDATE_REPLACED_PATH_MISSING',
        )
      }
    }
  }

  return {
    files: [...inventory],
    added,
    replaced,
    removed,
  }
}

module.exports = {
  ALLOWED_SCOPES,
  normalizedKey,
  validateFileInventory,
  validateManifestPaths,
  validateUpdatePath,
}
