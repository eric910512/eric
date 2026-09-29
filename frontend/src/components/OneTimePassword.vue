<script setup>
import { ref } from 'vue'

/**
 * A temporary password shown exactly once (api-design.md §4.1). The value lives only in the
 * parent's local state: it is never put in a store, storage or the URL, and it is gone as soon
 * as this panel is closed or the page is left.
 */
const props = defineProps({
  email: { type: String, required: true },
  password: { type: String, required: true },
  who: { type: String, default: '' }, // e.g. "測試病人 丙（P00005）"
})
const emit = defineEmits(['done'])
const copied = ref(false)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.password)
    copied.value = true
  } catch {
    copied.value = false // clipboard blocked: the password is still on screen to copy by hand
  }
}
</script>

<template>
  <section class="rounded-2xl border-2 border-action-ink/40 bg-action p-5 text-action-ink" role="alert" aria-labelledby="otp-title" data-one-time-password>
    <h2 id="otp-title" class="text-lg font-bold">初始密碼只會顯示這一次</h2>
    <p class="mt-1 text-ink">
      請把帳號與初始密碼交給{{ who || '使用者' }}。第一次登入後，系統會要求設定新密碼。關閉後就無法再查看。
    </p>
    <dl class="mt-4 grid gap-3 sm:grid-cols-[auto_1fr] sm:items-center">
      <dt class="text-sm text-ink-soft">登入帳號</dt>
      <dd class="font-bold break-all text-ink" data-otp-email>{{ email }}</dd>
      <dt class="text-sm text-ink-soft">初始密碼</dt>
      <dd class="flex flex-wrap items-center gap-3">
        <code class="rounded-lg bg-surface px-3 py-2 text-2xl font-bold tracking-wider text-ink select-all" data-otp-password>{{ password }}</code>
        <button type="button" class="min-h-11 rounded-full border border-action-ink/40 bg-surface px-4 font-bold hover:bg-white" @click="copy">
          {{ copied ? '已複製' : '複製密碼' }}
        </button>
      </dd>
    </dl>
    <button type="button" class="mt-5 min-h-12 rounded-full bg-action-ink px-6 font-bold text-white hover:bg-action-ink/90" data-otp-done @click="emit('done')">
      我已交給使用者，關閉
    </button>
  </section>
</template>
