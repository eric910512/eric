import { defineStore } from 'pinia'

import { api, apiErrorMessage, loadMock, USE_MOCK } from '@/api/client'
import { useDashboardStore } from '@/stores/dashboard'

const delay = (ms = 250) => new Promise((r) => setTimeout(r, ms))

function toError(err) {
  if (!err.response) return { code: err.code ?? 'NETWORK', message: err.message || '無法連線到伺服器，請稍後再試' }
  const error = err.response.data?.error
  return { code: error?.code ?? 'UNKNOWN', message: apiErrorMessage(err), details: error?.details ?? [] }
}

/** Nurse review workflow: notifications (lifecycle / read / resolve), symptom review, labs. */
export const useNurseStore = defineStore('nurse', {
  state: () => ({
    notifications: {}, // "status:patientId|all" → { items, meta }
    records: {}, // "patientId:reviewStatus" → { items, meta }
    abnormalVitals: null, // { items, meta, hours }
    labs: {}, // patientId → { test_types, latest, series, results, meta }
    center: null, // Notification Center list: { items, meta, params }
    details: {}, // notification id → detail
    loading: {},
    errors: {},
  }),

  actions: {
    async fetchNotifications({ status = 'unresolved', patientId = null } = {}) {
      const key = `${status}:${patientId ?? 'all'}`
      this.loading[key] = true
      this.errors[key] = null
      try {
        if (USE_MOCK) {
          await delay()
          const res = (await loadMock()).mockListNotifications({ status, patientId })
          this.notifications[key] = { items: res.data, meta: res.meta }
        } else {
          const params = { status, per_page: 50, ...(patientId ? { patient_id: patientId } : {}) }
          const res = await api.get('/notifications', { params })
          this.notifications[key] = { items: res.data.data, meta: res.data.meta }
        }
      } catch (err) {
        this.errors[key] = toError(err).message
      } finally {
        this.loading[key] = false
      }
    },

    async fetchRecords(patientId, reviewStatus = 'submitted') {
      const key = `${patientId}:${reviewStatus}`
      this.loading[key] = true
      this.errors[key] = null
      try {
        if (USE_MOCK) {
          await delay()
          const res = (await loadMock()).mockListRecords(patientId, { reviewStatus })
          this.records[key] = { items: res.data, meta: res.meta }
        } else {
          const res = await api.get(`/symptoms/records/${patientId}`, { params: { review_status: reviewStatus, per_page: 50 } })
          this.records[key] = { items: res.data.data, meta: res.data.meta }
        }
      } catch (err) {
        this.errors[key] = toError(err).message
      } finally {
        this.loading[key] = false
      }
    },

    async fetchAbnormalVitals(hours = 72) {
      this.loading.abnormal = true
      this.errors.abnormal = null
      try {
        if (USE_MOCK) {
          await delay()
          const res = (await loadMock()).mockListAbnormal({ hours })
          this.abnormalVitals = { items: res.data, meta: res.meta, hours }
        } else {
          const res = await api.get('/vital-signs/abnormal', { params: { hours } })
          this.abnormalVitals = { items: res.data.data, meta: res.data.meta, hours }
        }
      } catch (err) {
        this.errors.abnormal = toError(err).message
      } finally {
        this.loading.abnormal = false
      }
    },

    async fetchLabHistory(patientId, days = 90) {
      const key = `labs:${patientId}`
      this.loading[key] = true
      this.errors[key] = null
      try {
        if (USE_MOCK) {
          await delay()
          const res = (await loadMock()).mockLabHistory(patientId, { days })
          this.labs[patientId] = { ...res.data, meta: res.meta }
        } else {
          const res = await api.get(`/labs/results/${patientId}`, { params: { days } })
          this.labs[patientId] = { ...res.data.data, meta: res.data.meta }
        }
      } catch (err) {
        this.errors[key] = toError(err).message
      } finally {
        this.loading[key] = false
      }
    },

    /**
     * Record a lab panel (nurse). Keep `idempotencyKey` for retries of the same entry.
     * Throws { code, message, details, retryable }.
     */
    async submitLabResults(patientId, payload, idempotencyKey) {
      let result
      try {
        if (USE_MOCK) {
          await delay(400)
          result = (await loadMock()).submitMockLabs(patientId, payload)
        } else {
          result = (await api.post('/labs/results', { ...payload, patient_id: patientId }, {
            headers: { 'Idempotency-Key': idempotencyKey },
          })).data.data
        }
      } catch (err) {
        const e = toError(err)
        throw { ...e, retryable: !err.response || err.response.status >= 500 || e.code === 'IDEMPOTENCY_IN_PROGRESS' }
      }
      if (!(patientId in this.labs)) this.labs[patientId] = null // so refreshAfterChange re-fetches it
      await this.refreshAfterChange(patientId)
      return result
    },

    /** Notification Center list. status: pending | in_progress | resolved | …; priority: critical | warning */
    async fetchCenter(params = this.center?.params ?? { status: 'pending' }) {
      this.loading.center = true
      this.errors.center = null
      try {
        const { status, priority = null, patientId = null } = params
        if (USE_MOCK) {
          await delay()
          const res = (await loadMock()).mockListNotifications({ status, priority, patientId })
          this.center = { items: res.data, meta: res.meta, params }
        } else {
          const query = { status, per_page: 100, ...(priority ? { priority } : {}), ...(patientId ? { patient_id: patientId } : {}) }
          const res = await api.get('/notifications', { params: query })
          this.center = { items: res.data.data, meta: res.data.meta, params }
        }
      } catch (err) {
        this.errors.center = toError(err).message
      } finally {
        this.loading.center = false
      }
    },

    async fetchNotificationDetail(id) {
      const key = `detail:${id}`
      this.loading[key] = true
      this.errors[key] = null
      try {
        this.details[id] = USE_MOCK
          ? (await delay(), (await loadMock()).mockGetNotification(id))
          : (await api.get(`/notifications/${id}`)).data.data
      } catch (err) {
        this.errors[key] = toError(err).message
      } finally {
        this.loading[key] = false
      }
    },

    /** Lifecycle step: acknowledge (接手) → start (處理中) → resolve (完成, note required). */
    async transition(id, action, resolutionNote = null) {
      let detail
      try {
        if (USE_MOCK) {
          await delay()
          detail = (await loadMock()).mockTransition(id, action, resolutionNote)
        } else {
          const body = action === 'resolve' ? { resolution_note: resolutionNote } : {}
          detail = (await api.post(`/notifications/${id}/${action}`, body)).data.data
        }
      } catch (err) {
        throw err.response ? toError(err) : { code: err.code ?? 'UNKNOWN', message: err.message, details: err.details ?? [] }
      }
      this.details[id] = detail
      await this.refreshAfterChange(detail.patient?.id ?? null)
      return detail
    },

    async markRead(notificationId) {
      try {
        if (USE_MOCK) (await loadMock()).mockMarkRead(notificationId)
        else await api.patch(`/notifications/${notificationId}/read`)
      } catch (err) {
        throw toError(err)
      }
      await this.refreshAfterChange()
    },

    async resolve(notificationId, resolutionNote) {
      try {
        if (USE_MOCK) {
          await delay()
          const { mockResolve } = await loadMock()
          mockResolve(notificationId, resolutionNote)
        } else {
          await api.patch(`/notifications/${notificationId}/resolve`, { resolution_note: resolutionNote })
        }
      } catch (err) {
        throw toError(err)
      }
      await this.refreshAfterChange()
    },

    async review(recordId, payload, patientId) {
      let result
      try {
        if (USE_MOCK) {
          await delay(400)
          result = (await loadMock()).mockReview(recordId, payload)
        } else {
          result = (await api.post(`/symptoms/records/${recordId}/review`, payload)).data.data
        }
      } catch (err) {
        throw toError(err)
      }
      await this.refreshAfterChange(patientId)
      return result
    },

    /** Re-fetch every list currently shown, plus the dashboards whose counts changed. */
    async refreshAfterChange(patientId = null) {
      const dashboard = useDashboardStore()
      const jobs = [
        ...Object.keys(this.notifications).map((key) => {
          const [status, pid] = key.split(':')
          return this.fetchNotifications({ status, patientId: pid === 'all' ? null : pid })
        }),
        ...Object.keys(this.records).map((key) => {
          const [pid, status] = key.split(':')
          return this.fetchRecords(pid, status)
        }),
        dashboard.fetchNurseOverview(),
        ...(this.abnormalVitals ? [this.fetchAbnormalVitals(this.abnormalVitals.hours)] : []),
        ...Object.keys(this.labs).map((pid) => this.fetchLabHistory(pid)),
        ...(this.center ? [this.fetchCenter()] : []),
      ]
      const ids = new Set([patientId, ...Object.keys(dashboard.patients)].filter(Boolean))
      for (const id of ids) jobs.push(dashboard.fetchPatientDashboard(id))
      await Promise.all(jobs)
    },
  },
})
