<script setup>
import { computed } from 'vue'

import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatShortDate } from '@/utils/format'

/**
 * self-care-stamps widget: one stamp card per self-care question of 每日症狀與自我照護回報, one
 * cell per day of the current month (patient's local calendar, from the backend).
 * 有 → red ✓ stamp, 沒有 → red X, not reported → empty cell. "Not reported" is never shown as X.
 */
const props = defineProps({
  stamps: { type: Object, default: null },
})

const MARK = {
  yes: { text: '✓', label: '有做到' },
  no: { text: 'X', label: '沒有做到' },
}

const cards = computed(() =>
  (props.stamps?.items ?? []).map((item) => ({
    ...item,
    done: item.days.filter((d) => d.answer === 'yes').length,
    days: item.days.map((d) => ({
      ...d,
      short: formatShortDate(d.date),
      today: d.date === props.stamps.date,
      future: d.date > props.stamps.date,
    })),
  })),
)
const month = computed(() => Number(props.stamps?.month?.split('-')[1] ?? 0))

function cellLabel(d) {
  if (d.answer) return `${d.short} ${MARK[d.answer].label}`
  return `${d.short} ${d.future ? '' : '尚未回報'}`.trim()
}
</script>

<template>
  <WidgetFrame
    title="我的自我照護集點卡"
    icon="check"
    :state="cards.length ? 'ready' : 'empty'"
    empty-text="目前沒有自我照護項目。"
  >
    <template #meta>
      <span>{{ month }} 月</span>
    </template>

    <p class="mb-3 text-ink-soft">
      每天完成「每日症狀與自我照護回報」後，就會在這裡集點：
      <span class="whitespace-nowrap"><b class="text-critical">✓</b> 有做到</span>、
      <span class="whitespace-nowrap"><b class="text-critical">X</b> 沒有做到</span>、
      <span class="whitespace-nowrap">空白 = 還沒回報</span>。
    </p>

    <div class="space-y-4">
      <article
        v-for="card in cards"
        :key="card.code"
        class="rounded-xl border border-line bg-mist/50 p-3"
        :data-stamp-card="card.code"
      >
        <h3 class="font-bold leading-snug">{{ card.label }}</h3>
        <p class="mt-0.5 text-sm text-ink-soft">本月有做到 <b class="text-critical">{{ card.done }}</b> 天</p>
        <ol class="mt-2 grid grid-cols-7 gap-1" :aria-label="`${card.label}，每日紀錄`">
          <li
            v-for="d in card.days"
            :key="d.date"
            class="flex aspect-square min-w-0 flex-col items-center justify-center rounded-lg border text-center"
            :class="[
              d.answer === 'yes' ? 'border-critical/50 bg-critical-soft'
              : d.answer === 'no' ? 'border-line bg-surface'
              : d.future ? 'border-transparent bg-transparent'
              : 'border-dashed border-line bg-surface',
              d.today ? 'ring-2 ring-care ring-offset-1' : '',
            ]"
            :data-date="d.date"
            :data-answer="d.answer ?? 'none'"
            :aria-label="cellLabel(d)"
            :aria-current="d.today ? 'date' : undefined"
          >
            <span class="text-[11px] leading-none" :class="d.future ? 'text-muted' : 'text-ink-soft'">{{ d.short }}</span>
            <span
              class="mt-0.5 h-5 text-lg font-bold leading-5 text-critical"
              aria-hidden="true"
              data-mark
            >{{ d.answer ? MARK[d.answer].text : '' }}</span>
          </li>
        </ol>
      </article>
    </div>
  </WidgetFrame>
</template>
