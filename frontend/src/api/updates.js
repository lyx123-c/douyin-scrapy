const bridge = typeof window !== 'undefined' ? window.desktopRuntime?.updates || null : null

export const updateBridgeAvailable = Boolean(bridge)

function requireBridge() {
  if (!bridge) throw new Error('桌面端版本更新接口不可用。')
  return bridge
}

export function getLocalVersionInfo() {
  return requireBridge().getLocalInfo()
}

export function getUpdateState() {
  return requireBridge().getState()
}

export function revealUpdateInstaller() {
  return requireBridge().revealInstaller()
}

export function checkForUpdates() {
  return requireBridge().check()
}

export function startUpdateDownload() {
  return requireBridge().startDownload()
}

export function cancelUpdateDownload() {
  return requireBridge().cancelDownload()
}

export function quitAndInstallUpdate() {
  return requireBridge().quitAndInstall()
}

export function onUpdateStateChanged(callback) {
  return requireBridge().onStateChanged(callback)
}

export default {
  cancelUpdateDownload,
  checkForUpdates,
  getLocalVersionInfo,
  getUpdateState,
  onUpdateStateChanged,
  revealUpdateInstaller,
  quitAndInstallUpdate,
  startUpdateDownload,
  updateBridgeAvailable,
}
