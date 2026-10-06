/**
 * Mock symptom form + submission (VITE_USE_MOCK=true).
 * The form has the shape of GET /api/v1/symptoms/forms/{code} → data, and submitMockReport
 * returns the shape of POST /api/v1/symptoms/records → data. The alert thresholds mirror
 * the backend seed rules (severe_pain / severe_nausea / severe_fatigue / reported_fever).
 */
import { ruleOn, threshold } from '@/mock/alertRules'
import { nowIso } from '@/mock/clock'
import { addMockSymptomRecord, mockListRecordsStore } from '@/mock/nurseReview'
import { applyToRiskSummary, patientDashboards } from '@/mock/patientDashboards'
import { MockApiError } from '@/mock/patients'

const GENERAL = { code: 'general', name_zh: '全身症狀' }
const GI = { code: 'gastrointestinal', name_zh: '腸胃症狀' }
const scale = (id, code, name, question, category = GENERAL) => ({
  id,
  code,
  name_zh: name,
  question_text: question,
  help_text: null,
  value_type: 'scale',
  higher_is_worse: true,
  min_value: 0,
  max_value: 10,
  step: 1,
  unit: null,
  min_label: '沒有',
  max_label: '最嚴重',
  category,
})

export const mockForms = {
  daily_chemo_check: {
    id: 1,
    code: 'daily_chemo_check',
    name: '化療每日症狀自評',
    description: null,
    intended_for: 'patient',
    availability: 'always',
    recall_period_hours: 24,
    version: 1,
    items: [
      { display_order: 1, is_required: true, display_condition: null, definition: scale(1, 'pain', '疼痛', '過去 24 小時最嚴重的疼痛程度？') },
      { display_order: 2, is_required: true, display_condition: null, definition: scale(2, 'nausea', '噁心', '過去 24 小時最嚴重的噁心程度？', GI) },
      { display_order: 3, is_required: true, display_condition: null, definition: scale(3, 'fatigue', '疲倦', '過去 24 小時最嚴重的疲倦程度？') },
      {
        display_order: 4,
        is_required: true,
        display_condition: null,
        definition: {
          id: 4,
          code: 'fever',
          name_zh: '發燒或畏寒',
          question_text: '過去 24 小時有沒有發燒（體溫 38°C 以上）或畏寒發抖？',
          help_text: '化療期間發燒可能是嚴重感染的徵兆，請量體溫確認。',
          value_type: 'boolean',
          higher_is_worse: true,
          category: GENERAL,
        },
      },
    ],
  },
}

// 每日症狀與自我照護回報 (= backend/app/seeds/rt_daily_report.py): texts and option order exactly as given
const RT_CATEGORY = { code: 'rt_symptom_24h', name_zh: '過去 24 小時症狀自主管理' }
const SELF_CARE = { code: 'daily_self_care', name_zh: '我的每日自評' }
const choice = (id, code, text, category, worse, options) => ({
  id, code, name_zh: text, question_text: text, help_text: null, value_type: 'single_choice', higher_is_worse: worse, category,
  options: options.map(([value_code, label_zh, score], i) => ({ id: id * 10 + i, value_code, label_zh, score })),
})
const rtScale = (id, code, text) => ({ ...scale(id, code, text, text), min_label: null, max_label: null, category: RT_CATEGORY })
const YES_NO = [['yes', '有', 1], ['no', '沒有', 0]]
const RT_ITEMS = [
  rtScale(101, 'rt_pain_skin', '疼痛－放射線皮膚發紅、脫皮導致'),
  rtScale(102, 'rt_pain_oral', '疼痛－口腔黏膜紅腫、發炎導致'),
  choice(103, 'rt_dermatitis_redness', '放射線皮膚炎－發紅情形', RT_CATEGORY, true, [['none', '無', 0], ['light', '淺紅、粉紅', 1], ['dark', '深紅', 2]]),
  choice(104, 'rt_dermatitis_desquamation', '放射線皮膚炎－脫皮、脫屑情形', RT_CATEGORY, true,
    [['none', '無', 0], ['dry', '乾燥、有脫皮脫屑', 1], ['moist', '潮濕、有脫皮脫屑', 2], ['bleeding', '有脫皮脫屑伴出血', 3]]),
  rtScale(105, 'rt_appetite_poor', '食慾不佳'),
  rtScale(106, 'rt_fatigue', '疲倦'),
  choice(107, 'self_care_moisturizer', '我今天擦保濕乳液或醫師開的藥膏了嗎？', SELF_CARE, false, YES_NO),
  choice(108, 'self_care_towel_pat', '我今天洗完澡有用毛巾「按壓」，沒有來回摩擦皮膚嗎？', SELF_CARE, false, YES_NO),
  choice(109, 'self_care_mouth_rinse', '除了睡覺以外，我有每個小時，以及飯後都有確實漱口嗎？', SELF_CARE, false, YES_NO),
]
mockForms.rt_daily_report = {
  id: 2, code: 'rt_daily_report', name: '每日症狀與自我照護回報', description: null, intended_for: 'patient', availability: 'always',
  recall_period_hours: 24, version: 1,
  items: RT_ITEMS.map((definition, i) => ({ display_order: i + 1, is_required: true, display_condition: null, definition })),
}

