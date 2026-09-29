/**
 * Mock patient & care-team management (VITE_USE_MOCK=true), in the exact shapes of
 *   /api/v1/patients…, /api/v1/admin/users, PUT /api/v1/auth/password (api-design.md §4, §13).
 *
 * It also is the mock "server-side" access control: the same rules as app.core.auth —
 * admin sees every patient, a patient only themselves, a nurse only currently assigned
 * patients; anything else is 404. Other mock modules are guarded with it in `@/mock/api`.
 *
 * State (patients, accounts, assignments) survives a reload of the same tab (sessionStorage),
 * so a flow that signs in as several users in one tab keeps working. Synthetic data only;
 * mock temporary passwords live only in this tab's memory / sessionStorage.
 */
import { byTimeDesc, nowIso } from '@/mock/clock'
import { nurseOverview } from '@/mock/nurseOverview'
import { patientDashboards } from '@/mock/patientDashboards'
import { MOCK_PASSWORD, mockUsers } from '@/mock/users'
import { passwordProblems } from '@/utils/password'

const STATE_KEY = 'ccp.mock.care-team'
const AUTH_KEY = 'ccp.auth' // stores/auth session (the signed-in mock user)
const TZ = 'Asia/Taipei'
const clone = (v) => structuredClone(v)

/** Error with the API error envelope's fields (status / code / message / details). */
export class MockApiError extends Error {
  constructor(status, code, message, details = []) {
    super(message)
    Object.assign(this, { status, code, details })
  }
}
const notFound = (what = '病人') => new MockApiError(404, 'NOT_FOUND', `找不到這位${what}`)
const forbidden = () => new MockApiError(403, 'FORBIDDEN', '您沒有執行這個操作的權限')
const invalid = (details, message = '資料內容有誤') => new MockApiError(400, 'VALIDATION_ERROR', message, details)
const conflict = (message, details = []) => new MockApiError(409, 'CONFLICT', message, details)

export const mockCancerTypes = [
  { code: 'C11', name_zh: '鼻咽癌', name_en: 'Nasopharyngeal carcinoma' },
  { code: 'C50', name_zh: '乳癌', name_en: 'Breast cancer' },
]
const ALERT_TYPES = ['allergy', 'limb_restriction', 'fall_risk', 'isolation', 'other']
const SEVERITIES = ['high', 'medium', 'low']
const BODY_SITES = ['left_arm', 'right_arm', 'leg']
const GENDERS = ['male', 'female', 'other']
const BLOOD_TYPES = ['A', 'B', 'AB', 'O']
const DX_STATUS = ['active', 'remission', 'recurrence', 'resolved']
const PROFILE_FIELDS = ['display_name', 'gender', 'date_of_birth', 'height_cm', 'blood_type', 'allergies', 'baseline_ecog', 'timezone']
const FORBIDDEN_FIELDS = ['national_id', 'id_number', 'identity_number', 'id_card', 'nid']
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const ADMIN = { id: 'mock-user-admin01', display_name: '測試管理者' }
const SEED_AT = '2026-09-01T01:00:00.000Z'

// ------------------------------------------------------------------ seed (matches the other mock modules)
function seed() {
  const nurse01 = mockUsers['nurse01@demo.local']
  const person = (id, display_name, dob, gender, extra = {}) => ({
    id, patient_code: extra.code, display_name, gender, date_of_birth: dob, height_cm: null, blood_type: null, allergies: null,
    baseline_ecog: null, timezone: TZ, is_demo: true, created_at: SEED_AT, created_by: ADMIN, deleted: false,
    account_email: extra.email ?? null, care_alerts: extra.alerts ?? [], diagnoses: extra.dx ?? [], current_cycle: extra.cycle ?? null,
  })
  const patients = [
    person('72ba2de4-f19d-4daf-96f9-03a5e83c07d0', '測試病人 甲', '1970-05-12', 'male', {
      code: 'P00001', email: 'patient01@demo.local',
      alerts: [{ id: 1, alert_type: 'allergy', body_site: null, description: 'Penicillin 過敏', severity: 'high', is_active: true }],
      dx: [{ id: 1, cancer_type: mockCancerTypes[0], stage: 'III', diagnosis_date: '2026-08-20', status: 'active', is_primary: true, histology: null, notes: null }],
      cycle: { cycle_number: 1, total_cycles: 3, cycle_day: 4, in_nadir: false },
    }),
    person('b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e', '測試病人 乙', '1964-02-18', 'female', {
      code: 'P00002', email: 'patient02@demo.local',
      alerts: [
        { id: 2, alert_type: 'limb_restriction', body_site: 'right_arm', description: '右手禁止注射及量血壓', severity: 'high', is_active: true },
        { id: 3, alert_type: 'fall_risk', body_site: null, description: '跌倒高風險', severity: 'medium', is_active: true },
      ],
      dx: [{ id: 2, cancer_type: mockCancerTypes[1], stage: 'II', diagnosis_date: '2026-07-02', status: 'active', is_primary: true, histology: null, notes: null }],
      cycle: { cycle_number: 2, total_cycles: 4, cycle_day: 9, in_nadir: true },
    }),
    person('d2e3f4a5-6b7c-4d8e-9f0a-1b2c3d4e5f60', '測試病人 丙', '1978-11-03', 'female', { code: 'P00003', cycle: { cycle_number: 1, total_cycles: 4, cycle_day: 6, in_nadir: false } }),
    person('e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a80', '測試病人 丁', '1959-08-27', 'male', { code: 'P00004', cycle: { cycle_number: 3, total_cycles: 6, cycle_day: 15, in_nadir: false } }),
  ]
  const accounts = {}
  for (const u of Object.values(mockUsers)) {
    accounts[u.email] = { ...clone(u), password: MOCK_PASSWORD, must_change_password: false, is_active: true, last_login_at: null, nurse_profile: null }
  }
  accounts['nurse01@demo.local'].nurse_profile = { staff_code: 'N0001', department: '腫瘤科（Demo）', title: '個案管理師' }
  return {
    patients,
    accounts,
    assignments: patients.map((p, i) => ({ id: i + 1, patient_id: p.id, nurse_id: nurse01.id, is_primary: true, assigned_at: SEED_AT, ended_at: null, assigned_by: ADMIN })),
    nextId: 100,
  }
}

