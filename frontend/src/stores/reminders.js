import { defineStore } from 'pinia'

import { api, call } from '@/api/call'

/**
 * Manual / scheduled reminders of one patient (api-design.md §10, Sprint 8): sent reminders
 * (`GET /notifications?type=reminder`), reminders not due yet (`GET /notifications/scheduled`),
 * create (`POST /notifications`) and the handling lifecycle shared with risk alerts
 * (acknowledge → start → resolve). The backend decides who may do what (404 = not your patient).
 */
export const useRemindersStore = defineStore('reminders', {
  state: () => ({
    sent: {}, // patient id → reminders already delivered (staff view, with status)
    scheduled: {}, // patient id → reminders not yet due
    errors: {}, // patient id → { status, code, message }
  }),

  actions: {
    async fetch(patientId) {
      this.errors[patientId] = null
      try {
        const [sent, scheduled] = await Promise.all([
          call('mockListReminders', [patientId, { status: 'all' }], () =>
            api.get('/notifications', { params: { type: 'reminder', patient_id: patientId, per_page: 50 } })),
          call('mockScheduledReminders', [patientId], () => api.get('/notifications/scheduled', { params: { patient_id: patientId } })),
        ])
        this.sent[patientId] = sent.data
        this.scheduled[patientId] = scheduled.data
      } catch (err) {
        this.errors[patientId] = err
      }
    },

    /** { title, message, severity, scheduled_for? } → the reminder (key: Idempotency-Key, kept until a response arrives). */
    async create(patientId, payload, key) {
      const body = { patient_id: patientId, ...payload }
      const res = await call('mockCreateReminder', [body, key], () => api.post('/notifications', body, { headers: { 'Idempotency-Key': key } }))
      await this.fetch(patientId)
      return res.data
    },

    /** acknowledge (接手) → start (開始處理) → resolve (完成, internal note required). */
    async transition(patientId, id, action, note = null) {
      const body = action === 'resolve' ? { resolution_note: note } : {}
      const res = await call('mockTransition', [id, action, note], () => api.post(`/notifications/${id}/${action}`, body))
      await this.fetch(patientId)
      return res.data ?? res // the mock lifecycle returns the detail itself
    },
  },
})
