<script setup>
import { computed, reactive, ref } from 'vue'

import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'

/**
 * 批量建立教學帳號 (admin, development / staging only): numbered pairs of a nurse account and a patient
 * with a login account, the nurse as the patient's primary nurse (strictly one to one). Settings →
 * 預覽 (read-only) → 確認 (shared initial password, confirmation) → created in requests of 10 → results
 * (成功 / 已存在 / 衝突 / 失敗) and a CSV without the password. Existing accounts are never changed.
 */
const store = useAdminStore()
const form = reactive({
  start: 1, count: 50, patient_prefix: 'patientfyu', nurse_prefix: 'nursefyu', domain: 'demo.local',
  patient_name_prefix: '學生病人', nurse_name_prefix: '學生護理師',
})
const step = ref('settings') // settings | preview | creating | done
const preview = ref(null)
const results = ref(null)
const password = ref('')
const confirmed = ref(false)
const progress = ref({ done: 0, total: 0 })
const error = ref('')
const fieldErrors = ref({})

const STATUS = { will_create: '將建立', created: '成功', exists: '已存在', conflict: '衝突', failed: '失敗' }
const STATUS_CLASS = { will_create: 'bg-care-soft text-care', created: 'bg-ok-soft text-ok', exists: 'bg-mist text-ink-soft', conflict: 'bg-warn-soft text-warn', failed: 'bg-critical-soft text-critical' }
const spec = () => ({ ...form, start: Number(form.start), count: Number(form.count) })
const toCreate = computed(() => preview.value?.summary.will_create ?? 0)

function showError(e) {
  fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, d.issue]))
  error.value = e.status === 403 ? (e.code === 'TRAINING_ACCOUNTS_DISABLED' ? '此環境不允許建立教學帳號（僅限開發 / staging）。' : '只有管理者可以使用這個功能。') : e.message
}

async function runPreview() {
  error.value = ''
  fieldErrors.value = {}
  try {
    preview.value = await store.previewTraining(spec())
    results.value = null
    step.value = 'preview'
  } catch (e) {
    showError(e)
  }
}

async function create() {
  error.value = ''
  fieldErrors.value = {}
  if (!confirmed.value) {
    error.value = '請勾選確認'
    return
  }
  step.value = 'creating'
  progress.value = { done: 0, total: Number(form.count) }
  try {
    results.value = await store.createTraining(spec(), password.value, (done, total) => { progress.value = { done, total } })
    step.value = 'done'
  } catch (e) {
    showError(e)
    step.value = 'preview'
  } finally {
    password.value = '' // never kept after the request
    confirmed.value = false
  }
}

function reset() {
  step.value = 'settings'
  preview.value = null
  results.value = null
  error.value = ''
}

