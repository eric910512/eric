<script setup>
import AppIcon from '@/components/AppIcon.vue'
import { useDashboardStore } from '@/stores/dashboard'

/** Side navigation of the nurse workspace (ClinicalDesktopShell). */
defineProps({
  active: { type: String, required: true }, // route name of the current page
  badge: { type: Number, default: 0 }, // open alerts, shown on 通知與警示
})

const store = useDashboardStore()

const NAV = [
  { label: '照護總覽', icon: 'home', to: { name: 'nurse' }, key: 'nurse' },
  { label: '通知與警示', icon: 'bell', to: { name: 'nurse-notifications' }, key: 'nurse-notifications', badge: true },
  { label: '病人管理', icon: 'users', to: { name: 'nurse-patients' }, key: 'nurse-patients' },
  { label: '待審清單', icon: 'clipboard', to: { name: 'nurse-reviews' }, key: 'symptom-reviews' },
  { label: '護理評估', icon: 'check', to: { name: 'nurse-assessments' }, key: 'nursing-assessments' },
]
</script>

<template>
  <aside class="hidden border-r border-line bg-surface lg:block">
    <div class="sticky top-0 p-5">
      <p class="font-bold">{{ store.settings?.organization.name }}</p>
      <p class="text-sm text-ink-soft">{{ store.settings?.organization.department }}</p>
      <nav class="mt-6" aria-label="護理端功能">
        <ul class="space-y-1">
          <li v-for="n in NAV" :key="n.key">
            <RouterLink
              :to="n.to"
              class="flex min-h-12 items-center gap-3 rounded-lg px-3"
              :class="n.key === active ? 'bg-care-soft font-bold text-care' : 'text-ink-soft hover:bg-mist hover:text-ink'"
              :aria-current="n.key === active ? 'page' : undefined"
            >
              <AppIcon :name="n.icon" :size="20" /> {{ n.label }}
              <span v-if="n.badge && badge" class="ml-auto rounded-full bg-critical px-2 text-sm font-bold text-white" :aria-label="`${badge} 則待處理`">{{ badge }}</span>
            </RouterLink>
          </li>
        </ul>
      </nav>
      <p v-if="store.nurseOverview" class="mt-8 text-sm text-ink-soft">
        {{ store.nurseOverview.nurse.display_name }}<template v-if="store.nurseOverview.nurse.department"><br />{{ store.nurseOverview.nurse.department }}</template>
      </p>
    </div>
  </aside>
</template>
