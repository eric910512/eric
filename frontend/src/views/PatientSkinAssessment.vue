<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import SymptomField from '@/components/SymptomField.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { formatTime, localDate } from '@/utils/format'

/**
 * Patient: 皮膚自我評估 (/patient/skin) — form rt_skin_self_assessment (Phase 1a, text only, no photos).
 * Plain-language answers only: the patient never picks a CTCAE / RTOG grade, and never sees the nurse's
 * professional assessment. Every question is optional (at least one answer, or 「不確定，請護理師協助評估」).
 * Not a real-time service: nothing here promises that someone is watching. The wording of the new
 * questions and of the safety hint is pending clinical review (docs/skin-self-assessment-clinical-review.md).
 */
const FORM_CODE = 'rt_skin_self_assessment'
const ASSIST = 'skin_assist_request'
const MAX_NOTES = 2000
// 義大醫院放射腫瘤科 (same fixed information as the chat page / CHAT_SERVICE on the API)
const SERVICE = { hours_text: '星期一至五 09:00-12:30、13:30-17:30', phone_text: '07-6150011 分機 2205', tel: 'tel:076150011,2205' }
// PENDING CLINICAL REVIEW: which answers show the "let the care team know soon" hint
const SAFETY_ANSWERS = { rt_dermatitis_desquamation: ['bleeding'], skin_broken_exudate: ['yes'] }

const auth = useAuthStore()
const dashboard = useDashboardStore()
const portal = usePatientPortalStore()
const TZ = 'Asia/Taipei'
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const unread = computed(() => dashboard.patients[patientId.value]?.widgets?.notifications?.unread_count ?? 0)

const form = computed(() => dashboard.forms[FORM_CODE])
const formError = computed(() => dashboard.errors[`form:${FORM_CODE}`])
const questions = computed(() => (form.value?.items ?? []).filter((i) => i.definition.code !== ASSIST))
const history = computed(() => portal.formRecords[FORM_CODE])
const historyError = computed(() => portal.errors[`records:${FORM_CODE}`])

const answers = reactive({})
const assist = ref(false)
const notes = ref('')
const state = ref('form') // form | submitting | done
const missing = ref(false)
const submitError = ref(null)
let idempotencyKey = null

const answered = computed(() => questions.value.filter((i) => answers[i.definition.code] != null))
const safetyHint = computed(() => Object.entries(SAFETY_ANSWERS).some(([code, codes]) => codes.includes(answers[code])))

// a changed answer is a new submission (a retry of the same one keeps its Idempotency-Key)
watch([answers, assist, notes], () => {
  idempotencyKey = null
  submitError.value = null
  if (answered.value.length || assist.value) missing.value = false
})