/** CSV of the results (UTF-8 with BOM for Excel). No password column. */
function downloadCsv() {
  const head = ['編號', '病人帳號', '病人名稱', '病人代碼', '護理師帳號', '護理師名稱', '主責指派', '結果']
  const esc = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`
  const lines = [head, ...results.value.rows.map((r) => [r.number, r.patient_email, r.patient_name, r.patient_code ?? '', r.nurse_email, r.nurse_name,
    r.primary_assignment ? '是' : '否', STATUS[r.status] + (r.reason ? `：${r.reason}` : '')])].map((row) => row.map(esc).join(','))
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `training-accounts-${form.patient_prefix}-${String(form.start).padStart(3, '0')}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-training" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6" data-training>
      <header>
        <h1 class="text-2xl font-bold">批量建立教學帳號</h1>
        <p class="text-ink-soft">一次建立多組「護理師帳號 + 病人（含登入帳號）」，每位護理師為對應病人的主責護理師，一對一互相隔離。只限開發 / staging 環境；已存在的帳號不會被修改。</p>
      </header>

      <p v-if="error" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-training-error>{{ error }}</p>

      <!-- settings -->
      <form v-if="step === 'settings'" class="grid gap-3 rounded-2xl border border-line bg-surface p-5 sm:grid-cols-2" novalidate data-training-form @submit.prevent="runPreview">
        <label class="block"><span class="font-medium">起始編號</span>
          <input v-model.number="form.start" type="number" min="1" max="999" name="start" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
          <span v-if="fieldErrors.start" class="text-sm text-critical">{{ fieldErrors.start }}</span>
        </label>
        <label class="block"><span class="font-medium">建立數量（最多 50 組）</span>
          <input v-model.number="form.count" type="number" min="1" max="50" name="count" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
          <span v-if="fieldErrors.count" class="text-sm text-critical">{{ fieldErrors.count }}</span>
        </label>
        <label class="block"><span class="font-medium">病人帳號前綴</span>
          <input v-model.trim="form.patient_prefix" name="patient_prefix" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
          <span v-if="fieldErrors.patient_prefix" class="text-sm text-critical">{{ fieldErrors.patient_prefix }}</span>
        </label>
        <label class="block"><span class="font-medium">護理師帳號前綴</span>
          <input v-model.trim="form.nurse_prefix" name="nurse_prefix" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
          <span v-if="fieldErrors.nurse_prefix" class="text-sm text-critical">{{ fieldErrors.nurse_prefix }}</span>
        </label>
        <label class="block"><span class="font-medium">帳號網域</span>
          <input v-model.trim="form.domain" name="domain" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
        </label>
        <div />
        <label class="block"><span class="font-medium">病人名稱前綴</span>
          <input v-model="form.patient_name_prefix" name="patient_name_prefix" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
        </label>
        <label class="block"><span class="font-medium">護理師名稱前綴</span>
          <input v-model="form.nurse_name_prefix" name="nurse_name_prefix" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
        </label>
        <p class="text-sm text-ink-soft sm:col-span-2">例如第 1 組：{{ form.patient_prefix }}001@{{ form.domain }}（{{ form.patient_name_prefix }} 001）↔ {{ form.nurse_prefix }}001@{{ form.domain }}（{{ form.nurse_name_prefix }} 001）</p>
        <div class="sm:col-span-2"><button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90" data-training-preview>預覽</button></div>
      </form>

      <!-- preview + confirm -->
      <section v-if="preview && step === 'preview'" class="space-y-3 rounded-2xl border border-line bg-surface p-5" data-training-preview-result>
        <p class="font-bold">預覽：將建立 {{ preview.summary.will_create }} 組、已存在 {{ preview.summary.exists }} 組、衝突 {{ preview.summary.conflict }} 組（預覽不會建立任何資料）</p>
        <form class="space-y-3 rounded-xl bg-mist p-4" novalidate data-training-confirm @submit.prevent="create">
          <p>將建立 <strong>{{ toCreate }}</strong> 位護理師、<strong>{{ toCreate }}</strong> 位病人與登入帳號、<strong>{{ toCreate }}</strong> 組主責指派；已存在與衝突的組別會略過，不會修改。</p>
          <label class="block max-w-sm"><span class="font-medium">共同初始密碼（8–128 字元，需含英文與數字）</span>
            <input v-model="password" type="password" name="password" autocomplete="new-password" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
            <span v-if="fieldErrors.password" class="text-sm text-critical">{{ fieldErrors.password }}</span>
          </label>
          <p class="text-sm text-ink-soft">密碼只會以雜湊保存，不會顯示在結果、CSV、稽核紀錄或系統記錄中；學生第一次登入不需要再設定新密碼。</p>
          <label class="flex items-center gap-2"><input v-model="confirmed" type="checkbox" name="confirm" class="size-5" /> 我確認要建立以上教學帳號</label>
          <div class="flex flex-wrap gap-2">
            <button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="!toCreate || !password || !confirmed" data-training-create>確認建立</button>
            <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-surface" @click="reset">修改設定</button>
          </div>
        </form>
      </section>

      <p v-if="step === 'creating'" class="rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-training-progress>建立中… {{ progress.done }} / {{ progress.total }}</p>

      <section v-if="results && step === 'done'" class="space-y-3 rounded-2xl border border-line bg-surface p-5" data-training-results>
        <p class="font-bold" data-training-summary>完成：成功 {{ results.summary.created }}、已存在 {{ results.summary.exists }}、衝突 {{ results.summary.conflict }}、失敗 {{ results.summary.failed }}</p>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="min-h-11 rounded-full border border-care px-5 font-bold text-care hover:bg-care-soft" data-training-csv @click="downloadCsv">下載 CSV（不含密碼）</button>
          <button type="button" class="min-h-11 rounded-full px-5 text-ink-soft hover:bg-mist" @click="reset">建立另一批</button>
        </div>
      </section>

      <div v-if="(results && step === 'done') || (preview && step === 'preview')" class="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table class="w-full text-left text-sm" data-training-table>
          <thead class="bg-mist">
            <tr><th class="px-3 py-2">編號</th><th class="px-3 py-2">病人帳號</th><th class="px-3 py-2">病人代碼</th><th class="px-3 py-2">護理師帳號</th><th class="px-3 py-2">主責指派</th><th class="px-3 py-2">結果</th></tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="r in (step === 'done' ? results.rows : preview.rows)" :key="r.number" :data-row="r.number" :data-status="r.status">
              <td class="px-3 py-2">{{ r.number }}</td>
              <td class="px-3 py-2">{{ r.patient_email }}<span class="block text-ink-soft">{{ r.patient_name }}</span></td>
              <td class="px-3 py-2" data-code>{{ r.patient_code ?? '—' }}</td>
              <td class="px-3 py-2">{{ r.nurse_email }}<span class="block text-ink-soft">{{ r.nurse_name }}</span></td>
              <td class="px-3 py-2">{{ r.primary_assignment ? '主責' : (r.status === 'will_create' ? '將設為主責' : '—') }}</td>
              <td class="px-3 py-2"><span class="rounded-full px-2 py-0.5" :class="STATUS_CLASS[r.status]">{{ STATUS[r.status] }}</span><span v-if="r.reason" class="block text-ink-soft">{{ r.reason }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </main>
  </div>
</template>
