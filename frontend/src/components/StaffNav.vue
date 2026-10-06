<script setup>
import AppIcon from '@/components/AppIcon.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'

/** Navigation of the admin workspace (the nurse workspace uses NurseNav). */
defineProps({
  active: { type: String, required: true }, // key of the current page
})

const store = useDashboardStore()
const auth = useAuthStore()

const NAV = [
  { label: '管理總覽', icon: 'home', to: { name: 'admin-home' }, key: 'admin-home' },
  { label: '病人與照護團隊', icon: 'users', to: { name: 'admin-patients' }, key: 'admin-patients' },
  { label: '護理師帳號', icon: 'user', to: { name: 'admin-nurses' }, key: 'admin-nurses' },
  { label: '帳號狀態', icon: 'shield', to: { name: 'admin-accounts' }, key: 'admin-accounts' },
  { label: '批量建立教學帳號', icon: 'users', to: { name: 'admin-training' }, key: 'admin-training' },
  { label: '稽核紀錄', icon: 'clipboard', to: { name: 'admin-audit' }, key: 'admin-audit' },
  { label: '風險規則與量表', icon: 'alert', to: { name: 'admin-rules' }, key: 'admin-rules' },
  { label: '系統設定', icon: 'chart', to: { name: 'admin-settings' }, key: 'admin-settings' },
]
</script>

<template>
  <aside class="border-b border-line bg-surface lg:border-r lg:border-b-0">
    <div class="p-4 lg:sticky lg:top-0 lg:p-5">
      <p class="font-bold">{{ store.settings?.organization.name }}</p>
      <p class="text-sm text-ink-soft">系統管理</p>
      <nav class="mt-3 lg:mt-6" aria-label="管理功能">
        <ul class="flex gap-1 overflow-x-auto lg:block lg:space-y-1">
          <li v-for="n in NAV" :key="n.key" class="shrink-0">
            <RouterLink
              :to="n.to"
              class="flex min-h-12 items-center gap-3 rounded-lg px-3"
              :class="n.key === active ? 'bg-care-soft font-bold text-care' : 'text-ink-soft hover:bg-mist hover:text-ink'"
              :aria-current="n.key === active ? 'page' : undefined"
            >
              <AppIcon :name="n.icon" :size="20" /> {{ n.label }}
            </RouterLink>
          </li>
        </ul>
      </nav>
      <p class="mt-8 hidden text-sm text-ink-soft lg:block">{{ auth.user?.display_name }}</p>
    </div>
  </aside>
</template>
