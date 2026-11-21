import http from './index'

export function getHealth() {
  return http.get('/api/health')
}