const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
// answers arrive in the form's question order (API and mock)
function describe(r) {
  return r.values.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}` : `${v.label} ${v.score ?? v.value_numeric}`)).join('、')
}

function reset() {
  for (const k of Object.keys(answers)) delete answers[k]
  assist.value = false
  notes.value = ''
  missing.value = false
  submitError.value = null
  state.value = 'form'
}

async function submit() {
  if (state.value === 'submitting') return
  if (!answered.value.length && !assist.value) {
    missing.value = true
    return
  }
  idempotencyKey ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  state.value = 'submitting'
  const values = answered.value.map((i) => {
    const { code, value_type: type } = i.definition
    return type === 'scale' ? { definition_code: code, value_numeric: answers[code] } : { definition_code: code, option_code: answers[code] }
  })
  if (assist.value) values.push({ definition_code: ASSIST, option_code: 'yes' })
  const text = notes.value.trim()
  try {
    await dashboard.submitSymptomReport(patientId.value, { form_code: FORM_CODE, values, ...(text ? { notes: text } : {}) }, idempotencyKey)
    idempotencyKey = null
    state.value = 'done'
    portal.fetchFormRecords(patientId.value, FORM_CODE)
  } catch (err) {
    if (!err.retryable) idempotencyKey = null
    submitError.value = err
    state.value = 'form'
  }
}

onMounted(() => {
  dashboard.fetchSymptomForm(FORM_CODE)
  portal.fetchFormRecords(patientId.value, FORM_CODE)
  if (!dashboard.patients[patientId.value]) dashboard.fetchPatientDashboard(patientId.value)
})
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient-symptoms' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 症狀回報
        </RouterLink>
        <h1 class="text-lg font-bold">皮膚自我評估</h1>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4" data-skin-page>
      <!-- fixed notices: a self-record, not a diagnosis; not real-time monitoring; how to reach the team -->
      <section class="space-y-2 rounded-2xl border border-line bg-surface p-4 text-sm leading-relaxed" aria-label="使用前請先閱讀" data-skin-notice>
        <p class="flex gap-2"><AppIcon name="clipboard" :size="18" class="mt-0.5 shrink-0 text-care" />
          <span>這是您對皮膚狀況的<strong>自我紀錄</strong>，不是醫療診斷，也不會判定正式的皮膚反應分級。</span></p>
        <p class="flex gap-2" data-skin-not-realtime><AppIcon name="clock" :size="18" class="mt-0.5 shrink-0 text-care" />
          <span>本功能<strong>不是即時醫療監測服務</strong>：送出後不會立即有人查看，護理師會在服務時間（{{ SERVICE.hours_text }}）內查看。</span></p>
        <p class="flex gap-2"><AppIcon name="phone" :size="18" class="mt-0.5 shrink-0 text-care" />
          <span>想詢問皮膚狀況，請於服務時間撥打 <a :href="SERVICE.tel" class="font-bold text-care underline underline-offset-2">{{ SERVICE.phone_text }}</a>。</span></p>
        <p class="flex gap-2 rounded-xl bg-critical-soft px-3 py-2 text-critical" role="note" data-skin-emergency><AppIcon name="alert" :size="18" class="mt-0.5 shrink-0" />
          <span>如有呼吸困難、意識不清等緊急狀況，請立即撥打 119 或就近急診就醫。</span></p>
      </section>

      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="skin-form-title" data-skin-form-card :data-state="state">
        <h2 id="skin-form-title" class="text-lg font-bold">今天的皮膚狀況</h2>
        <p v-if="formError" class="mt-2 text-critical" role="alert">{{ formError.message }}</p>
        <div v-else-if="!form" class="mt-2 h-24 animate-pulse rounded-xl bg-mist" aria-busy="true" />

        <div v-else-if="state === 'done'" class="mt-2 space-y-3" data-skin-done>
          <p class="inline-flex items-center gap-2 text-lg font-bold text-ok"><AppIcon name="check" /> 已送出</p>
          <p>護理師會在服務時間內查看這筆紀錄。<strong>送出不代表已經有人看過</strong>；如果狀況變嚴重，請直接打電話聯絡或就醫。</p>
          <button type="button" class="min-h-12 rounded-full border border-line px-5 font-bold hover:bg-mist" data-skin-again @click="reset">再記錄一次</button>
        </div>

        <form v-else class="mt-3 space-y-6" novalidate data-skin-form @submit.prevent="submit">
          <p class="text-ink-soft">請依照您看到、感覺到的情形回答，不確定的題目可以不填。</p>
          <div v-for="item in questions" :key="item.definition.code" :data-question="item.definition.code">
            <SymptomField v-model="answers[item.definition.code]" :definition="item.definition" />
            <button v-if="answers[item.definition.code] != null" type="button" class="mt-1 min-h-10 rounded-full px-3 text-sm text-ink-soft hover:bg-mist"
              @click="delete answers[item.definition.code]">清除這題</button>
          </div>

          <p v-if="safetyHint" class="flex gap-2 rounded-xl bg-warn-soft px-4 py-3 text-warn" role="status" data-skin-safety>
            <AppIcon name="alert" :size="20" class="mt-0.5 shrink-0" />
            <span>您選擇的狀況，建議盡快讓醫療團隊知道：請於服務時間撥打 <a :href="SERVICE.tel" class="font-bold underline underline-offset-2">{{ SERVICE.phone_text }}</a>；如有緊急狀況，請立即撥打 119 或就近急診就醫。</span>
          </p>

          <label class="flex items-start gap-3 rounded-xl border px-4 py-3" :class="assist ? 'border-care bg-care-soft' : 'border-line'" data-skin-assist>
            <input v-model="assist" type="checkbox" class="mt-1 size-5 shrink-0 accent-care" />
            <span>
              <span class="font-bold">不確定，請護理師協助評估</span>
              <span class="block text-sm text-ink-soft">不知道怎麼選也沒關係，可以只勾選這一項，或在下面寫下您的狀況。</span>
            </span>
          </label>

          <label class="block">
            <span class="text-lg font-bold">補充說明（選填）</span>
            <span class="block text-sm text-ink-soft">例如：哪個部位、什麼時候開始、有沒有擦藥。</span>
            <textarea v-model="notes" rows="3" :maxlength="MAX_NOTES" class="mt-2 w-full rounded-xl border border-line bg-surface px-3 py-2 text-base" data-skin-notes />
            <span class="block text-right text-sm text-ink-soft">{{ notes.length }} / {{ MAX_NOTES }}</span>
          </label>

          <p v-if="missing" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-skin-missing>
            請至少回答一題，或勾選「不確定，請護理師協助評估」。
          </p>
          <p v-if="submitError" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-skin-error>{{ submitError.message }}</p>
          <button type="submit" class="min-h-12 w-full rounded-full bg-care px-6 text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
            :disabled="state === 'submitting'" data-skin-submit>{{ state === 'submitting' ? '送出中…' : '送出' }}</button>
        </form>
      </section>

      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="skin-history" data-skin-history :data-loaded="!!history">
        <h2 id="skin-history" class="text-lg font-bold">我的皮膚紀錄</h2>
        <p v-if="historyError" class="mt-2 text-critical" role="alert">{{ historyError.message }}</p>
        <p v-else-if="history && !history.length" class="mt-2 text-ink-soft">還沒有皮膚自我評估紀錄。</p>
        <ul class="mt-2 divide-y divide-line">
          <li v-for="r in history ?? []" :key="r.id" class="py-3" :data-skin-record="r.id">
            <p class="font-bold">{{ when(r.recorded_at) }}</p>
            <p>{{ describe(r) }}</p>
            <p v-if="r.notes" class="mt-1 whitespace-pre-line rounded-lg bg-mist px-3 py-2 text-sm">{{ r.notes }}</p>
            <p class="mt-1 text-sm text-ink-soft">{{ r.review_status === 'reviewed' ? '護理師已看過這筆紀錄' : '護理師會在服務時間內查看' }}</p>
          </li>
        </ul>
      </section>
    </main>

    <PatientBottomNav active="symptom-report" :unread="unread" />
  </div>
</template>
