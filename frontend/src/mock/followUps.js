/**
 * Mock 護理師追蹤管理 (Phase 1c, VITE_USE_MOCK=true) — same shapes and rules as backend app/modules/follow_up:
 * open → completed | cancelled (final), one open follow-up per record, optimistic lock (version), Idempotency-Key
 * replay, completion = a new signed nursing assessment (mock nursing store), chain to the next follow-up,
 * append-only events, overdue computed, nurse access = assigned patients (404 otherwise), patients read only.
 */
import { nowIso } from '@/mock/clock'
import { recordReviewAssessment } from '@/mock/nursing'
import { symptomRecordStore } from '@/mock/nurseReview'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockPerson } from '@/mock/patients'

const KEY = 'ccp.mock.followUps'
const NOTICE = '本功能為照護追蹤紀錄，並非即時監測或緊急通報系統。如有急性或嚴重不適，請依醫院指示聯繫醫療團隊；緊急情況請撥打 119。'
const DAY = 86400000
function load() {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* storage unavailable */ }
  return { items: [], events: [], keys: {} }
}
const state = load()
const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(state)) } catch { /* storage unavailable */ } }

const err = (status, code, message, details = []) => new MockApiError(status, code, message, details)
const notFound = () => err(404, 'NOT_FOUND', 'Follow-up not found')
const taipeiDay = (iso) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date(iso))
const recordById = (id) => Object.values(symptomRecordStore).flat().find((r) => r.id === id) ?? null
const isOverdue = (f) => f.status === 'open' && Date.parse(f.due_at) < Date.parse(nowIso())

function canSee(patientId) {
  try {
    mockAccessPatient(patientId, 'nurse')
    return true
  } catch {
    return false
  }
}

function parseDue(raw, field, details) {
  if (typeof raw !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(raw) || Number.isNaN(Date.parse(raw))) {
    details.push({ field, issue: 'must be an ISO 8601 datetime with timezone' })
    return null
  }
  const at = Date.parse(raw)
  const startOfToday = Date.parse(`${taipeiDay(nowIso())}T00:00:00+08:00`)
  if (at < startOfToday) { details.push({ field, issue: 'cannot be before today' }); return null }
  if (at > Date.parse(nowIso()) + 180 * DAY) { details.push({ field, issue: 'cannot be more than 180 days ahead' }); return null }
  return new Date(at).toISOString()
}
const textOf = (v, field, max, details) => {
  if (typeof v !== 'string' || !v.trim() || v.length > max) { details.push({ field, issue: `is required (1–${max} characters)` }); return null }
  return v.trim()
}
const invalid = (details) => { if (details.length) throw err(400, 'VALIDATION_ERROR', '追蹤資料有誤', details) }

function payload(f, detail = false) {
  const p = mockAccessPatientSafe(f.patient_id)
  const r = recordById(f.record_id)
  const data = {
    id: f.id, status: f.status, overdue: isOverdue(f), due_at: f.due_at, due_local_date: taipeiDay(f.due_at), reason: f.reason, version: f.version,
    patient: { id: f.patient_id, patient_code: p?.patient_code ?? null, display_name: p?.display_name ?? null },
    symptom_record: { id: f.record_id, recorded_at: r?.recorded_at ?? null, form_code: r?.form?.code ?? null },
    created_by: mockPerson(f.created_by), created_at: f.created_at, updated_at: f.updated_at,
    completed_by: f.completed_by ? mockPerson(f.completed_by) : null, completed_at: f.completed_at,
    cancelled_by: f.cancelled_by ? mockPerson(f.cancelled_by) : null, cancelled_at: f.cancelled_at, cancellation_reason: f.cancellation_reason,
    previous_follow_up_id: f.previous_id, next_follow_up_ids: state.items.filter((x) => x.previous_id === f.id).map((x) => x.id),
  }
  if (detail) {
    data.completion = f.completion ?? null
    data.review_assessment_id = f.review_assessment_id
  }
  return data
}
function mockAccessPatientSafe(id) {
  try { return mockAccessPatient(id) } catch { return null }
}

function event(f, type, actor, { oldDue = null, newDue = null, reason = null, assessmentId = null, related = null } = {}) {
  state.events.push({ follow_up_id: f.id, event_type: type, occurred_at: nowIso(), actor, old_due_at: oldDue, new_due_at: newDue, reason,
    related_assessment_id: assessmentId, related_follow_up_id: related })
}

