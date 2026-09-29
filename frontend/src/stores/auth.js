import { defineStore } from 'pinia'

import { api, loadMock, toApiError, USE_MOCK } from '@/api/client'
import { useAdminStore } from '@/stores/admin'
import { useAppointmentsStore } from '@/stores/appointments'
import { useChemotherapyStore } from '@/stores/chemotherapy'
import { useDashboardStore } from '@/stores/dashboard'
import { useNursingStore } from '@/stores/nursing'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { useRecordsStore } from '@/stores/records'
import { useNurseStore } from '@/stores/nurse'
import { usePatientsStore } from '@/stores/patients'
import { useRemindersStore } from '@/stores/reminders'
import { useSessionsStore } from '@/stores/sessions'
import { useTimelineStore } from '@/stores/timeline'

const STORAGE_KEY = 'ccp.auth'
const MOCK_TOKEN_SECONDS = 15 * 60
const REFRESH_AHEAD_MS = 60 * 1000 // renew the access token a minute before it expires
const XHR = { 'X-Requested-With': 'XMLHttpRequest' } // required by POST /auth/refresh (CSRF defence)

// The short-lived access token, the user and its expiry live in sessionStorage (this tab only,
// survives a reload). The refresh token is NEVER here: it is an HttpOnly cookie that page
// JavaScript cannot read (api-design.md §1.5); mock mode keeps its mock session in the mock state.
let refreshTimer = null
function readSession() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY)) ?? null
  } catch {
    return null
  }
}
function writeSession(value) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value))
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // storage unavailable (private mode etc.): session lives in memory only
  }
}

export class LoginError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

