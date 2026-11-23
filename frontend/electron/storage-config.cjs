const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const fsp = fs.promises
const STORAGE_CONFIG_VERSION = 1

class StorageConfigError extends Error {
  constructor(message, code = 'STORAGE_CONFIG_ERROR') {
    super(message)
    this.name = 'StorageConfigError'
    this.code = code
  }
}

function normalizeDirectory(value) {
  const normalized = String(value || '').trim()
  if (!normalized) {
    throw new StorageConfigError('目录路径不能为空。', 'STORAGE_PATH_EMPTY')
  }
  if (!path.isAbsolute(normalized)) {
    throw new StorageConfigError(`目录必须是绝对路径：${normalized}`, 'STORAGE_PATH_NOT_ABSOLUTE')
  }
  return path.resolve(normalized)
}

function isSubpath(parent, child) {
  const relative = path.relative(parent, child)
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
}

function validateStorageRoots({ appDataRoot, videoRoot, installRoot }) {
  const appRoot = normalizeDirectory(appDataRoot)
  const videoDir = normalizeDirectory(videoRoot)
  const installDir = normalizeDirectory(installRoot)

  if (appRoot === videoDir) {
    throw new StorageConfigError(
      '应用数据根目录和视频下载目录不能相同。',
      'STORAGE_ROOTS_EQUAL',
    )
  }
  if (isSubpath(appRoot, videoDir) || isSubpath(videoDir, appRoot)) {
    throw new StorageConfigError(
      '应用数据根目录和视频下载目录不能互相嵌套。',
      'STORAGE_ROOTS_NESTED',
    )
  }
  if (isSubpath(installDir, appRoot) || isSubpath(installDir, videoDir)) {
    throw new StorageConfigError(
      '应用数据和视频目录必须位于软件安装目录之外。',
      'STORAGE_PATH_INSIDE_INSTALL',
    )
  }

  return { appDataRoot: appRoot, videoRoot: videoDir }
}

async function ensureDirectoryWritable(directory) {
  const target = normalizeDirectory(directory)
  try {
    await fsp.mkdir(target, { recursive: true })
    const probe = path.join(
      target,
      `.douyin-write-test-${process.pid}-${crypto.randomBytes(6).toString('hex')}`,
    )
    const handle = await fsp.open(probe, 'wx')
    try {
      await handle.writeFile('storage-write-test', 'utf8')
      await handle.sync()
    } finally {
      await handle.close()
    }
    await fsp.unlink(probe)
    return target
  } catch (error) {
    throw new StorageConfigError(
      `目录不可写：${target}。${error.message}`,
      'STORAGE_PATH_NOT_WRITABLE',
    )
  }
}

async function readStorageConfig(configPath) {
  try {
    const raw = await fsp.readFile(configPath, 'utf8')
    const data = JSON.parse(raw)
    if (!data || data.version !== STORAGE_CONFIG_VERSION) {
      throw new StorageConfigError('存储配置版本无效。', 'STORAGE_CONFIG_INVALID')
    }
    return {
      ...data,
      appDataRoot: normalizeDirectory(data.appDataRoot),
      videoRoot: normalizeDirectory(data.videoRoot),
    }
  } catch (error) {
    if (error.code === 'ENOENT') return null
    if (error instanceof StorageConfigError) throw error
    throw new StorageConfigError(
      `无法读取存储配置：${error.message}`,
      'STORAGE_CONFIG_READ_FAILED',
    )
  }
}

