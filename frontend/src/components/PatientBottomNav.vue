<script setup>
import AppIcon from '@/components/AppIcon.vue'

/** Patient bottom navigation (ui-architecture.md §1.4). */
defineProps({
  active: { type: String, default: 'home' },
  unread: { type: Number, default: 0 },
})

const NAV = [
  { key: 'symptom-report', label: '症狀回報', icon: 'clipboard', to: { name: 'patient-symptoms' } },
  { key: 'vitals', label: '健康紀錄', icon: 'heart', to: { name: 'patient-timeline' } }, // 照護時間軸 / 記錄生命徵象
  { key: 'home', label: '首頁', icon: 'home', to: { name: 'patient' } },
  { key: 'notifications', label: '通知', icon: 'bell', to: { name: 'patient-notifications' } },
  { key: 'profile', label: '我的', icon: 'user', to: { name: 'patient-profile' } },
]
</script>

<template>
  <nav class="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-surface" aria-label="主要功能">
    <ul class="mx-auto grid max-w-xl grid-cols-5">
      <li v-for="n in NAV" :key="n.key">
        <RouterLink
          :to="n.to"
          class="relative flex min-h-16 flex-col items-center justify-center gap-0.5 text-sm"
          :class="n.key === active ? 'font-bold text-care' : 'text-ink-soft hover:text-ink'"
          :aria-current="n.key === active ? 'page' : undefined"
        >
          <AppIcon :name="n.icon" :size="24" />
          {{ n.label }}
          <span
            v-if="n.key === 'notifications' && unread"
            class="absolute top-2 right-[calc(50%-20px)] grid min-w-5 place-items-center rounded-full bg-critical px-1 text-xs font-bold text-white"
            :aria-label="`${unread} 則未讀`"
          >{{ unread }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>
