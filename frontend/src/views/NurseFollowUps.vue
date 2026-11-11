<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import NurseNav from '@/components/NurseNav.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { useFollowUpStore } from '@/stores/followUps'
import { formatTime, localDate } from '@/utils/format'

/**
 * 追蹤管理 (nurse, Phase 1c): follow-ups of the patients currently assigned to me — 待追蹤 / 已逾期 / 已完成 /
 * 已取消 / 全部, patient filter, sort by due date, pages; the detail with its history and the actions
 * (改期 / 結案 / 取消) for open ones. Overdue is a work-list hint only: nothing is sent to anyone.
 */
const store = useFollowUpStore()
const dashboard = useDashboardStore()
const route = useRoute()
const router = useRouter()
const TZ = 'Asia/Taipei'
const TABS = [
  { key: 'open', label: '待追蹤' }, { key: 'overdue', label: '已逾期' }, { key: 'completed', label: '已完成' },
  { key: 'cancelled', label: '已取消' }, { key: 'all', label: '全部' },
]
const STATUS = { open: '待追蹤', completed: '已完成', cancelled: '已取消' }
const EVENT = { created: '建立', rescheduled: '改期', completed: '結案', cancelled: '取消' }
const when = (iso) => (iso ? `${localDate(iso, TZ)} ${formatTime(iso, TZ)}` : '')
const today = () => localDate(new Date().toISOString(), TZ)
const newKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
const toIso = (d, t) => `${d}T${t || '09:00'}:00+08:00` // Asia/Taipei has no daylight saving time

const list = computed(() => store.list)
const selectedId = computed(() => route.query.id || null)
const selected = computed(() => (selectedId.value ? store.detail[selectedId.value] : null))
const events = computed(() => (selectedId.value ? store.events[selectedId.value] ?? [] : []))
const patients = computed(() => dashboard.nurseOverview?.caseload.data ?? [])
const totalPages = computed(() => Math.max(1, Math.ceil((list.value?.meta.total ?? 0) / store.query.perPage)))

function load(changes = {}) {
  return store.fetchList(changes)
}
function open(id) {
  router.replace({ query: { ...route.query, id } })
}
watch(selectedId, (id) => id && store.fetchDetail(id), { immediate: true })
onMounted(() => {
  load({ status: route.query.status || 'open', page: 1 })
  if (!dashboard.nurseOverview) dashboard.fetchNurseOverview()
})

// ---------------------------------------------------------------- actions (dialog)
const dialog = ref(null) // reschedule | complete | cancel
const form = reactive({})
const busy = ref(false)
const actionError = ref(null)
const conflict = ref(false)
let key = null

function start(kind) {
  dialog.value = kind
  actionError.value = null
  conflict.value = false
  key = newKey()
  for (const k of Object.keys(form)) delete form[k]
  Object.assign(form, { date: '', time: '09:00', reason: '', result: '', actions: '', assessment_type: 'phone_follow_up', next: false,
    next_date: '', next_time: '09:00', next_reason: '' })
}

