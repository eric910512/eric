import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useChemotherapyStore } from '@/stores/chemotherapy'
import { useDashboardStore } from '@/stores/dashboard'

/** Treatment schedule (api-design.md §5 appointments). Errors are { status, code, message, details }. */
export const useAppointmentsStore = defineStore('appointments', {
  state: () => ({
    byPatient: {}, // patient id → appointments (soonest first)
    loading: {},
    errors: {},
  }),

  actions: {
    async fetch(patientId) {
      this.loading[patientId] = true
      this.errors[patientId] = null
      try {
        const body = await call('mockListAppointments', [patientId], () => api.get('/chemotherapy/appointments', { params: { patient_id: patientId } }))
        this.byPatient[patientId] = body.data
      } catch (err) {
        this.errors[patientId] = err
      } finally {
        this.loading[patientId] = false
      }
    },

    /** After a write: the list, 今日行程 / the nurse overview, and the cycles (appointment links). */
    async _refresh(patientId) {
      const dashboard = useDashboardStore()
      const chemo = useChemotherapyStore()
      await Promise.all([
        this.fetch(patientId),
        ...(dashboard.patients[patientId] ? [dashboard.fetchPatientDashboard(patientId)] : []),
        ...(dashboard.nurseOverview ? [dashboard.fetchNurseOverview()] : []),
        ...(chemo.plans[patientId] ? [chemo.fetchPatient(patientId)] : []),
      ])
    },

    async create(patientId, payload) {
      const body = { ...payload, patient_id: patientId }
      const res = await call('mockCreateAppointment', [body], () => api.post('/chemotherapy/appointments', body))
      await this._refresh(patientId)
      return res.data
    },

    async update(patientId, id, changes) {
      await call('mockUpdateAppointment', [id, changes], () => api.patch(`/chemotherapy/appointments/${id}`, changes))
      await this._refresh(patientId)
    },

    /** action: check-in | complete | cancel ({ reason }) | reschedule ({ scheduled_at, reason }) */
    async act(patientId, id, action, payload = {}) {
      const name = { 'check-in': 'mockCheckInAppointment', complete: 'mockCompleteAppointment', cancel: 'mockCancelAppointment', reschedule: 'mockRescheduleAppointment' }[action]
      const res = await call(name, [id, payload], () => api.post(`/chemotherapy/appointments/${id}/${action}`, payload))
      await this._refresh(patientId)
      return res.data
    },
  },
})
