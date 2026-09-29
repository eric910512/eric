import { defineStore } from 'pinia'

import { api, apiErrorMessage, loadMock, USE_MOCK } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const PAGE_SIZE = 20
const delay = (ms = 250) => new Promise((r) => setTimeout(r, ms))

/** Patient Care Timeline pages (GET /api/v1/patients/{id}/timeline), per patient. */
export const useTimelineStore = defineStore('timeline', {
  state: () => ({
    byPatient: {}, // patientId → { items, meta, filters, loading, loadingMore, error }
  }),

  actions: {
    async _fetch(patientId, filters, cursor) {
      const params = {
        limit: PAGE_SIZE,
        ...(filters.startDate ? { start_date: filters.startDate } : {}),
        ...(filters.endDate ? { end_date: filters.endDate } : {}),
        ...(cursor ? { cursor } : {}),
      }
      if (USE_MOCK) {
        await delay()
        const audience = useAuthStore().role === 'patient' ? 'patient' : 'staff'
        return (await loadMock()).mockTimeline(patientId, { ...filters, limit: PAGE_SIZE, cursor, audience })
      }
      const res = await api.get(`/patients/${patientId}/timeline`, { params })
      return res.data
    },

    /** First page (resets). filters: { startDate, endDate } as local YYYY-MM-DD. */
    async load(patientId, filters = {}) {
      const entry = { items: [], meta: null, filters, loading: true, loadingMore: false, error: null }
      this.byPatient[patientId] = entry
      const mine = this.byPatient[patientId] // the stored (reactive) entry
      const current = () => this.byPatient[patientId] === mine // false after a newer load or sign-out ($reset)
      try {
        const res = await this._fetch(patientId, filters, null)
        if (current()) Object.assign(mine, { items: res.data, meta: res.meta })
      } catch (err) {
        if (current()) mine.error = err.response ? apiErrorMessage(err) : err.message
      } finally {
        if (current()) mine.loading = false
      }
    },

    async loadMore(patientId) {
      const entry = this.byPatient[patientId]
      if (!entry?.meta?.has_more || entry.loadingMore) return
      entry.loadingMore = true
      entry.error = null
      try {
        const res = await this._fetch(patientId, entry.filters, entry.meta.next_cursor)
        entry.items.push(...res.data)
        entry.meta = res.meta
      } catch (err) {
        entry.error = err.response ? apiErrorMessage(err) : err.message
      } finally {
        entry.loadingMore = false
      }
    },
  },
})
