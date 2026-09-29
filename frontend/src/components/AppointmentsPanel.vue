<script setup>
import { computed, reactive, ref, watch } from 'vue'

import { useAppointmentsStore } from '@/stores/appointments'
import { formatTime, localDate } from '@/utils/format'

/**
 * 治療行程 (staff): the patient's appointments — create with preparation steps, edit, check in,
 * complete, reschedule (the original stays in the history as 已改期) and cancel. Nurses write,
 * admins read.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  canWrite: { type: Boolean, default: false },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useAppointmentsStore()
const TYPE = { chemo_infusion: '化療注射', lab_draw: '抽血', clinic_visit: '門診', imaging: '影像檢查', radiotherapy: '放射治療', education_session: '衛教', other: '其他' }
const STATUS = { scheduled: '已排定', checked_in: '已報到', completed: '已完成', cancelled: '已取消', no_show: '未到', rescheduled: '已改期' }
const STEP = { fasting: '空腹', check_in: '報到', medication: '用藥', bring_item: '攜帶物品', other: '其他' }
const ISSUE = { 'must be within 30 days ago and one year ahead': '需在 30 天前到一年內', 'must differ from the current time': '需與原時間不同' }

const rows = computed(() => store.byPatient[props.patientId] ?? [])
const now = ref(Date.now())
const upcoming = computed(() => rows.value.filter((a) => ['scheduled', 'checked_in'].includes(a.status)))
const history = computed(() => rows.value.filter((a) => !['scheduled', 'checked_in'].includes(a.status)).reverse())
const error = computed(() => store.errors[props.patientId])
const when = (iso) => `${localDate(iso, props.timezone)} ${formatTime(iso, props.timezone)}`
const isToday = (iso) => localDate(iso, props.timezone) <= localDate(new Date(now.value).toISOString(), props.timezone)
function localInput(iso) {
  const d = new Date(iso)
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
    await fn()
    notice.value = done
    now.value = Date.now()
    return true
  } catch (e) {
    fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field.replace(/^instructions\[\d+\]\./, 'instructions.'), ISSUE[d.issue] ?? d.issue]))
    failure.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
    return false
  }
}

// ------------------------------------------------------------------ create / edit
const form = reactive({ open: false, id: null })
function openForm(a = null) {
  const soon = new Date(Date.now() + 86400000)
  soon.setMinutes(0, 0, 0)
  Object.assign(form, {
    open: true, id: a?.id ?? null, appointment_type: a?.appointment_type ?? 'clinic_visit', title: a?.title ?? '',
    scheduled_at: localInput(a?.scheduled_at ?? soon.toISOString()), duration_min: a?.duration_min ?? '', location: a?.location ?? '',
    notes: a?.notes ?? '', steps: (a?.instructions ?? []).map((i) => ({ instruction_type: i.instruction_type, text: i.text, due_at: i.due_at ? localInput(i.due_at) : '' })),
  })
  fieldErrors.value = {}
}
const addStep = () => form.steps.push({ instruction_type: 'check_in', text: '', due_at: '' })
async function save() {
  const payload = {
    appointment_type: form.appointment_type, title: form.title, duration_min: form.duration_min === '' ? null : Number(form.duration_min),
    location: form.location || null, notes: form.notes || null,
    instructions: form.steps.map((s) => ({ instruction_type: s.instruction_type, text: s.text, ...(s.due_at ? { due_at: toIso(s.due_at) } : {}) })),
  }
  const ok = form.id
    ? await run(() => store.update(props.patientId, form.id, payload), '已更新行程')
    : await run(() => store.create(props.patientId, { ...payload, scheduled_at: toIso(form.scheduled_at) }), '已建立行程')
  if (ok) form.open = false
}

// ------------------------------------------------------------------ actions
const pending = reactive({ id: null, action: null, reason: '', scheduled_at: '' })
function openAction(a, action) {
  Object.assign(pending, { id: a.id, action, reason: '', scheduled_at: localInput(a.scheduled_at) })
  fieldErrors.value = {}
}
async function step(a, action) {
  await run(() => store.act(props.patientId, a.id, action), action === 'check-in' ? `${a.title}：已報到` : `${a.title}：已完成`)
}
async function confirmAction() {
  const payload = pending.action === 'reschedule' ? { scheduled_at: toIso(pending.scheduled_at), reason: pending.reason } : { reason: pending.reason }
  const text = pending.action === 'reschedule' ? '已改期，原行程保留在紀錄中' : '已取消行程'
  if (await run(() => store.act(props.patientId, pending.id, pending.action, payload), text)) pending.id = null
}

watch(() => props.patientId, (id) => id && store.fetch(id), { immediate: true })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="appt-title" data-appointments-panel>
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 id="appt-title" class="text-lg font-bold">治療行程</h2>
      <button v-if="canWrite && !form.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" data-new-appointment @click="openForm()">新增行程</button>
    </div>
    <p v-if="notice" class="mt-3 rounded-xl bg-ok-soft px-4 py-2 font-medium text-ok" role="status" data-appt-notice>{{ notice }}</p>
    <p v-if="failure" class="mt-3 rounded-xl bg-critical-soft px-4 py-2 font-medium text-critical" role="alert" data-appt-error>{{ failure }}</p>
    <p v-if="error" class="mt-3 text-critical" role="alert">{{ error.message }}</p>

    <form v-if="form.open" class="mt-3 grid gap-3 rounded-xl bg-mist p-3 sm:grid-cols-2" novalidate data-appointment-form @submit.prevent="save">
      <p class="font-bold sm:col-span-2">{{ form.id ? '修改行程' : '新增行程' }}</p>
      <label class="block"><span class="text-sm font-medium">類型</span>
        <select v-model="form.appointment_type" name="appointment_type" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
          <option v-for="(label, value) in TYPE" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label class="block"><span class="text-sm font-medium">名稱</span>
        <input v-model="form.title" name="title" maxlength="100" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.title" class="text-sm text-critical">請輸入名稱</span>
      </label>
      <label v-if="!form.id" class="block"><span class="text-sm font-medium">時間</span>
        <input v-model="form.scheduled_at" type="datetime-local" name="scheduled_at" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
        <span v-if="fieldErrors.scheduled_at" class="text-sm text-critical">{{ fieldErrors.scheduled_at }}</span>
      </label>
      <label class="block"><span class="text-sm font-medium">預計時間（分鐘）</span>
        <input v-model="form.duration_min" type="number" min="1" max="1440" name="duration_min" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label class="block"><span class="text-sm font-medium">地點</span>
        <input v-model="form.location" name="location" maxlength="100" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <label class="block"><span class="text-sm font-medium">備註（僅照護團隊可見）</span>
        <input v-model="form.notes" name="notes" maxlength="2000" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
      </label>
      <fieldset class="sm:col-span-2">
        <legend class="text-sm font-medium">準備事項（病人的今日行程會以黃底提醒）</legend>
        <div v-for="(s, n) in form.steps" :key="n" class="mt-2 grid gap-2 sm:grid-cols-[8rem_1fr_12rem_auto]" data-step>
          <select v-model="s.instruction_type" :aria-label="`第 ${n + 1} 項類型`" class="min-h-11 rounded-xl border border-line bg-surface px-2">
            <option v-for="(label, value) in STEP" :key="value" :value="value">{{ label }}</option>
          </select>
          <input v-model="s.text" name="step_text" maxlength="255" placeholder="例如：08:20 報到" :aria-label="`第 ${n + 1} 項內容`" class="min-h-11 rounded-xl border border-line bg-surface px-3" />
          <input v-model="s.due_at" type="datetime-local" :aria-label="`第 ${n + 1} 項時間`" class="min-h-11 rounded-xl border border-line bg-surface px-2" />
          <button type="button" class="min-h-11 rounded-full px-3 text-ink-soft hover:bg-white" @click="form.steps.splice(n, 1)">移除</button>
        </div>
        <span v-if="fieldErrors['instructions.text']" class="text-sm text-critical">每一項準備事項都需要內容</span>
        <button type="button" class="mt-2 min-h-10 rounded-full border border-line px-4 text-sm text-care hover:bg-care-soft" data-add-step @click="addStep">加入準備事項</button>
      </fieldset>
      <div class="flex gap-2 sm:col-span-2">
        <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">{{ form.id ? '儲存' : '建立行程' }}</button>
        <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="form.open = false">取消</button>
      </div>
    </form>

    <p v-if="store.byPatient[patientId] && !rows.length" class="mt-2 text-ink-soft">目前沒有行程。</p>
    <ul v-if="upcoming.length" class="mt-3 divide-y divide-line rounded-xl border border-line" data-upcoming>
      <li v-for="a in upcoming" :key="a.id" class="p-3" :data-appointment="a.id" :data-appointment-status="a.status">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span class="font-bold">{{ when(a.scheduled_at) }}</span>
          <span>{{ a.title }}</span>
          <span class="rounded-full px-2 py-0.5 text-sm" :class="a.status === 'checked_in' ? 'bg-ok-soft text-ok' : 'bg-mist text-ink-soft'">{{ STATUS[a.status] }}</span>
          <span v-if="canWrite" class="ml-auto flex flex-wrap gap-1.5">
            <button v-if="a.status === 'scheduled' && isToday(a.scheduled_at)" type="button" class="min-h-9 rounded-full bg-care px-3 text-sm font-bold text-white hover:bg-care/90" data-check-in @click="step(a, 'check-in')">報到</button>
            <button v-if="isToday(a.scheduled_at)" type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-complete @click="step(a, 'complete')">完成</button>
            <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-edit @click="openForm(a)">修改</button>
            <button v-if="a.status === 'scheduled'" type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-mist" data-reschedule @click="openAction(a, 'reschedule')">改期</button>
            <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm text-critical hover:bg-critical-soft" data-cancel @click="openAction(a, 'cancel')">取消</button>
          </span>
        </div>
        <p class="text-sm text-ink-soft">
          {{ TYPE[a.appointment_type] }}<template v-if="a.location">・{{ a.location }}</template><template v-if="a.cycle_number">・第 {{ a.cycle_number }} 次 Cycle</template>
          <template v-if="a.rescheduled_from_id">・改期而來</template>
        </p>
        <ul v-if="a.instructions.length" class="mt-1 flex flex-wrap gap-2 text-sm">
          <li v-for="i in a.instructions" :key="i.id" class="rounded-full bg-action px-2 py-0.5 text-action-ink">{{ i.text }}</li>
        </ul>
        <p v-if="a.notes" class="mt-1 text-sm">備註：{{ a.notes }}</p>
        <form v-if="pending.id === a.id" class="mt-2 flex flex-wrap items-end gap-2 rounded-xl bg-mist p-3" data-appointment-action @submit.prevent="confirmAction">
          <label v-if="pending.action === 'reschedule'" class="block"><span class="text-sm font-medium">新的時間</span>
            <input v-model="pending.scheduled_at" type="datetime-local" name="new_scheduled_at" class="mt-1 block min-h-11 rounded-xl border border-line bg-surface px-3" />
            <span v-if="fieldErrors.scheduled_at" class="block text-sm text-critical">{{ fieldErrors.scheduled_at }}</span>
          </label>
          <label class="block min-w-48 flex-1"><span class="text-sm font-medium">{{ pending.action === 'reschedule' ? '改期原因' : '取消原因' }}</span>
            <input v-model="pending.reason" name="reason" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
          </label>
          <button type="submit" class="min-h-11 rounded-full px-4 font-bold text-white" :class="pending.action === 'cancel' ? 'bg-critical' : 'bg-care'" :disabled="!pending.reason.trim()">
            {{ pending.action === 'reschedule' ? '確定改期' : '確定取消' }}
          </button>
          <button type="button" class="min-h-11 rounded-full px-3 text-ink-soft hover:bg-white" @click="pending.id = null">返回</button>
        </form>
      </li>
    </ul>

    <details v-if="history.length" class="mt-3" data-appointment-history>
      <summary class="min-h-11 cursor-pointer py-2 text-care">過去與已變更的行程（{{ history.length }}）</summary>
      <ul class="divide-y divide-line">
        <li v-for="a in history" :key="a.id" class="py-2 text-sm" :data-appointment="a.id" :data-appointment-status="a.status">
          <strong>{{ when(a.scheduled_at) }}</strong>　{{ a.title }}　<span class="text-ink-soft">{{ STATUS[a.status] }}</span>
          <template v-if="a.rescheduled_to_id">（已改到新的行程）</template>
        </li>
      </ul>
    </details>
  </section>
</template>
