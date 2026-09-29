<script setup>
import { onMounted, reactive, ref } from 'vue'

import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'
import { useDashboardStore } from '@/stores/dashboard'

/**
 * 風險規則與量表 (admin, database-design §7 / §5): turn an alert rule on or off, change its
 * threshold and try it on a sample value, and set which questions the symptom form asks.
 * Changes apply to records submitted afterwards; existing alerts and records are not recalculated.
 */
const dashboard = useDashboardStore()
const store = useAdminStore()

const SOURCE_TEXT = { symptom: '症狀回報', vital_sign: '生命徵象', lab: '檢驗' }
const CONDITION_TEXT = { within_nadir: '只在低谷期', outside_nadir: '低谷期以外', value_above: '且高於', consecutive_records: '連續筆數' }
const conditions = (c) => Object.entries(c ?? {}).filter(([, v]) => v !== false && v != null)
  .map(([k, v]) => (v === true ? CONDITION_TEXT[k] ?? k : `${CONDITION_TEXT[k] ?? k} ${v}`))

// ---- rules
const editing = ref(null) // rule id
const draft = reactive({ threshold_value: '', severity: 'warning' })
const ruleError = ref({})
const ruleNotice = ref('')
const trial = reactive({}) // rule id → { value, in_nadir, result }

function edit(r) {
  editing.value = r.id
  Object.assign(draft, { threshold_value: String(r.threshold_value), severity: r.severity })
  ruleError.value = {}
}
async function saveRule(r, patch, done) {
  ruleError.value = { ...ruleError.value, [r.id]: '' }
  try {
    const updated = await store.updateRule(r.id, patch)
    ruleNotice.value = `${updated.name}：${done}`
    editing.value = null
  } catch (e) {
    ruleError.value = { ...ruleError.value, [r.id]: e.details?.map((d) => d.issue).join('；') || e.message }
  }
}
function submitThreshold(r) {
  const v = Number(draft.threshold_value)
  if (draft.threshold_value.trim() === '' || !Number.isFinite(v)) {
    ruleError.value = { ...ruleError.value, [r.id]: '請輸入數字' }
    return
  }
  saveRule(r, { threshold_value: v, severity: draft.severity }, '已更新門檻')
}
async function runTrial(r) {
  const t = trial[r.id]
  const v = Number(t.value)
  if (t.value === '' || !Number.isFinite(v)) {
    t.result = { error: '請輸入數字' }
    return
  }
  try {
    t.result = await store.testRule(r.id, { value: v, in_nadir: t.in_nadir })
  } catch (e) {
    t.result = { error: e.message }
  }
}
const openTrial = (r) => (trial[r.id] = trial[r.id] ?? { value: '', in_nadir: false, result: null })

