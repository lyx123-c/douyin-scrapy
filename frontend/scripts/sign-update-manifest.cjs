const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const {
  createDetachedSignature,
  signManifest,
} = require('../electron/update/signature.cjs')

function usage() {
  console.error(
    'Usage: node scripts/sign-update-manifest.cjs --manifest <file> --key <key-id=private-key.pem> [--key ...] [--output <file>] [--detached <file> --detached-key <key-id>]',
  )
}

function valuesFor(flag) {
  const values = []
  for (let index = 0; index < process.argv.length; index += 1) {
    if (process.argv[index] === flag && process.argv[index + 1]) values.push(process.argv[index + 1])
  }
  return values
}

const manifestPath = valuesFor('--manifest')[0]
const keySpecs = valuesFor('--key')
const outputPath = valuesFor('--output')[0]
const detachedPath = valuesFor('--detached')[0]
const detachedKey = valuesFor('--detached-key')[0]

if (!manifestPath || keySpecs.length === 0) {
  usage()
  process.exit(1)
}

try {
  const manifest = JSON.parse(fs.readFileSync(path.resolve(manifestPath), 'utf8'))
  const signers = keySpecs.map((spec) => {
    const separator = spec.indexOf('=')
    if (separator <= 0 || separator === spec.length - 1) {
      throw new Error(`Invalid --key value: ${spec}`)
    }
    const keyId = spec.slice(0, separator)
    const keyPath = path.resolve(spec.slice(separator + 1))
    return {
      keyId,
      privateKey: crypto.createPrivateKey(fs.readFileSync(keyPath)),
    }
  })

  const signed = signManifest(manifest, signers)
  const signedPath = path.resolve(outputPath || manifestPath)
  fs.mkdirSync(path.dirname(signedPath), { recursive: true })
  fs.writeFileSync(signedPath, `${JSON.stringify(signed, null, 2)}\n`, 'utf8')

  let detachedResult = null
  if (detachedPath) {
    const signatureKey = detachedKey || signers[0].keyId
    if (!signers.some((signer) => signer.keyId === signatureKey)) {
      throw new Error(`Detached signature key is not present in signers: ${signatureKey}`)
    }
    const detached = createDetachedSignature(signed, signatureKey)
    const resolvedDetachedPath = path.resolve(detachedPath)
    fs.mkdirSync(path.dirname(resolvedDetachedPath), { recursive: true })
    fs.writeFileSync(resolvedDetachedPath, `${JSON.stringify(detached, null, 2)}\n`, 'utf8')
    detachedResult = resolvedDetachedPath
  }

  console.log(JSON.stringify({
    manifest: signedPath,
    key_ids: signed.signatures.map((item) => item.key_id),
    detached_signature: detachedResult,
  }, null, 2))
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
