import axios from 'axios'

/**
 * Axios instance for the backend.
 * - Base URL: same origin, /api/v1 (VITE_API_BASE_URL may only change the path). The API is served
 *   under the frontend's own origin — the Vite dev server proxies /api in development, the Render
 *   static site rewrites /api/* to the backend in production — so the refresh-token cookie
 *   (HttpOnly, SameSite=Strict, Path=/api/v1/auth) is first-party and sent automatically.
 * - Timeout: VITE_API_TIMEOUT_MS (default 10 s; raise it for hosts that cold-start).
 * - Request: adds "Authorization: Bearer <token>" when signed in.
 * - Response: 401 TOKEN_EXPIRED → one silent refresh (POST /auth/refresh with the cookie), then the
 *   original request is sent again; any other 401, or a failed refresh, calls onUnauthorized().
 *   A 403 PASSWORD_CHANGE_REQUIRED (first sign-in) calls onPasswordChangeRequired().
 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: Number(import.meta.env.VITE_API_TIMEOUT_MS) || 10000,
  headers: { Accept: 'application/json' },
})

export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

/**
 * Mock data and handlers (mock mode only). In API builds (VITE_USE_MOCK=false) the condition
 * is the constant `false` at build time, so this import is removed and no mock code or
 * synthetic data is shipped.
 */
export const loadMock = () =>
  import.meta.env.VITE_USE_MOCK !== 'false'
    ? import('@/mock/api')
    : Promise.reject(new Error('Mock data is disabled (VITE_USE_MOCK=false)'))

let getToken = () => null
let unauthorizedHandler = null
let passwordChangeHandler = null
let refresher = null
let inflight = null

/** Called once from main.js so this module does not import the store or router. */
export function configureAuth({ tokenGetter, onUnauthorized, onPasswordChangeRequired = null, refresh = null }) {
  getToken = tokenGetter
  unauthorizedHandler = onUnauthorized
  passwordChangeHandler = onPasswordChangeRequired
  refresher = refresh
}

/** One refresh at a time: concurrent expired requests wait for the same refresh. → true when refreshed. */
export function refreshAccess() {
  if (!refresher) return Promise.resolve(false)
  inflight ??= Promise.resolve(refresher()).catch(() => false).finally(() => { inflight = null })
  return inflight
}

/** Signed-out / expired session detected outside axios (mock mode): same handling as a 401. */
export function notifyUnauthorized(code) {
  unauthorizedHandler?.(code)
}

const AUTH_ENDPOINTS = ['/auth/login', '/auth/logout', '/auth/refresh']

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config ?? {}
    const code = error.response?.data?.error?.code
    const isAuthEndpoint = AUTH_ENDPOINTS.includes(config.url) // never refresh / loop on these
    if (error.response?.status === 401 && !isAuthEndpoint) {
      if (code === 'TOKEN_EXPIRED' && !config._retried && (await refreshAccess())) {
        config._retried = true
        config.headers = { ...config.headers, Authorization: `Bearer ${getToken()}` }
        return api(config) // the original request continues with the new access token
      }
      unauthorizedHandler?.(code)
    }
    if (error.response?.status === 403 && error.response.data?.error?.code === 'PASSWORD_CHANGE_REQUIRED' && passwordChangeHandler) {
      passwordChangeHandler()
    }
    return Promise.reject(error)
  },
)

/** Human-readable message from an API error (api-design.md §1.2 envelope). */
export function apiErrorMessage(error, fallback = '發生錯誤，請稍後再試') {
  if (!error.response) return '無法連線到伺服器，請確認網路或稍後再試'
  return error.response.data?.error?.message ?? fallback
}

/**
 * { status, code, message, details } from an API error or a mock-mode error (MockApiError
 * carries the same fields), so views handle 403 / 404 / 409 / validation the same way.
 */
export function toApiError(err) {
  if (err?.response) {
    const e = err.response.data?.error
    return { status: err.response.status, code: e?.code ?? 'UNKNOWN', message: apiErrorMessage(err), details: e?.details ?? [] }
  }
  if (err?.status) return { status: err.status, code: err.code, message: err.message, details: err.details ?? [] }
  return { status: 0, code: 'NETWORK', message: '無法連線到伺服器，請確認網路或稍後再試', details: [] }
}
