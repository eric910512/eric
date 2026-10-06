/**
 * Mock corrections of observations (VITE_USE_MOCK=true) and the nurse review queues, in the
 * shapes of the Sprint 5 API (app/services/corrections.py):
 *   POST …/{id}/amend · POST …/{id}/mark-error · GET …/{id}/history for symptom records,
 *   vital signs and lab results; GET /vital-signs/patient/{id}; GET /labs/abnormal;
 *   GET /dashboard/widgets/pending-symptom-reviews/data.
 * Same rules: nurses only, a reason is required, the original is kept (`amended` /
 * `entered_in_error`) and the correction keeps its cycle, time and author. Open alerts of the
 * original are closed when the corrected values no longer meet the rule (kept when they still
 * do), and every open alert is closed on mark-error. Synthetic data only.
 */
import { addLabRow, LAB_DECIMALS, LAB_RULES, labRecordStore, applyLabRisk } from '@/mock/labs'
import { abnormalVitalRecords, closeAlertsOf, closeNotifications, openAlertsOf, symptomRecordStore } from '@/mock/nurseReview'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockAccessPatient, mockCurrentUser } from '@/mock/patients'
import { mockForms, SYMPTOM_RULES } from '@/mock/symptoms'
import { mockReferenceRanges, submittedVitals, VITAL_RULES } from '@/mock/vitals'
import { vitalFlag, vitalHint, VITAL_FIELDS } from '@/utils/vitals'

const clone = (v) => structuredClone(v)
let nextId = 70000
const invalid = (details, message = '更正內容有誤') => new MockApiError(400, 'VALIDATION_ERROR', message, details)
const P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const VITAL_EXTRA = ['temperature_site', 'bp_measure_site', 'notes']

// ------------------------------------------------------------------ vital readings of every patient
/** P00001's seeded reading comes from its dashboard (the same reading the other mock modules show). */
const seedVitals = []
{
  const lv = patientDashboards[P1]?.widgets['latest-vitals']
  if (lv?.temperature_c) {
    seedVitals.push({
      id: 501, patient_id: P1, measured_at: lv.temperature_c.measured_at, cycle_day: 3, source: 'patient_app', record_status: 'final',
      amends_id: null, notes: null, recorded_by: null, flags: [], severity: null, temperature_site: 'ear', bp_measure_site: lv.blood_pressure?.measure_site ?? null,
      temperature_c: lv.temperature_c.value, heart_rate_bpm: lv.heart_rate_bpm?.value ?? null, systolic_bp_mmhg: lv.blood_pressure?.systolic ?? null,
      diastolic_bp_mmhg: lv.blood_pressure?.diastolic ?? null, respiratory_rate: lv.respiratory_rate?.value ?? null, spo2_pct: lv.spo2_pct?.value ?? null,
      weight_kg: lv.weight_kg?.value ?? null, pain_score: null,
    })
  }
}
const allVitals = () => [...abnormalVitalRecords, ...seedVitals, ...submittedVitals]
const vitalsOf = (pid) => allVitals().filter((v) => v.patient_id === pid).sort((a, b) => Date.parse(b.measured_at) - Date.parse(a.measured_at) || b.id - a.id)
const inNadir = (pid) => !!patientDashboards[pid]?.widgets['treatment-progress']?.current_cycle?.in_nadir
function flagsOf(v, pid) {
  return Object.keys(VITAL_FIELDS).filter((f) => v[f] != null && vitalFlag(f, v[f], mockReferenceRanges)).map((f) => {
    const level = vitalFlag(f, v[f], mockReferenceRanges)
    const value = `${Number(v[f]).toFixed(VITAL_FIELDS[f].decimals)}${VITAL_FIELDS[f].unit}`
    const message = f === 'temperature_c' && level === 'critical'
      ? (inNadir(pid) ? `體溫 ${value}，目前處於骨髓抑制期，請立即聯絡醫療團隊` : `體溫 ${value}，化療期間發燒請立即聯絡醫療團隊`)
      : `${vitalHint(f, v[f], mockReferenceRanges).text}（${value}）`
    return { field: f, level, message }
  }).sort((a, b) => (a.level === 'critical' ? -1 : 0) - (b.level === 'critical' ? -1 : 0))
}
const vitalValues = (v) => ({
  ...Object.fromEntries([...Object.keys(VITAL_FIELDS), 'pain_score'].map((f) => [f, v[f] ?? null])), // API fields incl. pain_score
  temperature_site: v.temperature_site ?? null, bp_measure_site: v.bp_measure_site ?? null,
})
const vitalPayload = (v) => ({
  id: v.id, measured_at: v.measured_at, cycle_id: v.cycle_id ?? null, cycle_day: v.cycle_day ?? null, source: v.source ?? 'patient_app',
  record_status: v.record_status, amends_id: v.amends_id ?? null, ...vitalValues(v), notes: v.notes ?? null,
  flags: v.flags ?? [], recorded_by: v.recorded_by ?? null,
})
/** GET /vital-signs/patient/{id} (staff). */
export function mockListPatientVitals(patientId, { includeHistory = false } = {}) {
  mockCurrentUser('nurse', 'admin')
  const pid = mockAccessPatient(patientId).id
  const rows = vitalsOf(pid).filter((v) => includeHistory || v.record_status === 'final')
  return { data: rows.map(vitalPayload), meta: { total: rows.length, timezone: 'Asia/Taipei' } }
}
/** Final readings for the mock timeline. */
export const mockTimelineVitals = (pid) => vitalsOf(pid).filter((v) => v.record_status === 'final')

