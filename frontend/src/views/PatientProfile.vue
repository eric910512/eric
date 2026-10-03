<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { formatTime, localDate } from '@/utils/format'

/**
 * Patient: 我的 (/patient/me) — my data, care alerts, hospital contacts, password, sign out.
 * 基本資料 (profile sprint): the patient maintains their notification email (verified before any email
 * is sent), email notifications, height and weight (each new weight is kept: 體重紀錄); BMI is
 * computed from height and the latest weight. Other data is kept by the nursing team.
 */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const portal = usePatientPortalStore()
const router = useRouter()
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const p = computed(() => portal.profile)
const b = computed(() => portal.basic)
const unread = computed(() => dashboard.patients[patientId.value]?.widgets.notifications?.unread_count ?? 0)
const contacts = computed(() => dashboard.settings?.contacts ?? [])
const GENDER = { male: '男', female: '女', other: '其他' }
const ALERT = { allergy: '過敏', limb_restriction: '肢體限制', fall_risk: '跌倒風險', isolation: '隔離', other: '其他' }
const when = (iso) => `${localDate(iso)} ${formatTime(iso)}`

// ------------------------------------------------------------------ 基本資料 form
const form = reactive({ email: '', height: '', weight: '', notify: false })
const fieldErrors = ref({})
const failure = ref('')
const notice = ref('')
const saving = ref(false)
const verifyMessage = ref('')
const verifyError = ref('')
const verifying = ref(false)
let weightKey = crypto.randomUUID() // Idempotency-Key of the new weight (POST /vital-signs); new one after success / 4xx

function fill() {
  if (!b.value) return
  form.email = b.value.email ?? ''
  form.height = b.value.height_cm ?? ''
  form.weight = ''
  form.notify = b.value.email_notification_enabled
}
const emailEdited = computed(() => (form.email.trim().toLowerCase() || null) !== (b.value?.email ?? null))
const canNotify = computed(() => !!b.value?.email_verified && !emailEdited.value)
const emailState = computed(() => {
  if (!b.value?.email) return { text: '未設定', cls: 'bg-mist text-ink-soft' }
  return b.value.email_verified ? { text: '已驗證', cls: 'bg-ok-soft text-ok' } : { text: '尚未驗證', cls: 'bg-warn-soft text-warn' }
})
const ISSUE = {
  'must be a valid email address': '請輸入正確的 Email',
  'must be a number between 30 and 250': '身高請輸入 30–250 公分',
  'must be between 20 and 300': '體重請輸入 20–300 公斤',
  'must be a number': '請輸入數字',
}
const issue = (d) => ISSUE[d.issue] ?? d.issue

async function load() {
  await Promise.all([portal.fetchBasic(patientId.value), portal.fetchWeights(patientId.value)])
  fill()
}

async function save() {
  fieldErrors.value = {}
  failure.value = ''
  notice.value = ''
  verifyMessage.value = ''
  const errs = {}
  const height = form.height === '' || form.height === null ? null : Number(form.height)
  const weight = form.weight === '' || form.weight === null ? null : Number(form.weight)
  if (height !== null && !Number.isFinite(height)) errs.height_cm = '請輸入數字'
  if (weight !== null && !Number.isFinite(weight)) errs.weight_kg = '請輸入數字'
  if (Object.keys(errs).length) {
    fieldErrors.value = errs
    return
  }
  const changes = {}
  if (emailEdited.value) changes.email = form.email.trim() || null
  if (height !== (b.value?.height_cm ?? null)) changes.height_cm = height
  if (!emailEdited.value && form.notify !== b.value?.email_notification_enabled) changes.email_notification_enabled = form.notify
  if (!Object.keys(changes).length && weight === null) {
    notice.value = '沒有需要儲存的變更'
    return
  }
  saving.value = true
  const done = []
  try {
    if (Object.keys(changes).length) {
      await portal.saveBasic(patientId.value, changes)
      done.push('基本資料')
    }
    if (weight !== null) {
      try {
        await dashboard.submitVitalSigns(patientId.value, { weight_kg: weight }, weightKey)
        weightKey = crypto.randomUUID()
        done.push('體重')
      } catch (e) {
        if (!e.retryable) weightKey = crypto.randomUUID()
        fieldErrors.value = { weight_kg: (e.details ?? []).filter((d) => d.field === 'weight_kg').map(issue)[0] ?? e.message }
        throw null // eslint-disable-line no-throw-literal -- message already shown on the field
      }
    }
    await load()
    notice.value = `已儲存${done.join('與')}`
    if (changes.email) notice.value += '。新的 Email 需要驗證後才會收到通知'
  } catch (e) {
    if (e) {
      fieldErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, issue(d)]))
      failure.value = e.code === 'EMAIL_NOT_VERIFIED' ? '請先完成 Email 驗證，才能開啟 Email 通知' : e.message
    } else if (done.length) {
      await load()
      form.weight = String(weight)
      notice.value = `已儲存${done.join('與')}，體重尚未儲存`
    }
  } finally {
    saving.value = false
  }
}