async function submit() {
  if (busy.value) return
  const f = selected.value
  let body
  if (dialog.value === 'reschedule') body = { due_at: form.date ? toIso(form.date, form.time) : null, reason: form.reason, version: f.version }
  else if (dialog.value === 'cancel') body = { reason: form.reason, version: f.version }
  else {
    body = { result: form.result, actions: form.actions, assessment_type: form.assessment_type, version: f.version,
      ...(form.next ? { next_follow_up: { due_at: form.next_date ? toIso(form.next_date, form.next_time) : null, reason: form.next_reason } } : {}) }
  }
  busy.value = true
  actionError.value = null
  try {
    const data = await store.act(f.id, dialog.value, body, key)
    dialog.value = null
    await Promise.all([store.fetchDetail(f.id), load()])
    if (data.next_follow_up) open(data.next_follow_up.id)
  } catch (err) {
    actionError.value = err
    const retryable = !err.status || err.status >= 500 || err.code === 'IDEMPOTENCY_IN_PROGRESS'
    if (!retryable) key = newKey()
    if (err.code === 'VERSION_CONFLICT' || err.code === 'FOLLOW_UP_CLOSED') {
      conflict.value = true // someone else changed it: show the current state
      await Promise.all([store.fetchDetail(f.id), load()])
    }
  } finally {
    busy.value = false
  }
}
const fieldError = (name) => actionError.value?.details?.some((d) => d.field === name || d.field.startsWith(`${name}.`))
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="nurse-follow-ups" />

    <main class="min-w-0 p-4 lg:p-6" data-follow-ups-page>
      <header class="flex flex-wrap items-end gap-3">
        <div class="mr-auto">
          <RouterLink :to="{ name: 'nurse' }" class="text-sm text-care hover:underline lg:hidden">← 照護總覽</RouterLink>
          <h1 class="text-2xl font-bold">追蹤管理</h1>
          <p class="text-ink-soft">您目前負責病人的護理追蹤。逾期只是工作提示，系統不會自動通知任何人。</p>
        </div>
      </header>

      <div class="mt-4 flex flex-wrap items-center gap-2">
        <div class="flex flex-wrap rounded-full border border-line bg-surface p-0.5" role="tablist" aria-label="追蹤狀態">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="store.query.status === t.key"
            class="min-h-10 rounded-full px-3 text-sm" :class="store.query.status === t.key ? 'bg-care font-bold text-white' : 'text-ink-soft hover:text-ink'"
            :data-follow-up-tab="t.key" @click="load({ status: t.key, page: 1 })">
            {{ t.label }}<template v-if="list?.meta.counts">（{{ list.meta.counts[t.key] }}）</template>
          </button>
        </div>
        <select :value="store.query.patientId ?? ''" class="min-h-10 rounded-lg border border-line bg-surface px-2 text-sm" aria-label="病人"
          data-follow-up-patient @change="load({ patientId: $event.target.value || null, page: 1 })">
          <option value="">全部病人</option>
          <option v-for="p in patients" :key="p.patient_id" :value="p.patient_id">{{ p.display_name }}（{{ p.patient_code }}）</option>
        </select>
        <select :value="store.query.sort" class="min-h-10 rounded-lg border border-line bg-surface px-2 text-sm" aria-label="排序"
          data-follow-up-sort @change="load({ sort: $event.target.value, page: 1 })">
          <option value="due_at">預定日期：近 → 遠</option>
          <option value="-due_at">預定日期：遠 → 近</option>
          <option value="-updated_at">最近更新</option>
        </select>
      </div>

      <div class="mt-4 grid gap-4 xl:grid-cols-[1fr_26rem]">
        <section class="min-w-0 rounded-2xl border border-line bg-surface" aria-label="追蹤清單">
          <p v-if="store.listError" class="p-5 text-critical" role="alert">{{ store.listError.message }}</p>
          <div v-else-if="!list" class="space-y-2 p-5" aria-busy="true"><div v-for="n in 3" :key="n" class="h-16 animate-pulse rounded-xl bg-mist" /></div>
          <p v-else-if="!list.items.length" class="p-5 text-ink-soft" data-follow-up-empty>沒有符合條件的追蹤。</p>
          <ul v-else class="divide-y divide-line">
            <li v-for="f in list.items" :key="f.id">
              <button type="button" class="flex w-full flex-wrap items-start gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-mist"
                :class="f.id === selectedId ? 'bg-care-soft' : ''" :data-follow-up-row="f.id" @click="open(f.id)">
                <span class="min-w-0 flex-1">
                  <span class="flex flex-wrap items-baseline gap-x-2">
                    <span class="font-bold">{{ f.patient.display_name }}</span>
                    <span class="text-sm text-ink-soft">{{ f.patient.patient_code }}</span>
                    <span class="text-sm text-ink-soft">回報 {{ localDate(f.symptom_record.recorded_at, TZ) }}</span>
                  </span>
                  <span class="mt-0.5 block truncate">{{ f.reason }}</span>
                  <span class="block text-xs text-ink-soft">{{ f.created_by?.display_name }} 建立・更新 {{ when(f.updated_at) }}</span>
                </span>
                <span class="text-right text-sm">
                  <span class="block font-bold" :class="f.overdue ? 'text-critical' : ''">{{ when(f.due_at) }}</span>
                  <span class="mt-0.5 inline-block rounded-full px-2 py-0.5 text-xs font-bold"
                    :class="f.overdue ? 'bg-critical-soft text-critical' : f.status === 'open' ? 'bg-warn-soft text-warn' : f.status === 'completed' ? 'bg-ok-soft text-ok' : 'bg-mist text-ink-soft'"
                    :data-follow-up-state="f.overdue ? 'overdue' : f.status">{{ f.overdue ? '已逾期' : STATUS[f.status] }}</span>
                </span>
              </button>
            </li>
          </ul>
          <nav v-if="list && totalPages > 1" class="flex items-center justify-between border-t border-line px-4 py-2 text-sm" aria-label="分頁">
            <button type="button" class="min-h-10 rounded-full px-3 hover:bg-mist disabled:opacity-40" :disabled="store.query.page <= 1"
              data-follow-up-prev @click="load({ page: store.query.page - 1 })">上一頁</button>
            <span>第 {{ store.query.page }} / {{ totalPages }} 頁</span>
            <button type="button" class="min-h-10 rounded-full px-3 hover:bg-mist disabled:opacity-40" :disabled="store.query.page >= totalPages"
              data-follow-up-next @click="load({ page: store.query.page + 1 })">下一頁</button>
          </nav>
        </section>

        <!-- detail + history + actions -->
        <section v-if="selectedId" class="min-w-0 rounded-2xl border border-line bg-surface p-5" aria-label="追蹤內容" data-follow-up-detail>
          <p v-if="store.errors[selectedId]" class="text-critical" role="alert">{{ store.errors[selectedId].status === 404 ? '找不到這筆追蹤，或您目前沒有這位病人的照護權限。' : store.errors[selectedId].message }}</p>
          <div v-else-if="!selected" class="h-32 animate-pulse rounded-xl bg-mist" aria-busy="true" />
          <template v-else>
            <div class="flex flex-wrap items-baseline gap-2">
              <h2 class="text-lg font-bold">{{ selected.patient.display_name }}</h2>
              <span class="text-sm text-ink-soft">{{ selected.patient.patient_code }}</span>
              <span class="ml-auto rounded-full px-2 py-0.5 text-sm font-bold"
                :class="selected.overdue ? 'bg-critical-soft text-critical' : selected.status === 'open' ? 'bg-warn-soft text-warn' : selected.status === 'completed' ? 'bg-ok-soft text-ok' : 'bg-mist text-ink-soft'"
                data-follow-up-detail-state>{{ selected.overdue ? '已逾期' : STATUS[selected.status] }}</span>
            </div>
            <dl class="mt-3 grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1 text-sm">
              <dt class="text-ink-soft">預定追蹤</dt><dd class="font-bold" data-follow-up-due>{{ when(selected.due_at) }}</dd>
              <dt class="text-ink-soft">追蹤原因</dt><dd class="whitespace-pre-line">{{ selected.reason }}</dd>
              <dt class="text-ink-soft">症狀回報</dt>
              <dd><RouterLink :to="{ name: 'nurse', params: { patientId: selected.patient.id } }" class="text-care underline underline-offset-2"
                data-follow-up-record>{{ when(selected.symptom_record.recorded_at) }}</RouterLink></dd>
              <dt class="text-ink-soft">建立</dt><dd>{{ selected.created_by?.display_name }}，{{ when(selected.created_at) }}</dd>
              <template v-if="selected.status === 'completed'">
                <dt class="text-ink-soft">結案</dt><dd>{{ selected.completed_by?.display_name }}，{{ when(selected.completed_at) }}</dd>
                <dt class="text-ink-soft">追蹤結果</dt><dd class="whitespace-pre-line" data-follow-up-result>{{ selected.completion?.result }}</dd>
                <dt class="text-ink-soft">處置</dt><dd class="whitespace-pre-line">{{ selected.completion?.actions }}</dd>
                <dt class="text-ink-soft">護理紀錄</dt><dd>已簽署的護理評估（{{ when(selected.completion?.signed_at) }}）</dd>
              </template>
              <template v-if="selected.status === 'cancelled'">
                <dt class="text-ink-soft">取消</dt><dd>{{ selected.cancelled_by?.display_name }}，{{ when(selected.cancelled_at) }}</dd>
                <dt class="text-ink-soft">取消原因</dt><dd class="whitespace-pre-line">{{ selected.cancellation_reason }}</dd>
              </template>
            </dl>
            <p class="mt-2 flex flex-wrap gap-3 text-sm">
              <button v-if="selected.previous_follow_up_id" type="button" class="text-care underline underline-offset-2" data-follow-up-previous
                @click="open(selected.previous_follow_up_id)">← 前一次追蹤</button>
              <button v-for="n in selected.next_follow_up_ids" :key="n" type="button" class="text-care underline underline-offset-2" data-follow-up-next-link
                @click="open(n)">下一次追蹤 →</button>
            </p>

            <p v-if="conflict" class="mt-3 rounded-xl bg-warn-soft px-3 py-2 text-sm text-warn" role="alert" data-follow-up-conflict>
              這筆追蹤剛被其他護理師更新，已顯示最新內容，請確認後再操作。
            </p>
            <div v-if="selected.status === 'open'" class="mt-4 flex flex-wrap gap-2">
              <button type="button" class="min-h-11 rounded-full bg-care px-4 font-bold text-white" data-follow-up-action="complete" @click="start('complete')">結案</button>
              <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-follow-up-action="reschedule" @click="start('reschedule')">改期</button>
              <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-follow-up-action="cancel" @click="start('cancel')">取消追蹤</button>
            </div>

            <h3 class="mt-5 font-bold">追蹤歷史</h3>
            <ol class="mt-2 space-y-2 border-l-2 border-line pl-3 text-sm" data-follow-up-events>
              <li v-for="(e, i) in events" :key="i" :data-follow-up-event="e.event_type">
                <p><span class="font-bold">{{ EVENT[e.event_type] }}</span>・{{ when(e.occurred_at) }}・{{ e.actor?.display_name }}</p>
                <p v-if="e.event_type === 'rescheduled'" class="text-ink-soft">{{ when(e.old_due_at) }} → {{ when(e.new_due_at) }}</p>
                <p v-else-if="e.event_type === 'created'" class="text-ink-soft">預定 {{ when(e.new_due_at) }}</p>
                <p v-if="e.reason" class="whitespace-pre-line">{{ e.reason }}</p>
              </li>
            </ol>
          </template>
        </section>
      </div>

      <!-- action dialog -->
      <div v-if="dialog && selected" class="fixed inset-0 z-50 flex items-end justify-center bg-ink/60 p-3 sm:items-center" role="dialog" aria-modal="true"
        :aria-label="{ reschedule: '改期', complete: '結案', cancel: '取消追蹤' }[dialog]" data-follow-up-dialog @click.self="dialog = null">
        <form class="max-h-full w-full max-w-lg space-y-3 overflow-auto rounded-2xl bg-surface p-5" novalidate @submit.prevent="submit">
          <h2 class="text-lg font-bold">{{ { reschedule: '改期', complete: '結案', cancel: '取消追蹤' }[dialog] }}：{{ selected.patient.display_name }}</h2>
          <template v-if="dialog === 'reschedule'">
            <p class="text-sm text-ink-soft">目前預定：{{ when(selected.due_at) }}（原日期會保留在歷史中）</p>
            <div class="flex flex-wrap gap-2">
              <input v-model="form.date" type="date" :min="today()" class="min-h-11 rounded-lg border border-line bg-surface px-2" aria-label="新日期" data-dialog-date />
              <input v-model="form.time" type="time" class="min-h-11 rounded-lg border border-line bg-surface px-2" aria-label="新時間" data-dialog-time />
            </div>
            <textarea v-model="form.reason" rows="2" maxlength="1000" placeholder="改期原因（必填）" class="w-full rounded-xl border px-3 py-2"
              :class="fieldError('reason') ? 'border-critical' : 'border-line'" data-dialog-reason />
          </template>
          <template v-else-if="dialog === 'cancel'">
            <p class="text-sm text-ink-soft">取消不會刪除追蹤或症狀回報，會保留在歷史中。</p>
            <textarea v-model="form.reason" rows="2" maxlength="1000" placeholder="取消原因（必填）" class="w-full rounded-xl border px-3 py-2"
              :class="fieldError('reason') ? 'border-critical' : 'border-line'" data-dialog-reason />
          </template>
          <template v-else>
            <p class="text-sm text-ink-soft">結案會新增一筆已簽署的護理評估（追蹤結果與處置），原本的審閱紀錄不會被修改。</p>
            <div class="flex flex-wrap gap-2 text-sm">
              <label v-for="(l, v) in { phone_follow_up: '電話追蹤', follow_up: '一般追蹤' }" :key="v" class="flex items-center gap-1">
                <input v-model="form.assessment_type" type="radio" :value="v" class="accent-care" /> {{ l }}
              </label>
            </div>
            <textarea v-model="form.result" rows="3" maxlength="2000" placeholder="追蹤結果（必填）" class="w-full rounded-xl border px-3 py-2"
              :class="fieldError('result') ? 'border-critical' : 'border-line'" data-dialog-result />
            <textarea v-model="form.actions" rows="2" maxlength="2000" placeholder="處理方式／護理措施（必填）" class="w-full rounded-xl border px-3 py-2"
              :class="fieldError('actions') ? 'border-critical' : 'border-line'" data-dialog-actions />
            <label class="flex items-center gap-2"><input v-model="form.next" type="checkbox" class="size-5 accent-care" data-dialog-next /> 需要再次追蹤</label>
            <template v-if="form.next">
              <div class="flex flex-wrap gap-2">
                <input v-model="form.next_date" type="date" :min="today()" class="min-h-11 rounded-lg border border-line bg-surface px-2" aria-label="下次追蹤日期" data-dialog-next-date />
                <input v-model="form.next_time" type="time" class="min-h-11 rounded-lg border border-line bg-surface px-2" aria-label="下次追蹤時間" />
              </div>
              <textarea v-model="form.next_reason" rows="2" maxlength="1000" placeholder="下次追蹤原因（必填）" class="w-full rounded-xl border border-line px-3 py-2" data-dialog-next-reason />
            </template>
          </template>
          <p v-if="actionError" class="rounded-xl bg-critical-soft px-3 py-2 text-sm text-critical" role="alert" data-dialog-error>
            {{ actionError.code === 'VALIDATION_ERROR' ? '請填寫必填欄位，並確認日期在今天到 180 天內。' : actionError.message }}
          </p>
          <div class="flex flex-wrap gap-2">
            <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white disabled:opacity-60" :disabled="busy" data-dialog-submit>
              {{ busy ? '儲存中…' : '確認' }}</button>
            <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" :disabled="busy" @click="dialog = null">返回</button>
          </div>
        </form>
      </div>
    </main>
  </div>
</template>
