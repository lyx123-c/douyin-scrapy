const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const {
  createKeyringEntry,
  updateKeyStatus,
  validateKeyring,
} = require('../electron/update/signature.cjs')

function value(flag) {
  const index = process.argv.indexOf(flag)
  return index >= 0 ? process.argv[index + 1] : null
}

function usage() {
  console.error(
    'Usage: node scripts/manage-update-key.cjs generate --key-id <id> --private-key-out <file> --keyring <file> | retire|revoke --key-id <id> --keyring <file>',
  )
}

function emptyKeyring() {
  return {
    schema_version: 1,
    keys: [],
    updated_at: null,
  }
}

function writeJson(filePath, payload) {
  const resolved = path.resolve(filePath)
  fs.mkdirSync(path.dirname(resolved), { recursive: true })
  const temp = `${resolved}.${process.pid}.tmp`
  fs.writeFileSync(temp, `${JSON.stringify(payload, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  fs.renameSync(temp, resolved)
}

const command = process.argv[2]
const keyId = value('--key-id')
const keyringPath = value('--keyring')
if (!command || !keyId || !keyringPath) {
  usage()
  process.exit(1)
}

try {
  const resolvedKeyring = path.resolve(keyringPath)
  const keyring = fs.existsSync(resolvedKeyring)
    ? JSON.parse(fs.readFileSync(resolvedKeyring, 'utf8'))
    : emptyKeyring()
  validateKeyring(keyring)

  if (command === 'generate') {
    const privateKeyOut = value('--private-key-out')
    if (!privateKeyOut) throw new Error('generate requires --private-key-out')
    const resolvedPrivateKey = path.resolve(privateKeyOut)
    if (fs.existsSync(resolvedPrivateKey)) {
      throw new Error(`Refusing to overwrite existing private key: ${resolvedPrivateKey}`)
    }
    const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519')
    const entry = createKeyringEntry({ keyId, publicKey, status: 'active' })
    if (keyring.keys.some((key) => key.key_id === keyId)) {
      throw new Error(`Key already exists: ${keyId}`)
    }
    const updated = {
      ...keyring,
      keys: [...keyring.keys, entry],
      updated_at: new Date().toISOString(),
    }
    validateKeyring(updated)
    fs.mkdirSync(path.dirname(resolvedPrivateKey), { recursive: true })
    fs.writeFileSync(
      resolvedPrivateKey,
      privateKey.export({ format: 'pem', type: 'pkcs8' }),
      { encoding: 'utf8', mode: 0o600, flag: 'wx' },
    )
    writeJson(resolvedKeyring, updated)
    console.log(JSON.stringify({ key_id: keyId, private_key: resolvedPrivateKey, keyring: resolvedKeyring }, null, 2))
    process.exit(0)
  }

  if (command === 'retire' || command === 'revoke') {
    const status = command === 'retire' ? 'retired' : 'revoked'
    const updated = updateKeyStatus(keyring, keyId, status)
    writeJson(resolvedKeyring, updated)
    console.log(JSON.stringify({ key_id: keyId, status, keyring: resolvedKeyring }, null, 2))
    process.exit(0)
  }

  usage()
  process.exit(1)
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