function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.patients) return saved
  } catch {
    // storage unavailable: start from the seed
  }
  return seed()
}
const state = load()
function save() {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage unavailable: state lives in memory only
  }
}
const nextId = () => ++state.nextId
const uuid = () => crypto.randomUUID()

// ------------------------------------------------------------------ signed-in user (mock session)
let sessionUser = null
let sessionToken = null
/** Called by the auth store on sign-in / sign-out. */
export function setMockViewer(user, token = null) {
  sessionUser = user ? JSON.parse(JSON.stringify(user)) : null // may be a reactive store object
  sessionToken = user ? token : null
}
function viewerToken() {
  if (sessionUser) return sessionToken
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_KEY))?.token ?? null
  } catch {
    return null
  }
}
/** Session id in a mock token (`mock-token-<user>~<sid>~<exp ms>`), like the API's `sid` claim. */
const sidOf = (token) => (typeof token === 'string' && token.includes('~') ? token.split('~')[1] : null)
const expOf = (token) => Number(typeof token === 'string' ? token.split('~')[2] : 0) || null
const SESSION_DAYS = 14
const liveSession = (s) => s && !s.revoked_at && Date.parse(s.expires_at) > Date.now()
function sessionsOf(acct) {
  return Object.entries(state.sessions ?? {}).filter(([, s]) => s.user_id === acct.id && liveSession(s))
}
function viewer() {
  if (sessionUser) return sessionUser
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_KEY))?.user ?? null
  } catch {
    return null
  }
}
function account(user) {
  return user ? Object.values(state.accounts).find((a) => a.id === user.id) ?? null : null
}
/** Active account with an active session, else 401 (= JWT user lookup). */
function signedIn() {
  const token = viewerToken()
  const acct = account(viewer())
  const s = state.sessions?.[sidOf(token)]
  if (!acct || !acct.is_active || !liveSession(s) || s.user_id !== acct.id) throw new MockApiError(401, 'UNAUTHENTICATED', '請重新登入')
  if (expOf(token) && Date.now() >= expOf(token)) throw new MockApiError(401, 'TOKEN_EXPIRED', 'Access token has expired')
  return acct
}
/** The signed-in user; 401 / 403 PASSWORD_CHANGE_REQUIRED like require_auth. */
function current(...roles) {
  const acct = signedIn()
  if (acct.must_change_password) throw new MockApiError(403, 'PASSWORD_CHANGE_REQUIRED', '請先設定新密碼')
  if (roles.length && !roles.includes(acct.role)) throw forbidden()
  return acct
}

/** The signed-in mock account (for other mock modules' role checks, like require_auth). */
export function mockCurrentUser(...roles) {
  return current(...roles)
}
/** { id, display_name } of a mock account id (other modules' "by" fields). */
export function mockPerson(userId) {
  return person(accountById(userId))
}

// ------------------------------------------------------------------ access control (= ensure_can_view_patient)
const activeAssignments = (patientId) => state.assignments.filter((a) => a.patient_id === patientId && !a.ended_at)
function canView(user, patient) {
  if (!user || !patient || patient.deleted) return false
  if (user.role === 'admin') return true
  if (user.role === 'patient') return user.patient_id === patient.id
  if (user.role === 'nurse') return activeAssignments(patient.id).some((a) => a.nurse_id === user.id)
  return false
}
function resolve(patientId, user) {
  const id = patientId === 'me' ? (user.role === 'patient' ? user.patient_id : null) : patientId
  return state.patients.find((p) => p.id === id && !p.deleted) ?? null
}
/** The patient if the signed-in user may access them, else 404 (or 401 / 403 as above). */
export function mockAccessPatient(patientId, ...roles) {
  const user = current(...roles)
  const patient = resolve(patientId, user)
  if (!canView(user, patient)) throw notFound()
  return patient
}
/** Guard for patient-scoped mock handlers: returns the resolved patient id. */
export function mockEnsureCanView(patientId) {
  return mockAccessPatient(patientId).id
}
/** Filter for mock lists that span patients (notifications, abnormal vitals, caseload). */
export function mockVisiblePatient(patientId) {
  try {
    const user = current()
    return canView(user, state.patients.find((p) => p.id === patientId))
  } catch {
    return false
  }
}

// ------------------------------------------------------------------ sign-in / password (auth mock)
function userPayload(acct) {
  return { id: acct.id, display_name: acct.display_name, email: acct.email, role: acct.role, patient_id: acct.patient_id ?? null,
    must_change_password: acct.must_change_password }
}
/** POST /auth/login → { user, sid } (a new server-side session), or null for wrong email / password. */
export function mockSignIn(email, password, userAgent = null) {
  const acct = state.accounts[email.trim().toLowerCase()]
  if (!acct || !acct.is_active || password !== acct.password) return null
  const now = nowIso()
  acct.last_login_at = now
  const sid = uuid()
  state.sessions ??= {}
  state.sessions[sid] = { user_id: acct.id, created_at: now, expires_at: new Date(Date.now() + SESSION_DAYS * 86400e3).toISOString(),
    revoked_at: null, ip_address: '127.0.0.1', user_agent: userAgent ? String(userAgent).slice(0, 255) : null }
  save()
  return { user: userPayload(acct), sid }
}
export const mockAuthenticate = (email, password) => mockSignIn(email, password)?.user ?? null
function endSessions(acct, keep = null) {
  const ended = sessionsOf(acct).filter(([sid]) => sid !== keep)
  for (const [, s] of ended) s.revoked_at = nowIso()
  return ended.length
}
/**
 * POST /auth/refresh: the session of `token` (the mock stands in for the HttpOnly cookie with the
 * session id in its own token) → { user, sid } for a new access token. Ended / expired session or
 * disabled account → 401 like the API (REFRESH_TOKEN_REVOKED / _EXPIRED / _INVALID).
 */
