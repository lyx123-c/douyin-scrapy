const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron')
const { spawn, execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const storage = require('./storage-config.cjs')
const { launchInstallerAfterExit } = require('./update/installer-handoff.cjs')
const signature = require('./update/signature.cjs')
const { UpdateController, UPDATE_STATUSES } = require('./update/update-controller.cjs')
const updateSource = require('./update/update-source.cjs')
const versionMetadata = require('./update/version-metadata.cjs')

const APP_ID = 'com.local.douyinworkbench'
const APP_NAME = '抖音视频工作台'

const DEV_URL = String(process.env.ELECTRON_DEV_URL || '').trim()
const IS_SMOKE_TEST = process.env.ELECTRON_SMOKE_TEST === '1'
const WINDOW_BEHAVIOR_TEST = process.env.ELECTRON_WINDOW_TEST === '1'
const SKIP_BACKEND_START = process.env.ELECTRON_SKIP_BACKEND_START === '1'
const BACKEND_HOST = '127.0.0.1'
const BACKEND_PORT = process.env.DOUYIN_BACKEND_PORT || '8000'
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`
const HEALTH_URL = `${BACKEND_URL}/api/health`
const APP_ICON = app.isPackaged
  ? path.join(process.resourcesPath, 'icon.png')
  : path.join(__dirname, '..', 'build', 'icon.png')
const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
const RENDERER_ENTRY = path.join(app.getAppPath(), 'dist', 'index.html')
const VERSION_METADATA_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'version-metadata.json')
  : path.join(__dirname, '..', 'build', 'version-metadata.json')
const UPDATE_SOURCE_CONFIG_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'update', 'update-source.json')
  : path.join(__dirname, '..', 'update', 'update-source.json')
const UPDATE_KEYRING_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'update', 'keyring.json')
  : path.join(__dirname, '..', 'update', 'keyring.json')


if (IS_SMOKE_TEST) {
  const smokeUserData = path.join(app.getPath('temp'), `douyin-workbench-smoke-${process.pid}`)
  fs.mkdirSync(smokeUserData, { recursive: true })
  app.setPath('userData', smokeUserData)
}

const STORAGE_CONFIG_PATH = process.env.DOUYIN_STORAGE_CONFIG_PATH
  || path.join(app.getPath('userData'), 'storage-settings.json')
const UPDATE_RESULT_PATH = path.join(app.getPath('userData'), 'update-result.json')

const hasSingleInstanceLock = IS_SMOKE_TEST || app.requestSingleInstanceLock()

let mainWindow = null
let backendProc = null
let backendKilled = false
let storageSettings = null
let localVersionMetadata = null
let updateSourceConfig = null
let updateKeyring = null
let updateController = null
let storageOperationInProgress = false

function validateDevUrl(value) {
  const url = new URL(value)
  if (!['127.0.0.1', 'localhost', '::1'].includes(url.hostname)) {
    throw new Error(`开发服务器只允许本机地址：${value}`)
  }
  return url.toString()
}

function resolveBackendWorkingDirectory() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : PROJECT_ROOT
}

function resolveFfmpegExecutable() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'ffmpeg', 'ffmpeg.exe')
  }
  return String(process.env.FFMPEG_PATH || '').trim()
}

function resolveDevPythonExecutable() {
  const candidates = [process.env.DOUYIN_PYTHON_PATH]

  candidates.push(
    path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(os.homedir(), '.venv', 'Scripts', 'python.exe'),
  )

  const executable = candidates
    .filter(Boolean)
    .find((candidate) => fs.existsSync(candidate))

  return executable || 'python'
}

function getInstallRoot() {
  return app.isPackaged ? path.dirname(app.getPath('exe')) : PROJECT_ROOT
}

function applyStorageEnvironment(config) {
  process.env.DOUYIN_APP_USER_DATA_DIR = config.appDataRoot
  process.env.DOUYIN_SAVE_DIR = config.videoRoot
  storageSettings = config
}

async function validateAndCreateStorage(config) {
  const roots = storage.validateStorageRoots({
    appDataRoot: config.appDataRoot,
    videoRoot: config.videoRoot,
    installRoot: getInstallRoot(),
  })
  await storage.ensureDirectoryWritable(roots.appDataRoot)
  await storage.ensureDirectoryWritable(roots.videoRoot)
  return { ...config, ...roots }
}

async function promptForStorageConfiguration() {
  const legacyRoot = app.getPath('userData')
  const hasLegacyData = ['data', 'tasks', 'browser-profile']
    .some((name) => fs.existsSync(path.join(legacyRoot, name)))
  if (hasLegacyData) {
    const result = await dialog.showMessageBox({
      type: 'info',
      title: '存储位置',
      message: '检测到旧版本数据。本版本不会自动迁移，请在下一步主动选择数据位置。',
      detail: legacyRoot,
      buttons: ['继续选择', '退出'],
      defaultId: 0,
      cancelId: 1,
    })
    if (result.response !== 0) {
      throw new storage.StorageConfigError('用户取消了存储初始化。', 'STORAGE_SETUP_CANCELLED')
    }
  }

  while (true) {
    try {
      const appDataRoot = await storage.chooseDirectory(dialog, null, {
        title: '选择应用数据根目录',
        defaultPath: path.join(app.getPath('documents'), APP_NAME),
      })
      const videoRoot = await storage.chooseDirectory(dialog, null, {
        title: '选择视频下载目录',
        defaultPath: path.join(app.getPath('videos'), APP_NAME),
      })
      return await validateAndCreateStorage({ appDataRoot, videoRoot })
    } catch (error) {
      if (error.code === 'STORAGE_SELECTION_CANCELLED') throw error
      const result = await dialog.showMessageBox({
        type: 'error',
        title: '存储目录不可用',
        message: error.message || String(error),
        buttons: ['重新选择', '退出'],
        defaultId: 0,
        cancelId: 1,
      })
      if (result.response !== 0) throw error
    }
  }
}

async function prepareStorageConfiguration() {
  if (!app.isPackaged) {
    return { persist: false }
  }

  try {
    const persisted = await storage.readStorageConfig(STORAGE_CONFIG_PATH)
    if (persisted) {
      const validated = await validateAndCreateStorage(persisted)
      applyStorageEnvironment(validated)
      return { config: validated, persist: false }
    }
  } catch (error) {
    await dialog.showMessageBox({
      type: 'warning',
      title: '存储配置不可用',
      message: '现有存储配置无法继续使用，请重新选择目录。',
      detail: error.message || String(error),
      buttons: ['重新选择'],
    })
  }

  const envDataRoot = String(process.env.DOUYIN_APP_USER_DATA_DIR || '').trim()
  const envVideoRoot = String(process.env.DOUYIN_SAVE_DIR || '').trim()
  let config
  if (envDataRoot && envVideoRoot && (IS_SMOKE_TEST || process.env.DOUYIN_STORAGE_ALLOW_ENV === '1')) {
    config = await validateAndCreateStorage({ appDataRoot: envDataRoot, videoRoot: envVideoRoot })
  } else {
    config = await promptForStorageConfiguration()
  }
  applyStorageEnvironment(config)
  return { config, persist: true }
}

async function persistPreparedStorage(prepared) {
  if (app.isPackaged && prepared.persist && prepared.config) {
    await storage.writeStorageConfig(STORAGE_CONFIG_PATH, prepared.config)
  }
}

async function backendIsReady() {
  try {
    const response = await fetch(HEALTH_URL)
    if (!response.ok) return false
    const payload = await response.json()
    return payload?.success === true && payload?.data?.status === 'running'
  } catch {
    return false
  }
}

function startBackend() {
  const backendRoot = resolveBackendWorkingDirectory()
  if (!fs.existsSync(backendRoot)) {
    throw new Error(`Backend 运行目录不存在：${backendRoot}`)
  }
  const executable = app.isPackaged
    ? path.join(backendRoot, 'Backend.exe')
    : resolveDevPythonExecutable()
  if (app.isPackaged && !fs.existsSync(executable)) {
    throw new Error(`未找到随包 Backend：${executable}`)
  }

  const args = app.isPackaged
    ? []
    : [
        '-m',
        'uvicorn',
        'backend.api.main:app',
        '--host',
        BACKEND_HOST,
        '--port',
        String(BACKEND_PORT),
      ]

  backendKilled = false
  const browserRoot = path.join(backendRoot, 'playwright-browsers')
  const backendEnv = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    DOUYIN_BACKEND_HOST: BACKEND_HOST,
    DOUYIN_BACKEND_PORT: String(BACKEND_PORT),
    DOUYIN_PRODUCT_ID: localVersionMetadata.product_id,
    DOUYIN_PRODUCT_VERSION: localVersionMetadata.version,
    DOUYIN_UPDATE_CHANNEL: localVersionMetadata.channel,
    DOUYIN_RELEASE_TAG: localVersionMetadata.tag,
    DOUYIN_RELEASE_COMMIT: localVersionMetadata.commit,
    DOUYIN_BUILD_TIME: localVersionMetadata.build_time,
    DOUYIN_UPDATER_VERSION: localVersionMetadata.updater_version,
  }
  if (app.isPackaged) {
    for (const name of [
      'DOUYIN_VIDEO_DATA_DIR',
      'DOUYIN_TASK_DATA_DIR',
      'DOUYIN_BROWSER_PROFILE_DIR',
      'DOUYIN_ANALYTICS_CSV',
      'DOUYIN_VIDEOS_CSV',
      'DOUYIN_DOWNLOAD_QUEUE_JSON',
      'DOUYIN_LOG_DIR',
    ]) {
      delete backendEnv[name]
    }
  }
  if (app.isPackaged && fs.existsSync(browserRoot)) {
    backendEnv.PLAYWRIGHT_BROWSERS_PATH = browserRoot
  }
  const ffmpegExecutable = resolveFfmpegExecutable()
  if (ffmpegExecutable) {
    backendEnv.FFMPEG_PATH = ffmpegExecutable
    console.log(`[electron] ffmpeg executable: ${ffmpegExecutable} (exists=${fs.existsSync(ffmpegExecutable)})`)
  }

  backendProc = spawn(executable, args, {
    cwd: backendRoot,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: backendEnv,
  })

  backendProc.stdout.on('data', (chunk) => {
    process.stdout.write(`[backend] ${chunk}`)
  })
  backendProc.stderr.on('data', (chunk) => {
    process.stderr.write(`[backend] ${chunk}`)
  })
  backendProc.on('error', (error) => {
    console.error(`[electron] backend spawn failed: ${error.message}`)
    backendProc = null
  })
  backendProc.on('exit', (code, signal) => {
    console.log(`[electron] backend exited (code=${code}, signal=${signal})`)
    backendProc = null
  })
}

function killBackend() {
  if (backendKilled || !backendProc || !backendProc.pid) {
    return
  }
  backendKilled = true
  const pid = backendProc.pid

  try {
    if (process.platform === 'win32') {
      execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      })
    } else {
      backendProc.kill('SIGKILL')
    }
  } catch {
    // Process may already have exited.
  } finally {
    backendProc = null
  }
}

async function waitForBackendReady(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (!backendProc) {
      throw new Error('FastAPI 后端进程提前退出，请检查启动日志。')
    }
    if (await backendIsReady()) {
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  throw new Error(`等待 FastAPI 就绪超时（${HEALTH_URL}）`)
}

async function ensureBackend() {
  if (await backendIsReady()) {
    console.log(`[electron] reusing backend at ${BACKEND_URL}`)
    return
  }
  if (SKIP_BACKEND_START) {
    console.log('[electron] backend startup skipped by ELECTRON_SKIP_BACKEND_START')
    return
  }
  startBackend()
  await waitForBackendReady()
}

function comparablePath(value) {
  const resolved = path.resolve(String(value || ''))
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

async function verifyBackendStorage(config) {
  const payload = await getBackendPayload('/api/system/storage')
  const expectedDerivedPaths = {
    video_data_dir: path.join(config.appDataRoot, 'data'),
    task_data_dir: path.join(config.appDataRoot, 'tasks'),
    browser_profile_dir: path.join(config.appDataRoot, 'browser-profile'),
    log_dir: path.join(config.appDataRoot, 'logs'),
  }
  const appDataMatches = comparablePath(payload.app_user_data_dir) === comparablePath(config.appDataRoot)
  const videoMatches = comparablePath(payload.save_dir) === comparablePath(config.videoRoot)
  const derivedPathsMatch = Object.entries(expectedDerivedPaths).every(
    ([key, expected]) => comparablePath(payload[key]) === comparablePath(expected),
  )
  if (
    !appDataMatches
    || !videoMatches
    || !derivedPathsMatch
    || payload.using_external_app_data !== true
  ) {
    throw new storage.StorageConfigError(
      'Backend 实际存储路径与 Electron 配置不一致。',
      'STORAGE_BACKEND_PATH_MISMATCH',
    )
  }
  return payload
}

async function getBackendPayload(pathname) {
  const response = await fetch(`${BACKEND_URL}${pathname}`)
  const payload = await response.json()
  if (!response.ok || payload?.success !== true) {
    throw new storage.StorageConfigError(
      payload?.message || `Backend 请求失败：${pathname}`,
      'STORAGE_BACKEND_REQUEST_FAILED',
    )
  }
  return payload.data
}

async function assertBackendIdle() {
  if (!backendProc?.pid) {
    throw new storage.StorageConfigError(
      '当前 Backend 不是由本应用启动，不能安全修改存储位置。',
      'STORAGE_BACKEND_NOT_OWNED',
    )
  }
  const [downloads, capture] = await Promise.all([
    getBackendPayload('/api/downloads/status'),
    getBackendPayload('/api/capture/status'),
  ])
  const captureActive = ['running', 'stopping'].includes(capture?.status)
  if (downloads?.running || downloads?.thread_alive || captureActive) {
    throw new storage.StorageConfigError(
      '抓取或下载任务正在运行，请停止任务后再修改存储位置。',
      'STORAGE_BACKEND_BUSY',
    )
  }
}

async function stopBackendAndWait(timeoutMs = 15000) {
  const proc = backendProc
  if (!proc) return
  const exited = new Promise((resolve) => {
    if (proc.exitCode !== null) {
      resolve(true)
      return
    }
    const done = () => resolve(true)
    proc.once('exit', done)
    proc.once('error', done)
  })
  killBackend()
  const completed = await Promise.race([
    exited,
    new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
  ])
  if (!completed) {
    throw new storage.StorageConfigError(
      'Backend 未能在修改存储位置前安全退出。',
      'STORAGE_BACKEND_STOP_TIMEOUT',
    )
  }
}

async function restartBackendWithStorage(config) {
  applyStorageEnvironment(config)
  try {
    startBackend()
    await waitForBackendReady()
    await verifyBackendStorage(config)
  } catch (error) {
    await stopBackendAndWait().catch((stopError) => {
      console.error(`[electron] failed to clean up storage restart: ${stopError.message}`)
    })
    throw error
  }
}

async function restoreStorageAfterFailure(oldConfig) {
  if (!oldConfig) return
  await storage.writeStorageConfig(STORAGE_CONFIG_PATH, oldConfig).catch(() => {})
  applyStorageEnvironment(oldConfig)
  try {
    await restartBackendWithStorage(oldConfig)
  } catch (error) {
    console.error(`[electron] failed to restore backend storage: ${error.message}`)
  }
}

async function storageStatusForRenderer() {
  return {
    configured: Boolean(storageSettings),
    production: app.isPackaged,
    installRoot: getInstallRoot(),
    appDataRoot: storageSettings?.appDataRoot || null,
    videoRoot: storageSettings?.videoRoot || null,
    paths: storageSettings ? storage.storageStatus(storageSettings, getInstallRoot()).paths : null,
  }
}

async function withStorageOperation(operation) {
  if (storageOperationInProgress) {
    throw new storage.StorageConfigError('另一个存储操作正在进行。', 'STORAGE_OPERATION_BUSY')
  }
  storageOperationInProgress = true
  try {
    return await operation()
  } finally {
    storageOperationInProgress = false
  }
}

async function changeVideoDirectory() {
  if (!app.isPackaged || !storageSettings) {
    throw new storage.StorageConfigError('仅正式版支持修改视频下载目录。', 'STORAGE_PRODUCTION_ONLY')
  }
  return withStorageOperation(async () => {
    await assertBackendIdle()
    const videoRoot = await storage.chooseDirectory(dialog, mainWindow, {
      title: '选择新的视频下载目录',
      defaultPath: storageSettings.videoRoot,
    })
    const candidate = await validateAndCreateStorage({
      appDataRoot: storageSettings.appDataRoot,
      videoRoot,
    })
    const oldConfig = storageSettings
    await stopBackendAndWait()
    try {
      await storage.writeStorageConfig(STORAGE_CONFIG_PATH, candidate)
      await restartBackendWithStorage(candidate)
    } catch (error) {
      await restoreStorageAfterFailure(oldConfig)
      throw error
    }
    return storageStatusForRenderer()
  })
}

async function migrateAppDataDirectory() {
  if (!app.isPackaged || !storageSettings) {
    throw new storage.StorageConfigError('仅正式版支持迁移应用数据目录。', 'STORAGE_PRODUCTION_ONLY')
  }
  return withStorageOperation(async () => {
    await assertBackendIdle()
    const appDataRoot = await storage.chooseDirectory(dialog, mainWindow, {
      title: '选择新的应用数据根目录',
      defaultPath: storageSettings.appDataRoot,
    })
    const candidate = await validateAndCreateStorage({
      appDataRoot,
      videoRoot: storageSettings.videoRoot,
    })
    if (candidate.appDataRoot === storageSettings.appDataRoot) {
      throw new storage.StorageConfigError('新目录与当前应用数据目录相同。', 'STORAGE_PATH_UNCHANGED')
    }
    const oldConfig = storageSettings
    await stopBackendAndWait()
    try {
      const migration = await storage.copyDirectoryVerified(
        oldConfig.appDataRoot,
        candidate.appDataRoot,
      )
      await storage.writeStorageConfig(STORAGE_CONFIG_PATH, candidate)
      await restartBackendWithStorage(candidate)
      return { ...(await storageStatusForRenderer()), migration }
    } catch (error) {
      await restoreStorageAfterFailure(oldConfig)
      throw error
    }
  })
}

function registerStorageIpc() {
  ipcMain.handle('storage:get-status', () => storageStatusForRenderer())
  ipcMain.handle('storage:choose-video-directory', () => changeVideoDirectory())
  ipcMain.handle('storage:choose-data-directory', () => migrateAppDataDirectory())
  ipcMain.handle('storage:open-directory', async (_event, target) => {
    const status = await storageStatusForRenderer()
    const resolved = path.resolve(String(target || ''))
    const normalizedTarget = comparablePath(resolved)
    const allowed = new Set([status.appDataRoot, status.videoRoot, status.installRoot]
      .filter(Boolean)
      .map(comparablePath))
    if (!allowed.has(normalizedTarget)) {
      throw new storage.StorageConfigError('不允许打开该目录。', 'STORAGE_OPEN_PATH_DENIED')
    }
    const error = await shell.openPath(resolved)
    if (error) throw new storage.StorageConfigError(error, 'STORAGE_OPEN_FAILED')
    return { path: resolved }
  })
}

function broadcastUpdateState(state) {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send('updates:state-changed', state)
  }
}

async function postBackendPayload(pathname) {
  const response = await fetch(`${BACKEND_URL}${pathname}`, { method: 'POST' })
  const payload = await response.json()
  if (!response.ok || payload?.success !== true) {
    throw new storage.StorageConfigError(
      payload?.message || `Backend 请求失败：${pathname}`,
      payload?.data?.code || 'UPDATE_BACKEND_REQUEST_FAILED',
    )
  }
  return payload.data
}

function waitForProcessExit(proc, timeoutMs) {
  if (!proc) return Promise.resolve(true)
  if (proc.exitCode !== null) return Promise.resolve(true)
  return new Promise((resolve) => {
    const finish = (exited) => {
      clearTimeout(timer)
      proc.off('exit', onExit)
      proc.off('error', onError)
      resolve(exited)
    }
    const onExit = () => finish(true)
    const onError = () => finish(true)
    const timer = setTimeout(() => finish(false), timeoutMs)
    proc.once('exit', onExit)
    proc.once('error', onError)
  })
}

async function prepareBackendForUpdate() {
  if (!app.isPackaged) {
    throw new storage.StorageConfigError(
      '开发模式不允许启动安装程序。',
      'UPDATE_PRODUCTION_ONLY',
    )
  }
  await assertBackendIdle()
  const proc = backendProc
  const payload = await postBackendPayload('/api/system/prepare-update-shutdown')
  if (!payload?.runtime_stopped || !payload?.shutdown_scheduled) {
    throw new storage.StorageConfigError(
      'Backend 未确认安全停止。',
      'UPDATE_BACKEND_SHUTDOWN_UNCONFIRMED',
    )
  }
  const exited = await waitForProcessExit(proc, 35000)
  if (!exited) {
    throw new storage.StorageConfigError(
      'Backend 未在安装前完全退出，已取消安装。',
      'UPDATE_BACKEND_EXIT_TIMEOUT',
    )
  }
}

async function launchVerifiedInstaller(installerPath, targetVersion) {
  launchInstallerAfterExit({
    electronPid: process.pid,
    installerPath,
    appExePath: app.getPath('exe'),
    installRoot: getInstallRoot(),
    targetVersion,
    resultPath: UPDATE_RESULT_PATH,
  })
  setImmediate(() => app.exit(0))
}

async function verifyBackendReleaseIdentity() {
  const payload = await getBackendPayload('/api/health')
  if (payload.product_id !== localVersionMetadata.product_id
    || payload.version !== localVersionMetadata.version) {
    throw new Error('Backend 版本信息与安装元数据不一致。')
  }
}

function readUpdateResult() {
  try {
    if (!fs.existsSync(UPDATE_RESULT_PATH)) return null
    return JSON.parse(fs.readFileSync(UPDATE_RESULT_PATH, 'utf8'))
  } catch (error) {
    console.error(`[electron] failed to read update result: ${error.message}`)
    return null
  }
}

function applyStartupUpdateResult() {
  const result = readUpdateResult()
  if (!result || !updateController) return
  updateController.applyStartupResult(result)
  if (result.status === 'installed' && result.target_version === localVersionMetadata.version) {
    fs.rmSync(UPDATE_RESULT_PATH, { force: true })
  }
}

async function recoverBackendAfterFailedUpdate() {
  if (backendProc || !storageSettings) return
  startBackend()
  await waitForBackendReady()
  await verifyBackendStorage(storageSettings)
}

function registerUpdateIpc() {
  ipcMain.handle('updates:get-local-info', () => versionMetadata.createVersionInfo(
    localVersionMetadata,
    { updateSource: updateSourceConfig },
  ))
  ipcMain.handle('updates:get-state', () => updateController?.getState() || null)
  ipcMain.handle('updates:reveal-installer', () => {
    const installerPath = updateController?.getInstallerPath()
    if (!installerPath || !fs.existsSync(installerPath)) {
      throw new Error('更新安装包不存在或尚未下载完成。')
    }
    shell.showItemInFolder(installerPath)
    return { path: installerPath }
  })
  ipcMain.handle('updates:check', () => {
    if (!updateController) throw new Error('更新控制器尚未初始化。')
    return updateController.check()
  })
  ipcMain.handle('updates:start-download', () => {
    if (!updateController) throw new Error('更新控制器尚未初始化。')
    return updateController.startDownload()
  })
  ipcMain.handle('updates:cancel-download', () => {
    if (!updateController) throw new Error('更新控制器尚未初始化。')
    return updateController.cancelDownload()
  })
  ipcMain.handle('updates:quit-and-install', () => {
    if (!updateController) throw new Error('更新控制器尚未初始化。')
    return updateController.install()
  })
}
async function exerciseWindowControls() {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
  mainWindow.minimize()
  await wait(150)
  const minimized = mainWindow.isMinimized()
  mainWindow.restore()
  await wait(150)
  const restored = !mainWindow.isMinimized()
  mainWindow.maximize()
  await wait(150)
  const maximized = mainWindow.isMaximized()
  mainWindow.unmaximize()
  await wait(150)
  return {
    minimized,
    restored,
    maximized,
    unmaximized: !mainWindow.isMaximized(),
    minimizable: mainWindow.isMinimizable(),
    maximizable: mainWindow.isMaximizable(),
    resizable: mainWindow.isResizable(),
  }
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    title: APP_NAME,
    icon: APP_ICON,
    frame: true,
    autoHideMenuBar: true,
    backgroundColor: '#f3f6f6',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      additionalArguments: [`--douyin-backend-url=${BACKEND_URL}`],
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  mainWindow.removeMenu()

  mainWindow.once('ready-to-show', () => {
    if (!IS_SMOKE_TEST || WINDOW_BEHAVIOR_TEST) {
      mainWindow.show()
    }
  })

  mainWindow.webContents.on('did-finish-load', () => {
    console.log(`[electron] renderer loaded: ${mainWindow.webContents.getURL()}`)
    if (!IS_SMOKE_TEST) {
      return
    }
    setTimeout(async () => {
      try {
        const state = await mainWindow.webContents.executeJavaScript(`(async () => {
          const apiBaseUrl = String(window.desktopRuntime?.apiBaseUrl || '')
          const state = {
            bridge: Boolean(window.desktopRuntime?.electron),
            mounted: Boolean(document.querySelector('#app')?.childElementCount),
            url: window.location.href,
            apiBaseUrl,
            versionInfo: null,
            apiHealth: null,
          }
          try {
            state.versionInfo = await window.desktopRuntime?.updates?.getLocalInfo() || null
          } catch (error) {
            state.versionInfo = { error: String(error?.message || error) }
          }
          try {
            const response = await fetch(apiBaseUrl + '/api/health')
            const payload = await response.json()
            state.apiHealth = {
              ok: response.ok,
              version: payload?.data?.version || '',
              status: payload?.data?.status || '',
            }
          } catch (error) {
            state.apiHealth = { ok: false, error: String(error?.message || error) }
          }
          return state
        })()`)
        if (WINDOW_BEHAVIOR_TEST) {
          state.window = await exerciseWindowControls()
        }
        console.log(`[electron] smoke state: ${JSON.stringify(state)}`)
        const controlsOk = !WINDOW_BEHAVIOR_TEST || (
          state.window?.minimized
          && state.window?.restored
          && state.window?.maximized
          && state.window?.unmaximized
          && state.window?.minimizable
          && state.window?.maximizable
          && state.window?.resizable
        )
        app.exit(
          state.bridge
          && state.mounted
          && state.apiBaseUrl
          && state.versionInfo?.product_id
          && state.versionInfo?.current_version
          && state.versionInfo.current_version === state.apiHealth?.version
          && state.apiHealth?.ok
          && state.apiHealth?.status === 'running'
          && controlsOk
            ? 0
            : 1,
        )
      } catch (error) {
        console.error(`[electron] smoke check failed: ${error.message}`)
        app.exit(1)
      }
    }, 500)
  })

  mainWindow.webContents.on('did-fail-load', (_event, code, description, url) => {
    if (code === -3) {
      return
    }
    dialog.showErrorBox(
      '页面加载失败',
      `无法加载页面。\n\n地址：${url}\n错误：${description} (${code})`,
    )
  })

  if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

  if (DEV_URL) {
    await mainWindow.loadURL(validateDevUrl(DEV_URL))
    return
  }

  if (!fs.existsSync(RENDERER_ENTRY)) {
    throw new Error(`未找到前端生产构建：${RENDERER_ENTRY}`)
  }
  await mainWindow.loadFile(RENDERER_ENTRY)
}


if (!hasSingleInstanceLock) {
  app.quit()
} else {
  registerStorageIpc()
  registerUpdateIpc()

  app.on('second-instance', () => {
    if (!mainWindow) {
      return
    }
    if (mainWindow.isMinimized()) {
      mainWindow.restore()
    }
    mainWindow.focus()
  })

  app.whenReady().then(async () => {
    try {
      app.setAppUserModelId(APP_ID)
      localVersionMetadata = versionMetadata.readVersionMetadata(VERSION_METADATA_PATH)
      if (localVersionMetadata.version !== app.getVersion()) {
        throw new Error(
          `Version metadata mismatch: app=${app.getVersion()} metadata=${localVersionMetadata.version}`,
        )
      }
      updateSourceConfig = updateSource.readUpdateSourceConfig(UPDATE_SOURCE_CONFIG_PATH)
      updateKeyring = JSON.parse(fs.readFileSync(UPDATE_KEYRING_PATH, 'utf8'))
      signature.validateKeyring(updateKeyring)
      updateController = new UpdateController({
        currentVersion: localVersionMetadata.version,
        currentUpdaterVersion: localVersionMetadata.updater_version,
        productId: localVersionMetadata.product_id,
        channel: localVersionMetadata.channel,
        sourceConfig: updateSourceConfig,
        keyring: updateKeyring,
        tempRoot: getInstallRoot(),
        onStateChanged: broadcastUpdateState,
        prepareInstall: prepareBackendForUpdate,
        recoverInstall: recoverBackendAfterFailedUpdate,
        launchInstaller: launchVerifiedInstaller,
      })
      console.log(`[electron] startup packaged=${app.isPackaged} icon=${APP_ICON} exists=${fs.existsSync(APP_ICON)}`)
      const preparedStorage = await prepareStorageConfiguration()
      await ensureBackend()
      if (preparedStorage.config) {
        await verifyBackendStorage(preparedStorage.config)
      }
      await verifyBackendReleaseIdentity()
      applyStartupUpdateResult()
      await persistPreparedStorage(preparedStorage)
      await createWindow()
    } catch (error) {
      dialog.showErrorBox('启动失败', String(error?.message || error))
      killBackend()
      app.quit()
    }
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && !IS_SMOKE_TEST) {
      createWindow().catch((error) => {
        dialog.showErrorBox('页面加载失败', String(error?.message || error))
      })
    }
  })
}


app.on('before-quit', () => {
  killBackend()
})

app.on('window-all-closed', () => {
  killBackend()
  app.quit()
})

process.on('exit', () => {
  killBackend()
})
