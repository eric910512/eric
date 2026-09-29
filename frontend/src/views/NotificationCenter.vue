<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import NotificationDetail from '@/components/NotificationDetail.vue'
import NurseNav from '@/components/NurseNav.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen, localDate } from '@/utils/format'

/**
 * Nurse Notification Center: 待處理 (new + acknowledged) / 處理中 / 已完成.
 * Flow per alert: 查看 → 接手 → 處理中 → 完成. Filters and selection live in the URL query.
 */
const route = useRoute()
const router = useRouter()
const store = useNurseStore()
const dashboard = useDashboardStore()

const TZ = 'Asia/Taipei'
const today = localDate(new Date().toISOString(), TZ)

const TABS = [
  { key: 'pending', label: '待處理', empty: '目前沒有待處理的警示。' },
  { key: 'in_progress', label: '處理中', empty: '目前沒有處理中的警示。' },
  { key: 'resolved', label: '已完成', empty: '還沒有已完成的警示。' },
]
const SEVERITY = {
  critical: { label: '危急', chip: 'bg-critical text-white', bar: 'bg-critical' },
  warning: { label: '注意', chip: 'bg-warn-soft text-warn', bar: 'bg-warn' },
  info: { label: '資訊', chip: 'bg-care-soft text-care', bar: 'bg-care' },
}
const STATUS_CHIP = {
  new: 'bg-critical-soft text-critical',
  acknowledged: 'bg-care-soft text-care',
  in_progress: 'bg-warn-soft text-warn',
  resolved: 'bg-ok-soft text-ok',
}
const NEXT = { new: ['acknowledge', '接手'], acknowledged: ['start', '開始處理'], in_progress: ['resolve', '完成處理'] }

const tab = computed(() => (TABS.some((t) => t.key === route.query.tab) ? route.query.tab : 'pending'))
const priority = computed(() => (['critical', 'warning'].includes(route.query.priority) ? route.query.priority : null))
const patientId = computed(() => route.query.patient || null)
const selectedId = computed(() => (route.query.id ? Number(route.query.id) : null))
const focusNote = ref(false)

const list = computed(() => store.center)
const counts = computed(() => list.value?.meta.counts ?? {})
const patients = computed(() => dashboard.nurseOverview?.caseload.data ?? [])
const busy = reactive({})
const rowErrors = reactive({})

function setQuery(patch) {
  router.replace({ query: { ...route.query, ...patch } })
}
function select(id, withNote = false) {
  focusNote.value = withNote
  setQuery({ id: String(id) })
}

function load() {
  store.fetchCenter({ status: tab.value, priority: priority.value, patientId: patientId.value })
}
watch([tab, priority, patientId], load)
onMounted(() => {
  load()
  dashboard.fetchSettings()
  if (!dashboard.nurseOverview) dashboard.fetchNurseOverview()
})

async function quick(n) {
  const [action] = NEXT[n.status]
  if (action === 'resolve') return select(n.id, true) // needs a note: open the detail
  busy[n.id] = true
  rowErrors[n.id] = null
  try {
    await store.transition(n.id, action)
  } catch (err) {
    rowErrors[n.id] = err.message
    if (err.code === 'INVALID_TRANSITION') load()
  } finally {
    busy[n.id] = false
  }
}