export function mockRefresh(token) {
  const sid = sidOf(token)
  const s = state.sessions?.[sid]
  if (!s) throw new MockApiError(401, 'REFRESH_TOKEN_INVALID', '請重新登入')
  if (s.revoked_at) throw new MockApiError(401, 'REFRESH_TOKEN_REVOKED', '這個登入已結束，請重新登入')
  if (Date.parse(s.expires_at) <= Date.now()) throw new MockApiError(401, 'REFRESH_TOKEN_EXPIRED', '登入已逾時，請重新登入')
  const acct = Object.values(state.accounts).find((a) => a.id === s.user_id)
  if (!acct?.is_active) {
    s.revoked_at = nowIso()
    save()
    throw new MockApiError(401, 'ACCOUNT_DISABLED', '請重新登入')
  }
  return { user: userPayload(acct), sid }
}
/** POST /auth/logout (token: the one being signed out). */
export function mockLogout(token) {
  const s = state.sessions?.[sidOf(token)]
  if (s && !s.revoked_at) s.revoked_at = nowIso()
  save()
}
function sessionRows(acct, currentSid) {
  return sessionsOf(acct)
    .map(([sid, s]) => ({ id: sid, created_at: s.created_at, expires_at: s.expires_at, ip_address: s.ip_address, user_agent: s.user_agent, current: sid === currentSid }))
    .sort((a, b) => Number(b.current) - Number(a.current) || Date.parse(b.created_at) - Date.parse(a.created_at))
}
/** GET /auth/sessions */
export function mockListSessions() {
  return { data: sessionRows(current(), sidOf(viewerToken())) }
}
/** DELETE /auth/sessions/{sid}: own sessions only (others → 404). */
export function mockEndSession(sid) {
  const acct = current()
  const s = state.sessions?.[sid]
  if (!s || s.user_id !== acct.id || !liveSession(s)) throw notFound()
  s.revoked_at = nowIso()
  save()
  return null
}
/** POST /auth/sessions/revoke-others */
export function mockEndOtherSessions() {
  const n = endSessions(current(), sidOf(viewerToken()))
  save()
  return { data: { revoked: n } }
}
/** GET /auth/me */
export function mockMe() {
  return userPayload(signedIn())
}
/** PUT /auth/password (allowed while a change is required). */
export function mockChangePassword(currentPassword, newPassword) {
  const acct = signedIn()
  const details = []
  if (typeof currentPassword !== 'string' || !currentPassword) details.push({ field: 'current_password', issue: 'is required' })
  else if (currentPassword !== acct.password) details.push({ field: 'current_password', issue: 'is incorrect' })
  details.push(...passwordProblems(newPassword, currentPassword).map((issue) => ({ field: 'new_password', issue })))
  if (details.length) throw invalid(details, '密碼設定有誤')
  Object.assign(acct, { password: newPassword, must_change_password: false })
  endSessions(acct, sidOf(viewerToken())) // every other sign-in ends; this one continues
  save()
}

