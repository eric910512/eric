<script setup>
import { computed, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import NoteComposer from '@/components/NoteComposer.vue'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen } from '@/utils/format'

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
const form = reactive({ action_note: '', assessment_type: 'phone_follow_up', resolve_alerts: true, grades: {} })

const scaleValues = computed(() => props.record.values.filter((v) => v.value_numeric !== undefined))
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
      <span
        class="ml-auto rounded-full px-2.5 py-0.5 text-sm font-bold"
        :class="reviewed ? 'bg-ok-soft text-ok' : 'bg-warn-soft text-warn'"
      >{{ reviewed ? '已審閱' : '待審閱' }}</span>
    </header>

    <ul class="mt-2 flex flex-wrap gap-2">
      <li
        v-for="v in scaleValues"
        :key="v.definition_code"
        class="rounded-lg px-3 py-1"
        :class="v.score >= 7 ? 'bg-action font-bold text-action-ink' : 'bg-mist'"
      >{{ v.label }} {{ v.score }}</li>
      <li
        v-if="fever"
        class="rounded-lg px-3 py-1"
        :class="fever.value_boolean ? 'bg-critical font-bold text-white' : 'bg-mist'"
      >{{ fever.value_boolean ? '有發燒或畏寒' : '無發燒' }}</li>
    </ul>

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

        <details class="rounded-lg border border-line px-3 py-2">
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
