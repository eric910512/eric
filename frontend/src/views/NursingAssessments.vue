<script setup>
import { computed, onMounted } from 'vue'

import NurseNav from '@/components/NurseNav.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { useNursingStore } from '@/stores/nursing'
import { formatTime, localDate } from '@/utils/format'

/** 護理評估 (nurse): my drafts waiting for signature and my recent assessments of current patients. */
const dashboard = useDashboardStore()
const store = useNursingStore()
const TZ = 'Asia/Taipei'
const RISK = { low: '低', medium: '中', high: '高' }
const drafts = computed(() => (store.mine ?? []).filter((a) => a.sign_status === 'draft'))
const signed = computed(() => (store.mine ?? []).filter((a) => a.sign_status === 'signed').slice(0, 30))
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchMine()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="nursing-assessments" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header>
        <h1 class="text-2xl font-bold">護理評估</h1>
        <p class="text-ink-soft">我撰寫的評估（目前負責的病人）。在病人資料頁新增、修改、簽署或修正。</p>
      </header>
      <p v-if="store.errors.mine" class="text-critical" role="alert">{{ store.errors.mine.message }}</p>
      <div v-else-if="!store.mine" class="h-32 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <template v-else>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="drafts-title" data-my-drafts>
          <h2 id="drafts-title" class="border-b border-line px-5 py-3 text-lg font-bold">待簽署草稿 <span class="text-base font-normal text-ink-soft">{{ drafts.length }} 份</span></h2>
          <p v-if="!drafts.length" class="p-5 text-ink-soft">沒有待簽署的草稿。</p>
          <ul class="divide-y divide-line">
            <li v-for="a in drafts" :key="a.id">
              <RouterLink :to="{ name: 'nurse-patient', params: { id: a.patient_id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ a.patient_name }}</span><span class="text-ink-soft">{{ a.patient_code }}</span>
                <span>{{ a.assessment_type_text }}</span>
                <span v-if="a.amends_id" class="rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">修正版本</span>
                <span class="ml-auto text-sm text-ink-soft">{{ when(a.assessed_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
        <section class="rounded-2xl border border-line bg-surface" aria-labelledby="signed-title">
          <h2 id="signed-title" class="border-b border-line px-5 py-3 text-lg font-bold">最近簽署</h2>
          <p v-if="!signed.length" class="p-5 text-ink-soft">還沒有簽署的評估。</p>
          <ul class="divide-y divide-line">
            <li v-for="a in signed" :key="a.id">
              <RouterLink :to="{ name: 'nurse-patient', params: { id: a.patient_id } }" class="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-mist">
                <span class="font-bold">{{ a.patient_name }}</span><span class="text-ink-soft">{{ a.patient_code }}</span>
                <span>{{ a.assessment_type_text }}</span>
                <span v-if="a.risk_level" class="text-sm" :class="a.risk_level === 'high' ? 'font-bold text-critical' : 'text-ink-soft'">風險 {{ RISK[a.risk_level] }}</span>
                <span class="ml-auto text-sm text-ink-soft">{{ when(a.assessed_at) }}</span>
              </RouterLink>
            </li>
          </ul>
        </section>
      </template>
    </main>
  </div>
</template>