// ------------------------------------------------------------------ lookups
const symptomById = (id) => Object.values(symptomRecordStore).flat().find((r) => r.id === id)
const labById = (id) => Object.values(labRecordStore).flat().find((r) => r.id === id)
const vitalById = (id) => allVitals().find((v) => v.id === id)
export const mockSymptomPatient = (id) => symptomById(id)?.patient_id ?? null
export const mockVitalPatient = (id) => vitalById(id)?.patient_id ?? null
export const mockLabPatient = (id) => {
  const r = labById(id)
  return r ? Object.entries(labRecordStore).find(([, rows]) => rows.includes(r))[0] : null
}

// ------------------------------------------------------------------ shared rules
function reason(body, field = 'amend_reason') {
  const v = body[field]
  if (typeof v !== 'string' || !v.trim() || v.trim().length > 500) throw invalid([{ field, issue: 'is required (at most 500 characters)' }], '請填寫原因')
  return v.trim()
}
function ensureFinal(r) {
  if (r.record_status !== 'final') throw new MockApiError(409, 'INVALID_STATE', '這筆紀錄已被更正或標示為錯誤，請對最新的紀錄操作')
}
function unknownFields(body, allowed) {
  const extra = Object.keys(body).filter((k) => k !== 'amend_reason' && !allowed.includes(k)).sort()
  if (extra.length) throw invalid(extra.map((k) => ({ field: k, issue: 'is not a correctable field' })))
}
/** Alerts of the original: close those whose rule the corrected record no longer meets. */
function reconcile(table, originalId, matchingCodes, why) {
  const closed = []
  for (const [code, rows] of Object.entries(openAlertsOf(table, originalId))) {
    if (matchingCodes.includes(code)) continue // still valid
    closeNotifications(rows, `原始紀錄已更正（${why}）：更正後的數值已不符合此警示條件`)
    closed.push(code)
  }
  return closed
}
const idem = {}
function idempotent(key, body, fn) {
  if (!key) return fn()
  const slot = `${mockCurrentUser().id}:${key}`
  const hash = JSON.stringify(body)
  if (idem[slot]) {
    if (idem[slot].hash !== hash) throw new MockApiError(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    if (idem[slot].error) throw new MockApiError(idem[slot].error.status, idem[slot].error.code, 'This request already failed; send a new request')
    return idem[slot].result
  }
  try {
    const result = fn()
    idem[slot] = { hash, result }
    return result
  } catch (e) {
    if (e instanceof MockApiError && e.status < 500) idem[slot] = { hash, error: { status: e.status, code: e.code } }
    throw e
  }
}
const nurseOnly = () => mockCurrentUser('nurse')

// ------------------------------------------------------------------ vitals
export function mockAmendVital(id, body, key = null) {
  return idempotent(key, body, () => {
    nurseOnly()
    const orig = vitalById(id)
    ensureFinal(orig)
    const why = reason(body)
    unknownFields(body, [...Object.keys(VITAL_FIELDS), ...VITAL_EXTRA, 'measured_at'])
    const next = { ...vitalValues(orig), notes: orig.notes ?? null }
    for (const [k, v] of Object.entries(body)) if (k !== 'amend_reason' && k !== 'measured_at') next[k] = v
    const details = []
    for (const [f, meta] of Object.entries(VITAL_FIELDS)) {
      const v = next[f]
      if (v != null && (typeof v !== 'number' || (meta.min != null && v < meta.min) || (meta.max != null && v > meta.max))) details.push({ field: f, issue: `must be between ${meta.min} and ${meta.max}` })
    }
    if (details.length) throw invalid(details, '生命徵象內容有誤')
    const measured_at = body.measured_at ?? orig.measured_at
    if (JSON.stringify(next) === JSON.stringify({ ...vitalValues(orig), notes: orig.notes ?? null }) && measured_at === orig.measured_at) {
      throw invalid([{ field: 'amend_reason', issue: 'nothing changed: send the corrected fields' }])
    }
    const pid = orig.patient_id
    const row = { ...clone(orig), ...next, id: nextId++, measured_at, record_status: 'final', amends_id: orig.id, patient: orig.patient }
    row.flags = flagsOf(row, pid)
    row.severity = row.flags[0]?.level ?? null
    orig.record_status = 'amended'
    submittedVitals.push(row)
    const matching = VITAL_RULES.filter((r) => row[r.field] != null && r.test(row[r.field]) && (r.nadir === undefined || r.nadir === inNadir(pid))).map((r) => r.code)
    reconcile('vital_signs', orig.id, matching, why)
    refreshLatestVitals(pid)
    return { data: { ...vitalPayload(row), patient_id: pid, triggered_alerts: [], warnings: [] } }
  })
}
function refreshLatestVitals(pid) {
  const lv = patientDashboards[pid]?.widgets['latest-vitals']
  if (!lv) return
  const rows = mockTimelineVitals(pid)
  const latest = (f) => rows.find((v) => v[f] != null)
  for (const f of ['temperature_c', 'heart_rate_bpm', 'spo2_pct', 'respiratory_rate']) {
    const v = latest(f)
    lv[f] = v ? { value: v[f], measured_at: v.measured_at, flag: vitalFlag(f, v[f], mockReferenceRanges) } : null
  }
  const w = latest('weight_kg')
  lv.weight_kg = w ? { ...(lv.weight_kg ?? {}), value: w.weight_kg, measured_at: w.measured_at } : null
  const bp = latest('systolic_bp_mmhg')
  lv.blood_pressure = bp ? { systolic: bp.systolic_bp_mmhg, diastolic: bp.diastolic_bp_mmhg, measure_site: bp.bp_measure_site, measured_at: bp.measured_at,
    flag: vitalFlag('systolic_bp_mmhg', bp.systolic_bp_mmhg, mockReferenceRanges) } : null
}
export function mockMarkVitalError(id, body = {}) {
  nurseOnly()
  const r = vitalById(id)
  ensureFinal(r)
  const why = reason(body, 'reason')
  r.record_status = 'entered_in_error'
  const closed = closeAlertsOf('vital_signs', r.id, `紀錄已標示為錯誤：${why}`)
  refreshLatestVitals(r.patient_id)
  return { data: { id: r.id, record_status: r.record_status, closed_alerts: closed } }
}

// ------------------------------------------------------------------ symptoms
const symptomPayload = (r) => ({
  id: r.id, patient_id: r.patient_id, recorded_at: r.recorded_at, cycle_day: r.cycle_day, source: r.source, record_status: r.record_status,
  review_status: r.review_status, notes: r.notes, values: clone(r.values), alerts: clone(r.alerts ?? []), amends_id: r.amends_id ?? null,
  form: clone(r.form), reviewed_at: r.reviewed_at, reviewed_by: clone(r.reviewed_by), review: clone(r.review),
})
export function mockAmendSymptom(id, body, key = null) {
  return idempotent(key, body, () => {
    nurseOnly()
    const orig = symptomById(id)
    ensureFinal(orig)
    const why = reason(body)
    unknownFields(body, ['values', 'recorded_at', 'notes'])
    const defs = Object.fromEntries(mockForms.daily_chemo_check.items.map((i) => [i.definition.code, i.definition]))
    let values = clone(orig.values)
    if ('values' in body) {
      const details = []
      if (!Array.isArray(body.values) || !body.values.length) details.push({ field: 'values', issue: 'must be a non-empty list' })
      values = (body.values ?? []).map((v, i) => {
        const d = defs[v?.definition_code]
        if (!d) details.push({ field: `values[${i}].definition_code`, issue: 'is not part of the form' })
        else if (d.value_type === 'boolean' ? typeof v.value_boolean !== 'boolean' : !(Number.isInteger(v.value_numeric) && v.value_numeric >= d.min_value && v.value_numeric <= d.max_value)) {
          details.push({ field: `values[${i}].${d.value_type === 'boolean' ? 'value_boolean' : 'value_numeric'}`, issue: d.value_type === 'boolean' ? 'must be true or false' : `must be between ${d.min_value} and ${d.max_value}` })
        }
        if (!d) return null
        return d.value_type === 'boolean'
          ? { definition_code: d.code, label: d.name_zh, value_boolean: v.value_boolean, score: v.value_boolean ? 1 : 0 }
          : { definition_code: d.code, label: d.name_zh, value_numeric: v.value_numeric, score: v.value_numeric }
      })
      if (details.length) throw invalid(details, '症狀回報內容有誤')
    }
    const notes = 'notes' in body ? (body.notes || null) : orig.notes
    const same = (a, b) => JSON.stringify(a.map((v) => [v.definition_code, v.score])) === JSON.stringify(b.map((v) => [v.definition_code, v.score]))
    if (same(values, orig.values) && notes === orig.notes && (body.recorded_at ?? orig.recorded_at) === orig.recorded_at) {
      throw invalid([{ field: 'amend_reason', issue: 'nothing changed: send the corrected fields' }])
    }
    const row = { ...clone(orig), id: nextId++, values, notes, recorded_at: body.recorded_at ?? orig.recorded_at, record_status: 'final', amends_id: orig.id, alerts: [] }
    orig.record_status = 'amended'
    symptomRecordStore[orig.patient_id].unshift(row)
    const matching = SYMPTOM_RULES.filter((r) => (values.find((v) => v.definition_code === r.definition)?.score ?? -1) >= r.threshold).map((r) => r.code)
    reconcile('symptom_records', orig.id, matching, why)
    return { data: symptomPayload(row) }
  })
}
export function mockMarkSymptomError(id, body = {}) {
  nurseOnly()
  const r = symptomById(id)
  ensureFinal(r)
  const why = reason(body, 'reason')
  r.record_status = 'entered_in_error'
  return { data: { id: r.id, record_status: r.record_status, closed_alerts: closeAlertsOf('symptom_records', r.id, `紀錄已標示為錯誤：${why}`) } }
}

// ------------------------------------------------------------------ labs
const labPayload = (r) => ({ ...(({ _alerts, ...x }) => clone(x))(r), alerts: clone(r._alerts ?? []) })
export function mockAmendLab(id, body, key = null) {
  return idempotent(key, body, () => {
    nurseOnly()
    const orig = labById(id)
    ensureFinal(orig)
    const why = reason(body)
    unknownFields(body, ['value', 'collected_at'])
    const value = 'value' in body ? body.value : orig.value
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw invalid([{ field: 'value', issue: 'must be a number' }], '檢驗資料內容有誤')
    const v = Number(value.toFixed(LAB_DECIMALS[orig.test_code] ?? 2))
    const collected = body.collected_at ?? orig.collected_at
    if (v === orig.value && collected === orig.collected_at) throw invalid([{ field: 'amend_reason', issue: 'nothing changed: send the corrected fields' }])
    const pid = mockLabPatient(id)
    const row = addLabRow(pid, orig.test_code, v, collected, orig.cycle_day)
    Object.assign(row, { amends_id: orig.id, resulted_at: orig.resulted_at })
    orig.record_status = 'amended'
    const matching = orig.test_code === 'ANC' ? LAB_RULES.filter((r) => r.test(v)).map((r) => r.code) : []
    reconcile('lab_results', orig.id, matching, why)
    applyLabRisk(pid)
    return { data: labPayload(row) }
  })
}
export function mockMarkLabError(id, body = {}) {
  nurseOnly()
  const r = labById(id)
  ensureFinal(r)
  const why = reason(body, 'reason')
  r.record_status = 'entered_in_error'
  const closed = closeAlertsOf('lab_results', r.id, `紀錄已標示為錯誤：${why}`)
  applyLabRisk(mockLabPatient(id))
  return { data: { id: r.id, record_status: r.record_status, closed_alerts: closed } }
}

// ------------------------------------------------------------------ history
function chain(find, record, payload) {
  const all = []
  let first = record
  while (first.amends_id) first = find(first.amends_id)
  for (let cur = first; cur;) {
    all.push(cur)
    const id = cur.id
    cur = [...Object.values(symptomRecordStore).flat(), ...Object.values(labRecordStore).flat(), ...allVitals()].find((x) => x.amends_id === id && find(x.id) === x)
  }
  return all.map((r) => {
    const newer = all.find((x) => x.amends_id === r.id)
    return { id: r.id, record_status: r.record_status, amends_id: r.amends_id ?? null, amended_by_id: newer?.id ?? null, cycle_id: r.cycle_id ?? null,
      cycle_day: r.cycle_day ?? null, source: r.source ?? 'patient_app', created_at: r.created_at ?? r.recorded_at ?? r.measured_at ?? r.collected_at, ...payload(r) }
  })
}
export function mockVitalHistory(id) {
  mockCurrentUser('nurse', 'admin')
  const r = vitalById(id)
  return { data: chain(vitalById, r, (v) => ({ measured_at: v.measured_at, ...vitalValues(v), notes: v.notes ?? null, recorded_by: v.recorded_by ?? null })) }
}
export function mockSymptomHistory(id) {
  mockCurrentUser('nurse', 'admin')
  const r = symptomById(id)
  return { data: chain(symptomById, r, (x) => ({ recorded_at: x.recorded_at, notes: x.notes, review_status: x.review_status, values: clone(x.values), reported_by: clone(x.reported_by) })) }
}
export function mockLabRecordHistory(id) {
  mockCurrentUser('nurse', 'admin')
  const r = labById(id)
  return { data: chain(labById, r, (x) => ({ collected_at: x.collected_at, test_code: x.test_code, value: String(x.value), unit: x.unit, abnormal_flag: x.abnormal_flag, recorded_by: clone(x.recorded_by) })) }
}

// ------------------------------------------------------------------ queues (nurse, visible patients only)
function patientRef(pid) {
  const s = patientDashboards[pid]?.widgets['patient-summary']
  const p = s ?? mockAccessPatient(pid)
  return { id: pid, patient_code: p.patient_code, display_name: p.display_name }
}
/** pending-symptom-reviews widget: submitted, final reports, oldest first. */
export function mockPendingReviews(visible) {
  nurseOnly()
  const rows = Object.entries(symptomRecordStore).filter(([pid]) => visible(pid)).flatMap(([, list]) => list)
    .filter((r) => r.record_status === 'final' && r.review_status === 'submitted')
    .sort((a, b) => Date.parse(a.recorded_at) - Date.parse(b.recorded_at) || a.id - b.id)
  const items = rows.map((r) => {
    const scored = r.values.filter((v) => v.score != null).sort((a, b) => b.score - a.score)
    return { id: r.id, recorded_at: r.recorded_at, cycle_day: r.cycle_day, summary: scored.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}` : `${v.label} ${v.score}`)).join('、'),
      max_score: scored[0]?.score ?? null, open_alert_count: Object.keys(openAlertsOf('symptom_records', r.id)).length, patient: patientRef(r.patient_id) }
  })
  return { data: items, meta: { total: items.length } }
}
/** GET /labs/abnormal: flagged, final results of visible patients, newest first. */
export function mockAbnormalLabs(visible, { hours = 168 } = {}) {
  nurseOnly()
  const since = Date.now() - hours * 3600000
  const items = Object.entries(labRecordStore).filter(([pid]) => visible(pid))
    .flatMap(([pid, rows]) => rows.filter((r) => r.record_status === 'final' && r.abnormal_flag !== 'N' && Date.parse(r.collected_at) >= since)
      .map((r) => ({ ...labPayload(r), patient: patientRef(pid) })))
    .sort((a, b) => Date.parse(b.collected_at) - Date.parse(a.collected_at) || b.id - a.id)
  return { data: items, meta: { hours, total: items.length, critical: items.filter((i) => i.level === 'critical').length,
    unresolved_alerts: items.flatMap((i) => i.alerts).filter((a) => !a.resolved).length } }
}
