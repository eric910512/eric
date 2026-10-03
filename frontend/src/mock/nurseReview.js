/**
 * Mock data for the nurse review workflow (VITE_USE_MOCK=true), in the shapes of
 *   GET /api/v1/notifications, GET /api/v1/notifications/{id},
 *   POST /api/v1/notifications/{id}/acknowledge|start|resolve, GET /api/v1/symptoms/records/{patient_id}
 * Mutations update this module's in-memory state (reset on page reload). Synthetic data only.
 */
import { afterSteps, byTimeDesc, nowIso } from '@/mock/clock'
import { nurseOverview } from '@/mock/nurseOverview'
import { recordReviewAssessment } from '@/mock/nursing'
import { patientDashboards } from '@/mock/patientDashboards'

const NURSE = { id: 'mock-user-nurse01', display_name: '測試護理師 林' }
const P1 = { id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0', patient_code: 'P00001', display_name: '測試病人 甲' }
const P2 = { id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e', patient_code: 'P00002', display_name: '測試病人 乙' }

const notifications = [
  {
    id: 11, event_key: 'vital_signs:902:rule:5', type: 'risk_alert', severity: 'critical',
    title: '疑似嗜中性白血球低下發燒', message: '測試病人 乙（P00002）體溫 38.4°C，Cycle 2 Day 9（骨髓抑制期）',
    patient: P2, alert_rule: { id: 5, code: 'suspected_febrile_neutropenia' }, source: { table: 'vital_signs', id: 902 },
    is_read: false, read_at: null, acknowledged: null, created_at: '2026-09-24T03:10:05.000Z',
  },
  {
    id: 13, event_key: 'symptom_records:31:rule:3', type: 'risk_alert', severity: 'warning',
    title: '疲倦程度偏高', message: '測試病人 乙（P00002）回報疲倦 8/10，Cycle 2 Day 9',
    patient: P2, alert_rule: { id: 3, code: 'severe_fatigue' }, source: { table: 'symptom_records', id: 31 },
    is_read: false, read_at: null, acknowledged: null, created_at: '2026-09-24T02:40:05.000Z',
  },
  {
    id: 7, event_key: 'symptom_records:24:rule:2', type: 'risk_alert', severity: 'warning',
    title: '噁心程度偏高', message: '測試病人 乙（P00002）回報噁心 8/10，Cycle 2 Day 3',
    patient: P2, alert_rule: { id: 2, code: 'severe_nausea' }, source: { table: 'symptom_records', id: 24 },
    is_read: true, read_at: '2026-09-18T02:00:00.000Z',
    acknowledged: { by: NURSE, at: '2026-09-18T02:05:00.000Z', resolution_note: '已電話衛教止吐藥使用時機，隔日追蹤。' },
    created_at: '2026-09-18T01:30:05.000Z',
  },
]

const value = (code, label, score) => ({ definition_code: code, label, value_numeric: score, score })
const fever = (yes) => ({ definition_code: 'fever', label: '發燒或畏寒', value_boolean: yes, score: yes ? 1 : 0 })

const records = {
  [P1.id]: [
    { id: 2, recorded_at: '2026-09-23T12:00:00.000Z', cycle_day: 3, values: [value('pain', '疼痛', 3), value('nausea', '噁心', 4), value('fatigue', '疲倦', 6)], alerts: [] },
    { id: 1, recorded_at: '2026-09-22T12:00:00.000Z', cycle_day: 2, values: [value('pain', '疼痛', 2), value('nausea', '噁心', 6), value('fatigue', '疲倦', 5)], alerts: [] },
  ].map((r) => ({ ...r, patient: P1 })),
  [P2.id]: [
    {
      id: 31, recorded_at: '2026-09-24T02:40:00.000Z', cycle_day: 9,
      values: [value('pain', '疼痛', 4), value('nausea', '噁心', 2), value('fatigue', '疲倦', 8), fever(false)],
      alerts: [{ event_key: 'symptom_records:31:rule:3', alert_rule_code: 'severe_fatigue', severity: 'warning', title: '疲倦程度偏高', resolved: false, resolved_at: null, resolved_by: null }],
    },
    {
      id: 24, recorded_at: '2026-09-18T01:30:00.000Z', cycle_day: 3,
      values: [value('pain', '疼痛', 2), value('nausea', '噁心', 8), value('fatigue', '疲倦', 6), fever(false)],
      alerts: [{ event_key: 'symptom_records:24:rule:2', alert_rule_code: 'severe_nausea', severity: 'warning', title: '噁心程度偏高', resolved: true, resolved_at: '2026-09-18T02:05:00.000Z', resolved_by: NURSE.display_name }],
      review_status: 'reviewed', reviewed_at: '2026-09-18T02:05:00.000Z', reviewed_by: NURSE,
      review: { nursing_assessment_id: 7, assessment_type: 'phone_follow_up', action_note: '已電話衛教止吐藥使用時機，隔日追蹤。', risk_level: 'medium' },
    },
  ].map((r) => ({ ...r, patient: P2 })),
}

for (const list of Object.values(records)) {
  for (const r of list) {
    Object.assign(r, {
      patient_id: r.patient.id,
      form: { code: 'daily_chemo_check', version: 1 },
      source: 'patient_app',
      record_status: 'final',
      notes: null,
      reported_by: { id: `mock-${r.patient.patient_code}`, display_name: r.patient.display_name },
      amends_id: null,
      review_status: r.review_status ?? 'submitted',
      reviewed_at: r.reviewed_at ?? null,
      reviewed_by: r.reviewed_by ?? null,
      review: r.review ?? null,
    })
    delete r.patient
  }
}

const now = nowIso
const clone = (v) => structuredClone(v)

// ------------------------------------------------------------------ notification lifecycle
// new → acknowledged → in_progress → resolved (same rules as the backend)
const STAFF_TEXT = { new: '待處理', acknowledged: '已接手', in_progress: '處理中', resolved: '已完成' }
const PATIENT_TEXT = { new: '護理團隊已收到通知', acknowledged: '護理師已接手', in_progress: '護理師正在處理', resolved: '已處理完成' }
const OPEN = ['new', 'acknowledged', 'in_progress']
const PENDING = ['new', 'acknowledged']
const TRANSITIONS = { acknowledge: ['new', 'acknowledged'], start: ['acknowledged', 'in_progress'], resolve: ['in_progress', 'resolved'] }
const ACTION_TEXT = { acknowledge: '接手', start: '開始處理', resolve: '完成處理' }
const STEP_KEY = { acknowledge: 'acknowledged', start: 'started', resolve: 'resolved' }
// demo wording, same as the backend seed (to be confirmed clinically)
const RECOMMENDED = {
  severe_pain: '電話評估疼痛部位、性質與止痛藥使用情形；必要時聯絡醫師調整止痛處方。',
  severe_nausea: '確認止吐藥是否依時服用、進食與飲水量；有脫水徵象或無法進食時安排回診。',
  severe_fatigue: '評估活動耐受度與睡眠，確認是否合併發燒、喘或貧血症狀，衛教節省體力。',
  suspected_febrile_neutropenia: '視為緊急狀況：立即聯絡病人前往急診，通知主治醫師，依嗜中性白血球低下發燒流程處理。',
  severe_neutropenia: '立即電話聯絡病人確認有無發燒，衛教感染預防；通知主治醫師評估是否調整療程或使用白血球生長激素。',
  neutropenia: '電話衛教感染預防（避免人多處、勤洗手、避免生食），提醒發燒 ≥38°C 立即就醫，下次抽血追蹤。',
}

function initLifecycle(n) {
  if (n.status) return
  const done = n.acknowledged // seed rows resolved before the lifecycle existed
  const step = done ? { by: done.by, at: done.at } : null
  n.status = done ? 'resolved' : 'new'
  n.handling = { acknowledged: step, started: step, resolved: step, resolution_note: done?.resolution_note ?? null }
  delete n.acknowledged
}
notifications.forEach(initLifecycle)

// risk alerts and reminders (manual / scheduled, Sprint 8) follow the lifecycle on the staff side
const LIFECYCLE = ['risk_alert', 'reminder']
/** Reminders scheduled for later stay hidden until their time (= backend `_visible`). */
const due = (n) => !n.scheduled_for || Date.parse(n.scheduled_for) <= Date.parse(now())
const originOf = (n) => n.origin ?? (n.type === 'risk_alert' ? 'alert_rule' : 'scheduled')

/** API payload for the nurse (staff view). */
function view(n) {
  const { _detail, delivered, ...rest } = n // eslint-disable-line no-unused-vars -- internal mock flags
  // email_delivery: email channel of staff-sent reminders (separate from the handling status); null otherwise
  const out = { ...clone(rest), origin: originOf(n), scheduled_for: n.scheduled_for ?? null, email_delivery: clone(n.email_delivery ?? null) }
  if (!LIFECYCLE.includes(n.type)) return { ...out, status: null, status_text: null, handling: null, is_mine: true }
  const mine = n.type === 'risk_alert' // a reminder's recipient is the patient
  return {
    ...out,
    status_text: STAFF_TEXT[n.status],
    is_mine: mine,
    ...(mine ? {} : { is_read: null, read_at: null }),
    acknowledged: n.status === 'resolved'
      ? { by: n.handling.resolved.by, at: n.handling.resolved.at, resolution_note: n.handling.resolution_note } : null,
  }
}

function statusFilter(status) {
  if (status === 'open' || status === 'unresolved') return (n) => OPEN.includes(n.status)
  if (status === 'pending') return (n) => PENDING.includes(n.status)
  if (STAFF_TEXT[status]) return (n) => n.status === status
  return () => true
}

/** Mirror a status change onto the patient's copy, the nurse view and the caseload. */
function mirror(n) {
  const w = patientDashboards[n.patient?.id]?.widgets
  if (w) {
    const patch = { status: n.status, status_text: PATIENT_TEXT[n.status], resolved: n.status === 'resolved', resolved_at: n.handling.resolved?.at ?? null }
    for (const item of w.notifications.items) if (item.event_key && item.event_key === n.event_key) Object.assign(item, patch)
    const rs = w['risk-summary']
    if (rs) {
      for (const item of rs.today.alerts.items) if (item.event_key && item.event_key === n.event_key) Object.assign(item, patch)
      rs.today.alerts.open = rs.today.alerts.items.filter((a) => !a.resolved).length
    }
    const nv = w['nurse-view']
    nv.unacknowledged_alerts.items = nv.unacknowledged_alerts.items
      .map((a) => (a.event_key === n.event_key || a.id === n.id ? { ...a, status: n.status } : a))
      .filter((a) => a.status !== 'resolved')
    nv.unacknowledged_alerts.count = nv.unacknowledged_alerts.items.length
  }
  const row = nurseOverview.caseload.data.find((p) => p.patient_id === n.patient?.id)
  if (row) {
    const open = notifications.filter((x) => x.patient?.id === n.patient.id && x.type === 'risk_alert' && OPEN.includes(x.status))
    row.unacknowledged_alert_count = open.length
    row.unacknowledged_critical_count = open.filter((x) => x.severity === 'critical').length
  }
}

function patientContext(patientId) {
  const w = patientDashboards[patientId]?.widgets
  if (!w) return null
  const s = w['patient-summary']
  const c = w['treatment-progress']?.current_cycle
  return {
    id: patientId, patient_code: s.patient_code, display_name: s.display_name,
    diagnoses: [w['treatment-progress']?.diagnosis_name].filter(Boolean),
    care_alerts: s.care_alerts.map((a) => ({ alert_type: a.alert_type, description: a.description })),
    current_cycle: c ? { cycle_number: c.cycle_number, cycle_day: c.cycle_day, in_nadir: c.in_nadir } : null,
  }
}

const SYMPTOM_OF_RULE = { severe_pain: ['pain', 7], severe_nausea: ['nausea', 7], severe_fatigue: ['fatigue', 8] }

function mockSource(n) {
  if (n._detail) return n._detail
  if (n.source?.table === 'symptom_records') {
    const r = Object.values(records).flat().find((x) => x.id === n.source.id)
    const [code, threshold] = SYMPTOM_OF_RULE[n.alert_rule?.code] ?? []
    const v = r?.values.find((x) => x.definition_code === code)
    return {
      trigger: v ? { rule_code: n.alert_rule.code, rule_name: n.title, source_type: 'symptom', severity: n.severity,
        label: v.label, value: String(v.score), condition: `${v.label} >= ${threshold}` } : null,
      source_record: r ? { table: 'symptom_records', id: r.id, recorded_at: r.recorded_at, cycle_day: r.cycle_day, source: r.source,
        review_status: r.review_status, notes: r.notes, values: clone(r.values) } : null,
    }
  }
  if (n.source?.table === 'vital_signs') {
    const v = abnormalVitals.find((x) => x.id === n.source.id)
    const keys = ['temperature_c', 'heart_rate_bpm', 'systolic_bp_mmhg', 'diastolic_bp_mmhg', 'spo2_pct', 'respiratory_rate', 'weight_kg',
      'temperature_site', 'bp_measure_site']
    return {
      trigger: v ? { rule_code: n.alert_rule.code, rule_name: n.title, source_type: 'vital_sign', severity: n.severity,
        label: '體溫', value: `${v.temperature_c}°C`, condition: '體溫 >= 38 且在骨髓抑制期' } : null,
      source_record: v ? { table: 'vital_signs', id: v.id, measured_at: v.measured_at, cycle_day: v.cycle_day, source: v.source,
        values: Object.fromEntries(keys.map((k) => [k, v[k]])) } : null,
    }
  }
  return { trigger: null, source_record: null }
}

function find(id) {
  const n = notifications.find((x) => x.id === id && due(x))
  if (!n) throw Object.assign(new Error('找不到通知'), { status: 404, code: 'NOT_FOUND' })
  return n
}

export function mockGetNotification(id) {
  const n = find(id)
  const src = mockSource(n)
  return {
    ...view(n),
    patient: patientContext(n.patient?.id) ?? clone(n.patient),
    trigger: clone(src.trigger),
    source_record: clone(src.source_record),
    recommended_action: RECOMMENDED[n.alert_rule?.code] ?? null,
    recipients: n.type === 'reminder' ? 1 : 2,
    allowed_actions: Object.entries(TRANSITIONS).filter(([, [from]]) => from === n.status).map(([a]) => a),
  }
}

export function mockTransition(id, action, note) {
  const n = find(id)
  const [from, to] = TRANSITIONS[action]
  if (n.status !== from) {
    throw Object.assign(new Error(`這則警示目前是「${STAFF_TEXT[n.status]}」，不能${ACTION_TEXT[action]}`), { status: 409, code: 'INVALID_TRANSITION' })
  }
  if (action === 'resolve' && !note?.trim()) {
    throw Object.assign(new Error('請填寫處理說明'), { status: 400, code: 'VALIDATION_ERROR', details: [{ field: 'resolution_note', issue: 'is required' }] })
  }
  const step = { by: NURSE, at: afterSteps([n.handling.acknowledged?.at, n.handling.started?.at]) }
  n.status = to
  n.handling[STEP_KEY[action]] = step
  if (action === 'resolve') {
    n.handling.resolution_note = note.trim()
    markRecordAlerts(n)
  }
  if (n.type === 'risk_alert') {
    n.is_read = true
    n.read_at ??= step.at
  }
  mirror(n)
  return mockGetNotification(id)
}

/** `visible(patientId)`: the signed-in user's patient scope (see `@/mock/api`). */
export function mockListNotifications({ status = 'all', patientId = null, priority = null, type = 'risk_alert', visible = () => true } = {}) {
  const scoped = notifications.filter((n) => n.type === (type || 'risk_alert') && due(n) && (!patientId || n.patient?.id === patientId)
    && (!priority || n.severity === priority) && visible(n.patient?.id))
  const items = scoped.filter(statusFilter(status)).sort((a, b) =>
    status === 'resolved' ? byTimeDesc(a.handling.resolved.at, b.handling.resolved.at) : byTimeDesc(a.created_at, b.created_at))
  const counts = Object.fromEntries(Object.keys(STAFF_TEXT).map((k) => [k, scoped.filter((n) => n.status === k).length]))
  counts.open = OPEN.reduce((t, k) => t + counts[k], 0)
  counts.pending = PENDING.reduce((t, k) => t + counts[k], 0)
  return {
    data: items.map(view),
    meta: {
      page: 1, per_page: 50, total: items.length, counts, unresolved: counts.open,
      unread: notifications.filter((n) => n.type === 'risk_alert' && !n.is_read && visible(n.patient?.id)).length,
    },
  }
}

export function mockMarkRead(id) {
  const n = notifications.find((x) => x.id === id && x.type === 'risk_alert')
  if (!n) throw new Error('找不到通知')
  if (!n.is_read) Object.assign(n, { is_read: true, read_at: now() })
  return view(n)
}

/** Show the resolution on the source record's alert list. */
function markRecordAlerts(n) {
  for (const list of Object.values(records)) {
    for (const r of list) {
      for (const a of r.alerts) {
        if (a.event_key === n.event_key) {
          Object.assign(a, { resolved: true, status: 'resolved', resolved_at: n.handling.resolved.at, resolved_by: n.handling.resolved.by.display_name })
        }
      }
    }
  }
}

/** Quick close (existing flows): fill any lifecycle step not yet taken. */
function closeEvent(n, note) {
  const step = { by: NURSE, at: afterSteps([n.handling.acknowledged?.at, n.handling.started?.at]) }
  n.handling.acknowledged ??= step
  n.handling.started ??= step
  n.handling.resolved = step
  n.handling.resolution_note = note
  n.status = 'resolved'
  n.is_read = true
  n.read_at ??= step.at
  markRecordAlerts(n)
  mirror(n)
}

export function mockResolve(id, note) {
  const n = find(id)
  if (n.type !== 'risk_alert') throw Object.assign(new Error('只有風險警示需要處理'), { status: 422, code: 'INVALID_STATE' })
  if (n.status === 'resolved') {
    throw Object.assign(new Error(`這則警示已由${n.handling.resolved.by.display_name}處理`), { code: 'INVALID_STATE' })
  }
  closeEvent(n, note)
  return { ...view(n), related_notifications_updated: 0 }
}

/** Symptom records by patient (the corrections mock adds / changes rows here). */
export const symptomRecordStore = records

/** A report submitted in mock mode joins the nurse's lists (like the API). */
export function addMockSymptomRecord(patientId, rec) {
  const p = patientContext(patientId)
  ;(records[patientId] ??= []).unshift({
    form: { code: 'daily_chemo_check', version: 1 }, source: 'patient_app', record_status: 'final', notes: null, amends_id: null,
    reported_by: p ? { id: `mock-${p.patient_code}`, display_name: p.display_name } : null,
    review_status: 'submitted', reviewed_at: null, reviewed_by: null, review: null, alerts: [], ...rec, patient_id: patientId,
  })
}

/** Close every open alert raised by one source record (corrections mock). Returns the rule codes. */
export function closeAlertsOf(table, id, note) {
  const rows = notifications.filter((n) => n.source?.table === table && n.source.id === id && n.status !== 'resolved')
  for (const n of rows) closeEvent(n, note)
  return [...new Set(rows.map((n) => n.alert_rule?.code).filter(Boolean))]
}
/** Open alerts of one source record: rule code → notifications. */
export function openAlertsOf(table, id) {
  const out = {}
  for (const n of notifications) {
    if (n.source?.table === table && n.source.id === id && n.status !== 'resolved') (out[n.alert_rule?.code] ??= []).push(n)
  }
  return out
}
export function closeNotifications(rows, note) {
  for (const n of rows) closeEvent(n, note)
}

export function mockListRecords(patientId, { reviewStatus = 'all' } = {}) {
  const all = (records[patientId] ?? []).filter((r) => r.record_status === 'final')
  const items = reviewStatus === 'all' ? all : all.filter((r) => r.review_status === reviewStatus)
  return {
    data: clone(items),
    meta: {
      page: 1, per_page: 50, total: items.length,
      submitted: all.filter((r) => r.review_status === 'submitted').length,
      reviewed: all.filter((r) => r.review_status === 'reviewed').length,
    },
  }
}

export function mockReview(recordId, payload) {
  const record = Object.values(records).flat().find((r) => r.id === recordId)
  if (!record) throw new Error('找不到症狀紀錄')
  if (record.review_status === 'reviewed') {
    throw Object.assign(new Error(`這筆紀錄已由${record.reviewed_by.display_name}審閱`), { code: 'INVALID_STATE' })
  }
  Object.assign(record, {
    review_status: 'reviewed',
    reviewed_at: now(),
    reviewed_by: NURSE,
    review: {
      // like the API: the review is a signed nursing assessment (shows in the timeline / nurse view)
      nursing_assessment_id: recordReviewAssessment(record.patient_id, NURSE.id, {
        assessmentType: payload.assessment_type, riskLevel: payload.risk_level ?? null, plan: payload.action_note,
        subjective: record.values.map((v) => `${v.label} ${v.score ?? ''}`.trim()).join('、'),
      }),
      assessment_type: payload.assessment_type ?? 'phone_follow_up',
      action_note: payload.action_note,
      risk_level: payload.risk_level ?? null,
    },
  })
  let resolved = 0
  if (payload.resolve_alerts !== false) {
    for (const n of notifications) {
      if (n.source?.table === 'symptom_records' && n.source.id === recordId && n.status !== 'resolved') {
        closeEvent(n, payload.action_note)
        resolved += 1
      }
    }
  }
  const nv = patientDashboards[record.patient_id]?.widgets['nurse-view']
  if (nv) {
    nv.pending_symptom_reviews.items = nv.pending_symptom_reviews.items.filter((i) => i.id !== recordId)
    nv.pending_symptom_reviews.count = Math.max(0, nv.pending_symptom_reviews.count - 1)
  }
  const row = nurseOverview.caseload.data.find((p) => p.patient_id === record.patient_id)
  if (row) row.pending_review_count = Math.max(0, row.pending_review_count - 1)
  return { ...clone(record), resolved_notifications: resolved }
}

const abnormalVitals = [
  {
    id: 902, patient: P2, measured_at: '2026-09-24T03:10:00.000Z', cycle_day: 9, in_nadir: true, source: 'patient_app',
    temperature_c: 38.4, heart_rate_bpm: 112, systolic_bp_mmhg: 104, diastolic_bp_mmhg: 66, spo2_pct: 95,
    respiratory_rate: 22, weight_kg: 55.2, pain_score: null, temperature_site: 'ear', bp_measure_site: 'left_arm',
    flags: [
      { field: 'temperature_c', level: 'critical', message: '體溫 38.4°C，目前處於骨髓抑制期，請立即聯絡醫療團隊' },
      { field: 'heart_rate_bpm', level: 'warning', message: '心跳偏快（112次/分）' },
    ],
    severity: 'critical',
  },
  {
    id: 887, patient: P2, measured_at: '2026-09-23T12:00:00.000Z', cycle_day: 8, in_nadir: true, source: 'patient_app',
    temperature_c: 37.8, heart_rate_bpm: 98, systolic_bp_mmhg: 110, diastolic_bp_mmhg: 70, spo2_pct: 96,
    respiratory_rate: null, weight_kg: null, pain_score: null, temperature_site: 'ear', bp_measure_site: 'left_arm',
    flags: [{ field: 'temperature_c', level: 'warning', message: '體溫偏高（37.8°C）' }],
    severity: 'warning',
  },
]
for (const v of abnormalVitals) Object.assign(v, { patient_id: v.patient.id, record_status: 'final', amends_id: null, notes: null, recorded_by: null })
/** Flagged seed readings (P00002); the corrections mock changes their status here. */
export const abnormalVitalRecords = abnormalVitals

export function mockListAbnormal({ hours = 72, visible = () => true } = {}) {
  const items = abnormalVitals.filter((v) => visible(v.patient.id) && v.record_status === 'final').map((v) => ({
    ...clone(v),
    alerts: notifications
      .filter((n) => n.source?.table === 'vital_signs' && n.source.id === v.id)
      .map((n) => ({
        event_key: n.event_key, alert_rule_code: n.alert_rule?.code, severity: n.severity, title: n.title,
        status: n.status, resolved: n.status === 'resolved', resolved_by: n.handling.resolved?.by.display_name ?? null,
        resolution_note: n.handling.resolution_note, my_notification_id: n.id,
      })),
  }))
  return {
    data: items,
    meta: {
      hours, total: items.length,
      critical: items.filter((i) => i.severity === 'critical').length,
      warning: items.filter((i) => i.severity === 'warning').length,
      unresolved_alerts: items.flatMap((i) => i.alerts).filter((a) => !a.resolved).length,
    },
  }
}

/** Patient of a notification / symptom record (for the access guard in `@/mock/api`). */
export const mockNotificationPatient = (id) => notifications.find((x) => x.id === id)?.patient?.id ?? null
export const mockRecordPatient = (recordId) => Object.values(records).flat().find((r) => r.id === recordId)?.patient_id ?? null

/** Add a nurse notification raised by another mock module (e.g. lab alerts). Returns its id. */
export function addMockNurseNotification(n) {
  const id = Math.max(...notifications.map((x) => x.id)) + 1
  notifications.push({
    id, type: 'risk_alert', is_read: false, read_at: null, status: 'new',
    handling: { acknowledged: null, started: null, resolved: null, resolution_note: null }, ...n,
  })
  const row = nurseOverview.caseload.data.find((p) => p.patient_id === n.patient?.id)
  if (row) {
    row.unacknowledged_alert_count += 1
    if (n.severity === 'critical') row.unacknowledged_critical_count += 1
    row.latest_alert_at = n.created_at
  }
  return id
}

/** Resolution state of a notification (so other mock modules show it on their records). */
export function mockNotificationState(id) {
  const n = notifications.find((x) => x.id === id)
  if (!n) return null
  return {
    status: n.status,
    acknowledged: n.status === 'resolved'
      ? clone({ by: n.handling.resolved.by, at: n.handling.resolved.at, resolution_note: n.handling.resolution_note }) : null,
  }
}

// ------------------------------------------------------------------ reminders (Sprint 8)
/** Add a manual / scheduled reminder (staff side; the patient copy is delivered by `@/mock/reminders`). */
export function addMockReminder(r) {
  const id = Math.max(5000, ...notifications.map((x) => x.id)) + 1
  const n = {
    id, event_key: `reminder:${crypto.randomUUID()}`, type: 'reminder', alert_rule: null, source: null, is_read: false, read_at: null,
    status: 'new', handling: { acknowledged: null, started: null, resolved: null, resolution_note: null }, ...r,
  }
  notifications.push(n)
  return n
}
/** A reminder row (for delivery to the patient's notification list). */
export const mockReminderRows = () => notifications.filter((n) => n.type === 'reminder')
export const mockReminderView = (n) => view(n)
/** GET /notifications/scheduled?patient_id= : not yet due, soonest first. */
export function mockScheduledReminders(patientId) {
  const rows = notifications.filter((n) => n.type === 'reminder' && n.patient?.id === patientId && !due(n))
    .sort((a, b) => Date.parse(a.scheduled_for) - Date.parse(b.scheduled_for) || a.id - b.id)
  return {
    data: rows.map((n) => ({ id: n.id, event_key: n.event_key, type: n.type, origin: originOf(n), severity: n.severity, title: n.title,
      message: n.message, scheduled_for: n.scheduled_for, created_at: n.created_at, patient: clone(n.patient),
      email_delivery: clone(n.email_delivery ?? null) })),
  }
}
