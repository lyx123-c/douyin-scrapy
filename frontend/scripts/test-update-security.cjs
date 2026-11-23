const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { canonicalize } = require('../electron/update/canonical-json.cjs')
const {
  selectCompatiblePackage,
  verifyArtifactFile,
  verifyUpdateManifest,
} = require('../electron/update/manifest-verifier.cjs')
const { validateUpdatePath } = require('../electron/update/path-policy.cjs')
const {
  createKeyringEntry,
  signManifest,
  verifyManifestSignatures,
} = require('../electron/update/signature.cjs')
const {
  fetchUpdateManifest,
  releaseAssetUrl,
  validateUpdateSourceConfig,
} = require('../electron/update/update-source.cjs')

const PRODUCT_ID = 'com.local.douyinworkbench'
const CURRENT_VERSION = '1.2.0'
const TARGET_VERSION = '1.3.0'
const UPDATER_VERSION = '0.1.0'
const ALLOWED_ASSET_HOSTS = ['github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com']

function sha(char = 'a') {
  return char.repeat(64)
}

function makeKey(keyId) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519')
  return {
    keyId,
    privateKey,
    entry: createKeyringEntry({ keyId, publicKey, status: 'active' }),
  }
}

function makeManifest() {
  return {
    schema_version: 1,
    product_id: PRODUCT_ID,
    channel: 'stable',
    version: TARGET_VERSION,
    tag: 'v1.3.0',
    commit: 'abcdef1234567890',
    build_time: '2026-11-23T10:00:00.000Z',
    release_sequence: 10,
    published_at: '2026-11-23T10:05:00.000Z',
    min_supported_version: '1.0.0',
    minimum_updater_version: UPDATER_VERSION,
    release_notes: 'Security and reliability update.',
    files: [
      { path: 'program/app.asar', size: 100, sha256: sha('a') },
      { path: 'program/resources/backend/Backend.exe', size: 200, sha256: sha('b'), executable: true },
      { path: 'updater/Updater.exe', size: 300, sha256: sha('c'), executable: true },
    ],
    changes: {
      added: ['updater/Updater.exe'],
      replaced: ['program/app.asar', 'program/resources/backend/Backend.exe'],
      removed: ['program/old-runtime.dll'],
    },
    packages: [
      {
        package_id: 'file-1.2.0-to-1.3.0',
        type: 'file_delta',
        base_version: CURRENT_VERSION,
        target_version: TARGET_VERSION,
        archive_format: 'zip',
        delta_algorithm: 'none',
        url: 'https://github.com/example/updates/releases/download/v1.3.0/file.zip',
        size: 1000,
        sha256: sha('d'),
      },
      {
        package_id: 'full-1.3.0',
        type: 'full',
        base_version: null,
        target_version: TARGET_VERSION,
        archive_format: 'nsis',
        delta_algorithm: 'none',
        url: 'https://github.com/example/updates/releases/download/v1.3.0/setup.exe',
        size: 2000,
        sha256: sha('e'),
      },
    ],
    signatures: [],
  }
}

const currentFiles = [
  'program/app.asar',
  'program/resources/backend/Backend.exe',
  'program/old-runtime.dll',
]

function verify(manifest, keyring, overrides = {}) {
  return verifyUpdateManifest(manifest, {
    keyring,
    expectedProductId: PRODUCT_ID,
    expectedChannel: 'stable',
    currentVersion: CURRENT_VERSION,
    currentUpdaterVersion: UPDATER_VERSION,
    currentFiles,
    allowedAssetHosts: ALLOWED_ASSET_HOSTS,
    ...overrides,
  })
}

test('canonical JSON sorts object keys and preserves arrays', () => {
  assert.equal(
    canonicalize({ z: 1, a: [{ y: 2, x: 3 }] }),
    '{"a":[{"x":3,"y":2}],"z":1}',
  )
})

test('1. valid Ed25519 manifest signature is accepted', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  const verified = verifyManifestSignatures(signed, keyring)
  assert.deepEqual(verified.active_key_ids, ['release-2026'])
  assert.equal(verify(signed, keyring).status, 'update_available')
})

