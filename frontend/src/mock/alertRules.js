/**
 * Mock alert rules (VITE_USE_MOCK=true): the same rules as the backend seed, in the shape of
 * GET /api/v1/notifications/alert-rules, editable by the mock admin console. The mock symptom,
 * vital-sign and lab checks read `ruleOn` / `threshold` from here, so a disabled or changed rule
 * affects new mock reports like it does in the API. Synthetic data only.
 */
import { MockApiError, mockCurrentUser } from '@/mock/patients'

const STATE_KEY = 'ccp.mock.alert-rules'
const T = { symptom: (code, label) => ({ type: 'symptom', code, label }), vital: (code, label) => ({ type: 'vital_sign', code, label }), lab: (code, label) => ({ type: 'lab', code, label }) }
const rule = (id, code, name, source_type, target, operator, threshold_value, severity, cooldown, extra = {}, recommended = null) => ({
  id, code, name, source_type, target, operator, threshold_value, extra_conditions: extra, severity,
  message_template: null, recommended_action: recommended, notify_patient: true, notify_nurse: true, cooldown_minutes: cooldown, is_active: true,
})
function seed() {
  return [
    rule(1, 'severe_pain', '疼痛程度偏高', 'symptom', T.symptom('pain', '疼痛'), '>=', 7, 'warning', 720),
    rule(2, 'severe_nausea', '噁心程度偏高', 'symptom', T.symptom('nausea', '噁心'), '>=', 7, 'warning', 720),
    rule(3, 'severe_fatigue', '疲倦程度偏高', 'symptom', T.symptom('fatigue', '疲倦'), '>=', 8, 'warning', 720),
    rule(4, 'reported_fever', '病人回報發燒或畏寒', 'symptom', T.symptom('fever', '發燒或畏寒'), '==', 1, 'critical', 240),
    rule(5, 'suspected_febrile_neutropenia', '疑似嗜中性白血球低下發燒', 'vital_sign', T.vital('temperature_c', '體溫'), '>=', 38, 'critical', 240, { within_nadir: true }),
    rule(6, 'fever', '化療期間發燒', 'vital_sign', T.vital('temperature_c', '體溫'), '>=', 38, 'critical', 240, { outside_nadir: true }),
    rule(7, 'tachycardia', '心跳過快', 'vital_sign', T.vital('heart_rate_bpm', '心跳'), '>=', 120, 'warning', 720),
    rule(8, 'hypotension', '血壓偏低', 'vital_sign', T.vital('systolic_bp_mmhg', '收縮壓'), '<=', 90, 'warning', 720),
    rule(9, 'low_spo2', '血氧過低', 'vital_sign', T.vital('spo2_pct', '血氧'), '<=', 90, 'critical', 240),
    rule(10, 'severe_neutropenia', '嗜中性白血球嚴重低下', 'lab', T.lab('ANC', '嗜中性白血球絕對數 (ANC)'), '<=', 0.5, 'critical', 240),
    rule(11, 'neutropenia', '嗜中性白血球低下', 'lab', T.lab('ANC', '嗜中性白血球絕對數 (ANC)'), '<=', 1.0, 'warning', 720, { value_above: 0.5 }),
  ]
}
function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (Array.isArray(saved)) return saved
  } catch {
    // storage unavailable
  }
  return seed()
}
const rules = load()
const save = () => {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(rules))
  } catch {
    // storage unavailable
  }
}
const clone = (v) => structuredClone(v)
const OPS = { '>=': (a, b) => a >= b, '<=': (a, b) => a <= b, '>': (a, b) => a > b, '<': (a, b) => a < b, '==': (a, b) => a === b }
const CONDITION_KEYS = ['within_nadir', 'outside_nadir', 'consecutive_records', 'value_above']
const VITAL_CODES = ['temperature_c', 'heart_rate_bpm', 'systolic_bp_mmhg', 'diastolic_bp_mmhg', 'spo2_pct', 'respiratory_rate', 'weight_kg', 'pain_score']
const invalid = (details) => new MockApiError(400, 'VALIDATION_ERROR', '風險規則內容有誤', details)

/** For the mock engines: is the rule on, and its current threshold. */
export const ruleOn = (code) => rules.find((r) => r.code === code)?.is_active !== false
export const threshold = (code, fallback) => rules.find((r) => r.code === code)?.threshold_value ?? fallback

