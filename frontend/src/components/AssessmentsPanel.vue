<script setup>
import { computed, reactive, ref, watch } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { useNursingStore } from '@/stores/nursing'
import { formatTime, localDate } from '@/utils/format'

/**
 * 護理評估 (staff): SOAP assessments of one patient. Drafts are edited and signed by their
 * author; signed ones are locked and corrected with 修正 (a new version — the original stays in
 * the history). Nurses of the patient write; admins read. Patients never see this content.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  canWrite: { type: Boolean, default: false },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const auth = useAuthStore()
const store = useNursingStore()
const TYPE = { initial: '初次評估', pre_chemo: '化療前評估', during_infusion: '輸注中評估', post_chemo: '化療後評估', follow_up: '追蹤評估', phone_follow_up: '電話追蹤' }
const CONDITION = { stable: '穩定', concern: '需注意', urgent: '緊急' }
const RISK = { low: '低', medium: '中', high: '高' }
const READINESS = { ready: '可施打', hold: '暫緩', delay: '延後', refer_physician: '轉介醫師' }
const ITEM_TYPE = { problem: '問題', goal: '目標', intervention: '措施', education: '衛教', referral: '轉介' }
const ITEM_STATUS = { open: '待處理', in_progress: '處理中', resolved: '已解決', done: '已完成' }
const SOAP = [['subjective', 'S 主觀'], ['objective', 'O 客觀'], ['assessment', 'A 評估'], ['plan', 'P 計畫']]

const rows = computed(() => store.byPatient[props.patientId] ?? [])
const openId = ref(null)
const open = computed(() => (openId.value ? store.details[openId.value] : null))
const when = (iso) => (iso ? `${localDate(iso, props.timezone)} ${formatTime(iso, props.timezone)}` : '—')
const mineDraft = (a) => a.sign_status === 'draft' && a.assessed_by?.id === auth.user?.id
function localInput(iso) {
  const d = iso ? new Date(iso) : new Date()
  return `${localDate(d.toISOString(), props.timezone)}T${formatTime(d.toISOString(), props.timezone)}`
}
const toIso = (local) => (local ? new Date(`${local}:00+08:00`).toISOString() : null) // inputs are in the patient's timezone (Asia/Taipei)

const notice = ref('')
const failure = ref('')
const fieldErrors = ref({})
async function run(fn, done) {
  notice.value = ''
  failure.value = ''
  fieldErrors.value = {}
  try {
    const r = await fn()
    notice.value = done
    return r ?? true
  } catch (e) {
    if (e.status >= 400 && e.status < 500) form.key = crypto.randomUUID() // answered: a corrected submission is a new request
    fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field.replace(/^items\[\d+\]\./, 'items.'), d.issue]))
    failure.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.code === 'RECORD_LOCKED' ? '已簽署的評估不能直接修改，請使用「修正」。' : e.message
    return false
  }
}

async function show(a) {
  openId.value = openId.value === a.id ? null : a.id
  if (openId.value) {
    await store.fetchDetail(a.id)
    if (a.amends_id || a.amended_by_id) store.fetchVersions(a.id)
  }
}

// ------------------------------------------------------------------ form: new draft, edit draft, correction
const form = reactive({ open: false, mode: 'new', id: null })
function fill(a = {}) {
  return {
    assessment_type: a.assessment_type ?? 'follow_up', assessed_at: localInput(a.assessed_at), ecog_status: a.ecog_status ?? '',
    overall_condition: a.overall_condition ?? '', risk_level: a.risk_level ?? '', chemo_readiness: a.chemo_readiness ?? '',
    subjective: a.subjective ?? '', objective: a.objective ?? '', assessment: a.assessment ?? '', plan: a.plan ?? '',
    items: (a.items ?? []).map((i) => ({ item_type: i.item_type, description: i.description, priority: i.priority ?? '', item_status: i.item_status })),
    amend_reason: '',
  }
}
function openForm(mode, a = null) {
  Object.assign(form, { open: true, mode, id: a?.id ?? null, key: crypto.randomUUID(), ...fill(a ?? {}) })
  fieldErrors.value = {}
}
function payload() {
  const nul = (v) => (v === '' ? null : v)
  return {
    assessment_type: form.assessment_type, assessed_at: toIso(form.assessed_at), ecog_status: form.ecog_status === '' ? null : Number(form.ecog_status),
    overall_condition: nul(form.overall_condition), risk_level: nul(form.risk_level), chemo_readiness: nul(form.chemo_readiness),
    ...Object.fromEntries(SOAP.map(([k]) => [k, form[k].trim() || null])),
    items: form.items.filter((i) => i.description.trim()).map((i) => ({ item_type: i.item_type, description: i.description.trim(), ...(i.priority ? { priority: i.priority } : {}), ...(i.item_status ? { item_status: i.item_status } : {}) })),
  }
}
async function save() {
  let r
  if (form.mode === 'new') r = await run(() => store.create(props.patientId, payload(), form.key), '已儲存草稿')
  else if (form.mode === 'edit') r = await run(() => store.update(props.patientId, form.id, payload()), '已更新草稿')
  else {
    const { assessed_at: _a, ...rest } = payload() // a correction keeps the original time
    r = await run(() => store.amend(props.patientId, form.id, { amend_reason: form.amend_reason, ...rest }, form.key), '已建立修正版本（草稿），簽署後取代原評估')
  }
  if (r) {
    form.open = false
    openId.value = r.id ?? openId.value
    if (r.id) await store.fetchDetail(r.id)
  }
}
async function sign(a) {
  if (await run(() => store.sign(props.patientId, a.id), '已簽署')) {
    await store.fetchDetail(a.id)
    if (a.amends_id) store.fetchVersions(a.id)
  }
}
async function setItem(a, item, status) {
  await run(() => store.updateItem(props.patientId, a.id, item.id, status), `「${item.description}」：${ITEM_STATUS[status]}`)
}

watch(() => props.patientId, (id) => id && store.fetchPatient(id), { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="na-title" data-assessments-panel>
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 id="na-title" class="text-lg font-bold">護理評估</h2>
      <button v-if="canWrite && !form.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" data-new-assessment @click="openForm('new')">新增評估</button>
    </div>
    <p class="text-sm text-ink-soft">內部紀錄：病人只會在時間軸看到「護理師已完成評估」，看不到內容、草稿與風險判斷。</p>
    <p v-if="notice" class="mt-3 rounded-xl bg-ok-soft px-4 py-2 font-medium text-ok" role="status" data-na-notice>{{ notice }}</p>
    <p v-if="failure" class="mt-3 rounded-xl bg-critical-soft px-4 py-2 font-medium text-critical" role="alert" data-na-error>{{ failure }}</p>
    <p v-if="store.errors[`patient:${patientId}`]" class="mt-3 text-critical" role="alert">{{ store.errors[`patient:${patientId}`].message }}</p>

    <form v-if="form.open" class="mt-3 grid gap-3 rounded-xl bg-mist p-3 sm:grid-cols-2" novalidate data-assessment-form @submit.prevent="save">
      <p class="font-bold sm:col-span-2">{{ { new: '新增評估（草稿）', edit: '修改草稿', amend: '修正已簽署的評估' }[form.mode] }}</p>
      <label class="block"><span class="text-sm font-medium">評估類型</span>
        <select v-model="form.assessment_type" name="assessment_type" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="(label, value) in TYPE" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label v-if="form.mode !== 'amend'" class="block"><span class="text-sm font-medium">評估時間</span>
        <input v-model="form.assessed_at" type="datetime-local" name="assessed_at" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.assessed_at" class="text-sm text-critical">需在 7 天內，且不能是未來</span>
      </label>
      <div class="grid grid-cols-2 gap-2 sm:col-span-2 sm:grid-cols-4">
        <label class="block"><span class="text-sm font-medium">ECOG</span>
          <select v-model="form.ecog_status" name="ecog_status" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-2">
            <option value="">未評</option>
            <option v-for="n in 6" :key="n" :value="n - 1">{{ n - 1 }}</option>
          </select>
        </label>
        <label class="block"><span class="text-sm font-medium">整體狀況</span>
          <select v-model="form.overall_condition" name="overall_condition" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-2">
            <option value="">未評</option>
            <option v-for="(label, value) in CONDITION" :key="value" :value="value">{{ label }}</option>
          </select>
        </label>
        <label class="block"><span class="text-sm font-medium">風險</span>
          <select v-model="form.risk_level" name="risk_level" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-2">
            <option value="">未評</option>
            <option v-for="(label, value) in RISK" :key="value" :value="value">{{ label }}</option>
          </select>
        </label>
        <label class="block"><span class="text-sm font-medium">化療準備</span>
          <select v-model="form.chemo_readiness" name="chemo_readiness" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-2">
            <option value="">未評</option>
            <option v-for="(label, value) in READINESS" :key="value" :value="value">{{ label }}</option>
          </select>
        </label>
      </div>
      <label v-for="[k, label] in SOAP" :key="k" class="block sm:col-span-2"><span class="text-sm font-medium">{{ label }}</span>
        <textarea v-model="form[k]" :name="k" rows="2" maxlength="5000" class="mt-1 block w-full rounded-xl border border-line bg-surface px-3 py-2" />
      </label>
      <fieldset class="sm:col-span-2">
        <legend class="text-sm font-medium">護理問題與措施</legend>
        <div v-for="(i, n) in form.items" :key="n" class="mt-2 grid gap-2 sm:grid-cols-[7rem_1fr_6rem_auto]" data-item-row>
          <select v-model="i.item_type" :aria-label="`第 ${n + 1} 項類型`" class="min-h-11 rounded-xl border border-line bg-surface px-2">
            <option v-for="(label, value) in ITEM_TYPE" :key="value" :value="value">{{ label }}</option>
          </select>
          <input v-model="i.description" name="item_description" maxlength="2000" :aria-label="`第 ${n + 1} 項內容`" class="min-h-11 rounded-xl border border-line bg-surface px-3" />
          <select v-model="i.priority" :aria-label="`第 ${n + 1} 項優先`" class="min-h-11 rounded-xl border border-line bg-surface px-2">
            <option value="">優先</option>
            <option v-for="(label, value) in RISK" :key="value" :value="value">{{ label }}</option>
          </select>
          <button type="button" class="min-h-11 rounded-full px-3 text-ink-soft hover:bg-white" @click="form.items.splice(n, 1)">移除</button>
        </div>
        <button type="button" class="mt-2 min-h-10 rounded-full border border-line px-4 text-sm text-care hover:bg-care-soft" data-add-item @click="form.items.push({ item_type: 'problem', description: '', priority: '', item_status: 'open' })">加入項目</button>
      </fieldset>
      <label v-if="form.mode === 'amend'" class="block sm:col-span-2"><span class="text-sm font-medium">修正原因（必填）</span>
        <input v-model="form.amend_reason" name="amend_reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.amend_reason" class="text-sm text-critical">請填寫修正原因</span>
      </label>
      <div class="flex gap-2 sm:col-span-2">
        <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">{{ form.mode === 'amend' ? '建立修正版本' : '儲存草稿' }}</button>
        <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="form.open = false">取消</button>
      </div>
    </form>

    <p v-if="store.byPatient[patientId] && !rows.length" class="mt-2 text-ink-soft">尚無護理評估。</p>
    <ul class="mt-3 divide-y divide-line" data-assessment-list>
      <li v-for="a in rows" :key="a.id" class="py-2" :data-assessment="a.id" :data-sign-status="a.sign_status">
        <button type="button" class="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left" :aria-expanded="openId === a.id" @click="show(a)">
          <span class="font-bold">{{ a.assessment_type_text }}</span>
          <span class="rounded-full px-2 py-0.5 text-sm" :class="a.sign_status === 'draft' ? 'bg-warn-soft text-warn' : 'bg-ok-soft text-ok'">{{ a.sign_status === 'draft' ? '草稿' : '已簽署' }}</span>
          <span v-if="a.amends_id" class="rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">修正版本</span>
          <span v-if="a.risk_level" class="text-sm" :class="a.risk_level === 'high' ? 'font-bold text-critical' : a.risk_level === 'medium' ? 'text-warn' : 'text-ink-soft'">風險 {{ RISK[a.risk_level] }}</span>
          <span class="ml-auto text-sm text-ink-soft">{{ when(a.assessed_at) }}・{{ a.assessed_by?.display_name }}</span>
        </button>
        <div v-if="openId === a.id && open" class="mt-2 rounded-xl bg-mist p-3" data-assessment-detail>
          <dl class="space-y-1">
            <div v-for="[k, label] in SOAP" v-show="open[k]" :key="k" class="flex gap-2">
              <dt class="w-14 shrink-0 font-bold text-ink-soft">{{ label }}</dt><dd class="min-w-0 break-words">{{ open[k] }}</dd>
            </div>
          </dl>
          <p class="mt-1 text-sm text-ink-soft">
            <template v-if="open.ecog_status != null">ECOG {{ open.ecog_status }}・</template>
            <template v-if="open.overall_condition">{{ CONDITION[open.overall_condition] }}・</template>
            <template v-if="open.chemo_readiness">化療準備：{{ READINESS[open.chemo_readiness] }}・</template>
            <template v-if="open.cycle_day">療程第 {{ open.cycle_day }} 天</template>
            <template v-if="open.signed_at">・{{ when(open.signed_at) }} 簽署</template>
          </p>
          <ul v-if="open.items.length" class="mt-2 space-y-1">
            <li v-for="i in open.items" :key="i.id" class="flex flex-wrap items-center gap-2 text-sm" :data-item="i.id">
              <span class="rounded-full bg-surface px-2 py-0.5">{{ ITEM_TYPE[i.item_type] }}</span>
              <span class="min-w-0 flex-1">{{ i.description }}</span>
              <select v-if="canWrite && open.record_status === 'final'" :value="i.item_status" :aria-label="`${i.description} 狀態`" class="min-h-9 rounded-lg border border-line bg-surface px-2" data-item-status @change="setItem(open, i, $event.target.value)">
                <option v-for="(label, value) in ITEM_STATUS" :key="value" :value="value">{{ label }}</option>
              </select>
              <span v-else class="text-ink-soft">{{ ITEM_STATUS[i.item_status] }}</span>
            </li>
          </ul>
          <div v-if="store.versions[a.id]?.length > 1" class="mt-2 text-sm" data-versions>
            <p class="font-bold">版本紀錄</p>
            <ol class="list-decimal pl-5">
              <li v-for="v in store.versions[a.id]" :key="v.id">
                {{ when(v.assessed_at) }}・{{ v.assessed_by?.display_name }}・{{ v.record_status === 'amended' ? '已被修正' : v.sign_status === 'draft' ? '修正草稿' : '目前版本' }}・風險 {{ RISK[v.risk_level] ?? '—' }}
              </li>
            </ol>
          </div>
          <div v-if="canWrite" class="mt-3 flex flex-wrap gap-2">
            <template v-if="mineDraft(open)">
              <button type="button" class="min-h-10 rounded-full border border-line px-4 hover:bg-white" data-edit-assessment @click="openForm('edit', open)">修改草稿</button>
              <button type="button" class="min-h-10 rounded-full bg-care px-4 font-bold text-white hover:bg-care/90" data-sign-assessment @click="sign(open)">簽署</button>
            </template>
            <p v-else-if="open.sign_status === 'draft'" class="text-sm text-ink-soft">草稿只有 {{ open.assessed_by?.display_name }} 可以修改與簽署。</p>
            <button v-if="open.sign_status === 'signed' && open.record_status === 'final' && !open.amended_by_id" type="button" class="min-h-10 rounded-full border border-line px-4 hover:bg-white" data-amend-assessment @click="openForm('amend', open)">修正</button>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
