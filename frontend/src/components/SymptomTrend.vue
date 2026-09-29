<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatShortDate } from '@/utils/format'

/**
 * symptom-trend widget: daily max score (0–10) per symptom, with the nadir window shaded.
 * Missing days are gaps, never zeros (database-design.md §4 缺值即資訊).
 * Rendered at the container's real pixel width so text never scales.
 */
const props = defineProps({
  trend: { type: Object, required: true },
  title: { type: String, default: '症狀趨勢' },
})

const COLORS = ['#2f6b8a', '#c27a12', '#b4436c', '#2e7d5b']
const HEIGHT = 200
const PAD = { top: 12, right: 12, bottom: 28, left: 28 }
const MAX = 10

const box = ref(null)
const width = ref(600)
let observer
onMounted(() => {
  if (!box.value) return // empty state: no chart to measure
  observer = new ResizeObserver(([entry]) => (width.value = Math.max(280, entry.contentRect.width)))
  observer.observe(box.value)
})
onBeforeUnmount(() => observer?.disconnect())

const dates = computed(() => props.trend.series[0]?.points.map((p) => p.date) ?? [])
const hasData = computed(() => props.trend.series.some((s) => s.points.some((p) => p.value !== null)))

const plotW = computed(() => width.value - PAD.left - PAD.right)
const plotH = HEIGHT - PAD.top - PAD.bottom
const step = computed(() => (dates.value.length > 1 ? plotW.value / (dates.value.length - 1) : 0))
const x = (i) => PAD.left + i * step.value
const y = (v) => PAD.top + plotH - (v / MAX) * plotH

const lines = computed(() =>
  props.trend.series.map((s, si) => {
    let d = ''
    let pen = false
    const dots = []
    s.points.forEach((p, i) => {
      if (p.value === null) {
        pen = false
        return
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`
      pen = true
      dots.push({ cx: x(i), cy: y(p.value), value: p.value, date: p.date })
    })
    const last = [...s.points].reverse().find((p) => p.value !== null)
    return { key: s.key, label: s.label, color: COLORS[si % COLORS.length], d, dots, last }
  }),
)

const nadirBands = computed(() =>
  props.trend.cycle_markers
    .filter((m) => m.nadir_start_date && m.nadir_end_date)
    .map((m) => {
      const from = Math.max(0, dates.value.indexOf(m.nadir_start_date))
      let to = dates.value.indexOf(m.nadir_end_date)
      if (m.nadir_start_date > dates.value.at(-1)) return null
      if (to === -1) to = dates.value.length - 1
      return { key: m.cycle_number, x: x(from) - step.value / 2, w: (to - from + 1) * step.value }
    })
    .filter(Boolean),
)

const cycleStarts = computed(() =>
  props.trend.cycle_markers
    .map((m) => ({ n: m.cycle_number, i: dates.value.indexOf(m.start_date) }))
    .filter((m) => m.i >= 0),
)

const xLabels = computed(() => {
  const n = dates.value.length
  const every = Math.ceil(n / Math.max(2, Math.floor(plotW.value / 64)))
  return dates.value
    .map((d, i) => ({ i, text: formatShortDate(d) }))
    .filter(({ i }) => i === n - 1 || (i % every === 0 && n - 1 - i >= every / 2))
})
</script>

<template>
  <WidgetFrame
    :title="title"
    icon="chart"
    :state="hasData ? 'ready' : 'empty'"
    empty-text="這段期間還沒有症狀紀錄。每天填寫自評，就能在這裡看到變化。"
  >
    <template #meta>
      <span>{{ formatShortDate(trend.from) }}–{{ formatShortDate(trend.to) }}，每日最高分</span>
    </template>

    <ul class="mb-2 flex flex-wrap gap-x-5 gap-y-1">
      <li v-for="l in lines" :key="l.key" class="inline-flex items-center gap-2">
        <i class="h-1 w-5 rounded" :style="{ background: l.color }" />
        <span>{{ l.label }}</span>
        <strong v-if="l.last">{{ l.last.value }}</strong>
      </li>
      <li v-if="nadirBands.length" class="inline-flex items-center gap-2 text-ink-soft">
        <i class="size-3 rounded-sm bg-nadir-soft ring-1 ring-nadir/40" />骨髓抑制期
      </li>
    </ul>

    <div ref="box" class="w-full">
      <svg :width="width" :height="HEIGHT" role="img" :aria-label="`${title}折線圖，詳細數值見下表`">
        <rect
          v-for="b in nadirBands"
          :key="`n${b.key}`"
          :x="b.x"
          :y="PAD.top"
          :width="b.w"
          :height="plotH"
          class="fill-nadir-soft"
        />
        <g class="stroke-line" stroke-width="1">
          <line v-for="v in [0, 5, 10]" :key="v" :x1="PAD.left" :x2="width - PAD.right" :y1="y(v)" :y2="y(v)" />
        </g>
        <g class="fill-ink-soft text-[12px]">
          <text v-for="v in [0, 5, 10]" :key="`t${v}`" :x="PAD.left - 8" :y="y(v) + 4" text-anchor="end">{{ v }}</text>
          <text v-for="l in xLabels" :key="`x${l.i}`" :x="x(l.i)" :y="HEIGHT - 8" text-anchor="middle">{{ l.text }}</text>
        </g>
        <g v-for="c in cycleStarts" :key="`c${c.n}`">
          <line :x1="x(c.i)" :x2="x(c.i)" :y1="PAD.top" :y2="PAD.top + plotH" class="stroke-care" stroke-dasharray="3 3" />
          <text :x="x(c.i) + 4" :y="PAD.top + 12" class="fill-care text-[12px] font-bold">第 {{ c.n }} 次療程</text>
        </g>
        <g v-for="l in lines" :key="l.key">
          <path :d="l.d" fill="none" :stroke="l.color" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
          <circle v-for="dot in l.dots" :key="dot.date" :cx="dot.cx" :cy="dot.cy" r="4" :fill="l.color" class="stroke-surface" stroke-width="2" />
        </g>
      </svg>
    </div>

    <div class="sr-only"><table>
      <caption>{{ title }}（0 到 10 分，空白代表當天沒有紀錄）</caption>
      <thead>
        <tr><th>日期</th><th v-for="s in trend.series" :key="s.key">{{ s.label }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="(d, i) in dates" :key="d">
          <th>{{ d }}</th>
          <td v-for="s in trend.series" :key="s.key">{{ s.points[i].value ?? '' }}</td>
        </tr>
      </tbody>
    </table></div>
  </WidgetFrame>
</template>
