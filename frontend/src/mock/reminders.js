/**
 * Mock manual / scheduled reminders (VITE_USE_MOCK=true), in the shapes of POST /api/v1/notifications
 * and GET /api/v1/notifications/scheduled. Same rules as app/modules/notification/reminders.py:
 * nurse (assigned patients) or admin; title / message required; severity info | warning;
 * scheduled_for at least 1 minute and at most 1 year ahead; the patient needs an account; optional
 * Idempotency-Key. The staff copy lives with the other mock notifications (`@/mock/nurseReview`,
 * same lifecycle); the patient sees the reminder in their notification list once it is due.
 */
import { nowIso } from '@/mock/clock'
import { addMockReminder, mockReminderRows, mockReminderView } from '@/mock/nurseReview'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockAccessPatient, mockCurrentUser } from '@/mock/patients'

const MIN_AHEAD = 60e3
const MAX_AHEAD = 366 * 86400e3
const ALLOWED = ['patient_id', 'type', 'title', 'message', 'severity', 'scheduled_for']
const seen = {} // Idempotency-Key → { hash, id } | { hash, error } (in memory, like the rest of the mock notifications)
const invalid = (details) => new MockApiError(400, 'VALIDATION_ERROR', '提醒內容有誤', details)

function validate(body) {
  const d = []
  const text = (k, limit) => {
    const v = body[k]
    if (typeof v !== 'string' || !v.trim()) d.push({ field: k, issue: `is required (at most ${limit} characters)` })
    else if (v.trim().length > limit) d.push({ field: k, issue: `must be text of at most ${limit} characters` })
  }
  text('title', 200)
  text('message', 2000)
  if ('severity' in body && !['info', 'warning'].includes(body.severity)) d.push({ field: 'severity', issue: 'must be one of: info, warning' })
  if ('type' in body && body.type !== 'reminder') d.push({ field: 'type', issue: "must be 'reminder'" })
  for (const k of Object.keys(body).filter((x) => !ALLOWED.includes(x)).sort()) d.push({ field: k, issue: 'is not a supported field' })
  let at = null
  if (body.scheduled_for != null) {
    const raw = body.scheduled_for
    const ok = typeof raw === 'string' && /[zZ]|[+-]\d{2}:\d{2}$/.test(raw) && !Number.isNaN(Date.parse(raw))
    if (!ok) d.push({ field: 'scheduled_for', issue: 'must be an ISO 8601 datetime with timezone' })
    else {
      at = Date.parse(raw)
      const now = Date.parse(nowIso())
      if (at < now + MIN_AHEAD || at > now + MAX_AHEAD) d.push({ field: 'scheduled_for', issue: 'must be at least 1 minute and at most 1 year from now (omit it to send now)' })
    }
  }
  return { d, at }
}

function create(body) {
  mockCurrentUser('nurse', 'admin')
  if (typeof body.patient_id !== 'string' || !body.patient_id) throw invalid([{ field: 'patient_id', issue: 'is required' }])
  const patient = mockAccessPatient(body.patient_id) // nurse: assigned patients only (404 otherwise)
  const { d, at } = validate(body)
  if (d.length) throw invalid(d)
  if (!patient.account_email) throw new MockApiError(422, 'NO_PATIENT_ACCOUNT', '這位病人尚未開通登入帳號，無法收到提醒')
  const created = nowIso()
  const n = addMockReminder({
    severity: body.severity ?? 'info', title: body.title.trim(), message: body.message.trim(),
    patient: { id: patient.id, patient_code: patient.patient_code, display_name: patient.display_name },
    origin: at ? 'scheduled' : 'manual', scheduled_for: at ? new Date(at).toISOString() : null, created_at: created,
  })
  return { data: mockReminderView(n) }
}

/** POST /notifications (reminder). */
export function mockCreateReminder(body = {}, key = null) {
  if (!key) return create(body)
  const slot = `${mockCurrentUser().id}:${key}`
  const hash = JSON.stringify(body, Object.keys(body).sort())
  const prior = seen[slot]
  if (prior) {
    if (prior.hash !== hash) throw new MockApiError(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    if (prior.error) throw new MockApiError(prior.error.status, prior.error.code, 'This request already failed; send a new request')
    return { data: mockReminderView(mockReminderRows().find((n) => n.id === prior.id)) }
  }
  try {
    const res = create(body)
    seen[slot] = { hash, id: res.data.id }
    return res
  } catch (e) {
    if (e instanceof MockApiError && e.status < 500) seen[slot] = { hash, error: { status: e.status, code: e.code } }
    throw e
  }
}

/**
 * Put reminders that are due into their patient's notification list (the patient's own copy: no
 * lifecycle, no staff details). Called whenever the patient's notifications are read.
 */
export function deliverDueReminders(patientId) {
  const w = patientDashboards[patientId]?.widgets
  if (!w) return
  const now = Date.parse(nowIso())
  for (const n of mockReminderRows()) {
    if (n.patient?.id !== patientId || n.delivered || (n.scheduled_for && Date.parse(n.scheduled_for) > now)) continue
    n.delivered = true
    w.notifications.items.unshift({
      id: n.id, event_key: n.event_key, type: 'reminder', origin: n.origin, severity: n.severity, title: n.title, message: n.message,
      scheduled_for: n.scheduled_for, is_read: false, created_at: n.created_at,
    })
  }
  w.notifications.unread_count = w.notifications.items.filter((x) => !x.is_read).length
}
