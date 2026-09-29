/**
 * Mock data in the exact shape of
 *   GET /api/v1/dashboard/patient/<patient_id>  →  response.data
 * (backend API Sprint 1). All people and codes are synthetic.
 *
 * Swapping to the real API only requires the store to call the endpoint instead.
 */

const TODAY = '2026-09-24'

function days(to, count) {
  const end = new Date(`${to}T00:00:00Z`)
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(end)
    d.setUTCDate(end.getUTCDate() - (count - 1 - i))
    return d.toISOString().slice(0, 10)
  })
}

/** Build a symptom-trend series. `cycleStart` = Day 1 date; `values` maps date → score. */
function series(key, label, dates, cycleStarts, values) {
  return {
    key,
    label,
    higher_is_worse: true,
    points: dates.map((date) => {
      const started = cycleStarts.filter((c) => c.start <= date).at(-1)
      const cycleDay = started
        ? Math.round((new Date(date) - new Date(started.start)) / 86400000) + 1
        : null
      return {
        date,
        cycle_number: started ? started.number : null,
        cycle_day: cycleDay,
        value: values[date] ?? null,
      }
    }),
  }
}

const trendDates = days(TODAY, 14)

/* ------------------------------------------------------------------ P00001 */
const p1Cycles = [{ number: 1, start: '2026-09-21' }]

const P00001 = {
  patient_id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0',
  generated_at: '2026-09-24T04:47:27.000Z',
  widgets: {
    'patient-summary': {
      patient_id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0',
      patient_code: 'P00001',
      display_name: '測試病人 甲',
      age: 56,
      gender: 'male',
      care_alerts: [
        { alert_type: 'allergy', body_site: null, description: 'Penicillin 過敏', severity: 'high' },
      ],
    },
    'risk-summary': {
      date: TODAY, level: 'low', status: 'stable', title: '今天狀況穩定',
      message: '請繼續每天記錄症狀和生命徵象，有不舒服隨時回報。', reasons: [], in_nadir: false,
      today: {
        symptom_reported: false, last_symptom_report_at: null, symptoms: [],
        vitals_recorded: false, last_vitals_at: null, vital_flags: [],
        alerts: { total: 0, open: 0, critical: 0, items: [] },
      },
      actions: [{ code: 'report_symptoms', label: '回報今天的症狀' }, { code: 'measure_vitals', label: '量測並記錄生命徵象' }],
    },
    'today-schedule': {
      date: TODAY,
      server_time: '2026-09-24T04:47:27.000Z',
      appointments: [
        {
          id: 1,
          appointment_type: 'clinic_visit',
          title: '門診追蹤與抽血',
          scheduled_at: '2026-09-24T06:00:00.000Z',
          location: '腫瘤科門診（Demo）',
          status: 'scheduled',
        },
      ],
      highlight_instructions: [
        { appointment_id: 1, instruction_type: 'check_in', due_at: '2026-09-24T05:40:00.000Z', text: '13:40 報到' },
      ],
      quick_contact: { key: 'leave', label: '請假專線', phone: '07-000-0000' },
    },
    'treatment-progress': {
      plan_id: 1,
      diagnosis_name: '鼻咽癌',
      regimen_name: 'Cisplatin q3w',
      attending_physician_name: '測試醫師 王',
      completed_cycles: 0,
      total_cycles: 3,
      current_cycle: { cycle_number: 1, cycle_day: 4, in_nadir: false, nadir_end_date: '2026-10-04' },
      next_cycle_date: '2026-10-12',
      next_cycle_date_estimated: true,
      disclaimer_key: 'treatment_progress_disclaimer',
    },
    'latest-vitals': {
      temperature_c: { value: 37.2, measured_at: '2026-09-23T12:30:00.000Z', flag: null },
      heart_rate_bpm: { value: 88, measured_at: '2026-09-23T12:30:00.000Z', flag: null },
      blood_pressure: {
        systolic: 118,
        diastolic: 76,
        measure_site: 'left_arm',
        measured_at: '2026-09-23T12:30:00.000Z',
        flag: null,
      },
      respiratory_rate: { value: 18, measured_at: '2026-09-23T12:30:00.000Z', flag: null },
      spo2_pct: { value: 98, measured_at: '2026-09-23T12:30:00.000Z', flag: null },
      weight_kg: { value: 64.0, measured_at: '2026-09-23T12:30:00.000Z', change_pct_7d: null, flag: null },
    },
    'symptom-trend': {
      bucket: 'day',
      agg: 'max',
      from: trendDates[0],
      to: TODAY,
      series: [
        series('pain', '疼痛', trendDates, p1Cycles, { '2026-09-22': 2, '2026-09-23': 3 }),
        series('nausea', '噁心', trendDates, p1Cycles, { '2026-09-22': 6, '2026-09-23': 4 }),
        series('fatigue', '疲倦', trendDates, p1Cycles, { '2026-09-22': 5, '2026-09-23': 6 }),
      ],
      cycle_markers: [
        { cycle_number: 1, start_date: '2026-09-21', nadir_start_date: '2026-09-27', nadir_end_date: '2026-10-04' },
      ],
    },
    'symptom-quick-report': {
      form: { code: 'daily_chemo_check', name: '化療每日症狀自評', version: 1, item_count: 3 },
      reported_today: false,
      today_record_id: null,
      last_report: { id: 2, recorded_at: '2026-09-23T12:00:00.000Z', cycle_day: 3 },
    },
    notifications: {
      items: [
        {
          id: 1,
          type: 'reminder',
          severity: 'info',
          title: '今日回診提醒',
          message: '今天 14:00 門診追蹤與抽血，請於 13:40 報到。',
          is_read: false,
          created_at: '2026-09-24T00:00:00.000Z',
        },
      ],
      unread_count: 1,
    },
    'nurse-view': {
      care_team: [
        {
          nurse_id: '49e33737-64c5-4c53-bda1-887244da1ccb',
          display_name: '測試護理師 林',
          is_primary: true,
          assigned_at: '2026-09-24T04:22:50.000Z',
        },
      ],
      risk: { level: 'low', reasons: [] },
      last_report_at: '2026-09-23T12:30:00.000Z',
      hours_since_last_report: 16,
      pending_symptom_reviews: {
        count: 2,
        items: [
          { id: 2, recorded_at: '2026-09-23T12:00:00.000Z', cycle_day: 3, summary: '疲倦 6、噁心 4、疼痛 3', max_score: 6 },
          { id: 1, recorded_at: '2026-09-22T12:00:00.000Z', cycle_day: 2, summary: '噁心 6、疲倦 5、疼痛 2', max_score: 6 },
        ],
      },
      unacknowledged_alerts: { count: 0, items: [] },
      pending_assessment_signoff: { count: 0, items: [] },
      latest_assessment: null,
    },
  },
}

