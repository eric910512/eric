/**
 * Mock Patient Care Timeline (VITE_USE_MOCK=true), in the shape of
 *   GET /api/v1/patients/{id}/timeline → { data, meta }
 * Built at call time from the other mock modules (symptom records, labs, notifications with
 * their lifecycle, vitals), so status changes made in the session show up. Same privacy
 * rules as the backend: patients get their own notification wording and status text only,
 * no staff names, internal notes, drafts or risk judgements. Synthetic data only.
 */
import { mockTimelineAppointments } from '@/mock/appointments'
import { mockTimelineCycles, mockTimelineMedications } from '@/mock/chemotherapy'
import { mockTimelineAssessments } from '@/mock/nursing'
import { mockTimelineVitals } from '@/mock/records'
import { byTimeDesc } from '@/mock/clock'
import { mockLabHistory } from '@/mock/labs'
import { mockListAbnormal, mockListNotifications, mockListRecords } from '@/mock/nurseReview'
import { patientDashboards } from '@/mock/patientDashboards'
import { VITAL_FIELDS } from '@/utils/vitals'

const P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'
const NURSE = '測試護理師 林'
const MEDICATION_TYPE_TEXT = { chemo: '化療給藥', premedication: '前置用藥', supportive: '支持性用藥' }
const ADMIN_STATUS_TEXT = { given: '已給藥', held: '暫停給藥', partial: '部分給藥', refused: '拒絕給藥' }

const PATIENT_STEP = { acknowledged: '護理師已接手', in_progress: '護理師正在處理', resolved: '已處理完成' }
const STAFF_STEP = { acknowledged: '接手', in_progress: '開始處理', resolved: '完成處理' }
const STEP_KEY = { acknowledged: 'acknowledged', in_progress: 'started', resolved: 'resolved' }
const RANK = { resolved: 1, in_progress: 2, acknowledged: 3, NOTIFICATION: 4, NURSING_ASSESSMENT: 5, LAB_RESULT: 6, VITAL_SIGN: 7, SYMPTOM: 8, medication: 9, cycle_end: 10, cycle_start: 11, APPOINTMENT: 12 }
const PATIENT_LAB = { WBC: '白血球', ANC: '嗜中性白血球（抵抗力）', HGB: '血色素', PLT: '血小板' }
const LAB_STATUS = { N: '正常', L: '偏低', LL: '過低', H: '偏高', HH: '過高' }
const LAB_LEVEL = { LL: 'critical', HH: 'critical', L: 'warning', H: 'warning' }

const midnightUtc = (d) => new Date(`${d}T00:00:00+08:00`).toISOString()
const localDay = (iso) => new Date(new Date(iso).getTime() + 8 * 3600000).toISOString().slice(0, 10)
const days = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000)

function cycleAt(patientId, iso) {
  const day = localDay(iso)
  const c = [...mockTimelineCycles(patientId)].reverse().find((x) => x.start <= day)
  return c ? { cycle_id: c.id, cycle_day: days(c.start, day) + 1 } : { cycle_id: null, cycle_day: null }
}

function ev(patientId, e) {
  return { all_day: false, severity: null, ...cycleAt(patientId, e.occurred_at), ...e, source_id: e.source?.id ?? null }
}

