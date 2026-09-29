<script setup>
import { computed } from 'vue'

import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatWhen } from '@/utils/format'

/**
 * lab-summary widget (patient). Simplified view from GET /labs/summary: friendly names,
 * a status word and what an abnormal value means day to day. No reference-range numbers.
 */
const props = defineProps({
  summary: { type: Object, default: null },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const TONE = {
  critical: { pill: 'bg-critical text-white', row: 'bg-critical-soft', value: 'text-critical' },
  warning: { pill: 'bg-warn-soft text-warn', row: 'bg-warn-soft/60', value: 'text-warn' },
  normal: { pill: 'bg-ok-soft text-ok', row: '', value: '' },
}
const tone = (item) => TONE[item.level ?? 'normal']

const items = computed(() => props.summary?.items ?? [])
const hasCritical = computed(() => items.value.some((i) => i.level === 'critical'))
</script>

<template>
  <WidgetFrame
    title="最近檢驗結果"
    icon="flask"
    :state="summary ? (items.length ? 'ready' : 'empty') : 'loading'"
    :tone="hasCritical ? 'critical' : 'default'"
    empty-text="最近 30 天還沒有檢驗結果。抽血後，護理師會把結果登錄在這裡。"
  >
    <template #meta>
      <span v-if="summary?.last_collected_at">{{ formatWhen(summary.last_collected_at, today, timezone) }} 抽血</span>
    </template>

    <p class="font-medium" :class="hasCritical ? 'text-critical' : ''">{{ summary.message }}</p>
    <ul class="mt-3 divide-y divide-line overflow-hidden rounded-xl border border-line">
      <li v-for="item in items" :key="item.code" class="px-4 py-3" :class="tone(item).row">
        <div class="flex items-center gap-3">
          <span class="min-w-0 flex-1 font-medium">{{ item.label }}</span>
          <span class="whitespace-nowrap">
            <strong class="text-xl" :class="tone(item).value">{{ item.value }}</strong>
            <span class="ml-1 text-sm text-ink-soft">{{ item.unit }}</span>
          </span>
          <span class="w-12 shrink-0 rounded-full py-0.5 text-center text-sm font-bold" :class="tone(item).pill">
            {{ item.status_text }}
          </span>
        </div>
        <p v-if="item.explanation" class="mt-1.5 text-[0.95rem] leading-relaxed">{{ item.explanation }}</p>
      </li>
    </ul>
    <p class="mt-3 text-sm text-ink-soft">數值代表的意義，請以醫師門診說明為準。</p>
  </WidgetFrame>
</template>
