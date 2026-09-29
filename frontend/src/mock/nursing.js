/**
 * Mock nursing assessments (VITE_USE_MOCK=true), in the shapes of /api/v1/nursing-assessments…,
 * with the backend rules (app/modules/nursing/services.py): drafts edited / signed by their
 * author, signed ones locked (422 RECORD_LOCKED), corrections as new draft versions that mark
 * the original `amended` when signed, item follow-up. Staff only.
 *
 * It also feeds the mock timeline (NURSING_ASSESSMENT events) and the dashboard's nurse view
 * (latest assessment, drafts waiting for sign-off, the assessment's risk contribution — the same
 * rule as the backend risk summary). State survives a reload (sessionStorage). Synthetic data only.
 */
import { nowIso } from '@/mock/clock'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockPerson } from '@/mock/patients'

const STATE_KEY = 'ccp.mock.nursing'
const P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'
const TYPES = ['initial', 'pre_chemo', 'during_infusion', 'post_chemo', 'follow_up', 'phone_follow_up']
const TYPE_TEXT = { initial: '初次評估', pre_chemo: '化療前評估', during_infusion: '輸注中評估', post_chemo: '化療後評估', follow_up: '追蹤評估', phone_follow_up: '電話追蹤' }
const CONDITIONS = ['stable', 'concern', 'urgent']
const RISKS = ['low', 'medium', 'high']
const READINESS = ['ready', 'hold', 'delay', 'refer_physician']
const ITEM_TYPES = ['problem', 'goal', 'intervention', 'education', 'referral']
const ITEM_STATUS = ['open', 'in_progress', 'resolved', 'done']
const PRIORITIES = ['high', 'medium', 'low']
const SOAP = ['subjective', 'objective', 'assessment', 'plan']
const FIELDS = ['assessment_type', 'assessed_at', 'appointment_id', 'ecog_status', 'overall_condition', 'risk_level', 'chemo_readiness', ...SOAP, 'next_follow_up_at', 'items']
const invalid = (details, message = '護理評估內容有誤') => new MockApiError(400, 'VALIDATION_ERROR', message, details)

function seed() {
  return {
    assessments: [{
      id: 7, patient_id: P2, assessed_by: 'mock-user-nurse01', assessed_at: '2026-09-22T06:00:00.000Z', assessment_type: 'phone_follow_up',
      appointment_id: null, ecog_status: 1, overall_condition: 'concern', risk_level: 'medium', chemo_readiness: null,
      subjective: '病人表示噁心，止吐藥吃了會想睡。', objective: null, assessment: '化療後噁心，止吐藥使用時機不當。',
      plan: '已電話衛教止吐藥使用時機，隔日追蹤。', next_follow_up_at: null, sign_status: 'signed', signed_at: '2026-09-22T06:10:00.000Z',
      record_status: 'final', amends_id: null, cycle_id: 12, cycle_day: 7, created_at: '2026-09-22T06:00:00.000Z', updated_at: '2026-09-22T06:10:00.000Z',
      items: [],
    }],
    nextId: 1000,
    idempotency: {},
  }
}
let restored = false
function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.assessments) {
      restored = true
      return saved
    }
  } catch {
    // storage unavailable: start from the seed
  }
  return seed()
}
const state = load()
const nextId = () => ++state.nextId

let cycleContext = () => ({ cycle_id: null, cycle_day: null }) // set by the chemotherapy mock
let appointmentOf = () => null // set by the appointments mock
export function linkNursing({ cycle, appointment }) {
  if (cycle) cycleContext = cycle
  if (appointment) appointmentOf = appointment
}

// ------------------------------------------------------------------ serialize (= assessment_payload)
function payload(a, summary = false) {
  const p = mockAccessPatient(a.patient_id)
  const newer = state.assessments.find((x) => x.amends_id === a.id)
  const data = {
    id: a.id, patient_id: a.patient_id, patient_code: p.patient_code, patient_name: p.display_name,
    assessment_type: a.assessment_type, assessment_type_text: TYPE_TEXT[a.assessment_type] ?? '護理評估', assessed_at: a.assessed_at,
    assessed_by: mockPerson(a.assessed_by), sign_status: a.sign_status, signed_at: a.signed_at, record_status: a.record_status,
    amends_id: a.amends_id, amended_by_id: newer?.id ?? null, risk_level: a.risk_level, overall_condition: a.overall_condition,
    chemo_readiness: a.chemo_readiness, cycle_id: a.cycle_id, cycle_day: a.cycle_day, appointment_id: a.appointment_id,
  }
  if (!summary) {
    Object.assign(data, {
      ecog_status: a.ecog_status, ...Object.fromEntries(SOAP.map((k) => [k, a[k]])), next_follow_up_at: a.next_follow_up_at,
      items: a.items.map((i) => ({ id: i.id, item_type: i.item_type, code: i.code, description: i.description, priority: i.priority,
        item_status: i.item_status, resolved_at: i.resolved_at })),
      created_at: a.created_at, updated_at: a.updated_at,
    })
  }
  return data
}
const find = (id) => state.assessments.find((a) => a.id === id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'Nursing assessment not found') })()
export const mockAssessmentPatient = (id) => state.assessments.find((a) => a.id === id)?.patient_id ?? null