function build(patientId, staff) {
  const events = []
  const w = patientDashboards[patientId]?.widgets
  if (!w) return events

  // Chemotherapy: cycle start / end and final medication records (from the mock chemotherapy state)
  for (const c of mockTimelineCycles(patientId)) {
    const detail = { kind: 'cycle_start', cycle_number: c.number, regimen: c.regimen, scheduled_date: c.scheduled, status: c.status, total_cycles: c.total }
    events.push(ev(patientId, { event_id: `CHEMOTHERAPY:cycle_start:${c.id}`, event_type: 'CHEMOTHERAPY', occurred_at: midnightUtc(c.start), all_day: true,
      title: `第 ${c.number} 次化療開始`, summary: `${c.regimen ? `${c.regimen}，` : ''}療程第 1 天`, source: { table: 'chemotherapy_cycles', id: c.id }, cycle_id: c.id, cycle_day: 1, detail, _rank: RANK.cycle_start }))
    if (c.end) {
      events.push(ev(patientId, { event_id: `CHEMOTHERAPY:cycle_end:${c.id}`, event_type: 'CHEMOTHERAPY', occurred_at: midnightUtc(c.end), all_day: true,
        title: `第 ${c.number} 次化療結束`, summary: `${c.regimen ? `${c.regimen}，` : ''}本次療程結束`, source: { table: 'chemotherapy_cycles', id: c.id }, cycle_id: c.id,
        cycle_day: days(c.start, c.end) + 1, detail: { ...detail, kind: 'cycle_end' }, _rank: RANK.cycle_end }))
    }
  }
  for (const m of mockTimelineMedications(patientId)) {
    const statusText = ADMIN_STATUS_TEXT[m.administration_status] ?? m.administration_status
    const dose = `${m.dose_value} ${m.dose_unit}`
    events.push(ev(patientId, { event_id: `CHEMOTHERAPY:medication:${m.id}`, event_type: 'CHEMOTHERAPY', occurred_at: m.administered_at,
      title: MEDICATION_TYPE_TEXT[m.medication_type] ?? '給藥', summary: `${m.drug} ${dose}${m.route ? ` ${m.route}` : ''}（${statusText}）`,
      source: { table: 'medication_records', id: m.id }, cycle_id: m.cycle_id, cycle_day: m.cycle_day, _rank: RANK.medication,
      detail: { kind: 'medication', medication_type: m.medication_type, drug: m.drug, dose, route: m.route, administration_status: m.administration_status,
        administration_status_text: statusText, infusion_duration_min: m.infusion_duration_min,
        ...(staff ? { reaction_notes: m.reaction_notes, administered_by: m.administrator } : {}) } }))
  }

  for (const a of mockTimelineAppointments(patientId, staff)) events.push(ev(patientId, { ...a, _rank: RANK.APPOINTMENT }))

  for (const r of mockListRecords(patientId, { reviewStatus: 'all' }).data) {
    const values = r.values.map((v) => (v.value_boolean !== undefined
      ? { code: v.definition_code, label: v.label, value: v.value_boolean ? '有' : '沒有' }
      : { code: v.definition_code, label: v.label, value: String(v.score), score: v.score }))
    const scored = values.filter((v) => v.score != null).sort((a, b) => b.score - a.score)
    const fever = values.some((v) => v.value === '有')
    events.push(ev(patientId, { event_id: `SYMPTOM:${r.id}`, event_type: 'SYMPTOM', occurred_at: r.recorded_at, title: '症狀回報',
      summary: [...scored.slice(0, 3).map((v) => `${v.label} ${v.value}`), ...(fever ? ['發燒或畏寒'] : [])].join('、'),
      severity: fever ? 'critical' : scored[0]?.score >= 7 ? 'warning' : null, source: { table: 'symptom_records', id: r.id },
      cycle_day: r.cycle_day, _rank: RANK.SYMPTOM,
      detail: { values, notes: r.notes, reviewed: r.review_status === 'reviewed',
        ...(staff ? { review_status: r.review_status, reviewed_by: r.reviewed_by?.display_name ?? null } : {}) } }))
  }

  const vitals = mockTimelineVitals(patientId)
  for (const v of vitals) {
    const level = Object.fromEntries((v.flags ?? []).map((f) => [f.field, f.level]))
    const values = Object.entries(VITAL_FIELDS).filter(([k]) => v[k] != null)
      .map(([k, f]) => ({ field: k, label: f.label, value: `${Number(v[k]).toFixed(f.decimals)}${f.unit}`, flag: level[k] ?? null }))
    const short = values.filter((x) => !['systolic_bp_mmhg', 'diastolic_bp_mmhg'].includes(x.field)).map((x) => `${x.label} ${x.value}`)
    if (v.systolic_bp_mmhg) short.splice(1, 0, `血壓 ${v.systolic_bp_mmhg}/${v.diastolic_bp_mmhg}`)
    events.push(ev(patientId, { event_id: `VITAL_SIGN:${v.id}`, event_type: 'VITAL_SIGN', occurred_at: v.measured_at, title: '生命徵象量測',
      summary: short.slice(0, 4).join('、'), severity: v.flags?.[0]?.level ?? null, source: { table: 'vital_signs', id: v.id }, _rank: RANK.VITAL_SIGN,
      detail: { values, flags: (v.flags ?? []).map((f) => f.message), temperature_site: v.temperature_site, bp_measure_site: v.bp_measure_site,
        ...(staff ? { source: 'patient_app', recorded_by: null } : {}) } }))
  }

  const panels = new Map()
  for (const r of mockLabHistory(patientId, { days: 365 }).data.results) {
    if (!panels.has(r.collected_at)) panels.set(r.collected_at, [])
    panels.get(r.collected_at).push(r)
  }
  for (const [at, rows] of panels) {
    const first = Math.min(...rows.map((r) => r.id))
    const levels = rows.map((r) => LAB_LEVEL[r.abnormal_flag])
    const items = staff
      ? rows.map((r) => ({ test_code: r.test_code, name: r.name_zh, value: r.value, unit: r.unit, ref_low: r.ref_low, ref_high: r.ref_high, abnormal_flag: r.abnormal_flag }))
      : rows.map((r) => ({ code: r.test_code, label: PATIENT_LAB[r.test_code], value: r.value, unit: r.unit, status_text: LAB_STATUS[r.abnormal_flag], level: LAB_LEVEL[r.abnormal_flag] ?? null }))
    events.push(ev(patientId, { event_id: `LAB_RESULT:${first}`, event_type: 'LAB_RESULT', occurred_at: at, title: '抽血檢驗',
      summary: staff ? items.map((i) => `${i.test_code} ${i.value}${i.abnormal_flag !== 'N' ? ` ${i.abnormal_flag}` : ''}`).join('、')
        : items.map((i) => `${i.label} ${i.value}（${i.status_text}）`).join('、'),
      severity: levels.includes('critical') ? 'critical' : levels.includes('warning') ? 'warning' : null,
      source: { table: 'lab_results', id: first, ids: rows.map((r) => r.id) }, cycle_day: rows[0].cycle_day, _rank: RANK.LAB_RESULT, detail: { items } }))
  }

  const patientCopies = w.notifications.items.filter((n) => n.type === 'risk_alert')
  for (const n of mockListNotifications({ status: 'all', patientId }).data) {
    const mine = patientCopies.find((p) => p.event_key && p.event_key === n.event_key)
    const message = staff ? n.message : mine?.message ?? `「${n.title}」護理團隊已收到通知，會與您聯繫。`
    events.push(ev(patientId, { event_id: `NOTIFICATION:${n.id}`, event_type: 'NOTIFICATION', occurred_at: n.created_at, title: n.title, summary: message,
      severity: n.severity, source: { table: 'notifications', id: n.id }, _rank: RANK.NOTIFICATION,
      detail: { notification_id: n.id, message, status: n.status, status_text: staff ? n.status_text : PATIENT_STEP[n.status] ?? '護理團隊已收到通知',
        ...(staff ? { recommended_action: null } : {}) } }))
    const seen = new Set()
    for (const status of ['resolved', 'in_progress', 'acknowledged']) {
      const step = n.handling?.[STEP_KEY[status]]
      if (!step || seen.has(step.at)) continue // steps sharing one timestamp (quick resolve) collapse into the latest
      seen.add(step.at)
      events.push(ev(patientId, { event_id: `NOTIFICATION_STATUS:${n.id}:${status}`, event_type: 'NOTIFICATION_STATUS', occurred_at: step.at,
        title: staff ? `${STAFF_STEP[status]}：${n.title}` : PATIENT_STEP[status],
        summary: staff ? `${step.by.display_name}${STAFF_STEP[status]}${status === 'resolved' && n.handling.resolution_note ? `：${n.handling.resolution_note}` : ''}`
          : `「${n.title}」${PATIENT_STEP[status]}`,
        source: { table: 'notifications', id: n.id }, _rank: RANK[status],
        detail: staff ? { notification_id: n.id, status, by: step.by.display_name, ...(status === 'resolved' ? { resolution_note: n.handling.resolution_note } : {}) }
          : { notification_id: n.id, status } }))
    }
  }

  for (const a of mockTimelineAssessments(patientId, staff)) events.push(ev(patientId, { ...a, _rank: RANK.NURSING_ASSESSMENT }))
  return events
}

