/**
 * Mock symptom form + submission (VITE_USE_MOCK=true).
 * The form has the shape of GET /api/v1/symptoms/forms/{code} → data, and submitMockReport
 * returns the shape of POST /api/v1/symptoms/records → data. The alert thresholds mirror
 * the backend seed rules (severe_pain / severe_nausea / severe_fatigue / reported_fever).
 */
import { ruleOn, threshold } from '@/mock/alertRules'
import { nowIso } from '@/mock/clock'
import { addMockSymptomRecord } from '@/mock/nurseReview'
import { applyToRiskSummary, patientDashboards } from '@/mock/patientDashboards'

const scale = (id, code, name, question) => ({
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
      { display_order: 2, is_required: true, display_condition: null, definition: scale(2, 'nausea', '噁心', '過去 24 小時最嚴重的噁心程度？') },
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
        },
      },
    ],
  },
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

/** Apply a report to the in-memory mock dashboard so the UI updates like it would with the API. */
export function submitMockReport(patientId, body) {
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
