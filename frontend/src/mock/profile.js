/**
 * Mock patient basic data + contact-email verification + email channel of reminders
 * (VITE_USE_MOCK=true), in the shapes of
 *   GET / PATCH /patients/{me|id}/profile · GET /patients/{id}/weights ·
 *   POST /patients/me/email-verification · POST /patients/me/email-verification/confirm
 * and the `email_delivery` of POST /notifications. Same rules as app/modules/patient/profile.py
 * and app/modules/notification/delivery.py:
 * - the contact email is not the login account; a new address must be verified again, switches
 *   email notifications off and invalidates links already sent
 * - email notifications only for a verified address; one-time links, 24 h, one resend per 60 s
 * - weights are the patient's vital-sign readings (POST /vital-signs, source patient_app); BMI computed
 * - staff read the profile with the email masked; only the patient writes it
 * - reminders: email only when verified + enabled; scheduled reminders skipped; failures never fail
 *   the reminder. Emails go to the MockEmailService outbox (`@/mock/email`).
 */
import { nowIso } from '@/mock/clock'
import { maskEmail, mockEmailAvailable, mockSendEmail, notificationEmail, verificationEmail } from '@/mock/email'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockPatientAccount, mockPatientRecord, saveMockCareTeam } from '@/mock/patients'
import { mockTimelineVitals } from '@/mock/records'

const TOKENS_KEY = 'ccp.mock.email-verification'
const PATIENT_FIELDS = ['email', 'height_cm', 'email_notification_enabled']
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const VERIFICATION_HOURS = 24
const RESEND_SECONDS = 60
const invalid = (details) => new MockApiError(400, 'VALIDATION_ERROR', '基本資料有誤', details)

function loadTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(TOKENS_KEY)) ?? []
  } catch {
    return []
  }
}
const tokens = loadTokens() // { raw, patient_id, created_at, expires_at, used_at, revoked_at } — mock only
function saveTokens() {
  try {
    sessionStorage.setItem(TOKENS_KEY, JSON.stringify(tokens))
  } catch {
    // storage unavailable: in memory only
  }
}

const contactOf = (p) => p.contact ?? null
const verified = (c) => !!(c?.email && c?.email_verified_at)
const isStaff = (user) => user.role === 'nurse' || user.role === 'admin'

// ------------------------------------------------------------------ weight / BMI
function weightRows(patientId) {
  return mockTimelineVitals(patientId).filter((v) => v.weight_kg != null)
}

/** kg / m², one decimal (round half up, like the API); null when a value is missing. */
export function bmi(heightCm, weightKg) {
  if (!heightCm || !weightKg) return null
  const m = Number(heightCm) / 100
  return Math.round(Number((Number(weightKg) / (m * m)).toPrecision(12)) * 10) / 10
}

function weightPayload(v, patientId, user) {
  const data = { id: v.id, weight_kg: Number(v.weight_kg), measured_at: v.measured_at, source: v.source ?? 'patient_app',
    entered_by_patient: (v.source ?? 'patient_app') === 'patient_app' }
  if (isStaff(user)) data.recorded_by = data.entered_by_patient ? mockPatientAccount(patientId) : null
  return data
}

// ------------------------------------------------------------------ profile
function pendingToken(patientId) {
  const now = Date.parse(nowIso())
  return tokens.filter((t) => t.patient_id === patientId && !t.revoked_at && !t.used_at && Date.parse(t.expires_at) > now)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null
}

function payload(p, user) {
  const c = contactOf(p)
  const own = user.role === 'patient'
  const weight = weightRows(p.id)[0] ?? null
  const email = c?.email ?? null
  const pending = own && email && !verified(c) ? pendingToken(p.id) : null
  return {
    patient_id: p.id, patient_code: p.patient_code, display_name: p.display_name,
    height_cm: p.height_cm ?? null,
    latest_weight: weight ? weightPayload(weight, p.id, user) : null,
    bmi: bmi(p.height_cm, weight?.weight_kg),
    email: own ? email : null,
    email_masked: maskEmail(email),
    email_verified: verified(c),
    email_verified_at: verified(c) ? c.email_verified_at : null,
    email_notification_enabled: !!c?.email_notification_enabled,
    email_verification_sent_at: pending?.created_at ?? null,
    email_delivery_available: mockEmailAvailable,
  }
}

