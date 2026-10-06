<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { useRemindersStore } from '@/stores/reminders'
import { allowsFull, categoryLabel, DEFAULT_CATEGORY, DEFAULT_EMAIL_MODE, EMAIL_CATEGORIES, EMAIL_MODES, SENSITIVE_NOTICE } from '@/utils/emailPolicy'
import { formatTime, localDate } from '@/utils/format'

/**
 * 提醒 (staff): write a reminder to the patient, sent now or at a set time. The patient sees it in
 * 通知 once it is due; until then only staff see it under 排程中. Sent reminders follow the same
 * handling steps as risk alerts (接手 → 開始處理 → 完成); the completion note stays internal.
 * Email channel: when the patient has a verified contact email with email notifications on, a
 * summary email (no content) goes out too; its outcome is shown per reminder and never changes the
 * reminder's own status.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  canWrite: { type: Boolean, default: false },
  hasAccount: { type: Boolean, default: true },
  timezone: { type: String, default: 'Asia/Taipei' },
  emailContact: { type: Object, default: null }, // patient.notification_contact (masked email, verified, enabled)
})

const store = useRemindersStore()
const STATUS = { new: '待處理', acknowledged: '已接手', in_progress: '處理中', resolved: '已完成' }
const STATUS_CLASS = { new: 'bg-warn-soft text-warn', acknowledged: 'bg-care-soft text-care', in_progress: 'bg-care-soft text-care', resolved: 'bg-ok-soft text-ok' }
const ORIGIN = { manual: '立即送出', scheduled: '排程送出', alert_rule: '風險規則' }
const NEXT = { new: ['acknowledge', '接手'], acknowledged: ['start', '開始處理'], in_progress: ['resolve', '完成'] }
const EMAIL_STATUS = { sent: 'Email 已寄出', failed: 'Email 寄送失敗', pending: 'Email 寄送中', skipped: 'Email 未寄' }
const EMAIL_CLASS = { sent: 'bg-ok-soft text-ok', failed: 'bg-critical-soft text-critical', pending: 'bg-care-soft text-care', skipped: 'bg-mist text-ink-soft' }
const SKIP = {
  no_email: '病人未設定 Email', not_verified: '病人 Email 尚未驗證', disabled: '病人未開啟 Email 通知',
  scheduled: '排程提醒不寄 Email', not_configured: '系統尚未設定 Email 服務', not_requested: '選擇不寄 Email',
}
const ERROR = { TIMEOUT: '逾時', PROVIDER_REJECTED: '寄送服務拒收', PROVIDER_ERROR: '寄送服務錯誤' }
const MODE_TEXT = { summary: '摘要', full: '標題與內容' }
function emailText(d) {
  if (!d) return ''
  if (d.status === 'skipped') return `${EMAIL_STATUS.skipped}（${SKIP[d.skip_reason] ?? d.skip_reason}）`
  const mode = MODE_TEXT[d.mode] ? `（${MODE_TEXT[d.mode]}${d.downgraded ? '，敏感主題已改為摘要' : ''}）` : ''
  if (d.status === 'failed') return `${EMAIL_STATUS.failed}${mode}（${ERROR[d.error_code] ?? d.error_code}，App 通知已送出）`
  return `${EMAIL_STATUS[d.status] ?? d.status}${mode}`
}
const emailChannel = computed(() => {
  const c = props.emailContact
  if (!c?.email_masked) return '病人未設定通知 Email：只會送 App 通知。'
  if (!c.email_verified) return `病人的 Email（${c.email_masked}）尚未驗證：只會送 App 通知。`
  if (!c.email_notification_enabled) return `病人未開啟 Email 通知（${c.email_masked}）：只會送 App 通知。`
  return `病人已開啟 Email 通知（${c.email_masked}）：立即送出的通知會依下方「Email 通知」設定寄出 Email。`
})

const sent = computed(() => store.sent[props.patientId] ?? null)
const scheduled = computed(() => store.scheduled[props.patientId] ?? [])
const loadError = computed(() => store.errors[props.patientId])
const when = (iso) => `${localDate(iso, props.timezone)} ${formatTime(iso, props.timezone)}`
const toIso = (local) => new Date(`${local}:00+08:00`).toISOString() // the input is in the patient's timezone (Asia/Taipei)

const blank = () => ({ title: '', message: '', severity: 'info', timing: 'now', at: '', category: DEFAULT_CATEGORY, emailMode: DEFAULT_EMAIL_MODE })
const form = reactive({ open: false, ...blank() })
// email content policy (the backend enforces it again): sensitive topics never offer 標題與內容
const fullAllowed = computed(() => allowsFull(form.category))
watch(() => form.category, () => {
  if (!fullAllowed.value && form.emailMode === 'full') form.emailMode = 'summary'
})
const fieldErrors = ref({})
const failure = ref('')
const notice = ref('')
const submitting = ref(false)
let key = crypto.randomUUID() // one Idempotency-Key per reminder; a new one after a 4xx
const ISSUE = {
  'must be at least 1 minute and at most 1 year from now (omit it to send now)': '請選擇 1 分鐘後到一年內的時間',
  'must be an ISO 8601 datetime with timezone': '請選擇送出時間',
}

async function submit() {
  const errs = {}
  if (!form.title.trim()) errs.title = '請輸入標題'
  if (!form.message.trim()) errs.message = '請輸入提醒內容'
  if (form.timing === 'later' && !form.at) errs.scheduled_for = '請選擇送出時間'
  fieldErrors.value = errs
  failure.value = ''
  notice.value = ''
  if (Object.keys(errs).length) return
  submitting.value = true
  try {
    const payload = { title: form.title.trim(), message: form.message.trim(), severity: form.severity,
      category: form.category, email_mode: form.emailMode,
      ...(form.timing === 'later' ? { scheduled_for: toIso(form.at) } : {}) }
    const r = await store.create(props.patientId, payload, key)
    const email = r.email_delivery ? `；${emailText(r.email_delivery)}` : ''
    notice.value = (r.scheduled_for ? `已排定，將於 ${when(r.scheduled_for)} 送出` : '已送出，病人可在「通知」看到') + email
    Object.assign(form, { open: false, ...blank() })
    key = crypto.randomUUID()
  } catch (e) {
    if (e.status >= 400 && e.status < 500) key = crypto.randomUUID()
    fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, ISSUE[d.issue] ?? d.issue]))
    failure.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
  } finally {
    submitting.value = false
  }
}

// lifecycle
const noteFor = ref(null) // reminder id waiting for the completion note
const note = ref('')
const rowError = ref({})
async function step(r, action) {
  if (action === 'resolve' && noteFor.value !== r.id) {
    noteFor.value = r.id
    note.value = ''
    return
  }
  if (action === 'resolve' && !note.value.trim()) {
    rowError.value = { ...rowError.value, [r.id]: '請填寫處理說明' }
    return
  }
  rowError.value = { ...rowError.value, [r.id]: '' }
  try {
    await store.transition(props.patientId, r.id, action, action === 'resolve' ? note.value.trim() : null)
    if (action === 'resolve') noteFor.value = null
  } catch (e) {
    rowError.value = { ...rowError.value, [r.id]: e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message }
  }
}

onMounted(() => store.fetch(props.patientId))
watch(() => props.patientId, (id) => store.fetch(id))
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="reminders-title" data-reminders>
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 id="reminders-title" class="text-lg font-bold">提醒與通知</h2>
      <button v-if="canWrite && hasAccount && !form.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" data-new-reminder @click="form.open = true; notice = ''">
        發送通知
      </button>
    </div>
    <p class="text-sm text-ink-soft">病人會在「通知」看到提醒；排定時間的提醒到時間才會出現。</p>
    <p v-if="canWrite && hasAccount" class="mt-1 text-sm text-ink-soft" data-email-channel>{{ emailChannel }}</p>
    <p v-if="canWrite && !hasAccount" class="mt-2 text-sm text-warn">這位病人尚未開通登入帳號，還不能收到提醒。</p>
    <p v-if="notice" class="mt-3 rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-reminder-notice>{{ notice }}</p>
    <p v-if="failure" class="mt-3 rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-reminder-error>{{ failure }}</p>

    <form v-if="form.open" class="mt-4 space-y-4 rounded-xl bg-mist p-4" novalidate data-reminder-form @submit.prevent="submit">
      <label class="block"><span class="font-medium">標題 <span class="text-critical">*</span></span>
        <input v-model="form.title" name="title" maxlength="200" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4" :aria-invalid="!!fieldErrors.title" />
        <span v-if="fieldErrors.title" class="mt-1 block text-sm text-critical">{{ fieldErrors.title }}</span>
      </label>
      <label class="block"><span class="font-medium">提醒內容 <span class="text-critical">*</span></span>
        <textarea v-model="form.message" name="message" rows="3" maxlength="2000" class="mt-1 block w-full rounded-xl border border-line bg-surface px-4 py-3" :aria-invalid="!!fieldErrors.message" />
        <span v-if="fieldErrors.message" class="mt-1 block text-sm text-critical">{{ fieldErrors.message }}</span>
      </label>
      <fieldset>
        <legend class="font-medium">重要性</legend>
        <div class="mt-1 flex flex-wrap gap-4">
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.severity" type="radio" name="severity" value="info" class="size-5" /> 一般</label>
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.severity" type="radio" name="severity" value="warning" class="size-5" /> 請特別注意</label>
        </div>
      </fieldset>
      <fieldset>
        <legend class="font-medium">送出時間</legend>
        <div class="mt-1 flex flex-wrap items-center gap-4">
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.timing" type="radio" name="timing" value="now" class="size-5" /> 立即送出</label>
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.timing" type="radio" name="timing" value="later" class="size-5" /> 指定時間</label>
          <input v-if="form.timing === 'later'" v-model="form.at" type="datetime-local" name="scheduled_for" class="min-h-12 rounded-xl border border-line bg-surface px-3" :aria-invalid="!!fieldErrors.scheduled_for" />
        </div>
        <span v-if="fieldErrors.scheduled_for" class="mt-1 block text-sm text-critical">{{ fieldErrors.scheduled_for }}</span>
      </fieldset>
      <label class="block"><span class="font-medium">通知主題</span>
        <select v-model="form.category" name="category" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-3" :aria-invalid="!!fieldErrors.category">
          <option v-for="c in EMAIL_CATEGORIES" :key="c.value" :value="c.value">{{ c.label }}</option>
        </select>
        <span v-if="fieldErrors.category" class="mt-1 block text-sm text-critical">{{ fieldErrors.category }}</span>
      </label>
      <fieldset data-email-mode>
        <legend class="font-medium">Email 通知</legend>
        <div class="mt-1 flex flex-wrap gap-4">
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.emailMode" type="radio" name="email_mode" value="none" class="size-5" /> {{ EMAIL_MODES.none }}</label>
          <label class="flex min-h-11 items-center gap-2"><input v-model="form.emailMode" type="radio" name="email_mode" value="summary" class="size-5" /> {{ EMAIL_MODES.summary }}</label>
          <label v-if="fullAllowed" class="flex min-h-11 items-center gap-2"><input v-model="form.emailMode" type="radio" name="email_mode" value="full" class="size-5" /> {{ EMAIL_MODES.full }}</label>
        </div>
        <p v-if="!fullAllowed" class="mt-1 rounded-xl bg-warn-soft px-3 py-2 text-sm text-warn" data-sensitive-notice>{{ SENSITIVE_NOTICE }}</p>
        <p v-else-if="form.emailMode === 'full'" class="mt-1 text-sm text-ink-soft">Email 會包含標題與內容，請勿填寫診斷、檢驗數值等敏感醫療資訊。</p>
        <span v-if="fieldErrors.email_mode" class="mt-1 block text-sm text-critical">{{ fieldErrors.email_mode }}</span>
      </fieldset>
      <div class="flex flex-wrap gap-2">
        <button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="submitting">
          {{ submitting ? '送出中…' : form.timing === 'later' ? '排定提醒' : '送出提醒' }}
        </button>
        <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-surface" @click="form.open = false">取消</button>
      </div>
    </form>

    <p v-if="loadError" class="mt-3 text-critical" role="alert">{{ loadError.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : loadError.message }}</p>
    <template v-else>
      <div v-if="scheduled.length" class="mt-4">
        <h3 class="font-bold">排程中 <span class="font-normal text-ink-soft">病人還看不到</span></h3>
        <ul class="mt-2 divide-y divide-line rounded-xl border border-dashed border-line" data-scheduled-list>
          <li v-for="r in scheduled" :key="r.id" class="px-4 py-3" :data-scheduled="r.id">
            <p class="font-medium">{{ r.title }}</p>
            <p class="text-sm text-ink-soft">將於 {{ when(r.scheduled_for) }} 送出<template v-if="r.email_delivery">（{{ emailText(r.email_delivery) }}）</template></p>
          </li>
        </ul>
      </div>
      <div class="mt-4">
        <h3 class="font-bold">已送出</h3>
        <p v-if="sent && !sent.length" class="mt-1 text-ink-soft">還沒有送出的提醒。</p>
        <ul v-else-if="sent" class="mt-2 divide-y divide-line" data-sent-list>
          <li v-for="r in sent" :key="r.id" class="py-3" :data-reminder="r.id" :data-status="r.status">
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div class="min-w-0">
                <p>
                  <span class="font-medium">{{ r.title }}</span>
                  <span class="ml-2 rounded-full px-2 py-0.5 text-sm" :class="STATUS_CLASS[r.status]">{{ STATUS[r.status] }}</span>
                  <span v-if="r.severity === 'warning'" class="ml-1 rounded-full bg-warn-soft px-2 py-0.5 text-sm text-warn">請特別注意</span>
                </p>
                <p class="text-sm break-words text-ink-soft">{{ r.message }}</p>
                <p class="text-sm text-ink-soft">{{ ORIGIN[r.origin] ?? r.origin }}，{{ when(r.scheduled_for ?? r.created_at) }}</p>
                <p v-if="r.email_delivery" class="mt-1 text-sm" data-email-delivery :data-email-status="r.email_delivery.status"
                  :data-email-mode="r.email_delivery.mode" :data-email-downgraded="r.email_delivery.downgraded ? 'true' : 'false'">
                  <span class="rounded-full px-2 py-0.5" :class="EMAIL_CLASS[r.email_delivery.status]">{{ emailText(r.email_delivery) }}</span>
                  <span v-if="r.email_delivery.category" class="ml-2 text-ink-soft">主題：{{ categoryLabel(r.email_delivery.category) }}</span>
                </p>
                <p v-if="r.handling?.resolution_note" class="text-sm text-ink-soft">處理說明（內部）：{{ r.handling.resolution_note }}</p>
              </div>
              <button v-if="canWrite && NEXT[r.status]" type="button" class="min-h-11 shrink-0 rounded-full border border-line px-4 hover:bg-mist" :data-step="NEXT[r.status][0]" @click="step(r, NEXT[r.status][0])">
                {{ NEXT[r.status][1] }}
              </button>
            </div>
            <form v-if="noteFor === r.id" class="mt-2 flex flex-wrap items-end gap-2" data-resolve-form @submit.prevent="step(r, 'resolve')">
              <label class="block min-w-0 flex-1"><span class="text-sm">處理說明（內部，病人看不到）</span>
                <input v-model="note" name="resolution_note" maxlength="1000" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
              </label>
              <button type="submit" class="min-h-11 rounded-full bg-care px-4 font-bold text-white">完成</button>
            </form>
            <p v-if="rowError[r.id]" class="mt-1 text-sm text-critical" role="alert">{{ rowError[r.id] }}</p>
          </li>
        </ul>
        <div v-else class="mt-2 h-12 animate-pulse rounded bg-mist" aria-busy="true" />
      </div>
    </template>
  </section>
</template>
