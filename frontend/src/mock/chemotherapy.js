/**
 * Mock chemotherapy (VITE_USE_MOCK=true), in the shapes of /api/v1/chemotherapy/… (api-design.md §5):
 * drugs, regimens, plans, cycles and medication records, with the same rules as the backend
 * (app/modules/chemotherapy/services.py): who may write, cycle lifecycle, append-only medication
 * corrections (amends_id / amended / entered_in_error), cycle_day fixed when a record is written.
 *
 * Patient access (404 outside the viewer's scope) is applied in `@/mock/api`. The mock
 * timeline and dashboard read their cycles / medications from here, so changes show up there.
 * State survives a reload of the tab (sessionStorage). Synthetic data only.
 */
import { createInfusions, cycleAppointmentId, infusionOptions, linkChemotherapy, moveCycleAppointments } from '@/mock/appointments'
import { nowIso } from '@/mock/clock'
import { linkNursing } from '@/mock/nursing'
import { nurseOverview } from '@/mock/nurseOverview'
import { patientDashboards } from '@/mock/patientDashboards'
import { MockApiError, mockAccessPatient, mockCurrentUser, mockPerson } from '@/mock/patients'

const STATE_KEY = 'ccp.mock.chemotherapy'
const TZ = 'Asia/Taipei'
const P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'
const NURSE01 = 'mock-user-nurse01'
const clone = (v) => structuredClone(v)

const INTENTS = ['curative', 'adjuvant', 'neoadjuvant', 'palliative']
const ROUTES = ['IV', 'PO', 'SC']
const RISKS = ['high', 'moderate', 'low']
const MED_TYPES = ['chemo', 'premedication', 'supportive']
const ADMIN_STATUS = ['given', 'held', 'partial', 'refused']
const OPEN_PLAN = ['planned', 'active']
const NOT_STARTED = ['scheduled', 'delayed']
const STARTED = ['in_progress', 'completed']
const FINISHED = ['completed', 'cancelled']
const MED_FIELDS = ['drug_id', 'medication_type', 'dose_value', 'dose_unit', 'route', 'administered_at', 'infusion_duration_min', 'administration_status', 'reaction_notes']

const invalid = (details, message = '資料內容有誤') => new MockApiError(400, 'VALIDATION_ERROR', message, details)
const conflict = (code, message, details = []) => new MockApiError(409, code, message, details)
const notFound = (what) => new MockApiError(404, 'NOT_FOUND', `${what} not found`)

// ------------------------------------------------------------------ seed (matches the mock dashboards / timeline)
function seed() {
  const cycle = (id, plan, patient, number, scheduled, extra = {}) => ({
    id, plan_id: plan, patient_id: patient, cycle_number: number, scheduled_date: scheduled, actual_start_date: null,
    actual_end_date: null, status: 'scheduled', delay_days: null, delay_reason: null, dose_modification_pct: 100,
    nadir_start_day: null, nadir_end_day: null, weight_kg: null, bsa_m2: null, notes: null, ...extra,
  })
  const med = (id, patient, cycleId, drugId, dose, at) => ({
    id, patient_id: patient, cycle_id: cycleId, cycle_day: 1, drug_id: drugId, medication_type: 'chemo', dose_value: dose,
    dose_unit: 'mg', route: 'IV', administered_at: at, infusion_duration_min: 120, administration_status: 'given',
    reaction_notes: null, administered_by: NURSE01, source: 'nurse', record_status: 'final', amends_id: null, created_at: at,
  })
  return {
    drugs: [
      { id: 1, generic_name: 'Cisplatin', brand_name: null, drug_class: 'platinum', default_route: 'IV', is_active: true },
      { id: 2, generic_name: 'Doxorubicin', brand_name: null, drug_class: 'anthracycline', default_route: 'IV', is_active: true },
      { id: 3, generic_name: 'Cyclophosphamide', brand_name: null, drug_class: 'alkylating', default_route: 'IV', is_active: true },
    ],
    regimens: [
      { id: 1, name: 'Cisplatin q3w', description: 'Cisplatin 100 mg/m² Day 1，每 21 天一個 Cycle（Demo）', cycle_length_days: 21,
        default_total_cycles: 3, emetogenic_risk: 'high', is_active: true,
        drugs: [{ id: 1, drug_id: 1, dose_value: 100, dose_unit: 'mg/m2', route: 'IV', day_of_cycle: '1', sequence: 1 }] },
      { id: 2, name: 'AC q3w', description: 'Doxorubicin 60 mg/m² + Cyclophosphamide 600 mg/m² Day 1（Demo）', cycle_length_days: 21,
        default_total_cycles: 4, emetogenic_risk: 'high', is_active: true,
        drugs: [{ id: 2, drug_id: 2, dose_value: 60, dose_unit: 'mg/m2', route: 'IV', day_of_cycle: '1', sequence: 1 },
          { id: 3, drug_id: 3, dose_value: 600, dose_unit: 'mg/m2', route: 'IV', day_of_cycle: '1', sequence: 2 }] },
    ],
    plans: [
      { id: 1, patient_id: P1, diagnosis_id: 1, regimen_id: 1, plan_name: 'Cisplatin 同步化療（Demo）', intent: 'curative', line_of_therapy: 1,
        total_cycles: 3, start_date: '2026-09-21', end_date: null, status: 'active', discontinue_reason: null,
        attending_physician_name: '測試醫師 王', created_by: NURSE01, created_at: '2026-09-01T01:00:00.000Z' },
      { id: 2, patient_id: P2, diagnosis_id: 2, regimen_id: 2, plan_name: 'AC 輔助化療（Demo）', intent: 'adjuvant', line_of_therapy: 1,
        total_cycles: 4, start_date: '2026-08-26', end_date: null, status: 'active', discontinue_reason: null,
        attending_physician_name: '測試醫師 陳', created_by: NURSE01, created_at: '2026-08-20T01:00:00.000Z' },
    ],
    cycles: [
      cycle(1, 1, P1, 1, '2026-09-21', { actual_start_date: '2026-09-21', status: 'in_progress', nadir_start_day: 7, nadir_end_day: 14, weight_kg: 64.5, bsa_m2: 1.74 }),
      cycle(11, 2, P2, 1, '2026-08-26', { actual_start_date: '2026-08-26', actual_end_date: '2026-09-15', status: 'completed', nadir_start_day: 7, nadir_end_day: 14 }),
      cycle(12, 2, P2, 2, '2026-09-16', { actual_start_date: '2026-09-16', status: 'in_progress', nadir_start_day: 7, nadir_end_day: 14 }),
      cycle(13, 2, P2, 3, '2026-10-07'),
      cycle(14, 2, P2, 4, '2026-10-28'),
    ],
    medications: [
      med(1, P1, 1, 1, 174, '2026-09-21T01:30:00.000Z'),
      med(11, P2, 11, 2, 90, '2026-08-26T02:00:00.000Z'),
      med(12, P2, 12, 2, 90, '2026-09-16T02:00:00.000Z'),
    ],
    nextId: 1000,
  }
}
function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STATE_KEY))
    if (saved?.plans) return saved
  } catch {
    // storage unavailable: start from the seed
  }
  return seed()
}
const state = load()
function save() {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // storage unavailable: state lives in memory only
  }
}
const nextId = () => ++state.nextId
linkNursing({ cycle: (patientId, at) => { const c = mockCurrentCycle(patientId, localDay(at)); return { cycle_id: c?.cycle_id ?? null, cycle_day: c?.cycle_day ?? null } } })
linkChemotherapy({
  number: (id) => state.cycles.find((c) => c.id === id)?.cycle_number ?? null,
  belongs: (id, patientId) => state.cycles.some((c) => c.id === id && c.patient_id === patientId),
})