/* ------------------------------------------------------------------ P00002 (high risk) */
const p2Cycles = [
  { number: 1, start: '2026-08-26' },
  { number: 2, start: '2026-09-16' },
]

const P00002 = {
  patient_id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e',
  generated_at: '2026-09-24T04:47:27.000Z',
  widgets: {
    'patient-summary': {
      patient_id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e',
      patient_code: 'P00002',
      display_name: '測試病人 乙',
      age: 62,
      gender: 'female',
      care_alerts: [
        { alert_type: 'limb_restriction', body_site: 'right_arm', description: '右手禁止注射及量血壓', severity: 'high' },
        { alert_type: 'fall_risk', body_site: null, description: '跌倒高風險', severity: 'medium' },
      ],
    },
    'risk-summary': {
      date: TODAY, level: 'high', status: 'urgent', title: '請立即聯絡醫療團隊',
      message: '今天有需要馬上處理的狀況。請撥打照護專線；如果無法接通，請直接前往急診。',
      reasons: ['骨髓抑制期發燒 38.4°C', '心跳 112 bpm', '7 天體重變化 -3.5%', '疑似嗜中性白血球低下發燒（護理團隊處理中）'], in_nadir: true,
      today: {
        symptom_reported: true, last_symptom_report_at: '2026-09-24T02:40:00.000Z',
        symptoms: [{ label: '疲倦', score: 8 }, { label: '疼痛', score: 4 }, { label: '噁心', score: 2 }],
        vitals_recorded: true, last_vitals_at: '2026-09-24T03:10:00.000Z',
        vital_flags: [
          { field: 'temperature_c', level: 'critical', message: '體溫 38.4°C，目前處於骨髓抑制期，請立即聯絡醫療團隊' },
          { field: 'heart_rate_bpm', level: 'warning', message: '心跳偏快（112次/分）' },
        ],
        alerts: { total: 1, open: 1, critical: 1, items: [
          { id: 12, event_key: 'vital_signs:902:rule:5', title: '體溫偏高，請立即聯絡醫療團隊', severity: 'critical', created_at: '2026-09-24T03:10:05.000Z', status: 'new', status_text: '護理團隊已收到通知', resolved: false, resolved_at: null },
        ] },
      },
      actions: [{ code: 'call_hotline', label: '撥打照護專線' }],
    },
    'today-schedule': {
      date: TODAY,
      server_time: '2026-09-24T04:47:27.000Z',
      appointments: [],
      highlight_instructions: [],
      quick_contact: { key: 'leave', label: '請假專線', phone: '07-000-0000' },
    },
    'treatment-progress': {
      plan_id: 2,
      diagnosis_name: '乳癌',
      regimen_name: 'AC q3w',
      attending_physician_name: '測試醫師 陳',
      completed_cycles: 1,
      total_cycles: 4,
      current_cycle: { cycle_number: 2, cycle_day: 9, in_nadir: true, nadir_end_date: '2026-09-29' },
      next_cycle_date: '2026-10-07',
      next_cycle_date_estimated: false,
      disclaimer_key: 'treatment_progress_disclaimer',
    },
    'latest-vitals': {
      temperature_c: { value: 38.4, measured_at: '2026-09-24T03:10:00.000Z', flag: 'critical' },
      heart_rate_bpm: { value: 112, measured_at: '2026-09-24T03:10:00.000Z', flag: 'warning' },
      blood_pressure: {
        systolic: 104,
        diastolic: 66,
        measure_site: 'left_arm',
        measured_at: '2026-09-24T03:10:00.000Z',
        flag: null,
      },
      respiratory_rate: { value: 22, measured_at: '2026-09-24T03:10:00.000Z', flag: null },
      spo2_pct: { value: 95, measured_at: '2026-09-24T03:10:00.000Z', flag: null },
      weight_kg: { value: 55.2, measured_at: '2026-09-24T03:10:00.000Z', change_pct_7d: -3.5, flag: 'warning' },
    },
    'symptom-trend': {
      bucket: 'day',
      agg: 'max',
      from: trendDates[0],
      to: TODAY,
      series: [
        series('pain', '疼痛', trendDates, p2Cycles, {
          '2026-09-16': 1, '2026-09-18': 2, '2026-09-20': 2, '2026-09-22': 3, '2026-09-23': 4, '2026-09-24': 4,
        }),
        series('nausea', '噁心', trendDates, p2Cycles, {
          '2026-09-16': 3, '2026-09-17': 7, '2026-09-18': 8, '2026-09-19': 6, '2026-09-20': 4,
          '2026-09-22': 3, '2026-09-23': 3, '2026-09-24': 2,
        }),
        series('fatigue', '疲倦', trendDates, p2Cycles, {
          '2026-09-16': 3, '2026-09-17': 5, '2026-09-18': 6, '2026-09-19': 6, '2026-09-20': 7,
          '2026-09-22': 7, '2026-09-23': 8, '2026-09-24': 8,
        }),
      ],
      cycle_markers: [
        { cycle_number: 2, start_date: '2026-09-16', nadir_start_date: '2026-09-22', nadir_end_date: '2026-09-29' },
      ],
    },
    'symptom-quick-report': {
      form: { code: 'daily_chemo_check', name: '化療每日症狀自評', version: 1, item_count: 3 },
      reported_today: true,
      today_record_id: 31,
      last_report: { id: 31, recorded_at: '2026-09-24T02:40:00.000Z', cycle_day: 9 },
    },
    notifications: {
      items: [
        {
          id: 12,
          event_key: 'vital_signs:902:rule:5',
          type: 'risk_alert',
          status: 'new',
          status_text: '護理團隊已收到通知',
          severity: 'critical',
          title: '體溫偏高，請立即聯絡醫療團隊',
          message: '您目前在骨髓抑制期，體溫 38.4°C。請撥打照護專線或直接前往急診。',
          is_read: false,
          created_at: '2026-09-24T03:10:05.000Z',
        },
      ],
      unread_count: 1,
    },
    'nurse-view': {
      care_team: [
        {
          nurse_id: '49e33737-64c5-4c53-bda1-887244da1ccb',
          display_name: '測試護理師 林',
          is_primary: true,
          assigned_at: '2026-08-20T01:00:00.000Z',
        },
      ],
      risk: {
        level: 'high',
        reasons: ['骨髓抑制期發燒 38.4°C', '心跳 112 bpm', '7 天體重變化 -3.5%', '未處理警示：疑似嗜中性白血球低下發燒'],
      },
      last_report_at: '2026-09-24T03:10:00.000Z',
      hours_since_last_report: 1,
      pending_symptom_reviews: {
        count: 1,
        items: [
          { id: 31, recorded_at: '2026-09-24T02:40:00.000Z', cycle_day: 9, summary: '疲倦 8、疼痛 4、噁心 2', max_score: 8 },
        ],
      },
      unacknowledged_alerts: {
        count: 1,
        items: [
          {
            id: 11,
            event_key: 'vital_signs:902:rule:5',
            status: 'new',
            severity: 'critical',
            title: '疑似嗜中性白血球低下發燒',
            message: '測試病人 乙（P00002）體溫 38.4°C，Cycle 2 Day 9（骨髓抑制期）',
            created_at: '2026-09-24T03:10:05.000Z',
          },
        ],
      },
      pending_assessment_signoff: { count: 0, items: [] },
      latest_assessment: {
        id: 7,
        assessment_type: 'phone_follow_up',
        assessed_at: '2026-09-22T06:00:00.000Z',
        risk_level: 'medium',
        overall_condition: 'concern',
        sign_status: 'signed',
      },
    },
  },
}

