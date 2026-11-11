<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import SkinPhoto from '@/components/SkinPhoto.vue'
import SkinPhotoHistory from '@/components/SkinPhotoHistory.vue'
import SkinPhotoPicker from '@/components/SkinPhotoPicker.vue'
import SkinPhotoViewer from '@/components/SkinPhotoViewer.vue'
import SymptomField from '@/components/SymptomField.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { useFollowUpStore } from '@/stores/followUps'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { useSkinPhotoStore } from '@/stores/skinPhotos'
import { formatTime, localDate } from '@/utils/format'

/**
 * Patient: 皮膚自我評估 (/patient/skin) — form rt_skin_self_assessment. Phase 1b: optional photos (at most 3,
 * after the versioned consent — a draft pending the hospital's review), uploaded one by one before sending
 * and attached with the report; when photos are off the text report works as before.
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
const photoStore = useSkinPhotoStore()
const followUps = useFollowUpStore() // 照護追蹤 (Phase 1c): read only
const FOLLOW_UP_TEXT = { open: '已安排追蹤', completed: '追蹤已完成', cancelled: '追蹤已取消' }
const followUpOf = computed(() => {
  const out = {}
  for (const f of followUps.mine?.items ?? []) if (!out[f.symptom_record.id] || f.status === 'open') out[f.symptom_record.id] = f
  return out
})
const shortDate = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
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

// 皮膚照片 (optional)
const photos = ref([]) // SkinPhotoPicker items
const photoSettings = computed(() => photoStore.settings)
const consent = computed(() => photoSettings.value?.consent)
const photoBlocked = ref('') // why the report cannot be sent yet (a photo still uploading / failed)
const viewing = ref(null) // { photo, answers, notes }
const photoHistory = ref(null)
const consentBusy = ref(false)
const consentError = ref(null)
const photoSpec = computed(() => photos.value.filter((p) => p.status === 'uploaded').map((p) => `${p.photoId}:${p.site}`).join(','))

const answered = computed(() => questions.value.filter((i) => answers[i.definition.code] != null))
const safetyHint = computed(() => Object.entries(SAFETY_ANSWERS).some(([code, codes]) => codes.includes(answers[code])))

// a changed answer or photo set is a new submission (a retry of the same one keeps its Idempotency-Key)
watch([answers, assist, notes, photoSpec], () => {
  photoBlocked.value = ''
  idempotencyKey = null
  submitError.value = null
  if (answered.value.length || assist.value) missing.value = false
})

const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
// answers arrive in the form's question order (API and mock)
function describe(r) {
  return r.values.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}` : `${v.label} ${v.score ?? v.value_numeric}`)).join('、')
}

function clearPhotos() {
  for (const p of photos.value) if (p.previewUrl) URL.revokeObjectURL(p.previewUrl)
  photos.value = []
}

async function acceptConsent() {
  consentBusy.value = true
  consentError.value = null
  try {
    await photoStore.acceptConsent()
  } catch (err) {
    consentError.value = err
    if (err.code === 'CONSENT_VERSION_MISMATCH') photoStore.fetchSettings() // show the new text
  } finally {
    consentBusy.value = false
  }
}

async function revokeConsent() {
  if (!confirm('撤回後就不能再上傳皮膚照片。這次還沒送出的照片會一併移除；已送出的照片不會被刪除，依醫院規定保存。確定要撤回嗎？')) return
  for (const p of [...photos.value]) if (p.photoId) photoStore.discard(p.photoId).catch(() => {})
  clearPhotos()
  await photoStore.revokeConsent()
}

function reset() {
  clearPhotos()
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
  if (photos.value.some((p) => p.status === 'preparing' || p.status === 'uploading')) {
    photoBlocked.value = '照片還在上傳，請稍候再送出。'
    return
  }
  if (photos.value.some((p) => p.status === 'failed' || p.status === 'invalid')) {
    photoBlocked.value = '有照片沒有上傳成功：請按「重試」，或「移除」後再送出（您填寫的內容都還在）。'
    return
  }
  const attached = photos.value.map((p) => ({ id: p.photoId, ...(p.site ? { body_site: p.site } : {}) }))
  idempotencyKey ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  state.value = 'submitting'
  const values = answered.value.map((i) => {
    const { code, value_type: type } = i.definition
    return type === 'scale' ? { definition_code: code, value_numeric: answers[code] } : { definition_code: code, option_code: answers[code] }
  })
  if (assist.value) values.push({ definition_code: ASSIST, option_code: 'yes' })
  const text = notes.value.trim()
  try {
    await dashboard.submitSymptomReport(patientId.value,
      { form_code: FORM_CODE, values, ...(text ? { notes: text } : {}), ...(attached.length ? { photos: attached } : {}) }, idempotencyKey)
    idempotencyKey = null
    state.value = 'done'
    clearPhotos() // now part of the report (not discarded)
    portal.fetchFormRecords(patientId.value, FORM_CODE)
    photoHistory.value?.reload()
  } catch (err) {
    if (!err.retryable) idempotencyKey = null
    submitError.value = err
    state.value = 'form'
  }
}

onMounted(() => {
  dashboard.fetchSymptomForm(FORM_CODE)
  portal.fetchFormRecords(patientId.value, FORM_CODE)
  photoStore.fetchSettings()
  followUps.fetchMine()
  if (!dashboard.patients[patientId.value]) dashboard.fetchPatientDashboard(patientId.value)
})
onBeforeUnmount(() => {
  clearPhotos() // previews only: unsent photos stay pending and are cleaned up by the server
  photoStore.releaseImages()
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

          <!-- 皮膚照片 (optional) -->
          <section class="space-y-3 rounded-xl border border-line p-4" aria-labelledby="photo-title" data-photo-section>
            <h3 id="photo-title" class="text-lg font-bold">皮膚照片（選填）</h3>
            <p v-if="!photoSettings && !photoStore.settingsError" class="h-10 animate-pulse rounded-lg bg-mist" aria-busy="true" />
            <p v-else-if="photoStore.settingsError || !photoSettings.upload_enabled" class="rounded-lg bg-mist px-3 py-2 text-sm text-ink-soft" data-photo-disabled>
              照片上傳目前未開放。您仍可以用文字記錄皮膚狀況，或在服務時間撥打電話詢問。
            </p>
            <div v-else-if="!consent?.accepted" class="space-y-2 text-sm" data-photo-consent>
              <p class="flex flex-wrap items-center gap-2">
                <span class="font-bold">{{ consent.title }}</span>
                <span v-if="consent.review_status === 'draft'" class="rounded-full bg-warn-soft px-2 py-0.5 text-xs font-bold text-warn" data-consent-draft>草稿・待院方審核</span>
              </p>
              <p class="text-ink-soft">{{ consent.notice }}</p>
              <dl class="space-y-1.5">
                <div v-for="s in consent.sections" :key="s.heading">
                  <dt class="font-bold">{{ s.heading }}</dt>
                  <dd>{{ s.text }}</dd>
                </div>
              </dl>
              <p v-if="consentError" class="text-critical" role="alert">{{ consentError.message }}</p>
              <button type="button" class="min-h-12 w-full rounded-full border border-care px-5 font-bold text-care hover:bg-care-soft disabled:opacity-60"
                :disabled="consentBusy" data-consent-accept @click="acceptConsent">我已閱讀，同意上傳皮膚照片</button>
              <p class="text-ink-soft">不同意也沒關係，您仍可以只用文字記錄。</p>
            </div>
            <template v-else>
              <SkinPhotoPicker v-model="photos" :patient-id="patientId" :max="photoSettings.max_per_record" :body-sites="photoSettings.body_sites" />
              <p class="text-xs text-ink-soft">照片送出後不會立即有人查看；上傳照片不代表醫護人員已經看過。<button type="button"
                class="ml-1 underline underline-offset-2" data-consent-revoke @click="revokeConsent">撤回照片同意</button></p>
            </template>
          </section>

          <label class="block">
            <span class="text-lg font-bold">補充說明（選填）</span>
            <span class="block text-sm text-ink-soft">例如：哪個部位、什麼時候開始、有沒有擦藥。</span>
            <textarea v-model="notes" rows="3" :maxlength="MAX_NOTES" class="mt-2 w-full rounded-xl border border-line bg-surface px-3 py-2 text-base" data-skin-notes />
            <span class="block text-right text-sm text-ink-soft">{{ notes.length }} / {{ MAX_NOTES }}</span>
          </label>

          <p v-if="missing" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert" data-skin-missing>
            請至少回答一題，或勾選「不確定，請護理師協助評估」。
          </p>
          <p v-if="photoBlocked" class="rounded-xl bg-warn-soft px-4 py-3 text-warn" role="alert" data-photo-blocked>{{ photoBlocked }}</p>
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
            <ul v-if="r.photos?.length" class="mt-2 flex gap-2" data-skin-record-photos>
              <li v-for="ph in r.photos" :key="ph.id">
                <button type="button" class="block size-16 overflow-hidden rounded-lg border border-line" :aria-label="`放大照片${ph.body_site_label ? `（${ph.body_site_label}）` : ''}`"
                  @click="viewing = { photo: ph, answers: describe(r), notes: r.notes ?? '' }">
                  <SkinPhoto :photo-id="ph.id" />
                </button>
              </li>
            </ul>
            <p class="mt-1 text-sm text-ink-soft">{{ r.review_status === 'reviewed' ? '護理師已看過這筆紀錄' : '護理師會在服務時間內查看' }}</p>
            <p v-if="followUpOf[r.id]" class="mt-1 text-sm font-bold text-care" data-skin-follow-up>
              {{ FOLLOW_UP_TEXT[followUpOf[r.id].status] }}<template v-if="followUpOf[r.id].status === 'open'">（預定 {{ shortDate(followUpOf[r.id].due_local_date) }}）</template>
            </p>
          </li>
        </ul>
      </section>

      <!-- 照護追蹤 (Phase 1c): read only; no nurse notes; not real-time monitoring -->
      <section v-if="followUps.mine?.items.length || followUps.mineError" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="my-follow-ups"
        data-my-follow-ups>
        <h2 id="my-follow-ups" class="text-lg font-bold">照護追蹤</h2>
        <p class="mt-2 rounded-xl bg-critical-soft px-3 py-2 text-sm text-critical" role="note" data-follow-up-notice>
          本功能為照護追蹤紀錄，並非即時監測或緊急通報系統。如有急性或嚴重不適，請依醫院指示聯繫醫療團隊；緊急情況請撥打 119。
        </p>
        <p v-if="followUps.mineError" class="mt-2 text-critical" role="alert">{{ followUps.mineError.message }}</p>
        <ul v-else class="mt-2 divide-y divide-line">
          <li v-for="f in followUps.mine.items" :key="f.id" class="flex flex-wrap items-baseline gap-x-3 py-2" :data-my-follow-up="f.status">
            <span class="font-bold">{{ FOLLOW_UP_TEXT[f.status] }}</span>
            <span class="text-sm text-ink-soft">預定 {{ f.due_local_date }}・{{ localDate(f.symptom_record.recorded_at, TZ) }} 的回報</span>
          </li>
        </ul>
      </section>

      <SkinPhotoHistory v-if="photoSettings?.view_enabled" ref="photoHistory" :patient-id="patientId" title="我的皮膚照片" />
      <SkinPhotoViewer v-if="viewing" :photo="viewing.photo" :answers="viewing.answers" :notes="viewing.notes" @close="viewing = null" />
    </main>

    <PatientBottomNav active="symptom-report" :unread="unread" />
  </div>
</template>