const PW_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
function temporaryPassword() {
  const pick = () => PW_ALPHABET[crypto.getRandomValues(new Uint32Array(1))[0] % PW_ALPHABET.length]
  for (;;) {
    const s = Array.from({ length: 12 }, pick).join('')
    if (/[A-Za-z]/.test(s) && /\d/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8)}`
  }
}

// ------------------------------------------------------------------ serializers (= patient/management.py)
const person = (u) => (u ? { id: u.id, display_name: u.display_name } : null)
const accountById = (id) => Object.values(state.accounts).find((a) => a.id === id)
function localToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date())
}
function age(p) {
  const [y, m, d] = localToday().split('-').map(Number)
  const [by, bm, bd] = p.date_of_birth.split('-').map(Number)
  return y - by - (m < bm || (m === bm && d < bd) ? 1 : 0)
}
function careTeam(p) {
  const rows = activeAssignments(p.id).sort((a, b) => Number(b.is_primary) - Number(a.is_primary) || a.assigned_at.localeCompare(b.assigned_at))
  const primary = rows.find((a) => a.is_primary)
  return {
    primary_nurse: primary ? person(accountById(primary.nurse_id)) : null,
    nurses: rows.map((a) => ({ ...person(accountById(a.nurse_id)), is_primary: a.is_primary, assigned_at: a.assigned_at })),
  }
}
function primaryDiagnosis(p) {
  const d = p.diagnoses.find((x) => x.is_primary) ?? p.diagnoses[0]
  return d ? { id: d.id, cancer_type_code: d.cancer_type.code, name_zh: d.cancer_type.name_zh, stage: d.stage } : null
}
function listItem(p, user) {
  const team = careTeam(p)
  const item = {
    id: p.id, patient_code: p.patient_code, display_name: p.display_name, gender: p.gender, age: age(p),
    primary_diagnosis: primaryDiagnosis(p), current_cycle: clone(p.current_cycle), has_account: !!p.account_email,
    care_team: team, created_at: p.created_at,
  }
  if (user.role === 'nurse') item.is_primary_nurse = team.nurses.some((n) => n.id === user.id && n.is_primary)
  return item
}
function detail(p, user) {
  const data = {
    id: p.id, patient_code: p.patient_code, display_name: p.display_name, gender: p.gender, date_of_birth: p.date_of_birth,
    age: age(p), height_cm: p.height_cm, blood_type: p.blood_type, allergies: p.allergies, baseline_ecog: p.baseline_ecog,
    timezone: p.timezone, is_demo: true, care_alerts: clone(p.care_alerts.filter((a) => a.is_active)),
    diagnoses: clone(p.diagnoses), current_cycle: clone(p.current_cycle),
  }
  if (user.role === 'nurse' || user.role === 'admin') {
    const acct = p.account_email ? state.accounts[p.account_email] : null
    Object.assign(data, {
      care_team: careTeam(p),
      account: {
        has_account: !!acct, email: acct?.email ?? null, is_active: acct ? acct.is_active : null,
        must_change_password: acct ? acct.must_change_password : null, last_login_at: acct ? acct.last_login_at : null,
      },
      created_at: p.created_at,
      created_by: clone(p.created_by),
    })
  }
  return data
}
function assignmentPayload(a) {
  return { id: a.id, nurse: person(accountById(a.nurse_id)), is_primary: a.is_primary, assigned_at: a.assigned_at,
    ended_at: a.ended_at, active: !a.ended_at, assigned_by: clone(a.assigned_by) }
}
/** Account in the shape of the admin user list (= admin/services.user_payload). */
function staffPayload(acct) {
  const patient = acct.patient_id ? state.patients.find((x) => x.id === acct.patient_id) : null
  return {
    id: acct.id, email: acct.email, display_name: acct.display_name, role: acct.role, is_active: acct.is_active,
    must_change_password: acct.must_change_password, locked: false, locked_until: null, last_login_at: acct.last_login_at,
    created_at: acct.created_at ?? '2026-09-01T01:00:00.000Z', nurse_profile: clone(acct.nurse_profile),
    patient: patient ? { id: patient.id, patient_code: patient.patient_code } : null,
    active_sessions: sessionsOf(acct).length,
    ...(acct.role === 'nurse' ? { active_patient_count: state.assignments.filter((a) => a.nurse_id === acct.id && !a.ended_at).length } : {}),
  }
}

// ------------------------------------------------------------------ validation (= _profile_values)
function profileValues(body, partial) {
  const details = []
  const values = {}
  for (const f of FORBIDDEN_FIELDS) if (f in body) details.push({ field: f, issue: 'identity numbers are not stored; the patient code identifies the patient' })
  for (const f of ['patient_code', 'id', 'is_demo', 'user_id']) if (f in body) details.push({ field: f, issue: 'cannot be set' })
  const has = (k) => !partial || k in body
  if (has('display_name')) {
    const v = body.display_name
    if (typeof v !== 'string' || !v.trim() || v.trim().length > 100) details.push({ field: 'display_name', issue: 'is required (at most 100 characters)' })
    else values.display_name = v.trim()
  }
  if (has('gender')) {
    if (!GENDERS.includes(body.gender)) details.push({ field: 'gender', issue: `must be one of: ${GENDERS.join(', ')}` })
    else values.gender = body.gender
  }
  if (has('date_of_birth')) {
    const v = body.date_of_birth
    const ok = typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v))
    const today = localToday()
    const oldest = `${Number(today.slice(0, 4)) - 120}${today.slice(4)}`
    if (!ok || v >= today || v < oldest) details.push({ field: 'date_of_birth', issue: 'must be a past date within 120 years (YYYY-MM-DD)' })
    else values.date_of_birth = v
  }
  if ('height_cm' in body) {
    const v = body.height_cm
    if (v !== null && (typeof v !== 'number' || v < 30 || v > 250)) details.push({ field: 'height_cm', issue: 'must be between 30 and 250' })
    else values.height_cm = v
  }
  if ('blood_type' in body) {
    if (body.blood_type !== null && !BLOOD_TYPES.includes(body.blood_type)) details.push({ field: 'blood_type', issue: `must be one of: ${BLOOD_TYPES.join(', ')}` })
    else values.blood_type = body.blood_type
  }
  if ('allergies' in body) {
    const v = body.allergies
    if (v !== null && (typeof v !== 'string' || v.length > 500)) details.push({ field: 'allergies', issue: 'must be text of at most 500 characters' })
    else values.allergies = typeof v === 'string' ? v.trim() || null : null
  }
  if ('baseline_ecog' in body) {
    const v = body.baseline_ecog
    if (v !== null && (!Number.isInteger(v) || v < 0 || v > 5)) details.push({ field: 'baseline_ecog', issue: 'must be an integer from 0 to 5' })
    else values.baseline_ecog = v
  }
  if ('timezone' in body) {
    let ok = typeof body.timezone === 'string'
    try {
      if (ok) new Intl.DateTimeFormat('en', { timeZone: body.timezone })
    } catch {
      ok = false
    }
    if (!ok || !body.timezone.includes('/')) details.push({ field: 'timezone', issue: 'must be an IANA timezone, e.g. Asia/Taipei' })
    else values.timezone = body.timezone
  }
  const known = new Set([...PROFILE_FIELDS, ...FORBIDDEN_FIELDS, 'patient_code', 'id', 'is_demo', 'user_id', 'account'])
  for (const k of Object.keys(body).filter((x) => !known.has(x)).sort()) details.push({ field: k, issue: 'is not a patient profile field' })
  if (details.length) throw invalid(details, '病人資料有誤')
  return values
}
function accountEmail(raw, field = 'account.email') {
  if (typeof raw !== 'string' || !EMAIL.test(raw.trim()) || raw.trim().length > 255) {
    throw invalid([{ field, issue: 'must be a valid email address' }], '帳號資料有誤')
  }
  const email = raw.trim().toLowerCase()
  if (state.accounts[email]) throw conflict('這個 email 已經有帳號', [{ field, issue: 'is already registered' }])
  return email
}

// ------------------------------------------------------------------ blank dashboard / caseload row for new patients
function blankDashboard(p) {
  const today = localToday()
  const dates = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(`${today}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() - 13 + i)
    return d.toISOString().slice(0, 10)
  })
  const series = (key, label) => ({ key, label, higher_is_worse: true, points: dates.map((date) => ({ date, cycle_number: null, cycle_day: null, value: null })) })
  const at = nowIso()
  return {
    patient_id: p.id,
    generated_at: at,
    widgets: {
      'patient-summary': { patient_id: p.id, patient_code: p.patient_code, display_name: p.display_name, age: age(p), gender: p.gender, care_alerts: [] },
      'risk-summary': {
        date: today, level: 'low', status: 'stable', title: '今天狀況穩定', message: '請繼續每天記錄症狀和生命徵象，有不舒服隨時回報。', reasons: [], in_nadir: false,
        today: { symptom_reported: false, last_symptom_report_at: null, symptoms: [], vitals_recorded: false, last_vitals_at: null, vital_flags: [],
          alerts: { total: 0, open: 0, critical: 0, items: [] } },
        actions: [{ code: 'report_symptoms', label: '回報今天的症狀' }, { code: 'measure_vitals', label: '量測並記錄生命徵象' }],
      },
      'today-schedule': { date: today, server_time: at, appointments: [], highlight_instructions: [], quick_contact: { key: 'leave', label: '請假專線', phone: '07-000-0000' } },
      'treatment-progress': null,
      'latest-vitals': { temperature_c: null, heart_rate_bpm: null, blood_pressure: null, respiratory_rate: null, spo2_pct: null, weight_kg: null },
      'lab-summary': { last_collected_at: null, message: '最近 30 天還沒有檢驗結果。', items: [] },
      'symptom-trend': { bucket: 'day', agg: 'max', from: dates[0], to: today, series: [series('pain', '疼痛'), series('nausea', '噁心'), series('fatigue', '疲倦')], cycle_markers: [] },
      'symptom-quick-report': { form: { code: 'daily_chemo_check', name: '化療每日症狀自評', version: 1, item_count: 4 }, reported_today: false, today_record_id: null, last_report: null },
      notifications: { items: [], unread_count: 0 },
      'nurse-view': {
        care_team: [], risk: { level: 'low', reasons: [] }, last_report_at: null, hours_since_last_report: null,
        pending_symptom_reviews: { count: 0, items: [] }, unacknowledged_alerts: { count: 0, items: [] },
        pending_assessment_signoff: { count: 0, items: [] }, latest_assessment: null,
      },
    },
  }
}
/** Make a mock-created patient a first-class patient for the other mock modules (no data yet). */
function register(p) {
  if (!patientDashboards[p.id]) patientDashboards[p.id] = blankDashboard(p)
  if (!nurseOverview.caseload.data.some((r) => r.patient_id === p.id)) {
    nurseOverview.caseload.data.push({
      patient_id: p.id, patient_code: p.patient_code, display_name: p.display_name, risk_level: 'low', risk_reasons: [], cycle: null,
      last_report_at: null, hours_since_last_report: null, pending_review_count: 0, unacknowledged_alert_count: 0,
      unacknowledged_critical_count: 0, latest_alert_at: null, care_alert_types: [],
    })
  }
}
/** Keep the other mock modules' copies of a patient's name / care alerts / care team in step. */
function sync(p) {
  const w = patientDashboards[p.id]?.widgets
  if (w) {
    Object.assign(w['patient-summary'], { display_name: p.display_name, gender: p.gender, age: age(p),
      care_alerts: p.care_alerts.filter((a) => a.is_active).map(({ alert_type, body_site, description, severity }) => ({ alert_type, body_site, description, severity })) })
    if (w['nurse-view']) {
      w['nurse-view'].care_team = careTeam(p).nurses.map((n) => ({ nurse_id: n.id, display_name: n.display_name, is_primary: n.is_primary, assigned_at: n.assigned_at }))
    }
  }
  const row = nurseOverview.caseload.data.find((r) => r.patient_id === p.id)
  if (row) Object.assign(row, { display_name: p.display_name, care_alert_types: [...new Set(p.care_alerts.filter((a) => a.is_active).map((a) => a.alert_type))] })
  if (p.account_email && state.accounts[p.account_email]) state.accounts[p.account_email].display_name = p.display_name
}
for (const p of state.patients) if (!p.deleted) register(p) // after a reload

