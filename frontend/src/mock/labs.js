/**
 * Mock lab results (VITE_USE_MOCK=true), in the shapes of
 *   GET  /api/v1/labs/test-types
 *   GET  /api/v1/labs/results/{patient_id}   (nurse: full data)
 *   GET  /api/v1/labs/summary/{patient_id}   (patient: simplified)
 *   POST /api/v1/labs/results
 * Mirrors the backend seed: ranges, flags, patient wording, ANC alert rules and the
 * risk engine's lab criteria. In-memory, reset on page reload. Synthetic data only.
 */
import { ruleOn, threshold } from '@/mock/alertRules'
import { byTimeDesc, nowIso } from '@/mock/clock'
import { nurseOverview } from '@/mock/nurseOverview'
import { addMockNurseNotification, mockNotificationState } from '@/mock/nurseReview'
import { applyToRiskSummary, patientDashboards } from '@/mock/patientDashboards'

// Demo reference / critical ranges (adult) — to be confirmed clinically.
export const mockTestTypes = [
  { code: 'WBC', loinc_code: '6690-2', name_zh: '白血球 (WBC)', unit: '10³/µL', ref_low: 4.0, ref_high: 10.0, critical_low: 1.0, critical_high: 30.0 },
  { code: 'ANC', loinc_code: '751-8', name_zh: '嗜中性白血球絕對數 (ANC)', unit: '10³/µL', ref_low: 1.5, ref_high: 7.5, critical_low: 0.5, critical_high: null },
  { code: 'HGB', loinc_code: '718-7', name_zh: '血色素 (Hb)', unit: 'g/dL', ref_low: 12.0, ref_high: 16.0, critical_low: 7.0, critical_high: 20.0 },
  { code: 'PLT', loinc_code: '777-3', name_zh: '血小板 (Platelet)', unit: '10³/µL', ref_low: 150, ref_high: 400, critical_low: 20, critical_high: 1000 },
]
const TYPES = Object.fromEntries(mockTestTypes.map((t) => [t.code, t]))
const LEVEL = { LL: 'critical', HH: 'critical', L: 'warning', H: 'warning', N: null }

const PATIENT_LABELS = { WBC: '白血球', ANC: '嗜中性白血球（抵抗力）', HGB: '血色素', PLT: '血小板' }
const STATUS = { N: ['normal', '正常'], L: ['low', '偏低'], LL: ['very_low', '過低'], H: ['high', '偏高'], HH: ['very_high', '過高'] }
const EXPLAIN_LOW = {
  ANC: ['抵抗力偏低：避免出入人多的地方、勤洗手；發燒 38°C 以上請立即就醫。', '抵抗力很低，感染風險高：請避免外出和生食，一旦發燒請立即就醫。'],
  WBC: ['白血球偏低，抵抗力較弱，請注意防護與手部清潔。', '白血球過低，感染風險高，請與醫療團隊聯繫。'],
  HGB: ['血色素偏低，可能容易疲倦或頭暈，起身時請放慢動作。', '血色素過低，如果頭暈、喘或心悸，請立即聯絡醫療團隊。'],
  PLT: ['血小板偏低，容易瘀青或出血：避免碰撞，使用軟毛牙刷。', '血小板過低：若有出血不止、黑便或血尿，請立即就醫。'],
}
// dashboard risk engine (backend LAB_RISK_RULES)
const LAB_RISK = { ANC: { LL: 'high', L: 'medium' }, PLT: { LL: 'high', L: 'medium' }, HGB: { LL: 'high' }, WBC: { LL: 'medium' } }
const RISK_NAME = { ANC: 'ANC', WBC: '白血球', HGB: '血色素', PLT: '血小板' }
const RANK = { low: 0, medium: 1, high: 2 }

