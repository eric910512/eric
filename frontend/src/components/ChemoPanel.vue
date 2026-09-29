<script setup>
import { computed, reactive, ref, watch } from 'vue'

import { useAppointmentsStore } from '@/stores/appointments'
import { useChemotherapyStore } from '@/stores/chemotherapy'
import { formatTime, localDate } from '@/utils/format'

/**
 * 化療療程 (staff): the patient's plans and cycles, starting / completing / delaying a cycle,
 * recording administrations and correcting them. Nurses write; admins read. A correction adds
 * a new record and keeps the original (shown as 已更正), so the history is never overwritten.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  canWrite: { type: Boolean, default: false },
  diagnoses: { type: Array, default: () => [] },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useChemotherapyStore()
const appointments = useAppointmentsStore()
const INTENT = { curative: '根治', adjuvant: '輔助', neoadjuvant: '前導', palliative: '緩和' }
const PLAN_STATUS = { planned: '預定', active: '進行中', completed: '已完成', discontinued: '已停止' }
const CYCLE_STATUS = { scheduled: '預定', in_progress: '進行中', completed: '已完成', delayed: '已延後', cancelled: '已取消' }
const MED_TYPE = { chemo: '化療藥物', premedication: '前置用藥', supportive: '支持性用藥' }
const ADMIN_STATUS = { given: '已給藥', held: '暫停給藥', partial: '部分給藥', refused: '拒絕給藥' }
const RECORD_STATUS = { amended: '已更正', entered_in_error: '標示錯誤' }
const ISSUE = {
  'cannot be in the future': '不能是未來的時間',
  'must be within this cycle (from its start to its end)': '需在這個 Cycle 的期間內',
  'must be after the previous cycle': '需晚於上一個 Cycle',
  'must be later than the current scheduled date': '需晚於目前的預定日期',
}

const plans = computed(() => store.plans[props.patientId] ?? [])
const meds = computed(() => store.medications[props.patientId] ?? [])
const current = computed(() => plans.value.find((p) => ['planned', 'active'].includes(p.status)) ?? null)
const past = computed(() => plans.value.filter((p) => p !== current.value))
const error = computed(() => store.errors[`plans:${props.patientId}`])
const today = () => localDate(new Date().toISOString(), props.timezone)
const when = (iso) => (iso ? `${localDate(iso, props.timezone)} ${formatTime(iso, props.timezone)}` : '—')
const issue = (d) => ISSUE[d.issue] ?? d.issue
const planSummary = (p) => [
  p.regimen?.name ?? '無處方範本', p.diagnosis?.name_zh, p.intent && INTENT[p.intent],
  p.attending_physician_name && `主治 ${p.attending_physician_name}`, `已完成 ${p.progress.completed_cycles} / ${p.total_cycles} 次`,
].filter(Boolean).join('，')

const notice = ref('')
const failure = ref('')
const fieldErrors = ref({})
async function act(fn, done) {
  notice.value = ''
  failure.value = ''
  fieldErrors.value = {}
  try {
    await fn()
    notice.value = done
    return true
  } catch (e) {
    // The server answered (4xx): this key is used up — the corrected entry is a new request.
    // A network error / 5xx keeps the key, so a retry can never create a second record.
    if (e.status >= 400 && e.status < 500) medForm.key = crypto.randomUUID()
    fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, issue(d)]))
    failure.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
    return false
  }
}

// ------------------------------------------------------------------ plan
const planForm = reactive({ open: false })
async function openPlan() {
  const [regimens] = await Promise.all([store.fetchRegimens(), store.fetchDrugs()])
  const r = regimens?.[0]
  Object.assign(planForm, {
    open: true, diagnosis_id: props.diagnoses.find((d) => d.is_primary)?.id ?? props.diagnoses[0]?.id ?? '',
    regimen_id: r?.id ?? '', plan_name: r ? `${r.name} 療程` : '', intent: 'curative', total_cycles: r?.default_total_cycles ?? 3,
    start_date: today(), attending_physician_name: '', generate_cycles: true, infusions: true, infusion_time: '09:00', infusion_location: '日間化療室',
  })
}
function onRegimen() {
  const r = store.regimens?.find((x) => x.id === planForm.regimen_id)
  if (r?.default_total_cycles) planForm.total_cycles = r.default_total_cycles
}
async function createPlan() {
  const payload = {
    diagnosis_id: planForm.diagnosis_id || null, regimen_id: planForm.regimen_id || null, plan_name: planForm.plan_name,
    intent: planForm.intent, total_cycles: Number(planForm.total_cycles), start_date: planForm.start_date,
    attending_physician_name: planForm.attending_physician_name || null, generate_cycles: planForm.generate_cycles,
    ...(planForm.generate_cycles && planForm.infusions
      ? { generate_infusion_appointments: { enabled: true, time: planForm.infusion_time, location: planForm.infusion_location || null } } : {}),
  }
  if (await act(() => store.createPlan(props.patientId, payload), '已建立療程')) {
    planForm.open = false
    if (appointments.byPatient[props.patientId]) appointments.fetch(props.patientId)
  }
}
const stopForm = reactive({ open: false, reason: '' })
async function discontinue() {
  if (await act(() => store.discontinuePlan(props.patientId, current.value.id, stopForm.reason), '已停止療程')) Object.assign(stopForm, { open: false, reason: '' })
}
async function addCycle() {
  const last = current.value.cycles.at(-1)
  await act(() => store.addCycle(props.patientId, current.value.id, { scheduled_date: last?.scheduled_date ?? today() }), '已新增 Cycle')
}

// ------------------------------------------------------------------ cycle actions
const cycleForm = reactive({ id: null, action: null, start_date: '', new_scheduled_date: '', delay_reason: '', end_date: '', move: true })
function openCycle(c, action) {
  Object.assign(cycleForm, { id: c.id, action, start_date: today(), end_date: today(), new_scheduled_date: '', delay_reason: '', move: true })
  fieldErrors.value = {}
}
async function submitCycle() {
  const body = { start: { start_date: cycleForm.start_date }, complete: { end_date: cycleForm.end_date },
    delay: { new_scheduled_date: cycleForm.new_scheduled_date, delay_reason: cycleForm.delay_reason, reschedule_appointments: cycleForm.move } }[cycleForm.action]
  const text = { start: '已開始 Cycle，之後的紀錄會算在這個 Cycle', complete: '已完成 Cycle', delay: '已延後 Cycle' }[cycleForm.action]
  if (await act(() => store.cycleAction(props.patientId, cycleForm.id, cycleForm.action, body), text)) {
    cycleForm.id = null
    if (appointments.byPatient[props.patientId]) appointments.fetch(props.patientId)
  }
}

// ------------------------------------------------------------------ medication form (new record or correction)
const medForm = reactive({ open: false })
function localInput(iso) {
  const d = iso ? new Date(iso) : new Date()
  return `${localDate(d.toISOString(), props.timezone)}T${formatTime(d.toISOString(), props.timezone)}`
}
function toIso(local) {
  return new Date(`${local}:00+08:00`).toISOString() // form times are in the patient's timezone (Asia/Taipei)
}
async function openMedication(cycle, amend = null) {
  const drugs = await store.fetchDrugs()
  const base = amend ?? {}
  Object.assign(medForm, {
    open: true, cycle, amend, key: crypto.randomUUID(), // one key per form: retries of this entry never add a second record
    drug_id: base.drug?.id ?? drugs?.[0]?.id ?? '', medication_type: base.medication_type ?? 'chemo',
    dose_value: base.dose_value ?? '', dose_unit: base.dose_unit ?? 'mg', route: base.route ?? 'IV',
    administered_at: localInput(base.administered_at), infusion_duration_min: base.infusion_duration_min ?? '',
    administration_status: base.administration_status ?? 'given', reaction_notes: base.reaction_notes ?? '', amend_reason: '',
  })
  fieldErrors.value = {}
}
const drugsForForm = computed(() => {
  const list = store.drugs ?? []
  const keep = medForm.amend?.drug
  return keep && !list.some((d) => d.id === keep.id) ? [...list, keep] : list
})
async function submitMedication() {
  const fields = {
    drug_id: Number(medForm.drug_id), medication_type: medForm.medication_type, dose_value: Number(medForm.dose_value),
    dose_unit: medForm.dose_unit, route: medForm.route || null, administered_at: toIso(medForm.administered_at),
    infusion_duration_min: medForm.infusion_duration_min === '' ? null : Number(medForm.infusion_duration_min),
    administration_status: medForm.administration_status, reaction_notes: medForm.reaction_notes.trim() || null,
  }
  if (medForm.amend) {
    const a = medForm.amend
    const changed = Object.fromEntries(Object.entries(fields).filter(([k, v]) => (k === 'drug_id' ? v !== a.drug.id : k === 'administered_at' ? Date.parse(v) !== Date.parse(a.administered_at) : v !== (a[k] ?? null))))
    if (await act(() => store.amendMedication(props.patientId, a.id, { amend_reason: medForm.amend_reason, ...changed }, medForm.key), '已更正，原紀錄保留在歷史中')) medForm.open = false
  } else if (await act(() => store.recordMedication(props.patientId, medForm.cycle.id, fields, medForm.key), '已登錄給藥')) {
    medForm.open = false
  }
}
const errorForm = reactive({ id: null, reason: '' })
async function markError() {
  if (await act(() => store.markMedicationError(props.patientId, errorForm.id, errorForm.reason), '已標示為錯誤，紀錄仍保留')) Object.assign(errorForm, { id: null, reason: '' })
}

watch(() => props.patientId, (id) => id && store.fetchPatient(id), { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="chemo-title" data-chemo-panel>
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 id="chemo-title" class="text-lg font-bold">化療療程</h2>
      <button v-if="canWrite && !current && !planForm.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" data-new-plan @click="openPlan">建立療程</button>
    </div>
    <p v-if="notice" class="mt-3 rounded-xl bg-ok-soft px-4 py-2 font-medium text-ok" role="status" data-chemo-notice>{{ notice }}</p>
    <p v-if="failure" class="mt-3 rounded-xl bg-critical-soft px-4 py-2 font-medium text-critical" role="alert" data-chemo-error>{{ failure }}</p>
    <p v-if="error" class="mt-3 text-critical" role="alert">{{ error.message }}</p>

    <!-- Create plan -->
    <form v-if="planForm.open" class="mt-3 grid gap-3 rounded-xl bg-mist p-3 sm:grid-cols-2" data-plan-form @submit.prevent="createPlan">
      <p v-if="!diagnoses.length" class="text-warn sm:col-span-2">請先在「診斷」新增診斷，療程需要對應一個診斷。</p>
      <label class="block"><span class="text-sm font-medium">診斷</span>
        <select v-model="planForm.diagnosis_id" name="diagnosis_id" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="d in diagnoses" :key="d.id" :value="d.id">{{ d.cancer_type.name_zh }}{{ d.stage ? ` 第 ${d.stage} 期` : '' }}</option>
        </select>
        <span v-if="fieldErrors.diagnosis_id" class="text-sm text-critical">請選擇這位病人的診斷</span>
      </label>
      <label class="block"><span class="text-sm font-medium">處方</span>
        <select v-model="planForm.regimen_id" name="regimen_id" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" @change="onRegimen">
          <option value="">不使用處方範本</option>
          <option v-for="r in store.regimens ?? []" :key="r.id" :value="r.id">{{ r.name }}（每 {{ r.cycle_length_days }} 天）</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">療程名稱</span>
        <input v-model="planForm.plan_name" name="plan_name" maxlength="100" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.plan_name" class="text-sm text-critical">請輸入療程名稱</span>
      </label>
      <label class="block"><span class="text-sm font-medium">治療目的</span>
        <select v-model="planForm.intent" name="intent" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="(label, value) in INTENT" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">總 Cycle 數</span>
        <input v-model="planForm.total_cycles" type="number" min="1" max="30" name="total_cycles" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.total_cycles" class="text-sm text-critical">1–30</span>
      </label>
      <label class="block"><span class="text-sm font-medium">開始日期</span>
        <input v-model="planForm.start_date" type="date" name="start_date" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label class="block"><span class="text-sm font-medium">主治醫師</span>
        <input v-model="planForm.attending_physician_name" name="attending_physician_name" maxlength="100" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label class="inline-flex min-h-11 items-center gap-2 self-end"><input v-model="planForm.generate_cycles" type="checkbox" name="generate_cycles" class="size-5" /> 依處方週期自動排定所有 Cycle</label>
      <template v-if="planForm.generate_cycles">
        <label class="inline-flex min-h-11 items-center gap-2"><input v-model="planForm.infusions" type="checkbox" name="infusions" class="size-5" /> 同時為每個 Cycle 建立化療注射行程</label>
        <div v-if="planForm.infusions" class="grid grid-cols-[7rem_1fr] gap-2">
          <label class="block"><span class="text-sm font-medium">注射時間</span>
            <input v-model="planForm.infusion_time" type="time" name="infusion_time" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-2" />
          </label>
          <label class="block"><span class="text-sm font-medium">地點</span>
            <input v-model="planForm.infusion_location" name="infusion_location" maxlength="100" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
          </label>
        </div>
      </template>
      <p v-if="fieldErrors.generate_cycles" class="text-sm text-critical sm:col-span-2">自動排定需要有週期天數的處方；或取消勾選，之後逐一新增 Cycle。</p>
      <div class="flex gap-2 sm:col-span-2">
        <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90" :disabled="!diagnoses.length">建立療程</button>
        <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="planForm.open = false">取消</button>
      </div>
    </form>

    <p v-if="!plans.length && !planForm.open && !error" class="mt-2 text-ink-soft">{{ store.plans[patientId] ? '尚未建立化療療程。' : '載入中…' }}</p>

    <!-- Current plan -->
    <div v-if="current" class="mt-3" data-current-plan :data-plan-status="current.status">
      <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p class="text-lg font-bold">{{ current.plan_name }}</p>
        <span class="rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">{{ PLAN_STATUS[current.status] }}</span>
      </div>
      <p class="text-sm text-ink-soft">{{ planSummary(current) }}</p>

      <ol class="mt-3 divide-y divide-line rounded-xl border border-line" data-cycles>
        <li v-for="c in current.cycles" :key="c.id" class="p-3" :data-cycle="c.cycle_number" :data-cycle-status="c.status">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span class="font-bold">第 {{ c.cycle_number }} 次</span>
            <span class="rounded-full px-2 py-0.5 text-sm" :class="c.status === 'in_progress' ? 'bg-care text-white' : c.status === 'completed' ? 'bg-ok-soft text-ok' : c.status === 'delayed' ? 'bg-warn-soft text-warn' : 'bg-mist text-ink-soft'">
              {{ CYCLE_STATUS[c.status] }}<template v-if="c.status === 'in_progress'">・第 {{ c.cycle_day }} 天</template>
            </span>
            <span class="text-sm text-ink-soft">
              <template v-if="c.actual_start_date">{{ c.actual_start_date }} 開始<template v-if="c.actual_end_date">，{{ c.actual_end_date }} 結束</template></template>
              <template v-else>預定 {{ c.scheduled_date }}</template>
              <template v-if="c.delay_days">（延後 {{ c.delay_days }} 天：{{ c.delay_reason }}）</template>
              <template v-if="c.medication_count">・給藥 {{ c.medication_count }} 筆</template>
            </span>
            <span v-if="canWrite" class="ml-auto flex flex-wrap gap-1.5">
              <template v-if="['scheduled', 'delayed'].includes(c.status)">
                <button type="button" class="min-h-9 rounded-full bg-care px-3 text-sm font-bold text-white hover:bg-care/90" data-start-cycle @click="openCycle(c, 'start')">開始</button>
                <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-delay-cycle @click="openCycle(c, 'delay')">延後</button>
              </template>
              <template v-else-if="c.status === 'in_progress'">
                <button type="button" class="min-h-9 rounded-full bg-care px-3 text-sm font-bold text-white hover:bg-care/90" data-record-medication @click="openMedication(c)">登錄給藥</button>
                <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-complete-cycle @click="openCycle(c, 'complete')">完成</button>
              </template>
            </span>
          </div>
          <form v-if="cycleForm.id === c.id" class="mt-3 flex flex-wrap items-end gap-3 rounded-xl bg-mist p-3" data-cycle-form @submit.prevent="submitCycle">
            <label v-if="cycleForm.action === 'start'" class="block"><span class="text-sm font-medium">Day 1（開始日期）</span>
              <input v-model="cycleForm.start_date" type="date" name="start_date" class="mt-1 block min-h-11 rounded-xl border border-line bg-surface px-3" />
              <span v-if="fieldErrors.start_date" class="block text-sm text-critical">{{ fieldErrors.start_date }}</span>
            </label>
            <label v-if="cycleForm.action === 'complete'" class="block"><span class="text-sm font-medium">結束日期</span>
              <input v-model="cycleForm.end_date" type="date" name="end_date" class="mt-1 block min-h-11 rounded-xl border border-line bg-surface px-3" />
              <span v-if="fieldErrors.end_date" class="block text-sm text-critical">{{ fieldErrors.end_date }}</span>
            </label>
            <template v-if="cycleForm.action === 'delay'">
              <label class="block"><span class="text-sm font-medium">新的預定日期</span>
                <input v-model="cycleForm.new_scheduled_date" type="date" name="new_scheduled_date" class="mt-1 block min-h-11 rounded-xl border border-line bg-surface px-3" />
                <span v-if="fieldErrors.new_scheduled_date" class="block text-sm text-critical">{{ fieldErrors.new_scheduled_date }}</span>
              </label>
              <label class="block min-w-48 flex-1"><span class="text-sm font-medium">延後原因</span>
                <input v-model="cycleForm.delay_reason" name="delay_reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
                <span v-if="fieldErrors.delay_reason" class="block text-sm text-critical">請填寫原因</span>
              </label>
              <label class="inline-flex min-h-11 items-center gap-2"><input v-model="cycleForm.move" type="checkbox" name="reschedule_appointments" class="size-5" /> 同步改期這個 Cycle 的行程</label>
            </template>
            <p v-if="cycleForm.action === 'start'" class="w-full text-sm text-ink-soft">開始後，新的症狀、量測、檢驗紀錄會算在這個 Cycle；之前的紀錄不會改變。</p>
            <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">{{ { start: '開始 Cycle', complete: '完成 Cycle', delay: '延後' }[cycleForm.action] }}</button>
            <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="cycleForm.id = null">取消</button>
          </form>
        </li>
      </ol>

      <div v-if="canWrite" class="mt-3 flex flex-wrap gap-2">
        <button v-if="current.cycles.length < current.total_cycles" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" @click="addCycle">新增 Cycle</button>
        <button v-if="!stopForm.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-critical hover:bg-critical-soft" @click="stopForm.open = true">停止療程</button>
      </div>
      <form v-if="stopForm.open" class="mt-3 flex flex-wrap items-end gap-3 rounded-xl bg-critical-soft p-3" @submit.prevent="discontinue">
        <label class="block min-w-56 flex-1"><span class="text-sm font-medium">停止原因</span>
          <input v-model="stopForm.reason" name="discontinue_reason" maxlength="2000" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        </label>
        <button type="submit" class="min-h-11 rounded-full bg-critical px-5 font-bold text-white hover:bg-critical/90" :disabled="!stopForm.reason.trim()">確定停止</button>
        <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="stopForm.open = false">取消</button>
        <p class="w-full text-sm text-critical">尚未開始的 Cycle 會取消，進行中的 Cycle 今天結束。</p>
      </form>
    </div>

    <!-- Medication form -->
    <form v-if="medForm.open" class="mt-4 grid gap-3 rounded-xl border-2 border-care/40 bg-care-soft/40 p-3 sm:grid-cols-2" novalidate data-medication-form @submit.prevent="submitMedication">
      <p class="font-bold sm:col-span-2">{{ medForm.amend ? `更正給藥紀錄（原紀錄會保留）` : `第 ${medForm.cycle.cycle_number} 次 Cycle：登錄給藥` }}</p>
      <label class="block"><span class="text-sm font-medium">藥物</span>
        <select v-model="medForm.drug_id" name="drug_id" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="d in drugsForForm" :key="d.id" :value="d.id">{{ d.generic_name }}</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">類別</span>
        <select v-model="medForm.medication_type" name="medication_type" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="(label, value) in MED_TYPE" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <div class="grid grid-cols-[1fr_6rem] gap-2">
        <label class="block"><span class="text-sm font-medium">劑量</span>
          <input v-model="medForm.dose_value" type="number" step="0.01" min="0" name="dose_value" inputmode="decimal" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        </label>
        <label class="block"><span class="text-sm font-medium">單位</span>
          <input v-model="medForm.dose_unit" name="dose_unit" maxlength="20" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        </label>
        <span v-if="fieldErrors.dose_value || fieldErrors.dose_unit" class="col-span-2 text-sm text-critical">請輸入大於 0 的劑量與單位</span>
      </div>
      <label class="block"><span class="text-sm font-medium">途徑</span>
        <select v-model="medForm.route" name="route" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="r in ['IV', 'PO', 'SC']" :key="r" :value="r">{{ r }}</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">給藥時間</span>
        <input v-model="medForm.administered_at" type="datetime-local" name="administered_at" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.administered_at" class="text-sm text-critical">{{ fieldErrors.administered_at }}</span>
      </label>
      <label class="block"><span class="text-sm font-medium">輸注時間（分鐘）</span>
        <input v-model="medForm.infusion_duration_min" type="number" min="1" max="1440" name="infusion_duration_min" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label class="block"><span class="text-sm font-medium">給藥狀態</span>
        <select v-model="medForm.administration_status" name="administration_status" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="(label, value) in ADMIN_STATUS" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">反應紀錄</span>
        <input v-model="medForm.reaction_notes" name="reaction_notes" maxlength="2000" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label v-if="medForm.amend" class="block sm:col-span-2"><span class="text-sm font-medium">更正原因（必填）</span>
        <input v-model="medForm.amend_reason" name="amend_reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.amend_reason" class="text-sm text-critical">{{ fieldErrors.amend_reason === 'nothing changed: send the corrected fields' ? '請修改要更正的欄位' : '請填寫更正原因' }}</span>
      </label>
      <div class="flex gap-2 sm:col-span-2">
        <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">{{ medForm.amend ? '儲存更正' : '登錄給藥' }}</button>
        <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="medForm.open = false">取消</button>
      </div>
    </form>

    <!-- Medication history -->
    <div v-if="meds.length" class="mt-4">
      <h3 class="font-bold">給藥紀錄</h3>
      <ul class="mt-2 divide-y divide-line" data-medication-history>
        <li v-for="m in meds" :key="m.id" class="py-2" :data-medication="m.id" :data-record-status="m.record_status">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span class="font-bold" :class="m.record_status !== 'final' ? 'text-ink-soft line-through' : ''">
              {{ [m.drug.generic_name, m.dose_value, m.dose_unit, m.route].filter((x) => x != null).join(' ') }}
            </span>
            <span class="text-sm">{{ ADMIN_STATUS[m.administration_status] }}</span>
            <span v-if="RECORD_STATUS[m.record_status]" class="rounded-full px-2 py-0.5 text-sm" :class="m.record_status === 'amended' ? 'bg-warn-soft text-warn' : 'bg-critical-soft text-critical'">{{ RECORD_STATUS[m.record_status] }}</span>
            <span v-if="m.amends_id" class="rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">更正紀錄</span>
            <span v-if="canWrite && m.record_status === 'final'" class="ml-auto flex gap-1.5">
              <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-amend @click="openMedication(null, m)">更正</button>
              <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm text-critical hover:bg-critical-soft" data-mark-error @click="errorForm.id = m.id">標示錯誤</button>
            </span>
          </div>
          <p class="text-sm text-ink-soft">
            第 {{ m.cycle_number }} 次 Cycle 第 {{ m.cycle_day ?? '—' }} 天，{{ when(m.administered_at) }}<template v-if="m.administered_by">，{{ m.administered_by.display_name }}</template>
            <template v-if="m.amended_by_id">・已由新紀錄更正</template>
          </p>
          <p v-if="m.reaction_notes" class="text-sm">反應紀錄：{{ m.reaction_notes }}</p>
          <form v-if="errorForm.id === m.id" class="mt-2 flex flex-wrap items-end gap-2 rounded-xl bg-critical-soft p-3" @submit.prevent="markError">
            <label class="block min-w-48 flex-1"><span class="text-sm font-medium">原因（必填）</span>
              <input v-model="errorForm.reason" name="error_reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
            </label>
            <button type="submit" class="min-h-11 rounded-full bg-critical px-4 font-bold text-white" :disabled="!errorForm.reason.trim()">標示錯誤</button>
            <button type="button" class="min-h-11 rounded-full px-3 text-ink-soft hover:bg-white" @click="errorForm.id = null">取消</button>
          </form>
        </li>
      </ul>
    </div>

    <details v-if="past.length" class="mt-4">
      <summary class="min-h-11 cursor-pointer py-2 text-care">過去的療程（{{ past.length }}）</summary>
      <ul class="space-y-1">
        <li v-for="p in past" :key="p.id" class="text-sm">
          <strong>{{ p.plan_name }}</strong>　{{ PLAN_STATUS[p.status] }}，{{ p.start_date }}–{{ p.end_date ?? '' }}<template v-if="p.discontinue_reason">（{{ p.discontinue_reason }}）</template>
        </li>
      </ul>
    </details>
  </section>
</template>
