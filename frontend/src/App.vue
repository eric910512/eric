<script setup>
import { useRouter } from 'vue-router'

import { USE_MOCK } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const ROLE_LABEL = { patient: '病人', nurse: '護理師', admin: '管理者' }

function logout() {
  auth.logout()
  router.replace({ name: 'login', query: { reason: 'logout' } })
}
</script>

<template>
  <div v-if="USE_MOCK || auth.isAuthenticated" class="bg-ink text-sm text-white">
    <div class="mx-auto flex min-h-8 max-w-6xl flex-wrap items-center gap-x-4 px-4 py-1">
      <span v-if="USE_MOCK" class="text-white/80">示範模式：使用假資料，不連線後端</span>
      <template v-if="auth.isAuthenticated">
        <span class="ml-auto">{{ auth.user.display_name }}（{{ ROLE_LABEL[auth.role] ?? auth.role }}）</span>
        <RouterLink v-if="!auth.mustChangePassword" :to="{ name: 'account-sessions' }" class="min-h-8 content-center rounded px-2 underline-offset-2 hover:underline" data-sessions-link>登入裝置</RouterLink>
        <button type="button" class="min-h-8 rounded px-2 underline-offset-2 hover:underline" @click="logout">登出</button>
      </template>
    </div>
  </div>
  <RouterView />
</template>
