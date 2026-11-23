const crypto = require('node:crypto')

const { canonicalJsonBytes } = require('./canonical-json.cjs')
const { UpdateProtocolError } = require('./update-errors.cjs')

const KEY_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$/
const KEY_STATUSES = new Set(['active', 'retired', 'revoked'])

function isPlainObject(value) {
  return Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
}

function parseDate(value, fieldName) {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) {
    throw new UpdateProtocolError(`${fieldName} must be an ISO date-time.`, 'UPDATE_KEYRING_INVALID')
  }
  return new Date(value)
}

function publicKeyObject(encoded) {
  try {
    const key = crypto.createPublicKey({
      key: Buffer.from(encoded, 'base64'),
      format: 'der',
      type: 'spki',
    })
    if (key.asymmetricKeyType !== 'ed25519') throw new Error('key type is not Ed25519')
    return key
  } catch (error) {
    throw new UpdateProtocolError(
      `Invalid Ed25519 public key: ${error.message}`,
      'UPDATE_KEYRING_INVALID',
    )
  }
}

function validateKeyring(keyring) {
  if (!isPlainObject(keyring) || keyring.schema_version !== 1 || !Array.isArray(keyring.keys)) {
    throw new UpdateProtocolError('Unsupported public keyring.', 'UPDATE_KEYRING_INVALID')
  }
  for (const field of Object.keys(keyring)) {
    if (!['schema_version', 'keys', 'updated_at'].includes(field)) {
      throw new UpdateProtocolError(`Unknown keyring field: ${field}`, 'UPDATE_KEYRING_INVALID')
    }
  }

  const ids = new Set()
  return keyring.keys.map((entry) => {
    if (!isPlainObject(entry)) {
      throw new UpdateProtocolError('Keyring entries must be objects.', 'UPDATE_KEYRING_INVALID')
    }
    for (const field of Object.keys(entry)) {
      if (!['key_id', 'algorithm', 'public_key', 'status', 'not_before', 'not_after'].includes(field)) {
        throw new UpdateProtocolError(`Unknown keyring key field: ${field}`, 'UPDATE_KEYRING_INVALID')
      }
    }
    if (typeof entry.key_id !== 'string' || !KEY_ID_RE.test(entry.key_id)) {
      throw new UpdateProtocolError('Invalid key_id in keyring.', 'UPDATE_KEYRING_INVALID')
    }
    if (ids.has(entry.key_id)) {
      throw new UpdateProtocolError(`Duplicate key_id: ${entry.key_id}`, 'UPDATE_KEYRING_INVALID')
    }
    ids.add(entry.key_id)
    if (entry.algorithm !== 'Ed25519') {
      throw new UpdateProtocolError(`Unsupported algorithm for ${entry.key_id}.`, 'UPDATE_KEYRING_INVALID')
    }
    if (!KEY_STATUSES.has(entry.status)) {
      throw new UpdateProtocolError(`Invalid status for key ${entry.key_id}.`, 'UPDATE_KEYRING_INVALID')
    }
    if (typeof entry.public_key !== 'string' || !entry.public_key) {
      throw new UpdateProtocolError(`Missing public key for ${entry.key_id}.`, 'UPDATE_KEYRING_INVALID')
    }

    return {
      ...entry,
      _publicKeyObject: publicKeyObject(entry.public_key),
      _notBefore: parseDate(entry.not_before, 'not_before'),
      _notAfter: parseDate(entry.not_after, 'not_after'),
    }
  })
}

function manifestPayload(manifest) {
  if (!isPlainObject(manifest)) {
    throw new UpdateProtocolError('Manifest must be an object.', 'UPDATE_MANIFEST_INVALID')
  }
  const payload = {}
  for (const [key, value] of Object.entries(manifest)) {
    if (key !== 'signatures') payload[key] = value
  }
  return payload
}

function signManifest(manifest, signers) {
  if (!Array.isArray(signers) || signers.length === 0) {
    throw new UpdateProtocolError('At least one signing key is required.', 'UPDATE_SIGNING_KEY_REQUIRED')
  }
  const payload = canonicalJsonBytes(manifestPayload(manifest))
  const signatures = signers.map(({ keyId, privateKey }) => {
    if (typeof keyId !== 'string' || !KEY_ID_RE.test(keyId)) {
      throw new UpdateProtocolError('Invalid signing key_id.', 'UPDATE_SIGNING_KEY_INVALID')
    }
    let keyObject = privateKey
    if (typeof privateKey === 'string' || Buffer.isBuffer(privateKey)) {
      keyObject = crypto.createPrivateKey(privateKey)
    }
    if (!keyObject || keyObject.asymmetricKeyType !== 'ed25519') {
      throw new UpdateProtocolError('Signing key must be Ed25519.', 'UPDATE_SIGNING_KEY_INVALID')
    }
    return {
      key_id: keyId,
      algorithm: 'Ed25519',
      signature: crypto.sign(null, payload, keyObject).toString('base64'),
    }
  })

  return {
    ...manifestPayload(manifest),
    signatures,
  }
}

