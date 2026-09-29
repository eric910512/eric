<script setup>
import { computed } from 'vue'

import WidgetFrame from '@/components/WidgetFrame.vue'
import { addDays, daysBetween, formatShortDate } from '@/utils/format'

/**
 * treatment-progress widget. The cycle strip shows every day of the current cycle:
 * infusion day, the nadir (low-immunity) window, and today.
 */
const props = defineProps({
  progress: { type: Object, default: null },
  cycleMarkers: { type: Array, default: () => [] },
  today: { type: String, required: true },
  disclaimer: { type: String, default: '' },
  detailsTo: { type: Object, default: null }, // route to the full plan (patient: 我的療程)
})

const DEFAULT_CYCLE_LENGTH = 21

const cycle = computed(() => props.progress?.current_cycle ?? null)

const strip = computed(() => {
  if (!cycle.value) return null
  const start = addDays(props.today, -(cycle.value.cycle_day - 1))
  const nextDate = props.progress.next_cycle_date
  const length = nextDate ? daysBetween(start, nextDate) : DEFAULT_CYCLE_LENGTH

  const marker = props.cycleMarkers.find((m) => m.cycle_number === cycle.value.cycle_number)
  const nadirEnd = marker?.nadir_end_date ?? cycle.value.nadir_end_date
  const nadirStart = marker?.nadir_start_date ?? null
  const nadirFrom = nadirStart ? daysBetween(start, nadirStart) + 1 : null
  const nadirTo = nadirEnd ? daysBetween(start, nadirEnd) + 1 : null

  const days = Array.from({ length }, (_, i) => {
    const day = i + 1
    return {
      day,
      date: addDays(start, i),
      infusion: day === 1,
      nadir: nadirFrom !== null && nadirTo !== null && day >= nadirFrom && day <= nadirTo,
      today: day === cycle.value.cycle_day,
      past: day < cycle.value.cycle_day,
    }
  })
  return { start, length, days, nadirStart, nadirEnd, nadirFrom, nadirTo }
})

const ticks = computed(() => {
  if (!strip.value) return []
  const set = new Set([1, 7, 14, strip.value.length])
  return [...set].filter((d) => d <= strip.value.length)
})

const stripLabel = computed(() => {
  if (!strip.value) return ''
  const s = strip.value
  const nadir = s.nadirFrom ? `，骨髓抑制期為第 ${s.nadirFrom} 到 ${s.nadirTo} 天` : ''
  return `本次療程共 ${s.length} 天，今天是第 ${cycle.value.cycle_day} 天${nadir}`
})

const cycles = computed(() => {
  const p = props.progress
  if (!p?.total_cycles) return []
  return Array.from({ length: p.total_cycles }, (_, i) => {
    const n = i + 1
    return { n, done: n <= p.completed_cycles, current: n === cycle.value?.cycle_number }
  })
})
</script>

<template>
  <WidgetFrame
    title="化療進度"
    icon="drop"
    :state="progress ? 'ready' : 'empty'"
    empty-text="目前沒有進行中的化療療程。"
  >
    <template #meta>
      <span v-if="progress?.attending_physician_name">主治 {{ progress.attending_physician_name }}</span>
    </template>

    <template v-if="progress">
      <p class="text-ink-soft">{{ progress.diagnosis_name }}　{{ progress.regimen_name }}</p>

      <div v-if="cycle" class="mt-1 flex flex-wrap items-baseline gap-x-3">
        <p class="text-3xl font-bold tracking-tight">
          第 {{ cycle.cycle_number }} 次療程，第 {{ cycle.cycle_day }} 天
        </p>
      </div>

      <!-- Cycle strip -->
      <figure v-if="strip" class="mt-4">
        <div
          class="grid gap-[3px]"
          :style="{ gridTemplateColumns: `repeat(${strip.length}, minmax(0, 1fr))` }"
          role="img"
          :aria-label="stripLabel"
        >
          <div
            v-for="d in strip.days"
            :key="d.day"
            class="relative h-9 rounded-[4px]"
            :class="[
              d.nadir ? 'bg-nadir-soft' : d.past ? 'bg-care-soft' : 'bg-mist',
              d.today ? 'ring-2 ring-ink ring-offset-2 ring-offset-surface' : '',
            ]"
          >
            <span v-if="d.infusion" class="absolute inset-x-0 bottom-0 h-1.5 rounded-b-[4px] bg-care" />
          </div>
        </div>
        <div
          class="mt-1 grid text-sm text-ink-soft"
          :style="{ gridTemplateColumns: `repeat(${strip.length}, minmax(0, 1fr))` }"
          aria-hidden="true"
        >
          <span
            v-for="t in ticks"
            :key="t"
            class="text-center"
            :style="{ gridColumn: `${t} / span 1` }"
          >{{ t }}</span>
        </div>
        <figcaption class="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-soft">
          <span class="inline-flex items-center gap-1.5"><i class="h-1.5 w-4 rounded bg-care" />注射日</span>
          <span class="inline-flex items-center gap-1.5"><i class="size-3 rounded-sm bg-nadir-soft ring-1 ring-nadir/40" />骨髓抑制期</span>
          <span class="inline-flex items-center gap-1.5"><i class="size-3 rounded-sm ring-2 ring-ink" />今天</span>
        </figcaption>
      </figure>

      <p
        v-if="cycle?.in_nadir"
        class="mt-4 rounded-xl bg-nadir-soft px-4 py-3 font-medium text-nadir"
      >
        目前在骨髓抑制期（到 {{ formatShortDate(strip?.nadirEnd) }}），抵抗力較弱。體溫 38°C 以上請立即聯絡醫療團隊。
      </p>
      <p v-else-if="strip?.nadirStart && today < strip.nadirStart" class="mt-4 text-ink-soft">
        骨髓抑制期預計在 {{ formatShortDate(strip.nadirStart) }} 到 {{ formatShortDate(strip.nadirEnd) }}，這段期間請特別注意體溫。
      </p>

      <div class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <div class="flex items-center gap-2">
          <ol class="flex gap-1.5" :aria-label="`共 ${progress.total_cycles} 次療程，已完成 ${progress.completed_cycles} 次`">
            <li
              v-for="c in cycles"
              :key="c.n"
              class="h-2.5 w-8 rounded-full"
              :class="c.done ? 'bg-ok' : c.current ? 'bg-care' : 'bg-mist ring-1 ring-line'"
            />
          </ol>
          <span class="text-ink-soft">已完成 {{ progress.completed_cycles }} / {{ progress.total_cycles }} 次</span>
        </div>
        <p v-if="progress.next_cycle_date">
          下次療程 <strong>{{ formatShortDate(progress.next_cycle_date) }}</strong>
          <span v-if="progress.next_cycle_date_estimated" class="text-ink-soft">（預估）</span>
        </p>
      </div>

      <RouterLink v-if="detailsTo" :to="detailsTo" class="mt-3 inline-flex min-h-11 items-center font-bold text-care hover:underline" data-treatment-details>查看療程與給藥紀錄</RouterLink>
      <p v-if="disclaimer" class="mt-3 text-sm text-ink-soft">{{ disclaimer }}</p>
    </template>
  </WidgetFrame>
</template>
