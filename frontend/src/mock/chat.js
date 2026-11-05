/**
 * Mock 病人與護理師聊天室 (VITE_USE_MOCK=true), in the shapes of /api/v1/patients/{id}/messages…,
 * /api/v1/chat/unread-count and /api/v1/chat/inbox, with the backend rules (app/modules/chat):
 * - access: the patient (me / own id) and currently assigned nurses; others 404; admin 403
 * - the patient's first message of an Asia/Taipei day is followed by one system auto reply
 * - Idempotency-Key required; the same key returns the stored message (different body → 422)
 * - read pointers per user; patient unread = nurse messages, nurse unread = patient messages
 * State survives a reload (sessionStorage).
 */
import { nowIso } from '@/mock/clock'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockListPatients, mockPerson } from '@/mock/patients'

const STATE_KEY = 'ccp.mock.chat'
const TZ = 'Asia/Taipei'
const MAX_BODY = 1000
const PREVIEW = 60
const RATE_COUNT = 20
const RATE_WINDOW_MS = 60_000
const SERVICE = {
  name: '義大醫院放射腫瘤科',
  hours: [['09:00', '12:30'], ['13:30', '17:30']],
  hours_text: '星期一至五 09:00-12:30、13:30-17:30',
  phone: '07-6150011',
  extension: '2205',
  phone_text: '07-6150011 分機 2205',
}
const NURSE_LABEL = '放射腫瘤科護理師'
const SYSTEM_LABEL = '系統自動回覆'
export const AUTO_REPLY_TEXT = '您好！這裡是義大醫院放射腫瘤科\n'
  + '小幫手將於上班時間星期一至五，早上 09:00-12:30，以及下午 13:30-17:30，依序回覆訊息，'
  + '若您有放射線治療照護上的問題皆可詢問，若您的問題無法透過訊息解決，請您於上班時間至本科掛號'
  + '或是撥打電話 07-6150011 分機 2205，至放射腫瘤科門診，由醫師看診評估，謝謝您'

const invalid = (field, issue, message = '訊息內容有誤') => new MockApiError(400, 'VALIDATION_ERROR', message, [{ field, issue }])

function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.messages) return saved
  } catch {
    // storage unavailable: start empty
  }
  return { messages: [], reads: {}, keys: {}, nextId: 0 }
}
const state = load()
function save() {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage full / unavailable: keep in memory
  }
}

const dayFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ })
const partsFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
function inServiceHours(iso) {
  const parts = Object.fromEntries(partsFmt.formatToParts(new Date(iso)).map((p) => [p.type, p.value]))
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const hm = `${parts.hour}:${parts.minute}`
  return SERVICE.hours.some(([start, end]) => start <= hm && hm < end)
}

/** The patient the signed-in user may chat about (patient / assigned nurse; admin → 403). */
const conversation = (patientId) => mockAccessPatient(patientId, 'patient', 'nurse')
const readKey = (pid, uid) => `${pid}|${uid}`
const pointer = (pid, uid) => state.reads[readKey(pid, uid)] ?? 0
const counterpart = (user) => (user.role === 'patient' ? 'nurse' : 'patient')
const ofPatient = (pid) => state.messages.filter((m) => m.patient_id === pid)

function label(m, viewer, patient) {
  if (m.sender_role === 'system') return SYSTEM_LABEL
  if (m.sender_role === 'nurse') return viewer.role === 'patient' ? NURSE_LABEL : (mockPerson(m.sender_id)?.display_name ?? NURSE_LABEL)
  return patient.display_name
}
const payload = (m, viewer, patient) => ({
  id: m.id, sender_role: m.sender_role, kind: m.kind, body: m.body, created_at: m.created_at,
  is_mine: m.sender_id != null && m.sender_id === viewer.id, sender_label: label(m, viewer, patient),
})
const unreadOf = (pid, user) => ofPatient(pid).filter((m) => m.sender_role === counterpart(user) && m.id > pointer(pid, user.id)).length
function setPointer(pid, uid, id) {
  const key = readKey(pid, uid)
  if (id > (state.reads[key] ?? 0)) state.reads[key] = id
  return state.reads[key] ?? 0
}

/** GET /patients/{id}/messages?before_id=&after_id=&limit= */
export function mockChatMessages(patientId, { before_id: beforeId = null, after_id: afterId = null, limit = 30 } = {}) {
  const patient = conversation(patientId)
  const user = mockCurrentUser()
  if (beforeId != null && afterId != null) throw invalid('after_id', 'cannot be combined with before_id', 'Invalid query parameter')
  let rows = ofPatient(patient.id)
  let hasMore
  if (afterId != null) {
    rows = rows.filter((m) => m.id > afterId)
    hasMore = rows.length > limit
    rows = rows.slice(0, limit)
  } else {
    if (beforeId != null) rows = rows.filter((m) => m.id < beforeId)
    hasMore = rows.length > limit
    rows = rows.slice(-limit)
  }
  return {
    data: rows.map((m) => payload(m, user, patient)),
    meta: { has_more: hasMore, service: { ...serviceInfo() }, last_read_message_id: pointer(patient.id, user.id), unread_count: unreadOf(patient.id, user) },
  }
}
function serviceInfo() {
  const { hours, ...rest } = SERVICE
  return { ...rest, in_service_hours: inServiceHours(nowIso()) }
}

