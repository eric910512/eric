import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useDashboardStore } from '@/stores/dashboard'
import { useTimelineStore } from '@/stores/timeline'

/** Nursing assessments (api-design.md §9). Errors are { status, code, message, details }. */
export const useNursingStore = defineStore('nursing', {
  state: () => ({
    byPatient: {}, // patient id → assessments (summary rows, newest first)
    mine: null, // the nurse's own assessments of current patients
    details: {}, // id → full assessment
    versions: {}, // id → version chain
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
        this.errors[key] = err
        return null
      } finally {
        this.loading[key] = false
      }
    },

    async fetchPatient(patientId) {
      const body = await this._load(`patient:${patientId}`, () =>
        call('mockListAssessments', [patientId, {}], () => api.get('/nursing-assessments', { params: { patient_id: patientId } })))
      if (body) this.byPatient[patientId] = body.data
    },

    async fetchMine() {
      const body = await this._load('mine', () => call('mockListAssessments', [null, {}], () => api.get('/nursing-assessments')))
      if (body) this.mine = body.data
    },

    async fetchDetail(id) {
      const body = await this._load(`detail:${id}`, () => call('mockGetAssessment', [id], () => api.get(`/nursing-assessments/${id}`)))
      if (body) this.details[id] = body.data
      return body?.data ?? null
    },

    async fetchVersions(id) {
      const body = await this._load(`versions:${id}`, () => call('mockAssessmentVersions', [id], () => api.get(`/nursing-assessments/${id}/versions`)))
      if (body) this.versions[id] = body.data
    },

    /** After a write: lists, the dashboard (nurse view / risk) and the timeline of the patient. */
    async _refresh(patientId) {
      const dashboard = useDashboardStore()
      const timeline = useTimelineStore()
      await Promise.all([
        this.fetchPatient(patientId),
        ...(this.mine ? [this.fetchMine()] : []),
        ...(dashboard.patients[patientId] ? [dashboard.fetchPatientDashboard(patientId)] : []),
        ...(timeline.byPatient[patientId] ? [timeline.load(patientId, timeline.byPatient[patientId].filters)] : []),
      ])
    },

    /** Keep `idempotencyKey` for retries of the same submission. */
    async create(patientId, payload, idempotencyKey) {
      const body = { ...payload, patient_id: patientId }
      const res = await call('mockCreateAssessment', [body, idempotencyKey], () =>
        api.post('/nursing-assessments', body, { headers: { 'Idempotency-Key': idempotencyKey } }))
      this.details[res.data.id] = res.data
      await this._refresh(patientId)
      return res.data
    },

    async update(patientId, id, changes) {
      const res = await call('mockUpdateAssessment', [id, changes], () => api.patch(`/nursing-assessments/${id}`, changes))
      this.details[id] = res.data
      await this._refresh(patientId)
      return res.data
    },

    async sign(patientId, id) {
      const res = await call('mockSignAssessment', [id], () => api.post(`/nursing-assessments/${id}/sign`, {}))
      this.details[id] = res.data
      await this._refresh(patientId)
      return res.data
    },

    async amend(patientId, id, payload, idempotencyKey) {
      const res = await call('mockAmendAssessment', [id, payload, idempotencyKey], () =>
        api.post(`/nursing-assessments/${id}/amend`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }))
      this.details[res.data.id] = res.data
      await this._refresh(patientId)
      return res.data
    },

    async updateItem(patientId, id, itemId, status) {
      await call('mockUpdateAssessmentItem', [id, itemId, { item_status: status }], () =>
        api.patch(`/nursing-assessments/${id}/items/${itemId}`, { item_status: status }))
      await this.fetchDetail(id)
    },
  },
})
