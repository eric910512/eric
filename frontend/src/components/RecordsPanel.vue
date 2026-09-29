<script setup>
import { computed, reactive, ref, watch } from 'vue'

import { useRecordsStore } from '@/stores/records'
import { formatTime, localDate } from '@/utils/format'
import { VITAL_FIELDS } from '@/utils/vitals'

/**
 * 紀錄修正 (nurse): recent symptom reports, vital signs and lab results of one patient, with
 * 更正 (a new record; the original is kept as 已更正), 標示錯誤 (kept, no longer used) and the
 * correction history. Alerts raised by a wrong value are closed by the server when the corrected
 * value no longer meets the rule.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  canWrite: { type: Boolean, default: false },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useRecordsStore()
const TABS = [['symptom', '症狀回報'], ['vital', '生命徵象'], ['lab', '檢驗']]
const tab = ref('symptom')
const data = computed(() => store.byPatient[props.patientId])
const rows = computed(() => (data.value?.[tab.value] ?? []).slice(0, 15))
const when = (iso) => (iso ? `${localDate(iso, props.timezone)} ${formatTime(iso, props.timezone)}` : '—')
const STATUS = { amended: '已被更正', entered_in_error: '標示錯誤', final: '目前版本' }
const FLAG = { N: '正常', L: '偏低', LL: '過低', H: '偏高', HH: '過高' }

const timeOf = (r) => r.recorded_at ?? r.measured_at ?? r.collected_at
function summary(kind, r) {
  if (kind === 'symptom') return (r.values ?? []).map((v) => (v.value_boolean !== undefined && v.value_boolean !== null ? `${v.label ?? v.definition_code} ${v.value_boolean ? '有' : '沒有'}` : `${v.label ?? v.definition_code} ${v.score ?? v.value_numeric}`)).join('、')
  if (kind === 'vital') return Object.entries(VITAL_FIELDS).filter(([f]) => r[f] != null).map(([f, m]) => `${m.label} ${Number(r[f]).toFixed(m.decimals)}${m.unit}`).join('、')
  return `${r.test_code} ${r.value} ${r.unit ?? ''}（${FLAG[r.abnormal_flag] ?? r.abnormal_flag}）`
}

const notice = ref('')
const failure = ref('')
const fieldErrors = ref({})
async function run(fn, done) {
  notice.value = ''
  failure.value = ''
  fieldErrors.value = {}
  try {
    await fn()
    notice.value = done
    return true
  } catch (e) {
    if (e.status >= 400 && e.status < 500) form.key = crypto.randomUUID() // answered: a corrected submission is a new request
    fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, d.issue]))
    failure.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
    return false
  }
}

// ------------------------------------------------------------------ correction form
const form = reactive({ id: null, kind: null, mode: null })
function open(kind, r, mode) {
  const base = { id: r.id, kind, mode, key: crypto.randomUUID(), reason: '' }
  if (mode === 'amend' && kind === 'symptom') base.values = r.values.map((v) => ({ code: v.definition_code, label: v.label ?? v.definition_code, boolean: v.value_boolean !== undefined && v.value_boolean !== null, value: v.value_boolean ?? v.score ?? v.value_numeric }))
  if (mode === 'amend' && kind === 'vital') base.values = Object.fromEntries(Object.keys(VITAL_FIELDS).map((f) => [f, r[f] ?? '']))
  if (mode === 'amend' && kind === 'lab') base.value = Number(r.value)
  Object.assign(form, { values: null, value: null, ...base })
  fieldErrors.value = {}
}
async function submit() {
  if (form.mode === 'error') {
    if (await run(() => store.markError(props.patientId, form.kind, form.id, form.reason), '已標示為錯誤；紀錄保留，這筆資料與它的警示不再使用')) form.id = null
    return
  }
  let payload = { amend_reason: form.reason }
  if (form.kind === 'symptom') {
    payload.values = form.values.map((v) => (v.boolean ? { definition_code: v.code, value_boolean: v.value === true || v.value === 'true' } : { definition_code: v.code, value_numeric: Number(v.value) }))
  } else if (form.kind === 'vital') {
    const r = data.value.vital.find((x) => x.id === form.id)
    for (const [f, v] of Object.entries(form.values)) {
      const next = v === '' ? null : Number(v)
      if (next !== (r[f] ?? null)) payload[f] = next
    }
  } else payload.value = Number(form.value)
  if (await run(() => store.amend(props.patientId, form.kind, form.id, payload, form.key), '已更正；原紀錄保留在修正歷史中')) form.id = null
}
const historyOf = reactive({})
async function toggleHistory(kind, r) {
  const k = `${kind}:${r.id}`
  historyOf[k] = !historyOf[k]
  if (historyOf[k]) await store.fetchHistory(kind, r.id)
}

watch(() => props.patientId, (id) => id && store.fetchPatient(id), { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="rec-title" data-records-panel>
    <h2 id="rec-title" class="text-lg font-bold">紀錄修正</h2>
    <p class="text-sm text-ink-soft">更正會新增一筆紀錄並保留原紀錄；Dashboard、風險、通知與時間軸都會改用更正後的資料。</p>
    <div class="mt-3 flex gap-1 overflow-x-auto" role="tablist" aria-label="紀錄類型">
      <button v-for="[k, label] in TABS" :key="k" type="button" role="tab" :aria-selected="tab === k" class="min-h-10 shrink-0 rounded-full px-4"
              :class="tab === k ? 'bg-care text-white' : 'bg-mist text-ink-soft hover:text-ink'" :data-tab="k" @click="tab = k; form.id = null">{{ label }}</button>
    </div>
    <p v-if="notice" class="mt-3 rounded-xl bg-ok-soft px-4 py-2 font-medium text-ok" role="status" data-rec-notice>{{ notice }}</p>
    <p v-if="failure" class="mt-3 rounded-xl bg-critical-soft px-4 py-2 font-medium text-critical" role="alert" data-rec-error>{{ failure }}</p>
    <p v-if="store.errors[patientId]" class="mt-3 text-critical" role="alert">{{ store.errors[patientId].message }}</p>

    <p v-if="data && !rows.length" class="mt-3 text-ink-soft">最近沒有紀錄。</p>
    <ul class="mt-2 divide-y divide-line" :data-record-list="tab">
      <li v-for="r in rows" :key="`${tab}:${r.id}`" class="py-2" :data-record="r.id">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span class="text-sm text-ink-soft">{{ when(timeOf(r)) }}</span>
          <span class="min-w-0 flex-1">{{ summary(tab, r) }}</span>
          <span v-if="r.amends_id" class="rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">已更正版本</span>
          <span v-if="tab === 'symptom' && r.review_status === 'submitted'" class="rounded-full bg-warn-soft px-2 py-0.5 text-sm text-warn">待審閱</span>
          <span class="flex gap-1.5">
            <button v-if="r.amends_id" type="button" class="min-h-9 rounded-full px-3 text-sm text-care hover:bg-care-soft" data-history @click="toggleHistory(tab, r)">修正歷史</button>
            <template v-if="canWrite">
              <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-amend-record @click="open(tab, r, 'amend')">更正</button>
              <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm text-critical hover:bg-critical-soft" data-error-record @click="open(tab, r, 'error')">標示錯誤</button>
            </template>
          </span>
        </div>
        <ol v-if="historyOf[`${tab}:${r.id}`] && store.history[`${tab}:${r.id}`]" class="mt-2 list-decimal space-y-0.5 rounded-xl bg-mist py-2 pr-3 pl-8 text-sm" data-record-history>
          <li v-for="h in store.history[`${tab}:${r.id}`]" :key="h.id">{{ summary(tab, h) }}・{{ STATUS[h.record_status] }}</li>
        </ol>
        <form v-if="form.id === r.id && form.kind === tab" class="mt-2 grid gap-2 rounded-xl bg-mist p-3" novalidate data-record-form @submit.prevent="submit">
          <template v-if="form.mode === 'amend'">
            <div v-if="tab === 'symptom'" class="grid gap-2 sm:grid-cols-2">
              <label v-for="v in form.values" :key="v.code" class="flex items-center justify-between gap-2">
                <span>{{ v.label }}</span>
                <select v-if="v.boolean" v-model="v.value" :name="`value_${v.code}`" class="min-h-10 rounded-lg border border-line bg-surface px-2">
                  <option :value="false">沒有</option><option :value="true">有</option>
                </select>
                <select v-else v-model.number="v.value" :name="`value_${v.code}`" class="min-h-10 rounded-lg border border-line bg-surface px-2">
                  <option v-for="n in 11" :key="n" :value="n - 1">{{ n - 1 }}</option>
                </select>
              </label>
            </div>
            <div v-else-if="tab === 'vital'" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <label v-for="(m, f) in VITAL_FIELDS" :key="f" class="block"><span class="text-sm">{{ m.label }}（{{ m.unit }}）</span>
                <input v-model="form.values[f]" type="number" :step="m.decimals ? '0.1' : '1'" :name="f" inputmode="decimal" class="mt-1 block min-h-10 w-full rounded-lg border border-line bg-surface px-2" />
                <span v-if="fieldErrors[f]" class="text-sm text-critical">{{ m.min }}–{{ m.max }}</span>
              </label>
            </div>
            <label v-else class="block"><span class="text-sm">更正後的數值（{{ r.unit }}）</span>
              <input v-model="form.value" type="number" step="0.01" name="value" class="mt-1 block min-h-10 w-40 rounded-lg border border-line bg-surface px-2" />
            </label>
          </template>
          <label class="block"><span class="text-sm font-medium">{{ form.mode === 'amend' ? '更正原因（必填）' : '標示錯誤的原因（必填）' }}</span>
            <input v-model="form.reason" name="reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
            <span v-if="fieldErrors.amend_reason" class="text-sm text-critical">{{ fieldErrors.amend_reason.startsWith('nothing changed') ? '請修改要更正的數值' : '請填寫原因' }}</span>
          </label>
          <div class="flex gap-2">
            <button type="submit" class="min-h-11 rounded-full px-5 font-bold text-white" :class="form.mode === 'error' ? 'bg-critical' : 'bg-care'" :disabled="!form.reason.trim()">
              {{ form.mode === 'error' ? '確定標示錯誤' : '儲存更正' }}
            </button>
            <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="form.id = null">取消</button>
          </div>
        </form>
      </li>
    </ul>
  </section>
</template>
