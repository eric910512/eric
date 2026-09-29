/**
 * Mock treatment schedule (VITE_USE_MOCK=true), in the shapes of /api/v1/chemotherapy/appointments…
 * and /api/v1/dashboard/widgets/today-appointments/data, with the backend rules
 * (app/modules/chemotherapy/appointments.py): scheduled → checked_in → completed, cancel with a
 * reason, reschedule = new appointment (rescheduled_from_id) + original `rescheduled`.
 *
 * It also derives the mock 今日行程 (today-schedule widget), the nurse's today list and the
 * timeline APPOINTMENT events. Seeded demo patients live on the demo day 2026-09-24 (the date of
 * the other mock data); patients created in mock mode use today. State survives a reload (sessionStorage).
 */
import { nowIso } from '@/mock/clock'
import { linkNursing } from '@/mock/nursing'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockPerson } from '@/mock/patients'

const STATE_KEY = 'ccp.mock.appointments'
const TZ = 'Asia/Taipei'
const DEMO_DAY = '2026-09-24'
const P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const P4 = 'e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a80'
const SEEDED = new Set([P1, 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e', 'd2e3f4a5-6b7c-4d8e-9f0a-1b2c3d4e5f60', P4])
const TYPES = ['chemo_infusion', 'lab_draw', 'clinic_visit', 'imaging', 'radiotherapy', 'education_session', 'other']
const INSTRUCTION_TYPES = ['fasting', 'check_in', 'medication', 'bring_item', 'other']
const OPEN = ['scheduled', 'checked_in']
const HIDDEN_TODAY = ['cancelled', 'rescheduled']
const TYPE_TEXT = { chemo_infusion: '化療注射', lab_draw: '抽血', clinic_visit: '門診', imaging: '影像檢查', radiotherapy: '放射治療', education_session: '衛教', other: '行程' }
const STATUS_TEXT = { scheduled: '已排定', checked_in: '已報到', completed: '已完成', cancelled: '已取消', no_show: '未到', rescheduled: '已改期' }
const clone = (v) => structuredClone(v)
const invalid = (details, message) => new MockApiError(400, 'VALIDATION_ERROR', message, details)
const conflict = (message) => new MockApiError(409, 'INVALID_STATE', message)

function seed() {
  return {
    appointments: [
      { id: 1, patient_id: P1, cycle_id: 1, appointment_type: 'clinic_visit', title: '門診追蹤與抽血', scheduled_at: '2026-09-24T06:00:00.000Z',
        duration_min: 30, location: '腫瘤科門診（Demo）', status: 'scheduled', rescheduled_from_id: null, notes: null, created_by: 'mock-user-nurse01',
        created_at: '2026-09-20T01:00:00.000Z',
        instructions: [{ id: 1, instruction_type: 'check_in', due_at: '2026-09-24T05:40:00.000Z', text: '13:40 報到', is_highlighted: true }] },
      { id: 8, patient_id: P4, cycle_id: null, appointment_type: 'lab_draw', title: '化療前抽血', scheduled_at: '2026-09-24T07:30:00.000Z',
        duration_min: 15, location: '一樓檢驗科（Demo）', status: 'scheduled', rescheduled_from_id: null, notes: null, created_by: 'mock-user-nurse01',
        created_at: '2026-09-20T01:00:00.000Z', instructions: [] },
    ],
    nextId: 1000,
  }
}
function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.appointments) return saved
  } catch {
    // storage unavailable: start from the seed
  }
  return seed()
}
const state = load()
const nextId = () => ++state.nextId

// ------------------------------------------------------------------ dates
const fmtDay = new Intl.DateTimeFormat('en-CA', { timeZone: TZ })
const localDay = (iso) => fmtDay.format(new Date(iso))
const realToday = () => fmtDay.format(new Date())
/** The "today" of a patient in mock mode: the demo day for seeded patients, else today. */
const todayOf = (pid) => patientDashboards[pid]?.widgets['today-schedule']?.date ?? (SEEDED.has(pid) ? DEMO_DAY : realToday())

