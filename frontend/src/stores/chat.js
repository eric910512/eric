import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { useAuthStore } from '@/stores/auth'

/**
 * 病人與護理師聊天室 (text only). The backend decides access (patient: own conversation via `me`;
 * nurse: currently assigned patients) and posts the daily auto reply; this store only shows it.
 * - one open conversation (`thread`): newest page first, older pages on demand, newer messages by
 *   polling (`after_id`), read pointer moved forward while the page is visible
 * - sending: shown at once as pending; every message carries its own Idempotency-Key, kept for a
 *   retry after a network error / timeout (e.g. the API waking up), so a retry never sends twice
 * - `unread`: patient → unread nurse messages; nurse → unread patient messages over all assigned
 *   patients (`inbox` holds the per-patient counts)
 * The store belongs to one signed-in user: a different user (sign-out / sign-in) starts it afresh.
 */
const PAGE = 30
const newKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
// the request may have reached the server: retry with the same key (a replay, never a second message)
const retryable = (err) => !err?.status || err.status >= 500 || err.code === 'IDEMPOTENCY_IN_PROGRESS'
const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible'
const lastId = (t) => t.messages.at(-1)?.id ?? 0
const conversationPath = (patientId) => `/patients/${encodeURIComponent(patientId)}/messages`

function blankThread(patientId) {
  return { patientId, messages: [], hasMore: false, loaded: false, loading: false, loadingOlder: false, error: null, pollError: null,
    service: null, lastRead: 0, pending: [] }
}
function merge(t, rows) {
  const seen = new Set(t.messages.map((m) => m.id))
  const add = rows.filter((m) => m && !seen.has(m.id))
  if (add.length) t.messages = [...t.messages, ...add].sort((a, b) => a.id - b.id)
  return add.length
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    owner: null, // user id the state belongs to
    unread: 0,
    inbox: null, // nurse: [{ patient, last_message, unread_count }]
    inboxError: null,
    thread: null,
  }),

  actions: {
    _own() {
      const uid = useAuthStore().user?.id ?? null
      if (this.owner !== uid) {
        this.$reset()
        this.owner = uid
      }
    },
    _current(patientId) {
      return this.thread && this.thread.patientId === patientId ? this.thread : null
    },

    /** Open a conversation: the newest page, then mark it read. */
    async openThread(patientId) {
      this._own()
      this.thread = blankThread(patientId)
      await this._loadLatest(patientId)
    },
    async _loadLatest(patientId) {
      const t = this._current(patientId)
      if (!t || t.loading) return
      t.loading = true
      t.error = null
      try {
        const body = await call('mockChatMessages', [patientId, { limit: PAGE }],
          () => api.get(conversationPath(patientId), { params: { limit: PAGE } }))
        if (this._current(patientId) !== t) return
        merge(t, body.data)
        t.hasMore = body.meta.has_more
        t.service = body.meta.service
        t.lastRead = body.meta.last_read_message_id
        t.loaded = true
        await this.markRead()
      } catch (err) {
        t.error = err
      } finally {
        t.loading = false
      }
    },

    /** Polling: messages newer than the last one shown (or the first page, until it loaded). */
    async fetchNewer() {
      const t = this.thread
      if (!t) return
      if (!t.loaded) return this._loadLatest(t.patientId)
      try {
        const body = await call('mockChatMessages', [t.patientId, { after_id: lastId(t), limit: 100 }],
          () => api.get(conversationPath(t.patientId), { params: { after_id: lastId(t), limit: 100 } }))
        if (this.thread !== t) return
        const added = merge(t, body.data)
        t.service = body.meta.service
        t.pollError = null
        await this.markRead()
        if (added && this.inbox) this.fetchInbox() // nurse: the list's last-message preview
      } catch (err) {
        if (this.thread === t) t.pollError = err // shown as "reconnecting"; the next poll tries again
      }
    },

    async fetchOlder() {
      const t = this.thread
      if (!t || !t.hasMore || t.loadingOlder || !t.messages.length) return
      t.loadingOlder = true
      try {
        const before = t.messages[0].id
        const body = await call('mockChatMessages', [t.patientId, { before_id: before, limit: PAGE }],
          () => api.get(conversationPath(t.patientId), { params: { before_id: before, limit: PAGE } }))
        if (this.thread !== t) return
        merge(t, body.data)
        t.hasMore = body.meta.has_more
      } catch (err) {
        if (this.thread === t) t.pollError = err
      } finally {
        t.loadingOlder = false
      }
    },

    /** Move the read pointer to the last shown message (only while the page is on screen). */
    async markRead() {
      const t = this.thread
      if (!t || !visible() || lastId(t) <= t.lastRead) return
      const upTo = lastId(t)
      try {
        const body = await call('mockMarkChatRead', [t.patientId, upTo],
          () => api.post(`${conversationPath(t.patientId)}/read`, { last_message_id: upTo }))
        if (this.thread === t) t.lastRead = body.data.last_read_message_id
        if (useAuthStore().role === 'patient') this.unread = body.data.unread_count
        else {
          const row = this.inbox?.find((r) => r.patient.id === t.patientId)
          if (row) row.unread_count = body.data.unread_count
          this.fetchUnread()
        }
      } catch {
        // not fatal: tried again after the next new message
      }
    },

    /** Send a text message: shown at once as pending, replaced by the stored message. */
    async send(text) {
      const t = this.thread
      const body = (text ?? '').trim()
      if (!t || !body) return
      t.pending.push({ localId: newKey(), body, key: newKey(), status: 'sending', error: null, created_at: new Date().toISOString() })
      await this._deliver(t, t.pending.at(-1))
    },
    async retry(localId) {
      const t = this.thread
      const item = t?.pending.find((p) => p.localId === localId)
      if (item && item.status === 'failed') await this._deliver(t, item)
    },
    discard(localId) {
      const t = this.thread
      if (t) t.pending = t.pending.filter((p) => p.localId !== localId || p.status === 'sending')
    },
    async _deliver(t, item) {
      item.status = 'sending'
      item.error = null
      try {
        const res = await call('mockSendChatMessage', [t.patientId, item.body, item.key],
          () => api.post(conversationPath(t.patientId), { body: item.body }, { headers: { 'Idempotency-Key': item.key } }))
        t.pending = t.pending.filter((p) => p.localId !== item.localId)
        merge(t, [res.data.message, res.data.auto_reply])
        t.lastRead = Math.max(t.lastRead, lastId(t))
        if (this.inbox) this.fetchInbox() // nurse: the list's last-message preview
        if (this.thread === t) await this.fetchNewer() // a replay does not return the auto reply; this does
      } catch (err) {
        item.status = 'failed'
        item.error = err?.message || '傳送失敗'
        if (!retryable(err)) item.key = newKey() // the server answered: a retry is a new request
      }
    },

    closeThread() {
      this.thread = null
    },

    async fetchUnread() {
      this._own()
      try {
        const body = await call('mockChatUnreadCount', [], () => api.get('/chat/unread-count'))
        this.unread = body.data.unread_count
      } catch {
        // keep the last known count; the next poll tries again
      }
    },

    /** Nurse: assigned patients with last message and unread count. */
    async fetchInbox() {
      this._own()
      try {
        const body = await call('mockChatInbox', [], () => api.get('/chat/inbox'))
        this.inbox = body.data
        this.unread = body.meta.unread_count
        this.inboxError = null
      } catch (err) {
        this.inboxError = err
      }
    },
  },
})
