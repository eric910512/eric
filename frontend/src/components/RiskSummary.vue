<script setup>
import { computed } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { formatTime } from '@/utils/format'

/**
 * risk-summary widget (patient home): the risk engine's result for today in plain language,
 * what has been recorded today, today's alerts and whether the care team handled them.
 */
const props = defineProps({
  summary: { type: Object, required: true },
  hotline: { type: Object, default: null },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const TONE = {
  urgent: { box: 'border-2 border-critical bg-critical-soft', title: 'text-critical', icon: 'alert' },
  handled: { box: 'border border-care/40 bg-care-soft', title: 'text-care', icon: 'check' },
  attention: { box: 'border border-warn/50 bg-warn-soft', title: 'text-warn', icon: 'alert' },
  stable: { box: 'border border-ok/40 bg-ok-soft', title: 'text-ok', icon: 'check' },
}
const tone = computed(() => TONE[props.summary.status] ?? TONE.stable)
const today = computed(() => props.summary.today)
</script>

<template>
  <section
    class="rounded-2xl p-5"
    :class="tone.box"
    :role="summary.status === 'urgent' ? 'alert' : undefined"
    aria-label="今日健康風險摘要"
  >
    <p class="text-sm font-medium text-ink-soft">今天的健康狀況</p>
    <p class="mt-1 flex items-start gap-2 text-xl font-bold" :class="tone.title">
      <AppIcon :name="tone.icon" :size="26" class="mt-0.5 shrink-0" />{{ summary.title }}
    </p>
    <p class="mt-1 text-lg">{{ summary.message }}</p>
    <p v-if="summary.in_nadir" class="mt-2 inline-block rounded-full bg-nadir-soft px-3 py-0.5 font-medium text-nadir">目前在骨髓抑制期</p>

    <ul v-if="summary.reasons.length" class="mt-3 list-disc space-y-0.5 pl-5">
      <li v-for="r in summary.reasons" :key="r">{{ r }}</li>
    </ul>

    <!-- today's checklist -->
    <ul class="mt-4 divide-y divide-line/70 rounded-xl bg-surface">
      <li class="flex items-start gap-3 px-4 py-3">
        <AppIcon :name="today.symptom_reported ? 'check' : 'clipboard'" class="mt-0.5 shrink-0" :class="today.symptom_reported ? 'text-ok' : 'text-muted'" />
        <div class="min-w-0 flex-1">
          <p class="font-medium">
            症狀回報
            <span class="font-normal text-ink-soft">
              {{ today.symptom_reported ? `已完成（${formatTime(today.last_symptom_report_at, timezone)}）` : '今天還沒回報' }}
            </span>
          </p>
          <p v-if="today.symptoms.length" class="text-sm text-ink-soft">
            <span v-for="(s, i) in today.symptoms" :key="s.label" :class="s.score >= 7 ? 'font-bold text-action-ink' : ''">
              {{ i ? '、' : '' }}{{ s.label }} {{ s.score }}
            </span>
          </p>
        </div>
      </li>
      <li class="flex items-start gap-3 px-4 py-3">
        <AppIcon :name="today.vitals_recorded ? 'check' : 'heart'" class="mt-0.5 shrink-0" :class="today.vitals_recorded ? 'text-ok' : 'text-muted'" />
        <div class="min-w-0 flex-1">
          <p class="font-medium">
            生命徵象
            <span class="font-normal text-ink-soft">
              {{ today.vitals_recorded ? `已記錄（${formatTime(today.last_vitals_at, timezone)}）` : '今天還沒記錄' }}
            </span>
          </p>
          <p v-for="f in today.vital_flags" :key="f.field" class="text-sm font-medium" :class="f.level === 'critical' ? 'text-critical' : 'text-warn'">
            {{ f.message }}
          </p>
        </div>
      </li>
      <li v-if="today.alerts.total" class="flex items-start gap-3 px-4 py-3">
        <AppIcon name="bell" class="mt-0.5 shrink-0" :class="today.alerts.open ? 'text-critical' : 'text-ok'" />
        <div class="min-w-0 flex-1">
          <p class="font-medium">
            今天的提醒 {{ today.alerts.total }} 則
            <span class="font-normal text-ink-soft">{{ today.alerts.open ? `，${today.alerts.open} 則護理團隊處理中` : '，都已處理' }}</span>
          </p>
          <ul class="mt-1 space-y-1 text-sm">
            <li v-for="a in today.alerts.items" :key="a.id">
              <span class="font-medium" :class="a.severity === 'critical' ? 'text-critical' : ''">{{ a.title }}</span>
              <span class="text-ink-soft">（{{ formatTime(a.created_at, timezone) }}，{{ a.status_text ?? (a.resolved ? '已處理完成' : '護理團隊已收到通知') }}）</span>
            </li>
          </ul>
        </div>
      </li>
    </ul>

    <!-- next steps -->
    <div v-if="summary.actions.length" class="mt-4 flex flex-col gap-2">
      <template v-for="a in summary.actions" :key="a.code">
        <a v-if="a.code === 'call_hotline' && hotline" :href="`tel:${hotline.phone}`"
           class="flex min-h-14 items-center justify-center gap-2 rounded-full bg-critical text-lg font-bold text-white hover:bg-critical/90">
          <AppIcon name="phone" /> {{ a.label }} {{ hotline.phone }}
        </a>
        <a v-else-if="a.code === 'report_symptoms'" href="#w-symptom-quick-report"
           class="flex min-h-12 items-center justify-center gap-2 rounded-full border border-line bg-surface font-bold text-care hover:bg-care-soft">
          <AppIcon name="clipboard" :size="20" /> {{ a.label }}
        </a>
        <RouterLink v-else-if="a.code === 'measure_vitals'" :to="{ name: 'patient-vitals' }"
           class="flex min-h-12 items-center justify-center gap-2 rounded-full border border-line bg-surface font-bold text-care hover:bg-care-soft">
          <AppIcon name="heart" :size="20" /> {{ a.label }}
        </RouterLink>
      </template>
    </div>
  </section>
</template>