test('2. manifest content tampering invalidates the signature', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  signed.release_notes = 'tampered'
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verifyManifestSignatures(signed, keyring),
    (error) => error.code === 'UPDATE_SIGNATURE_INVALID',
  )
})

test('3. invalid signature bytes are rejected', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  signed.signatures[0].signature = Buffer.alloc(64).toString('base64')
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verifyManifestSignatures(signed, keyring),
    (error) => error.code === 'UPDATE_SIGNATURE_INVALID',
  )
})

test('4. unknown signing keys are rejected', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  signed.signatures[0].key_id = 'unknown-key'
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verifyManifestSignatures(signed, keyring),
    (error) => error.code === 'UPDATE_SIGNATURE_KEY_UNKNOWN',
  )
})

test('5. incorrect product identifiers are rejected after valid signature verification', () => {
  const key = makeKey('release-2026')
  const manifest = makeManifest()
  manifest.product_id = 'com.example.other'
  const signed = signManifest(manifest, [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring),
    (error) => error.code === 'UPDATE_PRODUCT_MISMATCH',
  )
})

test('6. version downgrade is rejected', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring, { currentVersion: '1.4.0' }),
    (error) => error.code === 'UPDATE_VERSION_DOWNGRADE',
  )
})

test('7. stale release replay is rejected', () => {
  const key = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring, {
      lastAcceptedRelease: { version: TARGET_VERSION, release_sequence: 10 },
    }),
    (error) => error.code === 'UPDATE_REPLAY_DETECTED',
  )
})

test('8. SHA256 mismatch is rejected separately from signatures', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-update-artifact-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const artifact = path.join(root, 'package.bin')
  fs.writeFileSync(artifact, 'authenticated content')
  await assert.rejects(
    verifyArtifactFile(artifact, {
      expectedSize: Buffer.byteLength('authenticated content'),
      expectedSha256: sha('f'),
    }),
    (error) => error.code === 'UPDATE_ARTIFACT_HASH_MISMATCH',
  )
})

test('9. path traversal is rejected', () => {
  const key = makeKey('release-2026')
  const manifest = makeManifest()
  manifest.files[0].path = 'program/../../user-data/cookies.sqlite'
  const signed = signManifest(manifest, [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring),
    (error) => error.code === 'UPDATE_PATH_INVALID',
  )
})

test('10. deleting a user-data directory is rejected', () => {
  const key = makeKey('release-2026')
  const manifest = makeManifest()
  manifest.changes.removed = ['user-data/cookies.sqlite']
  const signed = signManifest(manifest, [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring),
    (error) => error.code === 'UPDATE_PATH_INVALID',
  )
})

test('11. incompatible base version never selects a delta package', () => {
  const manifest = makeManifest()
  const selected = selectCompatiblePackage(manifest, CURRENT_VERSION)
  assert.equal(selected.type, 'file_delta')

  const incompatible = {
    packages: [{
      ...manifest.packages[0],
      base_version: '1.1.0',
    }],
  }
  assert.throws(
    () => selectCompatiblePackage(incompatible, CURRENT_VERSION),
    (error) => error.code === 'UPDATE_BASE_VERSION_INCOMPATIBLE',
  )
})

test('12. unknown manifest schema version is rejected', () => {
  const key = makeKey('release-2026')
  const manifest = makeManifest()
  manifest.schema_version = 2
  const signed = signManifest(manifest, [{ keyId: key.keyId, privateKey: key.privateKey }])
  const keyring = { schema_version: 1, keys: [key.entry], updated_at: null }
  assert.throws(
    () => verify(signed, keyring),
    (error) => error.code === 'UPDATE_SCHEMA_UNSUPPORTED',
  )
})

test('key rotation accepts active and retired dual signatures but rejects revoked keys', () => {
  const oldKey = makeKey('release-2025')
  const newKey = makeKey('release-2026')
  const signed = signManifest(makeManifest(), [
    { keyId: oldKey.keyId, privateKey: oldKey.privateKey },
    { keyId: newKey.keyId, privateKey: newKey.privateKey },
  ])
  const keyring = {
    schema_version: 1,
    keys: [
      { ...oldKey.entry, status: 'retired' },
      newKey.entry,
    ],
    updated_at: null,
  }
  assert.equal(verify(signed, keyring).status, 'update_available')

  const revoked = {
    ...keyring,
    keys: [keyring.keys[0], { ...keyring.keys[1], status: 'revoked' }],
  }
  assert.throws(
    () => verify(signed, revoked),
    (error) => error.code === 'UPDATE_SIGNATURE_KEY_REVOKED',
  )
})