function parseIso(raw, field, details, required = true) {
  if (raw == null) {
    if (required) details.push({ field, issue: 'is required (ISO 8601 datetime with timezone)' })
    return null
  }
  const t = typeof raw === 'string' && /([zZ]|[+-]\d{2}:\d{2})$/.test(raw) ? Date.parse(raw) : NaN
  if (Number.isNaN(t)) {
    details.push({ field, issue: 'must be an ISO 8601 datetime with timezone' })
    return null
  }
  return new Date(t).toISOString()
}
function scheduledAt(raw, details) {
  const at = parseIso(raw, 'scheduled_at', details)
  if (at && !(Date.now() - 30 * 86400000 <= Date.parse(at) && Date.parse(at) <= Date.now() + 366 * 86400000)) {
    details.push({ field: 'scheduled_at', issue: 'must be within 30 days ago and one year ahead' })
  }
  return at
}

// ------------------------------------------------------------------ serialize (= appointment_payload)
let cycleNumber = () => null // set by the chemotherapy mock (avoids an import cycle)
let cycleOfPatient = () => false
export function linkChemotherapy({ number, belongs }) {
  cycleNumber = number
  cycleOfPatient = belongs
}
const isStaff = (u) => u.role === 'nurse' || u.role === 'admin'
function payload(a, viewer) {
  const next = state.appointments.find((x) => x.rescheduled_from_id === a.id)
  const data = {
    id: a.id, patient_id: a.patient_id, cycle_id: a.cycle_id, cycle_number: a.cycle_id ? cycleNumber(a.cycle_id) : null,
    appointment_type: a.appointment_type, title: a.title, scheduled_at: a.scheduled_at, duration_min: a.duration_min,
    location: a.location, status: a.status, rescheduled_from_id: a.rescheduled_from_id, rescheduled_to_id: next?.id ?? null,
    instructions: a.instructions.map((i) => ({ id: i.id, instruction_type: i.instruction_type, due_at: i.due_at, text: i.text, is_highlighted: i.is_highlighted })),
  }
  if (isStaff(viewer)) Object.assign(data, { notes: a.notes, created_by: mockPerson(a.created_by), created_at: a.created_at })
  return data
}
const find = (id) => state.appointments.find((a) => a.id === id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'Appointment not found') })()
export const mockAppointmentPatient = (id) => state.appointments.find((a) => a.id === id)?.patient_id ?? null
linkNursing({ appointment: mockAppointmentPatient })

