import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useDashboardStore } from '@/stores/dashboard'

/**
 * Patient pages (Sprint 6): own notifications (unread, read, read all), own symptom reports, profile.
 * Profile sprint: basic data the patient maintains (contact email + verification, email notifications,
 * height), weight history and BMI (`/patients/{id}/profile`, `/weights`; new weights via POST /vital-signs).
 */
export const usePatientPortalStore = defineStore('patientPortal', {
  state: () => ({
    notifications: null, // { items, meta }
    unread: 0,
    records: null, // own symptom reports
    profile: null,
    basic: null, // GET /patients/{id}/profile
    weights: null, // GET /patients/{id}/weights
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

    async fetchNotifications() {
      const body = await this._load('notifications', () => call('mockMyNotifications', [], () => api.get('/notifications', { params: { per_page: 50 } })))
      if (body) {
        this.notifications = { items: body.data, meta: body.meta }
        this.unread = body.meta.unread
      }
    },

    async fetchUnread() {
      const body = await this._load('unread', () => call('mockMyUnreadCount', [], () => api.get('/notifications/unread-count')))
      if (body) this.unread = body.data.unread
    },

    /** The home page badge comes from the dashboard: keep it in step. */
    _syncDashboard() {
      const dashboard = useDashboardStore()
      for (const d of Object.values(dashboard.patients)) {
        if (d?.widgets?.notifications) d.widgets.notifications.unread_count = this.unread
      }
    },

    async markRead(id) {
      const n = this.notifications?.items.find((x) => x.id === id)
      if (!n || n.is_read) return
      const body = await call('mockMyMarkRead', [id], () => api.patch(`/notifications/${id}/read`))
      Object.assign(n, body.data)
      this.unread = Math.max(0, this.unread - 1)
      this._syncDashboard()
    },

    async readAll() {
      await call('mockMyReadAll', [], () => api.post('/notifications/read-all'))
      await this.fetchNotifications()
      this._syncDashboard()
    },

    async fetchRecords(patientId) {
      const body = await this._load('records', () =>
        call('mockListRecords', [patientId, { reviewStatus: 'all' }], () => api.get(`/symptoms/records/${patientId}`, { params: { review_status: 'all', per_page: 30 } })))
      if (body) this.records = body.data
    },

    async fetchProfile(patientId) {
      const body = await this._load('profile', () => call('mockGetPatient', [patientId], () => api.get(`/patients/${patientId}`)))
      if (body) this.profile = body.data
    },

    async fetchBasic(patientId) {
      const body = await this._load('basic', () => call('mockGetProfile', [patientId], () => api.get(`/patients/${patientId}/profile`)))
      if (body) this.basic = body.data
    },

    async fetchWeights(patientId) {
      const body = await this._load('weights', () =>
        call('mockPatientWeights', [patientId, { limit: 20 }], () => api.get(`/patients/${patientId}/weights`, { params: { limit: 20 } })))
      if (body) this.weights = body.data
    },

    /** { email?, height_cm?, email_notification_enabled? } → the updated profile (throws { status, code, details }). */
    async saveBasic(patientId, changes) {
      const body = await call('mockUpdateProfile', [patientId, changes], () => api.patch(`/patients/${patientId}/profile`, changes))
      this.basic = body.data
      return body.data
    },

    /** Send the verification link → { status: sent | failed, error_code }. */
    async requestVerification(patientId) {
      const body = await call('mockRequestEmailVerification', [patientId], () => api.post(`/patients/${patientId}/email-verification`))
      this.basic = body.data.profile
      return body.data.delivery
    },

    async confirmVerification(patientId, token) {
      const body = await call('mockConfirmEmailVerification', [patientId, { token }], () =>
        api.post(`/patients/${patientId}/email-verification/confirm`, { token }))
      this.basic = body.data
      return body.data
    },
  },
})
