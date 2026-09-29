/**
 * Mock GET /api/v1/settings/public and GET /api/v1/dashboard/layout — same response shape
 * ({ data, meta }) and the same role rule as the backend (layouts exist for patients only).
 * The content is the built-in defaults, which the backend serves unchanged.
 */
import { patientLayout, publicSettings } from '@/config/defaults'

export function mockPublicSettings() {
  return { data: structuredClone(publicSettings), meta: { source: 'config_file' } }
}

export function mockDashboardLayout(role, context = 'overview') {
  if (role !== 'patient') {
    throw Object.assign(new Error('此角色目前沒有可設定的版面（護理端與管理端畫面為固定版面）'), { status: 404, code: 'NOT_FOUND' })
  }
  return { data: structuredClone(patientLayout), meta: { role, context } }
}
