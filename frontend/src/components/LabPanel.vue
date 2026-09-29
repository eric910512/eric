<script setup>
import { computed, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen } from '@/utils/format'

/**
 * Nurse lab panel: latest WBC / ANC / Hb / PLT with reference ranges, flags, trend and
 * alert status (GET /labs/results/{id}), history by collection, and panel entry
 * (POST /labs/results, Idempotency-Key kept across retries of the same entry).
 */
const props = defineProps({
  patientId: { type: String, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useNurseStore()
const data = computed(() => store.labs[props.patientId])
const error = computed(() => store.errors[`labs:${props.patientId}`])

const FLAG = {
  LL: { text: '嚴重偏低', cls: 'bg-critical text-white', value: 'text-critical' },
  HH: { text: '嚴重偏高', cls: 'bg-critical text-white', value: 'text-critical' },
  L: { text: '偏低', cls: 'bg-warn-soft text-warn', value: 'text-warn' },
  H: { text: '偏高', cls: 'bg-warn-soft text-warn', value: 'text-warn' },
  N: { text: '正常', cls: 'bg-ok-soft text-ok', value: '' },
}
// Plausible input ranges and precision (same as the API; rejects typos, not a clinical judgement)
const INPUT = { WBC: [0, 500, 2], ANC: [0, 200, 2], HGB: [1, 25, 1], PLT: [0, 3000, 0] }
const SHORT = { WBC: 'WBC', ANC: 'ANC', HGB: 'Hb', PLT: 'PLT' }

const types = computed(() => data.value?.test_types ?? [])
const RANGE_DECIMALS = { WBC: 1, ANC: 1, HGB: 1, PLT: 0 }
const num = (code, v) => (RANGE_DECIMALS[code] != null ? v.toFixed(RANGE_DECIMALS[code]) : String(v))
const range = (t, code = t.code ?? t.test_code) =>
  (t.ref_low != null && t.ref_high != null ? `${num(code, t.ref_low)}–${num(code, t.ref_high)}` : '—')
const criticalText = (t) => [t.critical_low != null && `≤${num(t.code, t.critical_low)}`, t.critical_high != null && `≥${num(t.code, t.critical_high)}`].filter(Boolean).join('、')

function flagOf(t, v) {
  if (t.critical_low != null && v <= t.critical_low) return 'LL'
  if (t.critical_high != null && v >= t.critical_high) return 'HH'
  if (t.ref_low != null && v < t.ref_low) return 'L'
  if (t.ref_high != null && v > t.ref_high) return 'H'
  return 'N'
}

/** Sparkline points (oldest → newest) with the reference band, scaled to the data and range. */
function spark(code, t) {
  const pts = data.value?.series?.[code] ?? []
  if (pts.length < 2) return null
  const W = 112
  const H = 32
  const vals = pts.map((p) => p.value)
  const lo = Math.min(...vals, t.ref_low ?? Infinity)
  const hi = Math.max(...vals, t.ref_high ?? -Infinity)
  const span = hi - lo || 1
  const y = (v) => H - 3 - ((v - lo) / span) * (H - 6)
  const x = (i) => 3 + (i / (pts.length - 1)) * (W - 6)
  return {
    W, H,
    line: pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' '),
    band: t.ref_low != null && t.ref_high != null ? { y: y(t.ref_high), h: y(t.ref_low) - y(t.ref_high) } : null,
    dots: pts.map((p, i) => ({ cx: x(i), cy: y(p.value), flag: p.abnormal_flag })),
    label: `${SHORT[code]} 趨勢：${pts.map((p) => p.value).join('、')}`,
  }
}

const rows = computed(() => types.value.map((t) => ({ t, latest: data.value?.latest?.[t.code] ?? null, spark: spark(t.code, t) })))
const openAlerts = computed(() => rows.value.flatMap((r) => (r.latest?.alerts ?? []).filter((a) => !a.resolved)))

// History grouped by collection time: one row per panel
const history = computed(() => {
  const groups = new Map()
  for (const r of data.value?.results ?? []) {
    if (!groups.has(r.collected_at)) groups.set(r.collected_at, { collected_at: r.collected_at, cycle_day: r.cycle_day, values: {} })
    groups.get(r.collected_at).values[r.test_code] = r
  }
  return [...groups.values()]
})
const showHistory = ref(false)

// ---------------- entry form ----------------
const formOpen = ref(false)
const form = reactive({ collected_at: '', values: { WBC: '', ANC: '', HGB: '', PLT: '' } })
const fieldErrors = ref({})
const formError = ref('')
const submitting = ref(false)
const result = ref(null)
let idempotencyKey = null

function localInputValue(date) {
  const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return d.toISOString().slice(0, 16)
}
function resetForm() {
  form.collected_at = localInputValue(new Date())
  for (const k of Object.keys(form.values)) form.values[k] = ''
  fieldErrors.value = {}
  formError.value = ''
  idempotencyKey = null
}
function openForm() {
  resetForm()
  result.value = null
  formOpen.value = true
}
watch(form, () => { idempotencyKey = null }, { deep: true }) // any edit makes it a new entry

function onInput(key) {
  if (fieldErrors.value[key]) fieldErrors.value = { ...fieldErrors.value, [key]: undefined }
  formError.value = ''
}

const preview = (t) => {
  const raw = form.values[t.code]
  if (raw === '' || raw == null || Number.isNaN(Number(raw))) return null
  return flagOf(t, Number(raw))
}

function validate() {
  const errs = {}
  const results = []
  for (const t of types.value) {
    const raw = form.values[t.code]
    if (raw === '' || raw == null) continue
    const v = Number(raw)
    const [lo, hi, dec] = INPUT[t.code] ?? [0, 1e5, 3]
    if (!Number.isFinite(v)) errs[t.code] = '請輸入數字'
    else if (v < lo || v > hi) errs[t.code] = `請確認數值（${lo}–${hi}）`
    else results.push({ test_code: t.code, value: Number(v.toFixed(dec)) })
  }
  if (!form.collected_at) errs.collected_at = '請填寫採檢時間'
  else {
    const at = new Date(form.collected_at)
    if (at.getTime() > Date.now() + 5 * 60000) errs.collected_at = '採檢時間不能在未來'
    else if (Date.now() - at.getTime() > 30 * 86400000) errs.collected_at = '只能登錄 30 天內的檢驗'
  }
  if (!results.length && !Object.keys(errs).some((k) => k !== 'collected_at')) formError.value = '請至少填寫一項檢驗值'
  fieldErrors.value = errs
  return Object.keys(errs).length || !results.length ? null : results
}

const newKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`

async function submit() {
  if (submitting.value) return
  formError.value = ''
  const results = validate()
  if (!results) return
  submitting.value = true
  idempotencyKey ??= newKey()
  const payload = { collected_at: new Date(form.collected_at).toISOString(), results }
  try {
    result.value = await store.submitLabResults(props.patientId, payload, idempotencyKey)
    formOpen.value = false
    idempotencyKey = null
  } catch (err) {
    // map API details back to fields: results[i].value → test code of the i-th submitted result
    const errs = {}
    for (const d of err.details ?? []) {
      const m = d.field?.match(/^results\[(\d+)\]/)
      if (m) errs[results[Number(m[1])]?.test_code ?? 'results'] = d.issue
      else errs[d.field] = d.issue
    }
    fieldErrors.value = errs
    formError.value = err.message
    if (!err.retryable) idempotencyKey = null
  } finally {
    submitting.value = false
  }
}

watch(() => props.patientId, (id) => {
  store.fetchLabHistory(id)
  formOpen.value = false
  result.value = null
}, { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface" aria-labelledby="lab-title">
    <header class="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
      <AppIcon name="flask" class="shrink-0 text-care" />
      <h2 id="lab-title" class="text-lg font-bold">檢驗數據</h2>
      <span v-if="data" class="text-sm text-ink-soft">近 {{ data.meta.days }} 天 {{ history.length }} 次採檢</span>
      <button
        v-if="!formOpen && data"
        type="button"
        class="ml-auto inline-flex min-h-10 items-center gap-1.5 rounded-full bg-care px-4 font-medium text-white hover:bg-care/90"
        @click="openForm"
      >
        <AppIcon name="flask" :size="18" /> 登錄檢驗
      </button>
    </header>

    <div class="px-5 py-4">
      <!-- result of the last entry -->
      <div
        v-if="result"
        class="mb-4 rounded-xl border px-4 py-3"
        :class="result.triggered_alerts.some((a) => a.severity === 'critical') ? 'border-critical/50 bg-critical-soft' : result.triggered_alerts.length ? 'border-warn/40 bg-warn-soft' : 'border-ok/30 bg-ok-soft'"
        role="status"
      >
        <p class="font-bold">
          已登錄 {{ result.results.length }} 項檢驗<template v-if="result.cycle_day">（療程第 {{ result.cycle_day }} 天）</template>
        </p>
        <ul v-if="result.triggered_alerts.length" class="mt-1 space-y-0.5">
          <li v-for="a in result.triggered_alerts" :key="a.alert_rule_code" :class="a.severity === 'critical' ? 'font-bold text-critical' : 'text-warn'">
            {{ a.severity === 'critical' ? '危急警示' : '警示' }}：{{ a.label }} {{ a.value }}，
            {{ a.notified ? '已通知病人與護理團隊' : '冷卻期間內，未重複發送通知' }}
          </li>
        </ul>
        <p v-else class="mt-0.5 text-ink-soft">沒有觸發警示。</p>
      </div>

      <!-- entry form -->
      <form v-if="formOpen" class="mb-5 rounded-xl border border-care/40 bg-care-soft/40 p-4" novalidate @submit.prevent="submit">
        <div class="flex flex-wrap items-end gap-4">
          <label class="block">
            <span class="text-sm font-medium">採檢時間</span>
            <input
              v-model="form.collected_at"
              type="datetime-local"
              class="mt-1 block min-h-11 rounded-lg border bg-surface px-3"
              :class="fieldErrors.collected_at ? 'border-critical' : 'border-line'"
              :aria-invalid="!!fieldErrors.collected_at"
              @input="onInput('collected_at')"
            />
          </label>
          <p class="pb-2 text-sm text-ink-soft">填寫有結果的項目即可，空白的不會送出。</p>
        </div>
        <p v-if="fieldErrors.collected_at" class="mt-1 text-sm text-critical">{{ fieldErrors.collected_at }}</p>

        <div class="mt-4 grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
          <label v-for="t in types" :key="t.code" class="block rounded-lg border bg-surface p-3" :class="fieldErrors[t.code] ? 'border-critical' : 'border-line'">
            <span class="flex items-start justify-between gap-2">
              <span class="min-w-0">
                <span class="block font-bold">{{ SHORT[t.code] }}</span>
                <span class="block truncate text-xs text-ink-soft">{{ t.name_zh.replace(/\s*\(.*\)$/, '') }}</span>
              </span>
              <span
                v-if="preview(t)"
                class="shrink-0 rounded-full px-2 py-0.5 text-xs font-bold whitespace-nowrap"
                :class="FLAG[preview(t)].cls"
              >{{ FLAG[preview(t)].text }}</span>
            </span>
            <span class="mt-1 flex items-center gap-2">
              <input
                v-model="form.values[t.code]"
                type="number"
                inputmode="decimal"
                :step="INPUT[t.code] ? 1 / 10 ** INPUT[t.code][2] : 'any'"
                :name="`lab-${t.code}`"
                class="min-h-11 w-full min-w-0 rounded-lg border border-line px-3 text-lg"
                :aria-invalid="!!fieldErrors[t.code]"
                :aria-describedby="`lab-hint-${t.code}`"
                @input="onInput(t.code)"
              />
              <span class="shrink-0 text-sm text-ink-soft">{{ t.unit }}</span>
            </span>
            <span :id="`lab-hint-${t.code}`" class="mt-1 block text-xs" :class="fieldErrors[t.code] ? 'text-critical' : 'text-ink-soft'">
              {{ fieldErrors[t.code] ?? `參考 ${range(t)}` }}
            </span>
          </label>
        </div>

        <p v-if="formError" class="mt-3 font-medium text-critical" role="alert">{{ formError }}</p>
        <div class="mt-4 flex flex-wrap gap-3">
          <button
            type="submit"
            class="min-h-11 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60"
            :disabled="submitting"
          >
            {{ submitting ? '儲存中…' : '儲存檢驗結果' }}
          </button>
          <button type="button" class="min-h-11 rounded-full border border-line bg-surface px-5 hover:bg-mist" :disabled="submitting" @click="formOpen = false">
            取消
          </button>
        </div>
      </form>

      <p v-if="error" class="text-critical">{{ error }}</p>
      <div v-else-if="!data" class="space-y-2" aria-busy="true" aria-label="載入中">
        <div v-for="n in 4" :key="n" class="h-10 animate-pulse rounded bg-mist" />
      </div>
      <template v-else>
        <p v-if="!history.length" class="text-ink-soft">近 {{ data.meta.days }} 天沒有檢驗紀錄。</p>

        <!-- latest per test -->
        <div v-else class="-mx-5 overflow-x-auto px-5">
          <table class="w-full min-w-[40rem] text-left">
            <thead class="text-sm text-ink-soft">
              <tr class="border-b border-line">
                <th class="py-2 pr-3 font-medium">項目</th>
                <th class="py-2 pr-3 text-right font-medium">最新值</th>
                <th class="py-2 pr-3 font-medium">參考範圍</th>
                <th class="py-2 pr-3 font-medium">判讀</th>
                <th class="py-2 pr-3 font-medium">採檢</th>
                <th class="py-2 font-medium">趨勢</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="r in rows" :key="r.t.code">
                <tr class="border-b border-line/60 align-middle" :class="r.latest?.abnormal_flag === 'LL' || r.latest?.abnormal_flag === 'HH' ? 'bg-critical-soft/60' : ''">
                  <td class="py-2.5 pr-3">
                    <span class="font-bold">{{ SHORT[r.t.code] }}</span>
                    <span class="block text-xs text-ink-soft">{{ r.t.name_zh.replace(/\s*\(.*\)$/, '') }}</span>
                  </td>
                  <td class="py-2.5 pr-3 text-right whitespace-nowrap">
                    <template v-if="r.latest">
                      <strong class="text-xl tabular-nums" :class="FLAG[r.latest.abnormal_flag]?.value">{{ r.latest.value }}</strong>
                      <span class="ml-1 text-sm text-ink-soft">{{ r.latest.unit }}</span>
                    </template>
                    <span v-else class="text-ink-soft">—</span>
                  </td>
                  <td class="py-2.5 pr-3 text-sm whitespace-nowrap">
                    {{ range(r.latest ?? r.t) }}
                    <span v-if="criticalText(r.t)" class="block text-xs text-ink-soft">危急 {{ criticalText(r.t) }}</span>
                  </td>
                  <td class="py-2.5 pr-3">
                    <span v-if="r.latest" class="rounded-full px-2.5 py-0.5 text-sm font-bold whitespace-nowrap" :class="FLAG[r.latest.abnormal_flag].cls">
                      {{ FLAG[r.latest.abnormal_flag].text }}
                    </span>
                  </td>
                  <td class="py-2.5 pr-3 text-sm whitespace-nowrap">
                    <template v-if="r.latest">
                      {{ formatWhen(r.latest.collected_at, today, timezone) }}
                      <span v-if="r.latest.cycle_day" class="block text-xs text-ink-soft">療程第 {{ r.latest.cycle_day }} 天</span>
                    </template>
                  </td>
                  <td class="py-2.5">
                    <svg v-if="r.spark" :width="r.spark.W" :height="r.spark.H" role="img" :aria-label="r.spark.label" class="block">
                      <rect v-if="r.spark.band" x="0" :y="r.spark.band.y" :width="r.spark.W" :height="Math.max(r.spark.band.h, 1)" class="fill-ok-soft" />
                      <polyline :points="r.spark.line" fill="none" class="stroke-care" stroke-width="1.75" stroke-linejoin="round" />
                      <circle v-for="(d, i) in r.spark.dots" :key="i" :cx="d.cx" :cy="d.cy" r="2.6"
                              :class="d.flag === 'LL' || d.flag === 'HH' ? 'fill-critical' : d.flag === 'N' ? 'fill-care' : 'fill-warn'" />
                    </svg>
                    <span v-else class="text-xs text-ink-soft">資料不足</span>
                  </td>
                </tr>
                <tr v-for="a in r.latest?.alerts ?? []" :key="a.event_key" class="border-b border-line/60">
                  <td colspan="6" class="pb-2.5 pl-4 text-sm">
                    <span class="inline-flex items-center gap-1.5" :class="a.resolved ? 'text-ink-soft' : a.severity === 'critical' ? 'font-bold text-critical' : 'font-medium text-warn'">
                      <AppIcon :name="a.resolved ? 'check' : 'alert'" :size="16" />
                      {{ a.title }}：{{ a.resolved ? `已由${a.resolved_by ?? '護理團隊'}處理${a.resolution_note ? `（${a.resolution_note}）` : ''}` : '尚未處理，請在上方警示清單處理' }}
                    </span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p v-if="openAlerts.length" class="sr-only">{{ openAlerts.length }} 則檢驗警示尚未處理</p>

        <!-- history -->
        <div v-if="history.length" class="mt-4">
          <button
            type="button"
            class="inline-flex min-h-10 items-center gap-1 font-medium text-care hover:underline"
            :aria-expanded="showHistory"
            aria-controls="lab-history"
            @click="showHistory = !showHistory"
          >
            <AppIcon name="chevron" :size="18" class="transition-transform" :class="showHistory ? 'rotate-90' : ''" />
            歷次檢驗（{{ history.length }} 次）
          </button>
          <div v-if="showHistory" id="lab-history" class="-mx-5 mt-2 overflow-x-auto px-5">
            <table class="w-full min-w-[34rem] text-left text-sm">
              <thead class="text-ink-soft">
                <tr class="border-b border-line">
                  <th class="py-2 pr-3 font-medium">採檢時間</th>
                  <th v-for="t in types" :key="t.code" class="py-2 pr-3 text-right font-medium">{{ SHORT[t.code] }}<span class="block text-xs font-normal">{{ t.unit }}</span></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="h in history" :key="h.collected_at" class="border-b border-line/60">
                  <td class="py-2 pr-3 whitespace-nowrap">
                    {{ formatWhen(h.collected_at, today, timezone) }}
                    <span v-if="h.cycle_day" class="text-ink-soft">・D{{ h.cycle_day }}</span>
                  </td>
                  <td v-for="t in types" :key="t.code" class="py-2 pr-3 text-right tabular-nums whitespace-nowrap">
                    <template v-if="h.values[t.code]">
                      <span :class="[FLAG[h.values[t.code].abnormal_flag]?.value, h.values[t.code].abnormal_flag !== 'N' ? 'font-bold' : '']">{{ h.values[t.code].value }}</span>
                      <sup v-if="h.values[t.code].abnormal_flag !== 'N'" class="ml-0.5 font-bold" :class="FLAG[h.values[t.code].abnormal_flag]?.value">{{ h.values[t.code].abnormal_flag }}</sup>
                    </template>
                    <span v-else class="text-ink-soft">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>
  </section>
</template>
