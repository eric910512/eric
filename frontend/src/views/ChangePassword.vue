<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import { useAuthStore } from '@/stores/auth'
import { PASSWORD_MAX, PASSWORD_MIN, passwordProblems } from '@/utils/password'

/** 設定新密碼: required after signing in with a temporary password; also usable any time. */
const auth = useAuthStore()
const router = useRouter()

const current = ref('')
const next = ref('')
const confirm = ref('')
const show = ref(false)
const submitting = ref(false)
const error = ref('')
const fieldErrors = ref({})

const firstTime = computed(() => auth.mustChangePassword)
const ISSUE_TEXT = {
  'is incorrect': '密碼不正確',
  'is required': '請輸入密碼',
  'must contain both letters and digits': '需要同時包含英文字母和數字',
  'must differ from the current password': '不能和目前的密碼相同',
}
const issueText = (issue) =>
  ISSUE_TEXT[issue] ?? (/characters$/.test(issue) ? `長度需為 ${PASSWORD_MIN}–${PASSWORD_MAX} 個字元` : issue)
const rules = computed(() => [
  { ok: next.value.length >= PASSWORD_MIN && next.value.length <= PASSWORD_MAX, text: `${PASSWORD_MIN} 個字元以上` },
  { ok: /\p{L}/u.test(next.value) && /\d/.test(next.value), text: '同時包含英文字母和數字' },
  { ok: next.value.length > 0 && next.value !== current.value, text: '和目前的密碼不同' },
])

async function submit() {
  error.value = ''
  const errs = {}
  const local = passwordProblems(next.value, current.value)
  if (!current.value) errs.current_password = ISSUE_TEXT['is required']
  if (local.length) errs.new_password = local.map(issueText).join('；')
  if (next.value !== confirm.value) errs.confirm = '兩次輸入的新密碼不一樣'
  fieldErrors.value = errs
  if (Object.keys(errs).length) return
  submitting.value = true
  try {
    await auth.changePassword(current.value, next.value)
    current.value = ''
    next.value = ''
    confirm.value = ''
    router.replace(auth.homeRoute)
  } catch (e) {
    const byField = {}
    for (const d of e.details ?? []) byField[d.field] = [byField[d.field], issueText(d.issue)].filter(Boolean).join('；')
    fieldErrors.value = byField
    if (!e.details?.length) error.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="grid min-h-[calc(100dvh-2rem)] place-items-center px-4 py-10">
    <form class="w-full max-w-md rounded-3xl border border-line bg-surface p-6" novalidate data-change-password @submit.prevent="submit">
      <span class="grid size-12 place-items-center rounded-full bg-care-soft text-care"><AppIcon name="shield" /></span>
      <h1 class="mt-3 text-2xl font-bold">設定新密碼</h1>
      <p v-if="firstTime" class="mt-1 text-ink-soft">您正在使用初始密碼。請先設定只有您知道的新密碼，才能開始使用。</p>
      <p v-else class="mt-1 text-ink-soft">更換密碼後，下次請使用新密碼登入。<button type="button" class="ml-1 text-care underline" data-cancel-change @click="router.back()">返回</button></p>
      <p v-if="error" class="mt-4 rounded-xl bg-critical-soft px-4 py-3 font-medium text-critical" role="alert">{{ error }}</p>

      <label class="mt-5 block">
        <span class="font-medium">{{ firstTime ? '初始密碼' : '目前的密碼' }}</span>
        <input
          v-model="current" :type="show ? 'text' : 'password'" name="current_password" autocomplete="current-password"
          class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4 text-lg focus:border-care"
          :aria-invalid="!!fieldErrors.current_password" aria-describedby="err-current"
        />
        <span v-if="fieldErrors.current_password" id="err-current" class="mt-1 block text-critical">{{ fieldErrors.current_password }}</span>
      </label>
      <label class="mt-4 block">
        <span class="font-medium">新密碼</span>
        <input
          v-model="next" :type="show ? 'text' : 'password'" name="new_password" autocomplete="new-password"
          class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4 text-lg focus:border-care"
          :aria-invalid="!!fieldErrors.new_password" aria-describedby="pw-rules err-new"
        />
        <span v-if="fieldErrors.new_password" id="err-new" class="mt-1 block text-critical">{{ fieldErrors.new_password }}</span>
      </label>
      <ul id="pw-rules" class="mt-2 space-y-1 text-sm">
        <li v-for="r in rules" :key="r.text" class="flex items-center gap-2" :class="r.ok ? 'text-ok' : 'text-ink-soft'">
          <AppIcon :name="r.ok ? 'check' : 'chevron'" :size="16" /> {{ r.text }}
        </li>
      </ul>
      <label class="mt-4 block">
        <span class="font-medium">再輸入一次新密碼</span>
        <input
          v-model="confirm" :type="show ? 'text' : 'password'" name="confirm_password" autocomplete="new-password"
          class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4 text-lg focus:border-care"
          :aria-invalid="!!fieldErrors.confirm" aria-describedby="err-confirm"
        />
        <span v-if="fieldErrors.confirm" id="err-confirm" class="mt-1 block text-critical">{{ fieldErrors.confirm }}</span>
      </label>
      <label class="mt-3 inline-flex min-h-11 items-center gap-2">
        <input v-model="show" type="checkbox" class="size-5" /> 顯示密碼
      </label>

      <button
        type="submit"
        class="mt-5 min-h-14 w-full rounded-full bg-care text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
        :disabled="submitting"
      >
        {{ submitting ? '儲存中…' : '儲存新密碼' }}
      </button>
    </form>
  </main>
</template>
