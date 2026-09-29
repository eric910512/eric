<script setup>
import { computed, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { useTimelineStore } from '@/stores/timeline'
import { formatDate, formatTime, localDate } from '@/utils/format'
import { BP_SITES, TEMPERATURE_SITES } from '@/utils/vitals'

/**
 * Patient Care Timeline (GET /api/v1/patients/{id}/timeline): chemotherapy, symptoms, vitals,
 * labs, risk alerts, their handling and nursing care, newest first, grouped by local day.
 * `audience` only changes wording; what each viewer may see is decided by the API.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  audience: { type: String, default: 'patient' }, // patient | staff
  timezone: { type: String, default: 'Asia/Taipei' },
  title: { type: String, default: '照護時間軸' },
})

const store = useTimelineStore()
const entry = computed(() => store.byPatient[props.patientId])
const items = computed(() => entry.value?.items ?? [])
const staff = computed(() => props.audience === 'staff')
const today = computed(() => localDate(new Date().toISOString(), props.timezone))

const filters = reactive({ startDate: '', endDate: '' })
const filterError = ref('')
const open = reactive({})

function load() {
  store.load(props.patientId, { startDate: filters.startDate || null, endDate: filters.endDate || null })
}
watch(() => props.patientId, () => {
  filters.startDate = ''
  filters.endDate = ''
  Object.keys(open).forEach((k) => delete open[k])
  load()
}, { immediate: true })

function applyFilters() {
  filterError.value = filters.startDate && filters.endDate && filters.startDate > filters.endDate ? '結束日期不能早於開始日期。' : ''
  if (!filterError.value) load()
}
function clearFilters() {
  filters.startDate = ''
  filters.endDate = ''
  filterError.value = ''
  load()
}

// One visual language per category: icon + label; colour is reserved for severity,
// except chemotherapy (treatment milestones) and handling steps.
const KIND = {
  CHEMOTHERAPY: { label: '治療', icon: 'pill', dot: 'bg-nadir-soft text-nadir' },
  SYMPTOM: { label: '病人回報', icon: 'clipboard', dot: 'bg-care-soft text-care' },
  VITAL_SIGN: { label: '量測', icon: 'heart', dot: 'bg-care-soft text-care' },
  LAB_RESULT: { label: '檢驗', icon: 'flask', dot: 'bg-care-soft text-care' },
  NOTIFICATION: { label: '風險提醒', icon: 'bell', dot: 'bg-warn-soft text-warn' },
  NOTIFICATION_STATUS: { label: '處理進度', icon: 'check', dot: 'bg-ok-soft text-ok' },
  NURSING_ASSESSMENT: { label: '護理處理', icon: 'user', dot: 'bg-ok-soft text-ok' },
  APPOINTMENT: { label: '行程', icon: 'calendar', dot: 'bg-mist text-ink' },
}
const SEVERITY = {
  critical: { text: '危急', chip: 'bg-critical text-white', ring: 'border-critical/50' },
  warning: { text: '注意', chip: 'bg-warn-soft text-warn', ring: 'border-warn/40' },
}
const FLAG_TEXT = { critical: 'text-critical font-bold', warning: 'text-warn font-bold' }
const LAB_FLAG = { LL: '嚴重偏低', HH: '嚴重偏高', L: '偏低', H: '偏高', N: '正常' }
const RISK_TEXT = { high: '高', medium: '中', low: '低' }

/** Events whose summary already says everything stay a plain row (no expand). */
function hasDetail(e) {
  if (e.event_type === 'NOTIFICATION_STATUS') return staff.value && !!e.detail.resolution_note
  if (e.event_type === 'NURSING_ASSESSMENT') return staff.value
  return true
}

function dot(e) {
  if (e.event_type === 'NOTIFICATION' && e.severity === 'critical') return 'bg-critical-soft text-critical'
  return KIND[e.event_type]?.dot ?? 'bg-mist text-ink-soft'
}

const days = computed(() => {
  const groups = []
  for (const e of items.value) {
    const day = localDate(e.occurred_at, props.timezone)
    let g = groups.at(-1)
    if (!g || g.day !== day) {
      g = { day, events: [], cycleDays: new Set() }
      groups.push(g)
    }
    g.events.push(e)
    if (e.cycle_day) g.cycleDays.add(e.cycle_day)
  }
  return groups.map((g) => ({ ...g, cycleDay: g.cycleDays.size === 1 ? [...g.cycleDays][0] : null }))
})

function dayLabel(day) {
  const diff = Math.round((new Date(today.value) - new Date(day)) / 86400000)
  const prefix = diff === 0 ? '今天・' : diff === 1 ? '昨天・' : ''
  return `${prefix}${formatDate(day)}`
}
/** API person fields are { id, display_name }. */
const personName = (p) => (p && typeof p === 'object' ? p.display_name : p)
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface" aria-labelledby="timeline-title" data-timeline>
    <header class="border-b border-line px-4 py-3 sm:px-5">
      <div class="flex flex-wrap items-center gap-2">
        <AppIcon name="calendar" class="shrink-0 text-care" />
        <h2 id="timeline-title" class="text-lg font-bold">{{ title }}</h2>
        <span class="text-sm text-ink-soft">最新的在最上面</span>
      </div>
      <form class="mt-3 flex flex-wrap items-end gap-2" @submit.prevent="applyFilters">
        <label class="min-w-0 flex-1 basis-32 text-sm">
          <span class="text-ink-soft">從</span>
          <input v-model="filters.startDate" type="date" name="timeline-start" class="mt-0.5 block min-h-10 w-full rounded-lg border border-line bg-surface px-2" />
        </label>
        <label class="min-w-0 flex-1 basis-32 text-sm">
          <span class="text-ink-soft">到</span>
          <input v-model="filters.endDate" type="date" name="timeline-end" class="mt-0.5 block min-h-10 w-full rounded-lg border border-line bg-surface px-2" />
        </label>
        <button type="submit" class="min-h-10 rounded-full bg-care px-4 font-medium text-white hover:bg-care/90">查看</button>
        <button v-if="entry?.filters?.startDate || entry?.filters?.endDate" type="button" class="min-h-10 rounded-full border border-line px-4 hover:bg-mist" @click="clearFilters">全部</button>
      </form>
      <p v-if="filterError" class="mt-1 text-sm text-critical" role="alert">{{ filterError }}</p>
    </header>

    <div class="px-4 py-4 sm:px-5">
      <p v-if="entry?.error && !items.length" class="text-critical">{{ entry.error }}</p>
      <div v-else-if="!entry || entry.loading" class="space-y-3" aria-busy="true" aria-label="載入中">
        <div v-for="n in 4" :key="n" class="h-16 animate-pulse rounded-xl bg-mist" />
      </div>
      <p v-else-if="!items.length" class="text-ink-soft">
        {{ entry.filters.startDate || entry.filters.endDate ? '這段期間沒有紀錄。' : '還沒有任何照護紀錄。' }}
      </p>

      <ol v-else class="space-y-5">
        <li v-for="g in days" :key="g.day">
          <h3 class="mb-2 flex flex-wrap items-baseline gap-x-2 font-bold">
            {{ dayLabel(g.day) }}
            <span v-if="g.cycleDay" class="text-sm font-normal text-ink-soft">療程第 {{ g.cycleDay }} 天</span>
          </h3>
          <ol class="relative space-y-2 before:absolute before:top-2 before:bottom-2 before:left-[1.125rem] before:w-px before:bg-line">
            <li v-for="e in g.events" :key="e.event_id" class="relative flex gap-3" :data-event-type="e.event_type" :data-event-id="e.event_id" :data-occurred-at="e.occurred_at">
              <span class="relative z-[1] mt-2 grid size-9 shrink-0 place-items-center rounded-full ring-4 ring-surface" :class="dot(e)">
                <AppIcon :name="KIND[e.event_type]?.icon ?? 'clock'" :size="18" />
              </span>
              <div class="min-w-0 flex-1 rounded-xl border" :class="SEVERITY[e.severity]?.ring ?? 'border-line'">
                <component
                  :is="hasDetail(e) ? 'button' : 'div'"
                  :type="hasDetail(e) ? 'button' : undefined"
                  class="block w-full rounded-xl px-3 py-2.5 text-left"
                  :class="hasDetail(e) ? 'hover:bg-mist/60' : ''"
                  :aria-expanded="hasDetail(e) ? !!open[e.event_id] : undefined"
                  :aria-controls="hasDetail(e) ? `tl-${e.event_id}` : undefined"
                  @click="hasDetail(e) && (open[e.event_id] = !open[e.event_id])"
                >
                  <span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                    <span class="font-medium text-ink-soft">{{ KIND[e.event_type]?.label }}</span>
                    <span v-if="SEVERITY[e.severity]" class="rounded-full px-2 text-xs font-bold" :class="SEVERITY[e.severity].chip">{{ SEVERITY[e.severity].text }}</span>
                    <span class="ml-auto text-ink-soft">{{ e.all_day ? '全天' : formatTime(e.occurred_at, timezone) }}</span>
                  </span>
                  <span class="mt-0.5 block font-bold break-words">{{ e.title }}</span>
                  <span class="block text-[0.95rem] break-words" :class="open[e.event_id] || !hasDetail(e) ? '' : 'line-clamp-2'">{{ e.summary }}</span>
                </component>

                <!-- detail -->
                <div v-if="open[e.event_id] && hasDetail(e)" :id="`tl-${e.event_id}`" class="border-t border-line px-3 py-3 text-[0.95rem]" data-detail>
                  <template v-if="e.event_type === 'SYMPTOM'">
                    <dl class="grid grid-cols-2 gap-2">
                      <div v-for="v in e.detail.values" :key="v.code" class="rounded-lg bg-mist px-2.5 py-1.5">
                        <dt class="text-sm text-ink-soft">{{ v.label }}</dt>
                        <dd class="font-bold" :class="v.score >= 7 || v.value === '有' ? 'text-warn' : ''">{{ v.value }}<span v-if="v.score != null" class="text-sm font-normal text-ink-soft"> / 10</span></dd>
                      </div>
                    </dl>
                    <p v-if="e.detail.notes" class="mt-2">備註：{{ e.detail.notes }}</p>
                    <p class="mt-2 text-sm text-ink-soft">
                      <template v-if="staff">{{ e.detail.reviewed_by ? `已由${e.detail.reviewed_by}審閱` : '尚未審閱' }}</template>
                      <template v-else>{{ e.detail.reviewed ? '護理師已看過這筆回報' : '護理師會查看這筆回報' }}</template>
                    </p>
                  </template>

                  <template v-else-if="e.event_type === 'VITAL_SIGN'">
                    <dl class="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      <div v-for="v in e.detail.values" :key="v.field" class="rounded-lg bg-mist px-2.5 py-1.5">
                        <dt class="text-sm text-ink-soft">{{ v.label }}</dt>
                        <dd :class="FLAG_TEXT[v.flag] ?? 'font-bold'">{{ v.value }}</dd>
                      </div>
                    </dl>
                    <ul v-if="e.detail.flags?.length" class="mt-2 space-y-0.5 text-sm">
                      <li v-for="f in e.detail.flags" :key="f" class="text-warn">{{ f }}</li>
                    </ul>
                    <p v-if="e.detail.temperature_site || e.detail.bp_measure_site" class="mt-2 text-sm text-ink-soft">
                      {{ [TEMPERATURE_SITES[e.detail.temperature_site], e.detail.bp_measure_site && `血壓量${BP_SITES[e.detail.bp_measure_site]}`].filter(Boolean).join('、') }}
                    </p>
                  </template>

                  <template v-else-if="e.event_type === 'LAB_RESULT'">
                    <ul class="divide-y divide-line">
                      <li v-for="i in e.detail.items" :key="i.test_code ?? i.code" class="flex flex-wrap items-baseline gap-x-2 py-1.5">
                        <span class="min-w-0 flex-1">{{ staff ? `${i.test_code}` : i.label }}</span>
                        <strong class="tabular-nums">{{ i.value }}</strong>
                        <span class="text-sm text-ink-soft">{{ i.unit }}</span>
                        <span v-if="staff" class="w-full text-sm text-ink-soft sm:w-auto">
                          參考 {{ i.ref_low }}–{{ i.ref_high }}
                          <span :class="i.abnormal_flag === 'LL' || i.abnormal_flag === 'HH' ? 'font-bold text-critical' : i.abnormal_flag !== 'N' ? 'font-bold text-warn' : ''">・{{ LAB_FLAG[i.abnormal_flag] }}</span>
                        </span>
                        <span v-else class="text-sm font-bold" :class="i.level === 'critical' ? 'text-critical' : i.level === 'warning' ? 'text-warn' : 'text-ok'">{{ i.status_text }}</span>
                      </li>
                    </ul>
                    <p v-if="!staff" class="mt-1 text-sm text-ink-soft">數值代表的意義，請以醫師門診說明為準。</p>
                  </template>

                  <template v-else-if="e.event_type === 'NOTIFICATION'">
                    <p class="inline-flex rounded-full bg-mist px-2.5 py-0.5 text-sm font-bold">處理狀態：{{ e.detail.status_text }}</p>
                    <p v-if="staff && e.detail.recommended_action" class="mt-2 rounded-lg bg-care-soft/60 px-3 py-2 text-sm"><strong>建議處理：</strong>{{ e.detail.recommended_action }}</p>
                    <RouterLink
                      v-if="staff"
                      :to="{ name: 'nurse-notifications', query: { tab: e.detail.status === 'resolved' ? 'resolved' : e.detail.status === 'in_progress' ? 'in_progress' : 'pending', id: e.detail.notification_id } }"
                      class="mt-2 block text-sm font-medium text-care hover:underline"
                    >在通知中心開啟</RouterLink>
                  </template>

                  <template v-else-if="e.event_type === 'NOTIFICATION_STATUS'">
                    <p v-if="staff && e.detail.resolution_note" class="rounded-lg bg-ok-soft px-3 py-2 text-sm">
                      <strong>處理說明（內部）：</strong>{{ e.detail.resolution_note }}
                    </p>
                  </template>

                  <template v-else-if="e.event_type === 'NURSING_ASSESSMENT'">
                    <template v-if="staff">
                      <p class="text-sm text-ink-soft">
                        {{ e.detail.assessment_type_text }}・{{ personName(e.detail.assessed_by) }}・{{ e.detail.sign_status === 'signed' ? '已簽署' : '草稿' }}
                        <template v-if="e.detail.risk_level">・風險 {{ RISK_TEXT[e.detail.risk_level] ?? e.detail.risk_level }}</template>
                      </p>
                      <dl class="mt-1 space-y-1">
                        <div v-for="[k, l] in [['subjective', 'S'], ['objective', 'O'], ['assessment', 'A'], ['plan', 'P']]" v-show="e.detail[k]" :key="k" class="flex gap-2">
                          <dt class="w-5 shrink-0 font-bold text-ink-soft">{{ l }}</dt>
                          <dd class="min-w-0 break-words">{{ e.detail[k] }}</dd>
                        </div>
                      </dl>
                    </template>
                  </template>

                  <template v-else-if="e.event_type === 'APPOINTMENT'">
                    <p>{{ e.detail.appointment_type_text }}・{{ e.detail.status_text }}<template v-if="e.detail.duration_min">・約 {{ e.detail.duration_min }} 分鐘</template></p>
                    <p v-if="e.detail.rescheduled_to" class="mt-1 text-sm text-ink-soft">已改到 {{ localDate(e.detail.rescheduled_to.scheduled_at, timezone) }} {{ formatTime(e.detail.rescheduled_to.scheduled_at, timezone) }}</p>
                    <p v-if="e.detail.rescheduled_from" class="mt-1 text-sm text-ink-soft">由 {{ localDate(e.detail.rescheduled_from.scheduled_at, timezone) }} {{ formatTime(e.detail.rescheduled_from.scheduled_at, timezone) }} 改期</p>
                    <ul v-if="e.detail.instructions?.length" class="mt-1 space-y-0.5 text-sm">
                      <li v-for="(i, n) in e.detail.instructions" :key="n">・{{ i.text }}</li>
                    </ul>
                    <p v-if="staff && e.detail.notes" class="mt-1 text-sm">備註（內部）：{{ e.detail.notes }}</p>
                  </template>

                  <template v-else-if="e.event_type === 'CHEMOTHERAPY'">
                    <p v-if="e.detail.kind === 'medication'">
                      {{ e.detail.drug }} {{ e.detail.dose }}<template v-if="e.detail.route"> {{ e.detail.route }}</template>・{{ e.detail.administration_status_text }}
                      <template v-if="e.detail.infusion_duration_min">・輸注 {{ e.detail.infusion_duration_min }} 分鐘</template>
                    </p>
                    <p v-else>
                      {{ e.detail.regimen }}・第 {{ e.detail.cycle_number }}<template v-if="e.detail.total_cycles"> / {{ e.detail.total_cycles }}</template> 次療程
                    </p>
                    <p v-if="staff && e.detail.reaction_notes" class="mt-1 text-sm">反應紀錄：{{ e.detail.reaction_notes }}</p>
                    <p v-if="staff && e.detail.administered_by" class="mt-1 text-sm text-ink-soft">給藥：{{ personName(e.detail.administered_by) }}</p>
                  </template>
                </div>
              </div>
            </li>
          </ol>
        </li>
      </ol>

      <div v-if="entry?.meta?.has_more" class="mt-4 text-center">
        <button
          type="button"
          class="min-h-11 rounded-full border border-line px-5 font-medium text-care hover:bg-care-soft disabled:opacity-60"
          :disabled="entry.loadingMore"
          @click="store.loadMore(patientId)"
        >{{ entry.loadingMore ? '載入中…' : '載入較早的紀錄' }}</button>
        <p v-if="entry.error && items.length" class="mt-1 text-sm text-critical">{{ entry.error }}</p>
      </div>
    </div>
  </section>
</template>