// ------------------------------------------------------------------ read
export function mockListAssessments(patientId = null, { signStatus = null, includeHistory = false } = {}, visible = () => true) {
  const user = mockCurrentUser('nurse', 'admin')
  if (signStatus && !['draft', 'signed'].includes(signStatus)) throw invalid([{ field: 'sign_status', issue: 'must be draft or signed' }], '查詢條件有誤')
  let rows
  if (patientId) {
    const pid = mockAccessPatient(patientId).id
    rows = state.assessments.filter((a) => a.patient_id === pid)
  } else {
    if (user.role !== 'nurse') throw invalid([{ field: 'patient_id', issue: 'is required' }], 'patient_id is required')
    rows = state.assessments.filter((a) => a.assessed_by === user.id && visible(a.patient_id))
  }
  rows = rows.filter((a) => (!signStatus || a.sign_status === signStatus) && (includeHistory || a.record_status === 'final'))
    .sort((a, b) => Date.parse(b.assessed_at) - Date.parse(a.assessed_at) || b.id - a.id)
  return { data: rows.map((a) => payload(a, true)) }
}
export function mockGetAssessment(id) {
  mockCurrentUser('nurse', 'admin')
  return { data: payload(find(id)) }
}
export function mockAssessmentVersions(id) {
  mockCurrentUser('nurse', 'admin')
  let first = find(id)
  while (first.amends_id) first = find(first.amends_id)
  const chain = []
  for (let cur = first; cur; cur = state.assessments.find((x) => x.amends_id === cur.id)) chain.push(cur)
  return { data: chain.map((a) => payload(a)) }
}