// ------------------------------------------------------------------ patients
function nextCode() {
  const max = Math.max(0, ...state.patients.map((p) => Number(/^P(\d{5})$/.exec(p.patient_code)?.[1] ?? 0)))
  return `P${String(max + 1).padStart(5, '0')}`
}

export function mockListPatients({ q = null, assigned = 'any', sort = 'patient_code', page = 1, perPage = 20 } = {}) {
  const user = current('nurse', 'admin')
  let rows = state.patients.filter((p) => !p.deleted)
  if (user.role === 'nurse') rows = rows.filter((p) => canView(user, p))
  else if (assigned !== 'any') rows = rows.filter((p) => (activeAssignments(p.id).length > 0) === (assigned === 'true'))
  if (q?.trim()) {
    const s = q.trim().toLowerCase()
    rows = rows.filter((p) => p.patient_code.toLowerCase().includes(s) || p.display_name.toLowerCase().includes(s))
  }
  const key = { patient_code: (p) => p.patient_code, display_name: (p) => p.display_name, created_at: (p) => p.created_at }[sort]
  rows.sort((a, b) => (sort === 'created_at' ? byTimeDesc(a.created_at, b.created_at) : key(a).localeCompare(key(b))) || a.patient_code.localeCompare(b.patient_code))
  return { data: rows.slice((page - 1) * perPage, page * perPage).map((p) => listItem(p, user)), meta: { page, per_page: perPage, total: rows.length } }
}

