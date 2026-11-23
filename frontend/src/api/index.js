import axios from 'axios'

export class ApiError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = options.statusCode ?? null
    this.code = options.code ?? 'API_ERROR'
    this.details = options.details ?? null
  }
}

const runtimeApiBaseUrl = String(
  typeof window !== 'undefined' ? window.desktopRuntime?.apiBaseUrl || '' : '',
).trim()
const environmentApiBaseUrl = String(import.meta.env.VITE_API_BASE_URL || '').trim()
const developmentApiBaseUrl = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''

export const usingDesktopApiBridge = Boolean(runtimeApiBaseUrl)
export const configuredApiBaseUrl = (
  runtimeApiBaseUrl || environmentApiBaseUrl || developmentApiBaseUrl
).replace(/\/+$/, '')

const requestBaseUrl = import.meta.env.DEV ? '/backend' : configuredApiBaseUrl

const http = axios.create({
  baseURL: requestBaseUrl,
  timeout: 10000,
  headers: {
    Accept: 'application/json',
  },
})

function normalizeError(error) {
  if (error instanceof ApiError) {
    return error
  }

  if (error.code === 'ERR_CANCELED') {
    return new ApiError('请求已取消。', {
      code: 'REQUEST_CANCELED',
    })
  }

  const statusCode = error.response?.status ?? null
  const payload = error.response?.data
  const details = payload?.data ?? null
  const code = details?.code || error.code || 'API_ERROR'

  if (error.code === 'ECONNABORTED') {
    return new ApiError('FastAPI 请求超时，请确认服务状态。', {
      statusCode,
      code: 'REQUEST_TIMEOUT',
      details,
    })
  }

  if (!error.response) {
    const message = configuredApiBaseUrl
      ? `无法连接 FastAPI：${configuredApiBaseUrl}`
      : 'Electron 未提供有效的 Backend API 地址。'
    return new ApiError(message, {
      code: 'NETWORK_ERROR',
    })
  }

  return new ApiError(
    payload?.message || `FastAPI 请求失败（HTTP ${statusCode}）。`,
    {
      statusCode,
      code,
      details,
    },
  )
}

http.interceptors.response.use(
  (response) => {
    const payload = response.data
    if (!payload || payload.success !== true) {
      throw new ApiError(payload?.message || 'FastAPI 返回了无法识别的响应。', {
        statusCode: response.status,
        code: payload?.data?.code || 'INVALID_RESPONSE',
        details: payload?.data ?? null,
      })
    }
    return payload
  },
  (error) => Promise.reject(normalizeError(error)),
)

export default http