// ------------------------------------------------------------------ dates
const localToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date())
const localDay = (iso) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date(iso))
const addDays = (d, n) => {
  const x = new Date(`${d}T00:00:00Z`)
  x.setUTCDate(x.getUTCDate() + n)
  return x.toISOString().slice(0, 10)
}
const daysBetween = (a, b) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000)
const isDate = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().startsWith(v)
const cycleDayOn = (c, day) => (!c.actual_start_date || day < c.actual_start_date ? null : daysBetween(c.actual_start_date, day) + 1)
function inNadir(c, day) {
  if (!c.actual_start_date || c.nadir_start_day == null) return [false, null, null]
  const start = addDays(c.actual_start_date, c.nadir_start_day - 1)
  const end = addDays(c.actual_start_date, (c.nadir_end_day ?? c.nadir_start_day) - 1)
  return [start <= day && day <= end, start, end]
}

// ------------------------------------------------------------------ field parsing (= services.Fields)
class Fields {
  constructor(body, message) {
    Object.assign(this, { body, message, details: [], values: {} })
  }
  has(k) { return k in this.body }
  error(field, issue) { this.details.push({ field, issue }) }
  text(k, limit, { required = false } = {}) {
    if (!(k in this.body) && !required) return
    const v = this.body[k]
    if (v == null || (typeof v === 'string' && !v.trim())) {
      if (required) this.error(k, `is required (at most ${limit} characters)`)
      else this.values[k] = null
      return
    }
    if (typeof v !== 'string' || v.trim().length > limit) this.error(k, `must be text of at most ${limit} characters`)
    else this.values[k] = v.trim()
  }
  choice(k, choices, { required = false, allowNull = false } = {}) {
    if (!(k in this.body)) {
      if (required) this.error(k, `is required (one of: ${choices.join(', ')})`)
      return
    }
    const v = this.body[k]
    if (v === null && allowNull && !required) this.values[k] = null
    else if (!choices.includes(v)) this.error(k, `must be one of: ${choices.join(', ')}`)
    else this.values[k] = v
  }
  integer(k, lo, hi, { required = false, allowNull = true } = {}) {
    const v = this.body[k]
    if (v == null) {
      if (required) this.error(k, `is required (integer ${lo}–${hi})`)
      else if (k in this.body && allowNull) this.values[k] = null
      return
    }
    if (!Number.isInteger(v) || v < lo || v > hi) this.error(k, `must be an integer from ${lo} to ${hi}`)
    else this.values[k] = v
  }
  number(k, lo, hi, { places = 2, required = false } = {}) {
    const v = this.body[k]
    if (v == null) {
      if (required) this.error(k, `is required (number ${lo}–${hi})`)
      else if (k in this.body) this.values[k] = null
      return
    }
    const n = typeof v === 'number' || (typeof v === 'string' && v.trim() !== '') ? Number(v) : NaN
    if (!Number.isFinite(n) || n < lo || n > hi) this.error(k, `must be a number from ${lo} to ${hi}`)
    else this.values[k] = Math.round(n * 10 ** places) / 10 ** places
  }
  day(k, { required = false } = {}) {
    const v = this.body[k]
    if (v == null) {
      if (required) this.error(k, 'is required (YYYY-MM-DD)')
      else if (k in this.body) this.values[k] = null
      return null
    }
    if (!isDate(v)) {
      this.error(k, 'must be a date (YYYY-MM-DD)')
      return null
    }
    this.values[k] = v
    return v
  }
  boolean(k, dflt) {
    const v = k in this.body ? this.body[k] : dflt
    if (typeof v !== 'boolean') {
      this.error(k, 'must be true or false')
      return dflt
    }
    return v
  }
  unknown(allowed) {
    for (const k of Object.keys(this.body).filter((x) => !allowed.includes(x)).sort()) this.error(k, 'is not a supported field')
  }
  done() {
    if (this.details.length) throw invalid(this.details, this.message)
    return this.values
  }
}

