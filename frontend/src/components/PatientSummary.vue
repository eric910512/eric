<script setup>
import { computed } from 'vue'

import AppIcon from '@/components/AppIcon.vue'

/** patient-summary widget. `variant="banner"` is the nurse-side pinned banner. */
const props = defineProps({
  summary: { type: Object, required: true },
  variant: { type: String, default: 'card' }, // card | banner
})

const GENDER = { male: '男', female: '女', other: '其他' }
const ALERT_ICON = { allergy: 'alert', limb_restriction: 'hand', fall_risk: 'shield', isolation: 'shield', other: 'alert' }

const initial = computed(() => props.summary.display_name.trim().slice(-1))
const identity = computed(() =>
  [`${props.summary.age} 歲`, GENDER[props.summary.gender] ?? '', props.summary.patient_code].filter(Boolean),
)
</script>

<template>
  <section
    class="flex gap-4 border bg-surface"
    :class="
      variant === 'banner'
        ? 'items-center rounded-xl border-line px-5 py-3'
        : 'items-start rounded-3xl border-line p-5'
    "
    aria-label="病人資料"
  >
    <div
      class="grid shrink-0 place-items-center rounded-full bg-care-soft font-bold text-care"
      :class="variant === 'banner' ? 'size-12 text-xl' : 'size-16 text-2xl'"
      aria-hidden="true"
    >
      {{ initial }}
    </div>

    <div class="min-w-0 flex-1" :class="variant === 'banner' ? 'flex flex-wrap items-center gap-x-6 gap-y-2' : ''">
      <div>
        <p class="font-bold" :class="variant === 'banner' ? 'text-xl' : 'text-2xl'">{{ summary.display_name }}</p>
        <p class="mt-0.5 flex flex-wrap gap-x-3 text-ink-soft">
          <span v-for="part in identity" :key="part">{{ part }}</span>
        </p>
      </div>

      <ul v-if="summary.care_alerts.length" class="flex flex-wrap gap-2" :class="variant === 'card' ? 'mt-3' : ''">
        <li
          v-for="alert in summary.care_alerts"
          :key="alert.description"
          class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-medium"
          :class="alert.severity === 'high' ? 'bg-critical-soft text-critical' : 'bg-warn-soft text-warn'"
        >
          <AppIcon :name="ALERT_ICON[alert.alert_type] ?? 'alert'" :size="18" />
          {{ alert.description }}
        </li>
      </ul>
    </div>
  </section>
</template>