function verifyManifestSignatures(manifest, keyring, { now = new Date() } = {}) {
  const signatures = manifest?.signatures
  if (!Array.isArray(signatures) || signatures.length === 0) {
    throw new UpdateProtocolError('Manifest has no signatures.', 'UPDATE_SIGNATURE_INVALID')
  }

  const entries = new Map(validateKeyring(keyring).map((entry) => [entry.key_id, entry]))
  const payload = canonicalJsonBytes(manifestPayload(manifest))
  const seen = new Set()
  const verifiedKeyIds = []
  const activeKeyIds = []
  const currentTime = now instanceof Date ? now : new Date(now)

  if (Number.isNaN(currentTime.getTime())) {
    throw new UpdateProtocolError('Invalid verification time.', 'UPDATE_SIGNATURE_INVALID')
  }

  for (const signature of signatures) {
    if (!isPlainObject(signature) || signature.algorithm !== 'Ed25519') {
      throw new UpdateProtocolError('Unsupported manifest signature.', 'UPDATE_SIGNATURE_INVALID')
    }
    if (typeof signature.key_id !== 'string' || !signature.key_id) {
      throw new UpdateProtocolError('Manifest signature has no key_id.', 'UPDATE_SIGNATURE_INVALID')
    }
    if (typeof signature.signature !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(signature.signature)) {
      throw new UpdateProtocolError('Manifest signature encoding is invalid.', 'UPDATE_SIGNATURE_INVALID')
    }
    const signatureBytes = Buffer.from(signature.signature, 'base64')
    if (signatureBytes.length !== 64) {
      throw new UpdateProtocolError('Ed25519 signature length is invalid.', 'UPDATE_SIGNATURE_INVALID')
    }
    if (seen.has(signature.key_id)) {
      throw new UpdateProtocolError(
        `Duplicate manifest signature key_id: ${signature.key_id}`,
        'UPDATE_SIGNATURE_INVALID',
      )
    }
    seen.add(signature.key_id)

    const key = entries.get(signature.key_id)
    if (!key) {
      throw new UpdateProtocolError(
        `Unknown manifest signing key: ${signature.key_id}`,
        'UPDATE_SIGNATURE_KEY_UNKNOWN',
      )
    }
    if (key.status === 'revoked') {
      throw new UpdateProtocolError(
        `Manifest signing key is revoked: ${signature.key_id}`,
        'UPDATE_SIGNATURE_KEY_REVOKED',
      )
    }
    if (key._notBefore && currentTime < key._notBefore) {
      throw new UpdateProtocolError(
        `Manifest signing key is not active yet: ${signature.key_id}`,
        'UPDATE_SIGNATURE_KEY_UNAVAILABLE',
      )
    }
    if (key._notAfter && currentTime > key._notAfter) {
      throw new UpdateProtocolError(
        `Manifest signing key has expired: ${signature.key_id}`,
        'UPDATE_SIGNATURE_KEY_UNAVAILABLE',
      )
    }

    let verified = false
    try {
      verified = crypto.verify(
        null,
        payload,
        key._publicKeyObject,
        signatureBytes,
      )
    } catch {
      verified = false
    }
    if (!verified) {
      throw new UpdateProtocolError(
        `Manifest signature verification failed: ${signature.key_id}`,
        'UPDATE_SIGNATURE_INVALID',
      )
    }

    verifiedKeyIds.push(signature.key_id)
    if (key.status === 'active') activeKeyIds.push(signature.key_id)
  }

  if (activeKeyIds.length === 0) {
    throw new UpdateProtocolError(
      'Manifest is not authenticated by an active key.',
      'UPDATE_SIGNATURE_ACTIVE_KEY_REQUIRED',
    )
  }

  return {
    verified_key_ids: verifiedKeyIds,
    active_key_ids: activeKeyIds,
  }
}

function createKeyringEntry({
  keyId,
  publicKey,
  status = 'active',
  notBefore = null,
  notAfter = null,
}) {
  if (typeof keyId !== 'string' || !KEY_ID_RE.test(keyId)) {
    throw new UpdateProtocolError('Invalid key_id.', 'UPDATE_KEYRING_INVALID')
  }
  if (!KEY_STATUSES.has(status)) {
    throw new UpdateProtocolError('Invalid key status.', 'UPDATE_KEYRING_INVALID')
  }
  const keyObject = publicKey?.asymmetricKeyType
    ? publicKey
    : crypto.createPublicKey(publicKey)
  if (keyObject.asymmetricKeyType !== 'ed25519') {
    throw new UpdateProtocolError('Public key must be Ed25519.', 'UPDATE_KEYRING_INVALID')
  }
  return {
    key_id: keyId,
    algorithm: 'Ed25519',
    public_key: keyObject.export({ format: 'der', type: 'spki' }).toString('base64'),
    status,
    not_before: notBefore,
    not_after: notAfter,
  }
}

function updateKeyStatus(keyring, keyId, status) {
  validateKeyring(keyring)
  if (!KEY_STATUSES.has(status)) {
    throw new UpdateProtocolError('Invalid key status.', 'UPDATE_KEYRING_INVALID')
  }
  if (!keyring.keys.some((key) => key.key_id === keyId)) {
    throw new UpdateProtocolError(`Unknown key_id: ${keyId}`, 'UPDATE_KEYRING_INVALID')
  }
  const updated = {
    ...keyring,
    keys: keyring.keys.map((key) => (
      key.key_id === keyId ? { ...key, status } : key
    )),
    updated_at: new Date().toISOString(),
  }
  validateKeyring(updated)
  return updated
}

function createDetachedSignature(manifest, keyId) {
  const signature = manifest.signatures?.find((item) => item.key_id === keyId)
  if (!signature) {
    throw new UpdateProtocolError(`Signature key_id not found: ${keyId}`, 'UPDATE_SIGNATURE_INVALID')
  }
  return {
    schema_version: 1,
    key_id: keyId,
    algorithm: 'Ed25519',
    payload_sha256: crypto.createHash('sha256').update(canonicalJsonBytes(manifestPayload(manifest))).digest('hex'),
    signature: signature.signature,
  }
}

module.exports = {
  KEY_ID_RE,
  createDetachedSignature,
  createKeyringEntry,
  manifestPayload,
  signManifest,
  updateKeyStatus,
  validateKeyring,
  verifyManifestSignatures,
}
