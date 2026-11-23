const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const storage = require('../electron/storage-config.cjs')

const fsp = fs.promises

async function makeTempRoot(t) {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'douyin-storage-test-'))
  t.after(async () => {
    await fsp.rm(root, { recursive: true, force: true })
  })
  return root
}

test('storage config round-trips and reports managed paths', async (t) => {
  const root = await makeTempRoot(t)
  const configPath = path.join(root, 'bootstrap', 'storage-settings.json')
  const appDataRoot = path.join(root, 'app-data')
  const videoRoot = path.join(root, 'videos')
  const installRoot = path.join(root, 'install')

  const written = await storage.writeStorageConfig(configPath, { appDataRoot, videoRoot })
  const read = await storage.readStorageConfig(configPath)
  const status = storage.storageStatus(read, installRoot)

  assert.equal(written.version, storage.STORAGE_CONFIG_VERSION)
  assert.equal(read.appDataRoot, path.resolve(appDataRoot))
  assert.equal(read.videoRoot, path.resolve(videoRoot))
  assert.equal(status.paths.browserProfile, path.join(appDataRoot, 'browser-profile'))
  assert.equal(status.paths.downloadQueue, path.join(appDataRoot, 'tasks', 'download_queue.json'))
})

test('storage roots reject equal, nested and installation paths', async (t) => {
  const root = await makeTempRoot(t)
  const appDataRoot = path.join(root, 'app-data')
  const videoRoot = path.join(root, 'videos')
  const installRoot = path.join(root, 'install')

  assert.throws(
    () => storage.validateStorageRoots({ appDataRoot, videoRoot: appDataRoot, installRoot }),
    (error) => error.code === 'STORAGE_ROOTS_EQUAL',
  )
  assert.throws(
    () => storage.validateStorageRoots({
      appDataRoot,
      videoRoot: path.join(appDataRoot, 'nested'),
      installRoot,
    }),
    (error) => error.code === 'STORAGE_ROOTS_NESTED',
  )
  assert.throws(
    () => storage.validateStorageRoots({
      appDataRoot: path.join(installRoot, 'data'),
      videoRoot,
      installRoot,
    }),
    (error) => error.code === 'STORAGE_PATH_INSIDE_INSTALL',
  )
})

test('verified migration copies files and refuses a nonempty target', async (t) => {
  const root = await makeTempRoot(t)
  const source = path.join(root, 'source')
  const target = path.join(root, 'target')
  const nonemptyTarget = path.join(root, 'nonempty')
  await fsp.mkdir(path.join(source, 'database'), { recursive: true })
  await fsp.writeFile(path.join(source, 'tasks.json'), '{"version":1}\n')
  await fsp.writeFile(path.join(source, 'database', 'videos.csv'), 'id,title\n1,demo\n')
  await fsp.mkdir(nonemptyTarget)
  await fsp.writeFile(path.join(nonemptyTarget, 'existing.txt'), 'keep')

  await storage.ensureDirectoryWritable(target)
  const result = await storage.copyDirectoryVerified(source, target)
  assert.equal(result.files, 2)
  assert.equal(result.bytes, 30)
  assert.equal(
    await fsp.readFile(path.join(target, 'database', 'videos.csv'), 'utf8'),
    'id,title\n1,demo\n',
  )

  await assert.rejects(
    storage.copyDirectoryVerified(source, nonemptyTarget),
    (error) => error.code === 'STORAGE_MIGRATION_TARGET_NOT_EMPTY',
  )
  assert.equal(await fsp.readFile(path.join(nonemptyTarget, 'existing.txt'), 'utf8'), 'keep')
})