const HOME_BY_ROLE = {
  patient: { name: 'patient' },
  nurse: { name: 'nurse' },
  admin: { name: 'admin-home' },
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const saved = readSession()
    return {
      token: saved?.token ?? null,
      user: saved?.user ?? null,
      expiresAt: saved?.expiresAt ?? 0,
    }
  },

  getters: {
    isAuthenticated: (s) => !!s.token && Date.now() < s.expiresAt,
    /** Signed in on this tab, access token possibly expired (a refresh may renew it). */
    hasSession: (s) => !!s.token && !!s.user,
    role: (s) => s.user?.role ?? null,
    homeRoute: (s) => (s.user?.must_change_password ? { name: 'change-password' } : HOME_BY_ROLE[s.user?.role] ?? { name: 'login' }),
    /** First sign-in with a temporary password: every page except 設定新密碼 is locked. */
    mustChangePassword: (s) => !!s.user?.must_change_password,
  },

  actions: {
    async login(email, password) {
      const payload = USE_MOCK ? await mockLogin(email, password) : await apiLogin(email, password)
      this._accept(payload)
      return this.user
    },

    /**
     * New access token for this session: POST /auth/refresh (the browser sends the HttpOnly
     * refresh cookie; it is rotated), mock mode: the mock session. → true, or false when the
     * session is over (signed out elsewhere, revoked, password changed, expired).
     */
    async refresh() {
      if (!this.token) return false
      try {
        this._accept(USE_MOCK ? await mockRefresh(this.token) : (await api.post('/auth/refresh', null, { headers: XHR })).data.data)
        if (USE_MOCK) (await loadMock()).setMockViewer(this.user, this.token) // before the caller retries
        return true
      } catch {
        return false
      }
    },

    _accept(payload) {
      this.token = payload.access_token
      this.user = payload.user
      this.expiresAt = Date.now() + payload.expires_in * 1000
      this._save()
    },

    /** Keep the access token fresh while the tab is open (401 TOKEN_EXPIRED also refreshes). */
    scheduleRefresh() {
      clearTimeout(refreshTimer)
      if (!this.token) return
      const left = this.expiresAt - Date.now()
      // a minute ahead, or halfway for short-lived tokens; an already-expired token waits for the
      // next request (401 TOKEN_EXPIRED → refresh) instead of refreshing in a loop
      if (left > 0) refreshTimer = setTimeout(() => this.refresh(), Math.max(1000, left - Math.min(REFRESH_AHEAD_MS, left / 2)))
    },

    _save() {
      writeSession({ token: this.token, user: this.user, expiresAt: this.expiresAt })
      if (USE_MOCK) loadMock().then((m) => m.setMockViewer(this.user, this.token))
      this.scheduleRefresh()
    },

    /**
     * PUT /auth/password. Clears must_change_password on success (the same token keeps working).
     * Throws { status, code, message, details }.
     */
    async changePassword(currentPassword, newPassword) {
      try {
        if (USE_MOCK) {
          await new Promise((r) => setTimeout(r, 300))
          const { mockChangePassword } = await loadMock()
          mockChangePassword(currentPassword, newPassword)
        } else {
          await api.put('/auth/password', { current_password: currentPassword, new_password: newPassword })
        }
      } catch (err) {
        throw toApiError(err)
      }
      this.user = { ...this.user, must_change_password: false }
      this._save()
    },

    /** Mark the session as needing a password change (the API answered 403 PASSWORD_CHANGE_REQUIRED). */
    requirePasswordChange() {
      if (!this.user || this.user.must_change_password) return
      this.user = { ...this.user, must_change_password: true }
      this._save()
    },

    /**
     * Sign out. `server: true` (the 登出 buttons) also ends the session on the server, so this
     * access token stops working at once (POST /auth/logout, best effort); after a 401 there is
     * no session left to end.
     */
    logout({ server = true } = {}) {
      const token = this.token
      if (server && token) {
        if (USE_MOCK) loadMock().then((m) => m.mockLogout(token)).catch(() => {})
        else api.post('/auth/logout', null, { headers: { Authorization: `Bearer ${token}` } }).catch(() => {})
      }
      clearTimeout(refreshTimer)
      this.token = null
      this.user = null
      this.expiresAt = 0
      writeSession(null)
      if (USE_MOCK) loadMock().then((m) => m.setMockViewer(null, null))
      // Never let one user's patient data remain in memory for the next sign-in.
      useDashboardStore().$reset()
      useNurseStore().$reset()
      usePatientsStore().$reset()
      useChemotherapyStore().$reset()
      useAppointmentsStore().$reset()
      useNursingStore().$reset()
      useRecordsStore().$reset()
      usePatientPortalStore().$reset()
      useTimelineStore().$reset()
      useAdminStore().$reset()
      useRemindersStore().$reset()
      useSessionsStore().$reset()
    },
  },
})

async function apiLogin(email, password) {
  try {
    const res = await api.post('/auth/login', { email, password })
    return res.data.data
  } catch (err) {
    const error = err.response?.data?.error
    if (!err.response) throw new LoginError('NETWORK', '無法連線到伺服器，請確認後端已啟動')
    throw new LoginError(error?.code ?? 'UNKNOWN', error?.message ?? '登入失敗，請稍後再試')
  }
}

async function mockLogin(email, password) {
  await new Promise((r) => setTimeout(r, 300))
  const signed = (await loadMock()).mockSignIn(email, password, navigator.userAgent)
  if (!signed) throw new LoginError('UNAUTHENTICATED', '帳號或密碼錯誤')
  return mockTokenPayload(signed)
}

/**
 * The mock access token names its mock session and expiry (like the API's `sid` and `exp`), so an
 * ended session is refused and an expired token must be refreshed, as in API mode.
 */
function mockTokenPayload({ user, sid }) {
  const exp = Date.now() + MOCK_TOKEN_SECONDS * 1000
  return { access_token: `mock-token-${user.id}~${sid}~${exp}`, token_type: 'Bearer', expires_in: MOCK_TOKEN_SECONDS, user }
}

async function mockRefresh(token) {
  await new Promise((r) => setTimeout(r, 150))
  return mockTokenPayload((await loadMock()).mockRefresh(token))
}