/** GET /patients/{me|id}/profile — patient: own; nurse: assigned (404 otherwise); admin: all. */
export function mockGetProfile(patientId) {
  const p = mockAccessPatient(patientId)
  return { data: payload(p, mockCurrentUser()) }
}

/** GET /patients/{me|id}/weights — final readings with a weight, newest first. */
export function mockPatientWeights(patientId, { limit = 30 } = {}) {
  const p = mockAccessPatient(patientId)
  const user = mockCurrentUser()
  const rows = weightRows(p.id).slice(0, Math.min(Math.max(limit, 1), 100)).map((v) => weightPayload(v, p.id, user))
  return { data: rows, meta: { total: rows.length } }
}

function revokeTokens(patientId) {
  const now = nowIso()
  for (const t of tokens) if (t.patient_id === patientId && !t.revoked_at && !t.used_at) t.revoked_at = now
}

/** PATCH /patients/me/profile — the patient only (nurse / admin 403; another patient 404). */
export function mockUpdateProfile(patientId, body = {}) {
  const p = mockAccessPatient(patientId, 'patient')
  const details = Object.keys(body).filter((k) => !PATIENT_FIELDS.includes(k)).sort()
    .map((k) => ({ field: k, issue: 'cannot be changed here (ask the nursing team)' }))
  const values = {}
  if ('email' in body) {
    const raw = body.email
    if (raw == null || (typeof raw === 'string' && !raw.trim())) values.email = null
    else if (typeof raw !== 'string' || raw.trim().length > 255 || !EMAIL.test(raw.trim())) details.push({ field: 'email', issue: 'must be a valid email address' })
    else values.email = raw.trim().toLowerCase()
  }
  if ('height_cm' in body) {
    const h = body.height_cm
    if (h != null && (typeof h !== 'number' || !Number.isFinite(h) || h < 30 || h > 250)) details.push({ field: 'height_cm', issue: 'must be a number between 30 and 250' })
    else values.height_cm = h == null ? null : Math.round(h * 10) / 10
  }
  if ('email_notification_enabled' in body) {
    if (typeof body.email_notification_enabled !== 'boolean') details.push({ field: 'email_notification_enabled', issue: 'must be true or false' })
    else values.email_notification_enabled = body.email_notification_enabled
  }
  if (details.length) throw invalid(details)

  // the API rejects the whole request (nothing saved) when notifications are switched on for an unverified address
  const emailChanges = 'email' in values && values.email !== (contactOf(p)?.email ?? null)
  if (values.email_notification_enabled === true && (emailChanges || !verified(contactOf(p)))) {
    throw new MockApiError(422, 'EMAIL_NOT_VERIFIED', '請先完成 Email 驗證，才能開啟 Email 通知',
      [{ field: 'email_notification_enabled', issue: 'the email address must be verified first' }])
  }

  if ('height_cm' in values) p.height_cm = values.height_cm
  if ('email' in values || 'email_notification_enabled' in values) {
    p.contact ??= { email: null, email_verified_at: null, email_notification_enabled: false }
  }
  if (emailChanges) {
    Object.assign(p.contact, { email: values.email, email_verified_at: null, email_notification_enabled: false })
    revokeTokens(p.id)
  }
  if ('email_notification_enabled' in values) p.contact.email_notification_enabled = values.email_notification_enabled
  saveMockCareTeam()
  saveTokens()
  return { data: payload(p, mockCurrentUser()) }
}