const NURSE = { id: 'mock-user-nurse01', display_name: '測試護理師 林' }
const P1 = { id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0', patient_code: 'P00001', display_name: '測試病人 甲' }
const P2 = { id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e', patient_code: 'P00002', display_name: '測試病人 乙' }
const PATIENTS = { [P1.id]: P1, [P2.id]: P2 }
/** Seeded patients, or one created in mock mode (known from its dashboard; no lab data yet). */
function patientRef(patientId) {
  const summary = patientDashboards[patientId]?.widgets['patient-summary']
  return PATIENTS[patientId] ?? (summary ? { id: patientId, patient_code: summary.patient_code, display_name: summary.display_name } : null)
}

export function labFlag(t, v) {
  if (t.critical_low != null && v <= t.critical_low) return 'LL'
  if (t.critical_high != null && v >= t.critical_high) return 'HH'
  if (t.ref_low != null && v < t.ref_low) return 'L'
  if (t.ref_high != null && v > t.ref_high) return 'H'
  return 'N'
}

let nextId = 9000
const results = {} // patient_id → rows, newest first

function addRow(patientId, code, value, collectedAt, cycleDay, alerts = []) {
  const t = TYPES[code]
  const row = {
    id: nextId++, test_code: code, name_zh: t.name_zh, value, unit: t.unit,
    ref_low: t.ref_low, ref_high: t.ref_high, critical_low: t.critical_low, critical_high: t.critical_high,
    abnormal_flag: labFlag(t, value), collected_at: collectedAt, resulted_at: collectedAt,
    cycle_day: cycleDay, source: 'nurse', record_status: 'final', amends_id: null, recorded_by: NURSE, _alerts: alerts,
  }
  row.level = LEVEL[row.abnormal_flag]
  ;(results[patientId] ??= []).unshift(row)
  return row
}

// Seed: P00001 pre-chemo CBC (C1D1, normal); P00002 C2D1 baseline + nadir CBC today (ANC 1.2, below range, no alert rule).
for (const [code, v] of [['WBC', 6.2], ['ANC', 3.8], ['HGB', 12.8], ['PLT', 245]]) addRow(P1.id, code, v, '2026-09-20T23:30:00.000Z', 1)
for (const [code, v] of [['WBC', 5.6], ['ANC', 3.1], ['HGB', 11.9], ['PLT', 210]]) addRow(P2.id, code, v, '2026-09-15T23:30:00.000Z', 1)
for (const [code, v] of [['WBC', 2.4], ['ANC', 1.2], ['HGB', 10.8], ['PLT', 118]]) addRow(P2.id, code, v, '2026-09-23T23:00:00.000Z', 9)

const clone = (v) => structuredClone(v)
const now = nowIso
const since = (days) => new Date(Date.now() - days * 86400000).toISOString()
const fmt = (row) => `${row.value} ${row.unit}`

function withAlerts(row) {
  const { _alerts, ...rest } = row
  return {
    ...rest,
    alerts: _alerts.map((a) => {
      const ack = mockNotificationState(a.my_notification_id)?.acknowledged
      return { ...a, resolved: !!ack, resolved_by: ack?.by.display_name ?? null, resolution_note: ack?.resolution_note ?? null }
    }),
  }
}

function latestByTest(patientId, days) {
  const latest = {}
  for (const r of results[patientId] ?? []) if (r.record_status === 'final' && Date.parse(r.collected_at) >= Date.parse(since(days))) latest[r.test_code] ??= r
  return mockTestTypes.map((t) => latest[t.code]).filter(Boolean)
}

export function mockLabSummary(patientId) {
  const items = latestByTest(patientId, 30).map((r) => {
    const [status, statusText] = STATUS[r.abnormal_flag]
    const low = ['L', 'LL'].includes(r.abnormal_flag)
    const high = ['H', 'HH'].includes(r.abnormal_flag)
    return {
      code: r.test_code, label: PATIENT_LABELS[r.test_code], value: r.value, unit: r.unit, status, status_text: statusText,
      level: r.level, collected_at: r.collected_at,
      explanation: low ? EXPLAIN_LOW[r.test_code][r.abnormal_flag === 'LL' ? 1 : 0] : high ? '數值偏高，醫療團隊會一併評估。' : null,
    }
  })
  const message = !items.length ? '最近 30 天還沒有檢驗結果。'
    : items.some((i) => i.level === 'critical') ? '有檢驗數值需要特別注意，請依下方說明照顧自己，護理團隊會與您聯繫。'
      : items.some((i) => i.status !== 'normal') ? '有部分數值不在正常範圍，請參考下方說明。'
        : '最近一次檢驗數值都在正常範圍。'
  return { last_collected_at: items.map((i) => i.collected_at).sort(byTimeDesc)[0] ?? null, message, items }
}

export function mockLabHistory(patientId, { days = 90, test = null } = {}) {
  if (!patientRef(patientId)) throw new Error('找不到這位病人的示範資料')
  const rows = (results[patientId] ?? []).filter((r) => r.record_status === 'final' && Date.parse(r.collected_at) >= Date.parse(since(days)) && (!test || r.test_code === test))
  const latest = {}
  const series = {}
  for (const r of rows) {
    latest[r.test_code] ??= withAlerts(r)
    ;(series[r.test_code] ??= []).unshift({ collected_at: r.collected_at, value: r.value, abnormal_flag: r.abnormal_flag })
  }
  return {
    data: {
      test_types: clone(mockTestTypes),
      latest,
      series,
      results: rows.map((r) => { const { _alerts, ...rest } = r; return clone(rest) }),
    },
    meta: { days, total: rows.length },
  }
}

/** Risk contribution of labs within 7 days (backend _assess_risk lab criteria). */
function labRisk(patientId) {
  let level = 'low'
  const reasons = []
  for (const r of latestByTest(patientId, 7)) {
    const l = LAB_RISK[r.test_code]?.[r.abnormal_flag]
    if (!l) continue
    reasons.push(`${RISK_NAME[r.test_code]} ${fmt(r)}（${r.abnormal_flag === 'LL' ? '嚴重偏低' : '偏低'}）`)
    if (RANK[l] > RANK[level]) level = l
  }
  return { level, reasons }
}

const isLabReason = (s) => /^(ANC|白血球|血色素|血小板) .+（(嚴重)?偏低）$/.test(s)

/** Merge current lab risk into the mock nurse view, risk summary and caseload. */
function applyLabRisk(patientId) {
  const w = patientDashboards[patientId]?.widgets
  if (!w) return
  const lab = labRisk(patientId)
  const nv = w['nurse-view']
  nv.risk.reasons = [...nv.risk.reasons.filter((r) => !isLabReason(r)), ...lab.reasons]
  if (RANK[lab.level] > RANK[nv.risk.level]) nv.risk.level = lab.level
  const rs = w['risk-summary']
  if (rs) {
    rs.reasons = [...rs.reasons.filter((r) => !isLabReason(r)), ...lab.reasons]
    if (lab.level === 'medium' && rs.level === 'low') {
      Object.assign(rs, { level: 'medium', status: 'attention', title: '今天有些狀況需要留意', message: '請多休息並持續記錄，護理團隊會追蹤您的狀況。' })
    }
  }
  const row = nurseOverview.caseload.data.find((p) => p.patient_id === patientId)
  if (row) {
    row.risk_reasons = [...row.risk_reasons.filter((r) => !isLabReason(r)), ...lab.reasons]
    if (RANK[nv.risk.level] > RANK[row.risk_level]) row.risk_level = nv.risk.level
  }
}
Object.keys(PATIENTS).forEach(applyLabRisk)

// thresholds / on-off come from the mock alert rules (editable in the admin console)
const RULES = [
  { code: 'severe_neutropenia', name: '嗜中性白血球嚴重低下', severity: 'critical', test: (v) => ruleOn('severe_neutropenia') && v <= threshold('severe_neutropenia', 0.5) },
  { code: 'neutropenia', name: '嗜中性白血球低下', severity: 'warning', test: (v) => ruleOn('neutropenia') && v <= threshold('neutropenia', 1.0) && v > 0.5 },
]
const DECIMALS = { WBC: 2, ANC: 2, HGB: 1, PLT: 0 }
/** For the corrections mock: rows by patient, rules, and adding a row. */
export { results as labRecordStore, RULES as LAB_RULES, addRow as addLabRow, DECIMALS as LAB_DECIMALS, applyLabRisk }

export function submitMockLabs(patientId, body) {
  const patient = patientRef(patientId)
  const w = patientDashboards[patientId]?.widgets
  if (!patient || !w) throw new Error('找不到這位病人的示範資料')
  const at = now()
  const cycle = w['treatment-progress']?.current_cycle
  const collectedLocal = new Date(new Date(body.collected_at).getTime() + 8 * 3600000).toISOString().slice(0, 10)
  const todayLocal = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
  const cycleDay = cycle ? cycle.cycle_day - Math.round((new Date(todayLocal) - new Date(collectedLocal)) / 86400000) : null

  const triggered = []
  const rows = body.results.map(({ test_code: code, value }) => {
    const v = Number(Number(value).toFixed(DECIMALS[code]))
    const row = addRow(patientId, code, v, body.collected_at, cycleDay > 0 ? cycleDay : null)
    row.resulted_at = body.resulted_at ?? at
    if (code !== 'ANC') return row
    for (const rule of RULES.filter((r) => r.test(v))) {
      const label = PATIENT_LABELS.ANC
      const value = fmt(row)
      const message = `您的${label}為 ${value}，護理團隊已收到通知，會與您聯繫。` +
        (rule.severity === 'critical' ? '化療期間請不要等待，請立即撥打照護專線或前往急診。' : '若情況加重，請撥打照護專線。')
      const eventKey = `lab_results:${row.id}:rule:${rule.code}`
      const nurseMessage = `${patient.display_name}（${patient.patient_code}）ANC ${value}` +
        (cycle ? `，Cycle ${cycle.cycle_number} Day ${row.cycle_day ?? ''}` : '') + (rule.severity === 'critical' ? '，感染風險極高，請立即評估' : '')
      const id = addMockNurseNotification({
        event_key: eventKey, severity: rule.severity, title: rule.name, message: nurseMessage, patient,
        alert_rule: { id: 0, code: rule.code }, source: { table: 'lab_results', id: row.id }, created_at: at,
        _detail: {
          trigger: { rule_code: rule.code, rule_name: rule.name, source_type: 'lab', severity: rule.severity, label, value,
            condition: rule.code === 'severe_neutropenia' ? `${label} <= 0.5` : `${label} <= 1 且 > 0.5` },
          source_record: { table: 'lab_results', ...(({ _alerts, ...r }) => clone(r))(row) },
        },
      })
      row._alerts.push({ event_key: eventKey, alert_rule_code: rule.code, severity: rule.severity, title: rule.name, my_notification_id: id })
      w.notifications.items.unshift({ id: nextId++, event_key: eventKey, type: 'risk_alert', status: 'new', status_text: '護理團隊已收到通知', severity: rule.severity, title: rule.name, message, is_read: false, created_at: at })
      w.notifications.unread_count += 1
      w['nurse-view'].unacknowledged_alerts.items.unshift({ id, event_key: eventKey, severity: rule.severity, title: rule.name, message: nurseMessage, created_at: at })
      w['nurse-view'].unacknowledged_alerts.count += 1
      triggered.push({ alert_rule_code: rule.code, severity: rule.severity, test_code: 'ANC', label, value, message, notified: true, _title: rule.name, _eventKey: eventKey })
    }
    return row
  })

  applyToRiskSummary(w, { alerts: triggered.map((t) => ({ title: t._title, severity: t.severity, event_key: t._eventKey })), at })
  if (triggered.some((t) => t.severity === 'critical')) {
    w['nurse-view'].risk.level = 'high'
    const row = nurseOverview.caseload.data.find((p) => p.patient_id === patientId)
    if (row) row.risk_level = 'high'
  }
  applyLabRisk(patientId)

  return {
    collected_at: body.collected_at,
    cycle_day: rows[0].cycle_day,
    results: rows.map(withAlerts),
    triggered_alerts: triggered.map(({ _title, _eventKey, ...t }) => t),
  }
}
