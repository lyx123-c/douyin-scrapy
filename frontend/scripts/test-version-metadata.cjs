const assert = require('node:assert/strict')
const { execFileSync, spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const versionMetadata = require('../electron/update/version-metadata.cjs')

const frontendRoot = path.resolve(__dirname, '..')
const projectRoot = path.resolve(frontendRoot, '..')
const generatorPath = path.join(__dirname, 'generate-version-metadata.cjs')

function run(command, args, cwd) {
  return execFileSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  }).trim()
}

function git(cwd, args) {
  return run('git', args, cwd)
}

function initializeRepository(root) {
  git(root, ['init'])
  git(root, ['config', 'user.name', 'Version Metadata Test'])
  git(root, ['config', 'user.email', 'version-metadata@example.invalid'])
  fs.writeFileSync(path.join(root, 'state.txt'), 'one\n', 'utf8')
  git(root, ['add', 'state.txt'])
  git(root, ['commit', '-m', 'first commit'])
}

function commitFile(root, content, message) {
  fs.writeFileSync(path.join(root, 'state.txt'), content, 'utf8')
  git(root, ['add', 'state.txt'])
  git(root, ['commit', '-m', message])
}

function generate(gitRoot, outputPath) {
  return spawnSync(
    process.execPath,
    [generatorPath, '--git-root', gitRoot, '--output', outputPath],
    {
      cwd: frontendRoot,
      encoding: 'utf8',
      windowsHide: true,
    },
  )
}

function generateRelease(gitRoot, outputPath) {
  return spawnSync(
    process.execPath,
    [generatorPath, '--git-root', gitRoot, '--output', outputPath, '--require-release'],
    {
      cwd: frontendRoot,
      encoding: 'utf8',
      windowsHide: true,
    },
  )
}

function readGeneratedMetadata(outputPath) {
  return JSON.parse(fs.readFileSync(outputPath, 'utf8'))
}

test('version metadata records the exact tag when HEAD is tagged', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-exact-'))
  const outputPath = path.join(root, 'metadata.json')
  try {
    initializeRepository(root)
    git(root, ['tag', 'v0.2.10'])
    const result = generate(root, outputPath)
    assert.equal(result.status, 0, result.stderr)
    const metadata = readGeneratedMetadata(outputPath)
    assert.equal(metadata.tag, 'v0.2.10')
    assert.equal(metadata.commit, git(root, ['rev-parse', 'HEAD']))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('version metadata records git describe when HEAD is ahead of the latest tag', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-ahead-'))
  const outputPath = path.join(root, 'metadata.json')
  try {
    initializeRepository(root)
    git(root, ['tag', 'v0.2.10'])
    commitFile(root, 'two\n', 'second commit')
    const expectedTag = git(root, ['describe', '--tags', 'HEAD'])
    const result = generate(root, outputPath)
    assert.equal(result.status, 0, result.stderr)
    const metadata = readGeneratedMetadata(outputPath)
    assert.equal(metadata.tag, expectedTag)
    assert.match(metadata.tag, /^v0\.2\.10-1-g[0-9a-f]+$/)
    assert.equal(metadata.commit, git(root, ['rev-parse', 'HEAD']))
    assert.doesNotThrow(() => versionMetadata.validateVersionMetadata(metadata))
    assert.equal(versionMetadata.createVersionInfo(metadata).tag, metadata.tag)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('release identity still requires an exact tag when HEAD is ahead', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-release-ahead-'))
  const outputPath = path.join(root, 'metadata.json')
  try {
    initializeRepository(root)
    git(root, ['tag', 'v0.2.10'])
    commitFile(root, 'two\n', 'second commit')
    const result = generateRelease(root, outputPath)
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /requires an exact Git tag matching/)
    assert.equal(fs.existsSync(outputPath), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('version metadata reports no marker only when the repository has no tags', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-untagged-'))
  const outputPath = path.join(root, 'metadata.json')
  try {
    initializeRepository(root)
    const result = generate(root, outputPath)
    assert.equal(result.status, 0, result.stderr)
    const metadata = readGeneratedMetadata(outputPath)
    assert.equal(metadata.tag, '未标记')
    assert.equal(metadata.commit, git(root, ['rev-parse', 'HEAD']))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('version metadata generation fails without fabricating a tag when Git fails', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-git-failure-'))
  const outputPath = path.join(root, 'metadata.json')
  try {
    const result = generate(root, outputPath)
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /Unable to determine the current Git commit/)
    assert.equal(fs.existsSync(outputPath), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('current repository metadata matches git describe and preserves the full commit', () => {
  const outputPath = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), 'douyin-version-current-')),
    'metadata.json',
  )
  try {
    const expectedTag = git(projectRoot, ['describe', '--tags', 'HEAD'])
    const expectedCommit = git(projectRoot, ['rev-parse', 'HEAD'])
    const result = generate(projectRoot, outputPath)
    assert.equal(result.status, 0, result.stderr)
    const metadata = readGeneratedMetadata(outputPath)
    assert.equal(metadata.tag, expectedTag)
    assert.equal(metadata.commit, expectedCommit)
  } finally {
    fs.rmSync(path.dirname(outputPath), { recursive: true, force: true })
  }
})
