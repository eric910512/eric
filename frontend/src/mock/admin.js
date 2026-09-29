/**
 * Mock admin console (VITE_USE_MOCK=true): overview, audit log, read-only settings and symptom
 * form composition, in the shapes of GET /admin/overview, /admin/audit-logs, /admin/settings and
 * GET / PUT /symptoms/forms. Accounts live in `@/mock/patients`, alert rules in `@/mock/alertRules`.
 *
 * The mock audit log only records what the mock admin console does (plus a few seed rows); the API
 * audits every request. Synthetic data only; never a password.
 */
import { nowIso } from '@/mock/clock'
import { mockPublicSettings } from '@/mock/config'
import { mockListNotifications, symptomRecordStore } from '@/mock/nurseReview'
import { MockApiError, mockCareTeamCounts, mockCurrentUser } from '@/mock/patients'
import { mockForms } from '@/mock/symptoms'

const STATE_KEY = 'ccp.mock.admin'
const clone = (v) => structuredClone(v)
const invalid = (details, message) => new MockApiError(400, 'VALIDATION_ERROR', message, details)

function seed() {
  const row = (id, at, action, resource_type, resource_id, actor, changes = null, category = 'data') => ({
    id, occurred_at: at, category, action, actor, resource_type, resource_id, patient: null, outcome: 'success', reason: null,
    changes, request_id: null, http_method: null, endpoint: null,
  })
  const admin = { id: 'mock-user-admin01', display_name: '系統管理者', role: 'admin' }
  return {
    audit: [
      row(2, '2026-09-01T01:05:00.000Z', 'CREATE', 'users', 'mock-user-nurse01', admin, { role: 'nurse', temporary_password_issued: true }),
      row(1, '2026-09-01T01:00:00.000Z', 'LOGIN', 'users', 'mock-user-admin01', admin, null, 'auth'),
    ],
    forms: null, // edited composition of mockForms (null = seed)
  }
}
function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.audit) return saved
  } catch {
    // storage unavailable
  }
  return seed()
}
const state = load()
const save = () => {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage unavailable
  }
}
// every symptom definition of the seed forms (definitions are not edited, only composed into forms)
const DEFINITIONS = Object.fromEntries(Object.values(mockForms).flatMap((f) => f.items).map((i) => [i.definition.code, i.definition]))
// restore an edited form composition into the shared mock form (the patient form uses it too)
if (state.forms) for (const [code, f] of Object.entries(state.forms)) Object.assign(mockForms[code], f)

/** Append an audit row for a mock admin action (no password is ever recorded). */
export function mockAudit(action, resourceType, resourceId, changes = null, category = 'data') {
  const u = mockCurrentUser()
  state.audit.unshift({
    id: Math.max(0, ...state.audit.map((r) => r.id)) + 1, occurred_at: nowIso(), category, action,
    actor: { id: u.id, display_name: u.display_name, role: u.role }, resource_type: resourceType, resource_id: resourceId,
    patient: null, outcome: 'success', reason: null, changes, request_id: null, http_method: null, endpoint: null,
  })
  save()
}

export function mockAdminOverview() {
  mockCurrentUser('admin')
  const alerts = mockListNotifications({ status: 'open' })
  const pending = Object.values(symptomRecordStore).flat().filter((r) => r.record_status === 'final' && r.review_status === 'submitted')
  return {
    data: {
      ...mockCareTeamCounts(),
      alerts: { open: alerts.meta.counts.open, critical: alerts.data.filter((n) => n.severity === 'critical').length },
      symptom_reviews: { pending: pending.length },
      generated_at: nowIso(),
    },
  }
}