export function mockCreatePatient(body) {
  const user = current('nurse', 'admin')
  const values = profileValues(body, false)
  if (body.account != null && (typeof body.account !== 'object' || Array.isArray(body.account))) {
    throw invalid([{ field: 'account', issue: 'must be an object with email' }])
  }
  const email = body.account ? accountEmail(body.account.email) : null
  const p = {
    id: uuid(), patient_code: nextCode(), height_cm: null, blood_type: null, allergies: null, baseline_ecog: null, timezone: TZ,
    ...values, is_demo: true, created_at: nowIso(), created_by: person(user), deleted: false, account_email: null,
    care_alerts: [], diagnoses: [], current_cycle: null,
  }
  state.patients.push(p)
  register(p)
  const password = email ? createAccount(p, email) : null
  save()
  return {
    data: { id: p.id, patient_code: p.patient_code, display_name: p.display_name, is_demo: true, created_at: p.created_at,
      account: email ? { email, temporary_password: password, must_change_password: true } : null },
  }
}

function createAccount(p, email) {
  if (p.account_email) throw conflict('這位病人已經有登入帳號')
  const password = temporaryPassword()
  state.accounts[email] = { id: `mock-user-${uuid()}`, display_name: p.display_name, email, role: 'patient', patient_id: p.id,
    password, must_change_password: true, is_active: true, last_login_at: null, nurse_profile: null }
  p.account_email = email
  return password
}

export function mockCreateAccount(patientId, body) {
  const p = mockAccessPatient(patientId, 'nurse', 'admin')
  const email = accountEmail(body?.email, 'email')
  const password = createAccount(p, email)
  save()
  return { data: { patient_id: p.id, email, temporary_password: password, must_change_password: true } }
}

export function mockGetPatient(patientId) {
  const p = mockAccessPatient(patientId)
  return { data: detail(p, current()) }
}

export function mockUpdatePatient(patientId, body) {
  const p = mockAccessPatient(patientId, 'nurse', 'admin')
  if ('account' in body) throw invalid([{ field: 'account', issue: 'use POST /patients/{id}/account' }])
  Object.assign(p, profileValues(body, true))
  sync(p)
  save()
  return { data: detail(p, current()) }
}

export function mockListCancerTypes() {
  current()
  return { data: clone(mockCancerTypes) }
}

// ------------------------------------------------------------------ care alerts
function alertValues(body, partial, currentType = null) {
  const details = []
  const values = {}
  if (!partial || 'alert_type' in body) {
    if (!ALERT_TYPES.includes(body.alert_type)) details.push({ field: 'alert_type', issue: `must be one of: ${ALERT_TYPES.join(', ')}` })
    else values.alert_type = body.alert_type
  }
  if (!partial || 'description' in body) {
    const d = body.description
    if (typeof d !== 'string' || !d.trim() || d.trim().length > 255) details.push({ field: 'description', issue: 'is required (at most 255 characters)' })
    else values.description = d.trim()
  }
  if (!partial || 'severity' in body) {
    if (!SEVERITIES.includes(body.severity)) details.push({ field: 'severity', issue: `must be one of: ${SEVERITIES.join(', ')}` })
    else values.severity = body.severity
  }
  if ('body_site' in body) values.body_site = body.body_site
  if (partial && 'is_active' in body) {
    if (typeof body.is_active !== 'boolean') details.push({ field: 'is_active', issue: 'must be true or false' })
    else values.is_active = body.is_active
  }
  const kind = values.alert_type ?? currentType
  if (kind === 'limb_restriction' && ('body_site' in values || !partial) && !BODY_SITES.includes(values.body_site)) {
    details.push({ field: 'body_site', issue: `limb restrictions need one of: ${BODY_SITES.join(', ')}` })
  }
  if (details.length) throw invalid(details, '注意事項內容有誤')
  return values
}
export function mockListCareAlerts(patientId, { includeInactive = false } = {}) {
  const p = mockAccessPatient(patientId)
  const staff = current().role !== 'patient'
  return { data: clone(p.care_alerts.filter((a) => a.is_active || (includeInactive && staff))) }
}
export function mockCreateCareAlert(patientId, body) {
  const p = mockAccessPatient(patientId, 'nurse')
  const alert = { id: nextId(), body_site: null, ...alertValues(body, false), is_active: true }
  p.care_alerts.push(alert)
  sync(p)
  save()
  return { data: clone(alert) }
}
export function mockUpdateCareAlert(patientId, alertId, body) {
  const p = mockAccessPatient(patientId, 'nurse')
  const alert = p.care_alerts.find((a) => a.id === alertId)
  if (!alert) throw notFound('注意事項')
  Object.assign(alert, alertValues(body, true, alert.alert_type))
  sync(p)
  save()
  return { data: clone(alert) }
}

// ------------------------------------------------------------------ diagnoses
function diagnosisValues(body, partial) {
  const details = []
  const values = {}
  if (!partial || 'cancer_type_code' in body) {
    const t = mockCancerTypes.find((x) => x.code === body.cancer_type_code)
    if (!t) details.push({ field: 'cancer_type_code', issue: 'must be an active cancer type code (GET /patients/cancer-types)' })
    else values.cancer_type = clone(t)
  }
  if (!partial || 'diagnosis_date' in body) {
    const v = body.diagnosis_date
    if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v) || v > localToday()) {
      details.push({ field: 'diagnosis_date', issue: 'is required and cannot be in the future (YYYY-MM-DD)' })
    } else values.diagnosis_date = v
  }
  for (const [key, limit] of [['stage', 10], ['histology', 100], ['notes', 2000]]) {
    if (key in body) {
      const v = body[key]
      if (v !== null && (typeof v !== 'string' || v.length > limit)) details.push({ field: key, issue: `must be text of at most ${limit} characters` })
      else values[key] = typeof v === 'string' ? v.trim() || null : null
    }
  }
  if (!partial || 'status' in body) {
    const s = body.status ?? 'active'
    if (!DX_STATUS.includes(s)) details.push({ field: 'status', issue: `must be one of: ${DX_STATUS.join(', ')}` })
    else values.status = s
  }
  if ('is_primary' in body) {
    if (typeof body.is_primary !== 'boolean') details.push({ field: 'is_primary', issue: 'must be true or false' })
    else values.is_primary = body.is_primary
  }
  if (details.length) throw invalid(details, '診斷資料有誤')
  return values
}
export function mockListDiagnoses(patientId) {
  return { data: clone(mockAccessPatient(patientId).diagnoses) }
}
export function mockCreateDiagnosis(patientId, body) {
  const p = mockAccessPatient(patientId, 'nurse')
  const dx = { id: nextId(), stage: null, histology: null, notes: null, is_primary: p.diagnoses.length === 0, ...diagnosisValues(body, false) }
  if (dx.is_primary) p.diagnoses.forEach((d) => { d.is_primary = false })
  p.diagnoses.push(dx)
  save()
  return { data: clone(dx) }
}
export function mockUpdateDiagnosis(patientId, diagnosisId, body) {
  const p = mockAccessPatient(patientId, 'nurse')
  const dx = p.diagnoses.find((d) => d.id === diagnosisId)
  if (!dx) throw notFound('診斷')
  const values = diagnosisValues(body, true)
  if (values.is_primary) p.diagnoses.forEach((d) => { d.is_primary = false })
  Object.assign(dx, values)
  save()
  return { data: clone(dx) }
}

