<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import HealthRecordsTabs from '@/components/HealthRecordsTabs.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import VitalInput from '@/components/VitalInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { formatWhen } from '@/utils/format'
import { BP_SITES, parseNumber, TEMPERATURE_SITES, validateVital, VITAL_FIELDS, vitalHint } from '@/utils/vitals'

/** Patient vital-sign entry (POST /api/v1/vital-signs). */
const auth = useAuthStore()
const store = useDashboardStore()
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const dashboard = computed(() => store.patients[patientId.value])
const ranges = computed(() => store.referenceRanges)
const hotline = computed(() => dashboard.value?.widgets['today-schedule']?.quick_contact)
const today = computed(() => dashboard.value?.widgets['today-schedule']?.date ?? '')

const FIELDS = ['temperature_c', 'heart_rate_bpm', 'systolic_bp_mmhg', 'diastolic_bp_mmhg', 'spo2_pct', 'respiratory_rate', 'weight_kg']
const blank = () => Object.fromEntries(FIELDS.map((f) => [f, '']))
const form = reactive({ ...blank(), temperature_site: 'ear', bp_measure_site: 'left_arm', when: 'now', measuredLocal: '' })
const state = ref('form') // form | submitting | done
const showErrors = ref(false)
const submitError = ref(null)
const result = ref(null)
let idempotencyKey = null

// Limb restrictions from the patient's care alerts, e.g. 右手禁止注射及量血壓
const restrictions = computed(() =>
  (dashboard.value?.widgets['patient-summary']?.care_alerts ?? []).filter((a) => a.alert_type === 'limb_restriction' && a.body_site),
)
const restrictedSites = computed(() => new Set(restrictions.value.map((a) => a.body_site)))
const pickedRestricted = computed(() => restrictedSites.value.has(form.bp_measure_site))

watch(restrictedSites, (sites) => {
  // default the arm away from a restricted limb
  if (sites.has(form.bp_measure_site)) form.bp_measure_site = Object.keys(BP_SITES).find((s) => !sites.has(s)) ?? form.bp_measure_site
}, { immediate: true })

const values = computed(() => Object.fromEntries(FIELDS.map((f) => [f, parseNumber(form[f])])))
const fieldErrors = computed(() => {
  const e = Object.fromEntries(FIELDS.map((f) => [f, validateVital(f, values.value[f])]))
  const { systolic_bp_mmhg: s, diastolic_bp_mmhg: d } = values.value
  if (!e.systolic_bp_mmhg && !e.diastolic_bp_mmhg) {
    if ((s === null) !== (d === null)) e[s === null ? 'systolic_bp_mmhg' : 'diastolic_bp_mmhg'] = '血壓請同時填寫收縮壓與舒張壓'
    else if (s !== null && s <= d) e.systolic_bp_mmhg = '收縮壓應大於舒張壓'
  }
  return e
})
const hints = computed(() =>
  Object.fromEntries(FIELDS.map((f) => [f, fieldErrors.value[f] ? null : vitalHint(f, values.value[f], ranges.value)])),
)
const hasAny = computed(() => FIELDS.some((f) => values.value[f] !== null))
const hasErrors = computed(() => FIELDS.some((f) => fieldErrors.value[f]))
const feverCritical = computed(() => hints.value.temperature_c?.level === 'critical')

const nowLocal = () => {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}
const minLocal = computed(() => {
  const d = new Date(Date.now() - 24 * 3600 * 1000)
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
})

watch(form, () => {
  idempotencyKey = null // any change makes it a new request
  submitError.value = null
})

function newKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function payload() {
  const body = {}
  for (const f of FIELDS) if (values.value[f] !== null) body[f] = values.value[f]
  if (body.temperature_c != null) body.temperature_site = form.temperature_site
  if (body.systolic_bp_mmhg != null) body.bp_measure_site = form.bp_measure_site
  if (form.when === 'earlier' && form.measuredLocal) body.measured_at = new Date(form.measuredLocal).toISOString()
  return body
}

async function submit() {
  if (state.value === 'submitting') return
  showErrors.value = true
  if (!hasAny.value || hasErrors.value) return
  idempotencyKey ??= newKey()
  state.value = 'submitting'
  try {
    result.value = await store.submitVitalSigns(patientId.value, payload(), idempotencyKey)
    idempotencyKey = null
    state.value = 'done'
    window.scrollTo({ top: 0 })
  } catch (err) {
    if (!err.retryable) idempotencyKey = null
    submitError.value = err
    state.value = 'form'
  }
}

function again() {
  Object.assign(form, blank(), { when: 'now', measuredLocal: '' })
  showErrors.value = false
  result.value = null
  state.value = 'form'
}

onMounted(() => {
  store.fetchReferenceRanges()
  store.fetchSettings()
  if (!dashboard.value) store.fetchPatientDashboard(patientId.value)
})

