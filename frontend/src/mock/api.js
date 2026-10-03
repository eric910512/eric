/**
 * Everything the stores use in mock mode (VITE_USE_MOCK=true). Loaded only through
 * `loadMock()` in `@/api/client`, so API builds contain none of it.
 *
 * Patient-scoped handlers are wrapped with the mock access control (`@/mock/patients`), the
 * same rule as the backend: admin every patient, a patient only themselves, a nurse only
 * currently assigned patients — anything else fails with 404 NOT_FOUND.
 */
import * as admin from '@/mock/admin'
import * as rules from '@/mock/alertRules'
import * as appts from '@/mock/appointments'
import * as chemo from '@/mock/chemotherapy'
import * as labs from '@/mock/labs'
import * as nursing from '@/mock/nursing'
import * as recs from '@/mock/records'
import * as profile from '@/mock/profile'
import * as reminders from '@/mock/reminders'
import * as review from '@/mock/nurseReview'
import { patientDashboards } from '@/mock/patientDashboards'
import * as care from '@/mock/patients'
import { mockCurrentUser, mockEnsureCanView, mockNurseOverview as careTeamOverview, mockVisiblePatient, MockApiError } from '@/mock/patients'
import * as symptoms from '@/mock/symptoms'
import * as timeline from '@/mock/timeline'
import * as vitals from '@/mock/vitals'

/** Wrap `fn(patientId, …)` so it runs only for a patient the signed-in user may access. */
const scoped = (fn) => (patientId, ...rest) => fn(mockEnsureCanView(patientId), ...rest)
/** Same, for handlers keyed by another id (notification, record) → its patient. */
const scopedBy = (patientOf, fn, what) => (id, ...rest) => {
  const patientId = patientOf(id)
  if (!patientId) throw new MockApiError(404, 'NOT_FOUND', `找不到${what}`)
  mockEnsureCanView(patientId)
  return fn(id, ...rest)
}

export { mockDashboardLayout, mockPublicSettings } from '@/mock/config'

// chemotherapy: master data (role checks inside), plan / cycle / medication by the patient they belong to
export {
  mockCreateDrug, mockCreatePlan, mockCreateRegimen, mockListDrugs, mockListPatientMedications, mockListPlans, mockListRegimens,
  mockUpdateDrug, mockUpdateRegimen,
} from '@/mock/chemotherapy'
const byPlan = (fn) => scopedBy(chemo.mockPlanPatient, fn, '療程')
const byCycle = (fn) => scopedBy(chemo.mockCyclePatient, fn, 'Cycle')
const byMedication = (fn) => scopedBy(chemo.mockMedicationPatient, fn, '給藥紀錄')
export const mockGetPlan = byPlan(chemo.mockGetPlan)
export const mockUpdatePlan = byPlan(chemo.mockUpdatePlan)
export const mockDiscontinuePlan = byPlan(chemo.mockDiscontinuePlan)
export const mockListCycles = byPlan(chemo.mockListCycles)
export const mockAddCycle = byPlan(chemo.mockAddCycle)
export const mockGetCycle = byCycle(chemo.mockGetCycle)
export const mockUpdateCycle = byCycle(chemo.mockUpdateCycle)
export const mockStartCycle = byCycle(chemo.mockStartCycle)
export const mockCompleteCycle = byCycle(chemo.mockCompleteCycle)
export const mockDelayCycle = byCycle(chemo.mockDelayCycle)
export const mockListCycleMedications = byCycle(chemo.mockListCycleMedications)
export const mockCreateMedication = byCycle(chemo.mockCreateMedication)
export const mockAmendMedication = byMedication(chemo.mockAmendMedication)
export const mockMarkMedicationError = byMedication(chemo.mockMarkMedicationError)

