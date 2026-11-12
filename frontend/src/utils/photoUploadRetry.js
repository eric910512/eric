/**
 * What a failed photo upload means for the next attempt (SkinPhotoPicker.vue). No imports: unit-tested with
 * `node --test "tests/unit/*.test.mjs"` (frontend/).
 *
 * The API keeps one answer per Idempotency-Key (backend app/core/idempotency.py): the same key returns the
 * photo it created, 409 IDEMPOTENCY_IN_PROGRESS while the first try still runs, or — for an error raised by the
 * photo API itself — that same error again. So:
 *
 * - `unknown`: no answer (network, timeout) or an answer that did not come from the API (a proxy's 502 / 504,
 *   an HTML page), or the first try is still running. The photo MAY have been created. Keep the key: sending
 *   it again is the safe way to find out (the API answers with the created photo, or with the definite result).
 * - `not_created`: the API itself said that this attempt stored nothing and that trying later can work
 *   (503 PHOTO_STORAGE_UNAVAILABLE, 429 RATE_LIMITED). That key would only ever repeat this answer: the next
 *   attempt uses a NEW key. (A duplicate is still impossible: the API returns the already pending copy of the
 *   same picture instead of storing it twice.)
 * - `rejected`: the request itself is not acceptable (400, 401, 403, 404, 413, 415, 422 …, or photos switched
 *   off). Not a temporary failure: no retry — the patient removes or chooses the photo again.
 */
export const NOT_CREATED_RETRYABLE = new Set(['PHOTO_STORAGE_UNAVAILABLE', 'RATE_LIMITED'])

export const UNKNOWN_RESULT_MESSAGE = '網路不穩定，無法確認照片是否已上傳。按「重試」會先確認，不會重複上傳。'

/** @param err `{ status, code, message }` (api/client.js toApiError) → `{ kind, retry, newKey, message }` */
export function classifyUploadFailure(err) {
  const status = Number(err?.status) || 0
  const code = err?.code ?? 'UNKNOWN'
  const unknown = { kind: 'unknown', retry: true, newKey: false, message: UNKNOWN_RESULT_MESSAGE }
  if (!status || code === 'NETWORK') return unknown
  if (status === 409 && code === 'IDEMPOTENCY_IN_PROGRESS') return unknown
  if (NOT_CREATED_RETRYABLE.has(code) && (status === 503 || status === 429)) {
    return { kind: 'not_created', retry: true, newKey: true, message: err.message || '照片暫時無法儲存，請稍後再試' }
  }
  const rejected = { kind: 'rejected', retry: false, newKey: true, message: err.message || '這張照片無法上傳' }
  if (code === 'PHOTO_UPLOADS_DISABLED') return rejected // photos are switched off: retrying cannot help
  // any other 5xx is not one of the API's definite answers (502 / 504 from the proxy, an unexpected 500): the
  // result is not known. The API lets the same key run again after an unexpected 500 and replays a created photo.
  if (status >= 500) return unknown
  return rejected
}