const unread = computed(() => dashboard.value?.widgets.notifications.unread_count ?? 0)
const LEVEL_BOX = { critical: 'border-critical bg-critical-soft text-critical', warning: 'border-warn/50 bg-warn-soft text-warn' }
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">記錄生命徵象</h1>
      </div>
      <HealthRecordsTabs active="patient-vitals" />
    </header>

    <main class="mx-auto max-w-xl px-4 pt-4">
      <!-- Result -->
      <section v-if="state === 'done' && result" class="space-y-4" aria-live="polite">
        <div class="rounded-2xl border border-line bg-surface p-5">
          <p class="inline-flex items-center gap-2 text-xl font-bold text-ok"><AppIcon name="check" /> 已記錄</p>
          <p class="mt-1 text-ink-soft">{{ formatWhen(result.measured_at, today || result.measured_at.slice(0, 10)) }} 量測</p>
          <ul class="mt-3 flex flex-wrap gap-2">
            <li v-for="f in FIELDS.filter((x) => result[x] != null)" :key="f" class="rounded-lg bg-mist px-3 py-1">
              {{ VITAL_FIELDS[f].label }} <strong>{{ result[f] }}</strong> {{ VITAL_FIELDS[f].unit }}
            </li>
          </ul>
        </div>

        <div v-for="t in result.triggered_alerts" :key="t.alert_rule_code" class="rounded-2xl border-2 p-5"
             :class="LEVEL_BOX[t.severity] ?? LEVEL_BOX.warning" :role="t.severity === 'critical' ? 'alert' : undefined">
          <p class="flex items-start gap-2 text-lg font-bold"><AppIcon name="alert" class="mt-0.5 shrink-0" />{{ t.label }} {{ t.value }}</p>
          <p class="mt-1 text-ink">{{ t.message }}</p>
          <a v-if="t.severity === 'critical' && hotline" :href="`tel:${hotline.phone}`"
             class="mt-3 flex min-h-12 items-center justify-center gap-2 rounded-full bg-critical font-bold text-white hover:bg-critical/90">
            <AppIcon name="phone" /> 撥打 {{ hotline.phone }}
          </a>
        </div>

        <ul v-if="result.flags.filter((f) => !result.triggered_alerts.some((t) => t.field === f.field)).length" class="space-y-2">
          <li v-for="f in result.flags.filter((x) => !result.triggered_alerts.some((t) => t.field === x.field))" :key="f.field"
              class="rounded-xl border px-4 py-3 font-medium" :class="LEVEL_BOX[f.level]">{{ f.message }}</li>
        </ul>
        <p v-for="w in result.warnings" :key="w.code" class="rounded-xl bg-warn-soft px-4 py-3 font-medium text-warn">{{ w.message }}</p>

        <div class="flex flex-wrap gap-3">
          <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 flex-1 items-center justify-center rounded-full bg-care px-6 font-bold text-white hover:bg-care/90">回首頁</RouterLink>
          <button type="button" class="min-h-12 rounded-full border border-line px-6 font-medium hover:bg-mist" @click="again">再記錄一筆</button>
        </div>
      </section>

      <!-- Form -->
      <form v-else class="space-y-4" novalidate @submit.prevent="submit">
        <p class="text-ink-soft">量完就填，沒有量的項目可以空著。</p>

        <fieldset class="rounded-2xl border border-line bg-surface p-5">
          <legend class="sr-only">體溫</legend>
          <VitalInput v-model="form.temperature_c" field="temperature_c" :error="showErrors ? fieldErrors.temperature_c : null" :hint="hints.temperature_c" />
          <div class="mt-1 flex flex-wrap gap-2" role="radiogroup" aria-label="量測方式">
            <label v-for="(label, key) in TEMPERATURE_SITES" :key="key" class="relative">
              <input v-model="form.temperature_site" type="radio" :value="key" class="peer sr-only" />
              <span class="grid min-h-10 cursor-pointer place-items-center rounded-full border px-4 peer-focus-visible:outline-3 peer-focus-visible:outline-care"
                    :class="form.temperature_site === key ? 'border-care bg-care font-bold text-white' : 'border-line hover:bg-care-soft'">{{ label }}</span>
            </label>
          </div>
          <div v-if="feverCritical" class="mt-4 rounded-xl border-2 border-critical bg-critical-soft p-4" role="alert">
            <p class="flex items-start gap-2 font-bold text-critical"><AppIcon name="alert" class="mt-0.5 shrink-0" />化療期間發燒可能是嚴重感染，請不要等待。</p>
            <p class="mt-1">請立刻撥打照護專線或前往急診。儲存後護理師也會收到通知。</p>
            <a v-if="hotline" :href="`tel:${hotline.phone}`" class="mt-3 flex min-h-12 items-center justify-center gap-2 rounded-full bg-critical font-bold text-white hover:bg-critical/90">
              <AppIcon name="phone" /> 撥打 {{ hotline.phone }}
            </a>
          </div>
        </fieldset>

        <fieldset class="rounded-2xl border border-line bg-surface p-5">
          <legend class="sr-only">血壓</legend>
          <div class="grid grid-cols-2 gap-3">
            <VitalInput v-model="form.systolic_bp_mmhg" field="systolic_bp_mmhg" label="收縮壓（上）" :error="showErrors ? fieldErrors.systolic_bp_mmhg : null" :hint="hints.systolic_bp_mmhg" />
            <VitalInput v-model="form.diastolic_bp_mmhg" field="diastolic_bp_mmhg" label="舒張壓（下）" :error="showErrors ? fieldErrors.diastolic_bp_mmhg : null" :hint="hints.diastolic_bp_mmhg" />
          </div>
          <p class="mt-1 font-medium">量測部位</p>
          <div class="mt-1 flex flex-wrap gap-2" role="radiogroup" aria-label="量測部位">
            <label v-for="(label, key) in BP_SITES" :key="key" class="relative">
              <input v-model="form.bp_measure_site" type="radio" :value="key" class="peer sr-only" />
              <span class="grid min-h-10 cursor-pointer place-items-center rounded-full border px-4 peer-focus-visible:outline-3 peer-focus-visible:outline-care"
                    :class="form.bp_measure_site === key
                      ? (restrictedSites.has(key) ? 'border-critical bg-critical font-bold text-white' : 'border-care bg-care font-bold text-white')
                      : 'border-line hover:bg-care-soft'">{{ label }}{{ restrictedSites.has(key) ? '（禁止）' : '' }}</span>
            </label>
          </div>
          <p v-for="r in restrictions" :key="r.description" class="mt-2 flex items-start gap-1.5 text-sm"
             :class="pickedRestricted ? 'font-bold text-critical' : 'text-ink-soft'">
            <AppIcon name="hand" :size="18" class="shrink-0" />
            {{ r.description }}{{ pickedRestricted ? '，請改量另一側。' : '' }}
          </p>
        </fieldset>

        <fieldset class="grid grid-cols-2 gap-3 rounded-2xl border border-line bg-surface p-5">
          <legend class="sr-only">心跳與血氧</legend>
          <VitalInput v-model="form.heart_rate_bpm" field="heart_rate_bpm" :error="showErrors ? fieldErrors.heart_rate_bpm : null" :hint="hints.heart_rate_bpm" />
          <VitalInput v-model="form.spo2_pct" field="spo2_pct" :error="showErrors ? fieldErrors.spo2_pct : null" :hint="hints.spo2_pct" />
          <VitalInput v-model="form.respiratory_rate" field="respiratory_rate" label="呼吸（選填）" :error="showErrors ? fieldErrors.respiratory_rate : null" :hint="hints.respiratory_rate" />
          <VitalInput v-model="form.weight_kg" field="weight_kg" :error="showErrors ? fieldErrors.weight_kg : null" :hint="hints.weight_kg" />
        </fieldset>

        <fieldset class="rounded-2xl border border-line bg-surface p-5">
          <legend class="sr-only">量測時間</legend>
          <p class="font-medium" aria-hidden="true">量測時間</p>
          <div class="mt-1 flex flex-wrap gap-2">
            <label v-for="(label, key) in { now: '剛剛量的', earlier: '稍早量的' }" :key="key" class="relative">
              <input v-model="form.when" type="radio" :value="key" class="peer sr-only" @change="key === 'earlier' && !form.measuredLocal && (form.measuredLocal = nowLocal())" />
              <span class="grid min-h-10 cursor-pointer place-items-center rounded-full border px-4 peer-focus-visible:outline-3 peer-focus-visible:outline-care"
                    :class="form.when === key ? 'border-care bg-care font-bold text-white' : 'border-line hover:bg-care-soft'">{{ label }}</span>
            </label>
          </div>
          <input v-if="form.when === 'earlier'" v-model="form.measuredLocal" type="datetime-local" :min="minLocal" :max="nowLocal()"
                 aria-label="量測時間" class="mt-3 min-h-12 w-full rounded-xl border border-line bg-surface px-3" />
        </fieldset>

        <p v-if="showErrors && !hasAny" class="font-medium text-critical" role="alert">請至少填寫一項量測數值。</p>
        <div v-if="submitError" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">
          <p class="font-medium">{{ submitError.message }}</p>
          <ul v-if="submitError.details?.length" class="mt-1 list-disc pl-5 text-sm">
            <li v-for="d in submitError.details" :key="d.field + d.issue">{{ VITAL_FIELDS[d.field]?.label ?? d.field }}：{{ d.issue }}</li>
          </ul>
        </div>

        <button type="submit" class="min-h-14 w-full rounded-full bg-care text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
                :disabled="state === 'submitting'">{{ state === 'submitting' ? '儲存中…' : '儲存' }}</button>
      </form>
    </main>

    <PatientBottomNav active="vitals" :unread="unread" />
  </div>
</template>
