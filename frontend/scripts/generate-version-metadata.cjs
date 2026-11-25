const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const frontendRoot = path.resolve(__dirname, '..')
const packageJson = JSON.parse(fs.readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'))
const contractPath = path.join(frontendRoot, 'update', 'update-contract.json')
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'))

function argumentValue(name, fallback = null) {
  const index = process.argv.indexOf(name)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}

const projectRoot = path.resolve(argumentValue('--git-root', path.resolve(frontendRoot, '..')))
const NO_TAG_LABEL = '未标记'

function tryGit(args) {
  try {
    return {
      ok: true,
      value: execFileSync('git', args, {
        cwd: projectRoot,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      }).trim(),
    }
  } catch (error) {
    return {
      ok: false,
      error,
    }
  }
}

function requireGit(args, description) {
  const result = tryGit(args)
  if (!result.ok) {
    const detail = String(result.error?.stderr || result.error?.message || '').trim()
    throw new Error(`${description}: ${detail || 'Git command failed.'}`)
  }
  return result.value
}

function resolveGitIdentity() {
  const commit = requireGit(['rev-parse', 'HEAD'], 'Unable to determine the current Git commit')
  if (!/^[a-fA-F0-9]{7,64}$/.test(commit)) {
    throw new Error('Unable to determine the current Git commit.')
  }

  const tags = requireGit(['tag', '--list'], 'Unable to list Git tags')
  let tag = NO_TAG_LABEL
  let exactTag = null
  if (tags) {
    const described = requireGit(
      ['describe', '--tags', 'HEAD'],
      'Unable to describe the current Git commit',
    )
    if (!described) {
      throw new Error('Unable to describe the current Git commit.')
    }
    const exact = tryGit(['describe', '--tags', '--exact-match', 'HEAD'])
    exactTag = exact.ok && exact.value ? exact.value : null
    tag = described
  }

  const status = requireGit(
    ['status', '--porcelain', '--untracked-files=normal'],
    'Unable to inspect Git worktree status',
  )

  return {
    commit,
    exactTag,
    tag,
    dirty: Boolean(status),
  }
}

function writeMetadata(outputPath, metadata) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true })
  const tempPath = `${outputPath}.${process.pid}.tmp`
  fs.writeFileSync(tempPath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8')
  fs.renameSync(tempPath, outputPath)
}

function main() {
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
  if (contract.schema_version !== 1 || !contract.product_id || !contract.channel || !contract.updater_version) {
    console.error(`Invalid update contract: ${contractPath}`)
    process.exit(1)
  }

  const identity = resolveGitIdentity()
  if (requireRelease) {
    const expectedTags = new Set([`v${buildVersion}`, buildVersion])
    if (!identity.exactTag || !expectedTags.has(identity.exactTag)) {
      console.error(`Release build requires an exact Git tag matching ${buildVersion}.`)
      process.exit(1)
    }
    if (identity.dirty) {
      console.error('Release build requires a clean Git worktree.')
      process.exit(1)
    }
  }

  const metadata = {
    schema_version: 1,
    product_id: contract.product_id,
    version: buildVersion,
    channel: contract.channel,
    tag: identity.tag,
    commit: identity.commit,
    build_time: new Date().toISOString(),
    updater_version: contract.updater_version,
    build_dirty: identity.dirty,
  }

  writeMetadata(outputPath, metadata)
  console.log(JSON.stringify({ output: outputPath, metadata }, null, 2))
}

try {
  main()
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
