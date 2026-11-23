const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const frontendRoot = path.resolve(__dirname, '..')
const projectRoot = path.resolve(frontendRoot, '..')
const packageJson = JSON.parse(fs.readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'))
const contractPath = path.join(frontendRoot, 'update', 'update-contract.json')
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'))

function argumentValue(name, fallback = null) {
  const index = process.argv.indexOf(name)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}

function git(args, fallback = '') {
  try {
    return execFileSync('git', args, {
      cwd: projectRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      windowsHide: true,
    }).trim()
  } catch {
    return fallback
  }
}

const requireRelease = process.argv.includes('--require-release')
const testBuild = process.argv.includes('--test-build')
if (requireRelease && testBuild) {
  console.error('Release builds cannot use test version overrides.')
  process.exit(1)
}
const buildVersion = argumentValue('--version', packageJson.version)
if (buildVersion !== packageJson.version && !testBuild) {
  console.error('Version override requires --test-build.')
  process.exit(1)
}
if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(buildVersion)) {
  console.error(`Invalid build version override: ${buildVersion}`)
  process.exit(1)
}
const outputPath = path.resolve(
  argumentValue('--output', path.join(frontendRoot, 'build', 'version-metadata.json')),
)
const commit = git(['rev-parse', 'HEAD'])
const tag = git(['describe', '--tags', '--exact-match', 'HEAD'])
const dirty = Boolean(git(['status', '--porcelain', '--untracked-files=normal']))

if (!/^[a-fA-F0-9]{7,64}$/.test(commit)) {
  console.error('Unable to determine the current Git commit.')
  process.exit(1)
}
if (contract.schema_version !== 1 || !contract.product_id || !contract.channel || !contract.updater_version) {
  console.error(`Invalid update contract: ${contractPath}`)
  process.exit(1)
}
if (requireRelease) {
  const expectedTags = new Set([`v${buildVersion}`, buildVersion])
  if (!tag || !expectedTags.has(tag)) {
    console.error(`Release build requires an exact Git tag matching ${buildVersion}.`)
    process.exit(1)
  }
  if (dirty) {
    console.error('Release build requires a clean Git worktree.')
    process.exit(1)
  }
}

const metadata = {
  schema_version: 1,
  product_id: contract.product_id,
  version: buildVersion,
  channel: contract.channel,
  tag,
  commit,
  build_time: new Date().toISOString(),
  updater_version: contract.updater_version,
  build_dirty: dirty,
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true })
const tempPath = `${outputPath}.${process.pid}.tmp`
fs.writeFileSync(tempPath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8')
fs.renameSync(tempPath, outputPath)
console.log(JSON.stringify({ output: outputPath, metadata }, null, 2))
