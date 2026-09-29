<script setup>
import { computed, onMounted } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'
import { useDashboardStore } from '@/stores/dashboard'
import { formatTime, localDate } from '@/utils/format'

/** 管理總覽 (admin home): what needs an administrator's attention, each linked to where it is handled. */
const dashboard = useDashboardStore()
const store = useAdminStore()
const o = computed(() => store.overview)

const tiles = computed(() => o.value && [
  { key: 'unassigned', label: '尚未指派護理師的病人', value: o.value.patients.unassigned, of: `共 ${o.value.patients.total} 位病人`,
    to: { name: 'admin-patients', query: { assigned: 'false' } }, attention: o.value.patients.unassigned > 0 },
  { key: 'without-account', label: '尚未開通登入帳號的病人', value: o.value.patients.without_account, of: '在病人資料頁開通',
    to: { name: 'admin-patients' }, attention: false },
  { key: 'nurses', label: '啟用中的護理師', value: o.value.nurses.active, of: `${o.value.nurses.must_change_password} 位尚未設定新密碼`,
    to: { name: 'admin-nurses' }, attention: false },
  { key: 'inactive', label: '已停用的帳號', value: o.value.accounts.inactive, of: `${o.value.accounts.locked} 個帳號登入鎖定中`,
    to: { name: 'admin-accounts', query: { status: o.value.accounts.locked ? 'locked' : 'inactive' } }, attention: o.value.accounts.locked > 0 },
  { key: 'assignments', label: '進行中的照護指派', value: o.value.assignments.active, of: '主責與協同護理師',
    to: { name: 'admin-patients' }, attention: false },
])

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchOverview()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-home" />
    <main class="min-w-0 space-y-5 p-4 lg:p-6" :data-loaded="o ? '' : undefined">
      <header>
        <h1 class="text-2xl font-bold">管理總覽</h1>
        <p class="text-ink-soft">帳號、照護團隊與系統設定。臨床紀錄由病人的護理師處理，管理者不在這裡修改。</p>
      </header>

      <p v-if="store.errors.overview" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ store.errors.overview.message }}</p>

      <template v-else-if="o">
        <section aria-label="臨床狀況（唯讀）" class="grid gap-3 sm:grid-cols-2" data-clinical>
          <div class="rounded-2xl border border-line bg-surface p-5">
            <p class="flex items-center gap-2 text-ink-soft"><AppIcon name="alert" :size="18" /> 尚未處理的風險警示</p>
            <p class="mt-1 text-3xl font-bold" data-open-alerts>{{ o.alerts.open }}</p>
            <p class="text-sm" :class="o.alerts.critical ? 'font-bold text-critical' : 'text-ink-soft'">其中 {{ o.alerts.critical }} 件為緊急</p>
          </div>
          <div class="rounded-2xl border border-line bg-surface p-5">
            <p class="flex items-center gap-2 text-ink-soft"><AppIcon name="clipboard" :size="18" /> 等待護理師審閱的症狀回報</p>
            <p class="mt-1 text-3xl font-bold" data-pending-reviews>{{ o.symptom_reviews.pending }}</p>
            <p class="text-sm text-ink-soft">由負責護理師處理；若長時間未下降，請確認指派是否完整</p>
          </div>
        </section>

        <section aria-labelledby="admin-work" class="rounded-2xl border border-line bg-surface">
          <h2 id="admin-work" class="border-b border-line px-5 py-3 text-lg font-bold">帳號與照護團隊</h2>
          <ul class="divide-y divide-line">
            <li v-for="t in tiles" :key="t.key">
              <RouterLink :to="t.to" class="flex min-h-16 items-center gap-4 px-5 py-3 hover:bg-mist" :data-tile="t.key">
                <span class="w-14 shrink-0 text-right text-2xl font-bold tabular-nums" :class="t.attention ? 'text-warn' : ''">{{ t.value }}</span>
                <span class="min-w-0 flex-1">
                  <span class="block font-medium">{{ t.label }}</span>
                  <span class="block text-sm text-ink-soft">{{ t.of }}</span>
                </span>
                <AppIcon name="chevron" :size="18" class="shrink-0 text-ink-soft" />
              </RouterLink>
            </li>
          </ul>
        </section>
        <p class="text-sm text-ink-soft">更新於 {{ localDate(o.generated_at) }} {{ formatTime(o.generated_at) }}，<button type="button" class="underline" @click="store.fetchOverview()">重新整理</button></p>
      </template>

      <div v-else class="grid gap-3 sm:grid-cols-2" aria-busy="true">
        <div v-for="k in 4" :key="k" class="h-28 animate-pulse rounded-2xl bg-mist" />
      </div>
    </main>
  </div>
</template>