/** POST /patients/{id}/messages {body} with Idempotency-Key */
export function mockSendChatMessage(patientId, body, idempotencyKey) {
  const patient = conversation(patientId)
  const user = mockCurrentUser()
  if (!idempotencyKey) throw new MockApiError(428, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header is required')
  const text = typeof body === 'string' ? body.trim() : ''
  const key = `${user.id}|${idempotencyKey}`
  const seen = state.keys[key]
  if (seen) {
    if (seen.hash !== `${patient.id}|${body}`) throw new MockApiError(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    const m = state.messages.find((x) => x.id === seen.id)
    return { data: { message: payload(m, user, patient), auto_reply: null } }
  }
  if (!text || text.length > MAX_BODY) throw invalid('body', `is required (1–${MAX_BODY} characters)`)
  const now = nowIso()
  if (state.messages.filter((m) => m.sender_id === user.id && Date.parse(now) - Date.parse(m.created_at) < RATE_WINDOW_MS).length >= RATE_COUNT) {
    throw new MockApiError(429, 'RATE_LIMITED', '訊息傳送太頻繁，請稍後再試')
  }
  const message = { id: ++state.nextId, patient_id: patient.id, sender_id: user.id, sender_role: user.role === 'patient' ? 'patient' : 'nurse',
    kind: 'text', body: text, auto_reply_date: null, created_at: now }
  state.messages.push(message)
  let autoReply = null
  const day = dayFmt.format(new Date(now))
  if (message.sender_role === 'patient' && !ofPatient(patient.id).some((m) => m.auto_reply_date === day)) {
    autoReply = { id: ++state.nextId, patient_id: patient.id, sender_id: null, sender_role: 'system', kind: 'auto_reply',
      body: AUTO_REPLY_TEXT, auto_reply_date: day, created_at: now }
    state.messages.push(autoReply)
  }
  setPointer(patient.id, user.id, (autoReply ?? message).id)
  state.keys[key] = { hash: `${patient.id}|${body}`, id: message.id }
  save()
  return { data: { message: payload(message, user, patient), auto_reply: autoReply ? payload(autoReply, user, patient) : null } }
}

/** POST /patients/{id}/messages/read {last_message_id} */
export function mockMarkChatRead(patientId, lastMessageId) {
  const patient = conversation(patientId)
  const user = mockCurrentUser()
  if (!Number.isInteger(lastMessageId) || lastMessageId < 0) throw invalid('last_message_id', 'must be a non-negative integer', '輸入資料格式錯誤')
  const latest = Math.max(0, ...ofPatient(patient.id).map((m) => m.id))
  const last = setPointer(patient.id, user.id, Math.min(lastMessageId, latest))
  save()
  return { data: { last_read_message_id: last, unread_count: unreadOf(patient.id, user) } }
}

const assigned = () => mockListPatients({ perPage: 10_000 }).data

/** GET /chat/unread-count */
export function mockChatUnreadCount() {
  const user = mockCurrentUser('patient', 'nurse')
  if (user.role === 'patient') return { data: { unread_count: user.patient_id ? unreadOf(user.patient_id, user) : 0 } }
  const counts = assigned().map((p) => unreadOf(p.id, user))
  return { data: { unread_count: counts.reduce((a, b) => a + b, 0), patients_with_unread: counts.filter(Boolean).length } }
}

/** GET /chat/inbox (nurse) */
export function mockChatInbox() {
  const user = mockCurrentUser('nurse')
  const rows = assigned().map((p) => {
    const last = ofPatient(p.id).at(-1)
    const text = last ? last.body.split(/\s+/).join(' ') : ''
    return {
      patient: { id: p.id, display_name: p.display_name, patient_code: p.patient_code },
      last_message: last ? { id: last.id, sender_role: last.sender_role, kind: last.kind,
        body: text.slice(0, PREVIEW) + (text.length > PREVIEW ? '…' : ''), created_at: last.created_at } : null,
      unread_count: unreadOf(p.id, user),
    }
  })
  rows.sort((a, b) => (a.unread_count === 0) - (b.unread_count === 0)
    || (b.last_message?.id ?? 0) - (a.last_message?.id ?? 0) || a.patient.patient_code.localeCompare(b.patient.patient_code))
  return { data: rows, meta: { total: rows.length, unread_count: rows.reduce((n, r) => n + r.unread_count, 0) } }
}