// ---- symptom forms
const formDraft = reactive({}) // code → items [{ definition_code, label, is_required }]
const formNotice = ref('')
const formError = ref({})
function editForm(f) {
  formDraft[f.code] = f.items.map((i) => ({ definition_code: i.definition_code, label: i.label, is_required: i.is_required }))
}
function move(code, n, by) {
  const list = formDraft[code]
  const [it] = list.splice(n, 1)
  list.splice(n + by, 0, it)
}
async function saveForm(f) {
  formError.value = { ...formError.value, [f.code]: '' }
  try {
    const updated = await store.updateForm(f.code, { items: formDraft[f.code].map(({ definition_code, is_required }) => ({ definition_code, is_required })) })
    formNotice.value = `${updated.name}：已儲存（第 ${updated.version} 版）`
    delete formDraft[f.code]
  } catch (e) {
    formError.value = { ...formError.value, [f.code]: e.details?.map((d) => d.issue).join('；') || e.message }
  }
}

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchRules()
  store.fetchForms()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-rules" />
    <main class="min-w-0 space-y-5 p-4 lg:p-6">
      <header>
        <h1 class="text-2xl font-bold">風險規則與量表</h1>
        <p class="text-ink-soft">變更只影響之後送出的紀錄；已產生的警示與既有紀錄不會重新計算。每次變更都會留下稽核紀錄。</p>
      </header>

      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="rules-title">
        <h2 id="rules-title" class="border-b border-line px-5 py-3 text-lg font-bold">風險警示規則</h2>
        <p v-if="ruleNotice" class="mx-5 mt-3 rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-rule-notice>{{ ruleNotice }}</p>
        <p v-if="store.errors.rules" class="p-5 text-critical" role="alert">{{ store.errors.rules.message }}</p>
        <ul v-else-if="store.rules" class="divide-y divide-line" data-rule-list>
          <li v-for="r in store.rules" :key="r.id" class="px-5 py-4" :data-rule="r.code" :data-active="r.is_active ? 'true' : 'false'">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0" :class="r.is_active ? '' : 'opacity-60'">
                <p>
                  <span class="font-bold">{{ r.name }}</span>
                  <span class="ml-2 rounded-full px-2 py-0.5 text-sm" :class="r.severity === 'critical' ? 'bg-critical-soft text-critical' : 'bg-warn-soft text-warn'">{{ r.severity === 'critical' ? '緊急' : '注意' }}</span>
                  <span v-if="!r.is_active" class="ml-1 rounded-full bg-mist px-2 py-0.5 text-sm" data-rule-off>已停用</span>
                </p>
                <p class="text-ink-soft" data-rule-condition>
                  {{ SOURCE_TEXT[r.source_type] }}：{{ r.target?.label }} {{ r.operator }} <span class="font-bold text-ink tabular-nums">{{ r.threshold_value }}</span>
                  <template v-for="c in conditions(r.extra_conditions)" :key="c">，{{ c }}</template>
                </p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-edit-rule @click="edit(r)">調整門檻</button>
                <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-try-rule @click="openTrial(r)">試算</button>
                <button type="button" class="min-h-11 rounded-full border px-4" :class="r.is_active ? 'border-line text-critical hover:bg-critical-soft' : 'border-care text-care hover:bg-care-soft'"
                  data-toggle-rule @click="saveRule(r, { is_active: !r.is_active }, r.is_active ? '已停用' : '已啟用')">
                  {{ r.is_active ? '停用' : '啟用' }}
                </button>
              </div>
            </div>

            <form v-if="editing === r.id" class="mt-3 flex flex-wrap items-end gap-3 rounded-xl bg-mist p-3" novalidate data-rule-form @submit.prevent="submitThreshold(r)">
              <label class="block"><span class="text-sm">門檻（{{ r.target?.label }} {{ r.operator }}）</span>
                <input v-model="draft.threshold_value" name="threshold_value" inputmode="decimal" class="mt-1 block min-h-12 w-32 rounded-xl border border-line bg-surface px-3" />
              </label>
              <label class="block"><span class="text-sm">等級</span>
                <select v-model="draft.severity" name="severity" class="mt-1 block min-h-12 rounded-xl border border-line bg-surface px-3">
                  <option value="critical">緊急</option><option value="warning">注意</option>
                </select>
              </label>
              <button type="submit" class="min-h-12 rounded-full bg-care px-5 font-bold text-white">儲存</button>
              <button type="button" class="min-h-12 rounded-full px-4 text-ink-soft hover:bg-surface" @click="editing = null">取消</button>
            </form>

            <form v-if="trial[r.id]" class="mt-3 flex flex-wrap items-end gap-3 rounded-xl border border-dashed border-line p-3" novalidate data-trial-form @submit.prevent="runTrial(r)">
              <label class="block"><span class="text-sm">試算數值</span>
                <input v-model="trial[r.id].value" name="value" inputmode="decimal" class="mt-1 block min-h-12 w-32 rounded-xl border border-line px-3" />
              </label>
              <label v-if="r.extra_conditions?.within_nadir || r.extra_conditions?.outside_nadir" class="flex min-h-12 items-center gap-2">
                <input v-model="trial[r.id].in_nadir" type="checkbox" name="in_nadir" class="size-5" /> 在低谷期
              </label>
              <button type="submit" class="min-h-12 rounded-full bg-ink px-5 font-bold text-white">試算</button>
              <p v-if="trial[r.id].result" class="min-h-12 content-center" role="status" data-trial-result>
                <template v-if="trial[r.id].result.error"><span class="text-critical">{{ trial[r.id].result.error }}</span></template>
                <template v-else>
                  <span class="font-bold" :class="trial[r.id].result.matches ? 'text-critical' : 'text-ok'">{{ trial[r.id].result.matches ? '會產生警示' : '不會產生警示' }}</span>
                  <span v-if="!trial[r.id].result.is_active" class="text-ink-soft">（規則停用中，實際不會發出）</span>
                  <span class="text-ink-soft">。只是試算，不會發出通知。</span>
                </template>
              </p>
            </form>
            <p v-if="ruleError[r.id]" class="mt-2 text-sm text-critical" role="alert" data-rule-error>{{ ruleError[r.id] }}</p>
          </li>
        </ul>
        <div v-else class="space-y-3 p-5" aria-busy="true">
          <div v-for="k in 4" :key="k" class="h-12 animate-pulse rounded bg-mist" />
        </div>
      </section>

      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="forms-title">
        <h2 id="forms-title" class="border-b border-line px-5 py-3 text-lg font-bold">症狀自評量表</h2>
        <p v-if="formNotice" class="mx-5 mt-3 rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-form-notice>{{ formNotice }}</p>
        <p v-if="store.errors.forms" class="p-5 text-critical" role="alert">{{ store.errors.forms.message }}</p>
        <div v-else-if="store.forms" class="divide-y divide-line">
          <article v-for="f in store.forms" :key="f.code" class="px-5 py-4" :data-form="f.code">
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <h3 class="font-bold">{{ f.name }} <span class="font-normal text-ink-soft" data-form-version>第 {{ f.version }} 版</span></h3>
              <button v-if="!formDraft[f.code]" type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-edit-form @click="editForm(f)">編輯題目</button>
            </div>
            <ol v-if="!formDraft[f.code]" class="mt-2 space-y-1">
              <li v-for="i in f.items" :key="i.definition_code">{{ i.display_order }}. {{ i.label }} <span v-if="i.is_required" class="text-sm text-ink-soft">（必填）</span></li>
            </ol>
            <form v-else class="mt-3 space-y-2" data-form-editor @submit.prevent="saveForm(f)">
              <ol class="space-y-2">
                <li v-for="(i, n) in formDraft[f.code]" :key="i.definition_code" class="flex flex-wrap items-center gap-2 rounded-xl bg-mist px-3 py-2" :data-item="i.definition_code">
                  <span class="min-w-0 flex-1 font-medium">{{ n + 1 }}. {{ i.label }}</span>
                  <label class="flex min-h-11 items-center gap-2"><input v-model="i.is_required" type="checkbox" class="size-5" :name="`required-${i.definition_code}`" /> 必填</label>
                  <button type="button" class="min-h-11 rounded-full px-3 hover:bg-surface disabled:opacity-40" :disabled="n === 0" :aria-label="`${i.label} 往上移`" @click="move(f.code, n, -1)">上移</button>
                  <button type="button" class="min-h-11 rounded-full px-3 hover:bg-surface disabled:opacity-40" :disabled="n === formDraft[f.code].length - 1" :aria-label="`${i.label} 往下移`" @click="move(f.code, n, 1)">下移</button>
                </li>
              </ol>
              <p class="text-sm text-ink-soft">題目內容（問句、分數範圍）已被病人使用過，不能在這裡修改；可調整順序與是否必填。</p>
              <p v-if="formError[f.code]" class="text-sm text-critical" role="alert">{{ formError[f.code] }}</p>
              <div class="flex gap-2">
                <button type="submit" class="min-h-12 rounded-full bg-care px-5 font-bold text-white" data-save-form>儲存量表</button>
                <button type="button" class="min-h-12 rounded-full px-4 text-ink-soft hover:bg-mist" @click="delete formDraft[f.code]">取消</button>
              </div>
            </form>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>
