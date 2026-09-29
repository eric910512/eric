/**
 * Built-in copies of GET /api/v1/dashboard/layout (patient home) and GET /api/v1/settings/public.
 * The API is always tried first; these are used only when that request fails (network error,
 * 5xx, …) and as the mock-mode data. Must match the backend (app/modules/dashboard/layouts.py,
 * Config.INSTITUTION) — the contract test compares them. Not patient data.
 */

export const patientLayout = {
  schema_version: 1,
  layout_id: null,
  source: 'code_default',
  scope: 'role',
  version: 1,
  grid: { columns: 1, row_height: 80, gap: 12 },
  pinned: [{ instance_key: 'summary', widget_code: 'patient-summary', config: {} }],
  items: [
    { instance_key: 'risk', widget_code: 'risk-summary', position: { x: 0, y: 0, w: 1, h: 3 }, config: {} },
    { instance_key: 'today', widget_code: 'today-schedule', position: { x: 0, y: 1, w: 1, h: 3 }, config: {} },
    { instance_key: 'progress', widget_code: 'treatment-progress', position: { x: 0, y: 2, w: 1, h: 3 }, config: { show_physician: true } },
    { instance_key: 'report', widget_code: 'symptom-quick-report', position: { x: 0, y: 3, w: 1, h: 2 }, config: {} },
    { instance_key: 'vitals', widget_code: 'latest-vitals', position: { x: 0, y: 4, w: 1, h: 3 }, config: {} },
    { instance_key: 'labs', widget_code: 'lab-summary', position: { x: 0, y: 5, w: 1, h: 3 }, config: {} },
    { instance_key: 'trend', widget_code: 'symptom-trend', position: { x: 0, y: 6, w: 1, h: 4 }, config: {} },
    { instance_key: 'notes', widget_code: 'my-notifications', position: { x: 0, y: 7, w: 1, h: 2 }, config: {} },
  ],
  permissions: { can_edit: false, can_reorder: false, can_remove: false, can_collapse: true },
}

/** Institution display settings, shape of GET /api/v1/settings/public → data (api-design.md v1.1 §12). */
export const publicSettings = {
  organization: { name: 'Demo 醫院', department: '腫瘤內科' },
  contacts: [{ key: 'leave', label: '請假專線', phone: '07-000-0000' }],
  disclaimers: {
    treatment_progress_disclaimer: '以上療程次數僅供參考，正確資訊請依醫療團隊告知為準',
  },
}