const find = (id) => rules.find((r) => r.id === id) ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'Alert rule not found') })()
export function mockListRules() {
  mockCurrentUser('admin')
  return { data: clone([...rules].sort((a, b) => a.source_type.localeCompare(b.source_type) || a.code.localeCompare(b.code))) }
}
export function mockGetRule(id) {
  mockCurrentUser('admin')
  return { data: clone(find(id)) }
}
function values(body, partial) {
  const d = []
  const out = {}
  const has = (k) => k in body
  if (!partial || has('name')) {
    if (typeof body.name !== 'string' || !body.name.trim() || body.name.length > 100) d.push({ field: 'name', issue: 'is required (at most 100 characters)' })
    else out.name = body.name.trim()
  }
  if (!partial || has('operator')) {
    if (!OPS[body.operator]) d.push({ field: 'operator', issue: 'must be one of: >=, <=, >, <, ==' })
    else out.operator = body.operator
  }
  if (!partial || has('threshold_value')) {
    if (typeof body.threshold_value !== 'number' || !Number.isFinite(body.threshold_value)) d.push({ field: 'threshold_value', issue: 'is required (number)' })
    else out.threshold_value = body.threshold_value
  }
  if (!partial || has('severity')) {
    if (!['critical', 'warning'].includes(body.severity)) d.push({ field: 'severity', issue: 'must be one of: critical, warning' })
    else out.severity = body.severity
  }
  for (const k of ['message_template', 'recommended_action']) {
    if (!has(k)) continue
    if (body[k] !== null && (typeof body[k] !== 'string' || body[k].length > 500)) d.push({ field: k, issue: 'must be text of at most 500 characters' })
    else out[k] = body[k] ? body[k].trim() || null : null
  }
  for (const k of ['notify_patient', 'notify_nurse', 'is_active']) {
    if (!has(k)) continue
    if (typeof body[k] !== 'boolean') d.push({ field: k, issue: 'must be true or false' })
    else out[k] = body[k]
  }
  if (has('cooldown_minutes')) {
    if (!Number.isInteger(body.cooldown_minutes) || body.cooldown_minutes < 0 || body.cooldown_minutes > 10080) d.push({ field: 'cooldown_minutes', issue: 'must be an integer from 0 to 10080' })
    else out.cooldown_minutes = body.cooldown_minutes
  }
  if (has('extra_conditions')) {
    const c = body.extra_conditions
    if (c !== null && (typeof c !== 'object' || Object.keys(c).some((k) => !CONDITION_KEYS.includes(k)))) d.push({ field: 'extra_conditions', issue: `must be an object with keys among: ${CONDITION_KEYS.join(', ')}` })
    else out.extra_conditions = c ?? {}
  }
  return { out, d }
}
export function mockCreateRule(body) {
  mockCurrentUser('admin')
  const { out, d } = values(body, false)
  if (typeof body.code !== 'string' || !body.code.trim()) d.push({ field: 'code', issue: 'is required (at most 50 characters)' })
  let target = null
  if (body.source_type === 'vital_sign') {
    if (!VITAL_CODES.includes(body.vital_field)) d.push({ field: 'vital_field', issue: `must be one of: ${VITAL_CODES.join(', ')}` })
    else target = { type: 'vital_sign', code: body.vital_field, label: body.vital_field }
  } else if (body.source_type === 'symptom') {
    if (!['pain', 'nausea', 'fatigue', 'fever'].includes(body.symptom_code)) d.push({ field: 'symptom_code', issue: 'must be an active symptom definition code' })
    else target = { type: 'symptom', code: body.symptom_code, label: body.symptom_code }
  } else if (body.source_type === 'lab') {
    if (!['WBC', 'ANC', 'HGB', 'PLT'].includes(body.test_code)) d.push({ field: 'test_code', issue: 'must be a lab test code' })
    else target = { type: 'lab', code: body.test_code, label: body.test_code }
  } else d.push({ field: 'source_type', issue: 'must be vital_sign, symptom or lab' })
  if (d.length) throw invalid(d)
  if (rules.some((r) => r.code === body.code.trim())) throw new MockApiError(409, 'CONFLICT', '規則代碼已存在', [{ field: 'code', issue: 'is already used' }])
  const r = { id: Math.max(...rules.map((x) => x.id)) + 1, code: body.code.trim(), source_type: body.source_type, target, extra_conditions: {},
    message_template: null, recommended_action: null, notify_patient: true, notify_nurse: true, cooldown_minutes: 0, is_active: true, ...out }
  rules.push(r)
  save()
  return { data: clone(r) }
}
export function mockUpdateRule(id, body) {
  mockCurrentUser('admin')
  const r = find(id)
  const { out, d } = values(body, true)
  const unknown = Object.keys(body).filter((k) => !['name', 'operator', 'threshold_value', 'severity', 'message_template', 'recommended_action', 'notify_patient', 'notify_nurse', 'cooldown_minutes', 'extra_conditions', 'is_active'].includes(k))
  d.push(...unknown.sort().map((k) => ({ field: k, issue: 'is not a supported field' })))
  if (d.length) throw invalid(d)
  const changed = Object.keys(out).filter((k) => JSON.stringify(r[k]) !== JSON.stringify(out[k]))
  Object.assign(r, out)
  save()
  return { data: clone(r), changed }
}
export function mockTestRule(id, body = {}) {
  mockCurrentUser('admin')
  const r = find(id)
  if (typeof body.value !== 'number') throw new MockApiError(400, 'VALIDATION_ERROR', '試跑資料有誤', [{ field: 'value', issue: 'is required (number)' }])
  const inNadir = body.in_nadir ?? false
  const c = r.extra_conditions ?? {}
  const comparison = OPS[r.operator](body.value, r.threshold_value)
  const conditionsMet = !((c.within_nadir && !inNadir) || (c.outside_nadir && inNadir)) && (c.value_above == null || body.value > c.value_above)
  return { data: { matches: comparison && conditionsMet, comparison, conditions_met: conditionsMet, consecutive_records: c.consecutive_records ?? null, is_active: r.is_active, sent: false } }
}
