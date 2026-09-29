<script setup>
import { computed, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import SymptomReviewCard from '@/components/SymptomReviewCard.vue'
import { useNurseStore } from '@/stores/nurse'

/** Symptom reports of one patient, split into 待審閱 / 已審閱. */
const props = defineProps({
  patientId: { type: String, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useNurseStore()
const tab = ref('submitted')
const key = computed(() => `${props.patientId}:${tab.value}`)
const list = computed(() => store.records[key.value])

watch([() => props.patientId, tab], () => store.fetchRecords(props.patientId, tab.value), { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface" aria-label="症狀審閱">
    <header class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
      <AppIcon name="clipboard" class="text-care" />
      <h2 class="text-lg font-bold">症狀審閱</h2>
      <div class="ml-auto flex rounded-full border border-line p-0.5" role="tablist">
        <button
          v-for="t in [{ k: 'submitted', l: '待審閱', c: list?.meta.submitted }, { k: 'reviewed', l: '已審閱', c: list?.meta.reviewed }]"
          :key="t.k"
          type="button"
          role="tab"
          :aria-selected="tab === t.k"
          class="min-h-9 rounded-full px-4 text-sm"
          :class="tab === t.k ? 'bg-care font-bold text-white' : 'text-ink-soft hover:text-ink'"
          @click="tab = t.k"
        >{{ t.l }}<template v-if="t.c != null">（{{ t.c }}）</template></button>
      </div>
    </header>

    <div class="p-4">
      <p v-if="store.errors[key]" class="text-critical">{{ store.errors[key] }}</p>
      <div v-else-if="!list" class="space-y-3" aria-busy="true">
        <div v-for="n in 2" :key="n" class="h-24 animate-pulse rounded-xl bg-mist" />
      </div>
      <p v-else-if="!list.items.length" class="text-ink-soft">
        {{ tab === 'submitted' ? '沒有待審閱的症狀回報。' : '還沒有已審閱的回報。' }}
      </p>
      <div v-else class="space-y-3">
        <SymptomReviewCard
          v-for="r in list.items"
          :key="r.id"
          :record="r"
          :patient-id="patientId"
          :today="today"
          :timezone="timezone"
        />
      </div>
    </div>
  </section>
</template>