/** Same contract as the API: newest first, limit, cursor (here an offset), local-date range. */
export function mockTimeline(patientId, { startDate = null, endDate = null, limit = 30, cursor = null, audience = 'patient' } = {}) {
  if (!patientDashboards[patientId]) throw new Error('找不到這位病人的示範資料')
  const all = build(patientId, audience === 'staff')
    .filter((e) => (!startDate || localDay(e.occurred_at) >= startDate) && (!endDate || localDay(e.occurred_at) <= endDate))
    .sort((a, b) => byTimeDesc(a.occurred_at, b.occurred_at) || a._rank - b._rank || (b.source_id ?? 0) - (a.source_id ?? 0))
  const offset = cursor ? Number(cursor) : 0
  const page = all.slice(offset, offset + limit).map(({ _rank, ...e }) => structuredClone(e))
  const hasMore = offset + limit < all.length
  return {
    data: page,
    meta: { limit, returned: page.length, has_more: hasMore, next_cursor: hasMore ? String(offset + limit) : null,
      start_date: startDate, end_date: endDate, timezone: 'Asia/Taipei',
      event_types: ['CHEMOTHERAPY', 'SYMPTOM', 'VITAL_SIGN', 'LAB_RESULT', 'NOTIFICATION', 'NOTIFICATION_STATUS', 'NURSING_ASSESSMENT', 'APPOINTMENT'] },
  }
}