export const patientDashboards = {
  [P00001.patient_id]: P00001,
  [P00002.patient_id]: P00002,
}

/** Keep the mock risk-summary in step with a submission (the real API derives it server-side). */
export function applyToRiskSummary(w, { symptoms = null, vitalFlags = null, alerts = [], at }) {
  const rs = w['risk-summary']
  if (!rs) return
  if (symptoms) {
    rs.today.symptom_reported = true
    rs.today.last_symptom_report_at = at
    rs.today.symptoms = symptoms.slice(0, 3)
    rs.actions = rs.actions.filter((a) => a.code !== 'report_symptoms')
  }
  if (vitalFlags) {
    rs.today.vitals_recorded = true
    rs.today.last_vitals_at = at
    rs.today.vital_flags = vitalFlags
    rs.actions = rs.actions.filter((a) => a.code !== 'measure_vitals')
  }
  for (const a of alerts) {
    rs.today.alerts.items.unshift({ id: Math.floor(Math.random() * 1e6), event_key: a.event_key ?? null, title: a.title, severity: a.severity, created_at: at, status: 'new', status_text: '護理團隊已收到通知', resolved: false, resolved_at: null })
    rs.today.alerts.total += 1
    rs.today.alerts.open += 1
    if (a.severity === 'critical') rs.today.alerts.critical += 1
    rs.reasons.push(`${a.title}（護理團隊處理中）`)
  }
  if (alerts.some((a) => a.severity === 'critical')) {
    Object.assign(rs, { level: 'high', status: 'urgent', title: '請立即聯絡醫療團隊', message: '今天有需要馬上處理的狀況。請撥打照護專線；如果無法接通，請直接前往急診。' })
    if (!rs.actions.some((a) => a.code === 'call_hotline')) rs.actions.unshift({ code: 'call_hotline', label: '撥打照護專線' })
  } else if (alerts.length && rs.level === 'low') {
    Object.assign(rs, { level: 'medium', status: 'attention', title: '今天有些狀況需要留意', message: '請多休息並持續記錄，護理團隊會追蹤您的狀況。' })
  }
}
