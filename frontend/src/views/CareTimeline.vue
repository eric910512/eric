<script setup>
import { computed, onMounted } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import HealthRecordsTabs from '@/components/HealthRecordsTabs.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import PatientTimeline from '@/components/PatientTimeline.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'

/** Patient: 健康紀錄 → 照護時間軸 (/patient/timeline). */
const auth = useAuthStore()
const store = useDashboardStore()
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const unread = computed(() => store.patients[patientId.value]?.widgets.notifications?.unread_count ?? 0)
const timezone = computed(() => store.patients[patientId.value]?.timezone ?? 'Asia/Taipei')

onMounted(() => {
  if (!store.patients[patientId.value]) store.fetchPatientDashboard(patientId.value)
})
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">健康紀錄</h1>
      </div>
      <HealthRecordsTabs active="patient-timeline" />
    </header>

    <main class="mx-auto max-w-xl px-4 pt-4">
      <p class="mb-3 text-ink-soft">化療、症狀回報、量測、檢驗和護理團隊的處理，都依時間列在這裡。點一下可以看詳細內容。</p>
      <PatientTimeline v-if="patientId" :patient-id="patientId" audience="patient" :timezone="timezone" />
    </main>

    <PatientBottomNav active="vitals" :unread="unread" />
  </div>
</template>
