<script setup>
import { computed } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatWhen } from '@/utils/format'

/** latest-vitals widget. Flags come from the API (reference ranges live on the server). */
const props = defineProps({
  vitals: { type: Object, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
  entryRoute: { type: Object, default: null }, // patient view: link to the entry page
})

const FLAG = {
  critical: { text: '危急', tile: 'border-critical bg-critical-soft', value: 'text-critical' },
  warning: { text: '偏離', tile: 'border-warn/50 bg-warn-soft', value: 'text-warn' },
}

const tiles = computed(() => {
  const v = props.vitals
  const list = [
    v.temperature_c && { ...v.temperature_c, key: 't', icon: 'thermometer', label: '體溫', value: v.temperature_c.value.toFixed(1), unit: '°C' },
    v.heart_rate_bpm && { ...v.heart_rate_bpm, key: 'hr', icon: 'heart', label: '心跳', unit: '次/分' },
    v.blood_pressure && {
      ...v.blood_pressure, key: 'bp', icon: 'pressure', label: '血壓',
      value: `${v.blood_pressure.systolic}/${v.blood_pressure.diastolic}`, unit: 'mmHg',
    },
    v.spo2_pct && { ...v.spo2_pct, key: 'spo2', icon: 'drop', label: '血氧', unit: '%' },
    v.respiratory_rate && { ...v.respiratory_rate, key: 'rr', icon: 'lungs', label: '呼吸', unit: '次/分' },
    v.weight_kg && {
      ...v.weight_kg, key: 'wt', icon: 'scale', label: '體重', value: v.weight_kg.value.toFixed(1), unit: 'kg',
      note: v.weight_kg.change_pct_7d != null ? `7 天 ${v.weight_kg.change_pct_7d > 0 ? '+' : ''}${v.weight_kg.change_pct_7d}%` : null,
    },
  ]
  return list.filter(Boolean)
})

const lastMeasured = computed(() => {
  const times = tiles.value.map((t) => t.measured_at).sort()
  return times.at(-1)
})
</script>

<template>
  <WidgetFrame
    title="最新生命徵象"
    icon="heart"
    :state="tiles.length || entryRoute ? 'ready' : 'empty'"
    empty-text="還沒有生命徵象紀錄。量測後記得在「健康紀錄」填寫。"
  >
    <template #meta>
      <span v-if="lastMeasured">{{ formatWhen(lastMeasured, today, timezone) }} 量測</span>
    </template>

    <p v-if="!tiles.length" class="text-ink-soft">還沒有生命徵象紀錄。</p>
    <ul v-else class="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <li
        v-for="t in tiles"
        :key="t.key"
        class="rounded-xl border px-4 py-3"
        :class="FLAG[t.flag]?.tile ?? 'border-line'"
      >
        <p class="flex items-center gap-1.5 text-ink-soft">
          <AppIcon :name="t.icon" :size="18" />
          {{ t.label }}
          <span
            v-if="t.flag"
            class="ml-auto rounded-full px-2 text-sm font-bold"
            :class="FLAG[t.flag].value"
          >{{ FLAG[t.flag].text }}</span>
        </p>
        <p class="mt-1">
          <span class="text-2xl font-bold" :class="FLAG[t.flag]?.value">{{ t.value }}</span>
          <span class="ml-1 text-ink-soft">{{ t.unit }}</span>
        </p>
        <p v-if="t.note" class="text-sm" :class="FLAG[t.flag]?.value ?? 'text-ink-soft'">{{ t.note }}</p>
      </li>
    </ul>
    <RouterLink
      v-if="entryRoute"
      :to="entryRoute"
      class="mt-4 inline-flex min-h-12 items-center gap-2 rounded-full border border-line px-5 font-medium text-care hover:bg-care-soft"
    >
      <AppIcon name="heart" :size="20" /> 記錄生命徵象
    </RouterLink>
  </WidgetFrame>
</template>