function idempotent(key, body, run) {
  const user = mockCurrentUser('nurse')
  if (!key) throw err(428, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header is required')
  const k = `${user.id}:${key}`
  const sig = JSON.stringify(body)
  const seen = state.keys[k]
  if (seen) {
    if (seen.sig !== sig) throw err(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    if (seen.error) throw err(seen.error.status, seen.error.code, seen.error.message)
    return { data: payload(state.items.find((x) => x.id === seen.id), true) }
  }
  try {
    const result = run(user)
    state.keys[k] = { sig, id: result.data.id }
    save()
    return result
  } catch (e) {
    if (e.status && e.status < 500) { state.keys[k] = { sig, error: { status: e.status, code: e.code, message: e.message } }; save() }
    throw e
  }
}

/** Create (from the review or afterwards). `record` = the mock symptom record (reviewed). */
export function createMockFollowUp(record, userId, dueAt, reason, assessmentId, previousId = null) {
  if (record.record_status !== 'final') throw err(422, 'INVALID_STATE', '只有有效的症狀紀錄可以建立追蹤')
  if (record.review_status !== 'reviewed' || !assessmentId) throw err(409, 'RECORD_NOT_REVIEWED', '請先完成審閱，再建立追蹤')
  const open = state.items.find((x) => x.record_id === record.id && x.status === 'open')
  if (open) throw err(409, 'FOLLOW_UP_EXISTS', '這筆症狀紀錄已有進行中的追蹤', [{ field: 'symptom_record_id', issue: 'an open follow-up exists', follow_up_id: open.id }])
  const now = nowIso()
  const f = { id: crypto.randomUUID(), patient_id: record.patient_id, record_id: record.id, review_assessment_id: assessmentId, due_at: dueAt, reason,
    status: 'open', version: 1, created_by: userId, created_at: now, updated_at: now, completed_by: null, completed_at: null, completion: null,
    cancelled_by: null, cancelled_at: null, cancellation_reason: null, previous_id: previousId }
  state.items.push(f)
  event(f, 'created', mockPerson(userId), { newDue: dueAt, reason, assessmentId, related: previousId })
  save()
  return f
}

/** Review payload → follow-up spec (= review_record): `follow_up {due_at, reason}` or Phase 1a `next_follow_up_at`. */
export function reviewFollowUpSpec(payload) {
  const details = []
  if (payload.follow_up != null) {
    const due = parseDue(payload.follow_up.due_at, 'follow_up.due_at', details)
    const reason = textOf(payload.follow_up.reason, 'reason', 1000, details)
    invalid(details)
    return { due, reason }
  }
  if (payload.next_follow_up_at) return { due: new Date(payload.next_follow_up_at).toISOString(), reason: (payload.action_note ?? '').trim().slice(0, 1000) }
  return null
}

/** Summary on a record for nurses (= record_summary). */
export function mockRecordFollowUp(recordId) {
  const rows = state.items.filter((x) => x.record_id === recordId).sort((a, b) => (a.status === 'open' ? -1 : 0) - (b.status === 'open' ? -1 : 0) || b.created_at.localeCompare(a.created_at))
  const f = rows[0]
  return f ? { id: f.id, status: f.status, due_at: f.due_at, overdue: isOverdue(f), version: f.version } : null
}
export const mockRecordHasOpenFollowUp = (recordId) => state.items.some((x) => x.record_id === recordId && x.status === 'open')
export const mockRecordHasAnyFollowUp = (recordId) => state.items.some((x) => x.record_id === recordId)

const SORTS = {
  due_at: (a, b) => a.due_at.localeCompare(b.due_at),
  '-due_at': (a, b) => b.due_at.localeCompare(a.due_at),
  '-updated_at': (a, b) => b.updated_at.localeCompare(a.updated_at),
}
/** GET /care-follow-ups */
export function mockListFollowUps({ status = 'open', patientId = null, sort = 'due_at', page = 1, perPage = 20 } = {}) {
  mockCurrentUser('nurse')
  if (patientId) mockAccessPatient(patientId, 'nurse')
  const by = { open: (f) => f.status === 'open', overdue: isOverdue, completed: (f) => f.status === 'completed', cancelled: (f) => f.status === 'cancelled', all: () => true }
  if (!by[status] || !SORTS[sort]) throw err(400, 'VALIDATION_ERROR', '查詢條件有誤')
  const base = state.items.filter((f) => canSee(f.patient_id) && (!patientId || f.patient_id === patientId))
  const rows = base.filter(by[status]).sort(SORTS[sort])
  return {
    data: rows.slice((page - 1) * perPage, page * perPage).map((f) => payload(f)),
    meta: { page, per_page: perPage, total: rows.length, counts: Object.fromEntries(Object.entries(by).map(([k, fn]) => [k, base.filter(fn).length])) },
  }
}

function loadOwn(id) {
  mockCurrentUser('nurse')
  const f = state.items.find((x) => x.id === id)
  if (!f || !canSee(f.patient_id)) throw notFound()
  return f
}
export const mockFollowUp = (id) => ({ data: payload(loadOwn(id), true) })
export function mockFollowUpEvents(id) {
  const f = loadOwn(id)
  return { data: state.events.filter((e) => e.follow_up_id === id).map(({ follow_up_id, ...e }) => e) }
}

/** POST /care-follow-ups {symptom_record_id, due_at, reason} */
export function mockCreateFollowUp(body, key) {
  mockCurrentUser('nurse')
  const record = recordById(body.symptom_record_id)
  if (!record || !canSee(record.patient_id)) throw err(404, 'NOT_FOUND', 'Symptom record not found')
  const details = []
  const due = parseDue(body.due_at, 'due_at', details)
  const reason = textOf(body.reason, 'reason', 1000, details)
  invalid(details)
  return idempotent(key, body, (user) => ({ data: payload(createMockFollowUp(record, user.id, due, reason, record.review?.nursing_assessment_id), true) }))
}

function transition(f, version, changes) {
  if (f.status !== 'open') throw err(409, 'FOLLOW_UP_CLOSED', '這筆追蹤已經結案或取消，不能再修改', [{ field: 'status', issue: f.status }])
  if (f.version !== version) throw err(409, 'VERSION_CONFLICT', '這筆追蹤剛被其他人更新，請重新載入後再操作', [{ field: 'version', issue: `current version is ${f.version}` }])
  Object.assign(f, changes, { version: f.version + 1, updated_at: nowIso() })
}
const versionOf = (body, details) => (Number.isInteger(body.version) && body.version >= 1 ? body.version : (details.push({ field: 'version', issue: 'is required' }), null))

export function mockRescheduleFollowUp(id, body, key) {
  const f = loadOwn(id)
  const details = []
  const due = parseDue(body.due_at, 'due_at', details)
  const reason = textOf(body.reason, 'reason', 1000, details)
  const version = versionOf(body, details)
  invalid(details)
  return idempotent(key, body, (user) => {
    const old = f.due_at
    transition(f, version, { due_at: due })
    event(f, 'rescheduled', mockPerson(user.id), { oldDue: old, newDue: due, reason })
    return { data: payload(f, true) }
  })
}

export function mockCompleteFollowUp(id, body, key) {
  const f = loadOwn(id)
  const details = []
  const result = textOf(body.result, 'result', 2000, details)
  const actions = textOf(body.actions, 'actions', 2000, details)
  const version = versionOf(body, details)
  const type = body.assessment_type ?? 'phone_follow_up'
  if (!['phone_follow_up', 'follow_up'].includes(type)) details.push({ field: 'assessment_type', issue: 'is not valid' })
  let next = null
  if (body.next_follow_up != null) next = { due: parseDue(body.next_follow_up.due_at, 'next_follow_up.due_at', details), reason: textOf(body.next_follow_up.reason, 'reason', 1000, details) }
  invalid(details)
  return idempotent(key, body, (user) => {
    if (f.status !== 'open') throw err(409, 'FOLLOW_UP_CLOSED', '這筆追蹤已經結案或取消，不能再修改')
    if (f.version !== version) throw err(409, 'VERSION_CONFLICT', '這筆追蹤剛被其他人更新，請重新載入後再操作')
    const assessmentId = recordReviewAssessment(f.patient_id, user.id, { assessmentType: type, riskLevel: null, subjective: `追蹤：${f.reason}`, plan: actions })
    const now = nowIso()
    transition(f, version, { status: 'completed', completed_by: user.id, completed_at: now,
      completion: { nursing_assessment_id: assessmentId, assessment_type: type, result, actions, sign_status: 'signed', signed_at: now } })
    event(f, 'completed', mockPerson(user.id), { oldDue: f.due_at, reason: result, assessmentId })
    const data = payload(f, true)
    if (next) {
      const n = createMockFollowUp(recordById(f.record_id), user.id, next.due, next.reason, assessmentId, f.id)
      data.next_follow_up = payload(n)
      data.next_follow_up_ids = [n.id]
    }
    return { data }
  })
}

export function mockCancelFollowUp(id, body, key) {
  const f = loadOwn(id)
  const details = []
  const reason = textOf(body.reason, 'reason', 1000, details)
  const version = versionOf(body, details)
  invalid(details)
  return idempotent(key, body, (user) => {
    transition(f, version, { status: 'cancelled', cancelled_by: user.id, cancelled_at: nowIso(), cancellation_reason: reason })
    event(f, 'cancelled', mockPerson(user.id), { oldDue: f.due_at, reason })
    return { data: payload(f, true) }
  })
}

/** GET /patients/me/care-follow-ups — read only, minimal (no reasons, notes or names). */
export function mockMyFollowUps() {
  const pid = mockAccessPatient('me', 'patient').id
  const data = state.items.filter((f) => f.patient_id === pid).sort((a, b) => b.due_at.localeCompare(a.due_at)).map((f) => ({
    id: f.id, status: f.status, due_local_date: taipeiDay(f.due_at),
    symptom_record: { id: f.record_id, recorded_at: recordById(f.record_id)?.recorded_at ?? null }, completed_at: f.completed_at, cancelled_at: f.cancelled_at,
  }))
  return { data, meta: { notice: NOTICE } }
}
