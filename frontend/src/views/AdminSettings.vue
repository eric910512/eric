<script setup>
import { onMounted } from 'vue'

import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'
import { useDashboardStore } from '@/stores/dashboard'

/** 系統設定 (admin): institution information and the security policy. Read-only in Phase 1 (set in the config file). */
const dashboard = useDashboardStore()
const store = useAdminStore()

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchSettings()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-settings" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header>
        <h1 class="text-2xl font-bold">系統設定</h1>
        <p class="text-ink-soft">目前由部署設定檔提供，這裡只能查看；需要變更時請聯絡系統維護人員。</p>
      </header>
      <p v-if="store.errors.settings" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ store.errors.settings.message }}</p>
      <template v-else-if="store.settings">
        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="inst-title" data-institution>
          <h2 id="inst-title" class="text-lg font-bold">機構資訊</h2>
          <dl class="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-[10rem_1fr]">
            <dt class="text-ink-soft">機構</dt><dd>{{ store.settings.institution.organization.name }}</dd>
            <dt class="text-ink-soft">科別</dt><dd>{{ store.settings.institution.organization.department }}</dd>
            <template v-for="c in store.settings.institution.contacts" :key="c.key">
              <dt class="text-ink-soft">{{ c.label }}</dt><dd class="tabular-nums">{{ c.phone }}</dd>
            </template>
          </dl>
        </section>
        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="sec-title" data-security>
          <h2 id="sec-title" class="text-lg font-bold">安全政策</h2>
          <dl class="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-[10rem_1fr]">
            <dt class="text-ink-soft">登入有效時間</dt><dd>{{ store.settings.security.access_token_minutes }} 分鐘</dd>
            <dt class="text-ink-soft">登入鎖定</dt>
            <dd data-lockout>連續輸錯 {{ store.settings.security.login_lockout.max_failed_attempts }} 次，鎖定 {{ store.settings.security.login_lockout.lock_minutes }} 分鐘</dd>
            <dt class="text-ink-soft">密碼規則</dt>
            <dd>{{ store.settings.security.password_policy.min_length }}–{{ store.settings.security.password_policy.max_length }} 個字元，需同時包含英文字母與數字</dd>
          </dl>
        </section>
      </template>
      <div v-else class="h-40 animate-pulse rounded-2xl bg-mist" aria-busy="true" />
    </main>
  </div>
</template>
