<script setup>
import { computed, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import SymptomField from '@/components/SymptomField.vue'
import WidgetFrame from '@/components/WidgetFrame.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { formatWhen } from '@/utils/format'

/**
 * symptom-quick-report widget: today's status plus the inline daily symptom report
 * (pain, nausea, fatigue, fever), rendered from GET /symptoms/forms/{code}.
 */
const props = defineProps({
  report: { type: Object, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
  patientId: { type: String, required: true },
  hotline: { type: Object, default: null },
})

const store = useDashboardStore()
const state = ref('idle') // idle | form | submitting | done
const answers = reactive({})
const showMissing = ref(false)
const submitError = ref(null)
const result = ref(null)
let idempotencyKey = null

const formCode = computed(() => props.report.form?.code)
const form = computed(() => store.forms[formCode.value])
const formError = computed(() => store.errors[`form:${formCode.value}`])
const items = computed(() => form.value?.items ?? [])
const missing = computed(() => items.value.filter((i) => i.is_required && answers[i.definition.code] == null))
const feverAnswered = computed(() =>
  items.value.some((i) => i.definition.code === 'fever' && answers.fever === true),
)

// Changing an answer makes it a different request, so it needs a new Idempotency-Key.
watch(answers, () => {
  idempotencyKey = null
  submitError.value = null
})

function newKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function open() {
  result.value = null
  submitError.value = null
  showMissing.value = false
  for (const k of Object.keys(answers)) delete answers[k]
  state.value = 'form'
  await store.fetchSymptomForm(formCode.value)
}

async function submit() {
  // Synchronous guard: a double tap fires twice before the disabled button re-renders.
  if (state.value === 'submitting') return
  if (missing.value.length) {
    showMissing.value = true
    return
  }
  idempotencyKey ??= newKey() // kept for retries of this exact submission
  state.value = 'submitting'
  submitError.value = null
  const values = items.value
    .filter((i) => answers[i.definition.code] != null)
    .map((i) => {
      const { code, value_type: type } = i.definition
      return type === 'boolean'
        ? { definition_code: code, value_boolean: answers[code] }
        : { definition_code: code, value_numeric: answers[code] }
    })
  try {
    result.value = await store.submitSymptomReport(
      props.patientId,
      { form_code: formCode.value, values },
      idempotencyKey,
    )
    idempotencyKey = null
    state.value = 'done'
  } catch (err) {
    if (!err.retryable) idempotencyKey = null
    submitError.value = err
    state.value = 'form'
  }
}

const ALERT_STYLE = {
  critical: 'border-critical bg-critical-soft text-critical',
  warning: 'border-warn/50 bg-warn-soft text-warn',
  info: 'border-line bg-care-soft text-care',
}
</script>

<template>
  <WidgetFrame title="今天的症狀" icon="clipboard" :state="report.form ? 'ready' : 'empty'" empty-text="目前沒有需要填寫的量表。">
    <!-- Status -->
    <div v-if="state === 'idle'" class="space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p v-if="report.reported_today" class="inline-flex items-center gap-2 text-lg font-bold text-ok">
            <AppIcon name="check" /> 今天已回報，謝謝您
          </p>
          <p v-else class="text-lg font-bold">今天還沒回報症狀</p>
          <p v-if="report.last_report" class="text-ink-soft">
            上次回報：{{ formatWhen(report.last_report.recorded_at, today, timezone) }}
          </p>
        </div>
        <button
          type="button"
          class="inline-flex min-h-12 items-center rounded-full px-6 font-bold"
          :class="report.reported_today ? 'border border-line text-care hover:bg-care-soft' : 'bg-care text-white hover:bg-care/90'"
          @click="open"
        >
          {{ report.reported_today ? '再次回報' : '開始回報' }}
        </button>
      </div>
      <p class="flex items-start gap-2 text-ink-soft">
        <AppIcon name="thermometer" :size="20" class="mt-0.5 shrink-0 text-critical" />
        化療期間體溫 38°C 以上或畏寒發抖，請不要等待，立即聯絡醫療團隊。
      </p>
    </div>

    <!-- Form -->
    <form v-else-if="state === 'form' || state === 'submitting'" novalidate @submit.prevent="submit">
      <p v-if="formError" class="text-critical">{{ formError }}</p>
      <div v-else-if="!form" class="space-y-3" aria-busy="true" aria-label="載入題目中">
        <div v-for="n in 3" :key="n" class="h-20 animate-pulse rounded-lg bg-mist" />
      </div>

      <template v-else>
        <p class="text-ink-soft">請依「過去 {{ form.recall_period_hours ?? 24 }} 小時」最嚴重的情形回答。</p>
        <div class="mt-4 space-y-6">
          <div v-for="item in items" :key="item.definition.code">
            <SymptomField
              v-model="answers[item.definition.code]"
              :definition="item.definition"
              :required="item.is_required"
              :invalid="showMissing && answers[item.definition.code] == null"
            />
            <p v-if="showMissing && item.is_required && answers[item.definition.code] == null" class="mt-1 font-medium text-critical">
              請回答這一題。
            </p>

            <!-- Fever reminder -->
            <div
              v-if="item.definition.code === 'fever' && feverAnswered"
              class="mt-3 rounded-xl border-2 border-critical bg-critical-soft p-4"
              role="alert"
            >
              <p class="flex items-start gap-2 font-bold text-critical">
                <AppIcon name="alert" class="mt-0.5 shrink-0" />
                化療期間發燒可能是嚴重感染，請不要等待。
              </p>
              <p class="mt-1">請先量體溫：38°C 以上請立刻撥打照護專線或前往急診。送出回報後，護理師也會收到通知。</p>
              <a
                v-if="hotline"
                :href="`tel:${hotline.phone}`"
                class="mt-3 flex min-h-12 items-center justify-center gap-2 rounded-full bg-critical font-bold text-white hover:bg-critical/90"
              >
                <AppIcon name="phone" /> 撥打 {{ hotline.phone }}
              </a>
            </div>
          </div>
        </div>

        <p v-if="showMissing && missing.length" class="mt-5 font-medium text-critical" role="alert">
          還有 {{ missing.length }} 題沒有回答。
        </p>
        <div v-if="submitError" class="mt-5 rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">
          <p class="font-medium">{{ submitError.message }}</p>
          <ul v-if="submitError.details.length" class="mt-1 list-disc pl-5 text-sm">
            <li v-for="d in submitError.details" :key="d.field + d.issue">{{ d.field }}：{{ d.issue }}</li>
          </ul>
        </div>

        <div class="mt-6 flex flex-wrap gap-3">
          <button
            type="submit"
            class="min-h-12 flex-1 rounded-full bg-care px-6 text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
            :disabled="state === 'submitting'"
          >
            {{ state === 'submitting' ? '送出中…' : '送出回報' }}
          </button>
          <button
            type="button"
            class="min-h-12 rounded-full border border-line px-6 font-medium hover:bg-mist"
            :disabled="state === 'submitting'"
            @click="state = 'idle'"
          >
            取消
          </button>
        </div>
      </template>
    </form>

    <!-- Result -->
    <div v-else-if="state === 'done' && result" aria-live="polite">
      <p class="inline-flex items-center gap-2 text-lg font-bold text-ok">
        <AppIcon name="check" /> 已送出，謝謝您
      </p>
      <p class="text-ink-soft">
        {{ result.values.filter((v) => v.definition_code !== 'fever').map((v) => `${v.label} ${v.score}`).join('、') }}
      </p>

      <ul v-if="result.triggered_alerts.length" class="mt-4 space-y-3">
        <li
          v-for="a in result.triggered_alerts"
          :key="a.alert_rule_code"
          class="rounded-xl border px-4 py-3"
          :class="ALERT_STYLE[a.severity] ?? ALERT_STYLE.info"
          :role="a.severity === 'critical' ? 'alert' : undefined"
        >
          <p class="flex items-start gap-2 font-bold">
            <AppIcon name="alert" :size="20" class="mt-0.5 shrink-0" />{{ a.symptom }}
          </p>
          <p class="mt-1 text-ink">{{ a.message }}</p>
          <a
            v-if="a.severity === 'critical' && hotline"
            :href="`tel:${hotline.phone}`"
            class="mt-3 flex min-h-12 items-center justify-center gap-2 rounded-full bg-critical font-bold text-white hover:bg-critical/90"
          >
            <AppIcon name="phone" /> 撥打 {{ hotline.phone }}
          </a>
        </li>
      </ul>

      <button type="button" class="mt-4 min-h-12 rounded-full border border-line px-6 font-medium hover:bg-mist" @click="state = 'idle'">
        完成
      </button>
    </div>
  </WidgetFrame>
</template>
