<script setup>
import { computed, onMounted } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import { widgetRegistry } from '@/dashboard/registry'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'

/**
 * Patient home (PatientMobileShell, ui-architecture.md §1.1).
 * Widgets are rendered from the Layout JSON through the Component Registry.
 */
const store = useDashboardStore()
const auth = useAuthStore()

// A patient only ever sees their own record: "me" against the API, the account's patient in mock mode.
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const dashboard = computed(() => store.patients[patientId.value])
const loadError = computed(() => store.errors[`patient:${patientId.value}`] || store.errors.layout)

const ctx = computed(() => {
  if (!dashboard.value) return null
  return {
    widgets: dashboard.value.widgets,
    patientId: patientId.value,
    today: dashboard.value.widgets['today-schedule'].date,
    timezone: 'Asia/Taipei',
    settings: store.settings,
  }
})

function render(item) {
  const entry = widgetRegistry[item.widget_code]
  return entry ? { component: entry.component, props: entry.props(ctx.value) } : null
}

const pinned = computed(() => (store.patientLayout?.pinned ?? []).map((item) => ({ item, view: render(item) })))
const items = computed(() =>
  [...(store.patientLayout?.items ?? [])]
    .sort((a, b) => a.position.y - b.position.y)
    .map((item) => ({ item, view: render(item) })),
)

function load() {
  store.fetchSettings()
  store.fetchPatientLayout()
  store.fetchPatientDashboard(patientId.value)
}
onMounted(load)

const unread = computed(() => dashboard.value?.widgets.notifications.unread_count ?? 0)

</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-3 px-4 py-3">
        <span class="grid size-10 place-items-center rounded-full bg-care text-white"><AppIcon name="home" :size="20" /></span>
        <div>
          <p class="font-bold leading-tight">{{ store.settings?.organization.name ?? '　' }}</p>
          <p class="text-sm text-ink-soft">{{ store.settings?.organization.department }}</p>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4">
      <div v-if="loadError" class="rounded-2xl border border-critical/40 bg-surface p-5">
        <p class="font-bold text-critical">{{ loadError }}</p>
        <button type="button" class="mt-3 min-h-12 rounded-full border border-line px-5" @click="load">重新載入</button>
      </div>

      <template v-else-if="ctx && store.patientLayout">
        <template v-for="{ item, view } in pinned" :key="item.instance_key">
          <component :is="view.component" v-if="view" v-bind="view.props" />
        </template>

        <template v-for="{ item, view } in items" :key="item.instance_key">
          <div v-if="view" :id="`w-${item.widget_code}`" class="scroll-mt-20">
            <component :is="view.component" v-bind="view.props" />
          </div>
          <div v-else class="rounded-2xl border border-dashed border-line p-5 text-ink-soft">
            這個區塊需要更新 App 版本才能顯示。
          </div>
        </template>
      </template>

      <div v-else class="space-y-4" aria-busy="true" aria-label="載入中">
        <div v-for="n in 4" :key="n" class="h-36 animate-pulse rounded-2xl bg-surface" />
      </div>
    </main>

    <PatientBottomNav active="home" :unread="unread" />
  </div>
</template>
