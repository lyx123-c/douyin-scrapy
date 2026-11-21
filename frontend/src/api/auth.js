import http from './index'

const LOGIN_TIMEOUT_MS = 150000

export function getAuthStatus({ signal } = {}) {
  return http.get('/api/auth/status', { signal })
}

export function login() {
  return http.post('/api/auth/login', undefined, {
    timeout: LOGIN_TIMEOUT_MS,
  })
}

export function isAuthRequiredError(error) {
  return Number(error?.statusCode) === 401
}

export function isAuthStatusUnknownError(error) {
  return Number(error?.statusCode) === 503 && error?.code === 'LOGIN_STATUS_UNKNOWN'
}
