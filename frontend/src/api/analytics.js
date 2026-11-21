import http from './index'

export function getAnalytics({ keyword = '', signal } = {}) {
  return http.get('/api/analytics', {
    params: { keyword },
    signal,
  })
}