function handler(n) {
  const h = n.handling
  if (n.status === 'resolved') return h?.resolved?.by?.display_name
  if (n.status === 'in_progress') return h?.started?.by?.display_name
  if (n.status === 'acknowledged') return h?.acknowledged?.by?.display_name
  return null
}
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="nurse-notifications" :badge="counts.open ?? 0" />

    <main class="min-w-0 p-4 lg:p-6">
      <header class="flex flex-wrap items-end gap-3">
        <div class="mr-auto">
          <RouterLink :to="{ name: 'nurse' }" class="text-sm text-care hover:underline lg:hidden">← 照護總覽</RouterLink>
          <h1 class="text-2xl font-bold">通知中心</h1>
          <p class="text-ink-soft">負責病人的風險警示：接手、處理、完成。</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <div class="flex rounded-full border border-line bg-surface p-0.5" role="group" aria-label="優先程度">
            <button
              v-for="p in [{ k: null, l: '全部' }, { k: 'critical', l: '危急' }, { k: 'warning', l: '注意' }]"
              :key="p.l"
              type="button"
              class="min-h-9 rounded-full px-4 text-sm"
              :class="priority === p.k ? 'bg-ink font-bold text-white' : 'text-ink-soft hover:text-ink'"
              :aria-pressed="priority === p.k"
              @click="setQuery({ priority: p.k ?? undefined, id: undefined })"
            >{{ p.l }}</button>
          </div>
          <select
            :value="patientId ?? ''"
            class="min-h-10 rounded-full border border-line bg-surface px-3"
            aria-label="病人"
            @change="setQuery({ patient: $event.target.value || undefined, id: undefined })"
          >
            <option value="">所有病人</option>
            <option v-for="p in patients" :key="p.patient_id" :value="p.patient_id">{{ p.display_name }}（{{ p.patient_code }}）</option>
          </select>
        </div>
      </header>

      <div class="mt-4 flex gap-1 border-b border-line" role="tablist" aria-label="處理狀態">
        <button
          v-for="t in TABS"
          :key="t.key"
          type="button"
          role="tab"
          :aria-selected="tab === t.key"
          class="-mb-px flex min-h-12 items-center gap-2 border-b-[3px] px-4 font-medium"
          :class="tab === t.key ? 'border-care text-ink' : 'border-transparent text-ink-soft hover:text-ink'"
          @click="setQuery({ tab: t.key, id: undefined })"
        >
          {{ t.label }}
          <span
            v-if="counts[t.key] != null && t.key !== 'resolved'"
            class="rounded-full px-2 text-sm font-bold"
            :class="counts[t.key] ? (t.key === 'pending' ? 'bg-critical text-white' : 'bg-warn-soft text-warn') : 'bg-mist text-ink-soft'"
          >{{ counts[t.key] }}</span>
        </button>
      </div>

      <div class="mt-4 grid items-start gap-4 lg:grid-cols-[minmax(20rem,26rem)_1fr]">
        <!-- list -->
        <section :class="selectedId ? 'hidden lg:block' : ''" aria-label="通知列表">
          <p v-if="store.errors.center" class="rounded-2xl border border-line bg-surface p-5 text-critical">{{ store.errors.center }}</p>
          <div v-else-if="!list" class="space-y-3" aria-busy="true">
            <div v-for="n in 3" :key="n" class="h-24 animate-pulse rounded-2xl bg-surface" />
          </div>
          <p v-else-if="!list.items.length" class="rounded-2xl border border-line bg-surface p-5 text-ink-soft">
            {{ TABS.find((t) => t.key === tab).empty }}
          </p>
          <ul v-else class="space-y-2">
            <li
              v-for="n in list.items"
              :key="n.id"
              class="relative overflow-hidden rounded-2xl border bg-surface"
              :class="n.id === selectedId ? 'border-care ring-2 ring-care/30' : 'border-line'"
              :data-notification="n.id"
            >
              <i class="absolute inset-y-0 left-0 w-1.5" :class="SEVERITY[n.severity]?.bar" />
              <button type="button" class="block w-full py-3 pr-4 pl-5 text-left hover:bg-mist/60" :aria-label="`查看 ${n.title}`" @click="select(n.id)">
                <span class="flex flex-wrap items-center gap-2">
                  <span class="rounded-full px-2 text-xs font-bold" :class="SEVERITY[n.severity]?.chip">{{ SEVERITY[n.severity]?.label }}</span>
                  <span class="rounded-full px-2 text-xs font-bold" :class="STATUS_CHIP[n.status]">
                    {{ n.status_text }}<template v-if="handler(n)">・{{ handler(n) }}</template>
                  </span>
                  <span class="ml-auto text-sm text-ink-soft">{{ formatWhen(n.created_at, today, TZ) }}</span>
                </span>
                <span class="mt-1 block font-bold">{{ n.title }}</span>
                <span class="block text-sm text-ink-soft">{{ n.patient?.display_name }}（{{ n.patient?.patient_code }}）</span>
                <span class="mt-0.5 line-clamp-2 block text-sm">{{ n.message }}</span>
              </button>
              <div v-if="NEXT[n.status]" class="flex items-center gap-2 border-t border-line/70 py-2 pr-4 pl-5">
                <button
                  type="button"
                  class="min-h-10 rounded-full px-4 text-sm font-bold disabled:opacity-60"
                  :class="n.status === 'in_progress' ? 'bg-ok text-white hover:bg-ok/90' : n.severity === 'critical' ? 'bg-critical text-white hover:bg-critical/90' : 'bg-care text-white hover:bg-care/90'"
                  :disabled="busy[n.id]"
                  :data-quick="NEXT[n.status][0]"
                  @click="quick(n)"
                >{{ busy[n.id] ? '儲存中…' : NEXT[n.status][1] }}</button>
                <p v-if="rowErrors[n.id]" class="text-sm text-critical" role="alert">{{ rowErrors[n.id] }}</p>
              </div>
            </li>
          </ul>
        </section>

        <!-- detail -->
        <NotificationDetail
          v-if="selectedId"
          :notification-id="selectedId"
          :today="today"
          :timezone="TZ"
          :focus-note="focusNote"
          @close="setQuery({ id: undefined })"
        />
        <div v-else class="hidden rounded-2xl border border-dashed border-line p-8 text-center text-ink-soft lg:block">
          <AppIcon name="bell" :size="28" class="mx-auto" />
          <p class="mt-2">選擇左側的通知，查看觸發原因、原始資料與建議處理。</p>
        </div>
      </div>
    </main>
  </div>
</template>
