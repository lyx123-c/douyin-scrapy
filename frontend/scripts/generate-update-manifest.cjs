const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const frontendRoot = path.resolve(__dirname, '..')
const projectRoot = path.resolve(frontendRoot, '..')
const packageJson = JSON.parse(fs.readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'))
const contract = JSON.parse(fs.readFileSync(path.join(frontendRoot, 'update', 'update-contract.json'), 'utf8'))

function arg(name, fallback = null) {
  const index = process.argv.indexOf(name)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}

async function sha256File(filePath) {
  const hash = crypto.createHash('sha256')
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('error', reject)
    stream.on('end', resolve)
  })
  return hash.digest('hex')
}

async function fileInventory(root) {
  const resolvedRoot = path.resolve(root)
  const entries = []
  async function walk(current, relativeRoot = '') {
    const children = await fs.promises.readdir(current, { withFileTypes: true })
    for (const child of children.sort((a, b) => a.name.localeCompare(b.name))) {
      const absolute = path.join(current, child.name)
      const relative = relativeRoot ? `${relativeRoot}/${child.name}` : child.name
      if (child.isDirectory()) {
        await walk(absolute, relative)
      } else if (child.isSymbolicLink()) {
        throw new Error(`Symlinks are not supported in update inventory: ${relative}`)
      } else if (child.isFile()) {
        const stat = await fs.promises.stat(absolute)
        entries.push({
          path: `program/${relative}`,
          size: stat.size,
          sha256: await sha256File(absolute),
          executable: /\.exe$/i.test(relative),
        })
      }
    }
  }
  await walk(resolvedRoot)
  return entries
}

async function main() {
  const version = String(arg('--version', packageJson.version))
  const releaseSequence = Number(arg('--release-sequence', '0'))
  const minSupportedVersion = String(arg('--min-supported-version', '0.0.0'))
  const unpackedRoot = path.resolve(arg('--unpacked', path.join(frontendRoot, 'release', 'win-unpacked')))
  const installerPath = path.resolve(arg(
    '--installer',
    path.join(frontendRoot, 'release', `${packageJson.build.productName}-${version}-x64.exe`),
  ))
  const outputPath = path.resolve(arg('--output', path.join(frontendRoot, 'release', 'stable-unsigned.json')))
  const notesPath = arg('--release-notes-file')
  const releaseNotes = notesPath
    ? fs.readFileSync(path.resolve(notesPath), 'utf8').trim()
    : String(arg('--release-notes', '抖音视频工作台完整安装包更新。'))

  if (!Number.isSafeInteger(releaseSequence) || releaseSequence < 1) {
    throw new Error('--release-sequence must be a positive integer.')
  }
  if (!contract.update_repository) {
    throw new Error('update-contract.json repository is not configured.')
  }
  if (!fs.existsSync(unpackedRoot) || !fs.statSync(unpackedRoot).isDirectory()) {
    throw new Error(`Unpacked application directory not found: ${unpackedRoot}`)
  }
  if (!fs.existsSync(installerPath)) {
    throw new Error(`Installer not found: ${installerPath}`)
  }

  const versionMetadataPath = path.join(unpackedRoot, 'resources', 'version-metadata.json')
  const versionMetadata = JSON.parse(fs.readFileSync(versionMetadataPath, 'utf8'))
  if (versionMetadata.version !== version || versionMetadata.product_id !== contract.product_id) {
    throw new Error('version-metadata.json does not match release version and product.')
  }
  const files = await fileInventory(unpackedRoot)
  if (files.length === 0) throw new Error('Update file inventory is empty.')

  const installerName = path.basename(installerPath)
  const packageUrl = `https://github.com/${contract.update_repository}/releases/download/v${version}/${encodeURIComponent(installerName)}`
  const manifest = {
    schema_version: 1,
    product_id: contract.product_id,
    channel: contract.channel,
    version,
    tag: `v${version}`,
    commit: versionMetadata.commit,
    build_time: versionMetadata.build_time,
    release_sequence: releaseSequence,
    published_at: new Date().toISOString(),
    min_supported_version: minSupportedVersion,
    minimum_updater_version: contract.updater_version,
    release_notes: releaseNotes,
    files,
    changes: {
      added: [],
      replaced: [],
      removed: [],
    },
    packages: [{
      package_id: `full-${version}`,
      type: 'full',
      base_version: null,
      target_version: version,
      archive_format: 'nsis',
      delta_algorithm: 'none',
      url: packageUrl,
      size: fs.statSync(installerPath).size,
      sha256: await sha256File(installerPath),
    }],
    signatures: [],
  }

  fs.mkdirSync(path.dirname(outputPath), { recursive: true })
  fs.writeFileSync(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  console.log(JSON.stringify({
    output: outputPath,
    version,
    release_sequence: releaseSequence,
    files: files.length,
    installer: installerPath,
    installer_bytes: manifest.packages[0].size,
    installer_sha256: manifest.packages[0].sha256,
    url: packageUrl,
  }, null, 2))
}

main().catch((error) => {
  console.error(error.message)
  process.exit(1)
})
