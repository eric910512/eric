// Photo upload retry decisions (src/utils/photoUploadRetry.js). Run from frontend/: node --test "tests/unit/*.test.mjs"
import assert from 'node:assert/strict'
import test from 'node:test'

import { classifyUploadFailure, UNKNOWN_RESULT_MESSAGE } from '../../src/utils/photoUploadRetry.js'

const api = (status, code, message = `API ${code}`) => ({ status, code, message, details: [] })

test('503 PHOTO_STORAGE_UNAVAILABLE from the API: nothing stored → retry with a NEW key, server message shown', () => {
  const o = classifyUploadFailure(api(503, 'PHOTO_STORAGE_UNAVAILABLE', '照片暫時無法儲存，請稍後再試'))
  assert.deepEqual(o, { kind: 'not_created', retry: true, newKey: true, message: '照片暫時無法儲存，請稍後再試' })
})

test('429 RATE_LIMITED: nothing stored, temporary → retry later with a new key', () => {
  assert.deepEqual(classifyUploadFailure(api(429, 'RATE_LIMITED')), { kind: 'not_created', retry: true, newKey: true, message: 'API RATE_LIMITED' })
})

for (const [label, err] of [
  ['network failure (toApiError NETWORK, status 0)', { status: 0, code: 'NETWORK', message: '無法連線到伺服器' }],
  ['request timeout (axios → NETWORK)', { status: 0, code: 'NETWORK' }],
  ['no status at all', { code: 'ECONNABORTED' }],
  ['undefined error', undefined],
  ['502 from the proxy (no API envelope → UNKNOWN)', api(502, 'UNKNOWN')],
  ['504 gateway timeout from the proxy', api(504, 'UNKNOWN')],
  ['503 without the API code (proxy / platform)', api(503, 'UNKNOWN')],
  ['unexpected 500 INTERNAL_ERROR from the API', api(500, 'INTERNAL_ERROR')],
  ['409 IDEMPOTENCY_IN_PROGRESS (the first try is still running)', api(409, 'IDEMPOTENCY_IN_PROGRESS')],
]) {
  test(`unknown result — ${label}: retry with the SAME key (may already be created)`, () => {
    const o = classifyUploadFailure(err)
    assert.equal(o.kind, 'unknown')
    assert.equal(o.retry, true)
    assert.equal(o.newKey, false)
    assert.equal(o.message, UNKNOWN_RESULT_MESSAGE)
  })
}

for (const [status, code] of [
  [400, 'VALIDATION_ERROR'], [401, 'TOKEN_INVALID'], [401, 'UNAUTHORIZED'], [403, 'PHOTO_CONSENT_REQUIRED'], [403, 'FORBIDDEN'],
  [404, 'NOT_FOUND'], [409, 'CONSENT_VERSION_MISMATCH'], [413, 'PHOTO_TOO_LARGE'], [415, 'PHOTO_UNSUPPORTED_TYPE'],
  [422, 'IDEMPOTENCY_KEY_MISMATCH'], [428, 'IDEMPOTENCY_KEY_REQUIRED'], [503, 'PHOTO_UPLOADS_DISABLED'],
]) {
  test(`${status} ${code}: rejected, not a temporary failure → no retry`, () => {
    const o = classifyUploadFailure(api(status, code, `訊息 ${code}`))
    assert.deepEqual(o, { kind: 'rejected', retry: false, newKey: true, message: `訊息 ${code}` })
  })
}

test('PHOTO_STORAGE_UNAVAILABLE with an unexpected status is not trusted as "not created"', () => {
  assert.equal(classifyUploadFailure(api(200, 'PHOTO_STORAGE_UNAVAILABLE')).kind, 'rejected')
  assert.equal(classifyUploadFailure(api(502, 'PHOTO_STORAGE_UNAVAILABLE')).kind, 'unknown')
})
