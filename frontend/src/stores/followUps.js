import { defineStore } from 'pinia'

import { api, call } from '@/api/call'

/**
 * 護理師追蹤管理 (Phase 1c): nurse lists / detail / history and the write actions (create, reschedule,
 * complete, cancel — Idempotency-Key per attempt, the version the nurse saw); the patient's read-only list.
 * Errors are thrown as { status, code, message, details } (VERSION_CONFLICT / FOLLOW_UP_CLOSED → reload).
 */
export const useFollowUpStore = defineStore('followUps', {
  state: () => ({
    list: null, // { items, meta } of the current query
    listError: null,
    query: { status: 'open', patientId: null, sort: 'due_at', page: 1, perPage: 20 },
    detail: {}, // id → follow-up (with completion)
    events: {}, // id → history
    errors: {}, // id → error
    mine: null, // patient: { items, notice }
    mineError: null,
  }),

  actions: {
    async fetchList(changes = {}) {
      this.query = { ...this.query, ...changes }
      const q = this.query
      this.listError = null
      try {
        const params = { status: q.status, sort: q.sort, page: q.page, per_page: q.perPage, ...(q.patientId ? { patient_id: q.patientId } : {}) }
        const body = await call('mockListFollowUps', [{ status: q.status, patientId: q.patientId, sort: q.sort, page: q.page, perPage: q.perPage }],
          () => api.get('/care-follow-ups', { params }))
        this.list = { items: body.data, meta: body.meta }
      } catch (err) {
        this.listError = err
      }
    },

    async fetchDetail(id) {
      this.errors[id] = null
      try {
        const [d, e] = await Promise.all([
          call('mockFollowUp', [id], () => api.get(`/care-follow-ups/${id}`)),
          call('mockFollowUpEvents', [id], () => api.get(`/care-follow-ups/${id}/events`)),
        ])
        this.detail[id] = d.data
        this.events[id] = e.data
      } catch (err) {
        this.errors[id] = err
      }
    },

    async create(body, key) {
      return (await call('mockCreateFollowUp', [body, key], () => api.post('/care-follow-ups', body, { headers: { 'Idempotency-Key': key } }))).data
    },

    /** action: reschedule | complete | cancel */
    async act(id, action, body, key) {
      const mockName = { reschedule: 'mockRescheduleFollowUp', complete: 'mockCompleteFollowUp', cancel: 'mockCancelFollowUp' }[action]
      const request = action === 'reschedule'
        ? () => api.patch(`/care-follow-ups/${id}/reschedule`, body, { headers: { 'Idempotency-Key': key } })
        : () => api.post(`/care-follow-ups/${id}/${action}`, body, { headers: { 'Idempotency-Key': key } })
      const data = (await call(mockName, [id, body, key], request)).data
      this.detail[id] = data
      return data
    },

    async fetchMine() {
      this.mineError = null
      try {
        const body = await call('mockMyFollowUps', [], () => api.get('/patients/me/care-follow-ups'))
        this.mine = { items: body.data, notice: body.meta.notice }
      } catch (err) {
        this.mineError = err
      }
    },
  },
})