// ------------------------------------------------------------------ assignments
const ASSIGN_ORDER = (a, b) => Number(!b.ended_at) - Number(!a.ended_at) || byTimeDesc(a.assigned_at, b.assigned_at)
export function mockListAssignments(patientId) {
  const p = mockAccessPatient(patientId, 'nurse', 'admin')
  return { data: state.assignments.filter((a) => a.patient_id === p.id).sort(ASSIGN_ORDER).map(assignmentPayload) }
}
export function mockCreateAssignment(patientId, body) {
  const admin = current('admin')
  const p = mockAccessPatient(patientId, 'admin')
  const nurse = accountById(body?.nurse_id)
  if (!nurse || nurse.role !== 'nurse' || !nurse.is_active) throw invalid([{ field: 'nurse_id', issue: 'must be the id of an active nurse account' }], '指派資料有誤')
  const isPrimary = body.is_primary ?? false
  if (typeof isPrimary !== 'boolean') throw invalid([{ field: 'is_primary', issue: 'must be true or false' }], '指派資料有誤')
  const active = activeAssignments(p.id)
  if (active.some((a) => a.nurse_id === nurse.id)) throw conflict('這位護理師已經負責這位病人')
  if (isPrimary) active.forEach((a) => { a.is_primary = false })
  const a = { id: nextId(), patient_id: p.id, nurse_id: nurse.id, is_primary: isPrimary, assigned_at: nowIso(), ended_at: null, assigned_by: person(admin) }
  state.assignments.push(a)
  sync(p)
  save()
  return { data: assignmentPayload(a) }
}
export function mockEndAssignment(patientId, assignmentId) {
  const p = mockAccessPatient(patientId, 'admin')
  const a = state.assignments.find((x) => x.id === assignmentId && x.patient_id === p.id)
  if (!a) throw notFound('指派')
  if (a.ended_at) throw conflict('這個指派已經結束')
  Object.assign(a, { ended_at: nowIso(), is_primary: false })
  sync(p)
  save()
  return { data: assignmentPayload(a) }
}

