import http from './index'

export function getCaptureStatus({ signal } = {}) {
  return http.get('/api/capture/status', { signal })
}

export function startCapture({ targetUrl, mode }) {
  return http.post('/api/capture/start', {
    target_url: targetUrl,
    mode,
  })
}

export function stopCapture() {
  return http.post('/api/capture/stop')
}

export function getCaptureResults({ signal } = {}) {
  return http.get('/api/capture/results', { signal })
}

export function clearCapture() {
  return http.post('/api/capture/clear')
}