async function verify() {
  verifyMessage.value = ''
  verifyError.value = ''
  verifying.value = true
  try {
    const delivery = await portal.requestVerification(patientId.value)
    verifyMessage.value = delivery.status === 'sent'
      ? `驗證信已寄到 ${b.value.email}，請在 24 小時內開啟信中的連結`
      : '驗證信寄送失敗，請稍後再試'
  } catch (e) {
    verifyError.value = e.message
  } finally {
    verifying.value = false
  }
}

function logout() {
  auth.logout()
  router.replace({ name: 'login', query: { reason: 'logout' } })
}

onMounted(() => {
  dashboard.fetchSettings()
  portal.fetchProfile(patientId.value)
  load()
  if (!dashboard.patients[patientId.value]) dashboard.fetchPatientDashboard(patientId.value)
})
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">我的</h1>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4" data-patient-profile>
      <p v-if="portal.errors.profile" class="rounded-2xl bg-critical-soft p-4 text-critical" role="alert">{{ portal.errors.profile.message }}</p>
      <div v-else-if="!p" class="h-40 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <template v-else>
        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="me-title">
          <h2 id="me-title" class="text-xl font-bold">{{ p.display_name }}</h2>
          <p class="text-ink-soft">病人代碼 {{ p.patient_code }}</p>
          <dl class="mt-3 grid grid-cols-[6rem_1fr] gap-y-2">
            <dt class="text-ink-soft">性別</dt><dd>{{ GENDER[p.gender] ?? '—' }}</dd>
            <dt class="text-ink-soft">出生日期</dt><dd>{{ p.date_of_birth }}（{{ p.age }} 歲）</dd>
            <dt class="text-ink-soft">血型</dt><dd>{{ p.blood_type ?? '—' }}</dd>
            <dt class="text-ink-soft">過敏史</dt><dd>{{ p.allergies ?? '—' }}</dd>
          </dl>
          <p class="mt-3 text-sm text-ink-soft">以上資料有誤時，請告訴護理師協助修改。</p>
        </section>

        <section v-if="p.care_alerts.length" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="alerts-title">
          <h2 id="alerts-title" class="text-lg font-bold">照護注意事項</h2>
          <ul class="mt-2 space-y-2">
            <li v-for="a in p.care_alerts" :key="a.id" class="rounded-xl bg-warn-soft px-3 py-2"><strong class="text-warn">{{ ALERT[a.alert_type] ?? '注意' }}</strong>　{{ a.description }}</li>
          </ul>
        </section>
      </template>

      <!-- 基本資料: maintained by the patient -->
      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="basic-title" data-basic-profile>
        <h2 id="basic-title" class="text-lg font-bold">基本資料</h2>
        <p v-if="portal.errors.basic" class="mt-2 text-critical" role="alert">{{ portal.errors.basic.message }}</p>
        <div v-else-if="!b" class="mt-2 h-40 animate-pulse rounded-xl bg-mist" aria-busy="true" />
        <form v-else class="mt-3 space-y-4" novalidate @submit.prevent="save">
          <div>
            <label class="block"><span class="font-medium">Email</span>
              <span class="ml-2 rounded-full px-2 py-0.5 text-sm" :class="emailState.cls" data-email-state>{{ emailState.text }}</span>
              <input v-model="form.email" type="email" name="email" inputmode="email" autocomplete="email" maxlength="255" placeholder="用來接收通知"
                class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.email" />
            </label>
            <span v-if="fieldErrors.email" class="mt-1 block text-sm text-critical">{{ fieldErrors.email }}</span>
            <p class="mt-1 text-sm text-ink-soft">這是接收通知用的 Email，不會改變登入帳號。</p>
            <template v-if="b.email && !b.email_verified && !emailEdited">
              <button v-if="b.email_delivery_available" type="button" class="mt-2 min-h-11 rounded-full border border-care px-4 font-bold text-care hover:bg-care-soft disabled:opacity-60"
                :disabled="verifying" data-verify-email @click="verify">{{ verifying ? '寄送中…' : b.email_verification_sent_at ? '重新寄送驗證信' : '驗證 Email' }}</button>
              <p v-else class="mt-2 text-sm text-warn" data-email-unavailable>系統目前尚未開放 Email 寄送，暫時無法驗證 Email。</p>
            </template>
            <p v-if="verifyMessage" class="mt-2 rounded-xl bg-care-soft px-3 py-2 text-sm text-care" role="status" data-verify-notice>{{ verifyMessage }}</p>
            <p v-if="verifyError" class="mt-2 rounded-xl bg-critical-soft px-3 py-2 text-sm text-critical" role="alert" data-verify-error>{{ verifyError }}</p>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <label class="block"><span class="font-medium">身高（公分）</span>
              <input v-model="form.height" type="number" name="height_cm" inputmode="decimal" step="0.1" min="30" max="250"
                class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.height_cm" />
              <span v-if="fieldErrors.height_cm" class="mt-1 block text-sm text-critical">{{ fieldErrors.height_cm }}</span>
            </label>
            <label class="block"><span class="font-medium">新增體重（公斤）</span>
              <input v-model="form.weight" type="number" name="weight_kg" inputmode="decimal" step="0.1" min="20" max="300"
                class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.weight_kg" />
              <span v-if="fieldErrors.weight_kg" class="mt-1 block text-sm text-critical">{{ fieldErrors.weight_kg }}</span>
            </label>
          </div>

          <dl class="grid grid-cols-[6rem_1fr] gap-y-2 rounded-xl bg-mist p-4">
            <dt class="text-ink-soft">身高</dt><dd data-height>{{ b.height_cm != null ? `${b.height_cm} cm` : '—' }}</dd>
            <dt class="text-ink-soft">目前體重</dt>
            <dd data-latest-weight>
              <template v-if="b.latest_weight">{{ b.latest_weight.weight_kg }} kg <span class="text-sm text-ink-soft">（{{ when(b.latest_weight.measured_at) }}）</span></template>
              <template v-else>—</template>
            </dd>
            <dt class="text-ink-soft">BMI</dt><dd class="font-bold" data-bmi>{{ b.bmi ?? '—' }}</dd>
          </dl>

          <label class="flex min-h-12 items-start gap-3" :class="canNotify ? '' : 'opacity-60'">
            <input v-model="form.notify" type="checkbox" name="email_notification_enabled" class="mt-1 size-6 shrink-0" :disabled="!canNotify" data-email-notify />
            <span><span class="font-medium">接收 Email 通知</span>
              <span class="block text-sm text-ink-soft">{{ canNotify ? '護理團隊發送通知時，也寄一封 Email 提醒您登入查看（Email 不含通知內容）。' : 'Email 驗證完成後才能開啟。' }}</span>
            </span>
          </label>

          <p v-if="notice" class="rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-basic-notice>{{ notice }}</p>
          <p v-if="failure" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-basic-error>{{ failure }}</p>
          <button type="submit" class="min-h-12 w-full rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="saving" data-save-basic>
            {{ saving ? '儲存中…' : '儲存' }}
          </button>
        </form>
      </section>

      <section v-if="portal.weights?.length" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="weights-title" data-weight-history>
        <h2 id="weights-title" class="text-lg font-bold">體重紀錄</h2>
        <ul class="mt-2 divide-y divide-line">
          <li v-for="w in portal.weights" :key="w.id" class="flex items-center justify-between gap-2 py-2" data-weight>
            <span><strong>{{ w.weight_kg }} kg</strong> <span class="text-sm text-ink-soft">{{ w.entered_by_patient ? '本人輸入' : '醫療團隊量測' }}</span></span>
            <span class="text-sm text-ink-soft">{{ when(w.measured_at) }}</span>
          </li>
        </ul>
      </section>

      <section v-if="contacts.length" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="contacts-title">
        <h2 id="contacts-title" class="text-lg font-bold">聯絡醫院</h2>
        <ul class="mt-2 space-y-2">
          <li v-for="c in contacts" :key="c.key">
            <a :href="`tel:${c.phone}`" class="flex min-h-12 items-center gap-2 rounded-full border border-line px-4 text-care hover:bg-care-soft">
              <AppIcon name="phone" :size="20" /> {{ c.label }} {{ c.phone }}
            </a>
          </li>
        </ul>
      </section>

      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="account-title">
        <h2 id="account-title" class="text-lg font-bold">帳號</h2>
        <p class="mt-1 break-all text-ink-soft">{{ auth.user?.email }}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <RouterLink :to="{ name: 'change-password' }" class="inline-flex min-h-12 items-center rounded-full border border-line px-5 font-bold text-care hover:bg-care-soft" data-change-password-link>修改密碼</RouterLink>
          <RouterLink :to="{ name: 'account-sessions' }" class="inline-flex min-h-12 items-center rounded-full border border-line px-5 font-bold text-care hover:bg-care-soft" data-profile-sessions>登入裝置</RouterLink>
          <button type="button" class="min-h-12 rounded-full bg-ink px-5 font-bold text-white hover:bg-ink/90" data-logout @click="logout">登出</button>
        </div>
      </section>
    </main>

    <PatientBottomNav active="profile" :unread="unread" />
  </div>
</template>
