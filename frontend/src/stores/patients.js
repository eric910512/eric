import { defineStore } from 'pinia'

import { api, call } from '@/api/call'

/**
 * Patient & care-team management (api-design.md §4, §13): patient list / detail / edit,
 * login accounts, care alerts, diagnoses, nurse assignments, nurse accounts (admin).
 * The backend decides who may see what; the UI only mirrors it (a 404 means "not yours").
 */
export const usePatientsStore = defineStore('patients', {
  state: () => ({
    list: null, // { items, meta, params }
    details: {}, // patient id → detail
    assignments: {}, // patient id → assignment history
    staff: null, // nurse accounts (admin)
    cancerTypes: null,
    loading: {},
    errors: {}, // key → { status, code, message }
  }),

  actions: {
    async _load(key, loader) {
      this.loading[key] = true
      this.errors[key] = null
      try {
        return await loader()
      } catch (err) {
        this.errors[key] = err
        return null
      } finally {
        this.loading[key] = false
      }
    },

    /** params: { q, assigned: any|true|false (admin), sort, page } */
    async fetchList(params = this.list?.params ?? {}) {
      const { q = '', assigned = 'any', sort = 'patient_code', page = 1 } = params
      const query = { sort, page, per_page: 50, ...(q ? { q } : {}), ...(assigned !== 'any' ? { assigned } : {}) }
      const body = await this._load('list', () =>
        call('mockListPatients', [{ q, assigned, sort, page, perPage: 50 }], () => api.get('/patients', { params: query })))
      if (body) this.list = { items: body.data, meta: body.meta, params: { q, assigned, sort, page } }
    },

    async fetchPatient(id) {
      const body = await this._load(`patient:${id}`, () => call('mockGetPatient', [id], () => api.get(`/patients/${id}`)))
      if (body) this.details[id] = body.data
      else delete this.details[id]
      return body?.data ?? null
    },

    /** POST /patients → { id, patient_code, …, account: { email, temporary_password } | null }. Shown once, never stored. */
    async createPatient(payload) {
      const body = await call('mockCreatePatient', [payload], () => api.post('/patients', payload))
      this.list = null
      return body.data
    },

    async updatePatient(id, changes) {
      const body = await call('mockUpdatePatient', [id, changes], () => api.patch(`/patients/${id}`, changes))
      this.details[id] = body.data
      return body.data
    },

    /** POST /patients/{id}/account → { email, temporary_password } (shown once). */
    async createAccount(id, email) {
      const body = await call('mockCreateAccount', [id, { email }], () => api.post(`/patients/${id}/account`, { email }))
      await this.fetchPatient(id)
      return body.data
    },

    async fetchCancerTypes() {
      if (this.cancerTypes) return this.cancerTypes
      const body = await this._load('cancer-types', () => call('mockListCancerTypes', [], () => api.get('/patients/cancer-types')))
      this.cancerTypes = body?.data ?? null
      return this.cancerTypes
    },

    async addCareAlert(id, payload) {
      await call('mockCreateCareAlert', [id, payload], () => api.post(`/patients/${id}/care-alerts`, payload))
      await this.fetchPatient(id)
    },

    async updateCareAlert(id, alertId, changes) {
      await call('mockUpdateCareAlert', [id, alertId, changes], () => api.patch(`/patients/${id}/care-alerts/${alertId}`, changes))
      await this.fetchPatient(id)
    },

    async addDiagnosis(id, payload) {
      await call('mockCreateDiagnosis', [id, payload], () => api.post(`/patients/${id}/diagnoses`, payload))
      await this.fetchPatient(id)
    },

    async fetchAssignments(id) {
      const body = await this._load(`assignments:${id}`, () =>
        call('mockListAssignments', [id], () => api.get(`/patients/${id}/nurse-assignments`)))
      if (body) this.assignments[id] = body.data
    },

    /** Admin: assign a nurse ({ nurse_id, is_primary }). */
    async assignNurse(id, payload) {
      const body = await call('mockCreateAssignment', [id, payload], () => api.post(`/patients/${id}/nurse-assignments`, payload))
      await Promise.all([this.fetchAssignments(id), this.fetchPatient(id), ...(this.staff ? [this.fetchStaff()] : [])])
      return body.data
    },

    /** Admin: end an assignment — that nurse loses access to the patient at once. */
    async endAssignment(id, assignmentId) {
      const body = await call('mockEndAssignment', [id, assignmentId], () => api.post(`/patients/${id}/nurse-assignments/${assignmentId}/end`))
      await Promise.all([this.fetchAssignments(id), this.fetchPatient(id), ...(this.staff ? [this.fetchStaff()] : [])])
      return body.data
    },

    async fetchStaff() {
      const body = await this._load('staff', () =>
        call('mockListStaff', [{}], () => api.get('/admin/users', { params: { role: 'nurse' } })))
      if (body) this.staff = body.data
    },

    /** Admin: nurse account → includes temporary_password (shown once). */
    async createNurse(payload) {
      const body = await call('mockCreateNurse', [payload], () => api.post('/admin/users', payload))
      await this.fetchStaff()
      return body.data
    },
  },
})
