import { api, loadMock, notifyUnauthorized, refreshAccess, toApiError, USE_MOCK } from '@/api/client'

const delay = (ms = 250) => new Promise((r) => setTimeout(r, ms))

/**
 * One request: the API, or in mock mode the handler of the same name in `@/mock/api` (same
 * envelope, same errors, same rules). Returns the response body ({ data, meta? }); a 204
 * returns null. Throws { status, code, message, details } (toApiError).
 */
export async function call(mockName, mockArgs, request) {
  try {
    if (USE_MOCK) {
      await delay()
      const mock = await loadMock()
      try {
        return structuredClone(mock[mockName](...mockArgs) ?? null)
      } catch (err) {
        // same as the API interceptor: an expired access token is refreshed once and the call
        // repeated; any other 401 (session ended, account disabled) signs out
        if (err?.status === 401 && err.code === 'TOKEN_EXPIRED' && (await refreshAccess())) {
          return structuredClone(mock[mockName](...mockArgs) ?? null)
        }
        if (err?.status === 401) notifyUnauthorized(err.code)
        throw err
      }
    }
    const res = await request()
    return res.status === 204 ? null : res.data
  } catch (err) {
    throw toApiError(err)
  }
}

export { api }