const isStaff = (u) => u.role === 'nurse' || u.role === 'admin'
const drugById = (id) => state.drugs.find((d) => d.id === id)
const regimenById = (id) => state.regimens.find((r) => r.id === id)
const cyclesOf = (planId) => state.cycles.filter((c) => c.plan_id === planId).sort((a, b) => a.cycle_number - b.cycle_number)

// ------------------------------------------------------------------ serializers (= services.*_payload)
const drugPayload = (d) => clone(d)
function regimenPayload(r, full = true) {
  const data = { id: r.id, name: r.name, cycle_length_days: r.cycle_length_days, default_total_cycles: r.default_total_cycles, emetogenic_risk: r.emetogenic_risk }
  if (full) {
    Object.assign(data, {
      description: r.description, is_active: r.is_active,
      drugs: r.drugs.map((rd) => ({ id: rd.id, drug: { id: rd.drug_id, generic_name: drugById(rd.drug_id).generic_name },
        dose_value: rd.dose_value, dose_unit: rd.dose_unit, route: rd.route, day_of_cycle: rd.day_of_cycle, sequence: rd.sequence })),
    })
  }
  return data
}
function cyclePayload(c, viewer, today = localToday()) {
  const started = STARTED.includes(c.status) && c.actual_start_date
  const running = c.status === 'in_progress'
  const data = {
    id: c.id, plan_id: c.plan_id, cycle_number: c.cycle_number, scheduled_date: c.scheduled_date, actual_start_date: c.actual_start_date,
    actual_end_date: c.actual_end_date, status: c.status, delay_days: c.delay_days, delay_reason: c.delay_reason,
    dose_modification_pct: c.dose_modification_pct, nadir_start_day: c.nadir_start_day, nadir_end_day: c.nadir_end_day,
    cycle_day: running ? cycleDayOn(c, today) : null, in_nadir: running ? inNadir(c, today)[0] : false,
    medication_count: started ? state.medications.filter((m) => m.cycle_id === c.id && m.record_status === 'final').length : 0,
    appointment_id: cycleAppointmentId(c.id),
  }
  if (isStaff(viewer)) Object.assign(data, { weight_kg: c.weight_kg, bsa_m2: c.bsa_m2, notes: c.notes })
  return data
}
function planPayload(plan, viewer, withCycles = true) {
  const patient = mockAccessPatient(plan.patient_id)
  const dx = patient.diagnoses.find((d) => d.id === plan.diagnosis_id)
  const cycles = cyclesOf(plan.id)
  const current = cycles.find((c) => c.status === 'in_progress')
  const regimen = plan.regimen_id ? regimenById(plan.regimen_id) : null
  const data = {
    id: plan.id, patient_id: plan.patient_id,
    diagnosis: dx ? { id: dx.id, cancer_type_code: dx.cancer_type.code, name_zh: dx.cancer_type.name_zh, stage: dx.stage } : null,
    regimen: regimen ? regimenPayload(regimen, false) : null,
    plan_name: plan.plan_name, intent: plan.intent, line_of_therapy: plan.line_of_therapy, total_cycles: plan.total_cycles,
    start_date: plan.start_date, end_date: plan.end_date, status: plan.status, discontinue_reason: plan.discontinue_reason,
    attending_physician_name: plan.attending_physician_name,
    progress: {
      completed_cycles: cycles.filter((c) => c.status === 'completed').length,
      current_cycle_number: current?.cycle_number ?? null,
      current_cycle_day: current ? cycleDayOn(current, localToday()) : null,
    },
    created_at: plan.created_at,
  }
  if (withCycles) data.cycles = cycles.map((c) => cyclePayload(c, viewer))
  if (isStaff(viewer)) data.created_by = mockPerson(plan.created_by)
  return data
}
function medicationPayload(m, viewer) {
  const c = state.cycles.find((x) => x.id === m.cycle_id)
  const data = {
    id: m.id, cycle_id: m.cycle_id, cycle_number: c.cycle_number, cycle_day: m.cycle_day,
    drug: { id: m.drug_id, generic_name: drugById(m.drug_id).generic_name }, medication_type: m.medication_type,
    dose_value: m.dose_value, dose_unit: m.dose_unit, route: m.route, administered_at: m.administered_at,
    infusion_duration_min: m.infusion_duration_min, administration_status: m.administration_status, record_status: m.record_status,
  }
  if (isStaff(viewer)) {
    const newer = state.medications.find((x) => x.amends_id === m.id)
    Object.assign(data, { reaction_notes: m.reaction_notes, administered_by: mockPerson(m.administered_by), source: m.source,
      amends_id: m.amends_id, amended_by_id: newer?.id ?? null, created_at: m.created_at })
  }
  return data
}

// ------------------------------------------------------------------ lookups (the access guard in `@/mock/api` uses the patient ids)
export const mockPlanPatient = (id) => state.plans.find((p) => p.id === id)?.patient_id ?? null
export const mockCyclePatient = (id) => state.cycles.find((c) => c.id === id)?.patient_id ?? null
export const mockMedicationPatient = (id) => state.medications.find((m) => m.id === id)?.patient_id ?? null
const plan = (id) => state.plans.find((p) => p.id === id) ?? (() => { throw notFound('Chemotherapy plan') })()
const cycle = (id) => state.cycles.find((c) => c.id === id) ?? (() => { throw notFound('Cycle') })()
const medication = (id) => state.medications.find((m) => m.id === id) ?? (() => { throw notFound('Medication record') })()

