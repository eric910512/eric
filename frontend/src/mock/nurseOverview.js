/**
 * Mock data for the nurse overview, shaped like the Phase 1 nurse widgets
 * (api-design.md v1.1 §8.2: caseload). Today's appointments come from `@/mock/appointments`.
 * All people and codes are synthetic.
 */

export const nurseOverview = {
  nurse: { display_name: '測試護理師 林', department: '腫瘤科（Demo）' },
  caseload: {
    data: [
      {
        patient_id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e',
        patient_code: 'P00002',
        display_name: '測試病人 乙',
        risk_level: 'high',
        risk_reasons: ['骨髓抑制期發燒 38.4°C', '心跳 112 bpm'],
        cycle: { cycle_number: 2, cycle_day: 9 },
        last_report_at: '2026-09-24T03:10:00.000Z',
        hours_since_last_report: 1,
        pending_review_count: 1,
        unacknowledged_alert_count: 1,
        unacknowledged_critical_count: 1, latest_alert_at: '2026-09-24T03:10:05.000Z',
        care_alert_types: ['limb_restriction', 'fall_risk'],
      },
      {
        patient_id: 'd2e3f4a5-6b7c-4d8e-9f0a-1b2c3d4e5f60',
        patient_code: 'P00003',
        display_name: '測試病人 丙',
        risk_level: 'medium',
        risk_reasons: ['治療期間超過 48 小時未回報'],
        cycle: { cycle_number: 1, cycle_day: 6 },
        last_report_at: '2026-09-21T11:00:00.000Z',
        hours_since_last_report: 65,
        pending_review_count: 0,
        unacknowledged_alert_count: 0,
        unacknowledged_critical_count: 0, latest_alert_at: null,
        care_alert_types: [],
      },
      {
        patient_id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0',
        patient_code: 'P00001',
        display_name: '測試病人 甲',
        risk_level: 'low',
        risk_reasons: [],
        cycle: { cycle_number: 1, cycle_day: 4 },
        last_report_at: '2026-09-23T12:30:00.000Z',
        hours_since_last_report: 16,
        pending_review_count: 2,
        unacknowledged_alert_count: 0,
        unacknowledged_critical_count: 0, latest_alert_at: null,
        care_alert_types: ['allergy'],
      },
      {
        patient_id: 'e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a80',
        patient_code: 'P00004',
        display_name: '測試病人 丁',
        risk_level: 'low',
        risk_reasons: [],
        cycle: { cycle_number: 3, cycle_day: 15 },
        last_report_at: '2026-09-24T01:00:00.000Z',
        hours_since_last_report: 3,
        pending_review_count: 0,
        unacknowledged_alert_count: 0,
        unacknowledged_critical_count: 0, latest_alert_at: null,
        care_alert_types: [],
      },
    ],
    meta: { total: 4, high: 1, medium: 1, low: 2, sort: 'risk' },
  },
}
