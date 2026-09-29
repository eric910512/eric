/**
 * Mock vital-sign submission (VITE_USE_MOCK=true) — shape of POST /api/v1/vital-signs → data.
 * Mirrors the backend seed rules: fever / suspected_febrile_neutropenia (≥38.0, split by nadir),
 * tachycardia (HR ≥120), hypotension (SBP ≤90), low_spo2 (≤90).
 */
import { ruleOn, threshold } from '@/mock/alertRules'
import { applyToRiskSummary, patientDashboards } from '@/mock/patientDashboards'
import { vitalFlag, vitalHint, VITAL_FIELDS } from '@/utils/vitals'
import { nowIso } from '@/mock/clock'

export const mockReferenceRanges = {
  temperature_c: { warning_high: 37.5, critical_high: 38.0 },
  heart_rate_bpm: { warning_low: 50, warning_high: 100, critical_high: 130 },
  systolic_bp_mmhg: { warning_low: 90, warning_high: 160 },
  spo2_pct: { warning_low: 94, critical_low: 90 },
  weight_change_pct_7d: { warning_low: -3.0 },
}

// thresholds / on-off come from the mock alert rules (editable in the admin console)
const at = (code, dflt, cmp) => (v) => ruleOn(code) && cmp(v, threshold(code, dflt))
const RULES = [
  { code: 'suspected_febrile_neutropenia', name: '疑似嗜中性白血球低下發燒', field: 'temperature_c', test: at('suspected_febrile_neutropenia', 38, (v, t) => v >= t), severity: 'critical', nadir: true },
  { code: 'fever', name: '化療期間發燒', field: 'temperature_c', test: at('fever', 38, (v, t) => v >= t), severity: 'critical', nadir: false },
  { code: 'tachycardia', name: '心跳過快', field: 'heart_rate_bpm', test: at('tachycardia', 120, (v, t) => v >= t), severity: 'warning' },
  { code: 'hypotension', name: '血壓偏低', field: 'systolic_bp_mmhg', test: at('hypotension', 90, (v, t) => v <= t), severity: 'warning' },
  { code: 'low_spo2', name: '血氧過低', field: 'spo2_pct', test: at('low_spo2', 90, (v, t) => v <= t), severity: 'critical' },
]

const fmt = (field, v) => `${Number(v).toFixed(VITAL_FIELDS[field].decimals)}${VITAL_FIELDS[field].unit}`
let nextId = 5000
/** Readings submitted in mock mode (the corrections mock reads / changes them). */
export const submittedVitals = []
export { RULES as VITAL_RULES }