// ------------------------------------------------------------------ parse (= services._values)
function parseIso(raw, field, details) {
  const t = typeof raw === 'string' && /([zZ]|[+-]\d{2}:\d{2})$/.test(raw) ? Date.parse(raw) : NaN
  if (Number.isNaN(t)) {
    details.push({ field, issue: 'must be an ISO 8601 datetime with timezone' })
    return undefined
  }
  return new Date(t).toISOString()
}
function values(patientId, body, { partial, window = true }) {
  const details = []
  const out = {}
  const has = (k) => k in body
  const choice = (k, list, required = false) => {
    if (!has(k)) {
      if (required) details.push({ field: k, issue: `is required (one of: ${list.join(', ')})` })
      return
    }
    if (body[k] === null && !required) out[k] = null
    else if (!list.includes(body[k])) details.push({ field: k, issue: `must be one of: ${list.join(', ')}` })
    else out[k] = body[k]
  }
  if (!partial || has('assessment_type')) choice('assessment_type', TYPES, true)
  if (has('assessed_at') && body.assessed_at != null) {
    const at = parseIso(body.assessed_at, 'assessed_at', details)
    if (at !== undefined) {
      if (Date.parse(at) > Date.now() + 5 * 60000 || (window && Date.parse(at) < Date.now() - 7 * 86400000)) {
        details.push({ field: 'assessed_at', issue: 'must be within the last 7 days and not in the future' })
      } else out.assessed_at = at
    }
  }
  if (has('ecog_status')) {
    const v = body.ecog_status
    if (v !== null && (!Number.isInteger(v) || v < 0 || v > 5)) details.push({ field: 'ecog_status', issue: 'must be an integer from 0 to 5' })
    else out.ecog_status = v
  }
  choice('overall_condition', CONDITIONS)
  choice('risk_level', RISKS)
  choice('chemo_readiness', READINESS)
  for (const k of SOAP) {
    if (!has(k)) continue
    const v = body[k]
    if (v != null && (typeof v !== 'string' || v.trim().length > 5000)) details.push({ field: k, issue: 'must be text of at most 5000 characters' })
    else out[k] = typeof v === 'string' ? v.trim() || null : null
  }
  if (has('next_follow_up_at')) {
    if (body.next_follow_up_at === null) out.next_follow_up_at = null
    else {
      const at = parseIso(body.next_follow_up_at, 'next_follow_up_at', details)
      if (at !== undefined) out.next_follow_up_at = at
    }
  }
  if (has('appointment_id')) {
    if (body.appointment_id !== null && appointmentOf(body.appointment_id) !== patientId) details.push({ field: 'appointment_id', issue: "must be one of this patient's appointments" })
    else out.appointment_id = body.appointment_id
  }
  if (has('items')) {
    if (!Array.isArray(body.items) || body.items.length > 30) details.push({ field: 'items', issue: 'must be a list of at most 30 items' })
    else {
      out.items = body.items.map((item, n) => {
        const at = `items[${n}]`
        if (!ITEM_TYPES.includes(item?.item_type)) details.push({ field: `${at}.item_type`, issue: `is required (one of: ${ITEM_TYPES.join(', ')})` })
        if (typeof item?.description !== 'string' || !item.description.trim() || item.description.trim().length > 2000) details.push({ field: `${at}.description`, issue: 'is required (at most 2000 characters)' })
        if (item?.priority != null && !PRIORITIES.includes(item.priority)) details.push({ field: `${at}.priority`, issue: `must be one of: ${PRIORITIES.join(', ')}` })
        if (item?.item_status != null && !ITEM_STATUS.includes(item.item_status)) details.push({ field: `${at}.item_status`, issue: `must be one of: ${ITEM_STATUS.join(', ')}` })
        return { id: nextId(), item_type: item?.item_type, code: item?.code?.trim() || null, description: (item?.description ?? '').trim(),
          priority: item?.priority ?? null, item_status: item?.item_status ?? 'open', resolved_at: null }
      })
    }
  }
  return { out, details }
}
function idempotent(key, body, create) {
  if (!key) return create()
  const user = mockCurrentUser()
  const slot = `${user.id}:${key}`
  const hash = JSON.stringify(body)
  const seen = state.idempotency[slot]
  if (seen) {
    if (seen.hash !== hash) throw new MockApiError(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    if (seen.error) throw new MockApiError(seen.error.status, seen.error.code, 'This request already failed; send a new request')
    return { data: payload(find(seen.id)), replayed: true }
  }
  try {
    const result = create()
    state.idempotency[slot] = { hash, id: result.data.id }
    return result
  } catch (e) {
    if (e instanceof MockApiError && e.status < 500) state.idempotency[slot] = { hash, error: { status: e.status, code: e.code } }
    throw e
  } finally {
    sync()
  }
}

// ------------------------------------------------------------------ write
function createDraft(patientId, userId, body, amends = null) {
  const { out, details } = values(patientId, body, { partial: false, window: !amends })
  for (const k of Object.keys(body).filter((x) => !['patient_id', ...FIELDS].includes(x)).sort()) details.push({ field: k, issue: 'is not a supported field' })
  if (details.length) throw invalid(details)
  const at = out.assessed_at ?? nowIso()
  const a = {
    id: nextId(), patient_id: patientId, assessed_by: userId, appointment_id: null, ecog_status: null, overall_condition: null,
    risk_level: null, chemo_readiness: null, subjective: null, objective: null, assessment: null, plan: null, next_follow_up_at: null,
    items: [], ...out, assessed_at: at, sign_status: 'draft', signed_at: null, record_status: 'final', amends_id: amends?.id ?? null,
    ...cycleContext(patientId, at), created_at: nowIso(), updated_at: nowIso(),
  }
  state.assessments.push(a)
  return a
}
export function mockCreateAssessment(body, idempotencyKey = null) {
  return idempotent(idempotencyKey, body, () => {
    const user = mockCurrentUser('nurse')
    if (typeof body.patient_id !== 'string' || !body.patient_id) throw invalid([{ field: 'patient_id', issue: 'is required' }])
    const pid = mockAccessPatient(body.patient_id).id
    return { data: payload(createDraft(pid, user.id, body)) }
  })
}
const locked = () => new MockApiError(422, 'RECORD_LOCKED', '已簽署的評估不能直接修改，請使用「修正」')
const ensureAuthor = (a, user) => {
  if (a.assessed_by !== user.id) throw new MockApiError(403, 'FORBIDDEN', '只有撰寫這份評估的護理師可以修改或簽署')
}
export function mockUpdateAssessment(id, body) {
  const user = mockCurrentUser('nurse')
  const a = find(id)
  if (a.sign_status === 'signed' || a.record_status !== 'final') throw locked()
  ensureAuthor(a, user)
  const { out, details } = values(a.patient_id, body, { partial: true })
  for (const k of Object.keys(body).filter((x) => !FIELDS.includes(x)).sort()) details.push({ field: k, issue: 'is not a supported field' })
  if (details.length) throw invalid(details)
  Object.assign(a, out, { updated_at: nowIso() })
  if ('assessed_at' in out) Object.assign(a, cycleContext(a.patient_id, a.assessed_at))
  sync()
  return { data: payload(a) }
}
export function mockSignAssessment(id) {
  const user = mockCurrentUser('nurse')
  const a = find(id)
  if (a.sign_status === 'signed') throw new MockApiError(409, 'INVALID_STATE', '這份評估已簽署')
  if (a.record_status !== 'final') throw locked()
  ensureAuthor(a, user)
  if (!SOAP.some((k) => (a[k] ?? '').trim())) throw invalid([{ field: 'subjective', issue: 'at least one of S / O / A / P is required before signing' }], '護理評估內容不足')
  Object.assign(a, { sign_status: 'signed', signed_at: nowIso(), updated_at: nowIso() })
  if (a.amends_id) find(a.amends_id).record_status = 'amended'
  sync()
  return { data: payload(a) }
}
export function mockAmendAssessment(id, body, idempotencyKey = null) {
  return idempotent(idempotencyKey, body, () => {
    const user = mockCurrentUser('nurse')
    const orig = find(id)
    if (orig.sign_status !== 'signed') throw new MockApiError(409, 'INVALID_STATE', '草稿請直接修改；只有已簽署的評估需要修正')
    if (orig.record_status !== 'final') throw new MockApiError(409, 'INVALID_STATE', '這份評估已被修正，請對最新的版本操作')
    if (state.assessments.some((x) => x.amends_id === orig.id && x.sign_status === 'draft')) throw new MockApiError(409, 'INVALID_STATE', '這份評估已有尚未簽署的修正版本')
    const details = []
    if (typeof body.amend_reason !== 'string' || !body.amend_reason.trim() || body.amend_reason.trim().length > 500) details.push({ field: 'amend_reason', issue: 'is required (at most 500 characters)' })
    for (const k of Object.keys(body).filter((x) => !['amend_reason', ...FIELDS].includes(x)).sort()) details.push({ field: k, issue: 'is not a supported field' })
    if (details.length) throw invalid(details, '修正內容有誤')
    const base = Object.fromEntries(['assessment_type', 'appointment_id', 'ecog_status', 'overall_condition', 'risk_level', 'chemo_readiness', ...SOAP, 'assessed_at', 'next_follow_up_at']
      .filter((k) => orig[k] != null).map((k) => [k, orig[k]]))
    base.items = orig.items.map(({ item_type, code, description, priority, item_status }) => ({ item_type, code, description, priority, item_status }))
    const { amend_reason: _r, ...rest } = body
    return { data: payload(createDraft(orig.patient_id, user.id, { ...base, ...rest }, orig)) }
  })
}
export function mockUpdateAssessmentItem(id, itemId, body = {}) {
  mockCurrentUser('nurse')
  const a = find(id)
  const item = a.items.find((i) => i.id === itemId)
  if (!item) throw new MockApiError(404, 'NOT_FOUND', 'Assessment item not found')
  if (a.record_status !== 'final') throw new MockApiError(409, 'INVALID_STATE', '這份評估已被修正，請更新最新版本的項目')
  const details = []
  if (!ITEM_STATUS.includes(body.item_status)) details.push({ field: 'item_status', issue: `is required (one of: ${ITEM_STATUS.join(', ')})` })
  for (const k of Object.keys(body).filter((x) => x !== 'item_status').sort()) details.push({ field: k, issue: 'is not a supported field' })
  if (details.length) throw invalid(details, '項目資料有誤')
  Object.assign(item, { item_status: body.item_status, resolved_at: ['resolved', 'done'].includes(body.item_status) ? nowIso() : null })
  sync()
  const { id: iid, item_type, code, description, priority, item_status, resolved_at } = item
  return { data: { id: iid, item_type, code, description, priority, item_status, resolved_at } }
}

/** A symptom review signs an assessment (= the backend review flow). Returns its id. */
export function recordReviewAssessment(patientId, userId, { assessmentType, riskLevel, subjective, plan }) {
  const at = nowIso()
  const a = {
    id: nextId(), patient_id: patientId, assessed_by: userId, assessed_at: at, assessment_type: assessmentType ?? 'phone_follow_up',
    appointment_id: null, ecog_status: null, overall_condition: null, risk_level: riskLevel ?? null, chemo_readiness: null,
    subjective, objective: null, assessment: '症狀回報審閱', plan, next_follow_up_at: null, sign_status: 'signed', signed_at: at,
    record_status: 'final', amends_id: null, ...cycleContext(patientId, at), created_at: at, updated_at: at, items: [],
  }
  state.assessments.push(a)
  sync()
  return a.id
}

// ------------------------------------------------------------------ for the other mock modules
/** Timeline NURSING_ASSESSMENT events (= backend _assessments): final ones; patients signed only, no content. */
export function mockTimelineAssessments(patientId, staff) {
  return state.assessments
    .filter((a) => a.patient_id === patientId && a.record_status === 'final' && (staff || a.sign_status === 'signed'))
    .map((a) => {
      const type = TYPE_TEXT[a.assessment_type] ?? '護理評估'
      const who = mockPerson(a.assessed_by)
      if (!staff) {
        return { event_id: `NURSING_ASSESSMENT:${a.id}`, event_type: 'NURSING_ASSESSMENT', occurred_at: a.assessed_at, title: `護理師${type}`,
          summary: a.assessment_type === 'phone_follow_up' ? '護理師已追蹤您的狀況。' : '護理師已完成評估。', severity: null,
          source: { table: 'nursing_assessments', id: a.id }, cycle_id: a.cycle_id, cycle_day: a.cycle_day,
          detail: { assessment_type: a.assessment_type, assessment_type_text: type } }
      }
      const text = a.plan || a.assessment || a.subjective || ''
      return {
        event_id: `NURSING_ASSESSMENT:${a.id}`, event_type: 'NURSING_ASSESSMENT', occurred_at: a.assessed_at,
        title: `護理評估（${type}）${a.sign_status === 'draft' ? '・草稿' : ''}`,
        summary: text ? `${who?.display_name ?? '護理師'}：${text.slice(0, 80)}${text.length > 80 ? '…' : ''}` : who?.display_name ?? '',
        severity: { high: 'critical', medium: 'warning' }[a.risk_level] ?? null, source: { table: 'nursing_assessments', id: a.id },
        cycle_id: a.cycle_id, cycle_day: a.cycle_day,
        detail: { assessment_type: a.assessment_type, assessment_type_text: type, assessed_by: who, sign_status: a.sign_status, risk_level: a.risk_level,
          ecog_status: a.ecog_status, overall_condition: a.overall_condition, chemo_readiness: a.chemo_readiness,
          ...Object.fromEntries(SOAP.map((k) => [k, a[k]])), next_follow_up_at: a.next_follow_up_at },
      }
    })
}

/** Dashboard nurse view: latest assessment, drafts, and the assessment risk reason (backend _assess_risk rule). */
function sync() {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage unavailable: state lives in memory only
  }
  const RANK = { low: 0, medium: 1, high: 2 }
  for (const pid of new Set(state.assessments.map((a) => a.patient_id))) {
    const nv = patientDashboards[pid]?.widgets['nurse-view']
    if (!nv) continue
    const rows = state.assessments.filter((a) => a.patient_id === pid && a.record_status === 'final')
      .sort((a, b) => Date.parse(b.assessed_at) - Date.parse(a.assessed_at))
    const latest = rows[0]
    nv.latest_assessment = latest ? { id: latest.id, assessment_type: latest.assessment_type, assessed_at: latest.assessed_at, risk_level: latest.risk_level,
      overall_condition: latest.overall_condition, sign_status: latest.sign_status } : null
    const drafts = rows.filter((a) => a.sign_status === 'draft')
    nv.pending_assessment_signoff = { count: drafts.length, items: drafts.map((a) => ({ id: a.id, assessment_type: a.assessment_type, assessed_at: a.assessed_at, assessed_by: mockPerson(a.assessed_by) })) }
    nv.risk.reasons = nv.risk.reasons.filter((r) => !r.startsWith('護理評估風險'))
    if (latest && ['medium', 'high'].includes(latest.risk_level)) {
      nv.risk.reasons.push(`護理評估風險：${latest.risk_level}`)
      if (RANK[latest.risk_level] > RANK[nv.risk.level]) nv.risk.level = latest.risk_level
    }
  }
}
if (restored) queueMicrotask(sync) // after a reload: the dashboards are registered by then

export function resetMockNursing() {
  Object.assign(state, seed())
  sync()
}
