<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import LabPanel from '@/components/LabPanel.vue'
import LatestVitals from '@/components/LatestVitals.vue'
import NurseNav from '@/components/NurseNav.vue'
import NurseNotificationList from '@/components/NurseNotificationList.vue'
import PatientSummary from '@/components/PatientSummary.vue'
import PatientTimeline from '@/components/PatientTimeline.vue'
import SymptomReviewPanel from '@/components/SymptomReviewPanel.vue'
import SymptomTrend from '@/components/SymptomTrend.vue'
import TreatmentProgress from '@/components/TreatmentProgress.vue'
import VitalAbnormalList from '@/components/VitalAbnormalList.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { formatTime, formatWhen, localDate } from '@/utils/format'

/** Nurse overview + single-patient panel (ClinicalDesktopShell, ui-architecture.md §1.3, §2.3). */
const store = useDashboardStore()
const route = useRoute()
const router = useRouter()

const TZ = 'Asia/Taipei'
const RISK = {
  high: { label: '高風險', dot: 'bg-critical', text: 'text-critical', soft: 'bg-critical-soft' },
  medium: { label: '中風險', dot: 'bg-warn', text: 'text-warn', soft: 'bg-warn-soft' },
  low: { label: '低風險', dot: 'bg-ok', text: 'text-ok', soft: 'bg-ok-soft' },
}
const ALERT_TYPE = { allergy: '過敏', limb_restriction: '肢體限制', fall_risk: '跌倒風險', isolation: '隔離' }

const overview = computed(() => store.nurseOverview)
const caseload = computed(() => overview.value?.caseload.data ?? [])
const selectedId = computed(() => route.params.patientId || caseload.value[0]?.patient_id)
const patient = computed(() => store.patients[selectedId.value])
const patientError = computed(() => store.errors[`patient:${selectedId.value}`])
const nv = computed(() => patient.value?.widgets['nurse-view'])
const today = computed(() => patient.value?.widgets['today-schedule'].date ?? localDate(new Date().toISOString(), TZ))

const riskBar = computed(() => {
  const m = overview.value?.caseload.meta
  if (!m?.total) return []
  return ['high', 'medium', 'low'].map((k) => ({ key: k, count: m[k], pct: (m[k] / m.total) * 100 }))
})
const alertTotal = computed(() => caseload.value.reduce((n, p) => n + p.unacknowledged_alert_count, 0))

function select(id) {
  router.push({ name: 'nurse', params: { patientId: id } })
}

