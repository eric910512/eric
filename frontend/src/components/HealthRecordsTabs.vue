<script setup>
/** Tabs inside the patient's 「健康紀錄」 section. */
defineProps({ active: { type: String, required: true } }) // patient-vitals | patient-timeline

const TABS = [
  { name: 'patient-timeline', label: '照護時間軸' },
  { name: 'patient-vitals', label: '記錄生命徵象' },
]
</script>

<template>
  <nav class="mx-auto flex max-w-xl gap-1 px-4" aria-label="健康紀錄">
    <RouterLink
      v-for="t in TABS"
      :key="t.name"
      :to="{ name: t.name }"
      class="-mb-px flex min-h-11 flex-1 items-center justify-center border-b-[3px] px-2 font-medium"
      :class="active === t.name ? 'border-care text-ink' : 'border-transparent text-ink-soft hover:text-ink'"
      :aria-current="active === t.name ? 'page' : undefined"
    >{{ t.label }}</RouterLink>
  </nav>
</template>