// treatment schedule
export { mockCreateAppointment, mockListAppointments } from '@/mock/appointments'
const byAppointment = (fn) => scopedBy(appts.mockAppointmentPatient, fn, '行程')
export const mockGetAppointment = byAppointment(appts.mockGetAppointment)
export const mockUpdateAppointment = byAppointment(appts.mockUpdateAppointment)
export const mockCheckInAppointment = byAppointment(appts.mockCheckInAppointment)
export const mockCompleteAppointment = byAppointment(appts.mockCompleteAppointment)
export const mockCancelAppointment = byAppointment(appts.mockCancelAppointment)
export const mockRescheduleAppointment = byAppointment(appts.mockRescheduleAppointment)
export const mockTodayAppointments = () => appts.mockTodayAppointments(mockVisiblePatient)
// nursing assessments (staff only; by the patient they belong to)
export const mockListAssessments = (patientId = null, opts = {}) => nursing.mockListAssessments(patientId, opts, mockVisiblePatient)
export { mockCreateAssessment } from '@/mock/nursing'
const byAssessment = (fn) => scopedBy(nursing.mockAssessmentPatient, fn, '護理評估')
export const mockGetAssessment = byAssessment(nursing.mockGetAssessment)
export const mockAssessmentVersions = byAssessment(nursing.mockAssessmentVersions)
export const mockUpdateAssessment = byAssessment(nursing.mockUpdateAssessment)
export const mockSignAssessment = byAssessment(nursing.mockSignAssessment)
export const mockAmendAssessment = byAssessment(nursing.mockAmendAssessment)
export const mockUpdateAssessmentItem = byAssessment(nursing.mockUpdateAssessmentItem)
// corrections (nurse) and review queues
const bySymptom = (fn) => scopedBy(recs.mockSymptomPatient, fn, '症狀紀錄')
const byVital = (fn) => scopedBy(recs.mockVitalPatient, fn, '生命徵象')
const byLab = (fn) => scopedBy(recs.mockLabPatient, fn, '檢驗結果')
export const mockAmendSymptom = bySymptom(recs.mockAmendSymptom)
export const mockMarkSymptomError = bySymptom(recs.mockMarkSymptomError)
export const mockSymptomHistory = bySymptom(recs.mockSymptomHistory)
export const mockAmendVital = byVital(recs.mockAmendVital)
export const mockMarkVitalError = byVital(recs.mockMarkVitalError)
export const mockVitalHistory = byVital(recs.mockVitalHistory)
export const mockAmendLab = byLab(recs.mockAmendLab)
export const mockMarkLabError = byLab(recs.mockMarkLabError)
export const mockLabRecordHistory = byLab(recs.mockLabRecordHistory)
export const mockListPatientVitals = scoped(recs.mockListPatientVitals)
export const mockPendingReviews = () => recs.mockPendingReviews(mockVisiblePatient)
export const mockAbnormalLabs = (opts = {}) => recs.mockAbnormalLabs(mockVisiblePatient, opts)
/** Nurse overview: caseload (care team) + today's appointments of the same patients. */
export const mockNurseOverview = () => ({ ...careTeamOverview(), todayAppointments: appts.mockTodayAppointments(mockVisiblePatient).data })
export const mockLabHistory = scoped(labs.mockLabHistory)
export const mockLabSummary = scoped(labs.mockLabSummary)
export const submitMockLabs = scoped(labs.submitMockLabs)
export const mockGetNotification = scopedBy(review.mockNotificationPatient, review.mockGetNotification, '通知')
export const mockTransition = scopedBy(review.mockNotificationPatient, review.mockTransition, '通知')
export const mockMarkRead = scopedBy(review.mockNotificationPatient, review.mockMarkRead, '通知')
export const mockResolve = scopedBy(review.mockNotificationPatient, review.mockResolve, '通知')
export const mockReview = scopedBy(review.mockRecordPatient, review.mockReview, '症狀紀錄')
/** Symptom records; a patient gets the patient view (no reviewer, review note or who resolved alerts), like the API. */
export const mockListRecords = scoped((patientId, opts) => {
  const res = review.mockListRecords(patientId, opts)
  if (mockCurrentUser().role !== 'patient') return res
  return { ...res, data: res.data.map((r) => ({ ...r, reviewed_by: null, review: null, alerts: (r.alerts ?? []).map((a) => ({ ...a, resolved_by: null })) })) }
})
export const mockListNotifications = (opts = {}) => review.mockListNotifications({ ...opts, visible: mockVisiblePatient })
// manual / scheduled reminders (Sprint 8): nurse for assigned patients, admin; lifecycle through mockTransition
export const mockCreateReminder = reminders.mockCreateReminder
const staffOnly = (fn) => (...args) => {
  mockCurrentUser('nurse', 'admin') // role first (403), then the patient scope (404), like the API
  return fn(...args)
}
export const mockScheduledReminders = staffOnly(scoped(review.mockScheduledReminders))
export const mockListReminders = staffOnly(scoped((patientId, opts = {}) =>
  review.mockListNotifications({ ...opts, patientId, type: 'reminder', visible: mockVisiblePatient })))
