/**
 * Component Registry (ui-architecture.md §4.4): widget_code from the Layout JSON → Vue component.
 * `props(ctx)` maps the dashboard payload to the component's props.
 * Unknown widget codes render a placeholder instead of breaking the page.
 */
import LabSummary from '@/components/LabSummary.vue'
import LatestVitals from '@/components/LatestVitals.vue'
import NotificationCard from '@/components/NotificationCard.vue'
import PatientSummary from '@/components/PatientSummary.vue'
import RiskSummary from '@/components/RiskSummary.vue'
import SymptomQuickReport from '@/components/SymptomQuickReport.vue'
import SymptomTrend from '@/components/SymptomTrend.vue'
import TodaySchedule from '@/components/TodaySchedule.vue'
import TreatmentProgress from '@/components/TreatmentProgress.vue'

export const widgetRegistry = {
  'patient-summary': {
    component: PatientSummary,
    props: (ctx) => ({ summary: ctx.widgets['patient-summary'] }),
  },
  'risk-summary': {
    component: RiskSummary,
    props: (ctx) => ({
      summary: ctx.widgets['risk-summary'],
      hotline: ctx.widgets['today-schedule']?.quick_contact ?? null,
      timezone: ctx.timezone,
    }),
  },
  'today-schedule': {
    component: TodaySchedule,
    props: (ctx) => ({ schedule: ctx.widgets['today-schedule'], timezone: ctx.timezone }),
  },
  'treatment-progress': {
    component: TreatmentProgress,
    props: (ctx) => ({
      progress: ctx.widgets['treatment-progress'],
      cycleMarkers: ctx.widgets['symptom-trend']?.cycle_markers ?? [],
      today: ctx.today,
      disclaimer: ctx.settings?.disclaimers?.treatment_progress_disclaimer ?? '',
      detailsTo: { name: 'patient-treatment' },
    }),
  },
  'symptom-quick-report': {
    component: SymptomQuickReport,
    props: (ctx) => ({
      report: ctx.widgets['symptom-quick-report'],
      today: ctx.today,
      timezone: ctx.timezone,
      patientId: ctx.patientId,
      hotline: ctx.widgets['today-schedule']?.quick_contact ?? null,
    }),
  },
  'latest-vitals': {
    component: LatestVitals,
    props: (ctx) => ({
      vitals: ctx.widgets['latest-vitals'],
      today: ctx.today,
      timezone: ctx.timezone,
      entryRoute: { name: 'patient-vitals' },
    }),
  },
  'lab-summary': {
    component: LabSummary,
    props: (ctx) => ({ summary: ctx.widgets['lab-summary'] ?? null, today: ctx.today, timezone: ctx.timezone }),
  },
  'symptom-trend': {
    component: SymptomTrend,
    props: (ctx) => ({ trend: ctx.widgets['symptom-trend'], title: '近兩週症狀' }),
  },
  'my-notifications': {
    component: NotificationCard,
    props: (ctx) => ({ notifications: ctx.widgets.notifications, today: ctx.today, timezone: ctx.timezone }),
  },
}