const rule = (code, name, definition, dflt, severity) => ({
  code, name, definition, severity,
  get threshold() { return threshold(code, dflt) }, // editable in the admin console (mock alert rules)
  get active() { return ruleOn(code) },
})
const RULES = [
  rule('severe_pain', '疼痛程度偏高', 'pain', 7, 'warning'),
  rule('severe_nausea', '噁心程度偏高', 'nausea', 7, 'warning'),
  rule('severe_fatigue', '疲倦程度偏高', 'fatigue', 8, 'warning'),
  rule('reported_fever', '病人回報發燒或畏寒', 'fever', 1, 'critical'),
]

export { RULES as SYMPTOM_RULES }

function patientMessage(label, score, severity, isBoolean) {
  const base = isBoolean
    ? `您回報了「${label}」，護理團隊已收到通知。`
    : `您的${label}程度偏高（${score} 分），護理團隊已收到通知，會與您聯繫。`
  return base + (severity === 'critical' ? '化療期間請不要等待，請立即撥打照護專線或前往急診。' : '若症狀加重，請撥打照護專線。')
}

let nextId = 1000

/**
 * POST /symptoms/records for rt_daily_report (= create_symptom_record + the once-per-day rule): every
 * question required, options / 0–10 validated, one report per patient per local day (409
 * ALREADY_REPORTED_TODAY), no alert rule; the record reaches the nurse lists and the ≥ 7 risk summary.
 */