// ------------------------------------------------------------------ read
export function mockListAppointments(patientId, { date = null, from = null, to = null, status = null } = {}) {
  const viewer = mockCurrentUser()
  const pid = mockAccessPatient(patientId).id
  const details = []
  const isDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`))
  for (const [k, v] of [['date', date], ['from', from], ['to', to]]) if (v && !isDate(v)) details.push({ field: k, issue: 'must be a date (YYYY-MM-DD)' })
  if (status && !STATUS_TEXT[status]) details.push({ field: 'status', issue: `must be one of: ${Object.keys(STATUS_TEXT).join(', ')}` })
  if (from && to && from > to) details.push({ field: 'to', issue: 'must not be earlier than from' })
  if (details.length) throw invalid(details, '查詢條件有誤')
  const start = date ?? from
  const end = date ?? to
  const rows = state.appointments
    .filter((a) => a.patient_id === pid && (!start || localDay(a.scheduled_at) >= start) && (!end || localDay(a.scheduled_at) <= end) && (!status || a.status === status))
    .sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at) || a.id - b.id)
  return { data: rows.map((a) => payload(a, viewer)), meta: { timezone: TZ } }
}
export function mockGetAppointment(id) {
  return { data: payload(find(id), mockCurrentUser()) }
}
/** today-appointments widget for the signed-in nurse (visible patients only). */
export function mockTodayAppointments(visible) {
  mockCurrentUser('nurse')
  const items = state.appointments
    .filter((a) => visible(a.patient_id) && !HIDDEN_TODAY.includes(a.status) && localDay(a.scheduled_at) === todayOf(a.patient_id))
    .sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at) || a.id - b.id)
    .map((a) => {
      const s = patientDashboards[a.patient_id]?.widgets['patient-summary']
      const p = s ?? mockAccessPatient(a.patient_id)
      return { id: a.id, patient_id: a.patient_id, patient_code: p.patient_code, display_name: p.display_name, appointment_type: a.appointment_type,
        title: a.title, scheduled_at: a.scheduled_at, location: a.location, status: a.status }
    })
  return { data: items, meta: { total: items.length } }
}

// ------------------------------------------------------------------ write
function instructions(raw, details) {
  if (!Array.isArray(raw) || raw.length > 10) {
    details.push({ field: 'instructions', issue: 'must be a list of at most 10 items' })
    return []
  }
  return raw.map((item, n) => {
    const at = `instructions[${n}]`
    if (!INSTRUCTION_TYPES.includes(item?.instruction_type)) details.push({ field: `${at}.instruction_type`, issue: `must be one of: ${INSTRUCTION_TYPES.join(', ')}` })
    if (typeof item?.text !== 'string' || !item.text.trim() || item.text.trim().length > 255) details.push({ field: `${at}.text`, issue: 'is required (at most 255 characters)' })
    const due = parseIso(item?.due_at, `${at}.due_at`, details, false)
    const highlighted = item?.is_highlighted ?? true
    if (typeof highlighted !== 'boolean') details.push({ field: `${at}.is_highlighted`, issue: 'must be true or false' })
    for (const k of Object.keys(item ?? {}).filter((x) => !['instruction_type', 'text', 'due_at', 'is_highlighted'].includes(x)).sort()) {
      details.push({ field: `${at}.${k}`, issue: 'is not a supported field' })
    }
    return { id: nextId(), instruction_type: item?.instruction_type, text: (item?.text ?? '').trim(), due_at: due, is_highlighted: highlighted === true }
  })
}
function values(patientId, body, partial, details) {
  const out = {}
  const has = (k) => k in body
  if (!partial || has('appointment_type')) {
    if (!TYPES.includes(body.appointment_type)) details.push({ field: 'appointment_type', issue: `${has('appointment_type') ? 'must be one of' : 'is required (one of'}: ${TYPES.join(', ')}${has('appointment_type') ? '' : ')'}` })
    else out.appointment_type = body.appointment_type
  }
  if (!partial || has('title')) {
    if (typeof body.title !== 'string' || !body.title.trim() || body.title.trim().length > 100) details.push({ field: 'title', issue: 'is required (at most 100 characters)' })
    else out.title = body.title.trim()
  }
  if (has('duration_min')) {
    const v = body.duration_min
    if (v !== null && (!Number.isInteger(v) || v < 1 || v > 1440)) details.push({ field: 'duration_min', issue: 'must be an integer from 1 to 1440' })
    else out.duration_min = v
  }
  for (const [k, limit] of [['location', 100], ['notes', 2000]]) {
    if (!has(k)) continue
    const v = body[k]
    if (v != null && (typeof v !== 'string' || v.trim().length > limit)) details.push({ field: k, issue: `must be text of at most ${limit} characters` })
    else out[k] = typeof v === 'string' ? v.trim() || null : null
  }
  if (has('cycle_id')) {
    if (body.cycle_id !== null && !(Number.isInteger(body.cycle_id) && cycleOfPatient(body.cycle_id, patientId))) details.push({ field: 'cycle_id', issue: "must be one of this patient's cycles" })
    else out.cycle_id = body.cycle_id
  }
  if (has('instructions')) out.instructions = instructions(body.instructions, details)
  return out
}
const FIELDS = ['appointment_type', 'title', 'duration_min', 'location', 'notes', 'cycle_id', 'instructions']
const unknown = (body, allowed, details) => {
  for (const k of Object.keys(body).filter((x) => !allowed.includes(x)).sort()) details.push({ field: k, issue: 'is not a supported field' })
}

export function mockCreateAppointment(body) {
  const user = mockCurrentUser('nurse')
  if (typeof body.patient_id !== 'string' || !body.patient_id) throw invalid([{ field: 'patient_id', issue: 'is required' }], '行程資料有誤')
  const pid = mockAccessPatient(body.patient_id).id
  const details = []
  const out = values(pid, body, false, details)
  const at = scheduledAt(body.scheduled_at, details)
  unknown(body, ['patient_id', 'scheduled_at', ...FIELDS], details)
  if (details.length) throw invalid(details, '行程資料有誤')
  const a = { id: nextId(), patient_id: pid, cycle_id: null, duration_min: null, location: null, notes: null, instructions: [], ...out,
    scheduled_at: at, status: 'scheduled', rescheduled_from_id: null, created_by: user.id, created_at: nowIso() }
  state.appointments.push(a)
  sync(pid)
  return { data: payload(a, user) }
}
export function mockUpdateAppointment(id, body) {
  const user = mockCurrentUser('nurse')
  const a = find(id)
  if (!OPEN.includes(a.status)) throw conflict('已結束、取消或改期的行程不能修改')
  const details = []
  const out = values(a.patient_id, body, true, details)
  if ('scheduled_at' in body) details.push({ field: 'scheduled_at', issue: 'use reschedule (the original time is kept in the history)' })
  unknown(body, [...FIELDS, 'scheduled_at'], details)
  if (details.length) throw invalid(details, '行程資料有誤')
  Object.assign(a, out)
  sync(a.patient_id)
  return { data: payload(a, user) }
}
function step(id, fn) {
  const user = mockCurrentUser('nurse')
  const a = find(id)
  fn(a)
  sync(a.patient_id)
  return { data: payload(a, user) }
}
export const mockCheckInAppointment = (id) => step(id, (a) => {
  if (a.status !== 'scheduled') throw conflict('只有尚未報到的行程可以報到')
  if (localDay(a.scheduled_at) > todayOf(a.patient_id)) throw conflict('行程還沒到，不能報到')
  a.status = 'checked_in'
})
export const mockCompleteAppointment = (id) => step(id, (a) => {
  if (!OPEN.includes(a.status)) throw conflict('這個行程已結束、取消或改期')
  if (localDay(a.scheduled_at) > todayOf(a.patient_id)) throw conflict('行程還沒到，不能完成')
  a.status = 'completed'
})
export const mockCancelAppointment = (id, body = {}) => step(id, (a) => {
  if (!OPEN.includes(a.status)) throw conflict('這個行程已結束、取消或改期')
  const details = []
  if (typeof body.reason !== 'string' || !body.reason.trim() || body.reason.trim().length > 500) details.push({ field: 'reason', issue: 'is required (at most 500 characters)' })
  unknown(body, ['reason'], details)
  if (details.length) throw invalid(details, '取消資料有誤')
  a.status = 'cancelled'
})
function move(a, userId, at) {
  const delta = Date.parse(at) - Date.parse(a.scheduled_at)
  const copy = { ...clone(a), id: nextId(), scheduled_at: at, status: 'scheduled', rescheduled_from_id: a.id, created_by: userId, created_at: nowIso(),
    instructions: a.instructions.map((i) => ({ ...i, id: nextId(), due_at: i.due_at ? new Date(Date.parse(i.due_at) + delta).toISOString() : null })) }
  a.status = 'rescheduled'
  state.appointments.push(copy)
  return copy
}
export function mockRescheduleAppointment(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const a = find(id)
  if (a.status !== 'scheduled') throw conflict('只有尚未報到的行程可以改期')
  const details = []
  const at = scheduledAt(body.scheduled_at, details)
  if (typeof body.reason !== 'string' || !body.reason.trim() || body.reason.trim().length > 500) details.push({ field: 'reason', issue: 'is required (at most 500 characters)' })
  unknown(body, ['scheduled_at', 'reason'], details)
  if (details.length) throw invalid(details, '改期資料有誤')
  if (Date.parse(at) === Date.parse(a.scheduled_at)) throw invalid([{ field: 'scheduled_at', issue: 'must differ from the current time' }], '改期資料有誤')
  const copy = move(a, user.id, at)
  sync(a.patient_id)
  return { data: payload(copy, user) }
}

// ------------------------------------------------------------------ chemotherapy integration (called by the chemotherapy mock)
/** `generate_infusion_appointments` → { time, location } or null; pushes errors to `details`. */
export function infusionOptions(raw, details) {
  if (raw == null || raw === false) return null
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    details.push({ field: 'generate_infusion_appointments', issue: 'must be an object {enabled, time, location}' })
    return null
  }
  if ((raw.enabled ?? true) !== true) return null
  const ok = typeof raw.time === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(raw.time)
  if (!ok) details.push({ field: 'generate_infusion_appointments.time', issue: "must be HH:MM (patient's local time)" })
  if (raw.location != null && (typeof raw.location !== 'string' || raw.location.length > 100)) {
    details.push({ field: 'generate_infusion_appointments.location', issue: 'must be text of at most 100 characters' })
  }
  for (const k of Object.keys(raw).filter((x) => !['enabled', 'time', 'location'].includes(x)).sort()) {
    details.push({ field: `generate_infusion_appointments.${k}`, issue: 'is not a supported field' })
  }
  return ok ? { time: raw.time, location: raw.location?.trim() || null } : null
}
/** One chemo_infusion appointment per cycle (scheduled date + local time). Returns the ids. */
export function createInfusions(patientId, userId, cycles, { time, location }) {
  const ids = cycles.map((c) => {
    const a = { id: nextId(), patient_id: patientId, cycle_id: c.id, appointment_type: 'chemo_infusion', title: `第 ${c.cycle_number} 次化療注射`,
      scheduled_at: new Date(`${c.scheduled_date}T${time}:00+08:00`).toISOString(), duration_min: null, location, status: 'scheduled',
      rescheduled_from_id: null, notes: null, created_by: userId, created_at: nowIso(), instructions: [] }
    state.appointments.push(a)
    return a.id
  })
  sync(patientId)
  return ids
}
/** Cycle delayed by `days`: its scheduled appointments move by the same days (history kept). */
export function moveCycleAppointments(cycleId, userId, days) {
  const moved = state.appointments
    .filter((a) => a.cycle_id === cycleId && a.status === 'scheduled')
    .map((a) => [a.id, move(a, userId, new Date(Date.parse(a.scheduled_at) + days * 86400000).toISOString()).id])
  if (moved.length) sync(state.appointments.find((a) => a.id === moved[0][0]).patient_id)
  return moved
}
export function cycleAppointmentId(cycleId) {
  const rows = state.appointments.filter((a) => a.cycle_id === cycleId && a.appointment_type === 'chemo_infusion' && !HIDDEN_TODAY.includes(a.status))
  return rows.sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at))[0]?.id ?? null
}

// ------------------------------------------------------------------ for the other mock modules
/** Timeline APPOINTMENT events: appointments whose time has come (= backend _appointments). */
export function mockTimelineAppointments(patientId, staff) {
  const at = (x) => (x ? { id: x.id, scheduled_at: x.scheduled_at } : null)
  return state.appointments
    .filter((a) => a.patient_id === patientId && Date.parse(a.scheduled_at) <= Date.now())
    .map((a) => {
      const next = state.appointments.find((x) => x.rescheduled_from_id === a.id)
      const prev = state.appointments.find((x) => x.id === a.rescheduled_from_id)
      return {
        event_id: `APPOINTMENT:${a.id}`, event_type: 'APPOINTMENT', occurred_at: a.scheduled_at, title: a.title,
        summary: [STATUS_TEXT[a.status], a.location].filter(Boolean).join('，'), source: { table: 'appointments', id: a.id }, ...(a.cycle_id ? { cycle_id: a.cycle_id } : {}),
        detail: {
          kind: 'appointment', appointment_type: a.appointment_type, appointment_type_text: TYPE_TEXT[a.appointment_type] ?? '行程', status: a.status,
          status_text: STATUS_TEXT[a.status], location: a.location, duration_min: a.duration_min,
          instructions: a.instructions.map((i) => ({ instruction_type: i.instruction_type, text: i.text, due_at: i.due_at })),
          rescheduled_from: at(prev), rescheduled_to: at(next), ...(staff ? { notes: a.notes } : {}),
        },
      }
    })
}

/** The dashboard's 今日行程 (today-schedule widget) derived again from this state. */
function sync(patientId) {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage unavailable: state lives in memory only
  }
  const w = patientDashboards[patientId]?.widgets['today-schedule']
  if (!w) return
  const rows = state.appointments
    .filter((a) => a.patient_id === patientId && localDay(a.scheduled_at) === w.date && !HIDDEN_TODAY.includes(a.status))
    .sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at))
  w.appointments = rows.map((a) => ({ id: a.id, appointment_type: a.appointment_type, title: a.title, scheduled_at: a.scheduled_at, location: a.location, status: a.status }))
  w.highlight_instructions = rows.flatMap((a) => a.instructions.filter((i) => i.is_highlighted).map((i) => ({ appointment_id: a.id, instruction_type: i.instruction_type, due_at: i.due_at, text: i.text })))
    .sort((x, y) => (x.due_at === null) - (y.due_at === null) || Date.parse(x.due_at) - Date.parse(y.due_at))
}
for (const pid of new Set(state.appointments.map((a) => a.patient_id))) sync(pid) // after a reload
