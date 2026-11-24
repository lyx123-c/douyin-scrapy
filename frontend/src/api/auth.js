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

export function resetAuth() {
  return http.post('/api/auth/reset')
}

export function isAuthRequiredError(error) {
  return Number(error?.statusCode) === 401
}

export function isAuthStatusUnknownError(error) {
  return Number(error?.statusCode) === 503 && error?.code === 'LOGIN_STATUS_UNKNOWN'
}

export function isLoginBrowserClosedError(error) {
  const message = String(error?.message || '').toLowerCase()
  return [
    '登录窗口已关闭',
    '登录浏览器已关闭',
    'target page, context or browser has been closed',
    'target closed',
    'browser has been closed',
    'browser closed',
  ].some((marker) => message.includes(marker))
}