export function submitMockVitals(patientId, body) {
  const dashboard = patientDashboards[patientId]
  if (!dashboard) throw new Error('找不到這位病人的示範資料')
  const w = dashboard.widgets
  const now = nowIso()
  const measuredAt = body.measured_at ?? now
  const inNadir = !!w['treatment-progress']?.current_cycle?.in_nadir
  const id = nextId++

  const flags = Object.keys(VITAL_FIELDS)
    .filter((f) => body[f] != null && vitalFlag(f, body[f], mockReferenceRanges))
    .map((f) => {
      const level = vitalFlag(f, body[f], mockReferenceRanges)
      let message = `${vitalHint(f, body[f], mockReferenceRanges).text}（${fmt(f, body[f])}）`
      if (f === 'temperature_c' && level === 'critical') {
        message = inNadir ? `體溫 ${fmt(f, body[f])}，目前處於骨髓抑制期，請立即聯絡醫療團隊` : `體溫 ${fmt(f, body[f])}，化療期間發燒請立即聯絡醫療團隊`
      }
      return { field: f, level, message }
    })
    .sort((a, b) => (a.level === 'critical' ? -1 : 0) - (b.level === 'critical' ? -1 : 0))

  const triggered = RULES
    .filter((r) => body[r.field] != null && r.test(body[r.field]) && (r.nadir === undefined || r.nadir === inNadir))
    .map((r) => {
      const label = VITAL_FIELDS[r.field].label
      const value = fmt(r.field, body[r.field])
      return {
        alert_rule_code: r.code, severity: r.severity, field: r.field, label, value, notified: true,
        message: `您的${label}為 ${value}，護理團隊已收到通知，會與您聯繫。` +
          (r.severity === 'critical' ? '化療期間請不要等待，請立即撥打照護專線或前往急診。' : '若情況加重，請撥打照護專線。'),
        _title: r.name,
      }
    })
    .sort((a, b) => (a.severity === 'critical' ? -1 : 0) - (b.severity === 'critical' ? -1 : 0))

  const restricted = w['patient-summary'].care_alerts.filter(
    (a) => a.alert_type === 'limb_restriction' && a.body_site && a.body_site === body.bp_measure_site,
  )
  const warnings = restricted.map((a) => ({ code: 'LIMB_RESTRICTION', message: `注意：${a.description}。本次血壓量測部位為${{ left_arm: '左手', right_arm: '右手', leg: '腳' }[body.bp_measure_site]}。` }))

  // Dashboard side effects: latest values, notifications, nurse view
  const lv = w['latest-vitals']
  const put = (key, field) => {
    if (body[field] != null) lv[key] = { ...(lv[key] ?? {}), value: body[field], measured_at: measuredAt, flag: vitalFlag(field, body[field], mockReferenceRanges) }
  }
  put('temperature_c', 'temperature_c')
  put('heart_rate_bpm', 'heart_rate_bpm')
  put('spo2_pct', 'spo2_pct')
  put('respiratory_rate', 'respiratory_rate')
  if (body.weight_kg != null) lv.weight_kg = { value: body.weight_kg, measured_at: measuredAt, change_pct_7d: null, flag: null }
  if (body.systolic_bp_mmhg != null) {
    lv.blood_pressure = { systolic: body.systolic_bp_mmhg, diastolic: body.diastolic_bp_mmhg, measure_site: body.bp_measure_site ?? null,
      measured_at: measuredAt, flag: vitalFlag('systolic_bp_mmhg', body.systolic_bp_mmhg, mockReferenceRanges) }
  }
  const nv = w['nurse-view']
  nv.last_report_at = now
  nv.hours_since_last_report = 0
  for (const t of triggered) {
    w.notifications.items.unshift({ id: nextId++, type: 'risk_alert', status: 'new', status_text: '護理團隊已收到通知', severity: t.severity, title: t._title, message: t.message, is_read: false, created_at: now })
    w.notifications.unread_count += 1
    nv.unacknowledged_alerts.items.unshift({ id: nextId++, event_key: `vital_signs:${id}:rule:${t.alert_rule_code}`, severity: t.severity, title: t._title, message: t.message, created_at: now })
    nv.unacknowledged_alerts.count += 1
  }

  applyToRiskSummary(w, { vitalFlags: flags, alerts: triggered.map((t) => ({ title: t._title, severity: t.severity })), at: measuredAt })

  submittedVitals.push({
    id, patient_id: patientId, measured_at: measuredAt, cycle_day: w['treatment-progress']?.current_cycle?.cycle_day ?? null, source: 'patient_app',
    record_status: 'final', amends_id: null, notes: null, recorded_by: null, flags, severity: flags[0]?.level ?? null,
    ...Object.fromEntries(Object.keys(VITAL_FIELDS).map((f) => [f, body[f] ?? null])),
    temperature_site: body.temperature_site ?? null, bp_measure_site: body.bp_measure_site ?? null,
  })
  return {
    id, patient_id: patientId, measured_at: measuredAt, cycle_day: w['treatment-progress']?.current_cycle?.cycle_day ?? null,
    source: 'patient_app', record_status: 'final',
    ...Object.fromEntries(Object.keys(VITAL_FIELDS).map((f) => [f, body[f] ?? null])),
    temperature_site: body.temperature_site ?? null, bp_measure_site: body.bp_measure_site ?? null, notes: null,
    flags, triggered_alerts: triggered.map(({ _title, ...t }) => t), warnings,
  }
}