function submitMockDailyReport(patientId, body) {
  const form = mockForms.rt_daily_report
  const invalid = (details) => new MockApiError(400, 'VALIDATION_ERROR', '症狀回報內容有誤', details)
  const given = Object.fromEntries((body.values ?? []).map((v) => [v.definition_code, v]))
  const details = []
  const values = []
  for (const item of form.items) {
    const def = item.definition
    const v = given[def.code]
    if (!v) { details.push({ field: 'values', issue: `'${def.code}' is required` }); continue }
    if (def.value_type === 'scale') {
      const n = v.value_numeric
      if (typeof n !== 'number' || !Number.isInteger(n) || n < 0 || n > 10) { details.push({ field: `values.${def.code}.value_numeric`, issue: 'must be between 0 and 10' }); continue }
      values.push({ definition_code: def.code, label: def.name_zh, value_numeric: n, score: n })
    } else {
      const opt = def.options.find((o) => o.value_code === v.option_code)
      if (!opt) { details.push({ field: `values.${def.code}.option_code`, issue: 'is not a valid option' }); continue }
      values.push({ definition_code: def.code, label: def.name_zh, option_code: opt.value_code, option_label: opt.label_zh, score: opt.score })
    }
  }
  if (details.length) throw invalid(details)
  const day = (iso) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date(iso))
  const now = nowIso()
  const existing = mockListRecordsStore(patientId).find((r) => r.form?.code === form.code && r.record_status === 'final' && day(r.recorded_at) === day(now))
  if (existing) {
    throw new MockApiError(409, 'ALREADY_REPORTED_TODAY', '今天已回報',
      [{ field: 'form_code', issue: `already reported on ${day(now)}`, record_id: existing.id }])
  }
  const w = patientDashboards[patientId]?.widgets
  const cycleDay = w?.['treatment-progress']?.current_cycle?.cycle_day ?? null
  const id = nextId++
  addMockSymptomRecord(patientId, { id, recorded_at: now, cycle_day: cycleDay, form: { code: form.code, version: form.version }, values: structuredClone(values), alerts: [] })
  if (w) {
    const nv = w['nurse-view']
    const scored = [...values].sort((a, b) => b.score - a.score)
    nv.pending_symptom_reviews.count += 1
    nv.pending_symptom_reviews.items.unshift({
      id, recorded_at: now, cycle_day: cycleDay, max_score: scored[0]?.score ?? null,
      summary: scored.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}` : `${v.label} ${v.score}`)).join('、'),
    })
    nv.last_report_at = now
    nv.hours_since_last_report = 0
    applyToRiskSummary(w, { symptoms: values.filter((v) => v.value_numeric !== undefined).map((v) => ({ label: v.label, score: v.score })), alerts: [], at: now })
  }
  return {
    id, patient_id: patientId, form: { code: form.code, version: form.version }, recorded_at: now, cycle_day: cycleDay, source: 'patient_app',
    review_status: 'submitted', record_status: 'final', amends_id: null, notes: null, values, triggered_alerts: [],
  }
}

/** Apply a report to the in-memory mock dashboard so the UI updates like it would with the API. */
export function submitMockReport(patientId, body) {
  if (body.form_code === 'rt_daily_report') return submitMockDailyReport(patientId, body)
  const dashboard = patientDashboards[patientId]
  if (!dashboard) throw new Error('找不到這位病人的示範資料')
  const w = dashboard.widgets
  const now = nowIso()
  const today = w['today-schedule'].date
  const cycleDay = w['treatment-progress']?.current_cycle?.cycle_day ?? null
  const form = mockForms[body.form_code]
  const id = nextId++

  const values = body.values.map((v) => {
    const def = form.items.find((i) => i.definition.code === v.definition_code).definition
    const score = def.value_type === 'boolean' ? (v.value_boolean ? 1 : 0) : v.value_numeric
    return { ...v, label: def.name_zh, score }
  })

  const triggered = RULES.flatMap((rule) => {
    const v = values.find((x) => x.definition_code === rule.definition)
    if (!rule.active || !v || v.score < rule.threshold) return []
    const isBoolean = rule.definition === 'fever'
    return [{
      alert_rule_code: rule.code,
      severity: rule.severity,
      symptom: v.label,
      message: patientMessage(v.label, v.score, rule.severity, isBoolean),
      notified: true,
      _title: rule.name,
      _nurseMessage: `${w['patient-summary'].display_name}（${w['patient-summary'].patient_code}）回報${v.label}${isBoolean ? '' : ` ${v.score}/10`}`,
    }]
  }).sort((a, b) => (a.severity === 'critical' ? -1 : 0) - (b.severity === 'critical' ? -1 : 0))

  // Dashboard side effects
  w['symptom-quick-report'].reported_today = true
  w['symptom-quick-report'].today_record_id = id
  w['symptom-quick-report'].last_report = { id, recorded_at: now, cycle_day: cycleDay }
  for (const series of w['symptom-trend'].series) {
    const v = values.find((x) => x.definition_code === series.key)
    const point = series.points.find((p) => p.date === today)
    if (v && point) point.value = Math.max(point.value ?? 0, v.score)
  }
  const nv = w['nurse-view']
  const summary = values.filter((v) => v.definition_code !== 'fever').sort((a, b) => b.score - a.score)
  nv.pending_symptom_reviews.count += 1
  nv.pending_symptom_reviews.items.unshift({
    id,
    recorded_at: now,
    cycle_day: cycleDay,
    summary: summary.map((v) => `${v.label} ${v.score}`).join('、') + (values.find((v) => v.definition_code === 'fever')?.value_boolean ? '、發燒' : ''),
    max_score: summary[0]?.score ?? null,
  })
  nv.last_report_at = now
  nv.hours_since_last_report = 0
  for (const t of triggered) {
    w.notifications.items.unshift({
      id: nextId++, type: 'risk_alert', status: 'new', status_text: '護理團隊已收到通知', severity: t.severity, title: t._title, message: t.message, is_read: false, created_at: now,
    })
    w.notifications.unread_count += 1
    nv.unacknowledged_alerts.items.unshift({
      id: nextId++, event_key: `symptom_records:${id}:rule:${t.alert_rule_code}`, severity: t.severity,
      title: t._title, message: t._nurseMessage, created_at: now,
    })
    nv.unacknowledged_alerts.count += 1
  }
  if (triggered.some((t) => t.severity === 'critical')) {
    nv.risk.level = 'high'
    nv.risk.reasons.unshift(`未處理警示：${triggered.find((t) => t.severity === 'critical')._title}`)
  }

  applyToRiskSummary(w, {
    symptoms: summary.map((v) => ({ label: v.label, score: v.score })),
    alerts: triggered.map((t) => ({ title: t._title, severity: t.severity })),
    at: now,
  })

  addMockSymptomRecord(patientId, {
    id, recorded_at: now, cycle_day: cycleDay, values: values.map(({ definition_code, label, value_numeric, value_boolean, score }) => (
      value_boolean !== undefined ? { definition_code, label, value_boolean, score } : { definition_code, label, value_numeric, score })),
    alerts: triggered.map((t) => ({ event_key: `symptom_records:${id}:rule:${t.alert_rule_code}`, alert_rule_code: t.alert_rule_code, severity: t.severity,
      title: t._title, resolved: false, resolved_at: null, resolved_by: null })),
  })
  return {
    id,
    patient_id: patientId,
    form: { code: form.code, version: form.version },
    recorded_at: now,
    cycle_day: cycleDay,
    source: 'patient_app',
    review_status: 'submitted',
    record_status: 'final',
    amends_id: null,
    values,
    triggered_alerts: triggered.map(({ _title, _nurseMessage, ...t }) => t),
  }
}
