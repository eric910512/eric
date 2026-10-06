<script setup>
import { computed, onMounted, watch } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import DailySelfCareReport from '@/components/DailySelfCareReport.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import SymptomQuickReport from '@/components/SymptomQuickReport.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { formatTime, localDate } from '@/utils/format'

/** Patient: 症狀回報 (/patient/symptoms) — today's report, 每日症狀與自我照護回報 and my earlier reports. */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const portal = usePatientPortalStore()
const TZ = 'Asia/Taipei'
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const widgets = computed(() => dashboard.patients[patientId.value]?.widgets)
const today = computed(() => widgets.value?.['today-schedule']?.date ?? localDate(new Date().toISOString(), TZ))
const unread = computed(() => widgets.value?.notifications?.unread_count ?? 0)
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
const values = (r) => r.values.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}`
  : v.value_boolean !== undefined && v.value_boolean !== null ? (v.value_boolean ? `${v.label}：有` : null) : `${v.label} ${v.score ?? v.value_numeric}`)).filter(Boolean).join('、')

onMounted(() => {
  if (!widgets.value) dashboard.fetchPatientDashboard(patientId.value)
  portal.fetchRecords(patientId.value)
})
// a new report shows up in the list
watch(() => widgets.value?.['symptom-quick-report']?.today_record_id, (id, old) => id && id !== old && portal.fetchRecords(patientId.value))
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">症狀回報</h1>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4" data-patient-symptoms>
      <SymptomQuickReport
        v-if="widgets?.['symptom-quick-report']"
        :report="widgets['symptom-quick-report']"
        :today="today"
        :timezone="TZ"
        :patient-id="patientId"
        :hotline="widgets['today-schedule']?.quick_contact ?? null"
      />
      <div v-else class="h-32 animate-pulse rounded-2xl bg-surface" aria-busy="true" />

      <DailySelfCareReport :patient-id="patientId" :timezone="TZ" @submitted="portal.fetchRecords(patientId)" />

      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="my-reports" data-my-reports :data-loaded="!!portal.records">
        <h2 id="my-reports" class="text-lg font-bold">我的回報</h2>
        <p v-if="portal.errors.records" class="mt-2 text-critical" role="alert">{{ portal.errors.records.message }}</p>
        <p v-else-if="portal.records && !portal.records.length" class="mt-2 text-ink-soft">還沒有回報紀錄。</p>
        <ul class="mt-2 divide-y divide-line">
          <li v-for="r in portal.records ?? []" :key="r.id" class="py-3" :data-report="r.id">
            <p class="font-bold">{{ when(r.recorded_at) }}<template v-if="r.cycle_day">・療程第 {{ r.cycle_day }} 天</template></p>
            <p>{{ values(r) }}</p>
            <p class="text-sm text-ink-soft">{{ r.review_status === 'reviewed' ? '護理師已看過這筆回報' : '護理師會查看這筆回報' }}</p>
          </li>
        </ul>
      </section>
    </main>

    <PatientBottomNav active="symptom-report" :unread="unread" />
  </div>
</template>
