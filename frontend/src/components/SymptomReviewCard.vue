<script setup>
import { computed, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import NoteComposer from '@/components/NoteComposer.vue'
import { useNurseStore } from '@/stores/nurse'
import { addDays, formatShortDate, formatWhen, localDate } from '@/utils/format'

/**
 * One symptom report for nurse review: the answers, the alerts it raised, and either the
 * review form or the reviewed state (reviewer, time, action note).
 */
const props = defineProps({
  record: { type: Object, required: true },
  patientId: { type: String, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const store = useNurseStore()
const open = ref(false)
const busy = ref(false)
const error = ref(null)
const form = reactive({ action_note: '', assessment_type: 'phone_follow_up', resolve_alerts: true, grades: {}, follow_up: false, follow_up_date: '' })

const scaleValues = computed(() => props.record.values.filter((v) => v.value_numeric !== undefined))
// single-choice answers (e.g. 每日症狀與自我照護回報): shown as answers only — "needs attention" is the
// existing score >= 7 rule of the 0–10 questions, no rule for options
const SKIN_FORM = 'rt_skin_self_assessment'
const ASSIST = 'skin_assist_request'
const choiceValues = computed(() => props.record.values.filter((v) => v.option_label !== undefined && v.definition_code !== ASSIST))
const FORM_NAME = { rt_daily_report: '每日症狀與自我照護回報', [SKIN_FORM]: '皮膚自我評估' }
// 皮膚自我評估: no professional grade here yet (the standard is not confirmed — CTCAE v5.0 is only a
// candidate, and the per-question grade column does not fit an overall dermatitis grade); optional follow-up
const skin = computed(() => props.record.form?.code === SKIN_FORM)
const assistRequested = computed(() => props.record.values.some((v) => v.definition_code === ASSIST))
const followUpAt = computed(() => props.record.review?.next_follow_up_at ?? null)
const maxFollowUp = computed(() => addDays(props.today, 180))
const formName = computed(() => FORM_NAME[props.record.form?.code] ?? null)
const fever = computed(() => props.record.values.find((v) => v.definition_code === 'fever'))
const openAlerts = computed(() => props.record.alerts.filter((a) => !a.resolved))
const reviewed = computed(() => props.record.review_status === 'reviewed')

// A validation message goes away as soon as the nurse starts fixing it.
watch(() => form.action_note, () => {
  error.value = null
})

const TYPE_LABEL = { phone_follow_up: '電話追蹤', follow_up: '一般追蹤' }

async function submit() {
  if (!form.action_note.trim()) {
    error.value = '請填寫處置說明。'
    return
  }
  if (form.follow_up && !form.follow_up_date) {
    error.value = '請選擇追蹤日期。'
    return
  }
  busy.value = true
  error.value = null
  const ctcae = Object.entries(form.grades)
    .filter(([, g]) => g !== '' && g !== null && g !== undefined)
    .map(([code, g]) => ({ definition_code: code, ctcae_grade: Number(g) }))
  try {
    await store.review(props.record.id, {
      action_note: form.action_note.trim(),
      assessment_type: form.assessment_type,
      resolve_alerts: form.resolve_alerts,
      ...(ctcae.length ? { ctcae_grades: ctcae } : {}),
      // Asia/Taipei has no daylight saving time: 09:00 local on the chosen day
      ...(skin.value && form.follow_up ? { next_follow_up_at: `${form.follow_up_date}T09:00:00+08:00` } : {}),
    }, props.patientId)
    open.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <article class="rounded-xl border px-4 py-3" :class="reviewed ? 'border-line bg-surface' : openAlerts.length ? 'border-warn/60 bg-surface' : 'border-line bg-surface'">
    <header class="flex flex-wrap items-baseline gap-x-3">
      <p class="font-bold">{{ formatWhen(record.recorded_at, today, timezone) }}</p>
      <p v-if="record.cycle_day" class="text-sm text-ink-soft">療程第 {{ record.cycle_day }} 天</p>
      <p class="text-sm text-ink-soft">{{ record.source === 'nurse' ? '護理師代填' : '病人自評' }}</p>
      <p v-if="formName" class="text-sm font-medium text-care" data-record-form>{{ formName }}</p>
      <span
        class="ml-auto rounded-full px-2.5 py-0.5 text-sm font-bold"
        :class="reviewed && followUpAt ? 'bg-action text-action-ink' : reviewed ? 'bg-ok-soft text-ok' : 'bg-warn-soft text-warn'"
        :data-review-state="reviewed && followUpAt ? 'follow_up' : reviewed ? 'reviewed' : 'submitted'"
      >{{ reviewed && followUpAt ? '需追蹤' : reviewed ? '已審閱' : '待審閱' }}</span>
    </header>

    <p v-if="assistRequested" class="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-warn-soft px-3 py-1 font-bold text-warn" data-assist-request>
      <AppIcon name="hand" :size="16" /> 病人不確定自己的狀況，請護理師協助評估
    </p>

    <ul class="mt-2 flex flex-wrap gap-2">
      <li
        v-for="v in scaleValues"
        :key="v.definition_code"
        class="rounded-lg px-3 py-1"
        :class="v.score >= 7 ? 'bg-action font-bold text-action-ink' : 'bg-mist'"
        :data-attention="v.score >= 7 ? 'true' : null"
      >{{ v.label }} {{ v.score }}<span v-if="v.score >= 7" class="sr-only">（需要注意）</span></li>
      <li v-for="v in choiceValues" :key="v.definition_code" class="rounded-lg bg-mist px-3 py-1" data-choice-answer>{{ v.label }}：{{ v.option_label }}</li>
      <li
        v-if="fever"
        class="rounded-lg px-3 py-1"
        :class="fever.value_boolean ? 'bg-critical font-bold text-white' : 'bg-mist'"
      >{{ fever.value_boolean ? '有發燒或畏寒' : '無發燒' }}</li>
    </ul>

    <p v-if="record.notes" class="mt-2 whitespace-pre-line rounded-lg bg-mist px-3 py-2 text-sm" data-record-notes>
      <span class="font-medium">{{ record.source === 'nurse' ? '備註' : '病人補充說明' }}：</span>{{ record.notes }}
    </p>

    <ul v-if="record.alerts.length" class="mt-2 space-y-1 text-sm">
      <li v-for="a in record.alerts" :key="a.event_key" class="flex items-center gap-1.5">
        <AppIcon :name="a.resolved ? 'check' : 'alert'" :size="16" :class="a.resolved ? 'text-ok' : a.severity === 'critical' ? 'text-critical' : 'text-warn'" />
        <span :class="a.resolved ? 'text-ink-soft' : 'font-medium'">{{ a.title }}</span>
        <span v-if="a.resolved" class="text-ink-soft">（已由 {{ a.resolved_by }} 處理）</span>
      </li>
    </ul>

    <!-- reviewed state -->
    <div v-if="reviewed" class="mt-3 rounded-lg bg-ok-soft px-3 py-2">
      <p class="flex flex-wrap items-center gap-1.5 text-sm font-bold text-ok">
        <AppIcon name="check" :size="16" />
        {{ record.reviewed_by?.display_name }}，{{ formatWhen(record.reviewed_at, today, timezone) }}
        <span v-if="record.review">，{{ TYPE_LABEL[record.review.assessment_type] ?? record.review.assessment_type }}</span>
      </p>
      <p v-if="record.review" class="mt-0.5">{{ record.review.action_note }}</p>
      <p v-if="followUpAt" class="mt-1 text-sm font-bold text-action-ink" data-follow-up>需追蹤：{{ formatShortDate(localDate(followUpAt, timezone)) }}</p>
    </div>

    <!-- review form -->
    <template v-else>
      <button
        v-if="!open"
        type="button"
        class="mt-3 min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90"
        @click="open = true"
      >審閱</button>

      <form v-else class="mt-3 space-y-4 border-t border-line pt-3" @submit.prevent="submit">
        <fieldset>
          <legend class="font-medium">處置方式</legend>
          <div class="mt-1 flex flex-wrap gap-2">
            <label v-for="(label, value) in TYPE_LABEL" :key="value" class="relative">
              <input v-model="form.assessment_type" type="radio" :value="value" class="peer sr-only" />
              <span
                class="grid min-h-10 cursor-pointer place-items-center rounded-full border px-4 peer-focus-visible:outline-3 peer-focus-visible:outline-care"
                :class="form.assessment_type === value ? 'border-care bg-care font-bold text-white' : 'border-line hover:bg-care-soft'"
              >{{ label }}</span>
            </label>
          </div>
        </fieldset>

        <NoteComposer
          v-model="form.action_note"
          label="處置說明"
          placeholder="例如：已電話衛教止痛藥使用，明日追蹤"
          :maxlength="2000"
          :invalid="!!error && !form.action_note.trim()"
        />

        <template v-if="skin">
          <p class="rounded-lg bg-mist px-3 py-2 text-sm text-ink-soft" data-grade-pending>
            皮膚反應的專業分級標準尚待院方確認（候選：CTCAE v5.0 Dermatitis radiation），本版不在此記錄分級；請在處置說明中描述評估結果。病人看不到這裡的內容。
          </p>
          <div class="space-y-2" data-follow-up-form>
            <label class="flex items-center gap-2">
              <input v-model="form.follow_up" type="checkbox" class="size-5 accent-care" data-follow-up-toggle />
              <span class="font-medium">需要追蹤</span>
            </label>
            <label v-if="form.follow_up" class="flex flex-wrap items-center gap-2">
              <span>追蹤日期</span>
              <input v-model="form.follow_up_date" type="date" :min="today" :max="maxFollowUp" class="min-h-10 rounded-lg border border-line bg-surface px-2" data-follow-up-date />
            </label>
          </div>
        </template>
        <details v-else class="rounded-lg border border-line px-3 py-2">
          <summary class="cursor-pointer font-medium">CTCAE 分級（選填）</summary>
          <div class="mt-2 grid gap-2 sm:grid-cols-2">
            <label v-for="v in scaleValues" :key="v.definition_code" class="flex items-center justify-between gap-3">
              <span>{{ v.label }}（自評 {{ v.score }}）</span>
              <select v-model="form.grades[v.definition_code]" class="min-h-10 rounded-lg border border-line bg-surface px-2">
                <option value="">未分級</option>
                <option v-for="g in 6" :key="g - 1" :value="g - 1">Grade {{ g - 1 }}</option>
              </select>
            </label>
          </div>
        </details>

        <label v-if="openAlerts.length" class="flex items-start gap-2">
          <input v-model="form.resolve_alerts" type="checkbox" class="mt-1 size-5 accent-care" />
          <span>同時將這筆回報的 {{ openAlerts.length }} 則警示標為已處理（使用上面的處置說明）</span>
        </label>

        <p v-if="error" class="font-medium text-critical" role="alert">{{ error }}</p>
        <div class="flex flex-wrap gap-2">
          <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="busy">
            {{ busy ? '儲存中…' : '完成審閱' }}
          </button>
          <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" :disabled="busy" @click="open = false">取消</button>
        </div>
      </form>
    </template>
  </article>
</template>
