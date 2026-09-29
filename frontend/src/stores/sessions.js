import { defineStore } from 'pinia'

import { api, call } from '@/api/call'

/**
 * The signed-in account's sessions (sign-ins) — Authentication Hardening. Ending a session makes
 * its access token stop working at once (checked by the backend on every request).
 */
export const useSessionsStore = defineStore('sessions', {
  state: () => ({ items: null, error: null }),

  actions: {
    async fetch() {
      this.error = null
      try {
        this.items = (await call('mockListSessions', [], () => api.get('/auth/sessions'))).data
      } catch (err) {
        this.error = err
      }
    },
    async end(id) {
      await call('mockEndSession', [id], () => api.delete(`/auth/sessions/${id}`))
      await this.fetch()
    },
    /** Sign out everywhere else → number of sessions ended. */
    async endOthers() {
      const n = (await call('mockEndOtherSessions', [], () => api.post('/auth/sessions/revoke-others'))).data.revoked
      await this.fetch()
      return n
    },
  },
})
