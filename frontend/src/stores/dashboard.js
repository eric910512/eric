import { defineStore } from 'pinia'

import { api, apiErrorMessage, loadMock, USE_MOCK } from '@/api/client'
import { patientLayout, publicSettings } from '@/config/defaults'
import { useAuthStore } from '@/stores/auth'

const MOCK_DELAY_MS = 250

function mock(value) {
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), MOCK_DELAY_MS))
}

/**
 * Settings / layout: the API (or its mock) first; the built-in copy only when that fails
 * (network error, 5xx, …), so the page still renders. Returns { value, source }.
 */
async function withFallback(request, fallback) {
  try {
    return { value: (await request()).data, source: USE_MOCK ? 'mock' : 'api' }
  } catch (err) {
    console.warn('Using built-in default:', err.message ?? err)
    return { value: structuredClone(fallback), source: 'fallback' }
  }
}

const RISK_RANK = { high: 2, medium: 1, low: 0 }
const hoursDesc = (i) => -(i.hours_since_last_report ?? 1e6)

/** Mock-mode mirror of the backend caseload ordering (dashboard services CASELOAD_SORTS). */
function sortCaseload(items, sort) {
  if (sort === 'name') items.sort((a, b) => a.patient_code.localeCompare(b.patient_code))
  else if (sort === 'last_report') items.sort((a, b) => hoursDesc(a) - hoursDesc(b) || a.patient_code.localeCompare(b.patient_code))
  else {
    items.sort((a, b) =>
      RISK_RANK[b.risk_level] - RISK_RANK[a.risk_level] ||
      b.unacknowledged_critical_count - a.unacknowledged_critical_count ||
      b.unacknowledged_alert_count - a.unacknowledged_alert_count ||
      (b.latest_alert_at ?? '').localeCompare(a.latest_alert_at ?? '') ||
      hoursDesc(a) - hoursDesc(b) ||
      a.patient_code.localeCompare(b.patient_code))
  }
  items.forEach((item, i) => { item.priority_rank = i + 1 })
}