// ------------------------------------------------------------------ staff (admin)
/** GET /admin/users: role nurse (default) | patient | admin | all; status any | active | inactive | locked. */
export function mockListStaff({ q = null, role = 'nurse', status = 'any' } = {}) {
  current('admin')
  const s = q?.trim().toLowerCase()
  const rows = Object.values(state.accounts)
    .filter((a) => (role === 'all' || a.role === role) && (!s || a.display_name.toLowerCase().includes(s) || a.email.includes(s))
      && (status === 'any' || (status === 'active' ? a.is_active : status === 'inactive' ? !a.is_active : false)))
    .sort((a, b) => a.display_name.localeCompare(b.display_name))
  return { data: rows.map(staffPayload) }
}
/** PATCH /admin/users/{id}: is_active (a disabled account stops working at once), unlock, display_name, nurse_profile. */
export function mockUpdateAccount(id, body = {}) {
  const admin = current('admin')
  const acct = accountById(id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'User not found') })()
  const details = []
  for (const k of ['role', 'email', 'password', 'temporary_password']) if (k in body) details.push({ field: k, issue: 'cannot be changed here' })
  if ('is_active' in body && typeof body.is_active !== 'boolean') details.push({ field: 'is_active', issue: 'must be true or false' })
  if ('display_name' in body && (typeof body.display_name !== 'string' || !body.display_name.trim() || body.display_name.length > 100)) details.push({ field: 'display_name', issue: 'is required (at most 100 characters)' })
  if ('nurse_profile' in body && (acct.role !== 'nurse' || typeof body.nurse_profile !== 'object')) details.push({ field: 'nurse_profile', issue: 'only for nurse accounts (object with staff_code / department / title)' })
  for (const k of Object.keys(body).filter((x) => !['is_active', 'unlock', 'display_name', 'nurse_profile', 'role', 'email', 'password', 'temporary_password'].includes(x)).sort()) {
    details.push({ field: k, issue: 'is not a supported field' })
  }
  if (details.length) throw invalid(details, '帳號資料有誤')
  const changed = []
  if (body.is_active === false && acct.is_active) {
    if (acct.id === admin.id) throw conflict('不能停用自己的帳號')
    if (acct.role === 'admin' && Object.values(state.accounts).filter((a) => a.role === 'admin' && a.is_active).length <= 1) throw conflict('至少要保留一個啟用中的管理者帳號')
  }
  if ('is_active' in body && body.is_active !== acct.is_active) {
    acct.is_active = body.is_active
    changed.push('is_active')
    if (!acct.is_active && endSessions(acct)) changed.push('sessions_revoked') // re-enabling does not revive them
  }
  if ('display_name' in body && body.display_name.trim() !== acct.display_name) {
    acct.display_name = body.display_name.trim()
    const p = acct.patient_id && state.patients.find((x) => x.id === acct.patient_id)
    if (p) {
      p.display_name = acct.display_name
      sync(p)
    }
    changed.push('display_name')
  }
  if (body.nurse_profile) {
    const code = body.nurse_profile.staff_code?.trim()
    if (code && code !== acct.nurse_profile?.staff_code && Object.values(state.accounts).some((a) => a.nurse_profile?.staff_code === code)) {
      throw conflict('員工編號已被使用', [{ field: 'nurse_profile.staff_code', issue: 'is already used' }])
    }
    acct.nurse_profile = { ...(acct.nurse_profile ?? { staff_code: null, department: null, title: null }), ...body.nurse_profile }
    changed.push('nurse_profile')
  }
  save()
  return { data: staffPayload(acct), changed }
}
/** GET /admin/users/{id}/sessions */
export function mockUserSessions(id) {
  current('admin')
  const acct = accountById(id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'User not found') })()
  return { data: sessionRows(acct, null) }
}
/** POST /admin/users/{id}/revoke-sessions (not yourself) → { revoked }. */
export function mockRevokeSessions(id) {
  const admin = current('admin')
  const acct = accountById(id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'User not found') })()
  if (acct.id === admin.id) throw conflict('要結束自己的登入請使用「登出」或「登入裝置」')
  const n = endSessions(acct)
  save()
  return { data: { revoked: n } }
}
/** POST /admin/users/{id}/password-reset: new temporary password (once), must change, sessions end. */
export function mockResetPassword(id) {
  const admin = current('admin')
  const acct = accountById(id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'User not found') })()
  if (acct.id === admin.id) throw conflict('請用「修改密碼」變更自己的密碼')
  const password = temporaryPassword()
  Object.assign(acct, { password, must_change_password: true })
  const n = endSessions(acct)
  save()
  return { data: { ...staffPayload(acct), temporary_password: password, sessions_revoked: n } }
}
/** Admin overview counts from the care-team state (the mock admin module adds alerts / reviews). */
export function mockCareTeamCounts() {
  const live = state.patients.filter((p) => !p.deleted)
  const nurses = Object.values(state.accounts).filter((a) => a.role === 'nurse')
  return {
    patients: { total: live.length, unassigned: live.filter((p) => !activeAssignments(p.id).length).length, without_account: live.filter((p) => !p.account_email).length },
    nurses: { total: nurses.length, active: nurses.filter((a) => a.is_active).length, must_change_password: nurses.filter((a) => a.must_change_password).length },
    accounts: { inactive: Object.values(state.accounts).filter((a) => !a.is_active).length, locked: 0 },
    assignments: { active: state.assignments.filter((a) => !a.ended_at).length },
  }
}
export function mockCreateNurse(body) {
  current('admin')
  const details = []
  const role = body.role ?? 'nurse'
  if (!['nurse', 'admin'].includes(role)) details.push({ field: 'role', issue: "must be 'nurse' or 'admin' (patients: POST /patients/{id}/account)" })
  if (role === 'admin' && body.nurse_profile && Object.keys(body.nurse_profile).length) details.push({ field: 'nurse_profile', issue: 'only for nurse accounts' })
  const name = body.display_name
  if (typeof name !== 'string' || !name.trim() || name.trim().length > 100) details.push({ field: 'display_name', issue: 'is required (at most 100 characters)' })
  if ('temporary_password' in body || 'password' in body) details.push({ field: 'temporary_password', issue: 'is generated by the system' })
  const profile = body.nurse_profile ?? {}
  for (const [key, limit] of [['staff_code', 30], ['department', 100], ['title', 50]]) {
    const v = profile[key]
    if (v != null && (typeof v !== 'string' || v.length > limit)) details.push({ field: `nurse_profile.${key}`, issue: `must be text of at most ${limit} characters` })
  }
  if (details.length) throw invalid(details, '帳號資料有誤')
  const email = accountEmail(body.email, 'email')
  const staffCode = profile.staff_code?.trim() || null
  if (staffCode && Object.values(state.accounts).some((a) => a.nurse_profile?.staff_code === staffCode)) {
    throw conflict('員工編號已被使用', [{ field: 'nurse_profile.staff_code', issue: 'is already used' }])
  }
  const password = temporaryPassword()
  const acct = {
    id: `mock-user-${uuid()}`, display_name: name.trim(), email, role, patient_id: null, password,
    must_change_password: true, is_active: true, last_login_at: null, created_at: nowIso(),
    nurse_profile: role === 'nurse' ? { staff_code: staffCode, department: profile.department || null, title: profile.title || null } : null,
  }
  state.accounts[email] = acct
  save()
  return { data: { ...staffPayload(acct), temporary_password: password } }
}

// ------------------------------------------------------------------ nurse caseload (dashboard widget)
const RISK_KEYS = ['high', 'medium', 'low']
/** The caseload widget for the signed-in nurse: only currently assigned patients. */
export function mockNurseOverview() {
  const user = current('nurse')
  const rows = nurseOverview.caseload.data
    .filter((r) => canView(user, state.patients.find((p) => p.id === r.patient_id)))
    .map((r) => ({ ...clone(r), is_primary_nurse: activeAssignments(r.patient_id).some((a) => a.nurse_id === user.id && a.is_primary) }))
  const acct = account(user)
  return {
    nurse: { display_name: acct.display_name, department: acct.nurse_profile?.department ?? null },
    caseload: {
      data: rows,
      meta: { total: rows.length, sort: nurseOverview.caseload.meta.sort, ...Object.fromEntries(RISK_KEYS.map((k) => [k, rows.filter((r) => r.risk_level === k).length])) },
    },
  }
}

/** Test / demo helper: forget every change (fresh seed). */
export function resetMockCareTeam() {
  Object.assign(state, seed())
  save()
}