const TPE_OFFSET = 8 * 3600e3
const tpeDate = (iso) => new Date(Date.parse(iso) + TPE_OFFSET).toISOString().slice(0, 10)
/** GET /admin/audit-logs: filters actor_id, action, resource_type, category, outcome, from / to (Asia/Taipei dates). */
export function mockSearchAudit(filters = {}) {
  mockCurrentUser('admin')
  const details = []
  const isDate = (v) => !v || /^\d{4}-\d{2}-\d{2}$/.test(v)
  if (!isDate(filters.from)) details.push({ field: 'from', issue: 'must be a date (YYYY-MM-DD)' })
  if (!isDate(filters.to)) details.push({ field: 'to', issue: 'must be a date (YYYY-MM-DD)' })
  if (!details.length && filters.from && filters.to && filters.from > filters.to) details.push({ field: 'to', issue: 'must not be earlier than from' })
  if (details.length) throw invalid(details, '查詢條件有誤')
  const page = Math.max(1, Number(filters.page) || 1)
  const perPage = Math.min(200, Math.max(1, Number(filters.per_page) || 50))
  const rows = state.audit.filter((r) => (!filters.actor_id || r.actor?.id === filters.actor_id)
    && (!filters.patient_id || r.patient?.id === filters.patient_id)
    && ['action', 'resource_type', 'category', 'outcome'].every((k) => !filters[k] || r[k] === filters[k])
    && (!filters.from || tpeDate(r.occurred_at) >= filters.from) && (!filters.to || tpeDate(r.occurred_at) <= filters.to))
  const used = Object.fromEntries(Object.entries(filters).filter(([k, v]) => v && !['page', 'per_page'].includes(k)))
  const result = { data: clone(rows.slice((page - 1) * perPage, page * perPage)), meta: { page, per_page: perPage, total: rows.length } }
  mockAudit('VIEW', 'audit_logs', null, { filters: used, total: rows.length }, 'access')
  return result
}

export function mockAdminSettings() {
  mockCurrentUser('admin')
  const pub = mockPublicSettings().data
  return {
    data: {
      institution: { organization: pub.organization, contacts: pub.contacts, disclaimers: pub.disclaimers },
      security: {
        access_token_minutes: 15,
        login_lockout: { max_failed_attempts: 5, lock_minutes: 15 },
        password_policy: { min_length: 8, max_length: 128, letters_and_digits: true },
      },
      source: 'config_file',
    },
  }
}

function formPayload(f) {
  return {
    code: f.code, name: f.name, version: f.version, intended_for: f.intended_for, is_active: true,
    items: f.items.map((i) => ({ definition_code: i.definition.code, label: i.definition.name_zh, value_type: i.definition.value_type,
      display_order: i.display_order, is_required: i.is_required })),
  }
}
export function mockListForms() {
  mockCurrentUser('admin')
  return { data: Object.values(mockForms).map(formPayload) }
}
/** PUT /symptoms/forms/{code}: {name?, items?: [{definition_code, is_required}]}; version +1 when changed. */
export function mockUpdateForm(code, body = {}) {
  mockCurrentUser('admin')
  const form = mockForms[code] ?? (() => { throw new MockApiError(404, 'NOT_FOUND', 'Symptom form not found') })()
  const defs = DEFINITIONS
  const d = []
  if ('name' in body && (typeof body.name !== 'string' || !body.name.trim() || body.name.length > 100)) d.push({ field: 'name', issue: 'is required (at most 100 characters)' })
  let items = null
  if (body.items != null) {
    if (!Array.isArray(body.items) || !body.items.length || body.items.length > 30) d.push({ field: 'items', issue: 'must be a non-empty list of at most 30 items' })
    else {
      const seen = new Set()
      items = []
      body.items.forEach((it, n) => {
        const def = defs[it?.definition_code]
        if (!def) d.push({ field: `items[${n}].definition_code`, issue: 'must be an active symptom definition code' })
        else if (seen.has(def.code)) d.push({ field: `items[${n}].definition_code`, issue: 'appears more than once' })
        else {
          seen.add(def.code)
          if ('is_required' in it && typeof it.is_required !== 'boolean') d.push({ field: `items[${n}].is_required`, issue: 'must be true or false' })
          items.push({ display_order: n + 1, is_required: it.is_required === true, display_condition: null, definition: def })
        }
      })
    }
  }
  for (const k of Object.keys(body).filter((x) => !['name', 'items'].includes(x)).sort()) d.push({ field: k, issue: 'is not a supported field' })
  if (d.length) throw invalid(d, '量表內容有誤')
  const changed = []
  if ('name' in body && body.name.trim() !== form.name) {
    form.name = body.name.trim()
    changed.push('name')
  }
  const sig = (list) => JSON.stringify(list.map((i) => [i.definition.code, i.display_order, i.is_required]))
  if (items && sig(items) !== sig(form.items)) {
    form.items = items
    changed.push('items')
  }
  if (changed.length) {
    form.version += 1
    state.forms = { ...(state.forms ?? {}), [code]: { name: form.name, version: form.version, items: form.items } }
    mockAudit('UPDATE', 'symptom_forms', form.id, { code, fields: changed, version: form.version, items: form.items.map((i) => i.definition.code) })
  }
  return { data: formPayload(form) }
}