onMounted(() => {
  store.fetchSettings()
  store.fetchNurseOverview()
})
watch(selectedId, (id) => id && store.fetchPatientDashboard(id), { immediate: true })
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="nurse" :badge="alertTotal" />

    <main class="min-w-0 p-4 lg:p-6">
      <!-- Overview strip -->
      <section v-if="overview" class="rounded-2xl border border-line bg-surface p-5" aria-label="照護總覽">
        <div class="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 class="text-2xl font-bold">今天的照護重點</h1>
            <p class="text-ink-soft">負責 {{ overview.caseload.meta.total }} 位病人</p>
          </div>
          <RouterLink
            :to="{ name: 'nurse-notifications' }"
            class="inline-flex min-h-11 items-center gap-2 rounded-full px-4 py-2 font-bold"
            :class="alertTotal ? 'bg-critical-soft text-critical hover:bg-critical/15' : 'bg-mist text-ink-soft hover:text-ink'"
          >
            <AppIcon :name="alertTotal ? 'alert' : 'bell'" :size="20" /> {{ alertTotal ? `${alertTotal} 則警示待處理` : '通知中心' }}
          </RouterLink>
        </div>

        <div class="mt-4 flex h-3 overflow-hidden rounded-full" role="img"
             :aria-label="riskBar.map((r) => `${RISK[r.key].label} ${r.count} 位`).join('，')">
          <span v-for="r in riskBar" :key="r.key" :class="RISK[r.key].dot" :style="{ width: `${r.pct}%` }" />
        </div>
        <ul class="mt-2 flex flex-wrap gap-x-6 gap-y-1">
          <li v-for="r in riskBar" :key="r.key" class="inline-flex items-center gap-2">
            <i class="size-2.5 rounded-full" :class="RISK[r.key].dot" />{{ RISK[r.key].label }} <strong>{{ r.count }}</strong>
          </li>
        </ul>

        <div v-if="overview.todayAppointments.length" class="mt-4 border-t border-line pt-3">
          <p class="text-sm text-ink-soft">今日行程</p>
          <ul class="mt-1 flex flex-wrap gap-x-6 gap-y-1">
            <li v-for="a in overview.todayAppointments" :key="a.id">
              <strong>{{ formatTime(a.scheduled_at, TZ) }}</strong> {{ a.display_name }}（{{ a.patient_code }}）{{ a.title }}
            </li>
          </ul>
        </div>
      </section>

      <div class="mt-4 grid gap-4 2xl:grid-cols-2">
        <NurseNotificationList :today="today" :timezone="TZ" :limit="4" @select-patient="select" />
        <VitalAbnormalList :today="today" :timezone="TZ" @select-patient="select" />
      </div>

      <div class="mt-4 grid gap-4 xl:grid-cols-[22rem_1fr]">
        <!-- Caseload -->
        <section class="self-start rounded-2xl border border-line bg-surface" aria-labelledby="caseload-title">
          <div class="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
            <h2 id="caseload-title" class="text-lg font-bold">我的個案</h2>
            <label class="ml-auto flex items-center gap-2 text-sm text-ink-soft">
              排序
              <select
                :value="store.caseloadSort"
                class="min-h-9 rounded-lg border border-line bg-surface px-2 text-ink"
                aria-label="個案排序"
                @change="store.fetchNurseOverview($event.target.value)"
              >
                <option value="risk">高風險優先</option>
                <option value="last_report">最久未回報</option>
                <option value="name">病歷代碼</option>
              </select>
            </label>
          </div>
          <p v-if="overview && !caseload.length" class="p-5 text-ink-soft">目前沒有指派給您的病人。</p>
          <ul v-else-if="caseload.length" class="divide-y divide-line">
            <li v-for="p in caseload" :key="p.patient_id">
              <button
                type="button"
                class="flex w-full gap-3 px-5 py-3 text-left hover:bg-mist"
                :class="p.patient_id === selectedId ? 'bg-care-soft' : ''"
                :aria-pressed="p.patient_id === selectedId"
                @click="select(p.patient_id)"
              >
                <span class="flex w-7 shrink-0 flex-col items-center gap-1 pt-0.5" :aria-label="`第 ${p.priority_rank} 位`">
                  <span class="text-sm font-bold text-ink-soft">{{ p.priority_rank }}</span>
                  <i class="size-2.5 rounded-full" :class="RISK[p.risk_level].dot" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="flex items-baseline justify-between gap-2">
                    <span class="font-bold">{{ p.display_name }}</span>
                    <span class="text-sm text-ink-soft">{{ p.patient_code }}</span>
                  </span>
                  <span class="block text-sm text-ink-soft">
                    <template v-if="p.cycle">第 {{ p.cycle.cycle_number }} 次療程第 {{ p.cycle.cycle_day }} 天，</template>
                    <template v-else>目前沒有進行中的療程，</template>
                    {{ p.hours_since_last_report != null ? `${p.hours_since_last_report} 小時前回報` : '尚無回報' }}
                  </span>
                  <span v-if="p.risk_reasons.length" class="mt-1 block text-sm" :class="RISK[p.risk_level].text">
                    {{ p.risk_reasons.join('、') }}
                  </span>
                  <span v-if="p.unacknowledged_alert_count || p.pending_review_count || p.care_alert_types.length" class="mt-1 flex flex-wrap gap-1.5 text-xs">
                    <span v-if="p.unacknowledged_critical_count" class="rounded-full bg-critical px-2 py-0.5 font-bold text-white">危急警示 {{ p.unacknowledged_critical_count }}</span>
                    <span v-else-if="p.unacknowledged_alert_count" class="rounded-full bg-warn-soft px-2 py-0.5 font-bold text-warn">警示 {{ p.unacknowledged_alert_count }}</span>
                    <span v-if="p.pending_review_count" class="rounded-full bg-mist px-2 py-0.5">待審 {{ p.pending_review_count }}</span>
                    <span v-for="t in p.care_alert_types" :key="t" class="rounded-full bg-warn-soft px-2 py-0.5 text-warn">{{ ALERT_TYPE[t] ?? t }}</span>
                  </span>
                </span>
              </button>
            </li>
          </ul>
          <div v-else class="space-y-3 p-5" aria-busy="true">
            <div v-for="n in 3" :key="n" class="h-14 animate-pulse rounded bg-mist" />
          </div>
        </section>

        <!-- Selected patient -->
        <div class="min-w-0 space-y-4">
          <div v-if="patientError" class="rounded-2xl border border-line bg-surface p-5">
            <p class="font-bold">{{ patientError }}</p>
            <p v-if="USE_MOCK" class="mt-1 text-ink-soft">示範模式的完整資料只有 P00001 和 P00002，請從左側選擇。</p>
          </div>

          <template v-else-if="patient && nv">
            <PatientSummary :summary="patient.widgets['patient-summary']" variant="banner" />

            <section
              class="rounded-2xl border p-5"
              :class="[RISK[nv.risk.level].soft, nv.risk.level === 'high' ? 'border-critical/50' : 'border-line']"
              aria-label="風險判斷"
            >
              <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
                <p class="text-xl font-bold" :class="RISK[nv.risk.level].text">{{ RISK[nv.risk.level].label }}</p>
                <p class="text-ink-soft">
                  最後回報 {{ nv.last_report_at ? formatWhen(nv.last_report_at, today, TZ) : '—' }}
                  <span v-if="nv.care_team[0]">，主責 {{ nv.care_team[0].display_name }}</span>
                </p>
              </div>
              <ul v-if="nv.risk.reasons.length" class="mt-2 list-disc space-y-0.5 pl-5">
                <li v-for="r in nv.risk.reasons" :key="r">{{ r }}</li>
              </ul>
              <p v-else class="mt-1">目前沒有需要注意的異常。</p>
            </section>

            <NurseNotificationList
              v-if="nv.unacknowledged_alerts.count"
              :patient-id="selectedId"
              :today="today"
              :timezone="TZ"
              title="這位病人的未處理警示"
            />

            <div class="grid gap-4 2xl:grid-cols-2">
              <TreatmentProgress
                :progress="patient.widgets['treatment-progress']"
                :cycle-markers="patient.widgets['symptom-trend'].cycle_markers"
                :today="today"
              />
              <LatestVitals :vitals="patient.widgets['latest-vitals']" :today="today" :timezone="TZ" />
            </div>

            <LabPanel :patient-id="selectedId" :today="today" :timezone="TZ" />

            <SymptomTrend :trend="patient.widgets['symptom-trend']" />

            <SymptomReviewPanel :patient-id="selectedId" :today="today" :timezone="TZ" />

            <PatientTimeline :patient-id="selectedId" audience="staff" :timezone="TZ" title="病人照護時間軸" />
          </template>

          <div v-else class="space-y-4" aria-busy="true" aria-label="載入中">
            <div v-for="n in 3" :key="n" class="h-32 animate-pulse rounded-2xl bg-surface" />
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
