import { defineStore } from 'pinia'

import { api, call } from '@/api/call'

/**
 * Admin console (api-design.md §13): overview, accounts and their status, audit log search,
 * read-only settings, alert rules and symptom form composition. Every change is audited by
 * the backend; admins do not edit clinical records here.
 */
export const useAdminStore = defineStore('admin', {
  state: () => ({
    overview: null,
    accounts: null, // { items, params }
    audit: null, // { items, meta, params }
    settings: null,
    rules: null,
    forms: null,
    loading: {},
    errors: {}, // key → { status, code, message, details }
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

    async fetchOverview() {
      const body = await this._load('overview', () => call('mockAdminOverview', [], () => api.get('/admin/overview')))
      if (body) this.overview = body.data
    },

    /** params: { role: all|nurse|patient|admin, status: any|active|inactive|locked, q } */
    async fetchAccounts(params = this.accounts?.params ?? {}) {
      const { role = 'all', status = 'any', q = '' } = params
      const query = { role, status, ...(q ? { q } : {}) }
      const body = await this._load('accounts', () =>
        call('mockListStaff', [{ role, status, q: q || null }], () => api.get('/admin/users', { params: query })))
      if (body) this.accounts = { items: body.data, params: { role, status, q } }
    },

    /** { is_active } | { unlock: true } | { display_name } | { nurse_profile } → the updated account. */
    async updateAccount(id, patch) {
      const body = await call('mockUpdateAccount', [id, patch], () => api.patch(`/admin/users/${id}`, patch))
      const i = this.accounts?.items.findIndex((a) => a.id === id) ?? -1
      if (i >= 0) this.accounts.items[i] = body.data
      return body.data
    },

    /** Force sign-out everywhere → number of sessions ended. */
    async revokeSessions(id) {
      const n = (await call('mockRevokeSessions', [id], () => api.post(`/admin/users/${id}/revoke-sessions`))).data.revoked
      await this.fetchAccounts()
      return n
    },

    /** New temporary password (shown once; never stored here) → { ...account, temporary_password, sessions_revoked }. */
    async resetPassword(id) {
      const body = await call('mockResetPassword', [id], () => api.post(`/admin/users/${id}/password-reset`))
      const { temporary_password: _pw, ...acct } = body.data // eslint-disable-line no-unused-vars -- the store never keeps it
      const i = this.accounts?.items.findIndex((a) => a.id === id) ?? -1
      if (i >= 0) this.accounts.items[i] = { ...this.accounts.items[i], ...acct, active_sessions: 0 }
      return body.data
    },

    /** Admin account (nurse accounts: patients store createNurse) → includes temporary_password, shown once. */
    async createAdmin(payload) {
      const body = await call('mockCreateNurse', [{ ...payload, role: 'admin' }], () => api.post('/admin/users', { ...payload, role: 'admin' }))
      await this.fetchAccounts()
      return body.data
    },

    /** filters: { actor_id, action, resource_type, category, outcome, from, to, page } */
    async searchAudit(filters = {}) {
      const params = Object.fromEntries(Object.entries({ per_page: 50, ...filters }).filter(([, v]) => v !== '' && v != null))
      const body = await this._load('audit', () => call('mockSearchAudit', [params], () => api.get('/admin/audit-logs', { params })))
      if (body) this.audit = { items: body.data, meta: body.meta, params }
    },

    async fetchSettings() {
      const body = await this._load('settings', () => call('mockAdminSettings', [], () => api.get('/admin/settings')))
      if (body) this.settings = body.data
    },

    async fetchRules() {
      const body = await this._load('rules', () => call('mockListRules', [], () => api.get('/notifications/alert-rules')))
      if (body) this.rules = body.data
    },

    async updateRule(id, patch) {
      const body = await call('mockUpdateRule', [id, patch], () => api.patch(`/notifications/alert-rules/${id}`, patch))
      const i = this.rules?.findIndex((r) => r.id === id) ?? -1
      if (i >= 0) this.rules[i] = body.data
      return body.data
    },

    /** Dry run { value, in_nadir? } → { matches, comparison, conditions_met, … }; nothing is sent. */
    async testRule(id, sample) {
      return (await call('mockTestRule', [id, sample], () => api.post(`/notifications/alert-rules/${id}/test`, sample))).data
    },

    async fetchForms() {
      const body = await this._load('forms', () => call('mockListForms', [], () => api.get('/symptoms/forms')))
      if (body) this.forms = body.data
    },

    /** { name?, items?: [{ definition_code, is_required }] } in display order → version +1 when changed. */
    async updateForm(code, payload) {
      const body = await call('mockUpdateForm', [code, payload], () => api.put(`/symptoms/forms/${code}`, payload))
      const i = this.forms?.findIndex((f) => f.code === code) ?? -1
      if (i >= 0) this.forms[i] = body.data
      return body.data
    },
  },
})