async function writeStorageConfig(configPath, config) {
  const payload = {
    version: STORAGE_CONFIG_VERSION,
    appDataRoot: normalizeDirectory(config.appDataRoot),
    videoRoot: normalizeDirectory(config.videoRoot),
    updatedAt: new Date().toISOString(),
  }
  await fsp.mkdir(path.dirname(configPath), { recursive: true })
  const tempPath = `${configPath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`
  try {
    await fsp.writeFile(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
    await fsp.rename(tempPath, configPath)
  } finally {
    await fsp.rm(tempPath, { force: true }).catch(() => {})
  }
  return payload
}

async function chooseDirectory(dialog, parentWindow, { title, defaultPath }) {
  const result = await dialog.showOpenDialog(parentWindow || undefined, {
    title,
    defaultPath,
    properties: ['openDirectory', 'createDirectory'],
  })
  if (result.canceled || !result.filePaths?.length) {
    throw new StorageConfigError('用户取消了目录选择。', 'STORAGE_SELECTION_CANCELLED')
  }
  return normalizeDirectory(result.filePaths[0])
}

async function directoryIsEmpty(directory) {
  try {
    return (await fsp.readdir(directory)).length === 0
  } catch (error) {
    if (error.code === 'ENOENT') return true
    throw error
  }
}

async function hashFile(filePath) {
  const hash = crypto.createHash('sha256')
  const stream = fs.createReadStream(filePath)
  for await (const chunk of stream) hash.update(chunk)
  return hash.digest('hex')
}

async function buildTreeManifest(root) {
  const manifest = new Map()
  async function walk(current, relativeRoot = '') {
    const entries = await fsp.readdir(current, { withFileTypes: true })
    for (const entry of entries) {
      const absolute = path.join(current, entry.name)
      const relative = relativeRoot ? `${relativeRoot}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        manifest.set(relative, { type: 'directory' })
        await walk(absolute, relative)
      } else if (entry.isSymbolicLink()) {
        manifest.set(relative, {
          type: 'symlink',
          target: await fsp.readlink(absolute),
        })
      } else if (entry.isFile()) {
        const stat = await fsp.stat(absolute)
        manifest.set(relative, {
          type: 'file',
          size: stat.size,
          sha256: await hashFile(absolute),
        })
      }
    }
  }
  await walk(normalizeDirectory(root))
  return manifest
}

function compareManifests(source, target) {
  if (source.size !== target.size) {
    throw new StorageConfigError(
      `迁移文件数量不一致：${source.size} -> ${target.size}`,
      'STORAGE_MIGRATION_VERIFY_FAILED',
    )
  }
  for (const [relative, sourceEntry] of source) {
    const targetEntry = target.get(relative)
    if (!targetEntry || JSON.stringify(sourceEntry) !== JSON.stringify(targetEntry)) {
      throw new StorageConfigError(
        `迁移文件校验失败：${relative}`,
        'STORAGE_MIGRATION_VERIFY_FAILED',
      )
    }
  }
}

async function copyDirectoryVerified(source, target) {
  const sourceRoot = normalizeDirectory(source)
  const targetRoot = normalizeDirectory(target)
  if (isSubpath(sourceRoot, targetRoot) || isSubpath(targetRoot, sourceRoot)) {
    throw new StorageConfigError(
      '迁移源目录和目标目录不能相同或嵌套。',
      'STORAGE_MIGRATION_PATH_INVALID',
    )
  }
  if (!(await directoryIsEmpty(targetRoot))) {
    throw new StorageConfigError(
      `目标目录不是空目录：${targetRoot}`,
      'STORAGE_MIGRATION_TARGET_NOT_EMPTY',
    )
  }

  const targetExisted = fs.existsSync(targetRoot)
  const createdFiles = []
  const createdDirectories = []

  try {
    if (!targetExisted) {
      await fsp.mkdir(targetRoot, { recursive: true })
    }
    async function copy(current, destination) {
      const entries = await fsp.readdir(current, { withFileTypes: true })
      for (const entry of entries) {
        const sourcePath = path.join(current, entry.name)
        const targetPath = path.join(destination, entry.name)
        if (entry.isDirectory()) {
          await fsp.mkdir(targetPath, { recursive: false })
          createdDirectories.push(targetPath)
          await copy(sourcePath, targetPath)
        } else if (entry.isSymbolicLink()) {
          const linkTarget = await fsp.readlink(sourcePath)
          await fsp.symlink(linkTarget, targetPath)
          createdFiles.push(targetPath)
        } else if (entry.isFile()) {
          await fsp.copyFile(sourcePath, targetPath, fs.constants.COPYFILE_EXCL)
          createdFiles.push(targetPath)
        }
      }
    }
    await copy(sourceRoot, targetRoot)
    const sourceManifest = await buildTreeManifest(sourceRoot)
    const targetManifest = await buildTreeManifest(targetRoot)
    compareManifests(sourceManifest, targetManifest)
    return {
      source: sourceRoot,
      target: targetRoot,
      files: [...sourceManifest.values()].filter((item) => item.type === 'file').length,
      bytes: [...sourceManifest.values()]
        .filter((item) => item.type === 'file')
        .reduce((total, item) => total + item.size, 0),
    }
  } catch (error) {
    for (const filePath of createdFiles.reverse()) {
      await fsp.rm(filePath, { force: true }).catch(() => {})
    }
    for (const directory of createdDirectories.reverse()) {
      await fsp.rmdir(directory).catch(() => {})
    }
    if (!targetExisted) {
      await fsp.rmdir(targetRoot).catch(() => {})
    }
    throw error
  }
}

function storageStatus(config, installRoot) {
  return {
    version: STORAGE_CONFIG_VERSION,
    appDataRoot: normalizeDirectory(config.appDataRoot),
    videoRoot: normalizeDirectory(config.videoRoot),
    installRoot: normalizeDirectory(installRoot),
    paths: {
      videoData: path.join(config.appDataRoot, 'data'),
      tasks: path.join(config.appDataRoot, 'tasks'),
      browserProfile: path.join(config.appDataRoot, 'browser-profile'),
      logs: path.join(config.appDataRoot, 'logs'),
      videoLibraryCsv: path.join(config.appDataRoot, 'data', 'douyin_videos.csv'),
      analyticsCsv: path.join(config.appDataRoot, 'data', 'video_analytics.csv'),
      downloadQueue: path.join(config.appDataRoot, 'tasks', 'download_queue.json'),
    },
  }
}

module.exports = {
  STORAGE_CONFIG_VERSION,
  StorageConfigError,
  buildTreeManifest,
  chooseDirectory,
  compareManifests,
  copyDirectoryVerified,
  ensureDirectoryWritable,
  normalizeDirectory,
  readStorageConfig,
  storageStatus,
  validateStorageRoots,
  writeStorageConfig,
}