// ------------------------------------------------------------------ drugs / regimens
export function mockListDrugs({ q = null, includeInactive = false } = {}) {
  mockCurrentUser('nurse', 'admin')
  const s = q?.trim().toLowerCase()
  const rows = state.drugs.filter((d) => (includeInactive || d.is_active) && (!s || d.generic_name.toLowerCase().includes(s) || (d.brand_name ?? '').toLowerCase().includes(s)))
  return { data: rows.sort((a, b) => a.generic_name.localeCompare(b.generic_name)).map(drugPayload) }
}
function drugValues(body, partial, current = null) {
  const f = new Fields(body, '藥物資料有誤')
  if (!partial || f.has('generic_name')) f.text('generic_name', 100, { required: true })
  if (f.has('brand_name')) f.text('brand_name', 100)
  if (f.has('drug_class')) f.text('drug_class', 50)
  if (f.has('default_route')) f.choice('default_route', ROUTES, { allowNull: true })
  if (f.has('is_active')) f.values.is_active = f.boolean('is_active')
  f.unknown(['generic_name', 'brand_name', 'drug_class', 'default_route', 'is_active'])
  const values = f.done()
  const name = values.generic_name
  if (name && name !== current?.generic_name && state.drugs.some((d) => d.generic_name.toLowerCase() === name.toLowerCase())) {
    throw conflict('CONFLICT', '這個藥物已存在', [{ field: 'generic_name', issue: 'is already registered' }])
  }
  return values
}
export function mockCreateDrug(body) {
  mockCurrentUser('admin')
  const d = { id: nextId(), brand_name: null, drug_class: null, default_route: null, ...drugValues(body, false), is_active: true }
  state.drugs.push(d)
  save()
  return { data: drugPayload(d) }
}
export function mockUpdateDrug(id, body) {
  mockCurrentUser('admin')
  const d = drugById(id) ?? (() => { throw notFound('Drug') })()
  Object.assign(d, drugValues(body, true, d))
  save()
  return { data: drugPayload(d) }
}
export function mockListRegimens({ includeInactive = false } = {}) {
  mockCurrentUser('nurse', 'admin')
  return { data: state.regimens.filter((r) => includeInactive || r.is_active).sort((a, b) => a.name.localeCompare(b.name)).map((r) => regimenPayload(r)) }
}
function regimenDrugs(raw) {
  if (!Array.isArray(raw) || raw.length > 20) throw invalid([{ field: 'drugs', issue: 'must be a list of at most 20 drugs' }], '處方資料有誤')
  const details = []
  const seen = new Set()
  const rows = raw.map((item, i) => {
    const at = `drugs[${i}]`
    const drug = Number.isInteger(item?.drug_id) ? drugById(item.drug_id) : null
    if (!drug || !drug.is_active) {
      details.push({ field: `${at}.drug_id`, issue: 'must be an active drug id' })
      return null
    }
    const f = new Fields(item, '')
    f.number('dose_value', 0.01, 100000)
    f.text('dose_unit', 20)
    f.choice('route', ROUTES, { allowNull: true })
    f.text('day_of_cycle', 20)
    f.integer('sequence', 1, 99)
    details.push(...f.details.map((d) => ({ field: `${at}.${d.field}`, issue: d.issue })))
    const key = `${drug.id}|${f.values.day_of_cycle ?? ''}`
    if (seen.has(key)) details.push({ field: at, issue: 'the same drug is listed twice for the same day' })
    seen.add(key)
    return { id: nextId(), drug_id: drug.id, dose_value: null, dose_unit: null, route: null, day_of_cycle: null, sequence: i + 1, ...f.values }
  })
  if (details.length) throw invalid(details, '處方資料有誤')
  return rows
}
function regimenValues(body, partial, current = null) {
  const f = new Fields(body, '處方資料有誤')
  if (!partial || f.has('name')) f.text('name', 50, { required: true })
  if (f.has('description')) f.text('description', 2000)
  f.integer('cycle_length_days', 1, 365, { required: !partial })
  f.integer('default_total_cycles', 1, 30)
  if (f.has('emetogenic_risk')) f.choice('emetogenic_risk', RISKS, { allowNull: true })
  if (f.has('is_active')) f.values.is_active = f.boolean('is_active')
  f.unknown(['name', 'description', 'cycle_length_days', 'default_total_cycles', 'emetogenic_risk', 'is_active', 'drugs'])
  const values = f.done()
  if (values.name && values.name !== current?.name && state.regimens.some((r) => r.name.toLowerCase() === values.name.toLowerCase())) {
    throw conflict('CONFLICT', '這個處方名稱已存在', [{ field: 'name', issue: 'is already registered' }])
  }
  return values
}
export function mockCreateRegimen(body) {
  mockCurrentUser('admin')
  const values = regimenValues(body, false)
  const r = { id: nextId(), description: null, default_total_cycles: null, emetogenic_risk: null, ...values, is_active: true, drugs: regimenDrugs(body.drugs ?? []) }
  state.regimens.push(r)
  save()
  return { data: regimenPayload(r) }
}
export function mockUpdateRegimen(id, body) {
  mockCurrentUser('admin')
  const r = regimenById(id) ?? (() => { throw notFound('Regimen') })()
  Object.assign(r, regimenValues(body, true, r))
  if ('drugs' in body) r.drugs = regimenDrugs(body.drugs)
  save()
  return { data: regimenPayload(r) }
}

