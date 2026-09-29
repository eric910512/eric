/**
 * Mock notifications of the signed-in patient (VITE_USE_MOCK=true), in the shapes of
 * GET /api/v1/notifications (patient), GET /notifications/{id}, GET /notifications/unread-count,
 * PATCH /notifications/{id}/read and POST /notifications/read-all.
 *
 * They are the items of the patient's dashboard "notifications" widget (reminders and the
 * alerts raised by the patient's own reports), so the home page badge and this list agree.
 * Patient wording only: no staff names, internal notes or handling details. Synthetic data only.
 */
import { nowIso } from '@/mock/clock'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockCurrentUser } from '@/mock/patients'
import { deliverDueReminders } from '@/mock/reminders'

function widget() {
  const user = mockCurrentUser('patient')
  deliverDueReminders(user.patient_id) // manual / scheduled reminders appear once due
  const w = patientDashboards[user.patient_id]?.widgets
  return { user, w, items: w?.notifications?.items ?? [] }
}
function payload(n, pid, w) {
  const s = w['patient-summary']
  const alert = n.type === 'risk_alert'
  return {
    id: n.id, event_key: n.event_key ?? null, type: n.type, origin: n.origin ?? (alert ? 'alert_rule' : 'scheduled'), severity: n.severity, title: n.title, message: n.message,
    patient: { id: pid, patient_code: s.patient_code, display_name: s.display_name },
    alert_rule: null, source: null,
    status: alert ? n.status ?? 'new' : null, status_text: alert ? n.status_text ?? '護理團隊已收到通知' : null,
    handling: alert ? { acknowledged: null, started: null, resolved: n.resolved ? { at: n.resolved_at } : null } : null,
    is_mine: true, is_read: !!n.is_read, read_at: n.read_at ?? null, scheduled_for: n.scheduled_for ?? null, created_at: n.created_at,
  }
}
const sorted = (items) => [...items].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)
const unreadOf = (items) => items.filter((n) => !n.is_read).length
function sync(w) {
  w.notifications.unread_count = unreadOf(w.notifications.items)
}

export function mockMyNotifications() {
  const { user, w, items } = widget()
  const data = w ? sorted(items).map((n) => payload(n, user.patient_id, w)) : []
  const open = data.filter((n) => n.status && n.status !== 'resolved').length
  return { data, meta: { page: 1, per_page: 50, total: data.length, unread: unreadOf(items), unresolved: open,
    counts: { new: data.filter((n) => n.status === 'new').length, acknowledged: 0, in_progress: 0, resolved: data.filter((n) => n.status === 'resolved').length, open, pending: open } } }
}
export function mockMyNotification(id) {
  const { user, w, items } = widget()
  const n = items.find((x) => x.id === id)
  if (!n) throw new MockApiError(404, 'NOT_FOUND', 'Notification not found')
  return { data: payload(n, user.patient_id, w) }
}
export function mockMyUnreadCount() {
  return { data: { unread: unreadOf(widget().items) } }
}
export function mockMyMarkRead(id) {
  const { user, w, items } = widget()
  const n = items.find((x) => x.id === id)
  if (!n) throw new MockApiError(404, 'NOT_FOUND', 'Notification not found')
  if (!n.is_read) Object.assign(n, { is_read: true, read_at: nowIso() })
  sync(w)
  return { data: payload(n, user.patient_id, w) }
}
export function mockMyReadAll() {
  const { w, items } = widget()
  const at = nowIso()
  const changed = items.filter((n) => !n.is_read)
  for (const n of changed) Object.assign(n, { is_read: true, read_at: at })
  if (w) sync(w)
  return { data: { updated: changed.length, unread: 0 } }
}
