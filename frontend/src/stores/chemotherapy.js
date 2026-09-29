import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useDashboardStore } from '@/stores/dashboard'

/**
 * Chemotherapy (api-design.md §5): plans, cycles, medication records, drugs and regimens.
 * Errors are { status, code, message, details }; what may be seen is decided by the backend.
 */
export const useChemotherapyStore = defineStore('chemotherapy', {
  state: () => ({
    plans: {}, // patient id → plans (with cycles)
    medications: {}, // patient id → medication records (staff: full history)
    drugs: null,
    regimens: null,
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
      const [plans, meds] = await Promise.all([
        this._load(`plans:${patientId}`, () => call('mockListPlans', [patientId], () => api.get('/chemotherapy/plans', { params: { patient_id: patientId } }))),
        this._load(`meds:${patientId}`, () => call('mockListPatientMedications', [patientId], () => api.get('/chemotherapy/medications', { params: { patient_id: patientId } }))),
      ])
      if (plans) this.plans[patientId] = plans.data
      if (meds) this.medications[patientId] = meds.data
    },

    async fetchDrugs() {
      const body = await this._load('drugs', () => call('mockListDrugs', [{}], () => api.get('/chemotherapy/drugs')))
      if (body) this.drugs = body.data
      return this.drugs
    },

    async fetchRegimens() {
      const body = await this._load('regimens', () => call('mockListRegimens', [{}], () => api.get('/chemotherapy/regimens')))
      if (body) this.regimens = body.data
      return this.regimens
    },

    /** After a write: this patient's plans / medications and the dashboard derived from them. */
    async _refresh(patientId) {
      const dashboard = useDashboardStore()
      await Promise.all([
        this.fetchPatient(patientId),
        ...(dashboard.patients[patientId] ? [dashboard.fetchPatientDashboard(patientId)] : []),
        ...(dashboard.nurseOverview ? [dashboard.fetchNurseOverview()] : []),
      ])
    },

    async createPlan(patientId, payload) {
      const body = { ...payload, patient_id: patientId }
      const res = await call('mockCreatePlan', [body], () => api.post('/chemotherapy/plans', body))
      await this._refresh(patientId)
      return res.data
    },

    async discontinuePlan(patientId, planId, reason) {
      const body = { discontinue_reason: reason }
      await call('mockDiscontinuePlan', [planId, body], () => api.post(`/chemotherapy/plans/${planId}/discontinue`, body))
      await this._refresh(patientId)
    },

    async addCycle(patientId, planId, payload) {
      await call('mockAddCycle', [planId, payload], () => api.post(`/chemotherapy/plans/${planId}/cycles`, payload))
      await this._refresh(patientId)
    },

    /** action: start | complete | delay */
    async cycleAction(patientId, cycleId, action, payload = {}) {
      const name = { start: 'mockStartCycle', complete: 'mockCompleteCycle', delay: 'mockDelayCycle' }[action]
      await call(name, [cycleId, payload], () => api.post(`/chemotherapy/cycles/${cycleId}/${action}`, payload))
      await this._refresh(patientId)
    },

    /** Keep `idempotencyKey` for retries of the same entry: a retry never adds a second record. */
    async recordMedication(patientId, cycleId, payload, idempotencyKey) {
      const res = await call('mockCreateMedication', [cycleId, payload, idempotencyKey], () =>
        api.post(`/chemotherapy/cycles/${cycleId}/medications`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }))
      await this._refresh(patientId)
      return res.data
    },

    async amendMedication(patientId, recordId, payload, idempotencyKey) {
      const res = await call('mockAmendMedication', [recordId, payload, idempotencyKey], () =>
        api.post(`/chemotherapy/medications/${recordId}/amend`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }))
      await this._refresh(patientId)
      return res.data
    },

    async markMedicationError(patientId, recordId, reason) {
      const body = { reason }
      await call('mockMarkMedicationError', [recordId, body], () => api.post(`/chemotherapy/medications/${recordId}/mark-error`, body))
      await this._refresh(patientId)
    },
  },
})
