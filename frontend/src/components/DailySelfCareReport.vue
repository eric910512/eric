<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import SymptomField from '@/components/SymptomField.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { formatTime, localDate } from '@/utils/format'

/**
 * 每日症狀與自我照護回報 (form rt_daily_report), rendered from GET /symptoms/forms/rt_daily_report:
 * the questions grouped into their sections (symptom categories), 0–10 scales and single choices in
 * the defined order. Choosing 「沒有」 on a self-care question shows a reminder but never blocks
 * submitting. One report per day: the backend rejects a second one (409 ALREADY_REPORTED_TODAY) and
 * this card shows 今天已回報 instead of the form.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})
const emit = defineEmits(['submitted'])

const FORM_CODE = 'rt_daily_report'
const SELF_CARE = 'daily_self_care'
const REMINDER = '這是每天重要的自我照護，請記得確實做到。'

const dashboard = useDashboardStore()
const portal = usePatientPortalStore()
const state = ref('loading') // loading | idle | form | submitting | done
const answers = reactive({})
const showMissing = ref(false)
const submitError = ref(null)
let idempotencyKey = null

const form = computed(() => dashboard.forms[FORM_CODE])
const formError = computed(() => dashboard.errors[`form:${FORM_CODE}`] || portal.errors[`latest:${FORM_CODE}`])
const latest = computed(() => portal.latestReports[FORM_CODE] ?? null)
// the patient's real calendar day (the backend counts reports per local day in the patient's timezone)
const today = () => localDate(new Date().toISOString(), props.timezone)
const reportedToday = computed(() => !!latest.value && localDate(latest.value.recorded_at, props.timezone) === today())
const sections = computed(() => {
  const out = []
  for (const item of form.value?.items ?? []) {
    const cat = item.definition.category ?? { code: 'other', name_zh: '' }
    let section = out.find((s) => s.code === cat.code)
    if (!section) out.push((section = { code: cat.code, title: cat.name_zh, items: [] }))
    section.items.push(item)
  }
  return out
})
const items = computed(() => form.value?.items ?? [])
const missing = computed(() => items.value.filter((i) => i.is_required && answers[i.definition.code] == null))
const hint = (item) => (item.definition.category?.code === SELF_CARE && answers[item.definition.code] === 'no' ? REMINDER : '')

watch(answers, () => {
  idempotencyKey = null
  submitError.value = null
})

async function load() {
  await Promise.all([dashboard.fetchSymptomForm(FORM_CODE), portal.fetchLatestReport(props.patientId, FORM_CODE)])
  state.value = reportedToday.value ? 'done' : 'idle'
}

function open() {
  for (const k of Object.keys(answers)) delete answers[k]
  showMissing.value = false
  submitError.value = null
  state.value = 'form'
}

async function submit() {
  if (state.value === 'submitting') return
  if (missing.value.length) {
    showMissing.value = true
    return
  }
  idempotencyKey ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  state.value = 'submitting'
  const values = items.value.map((i) => {
    const { code, value_type: type } = i.definition
    return type === 'single_choice' ? { definition_code: code, option_code: answers[code] } : { definition_code: code, value_numeric: answers[code] }
  })
  try {
    await dashboard.submitSymptomReport(props.patientId, { form_code: FORM_CODE, values }, idempotencyKey)
    idempotencyKey = null
    await portal.fetchLatestReport(props.patientId, FORM_CODE)
    state.value = 'done'
    emit('submitted')
  } catch (err) {
    if (!err.retryable) idempotencyKey = null
    if (err.code === 'ALREADY_REPORTED_TODAY') {
      await portal.fetchLatestReport(props.patientId, FORM_CODE)
      state.value = 'done'
      return
    }
    submitError.value = err
    state.value = 'form'
  }
}

onMounted(load)
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="daily-report-title" data-daily-report :data-state="state">
    <h2 id="daily-report-title" class="text-lg font-bold">每日症狀與自我照護回報</h2>
    <p v-if="formError" class="mt-2 text-critical" role="alert">{{ formError.message }}</p>

    <div v-else-if="state === 'loading'" class="mt-2 h-16 animate-pulse rounded-xl bg-mist" aria-busy="true" />

    <template v-else-if="state === 'done'">
      <p class="mt-2 inline-flex items-center gap-2 text-lg font-bold text-ok" data-daily-done>
        <AppIcon name="check" /> 今天已回報
      </p>
      <p v-if="latest" class="text-ink-soft">回報時間：{{ localDate(latest.recorded_at, timezone) }} {{ formatTime(latest.recorded_at, timezone) }}，明天可以再回報。</p>
    </template>

    <template v-else-if="state === 'idle'">
      <p class="mt-1 text-ink-soft">每天一次，請回想過去 24 小時的情形，並記錄今天的自我照護。</p>
      <button type="button" class="mt-3 min-h-12 w-full rounded-full bg-care px-6 text-lg font-bold text-white hover:bg-care/90" data-daily-open @click="open">開始回報</button>
    </template>

    <form v-else class="mt-3 space-y-6" novalidate data-daily-form @submit.prevent="submit">
      <section v-for="s in sections" :key="s.code" class="space-y-5" :data-section="s.code">
        <h3 class="rounded-lg bg-care-soft px-3 py-2 text-lg font-bold text-care">{{ s.title }}</h3>
        <div v-for="(item, n) in s.items" :key="item.definition.code" :data-question="item.definition.code">
          <p class="text-sm text-ink-soft">{{ n + 1 }}.</p>
          <SymptomField
            v-model="answers[item.definition.code]"
            :definition="item.definition"
            :required="item.is_required"
            :invalid="showMissing && answers[item.definition.code] == null"
            :hint="hint(item)"
          />
          <p v-if="showMissing && answers[item.definition.code] == null" class="mt-1 text-critical">請回答這一題</p>
        </div>
      </section>
      <p v-if="showMissing && missing.length" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-daily-missing>
        還有 {{ missing.length }} 題沒有回答。
      </p>
      <p v-if="submitError" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-daily-error>{{ submitError.message }}</p>
      <div class="flex flex-wrap gap-2">
        <button type="submit" class="min-h-12 flex-1 rounded-full bg-care px-6 text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
          :disabled="state === 'submitting'" data-daily-submit>{{ state === 'submitting' ? '送出中…' : '送出' }}</button>
        <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-mist" @click="state = 'idle'">取消</button>
      </div>
    </form>
  </section>
</template>