// ------------------------------------------------------------------ plans
export function mockListPlans(patientId) {
  const viewer = mockCurrentUser()
  const pid = mockAccessPatient(patientId).id
  const rows = state.plans.filter((p) => p.patient_id === pid).sort((a, b) => b.start_date.localeCompare(a.start_date) || b.id - a.id)
  return { data: rows.map((p) => planPayload(p, viewer)) }
}
export function mockGetPlan(id) {
  return { data: planPayload(plan(id), mockCurrentUser()) }
}
function planFields(f, partial) {
  if (!partial || f.has('plan_name')) f.text('plan_name', 100, { required: !partial })
  if (f.has('intent') || !partial) f.choice('intent', INTENTS, { allowNull: true })
  f.integer('line_of_therapy', 1, 20)
  f.integer('total_cycles', 1, 30, { required: !partial, allowNull: false })
  if (f.has('attending_physician_name')) f.text('attending_physician_name', 100)
}
export function mockCreatePlan(body) {
  const user = mockCurrentUser('nurse')
  if (typeof body.patient_id !== 'string' || !body.patient_id) throw invalid([{ field: 'patient_id', issue: 'is required' }], '療程資料有誤')
  const patient = mockAccessPatient(body.patient_id)
  const f = new Fields(body, '療程資料有誤')
  planFields(f, false)
  const start = f.day('start_date', { required: true })
  const generate = f.boolean('generate_cycles', true)
  const dx = Number.isInteger(body.diagnosis_id) ? patient.diagnoses.find((d) => d.id === body.diagnosis_id) : null
  if (!dx) f.error('diagnosis_id', "must be one of this patient's diagnoses")
  let regimen = null
  if (body.regimen_id != null) {
    regimen = Number.isInteger(body.regimen_id) ? regimenById(body.regimen_id) : null
    if (!regimen?.is_active) f.error('regimen_id', 'must be an active regimen id')
  }
  if (generate && !f.details.length && !regimen?.cycle_length_days) {
    f.error('generate_cycles', 'needs a regimen with cycle_length_days (or send false and add cycles one by one)')
  }
  const infusions = infusionOptions(body.generate_infusion_appointments, f.details)
  if (infusions && !generate) f.error('generate_infusion_appointments', 'needs generate_cycles')
  f.unknown(['patient_id', 'diagnosis_id', 'regimen_id', 'plan_name', 'intent', 'line_of_therapy', 'total_cycles', 'start_date', 'attending_physician_name', 'generate_cycles', 'generate_infusion_appointments'])
  const { start_date: _s, ...values } = f.done()
  if (state.plans.some((p) => p.patient_id === patient.id && OPEN_PLAN.includes(p.status))) {
    throw conflict('CONFLICT', '這位病人已有進行中或預定的療程，請先完成或停止原療程')
  }
  const p = { id: nextId(), patient_id: patient.id, diagnosis_id: dx.id, regimen_id: regimen?.id ?? null, intent: null, line_of_therapy: null,
    attending_physician_name: null, ...values, start_date: start, end_date: null, status: 'planned', discontinue_reason: null,
    created_by: user.id, created_at: nowIso() }
  state.plans.push(p)
  if (generate) {
    for (let n = 1; n <= p.total_cycles; n += 1) {
      state.cycles.push({ id: nextId(), plan_id: p.id, patient_id: patient.id, cycle_number: n, scheduled_date: addDays(start, (n - 1) * regimen.cycle_length_days),
        actual_start_date: null, actual_end_date: null, status: 'scheduled', delay_days: null, delay_reason: null, dose_modification_pct: 100,
        nadir_start_day: null, nadir_end_day: null, weight_kg: null, bsa_m2: null, notes: null })
    }
    if (infusions) createInfusions(patient.id, user.id, cyclesOf(p.id), infusions)
  }
  sync(patient.id)
  return { data: planPayload(p, user) }
}
function ensureOpen(p) {
  if (!OPEN_PLAN.includes(p.status)) throw conflict('INVALID_STATE', '這個療程已結束，不能再修改')
}
export function mockUpdatePlan(id, body) {
  const user = mockCurrentUser('nurse')
  const p = plan(id)
  ensureOpen(p)
  const f = new Fields(body, '療程資料有誤')
  planFields(f, true)
  f.unknown(['plan_name', 'intent', 'line_of_therapy', 'total_cycles', 'attending_physician_name'])
  const values = f.done()
  if ('total_cycles' in values && values.total_cycles < Math.max(0, ...cyclesOf(p.id).map((c) => c.cycle_number))) {
    throw invalid([{ field: 'total_cycles', issue: 'cannot be less than the number of existing cycles' }], '療程資料有誤')
  }
  Object.assign(p, values)
  sync(p.patient_id)
  return { data: planPayload(p, user) }
}
export function mockDiscontinuePlan(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const p = plan(id)
  ensureOpen(p)
  const f = new Fields(body, '停止療程資料有誤')
  f.text('discontinue_reason', 2000, { required: true })
  f.unknown(['discontinue_reason'])
  const { discontinue_reason: reason } = f.done()
  const today = localToday()
  for (const c of cyclesOf(p.id)) {
    if (NOT_STARTED.includes(c.status)) c.status = 'cancelled'
    else if (c.status === 'in_progress') Object.assign(c, { status: 'completed', actual_end_date: today > c.actual_start_date ? today : c.actual_start_date })
  }
  Object.assign(p, { status: 'discontinued', discontinue_reason: reason, end_date: today })
  sync(p.patient_id)
  return { data: planPayload(p, user) }
}

