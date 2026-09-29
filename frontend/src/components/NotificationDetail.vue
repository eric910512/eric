<script setup>
import { computed, nextTick, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import NoteComposer from '@/components/NoteComposer.vue'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen } from '@/utils/format'
import { BP_SITES, TEMPERATURE_SITES, VITAL_FIELDS } from '@/utils/vitals'

/**
 * One alert in the Notification Center (GET /notifications/{id}): patient, trigger, original
 * record, recommended action, handling steps, and the next lifecycle action.
 */
const props = defineProps({
  notificationId: { type: Number, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
  focusNote: { type: Boolean, default: false }, // opened from 「完成處理」 in the list
})
const emit = defineEmits(['close'])

const store = useNurseStore()
const d = computed(() => store.details[props.notificationId])
const loadError = computed(() => store.errors[`detail:${props.notificationId}`])

const note = ref('')
const busy = ref(false)
const error = ref('')
const noteInvalid = ref(false)
const noteBox = ref(null)

watch(() => props.notificationId, (id) => {
  note.value = ''
  error.value = ''
  noteInvalid.value = false
  store.fetchNotificationDetail(id)
}, { immediate: true })
watch([() => props.focusNote, d], async ([focus, detail]) => {
  if (focus && detail?.allowed_actions.includes('resolve')) {
    await nextTick()
    noteBox.value?.querySelector('textarea')?.focus()
  }
})

const SEVERITY = {
  critical: { label: '危急', chip: 'bg-critical text-white', ink: 'text-critical' },
  warning: { label: '注意', chip: 'bg-warn-soft text-warn', ink: 'text-warn' },
  info: { label: '資訊', chip: 'bg-care-soft text-care', ink: 'text-care' },
}
const STEPS = [
  { key: 'created', label: '收到通知' },
  { key: 'acknowledged', label: '接手' },
  { key: 'started', label: '處理中' },
  { key: 'resolved', label: '完成' },
]
const ACTIONS = {
  acknowledge: { label: '接手這則警示', done: '已接手' },
  start: { label: '開始處理', done: '已開始處理' },
  resolve: { label: '完成處理', done: '已完成' },
}

const steps = computed(() => STEPS.map((s) => {
  const h = s.key === 'created' ? { at: d.value.created_at } : d.value.handling?.[s.key]
  return { ...s, at: h?.at ?? null, by: h?.by?.display_name ?? null }
}))
const next = computed(() => d.value?.allowed_actions?.[0] ?? null)

const sourceRows = computed(() => {
  const r = d.value?.source_record
  if (!r) return []
  if (r.table === 'vital_signs') {
    return Object.entries(VITAL_FIELDS)
      .filter(([k]) => r.values[k] != null)
      .map(([k, f]) => ({
        label: f.label,
        value: `${Number(r.values[k]).toFixed(f.decimals)} ${f.unit}`,
        note: k === 'temperature_c' ? TEMPERATURE_SITES[r.values.temperature_site] : k === 'systolic_bp_mmhg' ? BP_SITES[r.values.bp_measure_site] : null,
      }))
  }
  if (r.table === 'symptom_records') {
    return r.values.map((v) => ({
      label: v.label,
      value: v.value_boolean != null ? (v.value_boolean ? '有' : '沒有') : `${v.score ?? v.value_numeric} / 10`,
    }))
  }
  if (r.table === 'lab_results') {
    return [{ label: r.name_zh, value: `${r.value} ${r.unit}`, note: r.ref_low != null ? `參考 ${r.ref_low}–${r.ref_high}` : null }]
  }
  return []
})
const sourceTitle = computed(() => ({
  vital_signs: ['生命徵象', 'measured_at'],
  symptom_records: ['症狀回報', 'recorded_at'],
  lab_results: ['檢驗結果', 'collected_at'],
})[d.value?.source_record?.table] ?? ['原始資料', null])

async function run(action) {
  if (busy.value) return
  error.value = ''
  if (action === 'resolve' && !note.value.trim()) {
    noteInvalid.value = true
    error.value = '請填寫處理說明，完成後才會關閉這則警示。'
    return
  }
  busy.value = true
  try {
    await store.transition(props.notificationId, action, action === 'resolve' ? note.value.trim() : null)
    note.value = ''
  } catch (err) {
    error.value = err.message
    if (err.code === 'INVALID_TRANSITION') store.fetchNotificationDetail(props.notificationId) // someone else moved it
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="rounded-2xl border bg-surface" :class="d?.severity === 'critical' && d?.status !== 'resolved' ? 'border-critical/60' : 'border-line'" aria-label="通知內容">
    <p v-if="loadError" class="p-5 text-critical">{{ loadError }}</p>
    <div v-else-if="!d" class="space-y-3 p-5" aria-busy="true" aria-label="載入中">
      <div v-for="n in 4" :key="n" class="h-8 animate-pulse rounded bg-mist" />
    </div>

    <template v-else>
      <header class="border-b border-line px-5 py-4">
        <div class="flex items-start gap-3">
          <div class="min-w-0 flex-1">
            <p class="flex flex-wrap items-center gap-2">
              <span class="rounded-full px-2.5 py-0.5 text-sm font-bold" :class="SEVERITY[d.severity]?.chip">{{ SEVERITY[d.severity]?.label }}</span>
              <span class="text-sm text-ink-soft">{{ formatWhen(d.created_at, today, timezone) }}</span>
            </p>
            <h2 class="mt-1 text-xl font-bold" :class="d.status !== 'resolved' ? SEVERITY[d.severity]?.ink : ''">{{ d.title }}</h2>
            <p class="mt-0.5">{{ d.message }}</p>
          </div>
          <button type="button" class="-mr-2 min-h-11 shrink-0 rounded-full px-3 text-ink-soft hover:bg-mist lg:hidden" @click="emit('close')">返回列表</button>
        </div>

        <!-- handling steps (a real sequence) -->
        <ol class="mt-4 grid grid-cols-4 gap-1" aria-label="處理進度">
          <li v-for="(s, i) in steps" :key="s.key" class="min-w-0">
            <span class="block h-1.5 rounded-full" :class="s.at ? (d.status === 'resolved' ? 'bg-ok' : 'bg-care') : 'bg-mist'" />
            <span class="mt-1.5 flex items-center gap-1 text-sm font-medium" :class="s.at ? '' : 'text-ink-soft'">
              <span class="grid size-5 shrink-0 place-items-center rounded-full text-xs" :class="s.at ? 'bg-care text-white' : 'bg-mist'">{{ i + 1 }}</span>
              {{ s.label }}
            </span>
            <span v-if="s.at" class="block truncate text-xs text-ink-soft">{{ s.by ?? '' }} {{ formatWhen(s.at, today, timezone) }}</span>
          </li>
        </ol>
      </header>

      <div class="space-y-5 px-5 py-4">
        <!-- patient -->
        <div v-if="d.patient" class="rounded-xl bg-mist px-4 py-3">
          <p class="flex flex-wrap items-baseline gap-x-2">
            <span class="text-lg font-bold">{{ d.patient.display_name }}</span>
            <span class="text-ink-soft">{{ d.patient.patient_code }}</span>
            <RouterLink :to="{ name: 'nurse', params: { patientId: d.patient.id } }" class="ml-auto text-sm font-medium text-care hover:underline">病人資料</RouterLink>
          </p>
          <p class="text-sm">
            {{ d.patient.diagnoses?.join('、') }}<template v-if="d.patient.current_cycle">，第 {{ d.patient.current_cycle.cycle_number }} 次療程第 {{ d.patient.current_cycle.cycle_day }} 天</template>
            <span v-if="d.patient.current_cycle?.in_nadir" class="ml-1 rounded-full bg-nadir-soft px-2 text-xs font-bold text-nadir">骨髓抑制期</span>
          </p>
          <p v-if="d.patient.care_alerts?.length" class="mt-1 flex flex-wrap gap-1.5">
            <span v-for="a in d.patient.care_alerts" :key="a.description" class="rounded-full bg-warn-soft px-2 text-xs font-bold text-warn">{{ a.description }}</span>
          </p>
        </div>

        <!-- trigger -->
        <div v-if="d.trigger">
          <h3 class="font-bold">觸發原因</h3>
          <p class="mt-1">
            <strong class="text-lg" :class="SEVERITY[d.severity]?.ink">{{ d.trigger.label }} {{ d.trigger.value }}</strong>
            <span class="ml-2 text-sm text-ink-soft">規則：{{ d.trigger.condition }}</span>
          </p>
        </div>

        <!-- original record -->
        <div v-if="d.source_record">
          <h3 class="font-bold">
            原始{{ sourceTitle[0] }}
            <span v-if="sourceTitle[1]" class="ml-1 text-sm font-normal text-ink-soft">
              {{ formatWhen(d.source_record[sourceTitle[1]], today, timezone) }}<template v-if="d.source_record.cycle_day">，療程第 {{ d.source_record.cycle_day }} 天</template>
            </span>
          </h3>
          <dl class="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div v-for="r in sourceRows" :key="r.label" class="rounded-lg border border-line px-3 py-2">
              <dt class="text-sm text-ink-soft">{{ r.label }}</dt>
              <dd class="font-bold">{{ r.value }} <span v-if="r.note" class="text-xs font-normal text-ink-soft">{{ r.note }}</span></dd>
            </div>
          </dl>
        </div>

        <!-- recommended action -->
        <div v-if="d.recommended_action" class="rounded-xl border border-care/30 bg-care-soft/60 px-4 py-3">
          <h3 class="flex items-center gap-1.5 font-bold text-care"><AppIcon name="clipboard" :size="18" /> 建議處理</h3>
          <p class="mt-1">{{ d.recommended_action }}</p>
        </div>

        <!-- resolution (internal) -->
        <div v-if="d.status === 'resolved'" class="rounded-xl bg-ok-soft px-4 py-3">
          <p class="flex items-center gap-1.5 font-bold text-ok">
            <AppIcon name="check" :size="18" /> 已完成：{{ d.handling.resolved?.by?.display_name }}，{{ formatWhen(d.handling.resolved?.at, today, timezone) }}
          </p>
          <p class="mt-1">{{ d.handling.resolution_note }}</p>
          <p class="mt-1 text-xs text-ink-soft">處理說明只有醫療團隊看得到；病人只會看到「已處理完成」。</p>
        </div>

        <!-- next action -->
        <div v-else-if="next" class="border-t border-line pt-4">
          <div v-if="next === 'resolve'" ref="noteBox">
            <NoteComposer
              v-model="note"
              label="處理說明（內部紀錄，病人看不到內容）"
              placeholder="例如：已電話聯繫病人，建議立即至急診"
              :invalid="noteInvalid"
              @update:model-value="noteInvalid = false; error = ''"
            />
          </div>
          <p v-else class="text-sm text-ink-soft">
            {{ next === 'acknowledge' ? '接手後，其他護理師會看到由您負責這則警示。' : '開始處理後，病人會看到「護理師正在處理」。' }}
          </p>
          <button
            type="button"
            class="mt-3 inline-flex min-h-12 items-center gap-2 rounded-full px-6 text-lg font-bold text-white disabled:opacity-60"
            :class="next === 'resolve' ? 'bg-ok hover:bg-ok/90' : d.severity === 'critical' ? 'bg-critical hover:bg-critical/90' : 'bg-care hover:bg-care/90'"
            :disabled="busy"
            :data-action="next"
            @click="run(next)"
          >
            <AppIcon :name="next === 'resolve' ? 'check' : 'hand'" :size="20" />
            {{ busy ? '儲存中…' : ACTIONS[next].label }}
          </button>
          <p v-if="error" class="mt-2 font-medium text-critical" role="alert">{{ error }}</p>
        </div>
      </div>
    </template>
  </section>
</template>
