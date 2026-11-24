import http from './index'

export function getDownloads({ signal } = {}) {
  return http.get('/api/downloads', { signal })
}

export function getDownloadStatus({ signal } = {}) {
  return http.get('/api/downloads/status', { signal })
}

export function startDownloads() {
  return http.post('/api/downloads/start')
}

export function stopDownloads() {
  return http.post('/api/downloads/stop')
}

export function addDownloadTasks(items) {
  return http.post('/api/downloads/tasks', { items })
}

export function immediateDownload({ videoId, title = '无标题', force = false }) {
  return http.post('/api/downloads/immediate', {
    video_id: videoId,
    title,
    force,
  })
}

export function getDownloadTask(taskId) {
  return http.get(`/api/downloads/tasks/${encodeURIComponent(taskId)}`)
}

export function retryDownloadTask(taskId) {
  return http.post(`/api/downloads/tasks/${encodeURIComponent(taskId)}/retry`)
}

export function cancelDownloadTask(taskId) {
  return http.post(`/api/downloads/tasks/${encodeURIComponent(taskId)}/cancel`)
}

export function deleteDownloadTask(taskId) {
  return http.delete(`/api/downloads/tasks/${encodeURIComponent(taskId)}`)
}

export function clearCompletedDownloads() {
  return http.post('/api/downloads/tasks/clear-completed')
}
