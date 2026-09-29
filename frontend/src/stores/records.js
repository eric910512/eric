import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useNurseStore } from '@/stores/nurse'
import { useTimelineStore } from '@/stores/timeline'

/** API path and mock handler names per kind of observation. */
const KIND = {
  symptom: { path: (id) => `/symptoms/records/${id}`, amend: 'mockAmendSymptom', error: 'mockMarkSymptomError', history: 'mockSymptomHistory' },
  vital: { path: (id) => `/vital-signs/${id}`, amend: 'mockAmendVital', error: 'mockMarkVitalError', history: 'mockVitalHistory' },
  lab: { path: (id) => `/labs/results/${id}`, amend: 'mockAmendLab', error: 'mockMarkLabError', history: 'mockLabRecordHistory' },
}

/**
 * Nurse: a patient's recent observations to correct (Sprint 5), their correction history, and
 * the cross-patient review queues. Errors are { status, code, message, details }.
 */
export const useRecordsStore = defineStore('records', {
  state: () => ({
    byPatient: {}, // patient id → { symptom: [], vital: [], lab: [] }
    history: {}, // `${kind}:${id}` → chain
    queues: null, // { symptoms, vitals, labs, notifications }
    loading: {},
    errors: {},
  }),

  actions: {
    async fetchPatient(patientId) {
      this.loading[patientId] = true
      this.errors[patientId] = null
      try {
        const [s, v, l] = await Promise.all([
          call('mockListRecords', [patientId, { reviewStatus: 'all' }], () => api.get(`/symptoms/records/${patientId}`, { params: { review_status: 'all', per_page: 50 } })),
          call('mockListPatientVitals', [patientId], () => api.get(`/vital-signs/patient/${patientId}`)),
          call('mockLabHistory', [patientId, { days: 90 }], () => api.get(`/labs/results/${patientId}`, { params: { days: 90 } })),
        ])
        this.byPatient[patientId] = { symptom: s.data, vital: v.data, lab: l.data.results }
      } catch (err) {
        this.errors[patientId] = err
      } finally {
        this.loading[patientId] = false
      }
    },

    async fetchHistory(kind, id) {
      const k = KIND[kind]
      const body = await call(k.history, [id], () => api.get(`${k.path(id)}/history`))
      this.history[`${kind}:${id}`] = body.data
    },

    /** After a correction: this list, the queues, the timeline, and (via the nurse store) dashboards and alert lists. */
    async _refresh(patientId) {
      const timeline = useTimelineStore()
      const nurse = useNurseStore()
      await Promise.all([
        this.fetchPatient(patientId),
        ...(this.queues ? [this.fetchQueues()] : []),
        ...(timeline.byPatient[patientId] ? [timeline.load(patientId, timeline.byPatient[patientId].filters)] : []),
        nurse.refreshAfterChange(patientId),
      ])
    },

    /** Keep `idempotencyKey` for retries of the same correction. */
    async amend(patientId, kind, id, payload, idempotencyKey) {
      const k = KIND[kind]
      const res = await call(k.amend, [id, payload, idempotencyKey], () =>
        api.post(`${k.path(id)}/amend`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }))
      await this._refresh(patientId)
      return res.data
    },

    async markError(patientId, kind, id, reason) {
      const k = KIND[kind]
      const res = await call(k.error, [id, { reason }], () => api.post(`${k.path(id)}/mark-error`, { reason }))
      await this._refresh(patientId)
      return res.data
    },

    /** 待審清單: pending symptom reviews, abnormal vitals / labs, open alerts of current patients. */
    async fetchQueues() {
      this.loading.queues = true
      this.errors.queues = null
      try {
        const [s, v, l, n] = await Promise.all([
          call('mockPendingReviews', [], () => api.get('/dashboard/widgets/pending-symptom-reviews/data')),
          call('mockListAbnormal', [{ hours: 72 }], () => api.get('/vital-signs/abnormal', { params: { hours: 72 } })),
          call('mockAbnormalLabs', [{ hours: 168 }], () => api.get('/labs/abnormal', { params: { hours: 168 } })),
          call('mockListNotifications', [{ status: 'pending' }], () => api.get('/notifications', { params: { status: 'pending', per_page: 100 } })),
        ])
        this.queues = { symptoms: s.data, vitals: v.data, labs: l.data, notifications: n.data }
      } catch (err) {
        this.errors.queues = err
      } finally {
        this.loading.queues = false
      }
    },
  },
})
