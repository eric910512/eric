<script setup>
import { computed, onMounted, reactive } from 'vue'

import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'
import { useDashboardStore } from '@/stores/dashboard'
import { formatTime, localDate } from '@/utils/format'

/**
 * 稽核紀錄 (admin): who did what, when, to which record. The search itself is audited by the
 * backend. Entries never contain passwords; changes are shown as recorded.
 */
const dashboard = useDashboardStore()
const store = useAdminStore()

const ACTION_TEXT = {
  CREATE: '建立', UPDATE: '修改', AMEND: '修正', MARK_ERROR: '標記錯誤', DELETE: '刪除', VIEW: '查看', ASSIGN: '指派', SIGN: '簽署',
  ACKNOWLEDGE: '接手', LOGIN: '登入', LOGIN_FAILED: '登入失敗', LOGOUT: '登出', PERMISSION_DENIED: '拒絕存取', EXPORT: '匯出',
}
const RESOURCE_TEXT = {
  users: '帳號', patient_profiles: '病人資料', nurse_patient_assignments: '照護指派', patient_care_alerts: '照護注意事項', cancer_diagnoses: '診斷',
  alert_rules: '風險規則', symptom_forms: '症狀量表', audit_logs: '稽核紀錄', symptom_records: '症狀紀錄', vital_signs: '生命徵象',
  lab_results: '檢驗結果', notifications: '通知', nursing_assessments: '護理評估', nursing_assessment_items: '護理評估項目',
  drugs: '藥品', chemo_regimens: '化療處方', chemotherapy_plans: '化療計畫', chemotherapy_cycles: '化療 Cycle', medication_records: '給藥紀錄',
  appointments: '治療行程', dashboard: '儀表板', patient_timeline: '照護時間軸',
}
const filters = reactive({ category: '', action: '', resource_type: '', outcome: '', from: '', to: '' })
const page = computed(() => store.audit?.meta.page ?? 1)
const pages = computed(() => (store.audit ? Math.max(1, Math.ceil(store.audit.meta.total / store.audit.meta.per_page)) : 1))
const fieldError = computed(() => Object.fromEntries((store.errors.audit?.details ?? []).map((d) => [d.field, d.issue])))

const search = (p = 1) => store.searchAudit({ ...filters, page: p })
const stamp = (iso) => `${localDate(iso)} ${formatTime(iso)}`
const who = (a) => (a ? a.display_name ?? a.identifier ?? '—' : '系統')
const changesText = (c) => (c ? JSON.stringify(c) : '')

onMounted(() => {
  dashboard.fetchSettings()
  search()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-audit" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header>
        <h1 class="text-2xl font-bold">稽核紀錄</h1>
        <p class="text-ink-soft">帳號、照護團隊、臨床紀錄與設定的所有變更，以及病人資料的查看紀錄。查詢本身也會被記錄。</p>
      </header>

      <form class="grid gap-3 rounded-2xl border border-line bg-surface p-4 sm:grid-cols-3 lg:grid-cols-6" role="search" data-audit-filters @submit.prevent="search()">
        <label class="block"><span class="text-sm text-ink-soft">類別</span>
          <select v-model="filters.category" name="category" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-3">
            <option value="">全部</option><option value="data">資料變更</option><option value="access">查看</option><option value="auth">登入登出</option><option value="admin">管理</option>
          </select>
        </label>
        <label class="block"><span class="text-sm text-ink-soft">動作</span>
          <select v-model="filters.action" name="action" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-3">
            <option value="">全部</option>
            <option v-for="(t, k) in ACTION_TEXT" :key="k" :value="k">{{ t }}</option>
          </select>
        </label>
        <label class="block"><span class="text-sm text-ink-soft">對象</span>
          <select v-model="filters.resource_type" name="resource_type" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-3">
            <option value="">全部</option>
            <option v-for="(t, k) in RESOURCE_TEXT" :key="k" :value="k">{{ t }}</option>
          </select>
        </label>
        <label class="block"><span class="text-sm text-ink-soft">結果</span>
          <select v-model="filters.outcome" name="outcome" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-3">
            <option value="">全部</option><option value="success">成功</option><option value="failure">失敗</option>
          </select>
        </label>
        <label class="block"><span class="text-sm text-ink-soft">從</span>
          <input v-model="filters.from" type="date" name="from" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-3" />
        </label>
        <label class="block"><span class="text-sm text-ink-soft">到</span>
          <input v-model="filters.to" type="date" name="to" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-3" :aria-invalid="!!fieldError.to" />
        </label>
        <div class="flex items-center gap-3 sm:col-span-3 lg:col-span-6">
          <button type="submit" class="min-h-12 rounded-full bg-ink px-6 font-bold text-white">查詢</button>
          <p v-if="store.errors.audit" class="text-critical" role="alert" data-audit-error>
            {{ fieldError.to === 'must not be earlier than from' ? '結束日期不能早於開始日期' : store.errors.audit.message }}
          </p>
        </div>
      </form>

      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="audit-title">
        <h2 id="audit-title" class="border-b border-line px-5 py-3 text-lg font-bold">
          紀錄 <span v-if="store.audit" class="text-base font-normal text-ink-soft" data-audit-total>共 {{ store.audit.meta.total }} 筆</span>
        </h2>
        <p v-if="store.audit && !store.audit.items.length" class="p-5 text-ink-soft">沒有符合條件的紀錄。</p>
        <ol v-else-if="store.audit" class="divide-y divide-line" data-audit-list>
          <li v-for="r in store.audit.items" :key="r.id" class="px-5 py-3" :data-audit-row="r.id" :data-action="r.action" :data-resource="r.resource_type">
            <p class="flex flex-wrap items-baseline gap-x-2">
              <span class="tabular-nums text-ink-soft">{{ stamp(r.occurred_at) }}</span>
              <span class="font-bold">{{ who(r.actor) }}</span>
              <span>{{ ACTION_TEXT[r.action] ?? r.action }}</span>
              <span>{{ RESOURCE_TEXT[r.resource_type] ?? r.resource_type }}</span>
              <span v-if="r.patient" class="text-ink-soft">{{ r.patient.patient_code }}</span>
              <span v-if="r.outcome !== 'success'" class="rounded-full bg-critical-soft px-2 py-0.5 text-sm text-critical">失敗</span>
            </p>
            <p v-if="r.reason" class="text-sm text-ink-soft">原因：{{ r.reason }}</p>
            <details v-if="r.changes" class="mt-1 text-sm">
              <summary class="cursor-pointer text-care">內容</summary>
              <pre class="mt-1 overflow-x-auto rounded-lg bg-mist p-3 text-xs whitespace-pre-wrap break-all">{{ changesText(r.changes) }}</pre>
            </details>
          </li>
        </ol>
        <div v-else class="space-y-3 p-5" aria-busy="true">
          <div v-for="k in 4" :key="k" class="h-10 animate-pulse rounded bg-mist" />
        </div>
        <nav v-if="store.audit && pages > 1" class="flex items-center justify-between border-t border-line px-5 py-3" aria-label="分頁">
          <button type="button" class="min-h-11 rounded-full px-4 hover:bg-mist disabled:opacity-40" :disabled="page <= 1" @click="search(page - 1)">上一頁</button>
          <span class="text-ink-soft">第 {{ page }} / {{ pages }} 頁</span>
          <button type="button" class="min-h-11 rounded-full px-4 hover:bg-mist disabled:opacity-40" :disabled="page >= pages" @click="search(page + 1)">下一頁</button>
        </nav>
      </section>
    </main>
  </div>
</template>
