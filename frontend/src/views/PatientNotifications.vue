<script setup>
import { computed, onMounted, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import { usePatientPortalStore } from '@/stores/patientPortal'
import { formatTime, localDate } from '@/utils/format'

/** Patient: 通知 (/patient/notifications) — reminders and alerts about my own reports. */
const store = usePatientPortalStore()
const TZ = 'Asia/Taipei'
const items = computed(() => store.notifications?.items ?? [])
const openId = ref(null)
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
const failure = ref('')

async function open(n) {
  openId.value = openId.value === n.id ? null : n.id
  failure.value = ''
  if (openId.value && !n.is_read) {
    try {
      await store.markRead(n.id)
    } catch (e) {
      failure.value = e.message
    }
  }
}
async function readAll() {
  failure.value = ''
  try {
    await store.readAll()
  } catch (e) {
    failure.value = e.message
  }
}

onMounted(() => store.fetchNotifications())
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">通知</h1>
        <button v-if="store.unread" type="button" class="ml-auto min-h-11 rounded-full px-4 font-bold text-care hover:bg-care-soft" data-read-all @click="readAll">全部已讀</button>
      </div>
    </header>

    <main class="mx-auto max-w-xl px-4 pt-4" data-patient-notifications>
      <p class="mb-3 text-ink-soft">
        <template v-if="store.unread">有 <strong data-unread>{{ store.unread }}</strong> 則未讀。</template>
        <template v-else-if="store.notifications">全部都讀過了。</template>
      </p>
      <p v-if="failure || store.errors.notifications" class="mb-3 rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ failure || store.errors.notifications.message }}</p>
      <div v-if="!store.notifications && !store.errors.notifications" class="h-40 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <p v-else-if="!items.length" class="rounded-2xl border border-line bg-surface p-5 text-ink-soft">目前沒有通知。</p>
      <ul v-else class="space-y-2">
        <li v-for="n in items" :key="n.id" class="rounded-2xl border bg-surface" :class="n.severity === 'critical' && !n.is_read ? 'border-critical/50' : 'border-line'" :data-notification="n.id" :data-read="n.is_read">
          <button type="button" class="flex w-full items-start gap-3 p-4 text-left" :aria-expanded="openId === n.id" @click="open(n)">
            <span class="mt-2 size-2.5 shrink-0 rounded-full" :class="n.is_read ? 'bg-transparent' : n.severity === 'critical' ? 'bg-critical' : 'bg-care'" :aria-label="n.is_read ? '已讀' : '未讀'" />
            <span class="min-w-0 flex-1">
              <span class="block" :class="n.is_read ? '' : 'font-bold'">{{ n.title }}</span>
              <span class="block text-sm text-ink-soft">{{ when(n.created_at) }}<template v-if="n.status_text">・{{ n.status_text }}</template></span>
            </span>
            <AppIcon name="chevron" :size="18" class="mt-1 shrink-0 text-ink-soft transition" :class="openId === n.id ? 'rotate-90' : ''" />
          </button>
          <div v-if="openId === n.id" class="border-t border-line px-4 py-3" data-notification-detail>
            <p>{{ n.message }}</p>
            <p v-if="n.status_text" class="mt-2 inline-flex rounded-full bg-mist px-3 py-1 text-sm font-bold">處理狀態：{{ n.status_text }}</p>
            <p v-if="n.severity === 'critical'" class="mt-2 text-sm text-critical">身體不舒服時請不要等待，撥打照護專線或前往急診。</p>
          </div>
        </li>
      </ul>
    </main>

    <PatientBottomNav active="notifications" :unread="store.unread" />
  </div>
</template>
