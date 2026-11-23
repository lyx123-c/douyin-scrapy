class CanonicalJsonError extends Error {
  constructor(message, code = 'CANONICAL_JSON_INVALID') {
    super(message)
    this.name = 'CanonicalJsonError'
    this.code = code
  }
}

function canonicalize(value, seen = new WeakSet()) {
  if (value === null) return 'null'

  const valueType = typeof value
  if (valueType === 'string' || valueType === 'boolean') {
    return JSON.stringify(value)
  }
  if (valueType === 'number') {
    if (!Number.isFinite(value)) {
      throw new CanonicalJsonError('Canonical JSON does not allow non-finite numbers.')
    }
    return JSON.stringify(value)
  }
  if (valueType !== 'object') {
    throw new CanonicalJsonError(`Canonical JSON does not allow ${valueType} values.`)
  }
  if (seen.has(value)) {
    throw new CanonicalJsonError('Canonical JSON does not allow circular references.')
  }

  seen.add(value)
  try {
    if (Array.isArray(value)) {
      return `[${value.map((item) => canonicalize(item, seen)).join(',')}]`
    }

    const prototype = Object.getPrototypeOf(value)
    if (prototype !== Object.prototype && prototype !== null) {
      throw new CanonicalJsonError('Canonical JSON only accepts plain objects.')
    }

    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== 'string') {
        throw new CanonicalJsonError('Canonical JSON does not allow symbol keys.')
      }
    }

    const entries = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key], seen)}`)
    return `{${entries.join(',')}}`
  } finally {
    seen.delete(value)
  }
}

function canonicalJsonBytes(value) {
  return Buffer.from(canonicalize(value), 'utf8')
}

module.exports = {
  CanonicalJsonError,
  canonicalJsonBytes,
  canonicalize,
}