// patient basic data + contact email (profile sprint): reads scoped like every patient handler; writes check the role first (403)
export const mockGetProfile = profile.mockGetProfile
export const mockPatientWeights = profile.mockPatientWeights
export const mockUpdateProfile = profile.mockUpdateProfile
export const mockRequestEmailVerification = profile.mockRequestEmailVerification
export const mockConfirmEmailVerification = profile.mockConfirmEmailVerification
export { mockEmailOutbox } from '@/mock/email'
export const mockListAbnormal = (opts = {}) => review.mockListAbnormal({ ...opts, visible: mockVisiblePatient })
export const mockTimeline = scoped(timeline.mockTimeline)
export const submitMockReport = scoped(symptoms.submitMockReport)
export const submitMockVitals = scoped(vitals.submitMockVitals)
export const { mockForms } = symptoms
export const { mockReferenceRanges } = vitals

/** GET /dashboard/patient/{id|me} */
export const mockPatientDashboard = scoped((patientId) => {
  reminders.deliverDueReminders(patientId)
  const found = patientDashboards[patientId]
  if (!found) throw new Error('找不到這位病人的示範資料') // P00003 / P00004: caseload rows only
  return { ...found, widgets: { ...found.widgets, 'lab-summary': labs.mockLabSummary(patientId) } }
})

export {
  mockAccessPatient, mockAuthenticate, mockChangePassword, mockEndOtherSessions, mockEndSession, mockListSessions, mockLogout, mockRefresh, mockSignIn,
  mockUserSessions, mockCreateAccount, mockCreateAssignment, mockCreateCareAlert,
  mockCreateDiagnosis, mockCreatePatient, mockEndAssignment, mockGetPatient, mockListAssignments,
  mockListCancerTypes, mockListCareAlerts, mockListDiagnoses, mockListPatients, mockListStaff, mockMe,
  mockUpdateCareAlert, mockUpdateDiagnosis, mockUpdatePatient, resetMockCareTeam, setMockViewer,
} from '@/mock/patients'
export { MOCK_PASSWORD, mockUsers } from '@/mock/users'
export { mockMyMarkRead, mockMyNotification, mockMyNotifications, mockMyReadAll, mockMyUnreadCount } from '@/mock/patientNotifications'

// admin console: every change is audited (the mock audit log records the admin console's own actions)
export { mockAdminOverview, mockAdminSettings, mockListForms, mockSearchAudit } from '@/mock/admin'
export { mockGetRule, mockListRules } from '@/mock/alertRules'
export function mockCreateNurse(body) {
  const res = care.mockCreateNurse(body)
  admin.mockAudit('CREATE', 'users', res.data.id, { role: res.data.role, temporary_password_issued: true })
  return res
}
export function mockUpdateAccount(id, body) {
  const { data, changed } = care.mockUpdateAccount(id, body)
  if (changed.length) admin.mockAudit('UPDATE', 'users', id, { fields: changed, ...(changed.includes('is_active') ? { is_active: data.is_active } : {}) })
  return { data }
}
export function mockCreateRule(body) {
  const res = rules.mockCreateRule(body)
  admin.mockAudit('CREATE', 'alert_rules', res.data.id, { code: res.data.code, rule: res.data })
  return res
}
export function mockUpdateRule(id, body) {
  const before = rules.mockGetRule(id).data
  const { data, changed } = rules.mockUpdateRule(id, body)
  if (changed.length) {
    admin.mockAudit('UPDATE', 'alert_rules', id, { code: data.code, fields: changed,
      old: Object.fromEntries(changed.map((k) => [k, before[k]])), new: Object.fromEntries(changed.map((k) => [k, data[k]])) })
  }
  return { data }
}
export const mockTestRule = rules.mockTestRule
export const mockUpdateForm = admin.mockUpdateForm
export function mockRevokeSessions(id) {
  const res = care.mockRevokeSessions(id)
  admin.mockAudit('UPDATE', 'users', id, { sessions_revoked: res.data.revoked, forced: true })
  return res
}
export function mockResetPassword(id) {
  const res = care.mockResetPassword(id)
  admin.mockAudit('UPDATE', 'users', id, { password: 'reset', temporary_password_issued: true, sessions_revoked: res.data.sessions_revoked })
  return res
}