// ------------------------------------------------------------------ cycles
function cycleFields(f) {
  f.number('weight_kg', 20, 300, { places: 1 })
  f.number('bsa_m2', 0.5, 3.5)
  f.integer('dose_modification_pct', 1, 150, { allowNull: false })
  f.integer('nadir_start_day', 1, 60)
  f.integer('nadir_end_day', 1, 60)
  if (f.has('notes')) f.text('notes', 2000)
}
const CYCLE_FIELDS = ['weight_kg', 'bsa_m2', 'dose_modification_pct', 'nadir_start_day', 'nadir_end_day', 'notes']
function checkNadir(values, current = {}) {
  const s = values.nadir_start_day !== undefined ? values.nadir_start_day : current.nadir_start_day
  const e = values.nadir_end_day !== undefined ? values.nadir_end_day : current.nadir_end_day
  if (s != null && e != null && e < s) throw invalid([{ field: 'nadir_end_day', issue: 'must not be before nadir_start_day' }], 'Cycle 資料有誤')
}
export function mockListCycles(planId) {
  const viewer = mockCurrentUser()
  return { data: cyclesOf(plan(planId).id).map((c) => cyclePayload(c, viewer)) }
}
export function mockGetCycle(id) {
  return { data: cyclePayload(cycle(id), mockCurrentUser()) }
}
export function mockAddCycle(planId, body) {
  const user = mockCurrentUser('nurse')
  const p = plan(planId)
  ensureOpen(p)
  const f = new Fields(body, 'Cycle 資料有誤')
  f.day('scheduled_date', { required: true })
  cycleFields(f)
  f.unknown(['scheduled_date', ...CYCLE_FIELDS])
  const values = f.done()
  checkNadir(values)
  const number = Math.max(0, ...state.cycles.filter((c) => c.plan_id === p.id).map((c) => c.cycle_number)) + 1
  if (number > (p.total_cycles ?? 0)) throw conflict('CONFLICT', `已達療程的 ${p.total_cycles} 個 Cycle；請先修改療程的總 Cycle 數`)
  const c = { id: nextId(), plan_id: p.id, patient_id: p.patient_id, cycle_number: number, actual_start_date: null, actual_end_date: null,
    status: 'scheduled', delay_days: null, delay_reason: null, dose_modification_pct: 100, nadir_start_day: null, nadir_end_day: null,
    weight_kg: null, bsa_m2: null, notes: null, ...values }
  state.cycles.push(c)
  sync(p.patient_id)
  return { data: cyclePayload(c, user) }
}
export function mockUpdateCycle(id, body) {
  const user = mockCurrentUser('nurse')
  const c = cycle(id)
  ensureOpen(plan(c.plan_id))
  const f = new Fields(body, 'Cycle 資料有誤')
  if (f.has('scheduled_date')) f.day('scheduled_date', { required: true })
  cycleFields(f)
  for (const k of ['actual_start_date', 'actual_end_date', 'status', 'cycle_number']) {
    if (k in body) f.error(k, 'use start / complete / delay (dates of started cycles are not edited)')
  }
  f.unknown(['scheduled_date', ...CYCLE_FIELDS, 'actual_start_date', 'actual_end_date', 'status', 'cycle_number'])
  const values = f.done()
  if ('scheduled_date' in values && !NOT_STARTED.includes(c.status)) throw conflict('INVALID_STATE', '已開始的 Cycle 不能修改預定日期')
  if (FINISHED.includes(c.status) && Object.keys(values).length) throw conflict('INVALID_STATE', '已結束的 Cycle 不能修改')
  checkNadir(values, c)
  Object.assign(c, values)
  sync(c.patient_id)
  return { data: cyclePayload(c, user) }
}
export function mockStartCycle(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const c = cycle(id)
  const p = plan(c.plan_id)
  ensureOpen(p)
  if (!NOT_STARTED.includes(c.status)) throw conflict('INVALID_STATE', '這個 Cycle 已經開始或已結束')
  const f = new Fields(body, '開始 Cycle 資料有誤')
  const today = localToday()
  const start = f.day('start_date') ?? today
  cycleFields(f)
  f.unknown(['start_date', ...CYCLE_FIELDS])
  if (start > today) f.error('start_date', 'cannot be in the future')
  else if (start < addDays(today, -30)) f.error('start_date', 'cannot be more than 30 days ago')
  const { start_date: _s, ...values } = f.done()
  if (state.cycles.some((x) => x.patient_id === c.patient_id && x.status === 'in_progress')) {
    throw conflict('INVALID_STATE', '這位病人還有進行中的 Cycle，請先完成該 Cycle')
  }
  const earlier = cyclesOf(p.id).filter((x) => x.cycle_number < c.cycle_number)
  if (earlier.some((x) => !FINISHED.includes(x.status))) throw conflict('INVALID_STATE', '前面的 Cycle 尚未完成或取消')
  const lastStart = earlier.map((x) => x.actual_start_date).filter(Boolean).sort().at(-1)
  const lastEnd = earlier.map((x) => x.actual_end_date).filter(Boolean).sort().at(-1)
  if ((lastEnd && start < lastEnd) || (lastStart && start <= lastStart)) {
    throw invalid([{ field: 'start_date', issue: 'must be after the previous cycle' }], '開始 Cycle 資料有誤')
  }
  const previous = earlier.filter((x) => x.status === 'completed').at(-1)
  if (previous) {
    if (!('nadir_start_day' in values)) values.nadir_start_day = c.nadir_start_day ?? previous.nadir_start_day
    if (!('nadir_end_day' in values)) values.nadir_end_day = c.nadir_end_day ?? previous.nadir_end_day
  }
  checkNadir(values, c)
  Object.assign(c, values, { actual_start_date: start, status: 'in_progress' })
  if (p.status === 'planned') p.status = 'active'
  sync(c.patient_id)
  return { data: cyclePayload(c, user) }
}
export function mockCompleteCycle(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const c = cycle(id)
  const p = plan(c.plan_id)
  ensureOpen(p)
  if (c.status !== 'in_progress') throw conflict('INVALID_STATE', '只有進行中的 Cycle 可以完成')
  const f = new Fields(body, '完成 Cycle 資料有誤')
  const today = localToday()
  const end = f.day('end_date') ?? today
  if (f.has('notes')) f.text('notes', 2000)
  f.unknown(['end_date', 'notes'])
  if (end > today) f.error('end_date', 'cannot be in the future')
  else if (end < c.actual_start_date) f.error('end_date', 'cannot be before the cycle start')
  const values = f.done()
  if ('notes' in values) c.notes = values.notes
  Object.assign(c, { actual_end_date: end, status: 'completed' })
  const cycles = cyclesOf(p.id)
  if (cycles.length >= (p.total_cycles ?? 0) && cycles.every((x) => FINISHED.includes(x.status))) Object.assign(p, { status: 'completed', end_date: end })
  sync(c.patient_id)
  return { data: cyclePayload(c, user) }
}
export function mockDelayCycle(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const c = cycle(id)
  ensureOpen(plan(c.plan_id))
  if (!NOT_STARTED.includes(c.status)) throw conflict('INVALID_STATE', '只有尚未開始的 Cycle 可以延後')
  const f = new Fields(body, '延後 Cycle 資料有誤')
  const next = f.day('new_scheduled_date', { required: true })
  f.text('delay_reason', 500, { required: true })
  const moveAppointments = f.boolean('reschedule_appointments', false)
  f.unknown(['new_scheduled_date', 'delay_reason', 'reschedule_appointments'])
  if (next && next <= c.scheduled_date) f.error('new_scheduled_date', 'must be later than the current scheduled date')
  const values = f.done()
  const days = daysBetween(c.scheduled_date, next)
  Object.assign(c, { delay_days: (c.delay_days ?? 0) + days, delay_reason: values.delay_reason, scheduled_date: next, status: 'delayed' })
  if (moveAppointments) moveCycleAppointments(c.id, user.id, days)
  sync(c.patient_id)
  return { data: cyclePayload(c, user) }
}

