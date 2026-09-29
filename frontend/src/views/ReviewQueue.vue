<script setup>
import { computed, onMounted } from 'vue'

import NurseNav from '@/components/NurseNav.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { useRecordsStore } from '@/stores/records'
import { formatTime, localDate } from '@/utils/format'

/** 待審清單 (nurse): everything waiting across current patients — symptom reports to review,
 * abnormal vital signs and lab results, and alerts not yet taken over. Rows open the patient. */
const dashboard = useDashboardStore()
const store = useRecordsStore()
const TZ = 'Asia/Taipei'
const q = computed(() => store.queues)
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
const FLAG = { L: '偏低', LL: '過低', H: '偏高', HH: '過高' }
const sections = computed(() => [
  { key: 'symptoms', title: '待審症狀', count: q.value?.symptoms.length ?? 0 },
  { key: 'vitals', title: '異常生命徵象（72 小時）', count: q.value?.vitals.length ?? 0 },
  { key: 'labs', title: '異常檢驗（7 天）', count: q.value?.labs.length ?? 0 },
  { key: 'notifications', title: '待處理通知', count: q.value?.notifications.length ?? 0 },
])

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchQueues()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="symptom-reviews" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header>
        <h1 class="text-2xl font-bold">待審清單</h1>
        <p class="text-ink-soft">目前負責的病人中，等待審閱或處理的項目。</p>
      </header>
      <ul v-if="q" class="flex flex-wrap gap-2" aria-label="數量">
        <li v-for="s in sections" :key="s.key" class="rounded-full bg-surface px-4 py-1.5 ring-1 ring-line" :data-count="s.key">{{ s.title }} <strong>{{ s.count }}</strong></li>
      </ul>
      <p v-if="store.errors.queues" class="text-critical" role="alert">{{ store.errors.queues.message }}</p>
      <div v-else-if="!q" class="h-40 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <template v-else>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="q-sym" data-queue="symptoms">
          <h2 id="q-sym" class="border-b border-line px-5 py-3 text-lg font-bold">待審症狀</h2>
          <p v-if="!q.symptoms.length" class="p-5 text-ink-soft">沒有待審閱的症狀回報。</p>
          <ul class="divide-y divide-line">
            <li v-for="s in q.symptoms" :key="s.id">
              <RouterLink :to="{ name: 'nurse', params: { patientId: s.patient.id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ s.patient.display_name }}</span><span class="text-ink-soft">{{ s.patient.patient_code }}</span>
                <span class="min-w-0 flex-1">{{ s.summary }}</span>
                <span v-if="s.open_alert_count" class="rounded-full bg-warn-soft px-2 py-0.5 text-sm font-bold text-warn">警示 {{ s.open_alert_count }}</span>
                <span class="text-sm text-ink-soft">{{ when(s.recorded_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="q-vit" data-queue="vitals">
          <h2 id="q-vit" class="border-b border-line px-5 py-3 text-lg font-bold">異常生命徵象</h2>
          <p v-if="!q.vitals.length" class="p-5 text-ink-soft">72 小時內沒有異常的生命徵象。</p>
          <ul class="divide-y divide-line">
            <li v-for="v in q.vitals" :key="v.id">
              <RouterLink :to="{ name: 'nurse', params: { patientId: v.patient.id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ v.patient.display_name }}</span><span class="text-ink-soft">{{ v.patient.patient_code }}</span>
                <span class="min-w-0 flex-1" :class="v.severity === 'critical' ? 'font-bold text-critical' : 'text-warn'">{{ v.flags.map((f) => f.message).join('、') }}</span>
                <span class="text-sm text-ink-soft">{{ when(v.measured_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="q-lab" data-queue="labs">
          <h2 id="q-lab" class="border-b border-line px-5 py-3 text-lg font-bold">異常檢驗</h2>
          <p v-if="!q.labs.length" class="p-5 text-ink-soft">7 天內沒有異常的檢驗結果。</p>
          <ul class="divide-y divide-line">
            <li v-for="l in q.labs" :key="l.id">
              <RouterLink :to="{ name: 'nurse', params: { patientId: l.patient.id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ l.patient.display_name }}</span><span class="text-ink-soft">{{ l.patient.patient_code }}</span>
                <span class="min-w-0 flex-1" :class="l.level === 'critical' ? 'font-bold text-critical' : 'text-warn'">{{ l.test_code }} {{ l.value }} {{ l.unit }}（{{ FLAG[l.abnormal_flag] }}）</span>
                <span class="text-sm text-ink-soft">{{ when(l.collected_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="q-not" data-queue="notifications">
          <h2 id="q-not" class="border-b border-line px-5 py-3 text-lg font-bold">待處理通知</h2>
          <p v-if="!q.notifications.length" class="p-5 text-ink-soft">沒有待接手的通知。</p>
          <ul class="divide-y divide-line">
            <li v-for="n in q.notifications" :key="n.id">
              <RouterLink :to="{ name: 'nurse-notifications', query: { tab: 'pending', id: n.id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ n.patient?.display_name }}</span><span class="text-ink-soft">{{ n.patient?.patient_code }}</span>
                <span class="min-w-0 flex-1" :class="n.severity === 'critical' ? 'font-bold text-critical' : 'text-warn'">{{ n.title }}</span>
                <span class="text-sm text-ink-soft">{{ when(n.created_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
      </template>
    </main>
  </div>
</template>
