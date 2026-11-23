import http from './index'

const bridge = typeof window !== 'undefined' ? window.desktopRuntime?.storage || null : null

export const storageControlsAvailable = Boolean(bridge)

export function getManagedStorageStatus() {
  return bridge ? bridge.getStatus() : Promise.resolve(null)
}

export function chooseManagedVideoDirectory() {
  if (!bridge) return Promise.reject(new Error('桌面端存储管理不可用。'))
  return bridge.chooseVideoDirectory()
}

export function chooseManagedDataDirectory() {
  if (!bridge) return Promise.reject(new Error('桌面端存储管理不可用。'))
  return bridge.chooseDataDirectory()
}

export function openManagedDirectory(target) {
  if (!bridge) return Promise.reject(new Error('桌面端存储管理不可用。'))
  return bridge.openDirectory(target)
}

export function getBackendStoragePaths() {
  return http.get('/api/system/storage')
}

export default {
  getBackendStoragePaths,
  getManagedStorageStatus,
  openManagedDirectory,
  storageControlsAvailable,
  chooseManagedDataDirectory,
  chooseManagedVideoDirectory,
}
