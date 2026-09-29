<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import { DEMO_PASSWORD } from '@/config/demo'
import { LoginError, useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'

const auth = useAuthStore()
const dashboard = useDashboardStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const error = ref('')

const NOTICE = {
  expired: '登入已逾時，請重新登入。',
  unauthorized: '請重新登入後再繼續。',
  logout: '您已登出。',
}
const notice = computed(() => NOTICE[route.query.reason] ?? '')

const ERROR_TEXT = {
  UNAUTHENTICATED: '帳號或密碼錯誤，請再確認一次。',
  ACCOUNT_LOCKED: '登入失敗次數過多，帳號已暫時鎖定。請 15 分鐘後再試，或聯絡護理站。',
  VALIDATION_ERROR: '請輸入帳號與密碼。',
}

const DEMO_ACCOUNTS = [
  { email: 'patient01@demo.local', label: '病人（一般）' },
  ...(USE_MOCK ? [{ email: 'patient02@demo.local', label: '病人（高風險示範）' }] : []),
  { email: 'nurse01@demo.local', label: '護理師' },
  { email: 'admin01@demo.local', label: '管理者' },
]
const showDemo = import.meta.env.DEV
// Only referenced in dev builds; production builds drop the constant.
const demoPassword = import.meta.env.DEV ? DEMO_PASSWORD : ''

function useDemo(account) {
  email.value = account.email
  password.value = demoPassword
  error.value = ''
}

async function submit() {
  error.value = ''
  if (!email.value.trim() || !password.value) {
    error.value = ERROR_TEXT.VALIDATION_ERROR
    return
  }
  submitting.value = true
  try {
    await auth.login(email.value, password.value)
    const target = route.query.redirect && router.resolve(route.query.redirect)
    const allowed = target && (!target.meta.roles || target.meta.roles.includes(auth.role))
    router.replace(allowed ? target.fullPath : auth.homeRoute)
  } catch (err) {
    error.value = err instanceof LoginError ? (ERROR_TEXT[err.code] ?? err.message) : '登入失敗，請稍後再試。'
  } finally {
    submitting.value = false
  }
}

onMounted(() => dashboard.fetchSettings())
</script>

<template>
  <main class="grid min-h-[calc(100dvh-2rem)] place-items-center px-4 py-10">
    <div class="w-full max-w-md">
      <div class="mb-6 flex items-center gap-3">
        <span class="grid size-12 place-items-center rounded-full bg-care text-white"><AppIcon name="drop" /></span>
        <div>
          <p class="text-xl font-bold">{{ dashboard.settings?.organization.name ?? '化療照護' }}</p>
          <p class="text-ink-soft">{{ dashboard.settings?.organization.department ?? '　' }}</p>
        </div>
      </div>

      <form class="rounded-3xl border border-line bg-surface p-6" novalidate @submit.prevent="submit">
        <h1 class="text-2xl font-bold">登入</h1>
        <p class="mt-1 text-ink-soft">病人與護理人員請使用醫院提供的帳號登入。</p>

        <p v-if="notice && !error" class="mt-4 rounded-xl bg-care-soft px-4 py-3 text-care" role="status">{{ notice }}</p>
        <p v-if="error" class="mt-4 rounded-xl bg-critical-soft px-4 py-3 font-medium text-critical" role="alert">{{ error }}</p>

        <label class="mt-5 block">
          <span class="font-medium">電子郵件</span>
          <input
            v-model="email"
            type="email"
            name="email"
            autocomplete="username"
            inputmode="email"
            required
            class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4 text-lg focus:border-care"
            :aria-invalid="!!error"
          />
        </label>

        <label class="mt-4 block">
          <span class="font-medium">密碼</span>
          <span class="relative mt-1 block">
            <input
              v-model="password"
              :type="showPassword ? 'text' : 'password'"
              name="password"
              autocomplete="current-password"
              required
              class="block min-h-12 w-full rounded-xl border border-line bg-surface py-2 pr-20 pl-4 text-lg focus:border-care"
              :aria-invalid="!!error"
            />
            <button
              type="button"
              class="absolute inset-y-1 right-1 rounded-lg px-3 text-care hover:bg-care-soft"
              :aria-pressed="showPassword"
              @click="showPassword = !showPassword"
            >{{ showPassword ? '隱藏' : '顯示' }}</button>
          </span>
        </label>

        <button
          type="submit"
          class="mt-6 min-h-14 w-full rounded-full bg-care text-lg font-bold text-white hover:bg-care/90 disabled:opacity-60"
          :disabled="submitting"
        >
          {{ submitting ? '登入中…' : '登入' }}
        </button>
      </form>

      <section v-if="showDemo" class="mt-4 rounded-2xl border border-dashed border-line p-4" aria-label="開發用測試帳號">
        <p class="text-sm text-ink-soft">
          開發用測試帳號（{{ USE_MOCK ? '示範模式，不連後端' : '後端 seed 資料' }}），密碼皆為 {{ demoPassword }}
        </p>
        <ul class="mt-2 flex flex-wrap gap-2">
          <li v-for="a in DEMO_ACCOUNTS" :key="a.email">
            <button
              type="button"
              class="min-h-11 rounded-full border border-line bg-surface px-4 hover:bg-care-soft"
              @click="useDemo(a)"
            >{{ a.label }}</button>
          </li>
        </ul>
      </section>
    </div>
  </main>
</template>