test('update source distinguishes network failures from not-found and never uses an API token', async () => {
  const config = validateUpdateSourceConfig({
    schema_version: 1,
    provider: 'github_releases',
    repository: 'example/douyin-updates',
    allowed_manifest_hosts: ['github.com', 'release-assets.githubusercontent.com'],
    allowed_asset_hosts: ALLOWED_ASSET_HOSTS,
    request_timeout_ms: 5000,
    manifest_asset: 'stable.json',
  })
  assert.match(releaseAssetUrl(config, 'stable.json'), /github\.com\/example\/douyin-updates/)

  await assert.rejects(
    fetchUpdateManifest(config, {
      fetchImpl: async () => {
        throw new Error('offline')
      },
    }),
    (error) => error.code === 'UPDATE_NETWORK_ERROR',
  )

  await assert.rejects(
    fetchUpdateManifest(config, {
      fetchImpl: async () => ({
        ok: false,
        status: 404,
        url: 'https://github.com/example/douyin-updates/releases/latest/download/stable.json',
      }),
    }),
    (error) => error.code === 'UPDATE_SOURCE_NOT_FOUND',
  )

  assert.throws(
    () => validateUpdateSourceConfig({
      schema_version: 1,
      provider: 'github_releases',
      repository: 'lyx123-c/q',
      allowed_manifest_hosts: ['github.com'],
      allowed_asset_hosts: ALLOWED_ASSET_HOSTS,
      request_timeout_ms: 5000,
    }),
    (error) => error.code === 'UPDATE_SOURCE_REPOSITORY_CONFLICT',
  )

  const explicitlyApproved = validateUpdateSourceConfig({
    schema_version: 1,
    provider: 'github_releases',
    repository: 'lyx123-c/q',
    allow_development_repository_as_update_source: true,
    allowed_manifest_hosts: ['github.com'],
    allowed_asset_hosts: ALLOWED_ASSET_HOSTS,
    request_timeout_ms: 5000,
  })
  assert.equal(explicitlyApproved.repository, 'lyx123-c/q')
})

test('key generation and manifest signing CLIs produce a verifiable signature', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-update-cli-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const keyringPath = path.join(root, 'keyring.json')
  const privateKeyPath = path.join(root, 'private.pem')
  const unsignedPath = path.join(root, 'manifest-unsigned.json')
  const signedPath = path.join(root, 'manifest.json')
  execFileSync(process.execPath, [
    path.join(__dirname, 'manage-update-key.cjs'),
    'generate',
    '--key-id', 'release-2026-cli',
    '--private-key-out', privateKeyPath,
    '--keyring', keyringPath,
  ], { stdio: 'pipe' })
  fs.writeFileSync(unsignedPath, JSON.stringify(makeManifest(), null, 2), 'utf8')
  execFileSync(process.execPath, [
    path.join(__dirname, 'sign-update-manifest.cjs'),
    '--manifest', unsignedPath,
    '--key', `release-2026-cli=${privateKeyPath}`,
    '--output', signedPath,
  ], { stdio: 'pipe' })
  const signed = JSON.parse(fs.readFileSync(signedPath, 'utf8'))
  const keyring = JSON.parse(fs.readFileSync(keyringPath, 'utf8'))
  assert.deepEqual(verifyManifestSignatures(signed, keyring).active_key_ids, ['release-2026-cli'])
})

test('filesystem path policy rejects absolute, traversal and alternate data streams', () => {
  assert.equal(validateUpdatePath('program/resources/app.asar'), 'program/resources/app.asar')
  for (const value of [
    '/program/app.asar',
    'program/../app.asar',
    'program/app.asar:secret',
    'program\\app.asar',
    'data/cookies.sqlite',
    'program/CON.txt',
  ]) {
    assert.throws(() => validateUpdatePath(value), (error) => error.code === 'UPDATE_PATH_INVALID')
  }
})