export const useDashboardStore = defineStore('dashboard', {
  state: () => ({
    settings: null,
    patientLayout: null,
    configSource: { settings: null, layout: null }, // 'api' | 'mock' | 'fallback'
    nurseOverview: null,
    patients: {}, // patient_id (or "me") → dashboard payload
    forms: {}, // form code → form definition
    referenceRanges: null,
    caseloadSort: 'risk',
    loading: {},
    errors: {},
  }),

  actions: {
    async _load(key, loader) {
      this.loading[key] = true
      this.errors[key] = null
      try {
        return await loader()
      } catch (err) {
        const status = err.response?.status ?? err.status
        this.errors[key] = status === 404
          ? '找不到這位病人，或您目前沒有負責這位病人。' // same answer for "does not exist" and "not yours"
          : err.response ? apiErrorMessage(err) : err.message ?? '無法載入資料'
        return null
      } finally {
        this.loading[key] = false
      }
    },

    async fetchSettings() {
      const { value, source } = await withFallback(async () =>
        USE_MOCK ? mock((await loadMock()).mockPublicSettings()) : (await api.get('/settings/public')).data,
      publicSettings)
      this.settings = value
      this.configSource.settings = source
    },

    async fetchPatientLayout() {
      const { value, source } = await withFallback(async () =>
        USE_MOCK
          ? mock((await loadMock()).mockDashboardLayout(useAuthStore().role))
          : (await api.get('/dashboard/layout')).data,
      patientLayout)
      this.patientLayout = value
      this.configSource.layout = source
    },

    async fetchPatientDashboard(patientId) {
      const data = await this._load(`patient:${patientId}`, async () => {
        if (!USE_MOCK) return (await api.get(`/dashboard/patient/${patientId}`)).data.data
        return mock((await loadMock()).mockPatientDashboard(patientId))
      })
      if (data) this.patients[patientId] = data
    },

    async fetchSymptomForm(code) {
      if (this.forms[code]) return this.forms[code]
      const form = await this._load(`form:${code}`, async () => {
        if (USE_MOCK) return mock((await loadMock()).mockForms[code])
        return (await api.get(`/symptoms/forms/${code}`)).data.data
      })
      if (form) this.forms[code] = form
      return form
    },

    /**
     * Submit a symptom report, then refresh the dashboard.
     * `idempotencyKey` must stay the same when retrying the same submission
     * (api-design.md §1.7) so a retry never creates a second record or alert.
     * Throws { code, message, details } on failure.
     */
    async submitSymptomReport(patientId, payload, idempotencyKey) {
      let record
      try {
        if (USE_MOCK) {
          await new Promise((r) => setTimeout(r, 400))
          record = (await loadMock()).submitMockReport(patientId, payload)
        } else {
          const res = await api.post('/symptoms/records', { ...payload, patient_id: patientId }, {
            headers: { 'Idempotency-Key': idempotencyKey },
          })
          record = res.data.data
        }
      } catch (err) {
        const error = err.response?.data?.error
        throw {
          code: error?.code ?? (err.response ? 'UNKNOWN' : 'NETWORK'),
          message: err.response ? apiErrorMessage(err) : '網路不穩定，資料還沒送出。請再按一次「送出」重試。',
          details: error?.details ?? [],
          retryable: !err.response || err.response.status >= 500 || error?.code === 'IDEMPOTENCY_IN_PROGRESS',
        }
      }
      this.fetchPatientDashboard(patientId)
      return record
    },

    async fetchReferenceRanges() {
      if (this.referenceRanges) return this.referenceRanges
      this.referenceRanges = await this._load('ranges', async () =>
        USE_MOCK ? mock((await loadMock()).mockReferenceRanges) : (await api.get('/vital-signs/reference-ranges')).data.data,
      )
      return this.referenceRanges
    },

    /** Submit one set of vital signs (same idempotency rules as symptom reports), then refresh. */
    async submitVitalSigns(patientId, payload, idempotencyKey) {
      let record
      try {
        if (USE_MOCK) {
          await new Promise((r) => setTimeout(r, 400))
          record = (await loadMock()).submitMockVitals(patientId, payload)
        } else {
          const res = await api.post('/vital-signs', { ...payload, patient_id: patientId }, {
            headers: { 'Idempotency-Key': idempotencyKey },
          })
          record = res.data.data
        }
      } catch (err) {
        const error = err.response?.data?.error
        throw {
          code: error?.code ?? (err.response ? 'UNKNOWN' : 'NETWORK'),
          message: err.response ? apiErrorMessage(err) : '網路不穩定，資料還沒送出。請再按一次「儲存」重試。',
          details: error?.details ?? [],
          retryable: !err.response || err.response.status >= 500 || error?.code === 'IDEMPOTENCY_IN_PROGRESS',
        }
      }
      this.fetchPatientDashboard(patientId)
      return record
    },

    /** Nurse caseload. sort: risk (default, highest risk first) | last_report | name */
    async fetchNurseOverview(sort = this.caseloadSort) {
      this.caseloadSort = sort
      this.nurseOverview = await this._load('nurse', async () => {
        if (USE_MOCK) {
          const data = await mock((await loadMock()).mockNurseOverview())
          sortCaseload(data.caseload.data, sort)
          data.caseload.meta.sort = sort
          return data
        }
        const auth = useAuthStore()
        const [res, today] = await Promise.all([
          api.get('/dashboard/widgets/caseload/data', { params: { sort } }),
          api.get('/dashboard/widgets/today-appointments/data'),
        ])
        return {
          nurse: { display_name: auth.user?.display_name, department: null },
          caseload: { data: res.data.data, meta: res.data.meta },
          todayAppointments: today.data.data,
        }
      })
    },
  },
})