// ------------------------------------------------------------------ medication records
/**
 * Idempotency-Key (= app/core/idempotency.py): the same key with the same body returns the
 * record created the first time (or the same 4xx error again); the same key with another body → 422.
 */
function idempotent(key, body, create) {
  if (!key) return create()
  state.idempotency ??= {}
  const user = mockCurrentUser()
  const slot = `${user.id}:${key}`
  const hash = JSON.stringify(body, Object.keys(body).sort())
  const seen = state.idempotency[slot]
  if (seen) {
    if (seen.hash !== hash) throw new MockApiError(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    if (seen.error) throw new MockApiError(seen.error.status, seen.error.code, 'This request already failed; send a new request')
    return { ...medicationResponse(seen.id, user), replayed: true }
  }
  try {
    const result = create()
    state.idempotency[slot] = { hash, id: result.data.id }
    save()
    return result
  } catch (e) {
    if (e instanceof MockApiError && e.status < 500) {
      state.idempotency[slot] = { hash, error: { status: e.status, code: e.code } }
      save()
    }
    throw e
  }
}
const medicationResponse = (id, user) => ({ data: medicationPayload(medication(id), user) })
export function mockListCycleMedications(cycleId) {
  const viewer = mockCurrentUser()
  const c = cycle(cycleId)
  return { data: meds((m) => m.cycle_id === c.id, viewer).map((m) => medicationPayload(m, viewer)) }
}
export function mockListPatientMedications(patientId) {
  const viewer = mockCurrentUser()
  const pid = mockAccessPatient(patientId).id
  return { data: meds((m) => m.patient_id === pid, viewer).map((m) => medicationPayload(m, viewer)) }
}
function meds(filter, viewer) {
  return state.medications
    .filter((m) => filter(m) && (isStaff(viewer) || m.record_status === 'final'))
    .sort((a, b) => Date.parse(b.administered_at) - Date.parse(a.administered_at) || b.id - a.id)
}
function medicationValues(body, c, base = null) {
  const merged = { ...(base ? Object.fromEntries(MED_FIELDS.map((k) => [k, base[k]])) : {}), ...Object.fromEntries(Object.entries(body).filter(([k]) => MED_FIELDS.includes(k))) }
  const f = new Fields(merged, '給藥紀錄內容有誤')
  const drug = Number.isInteger(merged.drug_id) ? drugById(merged.drug_id) : null
  if (!drug || (!drug.is_active && drug.id !== base?.drug_id)) f.error('drug_id', 'must be an active drug id')
  f.choice('medication_type', MED_TYPES, { required: true })
  f.number('dose_value', 0.01, 100000, { required: true })
  f.text('dose_unit', 20, { required: true })
  f.choice('route', ROUTES, { allowNull: true })
  const raw = merged.administered_at
  const t = typeof raw === 'string' && /[zZ]|[+-]\d{2}:\d{2}$/.test(raw) ? Date.parse(raw) : NaN
  if (typeof raw !== 'string') f.error('administered_at', 'is required (ISO 8601 datetime with timezone)')
  else if (Number.isNaN(t)) f.error('administered_at', 'must be an ISO 8601 datetime with timezone')
  else {
    const at = new Date(t).toISOString()
    const day = localDay(at)
    if (t > Date.now() + 5 * 60000) f.error('administered_at', 'cannot be in the future')
    else if (day < c.actual_start_date || (c.actual_end_date && day > c.actual_end_date)) f.error('administered_at', 'must be within this cycle (from its start to its end)')
    f.values.administered_at = at
  }
  f.integer('infusion_duration_min', 1, 1440)
  f.choice('administration_status', ADMIN_STATUS, { required: true })
  f.text('reaction_notes', 2000)
  const values = f.done()
  values.drug_id = drug.id
  if (values.route == null && !('route' in merged)) values.route = drug.default_route
  return values
}
export function mockCreateMedication(cycleId, body, idempotencyKey = null) {
  return idempotent(idempotencyKey, body, () => createMedication(cycleId, body))
}
function createMedication(cycleId, body) {
  const user = mockCurrentUser('nurse')
  const c = cycle(cycleId)
  if (!STARTED.includes(c.status) || !c.actual_start_date) throw conflict('CYCLE_NOT_STARTED', '這個 Cycle 還沒開始，請先開始 Cycle 再登錄給藥')
  const extra = Object.keys(body).filter((k) => !MED_FIELDS.includes(k)).sort()
  if (extra.length) throw invalid(extra.map((k) => ({ field: k, issue: 'is not a supported field' })), '給藥紀錄內容有誤')
  const values = medicationValues(body, c)
  const m = { id: nextId(), patient_id: c.patient_id, cycle_id: c.id, route: null, infusion_duration_min: null, reaction_notes: null, ...values,
    cycle_day: cycleDayOn(c, localDay(values.administered_at)), source: 'nurse', record_status: 'final', amends_id: null,
    administered_by: user.id, created_at: nowIso() }
  state.medications.push(m)
  save()
  return { data: medicationPayload(m, user) }
}
function ensureFinal(m) {
  if (m.record_status !== 'final') throw conflict('INVALID_STATE', '這筆紀錄已被更正或標示為錯誤，請對最新的紀錄操作')
}
export function mockAmendMedication(id, body, idempotencyKey = null) {
  return idempotent(idempotencyKey, body, () => amendMedication(id, body))
}
function amendMedication(id, body) {
  const user = mockCurrentUser('nurse')
  const orig = medication(id)
  ensureFinal(orig)
  const f = new Fields(body, '更正內容有誤')
  f.text('amend_reason', 500, { required: true })
  f.unknown(['amend_reason', ...MED_FIELDS])
  f.done()
  const c = cycle(orig.cycle_id)
  const { amend_reason: _r, ...rest } = body
  const values = medicationValues(rest, c, orig)
  if (Object.entries(values).every(([k, v]) => orig[k] === v)) throw invalid([{ field: 'amend_reason', issue: 'nothing changed: send the corrected fields' }], '更正內容有誤')
  const m = { id: nextId(), patient_id: orig.patient_id, cycle_id: c.id, ...values, cycle_day: cycleDayOn(c, localDay(values.administered_at)),
    source: 'nurse', record_status: 'final', amends_id: orig.id, administered_by: orig.administered_by, created_at: nowIso() }
  orig.record_status = 'amended'
  state.medications.push(m)
  save()
  return { data: medicationPayload(m, user) }
}
export function mockMarkMedicationError(id, body = {}) {
  const user = mockCurrentUser('nurse')
  const m = medication(id)
  ensureFinal(m)
  const f = new Fields(body, '標示錯誤資料有誤')
  f.text('reason', 500, { required: true })
  f.unknown(['reason'])
  f.done()
  m.record_status = 'entered_in_error'
  save()
  return { data: medicationPayload(m, user) }
}

// ------------------------------------------------------------------ for the other mock modules
/** Started cycles of a patient, oldest first (mock timeline: cycle events and cycle context). */
export function mockTimelineCycles(patientId) {
  return state.cycles
    .filter((c) => c.patient_id === patientId && c.actual_start_date)
    .sort((a, b) => a.actual_start_date.localeCompare(b.actual_start_date))
    .map((c) => {
      const p = state.plans.find((x) => x.id === c.plan_id)
      return { id: c.id, number: c.cycle_number, start: c.actual_start_date, end: c.actual_end_date, status: c.status,
        scheduled: c.scheduled_date, regimen: p.regimen_id ? regimenById(p.regimen_id).name : null, total: p.total_cycles }
    })
}
/** Final medication records of a patient, for the mock timeline (staff fields included; the timeline filters them). */
export function mockTimelineMedications(patientId) {
  return state.medications
    .filter((m) => m.patient_id === patientId && m.record_status === 'final')
    .map((m) => ({ ...clone(m), drug: drugById(m.drug_id).generic_name, administrator: mockPerson(m.administered_by) }))
}
/** The current cycle's context for a new mock observation (like active_plan_and_cycle). */
export function mockCurrentCycle(patientId, day = localToday()) {
  const c = state.cycles.find((x) => x.patient_id === patientId && x.status === 'in_progress'
    && OPEN_PLAN.includes(state.plans.find((p) => p.id === x.plan_id)?.status))
  return c ? { cycle_id: c.id, cycle_day: cycleDayOn(c, day), in_nadir: inNadir(c, day)[0] } : null
}

/**
 * After a chemo write: the dashboard's treatment-progress widget and the caseload row are
 * derived again from this state (the API derives them on every request).
 */
function sync(patientId) {
  save()
  const w = patientDashboards[patientId]?.widgets
  const open = state.plans
    .filter((p) => p.patient_id === patientId && OPEN_PLAN.includes(p.status))
    .sort((a, b) => (a.status !== 'active') - (b.status !== 'active') || b.start_date.localeCompare(a.start_date))[0]
  const today = localToday()
  const cycles = open ? cyclesOf(open.id) : []
  const current = cycles.find((c) => c.status === 'in_progress')
  if (w) {
    if (!open) w['treatment-progress'] = null
    else {
      const [nadirNow, , nadirEnd] = current ? inNadir(current, today) : [false, null, null]
      const completed = cycles.filter((c) => c.status === 'completed').length
      const ref = current ? current.cycle_number : completed
      const upcoming = cycles.filter((c) => c.cycle_number > ref && NOT_STARTED.includes(c.status))
      const regimen = open.regimen_id ? regimenById(open.regimen_id) : null
      let next = upcoming.length ? upcoming[0].scheduled_date : null
      let estimated = false
      if (!next && current?.actual_start_date && regimen?.cycle_length_days && (open.total_cycles ?? 0) > current.cycle_number) {
        next = addDays(current.actual_start_date, regimen.cycle_length_days)
        estimated = true
      }
      const dx = mockAccessPatient(patientId).diagnoses.find((d) => d.id === open.diagnosis_id)
      w['treatment-progress'] = {
        plan_id: open.id, diagnosis_name: dx?.cancer_type.name_zh ?? null, regimen_name: regimen?.name ?? open.plan_name,
        attending_physician_name: open.attending_physician_name, completed_cycles: completed, total_cycles: open.total_cycles,
        current_cycle: current ? { cycle_number: current.cycle_number, cycle_day: cycleDayOn(current, today), in_nadir: nadirNow, nadir_end_date: nadirEnd } : null,
        next_cycle_date: next, next_cycle_date_estimated: estimated, disclaimer_key: 'treatment_progress_disclaimer',
      }
    }
  }
  const row = nurseOverview.caseload.data.find((r) => r.patient_id === patientId)
  if (row) row.cycle = current ? { cycle_number: current.cycle_number, cycle_day: cycleDayOn(current, today) } : null
}

/** Test / demo helper: forget every change (fresh seed). */
export function resetMockChemotherapy() {
  Object.assign(state, seed())
  save()
}

