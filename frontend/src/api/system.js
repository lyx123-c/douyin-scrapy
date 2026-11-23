import http from './index'

export function getHealth() {
  return http.get('/api/health')
}

export function getStoragePaths() {
  return http.get('/api/system/storage')
}