// ------------------------------------------------------------------ email verification
/** POST /patients/me/email-verification → { delivery, profile }. */
export function mockRequestEmailVerification(patientId) {
  const p = mockAccessPatient(patientId, 'patient')
  const c = contactOf(p)
  if (!c?.email) throw new MockApiError(422, 'NO_EMAIL', '請先填寫 Email', [{ field: 'email', issue: 'is required' }])
  if (verified(c)) throw new MockApiError(409, 'CONFLICT', '這個 Email 已經驗證過了')
  if (!mockEmailAvailable) throw new MockApiError(422, 'EMAIL_NOT_CONFIGURED', '系統目前尚未開放 Email 寄送，暫時無法驗證 Email')
  const now = nowIso()
  const latest = pendingToken(p.id)
  if (latest && Date.parse(latest.created_at) > Date.parse(now) - RESEND_SECONDS * 1000) {
    const retry = Math.floor((Date.parse(latest.created_at) + RESEND_SECONDS * 1000 - Date.parse(now)) / 1000) + 1
    throw new MockApiError(429, 'RATE_LIMITED', `驗證信剛寄出，請 ${retry} 秒後再試`, [{ field: 'retry_after', issue: String(retry) }])
  }
  revokeTokens(p.id)
  const raw = `mock-${crypto.randomUUID()}`
  tokens.push({ raw, patient_id: p.id, created_at: now, expires_at: new Date(Date.parse(now) + VERIFICATION_HOURS * 3600e3).toISOString(), used_at: null, revoked_at: null })
  saveTokens()
  const link = `${location.origin}/patient/verify-email#token=${raw}`
  const result = mockSendEmail(verificationEmail(c.email, link, VERIFICATION_HOURS, now))
  return { data: { delivery: { status: result.status, error_code: result.error_code }, profile: payload(p, mockCurrentUser()) } }
}

/** POST /patients/me/email-verification/confirm { token } — one use, 24 h, the latest link of this patient only. */
export function mockConfirmEmailVerification(patientId, body = {}) {
  const p = mockAccessPatient(patientId, 'patient')
  const raw = body.token
  if (typeof raw !== 'string' || !raw || raw.length > 200) throw new MockApiError(400, 'VALIDATION_ERROR', '驗證連結有誤', [{ field: 'token', issue: 'is required' }])
  const t = tokens.find((x) => x.raw === raw)
  const c = contactOf(p)
  if (!t || t.patient_id !== p.id || !c?.email) throw new MockApiError(422, 'VERIFICATION_LINK_INVALID', '驗證連結無效，請重新寄送驗證信')
  if (t.used_at) throw new MockApiError(422, 'VERIFICATION_LINK_USED', '這個驗證連結已經使用過了')
  if (t.revoked_at) throw new MockApiError(422, 'VERIFICATION_LINK_INVALID', '驗證連結已失效（Email 已變更或已重新寄送），請使用最新的驗證信')
  const now = nowIso()
  if (Date.parse(t.expires_at) <= Date.parse(now)) throw new MockApiError(422, 'VERIFICATION_LINK_EXPIRED', '驗證連結已過期，請重新寄送驗證信')
  t.used_at = now
  c.email_verified_at = now
  saveTokens()
  saveMockCareTeam()
  return { data: payload(p, mockCurrentUser()) }
}

// ------------------------------------------------------------------ email channel of reminders (= delivery.py)
function skipReason(p, scheduledFor) {
  const c = p ? contactOf(p) : null
  if (scheduledFor && Date.parse(scheduledFor) > Date.parse(nowIso())) return 'scheduled'
  if (!mockEmailAvailable) return 'not_configured'
  if (!c?.email) return 'no_email'
  if (!verified(c)) return 'not_verified'
  if (!c.email_notification_enabled) return 'disabled'
  return null
}

/** Plan and send the email of a new reminder; returns its `email_delivery`. Never throws. */
export function mockDeliverReminderEmail(patientId, { scheduledFor = null, sentAt }) {
  const p = mockPatientRecord(patientId)
  const reason = skipReason(p, scheduledFor)
  const c = p ? contactOf(p) : null
  const delivery = { channel: 'email', status: reason ? 'skipped' : 'pending', skip_reason: reason, error_code: null,
    recipient_masked: maskEmail(c?.email ?? null), attempted_at: null, completed_at: reason ? nowIso() : null }
  if (reason) return delivery
  delivery.attempted_at = nowIso()
  const result = mockSendEmail(notificationEmail(c.email, sentAt))
  delivery.completed_at = nowIso()
  delivery.status = result.status
  delivery.error_code = result.error_code
  return delivery
}
