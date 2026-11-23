const { contextBridge, ipcRenderer } = require('electron')

function resolveBackendApiBaseUrl() {
  const prefix = '--douyin-backend-url='
  const argument = process.argv.find((value) => value.startsWith(prefix))
  if (!argument) return ''

  try {
    const value = argument.slice(prefix.length).replace(/\/+$/, '')
    const url = new URL(value)
    if (
      url.protocol !== 'http:'
      || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    ) {
      return ''
    }
    return url.toString().replace(/\/+$/, '')
  } catch {
    return ''
  }
}

const backendApiBaseUrl = resolveBackendApiBaseUrl()

contextBridge.exposeInMainWorld(
  'desktopRuntime',
  Object.freeze({
    apiBaseUrl: backendApiBaseUrl,
    storage: Object.freeze({
      getStatus: () => ipcRenderer.invoke('storage:get-status'),
      chooseVideoDirectory: () => ipcRenderer.invoke('storage:choose-video-directory'),
      chooseDataDirectory: () => ipcRenderer.invoke('storage:choose-data-directory'),
      openDirectory: (target) => ipcRenderer.invoke('storage:open-directory', target),
    }),
    updates: Object.freeze({
      getLocalInfo: () => ipcRenderer.invoke('updates:get-local-info'),
      getState: () => ipcRenderer.invoke('updates:get-state'),
      check: () => ipcRenderer.invoke('updates:check'),
      startDownload: () => ipcRenderer.invoke('updates:start-download'),
      cancelDownload: () => ipcRenderer.invoke('updates:cancel-download'),
      quitAndInstall: () => ipcRenderer.invoke('updates:quit-and-install'),
      onStateChanged: (callback) => {
        if (typeof callback !== 'function') throw new TypeError('Update state callback must be a function.')
        const listener = (_event, state) => callback(state)
        ipcRenderer.on('updates:state-changed', listener)
        return () => ipcRenderer.removeListener('updates:state-changed', listener)
      },
    }),
    platform: process.platform,
    electron: process.versions.electron,
    chrome: process.versions.chrome,
  }),
)
